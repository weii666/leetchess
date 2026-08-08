"""錯誤模型的契約測試(對應 requirements 5.1、5.4,design 的「錯誤類別」表)。

類別碼字串是對外契約:前端據此決定 UI 行為,不得因重構而變動。
"""

from __future__ import annotations

import pytest

from service import errors as e


# design.md「HTTP 層 → 錯誤類別」表格,逐列固定下來
EXPECTED = [
    ("INVALID_MOVE_FORMAT", 400, "InvalidMoveFormatError"),
    ("POSITION_NOT_FOUND", 404, "PositionNotFoundError"),
    ("ILLEGAL_MOVE_SEQUENCE", 409, "IllegalMoveSequenceError"),
    ("WRONG_SIDE_TO_MOVE", 409, "WrongSideToMoveError"),
    ("SERVICE_BUSY", 503, "ServiceBusyError"),
    ("ENGINE_TIMEOUT", 504, "EngineTimeoutError"),
    ("INTERNAL", 500, "InternalError"),
]


def test_error_code_covers_exactly_the_seven_designed_categories() -> None:
    assert [c.name for c in e.ErrorCode] == [name for name, _, _ in EXPECTED]


@pytest.mark.parametrize("name,_status,_cls", EXPECTED)
def test_error_code_value_is_stable_machine_readable_string(
    name: str, _status: int, _cls: str
) -> None:
    code = e.ErrorCode[name]
    # 代碼字串等同名稱,序列化後前端可直接比對
    assert code.value == name
    assert code == name


@pytest.mark.parametrize("name,status,_cls", EXPECTED)
def test_http_status_mapping(name: str, status: int, _cls: str) -> None:
    assert e.HTTP_STATUS_BY_CODE[e.ErrorCode[name]] == status


def test_http_status_mapping_is_total() -> None:
    assert set(e.HTTP_STATUS_BY_CODE) == set(e.ErrorCode)


@pytest.mark.parametrize("name,status,cls_name", EXPECTED)
def test_each_code_has_an_exception_type(
    name: str, status: int, cls_name: str
) -> None:
    code = e.ErrorCode[name]
    cls = getattr(e, cls_name)
    assert issubclass(cls, e.ServiceError)
    assert e.EXCEPTION_BY_CODE[code] is cls
    assert cls.code is code
    assert cls().http_status == status


def test_exception_mapping_is_total_and_injective() -> None:
    assert set(e.EXCEPTION_BY_CODE) == set(e.ErrorCode)
    classes = list(e.EXCEPTION_BY_CODE.values())
    assert len(set(classes)) == len(classes)


def test_service_error_is_catchable_as_one_family() -> None:
    for cls in e.EXCEPTION_BY_CODE.values():
        with pytest.raises(e.ServiceError):
            raise cls()


def test_service_error_carries_code_and_message() -> None:
    err = e.PositionNotFoundError("找不到題目 9999")
    assert err.code is e.ErrorCode.POSITION_NOT_FOUND
    assert err.http_status == 404
    assert err.message == "找不到題目 9999"
    assert str(err) == "找不到題目 9999"


def test_every_exception_has_a_displayable_default_message() -> None:
    for cls in e.EXCEPTION_BY_CODE.values():
        err = cls()
        assert err.message
        assert err.message.strip() == err.message


def test_internal_error_never_carries_caller_supplied_detail() -> None:
    """5.4:INTERNAL 一律為固定的通用訊息,不含任何內部細節。"""
    leaky = "/Users/carlos/sandbox/leetchess/service/engine/process.py 第 42 行"
    err = e.InternalError(leaky)
    assert leaky not in err.message
    assert leaky not in str(err)
    assert err.message == e.InternalError.default_message


@pytest.mark.parametrize("name,_status,cls_name", EXPECTED)
def test_default_messages_do_not_leak_internals(
    name: str, _status: int, cls_name: str
) -> None:
    """5.4:預設訊息不得含檔案路徑、堆疊或引擎原始輸出。"""
    message = getattr(e, cls_name).default_message
    for token in ("/", "\\", "Traceback", "File \"", ".py", "bestmove", "info depth"):
        assert token not in message, f"{cls_name} 的預設訊息疑似外洩內部細節:{token}"


def test_service_error_does_not_expose_cause_in_message() -> None:
    """包裝底層例外時,對外訊息不得帶入原始例外文字。"""
    try:
        try:
            raise OSError("/private/var/pikafish: broken pipe")
        except OSError as cause:
            raise e.EngineTimeoutError() from cause
    except e.EngineTimeoutError as err:
        assert "/private/var/pikafish" not in err.message
        assert "/private/var/pikafish" not in str(err)
        assert isinstance(err.__cause__, OSError)
