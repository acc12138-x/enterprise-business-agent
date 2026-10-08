from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app.api.deps import get_gateway_caller
from app.workflows.graph import graph
from app.config.settings import get_settings

router = APIRouter(prefix="/threads", tags=["stream"])


# ============================================================
# 决策解析（与 chat.py 保持一致）
# ============================================================
APPROVE_WORDS = ["同意", "确认", "批准", "通过", "可以", "好的", "好", "approve", "yes", "ok", "okay"]
REJECT_WORDS = ["拒绝", "驳回", "不同意", "不行", "取消", "否", "reject", "no"]


def _parse_decision(text: str):
    t = (text or "").strip().lower()
    if not t:
        return None
    for w in REJECT_WORDS:
        if t == w.lower() or t.startswith(w.lower()):
            return "block_revise: 用户拒绝"
    for w in APPROVE_WORDS:
        if t == w.lower() or t.startswith(w.lower()):
            return "approve"
    return None


def _hitl_age_seconds(snapshot) -> float:
    """当前中断的存活秒数。取不到时间戳就返回 0。"""
    try:
        created_at = getattr(snapshot, "created_at", None)
        if not created_at:
            return 0.0
        if isinstance(created_at, str):
            # ISO 格式，可能是 "2026-01-01T12:00:00+00:00" 或 "...Z"
            s = created_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        else:
            dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 0.0


# ============================================================
# SSE 工具
# ============================================================
def safe_json(obj: Any) -> str:
    def _default(o):
        if hasattr(o, "value"):
            return {"__interrupt__": True, "value": _to_serializable(o.value)}
        if hasattr(o, "to_json"):
            try:
                return o.to_json()
            except Exception:
                pass
        return str(o)
    return json.dumps(obj, ensure_ascii=False, default=_default)


def _to_serializable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(x) for x in obj]
    return str(obj)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {safe_json(data)}\n\n"


def classify_event(node_name: str, node_output: Any) -> tuple[str, dict]:
    if node_name == "__interrupt__":
        items = node_output if isinstance(node_output, (list, tuple)) else [node_output]
        first = items[0] if items else {}
        payload = getattr(first, "value", first)
        return "hitl", {"type": "hitl", "payload": _to_serializable(payload)}
    if isinstance(node_output, dict):
        status = node_output.get("flow_status")
        if status in ("succeeded", "rejected", "waiting"):
            return "milestone", {"type": "milestone", "node": node_name, "status": status}
    return "status", {"type": "status", "node": node_name}


def build_fallback_answer(state: dict) -> str:
    intent = state.get("intent", "")
    if intent == "human":
        return "已转人工，客服稍后接入。"
    if intent == "complaint":
        return "已记录您的反馈，正在转交人工处理。"
    return state.get("answer", "") or ""


# ============================================================
# /runs/stream
# ============================================================
@router.post("/{thread_id}/runs/stream")
async def stream_run(thread_id: str, request: Request,
                     caller: dict = Depends(get_gateway_caller)):
    body = await request.json()
    user_input = body.get("input", {})
    message = user_input.get("message", "")
    messages = user_input.get("messages", [])

    init = {
        "thread_id": thread_id,
        "user_input": message,
        "slots": {},
    }
    config = {"configurable": {"thread_id": thread_id}}

    # ---------- 转人工：坐席接管中则 AI 静默，不跑工作流 ----------
    from app.services import handoff_service as _hs
    muted = _hs.before_turn(thread_id, message)

    async def event_generator() -> AsyncGenerator[str, None]:
        yield sse("status", {"type": "status", "message": "开始处理"})

        if muted:
            # 静默期间默认只回一个零宽空格（HANDOFF_MUTED_MODE，默认 never）。
            # ⚠️ 不能返回空字符串 —— 网关把空 content 判定为生成失败，
            # 会给用户回「Agent couldn't generate a response」。
            _ans = _hs.muted_reply(muted)
            if _ans != _hs.SILENT:
                _hs.record_message(thread_id, "assistant", _ans,
                                   handoff_id=muted["handoff_id"])
            yield sse("terminal", {
                "type": "terminal", "status": "waiting", "answer": _ans,
                "confidence": 0.0, "citations": [], "intent": "human",
            })
            return

        try:
            async for chunk in graph.astream(init, config, stream_mode="updates"):
                for node_name, node_output in chunk.items():
                    event_type, payload = classify_event(node_name, node_output)
                    yield sse(event_type, payload)

            snapshot = graph.get_state(config)
            final_values = snapshot.values if snapshot else {}

            if snapshot and snapshot.next:
                yield sse("hitl", {
                    "type": "hitl",
                    "thread_id": thread_id,
                    "reason": final_values.get("hitl_reason") or "需要人工确认",
                    "intent": final_values.get("intent"),
                })
                return

            answer = final_values.get("answer", "") or build_fallback_answer(final_values)
            # 会话落库 + 必要时转人工（通知坐席）
            _hs.after_turn(thread_id, message, answer, final_values)
            yield sse("terminal", {
                "type": "terminal",
                "status": final_values.get("flow_status", "succeeded"),
                "answer": answer,
                "confidence": final_values.get("confidence", 0.0),
                "citations": final_values.get("citations", []),
                "intent": final_values.get("intent"),
            })
        except Exception as e:
            yield sse("terminal", {"type": "terminal", "status": "failed", "error": str(e)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================
# /runs/resume（第 2 项：支持 message 字段 + 超时）
# ============================================================
@router.post("/{thread_id}/runs/resume")
async def resume_run(thread_id: str, request: Request,
                     caller: dict = Depends(get_gateway_caller)):
    body = await request.json()
    config = {"configurable": {"thread_id": thread_id}}
    settings = get_settings()
    timeout_sec = getattr(settings, "hitl_timeout_seconds", 1800)

    # 优先取 decision 字段；没有就解析 message
    decision = body.get("decision")
    if not decision:
        msg = body.get("message") or body.get("text") or body.get("input", {}).get("message", "")
        decision = _parse_decision(msg)

    # 检查超时
    try:
        snapshot = graph.get_state(config)
    except Exception:
        snapshot = None

    if snapshot and snapshot.next:
        age = _hitl_age_seconds(snapshot)
        if age > timeout_sec:
            try:
                result = graph.invoke(Command(resume="block_revise: timeout"), config=config)
                return {
                    "thread_id": thread_id,
                    "decision": "timeout",
                    "timed_out": True,
                    "age_seconds": int(age),
                    "flow_status": result.get("flow_status", "cancelled"),
                    "answer": "【超时】操作已自动取消，请重新发起。",
                    "intent": result.get("intent"),
                }
            except Exception as e:
                return {"thread_id": thread_id, "status": "failed", "error": str(e)}

    if not decision:
        return {
            "thread_id": thread_id,
            "status": "waiting_decision",
            "message": "请回复「同意」或「拒绝」",
        }

    try:
        result = graph.invoke(Command(resume=decision), config=config)
        answer = result.get("answer", "") or build_fallback_answer(result)
        return {
            "thread_id": thread_id,
            "decision": decision,
            "flow_status": result.get("flow_status", "succeeded"),
            "answer": answer,
            "intent": result.get("intent"),
        }
    except Exception as e:
        return {"thread_id": thread_id, "status": "failed", "error": str(e)}


# ============================================================
# /state
# ============================================================
@router.get("/{thread_id}/state")
async def get_state(thread_id: str,
                    caller: dict = Depends(get_gateway_caller)):
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if not snapshot:
        return {"thread_id": thread_id, "exists": False}

    settings = get_settings()
    timeout_sec = getattr(settings, "hitl_timeout_seconds", 1800)
    age = _hitl_age_seconds(snapshot) if snapshot.next else 0

    return {
        "thread_id": thread_id,
        "exists": True,
        "next": list(snapshot.next) if snapshot.next else [],
        "is_interrupted": bool(snapshot.next),
        "hitl_age_seconds": int(age),
        "hitl_timeout_seconds": timeout_sec,
        "values": {
            "intent": snapshot.values.get("intent"),
            "flow_status": snapshot.values.get("flow_status"),
            "hitl_pending": snapshot.values.get("hitl_pending"),
            "answer": (snapshot.values.get("answer") or "")[:200],
        },
    }
