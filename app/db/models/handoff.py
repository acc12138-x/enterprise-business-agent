"""转人工工单与会话消息模型。

转人工（HumanHandoff）把「工作流中断」升级为一条**可运营的工单**：
    queued（待接入）→ claimed（已接管）→ closed（已结束）
                     ↘ timeout（超时未接入）

关键作用：`status == "claimed"` 时，**入站消息不再启动 AI 工作流**
（见 app/services/handoff_service.py 的 is_ai_muted），
避免坐席正在回复时 AI 抢话。

会话消息（ConversationMessage）把入站/出站消息**按条落库**（不是逐 token），
让「人工客服台」「会话监控」「常见问题分析」共用一套数据。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.base import Base


STATUS_LABELS = {
    "queued": "待接入",
    "claimed": "已接管",
    "closed": "已结束",
    "timeout": "已超时",
}

# 触发转人工的来源
TRIGGER_LABELS = {
    "human_intent": "用户主动要求",
    "complaint": "投诉",
    "low_confidence": "置信度过低",
    "rule_need_human": "规则要求人工",
    "need_human_action": "高风险动作",
}

# 消息角色
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_HUMAN = "human_agent"      # 坐席以「本人」身份回复
ROLE_SYSTEM = "system"          # 状态切换提示


class HumanHandoff(Base):
    __tablename__ = "human_handoffs"

    handoff_id = Column(String(32), primary_key=True)
    thread_id = Column(String(128), index=True, nullable=False)
    sender_open_id = Column(String(128), index=True, default="")

    customer_id = Column(String(32), index=True, nullable=True)
    lead_id = Column(String(32), index=True, nullable=True)

    trigger = Column(String(32), default="human_intent", index=True)
    reason = Column(String(256), default="")

    # 给坐席看的上下文
    summary = Column(Text, default="")
    last_user_msg = Column(Text, default="")

    status = Column(String(16), default="queued", index=True, nullable=False)

    claimed_by = Column(Integer, index=True, nullable=True)
    claimed_by_name = Column(String(64), default="")
    claimed_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    close_note = Column(String(256), default="")

    # 通知结果（JSON 文本，便于排查"到底通知到谁了"）
    notify_result = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        def _iso(dt):
            return dt.isoformat() if dt else None

        return {
            "handoff_id": self.handoff_id,
            "thread_id": self.thread_id,
            "sender_open_id": self.sender_open_id or "",
            "customer_id": self.customer_id,
            "lead_id": self.lead_id,
            "trigger": self.trigger or "human_intent",
            "trigger_label": TRIGGER_LABELS.get(self.trigger or "", self.trigger or ""),
            "reason": self.reason or "",
            "summary": self.summary or "",
            "last_user_msg": self.last_user_msg or "",
            "status": self.status or "queued",
            "status_label": STATUS_LABELS.get(self.status or "", self.status or ""),
            "claimed_by": self.claimed_by,
            "claimed_by_name": self.claimed_by_name or "",
            "claimed_at": _iso(self.claimed_at),
            "closed_at": _iso(self.closed_at),
            "close_note": self.close_note or "",
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class ConversationMessage(Base):
    """会话消息落库（一行一条，非逐 token）。"""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(128), index=True, nullable=False)

    role = Column(String(16), default=ROLE_USER, index=True)
    content = Column(Text, default="")

    sender_open_id = Column(String(128), default="", index=True)
    sender_name = Column(String(64), default="")

    # 这条消息属于哪次转人工（接管期间的消息才有）
    handoff_id = Column(String(32), index=True, nullable=True)
    # 命中的意图（AI 回复时记录，便于分析常见问题）
    intent = Column(String(32), default="", index=True)

    meta = Column(Text, default="")     # JSON 扩展
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    def to_dict(self) -> dict:
        def _iso(dt):
            return dt.isoformat() if dt else None

        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "role": self.role or ROLE_USER,
            "content": self.content or "",
            "sender_open_id": self.sender_open_id or "",
            "sender_name": self.sender_name or "",
            "handoff_id": self.handoff_id,
            "intent": self.intent or "",
            "created_at": _iso(self.created_at),
        }
