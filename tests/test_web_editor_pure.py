"""收題頁純函式的驗證(design 的 Testing Strategy「純函式(`page.evaluate()`)」)。

`web/editor/check.js` 不碰 DOM、不發請求、不持有狀態,因此不必走完整的頁面流程 ——
把模組載進真實瀏覽器、以 `page.evaluate()` 呼叫並把結果取回 Python 比對即可。這也
順帶證明了 design 的依賴方向:`check.js` 只向左依賴 `web/fen.js`,單獨載入就能運作。

供檔方式與 `tests/test_web_pure.py` 相同(`page.route()` 就地供檔,不啟動伺服器
進程),理由亦同 —— Chromium 不允許自 `file://` 匯入 ES module,而前端以 ES modules
交付且無建置步驟,所以模組必須在一個真的 http 來源下被載入。

## 這一層永遠不是放行判準

`check.js` 只做淺層檢查(填了沒、形狀對不對)。候選題目是否合格由服務端的
`POST /api/editor/validate` 判定,兩者若對同一個輸入給出不同結論**以服務端為準**。
本檔因此刻意有一條反向斷言(見 `test_check_form_does_not_reimplement_the_schema`):
淺層檢查放行的東西不代表題目合格。

> `corpus-file.js` 的純函式(tasks 3.2)日後併入本檔,夾具與供檔方式共用。
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
CHECK_JS = WEB_DIR / "editor" / "check.js"
POSITIONS_DIR = PROJECT_ROOT / "positions"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-pure.test"

#: 《適情雅趣》第 25 局的起始局面,取自 `positions/適情雅趣~卷一/25.json`。
PUZZLE_FEN = "2Rakc3/4aR3/4b1n2/4C4/6b2/2B6/2P1c4/2n1B3C/1r2A1p2/4KA3 w - - 0 1"

#: 一份**淺層檢查全數通過**的表單。各測試以覆寫單一欄位的方式製造缺漏,
#: 因此每條斷言都只有一個變因。
VALID_FORM = {
    "id": "26",
    "title": "患在几席",
    "description": "適情雅趣 第二六局 患在几席",
    "difficulty": "1",
    "tags": "解殺還殺、鐵門栓",
    "fen": PUZZLE_FEN,
    "target": "適情雅趣~卷一/26.json",
}

#: 表單欄位的順序。`checkForm()` 的清單順序必須與此一致,8.4 的呈現才有穩定次序。
FORM_FIELD_ORDER = ["id", "title", "description", "difficulty", "tags", "fen", "target"]


@pytest.fixture
def module_page(browser_page) -> Iterator:
    """一個位於 http 來源、可以 `import` `web/` 底下模組的空白分頁。

    供檔直接讀 `web/` 的真實檔案,所以測到的就是交付物本身,不是任何複本。
    """

    def serve(route) -> None:
        path = urlsplit(route.request.url).path
        if path in ("/", "/index.html"):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                # 刻意不用 web/editor/index.html:那一頁日後會載入 editor.js,而本檔
                # 要驗證的是 check.js 能單獨運作。
                body='<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8">'
                "<title>收題頁純函式驗證</title>",
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


def with_check(page, body: str):
    """把 `body` 當成函式本體執行,其中 `check` 已綁定為 `check.js` 的匯出。"""
    return page.evaluate(
        "async () => {\n  const check = await import('/editor/check.js');\n"
        + body
        + "\n}"
    )


def js(value) -> str:
    """把 Python 值寫成等價的 JS 字面值。中文不轉義,失敗時的訊息才讀得懂。"""
    return json.dumps(value, ensure_ascii=False)


def check_form(page, **overrides) -> list[dict]:
    """以 `VALID_FORM` 為底、覆寫指定欄位後跑 `checkForm()`。"""
    values = {**VALID_FORM, **overrides}
    return with_check(page, f"  return check.checkForm({js(values)});")


def fields_of(issues: list[dict]) -> list[str | None]:
    return [issue["field"] for issue in issues]


def corpus_entries() -> list[tuple[pathlib.Path, dict]]:
    """題庫中的每一題,連同它所在的檔案路徑。

    以真實題庫當回歸樣本:描述的寫法與 FEN 的形狀一旦與既有題目分家,這裡先紅。
    """
    entries: list[tuple[pathlib.Path, dict]] = []
    for path in sorted(POSITIONS_DIR.rglob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            entries.append((path, entry))
    return entries


# --- checkForm:必填、題號、標籤(4.1、4.2、4.6、8.4)---------------------


def test_check_form_passes_a_complete_form(module_page) -> None:
    """淺層無異議時回空清單 —— 空清單是「這一層沒話說」,不是「這題合格」。"""
    assert check_form(module_page) == []


def test_check_form_reports_every_empty_field_in_form_order(module_page) -> None:
    """4.1、8.4:每一個未填妥的項目都被指出,且順序與表單欄位一致。

    順序是 8.4 呈現的基礎:清單若隨實作細節換位置,畫面上的訊息就會跳動。
    """
    issues = check_form(
        module_page,
        id="",
        title="",
        description="",
        difficulty="",
        tags="",
        fen="",
        target="",
    )

    assert fields_of(issues) == FORM_FIELD_ORDER


def test_check_form_reports_one_issue_per_field(module_page) -> None:
    """同一欄位最多一項 —— 一個空欄位不該同時觸發「必填」與「格式不對」兩條。"""
    issues = check_form(
        module_page,
        id="",
        title="",
        description="",
        difficulty="",
        tags="",
        fen="",
        target="",
    )
    seen = fields_of(issues)

    assert len(seen) == len(set(seen))


def test_check_form_issues_carry_a_message(module_page) -> None:
    """8.4:每一項都要說得出「是哪一項未通過」,不能只有欄位名。"""
    issues = check_form(module_page, id="", tags="")

    assert all(isinstance(issue["message"], str) for issue in issues)
    assert all(issue["message"].strip() for issue in issues)


@pytest.mark.parametrize(
    "raw_id",
    [
        "0",  # 正整數不含 0
        "-1",  # 負數
        "2.5",  # 小數
        "abc",  # 完全不是數字
        "25a",  # 前綴像數字
        "1e3",  # 科學記號:Number() 收得下,但題號不是這樣寫的
        "２５",  # 全形數字
        "1 2",  # 中間有空白
    ],
)
def test_check_form_rejects_an_id_that_is_not_a_positive_integer(
    module_page, raw_id: str
) -> None:
    """4.2:題號不是正整數時指出題號不合格式。"""
    issues = check_form(module_page, id=raw_id)

    assert fields_of(issues) == ["id"]


@pytest.mark.parametrize("raw_id", ["1", "25", " 26 ", "200"])
def test_check_form_accepts_a_positive_integer_id(module_page, raw_id: str) -> None:
    """正整數放行,前後空白不算數 —— 貼上時多帶一個空白是常態。"""
    assert check_form(module_page, id=raw_id) == []


@pytest.mark.parametrize("raw_tags", ["", "   ", "、", " , ,"])
def test_check_form_requires_at_least_one_tag(module_page, raw_tags: str) -> None:
    """4.6:標籤一個也沒有時指出標籤至少需要一個。分隔符號本身不算一個標籤。"""
    issues = check_form(module_page, tags=raw_tags)

    assert fields_of(issues) == ["tags"]


def test_check_form_attributes_a_broken_fen_to_the_fen_field(module_page) -> None:
    """FEN 結構不合法時,未通過項目定位到 FEN 欄位而不是落在 `null`。"""
    issues = check_form(module_page, fen="4k4/9/9 w - - 0 1")

    assert fields_of(issues) == ["fen"]


def test_check_form_attributes_a_bad_path_to_the_target_field(module_page) -> None:
    """路徑不合格時,未通過項目定位到目標檔案欄位。"""
    issues = check_form(module_page, target="26.json")

    assert fields_of(issues) == ["target"]


def test_check_form_tolerates_missing_keys(module_page) -> None:
    """Preconditions 說「無」:欄位整個不存在也必須有定義的行為,不得拋出。"""
    issues = with_check(module_page, "  return check.checkForm({});")

    assert fields_of(issues) == FORM_FIELD_ORDER


def test_check_form_does_not_reimplement_the_schema(module_page) -> None:
    """**這一層永遠不是放行判準。**

    難度 `7` 與 `max_dtm` 之類的規則屬題目 schema,權威在服務端;淺層檢查看到
    「難度有選」就沒話說。這條斷言是刻意的 —— 它釘住「空清單 != 題目合格」,
    避免日後有人把 schema 規則搬進這一層而製造第二個真相來源。
    """
    assert check_form(module_page, difficulty="7") == []


# --- checkFenStructure:列數、每列格數、走子方(2.4)---------------------


def test_check_fen_structure_accepts_a_real_fen(module_page) -> None:
    """結構合法的 FEN 回 `null`。"""
    assert (
        with_check(module_page, f"  return check.checkFenStructure({js(PUZZLE_FEN)});")
        is None
    )


@pytest.mark.parametrize(
    ("path", "entry"),
    [(path, entry) for path, entry in corpus_entries()],
    ids=[f"{path.name}#{entry['id']}" for path, entry in corpus_entries()],
)
def test_check_fen_structure_accepts_every_fen_in_the_corpus(
    module_page, path: pathlib.Path, entry: dict
) -> None:
    """結構檢查訂得過緊的回歸網:題庫裡的每一個 FEN 都必須通過。

    這一層擋下去的東西使用者就送不出,誤擋一個合法局面的代價高於漏過一個 ——
    合法性本來就由引擎判定。
    """
    assert (
        with_check(
            module_page, f"  return check.checkFenStructure({js(entry['fen'])});"
        )
        is None
    )


@pytest.mark.parametrize(
    "fen",
    [
        "4k4/9/9/9/9/9/9/9/4K4 w - - 0 1",  # 9 列
        "4k4/9/9/9/9/9/9/9/9/9/4K4 w - - 0 1",  # 11 列
        "4k4 w - - 0 1",  # 1 列
    ],
)
def test_check_fen_structure_rejects_a_wrong_number_of_ranks(
    module_page, fen: str
) -> None:
    """2.4:列數不是 10 就不是一個盤面。"""
    issue = with_check(module_page, f"  return check.checkFenStructure({js(fen)});")

    assert issue is not None
    assert issue["field"] == "fen"


@pytest.mark.parametrize(
    "fen",
    [
        "4k3/9/9/9/9/9/9/9/9/4K4 w - - 0 1",  # 第一列只有 8 格
        "4k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1",  # 第一列 10 格
        "4k4/9/9/9/9/9/9/9/9/4K44 w - - 0 1",  # 最後一列超寬
    ],
)
def test_check_fen_structure_rejects_a_rank_with_the_wrong_file_count(
    module_page, fen: str
) -> None:
    """2.4:每一列都必須恰好 9 格 —— 少一格會讓整列的子往旁邊平移。"""
    issue = with_check(module_page, f"  return check.checkFenStructure({js(fen)});")

    assert issue is not None
    assert issue["field"] == "fen"


@pytest.mark.parametrize(
    "fen",
    [
        "4k4/9/9/9/9/9/9/9/9/4K4",  # 只有盤面段
        "4k4/9/9/9/9/9/9/9/9/4K4 x - - 0 1",  # 走子方不是 w/b
        "4k4/9/9/9/9/9/9/9/9/4K4 W - - 0 1",  # 大寫不算
        "4k4/9/9/9/9/9/9/9/9/4K4 red - - 0 1",
    ],
)
def test_check_fen_structure_requires_a_side_to_move_field(
    module_page, fen: str
) -> None:
    """2.6 的前提:起手方沒有獨立輸入,只能自 FEN 讀 —— 那一欄缺了或不認得就是缺漏。"""
    issue = with_check(module_page, f"  return check.checkFenStructure({js(fen)});")

    assert issue is not None
    assert issue["field"] == "fen"


@pytest.mark.parametrize("fen", ["", "   ", "\n"])
def test_check_fen_structure_reports_an_empty_fen(module_page, fen: str) -> None:
    """空字串是有定義的輸入,回一項未通過(必填);2.5 的空盤面呈現由組裝層決定。"""
    issue = with_check(module_page, f"  return check.checkFenStructure({js(fen)});")

    assert issue is not None
    assert issue["field"] == "fen"


def test_check_fen_structure_accepts_a_black_to_move_fen(module_page) -> None:
    """題庫容得下黑先的排局,結構檢查不得假設紅先。"""
    fen = "3ak4/3RaR3/4b3N/6N2/2b6/9/3pP4/B3C1n1B/2rp2r2/4K4 b - - 0 1"

    assert (
        with_check(module_page, f"  return check.checkFenStructure({js(fen)});") is None
    )


# --- sideFromFen:起手方顯示字樣(2.6)-----------------------------------


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1", "紅先"),
        ("4k4/9/9/9/9/9/9/9/9/4K4 b - - 0 1", "黑先"),
        ("4k4/9/9/9/9/9/9/9/9/4K4", None),  # 沒有走子方欄位
        ("4k4/9/9/9/9/9/9/9/9/4K4 x - - 0 1", None),  # 認不得的走子方
        ("", None),
        ("   ", None),
    ],
)
def test_side_from_fen_reads_the_side_to_move_field(
    module_page, fen: str, expected: str | None
) -> None:
    """2.6:起手方由 FEN 的走子方欄位決定,判不出來時回 `null` 而不是猜一個。"""
    assert (
        with_check(module_page, f"  return check.sideFromFen({js(fen)});") == expected
    )


def test_side_from_fen_agrees_with_the_corpus(module_page) -> None:
    """題庫每一題的起手方都讀得出來,且與 FEN 那一欄一致。"""
    fens = [entry["fen"] for _, entry in corpus_entries()]
    expected = ["紅先" if fen.split(" ")[1] == "w" else "黑先" for fen in fens]

    got = with_check(
        module_page, f"  return {js(fens)}.map(fen => check.sideFromFen(fen));"
    )

    assert got == expected


# --- checkTargetPath:題庫目錄內、書目資料夾內、題目檔(5.1、5.2、5.3)---


@pytest.mark.parametrize(
    "target",
    [
        "適情雅趣~卷一/26.json",
        "適情雅趣~卷二/100-104.json",
        " 適情雅趣~卷一/26.json ",  # 前後空白
        "橘中秘/卷一/1.json",  # 更深的層次一樣在書目資料夾內
    ],
)
def test_check_target_path_accepts_a_path_inside_a_source_folder(
    module_page, target: str
) -> None:
    """5.1:路徑相對於題庫根目錄,位於書目資料夾內的題目檔一律放行。

    判準是**結構**(至少兩段 + `.json`),不是已知資料夾的白名單 —— 收下一本書時
    不該回頭改這個函式。
    """
    assert (
        with_check(module_page, f"  return check.checkTargetPath({js(target)});")
        is None
    )


def test_check_target_path_accepts_every_existing_corpus_file(module_page) -> None:
    """題庫中每一個既有題目檔的相對路徑都必須通過 —— 判準與現實同形的回歸網。"""
    relatives = sorted(
        str(path.relative_to(POSITIONS_DIR).as_posix())
        for path in POSITIONS_DIR.rglob("*.json")
    )

    rejected = with_check(
        module_page,
        f"  return {js(relatives)}.filter(p => check.checkTargetPath(p) !== null);",
    )

    assert relatives, "題庫必須至少有一個題目檔,否則這條回歸網是空的"
    assert rejected == []


@pytest.mark.parametrize("target", ["26.json", "./26.json"])
def test_check_target_path_rejects_a_file_in_the_corpus_root(
    module_page, target: str
) -> None:
    """5.2:直接躺在題庫根目錄的題目沒有出處可言 —— 出處由資料夾表達。

    後端的 `_source_of_path()` 也會擋下同一件事;此處是為了在送出前就給回饋。
    """
    issue = with_check(module_page, f"  return check.checkTargetPath({js(target)});")

    assert issue is not None
    assert issue["field"] == "target"


@pytest.mark.parametrize(
    "target",
    [
        "../26.json",
        "適情雅趣~卷一/../../26.json",
        "..",
        "/tmp/26.json",  # 絕對路徑
        "/適情雅趣~卷一/26.json",
    ],
)
def test_check_target_path_rejects_a_path_that_leaves_the_corpus(
    module_page, target: str
) -> None:
    """5.3:跳出題庫目錄的路徑一律擋下。

    平台本身也跳不出使用者選定的目錄樹(控制代碼只在其內解析),此處是為了給出
    清楚的訊息,不是唯一防線。
    """
    issue = with_check(module_page, f"  return check.checkTargetPath({js(target)});")

    assert issue is not None
    assert issue["field"] == "target"


@pytest.mark.parametrize(
    "target",
    [
        "適情雅趣~卷一/26",  # 沒有副檔名
        "適情雅趣~卷一/26.txt",
        "適情雅趣~卷一/26.json.bak",
        "適情雅趣~卷一/",  # 只到資料夾
        "適情雅趣~卷一//26.json",  # 空白段落
    ],
)
def test_check_target_path_rejects_a_path_that_is_not_a_puzzle_file(
    module_page, target: str
) -> None:
    """目標必須是題目檔:題庫的題目檔一律是 `.json`。"""
    issue = with_check(module_page, f"  return check.checkTargetPath({js(target)});")

    assert issue is not None
    assert issue["field"] == "target"


@pytest.mark.parametrize("target", ["", "   "])
def test_check_target_path_reports_an_empty_path(module_page, target: str) -> None:
    """5.1:路徑沒填就沒有寫入的目標,回一項未通過。"""
    issue = with_check(module_page, f"  return check.checkTargetPath({js(target)});")

    assert issue is not None
    assert issue["field"] == "target"


# --- parseTags:標籤輸入的切分 ------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("解殺還殺、鐵門栓", ["解殺還殺", "鐵門栓"]),
        ("解殺還殺,鐵門栓", ["解殺還殺", "鐵門栓"]),
        ("解殺還殺,鐵門栓", ["解殺還殺", "鐵門栓"]),
        ("解殺還殺 鐵門栓", ["解殺還殺", "鐵門栓"]),
        ("  解殺還殺 、 鐵門栓  ", ["解殺還殺", "鐵門栓"]),  # 空白與空項去掉
        ("解殺還殺、、鐵門栓", ["解殺還殺", "鐵門栓"]),
        ("馬後炮", ["馬後炮"]),
        ("", []),
        ("   ", []),
        ("、,,", []),
    ],
)
def test_parse_tags_splits_trims_and_drops_empties(
    module_page, raw: str, expected: list[str]
) -> None:
    """標籤輸入切成陣列,去除空白與空項;逗號(半形、全形)、頓號與空白都是分隔符號。"""
    assert with_check(module_page, f"  return check.parseTags({js(raw)});") == expected


def test_parse_tags_agrees_with_the_corpus_tags(module_page) -> None:
    """題庫既有的標籤都能原樣被切出來 —— 分隔符號訂得過寬會把一個標籤切成兩個。"""
    tag_lists = [entry["tags"] for _, entry in corpus_entries()]
    joined = ["、".join(tags) for tags in tag_lists]

    assert (
        with_check(module_page, f"  return {js(joined)}.map(raw => check.parseTags(raw));")
        == tag_lists
    )


# --- suggestDescription:描述的建議值(3.6、3.7)------------------------


@pytest.mark.parametrize(
    ("position_id", "expected"),
    [
        (1, "適情雅趣 第一局 局名"),
        (10, "適情雅趣 第一〇局 局名"),  # 逐字,不是「十」
        (20, "適情雅趣 第二〇局 局名"),
        (21, "適情雅趣 第二一局 局名"),
        (25, "適情雅趣 第二五局 局名"),  # 不是「二十五」
        (100, "適情雅趣 第一〇〇局 局名"),
        (200, "適情雅趣 第二〇〇局 局名"),
    ],
)
def test_suggest_description_writes_the_number_digit_by_digit(
    module_page, position_id: int, expected: str
) -> None:
    """3.6:局號採**逐字**中文數字,與既有題目的寫法一致(`25` → 「二五」)。"""
    got = with_check(
        module_page,
        f"  return check.suggestDescription('適情雅趣', {position_id}, '局名');",
    )

    assert got == expected


def test_suggest_description_reproduces_every_existing_description(
    module_page,
) -> None:
    """3.6 的回歸網:題庫既有的每一則描述都必須是建議值本身,逐字相同。

    書名取自書目資料夾名稱在 `~` 之前的部分 —— 資料夾帶卷次(`適情雅趣~卷一`)而
    描述只寫書名。這個換算屬組裝層(tasks 4.3),不是本模組的職責,故在測試裡明寫。
    """
    entries = corpus_entries()
    calls = [
        [path.parent.name.split("~")[0], entry["id"], entry["title"]]
        for path, entry in entries
    ]

    got = with_check(
        module_page,
        f"  return {js(calls)}.map(([source, id, title]) =>\n"
        "    check.suggestDescription(source, id, title));",
    )

    assert got == [entry["description"] for _, entry in entries]


@pytest.mark.parametrize(
    "call",
    [
        "check.suggestDescription('', 25, '患在几席')",  # 沒有書名
        "check.suggestDescription('適情雅趣', 25, '')",  # 沒有局名
        "check.suggestDescription('適情雅趣', 0, '患在几席')",  # 題號不是正整數
        "check.suggestDescription('適情雅趣', -1, '患在几席')",
        "check.suggestDescription('適情雅趣', 2.5, '患在几席')",
        "check.suggestDescription('適情雅趣', NaN, '患在几席')",
        "check.suggestDescription('適情雅趣', '25', '患在几席')",  # 契約是數字
        "check.suggestDescription()",
    ],
)
def test_suggest_description_returns_empty_when_it_cannot_suggest(
    module_page, call: str
) -> None:
    """3.6 只在題號與局名皆已填時才有建議值可言;湊不出來就回空字串,不拋例外。"""
    assert with_check(module_page, f"  return {call};") == ""


# --- 契約與純函式的性質 -------------------------------------------------


def test_module_exports_exactly_the_designed_interface(module_page) -> None:
    """design 的 `CheckModule` 是窮舉清單:多匯出一個就是把別人的職責搬了進來。"""
    names = with_check(module_page, "  return Object.keys(check).sort();")

    assert names == sorted(
        [
            "checkForm",
            "checkFenStructure",
            "checkTargetPath",
            "parseTags",
            "sideFromFen",
            "suggestDescription",
        ]
    )


def test_no_function_throws_on_hostile_input(module_page) -> None:
    """Invariants:不拋出例外 —— 檢查失敗一律以回傳值表達。

    組裝層在每次輸入變動時都會呼叫這些函式,一個例外就會讓整頁停止更新。
    """
    thrown = with_check(
        module_page,
        "  const inputs = [undefined, null, 0, 1, true, {}, [], '', '   ',\n"
        "    'a'.repeat(5000), '/'.repeat(50), '\\u0000', '\\n\\r\\t',\n"
        "    '../../../etc/passwd', '4k4/9', '中文'];\n"
        "  const calls = ['checkForm', 'checkFenStructure', 'checkTargetPath',\n"
        "    'parseTags', 'sideFromFen'];\n"
        "  const bad = [];\n"
        "  for (const name of calls) {\n"
        "    for (const input of inputs) {\n"
        "      try { check[name](input); }\n"
        "      catch (error) { bad.push(`${name}: ${String(error)}`); }\n"
        "    }\n"
        "    try { check.suggestDescription(inputs[0], inputs[1], inputs[2]); }\n"
        "    catch (error) { bad.push(`suggestDescription: ${String(error)}`); }\n"
        "  }\n"
        "  return bad;",
    )

    assert thrown == []


def test_the_same_input_gives_the_same_output(module_page) -> None:
    """純函式:相同輸入必得相同輸出,不讀取也不累積任何狀態。"""
    stable = with_check(
        module_page,
        f"  const values = {js(VALID_FORM)};\n"
        "  const once = JSON.stringify(check.checkForm(values));\n"
        "  check.checkForm({});\n"
        "  const twice = JSON.stringify(check.checkForm(values));\n"
        "  return once === twice && JSON.stringify(values) === "
        f"JSON.stringify({js(VALID_FORM)});",
    )

    assert stable is True


def test_check_module_imports_only_the_fen_module() -> None:
    """design 的 Allowed Dependencies:`check.js` 只向左依賴 `web/fen.js`。

    以原始碼斷言而非執行期行為 —— 一個「載入時不出錯」的 import 在瀏覽器裡看不見,
    但它一樣會把純函式的地位弄丟,也就不可能再用 `page.evaluate()` 單獨驗證。
    """
    source = CHECK_JS.read_text(encoding="utf-8")

    found = re.findall(r"^\s*import\b[^\n]*", source, flags=re.MULTILINE)
    found += re.findall(r"\bimport\s*\(", source)
    illegal = [line for line in found if "'../fen.js'" not in line]

    assert not illegal, f"check.js 只能 import ../fen.js,卻出現:{illegal}"


def test_check_module_touches_neither_dom_nor_network() -> None:
    """`check.js` 是純函式:不碰 DOM、不發請求、不持有可變的模組層狀態。"""
    source = CHECK_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)

    forbidden = [
        token
        for token in (
            "document",
            "window",
            "fetch",
            "XMLHttpRequest",
            "localStorage",
            "showDirectoryPicker",
        )
        if token in code
    ]

    assert not forbidden, f"check.js 不得使用:{forbidden}"
