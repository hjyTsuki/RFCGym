# H3Act 测试基础环境

最小可运行的 HTTP/3 → HTTP/1.1 协议翻译异常检测环境。完整规格见 [`docs/environment.md`](docs/environment.md)。

## 组件

| 服务 | 镜像 | 端口（主机） | 角色 |
|------|------|--------------|------|
| `mock-origin` | `nginx:1.27-alpine` | `8080/tcp` | HTTP/1.1 源站，详细记录请求头 |
| `mock-cdn` | `nginx:1.27` | `4433/tcp`、`4433/udp` | HTTP/3 边缘节点，反向代理到源站 |
| `h3act-client` | 本地构建 | — | aioquic + LLM Agents（云 API） |

## 快速启动

### 1. 准备目录

```bash
git clone <this-repo>
cd middleware
cp .env.example .env
# 编辑 .env，填入至少一个 LLM API key
```

### 2. 生成自签证书（mock-cdn 使用）

```bash
mkdir -p docker/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout docker/certs/key.pem \
  -out docker/certs/cert.pem \
  -subj "/CN=mock-cdn" \
  -addext "subjectAltName=DNS:mock-cdn,DNS:localhost,IP:127.0.0.1"
```

### 3. 生成大体积测试资源（Range 攻击需要 ~29MB）

```bash
docker run --rm -v "$(pwd)/docker/mock-origin/www:/out" alpine \
  sh -c "apk add --no-cache coreutils && dd if=/dev/urandom of=/out/test.png bs=1M count=29"
```

### 4. 启动

```bash
docker compose up -d mock-origin mock-cdn
docker compose ps
```

### 5. 连通性自检

```bash
# HTTP/1.1 源站
curl -sf http://localhost:8080/healthz

# HTTP/3 CDN（需要支持 --http3 的 curl）
curl -k --http3 https://localhost:4433/healthz

# 范围请求穿透
curl -k --http3 -H "Range: bytes=0-0" -o /dev/null -w "%{http_code}\n" https://localhost:4433/test.png
```

源站和 CDN 的访问日志：

```bash
docker compose exec mock-cdn    tail -f /var/log/nginx/access.log
docker compose exec mock-origin tail -f /var/log/nginx/access.log
```

### 6. 进入 fuzzer 容器

```bash
docker compose run --rm h3act-client bash
# 容器内可直接：
python -c "import aioquic, chromadb, anthropic; print('ok')"
```

## 目录结构

```
middleware/
├── .env.example
├── docker-compose.yml
├── README.md
├── docs/
│   └── environment.md           # 完整环境规格
├── docker/
│   ├── certs/                   # 自签证书（gitignored）
│   ├── h3act-client/
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   └── requirements.txt
│   ├── mock-cdn/
│   │   └── nginx.conf
│   └── mock-origin/
│       ├── nginx.conf
│       └── www/
├── rfcs/                        # （后续）RFC 9000/9110/9111/9112/9113/9114
├── primitives/                  # （后续）攻击原语 YAML
├── cases/                       # 测试用例归档
└── src/                         # （后续）H3Act 框架代码
```

## 测试两条路径

### 路径 A：本地闭环
`.env` 中保持 `TARGET_MODE=local`，由 `mock-cdn` 完成 HTTP/3→HTTP/1.1 翻译。

### 路径 B：真实商业 CDN
1. 把 `mock-origin` 部署到公网（带合法 TLS 证书）
2. 在 Cloudflare/Tencent/Alibaba 等控制台配置 CNAME 指向公网域名
3. `.env` 设置：
   ```
   TARGET_MODE=remote
   REMOTE_CDN_URL=https://your-test-domain.example.com
   ```
4. **务必遵守伦理约束**（参考论文 Ethical Considerations）：
   - 流量仅指向自己控制的源站
   - DoS 类测试限速（`INTER_REQUEST_DELAY_MS=500`）
   - 验证到漏洞后立即停止发送

## 常见问题

**Q: `curl --http3` 报错 "option --http3 unknown"**
A: 系统 curl 不支持 HTTP/3。进入 `h3act-client` 容器使用 aioquic 自带的 `http3_client.py`，或安装 `curl-http3`。

**Q: mock-cdn 启动失败 `unknown directive "quic"`**
A: Nginx 镜像版本过低。`docker-compose.yml` 中已固定 `nginx:1.27`，请删除旧镜像后重试：`docker rmi nginx:1.27 && docker compose pull`。

**Q: ChromaDB embedding 模型下载慢**
A: Dockerfile 在构建时预下载 `all-MiniLM-L6-v2`。若网络受限，可通过 `HF_ENDPOINT=https://hf-mirror.com` 构建参数指定镜像源。

## 下一步

环境就绪后，下一阶段实现：
1. `src/h3act/transport/` — aioquic 帧级构造（含 AST 化 payload 展开）
2. `src/h3act/rag/` — Hybrid RAG（静态原语注入 + 动态 RFC 检索）
3. `src/h3act/agents/` — Generator / Analyzer 双 Agent
4. `src/h3act/memory/` — 短/长期记忆 + 固化策略
