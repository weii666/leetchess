/**
 * 收題頁的組裝層:把七個欄位接到盤面、難度選項、描述建議值、淺層檢查與撞號檢查上
 * (tasks 4.2、4.3、5.1;requirements 2.1–2.6、3.2、3.3、3.6、3.7、4.1–4.6、8.3、8.4)。
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
 * 因此畫面該長什麼樣幾乎完全是七個欄位當下那些字的函式。模組層的狀態變數只有三個
 * (`suggested`、`writtenIds`、`collidedId`),它們記的都**不是畫面**,而是三件無法
 * 由當下的值看出來的事:描述欄裡那句話是誰打的、本分頁已經成功寫入過哪些題號、
 * 上一次寫入嘗試指認了哪個題號撞號。三者一律只經 `render()` 反映到畫面上。
 * (tasks 5.3 會再帶進目錄控制代碼,屆時同樣只驅動這一條路徑。)
 *
 * ## 描述建議值:每一次都算,但只在沒有人動過它時才寫回去
 *
 * requirement 3.6 要在題號與局名皆已填而描述仍為空時給出建議值,3.7 要最終值以維護者
 * 輸入的內容為準。兩者合起來的難處在於**建議值每一次輸入變動都會重算**:重算若無條件
 * 寫回描述欄,維護者打好的那一句話會在他去改標籤時被蓋掉,而 3.7 就等於沒有實作。
 *
 * 判準是「描述欄裡的字是不是本檔上一次放進去的那一句」(`suggested`)。是的話那一欄
 * 仍屬**自動狀態**,可以照著新的題號局名更新;不是的話它已經是維護者的內容,本檔不再
 * 碰它。空字串同樣算自動狀態 —— 那正是 3.6 的觸發條件。
 *
 * 還有一條:**使用者正在改描述欄時一律不寫回去**(`render()` 的 `origin`)。少了它,
 * 維護者全選刪掉想重寫時,那一刻描述變成空的、建議值立刻補回來,他的下一個字就接在
 * 建議值後面。3.6 講的是描述為空時「提供」建議值,不是「不准它空著」。
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
 * ## 寫入是一條序列,本檔目前只走到第二步
 *
 * 寫入一題的完整次序是(design 的 System Flows):**取題庫索引 → 撞號 → 送權威驗證
 * → 取目錄授權 → 重讀目標檔 → 文字層追加 → 寫回 → 記下題號並清空欄位**。
 * tasks 5.1 實作前兩步,5.2 接上權威驗證,5.3 接上授權與寫檔,5.4 接上成敗的呈現。
 * 因此 `#write` 的 click 處理**刻意寫成一條會被往下接的序列**(`runWriteSequence`),
 * 而不是一個自成一體的動作:5.2 要加的東西接在撞號通過的那一行之後,不必回頭改
 * 這裡的形狀。
 *
 * 淺層檢查未通過時 `#write` 仍然是停用的(4.1),所以序列跑得起來就代表七個欄位
 * 都已填妥 —— 撞號檢查不必再驗一次題號的寫法。
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
 * 變成最放行的一刻。此時該對使用者說什麼屬 5.4 的一般失敗處理(design 的 Risks)。
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

import { renderBoard } from '../board.js';
import { CatalogError, loadCatalog } from '../catalog.js';
import { DIFFICULTY_LABELS } from '../difficulty.js';
import { parseFen } from '../fen.js';
import { checkForm, checkFenStructure, sideFromFen, suggestDescription } from './check.js';

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

/** 起手方那一行的說法(2.6)。 */
const SIDE_TO_MOVE_LABEL = '起手方:';

/** FEN 讀不出走子方時的佔位符號 —— 不猜一個,黑先的排局會被靜默標錯。 */
const UNKNOWN_SIDE = '—';

/**
 * 盤面被清掉時放在那一格的說法(2.4)。
 *
 * 只交代「這一格為什麼空著」,**不重複哪裡不對** —— 那句話定位在 FEN 欄位旁邊,
 * 兩處說同一件事只會讓使用者以為有兩個問題。
 */
const CLEARED_BOARD_NOTE = 'FEN 目前無法解析,盤面已清空。';

/**
 * 七個欄位的名稱,**順序即表單欄位順序**,也就是 `checkForm()` 回傳清單的順序
 * (該函式明載這件事)。寫入操作旁的點名匯總照這個順序念,使用者由上往下找得到。
 */
const FIELD_NAMES = Object.freeze([
  'id',
  'title',
  'description',
  'difficulty',
  'tags',
  'fen',
  'target',
]);

/**
 * 書名與卷次的分隔符號(`structure.md` 的 Naming Conventions)。
 *
 * 資料夾是 `適情雅趣~卷一`,而題目的描述只寫書名(「適情雅趣 第二五局 患在几席」)。
 * 分隔符是 `~` 而不是 `-`,以與檔名的局號區間(`20-24.json`)區隔開。
 */
const VOLUME_SEPARATOR = '~';

/** 目標路徑的分段符號。路徑相對於題庫根目錄,一律以 `/` 分段(5.1)。 */
const PATH_SEPARATOR = '/';

/** 停用寫入時那一行的開頭(8.4)。後面接上未通過項目的欄位名稱。 */
const WRITE_NOTE_PREFIX = '無法寫入,尚未通過:';

/** 未通過的項目全都不屬於任一欄位時的退路 —— 空清單不會走到這裡。 */
const WRITE_NOTE_FALLBACK = '無法寫入,仍有項目未通過。';

/** 欄位名稱之間的分隔。頓號是中文的列舉符號,與提示裡的用法一致。 */
const NAME_SEPARATOR = '、';

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
 * 與控制項的 `data-field` 同名 —— 七個欄位因此共用這一個查詢,增減欄位時要動的是
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
  // 起手方(2.6)。它在 HTML 裡就排在 FEN 那一欄底下 —— **不能擺進 `#board`**,
  // `renderBoard()` 以 `replaceChildren` 畫盤,擺在那裡的話第一次繪盤就會被換掉。
  sideToMove: document.getElementById('side-to-move'),
  // 寫入操作與它的停用說明(4.1、8.4)。click 跑的是寫入序列,本輪走到撞號檢查
  // 為止(見檔首)。
  write: document.getElementById('write'),
  writeNote: document.getElementById('write-note'),
};

/**
 * 描述欄裡那句話,若它是本檔放進去的建議值。
 *
 * **本檔唯一的模組層狀態**,而且它記的不是畫面而是來源:描述欄當下的值等於它時,
 * 那一欄仍屬自動狀態、可以隨題號局名更新;不等於時,那是維護者自己的內容,3.7 要求
 * 以它為準,本檔不再碰。空字串(初始值)同樣算自動狀態 —— 那正是 3.6 的觸發條件。
 *
 * 維護者剛好把描述改成與建議值一字不差時,這兩者分不出來,而那沒有任何後果:接下來
 * 對那一欄做的事與「它還是建議值」完全相同。
 */
let suggested = '';

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
 * 那句話講的就是別人了。`collisionMessage()` 因此以「當下的題號是否仍是它」當
 * 顯示的條件,畫面便不會掛著一句已經與輸入框對不上的紅字。
 */
let collidedId = null;

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
 * 七個欄位的當下值,形狀即 `check.js` 的 `FormValues`。
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
 * @typedef {{board: (string|null)[][]|null, message: string, side: string|null}} FenView
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
 * 起手方三條路共用同一行:`sideFromFen()` 刻意不先跑結構檢查(見該函式說明),
 * 一個列數還沒打完的 FEN 仍可能已經打完走子方,先給出起手方沒有壞處。
 *
 * **前後的空白在最上面就去掉一次,這一行是必要的。** `checkFenStructure()` 與
 * `sideFromFen()` 內部都先 `trim()`,而 `parseFen()` 是以**字面的半形空格**切欄位的
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
  const side = sideFromFen(text);

  if (text === '') {
    return { board: parseFen(EMPTY_FEN), message: '', side };
  }

  const issue = checkFenStructure(text);
  if (issue !== null) {
    return { board: null, message: issue.message, side };
  }

  return { board: parseFen(text), message: '', side };
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
 * **接在既有的「尚未選擇」之後,不動它、也不重排。** 那一個選項的值是空字串,
 * `checkForm()` 以它判定「還沒選」;插到最前面會讓維護者在沒有做過選擇的情況下
 * 寫進一個難度。
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
}

/**
 * 由目標檔案路徑取得書名(requirement 3.6)。
 *
 * **書名沒有自己的輸入欄**(3.5):出處由題目所在的書目資料夾表達,因此描述建議值
 * 要用的書名只能從目標路徑推出來 —— 取第一段(書目資料夾),再切掉 `~` 之後的卷次。
 * 資料夾是 `適情雅趣~卷一`,而題庫既有的描述寫的是「適情雅趣 第二五局 患在几席」:
 * **卷次不入描述**,這條換算是 `structure.md` 的 Naming Conventions 載明的。
 *
 * 路徑還沒有資料夾那一段(空字串、或只打了 `26.json`)時回空字串,`suggestDescription`
 * 收到空書名就給不出建議值 —— 那是對的:此刻沒有任何線索指出這一題屬於哪一本書,
 * 猜一個書名會讓維護者收到一句看起來很像回事、卻寫錯出處的描述。建議值會在他填上
 * 目標路徑的那一刻出現,順序不拘。
 *
 * 以 `/` 開頭的路徑(`/適情雅趣~卷一/26.json`)第一段是空的,因而同樣給不出建議值。
 * **不為它補一條特例**:那條路徑本身就不合規(路徑相對於題庫根目錄),`checkTargetPath`
 * 已經在目標路徑那一欄說明了原因,而那一欄此刻是填了的、訊息看得見。先讓維護者把
 * 路徑改對,建議值隨即跟上 —— 對一個被判為不合規的路徑推敲書名,才是會誤導人的那條路。
 *
 * @param {string} target 目標檔案路徑,相對於題庫根目錄。
 * @returns {string} 取不到書名時為空字串。
 */
function bookFromTarget(target) {
  const segments = target.trim().split(PATH_SEPARATOR);
  if (segments.length < 2) {
    return '';
  }
  return segments[0].split(VOLUME_SEPARATOR)[0].trim();
}

/**
 * 描述建議值(requirements 3.6、3.7)。
 *
 * 兩道閘門,缺一不可:
 *
 * - **`origin === 'description'` 一律不寫**。使用者正在改那一欄,此刻補字進去會與他
 *   搶輸入框:全選刪掉想重寫的那一刻描述變成空的,建議值立刻補回來,他的下一個字
 *   就接在建議值後面。
 * - **只在自動狀態下寫**。描述欄裡的字不是本檔上一次放進去的那一句時,它已經是
 *   維護者的內容(3.7),本檔不再碰它。
 *
 * 湊得出建議值的前提是題號與局名都**通過檢查**,而不只是「非空」:判準沿用
 * `checkForm()`,組裝層不自己再寫一份「什麼是正整數」—— 寫一份就會與 `check.js`
 * 分家,而那正是 `1e3` 這種輸入被 `Number()` 收下、變成一個誰也沒打過的題號的來源。
 *
 * 湊不出來時寫回空字串而不是留著上一句:那一句是照舊的題號局名算出來的,留著就是
 * 一句**已知過期**的描述掛在自動狀態的欄位裡,而維護者會以為那是照他剛改的內容更新
 * 過的。描述欄在他動它之前,就是其餘三欄的函式。
 *
 * @param {string|null} origin 觸發本次重畫的欄位名;載入時為 `null`。
 */
function applySuggestion(origin) {
  if (origin === 'description') {
    return;
  }
  const description = elements.controls.get('description');
  if (description.value !== '' && description.value !== suggested) {
    return;
  }

  const values = readValues();
  // 這一次的檢查只為了「題號與局名可不可信」。清單完整地再算一次的成本是七個字串
  // 的判斷,而換來的是判準只有一份。
  const issues = checkForm(values);
  const blocked = issues.some((issue) => issue.field === 'id' || issue.field === 'title');
  const text = blocked
    ? ''
    : suggestDescription(bookFromTarget(values.target), Number(values.id.trim()), values.title);

  // 值沒變就不寫回去:指派 `value` 會重設游標與捲動位置,而在別的欄位打字時本函式
  // 每一個鍵入都會走到這裡。
  if (text === description.value) {
    return;
  }
  description.value = text;
  suggested = text;
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
 * @returns {string} 沒有指認、或指認已不適用時為空字串。
 */
function collisionMessage() {
  if (collidedId === null || Number(valueOf('id').trim()) !== collidedId) {
    return '';
  }
  return collisionText(collidedId);
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
 * ## 撞號的指認也落在這裡
 *
 * 它與 FEN 那一欄一樣是自淺層清單之外傳進來的,理由卻不同:FEN 是為了與盤面同源,
 * 撞號則是因為它**算不出來** —— 要問過題庫索引才知道(4.3、4.4)。兩者都寫進同一組
 * 訊息槽,使用者不必分辨一句話是哪一層檢查產生的。
 *
 * 撞號的指認**只在題號那一欄沒有淺層問題時才可能出現**:指認以數值比對成立
 * (見 `collisionMessage`),而題號空著或不是正整數時比不出相等。因此這裡直接覆寫
 * 不會蓋掉「請填入題號」那一類的說法 —— 兩者不可能同時有話說。
 *
 * @param {import('./check.js').CheckIssue[]} issues
 * @param {string} fenMessage FEN 欄位的當下訊息(2.4、2.5)。
 * @param {string} idMessage 題號欄的撞號指認(4.3、4.4);沒有時為空字串。
 */
function renderMessages(issues, fenMessage, idMessage) {
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
  if (idMessage !== '') {
    texts.set('id', idMessage);
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
 * @param {import('./check.js').CheckIssue[]} issues
 */
function renderWriteAction(issues) {
  elements.write.disabled = issues.length > 0;

  const names = issues.map((issue) => labelOf(issue.field)).filter((name) => name !== '');
  let text = '';
  if (issues.length > 0) {
    text = names.length > 0
      ? WRITE_NOTE_PREFIX + names.join(NAME_SEPARATOR)
      : WRITE_NOTE_FALLBACK;
  }
  elements.writeNote.textContent = text;
  elements.writeNote.hidden = text === '';
}

/**
 * 把當下的輸入整份畫出來。畫面的每一次變動都只經過這裡。
 *
 * 建議值排在檢查**之前**:它可能改寫描述欄,而檢查要看的是改寫之後的值 —— 順序顛倒
 * 的話,一個剛被填上建議值的描述欄底下會掛著「請填入描述」。
 *
 * @param {string|null} origin 觸發本次重畫的欄位名;載入時為 `null`。用途只有一個:
 *   讓建議值不與正在改描述的使用者搶輸入框(見 `applySuggestion`)。
 */
function render(origin) {
  const view = readFenView(valueOf('fen'));
  renderBoardArea(view);
  elements.sideToMove.textContent = SIDE_TO_MOVE_LABEL + (view.side ?? UNKNOWN_SIDE);

  applySuggestion(origin);

  const issues = checkForm(readValues());
  renderMessages(issues, view.message, collisionMessage());
  renderWriteAction(issues);
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
 * 寫入序列的前兩步:取題庫索引、撞號檢查(requirements 4.3、4.4、4.5)。
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
 * **序列到此為止。** 5.2 的權威驗證接在下面那一行註解的位置,再往下是 5.3 的目錄
 * 授權與寫檔、5.4 的成敗呈現。
 */
async function runWriteSequence() {
  const id = Number(valueOf('id').trim());
  const { positions } = await loadCatalog();
  const existingIds = new Set(positions.map((position) => Number(position.id)));

  collidedId = existingIds.has(id) || writtenIds.has(id) ? id : null;
  render(null);
  if (collidedId !== null) {
    return;
  }

  // 撞號通過(4.5)。tasks 5.2 的權威驗證自此接續,而後是 5.3 的授權與寫檔。
}

/**
 * 按下寫入時跑一次寫入序列(requirements 4.3、4.4、4.5)。
 *
 * 這一層只處理**取不到索引**這一種停止:服務重啟中、連線斷掉或逾時的時候
 * `loadCatalog()` 拋出 `CatalogError`,而寫入不成立(design 的 Risks 已把它歸在
 * 7.3 的一般失敗處理,不另立分支)。
 *
 * **不得把它折成「沒有撞號」**:那會讓最容易撞號的那一刻 —— 剛寫完一題、服務正在
 * 重啟、索引因此取不到 —— 變成最放行的一刻。此處因此只是讓序列停下,連上一次的
 * 撞號指認都不動:撤下指認是「撞號檢查通過」的後果,而這一次根本沒問到答案。
 *
 * 此刻該對使用者說什麼屬 tasks 5.4 的一般失敗呈現,所以這裡刻意不寫任何說法 ——
 * 兩處各寫一句只會讓同一件事有兩種講法。
 *
 * 其餘的例外原樣往上:那些不是預期內的停止而是缺陷,吞掉會讓它們永遠沒有人發現。
 */
async function attemptWrite() {
  try {
    await runWriteSequence();
  } catch (error) {
    if (!(error instanceof CatalogError)) {
      throw error;
    }
  }
}

renderDifficultyOptions();

// **`input` 而不是 `change`**(requirement 2.1):`change` 要等到欄位失焦才發,貼上
// FEN 之後盤面得等使用者去點別的地方才出現。`input` 涵蓋鍵入、貼上、剪下與復原,
// `<select>` 換選項時同樣會發。
//
// 七個欄位接的是同一個處理器,只是各自帶上自己的名字:畫面是**七個值一起**決定的
// (難度沒選會讓寫入停用、題號會影響描述建議值),為每一欄各寫一段就會漏掉那些跨欄
// 的關係。名字唯一的用途是讓建議值知道使用者此刻正在改哪一欄。
for (const [name, control] of elements.controls) {
  control.addEventListener('input', () => render(name));
}

// 寫入序列(5.1 走到撞號檢查為止)。處理器本身刻意是同步的,只把那個 promise 丟出去
// ——`addEventListener` 不會等 async 處理器,回傳一個 promise 給它只會讓「誰來處理
// 拒絕」變得含混。真正的例外處理集中在 `attemptWrite()` 一處。
//
// **停用中的按鈕不會發出 click**,所以這裡不必再判一次淺層檢查有沒有通過:
// 那個判斷已經在 `renderWriteAction()`,寫兩份就會有兩份要一起改。
elements.write.addEventListener('click', () => {
  void attemptWrite();
});

// 載入時就畫一次:此刻輸入框通常是空的,呈現的即是空盤面(2.5),而七項淺層檢查
// 皆未通過,寫入停用。同一個輸入值不該因為「使用者有沒有打過字」而呈現兩種樣子 ——
// 那是把歷史記進了畫面。重新整理後瀏覽器回填輸入框的情形也一併涵蓋:畫出來的、
// 檢查的仍是當下那些字。
render(null);
