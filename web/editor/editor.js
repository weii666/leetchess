/**
 * 收題頁的組裝層:把七個欄位接到盤面、難度選項、描述建議值與淺層檢查上
 * (tasks 4.2、4.3;requirements 2.1–2.6、3.2、3.3、3.6、3.7、4.1、4.2、4.6、8.3、8.4)。
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
 * 因此畫面該長什麼樣幾乎完全是七個欄位當下那些字的函式。**模組層只有一個狀態變數**
 * (`suggested`,見下一段),它記的不是畫面而是「描述欄裡那句話是誰打的」——
 * 那件事無法由當下的值看出來。(tasks 5.x 會帶進其餘的狀態 —— 目錄控制代碼、本分頁
 * 已寫入的題號 —— 屆時它們同樣只驅動這一條路徑。)
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
 * ## 本檔目前不含的部分
 *
 * 撞號、權威驗證、目錄授權與寫檔屬第 5 組。`#write` 因此**還沒有任何 click 處理**:
 * 本輪只做到「未通過時停用它」(4.1)。它們都會接在同一個 `render()` 上,不必回頭
 * 改動這裡的形狀。
 */

import { renderBoard } from '../board.js';
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
  // 寫入操作與它的停用說明(4.1、8.4)。本輪只停用它,不接 click(見檔首)。
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
 * 把未通過的項目寫到各欄位旁(requirements 4.1、4.2、4.6、8.4)。
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
 * @param {import('./check.js').CheckIssue[]} issues
 * @param {string} fenMessage FEN 欄位的當下訊息(2.4、2.5)。
 */
function renderMessages(issues, fenMessage) {
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
  renderMessages(issues, view.message);
  renderWriteAction(issues);
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

// 載入時就畫一次:此刻輸入框通常是空的,呈現的即是空盤面(2.5),而七項淺層檢查
// 皆未通過,寫入停用。同一個輸入值不該因為「使用者有沒有打過字」而呈現兩種樣子 ——
// 那是把歷史記進了畫面。重新整理後瀏覽器回填輸入框的情形也一併涵蓋:畫出來的、
// 檢查的仍是當下那些字。
render(null);
