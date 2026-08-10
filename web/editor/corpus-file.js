/**
 * 一題在題庫檔中**長什麼樣**,以及如何把它接到既有檔案文字之後。
 *
 * 本模組不碰檔案系統、不碰 DOM、不發請求:輸入輸出全是字串,因此能以
 * `page.evaluate()` 單獨驗證(`tests/test_web_editor_pure.py`)。落盤是 `fs.js` 的事。
 *
 * ## 既有題目逐字不變是**構造上**的事實,不是一個需要被證明的性質
 *
 * 5.7 要求寫入後目標檔既有的每一題逐字不變。要做到這件事有兩條路:
 *
 * 1. 讀檔 → `JSON.parse` → 陣列末端 push → 整份重新序列化寫回
 * 2. 讀檔文字 → 驗證它可解析為陣列 → 在收尾的 `]` 之前插入新題的**文字**
 *
 * 兩者的差別不在難易,而在 5.7 憑什麼成立。方案 1 的 5.7 是一個**性質**:序列化器
 * 對每一種既有輸入都剛好還原。那要對每一則描述的每一個轉義、每一個數字的每一種寫法
 * 都成立,而只要有一天不成立,壞掉的是**別人已經收好的題目** —— 一個沒有人會去看的
 * 檔案的中段,悄悄變了樣。方案 2 的 5.7 是**構造上的事實**:既有位元組原樣被搬過去,
 * 根本沒有被重寫的機會。本模組採方案 2(`research.md` 的 Decision 2)。
 *
 * 因此:**`JSON.parse` 只用來驗證**目標檔是不是一個陣列(5.6),它的結果**不參與
 * 輸出**。這條規矩沒有例外 —— 一旦有人「順手」拿解析後的陣列去組輸出,5.7 就從事實
 * 退回成性質,而測試不會立刻紅(解析後等價的輸出照樣解得開),要等到某一題的排版
 * 被改掉才有人發現。
 *
 * ## 序列化只涵蓋人工編輯的欄位
 *
 * 寫出的是 `id`、`title`、`description`、`fen`、`difficulty`、`tags` 六個(5.9),
 * 順序與既有題目檔一致。刻意**不寫**的有三個,每一個都有各自的理由:
 *
 * - `max_dtm` —— 最長殺著距離由驗證工具(corpus-verification)算出來回填,收題時
 *   還沒有人知道它是多少,寫一個猜的值比空著更糟
 * - `source` —— 出處由**所在資料夾**表達(`service/positions.py` 的
 *   `_source_of_path()`),它根本不是題目的欄位
 * - `side_to_move` —— 起手方由 **FEN** 表達,同理不是欄位
 *
 * 後兩者若寫進去,服務**啟動時**會因未知欄位而拒絕整個題庫 —— 代價不是一題壞掉,
 * 是整個站台連不開。
 *
 * ## 排版跟著既有題目檔走
 *
 * 兩格縮排、每個欄位各自一行、`tags` 維持**單行**、中文不轉義、檔案以換行結尾。
 * `JSON.stringify(value, null, 2)` 給不出這個形狀 —— 它會把 `tags` 攤成一行一個標籤,
 * 讓一題從 8 行變成 12 行以上。所以序列化是手寫的,而它的正確性由既有題庫檔的回歸
 * 比對釘住(每一題重新序列化後與原檔片段逐字相同)。
 *
 * 排版慣例哪天真的要改,順序是:先改題庫檔,回歸比對轉紅,再改這裡。反過來做會讓
 * 新題與既有題目長得不一樣,而那是沒有任何測試會抓到的。
 *
 * ## 依賴方向
 *
 * **無依賴。** 這是本模組能被單獨載入驗證的原因,也是它與 `check.js` 的差別
 * (後者要 `fen.js` 的常數)。
 */

/** 題目 schema 中**由人工編輯**的欄位,順序即寫進題目檔的順序。 */
const SCHEMA_FIELDS = ['id', 'title', 'description', 'fen', 'difficulty', 'tags'];

/** 題目物件的縮排(在陣列裡一層)。 */
const ENTRY_INDENT = '  ';

/** 欄位的縮排(在題目物件裡再一層)。 */
const FIELD_INDENT = '    ';

/**
 * @typedef {{id: number, title: string, description: string, fen: string,
 *            difficulty: number, tags: string[]}} PositionEntry
 */

/**
 * 目標檔的內容不是題目陣列(5.6)。
 *
 * 自成一個型別而不是丟一般的 `Error`:組裝層要分得出「這個檔不能寫」與「寫入本身
 * 失敗」——前者使用者自己處理得了(換一個檔、或先把那個檔修好),後者不行,兩者
 * 的訊息因此不一樣。
 */
export class CorpusFileError extends Error {
  /** @param {string} message 給使用者看的說法,繁體中文。 */
  constructor(message) {
    super(message);
    // 不設定的話 `name` 會是繼承來的 `'Error'`,錯誤訊息與紀錄都看不出是哪一種。
    this.name = 'CorpusFileError';
  }
}

/**
 * 一題的文字,含兩格縮排,**不含**結尾換行與陣列分隔用的逗號。
 *
 * 那兩者屬「這一題與別題之間」而不屬這一題,由 `appendPosition()` 依前後文補上 ——
 * 一題的文字若自帶逗號,空陣列那一種形態就得再把它拿掉。
 *
 * Preconditions:`entry` 的欄位已通過服務端驗證(`POST /api/editor/validate`),
 * 本模組**不重複驗證**。多帶的欄位(例如呼叫端順手塞進來的 `max_dtm`)一律不寫出,
 * 這不是驗證而是 5.9 的直接後果:寫出的欄位是一份**寫死的清單**,不是輸入的翻版。
 *
 * @param {PositionEntry} entry 要寫進題庫檔的一題。
 * @returns {string} 例如 `  {\n    "id": 26,\n    ...\n  }`。
 */
export function serializePosition(entry) {
  const source = entry == null ? {} : entry;
  const lines = SCHEMA_FIELDS.map(
    (name) => `${FIELD_INDENT}${quote(name)}: ${valueText(name, source[name])}`,
  );
  return `${ENTRY_INDENT}{\n${lines.join(',\n')}\n${ENTRY_INDENT}}`;
}

/**
 * 一個欄位的值寫成文字。
 *
 * `tags` 是唯一需要特別處理的:它必須留在**同一行**(5.8),而通用的 JSON 序列化
 * 一縮排就會把陣列攤開。其餘欄位交給 `JSON.stringify` —— 字串的轉義規則(引號、
 * 反斜線、換行)有一份標準答案,自己寫一份只會漏掉某個字元。**中文不會被轉義**:
 * `JSON.stringify` 只轉義控制字元與 `"` `\`,非 ASCII 一律原樣輸出。
 *
 * @param {string} name 欄位名。
 * @param {unknown} value 欄位值。
 * @returns {string}
 */
function valueText(name, value) {
  if (name === 'tags') {
    const tags = Array.isArray(value) ? value : [];
    return `[${tags.map((tag) => quote(tag)).join(', ')}]`;
  }
  return JSON.stringify(value);
}

/**
 * 一個字串寫成 JSON 的字串字面值。
 *
 * @param {unknown} value
 * @returns {string}
 */
function quote(value) {
  return JSON.stringify(String(value));
}

/**
 * 追加一題並回傳新的檔案全文(5.4、5.5、5.7)。
 *
 * 三種形態各有各的收尾方式:
 *
 * | 既有內容 | 判定 | 做法 |
 * |---|---|---|
 * | 不存在 | `existing` 為 `null` | 產出只含這一題的陣列(5.4) |
 * | 空陣列 | 解析後長度為 0 | 插入單一元素,**不補逗號** |
 * | 非空陣列 | 解析後長度大於 0 | 為前一個元素補逗號後插入(5.5) |
 * | 非陣列 | 解析失敗或不是陣列 | 拋 `CorpusFileError`,不寫入(5.6) |
 *
 * **輸出以 `existing` 的每一個位元組為前綴**,直到收尾的 `]` 之前(5.7)。被換掉的
 * 只有 `]` 與其前後的空白 —— 分隔用的逗號要插在最後一題的 `}` 之後,而那個位置本來
 * 就只有空白,沒有任何一題的內容在那裡。既有的每一題因此逐字不變(7.4),也一題都
 * 不會少(7.5)。
 *
 * 空陣列與非空陣列的差別只在那個逗號,所以兩者共用同一段程式:`]` 之前是空的就不補。
 *
 * @param {string|null} existing 目標檔既有全文;檔案不存在時傳 `null`。
 * @param {PositionEntry} entry 要追加的一題。
 * @returns {string} 新的檔案全文,以換行結尾。
 * @throws {CorpusFileError} 既有內容無法解析為 JSON 陣列。
 */
export function appendPosition(existing, entry) {
  const block = serializePosition(entry);
  if (existing == null) {
    return `[\n${block}\n]\n`;
  }

  // `JSON.parse` **只用來驗證**(5.6)。它的結果從這裡之後就不再被碰 —— 輸出組的是
  // `existing` 的原始文字,不是解析出來的值。
  if (!isPositionArray(existing)) {
    throw new CorpusFileError(
      '目標檔的內容不是題目陣列,無法追加。題目檔必須是標準 JSON 的陣列。',
    );
  }

  // 收尾的 `]` 之前的一切,原樣留著。`trimEnd()` 去掉的是 `}` 與 `]` 之間的換行與
  // 縮排 —— 那段空白不屬於任何一題,而逗號要接在 `}` 的正後方。
  const kept = existing.slice(0, existing.lastIndexOf(']')).trimEnd();

  // 前面還有東西就代表有前一個元素,要補逗號;空陣列的 `kept` 只剩下 `[`。
  const separator = kept === '[' ? '' : ',';
  return `${kept}${separator}\n${block}\n]\n`;
}

/**
 * 這份文字是不是一個 JSON 陣列。
 *
 * 判準**只到「是不是陣列」為止**:元素是不是合格的題目由服務端判定,在這裡多加一道
 * 會製造第二個真相來源,還會讓一個本來寫得進去的檔案被擋下。
 *
 * 含註解、尾隨逗號等非標準 JSON 的檔案會解析失敗而被擋下,這是要的行為 ——
 * 題庫檔本就必須是標準 JSON,不然服務啟動時一樣讀不進去。空檔案同理:它不是陣列,
 * 追加不成立,由呼叫端把訊息給使用者看。
 *
 * @param {string} text
 * @returns {boolean}
 */
function isPositionArray(text) {
  try {
    return Array.isArray(JSON.parse(text));
  } catch {
    return false;
  }
}
