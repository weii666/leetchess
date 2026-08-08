"""FastAPI 應用的組裝:三個端點、前端靜態檔的掛載、啟動與關閉掛鉤、`ServiceError`
的錯誤映射。

依賴方向為 `types / errors -> config -> positions / engine -> game -> models -> main`,
本模組位於最右端:可向左匯入全部,但**沒有任何模組匯入它**。這使 app 的組裝方式
與各層的實作互不牽制。

## 這裡是 composition root

題庫、引擎池、對局服務三者都在此建立並接起來,各層自己不建立協作者。因此
`main.py` 天然持有題庫的參照,`GET /api/positions/{id}` 的「題目資訊」直接由題庫
取得 —— `GameService.start()` 只回傳起始局面與其對局狀態,而 6.1 要的是**起始局面
與對局所需的題目資訊**(局名、難度、標籤、出處……)。為此擴充 `GameService` 的
簽章只會讓對局判定去背一份與判定無關的展示資料。

## 路由一律同步 `def`,不是 `async def`

引擎等待是阻塞的 pipe read。同步路由由框架自動丟進 threadpool 執行,阻塞因此不會
卡住事件迴圈;寫成 `async def` 則會讓單一次引擎搜尋凍結整個服務。

## threadpool 容量必須大於引擎池容量

引擎池是**唯一**的併發閘門(design「併發閘門的位置」)。threadpool 容量若不大於
池容量,請求會在觸及池之前先被 threadpool 卡住 —— 那裡沒有等待上限、也沒有錯誤
語意,使用者等到的不是「服務忙碌」而是不明的延遲,`SERVICE_BUSY` 因此與資源實況
脫節。容量在啟動掛鉤內套用到框架實際使用的 limiter,見 `_configure_threadpool()`。

## 著法格式驗證攔在路由函式之前

請求模型宣告為**主體參數**,驗證因此發生在路由函式被呼叫**之前**:格式不合法的
請求從未進入函式本體,連借引擎的那行程式碼都沒機會執行(5.2)。若改成在函式內
手動 `model_validate`,借用可能已經先發生,3.3 的容量就被無效請求吃掉了。

## 錯誤處理:三個處理器,一個出口

**進到端點之後**的失敗有三種來源,對外只有一種形狀 —— `{code, message}`,類別碼恆為
七種之一(5.1)。三個處理器把三種來源收斂到同一個 `_error_response()`:

- `ServiceError`:服務層與引擎層自己宣告的失敗,狀態碼與訊息都由例外自己帶。
- `RequestValidationError`:請求形狀不合法。框架原生的 `{"detail": [...]}` 不是本服務
  的契約,client 若要為它另寫一套解析,5.1 的「可程式判別」就落空了。
- `Exception`:沒人預期的失敗,一律轉 `INTERNAL`(500)。

**路由層刻意不在此契約內。** 打不到任何路由(404)與方法不符(405)仍是框架原生的
`{"detail": ...}`。七種類別碼裡沒有一種誠實描述「這個 URL 不存在」:`POSITION_NOT_FOUND`
會告訴 client 題目不見了,而真正的問題是它的網址打錯;`INTERNAL` 則把 client 的筆誤
升級成伺服器故障。要正確表達需要新增第八種類別碼,那是 design 層級的決定。

**前端掛在根路徑上,但看不見 `/api`。** 根掛載本會把 `/api` 的路由層 404 與 405 一併
接走,使上一段的區分消失;`_WebFiles` 因此把整個前綴對靜態檔遮蔽。`web/` 的內容不屬
於本服務,這裡只提供掛載點(`structure.md` 的「`service/` 與 `web/` 的交界」)。

**回應一律不帶例外自身的文字。** 未預期例外的訊息裡就是路徑、堆疊與引擎輸出
(5.4);細節以 `exc_info` 留在服務端日誌,回應只給固定的通用敘述。這條規則對
503 與 504 一樣管用:`ErrorResponse.from_error` 取的是 `ServiceError.message`,不是
`str(exc)`,引擎吐了什麼都到不了使用者眼前。

### 結構性驗證失敗用哪個類別碼

設計的錯誤類別表沒有 422 這一列,API Contract 也只允許 400/404/409/503/504 —— 因此
結構性錯誤必須落在既有的七種之內,見 `_structural_error()` 的分流理由。
"""

from __future__ import annotations

import logging
import pathlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio.to_thread
from anyio import CapacityLimiter
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.routing import Match, Mount
from starlette.types import Scope

from service.config import Settings, load_settings
from service.engine.pool import EnginePool
from service.errors import (
    InternalError,
    InvalidMoveFormatError,
    PositionNotFoundError,
    ServiceError,
)
from service.game import GameService
from service.models import (
    BlackMoveResponse,
    ErrorResponse,
    GameStateResponse,
    MoveSequenceRequest,
    PositionResponse,
)
from service.positions import PositionRepository

__all__ = [
    "MIN_THREADPOOL_CAPACITY",
    "THREADPOOL_HEADROOM_FACTOR",
    "MALFORMED_REQUEST_MESSAGE",
    "API_PREFIX",
    "WEB_DIR",
    "threadpool_capacity",
    "create_app",
    "app",
]


#: threadpool 容量的下限。回合制對局的瞬時並行搜尋數遠低於在線人數,但**等待閘門
#: 的請求同樣佔著執行緒**;容量只比池大一點,池滿時多出來的請求就會退回 threadpool
#: 排隊。執行緒相對於引擎進程(每個常駐一份 NNUE)是廉價資源,故取寬鬆值。
#: 40 亦為 anyio 的預設值,對 3.1 的 30 名同時對局使用者留有餘裕。
MIN_THREADPOOL_CAPACITY = 40

#: 池容量的放大倍率。池開得很大時容量必須跟著長,否則下限反而成了新的閘門。
THREADPOOL_HEADROOM_FACTOR = 4


def threadpool_capacity(pool_size: int) -> int:
    """由引擎池容量算出 threadpool 容量。**恆大於 `pool_size`。**

    這個「恆大於」是 design「併發閘門的位置」的硬性要求,不是調校建議:相等或更小
    時,請求會在觸及池之前先被 threadpool 卡住,服務忙碌的語意與資源實況脫節。
    """
    return max(MIN_THREADPOOL_CAPACITY, pool_size * THREADPOOL_HEADROOM_FACTOR)


# --- 依賴 ---------------------------------------------------------------


def _service(request: Request) -> GameService:
    """取用啟動掛鉤建好的對局服務。"""
    return request.app.state.service


def _repository(request: Request) -> PositionRepository:
    """取用啟動掛鉤建好的題庫。

    路由直接使用題庫是刻意的:題目資訊(局名、難度、標籤、出處)屬展示資料,
    與對局判定無關,不該塞進 `GameService` 的簽章。見模組說明。
    """
    return request.app.state.repository


Service = Annotated[GameService, Depends(_service)]
Repository = Annotated[PositionRepository, Depends(_repository)]

#: 三個端點共用的路徑前綴。前端靜態檔掛在根路徑上,因此這個前綴同時也是
#: 「靜態檔看不見的範圍」,見 `_WebFiles`。
API_PREFIX = "/api"

router = APIRouter(prefix=API_PREFIX)


# --- 端點 ---------------------------------------------------------------


@router.get("/positions/{position_id}")
def read_position(
    position_id: int, repository: Repository, service: Service
) -> PositionResponse:
    """題目的起始局面與對局所需的題目資訊(6.1)。

    題號先向題庫查:不存在時直接回報找不到,不必先付出借引擎的代價(6.2)。
    """
    position = repository.get(position_id)
    # 起始局面取自題目本身(`PositionResponse.from_domain` 讀 `position.fen`),
    # 此處只要它的對局狀態 —— 合法著法必須向引擎問,服務不實作象棋規則。
    _starting_fen, state = service.start(position_id)
    return PositionResponse.from_domain(
        position, state, repository.source_of(position_id)
    )


@router.post("/state")
def read_state(body: MoveSequenceRequest, service: Service) -> GameStateResponse:
    """走法序列走完之後的對局狀態(1.1、1.2、1.3)。

    用途是**重建狀態**(悔棋、重整、載入題目後確認起始局面),不是推進對局 ——
    走完紅方一手後直接呼叫黑方應手端點即可。前端若每手都先查一次局面,通過併發
    閘門的次數會加倍,直接壓縮 3.1 的可承載人數(design「典型流程」)。
    """
    return GameStateResponse.from_domain(service.state(body.position_id, body.moves))


@router.post("/black-move")
def read_black_move(body: MoveSequenceRequest, service: Service) -> BlackMoveResponse:
    """黑方應手、三態諮詢信號,以及**走後**的完整對局狀態(1.4、2.1–2.4)。

    紅方走出致勝一手後,同一份回應裡就是「黑方無著、對局結束、紅方獲勝」——
    那是每一題排局的最後一手,前端不必再查一次局面。
    """
    return BlackMoveResponse.from_domain(
        service.black_reply(body.position_id, body.moves)
    )


# --- 錯誤映射 -----------------------------------------------------------


#: 請求形狀不合法時的對外訊息。**固定字串,不帶任何請求內容** —— 框架的驗證細節
#: 裡有 `input`(原樣回放請求值)與使用者自訂的欄位名,把它們序列化出去等於讓請求
#: 方決定回應內容(5.4)。
MALFORMED_REQUEST_MESSAGE = "請求格式不合法"

#: 驗證錯誤的位置前綴,代表出錯的是路徑參數而非請求主體。
_PATH_LOCATION = "path"

#: 未捕捉例外的服務端日誌。細節只走 `exc_info`,不進回應(5.4)。
_logger = logging.getLogger(__name__)


def _error_response(error: ServiceError) -> Response:
    """所有失敗路徑的**唯一**出口:契約形狀加上例外自己宣告的狀態碼(5.1)。

    狀態碼取自 `ServiceError.http_status`,訊息取自 `ErrorResponse.from_error`——
    兩者都以 `errors.py` 為唯一來源,此處不再複述任何一條映射規則,否則兩處遲早
    漂移。三個處理器共用這一個出口,新增處理器時形狀不必再對一次。
    """
    return JSONResponse(
        status_code=error.http_status,
        content=ErrorResponse.from_error(error).model_dump(mode="json"),
    )


def _handle_service_error(request: Request, exc: ServiceError) -> Response:
    """服務層與引擎層宣告的失敗:狀態碼與訊息都由例外自己帶(5.1)。"""
    del request  # 錯誤回應不因請求內容而異,也不回放請求內容。
    return _error_response(exc)


def _handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    """請求形狀不合法時,改用本服務的契約形狀而非框架原生的 `{"detail": [...]}`。

    形狀不合法與著法不合法是同一件事的兩種表現:**client 送出的請求無法被處理**。
    兩者回不同形狀的話,client 得為錯誤回應寫兩套解析,5.1 的「可程式判別」就落空了。
    """
    del request
    return _error_response(_structural_error(exc))


def _structural_error(exc: RequestValidationError) -> ServiceError:
    """把結構性驗證失敗歸到七種錯誤類別之一。

    ## 為什麼不是新增一種類別碼

    七種類別碼是對外契約(5.1),而 design 的錯誤類別表沒有 422 這一列 —— API
    Contract 對這三個端點只列出 400/404/409/503/504。結構性錯誤因此必須落在既有的
    類別裡,而不是自行擴充契約。

    ## 分流:路徑參數回 404,其餘回 400

    `position_id` 是整組 API **唯一**的路徑參數(`GET /api/positions/{position_id}`),
    因此「路徑參數不是整數」與「沒有這個題目」在此完全等價,client 的處置也相同
    (回題目列表)。回 `INVALID_MOVE_FORMAT` 則是叫 client 去檢查它根本沒送出的
    著法。**日後若新增別的路徑參數,這個等價就不再成立,須回來重新分流。**

    其餘位置(主體、query、header)一律回 `INVALID_MOVE_FORMAT`(400):七種類別碼
    裡只有它落在 400,而 400 正是 API Contract 給輸入錯誤的那一格。訊息另給
    `MALFORMED_REQUEST_MESSAGE` 以與「某一手著法不合法」區分開來 —— 類別碼相同,
    但人看得出差別。
    """
    if any(_is_path_error(error) for error in exc.errors()):
        return PositionNotFoundError()
    return InvalidMoveFormatError(MALFORMED_REQUEST_MESSAGE)


def _is_path_error(error: Mapping[str, Any]) -> bool:
    """該筆驗證錯誤是否出在路徑參數上。"""
    location = error.get("loc", ())
    return bool(location) and location[0] == _PATH_LOCATION


def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """沒人預期的例外一律轉 `INTERNAL`(500),回應不帶例外自身的任何文字(5.4)。

    未預期失敗的訊息裡就是路徑、堆疊與引擎輸出,因此回應走 `InternalError()`——
    它**不接受**呼叫端傳入的訊息,連「順手把細節帶上」這個選項都不存在。要保留
    細節只有例外鏈與日誌兩條路,兩條都留在服務端。

    服務不因此停止:例外已經被攔在這裡,失敗的是這一個請求(4.4)。池中的引擎由
    借用的情境管理器歸還,與本處理器無關 —— 那是語言保證,不是這裡補救得了的事。

    堆疊在日誌裡會出現兩次:此處記一次,外層的 ServerErrorMiddleware 把原例外往外
    拋之後伺服器再記一次。刻意不省 —— 少掉這一筆,日誌就只剩伺服器那份堆疊,看不出
    「這個例外送出去的是哪一種回應」,而未預期失敗本就罕見,重複的代價很小。
    """
    del request
    _logger.error("未捕捉的例外,已轉為 INTERNAL", exc_info=exc)
    return _error_response(InternalError())


# --- 前端靜態檔 ---------------------------------------------------------


#: 前端所在目錄(`web/`)。頁面由本服務掛靜態檔提供,與 API **同源**,因此不需要
#: 任何 CORS 設定,本地開發也只需啟動一個進程(`structure.md`)。
#: 這條掛載是 `service/` 與 `web/` 的唯一實體交界:`web/` 的內容不屬於本服務,
#: 本服務只提供掛載點。
WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"


class _WebFiles(Mount):
    """掛在根路徑上、但**看不見 `/api`** 的靜態檔路由。

    根掛載對任何路徑都是 FULL 比對,而 starlette 的 FULL 一律勝過 PARTIAL ——
    `GET /api/state`(端點只收 POST)本該由路由層回 405,被靜態檔接走之後會退化成
    「找不到這個檔案」。前端因此再也分不出「網址打錯」與「方法用錯」,而那兩種
    形狀正是 `api.js` 要辨識的第二類錯誤。

    讓 `/api` 開頭的路徑對本路由不可見,方法不符與網址打錯就仍由路由層自己回答。
    這也使正確性不再取決於「掛載寫在 `include_router` 後面」這個順序 —— 順序哪天
    被調換,端點也不會安靜地被靜態檔吃掉。
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope.get("type") == "http" and _is_api_path(scope):
            return Match.NONE, {}
        return super().matches(scope)


def _is_api_path(scope: Scope) -> bool:
    """該請求是否落在 API 的路徑前綴底下。

    比對的是**去掉 `root_path` 之後**的路徑:服務若被掛在某個子路徑後面,
    `scope["path"]` 會帶著那段前綴,直接比對會使遮蔽失效。
    """
    path: str = scope.get("path", "")
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def _mount_web(app: FastAPI) -> None:
    """把 `web/` 掛在根路徑上。

    `html=True` 使目錄請求落到該目錄的 `index.html`,根路徑因此直接就是對局頁面。
    目錄不存在時 `StaticFiles` 會在此當場拋出而不是靜默略過 —— `web/` 是進版本庫的
    交付物,缺了它服務就沒有前端可提供,晚一點才以 404 現形只會更難查。
    """
    app.router.routes.append(
        _WebFiles("/", app=StaticFiles(directory=WEB_DIR, html=True), name="web")
    )


# --- 生命週期 -----------------------------------------------------------


def _configure_threadpool(pool_size: int) -> CapacityLimiter:
    """把 threadpool 容量套用到框架**實際使用的** limiter。

    必須在事件迴圈內執行:預設 limiter 綁在當前迴圈上,迴圈外取到的不是同一個。
    啟動掛鉤本身就跑在迴圈裡,故此處是唯一正確的時機。
    """
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = threadpool_capacity(pool_size)
    return limiter


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """啟動掛鉤建立題庫索引與引擎池,關閉掛鉤釋放全部引擎進程。

    題庫掃描與引擎啟動都是阻塞操作,此處直接同步執行:啟動期間尚無請求可服務,
    讓它們阻塞事件迴圈沒有代價,而任一失敗都應該直接使服務拒絕啟動(題庫 schema
    不符、重複題號、設定矛盾、引擎握手失敗)。

    池的關閉放在 `finally`:啟動之後的任何離開路徑 —— 正常關閉、掛鉤中的例外、
    伺服器崩潰前的收尾 —— 都必須收掉引擎進程。每個殘留進程常駐一份 51MB NNUE,
    漏掉的代價會隨每次重啟疊加。
    """
    settings: Settings = app.state.settings or load_settings()
    app.state.settings = settings

    repository = PositionRepository(settings.positions_dir)
    repository.load()

    pool = EnginePool(
        size=settings.pool_size,
        acquire_timeout=settings.acquire_timeout,
        engine_path=settings.engine_path,
    )
    app.state.repository = repository
    app.state.pool = pool
    app.state.service = GameService(repository, pool, settings)
    app.state.threadpool_limiter = _configure_threadpool(settings.pool_size)
    try:
        yield
    finally:
        pool.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    """組裝應用。協作者於啟動掛鉤建立,此處只接線。

    `settings` 留空時於啟動掛鉤才讀環境變數,而不是在匯入時 —— 匯入時就讀,等於
    讓「什麼時候 import 這個模組」影響服務的設定,測試與部署都會因此不可預期。
    """
    app = FastAPI(
        title="leetchess engine-service",
        description="象棋排局的引擎後端:起始局面、局面查詢、黑方應手。",
        lifespan=_lifespan,
    )
    # 掛鉤尚未跑完之前這些欄位不存在,明確填 None 使誤用得到清楚的失敗而非 AttributeError。
    app.state.settings = settings
    app.state.repository = None
    app.state.pool = None
    app.state.service = None
    app.state.threadpool_limiter = None

    app.include_router(router)
    # 型別檢查器只認 `(Request, Exception) -> Response`;前兩個處理器刻意收窄型別,
    # 由 starlette 依例外類別分派保證實際傳進來的一定是它。
    app.add_exception_handler(ServiceError, _handle_service_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    # `Exception` 這一條是最後一道網,連前兩個處理器自己炸掉都接得住 —— 它由外層的
    # ServerErrorMiddleware 處理,而那一層在送出本回應之後仍會把原例外往外拋,好讓
    # 伺服器留下完整堆疊。外洩與否只看送出去的這份回應,堆疊留在服務端正是要的。
    app.add_exception_handler(Exception, _handle_unexpected_error)
    # 前端掛在最後:比對順序上端點永遠先被試過,而 `_WebFiles` 另外把 `/api` 整段
    # 遮掉,兩層各自成立 —— 前者是慣例,後者是保證。
    _mount_web(app)
    return app


#: 供 `uvicorn service.main:app` 使用的應用實例。設定於啟動掛鉤讀取。
app = create_app()
