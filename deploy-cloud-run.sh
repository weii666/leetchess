#!/usr/bin/env bash
# 部署 leetchess 到 Cloud Run。
#
# 不需要本機 Docker:`gcloud run deploy --source .` 把原始碼上傳給 Cloud Build,
# 由它在雲端照 Dockerfile 建置(建置機本身是 linux/amd64,與 Cloud Run 目標架構
# 一致,見 Dockerfile 開頭的說明)。
#
# 設定來源(依優先序):
#   1. 呼叫前已 export 的環境變數
#   2. .env(本機專屬,已 gitignore,見 env.example)
#   3. 下面列的預設值
#
# 第一次使用:
#   cp env.example .env
#   # 編輯 .env,填入 GCP_PROJECT_ID
#   ./deploy-cloud-run.sh
#
# 用法:
#   ./deploy-cloud-run.sh

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

AUTH_FLAG="--allow-unauthenticated"
if [ "$ALLOW_UNAUTHENTICATED" != "true" ]; then
  AUTH_FLAG="--no-allow-unauthenticated"
fi

echo "部署 ${SERVICE_NAME} 到專案 ${GCP_PROJECT_ID}(${REGION},pool_size=${LEETCHESS_POOL_SIZE})..."

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project="$GCP_PROJECT_ID" \
  --region="$REGION" \
  --memory="$MEMORY" \
  --cpu="$CPU" \
  --set-env-vars="LEETCHESS_POOL_SIZE=${LEETCHESS_POOL_SIZE}" \
  "$AUTH_FLAG" \
  --quiet
