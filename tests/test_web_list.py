"""列表頁骨架與入口路由(tasks 3.1;requirements 1.1、6.2)。

本檔問兩件事:

1. **入口位址給的是列表頁,不是棋盤**(1.1)。這是本功能最外顯的改變,也是唯一
   一條會讓「改名」真的發生的斷言。
2. **骨架備妥 3.2 會填入內容的每一個容器**,且使用者可見文字為繁體中文(6.2)。

## 為什麼入口的斷言要走真實的掛載

`service/main.py` 以 `StaticFiles(html=True)` 把 `web/` 掛在 `/`,根路徑是靠**目錄下的
`index.html`** 解析的 —— 「列表頁叫什麼名字」與「`/` 給出什麼」在這裡是同一件事。
只斷言 `web/index.html` 這個檔案裡有什麼,`html=True` 的那一段完全不會被執行到,
而那正是本任務的全部重點。因此此處一律經 `TestClient` 對真實 app 發請求。

`test_web_page.py` 同樣不進生命週期,理由一致:要斷言的是**哪個路徑由誰接手**,
那發生在路由比對階段,與引擎和題庫索引無關。

## 為什麼還要在真瀏覽器裡看一次

`TestClient` 看到的是位元組。容器是否真的成為 DOM 節點、空狀態與錯誤區是否真的
一開始就不呈現,只有瀏覽器答得出來 —— 標籤沒閉合、`hidden` 打錯位置,在字串比對
下完全看不出來。

## 篩選區刻意不存在

篩選互動已移入 Backlog(tasks.md)。骨架若先擺一組按不動的下拉選單,使用者會以為
產品壞了。`test_the_list_page_ships_no_filter_controls` 把這個決定釘住 —— 日後接上
篩選時它會轉紅,那時該連同 tasks.md 的 Backlog 一起改。
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from service.main import create_app
from test_web_page import SIMPLIFIED_ONLY_CHARACTERS

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIST_HTML = PROJECT_ROOT / "web" / "index.html"
PLAY_HTML = PROJECT_ROOT / "web" / "play.html"

#: 列表頁骨架必須備妥的容器。tasks 3.2 的 boundary 只有 `web/list.js` 與
#: `web/list.css`,**沒有一個能回頭改 `index.html`** —— 少一個容器,那個任務就無處
#: 可寫。這與 `web/play.html` 當初一次備齊全部容器的理由完全相同。
REQUIRED_ELEMENT_IDS = [
    "positions",  # 題目列表容器(3.2)
    "progress",  # 完成計數所在的一區(3.5)
    "completed-count",  # 已完成題數(3.5)
    "total-count",  # 總題數(3.5)
    "empty",  # 題庫為空時的告知(1.5)
    "error",  # 索引取不到時的提示(design 的 Error Handling)
    "retry",  # 索引取不到時「可做什麼」的落點(design 的 Error Handling)
]

#: 只有對局頁才會有的東西。列表頁出現其中任何一項,就表示 `/` 又變回棋盤了。
BOARD_ONLY_MARKERS = ['id="board"', "app.js"]


@pytest.fixture
def page_client() -> Iterator[TestClient]:
    """一個未進入生命週期的 client(理由見模組說明)。"""
    yield TestClient(create_app(), raise_server_exceptions=False)


# --- 入口是列表,不是棋盤 -----------------------------------------------


def test_the_entry_path_serves_the_list_page(page_client: TestClient) -> None:
    """1.1:`/` 給的是 `web/index.html`,而它就是列表頁。

    兩段一起斷言才有意義:只比對檔案內容,`html=True` 沒被驗到;只看回應是 HTML,
    列表頁與棋盤都成立。
    """
    assert LIST_HTML.is_file(), f"{LIST_HTML} 必須存在"

    response = page_client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.text == LIST_HTML.read_text(encoding="utf-8")


def test_the_entry_path_is_not_the_board(page_client: TestClient) -> None:
    """1.1:入口不得是任何單一題目的棋盤。

    這一條是本任務存在的理由。它會在「有人把對局頁改回 `index.html`」的那一刻轉紅,
    而檔案存在與否的斷言不會。
    """
    text = page_client.get("/").text
    found = [marker for marker in BOARD_ONLY_MARKERS if marker in text]

    assert not found, f"入口位址給的仍是對局頁:出現了 {found}"


@pytest.mark.parametrize("element_id", REQUIRED_ELEMENT_IDS)
def test_the_list_page_provides_every_container_the_later_tasks_need(
    page_client: TestClient, element_id: str
) -> None:
    """骨架備妥全部容器 —— tasks 3.2 改不到 `index.html`,少一個就沒地方寫。"""
    assert f'id="{element_id}"' in page_client.get("/").text


def test_the_list_page_wires_its_own_module_and_stylesheet(
    page_client: TestClient,
) -> None:
    """`list.js` 與 `list.css` 的 `<link>` / `<script>` 必須在此接好。

    兩者屬 tasks 3.2,此刻都還不存在,那兩個請求會是 404 —— 與 `web/play.html` 當初
    先接好 `style.css` 與 `app.js` 的情形相同。接不好的代價不對稱:3.2 的 boundary
    沒有本檔,屆時模組會**載不進來而沒有任何地方能補**。
    """
    text = page_client.get("/").text

    assert "./list.css" in text
    assert "./list.js" in text
    assert 'type="module"' in text


def test_the_list_page_ships_no_filter_controls(page_client: TestClient) -> None:
    """篩選區已移入 Backlog:骨架不得先擺一組按不動的空殼(tasks.md 的範圍說明)。"""
    text = page_client.get("/").text

    assert "<select" not in text
    assert "<input" not in text


# --- 對局頁退居 `/play.html` --------------------------------------------


def test_the_play_page_is_served_at_its_own_path(page_client: TestClient) -> None:
    """對局頁仍拿得到,只是換了位址(design 的「路由:列表接管入口」)。"""
    assert PLAY_HTML.is_file(), f"{PLAY_HTML} 必須存在"

    response = page_client.get("/play.html")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.text == PLAY_HTML.read_text(encoding="utf-8")


def test_the_play_page_still_carries_the_board(page_client: TestClient) -> None:
    """4.2 的交接契約:`/play.html?id=<題號>` 給的是那個備妥棋盤的對局頁。

    查詢字串對靜態檔沒有意義,但題號正是經由它傳入的 —— 一併請求,證明帶著它也
    拿得到頁面,而不是只有裸路徑可用。
    """
    text = page_client.get("/play.html?id=1").text

    assert 'id="board"' in text
    assert "./app.js" in text


# --- 繁體中文(6.2)-----------------------------------------------------


def test_the_list_page_declares_traditional_chinese(page_client: TestClient) -> None:
    """6.2:頁面自我宣告為繁體中文,瀏覽器的字型選擇才會正確。"""
    assert 'lang="zh-Hant"' in page_client.get("/").text


def test_the_list_page_text_contains_no_simplified_characters(
    page_client: TestClient,
) -> None:
    """6.2:所有使用者可見文字為繁體中文。"""
    text = page_client.get("/").text
    found = sorted({char for char in SIMPLIFIED_ONLY_CHARACTERS if char in text})

    assert not found, f"頁面出現簡體字:{''.join(found)}"


# --- 骨架在真實瀏覽器中確實長出這些容器 ---------------------------------


def test_the_skeleton_renders_its_containers_in_a_real_browser(browser_page) -> None:
    """骨架是可解析的 HTML,每個容器在真瀏覽器裡都查得到(3.2 的前提)。"""
    browser_page.goto(LIST_HTML.as_uri())

    missing = [
        element_id
        for element_id in REQUIRED_ELEMENT_IDS
        if browser_page.locator(f"#{element_id}").count() != 1
    ]
    assert not missing, f"這些容器在 DOM 中不存在或不唯一:{missing}"


def test_the_empty_and_error_regions_start_hidden(browser_page) -> None:
    """空狀態與錯誤提示是**條件呈現**的,骨架不得一開始就把它們攤在畫面上。

    只看 `hidden` 這個屬性在不在字串裡不夠:它可能寫在錯誤的元素上,或被樣式覆蓋。
    問瀏覽器「使用者現在看不看得到」才是 1.5 與 Error Handling 真正要的那個性質。
    """
    browser_page.goto(LIST_HTML.as_uri())

    assert browser_page.locator("#empty").is_hidden()
    assert browser_page.locator("#error").is_hidden()


def test_the_list_container_starts_empty(browser_page) -> None:
    """列表內容由 `list.js` 填入(3.2):骨架不得夾帶任何寫死的題目。"""
    browser_page.goto(LIST_HTML.as_uri())

    # 容器不存在時「沒有子節點」也會成立,先把那條退路堵掉。
    assert browser_page.locator("#positions").count() == 1
    assert browser_page.locator("#positions > *").count() == 0
