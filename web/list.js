/**
 * 列表組裝層:把題目索引與完成狀態接到 `index.html` 的骨架上。
 *
 * 這是列表頁依賴鏈的最右端 —— **沒有任何模組可以匯入它**,它也是這一頁上唯一碰
 * DOM 的模組(design 的依賴方向)。它自己不取資料、不決定哪些題目該列出來、也不
 * 決定完成狀態怎麼存,能做的只有兩件事:
 *
 * 1. 把使用者的操作翻成一個狀態變更(切換某題的完成標記、重試載入);
 * 2. 把當下的狀態翻成畫面。
 *
 * ## 進入對局是一個連結,不是一段程式
 *
 * 選題的交接只有一個契約:`/play.html?id=<題號>`(3.1)。本檔因此**不處理導航** ——
 * 它只在每一列上放一個 `<a href>`,剩下的交給瀏覽器。這不只是省事:中鍵開新分頁、
 * 右鍵複製網址、Enter 鍵、上一頁,全部是真連結免費附帶而攔 click 改 `location`
 * 要一一重做的東西。連結只包住局名,完成標記在結構上就落在連結之外,「按標記不會
 * 跳走」因此是版面的結果,不是靠事件處理器裡的補救。**這一點與完成標記畫在哪一欄
 * 無關**,但也因此:換位置時只能動 DOM 順序,不得把它挪進 `<a>` 裡面。
 *
 * ## 只有一條寫進畫面的路徑
 *
 * 所有呈現都由 `render()` 自**當下的狀態**整份重畫,沒有任何一處是「事件發生時
 * 順手改一下某個節點」—— 與 `app.js` 的理由相同:增量更新等於在呈現層再存一份
 * 狀態,而三個條件區塊(列、空狀態、錯誤)就得各自記得在別種情形下要收起來,
 * 收不乾淨就會出現「錯誤與空狀態同時掛在畫面上」這種自相矛盾的畫面。
 *
 * ## 出處與描述:拿了不畫
 *
 * `/api/catalog` 與 `catalog.js` 一直都帶著 `source` 與 `description`,本檔刻意
 * **不呈現**(1.2):列是掃視用的,每列擠進越多欄位越難掃。兩者已移到對局介面
 * (4.5)。日後題庫收錄第二本書時列表要靠出處分辨同名排局,屆時只要在這裡多畫
 * 一欄 —— 資料層一個字都不必改(tasks.md 的 Backlog)。
 *
 * ## 哪些題目該列出來不是這裡的決定
 *
 * `loadCatalog()` 回來的就是索引的全部,本檔**不得再加任何一層過濾**。題庫裡曾有
 * 一個 `solvable` 欄位供列表篩掉偽題,已隨題目 schema 移除 —— 它從未有過非空的值,
 * 那道過濾因此不曾濾掉任何一題。日後真要篩,判準會有一個明確的出處,而不是在呈現
 * 層多長一個 truthy 判斷。
 *
 * ## 使用者看到的文字全部在這裡生出來
 *
 * `catalog.js` 保證後端的原文一個字都到不了這一層,失敗只帶一個類別碼。錯誤區的
 * 說法因此寫在骨架裡(那是 design 的 Error Handling 唯一的一則),本檔只負責讓它
 * 出現或收起來。文字一律繁體中文(6.2)。
 */

import { loadCatalog } from './catalog.js';
import { readDifficulty } from './difficulty.js';
import { loadCompleted, toggleCompleted } from './progress.js';

const elements = {
  positions: document.getElementById('positions'),
  completedCount: document.getElementById('completed-count'),
  totalCount: document.getElementById('total-count'),
  empty: document.getElementById('empty'),
  error: document.getElementById('error'),
  retry: document.getElementById('retry'),
};

/** 沒有值可填時的佔位符號(與對局介面一致)。 */
const BLANK = '—';

/** 局名為空時的說法 —— 題目一定有題號,但局名可能缺。 */
const UNTITLED = '(未命名)';

/** 對局介面的位址。題號經 `?id=` 交接(3.1 定案的契約,`web/app.js` 的
 * `readPositionId()` 讀它 —— 指函式而非行號:行號會在對方改動時靜默過期)。 */
const PLAY_PAGE = './play.html';

/**
 * 通往某一題對局介面的位址(4.1、4.2)。
 *
 * 題號取自題目本身,**與列上的 `data-id` 是同一個值**(3.2 的結構契約),不另立
 * 一套識別。特別不是列的位置:題庫的題號會有缺口(按局號收題,中間幾局還沒收),
 * 那時「第三列就是第三題」不成立,以位置推題號會整排指錯題。
 */
const playHref = (id) => `${PLAY_PAGE}?id=${encodeURIComponent(id)}`;

/** 完成標記的無障礙名稱 —— 一整欄的核取方塊長得一樣,要靠局名才分得出是哪一題。 */
const toggleLabel = (title) => `標記「${title}」為已完成`;

/**
 * 已上架的題目。**尚未載入完成或載入失敗時是空陣列** —— 那兩種情形下畫面上不該
 * 留著上一次的列。
 */
let positions = [];

/**
 * 完成的題號。狀態由本檔持有,`progress.js` 是純函式(design 把它定為「純函式 +
 * localStorage、依賴為無」),因此切換一律寫成 `completed = toggleCompleted(...)`。
 *
 * 開頁時讀一次即可:同一個分頁裡沒有第二個寫入者,而每次切換都以回傳值取代。
 * **讀取不寫入任何東西**(3.4),光是開啟列表不會產生任何標記。
 */
let completed = loadCompleted();

/** 這一次載入是否失敗。 */
let failed = false;

/** 是否還在等索引回來。 */
let loading = false;

/**
 * 難度那一格(1.2)。
 *
 * 1–3 畫成**一個帶顏色的詞**(不是標籤:沒有底色,字級與標籤的 chip 相同),顏色由
 * `list.css` 依 `data-level` 上色 —— **本檔不碰顏色**,呈現層裡的顏色屬樣式表。
 *
 * 說法與退路都出自 `difficulty.js`,與對局頁共用同一份;本檔只負責把它畫成列上的
 * 一格。認不得的值(0、4、`null`、欄位不存在)的處理見那裡。
 */
function difficultyCell(value) {
  const cell = document.createElement('span');
  cell.className = 'position-difficulty';

  const { text, level } = readDifficulty(value, BLANK);
  // 上色的掛勾。與 `data-completed` 同性質:是樣式要用的結構契約,不是測試鉤子。
  if (level !== null) cell.dataset.level = level;
  cell.textContent = text;
  return cell;
}

/**
 * 一列(1.2、1.3)。
 *
 * 欄序:**題號、局名、標籤、難度、完成標記**。前兩項是主要識別,故在左;標籤與
 * 難度是次要資訊,故在右。**難度緊鄰完成標記**,兩者因此在整份列表上連成固定的
 * 兩直欄 —— 標籤是全列最不可預測的一項(數量、長度都不定),把它夾在局名與難度
 * 之間,難度才不會跟著標籤多寡左右浮動。`list.css` 讓標籤欄靠右對齊,空隙因此落在
 * 標籤**左**側而不是右側。
 *
 * **完成標記在最右**,與題庫類產品的慣例一致:左緣留給題號那一欄,一路往下掃的是
 * 題號而不是勾選框。
 *
 * 完成標記在**結構上就落在 `<a>` 之外**(它是 `<li>` 的直接子節點,與連結平行),
 * 「按標記不會跳進對局頁」(4.1)因此是版面的結果。移動它的位置**只能動 DOM 順序
 * 或視覺順序** —— 一旦被塞進連結裡,那條保證就得改由 `stopPropagation` 之類的補救
 * 維持,而那種補救擋不住鍵盤與中鍵。
 */
function row(position) {
  const item = document.createElement('li');
  item.className = 'position';
  // 題號是列與題目的對應,樣式與進入對局的連結(4.1)都靠同一個值。
  item.dataset.id = String(position.id);

  const done = completed.has(position.id);
  // 完成狀態的呈現掛勾。核取方塊本身已經看得出來,但那只是一個小方格 ——
  // 整列的視覺區分(6.3)要有個能掛樣式的地方。
  if (done) item.dataset.completed = '';

  const title = position.title || UNTITLED;

  const toggle = document.createElement('input');
  toggle.type = 'checkbox';
  toggle.className = 'position-toggle';
  toggle.checked = done;
  toggle.setAttribute('aria-label', toggleLabel(title));
  toggle.addEventListener('change', () => mark(position.id));

  // 題號是「數字 + 點」,不加「第…題」(參照形態:leetcode 的 `1. Two Sum`)。
  // **點是分隔符而不是贅字** —— 它把題號與局名分開,而題號欄本身仍是獨立一欄。
  //
  // 右對齊由 `list.css` 負責,理由寫在那裡:本專案的題號**有缺口**,左對齊會讓那
  // 一排點跟著數字位數跑成參差的一排。
  const id = document.createElement('span');
  id.className = 'position-id';
  id.textContent = `${position.id}.`;

  // 局名即進入該題的入口(4.1)。**用真的 `<a href>`** —— 中鍵開新分頁、右鍵複製
  // 網址、Enter 鍵、上一頁全部隨之而來,自己攔 click 再改 `location` 則要一一重做,
  // 而通常不會做。連結只包住局名,完成標記因此**在結構上就不在連結裡** —— 標記
  // 一題不會把使用者丟進棋盤,這件事不靠 `stopPropagation` 之類的補救維持。
  const name = document.createElement('a');
  name.className = 'position-title';
  name.href = playHref(position.id);
  name.textContent = title;

  const difficulty = difficultyCell(position.difficulty);

  const tags = document.createElement('span');
  tags.className = 'position-tags';
  const labels = Array.isArray(position.tags) ? position.tags : [];
  if (labels.length === 0) {
    // 沒有標籤時留佔位符號,那一欄才不會塌掉而讓整列看起來少了一項。
    const none = document.createElement('span');
    none.className = 'position-tag-none';
    none.textContent = BLANK;
    tags.append(none);
  } else {
    for (const label of labels) {
      const chip = document.createElement('span');
      chip.className = 'position-tag';
      chip.textContent = label;
      tags.append(chip);
    }
  }

  // 完成標記排在最後 —— **DOM 順序即視覺順序**,不靠 `order` 或 `direction` 之類的
  // 純視覺搬移:那些手法只挪畫面,Tab 與螢幕閱讀器仍照 DOM 走,兩者一旦分家,
  // 鍵盤使用者的行進順序就與眼睛看到的對不上。
  item.append(id, name, tags, difficulty, toggle);
  return item;
}

/**
 * 完成題數與總題數(3.5)。
 *
 * 已完成的算法是**完成集合與目前列出的題目取交集**,不是集合本身的大小。集合是
 * 題號的集合而非列表的鏡像(3.7):裡面可能留著已經下架、或這份索引根本沒有的
 * 題號,直接數會出現「已完成 5 / 3 題」這種讀不通的數字。
 *
 * 還沒載到索引(載入中或失敗)時兩個數字都是佔位符號 —— 那時「總共幾題」還不知道,
 * 寫 0 等於宣稱題庫是空的,而那正是錯誤狀態最不該偽裝成的東西。
 */
function renderProgress() {
  if (loading || failed) {
    elements.completedCount.textContent = BLANK;
    elements.totalCount.textContent = BLANK;
    return;
  }
  const done = positions.filter((position) => completed.has(position.id)).length;
  elements.completedCount.textContent = String(done);
  elements.totalCount.textContent = String(positions.length);
}

/**
 * 把當下的狀態整份畫出來。畫面的每一次變動都只經過這裡。
 *
 * 三個區塊的顯示條件在同一個地方一次決定,因此**任一時刻至多只有一個成立**:
 *
 * - 錯誤區:索引取不到(design 的 Error Handling)
 * - 空狀態:索引取得了,但裡面一題都沒有(1.5)
 * - 列:其餘情形
 *
 * 「題庫為空」與「索引壞掉」對使用者是兩件事,`catalog.js` 對認不得的回應形狀
 * 刻意拋錯而不是給一份空陣列,正是為了讓這裡分得開;合成一個畫面會讓「索引壞掉」
 * 看起來像「題庫沒有題目」,而重試也就無從談起。
 */
function render() {
  elements.positions.replaceChildren(...positions.map(row));
  renderProgress();
  elements.empty.hidden = loading || failed || positions.length > 0;
  elements.error.hidden = !failed;
}

/**
 * 切換一題的完成標記(3.1、3.2)。
 *
 * 新的集合一律取自 `toggleCompleted` 的回傳值 —— 那個函式不就地修改,自己動手改
 * 集合會讓記憶體裡的狀態與寫進儲存區的那一份分岔。
 *
 * 重畫之後把焦點放回同一題的核取方塊:整份重畫會換掉原本那個節點,而以鍵盤操作的
 * 使用者會因此被丟回頁面開頭,一題也標不下去。
 */
function mark(id) {
  completed = toggleCompleted(completed, id);
  render();
  elements.positions
    .querySelector(`li[data-id="${id}"] .position-toggle`)
    ?.focus();
}

/**
 * 取得索引並畫出列表。**重試就是再跑一次它**(design 的 Error Handling)。
 *
 * 失敗時把 `positions` 清空:留著上一次的列,使用者會看到一份已知過期的列表配上
 * 一則載入失敗,分不出哪個才算數。
 */
async function start() {
  loading = true;
  failed = false;
  render();

  try {
    positions = (await loadCatalog()).positions;
  } catch {
    // `catalog.js` 只給得出一個類別碼,而 Error Handling 對索引取不到只有一則
    // 說法與一個重試 —— 不為每種失敗各做一套 UI。
    positions = [];
    failed = true;
  } finally {
    loading = false;
  }

  render();
}

// 重試不重讀完成狀態:那份資料與這次失敗無關,重讀只會多一次沒有理由的儲存區存取。
elements.retry.addEventListener('click', () => {
  start();
});

render();
start();
