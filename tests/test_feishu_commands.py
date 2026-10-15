"""飞书指令解析测试（工单操作）。

背景（真实反馈）
「工单创建后，工程师接单后无法处理这个订单，处理中状态无法有效使用」

排查结论：功能**其实早就实现完整**（接单/开始处理/解决/关闭/拒单都在），
但没有坏在代码上，而是坏在两个「人找不到」的地方：

  1. 「处理 <单号>」原本映射到 accept 而不是 start。
     工程师说「处理 XXX」想表达的是"开始干活"，结果在 accepted 状态下
     报「不允许从 accepted 流转到 accepted」，看起来就像功能坏了。
  2. 没有任何地方告诉用户这些指令存在 —— 「我的工单」只列一串单号，
     不提示下一步能发什么，能力介绍里也没提。

这里把「指令能被正确解析」和「引导文案存在」都固化下来。
"""
import pytest


@pytest.fixture(scope="module")
def oc():
    from app.api.routes import openai_compat
    return openai_compat


def _parse(oc, text):
    """返回 (动作 key 或 None, 单号 或 None)。"""
    m = oc._CMD_RE.match(text)
    if not m:
        return None, None
    return oc._ACTION_MAP.get(m.group("action")), m.group("ticket")


class TestActionParsing:
    """指令 → 动作 key 的映射。"""

    def test_处理_maps_to_start_not_accept(self, oc):
        """★ 回归重点：这是那次『处理中进不去』的直接原因。"""
        assert oc._ACTION_MAP["处理"] == "start"

    @pytest.mark.parametrize("text,expect", [
        ("接单 TBF792FD4", "accept"),
        ("接受 TBF792FD4", "accept"),
        ("受理 TBF792FD4", "accept"),
        ("开始处理 TBF792FD4", "start"),
        ("开始 TBF792FD4", "start"),
        ("处理 TBF792FD4", "start"),
        ("解决 TBF792FD4", "resolve"),
        ("完成 TBF792FD4", "resolve"),
        ("标记解决 TBF792FD4", "resolve"),
        ("关闭 TBF792FD4", "close"),
        ("关闭工单 TBF792FD4", "close"),
        ("拒单 TBF792FD4", "reject"),
        ("改派 TBF792FD4 朱一", "reassign"),
    ])
    def test_commands_parse(self, oc, text, expect):
        action, ticket = _parse(oc, text)
        assert action == expect, f"{text} → {action}"
        assert ticket == "TBF792FD4"

    @pytest.mark.parametrize("text", [
        "@业务助手 接单 TBF792FD4",
        "业务助手 开始处理 TBF792FD4",
        " 解决 TBF792FD4 ",
    ])
    def test_tolerates_mention_and_spaces(self, oc, text):
        action, ticket = _parse(oc, text)
        assert action == "resolve" or action is not None, text
        assert ticket == "TBF792FD4"

    def test_resolve_keeps_note(self, oc):
        m = oc._CMD_RE.match("解决 TBF792FD4 已更换主板")
        assert m.group("extra") == "已更换主板"

    @pytest.mark.parametrize("text", ["你好啊", "我要退款", "转人工", "接单", "处理"])
    def test_non_commands_are_not_actions(self, oc, text):
        """普通对话和缺单号的指令不能被当成动作指令，否则会误改数据。"""
        action, _ = _parse(oc, text)
        assert action is None, f"{text} 被误判为 {action}"


class TestHelpCommand:
    """没有这张清单，工程师根本不知道能操作工单。"""

    @pytest.mark.parametrize("text", ["命令", "指令", "帮助", "help", "怎么用", "功能"])
    def test_help_parses(self, oc, text):
        assert oc._CMD_HELP_RE.match(text), text

    def test_help_not_treated_as_action(self, oc):
        assert oc._CMD_RE.match("命令") is None

    def test_help_is_role_aware_for_engineer(self, oc):
        """工程师不该看到退款审批那节（他没这个权限）。"""
        eng = {"effective_permissions": ["ticket.view", "ticket.accept",
                                         "ticket.reject", "ticket.resolve"]}
        txt = oc._help_text(eng)
        assert "处理工单" in txt
        assert "开始处理" in txt
        assert "退款审批" not in txt

    def test_help_is_role_aware_for_supervisor(self, oc):
        sup = {"effective_permissions": ["ticket.view", "refund.approve", "*"]}
        txt = oc._help_text(sup)
        assert "退款审批" in txt
        assert "待审批" in txt

    def test_help_defaults_to_all_for_unknown_user(self, oc):
        txt = oc._help_text({})
        assert "处理工单" in txt and "退款审批" in txt


class TestNextActionHint:
    """「我的工单」要提示下一步，否则用户只看到一串单号。"""

    @pytest.mark.parametrize("status,cmd", [
        ("assigned", "接单"),
        ("accepted", "开始处理"),
        ("in_progress", "解决"),
        ("resolved", "关闭"),
    ])
    def test_actionable_statuses_have_hint(self, oc, status, cmd):
        label, got = oc._NEXT_ACTION_HINT[status]
        assert got == cmd, f"{status} 提示 {got}，应为 {cmd}"

    @pytest.mark.parametrize("status", ["pending", "rejected"])
    def test_non_actionable_statuses_have_no_command(self, oc, status):
        """这些状态没有可执行动作，不该乱给指令。"""
        label, cmd = oc._NEXT_ACTION_HINT[status]
        assert cmd is None

    def test_every_status_has_an_entry(self, oc):
        """新增状态时别忘了补提示，否则界面上会静默少一行引导。"""
        from app.db.models.ticket import STATUS_FLOW
        missing = set(STATUS_FLOW) - set(oc._NEXT_ACTION_HINT)
        assert not missing, f"这些状态缺少下一步提示：{missing}"
