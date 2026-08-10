"""EditorService:候選題目的**權威**驗證 —— 題目 schema,而後引擎可載入性。

依賴方向為 `types / errors -> config -> positions / engine -> game | editor -> models
-> main`,本模組與 `game.py` **同層**:只能向左依賴 `types`、`errors`、`config`、
`positions` 與 `engine`,**不得** import `game.py`、`models.py` 或 `main.py`。同層的
兩者互不匯入 —— 對局不該因為收題工具的存在而多一份依賴。

## 兩道檢查,順序是語意的一部分

    先 schema,而後才問引擎;schema 未通過時**一次都不借引擎**。

這不是效能取捨。引擎池是本服務唯一的併發閘門(見 `engine/pool.py`),欄位都不對的
題目沒有問到引擎的必要,借了就是白白從對局那邊拿走一格容量。順序因此以「零次借用」
釘死在測試裡,而不是靠讀程式碼相信。

## 規則只有一份

schema 判定一律呼叫 `positions.validate_position()`,本模組不複製也不擴充任何欄位
規則。理由是收題工具放行的題目,下次啟動時題庫**必須**載得起來;兩份規則遲早分岔,
分岔的下場是收題工具說可以、服務啟動時說不行。

## 可載入性只問「載不載得進去」

判定方式是對該 FEN 與**空走法序列**取一次合法著法(`go perft 1`)。取得任何結果即
視為可載入 —— 包含空清單:輪方無著可走(困斃、空盤)仍然是引擎載得進去的局面。

**不判斷紅先是否必勝**(4.10),因此不執行任何搜尋:`best_move()` 在本模組完全不
出現。是否紅先必勝屬 corpus-verification,在此順手判會讓收題卡在一次長搜尋上,而
且那個判準本來就不是「這一題能不能進題庫」。

## 字元把關不在這裡

抵達本模組的 FEN 已通過 `models.py` 的字元把關(它宣告為請求模型的欄位驗證器,
因此在路由函式被呼叫**之前**就擋掉控制字元與超長輸入)。本模組不重做字元檢查,
但也**不得**成為繞過它的入口 —— 任何未來的呼叫端都必須經由同一個請求模型進來。

## 判定與失敗的分界

「這一題不合格」是**結果**,以回傳的清單表達,`errors.py` 的七種錯誤類別不為它擴充
第八種。反之,確認**未能完成**(池滿、逾時、引擎崩潰)一律往上拋原例外:那時服務
根本沒有判定可言,把它說成「FEN 不合法」等於誣賴一份可能完全沒問題的題目(4.9)。

## 唯讀

不寫入任何檔案,不改變任何服務狀態。`positions.py` 的「唯讀」契約在本模組亦成立;
寫入題庫是瀏覽器那一端的事,服務端不具備該能力。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from service.config import Settings
from service.engine.pool import EnginePool
from service.errors import IllegalMoveSequenceError, InternalError
from service.positions import KNOWN_FIELDS, validate_position

__all__ = ["EditorService", "ValidationIssue"]


@dataclass(frozen=True)
class ValidationIssue:
    """一項未通過的驗證。

    `field` 為題目 schema 的欄位名,`None` 表示歸不到任何一個欄位(例如候選題目
    根本不是物件,或它多了一個 schema 以外的欄位 —— 那個欄位名不對應任何一格表單)。
    """

    field: str | None
    message: str


#: FEN 載不進去時的說法。指到欄位、不含任何內部細節(引擎輸出與堆疊一律留在服務端)。
UNLOADABLE_FEN_MESSAGE = (
    "引擎載不進這個 fen,它不是一個引擎認得的局面。"
    "請確認棋子擺法與 fen 的六個欄位(盤面、走子方、兩個 -、半回合數、回合數)。"
)


#: 由 `positions.py` 的錯誤訊息推出欄位名。**依序**嘗試,取第一個推得出來的。
#:
#: 順序有意義:型別不符的訊息形如「的欄位 id 必須是整數」,它同時也符合第三式的
#: 「的 X 」形狀,先比對第一式才不會把「欄位」兩個字當成欄位名。
#:
#: 這是**字面比對**,耦合到 `positions.py` 的訊息措辭:措辭一改,歸屬會退化成
#: `None`(訊息本身仍完整,使用者看得到問題,只是少了定位)。以字面比對換取
#: 「規則只有一份」是刻意的取捨 —— 另一條路是讓 schema 驗證改回傳結構化的錯誤,
#: 那等於為收題工具改動題庫載入路徑的契約,代價大得多。
_FIELD_PATTERNS = (
    re.compile(r"的欄位 (?P<field>\S+) 必須是"),  # 型別不符
    re.compile(r"缺少必填欄位:(?P<field>[^、\s]+)"),  # 缺欄位,取第一個
    re.compile(r"的 (?P<field>\S+) "),  # fen 判不出起手方
)


def _field_of(message: str) -> str | None:
    """由 schema 的錯誤訊息推出它在講哪一個欄位;推不出來時回 `None`。

    推出來的名字必須真的是題目 schema 的欄位(`KNOWN_FIELDS`),否則寧可回 `None`
    也不猜 —— 指錯欄位比不指欄位更糟,維護者會照著去改一個本來沒問題的格子。
    """
    for pattern in _FIELD_PATTERNS:
        match = pattern.search(message)
        if match and match.group("field") in KNOWN_FIELDS:
            return match.group("field")
    return None


class EditorService:
    """候選題目的驗證。不持有任何跨請求狀態,亦不改變任何服務狀態。

    協作者由呼叫端注入:引擎池提供唯一的併發閘門,設定提供查詢逾時。兩者都不由本類
    建立,測試因此能整組換成替身 —— 「schema 未通過時零次借用」正是靠這件事測得到。
    """

    def __init__(self, pool: EnginePool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings

    def validate(self, raw: object) -> list[ValidationIssue]:
        """驗證候選題目。回傳空清單代表合格(4.7)。

        兩道檢查依序進行:題目 schema,而後引擎可載入性。schema 未通過時**不借
        引擎** —— 欄位都不對的題目沒有問到引擎的必要,而引擎池是對局也在排隊的
        那道閘門。

        Args:
            raw: 任意 JSON 可解碼的值,包含非物件 —— 「這根本不是一個題目」同樣
                以未通過項目表達,不以例外表達。

        Returns:
            未通過項目的清單,每一項盡可能定位到欄位;空清單代表合格。

        Raises:
            ServiceBusyError: 等待上限內未借到引擎(4.9)。
            EngineTimeoutError: 引擎查詢逾時(4.9)。
            InternalError: 引擎已崩潰或無法服務(4.9)。三者都代表確認**未能完成**,
                此時**沒有**判定可言,呼叫端必須據此回報「確認未能完成」而不是
                「這一題不合格」——後者會擋下一份可能完全沒問題的題目,還把責任
                推給使用者。
        """
        try:
            position = validate_position(raw)
        except ValueError as error:
            # 題目 schema 的說法一字不改地成為訊息:重寫等於養出第二套說法,而
            # 那套說法遲早與題庫載入時的說法對不上。
            message = str(error)
            return [ValidationIssue(field=_field_of(message), message=message)]

        return self._loadability_issues(position.fen)

    # --- 內部 ------------------------------------------------------------

    def _loadability_issues(self, fen: str) -> list[ValidationIssue]:
        """借一個引擎問這個 FEN 載不載得進去。

        情境管理器保證任何離開路徑都歸還:回傳、判定為不合格、以及往上拋的服務失敗
        全都經過同一個 `finally`,漏掉任何一條都是永久少一格池容量。

        查詢的走法序列固定為空,逾時取自設定的 `search_timeout` —— 與 `game.py` 的
        局面查詢同一個上界,收題不該有自己的一套時間預算。
        """
        with self._pool.acquire() as engine:
            try:
                engine.legal_moves(fen, [], self._settings.search_timeout)
            except IllegalMoveSequenceError:
                # 走法序列是空的,序列不可能「走不出來」:引擎最後停在別的局面,
                # 只能是它沒有接受這個 FEN。
                return [self._unloadable()]
            except InternalError:
                # 引擎層解不出這個查詢。兩種來源必須分開:
                #   - 引擎仍健康 → 是 FEN 解不出來(實測真實引擎:欄位不足六個、
                #     fullmove 不是數字或小於 1,都走這裡),屬「載不進去」
                #   - 引擎已不健康 → 是引擎壞了,屬確認未能完成(4.9),原樣往上拋
                # 分不開的話,兩種截然不同的原因會擠在同一句話裡:引擎崩潰時告訴
                # 維護者「你的 FEN 不合法」是誣賴,而 FEN 真的壞掉時回 500 則讓他
                # 完全不知道該改哪裡。
                if not engine.is_healthy():
                    raise
                return [self._unloadable()]
        return []

    def _unloadable(self) -> ValidationIssue:
        """載不進去一律歸到 `fen`:那是候選題目裡唯一與局面有關的欄位。"""
        return ValidationIssue(field="fen", message=UNLOADABLE_FEN_MESSAGE)
