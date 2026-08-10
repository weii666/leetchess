"""驗證端點的整合測試(tasks 2.4;requirements 1.3、4.7、4.8、9.1、9.4)。

檔名與職責取自 design 的 File Structure Plan ——「驗證端點,含 FEN 字元把關的拒絕
路徑」。本檔問的是**組裝**而非判定:判定邏輯已由 `test_editor_service.py` 覆蓋
(schema 未過時不借引擎、可載入性、確認未能完成一律往上拋),此處只釘 design 的
Testing Strategy → Integration Tests 那四條:

1. **字元把關攔在路由函式之前**(9.1、9.4):帶控制字元的 FEN 回 400
   `INVALID_MOVE_FORMAT`,且**引擎一次都沒被借用**,那串內容也從未出現在引擎收到的
   任何一道指令裡。這是把驗證器掛在請求模型上、而非寫在路由函式體內的全部意義。
2. **驗證未通過是「結果」而非「錯誤」**(4.7、4.8):不合格的候選題目回 200 加
   `issues`,回應裡連 `code` 欄位都沒有 —— 既有的七種錯誤類別碼不為本功能擴充第八種。
3. **真正的服務失敗仍走既有的錯誤形狀**:池滿時回 503 `SERVICE_BUSY`,而**不是**
   `valid: true`。忙碌的服務絕不能長得像一份壞掉的題目(4.9)。
4. **既有的四個端點行為不變**(1.3)。

## 為什麼用替身引擎

同 `test_main.py`:組裝的語意與引擎實際算什麼無關,而真實引擎每個進程常駐一份 51MB
NNUE。此處另有一樣只有替身給得起的東西 —— `tests/fakes/fake_engine.py` 的**指令
記錄**:9.1 要的是「不將該內容送往引擎」,而那份記錄正是「到底送了什麼給引擎」的
直接證據,真實引擎給不出來。

## 借用次數為什麼要另外數

「引擎沒被查詢過」比「引擎沒被借用過」弱:借了又沒問的實作照樣吃掉池裡的一格,而池
是本服務唯一的併發閘門。`_CountingPool` 因此包在**真實池外面**只數次數,行為一字不
改 —— 不換掉協作者,只多一個計數器,斷言到的仍是這條路徑真正會走的池。

`test_editor_service.py` 的行程內替身(`_StubEngine` / `_SpyPool`)刻意不搬過來:它
存在的理由是製造引擎的各種例外分支,而那些分支的對應關係已在該檔釘死。本檔要的是
「端點與掛鉤真的接起來了」,把真實池換掉反而會讓組裝本身沒被驗到。

## 候選題目的樣本共用 `test_editor_service.py`

「一份合格的候選題目長什麼樣」只該有一份定義。抄第二份之後,題目 schema 一改就會有
一邊靜靜地繼續綠 —— 而兩邊本來就必須是同一份資料。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from service.config import Settings
from service.editor import EditorService
from service.errors import ErrorCode, IllegalMoveSequenceError
from service.main import create_app
from test_editor_service import OMIT, SAMPLE_FEN, _candidate

#: 驗證端點的位址(design 的 API Contract)。
VALIDATE_PATH = "/api/editor/validate"

#: 測試題庫的題號與書名。啟動掛鉤要載得起題庫,端點本身不讀它。
PUZZLE_ID = 1
PUZZLE_SOURCE = "測試書"

#: 替身的合法著法(`tests/fakes/fake_engine.py` 的 `PERFT_LINES`)。
FAKE_LEGAL_MOVES = ["e8f9", "e9f9"]

#: 替身回應是即時的,逾時與節點數取小值使測試快。
FAKE_SEARCH_TIMEOUT = 1.0
FAKE_ACQUIRE_TIMEOUT = 0.4
FAKE_NODES = 1_000

#: 七種對外錯誤類別碼(`service/errors.py` 為唯一來源)。
ERROR_CODES = frozenset(code.value for code in ErrorCode)


# --- 測試題庫與應用 -----------------------------------------------------


def _write_position(root: pathlib.Path) -> None:
    """在 `root/測試書/` 底下寫一題,使啟動掛鉤的題庫掃描有東西可載。"""
    payload: dict[str, Any] = {
        "id": PUZZLE_ID,
        "title": "盡善克終",
        "description": "測試書 第一局 盡善克終",
        "fen": SAMPLE_FEN,
        "difficulty": 3,
        "tags": ["雙馬", "連將殺"],
        "max_dtm": 16,
    }
    folder = root / PUZZLE_SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{PUZZLE_ID}-{PUZZLE_ID}.json").write_text(
        json.dumps([payload], ensure_ascii=False), encoding="utf-8"
    )


def _settings(
    engine_path: pathlib.Path, positions_dir: pathlib.Path, pool_size: int
) -> Settings:
    return Settings(
        pool_size=pool_size,
        acquire_timeout=FAKE_ACQUIRE_TIMEOUT,
        search_timeout=FAKE_SEARCH_TIMEOUT,
        stop_grace_period=0.5,
        total_time_budget=30.0,
        search_nodes=FAKE_NODES,
        positions_dir=positions_dir,
        engine_path=engine_path,
    )


class _CountingPool:
    """包在真實引擎池外面的計數代理:只數借用次數,不改變任何行為。

    `borrows` 為 0 即證明「借一個引擎」那一步一次都沒執行到 —— 那正是字元把關攔在
    路由函式之前的**可觀測後果**,而不是讀程式碼相信的事。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.borrows = 0

    @contextmanager
    def acquire(self) -> Iterator[Any]:
        self.borrows += 1
        with self._inner.acquire() as engine:
            yield engine


class _UnloadableEngine:
    """借得到、但載不進任何局面的引擎。

    這條分支替身進程給不出來:`tests/fakes/fake_engine.py` 收到畸形 FEN 時會在自己
    的 `d` 輸出上先崩掉,而崩潰是「確認未能完成」——語意與「這個 FEN 載不進去」剛好
    相反,拿它來測 4.8 會測到反面。因此只換掉**最內層那一個回答**,路由、請求模型
    與驗證服務都還是真的。
    """

    def legal_moves(self, fen: str, moves: list[str], timeout: float) -> list[str]:
        del fen, moves, timeout
        raise IllegalMoveSequenceError()

    def is_healthy(self) -> bool:
        # 引擎本身毫髮無傷:壞的是 FEN,不是引擎。兩者的分野正是 4.8 與 4.9 的分野。
        return True


class _UnloadablePool:
    """恆借出 `_UnloadableEngine` 的引擎池替身。"""

    @contextmanager
    def acquire(self) -> Iterator[_UnloadableEngine]:
        yield _UnloadableEngine()


@dataclass
class _Harness:
    """一個已走完啟動掛鉤的服務,加上它的引擎替身。

    替身一併帶著,是因為 9.1 要斷言的是「**送往引擎**的內容」——那份指令記錄只有
    替身給得出來。
    """

    client: TestClient
    engine: Any

    def validate(self, position: Any) -> Any:
        """對驗證端點送出一份候選題目,回傳 `TestClient` 的回應。"""
        return self.client.post(VALIDATE_PATH, json={"position": position})

    def count_borrows(self) -> _CountingPool:
        """把驗證服務的引擎池換成計數代理,回傳該代理。

        換的只是**外面那一層**:代理仍向掛鉤建好的真實池借用,因此端點走的路徑
        一步不少。設定同樣取自 app 狀態,不另造一份。
        """
        pool = _CountingPool(self.client.app.state.pool)
        self.client.app.state.editor = EditorService(
            pool, self.client.app.state.settings
        )
        return pool

    def make_the_engine_unloadable(self) -> None:
        """把驗證服務接到一個「載不進任何局面」的引擎上(理由見 `_UnloadableEngine`)。"""
        self.client.app.state.editor = EditorService(
            _UnloadablePool(), self.client.app.state.settings
        )

    def engine_commands(self) -> list[str]:
        """引擎替身至今收到的所有指令,依送達順序。"""
        return self.engine.commands()


@pytest.fixture
def make_service(tmp_path: pathlib.Path, fake_engine):
    """回傳工廠:`make_service(mode=..., pool_size=...)` 給出一個已啟動的服務。

    以 `TestClient` 的情境進出驅動啟動與關閉掛鉤 —— 驗證服務是否真的由掛鉤建起來,
    正是本任務要驗的一半,繞過掛鉤手工組裝就什麼都沒驗到。
    """
    harnesses: list[_Harness] = []
    counter = {"n": 0}

    def make(mode: str = "normal", *, pool_size: int = 1) -> _Harness:
        counter["n"] += 1
        root = tmp_path / f"corpus_{counter['n']}"
        _write_position(root)
        engine = fake_engine(mode)
        app = create_app(_settings(engine.path, root, pool_size))
        client = TestClient(app)
        client.__enter__()
        harness = _Harness(client=client, engine=engine)
        harnesses.append(harness)
        return harness

    yield make

    for harness in harnesses:
        harness.client.__exit__(None, None, None)


# --- 字元把關攔在路由函式之前(9.1、9.4;Integration Test 1)-------------


#: 一定要被擋在引擎之外的 FEN。控制字元是 9.1 的重點:引擎協定行導向,而
#: `_position_command()` 是裸的字串插值,一個換行就能讓一行指令變成兩行,第二行的
#: 內容由送出者決定 —— `quit` 只是最容易看懂的那個。
POISONED_FENS = [
    ("換行", f"{SAMPLE_FEN}\nquit"),
    ("歸位字元", f"{SAMPLE_FEN}\rquit"),
    ("tab", f"{SAMPLE_FEN}\tquit"),
    ("空位元組", f"{SAMPLE_FEN}\x00quit"),
]


@pytest.mark.parametrize(
    ("case", "fen"), POISONED_FENS, ids=[case for case, _ in POISONED_FENS]
)
def test_a_fen_with_control_characters_never_reaches_the_engine(
    make_service, case: str, fen: str
) -> None:
    """9.1、9.4:回 400 `INVALID_MOVE_FORMAT`,且引擎**一次都沒被借用**。

    借用次數為 0 是「攔截發生在路由函式之前」的直接證據:若把關改寫在函式本體內,
    借引擎那行極可能已經先跑過(`main.py` 的模組說明)。指令記錄則另外證明第二件
    事 —— 那串內容連送都沒被送往引擎(9.1 的後半句)。
    """
    service = make_service()
    pool = service.count_borrows()

    response = service.validate(_candidate(fen=fen))

    assert response.status_code == 400, case
    assert response.json()["code"] == "INVALID_MOVE_FORMAT", case
    assert pool.borrows == 0, f"{case}:不合格的 FEN 不得吃掉併發閘門的名額"
    assert not any("quit" in command for command in service.engine_commands()), (
        f"{case}:被擋下的內容不得出現在送往引擎的任何一道指令裡"
    )


@pytest.mark.parametrize(
    ("case", "fen"),
    [
        ("非法字元", f"{SAMPLE_FEN};go infinite"),
        ("超出長度上限", "1" * 300),
    ],
    ids=["非法字元", "超出長度上限"],
)
def test_the_whole_character_guard_is_wired_to_this_endpoint(
    make_service, case: str, fen: str
) -> None:
    """9.2、9.3 走的是同一個把關與同一個類別碼,不是只有控制字元被接上。

    三種拒絕的**判準**由 `test_models.py` 釘死,此處只證明它們確實掛在這個端點上。
    """
    service = make_service()
    pool = service.count_borrows()

    response = service.validate(_candidate(fen=fen))

    assert response.status_code == 400, case
    assert response.json()["code"] == "INVALID_MOVE_FORMAT", case
    assert pool.borrows == 0, case


def test_the_character_guard_is_not_a_schema_check(make_service) -> None:
    """`fen` 不是字串時**不歸字元把關管**,交由題目 schema 以它的說法回報。

    這是 tasks 2.2 的 Implementation Note:把關刻意放行非字串,使用者才會看到
    「欄位型別不對」而不是一句莫名的格式不合法。`["a\\nquit"]` 是最刁鑽的形狀 ——
    它裡面真的有換行,但那份內容永遠走不到引擎:schema 沒過就不借引擎。
    """
    service = make_service()
    pool = service.count_borrows()

    response = service.validate(_candidate(fen=["a\nquit"]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert payload["issues"][0]["field"] == "fen"
    assert pool.borrows == 0


# --- 驗證未通過是「結果」不是「錯誤」(4.7、4.8;Integration Test 2)-----


def test_a_valid_candidate_is_reported_as_valid(make_service) -> None:
    """4.7:欄位齊全且引擎載得進去的候選題目回 200 加 `valid: true`、空 `issues`。

    這條同時證明啟動掛鉤真的建起了驗證服務並接上引擎池 —— 少了任何一環,回應不會
    是 200。
    """
    service = make_service()

    response = service.validate(_candidate())

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


def test_a_valid_candidate_really_asks_the_engine(make_service) -> None:
    """合格的判定不是憑空給的:引擎確實被借了一次,並被問了這個局面的合法著法。

    少了這條,一個永遠回 `valid: true` 的空實作也會綠。
    """
    service = make_service()
    pool = service.count_borrows()

    assert service.validate(_candidate()).json()["valid"] is True

    assert pool.borrows == 1
    commands = service.engine_commands()
    assert f"position fen {SAMPLE_FEN}" in commands
    # 只列合法著法,不搜尋、不判紅先必勝(4.10)。
    assert "go perft 1" in commands
    assert not any(command.startswith("go nodes") for command in commands)


#: 不合格的候選題目與它該被指到的欄位。三種壞法各取一個代表 —— 逐欄位窮舉只是把
#: 題目 schema 抄第二遍,而 schema 的判定已由 `test_editor_service.py` 全面覆蓋。
BROKEN_CANDIDATES = [
    ("缺必填欄位", _candidate(fen=OMIT), "fen"),
    ("欄位型別不符", _candidate(id="21"), "id"),
    ("出處寫成未知欄位", _candidate(source="適情雅趣"), None),
]


@pytest.mark.parametrize(
    ("case", "raw", "field"),
    BROKEN_CANDIDATES,
    ids=[case for case, _, _ in BROKEN_CANDIDATES],
)
def test_a_failing_candidate_is_a_two_hundred_result_not_an_error(
    make_service, case: str, raw: Any, field: str | None
) -> None:
    """4.8:不合格的候選題目回 **200** 加 `issues`,每一項盡可能定位到欄位。

    這是 design 的 API Contract 明列的取捨:驗證未通過是**判定**,把它說成 HTTP
    錯誤就得新增第八種錯誤類別碼,而那是 design 層級的決定。
    """
    response = make_service().validate(raw)

    assert response.status_code == 200, case
    payload = response.json()
    assert payload["valid"] is False, case
    assert len(payload["issues"]) == 1, case
    assert payload["issues"][0]["field"] == field, case
    assert payload["issues"][0]["message"], f"{case}:訊息不得為空,維護者要看得懂"


def test_a_fen_the_engine_cannot_load_is_a_result_too(make_service) -> None:
    """4.8:引擎載不進去的 FEN 同樣是 200 的判定,並定位到 `fen`。

    這一項與 schema 未通過的那幾項走的是**不同的來源**(一個來自題庫模組,一個來自
    引擎),但對外必須是同一個形狀 —— 收題頁只有一套呈現,分不了來源也不該分。
    """
    service = make_service()
    service.make_the_engine_unloadable()

    response = service.validate(_candidate())

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert [issue["field"] for issue in payload["issues"]] == ["fen"]
    assert payload["issues"][0]["message"], "訊息不得為空,維護者要知道該改哪裡"
    assert "code" not in payload


def test_a_failing_result_carries_no_error_code(make_service) -> None:
    """回應形狀是判定,不是 `{code, message}` —— 七種類別碼一種都沒被動用。

    形狀若混進 `code`,前端就會開始拿錯誤處理那條路去接判定,而兩者的處置完全不同
    (判定要定位到欄位,錯誤要告訴使用者「確認未能完成」)。
    """
    payload = make_service().validate(_candidate(fen=OMIT)).json()

    assert set(payload) == {"valid", "issues"}
    assert set(payload["issues"][0]) == {"field", "message"}


def test_a_candidate_that_is_not_even_an_object_is_rejected_by_the_request_shape(
    make_service,
) -> None:
    """`position` 不是物件是**請求形狀**的問題,走既有的結構性驗證(400)。

    這與「候選題目不合格」不同層:前者連一份題目都沒送到,後者是送到了但不合格。
    """
    response = make_service().validate("盡善克終")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_MOVE_FORMAT"


# --- 真正的服務失敗仍走既有的錯誤形狀(Integration Test 3)--------------


def test_pool_exhaustion_is_reported_as_service_busy_not_as_a_verdict(
    make_service,
) -> None:
    """4.9:池滿時回既有的 503 忙碌形狀,**絕不是** `valid: true`。

    這是本任務最要緊的一條分界。把服務失敗收斂成判定,兩種都是災難:說 `valid: true`
    會讓一份沒驗過的題目直接寫進題庫;說 `valid: false` 則是誣賴一份可能完全沒問題
    的題目,還把責任推給維護者。
    """
    service = make_service(pool_size=1)

    with service.client.app.state.pool.acquire():
        response = service.validate(_candidate())

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_BUSY"
    assert set(payload) == {"code", "message"}
    assert "valid" not in payload


def test_an_engine_crash_keeps_the_existing_error_envelope(make_service) -> None:
    """引擎崩潰同樣是「確認未能完成」:既有的類別碼與 `{code, message}` 形狀不變。

    模式 `exit_on_go` 讓引擎在收到 `go` 時直接消失 —— 那時服務根本沒有判定可言。
    """
    service = make_service("exit_on_go")

    response = service.validate(_candidate())

    assert response.status_code >= 500
    payload = response.json()
    assert set(payload) == {"code", "message"}
    assert payload["code"] in ERROR_CODES
    assert "valid" not in payload


def test_a_service_failure_does_not_leak_the_engine_output(make_service) -> None:
    """5.4 對這個端點一樣管用:回應不帶引擎原始輸出,也不帶內部路徑。"""
    service = make_service("exit_on_go")

    text = service.validate(_candidate()).text

    for marker in ("bestmove", "uciok", "readyok", "Fen:", "Traceback", ".py"):
        assert marker not in text, f"回應帶了內部細節「{marker}」:{text}"


# --- 既有的四個端點行為不變(1.3;Integration Test 4)-------------------


def test_the_four_existing_endpoints_still_answer_as_before(make_service) -> None:
    """1.3:新增端點與驗證服務之後,既有四個端點的回應一字不變。

    以**正常回應**當探針而非方法不符:405 只證明路由還在,證明不了回的內容沒變,
    而本任務動的正是同一個 app 的組裝與同一個啟動掛鉤。
    """
    client = make_service().client

    catalog = client.get("/api/catalog")
    assert catalog.status_code == 200
    assert [entry["id"] for entry in catalog.json()["positions"]] == [PUZZLE_ID]
    assert catalog.json()["positions"][0]["source"] == PUZZLE_SOURCE

    position = client.get(f"/api/positions/{PUZZLE_ID}")
    assert position.status_code == 200
    assert position.json()["fen"] == SAMPLE_FEN
    assert position.json()["state"]["legal_moves"] == FAKE_LEGAL_MOVES

    state = client.post("/api/state", json={"position_id": PUZZLE_ID, "moves": []})
    assert state.status_code == 200
    assert state.json()["legal_moves"] == FAKE_LEGAL_MOVES

    # 起手方為紅,黑方應手端點照舊拒絕(1.5)。
    black_move = client.post(
        "/api/black-move", json={"position_id": PUZZLE_ID, "moves": []}
    )
    assert black_move.status_code == 409
    assert black_move.json()["code"] == "WRONG_SIDE_TO_MOVE"


def test_the_catalog_still_answers_with_the_engine_pool_shut_down(
    make_service,
) -> None:
    """problem-browser 5.2 不因本任務改變:題庫列表仍不碰引擎池。

    新增的驗證服務與索引共用同一個 app 狀態,「順手」把池接進索引那條路徑是真實的
    風險,這條在它發生的當下就會紅。
    """
    service = make_service()
    service.client.app.state.pool.shutdown()

    response = service.client.get("/api/catalog")

    assert response.status_code == 200
    assert len(response.json()["positions"]) == 1


def test_the_validate_endpoint_is_owned_by_the_router_not_the_static_mount(
    make_service,
) -> None:
    """新路徑落在 `/api` 底下,因此仍由路由層接手 —— 方法不符回 405 而非 404。

    `_WebFiles` 把整個 `/api` 前綴對靜態檔遮蔽,新增端點不需要為此改動任何一行;
    這條是那個保證仍然成立的證據。
    """
    response = make_service().client.get(VALIDATE_PATH)

    assert response.status_code == 405
