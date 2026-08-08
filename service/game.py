"""GameService:對局推進的判定 —— 輪方推導、終局認定、黑方應手與三態信號。

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

## 三態信號只認 `mate`

信號的分類規則見 `classify_score()`:只有 `mate` 型別的分數會產生勝負傾向,`cp`
與「引擎沒給分數」一律回報未知,**不得據以推斷勝負傾向**(2.3)。信號是諮詢性質
的,它與終局判定之間沒有任何一條連線 —— `black_reply()` 回傳的 `state` 同樣只
經由 `_game_state()` 產生。

## 不持有任何跨請求狀態

服務對每個請求無記憶:前端每次重送題號與完整走法序列,本類只是把它們轉成一次引擎
查詢。`GameService` 的實例欄位只有注入的協作者,沒有任何一局的進度。

## 一次請求只通過一次併發閘門

`black_reply()` 在同一次借用內完成應手搜尋與其後的局面查詢(`_state_with()` 接受
已借出的引擎)。借兩次的話,同樣的池容量只能承載一半的人,且第二次借用可能在池滿
時失敗 —— 使用者會在應手已經算好之後才收到忙碌錯誤。輪方不符則相反,必須在借用
**之前**就以 `side_to_move()`(不碰引擎)判定並拒絕(1.5)。
"""

from __future__ import annotations

from service.config import Settings
from service.engine.pool import EnginePool
from service.engine.process import EngineProcess
from service.errors import WrongSideToMoveError
from service.positions import PositionRepository
from service.types import (
    BlackReply,
    GameState,
    Position,
    Score,
    ScoreKind,
    Side,
    Signal,
)

__all__ = ["GameService", "side_after", "classify_score"]


def side_after(start: Side, move_count: int) -> Side:
    """題目起手方走了 `move_count` 步之後輪到哪一方。

    起手方取自題目,**不得硬編為紅** —— 題庫容得下黑先的排局,寫死會讓那些題目的
    勝負方整個顛倒。
    """
    return start if move_count % 2 == 0 else _opponent(start)


def classify_score(score: Score | None) -> tuple[Signal, int | None]:
    """把引擎分數分類為三態信號與殺著倒數(2.1、2.2、2.3)。

    分數視角為**當前輪方**,而應手只在輪方為黑時發生,故此處一律以黑方視角解讀:
    `mate` 為負代表黑方將被殺(紅方即將取勝,並附殺著倒數),為正代表黑方可殺。
    依 UCI 慣例,`mate 0` 代表輪方已被將死,同樣歸紅方即將取勝、倒數為 0。

    ## 只有 `mate` 型別會產生勝負傾向

    `cp` 一律歸「未搜得殺著」,**連正負號都不看**。實測 200k 節點下,一個實為
    `mate -15`(黑方將被殺)的局面回報 `cp 526` —— 方向還是相反的。據 cp 推斷等於
    在使用者必勝時告訴他正在落敗,那比誠實地說「未知」糟得多(2.3)。排局的評估
    函數本就不可靠:子力早已不對等,勝負全在殺法能否走通。

    引擎完全沒給分數時同樣歸未知 —— 此時的勝負由 `GameState` 表達,不由信號表達。
    (黑方已被將死的局面,實測真實引擎仍會給 `score mate 0`,故走的是上一段那條路。)

    Returns:
        (信號, 殺著倒數)。倒數僅在紅方即將取勝時有值,且**可能高估 1 步**,
        見 `GameService.black_reply`。
    """
    if score is None or score.kind is not ScoreKind.MATE:
        return Signal.UNKNOWN, None
    if score.value > 0:
        return Signal.BLACK_WINNING, None
    return Signal.RED_WINNING, -score.value


def _opponent(side: Side) -> Side:
    return Side.BLACK if side is Side.RED else Side.RED


def _moves_after(moves: list[str], reply: str | None) -> list[str]:
    """套用黑方應手之後的走法序列。

    引擎回報無著可走時序列**不追加任何一步** —— 要問的正是當前這個局面,它本身
    就是真終局。追加一個不存在的著法只會讓協定層的序列驗證炸掉。
    """
    return list(moves) if reply is None else [*moves, reply]


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

    def black_reply(self, position_id: int, moves: list[str]) -> BlackReply:
        """黑方應手、其三態信號,以及**走後**的完整對局狀態(1.4、2.1–2.4)。

        走後狀態同批回傳,前端因此不必為了畫出新盤面或知道自己贏了沒有而再發一次
        局面查詢 —— 尤其是最後一手:紅方走出致勝一手後,引擎對黑方回報無著可走
        (`move` 為 `None`),同一份回應裡的 `state` 就已經是「對局結束、紅方獲勝」。
        那是每一題排局的**最後一手**,不是邊緣案例。

        ## 殺著倒數是約數,不是確數

        `mate_in` 直接取自引擎在 `search_nodes` 個節點下的回報,**可能高估 1 步**:
        實測同一局面 250k 節點回報 16,1M 節點才收斂到 15。取得精確倒數需 4.5 倍
        算力(每手 0.55 秒而非 0.12 秒),而倒數屬諮詢性質,故刻意不付這個代價
        (見 `config.DEFAULT_SEARCH_NODES` 與 design「搜尋節點數與三態信號的關係」)。
        **呈現時宜表述為「約 N 步」而非確數。**

        ## 信號不參與終局判定

        `state` 一律由 `_state_with()` 產出,而該路徑的參數裡沒有分數,因此信號取
        任何值都不影響 `over`(2.4)。實測黑方已被將死時,引擎在 `bestmove (none)`
        之外還會給 `score mate 0`,信號因而是「紅方即將取勝、倒數 0」;引擎若一分
        未報,信號則落在「未知」。**兩種輸入得到的 `state` 完全相同** —— 勝負由
        `state` 表達,信號只是諮詢。

        Raises:
            PositionNotFoundError: 題號不存在(6.2)。
            WrongSideToMoveError: 該局面輪方為紅(1.5)。此判定在**借引擎之前**
                完成,否則池滿時使用者會收到「服務忙碌」而非「輪方不符」——
                前者會讓人一直重試,而真正的問題是 client 的對局狀態已經對不上。
            IllegalMoveSequenceError: 序列在該起始局面走不出(5.3)。
            ServiceBusyError: 等待上限內未借到引擎(3.3)。
            EngineTimeoutError: 引擎查詢逾時(4.1)。
        """
        if self.side_to_move(position_id, moves) is not Side.BLACK:
            raise WrongSideToMoveError()
        position = self._repository.get(position_id)
        with self._pool.acquire() as engine:
            best = engine.best_move(
                position.fen,
                list(moves),
                self._settings.search_nodes,
                self._settings.search_timeout,
            )
            # 應手與其後的局面共用這一次借用:每手棋只通過一次併發閘門,直接關係到
            # 3.1 可承載的人數,也避免「應手已算好卻在第二次借用時回報忙碌」。
            state = self._state_with(engine, position, _moves_after(moves, best.move))
        signal, mate_in = classify_score(best.score)
        return BlackReply(move=best.move, signal=signal, mate_in=mate_in, state=state)

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
