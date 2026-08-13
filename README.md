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

## 授權

本專案原始碼採 MIT License(見 [`LICENSE`](./LICENSE))。內含的 Pikafish 引擎與 NNUE 評估網路各自另有授權(GPL v3、非商業限定),不受本專案授權涵蓋,詳見 [`engine/licenses/`](./engine/licenses/)。
