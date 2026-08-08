"""EngineProcess:單一 pikafish 子進程的 UCI 封裝。

指令序列與輸出解析移植自 `poc/server.py` 的 `Engine` 類(該部分已驗證正確),
但**讀取方式整個換掉**:POC 以 `for line in self.proc.stdout` 直接迭代管線,沒有
任何上界,引擎一旦不輸出,呼叫的執行緒就永久卡死。服務端每個卡死的執行緒都不會
自己回來,累積下去等同於服務躺平。因此本模組的規則是:

    **任何一次讀取都有逾時上界,且上界由呼叫端傳入。**

作法是把阻塞的管線讀取隔離進一條背景執行緒,主線改由 `queue.Queue` 帶逾時取行。
`selectors` 對 fd 輪詢也可行,但需自行處理半行與文字解碼;佇列版把這兩件事留給
Python 既有的行緩衝,較不易出錯。

## 三個不可動搖的約束(見 `.kiro/steering/tech.md`)

- **stateless**:每次查詢重送完整 `position fen <fen> moves <...>`,本類不記憶局面
- **勝負不自實作**:合法著法來自 `go perft 1` 的輸出,終局即「合法著法數為 0」
- **循環規則不自實作**:長將、長捉、一將一殺一律由引擎判定

這也是 requirements 1.6 的保證方式 —— 判定全部取自引擎輸出,服務不實作任何規則
邏輯,因此必然與所執行的引擎一致。

## 尚未在此的部分

- **走法序列驗證(5.3)**屬 tasks 2.2:引擎對非法著法靜默忽略,須在送出 `position`
  之後、送出查詢指令之前插入一道 `d` 指令比對。銜接點是 `_query` 內兩次 `_send`
  之間,該處的逾時已由同一個 deadline 涵蓋。
- **逾時的兩段式處置(4.1、4.2)**屬 tasks 2.5:先送 `stop` 進寬限期、寬限期內回應
  則進程可繼續服役。本模組目前一律將逾時視為進程不可再用(標記為不健康),是安全
  的一側 —— 逾時後管線裡可能還躺著上一道指令的殘留輸出,重用會讀到錯位的結果。
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from service.errors import EngineTimeoutError, InternalError
from service.types import BestMove, Score, ScoreKind

__all__ = [
    "DEFAULT_STARTUP_TIMEOUT",
    "TERMINATE_GRACE_PERIOD",
    "EngineProcess",
]

#: UCI 座標:檔 a–i,列 0–9。用於認出 perft 輸出中的著法行。
MOVE_PATTERN = re.compile(r"^[a-i][0-9][a-i][0-9]$")

#: `info ... score (mate|cp) N ...`。視角為**當前輪方**,本層不做任何視角轉換。
SCORE_PATTERN = re.compile(r"\bscore (mate|cp) (-?\d+)\b")

PERFT_TERMINATOR = "Nodes searched"
BESTMOVE_PREFIX = "bestmove"

#: 引擎回報無著可走時 `bestmove` 帶的 token。
NO_MOVE_TOKENS = frozenset({"(none)", "none", "0000"})

#: 握手的逾時上界(秒)。含載入 NNUE,故遠寬於單次搜尋的逾時。
DEFAULT_STARTUP_TIMEOUT = 15.0

#: 終止進程時,等待自願結束與等待 kill 生效各自的上界(秒)。
TERMINATE_GRACE_PERIOD = 1.0


class _ReadTimeout(Exception):
    """讀取超過上界。僅在本模組內部流通,對外轉為 `EngineTimeoutError`。"""


class _StreamClosed(Exception):
    """引擎在預期輸出前結束了。僅在本模組內部流通,對外轉為 `InternalError`。"""


class _LineReader:
    """把子進程 stdout 的阻塞讀取隔離到背景執行緒,使呼叫端能以逾時取行。

    執行緒設為 daemon,且在進程終止(管線關閉)後自行結束 —— 讀取端一旦收到 EOF
    就跳出迴圈,不會殘留。
    """

    _EOF = object()

    def __init__(self, stream) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._pump, args=(stream,), name="engine-stdout", daemon=True
        )
        self._thread.start()

    def _pump(self, stream) -> None:
        try:
            for line in stream:
                self._queue.put(line.rstrip("\n"))
        except (OSError, ValueError):
            # 管線在讀取中被關閉。視同 EOF,由 finally 送出哨兵。
            pass
        finally:
            self._queue.put(self._EOF)

    def next_line(self, deadline: float) -> str:
        """取下一行,最多等到 `deadline`。

        Raises:
            _ReadTimeout: 到 deadline 仍無新行。
            _StreamClosed: 引擎的 stdout 已結束。
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _ReadTimeout
        try:
            item = self._queue.get(timeout=remaining)
        except queue.Empty as cause:
            raise _ReadTimeout from cause
        if item is self._EOF:
            # 哨兵放回去,使後續讀取同樣立刻得知串流已結束,而不是再等一次逾時。
            self._queue.put(self._EOF)
            raise _StreamClosed
        return item

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread.is_alive()


def _position_command(fen: str, moves: Sequence[str]) -> str:
    """完整局面指令。引擎 stateless,每次查詢都重送這一整句。"""
    command = f"position fen {fen}"
    if moves:
        command += " moves " + " ".join(moves)
    return command


def _parse_legal_moves(lines: Sequence[str]) -> list[str]:
    """從 `go perft 1` 的輸出取出合法著法。

    每行形如 `e8f9: 1`;`info string ...`、空行與結尾的 `Nodes searched: N` 都不會
    被誤認為著法。回傳空 list 即輪方無著可走,這是真終局的唯一判準(1.2)。
    """
    moves = []
    for line in lines:
        head = line.split(":", 1)[0].strip()
        if MOVE_PATTERN.match(head):
            moves.append(head)
    return moves


def _parse_score(lines: Sequence[str]) -> Score | None:
    """取最後一行分數。迭代加深會輸出多行,最後一行才是本次搜尋的結論。"""
    score = None
    for line in lines:
        match = SCORE_PATTERN.search(line)
        if match:
            score = Score(kind=ScoreKind(match.group(1)), value=int(match.group(2)))
    return score


def _parse_best_move(line: str) -> str | None:
    """從 `bestmove <move> [ponder <move>]` 取著法。無著可走時回傳 None。"""
    tokens = line.split()
    if len(tokens) < 2:
        raise InternalError()
    token = tokens[1]
    if token in NO_MOVE_TOKENS:
        return None
    if not MOVE_PATTERN.match(token):
        raise InternalError()
    return token


class EngineProcess:
    """單一引擎子進程。所有等待均有逾時上界。

    執行檔路徑由呼叫端注入,測試因此能以替身進程取代真實引擎 —— 逾時與崩潰路徑
    無法用真實引擎製造(它不會應要求永久沉默),路徑寫死等於那些路徑測不到。

    併發:池保證同一進程不會同時借給兩個請求;此處仍以一把鎖守住「一次一道指令
    交換」的不變量,使誤用不會演變成兩個回應交錯的難查 bug。這不影響服務整體的
    併發度 —— 併發由池中的多個進程提供,而非單進程內的多執行緒(`Threads=1`)。
    """

    def __init__(
        self,
        engine_path: Path | str,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        # 先解析為絕對路徑:下面把 cwd 換到引擎自己的目錄,相對路徑會相對於**新的**
        # cwd 解析而找不到執行檔。
        path = Path(engine_path).resolve()
        self._lock = threading.Lock()
        self._broken = False
        self._process = subprocess.Popen(  # noqa: S603 - 路徑由服務設定提供,非使用者輸入
            [str(path)],
            cwd=str(path.parent),  # 讓引擎在自己的目錄下找到 pikafish.nnue
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader = _LineReader(self._process.stdout)
        try:
            self._handshake(startup_timeout)
        except BaseException:
            # 握手失敗(例如替身永不回應)也不能留下孤兒進程與背景執行緒。
            self.terminate()
            raise

    # --- 對外的三個操作 --------------------------------------------------

    def legal_moves(self, fen: str, moves: list[str], timeout: float) -> list[str]:
        """該局面下輪方的所有合法著法(UCI)。

        空 list 代表輪方無著可走,即真終局。判定全部來自引擎輸出,本層不實作任何
        規則邏輯,循環規則局面亦然(1.1、1.6)。

        Raises:
            EngineTimeoutError: 引擎在 `timeout` 內未輸出完整結果(4.1)。
            InternalError: 引擎已終止或輸出無法解析。
        """
        lines = self._query(
            fen, moves, "go perft 1", _starts_with(PERFT_TERMINATOR), timeout
        )
        return _parse_legal_moves(lines)

    def best_move(
        self, fen: str, moves: list[str], nodes: int, timeout: float
    ) -> BestMove:
        """搜尋 `nodes` 個節點後的最佳著法與分數(1.4)。

        用 `go nodes` 而非 `go movetime`:目的不是重現同一步,而是當品質下限,
        保證每次都搜得夠深(見 `tech.md` 的引擎調用慣例)。

        分數視角為**當前輪方**,本層不轉換視角 —— 引擎層不知道紅黑與對局概念。
        呼叫端只在輪方為黑時請求應手,故該分數即黑方視角。

        Raises:
            EngineTimeoutError: 引擎在 `timeout` 內未回報 `bestmove`(4.1)。
            InternalError: 引擎已終止或輸出無法解析。
        """
        lines = self._query(
            fen, moves, f"go nodes {nodes}", _starts_with(BESTMOVE_PREFIX), timeout
        )
        return BestMove(move=_parse_best_move(lines[-1]), score=_parse_score(lines))

    def is_healthy(self) -> bool:
        """進程仍在執行,且未曾發生使其輸出不可信的事件(逾時、崩潰、終止)。"""
        return not self._broken and self._process.poll() is None

    def terminate(self) -> None:
        """終止進程並收回背景執行緒。可重複呼叫。

        先送 `quit` 讓引擎自己收尾,寬限期內未結束才 kill。終止後 `is_healthy()`
        恆為假。
        """
        self._broken = True
        if self._process.poll() is None:
            self._request_quit()
            if not self._wait_for_exit():
                self._process.kill()
                self._wait_for_exit()
        self._reader.join(TERMINATE_GRACE_PERIOD)
        self._close_streams()

    # --- 指令交換 --------------------------------------------------------

    def _query(
        self,
        fen: str,
        moves: Sequence[str],
        command: str,
        done: Callable[[str], bool],
        timeout: float,
    ) -> list[str]:
        """重送完整局面、送出查詢指令,收集輸出直到終止行。

        `deadline` 在進入時就算好,整趟交換(含 2.2 將插入於兩次 `_send` 之間的
        序列驗證)共用同一個上界,因此方法的總等待時間不超過傳入的 `timeout`。
        """
        deadline = time.monotonic() + timeout
        with self._lock:
            self._ensure_usable()
            self._send(_position_command(fen, moves))
            self._send(command)
            return self._read_until(done, deadline)

    def _handshake(self, timeout: float) -> None:
        """`uci` / 選項 / `isready`。`Threads=1`、`Hash=128` 依 `tech.md` 固定。"""
        deadline = time.monotonic() + timeout
        self._send("uci")
        self._read_until(_starts_with("uciok"), deadline)
        self._send("setoption name Threads value 1")
        self._send("setoption name Hash value 128")
        self._send("isready")
        self._read_until(_starts_with("readyok"), deadline)

    def _ensure_usable(self) -> None:
        if not self.is_healthy():
            raise InternalError()

    def _send(self, command: str) -> None:
        try:
            self._process.stdin.write(command + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as cause:
            self._broken = True
            raise InternalError() from cause

    def _read_until(self, done: Callable[[str], bool], deadline: float) -> list[str]:
        """讀到滿足 `done` 的那一行為止,連同該行一起回傳。

        逾時與串流結束都會把進程標記為不健康 —— 兩種情況下管線的後續內容都不再
        對得上任何一道指令,重用只會讀到錯位的結果。2.5 會在逾時這一側插入 `stop`
        與寬限期,寬限期內回應者可豁免標記。
        """
        lines: list[str] = []
        while True:
            try:
                line = self._reader.next_line(deadline)
            except _ReadTimeout as cause:
                self._broken = True
                raise EngineTimeoutError() from cause
            except _StreamClosed as cause:
                self._broken = True
                raise InternalError() from cause
            lines.append(line)
            if done(line):
                return lines

    # --- 終止的細節 ------------------------------------------------------

    def _request_quit(self) -> None:
        try:
            self._process.stdin.write("quit\n")
            self._process.stdin.flush()
        except (OSError, ValueError):
            pass  # 進程已不接受輸入,下一步會直接 kill

    def _wait_for_exit(self) -> bool:
        try:
            self._process.wait(TERMINATE_GRACE_PERIOD)
        except subprocess.TimeoutExpired:
            return False
        return True

    def _close_streams(self) -> None:
        for stream in (self._process.stdin, self._process.stdout):
            if stream is None or stream.closed:
                continue
            if stream is self._process.stdout and self._reader.is_running():
                continue  # 讀取執行緒還在用這個串流,交給 Popen 回收時關閉
            try:
                stream.close()
            except OSError:
                pass


def _starts_with(prefix: str) -> Callable[[str], bool]:
    return lambda line: line.startswith(prefix)
