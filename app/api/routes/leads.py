"""客户线索 API：线索 / 跟进记录 / 漏斗统计。

权限划分
    lead.view   查看线索、详情、统计、销售池
    lead.edit   新建、编辑、写跟进
    lead.assign 变更负责人（没有该权限时只能改其他字段）

⚠️ 路由声明顺序：/leads/stats 与 /leads/pool 必须在 /leads/{lead_id} 之前，
   否则 "stats" / "pool" 会被当成 lead_id 匹配掉。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_permission
from app.api.schemas.models import (
    LeadCreateRequest, LeadFollowupRequest, LeadUpdateRequest,
)
from app.services import lead_service

router = APIRouter(prefix="/leads", tags=["leads"])

_view = require_permission("lead.view")
_edit = require_permission("lead.edit")
_assign = require_permission("lead.assign")


def _can_assign(user: dict) -> bool:
    from app.services.permission import has_perm
    return has_perm(user.get("effective_permissions") or [], "lead.assign")


# ============================================================
# 统计与销售池（必须放在 /{lead_id} 之前）
# ============================================================
@router.get("/stats")
async def lead_stats(user: dict = Depends(_view)):
    """漏斗统计：各阶段数量、成交率、金额、按负责人排行。"""
    return lead_service.stats()


@router.get("/pool")
async def lead_pool(user: dict = Depends(_view)):
    """销售池：拥有 lead.edit 权限的在线人员，供分配下拉使用。"""
    return {"pool": lead_service.sales_pool()}


# ============================================================
# 列表 / 新建
# ============================================================
@router.get("")
async def list_leads(
    stage: Optional[str] = None,
    owner_id: Optional[int] = None,
    keyword: Optional[str] = None,
    only_open: bool = False,
    limit: int = 500,
    user: dict = Depends(_view),
):
    return lead_service.list_leads(
        stage=stage, owner_id=owner_id, keyword=keyword,
        only_open=only_open, limit=limit,
    )


@router.post("")
async def create_lead(req: LeadCreateRequest, user: dict = Depends(_edit)):
    if req.owner_id is not None and not _can_assign(user):
        raise HTTPException(status_code=403, detail="需要权限：lead.assign（指定负责人）")
    res = lead_service.create_lead(
        name=req.name, phone=req.phone, company=req.company,
        source=req.source, need_desc=req.need_desc, owner_id=req.owner_id,
        priority=req.priority, customer_id=req.customer_id,
        actor=user.get("name") or "system",
    )
    return res


# ============================================================
# 详情 / 更新 / 跟进
# ============================================================
@router.get("/{lead_id}")
async def get_lead(lead_id: str, user: dict = Depends(_view)):
    d = lead_service.get_lead(lead_id)
    if not d:
        raise HTTPException(status_code=404, detail="线索不存在")
    return d


@router.patch("/{lead_id}")
async def update_lead(lead_id: str, req: LeadUpdateRequest, user: dict = Depends(_edit)):
    patch = req.model_dump(exclude_unset=True)

    # 只有 lead.assign 才能改负责人
    if "owner_id" in patch and not _can_assign(user):
        raise HTTPException(status_code=403, detail="需要权限：lead.assign（变更负责人）")

    res = lead_service.update_lead(
        lead_id, patch, actor=user.get("name") or "system", user_id=user.get("id"),
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "更新失败")
    return res


@router.post("/{lead_id}/followups")
async def add_followup(lead_id: str, req: LeadFollowupRequest,
                       user: dict = Depends(_edit)):
    res = lead_service.add_followup(
        lead_id, req.content, user_id=user.get("id"),
        user_name=user.get("name") or "", channel=req.channel,
    )
    if not res:
        raise HTTPException(status_code=404, detail="线索不存在")
    return res
