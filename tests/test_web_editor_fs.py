"""`web/editor/fs.js` 的驗證:平台檔案 API 的唯一接觸點(6.1、6.2、6.3)。

## 這個檔案能證明什麼、不能證明什麼

design 的 Testing Strategy 已載明一個**刻意接受的覆蓋缺口**:

> 檔案系統操作以注入的 `fs.js` 替身驗證,不在測試中真的觸發系統目錄選擇框 ——
> 那個對話框無法由 Playwright 操作。

本檔把那個缺口壓到最小,但**不假裝它不存在**。`fs.js` 讀取的是
`globalThis.showDirectoryPicker`,取得的是一個 `FileSystemDirectoryHandle`;兩者在
測試中都由替身頂替,因此**被驗證的是 `fs.js` 自己的每一行**:能力偵測走哪一邊、
錯誤怎麼分類、控制代碼快取在哪、路徑怎麼走、串流什麼時候關。

**沒有被驗證的只有一件事** —— 真的 Chrome 彈出對話框之後,平台交回來的東西是不是
如替身所模擬。那一段要靠人工驗收(見本檔末的說明與實作報告),自動化到不了。
替身的形狀因此刻意貼著規格寫:`NotFoundError` / `AbortError` / `NotAllowedError`
都是 `DOMException` 且用平台的名稱,`createWritable()` 的內容**只有 `close()` 之後
才落盤**,名稱不合法時丟 `TypeError`。替身若與平台分家,這裡會全綠而真實環境會壞,
所以替身的擬真度是本檔唯一需要人來看的東西。

## 供檔方式

與 `tests/test_web_editor_pure.py` 相同:`page.route()` 就地供 `web/` 的真實檔案,
不啟動伺服器進程。Chromium 不允許自 `file://` 匯入 ES module,而前端以 ES modules
交付且無建置步驟,模組必須在一個真的 http 來源下被載入。

## 模組層狀態的隔離

`fs.js` 的目錄控制代碼**存在模組層變數**(design 的 Invariants、`research.md`
Decision 4),而 ES module 在同一個分頁裡只會被求值一次 —— 這正是「同一分頁只詢問
一次」的實作方式。`browser_page` 每個測試各給一個全新的 context 與分頁,所以模組
狀態不會外溢;同一個測試裡的多次 `page.evaluate()` 則**刻意共用**同一份模組實例,
因為那才是真實分頁裡的情形。
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

#: 題庫目錄底下的一個真實形狀的目標路徑(`structure.md` 的目錄佈局)。
TARGET_PATH = "適情雅趣~卷一/26.json"


@pytest.fixture
def module_page(browser_page) -> Iterator:
    """一個位於 http 來源、可以 `import` `web/` 底下模組的空白分頁。"""

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用 web/editor/index.html:那一頁日後會載入 editor.js,而本檔
                # 要驗證的是 fs.js 能單獨運作。
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
# 平台替身:一棵記憶體中的檔案樹,行為貼著 File System Access API 的規格
# =========================================================================

#: 每個測試本體前面都會被接上的替身定義。
#:
#: 檔案樹以巢狀物件表示:字串的值是**檔案內容**,物件的值是**資料夾**。
#: 每一次呼叫都記進 `log`,所以「有沒有帶 create」「順序是什麼」都是可斷言的事實。
FAKES = """
  /** 平台的失敗一律是帶名稱的 DOMException —— fs.js 只能靠 `name` 分類。 */
  function fsError(name) {
    return new DOMException('替身:' + name, name);
  }

  /** 名稱是不是平台會接受的一段。`/`、`.`、`..` 一律 TypeError,這是平台的圍籬。 */
  function rejectBadName(name) {
    if (name === '' || name === '.' || name === '..' || name.includes('/')) {
      throw new TypeError('替身:不合法的名稱 ' + JSON.stringify(name));
    }
  }

  function makeDirectory(tree, log) {
    return {
      kind: 'directory',
      async getDirectoryHandle(name, options) {
        const create = Boolean(options && options.create);
        log.push(['getDirectoryHandle', name, create]);
        rejectBadName(name);
        const child = tree[name];
        if (typeof child === 'string') throw fsError('TypeMismatchError');
        if (child === undefined) {
          if (!create) throw fsError('NotFoundError');
          tree[name] = {};
        }
        return makeDirectory(tree[name], log);
      },
      async getFileHandle(name, options) {
        const create = Boolean(options && options.create);
        log.push(['getFileHandle', name, create]);
        rejectBadName(name);
        const value = tree[name];
        if (value !== undefined && typeof value !== 'string') {
          throw fsError('TypeMismatchError');
        }
        if (value === undefined) {
          if (!create) throw fsError('NotFoundError');
          tree[name] = '';
        }
        return makeFile(tree, name, log);
      },
    };
  }

  function makeFile(tree, name, log) {
    return {
      kind: 'file',
      async getFile() {
        log.push(['getFile', name]);
        const content = tree[name];
        if (typeof content !== 'string') throw fsError('NotFoundError');
        return { async text() { return content; } };
      },
      async createWritable() {
        log.push(['createWritable', name]);
        let buffer = '';
        return {
          async write(text) {
            log.push(['write', text]);
            if (tree.__failWrite) throw fsError('NotAllowedError');
            buffer += text;
          },
          // 平台的承諾:`close()` 之前內容不落盤。替身照做 —— 這是「writeTextAt
          // 回傳時已落盤」唯一測得到的方式。
          async close() {
            log.push(['close', name]);
            tree[name] = buffer;
          },
          async abort() {
            log.push(['abort', name]);
          },
        };
      },
    };
  }

  /** 把某個名稱的目錄選擇框裝上去,並記錄它收到的 mode。 */
  function installPicker(log, behaviour) {
    globalThis.showDirectoryPicker = (options) => {
      log.push(['showDirectoryPicker', options && options.mode]);
      return behaviour(options);
    };
  }

  /** 讓這個分頁看起來像 Firefox / Safari —— 兩者確定不實作本機目錄選取。 */
  function removePicker() {
    globalThis.showDirectoryPicker = undefined;
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

    同一個分頁的多次呼叫共用同一份模組實例 —— 模組層的目錄控制代碼因此跨呼叫存續,
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

    註解裡出現 `FileSystemDirectoryHandle` 這種型別名是**說明**而不是接觸 ——
    真正的接觸是呼叫它的方法。邊界斷言只看得到的程式碼。
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


# =========================================================================
# 能力偵測(6.3):不支援的瀏覽器要在按下寫入之前就講清楚
# =========================================================================


def test_is_supported_is_true_when_the_platform_offers_the_picker(module_page) -> None:
    """Chrome / Edge / Opera 86+ 桌面版提供 `showDirectoryPicker`。"""
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  installPicker(log, async () => makeDirectory({}, log));\n"
            "  return fs.isSupported();",
        )
        is True
    )


def test_is_supported_is_false_when_the_platform_lacks_the_picker(module_page) -> None:
    """Firefox 與 Safari **明確不實作**這一組方法,不是版本落後(`research.md`)。

    6.3 的訊息要在頁面載入時就呈現,所以判定必須不觸發任何對話框。
    """
    assert (
        run(
            module_page,
            "  removePicker();\n  return fs.isSupported();",
        )
        is False
    )


def test_is_supported_does_not_open_the_picker(module_page) -> None:
    """能力偵測不得有副作用:它會在頁面載入時就被呼叫(design 的 Validation)。"""
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  installPicker(log, async () => makeDirectory({}, log));\n"
            "  fs.isSupported();\n"
            "  fs.isSupported();\n"
            "  return log;",
        )
        == []
    )


# =========================================================================
# 取得目錄授權(6.1、6.2、6.3)
# =========================================================================


def test_acquire_returns_the_directory_the_user_picked(module_page) -> None:
    """取得授權後回傳的就是使用者選定的那個控制代碼。"""
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  const picked = makeDirectory({}, log);\n"
            "  installPicker(log, async () => picked);\n"
            "  const handle = await fs.acquireCorpusDirectory();\n"
            "  return handle === picked;",
        )
        is True
    )


def test_acquire_asks_for_write_access_up_front(module_page) -> None:
    """一次要到讀寫權限,不是先讀再回頭補寫 —— 補的那次沒有使用者手勢可用。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => makeDirectory({}, log));\n"
        "  await fs.acquireCorpusDirectory();\n"
        "  return log;",
    ) == [["showDirectoryPicker", "readwrite"]]


def test_acquire_opens_the_picker_within_the_callers_call_stack(module_page) -> None:
    """對話框必須在**呼叫端的同步呼叫堆疊**內開啟(design 的 Preconditions)。

    平台要求 picker 由使用者手勢觸發。若本模組在開啟對話框之前先 `await` 了別的
    東西,手勢的有效期就可能已經過去,而那是自動化測試唯一測得到的部分 ——
    「有沒有在同一個 tick 內呼叫下去」。
    """
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  let openedSynchronously = false;\n"
            "  installPicker(log, async () => {\n"
            "    openedSynchronously = true;\n"
            "    return makeDirectory({}, log);\n"
            "  });\n"
            "  const pending = fs.acquireCorpusDirectory();\n"
            "  const observed = openedSynchronously;\n"
            "  await pending;\n"
            "  return observed;",
        )
        is True
    )


def test_acquire_asks_only_once_per_tab(module_page) -> None:
    """Invariant:同一分頁只詢問一次目錄。第二次直接回同一個控制代碼。

    平台的寫入權限本來就活到「該來源所有分頁關閉」為止,所以快取在模組層變數裡
    的存續期間與權限的存續期間**完全吻合**(`research.md` Decision 4)。
    """
    first = run(
        module_page,
        "  const log = [];\n"
        "  globalThis.__log = log;\n"
        "  installPicker(log, async () => makeDirectory({}, log));\n"
        "  const a = await fs.acquireCorpusDirectory();\n"
        "  const b = await fs.acquireCorpusDirectory();\n"
        "  return { same: a === b, log };",
    )
    assert first == {"same": True, "log": [["showDirectoryPicker", "readwrite"]]}

    # 另一次 evaluate 等同使用者稍後又按了一次寫入:模組實例同一份,仍不再詢問。
    assert run(
        module_page,
        "  await fs.acquireCorpusDirectory();\n  return globalThis.__log;",
    ) == [["showDirectoryPicker", "readwrite"]]


def test_acquire_asks_only_once_for_overlapping_callers(module_page) -> None:
    """兩個還沒完成的請求也只該開一次對話框 —— 兩個框疊在一起是無從解釋的畫面。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => makeDirectory({}, log));\n"
        "  const [a, b] = await Promise.all([\n"
        "    fs.acquireCorpusDirectory(),\n"
        "    fs.acquireCorpusDirectory(),\n"
        "  ]);\n"
        "  return { same: a === b, opened: log.length };",
    ) == {"same": True, "opened": 1}


def test_acquire_reports_an_unsupported_browser(module_page) -> None:
    """6.3:不支援時要明確告知,而不是丟一個平台的原始例外。"""
    result = run(
        module_page,
        "  removePicker();\n"
        "  try {\n"
        "    await fs.acquireCorpusDirectory();\n"
        "    return null;\n"
        "  } catch (error) { return describe(error, fs); }",
    )
    assert result["unsupported"] is True
    assert result["denied"] is False
    assert result["name"] == "UnsupportedBrowserError"
    assert result["message"]


@pytest.mark.parametrize(
    "platform_error",
    [
        # 使用者按了取消 —— 平台以 AbortError 表達。
        "AbortError",
        # 使用者在權限提示上按了封鎖。
        "NotAllowedError",
    ],
)
def test_acquire_reports_a_refusal_as_permission_denied(
    module_page, platform_error: str
) -> None:
    """6.2:拒絕授權或取消目錄選擇是**同一件事**的兩種按法,都要能被辨識出來。"""
    result = run(
        module_page,
        "  const log = [];\n"
        f"  installPicker(log, async () => {{ throw fsError('{platform_error}'); }});\n"
        "  try {\n"
        "    await fs.acquireCorpusDirectory();\n"
        "    return null;\n"
        "  } catch (error) { return describe(error, fs); }",
    )
    assert result["denied"] is True
    assert result["unsupported"] is False
    assert result["name"] == "PermissionDeniedError"
    assert result["message"]


def test_the_two_failures_are_different_identifiable_errors(module_page) -> None:
    """「不支援」與「被拒絕」必須分得開:兩者的訊息與可行的下一步完全不同。

    不支援是換一個瀏覽器,被拒絕是再按一次寫入。用同一個錯誤表達,呈現層就只能
    去比對訊息字串,而訊息不是契約。
    """
    assert run(
        module_page,
        "  return {\n"
        "    distinct: fs.UnsupportedBrowserError !== fs.PermissionDeniedError,\n"
        "    unsupportedIsNotDenied:\n"
        "      !(new fs.UnsupportedBrowserError() instanceof fs.PermissionDeniedError),\n"
        "    deniedIsNotUnsupported:\n"
        "      !(new fs.PermissionDeniedError() instanceof fs.UnsupportedBrowserError),\n"
        "    bothAreErrors:\n"
        "      new fs.UnsupportedBrowserError() instanceof Error &&\n"
        "      new fs.PermissionDeniedError() instanceof Error,\n"
        "  };",
    ) == {
        "distinct": True,
        "unsupportedIsNotDenied": True,
        "deniedIsNotUnsupported": True,
        "bothAreErrors": True,
    }


def test_acquire_can_be_tried_again_after_a_refusal(module_page) -> None:
    """6.2 的「保留內容」要有意義,就得讓維護者能再按一次寫入。

    失敗的嘗試若被快取起來,第二次會拿到同一個失敗而永遠不再開對話框 ——
    使用者就只能重整頁面,而重整會把辛苦貼好的 FEN 一起帶走。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  let attempt = 0;\n"
        "  const picked = makeDirectory({}, log);\n"
        "  installPicker(log, async () => {\n"
        "    attempt += 1;\n"
        "    if (attempt === 1) throw fsError('AbortError');\n"
        "    return picked;\n"
        "  });\n"
        "  let first = null;\n"
        "  try { await fs.acquireCorpusDirectory(); } catch (error) { first = error.name; }\n"
        "  const second = await fs.acquireCorpusDirectory();\n"
        "  return { first, recovered: second === picked, opened: log.length };",
    ) == {"first": "PermissionDeniedError", "recovered": True, "opened": 2}


def test_acquire_requests_write_permission_when_it_is_not_granted_yet(
    module_page,
) -> None:
    """控制代碼到手不等於權限到手 —— 尚未授予時要在同一個手勢裡把它要齊。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const picked = makeDirectory({}, log);\n"
        "  picked.queryPermission = async (options) => {\n"
        "    log.push(['queryPermission', options.mode]);\n"
        "    return 'prompt';\n"
        "  };\n"
        "  picked.requestPermission = async (options) => {\n"
        "    log.push(['requestPermission', options.mode]);\n"
        "    return 'granted';\n"
        "  };\n"
        "  installPicker(log, async () => picked);\n"
        "  const handle = await fs.acquireCorpusDirectory();\n"
        "  return { granted: handle === picked, log };",
    ) == {
        "granted": True,
        "log": [
            ["showDirectoryPicker", "readwrite"],
            ["queryPermission", "readwrite"],
            ["requestPermission", "readwrite"],
        ],
    }


def test_acquire_does_not_prompt_again_when_permission_is_already_granted(
    module_page,
) -> None:
    """已是 `granted` 就不再要一次 —— 多一次請求就多一個使用者看不懂的提示。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const picked = makeDirectory({}, log);\n"
        "  picked.queryPermission = async (options) => {\n"
        "    log.push(['queryPermission', options.mode]);\n"
        "    return 'granted';\n"
        "  };\n"
        "  picked.requestPermission = async () => {\n"
        "    log.push(['requestPermission']);\n"
        "    return 'granted';\n"
        "  };\n"
        "  installPicker(log, async () => picked);\n"
        "  await fs.acquireCorpusDirectory();\n"
        "  return log;",
    ) == [
        ["showDirectoryPicker", "readwrite"],
        ["queryPermission", "readwrite"],
    ]


def test_acquire_reports_a_refused_write_permission_and_keeps_nothing(
    module_page,
) -> None:
    """權限被拒 = 6.2 的拒絕授權,且**不得**把那個控制代碼留下來當成已授權。"""
    result = run(
        module_page,
        "  const log = [];\n"
        "  const picked = makeDirectory({}, log);\n"
        "  picked.queryPermission = async () => 'prompt';\n"
        "  picked.requestPermission = async () => {\n"
        "    log.push(['requestPermission']);\n"
        "    return 'denied';\n"
        "  };\n"
        "  installPicker(log, async () => picked);\n"
        "  let first = null;\n"
        "  try { await fs.acquireCorpusDirectory(); } catch (error) { first = describe(error, fs); }\n"
        "  let second = null;\n"
        "  try { await fs.acquireCorpusDirectory(); } catch (error) { second = error.name; }\n"
        "  return { first, second, opened: log.filter((e) => e[0] === 'showDirectoryPicker').length };",
    )
    assert result["first"]["denied"] is True
    assert result["first"]["unsupported"] is False
    assert result["first"]["message"]
    assert result["second"] == "PermissionDeniedError"
    # 被拒的那個控制代碼沒有被留下來:第二次仍然重新詢問,維護者按了允許就能繼續。
    assert result["opened"] == 2


def test_acquire_lets_a_programming_error_through(module_page) -> None:
    """不是使用者拒絕的失敗**不得**被講成拒絕授權。

    picker 在沒有使用者手勢時丟的是 `SecurityError`,那是呼叫端的 bug(6.1 要求
    授權請求掛在寫入的點擊處理常式內)。把它折成「你拒絕了授權」會讓維護者一直去
    按允許,而畫面永遠不會變。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => { throw fsError('SecurityError'); });\n"
        "  try {\n"
        "    await fs.acquireCorpusDirectory();\n"
        "    return null;\n"
        "  } catch (error) { return describe(error, fs); }",
    )
    assert result["name"] == "SecurityError"
    assert result["denied"] is False
    assert result["unsupported"] is False


# =========================================================================
# 讀取(`readTextAt`):不存在回 null,是 corpus-file.js 依賴的契約
# =========================================================================


def test_read_text_at_returns_the_contents_of_a_nested_file(module_page) -> None:
    """相對路徑逐段走下去,取回目標檔的全文。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = { '適情雅趣~卷一': { '26.json': '[\\n]\\n' } };\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  const text = await fs.readTextAt(dir, {TARGET_PATH!r});\n"
        "  return { text, log };",
    ) == {
        "text": "[\n]\n",
        "log": [
            ["getDirectoryHandle", "適情雅趣~卷一", False],
            ["getFileHandle", "26.json", False],
            ["getFile", "26.json"],
        ],
    }


def test_read_text_at_returns_null_for_a_missing_file(module_page) -> None:
    """不存在**回傳 null 而非拋出**。

    這是 3.2 已經依賴的契約:`corpus-file.js` 把 `null` 當成「建一個新檔」,
    把 `""` 當成錯誤。不存在若走例外,那條路就得靠呼叫端去辨認平台的錯誤名稱,
    平台細節也就漏了出去。
    """
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  const tree = { '適情雅趣~卷一': {} };\n"
            "  const dir = makeDirectory(tree, log);\n"
            f"  return await fs.readTextAt(dir, {TARGET_PATH!r});",
        )
        is None
    )


def test_read_text_at_returns_null_when_the_folder_is_missing(module_page) -> None:
    """資料夾不存在,那個檔案當然也不存在 —— 同一個答案,不是另一種失敗。"""
    assert (
        run(
            module_page,
            "  const log = [];\n"
            "  const dir = makeDirectory({}, log);\n"
            f"  return await fs.readTextAt(dir, {TARGET_PATH!r});",
        )
        is None
    )


def test_read_text_at_tells_an_empty_file_from_a_missing_one(module_page) -> None:
    """空檔案是 `""`,不存在是 `null`。一種狀態只有一個表示法(tasks 3.2 的筆記)。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = { '適情雅趣~卷一': { '26.json': '' } };\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  const empty = await fs.readTextAt(dir, {TARGET_PATH!r});\n"
        "  const missing = await fs.readTextAt(dir, '適情雅趣~卷一/27.json');\n"
        "  return { empty, missing, distinct: empty !== missing };",
    ) == {"empty": "", "missing": None, "distinct": True}


def test_read_text_at_creates_nothing(module_page) -> None:
    """讀取就只是讀取:一次不存在的讀取不得在磁碟上留下空資料夾或空檔案。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = {};\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  await fs.readTextAt(dir, {TARGET_PATH!r});\n"
        "  return { tree, created: log.filter((entry) => entry[2] === true) };",
    ) == {"tree": {}, "created": []}


@pytest.mark.parametrize("operation", ["readTextAt", "writeTextAt"])
def test_a_path_with_no_segments_is_a_calling_bug(module_page, operation: str) -> None:
    """指不到任何檔案的路徑不是「不存在」,是呼叫端傳錯了東西。

    回 `null` 會把一個程式錯誤講成「這個檔還沒建」,接著就會有人拿它去建一個
    沒有名字的檔。`TypeError` 讓它在開發時就當場現形。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const dir = makeDirectory({}, log);\n"
        "  try {\n"
        f"    await fs.{operation}(dir, '/', '文字');\n"
        "    return null;\n"
        "  } catch (error) { return { name: error.name, log }; }",
    )
    assert result["name"] == "TypeError"
    # 平台一次也沒有被碰到 —— 錯在抵達控制代碼之前就被攔下。
    assert result["log"] == []


# =========================================================================
# 寫入(`writeTextAt`):回傳時已落盤
# =========================================================================


def test_write_text_at_creates_the_target_file(module_page) -> None:
    """5.4 的「檔案不存在就建立」:目標檔要帶 `create`,中間資料夾不帶。"""
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = { '適情雅趣~卷一': {} };\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  await fs.writeTextAt(dir, {TARGET_PATH!r}, '[\\n]\\n');\n"
        "  return { tree, log };",
    ) == {
        "tree": {"適情雅趣~卷一": {"26.json": "[\n]\n"}},
        "log": [
            ["getDirectoryHandle", "適情雅趣~卷一", False],
            ["getFileHandle", "26.json", True],
            ["createWritable", "26.json"],
            ["write", "[\n]\n"],
            ["close", "26.json"],
        ],
    }


def test_write_text_at_replaces_the_whole_file(module_page) -> None:
    """整檔覆寫(design 的 Implementation Notes):內容是 `appendPosition` 的輸出,

    既有題目的不變性由那個輸出保證,不由寫入方式保證。串流因此不得保留原有內容。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = { '適情雅趣~卷一': { '26.json': '舊的一整份內容' } };\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  await fs.writeTextAt(dir, {TARGET_PATH!r}, '新的');\n"
        "  return tree;",
    ) == {"適情雅趣~卷一": {"26.json": "新的"}}


def test_write_text_at_resolves_only_after_the_stream_is_closed(module_page) -> None:
    """Postcondition:`writeTextAt` 回傳時內容**已落盤**。

    替身與平台一樣,只有 `close()` 才把內容放進檔案樹。所以「回傳之後讀得到新內容」
    就等於「回傳之前串流已經關掉」—— 少了 `close()` 這裡會讀到舊的。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  const tree = { '適情雅趣~卷一': { '26.json': '舊的' } };\n"
        "  const dir = makeDirectory(tree, log);\n"
        f"  const pending = fs.writeTextAt(dir, {TARGET_PATH!r}, '新的');\n"
        "  const beforeAwait = tree['適情雅趣~卷一']['26.json'];\n"
        "  await pending;\n"
        "  const afterAwait = tree['適情雅趣~卷一']['26.json'];\n"
        "  return { beforeAwait, afterAwait, closedLast: log[log.length - 1][0] };",
    ) == {"beforeAwait": "舊的", "afterAwait": "新的", "closedLast": "close"}


def test_write_text_at_does_not_create_missing_folders(module_page) -> None:
    """書目資料夾不存在就是寫入失敗,不是靜靜生一個出來。

    憑一個打錯的路徑在題庫裡長出一個沒人要的資料夾,比一則錯誤訊息難發現得多。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const tree = {};\n"
        "  const dir = makeDirectory(tree, log);\n"
        "  let failure = null;\n"
        f"  try {{ await fs.writeTextAt(dir, {TARGET_PATH!r}, '新的'); }}\n"
        "  catch (error) { failure = describe(error, fs); }\n"
        "  return { failure, tree };",
    )
    assert result["tree"] == {}
    assert result["failure"]["name"] == "NotFoundError"
    # 平台的失敗原樣往上:它既不是「不支援」也不是「使用者拒絕」,折進那兩種
    # 會讓呈現層說出與事實不符的話(design 的 Error Handling 把它歸在檔案失敗)。
    assert result["failure"]["denied"] is False
    assert result["failure"]["unsupported"] is False


def test_write_text_at_abandons_the_stream_when_writing_fails(module_page) -> None:
    """寫到一半失敗就**放棄**這個串流,不要關它 —— 關了會把半份內容落盤。"""
    result = run(
        module_page,
        "  const log = [];\n"
        "  const folder = { '26.json': '完整的舊內容', __failWrite: true };\n"
        "  const dir = makeDirectory({ '適情雅趣~卷一': folder }, log);\n"
        "  let failed = false;\n"
        f"  try {{ await fs.writeTextAt(dir, {TARGET_PATH!r}, '新的'); }}\n"
        "  catch (error) { failed = true; }\n"
        "  return { failed, kept: folder['26.json'], log: log.map((e) => e[0]) };",
    )
    assert result["failed"] is True
    assert result["kept"] == "完整的舊內容"
    assert "close" not in result["log"]
    assert "abort" in result["log"]


# =========================================================================
# 邊界:這裡是收題頁唯一接觸平台檔案 API 的地方
# =========================================================================


def test_fs_is_the_only_module_that_touches_the_platform_file_api() -> None:
    """design 的 Boundary Commitments:`fs.js` 是 File System Access API 的唯一接觸點。

    以原始碼斷言而非執行期行為 —— 這條界線的價值在於「其餘模組在沒有這個 API 的
    環境下仍測得動」,而那是一件關於**程式碼裡有沒有那行**的事實。

    比對的是**呼叫**而不是型別名:`editor.js` 在 JSDoc 裡寫
    `FileSystemDirectoryHandle` 只是在描述它拿到的東西,那不是接觸。註解因此先被
    去掉。
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


def test_fs_does_not_persist_the_directory_handle() -> None:
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
    """控制代碼的存續範圍就是模組的存續範圍:重新載入模組即回到未授權狀態。

    這正是 6.1「本次分頁首次要求寫入時請求授權」與平台授權週期吻合的方式,也是
    「重整頁面後要重選一次目錄」這個已被 6.1 接受的代價的來源。
    """
    assert run(
        module_page,
        "  const log = [];\n"
        "  installPicker(log, async () => makeDirectory({}, log));\n"
        "  await fs.acquireCorpusDirectory();\n"
        "  const again = await import('/editor/fs.js?reloaded');\n"
        "  await again.acquireCorpusDirectory();\n"
        "  return log.length;",
    ) == 2


def test_fs_leaves_path_rules_to_check_js_and_the_platform(module_page) -> None:
    """本模組**不做路徑驗證**:`checkTargetPath` 是 `check.js` 的事(5.2、5.3)。

    平台才是真正的圍籬 —— 控制代碼只能在使用者選定的目錄樹內解析路徑,`..` 這種
    名稱平台自己就會拒絕(design 的 Security Considerations)。在這裡再寫一份規則
    只會多一個真相來源,而且擋不住任何前一道沒擋住的東西。
    """
    result = run(
        module_page,
        "  const log = [];\n"
        "  const dir = makeDirectory({}, log);\n"
        "  let failure = null;\n"
        "  try { await fs.readTextAt(dir, '../外面/26.json'); }\n"
        "  catch (error) { failure = { name: error.name, message: error.message }; }\n"
        "  return { failure, log };",
    )
    # 那一段原封不動被交給平台,由平台拒絕;訊息也是平台的說法,不是本模組的。
    assert result["log"] == [["getDirectoryHandle", "..", False]]
    assert result["failure"]["name"] == "TypeError"
    assert "替身" in result["failure"]["message"]
