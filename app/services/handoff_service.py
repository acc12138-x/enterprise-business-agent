"""转人工服务：工单队列 + 会话落库 + AI 静默 + 坐席接管。

链路
    用户说「转人工」
      → create_handoff()   建工单（queued）+ 落库会话 + 通知坐席
      → claim()            坐席认领（claimed）
      → 此后 is_ai_muted() 为真，入站消息【不再启动 AI 工作流】
      → reply()            坐席以「本人」身份回复，直发飞书
      → close()            结束接管，插入「已转回智能助手」提示

另有 patrol_timeouts() 供后台巡检线程调用：超时未认领则升级通知主管并标记 timeout。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select

from app.audit.logger import log as audit_log
from app.config.settings import get_settings
from app.db.models.handoff import (
    ROLE_ASSISTANT, ROLE_HUMAN, ROLE_SYSTEM, ROLE_USER,
    STATUS_LABELS, HumanHandoff, ConversationMessage,
)
from app.db.models.user import User
from app.db.session import session_scope
from app.integrations.feishu_client import send_private


# ============================================================
# ID 生成
# ============================================================
def next_handoff_id() -> str:
    """HT-YYYYMMDD-0001（当天序号递增）。"""
    prefix = "HT-" + datetime.now().strftime("%Y%m%d") + "-"
    with session_scope() as s:
        rows = s.execute(
            select(HumanHandoff.handoff_id).where(HumanHandoff.handoff_id.like(prefix + "%"))
        ).scalars().all()
    mx = 0
    for r in rows:
        try:
            mx = max(mx, int(str(r).rsplit("-", 1)[1]))
        except Exception:
            continue
    return f"{prefix}{mx + 1:04d}"


# ============================================================
# 会话消息落库（一行一条，不逐 token）
# ============================================================
def record_message(thread_id: str, role: str, content: str,
                   sender_open_id: str = "", sender_name: str = "",
                   handoff_id: Optional[str] = None, intent: str = "",
                   meta: Optional[dict] = None) -> None:
    """落一条会话消息。失败不阻塞主流程。"""
    if not thread_id or not (content or "").strip():
        return
    try:
        with session_scope() as s:
            s.add(ConversationMessage(
                thread_id=thread_id,
                role=role,
                content=content,
                sender_open_id=sender_open_id or "",
                sender_name=sender_name or "",
                handoff_id=handoff_id,
                intent=intent or "",
                meta=json.dumps(meta or {}, ensure_ascii=False),
            ))
    except Exception as e:
        print(f"[HANDOFF] 会话落库失败（已忽略）: {e}")


def recent_summary(thread_id: str, limit: int = 6) -> str:
    """取该会话最近几条消息，拼成给坐席看的摘要。"""
    try:
        with session_scope() as s:
            rows = s.execute(
                select(ConversationMessage)
                .where(ConversationMessage.thread_id == thread_id)
                .order_by(ConversationMessage.id.desc())
                .limit(limit)
            ).scalars().all()
        lines = []
        for r in reversed(rows):
            who = {"user": "用户", "assistant": "AI", "human_agent": "人工", "system": "系统"}.get(
                r.role, r.role)
            txt = (r.content or "").replace("\n", " ")[:120]
            lines.append(f"{who}：{txt}")
        return "\n".join(lines)
    except Exception:
        return ""


# ============================================================
# 创建转人工工单
# ============================================================
def create_handoff(thread_id: str, sender_open_id: str = "",
                   trigger: str = "human_intent", reason: str = "",
                   last_user_msg: str = "", customer_id: Optional[str] = None,
                   lead_id: Optional[str] = None) -> Dict:
    """建转人工工单并通知坐席。

    幂等保护：同一 thread 若已有 queued/claimed 的工单，直接复用，不重复建。
    """
    existing = active_handoff(thread_id)
    if existing:
        return {"ok": True, "handoff_id": existing["handoff_id"], "reused": True}

    handoff_id = next_handoff_id()
    summary = recent_summary(thread_id)

    with session_scope() as s:
        s.add(HumanHandoff(
            handoff_id=handoff_id,
            thread_id=thread_id,
            sender_open_id=sender_open_id or "",
            customer_id=customer_id,
            lead_id=lead_id,
            trigger=trigger,
            reason=reason or "",
            summary=summary,
            last_user_msg=(last_user_msg or "")[:2000],
            status="queued",
        ))

    # 通知坐席（失败不影响工单创建）
    notify_result = {}
    try:
        from app.services.feishu_router import dispatch
        notify_result = dispatch(
            event="handoff_created",
            title=f"【转人工】{handoff_id}",
            content=(
                f"原因：{reason or '用户请求转人工'}\n"
                f"用户：{sender_open_id or '未知'}\n"
                f"最后消息：{(last_user_msg or '')[:200]}\n\n"
                f"请到「人工客服台」认领。"
            ),
            extra_roles=["agent", "supervisor"],
        )
    except Exception as e:
        print(f"[HANDOFF] 通知坐席失败（已忽略）: {e}")
        notify_result = {"error": str(e)}

    try:
        with session_scope() as s:
            h = s.get(HumanHandoff, handoff_id)
            if h:
                h.notify_result = json.dumps(notify_result, ensure_ascii=False)[:4000]
    except Exception:
        pass

    record_message(thread_id, ROLE_SYSTEM,
                   f"【已转人工】{reason or '用户请求转人工'}，工单号 {handoff_id}，等待客服接入。",
                   handoff_id=handoff_id)

    audit_log("handoff.create", actor="system", target_type="handoff",
              target_id=handoff_id,
              detail={"thread_id": thread_id, "trigger": trigger, "reason": reason})

    return {"ok": True, "handoff_id": handoff_id, "queued": True,
            "notify": notify_result, "reused": False}


# ============================================================
# 查询与静默判断
# ============================================================
def active_handoff(thread_id: str) -> Optional[Dict]:
    """该会话当前未结束的工单（queued 或 claimed）。"""
    if not thread_id:
        return None
    with session_scope() as s:
        h = s.execute(
            select(HumanHandoff)
            .where(HumanHandoff.thread_id == thread_id,
                   HumanHandoff.status.in_(["queued", "claimed"]))
            .order_by(HumanHandoff.id.desc() if hasattr(HumanHandoff, "id")
                      else HumanHandoff.created_at.desc())
        ).scalars().first()
        return h.to_dict() if h else None


def claimed_handoff(thread_id: str) -> Optional[Dict]:
    """该会话是否已被坐席接管（claimed）—— 用于 AI 静默判断。"""
    if not thread_id:
        return None
    try:
        with session_scope() as s:
            h = s.execute(
                select(HumanHandoff)
                .where(HumanHandoff.thread_id == thread_id,
                       HumanHandoff.status == "claimed")
                .order_by(HumanHandoff.created_at.desc())
            ).scalars().first()
            return h.to_dict() if h else None
    except Exception:
        return None


def is_ai_muted(thread_id: str) -> bool:
    """坐席接管中 → AI 必须静默。"""
    return claimed_handoff(thread_id) is not None


def list_handoffs(status: Optional[str] = None, limit: int = 200) -> List[Dict]:
    with session_scope() as s:
        q = select(HumanHandoff).order_by(HumanHandoff.created_at.desc()).limit(max(1, min(limit, 1000)))
        if status:
            q = q.where(HumanHandoff.status == status)
        rows = s.execute(q).scalars().all()
        return [r.to_dict() for r in rows]


def get_handoff(handoff_id: str) -> Optional[Dict]:
    with session_scope() as s:
        h = s.get(HumanHandoff, handoff_id)
        if not h:
            return None
        d = h.to_dict()
        rows = s.execute(
            select(ConversationMessage)
            .where(ConversationMessage.thread_id == h.thread_id)
            .order_by(ConversationMessage.id.asc())
            .limit(500)
        ).scalars().all()
        d["messages"] = [r.to_dict() for r in rows]
    return d


# ============================================================
# 认领 / 回复 / 结束
# ============================================================
def claim(handoff_id: str, user: Dict) -> Dict:
    """坐席认领。已被别人认领则失败。"""
    uid = int(user.get("id") or 0)
    uname = user.get("name") or ""
    with session_scope() as s:
        h = s.get(HumanHandoff, handoff_id)
        if not h:
            return {"ok": False, "error": "工单不存在"}
        if h.status == "claimed":
            if h.claimed_by == uid:
                return {"ok": True, "handoff_id": handoff_id, "already_mine": True}
            return {"ok": False, "error": f"已被 {h.claimed_by_name or '他人'} 认领"}
        if h.status in ("closed", "timeout"):
            return {"ok": False, "error": f"工单已{STATUS_LABELS.get(h.status, h.status)}，无法认领"}
        h.status = "claimed"
        h.claimed_by = uid
        h.claimed_by_name = uname
        h.claimed_at = datetime.now()
        thread_id = h.thread_id
        client_open_id = h.sender_open_id

    # 通知用户：人工已接入
    if client_open_id:
        try:
            send_private(client_open_id, "客服已接入，正在为您服务。")
        except Exception as e:
            print(f"[HANDOFF] 通知用户失败（已忽略）: {e}")

    record_message(thread_id, ROLE_SYSTEM, f"客服「{uname}」已接入，本次由人工为您服务。",
                   handoff_id=handoff_id)

    audit_log("handoff.claim", actor=uname, target_type="handoff",
              target_id=handoff_id, detail={"thread_id": thread_id})
    return {"ok": True, "handoff_id": handoff_id}


def reply(handoff_id: str, text: str, user: Dict) -> Dict:
    """坐席以「本人」身份回复，直发飞书。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "回复内容不能为空"}
    uname = user.get("name") or "客服"

    with session_scope() as s:
        h = s.get(HumanHandoff, handoff_id)
        if not h:
            return {"ok": False, "error": "工单不存在"}
        if h.status != "claimed":
            return {"ok": False, "error": "请先认领该会话再回复"}
        if h.claimed_by and h.claimed_by != int(user.get("id") or 0):
            return {"ok": False, "error": f"该会话由 {h.claimed_by_name} 接管中"}
        thread_id = h.thread_id
        client_open_id = h.sender_open_id

    sent, err = False, "未配置用户 open_id"
    if client_open_id:
        try:
            sent, err = send_private(client_open_id, text)
        except Exception as e:
            sent, err = False, str(e)

    record_message(thread_id, ROLE_HUMAN, text, sender_name=uname,
                   handoff_id=handoff_id, meta={"sent": sent, "error": err})

    audit_log("handoff.reply", actor=uname, target_type="handoff", target_id=handoff_id,
              detail={"chars": len(text), "sent": sent, "error": err},
              result="ok" if sent else "failed")

    return {"ok": bool(sent), "sent": sent, "error": "" if sent else err}


def close(handoff_id: str, user: Dict, note: str = "") -> Dict:
    """结束接管，切回 AI。"""
    uname = user.get("name") or "客服"
    with session_scope() as s:
        h = s.get(HumanHandoff, handoff_id)
        if not h:
            return {"ok": False, "error": "工单不存在"}
        if h.status == "closed":
            return {"ok": True, "handoff_id": handoff_id, "already": True}
        h.status = "closed"
        h.closed_at = datetime.now()
        h.close_note = (note or "")[:256]
        if not h.claimed_by:
            h.claimed_by = int(user.get("id") or 0)
            h.claimed_by_name = uname
        thread_id = h.thread_id
        client_open_id = h.sender_open_id
        duration = None
        if h.claimed_at:
            duration = int((h.closed_at - h.claimed_at).total_seconds())

    if client_open_id:
        try:
            send_private(client_open_id, "本次人工服务已结束，已转回智能助手。")
        except Exception:
            pass

    record_message(thread_id, ROLE_SYSTEM, "人工服务已结束，已转回智能助手。",
                   handoff_id=handoff_id)

    audit_log("handoff.close", actor=uname, target_type="handoff", target_id=handoff_id,
              detail={"note": note, "duration_sec": duration})
    return {"ok": True, "handoff_id": handoff_id, "duration_sec": duration}


# ============================================================
# 超时巡检（供后台线程调用）
# ============================================================
def patrol_timeouts() -> Dict:
    """处理超时未认领的转人工工单。

    queued 超过 handoff_timeout_seconds（默认 300 秒）：
      · 标记 timeout
      · 升级通知主管（飞书）
      · 告知用户「暂无人接入，已记录并会尽快联系」
    """
    s = get_settings()
    timeout_sec = int(getattr(s, "handoff_timeout_seconds", 300) or 300)
    deadline = datetime.now() - timedelta(seconds=timeout_sec)

    with session_scope() as sess:
        rows = sess.execute(
            select(HumanHandoff).where(
                HumanHandoff.status == "queued",
                HumanHandoff.created_at < deadline,
            )
        ).scalars().all()
        targets = [r.to_dict() for r in rows]
        for r in rows:
            r.status = "timeout"
            r.closed_at = datetime.now()
            r.close_note = f"超过 {timeout_sec} 秒无人认领，自动标记超时"

    if not targets:
        return {"timed_out": 0}

    for t in targets:
        try:
            from app.services.feishu_router import dispatch
            dispatch(
                event="handoff_timeout",
                title=f"【转人工超时】{t['handoff_id']}",
                content=(
                    f"该转人工请求已等待超过 {timeout_sec} 秒仍无人认领。\n"
                    f"原因：{t.get('reason') or '-'}\n"
                    f"用户：{t.get('sender_open_id') or '未知'}\n"
                    f"最后消息：{(t.get('last_user_msg') or '')[:200]}"
                ),
                extra_roles=["supervisor"],
            )
        except Exception as e:
            print(f"[HANDOFF] 超时升级通知失败（已忽略）: {e}")

        oid = t.get("sender_open_id") or ""
        if oid:
            try:
                send_private(oid, "抱歉，当前客服繁忙，您的请求已记录，我们会尽快与您联系。")
            except Exception:
                pass

        record_message(t["thread_id"], ROLE_SYSTEM,
                       "人工客服暂无人接入，已记录您的请求并升级通知主管。",
                       handoff_id=t["handoff_id"])

    print(f"[HANDOFF] 超时巡检：{len(targets)} 单已标记 timeout 并升级通知")
    return {"timed_out": len(targets)}


# ============================================================
# 统计
# ============================================================
def stats() -> Dict:
    with session_scope() as s:
        by_status = {k: 0 for k in STATUS_LABELS}
        for st, n in s.execute(
            select(HumanHandoff.status, func.count(HumanHandoff.handoff_id))
            .group_by(HumanHandoff.status)
        ).all():
            by_status[st or "queued"] = int(n)

        total = sum(by_status.values())

        # 平均首次响应时长（创建 → 认领）
        rows = s.execute(
            select(HumanHandoff.created_at, HumanHandoff.claimed_at)
            .where(HumanHandoff.claimed_at.isnot(None))
        ).all()
        # ⚠️ 是 claimed_at - created_at，写成反过来会得到负数
        delays = [int((claimed - created).total_seconds())
                  for created, claimed in rows if created and claimed]
        avg_claim = round(sum(delays) / len(delays), 1) if delays else 0.0

        # 按坐席
        per_agent = []
        for name, n in s.execute(
            select(HumanHandoff.claimed_by_name, func.count(HumanHandoff.handoff_id))
            .where(HumanHandoff.claimed_by_name != "")
            .group_by(HumanHandoff.claimed_by_name)
        ).all():
            per_agent.append({"name": name, "count": int(n)})
        per_agent.sort(key=lambda x: -x["count"])

    return {
        "total": total,
        "by_status": by_status,
        "status_labels": STATUS_LABELS,
        "queued": by_status.get("queued", 0),
        "claimed": by_status.get("claimed", 0),
        "closed": by_status.get("closed", 0),
        "timeout": by_status.get("timeout", 0),
        "avg_claim_seconds": avg_claim,
        "per_agent": per_agent,
    }


def mute_reply_text(handoff: Optional[Dict] = None) -> str:
    """AI 静默期间给用户的提示文案。"""
    who = (handoff or {}).get("claimed_by_name") or "客服"
    return f"人工客服「{who}」正在为您服务，请稍候。"


def muted_reply(handoff: Optional[Dict]) -> str:
    """静默期间**要不要回话、回什么**。返回空串表示保持安静。

    模式由 HANDOFF_MUTED_MODE 控制：

      never  （默认）完全不回。
             坐席认领时已经私聊告诉过用户「客服已接入」，
             之后用户每来一条都回「正在为您服务」纯属噪音 ——
             用户在等的是**坐席的答复**，不是机器人的复读。

      first  本次接管只回一次，之后安静。
             适合认领通知可能发不出去的场景。

      always 每条都回（旧行为，建议只在调试时用）。

    `_notified` 由 before_turn() 注入，表示本次接管是否已经提示过用户。
    """
    if not handoff:
        return ""
    mode = str(getattr(get_settings(), "handoff_muted_mode", "never") or "never").strip().lower()
    if mode == "always":
        return mute_reply_text(handoff)
    if mode == "first":
        return "" if handoff.get("_notified") else mute_reply_text(handoff)
    return ""


def _muted_notice_sent(handoff_id: str) -> bool:
    """本次接管是否已经给用户回过话（first 模式用）。"""
    if not handoff_id:
        return False
    try:
        with session_scope() as s:
            n = s.execute(
                select(func.count(ConversationMessage.id)).where(
                    ConversationMessage.handoff_id == handoff_id,
                    ConversationMessage.role == ROLE_ASSISTANT,
                )
            ).scalar() or 0
        return int(n) > 0
    except Exception:
        return False


# ============================================================
# 入站 / 出站钩子（供 openai_compat 与 stream 两个入口调用）
# ============================================================
INTENT_REASON = {
    "human": ("用户主动要求转人工", "human_intent"),
    "complaint": ("用户投诉，需人工介入", "complaint"),
}


def _should_handoff(final: Dict) -> bool:
    """判断这一轮是否要转人工。

    覆盖三种来源：用户主动要求、投诉、规则声明需要人工（action_result.need_human）。
    刻意【不】把低置信度也算进来 —— 那会把大量普通问答都推给人工。
    """
    intent = (final or {}).get("intent") or ""
    if intent in ("human", "complaint"):
        return True
    ar = (final or {}).get("action_result") or {}
    return bool(ar.get("need_human"))


def _handoff_reason(final: Dict) -> tuple:
    intent = (final or {}).get("intent") or ""
    if intent in INTENT_REASON:
        return INTENT_REASON[intent]
    return ("业务规则要求人工处理", "rule_need_human")


def before_turn(thread_id: str, user_text: str, sender_open_id: str = "") -> Optional[Dict]:
    """一轮对话【开始前】调用。

    · 落库用户消息
    · 若该会话已被坐席接管 → 返回工单 dict，调用方必须让 AI 静默
    · 否则返回 None，正常走工作流

    返回的 dict 额外带 `_notified`：本次接管是否已提示过用户，
    供 muted_reply() 的 first 模式判断。
    """
    h = claimed_handoff(thread_id)
    record_message(thread_id, ROLE_USER, user_text, sender_open_id=sender_open_id)
    if h:
        h["_notified"] = _muted_notice_sent(h.get("handoff_id") or "")
    return h


def after_turn(thread_id: str, user_text: str, answer: str,
               final: Optional[Dict] = None, sender_open_id: str = "") -> Optional[Dict]:
    """一轮对话【结束后】调用。

    · 落库 AI 回复
    · 命中转人工条件时创建工单并通知坐席（create_handoff 自带幂等保护）
    """
    final = final or {}
    record_message(thread_id, ROLE_ASSISTANT, answer,
                   sender_open_id=sender_open_id,
                   intent=final.get("intent") or "")
    if not _should_handoff(final):
        return None
    reason, trigger = _handoff_reason(final)
    try:
        return create_handoff(
            thread_id, sender_open_id=sender_open_id,
            trigger=trigger, reason=reason, last_user_msg=user_text,
        )
    except Exception as e:
        print(f"[HANDOFF] 自动转人工失败（已忽略）: {e}")
        return None
