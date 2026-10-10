"""OpenClaw Skill：转人工客服。

⚠️ 这个 Skill 以前是个**空壳** —— 只 `return {"status": "queued", ...}`，
不建单、不通知任何人。而真实的转人工走的是另一条路径：

    意图命中 human → hitl_gate 挂起 → 入口层 after_turn()
    → handoff_service.create_handoff() → 飞书通知坐席

于是同一个业务能力有两套实现，且**只有图层那套是真的** ——
网关侧若真按这个 schema 调过来，用户会以为转了人工，其实没人被通知。

现在它内部转调 handoff_service，两条路径统一。返回值保持向后兼容
（原字段都在），额外补上 handoff_id，便于调用方追踪。
"""
from __future__ import annotations

from typing import Any, Dict


def notify_human(thread_id: str, reason: str = "") -> Dict[str, Any]:
    """转人工客服：真正建单 + 通知坐席。"""
    from app.services.handoff_service import create_handoff

    try:
        res = create_handoff(
            thread_id=thread_id,
            sender_open_id="",
            trigger="human_intent",
            reason=reason or "网关侧调用转人工",
            last_user_msg="",
        )
        return {
            "thread_id": thread_id,
            "reason": reason,
            "status": "queued",
            "handoff_id": res.get("handoff_id"),
            "reused": bool(res.get("reused")),
            "notify": res.get("notify") or {},
            "message": "已转人工，请等待客服接入",
        }
    except Exception as e:
        # 建单失败不能假装成功 —— 原实现正是在这里静默骗人的
        print(f"[SKILL] notify_human 建单失败: {e}")
        return {
            "thread_id": thread_id,
            "reason": reason,
            "status": "failed",
            "handoff_id": None,
            "message": f"转人工失败：{e}",
        }


SCHEMA = {
    "name": "notify_human",
    "description": "转人工客服（建转人工工单并通知坐席；同一会话重复调用会复用未结束的工单）",
    "parameters": {
        "type": "object",
        "properties": {
            "thread_id": {"type": "string", "description": "会话 id"},
            "reason": {"type": "string", "description": "转人工原因，会展示给坐席"},
        },
        "required": ["thread_id"],
    },
}
