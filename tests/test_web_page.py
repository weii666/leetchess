"""頁面骨架與靜態檔掛載的測試(tasks 1.2、requirements 8.3)。

本檔問兩件事,而且只問這兩件:

1. **根路徑拿得到頁面骨架**,骨架裡備妥 4.x 會填入內容的每一個容器,且使用者可見
   文字為繁體中文(8.3)。
2. **掛載沒有動到既有的三個端點**。靜態檔掛在根路徑上,任何路徑都落在它的範圍內 ——
   `/api/...` 若被它接走,端點會安靜地變成「找不到檔案」,而不是明確的失敗。

## 為什麼不進生命週期

啟動掛鉤會建題庫索引並拉起真的引擎進程(每個常駐 51MB NNUE)。本檔要斷言的是
**哪個路徑由誰接手**,那發生在路由比對階段,與服務層無關。因此此處的 `TestClient`
刻意不以情境管理器進出 —— 端點是否被攔截,用「請求驗證仍走本服務的契約形狀」
就足以證明:靜態檔給不出 `code` 欄位。

三個端點的正常回應由 `test_main.py` 負責,此處不重複。
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from service.main import create_app

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"

#: 骨架必須備妥的容器。後續任務的 boundary 只有 `web/app.js` 與 `web/style.css`,
#: **沒有一個能回頭改 `index.html`** —— 少一個容器,那個任務就無處可寫。
REQUIRED_ELEMENT_IDS = [
    "board",  # 盤面(3.1、3.2)
    "puzzle-title",  # 局名(4.3)
    "puzzle-source",  # 出處(4.3)
    "puzzle-max-dtm",  # 最長殺著距離(4.3)
    "turn",  # 當前輪方,8.4 要求可辨識(4.3)
    "signal",  # 三態諮詢信號(4.4)
    "waiting",  # 引擎思考中的等待狀態(4.4)
    "error",  # 錯誤提示區,單一通用區塊(4.4)
    "moves",  # 中文記譜的歷史著法(4.3)
    "reset",  # 重來(4.3)
]

#: 只存在於簡體的字。頁面詞彙(載入、題目、歷史著法、錯誤、重來、勝負)一旦寫成
#: 簡體就會命中其中之一。刻意不收 `后`、`只`、`面` 這類兩邊都合法的字,避免誤報。
SIMPLIFIED_ONLY_CHARACTERS = (
    "来历号难标记误题长载关应战单显现进动资讯错选点对盘胜负开为个时说请图"
)


@pytest.fixture
def page_client() -> Iterator[TestClient]:
    """一個未進入生命週期的 client(理由見模組說明)。"""
    yield TestClient(create_app(), raise_server_exceptions=False)


# --- 根路徑給得出頁面骨架 -----------------------------------------------


def test_root_path_serves_the_page(page_client: TestClient) -> None:
    """掛載之後,根路徑回的是前端頁面本身,而不是 404。"""
    response = page_client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text


def test_served_page_is_the_file_in_the_web_directory(page_client: TestClient) -> None:
    """根路徑給的就是 `web/index.html`,不是別處產生的內容。"""
    assert INDEX_HTML.is_file(), f"{INDEX_HTML} 必須存在"

    assert page_client.get("/").text == INDEX_HTML.read_text(encoding="utf-8")


@pytest.mark.parametrize("element_id", REQUIRED_ELEMENT_IDS)
def test_page_provides_every_container_the_later_tasks_need(
    page_client: TestClient, element_id: str
) -> None:
    """骨架備妥全部容器 —— 後續任務改不到 `index.html`,少一個就沒地方寫。"""
    assert f'id="{element_id}"' in page_client.get("/").text


def test_page_declares_traditional_chinese(page_client: TestClient) -> None:
    """8.3:頁面自我宣告為繁體中文,瀏覽器的字型選擇才會正確。"""
    assert 'lang="zh-Hant"' in page_client.get("/").text


def test_page_text_contains_no_simplified_characters(page_client: TestClient) -> None:
    """8.3:所有使用者可見文字為繁體中文。"""
    text = page_client.get("/").text
    found = sorted({char for char in SIMPLIFIED_ONLY_CHARACTERS if char in text})

    assert not found, f"頁面出現簡體字:{''.join(found)}"


# --- 掛載不得改變既有三個端點的行為 -------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/api/positions/abc", 404, "POSITION_NOT_FOUND"),
        ("POST", "/api/state", 400, "INVALID_MOVE_FORMAT"),
        ("POST", "/api/black-move", 400, "INVALID_MOVE_FORMAT"),
    ],
)
def test_the_static_mount_does_not_intercept_the_api_endpoints(
    page_client: TestClient, method: str, path: str, status: int, code: str
) -> None:
    """三個端點仍由自己的路由接手,回的仍是 `{code, message}` 契約形狀(5.1)。

    以請求驗證失敗當探針,是因為它發生在**路由比對之後、服務層之前**:類別碼
    出現就證明請求走進了端點,而靜態檔永遠給不出 `code` 這個欄位。
    """
    response = page_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/state", 405),
        ("GET", "/api/black-move", 405),
        ("POST", "/api/positions/1", 405),
        ("GET", "/api/no-such-route", 404),
    ],
)
def test_route_level_failures_keep_the_framework_native_shape(
    page_client: TestClient, method: str, path: str, status: int
) -> None:
    """路由層的 404 與 405 仍是框架原生的 `{"detail": ...}`(`main.py` 的模組說明)。

    這是前端 `api.js` 要辨識的**第二種**錯誤形狀(design 的 api.js 一節)。掛載若把
    這些路徑接走,405 會退化成「找不到檔案」,前端就再也分不出「網址打錯」與
    「方法用錯」。
    """
    response = page_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == status
    assert "detail" in response.json()


# --- 骨架在真實瀏覽器中確實長出這些容器 ---------------------------------


def test_skeleton_renders_its_containers_in_a_real_browser(browser_page) -> None:
    """骨架是可解析的 HTML,每個容器在真瀏覽器裡都查得到(4.x 的前提)。

    TestClient 看到的是位元組;容器是否真的成為 DOM 節點,只有瀏覽器答得出來 ——
    標籤沒閉合之類的錯誤在字串比對下完全看不出來。
    """
    browser_page.goto(INDEX_HTML.as_uri())

    missing = [
        element_id
        for element_id in REQUIRED_ELEMENT_IDS
        if browser_page.locator(f"#{element_id}").count() != 1
    ]
    assert not missing, f"這些容器在 DOM 中不存在或不唯一:{missing}"


def test_reset_control_is_a_button_labelled_in_traditional_chinese(
    browser_page,
) -> None:
    """重來是可按的控制項而非純文字,標籤為繁體中文(8.3)。"""
    browser_page.goto(INDEX_HTML.as_uri())

    reset = browser_page.locator("#reset")
    assert reset.evaluate("element => element.tagName") == "BUTTON"
    assert reset.inner_text().strip() == "重來"
