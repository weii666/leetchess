/**
 * 難度分級的說法與其退路。**列表頁與對局頁共用。**
 *
 * 這個模組沒有依賴、也不碰 DOM:它只把一個難度值翻成「要顯示的字」與「上色用的
 * 掛勾」,由呼叫端各自決定畫成什麼元素、掛哪個 class。列表把它畫成一格
 * `.position-difficulty`,對局頁畫成 meta 行裡的 `#puzzle-difficulty`,兩者的版面
 * 差得遠,但**說法與分級規則必須是同一份**。
 *
 * 兩頁各寫一份 `Easy` / `Medium` / `Hard` 的話,改其中一邊就會靜默分家 —— 使用者
 * 從列表點進對局頁,同一題的難度說法不一樣,而那種錯誤沒有任何測試會自然抓到。
 *
 * ## 分級的定義不在這裡
 *
 * 1–3 的意義出自 `.kiro/steering/structure.md` 的題目 schema,改動要從那裡改起。
 *
 * ## 為什麼是英文
 *
 * 這是使用者可見文字裡**唯一**不用繁體中文的地方(另一處是 `LeetChess` 這個產品名,
 * 專有名詞不算),steering 的「使用者可見文字一律繁體中文」為此列了一條例外。
 *
 * 理由不是調性而是**可分辨**:列表上同一列右半邊的標籤(「解殺還殺」「借炮使馬」)
 * 也是中文詞,難度是無底色的彩色字,兩者的差別若只剩顏色,對色覺障礙者等於沒有線索,
 * 一般人在餘光掃視時也糊。拉丁字母在一排中文裡自成一格,不必靠顏色就分得出哪一個是
 * 難度。**因此顏色不是這一格唯一的線索,字形本身也是。**
 *
 * ## 認不得的值一定有退路
 *
 * schema 說難度是 1–3,但**沒有任何一層在執行期強制它**:`service/positions.py` 的
 * `_read_int` 對難度沒有下界,`0` 收得進來,`4`、`-1`、`null`、欄位不存在同理。
 * 這些值一律**原樣顯示、不給 `level`**(於是吃樣式表的中性色),而不是丟一個例外
 * 或畫不出那一格 —— 一題資料失準不該讓整份列表或整個對局頁消失。
 *
 * 判空以 `!= null` 而非 falsy:`0` 是一個真的值,雖然不是合法分級,使用者仍該看見它,
 * 才知道是資料有問題而不是欄位漏填。tasks 2.1 與 3.2 都在這個 falsy 陷阱上被抓過。
 */

/**
 * 難度分級的說法(1–3)。
 *
 * 用 `Map` 而不是物件字面值有兩個實質理由:
 *
 * 1. **鍵是數字而非字串。** 索引的 `difficulty` 一路是數字(`service/positions.py`
 *    的 `_read_int`),物件的鍵會把 `1` 與 `"1"` 混為一談;`Map.get('1')` 認不得,
 *    因而落到退路 —— 一個型別不對的值不會偽裝成一個合法的分級。
 * 2. 物件字面值查得到 `constructor`、`toString` 這些原型上的東西,`difficulty`
 *    只要是那幾個字串就會拿到一個函式當標籤。`Map` 結構上就沒有這個面。
 */
export const DIFFICULTY_LABELS = new Map([
  [1, 'Easy'],
  [2, 'Medium'],
  [3, 'Hard'],
]);

/**
 * 把一個難度值翻成要顯示的字與上色用的分級。
 *
 * @param {unknown} value 題目的 `difficulty`,任何值都收。
 * @param {string} blank 值不存在時的佔位符號,由呼叫端提供 —— 兩頁的佔位符號本來
 *   就是同一個字(`—`),但那是各頁自己的呈現決定,不該由本模組替它們決定。
 * @returns {{text: string, level: string|null}} `level` 為 `'1'`/`'2'`/`'3'`,
 *   認不得的值一律 `null`,樣式表因此吃基底規則的中性色。
 */
export function readDifficulty(value, blank) {
  const label = DIFFICULTY_LABELS.get(value);
  if (label !== undefined) {
    return { text: label, level: String(value) };
  }
  return { text: value != null ? String(value) : blank, level: null };
}
