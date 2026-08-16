/**
 * 對局狀態機:前端唯一持有對局狀態的地方。
 *
 * 後端不記任何對局進度,因此這裡就是對局的真相所在。而在這份狀態之中,
 * **走法序列又是唯一的真相** —— 盤面、輪方、歷史著法全部由它推導,沒有第二份
 * 可以彼此不同步的副本。
 *
 * 這使兩件事退化成同一個操作 —— 換掉走法序列後重繪:
 *
 * - 重來:序列清空
 * - 失敗復原:序列退回送出前的值
 *
 * 若呈現層各自持有狀態,這兩條路徑會各自需要清理邏輯,而清理不乾淨正是「畫面卡在
 * 不可知狀態」的來源(requirements 7.4)。
 *
 * ## 三條不可動搖的界線
 *
 * 1. **終局只依據回應中的 `state.over`**(3.3、4.3)。信號為即將取勝也好、即將
 *    落敗也好,一律不改變對局是否結束的判定 —— 後端的終局判定裡沒有分數,前端也
 *    不得引入。程式上的保證是:`over` 只有 `advance()` 寫入的後端局面這一個來源,
 *    信號連碰都碰不到它。
 * 2. **不提供任何形式的單手回退**(5.2)。狀態只能整體前進、清空或整體替換。
 *    允許逐步回退等於允許試錯搜尋,而排局的價值在於想清楚再落子(產品決定)。
 *    對外的狀態因此是**凍結的快照**,連 `state.moves.pop()` 都改不動真相。
 * 3. **不自行判斷任何棋規**。可走的著法一律是後端回應原封不動的轉交;此處只檢查
 *    「這一手在不在後端給的清單裡」,不檢查它像不像一手棋。
 *
 * ## 每手棋一次請求
 *
 * 走出一手後**直接呼叫應手端點,不先查詢局面**(3.1)。該回應已涵蓋「紅方這一手
 * 就將死黑方」(對手著法為空、對局結束、勝方為紅),而那是每一題排局的最後一手,
 * 不是邊緣案例。多一次呼叫會讓通過後端併發閘門的次數加倍。
 *
 * ## 依賴方向
 *
 * 只能向左依賴 `api.js` 與 `fen.js`:不碰 DOM、不自己發請求(逾時與錯誤模型只有
 * `api.js` 一份)、不知道 `board.js` 與 `app.js` 的存在。呈現層要什麼,由它自己
 * 從狀態快照裡讀。
 */

import { ApiError, ApiErrorCode, loadPosition, requestBlackMove } from './api.js';
import { applyMove, parseFen } from './fen.js';

/**
 * 失敗之後使用者能做的事,只分兩類(design 的 Error Handling)。
 *
 * 不為每種錯誤各做一套復原路徑:能再試一次的,與試幾次都一樣、只能重來的。
 * 對應到什麼文字是呈現層的事 —— 這裡不產生任何使用者可見的字。
 */
export const FailureKind = Object.freeze({
  /** 再送一次同一手就有機會成功:忙碌、逾時、斷線、後端內部錯誤。 */
  RETRY: 'retry',
  /** 對局狀態已失效,重試同一手只會再失敗一次(requirements 7.3)。 */
  RESET: 'reset',
});

/**
 * 走法序列與後端局面對不起來的兩種類別碼。
 *
 * 這兩種是**對局層面**的失效而非傳輸層面的意外:序列本身已經不被後端接受,
 * 再送一次結果相同,唯一的出路是重來。
 */
const RESET_CODES = new Set([
  ApiErrorCode.ILLEGAL_MOVE_SEQUENCE,
  ApiErrorCode.WRONG_SIDE_TO_MOVE,
]);

/** 輪方的另一方。 */
const OPPONENT = Object.freeze({ red: 'black', black: 'red' });

/**
 * 三態諮詢信號的回饋來源 —— 目前唯一的一個。
 *
 * **`mate_in` 可能為 `0`**(終局那手),因此一律以 `??` 而非 `||` 取值:JS 的
 * `0 || null` 會把倒數靜默吞掉,而那正好發生在最關鍵的最後一手。
 *
 * @param {{reply: object}} context 推進的上下文。
 * @returns {object|null} 一則回饋,或 `null` 表示這次沒有東西可說。
 */
export function signalFeedback({ reply }) {
  if (!reply) return null;
  return {
    source: 'signal',
    signal: reply.signal ?? null,
    mateIn: reply.mate_in ?? null,
  };
}

/**
 * 預設的走脫回饋來源。
 *
 * 回饋是**一份來源清單**而不是寫死的一段程式(requirements 4.5):日後的走脫判定表
 * 是往這份清單裡加一個來源,推進流程一字不改。來源拿到的是整個推進的上下文
 * (應手回應、當下的走法序列、題目),回傳一則回饋或 `null`。
 *
 * **來源影響不了對局是否結束** —— `over` 只由後端回應寫入,回饋只是狀態上的一個
 * 欄位。這個隔離是結構性的,不靠約定(requirements 4.3)。
 */
export const DEFAULT_FEEDBACK_SOURCES = Object.freeze([signalFeedback]);

/**
 * 建立一台對局狀態機。
 *
 * @param {object} options
 * @param {number|string} options.positionId 要下哪一題。**由外部指定** —— 跨題導航
 *   屬 problem-browser,狀態機不決定載入哪一題。
 * @param {Array<(context: object) => object|null>} [options.feedbackSources]
 *   走脫回饋的來源清單,預設只有三態信號。
 * @param {number} [options.timeoutMs] 傳給 `api.js` 的逾時上界,預設用它自己的。
 * @returns {{
 *   getState: () => object,
 *   subscribe: (listener: (state: object) => void) => () => void,
 *   load: () => Promise<boolean>,
 *   play: (uci: string) => Promise<boolean>,
 *   reset: () => void,
 * }} 對外只有這五個操作 —— **沒有退回上一手的入口**(requirements 5.2)。
 */
export function createGame({
  positionId,
  feedbackSources = DEFAULT_FEEDBACK_SOURCES,
  timeoutMs,
} = {}) {
  /** 題目與其起始局面(後端回應原樣)。載入成功前為 `null`。 */
  let position = null;

  /** 起始局面的後端狀態 —— 重來要用,因此與 `current` 分開留著。 */
  let startingState = null;

  /** 當前局面的後端狀態:可走的著法、是否結束、勝方。 */
  let currentState = null;

  /** **唯一的真相**:自起始局面起的完整 UCI 走法序列。 */
  let moves = [];

  /** 最近一次推進的走脫回饋。 */
  let feedback = Object.freeze([]);

  /** 是否正在等待後端回應。 */
  let waiting = false;

  /** 最近一次失敗,`{code, kind}`;沒有失敗時為 `null`。 */
  let failure = null;

  /**
   * 狀態的代次。
   *
   * 重來與重新載入都會整份換掉狀態,而那時可能還有請求在路上。代次一變,回來的
   * 回應就作廢 —— 否則一份在途的應手會把剛清空的走法序列重新填滿,requirements 5.1
   * 在真實網路下就只是偶爾成立。
   */
  let generation = 0;

  const listeners = new Set();

  /** 對外的狀態快照,每次變動後重建。 */
  let snapshot = buildSnapshot();

  const requestOptions = timeoutMs == null ? undefined : { timeoutMs };

  /**
   * 自走法序列推導出當前盤面。
   *
   * 每次都自起始局面重算而非增量更新:增量需要「上一次算到哪」這個第二份狀態,
   * 而它正是重來與失敗復原要清理的東西。重算 20 手的成本是可忽略的。
   */
  function deriveBoard() {
    const board = parseFen(position.fen);
    for (const uci of moves) applyMove(board, uci);
    return board;
  }

  /** 輪到誰走 —— 同樣自走法序列推導:走了偶數手就還輪題目的先行方。 */
  function deriveTurn(userSide) {
    return moves.length % 2 === 0 ? userSide : OPPONENT[userSide];
  }

  /**
   * 黑方剛才那一手 —— 同樣自走法序列推導。`moves` 裡偶數 index 恆為紅方手、奇數
   * index 恆為黑方手(userSide 恆為 'red',見 deriveTurn);黑方應手後陣列長度會
   * 變回偶數,此時最後一手才是黑方走的。長度是奇數代表紅方剛下但黑方尚未回應
   * (或紅方這手直接將死黑方、沒有應手),那格是使用者自己剛走的,不該標成黑方。
   */
  function deriveLastBlackMove() {
    return moves.length > 0 && moves.length % 2 === 0
      ? moves[moves.length - 1]
      : null;
  }

  function buildSnapshot() {
    const userSide = position ? position.side_to_move ?? 'red' : null;
    const turn = position ? deriveTurn(userSide) : null;
    const over = currentState ? currentState.over === true : false;
    const winner = currentState ? currentState.winner ?? null : null;
    // 等待中、非我方回合、對局已結束 —— 三種情況都不接受走子(2.5、3.2、6.2)。
    const acceptsMoves = Boolean(position) && !waiting && !over && turn === userSide;

    return Object.freeze({
      positionId,
      /** 題目資訊(局名、出處、最長殺著距離…)。呈現層自己挑要用哪些欄位。 */
      position,
      /** 唯一真相的唯讀副本。 */
      moves: Object.freeze([...moves]),
      /** 由走法序列推導的盤面;尚未載入題目時為 `null`。 */
      board: position ? deriveBoard() : null,
      /**
       * 黑方剛落子的起訖格,供 `board.js` 畫標示用;沒有黑方剛走的一手時為
       * `null`(尚未開局、或紅方這手已將死黑方)。
       */
      lastBlackMove: deriveLastBlackMove(),
      userSide,
      turn,
      /**
       * 現在可走的著法,**一律取自後端**。
       *
       * 不接受走子的狀態下這裡是**空的** —— `board.js` 不判斷子的歸屬,它的
       * 「可選取」定義就是「該格有著法可出發」,因此空集合是讓盤面整片不可選的
       * 唯一方式(requirements 2.5、6.2)。
       */
      legalMoves: Object.freeze(acceptsMoves ? [...(currentState?.legal_moves ?? [])] : []),
      /** 對局是否結束。**只有後端回應寫得到這個欄位。** */
      over,
      winner,
      /** 使用者是否獲勝;對局未結束時為 `null`(requirements 3.4)。 */
      userWon: over ? winner === userSide : null,
      waiting,
      feedback,
      error: failure,
    });
  }

  /**
   * 重建快照並通知訂閱者。狀態的每一次變動都經過這裡。
   *
   * **每個訂閱者各自被保護起來。** 訂閱者是呈現層,它會碰 DOM —— 一個沒接到的
   * 節點就足以讓它丟出例外。若讓那個例外往上穿,對局狀態會壞在兩種方式:推進到
   * 一半的狀態被當成失敗而只退回一部分,或永遠停在等待中(違反 6.4)。呈現層的
   * 缺陷不得升級成對局狀態的缺陷,使用者才不會卡在誰都救不回來的畫面(7.4)。
   *
   * 例外經 `console.error` 留下痕跡就好 —— 這裡是狀態機,沒有立場決定要怎麼告訴
   * 使用者;安靜吞掉則會讓呈現層的缺陷完全查不到。
   */
  function notify() {
    snapshot = buildSnapshot();
    for (const listener of listeners) {
      try {
        listener(snapshot);
      } catch (error) {
        console.error('訂閱者處理狀態時失敗', error);
      }
    }
  }

  /** 跑過每個回饋來源,收集這次推進的回饋。 */
  function collectFeedback(context) {
    const entries = [];
    for (const source of feedbackSources) {
      const entry = source(context);
      if (entry != null) entries.push(Object.freeze(entry));
    }
    return Object.freeze(entries);
  }

  /**
   * 把任何丟出來的東西收斂成 `{code, kind}`。
   *
   * **只帶類別碼** —— `api.js` 保證後端原文到不了這一層,這裡也不再加上任何文字
   * (requirements 7.5)。非 `ApiError` 的意外(程式缺陷)歸為通用錯誤,總之不能
   * 讓狀態機停在等待中。
   */
  function toFailure(error) {
    const code = error instanceof ApiError ? error.code : ApiErrorCode.UNKNOWN;
    return Object.freeze({
      code,
      kind: RESET_CODES.has(code) ? FailureKind.RESET : FailureKind.RETRY,
    });
  }

  /**
   * 載入題目。
   *
   * 回應已含起始局面的合法著法,因此載入之後不必再查一次局面。
   *
   * @returns {Promise<boolean>} 是否載入成功。失敗時狀態帶著 `error`,再呼叫一次
   *   即為重試(requirements 7.1)。
   */
  async function load() {
    const issued = ++generation;
    waiting = true;
    failure = null;
    notify();

    try {
      const payload = await loadPosition(positionId, requestOptions);
      if (issued !== generation) return false;   // 期間被重來或重新載入,這份作廢
      position = payload;
      startingState = payload.state;
      currentState = payload.state;
      moves = [];
      feedback = Object.freeze([]);
      waiting = false;
      notify();
      return true;
    } catch (error) {
      if (issued !== generation) return false;
      waiting = false;
      failure = toFailure(error);
      notify();
      return false;
    }
  }

  /**
   * 走出一手,並以**單一請求**取得對手應手與其後的局面狀態(requirements 3.1)。
   *
   * 自己的一手先進序列再送出,盤面因此立刻動;失敗時整份序列退回送出前的值,
   * 盤面也就跟著退回去 —— 那是**整體替換**,不是單手回退(requirements 5.2)。
   *
   * @param {string} uci 要走的著法,必須在當前的 `legalMoves` 之中。
   * @returns {Promise<boolean>} 對局是否因此前進。被擋下(等待中、非我方回合、
   *   對局已結束、著法不在後端給的清單裡)或請求失敗時為 `false`,兩者都**不會**
   *   留下半推進的狀態。
   */
  async function play(uci) {
    if (!snapshot.legalMoves.includes(uci)) return false;

    const issued = ++generation;
    const beforeSending = moves;          // 整份留著,失敗時原樣換回
    // 局面狀態也一起留著。序列與局面是同一份真相的兩半:只退回序列而讓局面停在
    // 推進後,快照就會對著起始盤面發出**走後**的著法清單,而那份畫面上的每一個
    // 落點都會被後端拒絕。收下回應之後才發生的失敗(例如某個回饋來源丟例外)
    // 正好落在這條路徑上。
    const stateBeforeSending = currentState;
    moves = [...moves, uci];
    waiting = true;
    failure = null;
    notify();

    try {
      // 每次都重送完整序列(3.5)—— 後端不記任何對局進度。
      const reply = await requestBlackMove(positionId, [...moves], requestOptions);
      if (issued !== generation) return false;

      // 對手著法為空是正常情況(紅方這一手就將死黑方),不是錯誤。
      if (reply.move != null) moves = [...moves, reply.move];
      // 終局、勝方、下一手可走的著法**全部**來自這裡,信號一概不參與(3.3、4.3)。
      currentState = reply.state;
      feedback = collectFeedback({ reply, moves: [...moves], position });
      waiting = false;
      notify();
      return true;
    } catch (error) {
      if (issued !== generation) return false;
      moves = beforeSending;
      currentState = stateBeforeSending;
      waiting = false;
      failure = toFailure(error);
      notify();
      return false;
    }
  }

  /**
   * 重來:走法序列清空,回到題目起始局面(requirements 5.1)。
   *
   * **不打後端。** 起始局面在載入時就拿到了,而重來是失敗之後的復原路徑
   * (requirements 7.3)—— 它自己再依賴一次網路,就會在連線壞掉時把使用者鎖死在
   * 不可復原的狀態,正好違反 requirements 7.4。
   *
   * 等待中也可以重來:代次一變,在途的應手回來時就作廢了。
   */
  function reset() {
    generation++;
    moves = [];
    currentState = startingState;
    feedback = Object.freeze([]);
    waiting = false;
    failure = null;
    notify();
  }

  return {
    /** 當前狀態的唯讀快照。 */
    getState: () => snapshot,
    /**
     * 訂閱狀態變動,回傳解除訂閱的函式。
     *
     * 不會立刻以當前狀態呼叫一次 —— 呈現層自己先 `getState()` 畫第一次。
     */
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    load,
    play,
    reset,
  };
}
