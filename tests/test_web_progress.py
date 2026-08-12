"""完成狀態的持久化與損毀復原(tasks 2.2;requirements 3.1、3.2、3.3、3.4、3.6、3.7)。

`web/progress.js` 是完成狀態的唯一寫入處:一份題號集合,存在瀏覽器本機。它不碰
DOM、不 import 任何同伴,因此與 `catalog.js` / `fen.js` 一樣可以用 `page.evaluate()`
單獨驗證,不必走完整的頁面流程。

## 為什麼要架一個 http 來源

兩個理由。其一與 `test_web_catalog.py` 相同:Chromium 不允許自 `file://`
(origin 為 `null`)匯入 ES module。其二是本檔獨有的 —— **`file://` 與 `about:blank`
下沒有可用的 `localStorage`**,而本模組要驗證的正是它。`page.route()` 就地供
`web/` 底下的真實交付檔,瀏覽器便給這個來源一份真的本機儲存區。

## 這裡最要緊的一條

**任何一種儲存異常都不得讓呼叫端炸掉。** 「解析失敗」遠不只 `JSON.parse` 拋錯:
存取 `localStorage` 這個動作本身在隱私模式或跨站儲存被擋時就會拋、型別可能整個
不對、陣列裡可能混著不是題號的東西、寫入可能撞到配額上限。判準只有一條 ——
列表要打得開。因此 `sabotage()` 把這四種情形都做得出來,而不只是塞一個壞字串。
"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
PROGRESS_JS = WEB_DIR / "progress.js"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下。
#: 與 `test_web_catalog.py` 刻意不同名:同名會共用 origin,也就共用 localStorage。
ORIGIN = "https://progress-browser.test"

#: 完成狀態的儲存鍵(design 的「完成狀態:一份題號集合」)。
#: 中段的 `v1` 是版本前綴 —— 日後格式改變時舊資料可辨識而非誤讀。
STORAGE_KEY = "leetchess:v1:completed"


# --- 夾具與呼叫工具 -----------------------------------------------------


@pytest.fixture
def progress_page(browser_page) -> Iterator:
    """一個位於 http 來源、可 `import` `web/progress.js` 且有本機儲存區的空白分頁。

    每個測試都拿到自己的 browser context(見 `conftest_web.browser_page`),因此
    儲存區也是乾淨的 —— 一個測試寫進去的完成狀態不會外溢到下一個。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用任何既有頁面:那些會去載 app.js,而本檔要驗證的是
                # progress.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>完成狀態驗證</title>",
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


#: 把儲存區弄壞的四種方式,對應四種真實情境:
#:
#: - `blocked` —— 隱私模式或跨站儲存被擋:**取用這個屬性本身就拋**
#: - `missing` —— 這個 API 根本不存在
#: - `read_throws` —— 讀得到物件但 `getItem` 拋錯
#: - `write_throws` —— 讀寫都在,但寫入撞到配額上限(讀仍正常,故可驗證讀寫分離)
#:
#: 破壞在 `import` **之前**施加:現實中儲存區是在任何程式執行前就已經被擋掉的,
#: 若模組在載入時就去碰儲存區,這裡必須看得出來。
_SABOTAGE = """
const sabotage = (mode) => {
  if (mode === null || mode === undefined) return;
  const define = (descriptor) =>
    Object.defineProperty(window, 'localStorage', { configurable: true, ...descriptor });
  if (mode === 'blocked') {
    define({ get() { throw new DOMException('storage is blocked', 'SecurityError'); } });
  } else if (mode === 'missing') {
    delete window.localStorage;
  } else if (mode === 'read_throws') {
    define({
      value: {
        getItem() { throw new Error('read failed'); },
        setItem() {},
        removeItem() {},
      },
    });
  } else if (mode === 'write_throws') {
    const kept = new Map();
    define({
      value: {
        getItem: (key) => (kept.has(key) ? kept.get(key) : null),
        setItem() { throw new DOMException('quota', 'QuotaExceededError'); },
        removeItem: (key) => { kept.delete(key); },
      },
    });
  } else {
    throw new Error(`unknown sabotage: ${mode}`);
  }
};
"""

#: 讀出完成狀態,再依序切換每一個題號,把每一步的集合內容帶回 Python。
#:
#: 任何拋出都被收成 `{ok: false}` 而非讓 `page.evaluate()` 爆炸 —— 斷言
#: `ok is True` 的失敗訊息比 Playwright 的堆疊好讀得多,而「不得拋出」正是
#: 3.6 的核心。
_RUN = (
    """
async ({ ids, mode }) => {
"""
    + _SABOTAGE
    + """
  const snapshot = (set) => [...set].sort((a, b) => a - b);
  try {
    sabotage(mode);
    const progress = await import('/progress.js');
    const initial = progress.loadCompleted();
    let completed = initial;
    const steps = [];
    for (const id of ids) {
      completed = progress.toggleCompleted(completed, id);
      steps.push({ ids: snapshot(completed), is_set: completed instanceof Set });
    }
    return {
      ok: true,
      key: progress.STORAGE_KEY,
      initial: snapshot(initial),
      is_set: initial instanceof Set,
      steps,
      ids: snapshot(completed),
    };
  } catch (error) {
    return { ok: false, error: String(error), name: error?.name ?? null };
  }
}
"""
)


def run(page, *ids: Any, mode: str | None = None) -> dict:
    """載入完成狀態並依序切換 `ids`,回傳攤平後的結果。"""
    return page.evaluate(_RUN, {"ids": list(ids), "mode": mode})


def ok(page, *ids: Any, mode: str | None = None) -> dict:
    """同 `run`,但斷言全程沒有拋出。"""
    result = run(page, *ids, mode=mode)
    assert result["ok"] is True, f"完成狀態的讀寫不得拋出:{result}"
    return result


def completed_ids(page, mode: str | None = None) -> list[int]:
    """目前記著的完成題號(遞增)。"""
    result = ok(page, mode=mode)
    assert result["is_set"] is True, f"完成狀態應是一個 Set:{result}"
    return result["initial"]


def toggled_ids(page, *ids: Any, mode: str | None = None) -> list[int]:
    """依序切換 `ids` 之後的完成題號。"""
    return ok(page, *ids, mode=mode)["ids"]


def raw(page, key: str = STORAGE_KEY) -> str | None:
    """儲存區裡的原始字串,`None` 代表這個鍵不存在。"""
    return page.evaluate("(key) => localStorage.getItem(key)", key)


def put_raw(page, value: str, key: str = STORAGE_KEY) -> None:
    """直接把一個原始字串塞進儲存區 —— 模擬使用者手改、舊版本殘留或別人塞的東西。"""
    page.evaluate("([key, value]) => localStorage.setItem(key, value)", [key, value])


def storage_keys(page) -> list[str]:
    """儲存區裡目前所有的鍵。"""
    return page.evaluate("() => Object.keys(localStorage).sort()")


# --- 3.1、3.2、3.3:切換、立即反映、重開仍在 ----------------------------


def test_toggling_a_position_marks_it_completed_immediately(progress_page) -> None:
    """3.2:切換後立即反映 —— 回傳的集合當場就帶著這個題號,不必等任何非同步。"""
    result = ok(progress_page, 3)

    assert result["steps"][0] == {"ids": [3], "is_set": True}


def test_a_completed_position_is_still_completed_after_reopening(progress_page) -> None:
    """3.3:重新開啟服務時先前的標記仍在 —— 這是完成標記存在的理由。

    `page.reload()` 給的是一個全新的 JS realm(模組重新載入、記憶體中的東西
    全沒了),因此能通過這條的只有真的寫進本機儲存區的實作。
    """
    toggled_ids(progress_page, 3, 7)

    progress_page.reload()

    assert completed_ids(progress_page) == [3, 7]


def test_clearing_the_only_mark_leaves_nothing_completed_after_reopening(
    progress_page,
) -> None:
    """3.3:**集合歸零**同樣要落地 —— 取消最後一個標記之後重開,它不得復活。

    上一條刻意留著第 7 題,集合因此永遠不會空;而「空集合就不寫」
    (`if (completed.size === 0) return;`)是寫入端最順手的最佳化,施加後上一條
    照樣是綠的 —— 使用者取消了唯一的標記,重開卻看到它回來,而且再也清不掉。
    這條專門守住那個路徑:走完整條「標上 → 重開 → 取消 → 重開」。
    """
    assert toggled_ids(progress_page, 3) == [3]
    progress_page.reload()
    assert completed_ids(progress_page) == [3]

    assert toggled_ids(progress_page, 3) == []
    progress_page.reload()
    assert completed_ids(progress_page) == []


# --- 版本前綴:舊資料可辨識而非誤讀 -------------------------------------


def test_a_value_left_under_another_key_is_not_read_as_progress(progress_page) -> None:
    """版本前綴要有用,就必須真的不去讀別的鍵。

    這裡擺的兩個鍵是版本前綴要防的兩種東西:沒有版本概念的舊格式,以及日後某個
    新版本的資料。兩者都不得被當成本版本的完成狀態。
    """
    put_raw(progress_page, "[1, 2, 3]", key="completed")
    put_raw(progress_page, '{"completed": [4, 5]}', key="leetchess:v2:completed")

    assert completed_ids(progress_page) == []


# --- 3.4:讀取不寫入 ----------------------------------------------------


def test_reading_leaves_a_damaged_value_exactly_as_it_was(progress_page) -> None:
    """3.4:讀到壞掉的值也不「順手修好」它。

    自動改寫看似體貼,實際上是在使用者沒有操作的情況下改動他的資料:若損毀其實
    來自一個還沒發布的新格式,一次讀取就會把它輾掉。**只有切換操作能改變儲存區。**
    """
    damaged = '[1, "oops", 6]'
    put_raw(progress_page, damaged)

    assert completed_ids(progress_page) == [1, 6]
    assert raw(progress_page) == damaged


# --- 3.6:解析失敗一律退回「全部未完成」 --------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("這不是 JSON", id="not-json"),
        pytest.param("null", id="null"),
    ],
)
def test_an_unreadable_stored_value_falls_back_to_nothing_completed(
    progress_page, stored: str
) -> None:
    """3.6:認不得的儲存值退回「全部未完成」而非拋錯。

    使用者手動改壞、舊版本殘留、瀏覽器塞了別的東西 —— 任何一種都不該讓列表
    打不開。這些案例分成兩類:根本不是 JSON 的(前三個),以及解析得出來但
    型別不對的(其餘)。**只擋 `JSON.parse` 的實作會在後面那幾個變紅。**
    """
    put_raw(progress_page, stored)

    assert completed_ids(progress_page) == []


def test_entries_that_are_not_position_numbers_are_dropped(progress_page) -> None:
    """3.6:陣列裡混著不是題號的東西時,丟掉那些東西而不是丟掉整份進度。

    整份丟掉也能滿足「不讓列表打不開」,但那會因為一筆髒資料抹掉使用者其餘的
    標記。判準是題號的形狀:正整數。字串型的 `"2"` 也一律不收 —— 悄悄轉型會讓
    儲存區裡同時存在 `2` 與 `"2"` 兩種寫法,而集合對它們的判定並不相同。
    """
    put_raw(
        progress_page,
        '[1, "2", null, true, -3, 2.5, 0, {"id": 4}, [5], 1e999, 6]',
    )

    assert completed_ids(progress_page) == [1, 6]


def test_a_toggle_of_something_that_is_not_a_position_number_stores_nothing(
    progress_page,
) -> None:
    """寫入端同樣認題號的形狀 —— 儲存區裡不會因為呼叫端傳了怪東西而多出垃圾。

    這是上一條的另一半:讀的時候擋得住,寫的時候也不放進去,壞資料才不會在
    下一次讀取時變成使用者的損失。
    """
    for value in ("x", 2.5, -1, 0, None, True):
        assert toggled_ids(progress_page, value) == [], f"{value!r} 不該被記成完成"

    assert storage_keys(progress_page) == []


def test_a_toggle_leaves_the_set_it_was_given_untouched(progress_page) -> None:
    """切換回傳新的集合,不就地改動傳進去的那一份。

    就地改動會讓呼叫端手上的「切換前」與「切換後」變成同一個物件 —— 任何想比對
    前後差異的呈現(例如只重畫變動的那一列)都會看到兩邊一樣。
    """
    put_raw(progress_page, "[1, 2]")

    result = progress_page.evaluate(
        """async () => {
          const progress = await import('/progress.js');
          const before = progress.loadCompleted();
          const after = progress.toggleCompleted(before, 3);
          return {
            before: [...before].sort((a, b) => a - b),
            after: [...after].sort((a, b) => a - b),
            same_object: before === after,
          };
        }"""
    )

    assert result == {"before": [1, 2], "after": [1, 2, 3], "same_object": False}


# --- 儲存區本身不可用:讀寫兩邊都要承受 ---------------------------------


@pytest.mark.parametrize("mode", ["missing"])
def test_a_storage_that_cannot_be_read_looks_like_nothing_completed(
    progress_page, mode: str
) -> None:
    """3.6:「解析失敗」也包含根本讀不到。

    隱私模式、被瀏覽器政策封鎖、跨站儲存被擋 —— `blocked` 連取用
    `localStorage` 這個屬性都會拋,`missing` 是這個 API 根本不存在。三種都必須
    退回「全部未完成」,而不是讓列表打不開。
    """
    assert completed_ids(progress_page, mode=mode) == []


@pytest.mark.parametrize("mode", ["blocked", "read_throws"])
def test_a_toggle_still_takes_effect_when_storage_is_unusable(
    progress_page, mode: str
) -> None:
    """3.2:寫不進去時,切換在**這一次瀏覽階段內**仍然照常運作。

    刻意的取捨:寫入失敗不往呼叫端拋,也不把切換退回去。理由是使用者對此無能
    為力(隱私模式、配額爆掉都不是他能處理的),而讓核取方塊按下去沒有反應,
    只會讓人以為是壞掉了。代價是關掉分頁後標記不會留著 —— 但在儲存區不可用的
    情況下,那本來就留不住。

    session 內必須是連貫的:標上去再按一次要回得來。每次都重新從(讀不到的)
    儲存區推導狀態的實作會在第二步變紅 —— 那會讓使用者再也取消不掉任何標記。
    """
    result = ok(progress_page, 3, 8, 3, mode=mode)

    assert [step["ids"] for step in result["steps"]] == [[3], [3, 8], [8]]


def test_a_write_that_fails_does_not_disturb_what_is_already_stored(
    progress_page,
) -> None:
    """配額爆掉時,先前存好的內容不會被清掉或截半。"""
    toggled_ids(progress_page, 1, 2)

    assert toggled_ids(progress_page, 9, mode="write_throws") == [9]

    progress_page.reload()
    assert completed_ids(progress_page) == [1, 2]


# --- 3.7:不再列出的題號 ------------------------------------------------


def test_the_progress_module_never_asks_what_is_listed(progress_page) -> None:
    """3.7 的結構性理由:它根本沒有「目前列出哪些題」這個輸入。

    `loadCompleted()` 不收參數,`toggleCompleted()` 只收「目前的集合」與「一個
    題號」。沒有列表可比對,也就沒有把集合修剪成列表的機會。
    """
    arity = progress_page.evaluate(
        """async () => {
          const progress = await import('/progress.js');
          return {
            load: progress.loadCompleted.length,
            toggle: progress.toggleCompleted.length,
          };
        }"""
    )

    assert arity == {"load": 0, "toggle": 2}


# --- 邊界:純資料、位於依賴鏈最左端 -------------------------------------


def test_the_progress_module_touches_no_dom() -> None:
    """「純資料,不碰 DOM」是可讀出來的:原始碼裡不出現 `document` 或 `window`。

    這條由原始碼把關而非行為把關,因為瀏覽器裡永遠有 DOM 可碰 —— 沒有任何
    執行結果能證明「它沒去碰」。`localStorage` 不在禁用之列:那是本模組的職責
    所在,但只能經由 `globalThis` 取用,不得經由 `window`(那是 DOM 的入口)。
    """
    source = PROGRESS_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)

    assert not re.search(r"\b(document|window|fetch|XMLHttpRequest)\b", code)
