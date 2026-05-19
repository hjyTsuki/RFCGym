# .cc-sessions

Claude Code 会话历史快照。从 `~/.claude/projects/D--Research-Code-RFCGym/` 拷贝而来。

## 内容

- `*.jsonl` — 每个 UUID 对应一次会话,JSONL 一行一条消息(user / assistant / tool result)
- `<uuid>/tool-results/` — 该会话内被读取的 PDF 等外部产物的缓存
- `memory/` — Claude 跨会话的持久记忆
  - `MEMORY.md` 是索引,其余 `.md` 是分主题记录(user 画像、项目方向、judge 架构等)

## 用途

- 复盘需求演化与决策过程
- 给后来者快速了解项目思路(memory/ 里有提炼)
- 供 LLM 训练/评测做 trace 分析

## 注意

公开仓快照,已扫过 API key / private key / 凭证等敏感模式,均未命中。后续若新增会话,记得再扫一遍再 push。
