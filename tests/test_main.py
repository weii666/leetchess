"""應用組裝與四個端點的測試(tasks 4.2、requirements 1.1、1.4、6.1)。

本檔釘死的是**組裝**而非判定邏輯 —— 對局判定已由 `test_game_service.py` 覆蓋,
此處只問四件事:

1. **四個端點都答得出來**,且回應形狀就是 `models.py` 的契約(1.1、1.4、6.1)。
2. **啟動掛鉤建起題庫索引與引擎池,關閉掛鉤把引擎進程全部收掉** —— 殘留的引擎
   進程每個常駐一份 51MB NNUE,關不掉等於每次重啟都漏一批記憶體。此處以作業系統
   層級的子進程清單斷言,而非問池自己「關好了嗎」。
3. **併發閘門是引擎池,不是 HTTP 的 threadpool**(design「併發閘門的位置」)。
   threadpool 容量若不大於池容量,請求會在**觸及池之前**就被卡住:使用者等到的
   不是「服務忙碌」而是不明的延遲,`SERVICE_BUSY` 的語意與資源實況脫節。
4. **著法格式驗證發生在路由函式本體之前**(5.2)。證明方式是先把池關掉再送出
   格式不合法的著法:若驗證真的攔在前面,回應是 400 而非 503。

## 為什麼用替身引擎

同 `test_game_service.py`:組裝的語意與引擎實際算什麼無關,而真實引擎每個進程要
載入 51MB NNUE,池容量一開就是好幾份。真終局(合法著法為空)也只有替身能穩定
構造。真實引擎的端到端往返屬 tasks 5.4 的整合測試。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import pathlib
import subprocess
import time
from typing import Any

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient

from service.config import Settings
from service.errors import ErrorCode, InternalError
from service.main import (
    MIN_THREADPOOL_CAPACITY,
    create_app,
    threadpool_capacity,
)
from service.types import Side

#: 測試題庫用的題號與書名。出處由資料夾表達,不是題目欄位。
PUZZLE_ID = 1
PUZZLE_SOURCE = "測試書"
MISSING_PUZZLE_ID = 9999

#: 《適情雅趣》第 21 局起始局面(紅先),與 `test_game_service.py` 同一份。
RED_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 w - - 0 1"
BLACK_FEN = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 b - - 0 1"

#: 替身的合法著法(`tests/fakes/fake_engine.py` 的 `PERFT_LINES`)。
FAKE_LEGAL_MOVES = ["e8f9", "e9f9"]

#: 替身在 `mate` 模式下回報的應手與殺著倒數(`score mate -15`,黑方視角)。
FAKE_REPLY_MOVE = "e9f9"
FAKE_MATE_IN = 15

#: 替身回應是即時的,逾時與節點數取小值使測試快。
FAKE_SEARCH_TIMEOUT = 1.0
FAKE_ACQUIRE_TIMEOUT = 0.4
FAKE_NODES = 1_000


# --- 測試題庫與應用 -----------------------------------------------------


def _write_position(root: pathlib.Path, side: Side, fen: str) -> None:
    """在 `root/測試書/` 底下寫一題。出處由資料夾表達,題目 JSON 沒有出處欄位。"""
    payload: dict[str, Any] = {
        "id": PUZZLE_ID,
        "title": "盡善克終",
        "description": "測試書 第一局 盡善克終",
        "fen": fen,
        "side_to_move": side.value,
        "difficulty": 3,
        "tags": ["雙馬", "連將殺"],
        "max_dtm": 16,
        "solvable": True,
    }
    folder = root / PUZZLE_SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{PUZZLE_ID:04d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
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


@pytest.fixture
def make_client(tmp_path: pathlib.Path, fake_engine):
    """回傳工廠:`make_client(mode, start_side=..., pool_size=...)` 給出一個已啟動的服務。

    以 `TestClient` 的情境進出驅動啟動與關閉掛鉤,因此每個測試拿到的都是走過完整
    生命週期的應用,而不是繞過掛鉤手工組出來的東西。
    """
    clients: list[TestClient] = []
    counter = {"n": 0}

    def make(
        mode: str = "normal",
        *,
        start_side: Side = Side.RED,
        pool_size: int = 1,
        raise_server_exceptions: bool = True,
    ) -> TestClient:
        counter["n"] += 1
        root = tmp_path / f"corpus_{counter['n']}"
        fen = RED_FEN if start_side is Side.RED else BLACK_FEN
        _write_position(root, start_side, fen)
        engine = fake_engine(mode)
        app = create_app(_settings(engine.path, root, pool_size))
        # 未捕捉的例外一律由全域處理器轉成回應,但 starlette 在送出回應之後仍會把
        # 原例外往外拋(好讓伺服器留下日誌)。測試要斷言的是**送出去的那份回應**,
        # 故此處關掉再拋 —— 打開時 TestClient 會先於斷言把例外丟回測試函式。
        client = TestClient(app, raise_server_exceptions=raise_server_exceptions)
        client.__enter__()
        clients.append(client)
        return client

    yield make

    for client in clients:
        client.__exit__(None, None, None)


def _child_pids() -> set[int]:
    """本行程目前的直接子進程 PID。

    引擎替身由 shell wrapper `exec` 成 Python 進程,父行程始終是 pytest 本身,
    因此「本行程的子進程」就是引擎進程所在的那一層。以作業系統的視角斷言,是為了
    讓「池自己以為關好了」這種情況無法蒙混過去。
    """
    listing = subprocess.run(
        ["ps", "-o", "pid=,ppid=", "-ax"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    mine = os.getpid()
    pids: set[int] = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit() and int(fields[1]) == mine:
            pids.add(int(fields[0]))
    return pids


# --- 題目起始局面端點(6.1) --------------------------------------------


def test_position_endpoint_returns_the_starting_position_and_puzzle_info(
    make_client,
) -> None:
    """6.1:起始局面**與對局所需的題目資訊**,含出處與該局面的合法著法。"""
    response = make_client().get(f"/api/positions/{PUZZLE_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == PUZZLE_ID
    assert payload["title"] == "盡善克終"
    assert payload["description"] == "測試書 第一局 盡善克終"
    assert payload["fen"] == RED_FEN
    assert payload["side_to_move"] == Side.RED.value
    assert payload["difficulty"] == 3
    assert payload["tags"] == ["雙馬", "連將殺"]
    assert payload["max_dtm"] == 16
    assert payload["solvable"] is True
    # 出處由題目所在的書目資料夾表達,不是題目欄位 —— 端點必須自題庫另取。
    assert payload["source"] == PUZZLE_SOURCE
    assert payload["state"] == {
        "side_to_move": Side.RED.value,
        "legal_moves": FAKE_LEGAL_MOVES,
        "over": False,
        "winner": None,
    }


def test_position_endpoint_reports_not_found_for_an_unknown_id(make_client) -> None:
    """6.2:不存在的題號回 404 與穩定的類別碼,而非 500。"""
    response = make_client().get(f"/api/positions/{MISSING_PUZZLE_ID}")

    assert response.status_code == 404
    assert response.json()["code"] == "POSITION_NOT_FOUND"
    assert response.json()["message"]


def test_position_endpoint_does_not_borrow_an_engine_for_an_unknown_id(
    make_client,
) -> None:
    """題號不存在時不該先付出借引擎的代價 —— 池關掉後仍須回 404 而非 503。"""
    client = make_client()
    client.app.state.pool.shutdown()

    response = client.get(f"/api/positions/{MISSING_PUZZLE_ID}")

    assert response.status_code == 404
    assert response.json()["code"] == "POSITION_NOT_FOUND"


# --- 題目索引端點(problem-browser 5.1、5.2、5.3、5.4)------------------


#: 索引端點的測試題庫:兩本書、三題,刻意涵蓋可解標記的三種取值。
#: 題號不連續(1、2、201)是重點 —— 唯一性由人工保證、無機制強制,索引不得預設
#: 題號是 1..N 的連續整數。
CATALOG_CORPUS: list[dict[str, Any]] = [
    {
        "source": "適情雅趣",
        "id": 1,
        "title": "盡善克終",
        "description": "適情雅趣 第一局 盡善克終",
        "difficulty": 3,
        "tags": ["雙馬", "連將殺"],
        "max_dtm": 16,
        "solvable": True,
    },
    {
        "source": "適情雅趣",
        "id": 2,
        "title": "野馬操田",
        "description": "適情雅趣 第二局 野馬操田",
        "difficulty": 5,
        "tags": ["單車"],
        "max_dtm": 8,
        "solvable": False,
    },
    {
        # 可解標記尚未回填(corpus-verification 未跑),欄位整個不存在。
        "source": "橘中秘",
        "id": 201,
        "title": "七星聚會",
        "description": "橘中秘 第二〇一局 七星聚會",
        "difficulty": 9,
        "tags": [],
    },
]


def _write_catalog_position(root: pathlib.Path, entry: dict[str, Any]) -> None:
    """依 `CATALOG_CORPUS` 的一筆資料寫出題目檔。出處由資料夾表達,不是題目欄位。"""
    payload = {key: value for key, value in entry.items() if key != "source"}
    payload["fen"] = RED_FEN
    payload["side_to_move"] = Side.RED.value
    folder = root / entry["source"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{entry['id']:04d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def catalog_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    """寫出 `CATALOG_CORPUS` 的題庫目錄,回傳其根目錄。"""
    root = tmp_path / "catalog_corpus"
    for entry in CATALOG_CORPUS:
        _write_catalog_position(root, entry)
    return root


@pytest.fixture
def make_catalog_client(catalog_corpus: pathlib.Path, fake_engine):
    """回傳工廠:對 `catalog_corpus` 起一個服務。可重複呼叫以模擬「重啟服務」。"""
    clients: list[TestClient] = []
    engine = fake_engine("normal")

    def make() -> TestClient:
        app = create_app(_settings(engine.path, catalog_corpus, 1))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make

    for client in clients:
        client.__exit__(None, None, None)


def test_catalog_endpoint_lists_every_position_with_the_fields_the_list_needs(
    make_catalog_client,
) -> None:
    """5.1:列表所需的全部資料來自這一份索引 —— 題庫中每一題、欄位齊備。

    欄位就是列表要畫的那幾樣:題號、局名、描述、難度、標籤、出處、可解標記。
    出處由題目所在的書目資料夾表達,不是題目欄位,端點必須自題庫另取。
    """
    response = make_catalog_client().get("/api/catalog")

    assert response.status_code == 200
    payload = response.json()
    # 題號遞增是刻意的:索引是列表的唯一資料來源,順序不該取決於檔案系統的走訪次序。
    assert [entry["id"] for entry in payload["positions"]] == [1, 2, 201]
    assert payload["positions"][0] == {
        "id": 1,
        "title": "盡善克終",
        "description": "適情雅趣 第一局 盡善克終",
        "difficulty": 3,
        "tags": ["雙馬", "連將殺"],
        "source": "適情雅趣",
        "solvable": True,
    }
    assert payload["positions"][2] == {
        "id": 201,
        "title": "七星聚會",
        "description": "橘中秘 第二〇一局 七星聚會",
        "difficulty": 9,
        "tags": [],
        "source": "橘中秘",
        "solvable": None,
    }


def test_catalog_endpoint_answers_with_the_engine_pool_shut_down(
    make_catalog_client,
) -> None:
    """5.2:呈現列表不得佔用任何引擎資源 —— 池關掉之後索引仍須完整回得出來。

    池關掉是「引擎資源一格都不剩」的最強形式:端點只要碰過池就會失敗,因此這個
    斷言直接證明它沒碰。它同時涵蓋池滿的情形 —— 池滿時借用會等到逾時後回 503。
    """
    client = make_catalog_client()
    client.app.state.pool.shutdown()

    response = client.get("/api/catalog")

    assert response.status_code == 200
    assert len(response.json()["positions"]) == len(CATALOG_CORPUS)


def test_catalog_endpoint_does_not_report_legal_moves_or_the_starting_position(
    make_catalog_client,
) -> None:
    """索引不含起始局面與合法著法 —— 那兩樣都得問引擎,而列表不需要它們。

    釘死欄位集合,日後有人「順手」把 `fen` 或 `state` 加進來時,5.2 的保證會在這裡
    先斷掉,而不是等到引擎池滿的線上時刻才現形。
    """
    payload = make_catalog_client().get("/api/catalog").json()

    for entry in payload["positions"]:
        assert set(entry) == {
            "id",
            "title",
            "description",
            "difficulty",
            "tags",
            "source",
            "solvable",
        }


def test_catalog_endpoint_keeps_an_unfilled_solvable_flag_empty(
    make_catalog_client,
) -> None:
    """可解標記可為空,**不得視為必填**,也不得擅自補一個預設值。

    corpus-verification 尚未回填,把空值當成 false 會讓整個題庫從列表上消失;
    當成 true 則是替驗證工具作了它還沒作的結論。空值一律照實傳出去。
    """
    payload = make_catalog_client().get("/api/catalog").json()
    by_id = {entry["id"]: entry for entry in payload["positions"]}

    assert by_id[201]["solvable"] is None
    # 標為不可解的題目仍在索引裡 —— 過不過濾是列表的事(1.4),索引只據實回報。
    assert by_id[2]["solvable"] is False


def test_catalog_endpoint_covers_a_new_position_after_a_restart(
    make_catalog_client, catalog_corpus: pathlib.Path
) -> None:
    """5.3:題庫新增題目後,索引在**不修改程式**的情況下涵蓋該題。

    新題直接寫進題庫目錄,服務重啟一次即出現 —— 這正是端點勝過靜態索引檔的地方:
    沒有「忘記重跑產出工具」這個失效模式。
    """
    before = make_catalog_client().get("/api/catalog").json()
    assert 302 not in {entry["id"] for entry in before["positions"]}

    _write_catalog_position(
        catalog_corpus,
        {
            "source": "百局象棋譜",
            "id": 302,
            "title": "小征東",
            "description": "百局象棋譜 第三〇二局 小征東",
            "difficulty": 4,
            "tags": ["馬炮"],
        },
    )

    after = make_catalog_client().get("/api/catalog").json()

    assert [entry["id"] for entry in after["positions"]] == [1, 2, 201, 302]
    assert after["positions"][3]["source"] == "百局象棋譜"


# --- 局面查詢端點(1.1) ------------------------------------------------


def test_state_endpoint_returns_the_legal_moves_of_the_position(make_client) -> None:
    """1.1:回傳該局面輪方的所有合法著法。"""
    response = make_client().post(
        "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
    )

    assert response.status_code == 200
    assert response.json() == {
        "side_to_move": Side.RED.value,
        "legal_moves": FAKE_LEGAL_MOVES,
        "over": False,
        "winner": None,
    }


def test_state_endpoint_takes_the_move_sequence_from_the_request_body(
    make_client,
) -> None:
    """走法序列走主體而非 query string(design:走法序列以請求主體傳遞)。"""
    response = make_client().post(
        "/api/state", json={"position_id": PUZZLE_ID, "moves": ["e8f9"]}
    )

    assert response.status_code == 200
    assert response.json()["side_to_move"] == Side.BLACK.value


def test_state_endpoint_reports_a_real_end_when_no_legal_moves_remain(
    make_client,
) -> None:
    """1.2:合法著法數為 0 即對局結束,且該輪方為負方。"""
    response = make_client("no_legal_moves").post(
        "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
    )

    assert response.status_code == 200
    assert response.json() == {
        "side_to_move": Side.RED.value,
        "legal_moves": [],
        "over": True,
        "winner": Side.BLACK.value,
    }


# --- 黑方應手端點(1.4) ------------------------------------------------


def test_black_move_endpoint_returns_the_reply_signal_and_following_state(
    make_client,
) -> None:
    """1.4、2.1、2.2:應手、三態信號、殺著倒數,以及**走後**的完整對局狀態。"""
    client = make_client("mate", start_side=Side.BLACK)

    response = client.post(
        "/api/black-move", json={"position_id": PUZZLE_ID, "moves": []}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["move"] == FAKE_REPLY_MOVE
    assert payload["signal"] == "red_winning"
    assert payload["mate_in"] == FAKE_MATE_IN
    # 走後狀態同批回傳,前端不必為了畫新盤面再發一次局面查詢(design:典型流程)。
    # 黑方走了一手,因此輪回紅方 —— 這正是「走後」而非「走前」的證據。
    assert payload["state"]["side_to_move"] == Side.RED.value
    assert payload["state"]["legal_moves"] == FAKE_LEGAL_MOVES
    assert payload["state"]["over"] is False


def test_black_move_endpoint_keeps_empty_fields_instead_of_omitting_them(
    make_client,
) -> None:
    """終局那一手:黑方無著可走,`move` 與 `mate_in` 為空但**欄位仍在**。

    省略欄位會讓前端分不清「黑方無著」與「服務漏傳」;而 `mate_in` 為 0 是終局那手
    的正常取值,任何 falsy 判斷都會把它連同這裡的 `None` 一起吃掉。
    """
    client = make_client("no_legal_moves", start_side=Side.BLACK)

    response = client.post(
        "/api/black-move", json={"position_id": PUZZLE_ID, "moves": []}
    )

    assert response.status_code == 200
    payload = response.json()
    assert "move" in payload and payload["move"] is None
    assert "mate_in" in payload and payload["mate_in"] is None
    # `move` 為空不代表沒有信號 —— 兩個欄位彼此獨立。
    assert payload["signal"] == "unknown"
    assert payload["state"] == {
        "side_to_move": Side.BLACK.value,
        "legal_moves": [],
        "over": True,
        "winner": Side.RED.value,
    }


def test_black_move_endpoint_rejects_a_request_when_red_is_to_move(
    make_client,
) -> None:
    """1.5:輪方為紅時拒絕應手請求,且此判定在借引擎之前完成。"""
    client = make_client("mate", start_side=Side.RED)
    client.app.state.pool.shutdown()

    response = client.post(
        "/api/black-move", json={"position_id": PUZZLE_ID, "moves": []}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "WRONG_SIDE_TO_MOVE"


# --- 錯誤映射的最小集合(5.1、5.2) ------------------------------------


@pytest.mark.parametrize("endpoint", ["/api/state", "/api/black-move"])
def test_invalid_move_format_is_rejected_before_the_route_body_runs(
    make_client, endpoint: str
) -> None:
    """5.2:格式不合法的著法在**進入路由函式之前**被拒,因此從未觸及引擎池。

    證明方式是先把池關掉:池關掉後任何借用都會回 503。此時仍拿到 400,代表借引擎
    的程式碼一步都沒跑到 —— 若驗證改成在函式本體內手動進行,借用會先發生。
    """
    client = make_client()
    client.app.state.pool.shutdown()

    response = client.post(
        endpoint, json={"position_id": PUZZLE_ID, "moves": ["e8f9", "not-a-move"]}
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "INVALID_MOVE_FORMAT"
    # 訊息要指出是哪一手不合法,client 才能直接顯示。
    assert "not-a-move" in payload["message"]


def test_pool_exhaustion_is_reported_as_service_busy(make_client) -> None:
    """3.3:等待上限內借不到引擎即回報忙碌(503),不無限期等待。"""
    client = make_client(pool_size=1)

    with client.app.state.pool.acquire():
        response = client.post(
            "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
        )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_BUSY"


# --- 啟動與關閉掛鉤 -----------------------------------------------------


def test_startup_builds_the_corpus_index_and_the_engine_pool(make_client) -> None:
    client = make_client(pool_size=2)

    assert len(client.app.state.repository) == 1
    assert client.app.state.pool.size == 2
    assert client.app.state.pool.available_count == 2


def test_shutdown_leaves_no_engine_subprocess_behind(
    tmp_path: pathlib.Path, fake_engine
) -> None:
    """完成狀態的明文要求:關閉後無殘留的引擎子進程。

    以作業系統的子進程清單為準,而不是問池「你關好了嗎」—— 殘留的每個進程都常駐
    一份 NNUE,漏掉的代價是每次重啟疊加一批記憶體。
    """
    pool_size = 2
    root = tmp_path / "corpus"
    _write_position(root, Side.RED, RED_FEN)
    engine = fake_engine("normal")
    app = create_app(_settings(engine.path, root, pool_size))

    before = _child_pids()
    with TestClient(app) as client:
        assert client.get(f"/api/positions/{PUZZLE_ID}").status_code == 200
        started = _child_pids() - before
        assert len(started) >= pool_size, "啟動掛鉤應該起了整池引擎進程"

    assert _child_pids() & started == set(), "關閉掛鉤必須收掉全部引擎進程"


# --- 併發閘門的位置 -----------------------------------------------------


@pytest.mark.parametrize("pool_size", [1, 4, 16, 64, 256])
def test_threadpool_capacity_is_always_greater_than_the_pool_size(
    pool_size: int,
) -> None:
    """threadpool 容量恆大於引擎池容量(design:併發閘門的位置)。

    兩者相等或更小時,請求會在觸及池之前先被 threadpool 卡住:那裡沒有等待上限也
    沒有錯誤語意,`SERVICE_BUSY` 因此無法反映資源實況。
    """
    assert threadpool_capacity(pool_size) > pool_size


@pytest.mark.parametrize("pool_size", [2, 11])
def test_threadpool_capacity_is_configured_on_the_running_event_loop(
    make_client, pool_size: int
) -> None:
    """設定必須真的套用到框架實際使用的 limiter,不是算出一個數字放著。

    **必須包含一個讓容量超過 anyio 預設值的池大小**。`MIN_THREADPOOL_CAPACITY`
    恰好等於 anyio 自己的預設 `total_tokens`,所以小池的斷言只是在驗證 anyio
    免費給的值 —— 把整行賦值刪掉測試照樣通過。池大小 11 起容量才會是 44,
    這時「執行緒池容量大於引擎池容量」這個保證才真的被釘住,而池大小正是
    可由環境變數調整、且任務 2.3 要依實測修訂的參數。
    """
    client = make_client(pool_size=pool_size)

    limiter = client.portal.call(anyio.to_thread.current_default_thread_limiter)

    assert limiter is client.app.state.threadpool_limiter
    assert limiter.total_tokens == threadpool_capacity(pool_size)
    assert limiter.total_tokens >= MIN_THREADPOOL_CAPACITY
    assert limiter.total_tokens > pool_size


def test_requests_reach_the_pool_concurrently_instead_of_queueing_in_the_threadpool(
    make_client,
) -> None:
    """池滿時的請求要**並行**撞上閘門,而不是在 threadpool 裡排隊。

    池被佔滿後,每個請求都會等滿借用上限才回報忙碌。若 threadpool 容量不大於池
    容量,這些請求會逐一序列化,總耗時逼近「請求數 × 借用上限」;容量足夠時則
    全部並行,總耗時約為一個借用上限。門檻取兩者之間,兩種行為因此可被區分。
    """
    request_count = 4
    client = make_client(pool_size=1)

    def hit() -> int:
        return client.post(
            "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
        ).status_code

    with client.app.state.pool.acquire():
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(request_count) as executor:
            statuses = list(executor.map(lambda _: hit(), range(request_count)))
        elapsed = time.monotonic() - started

    assert statuses == [503] * request_count
    serialized = request_count * FAKE_ACQUIRE_TIMEOUT
    assert elapsed < serialized / 2, (
        f"{request_count} 個請求耗時 {elapsed:.2f} 秒,"
        f"接近序列化的 {serialized:.2f} 秒 —— 請求被 threadpool 卡住了"
    )


# --- 全域錯誤處理(tasks 4.3、requirements 4.4、5.1、5.4)-----------------


class _ExplodingService:
    """借走一個引擎之後才炸掉的對局服務替身。

    存在的理由是**未捕捉例外的路徑無法用真實協作者製造** —— 服務層與引擎層都已把
    自己的失敗收斂成 `ServiceError`,那條路徑走的是既有的映射。此替身模擬「日後有人
    在借用中途寫出一個沒人預期的例外」:池的資源必須照樣回來(4.4),而例外攜帶的
    路徑與堆疊文字**不得**出現在回應裡(5.4)。

    例外訊息刻意塞進真實檔案路徑、假堆疊與引擎輸出樣本,使外洩一旦發生就會被斷言
    抓到 —— 訊息乾淨的例外證明不了處理器有在收斂。
    """

    def __init__(self, pool) -> None:
        self._pool = pool

    def state(self, position_id: int, moves: list[str]):
        del position_id, moves
        with self._pool.acquire():
            raise RuntimeError(
                f'Traceback (most recent call last):\n  File "{__file__}", line 1\n'
                "bestmove e8f9 ponder e9f9"
            )


#: 內部細節的痕跡:專案路徑、任何 Python 檔名、堆疊追蹤(5.4)。
INTERNAL_MARKERS = (
    str(pathlib.Path(__file__).resolve().parent.parent),
    "/Users/",
    ".py",
    "Traceback",
    'File "',
    "site-packages",
)

#: 引擎原始輸出的痕跡,取自 `tests/fakes/fake_engine.py` 實際會吐的字串(5.4)。
ENGINE_OUTPUT_MARKERS = (
    "bestmove",
    "uciok",
    "readyok",
    "info depth",
    "Nodes searched",
    "Fen:",
    "score mate",
    "score cp",
)

#: 「連續」觸發錯誤的次數。單次成功歸還證明不了不變量 —— 每次漏還一格的洩漏,
#: 正好要重複觸發才會把池吃乾淨。
FAILURE_REPEATS = 7

#: 七種對外錯誤類別碼(`service/errors.py` 為唯一來源)。回應只能落在這個集合裡。
ERROR_CODES = frozenset(code.value for code in ErrorCode)


def _assert_is_an_error_payload(response) -> None:
    """回應必須是 `{code, message}` 這一個形狀,別無其他欄位(5.1)。"""
    payload = response.json()
    assert set(payload) == {"code", "message"}, (
        f"錯誤回應形狀不是契約的 {{code, message}}:{payload}"
    )
    assert payload["code"] in ERROR_CODES, f"類別碼不在七種之內:{payload['code']}"
    assert payload["message"], "訊息不得為空,client 要能直接顯示"


def _assert_leaks_nothing(response) -> None:
    """回應全文不得帶內部路徑、堆疊追蹤或引擎原始輸出(5.4)。

    斷言的對象是**整份回應本文**而不只是 `message` —— 外洩可能發生在任何一個欄位,
    只看訊息等於預先假設了外洩會出現在哪裡。
    """
    text = response.text
    for marker in INTERNAL_MARKERS:
        assert marker not in text, f"回應帶了內部細節「{marker}」:{text}"
    for marker in ENGINE_OUTPUT_MARKERS:
        assert marker not in text, f"回應帶了引擎原始輸出「{marker}」:{text}"


# --- 結構性驗證失敗也要走契約形狀(5.1) -------------------------------


@pytest.mark.parametrize(
    ("case", "body"),
    [
        ("extra_field", {"position_id": PUZZLE_ID, "move": [], "moves": []}),
        ("wrong_type", {"position_id": "abc", "moves": []}),
        ("moves_not_a_list", {"position_id": PUZZLE_ID, "moves": "e8f9"}),
        ("missing_field", {}),
    ],
)
def test_structural_validation_failures_use_the_error_contract_shape(
    make_client, case: str, body: dict[str, Any]
) -> None:
    """5.1:請求形狀不合法時,回應仍是 `{code, message}`,不是框架的 `{detail: [...]}`。

    形狀不合法與著法不合法都是同一件事的兩種表現 —— **client 送出的請求無法被處理**。
    兩者回不同形狀的話,client 得為錯誤回應寫兩套解析,5.1 的「可程式判別」就落空了。
    """
    del case
    response = make_client().post("/api/state", json=body)

    assert response.status_code == 400
    _assert_is_an_error_payload(response)
    assert response.json()["code"] == "INVALID_MOVE_FORMAT"
    assert "detail" not in response.json()


def test_a_non_integer_position_id_is_reported_as_a_missing_puzzle(
    make_client,
) -> None:
    """題號不是整數時回 404 —— 與題號不存在同一個類別碼。

    `position_id` 是整組 API **唯一**的路徑參數,因此「路徑參數不合法」與「沒有這個
    題目」在此完全等價,client 的處置也相同(回題目列表)。回 `INVALID_MOVE_FORMAT`
    則會叫 client 去檢查它根本沒送出的著法。
    """
    response = make_client().get("/api/positions/abc")

    assert response.status_code == 404
    _assert_is_an_error_payload(response)
    assert response.json()["code"] == "POSITION_NOT_FOUND"


def test_structural_validation_failures_do_not_echo_the_offending_input(
    make_client,
) -> None:
    """驗證細節不得原樣回放:框架的錯誤項目帶著 `input` 與使用者自訂的欄位名。

    直接把 `exc.errors()` 序列化出去,等於讓請求方決定回應內容 —— 送一個長得像檔案
    路徑的欄位名進來,它就會從錯誤回應裡再冒出來(5.4)。
    """
    response = make_client().post(
        "/api/state",
        json={"position_id": PUZZLE_ID, "moves": [], "/Users/secret/leak.py": "x"},
    )

    assert response.status_code == 400
    _assert_is_an_error_payload(response)
    _assert_leaks_nothing(response)
    assert "leak" not in response.text


# --- 未捕捉的例外一律轉 INTERNAL(4.4、5.4) ---------------------------


def test_unexpected_exceptions_are_reported_as_internal_errors(make_client) -> None:
    """5.1、5.4:沒人預期的例外轉成 `INTERNAL`(500),且訊息是固定的通用敘述。"""
    client = make_client(raise_server_exceptions=False)
    client.app.state.service = _ExplodingService(client.app.state.pool)

    response = client.post("/api/state", json={"position_id": PUZZLE_ID, "moves": []})

    assert response.status_code == 500
    _assert_is_an_error_payload(response)
    assert response.json()["code"] == "INTERNAL"
    assert response.json()["message"] == InternalError().message
    _assert_leaks_nothing(response)


def test_unexpected_exceptions_do_not_leak_the_exception_text(make_client) -> None:
    """例外訊息本身**不得**成為回應訊息 —— 它裡面就是路徑、堆疊與引擎輸出(5.4)。"""
    client = make_client(raise_server_exceptions=False)
    client.app.state.service = _ExplodingService(client.app.state.pool)

    response = client.post("/api/state", json={"position_id": PUZZLE_ID, "moves": []})

    _assert_leaks_nothing(response)
    assert "line 1" not in response.text


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        ("exit_on_go", 500, "INTERNAL"),
        ("mute_on_display", 504, "ENGINE_TIMEOUT"),
        ("ignores_moves", 409, "ILLEGAL_MOVE_SEQUENCE"),
    ],
)
def test_engine_layer_failures_report_stable_codes_without_leaking(
    make_client, mode: str, status: int, code: str
) -> None:
    """引擎崩潰、逾時、序列走不出:各有穩定的類別碼,且回應不帶引擎原始輸出(5.4)。

    這三種模式都是**引擎已經吐過東西之後**才失敗的,因此正是「順手把引擎輸出塞進
    訊息」最容易發生的地方。
    """
    client = make_client(mode)

    response = client.post(
        "/api/state", json={"position_id": PUZZLE_ID, "moves": ["e8f9"]}
    )

    assert response.status_code == status
    _assert_is_an_error_payload(response)
    assert response.json()["code"] == code
    _assert_leaks_nothing(response)


def test_service_busy_does_not_leak_internal_details(make_client) -> None:
    """3.3 的忙碌回應同樣受 5.4 管轄。"""
    client = make_client(pool_size=1)

    with client.app.state.pool.acquire():
        response = client.post(
            "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
        )

    assert response.status_code == 503
    _assert_is_an_error_payload(response)
    _assert_leaks_nothing(response)


# --- 失敗路徑不洩漏池中資源(4.4) -------------------------------------


@pytest.mark.parametrize("mode", ["ignores_moves", "mute_on_display", "exit_on_go"])
def test_repeated_engine_failures_leave_the_pool_at_full_capacity(
    make_client, mode: str
) -> None:
    """4.4:連續失敗之後,池中可用引擎數回復原值,每一次都給得出穩定的類別碼。

    重複觸發是必要的 —— 每次漏還一格的洩漏,單次觀察時池裡還有別的引擎頂著,
    要連續觸發才會把容量吃乾淨,而那正是服務躺平的方式。

    **此處刻意不釘死每一次的類別碼**:逾時與串流結束會把該進程標記為不健康,後續
    請求因此改回 `INTERNAL`(重建屬 tasks 2.5)。要釘的是「每一次都落在七種類別碼
    之內、形狀一致、不外洩」,單次失敗的精確類別碼由上一組測試負責。
    """
    pool_size = 2
    client = make_client(mode, pool_size=pool_size)

    for _ in range(FAILURE_REPEATS):
        response = client.post(
            "/api/state", json={"position_id": PUZZLE_ID, "moves": ["e8f9"]}
        )
        assert response.status_code >= 400
        _assert_is_an_error_payload(response)
        _assert_leaks_nothing(response)

    assert client.app.state.pool.available_count == pool_size
    assert client.app.state.pool.borrowed_count == 0


def test_repeated_unexpected_failures_leave_the_pool_at_full_capacity(
    make_client,
) -> None:
    """4.4:未捕捉的例外同樣不得吃掉池中的名額。

    替身在**借用中途**炸掉,因此這裡問的不是「服務層記得歸還嗎」,而是借用的離開
    路徑是否為語言保證。漏還一格就少一格,連續三次之後池就空了。
    """
    pool_size = 2
    client = make_client(pool_size=pool_size, raise_server_exceptions=False)
    client.app.state.service = _ExplodingService(client.app.state.pool)

    for _ in range(FAILURE_REPEATS):
        response = client.post(
            "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
        )
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL"

    assert client.app.state.pool.available_count == pool_size
    assert client.app.state.pool.borrowed_count == 0


def test_the_service_still_answers_after_a_run_of_failures(make_client) -> None:
    """4.4 的整句話:單次(乃至連續)失敗之後,服務仍答得出正常請求。"""
    client = make_client(pool_size=2, raise_server_exceptions=False)
    healthy = client.app.state.service
    client.app.state.service = _ExplodingService(client.app.state.pool)

    for _ in range(FAILURE_REPEATS):
        assert (
            client.post(
                "/api/state", json={"position_id": PUZZLE_ID, "moves": []}
            ).status_code
            == 500
        )

    client.app.state.service = healthy
    response = client.post("/api/state", json={"position_id": PUZZLE_ID, "moves": []})

    assert response.status_code == 200
    assert response.json()["legal_moves"] == FAKE_LEGAL_MOVES
