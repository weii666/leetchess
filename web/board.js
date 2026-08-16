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
 * 不知道對局狀態機的存在。棋規一概不判斷:合法落點一律由外部傳入。
 *
 * 選子互動(tasks 3.2)照這兩條走:
 *
 * - **可選取 = 傳進來的著法裡有自該格出發的**。「是不是己方的子」不由本模組判斷
 *   (requirements 2.4)—— 後端給的合法著法只會自輪方的子出發,這個定義因此與
 *   POC 的 `!busy && isRed && legal.some(...)` 等價,而且不需要知道誰是己方。
 *   等待中與非我方回合(requirements 2.5、6.2)由 `game.js` 給出空的著法集合
 *   來表達,盤面自然就整片不可選。
 * - **選中狀態一樣不記在這裡**。點擊只經回呼往外通知,畫面要變得由外部帶著新的
 *   `selected` 再畫一次。POC `selectPiece` 的切換(再點一次已選中的子等於取消)
 *   是傳入值的純推導,故保留在此:點到 `selected` 那格時通知的是 `null`。
 */

import { FILES, RANKS, NAMES, fr2sq, sq2fr } from './fen.js';

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

/** 選中框與落點標示的用色 —— 取自 POC 的 `.selected` / `.dot`。 */
const HIGHLIGHT = '#2e86de';
const DOT_FILL = 'rgba(46, 134, 222, 0.55)';
const DOT_RING = 'rgba(46, 134, 222, 0.85)';

/** 上一手終點標示的邊框色 —— 中性灰,比子本身的黑色輕,加粗了也不會太搶戲。 */
const LAST_MOVE_STROKE = '#8a8a8a';

/** 子的圓半徑。選中框比它大一圈,落點的吃子圈則與 POC 的 `.dot.capture` 同寬。 */
const DISC_R = CELL * 0.43;

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
 * 整個子包在一個 `<g>` 裡,選子互動因此有單一的掛載點,點到字或點到圓都算點到
 * 同一個子。
 *
 * 選中的子多畫一圈套在外緣 —— POC 用的是 `box-shadow`,而 `.piece` 現在是 SVG
 * 的 `<g>`,`box-shadow` 對它無效(tasks 3.1 的交付事實),只能自繪。
 *
 * `lastMoveDestination` 是黑方剛落子的那顆子:邊框加粗成 4(與 `.piece.selectable:
 * hover .piece-disc` 的懸停回饋同一個寬度),顏色換成中性灰(`LAST_MOVE_STROKE`)
 * ——子本身的黑色描邊加粗後太重、太搶戲,灰色份量輕但仍看得出「這顆子被標記了」。
 * 不疊加任何新元素,只改這顆子自己邊框的線寬與顏色。起點不畫任何東西。
 */
function pieceElement(code, file, rank, { selectable, selected, lastMoveDestination }) {
  const isRed = code === code.toUpperCase();
  const color = isRed ? RED : BLACK;
  const group = svgElement('g', {
    class:
      `piece ${isRed ? 'red' : 'black'}`
      + (selectable ? ' selectable' : '')
      + (selected ? ' selected' : ''),
  });
  group.append(
    svgElement('circle', {
      class: 'piece-disc',
      cx: px(file),
      cy: py(rank),
      r: DISC_R,
      fill: PIECE_BG,
      stroke: lastMoveDestination ? LAST_MOVE_STROKE : color,
      'stroke-width': lastMoveDestination ? 4 : 2,
    }),
  );
  if (selected) {
    group.append(
      svgElement('circle', {
        class: 'piece-selection',
        cx: px(file),
        cy: py(rank),
        r: DISC_R + 3,
        fill: 'none',
        stroke: HIGHLIGHT,
        'stroke-width': 4,
      }),
    );
  }
  group.append(
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
 * 一個合法落點的標示。終點原本有子(吃子)時改畫成大一圈的空心圈,與 POC 的
 * `.dot.capture` 相同。
 *
 * `pointer-events: all` 是必要的:空心圈的 `fill` 為 `none`,SVG 預設只有筆畫
 * 吃得到點擊,圈內會直接穿透到底下那枚被吃的子 —— 使用者點在圈中央會落空。
 */
function destinationElement(file, rank, capture) {
  return svgElement('circle', {
    class: `dot${capture ? ' capture' : ''}`,
    cx: px(file),
    cy: py(rank),
    r: capture ? CELL * 0.4 : 11,
    fill: capture ? 'none' : DOT_FILL,
    stroke: capture ? DOT_RING : 'none',
    'stroke-width': capture ? 3 : 0,
    'pointer-events': 'all',
  });
}

/**
 * 把一份盤面畫進 `container`。
 *
 * 每次呼叫都自 `board` 重建整個盤面並整份換掉容器的內容 —— 沒有增量更新,也就
 * 沒有「上一次畫了什麼」需要記住。重繪不會累積,換一份資料進來畫面就完全是那份
 * 資料的樣子。
 *
 * 選子與合法落點的標示同樣只是資料:給什麼畫什麼,不給就沒有。
 *
 * @param {Element} container 盤面容器,內容會被整份取代。
 * @param {object} options
 * @param {(string|null)[][]} options.board `fen.js` 給出的 `board[rank][file]`
 *   陣列,空格為 `null`。
 * @param {string[]} [options.legalMoves] 當前可走的 UCI 著法,例如 `['d8d9']`。
 *   **由外部(後端)提供,本模組不驗證也不推導**。
 * @param {string|null} [options.selected] 選中的格名,例如 `'d8'`。落點只為這一
 *   格標示。
 * @param {string|null} [options.lastMove] 黑方剛才那一手的 UCI 著法,例如
 *   `'h9g7'`。只標終點:那顆子的邊框會加粗、變成灰色,起點不畫任何東西。沒有
 *   `lastMove` 時為 `null`,沒有子的邊框會改變。
 * @param {(square: string|null) => void} [options.onSelect] 點到可選取的子時
 *   呼叫;點到已選中的那一格時給 `null`(取消選取)。
 * @param {(uci: string) => void} [options.onMove] 點到已標示的落點時呼叫,帶完整
 *   的 UCI 著法。盤面**不**因此自行更新。
 */
export function renderBoard(
  container,
  {
    board,
    legalMoves = [],
    selected = null,
    lastMove = null,
    onSelect = () => {},
    onMove = () => {},
  },
) {
  /** 自 `square` 出發的著法 —— 可選取與落點標示都只看這個。 */
  const movesFrom = (square) =>
    legalMoves.filter((move) => move.slice(0, 2) === square);
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
  const lastMoveDestination = lastMove ? lastMove.slice(2, 4) : null;
  for (let rank = 0; rank < RANKS; rank++) {
    for (let file = 0; file < FILES; file++) {
      const code = board[rank][file];
      if (!code) continue;
      const square = fr2sq(file, rank);
      const isSelected = square === selected;
      const selectable = movesFrom(square).length > 0;
      const piece = pieceElement(code, file, rank, {
        selectable,
        selected: isSelected,
        lastMoveDestination: square === lastMoveDestination,
      });
      if (selectable) {
        piece.addEventListener('click', () =>
          onSelect(isSelected ? null : square),
        );
      }
      svg.append(piece);
    }
  }
  // 落點畫在棋子之後 —— 吃子的標示必須蓋在被吃的子上面才點得到。
  for (const move of selected ? movesFrom(selected) : []) {
    const [file, rank] = sq2fr(move.slice(2, 4));
    const dot = destinationElement(file, rank, board[rank][file] !== null);
    dot.addEventListener('click', () => onMove(move));
    svg.append(dot);
  }
  container.replaceChildren(svg);
}
