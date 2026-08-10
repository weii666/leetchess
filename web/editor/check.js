/**
 * 收題頁的**淺層檢查**與描述建議值。
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
 * FEN 走子方那一欄的取值與其顯示字樣。
 *
 * 中國象棋 FEN 沿用國際象棋的 `w`/`b`,紅方即 `w`(與 `service/positions.py` 的
 * `FEN_SIDES` 同一份約定)。用 `Map` 而非物件字面值:物件查得到 `constructor`、
 * `toString` 這些原型上的東西,一個叫 `toString` 的走子方會拿到一個函式當字樣。
 */
const SIDE_LABELS = new Map([
  ['w', '紅先'],
  ['b', '黑先'],
]);

/** 局號用的中文數字。**逐字**書寫,`25` 是「二五」而不是「二十五」。 */
const CHINESE_DIGITS = ['〇', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

/** 題目檔的副檔名。題庫的題目檔一律是 JSON(`structure.md` 的目錄佈局)。 */
const PUZZLE_FILE_SUFFIX = '.json';

/**
 * @typedef {'id'|'title'|'description'|'difficulty'|'tags'|'fen'|'target'|null} CheckField
 * @typedef {{field: CheckField, message: string}} CheckIssue
 * @typedef {{id: string, title: string, description: string, difficulty: string,
 *            tags: string, fen: string, target: string}} FormValues
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
 * 清單順序即表單欄位順序(8.4):題號、局名、描述、難度、標籤、FEN、目標檔案路徑,
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

  // --- 局名、描述、難度(4.1)---
  // 難度只檢查「有沒有選」。1/2/3 的值域出自 `structure.md`,由畫面上的三選一
  // 控制項保證,不在這裡再寫一份 —— 那會變成第二個真相來源。
  if (read(values, 'title') === '') {
    issues.push(issue('title', '請填入局名'));
  }
  if (read(values, 'description') === '') {
    issues.push(issue('description', '請填入描述'));
  }
  if (read(values, 'difficulty') === '') {
    issues.push(issue('difficulty', '請選擇難度'));
  }

  // --- 標籤(4.6)---
  if (parseTags(read(values, 'tags')).length === 0) {
    issues.push(issue('tags', '標籤至少需要一個'));
  }

  // --- FEN 與目標路徑(2.4、5.1–5.3)---
  // 兩者各自的檢查已經回傳定位到欄位的項目,直接接上即可,不必在此重寫說法。
  const fenIssue = checkFenStructure(read(values, 'fen'));
  if (fenIssue !== null) {
    issues.push(fenIssue);
  }
  const targetIssue = checkTargetPath(read(values, 'target'));
  if (targetIssue !== null) {
    issues.push(targetIssue);
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
 * 畫得出東西、`sideFromFen()` 要讀得到起手方,靠的就是這三件事成立。
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
 * 由 FEN 的走子方欄位取得起手方顯示字樣(2.6)。
 *
 * 起手方**沒有獨立輸入**,只有這一條來源。判不出來時回 `null` 而不是猜一個 ——
 * 題庫容得下黑先的排局,預設成紅先會讓那些題目靜默地標錯。
 *
 * 不先跑結構檢查:走子方那一欄讀不讀得到,與盤面段列數對不對是兩件事,一個結構
 * 不完整的 FEN 仍可能已經打完走子方,先給出起手方沒有壞處。
 *
 * @param {string} fen 完整 FEN。
 * @returns {'紅先'|'黑先'|null}
 */
export function sideFromFen(fen) {
  const text = typeof fen === 'string' ? fen.trim() : '';
  if (text === '') {
    return null;
  }
  const fields = text.split(/\s+/);
  if (fields.length < 2) {
    return null;
  }
  const label = SIDE_LABELS.get(fields[1]);
  return label === undefined ? null : label;
}

/**
 * 目標路徑檢查:須在題庫目錄內、須位於書目資料夾內、須為題目檔(5.1、5.2、5.3)。
 *
 * 路徑**相對於題庫根目錄**(5.1),例如 `適情雅趣~卷一/26.json`。
 *
 * 「書目資料夾內」的判準是**結構**:至少兩段(資料夾 + 檔名),與
 * `service/positions.py` 的 `_source_of_path()` 同一條規則 —— 出處由所在資料夾表達,
 * 直接躺在題庫根目錄的題目沒有出處可言,服務啟動時會拒絕。**該規則的權威在後端**,
 * 此處是為了在送出前就給回饋。
 *
 * 判準刻意不是「已知書目的白名單」:收下一本新書時不該回頭改這個函式,而白名單
 * 也擋不住任何真正的問題 —— 一個沒見過的資料夾名稱本來就是合法的。
 *
 * 至於「跳出題庫目錄」(5.3),平台本身其實跳不出去 —— File System Access API 的
 * 控制代碼只能在使用者選定的目錄樹內解析路徑。這裡擋 `..` 與絕對路徑是為了給出
 * 清楚的訊息,**不是唯一防線**。
 *
 * @param {string} target 相對於題庫根目錄的路徑。
 * @returns {CheckIssue|null} 通過時為 `null`。
 */
export function checkTargetPath(target) {
  const text = typeof target === 'string' ? target.trim() : '';
  if (text === '') {
    return issue('target', '請填入目標檔案路徑,例如 適情雅趣~卷一/26.json');
  }
  if (text.startsWith('/')) {
    return issue('target', '路徑相對於題庫根目錄,不要以 / 開頭');
  }

  const segments = text.split('/');
  if (segments.includes('..')) {
    return issue('target', '只能寫入題庫目錄內,路徑不得含有 ..');
  }
  // 空白段落(`a//b.json`、結尾的 `/`)與 `.` 都不是有意義的一段,與其猜使用者的
  // 意思不如講清楚 —— 猜錯會寫到別的檔案去。
  if (segments.some((segment) => segment === '' || segment === '.')) {
    return issue('target', '路徑的每一段都要有名字,不得含有空白段落或 .');
  }

  const filename = segments[segments.length - 1];
  if (!filename.endsWith(PUZZLE_FILE_SUFFIX) || filename === PUZZLE_FILE_SUFFIX) {
    return issue('target', `目標必須是題目檔,檔名以 ${PUZZLE_FILE_SUFFIX} 結尾`);
  }
  if (segments.length < 2) {
    return issue(
      'target',
      '題目必須放在書目資料夾內,出處由資料夾表達,例如 適情雅趣~卷一/26.json',
    );
  }

  return null;
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

/**
 * 描述的建議值(3.6),例如「適情雅趣 第二五局 患在几席」。
 *
 * 寫法沿用既有題目:`<書名> 第<局號>局 <局名>`,局號採**逐字**中文數字 ——
 * `25` 是「二五」不是「二十五」,`20` 是「二〇」不是「二十」。這不是美觀問題:
 * 題庫既有的每一則描述都是這個寫法,換一種寫法就會讓同一本書的描述前後不一致。
 *
 * 建議值**只是建議**(3.7):最終值以維護者輸入的內容為準,本函式不知道也不在乎
 * 它有沒有被改寫。
 *
 * 湊不出建議值時回空字串(書名或局名為空、題號不是正整數)—— 不拋例外,呼叫端
 * 每次輸入變動都會問一次。
 *
 * @param {string} source 書名。**不是資料夾名稱** —— 資料夾帶卷次
 *   (`適情雅趣~卷一`)而描述只寫書名,兩者的換算屬組裝層。
 * @param {number} id 題號,正整數。
 * @param {string} title 局名。
 * @returns {string} 湊不出來時為空字串。
 */
export function suggestDescription(source, id, title) {
  const book = typeof source === 'string' ? source.trim() : '';
  const name = typeof title === 'string' ? title.trim() : '';
  const number = chineseNumber(id);
  if (book === '' || name === '' || number === null) {
    return '';
  }
  return `${book} 第${number}局 ${name}`;
}

/**
 * 把題號寫成逐字的中文數字;不是正整數時回 `null`。
 *
 * @param {unknown} id
 * @returns {string|null}
 */
function chineseNumber(id) {
  if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) {
    return null;
  }
  return String(id)
    .split('')
    .map((digit) => CHINESE_DIGITS[Number(digit)])
    .join('');
}
