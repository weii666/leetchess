# engine/ — Pikafish 引擎(第三方,版本锁定)

本目录放置 build-time 工具(`tools/`)所需的 **native Pikafish** binary 与 NNUE 网路。
runtime 的 `pikafish.wasm` 是另一份构件,不在此目录。

## 取得档案

```sh
engine/fetch.sh          # 依当前平台下载并校验 binary + nnue
FORCE=1 engine/fetch.sh  # 强制重新下载
```

- binary、`pikafish.nnue`、`.7z` 皆 **gitignore**,由 `fetch.sh` 按需重建。
- 版本与全部 sha256 锁在 `ENGINE_VERSION`(唯一真相来源)。
  **改动其中任何一个值,`books/` 的判定表全部必须重新产生**(DESIGN.md §4.1)。

## 版本

- Release:`Pikafish-2026-01-02`
- 来源:<https://github.com/official-pikafish/Pikafish/releases/tag/Pikafish-2026-01-02>
- 本机(macOS Apple Silicon)使用 `MacOS/pikafish-apple-silicon`;Linux x86-64 使用 `Linux/pikafish-avx2`。

> DESIGN.md §3 的确定性假设要求跨指令集版本对同一批题目跑 `go nodes` 得到一致 `bestmove`。
> 若 build 机(如本机 apple-silicon)与 CI(如 Linux avx2)用不同 binary,须先验证一致性。

## 授权(GPL v3 / NNUE)—— 合规重点

Pikafish 采 **GPL v3**;NNUE 网路另有其授权(见 `licenses/NNUE-License.md`)。
将 `pikafish.wasm` 送进使用者浏览器**构成散布**,义务见 DESIGN.md §8。

本仓库已履行的部分:

- `licenses/Pikafish-COPYING-GPLv3.txt` — GPL v3 全文(随散布附带)
- `licenses/NNUE-License.md` — NNUE 网路授权
- `licenses/Pikafish-AUTHORS.txt` — 作者列表

**尚待处理(实作 `web/` 时):**

- 发布页面必须附上 GPL v3 全文或连结,并提供对应原始码取得管道
  (指向 <https://github.com/official-pikafish/Pikafish> 及本专案若有的 fork)。
- 若修改引擎(含为 WASM 而做的改动),改动须以 GPL v3 开源。
