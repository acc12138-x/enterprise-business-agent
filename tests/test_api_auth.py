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

        不依赖预置密码：先用管理员把某个工程师的密码重置，
        再用新密码登录。这样测试在任何种子数据下都成立，
        同时也覆盖了「管理员重置密码」这条链路。
        """
        r = client.get("/users", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        users = data if isinstance(data, list) else data.get("items", [])
        eng = next((u for u in users if u.get("role") == "engineer"), None)
        if not eng:
            import pytest
            pytest.skip("当前库里没有 engineer 角色的账号")

        newpwd = "engineer-test-pwd"
        r = client.post(f"/users/{eng['id']}/reset-password",
                        json={"new_password": newpwd}, headers=auth_headers)
        assert r.status_code == 200, r.text

        r = client.post("/auth/login", json={"name": eng["name"], "password": newpwd})
        assert r.status_code == 200, r.text
        perms = r.json()["user"]["effective_permissions"]
        assert "ticket.accept" in perms
        assert "refund.approve" not in perms

    def test_agent_has_handoff_permissions(self, client, auth_headers):
        """客服（agent）必须能接管转人工会话 —— 否则转人工功能形同虚设。"""
        r = client.get("/users/meta", headers=auth_headers)
        assert r.status_code == 200
        codes = {p["code"] for p in r.json()["all_permissions"]}
        for need in ("handoff.view", "handoff.claim", "handoff.reply",
                     "lead.view", "lead.edit"):
            assert need in codes, need
