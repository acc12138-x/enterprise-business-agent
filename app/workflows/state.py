from __future__ import annotations
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict


MAX_MESSAGES = 10   # 只保留最近 10 条（5 轮对话）


def _append_with_limit(old, new):
    """messages 只保留最近 MAX_MESSAGES 条。

    各节点返回的是完整 messages 列表（已包含历史），LangGraph 会把它作为
    new 传入 reducer；若再叠加 old 会导致同一条消息被重复写入。因此这里
    只对 new 做截断，不再叠加 old。同时防止 checkpointer 累积 state.messages
    导致 checkpoint blob 指数膨胀（曾到 7.88 GB）。
    """
    combined = list(new or [])
    return combined[-MAX_MESSAGES:]


class AgentState(TypedDict, total=False):
    # 会话
    thread_id: str
    trace_id: str
    sender_open_id: str
    user_input: str
    messages: Annotated[List[Dict], _append_with_limit]

    # 意图与槽位
    intent: str
    slots: Dict
    missing_slots: List[str]

    # 【Day 3 新增】业务上下文
    context: Dict[str, Any]         # 订单、物流、商品、客户等聚合信息
    context_errors: List[str]       # 上下文收集失败记录

    # 【Day 3 新增】规则匹配
    rule_matches: List[Dict]        # 命中的规则（转 dict 存储，避免序列化问题）
    rule_message: str               # 规则给出的提示语
    rule_next_action: Optional[str] # 规则建议的下一步动作

    # RAG
    query_rewritten: str
    # 反思与重试的每轮记录：[{attempt, query, hits, top_score, passed}, ...]
    # 保留下来是为了可观测 —— 能回答"这次为什么拒答""改写到第几轮才命中"
    rag_attempts: List[Dict]
    retrieved: List[Dict]
    answer: str
    citations: List[Dict]
    confidence: float

    # 工具调用
    tool_result: Optional[Dict]

    # 【Day 3 新增】动作执行
    action: Optional[str]
    action_result: Optional[Dict]

    # HITL
    hitl_pending: bool
    hitl_reason: str
    hitl_decision: Optional[str]

    # 状态
    flow_status: str
    error: Optional[str]
