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

from service.config import Settings
from service.errors import ErrorCode, InternalError, InvalidMoveFormatError
from service.game import GameService
from service.models import (
    BlackMoveResponse,
    ErrorResponse,
    GameStateResponse,
    MoveSequenceRequest,
    PositionResponse,
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
        "a0a1",  # 檔與列的下界
        "i9i8",  # 檔與列的上界
        "e0e1",
    ],
)
def test_valid_uci_moves_are_accepted(move: str) -> None:
    """檔 `a`–`i`、列 `0`–`9` 的四字元著法為合法格式(steering tech.md 走法格式)。"""
    assert validate_move(move) == move


@pytest.mark.parametrize(
    "move",
    [
        "j0a1",  # 檔越界:象棋只有 a–i,沒有 j
        "a0j1",
        "h2e",  # 太短
        "h2e22",  # 太長
        "H2E2",  # 大寫
        "h2 e2",  # 含空白
        "h2e2 ",  # 尾隨空白
        " h2e2",
        "",
        "炮二平五",  # 中文記譜:顯示層的表示法,不得進入服務
        "0-0",  # 西洋棋的王車易位記法
        "h2e2q",  # 西洋棋的升變後綴
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


def test_rejection_carries_the_machine_readable_error_class() -> None:
    """錯誤類別與 HTTP 狀態取自 errors.py 的單一來源(5.1、design 錯誤類別表)。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        validate_move("nope")
    assert excinfo.value.code is ErrorCode.INVALID_MOVE_FORMAT
    assert excinfo.value.http_status == 400


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


def test_move_sequence_request_defaults_to_an_empty_sequence() -> None:
    """未走任何一步的起始局面查詢仍是合法請求。"""
    request = MoveSequenceRequest.model_validate({"position_id": 7})
    assert request.moves == []


def test_move_sequence_request_rejects_unknown_fields() -> None:
    """拼錯的欄位必須失敗而非被忽略。

    `moves` 拼成 `move` 若被靜默忽略,請求會被當成「起始局面」處理,使用者拿到的
    是另一個局面的合法著法而非錯誤 —— 與引擎靜默忽略非法著法同一類的失敗模式。
    """
    with pytest.raises(ValidationError):
        MoveSequenceRequest.model_validate(
            {"position_id": 1, "move": ["h2e2"], "moves": []}
        )


def test_move_sequence_request_rejects_an_invalid_move() -> None:
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        MoveSequenceRequest.model_validate({"position_id": 1, "moves": ["j0a1"]})
    assert "j0a1" in excinfo.value.message


def test_move_sequence_request_names_the_offending_move_not_the_first_one() -> None:
    """序列中第 3 手不合法時,訊息要指出第 3 手,不是第 1 手。"""
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        MoveSequenceRequest.model_validate(
            {"position_id": 1, "moves": ["h2e2", "e9f9", "zzzz"]}
        )
    assert "zzzz" in excinfo.value.message
    assert "h2e2" not in excinfo.value.message


@pytest.mark.parametrize("value", [123, None, True, ["h2e2"], {"from": "h2"}])
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


def test_invalid_move_never_reaches_the_engine_pool(routed) -> None:
    handle, pool = routed
    with pytest.raises(InvalidMoveFormatError) as excinfo:
        handle({"position_id": 1, "moves": ["h2e2", "j0a1"]})
    assert "j0a1" in excinfo.value.message
    assert pool.acquisitions == 0, "格式驗證必須在借用引擎之前完成"


def test_valid_move_does_reach_the_engine_pool(routed) -> None:
    """對照組:證明上一個測試不是因為根本沒接上服務層才通過的。"""
    handle, pool = routed
    response = handle({"position_id": 1, "moves": ["h2e2"]})
    assert pool.acquisitions == 1
    assert response.legal_moves == ["b0c2", "g3g4"]


# --- 對局狀態回應 -----------------------------------------------------------


def test_game_state_response_maps_the_domain_state() -> None:
    state = GameState(
        side_to_move=Side.BLACK,
        legal_moves=["e9f9", "e9d9"],
        over=False,
        winner=None,
    )
    assert GameStateResponse.from_domain(state).model_dump(mode="json") == {
        "side_to_move": "black",
        "legal_moves": ["e9f9", "e9d9"],
        "over": False,
        "winner": None,
    }


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


def test_black_move_response_keeps_a_zero_mate_in() -> None:
    """**每一題排局的最後一手**都會走到這裡,不是邊緣案例。

    實測真實引擎在黑方被將死時輸出 `score mate 0` + `bestmove (none)`。模型若以
    `if mate_in:`、`mate_in or None` 或 `exclude_none` 之類的 falsy 判斷處理,倒數
    就會在終局那手靜默消失。此處對整份序列化結果做完整相等斷言,任何吞掉都會失敗。
    """
    reply = BlackReply(
        move=None, signal=Signal.RED_WINNING, mate_in=0, state=_terminal_state()
    )
    assert BlackMoveResponse.from_domain(reply).model_dump(mode="json") == {
        "move": None,
        "signal": "red_winning",
        "mate_in": 0,
        "state": {
            "side_to_move": "black",
            "legal_moves": [],
            "over": True,
            "winner": "red",
        },
    }


def test_black_move_response_serializes_a_zero_mate_in_to_json() -> None:
    """欄位不得在 JSON 序列化這一層被省略 —— 前端讀到的是 JSON,不是 dict。"""
    reply = BlackReply(
        move=None, signal=Signal.RED_WINNING, mate_in=0, state=_terminal_state()
    )
    payload = BlackMoveResponse.from_domain(reply).model_dump_json()
    assert '"mate_in":0' in payload.replace(" ", "")


def test_black_move_response_allows_no_move_with_an_unknown_signal() -> None:
    """`move=None` 也可能搭配 `signal=unknown`(引擎一分未報),契約須容得下。"""
    reply = BlackReply(
        move=None, signal=Signal.UNKNOWN, mate_in=None, state=_terminal_state()
    )
    assert BlackMoveResponse.from_domain(reply).model_dump(mode="json") == {
        "move": None,
        "signal": "unknown",
        "mate_in": None,
        "state": {
            "side_to_move": "black",
            "legal_moves": [],
            "over": True,
            "winner": "red",
        },
    }


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


def test_mate_in_is_documented_as_an_approximation() -> None:
    """殺著倒數在 250k 節點下**可能高估 1 步**,是約數不是確數。

    這是刻意的取捨(精確 DTM 需 4.5 倍算力),故必須寫在對外契約的欄位說明裡,
    否則 UI 會把它當確數呈現。
    """
    description = BlackMoveResponse.model_fields["mate_in"].description
    assert description is not None
    assert "約" in description


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
        "solvable": True,
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
        "solvable": True,
        "source": "適情雅趣",
        "state": {
            "side_to_move": "red",
            "legal_moves": ["g3g4"],
            "over": False,
            "winner": None,
        },
    }


def test_position_response_allows_unverified_puzzles() -> None:
    """`max_dtm` 與 `solvable` 由 corpus-verification 日後回填,現階段可為空。"""
    state = GameState(
        side_to_move=Side.RED, legal_moves=["g3g4"], over=False, winner=None
    )
    dumped = PositionResponse.from_domain(
        _position(max_dtm=None, solvable=None), state
    ).model_dump(mode="json")
    assert dumped["max_dtm"] is None
    assert dumped["solvable"] is None
    assert dumped["source"] is None


def test_position_response_keeps_optional_fields_present() -> None:
    """欄位為空時仍須存在,前端才不必分辨「沒有這個欄位」與「值為空」。"""
    state = GameState(
        side_to_move=Side.RED, legal_moves=["g3g4"], over=False, winner=None
    )
    dumped = PositionResponse.from_domain(
        _position(max_dtm=None, solvable=None), state
    ).model_dump(mode="json")
    assert "max_dtm" in dumped
    assert "solvable" in dumped


# --- 錯誤回應 ---------------------------------------------------------------


def test_error_response_carries_a_stable_machine_readable_code() -> None:
    """5.1:client 依代碼決定 UI 行為,故代碼是契約的一部分。"""
    dumped = ErrorResponse.from_error(InvalidMoveFormatError("著法格式不合法:j0a1"))
    assert dumped.model_dump(mode="json") == {
        "code": "INVALID_MOVE_FORMAT",
        "message": "著法格式不合法:j0a1",
    }


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


def test_error_response_uses_the_repository_error_code() -> None:
    from service.errors import PositionNotFoundError

    dumped = ErrorResponse.from_error(PositionNotFoundError()).model_dump(mode="json")
    assert dumped["code"] == "POSITION_NOT_FOUND"


# --- 依賴方向 ---------------------------------------------------------------


def test_models_does_not_redefine_domain_types() -> None:
    """領域型別只有一份,模型只做邊界轉換。

    重新定義 `Side` 或 `Signal` 會讓 `is` 比較與 `isinstance` 靜默失效,兩份列舉
    的成員永遠不相等。
    """
    from service import models

    assert models.Side is Side
    assert models.Signal is Signal


def test_repository_position_flows_into_the_response(tmp_path: pathlib.Path) -> None:
    """實際用題庫讀出的 `Position` 餵進回應模型,確認兩端欄位對得上。"""
    import json

    folder = tmp_path / "適情雅趣"
    folder.mkdir(parents=True)
    (folder / "0021.json").write_text(
        json.dumps(
            {
                "id": 21,
                "title": "野馬操田",
                "description": "《適情雅趣》第 21 局",
                "fen": RED_FEN,
                "side_to_move": "red",
                "difficulty": 3,
                "tags": ["連將殺"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = PositionRepository(tmp_path)
    repository.load()

    state = GameState(
        side_to_move=Side.RED, legal_moves=["g3g4"], over=False, winner=None
    )
    response = PositionResponse.from_domain(
        repository.get(21), state, source=repository.source_of(21)
    )
    assert response.model_dump(mode="json")["source"] == "適情雅趣"
    assert response.model_dump(mode="json")["fen"] == RED_FEN
