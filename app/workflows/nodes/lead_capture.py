"""销售线索捕获节点。

命中 `lead` 意图（合作 / 采购 / 报价 / 批量 等）时：
    1. 从用户原话里抽取手机号、公司名
    2. 建一条线索，并按「进行中线索最少」自动分配给销售
    3. 回一段确认话术，缺联系方式时主动索要

幂等：同一会话已有未结束线索时不重复创建，只做确认回复。
可用 LEAD_AUTO_CAPTURE=false 关闭自动捕获（关闭后只回复引导话术）。
"""
from __future__ import annotations

import re

from app.config.settings import get_settings
from app.workflows.state import AgentState


PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
# 公司名：以「公司/集团/工厂/企业/商行/中心/机构」结尾的 2~20 字名称
COMPANY_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,20}?(?:公司|集团|工厂|企业|商行|中心|机构|门店|连锁))"
)

# 明确表示在谈合作的信号词，用于挑出最像"需求描述"的一句话
NEED_HINT = ("合作", "采购", "批发", "代理", "加盟", "经销", "报价", "询价", "商务", "供应")


def _text_of(state: AgentState) -> str:
    """取当前用户消息：优先 user_input，回退到最后一条 user 消息。"""
    t = (state.get("user_input") or "").strip()
    if t:
        return t
    for m in reversed(state.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _reply_for(lead_id: str, owner_name: str, has_phone: bool) -> str:
    lines = [
        "收到，已为您登记合作意向 ✅",
        "",
        f"线索编号：**{lead_id}**",
    ]
    if owner_name:
        lines.append(f"对接人：**{owner_name}**")
    lines.append("")
    if has_phone:
        lines.append("我们已记录您的联系方式，会尽快与您联系。")
    else:
        lines.append("请补充**联系电话**（或直接回复手机号），我们会安排专人对接。")
    lines.append("")
    lines.append("您也可以补充：采购型号与数量、期望交期、是否需要定制，方便我们准备方案。")
    return "\n".join(lines)


def lead_capture_node(state: AgentState) -> AgentState:
    text = _text_of(state)
    tid = state.get("thread_id", "") or ""
    actor = "AI 自动捕获"

    # 关闭开关时只做引导，不建线索
    if not getattr(get_settings(), "lead_auto_capture", True):
        answer = (
            "收到您的合作意向。请留下**联系电话**，我们会有专人尽快与您联系。\n\n"
            "（当前已关闭自动登记，请在后台「线索管理」手动新建。）"
        )
        messages = (state.get("messages") or []) + [{"role": "assistant", "content": answer}]
        return {**state, "answer": answer, "messages": messages, "flow_status": "succeeded"}

    phone_m = PHONE_RE.search(text or "")
    phone = phone_m.group(1) if phone_m else ""

    company_m = COMPANY_RE.search(text or "")
    company = company_m.group(1) if company_m else ""

    # 需求描述：优先挑含合作信号词的短句，否则用整条消息
    need = (text or "").strip()
    for part in re.split(r"[。；;\n]", need):
        if any(k in part for k in NEED_HINT):
            need = part.strip()
            break
    need = need[:500]

    # 幂等：本会话已有未结束线索 → 不重复建
    from app.services import lead_service

    try:
        existing = lead_service.find_open_lead_by_thread(tid)
    except Exception as e:
        print(f"[LEAD] 查重失败（继续建线索）: {e}")
        existing = None

    if existing:
        answer = _reply_for(existing["lead_id"], existing.get("owner_name") or "",
                            bool(existing.get("phone")))
        messages = (state.get("messages") or []) + [{"role": "assistant", "content": answer}]
        return {**state, "answer": answer, "messages": messages, "flow_status": "succeeded"}

    customer_id = ""
    try:
        customer_id = lead_service.find_customer_by_phone(phone) or ""
    except Exception:
        customer_id = ""

    try:
        res = lead_service.create_lead(
            name=company or (phone if phone else "待补充联系人"),
            phone=phone,
            company=company,
            source="feishu",
            need_desc=need,
            customer_id=customer_id or None,
            source_thread_id=tid,
            source_msg=(text or "")[:2000],
            actor=actor,
        )
        answer = _reply_for(res["lead_id"], res.get("owner_name") or "", bool(phone))
        print(f"[LEAD] 自动捕获 {res['lead_id']} owner={res.get('owner_name') or '-'} "
              f"auto={res.get('auto_assigned')}")
    except Exception as e:
        print(f"[LEAD] 自动建线索失败（已降级为引导话术）: {e}")
        answer = (
            "收到您的合作意向。请留下**联系电话**，我们会安排专人尽快与您联系。\n\n"
            "（线索自动登记暂时失败，已通知人工跟进。）"
        )

    messages = (state.get("messages") or []) + [{"role": "assistant", "content": answer}]
    return {
        **state,
        "answer": answer,
        "messages": messages,
        "flow_status": "succeeded",
    }
