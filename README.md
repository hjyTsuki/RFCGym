# RFCGym

**让 Code Agent 在真实环境里基于 RFC 规范挖协议漏洞,而不是用一次性 Prompt Workflow 攻一种固定场景。**

## 问题

现有协议漏洞研究有两个明显缺口:

1. **场景一次性**:大多数工作针对单一固定场景做专用设计(如 HTTP/3→HTTP/1.1 转换、SMTP+SPF+Forwarding),缺少"协议规范输入 → PoC 输出"的泛化方法。
2. **缺 Agent × 环境交互**:LLM 在协议安全上的应用多停留在 Prompt Workflow,没有让 Agent 与真实协议栈多轮交互(发包、读响应、查文档、调工具)去探索漏洞。

## 核心 Idea

把协议漏洞挖掘统一建模为**规范驱动的开放式交互搜索问题**:

```
RFC → 可执行协议语义表示 → 假设生成 → 环境交互验证 → 证据归因 PoC
```

围绕这条主线构建两套能力:

- **Scaling**:协议关系建模为有向图(`version_of` / `translatable` / `composes_with` / `embeds` / `dispatches_to` / `layered_on`),随机游走抽取 1~3 个协议组合,LLM 判定是否构成可攻击场景并匹配相关论文,产出 `SCN-*.md` 场景草稿。
- **Runtime**:每个场景配套 Dockerized 协议栈,Agent 在其中尝试触发语义模糊点,产出 PoC(描述 + 攻击原语 + 执行脚本)。

## 评测两阶段

- **Stage 1 — 攻击是否成功**:Agent 自报的 testcase 实际执行,基于规则判定 True/False,避开 LLM-as-Judge 的循环自证。
- **Stage 2 — 是否合理 / 是否新 CVE**:对成功 PoC 做去重与新颖性判断。Judge 不"审稿",而是为 Agent 报告中的每个 claim 生成 validator 并实际执行,用证据替代主观判断(见 `memory/judge_architecture_reproducer.md`)。

## 漏洞分类(指导场景合成)

| 类别 | 例子 |
|---|---|
| 协议自身缺陷 | CORS 扩张信任边界 → CVE-2018-20744 |
| 单协议多实现语义偏差 | HDiff (HTTP)、H3Act (HTTP/3→1.1)、MIME 解析歧义 |
| 跨协议组合语义错配 | Composition Kills (SPF Return-Path vs From) |

## 仓库结构

```
upstream/           # 场景合成:协议图 + 随机游走 + LLM 判定
  data_sources/     #   seed_graph.yaml — 手工整理的协议节点与关系
  graph/            #   builder/schema/visualize
  sampler/          #   random_walk
  scenario_synthesizer/  # prompt 构建
scenarios/          # 已落地的 SCN-*.md 场景与已知攻击
pipeline/           # 基于 CVE-Factory 改造的环境搭建 + Agent 运行
CVE-Factory/        # 上游 CVE-Factory 内联(去除 cve_tasks/)作为参考实现
docker/             # 复现环境 (h3act-client / mock-cdn / mock-origin / certs)
Paper/              # 参考文献与 deep research 报告
docs/               # 环境与设计文档
```

## 当前进度

- ✅ 协议图 v1(~25 节点),random walker,prompt builder
- ✅ Stage 1 场景 `SCN-HTTP3-CDN-RANGE` 与 known-attacks 列表
- 🟡 接入 LLM SDK 自动产出 `SCN-*.md`,与现有 `scenarios/` 去重
- 🟡 Judge 的 validator 库与执行框架
- ⬜ Wireshark dissector 源码抽取 → 自动补图
- ⬜ 真实流行度加权(W3Techs CDN 占比、pcap 统计)

## 快速上手

```bash
pip install networkx pyyaml

# 看图统计
python -m upstream.cli stats

# 抽 5 条随机游走作为场景候选
python -m upstream.cli walk --n 5 --seed 42

# 看每个游走会发给 LLM 的 prompt
python -m upstream.cli walk --n 3 --seed 42 --print-prompt
```
