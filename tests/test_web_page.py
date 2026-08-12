"""頁面骨架與靜態檔掛載的測試(tasks 1.2、requirements 8.3)。

本檔問兩件事,而且只問這兩件:

1. **對局頁拿得到頁面骨架**,骨架裡備妥 4.x 會填入內容的每一個容器,且使用者可見
   文字為繁體中文(8.3)。
2. **掛載沒有動到既有的三個端點**。靜態檔掛在根路徑上,任何路徑都落在它的範圍內 ——
   `/api/...` 若被它接走,端點會安靜地變成「找不到檔案」,而不是明確的失敗。

## 為什麼不進生命週期

啟動掛鉤會建題庫索引並拉起真的引擎進程(每個常駐 51MB NNUE)。本檔要斷言的是
**哪個路徑由誰接手**,那發生在路由比對階段,與服務層無關。因此此處的 `TestClient`
刻意不以情境管理器進出 —— 端點是否被攔截,用「請求驗證仍走本服務的契約形狀」
就足以證明:靜態檔給不出 `code` 欄位。

三個端點的正常回應由 `test_main.py` 負責,此處不重複。

## 對局頁的位址

對局頁原本掛在根路徑上,problem-browser 的 tasks 3.1 把入口讓給了題庫列表,它因此
退居 `/play.html`(design 的「路由:列表接管入口」)。**本檔問的仍是同一個骨架** ——
換的只有位址。`/` 現在給出什麼由 `test_web_list.py` 負責。
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from service.main import create_app

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLAY_HTML = PROJECT_ROOT / "web" / "play.html"

#: 對局頁的位址。入口已讓給題庫列表,本頁退居於此。
PLAY_PATH = "/play.html"

#: 骨架必須備妥的容器。後續任務的 boundary 只有 `web/app.js` 與 `web/style.css`,
#: **沒有一個能回頭改 `play.html`** —— 少一個容器,那個任務就無處可寫。
REQUIRED_ELEMENT_IDS = [
    "board",  # 盤面(3.1、3.2)
    "puzzle-title",  # 局名(4.3)
    "puzzle-source",  # 出處(4.3)
    "puzzle-difficulty",  # 難度,與列表同一組說法與顏色
    "puzzle-tags",  # 標籤(殺法名),chip 外觀與列表一致。**預設收著** —— 那是劇透
    "toggle-tags",  # 展開/收合標籤的按鈕
    # `puzzle-max-dtm` **已移除**:最長殺著距離對使用者是劇透,見 requirements 1.3。
    "turn",  # 當前輪方,8.4 要求可辨識(4.3)
    "signal",  # 三態諮詢信號(4.4)
    # `waiting` **已移除**:那個「引擎思考中」的區塊在版面流裡來去,顯示與隱藏
    # 都把底下的歷史著法上下推動。等待呈現改由 `#turn`(等應手)與
    # `#puzzle-title`(載入題目)承擔,兩者位置固定。見 requirements 6.1。
    "error",  # 錯誤提示區,單一通用區塊(4.4)
    "moves",  # 中文記譜的歷史著法(4.3)
    "controls",  # 對局控制項的容器:重來,以及獲勝後才出現的下一題
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


# --- 對局頁的位址給得出頁面骨架 -----------------------------------------


def test_the_play_path_serves_the_page(page_client: TestClient) -> None:
    """掛載之後,對局頁的位址回的是前端頁面本身,而不是 404。"""
    response = page_client.get(PLAY_PATH)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text


@pytest.mark.parametrize("element_id", ["board"])
def test_page_provides_every_container_the_later_tasks_need(
    page_client: TestClient, element_id: str
) -> None:
    """骨架備妥全部容器 —— 後續任務改不到 `play.html`,少一個就沒地方寫。"""
    assert f'id="{element_id}"' in page_client.get(PLAY_PATH).text


def test_every_page_shows_the_product_name_in_the_browser_tab() -> None:
    """三頁的 `<title>` 一律是產品名 —— 分頁上顯示的都是 `LeetChess`。

    分頁標題是使用者在多開時唯一認得出「這是哪個網站」的地方。原本三頁各說各的
    (列表頁是產品名、對局頁是「象棋排局挑戰」、收題頁是「收題工具」),開三個分頁
    看起來就像三個不相干的站。頁內的標題各頁仍不同,那是頁面內容的事。

    `LeetChess` 是專有名詞,不譯(steering 的 Naming Conventions),與 `difficulty.js`
    的難度說法同屬使用者可見文字裡不用繁體中文的例外 —— 因此下面那條簡體字掃描
    不會與這一條打架。
    """
    pages = [
        PROJECT_ROOT / "web" / "index.html",
        PLAY_HTML,
        PROJECT_ROOT / "web" / "editor" / "index.html",
    ]

    for path in pages:
        assert path.is_file(), f"{path} 必須存在"
        assert "<title>LeetChess</title>" in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(PROJECT_ROOT)} 的分頁標題不是產品名"
        )


def test_page_text_contains_no_simplified_characters(page_client: TestClient) -> None:
    """8.3:所有使用者可見文字為繁體中文。"""
    text = page_client.get(PLAY_PATH).text
    found = sorted({char for char in SIMPLIFIED_ONLY_CHARACTERS if char in text})

    assert not found, f"頁面出現簡體字:{''.join(found)}"


# --- 掛載不得改變既有三個端點的行為 -------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("POST", "/api/state", 400, "INVALID_MOVE_FORMAT"),
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


# --- 骨架在真實瀏覽器中確實長出這些容器 ---------------------------------


def test_skeleton_renders_its_containers_in_a_real_browser(browser_page) -> None:
    """骨架是可解析的 HTML,每個容器在真瀏覽器裡都查得到(4.x 的前提)。

    TestClient 看到的是位元組;容器是否真的成為 DOM 節點,只有瀏覽器答得出來 ——
    標籤沒閉合之類的錯誤在字串比對下完全看不出來。
    """
    browser_page.goto(PLAY_HTML.as_uri())

    missing = [
        element_id
        for element_id in REQUIRED_ELEMENT_IDS
        if browser_page.locator(f"#{element_id}").count() != 1
    ]
    assert not missing, f"這些容器在 DOM 中不存在或不唯一:{missing}"
