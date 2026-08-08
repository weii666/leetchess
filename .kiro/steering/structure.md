# Project Structure

## Organization Philosophy

按**產出物的生命週期**分層,而非按技術層次。核心區分是「誰產生、多久重跑一次、變動時誰要跟著動」:

- 人工編輯、長期穩定的資料(題目)與工具反覆重跑的產出(驗證結果、日後的判定表)**必須分開**。solver 會因換引擎版本或調參數而重跑,題目本身不該跟著動
- 第三方構件(引擎 binary、nnue)不進 git,只鎖版本與 checksum
- 一次性驗證工具與產品程式分開,POC 不隨產品演進

## Directory Patterns

```
engine/       第三方引擎:版本鎖定與取得腳本
tools/        build-time 工具,唯一用到 Pikafish native 之處(規劃中)
positions/    題目資料,人工編輯,進 git
web/          前端(規劃中)
poc/          一次性驗證工具,不隨產品演進
.kiro/        規格與專案記憶
```

### `engine/`

**內容**:`ENGINE_VERSION`(版本與各檔 sha256)、`fetch.sh`(依平台下載並校驗)、`licenses/`(GPL v3 全文、NNUE 授權、AUTHORS)。

**規則**:binary、`pikafish.nnue`、壓縮包皆 gitignore(見 `engine/.gitignore`),由 `fetch.sh` 按需重建。版本鎖在純文字檔,進 git。

### `positions/`

**佈局**:出處以**資料夾**表達,題目 JSON 放在哪個資料夾就代表出自哪一本書。

```
positions/
  適情雅趣/
    0001.json
    0002.json
  橘中秘/
    0201.json
```

- 檔名補零至 4 位以便排序,數值與 `id` 一致
- **`id` 為全域唯一的數字**,跨書連續,對應列表上的題號。資料夾只表達出處,不參與編號
- 目錄結構須能容納多本古譜,不得假設只有一本

**題目 schema**:

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `id` | 數字 | 是 | 全域唯一題號,列表主要識別 |
| `title` | 字串 | 是 | 列表顯示用的局名,單行精簡 |
| `description` | 字串 | 是 | 完整描述,允許換行空格,涵蓋書名、局號、局名 |
| `difficulty` | 數字 | 是 | 難度分級 |
| `tags` | 字串陣列 | 是 | 可多個 |
| `fen` | 字串 | 是 | 起始局面 |
| `side_to_move` | 字串 | 是 | 起手方 |
| `max_dtm` | 數字 | 否 | 最長殺著距離,由驗證工具回填 |
| `solvable` | 布林 | 否 | 是否確認紅先必勝,由驗證工具回填 |

**欄位擁有權**:`max_dtm` 與 `solvable` 由驗證工具寫入,其餘欄位人工編輯。工具不得改寫人工欄位,否則會互相覆蓋。

### `poc/`

一次性開發驗證工具(本地 Python 後端 + 單頁棋盤),**不是產品形態**。產品化後功成身退,不再同步演進。`poc/DESIGN.md` 的適用範圍僅限 POC 本身 —— 服務級的架構決策以 `.kiro/steering/` 為準。

## Naming Conventions

- **題目檔名**:補零 4 位數字,如 `0001.json`
- **出處資料夾**:繁體中文書名
- **spec 名稱**:kebab-case,如 `engine-service`、`position-corpus`
- **所有使用者可見文字與專案文件**:繁體中文

## 版本控制策略

進 git:

- 版本鎖定檔與 checksum(`ENGINE_VERSION`)
- 題目資料(`positions/`)
- 授權檔案(`engine/licenses/`)
- 規格與 steering(`.kiro/specs/`、`.kiro/steering/`)

不進 git:

- 第三方構件(引擎 binary、nnue、壓縮包)
- 工具產出物(驗證結果快取、日後的判定表)
- 本機設定(`.claude/settings.local.json`)、`.venv/`

**原則**:能由鎖定版本重建的東西不進 git,重建所需的資訊進 git。

## Code Organization Principles

- **引擎存取集中在單一介面**(`EngineAdapter`,見 `tech.md`)。目前只有 native 實作;要換實作只動這一層
- **前端持有對局狀態,後端無 session**。前端每次重送完整走法序列
- **使用者資料只在瀏覽器本機**。完成狀態存 localStorage,鍵名帶版本前綴;後端不持有任何使用者資料
- **走脫回饋設計成可插拔**。三態信號是目前唯一來源,日後判定表上線時是新增來源而非改寫流程

## 依賴方向

```
positions/  <-  驗證工具         (回填 max_dtm / solvable)
positions/  <-  引擎服務         (依 id 讀 FEN 起局)
引擎服務     <-  前端對局         (HTTP 契約)
前端對局     <-  題目瀏覽         (題目 id 交接)
```

規格層級的依賴順序見 `roadmap.md` 的 Specs 清單。

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
