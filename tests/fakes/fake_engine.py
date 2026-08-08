#!/usr/bin/env python3
"""引擎替身進程:以可預測的輸出取代真實 pikafish。

存在的理由是**逾時與崩潰路徑無法用真實引擎測**——真實引擎不會應要求永久沉默或
在搜尋中途自殺。tasks 2.1 因此要求引擎執行檔路徑可注入,本腳本即那條注入路徑的
另一端;tasks 5.5 的逾時與崩潰恢復測試會再次使用它。

以 UCI 子集回應,只認得本服務實際送出的指令。行為由 `argv[1]` 的模式決定:

    normal                 完整回應;`go nodes` 給 cp 分數
    mate                   完整回應;`go nodes` 給 mate 分數(兩行,後者較淺)
    mate_for_black         完整回應;`go nodes` 給**正的** mate 分數(輪方可殺)
    no_legal_moves         perft 回報 0 個著法;`go nodes` 回 `bestmove (none)`
    ignores_moves          複製真實引擎對非法著法的靜默忽略:`d` 恆回起始局面
    mute                   完全不輸出,連握手都不回應
    mute_after_handshake   握手正常,`go` 之後永遠沉默
    mute_on_display        握手與 `go` 都正常,只有 `d` 永遠沉默
    exit_on_go             握手正常,收到 `go` 即異常終止
    truncated_go           握手正常,`go` 只輸出一半就停住(無終止行)

`d` 指令會回出「套用了所送走法數之後」的局面 FEN,使協定層的序列驗證(5.3)在
替身下也走完整條路徑。兩個例外:`ignores_moves` 恆回起始局面,重現真實引擎丟棄
非法著法後**不回報任何錯誤**的行為,那正是驗證要攔下的情況;`mute_on_display`
則對 `d` 完全沉默,使序列驗證那道讀取成為唯一的卡點 —— 它讓「該讀取是否受呼叫端
傳入的逾時管轄」可被測到,`mute_after_handshake` 停在更後面的 `go`,測不到這裡。

若環境變數 `FAKE_ENGINE_LOG` 指向一個檔案,收到的每道指令會逐行附加進去,
使測試能斷言送出的指令序列(例如 `Threads=1`、`Hash=128`)。

零依賴、純標準庫:它會被當成獨立執行檔啟動,不在 pytest 的行程內。
"""

from __future__ import annotations

import os
import sys

LOG_ENV = "FAKE_ENGINE_LOG"

MODE_NORMAL = "normal"
MODE_MATE = "mate"
MODE_MATE_FOR_BLACK = "mate_for_black"
MODE_NO_LEGAL_MOVES = "no_legal_moves"
MODE_IGNORES_MOVES = "ignores_moves"
MODE_MUTE = "mute"
MODE_MUTE_AFTER_HANDSHAKE = "mute_after_handshake"
MODE_MUTE_ON_DISPLAY = "mute_on_display"
MODE_EXIT_ON_GO = "exit_on_go"
MODE_TRUNCATED_GO = "truncated_go"

MODES = (
    MODE_NORMAL,
    MODE_MATE,
    MODE_MATE_FOR_BLACK,
    MODE_NO_LEGAL_MOVES,
    MODE_IGNORES_MOVES,
    MODE_MUTE,
    MODE_MUTE_AFTER_HANDSHAKE,
    MODE_MUTE_ON_DISPLAY,
    MODE_EXIT_ON_GO,
    MODE_TRUNCATED_GO,
)

#: 握手輸出。真實引擎在 `uciok` 前會列出一長串 option,此處只留必要的形狀。
UCI_LINES = ("id name fake-engine", "id author leetchess tests", "uciok")

#: `go perft 1` 的輸出。空行與 `info string` 行照抄真實引擎,確保解析器不被雜訊絆倒。
PERFT_LINES = (
    "info string Using 1 thread",
    "e8f9: 1",
    "e9f9: 1",
    "",
    "Nodes searched: 2",
)
PERFT_NO_MOVES_LINES = ("", "Nodes searched: 0")
PERFT_TRUNCATED_LINES = ("e8f9: 1",)

#: `go nodes N` 的輸出。mate 模式刻意給兩行分數,後到的才是正解——解析器必須取最後一行。
SEARCH_CP_LINES = (
    "info depth 12 seldepth 15 multipv 1 score cp 25 nodes 1024 time 3 pv e8f9",
    "bestmove e8f9 ponder e9f9",
)
SEARCH_MATE_LINES = (
    "info depth 20 seldepth 28 multipv 1 score mate -20 nodes 5000 time 4 pv e9f9",
    "info depth 38 seldepth 31 multipv 1 score mate -15 nodes 173519 time 89 pv e9f9",
    "bestmove e9f9 ponder g6h8",
)
#: 分數為**正**的 mate:輪方(黑)可將死對手。先給一行 cp,確保解析器取的是最後一行。
SEARCH_MATE_FOR_BLACK_LINES = (
    "info depth 14 seldepth 20 multipv 1 score cp -180 nodes 4000 time 3 pv e9f9",
    "info depth 31 seldepth 34 multipv 1 score mate 12 nodes 168420 time 85 pv e9f9",
    "bestmove e9f9 ponder g6h8",
)
SEARCH_NO_MOVE_LINES = ("bestmove (none)",)
SEARCH_TRUNCATED_LINES = (
    "info depth 12 seldepth 15 multipv 1 score cp 25 nodes 1024 time 3 pv e8f9",
)

POSITION_PREFIX = "position fen "
MOVES_SEPARATOR = " moves "

#: 尚未收到 `position` 前 `d` 所回報的局面。真實引擎此時回報象棋開局局面。
DEFAULT_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


class _Board:
    """替身記得的局面:起始 FEN 與**實際套用的步數**。

    只追蹤步數、不真的走棋 —— 序列驗證讀的是行棋方與 fullmove,兩者都由步數決定,
    盤面本身對驗證沒有作用。
    """

    def __init__(self) -> None:
        self.fen = DEFAULT_FEN
        self.applied = 0

    def set_position(self, command: str, mode: str) -> None:
        base, _, move_text = command[len(POSITION_PREFIX) :].partition(MOVES_SEPARATOR)
        self.fen = base.strip()
        # 靜默忽略模式一步都不套用,局面停在起始處 —— 這正是真實引擎遇到非法著法
        # 時的行為,且它同樣不輸出任何錯誤。
        self.applied = 0 if mode == MODE_IGNORES_MOVES else len(move_text.split())

    def displayed_fen(self) -> str:
        fields = self.fen.split()
        ply = 2 * (int(fields[5]) - 1) + (0 if fields[1] == "w" else 1) + self.applied
        fields[1] = "w" if ply % 2 == 0 else "b"
        # halfmove 一律回 0:替身不追蹤吃子,而驗證本來就不得讀這一欄(它會被吃子重置)。
        fields[4] = "0"
        fields[5] = str(ply // 2 + 1)
        return " ".join(fields)


def _record(command: str) -> None:
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    with open(path, "a", encoding="utf-8") as log:
        log.write(command + "\n")


def _emit(lines: tuple[str, ...]) -> None:
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle_go(mode: str, command: str) -> None:
    if mode == MODE_MUTE_AFTER_HANDSHAKE:
        return
    if mode == MODE_EXIT_ON_GO:
        # 不走 sys.exit:模擬引擎崩潰,不做任何收尾也不回應。
        os._exit(1)
    if command.startswith("go perft"):
        if mode == MODE_NO_LEGAL_MOVES:
            _emit(PERFT_NO_MOVES_LINES)
        elif mode == MODE_TRUNCATED_GO:
            _emit(PERFT_TRUNCATED_LINES)
        else:
            _emit(PERFT_LINES)
        return
    if mode == MODE_NO_LEGAL_MOVES:
        _emit(SEARCH_NO_MOVE_LINES)
    elif mode == MODE_TRUNCATED_GO:
        _emit(SEARCH_TRUNCATED_LINES)
    elif mode == MODE_MATE:
        _emit(SEARCH_MATE_LINES)
    elif mode == MODE_MATE_FOR_BLACK:
        _emit(SEARCH_MATE_FOR_BLACK_LINES)
    else:
        _emit(SEARCH_CP_LINES)


def _handle_display(mode: str, board: _Board) -> None:
    """`d` 的輸出。真實引擎先畫盤面再列這三行,序列驗證只讀 `Fen:`,終止行為 `Checkers:`。"""
    if mode == MODE_MUTE_ON_DISPLAY:
        # 只在 `d` 上沉默;握手與 `go` 照常,進程因此走得到序列驗證那道讀取才卡住。
        return
    _emit((f"Fen: {board.displayed_fen()}", "Key: 0000000000000000", "Checkers: "))


def _handle(mode: str, command: str, board: _Board) -> None:
    if command == "uci":
        _emit(UCI_LINES)
    elif command == "isready":
        _emit(("readyok",))
    elif command.startswith(POSITION_PREFIX):
        board.set_position(command, mode)  # 真實引擎對此不輸出任何內容
    elif command == "d":
        _handle_display(mode, board)
    elif command.startswith("go"):
        _handle_go(mode, command)
    # setoption 等其餘指令在真實引擎中也不產生輸出。


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else MODE_NORMAL
    if mode not in MODES:
        sys.stderr.write(f"fake_engine: 未知的模式 {mode!r}\n")
        return 2
    board = _Board()
    while True:
        raw = sys.stdin.readline()
        if raw == "":  # stdin 關閉
            return 0
        command = raw.strip()
        if not command:
            continue
        _record(command)
        if command == "quit":
            return 0
        if mode != MODE_MUTE:
            _handle(mode, command, board)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
