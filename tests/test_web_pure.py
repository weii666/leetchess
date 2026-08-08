"""純函式模組的驗證(design 的 Testing Strategy「純函式(`page.evaluate()`)」)。

`web/fen.js` 不碰 DOM 也不發請求,因此不必走完整的頁面流程 —— 把模組載進真實
瀏覽器、以 `page.evaluate()` 呼叫並把結果取回 Python 比對即可。這也順帶證明了
design 要求的依賴方向:`fen.js` 位於最左端,單獨載入就能運作,沒有任何其他
web 模組可以被它拉進來。

## 為什麼要架一個 http 來源,而不是 `file://`

Chromium 不允許從 `origin 'null'`(即 `file://`)匯入 ES module,一律以 CORS
擋下。前端以 ES modules 交付且無建置步驟(`tech.md`),所以測試必須讓模組在
一個真的 http(s) 來源下被載入。此處以 `page.route()` 就地供檔,不啟動任何伺服器
進程 —— 本輪要驗證的是模組本身,不是靜態檔掛載(那已由 `test_web_page.py` 覆蓋)。
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

#: 《適情雅趣》第 21 局的起始局面。紅方在下(rank 0),紅子為大寫。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: `PUZZLE_FEN` 逐格展開後應有的盤面。索引為 `[rank][file]`:rank 0 是紅方底線、
#: file 0 是 a 路(紅方視角由左至右),與 design 的座標約定一致。
PUZZLE_BOARD = [
    # rank 0:`4K4`
    [None, None, None, None, "K", None, None, None, None],
    # rank 1:`2rp2r2`
    [None, None, "r", "p", None, None, "r", None, None],
    # rank 2:`B3C1n1B`
    ["B", None, None, None, "C", None, "n", None, "B"],
    # rank 3:`3pP4`
    [None, None, None, "p", "P", None, None, None, None],
    # rank 4:`9`
    [None, None, None, None, None, None, None, None, None],
    # rank 5:`2b6`
    [None, None, "b", None, None, None, None, None, None],
    # rank 6:`6N2`
    [None, None, None, None, None, None, "N", None, None],
    # rank 7:`4b3N`
    [None, None, None, None, "b", None, None, None, "N"],
    # rank 8:`3RaR3`
    [None, None, None, "R", "a", "R", None, None, None],
    # rank 9:`3ak4`
    [None, None, None, "a", "k", None, None, None, None],
]


@pytest.fixture
def module_page(browser_page) -> Iterator:
    """一個位於 http 來源、可以 `import` `web/` 底下模組的空白分頁。

    供檔直接讀 `web/` 的真實檔案,所以測到的就是交付物本身,不是任何複本。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用 web/index.html:它會去載 app.js,而本檔要驗證的是
                # fen.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>純函式驗證</title>",
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


def with_fen(page, body: str):
    """把 `body` 當成函式本體執行,其中 `fen` 已綁定為 `web/fen.js` 的匯出。"""
    return page.evaluate(
        "async () => {\n  const fen = await import('/fen.js');\n" + body + "\n}"
    )


# --- parseFen:FEN 轉盤面陣列 -------------------------------------------


def test_parse_fen_expands_the_starting_position_square_by_square(module_page) -> None:
    """1.1:起始局面逐格解析正確 —— 整個盤面與預期完全相同。

    抽樣檢查會放過「某一列的空格數算錯導致整列右移」這類錯誤,因此此處比對全盤。
    """
    board = with_fen(module_page, f"  return fen.parseFen({PUZZLE_FEN!r});")

    assert board == PUZZLE_BOARD


def test_parse_fen_returns_ten_ranks_of_nine_files(module_page) -> None:
    """盤面陣列的形狀是 10 列 x 9 路,空格為 `null`。"""
    shape = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  return { ranks: b.length, files: b.map(row => row.length) };",
    )

    assert shape == {"ranks": 10, "files": [9] * 10}


def test_parse_fen_puts_red_on_the_bottom_rank(module_page) -> None:
    """1.5:紅方在下 —— 紅帥在 e0、黑將在 e9,而不是上下顛倒。"""
    kings = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  return { redKing: b[0][4], blackKing: b[9][4] };",
    )

    assert kings == {"redKing": "K", "blackKing": "k"}


def test_parse_fen_leaves_empty_squares_null(module_page) -> None:
    """數字代表的空格真的是空的 —— 整條河界(rank 4)無子,f9 也無子。"""
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  return { river: b[4], f9: b[9][5] };",
    )

    assert result == {"river": [None] * 9, "f9": None}


def test_parse_fen_ignores_the_fields_after_the_board(module_page) -> None:
    """只有第一段(盤面)參與解析;輪方與回合數不影響結果。"""
    board_only = PUZZLE_FEN.split(" ")[0]

    same = with_fen(
        module_page,
        f"  const withFields = fen.parseFen({PUZZLE_FEN!r});\n"
        f"  const boardOnly = fen.parseFen({board_only!r});\n"
        "  return JSON.stringify(withFields) === JSON.stringify(boardOnly);",
    )

    assert same is True


# --- applyMove:著法套用到盤面 ------------------------------------------


def test_apply_move_with_a_capture_replaces_the_captured_piece(module_page) -> None:
    """含吃子的著法:`d8d9` 紅俥吃黑士 —— 起點為空、終點是俥,被吃的士消失。"""
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  const before = b[9][3];\n"
        "  fen.applyMove(b, 'd8d9');\n"
        "  return { before, from: b[8][3], to: b[9][3],\n"
        "    advisors: b.flat().filter(piece => piece === 'a').length };",
    )

    assert result == {"before": "a", "from": None, "to": "R", "advisors": 1}


def test_apply_move_to_an_empty_square_leaves_the_origin_empty(module_page) -> None:
    """不吃子的著法:`f8f9` 之後起點為空、終點是該子,子力總數不變。"""
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  const count = board => board.flat().filter(Boolean).length;\n"
        "  const before = count(b);\n"
        "  fen.applyMove(b, 'f8f9');\n"
        "  return { from: b[8][5], to: b[9][5], before, after: count(b) };",
    )

    assert result == {"from": None, "to": "R", "before": 19, "after": 19}


def test_apply_move_touches_only_the_two_squares_of_the_move(module_page) -> None:
    """除了起點與終點,其餘 88 格必須原封不動。"""
    changed = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        f"  const original = fen.parseFen({PUZZLE_FEN!r});\n"
        "  fen.applyMove(b, 'd8d9');\n"
        "  const differing = [];\n"
        "  for (let rank = 0; rank < 10; rank++) {\n"
        "    for (let file = 0; file < 9; file++) {\n"
        "      if (b[rank][file] !== original[rank][file]) differing.push([rank, file]);\n"
        "    }\n"
        "  }\n"
        "  return differing;",
    )

    assert changed == [[8, 3], [9, 3]]


def test_apply_move_updates_the_board_it_is_given(module_page) -> None:
    """`applyMove` 就地更新傳入的盤面(POC 的既有契約),不另外回傳新盤面。

    `game.js` 會以走法序列自起始局面逐手推導盤面,就地更新正是那個用法。
    """
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  const returned = fen.applyMove(b, 'd8d9');\n"
        "  return { returned: returned === undefined, mutated: b[9][3] };",
    )

    assert result == {"returned": True, "mutated": "R"}


def test_apply_move_can_be_folded_over_a_move_sequence(module_page) -> None:
    """連續套用多手:走法序列是唯一真相,盤面由它逐手推導(design 的狀態模型)。"""
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  for (const move of ['d8d9', 'e9d9']) fen.applyMove(b, move);\n"
        "  return { d9: b[9][3], e9: b[9][4], d8: b[8][3] };",
    )

    # 俥吃士後被黑將吃回:d9 換成將,e9 空出來。
    assert result == {"d9": "k", "e9": None, "d8": None}


# --- 座標互轉 -----------------------------------------------------------


def test_square_and_coordinate_conversions_are_inverses(module_page) -> None:
    """`sq2fr` 與 `fr2sq` 對全部 90 格互為反函式。"""
    mismatches = with_fen(
        module_page,
        "  const bad = [];\n"
        "  for (let rank = 0; rank < 10; rank++) {\n"
        "    for (let file = 0; file < 9; file++) {\n"
        "      const square = fen.fr2sq(file, rank);\n"
        "      const [f, r] = fen.sq2fr(square);\n"
        "      if (f !== file || r !== rank) bad.push(square);\n"
        "    }\n"
        "  }\n"
        "  return bad;",
    )

    assert mismatches == []


def test_square_names_follow_the_a_to_i_and_zero_to_nine_convention(
    module_page,
) -> None:
    """a 路在紅方左手邊、rank 0 是紅方底線:a0 是左下角,i9 是右上角。"""
    result = with_fen(
        module_page,
        "  return { bottomLeft: fen.fr2sq(0, 0), topRight: fen.fr2sq(8, 9),\n"
        "    e0: fen.sq2fr('e0'), i9: fen.sq2fr('i9') };",
    )

    assert result == {
        "bottomLeft": "a0",
        "topRight": "i9",
        "e0": [4, 0],
        "i9": [8, 9],
    }


# --- 依賴方向 -----------------------------------------------------------


def test_fen_module_imports_no_other_web_module() -> None:
    """design 的依賴方向:`fen.js` 在最左端,不得 import 任何其他 web 模組。

    以原始碼斷言而非執行期行為 —— 一個「載入時不出錯」的 import 在瀏覽器裡是
    看不見的,但它一樣會把依賴方向弄反。
    """
    source = (WEB_DIR / "fen.js").read_text(encoding="utf-8")

    # 靜態 `import ... from '...'`、副作用式 `import '...'`,以及動態 `import(...)`。
    found = re.findall(r"^\s*import\b[^\n]*", source, flags=re.MULTILINE)
    found += re.findall(r"\bimport\s*\(", source)

    assert not found, f"fen.js 不得依賴任何其他模組,卻出現:{found}"
