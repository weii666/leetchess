"""產品頁面沒有任何通往收題頁的入口(task 6.3;requirement 1.2)。

收題頁是**題庫維護者的工具,不是產品的一部分**。它會請求本機目錄的寫入授權、會把
檔案整份覆寫,而產品的使用者是來下棋的 —— 一個他點得到的入口,最好的情況是讓他
看到一頁看不懂的表單,最壞的情況是他真的按下了那個要求授權的按鈕。

1.1 已經確立收題頁的位址完全由檔案位置決定(`StaticFiles(html=True)` 直接接手),
所以「不加入口」不是靠後端遮蔽,而是靠**產品頁面不去提它**。這種約束沒有任何機制
守得住 —— 它只是一個「大家記得別加」的約定,而約定會在半年後某個順手的下午破功。
本檔就是那個機制。

## 兩層檢查,各自抓不同的破法

**一、算完之後的 DOM。** 題庫列表頁的每一列都是 `list.js` 依索引畫出來的,對局頁的
盤面同理,所以靜態檔裡沒有的東西不代表畫面上沒有。這一層問的是使用者眼前那一份
文件:任何屬性值提到收題頁的位址、或任何看得見的字提到「收題」,都算入口。

**二、交付出去的原始碼。** DOM 那一層只看得到本檔真的走過的那些狀態 —— 一個藏在
「維護者模式」分支裡的連結完全躲得過。這一層因此直接掃產品交付的每一個檔案,不管
那段程式碼這次有沒有執行到。

兩層都不完美,但破法不同:第一層抓得到動態生成的,第二層抓得到沒被執行到的。

## 為什麼掃的是位址而不是「收題」兩個字

`list.js`、`app.js` 與 `list.css` 的註解裡都有「收題」——那是動詞(「題庫按局號收題」),
與收題頁無關。掃那兩個字會讓一份完全正確的程式碼轉紅,而假警報的代價是下一個人
學會忽略這個測試。

收題頁的位址是 `/editor/`,任何指向它的連結都必然帶著 `editor` 加一個斜線或位於路徑
開頭 —— 掃這兩種寫法既抓得到 `href="/editor/"`,也抓得到 `../editor/index.html`,而且
不會誤傷任何一句中文註解。
"""

from __future__ import annotations

import json
import pathlib
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_DIR = WEB_DIR / "editor"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-entry.test"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

#: 收題頁位址在原始碼裡看得出來的兩種寫法。
#:
#: `editor/` 抓相對與絕對的連結(`/editor/`、`../editor/index.html`);`/editor` 另外
#: 抓不帶尾斜線的那一種 —— `StaticFiles(html=True)` 對它會轉址,所以它一樣是入口。
EDITOR_URL_FORMS = ("editor/", "/editor")

#: 題庫索引的一份最小內容 —— 列表頁靠它畫出每一列。
#:
#: 兩題而不是一題:列表頁若把入口放在「每一列後面」那種地方,一題也看得出來,但
#: 兩題才問得出「是不是每一列都乾淨」。
CATALOG = {
    "positions": [
        {
            "id": 1,
            "title": "獨卒擒王",
            "description": "適情雅趣 第一局 獨卒擒王",
            "difficulty": 1,
            "tags": ["小兵"],
            "source": "適情雅趣",
        },
        {
            "id": 26,
            "title": "患在几席",
            "description": "適情雅趣 第二六局 患在几席",
            "difficulty": 2,
            "tags": ["鐵門栓"],
            "source": "適情雅趣",
        },
    ]
}

#: 對局頁載入一題時所需的回應形狀(`GET /api/positions/{id}`)。
POSITION = {
    "id": 1,
    "title": "獨卒擒王",
    "description": "適情雅趣 第一局 獨卒擒王",
    "fen": "4k4/9/9/9/9/9/9/9/4P4/4K4 w - - 0 1",
    "difficulty": 1,
    "tags": ["小兵"],
    "source": "適情雅趣",
    "state": {
        "side_to_move": "red",
        "legal_moves": ["e2e3"],
        "over": False,
        "winner": None,
    },
}


@pytest.fixture
def product_page(browser_page) -> Iterator:
    """一個備妥靜態路由與兩個產品端點的分頁。

    **收題頁的檔案照樣供得出來** —— 本檔要問的不是「拿不拿得到收題頁」(1.1 已經
    確立它拿得到),而是「產品頁面有沒有指向它」。把它擋掉會讓這裡的斷言變成一句
    廢話:一個 404 的連結仍然是一個入口。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path

        if path == "/api/catalog":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(CATALOG),
            )
            return

        if path.startswith("/api/positions/"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(POSITION),
            )
            return

        target = WEB_DIR / ("index.html" if path == "/" else path.lstrip("/"))
        if target.is_file():
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES.get(target.suffix, "text/plain; charset=utf-8"),
                body=target.read_text(encoding="utf-8"),
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    yield browser_page


def attributes_pointing_at_the_editor(page) -> list[str]:
    """畫面上每一個屬性值提到收題頁位址的節點。

    掃的是**全部屬性**而不只是 `href`:入口不一定長成一個連結,`data-href` 加一個
    click 處理器、`formaction`、`iframe` 的 `src` 都是入口,而它們的共同點是那個
    位址一定出現在某個屬性值裡。
    """
    return page.evaluate(
        """forms => {
          const found = [];
          for (const element of document.querySelectorAll('*')) {
            for (const attribute of element.attributes) {
              if (forms.some(form => attribute.value.includes(form))) {
                found.push(`<${element.tagName.toLowerCase()} ${attribute.name}="${attribute.value}">`);
              }
            }
          }
          return found;
        }""",
        list(EDITOR_URL_FORMS),
    )


def visible_text(page) -> str:
    """畫面上看得見的字。

    `innerText` 不含 `<script>` 與 `<style>` 的內容,所以那三個檔案註解裡的「收題」
    兩個字不會流進來 —— 那正是這一層問得起「收題」的原因。
    """
    return page.evaluate("() => document.body.innerText")


def delivered_product_files() -> list[pathlib.Path]:
    """產品交付出去的每一個檔案 —— `web/` 底下,但**不含** `web/editor/`。

    以「目錄」而不是一份寫死的清單劃界:日後新增一個 `web/ranking.js` 會自動落進
    這個檢查,而一份寫死的清單只會保護寫下它的那一天。
    """
    files = [
        path
        for path in sorted(WEB_DIR.rglob("*"))
        if path.is_file()
        and path.suffix in {".html", ".js", ".css"}
        and EDITOR_DIR not in path.parents
    ]
    assert files, "產品檔案一個都沒掃到 —— 這個檢查等於沒跑"
    return files


# --- 算完之後的 DOM ------------------------------------------------------


def test_the_problem_list_has_no_way_into_the_editor(product_page) -> None:
    """1.2:題庫列表頁的 DOM 中不存在任何指向收題頁的連結或入口。

    列表頁是產品的入口頁(problem-browser 的 tasks 3.1),也是最容易被順手加上一個
    「收題」按鈕的地方 —— 維護者自己每天都會開它。
    """
    product_page.goto(f"{ORIGIN}/")
    # 等到列真的畫出來 —— 每一列由 `list.js` 依索引生成(`li.position` 帶 `data-id`),
    # 而動態生成的內容正是這一層要看的東西。
    product_page.wait_for_selector("li.position[data-id]", timeout=5000)

    found = attributes_pointing_at_the_editor(product_page)
    assert found == [], f"題庫列表頁上有通往收題頁的入口:{found}"
    assert "收題" not in visible_text(product_page), "題庫列表頁上有一段提到收題的字"


def test_the_play_page_has_no_way_into_the_editor(product_page) -> None:
    """1.2 的另一半:對局頁同樣不存在任何入口。

    對局頁是使用者停留最久的一頁,而它已經有一組控制項(認輸、重來、回列表)——
    多一顆按鈕在那一排裡最不容易被注意到。
    """
    product_page.goto(f"{ORIGIN}/play.html?id=1")
    product_page.wait_for_selector("#board .piece", timeout=5000)

    found = attributes_pointing_at_the_editor(product_page)
    assert found == [], f"對局頁上有通往收題頁的入口:{found}"
    assert "收題" not in visible_text(product_page), "對局頁上有一段提到收題的字"


# --- 交付出去的原始碼 ----------------------------------------------------


def test_no_product_file_mentions_the_editor_address() -> None:
    """產品交付的每一個檔案裡都找不到收題頁的位址。

    這一層抓的是 DOM 那一層抓不到的東西:一個藏在沒被執行到的分支裡的連結。它也
    順帶守住了另一件事 —— 產品頁面**不匯入** `web/editor/` 底下的任何模組,因為那
    同樣要寫出這個位址。

    掃的是位址而不是「收題」兩個字,理由見檔首:那兩個字在註解裡是動詞。
    """
    offenders = {}
    for path in delivered_product_files():
        text = path.read_text(encoding="utf-8")
        hits = [form for form in EDITOR_URL_FORMS if form in text]
        if hits:
            offenders[path.relative_to(PROJECT_ROOT).as_posix()] = hits

    assert offenders == {}, (
        f"產品檔案裡出現了收題頁的位址:{offenders}。"
        "收題頁不在產品的導覽動線上(requirement 1.2)"
    )


def test_the_editor_page_itself_is_not_what_this_file_forbids() -> None:
    """對照組:收題頁自己當然寫得出那個位址,不然它連載入自己的模組都做不到。

    沒有這一條的話,上面那個檢查即使把整個 `web/` 掃進去(包含收題頁)也照樣會綠 ——
    而那樣的檢查連「收題頁存在」都證明不了。這一條釘住的是**分界畫在哪裡**:
    禁止的是產品檔案提它,不是任何檔案都不准提。
    """
    assert (EDITOR_DIR / "index.html").is_file(), "收題頁不見了"
    assert EDITOR_DIR not in {path.parent for path in delivered_product_files()}, (
        "收題頁的檔案被算成了產品檔案 —— 上面那個檢查會擋下收題頁自己的模組匯入"
    )
