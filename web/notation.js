/**
 * UCI 著法轉中文記譜。
 *
 * 自 `poc/index.html` 的 `uci2cn` 移植。與 `parseFen` / `applyMove` 不同的是,
 * POC 的這段邏輯**沒有被實戰跑過** —— 《適情雅趣》第 21 局全程沒有出現任何一對
 * 同名子共線,所以「前/後」那條分支一次都沒進去過。移植時因此逐條對規則驗過,
 * 並在 `tests/test_web_pure.py` 補上針對性案例(同線雙車、同線雙馬、同線兵、
 * 進退平)。結論是 POC 的判別與規則一致,行為照搬,只補註解與命名。
 *
 * 本模組是純函式:不碰 DOM、不發請求、不持有狀態,依賴方向上只能向左依賴
 * `fen.js`(design 的「模組結構與依賴方向」)。
 *
 * ## 記譜規則
 *
 * 記譜描述的是**走子前**的盤面,格式為「頭銜 + 走向 + 目標」:
 *
 * - **頭銜**:兵種名 + 縱線序號。紅方縱線以漢字自紅方右手邊起算(i 路為一、
 *   a 路為九),黑方以阿拉伯數字自黑方右手邊起算(a 路為 1、i 路為 9)。
 *   同一縱線上有兩個以上同色同兵種的子時,不寫縱線序號,改以「前」「後」
 *   (三子為前/中/後,四子以上自前向後編號)區分。
 * - **走向**:換縱線為「平」;同縱線則紅方朝 rank 大的方向為「進」、黑方相反。
 * - **目標**:走直線的子(車、炮、兵、將帥)接**移動格數**;走斜線的子
 *   (馬、相/象、仕/士)接**目標縱線序號**。
 */

import { NAMES, RANKS, sq2fr } from './fen.js';

/** 紅方使用的漢字數碼。索引 0 用不到 —— 縱線序號與格數都自 1 起算。 */
const CN_NUM = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

/**
 * 走斜線的兵種(以大寫代碼表示,不分紅黑)。
 *
 * 這些子每一步都同時改變縱線與橫線,所以「進/退」後面接目標縱線序號;寫格數對
 * 它們沒有意義(馬永遠是一格或兩格,相/象永遠兩格,仕/士永遠一格)。
 */
const DIAGONAL = new Set(['N', 'B', 'A']);

/**
 * 把一手 UCI 著法轉成中文記譜。
 *
 * 傳入的必須是**走子前**的盤面 —— 縱線序號與前/後判別都取決於該子當下的位置。
 * 呼叫端的用法是「先記譜、再 `applyMove`」。本函式不改動盤面。
 *
 * 不做合法性檢查:合法著法一律來自後端(design 的 Out of Boundary),此處只負責
 * 把一手已知合法的著法翻成中文。
 *
 * @param {(string|null)[][]} board 走子前的盤面,`board[rank][file]`。
 * @param {string} uci 四字元的 UCI 著法,例如 `'c3c5'`。
 * @returns {string} 中文記譜,例如 `'前俥進二'`。
 */
export function uci2cn(board, uci) {
  const [fromFile, fromRank] = sq2fr(uci.slice(0, 2));
  const [toFile, toRank] = sq2fr(uci.slice(2, 4));
  const piece = board[fromRank][fromFile];
  const isRed = piece === piece.toUpperCase();

  /** 檔案索引(0–8)轉縱線序號(1–9)。紅方自右起算,黑方方向相反。 */
  const col = (file) => (isRed ? 9 - file : file + 1);
  /** 紅方一律用漢字、黑方一律用阿拉伯數字 —— 縱線序號與格數都適用。 */
  const num = (n) => (isRed ? CN_NUM[n] : String(n));

  return head() + tail();

  /** 兵種名 + 縱線序號,或同線時的「前/中/後」。 */
  function head() {
    // 比對整個棋子代碼(而非兵種),顏色因此自動被分開:c 路上同時有紅俥與黑車
    // 時,兩方各自計算自己的前後。
    const sameFile = [];
    for (let rank = 0; rank < RANKS; rank++) {
      if (board[rank][fromFile] === piece) sameFile.push(rank);
    }

    if (sameFile.length < 2) return NAMES[piece] + num(col(fromFile));

    // 由前而後排序。「前」對紅方是 rank 大(逼近黑方底線),對黑方相反。
    sameFile.sort((a, b) => (isRed ? b - a : a - b));
    const index = sameFile.indexOf(fromRank);

    let rankWord;
    if (sameFile.length === 2) rankWord = index === 0 ? '前' : '後';
    else if (sameFile.length === 3) rankWord = ['前', '中', '後'][index];
    else rankWord = num(index + 1);   // 四子以上:自前向後編號

    return rankWord + NAMES[piece];
  }

  /** 進 / 退 / 平 + 目標(格數或目標縱線序號)。 */
  function tail() {
    if (fromRank === toRank) return '平' + num(col(toFile));

    const forward = isRed ? toRank > fromRank : toRank < fromRank;
    const steps = Math.abs(toRank - fromRank);
    const target = DIAGONAL.has(piece.toUpperCase()) ? col(toFile) : steps;

    return (forward ? '進' : '退') + num(target);
  }
}
