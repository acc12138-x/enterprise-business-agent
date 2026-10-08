
from __future__ import annotations

import os

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_permission
from app.api.routes import approvals, chat, tickets, knowledge, stream, openai_compat, admin, engineers, audit, customers, refunds, sla, users, auth, feishu, leads, handoffs
from app.api.schemas.models import HealthResponse
import asyncio
from contextlib import asynccontextmanager
from app.config.settings import get_settings

settings = get_settings()

# 缓存管理属于系统配置范畴
_cache_view = require_permission("system.view")
_cache_edit = require_permission("system.edit")


# ============================================================
# SLA 后台定时扫描（每 5 分钟）
# ============================================================
async def sla_scanner():
    """后台循环：每 5 分钟扫一次 SLA。"""
    # 启动后延迟 10 秒首次扫描
    await asyncio.sleep(10)
    while True:
        try:
            from app.services.sla_service import scan_all_tickets
            stats = scan_all_tickets()
            print(f"[SLA] 扫描完成: {stats}")
        except Exception as e:
            print(f"[SLA] 扫描失败: {e}")
        await asyncio.sleep(300)  # 5 分钟


# ============================================================
# 转人工后台巡检（每 60 秒）
# ------------------------------------------------------------
# 处理「没人认领」的转人工工单：超时 → 标记 timeout + 升级通知主管 + 告知用户。
# 注意：patrol_timeouts() 里有数据库查询和飞书 HTTP 调用，是同步函数，
# 必须丢进线程池执行，否则会卡住 event loop（照 _warmup_rag 的做法）。
# ============================================================
async def handoff_patrol():
    """后台循环：巡检超时未认领的转人工工单。"""
    await asyncio.sleep(20)          # 启动后延迟 20 秒，避开启动高峰
    interval = int(getattr(settings, "handoff_patrol_interval", 60) or 60)
    while True:
        try:
            from app.services.handoff_service import patrol_timeouts
            stats = await asyncio.get_running_loop().run_in_executor(None, patrol_timeouts)
            if stats.get("timed_out"):
                print(f"[HANDOFF] 巡检完成: {stats}")
        except Exception as e:
            print(f"[HANDOFF] 巡检失败: {e}")
        await asyncio.sleep(max(15, interval))


async def _warmup_rag():
    """后台预热 RAG：加载 Chroma、embedding、BM25。

    避免第一条用户消息等 30 秒。
    """
    import asyncio as _aio

    def _sync_warmup():
        import time as _t
        t0 = _t.time()

        # 1. 预热 embedding API
        try:
            from app.rag.embedding import embed_query
            embed_query("warmup")
            print(f"[WARMUP] embedding 就绪 ({_t.time()-t0:.2f}s)")
        except Exception as e:
            print(f"[WARMUP] embedding 失败: {e}")

        # 2. 预热 Chroma（触发 HNSW 索引加载）
        t1 = _t.time()
        try:
            from app.workflows.nodes.rag_search import get_retriever
            r = get_retriever()
            cnt = r.count()
            print(f"[WARMUP] Chroma 就绪 chunks={cnt} ({_t.time()-t1:.2f}s)")
        except Exception as e:
            print(f"[WARMUP] Chroma 失败: {e}")

        # 3. 预热 BM25 索引（走一次空查询）
        t2 = _t.time()
        try:
            r.search_bm25("warmup", k=1)
            print(f"[WARMUP] BM25 就绪 ({_t.time()-t2:.2f}s)")
        except Exception as e:
            print(f"[WARMUP] BM25 失败: {e}")

        # 4. 预热向量检索（真正触发 Chroma query）
        t3 = _t.time()
        try:
            r.search_vector("warmup", k=1)
            print(f"[WARMUP] 向量检索就绪 ({_t.time()-t3:.2f}s)")
        except Exception as e:
            print(f"[WARMUP] 向量检索失败: {e}")

        print(f"[WARMUP] 全部完成 ({_t.time()-t0:.2f}s)")

    # 放到线程池，别阻塞 event loop
    await _aio.get_running_loop().run_in_executor(None, _sync_warmup)


async def _probe_openclaw():
    """启动时探活 OpenClaw 网关（放到线程池，不阻塞启动）。"""
    from app.gateway.health import probe

    st = await asyncio.get_running_loop().run_in_executor(None, probe)
    if st.get("reachable") is True:
        print(f"[OpenClaw] 网关可达：{st.get('gateway_url')}（{st.get('message')}）")
    else:
        print(f"[OpenClaw] ⚠️ 网关不可达：{st.get('gateway_url')}（{st.get('message')}）")


@asynccontextmanager
async def lifespan(app):
    """启动时挂后台任务。"""
    task = asyncio.create_task(sla_scanner())
    print("[启动] SLA 定时扫描已启动（每 5 分钟）")
    # 转人工超时巡检
    patrol = asyncio.create_task(handoff_patrol())
    print("[启动] 转人工超时巡检已启动")
    # 后台预热（不阻塞 FastAPI 启动）
    asyncio.create_task(_warmup_rag())
    print("[启动] RAG 预热任务已挂载")
    # OpenClaw 网关探活（仅当 OPENCLAW_ENABLED=true）
    if getattr(settings, "openclaw_enabled", False):
        asyncio.create_task(_probe_openclaw())
        print("[启动] OpenClaw 网关探活任务已挂载")
    else:
        print("[启动] OpenClaw 未启用（OPENCLAW_ENABLED=false），跳过探活")
    yield
    task.cancel()
    print("[关闭] SLA 定时扫描已停止")
    patrol.cancel()
    print("[关闭] 转人工超时巡检已停止")

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="企业对话式企业业务 Agent 平台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(tickets.router)
app.include_router(knowledge.router)
app.include_router(stream.router)
app.include_router(openai_compat.router)
app.include_router(admin.router)
app.include_router(engineers.router)
app.include_router(users.router)
app.include_router(approvals.router)
app.include_router(auth.router)
app.include_router(audit.router)
app.include_router(customers.router)
app.include_router(refunds.router)
app.include_router(sla.router)
app.include_router(feishu.router)
app.include_router(leads.router)
app.include_router(handoffs.router)


@app.get("/cache/stats")
async def cache_stats(user: dict = Depends(_cache_view)):
    from app.services.cache_service import get_cache
    return get_cache().stats()


@app.post("/cache/clear")
async def cache_clear(user: dict = Depends(_cache_edit)):
    from app.services.cache_service import get_cache
    n = get_cache().clear_all()
    return {"cleared": n}


@app.post("/cache/cleanup")
async def cache_cleanup(user: dict = Depends(_cache_edit)):
    from app.services.cache_service import get_cache
    n = get_cache().clear_expired()
    return {"cleared": n}


@app.get("/health", response_model=HealthResponse)
async def health():
    from app.gateway.health import get_state
    return HealthResponse(status="ok", openclaw=get_state())


@app.get("/")
async def root():
    """根路径：仅暴露应用名与文档入口，故意公开（部署探活用）。"""
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
