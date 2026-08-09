# Implementation Plan

> **範圍原則**:善用 `poc/index.html`。進入題目之後,**不做比 POC 明顯複雜的功能** —— POC 已驗證的互動(棋盤、選子、走子、記譜、信號、重來)就是本輪的目標形態。新增的只有三樣:接上正式 API、等待狀態、失敗後可復原。

## 1. 基礎:測試工具與可開啟的頁面

- [x] 1.1 加入 Playwright 開發依賴並驗證瀏覽器可用
  - 以 uv 加入 `playwright` 開發依賴,取得其瀏覽器 binary
  - 建立最小的 Playwright 夾具,能開啟一個頁面並執行 JS
  - **這是整個測試策略的前提** —— vanilla JS 無 node 工具鏈,沒有它則 38 條 AC 全部只能手動驗證
  - 完成狀態:`uv run pytest` 中有一個 Playwright 測試實際開啟瀏覽器、執行一段 JS 並取回結果;既有 335 個測試不退化
  - 若瀏覽器下載在此環境不可行,**立即回報而非繼續往下做** —— 整個測試策略需重議

- [x] 1.2 建立頁面骨架並由後端掛載靜態檔
  - 建立 `web/index.html` 的版面骨架:盤面容器、側欄(題目資訊、信號、歷史著法、重來)、錯誤提示區
  - **在 `service/main.py` 加入 `web/` 的靜態檔掛載** —— 這是本 spec 唯一的跨目錄改動,與 API 同源因此不需要 CORS
  - 所有使用者可見文字為繁體中文
  - 完成狀態:啟動服務後以瀏覽器開啟根路徑可看到頁面骨架,且既有三個 API 端點行為不變
  - _Requirements: 8.3_
  - _Boundary: web/index.html, service/main.py_

## 2. 純函式:自 POC 移植

- [x] 2.1 (P) 移植 FEN 解析與著法套用
  - 自 `poc/index.html` 的 `parseFen`(第 105 行)與 `applyMove`(第 118 行)移植到 `web/fen.js`
  - 純函式,不碰 DOM、不發請求
  - 完成狀態:以 `page.evaluate()` 驗證起始局面解析正確、含吃子的著法套用後盤面正確
  - _Requirements: 1.1_
  - _Boundary: web/fen.js_

- [x] 2.2 (P) 移植中文記譜
  - 自 `poc/index.html` 的 `uci2cn`(第 199 行)移植到 `web/notation.js`,含縱線序號、進退平、同線前後子判別
  - **不預設 POC 的實作是對的** —— 這段邏輯繁瑣且從未被驗證過,移植時一併補上針對性測試:同線雙車、同線雙馬、同線兵、以及進退平三種走向
  - 完成狀態:上述四類記譜案例以 `page.evaluate()` 驗證通過;若移植過程發現 POC 有誤,修正並在測試中記錄該案例
  - _Requirements: 8.1_
  - _Boundary: web/notation.js_

## 3. 盤面呈現與走子互動

- [x] 3.1 移植 SVG 棋盤繪製
  - 自 `poc/index.html` 的 `drawGrid`(第 125 行)與 `render`(第 149 行)移植到 `web/board.js`
  - 以紅方在下的視角呈現;`board.js` 只接受資料並繪製,**自身不記憶任何狀態**
  - 完成狀態:給定起始 FEN 後,瀏覽器中可見完整棋盤與正確的初始子力配置
  - _Depends: 2.1_
  - _Requirements: 1.1, 1.5_
  - _Boundary: web/board.js_

- [x] 3.2 實作選子與合法落點互動
  - 自 `poc/index.html` 的 `selectPiece`(第 182 行)與 `render` 中的落點標示邏輯移植
  - 選取己方棋子後標示其所有合法落點;**合法落點一律取自外部傳入的資料,不自行判斷任何棋規**
  - 選取非己方棋子或未標示的位置時不改變盤面、不觸發任何動作
  - 完成狀態:瀏覽器中點選己方棋子可見落點標示;點選非己方棋子或空白處時盤面無變化且無請求送出
  - _Requirements: 2.1, 2.3, 2.4_
  - _Boundary: web/board.js_

## 4. 對局推進

- [x] 4.1 實作後端 client
  - `web/api.js`:載入題目與取得對手應手兩個操作,走法序列以請求主體傳遞
  - **每次請求都要有逾時上界**,逾時與連線失敗都轉為可辨識的失敗
  - **後端錯誤有兩種形狀**:端點錯誤為帶類別碼的結構,路由層則是框架原生格式。無法辨識的形狀一律歸為通用錯誤,**絕不把原始內容交給呈現層**
  - POC 的 `api()`(第 253 行)整份丟棄 —— 它綁死舊端點與 query string,無逾時也無錯誤模型
  - 完成狀態:以攔截後端的方式驗證逾時、連線失敗、帶類別碼的錯誤、框架原生格式四種情況,各自回傳可辨識且不含原始內容的失敗
  - _Requirements: 1.4, 3.5, 7.1, 7.2, 7.5_
  - _Boundary: web/api.js_

- [x] 4.2 實作對局狀態機
  - `web/game.js`:持有題目、走法序列、當前局面、信號、等待態 —— **走法序列是唯一真相**,盤面與歷史全部由它推導
  - 走出一手後**以單一請求取得對手應手與其後的局面狀態**,不先查詢局面
  - **終局只依據回應中的結束旗標**,絕不因信號為任何值而提早結束
  - **不提供任何單手回退**;重來為走法序列清空,失敗復原為退回送出前的值
  - 等待中與非我方回合皆不接受走子;任何失敗之後狀態回到可操作、等待態解除
  - 走脫回饋的來源須可擴充:目前只有信號,日後判定表是新增來源而非改寫推進流程
  - 完成狀態:以攔截後端的方式驗證 —— 信號為即將取勝但未結束時對局仍可繼續;對手著法為空且已結束時呈現使用者獲勝;重來後走法序列清空;失敗後仍可再走子
  - _Depends: 4.1_
  - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 4.3, 4.4, 5.1, 5.2, 6.2, 6.4, 7.3, 7.4_
  - _Boundary: web/game.js_

- [x] 4.3 組裝介面:事件綁定、盤面更新與歷史著法
  - `web/app.js`:綁定選子與落點事件,把狀態機的變化反映到盤面與側欄
  - 顯示題目局名、出處、最長殺著距離;題目不存在時告知使用者而非呈現空白盤面
  - 歷史著法以中文記譜呈現(移植 POC 的 `renderMoves`,第 238 行)
  - 當前輪方須對使用者可辨識
  - 重來按鈕接上狀態機的重來
  - 完成狀態:瀏覽器中可完成「選子 → 落點 → 盤面更新 → 歷史著法新增一列」的完整一手,且側欄顯示正確的題目資訊與輪方
  - _Depends: 3.1, 3.2, 4.2_
  - _Requirements: 1.2, 1.3, 1.4, 3.2, 5.1, 8.1, 8.4_
  - _Boundary: web/app.js_

- [x] 4.4 呈現三態信號、等待狀態與錯誤
  - **三態信號**:即將取勝時附殺著倒數,**以近似值形式(「約 N 步」)** —— 後端在 250k 節點下可能高估 1 步
  - **殺著倒數可能為 0**(終局那手)。JS 的 `if (mateIn)` 對 0 為假 —— **一律以 `!= null` 判斷**,否則終局那手的倒數會被靜默吞掉
  - **對手著法為空時信號仍可能有值**,兩者獨立呈現
  - 信號的呈現須讓使用者辨識它是參考資訊而非勝負判決
  - 等待後端回應時呈現等待狀態,回應或失敗後皆解除
  - 錯誤以**單一通用區塊**呈現,區分只需到「可重試」與「須重來」兩類,不為每種錯誤各做一套 UI
  - 完成狀態:三種信號各自可見;**殺著倒數為 0 時仍正常顯示**;等待期間有明確狀態且結束後解除;逾時與忙碌各自的提示可見且介面仍可操作
  - _Depends: 4.3_
  - _Requirements: 4.1, 4.2, 6.1, 6.4, 7.1, 7.2, 7.3_
  - _Boundary: web/app.js_

## 5. 版面與端到端驗收

- [x] 5.1 (P) 版面與行動裝置適配
  - `web/style.css`:桌面與行動裝置直向畫面的版面
  - 行動裝置直向畫面須完整呈現盤面與對局資訊,**不需橫向捲動**
  - 完成狀態:以行動裝置尺寸的視窗載入頁面,盤面與側欄皆完整可見且無橫向捲軸
  - _Depends: 1.2_
  - _Requirements: 8.2_
  - _Boundary: web/style.css_

- [x] 5.2 端到端:對真實服務走完一整局
  - 啟動真實服務,以瀏覽器自起始局面走完《適情雅趣》第 21 局到紅勝
  - 驗證**只在真終局停局** —— 過程中信號早已顯示即將取勝,但每個中途局面都必須仍可繼續走子
  - 驗證最後一手:對手著法為空、對局結束、呈現使用者獲勝,且**該手的殺著倒數為 0 時仍正確顯示**
  - 驗證歷史著法的中文記譜與實際走法一致
  - 完成狀態:一整局可在瀏覽器中走完並正確判定紅勝;此測試不使用攔截,打的是真實後端與真實引擎
  - _Depends: 4.4, 5.1_
  - _Requirements: 3.2, 3.3, 3.4, 4.2, 8.1_

---

## 範圍說明

本任務計畫涵蓋 requirements.md 的 Requirement 1 至 8。

**已延後的項目**(見 requirements 的 `## Backlog`):5.3 / 5.4 的重新開啟後狀態重建(需要進度持久化,屬 problem-browser)、6.3 的取消等待中請求(單次搜尋僅 0.12 秒,且前端取消不會中止後端搜尋)。

**本輪只用到兩個後端端點**:載入題目與取得對手應手。狀態重建延後後,局面查詢端點沒有使用場景 —— 載入題目的回應已含起始局面的合法著法,重來時再取一次即可。

## Implementation Notes

- 移植來源行號以 `poc/index.html` 目前的內容為準。POC 本身不改動,功成身退。
- 1.1:**可行性閘門已通過** —— Playwright 的 chromium 可在此環境下載並執行,測試策略成立。實測 `HeadlessChrome/151`,`page.evaluate()` 確為真瀏覽器回值。
- 1.1:**瀏覽器 binary 不由 `uv sync` 取得**,新環境須 `uv run playwright install chromium`(務必指明 chromium,裸跑會多抓 firefox 與 webkit 約 1GB)。已寫入 `tech.md`。
- 1.1:夾具在 `tests/conftest_web.py`,經 `tests/conftest.py` 註冊。`browser` 為 session 級、`browser_page` 為 function 級並每測試獨立 context(cookie / storage / `page.route()` 規則彼此隔離)。**後續任務直接用 `browser_page`**。
- 1.1:`playwright` 的 import 刻意置於夾具函式內而非模組頂層。review 實測該惰性省下約 25-30ms、對整體套件時間無可測影響 —— 真正的節省來自 pytest session 夾具本身的惰性,不需額外保護。
- 1.2:**根掛載會破壞路由層的 404/405 區分**。starlette 的路由比對中根 `Mount` 對任何路徑都是 FULL match,勝過 API 路由的 PARTIAL —— 實測無遮蔽時 `GET /api/state` 由 405 退化成 404,正好抹掉 engine-service 4.3 刻意保留、且 `api.js` 需要辨識的那個區分。`service/main.py` 的 `_WebFiles` 讓 `/api` 前綴對靜態檔回 `Match.NONE`,**保護不依賴掛載順序**(review 已以掛載順序突變確認)。
- 1.2:遮蔽只吃 `/api` 與 `/api/...`,**不會擋到 `web/api.js`**(任務 4.1 的檔案)—— review 已實測 `/api.js` 回 200。但 `web/api/` 這種子目錄會被遮蔽,design 的 `web/` 佈局是扁平的,不受影響。
- 1.2:`index.html` 已預先接上 `./style.css` 與 `./app.js`,兩者尚不存在故 console 有兩筆 404。**這是必要的** —— 任務 4.3/4.4/5.1 的 boundary 都不含 `index.html`,進入點必須現在備妥。
- 1.2:骨架容器 id:`#board`、`#puzzle-title`、`#puzzle-source`、`#puzzle-max-dtm`、`#turn`、`#signal`、`#waiting`、`#error`、`#moves`、`#reset`。後續任務直接用這些。
- 2.1:**ES modules 無法自 `file://` 載入** —— Chromium 以 CORS 擋下(origin 為 `null`)。所有 `web/*.js` 的瀏覽器測試必須經 http(s) origin;`tests/test_web_pure.py` 以 `page.route()` 合成 origin 就地供**真實交付檔**,不啟動伺服器進程。後續任務沿用該手法。
- 2.1:`web/fen.js` 匯出 `parseFen`、`applyMove`、`sq2fr`、`fr2sq`、`FILES`、`RANKS`。函式本體與 POC 第 105-123 行**逐字元相同**,只加了 `export`。
- 2.1:**`NAMES`(棋子代碼到中文名)歸 `fen.js`**(parent 決定,經 review 提請)。原因:2.2 的 boundary 是 `notation.js`、3.1 是 `board.js`,兩者互不相交卻都需要它,不指定位置必然各做一份並隨時間漂移;而棋子代碼本來就是 FEN 的一部分,`fen.js` 又是最左端的共同依賴。**任務 2.2 獲授權將 `NAMES` 移入 `fen.js` 並匯出;任務 3.1 從該處匯入,不得自行定義。**
- 2.1:`applyMove` 對越界或畸形 UCI 不做防護,會靜默寫壞盤面。這是 POC 既有契約且合法性判定明列為 Out of Boundary(著法一律來自後端),移植任務不自行加碼。
- 2.2:**POC 的 `uci2cn` 經獨立驗證後判定正確**,無須修正。**訂正**:當時記的「前/後分支在第 21 局裡一次都沒被執行過」是錯的 —— 5.2 的端到端實測顯示起始 FEN 的 d1/d3 就是一對黑卒共線,實戰中亦出現 g6/g8 雙傌(記譜「前傌進五」)。但補測的理由反而更強:靠一局棋恰好走到的路徑當覆蓋率並不可靠。現在有 12 個測試蓋著,review 另枚舉雙相/雙仕/雙傌的所有合法目的地確認無記譜碰撞。
- 2.2:兩個記譜慣例的取捨已釘在測試裡(非缺陷,皆無歧義):仕/相同線時用「前/後」而非棋譜慣見的「相七退五」;同線四子以上用「一二三四」而非「前二三後」。要改的話改動點單一,測試 docstring 已註明位置。
- 2.2:**`renderMoves` 歸 `app.js`**(design 第 163 行原本誤劃給 `notation.js`,與同段「純函式」矛盾,已修正)。tasks 4.3 本來就是這樣分的。
- 2.2:`NAMES` 已移入 `web/fen.js` 並匯出(純新增,既有函式一字未動)。**任務 3.1 從那裡匯入,不得自行定義。**
- 3.1:**棋子畫在 SVG 內,不是 POC 的絕對定位 `div`**。這是移植中唯一的實質偏離,經 review 實測證明必要:POC 的 `div.piece` 靠 `#board{position:relative}` + `.piece{position:absolute}` 這組全域 CSS 才定位,而 `style.css` 屬 5.1、`index.html` 不在 3.1 的 boundary —— 照搬會讓 32 個子 `position: static`、`distinctX: 1`,退化成一直列掉在棋盤下方。幾何(座標公式、10 橫線、河界斷開、九宮斜線、河界字樣位置、棋子直徑)與 POC 逐項相同。
- 3.1:**⚠ 給 3.2 與 5.1 的交付事實**:`.piece` 現在是 `<g>`,POC CSS 的 `box-shadow`(`.piece.selected`、`.selectable:hover`)、`border`、`border-radius`、`background` 在 SVG 元素上**一律無效**;`<rect class="board-bg">` 也蓋掉了 `#board{background}`。**5.1 改樣式要針對 `circle.piece-disc` / `text.piece-label` 用 `fill`/`stroke`;3.2 的選中標示要自繪圈或改 `stroke-width`。**
- 3.1:`renderBoard(container, { board })` 以 options 物件為入口,3.2 擴充 `selected` / 合法落點 / 事件回呼不改變呼叫形式。每個子是單一 `<g class="piece">`,點字或點圓都算點到同一個子。
- 3.1:`svg` 同時帶 `width`/`height` 屬性與 `viewBox`。5.1 用 `#board svg { width:100%; height:auto }` 即可蓋掉屬性,長寬比由 `viewBox` 自動保持。
- 3.1:依賴方向測試補強 —— 只斷言 import 路徑擋不住「仍匯入 `fen.js` 但自行定義一份 `NAMES`」,已加 `test_board_does_not_redefine_the_shared_piece_names` 並以突變確認會捕捉。
- 3.2:**⚠ 給 4.2 的強制約束**:`board.js` **不判斷子的歸屬** —— 「可選取」的定義就是「該格有著法可出發」。因此 **requirements 2.5 / 6.2(非我方回合、等待中不接受走子)完全落在 `game.js`:那些狀態下必須傳入空的 `legalMoves`**。review 已實測風險為真 —— 傳入黑方著法時點黑將會回報 `['select','e9']`。
- 3.2:`renderBoard(container, { board, legalMoves, selected, onSelect, onMove })`。點擊只以回呼往外通知,**不重繪就沒有任何視覺變化**(選中狀態也不留在 board.js)。再次點選已選中的格會回報 `onSelect(null)`。
- 3.2:**吃子標示的 `pointer-events: all` 是 load-bearing**。它是 `fill="none"` 的圓環,SVG hit-testing 會讓點擊穿過未填色的內部打到底下的棋子 —— 移除後點吃子圈中央會完全落空。5.1 改樣式時不可拿掉。
- 3.2:盤面留白帶(40px)上 `elementFromPoint` 回傳的是 `rect.board-bg`,與格點上的 `path.grid` 是**兩條不同的 hit-testing 路徑**。首輪 review 因留白帶未被覆蓋而 REJECTED,已補座標點擊測試。
- 3.2:`legalMoves` 含長度不足 4 的項目會拋 `TypeError`(不防護,與 `applyMove` 同一取捨 —— 合法性一律來自後端)。拋錯優於靜默畫錯。
- 4.1:`web/api.js` 匯出 `loadPosition(id, options)` 與 `requestBlackMove(id, moves, options)`。失敗一律是單一 `ApiError`,**只帶 code,`message` 也設成 code 本身** —— 連 `String(err)` 都洩漏不了後端文字。code 為七種契約碼加 `TIMEOUT` / `NETWORK` / `UNKNOWN`。
- 4.1:**⚠ 給 4.4 的交付事實**:(a) **呈現層必須自己把 code 對應成繁體中文** —— 沒有任何後端訊息會抵達它;(b) `mate_in: 0` 與 `move: null` **原樣穿透不加工**,falsy 陷阱歸 4.4 避。
- 4.1:分類**只讀 `code` 不看 HTTP 狀態**,且以 `Set` 精確比對 —— 契約外的碼(後端日後新增第八種、大小寫變體、前綴相同者)一律歸 `UNKNOWN` 而非放行。
- 4.1:逾時預設 10 秒,刻意大於後端的 `DEFAULT_TOTAL_TIME_BUDGET = 8.0`,否則合法的慢回應會被誤判成逾時。計時器在 `finally` 才解除,**涵蓋讀取回應本體的階段**。
- 4.1:**待補的回歸護欄**(review 發現,非阻斷):現有測試鎖不住「逾時涵蓋讀 body」—— 把 `clearTimeout` 移到 `await response.text()` 之前,35 個測試仍全綠。要驗得出來需真實 HTTP 伺服器(送出 header 後停住 body),`page.route()` 的 `fulfill` 做不到。另外兩個逾時測試在退化時會**掛住**而非乾淨轉紅,建議加 `Promise.race` 看門狗。可於 5.2 一併處理。
- 4.2:`createGame({ positionId, feedbackSources, timeoutMs })` 回傳 `{ getState, subscribe, load, play, reset }` —— **就這五個,沒有回退入口**。狀態快照為 `Object.freeze`,`feedback` 是**陣列**(目前只有 signal 一個來源),日後判定表往 `feedbackSources` 加一個函式即可,推進流程不動。
- 4.2:**重來不打後端**(design 第 115 行的措辭已修正)。它是 7.3 的復原路徑,若自己依賴網路,斷線時復原路徑本身就不可用,直接牴觸 7.4。起始狀態在 `load()` 時已取得,重取也只會拿到同一份回應。
- 4.2:**代次守衛** —— 重來或重新載入會使在途回應作廢。沒有它,一份在路上的應手會把剛清空的走法序列重新填滿,5.1 在真實網路下只是偶爾成立。
- 4.2:`!waiting` 守衛在走子路徑上其實冗餘(自己那一手先進序列,輪方已翻成黑方),**真正 load-bearing 的是「重新載入題目在途時」** —— 那時輪方仍是紅、對局也沒結束,唯一擋得住的只有等待態。載入失敗後重試(7.1)正好會經過這個狀態。
- 4.2:**⚠ 給 4.3 的授權與必辦事項**(review 發現,目前無法觸發是因為還沒有訂閱者):`play()` 的第一個 `notify()` 在 try 之外,且 catch 只退回 `moves` 沒退回 `currentState`。**訂閱者一丟例外**,狀態機會壞在兩種方式 —— 成功路徑上快照對著起始盤面發出走後的著法清單(實測 `moves: []` 但 `legalMoves` 是走後的),或永久卡在 `waiting: true` 且 `error` 為 null(違反 6.4)。**任務 4.3 獲授權修改 `web/game.js` 修掉這兩處**(建議在 `notify()` 內逐一保護 listener 呼叫,一次解決兩者),不得只在 app.js 迴避。
- 4.2:`over` 用 `=== true` 嚴格比較是刻意的防禦,但 `api.js` 的 `loadPosition` accept **未**檢查 `state.over` 型別(只有 `requestBlackMove` 有),那是這個嚴格比較唯一吃重卻未被測試釘住的路徑。可隨時補一條:`POSITION_RESPONSE` 的 `state.over` 給 `"false"`,斷言快照 `over` 仍為 False。
- 4.3:**載入失敗後按「重來」曾是一條死路** —— `game.reset()` 合法地產生 `position == null && error == null`,而 `app.js` 把這個組合讀成「載入中」,於是告知被抹掉、頁面停在假的載入中、沒有請求在跑、除了手動重新整理沒有出路。而重來是那個失敗畫面上**唯一的按鈕**。修法:重來時若 `position == null` 就改為重試 `load()` —— 重來無法還原一個從未載入的題目。
- 4.3:`app.js` 的 `render()` 是**唯一寫進畫面的路徑**,每次自快照整份重繪;呈現層唯一的狀態是 `selected`,且每次對局狀態變更都清掉它。
- 4.3:**測試的等待條件必須數半手,不能數 `#moves li`** —— 使用者自己的半手一進序列該列就出現,以它為條件會讓斷言在回合中途跑,黑方應手時有時無。實測:延遲 1.5 秒的端點下 `#moves li` 已是 1 而應手仍在路上。
- 4.3:`uci2cn` 需要**該著法走之前**的盤面,故歷史著法自起始 FEN 重放 —— 先記譜再 `applyMove`。用走後的盤面會讓記譜全錯。
- 4.3:**⚠ 給 4.4**:`#error` 目前只在「沒有題目」時寫入(1.4),其餘一律 `hidden`。**4.4 必須把 `renderLoadFailure` 擴成涵蓋走子途中的失敗,而不是另開一條錯誤路徑** —— 兩條路徑會互相蓋掉對方的訊息。`#signal` 與 `#waiting` 至今未被觸碰。
- 4.3:`game.js` 的兩處健壯性問題已修(`notify()` 逐一保護 listener、catch 一併退回 `currentState`),對外行為未變,51 條既有測試全過。
- 4.3:題號自 `?id=` 帶入(預設 1)。design 與 requirements 都沒規定參數名 —— problem-browser 日後產生連結時需與此一致。
- 4.4:**「數半手」的等待規則對最後一手不夠**(修正 4.3 的 note)。`game.play()` 在第一個 `await` 之前就同步把使用者的半手推進序列並 `notify()`,所以 `click()` 回來時計數已經是 1;而 `move: null` 時計數**永遠不再變**。正確的沉澱信號是**等 `#waiting` 轉隱藏** —— `waiting` 在點擊派發內同步翻成 true,無競態。
- 4.4:`#error` 是**單一節點單一寫入路徑**(全 `web/` 樹只有兩行寫它),載入失敗與走子失敗共用,不會互相殘留。錯誤碼只影響文案不影響版面;復原只分「可重試」與「須重來」兩類。
- 4.4:`#signal` 呈現為 `參考信號:<讀數>` 加一行常駐說明「僅供參考,不是勝負判決;對局只在真終局結束。」讀數依 `state.userSide` 換算而非依顏色。殺著倒數為 `約 N 步`,以 `!= null` 判斷 —— **實測 `mate_in: 0` 確實顯示「約 0 步」**。(**文案已於側欄精簡一輪中改寫,見本節末的「側欄文案精簡」**;`userSide` 換算與 `!= null` 判斷兩項不變。)
- 4.4:**⚠ 給 5.1**:`#signal` 現在含兩個 `<p>`(`.signal-reading` 與 `.signal-note`),說明那行要做成從屬樣式,讓「參考而非判決」在視覺上也讀得出來。`#signal` 骨架裡的靜態文字「勝負難料」已被取代,`#waiting` 的文字每次 render 都會被覆寫。錯誤區塊是單一 `<p>` 無分類 class —— 若要讓可重試與須重來有不同樣式,得先在 `app.js` 加 class hook。
- 4.4:三處非阻斷的測試敏感度缺口(程式正確,缺護欄):使用者方信號換算未被測到(所有 fixture 都是紅先,硬編 `winner === 'red'` 會存活);載入中與思考中的等待文案只斷言非空;`wait_for_reply` 與 `wait_settled` 是兩檔重複的相同輔助函式。
- 5.1:版面為**單一 flex 容器加 `flex-wrap`,無媒體查詢** —— 寬度連續,不存在「剛好卡在斷點上」的尺寸。行動裝置適配真正靠的是 `#board svg { width: 100%; height: auto }`(蓋掉 `board.js` 寫在元素上的 width/height 屬性,長寬比由 viewBox 保持)。
- 5.1:**`min-width: 0` 是防禦性的空操作,不是適配解法**。實作者原本在註解裡宣稱它是必要的,review 以突變推翻:移除後 320/360/390/414/768/1280 六個尺寸的量測**逐位元組相同**。註解已更正 —— 留著錯誤說明會讓維護者保住空操作、刪掉真正有用的那行。
- 5.1:CSS **完全沒有 `pointer-events` 宣告**(只在註解裡提到),3.2 吃子標示的 `pointer-events: all` 因此完好。`[hidden] { display: none !important }` 保護 `app.js` 對 `#waiting` / `#error` 的控制。
- 5.1:**⚠ 給 5.2 的既知脆弱性**:`tests/test_web_play.py` 的 `click_square()` 以 **viewBox 使用者座標**呼叫 Playwright 的 `position`,而那是 CSS 像素。目前成立只因為 `--board-max-width: 576px` 剛好等於 viewBox 寬度、且預設視窗夠寬。review 實測:把該變數改為 480px 會讓 **38 條測試失敗且大量掛住(1060 秒 vs 平常 30 秒)**。另外 `box-sizing: border-box` 加 2px 邊框已使實際偏移達 (3.7, 4.2)px,對半格 30.8px 尚有約 7 倍餘裕。**5.2 若新增點擊測試,請改用依實際 bounding box 縮放的座標。**
- 5.1:`#moves` 設了 `overflow-y: auto`,依 CSS 規範會**強制 `overflow-x` 也成為 `auto`** —— 水平溢出被它自己的內部捲軸吸收,`li` 的佈局盒永遠不會超出容器。拿 `li` 右緣跟視窗比的斷言**結構上不可能失敗**,必須比 `#moves` 自己的 `scrollWidth` 與 `clientWidth`。
- 5.2:端到端測試**零攔截** —— 真實 uvicorn(subprocess)、真實引擎池、真實 Pikafish。review 實測跑測試期間有 **2 個 pikafish 進程**(服務池 1 個 + 本地算紅方著法 1 個)。31 個半回合、16 次真實 `POST /api/black-move`,信號讀數自「約 16 步」嚴格遞減至「約 0 步」。
- 5.2:**紅方著法是當場算的不是寫死的** —— 寫死序列的前提是服務端引擎每次回同一手,而那取決於該進程的雜湊表狀態(非契約)。代價是測試期間同時有兩個引擎進程。
- 5.2:中文記譜的對照組是**純 Python 獨立推導**(該檔零 `page.evaluate`),不是拿 `notation.js` 的輸出當期望值。review 以「對調前/後排序」與「破壞黑方縱線編號」兩個突變證實它有牙齒。
- 5.2:**引擎殘留斷言抓不到「拿掉 lifespan 的 `pool.shutdown()`」** —— 父進程一死,Pikafish 讀到 stdin EOF 就自行結束。該斷言只證明「本測試沒留垃圾」。真正涵蓋關閉掛鉤的是 `tests/test_main.py::test_shutdown_leaves_no_engine_subprocess_behind`(它在 live pytest 父進程下用替身引擎,不適用 EOF 逃生門)。
- 5.2:**⚠ 待補的守衛測試**:刪掉 `game.js` 的 `acceptsMoves` 中的 `!over`,**608 條測試全數存活**。原因是紅勝的終局是奇數半手,`turn !== userSide` 已擋住輸入,`!over` 在該情境下冗餘。要隔離它需要一個「已結束但仍輪到使用者」的終局(使用者落敗),而 3.2 的紅勝情境結構上產生不出來。建議在 `tests/test_web_game.py` 補一條攔截式守衛測試。
- 5.2:**訂正兩處註解**(`tests/test_web_pure.py`、`web/notation.js`):原本聲稱「第 21 局全程沒有同名子共線」,實測推翻 —— 起始 FEN 的 d1/d3 就是一對黑卒共線。

### 側欄文案精簡(使用者看過實際畫面後的決定,不屬任何編號任務)

接續 problem-browser 4.5 拆掉重複那三行的同一輪:側欄剩下的「**標籤:值**」形態一併拿掉,只留值本身。形態照 `poc/index.html` —— POC 的 signal 區塊直接寫「勝負難料」,沒有「參考信號:」。

- **`#turn`(8.4、3.2)**:`輪方:紅方(你)` → `輪到你`;`輪方:黑方` → `黑方走棋`;`對局結束:紅方勝(你獲勝)` → `你獲勝`;`對局結束:黑方勝` → `黑方勝`;有結束但後端沒給勝方時仍是 `對局結束`;無題目時 `輪方:—` → `—`(`play.html` 的靜態預設值一併改)。那一格永遠只放這一件事,名目每次重畫都把同一句廢話再說一遍;而「可辨識」改由**該不該我動**承擔,比自己執的顏色叫什麼更直接。
- **`#signal`(4.1、4.2、4.4)**:`參考信號:` 前綴整條移除;倒數由 `(約 N 步)` 改為全形空格分隔的 `即將取勝　約 N 步`(取自 POC 的 `renderSignal`「紅勝　N 回殺」)—— 括號在中文裡讀起來像補述,而倒數正是這一行最要看的數字。註記「僅供參考,不是勝負判決;對局只在真終局結束。」縮成 **`僅供參考`**。
- **4.4 為什麼縮得下來**:原本那句話比讀數本身還長,而後半句「對局只在真終局結束」講的是系統內部規則,不是使用者此刻要做的判斷。4.4 現在由三份合起來承擔 —— 文案(`僅供參考`)、語意(`#signal` 的 `role="note"`,**不得改為 `status`**)、視覺(`.signal-note` 字級與顏色低於 `.signal-reading`)。三份各自都有測試釘住,見下。
- **測試調整**(既有 729 / 730 條零退化):
  - `tests/test_web_play.py::test_the_current_turn_is_shown` —— 原本找「紅」字,新文案沒有顏色,改為釘死整句 `輪到你`。
  - `…::test_the_winner_is_shown_when_the_game_ends`、`…::test_losing_is_reported_as_the_backend_says` —— 原本等 `#turn` 出現「結束」二字,新文案是 `你獲勝` / `黑方勝`。改等「勝」字(對局中的兩種說法都沒有它),再釘死整句。
  - `…::test_the_mate_countdown_is_shown_as_an_approximation` —— 原本只驗 `"4" in` 與 `"約" in`,前綴跑回來察覺不到。改為釘死 `.signal-reading` 整句 `即將取勝　約 4 步`。
  - `…::test_the_signal_is_presented_as_advisory_not_a_verdict` —— 原本只驗 `"參考" in #signal`。改為逐項驗三份保障:`.signal-note == "僅供參考"`、`#signal` 的 `role == "note"`、`#turn == "輪到你"`(順帶把原本那條 `"結束" not in #turn` 的弱斷言換成強的)。**`role` 那一條是新增的** —— 在此之前全樹沒有任何測試釘住它,改成 `status` 是靜默通過的。
  - `tests/test_web_e2e.py` —— 四個常數改值,兩個一併改名(`TURN_RED` → `TURN_YOURS`、`GAME_OVER_RED_WON` → `GAME_OVER_YOU_WON`,值裡已不再有顏色);`WINNING_SIGNAL` 正規式改為 `^即將取勝　約 (\d+) 步$`,頭尾錨定使前綴跑回來就對不上。
- **突變驗證**(五項,逐一施加後確認轉紅且失敗訊息是自己寫的斷言,非 `TypeError`、非逾時):前綴跑回 `#turn`、前綴跑回 `.signal-reading`、倒數整段拿掉、`僅供參考` 改成空字串、`role="note"` 改成 `role="status"`。
