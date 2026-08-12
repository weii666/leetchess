"""領域型別的契約測試(對應 tasks 1.2、design 的 Components and Interfaces)。

**本檔刻意很短。** 曾經有 11 個測試在此驗證 dataclass 的欄位名、型別提示與
`frozen=True`,那些是同義反覆:欄位名寫在 `types.py`,測試把同一份清單再抄一次,
改動時兩邊一起改,從來擋不住任何錯誤。真正會踩到的退化由使用這些型別的測試擋下
(`test_game_service.py`、`test_main.py`、`test_positions.py`)—— 欄位一改,那邊
立刻紅。

留下來的這一個有具體理由,見其 docstring。
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
