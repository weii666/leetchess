"""領域型別的契約測試(對應 tasks 1.2、design 的 Components and Interfaces)。

**本檔刻意很短。** 曾經有 11 個測試在此驗證 dataclass 的欄位名、型別提示與
`frozen=True`,那些是同義反覆:欄位名寫在 `types.py`,測試把同一份清單再抄一次,
改動時兩邊一起改,從來擋不住任何錯誤。真正會踩到的退化由使用這些型別的測試擋下
(`test_game_service.py`、`test_main.py`、`test_positions.py`)—— 欄位一改,那邊
立刻紅。

留下來的兩個都有具體理由,見各自的 docstring。
"""

from __future__ import annotations

from service import types as t


def test_enum_values_are_the_frozen_wire_contract() -> None:
    """三個列舉的字串值會原樣出現在 HTTP 回應裡,前端據以分支。

    改動任何一個都會靜默地讓前端的比對失效 —— 前端拿到沒見過的字串時不會拋錯,
    只會走進「其他」分支,信號燈默默不亮。這是少數值得把字面值釘死的地方。
    """
    assert [s.value for s in t.Side] == ["red", "black"]
    assert {s.value for s in t.Signal} == {"red_winning", "black_winning", "unknown"}
    assert {k.value for k in t.ScoreKind} == {"mate", "cp"}


def test_position_optional_fields_default_to_none() -> None:
    """`max_dtm` 由 corpus-verification 日後回填,現階段必須可省略。

    把它變成必填等於要求驗證工具先跑完才能載入題庫,整個題庫會在那之前無法啟動。
    """
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
