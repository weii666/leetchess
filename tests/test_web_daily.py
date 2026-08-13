"""每日推薦題的選題邏輯(`web/daily.js`)。

與 `test_web_catalog.py` 對 `catalog.js` 的驗證同一個理由:`daily.js` 是純函式、
不碰 DOM,因此可以用 `page.evaluate()` 單獨驗證,不必走完整的頁面流程。

## 這裡不驗證什麼

`daily.js` 選出**哪一題**只是計算,把它畫成一列(標籤欄換成「每日一題」、與
`#positions` 共用同一份完成/標星狀態)是 `list.js` 的事,那部分的整合測試在
`test_web_list.py`。本檔只驗證 `pickDailyPosition()`/`todayKey()` 這兩個純函式
本身的正確性與穩定性——尤其是「新增題目不該讓其他日期的推薦跟著跳」這個 rendezvous
hash 存在的理由,那正是這裡的核心斷言。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest

# `browser`/`browser_page` 不必在這裡 import——`tests/conftest.py` 已經在頂層
# `from tests.conftest_web import browser, browser_page` 過,對所有測試檔案自動
# 生效。在這裡再 import 一次會讓 pytest 認成另一份獨立定義的 session-scoped
# fixture,兩份各自嘗試開一個 `sync_playwright()`,第二個開始執行時撞見前一個
# 留下的 event loop 而炸掉(`test_web_catalog.py` 同樣不 import,是既有慣例)。

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
DAILY_JS = WEB_DIR / "daily.js"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下。
ORIGIN = "https://daily-position.test"

#: 合成的候選題目。`pickDailyPosition` 只用得到 `id`,但夾具仍給出完整形狀,
#: 與其他測試檔的 CATALOG 夾具風格一致。
POSITIONS: list[dict[str, Any]] = [
    {"id": 1, "title": "盡善克終", "difficulty": 3, "tags": ["連將殺"]},
    {"id": 2, "title": "車馬冷著", "difficulty": 3, "tags": ["連將殺"]},
    {"id": 3, "title": "棄子入局", "difficulty": 5, "tags": ["連將殺", "鬥快"]},
    {"id": 5, "title": "殘局存疑", "difficulty": 2, "tags": []},
    {"id": 8, "title": "雙包齊鳴", "difficulty": 5, "tags": ["雙包"]},
]


@pytest.fixture
def daily_page(browser_page) -> Iterator:
    """一個位於 http 來源、可 `import` `web/daily.js` 的空白分頁。

    理由與 `test_web_catalog.py` 的 `catalog_page` 相同:Chromium 不允許自
    `file://` 匯入 ES module,測試必須讓模組在真的 http(s) 來源下被載入。
    """

    def serve(route) -> None:
        from urllib.parse import urlsplit

        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>每日推薦題驗證</title>",
            )
            return

        target = WEB_DIR / path.lstrip("/")
        if target.is_file():
            route.fulfill(
                status=200,
                content_type="text/javascript; charset=utf-8",
                body=target.read_text(encoding="utf-8"),
            )
            return

        route.fulfill(status=404, content_type="text/plain", body="not found")

    browser_page.route(f"{ORIGIN}/**", serve)
    browser_page.goto(f"{ORIGIN}/index.html")
    yield browser_page


def pick(page, positions: list[dict[str, Any]], date: str) -> dict[str, Any] | None:
    """呼叫 `pickDailyPosition(positions, date)`,回傳選中的題目(或 `None`)。"""
    return page.evaluate(
        """async ({ positions, date }) => {
          const { pickDailyPosition } = await import('/daily.js');
          return pickDailyPosition(positions, date);
        }""",
        {"positions": positions, "date": date},
    )


# --- 決定性與穩定性 ------------------------------------------------------


def test_the_same_positions_and_date_always_pick_the_same_position(daily_page) -> None:
    """同一組 (positions, date) 多次呼叫,結果必須完全一樣——這是一個純函式。"""
    first = pick(daily_page, POSITIONS, "2024-01-01")
    second = pick(daily_page, POSITIONS, "2024-01-01")

    assert first is not None
    assert first == second


def test_the_pick_does_not_depend_on_array_order(daily_page) -> None:
    """挑的是雜湊值最大的候選,不是陣列裡的位置——洗牌後結果應該不變。

    這條抓得到「不小心用了 index 而不是題號去算雜湊」這種寫法:那種寫法在陣列
    順序不變的測試下全綠,只有真的洗牌才會露餡。
    """
    original = pick(daily_page, POSITIONS, "2024-03-17")
    shuffled = list(reversed(POSITIONS))

    reshuffled_pick = pick(daily_page, shuffled, "2024-03-17")

    assert reshuffled_pick == original


def test_removing_a_non_winning_candidate_does_not_change_the_result(daily_page) -> None:
    """rendezvous hash 的核心訴求:新增/刪除**不是贏家**的候選,其餘日期的推薦
    不受影響。

    這是題庫新增題目時「今天的推薦題會不會變動」這個問題的直接證據——單純
    `hash(date) % len(positions)` 的寫法在這裡會失敗,因為刪掉任何一題都會讓
    除數變動、進而讓 index 對應到別的題目。
    """
    date = "2024-06-01"
    winner = pick(daily_page, POSITIONS, date)
    assert winner is not None

    # 挑一個不是贏家的候選整個拿掉,贏家本身留著。
    loser = next(p for p in POSITIONS if p["id"] != winner["id"])
    without_a_loser = [p for p in POSITIONS if p["id"] != loser["id"]]
    assert len(without_a_loser) == len(POSITIONS) - 1, "測試前提:只移除了一題"
    assert any(p["id"] == winner["id"] for p in without_a_loser), "測試前提:贏家還在"

    still_the_same = pick(daily_page, without_a_loser, date)

    assert still_the_same == winner


def test_an_empty_catalog_has_no_daily_position(daily_page) -> None:
    """題庫為空時回傳 `null`,不是丟例外或猜一個題目——「今天沒有推薦題」是合法
    狀態(題庫還沒載好、載入失敗、或題庫真的是空的),呼叫端要能分得出來。
    """
    assert pick(daily_page, [], "2024-01-01") is None


def test_different_dates_can_pick_different_positions(daily_page) -> None:
    """挑選會隨日期變化,不會每天都卡在同一題——掃過 30 個連續日期,至少要出現
    一題以上的不同結果(候選只有 5 題,不強求平均分布,只驗證不是退化成常數)。
    """
    picks = {
        pick(daily_page, POSITIONS, f"2024-01-{day:02d}")["id"] for day in range(1, 31)
    }

    assert len(picks) > 1, f"30 天都選到同一題,雜湊疑似退化:{picks}"


# --- todayKey():本地日期字串 --------------------------------------------


def today_key_for(page, year: int, month_index: int, day: int) -> str:
    """呼叫 `todayKey(new Date(year, monthIndex, day))`。

    `monthIndex` 是 JS `Date` 建構子的月份(0 起算),與 Python 呼叫端刻意同名
    以避免對到 `todayKey()` 內部 `+1` 的邏輯時搞混。
    """
    return page.evaluate(
        """async ({ year, monthIndex, day }) => {
          const { todayKey } = await import('/daily.js');
          return todayKey(new Date(year, monthIndex, day));
        }""",
        {"year": year, "monthIndex": month_index, "day": day},
    )


@pytest.mark.parametrize(
    ("year", "month_index", "day", "expected"),
    [
        # 月、日都需要補零。
        (2024, 0, 5, "2024-01-05"),
        # 月、日都是兩位數,不需要補零。
        (2024, 11, 31, "2024-12-31"),
        # 月需要補零、日不需要。
        (2024, 8, 10, "2024-09-10"),
    ],
)
def test_today_key_formats_the_local_date_with_zero_padding(
    daily_page, year, month_index, day, expected
) -> None:
    """`YYYY-MM-DD`,月、日不足兩位補零——這是 `pickDailyPosition` 拿去雜湊的
    輸入,格式必須每天都固定寬度,不然「9」跟「09」會被當成不同的日期字串。
    """
    assert today_key_for(daily_page, year, month_index, day) == expected


# --- 邊界:純資料、不碰 DOM、不用 UTC ------------------------------------


def test_the_daily_module_touches_no_dom() -> None:
    """「純資料,不碰 DOM」由原始碼把關(與 `test_web_catalog.py` 的
    `test_the_catalog_module_touches_no_dom` 同一個理由:瀏覽器裡永遠有 DOM
    可碰,沒有任何執行結果能證明「它沒去碰」)。
    """
    source = DAILY_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)

    assert not re.search(r"\b(document|window|localStorage)\b", code)


def test_today_key_does_not_use_utc(daily_page) -> None:
    """`todayKey()` 不能用 `toISOString()`——那是 UTC,台灣時區(UTC+8)的深夜
    會被算成前一天。原始碼層級把關,理由與上一條相同。
    """
    source = DAILY_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)

    assert "toISOString" not in code
