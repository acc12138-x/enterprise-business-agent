<template>
  <div class="handoff-page">
    <div class="page-title">🎧 人工客服台</div>

    <!-- ============ 顶部统计条 ============ -->
    <div class="stat-bar">
      <div v-for="c in statCards" :key="c.key" class="stat-card card-panel">
        <el-statistic :value="c.value" :value-style="'color:' + c.color + ';font-weight:700;'">
          <template #title>
            <span class="stat-title">{{ c.title }}</span>
          </template>
        </el-statistic>
        <div v-if="c.extra" class="stat-extra" :title="c.extra">{{ c.extra }}</div>
      </div>
    </div>

    <!-- ============ 三栏主体 ============ -->
    <div class="handoff-body">
      <!-- ---------- 左栏：队列列表 ---------- -->
      <div class="card-panel pane pane-left">
        <div class="pane-head">
          <el-radio-group v-model="tab" size="small" @change="onTabChange">
            <el-radio-button value="queued">待接入 {{ queuedList.length }}</el-radio-button>
            <el-radio-button value="claimed">已接管 {{ claimedList.length }}</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
          </el-radio-group>
          <el-button link type="primary" :icon="Refresh" title="立即刷新" @click="manualRefresh" />
        </div>

        <div v-loading="listLoading" class="queue-list">
          <el-empty v-if="!displayList.length" description="暂无工单" :image-size="70" />

          <div
            v-for="row in displayList"
            :key="row.handoff_id"
            class="queue-item"
            :class="{ active: row.handoff_id === selectedId }"
            @click="selectHandoff(row)"
          >
            <div class="qi-top">
              <span class="qi-id">{{ row.handoff_id }}</span>
              <el-tag :type="statusType(row.status)" size="small" effect="plain">
                {{ row.status_label }}
              </el-tag>
            </div>
            <div class="qi-reason">
              🎯 {{ row.trigger_label }}<span v-if="row.reason"> · {{ row.reason }}</span>
            </div>
            <div class="qi-line">👤 {{ shortId(row.sender_open_id) }}</div>
            <div class="qi-line qi-msg">💬 {{ row.last_user_msg || "（暂无用户消息）" }}</div>
            <!-- 等待时长由 created_at 实时算出；超过 5 分钟标红加粗 -->
            <div class="qi-wait" :class="{ overdue: isOverdue(row) }">
              ⏱ {{ waitText(row) }}<span v-if="isOverdue(row)"> · 已超 5 分钟</span>
            </div>
          </div>
        </div>
      </div>

      <!-- ---------- 中栏：会话消息流 ---------- -->
      <div class="card-panel pane pane-mid">
        <div class="pane-head mid-head">
          <div v-if="detail" class="mid-info">
            <span class="mid-title">🧵 {{ detail.handoff_id }}</span>
            <el-tag :type="statusType(detail.status)" size="small" style="margin-left:8px;">
              {{ detail.status_label }}
            </el-tag>
            <div class="mid-sub">会话 {{ detail.thread_id }}</div>
          </div>
          <span v-else class="mid-title">请选择左侧工单</span>
        </div>

        <div ref="msgList" class="msg-list">
          <el-empty
            v-if="!detail"
            description="从左侧队列中选择一条转人工工单 👈"
            :image-size="80"
          />
          <template v-else>
            <el-empty
              v-if="!detail.messages || !detail.messages.length"
              description="该会话暂无消息"
              :image-size="70"
            />
            <div v-for="m in detail.messages" :key="m.id" class="msg-row" :class="m.role">
              <!-- 系统提示：居中、小字、灰色、斜体 -->
              <div v-if="m.role === 'system'" class="msg-system">{{ m.content }}</div>
              <template v-else>
                <div class="msg-meta">
                  <span class="msg-who">{{ roleLabel(m) }}</span>
                  <span>{{ fmtClock(m.created_at) }}</span>
                </div>
                <div class="msg-bubble">{{ m.content }}</div>
              </template>
            </div>
          </template>
        </div>

        <!-- 回复框 -->
        <div class="reply-bar">
          <el-input
            v-model="replyText"
            type="textarea"
            :rows="3"
            resize="none"
            :disabled="!canReply"
            :placeholder="canReply ? '输入回复内容，Ctrl + Enter 快速发送' : replyHint"
            @keydown.ctrl.enter.prevent="sendReply"
          />
          <div class="reply-foot">
            <span class="reply-hint" :class="{ warn: !!detail && !canReply }">
              {{ canReply ? "Ctrl + Enter 发送 · 消息会直接推送给用户" : replyHint }}
            </span>
            <el-button
              type="primary"
              :icon="Promotion"
              :loading="sending"
              :disabled="!canReply || !replyText.trim()"
              @click="sendReply"
            >发送</el-button>
          </div>
        </div>
      </div>

      <!-- ---------- 右栏：上下文 + 操作 ---------- -->
      <div class="card-panel pane pane-right">
        <div class="pane-head">
          <span class="mid-title">📋 工单上下文</span>
        </div>

        <div class="right-body">
          <template v-if="detail">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="工单号">{{ detail.handoff_id }}</el-descriptions-item>
              <el-descriptions-item label="状态">
                <el-tag :type="statusType(detail.status)" size="small">{{ detail.status_label }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="触发来源">{{ detail.trigger_label }}</el-descriptions-item>
              <el-descriptions-item label="认领人">{{ detail.claimed_by_name || "-" }}</el-descriptions-item>
              <el-descriptions-item label="认领时间">{{ fmtTime(detail.claimed_at) }}</el-descriptions-item>
              <el-descriptions-item :label="detail.status === 'closed' ? '处理时长' : '等待时长'">
                {{ waitText(detail) }}
              </el-descriptions-item>
            </el-descriptions>

            <!-- 关联线索：这里不跳转，只展示编号 -->
            <el-alert v-if="detail.lead_id" type="info" :closable="false" style="margin-top:12px;">
              <template #title>
                🔗 关联线索：{{ detail.lead_id }}
                <div class="alert-sub">可到「线索管理」按该编号查看详情</div>
              </template>
            </el-alert>
            <el-alert v-if="detail.customer_id" type="success" :closable="false" style="margin-top:8px;">
              <template #title>👤 关联客户：{{ detail.customer_id }}</template>
            </el-alert>

            <div class="sec-title">📝 会话摘要</div>
            <div class="sec-body">{{ detail.summary || "（暂无摘要）" }}</div>

            <div class="sec-title">💬 最后消息</div>
            <div class="sec-body">{{ detail.last_user_msg || "（暂无）" }}</div>

            <div v-if="detail.close_note" class="sec-title">🗒 结单说明</div>
            <div v-if="detail.close_note" class="sec-body">{{ detail.close_note }}</div>

            <div class="sec-title">⚙️ 操作</div>
            <div class="op-btns">
              <el-button
                type="primary"
                :loading="claiming"
                :disabled="detail.status !== 'queued'"
                @click="doClaim"
              >🙋 认领</el-button>
              <el-button
                type="success"
                :loading="closing"
                :disabled="detail.status !== 'claimed'"
                @click="doClose"
              >✅ 结束接管</el-button>
            </div>
            <div v-if="detail.status === 'claimed' && !canReply" class="op-tip">
              该会话由「{{ detail.claimed_by_name || "其他坐席" }}」接管中，你只能查看
            </div>
            <div v-else-if="detail.status === 'queued'" class="op-tip">
              认领后即可回复用户（AI 会自动静默）
            </div>
          </template>
          <el-empty v-else description="未选择工单" :image-size="70" />
        </div>

        <div class="op-bottom">
          <el-button plain style="width:100%;" @click="openCreate">➕ 手动建单</el-button>
        </div>
      </div>
    </div>

    <!-- ============ 手动转人工 ============ -->
    <el-dialog v-model="createVisible" title="➕ 手动转人工" width="480px">
      <el-alert type="info" :closable="false" style="margin-bottom:12px;">
        <template #title>
          正常应由用户说「转人工」自动建单；这里用于电话 / 线下渠道的兜底建单。
        </template>
      </el-alert>
      <el-form :model="createForm" label-width="96px">
        <el-form-item label="会话 ID" required>
          <el-input v-model="createForm.thread_id" placeholder="例如 web-abc123 / 飞书 open_id" />
        </el-form-item>
        <el-form-item label="转人工原因">
          <el-input v-model="createForm.reason" placeholder="例如：用户来电要求人工处理" />
        </el-form-item>
        <el-form-item label="用户 open_id">
          <el-input v-model="createForm.sender_open_id" placeholder="飞书 open_id，用于把回复推送给用户" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建工单</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh, Promotion } from "@element-plus/icons-vue";
// 统一用共享客户端：它自带 Authorization 请求头与统一错误提示。
// 千万不要在本页 axios.create() —— 自建实例不带 token，会得到 401「未登录」。
import api, { toastError as toastErrorShared } from "../api";

// ============================================================
// 状态
// ============================================================
const tab = ref("queued");            // queued / claimed / all
const queuedList = ref([]);           // status=queued
const claimedList = ref([]);          // status=claimed（用于「我接管的」计数）
const allList = ref([]);              // 全部（切到「全部」页签时才拉）
const stats = ref({});
const now = ref(Date.now());          // 秒级时钟，驱动「等待时长」实时刷新
const listLoading = ref(false);

const me = ref(null);                 // 当前登录坐席
const detail = ref(null);             // 当前工单详情（含 messages）
const selectedId = ref("");
const replyText = ref("");
const sending = ref(false);
const claiming = ref(false);
const closing = ref(false);
const msgList = ref(null);            // 消息滚动容器

const createVisible = ref(false);
const creating = ref(false);
const createForm = reactive({ thread_id: "", reason: "", sender_open_id: "" });

let pollTimer = null;
let clockTimer = null;

// ============================================================
// 工具函数
// ============================================================
/** ISO 字符串 → 毫秒时间戳；解析失败返回 0 */
function parseTime(s) {
  if (!s) return 0;
  const t = Date.parse(s);
  return isNaN(t) ? 0 : t;
}

/** 秒 → 中文时长（秒 / 分 / 小时 / 天） */
function fmtDuration(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (s < 60) return s + " 秒";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " 分 " + (s % 60) + " 秒";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " 小时 " + (m % 60) + " 分";
  return Math.floor(h / 24) + " 天 " + (h % 24) + " 小时";
}

/** 完整时间：YYYY-MM-DD HH:mm:ss */
function fmtTime(s) {
  const t = parseTime(s);
  if (!t) return "-";
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return (
    d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds())
  );
}

/** 消息气泡上只显示时分秒 */
function fmtClock(s) {
  const full = fmtTime(s);
  return full === "-" ? "" : full.slice(11);
}

/** 飞书 open_id 太长，截断显示 */
function shortId(id) {
  if (!id) return "未知用户";
  return id.length > 18 ? id.slice(0, 10) + "…" + id.slice(-4) : id;
}

/** 状态 → el-tag 类型 */
function statusType(s) {
  return { queued: "warning", claimed: "success", closed: "info", timeout: "danger" }[s] || "info";
}

/** 消息气泡上的人称标注 */
function roleLabel(m) {
  if (m.role === "user") return "👤 用户";
  if (m.role === "assistant") return "🤖 AI";
  if (m.role === "human_agent") return "🧑‍💼 人工 · " + (m.sender_name || "坐席");
  return m.role;
}

/** 等待秒数：进行中的工单算到「此刻」，已结束的算到 closed_at */
function waitSec(row) {
  if (!row) return 0;
  const created = parseTime(row.created_at);
  if (!created) return 0;
  const end =
    row.status === "closed" || row.status === "timeout"
      ? parseTime(row.closed_at) || now.value
      : now.value;
  return Math.max(0, Math.floor((end - created) / 1000));
}

/** 队列里展示的时长文案 */
function waitText(row) {
  if (!row) return "-";
  const created = parseTime(row.created_at);
  const claimed = parseTime(row.claimed_at);

  if (row.status === "closed") {
    const end = parseTime(row.closed_at) || now.value;
    const dur = claimed ? end - claimed : end - created;
    return "处理 " + fmtDuration(Math.floor(dur / 1000));
  }
  if (row.status === "claimed") {
    const waited = claimed ? claimed - created : now.value - created;
    const holding = now.value - (claimed || created);
    return "等待 " + fmtDuration(Math.floor(waited / 1000)) + " · 接管 " + fmtDuration(Math.floor(holding / 1000));
  }
  // queued / timeout
  return "已等待 " + fmtDuration(waitSec(row));
}

/** 待接入超过 5 分钟 → 标红加粗 */
function isOverdue(row) {
  return !!row && row.status === "queued" && waitSec(row) > 300;
}

/** 后端 detail 优先，其次 Error.message。
 *
 * 用共享的 toastError（来自 ../api）而不是直接 ElMessage.error：
 * 响应拦截器已经统一弹过一次错误，这个版本会做短窗口去重，
 * 避免同一个错误弹两条一模一样的红条。
 */
function toastError(e) {
  const d = e?.response?.data?.detail || e.message;
  toastErrorShared(typeof d === "string" ? d : JSON.stringify(d || "请求失败"));
}

// ============================================================
// 统计条
// ============================================================
/** 「我接管的」：接口只给全局 claimed 数，这里从已接管列表按当前登录人筛 */
const myClaimedCount = computed(() => {
  if (!me.value) return 0;
  return claimedList.value.filter((h) => h.claimed_by === me.value.id).length;
});

const perAgentText = computed(() => {
  const list = stats.value.per_agent || [];
  if (!list.length) return "";
  const head = list.slice(0, 3).map((a) => a.name + "(" + a.count + ")").join(" ");
  return "坐席：" + head + (list.length > 3 ? " …" : "");
});

const statCards = computed(() => [
  { key: "queued", title: "⏳ 待接入", value: stats.value.queued || 0, color: "#f59e0b" },
  { key: "mine", title: "🙋 我接管的", value: myClaimedCount.value, color: "#16a34a" },
  { key: "closed", title: "✅ 已结束", value: stats.value.closed || 0, color: "#6b7280" },
  { key: "timeout", title: "⏰ 已超时", value: stats.value.timeout || 0, color: "#ef4444" },
  {
    key: "avg",
    title: "⚡ 平均响应(秒)",
    value: stats.value.avg_claim_seconds || 0,
    color: "#4f46e5",
    extra: perAgentText.value,
  },
]);

// ============================================================
// 左栏：列表
// ============================================================
const displayList = computed(() => {
  if (tab.value === "queued") return queuedList.value;
  if (tab.value === "claimed") return claimedList.value;
  return allList.value;
});

/**
 * 拉队列 + 统计。
 * 待接入 / 已接管各自按状态查（保证小 limit 下也不会把待接入的老单挤掉），
 * 只有切到「全部」页签时才额外拉一次不带 status 的全量。
 * silent=true 用于 5 秒轮询：不显示 loading、失败不打扰用户。
 */
async function loadAll(silent = false) {
  if (!silent) listLoading.value = true;
  try {
    const jobs = [
      api.get("/handoffs/stats").catch(() => null),
      api.get("/handoffs", { params: { status: "queued", limit: 200 } }),
      api.get("/handoffs", { params: { status: "claimed", limit: 200 } }),
    ];
    if (tab.value === "all") jobs.push(api.get("/handoffs", { params: { limit: 200 } }));

    const [s, q, c, a] = await Promise.all(jobs);
    if (s) stats.value = s;
    if (Array.isArray(q)) queuedList.value = q;
    if (Array.isArray(c)) claimedList.value = c;
    if (Array.isArray(a)) allList.value = a;
  } catch (e) {
    if (!silent) toastError(e);
  } finally {
    if (!silent) listLoading.value = false;
  }
}

function onTabChange() {
  // 「全部」按需加载，其余页签的数据每次轮询都在手上
  if (tab.value === "all") loadAll();
}

async function manualRefresh() {
  await loadAll();
  await refreshDetail(false);
  ElMessage.success("已刷新");
}

// ============================================================
// 中栏：详情与消息
// ============================================================
/** 消息指纹：内容没变就不重建数组，避免滚动位置被打断 */
function msgSig(list) {
  if (!list || !list.length) return "";
  return list.map((m) => m.id + ":" + m.role + ":" + (m.content || "").length).join("|");
}

async function scrollToBottom() {
  await nextTick();
  if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight;
}

/** 应用详情：有新消息时自动滚到底部 */
function applyDetail(d, forceScroll) {
  const prev =
    detail.value && detail.value.handoff_id === d.handoff_id ? detail.value.messages : null;
  const changed = msgSig(prev) !== msgSig(d.messages);
  detail.value = d;
  if (changed || forceScroll) scrollToBottom();
}

async function selectHandoff(row) {
  if (!row) return;
  const id = row.handoff_id;
  selectedId.value = id; // 先高亮，再取详情
  replyText.value = ""; // 换工单才清空输入框；轮询刷新绝不动它
  try {
    const d = await api.get("/handoffs/" + id);
    // 期间又点了别的工单：丢弃这次过期响应，否则会显示错人的会话
    if (selectedId.value !== id) return;
    applyDetail(d, true);
  } catch (e) {
    toastError(e);
  }
}

/** 刷新当前详情（轮询用 silent=true） */
async function refreshDetail(silent = true) {
  const id = selectedId.value;
  if (!id) return;
  try {
    const d = await api.get("/handoffs/" + id);
    if (selectedId.value !== id) return;
    applyDetail(d, false);
  } catch (e) {
    // 工单可能已被删除；轮询期间的失败不打扰用户
    if (!silent) toastError(e);
  }
}

// ============================================================
// 回复权限
// ============================================================
/** 只有「已接管」且认领人就是自己时才能回复 */
const canReply = computed(() => {
  const d = detail.value;
  if (!d || !me.value) return false;
  return d.status === "claimed" && d.claimed_by === me.value.id;
});

const replyHint = computed(() => {
  const d = detail.value;
  if (!d) return "请先在左侧选择一条工单";
  if (d.status === "queued") return "该会话尚未认领，请先点右侧「认领」";
  if (d.status === "claimed") {
    return "该会话由「" + (d.claimed_by_name || "其他坐席") + "」接管中，你无法回复";
  }
  return "会话" + (d.status_label || d.status) + "，已不能再回复";
});

// ============================================================
// 动作：认领 / 回复 / 结束 / 手动建单
// ============================================================
async function doClaim() {
  const d = detail.value;
  if (!d) return;
  claiming.value = true;
  try {
    await api.post("/handoffs/" + d.handoff_id + "/claim", {});
    ElMessage.success("已认领，现在可以回复用户了");
    await loadAll(true);
    await refreshDetail(true);
  } catch (e) {
    toastError(e);
    await loadAll(true); // 可能已被别人抢走，刷新看真实状态
  } finally {
    claiming.value = false;
  }
}

async function sendReply() {
  const text = replyText.value.trim();
  const d = detail.value;
  if (!text || !d || !canReply.value || sending.value) return;

  sending.value = true;
  try {
    const r = await api.post("/handoffs/" + d.handoff_id + "/reply", { text });
    if (r && r.sent === false) {
      // sent=false：消息已落库，但推送到飞书失败 —— 只提示，不当异常
      ElMessage.warning("消息已记录，但推送到飞书失败：" + (r.error || "未知原因"));
    } else {
      ElMessage.success("已发送");
    }
    replyText.value = "";
    await refreshDetail(true);
    await scrollToBottom();
  } catch (e) {
    // 未认领 / 被他人接管 / 内容为空等，后端返回 400
    toastError(e);
  } finally {
    sending.value = false;
  }
}

async function doClose() {
  const d = detail.value;
  if (!d) return;

  let note = "";
  try {
    const r = await ElMessageBox.prompt("请填写结单说明（会记入该工单）", "结束接管", {
      confirmButtonText: "确认结束",
      cancelButtonText: "取消",
      inputType: "textarea",
      inputPlaceholder: "例如：已协助用户完成退款登记，问题解决",
      inputValidator: (v) => (v && v.trim() ? true : "结单说明不能为空"),
    });
    note = r.value;
  } catch (e) {
    return; // 用户取消
  }

  closing.value = true;
  try {
    const r = await api.post("/handoffs/" + d.handoff_id + "/close", { note });
    const dur = r && r.duration_sec != null ? "，本次服务时长 " + fmtDuration(r.duration_sec) : "";
    ElMessage.success("已结束接管" + dur + "，会话已转回智能助手");
    await loadAll(true);
    await refreshDetail(true);
  } catch (e) {
    toastError(e);
  } finally {
    closing.value = false;
  }
}

function openCreate() {
  Object.assign(createForm, { thread_id: "", reason: "", sender_open_id: "" });
  createVisible.value = true;
}

async function doCreate() {
  const threadId = createForm.thread_id.trim();
  if (!threadId) return ElMessage.warning("会话 ID 必填");
  creating.value = true;
  try {
    const r = await api.post("/handoffs", {
      thread_id: threadId,
      reason: createForm.reason.trim(),
      sender_open_id: createForm.sender_open_id.trim(),
    });
    if (r && r.reused) {
      ElMessage.info("该会话已有未结束的工单：" + r.handoff_id);
    } else {
      ElMessage.success("已创建工单 " + (r && r.handoff_id ? r.handoff_id : ""));
    }
    createVisible.value = false;
    await loadAll(true);
    if (r && r.handoff_id) {
      selectedId.value = r.handoff_id;
      await refreshDetail(true);
      await scrollToBottom();
    }
  } catch (e) {
    toastError(e);
  } finally {
    creating.value = false;
  }
}

// ============================================================
// 轮询（后端没有为这个页面提供 WebSocket，用 5 秒轮询代替）
// ============================================================
async function poll() {
  await loadAll(true);
  await refreshDetail(true); // 只换 messages，不动 replyText / selectedId
}

onMounted(async () => {
  // 当前登录坐席：仅用于「我接管的」计数与回复按钮的可用判断，
  // 真正的权限（handoff.view / claim / reply）由后端强制校验。
  try {
    const cached = localStorage.getItem("auth_user");
    if (cached) me.value = JSON.parse(cached);
  } catch (e) {
    /* 忽略缓存解析失败 */
  }
  try {
    me.value = await api.me();
    localStorage.setItem("auth_user", JSON.stringify(me.value));
  } catch (e) {
    /* 拦截器已提示，MainLayout 会处理跳登录 */
  }

  await loadAll();
  // 默认选中第一条待接入，省一次点击
  if (queuedList.value.length) await selectHandoff(queuedList.value[0]);

  pollTimer = setInterval(poll, 5000);
  clockTimer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});

onBeforeUnmount(() => {
  // 组件卸载必须清掉定时器，否则切走页面后仍在后台打接口
  if (pollTimer) clearInterval(pollTimer);
  if (clockTimer) clearInterval(clockTimer);
  pollTimer = null;
  clockTimer = null;
});
</script>

<style scoped>
.handoff-page {
  display: flex; flex-direction: column;
  height: calc(100vh - 56px - 48px);
  gap: 12px;
}

/* ---------- 顶部统计条 ---------- */
.stat-bar { display: flex; gap: 12px; flex-shrink: 0; }
.stat-card { flex: 1; min-width: 0; padding: 10px 14px; }
.stat-title { font-size: 12px; color: #6b7280; }
.stat-extra {
  font-size: 11px; color: #9ca3af; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* ---------- 三栏骨架 ---------- */
.handoff-body { flex: 1; min-height: 0; display: flex; gap: 12px; align-items: stretch; }
.pane { display: flex; flex-direction: column; min-height: 0; padding: 12px; }
.pane-left { width: 320px; flex-shrink: 0; }
.pane-mid { flex: 1; min-width: 0; }
.pane-right { width: 280px; flex-shrink: 0; }
.pane-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; padding-bottom: 10px; border-bottom: 1px solid #f1f5f9; flex-shrink: 0;
}
.mid-head { align-items: flex-start; }
.mid-info { min-width: 0; }
.mid-title { font-size: 14px; font-weight: 600; color: #1f2937; }
.mid-sub {
  font-size: 11px; color: #9ca3af; margin-top: 4px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.alert-sub { font-size: 11px; color: #9ca3af; margin-top: 2px; }

/* ---------- 左栏：队列 ---------- */
.queue-list { flex: 1; min-height: 0; overflow-y: auto; padding-top: 8px; }
.queue-item {
  border: 1px solid #eef2f7; border-radius: 8px;
  padding: 8px 10px; margin-bottom: 8px;
  background: #fff; cursor: pointer; transition: all 0.15s;
}
.queue-item:hover { border-color: #c7d2fe; background: #f8faff; }
.queue-item.active { border-color: #4f46e5; background: #eef2ff; }
.qi-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.qi-id { font-size: 12px; font-weight: 700; color: #4f46e5; }
.qi-reason { font-size: 12px; color: #374151; margin-top: 4px; }
.qi-line {
  font-size: 12px; color: #6b7280; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.qi-msg { color: #9ca3af; }
.qi-wait { font-size: 12px; color: #6b7280; margin-top: 4px; }
/* 超过 5 分钟未接入 → 标红加粗 */
.qi-wait.overdue { color: #dc2626; font-weight: 700; }

/* ---------- 中栏：消息流 ---------- */
.msg-list {
  flex: 1; min-height: 0; overflow-y: auto;
  padding: 12px 4px; display: flex; flex-direction: column; gap: 12px;
}
.msg-row { display: flex; flex-direction: column; gap: 4px; }
.msg-row.user,
.msg-row.assistant { align-items: flex-start; }
.msg-row.human_agent { align-items: flex-end; }
.msg-row.system { align-items: center; }
.msg-meta { display: flex; gap: 6px; align-items: center; font-size: 11px; color: #9ca3af; }
.msg-who { font-weight: 600; }
.msg-bubble {
  max-width: 78%; padding: 10px 14px;
  border-radius: 12px; font-size: 14px; line-height: 1.6;
  /* 保留消息里的换行 */
  white-space: pre-wrap; word-break: break-word;
}
.msg-row.user .msg-bubble { background: #f3f4f6; color: #111827; border-top-left-radius: 2px; }
.msg-row.assistant .msg-bubble { background: #e0f2fe; color: #0c4a6e; border-top-left-radius: 2px; }
.msg-row.human_agent .msg-bubble { background: #dcfce7; color: #14532d; border-top-right-radius: 2px; }
.msg-system {
  font-size: 12px; color: #9ca3af; font-style: italic;
  text-align: center; padding: 2px 10px;
}

/* ---------- 回复框 ---------- */
.reply-bar { flex-shrink: 0; padding-top: 10px; border-top: 1px solid #f1f5f9; }
.reply-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-top: 8px;
}
.reply-hint { font-size: 12px; color: #9ca3af; }
.reply-hint.warn { color: #f59e0b; }

/* ---------- 右栏：上下文 ---------- */
.right-body { flex: 1; min-height: 0; overflow-y: auto; padding-top: 10px; }
.sec-title { font-size: 12px; font-weight: 600; color: #374151; margin: 14px 0 6px; }
.sec-body {
  font-size: 12px; color: #4b5563; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
  background: #f8fafc; border: 1px solid #f1f5f9;
  border-radius: 8px; padding: 8px 10px;
  max-height: 160px; overflow: auto;
}
.op-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.op-tip { font-size: 11px; color: #f59e0b; margin-top: 6px; line-height: 1.5; }
.op-bottom { flex-shrink: 0; padding-top: 10px; border-top: 1px solid #f1f5f9; }
</style>
