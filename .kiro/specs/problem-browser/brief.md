# Brief: problem-browser

## Problem

對局介面已經能用了,但**開啟服務直接就是棋盤** —— 沒有任何選題途徑,要換題目只能手動改網址的 `?id=`。這是產品從「一個能下棋的 demo」變成「leetcode 式解題網站」的關鍵一塊:題庫要能瀏覽、能篩選,使用者要能記錄自己練到哪。

參照形態是 grind75 與 leetcode 的 problemset:**先看到題庫列表,點某一題才進到棋盤。**

## Current State

- `service/main.py` 把 `web/` 掛在根路徑,所以 `/` 目前直接是棋盤(`web/index.html`),題號由 `?id=` 帶入、預設 1。
- `web/` 已有完整的單題對局介面(八個模組,608 個測試)。它的 Out of Boundary 明列「**不決定要載入哪一題**」—— 選題本來就留給本 spec。
- 題目 metadata 已有列表所需的全部欄位:`id`、`title`、`description`、`difficulty`、`tags`,出處由資料夾表達(`positions/<書名>/<id>.json`)。目前只有 1 題,`position-corpus` 尚未收錄《適情雅趣》前 200 局。
- 無任何進度儲存機制。

## Desired Outcome

- 使用者可瀏覽全部題目,依難度與標籤篩選,看到題號、局名與出處
- 選一題即進入對局,結束後可回到列表繼續下一題
- 每題有一個 on/off 圖示,**由使用者自行標記是否完成**,重開瀏覽器仍在
- 只呈現已驗證可解的題目,不可解的題目不會被丟到使用者面前

## Approach

### 完成狀態由使用者自行標記

列表每題一個 on/off 圖示,使用者自己按。**系統不偵測、不自動判定解題成功** —— 對局照樣下到分出勝負,但那是對局結果,與「這題我標記為完成」是兩件事。這讓進度模型退化成一份題號集合,不需要任何解題成功的判定邏輯。

狀態存 **localStorage**(非 cookie,非後端)。後端只負責引擎,不持有任何使用者資料。

**實作參考 grind75**(techinterviewhandbook.org):實測其打勾狀態存於 localStorage 的 `1:completedQuestions`,值為 JSON 字串陣列(勾選後 `["valid-parentheses"]`,取消後 `[]`),鍵名前綴 `1:` 為版本或 profile 編號。本專案採相同做法,差別在於主鍵是數字 `id` 而非 slug,存數字陣列更省空間。

選 localStorage 而非 cookie 有兩個實際好處:沒有 cookie 的 4KB 上限,500 題可以直接存 JSON 陣列而不需要區間或 bitmap 這類緊湊編碼;而且不會隨每一個請求送往後端 —— 後端完全不需要這份資料。

### 題目索引

題目 metadata 在 build 時彙整成一份索引供列表使用(200 題約 60KB,500 題約 150KB,可一次載入),但對局仍讀單題資料 —— 保持「一題一檔」的編輯友善度,索引只是產出物。

列表與對局的邊界:problem-browser 負責「選哪一題」與「標記練了哪些」,web-play-runtime 負責「這一題怎麼下」。兩者透過題目 id 交接。

## Scope

- **In**: 題目列表與詳情、難度與標籤篩選、依出處(書目)篩選、題號與 `title` 顯示、`description` 於詳情呈現、選題進入對局的導航、完成狀態的 on/off 標記與 localStorage 持久化、題目索引的 build 時產出、過濾掉 `solvable: false` 的題目
- **Out**: 對局內的任何互動(屬 web-play-runtime)、題目內容與 schema(屬 position-corpus)、驗證與 `solvable` 的判定(屬 corpus-verification)、跨裝置進度同步與帳號(明確排除)、**自動偵測解題成功**(明確排除,完成與否由使用者自行標記)、題目推薦演算法與自適應難度(本輪不做)

## Boundary Candidates

- 題目索引產出(build-time)
- 列表與篩選 UI
- 完成狀態的儲存與編碼
- 列表與對局之間的導航

## Out of Boundary

- 不擁有對局狀態機、引擎存取、走子邏輯
- 不寫入題庫資料
- 不做使用者帳號、雲端同步、社群功能
- 不做難度自動調整 —— `difficulty` 由 position-corpus 人工標註
- 不判斷使用者是否真的解出題目

## Upstream / Downstream

- **Upstream**: position-corpus(`id`、`title`、`difficulty`、`tags` 與出處目錄)、corpus-verification(`solvable` 決定哪些題可上架)、web-play-runtime(對局入口)
- **Downstream**: service-deploy-ops(打包發布)

## Existing Spec Touchpoints

- **Extends**: 無
- **Adjacent**: 與 web-play-runtime 的接縫是題目 id 交接與返回列表;兩者的 UI 外殼可能共用,須在 design 階段決定誰擁有版面框架

## Constraints

- 完成狀態存 localStorage,格式為數字 `id` 的 JSON 陣列,對齊 grind75 的做法
- 無帳號、無雲端同步 —— 狀態只在本機,換裝置或清瀏覽器資料即歸零,這是刻意的取捨
- 完成狀態的鍵名須帶版本前綴,使日後格式變更能辨識舊資料
- 完成狀態要能容忍題庫擴充(200 → 500 題)與題目被剔除(已標記完成的題目後來被標為不可解)
- 繁體中文介面
- 題目索引可完全靜態化(200 題約 60KB),瀏覽列表不應打到後端 —— 後端只在真正開始對局時才被呼叫
