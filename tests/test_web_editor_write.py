"""收題頁按下寫入之後的撞號檢查(tasks 5.1;requirements 4.3、4.4、4.5)。

寫入一題是一條有次序的序列(design 的 System Flows):**取索引 → 撞號 → 送權威
驗證 → 取目錄授權 → 重讀目標檔 → 文字層追加 → 寫回 → 記下題號並清空欄位**。
本檔涵蓋的是**前兩步**,tasks 5.2–5.4 會沿用同一套夾具往下接 —— 因此夾具與輔助
函式都寫成「一次寫入嘗試」的形狀,而不是「一次撞號檢查」的形狀。

## 撞號的判準是一個聯集,而聯集的兩半各有一個時間窗

`GET /api/catalog` 是題庫索引,它是**服務啟動時的快照**;開發啟動腳本會在題目檔
變動時重啟服務,所以剛寫進去的一題要等重啟完成才會出現在索引裡。那段空窗由
**本分頁已成功寫入的題號集合**補上(4.4),而那個集合又只在**寫入成功之後**才
加入(design 的 State Management)—— 失敗的嘗試不佔用題號。

兩件事合起來使本檔的多數測試都是**兩次嘗試**:第一次造出一個看得見的狀態,第二次
在索引已經換了內容的情況下再問一次。單次嘗試看不出「有沒有重新取索引」,也看不出
「上一次失敗有沒有偷偷佔走題號」。

## 為什麼「通過」也要有正面的觀察

4.5 要求兩邊皆無時視為**通過**,而不只是「沒有出事」。5.1 之後的序列還不存在,
通過因此在畫面上不生任何新東西 —— 唯一看得見的正面後果是:**通過會把先前指認的
撞號撤下**。本檔的 4.5 測試因此先造一次撞號、再讓同一個題號通過,斷言那句指認
真的被收回。這同時是「索引取不到」那條測試的對照組:取不到索引**不得**把那句指認
收回,否則一次取不到就長得跟「沒有撞號」一模一樣。

## `recordWrittenId()` 是 5.3 的接口,不是測試用的後門

「寫入成功」這件事要到 5.3 才存在,本輪沒有任何路徑會讓一個題號進入本分頁的集合。
`editor.js` 因此把「記下一個已成功寫入的題號」export 出來,5.3 在寫回落盤之後呼叫
它;本檔以同一個接口佈置 4.4 的前置狀態。**這不是偽造成功路徑** —— 測試佈置的是
「第一題已經寫成功了」這個前提,而序列裡真正把它記下來的那一步屬 5.3。

## 頁面走 http 而不是 `file://`

理由與其餘收題頁測試相同:`editor.js` 是 ES module,Chromium 不允許自 `file://`
(origin 為 `null`)匯入 module。故沿用同一套 `page.route()` 就地供 `web/` 底下的
**真實交付檔**,另把 `/api/catalog` 換成本檔控制得動的替身。
"""

from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下。
ORIGIN = "https://web-editor-write.test"

#: 題庫索引端點。這是本輪唯一會被打到的後端位址(5.2 才會加上驗證端點)。
CATALOG_PATH = "/api/catalog"

#: 桌面尺寸。窄畫面的折行屬版面(4.1)。
DESKTOP = (1280, 800)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

#: 表單欄位的順序,即 `check.js` 的 `checkForm()` 回傳清單的順序。
FORM_FIELDS = ["id", "title", "description", "difficulty", "tags", "fen", "target"]

WRITE_BUTTON = "#write"
WRITE_NOTE = "#write-note"

#: 《適情雅趣》第 21 局的起始局面,紅先(與其餘收題頁測試同一串)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 一份填得完整、七項淺層檢查全過的表單 —— 寫入操作因此是**可按的**,
#: 撞號檢查才有機會執行。題號 26 是本檔各測試共用的那一個。
VALID_FORM = {
    "id": "26",
    "title": "患在几席",
    "description": "自己打的描述,不該被任何建議值蓋掉",
    "difficulty": "2",
    "tags": "解殺還殺 鐵門栓、悶宮",
    "fen": PUZZLE_FEN,
    "target": "適情雅趣~卷一/26.json",
}

#: 表單裡那個題號的數值形式。撞號的訊息要**指出重複的題號**(4.3、4.4),
#: 因此各測試斷言的是「這個數字出現在訊息裡」,而不是某一句寫死的說法。
FORM_ID = 26

#: 讓寫入序列的後續 microtask 與重畫跑完的窗口(毫秒)。
#:
#: 「沒有出現撞號指認」是一句**否定**的斷言,而否定沒有可等待的正面條件 ——
#: 只能給實作一段足夠出錯的時間再問。本機 `page.route()` 就地供的回應在數毫秒內
#: 就回來了,250 毫秒是兩個數量級的餘裕。
SETTLE_MS = 250

#: 等索引請求送達的上界(秒)。
CATALOG_TIMEOUT_S = 5.0


# --- 索引替身 -----------------------------------------------------------


@dataclass
class Catalog:
    """本檔控制得動的題庫索引 —— 測試中途改得了內容,也關得掉。

    `ids` 是索引裡的題號。索引的其餘欄位照 `GET /api/catalog` 的形狀給齊
    (`list.js` 消費的就是這個形狀),但撞號檢查只看題號。

    `failing` 為真時整個請求被砍掉 —— 服務重啟期間索引取不到就是這個樣子
    (design 的 Risks:此時寫入不成立,說法歸 5.4)。
    """

    ids: list[int] = field(default_factory=list)
    failing: bool = False
    requests: int = 0

    def payload(self) -> dict:
        return {
            "positions": [
                {
                    "id": position_id,
                    "title": f"第 {position_id} 局",
                    "description": "",
                    "difficulty": 2,
                    "tags": [],
                    "source": "適情雅趣",
                }
                for position_id in sorted(self.ids)
            ]
        }


# --- 夾具與操作 ---------------------------------------------------------


@pytest.fixture
def catalog() -> Catalog:
    """本次測試的索引內容。預設是一份空索引 —— 什麼題號都不撞。"""
    return Catalog()


@pytest.fixture
def editor_page(browser_page, catalog: Catalog) -> Iterator:
    """以 http 來源開啟**真實的**收題頁,並把索引端點接到 `catalog`。

    索引與靜態檔共用同一個處理器而不是註冊兩條 `page.route()`:多條規則的
    優先順序是框架的細節,寫成一個顯式的分派就不必記它。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path

        if path == CATALOG_PATH:
            catalog.requests += 1
            if catalog.failing:
                route.abort("failed")
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(catalog.payload()),
            )
            return

        if path in ("/editor", "/editor/"):
            path = "/editor/index.html"
        target = WEB_DIR / path.lstrip("/")
        if target.is_file():
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES.get(target.suffix, "text/plain"),
                body=target.read_text(encoding="utf-8"),
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.set_viewport_size({"width": DESKTOP[0], "height": DESKTOP[1]})
    browser_page.route(f"{ORIGIN}/**", serve)
    browser_page.goto(f"{ORIGIN}/editor/")
    # 難度選項由模組在載入時產生,等它出現再繼續 —— 否則第一次填表可能落在模組
    # 還沒執行完的瞬間。
    browser_page.wait_for_function(
        "() => document.querySelectorAll('[data-field=\"difficulty\"] option').length > 1",
        timeout=5000,
    )
    yield browser_page


def control(name: str) -> str:
    """某一欄的控制項選擇器。一律以 `data-field` 認欄位(4.1 的 DOM 契約)。"""
    return f'[data-field="{name}"]'


def slot(name: str) -> str:
    """某一欄的訊息槽選擇器(4.2 的 DOM 契約)。"""
    return f'[data-message-for="{name}"]'


def put(page, name: str, value: str) -> None:
    """把 `value` 填進某一欄。難度是 `<select>`,只能選不能打字(3.2)。"""
    if name == "difficulty":
        page.select_option(control(name), value)
        return
    page.fill(control(name), value)


def fill_valid_form(page, **overrides: str) -> None:
    """填出一份七項全過的表單,`overrides` 逐欄覆寫。

    描述**刻意最後填**:前面填局名時會冒出建議值,最後這一次覆寫讓它成為維護者
    自己的內容(3.7),後續的斷言才問得出「按下寫入之後內容有沒有被動過」。
    """
    values = dict(VALID_FORM)
    values.update(overrides)
    for name in ["id", "title", "difficulty", "tags", "fen", "target", "description"]:
        put(page, name, values[name])


def value_of(page, name: str) -> str:
    return page.input_value(control(name))


def message(page, name: str) -> str:
    """某一欄當下說的話;沒有話說(`hidden`)時為空字串,槽位不存在時為 `None`。"""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (element === null) return null;
          return element.hidden ? '' : element.textContent.trim();
        }""",
        slot(name),
    )


def note(page) -> str:
    """寫入操作旁那句說明;沒有話說時為空字串。"""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (element === null) return null;
          return element.hidden ? '' : element.textContent.trim();
        }""",
        WRITE_NOTE,
    )


def write_is_enabled(page) -> bool:
    return page.locator(WRITE_BUTTON).is_enabled()


def record_written_id(page, position_id: int) -> None:
    """佈置「這個題號已經在本分頁成功寫入過」這個前提(4.4)。

    走的是 `editor.js` export 出來的同一個接口 —— 5.3 在寫回落盤之後呼叫它。
    測試不模擬寫入,只宣告那一步已經發生過。
    """
    page.evaluate(
        """async id => {
          const module = await import('./editor.js');
          module.recordWrittenId(id);
        }""",
        position_id,
    )


def wait_for_catalog(page, catalog: Catalog, expected: int) -> None:
    """等到索引端點被打滿 `expected` 次。"""
    deadline = time.monotonic() + CATALOG_TIMEOUT_S
    while catalog.requests < expected and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert catalog.requests == expected, (
        f"索引端點被取用的次數是 {catalog.requests},預期 {expected}"
    )


def attempt_write(page, catalog: Catalog) -> None:
    """按一次寫入,等到這一次嘗試的索引請求送達並讓後續處理跑完。

    等待本身就是一項斷言:**每一次嘗試都必須重新取一次索引**(research 的
    Decision 5)。載入時取一次然後沿用的實作會停在這裡。
    """
    expected = catalog.requests + 1
    page.click(WRITE_BUTTON)
    wait_for_catalog(page, catalog, expected)
    page.wait_for_timeout(SETTLE_MS)


# --- 與既有題目撞號(4.3)----------------------------------------------


def test_an_id_that_exists_in_the_corpus_is_blocked_and_named(
    editor_page, catalog: Catalog
) -> None:
    """4.3:題號與題庫既有題目重複時擋下,並**指出重複的那個題號**。

    訊息落在題號那一欄旁邊(design 的 Error Handling:可自行修正的失敗定位到
    欄位)。斷言的是「那個數字出現在訊息裡」而不是一句寫死的說法 —— 維護者要
    知道的是**哪一個**題號撞了,措辭怎麼寫是實作的自由。
    """
    catalog.ids = [1, 25, FORM_ID, 30]
    fill_valid_form(editor_page)
    assert write_is_enabled(editor_page), "七項淺層檢查全過時寫入必須是可按的"

    attempt_write(editor_page, catalog)

    said = message(editor_page, "id")
    assert str(FORM_ID) in said, f"撞號時沒有指出重複的題號:{said!r}"

    marked = editor_page.get_attribute(control("id"), "aria-invalid")
    assert marked == "true", "看得見的那一句與無障礙標記說的必須是同一件事"


def test_a_blocked_attempt_keeps_everything_the_maintainer_typed(
    editor_page, catalog: Catalog
) -> None:
    """4.3:擋下不等於清空 —— design 的 Error Handling 三類失敗一律保留表單內容。

    只有寫入成功才清空(7.2),而本輪沒有成功這件事。任何一次清空都可能讓維護者
    重抄一次 FEN。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"擋下之後 {name} 的內容變了"
        )


def test_a_collision_leaves_the_write_control_pressable(
    editor_page, catalog: Catalog
) -> None:
    """4.3:擋下的是**這一次嘗試**,不是這顆按鈕。

    撞號檢查在每一次按下時重跑,所以擋不掉的寫入不存在;反過來把按鈕停用會讓
    維護者無法對同一個題號再試一次(索引可能剛好正在重啟)—— 他得先把題號改掉
    再改回來才按得下去,而那是一個沒有理由的手續。

    「尚未通過」那一行也不該說話:它講的是**淺層檢查**未過所以按鈕停用(8.4),
    而此刻七項都過了。撞號的說法定位在題號那一欄。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    assert write_is_enabled(editor_page), "撞號不該讓寫入變成按不下去"
    assert note(editor_page) == "", (
        f"撞號的說法不該擠進停用說明那一行:{note(editor_page)!r}"
    )


def test_the_collision_message_goes_away_when_the_id_changes(
    editor_page, catalog: Catalog
) -> None:
    """4.3:那句指認是針對**那一個題號**說的,題號一改就不再成立。

    留著的話,維護者改成一個沒撞的題號之後仍看得到紅字,而畫面上再也沒有東西
    告訴他那句話已經過期了。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)
    assert str(FORM_ID) in message(editor_page, "id")

    put(editor_page, "id", "27")

    assert message(editor_page, "id") == "", "換了題號之後那句指認還掛著"


# --- 與本分頁已寫入的題號撞號(4.4)------------------------------------


def test_an_id_written_in_this_tab_is_blocked_although_the_index_lacks_it(
    editor_page, catalog: Catalog
) -> None:
    """4.4:同一分頁已寫入的題號照樣擋下 —— **即使題庫索引還沒更新**。

    這是 research 的 Decision 5 存在的全部理由:索引是服務啟動時的快照,而開發
    啟動腳本要等題目檔變動觸發重啟才會換上新的。那段空窗裡,一個剛寫進去的題號
    在索引中查無此人,只有本分頁的集合擋得住它。

    本測試的索引**刻意不含** 26,所以擋得下來只可能出自本分頁的集合。
    """
    catalog.ids = [1, 2, 3]
    record_written_id(editor_page, FORM_ID)
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    said = message(editor_page, "id")
    assert str(FORM_ID) in said, f"本分頁已寫入的題號沒有被擋下:{said!r}"


def test_a_blocked_attempt_does_not_reserve_the_id(
    editor_page, catalog: Catalog
) -> None:
    """4.4 的另一半:**失敗的嘗試不佔用題號**(design 的流程層級決定)。

    本分頁的集合只在寫入成功後才加入。第一次嘗試被撞號擋下,26 因此不得進入那個
    集合;索引隨後不再含 26 時,同一個題號必須重新變成可用的。

    這一條殺的是「按下寫入就先把題號記起來」那種實作 —— 它在第二次嘗試會照樣擋,
    而維護者手上那個題號其實誰也沒在用。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)
    assert str(FORM_ID) in message(editor_page, "id")

    catalog.ids = []
    attempt_write(editor_page, catalog)

    assert message(editor_page, "id") == "", (
        "被擋下的那一次嘗試把題號佔走了 —— 集合只該在寫入成功後才加入"
    )


# --- 兩邊皆無即通過(4.5)----------------------------------------------


def test_an_id_in_neither_place_is_not_reported_as_a_collision(
    editor_page, catalog: Catalog
) -> None:
    """4.5:題號未出現在索引、也未出現在本分頁已寫入的集合中時,撞號檢查通過。

    本分頁的集合刻意放了**另一個**題號(27):判準是「這一個題號在不在裡面」,
    不是「集合空不空」。
    """
    catalog.ids = [1, 2, 3]
    record_written_id(editor_page, 27)
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    assert message(editor_page, "id") == "", (
        f"兩邊皆無的題號被當成撞號:{message(editor_page, 'id')!r}"
    )
    assert note(editor_page) == ""
    assert write_is_enabled(editor_page)


def test_passing_the_collision_check_withdraws_an_earlier_collision(
    editor_page, catalog: Catalog
) -> None:
    """4.5:通過是一個**結果**,不只是「沒有出事」。

    5.1 之後的序列還不存在,所以通過在畫面上唯一看得見的正面後果就是:先前指認
    的那個撞號被收回。這條同時是「索引取不到」那條測試的對照組 —— 兩者的差別
    正是這句指認有沒有被撤下。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)
    assert str(FORM_ID) in message(editor_page, "id")

    catalog.ids = [1, 2, 3]
    attempt_write(editor_page, catalog)

    assert message(editor_page, "id") == "", "撞號檢查通過時沒有把先前的指認撤下"
    assert write_is_enabled(editor_page)


# --- 每一次嘗試都重新取索引(research 的 Decision 5)--------------------


def test_the_index_is_fetched_again_on_every_attempt(
    editor_page, catalog: Catalog
) -> None:
    """索引在**每一次寫入嘗試**前重新取得,不是載入時取一次就沿用。

    服務會在題目檔變動時重啟,索引因此會自己跟上;重新取一次才讓已經進了索引的
    題號自然歸位。第一次嘗試時索引是空的(通過),隨後 26 進了索引,第二次嘗試
    必須看得到它 —— 沿用第一次那份快照的實作會在這裡放行。
    """
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)
    assert message(editor_page, "id") == ""

    catalog.ids = [FORM_ID]
    attempt_write(editor_page, catalog)

    assert str(FORM_ID) in message(editor_page, "id"), (
        "第二次嘗試用的是上一次的索引快照"
    )
    assert catalog.requests == 2


# --- 索引取不到(design 的 Risks;說法歸 5.4)---------------------------


def test_a_failed_index_fetch_is_not_read_as_no_collision(
    editor_page, catalog: Catalog
) -> None:
    """索引取不到時,寫入不成立 —— **不得**與「沒有撞號」長成同一個樣子。

    服務重啟期間索引請求會失敗。把失敗折成一份空索引繼續走,等於讓最容易撞號的
    那一刻(剛寫完一題、服務正在重啟)變成最放行的一刻。

    此刻畫面上該說什麼屬 5.4 的一般失敗處理(原本涵蓋這件事的 requirement 已
    刻意移除,見 design 的 Risks),所以這裡斷言的是**不該發生的事**:先前那句
    撞號指認不得被撤下。

    最後再讓索引回來一次,確認一次失敗沒有讓這一頁卡住 —— 表單內容與寫入操作
    都還在(7.3)。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)
    assert str(FORM_ID) in message(editor_page, "id")

    catalog.failing = True
    attempt_write(editor_page, catalog)

    assert str(FORM_ID) in message(editor_page, "id"), (
        "索引取不到卻把撞號指認收了回去 —— 那與「沒有撞號」分不出來"
    )

    catalog.failing = False
    catalog.ids = []
    attempt_write(editor_page, catalog)

    assert message(editor_page, "id") == "", "一次索引失敗之後這一頁就不動了"


# --- 本任務的邊界:序列停在撞號檢查之後 --------------------------------


def test_the_write_sequence_stops_after_the_collision_check(
    editor_page, catalog: Catalog
) -> None:
    """5.1 只實作序列的前兩步:取索引與撞號。

    權威驗證(5.2)、目錄授權與寫檔(5.3)、成敗呈現(5.4)都還不存在,因此一次
    通過的嘗試對後端**只有索引那一個請求**。這一條釘住的是「這裡還沒有偷跑」,
    5.2 起會由那些任務改寫。
    """
    paths: list[str] = []
    editor_page.on("request", lambda request: paths.append(urlsplit(request.url).path))

    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog)

    api = [path for path in paths if path.startswith("/api/")]
    assert api == [CATALOG_PATH], f"撞號檢查之外還打了別的端點:{api}"
