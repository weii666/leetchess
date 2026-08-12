"""後端 client 的驗證(tasks 4.1;requirements 1.4、3.5、7.1、7.2、7.5)。

`web/api.js` 是前端唯一與後端往來的地方。它要做的事只有兩件:把兩個端點包成
可呼叫的操作,以及把後端可能回的每一種東西**收斂成前端認得的成敗**。

## 為什麼整份用 `page.route()` 攔截

逾時、連線失敗、服務忙碌、路由層的框架原生格式 —— 這四種情況沒有一種能用真實
服務穩定觸發(要嘛得把服務打到忙碌,要嘛得拔網路線)。攔截讓每一種都成為一行
設定,而受測的仍是 `web/api.js` 這個真實交付檔本身。

夾具沿用 `test_web_pure.py` / `test_web_board.py` 的手法:以 `page.route()` 合成一個
http(s) 來源就地供 `web/` 底下的真實檔案,因為 Chromium 不允許自 `file://`
(origin 為 `null`)匯入 ES module。

**每個測試自己註冊 `/api/**` 的攔截**,而且會蓋過夾具的供檔規則 —— Playwright 的
路由規則後註冊者優先。

## 斷言「不含原始內容」的方式

requirements 7.5 要求後端的原始錯誤內容一路都不得到達使用者。測試不去猜呈現層
會讀哪個欄位,而是把失敗物件的**所有自有屬性連同 `toString()` 一起攤平成字串**
(見 `_INVOKE` 的 `dump`),再斷言後端送來的字樣完全不出現在裡面。這樣即使日後
有人把後端訊息塞進某個新欄位,測試也會紅。
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

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下。
ORIGIN = "https://web-play-runtime.test"

#: `GET /api/positions/{id}` 的回應(engine-service 的實測形狀)。
POSITION_RESPONSE: dict[str, Any] = {
    "id": 1,
    "title": "適情雅趣 第 21 局",
    "description": "《適情雅趣》第 21 局",
    "fen": "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1",
    "side_to_move": "red",
    "difficulty": 3,
    "tags": ["排局"],
    "max_dtm": 9,
    "source": "適情雅趣",
    "state": {
        "side_to_move": "red",
        "legal_moves": ["d8d9", "f8f9", "e2e1"],
        "over": False,
        "winner": None,
    },
}

#: `POST /api/black-move` 的回應。
BLACK_MOVE_RESPONSE: dict[str, Any] = {
    "move": "e9d9",
    "signal": "winning",
    "mate_in": 4,
    "state": {
        "side_to_move": "red",
        "legal_moves": ["d8d7", "f8f9"],
        "over": False,
        "winner": None,
    },
}

#: 契約裡的七種類別碼(`service/errors.py` 的 `ErrorCode`)。
BACKEND_CODES = [
    "INVALID_MOVE_FORMAT",
    "POSITION_NOT_FOUND",
    "ILLEGAL_MOVE_SEQUENCE",
    "WRONG_SIDE_TO_MOVE",
    "SERVICE_BUSY",
    "ENGINE_TIMEOUT",
    "INTERNAL",
]

# --- 夾具與呼叫工具 -----------------------------------------------------


@pytest.fixture
def api_page(browser_page) -> Iterator:
    """一個位於 http 來源、可 `import` `web/api.js` 的空白分頁。

    只供 `web/` 底下的真實檔案;`/api/**` 刻意不在此處處理,交由各測試自行
    註冊(後註冊者優先),沒註冊時會落到最後的 404,那本身也是一種被測情況。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 不用 web/play.html:它會去載 app.js(tasks 4.3),
                # 而本檔要驗證的是 api.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>後端 client 驗證</title>",
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


#: 在瀏覽器裡呼叫 `api.js` 的一個操作,把成敗攤平成可帶回 Python 的資料。
_INVOKE = """
async ({ op, args, timeoutMs }) => {
  const api = await import('/api.js');
  const options = timeoutMs == null ? undefined : { timeoutMs };
  const started = performance.now();
  try {
    const value = await api[op](...args, options);
    return { ok: true, value, elapsed_ms: performance.now() - started };
  } catch (error) {
    // 把失敗物件的所有自有屬性連同 toString() 攤平 —— 後端字樣若藏在任何
    // 一個欄位裡都會出現在 dump 中。
    const own = {};
    for (const key of Object.getOwnPropertyNames(error)) own[key] = String(error[key]);
    return {
      ok: false,
      elapsed_ms: performance.now() - started,
      code: error.code ?? null,
      name: error.name ?? null,
      is_api_error: error instanceof api.ApiError,
      dump: [String(error), JSON.stringify(own)].join(' | '),
    };
  }
}
"""


def invoke(page, op: str, *args, timeout_ms: int | None = None) -> dict:
    """呼叫 `api.js` 的 `op`,回傳 `{ok: True, value}` 或攤平後的失敗描述。"""
    return page.evaluate(
        _INVOKE, {"op": op, "args": list(args), "timeoutMs": timeout_ms}
    )


def route_api(page, handler) -> None:
    """攔截所有 `/api/` 開頭的請求。註冊在夾具之後,因此優先於供檔規則。"""
    page.route(f"{ORIGIN}/api/**", handler)


def respond(page, *, status: int = 200, body: Any = None, content_type: str | None = None):
    """讓後端一律回同一份內容,並把收到的請求記在回傳的 list 裡。"""
    seen: list[dict] = []

    def handler(route) -> None:
        request = route.request
        seen.append(
            {
                "url": request.url,
                "method": request.method,
                "post_data": request.post_data,
            }
        )
        route.fulfill(
            status=status,
            content_type=content_type or "application/json",
            body=body if isinstance(body, str) else json.dumps(body),
        )

    route_api(page, handler)
    return seen


def hang(page) -> None:
    """後端收下請求就再也不回話 —— 逾時上界唯一的驗證方式。"""
    route_api(page, lambda route: None)


def failure(page, op: str, *args, timeout_ms: int | None = None) -> dict:
    """呼叫並斷言它失敗,回傳攤平後的失敗描述。"""
    result = invoke(page, op, *args, timeout_ms=timeout_ms)
    assert result["ok"] is False, f"預期失敗卻成功了:{result}"
    assert result["is_api_error"] is True, f"失敗必須是 ApiError:{result}"
    return result


def value(page, op: str, *args, timeout_ms: int | None = None) -> Any:
    """呼叫並斷言它成功,回傳結果。"""
    result = invoke(page, op, *args, timeout_ms=timeout_ms)
    assert result["ok"] is True, f"預期成功卻失敗了:{result}"
    return result["value"]


# --- 成功路徑:兩個操作與請求形狀 ---------------------------------------


def test_loading_a_position_hits_the_position_endpoint_by_path(api_page) -> None:
    """載入題目走 `GET /api/positions/{id}`,題號在路徑上。"""
    seen = respond(api_page, body=POSITION_RESPONSE)

    value(api_page, "loadPosition", 1)

    assert len(seen) == 1
    assert seen[0]["method"] == "GET"
    assert urlsplit(seen[0]["url"]).path == "/api/positions/1"


def test_black_move_sends_the_sequence_in_the_request_body(api_page) -> None:
    """走法序列走請求主體,**不走 query string**(POC 的 `api()` 正是敗在這)。"""
    seen = respond(api_page, body=BLACK_MOVE_RESPONSE)

    value(api_page, "requestBlackMove", 1, ["d8d9", "e9d9", "f8f9"])

    assert len(seen) == 1
    assert seen[0]["method"] == "POST"
    parsed = urlsplit(seen[0]["url"])
    assert parsed.path == "/api/black-move"
    assert parsed.query == "", "走法序列不得出現在 query string"
    assert json.loads(seen[0]["post_data"]) == {
        "position_id": 1,
        "moves": ["d8d9", "e9d9", "f8f9"],
    }


def test_black_move_keeps_a_zero_countdown_and_an_empty_reply(api_page) -> None:
    """終局那手:應手為空、對局結束、**殺著倒數為 0**。

    0 不得在 client 這一層被 falsy 判斷吞掉 —— 呈現層(4.4)才有機會顯示它。
    """
    respond(
        api_page,
        body={
            "move": None,
            "signal": "winning",
            "mate_in": 0,
            "state": {
                "side_to_move": "black",
                "legal_moves": [],
                "over": True,
                "winner": "red",
            },
        },
    )

    reply = value(api_page, "requestBlackMove", 1, ["d8d9"])

    assert reply["mate_in"] == 0, "殺著倒數 0 必須原封不動傳出去"
    assert reply["move"] is None
    assert reply["state"]["over"] is True
    assert reply["state"]["winner"] == "red"


# --- 逾時 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "args"),
    [("loadPosition", [1])],
)
def test_a_backend_that_never_answers_becomes_a_timeout(api_page, op, args) -> None:
    """兩個操作都有逾時上界:後端不回話時在上界內轉為可辨識的失敗(7.1)。

    沒有上界的話呼叫方會永遠停在等待中 —— requirements 6.4 的「等待態必被解除」
    在那種情況下根本無從實現。
    """
    hang(api_page)

    result = failure(api_page, op, *args, timeout_ms=200)

    assert result["code"] == "TIMEOUT"
    assert result["elapsed_ms"] < 5000, "逾時後仍等了很久,上界沒有生效"


# --- 連線失敗 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "args"),
    [("loadPosition", [1])],
)
def test_a_connection_failure_becomes_a_recognisable_failure(api_page, op, args) -> None:
    """連線建立不起來時轉為可辨識的失敗,而不是讓例外原樣往上冒(7.1)。"""
    route_api(api_page, lambda route: route.abort("connectionfailed"))

    result = failure(api_page, op, *args)

    assert result["code"] == "NETWORK"


def test_a_connection_failure_leaks_no_browser_internals(api_page) -> None:
    """瀏覽器自己的錯誤訊息(`Failed to fetch`)也是技術細節,不得外流(7.5)。"""
    route_api(api_page, lambda route: route.abort("connectionfailed"))

    result = failure(api_page, "loadPosition", 1)

    assert "Failed to fetch" not in result["dump"]
    assert "TypeError" not in result["dump"]


# --- 帶類別碼的錯誤 -----------------------------------------------------


def test_a_missing_position_is_recognisable_when_loading(api_page) -> None:
    """題目不存在時呼叫方分得出來,呈現層才能告知而非畫出空白盤面(1.4)。"""
    respond(
        api_page,
        status=404,
        body={"code": "POSITION_NOT_FOUND", "message": "找不到題號 9999 的題目"},
    )

    result = failure(api_page, "loadPosition", 9999)

    assert result["code"] == "POSITION_NOT_FOUND"


def test_an_invalid_sequence_is_recognisable(api_page) -> None:
    """走法序列與局面不一致要分得出來 —— 那條路徑的出口是重來而非重試(7.3)。"""
    respond(
        api_page,
        status=409,
        body={"code": "ILLEGAL_MOVE_SEQUENCE", "message": "走法序列在此局面走不出"},
    )

    result = failure(api_page, "requestBlackMove", 1, ["d8d9"])

    assert result["code"] == "ILLEGAL_MOVE_SEQUENCE"


# --- 框架原生格式與其他無法辨識的回應 -----------------------------------


@pytest.mark.parametrize(
    ("status", "detail"),
    [(404, "Not Found")],
)
def test_the_framework_native_shape_becomes_a_generic_failure(
    api_page, status, detail
) -> None:
    """路由層的 404/405 回框架原生的 `{"detail": ...}`,不在契約內。

    它沒有類別碼,因此一律歸為通用錯誤;而且 `detail` 的原文一個字都不得
    出現在交給呈現層的東西裡(7.5)。
    """
    respond(api_page, status=status, body={"detail": detail})

    result = failure(api_page, "requestBlackMove", 1, ["d8d9"])

    assert result["code"] == "UNKNOWN"
    assert detail not in result["dump"]
    assert "detail" not in result["dump"]


def test_an_html_error_page_becomes_a_generic_failure(api_page) -> None:
    """反向代理回的 HTML 錯誤頁同樣是無法辨識的形狀。"""
    respond(
        api_page,
        status=502,
        body="<html><head><title>502 Bad Gateway</title></head>"
        "<body><center><h1>502 Bad Gateway</h1></center>"
        "<hr><center>nginx/1.25.3</center></body></html>",
        content_type="text/html",
    )

    result = failure(api_page, "loadPosition", 1)

    assert result["code"] == "UNKNOWN"
    assert "Bad Gateway" not in result["dump"]
    assert "nginx" not in result["dump"]


def test_an_unknown_error_code_becomes_a_generic_failure(api_page) -> None:
    """契約外的類別碼不得原樣傳出去 —— 那也是後端的原始內容。

    這條是 `code in 契約七碼` 與 `code 存在就照抄` 兩種寫法的分野。
    """
    respond(
        api_page,
        status=400,
        body={"code": "QUOTA_EXCEEDED", "message": "配額用盡"},
    )

    result = failure(api_page, "requestBlackMove", 1, ["d8d9"])

    assert result["code"] == "UNKNOWN"
    assert "QUOTA_EXCEEDED" not in result["dump"]
    assert "配額" not in result["dump"]


def test_a_success_status_with_a_non_json_body_is_not_passed_through(api_page) -> None:
    """200 但內容根本不是 JSON(登入導向頁最常見)。"""
    respond(
        api_page,
        status=200,
        body="<html><body>請先登入</body></html>",
        content_type="text/html",
    )

    result = failure(api_page, "loadPosition", 1)

    assert result["code"] == "UNKNOWN"
    assert "請先登入" not in result["dump"]


def test_every_failure_code_is_declared_in_the_exported_table(api_page) -> None:
    """失敗的類別是**列舉**而非隨手寫的字串,呼叫方才有東西可以比對。"""
    codes = api_page.evaluate(
        """async () => {
          const { ApiErrorCode } = await import('/api.js');
          return Object.values(ApiErrorCode);
        }"""
    )

    assert set(codes) == set(BACKEND_CODES) | {"TIMEOUT", "NETWORK", "UNKNOWN"}


# --- 依賴方向 -----------------------------------------------------------


def test_api_module_does_not_touch_the_dom() -> None:
    """`api.js` 只管往返後端;呈現是 `app.js` 的事(design 的 Components)。"""
    source = (WEB_DIR / "api.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    forbidden = [
        token
        for token in ("document", "localStorage", "alert(", "innerHTML")
        if token in code
    ]

    assert not forbidden, f"api.js 不得使用:{forbidden}"
