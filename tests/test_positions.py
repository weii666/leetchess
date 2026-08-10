"""題庫索引與依題號取題的測試(對應 tasks 3.1、requirements 6.1、6.2、6.3)。

題目 schema 的權威來源是 `.kiro/steering/structure.md` 的「題目 schema」表格,
目錄佈局則是「出處以**資料夾**表達」—— 題目 JSON 放在哪個資料夾就代表出自哪本書,
**沒有 `source` 欄位**;起手方同理由 `fen` 表達,**沒有 `side_to_move` 欄位**。
因此本測試以暫存目錄自建多本書的題庫,驗證:

- 依題號取題不受題目落在哪本書影響(6.1)
- 一個檔案裝一段局號區間的題目,內容**恆為陣列**
- 起手方由 FEN 的走子方那一欄推導,而不是另一個欄位
- 題號不存在時拋出 `PositionNotFoundError`(6.2)
- 重複題號使**啟動失敗**,並指出衝突的題號與**檔內題序** —— 題號全域唯一由人工
  保證,此檢查使資料錯誤在部署階段暴露而非執行期
- `max_dtm` 可為空(尚待 corpus-verification 回填),不得視為必填
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

#: 真實題庫裡用來釘住解析結果的那一題。專案的第一題,亦即《適情雅趣》第二一局。
REAL_POSITION_ID = 21

#: 標記「這個欄位在 JSON 裡不存在」,以區別於「欄位存在但值為 null」。
OMIT = object()


def _payload(book: str | None, position_id: int, **overrides: Any) -> dict[str, Any]:
    """一題的 JSON 內容。**沒有 `side_to_move` 也沒有 `solvable`** —— 起手方在 fen
    裡,可解標記已隨 schema 移除,兩者出現在這裡就會被當成未知欄位擋下。
    """
    payload: dict[str, Any] = {
        "id": position_id,
        "title": f"題目{position_id}",
        "description": f"{book} 第{position_id}局",
        "fen": SAMPLE_FEN,
        "difficulty": 3,
        "tags": ["連將殺"],
        "max_dtm": 16,
    }
    payload.update(overrides)
    for key in [k for k, v in payload.items() if v is OMIT]:
        del payload[key]
    return payload


def _write_file(
    root: pathlib.Path, book: str | None, filename: str, content: Any
) -> pathlib.Path:
    """在 `root/book/` 底下寫一個題目檔;`book` 為 None 時寫在題庫根目錄。"""
    folder = root if book is None else root / book
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def _write_position(
    root: pathlib.Path,
    book: str | None,
    position_id: int,
    *,
    filename: str | None = None,
    **overrides: Any,
) -> pathlib.Path:
    """寫一個只裝一題的題目檔。

    內容仍是**陣列**:檔案形狀不隨題數改變(見 `positions.py` 的「一檔多題」),
    只有一題的檔案寫成裸物件才是錯的。檔名沿用局號區間的慣例,一題的區間即它自己。
    """
    return _write_file(
        root,
        book,
        filename or f"{position_id}-{position_id}.json",
        [_payload(book, position_id, **overrides)],
    )


def _write_positions(
    root: pathlib.Path, book: str, filename: str, *position_ids: int
) -> pathlib.Path:
    """把數題寫進同一個檔案 —— 收題的常態形狀(`20-24.json` 裝第二〇到二四局)。"""
    return _write_file(
        root, book, filename, [_payload(book, one) for one in position_ids]
    )


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
    """實際載入專案的 `positions/`,證明現行題庫檔案能被正確解析(6.1)。

    只釘住一題的具體內容。題庫會一直長,把每一題都寫進斷言等於每收一題就要回頭
    改測試 —— 而這個測試要守的是「真實檔案解析得出來」,不是題庫的當期內容。
    """
    repository = _loaded(DEFAULT_POSITIONS_DIR)

    position = repository.get(REAL_POSITION_ID)
    assert position.id == REAL_POSITION_ID
    assert position.title == "盡善克終"
    assert position.fen == SAMPLE_FEN
    assert position.side_to_move is Side.RED
    assert position.difficulty == 3
    assert position.tags
    assert position.max_dtm == 16


def test_real_corpus_ids_are_unique_across_the_whole_corpus() -> None:
    """真實題庫載得起來,即表示沒有重號 —— 一檔多題之後,撞號最可能發生在同一檔內。

    `load()` 對重號是拋錯而非略過,所以這裡不必自己比對:載得起來就是沒撞。額外
    確認每一題都有出處,擋住「題目直接躺在題庫根目錄」那種佈局錯誤。
    """
    repository = _loaded(DEFAULT_POSITIONS_DIR)

    positions = repository.all()
    assert positions, "題庫不得是空的"
    assert len(positions) == len(repository)
    assert all(repository.source_of(one.id) for one in positions)


def _folder_holding(position_id: int) -> str:
    """真實題庫裡,收著某一題的那個資料夾的名字。"""
    for path in sorted(DEFAULT_POSITIONS_DIR.rglob("*.json")):
        entries = json.loads(path.read_text(encoding="utf-8"))
        if any(entry.get("id") == position_id for entry in entries):
            return path.parent.name
    raise AssertionError(f"真實題庫裡找不到第 {position_id} 題")


def test_real_corpus_source_comes_from_the_folder_name() -> None:
    """出處由所在資料夾表達,不是題目 JSON 的欄位。

    期望值**自檔案系統推導,不寫死字面**。理由是這一條要驗的就是「出處等於資料夾
    名」——抄一份資料夾名進來只是把同一件事寫第二遍,而題庫改名時會以一條看不出
    成因的失敗收場(實測過:資料夾自「…-卷一」改名為「…~卷一」之後,這裡報的是
    兩個看起來幾乎一樣的字串不相等)。

    推導不會讓斷言變成恆真:它仍然擋得住「出處取自 JSON 的某個欄位」「出處寫死在
    程式裡」「出處取的是檔名而非資料夾名」這幾種實作。
    """
    repository = _loaded(DEFAULT_POSITIONS_DIR)
    assert repository.source_of(REAL_POSITION_ID) == _folder_holding(REAL_POSITION_ID)


def test_repository_returns_the_shared_domain_type() -> None:
    """題目型別必須是 `service.types.Position` 本尊。

    若 `positions.py` 自行重新定義同名型別,`isinstance` 檢查與日後 `models.py`
    的 Pydantic 轉換會**靜默**失效,故在此釘死型別同一性。
    """
    from service import positions as positions_module

    assert getattr(positions_module, "Position", Position) is Position
    assert type(_loaded(DEFAULT_POSITIONS_DIR).get(REAL_POSITION_ID)) is Position


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


# --- 一檔多題 -----------------------------------------------------------


def test_every_position_in_one_file_is_indexed(tmp_path: pathlib.Path) -> None:
    """一個檔案裝一段局號區間,裡面每一題都要進索引。"""
    _write_positions(tmp_path, "適情雅趣-卷一", "20-24.json", 20, 21, 22)

    repository = _loaded(tmp_path)

    assert len(repository) == 3
    assert [one.id for one in repository.all()] == [20, 21, 22]
    assert repository.source_of(22) == "適情雅趣-卷一"


def test_a_gap_inside_the_filename_range_is_fine(tmp_path: pathlib.Path) -> None:
    """檔名是區間,不是保證 —— 收到哪一局就是哪一局,中間跳號是常態。

    程式**不解析檔名**:`20-24.json` 裡只有第二一局完全正常,那個區間也不會被
    拿來校驗題號。真要對上,得先有「哪些局該收」的清單,而那份清單並不存在。
    """
    _write_positions(tmp_path, "適情雅趣-卷一", "20-24.json", 21)

    assert [one.id for one in _loaded(tmp_path).all()] == [21]


def test_an_empty_position_file_is_not_an_error(tmp_path: pathlib.Path) -> None:
    """空陣列是「這一段還沒收」,不是壞掉的檔案 —— 先建檔再逐題填是收題的常態。"""
    _write_file(tmp_path, "適情雅趣-卷一", "25-29.json", [])
    _write_position(tmp_path, "適情雅趣-卷一", 21)

    assert len(_loaded(tmp_path)) == 1


def test_a_bare_object_instead_of_an_array_refuses_to_start(
    tmp_path: pathlib.Path,
) -> None:
    """只有一題時也要寫成陣列。裸物件收下的話,補進第二題就得改檔案結構。"""
    _write_file(tmp_path, "適情雅趣-卷一", "21-21.json", _payload("適情雅趣-卷一", 21))

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "21-21.json" in str(info.value)


def test_duplicate_ids_inside_one_file_name_the_offending_entries(
    tmp_path: pathlib.Path,
) -> None:
    """同一檔案裡撞號時,訊息要指到第幾題 —— 只講檔名會印出同一個檔名兩次。"""
    _write_positions(tmp_path, "適情雅趣-卷一", "20-24.json", 20, 21, 21)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()

    message = str(info.value)
    assert "20-24.json" in message
    assert "第 2 題" in message and "第 3 題" in message, "須指出檔內題序才修得動"


# --- 起手方來自 FEN -----------------------------------------------------


def test_side_to_move_comes_from_the_fen(tmp_path: pathlib.Path) -> None:
    """起手方只有一個出處:FEN 的走子方那一欄。"""
    black_first = SAMPLE_FEN.replace(" w ", " b ")
    _write_position(tmp_path, "橘中秘", 7, fen=black_first)
    _write_position(tmp_path, "橘中秘", 8)

    repository = _loaded(tmp_path)

    assert repository.get(7).side_to_move is Side.BLACK
    assert repository.get(8).side_to_move is Side.RED


@pytest.mark.parametrize(
    ("case", "fen"),
    [
        ("走子方認不得", SAMPLE_FEN.replace(" w ", " g ")),
        ("整段走子方缺席", SAMPLE_FEN.split(" ")[0]),
    ],
    ids=["走子方認不得", "整段走子方缺席"],
)
def test_a_fen_without_a_usable_side_to_move_refuses_to_start(
    tmp_path: pathlib.Path, case: str, fen: str
) -> None:
    """起手方判不出來就不是一道題:局面畫得出來,但輪方從第一手起就是錯的。"""
    _write_position(tmp_path, "適情雅趣-卷一", 21, fen=fen)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "fen" in str(info.value), f"{case}:錯誤訊息須指出是 fen 的問題"


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


def test_duplicate_ids_refuse_to_start_and_name_every_conflict(
    tmp_path: pathlib.Path,
) -> None:
    """一次指出所有衝突的題號與檔案,才能一輪修完而不是逐題重試。

    跨書重複、同書內以不同檔名重複、多組衝突並存,在此一併涵蓋 —— 它們走的是
    `load()` 裡同一段衝突累積邏輯,原本拆成四個測試只是把同一條路徑走四遍。
    """
    _write_position(tmp_path, "適情雅趣", 1)
    _write_position(tmp_path, "橘中秘", 1)  # 跨書重複
    _write_position(tmp_path, "適情雅趣", 5)
    _write_position(tmp_path, "適情雅趣", 5, filename="5-5-copy.json")  # 同書重複
    _write_position(tmp_path, "適情雅趣", 9)  # 未衝突,不該被指名

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()

    message = str(info.value)
    assert "1" in message and "5" in message
    assert "9" not in message, "未衝突的題號不該被指名"
    assert "適情雅趣" in message and "橘中秘" in message, "須指出檔案才修得動"


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


# --- max_dtm 可為空 ------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "value"), [("缺欄位", OMIT), ("明寫 null", None)], ids=["缺欄位", "明寫 null"]
)
def test_an_unfilled_max_dtm_is_none(
    tmp_path: pathlib.Path, case: str, value: Any
) -> None:
    """由 corpus-verification 日後回填,現階段缺欄與明寫 null 都不得視為錯誤。"""
    _write_position(tmp_path, "適情雅趣", 1, max_dtm=value)

    assert _loaded(tmp_path).get(1).max_dtm is None, case


# --- schema 不符於啟動階段失敗 ------------------------------------------


# 每一種「題目檔壞掉」的代表性情形各一個案例。
#
# 這裡曾經有 19 個案例(每個必填欄位各一、每種型別錯配各一),但題目 JSON 是專案
# 自己寫的資料、不是使用者輸入,逐欄位窮舉只是把 `REQUIRED_FIELDS` 的清單抄第二遍。
# 真正要守住的是「壞掉的題目會在啟動時被擋下,且訊息指得出是哪個檔案哪裡壞了」,
# 每種壞法一個代表就足以釘住這件事。
BROKEN_POSITIONS = [
    ("缺必填欄位", {"fen": OMIT}, "fen"),
    ("欄位型別不符", {"id": "1"}, "id"),
    # 出處由資料夾表達、起手方由 fen 表達;寫成欄位就是兩個真相來源,遲早互相矛盾。
    # 兩者都曾經是(或差點是)欄位,是最可能被手滑寫回去的兩個。
    ("出處寫成欄位", {"source": "橘中秘"}, "source"),
    ("起手方寫成欄位", {"side_to_move": "red"}, "side_to_move"),
]


@pytest.mark.parametrize(
    ("case", "overrides", "expected"),
    BROKEN_POSITIONS,
    ids=[case for case, _, _ in BROKEN_POSITIONS],
)
def test_a_broken_position_refuses_to_start(
    tmp_path: pathlib.Path, case: str, overrides: dict[str, Any], expected: str
) -> None:
    _write_position(tmp_path, "適情雅趣", 1, **overrides)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert expected in str(info.value), f"{case}:錯誤訊息須指出問題所在"


@pytest.mark.parametrize(
    ("case", "content"),
    [
        ("不是合法 JSON", "{ 這不是 JSON"),
        ("陣列裡不是物件", '["盡善克終"]'),
    ],
    ids=["不是合法 JSON", "陣列裡不是物件"],
)
def test_an_unparsable_position_file_refuses_to_start(
    tmp_path: pathlib.Path, case: str, content: str
) -> None:
    book = tmp_path / "適情雅趣"
    book.mkdir(parents=True)
    (book / "1-1.json").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "1-1.json" in str(info.value), f"{case}:錯誤訊息須指出是哪個檔案"


def test_position_file_outside_any_book_folder_refuses_to_start(
    tmp_path: pathlib.Path,
) -> None:
    """直接躺在題庫根目錄的題目沒有出處可言,屬佈局錯誤。"""
    _write_position(tmp_path, None, 1)

    with pytest.raises(ValueError) as info:
        PositionRepository(tmp_path).load()
    assert "1-1.json" in str(info.value)


def test_missing_corpus_directory_refuses_to_start(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError):
        PositionRepository(tmp_path / "不存在").load()


# --- 唯讀 ----------------------------------------------------------------


def _corpus_digest(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_loading_the_real_corpus_does_not_write_to_it() -> None:
    """載入是唯讀的。以**真實題庫**驗證,合成題庫的同名測試已刪(同一件事測兩遍)。"""
    before = _corpus_digest(DEFAULT_POSITIONS_DIR)

    _loaded(DEFAULT_POSITIONS_DIR).get(REAL_POSITION_ID)

    assert _corpus_digest(DEFAULT_POSITIONS_DIR) == before


# --- 擴充不需改程式或設定(6.3) ---------------------------------------


def test_a_brand_new_book_needs_no_registration(tmp_path: pathlib.Path) -> None:
    """新增一本書只是多一個資料夾,程式與設定都不動。"""
    _two_book_corpus(tmp_path)
    repository = _loaded(tmp_path)
    assert len(repository) == 3

    _write_position(tmp_path, "百局象棋譜", 301)
    reloaded = _loaded(tmp_path)

    assert len(reloaded) == 4
    assert reloaded.source_of(301) == "百局象棋譜"
