"""API 测试：客户线索 + 转人工客服。

覆盖
  · 权限：未登录必须 401
  · 线索：新建（含自动分配）→ 阶段流转 → 报价 → 成交 → 漏斗统计
  · 阶段状态机：非法流转被拒、流失必须填原因
  · 跟进记录：阶段/报价变化自动留痕
  · 转人工：建单 → 认领 → 回复 → 结束；重复认领被拒
"""
import pytest


def _users(client, headers):
    r = client.get("/users", headers=headers)
    assert r.status_code == 200
    data = r.json()
    return data if isinstance(data, list) else data.get("items", [])


class TestLeadAuth:
    def test_requires_auth(self, client):
        for path in ("/leads", "/leads/stats", "/leads/pool"):
            assert client.get(path).status_code == 401, path


class TestLeads:
    def test_stats_shape(self, client, auth_headers):
        r = client.get("/leads/stats", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        for k in ("total", "by_stage", "won", "lost", "conversion_rate",
                  "deal_sum", "owners"):
            assert k in d, k
        # 六个阶段一个都不能少
        assert set(d["by_stage"]) == {"new", "contacted", "quoted",
                                      "negotiating", "won", "lost"}

    def test_pool_shape(self, client, auth_headers):
        r = client.get("/leads/pool", headers=auth_headers)
        assert r.status_code == 200
        assert "pool" in r.json()

    def test_create_and_autostage(self, client, auth_headers):
        r = client.post("/leads", headers=auth_headers, json={
            "name": "测试线索公司", "phone": "13700009999",
            "company": "测试科技有限公司", "source": "feishu",
            "need_desc": "想采购 50 台设备",
        })
        assert r.status_code == 200, r.text
        res = r.json()
        lid = res["lead_id"]
        assert lid.startswith("LD-")
        # 未指定负责人时应当自动分配
        assert res["owner_id"], "应该自动分配负责人"

        # 详情
        r = client.get(f"/leads/{lid}", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert d["stage"] == "new"
        assert d["quoted"] is False
        assert d["won"] is False
        assert len(d["followups"]) >= 1, "创建时应自动写一条跟进"

        # 非法流转：new 不能直接到 won
        r = client.patch(f"/leads/{lid}", headers=auth_headers, json={"stage": "won"})
        assert r.status_code == 400
        assert "不能从" in r.json()["detail"]

        # 正常流转 + 报价
        assert client.patch(f"/leads/{lid}", headers=auth_headers,
                            json={"stage": "contacted"}).status_code == 200
        r = client.patch(f"/leads/{lid}", headers=auth_headers,
                         json={"quote_amount": 80000})
        assert r.status_code == 200
        assert r.json()["lead"]["quoted"] is True
        assert r.json()["lead"]["quote_amount"] == 80000

        # 成交
        r = client.patch(f"/leads/{lid}", headers=auth_headers,
                         json={"stage": "won", "deal_amount": 76000})
        assert r.status_code == 200
        d = r.json()["lead"]
        assert d["won"] is True
        assert d["deal_amount"] == 76000

        # 自动留痕：创建 / 阶段 / 报价 / 成交
        r = client.get(f"/leads/{lid}", headers=auth_headers)
        fu = r.json()["followups"]
        assert len(fu) >= 4, f"跟进记录应有 4 条以上，实际 {len(fu)}"
        joined = " ".join(f["content"] for f in fu)
        assert "报价" in joined
        assert "已成交" in joined

    def test_lost_requires_reason(self, client, auth_headers):
        r = client.post("/leads", headers=auth_headers,
                        json={"name": "流失测试", "phone": "13700008888"})
        lid = r.json()["lead_id"]

        r = client.patch(f"/leads/{lid}", headers=auth_headers, json={"stage": "lost"})
        assert r.status_code == 400
        assert "流失原因" in r.json()["detail"]

        r = client.patch(f"/leads/{lid}", headers=auth_headers,
                         json={"stage": "lost", "lost_reason": "预算不足"})
        assert r.status_code == 200
        assert r.json()["lead"]["lost_reason"] == "预算不足"

    def test_manual_followup(self, client, auth_headers):
        r = client.post("/leads", headers=auth_headers,
                        json={"name": "跟进测试", "phone": "13700007777"})
        lid = r.json()["lead_id"]
        r = client.post(f"/leads/{lid}/followups", headers=auth_headers,
                        json={"content": "电话沟通，客户要求下周给方案",
                              "channel": "phone"})
        assert r.status_code == 200
        r = client.get(f"/leads/{lid}", headers=auth_headers)
        contents = [f["content"] for f in r.json()["followups"]]
        assert any("下周给方案" in c for c in contents)

    def test_not_found(self, client, auth_headers):
        assert client.get("/leads/LD-19700101-9999",
                          headers=auth_headers).status_code == 404

    def test_filter_by_stage(self, client, auth_headers):
        r = client.get("/leads?stage=won&limit=5", headers=auth_headers)
        assert r.status_code == 200
        assert all(x["stage"] == "won" for x in r.json())


class TestHandoffAuth:
    def test_requires_auth(self, client):
        assert client.get("/handoffs").status_code == 401
        assert client.get("/handoffs/stats").status_code == 401


class TestHandoffs:
    def test_stats_shape(self, client, auth_headers):
        r = client.get("/handoffs/stats", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        for k in ("total", "by_status", "avg_claim_seconds", "per_agent"):
            assert k in d, k

    def test_full_flow(self, client, auth_headers):
        tid = "pytest-handoff-thread-1"
        # 建单
        r = client.post("/handoffs", headers=auth_headers,
                        json={"thread_id": tid, "reason": "pytest 测试",
                              "sender_open_id": ""})
        assert r.status_code == 200, r.text
        hid = r.json()["handoff_id"]
        assert hid.startswith("HT-")

        # 幂等：同一 thread 再建应复用
        r2 = client.post("/handoffs", headers=auth_headers,
                         json={"thread_id": tid, "reason": "重复"})
        assert r2.json()["handoff_id"] == hid

        # 详情
        r = client.get(f"/handoffs/{hid}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "queued"
        assert "messages" in r.json()

        # 认领
        r = client.post(f"/handoffs/{hid}/claim", headers=auth_headers)
        assert r.status_code == 200, r.text
        r = client.get(f"/handoffs/{hid}", headers=auth_headers)
        assert r.json()["status"] == "claimed"

        # 回复（用户 open_id 为空 → 发送失败，但消息要落库）
        r = client.post(f"/handoffs/{hid}/reply", headers=auth_headers,
                        json={"text": "您好，这里是人工客服。"})
        assert r.status_code == 200
        assert "sent" in r.json()

        r = client.get(f"/handoffs/{hid}", headers=auth_headers)
        roles = [m["role"] for m in r.json()["messages"]]
        assert "human_agent" in roles, "坐席回复必须落库"

        # 结束
        r = client.post(f"/handoffs/{hid}/close", headers=auth_headers,
                        json={"note": "pytest 已处理"})
        assert r.status_code == 200
        r = client.get(f"/handoffs/{hid}", headers=auth_headers)
        assert r.json()["status"] == "closed"
        # 结束后应插入一条系统提示，告诉用户已转回 AI
        sys_msgs = [m for m in r.json()["messages"] if m["role"] == "system"]
        assert any("转回智能助手" in m["content"] for m in sys_msgs)

    def test_reply_before_claim_is_rejected(self, client, auth_headers):
        r = client.post("/handoffs", headers=auth_headers,
                        json={"thread_id": "pytest-handoff-thread-2",
                              "reason": "未认领直接回复"})
        hid = r.json()["handoff_id"]
        r = client.post(f"/handoffs/{hid}/reply", headers=auth_headers,
                        json={"text": "没认领就想回复"})
        assert r.status_code == 400
        assert "认领" in r.json()["detail"]

    def test_not_found(self, client, auth_headers):
        assert client.get("/handoffs/HT-19700101-9999",
                          headers=auth_headers).status_code == 404

    def test_list_filter(self, client, auth_headers):
        r = client.get("/handoffs?status=closed&limit=5", headers=auth_headers)
        assert r.status_code == 200
        assert all(x["status"] == "closed" for x in r.json())


class TestHandoffServiceMuting:
    """AI 静默是转人工功能的关键：坐席接管期间绝不能跑工作流。"""

    def test_ai_muted_only_when_claimed(self):
        from app.services import handoff_service as hs

        tid = "pytest-mute-thread"
        assert hs.is_ai_muted(tid) is False

        h = hs.create_handoff(tid, sender_open_id="", reason="静默测试")
        hid = h["handoff_id"]
        # queued 阶段不静默
        assert hs.is_ai_muted(tid) is False

        hs.claim(hid, {"id": 999777, "name": "pytest 坐席"})
        assert hs.is_ai_muted(tid) is True
        assert hs.before_turn(tid, "在吗") is not None

        hs.close(hid, {"id": 999777, "name": "pytest 坐席"})
        assert hs.is_ai_muted(tid) is False
        assert hs.before_turn(tid, "在吗") is None


class TestMutedReplyMode:
    """坐席接管期间 AI 回不回话。

    真实反馈：用户被接管后每发一条都收到同一句「人工客服正在为您服务」，
    体验很差 —— 坐席认领时已经私聊告知过用户了，之后纯属噪音。
    默认改成 never（完全不回），用户在等的是坐席的答复。
    """

    @staticmethod
    def _set_mode(monkeypatch, mode):
        from app.config.settings import get_settings
        monkeypatch.setenv("HANDOFF_MUTED_MODE", mode)
        get_settings.cache_clear()

    def test_never_keeps_silent(self, monkeypatch):
        from app.services import handoff_service as hs
        self._set_mode(monkeypatch, "never")
        assert hs.muted_reply({"claimed_by_name": "小周"}) == ""
        assert hs.muted_reply({"claimed_by_name": "小周", "_notified": True}) == ""

    def test_first_replies_only_once(self, monkeypatch):
        from app.services import handoff_service as hs
        self._set_mode(monkeypatch, "first")
        assert hs.muted_reply({"claimed_by_name": "小周", "_notified": False}) != ""
        assert hs.muted_reply({"claimed_by_name": "小周", "_notified": True}) == ""

    def test_always_is_legacy_behaviour(self, monkeypatch):
        from app.services import handoff_service as hs
        self._set_mode(monkeypatch, "always")
        assert hs.muted_reply({"claimed_by_name": "小周", "_notified": True}) != ""

    def test_empty_handoff_is_silent(self, monkeypatch):
        from app.services import handoff_service as hs
        self._set_mode(monkeypatch, "always")
        assert hs.muted_reply(None) == ""

    def test_unknown_mode_falls_back_to_silent(self, monkeypatch):
        from app.services import handoff_service as hs
        self._set_mode(monkeypatch, "随便写的")
        assert hs.muted_reply({"claimed_by_name": "小周"}) == ""

    def test_before_turn_injects_notified_flag(self, monkeypatch):
        """before_turn 必须带上 _notified，否则 first 模式永远只回一次就哑了。"""
        from app.services import handoff_service as hs

        tid = "pytest-muted-flag-thread"
        h = hs.create_handoff(tid, sender_open_id="", reason="标记测试")
        hs.claim(h["handoff_id"], {"id": 999778, "name": "pytest 坐席"})
        got = hs.before_turn(tid, "第一条")
        assert got is not None and "_notified" in got
        hs.close(h["handoff_id"], {"id": 999778, "name": "pytest 坐席"})
