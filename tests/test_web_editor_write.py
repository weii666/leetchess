"""收題頁按下寫入之後的整條寫入流程(tasks 5.1–5.3;requirements 4.3–4.5、4.7–4.9、
5.4–5.9、6.1–6.4、7.4、7.5)。

寫入一題是一條有次序的序列(design 的 System Flows):**取索引 → 撞號 → 送權威
驗證 → 取目錄授權 → 重讀目標檔 → 文字層追加 → 寫回 → 記下題號並清空欄位**。
本檔涵蓋的是**記下題號為止**,task 5.4 會沿用同一套夾具接上成敗呈現與清空 ——
因此夾具與輔助函式都寫成「一次寫入嘗試」的形狀,而不是「一次撞號檢查」的形狀。

## 這條序列的每一步都是「擋得下來」或「已經動到磁碟」二者之一

前三步都可能讓寫入不成立,而它們的共同責任是**在動到磁碟之前擋下來**;後四步則
已經假定它成立了。這條分界在測試裡的樣子,是幾乎每一組都會問一次「平台被碰過
幾次」——`fs_kinds()` 回的那份流水帳。撞號擋下卻已經跳過目錄選擇框、驗證未通過卻
已經讀過檔,兩者都是次序壞掉,而它們在畫面上看起來與正確的實作一模一樣。

## 判定與「確認未能完成」是兩件事,而畫面必須分得出來

權威驗證端點**恆以 200 回答判定**:候選題目不合格時回 `valid: false` 與 `issues`
(`service/main.py` 的模組說明)。真正的服務失敗 —— 池滿 503、逾時 504、未預期 500、
連線斷掉 —— 走的是既有的 `{code, message}` 形狀,那時服務**根本沒有判定可言**。

兩者的處置完全相反,所以本檔的 5.2 測試分成兩組:一組問「不合格時那句話有沒有定位
到欄位」,另一組問「確認未能完成時畫面說的是不是那件事,而且沒有偷偷放行」。把 503
讀成 `valid: true` 會讓一份沒驗過的題目寫進題庫,讀成 `valid: false` 則是誣賴一份可能
完全沒問題的題目(requirement 4.9)。

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

#: 題庫索引端點。
CATALOG_PATH = "/api/catalog"

#: 權威驗證端點(design 的 API Contract)。撞號通過的嘗試必然走到這裡(5.2)。
VALIDATE_PATH = "/api/editor/validate"

#: 平台包裝的模組位址。本檔**以替身頂替它**(5.3),理由見 `FS_FAKE_SOURCE`。
FS_MODULE_PATH = "/editor/fs.js"

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

#: `VALID_FORM` 送往驗證端點時該有的候選題目(design 的「候選題目的資料契約」)。
#:
#: **正好六個由人工編輯的 schema 欄位**,型別即題目檔裡的型別:`id` 與 `difficulty`
#: 是數字而不是輸入框裡那串字,`tags` 是切分後的陣列。`max_dtm`(由驗證工具回填)、
#: `source`(由資料夾表達)、`side_to_move`(由 fen 表達)三者都不在候選題目裡 ——
#: 後端對未知欄位是直接拒絕的,多送一個就會讓一份好題目被判為不合格。
#:
#: 目標檔案路徑同樣不在裡面:它是**寫到哪個檔**,不是題目的欄位。
EXPECTED_CANDIDATE = {
    "id": FORM_ID,
    "title": "患在几席",
    "description": "自己打的描述,不該被任何建議值蓋掉",
    "fen": PUZZLE_FEN,
    "difficulty": 2,
    "tags": ["解殺還殺", "鐵門栓", "悶宮"],
}

#: 「確認未能完成」那句話的關鍵字(requirement 4.9)。
#:
#: 斷言的是這四個字而不是一整句寫死的說法:4.9 要維護者知道的是**這次確認沒有跑完**,
#: 措辭怎麼寫是實作的自由。
UNFINISHED_KEYWORD = "確認未能完成"

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


# --- 驗證端點替身 -------------------------------------------------------


#: 服務失敗回應裡的訊息原文。**它不得出現在畫面上任何一處** —— `api.js` 的契約只有
#: 類別碼,後端與瀏覽器的原文一個字都不外流(requirement 7.5 對這個端點同樣成立)。
BACKEND_DETAIL = "engine pool exhausted at 2026-08-10T00:00:00 in /srv/service/pool.py"


@dataclass
class Validator:
    """本檔控制得動的權威驗證端點 —— `POST /api/editor/validate` 的替身。

    **預設回一份合格的判定**,5.1 既有的那些測試因此照樣走得完整條序列:撞號通過
    之後真的會送一次驗證,而那一次不該改變它們原本斷言的任何一件事。

    `status` 與 `payload` 一起換掉就成了服務失敗(既有的 `{code, message}` 形狀);
    `failing` 為真時整個請求被砍掉 —— 那是連線斷掉,與服務回話說自己忙不過來是兩種
    不同的失敗,而 4.9 要求兩者收斂成同一個結論。

    `bodies` 記下每一次送出的請求主體:候選題目**帶了哪些欄位、型別是什麼**只有在
    這裡看得到,而那正是 design 的「候選題目的資料契約」要釘住的東西。
    """

    status: int = 200
    payload: object = None
    failing: bool = False
    requests: int = 0
    bodies: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.payload is None:
            self.payload = {"valid": True, "issues": []}

    def rejects(self, issues: list[dict]) -> None:
        """讓端點回一份**不合格**的判定(4.8)。狀態碼仍是 200 —— 那是判定不是錯誤。"""
        self.status = 200
        self.payload = {"valid": False, "issues": issues}

    def fails_with(self, status: int, code: str) -> None:
        """讓端點回一次**真正的服務失敗**(4.9):既有的 `{code, message}` 形狀。"""
        self.status = status
        self.payload = {"code": code, "message": BACKEND_DETAIL}

    def candidates(self) -> list:
        """每一次送出的候選題目本身(請求主體的 `position`)。"""
        return [body.get("position") for body in self.bodies]


# --- 平台包裝替身 -------------------------------------------------------


#: 題庫目錄裡那個目標檔的相對路徑,即 `VALID_FORM['target']`。
TARGET_PATH = VALID_FORM["target"]

#: 目標檔的既有內容 —— 一份**只有一題**的合法題目檔。
#:
#: 排版刻意照既有題目檔的樣子寫(兩格縮排、欄位各一行、標籤單行、中文不轉義),
#: 因為 7.4 要問的是「既有的每一題逐字不變」,而逐字不變只有在既有內容本來就有
#: 排版特徵時才問得出來 —— 一份被重新序列化過的檔案與原檔的差別正是排版。
EXISTING_FILE = """[
  {
    "id": 21,
    "title": "獨卒擒王",
    "description": "適情雅趣 第二一局 獨卒擒王",
    "fen": "4k4/9/9/9/9/9/9/9/4P4/4K4 w - - 0 1",
    "difficulty": 1,
    "tags": ["小兵", "困斃"]
  }
]
"""


#: 注入的 `fs.js` 替身原始碼。
#:
#: design 的 Testing Strategy 明載這是**刻意接受的覆蓋缺口**:系統目錄選擇框無法由
#: 自動化工具操作,所以檔案系統那一層以替身驗證。`fs.js` 自己的每一行由
#: `tests/test_web_editor_fs.py` 以平台替身驗證,本檔驗的是**組裝層有沒有照正確的
#: 次序使用它**——什麼時候問授權、什麼時候重讀、寫出去的是哪一串位元組。
#:
#: 替身的形狀貼著 `fs.js` 的契約寫,兩處尤其要緊:`acquireCorpusDirectory()`
#: **不是 async 函式**(它必須同步把對話框開下去,見該檔的 Preconditions),以及
#: `readTextAt()` 對不存在的檔案**回 `null` 而不是拋出** —— 一種狀態只有一個表示法。
#:
#: 狀態掛在 `globalThis.__fs` 上讓測試讀寫。`supported` 是唯一必須在模組求值**之前**
#: 就定好的旗標(6.3 要求載入時就告知),所以它讀的是 `globalThis.__fsSupported`,
#: 由 `add_init_script()` 在導覽之前種下。
FS_FAKE_SOURCE = """
const state = {
  supported: globalThis.__fsSupported !== false,
  denied: false,
  files: {},
  calls: [],
};
globalThis.__fs = state;

export class UnsupportedBrowserError extends Error {
  constructor() {
    super('這個瀏覽器沒有本機目錄選取');
    this.name = 'UnsupportedBrowserError';
  }
}

export class PermissionDeniedError extends Error {
  constructor() {
    super('未取得題庫目錄的存取授權');
    this.name = 'PermissionDeniedError';
  }
}

export function isSupported() {
  return state.supported;
}

export function acquireCorpusDirectory() {
  state.calls.push(['acquire']);
  if (!state.supported) return Promise.reject(new UnsupportedBrowserError());
  if (state.denied) return Promise.reject(new PermissionDeniedError());
  return Promise.resolve({ name: 'positions' });
}

export async function readTextAt(dir, relativePath) {
  state.calls.push(['read', relativePath]);
  return Object.prototype.hasOwnProperty.call(state.files, relativePath)
    ? state.files[relativePath]
    : null;
}

export async function writeTextAt(dir, relativePath, text) {
  state.calls.push(['write', relativePath]);
  state.files[relativePath] = text;
}
"""


# --- 夾具與操作 ---------------------------------------------------------


@pytest.fixture
def catalog() -> Catalog:
    """本次測試的索引內容。預設是一份空索引 —— 什麼題號都不撞。"""
    return Catalog()


@pytest.fixture
def validator() -> Validator:
    """本次測試的驗證端點行為。預設判定為合格。"""
    return Validator()


@pytest.fixture
def editor_page(browser_page, catalog: Catalog, validator: Validator) -> Iterator:
    """以 http 來源開啟**真實的**收題頁,並把兩個後端端點與平台包裝接到本檔的替身。

    索引、驗證、`fs.js` 與其餘靜態檔共用同一個處理器而不是註冊四條 `page.route()`:
    多條規則的優先順序是框架的細節,寫成一個顯式的分派就不必記它。

    `fs.js` 是**唯一被頂替的模組**,其餘一律供真實檔案 —— 被驗證的必須是真的
    `editor.js`、真的 `check.js`、真的 `corpus-file.js`(見 `FS_FAKE_SOURCE`)。
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

        if path == VALIDATE_PATH:
            validator.requests += 1
            # 主體先記下來再看要不要失敗:連線被砍掉的那一次同樣送出過候選題目,
            # 而「送了什麼」與「回了什麼」是兩件事。
            raw = route.request.post_data
            validator.bodies.append(json.loads(raw) if raw else None)
            if validator.failing:
                route.abort("failed")
                return
            route.fulfill(
                status=validator.status,
                content_type="application/json",
                body=json.dumps(validator.payload),
            )
            return

        if path == FS_MODULE_PATH:
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES[".js"],
                body=FS_FAKE_SOURCE,
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


def page_text(page) -> str:
    """整頁看得見的文字。用來斷言某些字**沒有**出現在畫面上任何一處。"""
    return page.evaluate("() => document.body.innerText")


def fs_calls(page) -> list[list]:
    """平台包裝被使用的完整流水帳,依發生次序。

    次序本身就是 5.3 要釘住的東西:授權在重讀之前、重讀在寫回之前。一份「有沒有
    呼叫過」的集合表達不了這件事。
    """
    return page.evaluate("() => globalThis.__fs.calls")


def fs_kinds(page) -> list[str]:
    """流水帳裡只取動作名 —— 問次序時路徑是噪音。"""
    return [call[0] for call in fs_calls(page)]


def fs_files(page) -> dict:
    """替身當下持有的檔案內容。寫回落盤之後這裡就是磁碟上的那串位元組。"""
    return page.evaluate("() => globalThis.__fs.files")


def fs_setup(page, **changes) -> None:
    """佈置替身的前提:既有檔案內容、是否拒絕授權。"""
    page.evaluate("changes => Object.assign(globalThis.__fs, changes)", changes)


def unsupported_is_shown(page) -> bool:
    """不支援的說明(6.3)當下看不看得見。"""
    return page.evaluate("() => !document.getElementById('unsupported').hidden")


def wait_for_catalog(page, catalog: Catalog, expected: int) -> None:
    """等到索引端點被打滿 `expected` 次。"""
    deadline = time.monotonic() + CATALOG_TIMEOUT_S
    while catalog.requests < expected and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert catalog.requests == expected, (
        f"索引端點被取用的次數是 {catalog.requests},預期 {expected}"
    )


def wait_for_validation(page, validator: Validator, expected: int) -> None:
    """等到驗證端點被打滿 `expected` 次。"""
    deadline = time.monotonic() + CATALOG_TIMEOUT_S
    while validator.requests < expected and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert validator.requests == expected, (
        f"驗證端點被取用的次數是 {validator.requests},預期 {expected}"
    )


def attempt_write(page, catalog: Catalog, validator: Validator | None = None) -> None:
    """按一次寫入,等到這一次嘗試該送的請求都送達並讓後續處理跑完。

    等待本身就是一項斷言:**每一次嘗試都必須重新取一次索引**(research 的
    Decision 5)。載入時取一次然後沿用的實作會停在這裡。

    給了 `validator` 就一併等權威驗證送達 —— 撞號通過的嘗試必然走到那一步(5.2),
    而不給的那些呼叫處問的是**撞號**,那時序列可能根本走不到驗證(撞號擋下、或索引
    取不到)。等一個不會發生的請求會讓那些測試變成等到逾時才失敗。
    """
    expected_catalog = catalog.requests + 1
    expected_validations = None if validator is None else validator.requests + 1
    page.click(WRITE_BUTTON)
    wait_for_catalog(page, catalog, expected_catalog)
    if expected_validations is not None:
        wait_for_validation(page, validator, expected_validations)
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


# --- 撞號通過即送權威驗證(4.7)----------------------------------------


def test_a_passing_collision_check_sends_the_candidate_for_validation(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.7:維護者要求寫入時,先確認這個 FEN 是引擎載得進去的局面。

    「先確認」是一句關於**次序**的話:確認在寫入之前,而確認本身在收題頁做不到 ——
    引擎只在服務端。因此撞號通過之後必然有這一次請求,少了它,寫入就是在沒有確認
    的情況下進行的。
    """
    catalog.ids = [1, 2, 3]
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert validator.requests == 1, "撞號通過之後沒有送出權威驗證"


def test_the_candidate_carries_exactly_the_six_schema_fields(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """送出的候選題目與 design 的「候選題目的資料契約」逐欄相同。

    這一條要釘的不只是「有沒有送」,而是**送了什麼**:

    - `id` 與 `difficulty` 是數字。輸入框給的是字串,原樣送出會被題目 schema 判為
      型別不符 —— 一份完全沒問題的題目因此被擋下,而維護者看不出哪裡錯。
    - `tags` 是切分後的陣列,不是那一行原始輸入。
    - **正好六個欄位**。`max_dtm`、`source`、`side_to_move` 都不是候選題目的欄位,
      後端對未知欄位是直接拒絕的;目標檔案路徑則是「寫到哪個檔」,不是題目的內容。
    """
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert validator.bodies == [{"position": EXPECTED_CANDIDATE}]
    sent = validator.candidates()[0]
    assert isinstance(sent["id"], int) and not isinstance(sent["id"], bool)
    assert isinstance(sent["difficulty"], int)
    assert isinstance(sent["tags"], list)


def test_the_candidate_is_normalised_before_it_is_confirmed(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """送去確認的就是之後要寫出去的那一份 —— 前後空白在**送出之前**去掉。

    貼上時前後多一個空白或 tab 是常態(自檔案或網頁複製)。服務端明載它不做任何
    正規化(`service/models.py`:驗過的內容必須與使用者要寫出去的那一份逐字相同),
    所以正規化只能有一處。放在送出之後就是兩份可能不同的內容:確認的是一串,寫進
    題目檔的是另一串,而那正是本工具要杜絕的事。
    """
    catalog.ids = []
    fill_valid_form(
        editor_page,
        title=f"  {VALID_FORM['title']}  ",
        fen=f"  {PUZZLE_FEN} ",
    )

    attempt_write(editor_page, catalog, validator)

    assert validator.candidates() == [EXPECTED_CANDIDATE]


def test_a_collision_never_reaches_the_validation_endpoint(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """次序是設計的一部分:撞號擋下的嘗試**不送驗證**(design 的 System Flows)。

    一個題號已經被用掉的候選題目根本寫不出去,借一次引擎去確認它的 FEN 只是從
    對局那邊拿走一格池容量 —— 而引擎池是本服務唯一的併發閘門。
    """
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    assert str(FORM_ID) in message(editor_page, "id")
    assert validator.requests == 0, "撞號已經擋下這一次嘗試,不該再去借引擎"


# --- 驗證未通過(4.8)--------------------------------------------------


def test_a_failed_verdict_is_shown_beside_the_field_it_names(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.8:引擎載不進這個 FEN 時不寫入,並**定位到那一欄**。

    走的是淺層檢查那一組同樣的訊息槽(`[data-message-for]`):design 的 Error
    Categories 明載服務端的 `issues` 與前端的 `CheckIssue` 同形,**呈現層不分辨
    來源**。維護者要做的事在兩種情況下完全一樣 —— 去改那一格。

    訊息原樣顯示。這與服務失敗那一類刻意相反:判定的說法出自題目 schema,是寫給
    維護者看的;而失敗回應裡的 `message` 是後端的內部原文,一個字都不外流。
    """
    said = "引擎載不進這個 fen,它不是一個引擎認得的局面。"
    validator.rejects([{"field": "fen", "message": said}])
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "fen") == said, "未通過的項目沒有落在 FEN 那一欄"
    marked = editor_page.get_attribute(control("fen"), "aria-invalid")
    assert marked == "true", "看得見的那一句與無障礙標記說的必須是同一件事"


def test_a_failed_verdict_keeps_everything_the_maintainer_typed(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.8:不合格是「可自行修正」那一類 —— 保留表單內容、寫入操作仍按得下去。

    停用寫入是錯的:維護者改完 FEN 之後要能再送一次,而驗證每一次按下都重跑。
    """
    validator.rejects([{"field": "fen", "message": "引擎載不進這個 fen"}])
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"驗證未通過之後 {name} 的內容變了"
        )
    assert write_is_enabled(editor_page), "驗證未通過不該讓寫入變成按不下去"


def test_a_failed_verdict_does_not_reserve_the_id(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.8 的另一半:驗證未通過**不執行寫入**,那個題號因此誰也沒在用。

    序列停在驗證這一步,所以本輪看得見的後果有二:不再有任何請求送出去,而且
    那個題號沒有進入本分頁的已寫入集合 —— 下一次嘗試必須照樣放行。
    """
    paths: list[str] = []
    editor_page.on("request", lambda request: paths.append(urlsplit(request.url).path))

    validator.rejects([{"field": "fen", "message": "引擎載不進這個 fen"}])
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)
    assert message(editor_page, "fen") != ""

    api = [path for path in paths if path.startswith("/api/")]
    assert api == [CATALOG_PATH, VALIDATE_PATH], f"驗證未通過卻繼續往下走:{api}"

    validator.payload = {"valid": True, "issues": []}
    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "id") == "", (
        "未通過的那一次嘗試把題號佔走了 —— 集合只該在寫入成功後才加入"
    )


def test_a_verdict_that_names_two_fields_is_shown_word_for_word(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """同一份候選題目可能只回報**一項**,而那一項的說法涵蓋不只一個欄位。

    題目 schema 遇到第一個問題就停(tasks 的 Implementation Notes 2.3):
    `缺少必填欄位:id、title` 只歸屬到 `id`。呈現層因此**不得假設逐欄位窮舉** ——
    訊息原樣顯示,那句話裡的第二個欄位名才是維護者唯一的線索。
    """
    said = "候選題目 缺少必填欄位:id、title"
    validator.rejects([{"field": "id", "message": said}])
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "id") == said, "訊息被改寫或截斷了"
    assert message(editor_page, "title") == "", (
        "呈現層自己推論了第二個欄位 —— 那份清單不是逐欄位窮舉的"
    )


def test_a_verdict_issue_without_a_field_still_reaches_the_maintainer(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """`field` 為空的未通過項目不得靜靜消失。

    歸不到欄位有兩種來源:候選題目多了一個 schema 以外的欄位,以及後端的欄位歸屬
    退化成 `None`(那是對訊息措辭的正則比對,措辭一漂移就退化)。兩種都不是「沒有
    問題」,而畫面上若只認得七個欄位槽,這一項就會連同「不寫入」這件事一起消失。
    """
    said = "候選題目 含有題目 schema 以外的欄位"
    validator.rejects([{"field": None, "message": said}])
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert said in note(editor_page), "歸不到欄位的那一項在畫面上消失了"


def test_the_verdict_goes_away_when_the_candidate_changes(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """那份判定是針對**那一份候選題目**說的,內容一改就不再成立。

    理由與撞號指認相同:留著的話,維護者改完 FEN 之後仍看得到紅字,而畫面上再也
    沒有東西告訴他那句話講的是上一份內容。
    """
    validator.rejects([{"field": "fen", "message": "引擎載不進這個 fen"}])
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)
    assert message(editor_page, "fen") != ""

    put(editor_page, "fen", "3ak4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")

    assert message(editor_page, "fen") == "", "改了 FEN 之後上一份判定還掛著"


# --- 確認未能完成(4.9)------------------------------------------------


#: 六種「確認未能完成」。四種是服務自己回話說它做不到,兩種是話根本沒傳回來。
#:
#: 對維護者而言六者是同一件事:**這次確認沒有跑完**,所以沒有寫入。分成六種說法
#: 只會讓他去理解 503 與 504 的差別,而那是服務的內部狀態,不是他要處理的事。
UNFINISHED_CASES = [
    ("引擎池滿", 503, {"code": "SERVICE_BUSY", "message": BACKEND_DETAIL}, False),
    ("搜尋逾時", 504, {"code": "ENGINE_TIMEOUT", "message": BACKEND_DETAIL}, False),
    ("未預期失敗", 500, {"code": "INTERNAL", "message": BACKEND_DETAIL}, False),
    ("路由層 404", 404, {"detail": BACKEND_DETAIL}, False),
    ("認不得的形狀", 200, {"ok": True, "message": BACKEND_DETAIL}, False),
    ("連線斷掉", 0, None, True),
]


@pytest.mark.parametrize(
    ("case", "status", "payload", "failing"),
    UNFINISHED_CASES,
    ids=[case for case, _, _, _ in UNFINISHED_CASES],
)
def test_a_service_failure_is_reported_as_a_confirmation_that_did_not_finish(
    editor_page,
    catalog: Catalog,
    validator: Validator,
    case: str,
    status: int,
    payload: object,
    failing: bool,
) -> None:
    """4.9:確認因服務不可用、逾時或連線失敗而無法完成時,告知**確認未能完成**。

    「認不得的形狀」那一格是這條分界最要緊的一種:一份 200 但不是判定的回應
    **絕不能**被讀成通過。判定只有一個形狀(`{valid, issues}`),認不得就是沒有
    判定 —— 而沒有判定時放行,等於把一份沒驗過的題目寫進題庫。

    後端回應裡的原文一個字都不該出現在畫面上:對外的契約只有類別碼(`api.js` 的
    模組說明),原文帶著內部路徑與時間戳,對維護者沒有用處。
    """
    validator.status = status
    validator.payload = payload
    validator.failing = failing
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert UNFINISHED_KEYWORD in note(editor_page), (
        f"{case}:畫面沒有說確認未能完成,而是 {note(editor_page)!r}"
    )
    assert BACKEND_DETAIL not in page_text(editor_page), f"{case}:後端原文流到了畫面上"
    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"{case}:確認未能完成之後 {name} 的內容變了"
        )


def test_a_service_failure_is_never_read_as_a_passing_verdict(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.9 的核心:池滿**不得**被當成驗證通過,也不得被當成這一題不合格。

    前者會讓一份沒驗過的題目直接寫進題庫;後者是誣賴一份可能完全沒問題的題目,還
    把責任推給維護者 —— 他會回頭去改一個本來就對的 FEN。

    序列因此停在這裡:不再有任何請求,七個欄位一句話都不說,而題號沒有被佔走。
    """
    paths: list[str] = []
    editor_page.on("request", lambda request: paths.append(urlsplit(request.url).path))

    validator.fails_with(503, "SERVICE_BUSY")
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)

    api = [path for path in paths if path.startswith("/api/")]
    assert api == [CATALOG_PATH, VALIDATE_PATH], f"確認未能完成卻繼續往下走:{api}"
    for name in FORM_FIELDS:
        assert message(editor_page, name) == "", (
            f"忙碌的服務被說成 {name} 這一欄的問題:{message(editor_page, name)!r}"
        )

    validator.status = 200
    validator.payload = {"valid": True, "issues": []}
    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "id") == "", "確認未能完成的那一次嘗試把題號佔走了"
    assert note(editor_page) == "", "確認未能完成那句話在下一次嘗試之後還掛著"


def test_a_fen_the_service_refuses_to_send_is_not_a_busy_service(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """FEN 的字元把關(400)不是「確認未能完成」——兩者的下一步完全不同。

    字元把關攔在路由函式之前,回的是既有的 `INVALID_MOVE_FORMAT`。它講的是**這串
    FEN 送不出去**(含控制字元或超長),再按幾次都一樣;說成「確認未能完成」會讓
    維護者以為過一會兒重試就好,而他該做的是重貼一次 FEN。反過來,一次池滿也不該
    被說成他的 FEN 有問題。
    """
    validator.fails_with(400, "INVALID_MOVE_FORMAT")
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "fen") != "", "沒有指出問題出在 FEN 那一欄"
    assert UNFINISHED_KEYWORD not in note(editor_page), (
        "送不出去的 FEN 被說成了服務端的問題"
    )
    assert BACKEND_DETAIL not in page_text(editor_page)


# --- 授權的時機:在驗證之後,不在之前(6.1)----------------------------


def test_a_failed_verdict_never_opens_the_directory_picker(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """次序是設計的一部分:**不合格的題目不讓維護者先跳一次目錄選擇框**。

    對話框是這條序列裡唯一會打斷維護者的東西 —— 它蓋住整個視窗、要用鍵盤或滑鼠
    才關得掉。為一份根本寫不出去的題目跳一次,代價完全落在人身上。
    """
    validator.rejects([{"field": "fen", "message": "引擎載不進這個 fen"}])
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "fen") != ""
    assert fs_calls(editor_page) == [], "不合格的題目卻已經去要了目錄授權"


def test_a_collision_never_opens_the_directory_picker(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """撞號擋下的嘗試同樣不碰平台 —— 理由與上一條相同,只是擋在更前面一步。"""
    catalog.ids = [FORM_ID]
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog)

    assert str(FORM_ID) in message(editor_page, "id")
    assert fs_calls(editor_page) == [], "撞號已經擋下這一次嘗試,不該再去要授權"


def test_the_target_file_is_read_only_after_the_directory_is_granted(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """授權 → 重讀 → 寫回,而且**就是這個次序**。

    重讀擺在授權之後才把「讀檔到寫檔」的視窗壓到最小(design 的 Risks):那段期間
    目標檔若被編輯器或 git 改動,追加會蓋掉那次改動。先讀再等使用者挑目錄,等的
    那段時間全都算在視窗裡,而挑目錄要花的正是人的時間 —— 那是這條序列裡最長的
    一段。
    """
    catalog.ids = []
    fs_setup(editor_page, files={TARGET_PATH: EXISTING_FILE})
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert fs_kinds(editor_page) == ["acquire", "read", "write"]
    assert fs_calls(editor_page)[1] == ["read", TARGET_PATH], "重讀的不是目標檔"
    assert fs_calls(editor_page)[2] == ["write", TARGET_PATH], "寫回的不是目標檔"


def test_drawing_and_typing_work_before_any_authorisation(
    editor_page, catalog: Catalog
) -> None:
    """6.4:授權尚未取得時,繪盤與全部欄位的填寫**仍然可用**。

    這是「授權只在按下寫入時才要」的可觀察後果。頁面載入時就跳對話框的實作會在
    這裡被抓到:那時維護者連要不要用這個工具都還沒決定。
    """
    fill_valid_form(editor_page)

    assert editor_page.locator("#board .piece").count() > 0, "沒有授權就畫不出盤面"
    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], f"{name} 填不進去"
    assert write_is_enabled(editor_page), "七欄都填妥了,寫入卻按不下去"
    assert fs_calls(editor_page) == [], "還沒按下寫入就去要了目錄授權"


# --- 平台不支援(6.3)--------------------------------------------------


def reload_as_unsupported(page) -> None:
    """把這一頁重新載入成「平台不支援」的樣子。

    `isSupported()` 在模組求值時就被問到(6.3 要求載入當下即告知),所以旗標必須在
    導覽**之前**種下 —— `add_init_script()` 只對之後的導覽生效,故要重新載入一次。
    """
    page.add_init_script("globalThis.__fsSupported = false;")
    page.reload()
    page.wait_for_function(
        "() => document.querySelectorAll('[data-field=\"difficulty\"] option').length > 1",
        timeout=5000,
    )


def test_an_unsupported_browser_says_so_before_anything_is_pressed(editor_page) -> None:
    """6.3:不支援時**載入當下**就告知,不必等到按下寫入才發現。

    等到按下才說是錯的:維護者會先花時間貼 FEN、填完七個欄位、核對盤面,然後才
    知道這個瀏覽器從一開始就做不到這件事。`isSupported()` 沒有副作用,頁面載入時
    問得起。
    """
    assert not unsupported_is_shown(editor_page), "支援的瀏覽器不該掛著不支援的說明"

    reload_as_unsupported(editor_page)

    assert unsupported_is_shown(editor_page), "不支援的瀏覽器上那句說明沒有出現"
    assert fs_kinds(editor_page) == [], "只是問支不支援,不該去要授權"


def test_an_unsupported_browser_that_is_pressed_anyway_says_why(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """6.3 的另一半:真的按下去時,寫入操作旁那一行也要說得出原因。

    畫面上那句常駐的說明可能被捲出視野,而按下寫入之後維護者的眼睛在按鈕附近。
    兩處說的是同一件事,不是兩種原因。
    """
    reload_as_unsupported(editor_page)

    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)

    assert "不支援" in note(editor_page), f"按下之後說的是 {note(editor_page)!r}"
    assert fs_kinds(editor_page) == ["acquire"], "不支援卻仍然去讀寫了檔案"


# --- 拒絕授權或取消選擇(6.2)------------------------------------------


def test_a_refused_authorisation_keeps_everything_and_says_nothing_was_written(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """6.2:拒絕授權或取消選擇時,**保留已填入的全部內容**,並告知寫入未進行。

    兩件事缺一不可。只保留內容而不說話,畫面看起來與什麼都沒按一樣;只說話而清空
    內容,維護者要重抄一次 FEN —— 而按 Esc 關掉對話框是最容易誤觸的一個動作。
    """
    catalog.ids = []
    fs_setup(editor_page, denied=True, files={TARGET_PATH: EXISTING_FILE})
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert note(editor_page) != "", "拒絕授權之後畫面一句話都沒說"
    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"拒絕授權之後 {name} 的內容變了"
        )
    assert fs_kinds(editor_page) == ["acquire"], "沒拿到授權卻仍然動了檔案"
    assert fs_files(editor_page) == {TARGET_PATH: EXISTING_FILE}, "目標檔被動過了"


def test_a_refused_authorisation_does_not_reserve_the_id(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.4 的規則在這條路上同樣成立:**沒寫成功的嘗試不佔用題號**。

    佔走的話,維護者按了允許之後再送一次,會被自己上一次的失敗擋下來,而畫面說的
    是「這個題號重複」——一句與事實完全相反的話。
    """
    catalog.ids = []
    fs_setup(editor_page, denied=True)
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)
    assert note(editor_page) != ""

    fs_setup(editor_page, denied=False)
    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "id") == "", "被拒絕的那一次嘗試把題號佔走了"
    assert "write" in fs_kinds(editor_page), "給了授權之後仍然沒有寫出去"


# --- 重讀、追加與寫回(5.4–5.9、7.4、7.5)------------------------------


def test_a_successful_write_keeps_every_existing_position_byte_for_byte(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """7.4、7.5:新題進去了,而既有的每一題**逐字不變、一題都不少**。

    斷言寫成「既有全文是新全文的前綴」而不是「解析後比對」,因為兩者證明的東西
    差很多:解析後相等只說明資料一樣,而前綴成立說明**那段位元組根本沒有被重寫
    過** —— 排版、縮排、中文的原樣全都在。這正是 `appendPosition()` 在文字層而不是
    在資料層做事的理由。
    """
    catalog.ids = [21]
    fs_setup(editor_page, files={TARGET_PATH: EXISTING_FILE})
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    written = fs_files(editor_page)[TARGET_PATH]
    kept = EXISTING_FILE[: EXISTING_FILE.rindex("]")].rstrip()
    assert written.startswith(kept), "既有內容被重新序列化過了"

    entries = json.loads(written)
    assert len(entries) == 2, "追加之後不是兩題"
    assert entries[1] == EXPECTED_CANDIDATE, "寫進去的那一題與送去驗證的那一份不同"


def test_a_missing_target_file_becomes_a_single_element_array(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """目標檔還不存在時產出一份只有一題的題目檔(3.2 的三種形態之一)。

    新的書目資料夾收第一題就是這個情形,而 `readTextAt()` 對不存在的檔案回 `null`
    ——那個 `null` 必須一路走到 `appendPosition()`,不能在中間被折成空字串:空字串
    是「檔案在但空的」,那一種要報錯而不是產出新檔。
    """
    catalog.ids = []
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert json.loads(fs_files(editor_page)[TARGET_PATH]) == [EXPECTED_CANDIDATE]


def test_every_attempt_re_reads_the_target_file(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """每一次寫入都**重讀一次**目標檔,不沿用上一次讀到的內容。

    可觀察的後果是連寫兩題會累積成兩題。沿用舊內容的實作在這裡會把第一題蓋掉 ——
    而那正是本工具最不能犯的錯:一次寫入弄丟一題既有的題目。
    """
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)

    put(editor_page, "id", "27")
    attempt_write(editor_page, catalog, validator)

    assert fs_kinds(editor_page) == ["acquire", "read", "write"] * 2
    entries = json.loads(fs_files(editor_page)[TARGET_PATH])
    assert [entry["id"] for entry in entries] == [26, 27], "第二次寫入沒有讀到第一次的結果"


def test_a_target_that_is_not_a_position_array_is_not_written(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """目標檔不是題目陣列時**不寫入**(5.6)。

    這是本工具最危險的一步:一個路徑打錯的目標檔可能是任何東西,而追加是整檔覆寫。
    判準止於「是不是陣列」,但那一道必須真的擋得住 —— 讀到的內容原封不動留在磁碟上。
    """
    junk = '{"這不是": "題目陣列"}\n'
    catalog.ids = []
    fs_setup(editor_page, files={TARGET_PATH: junk})
    fill_valid_form(editor_page)

    attempt_write(editor_page, catalog, validator)

    assert fs_kinds(editor_page) == ["acquire", "read"], "非題目陣列的檔案被寫過了"
    assert fs_files(editor_page) == {TARGET_PATH: junk}, "目標檔的內容變了"


def test_a_failed_append_does_not_reserve_the_id(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """追加失敗同樣不佔用題號 —— 與拒絕授權那一條是同一條規則的另一個入口。"""
    catalog.ids = []
    fs_setup(editor_page, files={TARGET_PATH: "不是 JSON"})
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)
    assert fs_kinds(editor_page) == ["acquire", "read"]

    fs_setup(editor_page, files={})
    attempt_write(editor_page, catalog, validator)

    assert message(editor_page, "id") == "", "追加失敗的那一次嘗試把題號佔走了"
    assert json.loads(fs_files(editor_page)[TARGET_PATH]) == [EXPECTED_CANDIDATE]


def test_a_written_id_is_reserved_for_the_rest_of_the_tab(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """4.4 走完整條真實路徑:**寫入成功之後**那個題號才進入本分頁的已寫入集合。

    5.1 是拿 `recordWrittenId()` 佈置前提來驗撞號規則的,那證明不了有人真的呼叫它。
    這一條從按下寫入開始:第一次成功、第二次同一題號被擋 —— 而題庫索引自始至終
    是空的,所以擋下來的唯一可能來源就是那次成功的寫入。
    """
    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)
    assert "write" in fs_kinds(editor_page), "第一次就沒寫成功,這一條問不出東西"

    attempt_write(editor_page, catalog)

    assert str(FORM_ID) in message(editor_page, "id"), (
        "寫入成功之後題號沒有進入已寫入集合 —— `recordWrittenId()` 沒有被呼叫"
    )
    assert fs_kinds(editor_page).count("write") == 1, "撞號沒擋住,同一題被寫了兩次"


# --- 本任務的邊界:序列停在寫回之後 ------------------------------------


def test_the_write_sequence_stops_after_the_file_is_written(
    editor_page, catalog: Catalog, validator: Validator
) -> None:
    """5.3 實作到寫回為止。

    成功訊息與清空欄位屬 5.4,因此一次成功的嘗試之後畫面上**沒有任何一句話**說它
    成功了,七個欄位也還是原樣。這一條釘住的是「這裡還沒有偷跑」,5.4 會改寫它。
    """
    paths: list[str] = []
    editor_page.on("request", lambda request: paths.append(urlsplit(request.url).path))

    catalog.ids = []
    fill_valid_form(editor_page)
    attempt_write(editor_page, catalog, validator)

    assert "write" in fs_kinds(editor_page)
    api = [path for path in paths if path.startswith("/api/")]
    assert api == [CATALOG_PATH, VALIDATE_PATH], f"寫入流程還打了別的端點:{api}"
    assert note(editor_page) == "", f"成功訊息屬 5.4:{note(editor_page)!r}"
    for name in FORM_FIELDS:
        assert value_of(editor_page, name) == VALID_FORM[name], (
            f"清空欄位屬 5.4,{name} 卻已經被動過"
        )
