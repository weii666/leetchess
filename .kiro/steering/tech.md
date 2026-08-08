# Technology Stack

## Architecture

**native Pikafish + 後端 API,前端為輕量 client。**

- 黑方應手與合法著法:伺服器端 native Pikafish live 計算,前端經 HTTP 取得
- 前端不下載任何引擎構件
- 需要真的伺服器與持續營運成本;離線不可用;每一手都有網路往返

> 原架構為「純前端 WASM,無後端,靜態託管」,已於 2026-08-08 推翻(官方不產出 wasm 構件、`-pthread` 連帶 COOP/COEP 限制託管、51MB nnue 首載)。完整決策與已否決方案見 `roadmap.md`。

## 三個不可動搖的約束

寫程式時若與這些衝突,是程式錯,不是約束錯。

### 1. 引擎完全 stateless

每次都重送完整局面:

```
position fen <fen> moves <...>
```

悔棋、跳步、重來都只是重送一次。服務端不持有任何一局的進度 —— 對局狀態由前端持有,這也是後端能以多進程水平擴展的前提。

### 2. 勝負判定不自己實作

以引擎回傳為唯一真相來源:合法著法數為 0 即該方負(`go perft 1`),mate 分數取自引擎回傳。前後端都不實作象棋規則。這會省掉最痛苦的一類 bug。

`cp` 分數**不可據以判定勝負傾向** —— 排局中極不可靠,信號寧可誠實地說「未知」。

### 3. 循環規則不自己實作

長將、長捉、一將一殺在排局中是**核心機制**,不是邊緣情況:大量排局的正解建立在「這步是長捉所以黑方必須變著」上,黑方也大量用它謀和。一律交給引擎判定。

## 引擎調用慣例

```
setoption name Threads value 1
setoption name Hash value 128
position fen <fen> moves <...>
go nodes <N>          # 不是 movetime
go perft 1            # 取合法著法 / 判真終局
```

- **`go nodes` 而非 `go movetime`** — 目的不是重現同一步,而是當**品質下限**:保證每次都搜得夠深、確實走在最頑強那一檔,而非負載一變就淺搜走出弱手
- **黑方在最頑強一檔內變化** — 不追求 bit 級一致。單次挑戰內的穩定性由前端 memo 負責(退一步重走同一步),跨次重玩才變化
- **`Threads=1`** — 併發靠多進程而非單進程多執行緒,如此資源可預測、單請求延遲穩定。實測顯示本 use case 算力需求極低(200k 與 2M nodes 同一手)

## 引擎版本鎖定

`engine/ENGINE_VERSION` 是**唯一真相來源**,鎖 release 版本、整包 sha256、nnue sha256、各平台 binary sha256。

`engine/fetch.sh` 依平台下載並校驗。binary 與 nnue 皆 gitignore,由 fetch 按需重建。

**任何一個值改變,所有下游產出物(題目驗證結果、日後的判定表)都必須重新產生。**

## 引擎抽象介面

引擎存取一律經過單一介面,實作可抽換:

```
EngineAdapter.best_move(fen, moves, nodes) -> (uci_move, score)
EngineAdapter.legal_moves(fen, moves)      -> [uci_move, ...]
```

此介面為服務端內部介面。前端只認 HTTP 契約,不認 UCI。

## 走法格式

**內部一律 UCI 座標**,中文記譜只在顯示層轉換。

檔 `a`–`i`(紅方左至右),列 `0`–`9`(紅方底線為 0)。例:炮二平五 = `h2e2`。

## Python 執行環境:uv + venv

專案的 Python(`tools/` 下的腳本等)一律以 **uv** 管理、在專案本地 **venv** 內執行,不使用系統全域 Python:

- root 置 `pyproject.toml`(鎖 `requires-python`)+ `.python-version`,依賴鎖進 `uv.lock`,一併進版本庫
- `uv sync` 建立/同步 `.venv`(uv 自動管理,不另手動維護);`.venv/` 進 `.gitignore`
- 一律走 `uv run <script.py>`,不直接 `python3 <script.py>`,確保版本與依賴可重現且與系統環境隔離

> POC 的 `poc/server.py` 為零依賴、純標準庫的一次性工具,不受此約束。

## 授權約束

### NNUE:禁止未經許可的商業使用

`engine/licenses/NNUE-License.md` 明文「No commercial use without permission」,僅 <https://pikafish.org/list.html> 名單上的個人與組織獲准商用。

本專案以**免費非商業服務**定位方可直接使用。若要收費、放廣告或任何商業化,須先取得許可,或改用 Fairy-Stockfish 的象棋 NNUE 網路(CC0,無此限制,棋力可能略遜)。**這是專案級的商業模式約束,不是技術細節。**

### GPL v3:伺服器端執行不構成散布

Pikafish 為 GPL v3 而非 AGPL v3,純伺服器端執行**不觸發**「提供對應原始碼」義務。但仍應在頁面標示所使用的引擎與網路及其授權。

**若日後改為向使用者端送出任何引擎構件,全套散布義務立即回歸**(附 GPL v3 全文或連結、提供原始碼取得管道;若修改引擎,改動須以 GPL v3 開源)。

## Key Technical Decisions

| 決策 | 理由 |
|---|---|
| 後端 API 而非純前端 | 官方不產出 wasm 構件;51MB nnue 首載;native 全速可把 `go nodes` 開更高 |
| 完成狀態存 localStorage 而非 cookie | 無 4KB 上限;不隨每個請求送往後端。做法對齊 grind75 |
| 題目一題一檔而非單一大 JSON | git diff 乾淨、新增題目不碰其他檔案、多人編輯不衝突。500 題約 150KB,效能非考量點 |
| 走脫判定表延後 | 前兩百局這類強制殺局「紅走脫即被反殺」,真終局本身即給出黑勝;信號只需在殺勢翻面時提示 |

---
_Document standards and patterns, not every dependency_
