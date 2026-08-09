"""題庫索引與依題號取題(需求 6.1、6.2、6.3)。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組只能向左依賴 `types.py`、`errors.py` 與 `config.py`,**不得** import `game.py`
或更右邊的模組。`Position` 由 `types.py` 匯入,**絕不在此重新定義** —— 兩份同名型別
會讓 `isinstance` 檢查與日後 `models.py` 的 Pydantic 轉換靜默失效。

## 佈局與出處

題目 schema 與目錄佈局的權威來源是 `.kiro/steering/structure.md`。關鍵一點:

    出處(書名)由題目所在的**資料夾**表達,不是題目 JSON 的欄位。

因此本模組把「檔案落在哪個書目資料夾」讀成出處,並拒絕含 `source` 之類未知欄位的
題目 —— 一旦出處同時存在於路徑與欄位,兩者遲早互相矛盾。

`id` 為全域唯一的數字,跨書連續。唯一性由人工保證、無機制強制,故本模組在**啟動掃描
階段偵測重複題號並拒絕啟動**,使資料錯誤在部署階段暴露而非等到使用者請求該題。

## 例外選擇

題庫載入發生於啟動期、不經過 HTTP,因此所有載入失敗一律以內建的 `ValueError`
表達,沿用 `config.py` 已確立的慣例;`errors.py` 的七種錯誤類別是**對外 HTTP 契約**,
只有 `get()` 這類服務期路徑才用得上(題號不存在對應 `PositionNotFoundError`)。

## 唯讀

本模組只讀不寫。`max_dtm` 與 `solvable` 由 corpus-verification 日後回填,現階段可為空,
**不得視為必填** —— 把它們當必填等於要求驗證工具先跑完才能啟動服務。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from service.errors import PositionNotFoundError
from service.types import Position, Side

__all__ = ["PositionRepository"]


#: 人工編輯、必填的欄位(structure.md 的題目 schema)。
REQUIRED_FIELDS = (
    "id",
    "title",
    "description",
    "fen",
    "side_to_move",
    "difficulty",
    "tags",
)

#: 由題目驗證工具回填、可為空的欄位。
OPTIONAL_FIELDS = ("max_dtm", "solvable")

KNOWN_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)


class PositionRepository:
    """由分書目錄建立題號索引,依題號提供題目。

    索引在 `load()` 完成後即固定,服務生命週期內不變;同一題號只對應一個題目。
    """

    def __init__(self, positions_dir: Path) -> None:
        self._positions_dir = Path(positions_dir)
        self._by_id: dict[int, Position] | None = None
        self._source_by_id: dict[int, str] = {}

    def load(self) -> None:
        """遞迴掃描題庫目錄,建立題號到題目的記憶體索引。

        於服務啟動時呼叫。任一題目不可解析、schema 不符,或出現重複題號時拋出
        `ValueError`,服務即拒絕啟動。索引只在整份掃描成功後才就位 —— 半套索引
        比沒有索引更危險。
        """
        if not self._positions_dir.is_dir():
            raise ValueError(f"題庫目錄不存在或不是目錄:{self._positions_dir}")

        by_id: dict[int, Position] = {}
        source_by_id: dict[int, str] = {}
        origin_by_id: dict[int, Path] = {}
        conflicts: dict[int, list[Path]] = {}

        for path in self._corpus_files():
            source = self._source_of_path(path)
            position = _read_position(path)
            if position.id in by_id:
                conflicts.setdefault(position.id, [origin_by_id[position.id]]).append(
                    path
                )
                continue
            by_id[position.id] = position
            source_by_id[position.id] = source
            origin_by_id[position.id] = path

        if conflicts:
            raise ValueError(self._duplicate_message(conflicts))

        self._by_id = by_id
        self._source_by_id = source_by_id

    def get(self, position_id: int) -> Position:
        """依題號取題;題號不存在時拋出 `PositionNotFoundError`(6.2)。"""
        index = self._loaded_index()
        try:
            return index[position_id]
        except KeyError as cause:
            raise PositionNotFoundError(f"找不到題號 {position_id} 的題目") from cause

    def source_of(self, position_id: int) -> str:
        """該題的出處書名,取自題目所在的書目資料夾名稱。"""
        self._loaded_index()
        try:
            return self._source_by_id[position_id]
        except KeyError as cause:
            raise PositionNotFoundError(f"找不到題號 {position_id} 的題目") from cause

    def __len__(self) -> int:
        """索引中的題目數。"""
        return len(self._loaded_index())

    def all(self) -> list[Position]:
        """索引中的每一題,**依題號遞增**。

        題庫列表需要一次取得全部題目,而以 `get()` 從 1 逐一探測是錯的 —— 題號的
        唯一性由人工保證、連續性沒有任何機制保證(見「佈局與出處」),遇到第一個
        缺口就會靜默截斷索引。

        順序固定為題號遞增而非檔案系統的走訪次序,否則列表會在每次重啟後莫名重排。
        """
        index = self._loaded_index()
        return [index[position_id] for position_id in sorted(index)]

    # --- 內部 -----------------------------------------------------------

    def _loaded_index(self) -> dict[int, Position]:
        if self._by_id is None:
            raise RuntimeError("題庫尚未載入,請先呼叫 PositionRepository.load()")
        return self._by_id

    def _corpus_files(self) -> list[Path]:
        """題庫中所有題目檔,依路徑排序以使掃描結果與錯誤訊息可重現。

        以副檔名認定題目檔:題庫目錄容得下 README 之類的附屬檔案。以點開頭的檔案
        與資料夾(`.DS_Store`、編輯器暫存目錄)一律跳過。
        """
        files = [
            path
            for path in self._positions_dir.rglob("*.json")
            if path.is_file() and not _is_hidden(path.relative_to(self._positions_dir))
        ]
        return sorted(files)

    def _source_of_path(self, path: Path) -> str:
        """出處 = 題庫根目錄底下的第一層資料夾名稱。

        直接躺在題庫根目錄的題目沒有出處可言,屬佈局錯誤,於此擋下。
        """
        parts = path.relative_to(self._positions_dir).parts
        if len(parts) < 2:
            raise ValueError(
                f"題目檔案 {path} 直接位於題庫根目錄。"
                "出處由資料夾表達,題目必須放在書目資料夾內,例如 適情雅趣/0001.json。"
            )
        return parts[0]

    def _duplicate_message(self, conflicts: dict[int, list[Path]]) -> str:
        lines = [
            "題庫有重複的題號,服務拒絕啟動。題號須全域唯一且跨書連續,"
            "請修正下列衝突後重新啟動:"
        ]
        for position_id in sorted(conflicts):
            where = "、".join(
                str(path.relative_to(self._positions_dir))
                for path in conflicts[position_id]
            )
            lines.append(f"  題號 {position_id}:{where}")
        return "\n".join(lines)


# --- 單一題目的讀取與驗證 -----------------------------------------------


def _is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


def _read_position(path: Path) -> Position:
    """讀取並驗證一個題目檔。任何不符即拋出 `ValueError`,附上檔案路徑。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as cause:
        raise ValueError(f"題目檔案 {path} 不是合法的 JSON:{cause}") from cause
    except OSError as cause:
        raise ValueError(f"題目檔案 {path} 無法讀取:{cause}") from cause

    if not isinstance(raw, dict):
        raise ValueError(f"題目檔案 {path} 的內容必須是 JSON 物件")

    _check_fields(path, raw)

    return Position(
        id=_read_int(path, raw, "id"),
        title=_read_str(path, raw, "title"),
        description=_read_str(path, raw, "description"),
        fen=_read_str(path, raw, "fen"),
        side_to_move=_read_side(path, raw),
        difficulty=_read_int(path, raw, "difficulty"),
        tags=_read_tags(path, raw),
        max_dtm=_read_optional_int(path, raw, "max_dtm"),
        solvable=_read_optional_bool(path, raw, "solvable"),
    )


def _check_fields(path: Path, raw: dict[str, Any]) -> None:
    """必填欄位齊全,且沒有 schema 以外的欄位。

    拒絕未知欄位是刻意的:出處寫成 `source` 欄位、或欄位名打錯字,都會在啟動階段
    立刻現形,而不是靜靜地被忽略。
    """
    missing = [name for name in REQUIRED_FIELDS if name not in raw]
    if missing:
        raise ValueError(f"題目檔案 {path} 缺少必填欄位:{'、'.join(missing)}")

    unknown = sorted(set(raw) - KNOWN_FIELDS)
    if unknown:
        raise ValueError(
            f"題目檔案 {path} 含有題目 schema 以外的欄位:{'、'.join(unknown)}。"
            "出處由所在資料夾表達,不是欄位。"
        )


def _type_error(path: Path, field: str, expected: str, value: Any) -> ValueError:
    return ValueError(
        f"題目檔案 {path} 的欄位 {field} 必須是{expected},目前為 {value!r}"
    )


def _read_int(path: Path, raw: dict[str, Any], field: str) -> int:
    value = raw[field]
    # `bool` 是 `int` 的子類別,不先擋下就會讓 true 靜靜變成 1。
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(path, field, "整數", value)
    return value


def _read_optional_int(path: Path, raw: dict[str, Any], field: str) -> int | None:
    if raw.get(field) is None:
        return None
    return _read_int(path, raw, field)


def _read_optional_bool(path: Path, raw: dict[str, Any], field: str) -> bool | None:
    if raw.get(field) is None:
        return None
    value = raw[field]
    if not isinstance(value, bool):
        raise _type_error(path, field, "布林值", value)
    return value


def _read_str(path: Path, raw: dict[str, Any], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str):
        raise _type_error(path, field, "字串", value)
    return value


def _read_tags(path: Path, raw: dict[str, Any]) -> list[str]:
    value = raw["tags"]
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise _type_error(path, "tags", "字串陣列", value)
    return list(value)


def _read_side(path: Path, raw: dict[str, Any]) -> Side:
    value = raw["side_to_move"]
    if not isinstance(value, str):
        raise _type_error(path, "side_to_move", "字串", value)
    try:
        return Side(value)
    except ValueError as cause:
        allowed = "、".join(side.value for side in Side)
        raise ValueError(
            f"題目檔案 {path} 的欄位 side_to_move 必須是 {allowed} 之一,"
            f"目前為 {value!r}"
        ) from cause
