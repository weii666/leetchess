# Project Structure

## Organization Philosophy

按**產出物的生命週期**分層,而非按技術層次。核心區分是「誰產生、多久重跑一次、變動時誰要跟著動」:

- 人工編輯、長期穩定的資料(題目)與工具反覆重跑的產出(驗證結果、日後的判定表)**必須分開**。solver 會因換引擎版本或調參數而重跑,題目本身不該跟著動
- 第三方構件(引擎 binary、nnue)不進 git,只鎖版本與 checksum
- 一次性驗證工具與產品程式分開,POC 不隨產品演進

## Directory Patterns

```
engine/       第三方引擎:版本鎖定與取得腳本
service/      後端引擎服務:HTTP 端點、對局判定、引擎池、題庫索引
tools/        build-time 工具,唯一用到 Pikafish native 之處(規劃中)
positions/    題目資料,人工編輯,進 git
web/          前端:vanilla JS + SVG,無建置步驟,由 service 掛靜態檔提供
poc/          一次性驗證工具,不隨產品演進
tests/        測試,與 service/ 平行
.kiro/        規格與專案記憶
```

### `engine/`

**內容**:`ENGINE_VERSION`(版本與各檔 sha256)、`fetch.sh`(依平台下載並校驗)、`licenses/`(GPL v3 全文、NNUE 授權、AUTHORS)。

**規則**:binary、`pikafish.nnue`、壓縮包皆 gitignore(見 `engine/.gitignore`),由 `fetch.sh` 按需重建。版本鎖在純文字檔,進 git。

### `positions/`

**佈局**:出處以**資料夾**表達,題目 JSON 放在哪個資料夾就代表出自哪一本書。

```
positions/
  適情雅趣-卷一/
    20-24.json
    25.json
  橘中秘/
    201-205.json
```

- **一個檔案裝一段局號區間的題目**,檔名即該區間,如 `20-24.json` 是第二〇局至第二四局。收題是一次抄一段書,檔案切得比那更細只會讓一次收題散進五個檔案。只裝一局時檔名就是那個局號(`25.json`)—— 區間退化成一個點,不寫成 `25-25.json`
- 檔案內容**恆為題目陣列**,只有一題時也是只有一個元素的陣列。形狀不隨題數改變,補進第二題才不必順手改檔案結構
- 區間內的局號**允許有缺口**:收到哪一局就是哪一局,`20-24.json` 裡可以只有第二一局
- **`id` 為全域唯一的數字**,跨書連續,對應列表上的題號。資料夾只表達出處,不參與編號
- 目錄結構須能容納多本古譜與分卷,不得假設只有一本

**題目 schema**:

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `id` | 數字 | 是 | 全域唯一題號,列表主要識別 |
| `title` | 字串 | 是 | 列表顯示用的局名,單行精簡 |
| `description` | 字串 | 是 | 完整描述,允許換行空格,涵蓋書名、局號、局名 |
| `difficulty` | 數字 | 是 | 難度分級,**1 = 簡單、2 = 中等、3 = 困難**(見下) |
| `tags` | 字串陣列 | 是 | 可多個 |
| `fen` | 字串 | 是 | 起始局面。**起手方也在這裡**,即 FEN 的走子方那一欄(`w` 紅、`b` 黑) |
| `max_dtm` | 數字 | 否 | 最長殺著距離,由驗證工具回填 |

**沒有 `side_to_move` 欄位**:起手方寫在 `fen` 裡,另立欄位等於同一件事有兩個出處,分岔時前端畫的是 FEN、後端算的是欄位,輪方從第一手起就錯開。`Position.side_to_move` 仍存在,但由 `service/positions.py` 的 `_side_from_fen()` 推導。

**沒有 `solvable` 欄位**:它原本要標記「紅先是否真的必勝」供列表篩掉偽題,但值要等 corpus-verification 跑完才有,在那之前每一題都是空值,那道過濾從上線到拆掉為止不曾濾掉任何一題。日後真要標記偽題,那是驗證工具的產出,屆時重新決定它的形狀與落點。

**難度分級**:`difficulty` 是**三級制**,只有 1、2、3 三個合法值。

| 值 | 列表上的說法 | 顏色 |
|---|---|---|
| 1 | Easy | 綠 |
| 2 | Medium | 琥珀 |
| 3 | Hard | 紅 |

**說法是英文,這是「使用者可見文字一律繁體中文」的一條例外**(見 Naming Conventions)。理由是可分辨而非調性:難度在列表上是無底色的彩色字,而同一列的標籤(「解殺還殺」「借炮使馬」)也是中文詞,兩者的差別會只剩顏色 —— 對色覺障礙者等於沒有線索。拉丁字母在一排中文裡自成一格,字形本身就是第二個線索。

這一則原先是**缺口** —— schema 只寫「數字|是|難度分級」,沒說範圍是多少。列表要把難度畫成三色標籤時才發現無從對照,故補在此處。**這裡是分級的唯一出處**,`web/list.js` 的 `DIFFICULTY_LABELS` 與 `web/list.css` 的三組顏色都是它的下游。

**沒有任何一層在執行期強制這個範圍**:`service/positions.py` 的 `_read_int` 對 `difficulty` 沒有下界,`0`、`4`、負數都收得進來。呈現層因此一律要有退路 —— 認不得的值原樣顯示、吃中性色,而不是讓那一列畫不出來。

**欄位擁有權**:`max_dtm` 由驗證工具寫入,其餘欄位人工編輯。工具不得改寫人工欄位,否則會互相覆蓋。

### `service/` 與 `web/` 的交界

後端服務**掛靜態檔提供前端頁面**,兩者同源,因此不需要 CORS 設定,本地開發也只需啟動一個進程。

這條掛載是兩個 spec 之間唯一的實體交界:`web/` 的內容由 web-play-runtime 擁有,而 `service/` 中負責掛載它的那一小段由同一個 spec 加入並維護。engine-service 不擁有任何前端內容,只提供掛載點。

### `poc/`

一次性開發驗證工具(本地 Python 後端 + 單頁棋盤),**不是產品形態**。產品化後功成身退,不再同步演進。`poc/DESIGN.md` 的適用範圍僅限 POC 本身 —— 服務級的架構決策以 `.kiro/steering/` 為準。

## Naming Conventions

- **題目檔名**:局號區間,如 `20-24.json`(第二〇局至第二四局);只有一局時即該局號,如 `25.json`
- **出處資料夾**:繁體中文書名,分卷者以 `-` 綴在書名後,如 `適情雅趣-卷一`
- **spec 名稱**:kebab-case,如 `engine-service`、`position-corpus`
- **所有使用者可見文字與專案文件**:繁體中文。兩處例外:
  - **產品名 `LeetChess`**:專有名詞,沒有中譯
  - **難度分級的說法 `Easy` / `Medium` / `Hard`**:理由見上方的難度分級表

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
positions/  <-  驗證工具         (回填 max_dtm)
positions/  <-  引擎服務         (依 id 讀 FEN 起局)
引擎服務     <-  前端對局         (HTTP 契約)
前端對局     <-  題目瀏覽         (題目 id 交接)
```

規格層級的依賴順序見 `roadmap.md` 的 Specs 清單。

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
