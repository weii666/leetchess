"""SVG 棋盤繪製與選子互動的驗證(tasks 3.1、3.2;requirements 1.1、1.5、2.1、2.3、2.4)。

`web/board.js` 只接受資料並繪製,自身不記憶任何狀態(design 的「模組結構與依賴
方向」)。這使它可以像純函式一樣測:餵一份盤面陣列進去,看畫出來的 DOM。

夾具沿用 `test_web_pure.py` 的手法 —— 以 `page.route()` 就地供 `web/` 底下的**真實
交付檔**,合成一個 http(s) 來源。理由同樣是 Chromium 不允許自 `file://`(origin 為
`null`)匯入 ES module。此處不用 `web/play.html`:它會去載 `app.js`(tasks 4.3),
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

#: 一組合法著法,扮演「後端給的 `legal_moves`」。三枚子各有著法,其中 `d8d9`
#: 的終點有黑士(吃子)。內容是什麼由外部決定,`board.js` 不參與判斷。
LEGAL_MOVES = ["d8d9", "d8c8", "d8d7", "f8f9", "e2e1"]


@pytest.fixture
def board_page(browser_page) -> Iterator:
    """一個位於 http 來源、備有盤面容器、可 `import` `web/` 模組的分頁。"""

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 容器的 id 與 `web/play.html` 的骨架一致,測到的介面才是真的那個。
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


def draw(
    page,
    fen: str = PUZZLE_FEN,
    *,
    legal_moves: list[str] | None = None,
    selected: str | None = None,
    last_move: str | None = None,
) -> None:
    """把 `fen` 的局面畫進 `#board`,走的是 `board.js` 對外的唯一入口。

    合法落點與選中格都是**傳進去的資料**;兩個回呼把互動往外通知,呼叫記錄留在
    `window.calls` 供 `reported()` 取回。每次繪製都重設記錄。
    """
    page.evaluate(
        """async ({ fen, legalMoves, selected, lastMove }) => {
          const { parseFen } = await import('/fen.js');
          const { renderBoard } = await import('/board.js');
          window.calls = [];
          renderBoard(document.getElementById('board'), {
            board: parseFen(fen),
            legalMoves,
            selected,
            lastMove,
            onSelect: (square) => window.calls.push(['select', square]),
            onMove: (uci) => window.calls.push(['move', uci]),
          });
        }""",
        {"fen": fen, "legalMoves": legal_moves or [], "selected": selected, "lastMove": last_move},
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


def test_red_and_black_pieces_are_distinguishable(board_page) -> None:
    """1.5:紅子與黑子分屬不同類別,呈現上才可能有別。"""
    draw(board_page)

    reds = {
        square_at(p["x"], p["y"]) for p in drawn_pieces(board_page) if p["red"]
    }

    assert reds == RED_SQUARES


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


# --- 選子與合法落點 -----------------------------------------------------


def click_square(page, square: str) -> None:
    """點在 `square` 這一格的正中央。

    刻意不用選擇器挑元素,而是對盤面的實際座標按下去 —— 命中誰由瀏覽器的
    hit test 決定。這樣「點空白處」「點被落點標示蓋住的子」才測得到真的行為,
    而不是測到我們自己寫上去的識別屬性。
    """
    file, rank = ord(square[0]) - 97, int(square[1])
    page.locator("#board svg").click(position={"x": x_of(file), "y": y_of(rank)})


def markers(page) -> list[dict]:
    """畫出來的每個落點標示:所在格、是否為吃子。"""
    dots = page.evaluate(
        """() => [...document.querySelectorAll('#board .dot')].map(dot => ({
          x: Number(dot.getAttribute('cx')),
          y: Number(dot.getAttribute('cy')),
          capture: dot.classList.contains('capture'),
        }))"""
    )
    return [
        {"square": square_at(dot["x"], dot["y"]), "capture": dot["capture"]}
        for dot in dots
    ]


def marked_squares(page) -> set[str]:
    return {marker["square"] for marker in markers(page)}


def reported(page) -> list[list]:
    """自繪製起,`board.js` 經回呼往外通知過的每一件事。"""
    return page.evaluate("() => window.calls")


def test_destination_markers_are_actually_visible(board_page) -> None:
    """2.1:標示要看得見 —— 元素存在但沒有尺寸或沒有顏色等於沒標。"""
    draw(board_page, legal_moves=LEGAL_MOVES, selected="d8")

    dots = board_page.locator("#board .dot")
    assert dots.count() == 3
    for index in range(dots.count()):
        dot = dots.nth(index)
        dot.wait_for(state="visible")
        box = dot.bounding_box()
        assert box["width"] > 0 and box["height"] > 0
        painted = board_page.evaluate(
            """(element) => {
              const style = getComputedStyle(element);
              return { fill: style.fill, stroke: style.stroke };
            }""",
            dot.element_handle(),
        )
        assert painted["fill"] != "none" or painted["stroke"] != "none"


def test_markers_come_verbatim_from_the_given_moves(board_page) -> None:
    """2.4:落點一律照抄傳進來的資料,`board.js` 不判斷任何棋規。

    給紅帥三個橫跨全盤、任何象棋規則都不會允許的落點。若模組心裡藏著哪怕
    一條「帥只能在九宮內走一格」的知識,這裡就會少標。
    """
    absurd = ["e0a9", "e0i0", "e0e5"]

    draw(board_page, legal_moves=absurd, selected="e0")

    assert marked_squares(board_page) == {"a9", "i0", "e5"}


def test_the_selected_piece_is_marked_out_from_the_others(board_page) -> None:
    """2.1:選中的子在盤面上看得出是被選中的那個。

    標示必須是自繪的 SVG 圖形:`.piece` 是 `<g>`,POC 用的 `box-shadow` 在
    SVG 元素上一律無效(tasks 3.1 的交付事實)。
    """
    draw(board_page, legal_moves=LEGAL_MOVES, selected="d8")

    selected = board_page.locator("#board .piece.selected")
    assert selected.count() == 1

    ring = board_page.evaluate(
        """() => {
          const piece = document.querySelector('#board .piece.selected');
          const disc = piece.querySelector('circle.piece-disc');
          const extra = [...piece.querySelectorAll('circle')]
            .find(circle => circle !== disc);
          if (!extra) return null;
          const style = getComputedStyle(extra);
          return {
            biggerThanDisc:
              Number(extra.getAttribute('r')) > Number(disc.getAttribute('r')),
            painted: style.stroke !== 'none' && parseFloat(style.strokeWidth) > 0,
            centre: [
              Number(extra.getAttribute('cx')),
              Number(extra.getAttribute('cy')),
            ],
          };
        }"""
    )

    assert ring is not None, "選中標示必須自繪,不能靠 CSS box-shadow"
    assert ring["biggerThanDisc"], "標示要圈在子的外圍才看得見"
    assert ring["painted"]
    assert square_at(*ring["centre"]) == "d8"


def test_clicking_the_selected_piece_again_reports_a_cleared_selection(
    board_page,
) -> None:
    """POC `selectPiece` 的切換行為:再點一次已選中的子等於取消選取。

    結果一樣是往外通知(這次是「沒有選中」),而不是自己改畫面。
    """
    draw(board_page, legal_moves=LEGAL_MOVES, selected="d8")

    click_square(board_page, "d8")

    assert reported(board_page) == [["select", None]]


def test_clicking_a_marked_destination_reports_that_move(board_page) -> None:
    """2.1 的下一步:點在標示的落點上,往外通知的是完整的 UCI 著法。

    盤面**不會**自己更新 —— 走法序列是 `game.js` 的唯一真相,`board.js` 只回報。
    """
    draw(board_page, legal_moves=LEGAL_MOVES, selected="d8")
    before = drawn_pieces(board_page)

    click_square(board_page, "c8")

    assert reported(board_page) == [["move", "d8c8"]]
    assert drawn_pieces(board_page) == before


def test_clicking_a_piece_without_legal_moves_reports_nothing(board_page) -> None:
    """2.3:點選沒有合法著法的子(對方的子即屬此類)不改變盤面、不觸發任何動作。

    「是不是己方的子」同樣不由 `board.js` 判斷 —— 後端給的合法著法只會自輪方的
    子出發,「有著法可走」因此就是可選取的完整定義(2.4)。
    """
    draw(board_page, legal_moves=LEGAL_MOVES)
    before = drawn_pieces(board_page)

    click_square(board_page, "e9")  # 黑將
    click_square(board_page, "e3")  # 紅兵,但這組著法裡沒有它的

    assert reported(board_page) == []
    assert drawn_pieces(board_page) == before
    assert markers(board_page) == []


def test_clicking_the_margin_outside_the_grid_reports_nothing(board_page) -> None:
    """2.3:盤面四周的留白帶也是使用者點得到的區域,同樣不得送出任何東西。

    這一條不能靠 `click_square` 涵蓋 —— 它只映射 90 個格點,而格點上蓋的是
    `path.grid`。留白帶上 `elementFromPoint` 回傳的是底層的 `rect.board-bg`,
    是另一條 hit-testing 路徑:在那個元素上掛 click 的話,使用者點棋盤邊緣就會
    觸發回呼,而格點測試一個都抓不到。
    """
    draw(board_page, legal_moves=LEGAL_MOVES, selected="d8")
    before = drawn_pieces(board_page)

    svg = board_page.locator("#board svg")
    for x, y in ((5, 5), (300, 633), (570, 300), (20, 320)):
        svg.click(position={"x": x, "y": y})

    assert reported(board_page) == []
    assert drawn_pieces(board_page) == before


# --- 上一手終點標示 -------------------------------------------------------


def piece_stroke_widths(page) -> dict[str, float]:
    """每顆子的邊框寬度:格名 -> `stroke-width`。"""
    discs = page.evaluate(
        """() => [...document.querySelectorAll('#board .piece')].map(piece => {
          const disc = piece.querySelector('circle.piece-disc');
          return {
            x: Number(disc.getAttribute('cx')),
            y: Number(disc.getAttribute('cy')),
            strokeWidth: Number(disc.getAttribute('stroke-width')),
          };
        })"""
    )
    return {square_at(disc["x"], disc["y"]): disc["strokeWidth"] for disc in discs}


def test_last_move_thickens_only_the_destination_pieces_border(board_page) -> None:
    """黑方剛落子的那顆子邊框加粗成 4(與選取/懸停紅子同一種回饋);起點的子不
    加粗邊框,附近其餘子都維持預設的 2。起點另外會有一個獨立的灰色圓點,見
    `test_last_move_marks_the_origin_with_a_dot_the_same_size_as_a_destination`。
    """
    draw(board_page, last_move="e9d9")

    widths = piece_stroke_widths(board_page)
    assert widths["d9"] == 4
    assert widths["e9"] == 2, "起點的子不加粗"
    assert set(widths.values()) == {2, 4}, "只有終點那一顆子的邊框變了"


def test_last_move_marks_the_origin_with_a_dot_the_same_size_as_a_destination(
    board_page,
) -> None:
    """起點畫一個灰色圓點,大小與合法落點提示相同(同一組「格位標示」語彙),
    但顏色是灰的、不是落點提示的藍色,而且點下去不會觸發任何回呼。
    """
    draw(board_page, last_move="e9d9")

    origins = board_page.locator("#board .last-move-origin")
    assert origins.count() == 1

    origin = origins.first
    assert square_at(
        float(origin.get_attribute("cx")), float(origin.get_attribute("cy"))
    ) == "e9"
    assert float(origin.get_attribute("r")) == 11, "跟合法落點提示同尺寸"

    fill = board_page.evaluate(
        "(element) => getComputedStyle(element).fill", origin.element_handle()
    )
    assert fill != "rgba(46, 134, 222, 0.55)", "灰色,不是落點提示的藍色"

    click_square(board_page, "e9")
    assert reported(board_page) == [], "起點的點不可互動"


def test_no_last_move_leaves_every_piece_at_the_default_border(board_page) -> None:
    """沒有上一手(預設 `lastMove=None`)時,沒有任何子的邊框被加粗,也不會畫出
    起點的圓點。
    """
    draw(board_page)

    assert set(piece_stroke_widths(board_page).values()) == {2}
    assert board_page.locator("#board .last-move-origin").count() == 0


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
