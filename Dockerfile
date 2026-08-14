# syntax=docker/dockerfile:1
#
# 正式部署映像(Cloud Run / GKE)。三個階段:
#   engine  取回 Linux 版 Pikafish binary + NNUE 權重(本機開發用的是 macOS arm64 版,
#           兩者不相容,絕不可把 engine/pikafish 從 build context 直接 COPY 進來 —— 見
#           .dockerignore 對它的排除)。
#   deps    以 uv 依 uv.lock 安裝正式依賴(--no-dev,不含 pytest/playwright 等開發群組)。
#   最終階段 只帶執行期需要的東西:.venv、引擎執行檔、service/ 與 web/ 原始碼、題庫。
#
# 目標平台不在此檔寫死。`deploy-cloud-run.sh` 透過 `gcloud run deploy --source .`
# 交給 Cloud Build 建置 —— 建置機本身就是 linux/amd64(GKE 與 Cloud Run 的預設節點
# 架構),不需要另外指定 --platform;未來要切 arm64 節點(Tau T2A / Axion)才需要
# 回來改建置方式。

FROM debian:bookworm-slim AS engine

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl p7zip-full \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /engine
COPY engine/ENGINE_VERSION engine/fetch.sh ./
RUN ./fetch.sh


FROM python:3.12-slim AS deps

# 官方建議的取得方式:只借用 uv 這個 static binary,不切換整個 base image。
# 版本釘死為目前本機使用的版本,與 uv.lock 的 revision 相容,避免建置時期
# 因 uv 自動升級而讀出不同的解析結果。
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --frozen:嚴格照 uv.lock 安裝,lock 檔與 pyproject.toml 不同步時直接失敗,而不是
#   靜默重新解析出建置當下才有的版本組合。
# --no-dev:排除 dev 依賴群組 —— 其中的 playwright 會另外下載瀏覽器執行檔,正式
#   映像不需要,裝了也只是白白拉大體積。
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

# libatomic1:pikafish 的 Linux AVX2 binary 動態連結它,但 python:3.12-slim 沒有
# 內建 —— 缺了它引擎進程連 exec 都會失敗(`error while loading shared libraries:
# libatomic.so.1`),且 EnginePool 的啟動掛鉤會卡在等待 handshake 而非乾脆拋出
# 例外,現象是容器起了卻永遠等不到 uvicorn 監聽 port。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --uid 10001 app

WORKDIR /app

COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# service/config.py 的 DEFAULT_ENGINE_PATH / DEFAULT_POSITIONS_DIR 由
# `Path(__file__).resolve().parent.parent` 推出專案根,因此以下四者的相對位置
# 必須原樣對應 repo 佈局(engine/pikafish、positions/、service/、web/ 同層)。
COPY --from=engine /engine/pikafish /app/engine/pikafish
COPY --from=engine /engine/pikafish.nnue /app/engine/pikafish.nnue
COPY service/ /app/service/
COPY positions/ /app/positions/
COPY web/ /app/web/

RUN chown -R app:app /app
USER app

EXPOSE 8080

# `service.main:app` 以模組路徑匯入,依賴 WORKDIR(/app)与本機 `uv run uvicorn`
# 的執行慣例一致 —— cwd 不是 /app 時匯入會失敗,見 start-dev.sh 的同一慣例。
# PORT 由 Cloud Run 於執行時注入;GKE 未設時退回 8080(對應 EXPOSE)。
CMD ["sh", "-c", "exec uvicorn service.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
