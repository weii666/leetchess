/**
 * 收題頁的組裝層:把 FEN 欄位接到盤面上(tasks 4.2;requirements 2.1–2.6)。
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
 * 因此本檔沒有模組層的狀態變數:畫面該長什麼樣完全是 `#field-fen` 那串字的函式。
 * (tasks 4.3 / 5.x 會帶進真正的狀態 —— 目錄控制代碼、本分頁已寫入的題號、當前的
 * 檢查清單 —— 屆時它們同樣只驅動這一條路徑。)
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
 * 難度選項、描述建議值、其餘六欄的檢查呈現屬 tasks 4.3;撞號、權威驗證、授權與
 * 寫檔屬第 5 組。它們都會接在同一個 `render()` 上,不必回頭改動這裡的形狀。
 */

import { renderBoard } from '../board.js';
import { parseFen } from '../fen.js';
import { checkFenStructure, sideFromFen } from './check.js';

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
 * 認槽位用 `data-message-for` 而不是 id,取值即 `check.js` 的 `CheckIssue.field` ——
 * **tasks 4.3 為其餘六欄接上訊息時,在 HTML 補一個同形狀的槽即可**,本函式一個字
 * 都不必改。
 *
 * @param {string} name 欄位名,例如 `'fen'`。
 * @returns {HTMLElement}
 */
function fieldMessage(name) {
  return document.querySelector(`[data-message-for="${name}"]`);
}

const elements = {
  board: document.getElementById('board'),
  fen: field('fen'),
  fenMessage: fieldMessage('fen'),
  // 起手方(2.6)。它在 HTML 裡就排在 FEN 那一欄底下 —— **不能擺進 `#board`**,
  // `renderBoard()` 以 `replaceChildren` 畫盤,擺在那裡的話第一次繪盤就會被換掉。
  sideToMove: document.getElementById('side-to-move'),
};

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

/** 把當下的輸入整份畫出來。畫面的每一次變動都只經過這裡。 */
function render() {
  const view = readFenView(elements.fen.value);
  renderBoardArea(view);
  elements.fenMessage.textContent = view.message;
  elements.fenMessage.hidden = view.message === '';
  elements.sideToMove.textContent = SIDE_TO_MOVE_LABEL + (view.side ?? UNKNOWN_SIDE);
}

// **`input` 而不是 `change`**(requirement 2.1):`change` 要等到欄位失焦才發,貼上
// FEN 之後盤面得等使用者去點別的地方才出現。`input` 涵蓋鍵入、貼上、剪下與復原。
elements.fen.addEventListener('input', render);

// 載入時就畫一次:此刻輸入框通常是空的,呈現的即是空盤面(2.5)。同一個輸入值不
// 該因為「使用者有沒有打過字」而呈現兩種樣子 —— 那是把歷史記進了畫面。重新整理後
// 瀏覽器回填輸入框的情形也一併涵蓋:畫出來的仍是當下那串字。
render();
