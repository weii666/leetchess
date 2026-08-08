"""依賴方向與循環匯入的測試(對應 design 的 File Structure Plan)。

依賴方向:types / errors  ->  config  ->  positions / engine  ->  game  ->  models  ->  main
`types.py` 與 `errors.py` 位於最左端,不得 import 任何其他 service 模組。
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICE_DIR = PROJECT_ROOT / "service"

LEFTMOST_MODULES = ["types", "errors"]


def _imported_module_names(source_path: pathlib.Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相對匯入,如 from . import x / from .types import Side
                prefix = "." * node.level
                names.add(f"{prefix}{node.module or ''}")
                names.update(f"{prefix}{alias.name}" for alias in node.names)
            elif node.module:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("module", LEFTMOST_MODULES)
def test_leftmost_modules_import_no_other_service_module(module: str) -> None:
    path = SERVICE_DIR / f"{module}.py"
    assert path.is_file(), f"{path} 必須存在"
    for name in _imported_module_names(path):
        assert not name.startswith("service"), f"{module}.py 不得 import {name}"
        assert not name.startswith("."), f"{module}.py 不得使用相對匯入 {name}"


def _import_in_fresh_interpreter(statement: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", statement],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "statement",
    [
        "import service.types, service.errors",
        "import service.errors, service.types",
        "from service.types import Side, GameState, Position; "
        "from service.errors import ErrorCode",
        "from service.errors import ErrorCode; "
        "from service.types import Position, GameState, Side",
    ],
)
def test_both_import_orders_succeed(statement: str) -> None:
    """兩個方向各匯入一次,實際證明沒有循環匯入。"""
    result = _import_in_fresh_interpreter(statement)
    assert result.returncode == 0, result.stderr


def test_corpus_and_game_types_come_from_the_same_module() -> None:
    """題庫型別(Position)與對局型別(GameState)共用 Side 且同源,故不互相依賴。"""
    from service.types import GameState, Position, Side

    assert Position.__module__ == GameState.__module__ == Side.__module__
    assert Side.__module__ == "service.types"
