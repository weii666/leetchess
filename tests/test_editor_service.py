"""候選題目的權威驗證(對應 tasks 2.1、2.3,requirements 4.7、4.10)。

檔名取自 design 的 File Structure Plan —— 該表把 `service/editor.py` 與
`positions.validate_position()` 的測試放在同一個檔案裡。前半是後者,後半是
`EditorService`:schema 未過時不借引擎、引擎可載入性,以及「確認未能完成」不得被
當成判定。

`validate_position()` 存在的唯一理由是**規則只有一份**:收題工具要在寫入前判斷一題
合不合格,而那個判準就是題庫載入時用的那一個。它因此是 `_read_position()` 的薄包裝,
不是第二套欄位檢查 —— 題目 schema 的定義權在 `.kiro/steering/structure.md` 與
`service/positions.py`,收題工具**遵循**而不定義。

本檔的骨幹測試是 `test_the_candidate_verdict_matches_what_loading_would_say`:它不分別
斷言兩邊的行為,而是把同一份資料同時餵給 `validate_position()` 與
`PositionRepository.load()`,比對兩者的**結論與訊息**。抄一份規則進包裝裡的實作,會
在第一個沒抄到的細節上讓它變紅;分別斷言的寫法則會兩邊都綠。
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from service.config import DEFAULT_POSITIONS_DIR, Settings
from service.editor import EditorService, ValidationIssue
from service.engine.pool import EnginePool
from service.errors import (
    EngineTimeoutError,
    IllegalMoveSequenceError,
    InternalError,
    ServiceBusyError,
)
from service.positions import PositionRepository, validate_position
from service.types import Position, Side

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICE_DIR = PROJECT_ROOT / "service"
REAL_ENGINE = PROJECT_ROOT / "engine" / "pikafish"

requires_real_engine = pytest.mark.skipif(
    not REAL_ENGINE.is_file(), reason="真實引擎未安裝,請先執行 engine/fetch.sh"
)

#: 《適情雅趣》第二一局的起始局面,僅作為候選題目的填充內容。
SAMPLE_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"

#: 標記「這個欄位在候選題目裡不存在」,以區別於「欄位存在但值為 null」。
OMIT = object()

#: 出錯訊息中指路用的說法。收題工具的候選題目還沒有檔案,指不出「哪一檔第幾題」。
CANDIDATE_LABEL = "候選題目"


def _candidate(**overrides: Any) -> dict[str, Any]:
    """一份合格的候選題目;`overrides` 用來製造各種不合格的變體。

    **沒有 `source` 也沒有 `side_to_move`**:出處由資料夾表達、起手方由 fen 表達,
    兩者寫成欄位就會被當成未知欄位擋下 —— 那正是下面幾個案例要驗的事。
    """
    payload: dict[str, Any] = {
        "id": 21,
        "title": "盡善克終",
        "description": "適情雅趣 第二一局 盡善克終",
        "fen": SAMPLE_FEN,
        "difficulty": 3,
        "tags": ["連將殺"],
    }
    payload.update(overrides)
    for key in [key for key, value in payload.items() if value is OMIT]:
        del payload[key]
    return payload


#: 每一種「候選題目不合格」的代表性情形。第三欄是訊息裡必須出現的字樣 —— 維護者要
#: 修得動,訊息就得指出是哪個欄位出的事。
#:
#: 涵蓋面沿用 `test_positions.py` 的 `BROKEN_POSITIONS`:逐欄位窮舉只是把
#: `REQUIRED_FIELDS` 抄第二遍,每種壞法一個代表即足以釘住「壞題目擋得下來」。
BROKEN_CANDIDATES = [
    ("缺必填欄位", _candidate(fen=OMIT), "fen"),
    ("欄位型別不符", _candidate(id="21"), "id"),
    ("標籤不是字串陣列", _candidate(tags="連將殺"), "tags"),
    # 出處與起手方都曾經是(或差點是)欄位,是最可能被手滑寫回去的兩個。
    ("出處寫成未知欄位", _candidate(source="適情雅趣"), "source"),
    ("起手方寫成未知欄位", _candidate(side_to_move="red"), "side_to_move"),
    ("fen 判不出起手方", _candidate(fen=SAMPLE_FEN.replace(" w ", " g ")), "fen"),
    ("根本不是物件", "盡善克終", "物件"),
]


# --- 合格的候選題目 ------------------------------------------------------


def test_a_complete_candidate_yields_the_shared_domain_type() -> None:
    """合格的候選題目驗完就是一個 `Position`,而且是 `types.py` 的那一個。

    包裝若自行組裝另一份同名型別,`isinstance` 檢查會靜默失效(見
    `test_module_boundaries.py` 對 `positions.py` 的同一條要求)。
    """
    position = validate_position(_candidate())

    assert type(position) is Position
    assert position.id == 21
    assert position.title == "盡善克終"
    assert position.tags == ["連將殺"]


def test_the_side_to_move_still_comes_from_the_fen() -> None:
    """起手方由 FEN 推導這件事,在候選題目上與在題庫檔上是同一條規則。"""
    assert validate_position(_candidate()).side_to_move is Side.RED
    black_first = SAMPLE_FEN.replace(" w ", " b ")
    assert validate_position(_candidate(fen=black_first)).side_to_move is Side.BLACK


@pytest.mark.parametrize(
    ("case", "value"), [("缺欄位", OMIT), ("明寫 null", None)], ids=["缺欄位", "明寫 null"]
)
def test_max_dtm_is_optional_for_a_candidate(case: str, value: Any) -> None:
    """`max_dtm` 由 corpus-verification 回填,收題工具本就不寫它 —— 不得視為必填。"""
    assert validate_position(_candidate(max_dtm=value)).max_dtm is None, case


# --- 不合格的候選題目 ----------------------------------------------------


@pytest.mark.parametrize(
    ("case", "raw", "expected"),
    BROKEN_CANDIDATES,
    ids=[case for case, _, _ in BROKEN_CANDIDATES],
)
def test_a_broken_candidate_is_rejected_with_a_locatable_message(
    case: str, raw: Any, expected: str
) -> None:
    """不合格時沿用既有的錯誤型別(`ValueError`),訊息指得出問題所在。

    錯誤型別不改成 `errors.py` 的七種:那七種是對外 HTTP 契約,而「這一題不合格」
    在本 spec 裡是驗證端點的**結果**(200 加 issues),不是 HTTP 層的錯誤。
    """
    with pytest.raises(ValueError) as info:
        validate_position(raw)

    message = str(info.value)
    assert expected in message, f"{case}:錯誤訊息須指出問題所在"
    assert message.startswith(CANDIDATE_LABEL), (
        f"{case}:候選題目還沒有檔案,訊息須以「{CANDIDATE_LABEL}」指路"
    )


# --- 與題庫載入的判定一致 ------------------------------------------------


def _loading_verdict(root: pathlib.Path, raw: Any) -> str | None:
    """把候選題目當成題庫裡唯一的一題載入,回傳判定;`None` 代表接受。

    回傳值去掉了「哪一檔第幾題」那段前綴,只留下判定本身,才比得上候選題目那條
    路徑的訊息 —— 兩者的差別本來就只該是指路的說法。
    """
    path = root / "適情雅趣" / "21-21.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([raw], ensure_ascii=False), encoding="utf-8")

    try:
        PositionRepository(root).load()
    except ValueError as error:
        return str(error).removeprefix(f"題目檔案 {path} 第 1 題 ")
    return None


def _candidate_verdict(raw: Any) -> str | None:
    """同一份資料走候選題目那條路徑的判定,前綴同樣去掉。"""
    try:
        validate_position(raw)
    except ValueError as error:
        return str(error).removeprefix(f"{CANDIDATE_LABEL} ")
    return None


@pytest.mark.parametrize(
    ("case", "raw"),
    [("合格的候選題目", _candidate())] + [(case, raw) for case, raw, _ in BROKEN_CANDIDATES],
    ids=["合格的候選題目"] + [case for case, _, _ in BROKEN_CANDIDATES],
)
def test_the_candidate_verdict_matches_what_loading_would_say(
    tmp_path: pathlib.Path, case: str, raw: Any
) -> None:
    """同一份資料,兩條路徑的判定與說法必須一模一樣。

    這是本任務的核心斷言:收題工具在寫入前放行的題目,題庫載入時必定也收得下;
    收題工具擋下的,載入時必定也擋。比對到訊息層是刻意的 —— 只比對「有沒有錯」的
    話,一份自己抄了一遍規則、但訊息說法不同的實作照樣會綠。
    """
    assert _candidate_verdict(raw) == _loading_verdict(tmp_path, raw), case


# --- 唯讀 ----------------------------------------------------------------


def _corpus_digest(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_validating_a_candidate_does_not_touch_the_corpus() -> None:
    """`positions.py` 的「唯讀」契約不因這個新入口改變。

    候選題目來自收題工具、還不在題庫裡,驗證只是判定,寫檔是瀏覽器那一端的事
    (服務端不具備任何寫入題庫的能力)。
    """
    before = _corpus_digest(DEFAULT_POSITIONS_DIR)

    validate_position(_candidate())

    assert _corpus_digest(DEFAULT_POSITIONS_DIR) == before


# =========================================================================
# EditorService(tasks 2.3、requirements 4.7、4.10)
# =========================================================================
#
# 本半段釘死三件事:
#
# 1. **兩道檢查的順序**:schema 先、引擎後,且 schema 未過時**一次都不借引擎**。
#    這不是效能考量而是語意 —— 欄位都不對的題目沒有問到引擎的必要,而「零次借用」
#    是唯一能證明順序的斷言(用真實池只看得到「借完又還回去了」)。
# 2. **不搜尋、不判勝負**(4.10):替身的 `best_move()` 一旦被呼叫就直接讓測試失敗。
# 3. **確認未能完成不得變成判定**(4.9):忙碌、逾時、引擎崩潰一律往上拋,絕不
#    收斂成「這題不合格」或「這題合格」。


#: 引擎替身的固定回應。合法著法的**內容**與可載入性判定無關 —— 取得任何結果即
#: 視為可載入(見 design 的 `service/editor.py`),空清單同樣是結果。
STUB_LEGAL_MOVES = ["e8f9", "e9f9"]

#: 刻意取一個不像預設值的數字,使「逾時確實取自設定」看得出來。
STUB_SEARCH_TIMEOUT = 7.25


class _StubEngine:
    """記錄每一次查詢的引擎替身。

    以行程內的替身而非 `tests/fakes/fake_engine.py` 那個子行程,理由是本節要驗的是
    **例外的對應關係**:引擎載不進去、逾時、崩潰各自該變成什麼。子行程替身給不出
    這些分支(它對任何 FEN 都照樣回話),而真實引擎更不可能應要求崩潰。
    """

    def __init__(
        self,
        error: Exception | None = None,
        healthy: bool = True,
        moves: list[str] | None = None,
    ) -> None:
        self._error = error
        self._healthy = healthy
        self._moves = STUB_LEGAL_MOVES if moves is None else moves
        #: 每次 `legal_moves` 收到的 (fen, moves, timeout)。
        self.queries: list[tuple[str, list[str], float]] = []

    def legal_moves(self, fen: str, moves: list[str], timeout: float) -> list[str]:
        self.queries.append((fen, list(moves), timeout))
        if self._error is not None:
            raise self._error
        return list(self._moves)

    def best_move(self, fen: str, moves: list[str], nodes: int, timeout: float):
        # 4.10:驗證不判斷紅先必勝,因此連搜尋的入口都不該被碰到。
        raise AssertionError("驗證候選題目不得執行搜尋(4.10)")

    def is_healthy(self) -> bool:
        return self._healthy


class _SpyPool:
    """數借用次數的引擎池替身。`borrows` 為 0 即證明引擎完全沒被動用。"""

    def __init__(self, engine: _StubEngine, error: Exception | None = None) -> None:
        self.engine = engine
        self.borrows = 0
        self._error = error

    @contextmanager
    def acquire(self) -> Iterator[_StubEngine]:
        self.borrows += 1
        if self._error is not None:
            raise self._error
        yield self.engine


def _editor_settings(**overrides: Any) -> Settings:
    """`EditorService` 只讀 `search_timeout`,其餘欄位取能通過總預算檢查的值。"""
    values: dict[str, Any] = {
        "pool_size": 1,
        "acquire_timeout": 1.0,
        "search_timeout": STUB_SEARCH_TIMEOUT,
        "stop_grace_period": 0.5,
        "total_time_budget": 30.0,
        "search_nodes": 1_000,
        "positions_dir": DEFAULT_POSITIONS_DIR,
        "engine_path": REAL_ENGINE,
    }
    values.update(overrides)
    return Settings(**values)


def _service_with(
    engine: _StubEngine | None = None, pool_error: Exception | None = None
) -> tuple[EditorService, _SpyPool]:
    pool = _SpyPool(engine if engine is not None else _StubEngine(), pool_error)
    return EditorService(pool, _editor_settings()), pool


# --- 合格的候選題目 ------------------------------------------------------


def test_a_valid_candidate_yields_no_issues() -> None:
    """齊全的欄位加上引擎載得進去的 FEN = 空清單(完成狀態的第一條)。"""
    service, pool = _service_with()

    assert service.validate(_candidate()) == []
    assert pool.borrows == 1, "合格的候選題目要問到引擎才算數"


def test_loadability_asks_the_engine_for_this_fen_with_no_moves() -> None:
    """可載入性 = 對**該 FEN** 與**空走法序列**取合法著法,逾時取自設定。

    走法序列非空的話問到的就是別的局面;而逾時若寫死,設定就管不到這條路徑。
    """
    engine = _StubEngine()
    service, _ = _service_with(engine)

    service.validate(_candidate())

    assert engine.queries == [(SAMPLE_FEN, [], STUB_SEARCH_TIMEOUT)]


def test_an_empty_move_list_is_still_a_result() -> None:
    """引擎回報無著可走(困斃或空盤)仍是「載得進去」。

    合格與否只看載不載得進去,不看局面的棋理 —— 是否紅先必勝屬 corpus-verification
    (4.10),本服務不判。
    """
    service, _ = _service_with(_StubEngine(moves=[]))

    assert service.validate(_candidate()) == []


# --- schema 未通過:一次都不借引擎 --------------------------------------


@pytest.mark.parametrize(
    ("case", "raw"),
    [(case, raw) for case, raw, _ in BROKEN_CANDIDATES],
    ids=[case for case, _, _ in BROKEN_CANDIDATES],
)
def test_a_schema_failure_never_borrows_an_engine(case: str, raw: Any) -> None:
    """兩道檢查的順序:schema 未通過時引擎的借用次數必須是 0(完成狀態的第三條)。

    以「借用次數」而非「有沒有查詢」斷言是刻意的 —— 借了又沒問的實作照樣佔用池的
    容量,而池是本服務唯一的併發閘門。
    """
    service, pool = _service_with()

    issues = service.validate(raw)

    assert pool.borrows == 0, f"{case}:schema 未通過時不得借引擎"
    assert len(issues) == 1, f"{case}:一次 schema 失敗對應一項未通過"


@pytest.mark.parametrize(
    ("case", "raw"),
    [(case, raw) for case, raw, _ in BROKEN_CANDIDATES],
    ids=[case for case, _, _ in BROKEN_CANDIDATES],
)
def test_a_schema_issue_carries_the_verdict_verbatim(case: str, raw: Any) -> None:
    """issue 的訊息就是 schema 那條路徑的說法,一字不改。

    重寫訊息等於在收題工具裡養出第二套說法,而題目 schema 的說法只該有一份
    (design 的 Implementation Notes)。
    """
    service, _ = _service_with()

    with pytest.raises(ValueError) as info:
        validate_position(raw)

    assert service.validate(raw)[0].message == str(info.value), case


#: 訊息與欄位歸屬的對應。**這是字面比對**:欄位名由 `positions.py` 的訊息推得,
#: 推不出來時為 `None`(design 的 Implementation Notes)。
FIELD_ATTRIBUTION = [
    ("缺必填欄位", _candidate(fen=OMIT), "fen"),
    ("欄位型別不符", _candidate(id="21"), "id"),
    ("標籤不是字串陣列", _candidate(tags="連將殺"), "tags"),
    ("fen 判不出起手方", _candidate(fen=SAMPLE_FEN.replace(" w ", " g ")), "fen"),
    # 未知欄位不是題目 schema 的欄位,歸不到任何一格表單,故為 None。
    ("出處寫成未知欄位", _candidate(source="適情雅趣"), None),
    ("根本不是物件", "盡善克終", None),
]


@pytest.mark.parametrize(
    ("case", "raw", "expected"),
    FIELD_ATTRIBUTION,
    ids=[case for case, _, _ in FIELD_ATTRIBUTION],
)
def test_a_schema_issue_points_at_the_field_its_message_names(
    case: str, raw: Any, expected: str | None
) -> None:
    """未通過項目要定位得到欄位,推不出欄位時明確為 `None` 而非亂猜一個。"""
    service, _ = _service_with()

    assert service.validate(raw)[0].field == expected, case


def test_a_value_that_looks_like_a_field_name_does_not_confuse_attribution() -> None:
    """欄位歸屬取自訊息的**結構**,不是在整句話裡撈欄位名。

    `id` 的值恰好是 `"tags"` 時,訊息裡兩個欄位名都在;歸屬必須是出問題的那一個。
    """
    service, _ = _service_with()

    assert service.validate(_candidate(id="tags"))[0].field == "id"


# --- 引擎載入不進去 ------------------------------------------------------


#: 引擎「沒把這個局面載進去」的兩種表現。
#:
#: - `IllegalMoveSequenceError`:走法序列是空的,序列不可能走不出來 —— 引擎最後
#:   停在別的局面,只能是它沒接受這個 FEN
#: - `InternalError` 且引擎仍健康:欄位少、fullmove 不是數字之類,查詢在引擎層就
#:   解不出來(實測真實引擎:只有兩欄、fullmove 非數字、fullmove 為 0 皆如此)
UNLOADABLE_FAILURES = [
    ("引擎停在別的局面", IllegalMoveSequenceError()),
    ("查詢在引擎層解不出來", InternalError()),
]


@pytest.mark.parametrize(
    ("case", "error"),
    UNLOADABLE_FAILURES,
    ids=[case for case, _ in UNLOADABLE_FAILURES],
)
def test_a_fen_the_engine_cannot_load_becomes_an_issue(
    case: str, error: Exception
) -> None:
    """載不進去的 FEN 得到對應項目,且定位到 `fen`(完成狀態的第二條)。"""
    service, _ = _service_with(_StubEngine(error=error))

    issues = service.validate(_candidate())

    assert len(issues) == 1, case
    assert issues[0].field == "fen", case
    assert issues[0].message, f"{case}:訊息不得為空"


def test_an_unloadable_fen_is_a_verdict_not_an_exception() -> None:
    """「這題不合格」以回傳值表達,不以例外表達(design 的 Invariants)。"""
    service, _ = _service_with(_StubEngine(error=IllegalMoveSequenceError()))

    assert isinstance(service.validate(_candidate())[0], ValidationIssue)


# --- 確認未能完成:不得收斂成判定(4.9) --------------------------------


#: 服務失敗與其發生的位置。三者都必須原樣往上拋 —— 呼叫端要據此回 503/504/500,
#: 而不是回一份 `issues`。
SERVICE_FAILURES = [
    ("池滿", ServiceBusyError, None, None),
    ("引擎逾時", EngineTimeoutError, EngineTimeoutError(), True),
    ("引擎崩潰", InternalError, InternalError(), False),
]


@pytest.mark.parametrize(
    ("case", "expected", "engine_error", "healthy"),
    SERVICE_FAILURES,
    ids=[case for case, _, _, _ in SERVICE_FAILURES],
)
def test_a_confirmation_that_could_not_complete_is_raised_not_reported(
    case: str,
    expected: type[Exception],
    engine_error: Exception | None,
    healthy: bool | None,
) -> None:
    """4.9:確認未能完成時不得給出任何判定 —— 不論是合格還是不合格。

    崩潰的引擎(`is_healthy()` 為假)與「引擎解不出這個 FEN」的差別就在健康狀態:
    前者是引擎壞了,後者是 FEN 壞了。把前者說成「FEN 不合法」等於誣賴使用者的題目,
    而那一題其實可能完全沒問題。
    """
    if engine_error is None:
        service, _ = _service_with(pool_error=expected())
    else:
        service, _ = _service_with(_StubEngine(error=engine_error, healthy=healthy))

    with pytest.raises(expected):
        service.validate(_candidate())


def test_a_service_failure_never_happens_before_the_schema_check() -> None:
    """池整個壞掉時,schema 不合格的候選題目仍得到判定 —— 它根本不必問引擎。"""
    service, pool = _service_with(pool_error=ServiceBusyError())

    assert len(service.validate(_candidate(fen=OMIT))) == 1
    assert pool.borrows == 0


# --- 唯讀 ----------------------------------------------------------------


def test_the_editor_service_does_not_touch_the_corpus() -> None:
    """服務端**不具備**寫入題庫的能力,驗證只是判定(design 的 Boundary Commitments)。"""
    service, _ = _service_with()
    before = _corpus_digest(DEFAULT_POSITIONS_DIR)

    service.validate(_candidate())
    service.validate(_candidate(fen=OMIT))

    assert _corpus_digest(DEFAULT_POSITIONS_DIR) == before


# --- 依賴方向(design 的 Boundary Commitments) --------------------------


#: `editor.py` 與 `game.py` 同層,允許的匯入範圍相同;兩者**不得互相匯入**。
EDITOR_ALLOWED_SERVICE_MODULES = {
    "service.types",
    "service.errors",
    "service.config",
    "service.positions",
    "service.engine",
}


def _imported_module_names(source_path: pathlib.Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = "." * node.level
                names.add(f"{prefix}{node.module or ''}")
                names.update(f"{prefix}{alias.name}" for alias in node.names)
            elif node.module:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_editor_imports_only_modules_to_its_left() -> None:
    """`editor.py` 不得 import `game.py`、`models.py` 或 `main.py`。

    與 `game.py` 同層的模組互相匯入會讓兩條服務路徑綁在一起,而它們本來各自獨立:
    對局不該因為收題工具的存在而多一份依賴。
    """
    path = SERVICE_DIR / "editor.py"
    assert path.is_file(), f"{path} 必須存在"
    for name in _imported_module_names(path):
        assert not name.startswith("."), f"editor.py 不得使用相對匯入 {name}"
        if not name.startswith("service"):
            continue
        root = ".".join(name.split(".")[:2])
        assert root in EDITOR_ALLOWED_SERVICE_MODULES, f"editor.py 不得 import {name}"


# --- 真實引擎:可載入性的判準對得上真實引擎 ------------------------------


#: 真實引擎確實載不進去的 FEN(實測結果,見 tasks 2.3 的探測):兩者都在引擎層
#: 就解不出來,而引擎本身毫髮無傷。**通過 schema** 是前提 —— 走子方那一欄仍是 `w`。
REAL_UNLOADABLE_FENS = [
    ("只有兩欄", SAMPLE_FEN.split(" w ")[0] + " w"),
    ("fullmove 不是數字", SAMPLE_FEN.rsplit(" ", 1)[0] + " x"),
]


@requires_real_engine
def test_real_engine_agrees_with_the_loadability_verdict() -> None:
    """替身給的是對應關係,真實引擎給的是「那些對應關係真的會發生」。

    同一個池連跑三種輸入,只付一次 NNUE 載入的代價:合法 FEN 得到空清單,兩個真實
    引擎載不進去的 FEN 各自得到定位到 `fen` 的項目。
    """
    pool = EnginePool(size=1, acquire_timeout=5.0, engine_path=REAL_ENGINE)
    service = EditorService(pool, _editor_settings(search_timeout=15.0))
    try:
        assert service.validate(_candidate()) == []

        for case, fen in REAL_UNLOADABLE_FENS:
            issues = service.validate(_candidate(fen=fen))
            assert [issue.field for issue in issues] == ["fen"], case
    finally:
        pool.shutdown()


@requires_real_engine
def test_the_engine_is_returned_to_the_pool_on_every_path() -> None:
    """容量只有一格時,載不進去的 FEN 之後仍要能驗下一題。

    借用以情境管理器進行,漏還的症狀要等到池被耗盡才浮現 —— 這裡讓它當場浮現。
    """
    pool = EnginePool(size=1, acquire_timeout=5.0, engine_path=REAL_ENGINE)
    service = EditorService(pool, _editor_settings(search_timeout=15.0))
    try:
        service.validate(_candidate(fen=REAL_UNLOADABLE_FENS[0][1]))

        assert (pool.available_count, pool.borrowed_count) == (1, 0)
        assert service.validate(_candidate()) == []
    finally:
        pool.shutdown()
