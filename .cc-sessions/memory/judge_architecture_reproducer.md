---
name: judge-architecture-reproducer
description: "RFCGym 的 Judge 设计 —— Judge 不是审稿人而是\"复现实验者\",根据 Agent 报告生成 validator 代码并执行验证"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3f10c679-71f1-4505-8d6f-73e9a4a6ace8
---

Judge-as-Reproducer 架构:Agent(通用编码 agent 如 Claude Code/Codex/mini-SWE)自由探索后产出自由格式报告;Judge 的工作不是审阅报告,而是 (1) 提取 claim → (2) 为每个 claim 生成 validator 代码 → (3) 在干净环境执行 → (4) 根据 assertion 结果裁决。

**Why:** 用户明确否定了两种之前的设计:
1. 不能假设 Agent 输出结构化 trace —— 通用编码 Agent 输出是自由 markdown
2. Judge 不能只读报告评分 —— 必须主动复现才能防 Agent 幻觉
"只信代码执行结果"是这个范式的核心。

**How to apply:**
- 讨论 Judge 时默认这个范式:claim extraction → validator synthesis → execution → verdict
- Verdict 由 assertion pass/fail 驱动,不是 Judge 主观打分
- 每个 verdict 都附带 standalone validator 代码作为产物,可入 validator 库 / 回归测试
- Validator 必须先在 baseline(已知合规 / 已知漏洞样本)上自检
- 研究问题围绕:validator synthesis 成功率、正确性、抗幻觉、跨 Agent 公平性
- 关联:[[project-research-direction]] 的 validator 库由此自然累积;[[project-rfcgym]] 黑盒约束依然成立
