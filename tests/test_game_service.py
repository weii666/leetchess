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
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

#: 合成測試題庫用的題號。與真實題庫無關,只需在該次測試的暫存題庫裡唯一。
PUZZLE_ID = 1

#: **真實**題庫裡《適情雅趣》第二一局的題號。題號即書上的局號,兩個真實引擎的
#: 測試載入 `positions/` 本身,因此不能沿用上面那個合成題號。
REAL_PUZZLE_ID = 21

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


def _write_position(root: pathlib.Path, position_id: int, fen: str) -> pathlib.Path:
    """在 `root/測試書/` 底下寫一題。

    題目 JSON 沒有出處欄位(出處由資料夾表達),也沒有 `side_to_move` —— 起手方是
    `fen` 走子方那一欄的事,所以 `RED_FEN` 與 `BLACK_FEN` 的差別就是起手方的差別。
    檔案內容是陣列,即使只裝一題。
    """
    payload: dict[str, Any] = {
        "id": position_id,
        "title": f"題目{position_id}",
        "description": f"測試題目 {position_id}",
        "fen": fen,
        "difficulty": 3,
        "tags": ["連將殺"],
        "max_dtm": 16,
    }
    folder = root / "測試書"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{position_id}-{position_id}.json"
    path.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")
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
        _write_position(root, PUZZLE_ID, fen)
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
        (Side.RED, 2, Side.RED),
        (Side.RED, 30, Side.RED),
        (Side.BLACK, 1, Side.RED),
        (Side.BLACK, 7, Side.RED),
    ],
)
def test_side_after_alternates_from_the_puzzle_starting_side(
    start: Side, move_count: int, expected: Side
) -> None:
    """輪方 = 題目起手方加走法數的奇偶。起手方**不得**硬編為紅。"""
    assert side_after(start, move_count) is expected


@pytest.mark.parametrize("start_side", [Side.BLACK])
def test_state_side_to_move_follows_the_puzzle_starting_side(
    make_service, start_side: Side
) -> None:
    harness = make_service(start_side=start_side)
    assert harness.service.state(PUZZLE_ID, []).side_to_move is start_side
    assert harness.service.state(PUZZLE_ID, ["e8f9"]).side_to_move is not start_side
    assert harness.service.state(PUZZLE_ID, ["e8f9", "e9f9"]).side_to_move is start_side


@pytest.mark.parametrize("start_side", [Side.BLACK])
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


def test_start_reports_a_real_end_when_the_puzzle_starts_with_no_legal_moves(
    make_service,
) -> None:
    """起始局面本身就是真終局時同樣依合法著法數判定,不是特例。"""
    harness = make_service("no_legal_moves")
    _fen, state = harness.service.start(PUZZLE_ID)

    assert state.over is True
    assert state.winner is Side.BLACK  # 紅方無著可走,紅負


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


# --- 真終局(1.2) -------------------------------------------------------


@pytest.mark.parametrize(
    ("start_side", "move_count", "loser", "winner"),
    [
        (Side.RED, 0, Side.RED, Side.BLACK),
        (Side.BLACK, 0, Side.BLACK, Side.RED),
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
        (Score(ScoreKind.MATE, -1), (Signal.RED_WINNING, 1)),
        # mate 為正:黑方可殺。倒數只在紅方取勝時提供,故為 None
        (Score(ScoreKind.MATE, 12), (Signal.BLACK_WINNING, None)),
        # cp 一律未知,**連正負號都不看**(2.3)
        (Score(ScoreKind.CP, 526), (Signal.UNKNOWN, None)),
        (Score(ScoreKind.CP, 0), (Signal.UNKNOWN, None)),
    ],
    ids=["mate_negative", "mate_positive", "cp_positive", "cp_zero"],
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
        (Side.RED, ["e8f9", "e9f9"]),  # 走完一個回合後又輪回紅方
    ],
    ids=["red_after_full_round"],
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


# --- 走不出的序列(5.3 在應手路徑上的同一保證) ------------------------


def test_black_reply_never_answers_from_another_position(make_service) -> None:
    """引擎靜默忽略非法著法,失敗表現是**拿別的局面回答**而非拋錯。

    應手路徑與局面查詢路徑必須同樣讓協定層的序列驗證錯誤穿透出去。
    """
    harness = make_service("ignores_moves")
    with pytest.raises(IllegalMoveSequenceError):
        harness.service.black_reply(PUZZLE_ID, ["a1a2"])


# --- 一次請求只通過一次併發閘門(3.1 的承載前提、4.4) ------------------


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
        fen, state = service.start(REAL_PUZZLE_ID)
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
                state = service.state(REAL_PUZZLE_ID, moves)
                continue

            # 輪到黑方 —— 即使上一手已經把黑方將死也照樣送出應手請求,那正是
            # 完成狀態要驗的路徑:前端不必先查局面才敢問應手。
            black_legal_moves = state.legal_moves
            reply = service.black_reply(REAL_PUZZLE_ID, moves)
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
