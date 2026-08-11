"""HTTP 層的請求與回應模型,以及著法格式驗證(5.2)。

依賴方向為 `types / errors -> config -> positions / engine -> game / editor -> models -> main`,
本模組位於 `main` 之左:可向左匯入領域型別與錯誤型別,**不得** import `main.py`。
領域型別一律由 `types.py` 匯入,**絕不在此重新定義** —— 兩份同名列舉的成員永遠不
相等,`is` 比較與 `isinstance` 會靜默失效。本模組只做邊界轉換:把領域物件攤平成
對外契約,把外來 JSON 收斂成領域可用的值。

## 走法序列走請求主體,不走 query string

POC 的 `/api/state?moves=...` 在長局會使 URL 過長,這是本 spec 明列要替換的技術債。
兩個 POST 端點共用 `MoveSequenceRequest`,序列在主體裡。

## 著法格式驗證攔在服務層之前

`MoveSequenceRequest` 一旦驗證失敗就拋 `InvalidMoveFormatError`,而請求主體的驗證
發生在路由函式被呼叫之前 —— 格式不合法的請求因此**從未觸及引擎池**,不會占掉併發
閘門的名額(3.3 的容量是稀缺資源)。

驗證器刻意拋 `InvalidMoveFormatError` 而**不是** `ValueError`:pydantic 只攔截
`ValueError` 與 `AssertionError` 並包成框架的泛用結構錯誤(422),那會讓 5.2 明列的
`INVALID_MOVE_FORMAT`(400)拿不到。非這兩類的例外會原樣往外傳,交由任務 4.3 的
全域例外處理器映射為錯誤類別碼。

## 送往引擎的 FEN 只做字元把關,不做文法驗證

`CandidatePositionRequest` 是專案中第一條**使用者文字進到引擎輸入**的路徑。引擎協定
行導向,而 `engine/process.py` 的 `_position_command()` 是裸的字串插值 —— FEN 裡一個
換行就能把一行指令變成兩行。把關因此與著法格式驗證同位置、同錯誤類別:寫在請求模型
上,攔在路由函式之前(9.1–9.4)。

把關**刻意止於「這個字串跳不出這一行指令」**:局面合不合法由引擎判定,在此重做一份
FEN 文法只會製造第二個真相來源與誤擋(9.5)。通過側的正確性以題庫中每一個真實 FEN
作為回歸樣本,見 `tests/test_models.py`。

## 空值一律保留欄位,不省略

回應模型不設 `exclude_none`,任何可為空的欄位(`mate_in`、`winner`、`max_dtm`、
`source`)在空值時仍出現於 JSON。前端因此不必分辨「沒有這個欄位」與
「值為空」—— 見 `BlackMoveResponse.mate_in` 的說明,那裡的差別會吃掉終局那一手。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from service.errors import ErrorCode, InvalidMoveFormatError, ServiceError
from service.types import BlackReply, GameState, Position, Side, Signal

__all__ = [
    "UCI_MOVE_PATTERN",
    "FEN_PATTERN",
    "MAX_FEN_LENGTH",
    "validate_move",
    "validate_fen",
    "MoveSequenceRequest",
    "CandidatePositionRequest",
    "GameStateResponse",
    "BlackMoveResponse",
    "PositionResponse",
    "ErrorResponse",
]

#: UCI 座標記法:檔 `a`–`i`(紅方左至右)、列 `0`–`9`(紅方底線為 0)。
#: 例:炮二平五 = `h2e2`。見 `.kiro/steering/tech.md` 的「走法格式」。
#: 象棋沒有升變也沒有王車易位,故著法恆為四個字元,不存在後綴。
UCI_MOVE_PATTERN = re.compile(r"[a-i][0-9][a-i][0-9]")

#: FEN 可出現的字元:英文字母、數字、`/`、空白、`-`。**不含任何控制字元** ——
#: 引擎協定是行導向的,`engine/process.py` 的 `_position_command()` 又是裸的字串
#: 插值(`f"position fen {fen}"`),FEN 裡一個換行就會讓一行指令變成兩行,而第二
#: 行的內容由送出者決定。
#:
#: **這是字元集白名單,不是 FEN 文法**(9.5)。局面合不合法由引擎判定(見
#: `.kiro/steering/tech.md` 的第二個不可動搖的約束),在此重做一份文法只會製造第二
#: 個真相來源,而且訂得越緊、誤擋合法變體的機會越大。本層只保證一件事:**這個字串
#: 跳不出這一行 UCI 指令**。
FEN_PATTERN = re.compile(r"[A-Za-z0-9/ \-]+")

#: FEN 的長度上限(9.3)。象棋盤 90 格全滿需 90 個字元加 9 個 `/`,再加走子方、
#: 可著法、半手數與回合數四欄,任何真實 FEN 都在 120 字元以內(題庫現有的落在
#: 57–65)。取 256 是刻意留兩倍餘裕:高到不可能誤擋任何真實局面,低到即使被大量
#: 送出也構不成負擔。長度是**獨立**的一道 —— 全部由白名單字元組成的超長字串同樣
#: 該被擋下,不必等到引擎那頭去承受。
MAX_FEN_LENGTH = 256

#: 錯誤訊息中回報不合法輸入的長度上限。訊息會原樣回給使用者,不設上限等於讓對方
#: 決定回應大小 —— 指出是哪一手只需要看得出來,不需要完整回放。
MAX_REPORTED_LENGTH = 32


def validate_move(value: Any) -> str:
    """確認單一著法符合 UCI 座標格式,回傳原值;不符即拒絕並指出該著法(5.2)。

    **著法是否合法(能不能走)不在此判定** —— 那由引擎回答,序列走不出時協定層的
    序列驗證會拋 `IllegalMoveSequenceError`(5.3)。此處只管形狀:形狀對了才有資格
    送進引擎,形狀錯了連借引擎都不必。

    Raises:
        InvalidMoveFormatError: 值不是字串,或不符 UCI 座標格式。訊息指出該著法。
    """
    if isinstance(value, str) and UCI_MOVE_PATTERN.fullmatch(value):
        return value
    raise InvalidMoveFormatError(f"著法格式不合法:「{_summarize(value)}」")


def validate_fen(value: str) -> str:
    """確認 FEN 只由 FEN 表示法用得到的字元組成且長度在上限內,回傳原值(9.1–9.3)。

    **局面合不合法不在此判定** —— 那由引擎回答(`tech.md` 的第二個不可動搖的約束)。
    此處保證的只有一件事:這個字串送進 `position fen <fen>` 之後,仍然只是**一行**
    指令。UCI 協定行導向,而指令是裸的字串插值,換行即可讓後半段變成另一道指令。

    長度先於字元集判定:超長的輸入不必逐字掃過才知道要擋,而長度本身就是獨立的一
    道 —— 全由白名單字元組成的超長字串同樣不該送往引擎(9.3)。

    Raises:
        InvalidMoveFormatError: 含集合外的字元(任何控制字元皆屬之),或超出長度
            上限。錯誤類別與著法格式驗證相同(9.4)。
    """
    if len(value) <= MAX_FEN_LENGTH and FEN_PATTERN.fullmatch(value):
        return value
    raise InvalidMoveFormatError(f"FEN 格式不合法:「{_summarize(_printable(value))}」")


def _printable(text: str) -> str:
    """把不可列印的字元換成其跳脫寫法,供錯誤訊息使用。

    被擋下的內容**不得原樣回到訊息裡**:訊息會流進日誌與畫面,把換行原樣送回去等
    於在另一個行導向的介面上重演同一個問題。跳脫之後使用者仍看得出送了什麼
    (`\\n` 比一個看不見的字元有用得多)。
    """
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)


def _summarize(value: Any) -> str:
    """把任意輸入濃縮成可安全回報的短字串。

    非字串以 `repr` 呈現(否則 `None` 會變成空白,使用者看不出送了什麼),過長者
    截斷 —— 訊息會回給使用者,不得把任意長度的輸入原樣送回去。
    """
    text = value if isinstance(value, str) else repr(value)
    if len(text) > MAX_REPORTED_LENGTH:
        return text[:MAX_REPORTED_LENGTH] + "…"
    return text


class MoveSequenceRequest(BaseModel):
    """`POST /api/state` 與 `POST /api/black-move` 共用的請求主體。

    兩個端點的輸入完全相同(題號加走法序列),刻意共用一個模型:契約分兩份會漂移,
    而它們表達的是同一件事 —— 前端持有完整對局狀態,每次重送題號與完整走法序列
    (服務對每個請求無記憶,見 `.kiro/steering/tech.md` 的第一個不可動搖的約束)。

    序列長度上限屬 requirements 的 Backlog(7.3),本輪不實作。
    """

    #: 未知欄位一律拒絕。`moves` 拼成 `move` 若被靜默忽略,請求會被當成「起始局面」
    #: 處理,使用者拿到的是另一個局面的合法著法而非錯誤 —— 與引擎靜默忽略非法著法
    #: 同一類的失敗模式,而那正是本 spec 花最多力氣防的一件事。
    model_config = ConfigDict(extra="forbid")

    position_id: int = Field(
        description="題號,全域唯一。不存在時由題庫回報 POSITION_NOT_FOUND(6.2)。"
    )
    moves: list[str] = Field(
        default_factory=list,
        description="自題目起始局面起的完整走法序列,UCI 座標。空序列即起始局面。",
    )

    @field_validator("moves", mode="before")
    @classmethod
    def _check_move_format(cls, value: Any) -> Any:
        """逐一驗證序列中的著法,不合法即指出**該**著法(5.2)。

        走 `mode="before"` 是為了讓非字串元素(例如 JSON 數字)也走
        `INVALID_MOVE_FORMAT` 這條路,而不是掉進框架的泛用結構錯誤 —— 同一類使用者
        輸入若回兩種錯誤類別,client 無法一致處理(5.1)。

        值本身不是序列時原樣放行,交由 pydantic 回報結構錯誤:那是請求形狀的問題,
        不是某一手著法的問題。
        """
        if not isinstance(value, (list, tuple)):
            return value
        return [validate_move(move) for move in value]


class CandidatePositionRequest(BaseModel):
    """`POST /api/editor/validate` 的請求主體:一份還不在任何檔案裡的候選題目。

    `position` 刻意保留為**未經模型化的物件**:題目 schema 的權威在
    `.kiro/steering/structure.md` 與 `service/positions.py`,此處若再宣告一次
    `id`/`title`/`description`/`difficulty`/`tags`,就成了第二份規則 —— 兩邊對必填
    欄位或難度值域的看法遲早分歧,而候選題目「合不合格」只該有一個答案。欄位的判定
    因此完全交給 `positions.validate_position()`,本模型只負責**取出其中的 FEN 並對
    字元把關**。

    這是專案中第一條使用者文字進到引擎輸入的路徑,把關寫在請求模型上而非路由函式裡
    是刻意的:請求主體的驗證發生在路由函式被呼叫**之前**,字元不合格的 FEN 因此連
    「借一個引擎」那一步都沒有機會執行(9.1)。
    """

    #: 未知欄位一律拒絕,理由同 `MoveSequenceRequest`:`position` 拼錯若被靜默忽略,
    #: 請求會變成「驗證一份空題目」,使用者拿到的是一串莫名其妙的欄位錯誤。
    model_config = ConfigDict(extra="forbid")

    position: dict[str, Any] = Field(
        description=(
            "候選題目,欄位依題目 schema。**本模型不宣告其欄位** —— schema 的權威"
            "在題庫模組,此處只取出 `fen` 做字元把關。"
        )
    )

    @field_validator("position", mode="before")
    @classmethod
    def _check_fen_characters(cls, value: Any) -> Any:
        """取出候選題目的 FEN 並施加字元集與長度把關(9.1–9.3)。

        值不是物件時原樣放行,交由 pydantic 回報結構錯誤:那是請求形狀的問題。

        **`fen` 缺席或不是字串時同樣放行**,不在此判斷。那是「欄位不對」而非「字元
        危險」,交由 `validate_position()` 以題目 schema 的說法回報,使用者才會看到
        「缺少 fen 欄位」這種有用的訊息,而不是一句格式不合法。放行也不擴大攻擊面:
        送進引擎的是一行文字,非字串根本走不到那裡,而 schema 未通過時服務層依約定
        不借引擎。

        回傳的是**原物件本身**,不做任何正規化 —— 寫入題庫的動作在瀏覽器端,服務端
        驗過的內容必須與使用者要寫出去的那一份逐字相同。
        """
        if not isinstance(value, dict):
            return value
        fen = value.get("fen")
        if isinstance(fen, str):
            validate_fen(fen)
        return value


class GameStateResponse(BaseModel):
    """某一局面的對局狀態。`GameState` 的對外投影。"""

    side_to_move: Side = Field(description="該局面輪到哪一方行棋。")
    legal_moves: list[str] = Field(
        description="輪方的全部合法著法,UCI 座標。取自引擎,服務不實作象棋規則。"
    )
    over: bool = Field(
        description=(
            "對局是否結束。**只由合法著法數是否為零決定**,與任何評分無關"
            "(1.3、2.4)—— 引擎回報殺棋不代表對局結束,使用者仍要把殺法走完。"
        )
    )
    winner: Side | None = Field(
        description="勝方。`over` 為真時必有值,否則為空。"
    )

    @classmethod
    def from_domain(cls, state: GameState) -> GameStateResponse:
        return cls(
            side_to_move=state.side_to_move,
            legal_moves=list(state.legal_moves),
            over=state.over,
            winner=state.winner,
        )


class BlackMoveResponse(BaseModel):
    """黑方應手、三態諮詢信號,以及**走後**的完整對局狀態。

    走後狀態同批回傳,前端因此不必為了畫出新盤面或知道自己贏了沒有而再發一次局面
    查詢 —— 每手棋只通過一次併發閘門,直接關係到可承載的人數(3.1)。
    """

    move: str | None = Field(
        description=(
            "黑方應手,UCI 座標;引擎回報無著可走時為空。"
            "**為空不代表沒有信號** —— 兩個欄位彼此獨立,見 `signal`。"
        )
    )
    signal: Signal = Field(
        description=(
            "三態諮詢信號(2.1)。諮詢性質,**不決定停局** —— 對局是否結束一律看"
            " `state.over`。`cp` 分數與引擎未給分一律歸 `unknown`,不得據以推斷"
            "勝負傾向(2.3)。"
        )
    )
    mate_in: int | None = Field(
        description=(
            "殺著倒數步數,僅信號為 `red_winning` 時有值(2.2)。"
            "**是約數不是確數:可能高估 1 步**,呈現時宜表述為「約 N 步」——"
            "精確倒數需 4.5 倍算力,而倒數屬諮詢性質,刻意不付這個代價"
            "(見 design「搜尋節點數與三態信號的關係」)。"
            "**值可能為 0**:黑方已被將死時引擎回報 `score mate 0`,那是每一題排局"
            "的最後一手。判斷有無一律用 `is not None`,用 falsy 判斷會把它吞掉。"
        )
    )
    state: GameStateResponse = Field(
        description="黑方應手之後的對局狀態;黑方無著可走時即為當前局面本身。"
    )

    @classmethod
    def from_domain(cls, reply: BlackReply) -> BlackMoveResponse:
        """由 `BlackReply` 建出回應。

        四個欄位一律逐一搬移,**不做任何「有值才帶」的省略** —— `mate_in` 為 0 與
        `move` 為 `None` 都是終局那一手的正常取值,省略會讓前端看不到倒數,或分不清
        「黑方無著」與「服務漏傳」。
        """
        return cls(
            move=reply.move,
            signal=reply.signal,
            mate_in=reply.mate_in,
            state=GameStateResponse.from_domain(reply.state),
        )


class PositionResponse(BaseModel):
    """題目的起始局面與對局所需的題目資訊(6.1)。

    欄位對應 `.kiro/steering/structure.md` 的題目 schema,外加起始局面的對局狀態
    與出處。出處**不是** schema 欄位:題目 JSON 放在哪個資料夾就代表出自哪一本書,
    故它另有來源(`PositionRepository.source_of()`),見 `source`。
    """

    id: int = Field(description="題號,全域唯一,跨書連續。")
    title: str = Field(description="局名,列表顯示用,單行精簡。")
    description: str = Field(description="完整描述,涵蓋書名、局號、局名。")
    fen: str = Field(description="起始局面。")
    side_to_move: Side = Field(
        description=(
            "起手方。**不得假設為紅** —— 題庫容得下黑先的排局。不是題目 schema 的"
            "欄位,由 `fen` 的走子方那一欄推導(見 `positions.py` 的 "
            "`_side_from_fen`),在此攤成獨立欄位是省得每個 client 自己去剖 FEN。"
        )
    )
    difficulty: int = Field(description="難度分級。")
    tags: list[str] = Field(description="題目標籤,可多個。")
    max_dtm: int | None = Field(
        default=None,
        description=(
            "最長殺著距離。由題目驗證工具(corpus-verification)日後回填,"
            "現階段可為空,**不得視為必填**。"
        ),
    )
    source: str | None = Field(
        default=None,
        description=(
            "出處書名。由題目所在資料夾表達而非題目欄位,故不在題目 schema 內;"
            "取自 `PositionRepository.source_of()`。"
        ),
    )
    state: GameStateResponse = Field(
        description="起始局面的對局狀態,含該局面的全部合法著法。"
    )

    @classmethod
    def from_domain(
        cls,
        position: Position,
        state: GameState,
        source: str | None = None,
    ) -> PositionResponse:
        """由題目與其起始局面狀態建出回應。

        `source` 另外傳入而非取自 `position`:出處由資料夾表達,`Position` 裡沒有
        這個欄位。呼叫端未提供時留空,前端據此不顯示出處。
        """
        return cls(
            id=position.id,
            title=position.title,
            description=position.description,
            fen=position.fen,
            side_to_move=position.side_to_move,
            difficulty=position.difficulty,
            tags=list(position.tags),
            max_dtm=position.max_dtm,
            source=source,
            state=GameStateResponse.from_domain(state),
        )


class ErrorResponse(BaseModel):
    """錯誤回應。所有失敗路徑共用同一個形狀(5.1)。"""

    code: ErrorCode = Field(
        description=(
            "機器可讀的錯誤類別碼,client 據此決定 UI 行為。"
            "字串值是對外契約,重構時不得變動。"
        )
    )
    message: str = Field(
        description=(
            "可直接顯示給使用者的訊息。"
            "**不含內部檔案路徑、堆疊追蹤或引擎原始輸出**(5.4)。"
        )
    )

    @classmethod
    def from_error(cls, error: ServiceError) -> ErrorResponse:
        """由服務例外建出回應。

        訊息一律取自 `ServiceError.message`,**不是** `str(error)` 也不是
        `error.args` —— `message` 是唯一保證經過 5.4 檢視的來源。取例外本身的字串
        表示,等於把日後任何一個「順手把細節塞進例外」的改動直接送到使用者眼前。

        只接受 `ServiceError`:未預期的例外必須先在任務 4.3 的全域處理器收斂為
        `InternalError`,不得直接餵進來。
        """
        return cls(code=error.code, message=error.message)
