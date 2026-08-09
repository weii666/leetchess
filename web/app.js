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
 * ## 失敗只有一個區塊、只分兩類
 *
 * 載入失敗與走子途中的失敗**寫進同一個 `#error`**(design 的 Error Handling)。
 * 兩條各自寫死的路徑會互相蓋掉對方的訊息 —— 共用一個區塊才保證任一時刻只有一則
 * 訊息,而且它一定是最新的那一則。區分只到「可重試」與「須重來」兩類,不為七種
 * 類別碼各做一套 UI;類別碼只影響**說法**(忙碌要能與真正的錯誤分開,7.2),
 * 不影響版面。
 *
 * ## 本檔目前不含的部分
 *
 * 版面屬 `style.css`(tasks 5.1)。
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

/**
 * 走子途中失敗的**成因**,依類別碼決定(requirements 7.1、7.2)。
 *
 * 這裡的每一句都是本檔自己寫的,與後端回的 `message` 無關 —— 呈現層拿得到的
 * 只有一個碼(requirements 7.5)。列出來的碼只有一個作用:讓使用者分得出這次
 * 是哪一種情況,尤其是**服務忙碌必須與真正的錯誤區分**(requirements 7.2)。
 * 沒列到的碼一律走通用說法,而不是各自長出一套 UI。
 */
const FAILURE_CAUSES = Object.freeze({
  SERVICE_BUSY: '服務忙碌中。',
  // 前端自己的逾時上界到了,與後端回報引擎搜尋逾時,對使用者是同一件事:等太久了。
  TIMEOUT: '等候回應逾時。',
  ENGINE_TIMEOUT: '等候回應逾時。',
  NETWORK: '連線失敗。',
  ILLEGAL_MOVE_SEQUENCE: '走法序列與後端的局面對不上。',
  WRONG_SIDE_TO_MOVE: '走法序列與後端的局面對不上。',
});

/** 認不得的碼(含 `UNKNOWN` 這種無法辨識的回應形狀)共用的說法。 */
const GENERIC_FAILURE_CAUSE = '發生了預期外的問題。';

/**
 * 失敗之後使用者能做的事 —— **只有兩類**,對應 `game.js` 的 `FailureKind`。
 *
 * 光說「出錯了」不算告知(requirements 7.1):使用者要知道下一步能做什麼。而那
 * 只有兩種答案 —— 再試一次同一手,或整局重來(requirements 7.3)。
 */
const RECOVERY = Object.freeze({
  retry: '這一手沒有送出,請稍後再試一次。',
  reset: '對局狀態已失效,請按「重來」重新開始。',
});

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

/**
 * 題庫列表的位址(problem-browser 的 requirements 4.3)。
 *
 * **相對位址,不是 `/index.html`。** 服務今天掛在網域根目錄,兩者解析出來一模
 * 一樣;一旦部署到子路徑底下,絕對路徑會把使用者丟出整個應用。列表那一側指回
 * 對局頁的連結(`web/list.js` 的 `PLAY_PAGE`)基於同一個理由也是相對的。
 */
const LIST_PAGE = './index.html';

/** 返回途徑的文字。使用者可見文字一律繁體中文(requirements 8.3)。 */
const BACK_TO_LIST_TEXT = '返回題庫列表';

/** 描述那一行的名目。出處那一行的名目寫在骨架裡,這一行的則由本檔生出來。 */
const DESCRIPTION_LABEL = '描述:';

/**
 * 三態諮詢信號的說法(requirements 4.1)。
 *
 * 三種狀態是**相對使用者**的,而後端給的 `red_winning` / `black_winning` 是相對
 * 顏色的 —— 中間隔著「使用者執哪一方」。照顏色直譯在今天的題庫(一律執紅)看不
 * 出差別,但那是巧合而非契約:題目自己帶著 `side_to_move`,換成執黑的題目時直譯
 * 會把即將取勝說成即將落敗。
 */
const SIGNAL_LABELS = Object.freeze({
  winning: '即將取勝',
  losing: '即將落敗',
  unknown: '勝負未知',
});

/** 後端的信號值對應到哪一方即將取勝;認不得的值一律當作未知。 */
const SIGNAL_WINNERS = Object.freeze({ red_winning: 'red', black_winning: 'black' });

/** 還沒有任何一手應手時的信號 —— 這不是「未知」,是根本還沒問過。 */
const NO_SIGNAL_YET = '尚未取得';

/**
 * 信號區的前綴與註記,合起來讓使用者辨識它是**參考資訊而非勝負判決**
 * (requirements 4.4)。
 *
 * 沒有這層框定的話,「即將取勝」看起來就是系統宣告勝負,而對局其實還在繼續 ——
 * 終局只由後端的結束旗標決定,信號連碰都碰不到它(requirements 3.3、4.3)。
 */
const SIGNAL_PREFIX = '參考信號';
const SIGNAL_NOTE = '僅供參考,不是勝負判決;對局只在真終局結束。';

/** 等待中要說的話 —— 載入題目與等應手是兩件事,不能都說成「引擎思考中」。 */
const WAITING_TEXTS = Object.freeze({
  load: '正在載入題目…',
  play: '引擎思考中…',
});

const elements = {
  board: document.getElementById('board'),
  sidebar: document.getElementById('sidebar'),
  title: document.getElementById('puzzle-title'),
  source: document.getElementById('puzzle-source'),
  maxDtm: document.getElementById('puzzle-max-dtm'),
  turn: document.getElementById('turn'),
  signal: document.getElementById('signal'),
  waiting: document.getElementById('waiting'),
  error: document.getElementById('error'),
  moves: document.getElementById('moves'),
  reset: document.getElementById('reset'),
};

/**
 * 返回列表的途徑(problem-browser 的 requirements 4.3)。
 *
 * **用真的 `<a href>`**,與列表那一側進來的路徑同一種形態:中鍵開新分頁、右鍵
 * 複製網址、Enter 鍵、瀏覽器的上一頁全部隨之而來,攔 click 再改 `location` 則要
 * 一一重做,而通常不會做。
 *
 * 它在**模組載入時**就進 DOM,不隨對局狀態變動 —— 題目載不起來的畫面上尤其需要
 * 一條出路:那裡原本只有「重來」一顆按鈕,而它在沒有題目時做的是重試載入,題目
 * 根本不存在的話按到天亮也出不去(requirements 7.4 禁止的無法復原畫面)。
 */
function mountBackLink() {
  const link = document.createElement('a');
  link.id = 'back-to-list';
  link.className = 'back-to-list';
  link.href = LIST_PAGE;
  link.textContent = BACK_TO_LIST_TEXT;
  elements.sidebar.prepend(link);
  return link;
}

/**
 * 題目描述的填入處(problem-browser 的 requirements 4.5)。
 *
 * 描述與出處**已自列表移到這裡**(problem-browser 的 1.2):列是掃視用的,每列
 * 擠進越多欄位越難掃,而使用者選定一題之後才真正需要讀這兩項。出處的容器骨架裡
 * 本來就有(`#puzzle-source`),描述沒有,故由本檔補上。
 *
 * 結構刻意與骨架裡出處那一行一致 —— `<p>名目:<span>值</span></p>` 落在
 * `#puzzle-info` 內,`style.css` 既有的 `#puzzle-info p` / `#puzzle-info span`
 * 兩條規則因此原樣適用,不必動樣式表(它屬 web-play-runtime,不在本任務的
 * boundary 內)。位置在局名之後、出處之前:描述是局名的展開,兩者要相鄰。
 */
function mountDescription() {
  const line = document.createElement('p');
  line.className = 'puzzle-description';
  const value = document.createElement('span');
  value.id = 'puzzle-description';
  value.textContent = BLANK;
  line.append(DESCRIPTION_LABEL, value);
  elements.title.after(line);
  return value;
}

// 兩個節點都由本檔動態建出而不是寫進 `play.html` —— 那個檔屬 web-play-runtime,
// 不在 problem-browser 的 boundary 內。代價只有上面那幾行。
//
// 返回的連結進 DOM 之後就不再需要參照:它是一條靜態連結,不隨對局狀態變動。
// 描述則相反,`renderPuzzleInfo` 每次重畫都要寫它,故留在 `elements` 裡。
mountBackLink();
elements.description = mountDescription();

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
 *
 * 描述來自 problem-browser 的 requirements 4.5,與出處同樣**取自
 * `GET /api/positions/{id}` 的回應**(`service/models.py` 的 `PositionResponse`
 * 兩個欄位都有),不另打 `/api/catalog`:為一個字串多一次往返之外,兩個端點對
 * 同一題給出不同內容時,列表與對局介面會各說各話。
 */
function renderPuzzleInfo(state) {
  if (!state.position) {
    elements.title.textContent = noPuzzleTitle(state);
    elements.description.textContent = BLANK;
    elements.source.textContent = BLANK;
    elements.maxDtm.textContent = BLANK;
    return;
  }
  const { title, description, source, max_dtm: maxDtm } = state.position;
  elements.title.textContent = title || '(未命名)';
  elements.description.textContent = description || BLANK;
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
 * 三態諮詢信號的讀數(requirements 4.1、4.2)。
 *
 * **殺著倒數一律以 `!= null` 判斷。** `mate_in` 可能是 `0` —— 那正是每一題排局的
 * 最後一手(這一手就將死對方),而 JS 的 `if (mateIn)`、`mateIn || '—'` 對 0 都是
 * 假,倒數會在最關鍵的那一手被靜默吞掉。
 *
 * 倒數寫成「約 N 步」而非確數:後端在 250k 節點下可能高估 1 步,寫成確數等於把一個
 * 刻意接受的誤差說成精確值。
 */
function signalReading(entry, userSide) {
  if (!entry) return NO_SIGNAL_YET;
  const winner = SIGNAL_WINNERS[entry.signal] ?? null;
  const label =
    winner == null
      ? SIGNAL_LABELS.unknown
      : winner === userSide
        ? SIGNAL_LABELS.winning
        : SIGNAL_LABELS.losing;
  return entry.mateIn != null ? `${label}(約 ${entry.mateIn} 步)` : label;
}

/**
 * 三態信號(requirements 4.1、4.2、4.4)。
 *
 * 回饋是**一份來源清單**(`game.js` 的 `feedbackSources`),信號只是其中一個來源 ——
 * 因此這裡是挑出信號那一則,而不是假設清單裡只有它。日後判定表加進來時本函式不必改。
 *
 * **信號與對手著法是各自獨立的欄位**:對手著法為空(排局的最後一手)時信號仍可能
 * 有值,兩者分開呈現。把「無應手」實作成整份回應為空,就會在最後一手把信號弄丟。
 */
function renderSignal(state) {
  const entry = state.feedback.find((item) => item.source === 'signal') ?? null;

  const reading = document.createElement('p');
  reading.className = 'signal-reading';
  reading.textContent = `${SIGNAL_PREFIX}:${signalReading(entry, state.userSide)}`;

  const note = document.createElement('p');
  note.className = 'signal-note';
  note.textContent = SIGNAL_NOTE;

  elements.signal.replaceChildren(reading, note);
}

/**
 * 等待狀態(requirements 6.1、6.4)。
 *
 * 「解除」在此**只是快照的 `waiting` 為假**,沒有第二個地方記著它 —— 成功、失敗、
 * 重來三條路徑因此不必各自記得要收起這一區,requirements 6.4 也就不會依賴任何一條
 * 路徑有沒有寫全。
 */
function renderWaiting(state) {
  elements.waiting.textContent = state.position ? WAITING_TEXTS.play : WAITING_TEXTS.load;
  elements.waiting.hidden = !state.waiting;
}

/**
 * 失敗的說法。載入失敗與走子途中的失敗共用同一個區塊,但說的不是同一件事。
 *
 * 還沒有題目時(requirements 1.4)不談「這一手」—— 使用者根本還沒走;有題目之後
 * 的失敗才需要告訴他那一手怎麼了,以及接下來能做什麼(requirements 7.1、7.3)。
 */
function failureMessage(state) {
  const { code, kind } = state.error;
  if (state.position == null) return loadFailureMessage(code);
  return `${FAILURE_CAUSES[code] ?? GENERIC_FAILURE_CAUSE}${RECOVERY[kind] ?? RECOVERY.retry}`;
}

/**
 * 失敗的告知 —— **全部的失敗只有這一個區塊**(requirements 1.4、7.1、7.2、7.3)。
 *
 * 載入失敗與走子途中的失敗刻意不分開寫:兩條路徑各自控制同一個節點的話,後寫的
 * 那條會蓋掉前一條的訊息,而畫面上留著的是哪一則取決於呼叫順序。共用一條路徑則
 * 保證任一時刻只有一則訊息,且它一定是快照裡最新的那一個失敗。
 *
 * 沒有失敗時一律收起來 —— 重試成功後,上一次的訊息不能留在畫面上。
 */
function renderFailure(state) {
  const failed = state.error != null;
  elements.error.textContent = failed ? failureMessage(state) : '';
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
  renderSignal(state);
  renderWaiting(state);
  renderFailure(state);
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
