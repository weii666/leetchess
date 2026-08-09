# Requirements Document

## Project Description (Input)

為 leetchess 建立題庫列表,使產品成為 leetcode 式的解題網站:**先看到題庫,點某一題才進到棋盤**。

**誰有問題**:練習排局的使用者。對局介面已經能用,但**開啟服務直接就是棋盤** —— 沒有任何選題途徑,要換題目只能手動改網址的 `?id=`,也沒有任何地方記得自己練過哪些。

**現況**:

- `service/main.py` 把 `web/` 掛在根路徑,`/` 目前直接是 `web/index.html`(棋盤),題號由 `?id=` 帶入、預設 1
- `web/` 已有完整的單題對局介面(八個模組,608 個測試)。它的 Out of Boundary 明列「**不決定要載入哪一題**」—— 選題本來就留給本 spec
- 題目 metadata 已有列表所需的全部欄位:`id`、`title`、`description`、`difficulty`、`tags`,出處由資料夾表達(`positions/<書名>/<id>.json`)
- 題庫目前只有 1 題。`position-corpus`(收錄《適情雅趣》前 200 局)尚未開始 —— 列表現在只會有一列,但**每收一題就自動出現**,列表的價值在結構不在題數
- 無任何進度儲存機制

**該改變什麼**:做出題庫列表作為產品入口 —— 瀏覽全部題目、依難度與標籤與出處篩選、每題一個 on/off 圖示由使用者自行標記完成、點一題進入棋盤、下完可回到列表。

**參照形態**:grind75(techinterviewhandbook.org)與 leetcode 的 problemset。

**已定案的產品決定**:

- **完成與否由使用者自行標記**,系統不偵測、不自動判定解題成功。對局照樣下到分出勝負,但那是對局結果,與「這題我標記為完成」是兩件事。進度模型因此退化成一份題號集合
- **狀態存 localStorage**,不進後端。實測 grind75 的做法:鍵 `1:completedQuestions`,值為 JSON 陣列,鍵名前綴為版本編號。本專案採相同做法,差別在主鍵是數字 `id` 而非 slug
- **不做帳號與雲端同步** —— 換裝置或清瀏覽器資料即歸零,這是刻意的取捨

**邊界**:不擁有對局內的任何互動(屬 web-play-runtime);不擁有題目 schema 與內容(屬 position-corpus);不判斷 `solvable`(屬 corpus-verification),只依它過濾;不判斷使用者是否真的解出題目。

詳細背景見同目錄 `brief.md`,專案級決策見 `.kiro/steering/`。

## Introduction

problem-browser 是產品的入口 —— 使用者開啟服務先看到題庫列表,選一題才進入對局。

它與 web-play-runtime 的分工是明確的:**problem-browser 決定「選哪一題」與「標記練了哪些」,web-play-runtime 決定「這一題怎麼下」**,兩者透過題目 id 交接。

三項已定案的決定框定本功能:

1. **完成狀態由使用者自行標記**,系統不偵測解題成功。
2. **進度只存瀏覽器本機**,後端不持有任何使用者資料。
3. **列表不打後端** —— 題目 metadata 可完全靜態化,後端只在真正開始對局時才被呼叫。

## Boundary Context

- **In scope**:題庫列表與各題的題號、局名、難度、標籤、出處;依難度、標籤、出處篩選;完成狀態的 on/off 標記與本機持久化;自列表進入對局與自對局返回列表;題目索引的產出;過濾掉不可解的題目。
- **Out of scope**:對局內的任何互動(web-play-runtime);題目 schema 與題庫內容(position-corpus);`solvable` 的判定(corpus-verification);使用者帳號、雲端同步、跨裝置進度;**自動偵測解題成功**;題目推薦與自適應難度。
- **Adjacent expectations**:題目 metadata 由 position-corpus 提供且欄位齊備;`solvable` 為 false 的題目不應上架,該欄位由 corpus-verification 回填,在它完成前所有題目視為可上架;對局介面接受題號並自行負責該題的一切。

## Requirements

### Requirement 1: 題庫列表作為入口

**Objective:** As a 練習排局的使用者, I want 開啟服務先看到題庫而非某一題的棋盤, so that 我能選擇要練哪一題

#### Acceptance Criteria

1. When 使用者開啟服務的入口位址, the 題庫瀏覽介面 shall 呈現題庫列表而非任何單一題目的棋盤。
2. When 列表呈現, the 題庫瀏覽介面 shall 對每一題顯示題號、局名、難度、標籤與出處。
3. The 題庫瀏覽介面 shall 使題號與局名成為每一列的主要識別,可供快速掃視。
4. Where 題目被標註為不可解, the 題庫瀏覽介面 shall 不將其列入。
5. If 題庫中沒有任何可列出的題目, then the 題庫瀏覽介面 shall 告知使用者題庫為空,而非呈現空白畫面。

### Requirement 2: 篩選

**Objective:** As a 使用者, I want 依難度、標籤與出處縮小範圍, so that 我能找到適合現在練的題目

#### Acceptance Criteria

1. When 使用者選擇一個難度, the 題庫瀏覽介面 shall 只列出該難度的題目。
2. When 使用者選擇一個標籤, the 題庫瀏覽介面 shall 只列出帶有該標籤的題目。
3. When 使用者選擇一個出處, the 題庫瀏覽介面 shall 只列出該出處的題目。
4. When 使用者同時選擇多個條件, the 題庫瀏覽介面 shall 只列出全部條件皆符合的題目。
5. When 篩選後沒有符合的題目, the 題庫瀏覽介面 shall 告知使用者沒有符合條件的題目,並使其能清除條件。
6. The 題庫瀏覽介面 shall 使目前生效的篩選條件對使用者可見。

### Requirement 3: 完成標記

**Objective:** As a 使用者, I want 自己標記練過哪些題, so that 下次回來知道從哪裡繼續

#### Acceptance Criteria

1. The 題庫瀏覽介面 shall 為每一題提供一個可切換的完成標記。
2. When 使用者切換某題的完成標記, the 題庫瀏覽介面 shall 立即反映該題的新狀態。
3. When 使用者重新開啟服務, the 題庫瀏覽介面 shall 呈現先前標記的完成狀態。
4. The 題庫瀏覽介面 shall 不自動判定任何題目為完成 —— 標記只由使用者的操作產生。
5. The 題庫瀏覽介面 shall 使完成題數與總題數對使用者可見。
6. If 先前儲存的完成狀態無法解析, then the 題庫瀏覽介面 shall 以「全部未完成」繼續運作,而非失敗或呈現空白。
7. Where 曾標記完成的題目後來不再列出, the 題庫瀏覽介面 shall 不因此失敗。

### Requirement 4: 列表與對局之間的往返

**Objective:** As a 使用者, I want 點一題就開始下,下完能回到列表, so that 我能連續練習

#### Acceptance Criteria

1. When 使用者選取列表中的一題, the 題庫瀏覽介面 shall 開啟該題的對局介面。
2. When 對局介面開啟, the 對局介面 shall 載入使用者所選的那一題。
3. The 對局介面 shall 提供返回列表的途徑。
4. When 使用者自對局返回列表, the 題庫瀏覽介面 shall 保留返回前的篩選條件與完成標記。

### Requirement 5: 題目索引

**Objective:** As a 使用者, I want 列表立刻出現而不必等後端, so that 瀏覽題庫不受引擎負載影響

#### Acceptance Criteria

1. The 題庫瀏覽介面 shall 自單一份題目索引取得列表所需的全部資料,不因篩選而再次取得。
2. The 題庫瀏覽介面 shall 不因呈現列表或篩選而佔用任何引擎資源 —— 引擎池滿時列表仍須能開啟與操作。
3. When 題庫新增題目, the 題目索引 shall 在不修改程式的情況下涵蓋該題。
4. The 題目索引 shall 支援題庫擴充至 500 題而不需要改變其產出方式。

### Requirement 6: 呈現與可用性

**Objective:** As a 使用者, I want 在手機上也能瀏覽題庫, so that 我不必坐在電腦前才能挑題

#### Acceptance Criteria

1. The 題庫瀏覽介面 shall 在行動裝置的直向畫面上完整呈現列表,不需橫向捲動。
2. The 題庫瀏覽介面 shall 以繁體中文呈現所有使用者可見文字。
3. The 題庫瀏覽介面 shall 使已完成與未完成的題目在視覺上可區分。
