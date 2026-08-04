# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

**当前仓库只有 `DESIGN.md`,尚无任何代码、构建脚本或依赖清单。** 项目处于设计定案阶段,下面的目录结构、命令和文件均为 `DESIGN.md` 规划的目标形态,实现时才会出现。动手前请先读 `DESIGN.md`——它记录了架构决策和**已被否决的方案及理由**(第 2 节),目的就是避免重新讨论已定案的事。

## 项目是什么

象棋排局(红先必胜)练习程式。使用者执红,引擎执黑当陪练。纯前端 WASM,无后端,静态托管。

## 核心架构:两种计算的分工

整个设计围绕一条分界线,理解它才能读懂其余部分:

- **黑方应手 = 引擎当下 live 计算**(Pikafish WASM 在浏览器内跑)。这是产品体验。
- **红方走脱判定(胜负事实)= 离线预解成判定表,runtime 查表**。这是客观事实,live 推导不可靠。

绝不可混淆:黑方走法不查表(明确否决完整解树),胜负也不 live 算(明确否决前端自行判定胜负)。

## 三个不可动摇的约束

写代码时若与这些冲突,是代码错,不是约束错:

1. **确定性**:同一题重复挑战,黑方必须走出同一步。因此引擎调用固定为 `Threads=1`、`Hash=128`、`go nodes 3000000`(**不是 `go movetime`**,墙钟时间不确定)、每次重送完整 `position fen <fen> moves <...>`。引擎完全 stateless,悔棋/跳步/重来都只是重送一次。

2. **胜负判定不自己实作**:以引擎回传(`bestmove (none)`、mate 分数)为唯一真相来源。判定表存布尔值(红方是否仍必胜),不存走法。`cp` 分数**不可据以判定走脱**,仅供显示。

3. **循环规则(长将/长捉/一将一杀)是核心机制**,不是边缘情况。判定表产生时必须完整实作并与 Pikafish 规则书对齐——只需对齐一次,错了在 build time 由 CI 抓到。

## 目标目录结构(规划中)

    engine/     引擎版本锁定:fetch.sh 下载 binary+nnue 验 checksum,ENGINE_VERSION 存版本
    tools/      build-time,唯一用到 Pikafish native 之处:solve.py(盘面→判定表)、verify.py(验红胜+规则一致)
    positions/  题目 metadata,一题一档(如 0001.json),进 git,人工编辑
    books/      判定表(如 0001.verdict.json),CI 产出,gitignore 或 LFS
    web/        纯前端,无后端

**`positions/` 与 `books/` 必须分开**:solver 会反复重跑(换引擎版本、调参数),题目本身不该跟着动。引擎版本或 nnue 一换,所有判定表必须重新产生。

`engine/` 的 binary 与 nnue 皆 gitignore,版本锁在纯文字 `ENGINE_VERSION`,两者一起验 checksum。

## Python 執行環境:uv + venv

正式開發時,專案的 Python(`tools/` 下的腳本等)一律以 **uv** 管理、在專案本地 **venv** 內執行,不使用系統全域 Python:

- root 置 `pyproject.toml`(鎖 `requires-python`)+ `.python-version`,依賴鎖進 `uv.lock`,一併進版本庫。
- `uv sync` 建立/同步 `.venv`(uv 自動管理的 venv,不另手動維護);`.venv/` 進 `.gitignore`。
- 一律走 `uv run <script.py>` 呼叫,不直接 `python3 <script.py>`,確保 Python 版本與依賴可重現、且與系統環境隔離。

> POC 的 `poc/server.py` 為零依賴、純標準庫的一次性工具,不受此約束,沿用直接執行即可。

## 走法格式约定

UCI 座标,非中文记谱。档 `a`–`i`(红方左至右),列 `0`–`9`(红方底线为 0)。例:炮二平五 = `h2e2`。前端负责双向转换。

## 引擎抽象介面

引擎存取一律经过单一介面,实作可抽换,目前仅规划 WASM 实作:

    EngineAdapter.best_move(fen, moves, nodes) -> (uci_move, score)

## 法律约束(GPL v3)

送 `pikafish.wasm` 进使用者浏览器构成散布。页面必须附 GPL v3 全文或连结、提供对应原始码取得管道;若修改引擎,改动须以 GPL v3 开源。

## 开工前应先做的验证

`DESIGN.md` 第 3 节标注为「优先做」:跨不同 native 指令集版本(avx2 vs sse41)对十来个代表性排局跑同样 `go nodes`,比对 `bestmove` 是否完全一致。若不成立,整个确定性假设要重想。
