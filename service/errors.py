"""錯誤模型:錯誤類別列舉、服務例外型別、對 HTTP 狀態的映射。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組位於最左端,**不得 import 任何其他 service 模組**。

`ErrorCode` 的字串值是對外契約(5.1):前端據此決定 UI 行為,重構時不得變動。
對外訊息一律為可直接顯示的通用敘述,**不得包含內部檔案路徑、堆疊追蹤或引擎原始
輸出**(5.4);內部細節請以例外鏈(`raise ... from cause`)留在服務端。
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "ErrorCode",
    "ServiceError",
    "InvalidMoveFormatError",
    "PositionNotFoundError",
    "IllegalMoveSequenceError",
    "WrongSideToMoveError",
    "ServiceBusyError",
    "EngineTimeoutError",
    "InternalError",
    "HTTP_STATUS_BY_CODE",
    "EXCEPTION_BY_CODE",
]


class ErrorCode(str, Enum):
    """七種錯誤類別。代碼字串等同名稱,序列化後前端可直接比對。"""

    INVALID_MOVE_FORMAT = "INVALID_MOVE_FORMAT"  # 著法不符 UCI 格式(5.2)
    POSITION_NOT_FOUND = "POSITION_NOT_FOUND"  # 題目 id 不存在(6.2)
    ILLEGAL_MOVE_SEQUENCE = "ILLEGAL_MOVE_SEQUENCE"  # 走法序列走不出(5.3)
    WRONG_SIDE_TO_MOVE = "WRONG_SIDE_TO_MOVE"  # 請求黑方應手但輪方為紅(1.5)
    SERVICE_BUSY = "SERVICE_BUSY"  # 等待上限內未借到引擎(3.3)
    ENGINE_TIMEOUT = "ENGINE_TIMEOUT"  # 搜尋超過逾時上限(4.1)
    INTERNAL = "INTERNAL"  # 其他未預期失敗(5.1、5.4)


#: 錯誤類別到 HTTP 狀態的映射。此處為唯一來源,例外型別由此讀取,避免兩處漂移。
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_MOVE_FORMAT: 400,
    ErrorCode.POSITION_NOT_FOUND: 404,
    ErrorCode.ILLEGAL_MOVE_SEQUENCE: 409,
    ErrorCode.WRONG_SIDE_TO_MOVE: 409,
    ErrorCode.SERVICE_BUSY: 503,
    ErrorCode.ENGINE_TIMEOUT: 504,
    ErrorCode.INTERNAL: 500,
}


class ServiceError(Exception):
    """所有服務例外的共同基底,使呼叫端能以單一 except 攔截整族。"""

    code: ErrorCode = ErrorCode.INTERNAL
    default_message: str = "服務發生未預期的錯誤,請稍後再試"

    def __init__(self, message: str | None = None) -> None:
        self._message = message if message else self.default_message
        super().__init__(self._message)

    @property
    def message(self) -> str:
        """可直接顯示給使用者的訊息,保證不含內部細節。"""
        return self._message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_CODE[self.code]


class InvalidMoveFormatError(ServiceError):
    """著法不符 UCI 格式(5.2)。訊息可指出不合法的著法本身。"""

    code = ErrorCode.INVALID_MOVE_FORMAT
    default_message = "著法格式不合法"


class PositionNotFoundError(ServiceError):
    """題目 id 不存在(6.2)。"""

    code = ErrorCode.POSITION_NOT_FOUND
    default_message = "找不到指定的題目"


class IllegalMoveSequenceError(ServiceError):
    """走法序列在該局面走不出(5.3)。"""

    code = ErrorCode.ILLEGAL_MOVE_SEQUENCE
    default_message = "走法序列與局面不一致"


class WrongSideToMoveError(ServiceError):
    """請求黑方應手但輪方為紅(1.5)。"""

    code = ErrorCode.WRONG_SIDE_TO_MOVE
    default_message = "目前不是該方行棋"


class ServiceBusyError(ServiceError):
    """等待上限內未借到引擎(3.3)。此為預期中的暫時狀態,不是故障。"""

    code = ErrorCode.SERVICE_BUSY
    default_message = "服務忙碌中,請稍後再試"


class EngineTimeoutError(ServiceError):
    """搜尋超過逾時上限(4.1)。"""

    code = ErrorCode.ENGINE_TIMEOUT
    default_message = "運算逾時,請重試"


class InternalError(ServiceError):
    """其他未預期失敗(5.1、5.4)。

    訊息固定為通用敘述且**不接受呼叫端傳入的細節** —— 未預期失敗的細節必然來自
    堆疊、路徑或引擎輸出,一旦允許覆寫就等於開了外洩的口子。細節請以
    `raise InternalError() from cause` 保留在服務端。
    """

    code = ErrorCode.INTERNAL
    default_message = "服務發生未預期的錯誤,請稍後再試"

    def __init__(self, message: str | None = None) -> None:  # noqa: ARG002
        super().__init__(None)


#: 錯誤類別到例外型別的映射。
EXCEPTION_BY_CODE: dict[ErrorCode, type[ServiceError]] = {
    ErrorCode.INVALID_MOVE_FORMAT: InvalidMoveFormatError,
    ErrorCode.POSITION_NOT_FOUND: PositionNotFoundError,
    ErrorCode.ILLEGAL_MOVE_SEQUENCE: IllegalMoveSequenceError,
    ErrorCode.WRONG_SIDE_TO_MOVE: WrongSideToMoveError,
    ErrorCode.SERVICE_BUSY: ServiceBusyError,
    ErrorCode.ENGINE_TIMEOUT: EngineTimeoutError,
    ErrorCode.INTERNAL: InternalError,
}
