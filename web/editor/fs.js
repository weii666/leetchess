/**
 * File System Access API 的**唯一**接觸點:目標題目檔的選定與讀寫(5.1、5.11、
 * 6.1、6.2、6.3)。
 *
 * 收題頁的寫檔完全發生在瀏覽器端 —— 服務端不具備任何寫入題庫的能力,這是本功能
 * 不需要存取控制的全部理由(design 的 Security Considerations)。那條唯一的寫入
 * 路徑就從這個模組出去,因此它被刻意收成一個很薄的一層:**沒有任何業務規則**,
 * 只有平台的四件事 —— 這個瀏覽器行不行、選一個檔、讀它、寫它。
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
 * - `PermissionDeniedError` —— 使用者拒絕或取消。下一步是再選一次檔
 *
 * 合成同一種的話,呈現層就只剩下比對訊息字串這條路,而訊息不是契約。
 *
 * ## 為什麼是「一個檔」而不是「一個目錄」
 *
 * 原本的做法是授權整個題庫目錄,再由組裝層以相對路徑定位目標檔。人工驗收時發現它
 * 反直覺:對話框問的是「哪個資料夾」,而畫面問的是「哪個檔案」,同一件事被拆成兩個
 * 步驟問兩次(requirements R5 的修訂)。改成直接選那一個 `.json` 之後:
 *
 * - **整層路徑解析消失**。本模組不再有 `splitPath` 與 `resolveDirectory`
 * - **打錯路徑寫到別的檔在構造上不再存在** —— 沒有路徑可以打錯
 * - **授權範圍從整個題庫目錄縮到單一檔案**,符合最小權限
 *
 * ## 這裡不做路徑判斷,也做不到
 *
 * `FileSystemFileHandle` 只帶得出 `name`(檔名),**不帶所在位置**。「這個檔在不在
 * 題庫裡」因此不是本層回答得了的問題,而那正是這個設計換來的代價。取而代之的保護
 * 有兩道:維護者在系統對話框裡**親眼看見**自己選了什麼(比手打路徑更難出錯),以及
 * `corpus-file.js` 的「不是題目陣列就不寫」(5.6)。
 *
 * ## 控制代碼只活在模組層變數裡
 *
 * 不進 IndexedDB、不進 localStorage(`research.md` Decision 4)。平台的寫入權限本來
 * 就在「該來源所有分頁關閉」時失效,持久化控制代碼救不回權限 —— 復原之後仍然需要
 * 一次使用者手勢,與重新選一次檔的成本差別極小,卻要換來儲存、版本與失效處理。
 *
 * 反過來說,模組層變數的存續期間與平台授權的存續期間**完全吻合**:分頁在,兩者都
 * 在;分頁關了,兩者一起沒。6.1 的「同一次分頁使用期間沿用,不重複詢問」因此不是
 * 一個需要被維護的狀態機,而是這個變數是不是 `null`。
 *
 * ## 選檔必須落在使用者手勢的呼叫堆疊內
 *
 * 平台的硬性要求。`pickCorpusFile()` 因此**不是 async 函式**,而是一個回傳 Promise
 * 的普通函式:它在被呼叫的那一刻就同步把對話框開下去,中間不 `await` 任何東西。
 *
 * **而這一次呼叫端天然做得到**:選檔是使用者主動按下的一個獨立操作,不在寫入序列
 * 內。舊設計把授權排在撞號與驗證之後,那幾步是 `await` 過的網路請求,而實測手勢的
 * 有效期只有約 5 秒(見 `tasks.md` 對 5.3 的筆記)—— 一次引擎池滿就會讓對話框開不
 * 起來。**那個缺口隨這次修訂消失了。**
 *
 * ## 依賴方向
 *
 * **無 import。** 只向外依賴平台。這也是收題頁其餘模組能在沒有 File System Access
 * API 的環境下被測試的原因 —— 整個功能對平台的依賴集中在這一個檔案裡。
 */

/**
 * 目前選定的題目檔。選定之前是 `null`,選定之後在**這個分頁的存續期間**有效。
 *
 * 這一個變數就是「同一次分頁使用期間沿用,不重複詢問」的全部實作(6.1)。
 *
 * @type {FileSystemFileHandle|null}
 */
let corpusFile = null;

/**
 * 還沒完成的那一次選檔請求。
 *
 * 兩個重疊的呼叫若各開一個對話框,使用者會看到兩個疊在一起的系統視窗,而選完第一個
 * 之後第二個還在那裡 —— 沒有任何說法能解釋那個畫面。所以未完成的請求也算「已經在
 * 問了」,後來者接同一個 Promise。
 *
 * **失敗時必須清掉**:6.2 要求拒絕之後保留全部內容,而那件事要有意義,就得讓維護者
 * 能再選一次。把失敗留在這裡會讓第二次拿到同一個失敗,使用者就只剩下重整頁面這條
 * 路,而重整會把辛苦貼好的 FEN 一起帶走。
 *
 * @type {Promise<FileSystemFileHandle>|null}
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
 * 檔案選擇框的選項。
 *
 * `id` 讓瀏覽器記住上一次的資料夾 —— 連續收題抄的是同一本書的同一卷,第二次開起來
 * 就在對的位置。`multiple: false` 因為一次只寫一個檔;型別過濾限 `.json`,好讓維護者
 * 在滿是圖片與文字檔的資料夾裡一眼看到題目檔。
 *
 * **刻意不用 `showSaveFilePicker`**:它對既有檔案會問「是否取代」,而本工具的常態是
 * 追加。新開一個題目檔屬另一個操作,本輪不做(requirements 5.4 移出範圍)。
 */
const PICKER_OPTIONS = Object.freeze({
  id: 'leetchess-corpus',
  multiple: false,
  types: [
    Object.freeze({
      description: '題目檔',
      accept: Object.freeze({ 'application/json': Object.freeze(['.json']) }),
    }),
  ],
});

/**
 * 平台用來表達「使用者不給」的兩個名稱。
 *
 * `AbortError` 是關掉或取消了對話框,`NotAllowedError` 是在權限提示上按了封鎖。
 * 兩者對使用者是同一件事 —— 這次沒選到檔 —— 下一步也一樣,所以收斂成同一種失敗。
 */
const REFUSAL_NAMES = new Set(['AbortError', 'NotAllowedError']);

/**
 * 這個瀏覽器不提供由網頁選取本機檔案並寫回的能力(6.3)。
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
 * 使用者拒絕授權或取消了檔案選擇(6.2)。
 *
 * 與 `UnsupportedBrowserError` 分成兩個型別,是因為使用者接下來能做的事完全不同:
 * 這一種再選一次就好,而且已填入的內容一個字都沒少。
 */
export class PermissionDeniedError extends Error {
  /** @param {string} [message] 給使用者看的說法,繁體中文。 */
  constructor(message = '未取得題目檔的寫入授權,這次沒有選定檔案。已填入的內容都保留著。') {
    super(message);
    this.name = 'PermissionDeniedError';
  }
}

/**
 * 目前的瀏覽器是否提供本機檔案選取(6.3)。
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
  return typeof globalThis.showOpenFilePicker === 'function';
}

/**
 * 目前選定的題目檔;尚未選定時為 `null`(5.10)。
 *
 * **不開任何對話框,也沒有任何副作用** —— 因此任何時候都呼叫得起,包括每一次重畫。
 * 組裝層以它回答兩個問題:檔名要顯示什麼,以及寫入按得下去嗎。
 *
 * @returns {FileSystemFileHandle|null}
 */
export function selectedFile() {
  return corpusFile;
}

/**
 * 開一次檔案選擇框,選定目標題目檔並取得寫入授權(5.1、5.11、6.1)。
 *
 * **每次呼叫都重新詢問** —— 它同時就是 5.11 的「更換目標題目檔」。不重複詢問那件事
 * (6.1)由呼叫端負責:組裝層以 `selectedFile()` 判斷有沒有選過,只在使用者按下選檔
 * 按鈕時才呼叫本函式。這條分工讓本模組不必猜「這一次是初次選定還是要換檔」。
 *
 * Preconditions:只能在使用者手勢的處理常式內呼叫(平台的硬性要求)。本函式刻意
 * 不宣告為 `async` —— 它在被呼叫的那一刻就同步把對話框開下去,不讓任何 `await` 插在
 * 中間耗掉手勢的有效期。
 *
 * @returns {Promise<FileSystemFileHandle>} 使用者選定的題目檔。
 * @throws {UnsupportedBrowserError} 這個瀏覽器沒有本機檔案選取(6.3)。
 * @throws {PermissionDeniedError} 使用者拒絕授權或取消了選擇(6.2)。
 */
export function pickCorpusFile() {
  if (pendingRequest !== null) return pendingRequest;

  pendingRequest = openPicker().then(
    (handle) => {
      // 只有**完全成功**的結果才留下來:控制代碼到手但權限沒到手的那一種,留下來
      // 會讓後續的寫入拿著一個寫不了的控制代碼去撞牆。
      corpusFile = handle;
      pendingRequest = null;
      return handle;
    },
    (error) => {
      // 取消換檔**不動已選定的那一個**:維護者按了更換又改變主意,原本那個檔應該
      // 還在。把它一起清掉等於用一次取消把上一次的選擇也撤銷了。
      pendingRequest = null;
      throw error;
    },
  );
  return pendingRequest;
}

/**
 * 開一次檔案選擇框並把權限要齊。
 *
 * 呼叫 `showOpenFilePicker()` 的那一行必須在本函式**第一個 `await` 之前**執行 ——
 * async 函式的本體同步起跑到第一個 `await` 為止,所以這一行仍落在呼叫端的手勢
 * 呼叫堆疊內。
 *
 * @returns {Promise<FileSystemFileHandle>}
 */
async function openPicker() {
  if (!isSupported()) throw new UnsupportedBrowserError();

  let handles;
  try {
    handles = await globalThis.showOpenFilePicker(PICKER_OPTIONS);
  } catch (error) {
    throw asPickFailure(error);
  }

  // 平台回的是陣列(它支援多選,我們關掉了)。空陣列在規格上不該發生,但真發生時
  // 當成「沒有選到」比讓 `undefined` 流下去好 —— 後者會在寫檔那一步才炸開。
  const handle = Array.isArray(handles) ? handles[0] : handles;
  if (handle == null) throw new PermissionDeniedError();

  await ensureWritePermission(handle);
  return handle;
}

/**
 * 把平台在選檔過程中丟出的東西翻成本模組的失敗。
 *
 * **只翻使用者不給的那兩種**,其餘原樣往上。特別是 `SecurityError`:那代表呼叫時
 * 沒有有效的使用者手勢,是呼叫端的 bug。把它折成「你拒絕了授權」會讓維護者一直去
 * 按允許,而畫面永遠不會變。
 *
 * @param {unknown} error 平台丟出來的東西。
 * @returns {unknown} 要往上丟的錯誤。
 */
function asPickFailure(error) {
  const name = error == null ? undefined : error.name;
  return REFUSAL_NAMES.has(name) ? new PermissionDeniedError() : error;
}

/**
 * 確認這個控制代碼真的可以寫。
 *
 * `showOpenFilePicker()` 給的是**唯讀**權限 —— 與目錄選擇框不同,它沒有 `mode` 選項,
 * 所以寫入權限一定要在這裡補要。而補要仍然落在同一個使用者手勢內,換到別的地方要
 * 就來不及了。
 *
 * 兩個方法都是**可選的**:替身或未來的平台若沒有提供,就以 picker 的結果為準。
 * 在這裡自己造一個「拒絕」出來,只會把一個能寫的檔講成不能寫。
 *
 * @param {FileSystemFileHandle} handle
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
    throw asPickFailure(error);
  }
  if (state !== 'granted') throw new PermissionDeniedError();
}

/**
 * 讀取選定檔案的全文。
 *
 * **不回 `null`**:這個檔是使用者剛剛在對話框裡選出來的,它存在。與舊的
 * `readTextAt()` 刻意不同 —— 那時候路徑是打出來的,「不存在」是一個正常的結果
 * (5.4 的建新檔),而現在沒有那條路(5.4 移出範圍)。
 *
 * 選檔之後檔案被外部刪掉或改名時,平台會拋出 —— 那是**失敗**而不是一種正常狀態,
 * 原樣往上交由 7.3 的一般寫入失敗處理。
 *
 * @param {FileSystemFileHandle} handle 選定的題目檔。
 * @returns {Promise<string>} 檔案全文。
 */
export async function readText(handle) {
  const file = await handle.getFile();
  return await file.text();
}

/**
 * 寫入選定的檔案。**回傳時內容已落盤**(串流已 `close()`)。
 *
 * 平台的 `createWritable()` 在 `close()` 之前不落盤,所以「有沒有等到 close」就是
 * 「呼叫端能不能相信寫完了」。7.1 的成功訊息、以及成功之後才記下題號(4.4),都
 * 建立在這一點上。
 *
 * **整檔覆寫**:內容是 `appendPosition()` 的輸出,既有題目的逐字不變(5.7)由那個
 * 輸出保證,不由寫入方式保證。`createWritable()` 預設不保留原有內容,正是要的行為。
 *
 * @param {FileSystemFileHandle} handle 選定的題目檔。
 * @param {string} text 要寫入的全文。
 * @returns {Promise<void>} 兌現時內容已在磁碟上。
 */
export async function writeText(handle, text) {
  const writable = await handle.createWritable();

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
