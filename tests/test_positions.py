"""題庫索引與依題號取題的測試(對應 tasks 3.1、requirements 6.1、6.2、6.3)。

題目 schema 的權威來源是 `.kiro/steering/structure.md` 的「題目 schema」表格,
目錄佈局則是「出處以**資料夾**表達」—— 題目 JSON 放在哪個資料夾就代表出自哪本書,
**沒有 `source` 欄位**。因此本測試以暫存目錄自建多本書的題庫,驗證:

- 依題號取題不受題目落在哪本書影響(6.1)
- 題號不存在時拋出 `PositionNotFoundError`(6.2)
- 重複題號使**啟動失敗**,並指出衝突的題號 —— 題號全域唯一由人工保證,
  此檢查使資料錯誤在部署階段暴露而非執行期
- `max_dtm` 與 `solvable` 可為空(尚待 corpus-verification 回填),不得視為必填
- 新增題目只需放入檔案,不需修改程式或設定(6.3)

啟動期的失敗一律以內建 `ValueError` 表達,**不使用** `errors.py` 的七種錯誤類別
(那七種是對外 HTTP 契約,啟動失敗不經過 HTTP)—— 沿用 1.3 已確立的慣例。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import pytest

from service.config import DEFAULT_POSITIONS_DIR
from service.errors import PositionNotFoundError, ServiceError
from service.positions import PositionRepository
from service.types import Position, Side


#: 《適情雅趣》第二一局的起始局面,僅作為測試題庫的填充內容。
SAMPLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 標記「這個欄位在 JSON 裡不存在」,以區別於「欄位存在但值為 null」。
OMIT = object()


def _write_position(
    root: pathlib.Path,
    book: str | None,
    position_id: int,
    *,
    filename: str | None = None,
    **overrides: Any,
) -> pathlib.Path:
    """在 `root/book/` 底下寫一題;`book` 為 None 時直接寫在題庫根目錄。"""
    payload: dict[str, Any] = {
        "id": position_id,
        "title": f"題目{position_id}",
        "description": f"{book} 第{position_id}局",
        "fen": SAMPLE_FEN,
        "side_to_move": "red",
        "difficulty": 3,
        "tags": ["連將殺"],
        "max_dtm": 16,
        "solvable": True,
    }
    payload.update(overrides)
    for key in [k for k, v in payload.items() if v is OMIT]:
        del payload[key]

    folder = root if book is None else root / book
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (filename if filename else f"{position_id:04d}.json")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _loaded(root: pathlib.Path) -> PositionRepository:
    repository = PositionRepository(root)
    repository.load()
    return repository


def _two_book_corpus(root: pathlib.Path) -> pathlib.Path:
    """兩本書的題庫:題號跨書連續,資料夾只表達出處。"""
    _write_position(root, "適情雅趣", 1)
    _write_position(root, "適情雅趣", 2)
    _write_position(root, "橘中秘", 201)
    return root


# --- 真實題庫 ------------------------------------------------------------


def test_real_corpus_loads_and_serves_positions_by_id() -> None:
    """實際載入專案的 `positions/`,證明現行題庫檔案能被正確解析(6.1)。"""
    repository = _loaded(DEFAULT_POSITIONS_DIR)

    position = repository.get(1)
    assert position.id == 1
    assert position.title == "盡善克終"
    assert position.fen == SAMPLE_FEN
    assert position.side_to_move is Side.RED
    assert position.difficulty == 3
    assert "連將殺" in position.tags
    assert position.max_dtm == 16
    assert position.solvable is True


def test_real_corpus_source_comes_from_the_folder_name() -> None:
    """出處由所在資料夾表達,不是題目 JSON 的欄位。"""
    repository = _loaded(DEFAULT_POSITIONS_DIR)
    assert repository.source_of(1) == "適情雅趣"


def test_repository_returns_the_shared_domain_type() -> None:
    """題目型別必須是 `service.types.Position` 本尊。

    若 `positions.py` 自行重新定義同名型別,`isinstance` 檢查與日後 `models.py`
    的 Pydantic 轉換會**靜默**失效,故在此釘死型別同一性。
    """
    from service import positions as positions_module

    assert getattr(positions_module, "Position", Position) is Position
    assert type(_loaded(DEFAULT_POSITIONS_DIR).get(1)) is Position


# --- 分書目錄與依題號取題(6.1) ----------------------------------------


def test_index_covers_every_book(tmp_path: pathlib.Path) -> None:
    """題號跨書連續,取題不需要知道題目在哪本書。"""
    repository = _loaded(_two_book_corpus(tmp_path))

    assert len(repository) == 3
    assert repository.get(1).id == 1
    assert repository.get(2).id == 2
    assert repository.get(201).id == 201


def test_source_reflects_the_owning_book(tmp_path: pathlib.Path) -> None:
    repository = _loaded(_two_book_corpus(tmp_path))

    assert repository.source_of(1) == "適情雅趣"
    assert repository.source_of(201) == "橘中秘"


def test_source_is_the_top_level_book_folder_even_when_nested(
    tmp_path: pathlib.Path,
) -> None:
    """書內再分卷時,出處仍是最上層的書目資料夾。"""
    _write_position(tmp_path, "適情雅趣/卷一", 1)

    assert _loaded(tmp_path).source_of(1) == "適情雅趣"


def test_side_to_move_is_parsed_into_the_domain_enum(tmp_path: pathlib.Path) -> None:
    _write_position(tmp_path, "橘中秘", 7, side_to_move="black")

    assert _loaded(tmp_path).get(7).side_to_move is Side.BLACK


def test_non_json_files_are_ignored(tmp_path: pathlib.Path) -> None:
    """題庫目錄容得下 README 之類的附屬檔案,不該讓啟動失敗。

    這裡只涵蓋副檔名不是 `.json` 的檔案;帶 `.json` 副檔名的隱藏檔另見
    `test_hidden_json_files_are_ignored`。
    """
    _write_position(tmp_path, "適情雅趣", 1)
    (tmp_path / "適情雅趣" / "README.md").write_text("說明", encoding="utf-8")
    (tmp_path / "適情雅趣" / ".DS_Store").write_bytes(b"\x00\x01")

    assert len(_loaded(tmp_path)) == 1


def test_hidden_json_files_are_ignored(tmp_path: pathlib.Path) -> None:
    """隱藏檔即使帶 `.json` 副檔名也不是題目,不得讓啟動失敗。

    macOS 的 AppleDouble sidecar `._0001.json` 同時是隱藏檔且帶 `.json` 副檔名
    —— 解壓縮題目批次、或經 exFAT 隨身碟搬運時就會產生。以點開頭的**資料夾**
    同理:`.git/objects/` 底下大有可能躺著副檔名為 `.json` 的檔案。
    兩者若被當成題目解析,啟動就會失敗。
    """
    _write_position(tmp_path, "適情雅趣", 1)
    # AppleDouble sidecar:magic bytes 開頭的二進位內容,絕非合法題目 JSON。
    (tmp_path / "適情雅趣" / "._0001.json").write_bytes(
        b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X"
    )
    stale = tmp_path / ".git" / "objects"
    stale.mkdir(parents=True)
    (stale / "stale.json").write_text("{ 這不是 JSON", encoding="utf-8")

    repository = _loaded(tmp_path)

    assert len(repository) == 1
    assert repository.get(1).id == 1


# --- 題號不存在(6.2) --------------------------------------------------


def test_unknown_id_raises_position_not_found(tmp_path: pathlib.Path) -> None:
    repository = _loaded(_two_book_corpus(tmp_path))

    with pytest.raises(PositionNotFoundError):
        repository.get(9999)


def test_position_not_found_is_a_service_error_with_404(
    tmp_path: pathlib.Path,
) -> None:
    """題目不存在是對外 HTTP 契約的一環(6.2),不是啟動期錯誤。"""
    repository = _loaded(_two_book_corpus(tmp_path))

    with pytest.raises(ServiceError) as info:
        repository.get(9999)
    assert info.value.http_status == 404


def test_source_of_unknown_id_raises_position_not_found(
    tmp_path: pathlib.Path,
) -> None:
    repository = _loaded(_two_book_corpus(tmp_path))

    with pytest.raises(PositionNotFoundError):
        repository.source_of(9999)


# --- 重複題號使啟動失敗 --------------------------------------------------


def test_duplicate_id_across_books_refuses_to_start(tmp_path: pathlib.Path) -> None:
    _write_position(tmp_path, "適情雅趣", 1)
    _write_position(tmp_path, "橘中秘", 1)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "1" in str(info.value)


def test_duplicate_id_within_one_book_refuses_to_start(
    tmp_path: pathlib.Path,
) -> None:
    """同一本書內以不同檔名放入同題號,同樣必須擋下。"""
    _write_position(tmp_path, "適情雅趣", 42)
    _write_position(tmp_path, "適情雅趣", 42, filename="0042-copy.json")

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "42" in str(info.value)


def test_duplicate_message_names_every_conflicting_id(
    tmp_path: pathlib.Path,
) -> None:
    """一次指出所有衝突的題號,才能一輪修完而不是逐題重試。"""
    _write_position(tmp_path, "適情雅趣", 1)
    _write_position(tmp_path, "橘中秘", 1)
    _write_position(tmp_path, "適情雅趣", 5)
    _write_position(tmp_path, "橘中秘", 5)
    _write_position(tmp_path, "適情雅趣", 9)  # 未衝突,不該被指名

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    message = str(info.value)
    assert "1" in message and "5" in message
    assert "9" not in message


def test_duplicate_message_points_at_the_conflicting_files(
    tmp_path: pathlib.Path,
) -> None:
    """啟動失敗訊息只給操作者看,指出檔案才修得動。"""
    _write_position(tmp_path, "適情雅趣", 1)
    _write_position(tmp_path, "橘中秘", 1)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    message = str(info.value)
    assert "適情雅趣" in message and "橘中秘" in message


def test_duplicate_id_leaves_the_repository_unusable(
    tmp_path: pathlib.Path,
) -> None:
    """啟動失敗後不得留下半套索引供人誤用。"""
    _write_position(tmp_path, "適情雅趣", 1)
    _write_position(tmp_path, "橘中秘", 1)

    repository = PositionRepository(tmp_path)
    with pytest.raises(ValueError):
        repository.load()
    with pytest.raises(RuntimeError):
        repository.get(1)


# --- max_dtm 與 solvable 可為空 -----------------------------------------


def test_missing_max_dtm_and_solvable_are_none(tmp_path: pathlib.Path) -> None:
    """兩欄由題目驗證工具日後回填,現階段缺欄不得視為錯誤。"""
    _write_position(tmp_path, "適情雅趣", 1, max_dtm=OMIT, solvable=OMIT)

    position = _loaded(tmp_path).get(1)
    assert position.max_dtm is None
    assert position.solvable is None


def test_explicit_null_max_dtm_and_solvable_are_none(
    tmp_path: pathlib.Path,
) -> None:
    _write_position(tmp_path, "適情雅趣", 1, max_dtm=None, solvable=None)

    position = _loaded(tmp_path).get(1)
    assert position.max_dtm is None
    assert position.solvable is None


def test_solvable_false_is_preserved(tmp_path: pathlib.Path) -> None:
    """`false` 與「未回填」是兩件事,不得被空值邏輯吃掉。"""
    _write_position(tmp_path, "適情雅趣", 1, solvable=False)

    assert _loaded(tmp_path).get(1).solvable is False


# --- schema 不符於啟動階段失敗 ------------------------------------------


@pytest.mark.parametrize(
    "field", ["id", "title", "description", "fen", "side_to_move", "difficulty", "tags"]
)
def test_missing_required_field_refuses_to_start(
    tmp_path: pathlib.Path, field: str
) -> None:
    _write_position(tmp_path, "適情雅趣", 1, **{field: OMIT})

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert field in str(info.value)


def test_malformed_json_refuses_to_start(tmp_path: pathlib.Path) -> None:
    book = tmp_path / "適情雅趣"
    book.mkdir(parents=True)
    (book / "0001.json").write_text("{ 這不是 JSON", encoding="utf-8")

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "0001.json" in str(info.value)


def test_unknown_side_to_move_refuses_to_start(tmp_path: pathlib.Path) -> None:
    _write_position(tmp_path, "適情雅趣", 1, side_to_move="green")

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "side_to_move" in str(info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "1"),
        ("id", True),
        ("title", 1),
        ("side_to_move", 1),
        ("difficulty", "3"),
        ("tags", "連將殺"),
        ("tags", [1, 2]),
        ("max_dtm", "16"),
        ("solvable", "true"),
    ],
)
def test_wrong_field_type_refuses_to_start(
    tmp_path: pathlib.Path, field: str, value: Any
) -> None:
    _write_position(tmp_path, "適情雅趣", 1, **{field: value})

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert field in str(info.value)


def test_source_as_a_field_refuses_to_start(tmp_path: pathlib.Path) -> None:
    """出處由資料夾表達;寫成欄位就是兩個真相來源,必須在部署階段擋下。"""
    _write_position(tmp_path, "適情雅趣", 1, source="橘中秘")

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "source" in str(info.value)


def test_position_file_outside_any_book_folder_refuses_to_start(
    tmp_path: pathlib.Path,
) -> None:
    """直接躺在題庫根目錄的題目沒有出處可言,屬佈局錯誤。"""
    _write_position(tmp_path, None, 1)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "0001.json" in str(info.value)


def test_json_object_is_required(tmp_path: pathlib.Path) -> None:
    book = tmp_path / "適情雅趣"
    book.mkdir(parents=True)
    (book / "0001.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError):
        PositionRepository(tmp_path).load()


def test_missing_corpus_directory_refuses_to_start(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError):
        PositionRepository(tmp_path / "不存在").load()


# --- 未載入前不得取題 ----------------------------------------------------


def test_get_before_load_raises(tmp_path: pathlib.Path) -> None:
    """`load()` 是啟動掛鉤的責任;未載入就取題是程式錯誤,不是題目不存在。"""
    _two_book_corpus(tmp_path)
    repository = PositionRepository(tmp_path)

    with pytest.raises(RuntimeError):
        repository.get(1)


def test_source_of_before_load_raises(tmp_path: pathlib.Path) -> None:
    _two_book_corpus(tmp_path)

    with pytest.raises(RuntimeError):
        PositionRepository(tmp_path).source_of(1)


# --- 唯讀 ----------------------------------------------------------------


def _corpus_digest(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_loading_does_not_write_to_the_corpus(tmp_path: pathlib.Path) -> None:
    _two_book_corpus(tmp_path)
    before = _corpus_digest(tmp_path)

    repository = _loaded(tmp_path)
    repository.get(1)

    assert _corpus_digest(tmp_path) == before


def test_loading_the_real_corpus_does_not_write_to_it() -> None:
    before = _corpus_digest(DEFAULT_POSITIONS_DIR)

    _loaded(DEFAULT_POSITIONS_DIR).get(1)

    assert _corpus_digest(DEFAULT_POSITIONS_DIR) == before


# --- 擴充至 500 題不需改程式或設定(6.3) ------------------------------


def test_five_hundred_positions_load_without_code_or_config_change(
    tmp_path: pathlib.Path,
) -> None:
    """題庫擴充只靠放檔案:同一份程式、同一個題庫路徑,不列舉書名。"""
    books = ["適情雅趣", "橘中秘", "梅花譜"]
    for position_id in range(1, 501):
        _write_position(tmp_path, books[position_id % len(books)], position_id)

    repository = _loaded(tmp_path)

    assert len(repository) == 500
    assert {repository.get(i).id for i in range(1, 501)} == set(range(1, 501))


def test_a_brand_new_book_needs_no_registration(tmp_path: pathlib.Path) -> None:
    """新增一本書只是多一個資料夾,程式與設定都不動。"""
    _two_book_corpus(tmp_path)
    repository = _loaded(tmp_path)
    assert len(repository) == 3

    _write_position(tmp_path, "百局象棋譜", 301)
    reloaded = _loaded(tmp_path)

    assert len(reloaded) == 4
    assert reloaded.source_of(301) == "百局象棋譜"
