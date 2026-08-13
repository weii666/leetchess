/**
 * 每日推薦題:從題庫依系統日期算出一題固定的推薦。
 *
 * 與 `catalog.js`、`progress.js`、`starred.js` 同層:純函式,不 import 任何其他
 * web 模組,也不碰 DOM。呈現(渲染成一列、換掉標籤欄)是 `list.js` 的事。
 *
 * ## 為什麼在瀏覽器算,不是後端
 *
 * `service/main.py` 的 `/api/catalog` 是啟動時建好、之後每次請求都回同一份靜態
 * 內容,後端完全沒有時鐘或時區概念。要在後端做「今天」等於要新增一個目前不存在
 * 的抽象,對單人使用的個人專案不成比例。前端則相反:`completed`、`starred` 本來
 * 就是整份狀態存在瀏覽器本機的模式,`positions` 也已經整份載進記憶體——在這裡
 * 多做一步「今天選哪一題」不需要新的依賴。
 *
 * 代價是本地日期/時區各自獨立:不同瀏覽器理論上可能在同一天算出不同題,這裡
 * 接受這個簡化。
 *
 * ## 為什麼是 rendezvous hash,不是 `hash(date) % positions.length`
 *
 * 取模的寫法簡單,但**題庫一旦變長(收新題目),幾乎所有日期的推薦題都會跟著
 * 改變**——不只當天,連已經過去的日期重算也會變,因為 `% length` 這個除數變了,
 * 等於重新洗牌整個對應關係。
 *
 * Rendezvous hash(又稱 HRW, Highest Random Weight)是這個問題的標準解法:不對
 * 「日期」取模,而是**對每一題各自算一次 `hash(日期:題號)`,取分數最高的那一題**。
 * 新增一題只是多一個候選者參與這場比較,只有在新題目剛好贏過原本的優勝者時,
 * 那個日期的推薦才會變——其餘所有日期的結果不受影響。
 *
 * 題庫規模只有幾百題,線性算過一輪的成本是微秒等級,不需要換成排序過的 hash ring
 * 換取 O(log N):那是題庫成長到百萬級、且高頻查詢的場景才需要的優化,在這裡是
 * 過度設計。
 *
 * 分數相同(雜湊碰撞)時,`pickDailyPosition` 保留**先出現(題號較小)者**——
 * `positions` 本來就依題號遞增排序,這讓平手時的結果本身也是決定性的,不必另外
 * 寫一套排序規則。
 */

/**
 * 瀏覽器本地日期的 `YYYY-MM-DD` 字串。
 *
 * **用 `getFullYear()`/`getMonth()`/`getDate()`,不用 `toISOString()`**——後者是
 * UTC,在台灣時區的深夜(UTC+8,`toISOString()` 還停在前一天)會把日期算錯一天。
 *
 * @param {Date} [now] 預設才用真的 `Date`;呼叫端(測試)可以直接傳一個固定的
 *   `Date` 進來,不必動全域的 `Date`。
 */
export function todayKey(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/** FNV-1a 32-bit,標準演算法,純整數運算,不依賴任何 library。 */
function hashString(value) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * 依日期字串選出當天的推薦題(rendezvous hash,理由見檔頭)。
 *
 * 純函式:同一組 `(positions, date)` 永遠選出同一題。對每一題各自算
 * `hash(日期:題號)`,取分數最高者;分數相同時保留 `positions` 裡先出現的那一題
 * (題號較小者)。
 *
 * @param {readonly object[]} positions `catalog.js` 的 `positions`。
 * @param {string} date `todayKey()` 的回傳值,或測試給的固定字串。
 * @returns {object|null} 選中的題目;`positions` 為空或不是陣列時回傳 `null`
 *   ——「今天沒有推薦題」是合法狀態(題庫還沒載好、載入失敗、或題庫真的是空的),
 *   呼叫端要能分得出來,不能丟例外逼列表打不開。
 */
export function pickDailyPosition(positions, date) {
  if (!Array.isArray(positions) || positions.length === 0) return null;

  let winner = null;
  let bestScore = -1;
  for (const position of positions) {
    const score = hashString(`${date}:${position.id}`);
    // 嚴格大於:分數相同時保留先出現的那一題,平手的結果因此也是決定性的。
    if (score > bestScore) {
      bestScore = score;
      winner = position;
    }
  }
  return winner;
}
