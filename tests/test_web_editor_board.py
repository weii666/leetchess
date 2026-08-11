"""收題頁的 FEN 輸入與盤面呈現(tasks 4.2;requirements 2.1–2.6)。

本檔問的是**行為**:FEN 欄位裡的字變了之後,盤面、訊息與起手方各自變成什麼樣子。
版面與 DOM 容器屬 tasks 4.1(`tests/test_web_editor_layout.py`),純函式屬 tasks 3.1
(`tests/test_web_editor_pure.py`),兩者都不在此重測。

## 為什麼頁面要走 http 而不是 `file://`

收題頁自本任務起掛上 `editor.js`,而它是 ES module —— Chromium 不允許自 `file://`
(origin 為 `null`)匯入 module,同一頁以 `file://` 開啟時整個模組根本不會執行。
因此本檔沿用 `tests/test_web_board.py` 的手法:以 `page.route()` 就地供 `web/` 底下的
**真實交付檔**,合成一個 http 來源。供的是真檔而不是替身頁面 —— 本任務要驗的正是
`web/editor/index.html` 與 `web/editor/editor.js` 接在一起之後的樣子。

## 盤面是縮放過的,座標要換算

`editor.css` 的 `#board svg { width: 100% }` 讓盤面隨欄寬縮放,`viewBox` 的使用者座標
因此不等於 CSS 像素。點擊一律經 `click_square()` 換算,不要直接拿 `viewBox` 的數字當
位置 —— 拿錯的話點到的是盤面上完全不同的一格,而「什麼都沒發生」的斷言照樣會綠。

## 這一頁新增的 DOM 契約(4.3 / 5.x 依賴)

兩個槽位都**宣告在 `web/editor/index.html`**,`editor.js` 只查得到、不生它們 ——
與 `#unsupported` 同一條規矩(位置固定、預設隱藏、由 JS 決定顯示與否)。

- `[data-message-for="fen"]` —— FEN 欄位的訊息槽,`hidden` 表示沒話說。屬性形式是
  刻意的:8.4 要把每一項未通過定位到欄位,4.3 為其餘六欄在 HTML 補上同形狀的槽即可。
- `#side-to-move` —— 起手方(2.6)。它讀自 FEN,沒有、也不得有獨立輸入。
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_JS = WEB_DIR / "editor" / "editor.js"
EDITOR_HTML = WEB_DIR / "editor" / "index.html"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-runtime.test"

#: 桌面尺寸。窄畫面的折行屬版面(4.1),此處固定在兩欄的情形下量行為。
DESKTOP = (1280, 800)

#: 供檔用的 content type。`.js` 若給錯型別,瀏覽器會直接拒絕執行整個模組。
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

#: 欄位一律以 `data-field` 查詢(tasks 4.1 的 DOM 契約),id 前綴只給 `<label for>` 用。
FEN_FIELD = '[data-field="fen"]'

#: 4.2 新增的兩個槽(見模組說明)。
FEN_MESSAGE = '[data-message-for="fen"]'
SIDE_TO_MOVE = "#side-to-move"

#: 《適情雅趣》第 21 局(`positions/適情雅趣/0001.json`)的起始局面,紅先。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 另一個合法局面,只有兩個帥將 —— 用來證明重繪只反映最後一次的內容。
TWO_KINGS_FEN = "4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1"

#: `PUZZLE_FEN` 的全部 19 個子,以「格名 -> 中文名」列出。逐格寫死而不在測試裡再
#: 解析一次 FEN:期望值要獨立於受測程式的推導方式。
EXPECTED_PIECES = {
    "d9": "士", "e9": "將",
    "d8": "俥", "e8": "士", "f8": "俥",
    "e7": "象", "i7": "傌",
    "g6": "傌",
    "c5": "象",
    "d3": "卒", "e3": "兵",
    "a2": "相", "e2": "炮", "g2": "馬", "i2": "相",
    "c1": "車", "d1": "卒", "g1": "車",
    "e0": "帥",
}

#: 上表中屬紅方(FEN 大寫)的格。
RED_SQUARES = {"d8", "f8", "i7", "g6", "e3", "a2", "e2", "i2", "e0"}

#: 前後帶空白的 `PUZZLE_FEN`。**貼上時多一個空白是常態** —— 自檔案或網頁複製一段
#: FEN,前後跟著一個空白或一個 tab 幾乎是免費附贈的。
#:
#: 這幾個案例釘住的是 `check.js` 與 `fen.js` 之間的一條契約:`checkFenStructure()`
#: 內部先 `trim()`(故帶空白的 FEN 通得過結構檢查),而 `parseFen()` 是以**字面的
#: 半形空格**切欄位的 —— 前導空白會讓它取到空的盤面段,於是畫出一個全空的盤面。
#: 兩者若拿到不同的字串,結果就是「訊息沒出現、棋子也沒出現」,與 2.5 的空輸入
#: 長得一模一樣。這正是 requirements 的 Introduction 說的最貴的那種錯誤:**看不出來**。
WHITESPACE_PADDED_FENS = [
    pytest.param(" " + PUZZLE_FEN, id="leading-space"),
    pytest.param(PUZZLE_FEN + " ", id="trailing-space"),
    pytest.param("  " + PUZZLE_FEN + "  ", id="both-sides"),
    pytest.param("\t" + PUZZLE_FEN + "\t", id="tabs"),
]

#: 結構上展不開成 10x9 盤面的 FEN(判準與說法都在 `check.js` 的 `checkFenStructure`)。
#: 四種各壞在不同的地方,任一種都必須清掉盤面而不是照畫一個殘缺的局面。
UNPARSEABLE_FENS = [
    pytest.param("3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2 w - - 0 1", id="nine-rows"),
    pytest.param("3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4/9 w - - 0 1", id="eleven-rows"),
    pytest.param("3ak44/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1", id="wide-row"),
    pytest.param("貼上前先抄一遍", id="not-a-fen"),
]

#: `editor.js` 可以匯入的模組(design 的 Components and Interfaces → `editor.js`)。
#: 只列出設計指派給它的依賴 —— 多出來的任何一個都表示組裝層開始自己長規則。
#:
#: `../catalog.js` 對應的是 design 那一列的「Outbound:`GET /api/catalog`(既有端點,
#: 取既有題號)」。design 寫的是**端點**,而那個端點在前端早就有一個唯一的 client
#: (`web/catalog.js`,列表頁用的那一支,帶逾時上界且不讓後端原文外流)。撞號檢查
#: (tasks 5.1)因此匯入它,而不是在組裝層再寫一份 fetch —— 後者正是本條要擋的
#: 「組裝層自己長規則」。
ALLOWED_IMPORTS = {
    "./check.js",
    "./corpus-file.js",
    "./fs.js",
    "../board.js",
    "../fen.js",
    "../difficulty.js",
    "../api.js",
    "../catalog.js",
}


# --- 夾具 ---------------------------------------------------------------


@pytest.fixture
def editor_page(browser_page) -> Iterator:
    """以 http 來源開啟**真實的**收題頁,模組因此載得進來(理由見模組說明)。"""

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
    # 盤面由模組在載入時就畫一次(空輸入即空盤面,2.5),等它出現再繼續 ——
    # 否則第一個斷言可能量在模組還沒執行完的瞬間。
    browser_page.wait_for_selector("#board svg")
    yield browser_page


def type_fen(page, fen: str) -> None:
    """把 `fen` 打進 FEN 欄位。

    `fill()` 會聚焦欄位並派送 `input` 事件,而**不失焦** —— 2.1 要的正是「每一次
    變動」都重繪,不是等到離開欄位才重繪。空字串即清空輸入(2.5)。
    """
    page.fill(FEN_FIELD, fen)


# --- 座標約定:紅方底線 rank 0 在下、a 路在左 ---------------------------

CELL = 62
MARGIN = 40
VIEWBOX_WIDTH = 576


def x_of(file: int) -> float:
    return MARGIN + file * CELL


def y_of(rank: int) -> float:
    return MARGIN + (9 - rank) * CELL


def square_at(x: float, y: float) -> str:
    """把 `viewBox` 座標換回格名,例如 `(288, 598)` -> `'e0'`。"""
    file = round((x - MARGIN) / CELL)
    rank = 9 - round((y - MARGIN) / CELL)
    return f"{chr(97 + file)}{rank}"


def drawn_pieces(page) -> dict[str, str]:
    """畫出來的每個子:格名 -> 中文名。"""
    pieces = page.evaluate(
        """() => [...document.querySelectorAll('#board .piece')].map(piece => {
          const disc = piece.querySelector('circle');
          return {
            x: Number(disc.getAttribute('cx')),
            y: Number(disc.getAttribute('cy')),
            name: piece.textContent.trim(),
            red: piece.classList.contains('red'),
          };
        })"""
    )
    return {square_at(piece["x"], piece["y"]): piece["name"] for piece in pieces}


def red_squares(page) -> set[str]:
    """畫出來的紅子在哪幾格。"""
    centres = page.evaluate(
        """() => [...document.querySelectorAll('#board .piece.red')].map(piece => {
          const disc = piece.querySelector('circle');
          return [Number(disc.getAttribute('cx')), Number(disc.getAttribute('cy'))];
        })"""
    )
    return {square_at(x, y) for x, y in centres}


def grid_segments(page) -> list[str]:
    """棋盤格線的每一段子路徑 —— 盤面本身還在不在,看的是這個而不是棋子。"""
    path = page.locator("#board svg path.grid")
    if path.count() == 0:
        return []
    return [segment.strip() for segment in re.findall(r"M[^M]*", path.get_attribute("d"))]


def click_square(page, square: str) -> None:
    """點在 `square` 這一格的正中央。

    盤面是縮放過的,故先量出實際寬度再把 `viewBox` 座標按比例換成 CSS 像素。
    刻意不用選擇器挑元素:命中誰由瀏覽器的 hit test 決定,這樣「點到子」「點到
    格線」「點到底色」三條路徑才都測得到。
    """
    board = page.locator("#board svg")
    box = board.bounding_box()
    scale = box["width"] / VIEWBOX_WIDTH
    file, rank = ord(square[0]) - 97, int(square[1])
    board.click(position={"x": x_of(file) * scale, "y": y_of(rank) * scale})


def message_text(page) -> str:
    """FEN 訊息槽當下說的話;沒有話說(`hidden`)時為空字串。"""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (element === null) return null;
          return element.hidden ? '' : element.textContent.trim();
        }""",
        FEN_MESSAGE,
    )


# --- 貼上即重繪(2.1)---------------------------------------------------


def test_pasting_a_fen_draws_that_position(editor_page) -> None:
    """2.1:貼上的 FEN 一子不差地出現在盤面上。

    比對全盤而不抽樣:抽樣會放過「整列右移一路」這類錯誤,而肉眼核對 FEN 正是
    這個工具存在的理由(見 requirements 的 Introduction)。
    """
    type_fen(editor_page, PUZZLE_FEN)

    assert drawn_pieces(editor_page) == EXPECTED_PIECES


@pytest.mark.parametrize("fen", WHITESPACE_PADDED_FENS)
def test_a_fen_padded_with_whitespace_draws_the_same_position(editor_page, fen: str) -> None:
    """2.1:前後多幾個空白仍然畫得出同一個局面,而且不報錯。

    這一條防的是一種**沉默的**失敗(見 `WHITESPACE_PADDED_FENS` 的說明):結構檢查
    看的是 `trim()` 過的字串,繪盤看的若是原字串,一個前導空白就會讓盤面全空 ——
    沒有訊息、沒有棋子,與「還沒開始填」完全分不出來。而使用者手上那串 FEN 是對的。
    """
    type_fen(editor_page, fen)

    assert drawn_pieces(editor_page) == EXPECTED_PIECES
    assert message_text(editor_page) == "", "前後的空白不是錯誤,不該有訊息"
    assert "紅先" in editor_page.locator(SIDE_TO_MOVE).inner_text()


def test_the_board_redraws_on_every_change_not_only_on_blur(editor_page) -> None:
    """2.1:「每一次變動」都重繪 —— 不是等到離開欄位才重繪。

    斷言的方式是**焦點還在 FEN 欄位裡**時就去看盤面:若實作接的是 `change` 或
    `blur`,此刻盤面上一個子都不會有。接著再改一次內容,盤面必須跟著換成新的
    局面,證明它跟的是當下的值而不是第一次的值。
    """
    type_fen(editor_page, PUZZLE_FEN)

    focused = editor_page.evaluate(
        "selector => document.activeElement === document.querySelector(selector)",
        FEN_FIELD,
    )
    assert focused, "fill() 之後焦點應仍在 FEN 欄位,否則這一條測不到 blur 與否"
    assert drawn_pieces(editor_page) == EXPECTED_PIECES

    type_fen(editor_page, TWO_KINGS_FEN)

    assert drawn_pieces(editor_page) == {"e9": "將", "e0": "帥"}


# --- 紅方在下(2.2)-----------------------------------------------------


def test_red_is_rendered_at_the_bottom(editor_page) -> None:
    """2.2:以紅方底線在下的視角繪製。

    以真實的版面座標判斷,而不是回頭讀繪製時寫下的屬性:座標算對但畫反了在屬性
    比對下看不出來。`board.js` 已經這樣畫(`tests/test_web_board.py`),此處確認
    收題頁**用的就是那一份繪製**,沒有自己再擺一次子。
    """
    type_fen(editor_page, PUZZLE_FEN)

    kings = editor_page.evaluate(
        """() => {
          const at = (name) => [...document.querySelectorAll('#board .piece')]
            .find(piece => piece.textContent.trim() === name)
            .getBoundingClientRect();
          const red = at('帥'), black = at('將');
          return {
            redY: red.top + red.height / 2,
            blackY: black.top + black.height / 2,
            redX: red.left + red.width / 2,
            blackX: black.left + black.width / 2,
          };
        }"""
    )

    assert kings["redY"] > kings["blackY"], "紅帥必須在黑將下方"
    assert abs(kings["redX"] - kings["blackX"]) < 1, "兩者都在 e 路,應同一條中線"


def test_red_and_black_pieces_are_distinguishable(editor_page) -> None:
    """2.2 的前提:紅子與黑子在呈現上分得開,否則「紅方在下」無從核對。"""
    type_fen(editor_page, PUZZLE_FEN)

    assert red_squares(editor_page) == RED_SQUARES


# --- 整片不可選取(2.3)-------------------------------------------------


def test_no_piece_is_selectable(editor_page) -> None:
    """2.3:盤面不可選取,而且是**因為著法集合是空的**。

    `board.js` 的可選取判準是「傳進來的著法裡有自該格出發的」,傳空陣列時整片
    盤面自然不可選 —— 唯讀盤面因此是既有模組的自然狀態,不是新增的參數。這一條
    釘住的正是那個機制:有任何一枚子帶上 `selectable`,就表示有人餵了著法進去。
    """
    type_fen(editor_page, PUZZLE_FEN)

    assert editor_page.locator("#board .piece").count() == len(EXPECTED_PIECES)
    assert editor_page.locator("#board .piece.selectable").count() == 0


@pytest.mark.parametrize(
    "square",
    [
        "e0",  # 紅帥
        "d8",  # 紅俥,在對局頁那組著法裡本來是有法可走的
        "e9",  # 黑將
        "c5",  # 黑象
        "a5",  # 空格
        "e5",  # 河界上的空格
    ],
)
def test_clicking_any_square_changes_nothing(editor_page, square: str) -> None:
    """2.3:點盤面上任何一格都不產生選中框、落點標示,也不改變任何一個子。

    有子的格與空格分開點:兩者在 `board.js` 裡是不同的 hit-testing 路徑,只點其中
    一種會漏掉另一種。
    """
    type_fen(editor_page, PUZZLE_FEN)
    before = drawn_pieces(editor_page)

    click_square(editor_page, square)

    assert drawn_pieces(editor_page) == before
    assert editor_page.locator("#board .piece.selected").count() == 0
    assert editor_page.locator("#board .dot").count() == 0


def test_clicking_the_margin_outside_the_grid_changes_nothing(editor_page) -> None:
    """2.3:盤面四周的留白帶也是使用者點得到的區域,同樣不得有任何反應。

    這一條不能靠格點涵蓋 —— 留白帶上 `elementFromPoint` 回傳的是底層的
    `rect.board-bg`,是另一條 hit-testing 路徑。
    """
    type_fen(editor_page, PUZZLE_FEN)
    before = drawn_pieces(editor_page)

    board = editor_page.locator("#board svg")
    box = board.bounding_box()
    scale = box["width"] / VIEWBOX_WIDTH
    for x, y in ((5, 5), (300, 633), (570, 300), (20, 320)):
        board.click(position={"x": x * scale, "y": y * scale})

    assert drawn_pieces(editor_page) == before
    assert editor_page.locator("#board .piece.selected").count() == 0
    assert editor_page.locator("#board .dot").count() == 0


# --- 無法解析(2.4)-----------------------------------------------------


@pytest.mark.parametrize("fen", UNPARSEABLE_FENS)
def test_an_unparseable_fen_clears_the_board_and_says_why(editor_page, fen: str) -> None:
    """2.4:結構不合法時顯示訊息,且**不繼續顯示前一個可解析內容的局面**。

    先貼一個畫得出來的局面再貼壞的,兩件事因此在同一條測試裡:訊息要出現,而且
    上一個局面的子一個都不能留在畫面上 —— 留著的話,使用者核對的是一個已經不存在
    於輸入框裡的局面,那比什麼都不畫更危險。
    """
    type_fen(editor_page, PUZZLE_FEN)
    assert drawn_pieces(editor_page) == EXPECTED_PIECES

    type_fen(editor_page, fen)

    assert drawn_pieces(editor_page) == {}, "盤面仍留著前一個局面"
    assert message_text(editor_page) != "", "沒有顯示無法解析的訊息"


def test_the_cleared_board_area_still_says_something(editor_page) -> None:
    """2.4:清掉盤面之後那一格不是一片空白。

    一塊沒有任何說明的空白區域看起來像是壞了(4.1 的 `.board-placeholder` 正是
    為此存在)。訊息的**細節**在欄位那一側,這裡只要交代這一格為什麼空著。
    """
    type_fen(editor_page, PUZZLE_FEN)

    type_fen(editor_page, "9/9/9 w - - 0 1")

    placeholder = editor_page.locator("#board .board-placeholder")
    assert placeholder.count() == 1, "盤面清掉之後留下一片沒有說明的空白"
    assert placeholder.is_visible()
    assert placeholder.inner_text().strip() != ""


def test_a_fen_that_becomes_valid_again_draws_the_new_position(editor_page) -> None:
    """壞掉的內容改對之後盤面要回來 —— 訊息也要跟著消失。

    無法解析不是一個會黏住的狀態:使用者改好了輸入,畫面就該只反映改好之後的值。
    """
    type_fen(editor_page, "9/9/9 w - - 0 1")
    assert message_text(editor_page) != ""

    type_fen(editor_page, PUZZLE_FEN)

    assert drawn_pieces(editor_page) == EXPECTED_PIECES
    assert message_text(editor_page) == "", "改好之後訊息還掛在畫面上"


# --- 清空即空盤面(2.5)-------------------------------------------------


def test_clearing_the_fen_shows_an_empty_board_without_an_error(editor_page) -> None:
    """2.5:清空輸入時呈現**空盤面**,而且不當成解析失敗。

    這是本任務最容易做錯的一處:`checkFenStructure('')` 會回一項未通過(那是給
    「必填」用的說法),照著顯示的話,一個空的 FEN 欄位就會掛著錯誤訊息 —— 而
    2.4 講的是**無法解析的內容**,空的輸入不是那件事(tasks.md 的 3.1 筆記)。
    """
    type_fen(editor_page, PUZZLE_FEN)
    assert drawn_pieces(editor_page) == EXPECTED_PIECES

    type_fen(editor_page, "")

    assert drawn_pieces(editor_page) == {}, "清空之後仍留著上一個局面"
    assert grid_segments(editor_page), "清空之後應呈現空盤面,而不是連盤面都沒有"
    assert message_text(editor_page) == "", "清空輸入不是解析失敗,不該有訊息"


def test_an_untouched_page_shows_an_empty_board_without_an_error(editor_page) -> None:
    """2.5:剛載入(FEN 欄位是空的)時與清空之後看到的是同一件事。

    同一個輸入值卻因為「使用者有沒有打過字」而呈現兩種樣子,是把歷史記進了畫面;
    盤面應該只是當下那串字的函式。
    """
    assert editor_page.input_value(FEN_FIELD) == ""
    assert drawn_pieces(editor_page) == {}
    assert grid_segments(editor_page), "空輸入時應呈現空盤面"
    assert message_text(editor_page) == ""


# --- 起手方(2.6)-------------------------------------------------------


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        (PUZZLE_FEN, "紅先"),
        (PUZZLE_FEN.replace(" w ", " b "), "黑先"),
    ],
)
def test_the_side_to_move_is_read_from_the_fen(editor_page, fen: str, expected: str) -> None:
    """2.6:起手方依 FEN 的走子方顯示。

    題庫容得下黑先的排局,因此兩種都要測 —— 只測紅先的話,一個永遠印「紅先」的
    實作照樣會綠。
    """
    type_fen(editor_page, fen)

    side = editor_page.locator(SIDE_TO_MOVE)
    assert side.count() == 1, "起手方沒有固定的呈現位置"
    assert expected in side.inner_text()


def test_the_side_to_move_updates_when_the_fen_changes(editor_page) -> None:
    """走子方改了,顯示要跟著改 —— 不能停在第一次讀到的值。"""
    type_fen(editor_page, PUZZLE_FEN)
    assert "紅先" in editor_page.locator(SIDE_TO_MOVE).inner_text()

    type_fen(editor_page, PUZZLE_FEN.replace(" w ", " b "))

    assert "黑先" in editor_page.locator(SIDE_TO_MOVE).inner_text()
    assert "紅先" not in editor_page.locator(SIDE_TO_MOVE).inner_text()


def test_the_side_to_move_has_no_input_of_its_own(editor_page) -> None:
    """2.6:起手方**不提供獨立輸入**。

    給了輸入框,維護者就填得出一個與 FEN 相矛盾的起手方,而題目檔裡根本沒有這個
    欄位可放。掃的是每一個表單控制項與它的標籤,不只看 `data-field`。
    """
    surfaces = editor_page.evaluate(
        """() => [...document.querySelectorAll('input, textarea, select')].map(
          element => [
            element.id,
            element.getAttribute('name') ?? '',
            element.getAttribute('data-field') ?? '',
            element.getAttribute('aria-label') ?? '',
            [...(element.labels ?? [])].map(label => label.textContent).join(' '),
          ].join(' ')
        )"""
    )

    for surface in surfaces:
        assert "起手方" not in surface and "走子方" not in surface, (
            f"出現了起手方的獨立輸入:{surface.strip()}"
        )


# --- 接線與依賴方向 -----------------------------------------------------


def test_the_page_loads_the_editor_module(editor_page) -> None:
    """收題頁確實掛上了 `editor.js`,而且是以 module 形態載入。

    `type="module"` 不是可有可無的寫法:`editor.js` 用 `import`,少了它整個檔案在
    第一行就是語法錯誤,而頁面看起來只是「什麼都沒發生」。
    """
    assert EDITOR_JS.is_file(), f"{EDITOR_JS} 必須存在"

    scripts = editor_page.evaluate(
        """() => [...document.querySelectorAll('script')].map(script => ({
          type: script.getAttribute('type'),
          src: script.getAttribute('src'),
        }))"""
    )

    assert any(
        script["type"] == "module" and script["src"] == "./editor.js"
        for script in scripts
    ), f"index.html 沒有以 module 掛上 ./editor.js:{scripts}"


def test_the_message_and_side_slots_are_declared_in_the_markup() -> None:
    """兩個槽位寫在 HTML 裡,不由 JS 生出來。

    這一頁已經有一條規矩(`#unsupported`):**要出現的東西先在版面裡佔好位置**,JS
    只決定它顯示與否。一頁上同時存在兩種相反的做法,4.3 要為其餘六欄補訊息槽時就得
    先猜該學哪一邊;而 JS 生節點的那一種還會在訊息出現與消失時推動底下的欄位。

    斷言看的是**檔案文字**而不是 DOM —— 由 JS 插進去的節點在 DOM 裡長得一模一樣,
    這一條要問的正是「它是不是一開始就在版面裡」。
    """
    html = EDITOR_HTML.read_text(encoding="utf-8")

    slot = re.search(r"<p[^>]*data-message-for=\"fen\"[^>]*>", html)
    assert slot is not None, "FEN 的訊息槽必須宣告在 index.html 裡"
    assert "hidden" in slot.group(0), f"訊息槽預設就掛在畫面上:{slot.group(0)}"
    assert re.search(r"<p[^>]*id=\"side-to-move\"[^>]*>", html) is not None, (
        "起手方的位置必須宣告在 index.html 裡"
    )


def test_the_editor_module_only_depends_on_its_designated_modules() -> None:
    """design 指派給 `editor.js` 的依賴就是那幾個,多一個都不行。

    組裝層只負責接線:檢查規則在 `check.js`、序列化在 `corpus-file.js`、平台 API 在
    `fs.js`。匯入了別的東西,通常表示其中一件事被搬到這裡自己做了一份。
    """
    imports = set(
        re.findall(
            r"^\s*import[^'\"]*['\"]([^'\"]+)['\"]",
            EDITOR_JS.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    assert imports <= ALLOWED_IMPORTS, f"匯入了設計未指派的模組:{imports - ALLOWED_IMPORTS}"


def test_the_editor_module_does_not_reimplement_the_fen_structure_check() -> None:
    """FEN 的結構判準只有一份,住在 `check.js`。

    只看 import 集合是不夠的:仍從 `check.js` 取別的東西、卻在組裝層另寫一段
    「幾列幾格」的判斷,上一條照樣會綠 —— 而那正是兩份判準開始漂移的起點。
    """
    source = EDITOR_JS.read_text(encoding="utf-8")

    assert "checkFenStructure" in source, "結構檢查必須呼叫 check.js 的那一份"
    assert "sideFromFen" in source, "起手方必須讀 check.js 的那一份"
    assert "split('/')" not in source and 'split("/")' not in source, (
        "組裝層自己在拆 FEN 的盤面段,結構判準因此有了第二份"
    )
