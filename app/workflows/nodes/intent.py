"""意图识别节点：意图 + 槽位 + 多轮上下文（含 HITL 标记）。"""
from __future__ import annotations
import re
import time
from typing import Dict, Optional

from app.intent.factory import get_intent_engine, reload_intent_engine
from app.workflows.state import AgentState

# ---------- 槽位正则 ----------
ORDER_ID_RE = re.compile(r"([Oo]\d{8,20})")
PHONE_RE = re.compile(r"(1[3-9]\d{9})")
ERROR_CODE_RE = re.compile(r"([Ee]\d{2,4})")
DEVICE_MODEL_RE = re.compile(r"([A-Z]{1,4}\d{2,5})")
DEVICE_MODEL_CN_RE = re.compile(r"型号[：: ]*([A-Za-z0-9\-]{2,20})")
ADDRESS_RE = re.compile(r"(地址[：: ].+)")
AMOUNT_RE = re.compile(r"(?:金额|退款|赔付|补偿)[：: ]*([0-9]+(?:\.[0-9]+)?)")
AMOUNT_RE2 = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:元|块)")

# 需要触发 HITL 的意图
HITL_INTENTS = {"human", "complaint"}

# ---------- 意图引擎动态重载 ----------
_engine_reload_ts = 0
_engine_reload_interval = 60


def _maybe_reload_engine():
    global _engine_reload_ts
    now = time.time()
    if now - _engine_reload_ts > _engine_reload_interval:
        try:
            reload_intent_engine("default")
            _engine_reload_ts = now
        except Exception:
            pass


def _extract_slots(text: str) -> Dict:
    slots: Dict = {}
    remaining = text

    m = ORDER_ID_RE.search(remaining)
    if m:
        slots["order_id"] = m.group(1)
        remaining = remaining.replace(m.group(0), " ")

    m = PHONE_RE.search(remaining)
    if m:
        slots["phone"] = m.group(1)

    # 错误码：优先从末尾 120 字符找（避免长上下文污染）
    tail_for_code = text[-120:] if len(text) > 120 else text
    m = ERROR_CODE_RE.search(tail_for_code)
    if m:
        slots["error_code"] = m.group(1).upper()
        # 从 remaining 中移除，避免被后续 device_model 重复匹配
        remaining = remaining.replace(m.group(1), " ")

    m = DEVICE_MODEL_CN_RE.search(remaining)
    if m:
        slots["device_model"] = m.group(1)
    else:
        exclude = slots.get("error_code", "")
        m = DEVICE_MODEL_RE.search(remaining)
        if m:
            candidate = m.group(1)
            if (not (candidate.startswith(("O", "o")) and candidate[1:].isdigit())
                    and candidate.upper() != exclude.upper()):
                slots["device_model"] = candidate

    m = ADDRESS_RE.search(remaining)
    if m:
        slots["address"] = m.group(1)

    m = AMOUNT_RE.search(remaining) or AMOUNT_RE2.search(remaining)
    if m:
        try:
            slots["amount"] = float(m.group(1))
        except Exception:
            pass

    if re.match(r"^[\s\+\-]*(1[3-9]\d{9})[\s\+\-]*$", remaining.strip()):
        m2 = re.search(r"1[3-9]\d{9}", remaining)
        if m2:
            slots["phone"] = m2.group(0)

    # ⚠️ 这里**不能**把 error_code 复制成 device_model。
    #
    # 曾经有这么一行（目的是"让工单显得信息完整"，好通过 is_complete 校验）：
    #     if "error_code" in slots and "device_model" not in slots:
    #         slots["device_model"] = slots["error_code"]
    #
    # 后果是**凭空编造型号**：用户只说「创建工单E300损坏」，
    # 工单就变成「型号=E300 故障码=E300」，两个字段都是故障码。
    # 于是 is_complete 通过、追问被跳过，工程师拿着一个错误的型号去修。
    # 真实数据：TB8DEA7C1 型号=E200 故障码=E200、TF4E4FB0E 型号=E300 故障码=E300。
    #
    # 正确做法是**留空** —— 型号缺失会被记进 missing_fields，
    # 触发"请补充设备型号"的追问。宁可多问一句，也不要给错的信息。

    print(f"[SLOT] text={text[:100]!r} -> {slots}")
    return slots


# ---------- 咨询判断 ----------
CONSULT_PATTERNS = [
    r"是什么", r"为什么",
    r"怎么(办|了|样|回事|排查|处理|解决|操作|做)",
    r"如何(排查|处理|解决|操作|修|做)?",
    r"适用范围", r"适用",
    r"包含(什么|哪些|哪些内容)?", r"包括(什么|哪些)?",
    r"介绍(一下|一下呗)?", r"说明(一下)?", r"讲讲",
    r"什么意思", r"含义",
    r"能不能", r"会不会", r"是不是", r"可不可以", r"有没有",
    r"第一[步个]",
    r"请教", r"求教", r"请问",
    r"(处理|排查|维修|检查|操作)流?程",
    r"会(怎么|怎样|如何|有什么|不会)",
    r"什么(后果|影响|情况|表现)",
    r"有(什么|哪些)(表现|征兆|症状|后果)",
    r"(故障|报警|异常).{0,4}(处理|排查|解决|方法|方案)",
    r"[吗呢]\s*[？?]?\s*$",
    r"[？?]\s*$",
]

ACTION_WORDS = ["帮我", "给我", "我要", "申请", "创建", "建单", "登记", "报修", "预约"]

BUSINESS_KEYWORDS = [
    "订单", "物流", "快递", "工单", "退款", "退货", "换货",
    "赔付", "补偿", "发票", "保修", "维修", "报修", "客户",
    "手机号", "运单", "发货", "签收", "加急", "急单",
    "工程师", "师傅", "有空", "空闲", "谁在", "负载", "工",
]

# 问候/致谢/告别的「开头词」，用于意图引擎完全未命中时的闲聊兜底
GREETING_PREFIX_RE = re.compile(
    r"^(你好|您好|哈喽|哈啰|嗨|hello|hi|在吗|在不在|在么|早|中午好|下午好|晚上好|晚安|"
    r"你是谁|你叫什么|你能做什么|你会做什么|能帮我做什么|你能干嘛|你会啥|有什么功能|"
    r"帮助|help|菜单|谢谢|感谢|多谢|thank|再见|拜拜|bye)",
    re.IGNORECASE,
)


def _looks_like_chitchat(text: str) -> bool:
    """意图引擎完全未命中时的闲聊兜底。

    仅当「短句 + 以问候/致谢/告别开头 + 无任何业务信号」时成立，
    避免把 "E102 报警"、"怎么退货" 这类短查询误判成闲聊。
    """
    t = (text or "").strip()
    if not t or len(t) > 12:
        return False
    if any(k in t for k in BUSINESS_KEYWORDS):
        return False
    if any(w in t for w in ACTION_WORDS):
        return False
    if re.search(r"\d", t):                    # 订单号 / 故障码 / 手机号
        return False
    if re.search(r"[赵张李王孙周吴郑陈刘杨黄胡]工", t):
        return False
    return bool(GREETING_PREFIX_RE.match(t))


def _is_consult_query(text: str) -> bool:
    if not text:
        return False

    # 新增：故障码(E102) + 报警/故障词，且没有显式动作词 -> 咨询
    has_code = bool(re.search(r"[Ee]\d{2,4}", text))
    has_alarm = any(k in text for k in ["报警", "故障", "异常", "报错"])
    has_action = any(w in text for w in ACTION_WORDS)
    if has_code and has_alarm and not has_action:
        # 但"帮我报修 E102" 这种有动作词的除外
        # 纯"E503 安全门报警" -> qa
        return True

    for w in BUSINESS_KEYWORDS:
        if w in text:
            return False
    for w in ACTION_WORDS:
        if w in text:
            return False
    if re.search(r"1[3-9]\d{9}", text):
        return False
    if re.match(r"^[\s\+\-\d]{8,20}$", text):
        return False
    if re.search(r"[赵张李王孙周吴郑陈刘杨黄胡]工", text):
        return False
    for pat in CONSULT_PATTERNS:
        if re.search(pat, text):
            return True
    return False


# ---------- 多轮上下文 ----------
FOLLOWUP_KEYWORDS = {
    "ticket": ["设备型号", "故障码", "故障代码", "型号"],
    "refund_apply": ["订单号", "退款金额", "金额"],
    "order_query": ["订单号或下单手机号", "订单号"],
    "logistics": ["订单号或下单手机号"],
}


def _detect_followup_intent(messages: list) -> Optional[str]:
    if not messages or len(messages) < 2:
        return None
    last_assistant = None
    for m in reversed(messages):
        if m.get("role") == "assistant":
            last_assistant = m.get("content", "")
            break
    if not last_assistant:
        return None
    if "请提供" not in last_assistant and "请补充" not in last_assistant:
        return None
    for intent, keywords in FOLLOWUP_KEYWORDS.items():
        if any(kw in last_assistant for kw in keywords):
            return intent
    return None


def _is_pure_slot_input(text: str, new_slots: dict) -> bool:
    t = text.strip()
    if not t or len(t) > 30:
        return False
    if not new_slots:
        return False
    for w in BUSINESS_KEYWORDS + ACTION_WORDS:
        if w in t:
            return False
    return True


# ---------- 候选确认 ----------
CODE_CAND_PAT = re.compile(r"(\d+)\.\s*\*\*([A-Z]+\d+)\*\*")


def _detect_code_correction(messages: list):
    if not messages or len(messages) < 2:
        return None
    last_assistant = None
    for m in reversed(messages):
        if m.get("role") == "assistant":
            last_assistant = m.get("content", "")
            break
    if not last_assistant or "您可能是想说" not in last_assistant:
        return None
    cands = CODE_CAND_PAT.findall(last_assistant)
    if not cands:
        return None
    return {int(i): code for i, code in cands}


def _match_correction(text: str, mapping: dict):
    t = text.strip().upper()
    if t.isdigit() and int(t) in mapping:
        return mapping[int(t)]
    for code in mapping.values():
        if code.upper() in t:
            return code
    if t in ("是", "对", "YES", "Y") or t.startswith(("对", "是")):
        return list(mapping.values())[0]
    return None


# ---------- 主节点 ----------
def intent_node(state: AgentState) -> AgentState:
    _maybe_reload_engine()

    text = state.get("user_input", "")
    trace = state.get("trace_id", "?")[:8]
    messages_history = state.get("messages", []) or []
    old_slots = state.get("slots", {}) or {}

    # 1. 候选确认优先
    correction_map = _detect_code_correction(messages_history)
    if correction_map:
        corrected = _match_correction(text, correction_map)
        if corrected:
            new_messages = messages_history + [{"role": "user", "content": text}]
            return {
                **state,
                "intent": "ticket",
                # 用户从候选列表里确认的是一个**故障码**，不是型号。
                # 早先这里把同一个值同时塞进 device_model，等于凭空编造型号
                # （与下面的复制是同一个错）。型号留空，交给追问补。
                "slots": {"error_code": corrected},
                "messages": new_messages,
                "flow_status": "running",
                "hitl_pending": False,
            }

    # 2. 咨询判断
    if _is_consult_query(text):
        new_messages = messages_history + [{"role": "user", "content": text}]
        return {
            **state,
            "intent": "qa",
            "slots": {},
            "messages": new_messages,
            "flow_status": "running",
            "hitl_pending": False,
        }

    # 3. 提取槽位
    new_slots = _extract_slots(text)

    # 4. 多轮补槽位：上一轮存在 pending 追问时，沿用上一轮意图（避免关键词歧义劫持）
    prev_intent = state.get("intent")
    prev_pending = bool(state.get("missing_slots")) and state.get("flow_status") == "waiting"
    followup_intent = _detect_followup_intent(messages_history)
    if prev_pending and prev_intent:
        followup_intent = prev_intent
    is_pure_slot = _is_pure_slot_input(text, new_slots)

    # 5. 意图引擎
    engine = get_intent_engine()
    top = engine.classify_top(text)
    if top:
        engine_intent = top.id
    elif _looks_like_chitchat(text):
        # 引擎没命中，但明显是问候/致谢/告别 → 闲聊，
        # 不要兜底成 qa 去检索知识库（否则会得到「没找到相关内容」的拒答）
        engine_intent = "chitchat"
    else:
        engine_intent = "qa"

    # 6. 决策
    if followup_intent and is_pure_slot:
        merged_slots = dict(old_slots)
        merged_slots.update(new_slots)
        intent_id = followup_intent
    else:
        merged_slots = new_slots
        intent_id = engine_intent

    new_messages = messages_history + [{"role": "user", "content": text}]

    # 7. 判断是否 HITL 意图
    hitl_pending = intent_id in HITL_INTENTS
    hitl_reason = None
    if intent_id == "human":
        hitl_reason = "用户请求转人工"
    elif intent_id == "complaint":
        hitl_reason = "用户投诉，需人工介入"

    # 触发 HITL 时，通知主管（异步不阻塞）
    if hitl_pending:
        try:
            from app.services.feishu_router import dispatch as _dispatch
            _dispatch(
                event="hitl_request",
                title="🚨 用户请求人工",
                content=f"理由：{hitl_reason}\n用户消息：{text[:100]}",
            )
        except Exception:
            pass

    # ============ 后处理：查看/我的工单 ≠ 建单 ============
    if intent_id == "ticket":
        has_create = any(w in text for w in
            ["报修", "建单", "创建", "登记", "帮我报", "我要报",
             "坏了", "报错", "报警", "出问题", "修一下", "故障"])
        has_query = any(w in text for w in
            ["查看", "查", "看", "我的", "列出", "有多少"])
        if has_query and not has_create:
            print(f"[INTENT] 后处理: ticket -> my_tickets")
            intent_id = "my_tickets"

    return {
        **state,
        "intent": intent_id,
        "slots": merged_slots,
        "messages": new_messages,
        "flow_status": "running",
        "hitl_pending": hitl_pending,
        "hitl_reason": hitl_reason,
    }
