/**
 * File System Access API 的**唯一**接觸點:目錄授權與相對路徑的讀寫(6.1、6.2、6.3)。
 *
 * 收題頁的寫檔完全發生在瀏覽器端 —— 服務端不具備任何寫入題庫的能力,這是本功能
 * 不需要存取控制的全部理由(design 的 Security Considerations)。那條唯一的寫入
 * 路徑就從這個模組出去,因此它被刻意收成一個很薄的一層:**沒有任何業務規則**,
 * 只有平台的四件事 —— 這個瀏覽器行不行、要一次目錄、讀一個檔、寫一個檔。
 *
 * ## 為什麼要有這一層
 *
 * 與 `api.js` 是同一個問題的兩個版本:那邊包住 HTTP,這邊包住檔案系統。共同的理由
 * 是**平台的原始失敗形狀不得外流**。平台丟的是 `DOMException`,靠 `name` 分辨 ——
 * `AbortError`、`NotAllowedError`、`NotFoundError`、`SecurityError`。呼叫端若要自己
 * 比對這些名稱,平台細節就散進了組裝層,而組裝層也就再也不能在沒有這個 API 的
 * 環境下被測試。
 *
 * 對外因此只有兩種可辨識的失敗,而它們**必須分得開**:
 *
 * - `UnsupportedBrowserError` —— 這個瀏覽器做不到。下一步是換一個瀏覽器
 * - `PermissionDeniedError` —— 使用者拒絕或取消。下一步是再按一次寫入
 *
 * 合成同一種的話,呈現層就只剩下比對訊息字串這條路,而訊息不是契約。
 *
 * ## 目錄控制代碼只活在模組層變數裡
 *
 * 不進 IndexedDB、不進 localStorage(`research.md` Decision 4)。平台的寫入權限本來
 * 就在「該來源所有分頁關閉」時失效,持久化控制代碼救不回權限 —— 復原之後仍然需要
 * 一次使用者手勢,與重新選一次目錄的成本差別極小,卻要換來儲存、版本與失效處理。
 *
 * 反過來說,模組層變數的存續期間與平台授權的存續期間**完全吻合**:分頁在,兩者都
 * 在;分頁關了,兩者一起沒。6.1 的「本次分頁首次要求寫入時請求授權」因此不是一個
 * 需要被維護的狀態機,而是這個變數是不是 `null`。
 *
 * ## 授權請求必須落在使用者手勢的呼叫堆疊內
 *
 * 平台的硬性要求。`acquireCorpusDirectory()` 因此**不是 async 函式**,而是一個回傳
 * Promise 的普通函式:它在被呼叫的那一刻就同步把對話框開下去,中間不 `await` 任何
 * 東西。呼叫端(`editor.js`)也必須在寫入按鈕的處理常式裡呼叫它 —— design 的寫入
 * 流程把「取授權」排在撞號檢查與候選驗證之後,那幾步是 `await` 過的網路請求,手勢
 * 的有效期(數秒)是這條流程真正的上界。
 *
 * ## 這裡不做路徑驗證
 *
 * 目標路徑的範圍限制(5.2、5.3)是 `check.js` 的 `checkTargetPath` 的事。**真正的
 * 圍籬是平台**:控制代碼只能在使用者選定的目錄樹內解析路徑,`..` 這種名稱平台自己
 * 就會拒絕。在這裡再寫一份規則只會多一個真相來源,而且擋不住任何前一道沒擋住的
 * 東西。
 *
 * ## 依賴方向
 *
 * **無 import。** 只向外依賴平台。這也是收題頁其餘模組能在沒有 File System Access
 * API 的環境下被測試的原因 —— 整個功能對平台的依賴集中在這一個檔案裡。
 */

/**
 * 題庫目錄的控制代碼。取得之前是 `null`,取得之後在**這個分頁的存續期間**有效。
 *
 * 這一個變數就是「同一分頁只詢問一次目錄」的全部實作。
 *
 * @type {FileSystemDirectoryHandle|null}
 */
let corpusDirectory = null;

/**
 * 還沒完成的那一次授權請求。
 *
 * 兩個重疊的呼叫若各開一個對話框,使用者會看到兩個疊在一起的系統視窗,而選完第一個
 * 之後第二個還在那裡 —— 沒有任何說法能解釋那個畫面。所以未完成的請求也算「已經在
 * 問了」,後來者接同一個 Promise。
 *
 * **失敗時必須清掉**:6.2 要求拒絕之後保留全部內容,而那件事要有意義,就得讓維護者
 * 能再按一次寫入。把失敗留在這裡會讓第二次拿到同一個失敗,使用者就只剩下重整頁面
 * 這條路,而重整會把辛苦貼好的 FEN 一起帶走。
 *
 * @type {Promise<FileSystemDirectoryHandle>|null}
 */
let pendingRequest = null;

/**
 * 要的是讀寫權限,而且一次要齊。
 *
 * 先要讀、寫的時候再回頭要寫,那一次補要沒有使用者手勢可用(寫入發生在一連串
 * `await` 之後),平台會直接拒絕。
 */
const READWRITE = Object.freeze({ mode: 'readwrite' });

/**
 * 平台用來表達「使用者不給」的兩個名稱。
 *
 * `AbortError` 是關掉或取消了對話框,`NotAllowedError` 是在權限提示上按了封鎖。
 * 兩者對使用者是同一件事 —— 這次沒授權 —— 下一步也一樣,所以收斂成同一種失敗。
 */
const REFUSAL_NAMES = new Set(['AbortError', 'NotAllowedError']);

/** 平台用來表達「這個東西不在那裡」的名稱。 */
const NOT_FOUND = 'NotFoundError';

/**
 * 這個瀏覽器不提供由網頁選取本機資料夾的能力(6.3)。
 *
 * Firefox 與 Safari 只實作 Origin Private File System,**明確不實作**本機磁碟的
 * picker —— 不是版本落後,所以訊息裡講的是「換一個瀏覽器」而不是「更新瀏覽器」。
 */
export class UnsupportedBrowserError extends Error {
  /** @param {string} [message] 給使用者看的說法,繁體中文。 */
  constructor(
    message = '這個瀏覽器不支援由網頁寫入本機檔案,收題工具無法在此使用。'
      + '請改用桌面版的 Chrome、Edge 或 Opera。',
  ) {
    super(message);
    // 不設定的話 `name` 會是繼承來的 `'Error'`,錯誤訊息與紀錄都看不出是哪一種。
    this.name = 'UnsupportedBrowserError';
  }
}

/**
 * 使用者拒絕授權或取消了目錄選擇(6.2)。
 *
 * 與 `UnsupportedBrowserError` 分成兩個型別,是因為使用者接下來能做的事完全不同:
 * 這一種再按一次寫入就好,而且已填入的內容一個字都沒少。
 */
export class PermissionDeniedError extends Error {
  /** @param {string} [message] 給使用者看的說法,繁體中文。 */
  constructor(message = '未取得題庫目錄的存取授權,這次沒有寫入。已填入的內容都保留著。') {
    super(message);
    this.name = 'PermissionDeniedError';
  }
}

/**
 * 目前的瀏覽器是否提供本機目錄選取(6.3)。
 *
 * **沒有副作用**:頁面載入時就會呼叫它來決定要不要立刻呈現 6.3 的訊息,而那時候
 * 不可能有使用者手勢,也不該有任何對話框。
 *
 * 判準是「這個全域函式在不在」而非瀏覽器名稱字串:UA 字串會被改寫、會有新的瀏覽器
 * 加入支援,而能力偵測對兩者都不必修改。
 *
 * @returns {boolean}
 */
export function isSupported() {
  return typeof globalThis.showDirectoryPicker === 'function';
}

/**
 * 取得題庫目錄的控制代碼(6.1)。已取得者直接回傳,**不重複詢問**。
 *
 * Preconditions:只能在使用者手勢的處理常式內呼叫(平台的硬性要求)。本函式刻意
 * 不宣告為 `async` —— 它在被呼叫的那一刻就同步把對話框開下去,不讓任何 `await` 插在
 * 中間耗掉手勢的有效期。
 *
 * @returns {Promise<FileSystemDirectoryHandle>} 使用者選定的題庫目錄。
 * @throws {UnsupportedBrowserError} 這個瀏覽器沒有本機目錄選取(6.3)。
 * @throws {PermissionDeniedError} 使用者拒絕授權或取消了選擇(6.2)。
 */
export function acquireCorpusDirectory() {
  if (corpusDirectory !== null) return Promise.resolve(corpusDirectory);
  if (pendingRequest !== null) return pendingRequest;

  pendingRequest = pickCorpusDirectory().then(
    (handle) => {
      // 只有**完全成功**的結果才留下來:控制代碼到手但權限沒到手的那一種,留下來
      // 會讓後續的寫入拿著一個寫不了的控制代碼去撞牆。
      corpusDirectory = handle;
      pendingRequest = null;
      return handle;
    },
    (error) => {
      pendingRequest = null;
      throw error;
    },
  );
  return pendingRequest;
}

/**
 * 開一次目錄選擇框並把權限要齊。
 *
 * 呼叫 `showDirectoryPicker()` 的那一行必須在本函式**第一個 `await` 之前**執行 ——
 * async 函式的本體同步起跑到第一個 `await` 為止,所以這一行仍落在呼叫端的手勢
 * 呼叫堆疊內。
 *
 * @returns {Promise<FileSystemDirectoryHandle>}
 */
async function pickCorpusDirectory() {
  if (!isSupported()) throw new UnsupportedBrowserError();

  let handle;
  try {
    handle = await globalThis.showDirectoryPicker(READWRITE);
  } catch (error) {
    throw asAcquireFailure(error);
  }

  await ensureWritePermission(handle);
  return handle;
}

/**
 * 把平台在取得授權過程中丟出的東西翻成本模組的失敗。
 *
 * **只翻使用者不給的那兩種**,其餘原樣往上。特別是 `SecurityError`:那代表呼叫時
 * 沒有有效的使用者手勢,是呼叫端的 bug(6.1 要求授權請求掛在寫入的點擊處理常式
 * 內)。把它折成「你拒絕了授權」會讓維護者一直去按允許,而畫面永遠不會變。
 *
 * @param {unknown} error 平台丟出來的東西。
 * @returns {unknown} 要往上丟的錯誤。
 */
function asAcquireFailure(error) {
  const name = error == null ? undefined : error.name;
  return REFUSAL_NAMES.has(name) ? new PermissionDeniedError() : error;
}

/**
 * 確認這個控制代碼真的可以寫。
 *
 * `showDirectoryPicker({mode: 'readwrite'})` 在 Chrome 上已經會問一次寫入權限,所以
 * 這通常是一次 `queryPermission()` 就結束的確認。留著 `requestPermission()` 那條路
 * 是因為「控制代碼到手」與「權限到手」在規格上是兩件事,而在這裡補要仍然落在同一個
 * 使用者手勢內 —— 換到別的地方要就來不及了。
 *
 * 兩個方法都是**可選的**:替身或未來的平台若沒有提供,就以 picker 的結果為準。
 * 在這裡自己造一個「拒絕」出來,只會把一個能寫的目錄講成不能寫。
 *
 * @param {FileSystemDirectoryHandle} handle
 * @returns {Promise<void>}
 * @throws {PermissionDeniedError} 使用者沒有給寫入權限。
 */
async function ensureWritePermission(handle) {
  if (typeof handle.queryPermission === 'function') {
    if ((await handle.queryPermission(READWRITE)) === 'granted') return;
  }
  if (typeof handle.requestPermission !== 'function') return;

  let state;
  try {
    state = await handle.requestPermission(READWRITE);
  } catch (error) {
    throw asAcquireFailure(error);
  }
  if (state !== 'granted') throw new PermissionDeniedError();
}

/**
 * 讀取相對路徑的檔案全文;**檔案不存在時回傳 `null` 而非拋出**。
 *
 * 這個回傳值是 `corpus-file.js` 已經依賴的契約:`null` 是「建一個新檔」(5.4),
 * `''` 是「這個檔不是題目陣列」(5.6)。一種狀態只有一個表示法 —— 不存在若走例外,
 * 呼叫端就得去辨認平台的錯誤名稱,平台細節也就漏了出去。
 *
 * 路徑中間的資料夾不存在時同樣回 `null`:那個檔案確實不在那裡,不是另一種失敗。
 *
 * **不建立任何東西。** 一次讀不到的讀取不該在磁碟上留下空資料夾或空檔案。
 *
 * @param {FileSystemDirectoryHandle} dir 題庫目錄的控制代碼。
 * @param {string} relativePath 相對於題庫根目錄的路徑,例如 `適情雅趣~卷一/26.json`。
 * @returns {Promise<string|null>} 檔案全文;不存在時為 `null`。
 */
export async function readTextAt(dir, relativePath) {
  const segments = splitPath(relativePath);
  const name = segments.pop();

  try {
    const parent = await resolveDirectory(dir, segments);
    const fileHandle = await parent.getFileHandle(name);
    const file = await fileHandle.getFile();
    return await file.text();
  } catch (error) {
    // 整段走訪都納入:資料夾不在、檔案不在、取檔案時剛好被刪掉,對呼叫端都是
    // 同一個答案。其餘的失敗(權限沒了、磁碟壞了)原樣往上 —— 那些不是「不存在」。
    if (error != null && error.name === NOT_FOUND) return null;
    throw error;
  }
}

/**
 * 寫入相對路徑的檔案。**回傳時內容已落盤**(串流已 `close()`)。
 *
 * 平台的 `createWritable()` 在 `close()` 之前不落盤,所以「有沒有等到 close」就是
 * 「呼叫端能不能相信寫完了」。7.1 的成功訊息、以及成功之後才記下題號(4.4),都
 * 建立在這一點上。
 *
 * **整檔覆寫**:內容是 `appendPosition()` 的輸出,既有題目的逐字不變(5.7)由那個
 * 輸出保證,不由寫入方式保證。`createWritable()` 預設不保留原有內容,正是要的行為。
 *
 * **只建目標檔,不建中間資料夾。** 書目資料夾不存在就是寫入失敗 —— 憑一個打錯的
 * 路徑在題庫裡長出一個沒人要的資料夾,比一則錯誤訊息難發現得多。
 *
 * @param {FileSystemDirectoryHandle} dir 題庫目錄的控制代碼。
 * @param {string} relativePath 相對於題庫根目錄的路徑。
 * @param {string} text 要寫入的全文。
 * @returns {Promise<void>} 兌現時內容已在磁碟上。
 */
export async function writeTextAt(dir, relativePath, text) {
  const segments = splitPath(relativePath);
  const name = segments.pop();

  const parent = await resolveDirectory(dir, segments);
  const fileHandle = await parent.getFileHandle(name, { create: true });
  const writable = await fileHandle.createWritable();

  try {
    await writable.write(text);
  } catch (error) {
    // 寫到一半失敗就**放棄**這個串流。改用 `close()` 收尾會把半份內容落盤,而那
    // 正好是最糟的結果 —— 一個解析不開的題目檔會讓服務下次啟動直接拒絕啟動。
    await abandon(writable);
    throw error;
  }

  await writable.close();
}

/**
 * 靜靜地放棄一個寫入串流。
 *
 * `abort()` 自己再失敗也不能蓋掉原本的錯誤 —— 使用者要知道的是「為什麼寫不進去」,
 * 不是「收拾殘局的時候又發生了什麼」。
 *
 * @param {FileSystemWritableFileStream} writable
 * @returns {Promise<void>}
 */
async function abandon(writable) {
  try {
    if (typeof writable.abort === 'function') await writable.abort();
  } catch {
    // 刻意吞掉:見上方說明。
  }
}

/**
 * 把相對路徑切成一段一段。
 *
 * **只去掉空的段**(開頭、結尾或連續的 `/`)。`.` 與 `..` 原封不動交給平台 ——
 * 這裡不做路徑驗證(見模組說明),平台自己就會拒絕它們。
 *
 * @param {string} relativePath
 * @returns {string[]} 至少有一段。
 * @throws {TypeError} 路徑裡一段也沒有 —— 那是呼叫端的 bug,不是使用者的錯誤。
 */
function splitPath(relativePath) {
  const segments = String(relativePath ?? '')
    .split('/')
    .filter((segment) => segment !== '');
  if (segments.length === 0) {
    throw new TypeError(`relativePath 必須指到一個檔案,卻是 ${JSON.stringify(relativePath)}`);
  }
  return segments;
}

/**
 * 逐段走到目標檔所在的資料夾。
 *
 * **一律不帶 `create`**:中間的資料夾必須本來就在(見 `writeTextAt` 的說明)。
 *
 * @param {FileSystemDirectoryHandle} dir 起點。
 * @param {string[]} segments 目標檔之前的每一段。
 * @returns {Promise<FileSystemDirectoryHandle>}
 */
async function resolveDirectory(dir, segments) {
  let current = dir;
  for (const segment of segments) {
    current = await current.getDirectoryHandle(segment);
  }
  return current;
}
