"""OpenAI 兼容层：OpenClaw 把 LangGraph 当自定义 LLM Provider 用。"""
from __future__ import annotations
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langgraph.types import Command

from app.api.deps import get_gateway_caller
from app.workflows.graph import graph
from app.services.permission import check_feishu_command

router = APIRouter(prefix="/v1", tags=["openai-compat"])

_session_map: Dict[str, str] = {}


def _strip_openclaw_wrapper(text: str) -> str:
    """去掉 OpenClaw 加的元数据。"""
    if not text:
        return text
    positions = [m.start() for m in re.finditer(r"```", text)]
    if len(positions) >= 4:
        real = text[positions[3] + 3:].strip()
    else:
        real = text
    real = re.split(r"\n+\[System:", real)[0].strip()
    return real


def _extract_chat_id(text: str) -> Optional[str]:
    """从元数据提取 chat_id。"""
    if not text:
        return None
    m = re.search(r'"chat_id":\s*"chat:([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'"chat_id":\s*"(oc_[^"]+)"', text)
    if m:
        return m.group(1)
    return None




def _extract_sender_id(text: str) -> Optional[str]:
    """从 OpenClaw 元数据提取 sender open_id。"""
    if not text:
        return None
    m = re.search(r'"sender_id":\s*"(ou_[^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'"id":\s*"(ou_[^"]+)"', text)
    if m:
        return m.group(1)
    return None


def _thread_id(session_key: str | None) -> str:
    if not session_key:
        return f"openclaw-{uuid.uuid4().hex[:8]}"
    if session_key not in _session_map:
        _session_map[session_key] = f"openclaw-{session_key[:20]}"
    return _session_map[session_key]


def _extract_raw_user_message(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                return " ".join(parts)
    return ""


class ChatCompletionRequest(BaseModel):
    model: str = "local-rag"
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None
    session_id: Optional[str] = None
    sessionId: Optional[str] = None


def _run_graph(user_msg, session_key, messages=None, sender_id="", extra_slots=None):
    """执行图。

    ★ 关键：不传 OpenClaw 的 messages 历史。
    原因：checkpointer 会累积 state.messages，如果每次再叠加
    OpenClaw 传来的 30+ 条历史，会指数膨胀（曾到 7.88 GB）。
    这里只用 user_input 字段传当前消息，state.messages 交由
    state.py 的 reducer 截断到最近 N 条。
    """
    tid = _thread_id(session_key)
    slots = dict(extra_slots or {})
    init = {
        "thread_id": tid,
        "sender_open_id": sender_id,
        "user_input": user_msg,
        "slots": slots,
    }
    config = {"configurable": {"thread_id": tid}}
    return graph.invoke(init, config=config)


def _build_answer(final):
    if final.get("hitl_pending"):
        reason = final.get("hitl_reason") or "需要人工确认"
        return f"【需要人工确认】{reason}\n\n请回复「同意」或您的具体意见。"
    answer = final.get("answer") or ""
    if not answer:
        intent = final.get("intent", "")
        if intent == "human":
            answer = "已转人工，客服稍后接入。"
        elif final.get("missing_slots"):
            answer = "请补充：" + "、".join(final["missing_slots"])
        else:
            answer = "暂无相关依据，建议转人工。"
    citations = final.get("citations") or []
    if citations and final.get("confidence", 0) > 0.5:
        sources = []
        for c in citations[:3]:
            src = c.get("source") or c.get("chunk_id", "")[:8]
            sources.append(f"[{c['index']}] {src}")
        answer = answer + "\n\n参考来源：\n" + "\n".join(sources)
    return answer


_APPROVE = ["同意", "确认", "批准", "通过", "可以", "好的", "approve", "yes", "ok"]
_REJECT = ["拒绝", "驳回", "不同意", "不行", "取消", "否", "reject", "no"]


def _parse_decision(text):
    t = (text or "").strip().lower()
    if not t:
        return None
    for w in _REJECT:
        if t == w.lower() or t.startswith(w.lower()):
            return "block_revise: 用户拒绝"
    for w in _APPROVE:
        if t == w.lower() or t.startswith(w.lower()):
            return "approve"
    return None


import re as _re_cmd

_CMD_QUERY_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>工单详情|工单|详情|查看工单|查询工单)"
    r"[\s@＠]*"
    r"(?P<ticket>[A-Za-z][A-Za-z0-9]{5,31})"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_QUERY_ACTIONS = {"工单详情", "工单", "详情", "查看工单", "查询工单"}

_CMD_MINE_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>我的工单|我的报修|我的维修|查看我的工单|我的单子)"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_CMD_URGE_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>催单|加急|催一下|催一催)"
    r"[\s@＠]*"
    r"(?P<ticket>[A-Za-z][A-Za-z0-9]{5,31})"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_CMD_APPROVE_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>同意|通过|批准|approve|驳回|拒绝|reject)"
    r"[\s@＠]*"
    r"(?P<refund>RF[A-Za-z0-9]{4,30})"
    r"(?:[\s@＠]+(?P<reason>.+?))?"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_CMD_PENDING_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>待审批|审批列表|待审批列表|退款审批|查看待审批)"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_CMD_RE = _re_cmd.compile(
    r"^[\s@＠]*(?:业务助手[\s@＠]*)?"
    r"(?P<action>开始处理|开始|接单|接受|处理|完成|解决|关闭|拒单|拒绝|改派)"
    r"[\s@＠]*"
    r"(?P<ticket>[A-Za-z][A-Za-z0-9]{5,31})"
    r"(?:[\s@＠]+(?P<extra>.+?))?"
    r"[\s@＠]*(?:业务助手)?[\s@＠]*$"
)

_ACTION_MAP = {
    "接单": "accept", "接受": "accept", "处理": "accept",
    "开始处理": "start", "开始": "start",
    "完成": "resolve", "解决": "resolve",
    "关闭": "close",
    "拒单": "reject", "拒绝": "reject",
    "改派": "reassign",
}


_CN_STATUS = {
    "pending": "待处理", "assigned": "待接单", "accepted": "已接单",
    "in_progress": "处理中", "resolved": "已解决", "closed": "已关闭",
    "rejected": "已拒单", "cancelled": "已取消",
}
_CN_ACTION = {
    "accept": "接单", "start": "开始处理", "resolve": "标记完成",
    "close": "关闭", "reject": "拒单", "reassign": "改派",
}


def _friendly_transition_error(ticket_id: str, action: str, exc) -> str:
    """把 HTTPException 转成用户友好提示。"""
    try:
        from app.db.models.ticket import Ticket
        from app.db.session import session_scope
        with session_scope() as s:
            t = s.get(Ticket, ticket_id)
            if t is None:
                return f"❌ 工单 **{ticket_id}** 不存在"
            cur_cn = _CN_STATUS.get(t.status, t.status)
    except Exception:
        cur_cn = "未知"

    act_cn = _CN_ACTION.get(action, action)
    detail = getattr(exc, "detail", str(exc))
    return (
        f"⚠️ 工单 **{ticket_id}** 当前是 **{cur_cn}** 状态，"
        f"无法执行「{act_cn}」操作。\n"
        f"（{detail}）"
    )


def _handle_feishu_command(text: str, sender_open_id: str) -> Optional[str]:
    """飞书命令：接单/开始/完成/关闭/拒单/改派 T123。

    匹配 → 返回答案字符串；不匹配 → 返回 None（走普通 chat 流程）。
    """
    if not text:
        return None

    # 待审批列表
    mp = _CMD_PENDING_RE.match(text)
    if mp:
        try:
            from app.db.models.refund import RefundRequest
            from app.db.session import session_scope
            from sqlalchemy import select as _select
            with session_scope() as s:
                rows = s.execute(
                    _select(RefundRequest).where(
                        RefundRequest.status.in_(["pending", "ai_review", "pending_approval"])
                    ).order_by(RefundRequest.created_at.desc()).limit(10)
                ).scalars().all()
                items = [r.to_dict() for r in rows]
            if not items:
                return "✅ 当前没有待审批退款。"
            NL = chr(10)
            lines = ["💰 **待审批退款**（" + str(len(items)) + " 笔）：", ""]
            for r in items:
                lines.append(
                    "- `" + r["refund_id"] + "`  ¥" + str(r["amount"])
                    + "  客户 " + (r.get("customer_id") or "-")
                    + "  订单 " + (r.get("order_id") or "-")
                )
            lines.append("")
            lines.append("回复「**同意 RFxxx**」或「**驳回 RFxxx 原因**」来审批。")
            return NL.join(lines)
        except Exception as e:
            return "❌ 查询待审批失败：" + str(e)

    # 同意 / 驳回 退款
    ma = _CMD_APPROVE_RE.match(text)
    if ma:
        action_cn = ma.group("action")
        refund_id = ma.group("refund").upper()
        reason = (ma.group("reason") or "").strip()

        is_approve = action_cn in ("同意", "通过", "批准", "approve")
        decision = "approve" if is_approve else "reject"

        # 权限校验（复用 refund.approve）
        try:
            from app.services.permission import check_feishu_command as _check
            chk = _check(sender_open_id, "approve_refund")
            if not chk.get("allowed"):
                return "❌ 权限不足：" + str(chk.get("reason"))
        except Exception as _e:
            print("[CMD] permission check exception: " + str(_e))

        try:
            from app.db.models.refund import RefundRequest
            from app.db.session import session_scope
            from datetime import datetime as _dt3
            with session_scope() as s:
                r = s.get(RefundRequest, refund_id)
                if r is None:
                    return "❌ 退款单 **" + refund_id + "** 不存在"
                if r.status not in ("pending", "ai_review", "pending_approval"):
                    return "⚠️ 退款单 **" + refund_id + "** 当前是 " + r.status + " 状态，不可审批"
                r.status = "approved" if is_approve else "rejected"
                r.approver = "飞书-" + (sender_open_id[:12] if sender_open_id else "unknown")
                r.approval_note = reason or ("飞书" + ("通过" if is_approve else "驳回"))
                r.approved_at = _dt3.now()
                amt = float(r.amount or 0)
                cust = r.customer_id or "-"
                order = r.order_id or "-"

            NL3 = chr(10)
            if is_approve:
                return (
                    "✅ 已同意退款 **" + refund_id + "**" + NL3
                    + "- 金额：¥" + str(amt) + NL3
                    + "- 客户：" + cust + "  订单：" + order + NL3
                    + "- 请到「退款管理」页面点【执行退款】打款"
                )
            else:
                return (
                    "❌ 已驳回退款 **" + refund_id + "**" + NL3
                    + "- 原因：" + (reason or "（未填）") + NL3
                    + "- 客户：" + cust
                )
        except Exception as e:
            return "❌ 审批失败：" + str(e)

    # 我的工单
    mm = _CMD_MINE_RE.match(text)
    if mm:
        try:
            from app.db.models.user import User
            from app.db.models.ticket import Ticket
            from app.db.session import session_scope
            from sqlalchemy import select as _select
            user = None
            with session_scope() as s:
                if sender_open_id:
                    u = s.execute(_select(User).where(User.feishu_open_id == sender_open_id)).scalars().first()
                    if u:
                        user = u.to_dict()
            if not user:
                return "❌ 未找到您的账号绑定。请在「人员管理」页面配置您的飞书 open_id。"
            with session_scope() as s:
                rows = s.execute(
                    _select(Ticket).where(
                        Ticket.assigned_to == user["name"],
                        Ticket.status.notin_(["closed", "cancelled", "resolved"])
                    ).order_by(Ticket.created_at.desc()).limit(10)
                ).scalars().all()
                tickets = [t.to_dict() for t in rows]
            if not tickets:
                return "👤 " + user["name"] + "，您当前没有进行中的工单。"
            NL = chr(10)
            lines = ["👤 **" + user["name"] + "** 进行中工单（" + str(len(tickets)) + " 个）：", ""]
            for t in tickets[:8]:
                st = _CN_STATUS.get(t["status"], t["status"])
                dev = t.get("device_model") or "待补"
                err = t.get("error_code") or "待补"
                lines.append("- `" + t["ticket_id"] + "` " + st + "  " + dev + "  " + err)
            return NL.join(lines)
        except Exception as e:
            return "❌ 查询我的工单失败：" + str(e)

    # 先试"查询详情"命令
    mq = _CMD_QUERY_RE.match(text)
    if mq:
        ticket_id = mq.group("ticket").upper()
        try:
            from app.db.models.ticket import Ticket
            from app.db.session import session_scope
            with session_scope() as s:
                t = s.get(Ticket, ticket_id)
                if t is None:
                    return f"❌ 工单 **{ticket_id}** 不存在"
                d = t.to_dict()

            _st = _CN_STATUS.get(d.get("status", ""), d.get("status", ""))
            NL = chr(10)
            lines = [
                f"📋 **工单 {d['ticket_id']}**",
                f"- 状态：{_st}",
                f"- 设备：{d.get('device_model') or '待补'}",
                f"- 故障码：{d.get('error_code') or '待补'}",
                f"- 工程师：{d.get('assigned_to') or '未派单'}",
            ]
            if d.get("created_at"):
                lines.append(f"- 创建：{d['created_at'][:19]}")
            if d.get("sla_deadline"):
                lines.append(f"- SLA：{d.get('sla_status', 'normal')} / 截止 {d['sla_deadline'][:19]}")
            if d.get("resolved_note"):
                lines.append(f"- 解决备注：{d['resolved_note']}")
            if d.get("missing_fields"):
                lines.append(f"- ⚠️ 待补：{', '.join(d['missing_fields'])}")
            return NL.join(lines)
        except Exception as e:
            return f"❌ 查询失败：{e}"

    # 催单
    mu = _CMD_URGE_RE.match(text)
    if mu:
        ticket_id = mu.group("ticket").upper()
        try:
            from datetime import datetime as _dt2, timedelta as _td2
            from app.db.models.ticket import Ticket
            from app.db.session import session_scope
            with session_scope() as s:
                t = s.get(Ticket, ticket_id)
                if t is None:
                    return "❌ 工单 **" + ticket_id + "** 不存在"
                now = _dt2.now()
                if t.sla_deadline is None or t.sla_deadline > now + _td2(hours=2):
                    t.sla_deadline = now + _td2(hours=2)
                t.risk_flag = "urgent"
                assigned = t.assigned_to or "未派单"
                dev = t.device_model or "-"
                err = t.error_code or "-"
            try:
                from app.services.feishu_router import dispatch as _dispatch2
                _dispatch2(
                    event="ticket_escalated",
                    title="🚨 工单加急 " + ticket_id,
                    content="设备 " + dev + " 故障码 " + err + "，用户催单，SLA 调整为 2 小时内",
                )
            except Exception as _e:
                print("[URGE] dispatch failed: " + str(_e))
            NL2 = chr(10)
            return (
                "🚨 已催单 **" + ticket_id + "**" + NL2
                + "- SLA 调整为 2 小时内" + NL2
                + "- 工程师：" + assigned + NL2
                + "- 已通知工程师和主管"
            )
        except Exception as e:
            return "❌ 催单失败：" + str(e)

    m = _CMD_RE.match(text)
    if not m:
        return None

    action_cn = m.group("action")
    ticket_id = m.group("ticket").upper()
    extra = (m.group("extra") or "").strip()
    action = _ACTION_MAP.get(action_cn)
    if not action:
        return None

    # 权限校验
    check = check_feishu_command(sender_open_id, action)
    if not check.get("allowed"):
        return f"❌ 权限不足：{check.get('reason')}"

    # 执行业务动作
    try:
        if action == "accept":
            from app.api.routes.tickets import _transition
            from fastapi import HTTPException as _HE
            try:
                r = _transition(ticket_id, "accepted")
            except _HE as e:
                return _friendly_transition_error(ticket_id, "accept", e)
            return f"✅ 已接单 **{ticket_id}**\n- 当前状态：{r.get('status')}\n- 工程师：{r.get('assigned_to')}"

        if action == "start":
            from app.api.routes.tickets import _transition
            from fastapi import HTTPException as _HE
            try:
                r = _transition(ticket_id, "in_progress")
            except _HE as e:
                return _friendly_transition_error(ticket_id, "start", e)
            return f"🔧 已开始处理 **{ticket_id}**\n- 当前状态：{r.get('status')}"

        if action == "resolve":
            from app.api.routes.tickets import _transition
            from fastapi import HTTPException as _HE
            try:
                r = _transition(ticket_id, "resolved", extra={"resolved_note": extra or "飞书操作"})
            except _HE as e:
                return _friendly_transition_error(ticket_id, "resolve", e)
            return f"🎯 已标记解决 **{ticket_id}**\n- 备注：{extra or '（无）'}"

        if action == "close":
            from app.api.routes.tickets import _transition
            from fastapi import HTTPException as _HE
            try:
                r = _transition(ticket_id, "closed")
            except _HE as e:
                return _friendly_transition_error(ticket_id, "close", e)
            return f"🔒 已关闭 **{ticket_id}**"

        if action == "reject":
            from app.api.routes.tickets import _transition, _pick_engineer
            from app.db.models.ticket import Ticket
            from app.db.session import session_scope
            from sqlalchemy import select
            with session_scope() as s:
                t = s.get(Ticket, ticket_id)
                if t is None:
                    return f"❌ 工单 {ticket_id} 不存在"
                old = t.assigned_to
                if t.assigned_engineer_id:
                    from app.db.models.user import User
                    oe = s.get(User, t.assigned_engineer_id)
                    if oe and oe.current_load > 0:
                        oe.current_load -= 1
                new_eng = _pick_engineer(s, t.error_code, exclude_names=[old])
                if new_eng is None:
                    t.status = "rejected"
                    t.reject_reason = extra or "飞书拒单"
                    return f"❌ 已拒单 **{ticket_id}**，无可用工程师重派"
                t.status = "assigned"
                t.assigned_to = new_eng.name
                t.assigned_engineer_id = new_eng.id
                t.reject_reason = extra or "飞书拒单"
                t.assign_count = (t.assign_count or 0) + 1
                new_eng.current_load += 1
                return f"❌ 已拒单 **{ticket_id}**\n- 原因：{extra or '（无）'}\n- 重派给：{new_eng.name}"

        if action == "reassign":
            if not extra:
                return f"⚠️ 请指定新工程师，例如：改派 {ticket_id} 王工"
            from app.db.models.ticket import Ticket
            from app.db.models.user import User
            from app.db.session import session_scope
            from sqlalchemy import select
            with session_scope() as s:
                t = s.get(Ticket, ticket_id)
                if t is None:
                    return f"❌ 工单 {ticket_id} 不存在"
                new_eng = s.execute(
                    select(User).where(User.role == "engineer", User.name == extra)
                ).scalar_one_or_none()
                if new_eng is None:
                    return f"❌ 未找到工程师 {extra}"
                old_name = t.assigned_to
                if t.assigned_engineer_id:
                    oe = s.get(User, t.assigned_engineer_id)
                    if oe and oe.current_load > 0:
                        oe.current_load -= 1
                t.assigned_to = new_eng.name
                t.assigned_engineer_id = new_eng.id
                t.assign_count = (t.assign_count or 0) + 1
                new_eng.current_load += 1
                return f"🔄 已改派 **{ticket_id}**\n- {old_name} → {new_eng.name}"

    except Exception as e:
        return f"❌ 命令执行失败：{e}"

    return None


def _try_resume(tid, user_msg):
    config = {"configurable": {"thread_id": tid}}
    try:
        snapshot = graph.get_state(config)
    except Exception as e:
        print(f"[RESUME] get_state 失败: {e}")
        return None
    is_interrupted = bool(snapshot and snapshot.next)
    print(f"[RESUME] tid={tid} interrupted={is_interrupted}")
    if not is_interrupted:
        return None
    decision = _parse_decision(user_msg)
    print(f"[RESUME] decision={decision}")
    if not decision:
        return {
            "hitl_pending": True,
            "hitl_reason": "等待用户确认",
            "answer": "当前有待确认的操作，请回复「同意」或「拒绝」。",
            "intent": None,
            "citations": [],
            "flow_status": "waiting",
        }
    try:
        result = graph.invoke(Command(resume=decision), config=config)
        print(f"[RESUME] 恢复成功 flow_status={result.get('flow_status')}")
        return result
    except Exception as e:
        print(f"[RESUME] 恢复失败: {e}")
        return {"answer": f"恢复失败：{e}", "flow_status": "error"}


@router.post("/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request,
                           caller: dict = Depends(get_gateway_caller)):
    raw_msg = _extract_raw_user_message(req.messages)
    user_msg = _strip_openclaw_wrapper(raw_msg)
    chat_id = _extract_chat_id(raw_msg)
    sender_id = _extract_sender_id(raw_msg)
    session_key = req.session_id or req.sessionId or req.user or chat_id

    # 把 sender_id 和 chat_id 塞到 slots 供 my_tickets 节点用
    extra_slots = {"_sender_open_id": sender_id or "", "_chat_id": chat_id or ""}

    # 陌生 open_id 自动登记到「待绑定飞书账号」，管理后台可一键绑定
    if sender_id:
        try:
            from app.services.binding_service import record_unbound
            record_unbound(sender_id, chat_id or "", user_msg)
        except Exception as _e:
            print(f"[BIND] auto record failed: {_e}")

    print(f"\n[REQ] cleaned={user_msg!r}")
    print(f"[REQ] chat_id={chat_id} session_key={session_key}")

    # 飞书命令短路（在意图识别之前）
    cmd_answer = _handle_feishu_command(user_msg, sender_id or "")
    if cmd_answer is not None:
        print(f"[CMD] {user_msg!r} -> {cmd_answer[:80]!r}")
        created = int(time.time())
        resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        if not req.stream:
            return {
                "id": resp_id,
                "object": "chat.completion",
                "created": created,
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": cmd_answer},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        async def cmd_stream():
            yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
            for i in range(0, len(cmd_answer), 20):
                piece = cmd_answer[i:i+20]
                yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': req.model, 'choices': [{'index': 0, 'delta': {'content': piece}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(cmd_stream(), media_type="text/event-stream")

    tid = _thread_id(session_key)

    # ---------- 转人工：坐席接管中则 AI 静默，不跑工作流 ----------
    # 放在最前面是有意的：接管期间既不该浪费 LLM 调用，也不该搅乱 checkpoint。
    from app.services import handoff_service as _hs

    muted = _hs.before_turn(tid, user_msg, sender_open_id=sender_id or "")
    if muted:
        final = {
            "answer": _hs.mute_reply_text(muted),
            "intent": "human",
            "flow_status": "waiting",
        }
    else:
        final = _try_resume(tid, user_msg)
        if final is None:
            final = _run_graph(user_msg, session_key, req.messages,
                               sender_id=sender_id or "", extra_slots=extra_slots)

    answer = _build_answer(final)

    # ---------- 会话落库 + 必要时转人工（通知坐席）----------
    if muted:
        _hs.record_message(tid, "assistant", answer, handoff_id=muted["handoff_id"])
    else:
        _hs.after_turn(tid, user_msg, answer, final, sender_open_id=sender_id or "")

    created = int(time.time())
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if not req.stream:
        return {
            "id": resp_id,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    async def stream_gen():
        chunk0 = {
            "id": resp_id, "object": "chat.completion.chunk",
            "created": created, "model": req.model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk0, ensure_ascii=False)}\n\n"
        for i in range(0, len(answer), 20):
            piece = answer[i:i + 20]
            chunk = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": created, "model": req.model,
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        end_chunk = {
            "id": resp_id, "object": "chat.completion.chunk",
            "created": created, "model": req.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_gen(), media_type="text/event-stream")


@router.get("/models")
async def list_models(caller: dict = Depends(get_gateway_caller)):
    return {
        "object": "list",
        "data": [
            {"id": "local-rag", "object": "model", "created": int(time.time()), "owned_by": "local-rag"},
            {"id": "local-rag-search", "object": "model", "created": int(time.time()), "owned_by": "local-rag"},
        ],
    }
