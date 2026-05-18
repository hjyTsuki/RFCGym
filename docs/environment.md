# H3Act 测试环境规格

> 目标：搭建可运行 HTTP/3 → HTTP/1.1 协议翻译异常检测的最小可用基础环境，覆盖**本地模拟 CDN** 与 **真实商业 CDN** 两条测试路径。LLM 全程使用云端 API（无需本地 GPU）。

---

## 1. 网络拓扑

```
                         ┌──────────────────────────┐
                         │      Cloud LLM API       │
                         │ (Claude / GPT / Gemini)  │
                         └────────────▲─────────────┘
                                      │ HTTPS
                                      │
   ┌──────────────────────────┐       │       ┌──────────────────────────┐
   │   h3act-client (fuzzer)  │───────┘       │   Real Commercial CDN    │
   │  - Generator Agent       │  HTTP/3 ───►  │  Cloudflare / Tencent... │
   │  - Analyzer Agent        │               └────────────┬─────────────┘
   │  - ChromaDB (vector RAG) │                            │ HTTP/1.1
   └──────────┬───────────────┘                            ▼
              │ HTTP/3 (QUIC)                  ┌──────────────────────────┐
              ▼                                │  mock-origin (Nginx 1.1) │
   ┌──────────────────────────┐                │  访问日志详细到 request   │
   │  mock-cdn (Nginx 1.27)   │   HTTP/1.1 ──► │  头逐字段                 │
   │  - HTTP/3 ingress (QUIC) │                └──────────────────────────┘
   │  - proxy_pass to origin  │
   │  - proxy_cache           │
   └──────────────────────────┘
```

两条数据路径：
- **本路径 A（本地闭环）**：`h3act-client → mock-cdn → mock-origin`，所有日志可控，用于开发调试与回归。
- **路径 B（真实评估）**：`h3act-client → 真实 CDN → mock-origin`，原点仍由我们控制以便对照日志。

---

## 2. 硬件要求（云 API 模式）

| 维度 | 最低 | 推荐 |
|------|------|------|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 20 GB SSD | 50 GB SSD |
| 网络 | 100 Mbps | 1 Gbps |
| GPU | 不需要 | 不需要 |
| 操作系统 | Linux / macOS / Windows + Docker Desktop | Ubuntu 22.04+ |

> 注：论文使用 4× RTX 4090（96 GB VRAM）跑本地 `gpt-oss:120b`。改用云 API 后这部分可省去，硬件门槛从工作站级降到普通开发机级。

---

## 3. 软件栈版本

### 3.1 Docker 侧（容器内）

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.12.x | h3act-client 运行时（与论文一致） |
| Nginx | ≥ 1.27.x（含 `http_v3` 模块） | mock-cdn 边缘节点 |
| Nginx | ≥ 1.24.x | mock-origin 源站 |
| OpenSSL | ≥ 3.0 | 自签证书 + QUIC TLS |

### 3.2 主机侧

| 组件 | 版本 | 用途 |
|------|------|------|
| Docker Engine | ≥ 24.0 | 容器编排 |
| Docker Compose | v2.20+ | 多服务编排 |
| Git | ≥ 2.30 | 拉取代码与 RFC 文档 |

> Windows 用户：使用 Docker Desktop + WSL2，确保 WSL2 启用且分配至少 8 GB 内存。

---

## 4. Python 依赖（h3act-client 容器内）

下列依赖会写入 `docker/h3act-client/requirements.txt`：

| 库 | 版本约束 | 作用 | 论文对应 |
|----|----------|------|----------|
| `aioquic` | >=1.0.0 | HTTP/3 客户端，支持自定义帧构造 | §3.4 实现章节 |
| `httpx` | >=0.27 | 通用 HTTP 客户端（fallback、健康检查） | 辅助 |
| `chromadb` | >=0.5 | 本地向量数据库 | §3.2.2 |
| `sentence-transformers` | >=3.0 | 加载 `all-MiniLM-L6-v2` embedding | §3.2.2 |
| `pydantic` | >=2.7 | JSON Schema 强约束 LLM 输出 | §3.4 |
| `anthropic` | >=0.40 | Claude API SDK | 云 API 替换 |
| `openai` | >=1.50 | GPT API SDK | 云 API 替换 |
| `google-genai` | >=0.5 | Gemini API SDK（可选） | 云 API 替换 |
| `tenacity` | >=8.5 | 失败重试 | 工程 |
| `structlog` | >=24.0 | 结构化日志 | 工程 |
| `python-dotenv` | >=1.0 | 加载 `.env` 配置 | 工程 |
| `pyyaml` | >=6.0 | 攻击原语 / 测试集 YAML | 工程 |

可选（更专业的 HTTP/3 操控）：
| 库 | 作用 |
|----|------|
| `scapy` | 底层包构造（如需细粒度 QUIC 控制） |
| `httpcore` + `h3` | 备用 HTTP/3 栈 |

---

## 5. LLM API 提供方（任选其一或组合）

| 厂商 | 推荐模型 | API Key 环境变量 | 备注 |
|------|----------|-------------------|------|
| Anthropic | `claude-opus-4-7` / `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` | 强推理，论文 RAG/Analyzer 场景适配最好 |
| OpenAI | `gpt-5` / `gpt-4.1` | `OPENAI_API_KEY` | 兼容性强，工具调用稳定 |
| Google | `gemini-2.5-pro` | `GOOGLE_API_KEY` | 长上下文（适合论文 §3.3.1 注意力稀释问题） |

> Generator 与 Analyzer 可使用不同模型/温度（对应论文 §3.3.1 双 Agent 温度差异）：
> - Generator：高温（0.7–0.9）发散
> - Analyzer：低温（0.0–0.2）严谨

---

## 6. 本地模拟 CDN（mock-cdn）能力清单

mock-cdn 是 Nginx 1.27+ 反向代理，必须覆盖论文测试的语义场景：

| 论文章节 | 测试能力 | mock-cdn 是否需支持 |
|----------|----------|---------------------|
| §5.1.1 Range | `Range` 头转发 / 移除 / 扩展 | ✅ 默认转发，可通过配置开关切换 |
| §5.1.2 条件请求 | `If-Match` 等条件头处理 | ✅ |
| §5.1.3 HTTP Slow | 慢速 body / 慢速 header | ✅ 通过 `proxy_request_buffering` 切换流式模式 |
| §5.2.1 URI 解析 | `:path` 含绝对 URI vs `:authority` 冲突 | ✅ |
| §5.2.2 重复 Host | 多个 `Host` / `:authority` | ✅ |
| §5.3 CRLF / Meta | 控制字符透传 | ✅ |

> mock-cdn 主要目的是给开发者一个**"行为可调"的对照组**，不追求完美模拟某一家真实 CDN。

---

## 7. 真实 CDN 接入准备清单

如计划在路径 B（真实 CDN）跑测试，需准备：

1. **可控域名一个**（用于解析到 CDN 的 CNAME）
2. **CDN 厂商账号**：选定 1–N 家，参考论文表 5：Cloudflare / Cloudfront / Fastly / 阿里云 CDN / 百度 CDN / 腾讯 CDN / 华为云 CDN
3. **CDN 配置最小改动**（论文 §4.1.1）：
   - 仅启用 HTTP/3
   - 允许 `Range` 请求
   - 其他缓存/优化规则**保持默认**，避免污染测试
4. **公网可达的 mock-origin**：建议放在国内/海外有公网 IP 的 VPS，作为各 CDN 的回源地址
5. **TLS 证书**：mock-origin 公网域名需要合法证书（Let's Encrypt 即可）

⚠️ **法律与伦理**：参照论文"Ethical Considerations"，所有测试必须仅指向**自己控制的源站**，不得对第三方源发起放大攻击；DoS 类测试需限速（如每次发送后 sleep 0.5s）。

---

## 8. 关键端口规划

| 服务 | 容器端口 | 主机端口 | 协议 |
|------|----------|----------|------|
| mock-cdn | 443/udp | 4433/udp | HTTP/3 (QUIC) |
| mock-cdn | 443/tcp | 4433/tcp | HTTP/2 fallback |
| mock-origin | 80/tcp | 8080/tcp | HTTP/1.1 |
| mock-origin | 443/tcp | 8443/tcp | HTTPS/1.1 |
| chromadb | 8000/tcp | 8001/tcp | gRPC/HTTP（如选择独立容器部署） |

> ChromaDB 默认使用嵌入式（in-process）模式，无需独立容器；如需多 Agent 共享则启用独立服务。

---

## 9. 数据卷

| 卷名 | 挂载路径 | 用途 |
|------|----------|------|
| `chroma-data` | `/app/chroma` | 向量库持久化 |
| `rfc-corpus` | `/app/rfcs` | RFC 文档语料（9000/9110/9111/9112/9113/9114 等） |
| `attack-primitives` | `/app/primitives` | 静态注入用的攻击原语库（YAML） |
| `cdn-logs` | `/var/log/nginx` | mock-cdn 访问日志 |
| `origin-logs` | `/var/log/nginx` | mock-origin 访问日志 |
| `test-cases` | `/app/cases` | 生成的测试用例归档 |

---

## 10. 环境变量（`.env`）

```ini
# LLM Provider
LLM_PROVIDER=anthropic            # anthropic | openai | google
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=
GOOGLE_API_KEY=

# Generator 配置
GENERATOR_MODEL=claude-sonnet-4-6
GENERATOR_TEMPERATURE=0.8

# Analyzer 配置
ANALYZER_MODEL=claude-opus-4-7
ANALYZER_TEMPERATURE=0.1

# 目标 CDN（任选一个生效）
TARGET_MODE=local                 # local | remote
LOCAL_CDN_HOST=mock-cdn
LOCAL_CDN_PORT=443
REMOTE_CDN_URL=https://your-test-domain.example.com

# 测试参数
BATCH_SIZE=5
BATCHES_PER_RUN=10
MEMORY_CONSOLIDATE_EVERY=3

# 限速（伦理保护）
INTER_REQUEST_DELAY_MS=500
```

---

## 11. 启动顺序与健康检查

1. `docker compose up -d mock-origin` → `curl http://localhost:8080/healthz` 应 200
2. `docker compose up -d mock-cdn` → `curl --http3 -k https://localhost:4433/healthz` 应 200（需 curl 编译时支持 HTTP/3）
3. `docker compose up -d h3act-client` → 进入容器执行 `python -m h3act.smoke_test`

> 若主机 `curl` 不支持 HTTP/3，可直接进入 `h3act-client` 容器使用 `aioquic` 自带的 `http3_client.py` 测试。

---

## 12. 与论文实现差异说明

| 项目 | 论文 | 本环境 | 理由 |
|------|------|--------|------|
| LLM | gpt-oss:120b 本地 | Claude/GPT/Gemini 云 API | 降低硬件门槛 |
| Embedding | all-MiniLM-L6-v2 | 同 | 保持轻量 |
| 向量库 | ChromaDB | 同 | 保持一致 |
| 客户端 | aioquic | 同 | 保持一致 |
| 源站 | Nginx 1.24.0-2ubuntu7.5 | Nginx ≥ 1.24 (Docker 镜像) | 容器化便于复现 |
| CDN | 9 家商业 CDN | mock-cdn + 商业 CDN 可选 | 先打通本地链路 |
| GPU | 4× RTX 4090 | 无 | 不再需要本地推理 |
