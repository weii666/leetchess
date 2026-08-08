# Brief: corpus-verification

## Problem

古譜排局有相當比例的「紅勝」結論已被現代引擎推翻,實際為和局甚至紅負。如果不在收題階段驗證,使用者會照著譜走卻贏不了 —— 這是排局練習程式最傷信任的失敗模式,而且發生在使用者面前才被發現。DESIGN §4.2 明講:「這件事在收題階段做,不要等使用者發現。」

現況更直接:`positions/0001.json` 的 `max_dtm: 16` 與 `solvable: true` 是人工填的,沒有任何工具驗證過。

## Current State

- 唯一的引擎存取實作是 `poc/server.py` 的 `Engine` 類,設計給互動式對弈(`go nodes 200000`,求反應快),不是給長時間離線驗證用的。
- `tools/` 目錄在 `structure.md` 的規劃中存在(`solve.py` / `verify.py`),但**尚未建立**。
- `engine/fetch.sh` 已備好 native binary,`structure.md` 明確定位 `tools/` 是「唯一用到 Pikafish native 的地方」。
- 無任何 CI。

## Desired Outcome

- 有 `tools/verify.py`:對每題用長時間搜尋確認「紅先真的必勝」,產出可信的 `max_dtm`,並把驗不出必勝的題目標為不可解而非靜默留著
- 全部收錄題目跑過一輪驗證,結果回填 `positions/*.json`
- 有 CI:每個 FEN 丟給引擎(`position fen ...` + `go depth 1`)確認合法,schema 符合,防止壞題進 repo
- 驗證結果可追溯:用哪個引擎版本、跑多久、什麼參數得出的結論

## Approach

以 native pikafish 執行長時間搜尋(遠高於 runtime 的 `go nodes` 設定),讀 mate 分數判定紅先必勝與 DTM。驗證是 build-time 行為,可以慢、可以跑幾小時,與 runtime 的品質下限旋鈕完全無關。

驗證結果寫回 `positions/*.json` 的 `max_dtm` 與 `solvable` 兩個欄位 —— 這兩個欄位由本 spec 擁有,其餘欄位由 position-corpus 擁有,工具不得改寫。驗證同時記錄所用引擎版本,`ENGINE_VERSION` 一變即代表結果失效需重跑(`tech.md`)。

## Scope

- **In**: `tools/verify.py`(紅先必勝驗證、DTM 產出、不可解標註)、驗證結果回填機制與引擎版本追溯、CI 的 FEN 合法性檢查與 schema 檢查、批次驗證的執行與斷點續跑、驗證報告(哪些題被剔除、為什麼)
- **Out**: 題目的收錄與 schema 定義(屬 position-corpus)、判定表產生 `tools/solve.py` 與 `books/`(後續 phase)、循環規則的完整實作與對齊(隨判定表一起延後)、runtime 的任何行為

## Boundary Candidates

- 驗證引擎封裝(長時間搜尋用,與 runtime 的存取層是不同用途)
- 單題驗證邏輯(必勝判定 + DTM)
- 批次執行與結果回填
- CI 檢查(合法性 + schema)

## Out of Boundary

- 不定義 schema、不新增題目、不編輯 `max_dtm` / `solvable` 以外的欄位
- 不產生判定表、不展開黑方應手子樹 —— 那是後續 phase 的 verdict-table
- 不實作循環規則判定:`tech.md` 指出循環規則只在判定表產生時必須完整對齊,本輪不做判定表,故不背這個包袱。若驗證過程遇到需要循環規則才能定論的題目,標為待決而非硬判

## Upstream / Downstream

- **Upstream**: position-corpus(提供待驗題目與 schema)、`engine/ENGINE_VERSION` 與 `engine/fetch.sh`(native binary)、`poc/server.py` 的 `Engine` 類(UCI 封裝的參考實作)
- **Downstream**: problem-browser(只顯示已驗證可解的題目)、web-play-runtime(依賴 `max_dtm` 顯示殺著倒數)、後續 phase 的 verdict-table(沿用同一套 native 引擎封裝與版本追溯機制)

## Existing Spec Touchpoints

- **Extends**: 無
- **Adjacent**: 與 position-corpus 共寫 `positions/*.json`,欄位擁有權必須劃清,否則人工編輯與工具回填會互相覆蓋。與 engine-service 同樣封裝 native Pikafish,是否共用同一套 UCI 封裝程式碼須在 design 階段決定,不要為了「看起來該共用」而硬綁

## Constraints

- 驗證用 native 引擎,但用途與 engine-service 截然相反(離線長搜求正確 vs 互動快搜求反應),參數與逾時設定不可共用預設值
- 引擎版本或 nnue 一換,所有驗證結果失效須重跑(`tech.md` 的引擎版本鎖定)
- 驗證是長時間作業:須支援中斷續跑,不能要求一次跑完 200 題
- 剔除的題目要留下記錄與理由,不靜默刪檔 —— 這是古譜研究的資料,不是錯誤
- `tools/` 須遵循 `tech.md` 的 uv + venv 規範(`uv run`,不直接 `python3`)
