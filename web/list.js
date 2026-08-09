/**
 * 列表組裝層:把題目索引與完成狀態接到 `index.html` 的骨架上。
 *
 * 這是列表頁依賴鏈的最右端 —— **沒有任何模組可以匯入它**,它也是這一頁上唯一碰
 * DOM 的模組(design 的依賴方向)。它自己不取資料、不判斷哪些題目可上架、不決定
 * 完成狀態怎麼存,能做的只有兩件事:
 *
 * 1. 把使用者的操作翻成一個狀態變更(切換某題的完成標記、重試載入);
 * 2. 把當下的狀態翻成畫面。
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
 * ## 可上架的判斷不在這裡
 *
 * `loadCatalog()` 回來的清單**已經**濾掉可解標記明確為 `false` 的題目,空值
 * (`null` 或欄位不存在)一律視為可上架。本檔因此**不得再加任何一層過濾** ——
 * corpus-verification 尚未回填,今天題庫裡每一題的 `solvable` 都是 `null`,多一層
 * truthy 判斷會讓整個列表空掉。
 *
 * ## 使用者看到的文字全部在這裡生出來
 *
 * `catalog.js` 保證後端的原文一個字都到不了這一層,失敗只帶一個類別碼。錯誤區的
 * 說法因此寫在骨架裡(那是 design 的 Error Handling 唯一的一則),本檔只負責讓它
 * 出現或收起來。文字一律繁體中文(6.2)。
 */

import { loadCatalog } from './catalog.js';
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

/** 難度欄的前綴。四項各自帶著自己的名目,列表因此不需要一列表頭。 */
const DIFFICULTY_PREFIX = '難度';

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
 * 一列(1.2、1.3)。
 *
 * 四項的順序即掃視的順序:題號、局名在前(它們是主要識別),難度與標籤在後。
 * 完成標記放在最左 —— 那一欄要能一路往下掃,看出練到哪裡了。
 */
function row(position) {
  const item = document.createElement('li');
  item.className = 'position';
  // 題號是列與題目的對應,樣式與日後的導航(4.1)都靠它。
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

  const id = document.createElement('span');
  id.className = 'position-id';
  id.textContent = `第 ${position.id} 題`;

  const name = document.createElement('span');
  name.className = 'position-title';
  name.textContent = title;

  // 難度以 `!= null` 判斷而非 falsy:這個欄位可能是 0,而 0 是一個真的難度。
  const difficulty = document.createElement('span');
  difficulty.className = 'position-difficulty';
  difficulty.textContent =
    position.difficulty != null
      ? `${DIFFICULTY_PREFIX} ${position.difficulty}`
      : `${DIFFICULTY_PREFIX} ${BLANK}`;

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

  item.append(toggle, id, name, difficulty, tags);
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
