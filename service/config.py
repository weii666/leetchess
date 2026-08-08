"""服務設定:池容量、各項逾時與總時間預算、搜尋節點數、題庫路徑。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組只能向左依賴 `types.py` 與 `errors.py`,**不得** import 更右邊的模組。

## 時間預算

design 的 Architecture「請求的時間預算」以**單一請求的總時間預算**為頂層約束,
三項分項為其子預算:

    總時間預算  =  借用等待上限  +  搜尋逾時  +  stop 寬限期

| 分項         | 約束對象                    | 逾時後的行為              | Req      |
|--------------|-----------------------------|---------------------------|----------|
| 借用等待上限 | 等待池中出現可用引擎        | 回報服務忙碌              | 3.3      |
| 搜尋逾時     | 單次 go nodes 或 go perft   | 送出 stop,進入寬限期     | 4.1      |
| stop 寬限期  | 等待 stop 後的 bestmove     | kill 進程並排入重建       | 4.1, 4.2 |

三項之和不得超過總預算。配置時以總預算為準向下分配,而非各自獨立設定 ——
否則驗收時無法判定服務是否合格。序列驗證的 `d` 指令沿用搜尋逾時,不另設分項。

**此約束是啟動時的硬性閘門而非警告**:設定矛盾即拒絕啟動,使錯誤在部署階段暴露。

## 例外選擇

設定錯誤發生於啟動期,不經過 HTTP,因此一律以內建的 `ValueError` 表達。
`errors.py` 的七種錯誤類別是**對外 HTTP 契約**,不得為啟動失敗硬塞一個進去。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ENV_PREFIX",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_ACQUIRE_TIMEOUT",
    "DEFAULT_SEARCH_TIMEOUT",
    "DEFAULT_STOP_GRACE_PERIOD",
    "DEFAULT_TOTAL_TIME_BUDGET",
    "DEFAULT_SEARCH_NODES",
    "DEFAULT_POSITIONS_DIR",
    "DEFAULT_ENGINE_PATH",
    "Settings",
    "load_settings",
]

#: 專案根。`config.py` 位於 `service/` 之下,故上溯兩層。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 所有設定項的環境變數前綴。
ENV_PREFIX = "LEETCHESS_"

# --- 預設值 -------------------------------------------------------------

#: 引擎池容量。
#:
#: **這是暫定值,尚無實測依據。** 任務 2.3 會量測單進程與多進程並存時的常駐
#: 記憶體(design 以每進程 128MB 雜湊表加 51MB NNUE 估算,該假設未經驗證),
#: 再以實測結果取代此處的數字。在那之前不要引用它作為容量規劃的依據。
#: 2.3 只需改動這一行,以及 design「記憶體上界」段落的估算。
DEFAULT_POOL_SIZE = 4

#: 借用等待上限(秒):等待池中出現可用引擎,逾時回報服務忙碌(3.3)。
DEFAULT_ACQUIRE_TIMEOUT = 2.0

#: 搜尋逾時(秒):單次 `go nodes` 或 `go perft` 的上界(4.1)。
#: POC 實測 200k 節點約 0.2 秒,此值為異常偵測用的寬鬆上界,非預期耗時。
DEFAULT_SEARCH_TIMEOUT = 5.0

#: stop 寬限期(秒):送出 `stop` 後等待 `bestmove` 的上界,逾期即 kill 並重建。
#: design 已註記「`stop` 後引擎回傳 `bestmove` 的延遲缺乏實測依據」,故可配置。
DEFAULT_STOP_GRACE_PERIOD = 1.0

#: 單一請求的總時間預算(秒)。三項分項之和不得超過它。
DEFAULT_TOTAL_TIME_BUDGET = 8.0

#: 黑方應手的搜尋節點數。
#:
#: POC 註記的「200k 與 2M 得同一手」對**著法**成立(實測各節點數下 bestmove 完全相同),
#: 但對 **mate 分數不成立** —— 200k 在殺勢局面回 `cp`,三態信號會落在「未知」。
#:
#: 實測(《適情雅趣》第 21 局,全新進程即冷雜湊表):
#:
#:   起始局面紅方視角      200k → mate 17(高估)  250k → mate 16(正確)
#:   走 f8f9 後黑方視角    200k → cp 526(未搜到) 230k → mate -16  1M → mate -15(精確)
#:
#: 取 250k:0.12 秒即可穩定取得 mate **型別**,三態信號因此可用。
#: **已知限制:殺著倒數(DTM)可能高估 1 步**,取得精確 DTM 需 1M(0.55 秒,4.5 倍成本)。
#: 這是刻意的取捨 —— 倒數是諮詢性質,4.5 倍時間直接換算為 4.5 倍伺服器成本。
#: UI 呈現殺著倒數時宜表述為「約 N 步」。
DEFAULT_SEARCH_NODES = 250_000

#: 題庫根目錄(唯讀)。啟動時遞迴掃描以建立 id 索引。
DEFAULT_POSITIONS_DIR = PROJECT_ROOT / "positions"

#: 引擎執行檔。預設為 `engine/fetch.sh` 依 `ENGINE_VERSION` 取回的 binary。
#: **可覆寫是刻意的**:逾時與崩潰路徑無法用真實引擎製造(它不會應要求永久沉默),
#: 測試必須能放入替身進程。路徑寫死等於那些路徑測不到(2.1、5.5)。
DEFAULT_ENGINE_PATH = PROJECT_ROOT / "engine" / "pikafish"


# --- 設定 ---------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """服務設定。建構即驗證,不合格的組合無法產生實例。

    驗證放在 `__post_init__` 而非 `load_settings`,使任何取得 `Settings` 的路徑
    (含測試與程式內直接建構)都經過同一道閘門。
    """

    pool_size: int
    acquire_timeout: float
    search_timeout: float
    stop_grace_period: float
    total_time_budget: float
    search_nodes: int
    positions_dir: Path
    engine_path: Path

    def __post_init__(self) -> None:
        self._check_positive()
        self._check_time_budget()

    @property
    def timeout_sum(self) -> float:
        """三項分項逾時之和,即單一請求的最壞情況耗時。"""
        return self.acquire_timeout + self.search_timeout + self.stop_grace_period

    def _check_positive(self) -> None:
        """非正值會讓總預算約束形同虛設(例如以負的寬限期換取更長的搜尋逾時)。"""
        for name in (
            "pool_size",
            "acquire_timeout",
            "search_timeout",
            "stop_grace_period",
            "total_time_budget",
            "search_nodes",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(
                    f"設定 {_env_var(name)} 必須為正數,目前為 {value}"
                )

    def _check_time_budget(self) -> None:
        """三項分項之和不得超過總時間預算(design:請求的時間預算)。"""
        if self.timeout_sum <= self.total_time_budget:
            return
        raise ValueError(
            "設定的三項分項逾時之和超出總時間預算,服務拒絕啟動:"
            f"借用等待上限 {self.acquire_timeout} 秒"
            f" + 搜尋逾時 {self.search_timeout} 秒"
            f" + stop 寬限期 {self.stop_grace_period} 秒"
            f" = {_trim(self.timeout_sum)} 秒,"
            f"已超過總時間預算 {_trim(self.total_time_budget)} 秒。"
            "請以總預算為準向下分配,或提高 "
            f"{_env_var('total_time_budget')}。"
        )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """由環境變數載入設定;未設定的項目走預設值。

    任一設定項不可解析、非正值,或三項分項逾時之和超過總預算時,拋出 `ValueError`
    使服務拒絕啟動。呼叫端(`main.py` 的啟動掛鉤)不攔截此例外。
    """
    source = os.environ if env is None else env
    return Settings(
        pool_size=_read_int(source, "pool_size", DEFAULT_POOL_SIZE),
        acquire_timeout=_read_float(source, "acquire_timeout", DEFAULT_ACQUIRE_TIMEOUT),
        search_timeout=_read_float(source, "search_timeout", DEFAULT_SEARCH_TIMEOUT),
        stop_grace_period=_read_float(
            source, "stop_grace_period", DEFAULT_STOP_GRACE_PERIOD
        ),
        total_time_budget=_read_float(
            source, "total_time_budget", DEFAULT_TOTAL_TIME_BUDGET
        ),
        search_nodes=_read_int(source, "search_nodes", DEFAULT_SEARCH_NODES),
        positions_dir=_read_path(source, "positions_dir", DEFAULT_POSITIONS_DIR),
        engine_path=_read_path(source, "engine_path", DEFAULT_ENGINE_PATH),
    )


# --- 環境變數讀取 -------------------------------------------------------


def _env_var(field_name: str) -> str:
    return ENV_PREFIX + field_name.upper()


def _read_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(_env_var(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as cause:
        raise ValueError(
            f"設定 {_env_var(name)} 必須為整數,無法解析:{raw!r}"
        ) from cause


def _read_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(_env_var(name))
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as cause:
        raise ValueError(
            f"設定 {_env_var(name)} 必須為數字(秒),無法解析:{raw!r}"
        ) from cause


def _read_path(source: Mapping[str, str], name: str, default: Path) -> Path:
    raw = source.get(_env_var(name))
    return default if raw is None else Path(raw)


def _trim(value: float) -> str:
    """去掉整數值的小數尾巴,使訊息中的秒數易讀。"""
    return str(int(value)) if value == int(value) else str(value)
