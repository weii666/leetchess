/**
 * 收題頁的**淺層檢查**。
 *
 * 本模組把表單當下的值翻成一份「哪裡還不對」的清單:不碰 DOM、不發請求、不持有
 * 狀態,因此相同輸入必得相同輸出,也因此能以 `page.evaluate()` 單獨驗證
 * (`tests/test_web_editor_pure.py`)。
 *
 * ## 這一層永遠不是放行判準
 *
 * 這裡做的只有「填了沒、形狀對不對」。**候選題目是否合格由服務端判定** ——
 * `POST /api/editor/validate` 走的是 `service/positions.py` 的同一份規則,那才是
 * 唯一權威。本模組回傳空清單只代表「這一層沒話說」,不代表這一題進得了題庫。
 *
 * 所以這裡**不得複製題目 schema 的規則**(必填欄位集合、型別、未知欄位、難度值域
 * ……)。複製一份的後果不是多一道保險,而是多一個真相來源:兩邊遲早分家,而分家
 * 時使用者看到的是「前端說可以、後端說不行」這種無從自救的矛盾。兩者若對同一個
 * 輸入給出不同結論,**以服務端為準**。
 *
 * 那這一層存在的理由是什麼?兩個:一是填表當下就給回饋,不必按下寫入才知道漏了
 * 一欄;二是讓寫入操作在明顯不完整時就停用。兩者都是體感,不是正確性。
 *
 * ## 偏保守是刻意的
 *
 * 檢查若比後端嚴,後果是使用者被前端擋下而後端本可接受 —— 方向安全。反過來則會
 * 讓壞資料一路走到寫檔前才被打回,那才是要避免的。因此拿不準的地方一律擋下。
 *
 * ## 依賴方向
 *
 * 只向左依賴 `web/fen.js` 的 `FILES` / `RANKS` 兩個常數。**`parseFen()` 刻意不用**:
 * 它的解析很寬鬆(列數不對、每列格數不對都照樣回一個 10x9 的盤面),那是對局路徑
 * 的既有契約,不得為本 spec 收緊。結構檢查因此是本模組自己的函式,與繪盤各司其職。
 */

import { FILES, RANKS } from '../fen.js';

/**
 * FEN 走子方那一欄認得的兩個取值。
 *
 * 中國象棋 FEN 沿用國際象棋的 `w`/`b`,紅方即 `w`(與 `service/positions.py` 的
 * `FEN_SIDES` 同一份約定)。用 `Set` 而非陣列:`has()` 問的就是「認不認得」,而
 * `includes()` 在一個兩元素的集合上讀起來像在找位置。
 *
 * **這裡曾經是一個 `Map`,值是「紅先」「黑先」兩個顯示字樣**,給 `sideFromFen()`
 * 用。那個函式連同起手方那一行呈現都已移除(requirement 2.6 的修訂),剩下的唯一
 * 用途是結構檢查問「走子方那一欄認不認得」—— 顯示字樣沒有讀者了,留著只會讓下一個
 * 人以為畫面上還有一處在印它。
 */
const SIDE_LABELS = new Set(['w', 'b']);

/**
 * @typedef {'id'|'title'|'difficulty'|'tags'|'fen'|null} CheckField
 * @typedef {{field: CheckField, message: string}} CheckIssue
 * @typedef {{id: string, title: string, difficulty: string,
 *            tags: string, fen: string}} FormValues
 */

/**
 * 一項未通過的檢查。
 *
 * @param {CheckField} field 對應的表單欄位名;不屬於任一欄位時為 `null`。
 * @param {string} message 給使用者看的說法,繁體中文。
 * @returns {CheckIssue}
 */
function issue(field, message) {
  return { field, message };
}

/**
 * 取表單的某一欄,非字串一律當空字串。
 *
 * Preconditions 是「無」:欄位不存在、值是 `null` 或數字都必須有定義的行為。一律
 * 折成空字串會讓它落到「尚未填妥」,那正是這種輸入該得到的說法。
 *
 * @param {unknown} values
 * @param {string} name
 * @returns {string}
 */
function read(values, name) {
  const value = values == null ? undefined : values[name];
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * 全部淺層檢查。空清單代表**這一層**沒話說(不代表題目合格,見模組說明)。
 *
 * 清單順序即表單欄位順序(8.4):題號、局名、難度、標籤、FEN,
 * 由下方各段的**敘述順序**保證。順序是呈現的基礎 —— 未通過項目在畫面上的位置若隨
 * 實作細節浮動,使用者每改一個欄位就得重新找一次訊息在哪。
 *
 * 同一欄位最多一項:一個空的 FEN 欄位不該同時說「必填」又說「列數不對」,那只是
 * 把同一件事講兩遍。
 *
 * @param {FormValues} values 表單的原始字串值,未經任何轉換。
 * @returns {CheckIssue[]}
 */
export function checkForm(values) {
  const issues = [];

  // --- 題號(4.1、4.2)---
  const id = read(values, 'id');
  if (id === '') {
    issues.push(issue('id', '請填入題號'));
  } else if (!isPositiveInteger(id)) {
    issues.push(issue('id', '題號必須是正整數,例如 26'));
  }

  // --- 局名、難度(4.1)---
  // 難度只檢查「有沒有選」。1/2/3 的值域出自 `structure.md`,由畫面上的三選一
  // 控制項保證,不在這裡再寫一份 —— 那會變成第二個真相來源。
  if (read(values, 'title') === '') {
    issues.push(issue('title', '請填入局名'));
  }
  if (read(values, 'difficulty') === '') {
    issues.push(issue('difficulty', '請選擇難度'));
  }

  // --- 標籤(4.6)---
  if (parseTags(read(values, 'tags')).length === 0) {
    issues.push(issue('tags', '標籤至少需要一個'));
  }

  // --- FEN(2.4)---
  // 它自己的檢查已經回傳定位到欄位的項目,直接接上即可,不必在此重寫說法。
  const fenIssue = checkFenStructure(read(values, 'fen'));
  if (fenIssue !== null) {
    issues.push(fenIssue);
  }

  return issues;
}

/**
 * 這串字是不是一個正整數的寫法。
 *
 * 刻意用字元集判斷而不是 `Number()`:`Number()` 收得下 `'1e3'`、`' 12 '`、`'0x1a'`
 * 與全形數字,那些都不是題號該有的寫法,放行只會讓一個看起來沒問題的題號在後端
 * 變成另一個數字。上界取 `Number.isSafeInteger`,再大的值連往返 JSON 都不安全。
 *
 * @param {string} text 已去除前後空白的字串。
 * @returns {boolean}
 */
function isPositiveInteger(text) {
  if (!/^[0-9]+$/.test(text)) {
    return false;
  }
  const value = Number(text);
  return value > 0 && Number.isSafeInteger(value);
}

/**
 * FEN 的結構檢查:列數、每列格數、走子方欄位(2.4)。
 *
 * **不是文法驗證,也不是合法性判定。** 局面是否合法一律由引擎判定(`tech.md` 的
 * 不可動搖約束),此處只回答「這串字展得開成一個 10x9 的盤面嗎」——`board.js` 要
 * 畫得出東西,靠的就是這三件事成立。
 *
 * 棋子代碼**不檢查**:認不得的字母會被當成一個子而占一格,格數因此仍然對得上。
 * 這是刻意的 —— 字元集訂窄了會誤擋合法變體,而那一層的權威在引擎。
 *
 * 空字串回一項未通過(欄位必填);2.5 的「清空輸入即空盤面」是組裝層對空輸入的
 * 呈現決定,不在這裡表達 —— 那一層看得到「使用者剛剛清空」這件事,本模組看不到。
 *
 * @param {string} fen 完整 FEN。
 * @returns {CheckIssue|null} 通過時為 `null`。
 */
export function checkFenStructure(fen) {
  const text = typeof fen === 'string' ? fen.trim() : '';
  if (text === '') {
    return issue('fen', '請填入 FEN');
  }

  // 以空白切欄位:盤面段在最前,走子方是第二欄。
  const fields = text.split(/\s+/);
  const rows = fields[0].split('/');
  if (rows.length !== RANKS) {
    return issue(
      'fen',
      `FEN 的盤面須有 ${RANKS} 列,目前有 ${rows.length} 列`,
    );
  }

  for (let index = 0; index < rows.length; index++) {
    const count = countFiles(rows[index]);
    if (count !== FILES) {
      // FEN 的第一列是 rank 9(黑方底線),兩種說法都給 —— 使用者對著的是 FEN
      // 字串,而盤面上的座標是 rank。
      const rank = RANKS - 1 - index;
      return issue(
        'fen',
        `FEN 第 ${index + 1} 列(rank ${rank})須有 ${FILES} 格,目前有 ${count} 格`,
      );
    }
  }

  if (fields.length < 2) {
    return issue('fen', 'FEN 缺少走子方欄位:盤面之後要接 w(紅先)或 b(黑先)');
  }
  if (!SIDE_LABELS.has(fields[1])) {
    return issue(
      'fen',
      `FEN 的走子方須是 w(紅先)或 b(黑先),目前是「${fields[1]}」`,
    );
  }

  return null;
}

/**
 * 一列 FEN 展開後占幾格:數字是連續空格數,其餘字元各占一格。
 *
 * @param {string} row FEN 盤面段以 `/` 切開後的一列。
 * @returns {number}
 */
function countFiles(row) {
  let count = 0;
  for (const character of row) {
    count += /[0-9]/.test(character) ? Number(character) : 1;
  }
  return count;
}

/**
 * 把標籤輸入切成陣列,去除空白與空項。
 *
 * 分隔符號收得寬(半形逗號、全形逗號、頓號、空白):維護者一次打好幾個標籤時用
 * 哪一個都很自然,而題庫既有的標籤全是不含空白的中文詞(`解殺還殺`、`鐵門栓`),
 * 把空白也當分隔沒有切壞任何一個的風險。
 *
 * 不去重也不排序:順序是維護者的意思,原樣送進題目檔。
 *
 * @param {string} raw 標籤欄位的原始輸入。
 * @returns {string[]}
 */
export function parseTags(raw) {
  const text = typeof raw === 'string' ? raw : '';
  return text
    .split(/[,,、\s]+/)
    .map((tag) => tag.trim())
    .filter((tag) => tag !== '');
}
