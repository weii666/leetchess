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
from test_web_play import PUZZLE_FEN, START_LEGAL, black_reply, click_square

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
LIST_HTML = WEB_DIR / "index.html"


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


def played_heading(position_id: Any) -> str:
    """對局介面的 `<h1>` 對某一題應有的字面:「題號 + 點 + 空格 + 局名」。

    列表只畫局名(題號是它自己獨立的一欄),對局頁把兩者串成一行 —— 從列表點進來
    看到的是同一組資訊,一眼就確認自己開對了題(`web/app.js` 的 `puzzleHeading`)。
    """
    return f"{position_id}. {TITLES[str(position_id)]}"

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


#: 一份形狀認不得的索引回應 —— 對 `catalog.js` 而言是失敗,不是「題庫沒有題目」。
BROKEN_INDEX = {"items": []}

#: 索引端點的路徑(`service/main.py` 的 `read_catalog`)。
CATALOG_PATH = "/api/catalog"

# --- 3.2 的夾具與呼叫工具 -----------------------------------------------
#
# 列的結構契約(由 `web/list.js` 產生、`web/list.css` 據以上色):
#
#   <li class="position" data-id="<題號>" [data-completed]>
#     <span class="position-id">                          <- 「<題號>.」,靠右對齊
#     <a class="position-title" href="./play.html?id=<題號>">   <- 固定 8em(八個中文字)
#     <span class="position-tags">                         <- 靠左對齊,緊接局名
#     <span class="position-difficulty" [data-level="1|2|3"]>   <- 靠右對齊
#     <input type="checkbox" class="position-toggle">
#     <button class="position-star" data-starred="true|false">  <- 標星,見 web/starred.js
#
# **DOM 順序即左到右的欄序。** 難度緊鄰完成標記(不在局名旁邊)。六欄只有標籤欄是
# 彈性的,其餘寬度固定 —— 每個 `<li>` 各自是一個 grid,固定寬度是各列欄界能對齊成
# 直欄的唯一辦法。
#
# `data-id` 是列與題號的對應,`data-completed` 是完成狀態的呈現掛勾,`data-level`
# 是難度標籤的上色掛勾;三者都不是測試專用的鉤子,列表本身要靠它們才畫得出樣式與
# 導航(4.1)。
#
# **完成標記與星號都是 `<li>` 的直接子節點** —— 它們與 `<a>` 平行而非在其中,
# 「按標記/星號不會跳進對局頁」(4.1)因此是結構的結果。欄序改過幾次(完成標記
# 自最左移到最右、星號自最左移到全列最後,都是使用者看過實際畫面後的意見),
# 改的一律是 `append` 的順序而不是 CSS 的 `order`:純視覺搬移會讓 Tab 順序與
# 眼睛看到的分家。


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


@pytest.mark.parametrize(
    ("difficulty", "shown"),
    [
        # 0 是 `_read_int` 收得進來的下界,也是最容易被 falsy 判斷吃掉的值。
        (0, "0"),
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


# --- 3.1、3.2、3.5:完成標記與計數 --------------------------------------


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


# --- 1.5 與 Error Handling:空題庫與壞索引是兩個畫面 --------------------


def test_an_empty_catalog_tells_the_user_instead_of_showing_a_blank_page(
    list_page,
) -> None:
    """1.5:題庫沒有任何可列出的題目時告知使用者,而非呈現空白畫面。"""
    open_empty_list(list_page)

    assert list_page.locator("#empty").inner_text().strip() != ""
    assert list_page.locator("#positions > *").count() == 0
    assert counts(list_page) == ["0", "0"]


def test_a_broken_index_shows_the_error_region_not_the_empty_state(list_page) -> None:
    """Error Handling:索引取不到時給的是「題庫載入失敗」與重試,不是「題庫沒有題目」。

    這兩件事對使用者完全不同:一個是題庫真的還沒有題,另一個是東西壞了、重試有用。
    `catalog.js` 對認不得的回應形狀刻意拋 `CatalogError` 而不是給一份空陣列,正是
    為了讓這裡分得開;呈現層若把兩者收進同一個畫面,那份刻意就白費了。
    """
    open_broken_list(list_page)

    assert list_page.locator("#empty").is_hidden(), "索引壞掉卻說成題庫沒有題目"
    assert list_page.locator("#positions > *").count() == 0


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


# =======================================================================
# 每日推薦題(`web/daily.js` 的 `pickDailyPosition()` 畫成 `#daily-position`
# 那一列)
# =======================================================================
#
# `daily.js` 本身選中哪一題只是純函式邏輯,由 `test_web_daily.py` 驗證;這裡驗證
# `list.js` 怎麼把它**畫出來**:推薦列是否恰好一列、選中的題目與直接呼叫
# `pickDailyPosition()` 算出的是否一致(不能寫死是哪一題,挑哪一題取決於執行當下
# 的真實日期)、標籤欄是否換成「每日一題」而不洩漏原本的標籤(防劇透)、下面的
# 編號列表是否原樣保留同一題(「下面列表不變」),以及在推薦列操作完成標記/星號時
# 是否與下面的正常列同步、焦點是否留在使用者實際點下去的那個容器。

#: 推薦列標籤欄的固定文字(`list.js` 的 `FEATURED_LABEL`)。
FEATURED_LABEL = "每日一題"


def featured_row(page) -> dict[str, Any] | None:
    """`#daily-position` 目前畫出來的那一列;沒有推薦題時為 `None`。"""
    items = page.evaluate(
        """() => [...document.querySelectorAll('#daily-position > li')].map((li) => ({
          id: li.dataset.id ?? null,
          html: li.innerHTML,
          tags: [...li.querySelectorAll('.position-tags .position-tag, .position-tags .position-tag-featured')].map(
            (el) => el.textContent,
          ),
          checked: li.querySelector('input[type="checkbox"]')?.checked ?? null,
          marked: li.matches('[data-completed]'),
          starred: li.querySelector('.position-star')?.dataset.starred ?? null,
        }))"""
    )
    return items[0] if items else None


def expected_daily_id(page, positions: list[dict[str, Any]]) -> Any:
    """直接呼叫 `pickDailyPosition(positions, todayKey())`,算出「應該」被推薦的
    題號 —— 交叉驗證的依據,而不是把某個題號寫死在斷言裡。
    """
    return page.evaluate(
        """async ({ positions }) => {
          const { pickDailyPosition, todayKey } = await import('/daily.js');
          return pickDailyPosition(positions, todayKey())?.id ?? null;
        }""",
        {"positions": positions},
    )


def test_the_daily_position_shows_exactly_one_recommended_row(list_page) -> None:
    """推薦列剛好一列,且是題庫裡真的存在的題號。"""
    open_list(list_page)

    featured = featured_row(list_page)
    assert featured is not None, "推薦列沒有畫出任何題目"
    assert featured["id"] in LISTED_IDS


def test_the_daily_position_matches_pick_daily_position(list_page) -> None:
    """推薦列選中的題目與直接呼叫 `pickDailyPosition()` 算出來的一致。"""
    open_list(list_page)

    featured = featured_row(list_page)
    assert featured is not None

    expected = expected_daily_id(list_page, CATALOG)
    assert featured["id"] == str(expected)


def test_the_daily_position_hides_the_real_tags(list_page) -> None:
    """推薦列的標籤欄只有「每日一題」,不是題目原本的殺法名 —— 防劇透(1.2 的
    tags 若直接畫出去,等於告訴使用者這題怎麼殺)。
    """
    open_list(list_page)

    featured = featured_row(list_page)
    assert featured is not None
    assert featured["tags"] == [FEATURED_LABEL]

    entry = next(e for e in CATALOG if str(e["id"]) == featured["id"])
    for tag in entry["tags"]:
        assert tag not in featured["html"], f"推薦列洩漏了原本的標籤:{tag}"


def test_the_same_position_also_appears_in_the_normal_list(list_page) -> None:
    """下面編號列表完全不變:推薦題依然以原本的題號、原本的位置出現在正常列表裡,
    同一題因此在頁面上出現兩次。
    """
    open_list(list_page)

    featured = featured_row(list_page)
    assert featured is not None

    normal_ids = [row["id"] for row in rows(list_page)]
    assert featured["id"] in normal_ids
    assert normal_ids == LISTED_IDS


def test_toggling_from_the_daily_row_syncs_the_normal_row(list_page) -> None:
    """在推薦列勾選完成,下面同一題的正常列同步更新(反之亦然)——兩者共用同一份
    `completed` 狀態,`render()` 重畫時一起套用。
    """
    open_list(list_page)
    featured = featured_row(list_page)
    assert featured is not None
    position_id = featured["id"]

    list_page.locator(
        f'#daily-position > li[data-id="{position_id}"] input[type="checkbox"]'
    ).click()

    normal_state = next(row for row in rows(list_page) if row["id"] == position_id)
    assert normal_state["checked"] is True
    assert normal_state["marked"] is True

    toggle(list_page, int(position_id))

    normal_state = next(row for row in rows(list_page) if row["id"] == position_id)
    assert normal_state["checked"] is False
    assert featured_row(list_page)["checked"] is False


def test_starring_from_the_daily_row_syncs_the_normal_row(list_page) -> None:
    """在推薦列按星號,下面同一題的正常列同步變成已標星 —— 與完成標記同一套理由,
    共用同一份 `starred` 狀態。
    """
    open_list(list_page)
    featured = featured_row(list_page)
    assert featured is not None
    position_id = featured["id"]
    assert featured["starred"] == "false"

    list_page.locator(
        f'#daily-position > li[data-id="{position_id}"] .position-star'
    ).click()

    assert featured_row(list_page)["starred"] == "true"

    normal_starred = list_page.evaluate(
        f"""() => document
          .querySelector('#positions > li[data-id="{position_id}"] .position-star')
          ?.dataset.starred"""
    )
    assert normal_starred == "true"


def test_toggling_from_the_daily_row_keeps_focus_in_the_daily_row(list_page) -> None:
    """從推薦列按下完成標記,重畫後焦點留在推薦列裡的核取方塊,不會被拉去下面
    列表裡同題號的那一顆 —— 兩個容器現在可能有相同 `data-id`,`mark()`/`star()`
    的 `root` 參數就是為了解決這個焦點還原的問題(見 `list.js`)。
    """
    open_list(list_page)
    featured = featured_row(list_page)
    assert featured is not None
    position_id = featured["id"]

    list_page.locator(
        f'#daily-position > li[data-id="{position_id}"] input[type="checkbox"]'
    ).click()

    focused = list_page.evaluate(
        """() => {
          const el = document.activeElement;
          return {
            inDaily: !!el.closest('#daily-position'),
            inPositions: !!el.closest('#positions'),
            className: el.className,
          };
        }"""
    )
    assert focused["inDaily"] is True
    assert focused["inPositions"] is False
    assert focused["className"] == "position-toggle"


# =======================================================================
# 篩選:全部題目 / 我的最愛 / 難度三檔(`list.js` 的 `visiblePositions()`)
# =======================================================================
#
# 篩選只影響 `#positions` 那份列表與 `#progress` 的 m/n 計數,`#daily-position` 的
# 推薦列固定畫 `daily`,不吃篩選條件 —— 即使推薦題被篩選條件排除在外,它仍然要
# 出現(見 `list.js` 的 `render()`)。
#
# 難度篩選轉呼叫 `catalog.js` 既有、已測試的 `filterPositions()`;「我的最愛」比對
# 的是 `starred.js` 已載入的 `starred` 集合。兩者判準的出處都不在 `list.js`。
#
# 網址是狀態保留的唯一場所(從對局頁按上一頁回列表是整頁重新載入,沒有第二個地方
# 活得過那次導覽):選了篩選之後網址要立刻反映,直接帶著那個網址開新分頁也要能
# 還原同一個篩選條件。

#: 涵蓋三種難度(既有 `CATALOG` 的難度只有 3、5、0,沒有 1、2,篩不出簡單/適中)。
FILTER_CATALOG: list[dict[str, Any]] = [
    {
        "id": 201,
        "title": "簡明入局",
        "description": "紅先勝,單步照將",
        "difficulty": 1,
        "tags": ["入門"],
        "source": "測試題庫",
    },
    {
        "id": 202,
        "title": "縱橫捭闔",
        "description": "紅先勝,騰挪取勢",
        "difficulty": 2,
        "tags": ["中局"],
        "source": "測試題庫",
    },
    {
        "id": 203,
        "title": "背水一戰",
        "description": "紅先勝,棄子搶攻",
        "difficulty": 3,
        "tags": ["殘局"],
        "source": "測試題庫",
    },
    {
        "id": 204,
        "title": "背城借一",
        "description": "紅先勝,連將入局",
        "difficulty": 3,
        "tags": ["殘局"],
        "source": "測試題庫",
    },
]

#: 難度篩選值到分級的對照,與 `list.js` 的 `FILTER_DIFFICULTY` 是同一份判準。
FILTER_DIFFICULTY = {"easy": 1, "medium": 2, "hard": 3}


def filter_select(page):
    """篩選下拉的 `<select id="filter-select">` locator。"""
    return page.locator("#filter-select")


def choose_filter(page, value: str) -> None:
    """真實選取下拉選單裡的一個選項,不是直接改 DOM 的 `value`。"""
    filter_select(page).select_option(value)


#: 篩選條件的儲存鍵(`web/filter.js` 的 `STORAGE_KEY`)。
FILTER_STORAGE_KEY = "leetchess:v1:filter"


def stored_filter(page) -> Any:
    """`sessionStorage` 裡目前的篩選條件原始值(沒寫過時為 `None`)。"""
    return page.evaluate(f"() => sessionStorage.getItem({json.dumps(FILTER_STORAGE_KEY)})")


def seed_filter(page, value: str) -> None:
    """在導覽之前先把篩選條件寫進 `sessionStorage`——模擬「上一頁就是選了這個
    篩選」而不是使用者這次進來才選。必須在 `page.goto` **之前**呼叫,道理與
    `list_page` 夾具的說明一致:`list.js` 開頁時只讀一次。
    """
    page.add_init_script(
        f"sessionStorage.setItem({json.dumps(FILTER_STORAGE_KEY)}, {json.dumps(value)})"
    )


# --- 骨架 -----------------------------------------------------------------


def test_the_filter_bar_ships_five_options(page_client: TestClient) -> None:
    """篩選下拉的五個選項,文字與 value 都要對 —— 這是使用者唯一看得到的說法。"""
    html = LIST_HTML.read_text(encoding="utf-8")
    assert '<select id="filter-select">' in html
    for value, label in [
        ("all", "全部題目"),
        ("favorite", "我的最愛"),
        ("easy", "簡單的題目"),
        ("medium", "適中的問題"),
        ("hard", "困難的問題"),
    ]:
        assert f'<option value="{value}">{label}</option>' in html


# --- 預設與難度篩選 ---------------------------------------------------------


def test_the_default_filter_lists_every_position(list_page) -> None:
    """開頁時篩選是「全部題目」,列表與計數與未篩選時一致。"""
    open_list(list_page, FILTER_CATALOG)

    assert filter_select(list_page).input_value() == "all"
    assert [row["id"] for row in rows(list_page)] == [
        str(entry["id"]) for entry in FILTER_CATALOG
    ]
    assert counts(list_page) == ["0", str(len(FILTER_CATALOG))]


@pytest.mark.parametrize(
    ("value", "expected_ids"),
    [
        ("easy", ["201"]),
        ("medium", ["202"]),
        ("hard", ["203", "204"]),
    ],
)
def test_choosing_a_difficulty_filters_the_list(
    list_page, value: str, expected_ids: list[str]
) -> None:
    """選一個難度,只留下該難度的題目,m/n 也只算這個子集合。"""
    open_list(list_page, FILTER_CATALOG)

    choose_filter(list_page, value)

    assert [row["id"] for row in rows(list_page)] == expected_ids
    assert counts(list_page) == ["0", str(len(expected_ids))]


def test_choosing_all_again_restores_the_full_list(list_page) -> None:
    """選過難度之後改回「全部題目」,列表與計數都要還原,不留殘影。"""
    open_list(list_page, FILTER_CATALOG)
    choose_filter(list_page, "hard")

    choose_filter(list_page, "all")

    assert [row["id"] for row in rows(list_page)] == [
        str(entry["id"]) for entry in FILTER_CATALOG
    ]
    assert counts(list_page) == ["0", str(len(FILTER_CATALOG))]


# --- 我的最愛 ---------------------------------------------------------------


def test_choosing_favorite_lists_only_starred_positions(list_page) -> None:
    """標星兩題後選「我的最愛」,只留下標星過的那兩題。"""
    open_list(list_page, FILTER_CATALOG)

    for position_id in (201, 203):
        list_page.locator(
            f'#positions > li[data-id="{position_id}"] .position-star'
        ).click()

    choose_filter(list_page, "favorite")

    assert sorted(int(row["id"]) for row in rows(list_page)) == [201, 203]
    assert counts(list_page) == ["0", "2"]


def test_unstarring_while_favorite_filter_is_active_removes_the_row(
    list_page,
) -> None:
    """在「我的最愛」篩選下取消標星,那一列立刻從畫面消失 —— 重畫一律照當下的
    `starred` 集合求值,不留著上一次篩出來的那份。
    """
    open_list(list_page, FILTER_CATALOG)
    list_page.locator('#positions > li[data-id="201"] .position-star').click()
    choose_filter(list_page, "favorite")
    assert [row["id"] for row in rows(list_page)] == ["201"]

    list_page.locator('#positions > li[data-id="201"] .position-star').click()

    assert rows(list_page) == []
    assert counts(list_page) == ["0", "0"]


# --- 每日一題不受篩選影響 -----------------------------------------------------


def test_the_daily_row_ignores_the_active_filter(list_page) -> None:
    """篩選條件排除掉推薦題所屬的難度時,推薦列依然畫著同一題 —— 推薦列固定畫
    `daily`,不吃 `visiblePositions()`。
    """
    open_list(list_page, FILTER_CATALOG)
    featured = featured_row(list_page)
    assert featured is not None
    entry = next(e for e in FILTER_CATALOG if str(e["id"]) == featured["id"])

    # 選一個推薦題**不屬於**的難度,確保篩選真的與推薦題錯開。
    excluded_value = next(
        value
        for value, difficulty in FILTER_DIFFICULTY.items()
        if difficulty != entry["difficulty"]
    )
    choose_filter(list_page, excluded_value)

    still_featured = featured_row(list_page)
    assert still_featured is not None
    assert still_featured["id"] == featured["id"]


# --- 狀態保留(sessionStorage) ------------------------------------------------
#
# 篩選條件記在 `sessionStorage`(`web/filter.js`),不是網址參數 —— 「回到列表」
# 那條連結(`web/app.js` 的 `mountBackLink()`)是固定的 `href="./index.html"`,
# 不會帶著上一頁的篩選狀態,網址參數因此接不起「點進一題再回來」這個往返。
# `sessionStorage` 不必靠任何一條特定連結傳遞,同一分頁的 session 活得過任何
# 返回路徑。見「每日一題不受篩選影響」之後、4.2 之前的完整往返測試。


def test_choosing_a_filter_persists_to_session_storage(list_page) -> None:
    """選了篩選之後立刻寫進 `sessionStorage`。"""
    open_list(list_page, FILTER_CATALOG)

    choose_filter(list_page, "easy")

    assert stored_filter(list_page) == "easy"


def test_a_stored_filter_is_restored_on_load(list_page) -> None:
    """開頁前 `sessionStorage` 已經有篩選條件時,下拉選單與列表都照這個條件
    還原 —— 模擬「篩選後點進一題、返回列表」的整頁重新載入。
    """
    seed_filter(list_page, "hard")
    open_list(list_page, FILTER_CATALOG)

    assert filter_select(list_page).input_value() == "hard"
    assert [row["id"] for row in rows(list_page)] == ["203", "204"]


def test_an_unrecognized_stored_filter_falls_back_to_all(list_page) -> None:
    """`sessionStorage` 裡是認不得的篩選值時回退成「全部題目」,而不是整份
    列表消失。
    """
    seed_filter(list_page, "impossible")
    open_list(list_page, FILTER_CATALOG)

    assert filter_select(list_page).input_value() == "all"
    assert [row["id"] for row in rows(list_page)] == [
        str(entry["id"]) for entry in FILTER_CATALOG
    ]


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


def open_position(page, position_id: Any) -> None:
    """自列表點進某一題 —— 真實點擊那個連結,不是直接改網址。"""
    page.locator(f'#positions > li[data-id="{position_id}"] a[href]').click()


# --- 4.2:對局介面載入的確實是那一題 ------------------------------------


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
    assert play_view(list_page)["title"] == played_heading(3), "沒有真的進到對局介面"

    go_back(list_page)

    marked = {"2", "5"}
    state = {row["id"]: (row["checked"], row["marked"]) for row in rows(list_page)}
    assert state == {
        row_id: (row_id in marked, row_id in marked) for row_id in LISTED_IDS
    }, f"返回列表之後標記不見了:{state}"
    assert counts(list_page) == before == ["2", TOTAL]
    assert json.loads(stored_completed(list_page)) == [2, 5]
    assert served["positions"] == ["3"], f"中途載入的不是所選的那一題:{served}"


def test_the_filter_survives_a_round_trip_through_a_game(list_page) -> None:
    """篩選條件經得起「點進一題、再從對局介面按返回」——這是使用者實測抓到的
    迴歸(見 `web/filter.js` 的檔頭說明):返回連結是固定的
    `href="./index.html"`(`web/app.js` 的 `mountBackLink()`),不會帶著上一頁
    的篩選狀態;篩選因此必須靠 `sessionStorage` 撐過這趟整頁重新載入,不能只
    在網址參數上做文章——這條測試直接走那條真正的返回連結,不是自己組網址。
    """
    served = open_list_that_can_be_played(list_page, FILTER_CATALOG)

    choose_filter(list_page, "hard")
    assert [row["id"] for row in rows(list_page)] == ["203", "204"]

    open_position(list_page, 203)
    assert urlsplit(list_page.url).path == PLAY_PATH, "沒有真的進到對局介面"

    go_back(list_page)

    assert filter_select(list_page).input_value() == "hard"
    assert [row["id"] for row in rows(list_page)] == ["203", "204"]
    assert served["positions"] == ["203"], f"中途載入的不是所選的那一題:{served}"


# --- 4.5(修訂後):對局介面顯示該題的出處,描述兩頁都不畫 ---------------
#
# 4.5 原本要求對局介面顯示出處**與描述**。使用者看過實際畫面後推翻了描述那一半:
# 真實題庫的 `description` 是「出處 + 局號 + 局名」的串接,與 `h1` 的局名和它下面
# 那一行的出處完全重複。requirements.md 4.5 與 tasks.md 4.2 皆已隨之修訂。


@pytest.mark.parametrize("position_id", ["3"])
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


@pytest.mark.parametrize("position_id", ["1"])
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


#: 讀出對局頁題目資訊的當下狀態。
_PUZZLE_INFO = """() => {
  const text = (id) => document.getElementById(id)?.innerText.trim() ?? null;
  const shown = (id) => {
    const el = document.getElementById(id);
    return el != null && !el.hidden;
  };
  const top = (id) => {
    const el = document.getElementById(id);
    if (el == null || el.hidden) return null;
    const box = el.getBoundingClientRect();
    return { top: Math.round(box.top), bottom: Math.round(box.bottom) };
  };
  return {
    meta: text('puzzle-meta'),
    source: text('puzzle-source'),
    difficulty: text('puzzle-difficulty'),
    toggle: text('toggle-tags'),
    toggleShown: shown('toggle-tags'),
    expanded: document.getElementById('toggle-tags')?.getAttribute('aria-expanded'),
    tagsShown: shown('puzzle-tags'),
    tags: [...document.querySelectorAll('#puzzle-tags .puzzle-tag')].map(
      (chip) => chip.textContent.trim(),
    ),
    dtm: document.getElementById('puzzle-max-dtm') !== null,
    boxes: {
      meta: top('puzzle-meta'),
      difficulty: top('puzzle-difficulty'),
      toggle: top('toggle-tags'),
      tags: top('puzzle-tags'),
    },
  };
}"""


def _on_the_same_line(one: dict | None, other: dict | None) -> bool:
    """兩個框是否在同一行 —— 以**垂直範圍重疊**判定,不是上緣相等。

    上緣相等太嚴:按鈕有內距因而比旁邊的文字高,`align-items: center` 對齊的是中線,
    兩者的上緣本來就差一兩個 px。重疊與否才是「看起來在同一行」的定義。
    """
    if one is None or other is None:
        return False
    return one["top"] < other["bottom"] and other["top"] < one["bottom"]


def test_the_puzzle_info_keeps_the_tags_behind_a_toggle(list_page) -> None:
    """題目資訊:出處一行,難度與標籤同一行,而**標籤預設收在按鈕後面**。

    這一條的歷史說明了它在守什麼。原本叫
    `test_the_source_and_mate_distance_share_a_single_line`,驗出處與最長殺著併在
    同一行;**最長殺著整組移除**(它對使用者劇透,預告這題幾步殺得完)之後改成驗
    那一行從頭到尾就是出處;難度與標籤加進來之後又改了一次。

    **要擋掉的東西一路沒變:劇透不得出現在對局頁上。** 標籤(殺法名)是同一類東西 ——
    「雙馬」「連將殺」等於告訴使用者這題怎麼殺,故預設不畫,要按一下才展開。因此這
    一條同時驗兩件事:一開始標籤真的不在畫面上,以及按下去之後它確實出得來。

    「同一行」以上緣相同來驗,「兩行」以上緣不同來驗:比對字面只能說出內容對不對,
    說不出它們排在哪裡(併行或分行時每一段字面都照樣正確)。
    """
    open_list_that_can_be_played(list_page)
    open_position(list_page, 1)
    list_page.wait_for_selector("#board svg .piece")

    before = list_page.evaluate(_PUZZLE_INFO)

    expected_difficulty = DIFFICULTY_LABELS[CATALOG[0]["difficulty"]]
    assert before["source"] == SOURCES["1"], f"出處不對:{before}"
    # 出處那一行**只有出處** —— 難度被併回去的話這一條就會轉紅。
    assert before["meta"] == SOURCES["1"], f"出處那一行不只有出處:{before}"
    assert before["difficulty"] == expected_difficulty, f"難度不對:{before}"
    assert not before["dtm"], "最長殺著那一格還在"

    # 標籤是劇透:預設不得出現在畫面上,連字都不該在 DOM 裡。
    assert not before["tagsShown"], f"標籤預設就展開了 —— 那是劇透:{before}"
    assert before["tags"] == [], f"標籤收著卻仍留在 DOM 裡:{before}"
    assert before["toggleShown"], f"沒有展開標籤的按鈕:{before}"
    assert before["toggle"] == "顯示標籤", f"按鈕的字不對:{before}"
    assert before["expanded"] == "false", f"aria-expanded 與畫面不符:{before}"

    boxes = before["boxes"]
    assert not _on_the_same_line(boxes["meta"], boxes["difficulty"]), (
        f"出處與難度被併成同一行:{boxes}"
    )
    assert _on_the_same_line(boxes["difficulty"], boxes["toggle"]), (
        f"難度與展開按鈕不在同一行:{boxes}"
    )

    list_page.locator("#toggle-tags").click()
    after = list_page.evaluate(_PUZZLE_INFO)

    assert after["tags"] == CATALOG[0]["tags"], f"展開後的標籤不對:{after}"
    assert after["tagsShown"], f"按了卻沒展開:{after}"
    assert after["toggle"] == "隱藏標籤", f"按鈕的字沒有跟著換:{after}"
    assert after["expanded"] == "true", f"aria-expanded 與畫面不符:{after}"
    # 標籤自成一行,不排在按鈕右邊(理由見 `style.css` 的 `#puzzle-tags`)。
    grown = after["boxes"]
    assert not _on_the_same_line(grown["tags"], grown["toggle"]), (
        f"標籤排在按鈕右邊而不是自成一行:{grown}"
    )
    assert grown["tags"]["top"] >= grown["toggle"]["bottom"] - 1, (
        f"標籤沒有落在按鈕那一行的下方:{grown}"
    )
    assert _on_the_same_line(grown["difficulty"], grown["toggle"]), (
        f"展開之後難度與按鈕被拆開了:{grown}"
    )

    # 再按一次收回去 —— 按鈕的字說的是「按下去會發生什麼」,兩個方向都要成立。
    list_page.locator("#toggle-tags").click()
    again = list_page.evaluate(_PUZZLE_INFO)
    assert not again["tagsShown"], f"再按一次沒有收回去:{again}"
    assert again["toggle"] == "顯示標籤", f"收回後按鈕的字不對:{again}"
    assert again["expanded"] == "false", f"aria-expanded 與畫面不符:{again}"


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

#: 終局之後 `#turn` 各自該說的話(`web/app.js` 的 `turnText`)。
YOU_WON = "你獲勝"


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


def test_the_next_position_actually_opens_that_position(list_page) -> None:
    """按下去真的到得了那一題,而不只是有條連結掛在那裡。"""
    served = open_and_win(list_page, 1)
    list_page.wait_for_selector(f"#{NEXT_LINK_ID}:not([hidden])")

    list_page.locator(f"#{NEXT_LINK_ID}").click()
    list_page.wait_for_selector("#board svg .piece")

    assert urlsplit(list_page.url).path == PLAY_PATH
    assert list_page.locator("#puzzle-title").inner_text().strip() == played_heading(2)
    assert served["positions"][-1] == "2", (
        f"對局頁最後要的不是第 2 題:{served['positions']}"
    )
