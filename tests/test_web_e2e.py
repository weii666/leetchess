"""端到端:對真實服務走完一整局,以及自列表選題、下完、返回。

本檔涵蓋兩個 spec 的最終驗收,共用同一個真實服務:

- **web-play-runtime 的 tasks 5.2**(其 requirements 3.2、3.3、3.4、4.2、8.1)——
  把《適情雅趣》第 21 局從起始局面走到紅勝。
- **problem-browser 的 tasks 5.2**(其 requirements 1.1、3.3、4.1、4.2、4.3、4.4)
  —— 自入口的題庫列表選一題、標記完成、走一手、返回列表,確認標記還在。

(兩個 spec 的最終驗收剛好都編號 5.2;下文一律寫明是哪一個 spec 的。)

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
2. **最後一手**(3.2、3.4、4.2、4.5)—— 對手著法為空、對局結束、呈現使用者獲勝,
   而且該手的 `mate_in` 是真實引擎給的 **0**(黑方已被將死時引擎仍輸出
   `score mate 0`)。這一段在攔截測試裡是手寫的 JSON,在這裡是引擎真的產生的。
   **那個 0 在畫面上一個字都不該出現**:終局的信號改為陳述結果(「已將死黑方」),
   不再是「即將取勝　約 0 步」那種倒數一件已經發生的事的說法。
3. **中文記譜與實際走法一致**(8.1)—— 記譜由本檔自己依走法與**走子前**的盤面
   獨立算一份再比對,而不是拿 `notation.js` 的輸出跟自己比。

## 座標一律依實際 bounding box 換算

`test_web_play.py` 的 `click_square()` 把 viewBox 使用者座標當成 CSS 像素傳給
Playwright 的 `position`,目前成立只因為 `--board-max-width: 576px` 剛好等於 viewBox
寬度。本檔改依 `#board svg` 排版後的邊界盒縮放(手法同 `test_web_layout.py`),
盤面尺寸再怎麼變,點的都還是同一格。

## 等待的沉澱信號是 `#turn` 不再說「(某方)走棋」

**不能數 `#moves li`**:使用者自己的半手在點擊當下就同步進了序列,而最後一手
`move` 為 `null`,列數在應手回來前後完全一樣。

這個信號原本是「`#waiting` 轉隱藏」。那個等待區塊已整條移除 —— 它在版面流裡來去,
顯示與隱藏都把底下的「歷史著法」上下推動,而等待資訊 `#turn` 本來就在說了(等應手
期間它顯示「黑方走棋」)。信號因此改掛在 `#turn` 上,判準完整寫在
`test_web_play.SETTLED` 的說明裡。兩個方向都成立:輪方**在點擊當下就同步**翻成對手
(`game.play()` 在第一個 `await` 之前就推進序列並 `notify()`),而落地之後三條出路
—— 成功、失敗、終局 —— 沒有一種留在「走棋」上。

**終局那一手不必特判**:它不會變回「輪到你」,但它會變成「你獲勝」,同樣離開了
「走棋」。這正是本檔走得到而攔截測試走不到的那一手。

順帶修掉 problem-browser 的 tasks 5.2 記下的一個問題:那裡的
`wait_for_function("#waiting hidden")` 接在 `expect_response` 之後,而 review 實測它
**執行當下已經為真**,等於一個假的等待。換成 `#turn` 之後這個呼叫點才真的在等一件事
—— `expect_response` 只保證回應到了瀏覽器,`#turn` 要等 `render()` 跑完才會變。

## problem-browser 的 tasks 5.2 為什麼也放這裡

那條動線同樣一個攔截都不裝,要的也是同一件東西:一個真的跑起來的服務、真的題庫
掃描、真的引擎應手。`live_service` 與 `red_engine` 都是 module 範圍的夾具 —— 另立
新檔會讓 uvicorn 與 Pikafish 各再起一次,而那正是這兩條測試絕大部分的成本所在。
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from service.engine.pool import EnginePool

# 只取一個常數(`web/progress.js` 的儲存鍵),**不帶進任何攔截** —— 那一檔的
# `page.route()` 全部掛在它自己的夾具裡,匯入不會執行到。第三份儲存鍵的字面值遲早
# 會與 `web/progress.js` 漂移,共用同一份才不會出現「兩邊各改一半」。
from test_web_list import STORAGE_KEY
from test_web_play import SETTLED, marked_squares, move_rows, pieces, text_of

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

requires_real_engine = pytest.mark.skipif(
    not REAL_ENGINE.is_file(), reason="真實引擎未安裝,請先執行 engine/fetch.sh"
)

#: 題庫中的《適情雅趣》第 21 局。題號即書上的局號。
PUZZLE_ID = 21
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 本地引擎(只用來替紅方挑殺著)的參數。節點數與服務的預設值相同。
NODES = 250_000
SEARCH_TIMEOUT = 15.0
ACQUIRE_TIMEOUT = 5.0

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

#: 入口、列表與對局介面的路徑。入口靠 `StaticFiles(html=True)` 解析到 `index.html`,
#: 而對局頁那條返回連結指的是 `./index.html` —— 兩者是同一頁的兩個位址。
ENTRY_PATH = "/"
LIST_PATH = "/index.html"
PLAY_PATH = "/play.html"
CATALOG_PATH = "/api/catalog"

#: 對局介面上那條返回連結的 id(`web/app.js` 的 `mountBackLink`)。
BACK_LINK_ID = "back-to-list"


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


def apply_move(board: dict[str, str], uci: str) -> None:
    """把 `uci` 套用到盤面字典上,吃子即覆蓋。"""
    board[uci[2:]] = board.pop(uci[:2])


# =======================================================================
# problem-browser 的 tasks 5.2:自列表選題、下完、返回
# =======================================================================
#
# problem-browser 的每一條前端測試都以 `page.route()` 供一份**合成**索引 —— 題庫只有
# 1 題,「一列的順序」「篩不篩得掉不可解的題目」「計數會不會更新」全都要多題才看得
# 出來。代價與上半場完全相同:被攔掉的那一段從來沒有被驗過,而這一次被攔掉的正是
# 本 spec 新做的東西 —— 真實的 `GET /api/catalog`、真實的題庫掃描。
#
# 因此本節一個攔截都不裝,走的是使用者真的會走的那一條:
#
#     開啟入口 -> 看到列表 -> 標記一題完成 -> 點進該題 -> 走一手(真引擎應手)
#               -> 返回列表 -> 標記還在
#
# ## 「標記還在」必須跨過一次真正的頁面導覽
#
# 讀兩次 localStorage 什麼也證明不了:同一個來源底下它本來就留著,那樣寫連「返回
# 途徑根本不存在」都是全綠的。這裡是真的點進對局頁、真的按下返回連結,列表**重新
# 從零載入**(以 `/api/catalog` 被打了兩次為證),標記仍在。
#
# ## 走一手是唯一會真的碰到引擎的環節
#
# 紅方那一手當場向本地引擎要(理由同上半場),而黑方的應手來自服務那一個引擎。
# 只點一下不算走成:落點標示、`#turn` 不再說「黑方走棋」、回應裡真的有一手黑棋、盤面上兩個
# 子都換了位置、歷史著法多出一整列 —— 五層都要對得上。
#
# ## 標記與對局是兩件獨立的事
#
# 產品決定:系統不自動判定解題成功(problem-browser 的 requirements 3.4)。走完一手
# 之後儲存區裡的那份完成狀態必須**原封不動**,這一條順道在對局頁上驗掉。


def get_json(url: str) -> Any:
    """向真實服務要一份 JSON。

    列表該畫成什麼樣子,由**服務當下真的回了什麼**決定,不是由本檔另外寫死一份
    題目清單 —— 那樣的話「列表根本沒去載索引」與「載了而畫對了」看起來一模一樣。
    """
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def listed_rows(page) -> list[dict[str, Any]]:
    """列表上畫出來的每一列:題號、可見文字、完成標記的勾選狀態與呈現掛勾。"""
    return page.evaluate(
        """() => [...document.querySelectorAll('#positions > li')].map((li) => ({
          id: li.dataset.id ?? null,
          text: li.innerText,
          checked: li.querySelector('input[type="checkbox"]')?.checked ?? null,
          marked: li.matches('[data-completed]'),
        }))"""
    )


def listed_counts(page) -> list[str]:
    """畫面上的「已完成 X / 總共 Y 題」兩個數字。"""
    return page.evaluate(
        """() => ['completed-count', 'total-count'].map(
          (id) => document.getElementById(id).textContent.trim(),
        )"""
    )


def stored_completed(page) -> Any:
    """儲存區裡目前的完成狀態原始值(沒寫過時為 `None`)。

    列表頁與對局頁同來源,因此在對局頁上讀到的就是列表寫下的那一份。
    """
    return page.evaluate(f"() => localStorage.getItem({json.dumps(STORAGE_KEY)})")


def completed_ids(page) -> list[int] | None:
    """儲存區裡的完成狀態,解析成題號清單;沒寫過時為 `None`。

    刻意**不直接** `json.loads(stored_completed(page))`:沒寫過時那是
    `json.loads(None)`,測試會以 `TypeError` 收場而不是自己寫的那句斷言 —— 而
    「儲存區裡什麼都沒有」正是這幾條斷言最主要要抓的退化(problem-browser 的
    tasks 4.2 在同一個地方留下過這則教訓)。
    """
    raw = stored_completed(page)
    return None if raw is None else json.loads(raw)


@requires_real_engine
def test_a_position_is_picked_from_the_list_played_and_returned_from(
    browser_page, live_service: str, red_engine: EnginePool
) -> None:
    """自入口的題庫列表選題、標記、下一手、返回,標記仍在(1.1、3.3、4.1、4.2、
    4.3、4.4;完成標記不因對局而變動則屬 3.4)。

    整條動線只走使用者真的會走的路徑:開的是服務的入口位址、點的是列上那個真連結、
    走的那一手由真實引擎應答、返回按的是對局頁上那條返回連結。
    """
    page = browser_page
    catalog_requests: list[str] = []
    page.on(
        "request",
        lambda request: catalog_requests.append(request.url)
        if urlsplit(request.url).path == CATALOG_PATH
        else None,
    )

    # --- 開啟入口 -> 看到列表(1.1)-------------------------------------
    page.goto(f"{live_service}{ENTRY_PATH}")
    page.wait_for_selector("#positions > li")

    assert page.locator("#board").count() == 0, "入口給的是棋盤,不是題庫列表"
    # 這些列是**去真實服務要來的**。有沒有發出那個請求,是內容比對分不出來的事。
    assert catalog_requests, "列表沒有向真實服務取索引"

    # 列表該有哪幾列,由真實索引決定 —— 題數不寫進斷言。題庫每收一題就會多一列,
    # 那時該轉紅的是題庫的內容檢查,不是這條動線。
    catalog = get_json(f"{live_service}{CATALOG_PATH}")["positions"]
    assert catalog, "真實題庫是空的,這條動線無從走起"
    chosen = next(entry for entry in catalog if entry["id"] == PUZZLE_ID)

    drawn = listed_rows(page)
    assert [row["id"] for row in drawn] == [str(entry["id"]) for entry in catalog], (
        f"列表畫出來的題目與真實索引對不上:{[row['id'] for row in drawn]}"
    )
    # 局名也要對得上:只比題號的話,一份題號恰好相同而內容寫死的清單照樣全綠。
    on_screen = {row["id"]: row["text"] for row in drawn}
    for entry in catalog:
        assert entry["title"] in on_screen[str(entry["id"])], (
            f"第 {entry['id']} 題的局名「{entry['title']}」沒有畫在它那一列上:"
            f"{on_screen[str(entry['id'])]!r}"
        )
    assert listed_counts(page) == ["0", str(len(catalog))]
    assert stored_completed(page) is None, "只是開啟列表就寫進了儲存區"

    # --- 標記一題完成(3.1、3.2)---------------------------------------
    toggle = page.locator(
        f'#positions > li[data-id="{PUZZLE_ID}"] input[type="checkbox"]'
    )
    toggle.click()

    marked = {row["id"]: (row["checked"], row["marked"]) for row in listed_rows(page)}
    assert marked[str(PUZZLE_ID)] == (True, True), f"標記沒有立即反映:{marked}"
    assert listed_counts(page) == ["1", str(len(catalog))]
    stored_after_marking = stored_completed(page)
    assert completed_ids(page) == [PUZZLE_ID], (
        f"標記完成之後儲存區裡不是那一題:{stored_after_marking!r}"
    )

    # --- 點進該題(4.1、4.2)-------------------------------------------
    page.locator(f'#positions > li[data-id="{PUZZLE_ID}"] a[href]').click()
    page.wait_for_selector("#board svg .piece")

    assert urlsplit(page.url).path == PLAY_PATH
    assert urlsplit(page.url).query == f"id={PUZZLE_ID}"
    # 載入的**確實是那一題**:題目資訊全部取自真實題庫,不是本檔寫死的常數。
    assert text_of(page, "#puzzle-title") == f"{chosen['id']}. {chosen['title']}"
    assert text_of(page, "#puzzle-source") == chosen["source"]
    # 描述**不再呈現**(problem-browser 4.5 修訂):真實題庫的 description 就是
    # 「出處 + 局號 + 局名」的串接,與上面兩行完全重複。這裡以真實題庫自己的那串字
    # 反驗它沒有回來 —— 寫死一個字串的話,題庫改了描述就抓不到。
    assert chosen["description"] not in page.locator("#sidebar").inner_text(), (
        "描述又跑回對局介面上了"
    )

    # --- 走一手,真實引擎應手 -------------------------------------------
    board = pieces(page)
    with red_engine.acquire() as engine:
        best = engine.best_move(PUZZLE_FEN, [], NODES, SEARCH_TIMEOUT)
    assert best.move is not None, "起始局面下引擎卻回報紅方無著可走"
    red_move = best.move

    click_square(page, red_move[:2])
    page.wait_for_selector("#board svg .piece.selected")
    assert red_move[2:] in marked_squares(page), (
        f"選了 {red_move[:2]} 之後 {red_move[2:]} 沒有被標成落點"
    )

    with page.expect_response(
        lambda response: "/api/black-move" in response.url
    ) as captured:
        click_square(page, red_move[2:])
    # 沉澱信號是 `#turn` 不再說「黑方走棋」,不是數 `#moves li` —— 自己那半手在
    # 點擊當下就同步進了序列(web-play-runtime 4.4 的教訓)。完整說明見
    # `test_web_play.SETTLED`。
    page.wait_for_function(SETTLED)

    reply = captured.value.json()
    assert reply["move"] is not None, "真實引擎沒有給出應手,這一手等於沒走成"
    assert reply["state"]["over"] is False, "才走一手,對局卻已結束"

    apply_move(board, red_move)
    apply_move(board, reply["move"])
    assert pieces(page) == board, "盤面沒有照著實際走的那兩手更新"
    assert page.locator("#error").is_hidden(), "走一手之後出現錯誤告知"

    rows = move_rows(page)
    assert len(rows) == 1, f"走完一個回合之後歷史著法不是一列:{rows}"
    assert all(rows[0]), f"歷史著法那一列有一半是空的:{rows[0]}"

    # 3.4:標記與對局是兩件事 —— 系統不自動判定解題成功,走一手不得動到完成狀態。
    assert stored_completed(page) == stored_after_marking, (
        "走了一手之後完成標記被動過 —— 系統不得自動判定任何題目為完成"
    )

    # --- 返回列表(4.3)-------------------------------------------------
    back = page.locator(f"#{BACK_LINK_ID}")
    assert back.count() == 1, "對局介面上沒有返回列表的途徑"
    requests_before = len(catalog_requests)

    back.click()
    # 先驗落點、再等列畫出來,而且兩個等待都給短上限。照預設的 30 秒寫下去的話,
    # 返回連結指錯或索引取不到時,失敗會是一個等滿 30 秒的 `TimeoutError`,說的是
    # 「在等某個選擇器」而不是「返回的落點不對」—— 真正說得出原因的那句斷言反而
    # 永遠跑不到。本機導覽走完只要幾十毫秒,5 秒綽綽有餘。
    try:
        page.wait_for_url(f"**{LIST_PATH}", timeout=5000)
    except PlaywrightTimeoutError:
        pytest.fail(f"按下返回之後沒有回到列表頁,停在:{page.url}")
    try:
        page.wait_for_selector("#positions > li", timeout=5000)
    except PlaywrightTimeoutError:
        pytest.fail(
            f"回到列表頁了,但一列都沒畫出來。索引請求:{catalog_requests}"
        )
    # 列表是**重新從零載入**的,不是回到一份還留在記憶體裡的畫面 —— 少了這一層,
    # 「標記還在」可能只是因為根本沒離開過那一頁。
    assert len(catalog_requests) == requests_before + 1, (
        f"返回列表之後沒有重新取索引:{catalog_requests}"
    )

    # --- 標記還在(3.3、4.4)-------------------------------------------
    returned = {row["id"]: (row["checked"], row["marked"]) for row in listed_rows(page)}
    assert returned == {
        row_id: (row_id == str(PUZZLE_ID), row_id == str(PUZZLE_ID))
        for row_id in [str(entry["id"]) for entry in catalog]
    }, f"返回列表之後完成標記不見了:{returned}"
    assert listed_counts(page) == ["1", str(len(catalog))]
    # 畫面對了而儲存區已被清掉的話,要到下一次重新載入才會浮現 —— 一併釘住。
    assert completed_ids(page) == [PUZZLE_ID], (
        f"返回列表之後儲存區裡的完成狀態不對:{stored_completed(page)!r}"
    )
