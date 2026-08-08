"""前端測試的 Playwright 夾具(design 的 Testing Strategy)。

前端是 vanilla JS 且不引入 node 工具鏈,因此沒有 JS 測試框架;所有前端 AC 的驗證
都經由此處的夾具在真實瀏覽器中完成 —— 純函式以 `page.evaluate()` 執行並取回結果,
互動與失敗路徑則以真實操作與 `page.route()` 攔截驗證。

**此模組不在頂層 import playwright**。既有 335 個測試與瀏覽器無關,而 conftest 會在
每次 collection 被匯入;把 playwright 的匯入延到夾具真正被要求時才發生,不需要瀏覽器
的測試就完全不付這個代價。同理,瀏覽器只在第一個要求它的測試出現時才啟動。

夾具由 `tests/conftest.py` 匯入而註冊 —— pytest 只會自動載入名為 `conftest.py` 的檔案,
本檔的名稱來自 design 的 File Structure Plan,並非 pytest 的自動載入慣例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import pytest

if TYPE_CHECKING:  # 僅供型別標註,執行期不匯入 playwright
    from playwright.sync_api import Browser, Page


@pytest.fixture(scope="session")
def browser() -> Iterator["Browser"]:
    """整個測試 session 共用一個 chromium。

    啟動瀏覽器約需數百毫秒,每個測試各啟一次會讓前端測試無謂地變慢;
    共用實例、每個測試各開一個獨立的 context(見 `browser_page`)即可維持隔離。
    只安裝了 chromium 一種瀏覽器,跨瀏覽器差異不在本專案的驗證範圍內。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def browser_page(browser: "Browser") -> Iterator["Page"]:
    """一個乾淨的分頁,測試結束後連同 context 一併關閉。

    每個測試取得自己的 browser context,因此 cookie、storage 與 `page.route()`
    的攔截規則都不會外溢到其他測試。
    """
    context = browser.new_context()
    try:
        yield context.new_page()
    finally:
        context.close()
