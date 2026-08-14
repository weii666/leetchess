#!/usr/bin/env bash
# 部署/更新 leetchess 的 Cloud Run 服務。
#
# 預設路徑(不加任何 flag):`gcloud run services update` —— 只更新資源設定
# (memory / cpu / 環境變數 / 公開存取),沿用**目前正在跑的 image**,完全不進
# Cloud Build、不推新 image,對 Artifact Registry 的儲存用量是 0。日常調參數
# (例如改 LEETCHESS_POOL_SIZE、MEMORY)都走這條路。
#
# --build:改用 `gcloud run deploy --source .`,交給 Cloud Build 重新建置整個
# image 並推新版本到 Artifact Registry —— 只有 Dockerfile 或會進 image 的內容
# (service/、web/、positions/、engine/ENGINE_VERSION、engine/fetch.sh、
# pyproject.toml、uv.lock)真的改了,才需要這個 flag。**第一次部署也必須加這個
# flag**:服務還不存在,沒有現成的 image 可以 update。
#
# 設定來源(依優先序):
#   1. 呼叫前已 export 的環境變數
#   2. .env(本機專屬,已 gitignore,見 env.example)
#   3. 下面列的預設值
#
# 第一次使用:
#   cp env.example .env
#   # 編輯 .env,填入 GCP_PROJECT_ID
#   ./deploy-cloud-run.sh --build
#
# 用法:
#   ./deploy-cloud-run.sh            只更新設定,不重建 image(日常用這個)
#   ./deploy-cloud-run.sh --build    重新建置並部署新 image(改了程式碼/Dockerfile 才需要)

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f .env ]; then
  # shellcheck source=/dev/null
  source .env
fi

: "${GCP_PROJECT_ID:?未設定 GCP_PROJECT_ID —— 執行 cp env.example .env 並填入專案 ID,或以環境變數傳入}"

REGION="${REGION:-asia-east1}"
SERVICE_NAME="${SERVICE_NAME:-leetchess}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-1}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-true}"
LEETCHESS_POOL_SIZE="${LEETCHESS_POOL_SIZE:-1}"

BUILD=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    *)
      echo "未知參數:$arg(僅接受 --build)" >&2
      exit 1
      ;;
  esac
done

COMMON_FLAGS=(
  --project="$GCP_PROJECT_ID"
  --region="$REGION"
  --memory="$MEMORY"
  --cpu="$CPU"
  --set-env-vars="LEETCHESS_POOL_SIZE=${LEETCHESS_POOL_SIZE}"
  --quiet
)

# 公開存取(allUsers 的 run.invoker)是 IAM binding,不是資源設定,`gcloud run
# services update` 不吃 --allow-unauthenticated —— 只有 `deploy` 支援這個捷徑
# flag。兩條路徑因此分開處理,IAM binding 用同一個 gcloud 指令對兩條路徑都適用。
apply_iam_binding() {
  local action="add-iam-policy-binding"
  [ "$ALLOW_UNAUTHENTICATED" = "true" ] || action="remove-iam-policy-binding"
  gcloud run services "$action" "$SERVICE_NAME" \
    --project="$GCP_PROJECT_ID" \
    --region="$REGION" \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --quiet
}

if [ "$BUILD" = "1" ]; then
  echo "建置並部署 ${SERVICE_NAME} 到專案 ${GCP_PROJECT_ID}(${REGION},pool_size=${LEETCHESS_POOL_SIZE})..."
  AUTH_FLAG="--allow-unauthenticated"
  [ "$ALLOW_UNAUTHENTICATED" = "true" ] || AUTH_FLAG="--no-allow-unauthenticated"
  gcloud run deploy "$SERVICE_NAME" --source . "$AUTH_FLAG" "${COMMON_FLAGS[@]}"
else
  echo "更新 ${SERVICE_NAME} 的設定(不重建 image,不影響 Artifact Registry 用量)..."
  gcloud run services update "$SERVICE_NAME" "${COMMON_FLAGS[@]}"
  apply_iam_binding
fi
