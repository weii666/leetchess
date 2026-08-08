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
from service.errors import EngineTimeoutError, IllegalMoveSequenceError, ServiceError
from service.types import BestMove, Score, ScoreKind

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

#: 《適情雅趣》第 21 局起始局面(紅先)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 同一局走 `f8f9` 之後的局面,**黑先且 fullmove 仍為 1**。
#: 用於證明步數推導以起始 FEN 實際的行棋方與回合數為基準,而非硬編「紅先、fullmove 1」。
PUZZLE_FEN_BLACK_TO_MOVE = (
    "3akR3/3Ra4/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 b - - 1 1"
)

#: 起始局面紅方的合法著法數,以及走 `f8f9` 後黑方僅剩的兩著(皆為實測值)。
PUZZLE_LEGAL_MOVE_COUNT = 44
PUZZLE_BLACK_REPLIES = {"e8f9", "e9f9"}

#: 一段連續吃子的合法序列(實測):`e8f9` 與 `d8d9` 都是吃子。
#: 走完三步後引擎回報 `b - - 0 2` —— **halfmove clock 被吃子重置為 0**,
#: 用它推步數會得出「零步」而漏判,故推導只能用行棋方與 fullmove。
PUZZLE_CAPTURING_SEQUENCE = ["f8f9", "e8f9", "d8d9"]

#: 起始局面下走不出的一步:a1 是空格。引擎對它**靜默忽略**,不回報任何錯誤。
ILLEGAL_MOVE = "a1a2"

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


def _capture(call: Callable[[], Any]) -> tuple[Any, ServiceError | None]:
    """執行 `call`,回傳(值, 服務錯誤)。

    序列驗證失效時的失敗表現**不是拋錯,而是回傳別的局面的資料**,因此測試必須
    同時看見「回傳了什麼」與「拋了什麼」;`pytest.raises` 只看得到後者。
    """
    try:
        return call(), None
    except ServiceError as error:
        return None, error


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


# --- 走法序列驗證(5.3)-------------------------------------------------
#
# 引擎對 `position ... moves ...` 中的非法著法**靜默忽略,不回報任何錯誤**,並以
# 它實際解析到的局面回應後續指令。不驗證的後果不是拋錯,而是把**別的局面**的合法
# 著法當成當前局面回給前端 —— 使用者看到一盤錯誤的棋,雙方都不會察覺。
#
# 驗證因此內建於協定層的兩個查詢方法之中,不交由呼叫方負責:呼叫方無從得知引擎
# 忽略了哪一步。


@pytest.mark.parametrize(
    ("query", "command"),
    [
        (lambda p: p.legal_moves(PUZZLE_FEN, ["f8f9"], FAKE_TIMEOUT), "go perft 1"),
        (lambda p: p.best_move(PUZZLE_FEN, ["f8f9"], 1000, FAKE_TIMEOUT), "go nodes 1000"),
    ],
)
def test_the_applied_sequence_is_checked_before_the_query_command(
    fake_engine, query, command
) -> None:
    """`d` 必須夾在 `position` 與查詢指令之間 —— 查詢送出後才發現不一致已經太遲。"""
    engine = fake_engine("normal")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        query(process)
    finally:
        process.terminate()
    commands = engine.commands()
    assert f"position fen {PUZZLE_FEN} moves f8f9" in commands
    assert "d" in commands
    assert commands.index("d") > commands.index(f"position fen {PUZZLE_FEN} moves f8f9")
    assert commands.index("d") < commands.index(command)


def test_legal_moves_rejects_a_sequence_the_engine_did_not_fully_apply(
    fake_engine,
) -> None:
    """替身模擬引擎的靜默忽略:局面停在起始處,回報的步數對不上請求所帶的走法數。"""
    process = _start(fake_engine, "ignores_moves")
    try:
        value, error = _capture(
            lambda: process.legal_moves(PUZZLE_FEN, ["f8f9"], timeout=FAKE_TIMEOUT)
        )
    finally:
        process.terminate()
    assert value is None, "序列未完整套用時仍回傳了著法"
    assert isinstance(error, IllegalMoveSequenceError)


def test_best_move_rejects_a_sequence_the_engine_did_not_fully_apply(
    fake_engine,
) -> None:
    process = _start(fake_engine, "ignores_moves")
    try:
        value, error = _capture(
            lambda: process.best_move(
                PUZZLE_FEN, ["f8f9"], nodes=1000, timeout=FAKE_TIMEOUT
            )
        )
    finally:
        process.terminate()
    assert value is None, "序列未完整套用時仍回傳了應手"
    assert isinstance(error, IllegalMoveSequenceError)


def test_an_inconsistent_sequence_does_not_send_the_query_command(fake_engine) -> None:
    """不一致時必須在送出查詢前就止住,否則管線裡會留下沒人收的搜尋輸出。"""
    engine = fake_engine("ignores_moves")
    process = EngineProcess(engine.path, startup_timeout=BOUNDED_WAIT)
    try:
        _capture(lambda: process.legal_moves(PUZZLE_FEN, ["f8f9"], FAKE_TIMEOUT))
    finally:
        process.terminate()
    assert not any(command.startswith("go") for command in engine.commands())


def test_the_process_stays_usable_after_an_inconsistent_sequence(fake_engine) -> None:
    """序列不一致是呼叫端的問題,不是引擎故障 —— 進程不得因此被判為不健康。"""
    process = _start(fake_engine, "ignores_moves")
    try:
        _capture(lambda: process.legal_moves(PUZZLE_FEN, ["f8f9"], FAKE_TIMEOUT))
        assert process.is_healthy()
        # 空序列在該替身下必然一致,證明管線沒有殘留而錯位。
        assert process.legal_moves(PUZZLE_FEN, [], timeout=FAKE_TIMEOUT) == [
            "e8f9",
            "e9f9",
        ]
    finally:
        process.terminate()


def test_the_inconsistency_error_leaks_no_engine_output(fake_engine) -> None:
    """5.4:對外訊息不得含引擎原始輸出(此處是 `d` 的 FEN)或內部路徑。"""
    process = _start(fake_engine, "ignores_moves")
    try:
        _, error = _capture(
            lambda: process.legal_moves(PUZZLE_FEN, ["f8f9"], FAKE_TIMEOUT)
        )
    finally:
        process.terminate()
    message = str(error)
    assert "Fen" not in message
    assert "Checkers" not in message
    assert "/" not in message, "訊息含路徑或 FEN 片段"


@pytest.mark.parametrize(
    "query",
    [
        lambda p: p.legal_moves(PUZZLE_FEN, ["f8f9"], timeout=FAKE_TIMEOUT),
        lambda p: p.best_move(PUZZLE_FEN, ["f8f9"], nodes=1000, timeout=FAKE_TIMEOUT),
    ],
    ids=["legal_moves", "best_move"],
)
def test_the_sequence_check_read_is_bounded_by_the_given_timeout(
    fake_engine, query
) -> None:
    """序列驗證那道 `d` 的讀取同樣受傳入的逾時管轄,不得自帶更寬的上界。

    這是 2.1 解掉無上界讀取之後新增的第一個阻塞讀取,若它改用自己算的 deadline
    (例如寫死一個大值),方法的總等待時間就會超過呼叫端給的 `timeout`,而其餘
    逾時測試全都停在 `go` 那一段、看不到這裡。替身握手與 `go` 都正常,只在 `d`
    上沉默,使進程必然走到這道讀取才卡住。
    """
    process = _start(fake_engine, "mute_on_display")
    try:
        outcome = _run_bounded(lambda: query(process))
        # finished 為假代表呼叫仍卡在 `d` 的讀取上 —— 上界失效的正是這個樣子。
        assert outcome.finished, "序列驗證的 `d` 讀取在引擎不輸出時永久阻塞"
        assert isinstance(outcome.error, EngineTimeoutError)
        assert outcome.elapsed >= FAKE_TIMEOUT
        assert outcome.elapsed < BOUNDED_WAIT
    finally:
        process.terminate()


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


# --- 真實引擎:序列驗證(5.3)------------------------------------------


@requires_real_engine
def test_real_engine_accepts_a_capturing_sequence(real_process) -> None:
    """含吃子的合法序列不得被誤判。

    走完這三步後引擎回報的 halfmove clock 為 0(被吃子重置),若推導把它當成步數
    來源,這個完全合法的序列會被判成不一致。步數只能由行棋方與 fullmove 推得。
    """
    moves = real_process.legal_moves(
        PUZZLE_FEN, PUZZLE_CAPTURING_SEQUENCE, timeout=REAL_TIMEOUT
    )
    assert moves, "合法的吃子序列被誤判為不一致"


@requires_real_engine
def test_real_engine_rejects_an_illegal_sequence_instead_of_returning_another_position(
    real_process,
) -> None:
    """驗證失效的表現不是拋錯,而是回傳**起始局面**的 44 著。

    因此先斷言回傳內容不是那一組著法 —— 只斷言「有拋錯」的測試抓不到這個 bug。
    """
    start_moves = set(real_process.legal_moves(PUZZLE_FEN, [], timeout=REAL_TIMEOUT))
    assert len(start_moves) == PUZZLE_LEGAL_MOVE_COUNT

    value, error = _capture(
        lambda: real_process.legal_moves(
            PUZZLE_FEN, [ILLEGAL_MOVE], timeout=REAL_TIMEOUT
        )
    )
    assert value is None or set(value) != start_moves, (
        "序列驗證失效:把起始局面的合法著法當成當前局面回傳了"
    )
    assert value is None, "非法著法被忽略後仍回傳了著法"
    assert isinstance(error, IllegalMoveSequenceError)


@requires_real_engine
def test_real_engine_rejects_an_illegal_move_in_the_middle_of_a_sequence(
    real_process,
) -> None:
    """非法著法之後的著法也一併被引擎丟棄,套用步數同樣對不上。"""
    replies = set(
        real_process.legal_moves(PUZZLE_FEN, ["f8f9"], timeout=REAL_TIMEOUT)
    )
    assert replies == PUZZLE_BLACK_REPLIES

    value, error = _capture(
        lambda: real_process.legal_moves(
            PUZZLE_FEN, ["f8f9", ILLEGAL_MOVE, "e8f9"], timeout=REAL_TIMEOUT
        )
    )
    assert value is None or set(value) != replies, "序列驗證失效:回傳了中途局面的著法"
    assert value is None
    assert isinstance(error, IllegalMoveSequenceError)


@requires_real_engine
def test_real_engine_best_move_rejects_an_illegal_sequence(real_process) -> None:
    """驗證是兩個查詢方法共同的保證,不是只有 `legal_moves` 才有。"""
    value, error = _capture(
        lambda: real_process.best_move(
            PUZZLE_FEN, [ILLEGAL_MOVE], nodes=1000, timeout=REAL_TIMEOUT
        )
    )
    assert value is None, "非法著法被忽略後仍回傳了別的局面的應手"
    assert isinstance(error, IllegalMoveSequenceError)


@requires_real_engine
def test_real_engine_sequence_check_uses_the_given_fen_not_a_hardcoded_start(
    real_process,
) -> None:
    """起始 FEN 未必紅先、fullmove 未必為 1,推導須以該題目的 FEN 為基準。"""
    moves = real_process.legal_moves(
        PUZZLE_FEN_BLACK_TO_MOVE, ["e8f9"], timeout=REAL_TIMEOUT
    )
    assert moves, "黑先起始局面的合法序列被誤判為不一致"

    value, error = _capture(
        lambda: real_process.legal_moves(
            PUZZLE_FEN_BLACK_TO_MOVE, [ILLEGAL_MOVE], timeout=REAL_TIMEOUT
        )
    )
    assert value is None
    assert isinstance(error, IllegalMoveSequenceError)


@requires_real_engine
def test_real_engine_stays_usable_after_an_illegal_sequence(real_process) -> None:
    """序列不一致不是引擎故障:同一個進程必須能立刻服務下一個請求。"""
    _capture(
        lambda: real_process.legal_moves(
            PUZZLE_FEN, [ILLEGAL_MOVE], timeout=REAL_TIMEOUT
        )
    )
    assert real_process.is_healthy()
    moves = real_process.legal_moves(PUZZLE_FEN, ["f8f9"], timeout=REAL_TIMEOUT)
    assert set(moves) == PUZZLE_BLACK_REPLIES
