"""转人工客服台 API：队列 / 认领 / 接管回复 / 结束接管。

权限划分
    handoff.view   查看队列、详情、统计
    handoff.claim  认领、结束接管
    handoff.reply  以人工身份回复用户

⚠️ /handoffs/stats 必须声明在 /handoffs/{handoff_id} 之前。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_permission
from app.api.schemas.models import (
    HandoffCloseRequest, HandoffCreateRequest, HandoffReplyRequest,
)
from app.services import handoff_service

router = APIRouter(prefix="/handoffs", tags=["handoffs"])

_view = require_permission("handoff.view")
_claim = require_permission("handoff.claim")
_reply = require_permission("handoff.reply")


@router.get("/stats")
async def handoff_stats(user: dict = Depends(_view)):
    """转人工统计：各状态数量、平均响应时长、按坐席。"""
    return handoff_service.stats()


@router.get("")
async def list_handoffs(
    status: Optional[str] = None,
    limit: int = 200,
    user: dict = Depends(_view),
):
    return handoff_service.list_handoffs(status=status, limit=limit)


@router.post("")
async def create_handoff(req: HandoffCreateRequest, user: dict = Depends(_view)):
    """手动建转人工工单（后台兜底用；正常应由 AI 自动创建）。"""
    return handoff_service.create_handoff(
        thread_id=req.thread_id,
        sender_open_id=req.sender_open_id,
        trigger="human_intent",
        reason=req.reason or f"{user.get('name') or '客服'} 手动转人工",
        last_user_msg="",
    )


@router.get("/{handoff_id}")
async def get_handoff(handoff_id: str, user: dict = Depends(_view)):
    d = handoff_service.get_handoff(handoff_id)
    if not d:
        raise HTTPException(status_code=404, detail="工单不存在")
    return d


@router.post("/{handoff_id}/claim")
async def claim(handoff_id: str, user: dict = Depends(_claim)):
    res = handoff_service.claim(handoff_id, user)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "认领失败")
    return res


@router.post("/{handoff_id}/reply")
async def reply(handoff_id: str, req: HandoffReplyRequest, user: dict = Depends(_reply)):
    res = handoff_service.reply(handoff_id, req.text, user)
    if not res.get("ok"):
        # 发送失败也返回 200 + sent=false，避免前端把「消息已记录但发送失败」当异常
        if res.get("error") in ("工单不存在", "请先认领该会话再回复") or \
                str(res.get("error", "")).startswith("该会话由"):
            raise HTTPException(status_code=400, detail=res["error"])
    return res


@router.post("/{handoff_id}/close")
async def close(handoff_id: str, req: HandoffCloseRequest, user: dict = Depends(_claim)):
    res = handoff_service.close(handoff_id, user, note=req.note)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "结束失败")
    return res
