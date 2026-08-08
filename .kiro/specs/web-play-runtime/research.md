# Research & Design Decisions — web-play-runtime

## Summary

- **Feature**: `web-play-runtime`
- **Discovery Scope**: Extension —— 上游契約已實作完成且經實測,技術選型與交付方式已由使用者定案,`poc/index.html` 提供可移植的既有資產。Discovery 集中在兩個未決問題:測試策略與狀態機的職責切分。
- **Key Findings**:
  - **測試策略是 vanilla JS 決定的直接後果。** 不引入 node 工具鏈等於沒有 JS 測試框架。`playwright` 有 Python 套件(1.62.0,需 Python ≥3.10),由 uv 管理、自帶瀏覽器 binary,**專案因此仍是純 Python**;純函式可用 `page.evaluate()` 在瀏覽器內執行並斷言。
  - **POC 有六個可直接移植的函式**,合計約 150 行:`parseFen`、`applyMove`、`drawGrid`、`render`、`uci2cn`、`renderMoves`。它們都是純呈現邏輯,與 API 形狀無關,不受契約變更影響。
  - **POC 的 API 層必須整份丟棄。** `api()`、`humanMove()`、`start()` 綁死舊端點與 query string 傳參,且沒有逾時、取消與錯誤模型 —— 那正是本 spec 要補的三件事之一。
  - **後端已完成且經實測**,契約不是紙上約定:三個端點、七種錯誤類別碼、`mate_in` 可能為 0、`move` 為 null 時 signal 仍有值,全部有實際回應樣本可對照。

## Research Log

### vanilla JS 的測試策略

- **Context**: steering 定案「不引入任何框架與 node 工具鏈」,而 requirements 有 38 條 AC 需要驗證。沒有 node 就沒有 vitest / jest。
- **Sources Consulted**: PyPI 的 `playwright` 套件 metadata(1.62.0,`requires_python >=3.10`)
- **Findings**:
  - `playwright` 的 Python 綁定與 Node 版功能對等,瀏覽器 binary 由 `playwright install` 下載,與 node 生態無關
  - 既有測試套件已是 pytest(335 個測試),Playwright 可直接併入同一次 `uv run pytest`
  - `page.evaluate()` 可在瀏覽器內執行任意 JS 並取回結果,故**純函式也能測**,不必為了可測性把邏輯搬離瀏覽器
- **Implications**: 測試分兩層 —— 純函式(記譜、FEN 解析)以 `page.evaluate()` 驗證;互動與失敗路徑以真實瀏覽器操作驗證。後端可用真實服務或路由攔截(`page.route()`)偽造回應,後者讓逾時、斷線、503 等難以自然觸發的路徑變得可測。

### POC 的可移植性盤點

- **Context**: brief 說「從 `poc/index.html` 移植」,需確認哪些真的能用、哪些必須重寫。
- **Sources Consulted**: `poc/index.html`(334 行)
- **Findings**:

  | 函式 | 判定 | 理由 |
  |---|---|---|
  | `parseFen` / `applyMove` | **移植** | 純資料轉換,與 API 無關 |
  | `drawGrid` / `render` | **移植** | SVG 棋盤繪製與盤面渲染,已驗證可用 |
  | `uci2cn` | **移植** | 中文記譜,含縱線序號、進退平、同線前後子判別 —— 這段邏輯繁瑣且已驗證,重寫風險高於移植 |
  | `renderMoves` | **移植** | 歷史著法列表 |
  | `selectPiece` / `setBanner` / `updateStatus` | **部分移植** | 互動骨架可用,但需接上新的狀態機 |
  | `renderSignal` | **重寫** | POC 讀的是原始 `score` 物件;新契約給的是已分類的 `signal` 與 `mate_in`,前端不再需要自己判斷 mate 正負 |
  | `api` / `humanMove` / `start` | **丟棄** | 綁死舊端點與 query string,無逾時、無取消、無錯誤模型 |

- **Implications**: 移植約 150 行純呈現邏輯,重寫 API 層與狀態機。`renderSignal` 的重寫其實是簡化 —— 分類已由後端完成(engine-service 的 `classify_score`),前端只做呈現。

### 上游契約的實際樣本

- **Context**: 契約不是紙上約定,後端已實作完成並可啟動,應以實際回應為準。
- **Sources Consulted**: engine-service 的實測記錄與 `service/models.py`
- **Findings**:

  ```
  GET  /api/positions/1     → id, title, description, fen, side_to_move,
                              difficulty, tags, max_dtm, solvable, source,
                              state{side_to_move, legal_moves, over, winner}
  POST /api/state           → side_to_move, legal_moves, over, winner
  POST /api/black-move      → move, signal, mate_in, state{...}
  錯誤                       → {code, message},code 為七種之一
  路由層 404/405             → {"detail": ...}(框架原生,不在契約內)
  ```

- **Implications**: 前端的錯誤處理必須容得下兩種形狀。`state` 巢狀在兩個回應中,型別相同,前端應以同一個轉換函式處理。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **分層模組 + 單向依賴**(選用) | api / notation / board 為葉,game 持狀態,app 組裝 | 職責清楚、純函式可獨立測試、與後端同構 | 需自行維持依賴紀律(無建置工具可強制) | 以 ES modules 表達,無建置步驟 |
| 單一檔案(POC 現況) | 全部塞進 `index.html` | 零結構成本 | 334 行已接近可讀上限,補上狀態機與失敗處理後會失控 | 已否決 |
| 事件匯流排 | 模組間以事件解耦 | 擴充彈性 | 單題對局的狀態極小,事件流反而讓「現在是什麼狀態」難以追蹤 | 過度設計,已否決 |

## Design Decisions

### Decision: 以 Playwright(Python)作為前端測試手段

- **Context**: vanilla JS 無 node 工具鏈,但有 38 條 AC 要驗證。
- **Alternatives Considered**:
  1. 引入 node + vitest —— 違反 steering 的「不引入 node 工具鏈」
  2. 不寫自動測試,全靠手動 —— 38 條 AC 無法靠手動維持
  3. Playwright 的 Python 綁定
- **Selected Approach**: 方案 3。併入既有 pytest 套件,以 `uv` 管理。
- **Rationale**: 唯一同時滿足「無 node 工具鏈」與「AC 可自動驗證」的選項。且真實瀏覽器測試對本功能特別合適 —— 要驗的多是互動與失敗路徑,那些在 jsdom 這類模擬環境中本來就不可靠。
- **Trade-offs**: 瀏覽器 binary 約數百 MB,首次安裝較慢;測試比純單元測試慢。以 `page.route()` 攔截後端可讓多數測試不需真的啟動服務,抵銷大部分成本。
- **Follow-up**: 需確認 Playwright 的瀏覽器下載在目標開發環境可行;若不可行則整個測試策略需重議。

### Decision: 狀態機持有走法序列,呈現層無狀態

- **Context**: 後端無 session,前端是對局狀態的唯一真相(requirements 3.5)。
- **Selected Approach**: `game.js` 持有唯一的可變狀態(題目、走法序列、當前局面、信號、等待態);`board.js` 與記譜層只接受資料並繪製,自身不記憶任何東西。
- **Rationale**: 唯一真相集中於一處,使「重來」「狀態重建」「失敗後復原」都退化成同一個操作 —— 換掉走法序列後重繪。若呈現層各自持有狀態,這三條路徑會各自需要清理邏輯。
- **Trade-offs**: 每次變更都要整體重繪。單題棋盤只有 90 個交叉點,重繪成本可忽略。

### Decision: 靜態檔由後端掛載,與 API 同源

- **Context**: 使用者定案「由 engine-service 掛靜態檔」。
- **Selected Approach**: `web/` 的內容由本 spec 擁有;`service/main.py` 中掛載它的那一小段亦由本 spec 加入。engine-service 只提供掛載點,不擁有前端內容。
- **Rationale**: 同源免去 CORS,本地開發只需啟動一個進程。engine-service 的 design 已同步調整 Out of Boundary。
- **Trade-offs**: 本 spec 會碰一個 `service/` 底下的檔案,是唯一的跨目錄改動,需在任務中明列為整合點而非隱藏其中。

## Risks & Mitigations

- **Playwright 瀏覽器下載失敗或環境不允許** —— 整個測試策略失效。緩解:實作第一個任務即驗證 `playwright install` 可行,失敗則立即回報而非繼續往下做。
- **移植的 `uci2cn` 帶有 POC 的既有缺陷** —— 中文記譜的同線前後子判別繁瑣,POC 未必完全正確,而移植會把缺陷一併帶入。緩解:移植時為其補上針對性測試(含同線雙車、雙馬、兵的情況),不預設 POC 是對的。
- **取消語意在後端無對應** —— 前端取消只是不再等待回應,後端仍會跑完那次搜尋並佔用池容量。緩解:requirements 6.3 只要求前端回到送出前的局面,不要求後端中止;但需在 design 中寫明此不對稱,避免日後誤以為取消能省下後端資源。
- **`mate_in = 0` 被 JavaScript 的 falsy 判斷吞掉** —— 這是上游明列的陷阱,而 JS 的 `if (mateIn)` 對 0 為假,比 Python 更容易踩。緩解:呈現層一律以 `!= null` 判斷,並為此補一條專門的測試。

## References

- [playwright · PyPI](https://pypi.org/project/playwright/) —— 1.62.0,`requires_python >=3.10`,Python 綁定無 node 依賴
- `poc/index.html` —— 可移植資產的來源
- `.kiro/specs/engine-service/design.md` —— API Contract、錯誤類別表、`mate_in` 與 `move` 的獨立性
- `.kiro/steering/tech.md` —— 前端技術決定、三個不可動搖的約束
- `.kiro/steering/structure.md` —— `service/` 與 `web/` 的交界
