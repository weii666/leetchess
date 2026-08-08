"""引擎層:UCI 協定封裝與引擎資源池。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本層只能向左依賴 `types.py`、`errors.py`、`config.py`,**不得**認識 `game.py` 或
`models.py` —— 引擎層不知道「對局」「題目」「HTTP」等上層概念。
"""

from __future__ import annotations

from service.engine.process import EngineProcess

__all__ = ["EngineProcess"]
