# Research & Design Decisions: corpus-editor

## Summary

- **Feature**: `corpus-editor`
- **Discovery Scope**: Extension(既有系統的延伸:`web/` 增一頁、`service/` 增一個受開關控制的唯讀端點)
- **Key Findings**:
  - File System Access API 的寫入授權「直到該來源所有分頁關閉為止」都有效,因此**不需要把目錄控制代碼存進 IndexedDB** —— 授權本來就活不過分頁,持久化只會帶來需要重新請求權限的複雜度而換不到任何東西
  - `renderBoard()` 傳入空的 `legalMoves` 時,**沒有任何一格可選取**,唯讀盤面因此是既有模組的自然狀態而非新功能
  - 「前後端驗證規則分家」不必靠紀律解決:把**權威驗證整個放在後端**,前端只留一層淺檢查當即時回饋,前端就不再是任何規則的唯一出處
  - 「既有題目逐字不變」不必靠序列化器的正確性保證:改用**文字層追加**(找到收尾的 `]` 之前插入),既有位元組根本沒有被重寫的機會

## Research Log

### File System Access API 的能力與限制

- **Context**:寫檔路徑選定由瀏覽器直接進行(見 requirements 的 Introduction),整個寫入設計建立在這個平台 API 上,其授權模型與支援範圍必須先確定。
- **Sources Consulted**:
  - [MDN: Window.showDirectoryPicker()](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker)
  - [Chrome for Developers: The File System Access API](https://developer.chrome.com/docs/capabilities/web-apis/file-system-access)
  - [WICG File System Access spec](https://wicg.github.io/file-system-access/)
- **Findings**:
  - 支援範圍:Chrome / Edge / Opera 86 以上的桌面版。**Firefox 與 Safari 只實作 Origin Private File System,不提供本機磁碟的 picker** —— 不是版本落後,是明確不實作這一組方法
  - 授權存續:取得寫入權限後,「該來源的所有分頁關閉之前」都不再詢問;分頁關閉即全部失效
  - 控制代碼可序列化進 IndexedDB 以跨重載復原,但**權限狀態不保證一併存續**,復原後仍須 `requestPermission()`
  - `requestPermission()` 與 picker 的呼叫都**必須由使用者手勢觸發**
  - 需要 secure context;`localhost` 視為安全來源,本機開發不受影響
  - `createWritable()` 取得的串流**在 `close()` 之前不落盤**;規格未承諾原子性(Chrome 實作走暫存檔再置換,但那是實作細節,不是契約)
- **Implications**:
  - R6.3「明確告知不支援」不是防禦性設計而是必要件 —— 兩大瀏覽器確定不能用
  - R6.1「本次分頁首次要求寫入時請求授權」與平台的授權週期完全吻合,控制代碼**只存在模組層變數中**即可,不引入 IndexedDB
  - 授權請求必須掛在寫入按鈕的點擊處理常式內,不得在頁面載入時預先索取
  - 讀檔到寫檔之間沒有鎖:同一個檔案若在這段期間被外部改動(編輯器、git 操作),追加會蓋掉那次改動。列為風險,以「寫入前即時重讀」縮小視窗,不另建鎖機制

### 既有前端模組的可重用程度

- **Context**:R2 要唯讀盤面、R3.2 要難度三選一、R8.2 要盤面外觀與對局頁一致。需確認能重用到什麼程度、以及哪些不能碰。
- **Sources Consulted**:`web/board.js`、`web/fen.js`、`web/difficulty.js`、`web/api.js`、`web/play.html`、`web/style.css`
- **Findings**:
  - `renderBoard(container, {board, legalMoves, selected, onSelect, onMove})`:可選取的定義是「傳進來的著法裡有自該格出發的」。**傳空陣列即整片不可選**,且落點標示與選中框都不會出現
  - `parseFen(fen)` 只解析第一段,**對格式不合法的輸入不拋錯也不回報** —— 缺列、多子、亂字元都會安靜地產生一個殘缺盤面
  - `difficulty.js` 的 `DIFFICULTY_LABELS` 是 `Map<number, string>`,`structure.md` 的三級制在前端的唯一出處
  - `api.js` 的模組說明明寫「對外只有兩個操作」,且其錯誤分類 `ApiErrorCode` 為凍結列舉、可安全匯入
  - `style.css` 以單一 flex 容器加 `flex-wrap` 達成左盤右欄,無媒體查詢
- **Implications**:
  - R2.3(盤面不可互動)由 `renderBoard` 的既有語意直接滿足,**不需要新增任何「唯讀模式」參數** —— 對 `board.js` 零改動
  - R2.4(無法解析要顯示訊息)**不能靠 `parseFen`**,它不會失敗。需要另建結構檢查,且**不得為此修改 `fen.js`** —— 那份寬鬆是對局路徑的既有契約,收緊它會波及 web-play-runtime
  - 難度選項自 `DIFFICULTY_LABELS` 產生,不在收題頁另寫一份說法
  - 版面沿用同一套 flex 慣例,不引入新的版面機制

### 既有題目檔的排版形狀

- **Context**:R5.8 要求寫入後的排版與既有檔案一致。需確認「一致」的具體形狀。
- **Sources Consulted**:`positions/適情雅趣~卷一/20-24.json`、`25.json`
- **Findings**:
  - 兩格縮排;物件的每個欄位各自一行;**`tags` 陣列寫在同一行**(`"tags": ["解殺還殺", "鐵門栓"]`);中文不轉義(非 ASCII 直接輸出);檔案以換行結尾
  - `JSON.stringify(value, null, 2)` 會把 `tags` 展開成多行,**與既有形狀不符**
- **Implications**:
  - 需要自訂序列化,不能直接用內建的縮排輸出
  - 但序列化只需對**新題**正確 —— 見下方「文字層追加」的決策

### 服務端的設定與掛載機制

- **Context**:R1.3–R1.6 的開關要落在既有機制上,不另造一套。
- **Sources Consulted**:`service/config.py`、`service/main.py`、`service/positions.py`、`service/engine/process.py`
- **Findings**:
  - `Settings` 是凍結 dataclass,`load_settings()` 以 `LEETCHESS_` 前綴讀環境變數,已有 `_read_int` / `_read_float` / `_read_path`,**沒有布林讀取器**
  - `_WebFiles` 是既有的遮蔽機制:它讓 `/api` 開頭的路徑對靜態檔路由不可見,理由是不讓靜態檔吃掉路由層的 404/405
  - `EngineProcess.legal_moves(fen, moves, timeout)` 直接吃裸 FEN,不需要題號
  - `service/positions.py` 的 `_read_position()` 是題目 schema 的權威實作,目前為私有
  - 依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`
- **Implications**:
  - `config.py` 需補一個 `_read_bool`,其餘沿用既有形狀
  - 開關關閉時的頁面遮蔽可**完全重用 `_WebFiles` 的既有機制**,只多一條前綴 —— 前提是收題頁的檔案集中在單一路徑前綴底下
  - 候選題目的驗證邏輯落在 `game.py` 同層(需要 positions 與 engine 兩者),故新增 `service/editor.py`
  - `_read_position()` 需以一個公開包裝暴露,否則收題頁的驗證會被迫複製一份 schema 規則

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 前端自帶完整驗證 | 前端實作全套 schema 規則,後端只提供引擎合法性 | 送出前即知結果、後端改動最小 | **與 `service/positions.py` 形成兩份規則**,漂移無機制可擋 | 已否決,見 Decision 1 |
| 後端權威驗證 + 前端淺檢查 | 前端只檢查「填了沒」,權威判定整批送後端 | 規則只有一份;前端錯了後端仍會擋 | 每次送出多一次往返(本機服務,可忽略) | **已採用** |
| 解析後整檔重寫 | 讀檔、parse、push、整份重新序列化 | 實作直觀 | 既有題目的位元組是否不變,取決於序列化器完全正確 | 已否決,見 Decision 2 |
| 文字層追加 | 讀檔文字、定位收尾的 `]`、插入新題文字 | **既有位元組沒有被重寫的機會** | 需處理空陣列、尾隨逗號、換行等文字細節 | **已採用** |

## Design Decisions

### Decision 1:題目 schema 的權威驗證放在後端,前端只做淺層即時回饋

- **Context**:`brief.md` 列為必須在 design 解掉的問題 —— 前端驗證若是 `service/positions.py` 的鏡像,兩份規則遲早分家,而「記得兩邊一起改」不是機制。
- **Alternatives Considered**:
  1. 前端實作全套規則,以測試比對兩邊 —— 測試能抓到漂移,但規則仍是兩份,且比對測試本身要跟著兩邊改
  2. 後端提供驗證端點,前端不實作任何 schema 規則
- **Selected Approach**:分成兩層,職責不同。
  - **前端淺層(即時)**:欄位是否為空、題號是否為正整數、標籤是否至少一個、FEN 結構是否可解析。用途是**填寫過程中的即時回饋與按鈕停用**,對應 4.1、4.2、4.6、8.4
  - **後端權威(送出時)**:以 `service/positions.py` 的同一份實作驗證整個候選題目,並向引擎確認 FEN 可載入。這是**唯一的放行判準**,對應 4.7、4.8
- **Rationale**:前端那一層永遠不是唯一出處 —— 它漏判時後端仍會擋下,它誤判時使用者看到的是「還沒填完」而不是壞資料進了題庫。漂移的代價從「壞資料進題庫」降為「即時回饋不夠即時」。
- **Trade-offs**:每次送出多一次本機往返(可忽略);收題頁因此**必須連得上服務**才能寫入,這與「寫檔不經後端」並不矛盾 —— 不經後端的是**寫入**,不是**判定**。
- **Follow-up**:實作時確認 `validate_position()` 與 `_read_position()` 走同一條路徑,不是複製一份。

### Decision 2:以文字層追加寫入,不重寫既有內容

- **Context**:5.7 要求寫入後既有的每一題逐字不變,5.8 要求排版與既有檔一致。
- **Alternatives Considered**:
  1. 讀檔 → `JSON.parse` → 陣列末端 push → 整份重新序列化寫回
  2. 讀檔文字 → 驗證可解析為陣列 → 在收尾的 `]` 之前插入新題的文字
- **Selected Approach**:方案 2。仍然 `JSON.parse` 一次,但**只用於驗證**(5.6 的「內容不是題目陣列」);實際寫出的內容是原始文字加上插入的片段。
- **Rationale**:方案 1 的 5.7 是一個需要被證明的性質(序列化器對每一種既有輸入都剛好還原);方案 2 的 5.7 是**構造上的事實**,既有位元組原樣被搬過去。序列化器的正確性因此只需覆蓋新題一種輸入。
- **Trade-offs**:要處理文字細節 —— 空陣列 `[]` 沒有前一個元素、末端可能已有換行、插入時要為前一個元素補逗號。這些是有限且可窮舉的情況,以測試釘住。
- **Follow-up**:序列化器以既有題庫檔做回歸比對(見 Testing Strategy),確保新題的排版與現況同形。

### Decision 3:收題頁的檔案集中在單一路徑前綴,開關關閉時整段遮蔽

- **Context**:1.3 要求開關未啟用時頁面無法取得。
- **Alternatives Considered**:
  1. 收題頁的檔案與既有前端平鋪在 `web/` 下,逐一列舉要遮蔽的檔名
  2. 收題頁的檔案全部收進 `web/editor/`,以單一前綴遮蔽
- **Selected Approach**:方案 2。`_WebFiles` 既有的 `/api` 遮蔽機制多一條 `/editor` 前綴。
- **Rationale**:逐一列舉會在新增檔案時漏掉,而漏掉的後果是「開關關了但檔案還在」。前綴規則對新增檔案自動成立。同時完全重用既有機制,不新增第二種遮蔽方式。
- **Trade-offs**:收題頁的模組需以 `../` 匯入共用模組,路徑略長。
- **Follow-up**:確認 `StaticFiles(html=True)` 使 `/editor/` 直接落到 `web/editor/index.html`。

### Decision 4:不持久化目錄控制代碼

- **Context**:File System Access API 允許把控制代碼存進 IndexedDB 以跨重載復原。
- **Alternatives Considered**:
  1. 存進 IndexedDB,重載後嘗試復原並以 `requestPermission()` 取回權限
  2. 只留在模組層變數,分頁存續期間有效
- **Selected Approach**:方案 2。
- **Rationale**:寫入權限本來就在「所有分頁關閉」時失效,持久化控制代碼救不回權限,復原後仍需要一次使用者手勢 —— 與重新選一次目錄的成本差別極小,卻要引入 IndexedDB 的儲存、版本與失效處理。沒有任何一條驗收條件要求跨重載保留。
- **Trade-offs**:重整頁面後首次寫入要重選目錄。已由 6.1 明文接受。
- **Follow-up**:無。

### Decision 5:撞號檢查 = 題庫索引快照 ∪ 本分頁已寫入的題號

- **Context**:4.3 要擋既有題號,4.4 要擋本次已寫入但尚未進索引的題號,4.5 要放行兩者皆無的題號。
- **Alternatives Considered**:
  1. 以目錄控制代碼掃描整個題庫目錄自行建索引 —— 需要複製一份掃描與解析邏輯
  2. 取 `GET /api/catalog` 的題號,聯集本分頁已成功寫入的題號
- **Selected Approach**:方案 2,且**每次寫入前重新取一次索引**而非只在載入時取一次。
- **Rationale**:開發啟動腳本會在題目檔變動時重啟服務,索引因此會自行跟上;重新取一次可讓已進索引的題號自然歸位。本分頁的集合負責覆蓋「已寫入但服務尚未重啟完成」的窗口。方案 1 要在前端重建一份題庫掃描,與 Decision 1 的「不複製規則」相牴觸。
- **Trade-offs**:服務重啟期間索引請求可能失敗。此時寫入不成立,落在 7.3 的一般失敗處理 —— 這是移除原 4.10 之後刻意接受的歸屬。
- **Follow-up**:本分頁集合只在**寫入成功後**加入,失敗的嘗試不得佔用題號。

## Synthesis Outcomes

### Generalization

- R4 的各條驗證是同一件事的變體:「一項檢查產出一則可定位到欄位的訊息」。前端淺層檢查因此設計成**一組檢查函式回傳 `{field, message}` 清單**,而非各自散落的 if。8.4「指出是哪一項未通過」由這個形狀直接滿足。
- 後端驗證回傳同形的清單,前端呈現層不必分辨訊息來自哪一層。

### Build vs. Adopt

| 需求 | 決定 | 理由 |
|---|---|---|
| 盤面繪製(2.1、2.2、8.2) | **採用** `board.js` 的 `renderBoard`,零改動 | 空 `legalMoves` 即唯讀 |
| FEN 展開為盤面(2.1) | **採用** `fen.js` 的 `parseFen` | 已驗證;不得為本 spec 收緊它 |
| FEN 結構檢查(2.4) | **自建**(收題頁內) | `parseFen` 不會失敗,無現成物可用 |
| 難度說法(3.2、8.3) | **採用** `difficulty.js` 的 `DIFFICULTY_LABELS` | 三級制的前端唯一出處 |
| 後端失敗分類(4.8) | **採用** `api.js` 的 `ApiErrorCode` | 避免第二套錯誤分類;只匯入不修改 |
| 題目 schema 驗證(4.7) | **採用** `service/positions.py` 的既有實作,加一層公開包裝 | Decision 1 |
| 引擎合法性(4.7) | **採用** `EngineProcess.legal_moves` | 已能吃裸 FEN |
| JSON 排版(5.8) | **自建**序列化器 | 內建縮排輸出與既有形狀不符 |

### Simplification

- 移除 IndexedDB 持久化(Decision 4)
- 不新增錯誤類別碼:驗證未通過是**結果**而非錯誤,以 200 回傳結構化結果,既有的七種類別碼只用於真正的服務失敗
- 不建書目或檔案的選單:路徑手填已由 5.1 定案
- 不為收題頁另建後端 client 模組的錯誤分類,直接匯入既有列舉

## Risks & Mitigations

- **讀檔與寫檔之間檔案被外部改動** —— 追加會蓋掉那次改動。以「按下寫入時才重讀目標檔」把視窗縮到最小;不建鎖機制(單人本機工具,代價不成比例)
- **前端淺層檢查與後端權威驗證給出不同說法** —— 後端為準;前端訊息措辭與後端對齊,且前端永遠不是放行判準
- **開關誤開於線上** —— 預設未啟用(1.6),且遮蔽以路徑前綴而非逐檔列舉(Decision 3);部署檢查歸 service-deploy-ops
- **序列化器與既有排版漂移** —— 以既有題庫檔做回歸比對,新增書目或欄位時該測試會先紅
- **`positions.py` 的公開包裝被誤用為寫入路徑** —— 該函式只驗證並回傳 `Position`,不碰檔案系統;`positions.py` 的「唯讀」模組契約不變

## References

- [MDN: Window.showDirectoryPicker()](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker) — 支援範圍與 secure context 要求
- [Chrome for Developers: The File System Access API](https://developer.chrome.com/docs/capabilities/web-apis/file-system-access) — 授權存續、使用者手勢、`createWritable` 的落盤時機
- [WICG File System Access](https://wicg.github.io/file-system-access/) — 規格本文
- `.kiro/steering/structure.md` — 題目 schema、難度三級制、目錄佈局與命名慣例
- `.kiro/steering/tech.md` — vanilla JS 無建置步驟、引擎抽象介面、勝負判定不自行實作
