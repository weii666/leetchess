/**
 * 收題頁的組裝層:把五個欄位接到盤面、難度選項、淺層檢查、撞號檢查、權威驗證與
 * 權威驗證上(tasks 4.2、4.3、5.1、5.2;requirements 2.1–2.6、3.2、3.3、3.6、3.7、
 * 4.1–4.9、8.3、8.4)。
 *
 * 這是收題頁依賴鏈的最右端,也是**唯一知道 DOM 存在的模組**(design 的
 * Components and Interfaces)。`check.js` 是純函式、`corpus-file.js` 是純函式、
 * `fs.js` 是平台包裝,三者都不得為了這一頁而長出對 DOM 的認識;反過來,本檔也
 * 不實作任何檢查規則 —— 「這串 FEN 展不展得開」的判準只有一份,在 `check.js`。
 *
 * ## 只有一條寫進畫面的路徑
 *
 * 與對局頁的 `app.js` 同一個形態:每一次變動都由 `render()` 自**當下的輸入值**
 * 整份重畫,沒有任何一處是「事件發生時順手改一下某個節點」。增量更新等於在呈現
 * 層再存一份狀態,而那份狀態遲早與輸入框裡的字分家 —— 使用者核對的會是一個已經
 * 不在輸入框裡的局面,那比什麼都不畫更危險(收題工具存在的理由就是肉眼核對 FEN)。
 *
 * 因此畫面該長什麼樣幾乎完全是五個欄位當下那些字的函式。模組層的狀態變數只有六個,
 * 它們記的都**不是畫面**,而是幾件無法由當下的值看出來的事:
 *
 * - `writtenIds` —— 本分頁已經成功寫入過哪些題號
 * - `collidedId` —— 上一次寫入嘗試指認了哪個題號撞號
 * - `candidateVerdict` —— 權威驗證對哪一份候選題目說了什麼
 * - `attemptNote` —— 上一次嘗試為什麼沒寫出去
 * - `successNote` —— 上一次寫入成功的是哪一題、進了哪個檔
 * - `writing` —— 此刻有沒有一次嘗試正在進行
 *
 * 六者一律只經 `render()` 反映到畫面上。**選定的檔案不在其中** —— 它是 `fs.js` 的
 * 模組層狀態(design 的 Invariants:同一次分頁使用期間沿用)。本檔每次要用時都問
 * `selectedFile()`;在這裡再記一份就是第二個真相來源,而分家時畫面說的檔名會與真正
 * 寫進去的那個檔不同。
 *
 * ## 未通過的項目呈現在兩個地方,而且是同一份清單
 *
 * `checkForm()` 回傳的每一項都定位到欄位,因此**填了而填錯**的那些呈現在該欄底下的
 * 訊息槽(8.4)。寫入操作旁邊那一行則是同一份清單的**點名匯總**,而且無條件涵蓋
 * 每一項。兩處都由同一次 `render()` 寫入,不會各說各話。
 *
 * 分成兩處不是重複,而是因為那兩句話對使用者根本不同:「題號必須是正整數」是在指出
 * 他打錯了,「請填入題號」對著一個他還沒走到的空格,講的只是這張表單還沒填完 ——
 * 而那件事整份講一次就夠。**空著的欄位因此一句話都不說**,那一項未通過改由點名表達
 * (見 `renderMessages`)。
 *
 * ## 盤面唯讀,靠的是空的著法集合
 *
 * `board.js` 的可選取判準是「傳進來的著法裡有自該格出發的」,傳空陣列時整片盤面
 * 自然不可選、也畫不出任何落點標示(requirement 2.3)。**唯讀盤面因此是既有模組
 * 的自然狀態,不是新增的參數** —— 不要為這一頁給 `renderBoard()` 加一個唯讀開關,
 * 那會讓對局頁跟著多背一個它用不到的分支。
 *
 * ## 空的輸入與解析不了的輸入是兩件事
 *
 * `checkFenStructure('')` 回的是「請填入 FEN」,那是給寫入前的必填檢查用的說法
 * (4.1),不是「這串字無法解析」。照著顯示的話,一個**還沒開始填**的欄位就會掛著
 * 錯誤訊息,而 requirement 2.5 要的是清空輸入即呈現空盤面、不報錯。兩者在本檔分成
 * 兩條路(見 `readFenView`),這是 tasks.md 對 3.1 的筆記點名的地方。
 *
 * ## 寫入是一條序列
 *
 * 寫入一題的完整次序是(design 的 System Flows):**取題庫索引 → 撞號 → 送權威驗證
 * → 重讀目標檔 → 文字層追加 → 寫回 → 記下題號並清空欄位**;選檔則是序列**之外**
 * 的一個獨立操作(5.1)。
 *
 * 序列分成兩段函式:`runWriteSequence()` 是**擋得下來的那些檢查**(索引、撞號、
 * 驗證),`writeCandidate()` 是**通過之後真的動到磁碟的那幾步**。切在這裡是因為前段
 * 的每一步都可能讓寫入不成立,而後段的每一步都已經假定它成立了。
 *
 * 外面再包一層 `attemptWrite()`,它管的是**與這一次嘗試同生共死的兩件事**:進行中
 * 停用寫入操作(避免重複送出),以及把預期內的停止翻成畫面上的一句話。兩者都寫在
 * `try`/`finally` 裡,所以任何一條例外路徑都不會讓按鈕永遠灰著。
 *
 * 淺層檢查未通過時 `#write` 仍然是停用的(4.1),所以序列跑得起來就代表五個欄位
 * 都已填妥 —— 撞號檢查不必再驗一次題號的寫法。
 *
 * ## 淺層檢查不是放行判準,權威在服務端
 *
 * `check.js` 只回答「填了沒、形狀對不對」。**這一題進不進得了題庫由
 * `POST /api/editor/validate` 判定**(4.7):它走的是 `service/positions.py` 的同一份
 * schema 規則,再加上向引擎確認這個 FEN 載不載得進去。本檔因此在撞號通過之後必然
 * 送出一次驗證,而不是「淺層檢查過了就寫下去」—— 那等於讓一份前端看得順眼、服務
 * 端啟動時卻載不起來的題目進版本庫,而那正是本工具存在的理由。
 *
 * ## 「這一題不合格」與「確認未能完成」是兩條路,而且不能互相折疊
 *
 * 驗證端點**恆以 200 回答判定**:不合格時回 `valid: false` 與 `issues`(`main.py` 的
 * 「未通過是『結果』,不是錯誤」)。真正的服務失敗 —— 池滿 503、逾時 504、未預期
 * 500、連線斷掉、逾時無回應 —— 走的是既有的 `{code, message}` 形狀,那時服務**根本
 * 沒有判定可言**。
 *
 * 兩者對維護者的意義完全相反,所以本檔分成兩條呈現路徑:
 *
 * - **不合格**:未通過項目與淺層檢查的 `CheckIssue` 同形,**寫進同一組欄位訊息槽**
 *   (8.4)。呈現層因此分不出、也不必分出一項是前端檢出的還是服務端判定的 —— 維護者
 *   要做的事一模一樣:去改那一格。
 * - **確認未能完成**(4.9):寫入操作旁那一行說明,而且**不指向任何欄位**。它講的是
 *   「這次確認沒有跑完」,不是「你哪裡填錯了」。
 *
 * 兩個方向的誤讀各是一種災難:把服務失敗讀成通過,一份沒驗過的題目會直接寫進題庫;
 * 讀成不合格,則是誣賴一份可能完全沒問題的題目,還讓維護者回頭去改一個本來就對的
 * FEN。因此**判定只從一個認得出來的 200 主體讀出**(`isVerdict`),認不得的形狀一律
 * 落到「確認未能完成」那一邊。
 *
 * ## 失敗的類別沿用 `api.js`,但請求是自己發的
 *
 * 類別碼用 `api.js` 的 `ApiErrorCode`(design 的 Error Categories 明載不新增分類):
 * 這個端點回的就是那七種後端契約碼之一,而 `TIMEOUT` / `NETWORK` / `UNKNOWN` 三種
 * client 自判的情況在這裡同樣成立。**但發請求的那段程式碼不共用** —— `api.js` 的
 * `request()` 未 export,對外只有 `loadPosition` 與 `requestBlackMove` 兩個位址寫死的
 * 操作,構造上到不了這個端點(design 的 Dependencies 已載明這件事)。`catalog.js` 對
 * `/api/catalog` 遇到的是同一件事,它的解法是自己寫一次帶逾時上界的 fetch,本檔沿用
 * 同一個形狀:**沿用的是類別的那一份列舉,重複的只有十來行傳輸細節**。
 *
 * 與 `catalog.js` 不同的是本檔**沿用它的錯誤類別而不是另立一套**:索引端點不借引擎,
 * 契約裡那七種類別對它沒有意義;驗證端點會借引擎,`SERVICE_BUSY` 與 `ENGINE_TIMEOUT`
 * 是它真的會回的東西。
 *
 * ## 判定是對「那一份候選題目」說的
 *
 * 判定無法由當下的輸入值算出來(要問過服務端才知道),但「這份判定還成不成立」可以:
 * 候選題目的內容一改,上一次那句話講的就是另一份東西了。`candidateVerdict` 因此連同
 * **送出去的那一份候選題目**一起記下來,只在當下組得出的候選題目與它逐欄相同時才顯示
 * (與 `collisionIssues()` 對題號做的事是同一條規則)。
 *
 * 這條規則還有一個附帶的好處:判定顯示得出來時,七項淺層檢查必然全過(不然那一次
 * 嘗試根本按不下去),所以欄位訊息槽裡不可能同時有淺層與服務端兩句話 —— 兩者搶同一格
 * 的情況構造上不存在。
 *
 * ## 撞號檢查看的是一個聯集,而且每一次嘗試都重新取索引
 *
 * 判準是「題號在不在 `GET /api/catalog` 的既有題號 ∪ 本分頁已成功寫入的題號裡」
 * (research 的 Decision 5)。兩半各補一個時間窗:索引是**服務啟動時的快照**,而
 * 開發啟動腳本要等題目檔變動觸發重啟才會換上新的一份,所以剛寫進去的一題會有一段
 * 「檔案裡有、索引裡沒有」的空窗(4.4);反過來,已經進了索引的題號會因為**每一次
 * 嘗試都重新取一次索引**而自然歸位,不必由本分頁的集合永遠背著。
 *
 * 本分頁的集合**只在寫入成功後才加入**(`recordWrittenId`),失敗的嘗試不佔用題號。
 * 這一輪還沒有「寫入成功」這件事(那是 5.3),所以本檔內沒有任何一處呼叫它 ——
 * 規則屬 5.1,發生的時機屬 5.3,兩者刻意分開。
 *
 * 索引取不到(服務重啟中、連線斷掉、逾時)時**序列就停在那裡**,而且絕不折成
 * 「一份空索引」繼續走:那會讓最容易撞號的那一刻(剛寫完一題、服務正在重啟)
 * 變成最放行的一刻。此時的說法落在一般寫入失敗處理(design 的 Risks:不另立分支)。
 *
 * ## 撞號的說法定位在題號那一欄,而且不停用寫入
 *
 * design 的 Error Handling 把撞號歸在「可自行修正」那一類 —— 定位到欄位(8.4)、
 * 保留表單內容。它因此**不進 `checkForm()` 的清單**:那份清單的每一項都是「這樣寫
 * 下去服務端會拒絕」而且由當下的值算得出來,撞號則是一次嘗試的結果。
 *
 * 停用寫入也是錯的:撞號檢查在每一次按下時重跑,擋不掉的寫入不存在,而停用會讓
 * 維護者無法對同一個題號再試一次(索引可能剛好正在重啟)—— 他得先把題號改掉再改
 * 回來才按得下去,那是一個沒有理由的手續。
 */

import { ApiError, ApiErrorCode, DEFAULT_TIMEOUT_MS } from '../api.js';
import { renderBoard } from '../board.js';
import { CatalogError, loadCatalog } from '../catalog.js';
import { DIFFICULTY_LABELS } from '../difficulty.js';
import { parseFen } from '../fen.js';
import { checkForm, checkFenStructure, parseTags } from './check.js';
import { CorpusFileError, appendPosition } from './corpus-file.js';
import {
  PermissionDeniedError,
  UnsupportedBrowserError,
  isSupported,
  pickCorpusFile,
  readText,
  selectedFile,
  writeText,
} from './fs.js';

/**
 * 空盤面的 FEN(requirement 2.5)。
 *
 * 刻意經 `parseFen()` 產生而不是自己組一個 10x9 的 `null` 陣列:盤面陣列的形狀是
 * `fen.js` 的契約,在這裡另建一份等於多一個要跟著改的地方。
 */
const EMPTY_FEN = '9/9/9/9/9/9/9/9/9/9 w - - 0 1';

/**
 * 傳給 `renderBoard()` 的著法集合 —— **永遠是空的**,requirement 2.3 就靠這件事
 * 成立。取個名字是為了讓「這裡為什麼傳空陣列」在呼叫處看得見。
 */
const NO_LEGAL_MOVES = Object.freeze([]);

/*
 * **起手方不再有呈現。** 原本 FEN 底下印著「起手方:紅先」,由 `check.js` 的
 * `sideFromFen()` 推出來(該函式已一併移除)。拿掉的理由是它不告訴收題的人任何他不
 * 知道的事:走子方就在他剛貼上的那串字裡,而盤面畫出來時紅方永遠在下。
 *
 * **這不改變起手方的來源** —— 它仍只由 FEN 表達,題目檔裡沒有這個欄位
 * (`corpus-file.js` 寫出的欄位裡沒有 `side_to_move`),畫面上也仍然沒有它的輸入。
 */

/**
 * 盤面被清掉時放在那一格的說法(2.4)。
 *
 * 只交代「這一格為什麼空著」,**不重複哪裡不對** —— 那句話定位在 FEN 欄位旁邊,
 * 兩處說同一件事只會讓使用者以為有兩個問題。
 */
const CLEARED_BOARD_NOTE = 'FEN 目前無法解析,盤面已清空。';

/**
 * 五個欄位的名稱,**順序即表單欄位順序**,也就是 `checkForm()` 回傳清單的順序
 * (該函式明載這件事)。寫入操作旁的點名匯總照這個順序念,使用者由上往下找得到。
 *
 * **FEN 排第三**(8.5):它是唯一需要對著盤面逐字核對的欄位,而盤面就在左邊。
 * 描述與目標檔案路徑不在其中 —— 前者整欄移除(3.9),後者改由檔案選擇框選定(5.1)。
 */
const FIELD_NAMES = Object.freeze(['id', 'title', 'fen', 'difficulty', 'tags']);

/**
 * 載入時預設選中的難度(`renderDifficultyOptions`)。
 *
 * 取的是 `difficulty.js` 那份對照表的**中間一個鍵**,不是寫死的 `2`:分級若有增減,
 * 這裡跟著動,而不是留下一個指向不存在選項的數字。三級以外的長度也算得出一個中間
 * 值,`Math.floor` 讓偶數個時偏向較容易的那一邊 —— 猜錯難度時,猜低的那一邊比較不會
 * 讓人卡在一題上。
 */
const DEFAULT_DIFFICULTY = [...DIFFICULTY_LABELS.keys()][
  Math.floor((DIFFICULTY_LABELS.size - 1) / 2)
];

/** 停用寫入時那一行的開頭(8.4)。後面接上未通過項目的欄位名稱。 */
const WRITE_NOTE_PREFIX = '無法寫入,尚未通過:';

/** 未通過的項目全都不屬於任一欄位時的退路 —— 空清單不會走到這裡。 */
const WRITE_NOTE_FALLBACK = '無法寫入,仍有項目未通過。';

/** 欄位名稱之間的分隔。頓號是中文的列舉符號,與提示裡的用法一致。 */
const NAME_SEPARATOR = '、';

/**
 * 權威驗證端點(design 的 API Contract)。
 *
 * 這是本檔唯一寫得出位址的後端端點:索引走 `catalog.js`(它是 `/api/catalog` 既有的
 * 唯一 client),需要引擎的對局端點在這一頁連字串都不存在。
 */
const VALIDATE_PATH = '/api/editor/validate';

/**
 * 候選題目**由人工編輯**的六個 schema 欄位,順序即寫進題目檔的順序
 * (與 `corpus-file.js` 的 `SCHEMA_FIELDS` 同一份約定)。
 *
 * 取這個名字是為了讓「送出去的是哪五個欄位」在程式碼裡看得見。四個**不在**裡面的
 * 欄位各有理由:`description` 整欄移除(3.9,schema 已改為選填)、`max_dtm` 由驗證
 * 工具回填、`source` 由所在資料夾表達、`side_to_move` 由 FEN 表達(design 的「候選
 * 題目的資料契約」)。後端對未知欄位是直接拒絕的,多送一個就會讓一份完全沒問題的
 * 題目被判為不合格。目標題目檔同樣不在裡面 —— 它是「寫到哪個檔」,不是題目的內容。
 */
const CANDIDATE_FIELDS = Object.freeze(['id', 'title', 'fen', 'difficulty', 'tags']);

/**
 * 後端契約承認的七種類別碼(`service/errors.py` 的 `ErrorCode`)。
 *
 * `api.js` 有一份同樣的集合但未 export,所以在這裡組第二份 —— **組的是同一份列舉的
 * 成員,不是另抄一串字面字串**:`ApiErrorCode` 改了名,這裡會跟著壞掉而不是靜默分家。
 * 這也是 design 的 Error Categories 說的「不新增分類」:本檔一個新的類別都沒有造。
 */
const BACKEND_CODES = new Set([
  ApiErrorCode.INVALID_MOVE_FORMAT,
  ApiErrorCode.POSITION_NOT_FOUND,
  ApiErrorCode.ILLEGAL_MOVE_SEQUENCE,
  ApiErrorCode.WRONG_SIDE_TO_MOVE,
  ApiErrorCode.SERVICE_BUSY,
  ApiErrorCode.ENGINE_TIMEOUT,
  ApiErrorCode.INTERNAL,
]);

/**
 * 確認未能完成時那一行(requirement 4.9)。
 *
 * **服務不可用、逾時、連線失敗共用同一句話**,不依類別碼分成好幾種說法:對維護者
 * 而言六種失敗是同一件事 —— 這次確認沒有跑完,所以什麼都沒有寫出去。要他去理解
 * 503 與 504 的差別,那是服務的內部狀態,不是他要處理的事。
 *
 * 句子裡因此**不帶任何後端原文**:對外的契約只有類別碼(`api.js` 的模組說明),
 * 原文帶著內部路徑與引擎輸出,對維護者沒有用處。
 */
const UNFINISHED_NOTE = '確認未能完成,尚未寫入。請稍後再按一次寫入。';

/**
 * FEN 連送都送不出去時那一句(`INVALID_MOVE_FORMAT`)。
 *
 * 字元把關攔在路由函式之前(`service/models.py`),回的是既有的 400。它**不是**
 * 「確認未能完成」:那句話講的是過一會兒重試就好,而這一種再按幾次都一樣,維護者
 * 該做的是重貼一次 FEN。歸在 FEN 那一欄旁邊,與其餘「可自行修正」的失敗同一類
 * (design 的 Error Strategy)。
 *
 * 這串字**由本檔自己寫**,不取回應裡的 `message`:那是後端原文,而它會把被擋下的
 * 內容原樣帶回來 —— 一個控制字元在畫面上原樣顯示等於在另一個介面上重演同一個問題。
 */
const UNSENDABLE_FEN_MESSAGE =
  'FEN 送不出去:它含有不能送往引擎的字元,或長度超出上限。請重新貼一次完整的 FEN。';

/**
 * 索引取不到時那一行(requirement 7.3;design 的 Risks 把它歸在一般寫入失敗)。
 *
 * 明講「請稍後再按一次」是因為這一種**通常會自己好**:服務在題目檔變動時重啟,而
 * 剛寫完一題正是最容易碰上它的時候(`start-dev.sh` 監看 `positions/`)。
 */
const INDEX_FAILURE_NOTE = '取不到題庫索引,寫入未進行。服務可能正在重啟,請稍後再按一次寫入。';

/**
 * 平台自己的寫入失敗時那一行(requirement 7.3)。
 *
 * **刻意不宣稱題目檔沒有被動過**:寫回是整檔覆寫,失敗在半途的話檔案可能已經被截斷。
 * 一句沒有根據的保證比不講更糟 —— 維護者會照著它跳過檢查。
 *
 * 也**不帶平台的原文**:那是 `DOMException` 的訊息,對維護者沒有用處,而 `fs.js` 的
 * 整個存在理由就是不讓平台細節外流。
 */
const WRITE_FAILURE_NOTE = '寫入未完成。請確認目標檔案目前的內容,再決定要不要重試。';

/**
 * 寫入成功那一句(requirement 7.1)。
 *
 * **題號與目標檔案都要出現在句子裡** —— 7.1 的要求就是這兩件事。理由在連續收題:
 * 選定的檔案是唯一留著不清空的東西(7.2),也因此是最容易在不知不覺間寫錯地方的
 * 那一個。指名道姓,維護者掃一眼就知道剛才那一題落在哪。
 *
 * 檔名取自控制代碼的 `name`,那是平台肯給的**全部**資訊 —— 它不帶所在位置,所以
 * 這句話講得出「26.json」而講不出它在哪個資料夾底下。這是選檔取代路徑之後的已知
 * 代價(見 `fs.js` 的模組說明)。
 *
 * @param {number} id 寫入的題號。
 * @param {string} name 目標題目檔的檔名。
 * @returns {string}
 */
const successText = (id, name) => `第 ${id} 題已寫入 ${name}。可以接著收下一題。`;

/**
 * 尚未選定目標題目檔時那一行(requirement 5.10)。
 *
 * 它與其餘的失敗說法**不同類**:那些講的是「出了什麼事」,這一句講的是「還有一步
 * 沒做」。因此它明講下一步是什麼 —— 畫面上那顆按鈕就在旁邊,而維護者此刻的注意力
 * 在寫入鈕上。
 */
const NO_FILE_NOTE = '尚未選定存檔位置,寫入未進行。請先按「選擇檔案」。';

/**
 * 尚未選定時,檔名顯示位置說的話(5.1)。
 *
 * **不留空白。** 一個空白的位置看起來像壞掉,而這是一個必要的步驟 —— 說出來才知道
 * 它在等什麼。
 */
const NO_FILE_CHOSEN = '尚未選定';

/**
 * 拒絕授權或取消檔案選擇之後那一行(requirement 6.2)。
 *
 * 6.2 要的是兩件事:**保留已填入的全部內容**,以及**告知寫入未進行**。內容的保留
 * 由本檔什麼都不做達成(只有寫入成功才清空,見 `clearPuzzleFields`),所以這裡只剩下說話。
 *
 * 句子裡明講「再按一次寫入」:按 Esc 關掉對話框是最容易誤觸的一個動作,而維護者
 * 此刻看著一個沒有反應的畫面,不會知道下一步該做什麼。
 */
const DENIED_NOTE = '未取得題目檔的寫入授權,沒有選定檔案。已填入的內容都還在,可以再按一次「選擇檔案」。';

/**
 * 平台不支援時,寫入操作旁那一行(requirement 6.3)。
 *
 * 與 `#unsupported` 那句常駐說明講的是同一件事。**兩處都要有**:常駐的那一句在頁面
 * 上方,可能已經被捲出視野,而按下寫入之後維護者的眼睛在按鈕附近。
 */
const UNSUPPORTED_NOTE = '這個瀏覽器不支援由網頁寫入本機檔案。請改用桌面版的 Chrome、Edge 或 Opera。';

/**
 * 判定為不合格、卻一項可顯示的未通過項目都沒有時的退路。
 *
 * 契約上不會發生(`valid` 由 `issues` 導出),但沉默的後果太不對稱:畫面上什麼都不
 * 說,看起來就與通過一模一樣,而這一次其實是**不通過**。寧可講一句沒有定位的話。
 */
const UNSPECIFIED_VERDICT_MESSAGE = '這一題未通過權威驗證,但服務端沒有指出是哪一項。';

/**
 * 撞號的說法(requirements 4.3、4.4)。**題號本身一定要出現在句子裡** —— 兩條
 * acceptance criteria 要的都是「指出重複的題號」,一句「這個題號已被使用」對著一個
 * 可能剛被改過的輸入框說話,維護者無從確認講的是不是他眼前那一個。
 *
 * **既有題目與本分頁已寫入者共用同一句話**,不分成兩種說法:兩者對維護者而言的
 * 下一步完全相同(換一個題號),而分開只會逼他去理解「索引」與「本次寫入」的差別
 * ——那是本工具的內部機制,不是他要處理的事。
 *
 * @param {number} id 重複的題號。
 * @returns {string}
 */
const collisionText = (id) => `題號 ${id} 已被使用,請改用其他題號`;

/**
 * 取表單控制項。
 *
 * 一律以 `data-field` 查詢而不是 id(tasks 4.1 的 DOM 契約):`data-field` 的取值
 * 就是 `check.js` 的 `CheckIssue.field`,同名才讓「哪一項未通過」對得起來;id 的
 * 命名慣例是給 `<label for>` 用的,改了不該波及這一側。
 *
 * @param {string} name 欄位名,例如 `'fen'`。
 * @returns {HTMLElement}
 */
function field(name) {
  return document.querySelector(`[data-field="${name}"]`);
}

/**
 * 取某一欄的訊息槽(requirement 8.4)。
 *
 * **槽位宣告在 `index.html`,本檔只查得到它、不生它。** 這是那一頁的既有規矩(見
 * `#unsupported` 的說明):位置固定、預設隱藏,顯示與否只是 `render()` 依當下的值
 * 決定的一個 `hidden`。要顯示才插一個節點進去的話,插入與移除都會推動底下的欄位,
 * 使用者每打一個字版面就抖一次。
 *
 * 認槽位用 `data-message-for` 而不是 id,取值即 `check.js` 的 `CheckIssue.field`,
 * 與控制項的 `data-field` 同名 —— 五個欄位因此共用這一個查詢,增減欄位時要動的是
 * `FIELD_NAMES` 與 HTML 裡的槽,本函式一個字都不必改。
 *
 * @param {string} name 欄位名,例如 `'fen'`。
 * @returns {HTMLElement}
 */
function fieldMessage(name) {
  return document.querySelector(`[data-message-for="${name}"]`);
}

const elements = {
  board: document.getElementById('board'),
  // 七個控制項與七個訊息槽,以欄位名索引。**不為每一欄各記一個常數**:欄位名同時是
  // `CheckIssue.field`,以它當鍵才讓「哪一項未通過」與「訊息寫到哪一格」自然對上,
  // 日後增減欄位也只動 `FIELD_NAMES` 一處。
  controls: new Map(FIELD_NAMES.map((name) => [name, field(name)])),
  messages: new Map(FIELD_NAMES.map((name) => [name, fieldMessage(name)])),
  // 寫入操作與它的停用說明(4.1、8.4)。click 跑的是寫入序列,本輪走到權威驗證
  // 為止(見檔首)。`writeNote` 同時承接上一次嘗試的頁面層級說法(4.9)。
  write: document.getElementById('write'),
  writeNote: document.getElementById('write-note'),
  // 平台不支援的常駐說明(6.3)。**不進 `render()`** —— 它顯示與否是
  // `isSupported()` 的函式,而那個答案在分頁存續期間不會變(見模組末尾的接線)。
  unsupported: document.getElementById('unsupported'),
  // 選檔操作與目前選定的檔名(5.1、5.11)。**兩者都宣告在 HTML 裡**,本檔只寫內容
  // 與接事件 —— 與訊息槽同一條規矩。
  pickFile: document.getElementById('pick-file'),
  selectedFile: document.getElementById('selected-file'),
};

/**
 * 本分頁**已成功寫入**的題號(requirement 4.4;design 的 State Management)。
 *
 * 它補的是題庫索引的一段空窗:索引是服務啟動時的快照,剛寫進去的一題要等服務因
 * 檔案變動而重啟才會出現在裡面。那段期間 `GET /api/catalog` 查無此人,只有這個集合
 * 擋得住第二次用到同一個題號。
 *
 * **只在寫入成功後才加入**(見 `recordWrittenId`),失敗的嘗試不佔用題號 —— 否則
 * 一次驗證未過就會讓一個誰也沒在用的題號自此不能再用,而維護者看不出原因。
 *
 * 不持久化(design 的 State Management):重整分頁即清空,而那時索引多半已經跟上。
 */
const writtenIds = new Set();

/**
 * 上一次寫入嘗試指認為撞號的題號;沒有指認時為 `null`。
 *
 * 記的是**那一個題號**而不是「現在有沒有撞號」:撞號無法由當下的輸入值算出來
 * (它要問過索引才知道),但「這句指認還成不成立」可以 —— 題號欄裡的字換掉了,
 * 那句話講的就是別人了。`collisionIssues()` 因此以「當下的題號是否仍是它」當
 * 顯示的條件,畫面便不會掛著一句已經與輸入框對不上的紅字。
 */
let collidedId = null;

/**
 * 上一次送出權威驗證的那一份候選題目與它得到的未通過項目;沒有或已不適用時為 `null`
 * (requirements 4.7、4.8)。
 *
 * `candidate` 是**送出去的那一份候選題目的序列化文字**,`issues` 是那一次的未通過
 * 項目(空陣列不會被記下來 —— 通過在本輪不生任何呈現)。
 *
 * 記下候選題目本身的理由與 `collidedId` 記下題號完全相同:判定算不出來,但「這份
 * 判定還講不講得通」算得出來 —— 當下組得出的候選題目與它逐字相同時才成立。比的是
 * **候選題目**而不是七個輸入框的字面:把標籤的分隔符由頓號改成空白不會改變送出去的
 * 那份陣列,一句成立的判定不該因此消失。
 *
 * 選定的檔案不在候選題目裡,所以換檔不會撤下判定 —— 那是對的:判定講的是這一題
 * 的內容,與它要寫到哪個檔無關。
 */
let candidateVerdict = null;

/**
 * 上一次寫入嘗試的頁面層級說法(requirement 4.9 的「確認未能完成」);沒有話說時
 * 為空字串。
 *
 * 與 `candidateVerdict` 分成兩個變數而不是一個帶標籤的物件,因為兩者連**成立的條件**
 * 都不同:判定是對某一份候選題目說的,內容一改就撤下;而「這次確認沒跑完」講的是
 * 那一次嘗試,不是任何一個欄位的內容 —— 維護者改了 FEN 也不會讓它變成假的。它因此
 * 一路留到**下一次嘗試**才被換掉。
 */
let attemptNote = '';

/**
 * 上一次**寫入成功**那句話(requirement 7.1);沒有時為空字串。
 *
 * 與 `attemptNote` 分開,因為兩者的存續期間不同。失敗那一句一路留到下一次嘗試 ——
 * 表單原封不動,那句話講的仍是此刻畫面上這一份。成功這一句不行:成功會把題目欄位
 * 清空(7.2),而**維護者一開始收下一題,它講的就是別人了**。因此任何一次欄位輸入
 * 都把它撤下 —— 與撞號指認、權威驗證判定被撤下的是同一條規則。
 *
 * 清空欄位是本檔自己做的,而以程式指派 `value` 不會發出 `input` 事件,所以那一次
 * 清空撤不掉它自己 —— 兩件事在同一瞬間發生而不互相抵消,靠的正是這個性質。
 */
let successNote = '';

/**
 * 是否有一次寫入嘗試正在進行(design 的 State Management)。
 *
 * 進行中時寫入操作停用,避免重複送出。代價不只是多借一次引擎:兩條序列會各自讀一次
 * 目標檔、各自追加一題、再各自整檔寫回,而**後寫的那一次讀到的是前一次寫回之前的
 * 內容** —— 先寫進去的那一題就這樣被蓋掉了。
 *
 * 只在 `renderWriteAction()` 影響停用,不影響任何一句話:「正在寫入」不是一個問題,
 * 不需要說法。
 */
let writing = false;

/**
 * 某一欄的當下輸入值。
 *
 * @param {string} name 欄位名。
 * @returns {string}
 */
function valueOf(name) {
  return elements.controls.get(name).value;
}

/**
 * 五個欄位的當下值,形狀即 `check.js` 的 `FormValues`。
 *
 * @returns {Record<string, string>}
 */
function readValues() {
  return Object.fromEntries(FIELD_NAMES.map((name) => [name, valueOf(name)]));
}

/**
 * 某一欄在畫面上的名稱,取自它的 `<label>`。
 *
 * **不另立一份欄位名稱表**:那會是第二個真相來源,改了 `<label>` 而忘了這裡時,
 * 停用說明點名的欄位就與畫面上的標題對不起來,而使用者要照著它去找那一格。
 *
 * @param {string|null} name 欄位名;不屬於任一欄位時為 `null`。
 * @returns {string} 找不到對應欄位時為空字串。
 */
function labelOf(name) {
  const control = name === null ? undefined : elements.controls.get(name);
  return control?.labels?.[0]?.textContent.trim() ?? '';
}

/**
 * @typedef {{board: (string|null)[][]|null, message: string}} FenView
 */

/**
 * 由 FEN 輸入的當下內容推導出畫面該呈現的一切。**純推導,不碰 DOM。**
 *
 * 三條路,分別對應三個 acceptance criteria:
 *
 * - **空的輸入** -> 空盤面、沒有訊息(2.5)。這一條必須排在結構檢查之前:
 *   `checkFenStructure('')` 回的是「請填入 FEN」,那屬寫入前的必填檢查(4.1),
 *   不是「無法解析」(2.4)。順序顛倒的話,還沒開始填的欄位就會掛著錯誤訊息。
 * - **展不開成 10x9 盤面** -> 不給盤面、給訊息(2.4)。此時**不留**前一個可解析
 *   內容的局面 —— 呼叫端拿到的 `board` 是 `null`,畫不出東西是必然而不是選擇。
 * - **其餘** -> 照著畫(2.1)。
 *
 * **這裡不再推導起手方。** 那一行呈現已經移除(見檔首附近的說明),`check.js` 的
 * `sideFromFen()` 也隨之刪掉了 —— 沒有呼叫端的推導留著只會讓人以為畫面上還有一處
 * 在印它。走子方那一欄仍由 `checkFenStructure()` 驗(認不得就是一項未通過)。
 *
 * **前後的空白在最上面就去掉一次,這一行是必要的。** `checkFenStructure()` 內部
 * 先 `trim()`,而 `parseFen()` 是以**字面的半形空格**切欄位的
 * (`fen.js` 的 `fen.split(' ')[0]`)—— 把沒有去過空白的字串同時交給兩邊,一個前導
 * 空白就會讓結構檢查照樣通過、盤面段卻取到空字串,於是畫出一個全空的盤面:訊息沒
 * 出現、棋子也沒出現,與「還沒開始填」完全分不出來,而使用者手上那串 FEN 是對的。
 * 貼上時前後多一個空白或 tab 是常態(自檔案或網頁複製),因此這不是防禦性的細節,
 * 而是本頁最容易踩到的一種沉默失敗。**兩邊必須看到同一個字串。**
 *
 * 去空白只發生在這裡,不回頭改 `fen.js` —— 它的寬鬆解析是對局路徑的既有契約
 * (`check.js` 檔首已載明不得為本 spec 收緊)。
 *
 * @param {unknown} raw FEN 欄位的原始值。
 * @returns {FenView}
 */
function readFenView(raw) {
  const text = (typeof raw === 'string' ? raw : '').trim();

  if (text === '') {
    return { board: parseFen(EMPTY_FEN), message: '' };
  }

  const issue = checkFenStructure(text);
  if (issue !== null) {
    return { board: null, message: issue.message };
  }

  return { board: parseFen(text), message: '' };
}

/**
 * 盤面(2.1、2.2、2.3、2.4、2.5)。
 *
 * 紅方底線在下由 `board.js` 的座標公式決定(它的 `py()`),本檔不重算也不翻轉 ——
 * 盤面外觀與對局頁一致(requirement 8.2)的前提就是**用的是同一份繪製**。
 *
 * 畫不出盤面時放一句話而不是留一片空白:`#board` 空著看起來就是一面壞掉的棋盤。
 *
 * **這一段是本檔唯一自己生節點的地方**,與訊息槽的做法不同,理由是 `#board` 的內容
 * 由 `renderBoard()` 以 `replaceChildren` 整份換掉:`index.html` 裡那個 `.board-placeholder`
 * 一經繪盤就沒了,回不去,只能重建一個。其餘要顯示的東西一律在 HTML 裡宣告槽位。
 *
 * @param {FenView} view
 */
function renderBoardArea(view) {
  if (view.board === null) {
    const note = document.createElement('p');
    note.className = 'board-placeholder';
    note.textContent = CLEARED_BOARD_NOTE;
    elements.board.replaceChildren(note);
    return;
  }
  renderBoard(elements.board, {
    board: view.board,
    // 空的著法集合 = 整片盤面不可選取、沒有任何落點標示(2.3)。兩個回呼因此永遠
    // 不會被呼叫,不必給。
    legalMoves: NO_LEGAL_MOVES,
  });
}

/**
 * 難度的三個選項(requirements 3.2、8.3)。
 *
 * **說法的唯一出處是 `difficulty.js`**,列表頁與對局頁讀的是同一份 —— 三選一的
 * 說法因此必然與列表頁一致,而不是「目前剛好一樣」。在這裡或 HTML 裡寫死一份就是
 * 第二個真相來源:改了模組而忘了這一頁時,兩邊會靜默分家,沒有任何測試會自然抓到。
 * (`tests/test_web_editor_layout.py` 與 `tests/test_web_editor_fields.py` 直接掃收題頁
 * 三個檔案的文字反向釘住這件事。)
 *
 * 值用題目 schema 的數字(1/2/3)轉成字串:`<option>` 的值只能是字串,而寫進題目檔
 * 時要的是數字 —— 那一步屬第 5 組的序列化,不在這裡先轉。
 *
 * ## 這三個就是全部的選項,而且預設選中中間那一級
 *
 * 這個 `<select>` 在 HTML 裡是空的,三個選項全由這裡產生 —— **不再有「尚未選擇」
 * 那一個空選項**。它原本排在最前面,由 `checkForm()` 以空字串判定「還沒選」;難度
 * 改為有預設值之後,它只剩一個用途:把一格已經填好的東西清成未填,而清成未填之後
 * 這一題就寫不出去。收題頁的難度因此**恆有值**。
 *
 * 預設值原本是「尚未選擇」,理由是「不做選擇就不該寫進一個難度」—— 而實際收題時
 * 每一題都得手動點一次同一個選項,那個保護擋到的只有維護者自己。中間那一級是
 * **不特別判斷時最可能對的答案**,判斷得出來的那幾題照樣改得動;這與標籤的預設值
 * (`index.html` 的 `#field-tags`)是同一個取捨。
 *
 * `checkForm()` 那條空值檢查仍留著,不因此刪:規則是「難度必須是三個分級之一」,
 * 而不是「畫面上剛好選不到空的」。
 *
 * 值取自 `DIFFICULTY_LABELS` 的鍵而不是寫一個 `2` 在這裡:那份對照表是難度分級的
 * 唯一出處,分級若有增減,這裡跟著動而不是留下一個指向不存在選項的數字。
 *
 * 這是本檔第二個、也是最後一個自己生節點的地方(另一個是 `renderBoardArea`)。理由
 * 與那裡不同:選項的**內容**不能寫在 HTML 裡,否則說法就有了第二份。它只在載入時
 * 產生一次,不隨輸入變動增減,因此沒有推動版面的問題。
 */
function renderDifficultyOptions() {
  const difficulty = elements.controls.get('difficulty');
  for (const [value, label] of DIFFICULTY_LABELS) {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = label;
    difficulty.append(option);
  }
  difficulty.value = String(DEFAULT_DIFFICULTY);
}

/**
 * 難度控制項當下該用哪一個分級的顏色(requirement 8.3 的「與列表一致」)。
 *
 * **顏色本身一個字都不在這裡** —— 三個色值住在 `editor.css`,本函式只寫上
 * `data-level`,與列表頁 `list.js` 的做法逐字相同(它也只寫 `dataset.level`,
 * 顏色在 `list.css`)。呈現層的顏色屬樣式表,寫在 JS 裡就會有第二個要改的地方。
 *
 * 值直接取自控制項當下的值,而不是另記一份狀態:畫面完全是當下那些值的函式
 * (見 `render()`)。**沒有值時把屬性整個移除**,不是寫一個空字串 ——
 * `[data-level=""]` 仍然是一個存在的屬性,日後多一條屬性選擇器就會誤中它。畫面上
 * 現在選不出空的難度(三個選項就是全部),這一條因此是防禦性的:值的來源只有
 * 控制項自己,而它在選項產生之前是空的。
 */
function renderDifficultyLevel() {
  const difficulty = elements.controls.get('difficulty');
  const value = difficulty.value;
  if (value === '') {
    delete difficulty.dataset.level;
    return;
  }
  difficulty.dataset.level = value;
}

/**
 * 題號那一欄當下該說的撞號指認(requirements 4.3、4.4)。
 *
 * 指認**只在題號欄裡仍是同一個號碼時成立**:維護者改成別的題號之後,那句話講的
 * 就是別人了,留著會讓一個沒撞號的題號看起來撞了號 —— 而畫面上再也沒有東西告訴他
 * 那句話已經過期。改回同一個號碼時它會再出現,那是對的:上一次問到的結果就是它。
 *
 * 比的是**數值**而不是字面:`'026'` 與 `26` 是同一個題號,寫法不同不該讓一句成立的
 * 指認消失。空的、或不是數字的題號一律不會與任何指認相等 —— `Number('')` 是 `0`,
 * 而題號恆為正整數。
 *
 * 回的是一份 `CheckIssue` 清單而不是一句話,與 `verdictIssues()` 同形:兩者都是
 * 「自淺層清單之外傳進來、但定位得到欄位」的項目,呈現層一視同仁(見 `renderMessages`)。
 *
 * @returns {import('./check.js').CheckIssue[]} 沒有指認、或指認已不適用時為空陣列。
 */
function collisionIssues() {
  if (collidedId === null || Number(valueOf('id').trim()) !== collidedId) {
    return [];
  }
  return [{ field: 'id', message: collisionText(collidedId) }];
}

/**
 * 權威驗證對**當下這一份候選題目**說的未通過項目(requirement 4.8)。
 *
 * 判定只在候選題目與送出去的那一份逐字相同時才成立(見 `candidateVerdict`)。內容
 * 一改就回空陣列 —— 留著的話,維護者改完 FEN 之後仍看得到紅字,而畫面上再也沒有
 * 東西告訴他那句話講的是上一份內容。
 *
 * @returns {import('./check.js').CheckIssue[]}
 */
function verdictIssues() {
  if (
    candidateVerdict === null ||
    candidateVerdict.candidate !== candidateKey(buildCandidate())
  ) {
    return [];
  }
  return candidateVerdict.issues;
}

/**
 * 寫入操作旁那一行當下該說的話,**淺層檢查以外的部分**(requirements 4.8、4.9)。
 *
 * 兩個來源,而且不可能同時有話說 —— 一次嘗試要麼拿到判定、要麼確認未能完成:
 *
 * - **確認未能完成**(4.9):不指向任何欄位,因為它講的不是欄位的內容。
 * - **歸不到欄位的未通過項目**:`field` 為空的那些(候選題目多了一個 schema 以外的
 *   欄位,或後端的欄位歸屬退化成空值)。它們在七個訊息槽裡沒有位置,若不落在這裡就
 *   會靜靜消失 —— 而那一項未通過仍然擋著寫入,畫面上卻找不到任何原因。
 *
 * @returns {string} 沒有話說時為空字串。
 */
function attemptMessage() {
  if (attemptNote !== '') {
    return attemptNote;
  }
  return verdictIssues()
    .filter((issue) => issue.field === null)
    .map((issue) => issue.message)
    .join(NAME_SEPARATOR);
}

/**
 * 把未通過的項目寫到各欄位旁(requirements 4.1、4.2、4.3、4.4、4.6、8.4)。
 *
 * 一欄至多一句:`checkForm()` 對同一欄位就只回一項,這裡取先到的那一項,順序即
 * 表單欄位順序。通過的欄位一律清成空字串並收起來 —— 未通過不是會黏住的狀態。
 *
 * ## 空著的欄位一句話都不說
 *
 * 一項未通過只在**維護者已經寫了東西、而那個東西不對**時掛到欄位旁;欄位是空的就
 * 安靜。「題號必須是正整數」是在指出他打錯了,「請填入題號」對著一個他還沒走到的
 * 空格,講的只是這張表單還沒填完 —— 一開頁就在七個格子旁掛七句紅字,等於在他還沒
 * 開始之前先說了七次錯,而之後真正的錯字反而混在裡面看不出來。
 *
 * 判空與 `check.js` 同一個尺度(去空白後為空):只打了幾個空白的欄位在畫面上與空的
 * 沒有兩樣,對它掛一句「請填入」同樣是對著一個看起來還沒填的格子說話。
 *
 * **未通過的項目沒有因此少一項**:寫入照樣停用,而「是哪一項」由
 * `renderWriteAction()` 無條件點名(8.4)。畫面上少掉的只是重複的那一份。
 *
 * 這條規則**不需要記住哪些欄位被碰過**:判準是當下的值,畫面因此仍然只是那七個字串
 * 的函式 —— 清空一個填錯的欄位,那一句話就跟著收起來,不會留著上一次的紅字。
 *
 * ## FEN 那一欄的訊息取自繪盤用的同一份推導
 *
 * 其餘六欄的訊息來自 `checkForm()`,FEN 這一欄來自 `readFenView()`。**兩者對同一串
 * 字給出的說法完全相同**(都是 `checkFenStructure()` 對去空白後的字串的結果),因此
 * 這不是一條例外規則,而是讓那一欄的訊息與盤面出自同一次推導 —— requirement 2.4 要求
 * 「顯示無法解析的訊息**且**不繼續顯示前一個局面」,兩件事同源才不可能各說各話。
 *
 * 空的 FEN 欄位在兩條路上也一致地安靜:`readFenView()` 對空輸入回空訊息(2.5 的
 * 空盤面),而上面那條規則同樣會略過它。
 *
 * `aria-invalid` 與看得見的那一句由同一次判斷寫入:訊息槽是給眼睛的,這一個是給
 * 螢幕閱讀器的,兩者說的必須是同一件事 —— 包括對空欄位一起沉默。
 *
 * ## 撞號的指認與服務端的判定也落在這裡,而且與淺層的那些長得一樣
 *
 * 兩者都是自淺層清單之外傳進來的(`extra`),理由與 FEN 那一欄不同:FEN 是為了與
 * 盤面同源,這兩者則是因為它們**算不出來** —— 撞號要問過題庫索引(4.3、4.4)、
 * 不合格要問過權威驗證(4.8)。三者都寫進同一組訊息槽,使用者不必分辨一句話是哪一層
 * 檢查產生的;design 的 Error Categories 明載服務端的 `issues` 與 `CheckIssue` 同形,
 * **呈現層不必分辨來源**。
 *
 * `extra` 的項目**覆寫**同一欄的淺層說法,而這不會蓋掉任何東西:兩者不可能同時
 * 有話說。撞號的指認以題號的數值比對成立(見 `collisionIssues`),題號空著或不是
 * 正整數時比不出相等;判定則只在候選題目與送出去的那一份相同時成立,而那一份能送
 * 出去就代表七項淺層檢查全過。**空欄位不說話**那條規則同樣不套用在 `extra` 上,
 * 理由相同 —— 走到這裡的欄位都已經填了東西。
 *
 * 一欄仍是至多一句:`extra` 內先到的那一項優先,與淺層清單的規則一致。
 *
 * `field` 為空的項目在這裡**沒有位置,也不該有** —— 那些由 `attemptMessage()` 承接,
 * 寫在寫入操作旁邊。歸不到欄位的說法硬塞進某一格,等於指著一個沒問題的欄位。
 *
 * @param {import('./check.js').CheckIssue[]} issues 淺層檢查的清單。
 * @param {string} fenMessage FEN 欄位的當下訊息(2.4、2.5)。
 * @param {import('./check.js').CheckIssue[]} extra 撞號指認與權威驗證的未通過項目
 *   (4.3、4.4、4.8)。
 */
function renderMessages(issues, fenMessage, extra) {
  const texts = new Map(FIELD_NAMES.map((name) => [name, '']));
  for (const issue of issues) {
    if (issue.field === null || valueOf(issue.field).trim() === '') {
      continue;
    }
    if (texts.get(issue.field) === '') {
      texts.set(issue.field, issue.message);
    }
  }
  texts.set('fen', fenMessage);

  const claimed = new Set();
  for (const issue of extra) {
    // 認不得的欄位名(例如 `max_dtm` —— 它是題目 schema 的欄位,卻沒有表單格子)
    // 在 `readVerdictIssues()` 就折成了空值,因此這裡跟著略過即可,那一項已由
    // `attemptMessage()` 收下。這一道判斷是第二層保險,不是唯一的一道。
    if (issue.field === null || !texts.has(issue.field) || claimed.has(issue.field)) {
      continue;
    }
    texts.set(issue.field, issue.message);
    claimed.add(issue.field);
  }

  for (const [name, text] of texts) {
    const slot = elements.messages.get(name);
    slot.textContent = text;
    slot.hidden = text === '';

    const control = elements.controls.get(name);
    if (text === '') {
      control.removeAttribute('aria-invalid');
    } else {
      control.setAttribute('aria-invalid', 'true');
    }
  }
}

/**
 * 寫入操作的停用與其理由(requirements 4.1、4.2、4.6、8.4)。
 *
 * 停用的判準是**整份清單是否為空**,不是某幾項:淺層檢查的每一項都是「這樣寫下去
 * 服務端會拒絕」,沒有哪一項可以放行。
 *
 * **只看淺層檢查,撞號不在內**(4.3、4.4):這一行說的是「這張表單還不能送」,而
 * 撞號是送出去之後問到的結果 —— 它每一次按下都重問一次,把按鈕停用只會讓維護者
 * 沒辦法對同一個題號再試一次(檔首已載明理由)。撞號的說法定位在題號那一欄。
 *
 * 光是停用不夠(8.4 明載「而不只是停用寫入操作」):被停用的按鈕說不出自己為什麼
 * 按不下去,而使用者第一個想知道的就是那個。點名用的是各欄 `<label>` 上的字,與畫面
 * 上的標題同一份,他照著抬頭找得到那一格。
 *
 * **這裡不套用「空著就不說」那條規則,而且不能套**:那條規則之所以成立,正是因為
 * 還沒填的欄位在這裡點得到名 —— 兩處一起沉默的話,一個空欄位就會停用寫入卻完全不
 * 出現在畫面上,8.4 隨即不成立。這一行因此涵蓋清單裡的每一項。
 *
 * ## 這一行也是上一次嘗試結果的所在(4.9、7.1、7.3)
 *
 * 成功、失敗與這裡的點名講的是同一個問題的三種答案 —— **剛才那一題到底寫出去了
 * 沒有,沒有的話是為什麼** —— 所以共用同一行,而不是在旁邊再長一塊訊息區:同一個
 * 問題有兩個地方可看,使用者就得每次都看兩個地方。
 *
 * 優先序是**成功 → 淺層點名 → 失敗**,兩處各有理由:
 *
 * - **成功排在淺層點名之前**,因為兩者在同一瞬間成立。寫入成功會把題目欄位清空
 *   (7.2),清空之後七項淺層檢查全部未通過,點名於是說「仍有項目未通過:題號、
 *   局名……」—— 那句話此刻是噪音(欄位空著是這個工具剛剛做的事,不是維護者的疏漏),
 *   而它會擠掉唯一有價值的那一句:剛才那一題寫進去了沒有。
 * - **淺層點名排在失敗之前**,理由相反:失敗那句話一路留到下一次嘗試,而表單在那
 *   之後可能被改到送不出去。那時候該講的是當下這一份為什麼按不下去,不是上一次嘗試
 *   遇到了什麼。這一條不會把成功擠掉 —— 成功已經排在更前面。
 *
 * ## 停用有兩個來源
 *
 * 淺層檢查未通過(4.1),以及**一次嘗試正在進行**(見 `writing`)。兩者都不影響這
 * 一行說什麼:進行中不是一個問題,不需要說法 —— 按鈕灰掉的那一兩秒,畫面上仍留著
 * 上一次的結果,而那正是維護者此刻該看的東西。
 *
 * @param {import('./check.js').CheckIssue[]} issues 淺層檢查的清單。
 * @param {string} success 上一次寫入成功那句話(7.1);沒有時為空字串。
 * @param {string} failure 上一次嘗試沒寫出去的原因(4.9、7.3);沒有時為空字串。
 */
function renderWriteAction(issues, success, failure) {
  elements.write.disabled = writing || issues.length > 0;

  const names = issues.map((issue) => labelOf(issue.field)).filter((name) => name !== '');
  let text = success;
  if (text === '') {
    if (issues.length > 0) {
      text = names.length > 0
        ? WRITE_NOTE_PREFIX + names.join(NAME_SEPARATOR)
        : WRITE_NOTE_FALLBACK;
    } else {
      text = failure;
    }
  }
  elements.writeNote.textContent = text;
  elements.writeNote.hidden = text === '';
}

/**
 * 目前選定的目標題目檔(requirements 5.1、5.10)。
 *
 * 真相來源是 `fs.js` 的 `selectedFile()`,本檔**不另存一份** —— 記兩份遲早分家,而
 * 分家時畫面說的檔名會與真正寫進去的那個檔不同,那是這個工具最不能犯的錯。
 *
 * `selectedFile()` 不開對話框也沒有副作用,所以每一次重畫都問得起。
 */
function renderSelectedFile() {
  const handle = selectedFile();
  elements.selectedFile.textContent = handle === null ? NO_FILE_CHOSEN : handle.name;
}

/**
 * 按下「選擇檔案」:開一次檔案選擇框(requirements 5.1、5.11、6.1、6.2)。
 *
 * **這是本檔唯一直接由使用者手勢驅動平台的地方**,而那正是它獨立成一顆按鈕的理由:
 * 檔案選擇框必須開在手勢的呼叫堆疊內,所以這裡**不得在 `pickCorpusFile()` 之前
 * `await` 任何東西**。舊設計把授權排在撞號與驗證之後 —— 那幾步是網路請求,而實測
 * 手勢的有效期只有約 5 秒。
 *
 * 每按一次都重新詢問,因此它同時就是 5.11 的「更換目標題目檔」。
 */
async function choose() {
  attemptNote = '';
  successNote = '';
  try {
    await pickCorpusFile();
  } catch (error) {
    if (error instanceof UnsupportedBrowserError) {
      attemptNote = UNSUPPORTED_NOTE;
    } else if (error instanceof PermissionDeniedError) {
      attemptNote = DENIED_NOTE;
    } else {
      throw error;
    }
  }
  render();
}

/**
 * 把當下的輸入整份畫出來。畫面的每一次變動都只經過這裡。
 *
 * **不再有 `origin` 參數。** 它唯一的用途是讓描述建議值不與正在打字的使用者搶輸入
 * 框,而描述欄已整個移除(3.9)—— 畫面現在**完全**是當下那些值的函式,連「是誰觸發
 * 的」都不必知道。
 */
function render() {
  const view = readFenView(valueOf('fen'));
  renderBoardArea(view);
  renderDifficultyLevel();
  renderSelectedFile();

  const issues = checkForm(readValues());
  renderMessages(issues, view.message, [...collisionIssues(), ...verdictIssues()]);
  renderWriteAction(issues, successNote, attemptMessage());
}

/**
 * 記下一個**已成功寫入**的題號(requirement 4.4;design 的 State Management)。
 *
 * 這是寫入序列最後一步的一半(另一半是清空欄位,屬 7.2 / tasks 5.4)。**本檔目前
 * 沒有任何一處呼叫它** —— 「寫入成功」這件事要到 tasks 5.3 把檔案寫回落盤之後才
 * 存在。規則(只在成功後加入)屬 5.1、時機屬 5.3,兩者刻意分開:把加入的動作提前到
 * 「按下寫入」或「撞號通過」,失敗的嘗試就會佔走一個誰也沒在用的題號,而維護者
 * 看不出原因。
 *
 * export 出來是因為它是 5.3 的接口,不是內部細節:序列的下一段要在寫回成功之後
 * 呼叫它,而那一段程式碼與這裡在同一個模組裡 —— 屆時 export 仍然是它被測試佈置
 * 前置狀態的方式(`tests/test_web_editor_write.py`)。
 *
 * 值一律折成數字:索引回來的題號是 JSON 的數字,而表單那一側是字串,集合裡混進
 * `'26'` 會讓 `has(26)` 找不到它 —— 撞號因此靜默失效,那正是本集合唯一要做的事。
 *
 * @param {number|string} id 剛寫入成功的那一題的題號。
 */
export function recordWrittenId(id) {
  writtenIds.add(Number(id));
}

/**
 * 由當下的欄位組出候選題目 —— **送去驗證的與之後要寫出去的是同一份**
 * (design 的「候選題目的資料契約」)。
 *
 * 三件事在這裡發生,而且只在這裡發生:
 *
 * - **欄位挑選**:正好五個由人工編輯的 schema 欄位(見 `CANDIDATE_FIELDS`)。
 * - **型別轉換**:題號與難度是**數字**。輸入框給的是字串,原樣送出會被題目 schema
 *   判為型別不符 —— 一份完全沒問題的題目因此被擋下,而維護者看不出哪裡錯。標籤是
 *   切分後的陣列,切法沿用 `check.js` 的 `parseTags`(那一層已經以它判定「至少一個」,
 *   在這裡另寫一種切法會讓檢查與送出的內容分家)。
 * - **去空白**:貼上時前後多一個空白是常態。正規化只有這一處,而且發生在**送驗證
 *   之前** —— 服務端驗過的內容必須與寫進題目檔的那一份逐字相同(`service/models.py`
 *   明載它不做任何正規化),兩邊各去一次空白就是兩份可能不同的內容。
 *
 * 題號與難度不必再驗一次寫法:`#write` 只有在 `checkForm()` 沒話說時才按得下去(4.1)。
 *
 * @returns {{id: number, title: string, description: string, fen: string,
 *            difficulty: number, tags: string[]}}
 */
function buildCandidate() {
  const values = readValues();
  return {
    id: Number(values.id.trim()),
    title: values.title.trim(),
    fen: values.fen.trim(),
    difficulty: Number(values.difficulty),
    tags: parseTags(values.tags),
  };
}

/**
 * 一份候選題目的識別字串,用來判斷上一次的判定還講不講得通(見 `candidateVerdict`)。
 *
 * 依 `CANDIDATE_FIELDS` 的順序取值再序列化,**不直接 `JSON.stringify` 整個物件**:
 * 那樣比的會連鍵的順序一起比進去,而順序是建構方式的細節,不是候選題目的內容。
 *
 * @param {object} candidate `buildCandidate()` 的產物。
 * @returns {string}
 */
function candidateKey(candidate) {
  return JSON.stringify(CANDIDATE_FIELDS.map((name) => candidate[name]));
}

/** 是否為可當作回應主體的 JSON 物件(排除 null 與陣列)。 */
const isObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * 這份主體是不是一個**判定**(design 的 API Contract)。
 *
 * 判定只有一個形狀。認不得就是沒有判定 —— 此時絕不能去讀 `valid`:一份錯誤回應
 * (`{code, message}`)、一頁 HTML、一份空內容都不帶 `valid`,而 `undefined` 是
 * falsy,拿它當「不合格」會誣賴一份可能完全沒問題的題目;更糟的是任何帶著別的
 * `valid` 欄位的回應都會被當成放行(4.9)。
 *
 * @param {unknown} payload
 * @returns {boolean}
 */
function isVerdict(payload) {
  return (
    isObject(payload) &&
    typeof payload.valid === 'boolean' &&
    Array.isArray(payload.issues)
  );
}

/**
 * 由錯誤回應的主體判出類別碼,做法與 `api.js` 的 `classify()` 相同。
 *
 * 分類**只看 `code`,不看 HTTP 狀態**:狀態碼是多對一的,而路由層的 404 與端點的
 * 404 更是完全不同的兩件事。契約外的一切都是 `UNKNOWN`。
 *
 * @param {unknown} payload 已解析的回應主體;不是 JSON 時為 `undefined`。
 * @returns {string} `ApiErrorCode` 之一。
 */
function classifyFailure(payload) {
  if (isObject(payload) && BACKEND_CODES.has(payload.code)) {
    return payload.code;
  }
  return ApiErrorCode.UNKNOWN;
}

/**
 * 把回應裡的未通過項目正規化成 `CheckIssue` 的形狀。
 *
 * 兩件事:**欄位名折成本頁認得的那七個**(其餘一律成為空值,由 `attemptMessage()`
 * 承接),以及**丟掉沒有說法的項目** —— 一句空話掛在欄位旁只會讓維護者盯著一個
 * 看不出所以然的紅框。
 *
 * 訊息**原樣顯示**,這與服務失敗那一類刻意相反:判定的說法出自題目 schema,是寫給
 * 維護者看的(`main.py` 的 `ValidationIssueEntry` 明載可直接顯示);而失敗回應裡的
 * `message` 是後端內部原文,一個字都不外流。
 *
 * @param {object} payload 已確認是判定的回應主體。
 * @returns {import('./check.js').CheckIssue[]}
 */
function readVerdictIssues(payload) {
  return payload.issues
    .filter((issue) => isObject(issue) && typeof issue.message === 'string')
    .filter((issue) => issue.message.trim() !== '')
    .map((issue) => ({
      field: elements.controls.has(issue.field) ? issue.field : null,
      message: issue.message,
    }));
}

/**
 * 送一次權威驗證:`POST /api/editor/validate`(requirement 4.7)。
 *
 * 形狀取自 `catalog.js` 的 `fetchIndex()`,理由也相同 —— `api.js` 的 `request()`
 * 未 export(見檔首)。**逾時上界取 `api.js` 的那一個**而不是 `catalog.js` 的:
 * 這個端點會借引擎,後端自己的總時間預算就是那個常數在遷就的東西;索引端點不借
 * 引擎,兩者的上界只是碰巧同值。
 *
 * @param {object} candidate 候選題目。
 * @returns {Promise<object>} 判定的回應主體 `{valid, issues}`。
 * @throws {ApiError} 服務失敗、逾時、連線失敗,或回應形狀認不得時。呈現層只拿得到
 *   一個碼 —— 後端與瀏覽器的原文到此為止。
 */
async function requestValidation(candidate) {
  const controller = new AbortController();
  // fetch 被 abort 時只會拋出一個 AbortError,分不出是誰下的令;自己記一筆,
  // 才能把「逾時」與「連線失敗」分成兩種失敗。
  let timedOut = false;
  const deadline = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(VALIDATE_PATH, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position: candidate }),
    });

    // 先取文字再自己 parse:`response.json()` 對空內容或 HTML 錯誤頁會拋錯,
    // 而那與連線斷掉是完全不同的情況,不能混進同一個 catch。
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined; // 不是 JSON —— 形狀無法辨識
    }

    if (!response.ok) {
      throw new ApiError(classifyFailure(payload));
    }
    if (!isVerdict(payload)) {
      throw new ApiError(ApiErrorCode.UNKNOWN);
    }
    return payload;
  } catch (error) {
    // 上面自己丟的分類結果原樣往上;其餘一律是連線層面的失敗 —— 瀏覽器的
    // 原始訊息到此為止,不往外帶。
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError(timedOut ? ApiErrorCode.TIMEOUT : ApiErrorCode.NETWORK);
  } finally {
    // 逾時要涵蓋讀取主體,所以計時器直到這裡(成功或失敗)才解除。
    clearTimeout(deadline);
  }
}

/**
 * 驗證沒有得到判定時,把那個類別碼翻成畫面上的說法(requirements 4.8、4.9)。
 *
 * 十種類別碼分成兩條路,而分界不是「哪一種錯」而是**確認到底有沒有跑**:
 *
 * - `INVALID_MOVE_FORMAT`:字元把關攔在路由函式之前,確認確實沒跑,但原因在這串
 *   FEN 身上,而且再按幾次都一樣。歸到 FEN 那一欄(見 `UNSENDABLE_FEN_MESSAGE`)。
 * - **其餘全部**:服務不可用、逾時、連線失敗、認不得的形狀 —— 一律「確認未能完成」
 *   (4.9)。不依碼分成好幾種說法,維護者的下一步都是同一個。
 *
 * 兩條路都**不執行寫入**,而且都不得被讀成通過。
 *
 * @param {string} code `ApiErrorCode` 之一。
 * @param {string} key 這一次送出的候選題目識別字串。
 */
function applyFailure(code, key) {
  if (code === ApiErrorCode.INVALID_MOVE_FORMAT) {
    candidateVerdict = {
      candidate: key,
      issues: [{ field: 'fen', message: UNSENDABLE_FEN_MESSAGE }],
    };
    return;
  }
  attemptNote = UNFINISHED_NOTE;
}

/**
 * 序列的後半:重讀目標檔 → 文字層追加 → 寫回 → 記下題號
 * (requirements 6.1、6.2、6.3、5.4–5.9、7.4、7.5)。
 *
 * ## 選檔不在這裡,而那正是重點
 *
 * 選定目標題目檔是使用者主動按下的一個**獨立操作**(5.1、5.11),由 `choose()` 承接。
 * 本函式只問 `selectedFile()`,尚未選定就在動磁碟之前停下(5.10)。
 *
 * 這條分工解掉了一個舊設計裡的硬傷:平台要求檔案選擇框開在使用者手勢的呼叫堆疊內,
 * 而舊設計把授權排在撞號與驗證之後 —— 那幾步是 `await` 過的網路請求,實測手勢的有效
 * 期只有約 5 秒,一次引擎池滿就會讓對話框開不起來。**現在那個情形構造上不會發生。**
 *
 * 反過來說,**尚未選檔不妨礙任何事**(6.4):繪盤與五個欄位都不經過本函式。
 *
 * ## 重讀與寫回之間沒有任何等待人的步驟
 *
 * 讀檔到寫檔之間目標檔若被編輯器或 git 改動,追加會蓋掉那次改動(design 的 Risks)。
 * 那個視窗壓不到零,但現在只剩兩次磁碟操作之間 —— 舊設計在這兩步中間還夾著一個目錄
 * 選擇框,而挑目錄花的是人的時間,是整條序列裡最長的一段。
 *
 * ## 兩種失敗,兩種歸屬
 *
 * - **尚未選定目標題目檔**(5.10):在這裡就翻成畫面上的說法,並指出下一步是按哪一
 *   顆按鈕。不支援與拒絕授權那兩種(6.2、6.3)不會走到這裡 —— 它們發生在選檔那一刻,
 *   由 `choose()` 承接。
 * - **其餘**(目標檔不是題目陣列、平台寫入失敗):原樣往上。它們的說法歸 7.3 的
 *   一般失敗處理,寫在 `attemptWrite()`;在這裡先寫一句,同一件事就會有兩種講法。
 *
 * 兩者的共同點是**都不記題號**:`recordWrittenId()` 只在寫回兌現之後才呼叫,失敗的
 * 嘗試不佔用題號(4.4)。佔走的話,維護者排除障礙後再送一次會被自己上一次的失敗
 * 擋下,而畫面說的是「這個題號重複」—— 一句與事實完全相反的話。
 *
 * @param {object} candidate `buildCandidate()` 的產物,即送去驗證的那一份。
 * @returns {Promise<void>}
 * @throws {CorpusFileError} 目標檔的既有內容不是題目陣列(5.6)。
 */
async function writeCandidate(candidate) {
  const handle = selectedFile();
  if (handle === null) {
    // 5.10:尚未選定目標題目檔。**在動磁碟之前就停下**,而不是等寫檔那一步才炸開。
    attemptNote = NO_FILE_NOTE;
    render();
    return;
  }

  // 讀檔到寫回之間的視窗越小越好(design 的 Risks):目標檔若在期間被編輯器或 git
  // 改動,追加會蓋掉那次改動。這兩行之間沒有任何等待人的步驟 —— 選檔早在寫入序列
  // 之前就完成了,那正是這次修訂把它移出序列的附帶好處。
  const existing = await readText(handle);
  const updated = appendPosition(existing, candidate);
  await writeText(handle, updated);

  // 落盤之後才記(4.4)。`writeText()` 兌現時內容已經在磁碟上(`fs.js` 的
  // Postconditions),所以這一行的前提是成立的。
  recordWrittenId(candidate.id);

  // 寫入成功(7.1、7.2)。清空與說話**必須是同一次重畫**:分兩次的話,中間那一幀
  // 是一份空表單配上「仍有項目未通過:題號、局名……」,而那句話是這個工具自己剛剛
  // 造成的。
  clearPuzzleFields();
  successNote = successText(candidate.id, handle.name);
  render();
}

/**
 * 清空題目欄位,**保留已選定的目標題目檔**(requirement 7.2)。
 *
 * 選定的檔案留著是因為連續收題抄的是同一本書的同一卷(requirement 7 的 Objective:
 * 「一次抄完一段書」)。而這件事在本函式裡**看不到**:那個控制代碼是 `fs.js` 的模組
 * 層狀態,本檔碰不到它 —— 五個題目欄位全部清空,選定的檔案自然就留著了。
 *
 * **以程式指派 `value` 不會發出 `input` 事件**,所以這一次清空不會觸發重畫、也撤
 * 不掉緊接著要設下的成功訊息。呼叫端負責在設好訊息之後重畫一次。
 */
function clearPuzzleFields() {
  for (const control of elements.controls.values()) {
    control.value = '';
  }
}

/**
 * 寫入序列擋得下來的那三步:取題庫索引、撞號檢查、送權威驗證
 * (requirements 4.3、4.4、4.5、4.7、4.8、4.9)。三步全過才交給 `writeCandidate()`。
 *
 * **每一次嘗試都重新取一次索引**,不是載入時取一次就沿用(research 的 Decision 5):
 * 服務會在題目檔變動時重啟,索引因此自己會跟上,重新取一次可讓已經進了索引的題號
 * 自然歸位,不必由本分頁的集合永遠背著。索引取不到時 `loadCatalog()` 拋出,序列就
 * 停在這一行 —— **絕不 catch 成一份空索引繼續走**(見 `attemptWrite`)。
 *
 * 題號直接取自輸入框:`#write` 只有在 `checkForm()` 沒話說時才按得下去(4.1),
 * 走到這裡就代表它已經是一個正整數的寫法,不必再驗一次。
 *
 * 兩邊皆無時把指認清成 `null`(4.5)—— 撞號檢查通過是一個**結果**,它會把上一次
 * 的指認撤下,而不是「什麼都不做」。
 *
 * ## 撞號擋下的嘗試不送驗證
 *
 * 次序是設計的一部分(design 的 System Flows):**撞號在驗證之前**。一個題號已經被
 * 用掉的候選題目寫不出去,借一次引擎去確認它的 FEN 只是從對局那邊拿走一格池容量 ——
 * 而引擎池是服務唯一的併發閘門。
 *
 * ## 上一次嘗試的結果在這裡就撤下
 *
 * 一次新的嘗試取代上一次的,所以序列一開始就把判定與說法清掉並重畫一次:畫面因此
 * 不可能同時掛著兩次嘗試的話,也不會在 `loadCatalog()` 拋出、序列停住的那條路上留著
 * 一句已經被撤下的舊話(狀態與畫面分家)。取不到索引時的說法在 `attemptWrite()`。
 *
 * **三步全過就交棒。** 驗證通過那一行呼叫 `writeCandidate()`,而本函式對它的結果
 * 不再多做任何事 —— 成功的呈現在 `writeCandidate()` 的最後一行,失敗的在 `attemptWrite()`。
 */
async function runWriteSequence() {
  candidateVerdict = null;
  attemptNote = '';
  successNote = '';
  render();

  const id = Number(valueOf('id').trim());
  const { positions } = await loadCatalog();
  const existingIds = new Set(positions.map((position) => Number(position.id)));

  collidedId = existingIds.has(id) || writtenIds.has(id) ? id : null;
  render();
  if (collidedId !== null) {
    return;
  }

  // 撞號通過(4.5)—— 送權威驗證(4.7)。
  const candidate = buildCandidate();
  const key = candidateKey(candidate);
  let payload;
  try {
    payload = await requestValidation(candidate);
  } catch (error) {
    // 預期內的停止只有 `ApiError` 這一種;其餘原樣往上,那些是缺陷而不是失敗
    // (理由見 `attemptWrite`)。
    if (!(error instanceof ApiError)) {
      throw error;
    }
    applyFailure(error.code, key);
    render();
    return;
  }

  const issues = readVerdictIssues(payload);
  // **兩者矛盾時取嚴的那一邊。** `valid` 由 `issues` 導出,契約上不可能不一致;
  // 真的不一致時放行的代價是壞資料進題庫,擋下的代價只是維護者再按一次。
  if (payload.valid && issues.length === 0) {
    // 驗證通過(4.7)—— 重讀目標檔、追加、寫回(5.5–5.9、5.10)。
    await writeCandidate(candidate);
    return;
  }

  // 未通過(4.8):不執行寫入,未通過項目定位到欄位。一項可顯示的都沒有時仍要出聲 ——
  // 沉默看起來與通過一模一樣,而這一次是不通過。
  candidateVerdict = {
    candidate: key,
    issues: issues.length > 0
      ? issues
      : [{ field: null, message: UNSPECIFIED_VERDICT_MESSAGE }],
  };
  render();
}

/**
 * 按下寫入時跑一次寫入序列,並承接**與這一次嘗試同生共死的兩件事**
 * (requirements 4.3–4.5、7.3)。
 *
 * ## 一、進行中停用寫入操作
 *
 * 重複送出的代價不只是多借一次引擎:兩條序列會各自讀一次目標檔、各自追加一題、再
 * 各自整檔寫回,而**後寫的那一次讀到的是前一次寫回之前的內容** —— 先寫進去的那一題
 * 就這樣被蓋掉了。停用與解除寫在 `try`/`finally` 裡,所以任何一條例外路徑 ——
 * 包括下面那個 `throw` —— 都不會讓按鈕永遠灰著。
 *
 * ## 二、把失敗翻成畫面上的一句話(7.3)
 *
 * 三類,而分界是「這是預期內的停止,還是一個缺陷」:
 *
 * - **取不到索引**:服務重啟中、連線斷掉或逾時的時候 `loadCatalog()` 拋出
 *   `CatalogError`(design 的 Risks 已把它歸在 7.3,**不另立分支**)。
 *   **不得把它折成「沒有撞號」**:那會讓最容易撞號的那一刻 —— 剛寫完一題、服務正在
 *   重啟、索引因此取不到 —— 變成最放行的一刻。序列因此只是停下,連上一次的撞號指認
 *   都不動:撤下指認是「撞號檢查通過」的後果,而這一次根本沒問到答案。
 * - **目標檔不是題目陣列**:`appendPosition()` 拋出 `CorpusFileError`(5.6)。追加是
 *   整檔覆寫,所以這一道擋的是「路徑打錯,而那個檔是別的東西」—— 本工具最危險的
 *   一步。擋下之後檔案一個位元組都沒被動過。
 * - **其餘**(平台寫入失敗、以及真正的缺陷):說一句話,**然後原樣往上拋**。7.3 沒有
 *   為「哪一種失敗」開例外,所以維護者一定要聽到一聲;但這一類與程式的缺陷分不開,
 *   吞掉會讓那些缺陷永遠沒有人發現。兩件事不衝突,所以兩件都做。
 *
 * **權威驗證的失敗不會走到這裡**:它在序列內部就翻成了說法(`applyFailure`),因為
 * 4.9 對它有專屬的要求(「告知確認未能完成」),而這裡的三類共用 7.3 的一般說法。
 */
async function attemptWrite() {
  writing = true;
  render();

  try {
    await runWriteSequence();
  } catch (error) {
    if (error instanceof CatalogError) {
      attemptNote = INDEX_FAILURE_NOTE;
    } else if (error instanceof CorpusFileError) {
      // 這一種的訊息**原樣顯示**:它由 `corpus-file.js` 寫給維護者看,講的是目標檔
      // 本身不是題目陣列。與後端或平台的原文刻意相反 —— 那些是內部細節。
      attemptNote = error.message;
    } else {
      attemptNote = WRITE_FAILURE_NOTE;
      throw error;
    }
  } finally {
    // 停用的解除與那句話的呈現在同一次重畫裡。`throw` 之後這裡照樣跑得到,所以
    // 「按鈕永遠灰著」不會因為某條例外路徑而發生。
    writing = false;
    render();
  }
}

renderDifficultyOptions();

// 平台支不支援(6.3)。**問一次就夠,而且要在載入時問**:`isSupported()` 只看一個
// 全域函式在不在,沒有副作用也不會在分頁存續期間改變答案。等到按下寫入才說是錯的
// —— 維護者會先花時間貼 FEN、填完七個欄位、核對盤面,然後才知道這個瀏覽器從一開始
// 就做不到這件事。
//
// 這也是本檔唯一不經 `render()` 的畫面更新,理由正是「它不是當下那些值的函式」。
elements.unsupported.hidden = isSupported();

// **`input` 而不是 `change`**(requirement 2.1):`change` 要等到欄位失焦才發,貼上
// FEN 之後盤面得等使用者去點別的地方才出現。`input` 涵蓋鍵入、貼上、剪下與復原,
// `<select>` 換選項時同樣會發。
//
// 七個欄位接的是同一個處理器,只是各自帶上自己的名字:畫面是**七個值一起**決定的
// (難度沒選會讓寫入停用、題號會影響描述建議值),為每一欄各寫一段就會漏掉那些跨欄
// 的關係。名字唯一的用途是讓建議值知道使用者此刻正在改哪一欄。
for (const control of elements.controls.values()) {
  control.addEventListener('input', () => {
    // 動手收下一題,上一題的成功訊息就讓位(7.1;見 `successNote`)。那句話講的是
    // 一個已經不在畫面上的題號,留著只會與此刻表單裡的內容對不上。
    successNote = '';
    render();
  });
}

// 寫入序列。處理器本身刻意是同步的,只把那個 promise 丟出去
// ——`addEventListener` 不會等 async 處理器,回傳一個 promise 給它只會讓「誰來處理
// 拒絕」變得含混。真正的例外處理集中在 `attemptWrite()` 一處。
//
// **停用中的按鈕不會發出 click**,所以這裡不必再判一次淺層檢查有沒有通過:
// 那個判斷已經在 `renderWriteAction()`,寫兩份就會有兩份要一起改。
elements.write.addEventListener('click', () => {
  void attemptWrite();
});

// 選檔(5.1、5.11)。與寫入同一個形態:處理器是同步的,只把 promise 丟出去。
//
// **但這一顆有一件寫入沒有的事** —— `choose()` 在第一個 `await` 之前必須把對話框
// 開下去,否則手勢就沒了。那件事由 `pickCorpusFile()` 保證(它刻意不是 async),
// 這裡要守的是:`choose()` 裡 `pickCorpusFile()` 之前不得插入任何 `await`。
elements.pickFile.addEventListener('click', () => {
  void choose();
});

// 載入時就畫一次:此刻輸入框通常是空的,呈現的即是空盤面(2.5),而五項淺層檢查
// 皆未通過,寫入停用。檔名顯示也在這一次畫上「尚未選定」。同一個輸入值不該因為「使用者有沒有打過字」而呈現兩種樣子 ——
// 那是把歷史記進了畫面。重新整理後瀏覽器回填輸入框的情形也一併涵蓋:畫出來的、
// 檢查的仍是當下那些字。
render();
