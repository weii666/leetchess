/**
 * 標星狀態:一份題號集合,存在瀏覽器本機。**與完成狀態(`progress.js`)是兩份獨立
 * 資料** —— 標星是使用者自己整理題目的方式,不代表做過或做對,因此不與完成狀態
 * 共用儲存鍵或集合。
 *
 * 形狀刻意與 `progress.js` 逐項對稱(儲存鍵版本化前綴、讀取的防禦式退路、寫入
 * 失敗一律安靜算了、`toggleStarred` 不就地修改):兩份狀態除了語意不同,其餘規則
 * 沒有理由分岔,細節取捨見 `progress.js` 對應函式的說明。
 *
 * 與 `catalog.js`、`fen.js`、`progress.js` 同層:不得 import 任何其他 web 模組,
 * 也不碰 DOM。
 *
 * 篩選「只看標星題目」尚未實作(Backlog):`loadStarred()` 回傳的集合可直接接上
 * `catalog.js` 既有的 `filterPositions()`,但這裡不做那條線。
 */

/** 標星狀態的儲存鍵,`v1` 是版本前綴 —— 與 `progress.js` 的 `STORAGE_KEY` 同一套理由。 */
export const STORAGE_KEY = 'leetchess:v1:starred';

const storage = () => {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
};

const isPositionId = (value) => Number.isInteger(value) && value > 0;

const toIds = (values) => new Set([...(values ?? [])].filter(isPositionId));

/**
 * 讀出目前標星的題號。讀不到或認不得時是空集合,不丟例外(理由見 `progress.js`
 * 的 `loadCompleted`)。
 *
 * @returns {Set<number>} 標星的題號。
 */
export function loadStarred() {
  const store = storage();
  if (store === null) return new Set();

  let stored;
  try {
    stored = store.getItem(STORAGE_KEY);
  } catch {
    return new Set();
  }

  let parsed;
  try {
    parsed = JSON.parse(stored);
  } catch {
    return new Set();
  }
  if (!Array.isArray(parsed)) return new Set();

  return toIds(parsed);
}

function writeIds(starred) {
  const store = storage();
  if (store === null) return;
  try {
    store.setItem(
      STORAGE_KEY,
      JSON.stringify([...starred].sort((a, b) => a - b)),
    );
  } catch {
    // 配額爆掉、被政策擋下 —— 安靜地算了,理由見 `progress.js` 的 `toggleCompleted`。
  }
}

/**
 * 切換一題的標星,並盡力寫入本機。傳入目前的集合、回傳新的集合,不就地修改。
 *
 * @param {Iterable<number>} starred 目前標星的題號。
 * @param {number} id 要切換的題號。
 * @returns {Set<number>} 切換後標星的題號。
 */
export function toggleStarred(starred, id) {
  const next = toIds(starred);
  if (!isPositionId(id)) return next;

  if (!next.delete(id)) next.add(id);
  writeIds(next);
  return next;
}
