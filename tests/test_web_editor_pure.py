"""收題頁純函式的驗證(design 的 Testing Strategy「純函式(`page.evaluate()`)」)。

`web/editor/check.js` 與 `web/editor/corpus-file.js` 都不碰 DOM、不發請求、不持有
狀態,因此不必走完整的頁面流程 —— 把模組載進真實瀏覽器、以 `page.evaluate()` 呼叫
並把結果取回 Python 比對即可。這也順帶證明了 design 的依賴方向:`check.js` 只向左
依賴 `web/fen.js`、`corpus-file.js` 一個都不依賴,單獨載入就能運作。

供檔方式與 `tests/test_web_pure.py` 相同(`page.route()` 就地供檔,不啟動伺服器
進程),理由亦同 —— Chromium 不允許自 `file://` 匯入 ES module,而前端以 ES modules
交付且無建置步驟,所以模組必須在一個真的 http 來源下被載入。

## 這一層永遠不是放行判準

`check.js` 只做淺層檢查(填了沒、形狀對不對)。候選題目是否合格由服務端的
`POST /api/editor/validate` 判定,兩者若對同一個輸入給出不同結論**以服務端為準**。
本檔因此刻意有一條反向斷言(見 `test_check_form_does_not_reimplement_the_schema`):
淺層檢查放行的東西不代表題目合格。

## 既有題目逐字不變是**構造上**的事實

`corpus-file.js` 的追加走文字層(research.md 的 Decision 2):`JSON.parse` 只用來驗證
目標檔是不是一個陣列,它的結果**不參與輸出**。本檔的 5.7 斷言因此不是「序列化器有
沒有剛好還原既有內容」,而是「輸出的前綴是不是既有全文的位元組」——
見 `test_append_position_keeps_the_existing_bytes_as_a_prefix` 與
`test_append_position_does_not_reformat_existing_content`,後者刻意餵一份排版與題庫
慣例完全不同的既有內容:任何「解析後整份重寫」的實作都會在那裡轉紅。
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
CORPUS_FILE_JS = WEB_DIR / "editor" / "corpus-file.js"
POSITIONS_DIR = PROJECT_ROOT / "positions"

#: 一個不會真的解析出去的網域 —— 所有請求都被 `page.route()` 攔下就地供檔。
ORIGIN = "https://web-editor-pure.test"

#: 《適情雅趣》第 25 局的起始局面(題庫裡的 `適情雅趣~卷一`)。
PUZZLE_FEN = "2Rakc3/4aR3/4b1n2/4C4/6b2/2B6/2P1c4/2n1B3C/1r2A1p2/4KA3 w - - 0 1"

#: 一份**淺層檢查全數通過**的表單。各測試以覆寫單一欄位的方式製造缺漏,
#: 因此每條斷言都只有一個變因。
VALID_FORM = {
    "id": "26",
    "title": "患在几席",
    "difficulty": "1",
    "tags": "解殺還殺、鐵門栓",
    "fen": PUZZLE_FEN,
}

#: 表單欄位的順序。`checkForm()` 的清單順序必須與此一致,8.4 的呈現才有穩定次序。
FORM_FIELD_ORDER = ["id", "title", "difficulty", "tags", "fen"]


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


def corpus_files() -> list[pathlib.Path]:
    """題庫中的每一個題目檔。"""
    return sorted(POSITIONS_DIR.rglob("*.json"))


def corpus_entries() -> list[tuple[pathlib.Path, dict]]:
    """題庫中的每一題,連同它所在的檔案路徑。

    以真實題庫當回歸樣本:描述的寫法與 FEN 的形狀一旦與既有題目分家,這裡先紅。
    """
    entries: list[tuple[pathlib.Path, dict]] = []
    for path in corpus_files():
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


def test_check_form_ignores_keys_that_are_no_longer_fields(module_page) -> None:
    """表單多帶一個本模組不認得的鍵時,**不生任何未通過項目**。

    `description` 與 `target` 曾經是欄位(requirements R5 與 R3 的修訂已移除)。
    組裝層若在改版途中還多送著它們,這一層應該視而不見,而不是憑空報一項錯 ——
    欄位清單的權威在本模組,不在呼叫端送了什麼。
    """
    issues = check_form(module_page, description="", target="26.json")

    assert fields_of(issues) == []


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


# --- 契約與純函式的性質 -------------------------------------------------


def test_module_exports_exactly_the_designed_interface(module_page) -> None:
    """design 的 `CheckModule` 是窮舉清單:多匯出一個就是把別人的職責搬了進來。"""
    names = with_check(module_page, "  return Object.keys(check).sort();")

    assert names == sorted(
        [
            "checkForm",
            "checkFenStructure",
            "parseTags",
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
        "  const calls = ['checkForm', 'checkFenStructure', 'parseTags'];\n"
        "  const bad = [];\n"
        "  for (const name of calls) {\n"
        "    for (const input of inputs) {\n"
        "      try { check[name](input); }\n"
        "      catch (error) { bad.push(`${name}: ${String(error)}`); }\n"
        "    }\n"
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


# =========================================================================
# corpus-file.js:題目的序列化與文字層追加(5.4–5.9、7.4、7.5)
# =========================================================================


#: 一題**新收的**題目。欄位集合即題目 schema 的人工編輯欄位 —— 沒有 `max_dtm`
#: (由 corpus-verification 回填)、沒有 `source`(由資料夾表達)、沒有
#: `side_to_move`(由 FEN 表達)。多寫任何一個都會讓服務啟動時拒絕整個題庫。
NEW_ENTRY = {
    "id": 26,
    "title": "患在几席",
    "description": "適情雅趣 第二六局 患在几席",
    "fen": PUZZLE_FEN,
    "difficulty": 1,
    "tags": ["解殺還殺", "鐵門栓"],
}

#: `NEW_ENTRY` 序列化後應有的文字,逐字寫死。
#:
#: 刻意不由程式產生期望值:期望值若也是「照排版規則組出來的」,兩邊會一起錯。
#: 這一段是照著題庫裡一個真實的題目檔手抄的 —— 兩格縮排、欄位各自
#: 一行、`tags` 單行、中文不轉義。
NEW_ENTRY_TEXT = (
    "  {\n"
    '    "id": 26,\n'
    '    "title": "患在几席",\n'
    '    "description": "適情雅趣 第二六局 患在几席",\n'
    f'    "fen": "{PUZZLE_FEN}",\n'
    '    "difficulty": 1,\n'
    '    "tags": ["解殺還殺", "鐵門栓"]\n'
    "  }"
)


def with_corpus(page, body: str):
    """把 `body` 當成函式本體執行,其中 `corpus` 已綁定為 `corpus-file.js` 的匯出。"""
    return page.evaluate(
        "async () => {\n"
        "  const corpus = await import('/editor/corpus-file.js');\n" + body + "\n}"
    )


def serialize(page, entry: dict) -> str:
    return with_corpus(page, f"  return corpus.serializePosition({js(entry)});")


def append(page, existing: str | None, entry: dict) -> str:
    return with_corpus(
        page, f"  return corpus.appendPosition({js(existing)}, {js(entry)});"
    )


def append_error(page, existing: str | None, entry: dict) -> dict | None:
    """呼叫 `appendPosition()` 並把拋出的例外攤成可比對的資料;沒拋出時回 `None`。"""
    return with_corpus(
        page,
        f"  try {{\n"
        f"    corpus.appendPosition({js(existing)}, {js(entry)});\n"
        "    return null;\n"
        "  } catch (error) {\n"
        "    return {\n"
        "      name: error.name,\n"
        "      isCorpusFileError: error instanceof corpus.CorpusFileError,\n"
        "      isError: error instanceof Error,\n"
        "      message: String(error.message),\n"
        "    };\n"
        "  }",
    )


def entry_blocks(path: pathlib.Path) -> list[str]:
    """`entry_blocks_of()`,但讀的是題目檔。"""
    return entry_blocks_of(path.read_text(encoding="utf-8"))


def entry_blocks_of(text: str) -> list[str]:
    """把一份題目檔全文切成「每一題的原始文字」,不含分隔用的逗號。

    以行為單位切:題目物件的 `{` 與 `}` 固定縮排兩格,而 JSON 的字串裡不可能出現
    未轉義的換行,所以行的邊界就是可信的邊界。切出來的片段是**原文的位元組**,
    序列化的比對因此比對的是現實而不是另一份規則。
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.split("\n"):
        if line == "  {":
            current = [line]
        elif current is not None:
            # 收尾的 `},` 那個逗號是**陣列的分隔符號**,不屬於這一題。
            if line in ("  }", "  },"):
                current.append("  }")
                blocks.append("\n".join(current))
                current = None
            else:
                current.append(line)
    return blocks


def without_max_dtm(block: str) -> str:
    """把一題的原始文字去掉 `max_dtm` 那一行。

    `max_dtm` 是驗證工具回填的欄位,收題工具**不寫出**它(5.9)。既有題庫裡有帶
    `max_dtm` 的題目,回歸比對因此比的是「原檔片段扣掉 `max_dtm` 之後」——
    這條測試證明的是排版與欄位順序,不是本工具能還原一份它本來就不該寫的欄位。

    `max_dtm` 是物件的最後一欄,拿掉之後前一行行尾的逗號就多出來了。
    """
    lines = block.split("\n")
    kept = [line for line in lines if not line.lstrip().startswith('"max_dtm"')]
    if len(kept) != len(lines):
        kept[-2] = kept[-2].rstrip(",")
    return "\n".join(kept)


def schema_fields_only(entry: dict) -> dict:
    """一題扣掉不由本工具寫出的欄位。"""
    return {name: value for name, value in entry.items() if name != "max_dtm"}


# --- serializePosition:排版與既有題目檔同形(5.8、5.9)-------------------


def test_serialize_position_matches_the_hand_written_text(module_page) -> None:
    """一題的文字:兩格縮排、欄位各自一行、`tags` 單行、中文不轉義。"""
    assert serialize(module_page, NEW_ENTRY) == NEW_ENTRY_TEXT


@pytest.mark.parametrize(
    ("path", "entry"),
    [(path, entry) for path, entry in corpus_entries()],
    ids=[f"{path.name}#{entry['id']}" for path, entry in corpus_entries()],
)
def test_serialize_position_reproduces_every_entry_in_the_corpus(
    module_page, path: pathlib.Path, entry: dict
) -> None:
    """5.8 的回歸網:既有題庫檔中的**每一題**重新序列化後與原檔片段逐字相同。

    這是排版漂移的警報器 —— 縮排、欄位順序、`tags` 是否單行、中文是否轉義,任何
    一項與現實分家都會在這裡先紅,而不是等到新題進了題庫才被人看出來。

    帶 `max_dtm` 的題目比對的是扣掉那一行的片段(見 `without_max_dtm`)。
    """
    blocks = entry_blocks(path)
    index = [item["id"] for item in json.loads(path.read_text(encoding="utf-8"))].index(
        entry["id"]
    )

    got = serialize(module_page, schema_fields_only(entry))

    assert got == without_max_dtm(blocks[index])


def test_entry_blocks_agrees_with_the_parsed_corpus() -> None:
    """切片器本身的檢查:切出來的每一段都必須是那一題的完整 JSON。

    沒有這一條,上面那個回歸網可能是在跟一份切壞的期望值比對。
    """
    for path in corpus_files():
        entries = json.loads(path.read_text(encoding="utf-8"))
        blocks = entry_blocks(path)

        assert len(blocks) == len(entries), path
        assert [json.loads(block) for block in blocks] == entries, path


def test_the_corpus_still_contains_an_entry_with_max_dtm() -> None:
    """`without_max_dtm()` 不得靜默地變成一個什麼都沒做的函式。

    題庫若哪天一題 `max_dtm` 都沒有了,上面那條回歸網就不再證明「本工具不寫出
    `max_dtm`」——這一條會先紅,提醒人把 5.9 的證據補在別處。
    """
    with_max_dtm = [entry for _, entry in corpus_entries() if "max_dtm" in entry]

    assert with_max_dtm, "題庫應至少有一題帶 max_dtm,否則 5.9 的回歸網是空的"


def test_serialize_position_keeps_tags_on_one_line(module_page) -> None:
    """`tags` 維持單行 —— `JSON.stringify(value, null, 2)` 會把它攤成多行。"""
    text = serialize(module_page, NEW_ENTRY)
    tag_lines = [line for line in text.split("\n") if '"tags"' in line]

    assert len(tag_lines) == 1
    assert tag_lines[0] == '    "tags": ["解殺還殺", "鐵門栓"]'


def test_serialize_position_does_not_escape_chinese(module_page) -> None:
    """中文不轉義:題目檔是給人讀的,`\\u60a3` 那種寫法沒有人核對得了。"""
    text = serialize(module_page, NEW_ENTRY)

    assert "患在几席" in text
    assert "\\u" not in text


def test_serialize_position_writes_only_schema_fields(module_page) -> None:
    """5.9:只寫出題目 schema 的人工編輯欄位。

    `max_dtm` 由 corpus-verification 回填、`source` 由資料夾表達、`side_to_move`
    由 FEN 表達 —— 後兩者根本不是欄位,寫出去會讓服務啟動時拒絕整個題庫。就算
    呼叫端塞了進來,序列化也不得把它們帶出去。
    """
    text = serialize(
        module_page,
        {**NEW_ENTRY, "max_dtm": 16, "source": "適情雅趣", "side_to_move": "w"},
    )

    assert "max_dtm" not in text
    assert "source" not in text
    assert "side_to_move" not in text
    assert text == NEW_ENTRY_TEXT


def test_serialize_position_omits_a_field_the_entry_does_not_have(module_page) -> None:
    """`description` 選填之後(3.10),沒有它的題目就少寫一行 —— 其餘完全不變。

    收題頁自 R3 的修訂起不再產生描述,所以這是**新題的常態形狀**。斷言的是「把
    描述那一行從預期文字裡拿掉」之後逐字相同:縮排、逗號的位置、`tags` 仍在同一行,
    全都不因為少了一個欄位而漂移。
    """
    entry = {name: value for name, value in NEW_ENTRY.items() if name != "description"}

    text = serialize(module_page, entry)

    expected = "\n".join(
        line for line in NEW_ENTRY_TEXT.split("\n") if '"description"' not in line
    )
    assert text == expected
    assert "description" not in text


@pytest.mark.parametrize(
    ("case", "value"),
    [("整個沒有這個鍵", None), ("鍵在但值是 undefined", "undefined")],
    ids=["整個沒有這個鍵", "鍵在但值是 undefined"],
)
def test_an_absent_and_an_undefined_field_are_treated_alike(
    module_page, case: str, value: str | None
) -> None:
    """兩者都算「沒有這個欄位」,而**後者是呼叫端最容易產生的形狀**。

    判準若寫成 `in`,`{description: undefined}` 會走進序列化,而
    `JSON.stringify(undefined)` 回的是 `undefined` 而不是一個字串 —— 產出的會是
    `"description": undefined`,一份解析不開的 JSON,下一次會讓服務啟動不起來。
    """
    entry = {name: v for name, v in NEW_ENTRY.items() if name != "description"}
    literal = js(entry)
    if value is not None:
        literal = literal[:-1] + ", description: undefined }"

    text = with_corpus(module_page, f"  return corpus.serializePosition({literal});")

    assert "description" not in text, case
    assert json.loads(f"[{text}]"), f"{case}:產出的不是合法 JSON"


def test_serialize_position_writes_fields_in_the_corpus_order(module_page) -> None:
    """欄位順序與既有題目檔一致 —— 順序不同不是錯誤,但會讓每一題長得不一樣。"""
    text = serialize(module_page, NEW_ENTRY)
    names = re.findall(r'^    "([a-z_]+)":', text, flags=re.MULTILINE)

    assert names == ["id", "title", "description", "fen", "difficulty", "tags"]


def test_serialize_position_escapes_a_description_with_newlines(module_page) -> None:
    """3.8:描述可以含換行,而換行必須被轉義 —— 一個欄位一行的排版才站得住。"""
    entry = {**NEW_ENTRY, "description": "第一行\n第二行"}

    text = serialize(module_page, entry)

    assert "\\n" in text
    assert len(text.split("\n")) == len(NEW_ENTRY_TEXT.split("\n"))
    assert json.loads(text) == entry


@pytest.mark.parametrize(
    "entry",
    [
        {**NEW_ENTRY, "title": '引號 " 與反斜線 \\'},
        {**NEW_ENTRY, "tags": ["單一標籤"]},
        {**NEW_ENTRY, "tags": ["一", "二", "三", "四"]},
        {**NEW_ENTRY, "description": ""},
        {**NEW_ENTRY, "id": 200, "difficulty": 3},
    ],
)
def test_serialize_position_always_produces_parsable_json(
    module_page, entry: dict
) -> None:
    """序列化的結果必須解得回原本那一題,一個欄位都不差。"""
    assert json.loads(serialize(module_page, entry)) == entry


def test_serialize_position_does_not_mutate_the_entry(module_page) -> None:
    """純函式:不改呼叫端交進來的物件。"""
    unchanged = with_corpus(
        module_page,
        f"  const entry = {js(NEW_ENTRY)};\n"
        "  const before = JSON.stringify(entry);\n"
        "  corpus.serializePosition(entry);\n"
        "  return before === JSON.stringify(entry);",
    )

    assert unchanged is True


# --- appendPosition:三種形態(5.4、5.5)---------------------------------


def test_append_position_creates_a_single_element_array(module_page) -> None:
    """5.4:目標檔不存在時,輸出是只含新題一個元素的題目陣列。"""
    got = append(module_page, None, NEW_ENTRY)

    assert got == f"[\n{NEW_ENTRY_TEXT}\n]\n"
    assert json.loads(got) == [NEW_ENTRY]


@pytest.mark.parametrize("existing", ["[]", "[]\n", "[\n]\n", "[ ]\n"])
def test_append_position_inserts_into_an_empty_array_without_a_comma(
    module_page, existing: str
) -> None:
    """空陣列沒有前一個元素,**不補逗號** —— 補了就是一份解不開的 JSON。"""
    got = append(module_page, existing, NEW_ENTRY)

    assert json.loads(got) == [NEW_ENTRY]
    assert ",\n  {" not in got


def test_append_position_appends_to_a_non_empty_array(module_page) -> None:
    """5.5:目標檔已存在時,新題附加到陣列的**末端**,前一個元素補上逗號。"""
    # 取題庫裡第一個題目檔而不是寫死某一個檔名:題目檔會被併檔改名
    # (`25.json` 併進了 `25-48.json`),寫死的路徑會在那一天無聲地變成 FileNotFound。
    existing = corpus_files()[0].read_text(encoding="utf-8")

    got = append(module_page, existing, NEW_ENTRY)

    assert json.loads(got) == json.loads(existing) + [NEW_ENTRY]
    assert got.endswith(f",\n{NEW_ENTRY_TEXT}\n]\n")


@pytest.mark.parametrize(
    "path", corpus_files(), ids=[path.name for path in corpus_files()]
)
def test_append_position_ends_with_a_newline(module_page, path: pathlib.Path) -> None:
    """輸出以換行結尾,與既有題目檔一致。"""
    existing = path.read_text(encoding="utf-8")

    assert append(module_page, existing, NEW_ENTRY).endswith("\n")
    assert append(module_page, None, NEW_ENTRY).endswith("\n")


# --- appendPosition:5.7 是構造上的事實 -----------------------------------


@pytest.mark.parametrize(
    "path", corpus_files(), ids=[path.name for path in corpus_files()]
)
def test_append_position_keeps_the_existing_bytes_as_a_prefix(
    module_page, path: pathlib.Path
) -> None:
    """5.7、7.4、7.5:輸出以既有全文的**每一個位元組**為前綴,直到收尾的 `]` 之前。

    這不是「序列化器剛好還原了既有內容」,而是既有位元組**沒有被重寫的機會**
    (research.md 的 Decision 2)。斷言因此比的是位元組層級的前綴關係,不是解析後
    的等價 —— 後者連「整份重新排版」都會通過。

    收尾的 `]` 與其前後的空白不在前綴內:分隔用的逗號要插在最後一題的 `}` 之後,
    那個位置本來就只有空白。既有的**每一題**因此逐字不變。
    """
    existing = path.read_text(encoding="utf-8")
    kept = existing[: existing.rindex("]")].rstrip()

    got = append(module_page, existing, NEW_ENTRY)

    assert kept.endswith("}"), "前提:非空的題目檔以某一題的 } 收尾"
    assert got.startswith(kept)
    # 被換掉的只有收尾那一段空白與 `]`,別的一個位元組都沒動。
    assert existing[len(kept) :].strip() == "]"


@pytest.mark.parametrize(
    "path", corpus_files(), ids=[path.name for path in corpus_files()]
)
def test_append_position_keeps_every_existing_entry_verbatim(
    module_page, path: pathlib.Path
) -> None:
    """7.4:寫入後既有的每一題逐字不變,連 `max_dtm` 那種本工具不寫的欄位也在。"""
    existing = path.read_text(encoding="utf-8")

    got = append(module_page, existing, NEW_ENTRY)

    assert entry_blocks(path) == entry_blocks_of(got)[: len(entry_blocks(path))]


def test_append_position_does_not_reformat_existing_content(module_page) -> None:
    """5.7 的關鍵樣本:既有內容的排版與題庫慣例完全不同,也一個位元組都不准動。

    任何「`JSON.parse` 之後整份重新序列化」的實作都會在這裡轉紅 —— 它會把這一行
    壓縮的 JSON 攤成兩格縮排的樣子,雖然解析後仍然等價。
    """
    existing = '[{"id":1,"title":"甲","tags":["x"],"nonsense":true}]'

    got = append(module_page, existing, NEW_ENTRY)

    assert got == f'[{{"id":1,"title":"甲","tags":["x"],"nonsense":true}},\n{NEW_ENTRY_TEXT}\n]\n'
    assert json.loads(got) == json.loads(existing) + [NEW_ENTRY]


def test_append_position_is_stable_across_repeated_appends(module_page) -> None:
    """連續收題:第二次追加的輸出仍以第一次的輸出為前綴。"""
    once = append(module_page, None, NEW_ENTRY)
    second = {**NEW_ENTRY, "id": 27}

    twice = append(module_page, once, second)

    assert twice.startswith(once[: once.rindex("]")].rstrip())
    assert json.loads(twice) == [NEW_ENTRY, second]


@pytest.mark.parametrize(
    "path", corpus_files(), ids=[path.name for path in corpus_files()]
)
def test_appending_every_entry_from_scratch_rebuilds_the_corpus_file(
    module_page, path: pathlib.Path
) -> None:
    """把一個既有題目檔的每一題依序追加一遍,得到的全文與原檔逐字相同。

    這是排版的端到端證據:縮排、逗號的位置、收尾的 `]` 與結尾換行全部包含在內。
    帶 `max_dtm` 的題目扣掉那一行(5.9 說本工具不寫出它),因此期望值是
    「原檔扣掉 `max_dtm` 那幾行」而不是原檔本身。
    """
    entries = json.loads(path.read_text(encoding="utf-8"))
    expected = (
        "[\n"
        + ",\n".join(without_max_dtm(block) for block in entry_blocks(path))
        + "\n]\n"
    )

    text = None
    for entry in entries:
        text = append(module_page, text, schema_fields_only(entry))

    assert text == expected


# --- appendPosition:目標檔不是題目陣列(5.6)----------------------------


@pytest.mark.parametrize(
    "existing",
    [
        "{}",  # 物件不是陣列
        '{"positions": []}',
        '"文字"',
        "42",
        "null",
        "true",
        "[",  # 解不開
        '[{"id": 1},]',  # 尾隨逗號不是標準 JSON
        "// 註解\n[]",
        "",  # 空檔案
        "   \n",
        "不是 JSON",
    ],
)
def test_append_position_rejects_content_that_is_not_a_position_array(
    module_page, existing: str
) -> None:
    """5.6:目標檔內容不是題目陣列時以專屬錯誤表達,**不寫入**。

    專屬錯誤是為了讓組裝層分得出「這個檔不能寫」與「寫入本身失敗」——
    兩者的訊息不一樣,而使用者能自己處理的只有前者。
    """
    thrown = append_error(module_page, existing, NEW_ENTRY)

    assert thrown is not None, f"{existing!r} 不是題目陣列,必須拋出"
    assert thrown["isCorpusFileError"] is True
    assert thrown["isError"] is True
    assert thrown["name"] == "CorpusFileError"
    assert thrown["message"].strip()


def test_append_position_accepts_an_array_of_anything(module_page) -> None:
    """5.6 的判準止於「是不是陣列」。

    元素是不是合格的題目由服務端判定(`POST /api/editor/validate`),本模組在這裡
    多加一道只會製造第二個真相來源,還會讓一個本來寫得進去的檔案被擋下。
    """
    assert append(module_page, "[1, 2]", NEW_ENTRY).startswith("[1, 2")


# --- 契約與純函式的性質 -------------------------------------------------


def test_corpus_file_module_exports_exactly_the_designed_interface(module_page) -> None:
    """design 的 `CorpusFileModule` 加上 `CorpusFileError` 是窮舉清單。"""
    names = with_corpus(module_page, "  return Object.keys(corpus).sort();")

    assert names == sorted(["CorpusFileError", "appendPosition", "serializePosition"])


def test_corpus_file_module_has_no_imports() -> None:
    """design 的 Dependencies 寫的是「無」:`corpus-file.js` 不依賴任何模組。

    以原始碼斷言而非執行期行為 —— 一個「載入時不出錯」的 import 在瀏覽器裡看不見,
    但它一樣會把這個模組的獨立性弄丟。
    """
    source = CORPUS_FILE_JS.read_text(encoding="utf-8")

    found = re.findall(r"^\s*import\b[^\n]*", source, flags=re.MULTILINE)
    found += re.findall(r"\bimport\s*\(", source)

    assert not found, f"corpus-file.js 不得 import 任何東西,卻出現:{found}"


def test_corpus_file_module_touches_neither_dom_nor_network() -> None:
    """純函式:不碰 DOM、不發請求、不碰檔案系統 —— 落盤是 `fs.js` 的事。"""
    source = CORPUS_FILE_JS.read_text(encoding="utf-8")
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
            "createWritable",
        )
        if token in code
    ]

    assert not forbidden, f"corpus-file.js 不得使用:{forbidden}"


def test_appending_the_same_input_gives_the_same_output(module_page) -> None:
    """純函式:相同輸入必得相同輸出,不讀取也不累積任何狀態。"""
    stable = with_corpus(
        module_page,
        f"  const existing = {js('[]')};\n"
        f"  const entry = {js(NEW_ENTRY)};\n"
        "  const once = corpus.appendPosition(existing, entry);\n"
        "  corpus.appendPosition(once, entry);\n"
        "  const twice = corpus.appendPosition(existing, entry);\n"
        "  return once === twice;",
    )

    assert stable is True
