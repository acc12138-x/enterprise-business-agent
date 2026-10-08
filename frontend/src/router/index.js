import { createRouter, createWebHistory } from "vue-router";
import MainLayout from "../layouts/MainLayout.vue";

const routes = [
  {
    path: "/login",
    name: "登录",
    component: () => import("../views/Login.vue"),
    meta: { public: true },
  },
  {
    path: "/",
    component: MainLayout,
    redirect: "/chat",
    children: [
      { path: "chat",          name: "对话测试",  component: () => import("../views/Chat.vue") },
      { path: "knowledge",     name: "知识库",    component: () => import("../views/Knowledge.vue") },
      { path: "tickets",       name: "工单",      component: () => import("../views/Tickets.vue") },
      { path: "customers",     name: "客户资产",  component: () => import("../views/Customers.vue") },
      { path: "leads",         name: "线索管理",  component: () => import("../views/Leads.vue") },
      { path: "handoffs",      name: "人工客服台", component: () => import("../views/Handoffs.vue") },
      { path: "refunds",       name: "退款管理",  component: () => import("../views/Refunds.vue") },
      { path: "sla",           name: "SLA时效",   component: () => import("../views/SLA.vue") },
      { path: "engineers",     name: "工程师",    component: () => import("../views/Engineers.vue") },
      { path: "people",        name: "人员管理",  component: () => import("../views/People.vue") },
      { path: "approvals",     name: "审批台",    component: () => import("../views/Approvals.vue") },
      { path: "feishu",        name: "飞书配置",  component: () => import("../views/FeishuConfig.vue") },
      { path: "audit",         name: "审计日志",  component: () => import("../views/Audit.vue") },
      { path: "notifications", name: "通知记录",  component: () => import("../views/Notifications.vue") },
      { path: "config",        name: "系统配置",  component: () => import("../views/Config.vue") },
      { path: "monitor",       name: "监控看板",  component: () => import("../views/Monitor.vue") },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem("auth_token");
  if (to.meta && to.meta.public) {
    if (token && to.path === "/login") return next("/chat");
    return next();
  }
  if (!token) {
    return next("/login");
  }
  next();
});

export default router;
