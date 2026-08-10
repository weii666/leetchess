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
import { loadCatalog } from './catalog.js';
import { readDifficulty } from './difficulty.js';
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

/**
 * 對局介面自己的位址,「下一題」就指向它(problem-browser 的跨題導航)。
 *
 * **相對位址**,理由同 `LIST_PAGE`:服務今天掛在網域根目錄,`/play.html` 與
 * `./play.html` 解析出來一模一樣,但部署到子路徑底下時絕對路徑會把使用者丟出
 * 整個應用。`web/list.js` 的 `PLAY_PAGE` 是同一個字串、同一個理由。
 */
const PLAY_PAGE = './play.html';

/** 通往某一題對局介面的位址。題號經 `?id=` 交接(problem-browser 3.1 定案的契約)。 */
const playHref = (id) => `${PLAY_PAGE}?id=${encodeURIComponent(id)}`;

/** 前進到下一題的文字。 */
const NEXT_POSITION_TEXT = '下一題';

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

/**
 * **對局結束後**的說法(requirements 4.2)—— 陳述已經發生的事,不再是預測。
 *
 * 形態取自 `poc/index.html` 的 `finish()`。用詞是**顏色絕對**的(不是「你贏了」),
 * 與進行中那三個相對使用者的標籤刻意不同:這一句講的是盤面上發生了什麼事,而
 * 「該不該高興」由 `#turn` 的「你獲勝」/「黑方勝」承擔,不必在側欄裡說兩遍。
 */
const MATE_OUTCOMES = Object.freeze({
  red: '已將死黑方',
  black: '紅方被將死',
});

/**
 * 對局結束、但**信號不足以說出「將死」**時的說法。
 *
 * 象棋規則下輪方無合法著法即負 —— 那可能是將死,也可能是困斃(無子可動)。
 * 後端的 `state` 只回報**誰贏**,沒有回報**為什麼贏**,而 steering 的不可違反約束
 * 之一是前端不自實作勝負判定:從「紅方贏了」推出「所以是將死」正是那種推斷。
 *
 * 唯一說得出「將死」二字的依據是**引擎自己報了 mate**:`service/game.py` 的
 * `classify_score()` 只在分數型別為 `mate` 時才給 `red_winning` / `black_winning`,
 * `cp` 與無分數一律歸 `unknown`(連正負號都不看)。因此「信號是這兩者之一」等價於
 * 「引擎回報了 mate」—— 那是引擎給的資訊,不是我們推的。信號為未知或根本沒有信號
 * (引擎一分未報)時,能誠實說的就只有對局結束了。
 */
const GAME_OVER_READING = '對局結束';

/**
 * 還沒有任何一手應手時的信號。
 *
 * **刻意與 `SIGNAL_LABELS.unknown` 是同一個字串**,而且是同一個值而非兩個相同的
 * 字面 —— 兩者在程式上確實是兩種狀態(那個是引擎答了但沒給 mate,這個是還沒問過),
 * 但對使用者都是「現在沒有讀數」,沒有理由用兩種說法。寫成兩個字面的話,改其中
 * 一個就會讓畫面上出現兩句意思相同、字卻差一點的話,那讀起來像錯字。
 *
 * ## 為什麼是「未知」而不是「未明」或「難料」
 *
 * 三者都比原本的「尚未取得」好 —— 那句講的是**取資料**這件事(取得了沒?),是給
 * 工程師看請求狀態的話,不是給使用者看盤面的話。但三者不等價:
 *
 * - 「難料」說的是這個局面**不好判斷**;
 * - 「未明」說的是勝負**還沒分出來**;
 * - 「未知」說的是**我們不知道**。
 *
 * 前兩者都在陳述局面的性質,而排局的勝負**早就是定的** —— 一題 mate in 16 的殺局,
 * 紅方的勝負在出題時就已經確定,只是我們還沒算。對這樣的局面說「勝負未明」或
 * 「難料」是不實的;只有「未知」講的是我們這一端的無知,那才永遠為真。
 *
 * 同理**不能寫成「各有千秋」「勢均力敵」**:那是斷言雙方相當,而這裡連問都還沒問過。
 * steering 的第二個不可動搖約束要求信號寧可誠實地說不知道,也不得從 `cp` 推斷勝負
 * 傾向;無中生有地說「相當」比據 `cp` 推斷走得更遠。
 */
const NO_SIGNAL_YET = SIGNAL_LABELS.unknown;

/**
 * 信號區的註記,讓使用者辨識它是**參考資訊而非勝負判決**(requirements 4.4)。
 *
 * 沒有這層框定的話,「即將取勝」看起來就是系統宣告勝負,而對局其實還在繼續 ——
 * 終局只由後端的結束旗標決定,信號連碰都碰不到它(requirements 3.3、4.3)。
 *
 * 這裡曾有一個 `參考信號:` 前綴,以及一句常駐註記(先是「僅供參考,不是勝負判決;
 * 對局只在真終局結束。」,後縮為「僅供參考」)。**兩者都已移除** —— 舊的
 * requirement 4.4「使信號的呈現讓使用者能辨識它是參考資訊而非勝負判決」已刪,
 * 其約束併入 4.2,因為**讀數本身就已經滿足它**:「即將」是未然語氣、「約」明說了
 * 不精確、「N 步」講明還要走多久。註記等於在一句自明的話後面再補一句註腳。
 *
 * 但語氣是載重的,不是修辭:把「即將取勝　約 15 步」寫成「紅勝 15」就真的變成判決。
 * 那條約束現在記在 requirement 4.2,並由 `test_the_mate_countdown_is_shown_as_an_approximation`
 * 釘住整句讀數(而非只驗子字串)。
 *
 * **未然語氣的適用範圍是「對局進行中」**(requirement 4.2 修訂後)。對局一旦結束就
 * 沒有東西好預測了,「即將取勝　約 0 步」在那裡是自相矛盾:說「即將」卻已經結束,
 * 說「約 0 步」卻是在倒數一件已經發生的事。終局的說法見 `outcomeReading()`。
 *
 * `#signal` 的 `role="note"`(而非 `role="status"`)**仍然保留且不得更動**:它與已刪的
 * 4.4 是分開的價值 —— `status` 會讓螢幕閱讀器把信號當系統狀態**主動播報**,那在聽覺上
 * 像判決,與文字寫得多清楚無關。依據已改掛 requirement 4.1。
 */

/*
 * 這裡曾有一組 `WAITING_TEXTS`(`載入中` 與 `引擎思考中…`),寫進一個專用的
 * `#waiting` 區塊。**兩者都已移除**(使用者看過實際畫面後的決定)。
 *
 * 那個區塊在版面流裡來去,顯示與隱藏都把底下的「歷史著法」上下推動 —— 每走一手
 * 就抖一次。等待的兩種情境現在各由**已經存在、位置固定**的元素承擔:
 *
 * - **等應手** -> `#turn` 顯示「黑方走棋」(`turnText`)。它只換字不換高度。
 * - **載入題目** -> `#puzzle-title` 顯示「載入中…」(`noPuzzleTitle`),盤面位置
 *   放「正在載入題目…」(`boardPlaceholderText`)。這兩處在改動之前就已經這樣做了。
 *
 * 誠實記錄代價:等應手期間的回饋**從一個專用區塊降級成輪方那一行的文字**。
 * requirement 6.1 已隨之修訂;6.2 與 6.4 不受影響 —— 它們是行為約束,由
 * `game.js` 的 `acceptsMoves` 與快照的 `waiting` 承擔,與畫不畫這個區塊無關。
 */

const elements = {
  board: document.getElementById('board'),
  sidebar: document.getElementById('sidebar'),
  title: document.getElementById('puzzle-title'),
  source: document.getElementById('puzzle-source'),
  difficulty: document.getElementById('puzzle-difficulty'),
  tags: document.getElementById('puzzle-tags'),
  toggleTags: document.getElementById('toggle-tags'),
  turn: document.getElementById('turn'),
  signal: document.getElementById('signal'),
  error: document.getElementById('error'),
  moves: document.getElementById('moves'),
  controls: document.getElementById('controls'),
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

// 返回的連結由本檔動態建出而不是寫進 `play.html`,代價只有上面那幾行。進 DOM 之後
// 就不再需要參照:它是一條靜態連結,不隨對局狀態變動。
//
// 這裡原本還掛著一個 `#puzzle-description`(problem-browser 的 4.2)。**已移除** ——
// 真實題庫的 `description` 就是「出處 + 局號 + 局名」的串接,與 `#puzzle-title` 和
// `#puzzle-source` 完全重複,側欄因此把同樣的字說了三遍。使用者看過實際畫面後決定
// 拿掉,requirements 4.5 已隨之修訂。
mountBackLink();

/**
 * 前往下一題的途徑,排在「重來」右邊(problem-browser 的跨題導航)。
 *
 * **與返回連結同樣是真的 `<a href>`**(problem-browser 4.1 已確立這個形態):中鍵
 * 開新分頁、右鍵複製網址、Enter 鍵全部免費附帶,攔 click 再改 `location` 要一一
 * 重做而通常不會做。
 *
 * 節點在**模組載入時**就建好但預設隱藏,不是等到獲勝才動態插入 —— 顯示與否因此
 * 只是 `render()` 依快照決定的一個 `hidden`,與畫面上其他每一件事走同一條路徑
 * (本檔開頭的「只有一條寫進畫面的路徑」)。
 */
function mountNextLink() {
  const link = document.createElement('a');
  link.id = 'next-position';
  link.className = 'next-position';
  link.textContent = NEXT_POSITION_TEXT;
  link.hidden = true;
  elements.controls.append(link);
  return link;
}

const nextLink = mountNextLink();

/**
 * 選中的格,例如 `'d8'`;沒有選子時為 `null`。
 *
 * 呈現層只有兩個這樣的狀態,另一個是 `tagsRevealed`(標籤展開與否)。兩者的共同點
 * 是**與對局的進度無關**:走子、重來、獲勝都不改變它們,故不進 `game`。任何與進度
 * 有關的東西都不得在這裡多開一份。
 */
let selected = null;

/**
 * 下一題的題號;還沒查、查不到、或這已經是最後一題時為 `null`。
 *
 * **`null` 一律代表「不顯示按鈕」**,三種來源共用同一個值是刻意的:對使用者而言
 * 「已是最後一題」與「索引取不到」都是同一件事 —— 這裡沒有下一題可去。為索引失敗
 * 另做一則提示,等於讓一個次要功能在終局畫面上喊自己壞了。
 */
let nextPositionId = null;

/** 索引是否已經查過(不論成敗)。查一次就夠 —— 索引不會在一局之內變。 */
let nextLookupDone = false;

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
 * 側欄標題的字面:「題號 + 點 + 空格 + 局名」,例如 `21. 盡善克終`。
 *
 * 與列表同一個形態(`web/list.js` 的 `${position.id}.` 加局名),使用者從列表點進來
 * 看到的是同一個字串,一眼就確認自己開對了題。**點是分隔符而不是贅字。**
 *
 * 列表把題號與局名切成兩欄(那裡要讓一整排的點對齊成直線),這裡只有一行,故直接
 * 串成一個字串,不另立元素 —— `#puzzle-title` 是頁面唯一的 `<h1>`,拆成兩個節點
 * 只會讓螢幕閱讀器把標題唸成兩段。
 *
 * 題號缺漏時只給局名,不畫出「undefined.」或一個孤零零的點。實務上不會發生
 * (`GET /api/positions/{id}` 一定帶 `id`),但標題是這一頁最顯眼的一行,寧可少一
 * 個題號也不要在上面印出一個壞掉的字串。
 */
function puzzleHeading(id, title) {
  const name = title || '(未命名)';
  return id != null ? `${id}. ${name}` : name;
}

/**
 * 側欄的題目資訊(requirements 1.2)。**現在只有局名與出處。**
 *
 * `state.position.max_dtm` 刻意**不取用**。它在回應裡照常存在(後端一個字沒改,
 * `service/models.py` 的 `PositionResponse` 仍回傳它,corpus-verification 也還要
 * 回填它),但**對使用者是劇透** —— 顯示「最長殺著 16」等於預告這題幾步殺得完,
 * 而排局練習的價值正在於自己找出殺法。requirement 1.3 已隨之推翻。
 *
 * `state.position.description` 同樣不取用,理由不同:真實題庫的描述是「出處 +
 * 局號 + 局名」的串接,畫出來就是把上面兩行再說一次(problem-browser 4.5)。
 */
function renderPuzzleInfo(state) {
  if (!state.position) {
    elements.title.textContent = noPuzzleTitle(state);
    elements.source.textContent = BLANK;
    // 整格收起來,不填佔位符號:側欄是直向堆疊的,一個只有「—」的空行只會讓下面的
    // 東西無故往下掉一格。按鈕同理 —— 沒有題目就沒有標籤可展開。
    hideLine(elements.difficulty);
    hideLine(elements.tags);
    elements.toggleTags.hidden = true;
    return;
  }
  const { id, title, source, difficulty, tags } = state.position;
  elements.title.textContent = puzzleHeading(id, title);
  elements.source.textContent = source || BLANK;
  renderDifficulty(difficulty);
  renderTags(tags);
}

/** 清空一格題目資訊並收起來。 */
function hideLine(line) {
  line.replaceChildren();
  line.hidden = true;
}

/**
 * 標籤此刻是否展開。**呈現層的狀態,不進 `game`** —— 它與對局的進度無關,重來、
 * 走子、獲勝都不該改變它。
 *
 * 不做持久化(不存 localStorage)是刻意的:標籤是**提示**而不是偏好設定,上一題按過
 * 展開,不該讓下一題一開啟就洩題。每一次載入頁面都從隱藏開始。理由見 `play.html`
 * 那段註解 —— 殺法名等於這一題的解法。
 */
let tagsRevealed = false;

/** 展開與收合的說法 —— 按鈕上寫的是**按下去會發生什麼**,不是目前的狀態。 */
const TAGS_TOGGLE_LABELS = Object.freeze({
  show: '顯示標籤',
  hide: '隱藏標籤',
});

/**
 * 難度那一行(與列表同一組說法與顏色)。
 *
 * `data-level` 是上色的掛勾,**本檔不碰顏色** —— 與列表同樣的分工(`web/list.js`
 * 的 `difficultyCell`),呈現層裡的顏色屬樣式表。認不得的值(0、4、`null`、欄位
 * 不存在)一律原樣顯示、不上 `data-level`,理由見 `difficulty.js`。
 *
 * 值不存在時整行收起來,而不是顯示佔位符號:列表上難度是固定的一欄,空了會讓那一列
 * 看起來少一項,故留佔位;側欄沒有欄的概念,收起來就好。
 */
function renderDifficulty(value) {
  const line = elements.difficulty;
  if (value == null) {
    hideLine(line);
    return;
  }
  const { text, level } = readDifficulty(value, BLANK);
  if (level !== null) {
    line.dataset.level = level;
  } else {
    delete line.dataset.level;
  }
  line.textContent = text;
  line.hidden = false;
}

/**
 * 標籤與它的展開按鈕 —— 殺法名(「臥槽馬」「雙馬飲泉」),這個服務最獨特的部分,
 * 同時也是這一題的**劇透**,故預設收著(見 `play.html`)。
 *
 * chip 的外觀與列表一致(`--tag-bg`、`--chip-font-size` 是兩頁共用的自訂屬性),
 * 但 class 名不同:列表的 `.position-tag` 是列的一部分,這裡是側欄的一格,兩份
 * 樣式表各自持有自己的規則。
 *
 * ## 沒有標籤時連按鈕都收起來
 *
 * 按下去什麼都不會發生的按鈕比沒有按鈕更難解釋。列表在那種情形下留一個「—」,因為
 * 那一欄空掉會讓整列看起來少一項;側欄沒有這個問題。
 *
 * ## `aria-expanded` 不是裝飾
 *
 * 按鈕的字面(「顯示標籤」/「隱藏標籤」)說的是**按下去會發生什麼**,而
 * `aria-expanded` 說的是**目前的狀態**。兩者方向相反且都必要:只有字面的話,螢幕
 * 閱讀器的使用者聽到「隱藏標籤」無從得知標籤此刻是不是已經展開了。
 */
function renderTags(tags) {
  const line = elements.tags;
  const button = elements.toggleTags;
  const labels = Array.isArray(tags) ? tags : [];

  if (labels.length === 0) {
    hideLine(line);
    button.hidden = true;
    return;
  }

  button.hidden = false;
  button.textContent = tagsRevealed
    ? TAGS_TOGGLE_LABELS.hide
    : TAGS_TOGGLE_LABELS.show;
  button.setAttribute('aria-expanded', String(tagsRevealed));

  if (!tagsRevealed) {
    hideLine(line);
    return;
  }

  line.replaceChildren(
    ...labels.map((label) => {
      const chip = document.createElement('span');
      chip.className = 'puzzle-tag';
      chip.textContent = label;
      return chip;
    }),
  );
  line.hidden = false;
}

/**
 * 索引中**題號大於 `currentId` 的最小題號**;沒有這樣一題時為 `null`。
 *
 * **絕對不是 `currentId + 1`。** 本專案的題號有缺口:題庫按局號收題,收到哪一局
 * 就是哪一局,中間跳號是常態(收第二本書後更明顯),而 `PositionRepository.all()`
 * 的存在正是因為「從 1 逐一探測」會在缺口處靜默截斷。加一算出來的題號多半根本不
 * 存在,使用者按下去只會看到「找不到這一題」。
 *
 * 清單來自 `loadCatalog()`,那份清單即索引的全部 —— 本檔**不對它再加任何過濾**,
 * 呈現層立第二個判準,兩個判準遲早會漂移(`web/list.js` 寫著同一件事)。
 *
 * 取最小值而不是取「陣列中的下一個」:索引今天依題號遞增,但那是後端的排序決定,
 * 不是本檔該依賴的東西。
 */
function nextPositionAfter(positions, currentId) {
  let best = null;
  for (const position of positions) {
    const id = position?.id;
    if (typeof id !== 'number' || !Number.isFinite(id)) continue;
    if (id <= currentId) continue;
    if (best === null || id < best) best = id;
  }
  return best;
}

/**
 * 查出下一題是哪一題 —— **只在使用者獲勝之後才跑,而且只跑一次**。
 *
 * 不在載入題目時就查:那會讓每一次開對局頁都多打一次 `/api/catalog`,而絕大多數
 * 對局根本走不到獲勝。終局時才查是安全的 —— 索引端點不碰引擎池(problem-browser
 * 的 1.1),此刻去要它不會跟任何人搶引擎。
 *
 * **失敗一律靜默**(`catch` 之後什麼都不做,`nextPositionId` 留在 `null`):跨題
 * 導航是次要功能,它壞掉不該讓使用者連「你獲勝」都看不到。`catalog.js` 本來就只
 * 給得出一個類別碼,這裡連那個碼都不需要 —— 三種失敗的後果都是同一個。
 */
async function lookUpNextPosition() {
  if (nextLookupDone) return;
  nextLookupDone = true;

  let positions;
  try {
    ({ positions } = await loadCatalog());
  } catch {
    return; // 索引取不到:按鈕不出現,終局畫面的其餘部分一個字都不受影響。
  }

  const current = game.getState().position?.id;
  if (typeof current !== 'number') return;
  nextPositionId = nextPositionAfter(positions, current);
  render();
}

/**
 * 前往下一題的途徑(problem-browser 的跨題導航)。
 *
 * **三個條件缺一不可**:對局結束、獲勝的是使用者、而且真的查得到下一題。
 *
 * 走脫導致黑勝時不出現是刻意的 —— 那時使用者該做的是「重來」把這一題走對,而不是
 * 跳過它。而查不到下一題(已是最後一題,或索引根本取不到)時同樣不出現:一顆按了
 * 沒反應的按鈕比沒有按鈕更糟。
 *
 * 隱藏時把 `href` 一併拿掉,而不是只設 `hidden` —— 這樣它在任何狀態下都不會是一條
 * 「指向 `undefined`」的連結。
 */
function renderNextPosition(state) {
  const available = state.over === true && state.userWon === true && nextPositionId != null;
  if (available) nextLink.href = playHref(nextPositionId);
  else nextLink.removeAttribute('href');
  nextLink.hidden = !available;
}

/**
 * 當前輪方(requirements 8.4);對局結束後改為呈現勝方(requirements 3.2)。
 *
 * 勝方**一律照後端回報** —— `userWon` 也是狀態機依後端的勝方推導的,這裡不自己
 * 判斷誰贏。
 *
 * **「標籤:值」的形態已整條拿掉**(使用者看過實際畫面後的決定):這一格永遠只放
 * 這一件事,`輪方:` 與 `對局結束:` 兩個名目每次重畫都把同一句廢話再說一遍,而
 * 「輪到你」「黑方走棋」「你獲勝」本身就已經是完整的句子。8.4 要的是輪方**可辨識**
 * 而不是有個名目在旁邊:少了前綴反而更短、更快讀得出來。形態取自 `poc/index.html`
 * 的 signal 區塊 —— 那裡直接寫「勝負難料」,沒有任何前綴。
 *
 * ## 這一行同時承擔等待中的呈現(requirements 6.1 修訂後)
 *
 * 等應手的期間走法序列裡已經有使用者那半手(`game.js` 的 `play()` 在第一個 `await`
 * 之前就同步推進並 `notify()`),輪方因此**在點擊當下就同步**翻成對手,這裡於是說
 * 「黑方走棋」—— 那句話本身就是「現在不是你動」。原本那個 `#waiting` 區塊講的是
 * 同一件事,而它在版面流裡來去會推動底下的歷史著法,故整條移除。
 *
 * 一併記下這一行對測試的意義:**「不是『(某方)走棋』」就是「這次請求已經落地」**。
 * 成功、失敗、終局三條路徑的結果分別是「輪到你」、「輪到你」(序列整份退回)與
 * 「你獲勝」/「黑方勝」/「對局結束」,沒有一種留在「走棋」上。反過來,只要
 * `waiting` 為真就必然不是使用者的回合 —— `waiting` 只在 `play()`(序列已含使用者
 * 那半手,輪方已翻成對手)與 `load()`(此時根本還沒有題目,這裡顯示佔位符號)
 * 之中為真。
 */
function turnText(state) {
  if (!state.position) return BLANK;
  if (state.over) {
    if (!state.winner) return '對局結束';
    if (state.userWon) return '你獲勝';
    return `${SIDE_NAMES[state.winner] ?? state.winner}勝`;
  }
  if (state.turn === state.userSide) return '輪到你';
  return `${SIDE_NAMES[state.turn] ?? state.turn}走棋`;
}

/**
 * **對局進行中**的三態諮詢信號讀數(requirements 4.1、4.2)。
 *
 * 這裡的每一句都是**預測**:「即將」是未然語氣,倒數說的是還要走多久。對局結束
 * 之後就沒有東西好預測了,那條路徑走的是 `outcomeReading()`。
 *
 * **殺著倒數一律以 `!= null` 判斷。** `mate_in` 可能是 `0`,而 JS 的 `if (mateIn)`、
 * `mateIn || '—'` 對 0 都是假,倒數會被靜默吞掉。這個陷阱**沒有因為終局改成陳述句
 * 而消失,只是換了位置**:後端給倒數的條件是「引擎回報 mate」,不是「對局還沒
 * 結束」,兩者不必同步 —— `over` 為假而 `mate_in` 為 0 的回應照樣要正常畫出倒數,
 * 由 `test_a_mate_countdown_of_zero_is_still_shown_while_the_game_goes_on` 釘住。
 *
 * 倒數寫成「約 N 步」而非確數:後端在 250k 節點下可能高估 1 步,寫成確數等於把一個
 * 刻意接受的誤差說成精確值。
 *
 * 倒數與讀數之間以**全形空格**分隔而非括號(形態取自 `poc/index.html` 的
 * `renderSignal`,那裡寫的是「紅勝　N 回殺」)。括號在中文裡讀起來像補述,而倒數
 * 是這一行最要看的數字;拿掉括號之後這一行少兩個字,數字也站得更前面。
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
  return entry.mateIn != null ? `${label}　約 ${entry.mateIn} 步` : label;
}

/**
 * **對局結束後**的讀數(requirements 4.2)—— 陳述結果,不再預測。
 *
 * 「即將取勝　約 0 步」在終局是兩個錯誤:對局已經結束了還說「即將」,而「約 0 步」
 * 是在倒數一件已經發生的事。
 *
 * ## 兩份輸入各管一件事,誰都不越界
 *
 * - **誰贏了** 只看 `state.winner` —— 後端 `state` 的欄位,唯一權威。`#turn` 讀的
 *   是同一個來源,兩行因此不可能互相矛盾。
 * - **能不能說「將死」** 只看信號是不是 mate 信號(見 `GAME_OVER_READING`)。
 *
 * 換句話說,方向來自後端的勝負,而「將死」這個**理由**來自引擎的 mate 回報。兩者
 * 缺一就退回「對局結束」:沒有勝方(後端只說結束)說不出誰將死誰,沒有 mate 信號
 * 則說不出這是將死還是困斃。
 *
 * **倒數在這條路徑上一律不畫**,而且判斷的依據是 `state.over` 而不是 `mateIn` 是否
 * 為 0 —— 用倒數的值決定要不要畫倒數,正是 4.2 那個 falsy 陷阱換個地方重來。
 * 因此 `over` 為真而 `mate_in` 不是 0(例如引擎報 mate 3 的那一手正好終局)時,
 * 這裡照樣只說「已將死黑方」,不會冒出一個「約 3 步」。
 */
function outcomeReading(state, entry) {
  const mateReported = entry != null && SIGNAL_WINNERS[entry.signal] != null;
  if (!mateReported) return GAME_OVER_READING;
  return MATE_OUTCOMES[state.winner] ?? GAME_OVER_READING;
}

/**
 * 三態信號(requirements 4.1、4.2、4.4)。
 *
 * 回饋是**一份來源清單**(`game.js` 的 `feedbackSources`),信號只是其中一個來源 ——
 * 因此這裡是挑出信號那一則,而不是假設清單裡只有它。日後判定表加進來時本函式不必改。
 *
 * **信號與對手著法是各自獨立的欄位**:對手著法為空(排局的最後一手)時信號仍可能
 * 有值,兩者分開呈現。把「無應手」實作成整份回應為空,就會在最後一手把信號弄丟。
 *
 * **`state.over` 是預測與陳述之間唯一的分水嶺**(requirements 4.2)。它與 `game.js`
 * 的終局判定是同一個欄位、同一個來源,所以信號區不會在對局已結束時還說「即將」,
 * 也不會在對局還在進行時就宣告將死。信號本身仍然**碰不到** `over`(4.3):這裡只是
 * 讀它來決定用哪一種語氣。
 */
function renderSignal(state) {
  const entry = state.feedback.find((item) => item.source === 'signal') ?? null;

  const reading = document.createElement('p');
  reading.className = 'signal-reading';
  reading.textContent = state.over
    ? outcomeReading(state, entry)
    : signalReading(entry, state.userSide);

  elements.signal.replaceChildren(reading);
}

/*
 * 這裡曾有一個 `renderWaiting()`,把 `state.waiting` 畫成 `#waiting` 的 `hidden`。
 * **已移除**,理由與那個元素本身相同(見本檔上方的說明)。
 *
 * 「等待態必解除」(requirements 6.4)沒有因此失去依據:**「解除」從頭到尾就只是
 * 快照的 `waiting` 為假**,沒有第二個地方記著它。成功、失敗、重來三條路徑不必各自
 * 記得要收起什麼,而 `waiting` 為假的後果現在直接就是**盤面重新接受走子** ——
 * `game.js` 的 `acceptsMoves` 一翻真,`legalMoves` 就不再是空集合。那才是 6.4 真正
 * 要保護的東西(「不留在等待中無法操作」),比一個區塊收沒收起來更貼近它的字面。
 */

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
  renderFailure(state);
  renderMoves(state);
  renderNextPosition(state);
  renderBoardArea(state);
}

// 對局狀態一變就整份重畫,並清掉選中的格 —— 走完一手之後,原本選中的那枚子已經
// 不在原地;失敗復原與重來則是整份換掉走法序列,選取更是無從對應。
//
// **索引只在使用者獲勝的那一刻去要**(problem-browser 的跨題導航)。放在這裡而不是
// `render()` 裡,是因為發請求是副作用而 `render()` 只該把快照翻成畫面 —— 而且
// `render()` 連選子都會觸發,每選一枚子就打一次索引顯然不對。
game.subscribe(() => {
  selected = null;
  render();
  const state = game.getState();
  if (state.over && state.userWon) lookUpNextPosition();
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

// 展開/收合標籤。**與畫面上其他每一件事走同一條路徑**:改一個狀態,然後整份重畫
// (本檔開頭的「只有一條寫進畫面的路徑」)—— 不在這裡順手動 `hidden` 或改按鈕的字。
//
// 「重來」刻意**不**重置這個狀態:重來是重走這一局,不是收回使用者已經看過的提示。
elements.toggleTags.addEventListener('click', () => {
  tagsRevealed = !tagsRevealed;
  render();
});

render();
game.load();
