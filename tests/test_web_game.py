"""對局狀態機的驗證(tasks 4.2;requirements 2.5、3.1–3.5、4.3、4.5、5.1、5.2、6.2、6.4、7.3、7.4)。

`web/game.js` 是前端唯一持有對局狀態的地方,而**走法序列是唯一的真相** —— 盤面、
輪方、可走的著法全部由它推導。本檔驗證的正是這個推導,以及三條不可動搖的界線:

1. **終局只依據後端回應中的 `state.over`**(3.3、4.3)。信號為任何值都不得使對局
   提早結束 —— 後端的終局判定裡沒有分數,前端也不得引入。這裡有一整節在打這一點。
2. **不提供任何形式的單手回退**(5.2)。狀態只能整體前進、清空(重來)或整體
   替換(失敗復原),因此測試同時斷言**對外的操作集合**沒有多出退一手的入口。
3. **不自行判斷任何棋規** —— 可走的著法一律取自後端回應。

## 為什麼整份用 `page.route()` 攔截

tasks 4.2 的完成狀態逐項都要求特定的後端回應:「信號為即將取勝但未結束」、
「對手著法為空且已結束」、「失敗後仍可再走子」。真實引擎不會照劇本演;攔截讓
每一種都成為一行設定,而受測的仍是 `web/game.js` 這個真實交付檔本身。

夾具沿用 `test_web_api.py` / `test_web_board.py` 的手法:以 `page.route()` 合成一個
http(s) 來源就地供 `web/` 底下的真實檔案,因為 Chromium 不允許自 `file://`
(origin 為 `null`)匯入 ES module。`game.js` 不碰 DOM,頁面因此只需要是空白的。

**檔名**:design 的 File Structure Plan 把互動測試劃給 `test_web_play.py`。此處沿用
既有的 `test_web_<模組>.py`(`test_web_api.py`、`test_web_board.py`)—— 本檔測的是
`game.js` 這個模組本身,`test_web_play.py` 留給 tasks 4.3/4.4 的介面層互動。
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

#: 《適情雅趣》第 21 局的起始局面(`positions/適情雅趣/0001.json`)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 起始局面可走的著法。內容由後端決定,`game.js` 不參與判斷。
START_LEGAL = ["d8d9", "f8f9", "e2e1"]

#: `GET /api/positions/{id}` 的回應(engine-service 的實測形狀)。
POSITION_RESPONSE: dict[str, Any] = {
    "id": 1,
    "title": "適情雅趣 第 21 局",
    "description": "《適情雅趣》第 21 局",
    "fen": PUZZLE_FEN,
    "side_to_move": "red",
    "difficulty": 3,
    "tags": ["排局"],
    "max_dtm": 9,
    "source": "適情雅趣",
    "state": {
        "side_to_move": "red",
        "legal_moves": START_LEGAL,
        "over": False,
        "winner": None,
    },
}

#: 契約裡的三種信號(`service/types.py` 的 `Signal`)。
SIGNALS = ["red_winning", "black_winning", "unknown"]


def black_reply(
    *,
    move: str | None = "e9d9",
    signal: str = "red_winning",
    mate_in: int | None = 4,
    legal_moves: list[str] | None = None,
    over: bool = False,
    winner: str | None = None,
) -> dict[str, Any]:
    """`POST /api/black-move` 的回應。預設是一手尋常的應手,對局尚未結束。"""
    return {
        "move": move,
        "signal": signal,
        "mate_in": mate_in,
        "state": {
            "side_to_move": "black" if over else "red",
            "legal_moves": [] if over else (legal_moves or ["d9d8", "f8f9"]),
            "over": over,
            "winner": winner,
        },
    }


#: 每一題排局的最後一手:紅方這一手就將死黑方 —— 黑方無應手、對局結束、紅方勝。
#: **殺著倒數為 0**,而 JS 的 `if (mateIn)` 對 0 為假,因此它一併是個 falsy 陷阱的樣本。
FINAL_REPLY = black_reply(move=None, signal="red_winning", mate_in=0, over=True, winner="red")


# --- 夾具 ---------------------------------------------------------------


@pytest.fixture
def game_page(browser_page) -> Iterator:
    """一個位於 http 來源、可 `import` `web/game.js` 的空白分頁。

    題目端點先給一份正常回應,測試要別的形狀時自行覆蓋(後註冊者優先);
    應手端點刻意不預設 —— 每個測試都得自己說後端這次要回什麼。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 不用 web/play.html:它會去載 app.js(tasks 4.3),
                # 而本檔要驗證的是 game.js 能單獨運作 —— 它不需要任何 DOM。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>對局狀態機驗證</title>",
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
    route_position(browser_page, POSITION_RESPONSE)
    browser_page.goto(f"{ORIGIN}/index.html")
    yield browser_page


@pytest.fixture
def api_calls(game_page) -> list[dict]:
    """所有打到 `/api/` 的請求,依發生順序記錄。

    「每手棋只發一次請求、不先查詢局面」(3.1)只能靠**看見全部請求**來證明,
    所以這裡記的是瀏覽器真的送出去的東西,而不是某個 route handler 收到的東西。
    """
    calls: list[dict] = []

    def record(request) -> None:
        path = urlsplit(request.url).path
        if path.startswith("/api/"):
            calls.append(
                {"path": path, "method": request.method, "post_data": request.post_data}
            )

    game_page.on("request", record)
    return calls


def route_position(page, body: Any, *, status: int = 200) -> None:
    """讓題目端點回同一份內容。"""
    page.route(
        f"{ORIGIN}/api/positions/**",
        lambda route: route.fulfill(
            status=status,
            content_type="application/json",
            body=body if isinstance(body, str) else json.dumps(body),
        ),
    )


def route_black_move(page, replies: list[tuple[int, Any]]) -> None:
    """讓應手端點依序回 `replies`;用完之後一直重複最後一項。"""
    served = {"count": 0}

    def handler(route) -> None:
        index = min(served["count"], len(replies) - 1)
        served["count"] += 1
        status, body = replies[index]
        route.fulfill(
            status=status,
            content_type="application/json",
            body=body if isinstance(body, str) else json.dumps(body),
        )

    page.route(f"{ORIGIN}/api/black-move", handler)


def replies(*bodies: Any) -> list[tuple[int, Any]]:
    """一串成功的應手。"""
    return [(200, body) for body in bodies]


def backend_error(code: str, *, status: int = 500) -> tuple[int, Any]:
    """後端的端點錯誤形狀:`{code, message}`。訊息刻意寫得很有辨識度。"""
    return (status, {"code": code, "message": f"後端原文:{code} 的技術細節"})


def hang(page) -> None:
    """應手端點收下請求就再也不回話 —— 觀察等待態唯一穩定的方式。"""
    page.route(f"{ORIGIN}/api/black-move", lambda route: None)


# --- 在瀏覽器裡驅動狀態機 -------------------------------------------------


_SETUP = """
async ({ positionId, extraFeedback }) => {
  const { createGame, DEFAULT_FEEDBACK_SOURCES } = await import('/game.js');
  // 走脫回饋的來源是一份清單(4.5)。多一個來源就是多一個項目,推進流程不因此改寫。
  const feedbackSources = extraFeedback
    ? [...DEFAULT_FEEDBACK_SOURCES, ({ reply, moves }) => ({
        source: 'stub',
        plies: moves.length,
        verdict: 'escaped',
        sawReply: reply != null,
      })]
    : DEFAULT_FEEDBACK_SOURCES;
  window.game = createGame({ positionId, feedbackSources });
  window.states = [];
  window.game.subscribe((state) => window.states.push(state));
  return null;
}
"""

_CALL = """
async ({ method, args }) => await window.game[method](...args)
"""


def setup_game(page, position_id: int = 1, *, extra_feedback: bool = False) -> None:
    page.evaluate(_SETUP, {"positionId": position_id, "extraFeedback": extra_feedback})


def call(page, method: str, *args) -> Any:
    return page.evaluate(_CALL, {"method": method, "args": list(args)})


def state(page) -> dict:
    return page.evaluate("() => window.game.getState()")


def notified(page) -> list[dict]:
    """訂閱者依序收到的每一份狀態。"""
    return page.evaluate("() => window.states")


def start(page, *, extra_feedback: bool = False) -> dict:
    """建立狀態機並載入題目,回傳載入後的狀態。"""
    setup_game(page, extra_feedback=extra_feedback)
    assert call(page, "load") is True
    return state(page)


def piece_at(snapshot: dict, square: str) -> str | None:
    """盤面上某一格的子;`None` 為空格。座標約定同 `fen.js`。"""
    file = ord(square[0]) - 97
    rank = int(square[1])
    return snapshot["board"][rank][file]


def black_move_calls(calls: list[dict]) -> list[dict]:
    return [call_ for call_ in calls if call_["path"] == "/api/black-move"]


# --- 載入:走法序列自空開始 -----------------------------------------------


def test_loading_puts_the_starting_position_on_the_state(game_page) -> None:
    """載入之後盤面就是題目的起始局面,走法序列是空的。"""
    snapshot = start(game_page)

    assert snapshot["moves"] == []
    assert snapshot["position"]["title"] == POSITION_RESPONSE["title"]
    assert snapshot["position"]["max_dtm"] == 9
    assert piece_at(snapshot, "d8") == "R"
    assert piece_at(snapshot, "e9") == "k"
    assert piece_at(snapshot, "e0") == "K"


def test_the_legal_moves_come_from_the_backend_reply(game_page) -> None:
    """可走的著法一律取自後端(2.4),`game.js` 不推導也不驗證。"""
    snapshot = start(game_page)

    assert snapshot["legalMoves"] == START_LEGAL
    assert snapshot["turn"] == "red"
    assert snapshot["waiting"] is False
    assert snapshot["over"] is False
    assert snapshot["error"] is None


def test_a_failed_load_clears_the_waiting_state_and_can_be_retried(game_page) -> None:
    """載入失敗後等待態解除(6.4),而且還能再試一次(7.1、7.4)。"""
    route_position(game_page, {"code": "POSITION_NOT_FOUND", "message": "沒這題"}, status=404)
    setup_game(game_page)

    assert call(game_page, "load") is False
    failed = state(game_page)
    assert failed["waiting"] is False
    assert failed["error"]["code"] == "POSITION_NOT_FOUND"
    assert failed["board"] is None

    route_position(game_page, POSITION_RESPONSE)
    assert call(game_page, "load") is True
    assert state(game_page)["error"] is None
    assert state(game_page)["legalMoves"] == START_LEGAL


def test_moves_are_refused_before_a_position_is_loaded(game_page, api_calls) -> None:
    """沒有題目就沒有局面,也就沒有任何著法可送。"""
    setup_game(game_page)

    assert call(game_page, "play", "d8d9") is False
    assert black_move_calls(api_calls) == []


# --- 推進:一手棋一次請求 -------------------------------------------------


def test_one_move_takes_exactly_one_request_and_never_asks_for_the_state(
    game_page, api_calls
) -> None:
    """走出一手後**以單一請求**取得應手與其後的局面狀態(3.1)。

    多一次查詢局面會讓通過後端併發閘門的次數加倍,因此這裡看的是**全部**送出去的
    請求,而不只是應手端點收到幾次。
    """
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    api_calls.clear()

    assert call(game_page, "play", "d8d9") is True

    assert [call_["path"] for call_ in api_calls] == ["/api/black-move"]


def test_the_reply_is_appended_and_the_board_follows_the_sequence(game_page) -> None:
    """盤面由走法序列推導 —— 自己的一手與對手的應手都在序列裡。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply(move="e9d9")))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["moves"] == ["d8d9", "e9d9"]
    assert piece_at(snapshot, "d8") is None
    assert piece_at(snapshot, "e9") is None
    assert piece_at(snapshot, "d9") == "k"  # 黑將吃回那枚俥
    assert snapshot["turn"] == "red"
    assert snapshot["legalMoves"] == ["d9d8", "f8f9"]


def test_every_request_resends_the_whole_sequence(game_page, api_calls) -> None:
    """每次請求重送完整走法序列(3.5),不依賴後端記憶任何對局進度。"""
    start(game_page)
    route_black_move(
        game_page,
        replies(
            black_reply(move="e9d9", legal_moves=["f8f9"]),
            black_reply(move="d9e9", legal_moves=["f9f8"]),
        ),
    )
    api_calls.clear()

    call(game_page, "play", "d8d9")
    call(game_page, "play", "f8f9")

    bodies = [json.loads(call_["post_data"]) for call_ in black_move_calls(api_calls)]
    assert bodies == [
        {"position_id": 1, "moves": ["d8d9"]},
        {"position_id": 1, "moves": ["d8d9", "e9d9", "f8f9"]},
    ]


def test_only_moves_offered_by_the_backend_are_sent(game_page, api_calls) -> None:
    """未列在後端合法著法中的走子不送出任何請求(2.3、2.4)。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    api_calls.clear()

    assert call(game_page, "play", "a0a1") is False

    assert black_move_calls(api_calls) == []
    assert state(game_page)["moves"] == []


# --- 終局:只看回應中的結束旗標 -------------------------------------------


@pytest.mark.parametrize("signal", SIGNALS + ["a_signal_nobody_has_seen_yet"])
def test_no_signal_value_ends_the_game(game_page, signal) -> None:
    """**信號為任何值都不得使對局結束**(3.3、4.3)。

    這是與後端對應的核心保證:後端的終局判定裡沒有分數,前端也不得引入。連契約外
    的新信號都一樣 —— 判定的依據只有 `state.over`。
    """
    start(game_page)
    route_black_move(game_page, replies(black_reply(signal=signal, over=False)))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["over"] is False
    assert snapshot["winner"] is None
    assert snapshot["legalMoves"] == ["d9d8", "f8f9"]


def test_a_winning_signal_leaves_the_game_playable(game_page, api_calls) -> None:
    """信號為即將取勝但未結束時,對局仍可繼續走子(tasks 4.2 的完成狀態)。"""
    start(game_page)
    route_black_move(
        game_page,
        replies(
            black_reply(signal="red_winning", mate_in=1, legal_moves=["d9d8"]),
            black_reply(signal="red_winning", mate_in=0, legal_moves=["d8d7"]),
        ),
    )

    call(game_page, "play", "d8d9")
    assert state(game_page)["over"] is False

    api_calls.clear()
    assert call(game_page, "play", "d9d8") is True
    assert len(black_move_calls(api_calls)) == 1
    assert state(game_page)["over"] is False


def test_a_losing_signal_does_not_end_the_game_either(game_page) -> None:
    """即將落敗同樣不是終局 —— 對局照樣下下去(3.3)。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply(signal="black_winning", mate_in=None)))

    call(game_page, "play", "d8d9")

    assert state(game_page)["over"] is False
    assert state(game_page)["legalMoves"] != []


def test_an_empty_reply_that_is_over_means_the_user_won(game_page) -> None:
    """對手著法為空且對局已結束 → 使用者獲勝(3.4)。那是每一題排局的最後一手。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["over"] is True
    assert snapshot["winner"] == "red"
    assert snapshot["userWon"] is True
    assert snapshot["moves"] == ["d8d9"], "黑方沒有應手,序列就停在自己這一手"
    assert piece_at(snapshot, "d9") == "R"


def test_a_finished_game_accepts_no_more_moves(game_page, api_calls) -> None:
    """對局結束後停止接受走子(3.2)—— 對外的著法集合先空掉,守衛再擋一次。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))
    call(game_page, "play", "d8d9")
    api_calls.clear()

    assert state(game_page)["legalMoves"] == []
    assert call(game_page, "play", "f8f9") is False
    assert black_move_calls(api_calls) == []


def test_losing_is_reported_as_the_backend_says(game_page) -> None:
    """勝方一律照後端回報 —— 黑方勝時使用者沒有獲勝。"""
    start(game_page)
    route_black_move(
        game_page, replies(black_reply(move="e9d9", over=True, winner="black"))
    )

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["over"] is True
    assert snapshot["winner"] == "black"
    assert snapshot["userWon"] is False


# --- 等待態與輪方 ---------------------------------------------------------


def test_no_moves_are_accepted_while_waiting(game_page, api_calls) -> None:
    """等待後端回應期間不接受新的走子(6.2)。"""
    start(game_page)
    hang(game_page)
    game_page.evaluate("() => { window.pending = window.game.play('d8d9'); }")
    game_page.wait_for_timeout(150)

    waiting = state(game_page)
    assert waiting["waiting"] is True
    assert waiting["moves"] == ["d8d9"], "自己的一手立刻進序列,盤面才會馬上動"
    assert call(game_page, "play", "f8f9") is False
    assert len(black_move_calls(api_calls)) == 1


def test_the_move_set_is_empty_while_waiting(game_page) -> None:
    """等待中對外的著法集合必須是空的(6.2)。

    `board.js` 不判斷子的歸屬 —— 它的「可選取」定義就是「該格有著法可出發」,
    因此**唯一**能讓盤面整片不可選的方式,就是這裡給出空集合。
    """
    start(game_page)
    hang(game_page)
    game_page.evaluate("() => { window.pending = window.game.play('d8d9'); }")
    game_page.wait_for_timeout(150)

    assert state(game_page)["legalMoves"] == []


def test_no_moves_are_accepted_while_the_position_is_reloading(
    game_page, api_calls
) -> None:
    """等待中不接受走子(6.2)—— **等的是哪一個請求都一樣**。

    走子期間輪方本來就已經翻給對方,光靠輪方也擋得住;重新載入時卻不然:輪方仍是
    自己、對局也還沒結束,此時唯一擋得住的就是等待態本身。載入失敗後重試(7.1)
    正好會經過這個狀態。
    """
    start(game_page)
    game_page.route(f"{ORIGIN}/api/positions/**", lambda route: None)
    game_page.evaluate("() => { window.pending = window.game.load(); }")
    game_page.wait_for_timeout(150)

    reloading = state(game_page)
    assert reloading["waiting"] is True
    assert reloading["turn"] == "red", "輪方還是自己 —— 擋住走子的只能是等待態"
    assert reloading["legalMoves"] == []
    assert call(game_page, "play", "d8d9") is False
    assert black_move_calls(api_calls) == []


def test_the_move_set_is_empty_when_it_is_not_the_users_turn(game_page) -> None:
    """非我方回合不接受走子(2.5),同樣以空的著法集合表達。"""
    start(game_page)
    hang(game_page)
    game_page.evaluate("() => { window.pending = window.game.play('d8d9'); }")
    game_page.wait_for_timeout(150)

    waiting = state(game_page)
    assert waiting["turn"] == "black", "輪方由走法序列推得,自己走完就輪到對方"
    assert waiting["userSide"] == "red"
    assert waiting["legalMoves"] == []


def test_the_move_set_is_empty_once_the_game_is_over(game_page) -> None:
    """對局結束後同樣沒有任何著法可出發(2.5、3.2)。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))

    call(game_page, "play", "d8d9")

    assert state(game_page)["legalMoves"] == []


def test_subscribers_see_the_waiting_state_come_and_go(game_page) -> None:
    """等待態在回應後被解除(6.4),而且訂閱者看得到它出現過。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    game_page.evaluate("() => { window.states = []; }")

    call(game_page, "play", "d8d9")

    seen = [snapshot["waiting"] for snapshot in notified(game_page)]
    assert seen == [True, False]
    assert state(game_page)["waiting"] is False


# --- 重來:走法序列清空 ---------------------------------------------------


def test_reset_clears_the_move_sequence(game_page) -> None:
    """重來即走法序列清空(5.1)—— 盤面與歷史都是它的推導,因此一併回到起點。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    call(game_page, "play", "d8d9")
    assert state(game_page)["moves"] == ["d8d9", "e9d9"]

    call(game_page, "reset")
    snapshot = state(game_page)

    assert snapshot["moves"] == []
    assert snapshot["legalMoves"] == START_LEGAL
    assert snapshot["feedback"] == []
    assert snapshot["error"] is None
    assert snapshot["over"] is False
    assert piece_at(snapshot, "d8") == "R"
    assert piece_at(snapshot, "d9") == "a"


def test_reset_needs_no_backend(game_page, api_calls) -> None:
    """重來不打後端。

    重來是失敗之後的復原路徑(7.3);它自己再依賴一次網路,就會在連線壞掉時把
    使用者鎖死在不可復原的狀態,正好違反 7.4。起始局面在載入時就拿到了。
    """
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    call(game_page, "play", "d8d9")
    api_calls.clear()

    call(game_page, "reset")

    assert api_calls == []
    assert state(game_page)["moves"] == []


def test_reset_recovers_a_game_finished_by_the_backend(game_page) -> None:
    """下完一整局之後重來,對局回到可走子的起點。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))
    call(game_page, "play", "d8d9")
    assert state(game_page)["over"] is True

    call(game_page, "reset")
    snapshot = state(game_page)

    assert snapshot["over"] is False
    assert snapshot["winner"] is None
    assert snapshot["userWon"] is None
    assert snapshot["legalMoves"] == START_LEGAL


def test_a_reply_that_lands_after_a_reset_is_discarded(game_page) -> None:
    """重來期間在途的應手回來時必須作廢,否則走法序列會被它重新填滿。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))

    game_page.evaluate(
        """() => {
          window.pending = window.game.play('d8d9');   // 送出去了
          window.game.reset();                          // 回應還沒回來就重來
        }"""
    )
    assert game_page.evaluate("async () => await window.pending") is False

    snapshot = state(game_page)
    assert snapshot["moves"] == []
    assert snapshot["waiting"] is False
    assert snapshot["legalMoves"] == START_LEGAL


# --- 不提供任何單手回退(5.2)---------------------------------------------


def test_the_game_offers_no_single_move_takeback(game_page) -> None:
    """對外的操作只有這五個 —— **沒有退回上一手的入口,也不為它預留結構**(5.2)。

    允許逐步回退等於允許試錯搜尋,而排局的價值在於想清楚再落子(product 決定)。
    """
    setup_game(game_page)

    surface = game_page.evaluate(
        """() => {
          const names = new Set();
          let object = window.game;
          while (object && object !== Object.prototype) {
            for (const name of Object.getOwnPropertyNames(object)) names.add(name);
            object = Object.getPrototypeOf(object);
          }
          return [...names];
        }"""
    )

    assert set(surface) == {"getState", "subscribe", "load", "play", "reset"}


def test_the_source_has_no_takeback_vocabulary() -> None:
    """連程式碼裡都不該出現悔棋的字眼(design 的 Non-Goals:不得為其預留任何結構)。"""
    source = (WEB_DIR / "game.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    found = re.findall(r"undo|takeback|stepBack|悔棋", code, flags=re.IGNORECASE)

    assert not found, f"game.js 不得提供任何單手回退,卻出現:{found}"


def test_the_state_cannot_be_edited_from_outside(game_page) -> None:
    """對外的狀態是一份唯讀快照 —— 改它不會動到真相,序列尤其不能被人 `pop` 掉一手。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    call(game_page, "play", "d8d9")

    game_page.evaluate(
        """() => {
          const snapshot = window.game.getState();
          try { snapshot.moves.pop(); } catch (ignored) {}
          try { snapshot.over = true; } catch (ignored) {}
          try { snapshot.legalMoves.length = 0; } catch (ignored) {}
        }"""
    )
    snapshot = state(game_page)

    assert snapshot["moves"] == ["d8d9", "e9d9"]
    assert snapshot["over"] is False
    assert snapshot["legalMoves"] == ["d9d8", "f8f9"]


# --- 失敗:整份序列退回送出前的值 -----------------------------------------


def test_a_failed_move_rolls_the_sequence_back_and_the_user_can_move_again(
    game_page,
) -> None:
    """失敗復原為**整份序列退回送出前的值**,之後仍可再走子(7.4)。"""
    start(game_page)
    route_black_move(game_page, [backend_error("INTERNAL"), (200, black_reply())])

    assert call(game_page, "play", "d8d9") is False
    failed = state(game_page)
    assert failed["moves"] == []
    assert failed["waiting"] is False
    assert failed["error"]["code"] == "INTERNAL"
    assert failed["legalMoves"] == START_LEGAL, "退回之後盤面又整片可走"
    assert piece_at(failed, "d8") == "R"

    assert call(game_page, "play", "d8d9") is True
    recovered = state(game_page)
    assert recovered["moves"] == ["d8d9", "e9d9"]
    assert recovered["error"] is None


def test_the_game_stays_operable_after_repeated_failures(game_page) -> None:
    """連續多次失敗後介面仍可操作(7.4)—— 單次不足以證明。"""
    start(game_page)
    route_black_move(
        game_page,
        [
            backend_error("SERVICE_BUSY", status=503),
            backend_error("ENGINE_TIMEOUT", status=504),
            backend_error("INTERNAL"),
            (200, black_reply()),
        ],
    )

    for _ in range(3):
        assert call(game_page, "play", "d8d9") is False
        assert state(game_page)["waiting"] is False
        assert state(game_page)["moves"] == []
        assert state(game_page)["legalMoves"] == START_LEGAL

    assert call(game_page, "play", "d8d9") is True
    assert state(game_page)["moves"] == ["d8d9", "e9d9"]


def test_a_failed_move_clears_the_waiting_state(game_page) -> None:
    """任何失敗之後等待態必被解除(6.4),不留在等待中無法操作。"""
    start(game_page)
    route_black_move(game_page, [backend_error("INTERNAL")])
    game_page.evaluate("() => { window.states = []; }")

    call(game_page, "play", "d8d9")

    assert [snapshot["waiting"] for snapshot in notified(game_page)] == [True, False]


@pytest.mark.parametrize(
    ("code", "status", "kind"),
    [
        ("ILLEGAL_MOVE_SEQUENCE", 409, "reset"),
        ("WRONG_SIDE_TO_MOVE", 409, "reset"),
        ("SERVICE_BUSY", 503, "retry"),
        ("ENGINE_TIMEOUT", 504, "retry"),
        ("INTERNAL", 500, "retry"),
        ("INVALID_MOVE_FORMAT", 400, "retry"),
    ],
)
def test_failures_are_sorted_into_retry_and_reset(game_page, code, status, kind) -> None:
    """失敗只分兩類:可重試與須重來(design 的 Error Handling)。

    走法序列與局面不一致時對局狀態已失效,重試同一手只會再失敗一次 —— 那一類要的
    是重來(7.3)。其餘一律可重試。
    """
    start(game_page)
    route_black_move(game_page, [backend_error(code, status=status)])

    call(game_page, "play", "d8d9")
    failure = state(game_page)["error"]

    assert failure["code"] == code
    assert failure["kind"] == kind


def test_a_stale_sequence_can_be_recovered_by_resetting(game_page) -> None:
    """對局狀態已失效之後,重來確實把它救回可走子(7.3、7.4)。"""
    start(game_page)
    route_black_move(
        game_page, [backend_error("ILLEGAL_MOVE_SEQUENCE", status=409), (200, black_reply())]
    )
    call(game_page, "play", "d8d9")
    assert state(game_page)["error"]["kind"] == "reset"

    call(game_page, "reset")
    assert state(game_page)["error"] is None
    assert call(game_page, "play", "d8d9") is True


def test_the_failure_carries_nothing_but_a_code(game_page) -> None:
    """後端的原文一個字都不得進到狀態裡(7.5)。"""
    start(game_page)
    route_black_move(game_page, [backend_error("INTERNAL")])

    call(game_page, "play", "d8d9")
    dumped = game_page.evaluate("() => JSON.stringify(window.game.getState())")

    assert set(state(game_page)["error"]) == {"code", "kind"}
    assert "後端原文" not in dumped
    assert "技術細節" not in dumped


def test_an_unrecognisable_reply_is_a_failure_not_a_move(game_page) -> None:
    """無法辨識的回應不得被當成一手棋推進對局(7.5)。"""
    start(game_page)
    route_black_move(game_page, [(200, {"move": "e9d9"})])  # 少了局面狀態

    assert call(game_page, "play", "d8d9") is False
    snapshot = state(game_page)
    assert snapshot["moves"] == []
    assert snapshot["error"]["code"] == "UNKNOWN"
    assert snapshot["over"] is False


# --- 走脫回饋的來源可擴充(4.5)-------------------------------------------


def test_the_signal_arrives_as_one_feedback_entry(game_page) -> None:
    """目前唯一的回饋來源是三態信號。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply(signal="red_winning", mate_in=4)))

    call(game_page, "play", "d8d9")

    assert state(game_page)["feedback"] == [
        {"source": "signal", "signal": "red_winning", "mateIn": 4}
    ]


def test_a_zero_countdown_survives(game_page) -> None:
    """**殺著倒數可能為 0**(終局那手)。JS 的 `if (mateIn)` 對 0 為假,不得被吞掉。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))

    call(game_page, "play", "d8d9")

    assert state(game_page)["feedback"] == [
        {"source": "signal", "signal": "red_winning", "mateIn": 0}
    ]


def test_an_empty_reply_still_carries_its_signal(game_page) -> None:
    """對手著法為空時信號仍可能有值,兩者是獨立資訊。"""
    start(game_page)
    route_black_move(game_page, replies(FINAL_REPLY))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["moves"] == ["d8d9"]
    assert snapshot["feedback"][0]["signal"] == "red_winning"


def test_an_extra_feedback_source_needs_no_change_to_the_advance_flow(game_page) -> None:
    """新增一個回饋來源就是多一個項目 —— 推進流程一字不改(4.5)。

    日後的走脫判定表就是這樣接進來的:它是**新增來源**,而不是改寫這條流程。
    """
    start(game_page, extra_feedback=True)
    route_black_move(game_page, replies(black_reply()))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["feedback"] == [
        {"source": "signal", "signal": "red_winning", "mateIn": 4},
        {"source": "stub", "plies": 2, "verdict": "escaped", "sawReply": True},
    ]
    assert snapshot["moves"] == ["d8d9", "e9d9"], "推進的結果與沒有這個來源時完全一樣"
    assert snapshot["over"] is False


def test_no_feedback_source_can_end_the_game(game_page) -> None:
    """回饋只是參考資訊 —— 它判什麼都不影響對局是否結束(4.3)。"""
    start(game_page, extra_feedback=True)
    route_black_move(game_page, replies(black_reply(signal="black_winning")))

    call(game_page, "play", "d8d9")
    snapshot = state(game_page)

    assert snapshot["feedback"][1]["verdict"] == "escaped"
    assert snapshot["over"] is False
    assert snapshot["legalMoves"] != []


def test_feedback_is_cleared_when_the_game_restarts(game_page) -> None:
    """重來之後沒有任何回饋 —— 還沒走出一手,就沒有東西可回饋。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    call(game_page, "play", "d8d9")
    assert state(game_page)["feedback"] != []

    call(game_page, "reset")

    assert state(game_page)["feedback"] == []


# --- 訂閱者的例外不得使狀態機壞掉 -----------------------------------------
#
# 呈現層(`app.js`)是第一個訂閱者,而它會在通知期間碰 DOM —— 一個沒接到的節點、
# 一則記譜算不出來的著法,都會讓 listener 丟出例外。那是呈現層的缺陷,但**不得
# 由此變成對局狀態的缺陷**:狀態機若因此讓快照與走法序列說不同的話,或讓等待態
# 永遠解不開(6.4),使用者就卡在一個誰都救不回來的畫面(7.4)。


def break_subscriber(page) -> None:
    """掛一個一被通知就丟例外的訂閱者,後面再掛一個正常的觀察者。

    兩個一起掛,才問得出第二件事:壞掉的那個不能把後面的訂閱者一併噤聲。
    """
    page.evaluate(
        """() => {
          window.game.subscribe(() => { throw new Error('呈現層壞了'); });
          window.later = [];
          window.game.subscribe((state) => window.later.push(state.moves));
        }"""
    )


def test_a_throwing_subscriber_does_not_corrupt_a_successful_move(game_page) -> None:
    """訂閱者丟例外之後,快照仍與走法序列一致。

    退化的樣子是快照對著**起始盤面**發出**走後**的著法清單 —— 那份畫面上的每一個
    落點都會被後端拒絕,而使用者看不出哪裡不對。
    """
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    break_subscriber(game_page)

    assert call(game_page, "play", "d8d9") is True
    snapshot = state(game_page)

    assert snapshot["moves"] == ["d8d9", "e9d9"]
    assert snapshot["legalMoves"] == ["d9d8", "f8f9"]
    assert snapshot["waiting"] is False
    assert snapshot["error"] is None


def test_a_throwing_subscriber_does_not_strand_the_waiting_state(game_page) -> None:
    """訂閱者丟例外時等待態仍必被解除(6.4),失敗也仍記得下來(7.1)。"""
    start(game_page)
    route_black_move(game_page, [backend_error("INTERNAL")])
    break_subscriber(game_page)

    assert call(game_page, "play", "d8d9") is False
    snapshot = state(game_page)

    assert snapshot["waiting"] is False
    assert snapshot["error"]["code"] == "INTERNAL"
    assert snapshot["moves"] == []


def test_a_throwing_subscriber_does_not_silence_the_others(game_page) -> None:
    """壞掉的訂閱者不得讓排在它後面的訂閱者收不到通知。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    break_subscriber(game_page)

    call(game_page, "play", "d8d9")

    assert game_page.evaluate("() => window.later") == [["d8d9"], ["d8d9", "e9d9"]]


def test_a_throwing_subscriber_does_not_break_load_or_reset(game_page) -> None:
    """載入與重來同樣經過通知,同樣不得因訂閱者的例外而失敗。"""
    start(game_page)
    route_black_move(game_page, replies(black_reply()))
    call(game_page, "play", "d8d9")
    break_subscriber(game_page)

    call(game_page, "reset")
    assert state(game_page)["moves"] == []

    assert call(game_page, "load") is True
    assert state(game_page)["legalMoves"] == START_LEGAL


def test_a_throwing_feedback_source_rolls_the_whole_state_back(game_page) -> None:
    """回饋來源丟例外時,**盤面與著法清單也要一起退回送出前的值**(7.4)。

    只退 `moves` 是不夠的:局面狀態停在推進後,快照就會對著起始盤面發出走後的
    著法清單。回饋是參考資訊,它壞掉不得讓對局進到一個說不通的狀態。
    """
    game_page.evaluate(
        """async () => {
          const { createGame } = await import('/game.js');
          window.game = createGame({
            positionId: 1,
            feedbackSources: [() => { throw new Error('回饋來源壞了'); }],
          });
        }"""
    )
    assert call(game_page, "load") is True
    route_black_move(game_page, replies(black_reply()))

    assert call(game_page, "play", "d8d9") is False
    snapshot = state(game_page)

    assert snapshot["moves"] == []
    assert snapshot["legalMoves"] == START_LEGAL, "退回之後盤面要與起始局面一致"
    assert snapshot["waiting"] is False
    assert snapshot["over"] is False
    assert piece_at(snapshot, "d8") == "R"


# --- 依賴方向 -----------------------------------------------------------


def test_game_module_imports_only_the_client_and_fen() -> None:
    """design 的依賴方向:`game.js` 在 `api.js` / `fen.js` 的右邊一層,不得反向依賴。"""
    source = (WEB_DIR / "game.js").read_text(encoding="utf-8")

    imported = re.findall(r"^\s*import[^\n]*?from\s*'([^']+)'", source, flags=re.MULTILINE)
    dynamic = re.findall(r"\bimport\s*\(\s*'([^']+)'", source)

    assert set(imported + dynamic) <= {"./api.js", "./fen.js"}, (
        f"game.js 只能依賴 api.js 與 fen.js,卻出現:{imported + dynamic}"
    )


def test_game_module_does_not_touch_the_dom() -> None:
    """`game.js` 只管對局狀態;呈現是 `app.js` 的事(design 的 Components)。"""
    source = (WEB_DIR / "game.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    forbidden = [
        token
        for token in ("document", "window", "localStorage", "alert(", "innerHTML")
        if token in code
    ]

    assert not forbidden, f"game.js 不得使用:{forbidden}"


def test_game_module_never_calls_fetch_itself() -> None:
    """後端往來一律經 `api.js` —— 逾時與錯誤模型才只有一份(design 的 api.js 責任)。"""
    source = (WEB_DIR / "game.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    assert "fetch(" not in code
