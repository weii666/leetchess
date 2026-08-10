"""介面組裝層的互動驗證(tasks 4.3、4.4;requirements 1.2、1.3、1.4、3.2、4.1、4.2、
4.4、4.5、5.1、6.1、6.2、6.4、8.1、8.4)。

`web/app.js` 是唯一把三個模組接起來的地方 —— 它沒有自己的邏輯可言,價值全在
「接得對不對」。因此本檔一律**以真實點擊驅動真實頁面**:載入 `web/play.html`
本身、載入 `web/app.js` 本身,點在棋盤的實際座標上,再看 DOM 變成什麼樣子。
沒有任何一條測試是直接呼叫函式的 —— 那樣測不出事件有沒有綁上去。

## 為什麼仍以 `page.route()` 攔後端

真實引擎不會照劇本演,而 4.3 的完成狀態逐項需要特定回應:「黑方應手為 e9d9」
(才有固定的中文記譜可比對)、「題目不存在」(1.4)、「這一手就結束對局」(3.2)。
攔截讓每一種都成為一行設定,而受測的仍是 `web/` 底下的真實交付檔。

夾具沿用 `test_web_board.py` / `test_web_game.py` 的手法合成一個 http(s) 來源 ——
Chromium 不允許自 `file://`(origin 為 `null`)匯入 ES module。與那兩檔不同的是,
**這裡供的是 `web/play.html` 本身**,骨架的每一個容器 id 因此都是真的那一個。

對局頁的位址是 `/play.html` —— 入口已讓給題庫列表(problem-browser 的 tasks 3.1)。
下面的靜態路由仍把 `/` 對應到 `index.html`,那不是殘留:它就是 `StaticFiles(html=True)`
的行為,合成來源照著它走,才不會與真實服務漂移。

`style.css` 尚屬 tasks 5.1、此刻不存在,靜態路由會回 404 —— 那不影響任何斷言:
盤面的呈現屬性直接寫在 SVG 元素上(tasks 3.1),沒有 CSS 也畫得出來。

三態信號(4.1、4.2、4.4)與等待狀態(6.1、6.2、6.4)屬 tasks 4.4,由本檔最後兩節驗證 ——
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
    page.goto(f"{ORIGIN}/play.html?id={position_id}")


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


#: **「這一次請求已經落地」的沉澱信號** —— `#turn` 不再說「(某方)走棋」。
#:
#: 舊的信號是「`#waiting` 轉隱藏」。那個元素已整條移除(它在版面流裡來去,會把
#: 底下的歷史著法上下推動),信號因此改掛在 `#turn` 上。
#:
#: 為什麼它與舊信號一樣可靠 —— 兩個方向都要成立:
#:
#: 1. **等待期間必為真的「走棋」。** `game.js` 的 `play()` 在第一個 `await` 之前
#:    就同步把使用者那半手推進序列並 `notify()`,輪方隨即翻成對手。所以 Playwright
#:    的 `click()` 一回來,`#turn` **已經**是「黑方走棋」,和 `waiting` 翻真是同一
#:    個同步時點,沒有競態。
#: 2. **落地之後必不再是「走棋」。** 三條出路都離開這個字串:成功且未終局 ->
#:    「輪到你」(應手進序列,輪方翻回自己);失敗 -> 「輪到你」(序列整份退回);
#:    終局 -> 「你獲勝」/「黑方勝」/「對局結束」。**終局那一手正是舊註解點名的
#:    邊界**,而它在這裡不必特判 —— 它不會變回「輪到你」,但它也絕不留在「走棋」。
#:
#: 寫成 `endsWith('走棋')` 而不是寫死「黑方走棋」,是為了執黑的題目:那時等待中
#: 說的是「紅方走棋」。判準是「輪不到我」,與顏色無關。
#:
#: 這個信號**不能**換成數 `#moves li`:使用者自己的半手在點擊當下就同步進了序列,
#: 而最後一手 `move` 為 `null`,列數在應手回來前後完全一樣(web-play-runtime 4.4
#: 的教訓)。
SETTLED = "() => !document.getElementById('turn').textContent.trim().endsWith('走棋')"


def wait_for_reply(page) -> None:
    """等到剛送出的那一手真的有了結果(沉澱信號見 `SETTLED`)。"""
    page.wait_for_function(SETTLED)


# --- 題目資訊(1.2、1.3)-------------------------------------------------


def test_the_title_and_source_are_shown(play_page) -> None:
    """載入完成後顯示題號、局名與出處(1.2)。

    標題的字面是「題號 + 點 + 空格 + 局名」,與列表同一個形態 —— 從列表點進來看到
    的是同一個字串,一眼就確認自己開對了題。
    """
    open_game(play_page)

    assert text_of(play_page, "#puzzle-title") == "1. 適情雅趣 第 21 局"
    assert "適情雅趣" in text_of(play_page, "#puzzle-source")


def test_the_longest_mate_distance_is_never_shown(play_page) -> None:
    """**最長殺著距離不得出現在對局介面上**(1.3 修訂後)。

    這一條取代了原本三條「顯示最長殺著」的測試(有值、空值、值為 0)。1.3 原本是
    「Where 題目帶有最長殺著距離, the 對局介面 shall 顯示該資訊」,使用者看過實際
    畫面後推翻:**那個數字是劇透** —— 它等於預告這題幾步殺得完,而排局練習的價值
    正在於自己找出殺法。

    斷言分三層,因為「不顯示」有三種漏法:

    1. 容器還在(只是沒填值)—— 直接查 `#puzzle-max-dtm` 的存在;
    2. 名目還在(「最長殺著 —」這種半殘的一行)—— 查那四個字;
    3. 值以別的形態畫到別處 —— 查整個側欄的可見文字裡沒有那個數字。

    夾具刻意把 `max_dtm` 換成 `97`:題目資訊裡的其他數字(「第 21 局」)不會誤觸,
    而**後端照樣回這個欄位** —— 這一條同時證明它到得了前端卻沒有被畫出來,而不是
    後端不給了。`service/models.py` 的 `PositionResponse` 一個字都沒改。
    """
    route_position(play_page, {**POSITION_RESPONSE, "max_dtm": 97})
    open_game(play_page)

    assert play_page.locator("#puzzle-max-dtm").count() == 0, "最長殺著那一格還在"

    sidebar = text_of(play_page, "#sidebar")
    assert "最長殺著" not in sidebar, f"側欄還留著「最長殺著」這個名目:{sidebar!r}"
    assert "97" not in sidebar, f"最長殺著距離仍畫在側欄上:{sidebar!r}"


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
    """當前輪方對使用者可辨識(8.4)。

    使用者執紅,故輪到自己時說的是「輪到你」而不是「紅方」—— 對使用者而言可辨識
    的是**該不該我動**,而不是自己執的顏色叫什麼。故此處不再找「紅」字,改為釘死
    整句;「黑」仍不得出現,那是下一條測的狀態。
    """
    open_game(play_page)

    turn = text_of(play_page, "#turn")
    assert turn == "輪到你", f"輪到自己時側欄說的是:{turn}"
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
    # 對局中的兩種說法(「輪到你」「黑方走棋」)都沒有「勝」字,故它就是終局的信號。
    play_page.wait_for_function(
        "() => document.getElementById('turn').textContent.includes('勝')"
    )

    turn = text_of(play_page, "#turn")
    assert turn == "你獲勝", f"使用者獲勝時側欄說的是:{turn}"


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
        "() => document.getElementById('turn').textContent.includes('勝')"
    )

    turn = text_of(play_page, "#turn")
    assert turn == "黑方勝", f"後端說黑方勝,側欄說的卻是:{turn}"
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
    """殺著倒數以**未然語氣與近似倒數**呈現(4.2)。

    後端在 250k 節點下可能高估 1 步,寫成確數等於把一個刻意接受的誤差說成精確值。

    讀數整句一併釘死:倒數與標籤之間是**全形空格**(形態取自 POC 的 `renderSignal`),
    而且讀數前面**沒有任何名目** —— 「參考信號:」那個前綴已在使用者看過畫面後移除,
    只驗子字串的話它跑回來也不會有人發現。

    **這一條現在也承擔已刪的 4.4。** 舊的 4.4 要求「使使用者能辨識信號是參考資訊
    而非勝負判決」,原本靠一句常駐註記承擔;它被刪除的理由是**讀數本身就說完了**
    ——「即將」是未然語氣、「約」明說了不精確、「N 步」講明還要走多久。但那個性質
    是載重的,不是修辭:把這一句改成「紅勝 4」就真的變成判決,而那正是 POC 用的說法。
    比對整句(而非子字串)是唯一擋得住那種退化的寫法。
    """
    open_game(play_page, [black_reply(signal="red_winning", mate_in=4)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    reading = text_of(play_page, ".signal-reading")
    assert reading == "即將取勝　約 4 步", f"信號讀數是:{reading}"


@pytest.mark.parametrize("mate_in", [0, 1])
def test_a_mate_countdown_of_zero_is_still_shown_while_the_game_goes_on(
    play_page, mate_in: int
) -> None:
    """**對局進行中,倒數為 0 時仍正常呈現**(4.2 修訂後)。

    JS 的 `if (mateIn)`、`mateIn || '—'` 對 0 都是假,倒數會被靜默吞掉,而
    engine-service 的 tasks 3.3 明文記著這個陷阱。此處鎖住的是「有值就呈現」,
    不是「非零才呈現」。

    **這一條原本用的是終局那一手(`FINAL_REPLY`),現在改用進行中的回應。**
    終局的信號已改為陳述結果、不再畫倒數(4.5),原本的寫法會與那條需求直接打架。
    但 falsy 陷阱**沒有因此消失,只是換了位置**:後端給倒數的條件是「引擎回報
    mate」而不是「對局還沒結束」,兩者不必同步 —— `over` 為假而 `mate_in` 為 0
    的回應照樣要畫出「約 0 步」。這正是 4.2 修訂後仍然保留該半句的理由。

    連 `mate_in: 1` 一起驗,是為了擋住另一種吞法:把倒數整段拿掉、或用一個下界
    把小數字濾掉,只驗 0 的話那些改動不見得會轉紅。
    """
    open_game(play_page, [black_reply(signal="red_winning", mate_in=mate_in)])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    reading = text_of(play_page, ".signal-reading")
    assert reading == f"即將取勝　約 {mate_in} 步", f"信號讀數是:{reading}"


def test_a_signal_without_a_countdown_shows_no_bogus_number(play_page) -> None:
    """沒有殺著倒數時不得憑空生一個數字。

    (原本這裡寫的是「與 `max_dtm` 的條件式呈現同理」。最長殺著距離已整組移除
    ——1.3 被推翻,見 `test_the_longest_mate_distance_is_never_shown` —— 那個類比
    因此沒有對象了,但這一條本身與它無關,照舊。)
    """
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
    # 這一手同時是終局,故信號說的是結果而非預測(4.5)。「已將死黑方」這四個字
    # **只有在信號帶著 mate 時說得出來** —— 信號若在最後一手被弄丟,這裡會退成
    # 「對局結束」,斷言照樣轉紅。
    assert text_of(play_page, ".signal-reading") == "已將死黑方"


# --- 終局的信號:陳述結果而非預測(4.5)---------------------------------
#
# 這一節整節是新的。終局之後信號那一行原本仍顯示 `即將取勝　約 0 步` —— 對局都
# 結束了還說「即將」,而「約 0 步」是在倒數一件已經發生的事。形態取自
# `poc/index.html` 的 `finish()`,POC 早就這樣做了。
#
# **三種說法各一條測試,第三種尤其不能少**:象棋規則下輪方無合法著法即負,那可能
# 是將死、也可能是困斃,而後端的 `state` 只回報**誰贏**、沒有回報**為什麼贏**。
# 「將死」二字唯一的依據是引擎自己報了 mate(`service/game.py` 的 `classify_score()`
# 只在分數型別為 `mate` 時才給 `red_winning` / `black_winning`)。信號為未知時前端
# **不得自己推斷**,只能說對局結束。


def test_a_red_win_is_stated_as_a_delivered_mate(play_page) -> None:
    """紅勝且引擎回報 mate -> `已將死黑方`(4.5)。

    整句比對而非子字串:退回「即將取勝　約 0 步」與多長出一段倒數,都只有比對
    整句才擋得下來。
    """
    open_game(play_page, [FINAL_REPLY])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    assert text_of(play_page, "#turn") == "你獲勝", "前置條件:這一手確實終局且紅方勝"
    reading = text_of(play_page, ".signal-reading")
    assert reading == "已將死黑方", f"終局的信號讀數是:{reading}"
    assert "即將" not in reading, f"對局已結束,信號仍用未然語氣:{reading}"
    assert not re.search(r"\d", reading), f"對局已結束,信號仍在倒數:{reading}"


def test_a_black_win_is_stated_as_a_mate_suffered(play_page) -> None:
    """黑勝且引擎回報 mate -> `紅方被將死`(4.5)。

    **`mate_in` 在這條路徑上是 `None`,而那是後端的契約**:`service/types.py` 寫著
    倒數「僅 `RED_WINNING` 時有值」。因此終局那句話**不得以倒數有沒有值為依據**
    ——「引擎報了 mate」這件事由信號值本身承擔,黑方即將取勝時一樣是 mate 信號。
    """
    open_game(
        play_page,
        [black_reply(move="e9d9", signal="black_winning", mate_in=None, over=True, winner="black")],
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert text_of(play_page, "#turn") == "黑方勝", "前置條件:這一手確實終局且黑方勝"
    reading = text_of(play_page, ".signal-reading")
    assert reading == "紅方被將死", f"終局的信號讀數是:{reading}"
    assert "即將" not in reading, f"對局已結束,信號仍用未然語氣:{reading}"


def test_a_win_without_a_mate_signal_is_not_called_a_mate(play_page) -> None:
    """**結束但信號不足以判斷是不是將死 -> `對局結束`**(4.5)。

    這是本節最重要的一條。引擎一分未報時 `classify_score()` 回的是 `unknown`,
    而對局仍可能因輪方無合法著法而結束 —— 那**可能是將死,也可能是困斃**。後端的
    `state` 說的只有「紅方贏了」,從它推出「所以是將死」就是前端自己在判勝負,正是
    steering 的不可違反約束(「不自實作勝負判定」)禁止的事。

    **一個只看 `state.winner` 就喊將死的實作會在這裡轉紅**,而其餘每一條終局測試
    都照樣是綠的 —— 這條測試存在的唯一理由就是釘住那個差別。
    """
    open_game(
        play_page,
        [black_reply(move=None, signal="unknown", mate_in=None, over=True, winner="red")],
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    assert text_of(play_page, "#turn") == "你獲勝", "前置條件:後端確實回報紅方勝"
    reading = text_of(play_page, ".signal-reading")
    assert reading == "對局結束", (
        f"引擎一分未報,信號卻說得出勝負的原委:{reading}"
    )
    assert "將死" not in reading, f"信號未報 mate,前端卻自己推斷出將死:{reading}"


def test_a_finished_game_without_a_winner_only_says_it_finished(play_page) -> None:
    """結束但後端沒給勝方 -> 同樣只說 `對局結束`(4.5)。

    「誰贏」只有 `state.winner` 一個來源(`#turn` 讀的也是它),沒有勝方就說不出
    是誰將死了誰。信號帶著 mate 也一樣 —— mate 信號給的是**理由**不是**方向**。
    """
    open_game(
        play_page,
        [black_reply(move=None, signal="red_winning", mate_in=0, over=True, winner=None)],
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    assert text_of(play_page, "#turn") == "對局結束", "前置條件:後端確實沒給勝方"
    assert text_of(play_page, ".signal-reading") == "對局結束"


def test_a_finished_game_draws_no_countdown_even_when_the_countdown_is_not_zero(
    play_page,
) -> None:
    """**`over` 為真而 `mate_in` 不是 0 時,照樣不畫倒數**(4.5)。

    `over` 與 `mate_in` 是兩個獨立欄位,實作**不得假設兩者同步**。真實引擎在黑方
    被將死時給的是 `score mate 0`,但那是引擎的行為而非協定的保證。

    這一條同時是一道防線:要是有人把「要不要畫倒數」的判斷從 `over` 改成「`mate_in`
    是不是 0」,那正是 4.2 那個 falsy 陷阱換個地方重來,而它會在這裡轉紅。
    """
    open_game(
        play_page,
        [black_reply(move=None, signal="red_winning", mate_in=5, over=True, winner="red")],
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_reply(play_page)

    reading = text_of(play_page, ".signal-reading")
    assert reading == "已將死黑方", f"終局的信號讀數是:{reading}"
    assert "5" not in reading, f"對局已結束,信號仍在倒數:{reading}"


def test_the_signal_is_presented_as_advisory_not_a_verdict(play_page) -> None:
    """信號在語意樹上標為註記,而且不讓對局停下(4.1、3.3)。

    **舊的 requirement 4.4 已刪除**(「使信號的呈現讓使用者能辨識它是參考資訊而非
    勝負判決」),其約束併入 4.2 —— 讀數本身就已經滿足它:「即將」是未然語氣、
    「約」明說了不精確、「N 步」講明還要走多久。曾經承擔它的常駐註記(「僅供參考,
    不是勝負判決;對局只在真終局結束。」,後縮為「僅供參考」)因此整行移除,這條
    測試不再驗任何註記文字。

    **但 `role` 那一份留著,而且依據改掛 4.1。** 它與已刪的 4.4 是分開的價值:
    `role="status"` 會讓螢幕閱讀器把信號當系統狀態**主動播報**,那在聽覺上像判決,
    與讀數的文字寫得多清楚無關 —— 語氣救不了播報方式。

    讀數的語氣改由 `test_the_mate_countdown_is_shown_as_an_approximation` 釘住
    (它比對整句而非子字串,所以「紅勝 15」這種退化會轉紅)。

    **註記已移除這件事本身也要有人守。** 那條測試只比對 `.signal-reading` 一整句,
    註記若以第二個節點的形式跑回來,它一個字都不會變 —— 換句話說「僅供參考」重新
    出現在畫面上是**沒有任何測試擋得下來**的退化。故此處反向釘住:信號區裡不得有
    註記節點、也不得出現那四個字。
    """
    open_game(play_page, [black_reply(signal="red_winning", mate_in=2, legal_moves=["f8f9"])])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    assert play_page.locator(".signal-note").count() == 0, "信號區又長出了註記節點"
    assert "僅供參考" not in text_of(play_page, "#signal"), (
        f"「僅供參考」又跑回信號區了:{text_of(play_page, '#signal')!r}"
    )
    role = play_page.locator("#signal").get_attribute("role")
    assert role == "note", f"#signal 的 role 是 {role!r},不是 note —— 信號被說成了判決"
    assert text_of(play_page, "#turn") == "輪到你", "信號為即將取勝不得讓對局提早結束"
    click_square(play_page, "f8")
    assert marked_squares(play_page) != set(), "信號為即將取勝不得讓盤面停下來"


def test_the_signal_goes_back_to_no_reading_after_a_reset(play_page) -> None:
    """重來後信號回到「尚未取得」—— 上一局的讀數不得留在畫面上(5.1、4.1)。"""
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


# --- 等待狀態(6.1、6.2、6.4)------------------------------------------
#
# 這一節原本靠**一個 `#waiting` 區塊的可見性**驗全部四條斷言。那個區塊已整條移除
# (它在版面流裡來去,顯示與隱藏都把底下的歷史著法上下推動),因此本節改成問那條
# 需求各自真正的內容:
#
# - 6.1(呈現等待)-> 等應手看 `#turn`,載入題目看 `#puzzle-title` 與盤面的佔位句。
# - 6.2(等待中不接受走子)-> 盤面整片不可選。這一條**過去在 DOM 層完全沒有測試**
#   (只有 `test_web_game.py` 在狀態機層驗),而拿掉可見的等待區塊之後,它就是
#   使用者唯一還看得出「系統正在忙」的行為,補上去。
# - 6.4(等待態必解除)-> **盤面重新接受走子**。這比「區塊有沒有收起來」更貼近
#   6.4 的字面(「不留在等待中無法操作」),而且它擋得住「`waiting` 忘了翻回假」
#   這個真正的缺陷 —— 那種缺陷下畫面看起來一切正常,只是再也走不動。


def test_the_turn_line_says_the_opponent_is_moving_while_the_engine_answers(
    play_page,
) -> None:
    """等應手期間由 `#turn` 呈現等待(6.1 修訂後)。

    「黑方走棋」本身就是「現在不是你動」—— 這正是原本那個「引擎思考中」方塊講的
    同一件事。斷言釘整句而不是「非空」:非空在 `#turn` 上永遠成立(它任何時候都
    有字),那樣的斷言驗不到任何東西。
    """
    open_game(play_page)
    hang_black_move(play_page)

    click_square(play_page, "d8")
    click_square(play_page, "d9")

    assert text_of(play_page, "#turn") == "黑方走棋", (
        f"等應手期間輪方那一行沒有說對手在走:{text_of(play_page, '#turn')!r}"
    )


def test_no_move_is_accepted_while_the_engine_answers(play_page) -> None:
    """等待後端回應期間不接受新的走子(6.2),而且在盤面上看得出來。

    `game.js` 的 `acceptsMoves` 為假時 `legalMoves` 是空集合,`board.js` 的
    「可選取」定義就是「該格有著法可出發」,於是整面盤子都選不起來。

    **斷言「一個子都不可選」而不是「某一格點不動」。** 等待中的 `currentState`
    還是走子**之前**那一份,它的 `legal_moves` 是舊局面的著法 —— 拿剛落子的那一格
    去試,舊清單裡本來就沒有它,守衛失效也照樣點不動(實測:那樣寫的版本擋不住
    「`legalMoves` 不套 `acceptsMoves`」這個突變)。`.selectable` 的總數則對整面盤
    負責,舊清單裡任何一格活過來都會被抓到。
    """
    open_game(play_page)
    hang_black_move(play_page)

    assert play_page.locator("#board svg .piece.selectable").count() > 0, (
        "前置條件:走子之前盤面本來就該有子可選"
    )

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    assert text_of(play_page, "#turn") == "黑方走棋", "前置條件:此刻應該正在等應手"

    assert play_page.locator("#board svg .piece.selectable").count() == 0, (
        "等應手期間盤面上仍有子可選 —— 等待中不得接受新的走子"
    )

    click_square(play_page, "f8")  # 起始局面的 `f8f9` 在等待中不得還能出發
    assert play_page.locator("#board svg .piece.selected").count() == 0, (
        "等應手期間仍選得起子"
    )
    assert marked_squares(play_page) == set(), "等應手期間盤面仍標出落點"


def test_the_puzzle_load_is_announced_by_the_title_and_the_board(play_page) -> None:
    """載入題目也是在等後端回應,同樣要有狀態(6.1)。

    這一種由**兩個本來就在的位置**承擔:頂上標題說「載入中…」,盤面的位置放一句
    「正在載入題目…」。兩者都不是新增的元素 —— `app.js` 的 `noPuzzleTitle` 與
    `boardPlaceholderText` 在移除 `#waiting` 之前就已經這樣寫了。
    """
    hang_position(play_page)
    visit(play_page)

    play_page.wait_for_function(
        "() => document.getElementById('puzzle-title').textContent.includes('載入中')"
    )
    assert "正在載入題目" in text_of(play_page, "#board"), (
        f"載入期間盤面的位置沒有說明狀態:{text_of(play_page, '#board')!r}"
    )


def test_the_board_accepts_moves_again_after_the_reply(play_page) -> None:
    """回應之後等待狀態解除(6.4)—— 判準是**盤面重新走得動**。"""
    open_game(play_page, [black_reply(move="e9d9")])

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    wait_for_moves(play_page, 2)

    click_square(play_page, "d9")  # `black_reply` 的預設 `legal_moves` 含 d9d8
    assert marked_squares(play_page) == {"d8"}, "回應之後盤面仍走不動,等於還停在等待中"


def test_the_board_accepts_moves_again_after_a_failure(play_page) -> None:
    """**失敗之後等待狀態也要解除**(6.4)—— 不得留在等待中無法操作。"""
    open_game(play_page)
    play_page.route(f"{ORIGIN}/api/black-move", lambda route: route.abort("failed"))

    click_square(play_page, "d8")
    click_square(play_page, "d9")
    play_page.wait_for_selector("#error:not([hidden])")

    # 失敗會把走法序列整份退回,盤面因此回到起始局面 —— 可走的仍是 `START_LEGAL`。
    click_square(play_page, "d8")
    assert marked_squares(play_page) == {"d9"}, "失敗之後盤面仍走不動,等於還停在等待中"


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
    """`app.js` 只往左依賴 `game.js` / `board.js` / `notation.js` / `fen.js` / `catalog.js`。

    `catalog.js` 是 problem-browser 的跨題導航加進來的:紅方獲勝之後要查出下一題是
    哪一題,而題目索引的唯一來源就是它(`loadCatalog()`,內含 `isListable()` 的
    可上架判準)。方向仍然是往左 —— `catalog.js` 與 `fen.js`、`api.js` 同層,**不
    依賴任何 web 模組、也不碰 DOM**,而且它連 `app.js` 的名字都不知道。

    這份清單是白名單而不是黑名單:多依賴一個模組必須是有人想過的決定,而不是隨手
    `import` 進來的結果。
    """
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    imported = re.findall(r"^\s*import[^\n]*?from\s*'([^']+)'", source, flags=re.MULTILINE)
    dynamic = re.findall(r"\bimport\s*\(\s*'([^']+)'", source)

    assert set(imported + dynamic) <= {
        "./game.js",
        "./board.js",
        "./notation.js",
        "./fen.js",
        "./catalog.js",
    }, f"app.js 的依賴超出組裝層可見的範圍:{imported + dynamic}"


def test_app_module_never_calls_fetch_itself() -> None:
    """後端往來一律經 `api.js`(`game.js` 轉手),組裝層不得自己發請求。"""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    assert "fetch(" not in code
