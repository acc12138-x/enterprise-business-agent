# 2GB 服务器多项目部署方案

> 目标：在 **一台 2GB 内存的云服务器**上，同时跑
> **① 本项目（售后 Agent）** + **② 你的另一个项目** + **③ 简历机器人**，
> 并用自己的域名通过 HTTPS 对外提供服务。

---

## 1. 结论速览

**能跑，但必须做取舍。** 关键不是 CPU，而是**别用容器跑 MySQL**。

| 方案 | 单项目常驻内存 | 2GB 能否跑三个 |
|---|---|---|
| 默认 `docker-compose.yml`（MySQL + Redis + Chroma + app） | **≈ 1.2 GB** | ❌ 跑一个就吃紧 |
| 本方案 `docker-compose.prod.yml`（SQLite + 内嵌 Chroma，只跑 app） | **≈ 400 MB** | ✅ 可以 |

本项目**原生支持** SQLite + 内嵌 Chroma（`DB_MODE=sqlite` / `CHROMA_MODE=embedded`），
所以这个取舍不需要改代码，只改 `.env`。

### 内存预算（按 2GB 规划）

| 组件 | 常驻内存 | 说明 |
|---|---|---|
| 系统 + Docker 守护进程 | 250 – 350 MB | 不可避免 |
| **Caddy 边缘代理** | ≈ 20 MB | 全机唯一对外 80/443 的容器 |
| **本项目 app** | 350 – 450 MB | 单 worker + SQLite + 内嵌 Chroma |
| **简历机器人** | 120 – 250 MB | 取决于实现，见 §8 |
| **你另一个项目** | 150 – 400 MB | 视技术栈，见 §9 |
| 余量（页缓存 / 突发） | 400 – 800 MB | 不要榨干 |
| **合计** | **≈ 1.2 – 1.7 GB** | 装得下，但**必须配 swap** |

---

## 2. 三条取舍铁律

1. **不要用容器跑 MySQL / Redis**
   省 ~550MB。本项目用 SQLite 完全够（个人项目/演示量级），缓存同理。
2. **前端构建产物静态化，容器里不跑 Node**
   `npm run dev` 的 Vite 进程要 200–400MB。生产环境把 `frontend/dist` 交给 Caddy 直接托管。
3. **应用只开 1 个 worker**
   uvicorn 每个 worker 是一份完整进程内存。`--workers 1`（Dockerfile 已默认）。

---

## 3. 服务器初始化（一次性）

```bash
# --- 时区（容器内也设了，但宿主机对了日志才好读）---
sudo timedatectl set-timezone Asia/Shanghai

# --- ⚠️ 必做：加 swap，2GB 机器的救命绳 ---
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# 降低 swap 倾向（默认 60 太激进，会让正常内存也被换出）
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf && sudo sysctl -p
free -h   # 确认 Swap 已生效

# --- Docker ---
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

# --- 防火墙：只放行 SSH / HTTP / HTTPS ---
sudo ufw allow 22 && sudo ufw allow 80 && sudo ufw allow 443
sudo ufw enable

# --- 共享网络：所有项目 + 边缘代理都接它 ---
docker network create edge
```

> **国内服务器**：`/etc/docker/daemon.json` 建议配镜像加速，否则拉镜像很慢：
> ```json
> { "registry-mirrors": ["https://docker.m.daocloud.io", "https://dockerproxy.com"] }
> ```
> 改完 `sudo systemctl restart docker`。

---

## 4. 多项目目录布局

统一放在 `/opt/apps` 下，边缘代理用相对路径引用各项目的前端产物：

```
/opt/apps/
├── edge/                  # 共享边缘代理（唯一对外）
│   ├── docker-compose.yml
│   └── Caddyfile
├── after-sales/           # ① 本项目
│   ├── docker-compose.prod.yml
│   ├── .env.production
│   ├── data/  logs/
│   └── frontend/dist/     # 前端构建产物（Caddy 挂载它）
├── resume-bot/            # ③ 简历机器人
└── other-project/         # ② 你的另一个项目
```

---

## 5. 共享边缘代理（Caddy）

**为什么用 Caddy 而不是 Nginx**：Caddy 自动申请/续期 Let's Encrypt 证书，
每个域名只需 3 行配置，省掉 certbot + 定时续期 + 重载。代价是多约 10MB 内存。

```bash
cd /opt/apps
cp -r <本项目>/deploy ./edge      # 或直接把 deploy/ 内容拷过来
cd edge
# 编辑 Caddyfile：把 agent.example.com / resume.example.com / other.example.com
# 换成你自己的域名
docker compose up -d
```

**域名解析**：在你的域名服务商处，为每个子域名加 A 记录指向服务器公网 IP：

| 子域名 | 指向 | 对应项目 |
|---|---|---|
| `agent.你的域名` | 服务器 IP | 本项目 |
| `resume.你的域名` | 服务器 IP | 简历机器人 |
| `other.你的域名` | 服务器 IP | 另一个项目 |

> Caddy 首次启动会在几十秒内自动签发证书。若失败，先确认 80 端口
> 从公网可达（Let's Encrypt 要用 80 做 HTTP-01 校验）。

---

## 6. 部署本项目

```bash
# --- 1) 拉代码 ---
sudo mkdir -p /opt/apps && cd /opt/apps
git clone <你的仓库> after-sales && cd after-sales

# --- 2) 配置 ---
cp .env.production.example .env.production
vi .env.production
#   必改：LLM_API_KEY / EMBEDDING_API_KEY / JWT_SECRET / OPENCLAW_* / FEISHU_WEBHOOK_*
#   已默认：DB_MODE=sqlite、CACHE_BACKEND=sqlite、CHROMA_MODE=embedded

# --- 3) 数据目录权限（容器内以 uid 10001 非 root 运行）---
mkdir -p data logs
sudo chown -R 10001:10001 data logs

# --- 4) 起后端（只跑 app 一个容器）---
docker compose -f docker-compose.prod.yml up -d --build

# --- 5) 建库 + 灌初始数据 ---
docker compose -f docker-compose.prod.yml exec app python scripts/migrate_users.py
docker compose -f docker-compose.prod.yml exec app python scripts/seed_customers.py

# --- 6) 验证 ---
curl -s http://127.0.0.1:18001/health
```

### 前端构建（关键：别在 2GB 机器上裸跑 Vite）

Vite 构建峰值可吃 500MB–1GB，服务器上直接构建有 OOM 风险。三选一：

**方案 A（推荐）：本地构建后上传**
```bash
# 本地
cd frontend && npm ci && npm run build
scp -r dist/* user@服务器:/opt/apps/after-sales/frontend/dist/
```

**方案 B：服务器上构建，但限制 Node 内存**
```bash
cd frontend
NODE_OPTIONS=--max-old-space-size=768 npm ci && NODE_OPTIONS=--max-old-space-size=768 npm run build
# 若报 Killed，说明内存不够，先用方案 A
```

**方案 C：多阶段 Docker 构建**（构建完即丢弃 Node 镜像）
适合有 CI 的场景。注意磁盘占用会比 A/B 多约 1GB 临时空间。

> Caddy 挂载的是 `../frontend/dist`，所以只要产物落到
> `/opt/apps/after-sales/frontend/dist`，**不需要重启 Caddy**。

### 更新发布

```bash
cd /opt/apps/after-sales
git pull
docker compose -f docker-compose.prod.yml up -d --build
# 前端有改动时，重新构建并覆盖 dist（无需重启 Caddy）
```

---

## 7. 各项目如何接入边缘代理

任何项目只要满足两点，就能挂到同一个域名体系下：

1. **加入 `edge` 网络**
   ```yaml
   networks:
     - edge
   networks:
     edge:
       external: true
       name: edge
   ```
2. **在 `Caddyfile` 里加一段**
   ```
   你的子域名 {
       reverse_proxy 容器名:端口
   }
   ```
   然后 `docker compose restart caddy`。

> 关键：**容器之间用容器名互相访问**，不需要映射端口到宿主机，也不需要知道宿主机 IP。

---

## 8. 简历机器人怎么放进来

> 你提到想做简历机器人 —— 建议**独立成一个容器**，与主项目解耦。

资源估算与建议：

| 实现方式 | 常驻内存 | 说明 |
|---|---|---|
| 纯静态页 + 前端直连 LLM API | ≈ 0（由 Caddy 托管） | 最省，但 API Key 会暴露在前端，不推荐 |
| FastAPI + SQLite + 调用云端 LLM | 120 – 200 MB | **推荐**，可复用你现有的 LangGraph/RAG 经验 |
| 再叠一套本地向量库 + 本地模型 | 600 MB+ | 2GB 机器上不建议 |

最小可用形态：

```
resume-bot/
├── docker-compose.yml       # 加入 edge 网络
├── app/main.py              # FastAPI：/api/chat
└── dist/                    # 前端产物（可选，Caddy 托管）
```

```yaml
# resume-bot/docker-compose.yml
services:
  resume-bot:
    build: .
    container_name: resume-bot
    restart: unless-stopped
    env_file: .env
    mem_limit: 300m
    networks: [edge]
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
networks:
  edge:
    external: true
    name: edge
```

Caddyfile 里对应 `resume.你的域名 { reverse_proxy resume-bot:8000 }`（模板已备好）。

> 💡 简历机器人**不需要** MySQL/Redis/独立向量库：SQLite + 云端 LLM 就够，
> 记忆量极小。这一点和主项目的取舍思路完全一致。

---

## 9. 你的另一个项目怎么放进来

原则一样，但**先给它做一次内存体检**：

```bash
# 在它现在的环境里跑起来，然后看真实占用
docker stats --no-stream
```

- 若它也用 MySQL/Redis：**优先改成 SQLite**，或让它独占 MySQL 而本项目不用（本项目已可无 MySQL）
- 若它是 Node/Java 服务：Node 加 `--max-old-space-size=256`；Java 加 `-Xmx256m`
- 给它也设 `mem_limit`，防止互相抢内存

> **如果三个项目加起来超过 1.7GB**：考虑把服务器升到 4GB（通常差价不大），
> 或者把简历机器人做成纯静态 + 边缘函数（不占常驻内存）。

---

## 10. 运维：监控与排错

```bash
# --- 实时内存 ---
docker stats --no-stream
free -h

# --- 谁在被 OOM Kill ---
dmesg -T | grep -i -E 'oom|killed process'

# --- 容器日志 ---
docker logs -f easkb-app --tail 100
docker logs -f edge-caddy --tail 50

# --- 磁盘（镜像/构建缓存最容易堆满）---
df -h
docker system df

# ⚠️⚠️ 下面这条慎用 —— 它会删掉【所有未被容器使用的镜像】和【全部构建缓存】，
#      不只是"清理垃圾"。真实踩过：
#        · python:3.12-slim 被删 → 下次构建必须联网重拉，镜像源一抖就构建失败
#        · apt / pip 中间层全没了 → 重建时依赖要重新下载 3~8 分钟
#        · 隐藏问题会在这时暴露：单一 PyPI 源文件 404 导致 build 直接挂掉
#      只想清悬空镜像（安全得多）请用：
#        docker image prune          # 只删 <none> 悬空镜像
#        docker builder prune        # 只清构建缓存
#      确认要释放全部空间时再执行下面这条：
docker system prune -af --volumes   # ⚠️ --volumes 还会删未使用的卷，确认后再执行
```

> **被 `prune -a` 清掉之后怎么快速恢复**：先把基础镜像拉回来，再构建。
> ```bash
> docker pull python:3.12-slim && docker pull node:20-alpine
> ```
> 跳过 `-a` 的日常清理（`docker image prune`）不会动基础镜像，推荐日常用它。

**常见问题**

| 现象 | 原因 | 处理 |
|---|---|---|
| `Killed` / 容器反复重启 | 内存超限被 OOM | 调低 `mem_limit`，检查是否有项目漏设；确认 swap 已开 |
| Caddy 签发证书失败 | 80 端口不通 | 检查云厂商安全组 + ufw；确认域名 A 记录已生效 |
| 前端刷新 404 | SPA history 路由缺回退 | Caddyfile 里的 `try_files {path} /index.html` |
| `/api` 请求 404 | 没剥前缀 | Caddyfile 里的 `uri strip_prefix /api` |
| 容器内写 `data/` 报权限拒绝 | 宿主机目录属主不对 | `sudo chown -R 10001:10001 data logs` |
| 镜像构建特别慢 | 没配镜像加速 | 见 §3 的 `registry-mirrors` |
| 日志把磁盘写满 | 没配日志轮转 | 三个 compose 都已配 `max-size: 10m` |

---

## 11. 上线安全清单

- [ ] `JWT_SECRET` 用随机串：`python -c "import secrets;print(secrets.token_urlsafe(48))"`
- [ ] 默认管理员密码 `admin123` **必须改**
- [ ] 数据库口令、云厂商 API Key 只放 `.env.production`（已 gitignore）
- [ ] **飞书群 webhook 只放 `.env`**，绝不写进 `app/config/feishu_routes.yaml`（该文件被 git 跟踪）
- [ ] 只暴露 80/443；应用端口绑定 `127.0.0.1` 或干脆不映射
- [ ] 云厂商安全组同样只放行 22/80/443
- [ ] SSH 禁用密码登录，改用密钥
- [ ] 定期备份 `data/`（SQLite / Chroma / checkpoint 都在里面）
- [ ] `docker compose -f docker-compose.prod.yml ps` 确认无 unhealthy 容器

---

## 12. 建议的执行顺序

1. 买服务器（**能上 4GB 就上 4GB**，2GB 是能用但没余量）
2. 按 §3 做初始化（**swap 和镜像加速别跳过**）
3. **先看 §13**：服务器上已经有 nginx 的话，**根本不用装 Caddy**，直接复用
4. 按 §4 建目录、按 §6 部署本项目（先用 `127.0.0.1:18001/health` 验证后端）
5. 按 §13 配 nginx + 申请证书，用 `agent.你的域名` 验证前端 + API
6. 再按 §9 接入另一个项目、按 §8 接入简历机器人
7. 每加一个项目，跑一次 `docker stats` 看内存，留够余量

---

## 13. 服务器上已经有 nginx 怎么办（**推荐路径**）

很多云服务器（阿里云、腾讯云镜像）**默认就装了宿主机 nginx**，80/443 被它占着。
这时候**不要**再装 Caddy —— 一层反代就够了，多一层多一个故障点。

### 先确认 80/443 被谁占着

```bash
sudo ss -tulpn | grep -E ':(80|443)\b'
systemctl is-active nginx
docker ps --format '{{.Names}}' | grep -i nginx    # 有输出说明 nginx 在容器里
```

- 宿主机 nginx（`systemctl is-active nginx` 返回 `active`）→ **走本节**
- 容器里的 nginx → 把我们的容器接进它所在的 docker 网络，用容器名反代
- 都没装 → 走 §5 的 Caddy 方案

### 步骤

**① 确认 nginx.conf 有没有 include conf.d**

```bash
grep -n 'include' /etc/nginx/nginx.conf
```

- 有 `include /etc/nginx/conf.d/*.conf;` → 直接加文件，**不动主配置**
- 没有（阿里云/Anolis 镜像常见）→ 补一行：

```bash
sudo sed -i 's|include\s\+/etc/nginx/mime\.types;|&\n    include /etc/nginx/conf.d/*.conf;|' /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

**② 放站点配置**

仓库里 [`deploy/nginx-agent.conf`](../deploy/nginx-agent.conf) 就是现成的模板，
把所有 `<你的域名>` 替换掉，然后：

```bash
sudo cp deploy/nginx-agent.conf /etc/nginx/conf.d/agent.conf
sudo nano /etc/nginx/conf.d/agent.conf      # 替换 <你的域名>
sudo nginx -t && sudo systemctl reload nginx
```

**③ 前端产物**

```bash
# 服务器没装 Node 时，用 Docker 构建（见 deploy/docker-compose.web.yml）
docker compose -f deploy/docker-compose.web.yml run --rm web-build
ls -la frontend/dist/            # 应看到 index.html + assets/
```

**④ 先跑通 HTTP，再上 HTTPS**

别一步到位。先用 `curl -sI http://你的域名` 确认 200，再申请证书。

**⑤ 申请证书**

```bash
# 方式 A：宿主机装了 certbot
sudo certbot --nginx -d 你的域名

# 方式 B：仓库里没有 certbot 包（部分发行版确实没有）→ 用 Docker 版
sudo docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v /var/lib/letsencrypt:/var/lib/letsencrypt \
  -v /opt/apps/after-sales/frontend/dist:/var/www/html \
  certbot/certbot certonly --webroot -w /var/www/html \
  -d 你的域名 --agree-tos --non-interactive -m 你的邮箱
```

方式 B 不会改你的 nginx 配置，证书落在
`/etc/letsencrypt/live/你的域名/`，再把 `nginx-agent.conf` 里 443 那段的
`ssl_certificate` 路径对上即可。

**⑥ 自动续期**（方式 B 必须自己配，方式 A 会自动加）

```bash
echo '20 3 * * * root docker run --rm -v /etc/letsencrypt:/etc/letsencrypt -v /var/lib/letsencrypt:/var/lib/letsencrypt -v /opt/apps/after-sales/frontend/dist:/var/www/html certbot/certbot renew --webroot -w /var/www/html --quiet && systemctl reload nginx' | sudo tee /etc/cron.d/certbot-renew
```

---

## 14. 实际部署踩过的坑（都已修复，记录备查）

这些是**真实部署时踩到的**，多数已在代码里修掉；留档是为了以后不再重犯。

| 现象 | 根因 | 处理 |
|---|---|---|
| `FileNotFoundError: 'I:\\XMWJ\\...'` | 初始化脚本把开发机的绝对路径写死在 `os.chdir()` | **已修**：改成从 `__file__` 推导；`clone_check.py` 加了硬编码路径检查 |
| `sqlite3.OperationalError: unable to open database file` | 脚本 `chdir` 到了别处，而 SQLite 用的是相对路径 `sqlite:///./data/app.db` | 确保 CWD 是项目根即可 |
| `git clone` 报 `Permission denied` | 目标目录属主是 root 且权限 700 | 换到自己的目录，或先 `sudo chown -R $USER` |
| 容器里连网关报 `Connection refused` | 网关只绑 `127.0.0.1:15568`，而容器里的 `host.docker.internal` 是 docker 网桥 IP | **不影响功能**：方向是「网关 → 我们」，反向探活失败只影响 `/health` 的展示 |
| 飞书发消息没反应 | 网关 Provider 的 `baseUrl` 端口写成 18000，容器实际发布在 **18001** | 改 `~/.openclaw/openclaw.json` 里的 `models.providers.<名字>.baseUrl` |
| `certbot: command not found` / `No match for argument` | 发行版仓库里没有 certbot | 用 §13 的 Docker 版 certbot |
| `systemctl --user` 报 `Failed to connect to bus` | SSH 会话没有 `XDG_RUNTIME_DIR` | `export XDG_RUNTIME_DIR=/run/user/$(id -u)` 后再执行 |
| 退款/客户资产页显示「未登录」 | 这几个页面自建了 axios 实例，没带 `Authorization` 头 | **已修**：统一改用共享客户端 |
| `data/` 目录容器内写不进去 | 宿主机目录属主不是容器内的 uid | `sudo chown -R 10001:10001 data logs` |

