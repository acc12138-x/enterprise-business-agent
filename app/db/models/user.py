"""User 模型：人员统一表（合并 Engineer + 权限）。"""
from __future__ import annotations
import json
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text
from app.db.base import Base


# 角色 → 默认权限
ROLE_PERMISSIONS = {
    "admin": ["*"],
    "supervisor": [
        "ticket.view", "ticket.accept", "ticket.reject", "ticket.resolve", "ticket.reassign",
        "refund.view", "refund.approve", "refund.execute",
        "audit.view", "user.view", "user.edit",
        "customer.view", "customer.create",
        "sla.view", "sla.edit",
        "knowledge.view", "knowledge.edit",
        "system.view", "system.edit",
        # 线索 + 转人工
        "lead.view", "lead.edit", "lead.assign",
        "handoff.view", "handoff.claim", "handoff.reply",
    ],
    "engineer": [
        "ticket.view", "ticket.accept", "ticket.reject", "ticket.resolve",
        "customer.view", "sla.view",
        "knowledge.view",
    ],
    "agent": [
        "ticket.view", "ticket.create",
        "customer.view", "customer.create",
        "refund.view", "refund.create",
        "sla.view",
        "knowledge.view",
        # 客服是转人工的坐席，也参与线索跟进
        "handoff.view", "handoff.claim", "handoff.reply",
        "lead.view", "lead.edit",
    ],
}

ROLE_LABELS = {
    "admin": "管理员",
    "supervisor": "主管",
    "engineer": "工程师",
    "agent": "客服",
}

# 全部可选权限（前端编辑用）
ALL_PERMISSIONS = [
    {"code": "ticket.view",      "label": "查看工单"},
    {"code": "ticket.create",    "label": "创建工单"},
    {"code": "ticket.accept",    "label": "接单"},
    {"code": "ticket.reject",    "label": "拒单"},
    {"code": "ticket.resolve",   "label": "标记完成"},
    {"code": "ticket.reassign",  "label": "改派工单"},
    {"code": "refund.view",      "label": "查看退款"},
    {"code": "refund.create",    "label": "创建退款"},
    {"code": "refund.approve",   "label": "审核退款"},
    {"code": "refund.execute",   "label": "执行退款"},
    {"code": "customer.view",    "label": "查看客户"},
    {"code": "customer.create",  "label": "创建客户"},
    {"code": "audit.view",       "label": "查看审计"},
    {"code": "user.view",        "label": "查看人员"},
    {"code": "user.edit",        "label": "编辑人员"},
    {"code": "sla.view",         "label": "查看 SLA"},
    {"code": "sla.edit",         "label": "编辑 SLA 规则"},
    {"code": "knowledge.view",   "label": "查看知识库"},
    {"code": "knowledge.edit",   "label": "维护知识库"},
    {"code": "system.view",      "label": "查看系统配置"},
    {"code": "system.edit",      "label": "修改系统配置"},
    {"code": "lead.view",        "label": "查看线索"},
    {"code": "lead.edit",        "label": "编辑线索/跟进"},
    {"code": "lead.assign",      "label": "分配线索负责人"},
    {"code": "handoff.view",     "label": "查看转人工队列"},
    {"code": "handoff.claim",    "label": "认领/结束接管"},
    {"code": "handoff.reply",    "label": "以人工身份回复"},
]


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), unique=True, index=True)   # U0001
    name = Column(String(64), unique=True, nullable=False, index=True)
    role = Column(String(32), default="engineer", index=True)
    job = Column(String(32), default="")            # 维修 / 客服 / 主管
    skills = Column(Text, default="[]")             # JSON: ["E102", "E205"]
    region = Column(String(64), default="")
    status = Column(String(16), default="online")   # online / offline / busy
    current_load = Column(Integer, default=0)
    max_load = Column(Integer, default=10)
    phone = Column(String(32), default="")
    email = Column(String(128), default="")
    feishu_open_id = Column(String(128), default="")
    feishu_chat_id = Column(String(128), default="")
    dept = Column(String(64), default="")
    permissions = Column(Text, default="{}")        # {"allow":[], "deny":[]}
    password_hash = Column(String(256), default="") # 管理后台登录
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def skill_list(self) -> list:
        try:
            return json.loads(self.skills or "[]")
        except Exception:
            return []

    @property
    def permission_overrides(self) -> dict:
        try:
            return json.loads(self.permissions or "{}")
        except Exception:
            return {}

    def effective_permissions(self) -> set:
        """角色默认权限 + 个人覆盖。"""
        base = set(ROLE_PERMISSIONS.get(self.role, []))
        ov = self.permission_overrides
        base |= set(ov.get("allow", []) or [])
        base -= set(ov.get("deny", []) or [])
        return base

    def has_permission(self, perm: str) -> bool:
        perms = self.effective_permissions()
        if "*" in perms or perm in perms:
            return True
        prefix = perm.split(".")[0] + ".*"
        return prefix in perms

    def to_dict(self, include_effective: bool = False) -> dict:
        def _iso(dt):
            return dt.isoformat() if dt else None
        d = {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "job": self.job,
            "skills": self.skill_list,
            "region": self.region,
            "status": self.status,
            "current_load": self.current_load,
            "max_load": self.max_load,
            "phone": self.phone,
            "email": self.email,
            "feishu_open_id": self.feishu_open_id,
            "feishu_chat_id": self.feishu_chat_id or "",
            "dept": self.dept,
            "permissions": self.permission_overrides,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }
        if include_effective:
            d["effective_permissions"] = sorted(self.effective_permissions())
        return d
