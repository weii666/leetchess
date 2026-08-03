#!/usr/bin/env python3
"""POC 本地引擎后端 —— 包住 native pikafish,供前端棋盘调用。

这是一次性开发工具,不是 DESIGN 的最终形态(最终 web/ 用 WASM,无后端)。
遵守 DESIGN §7:合法性与胜负一律由引擎判定,前端/后端都不自实作象棋规则。

- 引擎完全 stateless:每次请求都重送完整 `position fen <fen> moves <...>`。
- 合法着 / 终局:`go perft 1`。合法着数 = 0 → 轮到的一方被将死或困毙 → 该方负。
- 黑方应手:`go nodes <N>`,取 bestmove(黑方在此局必负,bestmove 即最顽强防守)。

端点:
  GET /                     前端页面(poc/index.html)
  GET /api/start            题目起始 FEN + metadata
  GET /api/state?moves=...  某走子序列后的:轮方、合法着、是否终局、胜方
  GET /api/black?moves=...  引擎算黑方应手(仅在轮到黑方时调用)
"""
import json
import os
import re
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "engine", "pikafish")
POSITION_FILE = os.path.join(ROOT, "positions", "0001.json")
# 黑方搜索节点数。POC 求反应快:200k ≈ 0.2s,黑方为必负方,浅搜即得最顽强防守
# (实测 2M→20k 同一手)。可用环境变量覆写。DESIGN 生产判定另走离线预解,与此无关。
BLACK_NODES = int(os.environ.get("BLACK_NODES", "200000"))

MOVE_RE = re.compile(r"^[a-i][0-9][a-i][0-9]$")


class Engine:
    """单一 pikafish 进程的 UCI 封装。stateless 用法:每次重送完整局面。"""

    def __init__(self, path):
        cwd = os.path.dirname(path)  # 让引擎在 engine/ 下找到 pikafish.nnue
        self.proc = subprocess.Popen(
            [path], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.lock = threading.Lock()
        self._send("uci"); self._read_until(lambda l: l.startswith("uciok"))
        self._send("setoption name Threads value 1")
        self._send("setoption name Hash value 128")
        self._send("isready"); self._read_until(lambda l: l.startswith("readyok"))

    def _send(self, cmd):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _read_until(self, done):
        lines = []
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if done(line):
                return lines
        raise RuntimeError("引擎在预期输出前结束了")

    @staticmethod
    def _position_cmd(fen, moves):
        cmd = f"position fen {fen}"
        if moves:
            cmd += " moves " + " ".join(moves)
        return cmd

    def legal_moves(self, fen, moves):
        """回传该局面下轮方的所有合法着(UCI)。空 list = 该方无着可走(被杀/困毙)。"""
        with self.lock:
            self._send(self._position_cmd(fen, moves))
            self._send("go perft 1")
            out = self._read_until(lambda l: l.startswith("Nodes searched"))
        legal = []
        for line in out:
            head = line.split(":", 1)[0].strip()
            if MOVE_RE.match(head):
                legal.append(head)
        return legal

    def best_move(self, fen, moves, nodes):
        """回传 (bestmove, score_dict)。bestmove 为 'none' 表示无着(被杀)。"""
        with self.lock:
            self._send(self._position_cmd(fen, moves))
            self._send(f"go nodes {nodes}")
            out = self._read_until(lambda l: l.startswith("bestmove"))
        score = None
        for line in out:
            m = re.search(r"score (mate|cp) (-?\d+)", line)
            if m:
                score = {"type": m.group(1), "value": int(m.group(2))}
        best = out[-1].split()[1]
        return best, score


def load_position():
    with open(POSITION_FILE, encoding="utf-8") as f:
        return json.load(f)


def side_to_move(fen, moves):
    """起始轮方 + 已走步数 → 当前轮方。"""
    base = fen.split()[1]  # 'w' or 'b'
    start_red = (base == "w")
    red_turn = start_red if (len(moves) % 2 == 0) else (not start_red)
    return "red" if red_turn else "black"


class Handler(BaseHTTPRequestHandler):
    engine = None
    position = None

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _moves_param(self, qs):
        raw = urllib.parse.parse_qs(qs).get("moves", [""])[0].strip()
        moves = [m for m in raw.split(",") if m]
        for m in moves:
            if not MOVE_RE.match(m):
                raise ValueError(f"非法走法格式:{m}")
        return moves

    def _state(self, moves):
        fen = self.position["fen"]
        turn = side_to_move(fen, moves)
        legal = self.engine.legal_moves(fen, moves)
        over = len(legal) == 0
        winner = None
        if over:
            winner = "black" if turn == "red" else "red"  # 轮方无着即该方负
        return {"sideToMove": turn, "legal": legal, "over": over, "winner": winner}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        try:
            if route == "/" or route == "/index.html":
                return self._file(os.path.join(HERE, "index.html"), "text/html; charset=utf-8")
            if route == "/api/start":
                p = self.position
                return self._json({
                    "fen": p["fen"], "id": p["id"], "source": p.get("source", ""),
                    "sideToMove": p.get("side_to_move", "red"),
                    "maxDtm": p.get("max_dtm"),
                })
            if route == "/api/state":
                moves = self._moves_param(parsed.query)
                return self._json(self._state(moves))
            if route == "/api/black":
                moves = self._moves_param(parsed.query)
                fen = self.position["fen"]
                if side_to_move(fen, moves) != "black":
                    return self._json({"error": "现在不是黑方走"}, 400)
                mv, score = self.engine.best_move(fen, moves, BLACK_NODES)
                if mv == "none":
                    return self._json({"move": None, "score": score,
                                       "state": self._state(moves)})
                after = moves + [mv]
                return self._json({"move": mv, "score": score,
                                   "state": self._state(after)})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 — POC:任何错误都回 JSON 便于前端显示
            return self._json({"error": str(e)}, 500)

    def log_message(self, *args):
        pass  # 静音默认访问日志


def main():
    if not os.path.exists(ENGINE):
        raise SystemExit(f"找不到引擎:{ENGINE}\n请先跑 engine/fetch.sh")
    Handler.engine = Engine(ENGINE)
    Handler.position = load_position()
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"POC 后端已启动: http://127.0.0.1:{port}  (题目 {Handler.position['id']} · {Handler.position.get('source','')})")
    print("Ctrl-C 结束")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收工")


if __name__ == "__main__":
    main()
