"""GameService:對局推進的判定 —— 輪方推導與終局認定。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組只能向左依賴 `types`、`errors`、`config`、`positions` 與 `engine`,**不得**
import `models.py` 或 `main.py`。領域型別一律由 `types.py` 匯入,**絕不在此重新
定義** —— 兩份同名型別會讓 `isinstance` 與日後 `models.py` 的 Pydantic 轉換靜默失效。

## 終局判定只有一個判準

    對局是否結束,只由合法著法數是否為零決定。

這不是「除了評分之外還要檢查合法著法」,而是**評分根本不參與這個判定**。實作上的
體現是 `_game_state()` 的參數裡沒有分數這個東西 —— 沒有一條路徑能讓評分影響 `over`,
這就是 requirements 2.4 的實現方式,以及 1.3「未達真終局不得以任何其他條件宣告結束」
的保證(見 `.kiro/steering/tech.md` 的第二個不可動搖的約束)。

為什麼要把話說得這麼死:排局的正解常常是一路將軍到底,引擎在第一手就會回報 mate。
若服務順手在那時宣告勝負,使用者根本沒機會把殺法走完 —— 那正是本專案刻意**不做**
走脫判定表的原因。三態信號(任務 3.3)是諮詢性質,它報錯的代價只是一次顯示抖動;
停局判斷若報錯,代價是整局被中斷。

## 不持有任何跨請求狀態

服務對每個請求無記憶:前端每次重送題號與完整走法序列,本類只是把它們轉成一次引擎
查詢。`GameService` 的實例欄位只有注入的協作者,沒有任何一局的進度。

## 尚未在此的部分

- **黑方應手與三態信號(1.4、2.1–2.3)屬 tasks 3.3**。接點已備妥:`side_to_move()`
  不必借引擎就能判斷輪方,使 3.3 能在進入池**之前**就對輪方為紅的請求拋出
  `WrongSideToMoveError`(1.5);`_state_with()` 接受已借出的引擎,使應手與應手後的
  局面查詢可共用同一次借用,不必通過併發閘門兩次。
"""

from __future__ import annotations

from service.config import Settings
from service.engine.pool import EnginePool
from service.engine.process import EngineProcess
from service.positions import PositionRepository
from service.types import GameState, Position, Side

__all__ = ["GameService", "side_after"]


def side_after(start: Side, move_count: int) -> Side:
    """題目起手方走了 `move_count` 步之後輪到哪一方。

    起手方取自題目,**不得硬編為紅** —— 題庫容得下黑先的排局,寫死會讓那些題目的
    勝負方整個顛倒。
    """
    return start if move_count % 2 == 0 else _opponent(start)


def _opponent(side: Side) -> Side:
    return Side.BLACK if side is Side.RED else Side.RED


def _game_state(side_to_move: Side, legal_moves: list[str]) -> GameState:
    """由輪方與合法著法組出對局狀態。**終局判定的唯一出口。**

    參數裡沒有分數,因此沒有任何一條路徑能讓評分影響 `over`(2.4)。合法著法非空
    即對局進行中,為空即該輪方無著可走、判負(1.2)—— 象棋的困斃同樣算負,與將死
    一樣走這條路徑,服務因此不需要分辨兩者(勝負不自實作)。
    """
    over = not legal_moves
    return GameState(
        side_to_move=side_to_move,
        legal_moves=list(legal_moves),
        over=over,
        winner=_opponent(side_to_move) if over else None,
    )


class GameService:
    """依引擎回傳判定對局狀態。不持有任何跨請求狀態。

    協作者由呼叫端注入:題庫提供起始局面與起手方,引擎池提供唯一的併發閘門,
    設定提供搜尋逾時。三者都不由本類建立,測試因此能整組換成替身。
    """

    def __init__(
        self,
        repository: PositionRepository,
        pool: EnginePool,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._pool = pool
        self._settings = settings

    # --- 對外的操作 ------------------------------------------------------

    def start(self, position_id: int) -> tuple[str, GameState]:
        """題目的起始局面與其對局狀態(6.1、1.1)。

        起始局面本身就是真終局時同樣依合法著法數判定,不是特例。

        Raises:
            PositionNotFoundError: 題號不存在(6.2)。
            ServiceBusyError: 等待上限內未借到引擎(3.3)。
            EngineTimeoutError: 引擎查詢逾時(4.1)。
        """
        position = self._repository.get(position_id)
        return position.fen, self._state_of(position, [])

    def state(self, position_id: int, moves: list[str]) -> GameState:
        """走法序列走完之後的對局狀態(1.1、1.2、1.3)。

        Raises:
            PositionNotFoundError: 題號不存在(6.2)。
            IllegalMoveSequenceError: 序列在該起始局面走不出(5.3)。此時**不**回傳
                任何狀態 —— 引擎會拿它自己解析到的局面回答,回傳等於把別的局面的
                合法著法交給前端,使用者會看到一盤錯誤的棋而非錯誤訊息。
            ServiceBusyError: 等待上限內未借到引擎(3.3)。
            EngineTimeoutError: 引擎查詢逾時(4.1)。
        """
        position = self._repository.get(position_id)
        return self._state_of(position, list(moves))

    def side_to_move(self, position_id: int, moves: list[str]) -> Side:
        """該局面輪到哪一方,由題目起手方與走法數推導,**不借用引擎**。

        不碰引擎是刻意的:3.3 要在進入併發閘門之前就能拒絕輪方為紅的應手請求
        (1.5),否則池滿時使用者會收到「服務忙碌」而非「輪方不符」,那是誤導。

        Raises:
            PositionNotFoundError: 題號不存在(6.2)。
        """
        position = self._repository.get(position_id)
        return side_after(position.side_to_move, len(moves))

    # --- 內部 ------------------------------------------------------------

    def _state_of(self, position: Position, moves: list[str]) -> GameState:
        """借一個引擎問一次局面。情境管理器保證任何離開路徑都歸還(4.4)。"""
        with self._pool.acquire() as engine:
            return self._state_with(engine, position, moves)

    def _state_with(
        self, engine: EngineProcess, position: Position, moves: list[str]
    ) -> GameState:
        """以**已借出的**引擎查詢局面。

        接受引擎而非自己借,使 3.3 的應手能在同一次借用內接著查應手後的局面 ——
        每手棋因此只通過一次併發閘門,直接關係到 3.1 可承載的人數。

        合法著法完全取自引擎輸出,本層不實作任何規則邏輯,循環規則局面亦然(1.6)。
        序列驗證已內建於協定層,此處不重複驗證。
        """
        legal_moves = engine.legal_moves(
            position.fen, moves, self._settings.search_timeout
        )
        return _game_state(side_after(position.side_to_move, len(moves)), legal_moves)
