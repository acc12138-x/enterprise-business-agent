"""客户线索服务：创建 / 跟进 / 阶段流转 / 自动分配 / 漏斗统计。

核心设计
  · 阶段流转走状态机（STAGE_FLOW），非法流转会被拒绝
  · **每次阶段变化、报价变化、成交都会自动写一条跟进记录** —— 进度自动留痕
  · 销售池 = 拥有 `lead.edit` 权限的在线用户；自动分配按「进行中线索数升序」
    （复用工程师派单的负载思路）
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, or_, select

from app.audit.logger import log as audit_log
from app.db.models.customer import Customer
from app.db.models.lead import (
    FINAL_STAGES, OPEN_STAGES, SOURCES, STAGES, STAGE_FLOW, STAGE_LABELS,
    Lead, LeadFollowup,
)
from app.db.models.user import User
from app.db.session import session_scope


# ============================================================
# ID 生成
# ============================================================
def next_lead_id() -> str:
    """LD-YYYYMMDD-0001（当天序号递增）。"""
    prefix = "LD-" + datetime.now().strftime("%Y%m%d") + "-"
    with session_scope() as s:
        rows = s.execute(
            select(Lead.lead_id).where(Lead.lead_id.like(prefix + "%"))
        ).scalars().all()
    mx = 0
    for r in rows:
        try:
            mx = max(mx, int(str(r).rsplit("-", 1)[1]))
        except Exception:
            continue
    return f"{prefix}{mx + 1:04d}"


# ============================================================
# 销售池与自动分配
# ============================================================
def sales_pool() -> List[Dict]:
    """销售池：拥有 `lead.edit` 权限的在线用户。

    不新建表、不复用 engineers —— 销售和工程师是两类人，
    用权限来界定池子最省事，也天然支持「临时让某人也能接线索」。
    """
    with session_scope() as s:
        rows = s.execute(select(User).where(User.status == "online")).scalars().all()
        return [
            {"id": u.id, "name": u.name, "role": u.role, "job": u.job or ""}
            for u in rows
            if u.has_permission("lead.edit")
        ]


def _open_counts() -> Dict[int, int]:
    """每个负责人当前进行中的线索数。"""
    with session_scope() as s:
        rows = s.execute(
            select(Lead.owner_id, func.count(Lead.lead_id))
            .where(Lead.stage.in_(OPEN_STAGES), Lead.owner_id.isnot(None))
            .group_by(Lead.owner_id)
        ).all()
    return {int(oid): int(n) for oid, n in rows if oid is not None}


def pick_owner() -> Optional[Dict]:
    """选一个负责人。

    排序优先级：
      1. 岗位是「销售」的人优先 —— 否则新装系统里管理员（id 最小）会把线索全接走
      2. 进行中线索数升序（负载均衡）
      3. id 升序（保证结果稳定可复现）

    若池子里没有任何销售，则退化为「全体有 lead.edit 权限的人按负载排」。
    """
    pool = sales_pool()
    if not pool:
        return None
    counts = _open_counts()
    pool.sort(key=lambda u: (
        0 if "销售" in (u.get("job") or "") else 1,
        counts.get(u["id"], 0),
        u["id"],
    ))
    return pool[0]


# ============================================================
# 跟进记录
# ============================================================
def _add_followup(s, lead_id: str, content: str = "", user_id: Optional[int] = None,
                  user_name: str = "", channel: str = "other",
                  stage_from: str = "", stage_to: str = "",
                  quote_change: str = "") -> None:
    """在给定 session 内写一条跟进记录（调用方负责 commit）。"""
    s.add(LeadFollowup(
        lead_id=lead_id,
        user_id=user_id,
        user_name=user_name or "",
        channel=channel or "other",
        content=content or "",
        stage_from=stage_from or "",
        stage_to=stage_to or "",
        quote_change=quote_change or "",
    ))


def add_followup(lead_id: str, content: str, user_id: Optional[int] = None,
                 user_name: str = "", channel: str = "other") -> Optional[dict]:
    """手动追加一条跟进记录。"""
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if not lead:
            return None
        _add_followup(s, lead_id, content=content, user_id=user_id,
                      user_name=user_name, channel=channel)
        lead.updated_at = datetime.now()
    audit_log("lead.followup", actor=user_name or "system",
              target_type="lead", target_id=lead_id, detail={"content": content[:200]})
    return {"ok": True, "lead_id": lead_id}


# ============================================================
# 创建
# ============================================================
def create_lead(name: str, phone: str = "", company: str = "", source: str = "other",
                need_desc: str = "", owner_id: Optional[int] = None,
                priority: str = "normal", customer_id: Optional[str] = None,
                source_thread_id: str = "", source_msg: str = "",
                actor: str = "system") -> Dict:
    """新建线索。owner_id 为空时自动分配。"""
    lead_id = next_lead_id()
    owner_name = ""
    auto_assigned = False

    if owner_id is None:
        picked = pick_owner()
        if picked:
            owner_id = picked["id"]
            owner_name = picked["name"]
            auto_assigned = True
    else:
        with session_scope() as s:
            u = s.get(User, owner_id)
            owner_name = u.name if u else ""

    src = source if source in SOURCES else "other"
    stg = "new"

    with session_scope() as s:
        s.add(Lead(
            lead_id=lead_id,
            customer_id=customer_id,
            name=(name or "").strip() or "未留名",
            phone=(phone or "").strip(),
            company=(company or "").strip(),
            source=src,
            need_desc=(need_desc or "").strip(),
            owner_id=owner_id,
            owner_name=owner_name,
            stage=stg,
            priority=priority if priority in ("high", "normal", "low") else "normal",
            source_thread_id=source_thread_id or "",
            source_msg=(source_msg or "")[:2000],
        ))
        note = "线索创建"
        if auto_assigned:
            note += f"，自动分配给 {owner_name}"
        _add_followup(
            s, lead_id, content=note, user_name=actor, channel="other",
            stage_to=stg,
        )

    audit_log("lead.create", actor=actor, target_type="lead", target_id=lead_id,
              detail={"name": name, "source": src, "owner": owner_name,
                      "auto_assigned": auto_assigned})

    return {
        "ok": True,
        "lead_id": lead_id,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "auto_assigned": auto_assigned,
    }


# ============================================================
# 更新（含阶段流转与自动留痕）
# ============================================================
_EDITABLE = {
    "name", "phone", "company", "source", "need_desc", "priority",
    "customer_id", "next_action",
}


def update_lead(lead_id: str, patch: Dict, actor: str = "system",
                user_id: Optional[int] = None) -> Dict:
    """更新线索。会自动处理阶段/报价/成交的联动与留痕。

    返回 {"ok": bool, "error": str, "lead": dict}
    """
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if not lead:
            return {"ok": False, "error": "线索不存在"}
        if (lead.stage or "new") in ("won",):
            # 已成交允许改金额等，但不允许回退阶段（除非显式 lost）
            pass

        old_stage = lead.stage or "new"
        notes: List[str] = []
        stage_from = stage_to = ""
        quote_change = ""
        owner_changed = ""

        # ---------- 阶段流转 ----------
        new_stage = patch.get("stage")
        if new_stage and new_stage != old_stage:
            if new_stage not in STAGES:
                return {"ok": False, "error": f"未知阶段：{new_stage}"}
            if new_stage not in STAGE_FLOW.get(old_stage, []):
                allowed = "、".join(STAGE_LABELS.get(x, x) for x in STAGE_FLOW.get(old_stage, []))
                return {
                    "ok": False,
                    "error": f"不能从「{STAGE_LABELS.get(old_stage, old_stage)}」"
                             f"流转到「{STAGE_LABELS.get(new_stage, new_stage)}」"
                             + (f"，可选：{allowed}" if allowed else "（终态不可流转）"),
                }
            lead.stage = new_stage
            stage_from, stage_to = old_stage, new_stage

            if new_stage == "won":
                lead.won = True
                lead.won_at = datetime.now()
                lead.lost_at = None
                lead.lost_reason = ""
            elif new_stage == "lost":
                lead.won = False
                lead.lost_at = datetime.now()
                lead.lost_reason = (patch.get("lost_reason") or lead.lost_reason or "").strip()
                if not lead.lost_reason:
                    return {"ok": False, "error": "标记流失时必须填写流失原因"}
            else:
                # 回到进行中：清掉终态标记
                if old_stage in FINAL_STAGES:
                    lead.won = False
                    lead.won_at = None
                    lead.lost_at = None
            notes.append(f"阶段：{STAGE_LABELS.get(old_stage, old_stage)} → "
                         f"{STAGE_LABELS.get(new_stage, new_stage)}")

        # ---------- 报价 ----------
        if "quote_amount" in patch:
            try:
                amt = int(patch.get("quote_amount") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "报价金额必须是整数"}
            if amt < 0:
                return {"ok": False, "error": "报价金额不能为负"}
            if amt != (lead.quote_amount or 0):
                old_amt = lead.quote_amount or 0
                quote_change = f"{old_amt} → {amt}"
                notes.append(f"报价：{old_amt} → {amt} 元")
                lead.quote_amount = amt
            lead.quoted = bool(amt > 0)
            if lead.quoted and not lead.quote_at:
                lead.quote_at = datetime.now()

        if patch.get("quoted") is False:
            lead.quoted = False
            lead.quote_amount = 0
            lead.quote_at = None
            notes.append("取消报价")
            quote_change = "取消报价"

        # ---------- 成交金额 ----------
        if "deal_amount" in patch:
            try:
                damt = int(patch.get("deal_amount") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "成交金额必须是整数"}
            if damt < 0:
                return {"ok": False, "error": "成交金额不能为负"}
            if damt != (lead.deal_amount or 0):
                notes.append(f"成交金额 → {damt} 元")
                lead.deal_amount = damt
            if damt > 0 and not lead.won:
                lead.won = True
                if not lead.won_at:
                    lead.won_at = datetime.now()
                if old_stage not in FINAL_STAGES:
                    lead.stage = "won"
                    stage_from, stage_to = old_stage, "won"
                    notes.append("金额大于 0，自动标记为已成交")

        # ---------- 流失原因 ----------
        if patch.get("lost_reason"):
            lead.lost_reason = str(patch["lost_reason"]).strip()

        # ---------- 负责人变更 ----------
        if "owner_id" in patch:
            new_owner = patch.get("owner_id")
            if new_owner:
                u = s.get(User, int(new_owner))
                if not u:
                    return {"ok": False, "error": "负责人不存在"}
                if int(new_owner) != (lead.owner_id or 0):
                    owner_changed = f"{lead.owner_name or '未分配'} → {u.name}"
                    lead.owner_id = u.id
                    lead.owner_name = u.name
                    notes.append(f"负责人：{owner_changed}")
            else:
                lead.owner_id = None
                lead.owner_name = ""
                notes.append("取消负责人")

        # ---------- 普通字段 ----------
        for k in _EDITABLE:
            if k in patch and patch[k] is not None:
                setattr(lead, k, patch[k])

        if patch.get("next_follow_at"):
            try:
                v = patch["next_follow_at"]
                lead.next_follow_at = (
                    datetime.fromisoformat(v.replace("Z", "")) if isinstance(v, str) else v
                )
            except Exception:
                pass

        lead.updated_at = datetime.now()

        # ---------- 自动留痕 ----------
        if notes:
            _add_followup(
                s, lead_id,
                content="；".join(notes),
                user_id=user_id, user_name=actor, channel="other",
                stage_from=stage_from, stage_to=stage_to, quote_change=quote_change,
            )

        out = lead.to_dict()

    audit_log("lead.update", actor=actor, target_type="lead", target_id=lead_id,
              detail={"patch": {k: str(v)[:80] for k, v in patch.items()}})
    return {"ok": True, "lead": out}


# ============================================================
# 查询
# ============================================================
def list_leads(stage: Optional[str] = None, owner_id: Optional[int] = None,
               keyword: Optional[str] = None, only_open: bool = False,
               limit: int = 500) -> List[Dict]:
    with session_scope() as s:
        q = select(Lead).order_by(Lead.created_at.desc()).limit(max(1, min(limit, 2000)))
        if stage:
            q = q.where(Lead.stage == stage)
        if only_open:
            q = q.where(Lead.stage.in_(OPEN_STAGES))
        if owner_id:
            q = q.where(Lead.owner_id == owner_id)
        if keyword:
            kw = f"%{keyword.strip()}%"
            q = q.where(or_(
                Lead.lead_id.like(kw), Lead.name.like(kw), Lead.phone.like(kw),
                Lead.company.like(kw), Lead.need_desc.like(kw),
            ))
        rows = s.execute(q).scalars().all()
        return [r.to_dict() for r in rows]


def get_lead(lead_id: str) -> Optional[Dict]:
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if not lead:
            return None
        d = lead.to_dict()
        rows = s.execute(
            select(LeadFollowup)
            .where(LeadFollowup.lead_id == lead_id)
            .order_by(LeadFollowup.created_at.desc())
        ).scalars().all()
        d["followups"] = [r.to_dict() for r in rows]
    return d


def stats() -> Dict:
    """漏斗统计：各阶段数量、成交率、金额、按负责人排行。"""
    with session_scope() as s:
        by_stage = {st: 0 for st in STAGES}
        for st, n in s.execute(
            select(Lead.stage, func.count(Lead.lead_id)).group_by(Lead.stage)
        ).all():
            by_stage[st or "new"] = int(n)

        total = sum(by_stage.values())
        won = by_stage.get("won", 0)
        lost = by_stage.get("lost", 0)
        closed = won + lost

        deal_sum = int(s.execute(
            select(func.coalesce(func.sum(Lead.deal_amount), 0))
        ).scalar() or 0)
        quote_sum = int(s.execute(
            select(func.coalesce(func.sum(Lead.quote_amount), 0))
        ).scalar() or 0)
        quoted_cnt = int(s.execute(
            select(func.count(Lead.lead_id)).where(Lead.quoted.is_(True))
        ).scalar() or 0)

        # 按负责人（进行中 + 成交）
        owner_rows = s.execute(
            select(Lead.owner_id, Lead.owner_name, Lead.stage, func.count(Lead.lead_id))
            .group_by(Lead.owner_id, Lead.owner_name, Lead.stage)
        ).all()

    owners: Dict[int, Dict] = {}
    for oid, oname, stg, n in owner_rows:
        key = int(oid) if oid is not None else 0
        item = owners.setdefault(key, {
            "owner_id": oid, "owner_name": oname or "未分配",
            "open": 0, "won": 0, "lost": 0, "total": 0, "deal_amount": 0,
        })
        item["total"] += int(n)
        if stg == "won":
            item["won"] += int(n)
        elif stg == "lost":
            item["lost"] += int(n)
        else:
            item["open"] += int(n)

    # 成交金额单独算（按负责人）
    with session_scope() as s:
        for oid, amt in s.execute(
            select(Lead.owner_id, func.coalesce(func.sum(Lead.deal_amount), 0))
            .group_by(Lead.owner_id)
        ).all():
            key = int(oid) if oid is not None else 0
            if key in owners:
                owners[key]["deal_amount"] = int(amt or 0)

    owner_list = sorted(owners.values(), key=lambda x: (-x["won"], -x["total"]))

    return {
        "total": total,
        "by_stage": by_stage,
        "stage_labels": STAGE_LABELS,
        "open": sum(by_stage.get(x, 0) for x in OPEN_STAGES),
        "won": won,
        "lost": lost,
        "conversion_rate": round(won / closed * 100, 1) if closed else 0.0,
        "quoted_count": quoted_cnt,
        "quote_sum": quote_sum,
        "deal_sum": deal_sum,
        "avg_deal": round(deal_sum / won, 1) if won else 0.0,
        "owners": owner_list,
    }


def find_customer_by_phone(phone: str) -> Optional[str]:
    """按手机号找客户（AI 自动建线索时尝试关联已有客户）。"""
    p = (phone or "").strip()
    if not p:
        return None
    with session_scope() as s:
        c = s.execute(select(Customer).where(Customer.phone == p)).scalar_one_or_none()
        return c.customer_id if c else None


def find_open_lead_by_thread(thread_id: str) -> Optional[Dict]:
    """该会话是否已经有一条未结束的线索。

    用于自动捕获的幂等保护：同一个会话反复说「合作」不会建出一堆重复线索。
    """
    if not thread_id:
        return None
    with session_scope() as s:
        lead = s.execute(
            select(Lead)
            .where(Lead.source_thread_id == thread_id, Lead.stage.in_(OPEN_STAGES))
            .order_by(Lead.created_at.desc())
        ).scalars().first()
        return lead.to_dict() if lead else None
