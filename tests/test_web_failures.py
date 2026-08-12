"""走子途中的失敗呈現(tasks 4.4;requirements 6.4、7.1、7.2、7.3、7.4、7.5)。

design 的 File Structure Plan 把失敗路徑獨立成一檔,理由在這裡看得最清楚:這些
測試問的不是「對局有沒有前進」,而是「**沒有前進的時候畫面說了什麼、使用者接
下來能做什麼**」。它們共用的是同一個問題,而不是同一段流程。

## 為什麼只分兩類

錯誤以**單一通用區塊**呈現(design 的 Error Handling),區分只到「可重試」與
「須重來」兩類 —— 不為七種類別碼各做一套 UI。因此本檔的斷言一律落在兩件事上:

1. 使用者看得到發生了什麼(且看不到後端原文,7.5);
2. 使用者知道接下來能做什麼,而且**那件事真的做得到**(7.4)。

## 夾具沿用 `test_web_play.py`

`play_page` 與座標輔助函式已在該檔備妥,那裡供的是 `web/play.html` 本身。失敗
路徑要問的是同一個頁面在另一種回應下的樣子,重開一份夾具只會讓兩邊漂移。
pytest 以檔名為模組名匯入 `tests/` 下的測試檔,故此處直接以模組名匯入。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from test_web_play import (  # noqa: F401 — `play_page` 以夾具身分被 pytest 取用
    ORIGIN,
    SETTLED,
    black_reply,
    click_square,
    marked_squares,
    move_rows,
    open_game,
    play_page,
    text_of,
    wait_for_moves,
)

#: 應手端點的錯誤回應。狀態碼與類別碼取自 engine-service 的契約。
BUSY = (503, {"code": "SERVICE_BUSY", "message": "engine pool exhausted"})
ENGINE_TIMEOUT = (504, {"code": "ENGINE_TIMEOUT", "message": "search timed out"})
INCONSISTENT = (409, {"code": "ILLEGAL_MOVE_SEQUENCE", "message": "move 3 is illegal"})
WRONG_SIDE = (409, {"code": "WRONG_SIDE_TO_MOVE", "message": "it is red to move"})
INTERNAL = (500, {"code": "INTERNAL", "message": "boom"})

#: 路由層的失敗 —— 框架原生形狀,**不在契約內**,一律歸為通用錯誤(7.5)。
ROUTE_LEVEL = (404, {"detail": "Not Found"})

#: 連線失敗。以 `None` 表示「這一次把請求砍掉」而不是回一份主體。
DISCONNECT = (0, None)

#: 一手正常的應手,用來驗證失敗之後真的還能繼續下。
SUCCESS = (200, black_reply(move="e9d9"))


def route_black_move_sequence(page, responses: list[tuple[int, Any]]) -> dict[str, int]:
    """讓應手端點依序回 `responses`;用完之後一直重複最後一項。

    回傳一個記著它被打了幾次的計數器 —— 「重試」與「原地把訊息清掉」在畫面上
    長得一樣,只有請求次數分得出來。
    """
    served = {"count": 0}

    def handler(route) -> None:
        status, body = responses[min(served["count"], len(responses) - 1)]
        served["count"] += 1
        if body is None:
            route.abort("failed")
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    page.route(f"{ORIGIN}/api/black-move", handler)
    return served


def play_a_move(page) -> None:
    """走一手俥六進一(d8d9)。"""
    click_square(page, "d8")
    click_square(page, "d9")


def wait_settled(page) -> None:
    """等到這一手真的有了結果 —— 沉澱信號的完整說明見 `test_web_play.SETTLED`。

    以「錯誤區塊出現」當結束條件在連續失敗時不管用:上一次的訊息還在畫面上,
    條件立刻成立,下一次點擊就落在請求仍在途、盤面不接受走子的那段空窗裡。

    信號本身已自「`#waiting` 轉隱藏」換成「`#turn` 不再說『(某方)走棋』」——
    那個等待區塊已整條移除(它會推動底下的歷史著法)。對這一檔而言兩者等價:
    失敗會把走法序列整份退回,輪方因此翻回使用者。
    """
    page.wait_for_function(SETTLED)


def failing_move(page, responses: list[tuple[int, Any]]) -> dict[str, int]:
    """開好局、裝上會失敗的應手端點、走一手,並等到錯誤呈現出來。"""
    open_game(page)
    served = route_black_move_sequence(page, responses)
    play_a_move(page)
    page.wait_for_selector("#error:not([hidden])")
    return served


# --- 逾時、斷線與後端錯誤都要告知並可重試(7.1)-------------------------


def test_an_engine_timeout_is_reported_as_a_timeout(play_page) -> None:
    """逾時有自己的說法(7.1)—— 與忙碌、與通用錯誤都分得開。"""
    failing_move(play_page, [ENGINE_TIMEOUT])

    assert "逾時" in text_of(play_page, "#error")


@pytest.mark.slow
def test_a_client_side_timeout_is_reported_and_clears_the_waiting_state(play_page) -> None:
    """**後端整個不回話**時,前端自己的逾時上界要接手(7.1、6.4)。

    這是唯一一條會真的等滿 `api.js` 的逾時上界(10 秒)的測試,慢得刻意:沒有它,
    「等待態必被解除」在最該成立的情況下反而沒有人驗證 —— 後端不回話正是使用者
    最容易永遠卡在等待中的那一種失敗。

    **標記為 slow,平時開發跳過**:它一個人就佔全套執行時間的一成以上,而逾時上界
    是 `api.js` 的模組常數、無法從外部縮短。上線前以 `uv run pytest --slow` 跑到。
    """
    open_game(play_page)
    play_page.route(f"{ORIGIN}/api/black-move", lambda route: None)
    play_a_move(play_page)
    # 前置條件:請求真的在途。等應手期間輪方那一行說的是對手在走(6.1 修訂後由
    # 它承擔等待呈現),而它在點擊派發內就同步變好,不必等。
    assert text_of(play_page, "#turn") == "黑方走棋", "這一手根本沒送出去,逾時無從發生"

    play_page.wait_for_selector("#error:not([hidden])", timeout=20000)

    assert "逾時" in text_of(play_page, "#error")
    # 「等待態必解除」的判準是**盤面重新走得動**,不是某個區塊有沒有收起來
    # (那個 `#waiting` 區塊已整條移除,理由見 `web/play.html`)。下面兩行本來
    # 就在驗這件事,現在它們是 6.4 在這一條測試裡唯一的守門人。
    click_square(play_page, "d8")
    assert marked_squares(play_page) == {"d9"}, "逾時之後盤面仍走不動,等於還停在等待中"


def test_an_unrecognised_response_shape_shows_a_generic_error(play_page) -> None:
    """路由層的框架原生形狀不在契約內,歸為通用錯誤且不呈現原始內容(7.5)。"""
    failing_move(play_page, [ROUTE_LEVEL])

    assert text_of(play_page, "#error") != ""
    body = play_page.locator("body").inner_text()
    assert "detail" not in body
    assert "Not Found" not in body


# --- 忙碌與真正的錯誤分得開(7.2)---------------------------------------


def test_a_busy_service_reads_differently_from_a_generic_error(play_page) -> None:
    """「可與真正的錯誤區分」是**兩份文字不一樣**,不是各自有一套 UI(7.2)。"""
    failing_move(play_page, [BUSY])
    busy = text_of(play_page, "#error")

    open_game(play_page)
    route_black_move_sequence(play_page, [INTERNAL])
    play_a_move(play_page)
    wait_settled(play_page)

    assert text_of(play_page, "#error") != busy


# --- 序列不一致要重來而非重試(7.3)-------------------------------------


def test_the_wrong_side_to_move_is_also_a_reset(play_page) -> None:
    """輪方不符同屬對局層面的失效 —— 再送一次同一手只會再失敗一次(7.3)。"""
    failing_move(play_page, [WRONG_SIDE])

    assert "重來" in text_of(play_page, "#error")


def test_a_reset_after_an_invalidated_game_clears_the_error(play_page) -> None:
    """7.3 提供的重來要真的走得通 —— 按下去之後回到起始局面且訊息收起來。"""
    failing_move(play_page, [INCONSISTENT])

    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)

    assert play_page.locator("#error").is_hidden()
    click_square(play_page, "d8")
    assert marked_squares(play_page) == {"d9"}


# --- 失敗之後維持可操作(7.4)-------------------------------------------


def test_repeated_failures_still_leave_the_interface_usable(play_page) -> None:
    """**連續多次失敗**之後介面仍可操作(7.4)—— 單次不足以證明。

    三次刻意挑不同的類別碼:一次可重試、一次連線層、一次通用。共用同一個區塊
    的實作若在某一類上寫壞(例如把 `hidden` 留在錯的一邊),這裡就會停下來。
    """
    served = failing_move(play_page, [BUSY, DISCONNECT, ROUTE_LEVEL, SUCCESS])

    for _ in range(2):
        assert play_page.locator("#error").is_visible()
        play_a_move(play_page)
        wait_settled(play_page)

    assert served["count"] == 3
    assert play_page.locator("#error").is_visible()
    play_a_move(play_page)
    wait_for_moves(play_page, 2)

    assert move_rows(play_page) == [("俥六進一", "將5平4")]
    assert play_page.locator("#error").is_hidden()


