"""EnginePool:固定容量的引擎進程集合,服務的**唯一併發閘門**。

引擎進程是本服務唯一稀缺的資源:每個進程常駐一份 NNUE 與雜湊表,數量不能隨請求
成長。因此設計把併發上限放在池而不是 HTTP 的 threadpool —— threadpool 的容量與
真實資源無關,卡在那裡的請求既得不到引擎也得不到明確的答覆。池滿即「服務忙碌」,
這是 3.3 的判準,也是本模組唯一的併發語意來源。

    **借用一定有等待上限,絕不無限期等待。**

無限期等待的後果和 `process.py` 裡沒有上界的讀取相同:每個卡住的執行緒都不會自己
回來,累積下去服務就躺平了。逾時的借用一律拋出 `ServiceBusyError`(503),它是預期
中的暫時狀態而非故障。

## 為什麼是 Condition 而不是一個 Queue

`queue.Queue` 的 `get(timeout=...)` 就足以表達「帶上限的借用」,但**不足以表達池的
不變量**:可用數加借出數(2.5 之後再加重建中數)恆等於容量。Queue 只認識可用那一
側,借出那一側得另外用一把鎖記帳,兩份狀態就會有短暫不一致的窗口,不變量因此無法
被原子地觀察或斷言。改用單一 `threading.Condition` 守住全部狀態,每次轉移都是原子
的,2.5 要加入「重建中」時也只是在同一把鎖下多一個桶。

## 情境管理器

借用以情境管理器提供,而不是一對 `acquire()` / `release()`。理由是歸還不能依賴呼叫
端記得寫:正常返回、引擎拋錯、上層拋錯、逾時,每一條離開路徑都必須歸還,漏掉任何
一條都是永久少一格容量,而且症狀要等到池被耗盡才浮現。`finally` 把這件事變成語言
保證。取得情境管理器本身不佔容量,真正的借用發生在進入時。

## 尚未在此的部分

- **崩潰重建(4.2、4.3)屬 tasks 2.5**:歸還目前一律放回可用區,不看健康狀態。
  接點是 `_release` 這個唯一的歸還漏斗 —— 2.5 在此依 `is_healthy()` 分流到「重建中」,
  並在背景重建,借用路徑不受阻塞。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from service.engine.process import DEFAULT_STARTUP_TIMEOUT, EngineProcess
from service.errors import ServiceBusyError

__all__ = ["EnginePool"]


class EnginePool:
    """固定容量的引擎池。借出與歸還為原子操作,借用附等待上限。

    執行檔路徑由呼叫端注入,理由同 `EngineProcess`:池的失效路徑(握手失敗、進程
    崩潰)無法用真實引擎製造,路徑寫死等於那些路徑測不到。

    不變量:可用數 + 借出數 = 容量。同一進程不會同時借給兩個請求 —— 借出即從可用
    區移除,歸還前不會再次出現在那裡。
    """

    def __init__(
        self,
        size: int,
        acquire_timeout: float,
        engine_path: Path | str,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        """啟動 `size` 個引擎進程;任一個啟動失敗即收掉已啟動的並拋出原例外。

        參數在啟動任何進程**之前**驗證:不合法的容量若先付出啟動成本才被擋下,
        錯誤訊息會被進程層的失敗蓋掉。

        Raises:
            ValueError: `size` 小於 1,或 `acquire_timeout` 非正數。
        """
        if size < 1:
            raise ValueError(f"引擎池容量必須至少為 1,目前為 {size}")
        if acquire_timeout <= 0:
            raise ValueError(f"借用等待上限必須為正數,目前為 {acquire_timeout}")

        self._size = size
        self._acquire_timeout = float(acquire_timeout)
        self._engine_path = Path(engine_path)
        self._startup_timeout = startup_timeout

        #: 一把鎖守住 `_available`、`_borrowed` 與 `_closed`,使不變量恆成立。
        self._condition = threading.Condition()
        self._available: list[EngineProcess] = []
        self._borrowed: set[EngineProcess] = set()
        self._closed = False

        self._available = self._start_processes()

    # --- 借還 ------------------------------------------------------------

    def acquire(self) -> AbstractContextManager[EngineProcess]:
        """借用一個引擎,離開情境時必定歸還。

        真正的借用發生在進入情境時,因此取得情境管理器而不進入不會佔用容量。

        Raises:
            ServiceBusyError: 等待上限內沒有可用引擎,或池已關閉(3.3)。
        """
        return self._lend()

    @contextmanager
    def _lend(self) -> Iterator[EngineProcess]:
        process = self._borrow()
        try:
            yield process
        finally:
            # 正常返回、例外、上層的 break/return 全走這裡,無一條離開路徑會漏掉歸還。
            self._release(process)

    def _borrow(self) -> EngineProcess:
        """取出一個可用引擎並記為借出。等待最多 `acquire_timeout` 秒。

        `deadline` 在進入時算好,因此即使被喚醒多次(其他借用者搶先取走、或虛假
        喚醒),總等待時間仍不超過上限 —— 每次重算剩餘時間而非重新計時。
        """
        deadline = time.monotonic() + self._acquire_timeout
        with self._condition:
            while not self._available:
                if self._closed:
                    # 關閉後不再有引擎會回到可用區,等下去只是白白耗掉上限。
                    raise ServiceBusyError()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ServiceBusyError()
                self._condition.wait(remaining)
            process = self._available.pop()
            self._borrowed.add(process)
            return process

    def _release(self, process: EngineProcess) -> None:
        """歸還的唯一漏斗。2.5 會在此依健康狀態分流到「重建中」。

        池已關閉時不放回可用區而是直接收掉:關閉後不會再有借用者,放回去就成了
        沒人收的孤兒進程。終止動作移到鎖外進行,不讓它擋住其他執行緒的借還。
        """
        discard = False
        with self._condition:
            self._borrowed.discard(process)
            if self._closed:
                discard = True
            else:
                self._available.append(process)
                self._condition.notify()
        if discard:
            process.terminate()

    # --- 生命週期 --------------------------------------------------------

    def shutdown(self) -> None:
        """關閉池並終止所有引擎。可重複呼叫。

        關閉後借用一律立刻回報服務忙碌(503),不再等待。仍在借出中的進程不強行
        中斷,而是在歸還時收掉 —— 中斷正在服務的請求換不到任何好處,它們最多再持有
        一個搜尋逾時的時間。
        """
        with self._condition:
            if self._closed:
                return
            self._closed = True
            idle = self._available
            self._available = []
            self._condition.notify_all()  # 喚醒所有等待者,讓它們立刻拿到忙碌錯誤
        for process in idle:
            process.terminate()

    # --- 觀察 ------------------------------------------------------------

    @property
    def size(self) -> int:
        """池容量。"""
        return self._size

    @property
    def available_count(self) -> int:
        """目前可借出的引擎數。"""
        with self._condition:
            return len(self._available)

    @property
    def borrowed_count(self) -> int:
        """目前借出中的引擎數。"""
        with self._condition:
            return len(self._borrowed)

    # --- 進程的建立 ------------------------------------------------------

    def _start_processes(self) -> list[EngineProcess]:
        """啟動整池進程。中途失敗即收掉已啟動的,不留孤兒。

        每個進程常駐一份 NNUE,建構失敗時若不收拾,失敗的啟動會在機器上留下數百
        MB 的殘留,而呼叫端從例外看不出這件事。
        """
        started: list[EngineProcess] = []
        try:
            for _ in range(self._size):
                started.append(self._create_process())
        except BaseException:
            for process in started:
                process.terminate()
            raise
        return started

    def _create_process(self) -> EngineProcess:
        """建立單一引擎進程。池中所有進程的唯一產生點,2.5 的重建同樣經由此處。"""
        return EngineProcess(self._engine_path, startup_timeout=self._startup_timeout)
