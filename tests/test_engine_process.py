"""EngineProcess 的測試(對應 tasks 2.1、requirements 1.1、1.4、1.6、4.1)。

本任務的核心價值是**逾時上界**:POC 的 `_read_until` 直接迭代管線,引擎不輸出即
執行緒永久卡死。因此每個查詢方法都必須在傳入的逾時內返回或拋錯。

所有可能卡死的呼叫都經 `_run_bounded` 在背景執行緒執行並附上等待上限 ——
若實作退化回無上界的阻塞讀取,測試會**失敗**而不是把整個測試套件掛住。

真實引擎的輸出解析另有一組測試(檔案末段),證明解析邏輯對真實輸出有效;
逾時、崩潰與異常路徑則一律用替身,那些情境真實引擎製造不出來。
"""

from __future__ import annotations

import pathlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from service.engine.process import EngineProcess
from service.errors import EngineTimeoutError, ServiceError
from service.types import BestMove, Score, ScoreKind

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

#: 《適情雅趣》第 21 局起始局面(紅先)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 起始局面紅方的合法著法數,以及走 `f8f9` 後黑方僅剩的兩著(皆為實測值)。
PUZZLE_LEGAL_MOVE_COUNT = 44
PUZZLE_BLACK_REPLIES = {"e8f9", "e9f9"}

#: 替身測試用的逾時。取小值使測試快,仍遠大於替身的回應時間。
FAKE_TIMEOUT = 0.3

#: 等待「應該要返回」的呼叫返回的上限。遠大於 FAKE_TIMEOUT,避免慢機誤判。
BOUNDED_WAIT = 8.0

REAL_TIMEOUT = 10.0

#: 真實引擎搜到此局面 mate 所需的節點數(冷雜湊表下實測約 0.5 秒)。
#: **設定的預設 200k 在冷雜湊表下搜不到這一步的殺**,只回 `score cp`;此處刻意用
#: 足夠的節點數,使 mate 解析的驗證不依賴前面測試留下的雜湊表內容。
REAL_MATE_NODES = 1_000_000

requires_real_engine = pytest.mark.skipif(
    not REAL_ENGINE.is_file(), reason="真實引擎未安裝,請先執行 engine/fetch.sh"
)


# --- 不會掛住測試套件的呼叫輔助 -----------------------------------------


@dataclass
class _Outcome:
    """一次有時限的呼叫結果。`finished` 為假代表呼叫仍卡在那裡。"""

    finished: bool = False
    value: Any = None
    error: BaseException | None = None
    elapsed: float = field(default=0.0)


def _run_bounded(call: Callable[[], Any], wait: float = BOUNDED_WAIT) -> _Outcome:
    """在背景執行緒呼叫 `call`,最多等 `wait` 秒。

    受測方法自己就該有逾時上界;此處的上限只是**安全網**,使實作退化時測試失敗
    而非讓 pytest 永遠停在那一行。
    """
    outcome = _Outcome()
    started = time.monotonic()

    def target() -> None:
        try:
            outcome.value = call()
        except BaseException as exc:  # noqa: BLE001 - 測試要看到任何例外
            outcome.error = exc
        finally:
            outcome.elapsed = time.monotonic() - started

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(wait)
    outcome.finished = not worker.is_alive()
    if not outcome.finished:
        outcome.elapsed = time.monotonic() - started
    return outcome


def _start(fake, mode: str) -> EngineProcess:
    engine = fake(mode)
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    return process


# --- 握手與啟動設定 -----------------------------------------------------


def test_handshake_fixes_single_thread_and_128mb_hash(fake_engine) -> None:
    """`Threads=1` 與 `Hash=128` 由 tech.md 的引擎調用慣例固定。"""
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        assert engine.commands() == [
            "uci",
            "setoption name Threads value 1",
            "setoption name Hash value 128",
            "isready",
        ]
    finally:
        process.terminate()


def test_engine_path_is_injected_rather_than_hardcoded(fake_engine) -> None:
    """路徑可注入是 5.5 的前提:替身必須能取代真實引擎。"""
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        assert process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT) == [
            "e8f9",
            "e9f9",
        ]
    finally:
        process.terminate()


def test_a_relative_engine_path_still_resolves(fake_engine, monkeypatch) -> None:
    """cwd 會被換到引擎自己的目錄(讓引擎找到 pikafish.nnue),相對路徑必須先解析。"""
    engine = fake_engine("normal")
    monkeypatch.chdir(engine.path.parent)
    process = EngineProcess(pathlib.Path(engine.path.name), startup_timeout=BOUNDED_WAIT)
    try:
        assert process.is_healthy()
        assert process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT) == [
            "e8f9",
            "e9f9",
        ]
    finally:
        process.terminate()


def test_handshake_with_a_mute_engine_times_out_instead_of_hanging(fake_engine) -> None:
    engine = fake_engine("mute")
    outcome = _run_bounded(
        lambda: EngineProcess(engine.path, startup_timeout=FAKE_TIMEOUT)
    )
    assert outcome.finished, "握手在替身不回應時永久阻塞"
    assert isinstance(outcome.error, EngineTimeoutError)
    assert outcome.elapsed < BOUNDED_WAIT


# --- 指令序列(移植自 POC 的 stateless 用法)----------------------------


def test_position_command_carries_the_whole_move_sequence(fake_engine) -> None:
    """引擎 stateless:每次重送完整 `position fen <fen> moves <...>`。"""
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        process.legal_moves(PUZZLE_FEN, ["f8f9", "e9f9"], timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert f"position fen {PUZZLE_FEN} moves f8f9 e9f9" in engine.commands()
    assert "go perft 1" in engine.commands()


def test_position_command_omits_moves_when_the_sequence_is_empty(fake_engine) -> None:
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert f"position fen {PUZZLE_FEN}" in engine.commands()
    assert not any(" moves" in command for command in engine.commands())


def test_best_move_uses_go_nodes_not_go_movetime(fake_engine) -> None:
    """tech.md:`go nodes` 作為品質下限,不得改用 `go movetime`。"""
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        process.best_move(PUZZLE_FEN, [], nodes=200_000, timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert "go nodes 200000" in engine.commands()
    assert not any(command.startswith("go movetime") for command in engine.commands())


# --- 輸出解析 -----------------------------------------------------------


def test_legal_moves_parses_perft_output(fake_engine) -> None:
    process = _start(fake_engine, "normal")
    try:
        assert process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT) == [
            "e8f9",
            "e9f9",
        ]
    finally:
        process.terminate()


def test_legal_moves_is_empty_when_the_side_to_move_has_no_move(fake_engine) -> None:
    """空 list 是真終局的唯一判準(1.2),由引擎輸出決定,本層不做規則判斷。"""
    process = _start(fake_engine, "no_legal_moves")
    try:
        assert process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT) == []
    finally:
        process.terminate()


def test_best_move_parses_a_centipawn_score(fake_engine) -> None:
    process = _start(fake_engine, "normal")
    try:
        result = process.best_move(PUZZLE_FEN, [], nodes=1000, timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert result == BestMove(move="e8f9", score=Score(kind=ScoreKind.CP, value=25))


def test_best_move_takes_the_last_reported_score(fake_engine) -> None:
    """迭代加深會輸出多行分數,最後一行才是本次搜尋的結論。"""
    process = _start(fake_engine, "mate")
    try:
        result = process.best_move(PUZZLE_FEN, [], nodes=1000, timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert result == BestMove(move="e9f9", score=Score(kind=ScoreKind.MATE, value=-15))


def test_best_move_reports_none_when_the_engine_has_no_move(fake_engine) -> None:
    """`bestmove (none)`:引擎回報無著可走,不是解析失敗。"""
    process = _start(fake_engine, "no_legal_moves")
    try:
        result = process.best_move(PUZZLE_FEN, [], nodes=1000, timeout=FAKE_TIMEOUT)
    finally:
        process.terminate()
    assert result == BestMove(move=None, score=None)


# --- 逾時上界(本任務的核心)-------------------------------------------


@pytest.mark.parametrize("mode", ["mute_after_handshake", "truncated_go"])
def test_legal_moves_times_out_instead_of_blocking_forever(fake_engine, mode) -> None:
    """替身握手後不再輸出(或只輸出一半),查詢必須在逾時後拋錯。"""
    process = _start(fake_engine, mode)
    try:
        outcome = _run_bounded(
            lambda: process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
        )
        assert outcome.finished, "legal_moves 在引擎不輸出時永久阻塞"
        assert isinstance(outcome.error, EngineTimeoutError)
        assert outcome.elapsed >= FAKE_TIMEOUT
        assert outcome.elapsed < BOUNDED_WAIT
    finally:
        process.terminate()


@pytest.mark.parametrize("mode", ["mute_after_handshake", "truncated_go"])
def test_best_move_times_out_instead_of_blocking_forever(fake_engine, mode) -> None:
    process = _start(fake_engine, mode)
    try:
        outcome = _run_bounded(
            lambda: process.best_move(
                PUZZLE_FEN, [], nodes=1000, timeout=FAKE_TIMEOUT
            )
        )
        assert outcome.finished, "best_move 在引擎不輸出時永久阻塞"
        assert isinstance(outcome.error, EngineTimeoutError)
        assert outcome.elapsed >= FAKE_TIMEOUT
        assert outcome.elapsed < BOUNDED_WAIT
    finally:
        process.terminate()


def test_timeout_error_message_leaks_no_engine_output(fake_engine) -> None:
    """5.4:對外訊息不得含引擎原始輸出或內部路徑。"""
    process = _start(fake_engine, "mute_after_handshake")
    try:
        outcome = _run_bounded(
            lambda: process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
        )
        assert outcome.finished
        message = str(outcome.error)
        assert "Nodes searched" not in message
        assert "info" not in message
        assert "/" not in message, "訊息含路徑片段"
        assert "Traceback" not in message
    finally:
        process.terminate()


def test_process_is_unhealthy_after_a_timeout(fake_engine) -> None:
    """逾時後進程狀態必須明確:此處標記為不健康,交由池決定重建(4.2 於 2.5)。"""
    process = _start(fake_engine, "mute_after_handshake")
    try:
        assert process.is_healthy()
        outcome = _run_bounded(
            lambda: process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
        )
        assert outcome.finished
        assert not process.is_healthy()
    finally:
        process.terminate()


def test_a_timed_out_process_leaves_no_reader_thread_behind(fake_engine) -> None:
    """無上界的讀取會累積永久卡死的執行緒;終止後背景執行緒必須跟著結束。"""
    engine = fake_engine("mute_after_handshake")
    baseline = threading.active_count()
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    outcome = _run_bounded(
        lambda: process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
    )
    assert outcome.finished
    assert threading.active_count() > baseline, "應有背景讀取執行緒在運作"
    process.terminate()
    deadline = time.monotonic() + BOUNDED_WAIT
    while threading.active_count() > baseline and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threading.active_count() == baseline, "終止後仍有讀取執行緒殘留"


# --- 崩潰與健康判定 -----------------------------------------------------


def test_a_crashed_engine_raises_a_service_error_and_is_unhealthy(fake_engine) -> None:
    """替身收到 `go` 即自殺:查詢必須拋出服務錯誤,而不是卡在已關閉的管線上。"""
    process = _start(fake_engine, "exit_on_go")
    try:
        outcome = _run_bounded(
            lambda: process.legal_moves(PUZZLE_FEN, [], timeout=BOUNDED_WAIT)
        )
        assert outcome.finished, "引擎崩潰後查詢永久阻塞"
        assert isinstance(outcome.error, ServiceError)
        assert not process.is_healthy()
    finally:
        process.terminate()


def test_a_healthy_process_stays_healthy_across_queries(fake_engine) -> None:
    process = _start(fake_engine, "normal")
    try:
        for _ in range(3):
            process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
            process.best_move(PUZZLE_FEN, [], nodes=1000, timeout=FAKE_TIMEOUT)
            assert process.is_healthy()
    finally:
        process.terminate()


def test_terminate_stops_the_process_and_is_idempotent(fake_engine) -> None:
    process = _start(fake_engine, "normal")
    process.terminate()
    assert not process.is_healthy()
    process.terminate()  # 重複終止不得拋錯
    assert not process.is_healthy()


def test_query_after_terminate_raises_instead_of_hanging(fake_engine) -> None:
    process = _start(fake_engine, "normal")
    process.terminate()
    outcome = _run_bounded(
        lambda: process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT)
    )
    assert outcome.finished, "對已終止的進程查詢時永久阻塞"
    assert isinstance(outcome.error, ServiceError)


# --- 真實引擎:證明解析邏輯對真實輸出有效 -------------------------------


@pytest.fixture(scope="module")
def real_process():
    if not REAL_ENGINE.is_file():
        pytest.skip("真實引擎未安裝")
    process = EngineProcess(REAL_ENGINE, startup_timeout=REAL_TIMEOUT)
    yield process
    process.terminate()


@requires_real_engine
def test_real_engine_lists_every_legal_move_of_the_start_position(real_process) -> None:
    moves = real_process.legal_moves(PUZZLE_FEN, [], timeout=REAL_TIMEOUT)
    assert len(moves) == PUZZLE_LEGAL_MOVE_COUNT
    assert all(len(move) == 4 for move in moves)


@requires_real_engine
def test_real_engine_lists_black_replies_after_the_key_move(real_process) -> None:
    moves = real_process.legal_moves(PUZZLE_FEN, ["f8f9"], timeout=REAL_TIMEOUT)
    assert set(moves) == PUZZLE_BLACK_REPLIES


@requires_real_engine
def test_real_engine_reports_a_mate_score_against_black(real_process) -> None:
    """黑方應手與 mate 分數的解析(1.4)。分數視角為當前輪方,此處為黑方。"""
    result = real_process.best_move(
        PUZZLE_FEN, ["f8f9"], nodes=REAL_MATE_NODES, timeout=REAL_TIMEOUT
    )
    assert result.move in PUZZLE_BLACK_REPLIES
    assert result.score is not None
    assert result.score.kind is ScoreKind.MATE
    assert result.score.value < 0, "黑方在此局必負,mate 應為負值"


@requires_real_engine
def test_real_engine_process_reports_itself_healthy(real_process) -> None:
    assert real_process.is_healthy()
