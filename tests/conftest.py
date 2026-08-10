"""測試共用夾具。

`fake_engine` 產生一個可執行的引擎替身,交給 `EngineProcess` 的可注入路徑。
逾時與崩潰路徑無法用真實引擎製造,tasks 2.1 要求路徑可注入正是為了這件事;
tasks 5.5 的逾時與崩潰恢復測試會沿用同一組夾具。

替身本體是 `tests/fakes/fake_engine.py`,但 `EngineProcess` 啟動的是「一個執行檔」
而非「一條命令列」,因此此處再包一層 shell wrapper,由 wrapper 以本次 venv 的
直譯器執行替身,並帶上模式與指令記錄檔的環境變數。

前端測試的 Playwright 夾具放在 `tests/conftest_web.py`(檔名取自 design 的
File Structure Plan),在此匯入以完成註冊 —— pytest 只自動載入名為 `conftest.py`
的檔案。該模組不在頂層匯入 playwright,故此匯入不影響既有測試的啟動時間。

## 停用清單

`tests/disabled.txt` 列出目前停用的測試,一行一個 node id,由下方的
`pytest_collection_modifyitems` 在收集階段剔除。

停用而非刪除,是為了讓「要加回來」的成本等於刪掉清單裡的一行 —— 測試碼本身
原封不動留在版本庫裡,不必從 git 歷史挖回來。清單集中在單一檔案,也使「現在
到底停用了哪些」可以一眼看完,而不必去各測試檔翻 `@pytest.mark.skip`。

**一個測試要能進入這份清單,判準是它的停用不會讓任何一個變異體從被殺變成存活**
(見 `.kiro/steering/tech.md` 未涵蓋、屬測試策略的部分)。覆蓋率不是判準:實測
`service/game.py` 砍掉一半測試後覆蓋率仍是 100%,卻放掉了 `mate 0` 與 `mate 1`
兩個邊界。
"""

from __future__ import annotations

import pathlib
import shlex
import stat
import sys
from dataclasses import dataclass

import pytest

from tests.conftest_web import browser, browser_page  # noqa: F401

TESTS_DIR = pathlib.Path(__file__).resolve().parent
FAKES_DIR = TESTS_DIR / "fakes"
FAKE_ENGINE_SCRIPT = FAKES_DIR / "fake_engine.py"
DISABLED_LIST = TESTS_DIR / "disabled.txt"


def _disabled_node_ids() -> set[str]:
    """讀取停用清單。空行與 `#` 開頭的行忽略,檔案不存在即視為沒有停用任何測試。"""
    if not DISABLED_LIST.is_file():
        return set()
    lines = DISABLED_LIST.read_text(encoding="utf-8").splitlines()
    return {
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    }


def pytest_addoption(parser: pytest.Parser) -> None:
    """`--slow`:連同標記為 slow 的測試一起跑。

    `pyproject.toml` 的 addopts 預設帶著 `-m 'not slow'`,平時開發即跳過那些
    必須真的等滿逾時上界的測試。上線前與 CI 應加上此旗標跑完整套。
    """
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="連同標記為 slow 的測試一起跑(預設跳過)",
    )
    parser.addoption(
        "--disabled-report",
        action="store_true",
        default=False,
        help="列出 tests/disabled.txt 的命中數與失效條目",
    )


def pytest_configure(config: pytest.Config) -> None:
    # 清掉 addopts 寫死的 `-m 'not slow'`,而不是再疊一層 marker 運算式。
    if config.getoption("--slow"):
        config.option.markexpr = ""


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """把 `tests/disabled.txt` 列出的測試從本次收集結果中剔除。

    走 deselect 而非 skip,理由是停用的測試不該出現在輸出裡佔一個 `s` —— 數百個
    skip 標記會把真正因環境不足而跳過的測試(例如缺引擎 binary 的
    `requires_real_engine`)淹沒,那正是需要被看見的訊號。

    清單裡對不上任何 node id 的項目**不會**報錯:測試改名或參數調整之後,留著的
    陳舊條目只是失效,不至於讓整套測試無法啟動。代價是失效條目會靜靜累積,故
    以 `--disabled-report` 提供一次性的對帳。
    """
    disabled = _disabled_node_ids()
    if not disabled and not config.getoption("--disabled-report"):
        return

    kept: list[pytest.Item] = []
    removed: list[pytest.Item] = []
    for item in items:
        (removed if item.nodeid in disabled else kept).append(item)

    if removed:
        config.hook.pytest_deselected(items=removed)
        items[:] = kept

    if config.getoption("--disabled-report"):
        matched = {item.nodeid for item in removed}
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"停用清單:{len(disabled)} 條,命中 {len(matched)} 個")
            for stale in sorted(disabled - matched):
                reporter.write_line(f"  失效條目(對不上任何測試):{stale}")


@dataclass(frozen=True)
class FakeEngine:
    """一個引擎替身執行檔,以及它收到的指令記錄。"""

    path: pathlib.Path
    log: pathlib.Path

    def commands(self) -> list[str]:
        """替身至今收到的所有指令,依送達順序。"""
        if not self.log.is_file():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def fake_engine(tmp_path: pathlib.Path):
    """回傳一個工廠:`fake_engine(mode)` 給出該模式的替身執行檔。

    同一個測試可建立多個模式的替身,彼此的指令記錄互不干擾。
    """
    counter = {"n": 0}

    def make(mode: str) -> FakeEngine:
        counter["n"] += 1
        name = f"engine_{counter['n']}_{mode}"
        executable = tmp_path / name
        log = tmp_path / f"{name}.log"
        executable.write_text(
            "#!/bin/sh\n"
            f"FAKE_ENGINE_LOG={shlex.quote(str(log))}\n"
            "export FAKE_ENGINE_LOG\n"
            f"exec {shlex.quote(sys.executable)} "
            f"{shlex.quote(str(FAKE_ENGINE_SCRIPT))} {shlex.quote(mode)}\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return FakeEngine(path=executable, log=log)

    return make
