"""端到端:對真實服務走完一整局(tasks 5.2;requirements 3.2、3.3、3.4、4.2、8.1)。

前面每一條前端測試都以 `page.route()` 攔下後端 —— 逾時、斷線、503、「這一手就結束
對局」這些形狀,真實引擎不會照劇本演。代價是:被攔掉的那一段從來沒有被驗過。

**本檔一個攔截都不裝。** 它啟一個真的 `uvicorn service.main:app`(真引擎池、真
Pikafish、真題庫),用瀏覽器把《適情雅趣》第 21 局從起始局面走到紅勝。這是唯一
能證明整條路徑確實接得起來的測試:

    瀏覽器點擊 -> app.js -> game.js -> api.js -> HTTP -> main.py -> GameService
              -> EnginePool -> Pikafish -> 回應 -> 快照 -> 重畫

## 紅方的著法為什麼是當場算的,不是寫死的

黑方應手來自**服務那一個**引擎進程,而寫死的著法序列只有在服務每次都回同一手時
才成立 —— 那取決於該進程的雜湊表狀態,不是契約。改成每輪拿一個本地引擎問「此刻
紅方的最佳著法」,黑方走什麼都能接得下去:引擎自己會沿著殺法走完。這也是
`tests/test_game_service.py::test_real_engine_plays_the_puzzle_to_a_true_end_with_red_winning`
的做法,差別只在那裡直接呼叫服務層,這裡整條路徑都經過瀏覽器。

## 三件只有這裡驗得到的事

1. **只在真終局停局**(3.3)—— 引擎自第一手起就回報 mate,信號一路顯示「即將
   取勝」,但每一個中途局面都必須仍然選得到子、標得出落點、走得出下一手。
2. **最後一手**(3.2、3.4、4.2)—— 對手著法為空、對局結束、呈現使用者獲勝,而且
   該手的 `mate_in` 是真實引擎給的 **0**(黑方已被將死時引擎仍輸出 `score mate 0`),
   倒數必須照樣顯示。這一段在攔截測試裡是手寫的 JSON,在這裡是引擎真的產生的。
3. **中文記譜與實際走法一致**(8.1)—— 記譜由本檔自己依走法與**走子前**的盤面
   獨立算一份再比對,而不是拿 `notation.js` 的輸出跟自己比。

## 座標一律依實際 bounding box 換算

`test_web_play.py` 的 `click_square()` 把 viewBox 使用者座標當成 CSS 像素傳給
Playwright 的 `position`,目前成立只因為 `--board-max-width: 576px` 剛好等於 viewBox
寬度。本檔改依 `#board svg` 排版後的邊界盒縮放(手法同 `test_web_layout.py`),
盤面尺寸再怎麼變,點的都還是同一格。

## 等待的沉澱信號是 `#waiting` 轉隱藏

不能數 `#moves li`:使用者自己的半手在點擊當下就同步進了序列,而最後一手
`move` 為 `null`,列數在應手回來前後完全一樣。
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

import pytest

from service.engine.pool import EnginePool
from test_web_play import marked_squares, move_rows, pieces, text_of

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

requires_real_engine = pytest.mark.skipif(
    not REAL_ENGINE.is_file(), reason="真實引擎未安裝,請先執行 engine/fetch.sh"
)

#: 題庫中的《適情雅趣》第 21 局 —— `positions/適情雅趣/0001.json`。
PUZZLE_ID = 1
PUZZLE_TITLE = "盡善克終"
PUZZLE_SOURCE = "適情雅趣"
PUZZLE_MAX_DTM_TEXT = "16 步"
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 本地引擎(只用來替紅方挑殺著)的參數。節點數與服務的預設值相同。
NODES = 250_000
SEARCH_TIMEOUT = 15.0
ACQUIRE_TIMEOUT = 5.0

#: 步數上界。此題的殺法約 31 個半回合,這只是安全網 —— 走不完時測試失敗,
#: 而不是永遠跑下去。
MAX_PLIES = 60

#: 服務的設定。池容量取 1:瀏覽器一次只會有一個請求在路上,而容量愈小,
#: 「關閉後有沒有殘留引擎進程」這件事愈是驗得清楚。
#: 三項分項逾時之和(5 + 15 + 1)不得超過總預算,故總預算取 30。
SERVICE_ENV = {
    "LEETCHESS_POOL_SIZE": "1",
    "LEETCHESS_ACQUIRE_TIMEOUT": "5",
    "LEETCHESS_SEARCH_TIMEOUT": "15",
    "LEETCHESS_STOP_GRACE_PERIOD": "1",
    "LEETCHESS_TOTAL_TIME_BUDGET": "30",
}

#: 服務起得來的等待上限(秒)。引擎握手加題庫掃描實測約 0.3 秒。
STARTUP_TIMEOUT = 60.0

#: 關閉的等待上限(秒)。
SHUTDOWN_TIMEOUT = 20.0

#: `board.js` 的 viewBox 尺寸與格線間距。
BOARD_VIEWBOX_WIDTH = 576
BOARD_VIEWBOX_HEIGHT = 638
CELL = 62
MARGIN = 40

#: `app.js` 的固定文案。
TURN_RED = "輪方:紅方(你)"
GAME_OVER_RED_WON = "對局結束:紅方勝(你獲勝)"
FINAL_SIGNAL = "參考信號:即將取勝(約 0 步)"
SIGNAL_NOTE = "僅供參考,不是勝負判決;對局只在真終局結束。"

#: 信號讀數的形狀:`參考信號:即將取勝(約 N 步)`。
WINNING_SIGNAL = re.compile(r"^參考信號:即將取勝\(約 (\d+) 步\)$")


# --- 真實服務 -----------------------------------------------------------


def _free_port() -> int:
    """要一個當下沒人用的埠。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _children_of(pid: int) -> list[int]:
    """`pid` 的直接子進程。引擎進程由服務直接 fork,故只需看這一層。"""
    listing = subprocess.run(
        ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=True
    ).stdout
    children = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == str(pid):
            children.append(int(fields[0]))
    return children


def _alive(pid: int) -> bool:
    """`pid` 是否還在。送 0 號訊號不做任何事,只檢查進程存不存在。"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until_ready(process: subprocess.Popen, base_url: str) -> None:
    """等到題目端點答得出話為止;服務中途死掉就當場失敗。"""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"服務啟動失敗,退出碼 {process.returncode}")
        try:
            with urllib.request.urlopen(
                f"{base_url}/api/positions/{PUZZLE_ID}", timeout=2
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.05)
    raise AssertionError(f"服務在 {STARTUP_TIMEOUT} 秒內沒有起來")


def _shutdown(process: subprocess.Popen) -> None:
    """先好好關,關不掉再連同整個進程群組砍掉。

    正常關閉走的是 uvicorn 的 SIGTERM -> lifespan 的 `finally` -> `pool.shutdown()`,
    那正是本檔要驗的清理路徑;硬砍只是兜底,免得測試自己留下孤兒進程。
    """
    process.terminate()
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=SHUTDOWN_TIMEOUT)


@pytest.fixture(scope="module")
def live_service() -> Iterator[str]:
    """一個真的跑起來的 `service.main:app`,回傳它的來源。

    **不是 `TestClient`**:瀏覽器要的是一個真的連得上去的來源,而 ES module、
    `fetch()` 的同源判定、靜態檔掛載都只在真實 HTTP 之下才成立。

    以獨立的 session 啟動(`start_new_session=True`),使關不掉時整個進程群組
    砍得掉。收尾時斷言啟動期間看到的引擎子進程全部消失 —— 每個殘留的 Pikafish
    都常駐一份 51MB NNUE,漏掉的代價會隨每次跑測試疊加。

    **那條斷言證明的是「本測試沒有留下垃圾」,不是「關閉掛鉤有跑」**:實測把
    `service/main.py` 的 `pool.shutdown()` 拿掉,引擎仍然全部消失 —— 父進程一死,
    引擎的 stdin 管線就關了,Pikafish 讀到 EOF 自行結束。掛鉤本身的驗證屬服務層,
    在 `tests/test_main.py::test_shutdown_leaves_no_engine_subprocess_behind`。
    """
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "service.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=PROJECT_ROOT,
        # 先把繼承來的 `LEETCHESS_*` 全部拿掉:開發者本機設了節點數或題庫路徑之類的
        # 覆寫時,這個測試該跑的仍是它自己宣告的那一組設定。
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("LEETCHESS_")},
            **SERVICE_ENV,
        },
        start_new_session=True,
    )
    engines: list[int] = []
    try:
        _wait_until_ready(process, f"http://127.0.0.1:{port}")
        engines = _children_of(process.pid)
        assert engines, "服務起來了卻沒有任何引擎子進程,池根本沒建起來"
        yield f"http://127.0.0.1:{port}"
    finally:
        _shutdown(process)
        leaked = [pid for pid in engines if _alive(pid)]
        # 先把殘留的收乾淨再斷言:這條斷言要是失敗了,失敗本身不能順便在機器上
        # 留下一批孤兒進程 —— 那會讓下一次跑測試的環境比這一次更糟。
        for pid in leaked:
            os.kill(pid, signal.SIGKILL)
        assert not leaked, f"服務關閉後仍有引擎子進程殘留:{leaked}"


@pytest.fixture(scope="module")
def red_engine() -> Iterator[EnginePool]:
    """替紅方挑殺著用的本地引擎,與服務那一個各自獨立。"""
    pool = EnginePool(
        size=1, acquire_timeout=ACQUIRE_TIMEOUT, engine_path=REAL_ENGINE
    )
    try:
        yield pool
    finally:
        pool.shutdown()


# --- 盤面座標:依實際渲染尺寸換算 ---------------------------------------


def click_square(page, square: str) -> None:
    """點在 `square` 這一格的正中央。

    座標依 `#board svg` **排版後的**邊界盒縮放:盤面寬度由 CSS 決定(且可能因
    視窗寬度而變),把 viewBox 的使用者座標直接當 CSS 像素用,只在兩者恰好相等
    時才成立。
    """
    box = page.locator("#board svg").bounding_box()
    assert box is not None, "盤面沒有排版出來"
    file, rank = ord(square[0]) - 97, int(square[1])
    page.mouse.click(
        box["x"] + (MARGIN + file * CELL) * box["width"] / BOARD_VIEWBOX_WIDTH,
        box["y"] + (MARGIN + (9 - rank) * CELL) * box["height"] / BOARD_VIEWBOX_HEIGHT,
    )


# --- 中文記譜的獨立實作(8.1 的對照組)---------------------------------

#: 紅方子力的名稱。黑方是其餘的那些,不必另列。
RED_PIECES = frozenset("帥仕相俥傌炮兵")

#: 走斜線的子:進 / 退 之後接的是**目標縱線序號**,不是走過的格數。
DIAGONAL_PIECES = frozenset("傌馬相象仕士")

#: 紅方用的漢字數目;黑方用阿拉伯數字。
RED_DIGITS = "一二三四五六七八九"


def _square_of(uci: str, at: int) -> tuple[int, int]:
    """UCI 的第 `at` 個格名轉成 (縱線索引, 橫線)。`a0` 是紅方左下角。"""
    return ord(uci[at]) - 97, int(uci[at + 1])


def expected_notation(board: dict[str, str], uci: str) -> str:
    """`uci` 在 `board` 這個**走子前**的盤面上該記成什麼。

    這是 `notation.js` 的獨立對照組 —— 拿 `uci2cn` 的輸出跟 `uci2cn` 比等於什麼
    都沒驗到。規則(與 `tests/test_web_pure.py` 釘住的那一組相同):

    - 縱線序號:紅方以漢字自**紅方右手邊**起算(i 路為一、a 路為九),
      黑方以阿拉伯數字自**黑方右手邊**起算(a 路為 1、i 路為 9)
    - 橫線不變為「平」,後接目標縱線序號
    - 否則為進 / 退(紅方 rank 變大為進,黑方相反);斜行子後接目標縱線序號,
      直行子後接走過的格數
    - 同一縱線上有同名的己方子時,縱線序號改成頭銜:兩子為前 / 後、三子為
      前 / 中 / 後,愈靠近對方底線者愈「前」(四子以上另有序號式寫法,此局走不到,
      見下方的斷言)
    """
    from_file, from_rank = _square_of(uci, 0)
    to_file, to_rank = _square_of(uci, 2)
    piece = board[uci[:2]]
    red = piece in RED_PIECES

    def file_number(file: int) -> int:
        return 9 - file if red else file + 1

    def digit(value: int) -> str:
        return RED_DIGITS[value - 1] if red else str(value)

    # 同縱線的同名己方子,自前向後排。紅方 rank 大者為前,黑方相反。
    twins = sorted(
        (
            int(square[1])
            for square, name in board.items()
            if name == piece and ord(square[0]) - 97 == from_file
        ),
        reverse=red,
    )
    if len(twins) == 1:
        origin = digit(file_number(from_file))
    else:
        titles = {2: "前後", 3: "前中後"}.get(len(twins))
        # 四子以上改用序號(一兵、二兵…),此局走不到那裡 —— 與其寫一段沒被跑過
        # 的對照邏輯,不如在真的出現時當場說清楚。
        assert titles is not None, (
            f"{uci} 的縱線上有 {len(twins)} 個「{piece}」,四子以上的序號式頭銜"
            "不在本對照組的範圍內"
        )
        origin = titles[twins.index(from_rank)]

    if from_rank == to_rank:
        direction, target = "平", file_number(to_file)
    else:
        forward = to_rank > from_rank if red else to_rank < from_rank
        direction = "進" if forward else "退"
        target = (
            file_number(to_file)
            if piece in DIAGONAL_PIECES
            else abs(to_rank - from_rank)
        )
    # 頭銜寫在兵種**之前**(「前俥進二」),縱線序號寫在**之後**(「俥九進四」)。
    return (
        f"{piece}{origin}{direction}{digit(target)}"
        if len(twins) == 1
        else f"{origin}{piece}{direction}{digit(target)}"
    )


def apply_move(board: dict[str, str], uci: str) -> None:
    """把 `uci` 套用到盤面字典上,吃子即覆蓋。"""
    board[uci[2:]] = board.pop(uci[:2])


# --- 一整局 -------------------------------------------------------------


@requires_real_engine
def test_the_whole_puzzle_is_played_to_a_red_win_against_the_real_service(
    browser_page, live_service: str, red_engine: EnginePool
) -> None:
    """自起始局面走完《適情雅趣》第 21 局到紅勝(3.2、3.3、3.4、4.2、8.1)。

    每一輪都是:本地引擎給紅方一手殺著 -> 在瀏覽器裡選子、確認落點標示、點落點
    -> 等 `#waiting` 轉隱藏 -> 讀真實服務回的那一手應手。中途的每一個局面都必須
    仍然可以繼續走子,直到黑方真的無著可走為止。
    """
    page = browser_page
    black_move_requests: list[str] = []
    page.on(
        "request",
        lambda request: black_move_requests.append(request.url)
        if "/api/black-move" in request.url
        else None,
    )

    page.goto(f"{live_service}/index.html?id={PUZZLE_ID}")
    page.wait_for_selector("#board svg .piece")

    # 題目資訊來自真實題庫,不是夾具寫的常數。
    assert text_of(page, "#puzzle-title") == PUZZLE_TITLE
    assert text_of(page, "#puzzle-source") == PUZZLE_SOURCE
    assert text_of(page, "#puzzle-max-dtm") == PUZZLE_MAX_DTM_TEXT

    board = pieces(page)
    moves: list[str] = []
    notation: list[str] = []
    readings: list[str] = []
    reply: dict[str, Any] | None = None

    for _ in range(MAX_PLIES):
        # 中途局面必須仍在進行中 —— 信號早就說「即將取勝」了(3.3)。
        assert text_of(page, "#turn") == TURN_RED, (
            f"走了 {len(moves)} 手之後輪方不是紅方:{text_of(page, '#turn')}"
        )
        assert page.locator("#error").is_hidden(), "中途出現錯誤告知"
        assert pieces(page) == board, f"走了 {len(moves)} 手之後盤面與實際走法對不上"

        with red_engine.acquire() as engine:
            best = engine.best_move(PUZZLE_FEN, list(moves), NODES, SEARCH_TIMEOUT)
        assert best.move is not None, "對局未結束,引擎卻回報紅方無著可走"
        red_move = best.move

        # 選子:中途局面選得到子、標得出落點 —— 這就是「仍可繼續走子」。
        click_square(page, red_move[:2])
        page.wait_for_selector("#board svg .piece.selected")
        assert red_move[2:] in marked_squares(page), (
            f"選了 {red_move[:2]} 之後 {red_move[2:]} 沒有被標成落點"
        )

        notation.append(expected_notation(board, red_move))
        with page.expect_response(
            lambda response: "/api/black-move" in response.url
        ) as captured:
            click_square(page, red_move[2:])
        page.wait_for_function("() => document.getElementById('waiting').hidden")
        apply_move(board, red_move)
        moves.append(red_move)

        reply = captured.value.json()
        readings.append(text_of(page, ".signal-reading"))
        if reply["move"] is None:
            break

        assert reply["state"]["over"] is False, "對手還有應手,對局卻已結束"
        notation.append(expected_notation(board, reply["move"]))
        apply_move(board, reply["move"])
        moves.append(reply["move"])
    else:
        pytest.fail(f"走了 {MAX_PLIES} 手仍未終局,已走:{moves}")

    assert reply is not None

    # --- 最後一手(3.2、3.4、4.2)---------------------------------------
    assert reply["move"] is None, "終局那一手對手仍給了應手"
    assert reply["state"]["over"] is True
    assert reply["state"]["winner"] == "red"
    assert reply["state"]["legal_moves"] == []
    # 真實引擎在黑方已被將死時仍輸出 `score mate 0` —— 倒數為 0 不是「沒有倒數」。
    assert reply["mate_in"] == 0, f"終局那一手的殺著倒數是 {reply['mate_in']},不是 0"

    assert text_of(page, "#turn") == GAME_OVER_RED_WON
    assert text_of(page, ".signal-reading") == FINAL_SIGNAL, (
        "倒數為 0 的那一手沒有正常顯示 —— 這正是 JS 的 falsy 陷阱該出現的地方"
    )
    assert text_of(page, ".signal-note") == SIGNAL_NOTE
    assert page.locator("#waiting").is_hidden(), "終局後仍停在等待中"
    assert page.locator("#error").is_hidden()
    assert pieces(page) == board, "終局盤面與實際走法對不上"

    # --- 中途的信號早就說即將取勝,對局卻沒有因此結束(3.3、4.2)-------
    winning = [reading for reading in readings if WINNING_SIGNAL.match(reading)]
    assert len(winning) == len(readings), (
        f"有信號不是「即將取勝」:{[r for r in readings if r not in winning]}"
    )
    first = WINNING_SIGNAL.match(readings[0])
    assert first is not None and int(first.group(1)) > 0, (
        f"第一手之後的信號沒有帶正的殺著倒數:{readings[0]}"
    )
    assert len(readings) > 1, "整局只有一手,「中途不得提早停局」等於沒被驗到"
    assert not any("即將落敗" in reading for reading in readings)

    # --- 歷史著法的中文記譜(8.1)---------------------------------------
    rows = move_rows(page)
    assert len(rows) == (len(moves) + 1) // 2, "回合數與實際走法對不上"
    assert rows[-1][1] == "", "最後一手黑方沒有應手,那一半必須留空"
    assert [cell for row in rows for cell in row if cell] == notation

    # --- 終局後不再接受走子(3.2)---------------------------------------
    requests_before = len(black_move_requests)
    click_square(page, moves[-1][2:])
    assert page.locator("#board svg .piece.selected").count() == 0, "終局後仍選得到子"
    assert page.locator("#board svg .dot").count() == 0, "終局後仍標出落點"
    assert len(black_move_requests) == requests_before, "終局後仍送出了走子請求"
    assert text_of(page, "#turn") == GAME_OVER_RED_WON
