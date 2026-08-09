"""列表頁骨架、入口路由,與列表的呈現與完成標記
(tasks 3.1、3.2;requirements 1.1、1.2、1.3、1.5、3.1、3.2、3.3、3.5、6.2、6.3)。

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

合成索引刻意同時擺了 `solvable` 為 `null`、欄位不存在與 `false` 三種情形:
**空值必須仍然列出**。corpus-verification 尚未回填,若把空值當成不可解,整個題庫
在它跑完之前都會是空的 —— 那是本檔最要緊的一條。

## 出處與描述是「拿了不畫」

`/api/catalog` 與 `catalog.js` 一直都帶著 `source` 與 `description`(日後收錄第二本書
時列表要靠它們;見 tasks.md 的 Backlog),但列表**不畫**這兩個欄位:列是掃視用的。
合成索引裡的描述與出處因此刻意與局名、標籤沒有任何共同字串,「有沒有畫出去」才驗
得出來。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from service.main import create_app
from test_web_page import SIMPLIFIED_ONLY_CHARACTERS

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
#: 五題涵蓋了本節要分辨的每一種情形:
#:
#: - 難度有 3、3、5、1、**0** 四種值;標籤「連將殺」橫跨多題,第 1 題帶兩個標籤
#: - `solvable`:1 為 `True`、2 為 `None`、3 完全沒有這個欄位、4 明確為 `False`
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
        "solvable": True,
    },
    {
        "id": 2,
        "title": "車馬冷著",
        "description": "紅先勝,冷著取勢",
        "difficulty": 3,
        "tags": ["連將殺"],
        "source": "適情雅趣",
        # corpus-verification 尚未驗到這一題 —— 空值,仍須列出。
        "solvable": None,
    },
    {
        "id": 3,
        "title": "棄子入局",
        "description": "紅先勝,棄子搶攻",
        "difficulty": 5,
        "tags": ["連將殺", "鬥快"],
        "source": "橘中秘",
        # 連欄位都沒有 —— 一樣是「尚未回填」,仍須列出。
    },
    {
        "id": 4,
        "title": "殘局存疑",
        "description": "尚未驗出著法的殘局",
        "difficulty": 1,
        "tags": ["雙包"],
        "source": "橘中秘",
        # 唯一明確不可解的一題 —— 任何情況下都不得出現。
        "solvable": False,
    },
    {
        "id": 5,
        "title": "一子解雙征",
        "description": "紅先勝,入門的單子解圍",
        # 難度 0:合法的下界,不得被 falsy 判斷吃掉。
        "difficulty": 0,
        # 沒有標籤 —— 那一欄的佔位路徑。
        "tags": [],
        "source": "橘中秘",
        "solvable": True,
    },
]

#: 扣掉明確不可解的第 4 題之後應該上架的題號,順序即索引的順序。
LISTED_IDS = ["1", "2", "3", "5"]

#: 總題數。由 `LISTED_IDS` 導出,夾具增減題目時不必逐條改斷言。
TOTAL = str(len(LISTED_IDS))

#: 題號到局名的對照,供無障礙名稱的斷言使用。
TITLES = {str(entry["id"]): entry["title"] for entry in CATALOG}

#: 不得出現在列表上的字串:全部五題的描述、兩個出處,以及不可解那一題的局名。
NEVER_ON_THE_LIST = [entry["description"] for entry in CATALOG] + [
    "適情雅趣",
    "橘中秘",
    "殘局存疑",
]

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
#     <input type="checkbox" class="position-toggle">
#     <span class="position-id">      <span class="position-title">
#     <span class="position-difficulty">  <span class="position-tags">
#
# `data-id` 是列與題號的對應,`data-completed` 是完成狀態的呈現掛勾;兩者都不是
# 測試專用的鉤子,列表本身要靠它們才畫得出樣式與導航(4.1)。


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
    """1.2:一列要有題號、局名、難度與標籤四項,缺一不可。"""
    open_list(list_page)

    drawn = {row["id"]: row["text"] for row in rows(list_page)}

    for entry in CATALOG:
        if str(entry["id"]) not in drawn:
            continue
        text = drawn[str(entry["id"])]
        assert str(entry["id"]) in text, f"第 {entry['id']} 題:列上看不到題號"
        assert entry["title"] in text, f"第 {entry['id']} 題:列上看不到局名"
        assert str(entry["difficulty"]) in text, f"第 {entry['id']} 題:列上看不到難度"
        for tag in entry["tags"]:
            assert tag in text, f"第 {entry['id']} 題:列上看不到標籤「{tag}」"


def test_the_list_shows_neither_source_nor_description(list_page) -> None:
    """1.2:出處與描述**不在列表呈現** —— 兩者已移到對局介面(4.5)。

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


# --- 1.4:不可解的題目不列入,空值仍須列出 ------------------------------


def test_a_position_marked_unsolvable_is_not_listed(list_page) -> None:
    """1.4:明確標為不可解的題目不列入。"""
    open_list(list_page)

    assert "4" not in [row["id"] for row in rows(list_page)]


def test_a_position_whose_solvable_flag_is_unfilled_is_still_listed(list_page) -> None:
    """1.4:空值視為可上架 —— `null` 與欄位不存在都算空值。

    這是最容易寫反的一條:`catalog.js` 已經做完過濾,`list.js` 若再加一層 truthy
    判斷,目前題庫(全部都還沒回填)會整個空掉。
    """
    open_list(list_page)

    listed = [row["id"] for row in rows(list_page)]

    assert "2" in listed, "solvable 為 null 的題目必須列出"
    assert "3" in listed, "沒有 solvable 欄位的題目必須列出"


def test_a_catalog_where_nothing_is_verified_yet_still_lists_everything(
    list_page,
) -> None:
    """1.4:整份索引都還沒回填時,列表**不得**變成空的。

    這是上一條的極端情形,也正是今天題庫的現實 —— corpus-verification 尚未跑過,
    每一題的 `solvable` 都是 `null`。列表在這裡空掉,產品就等於打不開。
    """
    unverified = [{**entry, "solvable": None} for entry in CATALOG]
    open_list(list_page, unverified)

    assert [row["id"] for row in rows(list_page)] == [
        str(entry["id"]) for entry in CATALOG
    ]
    assert list_page.locator("#empty").is_hidden()


# --- 3.1、3.2、3.5:完成標記與計數 --------------------------------------


def test_every_row_offers_a_completion_toggle(list_page) -> None:
    """3.1:每一題都有一個可切換的完成標記,初始為未完成。"""
    open_list(list_page)

    drawn = rows(list_page)

    assert [row["checked"] for row in drawn] == [False] * len(LISTED_IDS)
    assert [row["marked"] for row in drawn] == [False] * len(LISTED_IDS)


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
