"""客户资产模型：客户 + VIP + 风险等级。"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    email = Column(String(128), default="")
    address = Column(String(512), default="")
    vip_level = Column(String(16), default="normal")  # normal / silver / gold / diamond
    risk_level = Column(String(16), default="normal")  # normal / suspicious / high_risk
    risk_score = Column(Integer, default=0)
    total_orders = Column(Integer, default=0)
    total_refunds = Column(Integer, default=0)
    total_complaints = Column(Integer, default=0)
    total_tickets = Column(Integer, default=0)

    # ★ 客户归属：谁负责这个客户；来源于哪条线索
    owner_id = Column(Integer, index=True, nullable=True)
    owner_name = Column(String(64), default="")
    lead_id = Column(String(32), index=True, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        def _iso(dt): return dt.isoformat() if dt else None
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "vip_level": self.vip_level,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "total_orders": self.total_orders,
            "total_refunds": self.total_refunds,
            "total_complaints": self.total_complaints,
            "total_tickets": self.total_tickets,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name or "",
            "lead_id": self.lead_id,
            "created_at": _iso(self.created_at),
        }
