# Requirements Document

## Project Description (Input)

將 `poc/server.py` 產品化為 leetchess 的引擎後端服務。

**誰有問題**:leetchess 的所有使用者,以及依賴此服務的前端對局 client(web-play-runtime)。此服務是後端架構下唯一會被真實流量打到的元件,也是整個產品的心臟 —— 它一旦排隊或崩潰,產品就不可用。

**現況**:`poc/server.py`(203 行)已驗證「HTTP 包住 native Pikafish」這條路走得通,並確立了關鍵用法 —— 引擎完全 stateless(每次重送 `position fen <fen> moves <...>`)、合法著與終局走 `go perft 1`、黑方應手走 `go nodes N` 並解析 `score (mate|cp) N`。但它是一次性開發驗證工具,距離承載真實使用者仍有明確落差:

- **單一引擎進程配一把 `threading.Lock`,所有請求全序列化** —— 兩個使用者同時下棋,第二個必須等第一個搜完
- `POSITION_FILE` 硬編為 `positions/0001.json`,無法依題目 id 載入
- 走法序列以 query string 傳遞(`/api/state?moves=...`),長局會爆長
- `except Exception` 直接把 Python 例外字串回給前端,沒有錯誤模型
- 無單請求逾時、無併發上限、無 rate limit
- 引擎進程崩潰或卡死後無法恢復
- 未在啟動時校驗 `engine/ENGINE_VERSION`

**該改變什麼**:補齊周邊工程,使其成為能承載並發使用者的服務 —— 以引擎進程池取代單進程加鎖,使多人同時下棋不互相排隊且單請求延遲穩定可預期;加入單請求逾時與進程健康檢查,崩潰能自動重建而非整個服務躺平;重新定案 API 契約(走法序列不再走 query string),提供明確的錯誤碼而非例外字串;加入 rate limit 與併發上限,防止公開引擎 API 被當成免費分析服務刷爆;啟動時校驗引擎版本與 `ENGINE_VERSION` 一致。

POC 已驗證的 UCI 用法全部保留。`Threads value 1` 維持不變 —— 併發靠多進程而非單進程多執行緒,如此資源可預測、單請求延遲穩定,也符合 POC 實測「2M nodes 與 20k nodes 同一手」所顯示的低算力需求。

**邊界**:本服務不自實作任何象棋規則(合法著與勝負一律由引擎判定,尤其不碰循環規則);不持有 session 狀態(產品不提供悔棋,同一局面不會被重複請求,故無須任何應手穩定性保證);不擁有題目 schema、部署設定與營運監控;不實作走脫判定表,但 API 契約須為日後新增走脫判定來源預留擴充空間。

詳細背景、範圍界線與約束見同目錄 `brief.md`,專案級決策見 `.kiro/steering/roadmap.md`。

## Introduction

engine-service 是 leetchess 的引擎後端。使用者執紅練習象棋排局,黑方由伺服器端的 native Pikafish 即時計算應手;合法著法與勝負判定同樣由引擎給出,服務本身不實作任何象棋規則。

本服務的產品行為由兩項已定案的決定框定:

1. **不做走脫判定表。** 對局一路下到分出勝負為止 —— 只在真終局(輪方無合法著法)停局,不提早介入、不強迫重來。
2. **保留三態諮詢信號。** 服務在回傳黑方應手時一併給出評分狀態(紅方即將取勝 / 黑方即將取勝 / 未搜得殺著),供前端顯示。此信號取自 live 引擎、不需要任何離線表,且**不決定停局** —— 它報錯的代價只是一次顯示抖動,不會誤判勝負。

服務規模目標為**小眾公開**:數十名使用者同時對局。因為排局對局是回合制、使用者思考期間不發請求,「同時對局的使用者數」與「同時進行中的搜尋數」相差一個數量級,兩者在下列準則中分別表述。

## Boundary Context

- **In scope**:依題目載入起始局面、查詢任一局面的合法著法、判定真終局與勝負方、計算黑方應手並回傳三態評分、併發處理與忙碌回報、單請求逾時、引擎失效後的自動恢復、可判別的錯誤模型。
- **Out of scope**:**濫用防護、引擎版本校驗、可觀測性(見 `## Backlog`,本輪不實作)**;走脫判定表與移動級「就是這一步」回饋(後續 phase);對局 session 狀態(由前端持有;產品不提供悔棋,故無悔棋記憶與應手穩定性需求);題目 schema 與題庫內容(position-corpus);題目真偽驗證與 `max_dtm` / `solvable` 的產出(corpus-verification);託管平台、資源配額與監控告警設定(service-deploy-ops);使用者帳號、練習進度與任何個人資料。
- **Adjacent expectations**:前端持有完整對局狀態,每次請求重送題目識別碼與完整走法序列,服務不記憶任何一局的進度;題庫由 position-corpus 提供,其中 `solvable` 為 false 的題目不應被送到本服務;部署層的資源上限與對外速率策略由 service-deploy-ops 配置,本服務只負責在達到上限時表現正確。

## Requirements

**本輪實作範圍為 Requirement 1 至 6。** Requirement 7、8、9 已移至 `## Backlog`,不在本輪範圍。

### Requirement 1: 對局推進與勝負判定

**Objective:** As a 前端對局 client, I want 取得任一局面的合法著法、黑方應手與終局結果, so that 使用者能把一題排局一路下到分出勝負

#### Acceptance Criteria

1. When 收到帶有題目識別碼與走法序列的局面查詢請求, the 引擎服務 shall 回傳該局面輪方的所有合法著法。
2. When 局面輪方的合法著法數為 0, the 引擎服務 shall 回報對局已結束,並指出該輪方為負方。
3. While 對局尚未達到真終局, when 收到局面查詢請求, the 引擎服務 shall 回報對局仍在進行中,不得以任何其他條件宣告對局結束。
4. When 收到黑方應手請求且該局面輪方為黑方, the 引擎服務 shall 回傳一著黑方著法與該局面的評分狀態。
5. If 收到黑方應手請求但該局面輪方為紅方, then the 引擎服務 shall 拒絕該請求並指出輪方不符。
6. The 引擎服務 shall 使合法著法與勝負判定結果與專案所鎖定引擎版本的判定完全一致,包含長將、長捉、一將一殺等循環規則局面。

### Requirement 2: 三態諮詢信號

**Objective:** As a 使用者, I want 在對局中看到目前殺勢的參考狀態, so that 我能感知自己是否還保持著必勝走勢,而不需要系統替我中斷對局

#### Acceptance Criteria

1. When 回傳黑方應手, the 引擎服務 shall 一併回傳三種評分狀態之一:紅方即將取勝、黑方即將取勝、未搜得殺著。
2. When 引擎回報的評分為紅方即將取勝, the 引擎服務 shall 一併提供殺著倒數的步數。
3. When 引擎在該次搜尋中未搜得殺著, the 引擎服務 shall 回報「未搜得殺著」,不得以其他評估數值推斷勝負傾向。
4. The 引擎服務 shall 不因評分狀態為任何值而改變對局是否結束的判定。

### Requirement 3: 併發服務能力

**Objective:** As a 使用者, I want 在其他人同時使用服務時仍能正常下棋, so that 我的每一手不會因為別人正在思考而卡住

#### Acceptance Criteria

1. The 引擎服務 shall 在 30 名使用者同時進行對局的情況下維持正常服務。
2. While 進行中的搜尋數未達服務設定的併發上限, when 收到新的請求, the 引擎服務 shall 立即開始處理該請求,不等待其他請求完成。
3. While 進行中的搜尋數已達併發上限, when 收到新的請求, the 引擎服務 shall 在服務設定的等待上限內開始處理,或回報服務忙碌,不得無限期等待。
4. While 進行中的搜尋數未達併發上限, the 引擎服務 shall 使單一黑方應手請求的回應時間不超過無其他併發請求時的 2 倍。

### Requirement 4: 逾時與失效恢復

**Objective:** As a 服務操作者, I want 單次失敗不會拖垮整個服務, so that 我不需要人工重啟就能維持服務可用

#### Acceptance Criteria

1. If 單次引擎搜尋超過服務設定的時間上限, then the 引擎服務 shall 中止該次搜尋並回報逾時。
2. If 引擎在處理請求時異常終止或停止回應, then the 引擎服務 shall 自動恢復處理後續請求的能力,無需人工介入。
3. While 部分引擎處理能力正在恢復, when 收到新的請求, the 引擎服務 shall 以其餘可用能力繼續提供服務。
4. The 引擎服務 shall 在任一單次請求失敗後維持整體可用,不因單次失敗而停止服務。

### Requirement 5: 錯誤回報

**Objective:** As a 前端對局 client, I want 收到可程式判別的錯誤, so that 我能對使用者顯示合適的訊息並提供可復原的操作

#### Acceptance Criteria

1. If 請求因任何原因無法完成, then the 引擎服務 shall 回傳可由 client 程式判別的錯誤類別。
2. If 請求包含格式不合法的著法, then the 引擎服務 shall 拒絕該請求並指出不合法的著法。
3. If 請求的走法序列在該題目起始局面下無法完整走出, then the 引擎服務 shall 拒絕該請求並回報局面不一致。
4. The 引擎服務 shall 不在錯誤回應中包含內部檔案路徑、堆疊追蹤或引擎原始輸出。

### Requirement 6: 題目載入

**Objective:** As a 前端對局 client, I want 依題目識別碼取得起始局面, so that 使用者能練習題庫中的任一題而非寫死的單一題目

#### Acceptance Criteria

1. When 收到指定題目識別碼的起始局面請求, the 引擎服務 shall 回傳該題目的起始局面與對局所需的題目資訊。
2. If 請求的題目識別碼不存在, then the 引擎服務 shall 回報找不到該題目。
3. The 引擎服務 shall 支援題庫擴充至 500 題而不需要修改服務的程式或設定。

## Backlog(現階段不實作)

以下需求**不在本輪實作範圍**,列出供參考與日後規劃,性質等同 backlog 項目。設計與任務階段**不得**為這些需求產生元件或任務。

編號沿用 7、8、9 且不重編 —— 日後決定實作時直接搬回 `## Requirements` 即可,既有的追溯關係不受影響。

### Requirement 7: 濫用防護

**Objective:** As a 服務操作者, I want 服務不被當成免費分析工具刷爆, so that 營運成本可控且一般使用者不受影響

**撿起來的時機**:服務公開上線前。公開的引擎 API 缺乏速率限制會被當成免費分析服務,這在 service-deploy-ops 的波次必須解決。

#### Acceptance Criteria

1. While 單一來源的請求速率超過服務設定的上限, when 收到該來源的新請求, the 引擎服務 shall 拒絕該請求並回報速率超限。
2. The 引擎服務 shall 限制單次請求可要求的搜尋量不超過服務設定的上限。
3. If 請求的走法序列長度超過服務設定的上限, then the 引擎服務 shall 拒絕該請求。
4. When 因速率或規模限制而拒絕請求, the 引擎服務 shall 使拒絕原因可與其他錯誤類別區分。

### Requirement 8: 引擎版本一致性

**Objective:** As a 服務操作者, I want 確保線上跑的引擎與專案鎖定的版本一致, so that 題目驗證結果與線上行為不會對不上

**撿起來的時機**:corpus-verification 產出可信的 `max_dtm` 與 `solvable` 之後。在那之前線上引擎版本與驗證結果對不上並無實際後果;之後就有了。

#### Acceptance Criteria

1. When 服務啟動, the 引擎服務 shall 校驗所使用的引擎與評估網路是否與專案鎖定的版本一致。
2. If 校驗發現版本或內容不一致, then the 引擎服務 shall 拒絕啟動並指出不一致之處。
3. The 引擎服務 shall 可供操作者查詢目前執行中的引擎版本與評估網路版本。

### Requirement 9: 可觀測性

**Objective:** As a 服務操作者, I want 掌握服務健康狀態與請求狀況, so that 我能在使用者回報之前發現問題

**撿起來的時機**:有真實使用者流量時。開發與自用階段直接看終端輸出即可,不需要健康端點與結構化日誌。

#### Acceptance Criteria

1. When 處理任一請求, the 引擎服務 shall 記錄該次請求的結果與耗時。
2. The 引擎服務 shall 提供可供監控查詢的健康狀態,反映目前引擎處理能力是否可用。
3. If 引擎處理能力低於服務設定的下限, then the 引擎服務 shall 在健康狀態中回報為不健康。
4. The 引擎服務 shall 不在日誌中記錄可識別個別使用者身分的資訊。
