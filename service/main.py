"""FastAPI 應用的組裝:三個端點、啟動與關閉掛鉤、`ServiceError` 的錯誤映射。

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

## 錯誤處理

此處只註冊 `ServiceError` 的映射:七種錯誤類別各自的 HTTP 狀態與 `{code, message}`
形狀。**未捕捉例外一律轉 `INTERNAL`、`RequestValidationError` 的契約形狀,以及
不外洩路徑與堆疊的驗證,屬任務 4.3**,不在此完成。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import anyio.to_thread
from anyio import CapacityLimiter
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from service.config import Settings, load_settings
from service.engine.pool import EnginePool
from service.errors import ServiceError
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

router = APIRouter(prefix="/api")


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


# --- 錯誤映射(最小集合;完整處理見任務 4.3) ---------------------------


def _handle_service_error(request: Request, exc: ServiceError) -> Response:
    """把服務例外映射為它自己宣告的 HTTP 狀態與 `{code, message}`(5.1)。

    狀態碼取自 `ServiceError.http_status`,訊息取自 `ErrorResponse.from_error`——
    兩者都以 `errors.py` 為唯一來源,此處不再複述任何一條映射規則,否則兩處遲早
    漂移。
    """
    del request  # 錯誤回應不因請求內容而異,也不回放請求內容。
    error = ErrorResponse.from_error(exc)
    return JSONResponse(
        status_code=exc.http_status, content=error.model_dump(mode="json")
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
    # 型別檢查器只認 `(Request, Exception) -> Response`;此處刻意收窄為 ServiceError,
    # 由 starlette 依例外類別分派保證實際傳進來的一定是它。
    app.add_exception_handler(ServiceError, _handle_service_error)  # type: ignore[arg-type]
    return app


#: 供 `uvicorn service.main:app` 使用的應用實例。設定於啟動掛鉤讀取。
app = create_app()
