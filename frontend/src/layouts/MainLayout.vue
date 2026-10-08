<template>
  <el-container style="height: 100vh;">
    <el-aside width="224px" class="sidebar">
      <div class="logo">
        <el-icon :size="26"><Tools /></el-icon>
        <div>
          <div class="logo-title">业务助手</div>
          <div class="logo-sub">Business Agent</div>
        </div>
      </div>

      <div class="menu-wrap">
        <el-menu :default-active="$route.path" router class="menu">
          <el-menu-item-group v-for="group in visibleGroups" :key="group.key">
            <template #title>
              <span class="group-title">
                <el-icon :size="12"><component :is="group.icon" /></el-icon>
                <span>{{ group.label }}</span>
              </span>
            </template>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </el-menu-item>
          </el-menu-item-group>
        </el-menu>
      </div>

      <div class="footer">
        <div v-if="user" class="user-info">
          <el-avatar :size="28" :style="{ background: roleColor(user.role) }">
            {{ (user.name || "?").charAt(0) }}
          </el-avatar>
          <div class="user-detail">
            <div class="user-name">{{ user.name }}</div>
            <div class="user-role">{{ user.role_label }}</div>
          </div>
          <el-button size="small" link title="修改密码" @click="openPwdDialog">
            <el-icon><Key /></el-icon>
          </el-button>
          <el-button size="small" link title="退出" @click="doLogout">
            <el-icon><SwitchButton /></el-icon>
          </el-button>
        </div>
      </div>
    </el-aside>

    <!-- 修改自己的密码（任何账号都有） -->
    <el-dialog v-model="pwdDialog" title="修改密码" width="420px" append-to-body>
      <!-- label-width 要放得下最长的标签「确认新密码」（5 个汉字），
           否则会被折成「确认新密 / 码」，与其它行错位 -->
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="104px">
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="pwdForm.old_password" type="password" show-password
                    autocomplete="current-password" placeholder="当前使用的密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password
                    autocomplete="new-password" placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="pwdForm.confirm" type="password" show-password
                    autocomplete="new-password" @keyup.enter="doChangePwd" />
        </el-form-item>
      </el-form>
      <div class="pwd-tip">修改成功后当前登录会失效，需要用新密码重新登录。</div>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="pwdLoading" @click="doChangePwd">确定</el-button>
      </template>
    </el-dialog>

    <el-container>
      <el-header class="topbar">
        <span class="route-title">{{ $route.name }}</span>
        <span class="route-group" v-if="currentGroup">· {{ currentGroup.label }}</span>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox, ElMessage } from "element-plus";
import {
  Tools, ChatDotRound, Collection, Tickets, UserFilled, Money, CircleCheck,
  AlarmClock, User, Document, Bell, Setting, DataLine, SwitchButton, Key,
} from "@element-plus/icons-vue";
import api from "../api";

const router = useRouter();
const route = useRoute();
const user = ref(null);

/**
 * 左侧导航分组：按「业务域」归类，一组只放同类功能。
 *
 * 1. 工作台      —— 概览与自测
 * 2. 售后业务    —— 工单 / 客户 / 退款 / 时效 / 审批，都是日常售后处理
 * 3. 销售与客服  —— 线索漏斗、转人工接管，面向售前与人工服务
 * 4. 知识库      —— 知识资产的维护
 * 5. 组织与人员  —— 人、角色、权限
 * 6. 系统管理    —— 集成配置、日志、通知、全局设置
 */
const MENU_GROUPS = [
  {
    key: "workbench", label: "工作台", icon: "DataLine",
    items: [
      { path: "/monitor", label: "监控看板", icon: "DataLine" },
      { path: "/chat",    label: "对话测试", icon: "ChatDotRound" },
    ],
  },
  {
    key: "business", label: "售后业务", icon: "Tickets",
    items: [
      { path: "/tickets",   label: "工单",     icon: "Tickets" },
      { path: "/customers", label: "客户资产", icon: "UserFilled" },
      { path: "/refunds",   label: "退款管理", icon: "Money",       perm: "refund.view" },
      { path: "/sla",       label: "SLA 时效", icon: "AlarmClock" },
      { path: "/approvals", label: "审批台",   icon: "CircleCheck", perm: "refund.approve" },
    ],
  },
  {
    key: "sales", label: "销售与客服", icon: "TrendCharts",
    items: [
      { path: "/leads",    label: "线索管理",   icon: "TrendCharts", perm: "lead.view" },
      { path: "/handoffs", label: "人工客服台", icon: "Service",     perm: "handoff.view" },
    ],
  },
  {
    key: "knowledge", label: "知识库", icon: "Collection",
    items: [
      { path: "/knowledge", label: "知识库", icon: "Collection" },
    ],
  },
  {
    key: "team", label: "组织与人员", icon: "User",
    items: [
      { path: "/people",    label: "人员管理", icon: "UserFilled", perm: "user.view" },
      { path: "/engineers", label: "工程师",   icon: "User",       perm: "user.view" },
    ],
  },
  {
    key: "system", label: "系统管理", icon: "Setting",
    items: [
      { path: "/feishu",        label: "飞书配置", icon: "ChatDotRound", perm: "system.edit" },
      { path: "/audit",         label: "审计日志", icon: "Document",      perm: "audit.view" },
      { path: "/notifications", label: "通知记录", icon: "Bell",          perm: "audit.view" },
      { path: "/config",        label: "系统配置", icon: "Setting",       perm: "system.edit" },
    ],
  },
];

const perms = computed(() => (user.value && user.value.effective_permissions) || []);
const isAdmin = computed(() => perms.value.includes("*"));

function hasPerm(p) {
  if (!p) return true;
  if (isAdmin.value) return true;
  return perms.value.includes(p);
}

/** 过滤掉无权限的项；整组都空了就连组标题一起隐藏 */
const visibleGroups = computed(() =>
  MENU_GROUPS
    .map((g) => ({ ...g, items: g.items.filter((it) => hasPerm(it.perm)) }))
    .filter((g) => g.items.length > 0)
);

/** 当前页面所属分组，用于顶栏面包屑 */
const currentGroup = computed(() =>
  MENU_GROUPS.find((g) => g.items.some((it) => it.path === route.path)) || null
);

function roleColor(r) {
  return { admin: "#ef4444", supervisor: "#f59e0b", engineer: "#4f46e5", agent: "#22c55e" }[r] || "#9ca3af";
}

async function doLogout() {
  try {
    await ElMessageBox.confirm("确定退出登录？", "提示", { type: "warning" });
  } catch { return; }
  try { await api.logout(); } catch (e) { /* 忽略 */ }
  localStorage.removeItem("auth_token");
  localStorage.removeItem("auth_user");
  ElMessage.success("已退出");
  router.push("/login");
}

// ---------- 修改自己的密码 ----------
const pwdDialog = ref(false);
const pwdLoading = ref(false);
const pwdFormRef = ref(null);
const pwdForm = reactive({ old_password: "", new_password: "", confirm: "" });

const pwdRules = {
  old_password: [{ required: true, message: "请输入原密码", trigger: "blur" }],
  new_password: [
    { required: true, message: "请输入新密码", trigger: "blur" },
    { min: 6, message: "新密码至少 6 位", trigger: "blur" },
  ],
  confirm: [
    { required: true, message: "请再次输入新密码", trigger: "blur" },
    {
      validator: (rule, value, cb) =>
        value === pwdForm.new_password ? cb() : cb(new Error("两次输入不一致")),
      trigger: "blur",
    },
  ],
};

function openPwdDialog() {
  pwdForm.old_password = "";
  pwdForm.new_password = "";
  pwdForm.confirm = "";
  pwdDialog.value = true;
}

async function doChangePwd() {
  try {
    await pwdFormRef.value.validate();
  } catch { return; }

  pwdLoading.value = true;
  try {
    await api.changePassword({
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    });
    pwdDialog.value = false;
    ElMessage.success("密码已修改，请用新密码重新登录");
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  } catch (e) {
    // 拦截器已弹出具体原因（原密码错误 / 位数不足 等）
  } finally {
    pwdLoading.value = false;
  }
}

onMounted(async () => {
  const cached = localStorage.getItem("auth_user");
  if (cached) {
    try { user.value = JSON.parse(cached); } catch (e) { /* 忽略 */ }
  }
  try {
    const me = await api.me();
    user.value = me;
    localStorage.setItem("auth_user", JSON.stringify(me));
  } catch (e) {
    router.push("/login");
  }
});
</script>

<style scoped>
.sidebar {
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  border-right: 1px solid #e5e7eb;
  display: flex; flex-direction: column;
}
.logo {
  display: flex; align-items: center; gap: 10px;
  padding: 16px 18px; border-bottom: 1px solid #e5e7eb;
  color: #4f46e5; flex-shrink: 0;
}
.logo-title { font-weight: 700; font-size: 15px; }
.logo-sub { font-size: 11px; color: #6b7280; }

/* 菜单区可滚动；min-height:0 让 flex 子项能正常收缩 */
.menu-wrap { flex: 1; min-height: 0; overflow-y: auto; }
.menu { border: none; background: transparent; padding-bottom: 8px; }

/* 分组标题：小而灰，起"分隔章节"的作用 */
.menu :deep(.el-menu-item-group__title) {
  padding: 14px 18px 6px;
  line-height: 1.2;
}
.menu :deep(.el-menu-item-group:first-child .el-menu-item-group__title) {
  padding-top: 8px;
}
.group-title {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  color: #9ca3af; text-transform: uppercase;
}

/* 菜单项：圆角、紧凑 */
.menu :deep(.el-menu-item) {
  height: 38px; line-height: 38px;
  margin: 2px 10px; border-radius: 8px;
  font-size: 13px; color: #374151;
}
.menu :deep(.el-menu-item:hover) { background: #eef2ff; color: #4f46e5; }
.menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, #4f46e5, #7c3aed);
  color: #fff; font-weight: 600;
}

.footer { padding: 12px 16px; border-top: 1px solid #e5e7eb; flex-shrink: 0; }
.user-info { display: flex; align-items: center; gap: 10px; }
.user-detail { flex: 1; min-width: 0; }
.user-name {
  font-size: 13px; font-weight: 600; color: #111827;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.user-role { font-size: 11px; color: #6b7280; }

.pwd-tip {
  margin-top: -6px;
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.5;
}

.topbar {
  background: white; border-bottom: 1px solid #e5e7eb;
  display: flex; align-items: center; gap: 8px; padding: 0 24px;
  height: 56px;
}
.route-title { font-size: 16px; font-weight: 600; color: #374151; }
.route-group { font-size: 12px; color: #9ca3af; }

.main-content { background: #f8fafc; padding: 24px; overflow-y: auto; }
</style>
