---
name: project-rfcgym
description: "RFCGym 项目的 fuzzing 约束 —— 黑盒测试,只有协议输入规范,无源码"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6e7756f3-ff3c-4aaf-a229-950009dcdb7d
---

RFCGym 项目的核心约束:**黑盒 fuzzing,只有协议输入规范,拿不到目标源码**。

**Why:** 用户在 2026-05-15 明确说明 "肯定是做拿不到源码的黑盒测试,只有协议的输入规范"。这排除了所有 source-instrumented coverage-guided 方案 (AFL++ source mode, libFuzzer 等)。

**How to apply:**
- 推荐工具方向: generation-based (boofuzz/Peach)、spec-driven、differential fuzzing、LLM-aided 输入生成
- 还需明确用户是否能拿到目标 **binary**(决定能否用 AFL++ QEMU/Frida grey-box) vs 只有 **远程端点**(纯 black-box,只能靠响应/存活作为 oracle)
- Bug oracle 不能假设 ASan/sanitizer,要靠:进程崩溃/端口关闭、响应异常、超时、differential 比对
