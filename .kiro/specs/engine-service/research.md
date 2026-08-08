# Research & Design Decisions — engine-service

## Summary

- **Feature**: `engine-service`
- **Discovery Scope**: New Feature(POC 為一次性驗證工具,非產品形態;本 spec 為新建服務,但沿用 POC 已驗證的 UCI 用法)
- **Key Findings**:
  - **python-chess 不可用** —— `chess.engine` 綁定 `chess.Board`(西洋棋),象棋 FEN 與走法格式不同,無法適配。UCI 封裝必須自建,POC 已有可用雛形。
  - **FastAPI 同步 `def` endpoint 自動跑在 threadpool** —— 引擎等待是阻塞 pipe read,用 `def` 而非 `async def` 即可,無需手動 `run_in_executor`。但 anyio 預設 threadpool 上限會成為隱形併發天花板,須顯式配置。
  - **引擎池不是 `multiprocessing.Pool`** —— pikafish 是長駐子進程,經 stdin/stdout 通訊,需自管借還的資源池(語意近似連線池),而非 fork Python worker。
  - **POC 的 `_read_until` 無逾時** —— 阻塞在 `for line in self.proc.stdout`,引擎若不輸出則執行緒永久卡死。這是產品化必須解掉的第一個技術債。
  - **引擎靜默忽略非法著法** —— 實測證實 Pikafish 對 `position ... moves` 中的非法著法不報錯,直接以實際解析到的局面回應後續指令。5.3 無法靠引擎回報實現,須以 `d` 指令比對實際套用步數。
  - **NNUE 記憶體佔用需驗證** —— 每個引擎進程 `Hash 128MB`,加上 51MB nnue。若 nnue 各進程獨立載入 heap,池大小 N 的記憶體約為 `N × 179MB`,直接決定部署規格。

## Research Log

### 能否採用 python-chess 的 `chess.engine` 模組

- **Context**: Build vs. Adopt —— UCI 協定處理是否已有成熟方案可用,避免自己維護協定層。
- **Sources Consulted**:
  - [python-chess UCI/XBoard engine communication](https://python-chess.readthedocs.io/en/latest/engine.html)
  - [Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish) —— 變體引擎,支援 UCI/UCCI/USI 等協定
- **Findings**:
  - `chess.engine` 的 API 以 `chess.Board` 為中心(`engine.play(board, limit)`),棋盤模型硬綁西洋棋規則
  - 象棋 FEN(10×9 盤面、將士象兵)無法由 `chess.Board` 解析;走法座標系亦不同
  - 生態中處理象棋 UCI 的專案(如 Fairy-Stockfish 週邊工具)各自實作協定層,無通用 Python 套件
- **Implications**: **自建 UCI 封裝**。POC 的 `Engine` 類已驗證三個必要操作(`go perft 1` 取合法著、`go nodes` 取應手、解析 `score (mate|cp)`),約 60 行,遠低於適配通用函式庫的成本。決策記錄見下方 Decision。

### 阻塞子進程 I/O 下的併發模型

- **Context**: 引擎搜尋期間 Python 端阻塞在 pipe read。需決定 web 框架與併發模型,以滿足 3.2(不等待其他請求)與 3.3(達上限時明確回報)。
- **Sources Consulted**:
  - [FastAPI 阻塞操作處理討論](https://github.com/fastapi/fastapi/discussions/8842)
  - [run_in_executor 與 run_in_threadpool 差異](https://sentry.io/answers/fastapi-difference-between-run-in-executor-and-run-in-threadpool/)
- **Findings**:
  - FastAPI 對同步 `def` 路由自動使用 threadpool 執行,不阻塞 event loop;於 `async def` 中執行阻塞碼才是反模式
  - Starlette/anyio 的預設 threadpool 容量有限,未配置時會成為隱形的併發上限
  - 本 use case 的等待屬 I/O(等子進程輸出),不是 Python 端的 CPU-bound,執行緒模型完全適用
- **Implications**: 採 FastAPI + 同步 `def` 路由。真正的併發閘門設在**引擎池的容量**而非 threadpool —— 池滿即為 3.3 的「服務忙碌」判準,語意明確且可控。threadpool 容量須配置為大於池容量,避免兩層限流互相干擾。

### POC 現況與可複用性

- **Context**: 產品化應保留哪些、重寫哪些。
- **Sources Consulted**: `poc/server.py`(203 行)、`poc/README.md`
- **Findings**:
  - **可保留**:UCI 指令序列與輸出解析邏輯(`legal_moves` 走 `go perft 1` 並以正規式取著法、`best_move` 解析 `score (mate|cp) N`)、stateless 用法、`side_to_move` 由起始 FEN 與走法數推導
  - **必須重寫**:單進程加 `threading.Lock`(全序列化)、`_read_until` 無逾時、`POSITION_FILE` 硬編、走法序列走 query string、`except Exception` 直接回傳字串
  - `BLACK_NODES` 預設 200000(≈0.2s),原始碼註記「實測 2M→20k 同一手」
- **Implications**: UCI 協定層是唯一值得原樣移植的部分,其餘全部重建。POC 不再維護。

### 題目載入與分書目錄

- **Context**: 6.1 依 id 載入、6.3 擴充至 500 題不需改程式。steering 已定 `positions/<書名>/<id>.json` 佈局,`id` 全域唯一。
- **Sources Consulted**: `.kiro/steering/structure.md`、`positions/0001.json`
- **Findings**:
  - `id` 全域唯一但檔案分散於各書資料夾,由 id 無法直接推出路徑
  - 500 題 metadata 約 150KB,全部載入記憶體無壓力
- **Implications**: 啟動時遞迴掃描 `positions/` 建立 `id → 題目` 的記憶體索引。新增題目只需放檔案,無需改程式或設定,滿足 6.3。重複 id 屬資料錯誤,應在啟動時偵測並拒絕啟動。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **分層 + 資源池**(選用) | HTTP 層 / 服務層 / 引擎池 / UCI 協定層,依賴單向 | 邊界清楚、引擎池可獨立測試、符合 `tech.md` 的 EngineAdapter 抽象 | 需自行實作池的借還與健康檢查 | 與 steering 的依賴方向一致 |
| 單進程加鎖(POC 現況) | 一個引擎進程配一把鎖 | 實作最簡 | 全序列化,直接違反 3.2 | 已否決 |
| 每請求開新進程 | 請求到達時 spawn pikafish | 無池管理複雜度 | 每次啟動需載入 51MB nnue,延遲不可接受 | 已否決 |
| asyncio 子進程 | `asyncio.create_subprocess_exec` 全非同步 | 無執行緒開銷 | 協定層需重寫為 async;併發量僅數十,無實際收益 | 已否決 |

## Design Decisions

### Decision: 自建 UCI 協定層而非採用現成函式庫

- **Context**: Build vs. Adopt 檢查。
- **Alternatives Considered**:
  1. python-chess `chess.engine` —— 成熟、維護良好
  2. 自建,移植 POC 的 `Engine` 類
- **Selected Approach**: 自建。移植 POC 已驗證的指令序列與解析邏輯,補上逾時與健康檢查。
- **Rationale**: `chess.engine` 的棋盤模型綁定西洋棋,象棋 FEN 無法解析。適配成本高於自建(約 60 行協定碼)。
- **Trade-offs**: 需自行維護協定層,但介面面積極小(三個操作),且 POC 已驗證正確性。
- **Follow-up**: 協定層須有獨立測試,涵蓋 `bestmove (none)`、mate 與 cp 分數、perft 輸出解析。

### Decision: 引擎池容量作為併發閘門

- **Context**: 3.2 要求不等待其他請求,3.3 要求達上限時明確回報而非無限期等待。
- **Alternatives Considered**:
  1. 以 HTTP 層的 threadpool 容量限流
  2. 以引擎池容量限流,threadpool 配置為更大
- **Selected Approach**: 引擎池為唯一閘門。請求向池借用引擎,附帶等待上限;逾時未借到即回報服務忙碌。
- **Rationale**: 引擎進程是真正稀缺的資源(記憶體與 CPU),以它為閘門語意最直接。若以 threadpool 限流,拒絕點與資源實況脫節,且錯誤語意模糊。
- **Trade-offs**: 需自行實作借還與等待逾時,但語意清楚且可觀測。
- **Follow-up**: threadpool 容量必須配置為大於池容量,否則會在池之前先被卡住,產生誤導性的行為。

### Decision: 搜尋逾時採「先 stop 後 kill」兩段式

- **Context**: 4.1 要求逾時中止搜尋,4.2 要求引擎異常後自動恢復。POC 的阻塞讀取無逾時,引擎不輸出即永久卡死。
- **Alternatives Considered**:
  1. 直接 kill 進程並重建
  2. 先送 UCI `stop` 指令,寬限期內未回應再 kill 重建
- **Selected Approach**: 兩段式。逾時先送 `stop`(引擎會回 `bestmove`),寬限期內取得回應則進程可繼續服役;逾期則 kill 並重建。
- **Rationale**: `stop` 是 UCI 標準的正常中止路徑,進程狀態仍健康,重建 51MB nnue 的成本可避免。只有真正卡死才付重建代價。
- **Trade-offs**: 兩段式邏輯較複雜,但避免了每次逾時都重載 nnue。
- **Follow-up**: 需實測 `stop` 後 pikafish 回傳 `bestmove` 的延遲,以決定寬限期長度。

### Decision: 讀取管線加逾時保護

- **Context**: POC 的 `_read_until` 阻塞於 `for line in self.proc.stdout`,無任何逾時。
- **Selected Approach**: 讀取端加上逾時機制(以 pipe 檔案描述符的可讀性輪詢,或以獨立讀取執行緒配佇列),使任何一次等待都有上界。
- **Rationale**: 沒有逾時上界,任何引擎異常都會演變成執行緒洩漏,最終耗盡服務。這是 4.1 與 4.4 的前提。
- **Trade-offs**: 協定層複雜度上升,但這是不可省略的正確性前提。
- **Follow-up**: 實作時須驗證逾時路徑確實可觸發(以人為卡住的假引擎測試)。

### Decision: 序列驗證以 `d` 指令比對實際套用步數

- **Context**: 5.3 要求拒絕走不出的走法序列。原設計假設「引擎會拒絕該局面」,設計驗證階段以本機 native binary 實測後**證實此假設為誤**。
- **實測結果**(`Pikafish-2026-01-02`,《適情雅趣》第 21 局起始局面):

  | 送出 | `go perft 1` 回傳 |
  |---|---|
  | `moves f8f9`(合法) | 2 個合法著法(走後局面) |
  | `moves a1a2`(非法) | 44 個合法著法(**起始局面**) |
  | `moves e0e9`(格式合法但語意荒謬) | 44 個合法著法(**起始局面**) |

  引擎**靜默忽略非法著法,不回報任何錯誤**。此失敗模式比拋錯危險得多:服務會把起始局面的合法著法當成當前局面回傳,使用者看到一盤錯誤的棋而非錯誤訊息。

- **Alternatives Considered**:
  1. 逐步推進驗證 —— 對序列的每一步各送一次 `position` 加 `go perft 1`,確認該著法在合法集合內。N 步需 N 次往返。
  2. 服務端自行實作走子與規則 —— **直接違反 `tech.md` 的不可動搖約束**,不予考慮。
  3. `d` 指令比對 —— 送完整序列後送一次 `d`,由回傳 FEN 的 `side_to_move` 與 fullmove number 推出實際套用步數。
- **Selected Approach**: 方案 3。實測驗證推導規則成立:

  ```
  走 1 步    Fen: ... b - - 1 1      預期 side=b, fullmove=1    相符
  走 3 步    Fen: ... w - - 0 2      預期 side=w, fullmove=2    相符
  非法忽略   Fen: ... w - - 0 1      預期 side=b, fullmove=1    偵測到
  ```

  起始為紅先且 fullmove 為 1 時:走 `N` 步後 `fullmove = 1 + N // 2`,`side` 於 `N` 為偶數時為紅、奇數時為黑。
- **Rationale**: 成本為每次查詢一道額外指令(無搜尋開銷),遠低於方案 1 的 N 次往返;且不觸碰規則實作,符合約束。
- **Trade-offs**: 只能偵測「序列未完整套用」,無法指出是**哪一步**非法。對本產品足夠 —— 前端的每一步都取自服務回傳的合法著法集合,序列不一致代表 client 狀態已損毀,精確定位無實際價值。
- **Follow-up**:
  - **halfmove clock 不可用於此判斷**,它會因吃子重置(實測「走 3 步」該值為 0)
  - 驗證須內建於協定層而非交給呼叫方 —— 呼叫方無從得知引擎忽略了哪一步
  - 測試須斷言回應**不是其他局面的合法著法**,而非只斷言「有錯誤」

### Decision: 走法序列改用請求主體傳遞

- **Context**: POC 以 `GET /api/state?moves=a1a2,b1b2,...` 傳整個走法序列。5.3 要求偵測局面不一致。
- **Selected Approach**: 改用 POST 與 JSON 主體承載題目 id 與走法序列。
- **Rationale**: 長局的走法序列會使 URL 過長(部分代理與伺服器有長度限制);JSON 主體亦便於 Pydantic 做結構化驗證與型別檢查,直接支撐 5.2 的著法格式驗證。
- **Trade-offs**: 語意上是查詢卻用 POST,但這是承載複雜輸入的常見取捨。
- **Follow-up**: 走法序列長度上限屬 Backlog(7.3),本輪不設限,但契約設計不得阻礙日後加入。

## Risks & Mitigations

- **NNUE 記憶體隨池大小線性成長** —— 每進程 `Hash 128MB` 加 51MB nnue,池大小 N 的記憶體上界約 `N × 179MB`。**Pikafish 是否以 mmap 共享 nnue 尚未驗證**;若為各進程獨立載入 heap,4 進程即需約 716MB。緩解:實作階段第一步即實測單進程與多進程的實際 RSS,再定池大小預設值;部署規格以實測為準而非估算。
- **`stop` 後引擎回應延遲未知** —— 兩段式逾時的寬限期長度缺乏實測依據。緩解:寬限期設為可配置,實作時以實測值定預設。
- **併發目標與實際負載模型的落差** —— 3.1 的「30 名同時對局使用者」因排局為回合制,瞬時並行搜尋數遠低於 30(使用者思考期間不發請求)。緩解:池大小依瞬時並行數而非在線人數設定;以等待佇列吸收突發,而非以池大小硬扛。
- **引擎啟動成本影響恢復速度** —— 崩潰重建需重新載入 nnue,期間該槽位不可用。緩解:4.3 已要求以其餘可用容量繼續服務;池應在背景重建,不阻塞當前請求。
- **題目 id 重複** —— 分書目錄下 id 全域唯一由人工保證,無機制強制。緩解:啟動掃描時偵測重複並拒絕啟動,使錯誤在部署階段暴露而非執行期。

## References

- [python-chess UCI/XBoard engine communication](https://python-chess.readthedocs.io/en/latest/engine.html) —— 確認 `chess.engine` 綁定 `chess.Board`,不適用象棋
- [FastAPI 阻塞操作處理討論](https://github.com/fastapi/fastapi/discussions/8842) —— 同步 `def` 路由與 threadpool 行為
- [run_in_executor 與 run_in_threadpool 差異](https://sentry.io/answers/fastapi-difference-between-run-in-executor-and-run-in-threadpool/) —— 兩者取捨
- [Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish) —— 變體引擎生態,佐證象棋 UCI 無通用 Python 封裝
- `poc/server.py` —— UCI 用法的既有驗證與可複用邏輯
- `.kiro/steering/tech.md` —— 三個不可動搖的約束、引擎調用慣例、uv + venv 規範
- `.kiro/steering/structure.md` —— `positions/` 分書佈局與題目 schema
