"""題庫列表頁在行動裝置直向畫面下的版面(problem-browser requirements 6.1)。

requirements 6.1:「行動裝置的直向畫面上完整呈現列表,不需橫向捲動」。這句話唯一
能被驗證的地方是真實瀏覽器排出來的結果 —— 讀 `list.css` 的規則本身不算數,只有
`getBoundingClientRect()` 與 `scrollWidth` 算(同 `test_web_layout.py` 的哲學,
見該檔模組說明)。

## 為什麼沿用 `test_web_list.py` 的夾具而不是另起一份

`list_page` / `open_list` / `CATALOG` 已經是題庫列表頁的標準測試路徑,重開一份
只會讓兩邊漂移。pytest 以檔名為模組名匯入 `tests/` 下的測試檔,此處直接以模組名
匯入(與 `test_web_layout.py` 匯入 `test_web_play` 同一慣例)。

## 標籤逐字斷行怎麼被測出來

`.position-tag` 是純文字 pill,正常橫排的形狀天生寬扁(`padding: 3px 10px`,
左右內距遠大於上下,`width > height`)。被壓縮到逐字斷行時,每顆 pill 會變成
又窄又高的長條。比對 `flex-basis` 之類的 CSS 屬性值只能證明規則寫對了,不能證明
排出來的形狀對 —— 形狀才是使用者回報的症狀本身。
"""

from __future__ import annotations

import pytest

from test_web_layout import DESKTOP, MOBILE_PORTRAIT
from test_web_list import list_page, open_list  # noqa: F401 — list_page 以夾具身分被取用

#: 六個子元素的 CSS 選擇器,依 `list.js` 的 `append()` 順序(即 DOM 序)排列。
#: `list.css` 檔頭「欄序即 DOM 序」的規則要求視覺順序與這份順序一致,唯一的
#: 例外是 `.position-tags`(見 `test_visual_order_matches_dom_order_except_tags`)。
ROW_CHILD_SELECTORS = [
    ".position-id",
    ".position-title",
    ".position-tags",
    ".position-difficulty",
    ".position-toggle",
    ".position-star",
]

#: 三個真正需要視覺序與 DOM 序一致的可聚焦元素(局名連結、完成勾選、星號
#: 按鈕)——`.position-tags` 是純文字 `<span>`,不在 Tab 序列裡,不必比對。
FOCUSABLE_CHILD_SELECTORS = [
    ".position-title",
    ".position-toggle",
    ".position-star",
]

#: 同一個 flex line 內,依中心對齊(`align-items: center`)的不同高度元素之間,
#: 垂直中心天生會有的次像素落差上限。實測題號/局名/難度/完成勾選/星號同行時
#: 落差在個位數 px 內;跨行的落差以整行高度(數十 px)計,兩者差距懸殊,這個
#: 容差不會讓真正的順序顛倒被放過。
_ROW_JITTER_TOLERANCE_PX = 8


def open_at(page, size: tuple[int, int]):
    """以 `size` 這個視窗尺寸載入列表頁並等到列畫出來。

    視窗尺寸必須在導覽**之前**設定,理由同 `test_web_layout.open_at`:版面是
    載入當下就算出來的,先開再改尺寸測到的是 resize 後的結果。
    """
    page.set_viewport_size({"width": size[0], "height": size[1]})
    open_list(page)
    return page


def viewport_metrics(page) -> dict[str, float]:
    """視窗實際可用寬度與整份文件的捲動寬度。"""
    return page.evaluate(
        """() => ({
          clientWidth: document.documentElement.clientWidth,
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


# --- 沒有橫向捲軸(6.1)---------------------------------------------------


@pytest.mark.parametrize(
    "size", MOBILE_PORTRAIT, ids=["size0", "size1", "size2"]
)
def test_mobile_portrait_needs_no_horizontal_scrolling(list_page, size) -> None:
    """6.1 的字面要求:行動裝置直向畫面不需橫向捲動。"""
    page = open_at(list_page, size)

    metrics = viewport_metrics(page)
    assert metrics["scrollWidth"] <= metrics["clientWidth"], (
        f"{size[0]}×{size[1]} 下文件寬 {metrics['scrollWidth']}px "
        f"超過視窗可用寬 {metrics['clientWidth']}px,會出現橫向捲軸"
    )


# --- 標籤整顆換行,不逐字斷行 ---------------------------------------------


@pytest.mark.parametrize("size", [MOBILE_PORTRAIT[0]], ids=["size0"])
def test_tags_wrap_as_whole_pills_not_character_by_character(list_page, size) -> None:
    """回報症狀本身:標籤 pill 在窄螢幕下要整顆換行,不是逐字斷行。

    第 1 題的標籤是「雙馬」「連將殺」(`test_web_list.CATALOG`)—— 逐字斷行時每顆
    pill 會變成又窄又高的長條;正常橫排的 pill 天生寬扁,見模組說明。
    """
    page = open_at(list_page, size)

    tag_boxes = page.evaluate(
        """() => [...document.querySelectorAll(
          '#positions > li[data-id="1"] .position-tag'
        )].map((el) => {
          const { width, height } = el.getBoundingClientRect();
          return { width, height };
        })"""
    )

    assert len(tag_boxes) == 2, f"預期第 1 題有 2 顆標籤,實際排出 {len(tag_boxes)} 顆"
    for box in tag_boxes:
        assert box["width"] > box["height"], (
            f"標籤 pill 排成 {box['width']:.1f}×{box['height']:.1f},"
            "寬度不大於高度 —— 逐字斷行時 pill 會變成這種窄高的長條"
        )


@pytest.mark.parametrize("size", [MOBILE_PORTRAIT[0]], ids=["size0"])
def test_tags_occupy_their_own_full_width_row(list_page, size) -> None:
    """`.position-tags { flex-basis: 100% }` 這條規則本身要有測試守著。

    這條規則不是防止逐字斷行的機制(那是 `.position { display: flex }` 本身
    的效果——flex 預設依內容最小寬度排列,不像 grid 的 `minmax(0, 1fr)` 允許
    壓到 0;拿掉 `flex-basis: 100%`,標籤在本測試的短標題+短標籤資料下依然會
    正常橫排、不逐字斷行,`test_tags_wrap_as_whole_pills_not_character_by_character`
    因此測不出這條規則被拿掉)。它的作用是**強迫標籤獨佔一行**,讓標籤欄不與
    局名共用第一行——標題長度不一時,少了這條規則標籤會有時跟局名同行、有時
    自己一行,每一列的節奏就不統一了。直接斷言標籤欄的頂端在局名底端之後
    (兩者不同行),把這條規則的存在意義變成一條會轉紅的斷言。
    """
    page = open_at(list_page, size)

    title = box_of(page, '#positions > li[data-id="1"] .position-title')
    tags = box_of(page, '#positions > li[data-id="1"] .position-tags')

    assert tags["top"] >= title["bottom"] - 1, (
        f"標籤欄(top={tags['top']:.1f})與局名(bottom={title['bottom']:.1f})疊在"
        "同一行——flex-basis: 100% 沒有生效,標籤欄沒有獨佔一行"
    )


# --- 視覺順序即 DOM 序(標籤除外)------------------------------------------


@pytest.mark.parametrize("size", [MOBILE_PORTRAIT[0]], ids=["size0"])
def test_visual_order_matches_dom_order_except_tags(list_page, size) -> None:
    """`list.css` 檔頭「欄序即 DOM 序」的規則在行動裝置斷點下,對三個可聚焦
    元素(局名連結、完成勾選、星號按鈕)仍然成立。

    六欄 grid 換成 flex column-wrap 之後,沒有任何規則指定元素該落在哪一行 ——
    這條斷言把檔頭的文字承諾變成一條會轉紅的斷言:由上而下的視覺順序必須與
    DOM 序(局名→完成勾選→星號)一致。`.position-tags` 是唯一的例外(見
    `test_tags_are_the_last_row_despite_their_dom_position`),不放進這裡比對。

    比的是每個元素的**垂直中心**(`top + height/2`),不是 `top` 本身:同一個
    `.position` 仍是 `align-items: center`(基底規則沒被斷點覆寫),同一行內
    高度不同的元素(例如 28px 的星號按鈕與 16px 的局名文字)會依中心對齊,
    `top` 因此天生有數 px 落差,即使它們排在同一行也一樣 —— 比 `top` 會把這種
    正常的同行落差誤判成順序顛倒。`_ROW_JITTER_TOLERANCE_PX` 再放寬一點空間
    給同行內的次像素捨入,只有真正跨行的顛倒(落差以十位數 px 計)才會轉紅。
    """
    page = open_at(list_page, size)

    centers = [
        box["top"] + box["height"] / 2
        for box in (
            box_of(page, f'#positions > li[data-id="1"] {selector}')
            for selector in FOCUSABLE_CHILD_SELECTORS
        )
    ]

    for previous, current, label in zip(
        centers, centers[1:], FOCUSABLE_CHILD_SELECTORS[1:]
    ):
        assert current >= previous - _ROW_JITTER_TOLERANCE_PX, (
            f"視覺順序(由上到下)偏離了 DOM 序,{label} 的垂直中心"
            f"({current:.1f}px)明顯早於它前一個元素({previous:.1f}px):"
            f"{list(zip(FOCUSABLE_CHILD_SELECTORS, centers))}"
        )


@pytest.mark.parametrize("size", [MOBILE_PORTRAIT[0]], ids=["size0"])
def test_tags_are_the_last_row_despite_their_dom_position(list_page, size) -> None:
    """標籤在 DOM 序裡排第 3(題號、局名、**標籤**、難度、完成、星號),但視覺上
    要排在最後一行 —— 這是 `.position-tags { order: 1 }` 唯一要做的事(見
    `list.css`「合併第一、三行」一節)。

    比的是標籤欄的 `top` 是否晚於其餘五個元素裡最晚出現的那個(星號,DOM 序
    最後一個、也是第一行裡最右側的元素)的 `bottom` —— 兩者不同行,`top` 不必
    像同行比較那樣改比中心。
    """
    page = open_at(list_page, size)

    tags = box_of(page, '#positions > li[data-id="1"] .position-tags')
    star = box_of(page, '#positions > li[data-id="1"] .position-star')

    assert tags["top"] >= star["bottom"] - 1, (
        f"標籤欄(top={tags['top']:.1f})沒有排在星號(bottom={star['bottom']:.1f})"
        "之後 —— order: 1 沒有生效,標籤欄仍卡在原本的 DOM 位置"
    )


# --- 桌面回歸 --------------------------------------------------------------


def test_the_row_stays_a_grid_on_a_desktop_viewport(list_page) -> None:
    """桌面尺寸下 `.position` 仍是六欄 grid —— 斷點沒有外溢到寬螢幕。"""
    page = open_at(list_page, DESKTOP)

    display = page.evaluate(
        """() => getComputedStyle(
          document.querySelector('#positions > li[data-id="1"]')
        ).display"""
    )

    assert display == "grid", f"桌面尺寸下 .position 的 display 是 {display!r},預期 grid"
