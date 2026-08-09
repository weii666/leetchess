"""介面組裝層的互動驗證(tasks 4.3、4.4;requirements 1.2、1.3、1.4、3.2、4.1、4.2、
4.4、5.1、6.1、6.4、8.1、8.4)。

`web/app.js` 是唯一把三個模組接起來的地方 —— 它沒有自己的邏輯可言,價值全在
「接得對不對」。因此本檔一律**以真實點擊驅動真實頁面**:載入 `web/index.html`
本身、載入 `web/app.js` 本身,點在棋盤的實際座標上,再看 DOM 變成什麼樣子。
沒有任何一條測試是直接呼叫函式的 —— 那樣測不出事件有沒有綁上去。

## 為什麼仍以 `page.route()` 攔後端

真實引擎不會照劇本演,而 4.3 的完成狀態逐項需要特定回應:「黑方應手為 e9d9」
(才有固定的中文記譜可比對)、「題目不存在」(1.4)、「這一手就結束對局」(3.2)。
攔截讓每一種都成為一行設定,而受測的仍是 `web/` 底下的真實交付檔。

夾具沿用 `test_web_board.py` / `test_web_game.py` 的手法合成一個 http(s) 來源 ——
Chromium 不允許自 `file://`(origin 為 `null`)匯入 ES module。與那兩檔不同的是,
**這裡供的是 `web/index.html` 本身**,骨架的每一個容器 id 因此都是真的那一個。

`style.css` 尚屬 tasks 5.1、此刻不存在,靜態路由會回 404 —— 那不影響任何斷言:
盤面的呈現屬性直接寫在 SVG 元素上(tasks 3.1),沒有 CSS 也畫得出來。

三態信號(4.1、4.2、4.4)與等待狀態(6.1、6.4)屬 tasks 4.4,由本檔最後兩節驗證 ——
它們是**成功路徑上的呈現**,與這裡既有的走子流程共用同一組夾具。走子途中的失敗
呈現(7.1、7.2、7.3)另立 `test_web_failures.py`(design 的 File Structure Plan)。
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

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-play-runtime.test"

#: 《適情雅趣》第 21 局的起始局面(`positions/適情雅趣/0001.json`)。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 起始局面可走的著法。內容由後端決定,前端不參與判斷。
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
    "solvable": True,
    "source": "適情雅趣",
    "state": {
        "side_to_move": "red",
        "legal_moves": START_LEGAL,
        "over": False,
        "winner": None,
    },
}

#: 副檔名對應的 content type。`.css` 目前不存在(tasks 5.1),列著是為了它出現時
#: 不必回頭改夾具。
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


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
FINAL_REPLY = black_reply(move=None, signal="red_winning", mate_in=0, over=True, winner="red")


# --- 夾具 ---------------------------------------------------------------


@pytest.fixture
def play_page(browser_page) -> Iterator:
    """一個備妥靜態路由與題目端點、**但尚未導覽**的分頁。

    刻意不在夾具裡導覽:1.4 要驗的是「題目端點回 404 時的載入結果」,那必須在
    頁面開始跑 `app.js` **之前**就把路由換掉。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        target = WEB_DIR / ("index.html" if path == "/" else path.lstrip("/"))
        if target.is_file():
            route.fulfill(
                status=200,
                content_type=CONTENT_TYPES.get(target.suffix, "text/plain; charset=utf-8"),
                body=target.read_text(encoding="utf-8"),
            )
            return
        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    route_position(browser_page, POSITION_RESPONSE)
    yield browser_page


def route_position(page, body: Any, *, status: int = 200) -> dict[str, int]:
    """讓題目端點回同一份內容,並回傳一個記著它被打了幾次的計數器。

    計數是必要的:載入失敗後的復原能不能算數,取決於**有沒有真的再發一次請求**,
    光看畫面變成什麼樣分不出「重試中」與「原地清掉告知」。
    """
    served = {"count": 0}

    def handler(route) -> None:
        served["count"] += 1
        route.fulfill(
            status=status,
            content_type="application/json",
            body=body if isinstance(body, str) else json.dumps(body),
        )

    page.route(f"{ORIGIN}/api/positions/**", handler)
    return served


def route_black_move(page, bodies: list[Any]) -> None:
    """讓應手端點依序回 `bodies`;用完之後一直重複最後一項。"""
    served = {"count": 0}

    def handler(route) -> None:
        index = min(served["count"], len(bodies) - 1)
        served["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(bodies[index]),
        )

    page.route(f"{ORIGIN}/api/black-move", handler)


def hang_black_move(page) -> None:
    """應手端點收下請求就再也不回話 —— 觀察走子途中的畫面唯一穩定的方式。"""
    page.route(f"{ORIGIN}/api/black-move", lambda route: None)


def hang_position(page) -> None:
    """題目端點收下請求就不回話 —— 觀察**載入期間**的畫面。

    後註冊的路由優先,因此這會蓋掉 `play_page` 夾具裝好的那一條。
    """
    page.route(f"{ORIGIN}/api/positions/**", lambda route: None)


def visit(page, position_id: Any = 1) -> None:
    """開啟頁面。**題號自網址帶入** —— 要載入哪一題由外部決定(brief 的邊界)。"""
    page.goto(f"{ORIGIN}/index.html?id={position_id}")


def open_game(page, bodies: list[Any] | None = None) -> None:
    """開啟頁面並等到起始盤面畫出來。"""
    if bodies is not None:
        route_black_move(page, bodies)
    visit(page)
    page.wait_for_selector("#board svg .piece")


# --- 盤面座標(與 `board.js` 的繪製約定一致)-----------------------------

CELL = 62
MARGIN = 40


def x_of(file: int) -> float:
    return MARGIN + file * CELL


def y_of(rank: int) -> float:
    return MARGIN + (9 - rank) * CELL


def click_square(page, square: str) -> None:
    """點在 `square` 這一格的正中央 —— 命中誰由瀏覽器的 hit test 決定。"""
    file, rank = ord(square[0]) - 97, int(square[1])
    page.locator("#board svg").click(position={"x": x_of(file), "y": y_of(rank)})


def pieces(page) -> dict[str, str]:
    """畫在盤面上的每個子:格名 -> 中文名。"""
    drawn = page.evaluate(
        """() => [...document.querySelectorAll('#board .piece')].map(piece => {
          const disc = piece.querySelector('circle');
          return {
            x: Number(disc.getAttribute('cx')),
            y: Number(disc.getAttribute('cy')),
            name: piece.textContent.trim(),
          };
        })"""
    )
    return {
        f"{chr(97 + round((item['x'] - MARGIN) / CELL))}"
        f"{9 - round((item['y'] - MARGIN) / CELL)}": item["name"]
        for item in drawn
    }


def marked_squares(page) -> set[str]:
    """畫出來的合法落點標示所在的格。"""
    dots = page.evaluate(
        """() => [...document.querySelectorAll('#board .dot')].map(dot => ({
          x: Number(dot.getAttribute('cx')),
          y: Number(dot.getAttribute('cy')),
        }))"""
    )
    return {
        f"{chr(97 + round((dot['x'] - MARGIN) / CELL))}"
        f"{9 - round((dot['y'] - MARGIN) / CELL)}"
        for dot in dots
    }


# --- 側欄 ---------------------------------------------------------------


def text_of(page, selector: str) -> str:
    return page.locator(selector).inner_text().strip()


def move_rows(page) -> list[tuple[str, str]]:
    """歷史著法的每一列:(紅方著法, 黑方著法)。黑方尚未應手時為空字串。"""
    return [
        (row["red"].strip(), row["black"].strip())
        for row in page.evaluate(
            """() => [...document.querySelectorAll('#moves li')].map(row => ({
              red: row.querySelector('.mv-r')?.textContent ?? '',
              black: row.querySelector('.mv-b')?.textContent ?? '',
            }))"""
        )
    ]


def wait_for_moves(page, count: int) -> None:
    """等到歷史著法剛好記了 `count` **手**(不是幾列)—— 走子是非同步的。

    刻意數手而不數列:自己走完但應手還沒回來時,列數就已經是 1 了。以列數當等待
    條件會讓斷言在「半個回合」的狀態下就跑起來,測試因此偶爾綠、偶爾紅。
    """
    page.wait_for_function(
        """count => [...document.querySelectorAll('#moves li')].reduce(
             (total, row) => total + (row.querySelector('.mv-b')?.textContent ? 2 : 1),
             0,
           ) === count""",
        arg=count,
    )


def wait_for_reply(page) -> None:
    """等到剛送出的那一手真的有了結果。

    **數手數在這裡不管用**:排局的最後一手黑方沒有應手,手數在應手回來前後都是
    同一個數字,以它為條件的斷言會在回應還在路上時就跑起來。等待態解除才是「這
    次請求已經落地」的唯一信號,而它在點擊當下就已同步變為真。
    """
    page.wait_for_function("() => document.getElementById('waiting').hidden")


# --- 題目資訊(1.2、1.3)-------------------------------------------------


def test_the_title_and_source_are_shown(play_page) -> None:
    """載入完成後顯示局名與出處(1.2)。"""
    open_game(play_page)

    assert text_of(play_page, "#puzzle-title") == "適情雅趣 第 21 局"
    assert "適情雅趣" in text_of(play_page, "#puzzle-source")


def test_the_longest_mate_distance_is_shown(play_page) -> None:
    """題目帶有最長殺著距離時顯示該資訊(1.3)。"""
    open_game(play_page)

    assert "9" in text_of(play_page, "#puzzle-max-dtm")


def test_a_puzzle_without_a_mate_distance_shows_no_bogus_number(play_page) -> None:
    """1.3 是條件式的 —— 沒有這項資訊時不得憑空生一個數字出來。"""
    route_position(play_page, {**POSITION_RESPONSE, "max_dtm": None})
    open_game(play_page)

    assert not re.search(r"\d", text_of(play_page, "#puzzle-max-dtm"))


def test_a_mate_distance_of_zero_is_still_a_number(play_page) -> None:
    """`max_dtm` 為 0 時仍是一個要呈現的數字(1.3)。

    0 是 falsy —— 以真假值判斷會把它和「沒有這項資訊」混為一談,而那正是 `mate_in`
    已經踩過的同一個坑。此處鎖住的是「有值就呈現」,不是「非零才呈現」。
    """
    route_position(play_page, {**POSITION_RESPONSE, "max_dtm": 0})
    open_game(play_page)

    assert "0" in text_of(play_page, "#puzzle-max-dtm")


# --- 題目不存在(1.4)---------------------------------------------------


def test_a_missing_puzzle_is_reported_instead_of_a_blank_board(play_page) -> None:
    """題目不存在時告知使用者,**而非呈現空白盤面**(1.4)。"""
    route_position(
        play_page,
        {"code": "POSITION_NOT_FOUND", "message": "position 9999 not found"},
        status=404,
    )
    visit(play_page, 9999)
    play_page.wait_for_selector("#error:not([hidden])")

    assert "找不到" in text_of(play_page, "#error")
    assert play_page.locator("#board svg").count() == 0, "沒有題目就不該畫出一面空棋盤"
    assert text_of(play_page, "#board") != "", "盤面的位置要有話說,不能一片空白"


def test_a_missing_puzzle_leaks_no_backend_text(play_page) -> None:
    """告知的文字由前端自己產生 —— 後端原文一個字都不得出現(7.5 的延伸)。"""
    route_position(
        play_page,
        {"code": "POSITION_NOT_FOUND", "message": "position 9999 not found"},
        status=404,
    )
    visit(play_page, 9999)
    play_page.wait_for_selector("#error:not([hidden])")

    body = play_page.locator("body").inner_text()
    assert "not found" not in body
    assert "POSITION_NOT_FOUND" not in body


def test_a_missing_puzzle_does_not_leave_the_title_saying_loading(play_page) -> None:
    """載入失敗後標題不得停在「載入中…」—— 那會讓使用者以為還在等。"""
    route_position(play_page, {"code": "POSITION_NOT_FOUND", "message": "沒這題"}, status=404)
    visit(play_page, 9999)
    play_page.wait_for_selector("#error:not([hidden])")

    assert "載入中" not in text_of(play_page, "#puzzle-title")


def test_reset_after_a_failed_load_retries_the_load(play_page) -> None:
    """載入失敗後按「重來」要重新載入,而不是留下一個沒有請求在跑的畫面(1.4、7.4)。

    重來還原不了一個從未載入成功的題目 —— 起始局面根本沒拿到過。而「重來」是那個
    失敗畫面上**唯一的按鈕**:它若只是把失敗的告知清掉,使用者就落在 7.4 明文禁止
    的無法復原畫面,除了手動重新整理沒有出路。那裡唯一有意義的復原是再載入一次。
    """
    requested = route_position(
        play_page, {"code": "POSITION_NOT_FOUND", "message": "沒這題"}, status=404
    )
    visit(play_page, 9999)
    play_page.wait_for_selector("#error:not([hidden])")
    assert requested["count"] == 1

    # 點擊本身只是派送事件,請求是非同步發出的 —— 等到回應真的回來才數得準。
    with play_page.expect_response(f"{ORIGIN}/api/positions/**"):
        play_page.locator("#reset").click()

    assert requested["count"] == 2, "重來必須重新發出載入請求,而不是原地清掉失敗的告知"
    play_page.wait_for_function(
        "() => !document.getElementById('puzzle-title').textContent.includes('載入中')"
    )
    assert "找不到" in text_of(play_page, "#error"), "重試後仍失敗,告知不得消失"


# --- 輪方可辨識(8.4)---------------------------------------------------


def test_the_current_turn_is_shown(play_page) -> None:
    """當前輪方對使用者可辨識(8.4)。"""
    open_game(play_page)

    turn = text_of(play_page, "#turn")
    assert "紅" in turn
    assert "黑" not in turn


def test_the_turn_switches_to_black_while_the_engine_answers(play_page) -> None:
    """走完自己那一手後輪方翻成黑方 —— 光有靜態文字不算「可辨識」。"""
    open_game(play_page)
    hang_black_move(play_page)

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    play_page.wait_for_function(
        "() => document.getElementById('turn').textContent.includes('黑')"
    )

    assert "黑" in text_of(play_page, "#turn")


# --- 完整一手:選子 -> 落點 -> 盤面更新 -> 歷史著法新增一列 ----------------


def test_selecting_a_piece_marks_its_destinations(play_page) -> None:
    """選子後標示該子的合法落點 —— 證明選子事件真的接到盤面上了。"""
    open_game(play_page)

    click_square(play_page, "d8")

    assert marked_squares(play_page) == {"d9"}


def test_a_full_move_updates_the_board_and_appends_one_row(play_page) -> None:
    """tasks 4.3 的完成狀態:選子 -> 落點 -> 盤面更新 -> 歷史著法新增一列。"""
    open_game(play_page, [black_reply(move="e9d9")])
    assert pieces(play_page)["d8"] == "俥"
    assert move_rows(play_page) == []

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    placed = pieces(play_page)
    assert "d8" not in placed, "俥離開了原來的格"
    assert "e9" not in placed, "黑將吃回那枚俥"
    assert placed["d9"] == "將"
    assert move_rows(play_page) == [("俥六進一", "將5平4")]


def test_the_history_grows_one_row_per_pair_of_moves(play_page) -> None:
    """一列是一個回合(紅方一手 + 黑方應手),與 POC 的 `renderMoves` 相同。"""
    open_game(
        play_page,
        [
            black_reply(move="e9d9", legal_moves=["f8f9"]),
            black_reply(move="d9e9", legal_moves=["f9f8"]),
        ],
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)
    click_square(play_page, "f8")
    click_square(play_page, "f9")
    wait_for_moves(play_page, 4)

    assert move_rows(play_page) == [("俥六進一", "將5平4"), ("俥四進一", "將4平5")]


def test_a_move_without_a_reply_leaves_the_black_half_empty(play_page) -> None:
    """黑方無應手(排局的最後一手)時該列只有紅方那一半,不得憑空補字。"""
    open_game(play_page, [FINAL_REPLY])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 1)

    assert move_rows(play_page) == [("俥六進一", "")]


def test_clicking_an_unmarked_square_changes_nothing(play_page) -> None:
    """點在未標示的位置不改變盤面也不送出著法(2.3 在組裝層的體現)。"""
    open_game(play_page, [black_reply()])
    before = pieces(play_page)

    click_square(play_page, "d8")
    click_square(play_page, "a5")  # 空格,且不在 d8 的落點裡
    play_page.wait_for_timeout(150)

    assert pieces(play_page) == before
    assert move_rows(play_page) == []


# --- 終局呈現(3.2)-----------------------------------------------------


def test_the_winner_is_shown_when_the_game_ends(play_page) -> None:
    """對局結束時呈現勝方(3.2)。"""
    open_game(play_page, [FINAL_REPLY])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 1)
    play_page.wait_for_function(
        "() => document.getElementById('turn').textContent.includes('結束')"
    )

    turn = text_of(play_page, "#turn")
    assert "紅" in turn and "勝" in turn


def test_a_finished_game_offers_no_more_destinations(play_page) -> None:
    """對局結束後不再接受走子(3.2)—— 盤面整片沒有東西可選。"""
    open_game(play_page, [FINAL_REPLY])
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 1)

    click_square(play_page, "d9")  # 剛走到 d9 的俥
    click_square(play_page, "f8")

    assert marked_squares(play_page) == set()


def test_losing_is_reported_as_the_backend_says(play_page) -> None:
    """勝方一律照後端回報 —— 黑方勝時不得說成使用者獲勝。"""
    open_game(play_page, [black_reply(move="e9d9", over=True, winner="black")])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    play_page.wait_for_function(
        "() => document.getElementById('turn').textContent.includes('結束')"
    )

    turn = text_of(play_page, "#turn")
    assert "黑" in turn and "勝" in turn
    assert "獲勝" not in turn


# --- 重來(5.1)---------------------------------------------------------


def test_reset_clears_the_history_and_the_board(play_page) -> None:
    """重來回到起始局面並清空歷史著法(5.1)。"""
    open_game(play_page, [black_reply(move="e9d9")])
    start_pieces = pieces(play_page)
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)

    assert pieces(play_page) == start_pieces
    assert move_rows(play_page) == []


def test_the_game_can_be_played_again_after_a_reset(play_page) -> None:
    """重來之後還能再走 —— 清空不能把事件綁定一併清掉。"""
    open_game(play_page, [black_reply(move="e9d9")])
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)
    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert move_rows(play_page) == [("俥六進一", "將5平4")]


def test_reset_after_a_finished_game_makes_it_playable_again(play_page) -> None:
    """下完一整局之後重來,盤面又整片可選(5.1)。"""
    open_game(play_page, [FINAL_REPLY])
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 1)

    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)
    click_square(play_page, "d8")

    assert marked_squares(play_page) == {"d9"}


def test_reset_clears_the_selected_piece(play_page) -> None:
    """重來把選中的子一併放掉(5.1)。

    選中的格是唯一存在呈現層的狀態,重來則整份換掉走法序列 —— 留著它,畫面上就會
    有一組對應不到任何操作的落點標示(requirements 7.4 的「卡在不可知狀態」)。
    """
    open_game(play_page, [black_reply(move="e9d9", legal_moves=["f8f9"])])
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    click_square(play_page, "f8")
    assert marked_squares(play_page) != set(), "前置條件:選中 f8 之後應該看得到落點"

    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)

    assert marked_squares(play_page) == set()


# --- 三態諮詢信號(4.1、4.2、4.4)---------------------------------------


def test_a_winning_signal_is_shown_after_a_reply(play_page) -> None:
    """取得應手後呈現三種信號狀態之一 —— 這是「即將取勝」那一種(4.1)。"""
    open_game(play_page, [black_reply(signal="red_winning", mate_in=4)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert "即將取勝" in text_of(play_page, "#signal")


def test_a_losing_signal_is_shown_after_a_reply(play_page) -> None:
    """「即將落敗」那一種(4.1)。使用者執紅,故黑方即將取勝就是他要落敗。"""
    open_game(play_page, [black_reply(signal="black_winning", mate_in=3)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert "即將落敗" in text_of(play_page, "#signal")


def test_an_unknown_signal_is_shown_after_a_reply(play_page) -> None:
    """「未知」那一種(4.1)—— 引擎一分未報時仍要有話說,不得留白。"""
    open_game(play_page, [black_reply(signal="unknown", mate_in=None)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert "未知" in text_of(play_page, "#signal")


def test_the_mate_countdown_is_shown_as_an_approximation(play_page) -> None:
    """殺著倒數以**近似值**形式呈現(4.2)。

    後端在 250k 節點下可能高估 1 步,寫成確數等於把一個刻意接受的誤差說成精確值。
    """
    open_game(play_page, [black_reply(signal="red_winning", mate_in=4)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    signal = text_of(play_page, "#signal")
    assert "4" in signal
    assert "約" in signal, "倒數必須是近似值的說法,不能寫成確數"


def test_a_mate_countdown_of_zero_is_still_shown(play_page) -> None:
    """**倒數為 0 時仍正常呈現**(4.2)。

    `mate_in: 0` 不是邊緣案例 —— 它正是每一題排局的最後一手(紅方這一手就將死
    黑方)。JS 的 `if (mateIn)`、`mateIn || '—'` 對 0 都是假,倒數會在最關鍵的那
    一手被靜默吞掉。此處鎖住的是「有值就呈現」,不是「非零才呈現」。
    """
    open_game(play_page, [FINAL_REPLY])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    assert "0" in text_of(play_page, "#signal")


def test_a_signal_without_a_countdown_shows_no_bogus_number(play_page) -> None:
    """沒有殺著倒數時不得憑空生一個數字 —— 與 `max_dtm` 的條件式呈現同理。"""
    open_game(play_page, [black_reply(signal="red_winning", mate_in=None)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert not re.search(r"\d", text_of(play_page, "#signal"))


def test_a_reply_without_an_opponent_move_still_shows_its_signal(play_page) -> None:
    """對手著法為空時信號仍呈現 —— 兩者是**獨立**欄位(design 的 app.js 一節)。

    把「無應手」實作成整份回應為空、或順手省掉信號,就會在每一題的最後一手把
    信號一起弄丟。此處刻意同時斷言:黑方那一半是空的,而信號有值。
    """
    open_game(play_page, [FINAL_REPLY])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    assert move_rows(play_page) == [("俥六進一", "")], "前置條件:黑方確實沒有應手"
    assert "即將取勝" in text_of(play_page, "#signal")


def test_the_signal_is_presented_as_advisory_not_a_verdict(play_page) -> None:
    """信號的呈現須讓使用者辨識它是**參考資訊而非勝負判決**(4.4)。

    同一份畫面上還必須看得出對局沒有結束 —— 信號說即將取勝,但對局未終,盤面
    仍然可走(3.3 在呈現層的體現)。
    """
    open_game(play_page, [black_reply(signal="red_winning", mate_in=2, legal_moves=["f8f9"])])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert "參考" in text_of(play_page, "#signal")
    assert "結束" not in text_of(play_page, "#turn")
    click_square(play_page, "f8")
    assert marked_squares(play_page) != set(), "信號為即將取勝不得讓盤面停下來"


def test_the_signal_goes_back_to_no_reading_after_a_reset(play_page) -> None:
    """重來後信號回到「尚未取得」—— 上一局的讀數不得留在畫面上(5.1、4.4)。"""
    open_game(play_page, [black_reply(signal="red_winning", mate_in=4)])
    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)
    assert "即將取勝" in text_of(play_page, "#signal")

    play_page.locator("#reset").click()
    wait_for_moves(play_page, 0)

    signal = text_of(play_page, "#signal")
    assert "即將取勝" not in signal
    assert not re.search(r"\d", signal)


# --- 等待狀態(6.1、6.4)-----------------------------------------------


def test_the_waiting_state_is_shown_while_the_engine_answers(play_page) -> None:
    """等待後端回應期間呈現等待中的狀態(6.1)。"""
    open_game(play_page)
    hang_black_move(play_page)

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    play_page.wait_for_selector("#waiting:not([hidden])")

    assert text_of(play_page, "#waiting") != ""


def test_the_waiting_state_is_shown_while_the_puzzle_loads(play_page) -> None:
    """載入題目也是在等後端回應,同樣要有狀態(6.1)。"""
    hang_position(play_page)
    visit(play_page)

    play_page.wait_for_selector("#waiting:not([hidden])")
    assert text_of(play_page, "#waiting") != ""


def test_the_waiting_state_is_cleared_after_the_reply(play_page) -> None:
    """回應之後等待狀態解除(6.4)。"""
    open_game(play_page, [black_reply(move="e9d9")])
    assert play_page.locator("#waiting").is_hidden(), "前置條件:載入完成後就不該還在等"

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert play_page.locator("#waiting").is_hidden()


def test_the_waiting_state_is_cleared_after_a_failure(play_page) -> None:
    """**失敗之後等待狀態也要解除**(6.4)—— 不得留在等待中無法操作。"""
    open_game(play_page)
    play_page.route(f"{ORIGIN}/api/black-move", lambda route: route.abort("failed"))

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    play_page.wait_for_selector("#error:not([hidden])")

    assert play_page.locator("#waiting").is_hidden()


# --- 依賴方向 -----------------------------------------------------------


def test_app_module_is_imported_by_nobody(play_page) -> None:
    """`app.js` 在依賴鏈的最右端 —— 沒有任何模組可以反過來依賴它。"""
    importers = [
        source.name
        for source in WEB_DIR.glob("*.js")
        if source.name != "app.js"
        # 只看真的匯入 —— 註解裡提到 `app.js`(其他模組都有)不算依賴。
        and re.search(
            r"(from|import)\s*\(?\s*'\./app\.js'", source.read_text(encoding="utf-8")
        )
    ]

    assert importers == [], f"app.js 不得被任何模組匯入,卻出現在:{importers}"


def test_app_module_imports_only_the_layers_below_it() -> None:
    """`app.js` 只往左依賴 `game.js` / `board.js` / `notation.js` / `fen.js`。"""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    imported = re.findall(r"^\s*import[^\n]*?from\s*'([^']+)'", source, flags=re.MULTILINE)
    dynamic = re.findall(r"\bimport\s*\(\s*'([^']+)'", source)

    assert set(imported + dynamic) <= {
        "./game.js",
        "./board.js",
        "./notation.js",
        "./fen.js",
    }, f"app.js 的依賴超出組裝層可見的範圍:{imported + dynamic}"


def test_app_module_never_calls_fetch_itself() -> None:
    """後端往來一律經 `api.js`(`game.js` 轉手),組裝層不得自己發請求。"""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    assert "fetch(" not in code
