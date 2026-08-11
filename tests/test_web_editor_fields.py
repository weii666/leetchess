"""收題頁的欄位互動、難度選項與檢查結果呈現(tasks 4.3;requirements 3.2、3.3、
3.6、3.7、4.1、4.2、4.6、8.3、8.4)。

本檔問的是**行為**:七個欄位裡的字變了之後,難度有哪些選項、描述會不會被填上一個
建議值、哪一項未通過被指到哪一欄、寫入操作停不停用。

版面與 DOM 容器屬 tasks 4.1(`tests/test_web_editor_layout.py`)、繪盤與 FEN 訊息屬
4.2(`tests/test_web_editor_board.py`)、純函式屬 3.1(`tests/test_web_editor_pure.py`),
三者都不在此重測。

## 頁面走 http 而不是 `file://`

理由與 `tests/test_web_editor_board.py` 相同:`editor.js` 是 ES module,Chromium 不
允許自 `file://`(origin 為 `null`)匯入 module。故沿用同一套 `page.route()` 就地供
`web/` 底下的**真實交付檔**。

## 本檔特別在意的兩件事

1. **難度的三個說法一個字都不在收題頁的檔案裡。** 唯一出處是 `web/difficulty.js`,
   列表頁與對局頁讀的是同一份。因此期望值也不寫死在本檔:它自那個模組讀出來,
   模組改了本檔跟著改,而收題頁若自己抄了一份就會被抓到。
2. **描述建議值是建議,不是規定。** 3.7 要的是「最終值以維護者輸入的內容為準」,
   而建議值每一次輸入變動都會重算 —— 重算若蓋掉使用者打的字,那一句需求就等於
   沒有實作。本檔因此把「打完自己的描述後再去改別欄」當成主要情境反覆驗。
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_DIR = WEB_DIR / "editor"
EDITOR_HTML = EDITOR_DIR / "index.html"
EDITOR_JS = EDITOR_DIR / "editor.js"
EDITOR_CSS = EDITOR_DIR / "editor.css"
DIFFICULTY_JS = WEB_DIR / "difficulty.js"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-fields.test"

#: 桌面尺寸。窄畫面的折行屬版面(4.1)。
DESKTOP = (1280, 800)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

#: 表單欄位的順序,即 `check.js` 的 `checkForm()` 回傳清單的順序。
FORM_FIELDS = ["id", "title", "description", "difficulty", "tags", "fen", "target"]

#: 各欄在畫面上的名稱。寫入操作旁的點名照著這些字念(8.4),維護者要靠它們回頭找到
#: 是哪一格。**期望值寫在這裡,不回頭讀受測頁面的 `<label>`** —— 拿受測物自己的字
#: 當期望值,兩邊一起改壞也不會轉紅。
FIELD_LABELS = {
    "id": "題號",
    "title": "局名",
    "description": "描述",
    "difficulty": "難度",
    "tags": "標籤",
    "fen": "FEN",
    "target": "目標檔案路徑",
}

#: 寫入操作(4.1「不執行寫入」要有東西可停用)與它旁邊那句說明(8.4)。
#: **本任務只做到「停用」** —— 真正的寫入屬第 5 組。
WRITE_BUTTON = "#write"
WRITE_NOTE = "#write-note"

#: 《適情雅趣》第 21 局的起始局面,紅先(與 `tests/test_web_editor_board.py` 同一串)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 一份填得完整、七項淺層檢查全過的表單。
#:
#: 目標路徑帶卷次(`適情雅趣~卷一`),描述建議值卻只該有書名 —— 兩者的差別正是
#: `structure.md` 的 Naming Conventions 那一條(書名與卷次是兩件事)。
VALID_FORM = {
    "id": "26",
    "title": "患在几席",
    "description": "自己打的描述,不該被任何建議值蓋掉",
    "difficulty": "2",
    "tags": "解殺還殺 鐵門栓、悶宮",
    "fen": PUZZLE_FEN,
    "target": "適情雅趣~卷一/26.json",
}

#: 題號 26、局名「患在几席」、書名「適情雅趣」時的描述建議值(3.6)。
#: 寫法沿用既有題目:局號逐字中文數字,且**不含卷次**。
EXPECTED_SUGGESTION = "適情雅趣 第二六局 患在几席"


# --- 夾具與操作 ---------------------------------------------------------


@pytest.fixture
def editor_page(browser_page) -> Iterator:
    """以 http 來源開啟**真實的**收題頁(理由見模組說明)。"""

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/editor", "/editor/"):
            path = "/editor/index.html"
        target = WEB_DIR / path.lstrip("/")
        if target.is_file():
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES.get(target.suffix, "text/plain"),
                body=target.read_text(encoding="utf-8"),
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.set_viewport_size({"width": DESKTOP[0], "height": DESKTOP[1]})
    browser_page.route(f"{ORIGIN}/**", serve)
    browser_page.goto(f"{ORIGIN}/editor/")
    # 難度選項由模組在載入時產生,等它出現再繼續,否則第一個斷言可能量在模組還沒
    # 執行完的瞬間。等的是**數量**而不是某個選項可見:`<option>` 在收合的 `<select>`
    # 裡沒有版面,可見性判準對它不成立。
    browser_page.wait_for_function(
        "() => document.querySelectorAll('[data-field=\"difficulty\"] option').length > 1",
        timeout=5000,
    )
    yield browser_page


def control(name: str) -> str:
    """某一欄的控制項選擇器。一律以 `data-field` 認欄位(4.1 的 DOM 契約)。"""
    return f'[data-field="{name}"]'


def slot(name: str) -> str:
    """某一欄的訊息槽選擇器(4.2 的 DOM 契約,4.3 為其餘六欄補齊)。"""
    return f'[data-message-for="{name}"]'


def put(page, name: str, value: str) -> None:
    """把 `value` 填進某一欄。

    難度是 `<select>`,只能選不能打字(3.2)—— 這個分岔本身就是那一項需求的形狀。
    `fill()` 與 `select_option()` 都會派送 `input` 事件而不失焦,測到的因此是
    「每一次變動」而不是「離開欄位之後」。
    """
    if name == "difficulty":
        page.select_option(control(name), value)
        return
    page.fill(control(name), value)


def fill_valid_form(page, **overrides: str) -> None:
    """填出一份七項全過的表單,`overrides` 逐欄覆寫(空字串即留白)。

    描述**刻意最後填**:前面填局名時會冒出建議值,最後這一次覆寫讓它成為維護者
    自己的內容(3.7),後續各測試改別欄時它就不該再變。
    """
    values = dict(VALID_FORM)
    values.update(overrides)
    for name in ["id", "title", "difficulty", "tags", "fen", "target", "description"]:
        put(page, name, values[name])


def value_of(page, name: str) -> str:
    return page.input_value(control(name))


def label_of(name: str) -> str:
    """某一欄在畫面上的名稱(見 `FIELD_LABELS`)。"""
    return FIELD_LABELS[name]


def message(page, name: str) -> str:
    """某一欄當下說的話;沒有話說(`hidden`)時為空字串,槽位不存在時為 `None`。"""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (element === null) return null;
          return element.hidden ? '' : element.textContent.trim();
        }""",
        slot(name),
    )


def note(page) -> str:
    """寫入操作旁那句說明;沒有話說時為空字串。"""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (element === null) return null;
          return element.hidden ? '' : element.textContent.trim();
        }""",
        WRITE_NOTE,
    )


def write_is_enabled(page) -> bool:
    return page.locator(WRITE_BUTTON).is_enabled()


def module_difficulty_labels(page) -> list[str]:
    """自 `web/difficulty.js` 讀出三個說法 —— **期望值不寫死在測試裡**。

    收題頁自己抄一份說法時,這個比對仍會通過;抓它的是
    `test_the_difficulty_labels_are_written_in_no_editor_file`。兩條各管一件事。
    """
    return page.evaluate(
        """async () => {
          const module = await import('../difficulty.js');
          return [...module.DIFFICULTY_LABELS.values()];
        }"""
    )


def source_difficulty_labels() -> list[str]:
    """直接自 `web/difficulty.js` 的檔案文字取出三個說法(不需要瀏覽器)。"""
    text = DIFFICULTY_JS.read_text(encoding="utf-8")
    return re.findall(r"\[\s*\d+\s*,\s*'([^']+)'\s*\]", text)


# --- 難度三選一(3.2、8.3)---------------------------------------------


def test_the_difficulty_options_come_from_the_difficulty_module(editor_page) -> None:
    """3.2、8.3:三個選項的說法與順序就是 `difficulty.js` 的那一份。

    列表頁與對局頁讀的是同一個模組,說法因此必然一致 —— 這正是 4.3 要求「選項自
    既有的難度說法模組產生」的理由。期望值自模組讀出而不寫死在測試裡,否則本檔
    自己就成了第三份說法。
    """
    labels = module_difficulty_labels(editor_page)
    assert len(labels) == 3, f"difficulty.js 的分級數量不是三個:{labels}"

    options = editor_page.evaluate(
        """() => [...document.querySelectorAll('[data-field="difficulty"] option')].map(
          option => ({ value: option.value, text: option.textContent.trim() })
        )"""
    )

    assert [option["value"] for option in options] == ["", "1", "2", "3"], (
        f"難度選項的取值不是「尚未選擇 + 三個分級」:{options}"
    )
    assert [option["text"] for option in options[1:]] == labels, (
        f"難度選項的說法與 difficulty.js 不一致:{options}"
    )


def test_the_difficulty_starts_unchosen(editor_page) -> None:
    """3.2:預設是「尚未選擇」,而且它必須留在第一個。

    `check.js` 的 `checkForm()` 以空字串判定「還沒選」;產生選項時若插到最前面,
    維護者不做選擇也會寫進一個難度。

    還沒選是**還沒填**而不是填錯,因此難度那一格旁邊不掛訊息 —— 這一項未通過由
    寫入操作旁的點名表達(見 `test_an_untouched_page_says_nothing_beside_any_field`)。
    """
    assert value_of(editor_page, "difficulty") == ""
    assert message(editor_page, "difficulty") == "", "還沒選難度不是填錯,不該掛訊息"
    assert "難度" in note(editor_page), f"點名漏了還沒選的難度:{note(editor_page)!r}"


def test_the_difficulty_field_accepts_no_value_outside_the_three_levels(
    editor_page,
) -> None:
    """3.2:不接受自由輸入的數值 —— 三個分級以外的值連選都選不到。

    兩道防線擋掉「因為別的原因而通過」:先確認那個控制項真的找得到(選擇器打錯的話
    這一行就紅,而不是讓它去撐滿逾時再算成功),再接 Playwright 自己的錯誤型別而不是
    `Exception`。playwright 在函式內才匯入,理由與 `tests/conftest_web.py` 相同 ——
    不讓不需要瀏覽器的測試付這個代價。
    """
    from playwright.sync_api import Error as PlaywrightError

    assert editor_page.locator(control("difficulty")).count() == 1, "難度控制項不存在"

    with pytest.raises(PlaywrightError):
        editor_page.select_option(control("difficulty"), "4", timeout=1000)

    assert value_of(editor_page, "difficulty") == ""


def test_the_difficulty_labels_are_written_in_no_editor_file() -> None:
    """8.3:三個說法的唯一出處是 `web/difficulty.js`。

    掃的是收題頁自己的三個檔案的**文字**:寫死一份就是第二個真相來源,改了模組
    而忘了這一頁時,兩邊會靜默分家而沒有任何測試轉紅。
    """
    labels = source_difficulty_labels()
    assert len(labels) == 3, f"沒能自 difficulty.js 取出三個說法:{labels}"

    for path in (EDITOR_HTML, EDITOR_JS, EDITOR_CSS):
        text = path.read_text(encoding="utf-8")
        found = [label for label in labels if label in text]
        assert not found, f"{path.name} 裡寫死了難度說法 {found}"


# --- 多個標籤(3.3)-----------------------------------------------------


def test_several_tags_can_be_entered_in_one_go(editor_page) -> None:
    """3.3:一題填得下多個標籤,分隔符號收得寬。

    切分的規則在 `check.js` 的 `parseTags`(3.1 已單獨驗過),此處問的是**這一頁
    收不收得下**:三個標籤混用空白與頓號打進同一欄,值原樣留著、標籤那一項不再
    被指為未通過。
    """
    put(editor_page, "tags", VALID_FORM["tags"])

    assert value_of(editor_page, "tags") == VALID_FORM["tags"]
    assert message(editor_page, "tags") == "", "填了三個標籤卻仍被指為未通過"

    parsed = editor_page.evaluate(
        """async raw => {
          const module = await import('./check.js');
          return module.parseTags(raw);
        }""",
        VALID_FORM["tags"],
    )
    assert parsed == ["解殺還殺", "鐵門栓", "悶宮"], f"標籤沒有被切成三個:{parsed}"


# --- 描述建議值(3.6、3.7)---------------------------------------------


def test_a_description_is_suggested_once_the_id_and_title_are_filled(
    editor_page,
) -> None:
    """3.6:題號與局名皆已填而描述仍為空時,給出一個描述建議值。

    書名取自目標路徑的第一段(切掉 `~` 之後的卷次),寫法沿用既有題目 ——
    局號是**逐字**中文數字。
    """
    put(editor_page, "target", VALID_FORM["target"])
    put(editor_page, "id", VALID_FORM["id"])
    put(editor_page, "title", VALID_FORM["title"])

    assert value_of(editor_page, "description") == EXPECTED_SUGGESTION


def test_the_suggested_description_leaves_the_volume_out(editor_page) -> None:
    """3.6:資料夾帶卷次,描述只寫書名(`structure.md` 的 Naming Conventions)。

    這一條與上一條分開寫:上一條整串比對,壞掉時看不出是哪一部分錯了;而「卷次
    跟著跑進描述」是這裡最可能出的一種錯,值得單獨說一句。
    """
    put(editor_page, "target", VALID_FORM["target"])
    put(editor_page, "id", VALID_FORM["id"])
    put(editor_page, "title", VALID_FORM["title"])

    suggestion = value_of(editor_page, "description")
    assert "~" not in suggestion and "卷" not in suggestion, (
        f"建議值帶出了卷次:{suggestion}"
    )
    assert message(editor_page, "description") == "", "描述已有建議值,不該再說它沒填"


def test_the_suggestion_appears_even_when_the_target_is_filled_last(
    editor_page,
) -> None:
    """3.6:三欄的填寫順序不影響建議值 —— 它是當下那三個值的函式。

    書名來自目標路徑,因此「先填題號局名、後填路徑」是很自然的順序,而只在填局名
    那一刻算一次的實作會在這個順序下永遠給不出建議值。
    """
    put(editor_page, "id", VALID_FORM["id"])
    put(editor_page, "title", VALID_FORM["title"])
    assert value_of(editor_page, "description") == "", "還不知道書名就先湊了一個建議值"

    put(editor_page, "target", VALID_FORM["target"])

    assert value_of(editor_page, "description") == EXPECTED_SUGGESTION


def test_a_typed_description_is_never_replaced_by_a_suggestion(editor_page) -> None:
    """3.7:描述的最終值以維護者輸入的內容為準。

    先打自己的描述,再回頭填題號與局名 —— 建議值每一次輸入變動都會重算,重算若
    蓋掉這一句,3.7 就等於沒有實作。
    """
    put(editor_page, "description", "自己打的描述")

    put(editor_page, "target", VALID_FORM["target"])
    put(editor_page, "id", VALID_FORM["id"])
    put(editor_page, "title", VALID_FORM["title"])

    assert value_of(editor_page, "description") == "自己打的描述"


def test_the_suggestion_can_be_overwritten_and_the_overwrite_survives(
    editor_page,
) -> None:
    """3.6 + 3.7:建議值出現後可被覆寫,覆寫值不因後續的每一次鍵入而消失。

    這是本任務最細的一處:改寫之後再去動別的欄位,每一次都會重算建議值,而此刻
    描述欄裡的字已經不是工具給的那一句了。
    """
    put(editor_page, "target", VALID_FORM["target"])
    put(editor_page, "id", VALID_FORM["id"])
    put(editor_page, "title", VALID_FORM["title"])
    assert value_of(editor_page, "description") == EXPECTED_SUGGESTION

    put(editor_page, "description", "改寫過的描述")
    put(editor_page, "title", "另一個局名")
    put(editor_page, "id", "27")
    put(editor_page, "tags", "悶宮")

    assert value_of(editor_page, "description") == "改寫過的描述"


def test_no_suggestion_before_both_the_id_and_the_title_are_filled(editor_page) -> None:
    """3.6:條件是**兩者皆已填**,只填一個時不猜一個描述出來。"""
    put(editor_page, "target", VALID_FORM["target"])

    put(editor_page, "id", VALID_FORM["id"])
    assert value_of(editor_page, "description") == "", "只填了題號就湊出建議值"

    put(editor_page, "id", "")
    put(editor_page, "title", VALID_FORM["title"])
    assert value_of(editor_page, "description") == "", "只填了局名就湊出建議值"


def test_no_suggestion_while_the_id_is_not_a_positive_integer(editor_page) -> None:
    """3.6 的前提:題號不合格式時湊不出局號,不得給出一個帶著壞題號的建議值。

    判準沿用 `check.js` —— 組裝層不自己再寫一份「什麼是正整數」。
    """
    put(editor_page, "target", VALID_FORM["target"])
    put(editor_page, "title", VALID_FORM["title"])
    put(editor_page, "id", "二六")

    assert value_of(editor_page, "description") == ""
    assert message(editor_page, "id"), "題號不合格式,畫面卻沒有指出這一項"


# --- 未通過項目的呈現與寫入停用(4.1、4.2、4.6、8.4)--------------------


def test_a_complete_form_says_nothing_and_enables_writing(editor_page) -> None:
    """七項全過時每一個訊息槽都收起來,寫入操作可用。

    這一條是下面每一條的對照組:少了它,一個「永遠說有問題」的實作也會全綠。
    """
    fill_valid_form(editor_page)

    for name in FORM_FIELDS:
        assert message(editor_page, name) == "", f"{name} 明明填妥了卻仍被指為未通過"
    assert note(editor_page) == "", "七項全過卻仍掛著一句停用說明"
    assert write_is_enabled(editor_page), "七項全過,寫入操作卻仍停用"


@pytest.mark.parametrize("missing", FORM_FIELDS)
def test_a_missing_required_field_is_named_in_the_note_and_stops_writing(
    editor_page, missing: str
) -> None:
    """4.1、8.4:必填未填時停用寫入,並指出是哪一項 —— 指在**寫入操作旁**。

    空著的欄位自己不出聲(見 `test_an_untouched_page_says_nothing_beside_any_field`
    的說明),這一條因此同時釘住兩件事:那一格安靜,而那一項未通過並沒有消失。
    兩者少了任何一件,4.1 或 8.4 就有一個不成立。

    逐欄參數化涵蓋全部七欄(含難度與 FEN):只把點名接到其中一兩欄的實作,單一案例
    照樣會綠。難度的「空」是選回第一個選項,`put()` 已經吸收掉這個差別。
    """
    fill_valid_form(editor_page, **{missing: ""})

    assert message(editor_page, missing) == "", (
        f"{missing} 只是還沒填,那一格不該掛訊息"
    )
    assert not write_is_enabled(editor_page), f"{missing} 未填,寫入操作卻仍可用"
    assert label_of(missing) in note(editor_page), (
        f"點名漏了尚未填妥的 {missing}:{note(editor_page)!r}"
    )


def test_an_untouched_page_says_nothing_beside_any_field(editor_page) -> None:
    """4.1、8.4:剛開頁時七個欄位一句話都不說,但七項全數列在寫入操作旁。

    這是本輪定案的規則:**還沒填不是填錯**。一開頁就在每一格旁邊掛一句紅字,等於在
    維護者還沒開始之前先說了七次錯,而之後真正的錯字反而混在裡面看不出來。表單還沒
    填完是一件事,整份講一次就夠。

    「沒有變成看不見」由後半段保證:寫入停用,而且每一項都點得出名字。
    """
    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == "", f"{name} 一開頁就有值,前提不成立"
        assert message(editor_page, name) == "", f"{name} 還沒填就掛著訊息"

    text = note(editor_page)
    for name in FORM_FIELDS:
        assert label_of(name) in text, f"點名漏了 {name}:{text!r}"
    assert not write_is_enabled(editor_page), "七欄全空,寫入操作卻可用"


#: 填了、但填錯的七種輸入。**這些必須留在欄位旁** —— 維護者已經寫了東西而那個東西
#: 不對,訊息離那一格越遠越難對照(8.4)。描述與難度不在此列:兩者的淺層檢查只有
#: 「有沒有填」,不存在「填錯」的形態。
WRONGLY_FILLED = [
    pytest.param("id", "26.5", id="id-not-an-integer"),
    pytest.param("id", "0", id="id-not-positive"),
    pytest.param("tags", "、,  ", id="tags-only-separators"),
    pytest.param("fen", "9/9/9 w - - 0 1", id="fen-three-rows"),
    pytest.param("target", "26.json", id="target-outside-a-book-folder"),
    pytest.param("target", "/適情雅趣~卷一/26.json", id="target-absolute"),
]


@pytest.mark.parametrize(("name", "value"), WRONGLY_FILLED)
def test_a_wrongly_filled_field_is_pointed_at_its_own_field(
    editor_page, name: str, value: str
) -> None:
    """4.2、4.6、8.4:填了而填錯的欄位,訊息就掛在那一格旁邊。

    這是上一組的另一半:空著時安靜、填錯時出聲。兩者若共用同一條路,不是七欄一起
    吵、就是七欄一起啞 —— 而後者會讓「題號必須是正整數」這種真正需要當場看到的
    訊息只剩下寫入鈕旁的一個名字,維護者得自己回頭猜那一格是哪裡不對。
    """
    fill_valid_form(editor_page, **{name: value})

    assert message(editor_page, name), f"{name} 填錯了,那一格卻沒有說是哪裡不對"
    assert not write_is_enabled(editor_page)
    assert label_of(name) in note(editor_page)


def test_a_field_that_is_emptied_again_goes_quiet(editor_page) -> None:
    """填錯之後又清空,那一格要跟著安靜下來 —— 但那一項未通過仍在點名裡。

    這一條問的是規則的**判準是當下的值**,而不是「這一欄曾經出過錯」:記著歷史的
    實作會讓一個已經清空的欄位一直掛著上一次的紅字。
    """
    fill_valid_form(editor_page, id="26.5")
    assert message(editor_page, "id"), "前提不成立:填錯時那一格本來就該出聲"

    put(editor_page, "id", "")

    assert message(editor_page, "id") == "", "清空之後那一格還掛著上一次的訊息"
    assert label_of("id") in note(editor_page), "清空之後那一項未通過從畫面上消失了"
    assert not write_is_enabled(editor_page)


def test_an_id_that_is_not_a_positive_integer_is_pointed_at_its_own_field(
    editor_page,
) -> None:
    """4.2:題號不是正整數時停用寫入,並指出題號不合格式。

    訊息要落在**題號那一欄**,不是丟進一個共用的錯誤區 —— 8.4 要的是定位到欄位。
    """
    fill_valid_form(editor_page, id="26.5")

    assert message(editor_page, "id"), "題號不合格式,畫面卻沒有指出這一項"
    assert message(editor_page, "title") == "", "把題號的問題也標到了局名那一欄"
    assert not write_is_enabled(editor_page)


def test_no_tags_at_all_is_pointed_at_the_tags_field(editor_page) -> None:
    """4.6:標籤一個也沒填時停用寫入,並指出標籤至少需要一個。

    只打分隔符號同樣是一個都沒有 —— 這是 `parseTags` 的行為,呈現層照著它走。
    **這一欄不是空的**,所以訊息掛在欄位旁:維護者打的那幾個符號看起來像有填。
    """
    fill_valid_form(editor_page, tags="、,  ")

    assert message(editor_page, "tags"), "標籤一個也沒有,畫面卻沒有指出這一項"
    assert not write_is_enabled(editor_page)


def test_an_empty_fen_stops_writing_and_is_named_in_the_note(editor_page) -> None:
    """4.1 + 2.5 的交界:FEN 欄位空著時那一欄不掛訊息,但仍要指出這一項。

    FEN 對這條規則特別敏感,所以單獨留一條:requirement 2.5 明載清空 FEN 呈現的是
    **空盤面而不是錯誤**(`tests/test_web_editor_board.py` 反向釘住那一欄必須是空的)。
    其餘六欄如今照同一條規則走,但只有這一欄的安靜是被另一則需求直接要求的 ——
    哪天有人把「空欄位也要出聲」改回來,這一條會與 2.5 一起紅。
    """
    fill_valid_form(editor_page, fen="")

    assert message(editor_page, "fen") == "", "空的 FEN 欄位掛著錯誤訊息,與 2.5 相牴觸"
    assert not write_is_enabled(editor_page), "FEN 沒填,寫入操作卻仍可用"
    assert "FEN" in note(editor_page), f"停用說明沒有點名 FEN:{note(editor_page)!r}"


def test_a_broken_fen_is_pointed_at_its_own_field(editor_page) -> None:
    """2.4 + 8.4:貼了一串展不開的 FEN 時,訊息就在那一欄旁邊。

    與上一條成對:同一個欄位、兩種輸入、兩種呈現。少了這一條,一個「FEN 那一格
    永遠不出聲」的實作會全綠,而 FEN 抄錯正是這個工具存在的理由。
    """
    fill_valid_form(editor_page, fen="9/9/9 w - - 0 1")

    assert message(editor_page, "fen"), "FEN 展不開,那一格卻沒有說是哪裡不對"
    assert not write_is_enabled(editor_page)


def test_the_note_names_every_outstanding_item(editor_page) -> None:
    """8.4:同時有多項未通過時,說明要把它們都點出來,而不是只講第一項。"""
    fill_valid_form(editor_page, id="", tags="")

    text = note(editor_page)
    assert "題號" in text and "標籤" in text, f"停用說明漏了未通過的項目:{text!r}"
    assert "局名" not in text, f"停用說明點名了已經填妥的項目:{text!r}"


def test_a_fixed_message_disappears_again(editor_page) -> None:
    """未通過不是會黏住的狀態:改好了,訊息與點名都要跟著收起來、寫入要回來。"""
    fill_valid_form(editor_page, id="26.5")
    assert message(editor_page, "id")

    put(editor_page, "id", VALID_FORM["id"])

    assert message(editor_page, "id") == "", "改好之後訊息還掛在畫面上"
    assert note(editor_page) == "", "改好之後點名還掛在畫面上"
    assert write_is_enabled(editor_page)


@pytest.mark.parametrize("name", FORM_FIELDS)
def test_every_message_sits_in_its_own_field(editor_page, name: str) -> None:
    """8.4:訊息要**定位到欄位** —— 槽位與控制項在同一個 `.field` 裡,且排在其後。

    共用一塊錯誤區的實作在文字上也可能說出「題號未填」,但使用者得自己在七個欄位
    間對照;訊息就在那一格底下,眼睛不必移開。
    """
    placement = editor_page.evaluate(
        """selectors => {
          const control = document.querySelector(selectors.control);
          const message = document.querySelector(selectors.message);
          if (control === null || message === null) return null;
          const field = control.closest('.field');
          return {
            sameField: field !== null && field.contains(message),
            afterControl: Boolean(
              control.compareDocumentPosition(message) & Node.DOCUMENT_POSITION_FOLLOWING
            ),
          };
        }""",
        {"control": control(name), "message": slot(name)},
    )

    assert placement is not None, f"{name} 沒有訊息槽"
    assert placement["sameField"], f"{name} 的訊息不在該欄位裡"
    assert placement["afterControl"], f"{name} 的訊息排在控制項之前"


def test_every_message_slot_is_declared_in_the_markup() -> None:
    """七個槽位都寫在 HTML 裡、預設隱藏,不由 JS 生出來。

    這是 4.2 立下的規矩(`#unsupported` 更早):要出現的東西先在版面裡佔好位置,
    JS 只決定顯示與否 —— 否則訊息每次出現與消失都會推動底下的欄位,使用者每打一個
    字版面就抖一次。

    斷言看的是**檔案文字**:由 JS 插進去的節點在 DOM 裡長得一模一樣。
    """
    html = EDITOR_HTML.read_text(encoding="utf-8")

    for name in FORM_FIELDS:
        found = re.search(rf"<p[^>]*data-message-for=\"{name}\"[^>]*>", html)
        assert found is not None, f"{name} 的訊息槽必須宣告在 index.html 裡"
        assert "hidden" in found.group(0), f"{name} 的訊息槽預設就掛在畫面上"


def test_a_failed_check_is_told_apart_from_a_hint_without_colour_alone(
    editor_page,
) -> None:
    """8.4:未通過的訊息與常駐提示在視覺上分得開,而且**不只靠顏色**。

    標籤那一欄同時有兩者:一句常駐提示(「可填多個…」)與一個未通過的訊息。兩者
    若只差在顏色,對色覺障礙者等於沒有差別 —— 專案對難度說法用英文的理由就是這條
    (`structure.md`),同一個標準在此成立。

    先把標籤填成**填了而填錯**的樣子:空著的欄位如今是安靜的,量不到那一句話。
    """
    put(editor_page, "tags", "、,  ")

    styles = editor_page.evaluate(
        """() => {
          const read = (element) => {
            const style = getComputedStyle(element);
            return {
              visible: element.offsetParent !== null,
              color: style.color,
              fontWeight: style.fontWeight,
              fontStyle: style.fontStyle,
              textDecorationLine: style.textDecorationLine,
              paddingLeft: style.paddingLeft,
              borderLeftWidth: style.borderLeftWidth,
              borderLeftStyle: style.borderLeftStyle,
            };
          };
          const field = document.querySelector('[data-field="tags"]').closest('.field');
          const issue = field.querySelector('[data-message-for="tags"]');
          const hint = [...field.querySelectorAll('p')].find(
            node => node !== issue && node.textContent.trim() !== ''
          );
          if (issue === null || hint === undefined) return null;
          return { issue: read(issue), hint: read(hint) };
        }"""
    )

    assert styles is not None, "標籤那一欄沒有同時擺出未通過訊息與常駐提示"
    assert styles["issue"]["visible"], "標籤填錯了,未通過的訊息卻沒有顯示"
    assert styles["hint"]["visible"], "常駐提示不見了"

    non_colour = [
        key for key in styles["issue"] if key not in ("visible", "color")
    ]
    differences = [key for key in non_colour if styles["issue"][key] != styles["hint"][key]]
    assert differences, (
        "未通過的訊息與常駐提示只差在顏色,對色覺障礙者等於沒有差別:"
        f"{styles}"
    )


def test_aria_marks_the_controls_that_did_not_pass(editor_page) -> None:
    """8.4 的無障礙面:填錯的控制項帶 `aria-invalid`,填對的與還沒填的都不帶。

    訊息槽是看得見的那一半;螢幕閱讀器讀的是這一半。兩者由同一次呈現寫入,不會
    各說各話 —— 包括「還沒填的欄位不出聲」這條規則:對著一個空欄位宣告 invalid,
    在螢幕閱讀器上與畫面上是同一種噪音。
    """
    fill_valid_form(editor_page, id="26.5", tags="")

    marked = editor_page.evaluate(
        """() => Object.fromEntries(
          [...document.querySelectorAll('[data-field]')].map(
            element => [element.dataset.field, element.getAttribute('aria-invalid')]
          )
        )"""
    )

    assert marked["id"] == "true", f"填錯的欄位沒有標記:{marked}"
    assert marked["title"] is None, f"已填妥的欄位被標成未通過:{marked}"
    assert marked["tags"] is None, f"還沒填的欄位被標成未通過:{marked}"


# --- 本檔的邊界:寫入序列本身不在這裡驗 ---------------------------------


def test_the_write_control_never_touches_what_was_typed(editor_page) -> None:
    """按下寫入不得動到已填的內容 —— **只有寫入成功才清空**(7.2)。

    tasks 5.1 起這顆按鈕開始跑寫入序列(取題庫索引、撞號檢查),序列本身屬
    `tests/test_web_editor_write.py`。本條留下的是本檔仍答得出來的那一半:不論按下
    之後發生什麼,七個欄位裡的字都還在 —— 任何一次清空都可能讓維護者重抄一次 FEN。

    本檔的夾具沒有供索引端點,取索引因此會失敗,而那正是這裡要的:**連寫入序列
    在第一步就停掉的情況,已填的內容也一個字不少**(design 的 Error Handling)。

    本條原先另外斷言「按下去不發出任何請求」。5.1 把取索引接上之後那句話不再成立
    (該測試的說明已預告由 5.x 改寫),它要釘的邊界改由
    `test_the_write_sequence_stops_after_the_collision_check` 以「除了索引端點之外
    什麼都沒打」的形式接手。
    """
    fill_valid_form(editor_page)
    assert write_is_enabled(editor_page)

    editor_page.click(WRITE_BUTTON)
    editor_page.wait_for_timeout(200)

    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"按下寫入之後 {name} 的內容變了"
        )
