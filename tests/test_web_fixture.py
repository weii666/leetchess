"""Playwright 測試基礎設施的煙霧測試(tasks 1.1)。

前端定案為 vanilla JS 且不引入任何 node 工具鏈,因此沒有 JS 測試框架可用;
requirements 的 38 條 AC 全部依賴「能在真實瀏覽器中執行 JS 並取回結果」這件事成立。
本檔驗證的就是這個前提本身:夾具能開瀏覽器、能載入頁面、能執行 JS 並回傳值。

之後的純函式測試(tasks 2.1、2.2)沿用同一組夾具,只是把 evaluate 的內容換成
`web/` 底下的模組。
"""

from __future__ import annotations


def test_browser_page_evaluates_js_and_returns_the_value(browser_page) -> None:
    """`page.evaluate()` 能在瀏覽器內執行 JS 並把結果帶回 Python。"""
    assert browser_page.evaluate("() => 6 * 7") == 42


def test_browser_page_returns_structured_values_from_js(browser_page) -> None:
    """回傳值不限於純量 —— 後續測試要斷言的是盤面陣列與記譜字串這類結構。"""
    result = browser_page.evaluate(
        "() => ({ ok: true, squares: ['a0', 'b0'], count: [1, 2, 3].length })"
    )
    assert result == {"ok": True, "squares": ["a0", "b0"], "count": 3}


def test_browser_page_can_load_html_and_query_the_dom(browser_page) -> None:
    """夾具給的是一個真的頁面,DOM 可載入也可查詢(tasks 3.x 的互動測試需要)。"""
    browser_page.set_content(
        "<main id='board'><span class='piece'>帥</span>"
        "<span class='piece'>將</span></main>"
    )
    assert browser_page.locator("#board .piece").count() == 2
    assert browser_page.locator("#board .piece").first.inner_text() == "帥"


def test_browser_page_can_run_an_es_module(browser_page) -> None:
    """`web/` 以 ES modules 交付,夾具必須能載入 module 型別的 script。"""
    browser_page.set_content("<div id='out'></div>")
    browser_page.add_script_tag(
        content=(
            "const label = ['紅', '方'].join('');\n"
            "document.getElementById('out').textContent = label;"
        ),
        type="module",
    )
    assert browser_page.locator("#out").inner_text() == "紅方"
