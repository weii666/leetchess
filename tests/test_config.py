"""設定模組與時間預算約束的測試(對應 tasks 1.3、requirements 3.3 與 4.1)。

design 的 Architecture「請求的時間預算」定下頂層約束:

    總時間預算  =  借用等待上限  +  搜尋逾時  +  stop 寬限期

三項分項之和不得超過總預算。此約束是**啟動時的硬性閘門**,不是警告 ——
設定矛盾時服務必須拒絕啟動,否則驗收時無法判定服務是否合格。

設定錯誤發生於啟動期、不經過 HTTP,因此以內建的 `ValueError` 表達,
**不使用** `errors.py` 的七種錯誤類別(那七種是對外 HTTP 契約)。
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from service import config as c


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _env(**overrides: str) -> dict[str, str]:
    """僅含指定覆寫項的環境;未列出的設定項一律走預設值。"""
    return dict(overrides)


# --- 設定項齊全與預設值 -------------------------------------------------


def test_settings_exposes_every_required_field() -> None:
    """tasks 1.3 列出的七個設定項,加上 2.1 納入的引擎執行檔路徑。"""
    field_names = {f.name for f in dataclasses.fields(c.Settings)}
    assert field_names == {
        "pool_size",
        "acquire_timeout",
        "search_timeout",
        "stop_grace_period",
        "total_time_budget",
        "search_nodes",
        "positions_dir",
        "engine_path",
    }


def test_default_settings_load_and_satisfy_the_time_budget() -> None:
    settings = c.load_settings(_env())
    assert settings.timeout_sum <= settings.total_time_budget


def test_positions_dir_defaults_to_the_project_corpus_directory() -> None:
    assert c.load_settings(_env()).positions_dir == PROJECT_ROOT / "positions"


def test_engine_path_defaults_to_the_locked_engine_binary() -> None:
    """預設指向 engine/fetch.sh 取回的執行檔;可覆寫是測試放入替身的前提(2.1、5.5)。"""
    assert c.load_settings(_env()).engine_path == PROJECT_ROOT / "engine" / "pikafish"


def test_pool_size_default_is_provisional_and_overridable() -> None:
    """池容量預設值待 2.3 實測填入;此任務只保證機制可覆寫。"""
    assert c.load_settings(_env()).pool_size == c.DEFAULT_POOL_SIZE
    assert c.load_settings(_env(LEETCHESS_POOL_SIZE="7")).pool_size == 7


# --- 時間預算:啟動時的硬性閘門 -----------------------------------------


def test_contradictory_timeouts_are_rejected_at_startup() -> None:
    """三項分項之和超過總預算即拒絕啟動,並指出總預算被超出。"""
    with pytest.raises(ValueError) as excinfo:
        c.load_settings(
            _env(
                LEETCHESS_ACQUIRE_TIMEOUT="5",
                LEETCHESS_SEARCH_TIMEOUT="5",
                LEETCHESS_STOP_GRACE_PERIOD="5",
                LEETCHESS_TOTAL_TIME_BUDGET="10",
            )
        )
    message = str(excinfo.value)
    assert "總時間預算" in message
    # 訊息須帶出實際的和與預算,否則操作者無從得知該調哪一項
    assert "15" in message
    assert "10" in message
    # 三個分項都要列出,才知道預算被誰吃掉
    for name in ("借用等待上限", "搜尋逾時", "stop 寬限期"):
        assert name in message


def test_sum_exactly_equal_to_the_budget_is_accepted() -> None:
    """約束為「不得超過」,相等即合格。"""
    settings = c.load_settings(
        _env(
            LEETCHESS_ACQUIRE_TIMEOUT="2",
            LEETCHESS_SEARCH_TIMEOUT="5",
            LEETCHESS_STOP_GRACE_PERIOD="1",
            LEETCHESS_TOTAL_TIME_BUDGET="8",
        )
    )
    assert settings.timeout_sum == pytest.approx(8.0)


def test_the_budget_gate_also_guards_direct_construction() -> None:
    """閘門在 Settings 本身,繞過 load_settings 直接建構同樣被擋。"""
    with pytest.raises(ValueError, match="總時間預算"):
        c.Settings(
            pool_size=1,
            acquire_timeout=1.0,
            search_timeout=1.0,
            stop_grace_period=1.0,
            total_time_budget=2.0,
            search_nodes=250_000,
            positions_dir=PROJECT_ROOT / "positions",
            engine_path=PROJECT_ROOT / "engine" / "pikafish",
        )


# --- 取值的基本合法性 ---------------------------------------------------


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("LEETCHESS_POOL_SIZE", "-1"),
        ("LEETCHESS_ACQUIRE_TIMEOUT", "0"),
        ("LEETCHESS_SEARCH_TIMEOUT", "-1"),
        ("LEETCHESS_STOP_GRACE_PERIOD", "0"),
    ],
)
def test_non_positive_values_are_rejected(var: str, value: str) -> None:
    """負值或零會讓總預算約束形同虛設,須在同一道閘門擋下。"""
    with pytest.raises(ValueError, match=var):
        c.load_settings(_env(**{var: value}))


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("LEETCHESS_POOL_SIZE", "many"),
        ("LEETCHESS_SEARCH_TIMEOUT", "5s"),
        ("LEETCHESS_SEARCH_NODES", "2e5"),
    ],
)
def test_unparsable_values_name_the_offending_variable(var: str, value: str) -> None:
    with pytest.raises(ValueError, match=var):
        c.load_settings(_env(**{var: value}))


# --- 每一個設定項都可由環境覆寫 -----------------------------------------


def test_every_setting_is_overridable_from_the_environment(tmp_path: pathlib.Path) -> None:
    settings = c.load_settings(
        _env(
            LEETCHESS_POOL_SIZE="3",
            LEETCHESS_ACQUIRE_TIMEOUT="1.5",
            LEETCHESS_SEARCH_TIMEOUT="4",
            LEETCHESS_STOP_GRACE_PERIOD="0.5",
            LEETCHESS_TOTAL_TIME_BUDGET="12",
            LEETCHESS_SEARCH_NODES="500000",
            LEETCHESS_POSITIONS_DIR=str(tmp_path),
            LEETCHESS_ENGINE_PATH=str(tmp_path / "fake-engine"),
        )
    )
    assert settings.pool_size == 3
    assert settings.acquire_timeout == pytest.approx(1.5)
    assert settings.search_timeout == pytest.approx(4.0)
    assert settings.stop_grace_period == pytest.approx(0.5)
    assert settings.total_time_budget == pytest.approx(12.0)
    assert settings.search_nodes == 500_000
    assert settings.positions_dir == tmp_path
    assert settings.engine_path == tmp_path / "fake-engine"


def test_load_settings_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEETCHESS_POOL_SIZE", "5")
    assert c.load_settings().pool_size == 5
