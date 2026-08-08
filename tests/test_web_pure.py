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

import json
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


# --- uci2cn:UCI 轉中文記譜 --------------------------------------------

#: 同名子同縱線的局面。POC 的前/後判別**從未被跑到過** —— 《適情雅趣》第 21 局
#: 全程沒有任何一對同名子共線 —— 所以這裡刻意把每一種同線情形都擺出來:
#:
#: - c 路:紅俥 c0/c3 與黑車 c6/c9。同一縱線上四個「車」,但紅黑必須各算各的
#: - g 路:紅傌 g0/g4 與黑馬 g5/g9。斜行子的同線判別
#: - e 路:紅兵 e3/e5/e6,外加 e0 的紅帥。三子用前/中/後,且帥不得被算進兵裡
#: - d 路:黑卒 d3/d4/d6
#:
#: 局面不必是實戰會出現的局面 —— `uci2cn` 是盤面的純函式,不做合法性判斷。
STACKED_FEN = "2r1k1n2/9/9/2rpP4/4P1n2/3p2N2/2RpP4/9/9/2R1K1N2 w - - 0 1"

#: 單子局面:每個兵種各一,用來驗證縱線序號、進退平,以及斜行子接目標縱線。
#: 紅方 a0 俥、c0 相、d0 仕、e0 帥、b2 炮、e3 兵;
#: 黑方 a9 車、c9 象、d9 士、e9 將、h7 包、e6 卒。
SOLO_FEN = "r1bak4/9/7c1/4p4/9/9/4P4/1C7/9/R1BAK4 w - - 0 1"

#: 同一縱線上四個紅兵(e3–e6)。三子以上才會用到序號式的頭銜。
FOUR_PAWNS_FEN = "4k4/9/9/4P4/4P4/4P4/4P4/9/9/4K4 w - - 0 1"


def with_notation(page, body: str):
    """把 `body` 當成函式本體執行,其中 `fen`、`notation` 已綁定為對應模組的匯出。"""
    return page.evaluate(
        "async () => {\n"
        "  const fen = await import('/fen.js');\n"
        "  const notation = await import('/notation.js');\n" + body + "\n}"
    )


def cn_for(page, fen_string: str, moves: list[str]) -> list[str]:
    """取得每手著法在 `fen_string` 盤面上的中文記譜。

    所有著法都對**同一個未變動的盤面**求值(記譜是走子前盤面的函式),因此順序
    之間彼此獨立 —— 這同時也會抓出 `uci2cn` 偷改盤面的情形。
    """
    return with_notation(
        page,
        f"  const b = fen.parseFen({fen_string!r});\n"
        f"  return {json.dumps(moves)}.map(uci => notation.uci2cn(b, uci));",
    )


def test_notation_uses_file_numbers_and_piece_names_for_a_lone_piece(
    module_page,
) -> None:
    """8.1:單子記譜為「兵種 + 縱線序號」。

    紅方縱線以漢字由**紅方右手邊**起算(i 路為一、a 路為九),黑方以阿拉伯數字由
    **黑方右手邊**起算(a 路為 1、i 路為 9)。兩者方向相反正是最容易寫反的地方。
    """
    assert cn_for(
        module_page,
        SOLO_FEN,
        ["a0a4", "b2b7", "e3e4", "e0e1", "a9a5", "h7h9", "e6e5", "e9e8"],
    ) == [
        "俥九進四",
        "炮八進五",
        "兵五進一",
        "帥五進一",
        "車1進4",
        "包8退2",
        "卒5進1",
        "將5進1",
    ]


def test_notation_writes_forward_backward_and_sideways_moves(module_page) -> None:
    """8.1:進 / 退 / 平三種走向。

    改變縱線為「平」,後接目標縱線序號;同縱線則以移動格數計 —— 紅方向 rank 大
    的方向為進,黑方相反。`h7h9` 是黑包往自己底線退,方向判別若寫死成「rank 變大
    就是進」,這一手會變成「進」。
    """
    assert cn_for(
        module_page,
        SOLO_FEN,
        ["e3e4", "b2b0", "a0b0", "e0d0", "e6e5", "h7h9", "e6d6", "e9d9"],
    ) == [
        # 紅:進、退、平
        "兵五進一",
        "炮八退二",
        "俥九平八",
        "帥五平六",
        # 黑:進、退、平
        "卒5進1",
        "包8退2",
        "卒5平4",
        "將5平4",
    ]


def test_notation_uses_the_target_file_for_pieces_that_move_diagonally(
    module_page,
) -> None:
    """8.1:馬、相/象、仕/士 走斜線,「進/退」後面接**目標縱線序號**而非格數。

    傌從 g0 走到 h2 只跨一路,若誤用格數會寫成「傌三進二」中的「二」剛好對上,
    因此這裡挑的是格數與縱線序號不相等的走法。
    """
    assert cn_for(
        module_page,
        SOLO_FEN,
        ["c0e2", "d0e1", "c9e7", "d9e8"],
    ) == ["相七進五", "仕六進五", "象3進5", "士4進5"]


def test_notation_distinguishes_two_rooks_on_the_same_file(module_page) -> None:
    """8.1:同線雙車 —— 改以「前」「後」取代縱線序號。

    紅方越靠近自己底線(rank 小)為「後」,黑方相反。c 路上紅黑各有兩子,實作若
    沒有比對顏色就會四子混算,前後全錯。
    """
    assert cn_for(
        module_page,
        STACKED_FEN,
        ["c3c5", "c0c1", "c3c1", "c0b0", "c6c5", "c9c8", "c6c7", "c9b9"],
    ) == [
        "前俥進二",
        "後俥進一",
        "前俥退二",
        "後俥平八",
        "前車進1",
        "後車進1",
        "前車退1",
        "後車平2",
    ]


def test_notation_distinguishes_two_knights_on_the_same_file(module_page) -> None:
    """8.1:同線雙馬 —— 前/後 與「接目標縱線」兩條規則必須同時成立。

    g 路上紅傌 g0/g4、黑馬 g5/g9,四子同線而紅黑分算。
    """
    assert cn_for(
        module_page,
        STACKED_FEN,
        ["g4f6", "g0h2", "g4h2", "g5f7", "g9h7", "g5h3"],
    ) == ["前傌進四", "後傌進二", "前傌退二", "前馬退6", "後馬進8", "前馬進8"]


def test_notation_uses_front_middle_back_for_three_pawns_on_a_file(
    module_page,
) -> None:
    """8.1:同線兵 —— 三子用「前」「中」「後」。

    e 路上除了三個紅兵還有一個紅帥,兵種若沒比對就會把帥算成第四個兵。
    """
    assert cn_for(
        module_page,
        STACKED_FEN,
        ["e6e7", "e5d5", "e3e4", "d3d2", "d4c4", "d6d5"],
    ) == ["前兵進一", "中兵平六", "後兵進一", "前卒進1", "中卒平3", "後卒進1"]


def test_notation_numbers_four_or_more_pieces_on_a_file_from_the_front(
    module_page,
) -> None:
    """8.1:同線四子以上改用序號(自前向後為一、二、三、四),不再用前/中/後。"""
    assert cn_for(
        module_page,
        FOUR_PAWNS_FEN,
        ["e6e7", "e5e6", "e4e5", "e3d3"],
    ) == ["一兵進一", "二兵進一", "三兵進一", "四兵平六"]


def test_notation_also_uses_front_and_back_for_advisors_and_elephants(
    module_page,
) -> None:
    """同線的仕/相也走前/後 —— 記錄現行行為,因為這一條有兩種寫法。

    「同一縱線上有兩個以上同名子改用前/後」是通則,POC 對所有兵種一體適用,移植
    時照舊。但仕與相從同一縱線出發時,「進/退」本身就已足以區分(c0 與 c4 的相都
    走 e2,一個是進、一個是退),所以實戰棋譜多半仍寫「相七進五 / 相七退五」。

    兩種寫法都不會產生歧義,現階段不改;若日後要對齊棋譜慣例,改的是這裡。
    """
    # 紅相 c0/c4、紅仕 d0/d2 —— 這是仕與相唯一能共線的擺法。
    fen_string = "4k4/9/9/9/9/2B6/9/3A5/9/2BAK4 w - - 0 1"

    assert cn_for(
        module_page,
        fen_string,
        ["c4e2", "c0e2", "d2e1", "d0e1"],
    ) == ["前相退五", "後相進五", "前仕退五", "後仕進五"]


def test_notation_leaves_the_board_untouched(module_page) -> None:
    """記譜是**走子前**盤面的函式:`uci2cn` 不得改動傳入的盤面。

    呼叫端的用法是「先記譜、再 `applyMove`」,若此處偷改盤面,歷史著法會從第二手
    起全部錯位。
    """
    unchanged = with_notation(
        module_page,
        f"  const b = fen.parseFen({STACKED_FEN!r});\n"
        f"  const original = JSON.stringify(fen.parseFen({STACKED_FEN!r}));\n"
        "  notation.uci2cn(b, 'c3c5');\n"
        "  return JSON.stringify(b) === original;",
    )

    assert unchanged is True


def test_notation_reads_piece_names_from_the_fen_module(module_page) -> None:
    """棋子代碼到中文名的對照表由 `fen.js` 提供(棋子代碼本來就是 FEN 的一部分),
    `notation.js` 與日後的 `board.js` 共用同一份,不各自複製一份而漂移。
    """
    names = with_notation(module_page, "  return fen.NAMES;")

    assert names == {
        "R": "俥",
        "N": "傌",
        "B": "相",
        "A": "仕",
        "C": "炮",
        "P": "兵",
        "K": "帥",
        "r": "車",
        "n": "馬",
        "b": "象",
        "a": "士",
        "c": "包",
        "p": "卒",
        "k": "將",
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


def test_notation_module_imports_only_the_fen_module() -> None:
    """design 的依賴方向:`notation.js` 只能向左依賴 `fen.js`。

    一旦它 import 了 `board.js` / `game.js`,純函式的地位就沒了 —— 也就不可能再用
    `page.evaluate()` 單獨驗證。
    """
    source = (WEB_DIR / "notation.js").read_text(encoding="utf-8")

    found = re.findall(r"^\s*import\b[^\n]*", source, flags=re.MULTILINE)
    found += re.findall(r"\bimport\s*\(", source)
    illegal = [line for line in found if "'./fen.js'" not in line]

    assert not illegal, f"notation.js 只能 import ./fen.js,卻出現:{illegal}"


def test_notation_module_touches_neither_dom_nor_network() -> None:
    """`notation.js` 是純函式:不碰 DOM、不發請求、不持有可變狀態。"""
    source = (WEB_DIR / "notation.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    forbidden = [
        token
        for token in ("document", "window", "fetch", "XMLHttpRequest", "localStorage")
        if token in code
    ]

    assert not forbidden, f"notation.js 不得使用:{forbidden}"
