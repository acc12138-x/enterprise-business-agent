"""会话线程映射测试。

背景（两个线上真实故障）
1. 没有 session_key 时 `_thread_id` 返回 `uuid4().hex[:8]` —— **随机值**。
   于是每次请求都变成全新会话，多轮上下文与 HITL 断点全部丢失。
   现象：用户在飞书回「同意」确认转人工，却收到
   「知识库中没有找到相关内容」（那句被当成新问题重跑了 AI）。

2. `session_key[:20]` 直接截断。session_key 的常见形式是
   `agent:main:feishu:group:<chat_id>`，前 20 字符全是固定前缀，
   **所有群聊会共用一个线程**（跨会话串台）。
"""
import pytest


@pytest.fixture(autouse=True)
def _clean_maps():
    """每个用例前清空映射，避免相互影响。"""
    from app.api.routes import openai_compat as oc
    oc._session_map.clear()
    oc._tid_owner.clear()
    yield
    oc._session_map.clear()
    oc._tid_owner.clear()


@pytest.fixture
def thread_id():
    from app.api.routes.openai_compat import _thread_id
    return _thread_id


class TestStability:
    """同一个会话标识必须始终映射到同一个线程。"""

    def test_same_key_is_stable(self, thread_id):
        k = "oc_b96741016f9871ec34ea29974e789abc"
        assert thread_id(k) == thread_id(k)

    def test_group_and_direct_are_different(self, thread_id):
        g = thread_id("oc_b96741016f9871ec34ea29974e789abc")
        d = thread_id("ou_0c0a3491384fddd86f44308c83f25573")
        assert g != d


class TestMissingSessionKey:
    """★ 回归重点：缺 session_key 时绝不能随机。"""

    def test_falls_back_to_sender(self, thread_id):
        """同一个发送者的连续消息必须落在同一线程，否则多轮上下文会丢。"""
        sender = "ou_0c0a3491384fddd86f44308c83f25573"
        assert thread_id(None, sender) == thread_id("", sender)
        assert thread_id(None, sender) == thread_id(None, sender)

    def test_different_senders_do_not_merge(self, thread_id):
        """不同发送者不能共用一个线程，否则会串台。"""
        assert thread_id(None, "ou_AAA") != thread_id(None, "ou_BBB")

    def test_sender_key_is_marked(self, thread_id):
        """退化为发送者聚合时，id 要能看出是这条路径来的，便于排查。"""
        tid = thread_id(None, "ou_AAA")
        assert "sender:" in tid

    def test_no_identifier_at_all_still_returns_something(self, thread_id):
        """实在没有任何标识时也要能返回（随机），不能抛异常。"""
        tid = thread_id(None, "")
        assert tid.startswith("openclaw-")


class TestTruncationCollision:
    """★ 回归重点：截断后不能撞车。"""

    def test_full_session_names_do_not_collide(self, thread_id):
        """两个不同的群必须映射到不同线程。

        修复前二者都是 `openclaw-agent:main:feishu:gr`，会互相污染会话。
        """
        a = thread_id("agent:main:feishu:group:oc_AAAA")
        b = thread_id("agent:main:feishu:group:oc_BBBB")
        assert a != b, f"截断碰撞未修复：{a} == {b}"

    def test_collision_falls_back_to_hash(self, thread_id):
        from app.api.routes import openai_compat as oc
        a = thread_id("agent:main:feishu:group:oc_AAAA")
        b = thread_id("agent:main:feishu:group:oc_BBBB")
        # 先来的沿用可读 id，后来的改用哈希
        assert a == "openclaw-agent:main:feishu:gr"
        assert len(b) == len("openclaw-") + 16
        assert oc._tid_owner[a] != oc._tid_owner[b]

    def test_short_keys_keep_readable_id(self, thread_id):
        """普通 chat_id 不碰撞时，仍用可读的截断 id（不改变既有会话）。"""
        assert thread_id("oc_b96741016f9871ec34ea29974e789abc") == \
            "openclaw-oc_b96741016f9871ec3"
