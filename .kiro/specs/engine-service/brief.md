# Brief: engine-service

## Problem

`poc/server.py` 已經證明「HTTP 包住 native Pikafish」這條路走得通,但它是一次性驗證工具,離能承載真實使用者還差得遠:**單一引擎進程配一把 `threading.Lock`,所有請求全序列化**。兩個使用者同時下棋,第二個就得排隊等第一個搜完。任何錯誤都直接把 exception 字串回給前端。沒有逾時、沒有資源上限、沒有濫用防護。

這是整個產品的心臟,也是後端架構下唯一會被真實流量打到的元件。

## Current State

`poc/server.py`(203 行)已具備的雛形:

- `Engine` 類是 `tech.md` 所定 `EngineAdapter` 介面的本地實作:stateless 用法,每次重送 `position fen <fen> moves <...>`
- `legal_moves()` 走 `go perft 1`,解析輸出取合法著;空 list 即該方無著可走
- `best_move()` 走 `go nodes N`,回傳 bestmove 與 `score (mate|cp) N`
- 端點:`GET /api/start`、`GET /api/state?moves=...`、`GET /api/black?moves=...`
- `BLACK_NODES` 預設 200000(≈0.2s),註記「實測 2M→20k 同一手」
- 啟動時 `setoption name Threads value 1`、`Hash value 128`

已知不足:單進程序列化、`POSITION_FILE` 硬編為 `positions/0001.json`、走法序列走 query string(長局會爆長)、`except Exception` 直接回傳字串、無逾時、無 rate limit、引擎崩潰無重啟。

## Desired Outcome

一個能承載並發使用者的引擎服務:

- 多人同時下棋不互相排隊,單一請求的延遲穩定可預期
- 引擎崩潰或卡死能自動恢復,不會整個服務躺平
- API 契約明確、版本化、有錯誤碼,不是把 Python exception 丟給前端

> **本輪不做**:濫用防護、`ENGINE_VERSION` 啟動校驗、可觀測性(健康端點與結構化日誌)已列為 backlog,見 `requirements.md` 的 `## Backlog` 段落與各項的「撿起來的時機」。

## Approach

保留 POC 已驗證的 UCI 用法(stateless、`go perft 1` 取合法著、`go nodes` 取應手),把周邊工程補齊:以**引擎進程池**取代單進程加鎖,每個請求借一個進程用完歸還;加上單請求逾時與進程健康檢查(崩潰即重建)。API 契約重新設計,走法序列改用 POST body 而非 query string。

`Threads value 1` 維持不變 —— 併發靠多進程而非單進程多執行緒,這樣資源可預測、單請求延遲穩定,也符合 POC 實測「算力需求極低」的觀察。

## Scope

- **In**: 引擎進程池與生命週期(啟動、健康檢查、崩潰重建)、UCI 封裝(合法著、應手、mate 分數解析)、HTTP API 契約定案與錯誤模型、單請求逾時、併發上限與排隊策略、題目載入(依 id 而非硬編)
- **Out**: **濫用防護與 rate limit、`ENGINE_VERSION` 啟動校驗、健康端點與結構化日誌(已列 backlog,本輪不實作)**、部署、託管平台、監控告警(屬 service-deploy-ops)、前端任何內容(屬 web-play-runtime)、題目 schema(屬 position-corpus)、離線題目驗證工具(屬 corpus-verification)、判定表查詢(後續 phase)

## Boundary Candidates

- 引擎進程封裝與進程池(資源管理)
- UCI 協定層(合法著、應手、分數解析)
- HTTP API 層(契約、錯誤模型、序列化)
- 資源控制層(併發上限、單請求逾時)—— rate limit 屬 backlog,不在本輪

## Out of Boundary

- **不自實作任何象棋規則** —— 合法著與勝負一律由引擎給(`tech.md`「勝負判定不自己實作」),尤其不碰循環規則
- 不擁有部署設定與營運監控
- 不擁有題目內容或 schema,只依 id 讀取
- 不實作判定表,但 API 契約須為「日後新增走脫判定來源」留下擴充空間,而非日後改寫

## Upstream / Downstream

- **Upstream**: `poc/server.py`(產品化來源)、`engine/ENGINE_VERSION` 與 `engine/fetch.sh`(binary 與版本鎖)、position-corpus(題目資料)
- **Downstream**: web-play-runtime(唯一 client)、service-deploy-ops(部署此服務)、後續 phase 的 verdict-table(判定表查詢很可能掛在此服務上)

## Existing Spec Touchpoints

- **Extends**: 無(取代原本已刪除的 pikafish-wasm-toolchain 在依賴圖中的位置)
- **Adjacent**: corpus-verification 也封裝 native 引擎,但用途相反(離線長搜 vs 互動快搜)。是否共用同一套 UCI 封裝須在 design 階段決定,不要為了「看起來該共用」而硬綁

## Constraints

- 引擎完全 stateless:悔棋/跳步/重來都只是重送一次完整局面,這是進程池能成立的前提
- `go nodes N` 而非 `go movetime` —— 目的是品質下限,保證黑方走在最頑強一檔而非負載一變就淺搜走出弱手(`tech.md`)
- 黑方應手允許在「最頑強的一檔」之內變化;單次挑戰內的穩定性由前端 memo 負責,**後端不持有 session 狀態**
- 每個引擎進程 `Hash 128MB`,進程池大小直接決定記憶體佔用,須與部署資源上限一起算
- 引擎版本或 nnue 一換,依 `tech.md` 的引擎版本鎖定,所有下游產出物須重新產生
- Python 環境須遵循 `tech.md` 的 uv + venv 規範(`uv run`,不直接 `python3`)
