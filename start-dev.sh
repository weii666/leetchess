#!/usr/bin/env bash
# 開發用啟動:改動 service/ 的程式或 positions/ 的題目檔即自動重啟服務。
#
# 用法:
#   ./dev.sh                    起在 127.0.0.1:8000
#   ./dev.sh --port 9000        額外參數原樣傳給 uvicorn
#
# 題庫索引只在啟動掛鉤裡掃描一次(`service/main.py` 的 `_lifespan` 呼叫
# `PositionRepository.load()`),之後就固定在記憶體裡。改完題目檔非重啟不可,
# 這個腳本要解的就是那一步。
#
# ## 為什麼 --reload-include 要把 '*.py' 也寫出來
#
# uvicorn 的 `--reload-include` **取代**預設的 `*.py`,不是附加上去。只寫
# '*.json' 的話,監看清單裡就只剩 JSON —— 題目檔改了會重啟,而改 service/ 的
# 程式反而不會,那比完全不開 reload 更難察覺。
#
# ## 為什麼 web/ 不在監看清單裡
#
# 前端由 StaticFiles 掛載,每個請求都重新讀檔,改 JS/CSS 重新整理瀏覽器即可。
# 把它加進來只會讓改一行 CSS 就重啟整個服務 —— 而每次重啟要重開引擎池,
# 每個 Pikafish 進程載入一份 51MB NNUE。
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec uv run uvicorn service.main:app \
  --reload \
  --reload-dir service \
  --reload-dir positions \
  --reload-include '*.py' \
  --reload-include '*.json' \
  "$@"
