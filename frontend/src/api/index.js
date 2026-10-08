import axios from "axios";
import { ElMessage } from "element-plus";

const http = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

// ============================================================
// 全站错误提示（带去重）
// ------------------------------------------------------------
// 背景：响应拦截器已经统一提示过一次错误，而页面里的 catch 出于习惯
// 往往还会再提示一次，用户会看到**两条一模一样**的红条。
// 这里按「文案 + 短时间窗」去重：拦截器与页面都走 toastError，
// 同一条错误在 1.2 秒内只会弹一次。
// ============================================================
let _lastToastText = "";
let _lastToastAt = 0;

export function toastError(msg, windowMs = 1200) {
  const text = typeof msg === "string" ? msg : JSON.stringify(msg ?? "请求失败");
  const now = Date.now();
  if (text === _lastToastText && now - _lastToastAt < windowMs) {
    _lastToastAt = now;
    return;
  }
  _lastToastText = text;
  _lastToastAt = now;
  ElMessage.error(text);
}

http.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("auth_token");
    if (token) {
      config.headers = config.headers || {};
      config.headers["Authorization"] = "Bearer " + token;
    }
    return config;
  },
  (err) => Promise.reject(err)
);

http.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const status = err.response && err.response.status;
    if (status === 401 && !location.pathname.startsWith("/login")) {
      localStorage.removeItem("auth_token");
      localStorage.removeItem("auth_user");
      window.location.href = "/login";
      toastError("登录已过期，请重新登录");
      return Promise.reject(err);
    }
    const msg = (err.response && err.response.data && err.response.data.detail) || err.message || "请求失败";
    toastError(msg);
    return Promise.reject(err);
  }
);

export default {
  // ---------- 通用请求 ----------
  // 供页面直接调用。务必用这一个，不要在各页面里 axios.create() ——
  // 自建实例不会带上 Authorization 头，后端会返回 401「未登录」。
  get: (url, config) => http.get(url, config),
  post: (url, data, config) => http.post(url, data, config),
  put: (url, data, config) => http.put(url, data, config),
  patch: (url, data, config) => http.patch(url, data, config),
  delete: (url, config) => http.delete(url, config),

  login: (data) => http.post("/auth/login", data),

  // ---------- 飞书 ----------
  getFeishuStatus: () => http.get("/feishu/status"),
  getFeishuConfig: () => http.get("/feishu/config"),
  updateFeishuWebhook: (data) => http.put("/feishu/webhook", data),
  testFeishuPrivate: (data) => http.post("/feishu/test-private", data),
  testFeishuDispatch: (data) => http.post("/feishu/test-dispatch", data),
  sendFeishuRaw: (data) => http.post("/feishu/send", data),

  getApprovalsPending: () => http.get("/approvals/pending"),
  getApprovalStats: () => http.get("/approvals/stats"),
  batchApproveRefunds: (data) => http.post("/approvals/refunds/batch", data),
  reassignOverdueTicket: (id) => http.post(`/approvals/tickets/${id}/reassign`),
  me: () => http.get("/auth/me"),
  logout: () => http.post("/auth/logout"),
  changePassword: (data) => http.post("/auth/change-password", data),

  getDocs: () => http.get("/knowledge/docs"),
  getDocDetail: (id) => http.get("/knowledge/docs/" + id + "/detail"),
  deleteDoc: (id) => http.delete("/knowledge/docs/" + id),
  ingestText: (data) => http.post("/knowledge/ingest", data),
  ingestFile: (formData) => http.post("/knowledge/ingest-file", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  }),
  kbStats: () => http.get("/knowledge/stats"),

  chat: (data) => http.post("/chat", data),

  listTickets: (params) => http.get("/tickets", { params }),
  getTicket: (id) => http.get("/tickets/" + id),
  updateTicket: (id, data) => http.patch("/tickets/" + id, data),
  acceptTicket: (id) => http.post("/tickets/" + id + "/accept"),
  rejectTicket: (id, data) => http.post("/tickets/" + id + "/reject", data),
  startTicket: (id) => http.post("/tickets/" + id + "/start"),
  resolveTicket: (id, data) => http.post("/tickets/" + id + "/resolve", data),
  closeTicket: (id) => http.post("/tickets/" + id + "/close"),
  ticketAudit: (id) => http.get("/tickets/" + id + "/audit"),
  ticketNotifications: (id) => http.get("/tickets/" + id + "/notifications"),

  listEngineers: () => http.get("/engineers"),
  createEngineer: (data) => http.post("/engineers", data),
  updateEngineer: (id, data) => http.put("/engineers/" + id, data),
  deleteEngineer: (id) => http.delete("/engineers/" + id),
  toggleEngineer: (id) => http.post("/engineers/" + id + "/toggle-status"),

  getUsersMeta: () => http.get("/users/meta"),
  listUsers: (params) => http.get("/users", { params }),
  getUser: (id) => http.get("/users/" + id),
  createUser: (data) => http.post("/users", data),
  updateUser: (id, data) => http.put("/users/" + id, data),
  deleteUser: (id) => http.delete("/users/" + id),
  resetUserPassword: (id, data) => http.post("/users/" + id + "/reset-password", data),
  toggleUserStatus: (id) => http.post("/users/" + id + "/toggle-status"),
  getUserPermissions: (id) => http.get("/users/" + id + "/permissions"),
  checkUserPermission: (id, perm) => http.post("/users/" + id + "/check", { perm }),
  resolveUserOpenId: (id, data) => http.post("/users/" + id + "/resolve-open-id", data),

  // ---------- 待绑定飞书账号 ----------
  getPendingBindings: () => http.get("/users/pending-bindings"),
  bindPending: (data) => http.post("/users/pending-bindings/bind", data),
  recordPending: (data) => http.post("/users/pending-bindings/record", data),
  dismissPending: (openId) => http.delete("/users/pending-bindings/" + openId),

  listAudit: (params) => http.get("/logs/audit", { params }),
  listNotifications: (params) => http.get("/logs/notifications", { params }),
  auditStats: () => http.get("/logs/audit/stats"),
  notifStats: () => http.get("/logs/notifications/stats"),

  getSlaSummary: () => http.get("/sla/summary"),
  getSlaRules: () => http.get("/sla/rules"),
  getSlaTickets: () => http.get("/sla/tickets"),
  scanSla: () => http.post("/sla/scan"),
  updateSlaRules: (data) => http.put("/sla/rules", data),

  getConfig: () => http.get("/admin/config"),
  saveConfig: (data) => http.post("/admin/config", data),
  reloadConfig: () => http.post("/admin/reload"),
  providerTemplates: () => http.get("/admin/provider-templates"),
  dashboardStats: () => http.get("/admin/dashboard/stats"),

  uploadCustomers: (formData) => http.post("/customers/import/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  }),
  confirmImportCustomers: (data) => http.post("/customers/import/confirm", data),
};
