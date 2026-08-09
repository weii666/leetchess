"""題目索引的取得與篩選(tasks 2.1;requirements 1.2、1.3、1.4、2.1、2.2、2.3、2.4、5.1、5.2)。

`web/catalog.js` 是列表所需資料的唯一來源:向 `GET /api/catalog` 取一次索引,
之後的篩選全在記憶體中進行。它不碰 DOM,因此與 `fen.js` / `notation.js` 一樣
可以用 `page.evaluate()` 單獨驗證,不必走完整的頁面流程。

## 為什麼要架一個 http 來源

Chromium 不允許自 `file://`(origin 為 `null`)匯入 ES module,一律以 CORS 擋下。
前端以 ES modules 交付且無建置步驟(`tech.md`),所以測試必須讓模組在一個真的
http(s) 來源下被載入 —— 此處以 `page.route()` 就地供 `web/` 底下的**真實交付檔**,
不啟動任何伺服器進程。同一個攔截機制順帶也是模擬後端的手段。

## 這裡最要緊的一條

**`solvable` 為空值時必須仍然列出。** corpus-verification 尚未回填,若把空值當成
不可解,整個題庫在驗證工具跑完之前都會是空的 —— 產品會在最需要能用的時候完全
打不開。只有明確的 `false` 才排除。`CATALOG` 因此刻意同時擺了 `null`、欄位不存在
與 `false` 三種情形。
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
CATALOG_JS = WEB_DIR / "catalog.js"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下。
ORIGIN = "https://problem-browser.test"

#: 索引端點的路徑(`service/main.py` 的 `read_catalog`)。
CATALOG_PATH = "/api/catalog"

#: `GET /api/catalog` 的回應內容(engine-service 的實測形狀,依題號遞增)。
#:
#: 六題涵蓋了篩選與過濾要分辨的每一種情形:
#:
#: - 難度 3 有 1、2、5;難度 5 有 3、6;難度 1 只有 4
#: - 標籤「連將殺」橫跨兩個出處,「雙馬」橫跨兩種難度
#: - `solvable`:2 為 `null`、4 完全沒有這個欄位、5 明確為 `false`
CATALOG: list[dict[str, Any]] = [
    {
        "id": 1,
        "title": "盡善克終",
        "description": "適情雅趣 第二一局 盡善克終",
        "difficulty": 3,
        "tags": ["雙馬", "連將殺", "鬥快"],
        "source": "適情雅趣",
        "solvable": True,
    },
    {
        "id": 2,
        "title": "車馬冷著",
        "description": "適情雅趣 第二二局 車馬冷著",
        "difficulty": 3,
        "tags": ["連將殺"],
        "source": "適情雅趣",
        # corpus-verification 尚未驗到這一題 —— 空值,仍須列出。
        "solvable": None,
    },
    {
        "id": 3,
        "title": "棄子入局",
        "description": "橘中秘 第三局 棄子入局",
        "difficulty": 5,
        "tags": ["連將殺", "鬥快"],
        "source": "橘中秘",
        "solvable": True,
    },
    {
        "id": 4,
        "title": "順手牽羊",
        "description": "橘中秘 第四局 順手牽羊",
        "difficulty": 1,
        "tags": ["雙馬"],
        "source": "橘中秘",
        # 連欄位都沒有 —— 一樣是「尚未回填」,仍須列出。
    },
    {
        "id": 5,
        "title": "殘局存疑",
        "description": "適情雅趣 第五局 殘局存疑",
        "difficulty": 3,
        "tags": ["連將殺"],
        "source": "適情雅趣",
        # 唯一明確不可解的一題 —— 任何情況下都不得出現。
        "solvable": False,
    },
    {
        "id": 6,
        "title": "雙包齊鳴",
        "description": "適情雅趣 第六局 雙包齊鳴",
        "difficulty": 5,
        "tags": ["雙包"],
        "source": "適情雅趣",
        "solvable": True,
    },
]

#: 扣掉明確不可解的第 5 題之後應該上架的題號,順序即索引的順序。
LISTABLE_IDS = [1, 2, 3, 4, 6]


# --- 夾具與呼叫工具 -----------------------------------------------------


@pytest.fixture
def catalog_page(browser_page) -> Iterator:
    """一個位於 http 來源、可 `import` `web/catalog.js` 的空白分頁。

    只供 `web/` 底下的真實檔案;`/api/**` 刻意不在此處處理,交由各測試自行註冊
    (Playwright 後註冊者優先),沒註冊時會落到最後的 404 —— 那本身也是一種
    被測情況。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用任何既有頁面:那些會去載 app.js,而本檔要驗證的是
                # catalog.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>題目索引驗證</title>",
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


def serve_api(
    page,
    *,
    status: int = 200,
    body: Any = None,
    content_type: str = "application/json",
) -> list[dict]:
    """攔截**所有** `/api/` 開頭的請求並回同一份內容,把收到的請求記在回傳的 list。

    攔截整個 `/api/**` 而非只攔索引端點是刻意的:5.2 要求索引層不得碰引擎,
    而「有沒有碰」只有把每一個 API 請求都記下來才看得出來。
    """
    seen: list[dict] = []

    def handler(route) -> None:
        request = route.request
        seen.append({"path": urlsplit(request.url).path, "method": request.method})
        route.fulfill(
            status=status,
            content_type=content_type,
            body=body if isinstance(body, str) else json.dumps(body),
        )

    page.route(f"{ORIGIN}/api/**", handler)
    return seen


def serve_catalog(page, positions: list[dict[str, Any]] | None = None) -> list[dict]:
    """讓索引端點回一份題庫索引,回傳收到的請求記錄。"""
    return serve_api(
        page, body={"positions": CATALOG if positions is None else positions}
    )


def hang(page) -> None:
    """後端收下請求就再也不回話 —— 逾時上界唯一的驗證方式。"""
    page.route(f"{ORIGIN}/api/**", lambda route: None)


#: 取一次索引後,依序對每組條件篩選一遍,把每組的題號帶回 Python。
#:
#: 全部篩選共用**同一個**載入結果 —— 這正是 5.1 要的用法,也讓請求次數的斷言
#: 有意義:不論篩幾次,`seen` 都必須只有一筆。
_FILTER_IDS = """
async (criteriaList) => {
  const catalog = await import('/catalog.js');
  const loaded = await catalog.loadCatalog();
  return criteriaList.map((criteria) => loaded.filter(criteria).map((p) => p.id));
}
"""


def ids_for(page, *criteria: Any) -> list[list[int]]:
    """取得每一組篩選條件的結果題號。"""
    return page.evaluate(_FILTER_IDS, list(criteria))


def one_result(page, criteria: Any) -> list[int]:
    """單一組條件的結果題號。"""
    return ids_for(page, criteria)[0]


#: 載入索引,把成敗攤平成可帶回 Python 的資料。失敗時把失敗物件的所有自有屬性
#: 連同 `toString()` 一起攤平 —— 後端字樣若藏在任何一個欄位裡都會出現在 dump 中。
_LOAD = """
async ({ timeoutMs }) => {
  const catalog = await import('/catalog.js');
  const options = timeoutMs == null ? undefined : { timeoutMs };
  const started = performance.now();
  try {
    const loaded = await catalog.loadCatalog(options);
    return {
      ok: true,
      elapsed_ms: performance.now() - started,
      positions: loaded.positions,
    };
  } catch (error) {
    const own = {};
    for (const key of Object.getOwnPropertyNames(error)) own[key] = String(error[key]);
    return {
      ok: false,
      elapsed_ms: performance.now() - started,
      code: error.code ?? null,
      name: error.name ?? null,
      is_catalog_error: error instanceof catalog.CatalogError,
      dump: [String(error), JSON.stringify(own)].join(' | '),
    };
  }
}
"""


def load(page, timeout_ms: int | None = None) -> dict:
    """載入索引,回傳 `{ok: True, positions}` 或攤平後的失敗描述。"""
    return page.evaluate(_LOAD, {"timeoutMs": timeout_ms})


def loaded(page, timeout_ms: int | None = None) -> list[dict]:
    """載入索引並斷言成功,回傳上架的題目。"""
    result = load(page, timeout_ms)
    assert result["ok"] is True, f"預期載入成功卻失敗了:{result}"
    return result["positions"]


def failure(page, timeout_ms: int | None = None) -> dict:
    """載入索引並斷言失敗,回傳攤平後的失敗描述。"""
    result = load(page, timeout_ms)
    assert result["ok"] is False, f"預期載入失敗卻成功了:{result}"
    assert result["is_catalog_error"] is True, f"失敗必須是 CatalogError:{result}"
    return result


# --- 取得索引:一次,而且只碰索引端點 -----------------------------------


def test_loading_the_catalog_asks_the_index_endpoint_once(catalog_page) -> None:
    """5.1:索引來自單一份資料 —— 一次 `GET /api/catalog`。"""
    seen = serve_catalog(catalog_page)

    loaded(catalog_page)

    assert [(r["method"], r["path"]) for r in seen] == [("GET", CATALOG_PATH)]


def test_filtering_never_fetches_the_index_again(catalog_page) -> None:
    """5.1:篩選全在記憶體中進行 —— 改幾次條件都不再發請求。

    這是本模組的核心約束,而唯一能證明它的方式就是數請求次數:五組不同的條件
    篩完之後,索引端點仍然只被問過一次。
    """
    seen = serve_catalog(catalog_page)

    results = ids_for(
        catalog_page,
        {},
        {"difficulty": 3},
        {"tag": "連將殺"},
        {"source": "橘中秘"},
        {"difficulty": 5, "source": "適情雅趣"},
    )

    assert len(results) == 5
    assert len(seen) == 1, f"篩選不得再次取得索引,實際請求:{seen}"


def test_the_catalog_touches_no_endpoint_other_than_the_index(catalog_page) -> None:
    """5.2:不佔用任何引擎資源 —— 除了索引端點,一個請求都不發。

    引擎池滿的時候列表仍須能開啟與操作,而做到這件事的方式就是根本不去碰
    需要引擎的端點(`/api/positions/{id}` 與 `/api/black-move` 都要借引擎)。
    """
    seen = serve_catalog(catalog_page)

    ids_for(catalog_page, {"difficulty": 3}, {"tag": "雙馬"})

    assert {r["path"] for r in seen} == {CATALOG_PATH}


def test_the_catalog_keeps_every_field_a_row_needs(catalog_page) -> None:
    """1.2:每一題都帶著題號、局名、描述、難度、標籤與出處。

    索引是列表唯一的資料來源,少任何一個欄位那一列就畫不完整。
    """
    serve_catalog(catalog_page)

    first = loaded(catalog_page)[0]

    assert first == CATALOG[0]


def test_the_catalog_preserves_the_order_of_the_index(catalog_page) -> None:
    """1.3:順序即索引的順序(依題號遞增),不因取得而重排。

    題號與局名是每一列的主要識別,順序漂移會讓畫面在重啟後莫名重排,快速掃視
    也就無從談起。
    """
    serve_catalog(catalog_page)

    assert [p["id"] for p in loaded(catalog_page)] == LISTABLE_IDS


# --- 1.4:可解標記的過濾 ------------------------------------------------


def test_a_position_marked_unsolvable_is_left_out(catalog_page) -> None:
    """1.4:明確標為不可解的題目不列入。"""
    serve_catalog(catalog_page)

    assert 5 not in [p["id"] for p in loaded(catalog_page)]


def test_a_position_whose_solvable_flag_is_unfilled_is_still_listed(
    catalog_page,
) -> None:
    """1.4:空值視為可上架 —— `null` 與欄位不存在都算空值。

    這是最容易寫反的一條。corpus-verification 尚未回填,若把空值當成不可解,
    整個題庫在它跑完之前都會是空的。
    """
    serve_catalog(catalog_page)

    listed = [p["id"] for p in loaded(catalog_page)]

    assert 2 in listed, "solvable 為 null 的題目必須列出"
    assert 4 in listed, "沒有 solvable 欄位的題目必須列出"


def test_an_index_where_nothing_is_verified_yet_still_lists_everything(
    catalog_page,
) -> None:
    """1.4:整份索引都還沒回填時,題庫**不得**變成空的。

    這是上一條的極端情形,也是它真正要防的災難 —— corpus-verification 之前的
    現實就長這樣。
    """
    unverified = [{**entry, "solvable": None} for entry in CATALOG]
    serve_catalog(catalog_page, unverified)

    assert [p["id"] for p in loaded(catalog_page)] == [e["id"] for e in CATALOG]


def test_only_an_explicit_false_excludes_a_position(catalog_page) -> None:
    """1.4:排除的判準是「明確為 `false`」,不是「不為真」。

    直接對判斷本身取值,把四種可解標記一次擺出來 —— 用 falsy 判斷的實作會
    在 `null` 與欄位不存在這兩欄變紅。
    """
    verdicts = catalog_page.evaluate(
        """async () => {
          const catalog = await import('/catalog.js');
          return {
            yes: catalog.isListable({ id: 1, solvable: true }),
            no: catalog.isListable({ id: 2, solvable: false }),
            empty: catalog.isListable({ id: 3, solvable: null }),
            absent: catalog.isListable({ id: 4 }),
          };
        }"""
    )

    assert verdicts == {"yes": True, "no": False, "empty": True, "absent": True}


def test_an_unsolvable_position_stays_out_of_every_filter_result(catalog_page) -> None:
    """1.4:過濾先於篩選 —— 不可解的題目不會因為符合條件而回來。

    第 5 題的難度、標籤與出處都與第 1、2 題重疊,任何一組條件命中它們就會
    一併命中第 5 題;過濾若做在錯誤的層次,這裡就會看到 5。
    """
    serve_catalog(catalog_page)

    results = ids_for(
        catalog_page,
        {"difficulty": 3},
        {"tag": "連將殺"},
        {"source": "適情雅趣"},
        {"difficulty": 3, "tag": "連將殺", "source": "適情雅趣"},
    )

    assert results == [[1, 2], [1, 2, 3], [1, 2, 6], [1, 2]]


# --- 2.1、2.2、2.3:單一條件 -------------------------------------------


def test_filtering_by_difficulty_lists_only_that_difficulty(catalog_page) -> None:
    """2.1:選一個難度,只列出該難度的題目。"""
    serve_catalog(catalog_page)

    assert one_result(catalog_page, {"difficulty": 5}) == [3, 6]
    assert one_result(catalog_page, {"difficulty": 1}) == [4]


def test_filtering_by_tag_lists_only_positions_carrying_it(catalog_page) -> None:
    """2.2:選一個標籤,只列出帶有該標籤的題目 —— 標籤是多值欄位,比對的是「含有」。"""
    serve_catalog(catalog_page)

    assert one_result(catalog_page, {"tag": "雙馬"}) == [1, 4]
    assert one_result(catalog_page, {"tag": "鬥快"}) == [1, 3]


def test_filtering_by_source_lists_only_that_source(catalog_page) -> None:
    """2.3:選一個出處,只列出該出處的題目。"""
    serve_catalog(catalog_page)

    assert one_result(catalog_page, {"source": "橘中秘"}) == [3, 4]
    assert one_result(catalog_page, {"source": "適情雅趣"}) == [1, 2, 6]


def test_no_criteria_lists_the_whole_catalog(catalog_page) -> None:
    """沒有條件時列出全部(扣掉不可解的),空字串等同於未選 —— 下拉選單的「全部」。"""
    serve_catalog(catalog_page)

    assert ids_for(
        catalog_page,
        {},
        None,
        {"difficulty": None, "tag": None, "source": None},
        {"difficulty": "", "tag": "", "source": ""},
    ) == [LISTABLE_IDS] * 4


def test_a_difficulty_given_as_text_filters_the_same_as_a_number(catalog_page) -> None:
    """下拉選單的值是字串 —— `'3'` 必須與 `3` 篩出同一批,否則整份列表會憑空消失。"""
    serve_catalog(catalog_page)

    assert one_result(catalog_page, {"difficulty": "3"}) == one_result(
        catalog_page, {"difficulty": 3}
    )


# --- 2.4:多條件為 AND --------------------------------------------------


def test_multiple_criteria_keep_only_positions_matching_all_of_them(
    catalog_page,
) -> None:
    """2.4:同時選多個條件時取交集,不是聯集。

    難度 5 有 3、6,出處「適情雅趣」有 1、2、6 —— 兩者合起來只剩 6。聯集的
    實作會回四題,少一個條件的實作會回兩到三題,三種結果彼此可辨。
    """
    serve_catalog(catalog_page)

    assert one_result(catalog_page, {"difficulty": 5, "source": "適情雅趣"}) == [6]


def test_every_dimension_at_once_takes_the_intersection(catalog_page) -> None:
    """2.4:難度、標籤、出處三者並用。"""
    serve_catalog(catalog_page)

    assert one_result(
        catalog_page, {"difficulty": 1, "tag": "雙馬", "source": "橘中秘"}
    ) == [4]


def test_a_combination_nothing_matches_comes_back_empty(catalog_page) -> None:
    """2.4:條件互斥時回空陣列,而不是退回其中一個條件的結果或整份題庫。

    難度 1 只有第 4 題(出處「橘中秘」),與「適情雅趣」不可能同時成立。
    """
    serve_catalog(catalog_page)

    assert (
        ids_for(
            catalog_page,
            {"difficulty": 1, "source": "適情雅趣"},
            {"tag": "雙包", "difficulty": 3},
            {"tag": "不存在的標籤"},
            {"source": "不存在的出處"},
        )
        == [[]] * 4
    )


def test_filtering_leaves_the_loaded_index_untouched(catalog_page) -> None:
    """篩選是取值,不是消耗 —— 篩過之後索引本身仍是完整的,可以再篩別的條件。

    就地過濾掉不符條件的題目會讓第二次篩選只能在殘骸上進行,列表也就再也回不到
    完整狀態(2.5 的「清除條件」正是靠這個)。
    """
    serve_catalog(catalog_page)

    result = catalog_page.evaluate(
        """async () => {
          const catalog = await import('/catalog.js');
          const loaded = await catalog.loadCatalog();
          loaded.filter({ difficulty: 1 });
          loaded.filter({ tag: '雙包' });
          return {
            after: loaded.positions.map((p) => p.id),
            cleared: loaded.filter({}).map((p) => p.id),
          };
        }"""
    )

    assert result == {"after": LISTABLE_IDS, "cleared": LISTABLE_IDS}


# --- 取不到索引的情形 ---------------------------------------------------


def test_a_failing_index_endpoint_becomes_a_catalog_error(catalog_page) -> None:
    """索引取不到時失敗要可辨識,列表才有辦法告知使用者並提供重試。"""
    serve_api(catalog_page, status=500, body={"code": "INTERNAL", "message": "boom"})

    assert failure(catalog_page)["code"] is not None


def test_a_failure_carries_no_text_from_the_backend(catalog_page) -> None:
    """後端的原始內容一個字都不外流 —— 使用者看到的文字由呈現層自己決定。"""
    secret = "internal-detail-should-not-leak"
    serve_api(catalog_page, status=500, body={"code": "INTERNAL", "message": secret})

    assert secret not in failure(catalog_page)["dump"]


def test_an_unrecognisable_index_shape_is_a_failure(catalog_page) -> None:
    """回應形狀認不得就是失敗 —— 不能拿半份資料當成「題庫是空的」畫出去。

    「題庫真的沒有題目」與「索引壞掉」對使用者是兩件事(1.5 的空狀態 vs. 錯誤
    提示),認不得的形狀若靜默變成空陣列,後者會偽裝成前者。
    """
    for body in ({"items": []}, [], "<html>維護中</html>", ""):
        page = catalog_page
        page.unroute(f"{ORIGIN}/api/**")
        serve_api(page, body=body, content_type="text/plain")

        assert failure(page)["code"] is not None, f"這份回應應被視為失敗:{body!r}"


def test_an_empty_catalog_loads_as_an_empty_list(catalog_page) -> None:
    """題庫真的沒有題目時是成功載入一份空索引,不是失敗 —— 空狀態由列表呈現。"""
    serve_catalog(catalog_page, [])

    assert loaded(catalog_page) == []


def test_a_hanging_index_endpoint_gives_up_at_the_timeout(catalog_page) -> None:
    """沒有上界的請求會讓列表永遠停在載入中 —— 逾時把它收斂成一個可辨識的失敗。"""
    hang(catalog_page)

    result = failure(catalog_page, timeout_ms=300)

    assert result["elapsed_ms"] < 5000, f"逾時上界沒有生效:{result}"


# --- 邊界:純資料、位於依賴鏈最左端 -------------------------------------


def test_the_catalog_module_loads_with_no_other_web_module_available(
    browser_page,
) -> None:
    """design 的依賴方向:`catalog.js` 位於最左端,不 import 任何其他 web 模組。

    此處刻意**只供 `catalog.js` 一個檔案**,其餘一律 404 —— 模組若拉進了任何
    同伴,這個 import 就會失敗。
    """
    # 先把檔案讀出來再註冊路由:讀檔若在 handler 裡拋錯,那個請求就永遠不會被
    # 回應,測試會停在瀏覽器的等待中而不是明確地失敗。
    source = CATALOG_JS.read_text(encoding="utf-8")

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/catalog.js":
            route.fulfill(
                status=200,
                content_type="text/javascript; charset=utf-8",
                body=source,
            )
            return
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>單獨載入</title>",
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    browser_page.goto(f"{ORIGIN}/index.html")

    alone = browser_page.evaluate(
        """async () => {
          const catalog = await import('/catalog.js');
          return catalog
            .filterPositions(
              [
                { id: 1, difficulty: 3, tags: ['連將殺'], source: '適情雅趣' },
                { id: 2, difficulty: 5, tags: ['雙包'], source: '橘中秘' },
              ],
              { difficulty: 3 },
            )
            .map((p) => p.id);
        }"""
    )

    assert alone == [1]


def test_the_catalog_module_touches_no_dom() -> None:
    """「純資料,不碰 DOM」是可讀出來的:原始碼裡不出現 `document` 或 `window`。

    這條由原始碼把關而非行為把關,因為瀏覽器裡永遠有 DOM 可碰 —— 沒有任何
    執行結果能證明「它沒去碰」。
    """
    source = CATALOG_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)

    assert not re.search(r"\b(document|window|localStorage)\b", code)
