/**
 * FEN 解析、著法套用與座標互轉。
 *
 * 自 `poc/index.html` 移植(`parseFen`、`applyMove` 與其依賴的座標轉換),
 * 除了包裝成 ES module 之外沒有行為上的改動 —— POC 已實際跑過整局。
 *
 * 本模組位於依賴鏈的最左端:純函式、不碰 DOM、不發請求、不持有狀態,
 * 且**不得 import 任何其他 web 模組**(design 的「模組結構與依賴方向」)。
 * 這也是它能以 `page.evaluate()` 單獨驗證的原因。
 *
 * 座標約定:file `a`–`i` 對應 0–8(紅方視角由左至右),rank 0–9 且**紅方底線為
 * rank 0**。盤面陣列以 `board[rank][file]` 定址,空格為 `null`;紅子為大寫、
 * 黑子為小寫,與 FEN 一致。
 */

/** 路數(file):a–i。 */
export const FILES = 9;

/** 列數(rank):0–9。 */
export const RANKS = 10;

/**
 * FEN 棋子代碼到中文名的對照。大寫為紅、小寫為黑,同一兵種紅黑用字不同。
 *
 * 放在 `fen.js` 是因為棋子代碼本來就是 FEN 的一部分,而 `notation.js`(記譜)與
 * `board.js`(繪製)都需要它 —— 各自複製一份遲早會漂移成兩套字。
 */
export const NAMES = {
  R: '俥', N: '傌', B: '相', A: '仕', C: '炮', P: '兵', K: '帥',
  r: '車', n: '馬', b: '象', a: '士', c: '包', p: '卒', k: '將',
};

/** `'f8'` -> `[file, rank]`,即 `[5, 8]`。 */
export const sq2fr = (square) => [square.charCodeAt(0) - 97, +square[1]];

/** `[5, 8]` -> `'f8'`,`sq2fr` 的反函式。 */
export const fr2sq = (file, rank) => String.fromCharCode(97 + file) + rank;

/**
 * 把 FEN 展開成盤面陣列。
 *
 * 只有第一段(盤面)參與解析,其餘欄位(輪方、回合數)一律忽略 —— 輪方由
 * `game.js` 自走法序列推得,不從 FEN 讀。
 *
 * @param {string} fen 完整 FEN 或僅盤面段。
 * @returns {(string|null)[][]} `board[rank][file]`,空格為 `null`。
 */
export function parseFen(fen) {
  const rows = fen.split(' ')[0].split('/');   // rows[0] = rank9
  const b = Array.from({length: RANKS}, () => Array(FILES).fill(null));
  rows.forEach((row, i) => {
    const rank = 9 - i; let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) file += +ch;
      else { b[rank][file] = ch; file++; }
    }
  });
  return b;
}

/**
 * 把一手 UCI 著法就地套用到盤面:終點換成起點的子,起點清空。
 *
 * 吃子不需要特別處理 —— 終點原有的子被覆蓋掉就是被吃。**不做任何合法性檢查**:
 * 合法著法一律來自後端(design 的 Out of Boundary),此處只負責搬子。
 *
 * 就地更新是 POC 的既有契約,也正是 `game.js` 的用法:自起始局面把走法序列
 * 逐手套用上去,推導出當前盤面。
 *
 * @param {(string|null)[][]} b 盤面陣列,會被就地修改。
 * @param {string} uci 四字元的 UCI 著法,例如 `'d8d9'`。
 */
export function applyMove(b, uci) {
  const [ff, fr] = sq2fr(uci.slice(0, 2));
  const [tf, tr] = sq2fr(uci.slice(2, 4));
  b[tr][tf] = b[fr][ff];
  b[fr][ff] = null;
}
