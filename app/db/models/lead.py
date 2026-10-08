"""客户线索与跟进记录模型。

线索（Lead）描述一条从「陌生咨询」到「成交」的完整链路：
    谁负责（owner_id）→ 聊到哪一步（stage）→ 报没报价（quoted/quote_amount）
    → 成没成交（won/deal_amount）→ 谁的责任（owner_id + 跟进时间线）

跟进记录（LeadFollowup）是这条链路的时间线，**阶段变化会自动留痕**，
不依赖人工补记。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.db.base import Base


# ============================================================
# 阶段状态机
# ============================================================
STAGES = ["new", "contacted", "quoted", "negotiating", "won", "lost"]

STAGE_LABELS = {
    "new": "新建",
    "contacted": "已联系",
    "quoted": "已报价",
    "negotiating": "谈判中",
    "won": "已成交",
    "lost": "已流失",
}

# 允许的流转（终态没有出边）
STAGE_FLOW = {
    "new":         ["contacted", "lost"],
    "contacted":   ["quoted", "negotiating", "won", "lost"],
    "quoted":      ["negotiating", "won", "lost"],
    "negotiating": ["won", "lost"],
    "won":         [],
    "lost":        ["contacted"],      # 允许「重新激活」已流失线索
}

OPEN_STAGES = ["new", "contacted", "quoted", "negotiating"]   # 未结束
FINAL_STAGES = ["won", "lost"]

SOURCES = ["feishu", "phone", "referral", "website", "other"]
SOURCE_LABELS = {
    "feishu": "飞书咨询",
    "phone": "电话",
    "referral": "转介绍",
    "website": "官网",
    "other": "其他",
}

PRIORITIES = ["high", "normal", "low"]
PRIORITY_LABELS = {"high": "高", "normal": "中", "low": "低"}


class Lead(Base):
    __tablename__ = "leads"

    lead_id = Column(String(32), primary_key=True)
    # 关联已有客户（可空 —— 线索可能还没建档）
    customer_id = Column(String(32), index=True, nullable=True)

    # 联系人信息（冗余存一份，便于未建档线索独立存在）
    name = Column(String(64), nullable=False)
    phone = Column(String(32), default="", index=True)
    company = Column(String(128), default="")

    source = Column(String(32), default="other", index=True)
    need_desc = Column(Text, default="")

    # ★ 谁负责
    owner_id = Column(Integer, index=True, nullable=True)
    owner_name = Column(String(64), default="")

    # ★ 聊到哪一步
    stage = Column(String(16), default="new", index=True, nullable=False)
    priority = Column(String(16), default="normal")

    # ★ 报没报价
    quoted = Column(Boolean, default=False)
    quote_amount = Column(Integer, default=0)      # 单位：元
    quote_at = Column(DateTime, nullable=True)

    # ★ 成没成交
    won = Column(Boolean, default=False)
    deal_amount = Column(Integer, default=0)       # 单位：元
    won_at = Column(DateTime, nullable=True)
    lost_at = Column(DateTime, nullable=True)
    lost_reason = Column(String(256), default="")

    # 下一步计划
    next_action = Column(String(256), default="")
    next_follow_at = Column(DateTime, nullable=True)

    # 来源会话（AI 自动捕获时记录，便于回溯原始对话）
    source_thread_id = Column(String(128), default="")
    source_msg = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def can_transition_to(self, new_stage: str) -> bool:
        return new_stage in STAGE_FLOW.get(self.stage or "new", [])

    def to_dict(self) -> dict:
        def _iso(dt):
            return dt.isoformat() if dt else None

        return {
            "lead_id": self.lead_id,
            "customer_id": self.customer_id,
            "name": self.name,
            "phone": self.phone or "",
            "company": self.company or "",
            "source": self.source or "other",
            "source_label": SOURCE_LABELS.get(self.source or "other", self.source or ""),
            "need_desc": self.need_desc or "",
            "owner_id": self.owner_id,
            "owner_name": self.owner_name or "",
            "stage": self.stage or "new",
            "stage_label": STAGE_LABELS.get(self.stage or "new", self.stage or ""),
            "priority": self.priority or "normal",
            "priority_label": PRIORITY_LABELS.get(self.priority or "normal", ""),
            "quoted": bool(self.quoted),
            "quote_amount": self.quote_amount or 0,
            "quote_at": _iso(self.quote_at),
            "won": bool(self.won),
            "deal_amount": self.deal_amount or 0,
            "won_at": _iso(self.won_at),
            "lost_at": _iso(self.lost_at),
            "lost_reason": self.lost_reason or "",
            "next_action": self.next_action or "",
            "next_follow_at": _iso(self.next_follow_at),
            "source_thread_id": self.source_thread_id or "",
            "is_open": (self.stage or "new") in OPEN_STAGES,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class LeadFollowup(Base):
    """跟进记录：线索的时间线。阶段/报价变化自动写一条。"""

    __tablename__ = "lead_followups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(String(32), index=True, nullable=False)

    user_id = Column(Integer, nullable=True)
    user_name = Column(String(64), default="")

    channel = Column(String(32), default="other")   # feishu/phone/wechat/meeting/other
    content = Column(Text, default="")

    # 自动留痕：阶段变化
    stage_from = Column(String(16), default="")
    stage_to = Column(String(16), default="")

    # 自动留痕：报价变化（描述性文本，如 "3000 → 5000"）
    quote_change = Column(String(64), default="")

    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    def to_dict(self) -> dict:
        def _iso(dt):
            return dt.isoformat() if dt else None

        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "user_id": self.user_id,
            "user_name": self.user_name or "系统",
            "channel": self.channel or "other",
            "content": self.content or "",
            "stage_from": self.stage_from or "",
            "stage_to": self.stage_to or "",
            "stage_to_label": STAGE_LABELS.get(self.stage_to or "", ""),
            "quote_change": self.quote_change or "",
            "created_at": _iso(self.created_at),
        }
