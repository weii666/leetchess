# Brief: corpus-editor

## Problem

收題目前全靠手打 JSON。要進一題,得同時做對六件事:FEN 沒抄錯、六個必填欄位一個不漏、題號沒跟既有題目撞號、`difficulty` 落在 1–3、檔案放在書目資料夾內、JSON 陣列格式與縮排跟既有檔案一致。任何一項出錯,服務**啟動時就拒絕啟動**(`PositionRepository.load()` 拋 `ValueError`),整個站台連不開。

其中 FEN 的錯誤代價最高,因為它是唯一**看不出來**的:一串 `2Rakc3/4aR3/...` 用肉眼核對不了,抄漏一個數字、多一個斜線,schema 驗證全部照樣通過 —— 錯誤要等到有人點進那一題、引擎拒絕載入局面時才現形。

題庫現在只有 6 局,roadmap 的目標是《適情雅趣》前 200 局。用手打的方式再進 194 題,錯誤率與心力成本會直接成為收題進度的瓶頸,而這件事完全可以被一個工具消掉。

## Current State

- **題庫**:`positions/適情雅趣~卷一/` 底下只有 `20-24.json` 與 `25.json`,共 6 局。
- **schema 由執行期強制**,權威實作在 `service/positions.py`:
  - 必填 `id`(數字)、`title`、`description`、`fen`、`difficulty`、`tags`(字串陣列)
  - 選填 `max_dtm`(由 corpus-verification 回填,本 spec 不碰)
  - **拒絕未知欄位** —— 沒有 `source`(出處由資料夾名表達)、沒有 `side_to_move`(由 FEN 的 `w`/`b` 推導)
  - 一個檔案裝**一個題目陣列**,即使只有一題也是陣列;檔名為局號區間
  - 題目檔**必須在書目資料夾內**,直接躺在 `positions/` 根目錄會被擋下
  - **重複題號使服務拒絕啟動**,錯誤訊息指到「哪一檔的第幾題」
- **`difficulty` 的值域寫在 steering,執行期沒人強制**:`structure.md` 定義三級制(1/2/3),但 `_read_int` 沒有下界,`0`、`4`、負數都收得進去,前端只是原樣顯示、吃中性色。**編輯時擋住,是目前唯一能真正防住這個值的地方。**
- **現有 JSON 的排版有特定形狀**,與 `json.dumps(indent=2)` 的預設輸出不同:物件展開成多行、但 `tags` 陣列寫在一行(`"tags": ["解殺還殺", "鐵門栓"]`),中文不轉義。
- **前端已有可直接重用的兩塊**:`web/fen.js` 的 `parseFen()`(純函式、不碰 DOM)與 `web/board.js` 的 SVG 繪製(不記憶狀態、合法落點一律由呼叫端傳入,傳空集合即為唯讀盤面)。
- **引擎已能驗任意裸 FEN**:`EngineProcess.legal_moves(fen, moves, timeout)` 直接吃 FEN 字串,不需要題號。這個能力現成,本 spec 不必為它動引擎層。
- **但沒有任何端點暴露這個能力給裸 FEN**:`GET /api/positions/{id}` 與 `POST /api/state` 都以題號為入口,尚未進題庫的 FEN 無從驗起。
- **`service/` 完全沒有寫入路徑**,`positions.py` 的模組說明明寫「唯讀」。
- **開發時改題目檔會自動重啟**:`start-dev.sh` 已監看 `positions/*.json`,寫檔後重整瀏覽器即見新題,本 spec 不需為此設計任何東西。

## Desired Outcome

- 從網址直接進入 editor 頁面,首頁與列表頁都沒有連結指向它
- 貼上 FEN 後**立刻在左側畫出盤面**,用眼睛就能核對抄對了沒有
- 五個欄位填完並全數通過驗證後,才能按下寫入
- 一鍵把新題 append 進指定的題目檔,**排版與既有檔案一致**,git diff 只出現新增的那幾行
- **上線的服務沒有任何寫入能力** —— 題庫只能經由 git commit 進版本庫

## Approach

### 寫檔在瀏覽器端,後端不具備寫入能力

用 **File System Access API**(`showDirectoryPicker()`)取得 `positions/` 目錄控制代碼,由分頁自己讀回目標檔、append、寫回。

選這條而非後端寫入端點,理由是**攝取路徑的單一性**:上線題庫由 git commit 進版本庫,伺服器就不該存在「往題庫寫東西」這個能力。沒有寫入端點,就沒有需要小心關好的寫入端點,service-deploy-ops 上線時少一項要防的東西。代價(僅 Chromium 系支援、每次重開分頁要重新授權目錄、append 邏輯要在前端自己寫)由使用者只有維護者一人這個事實吸收掉。

### FEN 合法性仍要問引擎,因此新增一個唯讀端點

前端的 `parseFen()` 只認得字串形狀,答不出「這個局面引擎收不收」。新增 `POST /api/legal-moves` 之類的**唯讀**端點,收裸 FEN、回該局面的合法著法;回不出來即視為 FEN 不合法。

**這個端點與 editor 頁面同受一個環境變數 gating,預設關閉,未開啟時連路由都不註冊、editor 頁面本身也取不到。** 理由是 roadmap 的約束「公開引擎 API 會被當免費分析服務濫用」—— 一個吃任意 FEN 的引擎查詢正是那個濫用面,而 editor 是本機開發工具,上線根本不需要它存在。

**不驗證紅先是否必勝。** 那是 corpus-verification 的長時間搜尋,不屬編輯流程。

### 五個欄位全部手填

`id`、`title`、`description`、`difficulty`、`tags` 都給輸入框,不自動生成。`description` 可預填一個由書名與 title 拼出的建議值供修改,但最終值以人輸入的為準 —— 古譜的局號寫法(「第二〇局」)與局名用字有例外,自動拼接猜錯時人得改得動。

`difficulty` 以三選一呈現(Easy/Medium/Hard 對應 1/2/3),使 schema 的值域在**唯一能真正強制它的地方**被強制住。

### 目標檔案路徑手填

表單多一個「目標檔案」輸入(如 `適情雅趣~卷一/26-30.json`),檔不存在就新建、存在就 append。不依題號自動推導 —— 現有的區間切法是人工分段(`20-24` 五題、`25` 一題),沒有規則可推。

## Scope

- **In**:
  - `web/editor.html` 與其 JS:貼 FEN 即時繪盤(重用 `fen.js` + `board.js`)、五欄位表單、目標檔案路徑輸入
  - 送出前驗證:必填齊全、`difficulty` 為 1/2/3、`tags` 非空字串陣列、`id` 為數字且**未與既有題庫撞號**(比對 `GET /api/catalog`)、FEN 可解析且引擎收得下
  - File System Access API 的目錄授權、目標檔讀回、append、寫回,**輸出排版與既有題目檔一致**
  - `POST /api/legal-moves`(唯讀,裸 FEN)與其環境變數 gating
  - editor 頁面本身的 gating:未開啟時取不到該頁
- **Out**:
  - **修改與刪除既有題目** —— 明確排除,editor 只新增
  - 「紅先是否必勝」的驗證、`max_dtm` 與偽題剔除(屬 corpus-verification)
  - 圖形化擺子(拖曳棋子產生 FEN)—— 本輪只吃貼上的 FEN 字串
  - 題庫內容本身、schema 的定義權(屬 position-corpus 與 `structure.md`)
  - 題目在列表與對局頁的呈現(屬 problem-browser 與 web-play-runtime)
  - 任何形式的後端寫入能力
  - 給終端使用者的自建題目功能 —— 本工具只服務維護者,首頁不連結

## Boundary Candidates

- FEN 輸入到盤面呈現(唯讀繪製,`board.js` 的既有能力)
- 表單欄位與其驗證規則(schema 在前端的一份鏡像)
- 題號撞號檢查(需要讀取既有題庫索引)
- 檔案系統存取與 append(File System Access API 的授權與讀寫)
- JSON 序列化排版(與既有檔案逐字對齊)
- 裸 FEN 的引擎合法性端點與 gating(唯一觸及 `service/` 的部分)

## Out of Boundary

- 不定義 schema,只**遵循** `service/positions.py` 與 `structure.md` 已定的那一份
- 不執行長時間引擎搜尋、不判斷題目真偽
- 不決定題目在 UI 上如何排序、篩選或呈現
- 不擁有 `max_dtm` 與任何驗證工具回填的欄位
- 不觸碰既有題目檔中已存在的任何一題

## Upstream / Downstream

- **Upstream**:
  - `service/positions.py` 的 schema 定義(前端驗證是它的鏡像,分家即失效)
  - `structure.md` 的 `difficulty` 三級制與目錄佈局規則
  - `web/fen.js` 與 `web/board.js`(繪盤能力)
  - `EngineProcess.legal_moves`(合法性判定)
  - `GET /api/catalog`(撞號檢查的既有題號來源)
- **Downstream**:
  - position-corpus 的收題產出 —— 前 200 局的實際錄入靠這個工具進行
  - corpus-verification 讀取本工具產出的題目

## Existing Spec Touchpoints

- **Extends**:
  - engine-service —— 新增 `POST /api/legal-moves` 與 gating 機制,`service/main.py` 的路由組裝要跟著動
  - service-deploy-ops(尚未展開)—— 上線時必須確認 gating 預設關閉,editor 與該端點都不存在於線上服務
- **Adjacent**:
  - position-corpus 擁有 schema 與題庫內容,本 spec 只是把同一份規則實作成編輯期的驗證,**不得在此另立一套欄位定義**
  - problem-browser 與 web-play-runtime 同住 `web/`,editor 是第三個頁面,共用 `fen.js` / `board.js` / `difficulty.js` 但不改它們的既有行為
  - corpus-verification 對同一批檔案有寫入權(限 `max_dtm`),本工具只 append 新題、絕不改寫既有題目,兩者因此不會互相覆蓋

## Constraints

- **僅 Chromium 系瀏覽器可用**(Chrome / Edge)。Safari 與 Firefox 沒有 File System Access API,editor 在那裡直接不能用。這是選擇此方案時已知並接受的代價,不是缺陷。
- **每次重開分頁需重新授權目錄** —— 需要一次使用者手勢才能取回控制代碼。
- **輸出排版必須與既有題目檔逐字一致**:物件展開多行、`tags` 陣列在同一行、中文不轉義。`JSON.stringify(obj, null, 2)` 的預設輸出**不符合**這個形狀(它會把 `tags` 也展開成多行),序列化需要自訂。排版漂掉的話,append 一題會讓 git diff 出現整檔改動。
- **前端驗證是 `service/positions.py` 的鏡像,存在分家風險**。design 階段必須決定如何抑制分家(例如讓後端在同一個 gated 端點上提供驗證,或以測試把兩份規則釘在一起),不能只靠「記得兩邊一起改」。
- **`POST /api/legal-moves` 與 editor 頁面預設關閉**,上線服務不得暴露任何一項。gating 要做到「未開啟時路由不註冊」,而非「開啟時檢查權限」。
- **題號唯一性只在服務啟動時強制**。editor 的撞號檢查取自 `GET /api/catalog`,那是啟動時的快照 —— 同一輪連續新增多題時,前一題尚未經重啟進入索引,撞號檢查看不見它。design 需處理這個窗口。
- **不得改寫既有題目**:append 的實作要能在目標檔已有內容時只增不動,任何會重寫整檔既有題目的做法都必須保證輸出逐字等同輸入。
- 所有使用者可見文字繁體中文;FEN 走法座標系為 UCI(檔 `a`–`i`、列 `0`–`9`,紅方底線為 0)。
