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
"""

from __future__ import annotations

import pathlib
import shlex
import stat
import sys
from dataclasses import dataclass

import pytest

from tests.conftest_web import browser, browser_page  # noqa: F401

FAKES_DIR = pathlib.Path(__file__).resolve().parent / "fakes"
FAKE_ENGINE_SCRIPT = FAKES_DIR / "fake_engine.py"


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


def pytest_configure(config: pytest.Config) -> None:
    # 清掉 addopts 寫死的 `-m 'not slow'`,而不是再疊一層 marker 運算式。
    if config.getoption("--slow"):
        config.option.markexpr = ""


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
