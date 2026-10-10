FROM python:3.12-slim

# ---------- 基础环境 ----------
# TZ                  : 容器默认 UTC，会让日志与 SLA 计算偏移 8 小时
# PYTHONUNBUFFERED    : 日志实时输出（docker logs 才能看到）
# PYTHONDONTWRITEBYTECODE : 不写 .pyc，减少小磁盘压力
# MALLOC_ARENA_MAX    : 限制 glibc 内存竞技场，显著降低小内存机器 RSS
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MALLOC_ARENA_MAX=2

# 换 Debian 源为阿里云（避免国内访问 deb.debian.org 超时）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- 依赖（单独一层，利用构建缓存）----------
COPY requirements-prod.txt .
#
# ⚠️ 必须配多个 PyPI 源互为兜底。
# 只写一个镜像源会让整个构建挂掉 —— 国内镜像经常出现「索引里有这个版本、
# 但对应的文件 404」的残缺情况。真实踩过：阿里云镜像上
# pydantic-2.13.5 的 wheel 文件 404，导致 docker build 直接失败。
# 清华源作主源（国内快且是全量镜像），阿里云与官方 PyPI 兜底。
RUN pip install -r requirements-prod.txt \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
      --extra-index-url https://pypi.org/simple \
      --retries 5 --timeout 60

# ---------- 代码 ----------
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY pyproject.toml .

# ---------- 数据目录 + 非 root 用户 ----------
# 注意：宿主机挂载的 ./data 与 ./logs 需要 chown 到 10001，否则容器内无权写入
RUN mkdir -p /app/data /app/logs \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 单 worker：2GB 机器上每多一个 worker 就多一份完整进程内存
# --no-access-log：小服务器上省 IO 与日志体积（业务日志仍在）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
