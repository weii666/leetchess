/**
 * 篩選條件:一個字串,存在瀏覽器本機。**與 `progress.js`、`starred.js` 同一套
 * 規則**(儲存鍵版本化前綴、讀取的防禦式退路、寫入失敗一律安靜算了),差別只在
 * 存的是 `sessionStorage` 而非 `localStorage`——理由見下方 `STORAGE_KEY`。
 *
 * 與 `catalog.js`、`fen.js`、`progress.js`、`starred.js` 同層:不得 import 任何
 * 其他 web 模組,也不碰 DOM。
 *
 * ## 為什麼是 `sessionStorage` 而不是網址參數
 *
 * 原本用網址參數(`?filter=easy`)記,理由是「從對局頁按上一頁回來是整頁重新
 * 載入,網址是唯一活過那次導覽的地方」。但**回到列表的連結是一個固定的
 * `<a href="./index.html">`**(`web/app.js` 的 `mountBackLink()`,問題見那裡的
 * 理由:必須是真連結才有中鍵開新分頁、右鍵複製網址等免費附帶的行為),不是
 * `history.back()`,也不會知道使用者離開列表時網址上帶的是什麼篩選參數 ——
 * 要接得起來,`list.js`(建每一列指向對局頁的連結)與 `app.js`(建返回連結)
 * 兩邊都要來回傳遞這個參數,兩頁因此多一份耦合。
 *
 * `sessionStorage` 不必動 `app.js` 一個字:同一個分頁的 session 裡本來就活得過
 * 任何一種返回路徑(這條連結、瀏覽器的上一頁、甚至使用者自己改網址),寫入與
 * 讀出都只在 `list.js` 這一側發生。代價是網址不再帶著篩選條件,分享網址或加
 * 書籤不會連著篩選類別一起帶走——但這從來不是本功能要解的問題,篩選狀態能撐過
 * 「點進一題再回來」才是。
 */

/** 篩選條件的儲存鍵,`v1` 是版本前綴 —— 與 `progress.js`、`starred.js` 同一套理由。 */
export const STORAGE_KEY = 'leetchess:v1:filter';

/** 合法的篩選值,對照 `index.html` 的 `<option value="...">`。 */
export const FILTER_VALUES = new Set(['all', 'favorite', 'easy', 'medium', 'hard']);

const storage = () => {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
};

/**
 * 讀出目前的篩選條件。讀不到、儲存區不可用、或值認不得時一律回退 `'all'`,
 * 不丟例外(理由見 `progress.js` 的 `loadCompleted`)。
 *
 * @returns {string} `FILTER_VALUES` 之一。
 */
export function loadFilter() {
  const store = storage();
  if (store === null) return 'all';

  let value;
  try {
    value = store.getItem(STORAGE_KEY);
  } catch {
    return 'all';
  }

  return FILTER_VALUES.has(value) ? value : 'all';
}

/**
 * 寫入目前的篩選條件,盡力而為。認不得的值不寫入——呼叫端(`list.js`)只會傳
 * `<select>` 實際選到的值,這裡的檢查是防禦最後一道,不是期待它常態被觸發。
 *
 * @param {string} value 要寫入的篩選條件。
 */
export function saveFilter(value) {
  if (!FILTER_VALUES.has(value)) return;
  const store = storage();
  if (store === null) return;
  try {
    store.setItem(STORAGE_KEY, value);
  } catch {
    // 配額爆掉、被政策擋下 —— 安靜地算了,理由見 `progress.js` 的 `toggleCompleted`。
  }
}
