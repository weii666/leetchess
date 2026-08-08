"""SVG 棋盤繪製的驗證(tasks 3.1、requirements 1.1、1.5)。

`web/board.js` 只接受資料並繪製,自身不記憶任何狀態(design 的「模組結構與依賴
方向」)。這使它可以像純函式一樣測:餵一份盤面陣列進去,看畫出來的 DOM。

夾具沿用 `test_web_pure.py` 的手法 —— 以 `page.route()` 就地供 `web/` 底下的**真實
交付檔**,合成一個 http(s) 來源。理由同樣是 Chromium 不允許自 `file://`(origin 為
`null`)匯入 ES module。此處不用 `web/index.html`:它會去載 `app.js`(tasks 4.3),
而本檔要驗證的是 `board.js` 能單獨運作;頁面只需要一個與骨架同 id 的盤面容器。
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-play-runtime.test"

#: 《適情雅趣》第 21 局(`positions/適情雅趣/0001.json`)的起始局面。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 空盤,用來證明重繪不會留下前一次的棋子。
EMPTY_FEN = "9/9/9/9/9/9/9/9/9/9 w - - 0 1"

#: `PUZZLE_FEN` 的全部 19 個子,以「格名 -> 中文名」列出;紅子另記於 `RED_SQUARES`。
#: 刻意逐格寫死而不在測試裡再解析一次 FEN —— 期望值要獨立於受測程式的推導方式。
EXPECTED_PIECES = {
    "d9": "士", "e9": "將",
    "d8": "俥", "e8": "士", "f8": "俥",
    "e7": "象", "i7": "傌",
    "g6": "傌",
    "c5": "象",
    "d3": "卒", "e3": "兵",
    "a2": "相", "e2": "炮", "g2": "馬", "i2": "相",
    "c1": "車", "d1": "卒", "g1": "車",
    "e0": "帥",
}

#: 上表中屬紅方(FEN 大寫)的格。
RED_SQUARES = {"d8", "f8", "i7", "g6", "e3", "a2", "e2", "i2", "e0"}


@pytest.fixture
def board_page(browser_page) -> Iterator:
    """一個位於 http 來源、備有盤面容器、可 `import` `web/` 模組的分頁。"""

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 容器的 id 與 `web/index.html` 的骨架一致,測到的介面才是真的那個。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>棋盤繪製驗證</title>"
                '<main id="board" aria-label="棋盤"></main>',
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


def draw(page, fen: str = PUZZLE_FEN) -> None:
    """把 `fen` 的局面畫進 `#board`,走的是 `board.js` 對外的唯一入口。"""
    page.evaluate(
        """async (fen) => {
          const { parseFen } = await import('/fen.js');
          const { renderBoard } = await import('/board.js');
          renderBoard(document.getElementById('board'), { board: parseFen(fen) });
        }""",
        fen,
    )


def drawn_pieces(page) -> list[dict]:
    """畫出來的每個子:圓心座標、字、紅或黑。"""
    return page.evaluate(
        """() => [...document.querySelectorAll('#board .piece')].map(piece => {
          const disc = piece.querySelector('circle');
          return {
            x: Number(disc.getAttribute('cx')),
            y: Number(disc.getAttribute('cy')),
            name: piece.textContent.trim(),
            red: piece.classList.contains('red'),
          };
        })"""
    )


# --- 座標約定:紅方底線 rank 0 在下、a 路在左(design 的座標約定)-----------

CELL = 62
MARGIN = 40


def x_of(file: int) -> float:
    return MARGIN + file * CELL


def y_of(rank: int) -> float:
    return MARGIN + (9 - rank) * CELL


def square_at(x: float, y: float) -> str:
    """把畫布座標換回格名,例如 `(288, 598)` -> `'e0'`。"""
    file = round((x - MARGIN) / CELL)
    rank = 9 - round((y - MARGIN) / CELL)
    return f"{chr(97 + file)}{rank}"


# --- 格線 ---------------------------------------------------------------


def grid_segments(page) -> list[str]:
    """棋盤格線的每一段子路徑,例如 `'M40 598 H536'`。"""
    path = page.locator("#board svg path.grid").get_attribute("d")
    return [segment.strip() for segment in re.findall(r"M[^M]*", path)]


def test_grid_has_ten_horizontal_lines_spanning_the_whole_board(board_page) -> None:
    """1.1:十條橫線,每條自 a 路貫穿到 i 路。"""
    draw(board_page)

    horizontals = [
        segment
        for segment in grid_segments(board_page)
        if re.fullmatch(r"M\d+ \d+ H\d+", segment)
    ]

    assert len(horizontals) == 10
    assert {tuple(map(int, re.findall(r"\d+", s))) for s in horizontals} == {
        (x_of(0), y_of(rank), x_of(8)) for rank in range(10)
    }


def test_grid_has_nine_files_broken_at_the_river(board_page) -> None:
    """1.1:九條縱線;最外兩條整條貫穿,中間七條在楚河漢界處斷開。"""
    draw(board_page)

    verticals = [
        tuple(map(int, re.findall(r"\d+", segment)))
        for segment in grid_segments(board_page)
        if re.fullmatch(r"M\d+ \d+ V\d+", segment)
    ]
    spans: dict[int, set] = {}
    for x, y_from, y_to in verticals:
        spans.setdefault(x, set()).add(tuple(sorted((y_from, y_to))))

    assert set(spans) == {x_of(file) for file in range(9)}, "九條縱線都要在"
    for file in (0, 8):
        assert spans[x_of(file)] == {(y_of(9), y_of(0))}, "邊線整條貫穿"
    for file in range(1, 8):
        assert spans[x_of(file)] == {
            (y_of(9), y_of(5)),
            (y_of(4), y_of(0)),
        }, "中間的縱線在河界斷開"


def test_grid_draws_both_palace_diagonals(board_page) -> None:
    """1.1:上下九宮各一個交叉,合計四條斜線。"""
    draw(board_page)

    diagonals = [
        segment
        for segment in grid_segments(board_page)
        if re.fullmatch(r"M\d+ \d+ L\d+ \d+", segment)
    ]

    assert len(diagonals) == 4


def test_river_is_labelled_between_the_two_middle_ranks(board_page) -> None:
    """1.1:楚河漢界寫在河界內,而不是壓在某一列的線上。"""
    draw(board_page)

    labels = board_page.evaluate(
        """() => [...document.querySelectorAll('#board svg text.river')].map(
          text => ({ text: text.textContent, y: Number(text.getAttribute('y')) })
        )"""
    )

    assert [label["text"] for label in labels] == ["楚河", "漢界"]
    for label in labels:
        assert y_of(5) < label["y"] < y_of(4)


# --- 子力配置 -----------------------------------------------------------


def test_every_piece_of_the_starting_position_is_drawn_on_its_square(
    board_page,
) -> None:
    """1.1:起始局面的 19 個子全部畫出,且每個都在自己的格上、字形正確。

    抽樣會放過「整列右移一路」這類錯誤,因此比對全盤而非只看幾個子。
    """
    draw(board_page)

    placed = {square_at(p["x"], p["y"]): p["name"] for p in drawn_pieces(board_page)}

    assert placed == EXPECTED_PIECES


def test_red_and_black_pieces_are_distinguishable(board_page) -> None:
    """1.5:紅子與黑子分屬不同類別,呈現上才可能有別。"""
    draw(board_page)

    reds = {
        square_at(p["x"], p["y"]) for p in drawn_pieces(board_page) if p["red"]
    }

    assert reds == RED_SQUARES


def test_red_side_is_rendered_at_the_bottom(board_page) -> None:
    """1.5:視覺上紅方在下 —— 紅帥實際畫在黑將的下方,同一條中線上。

    以真實的版面座標判斷,而不是回頭讀繪製時寫下的屬性:座標算對但畫反了
    (例如 rank 上下顛倒)在屬性比對下看不出來。
    """
    draw(board_page)

    kings = board_page.evaluate(
        """() => {
          const at = (name) => [...document.querySelectorAll('#board .piece')]
            .find(piece => piece.textContent.trim() === name)
            .getBoundingClientRect();
          const red = at('帥'), black = at('將');
          return {
            redY: red.top + red.height / 2,
            blackY: black.top + black.height / 2,
            redX: red.left + red.width / 2,
            blackX: black.left + black.width / 2,
          };
        }"""
    )

    assert kings["redY"] > kings["blackY"], "紅帥必須在黑將下方"
    assert abs(kings["redX"] - kings["blackX"]) < 1, "兩者都在 e 路,應同一條中線"


# --- 不記憶狀態 ---------------------------------------------------------


def test_redrawing_the_same_position_does_not_accumulate_pieces(board_page) -> None:
    """同一容器連續繪製兩次,棋子數不得翻倍。

    這是「自身不記憶任何狀態」最直接的可觀察後果:每次繪製都從給進來的資料
    重建全盤,而不是在上一次的結果上疊加。
    """
    draw(board_page)
    draw(board_page)

    assert len(drawn_pieces(board_page)) == len(EXPECTED_PIECES)
    assert board_page.locator("#board svg").count() == 1


def test_redrawing_an_empty_position_leaves_no_pieces_behind(board_page) -> None:
    """畫面只反映最後一次傳入的資料 —— 換成空盤後,先前的子一個都不留。"""
    draw(board_page)
    draw(board_page, EMPTY_FEN)

    assert drawn_pieces(board_page) == []
    assert grid_segments(board_page), "格線仍在,消失的只有棋子"


def test_two_containers_are_drawn_independently(board_page) -> None:
    """繪製的目標由呼叫端指定,模組不綁定任何特定容器,也不共用狀態。"""
    counts = board_page.evaluate(
        """async (fen) => {
          const { parseFen } = await import('/fen.js');
          const { renderBoard } = await import('/board.js');
          const second = document.createElement('div');
          document.body.append(second);
          renderBoard(document.getElementById('board'), { board: parseFen(fen) });
          renderBoard(second, { board: parseFen(fen) });
          return {
            first: document.querySelectorAll('#board .piece').length,
            second: second.querySelectorAll('.piece').length,
          };
        }""",
        PUZZLE_FEN,
    )

    assert counts == {"first": len(EXPECTED_PIECES), "second": len(EXPECTED_PIECES)}


# --- 依賴方向 -----------------------------------------------------------


def test_board_module_only_depends_on_fen(board_page) -> None:
    """design 的依賴方向:`board.js` 只能向左依賴 `fen.js`。

    其餘模組(`api.js`、`game.js`、`app.js`)此刻都還不存在,若 `board.js` 匯入了
    它們,上面每一個測試都會直接爆掉;此處把這條規則寫成明確的斷言。
    """
    imports = re.findall(
        r"^\s*import[^'\"]*['\"]([^'\"]+)['\"]",
        (WEB_DIR / "board.js").read_text(encoding="utf-8"),
        re.MULTILINE,
    )

    assert imports == ["./fen.js"]


def test_board_does_not_redefine_the_shared_piece_names() -> None:
    """`NAMES` 必須自 `fen.js` 匯入,不得在本模組自行定義。

    只斷言 import 路徑集合是不夠的:仍從 `fen.js` 取 `FILES`/`RANKS`、卻另外
    在檔內補一份 `const NAMES = {...}`,上一個測試照樣會通過。而那正是
    tasks.md 要防的漂移場景 —— 棋子中文名一旦有兩份,`fen.js` 日後改字時
    棋盤不會跟著改,測試也不會變紅(本檔的期望值是寫死的中文字)。
    """
    source = (WEB_DIR / "board.js").read_text(encoding="utf-8")

    assert re.search(r"^\s*const\s+NAMES\s*=", source, re.MULTILINE) is None, (
        "NAMES 必須自 fen.js 匯入,不得在 board.js 自行定義"
    )
    assert "NAMES" in source, "board.js 應使用自 fen.js 匯入的 NAMES"
