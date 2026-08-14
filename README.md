# LeetChess

象棋排局(紅先必勝)練習服務——開頁面就能練,[Pikafish](https://github.com/official-pikafish/Pikafish) 引擎在伺服器端當陪練。

## 需求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 安裝

```sh
uv sync
```

## 執行

```sh
./start-dev.sh
```

開啟 <http://127.0.0.1:8123>。

## 測試

```sh
uv run pytest          # 快速套件,略過需要真實引擎或長逾時的測試
uv run pytest --slow   # 完整套件
```

需要真實引擎的測試(以及 `--slow`)得先下載 Pikafish binary 與 NNUE 評估網路:

```sh
engine/fetch.sh
```

瀏覽器端測試需要 Playwright 的 Chromium:

```sh
uv run playwright install chromium
```

## 部署(Cloud Run)

```sh
cp env.example .env   # 填入 GCP_PROJECT_ID,.env 已 gitignore,不進版本庫
./deploy-cloud-run.sh          # 只更新設定(memory / cpu / max-instances / 環境變數 / 公開存取)
./deploy-cloud-run.sh --build  # 重新建置 image 並部署
```

兩條路徑不是等價的,選錯不會報錯、只會讓改動悄悄沒生效:

- **改了 `Dockerfile`、`service/`、`web/`、`positions/`、`engine/ENGINE_VERSION`、`pyproject.toml`、`uv.lock` 這類會進 image 的內容 → 必須 `--build`。**
  不加的話 `update` 路徑沿用現有 image,你的改動不會出現在線上,也不會有任何錯誤訊息提醒你。第一次部署(服務還不存在)也一定要加。
- **只是調 `.env` 裡的資源設定(`LEETCHESS_POOL_SIZE`、`MEMORY`、`MAX_INSTANCES` 等)→ 用預設的 `update`,不要加 `--build`。**
  `--build` 每次都會觸發完整 Cloud Build(上傳原始碼、重新建置、推新 image),比 `update` 慢上好幾分鐘,且 `Dockerfile` 的 base image 版本是浮動的,沒事重建有極小機率因上游套件版本飄移而跟前一次建置不完全一樣。Artifact Registry 已設 [cleanup policy](./artifact-registry-cleanup-policy.json)(保留最近 3 個版本,無 tag 的舊 image 一天後自動清除),所以重複 `--build` 不會讓儲存空間無限增長,但仍不是零代價,能用 `update` 就不要 `--build`。

## 授權

本專案原始碼採 MIT License(見 [`LICENSE`](./LICENSE))。內含的 Pikafish 引擎與 NNUE 評估網路各自另有授權(GPL v3、非商業限定),不受本專案授權涵蓋,詳見 [`engine/licenses/`](./engine/licenses/)。
