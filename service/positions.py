"""題庫索引與依題號取題(需求 6.1、6.2、6.3)。

依賴方向為 `types / errors -> config -> positions / engine -> game / editor -> models -> main`,
本模組只能向左依賴 `types.py`、`errors.py` 與 `config.py`,**不得** import `game.py`
或更右邊的模組。`Position` 由 `types.py` 匯入,**絕不在此重新定義** —— 兩份同名型別
會讓 `isinstance` 檢查與日後 `models.py` 的 Pydantic 轉換靜默失效。

## 佈局與出處

題目 schema 與目錄佈局的權威來源是 `.kiro/steering/structure.md`。關鍵一點:

    出處(書名)由題目所在的**資料夾**表達,不是題目 JSON 的欄位。

因此本模組把「檔案落在哪個書目資料夾」讀成出處,並拒絕含 `source` 之類未知欄位的
題目 —— 一旦出處同時存在於路徑與欄位,兩者遲早互相矛盾。

同一道理也適用於起手方:`fen` 的走子方那一欄已經寫著誰先走,因此題目 JSON **沒有**
`side_to_move` 欄位,`Position.side_to_move` 由 FEN 推導(見 `_side_from_fen`)。

## 一檔多題

一個題目檔裝的是**一份題目陣列**,檔名以局號區間表達(`20-24.json` 即第二〇局至
第二四局)。收題是一次抄一段書,檔案切得比那更細只會讓一次收題散進五個檔案。

`id` 為全域唯一的數字,跨書連續。唯一性由人工保證、無機制強制,故本模組在**啟動掃描
階段偵測重複題號並拒絕啟動**,使資料錯誤在部署階段暴露而非等到使用者請求該題。
重複的兩題可能落在同一個檔案裡,錯誤訊息因此指到「哪一檔的第幾題」而不只是檔名。

## 例外選擇

題庫載入發生於啟動期、不經過 HTTP,因此所有載入失敗一律以內建的 `ValueError`
表達,沿用 `config.py` 已確立的慣例;`errors.py` 的七種錯誤類別是**對外 HTTP 契約**,
只有 `get()` 這類服務期路徑才用得上(題號不存在對應 `PositionNotFoundError`)。

## 唯讀

本模組只讀不寫。`max_dtm` 由 corpus-verification 日後回填,現階段可為空,
**不得視為必填** —— 把它當必填等於要求驗證工具先跑完才能啟動服務。

## 候選題目

收題工具要在寫入題庫**之前**就判斷一題合不合格,而那個判準只能是這裡的判準:
放行的題目一旦進了題庫,下次啟動就得載得起來。因此本模組另開一個公開入口
`validate_position()`,驗證一份還不在任何檔案裡的候選題目。

它是 `_read_position()` 的**薄包裝**,不複製也不擴充任何規則 —— 兩份規則遲早分岔,
而分岔的下場是收題工具說可以、服務啟動時說不行。唯讀契約不因這個入口改變:它只
判定並回傳 `Position`,不碰檔案系統。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from service.errors import PositionNotFoundError
from service.types import Position, Side

__all__ = ["PositionRepository", "validate_position"]


#: 人工編輯、必填的欄位(structure.md 的題目 schema)。
#:
#: **不含 `side_to_move`**:起手方寫在 `fen` 的走子方那一欄,另立欄位等於同一件事有
#: 兩個出處。它仍是 `Position` 的欄位,只是由 FEN 推導而來。
REQUIRED_FIELDS = (
    "id",
    "title",
    "description",
    "fen",
    "difficulty",
    "tags",
)

#: 由題目驗證工具回填、可為空的欄位。
OPTIONAL_FIELDS = ("max_dtm",)

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
        origin_by_id: dict[int, str] = {}
        conflicts: dict[int, list[str]] = {}

        for path in self._corpus_files():
            source = self._source_of_path(path)
            relative = path.relative_to(self._positions_dir)
            for offset, position in enumerate(_read_positions(path), start=1):
                # 一檔多題,所以出處要記到「第幾題」——只記檔名的話,同一個檔案裡
                # 的兩題撞號會印成同一個檔名兩次,看不出該去改哪一題。
                origin = f"{relative} 第 {offset} 題"
                if position.id in by_id:
                    conflicts.setdefault(
                        position.id, [origin_by_id[position.id]]
                    ).append(origin)
                    continue
                by_id[position.id] = position
                source_by_id[position.id] = source
                origin_by_id[position.id] = origin

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

    def _duplicate_message(self, conflicts: dict[int, list[str]]) -> str:
        lines = [
            "題庫有重複的題號,服務拒絕啟動。題號須全域唯一且跨書連續,"
            "請修正下列衝突後重新啟動:"
        ]
        for position_id in sorted(conflicts):
            lines.append(f"  題號 {position_id}:{'、'.join(conflicts[position_id])}")
        return "\n".join(lines)


# --- 候選題目的 schema 驗證 ---------------------------------------------


#: `validate_position()` 出錯時指路用的說法。載入路徑指得出「哪一檔第幾題」,候選題目
#: 還不在任何檔案裡,只能以它的身分自稱。
CANDIDATE_WHERE = "候選題目"


def validate_position(raw: Any) -> Position:
    """驗證一份**還不在題庫裡**的候選題目,合格時回傳對應的 `Position`。

    供收題工具在寫入前取得權威判定(見模組說明的「候選題目」)。判準與 `load()`
    逐字相同:兩者走的是同一個 `_read_position()`,本函式只固定了指路的說法。

    不合格時拋出 `ValueError`,與載入路徑同一個錯誤型別 —— 「這一題不合格」是驗證
    的**結果**而非服務失敗,呼叫端據此組出自己的回應形狀,`errors.py` 的七種錯誤
    類別(對外 HTTP 契約)不為此擴充。
    """
    return _read_position(CANDIDATE_WHERE, raw)


# --- 單一題目的讀取與驗證 -----------------------------------------------


def _is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


#: FEN 走子方那一欄的兩個合法取值。中國象棋 FEN 沿用國際象棋的 `w`/`b`,
#: 紅方即 `w`(見 `positions/` 現有題目與 Pikafish 的 `position fen`)。
FEN_SIDES = {"w": Side.RED, "b": Side.BLACK}


def _read_positions(path: Path) -> list[Position]:
    """讀取並驗證一個題目檔裡的每一題。

    檔案裝的是**一份題目陣列**(見模組說明的「一檔多題」),即使只有一題也一樣 ——
    形狀隨題數改變的話,收題時補進第二題就得順手改檔案結構,而那正是最容易漏掉的
    一步。任何不符即拋出 `ValueError`,附上檔案路徑與檔內題序。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as cause:
        raise ValueError(f"題目檔案 {path} 不是合法的 JSON:{cause}") from cause
    except OSError as cause:
        raise ValueError(f"題目檔案 {path} 無法讀取:{cause}") from cause

    if not isinstance(raw, list):
        raise ValueError(
            f"題目檔案 {path} 的內容必須是題目陣列。一個檔案裝一段局號區間的題目,"
            "只有一題時也要寫成只有一個元素的陣列。"
        )

    return [
        _read_position(f"題目檔案 {path} 第 {offset} 題", item)
        for offset, item in enumerate(raw, start=1)
    ]


def _read_position(where: str, raw: Any) -> Position:
    """驗證陣列中的一題。`where` 是出錯時指路用的說法,含檔案路徑與檔內題序。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必須是 JSON 物件")

    _check_fields(where, raw)

    fen = _read_str(where, raw, "fen")
    return Position(
        id=_read_int(where, raw, "id"),
        title=_read_str(where, raw, "title"),
        description=_read_str(where, raw, "description"),
        fen=fen,
        side_to_move=_side_from_fen(where, fen),
        difficulty=_read_int(where, raw, "difficulty"),
        tags=_read_tags(where, raw),
        max_dtm=_read_optional_int(where, raw, "max_dtm"),
    )


def _check_fields(where: str, raw: dict[str, Any]) -> None:
    """必填欄位齊全,且沒有 schema 以外的欄位。

    拒絕未知欄位是刻意的:出處寫成 `source` 欄位、起手方寫成 `side_to_move` 欄位、
    或欄位名打錯字,都會在啟動階段立刻現形,而不是靜靜地被忽略。
    """
    missing = [name for name in REQUIRED_FIELDS if name not in raw]
    if missing:
        raise ValueError(f"{where} 缺少必填欄位:{'、'.join(missing)}")

    unknown = sorted(set(raw) - KNOWN_FIELDS)
    if unknown:
        raise ValueError(
            f"{where} 含有題目 schema 以外的欄位:{'、'.join(unknown)}。"
            "出處由所在資料夾表達、起手方由 fen 表達,兩者都不是欄位。"
        )


def _side_from_fen(where: str, fen: str) -> Side:
    """由 FEN 的走子方那一欄判定起手方。

    起手方**只有這一個出處**。題目 JSON 不再有 `side_to_move` 欄位:那個欄位與 FEN
    講的是同一件事,而兩份會分岔 —— 分岔時前端畫的是 FEN、後端算輪方用的是欄位,
    整盤棋的輪方從第一手起就錯開。
    """
    fields = fen.split()
    if len(fields) < 2:
        raise ValueError(
            f"{where} 的 fen 缺少走子方那一欄,無從判定起手方:{fen!r}"
        )
    try:
        return FEN_SIDES[fields[1]]
    except KeyError as cause:
        allowed = "、".join(FEN_SIDES)
        raise ValueError(
            f"{where} 的 fen 走子方必須是 {allowed} 之一,目前為 {fields[1]!r}"
        ) from cause


def _type_error(where: str, field: str, expected: str, value: Any) -> ValueError:
    return ValueError(f"{where} 的欄位 {field} 必須是{expected},目前為 {value!r}")


def _read_int(where: str, raw: dict[str, Any], field: str) -> int:
    value = raw[field]
    # `bool` 是 `int` 的子類別,不先擋下就會讓 true 靜靜變成 1。
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(where, field, "整數", value)
    return value


def _read_optional_int(where: str, raw: dict[str, Any], field: str) -> int | None:
    if raw.get(field) is None:
        return None
    return _read_int(where, raw, field)


def _read_str(where: str, raw: dict[str, Any], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str):
        raise _type_error(where, field, "字串", value)
    return value


def _read_tags(where: str, raw: dict[str, Any]) -> list[str]:
    value = raw["tags"]
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise _type_error(where, "tags", "字串陣列", value)
    return list(value)
