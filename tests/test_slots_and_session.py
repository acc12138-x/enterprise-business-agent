"""会话隔离 与 槽位互斥 两个修复的回归测试。

一、群聊会话必须按「群 + 人」隔离
线上日志确认：session_key 会退化为 chat_id，而群聊的 chat_id 是**群 ID**，
与发送者无关 —— 于是同一个群里 A、B 共用一段多轮上下文和同一个 HITL 断点，
A 提到的订单号/手机号会出现在 B 的上下文里。

二、型号不能被故障码顶替
intent_node 里曾有一行：
    if "error_code" in slots and "device_model" not in slots:
        slots["device_model"] = slots["error_code"]
目的是"让工单显得信息完整"，后果是**凭空编造型号**。
真实数据：TB8DEA7C1 型号=E200 故障码=E200、TF4E4FB0E 型号=E300 故障码=E300。
用户只说「创建工单E300损坏」，工单却有了型号，于是跳过追问，
工程师拿着错误型号去修。
"""
import pytest


@pytest.fixture(scope="module")
def oc():
    from app.api.routes import openai_compat
    return openai_compat


@pytest.fixture(scope="module")
def intent():
    from app.workflows.nodes import intent
    return intent


class TestGroupSessionIsolation:
    """★ 回归重点：同群不同人必须是不同会话。"""

    def test_same_group_different_sender_differs(self, oc):
        a = oc.build_session_key(None, None, None, "oc_abc123", "ou_AAA")
        b = oc.build_session_key(None, None, None, "oc_abc123", "ou_BBB")
        assert a != b, "同群两人拿到同一个会话键 —— 上下文会互相串"

    def test_sender_comes_first_so_truncation_is_safe(self, oc):
        """_thread_id 取前 20 字符，发送者必须在前面。

        群 ID 形如 oc_xxx，前 20 字符几乎全是固定前缀；若把人放在后面，
        截断后全群又会撞回同一个线程 —— 等于没修。
        """
        key = oc.build_session_key(None, None, None, "oc_" + "x" * 40, "ou_AAA")
        assert key.startswith("ou_AAA")
        assert "ou_AAA" in key[:20]

    def test_private_chat_unchanged(self, oc):
        """私聊保持原样 —— 不能因为这次修复让存量私聊会话被重置。"""
        assert oc.build_session_key(None, None, None, "ou_AAA", "ou_AAA") == "ou_AAA"

    def test_group_without_sender_falls_back(self, oc):
        """没有发送者标识时只能退化为按群（好过随机）。"""
        assert oc.build_session_key(None, None, None, "oc_abc", None) == "oc_abc"

    def test_gateway_session_id_still_wins(self, oc):
        """网关若明确给了会话 id，就以它为准，不做拼装。"""
        assert oc.build_session_key("sess-1", None, None, "oc_abc", "ou_AAA") == "sess-1"

    def test_empty_everything(self, oc):
        assert oc.build_session_key(None, None, None, None, None) == ""


class TestDeviceModelNotFabricated:
    """★ 回归重点：型号缺失就该缺失，让它去触发追问。"""

    def test_only_error_code_leaves_model_empty(self, intent):
        slots = intent._extract_slots("创建工单E300损坏")
        assert slots.get("error_code") == "E300"
        assert "device_model" not in slots, \
            f"型号被凭空编出来了：{slots.get('device_model')}"

    def test_both_present_are_extracted(self, intent):
        slots = intent._extract_slots("XY200 报 E102")
        assert slots.get("device_model") == "XY200"
        assert slots.get("error_code") == "E102"

    def test_explicit_model_keyword(self, intent):
        slots = intent._extract_slots("型号 XY300 报 E102")
        assert slots.get("device_model") == "XY300"
        assert slots.get("error_code") == "E102"

    def test_empty_input_gives_no_slots(self, intent):
        assert intent._extract_slots("帮我建个工单") == {}

    def test_missing_model_triggers_incomplete(self):
        """端到端含义：型号为空时工单应当被判为"信息不完整"，从而触发追问。"""
        from app.api.routes.tickets import _compute_missing
        missing = _compute_missing(device_model=None, error_code="E300")
        assert missing, "型号缺失没有被识别出来 —— 追问不会发生"
