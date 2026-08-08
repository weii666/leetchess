# Brief: position-corpus

## Problem

產品是 leetcode 式的象棋解題網站,但目前只有 **1 題**。沒有題庫就沒有產品。而且古譜排局不能照抄:收題流程若沒有先想清楚(schema、出處表達、擴充方式),等到 200 題散落各處再回頭改結構,成本會高一個數量級。

## Current State

- `positions/0001.json` 是唯一一題,扁平放在 `positions/` 下。
- **`source` 一欄塞了三個資訊**:`"《适情雅趣》第21局 尽善克终"` 同時包含書名、局號、局名。列表需要「題號 + 標題」當主要識別,但標題埋在字串裡取不出來,也無法依書名篩選。這是本 spec 要解掉的第一個問題。
- 現有 `max_dtm: 16` 與 `solvable: true` 是人工填的,未經工具驗證。
- 現有欄位:`id`(字串 `"0001"`)、`fen`、`side_to_move`、`max_dtm`、`solvable`、`difficulty`、`source`、`tags`。

## Desired Outcome

- 題目 schema 正式定案(欄位語意、必填/選填、值域、tag 詞彙表),並讓現有題目遷移完成
- 出處以**目錄結構**表達,而非欄位
- 《適情雅趣》前 200 局完成收錄
- 收題流程可重複、可交接:新增一題該做什麼、標註規範是什麼,寫得下來
- schema 能無痛擴充到 500 題與多本古譜

## Approach

### 出處用目錄表達

題目 JSON 放在哪個資料夾,就代表它出自哪一本書。`source` 欄位隨之取消。

```
positions/
  適情雅趣/
    0001.json
    0002.json
  橘中秘/
    0201.json
```

- 檔名補零至 4 位以便排序,檔名數值與 `id` 一致
- **`id` 為全域唯一的數字**,跨書連續,對應列表上的題號(leetcode 式題號本就是全域的)。資料夾只表達出處,不參與編號
- 資料夾名稱使用繁體中文書名。若日後發現路徑編碼在部署或 URL 上造成困擾,可改為 ASCII slug 並在書目對照表中保留中文名 —— 這是 design 階段可回頭調整的決定

### 題目 schema(定案欄位)

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `id` | 數字 | 是 | 全域唯一題號,列表主要識別 |
| `title` | 字串 | 是 | **列表顯示用的局名**,如「盡善克終」。單行、精簡 |
| `description` | 字串 | 是 | **完整描述,允許換行與空格**,如「適情雅趣 第二一局 盡善克終」。給人看的自由文字,涵蓋書名、局號、局名 |
| `difficulty` | 數字 | 是 | 難度分級,值域由 tag 詞彙表一併定義 |
| `tags` | 字串陣列 | 是 | 可多個,如 `["雙馬", "連將殺", "鬥快"]` |
| `fen` | 字串 | 是 | 起始局面,對局載入所需 |
| `side_to_move` | 字串 | 是 | 起手方,現行題目皆為紅先 |
| `max_dtm` | 數字 | 否 | 最長殺著距離。初值人工填,真值由 corpus-verification 回填 |
| `solvable` | 布林 | 否 | 是否確認紅先必勝。同上,由 corpus-verification 回填 |

`title` 與 `description` 分工明確:列表只顯示 `title`(短、可掃視),`description` 供題目詳情呈現。局號不另設欄位 —— 書名已在資料夾、局名在 `title`、局號在 `description`,三者都有著落。

先把 schema 與 tag 詞彙表釘死並用 JSON Schema 表達(讓 CI 能檢),再批次收錄。FEN 一律以引擎可解析為準(合法性由 corpus-verification 的 CI 把關,本 spec 不自實作規則)。

## Scope

- **In**: schema 定案與 JSON Schema 檔、目錄佈局與出處表達方式、tag 詞彙表與難度分級定義、`id` 編號規則、`title` 與 `description` 的撰寫規範、《適情雅趣》前 200 局的 FEN 與 metadata 收錄、既有 `0001.json` 的遷移(拆 `source` 為 `title` + `description`、`id` 改數字、移入書名資料夾)
- **Out**: 「紅先是否真的必勝」的驗證與偽題剔除(屬 corpus-verification)、判定表(後續 phase)、題目的前端呈現與篩選 UI(屬 problem-browser)、《適情雅趣》以外的其他古譜(本輪不收,但目錄結構須能容納)

## Boundary Candidates

- schema 與詞彙表定義(結構)
- 目錄佈局與編號規則
- 收題流程(內容產出)
- 既有題目的遷移

## Out of Boundary

- 不執行長時間引擎搜尋、不判斷題目真偽 —— 那是 corpus-verification 的責任
- 不擁有 `books/`(判定表)任何內容
- 不決定題目在 UI 上怎麼排序、篩選或呈現
- 不擁有使用者的完成狀態 —— 那存在使用者瀏覽器,不進題庫

## Upstream / Downstream

- **Upstream**: 《適情雅趣》原書(來源)
- **Downstream**: corpus-verification(讀題並回填驗證結果)、engine-service(依 id 讀 FEN 起局)、problem-browser(讀 metadata 做列表與篩選)

## Existing Spec Touchpoints

- **Extends**: 無
- **Adjacent**: corpus-verification 對同一批檔案有寫入權(限 `max_dtm` / `solvable`),兩者的欄位擁有權必須在 design 階段寫明,否則會出現人工編輯與工具回填互相覆蓋

## Constraints

- 一題一檔,進 git,人工可編輯 —— 不得為了效能改成單一大 JSON。500 題 metadata 約 150KB,效能非考量點,選擇標準是編輯友善度
- `id` 全域唯一且為數字;資料夾只表達出處,不參與編號
- FEN 走法座標系為 UCI:檔 `a`–`i`(紅方左至右),列 `0`–`9`(紅方底線為 0)
- 所有文字欄位繁體中文
- 古譜排局有相當比例的「紅勝」結論已被現代引擎推翻,**收題階段就要有心理準備會被剔除**,schema 須能表達「已標註但不可解」的狀態,而不是靜默刪檔
- 目錄結構須能容納多本古譜,本輪雖只收《適情雅趣》,佈局不得假設只有一本
