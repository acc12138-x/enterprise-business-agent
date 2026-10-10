from __future__ import annotations

from langgraph.types import interrupt

from app.workflows.state import AgentState

CONFIDENCE_THRESHOLD = 0.5
HIGH_RISK_INTENTS = {"human", "complaint"}

# 低置信度时【跳过】人工确认的意图。
#
# ⚠️ ticket（报修建单）**刻意不在这里** —— 它是会真的写库、真的派单的
#    高风险动作：意图识别不准却照样建单，既会产生脏数据，用户也会卡住
#    等一个不存在的工程师。宁可多问一句「您是XX型号吗」。
#
#    留在里面的三类都是**只读查询**（查订单 / 查物流 / 查工程师），
#    就算意图判错也只是答非所问，没有副作用，为省一次确认而跳过。
SKIP_CONFIDENCE_INTENTS = {"order_query", "logistics", "engineer_query"}


def need_hitl(state: AgentState) -> bool:
    intent = state.get("intent", "")
    if intent in SKIP_CONFIDENCE_INTENTS:
        return False
    if intent in HIGH_RISK_INTENTS:
        return True
    # 规则动作声明需要人工审批（action_exec 写入 action_result.need_human）
    action_result = state.get("action_result") or {}
    if action_result.get("need_human"):
        return True
    if state.get("flow_status") == "rejected":
        return False
    if state.get("confidence", 1.0) < CONFIDENCE_THRESHOLD:
        return True
    return False


def _is_approve(decision) -> bool:
    s = str(decision or "").lower()
    return ("approve" in s) or (s in ("是", "对", "ok", "okay", "yes", "y"))


def hitl_gate_node(state: AgentState) -> AgentState:
    trace = state.get("trace_id", "?")[:8]
    need = need_hitl(state)
    print(f"[HITL:{trace}] need_hitl={need} intent={state.get('intent')} hitl_pending={state.get('hitl_pending')}")
    if not need_hitl(state):
        return {
            **state,
            "hitl_pending": False,
            "flow_status": state.get("flow_status", "succeeded"),
        }

    reason = state.get("hitl_reason") or "需要人工确认"
    intent = state.get("intent", "")

    decision = interrupt({
        "type": "hitl",
        "reason": reason,
        "intent": intent,
        "user_input": state.get("user_input"),
        "allowed": ["approve", "block_revise: <原因>"],
    })

    decision_str = str(decision)
    messages = state.get("messages", []) or []

    if _is_approve(decision):
        if intent == "human":
            new_answer = "✅ 已确认转人工，客服稍后接入。"
        elif intent == "complaint":
            new_answer = "✅ 已受理您的投诉，客服将在 24 小时内与您联系。"
        else:
            new_answer = "✅ 已确认。\n\n" + (state.get("answer") or "")
        flow = "succeeded"
    else:
        new_answer = f"❌ 已取消：{decision_str}"
        flow = "cancelled"

    messages = messages + [{"role": "assistant", "content": new_answer}]

    return {
        **state,
        "answer": new_answer,
        "hitl_pending": False,
        "hitl_decision": decision_str,
        "messages": messages,
        "flow_status": flow,
    }
