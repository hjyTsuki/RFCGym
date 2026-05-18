## Main Idea

- 网安协议的 Fuzzying Test 主要采取简单的 Prompt Workflow ，缺失Agent结合环境的探索。 我们旨在让Code Agent在真实环境交互，进行漏洞挖掘而不是简单的Prompt Wkf 来攻击。
- 为什么这个任务需要 agentic 来做，Case -> 找bug的时候需要和环境多轮交互 && 工具调用web search
- 多数聚焦在个别攻击场景的特别设计（例如只针对HTTP3转化到HTTP1.1这个部分），缺少一种泛化的 协议输入->POC 输出的泛化任务Method。

这一步包括 单个协议本身的问题 & 协议实现Code问题 & 交叉协议状态转换存在的各类场景

- 协议有问题：Case  
  -  雏形实现 -> 社区讨论 -> 整理
- 代码实现存在BUG -> CASE (从文本去发现代码实现的BUG可行)
  - 考虑把实现的代码交给LLM
  - 我们只考虑 通过协议发现的实现BUG

一般流程：

发现BUG -> 查看协议

- 针对 POC 的验证，极大程度依赖人工校验，尝试构建了一个自动过滤的framework，大程度减少了人工Check的负担。

- 估算任务数量 - 

 \> 100~ 场景

》一个场景 可以发现多少量级的BUG   3类攻击向量

SSL -> 几种厂商实现 -> 每个设置为一个场景 * 10+(出名厂商)

- 统计现有环境 -（存在的缺陷也可以统计）

厂商云部门

- CodeAgent Wkf 
- 调研 Anthropic Openai && 论文
  - https://www-cdn.anthropic.com/8b8380204f74670be75e81c820ca8dda846ab289.pdf
  - Mythos 只有CyberGYM相关的
  - 这两家与协议相关的都是AI Safety ，主要再MCP协议上的安全

### 任务描述

协议漏洞挖掘应被统一建模为一种**规范驱动的开放式交互搜索问题**。其核心不是直接让 LLM 从 RFC 生成测试用例，而是建立以下统一范式：

> **RFC** **→ 可执行协议语义表示 → 假设生成 → 环境交互验证 → 证据归因** **POC**   **(→ 技能沉淀)**

### Env Scaling 做法

这里我们尽量可嵌入 CVE-Factory 来复现环境

**来源**：收集论文的攻击场景，然后转化为可复现md，交给CVE-Stage1去做Web Search后复现整体环境

1. 场景描述 （instruction描述攻击场景，需要的RFC文档）
2. Dockerfile docker-compose.yaml (必须将协议的服务架起来)

**Runtime**：

测评各类Agent+model在发现BUG的能力，整理 POC 到固定目录下

POC包含：BUG描述，BUG攻击原语，BUG攻击脚本

**测试**：

Stage1：这阶段我们只需要验证是否攻击成功。由于预先发现BUG是未知的，只能依赖模型去生成testcase然后看输出为True or False。但这里有一个LLM as Judge 的问题。需要实践查看如何合理的**基于规则的方法验证攻击成功**

Stage2：对于攻击成功的POC，合理性判断以及是否存在新的CVE的情况。这一步是其他论文耗费人力成本最大的部分，需要人工校验这个是新的未知的POC，查看的FuzzyingCase巨量。

### 05-12

- 整理 SUB 结构 -- 优先完成 Scaling Pipeline && Agent 部署流程
- 整理 Docker 需求

### 整理图结构

- 导出现有图结构
- 完成图随机游走抽取策略Coding

这里先使用现成的组成图

1. 随机选择节点 1 个，以及总结点随机为 1~3 
2. 如果 > 1 ，随机游走，任选边走到下一个节点
3. 将得到的协议让Agent 判断是否存在场景 && 是否存在对应论文

### 任务合成 && 环境搭建pipeline

- 完成基于CVE-Factory的Code修改
- 完成测试环境 Harbor or TB1.0 适配，或者自己基于 Code Base 修改测试框架

1. 基于CVE-Factoy做修改
   1.  Stage1：信息收集
   2. 对应协议文本下载
   3. 对应协议 Docker 环境复现整理：依赖软件整理，软件名称，软件版本
   4.  单个协议：整理不同厂商之间对同种协议的实现，他们之间的交互场景
   5.  多个协议：整理多个协议之间的通讯场景，并且分化出不同厂商实现的组合
   6. 如果存在针对该协议的CVE，收集信息
   7.  Stage2：Docker构建
   8.  这部分没有什么区别；只是在涉及VM搭建上要实际测试来调整
   9.  Stage3：Test 
   10. 服务搭建成功测试，发一个正常的请求：例如邮件就正常写一个邮件发送验证完成流程搭建成功
   11. 如果存在现有CVE：为了验证环境搭建的成功，需要写 N 个攻击来测试搭建成功，这样也能验证漏洞挖掘环境搭建的合理性
   12.  Stage4：Solution 不需要
   13.  Stage5：环境修复
   14.  这个与之前保持一致即可
2. **测评流程**
   1.  **两个方案：**
   2. 只给出指令 给定协议类型，文件位置，场景描述。需要模型寻找语义模糊的地方，进行Fuzzing
   3. 强调分隔两阶段，先发现语义模糊位置，再去逐个做Fuzzing
   4.  **关于限制：**
   5.  这里生成Test肯定是无限的，肯定需要限制一下量来对比模型之间的能力差距
   6. 要求针对一个语义模糊点，只生成 <1000 个Test
   7. 时间设置尽量放开，测试可以无限时长
   8.  **关于Agent给出的结果：**
   9. 只需要给出Tests & 测试结果
   10. 需要Agent分析，得到最后的攻击成功发现的漏洞 （POC）

**指标：**

1. Success：发现 1 个语义模糊点，并且测试攻击成功  Success+1（如何保证不是误判，模型自己七篇自己，以及如何去重）
2. New-CVE：这里就需要构建一套LLM as Judge 去对比是否是一个合理的漏洞；是否是一个未发现的CVE；最后过滤完人工Check。

## 05-10

## 评测数据流示例

![img](https://eww8sudkcv.feishu.cn/space/api/box/stream/download/asynccode/?code=NDU0YzBkMzQ3NDE4OWEwZjE5NmI4OGQyMjZlZDc4YzVfZjk1a1pzV1BqUFhsdVBvRUY2TzluY293NGtpZDVFMmNfVG9rZW46SzVKWWJDVXRzbzBwU2V4UzhNNGMyV1EybndkXzE3Nzg3MzM2Mzg6MTc3ODczNzIzOF9WNA)

### 整理网络图结构

1. #### 数据源

**RFC Editor** 是互联网标准文档（RFC，Request for Comments）的官方发布机构。

互联网里大量核心协议都定义在 RFC 中：

https://www.rfc-editor.org/

https://www.rfc-editor.org/rfc-index.html

**Wireshark Dissector：**

1. 抓包解析，获取层级式的架构

优点：真实场景获取 && 常见程度高 

缺点：数据包收集源？覆盖面是否足够？

1. 直接解析 Wireshark Dissector 的源码

优点：包含了 1w+ 种协议；基于代码抽取可以规则的"from import"；

缺点：可行性分析，代码之间抽取的合理性是否足够，

**现成整理的**：不够全面 -- 可以作为参考或者前期尝试

![img](https://eww8sudkcv.feishu.cn/space/api/box/stream/download/asynccode/?code=NmI4M2VlM2VhOTI1ZmQ5MTlkYTU3Y2Q2ZGM1YjJjN2FfSXVDVWRCa1UxWWxwSVl4a3FrQU5YUmxqcEFRajZJWW1fVG9rZW46SHFkNmJSUmh4b1F0MnN4NEVKbWN3b3dzblRkXzE3Nzg3MzM2Mzg6MTc3ODczNzIzOF9WNA)

1. #### 整理知识图谱方案

### **DIVE: Scaling Diversity in Agentic Task Synthesis for Generalizable Tool Use** 

arXiv:2603.11076 — https://arxiv.org/abs/2603.11076 

**先执行真实工具收集证据，再从证据反推任务**

### 关于验证Docker 构建成功

1. 测试服务搭建起来，正常请求验证
   1. 举例：邮件STMP ，那就正常发 3 封邮件测试完整
2. 使用现有的 5 个已确认漏洞 验证可以攻击 

## 05-07

## 基于 05-06 的分类，收集论文，预测 Scaling 数量

总结：Scaling 预计情况

1. 收集论文预计 100+
2. 每一种的厂商组合包含 10+

可以达到 1000+的 任务，每个可以发现 10+个BUG（论文一般发现 7~30 个这个区间）

1. #### S&P 2025论文收集

| 类别     | 论文标题                                                     | 中文含义 / 主要问题                                          |
| -------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 2001/2/3 | Unveiling Security Vulnerabilities in Git Large File Storage Protocol | 分析 Git LFS 协议中的安全漏洞。论文指出 Git LFS 在协议设计、组件组合以及不同平台实现之间存在复杂语义问题，属于典型的协议组合与实现语义偏差问题。 |
| 2        | SAECRED: A State-Aware, Over-the-Air Protocol Testing Approach for Discovering Parsing Bugs in SAE Handshake Implementations of COTS Wi-Fi Access Points | 针对 WPA3-SAE 握手协议的实现解析错误进行研究。由于 SAE 协议状态复杂，不同厂商 AP 对协议包解析不一致，从而引入安全漏洞。 |
| 3        | Transport Layer Obscurity: Circumventing SNI Censorship on the TLS-Layer | 研究 TLS SNI 与 HTTP Host Header 之间的语义不一致问题。利用 TLS 层与 HTTP 层的协议语义错配，实现对 SNI 审查机制的绕过。 |
| 1        | An Attack on TON’s ADNL Secure Channel Protocol              | 针对 TON 区块链 ADNL 安全信道协议的攻击。发现协议自身设计存在问题，可导致密钥恢复等严重安全后果。 |
| 1        | Security Analysis of Master-Password-Protected Password Management Protocols | 对主密码保护型密码管理协议进行形式化安全分析。发现大量协议在弱口令场景下无法抵抗离线猜测攻击，属于协议设计层面的缺陷。 |
| 2        | From Control to Chaos: A Comprehensive Formal Analysis of 5G's Access Control | 对 5G 接入控制机制进行形式化分析。论文发现 5G 标准中的权限语义存在歧义，导致不同实现之间产生权限提升漏洞。 |

Scaling1： 将网络协议整理为图结构，每次抽取1~3个连续节点，交给模型合成任务

- 复现 1~2 篇论文工作场景 数据流 运作，Agent如何去做
- 尝试整理图结构

Scaling2： 爬取论文，将论文场景复现出来。

## 05-06

### 可 Scaling 的说明

1. 一篇现代协议安全论文通常围绕“协议语义失配（Semantic Inconsistency）”展开：首先选择 **2~3 种存在交互关系的网络协议或协议组件**（例如 HTTP + CDN + WAF，或 SMTP + SPF + Forwarding Service），分析它们在的语义偏差；选择**多个厂商或开源实现**（如 nginx、Envoy、Cloudflare、Spring、Apache 等）组合 **挖掘潜在攻击向量**，最终形成如可利用漏洞，并在互联网真实部署环境中进行大规模验证。 

1.  网络协议栈中包含大量协议类型，而协议安全研究主要集中在 Application Layer（应用层）。以现代互联网生态为例，Application Layer 保守估计包含约 100~300 种协议与协议格式定义，包括 HTTP/HTTPS、WebSocket、GraphQL、OAuth、SMTP、SPF、JSON、YAML、gRPC、Kubernetes API、MCP 等；每一种协议通常又存在 10~50 种不同厂商、框架或开源社区实现（如浏览器、CDN、API Gateway、WAF、反向代理、解析器、中间件等）。仅按照“两两协议交互 × 多实现组合”进行估算，其潜在协议安全场景规模即可达到：

![img](https://eww8sudkcv.feishu.cn/space/api/box/stream/download/asynccode/?code=N2NlOWFlOTdjODhlZGE3YmVhMTA4ZTFlNWUyZDVhYjZfRUk3MVlObWxtQ28wZXBDOURuT0VtcTNzQjd0VmFERzBfVG9rZW46SFhZTWJZc3RYb0VHWHR4QUVJOWNvZ2VGbnRYXzE3Nzg3MzM2Mzg6MTc3ODczNzIzOF9WNA)

虽然不是每个场景组合都是可用的，但现有可收集论文也 估算 包含了 x00~ 篇

下面是GPT粗筛选的S&P安全顶会25年的论文与协议安全相关的共计 70 篇，约40篇可以抽象出协议攻击 安全 4 大 * 3年 *粗略估计40篇 = 480 个任务

这里还没有计算分厂商实现，单篇多种协议组合。

```Plain
网络 / 通信协议安全
 Invade the Walled Garden: Evaluating GTP Security in Cellular Networks 
 Transport Layer Obscurity: Circumventing SNI Censorship on the TLS Layer 
 Resolution Without Dissent: In-Path Per-Query Sanitization to Defeat Surreptitious Communication Over DNS 
 SYN Proof-of-Work: Improving Volumetric DoS Resilience in TCP 
 Unveiling Security Vulnerabilities in Git Large File Storage Protocol 
 An Attack on TON’s ADNL Secure Channel Protocol 
 Post-quantum Cryptographic Analysis of SSH 
 TreeKEM: A Modular Machine-Checked Symbolic Security Analysis of Group Key Agreement in Messaging Layer Security 
 SAECRED: A State-Aware, Over-the-Air Protocol Testing Approach for Discovering Parsing Bugs in SAE Handshake Implementations of COTS Wi-Fi Access Points 
 From Control to Chaos: A Comprehensive Formal Analysis of 5G's Access Control 
 Mind the Location Leakage in LEO Direct-to-Cell Satellite Networks 
 WireWatch: Measuring the security of proprietary network encryption in the global Android ecosystem 
 Is Nobody There? Good! Globally Measuring Connection Tampering without Responsive Endhosts 
 BaseBridge: Bridging the Gap between Emulation and Over-The-Air Testing for Cellular Baseband Firmware 
Web / 身份认证 / 应用层协议
 “Only as Strong as the Weakest Link”: On the Security of Brokered Single Sign-On on the Web 
 Security Analysis of Master-Password-Protected Password Management Protocols 
 AccuRevoke: Enhancing Certificate Revocation with Distributed Cryptographic Accumulators 
 Clubcards for the WebPKI: smaller certificate revocation tests in theory and practice 
区块链 / Web3 协议安全
 P2C2T: Preserving the Privacy of Cross-Chain Transfer 
 Asymmetric Mempool DoS Security: Formal Definitions and Provable Secure Designs 
 Liquefaction: Privately Liquefying Blockchain Assets 
 Warning! The Timeout T Cannot Protect You From Losing Coins, PipeSwap: Forcing the Timely Release of a Secret for Atomic Cross-Chain Swaps 
 A Composability Analysis Framework for Web3 Wallet Recovery Mechanisms 
 Permissionless Verifiable Information Dispersal (Data Availability for Bitcoin Rollups) 
 VITARIT: Paying for Threshold Services on Bitcoin and Friends 
 Decentralization of Ethereum's Builder Market 
密码协议 / MPC / ZK / PIR / 签名 / 密钥协议
 Verifiable Secret Sharing Simplified 
 Augmented Shuffle Protocols for Accurate and Robust Frequency Estimation under Differential Privacy 
 TreePIR: Efficient Private Retrieval of Merkle Proofs via Tree Colorings with Fast Indexing and Zero Storage Overhead 
 Ringtail: Practical Two-Round Threshold Signatures from Learning with Errors 
 Phecda: Post-Quantum Transparent zkSNARKs from Improved Polynomial Commitment and VOLE-in-the-Head with Application in Publicly Verifiable AES 
 Efficient Proofs of Possession for Legacy Signatures 
 Improved Constructions for Distributed Multi-Point Functions 
 Preprocessing for Life: Dishonest-Majority MPC with a Trusted or Untrusted Dealer 
 Groundhog: A Restart-based Systems Framework for Increasing Availability in Threshold Cryptosystems 
 "Check-Before-you-Solve": Verifiable Time-lock Puzzles 
 Sparta: Practical Anonymity with Long-Term Resistance to Traffic Analysis 
 MatriGear: Accelerating Authenticated Matrix Triple Generation with Scalable Prime Fields via Optimized HE Packing 
 Myco: Unlocking Polylogarithmic Accesses in Metadata-Private Messaging 
 PGUS: Pretty Good User Security for Thick MVNOs with a Novel Sanitizable Blind Signature 
 Peer2PIR: Private Queries for IPFS 
 Papercraft: Lattice-based Verifiable Delay Function Implemented 
 Cauchyproofs: Batch-Updatable Vector Commitment with Easy Aggregation and Application to Stateless Blockchains 
 ZHE: Efficient Zero-Knowledge Proofs for HE Evaluations 
 Signature-Free Atomic Broadcast with Optimal O(n²) Messages and O(1) Expected Time 
 Impossibility Results for Post-Compromise Security in Real-World Communication Systems 
 Gold OPRF: Post-Quantum Oblivious Power-Residue PRF 
 Ring Referral: Efficient Publicly Verifiable Ad hoc Credential Scheme with Issuer and Strong User Anonymity for Decentralized Identity and More 
 Robust Threshold ECDSA with Online-Friendly Design in Three Rounds 
 ALPACA: Anonymous Blocklisting with Constant-Sized Updatable Proofs 
 Highly Efficient Actively Secure Two-Party Computation with One-Bit Advantage Bound 
 Towards Efficient and Practical Multi-party Computation under Inconsistent Trust in TEEs 
 Extended Diffie-Hellman Encryption for Secure and Efficient Real-Time Beacon Notifications 
 SoK: Dlog-based Distributed Key Generation 
边界相关，可算“协议安全/安全协议”但偏系统或测量
 TokenWeaver: Privacy Preserving and Post-Compromise Secure Attestation 
 PORTAL: Fast and Secure Device Access with Arm CCA for Modern Arm Mobile System-on-Chips 
 DataSeal: Ensuring the Verifiability of Private Computation on Encrypted Data 
 Hermes: Efficient and Secure Multi-Writer Encrypted Database 
 Mixnets on a tightrope: Quantifying the leakage of mix networks using a provably optimal heuristic adversary 
 SoK: Decoding the Enigma of Encrypted Network Traffic Classifiers 
 Is MPC Secure? Leveraging Neural Network Classifiers to Detect Data Leakage Vulnerabilities in MPC Implementations
```

每一篇论文大约发现 10~ 左右的BUG（10~100）

### 协议 BUG 分类

1. 协议与实现

   1. 协议自身BUG
   2. **协议实现BUG·**

2. 单 & 多

   1. 单个协议BUG

   2.  **BUG来源1**：协议本身存在的问题

   3. ```Plain
      We Still Don’t Have Secure Cross-Domain Requests: an Empirical Study of CORS
      https://www.usenix.org/system/files/conference/usenixsecurity18/sec18-chen.pdf
      ---- CORS 是什么  ---- 
      CORS 被提出：
       浏览器通过 HTTP Header 控制跨域授权 
       服务端显式声明允许哪些 Origin 访问资源 
       浏览器执行安全检查
      ------- 协议本身问题举例 -------
      协议本身问题：
      CORS 实际扩大了 Web 信任边界
          传统 SOP：
          A 与 B 隔离
          而 CORS：
          A → B → C
          形成复杂 trust graph。
          攻击面显著扩大。
       -------- 衍生出的CVE ------
      CVE-2018-20744
      这个漏洞的本质
      这是协议层语义问题。
      因为：
      CORS 规范禁止：
      Access-Control-Allow-Origin: *
      Access-Control-Allow-Credentials: true
      于是框架作者“聪明地”：
      把 * 变成 Origin reflection
      结果：
      安全边界直接崩塌。
      ```

   4.  **BUG来源2**：不同厂商对同一协议的理解存在偏差，导致的转化BUG

   5.  主要问题来源于单个协议存在语义模糊的地方

   6. ```Plain
      HDiff: A Semi-automatic Framework for Discovering Semantic Gap Attack in HTTP Implementations
      H3Act: Automated Measuring "Regressed" Semantic Conversion Anomalies of HTTP/3-to-HTTP/1.1 Translation in CDNs with Knowledge-Driven Fuzzing
      MIME协议：Inbox Invasion: Exploiting MIME Ambiguities to Evade Email Attachment Detectors
      ```

   7. **交叉协议BUG**

   8.  多个协议之间存在的BUG类型：主要来源于协议之间对同一对象的语义理解不对等

   9. ```Plain
      Composition Kills: A Case Study of Email Sender Authentication
      系统中的各个模块虽然处理的是同一条消息，但它们对“谁是真正的发送者”“哪个字段代表身份”“哪些内容参与认证”等问题的理解并不一致，从而导致攻击者可以利用这种认知差异绕过安全机制。例如在邮件系统中，SPF 可能认证的是 SMTP Envelope 中的 Return-Path，而邮件客户端展示给用户的却是 Header 中的 From 字段。攻击者可以构造一封邮件，让 SPF 对 attacker.com 验证成功，但在 From 中伪造为 paypal.com，于是服务器认为邮件“认证通过”，而用户看到的却是“来自 PayPal 的可信邮件”。这类漏洞并不是单个协议本身失效，而是多个协议组合后，对同一语义对象（身份、来源、权限等）理解不一致所导致的安全问题。
      ```#   R F C - V u l n e r a b i l i t y  
 