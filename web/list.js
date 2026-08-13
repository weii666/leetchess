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
 * `loadCatalog()` 回來的就是索引的全部,本檔**不得自行發明過濾判準**。題庫裡曾有
 * 一個 `solvable` 欄位供列表篩掉偽題,已隨題目 schema 移除 —— 它從未有過非空的值,
 * 那道過濾因此不曾濾掉任何一題。日後真要篩,判準會有一個明確的出處,而不是在呈現
 * 層多長一個 truthy 判斷。
 *
 * 使用者透過篩選下拉主動選擇的顯示範圍是另一回事:判準的出處在 `catalog.js` 既有、
 * 已測試的 `filterPositions()`(難度)與 `starred.js` 已載入的 `starred` 集合
 * (我的最愛),本檔只是照使用者的選擇呼叫它們、決定畫哪一個子集合,並不是自己
 * 發明了一條新的篩選規則。
 *
 * ## 使用者看到的文字全部在這裡生出來
 *
 * `catalog.js` 保證後端的原文一個字都到不了這一層,失敗只帶一個類別碼。錯誤區的
 * 說法因此寫在骨架裡(那是 design 的 Error Handling 唯一的一則),本檔只負責讓它
 * 出現或收起來。文字一律繁體中文(6.2)。
 */

import { loadCatalog, filterPositions } from './catalog.js';
import { readDifficulty } from './difficulty.js';
import { loadCompleted, toggleCompleted } from './progress.js';
import { loadStarred, toggleStarred } from './starred.js';
import { pickDailyPosition, todayKey } from './daily.js';
import { loadFilter, saveFilter } from './filter.js';

/** SVG 命名空間 —— 星號圖示是唯一需要它的元素(其餘皆為一般 HTML 節點)。 */
const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * 星號圖示的路徑。**沒有描邊/填色的差別在這裡決定** —— 兩種狀態共用同一個
 * `<path>`,由 `list.css` 依 `data-starred` 切換 `fill`/`stroke`,顏色因此永遠
 * 是同一顆星,不會出現標星前後形狀跳動的觀感。
 *
 * **五個外角與五個內凹角都是圓角**,不是直線相交的尖角(參照使用者提供的
 * leetcode 圖示)。這件事必須做在路徑本身裡,不能指望 `stroke-linejoin: round`
 * 代勞 —— 那個屬性只影響描邊怎麼畫,已標星是純填色(`fill: currentColor`),
 * 填色範圍就是路徑本身的形狀,路徑是尖角,填出來就是尖角。做法是在標準五角星的
 * 十個頂點各自切一刀:每個頂點往兩側鄰邊各退一小段距離,兩個退讓點之間用一段
 * 以原頂點為控制點的二次貝茲曲線相接,原本的直角銳角因此變成一段圓弧。外角
 * (五個尖端)退讓比例較大、內凹角退讓比例較小,尖端因此看起來更飽滿圓潤,
 * 內凹角只是稍微去掉銳利感,兩者的視覺比重才不會顛倒。
 */
const STAR_PATH =
  'M11.07,4.23 Q12,2 12.93,4.23 L14.07,6.96 Q14.65,8.36 16.16,8.48 ' +
  'L19.11,8.72 Q21.51,8.91 19.68,10.48 L17.43,12.4 Q16.28,13.39 16.63,14.86 ' +
  'L17.32,17.75 Q17.88,20.09 15.82,18.83 L13.29,17.29 Q12,16.5 10.71,17.29 ' +
  'L8.18,18.83 Q6.12,20.09 6.68,17.75 L7.37,14.86 Q7.72,13.39 6.57,12.4 ' +
  'L4.32,10.48 Q2.49,8.91 4.89,8.72 L7.84,8.48 Q9.35,8.36 9.93,6.96 Z';

const elements = {
  dailyPosition: document.getElementById('daily-position'),
  positions: document.getElementById('positions'),
  completedCount: document.getElementById('completed-count'),
  totalCount: document.getElementById('total-count'),
  empty: document.getElementById('empty'),
  error: document.getElementById('error'),
  retry: document.getElementById('retry'),
  filterSelect: document.getElementById('filter-select'),
};

/** 每日推薦題的標籤欄文字(1.2 的 tags 用它換掉,理由見 `row()` 的 `featured`)。 */
const FEATURED_LABEL = '每日一題';

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

/** 星號按鈕的無障礙名稱,依目前是否已標星動態切換文字(同上,理由一致)。 */
const starLabel = (title, isStarred) =>
  isStarred ? `取消標星「${title}」` : `標星「${title}」`;

/**
 * 已上架的題目。**尚未載入完成或載入失敗時是空陣列** —— 那兩種情形下畫面上不該
 * 留著上一次的列。
 */
let positions = [];

/**
 * 今天的推薦題(`daily.js` 的 `pickDailyPosition()`)。`positions` 成功載入後算
 * 一次即可——同一組 `(positions, 今天日期)` 是決定性的,不必每次 `render()` 都
 * 重算。載入中、失敗、或題庫是空的時候維持 `null`,`render()` 那時就不畫這一列
 * (見 `elements.dailyPosition` 的用法)。
 */
let daily = null;

/**
 * 完成的題號。狀態由本檔持有,`progress.js` 是純函式(design 把它定為「純函式 +
 * localStorage、依賴為無」),因此切換一律寫成 `completed = toggleCompleted(...)`。
 *
 * 開頁時讀一次即可:同一個分頁裡沒有第二個寫入者,而每次切換都以回傳值取代。
 * **讀取不寫入任何東西**(3.4),光是開啟列表不會產生任何標記。
 */
let completed = loadCompleted();

/**
 * 標星的題號。與 `completed` 同一套規則(狀態由本檔持有,`starred.js` 是純函式,
 * 切換一律寫成 `starred = toggleStarred(...)`),但兩者是**兩份獨立資料**——標星
 * 是使用者自己整理題目的方式,不代表做過或做對。
 */
let starred = loadStarred();

/**
 * 難度篩選的判準值,對照下拉選單的 `<option value="...">`(index.html)與
 * `difficulty.js` 的分級(1–3)。「我的最愛」不在這裡——它比對的是 `starred`
 * 集合,不是難度,見 `visiblePositions()`。
 */
const FILTER_DIFFICULTY = { easy: 1, medium: 2, hard: 3 };

/** 目前的篩選條件。開頁時讀一次 `sessionStorage`(`filter.js` 的
 * `loadFilter()`),之後只由選單的 `change` 事件更動。 */
let filter = loadFilter();
elements.filterSelect.value = filter;

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
 * 星號那一格。**與難度格同一套結構契約**:認不得的 `data-*` 沒有,這裡永遠有兩種
 * 合法狀態,`list.css` 依 `data-starred` 決定描邊或填色、以及是否常駐可見。
 *
 * 圖示是一個 `<button>` 包一枚 `<svg>` 星星,而非核取方塊 —— 星號沒有「已勾選」的
 * 既定觀感(核取方塊暗示的是一個表單欄位),按鈕加圖示才對得上「標記/收藏」這個
 * 動作的慣例(參照形態:leetcode 的星號)。
 *
 * `root` 是這一格畫在哪個容器裡(`elements.positions` 或 `elements.dailyPosition`,
 * 見 `row()`),原樣轉給 `star()` 供切換後重新聚焦——理由見 `star()` 的說明。
 */
function starCell(position, isStarred, root) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'position-star';
  button.dataset.starred = String(isStarred);
  button.setAttribute('aria-pressed', String(isStarred));
  button.setAttribute('aria-label', starLabel(position.title || UNTITLED, isStarred));
  button.addEventListener('click', () => star(position.id, root));

  const icon = document.createElementNS(SVG_NS, 'svg');
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', STAR_PATH);
  icon.append(path);
  button.append(icon);

  return button;
}

/**
 * 一列(1.2、1.3)。
 *
 * 欄序:**題號、局名、標籤、難度、完成標記、星號**。題號與局名是主要識別,故在
 * 最左;標籤與難度是次要資訊,故在右。**難度緊鄰完成標記**,兩者因此在整份列表
 * 上連成固定的直欄 —— 標籤是全列最不可預測的一項(數量、長度都不定),把它夾在
 * 局名與難度之間,難度才不會跟著標籤多寡左右浮動。`list.css` 讓標籤欄靠右對齊,
 * 空隙因此落在標籤**左**側而不是右側。
 *
 * **完成標記緊接難度之後**,與題庫類產品的慣例一致:左緣留給題號那一欄,一路
 * 往下掃的是題號而不是勾選框。**星號排在全列最後**——它原本放在最左側(題號
 * 之前),使用者看過實際畫面後指出這與最初提供的 leetcode 參考圖不符:該圖裡
 * 星號其實是每列**最右側**的元素(在難度標籤之後),因此移到這裡修正這個誤讀。
 *
 * 星號與完成標記都在**結構上就落在 `<a>` 之外**(兩者皆為 `<li>` 的直接子節點,
 * 與連結平行),「按標記不會跳進對局頁」(4.1)因此是版面的結果,標星同理。移動
 * 它們的位置**只能動 DOM 順序或視覺順序** —— 一旦被塞進連結裡,那條保證就得改由
 * `stopPropagation` 之類的補救維持,而那種補救擋不住鍵盤與中鍵。
 *
 * @param {object} position 要畫的題目。
 * @param {{root?: Element, featured?: boolean}} [options]
 *   `root` 是這個 `<li>` 最終會被放進哪個容器(`elements.positions` 或
 *   `elements.dailyPosition`),預設 `elements.positions`——完成標記與星號的
 *   事件處理器要靠它,重新渲染後才找得到正確的節點聚焦(見 `mark()`/`star()`)。
 *   `featured` 為 true 時是**每日推薦題那一列**(`elements.dailyPosition` 專用):
 *   標籤欄不畫題目原本的 `tags`,換成 `FEATURED_LABEL`(「每日一題」)——原本的
 *   標籤(殺法名)等於直接劇透怎麼解,推薦題只提示「有這一題」,不提示解法。
 *   難度、題號、局名、完成標記、星號完全不受影響,同一顆按鈕、同一份狀態,只是
 *   多畫在這裡一次(見 `render()`)。
 */
function row(position, { root = elements.positions, featured = false } = {}) {
  const item = document.createElement('li');
  item.className = 'position';
  // 題號是列與題目的對應,樣式與進入對局的連結(4.1)都靠同一個值。
  item.dataset.id = String(position.id);

  const done = completed.has(position.id);
  // 完成狀態的呈現掛勾。核取方塊本身已經看得出來,但那只是一個小方格 ——
  // 整列的視覺區分(6.3)要有個能掛樣式的地方。
  if (done) item.dataset.completed = '';

  const title = position.title || UNTITLED;

  // 命名為 `starButton` 而非 `star`:模組頂層另有一個切換標星的函式叫 `star`,
  // 同一個作用域裡撞名雖然不影響行為(閉包解析的是定義處的作用域,不是呼叫處),
  // 但讀起來容易誤會成同一樣東西。
  const starButton = starCell(position, starred.has(position.id), root);

  const toggle = document.createElement('input');
  toggle.type = 'checkbox';
  toggle.className = 'position-toggle';
  toggle.checked = done;
  toggle.setAttribute('aria-label', toggleLabel(title));
  toggle.addEventListener('change', () => mark(position.id, root));

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
  if (featured) {
    // 防劇透:推薦題不畫題目原本的殺法名,只印一個說明「這是推薦」而已。
    // **不是** `.position-tag`——這不是殺法標籤(不描述題目內容),用
    // `position-tag-featured`(白底深字反色、方形小圓角)取代,靠對比最大化
    // 抓住視線,不新增任何顏色(理由見 `list.css`)。
    const chip = document.createElement('span');
    chip.className = 'position-tag-featured';
    chip.textContent = FEATURED_LABEL;
    tags.append(chip);
  } else {
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
  }

  // 星號排在最後 —— **DOM 順序即視覺順序**,不靠 `order` 或 `direction` 之類的
  // 純視覺搬移:那些手法只挪畫面,Tab 與螢幕閱讀器仍照 DOM 走,兩者一旦分家,
  // 鍵盤使用者的行進順序就與眼睛看到的對不上。
  item.append(id, name, tags, difficulty, toggle, starButton);
  return item;
}

/**
 * 目前篩選條件下該列出的題目。**不含每日推薦列** —— 那一列永遠畫 `daily`,
 * 不吃篩選條件(見 `render()`),`#filter-bar` 的說明文字也是這麼寫的。
 *
 * 「我的最愛」比對的是已載入的 `starred` 集合,難度篩選則轉呼叫 `catalog.js`
 * 既有、已測試的 `filterPositions()` —— 兩者判準的出處都不在本檔,理由見檔頭
 * 「哪些題目該列出來不是這裡的決定」。
 */
function visiblePositions() {
  if (filter === 'favorite') {
    return positions.filter((position) => starred.has(position.id));
  }
  const difficulty = FILTER_DIFFICULTY[filter];
  if (difficulty !== undefined) {
    return filterPositions(positions, { difficulty });
  }
  return positions;
}

/**
 * 完成題數與總題數(3.5)。
 *
 * `visible` 是目前篩選條件下列出的題目(`visiblePositions()` 的回傳值,由
 * `render()` 算一次傳進來)—— 選了「簡單的題目」之後,這裡的 m/n 也只算簡單題目
 * 那個子集合,與畫面上實際列出來的列數對得上。
 *
 * 已完成的算法是**完成集合與 `visible` 取交集**,不是集合本身的大小。集合是
 * 題號的集合而非列表的鏡像(3.7):裡面可能留著已經下架、或這份索引根本沒有的
 * 題號,直接數會出現「已完成 5 / 3 題」這種讀不通的數字。
 *
 * 還沒載到索引(載入中或失敗)時兩個數字都是佔位符號 —— 那時「總共幾題」還不知道,
 * 寫 0 等於宣稱題庫是空的,而那正是錯誤狀態最不該偽裝成的東西。
 */
function renderProgress(visible) {
  if (loading || failed) {
    elements.completedCount.textContent = BLANK;
    elements.totalCount.textContent = BLANK;
    return;
  }
  const done = visible.filter((position) => completed.has(position.id)).length;
  elements.completedCount.textContent = String(done);
  elements.totalCount.textContent = String(visible.length);
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
 * 看起來像「題庫沒有題目」,而重試也就無從談起。**空狀態比對的是 `positions`
 * 而非篩選後的子集合** —— 篩選篩出零題是「這個篩選條件沒有東西可看」,與「題庫
 * 真的沒有題目」是不同的情形,不共用同一則文案。
 */
function render() {
  const visible = visiblePositions();
  elements.positions.replaceChildren(...visible.map((position) => row(position)));
  // `daily` 為 `null` 時傳空陣列給 `replaceChildren`,那一列乾脆不存在,不畫任何
  // 佔位或錯誤文案——索引本身的錯誤/空狀態已經由 #error/#empty 處理過一次。
  // **不吃篩選條件** —— 每日推薦題固定顯示今天推薦的那一題,見 `visiblePositions()`。
  elements.dailyPosition.replaceChildren(
    ...(daily ? [row(daily, { root: elements.dailyPosition, featured: true })] : []),
  );
  renderProgress(visible);
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
 *
 * `root` 是使用者實際點下去的那個容器(`elements.positions` 或
 * `elements.dailyPosition`,由 `row()` 透過 `toggle` 的 `change` 監聽器傳進來)。
 * 同一題現在可能同時畫在兩個容器裡(每日推薦題也在正常列表裡出現一次),重畫後
 * 若永遠往 `elements.positions` 找,從推薦列按下去的使用者,焦點會被丟到下面
 * 列表裡那一顆同題號的核取方塊,跟眼睛看的地方對不上。
 */
function mark(id, root = elements.positions) {
  completed = toggleCompleted(completed, id);
  render();
  root.querySelector(`li[data-id="${id}"] .position-toggle`)?.focus();
}

/**
 * 切換一題的標星。與 `mark(id)` 同一套理由(新集合一律取自純函式的回傳值、重畫
 * 之後把焦點放回同一個按鈕、`root` 決定焦點找回哪個容器),差別只在切換的是
 * `starred` 而非 `completed`。
 */
function star(id, root = elements.positions) {
  starred = toggleStarred(starred, id);
  render();
  root.querySelector(`li[data-id="${id}"] .position-star`)?.focus();
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
    // 只在成功時算一次:同一組 (positions, 今天日期) 是決定性的,重試失敗後
    // 再成功一次也不該換題——`positions` 沒變,推薦題也不該跟著跳。
    daily = pickDailyPosition(positions, todayKey());
  } catch {
    // `catalog.js` 只給得出一個類別碼,而 Error Handling 對索引取不到只有一則
    // 說法與一個重試 —— 不為每種失敗各做一套 UI。
    positions = [];
    daily = null;
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

// 篩選條件變更:更新狀態、寫進 sessionStorage、整份重畫。不重取索引 —— 篩選
// 在記憶體裡對同一份 `positions` 求值,不必也不該再發一次請求(與 `catalog.js`
// 的 `filter()` 同一套理由)。
elements.filterSelect.addEventListener('change', () => {
  filter = elements.filterSelect.value;
  saveFilter(filter);
  render();
});

render();
start();
