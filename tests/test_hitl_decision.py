"""HITL 决策解析测试。

背景（真实故障）
线上用户在飞书里长按消息选「回复」再发「同意」，期望触发断点恢复，
实际却得到「知识库中没有找到相关内容」—— 因为网关可能把内容传成
「回复 何雨轩: 同意」，而按前缀匹配的解析函数认不出来，
于是这句话被当成全新问题重跑了一遍 AI（走到 RAG → 无结果）。

这里把各种写法都固化成用例，避免回归。
"""
import pytest


@pytest.fixture(scope="module")
def parse():
    from app.api.routes.openai_compat import _parse_decision
    return _parse_decision


class TestApprove:
    @pytest.mark.parametrize("text", [
        "同意", "确认", "批准", "通过", "可以", "好的", "ok", "OK", "yes",
        "同意。", "同意！", "好的，同意", "同意，就按你说的办",
        " 同意 ", "确认一下",
    ])
    def test_approve(self, parse, text):
        assert parse(text) == "approve", text


class TestReject:
    @pytest.mark.parametrize("text", [
        "拒绝", "驳回", "不同意", "不可以", "不行", "取消", "否",
        "reject", "no", "拒绝。", "不同意，再改改",
    ])
    def test_reject(self, parse, text):
        got = parse(text)
        assert got and got.startswith("block_revise"), (text, got)


class TestQuotePrefix:
    """飞书「引用回复」会带上前缀，必须能剥掉。"""

    @pytest.mark.parametrize("text", [
        "回复 何雨轩: 同意",
        "回复 何雨轩：同意",
        "回复 张三:同意",
        "引用 某人: 同意",
    ])
    def test_quote_approve(self, parse, text):
        assert parse(text) == "approve", text

    @pytest.mark.parametrize("text", [
        "回复 何雨轩: 拒绝",
        "回复 何雨轩：不同意",
    ])
    def test_quote_reject(self, parse, text):
        got = parse(text)
        assert got and got.startswith("block_revise"), (text, got)


class TestNotADecision:
    """普通业务问题绝不能被误判成决策，否则会吞掉用户的正常提问。"""

    @pytest.mark.parametrize("text", [
        "", "   ", "你好啊", "我要退款", "今天天气不错",
        "帮我报修 XY200 故障码 E102", "转人工", "查询订单 O20240001",
        "退款大概要多久才能到账呢",
    ])
    def test_none(self, parse, text):
        assert parse(text) is None, text

    def test_negation_not_treated_as_approve(self, parse):
        """「不可以」里包含「可以」，必须先判否定。"""
        got = parse("不可以")
        assert got and got.startswith("block_revise"), got
