"""收題頁的位址與最小容器,以及「加了它之後產品那一側一動也沒動」(tasks 1.1;
requirements 1.1、1.3)。

本檔只問兩件事:

1. **收題頁由網址取得得到**(1.1)。收題工具刻意沒有任何入口 —— 列表頁與對局頁
   都不會連過去(1.2,由 `tests/test_web_editor_entry.py` 反向釘住),因此「網址
   打得到」是它唯一的到達方式。這條斷言若紅了,這個工具就等於不存在。
2. **既有的兩個產品頁與四個端點行為不變**(1.3)。收題頁是加在同一個靜態掛載底下
   的普通目錄,理論上碰不到任何既有路徑;但「理論上」正是需要一條斷言的地方。

## 為什麼位址要走真實的掛載

`service/main.py` 以 `StaticFiles(html=True)` 把 `web/` 掛在 `/`,目錄請求是靠**目錄
下的 `index.html`** 解析的 —— 「收題頁放在哪個目錄」與「`/editor/` 給出什麼」在這裡
是同一件事,而且這條對應**完全由檔案佈局決定,`main.py` 一個字都不必改**。只斷言
`web/editor/index.html` 這個檔案裡有什麼,`html=True` 的那一段完全不會被執行到,
而「取得得到」正是本任務的全部重點。因此此處一律經 `TestClient` 對真實 app 發請求。

## 為什麼不進生命週期

啟動掛鉤會建題庫索引並拉起真的引擎進程(每個常駐 51MB NNUE)。本檔要斷言的是
**哪個路徑由誰接手**,那發生在路由比對階段,與服務層無關 —— 與 `test_web_page.py`
同一個理由,夾具形狀也刻意相同。

四個端點因此不以正常回應當探針(那需要題庫與引擎),改問**方法不符**:405 是路由層
自己回的,靜態檔給不出這個狀態碼 —— 它對認不得的請求只會回 404。一條探針同時證明
了「這個路徑仍由端點接手」與「靜態掛載沒有把它接走」。
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from service.main import create_app
from test_web_page import SIMPLIFIED_ONLY_CHARACTERS

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_HTML = WEB_DIR / "editor" / "index.html"

#: 收題頁的位址。**目錄形式的網址**,不是 `.html` 檔名 —— 收題頁的檔案集中在
#: `web/editor/` 底下自成一區,與產品頁面互不牽動(design 的 File Structure Plan)。
EDITOR_PATH = "/editor/"


@pytest.fixture
def page_client() -> Iterator[TestClient]:
    """一個未進入生命週期的 client(理由見模組說明)。"""
    yield TestClient(create_app(), raise_server_exceptions=False)


# --- 收題頁由網址取得得到(1.1)-----------------------------------------


def test_the_editor_path_serves_the_page(page_client: TestClient) -> None:
    """1.1:收題頁的位址回的是頁面本身,而不是 404。"""
    response = page_client.get(EDITOR_PATH)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text


def test_served_page_is_the_file_in_the_editor_directory(
    page_client: TestClient,
) -> None:
    """`/editor/` 給的就是 `web/editor/index.html`,不是別處產生的內容。

    這條同時釘住檔案的**落點**:收題頁的全部檔案集中在單一目錄,搬出去或改名都會
    讓這條紅 —— 而位址是它唯一的到達方式,搬走就等於工具消失。
    """
    assert EDITOR_HTML.is_file(), f"{EDITOR_HTML} 必須存在"

    assert page_client.get(EDITOR_PATH).text == EDITOR_HTML.read_text(encoding="utf-8")


def test_the_editor_path_without_a_trailing_slash_also_arrives(
    page_client: TestClient,
) -> None:
    """少打一條斜線仍然到得了 —— 位址是唯一的入口,不該卡在這種地方。

    掛載的 `html=True` 會把目錄請求導到帶斜線的形式,此處只確認**結果**是那一頁,
    不斷言中間走了幾次轉址。
    """
    response = page_client.get("/editor", follow_redirects=True)

    assert response.status_code == 200
    assert response.text == EDITOR_HTML.read_text(encoding="utf-8")


def test_editor_page_text_contains_no_simplified_characters(
    page_client: TestClient,
) -> None:
    """8.3:所有使用者可見文字為繁體中文。字表與其餘頁面共用同一份。"""
    text = page_client.get(EDITOR_PATH).text
    found = sorted({char for char in SIMPLIFIED_ONLY_CHARACTERS if char in text})

    assert not found, f"頁面出現簡體字:{''.join(found)}"


def test_editor_page_renders_in_a_real_browser(browser_page) -> None:
    """骨架是可解析的 HTML,在真瀏覽器裡確實長出一個文件。

    `TestClient` 看到的是位元組:標籤沒閉合之類的錯誤在字串比對下完全看不出來,
    而後續任務都要往這個容器裡塞東西。
    """
    browser_page.goto(EDITOR_HTML.as_uri())

    assert browser_page.locator("html[lang='zh-Hant']").count() == 1
    assert browser_page.locator("body").count() == 1


# --- 產品那一側一動也沒動(1.3)-----------------------------------------


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("/", "index.html"),  # 題庫列表頁
        ("/play.html", "play.html"),  # 對局頁
    ],
)
def test_the_product_pages_are_unchanged(
    page_client: TestClient, path: str, source: str
) -> None:
    """1.3:收題頁加進同一個掛載之後,兩個產品頁給的仍是原來那個檔案。

    新目錄與它們是平行的兄弟,理論上互不相干 —— 但比對順序與 `html=True` 的目錄
    解析都在同一個掛載裡,「理論上」正是需要一條斷言的地方。
    """
    assert page_client.get(path).text == (WEB_DIR / source).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/catalog"),
        ("POST", "/api/positions/1"),
        ("GET", "/api/state"),
        ("GET", "/api/black-move"),
    ],
)
def test_the_four_existing_endpoints_are_still_routed(
    page_client: TestClient, method: str, path: str
) -> None:
    """1.3:四個既有端點仍由路由層接手,沒有一個被靜態檔吃掉。

    以方法不符當探針的理由見模組說明:405 只有路由層答得出來,靜態檔對認不得的
    請求一律是 404。
    """
    response = page_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 405
    assert "detail" in response.json()
