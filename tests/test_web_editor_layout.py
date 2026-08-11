"""收題頁的版面與表單容器(tasks 4.1;requirements 3.1、3.4、3.5、3.8、6.3、
7.6、8.1、8.2、8.3)。

本檔問的是**版面與 DOM 契約**,不是行為 —— 收題頁在本任務結束時還沒有任何
JavaScript(繪盤屬 4.2、欄位互動屬 4.3、寫入流程屬第 5 組)。因此這裡的每一條
斷言都只看「這一頁擺出了什麼、排成什麼樣子」。

## 為什麼版面要在真瀏覽器裡量

「左盤右表單」(8.1)不是「有兩個元素」就成立的:兩個 `<div>` 疊成上下兩列同樣
滿足「都存在」。這一頁與 `tests/test_web_layout.py` 走同一條路 —— 以指定尺寸的
視窗載入真實交付檔,再問瀏覽器 `getBoundingClientRect()` 排出來的位置。**欄位
若疊成單欄,下面的桌面測試就會紅**,這正是它存在的理由。

## 為什麼要自己種一個 SVG 進盤面區

盤面由 `board.js` 在 4.2 才畫進 `#board`,而 8.2(盤面外觀與對局頁一致)在本任務
就要成立 —— design 的 File Structure Plan 明載 `editor.css`「**須自帶盤面的尺寸
規則**」,因為對局頁那組 `#board svg { width: 100%; height: auto }` 住在
`web/style.css`,而收題頁不掛那一份。少了它,盤面會照 `board.js` 寫在元素上的
`width="576"` 撐開,窄畫面必然溢出。

那條規則的輸入是「`#board` 底下有一個帶 `viewBox` 與 `width`/`height` 呈現屬性的
`<svg>`」。本檔因此自己種一個形狀相同的替身進去 —— 種的是 `board.js` 產出的
**外形**(見下方常數),不是它的內容。等 4.2 接上真的繪製之後,這幾條斷言量的
仍是同一件事。

## 檔案以 `file://` 載入

與 `tests/test_web_editor_page.py`、`tests/test_web_list.py` 的骨架測試相同:本頁
在此階段沒有任何 `<script>`,也不發任何請求,樣式表走相對路徑就取得到。不必為了
量版面而拉起服務與引擎。
"""

from __future__ import annotations

import pathlib
import re

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_DIR = WEB_DIR / "editor"
EDITOR_HTML = EDITOR_DIR / "index.html"
EDITOR_CSS = EDITOR_DIR / "editor.css"

#: `board.js` 的 `viewBox` 尺寸(`MARGIN * 2 + CELL * (FILES - 1)` 與 rank 版本),
#: 與 `tests/test_web_layout.py` 同一組數字。盤面縮放後長寬比必須仍是這個比例。
BOARD_VIEWBOX_WIDTH = 576
BOARD_VIEWBOX_HEIGHT = 638
BOARD_ASPECT = BOARD_VIEWBOX_HEIGHT / BOARD_VIEWBOX_WIDTH

#: 桌面尺寸 —— 兩欄並排要成立的地方。
DESKTOP = (1280, 800)

#: 窄畫面。收題是桌機上的工作,但版面不得因此撐出橫向捲軸。
NARROW = [(390, 844), (768, 900)]

#: 表單欄位的順序,即 `web/editor/check.js` 的 `checkForm()` 回傳清單的順序
#: (該檔明載「清單順序即表單欄位順序」)。8.4 要把未通過項目定位到欄位,兩邊
#: 的順序一旦分家,使用者每改一欄就得重新找一次訊息在哪。
FORM_FIELDS = ["id", "title", "description", "difficulty", "tags", "fen", "target"]

#: 每一欄該用哪一種元素。**描述是 `textarea`** —— 3.8 要求描述允許換行,而
#: `<input>` 收不下換行(貼上時會被靜默吃掉)。**難度是 `select`** —— 3.2 要求
#: 三選一而非自由輸入的數值。
FIELD_ELEMENTS = {
    "id": "input",
    "title": "input",
    "description": "textarea",
    "difficulty": "select",
    "tags": "input",
    "fen": "input",
    "target": "input",
}

#: 7.6 是整個功能的硬邊界:本工具**只新增**。任何帶著這些字眼的控制項都表示有人
#: 開始往「改既有題目」的方向長,不論它叫什麼 id。
FORBIDDEN_CONTROL_WORDS = ["編輯", "修改", "刪除", "移除", "edit", "delete", "remove"]

#: 3.4 / 3.5:最長殺著距離由驗證工具回填、出處由所在資料夾表達,兩者都**不得**有
#: 輸入欄。給了的話,維護者填得出一個後端會拒絕的欄位。
FORBIDDEN_FIELD_WORDS = ["最長殺著", "殺著距離", "max_dtm", "dtm", "出處", "source"]

#: 難度說法的唯一出處是 `web/difficulty.js`(steering 的 Naming Conventions 把它
#: 列為繁體中文的兩個例外之一)。**HTML 裡一個字都不該有** —— 寫死一份就是第二個
#: 真相來源,改了模組而忘了頁面時兩邊會靜默分家。選項由 4.3 自模組產生。
DIFFICULTY_LABELS_IN_JS = ["Easy", "Medium", "Hard"]


# --- 輔助 ---------------------------------------------------------------


#: 種一個與 `board.js` 產出同形的 `<svg>` 進盤面區(理由見模組說明)。
#: 屬性三件套缺一不可:`viewBox` 決定長寬比、`width`/`height` 是 CSS 要蓋掉的
#: 那兩個呈現屬性。
PLANT_BOARD = """() => {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 576 638');
  svg.setAttribute('width', '576');
  svg.setAttribute('height', '638');
  const board = document.getElementById('board');
  if (board === null) throw new Error('#board 不存在');
  // 與 `board.js` 一樣用 `replaceChildren` —— 它一畫就把容器裡原有的東西換掉,
  // 佔位說明因此不會與盤面並存,量到的高度才是 4.2 之後的實際版面。
  board.replaceChildren(svg);
}"""


def hits(surface: str, words: list[str]) -> list[str]:
    """`surface` 命中了 `words` 裡的哪些字眼。

    拉丁字詞以「前後不是英文字母」為界比對,中文詞直接以子字串比對。這個差別是
    實質的:收題頁自己叫 editor,`edit` 若以子字串比對,任何帶 `editor` 的 id 或
    class 都會被誤判成「編輯既有題目的控制項」,而那會逼後續任務回頭改這個檔案。
    中文沒有這個問題 —— 「編輯」不會是別的詞的一部分。
    """
    lowered = surface.lower()
    found = []
    for word in words:
        if word.isascii():
            if re.search(rf"(?<![a-z]){re.escape(word.lower())}(?![a-z])", lowered):
                found.append(word)
        elif word.lower() in lowered:
            found.append(word)
    return found


def open_editor(page, size: tuple[int, int] = DESKTOP, plant_board: bool = True):
    """以 `size` 這個視窗尺寸載入收題頁。

    視窗尺寸必須在導覽**之前**設定 —— 版面是載入當下就算出來的,先開再改尺寸
    量到的是 resize 之後的結果,那不是使用者第一眼看到的畫面
    (與 `tests/test_web_layout.py` 的 `open_at` 同一個理由)。
    """
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(EDITOR_HTML.as_uri())
    if plant_board:
        page.evaluate(PLANT_BOARD)
    return page


def box_of(page, selector: str) -> dict[str, float]:
    """`selector` 排版後的邊界盒(CSS 像素,相對於視窗)。"""
    rect = page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (!element) return null;
          const { left, top, right, bottom, width, height } = element.getBoundingClientRect();
          return { left, top, right, bottom, width, height };
        }""",
        selector,
    )
    assert rect is not None, f"{selector} 不存在"
    return rect


def viewport_metrics(page) -> dict[str, float]:
    """視窗可用寬高與整份文件的捲動寬度。

    用 `documentElement.clientWidth` 而非 `window.innerWidth`:前者已扣掉直向
    捲軸佔去的寬度,而直向捲動是允許的。
    """
    return page.evaluate(
        """() => ({
          clientWidth: document.documentElement.clientWidth,
          clientHeight: document.documentElement.clientHeight,
          scrollWidth: document.documentElement.scrollWidth,
        })"""
    )


# --- 表單欄位(3.1、3.2、3.8)-------------------------------------------


def test_every_manual_field_has_a_control(browser_page) -> None:
    """3.1:題號、局名、描述、難度、標籤五項,加上 FEN(2.1)與目標檔案路徑(5.1)。

    以 `data-field` 認欄位而不是以 id:`data-field` 的取值就是 `check.js` 的
    `CheckIssue.field`,兩邊同名才讓 8.4「指出是哪一項未通過」有得對應。
    """
    open_editor(browser_page)

    found = browser_page.evaluate(
        """() => [...document.querySelectorAll('[data-field]')].map(element => ({
          field: element.dataset.field,
          tag: element.tagName.toLowerCase(),
        }))"""
    )

    assert [entry["field"] for entry in found] == FORM_FIELDS, (
        "表單欄位的組成或順序與 check.js 的 checkForm() 不一致"
    )
    for entry in found:
        assert entry["tag"] == FIELD_ELEMENTS[entry["field"]], (
            f"{entry['field']} 用的是 <{entry['tag']}>,"
            f"應為 <{FIELD_ELEMENTS[entry['field']]}>"
        )


@pytest.mark.parametrize("field", FORM_FIELDS)
def test_every_field_is_labelled(browser_page, field: str) -> None:
    """每一欄都有關聯到它的 `<label>`,而不只是旁邊擺一段文字。

    關聯是實質的:點標籤會聚焦到該欄,螢幕閱讀器也才唸得出這一格在問什麼。
    以 `element.labels` 判定 —— 那是瀏覽器**實際解析出來的**關聯,`for` 打錯字
    會讓它是空的,而肉眼看 HTML 看不出來。
    """
    open_editor(browser_page)

    label = browser_page.evaluate(
        """field => {
          const element = document.querySelector(`[data-field="${field}"]`);
          if (!element) return null;
          const labels = [...(element.labels ?? [])];
          return labels.map(node => node.textContent.trim()).join(' ');
        }""",
        field,
    )

    assert label, f"{field} 沒有任何關聯到它的 <label>"


def test_the_description_accepts_newlines(browser_page) -> None:
    """3.8:描述允許換行。

    斷言的是**行為**而不是元素名:真的打進兩行,再把值讀回來。`<input>` 會把
    換行靜默吃掉,這一條因此換成 `<input>` 就會紅。
    """
    open_editor(browser_page)

    browser_page.fill('[data-field="description"]', "第一行\n第二行")

    assert (
        browser_page.input_value('[data-field="description"]') == "第一行\n第二行"
    ), "描述欄位吃掉了換行"


def test_the_difficulty_control_is_a_choice_not_free_text(browser_page) -> None:
    """3.2:難度只能自三個合法分級中擇一,不接受自由輸入的數值。

    `<select>` 在結構上就沒有「自由輸入」這件事。同時確認初始值是空的 ——
    `check.js` 的 `checkForm()` 以空字串判定「還沒選」,預設就選中某一級會讓
    維護者在沒有做過選擇的情況下寫進一個難度。
    """
    open_editor(browser_page)

    assert browser_page.input_value('[data-field="difficulty"]') == "", (
        "難度預設就選了一級,維護者不做選擇也會寫進去"
    )


def test_the_difficulty_labels_are_not_duplicated_in_the_markup() -> None:
    """難度說法不得在 HTML 裡寫死一份 —— 唯一出處是 `web/difficulty.js`。

    選項由 4.3 自該模組產生。這一條看的是**檔案文字**而不是 DOM:4.3 之後 DOM 裡
    本來就會有那三個說法,而它們必須是 JS 放進去的。
    """
    text = EDITOR_HTML.read_text(encoding="utf-8")
    found = [label for label in DIFFICULTY_LABELS_IN_JS if label in text]

    assert not found, (
        f"HTML 裡寫死了難度說法 {found} —— 說法的唯一出處是 web/difficulty.js"
    )


# --- 不該存在的東西(3.4、3.5、7.6)-------------------------------------


def test_no_input_for_max_dtm_or_source(browser_page) -> None:
    """3.4、3.5:沒有最長殺著距離,也沒有出處的輸入。

    兩者都不是「忘了做」:`max_dtm` 由驗證工具回填(屬 corpus-verification),
    出處由目標檔所在的書目資料夾表達。給了輸入框,維護者就填得出一個後端會
    拒絕的欄位(既有 `_check_fields()` 對未知欄位的行為)。

    掃的是每一個表單控制項與每一個 `<label>` 的字面,不只看 `data-field`:
    一個沒掛 `data-field` 的輸入框照樣是輸入框。
    """
    open_editor(browser_page)

    surfaces = browser_page.evaluate(
        """() => [...document.querySelectorAll('input, textarea, select, label')].map(
          element => [
            element.id,
            element.getAttribute('name') ?? '',
            element.getAttribute('data-field') ?? '',
            element.getAttribute('aria-label') ?? '',
            element.getAttribute('placeholder') ?? '',
            element.textContent ?? '',
          ].join(' ')
        )"""
    )

    for surface in surfaces:
        found = hits(surface, FORBIDDEN_FIELD_WORDS)
        assert not found, f"出現了不該有的欄位({found}):{surface.strip()}"


def test_no_control_can_edit_or_delete_an_existing_puzzle(browser_page) -> None:
    """7.6:DOM 中不存在任何編輯或刪除既有題目的控制項。

    這是整個功能的硬邊界 —— 本工具**只新增**。斷言掃的是 DOM 而不是原始碼:
    HTML 註解裡本來就會討論這條邊界,拿檔案文字比對會被自己的說明打到。

    這一條刻意**不列舉允許的控制項**,而是禁掉一組字眼:後續任務會加上寫入與
    清空之類的按鈕(4.3、5.x),白名單會逼它們回頭改這個檔案,而黑名單不會 ——
    但只要有人開始往「改既有題目」的方向長,它就轉紅。
    """
    open_editor(browser_page)

    surfaces = browser_page.evaluate(
        """() => [...document.querySelectorAll(
          'a, button, input, textarea, select, [role="button"], [contenteditable]'
        )].map(element => [
          element.id,
          element.className ?? '',
          element.getAttribute('name') ?? '',
          element.getAttribute('aria-label') ?? '',
          element.getAttribute('title') ?? '',
          element.getAttribute('href') ?? '',
          element.textContent ?? '',
        ].join(' '))"""
    )

    for surface in surfaces:
        found = hits(surface, FORBIDDEN_CONTROL_WORDS)
        assert not found, f"出現了編輯或刪除既有題目的控制項({found}):{surface.strip()}"


# --- 不支援的瀏覽器(6.3)-----------------------------------------------


def test_the_unsupported_notice_has_a_fixed_place(browser_page) -> None:
    """6.3:平台不支援時的說明訊息有固定位置,且預設不佔畫面。

    位置固定的意思是「這段話該出現時,它出現在一個決定好的地方」——`fs.js` 的
    `isSupported()` 於載入時就評估得出來(design 的 Implementation Notes),因此
    它不會在使用者操作到一半時忽然冒出來推動版面(對局頁的 `#waiting` 正是栽在
    那件事上)。

    此處只確認槽位在、預設隱藏、且**排在表單欄位之前** —— 一個講「這個瀏覽器
    寫不了檔」的訊息擺在表單底下,等於等使用者填完才說。
    """
    open_editor(browser_page)

    notice = browser_page.locator("#unsupported")
    assert notice.count() == 1, "不支援的說明訊息沒有固定位置"
    assert notice.is_hidden(), "不支援的說明訊息預設就掛在畫面上"

    order = browser_page.evaluate(
        """() => {
          const notice = document.getElementById('unsupported');
          const first = document.querySelector('[data-field]');
          return notice.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING;
        }"""
    )
    assert order, "不支援的說明訊息排在表單欄位之後"


def test_the_unsupported_notice_says_why(browser_page) -> None:
    """6.3:訊息要「明確告知不支援**與其原因**」,不是一句「無法使用」。

    文字是靜態的(由 5.3 只負責掀開),故此處直接讀它的內容。
    """
    open_editor(browser_page)

    text = browser_page.evaluate(
        "() => document.getElementById('unsupported').textContent.trim()"
    )

    assert len(text) >= 20, f"不支援的說明訊息太短,說不出原因:{text!r}"


def test_hidden_regions_stay_hidden_under_the_stylesheet(browser_page) -> None:
    """`hidden` 的區塊不得被版面規則變回可見。

    與 `style.css` / `list.css` 同一個陷阱:任何給它 `display` 的規則都會蓋掉
    `hidden` 的預設值,那段「你的瀏覽器不支援」就會永遠掛在畫面上 —— 而絕大多數
    開這一頁的瀏覽器其實是支援的。
    """
    open_editor(browser_page)

    assert browser_page.locator("#unsupported").is_hidden()


# --- 左盤右表單(8.1)---------------------------------------------------


def test_the_form_sits_to_the_right_of_the_board_on_a_desktop_viewport(
    browser_page,
) -> None:
    """8.1:左側盤面、右側表單的兩欄版面。

    **兩個元素都存在不是兩欄的證據** —— 上下疊起來時它們同樣都在。這一條量的是
    排版後的實際位置:表單的左緣不得越過盤面的右緣(否則是重疊或左右顛倒),
    且表單的上緣要落在盤面的下緣之上(否則是疊成了兩列)。
    """
    page = open_editor(browser_page, DESKTOP)

    board = box_of(page, "#board")
    form = box_of(page, "#entry")

    assert board["width"] > 0 and board["height"] > 0, "盤面區排出來是空的"
    assert form["width"] > 0 and form["height"] > 0, "表單區排出來是空的"
    assert form["left"] >= board["right"] - 1, "表單沒有排在盤面右側"
    assert form["top"] < board["bottom"], "表單被推到盤面下方,不是兩欄"


@pytest.mark.parametrize("size", NARROW + [DESKTOP])
def test_no_horizontal_scrolling_at_any_width(browser_page, size) -> None:
    """任一寬度下都不得撐出橫向捲軸。

    盤面是這一頁最寬的東西,而 `board.js` 在元素上寫了 `width="576"` ——
    `editor.css` 少了那條尺寸規則的話,窄畫面立刻溢出。
    """
    page = open_editor(browser_page, size)

    metrics = viewport_metrics(page)
    assert metrics["scrollWidth"] <= metrics["clientWidth"], (
        f"{size[0]}×{size[1]} 下文件寬 {metrics['scrollWidth']}px "
        f"超過視窗可用寬 {metrics['clientWidth']}px"
    )


@pytest.mark.parametrize("size", NARROW)
def test_the_form_stacks_under_the_board_on_a_narrow_viewport(browser_page, size) -> None:
    """窄畫面上兩欄改為上下排列 —— 並排必然要橫向捲動。

    沿用對局頁的做法:單一 flex 容器加 `flex-wrap`,不寫媒體查詢,寬度是連續的。
    """
    page = open_editor(browser_page, size)

    board = box_of(page, "#board")
    form = box_of(page, "#entry")

    assert form["top"] >= board["bottom"] - 1, "窄畫面上表單仍與盤面並排"


# --- 盤面的尺寸規則(8.2)-----------------------------------------------


def test_the_editor_supplies_its_own_board_sizing_rule() -> None:
    """8.2:收題頁的樣式表**自帶**盤面的尺寸規則。

    對局頁那組 `#board svg { width: 100%; height: auto }` 住在 `web/style.css`,
    而收題頁不掛那一份(見下一條)。這是 design review 對本任務特別點名的一處。
    """
    assert EDITOR_CSS.is_file(), f"{EDITOR_CSS} 必須存在"

    css = EDITOR_CSS.read_text(encoding="utf-8")
    rule = re.search(r"#board\s+svg\s*\{([^}]*)\}", css)

    assert rule is not None, "editor.css 沒有 `#board svg` 的尺寸規則"
    body = rule.group(1)
    assert "width: 100%" in body, "`#board svg` 沒有把寬度交給容器"
    assert "height: auto" in body, "`#board svg` 沒有把高度交給 viewBox 的長寬比"


def test_the_editor_does_not_borrow_the_play_page_stylesheet() -> None:
    """收題頁不掛 `web/style.css` —— 這正是上一條必須成立的原因。

    寫成一條斷言而不只是註解:哪天有人「順手」把對局頁的樣式表接過來,
    上一條就會顯得多餘而可能被刪掉,收題頁的盤面規則也就跟著沒了。
    """
    assert "style.css" not in EDITOR_HTML.read_text(encoding="utf-8")


@pytest.mark.parametrize("size", NARROW + [DESKTOP])
def test_the_board_fills_its_column_and_keeps_its_aspect_ratio(browser_page, size) -> None:
    """盤面撐滿它那一欄,且長寬比仍是 `viewBox` 的比例。

    `board.js` 同時給了 `width`/`height` 屬性與 `viewBox`;CSS 只蓋掉其中一個的話
    比例就會壞掉 —— 棋盤看起來還在,格子卻不是正方形了。
    """
    page = open_editor(browser_page, size)

    board = box_of(page, "#board")
    svg = box_of(page, "#board svg")

    assert svg["width"] == pytest.approx(board["width"], abs=5), (
        f"盤面只用了 {svg['width']}px,它那一欄有 {board['width']}px"
    )
    ratio = svg["height"] / svg["width"]
    assert ratio == pytest.approx(BOARD_ASPECT, rel=0.02), (
        f"{size[0]}×{size[1]} 下盤面長寬比為 {ratio:.3f},應為 {BOARD_ASPECT:.3f}"
    )


@pytest.mark.parametrize("size", NARROW)
def test_the_board_fits_inside_a_narrow_viewport(browser_page, size) -> None:
    """盤面整個落在視窗內,不被裁切也不溢出。

    只驗「沒有橫向捲軸」是不夠的:把盤面切掉一半同樣沒有捲軸。
    """
    page = open_editor(browser_page, size)

    metrics = viewport_metrics(page)
    svg = box_of(page, "#board svg")

    assert svg["left"] >= 0, f"盤面左緣在視窗外({svg['left']}px)"
    assert svg["right"] <= metrics["clientWidth"] + 1, (
        f"盤面右緣 {svg['right']}px 超出視窗寬 {metrics['clientWidth']}px"
    )


# --- 樣式表確實是交付的一部分 -------------------------------------------


def test_the_stylesheet_is_served_and_applied(browser_page) -> None:
    """`index.html` 連的 `./editor.css` 真的存在且被套用。

    以「body 不再是預設的無邊距/預設區塊排版」當探針:路徑打錯時那個請求是
    404,頁面拿到的是瀏覽器預設樣式,而上面每一條版面斷言都會以難懂的方式紅掉。
    這一條讓那種情形一眼看得出原因。
    """
    assert EDITOR_CSS.is_file(), f"{EDITOR_CSS} 必須存在"

    page = open_editor(browser_page, DESKTOP)
    body = page.evaluate(
        """() => {
          const style = getComputedStyle(document.body);
          return { margin: style.marginTop, display: style.display };
        }"""
    )

    assert body["margin"] == "0px", "body 仍是瀏覽器預設邊距,樣式表沒有生效"
    assert body["display"] == "flex", "body 不是 flex 容器,兩欄版面沒有生效"
