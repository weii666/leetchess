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
                # 刻意不用 web/play.html:它會去載 app.js,而本檔要驗證的是
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


def test_parse_fen_returns_ten_ranks_of_nine_files(module_page) -> None:
    """盤面陣列的形狀是 10 列 x 9 路,空格為 `null`。"""
    shape = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  return { ranks: b.length, files: b.map(row => row.length) };",
    )

    assert shape == {"ranks": 10, "files": [9] * 10}


def test_parse_fen_leaves_empty_squares_null(module_page) -> None:
    """數字代表的空格真的是空的 —— 整條河界(rank 4)無子,f9 也無子。"""
    result = with_fen(
        module_page,
        f"  const b = fen.parseFen({PUZZLE_FEN!r});\n"
        "  return { river: b[4], f9: b[9][5] };",
    )

    assert result == {"river": [None] * 9, "f9": None}


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

#: 同名子同縱線的局面。這裡刻意把每一種同線情形都擺出來:
#:
#: (本註解原本聲稱「《適情雅趣》第 21 局全程沒有同名子共線」,經 5.2 的端到端
#: 實測推翻:起始 FEN 的 d1/d3 就是一對黑卒共線,實戰中亦出現 g6/g8 雙傌,記譜
#: 為「前傌進五」。前/後分支確實會被走到。但補這些案例的理由不變 —— 靠一局棋
#: 恰好走到的路徑當覆蓋率是不可靠的。)
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
