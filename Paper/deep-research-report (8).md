# S&P 2025 中与三类协议语义漏洞最相关的论文梳理

## 研究范围

我筛查了 entity["event","IEEE Symposium on Security and Privacy 2025","computer security conference"] 的官方 urlaccepted-papers listturn4search0，只保留“不是单纯提出新协议，而是明确分析协议失效、语义偏差、实现异构性或组合失配”的论文。强匹配的核心样本一共六篇：urlAn Attack on TON’s ADNL Secure Channel Protocolturn5search1、urlSecurity Analysis of Master-Password-Protected Password Management Protocolsturn7search13、urlUnveiling Security Vulnerabilities in Git Large File Storage Protocolturn7search6、urlSAECRED: A State-Aware, Over-the-Air Protocol Testing Approach for Discovering Parsing Bugs in SAE Handshake Implementations of COTS Wi-Fi Access Pointsturn21search1、urlFrom Control to Chaos: A Comprehensive Formal Analysis of 5G's Access Controlturn5search4，以及 urlTransport Layer Obscurity: Circumventing SNI Censorship on the TLS-Layerturn8search4。citeturn4search0turn5search1turn7search13turn7search6turn21search1turn5search4turn8search4

如果只看“与你给出的三分法贴合得最完整”的论文，最值得优先关注的是 Git LFS 这篇。作者没有只给出漏洞清单，而是直接把问题根源拆成三类：**protocol complexity、compositional intricacy、infrastructure heterogeneity**。这几乎与“协议自身问题 / 同一协议的实现偏差 / 交叉协议或组合语义失配”一一对应。更重要的是，这篇论文不是停留在概念层面，而是从这三类根因中提炼出 11 个安全性质、4 类攻击向量，并在 14 个主流平台上找到了 36 个此前未知漏洞。citeturn29view0turn29view1

## 协议本身存在问题

最纯粹、最“像你给的 CORS 例子”的 S&P 2025 论文，是 urlAn Attack on TON’s ADNL Secure Channel Protocolturn5search1。这篇论文的关键点不是某个实现把协议写错了，而是协议设计本身存在两个密码学缺陷：其一，握手允许 session-key replay；其二，完整性机制是非标准的，而且它的安全性反而依赖消息机密性。作者把这两个设计问题转化成了高效明文恢复攻击；只要拦截客户端与 liteserver 的通信并进行少量重放，就能恢复用于加密服务器响应的 keystream，进而解密账户余额、活动模式等敏感信息，甚至篡改资产价格与账户余额显示。这是标准意义上的“协议自己就不安全”。citeturn1view1turn6view4

另一篇应放入这个类别的是 urlSecurity Analysis of Master-Password-Protected Password Management Protocolsturn7search13。这篇论文把 43 个密码管理器背后的 M3PM 协议抽象出来，形式化定义其理想功能，然后发现 43 个样本里有 38 个至少对一种离线猜测攻击不安全。作者明确指出：**仅仅把 vault 加密并不等于协议安全；认证机制与加密机制的不当组合本身就会引入漏洞**。因此，它讨论的不是“编码犯错”，而是单一主密码保护模型下，协议设计在威胁模型、口令熵和服务器角色上的内生脆弱性。citeturn2view6turn19view0turn20view1

Git LFS 论文也部分落在这一类，因为作者把“Protocol Complexity”放在三大根因的第一位：Git LFS 不再是传统 Git 那种简单的 client-server 结构，而是同时牵涉 client、Git SSH server、LFS server 和 storage server，多组件协作直接扩大了攻击面，并增加了对安全性质进行整体分析的难度。若你要找一个“协议复杂化本身扩大 trust boundary”的 S&P 2025 对应物，它就是最接近的那个。citeturn29view0turn29view1

## 同一协议在不同实现中的语义偏差

在“同一协议、不同厂商/实现对语义理解不一致”这个类别里，最强匹配是 urlSAECRED: A State-Aware, Over-the-Air Protocol Testing Approach for Discovering Parsing Bugs in SAE Handshake Implementations of COTS Wi-Fi Access Pointsturn21search1。这篇论文的叙事非常清楚：WPA3-Personal 引入 SAE 以获得前向保密和抗离线猜测能力，但为了修补早期设计中的 downgrade 和 DoS 问题，增强版 SAE 又引入了更复杂、可变长度、上下文相关的报文格式；结果是，解析与状态更新变得很难被不同 AP 实现正确掌握。作者在 6 个商用 AP 和开源 hostapd 上发现了 4 类 bug，其中 2 类直接破坏 SAE 想保证的两个根性质：抗降级与抗 DoS。它本质上就是“协议语义越来越复杂 → 实现开始分叉 → 安全保证回退”的故事。citeturn2view1turn22view0turn23view1

urlFrom Control to Chaos: A Comprehensive Formal Analysis of 5G's Access Controlturn5search4 则是这一类别里最接近你所说 HDiff / H3Act 式“语义模糊导致厂商理解偏差”的案例。对这篇 S&P 论文最有力的后续公开证据，来自 entity["organization","3GPP","mobile standards organization"] 官方的 TS 29.510 change request：3GPP 在 CR 中明确写道，GSMA 报送的 CVD-2025-0101 中，“Coarse Scope Attack” 利用了 `producerSnssaiList` 这个 OAuth 2.0 access token claim 在规范里**定义不够清楚**这一点；NRF 厂商可能把“仅该 consumer 被授权访问的 slices”错误实现成“producer 支持的全部 slices”，从而让恶意 consumer 越权访问本不该访问的切片服务，造成数据泄露、服务中断和未授权资源使用。作者主页还记录了该论文的 CoreScan 结果被 entity["organization","GSMA","mobile industry association"] 以 CVD-2025-0101 形式确认，并触发了 3GPP 规范修订。就你的 taxonomy 而言，这几乎是“语义含混 → 厂商补全规范 → 安全边界坍塌”的教科书例子。citeturn2view5turn15view0turn24search1turn14search6

Git LFS 论文的 “Infrastructure Heterogeneity” 也应该放进这一类。作者指出，不同平台在 cloud storage integration、verification API enforcement、quota enforcement 等方面采用了不同实现方式，这种异构性本身就阻碍系统化漏洞发现。换言之，同一个 LFS 协议规范，落到不同平台后形成了不同的安全语义与不同的 enforcement path，这正是你所说“不同厂商对统一协议的理解存在偏差”的平台化版本。citeturn29view0turn29view1

## 交叉协议与组合语义失配

在“多个协议或多个子系统组合后，语义对象不再一致”的类别中，Git LFS 论文再次是最强样本。它的“Compositional Intricacy”不是一句泛泛而谈的口号，而是具体指出：Git LFS 与 repository archiving、forking 等辅助功能之间的联动会产生细微但关键的风险，例如仓库已经被标记为 read-only，但 LFS 仍可能允许新上传且不更新 quota 计量，最终形成 quota escape。更进一步，从协议流程看，LFS 先通过 SSH 上的 `git-lfs-authenticate` 取得 token，再把这个 token 带到 HTTP Batch API 中执行对象上传/下载。也就是说，认证、对象访问、仓库状态、配额和底层存储并不是由一个单一协议一次性决定的，而是分散在多个协议步骤和多个子系统中共同实现；一旦这些部分对“谁有权、何时可写、什么算一次资源消耗”理解不一致，就会出现组合型漏洞。citeturn29view0turn29view1

urlTransport Layer Obscurity: Circumventing SNI Censorship on the TLS-Layerturn8search4 虽然不属于传统意义上的 “CVE-style 漏洞论文”，但它非常符合“组合语义失配”的分析框架。论文指出，审查者通常依赖 TLS ClientHello 里明文的 SNI 来判断要访问的主机名；但某些虚拟主机环境即使没有 SNI 或者 SNI 错误，也仍可依靠**加密后的 HTTP Host header** 或默认 host 路由来确定目标域名。换言之，TLS 层和 HTTP / virtual-host routing 层并不总是共享同一个“决定性主机名”语义对象。作者由此系统性探索 TLS 层规避技术，最终识别出 38 种部分符合标准的规避技巧、归为 11 个类别，并成功在中国和伊朗实现规避。这篇论文的价值不在“协议 broken”，而在于它展示了：当不同层把“同一个名字”理解成不同东西时，系统行为就会出现可利用的缝隙。citeturn1view1turn28view0turn31view1

5G access control 论文也部分落入这一类，只是我把它放在第二类更合适。原因在于，3GPP CR 所暴露的问题并不只是“字段没写清楚”，还在于 5G 的 slice 授权语义被装进了通用的 OAuth 2.0 token claim 里；一旦 telecom-specific scope 被粗糙地映射到 generic token semantics，组合边界就会变得脆弱。这一点更像你说的“多个协议组合后，对同一语义对象理解不一致”。不过，就主分类而言，它仍然更像“规范含混导致实现偏差”。citeturn15view0

## 最值得优先读的论文清单

如果你的目标是按你提出的三类根因建立一个 S&P 2025 版的阅读样本库，那么我会把 urlUnveiling Security Vulnerabilities in Git Large File Storage Protocolturn7search6 放在第一位，因为它几乎直接复现了你的三分法：protocol complexity 对应“协议自身扩大攻击面”，compositional intricacy 对应“交叉协议/子系统组合杀伤”，infrastructure heterogeneity 对应“不同实现路径产生的语义偏差”。citeturn29view0turn29view1

若你需要一个“最纯的协议设计缺陷”样本，首选应是 urlAn Attack on TON’s ADNL Secure Channel Protocolturn5search1；它的结论直接落在密码学协议设计缺陷，而不是实现偶发 bug。citeturn6view4

若你需要一个“同一协议在不同实现中被解析/执行得不一样”的样本，首选应是 urlSAECRED: A State-Aware, Over-the-Air Protocol Testing Approach for Discovering Parsing Bugs in SAE Handshake Implementations of COTS Wi-Fi Access Pointsturn21search1；它最贴近你对语义模糊、实现偏差、状态转换 bug 的描述。citeturn22view0turn23view1

若你需要一个“规范里一个字段写得不够清楚，厂商各自补完后出现权限升级”的样本，首选应是 urlFrom Control to Chaos: A Comprehensive Formal Analysis of 5G's Access Controlturn5search4，配合官方的 url3GPP change request detailsturn15view0 一起读；它是 S&P 2025 里最接近你 CORS / Origin reflection 例子的论文。citeturn15view0turn24search1

若你需要一个“跨协议/跨层语义对象不一致”样本，则应读 urlTransport Layer Obscurity: Circumventing SNI Censorship on the TLS-Layerturn8search4；它把 TLS 里的 name semantics 与 HTTP / virtual-host routing 的 name semantics 不一致这件事，变成了可系统利用的空间。citeturn28view0turn31view1

最后，urlSecurity Analysis of Master-Password-Protected Password Management Protocolsturn7search13 是很好的“形式化协议分析”补充读物：它提醒你，很多看起来像“产品安全问题”的案例，实质上仍然是认证与加密组合方式导致的协议级缺陷。citeturn19view0turn20view1

## 与你给出的框架如何对齐

与你给出的 CORS / CVE-2018-20744 框架最接近的 S&P 2025 案例，不是浏览器论文，而是 5G access control 里的 `producerSnssaiList`。两者的共性都在于：规范对一个安全关键语义对象写得不够精确，工程实现者于是用“看起来合理”的方式去补完；最后系统虽然“能跑通”，但授权边界已经偏离了原始安全意图。3GPP CR 甚至把这种风险写得非常直白：如果厂商把 claim 理解成 producer 的全部 slices，而不是当前 consumer 被授权的 slices，就会发生跨切片越权。citeturn15view0

与你给出的 HDiff / H3Act 框架最接近的 S&P 2025 案例，则是 SAECRED 与 Git LFS 的组合。SAECRED 代表的是“同一标准在不同实现中的 packet parsing 与 state update 不一致”；Git LFS 的 challenge 3 则代表“同一协议在不同平台上的验证 API、quota enforcement、storage integration 路径不同”，因而同一安全性质并未被一致实现。前者更像报文语义偏差，后者更像平台语义偏差。citeturn23view1turn29view0turn29view1

与你给出的 Composition Kills 框架最接近的 S&P 2025 案例，是 Git LFS 和 Transport Layer Obscurity。前者里，SSH-issued token、HTTP object API、repo 状态、quota 记账与底层 storage 对“谁有权、何时可写、如何计量资源”并不总是共享同一语义；后者里，TLS SNI、HTTP Host header 与 virtual-host 路由对“真正的目标主机名”也并不总是一致。它们都不是“某个单独协议完全坏掉”，而是多个层/组件一起处理同一对象时，语义没有被端到端地保持一致。citeturn29view0turn29view1turn28view0turn31view1

整体上看，S&P 2025 对你这套协议漏洞 taxonomy 的最好回答不是“每类恰好一篇”，而是：**Git LFS 负责横跨三类，TON 与 M3PM 负责补足纯设计缺陷，SAECRED 与 5G access control 负责补足语义模糊导致的实现分叉，Transport Layer Obscurity 负责展示跨层组合失配如何被系统化利用。** 这六篇放在一起，已经足够构成一个很强的 S&P 2025 协议语义漏洞阅读集。citeturn6view4turn19view0turn22view0turn15view0turn28view0turn29view0