"""图路由测试。

本文件的由来（一次真实故障）
新增 lead 意图时，节点、边、路由返回值都改了，
**但忘了把 lead_capture_node 登记进 slot_filling 的条件边映射表**。
于是用户发「我们公司想谈合作」时 LangGraph 直接抛错 →
后端 500 → 飞书里只显示网关那句
「⚠️ Something went wrong while processing your request.」

当时的冒烟测试只断言了「lead_capture_node 在 g.nodes 里」——
**没有真正跑一遍**，所以完全没发现。

这里补两道防线：
  1. 静态：路由函数返回的每个值，都必须是条件边 path_map 里的键
  2. 动态：对不依赖 LLM 的意图，真的 invoke 一次图
"""
import pathlib
import re
import uuid

import pytest

GRAPH_PY = pathlib.Path(__file__).resolve().parents[1] / "app" / "workflows" / "graph.py"


def _source() -> str:
    return GRAPH_PY.read_text(encoding="utf-8")


def _tid(prefix: str) -> str:
    """每次运行都用独立的会话 id。

    写死会话 id 会让测试**不可重复运行** —— 上一轮留下的线索/工单会污染
    下一轮（例如「已结束的线索可以新建」那条，第二次跑时
    find_open_lead_by_thread 一开始就返回 None，断言全乱）。
    """
    return f"pytest-{prefix}-{uuid.uuid4().hex[:8]}"


class TestRouteMapStatic:
    """静态防线：return 出去的目标必须在映射表里登记过。"""

    def test_every_return_target_is_a_registered_key(self):
        src = _source()

        # 条件边映射表里的键：形如 "xxx": "yyy"
        keys = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)":\s*"', src))
        # path_map 里也有直接映射到 END 的键
        keys |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)":\s*END', src))

        # 路由函数里 return 出来的字符串
        returns = set(re.findall(r'return\s+"([A-Za-z_][A-Za-z0-9_]*)"', src))

        # "end" 是特殊键，映射到 END
        missing = sorted(returns - keys - {"end"})
        assert not missing, (
            f"这些路由返回值没有登记进条件边映射表，命中时会直接抛错：{missing}"
        )

    def test_end_key_is_mapped_where_used(self):
        """凡是可能返回 "end" 的路由，对应的 path_map 里必须有 "end" 键。"""
        src = _source()
        assert '"end": END' in src, '条件边里缺少 "end": END 的映射'


class TestRouteFunctions:
    """直接调用路由函数，断言返回值是合理的节点名。"""

    def test_route_after_intent(self):
        from app.workflows.graph import route_after_intent
        assert route_after_intent({"intent": "human"}) == "hitl_gate"
        assert route_after_intent({"intent": "complaint"}) == "hitl_gate"
        assert route_after_intent({"intent": "lead"}) == "slot_filling"
        assert route_after_intent({"intent": "qa"}) == "slot_filling"
        assert route_after_intent({}) == "slot_filling"

    def test_route_after_slot_lead(self):
        """★ 回归重点：lead 必须路由到 lead_capture_node。"""
        from app.workflows.graph import route_after_slot
        assert route_after_slot({"intent": "lead"}) == "lead_capture_node"

    def test_route_after_slot_others(self):
        from app.workflows.graph import route_after_slot
        assert route_after_slot({"intent": "chitchat"}) == "chitchat_node"
        assert route_after_slot({"intent": "ticket"}) == "ticket_node"
        assert route_after_slot({"intent": "refund_apply"}) == "refund_apply"
        assert route_after_slot({"intent": "my_tickets"}) == "my_tickets_node"
        assert route_after_slot({"intent": "engineer_query"}) == "engineer_query"
        assert route_after_slot({"intent": "qa"}) == "rag_search"
        # 缺槽位时结束本轮，等用户补充
        assert route_after_slot({"intent": "ticket", "missing_slots": ["error_code"]}) == "end"


class TestGraphActuallyRuns:
    """动态防线：真的跑一遍（只挑不依赖 LLM 的意图）。"""

    def _invoke(self, text: str, tid: str) -> dict:
        from app.workflows.graph import graph
        return graph.invoke(
            {"thread_id": tid, "user_input": text},
            config={"configurable": {"thread_id": tid}},
        )

    def test_lead_intent_end_to_end(self):
        """★ 回归重点：这条消息就是当时 500 的那句。

        修复前：route_after_slot 返回 lead_capture_node，
               但映射表里没有 → LangGraph 抛 KeyError。
        """
        from app.services import lead_service

        tid = _tid("graph-lead")
        final = self._invoke("我们公司想谈长期合作，手机 13800138000", tid)

        assert final.get("intent") == "lead", final.get("intent")
        answer = final.get("answer") or ""
        assert answer, "lead 意图必须有回复"
        assert "LD-" in answer, f"回复里应带线索编号，实际：{answer}"

        found = lead_service.find_open_lead_by_thread(tid)
        assert found, "lead 意图应创建线索"
        assert found["phone"] == "13800138000"

    def test_chitchat_intent_end_to_end(self):
        tid = _tid("graph-chitchat")
        final = self._invoke("你好啊", tid)
        assert final.get("intent") == "chitchat"
        assert final.get("answer")

    def test_lead_capture_is_idempotent(self):
        """同一会话重复说合作意向，不该建出两条线索。"""
        from app.services import lead_service

        tid = _tid("graph-lead-idem")
        f1 = self._invoke("想谈长期合作，手机 13900139000", tid)
        f2 = self._invoke("还是想合作", tid)

        first = lead_service.find_open_lead_by_thread(tid)
        assert first
        # 第二次应复用同一条线索：回复里带的是同一个编号
        assert first["lead_id"] in (f1.get("answer") or "")
        assert first["lead_id"] in (f2.get("answer") or "")

    def test_closed_lead_allows_new_one(self):
        """已结束的线索不阻挡新建（去重只针对未结束的）。"""
        from app.services import lead_service

        tid = _tid("graph-lead-reopen")
        self._invoke("想合作，手机 13700137000", tid)
        first = lead_service.find_open_lead_by_thread(tid)
        assert first

        # 标记流失 → 不再算「未结束」
        lead_service.update_lead(first["lead_id"],
                                 {"stage": "lost", "lost_reason": "pytest"},
                                 actor="pytest")
        assert lead_service.find_open_lead_by_thread(tid) is None

        self._invoke("重新想合作了", tid)
        second = lead_service.find_open_lead_by_thread(tid)
        assert second and second["lead_id"] != first["lead_id"]
