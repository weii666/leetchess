# Brief: service-deploy-ops

## Problem

後端架構意味著**有一台真的機器要養**,而且它跑的是 CPU 密集的引擎搜尋。這帶來一整組只在「要上線」時才收斂的問題:部署在哪、資源上限怎麼設、引擎版本怎麼上線、掛了怎麼知道、被濫用怎麼辦、授權怎麼標示。這些綁進任何功能 spec 都會被稀釋掉。

授權這一項尤其不能拖:`engine/licenses/NNUE-License.md` 明文禁止未經許可的商業使用,而這條線在服務上線那一刻就開始適用。

## Current State

- `poc/server.py` 只綁 `127.0.0.1`,`main()` 直接 `serve_forever()`,無任何部署考量。
- `engine/fetch.sh` + `engine/ENGINE_VERSION` 已有完整的 binary 下載與 sha256 校驗機制,鎖 `Pikafish-2026-01-02`,可直接用於部署流程。
- `engine/licenses/` 已備妥 GPL v3 全文、NNUE 授權、AUTHORS 列表 —— repo 層面已履行。
- **尚未有**:任何託管設定、部署流程、監控、rate limit、線上的授權標示。
- `engine/README.md` 的「尚待處理」清單是以 WASM 散布為前提寫的,架構改為後端後該清單需要重寫。

## Desired Outcome

- 服務跑在可控的託管環境上,引擎與 nnue 版本與 `ENGINE_VERSION` 一致且上線時校驗
- 有資源上限(CPU、記憶體、併發),超載時退化為排隊或明確拒絕,而不是整台機器卡死
- 有基本監控:服務存活、請求延遲、引擎進程健康、錯誤率
- 部署是可重現的一條路徑,不是手動 scp
- 頁面上有清楚的引擎與網路授權標示,且商業使用邊界在專案文件中寫明

## Approach

以容器化部署為主軸(引擎 binary + nnue 一起打包,版本由 `ENGINE_VERSION` 校驗),託管選型以「能跑常駐進程、CPU 可控、成本可預期」為準 —— 這不是靜態託管,VPS 或容器平台皆可。

授權處理分兩層:**法律層面**在專案文件中寫明本服務為免費非商業定位,並記錄商業化前必須先解決 NNUE 授權;**呈現層面**在頁面加上引擎與網路的出處與授權標示。因為不向使用者端散布任何引擎構件,GPL v3 的「提供對應原始碼」義務不觸發(Pikafish 是 GPL v3 而非 AGPL v3),但標示仍應做。

## Scope

- **In**: 託管選型與部署流程、容器化與引擎構件打包、`ENGINE_VERSION` 的上線校驗、資源上限與併發配置、監控與健康檢查、濫用防護的營運面(與 engine-service 的程式面搭配)、引擎與 NNUE 授權標示、`engine/README.md` 待辦清單的重寫、成本估算
- **Out**: 引擎服務本身的程式(屬 engine-service)、rate limit 的演算法實作(屬 engine-service,本 spec 只管配置與營運)、任何產品功能、使用者分析、自訂網域與 SEO(本輪不做)

## Boundary Candidates

- 構件打包與版本校驗
- 託管與資源配置
- 監控與健康檢查
- 授權標示與合規文件

## Out of Boundary

- 不修改引擎原始碼
- 不擁有任何棋類邏輯或 UI 功能
- 不做使用者帳號、付費、營利機制 —— 且依 NNUE 授權,任何營利機制都必須先取得許可才能討論

## Upstream / Downstream

- **Upstream**: engine-service(待部署的服務)、web-play-runtime 與 problem-browser(前端產出物)、`engine/ENGINE_VERSION` 與 `engine/licenses/`
- **Downstream**: 無(交付終點);後續 phase 的 verdict-table 若產出 `books/`,需納入同一條部署管線

## Existing Spec Touchpoints

- **Extends**: 無(取代原本已刪除的 static-deploy-compliance)
- **Adjacent**: `engine/README.md` 的合規待辦以 WASM 散布為前提,本 spec 完成後應改寫為後端架構下的實際義務

## Constraints

- **NNUE 授權禁止未經許可的商業使用**(`engine/licenses/NNUE-License.md`),僅 <https://pikafish.org/list.html> 名單者可商用。本服務以免費非商業定位運行;商業化前須取得許可或改用 Fairy-Stockfish 的 CC0 象棋網路
- Pikafish 為 GPL v3 而非 AGPL v3:純伺服器端執行不構成散布,不觸發提供原始碼義務。**但若日後改為向使用者端送出任何引擎構件,`tech.md` 所列全套散布義務立即回歸**
- 每個引擎進程 `Hash 128MB`,進程池大小 × 128MB 是記憶體下限,與 CPU 核數一起決定機器規格
- 引擎搜尋是 CPU 密集且無法快取(每局走法序列都不同),成本隨活躍使用者線性成長
- `ENGINE_VERSION` 是唯一真相來源,上線構件與它不一致即應拒絕啟動
