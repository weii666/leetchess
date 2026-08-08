/**
 * 後端 client:唯一與 engine-service 往來的地方。
 *
 * 對外只有兩個操作 —— 載入題目與取得對手應手。本輪用不到局面查詢端點:載入題目
 * 的回應已含起始局面的合法著法,重來時再取一次即可(design 的「每手棋一次請求」)。
 *
 * 本模組位於依賴鏈的最左端,與 `fen.js` 同層:**不得 import 任何其他 web 模組**,
 * 也不碰 DOM(呈現是 `app.js` 的事)。
 *
 * ## 兩件它必須包住、不讓外面看見的事
 *
 * 1. **每次請求都有逾時上界。** 沒有上界的請求會讓呼叫方永遠停在等待中,
 *    requirements 6.4 的「等待態必被解除」也就無從實現。逾時與連線失敗各自
 *    收斂成一種可辨識的失敗(7.1)。
 * 2. **後端的原始內容一個字都不外流。** 錯誤有兩種形狀 —— 端點錯誤是
 *    `{code, message}`(七種碼之一),路由層的 404/405 則是框架原生的
 *    `{"detail": ...}`,不在契約內。**只有類別碼是契約**,訊息文字不是;任何
 *    無法辨識的形狀(框架原生格式、HTML 錯誤頁、空內容、契約外的類別碼)一律
 *    歸為 `UNKNOWN`(7.5)。呈現層拿到的永遠只是一個碼,因此它不可能顯示出
 *    後端的原文或技術細節。
 *
 * ## 不支援取消
 *
 * requirements 6.3 已列 Backlog:單次搜尋約 0.12 秒加 RTT,且前端取消不會中止
 * 後端搜尋,省不下任何資源。此處的 `AbortController` **只服務逾時**,不對外
 * 暴露;要真正中止得先在後端契約加入取消機制。
 */

/**
 * 失敗的類別。前七種原樣沿用後端契約(`service/errors.py` 的 `ErrorCode`),
 * 後三種是 client 自己才知道的情況。
 *
 * 這是一份列舉而非隨手寫的字串:呼叫方(`game.js`、`app.js`)要有東西可比對,
 * 才能把失敗分到「可重試」與「須重來」兩類。
 */
export const ApiErrorCode = Object.freeze({
  // --- 後端契約的七種類別碼 ---
  INVALID_MOVE_FORMAT: 'INVALID_MOVE_FORMAT',
  POSITION_NOT_FOUND: 'POSITION_NOT_FOUND',
  ILLEGAL_MOVE_SEQUENCE: 'ILLEGAL_MOVE_SEQUENCE',
  WRONG_SIDE_TO_MOVE: 'WRONG_SIDE_TO_MOVE',
  SERVICE_BUSY: 'SERVICE_BUSY',
  ENGINE_TIMEOUT: 'ENGINE_TIMEOUT',
  INTERNAL: 'INTERNAL',
  // --- client 自己判定的三種 ---
  /** 逾時上界到了後端仍未回話。 */
  TIMEOUT: 'TIMEOUT',
  /** 連線建立不起來或中途斷掉。 */
  NETWORK: 'NETWORK',
  /** 回應的形狀無法辨識 —— 框架原生格式、HTML、空內容、契約外的碼。 */
  UNKNOWN: 'UNKNOWN',
});

/** 後端契約承認的類別碼,用來擋掉契約外的字串。 */
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
 * 逾時上界的預設值(毫秒)。
 *
 * 後端自己的總時間預算是 8 秒(`service/config.py` 的 `DEFAULT_TOTAL_TIME_BUDGET`
 * = 借用等待 + 搜尋逾時 + 停止寬限),超過就由它自己回 `SERVICE_BUSY` /
 * `ENGINE_TIMEOUT`。前端的上界必須比它寬,否則會把後端**正要成功回來**的回應
 * 誤判成逾時;10 秒留下約 2 秒給網路往返。
 */
export const DEFAULT_TIMEOUT_MS = 10000;

/**
 * 對呈現層唯一的失敗表示。
 *
 * **只帶類別碼,不帶任何來自後端或瀏覽器的文字。** `message` 刻意就是碼本身:
 * 這樣即使有人直接把 error 印出來,也不會洩漏後端原文或 `Failed to fetch`
 * 這類技術細節(7.5)。使用者看到的文字一律由呈現層依碼自行決定。
 */
export class ApiError extends Error {
  /** @param {string} code `ApiErrorCode` 之一。 */
  constructor(code) {
    super(code);
    this.name = 'ApiError';
    this.code = code;
  }
}

/** 是否為可當作回應主體的 JSON 物件(排除 null 與陣列)。 */
const isObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);

/**
 * 從錯誤回應的主體判出類別碼。
 *
 * 分類**只看 `code`,不看 HTTP 狀態** —— 狀態碼是多對一的(409 同時是
 * `ILLEGAL_MOVE_SEQUENCE` 與 `WRONG_SIDE_TO_MOVE`),而路由層的 404 與端點的
 * 404 更是完全不同的兩件事。契約外的一切都是 `UNKNOWN`。
 */
function classify(payload) {
  if (isObject(payload) && BACKEND_CODES.has(payload.code)) return payload.code;
  return ApiErrorCode.UNKNOWN;
}

/**
 * 發一次請求,把回應收斂成「解析後的主體」或一個 `ApiError`。
 *
 * @param {string} path 同源路徑,例如 `/api/black-move`。
 * @param {object} options
 * @param {string} [options.method] HTTP 方法,預設 `GET`。
 * @param {object} [options.body] 請求主體,會序列化成 JSON。
 * @param {number} [options.timeoutMs] 逾時上界。
 * @param {(payload: any) => boolean} options.accept 回應形狀的辨識函式。
 * @returns {Promise<any>} 解析後的回應主體。
 */
async function request(path, { method = 'GET', body, timeoutMs, accept }) {
  const controller = new AbortController();
  // fetch 被 abort 時只會拋出一個 AbortError,分不出是誰下的令;自己記一筆,
  // 才能把「逾時」與「連線失敗」分成兩種失敗。
  let timedOut = false;
  const deadline = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs ?? DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    // 先取文字再自己 parse:`response.json()` 對空內容或 HTML 會拋錯,而那與
    // 連線斷掉是完全不同的情況,不能混進同一個 catch。
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined;      // 不是 JSON —— 形狀無法辨識
    }

    if (!response.ok) throw new ApiError(classify(payload));
    if (!accept(payload)) throw new ApiError(ApiErrorCode.UNKNOWN);
    return payload;
  } catch (error) {
    // 上面自己丟的分類結果原樣往上;其餘一律是連線層面的失敗 —— 瀏覽器的
    // 原始訊息到此為止,不往外帶。
    if (error instanceof ApiError) throw error;
    throw new ApiError(timedOut ? ApiErrorCode.TIMEOUT : ApiErrorCode.NETWORK);
  } finally {
    // 逾時要涵蓋讀取主體,所以計時器直到這裡(成功或失敗)才解除。
    clearTimeout(deadline);
  }
}

/**
 * 載入題目:`GET /api/positions/{id}`。
 *
 * 回應含題目資訊與**起始局面的合法著法**,因此載入之後不需要再查一次局面。
 *
 * @param {number|string} positionId 題號。
 * @param {{timeoutMs?: number}} [options]
 * @returns {Promise<object>} 題目與其起始局面狀態。
 * @throws {ApiError} 題目不存在時為 `POSITION_NOT_FOUND`(1.4);其餘見 `ApiErrorCode`。
 */
export function loadPosition(positionId, options) {
  return request(`/api/positions/${encodeURIComponent(positionId)}`, {
    timeoutMs: options?.timeoutMs,
    // 前端真正依賴的欄位:起始盤面與該局面的合法著法。少了任何一個都畫不出
    // 可操作的棋盤,與其交出半份資料,不如歸為無法辨識。
    accept: (payload) =>
      isObject(payload) &&
      typeof payload.fen === 'string' &&
      isObject(payload.state) &&
      Array.isArray(payload.state.legal_moves),
  });
}

/**
 * 取得對手應手:`POST /api/black-move`。
 *
 * **走法序列走請求主體,不走 query string** —— 序列會長到不適合放在 URL 上,
 * 而且這是唯一的真相來源,每次都重送完整序列(3.5);後端不記任何對局進度。
 *
 * 使用者走完一手後直接呼叫本操作,**不先查詢局面確認終局** —— 回應已涵蓋
 * 「紅方這一手就將死黑方」(`move` 為 null、`state.over` 為真、勝方為紅),
 * 而那正是每一題排局的最後一手。
 *
 * @param {number|string} positionId 題號。
 * @param {string[]} moves 自起始局面起的完整 UCI 走法序列。
 * @param {{timeoutMs?: number}} [options]
 * @returns {Promise<object>} `{move, signal, mate_in, state}`。`move` 可能為
 *   `null`(黑方無應手),`mate_in` **可能為 `0`**(終局那手)—— 兩者都原樣傳出,
 *   由呈現層判斷,此處不做任何 falsy 的過濾。
 * @throws {ApiError} 見 `ApiErrorCode`。
 */
export function requestBlackMove(positionId, moves, options) {
  return request('/api/black-move', {
    method: 'POST',
    body: { position_id: positionId, moves },
    timeoutMs: options?.timeoutMs,
    // 局面狀態是推進對局的必要條件(終局與下一手的合法著法都在裡面)。
    accept: (payload) =>
      isObject(payload) &&
      isObject(payload.state) &&
      typeof payload.state.over === 'boolean' &&
      Array.isArray(payload.state.legal_moves),
  });
}
