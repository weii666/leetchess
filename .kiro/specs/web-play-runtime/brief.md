# Brief: web-play-runtime

## Problem

`poc/index.html` 已經能下完一整局,但它是為單機驗證寫的:寫死一題、無悔棋、錯誤處理只有把後端回的字串印出來、每次操作都假設後端瞬間回應。要變成真實使用者用的對局介面,缺的不是棋盤而是**互動的完整性** —— 悔棋、等待狀態、網路失敗的復原、跨題複用。

## Current State

`poc/index.html`(334 行,vanilla JS + SVG)已具備:

- SVG 棋盤繪製、FEN 解析與盤面渲染
- 選子 → 藍點顯示合法落點(合法著由後端 `go perft 1` 給,前端不判規則)
- UCI 轉中文記譜(`uci2cn`,含縱線序號、進退平、同線前後子判別)
- 三態諮詢信號顯示(即將紅勝 / 即將黑勝 / 未知中立)
- 歷史著法列表、重來按鈕、勝負橫幅

**尚未有**:悔棋、`(fen, moves) → bestmove` 的 session memo、引擎思考中的等待狀態與可取消、網路失敗的重試與復原、多題切換、行動裝置版面。

## Desired Outcome

在瀏覽器中完成一整局練習:載入題目 → 執紅走子 → 後端引擎執黑應手 → 真終局停局判勝負,過程中三態信號即時更新,可悔棋、可重來,網路慢或斷線時使用者清楚知道發生什麼事而不是畫面卡住。

## Approach

從 `poc/index.html` 移植已驗證的棋盤、記譜與信號邏輯,重點補三件 POC 沒處理的事:

1. **悔棋** —— 砍 `moves` 尾巴重送。配 session 內 `(fen, moves) → bestmove` memo,退一步重走同一步為零成本,也避免「退回重走卻換手」的困惑(`tech.md`:黑方在最頑強一檔內變化,單次挑戰內的穩定性由前端 memo 負責)。memo 在前端,後端保持無 session 狀態。
2. **非同步與失敗** —— 每一手都是網路往返,引擎思考期間須有明確的等待狀態;逾時、斷線、後端錯誤都要有可復原的處理路徑,不能讓對局卡在不可知狀態。
3. **跨題複用** —— 題目由外部傳入(id 或 FEN),不再硬編。

停局條件只認真終局(輪方合法著數 = 0),三態信號僅為諮詢、不決定停局 —— 信號報錯的代價只是一次 UI 抖動,不會誤判勝負。

## Scope

- **In**: 棋盤渲染與走子互動、合法著提示、對局狀態機(走法序列、輪方、終局)、真終局停局與勝負顯示、三態諮詢信號、悔棋與 session memo、中文記譜、重來、引擎思考中的等待與取消、網路失敗處理、行動裝置可用的版面
- **Out**: 後端 API 的實作(屬 engine-service)、跨題導航與進度紀錄(屬 problem-browser)、題目 schema(屬 position-corpus)、判定表與移動級走脫回饋(後續 phase)、`poc/` 的後續維護

## Boundary Candidates

- 後端 API client(請求、逾時、重試、錯誤模型)
- 對局狀態機(走法序列、輪方、終局、悔棋)
- 棋盤呈現與互動(SVG 棋盤、選子、合法點)
- 輔助呈現(中文記譜、三態信號、歷史著法)

## Out of Boundary

- **不自實作任何象棋規則** —— 合法著與勝負一律來自後端(`tech.md`「勝負判定不自己實作」),尤其不碰循環規則
- 不持有題目資料的定義權,只消費
- 不決定要載入哪一題
- 不實作判定表,但**走脫回饋的來源必須設計成可插拔**:三態信號是目前唯一來源,日後判定表是新增來源而非改寫流程

## Upstream / Downstream

- **Upstream**: engine-service(HTTP API 契約)、position-corpus(FEN 與 metadata)、`poc/index.html`(移植來源)
- **Downstream**: problem-browser(嵌入或導向本 client)、service-deploy-ops(打包發布)、後續 phase 的 verdict-table(接進走脫回饋插槽)

## Existing Spec Touchpoints

- **Extends**: 無
- **Adjacent**: 與 engine-service 共享 HTTP API 契約,契約已於 engine-service 的 design 階段定案(走法序列改走請求主體,不再是 query string)。與 problem-browser 的邊界在「單題內迴圈 vs 跨題導航」

> **契約使用約束(來自 engine-service design)**:使用者走完一手紅方著法後,**直接呼叫 `/api/black-move`,不得先呼叫 `/api/state` 確認終局** —— 前者的回應已涵蓋「紅方這一手就將死黑方」的情況(黑方著法為空、局面結束、勝方為紅),而那正是每一題排局的最後一手。`/api/state` 只用於重建狀態(悔棋、頁面重整、載入題目)。
>
> 這不是風格偏好:每手多一次呼叫會讓通過後端併發閘門的次數加倍,直接壓縮服務可承載的同時使用者數。

## Constraints

- 每一手都有網路往返:引擎搜尋約 0.2s(native,`go nodes 200000`)加 RTT,UI 必須非同步且有等待狀態
- 走法內部一律 UCI 座標,中文記譜只在顯示層。檔 `a`–`i`(紅方左至右),列 `0`–`9`(紅方底線為 0)
- 後端無 session 狀態,對局狀態完全由前端持有並每次重送 —— 這是悔棋與重來能做得簡單的原因,也意味著前端是狀態的唯一真相
- 三態信號會抖、未知偏多是預期行為(`product.md`),UI 呈現須讓使用者理解它是諮詢而非判決
- 繁體中文介面
