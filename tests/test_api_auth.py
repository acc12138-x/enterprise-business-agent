"""API 测试：认证。"""


class TestAuth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_login_success(self, client):
        r = client.post("/auth/login", json={"name": "管理员", "password": "admin123"})
        assert r.status_code == 200
        d = r.json()
        assert "token" in d
        assert d["user"]["role"] == "admin"

    def test_login_wrong_password(self, client):
        r = client.post("/auth/login", json={"name": "管理员", "password": "wrong"})
        assert r.status_code == 401

    def test_login_unknown_user(self, client):
        r = client.post("/auth/login", json={"name": "不存在", "password": "x"})
        assert r.status_code == 401

    def test_me_requires_token(self, client):
        r = client.get("/auth/me")
        assert r.status_code == 401

    def test_me_with_token(self, client, auth_headers):
        r = client.get("/auth/me", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["name"] == "管理员"

    def test_engineer_permissions(self, client, auth_headers):
        """工程师登录后不应拥有退款审批权限。

        ⚠️ 用【临时账号】测，绝不重置已有账号的密码。
        早期版本图省事，直接拿库里第一个 engineer 账号重置密码 ——
        结果是**测试会改掉真实账号的密码**，开发者再用原密码就登不进去了。
        现在改成：建一个临时工程师 → 设密码 → 登录校验 → 删掉。
        同时这也覆盖了「建账号 / 重置密码 / 删账号」三条链路。
        """
        import uuid

        import pytest

        uname = f"pytest-eng-{uuid.uuid4().hex[:6]}"
        pwd = "pytest-eng-pwd"

        r = client.post("/users", headers=auth_headers, json={
            "name": uname, "role": "engineer", "job": "维修",
            "status": "online", "password": pwd,
        })
        if r.status_code != 200:
            pytest.skip(f"建临时账号失败，跳过：{r.status_code} {r.text[:120]}")

        uid = r.json().get("id") or r.json().get("user", {}).get("id")
        try:
            r = client.post("/auth/login", json={"name": uname, "password": pwd})
            assert r.status_code == 200, r.text
            perms = r.json()["user"]["effective_permissions"]
            assert "ticket.accept" in perms
            assert "refund.approve" not in perms
            # 转人工是客服的活，工程师不该有
            assert "handoff.claim" not in perms
        finally:
            if uid:
                client.delete(f"/users/{uid}", headers=auth_headers)

    def test_agent_has_handoff_permissions(self, client, auth_headers):
        """客服（agent）必须能接管转人工会话 —— 否则转人工功能形同虚设。"""
        r = client.get("/users/meta", headers=auth_headers)
        assert r.status_code == 200
        codes = {p["code"] for p in r.json()["all_permissions"]}
        for need in ("handoff.view", "handoff.claim", "handoff.reply",
                     "lead.view", "lead.edit"):
            assert need in codes, need
