"""`web/editor/fs.js` 的驗證:平台檔案 API 的唯一接觸點(5.1、5.11、6.1、6.2、6.3)。

## 這個檔案能證明什麼、不能證明什麼

design 的 Testing Strategy 已載明一個**刻意接受的覆蓋缺口**:

> 檔案系統操作以注入的 `fs.js` 替身驗證,不在測試中真的觸發系統檔案選擇框 ——
> 那個對話框無法由 Playwright 操作。

本檔把那個缺口壓到最小,但**不假裝它不存在**。`fs.js` 讀取的是
`globalThis.showOpenFilePicker`,取得的是一個 `FileSystemFileHandle`;兩者在測試中
都由替身頂替,因此**被驗證的是 `fs.js` 自己的每一行**:能力偵測走哪一邊、錯誤怎麼
分類、控制代碼快取在哪、權限怎麼要、串流什麼時候關。

**沒有被驗證的只有一件事** —— 真的 Chrome 彈出對話框之後,平台交回來的東西是不是
如替身所模擬。那一段要靠人工驗收,自動化到不了。替身的形狀因此刻意貼著規格寫:
`NotFoundError` / `AbortError` / `NotAllowedError` 都是 `DOMException` 且用平台的
名稱,`showOpenFilePicker()` 回的是**陣列**,`createWritable()` 的內容**只有
`close()` 之後才落盤**。替身若與平台分家,這裡會全綠而真實環境會壞,所以替身的
擬真度是本檔唯一需要人來看的東西。

## 由目錄改為單一檔案(requirements R5 的修訂)

原本這一層要的是**整個題庫目錄**的授權,再由組裝層以相對路徑定位目標檔;現在直接
選定那一個 `.json`。三件事因此消失了,本檔也不再測它們:

- 整層路徑解析(`splitPath` / `resolveDirectory`)—— 沒有路徑可以解析
- 「讀不存在的檔案回 `null`」—— 那個檔是使用者剛剛選出來的,它存在
- 「不建中間資料夾」—— 本模組不再建立任何東西

換來的代價寫在 `fs.js` 的模組說明裡:`FileSystemFileHandle` 不帶所在位置,所以
**本層無從判斷這個檔在不在題庫裡**。那道保護移到了維護者的眼睛(他在對話框裡看得見
自己選了什麼)與 `corpus-file.js` 的「不是題目陣列就不寫」。

## 供檔方式

與 `tests/test_web_editor_pure.py` 相同:`page.route()` 就地供 `web/` 的真實檔案,
不啟動伺服器進程。Chromium 不允許自 `file://` 匯入 ES module,而前端以 ES modules
交付且無建置步驟,模組必須在一個真的 http 來源下被載入。

## 模組層狀態的隔離

`fs.js` 的檔案控制代碼**存在模組層變數**(design 的 Invariants、`research.md`
Decision 4),而 ES module 在同一個分頁裡只會被求值一次 —— 這正是「同一次分頁使用
期間沿用,不重複詢問」的實作方式。`browser_page` 每個測試各給一個全新的 context 與
分頁,所以模組狀態不會外溢;同一個測試裡的多次 `page.evaluate()` 則**刻意共用**同一
份模組實例,因為那才是真實分頁裡的情形。
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EDITOR_DIR = WEB_DIR / "editor"
FS_JS = EDITOR_DIR / "fs.js"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-fs.test"

#: 目標題目檔的檔名。**只有檔名,沒有路徑** —— 那正是控制代碼帶得出來的全部資訊。
TARGET_NAME = "26.json"


@pytest.fixture
def module_page(browser_page) -> Iterator:
    """一個位於 http 來源、可以 `import` `web/` 底下模組的空白分頁。"""

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用 web/editor/index.html:那一頁會載入 editor.js,而本檔要
                # 驗證的是 fs.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>收題頁平台包裝驗證</title>",
            )
            return

        target = WEB_DIR / path.lstrip("/")
        if target.is_file():
            route.fulfill(
                status=200,
                content_type="text/javascript; charset=utf-8",
                body=target.read_text(encoding="utf-8"),
            )
            return

        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    browser_page.goto(f"{ORIGIN}/index.html")
    yield browser_page


# =========================================================================
# 平台替身:一個檔案控制代碼,行為貼著 File System Access API 的規格
# =========================================================================

#: 每個測試本體前面都會被接上的替身定義。
#:
#: `store` 是那個檔案的內容(`content`);每一次呼叫都記進 `log`,所以「權限要了
#: 幾次」「close 有沒有被等到」都是可斷言的事實。
FAKES = """
  /** 平台的失敗一律是帶名稱的 DOMException —— fs.js 只能靠 `name` 分類。 */
  function fsError(name) {
    return new DOMException('替身:' + name, name);
  }

  /**
   * 一個檔案控制代碼。
   *
   * `permission` 給了才裝上 `queryPermission` / `requestPermission` —— 兩者在規格上
   * 是**可選的**,而 fs.js 對「平台沒有提供」的處置(以 picker 的結果為準)必須測
   * 得到:不給就是那條路。
   */
  function makeFileHandle(store, log, permission) {
    const handle = {
      kind: 'file',
      name: store.name,
      async getFile() {
        log.push(['getFile']);
        if (typeof store.content !== 'string') throw fsError('NotFoundError');
        return { async text() { return store.content; } };
      },
      async createWritable() {
        log.push(['createWritable']);
        let buffer = '';
        return {
          async write(text) {
            log.push(['write', text]);
            if (store.failWrite) throw fsError('NotAllowedError');
            buffer += text;
          },
          // 平台的承諾:`close()` 之前內容不落盤。替身照做 —— 這是「writeText
          // 回傳時已落盤」唯一測得到的方式。
          async close() { log.push(['close']); store.content = buffer; },
          async abort() { log.push(['abort']); },
        };
      },
    };
    if (permission) {
      handle.queryPermission = async (options) => {
        log.push(['queryPermission', options && options.mode]);
        return permission.query;
      };
      handle.requestPermission = async (options) => {
        log.push(['requestPermission', options && options.mode]);
        if (permission.throws) throw fsError(permission.throws);
        return permission.request;
      };
    }
    return handle;
  }

  /** 一個內容為 `content` 的檔案。 */
  function makeStore(content) {
    return { name: '26.json', content: content === undefined ? '[]' : content };
  }

  /** 把檔案選擇框裝上去,並記錄它收到的選項。 */
  function installPicker(log, behaviour) {
    globalThis.showOpenFilePicker = (options) => {
      log.push(['showOpenFilePicker', options && options.id, options && options.multiple]);
      return behaviour(options);
    };
  }

  /** 讓這個分頁看起來像 Firefox / Safari —— 兩者確定不實作本機檔案選取。 */
  function removePicker() {
    globalThis.showOpenFilePicker = undefined;
  }

  /** 把一個錯誤攤成可以帶回 Python 比對的形狀。 */
  function describe(error, fs) {
    return {
      name: error && error.name,
      message: error && error.message,
      isError: error instanceof Error,
      unsupported: error instanceof fs.UnsupportedBrowserError,
      denied: error instanceof fs.PermissionDeniedError,
    };
  }
"""


def run(page, body: str):
    """把 `body` 當成函式本體執行,其中 `fs` 已綁定為 `fs.js` 的匯出。

    同一個分頁的多次呼叫共用同一份模組實例 —— 模組層的檔案控制代碼因此跨呼叫存續,
    與真實分頁裡的情形一致。
    """
    return page.evaluate(
        "async () => {\n  const fs = await import('/editor/fs.js');\n"
        + FAKES
        + body
        + "\n}"
    )


def source_without_comments(path: pathlib.Path) -> str:
    """去掉註解之後的原始碼。

    註解裡出現 `FileSystemFileHandle` 這種型別名是**說明**而不是接觸 —— 真正的接觸
    是呼叫它的方法。邊界斷言只看得到的程式碼。
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


# =========================================================================
# 能力偵測(6.3):不支援的瀏覽器要在按下之前就講清楚
# =========================================================================


def test_is_supported_is_true_when_the_platform_offers_the_picker(module_page) -> None:
    """判準是「這個全域函式在不在」,不是 UA 字串。

    UA 會被改寫、會有新的瀏覽器加入支援,而能力偵測對兩者都不必修改。
    """
    assert run(
        module_page,
        "  installPicker([], async () => []);\n  return fs.isSupported();",
    ) is True


def test_is_supported_is_false_when_the_platform_lacks_the_picker(module_page) -> None:
    """Firefox 與 Safari 只實作 Origin Private File System,**明確不實作**本機 picker。"""
    assert run(module_page, "  removePicker();\n  return fs.isSupported();") is False


def test_is_supported_does_not_open_the_picker(module_page) -> None:
    """**沒有副作用。**

    頁面載入時就會呼叫它來決定要不要立刻呈現 6.3 的訊息,而那時候不可能有使用者
    手勢,也不該有任何對話框 —— 一個在載入時就掀起系統視窗的頁面是壞掉的。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => []);\n"
        "  fs.isSupported();\n"
        "  fs.isSupported();\n"
        "  return log;",
    ) == []


# =========================================================================
# 選定目標題目檔(5.1、5.11、6.1)
# =========================================================================


def test_pick_returns_the_file_the_user_chose(module_page) -> None:
    """平台回的是**陣列**(它支援多選,我們關掉了),取的是第一個。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  const handle = await fs.pickCorpusFile();\n"
        "  return handle.name;",
    ) == TARGET_NAME


def test_the_picker_is_asked_for_a_single_json_file(module_page) -> None:
    """選項限 `.json` 單選,並帶 `id` 讓瀏覽器記住上一次的資料夾。

    `id` 不是裝飾:連續收題抄的是同一本書的同一卷,第二次開起來就在對的位置。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  let seen = null;\n"
        "  installPicker(log, async (options) => {\n"
        "    seen = options;\n"
        "    return [makeFileHandle(makeStore(), log)];\n"
        "  });\n"
        "  await fs.pickCorpusFile();\n"
        "  return { multiple: seen.multiple, id: seen.id,"
        " extensions: seen.types[0].accept['application/json'] };",
    )
    assert result["multiple"] is False
    assert result["id"], "沒有帶 id,瀏覽器就記不住上一次的資料夾"
    assert result["extensions"] == [".json"]


def test_pick_opens_the_picker_within_the_callers_call_stack(module_page) -> None:
    """**平台的硬性要求**:對話框必須開在使用者手勢的呼叫堆疊內。

    `pickCorpusFile()` 因此刻意不是 `async` 函式 —— 它在被呼叫的那一刻就同步把
    對話框開下去。斷言的方式是「函式回傳之前 picker 就已經被呼叫過」:一個
    `async` 的實作會讓那一行落到 microtask 裡,手勢就沒了。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  const promise = fs.pickCorpusFile();\n"
        "  const openedSynchronously = log.length === 1;\n"
        "  await promise;\n"
        "  return openedSynchronously;",
    ) is True


def test_selected_file_is_null_before_anything_is_picked(module_page) -> None:
    """5.10 的判準:尚未選定時 `selectedFile()` 回 `null`。

    它**不開任何對話框**,所以組裝層每一次重畫都問得起。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  const before = fs.selectedFile();\n"
        "  return { before, calls: log.length };",
    ) == {"before": None, "calls": 0}


def test_selected_file_returns_the_handle_after_it_was_picked(module_page) -> None:
    """6.1:選定之後在同一次分頁使用期間沿用,而且**問它不會再開對話框**。"""
    result = run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  await fs.pickCorpusFile();\n"
        "  const opened = log.length;\n"
        "  const name = fs.selectedFile().name;\n"
        "  return { name, opened, after: log.length };",
    )
    assert result["name"] == TARGET_NAME
    assert result["after"] == result["opened"], "問 selectedFile() 竟然開了對話框"


def test_picking_again_asks_again(module_page) -> None:
    """5.11:**每一次呼叫都重新詢問** —— `pickCorpusFile()` 同時就是「更換目標題目檔」。

    不重複詢問那件事(6.1)由組裝層負責:它以 `selectedFile()` 判斷有沒有選過,
    只在使用者按下選檔按鈕時才呼叫本函式。這條分工讓本模組不必猜「這一次是初次
    選定還是要換檔」。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  await fs.pickCorpusFile();\n"
        "  await fs.pickCorpusFile();\n"
        "  return log.filter((entry) => entry[0] === 'showOpenFilePicker').length;",
    ) == 2


def test_overlapping_callers_share_one_dialog(module_page) -> None:
    """兩個重疊的呼叫只開一個對話框。

    各開一個的話,使用者會看到兩個疊在一起的系統視窗,而選完第一個之後第二個還在
    那裡 —— 沒有任何說法能解釋那個畫面。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  const [a, b] = await Promise.all([fs.pickCorpusFile(), fs.pickCorpusFile()]);\n"
        "  return { same: a === b,"
        " opened: log.filter((entry) => entry[0] === 'showOpenFilePicker').length };",
    ) == {"same": True, "opened": 1}


# =========================================================================
# 兩種失敗必須分得開(6.2、6.3)
# =========================================================================


def test_pick_reports_an_unsupported_browser(module_page) -> None:
    """不支援時**連對話框都不試著開**,直接以專屬型別表達。"""
    result = run(
        module_page,
        "  removePicker();\n"
        "  try { await fs.pickCorpusFile(); return null; }\n"
        "  catch (error) { return describe(error, fs); }",
    )
    assert result["unsupported"] is True
    assert result["denied"] is False


@pytest.mark.parametrize(
    "name",
    ["AbortError", "NotAllowedError"],
    ids=["取消對話框", "按了封鎖"],
)
def test_pick_reports_a_refusal_as_permission_denied(module_page, name: str) -> None:
    """平台的兩種「使用者不給」收斂成同一種失敗。

    `AbortError` 是關掉或取消了對話框,`NotAllowedError` 是在權限提示上按了封鎖。
    兩者對使用者是同一件事 —— 這次沒選到檔 —— 下一步也一樣。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        f"  installPicker(log, async () => {{ throw new DOMException('x', '{name}'); }});\n"
        "  try { await fs.pickCorpusFile(); return null; }\n"
        "  catch (error) { return describe(error, fs); }",
    )
    assert result["denied"] is True
    assert result["unsupported"] is False


def test_the_two_failures_are_different_identifiable_errors(module_page) -> None:
    """兩者**必須分得開**:使用者接下來能做的事完全不同。

    不支援要換一個瀏覽器,拒絕只要再選一次。合成同一種的話,呈現層就只剩下比對
    訊息字串這條路,而訊息不是契約。
    """
    result = run(
        module_page,
        "  return { same: fs.UnsupportedBrowserError === fs.PermissionDeniedError,\n"
        "    unsupportedIsError: (new fs.UnsupportedBrowserError()) instanceof Error,\n"
        "    deniedIsError: (new fs.PermissionDeniedError()) instanceof Error,\n"
        "    crossA: (new fs.UnsupportedBrowserError()) instanceof fs.PermissionDeniedError,\n"
        "    crossB: (new fs.PermissionDeniedError()) instanceof fs.UnsupportedBrowserError };",
    )
    assert result == {
        "same": False,
        "unsupportedIsError": True,
        "deniedIsError": True,
        "crossA": False,
        "crossB": False,
    }


def test_pick_can_be_tried_again_after_a_refusal(module_page) -> None:
    """6.2 要求拒絕之後保留全部內容,而那件事要有意義,就得讓維護者能再選一次。

    把失敗留在那個「進行中的請求」欄位裡會讓第二次拿到同一個失敗,使用者就只剩下
    重整頁面這條路,而重整會把辛苦貼好的 FEN 一起帶走。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  let refuse = true;\n"
        "  installPicker(log, async () => {\n"
        "    if (refuse) throw new DOMException('x', 'AbortError');\n"
        "    return [makeFileHandle(makeStore(), log)];\n"
        "  });\n"
        "  try { await fs.pickCorpusFile(); } catch { /* 預期內 */ }\n"
        "  refuse = false;\n"
        "  const handle = await fs.pickCorpusFile();\n"
        "  return handle.name;",
    ) == TARGET_NAME


def test_a_cancelled_change_keeps_the_file_already_selected(module_page) -> None:
    """按了「更換題目檔」又改變主意時,**原本那個檔應該還在**。

    把它一起清掉等於用一次取消把上一次的選擇也撤銷了 —— 維護者只是關掉一個對話框,
    卻發現自己得重選一次,而畫面上也沒有東西解釋為什麼。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  let refuse = false;\n"
        "  installPicker(log, async () => {\n"
        "    if (refuse) throw new DOMException('x', 'AbortError');\n"
        "    return [makeFileHandle(makeStore(), log)];\n"
        "  });\n"
        "  await fs.pickCorpusFile();\n"
        "  refuse = true;\n"
        "  try { await fs.pickCorpusFile(); } catch { /* 預期內 */ }\n"
        "  return fs.selectedFile() === null ? null : fs.selectedFile().name;",
    ) == TARGET_NAME


# =========================================================================
# 寫入權限要在同一個手勢內要齊(6.1)
# =========================================================================


def test_pick_requests_write_permission_when_it_is_not_granted_yet(module_page) -> None:
    """`showOpenFilePicker()` 給的是**唯讀**權限,寫入權限一定要在這裡補要。

    這與目錄選擇框不同 —— 那邊有 `mode` 選項。補要仍然落在同一個使用者手勢內,
    換到別的地方要就來不及了。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const permission = { query: 'prompt', request: 'granted' };\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log, permission)]);\n"
        "  await fs.pickCorpusFile();\n"
        "  return log.filter((entry) => entry[0].endsWith('Permission'));",
    )
    assert result == [["queryPermission", "readwrite"], ["requestPermission", "readwrite"]]


def test_pick_does_not_prompt_again_when_permission_is_already_granted(
    module_page,
) -> None:
    """已經有權限就不再問 —— 多跳一次權限提示是純粹的打擾。"""
    result = run(
        module_page,
        "  const log = [];\n"
        "  const permission = { query: 'granted', request: 'granted' };\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log, permission)]);\n"
        "  await fs.pickCorpusFile();\n"
        "  return log.filter((entry) => entry[0] === 'requestPermission').length;",
    )
    assert result == 0


def test_a_platform_without_permission_methods_falls_back_to_the_picker(
    module_page,
) -> None:
    """兩個方法在規格上是**可選的**:沒有提供時以 picker 的結果為準。

    在這裡自己造一個「拒絕」出來,只會把一個能寫的檔講成不能寫。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  const handle = await fs.pickCorpusFile();\n"
        "  return handle.name;",
    ) == TARGET_NAME


def test_a_refused_write_permission_keeps_nothing(module_page) -> None:
    """權限被拒時**不留下控制代碼**。

    留下來會讓後續的寫入拿著一個寫不了的控制代碼去撞牆,而那時候的錯誤發生在寫檔
    那一步 —— 離真正的原因(權限)已經很遠了。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const permission = { query: 'prompt', request: 'denied' };\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log, permission)]);\n"
        "  let failure = null;\n"
        "  try { await fs.pickCorpusFile(); }\n"
        "  catch (error) { failure = describe(error, fs); }\n"
        "  return { failure, selected: fs.selectedFile() };",
    )
    assert result["failure"]["denied"] is True
    assert result["selected"] is None


def test_pick_lets_a_programming_error_through(module_page) -> None:
    """`SecurityError` **不併入「使用者拒絕」**。

    它代表呼叫時沒有有效的使用者手勢,是呼叫端的 bug。報成「你拒絕了授權」會讓
    維護者一直去按允許,而畫面永遠不會變。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => { throw new DOMException('x', 'SecurityError'); });\n"
        "  try { await fs.pickCorpusFile(); return null; }\n"
        "  catch (error) { return describe(error, fs); }",
    )
    assert result["name"] == "SecurityError"
    assert result["denied"] is False
    assert result["unsupported"] is False


# =========================================================================
# 讀與寫
# =========================================================================


def test_read_text_returns_the_contents(module_page) -> None:
    assert run(
        module_page,
        "  const log = [];\n"
        "  const store = makeStore('[{\\\"id\\\": 25}]');\n"
        "  const handle = makeFileHandle(store, log);\n"
        "  return await fs.readText(handle);",
    ) == '[{"id": 25}]'


def test_read_text_does_not_swallow_a_vanished_file(module_page) -> None:
    """**不回 `null`** —— 與舊的 `readTextAt()` 刻意不同。

    那時候路徑是打出來的,「不存在」是一個正常的結果(5.4 的建新檔);現在這個檔是
    使用者剛剛在對話框裡選出來的,它存在。選檔之後被外部刪掉是**失敗**而不是一種
    正常狀態,原樣往上交由 7.3 的一般寫入失敗處理。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const handle = makeFileHandle({ name: '26.json', content: null }, log);\n"
        "  try { return { value: await fs.readText(handle) }; }\n"
        "  catch (error) { return { name: error.name }; }",
    )
    assert result == {"name": "NotFoundError"}


def test_write_text_replaces_the_whole_file(module_page) -> None:
    """**整檔覆寫**:內容是 `appendPosition()` 的輸出。

    既有題目的逐字不變(5.7)由那個輸出保證,不由寫入方式保證。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  const store = makeStore('舊的內容,應該完全消失');\n"
        "  const handle = makeFileHandle(store, log);\n"
        "  await fs.writeText(handle, '新的全文');\n"
        "  return store.content;",
    ) == "新的全文"


def test_write_text_resolves_only_after_the_stream_is_closed(module_page) -> None:
    """平台在 `close()` 之前不落盤,所以「有沒有等到 close」就是「能不能相信寫完了」。

    7.1 的成功訊息、以及成功之後才記下題號(4.4),都建立在這一點上。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  const handle = makeFileHandle(makeStore(), log);\n"
        "  await fs.writeText(handle, '內容');\n"
        "  return log.map((entry) => entry[0]);",
    ) == ["createWritable", "write", "close"]


def test_write_text_abandons_the_stream_when_writing_fails(module_page) -> None:
    """寫到一半失敗就**放棄**這個串流,不以 `close()` 收尾。

    `close()` 會把半份內容落盤,而那正好是最糟的結果 —— 一個解析不開的題目檔會讓
    服務下次啟動直接拒絕啟動。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const store = makeStore('原本的內容');\n"
        "  store.failWrite = true;\n"
        "  const handle = makeFileHandle(store, log);\n"
        "  let failed = false;\n"
        "  try { await fs.writeText(handle, '寫不進去'); } catch { failed = true; }\n"
        "  return { failed, kinds: log.map((entry) => entry[0]), content: store.content };",
    )
    assert result["failed"] is True
    assert "abort" in result["kinds"], "沒有放棄串流"
    assert "close" not in result["kinds"], "失敗之後仍然 close 了,半份內容會落盤"
    assert result["content"] == "原本的內容", "磁碟上的內容被動過了"


# =========================================================================
# 邊界:這一層的存在理由
# =========================================================================


def test_fs_is_the_only_module_that_touches_the_platform_file_api() -> None:
    """design 的 Boundary Commitments:`fs.js` 是 File System Access API 的唯一接觸點。

    以原始碼斷言而非執行期行為 —— 這條界線的價值在於「其餘模組在沒有這個 API 的
    環境下仍測得動」,而那是一件關於**程式碼裡有沒有那行**的事實。

    比對的是**呼叫**而不是型別名:別的模組在 JSDoc 裡寫 `FileSystemFileHandle` 只是
    在描述它拿到的東西,那不是接觸。註解因此先被去掉。
    """
    platform_calls = re.compile(
        r"\bshow(?:Directory|OpenFile|SaveFile)Picker\b"
        r"|\bgetDirectoryHandle\b"
        r"|\bgetFileHandle\b"
        r"|\bcreateWritable\b"
        r"|\b(?:query|request)Permission\b"
    )

    culprits = sorted(
        path.name
        for path in EDITOR_DIR.rglob("*")
        if path.is_file() and platform_calls.search(source_without_comments(path))
    )

    assert culprits == ["fs.js"], f"平台檔案 API 只能出現在 fs.js,卻也出現在:{culprits}"


def test_fs_does_not_persist_the_file_handle() -> None:
    """`research.md` Decision 4:控制代碼只存在模組層變數,不進任何持久化儲存。

    持久化救不回權限(權限隨分頁關閉而失效),卻要換來儲存、版本與失效處理。
    這條斷言擋的是日後「順手存一下比較方便」的那一次改動。
    """
    code = source_without_comments(FS_JS)

    forbidden = [
        token
        for token in (
            "indexedDB",
            "IDBDatabase",
            "localStorage",
            "sessionStorage",
            "document",
            "fetch(",
            "XMLHttpRequest",
        )
        if token in code
    ]

    assert not forbidden, f"fs.js 不得使用:{forbidden}"


def test_fs_keeps_the_handle_in_a_module_level_variable(module_page) -> None:
    """控制代碼的存續範圍就是模組的存續範圍:重新載入模組即回到未選定狀態。

    這正是 6.1「同一次分頁使用期間沿用」與平台授權週期吻合的方式,也是「重整頁面後
    要重選一次檔」這個已被 6.1 接受的代價的來源。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => [makeFileHandle(makeStore(), log)]);\n"
        "  await fs.pickCorpusFile();\n"
        "  const again = await import('/editor/fs.js?reloaded');\n"
        "  return again.selectedFile();",
    ) is None


def test_fs_has_no_path_handling_left() -> None:
    """**本模組不再處理任何路徑**(requirements R5 的修訂)。

    `FileSystemFileHandle` 只帶得出 `name`,不帶所在位置 —— 一份「切路徑、逐段走
    資料夾」的程式碼在這裡已經沒有對象。留著會讓下一個人以為工具還認得路徑,而那
    正是這次修訂要消滅的概念。

    這條同時守住 design 的安全性主張:路徑穿越**構造上不存在**,因為沒有路徑。
    """
    code = source_without_comments(FS_JS)

    leftovers = [
        token
        for token in ("splitPath", "resolveDirectory", "relativePath", "getDirectoryHandle")
        if token in code
    ]

    assert not leftovers, f"fs.js 仍留著路徑處理的痕跡:{leftovers}"
