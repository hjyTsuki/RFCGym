---
name: project-research-direction
description: "RFCGym 的研究方向 —— 基于 CVE outcome pattern 构建 validator 库,并研究 LLM-as-Judge 在协议 fuzzing grader 中的合理性"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3f10c679-71f1-4505-8d6f-73e9a4a6ace8
---

研究方向:从大量 CVE 中挖掘 outcome pattern → 沉淀为可执行的 validator 库 → 在此之上研究 LLM-as-Judge 作为 fuzzing grader 的合理性。

**Why:** 黑盒协议 fuzzing 的核心瓶颈是 grader/oracle 设计(无源码、无插桩,只能靠外部可观察信号 + 规范对照)。用户希望系统化地从历史漏洞中归纳模式,而不是靠手工经验积累 detector。

**How to apply:**
- 涉及 grader / oracle / validator 设计时,优先沿"pattern 挖掘 → DSL spec → 可执行 validator → LLM 上层判别"这条路线讨论
- 讨论 LLM-as-Judge 时,区分"检测层(不合理)"与"分诊/解释/聚合层(合理)";混合架构是默认推荐
- 评估方法默认包含:ground truth 回放、跨模型一致性、与人类专家对齐、对抗鲁棒性、置信度校准
- 关联记忆:[[project-rfcgym]] 是黑盒 + 仅 RFC 规范的约束
