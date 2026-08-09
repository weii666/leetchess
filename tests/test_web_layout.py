"""版面與行動裝置適配(tasks 5.1;requirements 8.2、4.4)。

requirements 8.2 說的是「在行動裝置的直向畫面上完整呈現盤面與對局資訊,**不需
橫向捲動**」。這句話唯一能被驗證的地方是真實瀏覽器的排版結果 —— CSS 規則本身
讀起來對不對不算證據,只有 `getBoundingClientRect()` 與 `scrollWidth` 算。因此本
檔一律以指定尺寸的視窗載入 `web/play.html` 本身,再問瀏覽器排出來多大。

## 三件事,缺一不可

1. **沒有橫向捲軸** —— `documentElement.scrollWidth <= clientWidth`。這是 8.2 的
   字面要求,也是唯一不看主觀判斷的那一條。
2. **盤面完整可見** —— 盤面若只是被切掉一半,第 1 條仍可能成立(`overflow:hidden`
   就足以讓捲軸消失)。故另外斷言盤面的邊界盒完全落在視窗內,且長寬比仍是
   `viewBox` 的比例 —— 縮放不得把棋盤壓扁。
3. **側欄的每一區都排得出來** —— 題目資訊、信號、歷史著法、重來按鈕。版面把側欄
   擠成零寬或推到視窗外,第 1、2 條都還是綠的。

## 為什麼還要驗互動

版面是唯一能讓「點得到的東西變成點不到」的一層:盤面縮放後,`board.js` 掛在
`<g>` 與落點圓上的事件仍在,但點擊落在哪一個元素上改由排版後的實際幾何決定。
因此最後一節在行動裝置尺寸下真的走一手 —— 座標依實際渲染尺寸換算,而不是
`viewBox` 的使用者座標。

## 夾具沿用 `test_web_play.py`

那裡的 `play_page` 供的是 `web/` 底下的真實交付檔(含 `style.css`),題目端點也
已備妥。版面要問的是同一個頁面在不同視窗尺寸下的樣子,重開一份夾具只會讓兩邊
漂移。pytest 以檔名為模組名匯入 `tests/` 下的測試檔,故此處直接以模組名匯入。
"""

from __future__ import annotations

import pathlib

import pytest

from test_web_play import (  # noqa: F401 — `play_page` 以夾具身分被 pytest 取用
    black_reply,
    open_game,
    play_page,
    route_black_move,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
STYLE_CSS = PROJECT_ROOT / "web" / "style.css"

#: `board.js` 的 `viewBox` 尺寸(`MARGIN * 2 + CELL * (FILES - 1)` 與 rank 版本)。
#: 盤面縮放後的長寬比必須仍是這個比例。
BOARD_VIEWBOX_WIDTH = 576
BOARD_VIEWBOX_HEIGHT = 638
BOARD_ASPECT = BOARD_VIEWBOX_HEIGHT / BOARD_VIEWBOX_WIDTH

#: 行動裝置的直向尺寸。取市面上常見的三種寬度,涵蓋現行機種的窄端到寬端。
MOBILE_PORTRAIT = [(390, 844), (360, 640), (414, 896)]

#: 桌面尺寸 —— 版面在大視窗下同樣不得產生橫向捲軸。
DESKTOP = (1280, 800)

#: 側欄裡使用者必須看得到的每一區(對應 requirements 1.2、1.3、4.1、8.1、8.4、5.1)。
SIDEBAR_SELECTORS = [
    "#puzzle-title",  # 局名
    "#puzzle-source",  # 出處
    "#puzzle-max-dtm",  # 最長殺著距離
    "#turn",  # 當前輪方
    "#signal",  # 三態諮詢信號
    "#moves-panel",  # 歷史著法
    "#reset",  # 重來
]


# --- 輔助 ---------------------------------------------------------------


def open_at(page, size: tuple[int, int], bodies=None):
    """以 `size` 這個視窗尺寸載入頁面並等到盤面畫出來。

    視窗尺寸必須在導覽**之前**設定 —— 版面是載入當下就算出來的,先開再改尺寸
    測到的是 resize 後的結果,那不是使用者第一眼看到的畫面。
    """
    page.set_viewport_size({"width": size[0], "height": size[1]})
    if bodies is not None:
        route_black_move(page, bodies)
    open_game(page)
    return page


def viewport_metrics(page) -> dict[str, float]:
    """視窗實際可用的寬高與整份文件的捲動寬度。

    用 `documentElement.clientWidth` 而非 `window.innerWidth`:前者已扣掉直向捲軸
    佔去的寬度,而直向捲動是允許的。拿 `innerWidth` 比會把捲軸的寬度誤判成溢出。
    """
    return page.evaluate(
        """() => ({
          clientWidth: document.documentElement.clientWidth,
          clientHeight: document.documentElement.clientHeight,
          scrollWidth: document.documentElement.scrollWidth,
        })"""
    )


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


# --- 沒有橫向捲軸(8.2)-------------------------------------------------


@pytest.mark.parametrize("size", MOBILE_PORTRAIT)
def test_mobile_portrait_needs_no_horizontal_scrolling(play_page, size) -> None:
    """8.2 的字面要求:行動裝置直向畫面不需橫向捲動。"""
    page = open_at(play_page, size)

    metrics = viewport_metrics(page)
    assert metrics["scrollWidth"] <= metrics["clientWidth"], (
        f"{size[0]}×{size[1]} 下文件寬 {metrics['scrollWidth']}px "
        f"超過視窗可用寬 {metrics['clientWidth']}px,會出現橫向捲軸"
    )


def test_desktop_needs_no_horizontal_scrolling(play_page) -> None:
    """桌面尺寸同樣不得溢出 —— 行動裝置的適配不能是把桌面版擠壞換來的。"""
    page = open_at(play_page, DESKTOP)

    metrics = viewport_metrics(page)
    assert metrics["scrollWidth"] <= metrics["clientWidth"]


# --- 盤面完整可見 -------------------------------------------------------


@pytest.mark.parametrize("size", MOBILE_PORTRAIT)
def test_the_board_fits_entirely_inside_the_mobile_viewport(play_page, size) -> None:
    """盤面整個落在視窗內,不被裁切也不溢出。

    只驗「沒有橫向捲軸」是不夠的:把盤面切掉一半同樣沒有捲軸。
    """
    page = open_at(play_page, size)

    metrics = viewport_metrics(page)
    board = box_of(page, "#board svg")

    assert board["width"] > 0 and board["height"] > 0, "盤面沒有排出可見的尺寸"
    assert board["left"] >= 0, f"盤面左緣在視窗外({board['left']}px)"
    assert board["right"] <= metrics["clientWidth"] + 1, (
        f"盤面右緣 {board['right']}px 超出視窗寬 {metrics['clientWidth']}px"
    )
    assert board["top"] >= 0, f"盤面上緣在視窗外({board['top']}px)"
    assert board["bottom"] <= metrics["clientHeight"] + 1, (
        f"盤面下緣 {board['bottom']}px 超出視窗高 {metrics['clientHeight']}px"
    )


@pytest.mark.parametrize("size", MOBILE_PORTRAIT + [DESKTOP])
def test_the_board_keeps_its_aspect_ratio(play_page, size) -> None:
    """縮放盤面不得把它壓扁 —— 長寬比仍是 `viewBox` 的比例。

    `board.js` 同時給了 `width` / `height` 屬性與 `viewBox`;若 CSS 只蓋掉其中一個,
    比例就會壞掉,棋盤看起來仍在但格子已不是正方形。
    """
    page = open_at(play_page, size)

    board = box_of(page, "#board svg")
    ratio = board["height"] / board["width"]

    assert ratio == pytest.approx(BOARD_ASPECT, rel=0.02), (
        f"{size[0]}×{size[1]} 下盤面長寬比為 {ratio:.3f},應為 {BOARD_ASPECT:.3f}"
    )


@pytest.mark.parametrize("size", MOBILE_PORTRAIT)
def test_the_board_uses_the_width_it_is_given(play_page, size) -> None:
    """盤面在窄畫面上要撐得起來,而不是縮成一小塊。

    「不需橫向捲動」若靠把盤面縮到 100px 換來,棋子上的字也就看不清了。
    """
    page = open_at(play_page, size)

    metrics = viewport_metrics(page)
    board = box_of(page, "#board svg")

    assert board["width"] >= metrics["clientWidth"] * 0.8, (
        f"盤面只用了 {board['width']}px,視窗可用寬為 {metrics['clientWidth']}px"
    )


# --- 側欄的每一區都排得出來 ---------------------------------------------


@pytest.mark.parametrize("size", MOBILE_PORTRAIT + [DESKTOP])
@pytest.mark.parametrize("selector", SIDEBAR_SELECTORS)
def test_every_sidebar_region_is_visible_and_inside_the_viewport(
    play_page, size, selector
) -> None:
    """題目資訊、輪方、信號、歷史著法、重來在任一尺寸下都看得到且不溢出。"""
    page = open_at(play_page, size)

    metrics = viewport_metrics(page)
    assert page.locator(selector).is_visible(), f"{selector} 不可見"

    box = box_of(page, selector)
    assert box["width"] > 0 and box["height"] > 0, f"{selector} 排出來是空的"
    assert box["left"] >= 0, f"{selector} 左緣在視窗外({box['left']}px)"
    assert box["right"] <= metrics["clientWidth"] + 1, (
        f"{selector} 右緣 {box['right']}px 超出視窗寬 {metrics['clientWidth']}px"
    )


def test_the_sidebar_sits_beside_the_board_on_a_desktop_viewport(play_page) -> None:
    """桌面尺寸下側欄在盤面旁邊,而不是被推到下一列。

    這是「桌面與行動裝置各自的版面」中桌面那一半 —— 少了它,「行動裝置適配」
    只要一路單欄到底就能全綠。
    """
    page = open_at(play_page, DESKTOP)

    board = box_of(page, "#board")
    sidebar = box_of(page, "#sidebar")

    assert sidebar["left"] >= board["right"] - 1, "側欄沒有排在盤面右側"
    assert sidebar["top"] < board["bottom"], "側欄被推到盤面下方"


@pytest.mark.parametrize("size", MOBILE_PORTRAIT)
def test_the_sidebar_stacks_under_the_board_on_a_mobile_viewport(play_page, size) -> None:
    """行動裝置直向畫面下側欄改排在盤面下方 —— 兩欄並排必然要橫向捲動。"""
    page = open_at(play_page, size)

    board = box_of(page, "#board")
    sidebar = box_of(page, "#sidebar")

    assert sidebar["top"] >= board["bottom"] - 1, "側欄仍與盤面並排"


# --- 信號的說明是從屬資訊(4.4)-----------------------------------------


def test_the_signal_note_reads_as_subordinate_to_the_reading(play_page) -> None:
    """4.4:信號要看得出是參考資訊而非勝負判決。

    說明那行(`.signal-note`)在視覺上必須從屬於讀數(`.signal-reading`)—— 字級更
    小、顏色更淡。兩行長得一樣重時,「參考而非判決」就只存在於文案裡。
    """
    page = open_at(play_page, DESKTOP)

    styles = page.evaluate(
        """() => {
          const read = (selector) => {
            const style = getComputedStyle(document.querySelector(selector));
            return { fontSize: parseFloat(style.fontSize), color: style.color };
          };
          return { reading: read('.signal-reading'), note: read('.signal-note') };
        }"""
    )

    assert styles["note"]["fontSize"] < styles["reading"]["fontSize"], (
        f"說明字級 {styles['note']['fontSize']}px 不小於讀數 "
        f"{styles['reading']['fontSize']}px"
    )
    assert styles["note"]["color"] != styles["reading"]["color"], "說明與讀數同色"


# --- 套上版面之後互動仍然可用 -------------------------------------------


def test_pieces_and_destinations_stay_clickable_when_the_board_is_scaled(
    play_page,
) -> None:
    """行動裝置尺寸下仍然選得到子、點得到落點,並真的走出那一手。

    盤面縮放後,`viewBox` 的使用者座標不再等於 CSS 像素;點擊要換算成實際渲染
    尺寸。這一節同時保護 tasks 3.2 的 `pointer-events: all` —— 樣式若把它蓋掉,
    吃子標示的圈內會穿透到底下的子,這一手就走不出去。
    """
    page = open_at(play_page, MOBILE_PORTRAIT[0], bodies=[black_reply(move="e9d9")])

    def click(square: str) -> None:
        box = box_of(page, "#board svg")
        file, rank = ord(square[0]) - 97, int(square[1])
        scale_x = box["width"] / BOARD_VIEWBOX_WIDTH
        scale_y = box["height"] / BOARD_VIEWBOX_HEIGHT
        page.mouse.click(
            box["left"] + (40 + file * 62) * scale_x,
            box["top"] + (40 + (9 - rank) * 62) * scale_y,
        )

    click("d8")
    page.wait_for_selector("#board svg .piece.selected")
    assert page.locator("#board svg .dot").count() > 0, "選中之後沒有標出落點"

    click("d9")
    page.wait_for_function("() => document.getElementById('waiting').hidden")

    assert page.locator("#moves li").count() == 1, "點落點沒有走出那一手"

    # 記譜進來之後版面仍不得溢出 —— 空的側欄不會溢出,有內容的才會。
    metrics = viewport_metrics(page)
    assert metrics["scrollWidth"] <= metrics["clientWidth"], (
        f"記下一手之後文件寬 {metrics['scrollWidth']}px "
        f"超過視窗可用寬 {metrics['clientWidth']}px"
    )

    # 著法列不得撐出 `#moves` **自己的**橫向捲軸。
    #
    # 不能拿 `li` 的右緣跟視窗比:`#moves` 設了 `overflow-y: auto`,依 CSS 規範
    # 這會強制 `overflow-x` 也成為 `auto`,於是水平溢出被它自己的內部捲軸吸收,
    # `li` 的佈局盒**永遠**不會超出容器 —— 那樣的斷言結構上不可能失敗。
    moves_overflow = page.evaluate(
        """() => {
          const list = document.getElementById('moves');
          return { scrollWidth: list.scrollWidth, clientWidth: list.clientWidth };
        }"""
    )
    assert moves_overflow["scrollWidth"] <= moves_overflow["clientWidth"], (
        f"著法列撐出 #moves 的橫向捲軸:"
        f"內容寬 {moves_overflow['scrollWidth']}px "
        f"超過可視寬 {moves_overflow['clientWidth']}px"
    )


# --- 樣式表確實是交付的一部分 -------------------------------------------


def test_the_stylesheet_is_served_and_applied(play_page) -> None:
    """`play.html` 連的 `./style.css` 真的存在且被套用。

    tasks 1.2 刻意先把 `<link>` 接好;本任務之前那個請求一直是 404,頁面拿到的是
    瀏覽器預設樣式。以「body 不再是預設的無邊距/預設字體」當探針,證明這一份
    樣式表確實進到了頁面,而不只是躺在磁碟上。
    """
    assert STYLE_CSS.is_file(), f"{STYLE_CSS} 必須存在"

    page = open_at(play_page, DESKTOP)
    body = page.evaluate(
        """() => {
          const style = getComputedStyle(document.body);
          return { margin: style.marginTop, display: style.display };
        }"""
    )

    assert body["margin"] == "0px", "body 仍是瀏覽器預設邊距,樣式表沒有生效"
    assert body["display"] != "block", "body 仍是預設的區塊排版,版面沒有生效"


def test_hidden_regions_stay_hidden_under_the_stylesheet(play_page) -> None:
    """`hidden` 的等待與錯誤區塊不得被版面規則變回可見。

    `#waiting` 與 `#error` 的顯示由 `app.js` 以 `hidden` 屬性控制(requirements
    6.1、6.4、7.1)。任何給它們 `display` 的規則都會蓋掉 `hidden` 的預設值,
    使畫面永遠停在「引擎思考中」。
    """
    page = open_at(play_page, DESKTOP)

    assert page.locator("#waiting").is_hidden(), "等待區塊被樣式變回可見"
    assert page.locator("#error").is_hidden(), "錯誤區塊被樣式變回可見"
