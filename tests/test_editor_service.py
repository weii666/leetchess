"""候選題目的權威驗證(對應 tasks 2.1、requirements 4.7)。

檔名取自 design 的 File Structure Plan —— 該表把 `service/editor.py` 與
`positions.validate_position()` 的測試放在同一個檔案裡。本輪只有後者,`EditorService`
的測試(schema 未過時不借引擎、引擎可載入性)於 tasks 2.3 加入同一檔。

`validate_position()` 存在的唯一理由是**規則只有一份**:收題工具要在寫入前判斷一題
合不合格,而那個判準就是題庫載入時用的那一個。它因此是 `_read_position()` 的薄包裝,
不是第二套欄位檢查 —— 題目 schema 的定義權在 `.kiro/steering/structure.md` 與
`service/positions.py`,收題工具**遵循**而不定義。

本檔的骨幹測試是 `test_the_candidate_verdict_matches_what_loading_would_say`:它不分別
斷言兩邊的行為,而是把同一份資料同時餵給 `validate_position()` 與
`PositionRepository.load()`,比對兩者的**結論與訊息**。抄一份規則進包裝裡的實作,會
在第一個沒抄到的細節上讓它變紅;分別斷言的寫法則會兩邊都綠。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import pytest

from service.config import DEFAULT_POSITIONS_DIR
from service.positions import PositionRepository, validate_position
from service.types import Position, Side

#: 《適情雅趣》第二一局的起始局面,僅作為候選題目的填充內容。
SAMPLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 標記「這個欄位在候選題目裡不存在」,以區別於「欄位存在但值為 null」。
OMIT = object()

#: 出錯訊息中指路用的說法。收題工具的候選題目還沒有檔案,指不出「哪一檔第幾題」。
CANDIDATE_LABEL = "候選題目"


def _candidate(**overrides: Any) -> dict[str, Any]:
    """一份合格的候選題目;`overrides` 用來製造各種不合格的變體。

    **沒有 `source` 也沒有 `side_to_move`**:出處由資料夾表達、起手方由 fen 表達,
    兩者寫成欄位就會被當成未知欄位擋下 —— 那正是下面幾個案例要驗的事。
    """
    payload: dict[str, Any] = {
        "id": 21,
        "title": "盡善克終",
        "description": "適情雅趣 第二一局 盡善克終",
        "fen": SAMPLE_FEN,
        "difficulty": 3,
        "tags": ["連將殺"],
    }
    payload.update(overrides)
    for key in [key for key, value in payload.items() if value is OMIT]:
        del payload[key]
    return payload


#: 每一種「候選題目不合格」的代表性情形。第三欄是訊息裡必須出現的字樣 —— 維護者要
#: 修得動,訊息就得指出是哪個欄位出的事。
#:
#: 涵蓋面沿用 `test_positions.py` 的 `BROKEN_POSITIONS`:逐欄位窮舉只是把
#: `REQUIRED_FIELDS` 抄第二遍,每種壞法一個代表即足以釘住「壞題目擋得下來」。
BROKEN_CANDIDATES = [
    ("缺必填欄位", _candidate(fen=OMIT), "fen"),
    ("欄位型別不符", _candidate(id="21"), "id"),
    ("標籤不是字串陣列", _candidate(tags="連將殺"), "tags"),
    # 出處與起手方都曾經是(或差點是)欄位,是最可能被手滑寫回去的兩個。
    ("出處寫成未知欄位", _candidate(source="適情雅趣"), "source"),
    ("起手方寫成未知欄位", _candidate(side_to_move="red"), "side_to_move"),
    ("fen 判不出起手方", _candidate(fen=SAMPLE_FEN.replace(" w ", " g ")), "fen"),
    ("根本不是物件", "盡善克終", "物件"),
]


# --- 合格的候選題目 ------------------------------------------------------


def test_a_complete_candidate_yields_the_shared_domain_type() -> None:
    """合格的候選題目驗完就是一個 `Position`,而且是 `types.py` 的那一個。

    包裝若自行組裝另一份同名型別,`isinstance` 檢查會靜默失效(見
    `test_module_boundaries.py` 對 `positions.py` 的同一條要求)。
    """
    position = validate_position(_candidate())

    assert type(position) is Position
    assert position.id == 21
    assert position.title == "盡善克終"
    assert position.tags == ["連將殺"]


def test_the_side_to_move_still_comes_from_the_fen() -> None:
    """起手方由 FEN 推導這件事,在候選題目上與在題庫檔上是同一條規則。"""
    assert validate_position(_candidate()).side_to_move is Side.RED
    black_first = SAMPLE_FEN.replace(" w ", " b ")
    assert validate_position(_candidate(fen=black_first)).side_to_move is Side.BLACK


@pytest.mark.parametrize(
    ("case", "value"), [("缺欄位", OMIT), ("明寫 null", None)], ids=["缺欄位", "明寫 null"]
)
def test_max_dtm_is_optional_for_a_candidate(case: str, value: Any) -> None:
    """`max_dtm` 由 corpus-verification 回填,收題工具本就不寫它 —— 不得視為必填。"""
    assert validate_position(_candidate(max_dtm=value)).max_dtm is None, case


# --- 不合格的候選題目 ----------------------------------------------------


@pytest.mark.parametrize(
    ("case", "raw", "expected"),
    BROKEN_CANDIDATES,
    ids=[case for case, _, _ in BROKEN_CANDIDATES],
)
def test_a_broken_candidate_is_rejected_with_a_locatable_message(
    case: str, raw: Any, expected: str
) -> None:
    """不合格時沿用既有的錯誤型別(`ValueError`),訊息指得出問題所在。

    錯誤型別不改成 `errors.py` 的七種:那七種是對外 HTTP 契約,而「這一題不合格」
    在本 spec 裡是驗證端點的**結果**(200 加 issues),不是 HTTP 層的錯誤。
    """
    with pytest.raises(ValueError) as info:
        validate_position(raw)

    message = str(info.value)
    assert expected in message, f"{case}:錯誤訊息須指出問題所在"
    assert message.startswith(CANDIDATE_LABEL), (
        f"{case}:候選題目還沒有檔案,訊息須以「{CANDIDATE_LABEL}」指路"
    )


# --- 與題庫載入的判定一致 ------------------------------------------------


def _loading_verdict(root: pathlib.Path, raw: Any) -> str | None:
    """把候選題目當成題庫裡唯一的一題載入,回傳判定;`None` 代表接受。

    回傳值去掉了「哪一檔第幾題」那段前綴,只留下判定本身,才比得上候選題目那條
    路徑的訊息 —— 兩者的差別本來就只該是指路的說法。
    """
    path = root / "適情雅趣" / "21-21.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([raw], ensure_ascii=False), encoding="utf-8")

    try:
        PositionRepository(root).load()
    except ValueError as error:
        return str(error).removeprefix(f"題目檔案 {path} 第 1 題 ")
    return None


def _candidate_verdict(raw: Any) -> str | None:
    """同一份資料走候選題目那條路徑的判定,前綴同樣去掉。"""
    try:
        validate_position(raw)
    except ValueError as error:
        return str(error).removeprefix(f"{CANDIDATE_LABEL} ")
    return None


@pytest.mark.parametrize(
    ("case", "raw"),
    [("合格的候選題目", _candidate())] + [(case, raw) for case, raw, _ in BROKEN_CANDIDATES],
    ids=["合格的候選題目"] + [case for case, _, _ in BROKEN_CANDIDATES],
)
def test_the_candidate_verdict_matches_what_loading_would_say(
    tmp_path: pathlib.Path, case: str, raw: Any
) -> None:
    """同一份資料,兩條路徑的判定與說法必須一模一樣。

    這是本任務的核心斷言:收題工具在寫入前放行的題目,題庫載入時必定也收得下;
    收題工具擋下的,載入時必定也擋。比對到訊息層是刻意的 —— 只比對「有沒有錯」的
    話,一份自己抄了一遍規則、但訊息說法不同的實作照樣會綠。
    """
    assert _candidate_verdict(raw) == _loading_verdict(tmp_path, raw), case


# --- 唯讀 ----------------------------------------------------------------


def _corpus_digest(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_validating_a_candidate_does_not_touch_the_corpus() -> None:
    """`positions.py` 的「唯讀」契約不因這個新入口改變。

    候選題目來自收題工具、還不在題庫裡,驗證只是判定,寫檔是瀏覽器那一端的事
    (服務端不具備任何寫入題庫的能力)。
    """
    before = _corpus_digest(DEFAULT_POSITIONS_DIR)

    validate_position(_candidate())

    assert _corpus_digest(DEFAULT_POSITIONS_DIR) == before
