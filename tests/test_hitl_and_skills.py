"""HITL 策略与 OpenClaw Skill 测试。

两处修复的回归守卫：

1. **报修建单不该跳过人工确认**
   原先 `SKIP_CONFIDENCE_INTENTS = {ticket, order_query, logistics, engineer_query}`
   —— 报修意图识别不准（低置信度）时**永远不会**转人工。
   但 ticket 是**会真的写库、真的派单**的高风险动作：判错了会产生脏数据，
   用户还会卡住等一个不存在的工程师。现已把它从跳过列表移除。
   留在列表里的应当只有**只读查询**。

2. **`notify_human` skill 曾经是空壳**
   只 `return {"status": "queued"}`，不建单、不通知任何人；
   而真实的转人工走的是 hitl_gate → after_turn → create_handoff。
   同一个能力两套实现、只有一套是真的。现已改为内部转调 handoff_service。
"""
import uuid

import pytest


def _tid(prefix: str) -> str:
    return f"pytest-{prefix}-{uuid.uuid4().hex[:8]}"


class TestHitlPolicy:
    """`need_hitl()` 的判定策略。"""

    def test_ticket_low_confidence_needs_human(self):
        """★ 回归重点：报修建单低置信度必须转人工。

        修复前这条断言会失败（ticket 在跳过列表里 → 直接返回 False）。
        """
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": "ticket", "confidence": 0.1}) is True

    def test_ticket_high_confidence_skips_human(self):
        """识别得准就别打扰人工 —— 只对低置信度兜底，不是所有报修都拦。"""
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": "ticket", "confidence": 0.95}) is False

    @pytest.mark.parametrize("intent", ["order_query", "logistics", "engineer_query"])
    def test_readonly_intents_skip_confidence_check(self, intent):
        """只读查询判错也无副作用，跳过确认以省事。"""
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": intent, "confidence": 0.05}) is False

    def test_skip_list_contains_only_readonly_intents(self):
        """★ 结构性守卫：跳过列表里不许出现会写库的意图。

        以后有人往里面加 ticket / refund_apply / complaint 之类，
        这条会立刻失败。
        """
        from app.workflows.nodes.hitl_gate import SKIP_CONFIDENCE_INTENTS
        write_intents = {"ticket", "refund_apply", "compensation", "complaint", "human", "lead"}
        overlap = SKIP_CONFIDENCE_INTENTS & write_intents
        assert not overlap, f"这些会写库的意图不该跳过人工确认：{overlap}"

    @pytest.mark.parametrize("intent", ["human", "complaint"])
    def test_high_risk_always_needs_human(self, intent):
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": intent, "confidence": 0.99}) is True

    def test_rule_declared_need_human(self):
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": "qa", "confidence": 0.9,
                          "action_result": {"need_human": True}}) is True

    def test_high_confidence_qa_no_human(self):
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": "qa", "confidence": 0.9}) is False

    def test_rejected_flow_does_not_ask_again(self):
        from app.workflows.nodes.hitl_gate import need_hitl
        assert need_hitl({"intent": "qa", "confidence": 0.1,
                          "flow_status": "rejected"}) is False


class TestNotifyHumanSkill:
    """★ 回归重点：这个 skill 以前不建单也不通知任何人。"""

    def test_skill_creates_real_handoff(self):
        from app.gateway.skills import invoke_skill
        from app.services import handoff_service as hs

        tid = _tid("skill")
        res = invoke_skill("notify_human", thread_id=tid, reason="pytest 调用")
        assert res["status"] == "queued"
        assert res.get("handoff_id"), "skill 必须真的建出工单（原来是空壳）"

        d = hs.get_handoff(res["handoff_id"])
        assert d is not None, "建出来的工单必须能查到"
        assert d["thread_id"] == tid
        hs.close(res["handoff_id"], {"id": 0, "name": "pytest"})

    def test_skill_is_idempotent(self):
        """同一会话重复调用应复用未结束的工单，而不是建一堆。"""
        from app.gateway.skills import invoke_skill
        from app.services import handoff_service as hs

        tid = _tid("skill-idem")
        a = invoke_skill("notify_human", thread_id=tid, reason="第一次")
        b = invoke_skill("notify_human", thread_id=tid, reason="第二次")
        assert a["handoff_id"] == b["handoff_id"]
        assert b["reused"] is True
        hs.close(a["handoff_id"], {"id": 0, "name": "pytest"})

    def test_skill_and_graph_path_share_one_implementation(self):
        """skill 建出来的工单与图层建的必须是同一张表、同一套状态。"""
        from app.gateway.skills import invoke_skill
        from app.services import handoff_service as hs

        tid = _tid("skill-shared")
        res = invoke_skill("notify_human", thread_id=tid, reason="一致性检查")
        hid = res["handoff_id"]

        # 认领后 AI 静默 —— 这是图层那套路径的核心行为，skill 建的也要生效
        hs.claim(hid, {"id": 999001, "name": "pytest 坐席"})
        assert hs.is_ai_muted(tid) is True
        hs.close(hid, {"id": 999001, "name": "pytest 坐席"})
        assert hs.is_ai_muted(tid) is False

    def test_skill_return_shape_is_backward_compatible(self):
        """老字段一个都不能少（网关侧可能已经按老结构解析）。"""
        from app.gateway.skills import invoke_skill
        from app.services import handoff_service as hs

        res = invoke_skill("notify_human", thread_id=_tid("skill-shape"))
        for k in ("thread_id", "reason", "status", "message"):
            assert k in res, f"缺少老字段 {k}"
        assert res["message"] == "已转人工，请等待客服接入"
        hs.close(res["handoff_id"], {"id": 0, "name": "pytest"})


class TestSkillRegistry:
    def test_all_five_skills_registered(self):
        from app.gateway.skills import list_skill_names, list_skill_schemas
        names = list_skill_names()
        assert set(names) == {"search_kb", "query_order", "create_ticket",
                              "assign_ticket", "notify_human"}
        # 每个 skill 都要有 schema，否则网关注册不了工具
        assert {s["name"] for s in list_skill_schemas()} == set(names)

    def test_unknown_skill_raises(self):
        from app.gateway.skills import invoke_skill
        with pytest.raises(KeyError):
            invoke_skill("not_exist")
