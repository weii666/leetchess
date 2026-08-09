"""領域型別。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組位於最左端,**不得 import 任何其他 service 模組**。

領域型別集中於此是刻意的:`Position`(題庫)與 `GameState`(對局)都需要 `Side`,
若 `Side` 落在 `game.py`,`positions.py` 就會反向依賴 `game.py`,違反上述方向。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Side",
    "Signal",
    "ScoreKind",
    "Score",
    "BestMove",
    "GameState",
    "BlackReply",
    "Position",
]


class Side(str, Enum):
    """對局的一方。紅為排局的攻方,黑為守方。"""

    RED = "red"
    BLACK = "black"


class Signal(str, Enum):
    """三態的診詢信號。

    `cp` 分數一律歸為 `UNKNOWN`,不得據以推斷勝負傾向。
    """

    RED_WINNING = "red_winning"  # 引擎回報黑方將被殺
    BLACK_WINNING = "black_winning"  # 引擎回報黑方可殺
    UNKNOWN = "unknown"  # 未搜得殺著


class ScoreKind(str, Enum):
    """引擎評分的種類。"""

    MATE = "mate"
    CP = "cp"


@dataclass(frozen=True)
class Score:
    """引擎回傳的評分。"""

    kind: ScoreKind
    value: int  # 黑方視角;mate 為負代表黑方將被殺


@dataclass(frozen=True)
class BestMove:
    """引擎搜尋後回傳的最佳著法。"""

    move: str | None  # UCI 著法;None 代表引擎回報無著可走
    score: Score | None


@dataclass(frozen=True)
class GameState:
    """某一局面的對局狀態。

    `over` 只由 `legal_moves` 是否為空決定,任何評分都不影響它。
    `over` 為真時 `winner` 必有值,且 `legal_moves` 為空。
    """

    side_to_move: Side
    legal_moves: list[str]
    over: bool
    winner: Side | None


@dataclass(frozen=True)
class BlackReply:
    """黑方應手與其伴隨的信號。"""

    move: str | None
    signal: Signal
    mate_in: int | None  # 僅 RED_WINNING 時有值
    state: GameState


@dataclass(frozen=True)
class Position:
    """題庫中的一道題目。

    欄位對應 `.kiro/steering/structure.md` 的題目 schema,但**不是它的鏡像**:

    - `side_to_move` **不是題目 JSON 的欄位**,而由 `fen` 的走子方那一欄推導
      (`positions.py` 的 `_side_from_fen`)。起手方在 FEN 裡本來就寫著,再開一個
      欄位等於同一件事有兩個出處,遲早互相矛盾。
    - `max_dtm` 由驗證工具日後回填,現階段可為空,不得視為必填。
    """

    id: int
    title: str
    description: str
    fen: str
    side_to_move: Side
    difficulty: int
    tags: list[str]
    max_dtm: int | None = None
