"""API 测试：工单等。

注意：自 全站接口鉴权覆盖 起，这些接口都需要登录态，
所以统一带上 auth_headers。
"""


class TestTickets:
    def test_list_tickets(self, client, auth_headers):
        r = client.get("/tickets", headers=auth_headers)
        assert r.status_code == 200
        assert "items" in r.json()

    def test_users_meta(self, client, auth_headers):
        r = client.get("/users/meta", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert "roles" in d
        assert "all_permissions" in d
        assert len(d["roles"]) >= 4
        assert len(d["all_permissions"]) >= 15

    def test_sla_summary(self, client, auth_headers):
        r = client.get("/sla/summary", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        for k in ["total", "normal", "warning", "overdue"]:
            assert k in d

    def test_sla_rules(self, client, auth_headers):
        r = client.get("/sla/rules", headers=auth_headers)
        assert r.status_code == 200

    def test_requires_auth(self, client):
        """未登录访问受保护接口必须是 401。"""
        for path in ("/tickets", "/users/meta", "/sla/summary", "/sla/rules"):
            assert client.get(path).status_code == 401, path
