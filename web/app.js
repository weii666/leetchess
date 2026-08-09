/**
 * 組裝層:把狀態機、棋盤與記譜接到頁面骨架上。
 *
 * 這是依賴鏈的最右端 —— **沒有任何模組可以匯入它**。它自己不判斷棋規、不發請求、
 * 不推導對局狀態,能做的只有兩件事:
 *
 * 1. 把使用者的點擊翻成狀態機的一個操作(`play` / `reset`);
 * 2. 把狀態機的每一份快照翻成畫面。
 *
 * ## 只有一條寫進畫面的路徑
 *
 * 所有呈現都由 `render()` 自**當下的快照**整份重畫,沒有任何一處是「事件發生時
 * 順手改一下某個節點」。因為那種增量更新等於在呈現層再存一份狀態,而重來與失敗
 * 復原(design 的「走法序列是唯一真相」)就得各自記得要清哪些地方 —— 清不乾淨
 * 正是「畫面卡在不可知狀態」的來源(requirements 7.4)。
 *
 * 唯一存在這裡的狀態是**選中的格**,而它純屬呈現:選子不改變對局,重畫盤面之外
 * 沒有任何副作用。它在對局狀態每次變動時一律清掉 —— 走完一手之後,原本選中的
 * 那枚子已經不在原地了。
 *
 * ## 使用者看到的文字全部在這裡生出來
 *
 * `api.js` 保證後端的原文一個字都到不了這一層(requirements 7.5),失敗只帶一個
 * 類別碼。因此「找不到題目」這幾個字必須由本檔依碼自己決定,而不是把後端回的東西
 * 印出來。文字一律繁體中文(requirements 8.3)。
 *
 * ## 本檔目前不含的部分
 *
 * 三態信號、等待呈現與一般的錯誤區塊屬 tasks 4.4;版面屬 `style.css`(tasks 5.1)。
 * 唯一先做的是 requirements 1.4(題目不存在的告知)—— 那是**載入**失敗,沒有它
 * 使用者會對著一片空白不知道發生什麼事。
 */

import { renderBoard } from './board.js';
import { applyMove, parseFen } from './fen.js';
import { createGame } from './game.js';
import { uci2cn } from './notation.js';

/** 網址沒指定時預設載入的題號。 */
const DEFAULT_POSITION_ID = 1;

/** 兩方的稱呼。 */
const SIDE_NAMES = Object.freeze({ red: '紅方', black: '黑方' });

/**
 * 載入失敗時要說的話,依類別碼決定。
 *
 * **後端只給碼,文字全由這裡產生**(requirements 7.5)。題目不存在是唯一需要
 * 特別說明的一種(requirements 1.4):它不是暫時性的問題,叫使用者重試沒有意義。
 */
const LOAD_FAILURE_MESSAGES = Object.freeze({
  POSITION_NOT_FOUND: '找不到這一題。',
});

/** 其餘載入失敗共用的說法 —— 連線、逾時、後端出錯,對使用者而言都是「再試一次」。 */
const GENERIC_LOAD_FAILURE = '題目載入失敗,請稍後再試。';

/** 載入失敗時頂上標題的位置。不能留著「載入中…」,那會讓人以為還在等。 */
const LOAD_FAILURE_TITLES = Object.freeze({
  POSITION_NOT_FOUND: '找不到題目',
});

const GENERIC_LOAD_FAILURE_TITLE = '題目載入失敗';

/**
 * 沒有題目、沒有失敗、也沒有請求在跑時的說法。
 *
 * 這一種**不得說成「載入中」**:宣稱正在進行卻沒有任何請求在跑,使用者會一直等
 * 一個永遠不會來的結果,而畫面上沒有東西告訴他該做什麼(requirements 7.4)。
 */
const IDLE_TITLE = '尚未載入題目';

/** 沒有值可填時的佔位符號。 */
const BLANK = '—';

const elements = {
  board: document.getElementById('board'),
  title: document.getElementById('puzzle-title'),
  source: document.getElementById('puzzle-source'),
  maxDtm: document.getElementById('puzzle-max-dtm'),
  turn: document.getElementById('turn'),
  error: document.getElementById('error'),
  moves: document.getElementById('moves'),
  reset: document.getElementById('reset'),
};

/** 選中的格,例如 `'d8'`;沒有選子時為 `null`。**唯一存在呈現層的狀態。** */
let selected = null;

/**
 * 要載入哪一題**由外部決定** —— 跨題導航屬 problem-browser,本介面只認網址上的
 * 題號(`?id=…`)。
 *
 * 純數字轉成數值,其餘原樣送出:題號長什麼樣是後端的定義,這裡不替它把關,
 * 認不得的題號會走 requirements 1.4 那條路。
 */
function readPositionId(search) {
  const raw = new URLSearchParams(search).get('id');
  if (raw == null || raw.trim() === '') return DEFAULT_POSITION_ID;
  const numeric = Number(raw);
  return Number.isInteger(numeric) ? numeric : raw;
}

const game = createGame({ positionId: readPositionId(window.location.search) });

/** 載入失敗的說法;`code` 認不得時給通用的那一句。 */
function loadFailureMessage(code) {
  return LOAD_FAILURE_MESSAGES[code] ?? GENERIC_LOAD_FAILURE;
}

/**
 * 還沒有題目可呈現時,頂上標題該說的話 —— 三種情況分開,不合併。
 *
 * 「失敗」與「載入中」以外還有第三種:兩者都不是。把它併進載入中等於憑空宣稱有
 * 進度,因此一律照 `waiting` 說實話。
 */
function noPuzzleTitle(state) {
  if (state.error) return LOAD_FAILURE_TITLES[state.error.code] ?? GENERIC_LOAD_FAILURE_TITLE;
  return state.waiting ? '載入中…' : IDLE_TITLE;
}

/**
 * 側欄的題目資訊(requirements 1.2、1.3)。
 *
 * 最長殺著距離是**條件式**的(1.3 的 Where):沒有這項資訊時留佔位符號,
 * 不得憑空生一個數字。以 `!= null` 判斷而非 falsy —— 這個欄位可能是 0。
 */
function renderPuzzleInfo(state) {
  if (!state.position) {
    elements.title.textContent = noPuzzleTitle(state);
    elements.source.textContent = BLANK;
    elements.maxDtm.textContent = BLANK;
    return;
  }
  const { title, source, max_dtm: maxDtm } = state.position;
  elements.title.textContent = title || '(未命名)';
  elements.source.textContent = source || BLANK;
  elements.maxDtm.textContent = maxDtm != null ? `${maxDtm} 步` : BLANK;
}

/**
 * 當前輪方(requirements 8.4);對局結束後改為呈現勝方(requirements 3.2)。
 *
 * 勝方**一律照後端回報** —— `userWon` 也是狀態機依後端的勝方推導的,這裡不自己
 * 判斷誰贏。
 */
function turnText(state) {
  if (!state.position) return `輪方:${BLANK}`;
  if (state.over) {
    if (!state.winner) return '對局結束';
    const winner = `對局結束:${SIDE_NAMES[state.winner] ?? state.winner}勝`;
    return state.userWon ? `${winner}(你獲勝)` : winner;
  }
  const side = SIDE_NAMES[state.turn] ?? state.turn;
  return state.turn === state.userSide ? `輪方:${side}(你)` : `輪方:${side}`;
}

/**
 * 載入失敗的告知(requirements 1.4)。
 *
 * 只處理**沒有題目**的情況;走子途中的失敗屬 tasks 4.4。題目載入成功後這一區
 * 一律收起來 —— 失敗後重試成功的話,上一次的訊息不能留在畫面上。
 */
function renderLoadFailure(state) {
  const failed = state.position == null && state.error != null;
  elements.error.textContent = failed ? loadFailureMessage(state.error.code) : '';
  elements.error.hidden = !failed;
}

/**
 * 沒有盤面可畫時放在 `#board` 的那句話 —— 與標題同樣分三種,理由也相同。
 */
function boardPlaceholderText(state) {
  if (state.error) return `${loadFailureMessage(state.error.code)}目前沒有盤面可以呈現。`;
  return state.waiting ? '正在載入題目…' : `${IDLE_TITLE},目前沒有盤面可以呈現。`;
}

/**
 * 盤面(requirements 1.1、2.1、2.2)。
 *
 * 選中與落點標示都只是傳給 `board.js` 的資料,點擊只經回呼往外通知 —— 畫面要變,
 * 一律是重畫一次的結果。
 *
 * **沒有盤面可畫時放一句話,而不是留一片空白**(requirements 1.4):`#board` 空著
 * 看起來就是一面畫不出來的棋盤,使用者無從得知是還在載入還是題目不存在。
 */
function renderBoardArea(state) {
  if (!state.board) {
    const note = document.createElement('p');
    note.className = 'board-placeholder';
    note.textContent = boardPlaceholderText(state);
    elements.board.replaceChildren(note);
    return;
  }
  renderBoard(elements.board, {
    board: state.board,
    legalMoves: state.legalMoves,
    selected,
    onSelect: (square) => {
      selected = square;
      render();
    },
    onMove: (uci) => {
      selected = null;
      game.play(uci);
    },
  });
}

/**
 * 歷史著法(requirements 8.1)。自 POC 的 `renderMoves` 移植。
 *
 * 一列是一個回合:紅方一手加黑方的應手。黑方沒有應手(排局的最後一手)時那一半
 * 留空,不補任何字。
 *
 * 記譜描述的是**走子前**的盤面,所以自起始局面重放一次:先記譜、再套用。這裡不
 * 用快照的 `board` —— 那是走完之後的盤面,拿它記譜會算錯縱線序號與前後子。
 *
 * 移植時的兩處調整:骨架用的是 `<ol>`,序號由它自己編,POC 的 `mv-n` 因此不需要;
 * 內容一律 `textContent` 而非 `innerHTML`。
 */
function renderMoves(state) {
  const notation = [];
  if (state.position) {
    const board = parseFen(state.position.fen);
    for (const uci of state.moves) {
      notation.push(uci2cn(board, uci));
      applyMove(board, uci);
    }
  }

  const rows = [];
  for (let index = 0; index < notation.length; index += 2) {
    const row = document.createElement('li');
    const red = document.createElement('span');
    red.className = 'mv-r';
    red.textContent = notation[index];
    const black = document.createElement('span');
    black.className = 'mv-b';
    black.textContent = notation[index + 1] ?? '';
    row.append(red, black);
    rows.push(row);
  }
  elements.moves.replaceChildren(...rows);
  elements.moves.scrollTop = elements.moves.scrollHeight;
}

/** 把當下的快照整份畫出來。畫面的每一次變動都只經過這裡。 */
function render() {
  const state = game.getState();
  renderPuzzleInfo(state);
  elements.turn.textContent = turnText(state);
  renderLoadFailure(state);
  renderMoves(state);
  renderBoardArea(state);
}

// 對局狀態一變就整份重畫,並清掉選中的格 —— 走完一手之後,原本選中的那枚子已經
// 不在原地;失敗復原與重來則是整份換掉走法序列,選取更是無從對應。
game.subscribe(() => {
  selected = null;
  render();
});

// 重來只是狀態機的一個操作(requirements 5.1);它不打後端,也不需要本檔清理任何
// 東西 —— 走法序列一清空,盤面與歷史著法自然回到起點。
//
// **但題目從未載入成功時,重來還原不了任何東西** —— 起始局面根本沒拿到過,清空
// 走法序列只會把 requirements 1.4 的告知一併抹掉,留下一個沒有請求在跑的空狀態。
// 而這顆按鈕是那個失敗畫面上唯一的按鈕:使用者按下去之後除了手動重新整理就沒有
// 出路了,正是 requirements 7.4 禁止的無法復原畫面。那裡唯一有意義的復原是**重試
// 載入**(requirements 7.1:再呼叫一次 `load()` 即為重試)。
elements.reset.addEventListener('click', () => {
  if (game.getState().position == null) {
    game.load();
    return;
  }
  game.reset();
});

render();
game.load();
