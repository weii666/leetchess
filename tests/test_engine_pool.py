"""EnginePool 的測試(對應 tasks 2.4、requirements 3.2、3.3)。

本任務的核心價值是**併發閘門**:池容量是服務唯一的併發上限,借用附等待上限,
逾時即回報服務忙碌而非無限期等待。因此併發相關的斷言必須用真正的並行執行緒證明,
不能寫成循序借還 —— 循序借還在「池把每個請求排隊處理」的錯誤實作下也會通過。

兩道機制使測試在實作退化時**失敗而非把整個測試套件掛住**:

- `_run_bounded` 在背景執行緒呼叫並附等待上限(沿用 `test_engine_process.py` 的作法)
- `_borrow_concurrently` 的兩道 `threading.Barrier` 都帶 timeout

`_borrow_concurrently` 用兩道柵欄而非一道:第一道證明「所有借用者同時持有」——
若池把借用排隊,後到者會在等待上限內拿到 `ServiceBusyError` 而永遠湊不齊人數;
第二道讓主執行緒能在「全部持有」這個穩定狀態下檢查池的計數,不與歸還競賽。

一律使用替身引擎:真實引擎每個進程要載入 51MB NNUE,池初始化 N 個進程的成本會
讓本檔的每個測試都變慢,而池的借還語意與引擎實際算什麼無關。
"""

from __future__ import annotations

import pathlib
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from service.engine.pool import EnginePool
from service.engine.process import EngineProcess
from service.errors import ServiceBusyError

#: 《適情雅趣》第 21 局起始局面(紅先),用於確認借到的引擎確實可用。
PUZZLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 替身的合法著法(`fake_engine.py` 的 `PERFT_LINES`)。
FAKE_LEGAL_MOVES = ["e8f9", "e9f9"]

#: 單次查詢的逾時。替身回應是即時的,取小值使測試快。
FAKE_TIMEOUT = 0.5

#: 借用等待上限。取小值使「池滿即逾時」的測試快,仍遠大於執行緒排程的抖動。
ACQUIRE_TIMEOUT = 0.3

#: 允許實作比等待上限晚返回的餘裕(秒)。涵蓋執行緒排程與慢機。
ACQUIRE_SLACK = 1.5

#: 用於「借用應該要等」的測試:上限拉長,使「等待中」與「立刻失敗」可區分。
SLOW_ACQUIRE_TIMEOUT = 3.0

#: 等待「應該要返回」的呼叫返回的上限。遠大於上面所有逾時,避免慢機誤判。
BOUNDED_WAIT = 8.0


# --- 不會掛住測試套件的呼叫輔助 -----------------------------------------


@dataclass
class _Outcome:
    """一次有時限的呼叫結果。`finished` 為假代表呼叫仍卡在那裡。"""

    finished: bool = False
    value: Any = None
    error: BaseException | None = None
    elapsed: float = field(default=0.0)


def _run_bounded(call: Callable[[], Any], wait: float = BOUNDED_WAIT) -> _Outcome:
    """在背景執行緒呼叫 `call`,最多等 `wait` 秒。

    借用自己就該有等待上限;此處的上限只是**安全網**,使實作退化成無限期等待時
    測試失敗而非讓 pytest 永遠停在那一行。
    """
    outcome = _Outcome()
    started = time.monotonic()

    def target() -> None:
        try:
            outcome.value = call()
        except BaseException as exc:  # noqa: BLE001 - 測試要看到任何例外
            outcome.error = exc
        finally:
            outcome.elapsed = time.monotonic() - started

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(wait)
    outcome.finished = not worker.is_alive()
    if not outcome.finished:
        outcome.elapsed = time.monotonic() - started
    return outcome


def _borrow_once(pool: EnginePool) -> EngineProcess:
    """借一次隨即歸還,回傳借到的引擎。借不到時例外由呼叫端觀察。"""
    with pool.acquire() as engine:
        return engine


def _borrow_concurrently(
    pool: EnginePool, count: int, inspect: Callable[[], None] | None = None
) -> tuple[list[EngineProcess], list[BaseException]]:
    """`count` 條執行緒同時借用,**全部借到之後**才各自歸還。

    回傳(持有過的引擎, 錯誤)。`inspect` 於「全部同時持有」這個穩定狀態下由主
    執行緒呼叫,適合檢查池的計數。

    柵欄都帶 timeout:池若把借用排隊,湊不齊人數的那一刻測試會失敗,不會掛住。
    """
    all_held = threading.Barrier(count + 1, timeout=BOUNDED_WAIT)
    may_release = threading.Barrier(count + 1, timeout=BOUNDED_WAIT)
    held: list[EngineProcess] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with pool.acquire() as engine:
                with lock:
                    held.append(engine)
                all_held.wait()
                may_release.wait()
        except BaseException as exc:  # noqa: BLE001 - 測試要看到任何例外
            with lock:
                errors.append(exc)
            all_held.abort()
            may_release.abort()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()

    failure: BaseException | None = None
    try:
        all_held.wait()
    except threading.BrokenBarrierError as exc:
        failure = exc
    else:
        if inspect is not None:
            try:
                inspect()
            except BaseException as exc:  # noqa: BLE001 - 交回給呼叫端的測試
                failure = exc
    finally:
        try:
            may_release.wait()
        except threading.BrokenBarrierError:
            pass

    for thread in threads:
        thread.join(BOUNDED_WAIT)
        if thread.is_alive():
            errors.append(RuntimeError("借用執行緒未在上限內結束"))

    # 柵欄破裂只是症狀,真正的原因在 errors 裡;其餘失敗(例如 inspect 的斷言)直接拋出。
    if failure is not None and not isinstance(failure, threading.BrokenBarrierError):
        raise failure
    return held, errors


# --- 夾具 ---------------------------------------------------------------


@pytest.fixture
def make_pool(fake_engine):
    """回傳一個工廠:`make_pool(size=..., acquire_timeout=...)` 給出一個替身引擎池。

    測試結束一律關閉,不讓替身進程殘留到下一個測試。
    """
    pools: list[EnginePool] = []

    def make(
        size: int = 2,
        acquire_timeout: float = ACQUIRE_TIMEOUT,
        mode: str = "normal",
    ) -> EnginePool:
        engine = fake_engine(mode)
        pool = EnginePool(
            size=size, acquire_timeout=acquire_timeout, engine_path=engine.path
        )
        pools.append(pool)
        return pool

    yield make

    for pool in pools:
        pool.shutdown()


# --- 池的初始化 ---------------------------------------------------------


def test_pool_starts_the_configured_number_of_engines(make_pool) -> None:
    pool = make_pool(size=3)
    assert pool.size == 3
    assert pool.available_count == 3
    assert pool.borrowed_count == 0


@pytest.mark.parametrize("size", [0, -1])
def test_size_must_be_at_least_one(size: int, tmp_path: pathlib.Path) -> None:
    """容量不合法時在啟動任何進程之前就拒絕 —— 故意給不存在的路徑,實作若先啟動
    進程會得到 `FileNotFoundError` 而非 `ValueError`,測試就會失敗。"""
    with pytest.raises(ValueError):
        EnginePool(
            size=size, acquire_timeout=ACQUIRE_TIMEOUT, engine_path=tmp_path / "nope"
        )


@pytest.mark.parametrize("acquire_timeout", [0.0, -1.0])
def test_acquire_timeout_must_be_positive(
    acquire_timeout: float, tmp_path: pathlib.Path
) -> None:
    with pytest.raises(ValueError):
        EnginePool(size=1, acquire_timeout=acquire_timeout, engine_path=tmp_path / "no")


# --- 借出與歸還 ---------------------------------------------------------


def test_borrowed_engine_answers_queries(make_pool) -> None:
    pool = make_pool(size=1)
    with pool.acquire() as engine:
        moves = engine.legal_moves(PUZZLE_FEN, [], FAKE_TIMEOUT)
    assert moves == FAKE_LEGAL_MOVES


def test_borrow_and_return_keep_the_pool_invariant(make_pool) -> None:
    """可用數 + 借出數恆等於池容量(2.5 之後才會有「重建中」狀態)。"""
    pool = make_pool(size=2)
    assert (pool.available_count, pool.borrowed_count) == (2, 0)
    with pool.acquire():
        assert (pool.available_count, pool.borrowed_count) == (1, 1)
    assert (pool.available_count, pool.borrowed_count) == (2, 0)


def test_engine_returns_to_the_pool_when_the_body_raises(make_pool) -> None:
    """情境管理器的價值就在這裡:例外路徑同樣必定歸還,池不會因此洩漏容量。"""
    pool = make_pool(size=1)
    borrowed = None
    with pytest.raises(RuntimeError):
        with pool.acquire() as engine:
            borrowed = engine
            raise RuntimeError("借用期間的失敗")
    assert (pool.available_count, pool.borrowed_count) == (1, 0)
    # 容量真的回來了:同一個引擎能再借出去。
    assert _borrow_once(pool) is borrowed


def test_a_borrow_that_is_never_entered_does_not_take_capacity(make_pool) -> None:
    """`acquire()` 只是取得情境管理器,未進入就不佔用容量。"""
    pool = make_pool(size=1)
    pool.acquire()
    assert (pool.available_count, pool.borrowed_count) == (1, 0)


# --- 併發閘門 -----------------------------------------------------------


def test_borrows_within_capacity_do_not_wait_for_each_other(make_pool) -> None:
    """3.2:未達併發上限的請求立即開始處理,不等待其他請求完成。

    三條執行緒必須**同時**持有引擎才能通過第一道柵欄;任何形式的排隊都會讓後到者
    在等待上限內拿到忙碌錯誤,人數永遠湊不齊。
    """
    size = 3
    pool = make_pool(size=size)

    def all_engines_are_out() -> None:
        assert pool.available_count == 0
        assert pool.borrowed_count == size

    held, errors = _borrow_concurrently(pool, size, inspect=all_engines_are_out)
    assert errors == [], "池容量內的並行借用不應失敗或彼此等待"
    assert len(held) == size
    assert (pool.available_count, pool.borrowed_count) == (size, 0)


def test_concurrent_borrowers_never_share_a_process(make_pool) -> None:
    """同一進程不會同時借給兩個請求 —— 同時持有的實例必須兩兩相異。"""
    size = 3
    pool = make_pool(size=size)
    held, errors = _borrow_concurrently(pool, size)
    assert errors == []
    assert len({id(engine) for engine in held}) == size


def test_borrow_beyond_capacity_reports_service_busy_within_the_wait_bound(
    make_pool,
) -> None:
    """3.3:池滿時在等待上限內拋出服務忙碌錯誤,絕不無限期阻塞。"""
    pool = make_pool(size=1, acquire_timeout=ACQUIRE_TIMEOUT)
    with pool.acquire():
        assert (pool.available_count, pool.borrowed_count) == (0, 1)
        outcome = _run_bounded(lambda: _borrow_once(pool))
    assert outcome.finished, "借用未返回,實作可能無限期等待"
    assert isinstance(outcome.error, ServiceBusyError), outcome.error
    # 下界證明它真的等過(不是立刻放棄),上界證明它沒有等超過設定的上限。
    assert outcome.elapsed >= ACQUIRE_TIMEOUT * 0.8, outcome.elapsed
    assert outcome.elapsed < ACQUIRE_TIMEOUT + ACQUIRE_SLACK, outcome.elapsed
    # 逾時的借用不得吃掉容量。
    assert (pool.available_count, pool.borrowed_count) == (1, 0)


def test_a_waiting_borrower_is_served_as_soon_as_an_engine_returns(make_pool) -> None:
    """等待上限之內出現可用引擎就借得到 —— 借用是「等待」而不是「池滿即失敗」。

    「借得到」本身不足以證明「歸還即被喚醒」:等待者的等待上限一到就會自己醒來,
    重新檢查時同樣看得到已歸還的引擎而成功。要區分這兩者只能量**歸還到取得之間的
    延遲** —— 靠喚醒是毫秒級,靠自己逾時醒來則要付滿整個等待上限。因此本測試記下
    歸還的時刻與等待者取得的時刻,並要求兩者的間隔遠小於等待上限。
    """
    pool = make_pool(size=1, acquire_timeout=SLOW_ACQUIRE_TIMEOUT)
    result: dict[str, Any] = {}

    def waiter() -> None:
        try:
            with pool.acquire() as engine:
                result["engine"] = engine
        except BaseException as exc:  # noqa: BLE001 - 測試要看到任何例外
            result["error"] = exc
        finally:
            result["done"] = time.monotonic()

    thread = threading.Thread(target=waiter, daemon=True)
    with pool.acquire() as held:
        thread.start()
        time.sleep(0.2)  # 讓等待者確實進入等待
        assert thread.is_alive(), "池滿時借用應等待,而非立刻失敗"
        assert result == {}
    released = time.monotonic()  # 必須緊接著情境結束,才是歸還發生的時刻
    thread.join(BOUNDED_WAIT)
    assert not thread.is_alive(), "歸還之後等待者仍未被喚醒"
    assert result.get("error") is None, result.get("error")
    assert result.get("engine") is held
    # 上界遠小於等待上限:若歸還沒有喚醒等待者,這裡會量到整整一個等待上限。
    assert result["done"] - released < SLOW_ACQUIRE_TIMEOUT / 2, (
        result["done"] - released
    )


# --- 關閉 ---------------------------------------------------------------


def test_shutdown_terminates_every_engine(make_pool) -> None:
    pool = make_pool(size=2)
    held, errors = _borrow_concurrently(pool, 2)
    assert errors == []
    pool.shutdown()
    assert pool.available_count == 0
    assert [engine.is_healthy() for engine in held] == [False, False]


def test_shutdown_is_idempotent(make_pool) -> None:
    pool = make_pool(size=1)
    pool.shutdown()
    pool.shutdown()
    assert (pool.available_count, pool.borrowed_count) == (0, 0)


def test_engine_borrowed_across_shutdown_is_not_returned_to_the_pool(make_pool) -> None:
    """關閉期間借出的進程在歸還時收掉,既不回到池裡也不變成孤兒。"""
    pool = make_pool(size=1)
    with pool.acquire() as engine:
        pool.shutdown()
        assert pool.borrowed_count == 1
    assert (pool.available_count, pool.borrowed_count) == (0, 0)
    assert not engine.is_healthy()


def test_borrow_after_shutdown_reports_service_busy_without_waiting(make_pool) -> None:
    pool = make_pool(size=1, acquire_timeout=SLOW_ACQUIRE_TIMEOUT)
    pool.shutdown()
    outcome = _run_bounded(lambda: _borrow_once(pool))
    assert outcome.finished
    assert isinstance(outcome.error, ServiceBusyError), outcome.error
    assert outcome.elapsed < SLOW_ACQUIRE_TIMEOUT / 2, outcome.elapsed


def test_shutdown_wakes_a_borrower_already_blocked_in_the_wait(make_pool) -> None:
    """關閉時已經停在等待中的借用者必須立刻收到忙碌錯誤,而不是耗完整個等待上限。

    這與上一個測試是**不同的路徑**:上面是關閉之後才來借,借用者一進去就看到已關閉;
    這裡的借用者是在關閉**之前**就睡進 `wait()` 的,`_closed` 在它睡著之後才被設起,
    唯有關閉時主動喚醒才能讓它重新檢查。沒有喚醒,它要等到自己的等待上限才醒來 ——
    而關閉後池永遠不會再有引擎回來,那段等待純屬白等。

    借用者跑在兩層執行緒裡:內層由 `_run_bounded` 附上安全網(退化成無限期等待時
    測試失敗而非掛住),外層讓主執行緒在借用者阻塞期間仍能檢查它的存活並呼叫關閉。
    """
    pool = make_pool(size=1, acquire_timeout=SLOW_ACQUIRE_TIMEOUT)
    outcomes: list[_Outcome] = []

    def bounded_borrow() -> None:
        outcomes.append(_run_bounded(lambda: _borrow_once(pool)))

    borrower = threading.Thread(target=bounded_borrow, daemon=True)
    with pool.acquire():
        borrower.start()
        time.sleep(0.2)  # 讓借用者確實睡進 wait(),而不是還沒進到借用就被關閉擋下
        assert borrower.is_alive(), "池滿時借用應等待,而非立刻失敗"
        # 必須在仍持有引擎時關閉:先離開情境的話引擎會回到池裡,借用者就借得到了,
        # 這條「關閉喚醒等待者」的路徑也就走不到。
        pool.shutdown()
    borrower.join(BOUNDED_WAIT)
    assert not borrower.is_alive(), "關閉之後等待者仍未被喚醒"
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.finished, "借用未返回,關閉未喚醒等待中的借用者"
    assert isinstance(outcome.error, ServiceBusyError), outcome.error
    assert outcome.elapsed < SLOW_ACQUIRE_TIMEOUT / 2, outcome.elapsed


# --- 初始化失敗 ---------------------------------------------------------

#: 一個會「第一次正常、之後永遠沉默」的替身:用於證明池初始化中途失敗時,
#: 已經啟動的進程會被收掉,而不是留成孤兒。conftest 的替身每次啟動行為相同,
#: 做不到這件事,故此處另寫一個最小的。
FLAKY_ENGINE_SOURCE = '''
import os
import sys

counter = os.environ["FLAKY_COUNTER"]
log = os.environ["FLAKY_LOG"]
try:
    with open(counter, encoding="utf-8") as handle:
        n = int(handle.read())
except (OSError, ValueError):
    n = 0
n += 1
with open(counter, "w", encoding="utf-8") as handle:
    handle.write(str(n))

first = n == 1
while True:
    raw = sys.stdin.readline()
    if raw == "":
        break
    command = raw.strip()
    if not command or not first:
        continue  # 第二個之後的進程永遠沉默,握手必定逾時
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(command + "\\n")
    if command == "quit":
        break
    if command == "uci":
        sys.stdout.write("uciok\\n")
    elif command == "isready":
        sys.stdout.write("readyok\\n")
    sys.stdout.flush()
'''


def _flaky_engine(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """建立上述替身,回傳(執行檔, 指令記錄檔)。"""
    script = tmp_path / "flaky_engine.py"
    script.write_text(FLAKY_ENGINE_SOURCE, encoding="utf-8")
    counter = tmp_path / "flaky_engine.count"
    log = tmp_path / "flaky_engine.log"
    executable = tmp_path / "flaky_engine"
    executable.write_text(
        "#!/bin/sh\n"
        f'FLAKY_COUNTER="{counter}"\n'
        f'FLAKY_LOG="{log}"\n'
        "export FLAKY_COUNTER FLAKY_LOG\n"
        f'exec "{sys.executable}" "{script}"\n',
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return executable, log


def test_failed_initialization_terminates_the_engines_already_started(
    tmp_path: pathlib.Path,
) -> None:
    """第二個進程握手逾時 → 建構失敗,且第一個進程必須已被收掉。

    收掉的證據是它收到了 `quit`(`EngineProcess.terminate()` 的第一步)。
    """
    executable, log = _flaky_engine(tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - 只在意「失敗被傳出去」
        EnginePool(
            size=2,
            acquire_timeout=ACQUIRE_TIMEOUT,
            engine_path=executable,
            startup_timeout=0.5,
        )
    commands = log.read_text(encoding="utf-8").split()
    assert "quit" in commands, f"已啟動的進程未被收掉,實際收到的指令:{commands}"
