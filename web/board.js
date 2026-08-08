/**
 * SVG 棋盤繪製。
 *
 * 自 `poc/index.html` 移植(`drawGrid` 與 `render`)。格線路徑、座標公式、九宮
 * 斜線與河界字樣的位置都與 POC 逐項相同 —— 那份繪製已實際跑過整局。
 *
 * 移植時只做兩處必要的調整:
 *
 * 1. **不記憶任何狀態**(design 的「模組結構與依賴方向」)。POC 的 `render` 讀取
 *    模組級的 `board` / `selected` / `legal` 等全域變數;此處一律由呼叫端傳入,
 *    容器也由呼叫端指定。唯一真相在 `game.js`,呈現層不得再存一份。
 * 2. **棋子畫在 SVG 內**,而非 POC 的絕對定位 `div`。POC 的 `div` 依賴一組固定
 *    px 的全域 CSS(`#board{position:relative}` 加 `.piece{position:absolute}`)才
 *    落得到位置;改進 SVG 之後座標系統只剩 `viewBox` 一個,縮放盤面只需縮放這個
 *    元素,requirements 8.2 的行動裝置版面(tasks 5.1)因此不必再處理棋子的重新
 *    定位。類別名(`piece` / `red` / `black`)沿用 POC,樣式仍歸 `style.css`。
 *
 * 呈現屬性(顏色、線寬、字級)以 POC 的值直接寫在元素上,頁面因此在沒有 CSS 的
 * 情況下也畫得出完整棋盤;`style.css` 的規則勝過呈現屬性,要改樣式不必回頭動這裡。
 *
 * 依賴方向:本模組位於 `fen.js` 的右邊一層,**只能匯入 `fen.js`** —— 不碰 API、
 * 不知道對局狀態機的存在。棋規一概不判斷:合法落點(tasks 3.2)一律由外部傳入。
 */

import { FILES, RANKS, NAMES } from './fen.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** 一格的邊長(`viewBox` 的使用者座標)。 */
const CELL = 62;

/** 盤面四周的留白 —— 邊線上的子有一半落在這個範圍內。 */
const MARGIN = 40;

/** file 0–8(a–i,紅方視角由左至右)對應的 x。 */
const px = (file) => MARGIN + file * CELL;

/** rank 0–9 對應的 y。**紅方底線 rank 0 在下**(requirements 1.5)。 */
const py = (rank) => MARGIN + (RANKS - 1 - rank) * CELL;

const WIDTH = px(FILES - 1) + MARGIN;
const HEIGHT = py(0) + MARGIN;

/** 棋盤底色、線色、紅黑用色 —— 取自 POC 的 CSS 變數。 */
const BOARD_BG = '#f0d9a8';
const LINE = '#6b5433';
const RIVER = '#8a6d3b';
const PIECE_BG = '#f7ecd0';
const RED = '#c0392b';
const BLACK = '#1c1c1c';

/** 建一個 SVG 元素;`text` 非空時作為其文字內容。 */
function svgElement(name, attributes, text) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  if (text !== undefined) element.textContent = text;
  return element;
}

/**
 * 格線的路徑:十條橫線、九條縱線(中間七條在河界斷開)、上下九宮各一個交叉。
 *
 * 與 POC `drawGrid` 的線段組成相同。
 */
function gridPath() {
  const X = px;
  const Y = py;
  const lines = [];
  // 橫線 10 條
  for (let r = 0; r < RANKS; r++) lines.push(`M${X(0)} ${Y(r)} H${X(8)}`);
  // 豎線:兩端整條,中間 7 條在河界斷開
  lines.push(`M${X(0)} ${Y(9)} V${Y(0)}`);
  lines.push(`M${X(8)} ${Y(9)} V${Y(0)}`);
  for (let f = 1; f < 8; f++) {
    lines.push(`M${X(f)} ${Y(9)} V${Y(5)}`);
    lines.push(`M${X(f)} ${Y(4)} V${Y(0)}`);
  }
  // 九宮斜線(上下各一個交叉)
  lines.push(`M${X(3)} ${Y(9)} L${X(5)} ${Y(7)}`);
  lines.push(`M${X(5)} ${Y(9)} L${X(3)} ${Y(7)}`);
  lines.push(`M${X(3)} ${Y(2)} L${X(5)} ${Y(0)}`);
  lines.push(`M${X(5)} ${Y(2)} L${X(3)} ${Y(0)}`);
  return lines.join(' ');
}

/** 「楚河」「漢界」兩個字樣,寫在 rank 4 與 rank 5 之間的空白處。 */
function riverLabels() {
  const y = (py(5) + py(4)) / 2 + 6;
  return [
    [(px(1) + px(2)) / 2, '楚河'],
    [(px(6) + px(7)) / 2, '漢界'],
  ].map(([x, text]) =>
    svgElement(
      'text',
      {
        class: 'river',
        x,
        y,
        fill: RIVER,
        'font-size': 20,
        'text-anchor': 'middle',
      },
      text,
    ),
  );
}

/**
 * 一個子:圓底加中文字。紅子為 FEN 的大寫,黑子為小寫。
 *
 * 整個子包在一個 `<g>` 裡,選子互動(tasks 3.2)因此有單一的掛載點,點到字或
 * 點到圓都算點到同一個子。
 */
function pieceElement(code, file, rank) {
  const isRed = code === code.toUpperCase();
  const color = isRed ? RED : BLACK;
  const group = svgElement('g', {
    class: `piece ${isRed ? 'red' : 'black'}`,
  });
  group.append(
    svgElement('circle', {
      class: 'piece-disc',
      cx: px(file),
      cy: py(rank),
      r: CELL * 0.43,
      fill: PIECE_BG,
      stroke: color,
      'stroke-width': 2,
    }),
    svgElement(
      'text',
      {
        class: 'piece-label',
        x: px(file),
        y: py(rank),
        fill: color,
        'font-size': CELL * 0.5,
        'font-weight': 700,
        'text-anchor': 'middle',
        'dominant-baseline': 'central',
      },
      NAMES[code],
    ),
  );
  return group;
}

/**
 * 把一份盤面畫進 `container`。
 *
 * 每次呼叫都自 `board` 重建整個盤面並整份換掉容器的內容 —— 沒有增量更新,也就
 * 沒有「上一次畫了什麼」需要記住。重繪不會累積,換一份資料進來畫面就完全是那份
 * 資料的樣子。
 *
 * 選子與合法落點的標示(tasks 3.2)以擴充 options 的方式加入,呼叫形式不變。
 *
 * @param {Element} container 盤面容器,內容會被整份取代。
 * @param {{board: (string|null)[][]}} options `board` 為 `fen.js` 給出的
 *   `board[rank][file]` 陣列,空格為 `null`。
 */
export function renderBoard(container, { board }) {
  const svg = svgElement('svg', {
    class: 'board',
    viewBox: `0 0 ${WIDTH} ${HEIGHT}`,
    width: WIDTH,
    height: HEIGHT,
  });
  svg.append(
    svgElement('rect', {
      class: 'board-bg',
      x: 0,
      y: 0,
      width: WIDTH,
      height: HEIGHT,
      fill: BOARD_BG,
    }),
    svgElement('path', {
      class: 'grid',
      d: gridPath(),
      stroke: LINE,
      'stroke-width': 1.5,
      fill: 'none',
    }),
    ...riverLabels(),
  );
  for (let rank = 0; rank < RANKS; rank++) {
    for (let file = 0; file < FILES; file++) {
      const code = board[rank][file];
      if (code) svg.append(pieceElement(code, file, rank));
    }
  }
  container.replaceChildren(svg);
}
