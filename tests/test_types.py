"""領域型別的契約測試(對應 tasks 1.2、design 的 Components and Interfaces)。

型別定義取自 design.md 的三個 Service Interface 程式碼區塊,欄位名與取值字串
是對外契約的一部分,測試把它們固定下來。
"""

from __future__ import annotations

import dataclasses
import typing

import pytest

from service import types as t


# --- 列舉 ---------------------------------------------------------------


def test_side_values() -> None:
    assert [s.value for s in t.Side] == ["red", "black"]
    assert t.Side.RED == "red"
    assert t.Side.BLACK == "black"


def test_signal_values() -> None:
    assert {s.value for s in t.Signal} == {
        "red_winning",
        "black_winning",
        "unknown",
    }
    assert t.Signal.RED_WINNING == "red_winning"
    assert t.Signal.BLACK_WINNING == "black_winning"
    assert t.Signal.UNKNOWN == "unknown"


def test_score_kind_values() -> None:
    assert {k.value for k in t.ScoreKind} == {"mate", "cp"}
    assert t.ScoreKind.MATE == "mate"
    assert t.ScoreKind.CP == "cp"


# --- dataclass 結構 ------------------------------------------------------


DOMAIN_DATACLASSES = ["Score", "BestMove", "GameState", "BlackReply", "Position"]


@pytest.mark.parametrize("name", DOMAIN_DATACLASSES)
def test_domain_dataclasses_are_frozen(name: str) -> None:
    cls = getattr(t, name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize("name", DOMAIN_DATACLASSES)
def test_all_fields_are_annotated_and_resolvable(name: str) -> None:
    cls = getattr(t, name)
    hints = typing.get_type_hints(cls)
    fields = [f.name for f in dataclasses.fields(cls)]
    assert fields, f"{name} 必須有欄位"
    for field in fields:
        assert field in hints, f"{name}.{field} 缺少可解析的型別提示"


def test_score_fields() -> None:
    hints = typing.get_type_hints(t.Score)
    assert [f.name for f in dataclasses.fields(t.Score)] == ["kind", "value"]
    assert hints["kind"] is t.ScoreKind
    assert hints["value"] is int

    score = t.Score(kind=t.ScoreKind.MATE, value=-3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        score.value = 1  # type: ignore[misc]


def test_best_move_allows_none() -> None:
    hints = typing.get_type_hints(t.BestMove)
    assert [f.name for f in dataclasses.fields(t.BestMove)] == ["move", "score"]
    assert hints["move"] == typing.Optional[str]
    assert hints["score"] == typing.Optional[t.Score]

    empty = t.BestMove(move=None, score=None)
    assert empty.move is None and empty.score is None

    filled = t.BestMove(move="b0c2", score=t.Score(t.ScoreKind.CP, 42))
    assert filled.score is not None
    assert filled.score.kind is t.ScoreKind.CP


def test_game_state_fields() -> None:
    hints = typing.get_type_hints(t.GameState)
    assert [f.name for f in dataclasses.fields(t.GameState)] == [
        "side_to_move",
        "legal_moves",
        "over",
        "winner",
    ]
    assert hints["side_to_move"] is t.Side
    assert hints["legal_moves"] == list[str]
    assert hints["over"] is bool
    assert hints["winner"] == typing.Optional[t.Side]

    state = t.GameState(
        side_to_move=t.Side.BLACK,
        legal_moves=[],
        over=True,
        winner=t.Side.RED,
    )
    assert state.over is True
    assert state.winner is t.Side.RED


def test_black_reply_fields() -> None:
    hints = typing.get_type_hints(t.BlackReply)
    assert [f.name for f in dataclasses.fields(t.BlackReply)] == [
        "move",
        "signal",
        "mate_in",
        "state",
    ]
    assert hints["move"] == typing.Optional[str]
    assert hints["signal"] is t.Signal
    assert hints["mate_in"] == typing.Optional[int]
    assert hints["state"] is t.GameState

    reply = t.BlackReply(
        move="b9c7",
        signal=t.Signal.RED_WINNING,
        mate_in=3,
        state=t.GameState(t.Side.RED, ["a0a1"], False, None),
    )
    assert reply.mate_in == 3
    assert reply.state.over is False


def test_position_fields_match_corpus_schema() -> None:
    hints = typing.get_type_hints(t.Position)
    assert [f.name for f in dataclasses.fields(t.Position)] == [
        "id",
        "title",
        "description",
        "fen",
        "side_to_move",
        "difficulty",
        "tags",
        "max_dtm",
        "solvable",
    ]
    assert hints["id"] is int
    assert hints["title"] is str
    assert hints["description"] is str
    assert hints["fen"] is str
    assert hints["side_to_move"] is t.Side
    assert hints["difficulty"] is int
    assert hints["tags"] == list[str]
    # max_dtm 與 solvable 由驗證工具回填,現階段可為空
    assert hints["max_dtm"] == typing.Optional[int]
    assert hints["solvable"] == typing.Optional[bool]


def test_position_optional_fields_default_to_none() -> None:
    position = t.Position(
        id=21,
        title="適情雅趣第 21 局",
        description="適情雅趣 第 21 局",
        fen="4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1",
        side_to_move=t.Side.RED,
        difficulty=3,
        tags=["殺法"],
    )
    assert position.max_dtm is None
    assert position.solvable is None
