"""意图识别测试。"""
import pytest

from app.intent.factory import get_intent_engine
from app.workflows.nodes.chitchat_node import chitchat_node
from app.workflows.nodes.intent import (
    _extract_slots,
    _is_consult_query,
    _looks_like_chitchat,
)


class TestSlotExtraction:
    def test_order_id(self):
        assert _extract_slots("订单 O20260101001 到哪了").get("order_id") == "O20260101001"

    def test_phone(self):
        assert _extract_slots("手机号 13800138000").get("phone") == "13800138000"

    def test_error_code(self):
        assert _extract_slots("E102 报警").get("error_code") == "E102"

    def test_device_model(self):
        assert _extract_slots("XY200 坏了").get("device_model") == "XY200"

    def test_amount(self):
        assert _extract_slots("退款 500 元").get("amount") == 500.0

    def test_error_code_does_not_become_device_model(self):
        """★ 这条曾经断言的是**相反**的行为，等于把 bug 锁死在测试里。

        旧版本：
            assert _extract_slots("E102 报警").get("device_model") == "E102"
        当时 intent 节点里有一行"error_code 兜底成 device_model"，
        本意是让工单显得信息完整，实际是**凭空编造型号** ——
        用户只说了故障码，工单却有了型号，于是跳过追问，
        工程师拿着错误型号去修（真实数据：型号=E200 故障码=E200）。

        现在断言：故障码就该待在故障码槽位，型号留空由追问补齐。
        """
        slots = _extract_slots("E102 报警")
        assert slots.get("error_code") == "E102"
        assert "device_model" not in slots


class TestConsultQuery:
    def test_how_to_troubleshoot(self):
        assert _is_consult_query("E102 报警怎么排查") is True

    def test_what_cause(self):
        assert _is_consult_query("E102 报警是什么原因") is True

    def test_action_word(self):
        assert _is_consult_query("帮我报修 E102") is False

    def test_order_related(self):
        assert _is_consult_query("订单到哪了") is False

    def test_pure_alarm_code(self):
        assert _is_consult_query("E503 安全门报警") is True

    def test_with_action_and_alarm(self):
        assert _is_consult_query("帮我修一下 E503 报警") is False


class TestChitchat:
    """问候 / 致谢 / 告别必须命中 chitchat。

    回归背景：早期 pattern 的尾部只允许标点（`[！!。.？?]*$`），
    于是「你好啊」「谢谢啦」这类带语气词的输入全部未命中，
    兜底成 qa 走了 RAG，最终回给用户「知识库中没有找到相关内容」。
    """

    @pytest.mark.parametrize("text", [
        "你好", "你好啊", "你好呀", "你好哦", "您好",
        "嗨", "嗨嗨", "hello", "hi",
        "在吗", "在吗？", "在不在",
        "早上好", "早上好啊", "中午好", "下午好", "晚上好", "晚安", "哈喽",
        "你是谁", "你是谁啊", "你叫什么名字", "你能做什么", "你能做什么呢",
        "你会啥", "你能干嘛", "帮助",
        "谢谢", "谢谢啦", "多谢", "感谢",
        "再见", "拜拜", "拜拜咯",
    ])
    def test_greeting_hits_chitchat(self, text):
        top = get_intent_engine().classify_top(text)
        assert top is not None, f"{text!r} 未命中任何意图"
        assert top.id == "chitchat", f"{text!r} -> {top.id}"

    @pytest.mark.parametrize("text", [
        "你好啊", "嗨嗨", "你会啥", "谢谢啦", "拜拜咯",
    ])
    def test_fallback_recognises_chitchat(self, text):
        assert _looks_like_chitchat(text) is True

    @pytest.mark.parametrize("text", [
        "E102 报警", "怎么退货", "查订单 O20260101001", "退款 500 元",
        "手机号 13800138000", "张工在吗", "帮我报修 E102",
    ])
    def test_business_never_falls_back_to_chitchat(self, text):
        assert _looks_like_chitchat(text) is False


class TestChitchatNode:
    """chitchat_node 按语义给不同回复，且不因缺 user_input 就退化。"""

    def test_thanks(self):
        r = chitchat_node({"user_input": "谢谢啦", "messages": []})
        assert "不客气" in r["answer"]

    def test_bye(self):
        r = chitchat_node({"user_input": "拜拜咯", "messages": []})
        assert "再见" in r["answer"]

    def test_whoami(self):
        r = chitchat_node({"user_input": "你是谁啊", "messages": []})
        assert "业务助手" in r["answer"]

    def test_greeting_returns_capability(self):
        r = chitchat_node({"user_input": "你好啊", "messages": []})
        assert "我可以帮您" in r["answer"]

    def test_falls_back_to_last_user_message(self):
        """没有 user_input 时，应从 messages 里取最后一条用户消息。"""
        r = chitchat_node({"messages": [{"role": "user", "content": "谢谢你"}]})
        assert "不客气" in r["answer"]
