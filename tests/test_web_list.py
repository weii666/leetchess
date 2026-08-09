"""列表頁骨架、入口路由、列表的呈現與完成標記,以及自列表進入對局
(tasks 3.1、3.2、4.1;requirements 1.1、1.2、1.3、1.5、3.1、3.2、3.3、3.5、4.1、
4.2、6.2、6.3)。

前半(3.1)問兩件事:

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

後半(3.2)驗證 `web/list.js` 與 `web/list.css`:列畫得對不對、完成標記按不按得動、
空狀態與錯誤狀態有沒有被混為一談。

## 為什麼 3.2 的測試要合成一份多題索引

題庫目前**只有 1 題**,對「列表長什麼樣」完全測不出東西 —— 一列的順序、篩不篩得掉
不可解的題目、計數會不會更新,全都要多題才看得出來。因此 3.2 的每一條都以
`page.route()` 攔下 `/api/catalog` 供一份合成索引,而受測的仍是 `web/` 底下的
**真實交付檔**(`index.html` / `list.js` / `list.css` 一個字都沒被替換)。

合成索引的題號**刻意有缺口**(1、2、3、5):題庫按局號收題,收到哪一局就是哪一局,
中間跳號是常態。列不得以位置推題號,「下一題」也不得寫成 `id + 1`。

## 出處與描述是「拿了不畫」

`/api/catalog` 與 `catalog.js` 一直都帶著 `source` 與 `description`(日後收錄第二本書
時列表要靠它們;見 tasks.md 的 Backlog),但列表**不畫**這兩個欄位:列是掃視用的。
合成索引裡的描述與出處因此刻意與局名、標籤沒有任何共同字串,「有沒有畫出去」才驗
得出來。

出處在**對局介面**畫出來(4.5),描述則**兩頁都不畫** —— 4.2 曾把它加進對局介面,
使用者看過實際畫面後拿掉了:真實題庫的描述是「出處 + 局號 + 局名」的串接,與 `h1`
的局名和它下面那一行的出處完全重複。同一份夾具因此同時是「不得出現在列表上」與
「不得出現在對局介面上」的來源。

## 4.1 為什麼連對局頁的回應也要合成

「載入的**確實是那一題**」只有在題目端點按題號給出**不同的**局名時才驗得出來 ——
題庫今天只有 1 題,不論交接對不對,點下去看到的都是那一題。因此 4.1 那一節讓
`/api/positions/{id}` 依題號回同一份索引夾具裡的那一題,再比對對局頁顯示的局名。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from service.main import create_app
from test_web_page import SIMPLIFIED_ONLY_CHARACTERS
from test_web_play import PUZZLE_FEN, START_LEGAL, black_reply, click_square, text_of

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
LIST_HTML = WEB_DIR / "index.html"
PLAY_HTML = WEB_DIR / "play.html"

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


# =======================================================================
# tasks 3.2:呈現列表與完成標記
# =======================================================================

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
#: 與 `test_web_catalog.py` / `test_web_progress.py` 刻意不同名:同名會共用 origin,
#: 也就共用 localStorage,而本節有幾條測試靠的正是那份儲存區。
ORIGIN = "https://problem-list.test"

#: 完成狀態的儲存鍵(`web/progress.js` 的 `STORAGE_KEY`)。
STORAGE_KEY = "leetchess:v1:completed"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

#: 合成的 `GET /api/catalog` 回應內容(形狀取自 `service/main.py` 的 `read_catalog`)。
#:
#: 四題涵蓋了本節要分辨的每一種情形:
#:
#: - 難度有 3、3、5、**0** 三種值;標籤「連將殺」橫跨多題,第 1 題帶兩個標籤
#:   —— 四題裡有兩題(5 與 0)**落在 schema 的 1–3 之外**,退路因此在每一條
#:   用到這份夾具的測試裡都被走到,不必特地去湊
#: - **題號有缺口**(1、2、3、5):第 4 局還沒收。「下一題」若寫成 `id + 1`,
#:   在第 3 題就會指到一個不存在的題目
#: - 第 5 題**沒有標籤**,那一欄的佔位路徑因此也會被走到
#: - **描述與出處與局名、標籤沒有任何共同字串** —— 列表若把它們畫出去,一比對就抓得到
#:
#: **難度 0 是刻意的。** `service/positions.py` 的 `_read_int` 對 difficulty 沒有下界,
#: 0 收得進來;呈現層若以 falsy 而非 `!= null` 判斷,那一題的難度會憑空變成佔位符號。
#: tasks 2.1 已經在同一個陷阱上被抓過一次(`difficulty: 0` 的篩選),這裡不再重蹈。
CATALOG: list[dict[str, Any]] = [
    {
        "id": 1,
        "title": "盡善克終",
        "description": "紅先勝,以雙馬盤旋收官",
        "difficulty": 3,
        "tags": ["雙馬", "連將殺"],
        "source": "適情雅趣",
    },
    {
        "id": 2,
        "title": "車馬冷著",
        "description": "紅先勝,冷著取勢",
        "difficulty": 3,
        "tags": ["連將殺"],
        "source": "適情雅趣",
    },
    {
        "id": 3,
        "title": "棄子入局",
        "description": "紅先勝,棄子搶攻",
        "difficulty": 5,
        "tags": ["連將殺", "鬥快"],
        "source": "橘中秘",
    },
    # 第 4 局還沒收 —— 題號的缺口是刻意的,見上方說明。
    {
        "id": 5,
        "title": "一子解雙征",
        "description": "紅先勝,入門的單子解圍",
        # 難度 0:合法的下界,不得被 falsy 判斷吃掉。
        "difficulty": 0,
        # 沒有標籤 —— 那一欄的佔位路徑。
        "tags": [],
        "source": "橘中秘",
    },
]

#: 索引裡的題號,順序即索引的順序。索引有幾題就列幾題 —— 列表不做任何過濾。
LISTED_IDS = ["1", "2", "3", "5"]

#: 總題數。由 `LISTED_IDS` 導出,夾具增減題目時不必逐條改斷言。
TOTAL = str(len(LISTED_IDS))

#: 題號到局名的對照,供無障礙名稱的斷言使用。
TITLES = {str(entry["id"]): entry["title"] for entry in CATALOG}

#: 不得出現在列表上的字串:每一題的描述,以及兩個出處。
NEVER_ON_THE_LIST = [entry["description"] for entry in CATALOG] + [
    "適情雅趣",
    "橘中秘",
]

#: 難度分級到說法的對照(`.kiro/steering/structure.md` 的題目 schema:1–3)。
#: 列表上的難度是**一個詞**而非數字 —— 「Hard」比「難度 3」少一次心算。
#:
#: **說法是英文,那是 steering 明列的例外**(其餘使用者可見文字一律繁體中文)。
#: 同一列的標籤也是中文詞,難度又是無底色的彩色字,兩者若同語言就只剩顏色分得開。
DIFFICULTY_LABELS = {1: "Easy", 2: "Medium", 3: "Hard"}


def difficulty_text(value: Any) -> str:
    """某個難度值在列上該顯示的字。

    合法分級顯示說法,其餘一律**原樣顯示**(`None` 顯示佔位符號)—— 這是超範圍值的
    退路,而不是「認不得就不畫」。
    """
    if value in DIFFICULTY_LABELS:
        return DIFFICULTY_LABELS[value]
    return "—" if value is None else str(value)


#: 一份形狀認不得的索引回應 —— 對 `catalog.js` 而言是失敗,不是「題庫沒有題目」。
BROKEN_INDEX = {"items": []}

#: 索引端點的路徑(`service/main.py` 的 `read_catalog`)。
CATALOG_PATH = "/api/catalog"

#: 列表頁與對局頁**共用**的自訂屬性。兩份樣式表各自宣告一遍(design 把 `list.css`
#: 的依賴列為「無」,而 `index.html` 只掛 `list.css`),因此漂移只能靠比對抓。
#:
#: 只比對 `document.body` 的三個屬性是不夠的:那樣只有 `--page-bg`、`--text` 與
#: `font-family` 會轉紅,`--accent`(完成標記的強調色兼計數的顏色,也最可能被單獨
#: 調整的那一個)漂了完全沒人知道。
SHARED_CUSTOM_PROPERTIES = [
    "--page-bg",
    "--panel-bg",
    "--panel-bg-strong",
    "--text",
    "--text-muted",
    "--text-dim",
    "--accent",
    "--line",
    "--gap",
    "--radius",
]


# --- 3.2 的夾具與呼叫工具 -----------------------------------------------
#
# 列的結構契約(由 `web/list.js` 產生、`web/list.css` 據以上色):
#
#   <li class="position" data-id="<題號>" [data-completed]>
#     <span class="position-id">                          <- 「<題號>.」,右對齊
#     <a class="position-title" href="./play.html?id=<題號>">
#     <span class="position-tags">                         <- 靠右對齊
#     <span class="position-difficulty" [data-level="1|2|3"]>
#     <input type="checkbox" class="position-toggle">
#
# **DOM 順序即左到右的欄序。** 難度緊鄰完成標記(不在局名旁邊):標籤是全列最不
# 可預測的一項,夾在中間,右側那兩欄才連成固定的兩直欄。
#
# `data-id` 是列與題號的對應,`data-completed` 是完成狀態的呈現掛勾,`data-level`
# 是難度標籤的上色掛勾;三者都不是測試專用的鉤子,列表本身要靠它們才畫得出樣式與
# 導航(4.1)。
#
# **完成標記排在最後,而且是 `<li>` 的直接子節點** —— 它與 `<a>` 平行而非在其中,
# 「按標記不會跳進對局頁」(4.1)因此是結構的結果。欄序改過一次(標記自最左移到
# 最右,使用者看過實際畫面後的意見),改的是 `append` 的順序而不是 CSS 的 `order`:
# 純視覺搬移會讓 Tab 順序與眼睛看到的分家。


@pytest.fixture
def list_page(browser_page) -> Iterator:
    """一個備妥靜態路由、**但尚未導覽**的分頁。

    刻意不在夾具裡導覽:錯誤狀態與預先寫好的完成標記都必須在頁面開始跑 `list.js`
    **之前**就布置好。

    `/api/**` 一律不在此處理(落到最後那條 404),由各測試自行註冊 —— Playwright
    後註冊者優先。

    等待上界壓到 5 秒:所有內容都是本機檔案與攔截下來的回應,慢不到哪裡去,而
    Playwright 預設的 30 秒會讓「畫面根本沒畫出來」這種退化變成漫長的卡頓而非明確
    的紅燈(tasks 2.1 的 review 記下的同一件事)。
    """
    browser_page.set_default_timeout(5000)

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        target = WEB_DIR / ("index.html" if path == "/" else path.lstrip("/"))
        if target.is_file():
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES.get(
                    target.suffix, "text/plain; charset=utf-8"
                ),
                body=target.read_text(encoding="utf-8"),
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    yield browser_page


def route_catalog(page, bodies: list[Any]) -> dict[str, int]:
    """讓索引端點依序回 `bodies`;用完之後一直重複最後一項。

    回傳一個記著它被打了幾次的計數器 —— 重試能不能算數,取決於**有沒有真的再發
    一次請求**,光看畫面變成什麼樣分不出「重試中」與「原地清掉告知」。
    """
    served = {"count": 0}

    def handler(route) -> None:
        body = bodies[min(served["count"], len(bodies) - 1)]
        served["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=body if isinstance(body, str) else json.dumps(body),
        )

    page.route(f"{ORIGIN}/api/**", handler)
    return served


def hold_catalog(page) -> dict[str, Any]:
    """收下索引請求但**先不回話**,把 route 交給測試自己決定何時放行。

    這是觀察「索引還沒回來」那段時間唯一穩定的方式 —— 載入中的畫面是個轉瞬即逝的
    狀態,靠時序去搶會 flaky,把回應扣在手上則要多久有多久。
    """
    held: dict[str, Any] = {}

    def handler(route) -> None:
        held["route"] = route

    page.route(f"{ORIGIN}/api/**", handler)
    return held


def wait_for_held_request(page, held: dict[str, Any]) -> None:
    """等到索引請求真的送出來為止(此時載入中的畫面已經畫好)。"""
    for _ in range(100):
        if "route" in held:
            return
        page.wait_for_timeout(50)
    raise AssertionError("索引端點從未收到請求")


def release(held: dict[str, Any], body: Any) -> None:
    """放行先前扣住的索引請求。"""
    held["route"].fulfill(
        status=200,
        content_type="application/json",
        body=body if isinstance(body, str) else json.dumps(body),
    )


def catalog_of(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """索引端點的回應主體。"""
    return {"positions": positions}


def open_list(page, positions: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """開啟列表頁並等到列畫出來。"""
    served = route_catalog(
        page, [catalog_of(CATALOG if positions is None else positions)]
    )
    page.goto(f"{ORIGIN}/")
    page.wait_for_selector("#positions > li")
    return served


def open_empty_list(page) -> None:
    """開啟一個題庫真的沒有題目的列表頁,等到空狀態出現。"""
    route_catalog(page, [catalog_of([])])
    page.goto(f"{ORIGIN}/")
    page.wait_for_selector("#empty:not([hidden])")


def open_broken_list(page, bodies: list[Any] | None = None) -> dict[str, int]:
    """開啟一個索引壞掉的列表頁,等到錯誤區出現。"""
    served = route_catalog(page, bodies if bodies is not None else [BROKEN_INDEX])
    page.goto(f"{ORIGIN}/")
    page.wait_for_selector("#error:not([hidden])")
    return served


def rows(page) -> list[dict[str, Any]]:
    """畫出來的每一列:題號、可見文字、完成標記的勾選狀態、完成的呈現掛勾。"""
    return page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => ({
          id: li.dataset.id ?? null,
          text: li.innerText,
          checked: li.querySelector('input[type="checkbox"]')?.checked ?? null,
          marked: li.matches('[data-completed]'),
        }))"""
    )


def open_list_expecting_rows(page, positions: list[dict[str, Any]]) -> None:
    """開啟列表,並在列**根本沒畫出來**時說出是哪一份資料害的。

    `open_list` 的等待逾時收場只會說「在等 `#positions > li`」—— 而「一筆認不得的
    難度讓整份列表消失」正是本節要抓的失效模式,那句話說不出成因。這裡把它翻成一句
    自己的斷言,順帶把當下的錯誤與畫面狀態一併帶出來。
    """
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    route_catalog(page, [catalog_of(positions)])
    page.goto(f"{ORIGIN}/")
    try:
        page.wait_for_selector("#positions > li", timeout=3000)
    except PlaywrightTimeoutError:
        pytest.fail(
            f"這份索引畫不出任何一列:{positions}\n"
            f"頁面錯誤:{errors or '(無)'}\n"
            f"列表容器:{page.evaluate('() => document.getElementById(\"positions\").innerHTML')!r}"
        )


def difficulty_cells(page) -> dict[str, dict[str, Any]]:
    """每一列的難度那一格:看得到的字、上色掛勾,以及實際算出來的兩個顏色。

    顏色讀的是 `getComputedStyle` 而非樣式表的字面值 —— 規則被更晚的規則蓋掉、
    選擇器打錯字之類的退化,只有算出來的值抓得到。
    """
    return page.evaluate(
        """() => Object.fromEntries(
          [...document.querySelectorAll('#positions > li')].map((li) => {
            const cell = li.querySelector('.position-difficulty');
            const style = cell && getComputedStyle(cell);
            return [
              li.dataset.id,
              {
                text: cell?.textContent.trim() ?? null,
                level: cell?.dataset.level ?? null,
                color: style?.color ?? null,
                background: style?.backgroundColor ?? null,
              },
            ];
          }),
        )"""
    )


def counts(page) -> list[str]:
    """畫面上的「已完成 X / 總共 Y 題」兩個數字。"""
    return page.evaluate(
        """() => ['completed-count', 'total-count'].map(
          (id) => document.getElementById(id).textContent.trim(),
        )"""
    )


def toggle(page, position_id: int) -> None:
    """按下某一題的完成標記 —— 真實點擊,不是直接呼叫函式。"""
    page.locator(f'#positions > li[data-id="{position_id}"] input[type="checkbox"]').click()


def seed_completed(page, ids: list[int]) -> None:
    """在頁面跑任何腳本之前,先在本機儲存區放好一份完成狀態。"""
    page.add_init_script(
        f"localStorage.setItem({json.dumps(STORAGE_KEY)}, {json.dumps(json.dumps(ids))})"
    )


def stored_completed(page) -> Any:
    """儲存區裡目前的原始值(沒寫過時為 `None`)。"""
    return page.evaluate(
        f"() => localStorage.getItem({json.dumps(STORAGE_KEY)})"
    )


# --- 1.2、1.3:一列有哪四項 --------------------------------------------


def test_the_list_shows_one_row_per_listable_position(list_page) -> None:
    """1.2:索引裡每一個可上架的題目各一列,順序即索引的順序。"""
    open_list(list_page)

    assert [row["id"] for row in rows(list_page)] == LISTED_IDS


def test_every_row_shows_its_id_title_difficulty_and_tags(list_page) -> None:
    """1.2:一列要有題號、局名、難度與標籤四項,缺一不可。

    難度那一項比對的是**分級的說法**(「困難」)而不是數字 —— 使用者看過實際畫面後
    改成了 leetcode 式的三色標籤,數字只在認不得的值上才會原樣出現。難度那一格另有
    專門的一節(下方「難度是三色標籤」)驗顏色與退路,這裡只確認四項都在。
    """
    open_list(list_page)

    drawn = {row["id"]: row["text"] for row in rows(list_page)}

    for entry in CATALOG:
        if str(entry["id"]) not in drawn:
            continue
        text = drawn[str(entry["id"])]
        assert str(entry["id"]) in text, f"第 {entry['id']} 題:列上看不到題號"
        assert entry["title"] in text, f"第 {entry['id']} 題:列上看不到局名"
        assert difficulty_text(entry["difficulty"]) in text, (
            f"第 {entry['id']} 題:列上看不到難度"
        )
        for tag in entry["tags"]:
            assert tag in text, f"第 {entry['id']} 題:列上看不到標籤「{tag}」"


def test_the_row_number_reads_as_a_numbered_entry(list_page) -> None:
    """題號那一欄是「數字 + 點」,不加「第…題」。

    參照形態是 leetcode 的 `1. Two Sum`:**點是分隔符而不是贅字** —— 它把題號與局名
    分開;「第」與「題」則是每一列各重複一次的贅字,而那一欄本來就在說「第幾題」。

    斷言分兩層:那一格**恰好等於**「題號.」(只驗「數字在裡面」的話,「第 3 題」與
    光禿禿的「3」都照樣全綠),以及那兩個字在整列的可見文字中一次都沒出現(避免只把
    字搬到別欄)。
    """
    open_list(list_page)

    cells = list_page.evaluate(
        """() => Object.fromEntries(
          [...document.querySelectorAll('#positions > li')].map((li) => [
            li.dataset.id,
            {
              id: li.querySelector('.position-id')?.textContent.trim() ?? null,
              row: li.innerText,
            },
          ]),
        )"""
    )

    assert sorted(cells) == sorted(LISTED_IDS)
    for row_id, cell in cells.items():
        assert cell["id"] == f"{row_id}.", (
            f"第 {row_id} 題的題號欄不是「{row_id}.」:{cell['id']!r}"
        )
        assert "第" not in cell["row"] and "題" not in cell["row"], (
            f"第 {row_id} 題的列上仍留著「第…題」的字樣:{cell['row']!r}"
        )


#: 題號位數不同、標籤數也不同的三題 —— 一份夾具同時驗題號右對齊與標籤靠右。
#:
#: 位數刻意跨 1/2/3 位(`1`、`22`、`333`):題號**有缺口**是本專案的實情
#: (按局號收題,中間幾局還沒收;收第二本書之後更明顯),而位數全部相同的夾具
#: 對「左對齊還是右對齊」完全不敏感 —— 兩者畫出來一模一樣。
ALIGNMENT_SAMPLES: list[dict[str, Any]] = [
    {"id": 1, "title": "一個標籤", "difficulty": 1, "tags": ["連將殺"],
     "source": "適情雅趣"},
    {"id": 22, "title": "三個標籤", "difficulty": 2, "tags": ["雙馬", "連將殺", "鬥快"],
     "source": "適情雅趣"},
    {"id": 333, "title": "沒有標籤", "difficulty": 3, "tags": [], "source": "適情雅趣"},
]


def text_boxes(page, selector: str) -> dict[str, dict[str, float]]:
    """每一列中某一格**文字本身**的邊界盒,不是那一格的邊界盒。

    量的是 `Range.getBoundingClientRect()` —— 格子本身是 grid item,寬度由欄寬決定,
    左右緣不論怎麼對齊都一樣;能分辨對齊方式的只有文字實際落在哪裡。
    """
    return page.evaluate(
        """(sel) => Object.fromEntries(
          [...document.querySelectorAll('#positions > li')].map((li) => {
            const cell = li.querySelector(sel);
            const range = document.createRange();
            range.selectNodeContents(cell);
            const text = range.getBoundingClientRect();
            const box = cell.getBoundingClientRect();
            return [
              li.dataset.id,
              {
                textLeft: text.left,
                textRight: text.right,
                cellLeft: box.left,
                cellRight: box.right,
              },
            ];
          }),
        )""",
        selector,
    )


def test_the_row_numbers_are_right_aligned(list_page) -> None:
    """題號右對齊 —— 那一排點連成一條直線,局名的起點固定。

    量的是**文字自己的邊界盒**(`Range`),不是格子的:格子是 grid item,寬度由欄寬
    決定,左右緣不論怎麼對齊都相同,只驗格子等於什麼都沒驗。

    兩個方向都要斷言:

    - 位數不同的三題,文字**右緣相同** —— 這是右對齊成立的直接後果;
    - 它們的文字**左緣互不相同** —— 這條擋掉「三個題號碰巧一樣寬」這種讓上一條自動
      成立的情形(例如有人把 `text-align` 拿掉又順手把欄寬縮到剛好貼齊)。

    刻意不驗 `text-align: right` 的字面值:那個屬性可以被 `direction`、`justify-self`
    或行內樣式繞過,而畫面上的結果才是使用者看到的東西。
    """
    list_page.set_viewport_size({"width": 1280, "height": 900})
    open_list(list_page, ALIGNMENT_SAMPLES)

    boxes = text_boxes(list_page, ".position-id")
    assert sorted(boxes) == ["1", "22", "333"]

    rights = {row_id: box["textRight"] for row_id, box in boxes.items()}
    lefts = {row_id: box["textLeft"] for row_id, box in boxes.items()}

    assert max(rights.values()) - min(rights.values()) < 0.5, (
        f"位數不同的題號右緣沒有對齊(那一排點不成直線):{rights}"
    )
    assert len(set(round(value, 1) for value in lefts.values())) == 3, (
        f"三個題號的左緣相同 —— 它們一樣寬,右緣對齊這件事因此什麼也沒證明:{lefts}"
    )
    # 題號欄仍是獨立一欄:文字不得溢出格子,也不得跑進局名那一欄。
    for row_id, box in boxes.items():
        assert box["textLeft"] >= box["cellLeft"] - 0.5, f"第 {row_id} 題的題號溢出欄外:{box}"
        assert box["textRight"] <= box["cellRight"] + 0.5, f"第 {row_id} 題的題號溢出欄外:{box}"


def test_the_list_shows_neither_source_nor_description(list_page) -> None:
    """1.2:出處與描述**不在列表呈現**(出處已移到對局介面,見 4.5;描述兩頁都不畫)。

    列是掃視用的,每列擠進越多欄位越難掃。索引照常帶著這兩個欄位(日後收錄第二本
    書時列表要靠出處分辨同名排局),前端只是拿了不畫。

    讀的是 `innerHTML` 而不是 `innerText`:後者對 `display: none` 的內容是**盲的**,
    畫進去再用一行 CSS 藏起來照樣全綠 —— 而那些字仍在 DOM 裡,Ctrl+F 找得到、輔助
    技術也讀得到,並沒有真的「不呈現」。`innerHTML` 連屬性裡的字串一併涵蓋。
    """
    open_list(list_page)

    markup = list_page.evaluate("() => document.body.innerHTML")
    leaked = [text for text in NEVER_ON_THE_LIST if text in markup]

    assert not leaked, f"列表上出現了不該呈現的欄位:{leaked}"


def test_the_id_and_title_read_as_the_primary_identifiers(list_page) -> None:
    """1.3:題號與局名是每一列的主要識別,可供快速掃視。

    「主要」在畫面上是有形的:兩者的字級必須明顯大於難度與標籤,否則四項一樣重,
    掃視時眼睛無處落腳。
    """
    open_list(list_page)

    sizes = list_page.evaluate(
        """() => {
          const row = document.querySelector('#positions > li');
          const size = (selector) =>
            parseFloat(getComputedStyle(row.querySelector(selector)).fontSize);
          return {
            id: size('.position-id'),
            title: size('.position-title'),
            difficulty: size('.position-difficulty'),
            tag: size('.position-tags'),
          };
        }"""
    )

    secondary = max(sizes["difficulty"], sizes["tag"])
    assert sizes["id"] > secondary, f"題號不比難度與標籤突出:{sizes}"
    assert sizes["title"] > secondary, f"局名不比難度與標籤突出:{sizes}"


# --- 欄序與對齊 ---------------------------------------------------------
#
# 左到右:題號、局名、標籤(靠右)、難度、完成標記。
#
# **難度緊鄰完成標記**,不在局名旁邊。標籤是全列最不可預測的一項(數量與長度都
# 不定),夾在局名與難度之間,右側那兩欄才連成固定的兩直欄。


#: 欄序:選擇器由左到右。DOM 順序也必須是這個順序(理由見完成標記那一節)。
COLUMN_ORDER = [
    ".position-id",
    ".position-title",
    ".position-tags",
    ".position-difficulty",
    ".position-toggle",
]


def test_the_columns_run_id_title_tags_difficulty_toggle(list_page) -> None:
    """五欄由左到右是題號、局名、標籤、難度、完成標記。

    畫面位置**與** DOM 順序兩者都驗:只驗畫面的話,一組 `order` 就能把欄序搬成別的
    樣子而測試全綠,而 Tab 與螢幕閱讀器仍走 DOM 順序;只驗 DOM 的話則反過來。

    這條同時釘住「難度不在局名旁邊」—— 難度移回原位(局名之後)會讓它與標籤的
    左緣順序倒過來。
    """
    list_page.set_viewport_size({"width": 1280, "height": 900})
    open_list(list_page)

    layout = list_page.evaluate(
        """(selectors) => [...document.querySelectorAll('#positions > li')].map((li) => ({
          id: li.dataset.id,
          lefts: selectors.map(
            (sel) => li.querySelector(sel).getBoundingClientRect().left,
          ),
          domOrder: selectors.map((sel) =>
            [...li.children].indexOf(li.querySelector(sel)),
          ),
        }))""",
        COLUMN_ORDER,
    )

    assert [entry["id"] for entry in layout] == LISTED_IDS
    for entry in layout:
        assert entry["lefts"] == sorted(entry["lefts"]), (
            f"第 {entry['id']} 題的欄序不是 {COLUMN_ORDER}:各欄左緣為 {entry['lefts']}"
        )
        assert entry["domOrder"] == [0, 1, 2, 3, 4], (
            f"第 {entry['id']} 題的 DOM 順序與畫面欄序對不上 —— Tab 會亂跳:"
            f"{entry['domOrder']}"
        )


def test_the_tags_are_flushed_right_so_the_difficulty_never_drifts(list_page) -> None:
    """標籤欄靠右對齊,難度與完成標記因此在每一列都落在同一個位置。

    這條有實質作用而不只是美觀:標籤靠左的話,空隙落在標籤與難度**之間**,而每一列
    空隙的寬度都不同 —— 難度看起來就像浮在一片寬度不定的空白右邊。靠右之後空隙移到
    標籤左側,標籤、難度、完成標記三者在右緣連成三條直欄。

    三層斷言,少一層就漏得掉一種寫法:

    1. **難度欄的左緣在三列上相同** —— 這是使用者真正在意的結果。
    2. **標籤文字的右緣在三列上相同** —— 只有第 1 條的話,靠左照樣全綠(欄是 `fr`,
       邊界本來就不隨內容動);這一條才真的在驗對齊方向。
    3. **標籤文字的左緣互不相同** —— 擋掉「三列標籤碰巧一樣寬」讓第 2 條自動成立。

    夾具的三列標籤數各為 1、3、0,寬度必不相同。
    """
    list_page.set_viewport_size({"width": 1280, "height": 900})
    open_list(list_page, ALIGNMENT_SAMPLES)

    # 標籤沒有折行,否則「右緣」指的是最後一行的右緣,三列不再可比。
    wrapped = list_page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => {
          const tops = [...li.querySelectorAll('.position-tags > *')].map(
            (chip) => Math.round(chip.getBoundingClientRect().top),
          );
          return new Set(tops).size;
        })"""
    )
    assert all(count <= 1 for count in wrapped), f"夾具的標籤折行了,右緣不再可比:{wrapped}"

    difficulty = text_boxes(list_page, ".position-difficulty")
    tags = text_boxes(list_page, ".position-tags")

    left_edges = {row_id: box["cellLeft"] for row_id, box in difficulty.items()}
    assert max(left_edges.values()) - min(left_edges.values()) < 0.5, (
        f"標籤多寡讓難度欄左右浮動:{left_edges}"
    )

    tag_rights = {row_id: box["textRight"] for row_id, box in tags.items()}
    tag_lefts = {row_id: box["textLeft"] for row_id, box in tags.items()}
    assert max(tag_rights.values()) - min(tag_rights.values()) < 0.5, (
        f"標籤沒有靠右對齊(右緣不齊):{tag_rights}"
    )
    assert len(set(round(value, 1) for value in tag_lefts.values())) == 3, (
        f"三列的標籤一樣寬 —— 右緣對齊這件事因此什麼也沒證明:{tag_lefts}"
    )


# --- 難度是三色標籤 -----------------------------------------------------
#
# 使用者看過實際畫面後的意見:「難度 3」這種字樣改成 leetcode 式的三色方框標籤 ——
# 1 簡單(綠)、2 中等(橙)、3 困難(紅)。分級的定義補進了
# `.kiro/steering/structure.md` 的題目 schema(它原先只寫「數字|是|難度分級」)。
#
# 本節分成兩層:
#
# 1. **說法與顏色**:三級各自的字、以及三者的顏色**彼此不同**。比的是「互不相同」
#    而不是寫死的色碼字面值 —— 後者每次微調配色都要跟著改,而它其實只在防「三級
#    同色」這一種退化,那正是互不相同這條在防的。
# 2. **超出 1–3 的值有退路**:0、4、負數、`null`、欄位不存在,一律原樣顯示、吃中性
#    色,而且**那一列照樣畫得出來**。`service/positions.py` 的 `_read_int` 對難度
#    沒有下界,0 是真的進得來的值。


#: 三個合法分級各一題,外加一題超出範圍 —— 一份夾具走完全部四條路徑。
#:
#: **每一題都帶一個標籤**,因為難度的字級是拿同一列的標籤 chip 當基準比的
#: (`test_the_difficulty_is_coloured_text_not_a_tag`)。標籤全空的話那一列只有
#: 佔位符號,沒有 chip 可比。
DIFFICULTY_SAMPLES: list[dict[str, Any]] = [
    {"id": 11, "title": "入門局", "difficulty": 1, "tags": ["連將殺"], "source": "適情雅趣"},
    {"id": 12, "title": "進階局", "difficulty": 2, "tags": ["連將殺"], "source": "適情雅趣"},
    {"id": 13, "title": "刁鑽局", "difficulty": 3, "tags": ["連將殺"], "source": "適情雅趣"},
    {"id": 14, "title": "離譜局", "difficulty": 9, "tags": ["連將殺"], "source": "適情雅趣"},
]


def test_each_difficulty_reads_as_a_word_not_a_number(list_page) -> None:
    """1 / 2 / 3 分別畫成 Easy / Medium / Hard。

    **說法是英文,而且不是隨手選的**:它是 steering 對「使用者可見文字一律繁體中文」
    列出的例外之一,理由寫在 `.kiro/steering/structure.md` 的難度分級表 —— 難度是
    無底色的彩色字,同一列的標籤又都是中文詞,同語言的話兩者只剩顏色分得開。
    改回中文會讓那條無障礙上的理由失效,故在此連字面一起釘住。
    """
    open_list(list_page, DIFFICULTY_SAMPLES)

    cells = difficulty_cells(list_page)

    assert {row_id: cell["text"] for row_id, cell in cells.items()} == {
        "11": "Easy",
        "12": "Medium",
        "13": "Hard",
        # 超出範圍:原樣顯示那個數字,不是空白、不是猜一個分級。
        "14": "9",
    }


def test_the_three_difficulties_do_not_share_a_colour(list_page) -> None:
    """三級的文字色彼此不同 —— 三級同色的話,顏色這個線索等於沒有。

    **只比文字色,因為顏色是這一格僅剩的線索。** 難度已從標籤改成一個帶顏色的詞
    (底色與框寬都沒了),文字色一旦退化成三級共用的死值,就再也沒有第二個地方
    分得出簡單與困難。

    比的是彼此相異而非某個色碼字面值:配色微調不該讓測試轉紅,而「三級同色」這種
    真正的退化逃不掉。順帶要求它們與中性色(超出範圍那一題)也不同,否則三級全部
    退回中性灰照樣算「彼此不同」—— 那是最容易發生的一種:選擇器打錯字。
    """
    open_list(list_page, DIFFICULTY_SAMPLES)

    cells = difficulty_cells(list_page)
    graded = ["11", "12", "13"]

    seen = [cells[row_id]["color"] for row_id in graded]
    assert len(set(seen)) == 3, f"三個難度的文字色沒有分開:{seen}"
    assert cells["14"]["color"] not in seen, (
        f"合法分級的文字色與中性色相同,規則沒有生效:{seen} 對 {cells['14']['color']}"
    )

    # 上色掛勾本身也釘住:樣式表靠它挑規則,改名會讓上面那條靜默退回中性色。
    assert [cells[row_id]["level"] for row_id in graded] == ["1", "2", "3"]
    assert cells["14"]["level"] is None, "超出範圍的難度不該掛上分級的上色掛勾"


@pytest.mark.parametrize("row_id", ["11", "12", "13", "14"])
def test_the_difficulty_is_coloured_text_not_a_tag(list_page, row_id) -> None:
    """難度是**一個帶顏色的詞**,不是標籤:沒有底色、沒有圓角,字級與標籤的 chip 相同。

    這條原本要求的是相反的事(底色 + 圓角 + 左右留白 = 一個方框)。使用者看過實際
    畫面後改了主意:難度與標籤都做成 chip 的話,一列的右半邊是一排形狀相同的小方塊,
    難度混在標籤裡認不出來。

    **字級與標籤看齊是第二次調整**:中途試過與局名同級的 18px,太搶 —— 難度終究是
    次要資訊。同級卻仍分得出來,靠的是它沒有灰底 chip,以及它的說法是英文而標籤是
    中文。

    「看齊」在數值上是**比 chip 大 1px**,不是相等:相同 `font-size` 下拉丁字母的
    x-height 只有 CJK 的一半上下,數值相等時英文看起來明顯比中文小。這裡因此驗
    「大一點點」而不是「相等」,上界擋住有人把它一路放大回 18px。

    底色**不能只比 `backgroundColor` 是不是某個值**:那個屬性在完全透明時照樣回報
    一個顏色值(`rgba(…, 0)`)。這裡要的正是全透明,因此直接要求 alpha 為 0。
    (tasks 3.2 在 `borderLeftColor` 上踩過同一個坑,方向相反而已。)

    **四題全驗,超出範圍那一題(14)尤其要驗** —— 它走的是基底規則,三個合法分級
    走的是各自的覆寫。只驗其中一邊的話,另一邊悄悄長回底色不會被發現。
    """
    open_list(list_page, DIFFICULTY_SAMPLES)

    box = list_page.evaluate(
        """(rowId) => {
          const cell = document.querySelector(
            `#positions > li[data-id="${rowId}"] .position-difficulty`,
          );
          const style = getComputedStyle(cell);
          const rect = cell.getBoundingClientRect();
          const row = cell.closest('li').getBoundingClientRect();
          const chip = cell.closest('li').querySelector('.position-tag');
          return {
            background: style.backgroundColor,
            radius: parseFloat(style.borderTopLeftRadius) || 0,
            padding: parseFloat(style.paddingLeft) || 0,
            fontSize: parseFloat(style.fontSize),
            chipFontSize: chip ? parseFloat(getComputedStyle(chip).fontSize) : null,
            width: rect.width,
            rowWidth: row.width,
          };
        }""",
        row_id,
    )

    transparent = box["background"] == "rgba(0, 0, 0, 0)" or box[
        "background"
    ].endswith(", 0)")
    assert transparent, f"第 {row_id} 題的難度還帶著底色,那是標籤的樣子:{box}"
    assert box["radius"] == 0, f"難度還留著圓角,那是標籤的樣子:{box}"
    assert box["padding"] == 0, f"難度還留著左右留白,那是標籤的樣子:{box}"
    gap = box["fontSize"] - box["chipFontSize"]
    assert 0 < gap <= 2, (
        f"難度的字級沒有與標籤 chip 看齊(該比它大 1px 左右,補拉丁字母的 x-height):{box}"
    )
    # 只有內容那麼寬,不撐滿整欄。
    assert box["width"] < box["rowWidth"] / 4, f"難度那一格撐得太寬:{box}"


@pytest.mark.parametrize(
    ("difficulty", "shown"),
    [
        # 0 是 `_read_int` 收得進來的下界,也是最容易被 falsy 判斷吃掉的值。
        (0, "0"),
        # 分級的上界之外。
        (4, "4"),
        (-1, "-1"),
    ],
)
def test_a_difficulty_outside_the_scale_still_draws_its_row(
    list_page, difficulty, shown
) -> None:
    """1–3 之外的值原樣顯示,而且**那一列照樣完整**。

    schema 說難度是 1–3,但沒有任何一層在執行期強制它。認不得的值若讓那一格丟例外,
    整份列表會跟著消失 —— 一題資料失準不該把題庫從畫面上抹掉。

    開啟用的是 `open_list_expecting_rows`:那個失效模式下 `open_list` 只會以「在等
    某個選擇器」逾時收場,說不出成因(tasks 5.2 記過同一件事)。
    """
    open_list_expecting_rows(
        list_page, [{**DIFFICULTY_SAMPLES[0], "difficulty": difficulty}]
    )

    cells = difficulty_cells(list_page)

    assert cells["11"]["text"] == shown, f"難度 {difficulty} 沒有原樣顯示:{cells}"
    assert cells["11"]["level"] is None
    # 那一列的其餘部分沒有被連累。
    drawn = rows(list_page)
    assert len(drawn) == 1 and DIFFICULTY_SAMPLES[0]["title"] in drawn[0]["text"]


@pytest.mark.parametrize("missing", [{"difficulty": None}, {}])
def test_a_position_without_a_difficulty_falls_back_to_the_placeholder(
    list_page, missing
) -> None:
    """難度為 `null` 或欄位不存在時留佔位符號,不是空白也不是「簡單」。

    參數化的兩種形狀是不同的東西:`null` 是「填了但沒有值」,欄位不存在是「這份
    索引根本沒帶」。前端對兩者的回答該一樣,而只測其中一種漏得掉另一種。
    """
    entry = {k: v for k, v in DIFFICULTY_SAMPLES[0].items() if k != "difficulty"}
    open_list_expecting_rows(list_page, [{**entry, **missing}])

    cells = difficulty_cells(list_page)

    assert cells["11"]["text"] == "—", f"難度缺值時的說法不對:{cells}"
    assert cells["11"]["level"] is None
    assert len(rows(list_page)) == 1, "難度缺值讓那一列畫不出來"


def test_a_difficulty_of_the_wrong_type_does_not_impersonate_a_grade(list_page) -> None:
    """字串型的 `"1"` 不得被當成分級 1。

    索引的難度一路是數字(`service/positions.py` 的 `_read_int`),字串進得來就表示
    上游出了事;此時原樣顯示、吃中性色,比默默當成「簡單」誠實。物件字面值查表會把
    `1` 與 `"1"` 混為一談,`Map` 不會 —— 這條就是釘住那個選擇。
    """
    open_list(list_page, [{**DIFFICULTY_SAMPLES[0], "difficulty": "1"}])

    cells = difficulty_cells(list_page)

    assert cells["11"]["text"] == "1"
    assert cells["11"]["level"] is None, "型別不對的難度卻拿到了分級的上色掛勾"


# --- 3.1、3.2、3.5:完成標記與計數 --------------------------------------


def test_every_row_offers_a_completion_toggle(list_page) -> None:
    """3.1:每一題都有一個可切換的完成標記,初始為未完成。"""
    open_list(list_page)

    drawn = rows(list_page)

    assert [row["checked"] for row in drawn] == [False] * len(LISTED_IDS)
    assert [row["marked"] for row in drawn] == [False] * len(LISTED_IDS)


def test_the_completion_toggle_sits_at_the_far_right_of_the_row(list_page) -> None:
    """完成標記畫在一列的**最右**(使用者看過實際畫面後的意見)。

    三層都要:

    1. **畫面上真的在最右** —— 它的左緣在同列其餘每一格的右緣之外。只驗 DOM 順序的
       話,一條 `order: -1` 就能把它搬回最左而測試全綠。
    2. **DOM 順序與它一致**(它是 `<li>` 的最後一個子節點)—— 只驗畫面位置的話,
       用 `order` 純視覺搬移照樣全綠,而 Tab 與螢幕閱讀器仍走 DOM 順序,鍵盤使用者
       的行進順序會與眼睛看到的分家。
    3. **它仍在連結之外** —— 見下一條。

    第 1 條刻意不用「它是最後一個 grid item」之類的說法:那和第 2 條是同一件事,
    量不到畫面。
    """
    open_list(list_page)

    geometry = list_page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => {
          const box = li.querySelector('.position-toggle');
          const others = [...li.children].filter((node) => node !== box);
          return {
            id: li.dataset.id,
            last: li.lastElementChild === box,
            left: box.getBoundingClientRect().left,
            othersRight: Math.max(
              ...others.map((node) => node.getBoundingClientRect().right),
            ),
          };
        })"""
    )

    assert [entry["id"] for entry in geometry] == LISTED_IDS
    for entry in geometry:
        assert entry["left"] >= entry["othersRight"], (
            f"第 {entry['id']} 題的完成標記不在最右:{entry}"
        )
        assert entry["last"], (
            f"第 {entry['id']} 題的完成標記在畫面上靠右,但 DOM 順序不是最後一個 ——"
            f" Tab 順序會與眼睛看到的對不上:{entry}"
        )


def test_the_completion_toggle_stays_outside_the_link(list_page) -> None:
    """完成標記在**結構上**就落在連結之外,不論它畫在哪一欄。

    這是 4.1「按完成標記不會跳進對局頁」成立的**唯一理由** —— 不靠
    `stopPropagation`、不靠 z-index 疊放。那兩種補救擋不住鍵盤的 Enter 與中鍵,而
    真正的結構保證擋得住。

    完成標記自最左移到最右時,最省事的寫法是把它塞進那個撐滿整格的 `<a>` 裡;
    `test_marking_a_position_complete_does_not_open_it` 會抓到,但那條要等 300ms 且
    說出來的是「離開了列表頁」。這一條直接說出成因。
    """
    open_list(list_page)

    structure = list_page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => {
          const box = li.querySelector('.position-toggle');
          return {
            id: li.dataset.id,
            insideAnchor: box.closest('a') !== null,
            childOfRow: box.parentElement === li,
          };
        })"""
    )

    for entry in structure:
        assert not entry["insideAnchor"], (
            f"第 {entry['id']} 題的完成標記被放進了連結裡 —— 按它會跳進對局頁:{entry}"
        )
        assert entry["childOfRow"], (
            f"第 {entry['id']} 題的完成標記不是列的直接子節點:{entry}"
        )


def test_toggling_a_position_marks_it_immediately(list_page) -> None:
    """3.2:切換後立即反映該題的新狀態,而且只有那一題變。"""
    open_list(list_page)

    toggle(list_page, 2)

    state = {row["id"]: (row["checked"], row["marked"]) for row in rows(list_page)}
    assert state == {
        row_id: (True, True) if row_id == "2" else (False, False)
        for row_id in LISTED_IDS
    }


def test_toggling_a_marked_position_clears_it(list_page) -> None:
    """3.1:標記是可**切換**的 —— 按第二次要能取消。"""
    open_list(list_page)

    toggle(list_page, 2)
    toggle(list_page, 2)

    state = {row["id"]: (row["checked"], row["marked"]) for row in rows(list_page)}
    assert state["2"] == (False, False)
    assert counts(list_page) == ["0", TOTAL]


def test_toggling_with_the_keyboard_keeps_the_focus_on_that_row(list_page) -> None:
    """切換後焦點仍在同一列的完成標記上。

    `list.js` 的呈現是**整份重畫**(與 `app.js` 同一個設計選擇:呈現層不留第二份
    狀態)。代價是切換後原本那個核取方塊節點已經被換掉,焦點會掉回頁面開頭 ——
    以鍵盤操作的使用者按一題就得從頭 Tab 一次,一路標下去根本辦不到。把焦點放回
    去是那個設計選擇**唯一的補償**,沒有測試釘住的話,下一個動 `render()` 的人會
    靜默弄壞它而不會有任何紅燈。
    """
    open_list(list_page)

    # 真的用鍵盤:聚焦到那一列的核取方塊,按空白鍵切換。
    list_page.locator('#positions > li[data-id="3"] input[type="checkbox"]').press(" ")

    where = list_page.evaluate(
        """() => {
          const active = document.activeElement;
          return {
            tag: active?.tagName ?? null,
            row: active?.closest('li')?.dataset.id ?? null,
            checked: active?.checked ?? null,
          };
        }"""
    )

    assert where == {"tag": "INPUT", "row": "3", "checked": True}, (
        f"切換後焦點沒有留在同一列:{where}"
    )


def test_the_counts_show_how_many_are_done_out_of_the_total(list_page) -> None:
    """3.5:完成題數與總題數對使用者可見,且隨每一次切換更新。

    只畫出總數、或把已完成寫死成 0,都會讓標記變成沒有回饋的動作。
    """
    open_list(list_page)

    assert counts(list_page) == ["0", TOTAL]

    toggle(list_page, 1)
    assert counts(list_page) == ["1", TOTAL]

    toggle(list_page, 3)
    assert counts(list_page) == ["2", TOTAL]

    toggle(list_page, 1)
    assert counts(list_page) == ["1", TOTAL]


def test_each_toggle_names_the_position_it_marks(list_page) -> None:
    """完成標記要說得出自己是哪一題的。

    一整欄的核取方塊長得一模一樣,畫面上靠位置分辨,而輔助技術沒有位置可用 ——
    沒有名稱的話讀出來就是連續五個「核取方塊,未勾選」。
    """
    open_list(list_page)

    labels = list_page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => ({
          id: li.dataset.id,
          label:
            li.querySelector('input[type="checkbox"]')?.getAttribute('aria-label')
            ?? null,
        }))"""
    )

    for entry in labels:
        assert entry["label"], f"第 {entry['id']} 題的完成標記沒有名稱"
        assert TITLES[entry["id"]] in entry["label"], (
            f"第 {entry['id']} 題的完成標記名稱認不出是哪一題:{entry['label']}"
        )


def test_nothing_is_marked_before_the_user_touches_it(list_page) -> None:
    """3.4:標記只由使用者的操作產生 —— 光是開啟列表不得寫入任何題號。"""
    open_list(list_page)

    assert counts(list_page) == ["0", TOTAL]
    assert stored_completed(list_page) is None, "只是載入列表就寫進了儲存區"


# --- 3.3:重新載入後標記仍在 -------------------------------------------


def test_the_marks_survive_a_reload(list_page) -> None:
    """3.3:重新開啟服務時呈現先前標記的完成狀態。

    這一條是完成標記整個功能的意義所在 —— 存不住的話,下次回來一樣不知道從哪裡繼續。
    """
    open_list(list_page)
    toggle(list_page, 2)
    toggle(list_page, 3)

    list_page.reload()
    list_page.wait_for_selector("#positions > li")

    state = {row["id"]: row["checked"] for row in rows(list_page)}
    assert state == {row_id: row_id in ("2", "3") for row_id in LISTED_IDS}
    assert counts(list_page) == ["2", TOTAL]


def test_clearing_the_last_mark_survives_a_reload(list_page) -> None:
    """3.3:取消最後一個標記之後重開,標記**不得**復活。

    「集合空了就不寫」這種最佳化在其他每一條測試下都成立,只有這裡會抓到它。
    """
    open_list(list_page)
    toggle(list_page, 2)
    toggle(list_page, 2)

    list_page.reload()
    list_page.wait_for_selector("#positions > li")

    assert [row["checked"] for row in rows(list_page)] == [False] * len(LISTED_IDS)
    assert counts(list_page) == ["0", TOTAL]


# --- 3.6、3.7:儲存區裡的意外 ------------------------------------------


def test_a_mark_for_a_position_no_longer_listed_does_not_break_the_list(
    list_page,
) -> None:
    """3.7:曾標記完成的題目後來不再列出時,列表不因此失敗,計數也不虛報。

    集合是題號的集合,不是列表的鏡像 —— 第 4 題已被標為不可解而下架,第 999 題
    根本不在這份索引裡,兩者都不該算進「已完成 X / 3 題」。
    """
    seed_completed(list_page, [2, 4, 999])
    open_list(list_page)

    state = {row["id"]: row["checked"] for row in rows(list_page)}
    assert state == {row_id: row_id == "2" for row_id in LISTED_IDS}
    assert counts(list_page) == ["1", TOTAL]


def test_a_corrupted_store_falls_back_to_nothing_completed(list_page) -> None:
    """3.6:先前儲存的完成狀態無法解析時以「全部未完成」繼續運作,而非失敗或空白。"""
    list_page.add_init_script(
        f"localStorage.setItem({json.dumps(STORAGE_KEY)}, '{{ not json')"
    )
    open_list(list_page)

    assert [row["checked"] for row in rows(list_page)] == [False] * len(LISTED_IDS)
    assert counts(list_page) == ["0", TOTAL]


# --- 載入中與載入失敗:兩個數字不得說謊 --------------------------------


def test_the_empty_state_stays_hidden_while_the_index_is_still_loading(
    list_page,
) -> None:
    """1.5 的告知只屬於「題庫真的沒有題目」,不屬於「還沒拿到」。

    索引還在路上時列表當然是空的,但那不是題庫的狀態 —— 把「題庫目前沒有題目」
    掛在整個取得期間,使用者會在題庫明明有題的情況下被告知它是空的。
    """
    held = hold_catalog(list_page)
    list_page.goto(f"{ORIGIN}/")
    wait_for_held_request(list_page, held)

    assert list_page.locator("#empty").is_hidden(), "索引還沒回來就說題庫沒有題目"
    assert list_page.locator("#error").is_hidden()

    # 放行之後仍要正常長出列來 —— 證明上面看到的是過渡狀態,不是卡死。
    release(held, catalog_of(CATALOG))
    list_page.wait_for_selector("#positions > li")
    assert counts(list_page) == ["0", TOTAL]


def test_the_counts_claim_nothing_while_the_index_is_still_loading(list_page) -> None:
    """3.5:還不知道總共幾題的時候,兩個數字不得憑空給一個數。

    寫 0 是最順手也最糟的選擇:「已完成 0 / 0 題」與「題庫是空的」在畫面上讀起來
    完全一樣,而此刻連題庫有沒有題目都還不知道。
    """
    held = hold_catalog(list_page)
    list_page.goto(f"{ORIGIN}/")
    wait_for_held_request(list_page, held)

    shown = counts(list_page)

    assert all(not value.isdigit() for value in shown), (
        f"索引還沒回來就報出了數字:{shown}"
    )


def test_the_counts_claim_nothing_after_a_failed_load(list_page) -> None:
    """3.5:索引取不到時同樣不得報數 —— 那會讓失敗看起來像一個空題庫。"""
    open_broken_list(list_page)

    shown = counts(list_page)

    assert all(not value.isdigit() for value in shown), (
        f"載入失敗卻報出了數字:{shown}"
    )


# --- 1.5 與 Error Handling:空題庫與壞索引是兩個畫面 --------------------


def test_an_empty_catalog_tells_the_user_instead_of_showing_a_blank_page(
    list_page,
) -> None:
    """1.5:題庫沒有任何可列出的題目時告知使用者,而非呈現空白畫面。"""
    open_empty_list(list_page)

    assert list_page.locator("#empty").inner_text().strip() != ""
    assert list_page.locator("#positions > *").count() == 0
    assert counts(list_page) == ["0", "0"]


def test_an_empty_catalog_is_not_reported_as_a_failure(list_page) -> None:
    """1.5:題庫是空的**不是**錯誤 —— 錯誤區不得跟著出現。"""
    open_empty_list(list_page)

    assert list_page.locator("#error").is_hidden()


def test_a_broken_index_shows_the_error_region_not_the_empty_state(list_page) -> None:
    """Error Handling:索引取不到時給的是「題庫載入失敗」與重試,不是「題庫沒有題目」。

    這兩件事對使用者完全不同:一個是題庫真的還沒有題,另一個是東西壞了、重試有用。
    `catalog.js` 對認不得的回應形狀刻意拋 `CatalogError` 而不是給一份空陣列,正是
    為了讓這裡分得開;呈現層若把兩者收進同一個畫面,那份刻意就白費了。
    """
    open_broken_list(list_page)

    assert list_page.locator("#empty").is_hidden(), "索引壞掉卻說成題庫沒有題目"
    assert list_page.locator("#positions > *").count() == 0


def test_the_error_region_is_announced_as_an_alert(list_page) -> None:
    """錯誤區是即時的告知,輔助技術要能在它出現時讀出來。

    (tasks 3.1 的 review 記下的缺口:拿掉 `role="alert"` 時當時 41 條測試全過。)
    """
    open_broken_list(list_page)

    assert list_page.locator("#error").get_attribute("role") == "alert"


def test_the_retry_button_fetches_the_index_again(list_page) -> None:
    """Error Handling 的「可做什麼」:重試要真的再取一次索引,而不只是把告知收起來。"""
    served = open_broken_list(list_page, [BROKEN_INDEX, catalog_of(CATALOG)])

    list_page.locator("#retry").click()
    list_page.wait_for_selector("#positions > li")

    assert served["count"] == 2, f"重試沒有再發請求:{served}"
    assert [row["id"] for row in rows(list_page)] == LISTED_IDS
    assert list_page.locator("#error").is_hidden(), "重試成功後錯誤告知必須收起來"


def test_a_successful_load_shows_neither_the_empty_nor_the_error_region(
    list_page,
) -> None:
    """正常情形下兩個條件區塊都不得出現在畫面上。"""
    open_list(list_page)

    assert list_page.locator("#empty").is_hidden()
    assert list_page.locator("#error").is_hidden()


# --- 6.2:繁體中文 ------------------------------------------------------


def test_the_rendered_list_contains_no_simplified_characters(list_page) -> None:
    """6.2:所有使用者可見文字為繁體中文 —— 含 `list.js` 生出來的那些。

    靜態檔的檢查(前半那條)看不到動態產生的字:難度、標籤、空狀態與錯誤說法都是
    `list.js` 寫出來的。
    """
    open_list(list_page)

    text = list_page.locator("body").inner_text()
    found = sorted({char for char in SIMPLIFIED_ONLY_CHARACTERS if char in text})

    assert not found, f"列表出現簡體字:{''.join(found)}"


# --- 6.3 與版面:已完成與未完成一眼可辨 --------------------------------


def test_a_completed_row_looks_different_from_an_unfinished_one(list_page) -> None:
    """6.3:已完成與未完成的題目在視覺上可區分 —— 這是可用性底線,不是裝飾。

    兩個獨立的線索都要在:底色與左緣的強調色。只靠底色的話,色彩辨識有困難的
    使用者在一片深色中分不出深淺;只靠一條細邊則在快速捲動時看漏。

    **強調色不能只比對 `borderLeftColor`。** 那個屬性在 `border-style: none` 或寬度
    為 0 時照樣回報一個顏色值 —— 整條 `border-left` 被刪掉、金邊在畫面上完全消失,
    只比顏色的斷言仍然全綠。因此連同 style 與 width 一起要求:那條邊要**真的畫得
    出來**。
    """
    open_list(list_page)
    toggle(list_page, 2)

    looks = list_page.evaluate(
        """() => {
          const of = (id) => {
            const style = getComputedStyle(
              document.querySelector(`#positions > li[data-id="${id}"]`),
            );
            return {
              background: style.backgroundColor,
              accent: style.borderLeftColor,
              accentStyle: style.borderLeftStyle,
              accentWidth: parseFloat(style.borderLeftWidth) || 0,
            };
          };
          return { done: of(2), todo: of(1) };
        }"""
    )

    assert looks["done"]["background"] != looks["todo"]["background"], (
        f"已完成與未完成的底色相同:{looks}"
    )
    assert looks["done"]["accent"] != looks["todo"]["accent"], (
        f"已完成與未完成的強調色相同:{looks}"
    )
    assert looks["done"]["accentStyle"] != "none", (
        f"已完成那條強調邊沒有線型,畫面上看不到:{looks}"
    )
    assert looks["done"]["accentWidth"] > 0, (
        f"已完成那條強調邊寬度為 0,畫面上看不到:{looks}"
    )


#: 兩頁各自的視覺語言:`:root` 上全部共用自訂屬性的解析值,加上 body 的字體。
_VISUAL_LANGUAGE = """
(names) => {
  const root = getComputedStyle(document.documentElement);
  const body = getComputedStyle(document.body);
  return {
    palette: Object.fromEntries(
      names.map((name) => [name, root.getPropertyValue(name).trim()]),
    ),
    font: body.fontFamily,
    color: body.color,
    background: body.backgroundColor,
  };
}
"""


def test_the_list_shares_the_visual_language_of_the_play_page(list_page) -> None:
    """`list.css` 與既有的 `style.css` 共用視覺語言,不是另一套配色。

    兩頁之間往返(4.1、4.3)是這個產品的主要動線;底色與字體換一套,使用者會覺得
    自己跳到了別的網站。

    比對的是 `:root` 上**全部共用自訂屬性**的解析值,不是 body 的那三個屬性。兩份
    樣式表各自宣告一份調色盤(design 把 `list.css` 的依賴列為「無」),只看 body
    的話只有 `--page-bg`、`--text` 與字體守得住 —— `--accent`、`--panel-bg` 這些漂了
    完全沒人知道,而 `--accent` 同時是完成標記的強調色與計數的顏色,正是最可能被
    單獨調整的那一個。
    """
    open_list(list_page)
    listed = list_page.evaluate(_VISUAL_LANGUAGE, SHARED_CUSTOM_PROPERTIES)

    list_page.goto(f"{ORIGIN}/play.html?id=1")
    list_page.wait_for_selector("#board")
    played = list_page.evaluate(_VISUAL_LANGUAGE, SHARED_CUSTOM_PROPERTIES)

    # 先確認兩邊都真的宣告了這些屬性 —— 兩邊同時漏掉會讓比對「相等」而毫無意義。
    missing = [name for name, value in listed["palette"].items() if value == ""]
    assert not missing, f"列表頁沒有宣告這些共用屬性:{missing}"

    assert listed == played, f"列表頁與對局頁的視覺語言不一致:{listed} vs {played}"


def test_the_desktop_layout_needs_no_horizontal_scrolling(list_page) -> None:
    """桌面尺寸下讀得順的基本版面:整份列表放得下,不必左右捲。

    (行動裝置的直向畫面屬 Backlog,本條只管桌面。)
    """
    list_page.set_viewport_size({"width": 1280, "height": 900})
    open_list(list_page)

    overflow = list_page.evaluate(
        """() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth,
        })"""
    )

    assert overflow["scroll"] <= overflow["client"], f"桌面尺寸下仍需橫向捲動:{overflow}"


# =======================================================================
# tasks 4.1:自列表進入對局(requirements 4.1、4.2)
# =======================================================================
#
# 交接只有一個契約:`/play.html?id=<題號>`(tasks 3.1 定案),題號就是列上的
# `data-id`(tasks 3.2 的結構契約),不另起一套識別。
#
# 本節分成兩層,兩層都必要:
#
# 1. **每一列指向的是它自己那一題** —— 以連結位址逐列比對。這一層抓得到「一律指向
#    第一題」與「拿陣列索引當題號」兩種寫法,而它們在只點第一題的測試下都是全綠的
#    (第一題的題號恰好是 1,也恰好是索引 0 加一)。
# 2. **對局介面載入的確實是那一題** —— 真的點下去、真的走完導航,再看對局頁的
#    局名與它實際請求的題號。完成狀態明寫「以題目資訊比對,不是只看有沒有開啟」:
#    網址對了但頁面載入別題(例如對局頁讀錯參數)只有這一層看得出來。


#: 對局介面的路徑(tasks 3.1 定案)。
PLAY_PATH = "/play.html"

#: 題號有缺口的一份索引 —— 題庫刪過題、或 corpus-verification 篩掉中間幾題之後
#: 就長這樣。**列的順序與題號在此完全對不上**,拿列的位置當題號會直接指錯題。
GAPPED_CATALOG: list[dict[str, Any]] = [
    {"id": 1, "title": "首局", "difficulty": 1, "tags": [], "source": "適情雅趣"},
    {"id": 2, "title": "次局", "difficulty": 2, "tags": [], "source": "適情雅趣"},
    {"id": 201, "title": "末局", "difficulty": 3, "tags": [], "source": "適情雅趣"},
]


def play_response(entry: dict[str, Any]) -> dict[str, Any]:
    """合成 `GET /api/positions/{id}` 的回應,**局名取自同一份索引夾具**。

    形狀取自 `test_web_play.py` 的實測回應。局名共用夾具是刻意的:斷言因此讀成
    「點第 N 題,對局頁就顯示第 N 題的局名」,而不是去比一個另外寫死的字串。
    """
    return {
        "id": entry["id"],
        "title": entry["title"],
        "description": entry.get("description", ""),
        "fen": PUZZLE_FEN,
        "side_to_move": "red",
        "difficulty": entry.get("difficulty"),
        "tags": entry.get("tags", []),
        "max_dtm": 9,
        "source": entry.get("source", ""),
        "state": {
            "side_to_move": "red",
            "legal_moves": START_LEGAL,
            "over": False,
            "winner": None,
        },
    }


def route_api(
    page,
    positions: list[dict[str, Any]] | None = None,
    *,
    black_move: dict[str, Any] | None = None,
    catalog_body: Any = None,
) -> dict[str, Any]:
    """索引、題目與應手三個端點由**同一個**處理器接手,並記下各自被要了幾次。

    非合併不可:Playwright 後註冊的路由優先,`/api/**` 各註冊一條只有最後那條會
    生效,對局頁的題目請求會落回夾具的 404。

    回傳的 `positions` 是**對局頁實際要了哪幾題**,依序。這是「載入的確實是那一題」
    最直接的證據 —— 局名比對之外再加一層,連「畫面對了但要了別題」都排除掉。
    `catalog` 則是索引被要了幾次:「下一題」那組測試靠它證明索引是**終局之後**才去
    要的,而不是每次開對局頁都多打一次。

    `black_move` 給了才接應手端點 —— 沒有它的測試根本不走子,而一條永遠回 200 的
    應手路由會讓「這一手真的送出去了嗎」變得看不出來。`catalog_body` 則讓索引可以
    單獨壞掉(題目端點照常活著),那是「索引取不到」那條路徑唯一的造法。
    """
    entries = CATALOG if positions is None else positions
    by_id = {str(entry["id"]): entry for entry in entries}
    served: dict[str, Any] = {"catalog": 0, "positions": [], "black_move": 0}

    def handler(route) -> None:
        path = urlsplit(route.request.url).path
        if path == CATALOG_PATH:
            served["catalog"] += 1
            body: Any = catalog_of(entries) if catalog_body is None else catalog_body
        elif path == "/api/black-move":
            if black_move is None:
                route.fulfill(status=404, content_type="text/plain", body="not found")
                return
            served["black_move"] += 1
            body = black_move
        elif path.startswith("/api/positions/"):
            requested = path.rsplit("/", 1)[-1]
            served["positions"].append(requested)
            entry = by_id.get(requested)
            if entry is None:
                route.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps({"detail": "not found"}),
                )
                return
            body = play_response(entry)
        else:
            route.fulfill(status=404, content_type="text/plain", body="not found")
            return
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route(f"{ORIGIN}/api/**", handler)
    return served


def open_list_that_can_be_played(
    page, positions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """開啟列表頁,且**點進去之後對局頁也活著** —— 兩個端點都備妥。"""
    served = route_api(page, positions)
    page.goto(f"{ORIGIN}/")
    page.wait_for_selector("#positions > li")
    return served


def row_links(page) -> list[dict[str, Any]]:
    """每一列的題號,以及那一列上通往對局的連結(原樣與解析後的位址)。"""
    return page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => {
          const link = li.querySelector('a[href]');
          return {
            id: li.dataset.id ?? null,
            href: link?.getAttribute('href') ?? null,
            resolved: link?.href ?? null,
            links: li.querySelectorAll('a[href]').length,
          };
        })"""
    )


def open_position(page, position_id: Any) -> None:
    """自列表點進某一題 —— 真實點擊那個連結,不是直接改網址。"""
    page.locator(f'#positions > li[data-id="{position_id}"] a[href]').click()


def played_title(page) -> str:
    """對局介面此刻顯示的局名。"""
    page.wait_for_selector("#board svg .piece")
    return page.locator("#puzzle-title").inner_text().strip()


# --- 4.1:每一列指向的是它自己那一題 ------------------------------------


def test_every_row_offers_a_link_into_its_own_position(list_page) -> None:
    """4.1:每一列都有一個通往**該題**對局介面的連結,位址即 `?id=` 契約。

    逐列比對是有理由的:只驗第一列的話,「一律指向第一題」與「拿陣列索引當題號」
    兩種寫法都會全綠 —— 第一題的題號恰好既是 1 也是索引 0 加一。
    """
    open_list(list_page)

    links = row_links(list_page)

    assert [link["id"] for link in links] == LISTED_IDS
    for link in links:
        assert link["href"], f"第 {link['id']} 題沒有通往對局的連結"
        assert link["resolved"] == f"{ORIGIN}{PLAY_PATH}?id={link['id']}", (
            f"第 {link['id']} 題的連結指向別處:{link['resolved']}"
        )
        # 原樣位址也要驗,不能只驗解析後的結果:服務今天掛在網域根目錄,`/play.html`
        # 與 `./play.html` 解析出來一模一樣,於是「改用絕對路徑」這種退化完全看不出來。
        # 一旦部署到子路徑底下,絕對路徑就會跳出整個應用,相對路徑才是唯一正確的。
        assert link["href"] == f".{PLAY_PATH}?id={link['id']}", (
            f"第 {link['id']} 題的連結不是相對位址:{link['href']}"
        )


def test_the_links_carry_the_real_ids_not_the_row_order(list_page) -> None:
    """4.1:題號取自題目本身,不是列的位置。

    題庫的題號**會有缺口**(刪題、或 corpus-verification 篩掉中間幾題),此時
    「第三列就是第三題」不成立。以位置推題號在今天的題庫(1、2、3、…)上完全
    看不出問題,收第二本書之後每一列都會指錯題。
    """
    open_list(list_page, GAPPED_CATALOG)

    resolved = [link["resolved"] for link in row_links(list_page)]

    assert resolved == [
        f"{ORIGIN}{PLAY_PATH}?id={entry['id']}" for entry in GAPPED_CATALOG
    ]


def test_the_link_is_as_tall_as_the_row_it_opens(list_page) -> None:
    """入口的點擊區域是整格,不是那幾個字那麼大。

    列與列之間只隔 8px,而標籤一多、那一列就會長高 —— 連結若只有一行文字高,列一
    高就變成「一條細細的可點區域浮在一大片死區中間」,點歪一點不是沒反應就是落到
    隔壁題上。

    夾具刻意給一題八個標籤讓那一欄折行:標籤少的時候局名本來就是全列最高的元素,
    這條斷言不論寫不寫都會成立 —— 那正是 tasks 3.2 那條假的金邊斷言犯過的錯。
    """
    crowded = [{**CATALOG[0], "tags": [f"標籤{index}" for index in range(8)]}]
    open_list(list_page, crowded)

    box = list_page.evaluate(
        """() => {
          const li = document.querySelector('#positions > li');
          const style = getComputedStyle(li);
          return {
            link: li.querySelector('a[href]').getBoundingClientRect().height,
            content:
              li.getBoundingClientRect().height
              - parseFloat(style.paddingTop)
              - parseFloat(style.paddingBottom),
          };
        }"""
    )

    # 這一列真的因為標籤而變高了,否則下面那條斷言什麼也沒證明。
    assert box["content"] > 40, f"夾具沒能讓這一列長高,斷言失去意義:{box}"
    assert box["link"] >= box["content"] - 0.5, (
        f"連結的點擊區域比整列矮:{box}"
    )


def test_a_row_offers_exactly_one_way_in(list_page) -> None:
    """一列只給一個入口。

    同一列擺兩個指向同一題的連結,輔助技術會逐一讀出來,鍵盤使用者也得多按一次
    Tab 才跳得過一列 —— 掃視一份長列表時,那是每一列都要付一次的代價。
    """
    open_list(list_page)

    counts_per_row = {link["id"]: link["links"] for link in row_links(list_page)}

    assert counts_per_row == {row_id: 1 for row_id in LISTED_IDS}


# --- 4.2:對局介面載入的確實是那一題 ------------------------------------


@pytest.mark.parametrize("position_id", ["1", "2", "5"])
def test_opening_a_position_loads_that_very_position(list_page, position_id) -> None:
    """4.2:點下去之後,對局介面載入的是**所選的那一題**。

    比的是題目資訊而非「有沒有開啟」:網址對了、頁面也開了,而載入的是別題
    (交接參數名寫錯、對局頁讀錯 query),只看有沒有棋盤完全分不出來。

    兩層證據都要:對局頁顯示的局名,以及題目端點**實際被要的那個題號**。前者
    確認使用者看到的是這一題,後者確認那不是巧合。
    """
    served = open_list_that_can_be_played(list_page)

    open_position(list_page, position_id)

    assert played_title(list_page) == TITLES[position_id]
    assert served["positions"] == [position_id], (
        f"對局頁要的題號不是所選的那一題:{served['positions']}"
    )
    assert urlsplit(list_page.url).path == PLAY_PATH


def test_opening_a_position_can_be_done_from_the_keyboard(list_page) -> None:
    """入口是一個真的連結,不是掛在某個元素上的 click 處理器。

    這條要的不是「Enter 也能用」這件小事,而是那一整組隨真連結一起來的東西 ——
    中鍵開新分頁、右鍵複製網址、瀏覽器的上一頁。自己寫 `location.href = …` 的話
    這些全部要各自重做一次,而通常不會做。
    """
    served = open_list_that_can_be_played(list_page)

    list_page.locator('#positions > li[data-id="3"] a[href]').press("Enter")

    assert played_title(list_page) == TITLES["3"]
    assert served["positions"] == ["3"]


def test_marking_a_position_complete_does_not_open_it(list_page) -> None:
    """完成標記與導航是兩個動作:按核取方塊**不得**把使用者丟進對局頁。

    整列可點是很自然的寫法,而完成標記就在那一列裡面 —— 想標記一題卻被丟進棋盤,
    每標一次就得按一次上一頁,列表也就沒法一路標下去。
    """
    served = open_list_that_can_be_played(list_page)

    toggle(list_page, 2)

    # 導航是非同步的:點完立刻斷言,真的跳走時也還來得及全綠。所有內容都在本機
    # 攔截下就地供應,離開這一頁只需要幾毫秒,這段等待綽綽有餘。
    list_page.wait_for_timeout(300)

    assert urlsplit(list_page.url).path == "/", "按完成標記卻離開了列表頁"
    assert served["positions"] == [], f"按完成標記卻載入了題目:{served['positions']}"

    state = {row["id"]: (row["checked"], row["marked"]) for row in rows(list_page)}
    assert state == {
        row_id: (True, True) if row_id == "2" else (False, False)
        for row_id in LISTED_IDS
    }


# =======================================================================
# tasks 4.2:自對局返回列表,並在對局介面顯示出處(requirements 4.3、4.4、4.5)
# =======================================================================
#
# ## 為什麼這一節在列表的測試檔,而不是 `test_web_play.py`
#
# 1. **往返的兩端都要真的走過**(4.4)。完成標記存在 localStorage,而它是 per-origin
#    的 —— 只有在同一個 origin 底下開列表、標記、進對局、再按返回,才驗得到「返回
#    後標記仍在」。`test_web_play.py` 另有自己的 origin,在那裡只讀得到一次儲存區,
#    證明不了往返;而「只讀一次儲存區」的測試連返回途徑根本不存在都抓不到。
# 2. **出處是自列表移過來的**(1.2 / 4.5)。同一份 `CATALOG` 夾具既是「不得出現在
#    列表上」那批字串的來源(`NEVER_ON_THE_LIST`),也是「必須出現在對局介面上」的
#    來源 —— 兩邊比對同一批字串,搬家有沒有搬到位一眼看得出來。分兩個檔各寫一份
#    夾具,兩份遲早各自漂移。**描述則是兩頁都不畫**(4.5 修訂後),同一份夾具因此
#    也是「兩邊都找不到」的來源。

#: 列表頁的位址。返回的落點就是它。
LIST_PATH = "/index.html"

#: 對局介面上那條返回連結的 id。與 `data-id`、`#puzzle-source` 同性質:是
#: `web/app.js` 自己畫樣式與導航要用的結構契約,不是測試專用的鉤子。
BACK_LINK_ID = "back-to-list"

#: 題號到出處的對照。夾具橫跨兩本書,「一律顯示某一本」的寫法因此躲不掉。
SOURCES = {str(entry["id"]): entry["source"] for entry in CATALOG}

#: 題號到描述的對照。每一題的描述與它的局名沒有共同字串,因此「描述不該出現」那幾條
#: 不會被局名誤觸。4.5 修訂後這份對照的用途反了過來:它現在是**不得出現**的那一批。
DESCRIPTIONS = {str(entry["id"]): entry["description"] for entry in CATALOG}


def play_view(page) -> dict[str, Any]:
    """對局介面畫出來之後的題目資訊與返回途徑。

    `bodyText` 取的是 `innerText` 而非 `innerHTML`:4.5 要的是使用者**看得到**
    出處,而 `display: none` 的節點仍留在 DOM 裡 —— 讀 HTML 的話,一個看不見的
    節點也算數。(tasks 3.2 在反方向踩過同一個坑。)

    `description` 照樣讀:4.5 修訂後那個節點應該**根本不存在**,而這裡回 `None`
    正是驗它不在的方式。
    """
    page.wait_for_selector("#board svg .piece")
    return page.evaluate(
        """(backId) => {
          const text = (id) => document.getElementById(id)?.textContent.trim() ?? null;
          const link = document.getElementById(backId);
          return {
            title: text('puzzle-title'),
            source: text('puzzle-source'),
            description: text('puzzle-description'),
            back: link && {
              href: link.getAttribute('href'),
              resolved: link.href,
              text: link.textContent.trim(),
              width: link.getBoundingClientRect().width,
            },
            bodyText: document.body.innerText,
          };
        }""",
        BACK_LINK_ID,
    )


def go_back(page) -> None:
    """自對局介面按下返回 —— 真實點擊那個連結,不是改網址也不是按上一頁。"""
    page.locator(f"#{BACK_LINK_ID}").click()
    page.wait_for_selector("#positions > li")


# --- 4.3:對局介面提供返回列表的途徑 ------------------------------------


def test_the_play_page_offers_a_way_back_to_the_list(list_page) -> None:
    """4.3:對局介面提供返回列表的途徑,而且是一個**真的連結**。

    與列表那一側同樣的理由(4.1):`<a href>` 免費附帶中鍵開新分頁、右鍵複製
    網址、Enter 鍵與瀏覽器的上一頁;攔 click 再改 `location` 的話這些要一一重做,
    而通常不會做。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)

    back = play_view(list_page)["back"]

    assert back is not None, "對局介面上沒有返回列表的途徑"
    assert back["text"], "返回的連結沒有可讀的文字"
    assert back["width"] > 0, "返回的連結排不出可見的尺寸"
    assert back["resolved"] == f"{ORIGIN}{LIST_PATH}", (
        f"返回的連結指向別處:{back['resolved']}"
    )


def test_the_way_back_is_dressed_in_the_pages_own_colours(list_page) -> None:
    """返回的連結接上了對局頁的視覺語言,不是瀏覽器預設的藍紫底線。

    這一條看似只是外觀,但沒上樣式的連結在這個橘色調介面裡是**唯一的藍紫**,
    看起來就像功能沒接完 —— 而它恰恰是本 spec 在對局頁上唯一的新控制項。

    比對的是解析後的實際顏色與側欄其餘說明文字相同,而不是斷言某個色碼字面值:
    調色盤日後改了,這條測試該跟著改的對象是「一致」而不是「等於 #9e9eff」。
    底線另外驗,因為 `color` 對了而底線還在的話,它看起來仍然不屬於這個側欄。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)
    # 導覽是非同步的:少了這個等待,`evaluate` 會在舊頁面或空白頁上跑,`getElementById`
    # 回 null 而測試以 TypeError 收場 —— 那看起來像紅燈,其實什麼都沒驗到。
    play_view(list_page)

    looks = list_page.evaluate(
        """(backId) => {
          const link = document.getElementById(backId);
          const reference = document.querySelector('#puzzle-info p');
          const seen = getComputedStyle(link);
          return {
            colour: seen.color,
            muted: getComputedStyle(reference).color,
            underline: seen.textDecorationLine,
          };
        }""",
        BACK_LINK_ID,
    )

    assert looks["colour"] == looks["muted"], (
        f"返回的連結沒有接上側欄的顏色:{looks['colour']},側欄說明文字為 {looks['muted']}"
    )
    assert looks["underline"] == "none", (
        f"返回的連結仍帶著瀏覽器預設的底線:{looks['underline']}"
    )


def test_the_way_back_is_a_relative_address(list_page) -> None:
    """返回的位址是相對的,不是 `/index.html`。

    服務今天掛在網域根目錄,`/index.html` 與 `./index.html` 解析出來一模一樣,
    於是「改用絕對路徑」這種退化完全看不出來 —— 一旦部署到子路徑底下,絕對路徑
    會把使用者丟出整個應用。4.1 的 review 在列表那一側抓到同一個洞並補了斷言,
    這裡是它的另一半。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)

    href = play_view(list_page)["back"]["href"]

    assert href == f".{LIST_PATH}", f"返回的連結不是相對位址:{href}"


def test_going_back_lands_on_the_list(list_page) -> None:
    """4.3:按下去真的回得到列表,而不只是有條連結掛在那裡。"""
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)
    play_view(list_page)

    go_back(list_page)

    assert urlsplit(list_page.url).path == LIST_PATH
    assert [row["id"] for row in rows(list_page)] == LISTED_IDS


def test_the_way_back_survives_a_position_that_fails_to_load(list_page) -> None:
    """4.3 沒有前提:題目載不起來時**更**需要一條回得去的路。

    載入失敗的畫面上原本只有「重來」一顆按鈕,而它在沒有題目時做的是重試載入
    (`web/app.js` 的既有行為)—— 題目根本不存在的話,使用者按到天亮也出不去,
    正是 web-play-runtime 的 requirements 7.4 禁止的無法復原畫面。
    """
    route_api(list_page)
    list_page.goto(f"{ORIGIN}{PLAY_PATH}?id=9999")
    list_page.wait_for_selector("#error:not([hidden])")

    link = list_page.locator(f"#{BACK_LINK_ID}")
    assert link.count() == 1, "載入失敗的畫面上沒有返回列表的途徑"
    assert link.get_attribute("href") == f".{LIST_PATH}", "返回的連結不是相對位址"

    link.click()
    list_page.wait_for_selector("#positions > li")


# --- 4.4:返回之後完成標記仍在 ------------------------------------------


def test_the_marks_survive_a_round_trip_through_a_game(list_page) -> None:
    """4.4:自對局返回列表之後,先前的完成標記仍在。

    **整趟都要真的走過** —— 在列表上按下標記、點進對局、等盤面畫出來、再按返回
    那條連結。只讀一次儲存區證明不了任何事:同一個 origin 底下 localStorage 本來
    就留著,那樣寫連「返回途徑根本不存在」都是全綠的。

    三層一起驗:畫面上的勾選與呈現掛勾、兩個計數,以及儲存區裡的原始值。少了最後
    一層的話,「返回後畫面對了但儲存區已被清掉」要到下一次重新載入才會浮現。
    """
    served = open_list_that_can_be_played(list_page)

    toggle(list_page, 2)
    toggle(list_page, 5)
    before = counts(list_page)

    open_position(list_page, 3)
    assert play_view(list_page)["title"] == TITLES["3"], "沒有真的進到對局介面"

    go_back(list_page)

    marked = {"2", "5"}
    state = {row["id"]: (row["checked"], row["marked"]) for row in rows(list_page)}
    assert state == {
        row_id: (row_id in marked, row_id in marked) for row_id in LISTED_IDS
    }, f"返回列表之後標記不見了:{state}"
    assert counts(list_page) == before == ["2", TOTAL]
    assert json.loads(stored_completed(list_page)) == [2, 5]
    assert served["positions"] == ["3"], f"中途載入的不是所選的那一題:{served}"


# --- 4.5(修訂後):對局介面顯示該題的出處,描述兩頁都不畫 ---------------
#
# 4.5 原本要求對局介面顯示出處**與描述**。使用者看過實際畫面後推翻了描述那一半:
# 真實題庫的 `description` 是「出處 + 局號 + 局名」的串接,與 `h1` 的局名和它下面
# 那一行的出處完全重複。requirements.md 4.5 與 tasks.md 4.2 皆已隨之修訂。


@pytest.mark.parametrize("position_id", ["1", "3", "5"])
def test_the_play_page_shows_the_source_of_that_very_position(
    list_page, position_id
) -> None:
    """4.5:對局介面顯示的出處是**那一題的**,不是寫死的一本書。

    夾具橫跨兩本書(第 1 題《適情雅趣》,第 3、5 題《橘中秘》),因此「一律顯示
    適情雅趣」這種寫法只在第一題全綠。這正是出處自列表移過來之後仍要顯示的理由:
    題庫收第二本書之後,分不出眼前這局出自哪一本就沒有意義。

    出處**取自 `GET /api/positions/{id}` 的回應**(`service/models.py` 的
    `PositionResponse.source`),不另打 `/api/catalog` —— 為一個字串多一次往返之外,
    兩個端點對同一題給出不同出處時,列表與對局頁會各說各話。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, position_id)

    view = play_view(list_page)
    expected = SOURCES[position_id]

    assert view["source"] == expected, f"第 {position_id} 題的出處不對:{view['source']}"
    assert expected in view["bodyText"], "出處在 DOM 裡,但使用者看不到"


@pytest.mark.parametrize("position_id", ["1", "3", "5"])
def test_the_play_page_does_not_repeat_the_description(list_page, position_id) -> None:
    """4.5(修訂後):描述**兩頁都不畫**。

    4.2 才剛把描述加進對局介面,現在整條拿掉 —— 這是刻意的,不是漏改。理由是使用者
    看過實際畫面之後說的那句「文字累贅」:真實題庫的 `description` 就是「出處 +
    局號 + 局名」的串接,而局名已經在 `h1`、出處就在它下面一行,同樣的字讀三遍。

    斷言分兩層:那個節點不存在(沒有被藏起來而已),以及那串字在使用者看得到的
    文字裡一次都沒出現(沒有換個地方再畫一次)。夾具裡每一題的描述與它的局名沒有
    共同字串,因此第二層不會被局名誤觸。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, position_id)

    view = play_view(list_page)

    assert view["description"] is None, (
        f"對局介面又長出了描述節點:{view['description']!r}"
    )
    assert DESCRIPTIONS[position_id] not in view["bodyText"], (
        f"第 {position_id} 題的描述又跑回對局介面上了"
    )
    assert "描述" not in view["bodyText"], "側欄還留著「描述」這個名目"


def test_the_source_moved_rather_than_multiplied(list_page) -> None:
    """出處是**自列表移到對局介面**(1.2 / 4.5),不是兩邊都畫。

    這一條與 `test_the_list_shows_neither_source_nor_description` 是同一件事的兩半:
    那一條釘住「列表上找不到」,這一條釘住「對局介面上找得到」。只寫前者的話,
    把出處整個弄丟也是全綠的。

    描述則是**兩頁都不畫**(見上一條),故這裡順帶釘住它在列表那一側也沒有回來。
    """
    open_list_that_can_be_played(list_page)
    listed = list_page.evaluate("() => document.getElementById('positions').innerHTML")

    open_position(list_page, 1)
    played = play_view(list_page)["bodyText"]

    assert SOURCES["1"] not in listed and DESCRIPTIONS["1"] not in listed
    assert SOURCES["1"] in played
    assert DESCRIPTIONS["1"] not in played


def test_the_meta_line_is_nothing_but_the_source(list_page) -> None:
    """那一行 meta **只剩出處**(使用者看過實際畫面後的第三輪意見)。

    這一條原本叫 `test_the_source_and_mate_distance_share_a_single_line`,驗的是
    出處與最長殺著併在同一行上。**最長殺著整組移除之後那件事沒有對象了**,斷言因此
    反過來:那一行從頭到尾就是出處本身,沒有分隔點、沒有「最長殺著」、也沒有任何
    名目(web-play-runtime 的 requirement 1.3 已推翻)。

    比對用 `==` 而非 `startswith` 是刻意的:後者對「適情雅趣 · 最長殺著 9」照樣
    成立,而那正是要擋掉的形態。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)
    list_page.wait_for_selector("#board svg .piece")

    line = list_page.evaluate(
        """() => ({
          meta: document.getElementById('puzzle-meta')?.innerText.trim() ?? null,
          dtm: document.getElementById('puzzle-max-dtm') !== null,
          source: document.getElementById('puzzle-source')?.innerText.trim() ?? null,
        })"""
    )

    assert line["meta"] is not None, "側欄沒有那一行 meta"
    assert line["source"] == SOURCES["1"], f"那一行的出處不對:{line}"
    assert line["meta"] == SOURCES["1"], f"那一行不只有出處:{line}"
    assert not line["dtm"], "最長殺著那一格還在"


# =======================================================================
# 跨題導航:紅方獲勝之後的「下一題」
# =======================================================================
#
# 這一節屬 problem-browser(跨題導航是本 spec 的事),但改的是 `web/app.js` ——
# tasks 4.2 那句「這是本 spec 唯一改動 `web/app.js` 的地方」因此不再成立,已在
# tasks.md 更正。
#
# **為什麼一定要合成索引才驗得到。** 真實題庫只有 1 題,對真實服務而言永遠沒有
# 下一題 —— 「有下一題」那條路徑在真實服務上一次都走不到。合成多題索引是唯一的
# 造法(改題庫不是選項:那會讓每一條依賴真實題庫的測試跟著漂)。真實服務那一側
# 走到的是「已是最後一題」,由 `tests/test_web_e2e.py` 覆蓋。
#
# **題號有缺口是這一節的核心。** `CATALOG` 的題號是 1、2、3、5(第 4 局還沒收)
# —— 第 3 題的下一題是**第 5 題**,不是第 4 題。以 `id + 1` 取代查索引的寫法會在
# 這裡直接撞牆,而在連號的夾具上完全看不出來。

#: 「下一題」那個控制項的 id(`web/app.js` 的 `mountNextLink`)。與 `#back-to-list`
#: 同性質:是 `app.js` 自己畫樣式與導航要用的結構契約,不是測試專用的鉤子。
NEXT_LINK_ID = "next-position"

#: 排局的最後一手:紅方這一手就將死黑方 —— 黑方無應手、對局結束、紅方勝。
RED_WINS = black_reply(move=None, signal="red_winning", mate_in=0, over=True, winner="red")

#: 走脫之後被將死:黑方有應手、對局結束、**黑方勝**。使用者這一局輸了。
BLACK_WINS = black_reply(
    move="e9d9", signal="black_winning", mate_in=0, over=True, winner="black"
)

#: 對局進行中的一手應手 —— 終局尚未到來,「下一題」在此不得出現。
GAME_CONTINUES = black_reply(move="e9d9", signal="red_winning", mate_in=4)

#: 終局之後 `#turn` 各自該說的話(`web/app.js` 的 `turnText`)。
YOU_WON = "你獲勝"
BLACK_WON = "黑方勝"


def play_one_move(page, expected_turn: str) -> None:
    """在對局頁走一手(`d8` -> `d9`),等到 `#turn` 說出 `expected_turn` 為止。

    等的是**終局後的那句話**而不是「等待態解除」:後者在點擊當下就已經為真過一瞬,
    拿它當沉澱信號會讓斷言在回應還在路上時就跑起來(problem-browser 5.2 的 review
    在同一件事上留過一筆)。

    **等不到時自己說出原因。** 「查索引把 `#turn` 弄壞了」這種退化會讓這個等待永遠
    等不到,而 Playwright 只會說「wait_for_function 逾時」—— 真正說得出原因的那句話
    永遠跑不到(problem-browser 5.2 的 review 記下的同一件事)。把當下的 `#turn`
    與錯誤區印出來,診斷才落在退化本身而不是等待機制上。
    """
    page.wait_for_selector("#board svg .piece")
    click_square(page, "d8")
    click_square(page, "d9")
    try:
        page.wait_for_function(
            "expected => document.getElementById('turn')?.textContent.trim() === expected",
            arg=expected_turn,
        )
    except PlaywrightTimeoutError:
        seen = page.evaluate(
            """() => ({
              turn: document.getElementById('turn')?.textContent.trim() ?? null,
              error: document.getElementById('error')?.textContent.trim() ?? null,
              moves: document.querySelectorAll('#moves li').length,
            })"""
        )
        pytest.fail(f"走完一手後 #turn 沒有變成 {expected_turn!r},畫面上是:{seen}")


def open_and_win(
    page,
    position_id: Any,
    *,
    positions: list[dict[str, Any]] | None = None,
    reply: dict[str, Any] | None = None,
    expected_turn: str = YOU_WON,
    catalog_body: Any = None,
) -> dict[str, Any]:
    """直接開某一題的對局頁並走一手,讓後端回報這一手就結束對局。

    不經列表:這一節要看的是**對局頁終局之後**的畫面,從列表點進來只是多繞一段
    已經被 4.1 那一節驗過的路。
    """
    served = route_api(
        page,
        positions,
        black_move=RED_WINS if reply is None else reply,
        catalog_body=catalog_body,
    )
    page.goto(f"{ORIGIN}{PLAY_PATH}?id={position_id}")
    play_one_move(page, expected_turn)
    return served


def wait_for_catalog(page, served: dict[str, Any]) -> None:
    """等到索引真的被要過一次為止。"""
    for _ in range(100):
        if served["catalog"] >= 1:
            return
        page.wait_for_timeout(50)
    raise AssertionError("終局之後從未去要索引")


def next_link(page) -> dict[str, Any] | None:
    """「下一題」此刻的樣子;沒有這個節點或它藏著時回 `None`。"""
    return page.evaluate(
        """(id) => {
          const link = document.getElementById(id);
          if (!link || link.hidden) return null;
          return {
            href: link.getAttribute('href'),
            resolved: link.href,
            text: link.textContent.trim(),
            tag: link.tagName,
            width: link.getBoundingClientRect().width,
          };
        }""",
        NEXT_LINK_ID,
    )


# --- 有下一題 -----------------------------------------------------------


def test_a_red_win_offers_the_next_position(list_page) -> None:
    """使用者獲勝之後,「重來」旁邊多一個通往下一題的途徑。

    合成索引是必要的:真實題庫只有 1 題,這條路徑在真實服務上永遠走不到。
    """
    open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    link = next_link(list_page)

    assert link is not None, "紅方獲勝之後沒有「下一題」"
    assert link["text"] == "下一題", f"「下一題」的說法不對:{link}"
    assert link["width"] > 0, "「下一題」排不出可見的尺寸"
    assert link["resolved"] == f"{ORIGIN}{PLAY_PATH}?id=2", (
        f"「下一題」指向別處:{link}"
    )


def test_the_next_position_is_a_real_relative_link(list_page) -> None:
    """它是**真的 `<a href>`** 而且位址是相對的。

    兩件事各有理由,而且都被同一個夾具驗到:

    - `<a href>` 而非 `<button>`(problem-browser 4.1 已確立的形態):中鍵開新分頁、
      右鍵複製網址、Enter 鍵全部免費附帶,攔 click 再改 `location` 要一一重做;
    - **相對位址**:服務今天掛在網域根目錄,`/play.html?id=2` 與 `./play.html?id=2`
      解析出來一模一樣,所以「改用絕對路徑」這種退化只有比對**原樣** `href` 才看得
      出來 —— 一旦部署到子路徑底下,絕對路徑會把使用者丟出整個應用。4.1 的 review
      在列表那一側抓過同一個洞。
    """
    open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    link = next_link(list_page)

    assert link["tag"] == "A", f"「下一題」不是連結而是 {link['tag']}"
    assert link["href"] == f".{PLAY_PATH}?id=2", f"「下一題」不是相對位址:{link}"


def test_the_next_position_skips_a_gap_in_the_numbering(list_page) -> None:
    """下一題是**索引裡題號大於當前題的最小題號** —— 不是 `id + 1`。

    夾具沒有第 4 題,因此第 3 題的下一題是**第 5 題**。`id + 1` 會指到第 4 題,
    而索引裡根本沒有那一題 —— 使用者按下去只會看到「找不到這一題」。

    本專案的題號有缺口是實情而非假想:題庫按局號收題,收到哪一局就是哪一局,
    `PositionRepository.all()` 的存在正是為了這件事。
    """
    open_and_win(list_page, 3)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    link = next_link(list_page)

    assert link["href"] == f".{PLAY_PATH}?id=5", (
        f"下一題不是可上架的最小題號(第 4 題不可上架):{link}"
    )


def test_the_next_position_actually_opens_that_position(list_page) -> None:
    """按下去真的到得了那一題,而不只是有條連結掛在那裡。"""
    served = open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    list_page.locator(f"#{NEXT_LINK_ID}").click()
    list_page.wait_for_selector("#board svg .piece")

    assert urlsplit(list_page.url).path == PLAY_PATH
    assert list_page.locator("#puzzle-title").inner_text().strip() == TITLES["2"]
    assert served["positions"][-1] == "2", (
        f"對局頁最後要的不是第 2 題:{served['positions']}"
    )


# --- 沒有下一題 ---------------------------------------------------------


def test_the_last_position_offers_no_next(list_page) -> None:
    """已是最後一題時**按鈕不出現** —— 不顯示一個按了沒反應的東西。

    第 5 題是夾具裡可上架的最後一題(第 4 題不可上架,而它的題號還比 5 小)。
    """
    served = open_and_win(list_page, 5)
    wait_for_catalog(list_page, served)
    list_page.wait_for_timeout(300)

    assert next_link(list_page) is None, "已是最後一題,卻仍冒出「下一題」"
    assert text_of(list_page, "#turn") == YOU_WON, "終局畫面本身被影響到了"


def test_a_black_win_offers_no_next_position(list_page) -> None:
    """走脫導致黑勝時**不出現** —— 那時該做的是重來,不是跳下一題。

    這一條同時釘住「索引根本不該被去要」:黑勝不是通關,連查都不必查。只驗按鈕
    沒出現的話,「照樣查索引但畫面上藏起來」也是全綠的。
    """
    served = open_and_win(list_page, 1, reply=BLACK_WINS, expected_turn=BLACK_WON)
    list_page.wait_for_timeout(300)

    assert next_link(list_page) is None, "黑方獲勝卻冒出了「下一題」"
    assert served["catalog"] == 0, "黑方獲勝時仍去要了索引"
    assert list_page.locator("#reset").is_visible(), "黑方獲勝時該留下的是「重來」"


def test_a_game_still_in_progress_offers_no_next_position(list_page) -> None:
    """對局進行中不出現 —— 這一題都還沒解完。"""
    served = route_api(list_page, black_move=GAME_CONTINUES)
    list_page.goto(f"{ORIGIN}{PLAY_PATH}?id=1")
    list_page.wait_for_selector("#board svg .piece")
    click_square(list_page, "d8")
    click_square(list_page, "d9")
    list_page.wait_for_function(
        "() => document.querySelectorAll('#moves li .mv-b')[0]?.textContent"
    )

    assert next_link(list_page) is None, "對局還沒結束就冒出了「下一題」"
    assert served["catalog"] == 0, "對局進行中就去要了索引"


def test_a_broken_catalog_leaves_the_win_screen_intact(list_page) -> None:
    """索引取不到時**按鈕不出現,而且終局畫面的其餘部分一個字都不受影響**。

    跨題導航是次要功能。它壞掉不該讓使用者連「你獲勝」都看不到 —— 這是本節唯一
    一條在驗「壞得夠安靜」的測試,故斷言不只看按鈕:輪方、錯誤區、盤面、歷史著法
    逐項都要照常。

    索引**單獨**壞掉、題目端點照常活著,是刻意的:那才是真實的失敗形狀(索引端點
    不借引擎,它掛掉與對局能不能打是兩件事)。
    """
    served = open_and_win(list_page, 1, catalog_body=BROKEN_INDEX)
    wait_for_catalog(list_page, served)
    list_page.wait_for_timeout(300)

    assert next_link(list_page) is None, "索引取不到,卻仍冒出「下一題」"
    assert text_of(list_page, "#turn") == YOU_WON, "索引壞掉把「你獲勝」弄不見了"
    assert list_page.locator("#error").is_hidden(), "索引壞掉冒出了對局的錯誤告知"
    assert list_page.locator("#waiting").is_hidden(), "索引壞掉讓畫面停在等待中"
    assert list_page.locator("#board svg .piece").count() > 0, "索引壞掉把盤面弄不見了"
    assert list_page.locator("#moves li").count() > 0, "索引壞掉把歷史著法弄不見了"
    assert list_page.locator("#reset").is_visible(), "索引壞掉把「重來」弄不見了"


# --- 何時去要索引 -------------------------------------------------------


def test_the_catalog_is_only_fetched_after_the_win(list_page) -> None:
    """索引是**紅勝之後**才去要的,不是載入題目時就要。

    在載入時就要的話,每一次開對局頁都多打一次請求,而絕大多數對局根本走不到獲勝。
    終局時才要是安全的:索引端點不碰引擎池(problem-browser 的 1.1)。
    """
    served = route_api(list_page, black_move=RED_WINS)
    list_page.goto(f"{ORIGIN}{PLAY_PATH}?id=1")
    list_page.wait_for_selector("#board svg .piece")
    list_page.wait_for_timeout(300)

    assert served["catalog"] == 0, "還沒獲勝就去要了索引"

    play_one_move(list_page, YOU_WON)
    wait_for_catalog(list_page, served)

    assert served["catalog"] == 1, f"索引被要了 {served['catalog']} 次"


# --- 版面:兩個控制項並排 -----------------------------------------------


def test_the_next_position_sits_to_the_right_of_the_restart(list_page) -> None:
    """「下一題」排在「重來」**右邊**,而且兩者在同一列上。

    「同一組控制項」在畫面上是有形的:同一列、緊鄰、等高。只驗左右順序的話,
    一個掉到下一段去也照樣成立。
    """
    open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    boxes = list_page.evaluate(
        """(id) => {
          const box = (selector) => {
            const { left, top, right, bottom, height } =
              document.querySelector(selector).getBoundingClientRect();
            return { left, top, right, bottom, height };
          };
          return { reset: box('#reset'), next: box('#' + id) };
        }""",
        NEXT_LINK_ID,
    )

    assert boxes["next"]["left"] >= boxes["reset"]["right"], (
        f"「下一題」沒有排在「重來」右邊:{boxes}"
    )
    assert abs(boxes["next"]["top"] - boxes["reset"]["top"]) < 2, (
        f"兩個控制項不在同一列上:{boxes}"
    )
    assert abs(boxes["next"]["height"] - boxes["reset"]["height"]) < 2, (
        f"兩個控制項不等高:{boxes}"
    )
    assert boxes["next"]["left"] - boxes["reset"]["right"] < 24, (
        f"兩個控制項隔得太開,看不出是同一組:{boxes}"
    )


def test_the_next_position_reads_as_the_primary_of_the_pair(list_page) -> None:
    """兩個控制項的主次分得出來:「下一題」是前進,「重來」是重試。

    主次由**填滿的強調色 vs. 中性灰**承擔,不是由位置或大小承擔。斷言比對的是
    「下一題的底色等於 `--accent`」而非某個色碼字面值 —— 調色盤日後改了,這條測試
    該跟著改的對象是「用的是強調色」而不是「等於 #ffd479」。

    另外驗兩者底色不同:少了這一層,「兩顆都是強調色」也會通過上一條。
    """
    open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    looks = list_page.evaluate(
        """(id) => {
          const paint = (selector) => {
            const style = getComputedStyle(document.querySelector(selector));
            return { background: style.backgroundColor, colour: style.color };
          };
          const root = getComputedStyle(document.documentElement);
          const probe = document.createElement('span');
          probe.style.color = 'var(--accent)';
          document.body.append(probe);
          const accent = getComputedStyle(probe).color;
          probe.remove();
          return { reset: paint('#reset'), next: paint('#' + id), accent };
        }""",
        NEXT_LINK_ID,
    )

    assert looks["next"]["background"] == looks["accent"], (
        f"「下一題」沒有用強調色當底:{looks}"
    )
    assert looks["next"]["background"] != looks["reset"]["background"], (
        f"兩個控制項同一個底色,主次分不出來:{looks}"
    )
    assert looks["next"]["colour"] != looks["reset"]["colour"], (
        f"兩個控制項同一個字色:{looks}"
    )
