/**
 * 題目索引:題庫列表所需資料的**唯一來源**。
 *
 * 索引向後端取**一次**,之後不論使用者怎麼改篩選條件,都在記憶體中對同一份
 * 資料求值 —— 篩選不會再發任何請求(problem-browser 5.1)。
 *
 * 本模組位於依賴鏈的最左端,與 `fen.js`、`api.js` 同層:**不得 import 任何其他
 * web 模組**,也不碰 DOM。呈現、篩選介面與空狀態的文字都是 `list.js` 的事;
 * 這裡只有資料。這使它能單獨被載進瀏覽器以 `page.evaluate()` 驗證。
 *
 * ## 為什麼不共用 `api.js`
 *
 * `api.js` 是對局用的 client,它的失敗模型(七種後端契約碼、可重試與須重來的
 * 分類)是為對局設計的,而索引端點根本不在那份契約裡 —— 它不借引擎,也就永遠
 * 不會回 `SERVICE_BUSY` 或 `ENGINE_TIMEOUT`。共用會讓兩邊的契約互相牽動。此處
 * 只沿用它的兩個做法:每次請求都有逾時上界,以及後端的原始內容一個字都不外流。
 *
 * ## 引擎資源
 *
 * **本模組只認得一個位址,就是 `CATALOG_PATH`。** 需要引擎的端點
 * (`/api/positions/{id}`、`/api/black-move`)在這裡連字串都不存在,因此引擎池
 * 滿的時候題庫列表照樣開得起來、篩得動(5.2)。
 */

/** 索引端點。這是本模組唯一會碰的位址。 */
export const CATALOG_PATH = '/api/catalog';

/**
 * 逾時上界的預設值(毫秒)。
 *
 * 索引端點不借引擎、只把啟動時已建好的題庫索引讀出來,所以它本來就該很快;
 * 上界存在的理由不是等它算完,而是**不讓列表永遠停在載入中** —— 沒有上界的
 * 請求會讓使用者面對一個不會結束也不會報錯的畫面。
 */
export const DEFAULT_TIMEOUT_MS = 10000;

/**
 * 取不到索引的類別。**只有三種,而且都不是後端契約的碼** —— 索引端點不借引擎,
 * 契約裡那七種類別對它沒有意義。
 */
export const CatalogErrorCode = Object.freeze({
  /** 逾時上界到了後端仍未回話。 */
  TIMEOUT: 'TIMEOUT',
  /** 連線建立不起來或中途斷掉。 */
  NETWORK: 'NETWORK',
  /** 後端回了,但那不是一份認得出來的索引(錯誤狀態、HTML、空內容、形狀不符)。 */
  UNAVAILABLE: 'UNAVAILABLE',
});

/**
 * 取不到索引時的唯一失敗表示。
 *
 * **只帶類別碼,不帶任何來自後端或瀏覽器的文字。** `message` 刻意就是碼本身:
 * 即使有人直接把它印出來,也不會洩漏後端原文或 `Failed to fetch` 這類技術細節。
 */
export class CatalogError extends Error {
  /** @param {string} code `CatalogErrorCode` 之一。 */
  constructor(code) {
    super(code);
    this.name = 'CatalogError';
    this.code = code;
  }
}

/** 是否為可當作回應主體的 JSON 物件(排除 null 與陣列)。 */
const isObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * 難度的比對值。空值代表「不篩這個維度」。
 *
 * 轉成數字是因為篩選條件多半來自下拉選單,而 DOM 給出來的值是字串;`'3'` 與
 * `3` 若不相等,使用者一選難度整份列表就會憑空消失。認不得的值會轉成 `NaN`
 * 而與任何題目都不相等 —— 未知的難度篩不到東西,勝過默默把條件當成沒選。
 */
const asDifficulty = (value) =>
  value === null || value === undefined || value === '' ? null : Number(value);

/** 標籤與出處的比對值。空字串等同於未選 —— 下拉選單的「全部」。 */
const asText = (value) =>
  value === null || value === undefined || value === '' ? null : String(value);

/**
 * 依難度、標籤、出處篩選(2.1、2.2、2.3、2.4)。
 *
 * **多個條件為 AND** —— 只列出全部條件皆符合的題目。未給或給空值的維度不參與
 * 比對,因此完全不給條件時回的就是原本那一批。
 *
 * 回傳的是**新陣列**,傳入的那一份原封不動:篩選是取值而不是消耗,列表才有辦法
 * 清除條件回到完整狀態(2.5)。
 *
 * @param {object[]} positions 要篩的題目。
 * @param {{difficulty?: number|string|null, tag?: string|null, source?: string|null}} [criteria]
 * @returns {object[]} 符合全部條件的題目,順序與傳入時相同。
 */
export function filterPositions(positions, criteria) {
  const difficulty = asDifficulty(criteria?.difficulty);
  const tag = asText(criteria?.tag);
  const source = asText(criteria?.source);

  return positions.filter(
    (position) =>
      (difficulty === null || position.difficulty === difficulty) &&
      // 標籤是多值欄位,比對的是「含有」而非相等。
      (tag === null || (Array.isArray(position.tags) && position.tags.includes(tag))) &&
      (source === null || position.source === source),
  );
}

/**
 * 取一次索引,把回應收斂成題目陣列或一個 `CatalogError`。
 *
 * @param {number|undefined} timeoutMs 逾時上界。
 * @returns {Promise<object[]>}
 */
async function fetchIndex(timeoutMs) {
  const controller = new AbortController();
  // fetch 被 abort 時只會拋出一個 AbortError,分不出是誰下的令;自己記一筆,
  // 才能把「逾時」與「連線失敗」分成兩種失敗。
  let timedOut = false;
  const deadline = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs ?? DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(CATALOG_PATH, { signal: controller.signal });

    // 先取文字再自己 parse:`response.json()` 對空內容或 HTML 錯誤頁會拋錯,
    // 而那與連線斷掉是完全不同的情況,不能混進同一個 catch。
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined; // 不是 JSON —— 形狀無法辨識
    }

    // 形狀認不得就是失敗,**不能靜默當成一份空索引**:「題庫真的沒有題目」與
    // 「索引壞掉」對使用者是兩件事(空狀態 vs. 錯誤提示與重試)。
    if (!response.ok || !isObject(payload) || !Array.isArray(payload.positions)) {
      throw new CatalogError(CatalogErrorCode.UNAVAILABLE);
    }
    return payload.positions;
  } catch (error) {
    // 上面自己丟的分類結果原樣往上;其餘一律是連線層面的失敗 —— 瀏覽器的
    // 原始訊息到此為止,不往外帶。
    if (error instanceof CatalogError) throw error;
    throw new CatalogError(
      timedOut ? CatalogErrorCode.TIMEOUT : CatalogErrorCode.NETWORK,
    );
  } finally {
    // 逾時要涵蓋讀取主體,所以計時器直到這裡(成功或失敗)才解除。
    clearTimeout(deadline);
  }
}

/**
 * 取得題目索引(5.1、5.2)。
 *
 * 回傳的物件就是列表之後要用的**全部**資料:`positions` 是索引裡的完整清單
 * (順序即索引的順序,依題號遞增),`filter()` 則在這份清單上就地求值。
 * **`filter()` 不會再發任何請求。**
 *
 * ## 這裡不再過濾任何東西
 *
 * 曾有一道 `isListable()` 依索引的 `solvable` 濾掉偽題(1.4)。該欄位已隨題目
 * schema 移除:它要等 corpus-verification 跑完才有值,而在那之前每一題都是空值,
 * 於是那道過濾從上線到拆掉為止不曾濾掉任何一題。日後真要標記偽題時,那是驗證
 * 工具的產出,屆時重新決定它要落在索引還是這一層 —— 而不是留一道空轉的過濾。
 *
 * @param {{timeoutMs?: number}} [options]
 * @returns {Promise<{positions: readonly object[], filter: (criteria?: object) => object[]}>}
 * @throws {CatalogError} 索引取不到或形狀認不得時。呈現層只拿得到一個碼。
 */
export async function loadCatalog(options) {
  const positions = Object.freeze(await fetchIndex(options?.timeoutMs));

  return Object.freeze({
    positions,
    filter: (criteria) => filterPositions(positions, criteria),
  });
}
