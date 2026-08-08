#!/usr/bin/env python3
"""引擎替身進程:以可預測的輸出取代真實 pikafish。

存在的理由是**逾時與崩潰路徑無法用真實引擎測**——真實引擎不會應要求永久沉默或
在搜尋中途自殺。tasks 2.1 因此要求引擎執行檔路徑可注入,本腳本即那條注入路徑的
另一端;tasks 5.5 的逾時與崩潰恢復測試會再次使用它。

以 UCI 子集回應,只認得本服務實際送出的指令。行為由 `argv[1]` 的模式決定:

    normal                 完整回應;`go nodes` 給 cp 分數
    mate                   完整回應;`go nodes` 給 mate 分數(兩行,後者較淺)
    no_legal_moves         perft 回報 0 個著法;`go nodes` 回 `bestmove (none)`
    mute                   完全不輸出,連握手都不回應
    mute_after_handshake   握手正常,`go` 之後永遠沉默
    exit_on_go             握手正常,收到 `go` 即異常終止
    truncated_go           握手正常,`go` 只輸出一半就停住(無終止行)

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
MODE_NO_LEGAL_MOVES = "no_legal_moves"
MODE_MUTE = "mute"
MODE_MUTE_AFTER_HANDSHAKE = "mute_after_handshake"
MODE_EXIT_ON_GO = "exit_on_go"
MODE_TRUNCATED_GO = "truncated_go"

MODES = (
    MODE_NORMAL,
    MODE_MATE,
    MODE_NO_LEGAL_MOVES,
    MODE_MUTE,
    MODE_MUTE_AFTER_HANDSHAKE,
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
SEARCH_NO_MOVE_LINES = ("bestmove (none)",)
SEARCH_TRUNCATED_LINES = (
    "info depth 12 seldepth 15 multipv 1 score cp 25 nodes 1024 time 3 pv e8f9",
)


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
    else:
        _emit(SEARCH_CP_LINES)


def _handle(mode: str, command: str) -> None:
    if command == "uci":
        _emit(UCI_LINES)
    elif command == "isready":
        _emit(("readyok",))
    elif command.startswith("go"):
        _handle_go(mode, command)
    # setoption / position / d 等其餘指令在真實引擎中也不產生輸出。


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else MODE_NORMAL
    if mode not in MODES:
        sys.stderr.write(f"fake_engine: 未知的模式 {mode!r}\n")
        return 2
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
            _handle(mode, command)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
