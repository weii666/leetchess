"""GameService 的對局狀態判定與黑方應手測試。

對應 tasks 3.2 與 3.3、requirements 1.1、1.2、1.3、1.4、1.5、2.1、2.2、2.3、2.4。

本任務的核心價值是**終局判定的唯一判準**:

    對局是否結束,只由合法著法數是否為零決定,與任何評分無關。

這條規則看起來像一句廢話,直到它被違反 —— 一旦有人「順手」在引擎回報 mate 時
提早宣告勝負,排局就會在使用者還沒走完殺法時被系統中斷,而那正是本專案刻意
不做走脫判定表的理由(見 `.kiro/steering/tech.md` 的三個不可動搖的約束、
requirements 1.3 與 2.4)。因此本檔有兩個測試專門釘死這件事:

- 替身版:`mate` 模式的引擎在同一局面下回報 `score mate -15`,而合法著法非空,
  對局必須回報進行中(`test_mate_score_never_ends_the_game`)
- 真實引擎版:走完《適情雅趣》第 21 局的整條殺法,過程中引擎早就回報 mate,
  但只有在黑方真的無著可走的那一手才停局(`test_real_engine_...`)

替身用於所有語意測試(快、可構造真終局與非法序列),真實引擎只用於「一整局走到
真終局且勝方認定正確」這一項 —— 那是唯一無法用替身取信的斷言。

3.3 的黑方應手在此之上再釘死兩件事:

- **`cp` 分數一律回報「未知」**,連正負號都不看(2.3)。實測 200k 節點下某個實為
  `mate -15` 的局面回報 `cp 526` —— 方向還是相反的,據以推斷等於在使用者必勝時
  告訴他正在落敗。
- **紅方走出致勝一手後,同一次應手請求就回傳「黑方無著、對局結束、紅方獲勝」**。
  那是每一題排局的最後一手,不是邊緣案例;前端不必為了知道贏了沒有再查一次。
"""

from __future__ import annotations

import ast
import json
import pathlib
from dataclasses import dataclass
from typing import Any

import pytest

from service.config import DEFAULT_POSITIONS_DIR, Settings
from service.engine.pool import EnginePool
from service.errors import (
    IllegalMoveSequenceError,
    PositionNotFoundError,
    ServiceBusyError,
    WrongSideToMoveError,
)
from service.game import GameService, classify_score, side_after
from service.positions import PositionRepository
from service.types import GameState, Position, Score, ScoreKind, Side, Signal

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICE_DIR = PROJECT_ROOT / "service"
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

#: 測試題庫用的題號。
PUZZLE_ID = 1

#: 《適情雅趣》第 21 局起始局面(紅先)。
RED_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 同一盤面但改為黑先。用於證明輪方推導以**題目起手方**為基準,而非硬編紅先。
BLACK_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 b - - 0 1"

#: 替身的合法著法(`tests/fakes/fake_engine.py` 的 `PERFT_LINES`)。
FAKE_LEGAL_MOVES = ["e8f9", "e9f9"]

#: 替身在 `mate` 與 `mate_for_black` 模式下回報的應手著法。
FAKE_REPLY_MOVE = "e9f9"

#: `mate` 模式最後一行為 `score mate -15`(黑方視角:黑方將被殺)。
FAKE_MATE_IN = 15

#: 替身回應是即時的,逾時與節點數取小值使測試快。
FAKE_SEARCH_TIMEOUT = 1.0
FAKE_ACQUIRE_TIMEOUT = 1.0
FAKE_NODES = 1_000

#: 真實引擎的參數。節點數取設定的預設值(250k),與線上行為一致。
REAL_SEARCH_TIMEOUT = 15.0
REAL_ACQUIRE_TIMEOUT = 5.0
REAL_NODES = 250_000

#: 真實對局的步數上界。第 21 局的殺法約 31 個半回合(引擎回報 mate 16),
#: 此上界只是安全網:實作退化成走不完時測試失敗,而不是永遠跑下去。
MAX_PLIES = 60

requires_real_engine = pytest.mark.skipif(
    not REAL_ENGINE.is_file(), reason="真實引擎未安裝,請先執行 engine/fetch.sh"
)


# --- 測試題庫 -----------------------------------------------------------


def _write_position(
    root: pathlib.Path, position_id: int, side: Side, fen: str
) -> pathlib.Path:
    """在 `root/測試書/` 底下寫一題。出處由資料夾表達,題目 JSON 沒有出處欄位。"""
    payload: dict[str, Any] = {
        "id": position_id,
        "title": f"題目{position_id}",
        "description": f"測試題目 {position_id}",
        "fen": fen,
        "side_to_move": side.value,
        "difficulty": 3,
        "tags": ["連將殺"],
        "max_dtm": 16,
        "solvable": True,
    }
    folder = root / "測試書"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{position_id:04d}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _settings(
    engine_path: pathlib.Path, positions_dir: pathlib.Path, **overrides
) -> Settings:
    values: dict[str, Any] = {
        "pool_size": 1,
        "acquire_timeout": FAKE_ACQUIRE_TIMEOUT,
        "search_timeout": FAKE_SEARCH_TIMEOUT,
        "stop_grace_period": 0.5,
        "total_time_budget": 30.0,
        "search_nodes": FAKE_NODES,
        "positions_dir": positions_dir,
        "engine_path": engine_path,
    }
    values.update(overrides)
    return Settings(**values)


@dataclass(frozen=True)
class _Harness:
    """一組可用的服務,以及構成它的池與題庫(測試需要直接檢查兩者)。"""

    service: GameService
    pool: EnginePool
    repository: PositionRepository


@pytest.fixture
def make_service(tmp_path: pathlib.Path, fake_engine):
    """回傳工廠:`make_service(mode, start_side=...)` 給出一個以替身引擎為後端的服務。

    一律用替身:對局判定的語意與引擎實際算什麼無關,而真實引擎每個進程要載入
    51MB NNUE。真終局(合法著法為空)也只有替身能穩定構造。
    """
    harnesses: list[_Harness] = []

    def make(
        mode: str = "normal",
        *,
        start_side: Side = Side.RED,
        size: int = 1,
    ) -> _Harness:
        root = tmp_path / f"corpus_{len(harnesses)}"
        fen = RED_FEN if start_side is Side.RED else BLACK_FEN
        _write_position(root, PUZZLE_ID, start_side, fen)
        repository = PositionRepository(root)
        repository.load()

        engine = fake_engine(mode)
        pool = EnginePool(
            size=size,
            acquire_timeout=FAKE_ACQUIRE_TIMEOUT,
            engine_path=engine.path,
        )
        settings = _settings(engine.path, root)
        harness = _Harness(
            service=GameService(repository, pool, settings),
            pool=pool,
            repository=repository,
        )
        harnesses.append(harness)
        return harness

    yield make

    for harness in harnesses:
        harness.pool.shutdown()


# --- 輪方推導(1.5 的前提) ---------------------------------------------


@pytest.mark.parametrize(
    ("start", "move_count", "expected"),
    [
        (Side.RED, 0, Side.RED),
        (Side.RED, 1, Side.BLACK),
        (Side.RED, 2, Side.RED),
        (Side.RED, 5, Side.BLACK),
        (Side.RED, 30, Side.RED),
        (Side.BLACK, 0, Side.BLACK),
        (Side.BLACK, 1, Side.RED),
        (Side.BLACK, 2, Side.BLACK),
        (Side.BLACK, 7, Side.RED),
    ],
)
def test_side_after_alternates_from_the_puzzle_starting_side(
    start: Side, move_count: int, expected: Side
) -> None:
    """輪方 = 題目起手方加走法數的奇偶。起手方**不得**硬編為紅。"""
    assert side_after(start, move_count) is expected


@pytest.mark.parametrize("start_side", [Side.RED, Side.BLACK])
def test_state_side_to_move_follows_the_puzzle_starting_side(
    make_service, start_side: Side
) -> None:
    harness = make_service(start_side=start_side)
    assert harness.service.state(PUZZLE_ID, []).side_to_move is start_side
    assert harness.service.state(PUZZLE_ID, ["e8f9"]).side_to_move is not start_side
    assert harness.service.state(PUZZLE_ID, ["e8f9", "e9f9"]).side_to_move is start_side


@pytest.mark.parametrize("start_side", [Side.RED, Side.BLACK])
def test_side_to_move_needs_no_engine(make_service, start_side: Side) -> None:
    """1.5 的前提:輪方判定純由題目與走法數決定,不必借引擎。

    3.3 要在借引擎**之前**就能拒絕輪方不符的應手請求,否則池滿時會回報忙碌而非
    輪方不符。此處以「關閉池之後仍答得出輪方,但查詢局面已回報忙碌」證明兩者
    確實不相干。

    **兩種起手方都要測**:只測紅先的話,把輪方推導硬編成紅也會通過,而黑先排局
    會因此對合法的黑方應手誤回「輪方不符」—— 那正是本方法要防的事。
    """
    harness = make_service(start_side=start_side)
    harness.pool.shutdown()

    assert harness.service.side_to_move(PUZZLE_ID, []) is start_side
    assert harness.service.side_to_move(PUZZLE_ID, ["e8f9"]) is not start_side
    with pytest.raises(ServiceBusyError):
        harness.service.state(PUZZLE_ID, [])


# --- 起始局面(1.1) -----------------------------------------------------


def test_start_returns_the_puzzle_fen_and_its_opening_state(make_service) -> None:
    harness = make_service()
    fen, state = harness.service.start(PUZZLE_ID)

    assert fen == RED_FEN
    assert state.side_to_move is Side.RED
    assert state.legal_moves == FAKE_LEGAL_MOVES
    assert state.over is False
    assert state.winner is None


def test_start_reports_a_real_end_when_the_puzzle_starts_with_no_legal_moves(
    make_service,
) -> None:
    """起始局面本身就是真終局時同樣依合法著法數判定,不是特例。"""
    harness = make_service("no_legal_moves")
    _fen, state = harness.service.start(PUZZLE_ID)

    assert state.over is True
    assert state.winner is Side.BLACK  # 紅方無著可走,紅負


def test_unknown_position_id_is_reported_as_not_found(make_service) -> None:
    harness = make_service()
    with pytest.raises(PositionNotFoundError):
        harness.service.start(9999)
    with pytest.raises(PositionNotFoundError):
        harness.service.state(9999, [])


# --- 對局進行中(1.1、1.3) ---------------------------------------------


def test_state_reports_the_engine_legal_moves_while_the_game_is_in_progress(
    make_service,
) -> None:
    """合法著法一律取自引擎輸出,服務不實作任何規則邏輯(1.1)。"""
    harness = make_service()
    state = harness.service.state(PUZZLE_ID, ["e8f9"])

    assert state.legal_moves == FAKE_LEGAL_MOVES
    assert state.over is False
    assert state.winner is None


def test_mate_score_never_ends_the_game(make_service) -> None:
    """**本任務的核心保證(1.3、2.4)**:終局判定與評分無關。

    `mate` 模式的替身在同一局面下同時滿足兩件事 —— 合法著法非空,且引擎評分為
    `mate`。測試先向同一個池借引擎,證明評分確實是殺棋(否則這個測試等於什麼都
    沒測),再要求服務回報對局進行中。

    這條路徑若失守,使用者會在自己走完殺法之前就被系統宣告結束,正是專案刻意
    不做走脫判定表所要避免的行為。
    """
    harness = make_service("mate")

    with harness.pool.acquire() as engine:
        best = engine.best_move(RED_FEN, ["e8f9"], FAKE_NODES, FAKE_SEARCH_TIMEOUT)
    assert best.score is not None
    assert best.score.kind is ScoreKind.MATE, "替身沒給出 mate 分數,測試前提不成立"

    state = harness.service.state(PUZZLE_ID, ["e8f9"])

    assert state.legal_moves == FAKE_LEGAL_MOVES, "前提:此局面仍有合法著法"
    assert state.over is False, "引擎回報 mate 不得使對局結束(1.3、2.4)"
    assert state.winner is None


# --- 真終局(1.2) -------------------------------------------------------


@pytest.mark.parametrize(
    ("start_side", "move_count", "loser", "winner"),
    [
        (Side.RED, 0, Side.RED, Side.BLACK),
        (Side.RED, 1, Side.BLACK, Side.RED),
        (Side.BLACK, 0, Side.BLACK, Side.RED),
        (Side.BLACK, 1, Side.RED, Side.BLACK),
    ],
)
def test_no_legal_moves_ends_the_game_and_the_side_to_move_loses(
    make_service, start_side: Side, move_count: int, loser: Side, winner: Side
) -> None:
    """1.2:合法著法數為 0 時對局結束,且**該輪方為負方**。"""
    harness = make_service("no_legal_moves", start_side=start_side)
    state = harness.service.state(PUZZLE_ID, FAKE_LEGAL_MOVES[:move_count])

    assert state.side_to_move is loser
    assert state.over is True
    assert state.winner is winner


def test_a_finished_game_has_a_winner_and_no_legal_moves(make_service) -> None:
    """design 的 Postcondition:`over` 為真時 `winner` 必有值且合法著法為空。"""
    harness = make_service("no_legal_moves")
    state = harness.service.state(PUZZLE_ID, [])

    assert state.over is True
    assert state.winner is not None
    assert state.legal_moves == []


# --- 走不出的序列不得回報別的局面(5.3 的下游後果) --------------------


def test_an_illegal_sequence_never_yields_another_positions_state(
    make_service,
) -> None:
    """引擎對非法著法**靜默忽略**,失敗表現是回傳起始局面的資料而非拋錯。

    `ignores_moves` 替身重現該行為。服務必須讓協定層的序列驗證錯誤穿透出去,
    絕不把別的局面的合法著法當成當前局面的狀態交給前端。
    """
    harness = make_service("ignores_moves")
    with pytest.raises(IllegalMoveSequenceError):
        harness.service.state(PUZZLE_ID, ["a1a2"])


# --- 引擎歸還(4.4) -----------------------------------------------------


def test_every_query_returns_its_engine_to_the_pool(make_service) -> None:
    """成功與失敗兩條路徑都必須歸還,否則連續請求會慢慢耗盡池容量。"""
    harness = make_service("ignores_moves", size=2)

    harness.service.state(PUZZLE_ID, [])
    assert (harness.pool.available_count, harness.pool.borrowed_count) == (2, 0)

    with pytest.raises(IllegalMoveSequenceError):
        harness.service.state(PUZZLE_ID, ["a1a2"])
    assert (harness.pool.available_count, harness.pool.borrowed_count) == (2, 0)


# --- 三態信號的分類(2.1、2.2、2.3) -----------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        # mate 為負:黑方將被殺,紅方即將取勝,並附殺著倒數(2.2)
        (Score(ScoreKind.MATE, -15), (Signal.RED_WINNING, 15)),
        (Score(ScoreKind.MATE, -1), (Signal.RED_WINNING, 1)),
        # UCI 慣例:`mate 0` 代表輪方已被將死,同樣是紅方取勝,倒數為 0
        (Score(ScoreKind.MATE, 0), (Signal.RED_WINNING, 0)),
        # mate 為正:黑方可殺。倒數只在紅方取勝時提供,故為 None
        (Score(ScoreKind.MATE, 12), (Signal.BLACK_WINNING, None)),
        (Score(ScoreKind.MATE, 1), (Signal.BLACK_WINNING, None)),
        # cp 一律未知,**連正負號都不看**(2.3)
        (Score(ScoreKind.CP, 526), (Signal.UNKNOWN, None)),
        (Score(ScoreKind.CP, -526), (Signal.UNKNOWN, None)),
        (Score(ScoreKind.CP, 0), (Signal.UNKNOWN, None)),
        # 引擎完全沒給分數(真終局時的 `bestmove (none)`)同樣是未知
        (None, (Signal.UNKNOWN, None)),
    ],
)
def test_classify_score_maps_only_mate_scores_to_a_winning_side(
    score: Score | None, expected: tuple[Signal, int | None]
) -> None:
    """只有 `mate` 型別會產生勝負傾向;`cp` 與無分數一律「未搜得殺著」(2.3)。

    `cp 526` 這個值不是隨手取的:實測 200k 節點下,一個實為 `mate -15`(黑方將被
    殺)的局面就回報 `cp 526`,**方向還是相反的**。若據 cp 正負推斷,使用者會在
    自己必勝時被告知正在落敗 —— 那比誠實地說「未知」糟得多。
    """
    assert classify_score(score) == expected


# --- 黑方應手(1.4、2.1、2.4) -----------------------------------------


def test_black_reply_returns_the_move_the_signal_and_the_state_after_it(
    make_service,
) -> None:
    """1.4:回傳一著黑方著法、該局面的評分狀態,以及**走後**的完整對局狀態。

    走後狀態同批回傳,前端因此不必為了畫出新盤面再發一次局面查詢。
    """
    harness = make_service("mate")
    reply = harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert reply.move == FAKE_REPLY_MOVE
    assert reply.signal is Signal.RED_WINNING
    assert reply.mate_in == FAKE_MATE_IN
    # 起手方為紅、序列走了 1 步(紅)加 1 步應手(黑),因此輪回紅方
    assert reply.state.side_to_move is Side.RED
    assert reply.state.legal_moves == FAKE_LEGAL_MOVES
    assert reply.state.over is False
    assert reply.state.winner is None


def test_black_reply_reports_a_positive_mate_as_black_winning_without_a_countdown(
    make_service,
) -> None:
    """2.1:mate 為正代表黑方可殺。殺著倒數只在紅方即將取勝時提供(2.2)。"""
    harness = make_service("mate_for_black")
    reply = harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert reply.move == FAKE_REPLY_MOVE
    assert reply.signal is Signal.BLACK_WINNING
    assert reply.mate_in is None


def test_black_reply_reports_a_cp_score_as_unknown_and_never_infers_a_winner(
    make_service,
) -> None:
    """2.3:未搜得殺著時一律回報未知,**不得以其他評估數值推斷勝負傾向**。

    替身的 `normal` 模式回報 `score cp 25`(黑方視角為正)。若實作偷偷以 cp 正負
    推斷,這裡就會得到「黑方即將取勝」。此測試存在的意義即在於釘死那條路不存在。
    """
    harness = make_service("normal")

    with harness.pool.acquire() as engine:
        best = engine.best_move(RED_FEN, ["e8f9"], FAKE_NODES, FAKE_SEARCH_TIMEOUT)
    assert best.score is not None
    assert best.score.kind is ScoreKind.CP, "替身沒給出 cp 分數,測試前提不成立"
    assert best.score.value > 0, "前提:cp 為正,足以誘使實作推斷黑方占優"

    reply = harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert reply.signal is Signal.UNKNOWN
    assert reply.mate_in is None


def test_black_reply_signal_never_ends_the_game(make_service) -> None:
    """2.4:評分狀態為任何值都不改變對局是否結束的判定。

    `mate` 模式同時滿足「評分為殺棋」與「合法著法非空」,對局必須回報進行中。
    """
    harness = make_service("mate")
    reply = harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert reply.signal is Signal.RED_WINNING, "前提:信號確實指向勝負"
    assert reply.state.legal_moves == FAKE_LEGAL_MOVES, "前提:此局面仍有合法著法"
    assert reply.state.over is False
    assert reply.state.winner is None


# --- 完成狀態:紅方致勝一手後的同一次應手請求(1.2、1.4) --------------


def test_black_reply_reports_the_finished_game_in_the_same_request(
    make_service,
) -> None:
    """**任務 3.3 的完成狀態**:紅方走出致勝一手後,同一次應手請求即回傳
    「黑方無著、對局結束、紅方獲勝」,前端無須另外查詢局面狀態。

    這是每一題排局的**最後一手**,不是邊緣案例。引擎此時回 `bestmove (none)`,
    應手為 None,而走後狀態要問的正是當前這個局面 —— 序列不再追加任何一步。
    """
    harness = make_service("no_legal_moves")
    reply = harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert reply.move is None
    assert reply.state.side_to_move is Side.BLACK, "無著可走的是黑方"
    assert reply.state.legal_moves == []
    assert reply.state.over is True
    assert reply.state.winner is Side.RED
    # 替身在真終局完全不給分數,信號因此誠實地說未知 —— 勝負由 `state` 表達,不由
    # 信號表達。真實引擎此時另會給 `score mate 0`(信號為紅方取勝、倒數 0),兩種
    # 輸入都必須得到同一個 `state`;真實引擎那一側由本檔的真實引擎測試覆蓋。
    assert reply.signal is Signal.UNKNOWN
    assert reply.mate_in is None


# --- 輪方不符(1.5) -----------------------------------------------------


@pytest.mark.parametrize(
    ("start_side", "moves"),
    [
        (Side.RED, []),  # 紅先題目的起始局面
        (Side.RED, ["e8f9", "e9f9"]),  # 走完一個回合後又輪回紅方
        (Side.BLACK, ["e8f9"]),  # 黑先題目走一步後輪到紅方
    ],
)
def test_black_reply_is_rejected_when_red_is_to_move(
    make_service, start_side: Side, moves: list[str]
) -> None:
    """1.5:輪方為紅時拒絕應手請求並指出輪方不符。

    黑先題目也要測:把輪方推導硬編為紅會讓黑先排局的合法應手被誤拒。
    """
    harness = make_service(start_side=start_side)
    with pytest.raises(WrongSideToMoveError):
        harness.service.black_reply(PUZZLE_ID, moves)


def test_black_reply_rejects_a_wrong_side_before_borrowing_an_engine(
    make_service,
) -> None:
    """輪方不符必須在**借引擎之前**判定。

    否則池滿時使用者收到的是「服務忙碌」而非「輪方不符」—— 一個會讓人一直重試的
    誤導性訊息,而真正的問題是 client 的對局狀態已經對不上。

    以關閉池的方式證明:此時任何借用都會拋出 `ServiceBusyError`,若輪方判定發生在
    借用之後,這裡就會拿到忙碌錯誤而不是輪方不符。
    """
    harness = make_service()
    harness.pool.shutdown()

    with pytest.raises(WrongSideToMoveError):
        harness.service.black_reply(PUZZLE_ID, [])


def test_black_reply_reports_an_unknown_position_as_not_found(make_service) -> None:
    harness = make_service()
    with pytest.raises(PositionNotFoundError):
        harness.service.black_reply(9999, [])


# --- 走不出的序列(5.3 在應手路徑上的同一保證) ------------------------


def test_black_reply_never_answers_from_another_position(make_service) -> None:
    """引擎靜默忽略非法著法,失敗表現是**拿別的局面回答**而非拋錯。

    應手路徑與局面查詢路徑必須同樣讓協定層的序列驗證錯誤穿透出去。
    """
    harness = make_service("ignores_moves")
    with pytest.raises(IllegalMoveSequenceError):
        harness.service.black_reply(PUZZLE_ID, ["a1a2"])


# --- 一次請求只通過一次併發閘門(3.1 的承載前提、4.4) ------------------


def test_black_reply_borrows_exactly_one_engine(make_service, monkeypatch) -> None:
    """應手與其後的局面查詢共用同一次借用。

    每手棋若借兩次,同樣的池容量只能承載一半的人,且第二次借用可能在池滿時失敗 ——
    使用者會在應手已經算好之後才收到忙碌錯誤。
    """
    harness = make_service("mate")
    original = harness.pool.acquire
    borrows = 0

    def counting_acquire():
        nonlocal borrows
        borrows += 1
        return original()

    monkeypatch.setattr(harness.pool, "acquire", counting_acquire)
    harness.service.black_reply(PUZZLE_ID, ["e8f9"])

    assert borrows == 1


def test_black_reply_returns_its_engine_to_the_pool(make_service) -> None:
    """成功與失敗兩條路徑都必須歸還,否則連續應手會慢慢耗盡池容量(4.4)。"""
    working = make_service("mate", size=2)
    working.service.black_reply(PUZZLE_ID, ["e8f9"])
    assert (working.pool.available_count, working.pool.borrowed_count) == (2, 0)

    failing = make_service("ignores_moves", size=2)
    with pytest.raises(IllegalMoveSequenceError):
        failing.service.black_reply(PUZZLE_ID, ["a1a2"])
    assert (failing.pool.available_count, failing.pool.borrowed_count) == (2, 0)


# --- 依賴方向(design 的 File Structure Plan) --------------------------

#: `game.py` 位於左端第四層,只能向左依賴 types / errors / config / positions / engine。
GAME_ALLOWED_SERVICE_MODULES = {
    "service.types",
    "service.errors",
    "service.config",
    "service.positions",
    "service.engine",
}


def _imported_module_names(source_path: pathlib.Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = "." * node.level
                names.add(f"{prefix}{node.module or ''}")
                names.update(f"{prefix}{alias.name}" for alias in node.names)
            elif node.module:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_game_imports_only_modules_to_its_left() -> None:
    """`game.py` 不得 import `models.py` 或 `main.py`。"""
    path = SERVICE_DIR / "game.py"
    assert path.is_file(), f"{path} 必須存在"
    for name in _imported_module_names(path):
        assert not name.startswith("."), f"game.py 不得使用相對匯入 {name}"
        if not name.startswith("service"):
            continue
        root = ".".join(name.split(".")[:2])
        assert root in GAME_ALLOWED_SERVICE_MODULES, f"game.py 不得 import {name}"


def test_game_does_not_redefine_domain_types() -> None:
    """領域型別只有一份。

    兩份同名型別會讓 `isinstance` 檢查與日後 `models.py` 的 Pydantic 轉換靜默
    失效,故 `game.py` 必須由 `types.py` 匯入而非自行定義。
    """
    from service import game as game_module

    for name, expected in (
        ("Side", Side),
        ("GameState", GameState),
        ("Position", Position),
    ):
        assert getattr(game_module, name, expected) is expected


# --- 真實引擎:一整局走到真終局(1.1、1.2、1.3) ------------------------


@requires_real_engine
def test_real_engine_plays_the_puzzle_to_a_true_end_with_red_winning() -> None:
    """自《適情雅趣》第 21 局的起始局面走到紅勝,證明勝方認定正確。

    每一步都取引擎的最佳著法(紅方走殺法、黑方走最頑強的抵抗),每走一步就問一次
    對局狀態。過程中兩件事必須同時成立:

    - 引擎早就回報 mate,但只要合法著法非空,對局就回報進行中(1.3、2.4)
    - 只有在黑方真的無著可走的那一手才停局,且勝方為紅(1.2)

    這是唯一無法用替身取信的斷言 —— 替身的「終局」是寫死的輸出,只有真實引擎能
    證明服務對真實棋局的停局時機與勝方認定都正確。
    """
    repository = PositionRepository(DEFAULT_POSITIONS_DIR)
    repository.load()
    settings = _settings(
        REAL_ENGINE,
        DEFAULT_POSITIONS_DIR,
        acquire_timeout=REAL_ACQUIRE_TIMEOUT,
        search_timeout=REAL_SEARCH_TIMEOUT,
        stop_grace_period=1.0,
        total_time_budget=60.0,
        search_nodes=REAL_NODES,
    )
    pool = EnginePool(
        size=1, acquire_timeout=REAL_ACQUIRE_TIMEOUT, engine_path=REAL_ENGINE
    )
    service = GameService(repository, pool, settings)

    try:
        fen, state = service.start(PUZZLE_ID)
        assert fen == RED_FEN
        assert state.over is False

        moves: list[str] = []
        mate_seen_while_in_progress = False

        for _ in range(MAX_PLIES):
            if state.over:
                break
            assert state.winner is None, "尚未終局卻已指出勝方"
            with pool.acquire() as engine:
                best = engine.best_move(fen, moves, REAL_NODES, REAL_SEARCH_TIMEOUT)
            assert best.move is not None, "合法著法非空時引擎不應回報無著可走"
            assert best.move in state.legal_moves, (
                f"引擎走的 {best.move} 不在服務回報的合法著法內"
            )
            if best.score is not None and best.score.kind is ScoreKind.MATE:
                mate_seen_while_in_progress = True
            moves.append(best.move)
            state = service.state(PUZZLE_ID, moves)

        assert state.over is True, f"走了 {len(moves)} 步仍未終局"
        assert state.legal_moves == []
        assert state.side_to_move is Side.BLACK, "終局時無著可走的應是黑方"
        assert state.winner is Side.RED
        assert mate_seen_while_in_progress, (
            "整局都沒出現 mate 評分,「評分不影響終局判定」這一點等於沒被驗到"
        )
    finally:
        pool.shutdown()


@requires_real_engine
def test_real_engine_black_reply_signals_red_winning_until_the_game_ends() -> None:
    """真實引擎下的三態信號分類與完成狀態(1.4、2.1、2.2、2.3、2.4)。

    紅方走引擎的最佳著法(即殺法),黑方一律經由 `black_reply` 應手,直到終局。
    三件事必須同時成立:

    - 應手途中至少出現一次「紅方即將取勝」並附殺著倒數 —— 這是 250k 節點這個
      設定值存在的理由(低於此門檻信號會落在「未知」,Requirement 2 形同虛設)
    - 信號指向紅方取勝的同時,對局仍回報進行中(2.4)
    - **最後一次應手直接回傳「黑方無著、對局結束、紅方獲勝」**,不必再查一次局面

    倒數只斷言「有值、在對局中為正、且曾經超過 1」而不斷言確數:250k 節點下的 DTM
    可能高估 1 步(實測 250k 回報 16,1M 才收斂到 15),斷言確數等於把一個刻意接受
    的誤差寫成契約。

    **實測補記:** 黑方已被將死的局面,真實引擎在 `bestmove (none)` 之外還會給出
    `score mate 0`,因此最後一次應手的信號是「紅方即將取勝、倒數 0」而非「未知」。
    這與 `state` 的結論一致,不是矛盾;替身的 `no_legal_moves` 模式則完全不給分數,
    覆蓋的是同一路徑的另一種輸入(見
    `test_black_reply_reports_the_finished_game_in_the_same_request`)。
    """
    repository = PositionRepository(DEFAULT_POSITIONS_DIR)
    repository.load()
    settings = _settings(
        REAL_ENGINE,
        DEFAULT_POSITIONS_DIR,
        acquire_timeout=REAL_ACQUIRE_TIMEOUT,
        search_timeout=REAL_SEARCH_TIMEOUT,
        stop_grace_period=1.0,
        total_time_budget=60.0,
        search_nodes=REAL_NODES,
    )
    pool = EnginePool(
        size=1, acquire_timeout=REAL_ACQUIRE_TIMEOUT, engine_path=REAL_ENGINE
    )
    service = GameService(repository, pool, settings)

    try:
        fen, state = service.start(PUZZLE_ID)
        moves: list[str] = []
        countdowns: list[int] = []
        reply = None

        for _ in range(MAX_PLIES):
            if state.side_to_move is Side.RED:
                assert state.over is False, "此題應以黑方無著可走告終,不該是紅方"
                with pool.acquire() as engine:
                    best = engine.best_move(fen, moves, REAL_NODES, REAL_SEARCH_TIMEOUT)
                assert best.move is not None, "合法著法非空時引擎不應回報無著可走"
                moves.append(best.move)
                state = service.state(PUZZLE_ID, moves)
                continue

            # 輪到黑方 —— 即使上一手已經把黑方將死也照樣送出應手請求,那正是
            # 完成狀態要驗的路徑:前端不必先查局面才敢問應手。
            black_legal_moves = state.legal_moves
            reply = service.black_reply(PUZZLE_ID, moves)
            if reply.signal is Signal.RED_WINNING:
                assert reply.mate_in is not None, "紅方即將取勝時必須附殺著倒數(2.2)"
                countdowns.append(reply.mate_in)
            if reply.move is None:
                break
            assert reply.mate_in is None or reply.mate_in > 0, (
                "黑方尚有應手可走,殺著倒數不應為 0"
            )
            assert reply.move in black_legal_moves, (
                f"應手 {reply.move} 不在服務回報的黑方合法著法內"
            )
            assert reply.state.over is False, "尚有應手可走卻宣告對局結束(1.3、2.4)"
            moves.append(reply.move)
            state = reply.state

        assert reply is not None, "整局沒發生過黑方應手,本測試等於什麼都沒測"
        assert countdowns, (
            "整局都沒出現「紅方即將取勝」,250k 節點的取捨與 Requirement 2 都未被驗到"
        )
        assert max(countdowns) > 1, (
            "倒數整局都不超過 1,等於只驗到終局前一刻,搜尋深度的取捨沒被驗到"
        )
        # 完成狀態:紅方致勝一手後的同一次應手請求就交代完勝負
        assert reply.move is None
        assert reply.state.over is True
        assert reply.state.side_to_move is Side.BLACK
        assert reply.state.legal_moves == []
        assert reply.state.winner is Side.RED
    finally:
        pool.shutdown()
