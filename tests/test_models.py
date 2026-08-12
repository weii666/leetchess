"""HTTP 請求回應模型與著法格式驗證的測試。

對應 tasks 4.1、requirements 5.2(與 5.1、5.4 的模型側)。

本檔釘死三件事,每一件都對應一個已知會產生真實 bug 的失敗模式:

1. **著法格式驗證必須在進入服務層之前攔截**(5.2)。證明方式不是「有拋錯」,而是
   以計數的替身池斷言**引擎池的借用次數為 0** —— 若驗證被挪到服務層之後,拋錯
   一樣會發生,但使用者已經占掉一個併發閘門的名額。

2. **`mate_in` 可能為 `0`,且不得被 falsy 判斷吞掉。** 真實引擎在黑方被將死時輸出
   `score mate 0` + `bestmove (none)`,故**每一題排局的最後一手**都會走這條路。
   模型若用 `if mate_in:`、`mate_in or None`、`exclude_none` 之類的寫法,終局那手
   的倒數就會靜默消失。因此本檔對整份序列化結果做**完整相等**斷言,不只檢查有值。

3. **`move` 為 `None` 時 `signal` 仍可能有值。** 「黑方無著」與「信號未知」是兩件
   獨立的事,契約必須同時容得下 `move=None + signal=red_winning`(被將死)與
   `move=None + signal=unknown`(引擎一分未報)兩種組合。
"""

from __future__ import annotations

import pathlib
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import ValidationError

from service.config import DEFAULT_POSITIONS_DIR, Settings
from service.errors import ErrorCode, InternalError, InvalidMoveFormatError
from service.game import GameService
from service.models import (
    MAX_FEN_LENGTH,
    BlackMoveResponse,
    CandidatePositionRequest,
    ErrorResponse,
    GameStateResponse,
    MoveSequenceRequest,
    PositionResponse,
    validate_fen,
    validate_move,
)
from service.positions import PositionRepository
from service.types import (
    BestMove,
    BlackReply,
    GameState,
    Position,
    Score,
    ScoreKind,
    Side,
    Signal,
)

#: 《適情雅趣》第 21 局起始局面(紅先),與其他測試共用同一份。
RED_FEN = "3ak1b2/4a4/4b4/9/9/9/9/4B4/4A4/2BAK1R2 w - - 0 1"


# --- 著法格式(5.2)---------------------------------------------------------


@pytest.mark.parametrize(
    "move",
    [
        "h2e2",  # 炮二平五,tech.md 的範例
        "i9i8",  # 檔與列的上界
    ],
)
def test_valid_uci_moves_are_accepted(move: str) -> None:
    """檔 `a`–`i`、列 `0`–`9` 的四字元著法為合法格式(steering tech.md 走法格式)。"""
    assert validate_move(move) == move


@pytest.mark.parametrize(
    "move",
    [
        "j0a1",  # 檔越界:象棋只有 a–i,沒有 j
        "h2e",  # 太短
        "H2E2",  # 大寫
        "h2e2 ",  # 尾隨空白
        "",
        "0-0",  # 西洋棋的王車易位記法
    ],
)
def test_invalid_uci_moves_are_rejected(move: str) -> None:
    assert isinstance(move, str)
    with pytest.raises(InvalidMoveFormatError):
        validate_move(move)


def test_rejection_names_the_offending_move() -> None:
    """5.2 明文要求「指出不合法的著法」,否則使用者不知道是哪一手打錯。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        validate_move("j0a1")
    assert "j0a1" in excinfo.value.message


# --- 請求模型:走法序列由請求主體承載 ---------------------------------------


def test_move_sequence_request_carries_the_sequence_in_the_body() -> None:
    """走法序列走請求主體而非 query string(design:走法序列以請求主體傳遞)。

    POC 的 `/api/state?moves=...` 在長局會使 URL 過長,這是本 spec 明列要替換的
    技術債之一。
    """
    request = MoveSequenceRequest.model_validate(
        {"position_id": 1, "moves": ["h2e2", "e9f9", "b0c2"]}
    )
    assert request.position_id == 1
    assert request.moves == ["h2e2", "e9f9", "b0c2"]


def test_move_sequence_request_rejects_unknown_fields() -> None:
    """拼錯的欄位必須失敗而非被忽略。

    `moves` 拼成 `move` 若被靜默忽略,請求會被當成「起始局面」處理,使用者拿到的
    是另一個局面的合法著法而非錯誤 —— 與引擎靜默忽略非法著法同一類的失敗模式。
    """
    with pytest.raises(ValidationError):
        MoveSequenceRequest.model_validate(
            {"position_id": 1, "move": ["h2e2"], "moves": []}
        )


def test_move_sequence_request_names_the_offending_move_not_the_first_one() -> None:
    """序列中第 3 手不合法時,訊息要指出第 3 手,不是第 1 手。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        MoveSequenceRequest.model_validate(
            {"position_id": 1, "moves": ["h2e2", "e9f9", "zzzz"]}
        )
    assert "zzzz" in excinfo.value.message
    assert "h2e2" not in excinfo.value.message


@pytest.mark.parametrize("value", [None, ["h2e2"]], ids=["None", "list"])
def test_move_sequence_request_rejects_non_string_moves(value: Any) -> None:
    """非字串的元素同樣走「著法格式不合法」,而不是掉進框架的泛用結構錯誤。

    否則 client 會對同一類使用者輸入收到兩種不同的錯誤類別,無法一致處理(5.1)。
    """
    with pytest.raises(InvalidMoveFormatError):
        MoveSequenceRequest.model_validate({"position_id": 1, "moves": [value]})


def test_rejection_message_does_not_echo_an_unbounded_payload() -> None:
    """訊息會回給使用者,不得把任意長度的輸入原樣送回去。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        MoveSequenceRequest.model_validate({"position_id": 1, "moves": ["x" * 5000]})
    assert len(excinfo.value.message) < 200


# --- 驗證發生在服務層之前,且未觸及引擎池(4.1 的完成狀態)-------------------


class _StubEngine:
    """只回一組固定合法著法的引擎替身。本檔不測引擎行為。"""

    def legal_moves(self, fen: str, moves: list[str], timeout: float) -> list[str]:
        return ["b0c2", "g3g4"]

    def best_move(
        self, fen: str, moves: list[str], nodes: int, timeout: float
    ) -> BestMove:
        return BestMove(move="e9f9", score=Score(kind=ScoreKind.MATE, value=-16))


class _CountingPool:
    """記錄借用次數的引擎池替身 —— 併發閘門是否被觸及,就看這個數字。"""

    def __init__(self) -> None:
        self.acquisitions = 0

    @contextmanager
    def acquire(self):
        self.acquisitions += 1
        yield _StubEngine()


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        pool_size=1,
        acquire_timeout=1.0,
        search_timeout=1.0,
        stop_grace_period=0.5,
        total_time_budget=10.0,
        search_nodes=1000,
        positions_dir=tmp_path,
        engine_path=tmp_path / "engine",
    )


class _StubRepository:
    def __init__(self, position: Position) -> None:
        self._position = position

    def get(self, position_id: int) -> Position:
        return self._position


@pytest.fixture
def routed(tmp_path: pathlib.Path):
    """模擬任務 4.2 的路由:先過 HTTP 模型,再進入服務層。

    這正是 4.1 完成狀態要驗的順序 —— 驗證若挪到服務層之後,拋錯照樣發生,但引擎
    池已經被借用過,格式錯誤的請求就會占掉併發閘門的名額。
    """
    position = Position(
        id=1,
        title="測試題",
        description="測試用題目",
        fen=RED_FEN,
        side_to_move=Side.RED,
        difficulty=3,
        tags=["連將殺"],
    )
    pool = _CountingPool()
    service = GameService(_StubRepository(position), pool, _settings(tmp_path))

    def handle(payload: dict[str, Any]) -> GameStateResponse:
        request = MoveSequenceRequest.model_validate(payload)  # HTTP 層
        return GameStateResponse.from_domain(
            service.state(request.position_id, request.moves)  # 服務層
        )

    return handle, pool


def test_valid_move_does_reach_the_engine_pool(routed) -> None:
    """對照組:證明上一個測試不是因為根本沒接上服務層才通過的。"""
    handle, pool = routed
    response = handle({"position_id": 1, "moves": ["h2e2"]})
    assert pool.acquisitions == 1
    assert response.legal_moves == ["b0c2", "g3g4"]


# --- 對局狀態回應 -----------------------------------------------------------


def test_game_state_response_carries_the_winner_at_a_true_terminal() -> None:
    state = GameState(
        side_to_move=Side.BLACK, legal_moves=[], over=True, winner=Side.RED
    )
    assert GameStateResponse.from_domain(state).model_dump(mode="json") == {
        "side_to_move": "black",
        "legal_moves": [],
        "over": True,
        "winner": "red",
    }


# --- 黑方應手回應:mate_in 為 0 與 move 為 None 的組合 -----------------------


def _terminal_state() -> GameState:
    return GameState(
        side_to_move=Side.BLACK, legal_moves=[], over=True, winner=Side.RED
    )


def test_black_move_response_serializes_a_zero_mate_in_to_json() -> None:
    """欄位不得在 JSON 序列化這一層被省略 —— 前端讀到的是 JSON,不是 dict。"""
    reply = BlackReply(
        move=None, signal=Signal.RED_WINNING, mate_in=0, state=_terminal_state()
    )
    payload = BlackMoveResponse.from_domain(reply).model_dump_json()
    assert '"mate_in":0' in payload.replace(" ", "")


def test_black_move_response_carries_move_signal_and_state_together() -> None:
    reply = BlackReply(
        move="e9f9",
        signal=Signal.RED_WINNING,
        mate_in=15,
        state=GameState(
            side_to_move=Side.RED,
            legal_moves=["g3g4"],
            over=False,
            winner=None,
        ),
    )
    dumped = BlackMoveResponse.from_domain(reply).model_dump(mode="json")
    assert dumped["move"] == "e9f9"
    assert dumped["signal"] == "red_winning"
    assert dumped["mate_in"] == 15
    assert dumped["state"]["over"] is False


# --- 題目起始局面回應 -------------------------------------------------------


def _position(**overrides: Any) -> Position:
    values: dict[str, Any] = {
        "id": 21,
        "title": "野馬操田",
        "description": "《適情雅趣》第 21 局",
        "fen": RED_FEN,
        "side_to_move": Side.RED,
        "difficulty": 3,
        "tags": ["連將殺", "馬後炮"],
        "max_dtm": 16,
    }
    values.update(overrides)
    return Position(**values)


def test_position_response_carries_the_start_position_and_puzzle_info() -> None:
    """6.1:回傳起始局面**與對局所需的題目資訊**。"""
    state = GameState(
        side_to_move=Side.RED, legal_moves=["g3g4"], over=False, winner=None
    )
    dumped = PositionResponse.from_domain(
        _position(), state, source="適情雅趣"
    ).model_dump(mode="json")
    assert dumped == {
        "id": 21,
        "title": "野馬操田",
        "description": "《適情雅趣》第 21 局",
        "fen": RED_FEN,
        "side_to_move": "red",
        "difficulty": 3,
        "tags": ["連將殺", "馬後炮"],
        "max_dtm": 16,
        "source": "適情雅趣",
        "state": {
            "side_to_move": "red",
            "legal_moves": ["g3g4"],
            "over": False,
            "winner": None,
        },
    }


def test_position_response_keeps_optional_fields_present() -> None:
    """欄位為空時仍須存在,前端才不必分辨「沒有這個欄位」與「值為空」。"""
    state = GameState(
        side_to_move=Side.RED, legal_moves=["g3g4"], over=False, winner=None
    )
    dumped = PositionResponse.from_domain(
        _position(max_dtm=None), state
    ).model_dump(mode="json")
    assert "max_dtm" in dumped
    assert "source" in dumped


# --- 錯誤回應 ---------------------------------------------------------------


def test_error_response_does_not_leak_internal_details() -> None:
    """5.4:錯誤回應不得含內部路徑、堆疊或引擎原始輸出。

    `InternalError` 刻意丟棄呼叫端傳入的訊息,模型必須取用它的 `message` 而非
    `str(exc)` 或例外的 `args` —— 前兩者是同一個值,第三者不是。
    """
    leaky = InternalError("/Users/someone/service/engine/process.py:88 bestmove (none)")
    dumped = ErrorResponse.from_error(leaky).model_dump(mode="json")
    assert "/Users/" not in dumped["message"]
    assert "process.py" not in dumped["message"]
    assert dumped["code"] == "INTERNAL"


# --- 依賴方向 ---------------------------------------------------------------


def test_models_does_not_redefine_domain_types() -> None:
    """領域型別只有一份,模型只做邊界轉換。

    重新定義 `Side` 或 `Signal` 會讓 `is` 比較與 `isinstance` 靜默失效,兩份列舉
    的成員永遠不相等。
    """
    from service import models

    assert models.Side is Side
    assert models.Signal is Signal


# --- 送往引擎的 FEN 字元把關(9.1–9.5)---------------------------------------
#
# 這是專案中**第一條使用者文字進到引擎輸入**的路徑。`engine/process.py` 的
# `_position_command()` 是裸的字串插值(`f"position fen {fen}"`),而 UCI 協定行導向
# —— FEN 裡一個換行就能把一行指令變成兩行,第二行由對方決定內容。
#
# 本節釘死的是**單一保證**:這個字串跳不出這一行指令。刻意**不驗 FEN 文法** ——
# 局面合不合法由引擎判定(`tech.md` 第二條不可動搖約束),在此重做一份只會製造第二
# 個真相來源與誤擋(9.5)。因此通過側的回歸網不是人工挑的樣本,而是**題庫裡現有的
# 每一個 FEN**:字元集若訂得比真實 FEN 需要的窄,這裡會先紅,而不是等到某一題進不
# 了庫才發現。


def _corpus_fens() -> list[tuple[int, str]]:
    """題庫中現有的每一個 (題號, FEN)。

    走 `PositionRepository` 而不是自己剖 JSON,題庫長到 200 題、檔案改名或分卷時
    這張回歸網都不必跟著改 —— 它問的一直是「現在庫裡有的那些」。
    """
    repository = PositionRepository(DEFAULT_POSITIONS_DIR)
    repository.load()
    return [(position.id, position.fen) for position in repository.all()]


CORPUS_FENS = _corpus_fens()


def test_the_corpus_regression_net_is_not_empty() -> None:
    """題庫讀不到東西時,下面那條 parametrize 會變成零個案例並靜默全綠。

    通過側的回歸網若能在「沒有樣本」的情況下通過,它就不再是回歸網。
    """
    assert CORPUS_FENS


@pytest.mark.parametrize(
    ("position_id", "fen"), CORPUS_FENS, ids=[str(pid) for pid, _ in CORPUS_FENS]
)
def test_fen_guard_accepts_every_fen_in_the_corpus(position_id: int, fen: str) -> None:
    """既有題庫中的每一個 FEN 全數通過(design Testing Strategy 單元測試 5)。

    字元集訂得過窄會誤擋合法題目,而誤擋的代價是「這一題進不了庫」。
    """
    assert validate_fen(fen) == fen


@pytest.mark.parametrize(
    ("label", "fen"),
    [
        ("換行", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\nquit"),
        ("歸位字元", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\rgo infinite"),
        ("tab", "9/9/9/9/9/9/9/9/9/9\tw - - 0 1"),
        ("空位元組", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\x00"),
        ("垂直定位", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\x0b"),
        ("換頁", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\x0c"),
        ("跳脫字元", "9/9/9/9/9/9/9/9/9/9 w - - 0 1\x1b[2J"),
        ("單獨的換行", "\n"),
    ],
)
def test_fen_guard_rejects_control_characters(label: str, fen: str) -> None:
    """控制字元一律不在集合內(9.1)。

    換行與歸位字元是**唯一真正能改變指令結構**的兩個,其餘控制字元一起擋掉是因為
    白名單的邊界要說得清楚 —— 「FEN 用得到的字元」比「已知有害的字元」好維護。
    """
    with pytest.raises(InvalidMoveFormatError):
        validate_fen(fen)


@pytest.mark.parametrize(
    "fen",
    [
        "3ak1b2/4a4/4b4/9/9/9/9/4B4/4A4/2BAK1R2 w - - 0 1; go infinite",
        "3ak1b2/4a4/4b4/9/9/9/9/4B4/4A4/2BAK1R2 w - - 0 1 | quit",
        "9/9 w - - 0 1\u2028position startpos",  # Unicode 行分隔符
        "9/9 w - - 0 1\x85go infinite",  # NEL,某些讀取器視為換行
        "9/9/9/9/9/9/9/9/9/9 w - - 0 1\u00a0",  # 不換行空白,不是 ASCII 空白
        "ＲＮＢ/9 w - - 0 1",  # 全形拉丁字母
        "炮二平五",  # 中文記譜:顯示層的表示法,不得進入服務
        "fen $(rm -rf /)",
    ],
)
def test_fen_guard_rejects_characters_outside_the_notation(fen: str) -> None:
    """FEN 表示法用不到的字元一律拒絕(9.2)。"""
    with pytest.raises(InvalidMoveFormatError):
        validate_fen(fen)


def test_fen_guard_rejects_an_over_length_string() -> None:
    """超出長度上限即拒絕(9.3)。

    全部由白名單字元組成也一樣 —— 長度是獨立的一道,不是字元集的副產品。
    """
    with pytest.raises(InvalidMoveFormatError):
        validate_fen("9" * (MAX_FEN_LENGTH + 1))


def test_fen_guard_accepts_a_string_at_the_length_limit() -> None:
    """上限本身是可通過的,界線不得差一。"""
    at_limit = "9" * MAX_FEN_LENGTH
    assert validate_fen(at_limit) == at_limit


def test_the_length_limit_clears_any_real_fen_by_a_wide_margin() -> None:
    """上限必須明顯高於任何真實 FEN,否則它會變成誤擋的來源。

    象棋盤 90 格全滿加 9 個 `/` 為 99 字元,再加走子方等四欄約 110 —— 上限取在這
    之上一大截才算「明顯高於」。
    """
    assert MAX_FEN_LENGTH >= 2 * max(len(fen) for _, fen in CORPUS_FENS)
    assert MAX_FEN_LENGTH >= 128


def test_fen_rejection_carries_the_existing_error_class() -> None:
    """沿用既有著法格式驗證的錯誤類別(9.4)。

    不另立第八種類別碼:對 client 而言「請求裡的字串形狀不對」就是同一件事,而
    `INVALID_MOVE_FORMAT` 已是 400。錯誤類別若不同,前端要為同一類輸入寫兩套處理。
    """
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        validate_fen("bad\nfen")
    assert excinfo.value.code is ErrorCode.INVALID_MOVE_FORMAT
    assert excinfo.value.http_status == 400


def test_fen_rejection_message_does_not_echo_an_unbounded_payload() -> None:
    """訊息會回給使用者,不得把任意長度的輸入原樣送回去。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        validate_fen("9" * 50_000)
    assert len(excinfo.value.message) < 200


def test_fen_rejection_message_does_not_echo_control_characters() -> None:
    """被擋下的控制字元不得原樣出現在回給使用者的訊息裡。

    訊息會流進日誌與畫面 —— 把換行原樣送回去,等於在另一個行導向的介面上重演同一
    個問題。
    """
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        validate_fen("9/9 w - - 0 1\nquit")
    assert "\n" not in excinfo.value.message
    assert "\r" not in excinfo.value.message


@pytest.mark.parametrize(
    "fen",
    [
        "9/9/9/9/9/9/9/9/9/9 w - - 0 1",  # 空盤:引擎會拒,字元層不拒
        "kkkkkkkkk/9/9/9/9/9/9/9/9/9 b - - 0 1",  # 九個將:棋規不合法
        "3ak1b2 x - - 0 1",  # 走子方不是 w/b
        "1/2/3",  # 只有三列
        "zzz",  # 根本不是 FEN
        "-",
    ],
)
def test_fen_guard_does_not_validate_the_grammar(fen: str) -> None:
    """本層**不判定局面合法性**(9.5)。

    以上每一個字串在字元層都是安全的 —— 它們跳不出這一行指令。合不合法由引擎回答,
    在此重做一份只會製造第二個真相來源與誤擋。這個測試釘住的是**沒有做什麼**。
    """
    assert validate_fen(fen) == fen


# --- 候選題目的請求模型 -----------------------------------------------------


def _candidate_position(**overrides: Any) -> dict[str, Any]:
    """一份形狀完整的候選題目。欄位取自 `structure.md` 的題目 schema。"""
    payload: dict[str, Any] = {
        "id": 21,
        "title": "野馬操田",
        "description": "《適情雅趣》第 21 局",
        "fen": RED_FEN,
        "difficulty": 3,
        "tags": ["連將殺"],
    }
    payload.update(overrides)
    return payload


def test_candidate_request_accepts_a_well_formed_position() -> None:
    request = CandidatePositionRequest.model_validate(
        {"position": _candidate_position()}
    )
    assert request.position == _candidate_position()


def test_candidate_request_rejects_a_position_whose_fen_carries_a_newline() -> None:
    """把關發生在請求模型上,因此在路由函式被呼叫之前(9.1、design Invariants)。"""
    with pytest.raises(InvalidMoveFormatError):
        CandidatePositionRequest.model_validate(
            {"position": _candidate_position(fen=RED_FEN + "\nquit")}
        )


def test_candidate_request_does_not_declare_the_position_schema_fields() -> None:
    """題目 schema 的權威在 `service/positions.py`,此處不得再宣告一次欄位。

    宣告了就是第二份規則:兩邊對「難度值域」或「必填欄位」的看法遲早分歧,而本
    spec 存在的理由之一正是避免這件事。`position` 因此是未經模型化的物件。
    """
    declared = set(CandidatePositionRequest.model_fields)
    assert declared == {"position"}


def test_candidate_request_rejects_unknown_fields() -> None:
    """拼錯的欄位必須失敗而非被忽略 —— 與 `MoveSequenceRequest` 同一個理由。"""
    with pytest.raises(ValidationError):
        CandidatePositionRequest.model_validate(
            {"position": _candidate_position(), "positions": {}}
        )


@pytest.mark.parametrize("position", ["not an object", 42, None, ["a"], True])
def test_candidate_request_rejects_a_non_object_position(position: Any) -> None:
    """`position` 不是物件是**請求形狀**的問題,交由框架回報結構錯誤。"""
    with pytest.raises(ValidationError):
        CandidatePositionRequest.model_validate({"position": position})


@pytest.mark.parametrize(
    "position",
    [
        {"id": 21, "title": "野馬操田"},  # 根本沒有 fen
        {"fen": None},
        {"fen": 123},
        {"fen": ["3ak1b2/4a4/4b4/9/9/9/9/4B4/4A4/2BAK1R2 w - - 0 1"]},
        {},
    ],
)
def test_candidate_request_passes_a_missing_or_non_string_fen_through(
    position: dict[str, Any],
) -> None:
    """缺 `fen` 或其值非字串是「欄位不對」,不是「字元危險」(design Implementation Notes)。

    這一層放行,交由 `validate_position()` 以題目 schema 的說法回報 —— 那個說法對
    使用者才有用(「fen 欄位缺少」而不是「格式不合法」)。**放行不擴大攻擊面**:
    非字串不可能是使用者送進引擎的那一行文字,而 schema 未過時服務層根本不借引擎。
    """
    request = CandidatePositionRequest.model_validate({"position": position})
    assert request.position == position


def test_candidate_request_does_not_mutate_the_position() -> None:
    """把關是唯讀的:通過之後的 `position` 與送進來的逐鍵相同。

    驗證器若順手正規化(去空白、補欄位),服務端驗的就不再是使用者要寫進題庫的那
    一份 —— 寫入由瀏覽器執行,兩邊的內容必須是同一個。
    """
    original = _candidate_position()
    request = CandidatePositionRequest.model_validate({"position": dict(original)})
    assert request.position == original
    assert request.position["fen"] == original["fen"]
