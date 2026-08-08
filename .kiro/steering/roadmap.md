# Roadmap

## Overview

leetchess 是象棋排局(紅先必勝)練習程式:使用者執紅,Pikafish 執黑當陪練。交付形態是 **service —— 開頁面就能練,不安裝**。架構為**輕前端 + 引擎後端**:native Pikafish 跑在伺服器,前端只負責棋盤與互動。

repo 內已有本地 POC(`poc/`:Python 小後端包住 native pikafish + 單頁棋盤,單題可玩),已驗證「人機對弈 + 真終局判定」跑得通。**本輪的核心工作就是把這個 POC 產品化**,而不是換一套技術。`poc/DESIGN.md` 仍是主要設計參考,但其 §1 的「純前端 WASM、無後端」已被推翻(理由見下)。

本 roadmap 拆成 6 個新 spec,採**垂直切片優先**:每一波都有可交付物,第二波結束時即可線上試玩。走脫判定表(離線預解)依 DESIGN §6 的決定列為後續 phase,不在本輪範圍。

## Approach Decision

- **Chosen**: **native Pikafish + 後端 API**,前端為輕量 client。分解採垂直切片優先,3 波交付。
- **Why**:
  - DESIGN §2 否決後端的原句理由是「唯一需要它的是 fallback 路徑,而 fallback 已被判定表消除」。§6 決定判定表延後、改用 live 引擎三態信號後,引擎從 fallback 變成**核心**,這條否決理由**已失效**。
  - 後端 API 完全符合 DESIGN「交付形式定為 service,不安裝」—— 被推翻的只有「無後端、靜態託管」這一句,交付形態未變。
  - POC 已經跑通同一套架構(`poc/server.py` 203 行),產品化路徑最短,風險最低。
  - 使用者端零下載:純前端路線需要在瀏覽器下載 **51MB 的 `pikafish.nnue`**,後端路線首屏幾乎瞬開,手機體驗差距尤其大。
  - 引擎跑 native 全速(WASM 約為 native 的 1/3–1/2),`go nodes` 可以開得更高,直接改善 DESIGN §6 自承的「三態信號未知偏多」問題。
  - DESIGN §9 的 `EngineAdapter` 抽象本來就預留這條路:「若日後需要後端…換掉這一個檔案即可。」
- **Rejected alternatives**:
  - **純前端 WASM(多執行緒)** —— 原本的計畫。查證後確認官方 release 與 CI **都不產出 wasm 構件**(CI matrix 只有 x86-64、macOS universal、Android arm64),npm 與 GitHub 也無可信的社群 port,只能自建;且官方 Makefile 的 wasm32 分支硬帶 `-pthread`,需 SharedArrayBuffer,連帶要求 COOP/COEP,GitHub Pages 直接出局。自建工具鏈 + 51MB 首載 + 瀏覽器相容性,成本明顯高於後端。
  - **純前端 WASM(單執行緒)** —— `poc/server.py` 註記「實測 2M nodes 與 20k nodes 同一手」,顯示本 use case 算力需求極低,拿掉 `-pthread` 即可消除 COOP/COEP 全部麻煩、任何靜態託管皆可用。此路可行且營運成本為零,但仍需自建 wasm 工具鏈、仍要下載 51MB nnue,且改 build flag 會觸發 GPL v3 改動開源義務。權衡後未採用。
  - **桌面 binary(Tauri/Electron)** —— DESIGN §2 已否決,理由(需安裝、與 service 定位不符)至今成立,不重啟討論。

## Scope

- **In**:
  - 後端引擎服務:native Pikafish 封裝、併發管理、API 契約、逾時與錯誤處理、濫用防護
  - 前端對局 client:棋盤、走子、真終局停局、三態諮詢信號、悔棋、中文記譜
  - 題庫:schema 定案、《適情雅趣》前 200 局收錄
  - Build-time 題目驗證:確認「紅先真的必勝」,剔除或改標註偽題
  - 選題瀏覽與練習進度
  - 服務部署與營運:託管、監控、rate limit、授權標示
- **Out**:
  - **走脫判定表(`books/`、`tools/solve.py`、runtime 查表)** —— DESIGN §6 已決定降為可延後升級,待需要「就是這一步」的移動級確定回饋時再上,屆時與三態信號並存
  - **WASM 相關的一切** —— 已否決,不保留任何 wasm 構件、載入層或雙軌設計
  - 離線可用 —— 後端架構的已知代價,明確排除
  - 使用者帳號、雲端進度同步(進度只存本機)
  - 排局編輯器、自建題目
  - 500 題全量(本輪目標為前 200 局,schema 與流程須能無痛擴充)
  - `poc/` 的持續維護 —— 產品化後 `poc/` 功成身退,不再同步演進

## Constraints

- **NNUE 授權禁止未經許可的商業使用。** `engine/licenses/NNUE-License.md` 明文:「No commercial use without permission」,僅 <https://pikafish.org/list.html> 名單上的個人與組織獲准商用。本專案以**免費非商業服務**定位方可直接使用;若日後要收費、放廣告或任何商業化,須先取得許可,或改用 Fairy-Stockfish 的象棋 NNUE 網路(**CC0**,無此限制,棋力可能略遜)。**這是專案級的商業模式約束,不是技術細節。**
- **GPL v3 義務大幅縮小但不歸零。** Pikafish 是 GPL v3 而非 AGPL v3,純伺服器端執行**不構成散布**,因此不觸發「提供對應原始碼」義務。但仍應在頁面標示所使用的引擎與網路及其授權 —— 這是 NNUE 授權與基本誠信的要求,不是 GPL 的。若日後改為向使用者端散布任何引擎構件,`tech.md` 所列全套散布義務立即回歸。
- **需要真的伺服器,有持續營運成本。** 不是靜態託管。每一手棋都消耗伺服器 CPU。
- **公開引擎 API 會被當免費分析服務濫用**,rate limit 與濫用防護是必要件,不是加分項。
- **離線不可用**,每一手都有網路往返延遲。
- **勝負與合法性一律由引擎判定**(`tech.md`):前後端都不自實作象棋規則,尤其不自實作循環規則(長將/長捉/一將一殺)。
- **引擎完全 stateless**:每次重送完整 `position fen <fen> moves <...>`。這是 POC 已驗證的做法,也是後端能水平擴展的前提。
- **引擎版本鎖定為唯一真相來源**:`engine/ENGINE_VERSION` 目前鎖 `Pikafish-2026-01-02`,整包與各平台 binary、nnue 皆驗 sha256。伺服器部署須沿用同一套鎖定機制。
- **語言**:所有使用者可見文字與專案文件一律繁體中文;走法內部格式為 UCI 座標,顯示層轉中文記譜。

## Boundary Strategy

- **Why this split**:
  - **引擎服務**(後端,進程管理與併發)與**對局 client**(前端,互動與呈現)天然分離,API 是明確接縫,POC 已經證明這個切法可行。
  - **題庫內容**(資料,人工編輯)與**題目驗證**(工具,長時間搜尋)分離,對應 `structure.md`「題目資料與工具產出必須分開」的同一理由:solver 會反覆重跑,題目本身不該跟著動。
  - **對局 client** 與 **選題瀏覽** 分離:前者是單題內的互動迴圈,後者是跨題的導航與進度,狀態模型不同。
  - **部署營運** 獨立:託管、監控、rate limit、授權標示橫跨整個產品,但只在「要上線」這一刻收斂,綁進任何功能 spec 都會被稀釋掉。
- **Shared seams to watch**:
  - **HTTP API 契約**是 engine-service 與 web-play-runtime 的正式接縫。POC 的 `/api/start`、`/api/state?moves=...`、`/api/black?moves=...` 是雛形,但 query string 傳整個走法序列在長局會爆長,契約須在 design 階段重新定案。
  - `EngineAdapter.best_move(fen, moves, nodes) -> (uci_move, score)` 外加 `legal_moves`(`tech.md`)仍是引擎存取的內部介面,由 engine-service 擁有;前端只認 HTTP 契約,不認 UCI。
  - **native 引擎封裝被兩個 spec 使用**:engine-service(互動式,求反應快)與 corpus-verification(離線驗證,求搜得深)。用途與參數截然不同,是否共用同一套 UCI 封裝程式碼須在 design 階段決定,不要為了「看起來該共用」而硬綁。
  - `positions/*.json` 的 schema 由 position-corpus 擁有;corpus-verification 只回填 `max_dtm` / `solvable`,其餘 spec 只讀不寫。
  - **判定表的預留接縫**:走脫回饋須設計成可插拔 —— 三態信號是目前唯一來源,日後判定表上線時是新增來源而非改寫流程(DESIGN §6 原案)。

## Specs (dependency order)

- [ ] engine-service -- 將 `poc/server.py` 產品化為引擎後端:native Pikafish 進程池、併發與逾時、HTTP API 契約、rate limit。Dependencies: none
- [ ] position-corpus -- 題目 schema 定案與《適情雅趣》前 200 局收錄,一題一檔進 git。Dependencies: none
- [ ] web-play-runtime -- 前端對局 client:棋盤、走子、真終局停局、三態信號、悔棋、中文記譜,以及網路延遲與失敗的互動處理。Dependencies: engine-service
- [ ] corpus-verification -- `tools/verify.py`:長時間搜尋確認紅先必勝、剔除偽題、回填 max_dtm 與 solvable,加 CI 的 FEN 合法性檢查。Dependencies: position-corpus
- [ ] problem-browser -- 題目列表、難度與標籤篩選、練習進度紀錄(localStorage)。Dependencies: web-play-runtime, position-corpus, corpus-verification
- [ ] service-deploy-ops -- 服務部署、引擎版本鎖定的上線流程、監控與資源上限、濫用防護、引擎與 NNUE 授權標示。Dependencies: engine-service, web-play-runtime

### 交付波次

- **波 1**(並行): engine-service, position-corpus
- **波 2**(並行): web-play-runtime, corpus-verification
- **波 3**(並行): problem-browser, service-deploy-ops

## Later Phase(本輪不做)

- **verdict-table** -- 離線預解走脫判定表:`tools/solve.py`、`books/*.verdict.json`、查表與「就是這一步」的移動級回饋。觸發條件:三態信號的「未知偏多」在實際使用中被判定為學習體驗的瓶頸。上線時與三態信號並存,不取代真終局停局。詳見 DESIGN §4.3 / §6 原案。後端架構下判定表可放在伺服器端查詢,不受使用者端體積限制 —— 這是此架構的附帶好處。
