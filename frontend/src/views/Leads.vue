<template>
  <div>
    <div class="page-title">🎯 客户线索</div>

    <!-- ============ 顶部统计卡：总量 / 进行中 / 已成交 / 成交率 ============ -->
    <el-row :gutter="12" style="margin-bottom:12px;">
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <el-statistic title="总线索数" :value="stats.total" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <el-statistic title="🔥 进行中" :value="stats.open" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <el-statistic title="✅ 已成交" :value="stats.won" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <el-statistic title="📈 成交率" :value="stats.conversion_rate" suffix="%" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 金额小字：报价总额 / 成交总额 / 平均成交额 -->
    <div class="money-line">
      💰 报价总额 ¥{{ fmtMoney(stats.quote_sum) }}
      <span class="dot">·</span>
      🏆 成交总额 ¥{{ fmtMoney(stats.deal_sum) }}
      <span class="dot">·</span>
      📊 平均成交额 ¥{{ fmtMoney(stats.avg_deal) }}
      <span class="dot">·</span>
      ❌ 已流失 {{ stats.lost }} 条
    </div>

    <!-- ============ 销售漏斗：new → contacted → quoted → negotiating → won ============ -->
    <el-card shadow="never" style="margin-bottom:16px;">
      <template #header>
        <span style="font-weight:600;">🔻 销售漏斗</span>
        <span class="hint" style="margin-left:8px;">（按各阶段线索数占最高阶段的比例）</span>
      </template>

      <div v-for="s in FUNNEL_STAGES" :key="s" class="funnel-row">
        <div class="funnel-name">{{ stageLabel(s) }}</div>
        <el-progress
          :percentage="funnelPercent(s)"
          :stroke-width="16"
          :color="stageColor(s)"
          :show-text="false"
          style="flex:1;"
        />
        <div class="funnel-count">{{ funnelCount(s) }}</div>
      </div>

      <div class="hint" style="margin-top:8px;">
        已流失 {{ funnelCount("lost") }} 条（不计入漏斗）
      </div>
    </el-card>

    <!-- ============ 筛选栏 ============ -->
    <el-card shadow="never" style="margin-bottom:16px;">
      <el-form inline>
        <el-form-item label="阶段">
          <el-select v-model="filters.stage" clearable placeholder="全部" style="width:140px;" @change="load">
            <el-option v-for="s in ALL_STAGES" :key="s" :value="s" :label="stageLabel(s)" />
          </el-select>
        </el-form-item>

        <el-form-item label="负责人">
          <el-select v-model="filters.owner_id" clearable placeholder="全部" style="width:170px;" @change="load">
            <el-option
              v-for="p in pool" :key="p.id" :value="p.id"
              :label="p.name + (p.job ? '（' + p.job + '）' : '')"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="关键词">
          <el-input
            v-model="filters.keyword"
            placeholder="线索号 / 姓名 / 电话 / 公司 / 需求"
            clearable
            style="width:250px;"
            @keyup.enter="load"
            @clear="load"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item label="只看进行中">
          <el-switch v-model="filters.only_open" @change="load" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
          <el-button @click="openAdd">+ 新建线索</el-button>
          <el-button :icon="Refresh" @click="refreshAll">刷新</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ============ 线索表格 ============ -->
    <el-card shadow="never">
      <el-table :data="leads" v-loading="loading" stripe empty-text="暂无线索">
        <el-table-column label="线索号" width="170">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.lead_id }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="客户 / 公司" min-width="150">
          <template #default="{ row }">
            <span style="font-weight:600;">{{ row.name }}</span>
            <el-tag
              v-if="row.priority === 'high'"
              type="danger" size="small" effect="plain" style="margin-left:6px;"
            >高</el-tag>
            <div v-if="row.company" class="sub-text">{{ row.company }}</div>
          </template>
        </el-table-column>

        <el-table-column label="联系电话" width="130">
          <template #default="{ row }">{{ row.phone || "-" }}</template>
        </el-table-column>

        <el-table-column label="来源" width="100">
          <template #default="{ row }">{{ row.source_label || "-" }}</template>
        </el-table-column>

        <el-table-column label="负责人" width="110">
          <template #default="{ row }">
            <span v-if="row.owner_name">{{ row.owner_name }}</span>
            <span v-else style="color:#9ca3af;">未分配</span>
          </template>
        </el-table-column>

        <el-table-column label="阶段" width="100">
          <template #default="{ row }">
            <el-tag :type="stageTagType(row.stage)" size="small">{{ row.stage_label }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="已报价" width="140">
          <template #default="{ row }">
            <template v-if="row.quoted">
              <el-tag type="success" size="small">是</el-tag>
              <span style="margin-left:6px;">¥{{ fmtMoney(row.quote_amount) }}</span>
            </template>
            <span v-else style="color:#9ca3af;">否</span>
          </template>
        </el-table-column>

        <el-table-column label="已成交" width="140">
          <template #default="{ row }">
            <template v-if="row.won">
              <el-tag type="success" size="small">是</el-tag>
              <span style="margin-left:6px;">¥{{ fmtMoney(row.deal_amount) }}</span>
            </template>
            <span v-else style="color:#9ca3af;">否</span>
          </template>
        </el-table-column>

        <el-table-column label="下次跟进" width="150">
          <template #default="{ row }">
            <span :style="row.next_follow_at ? '' : 'color:#9ca3af;'">{{ fmtTime(row.next_follow_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>

        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ============ 新建线索对话框 ============ -->
    <el-dialog v-model="addVisible" title="➕ 新建线索" width="600px">
      <el-form ref="addFormRef" :model="addForm" :rules="addRules" label-width="100px">
        <el-form-item label="姓名" prop="name">
          <el-input v-model="addForm.name" placeholder="必填，如 张先生" />
        </el-form-item>

        <el-form-item label="电话" prop="phone">
          <el-input v-model="addForm.phone" placeholder="选填，如 13800000000" />
        </el-form-item>

        <el-form-item label="公司" prop="company">
          <el-input v-model="addForm.company" placeholder="选填" />
        </el-form-item>

        <el-form-item label="来源" prop="source">
          <el-select v-model="addForm.source" style="width:100%;">
            <el-option v-for="s in SOURCES" :key="s.value" :value="s.value" :label="s.label" />
          </el-select>
        </el-form-item>

        <el-form-item label="需求描述" prop="need_desc">
          <el-input
            v-model="addForm.need_desc"
            type="textarea"
            :rows="3"
            placeholder="客户想解决什么问题 / 关注什么产品"
          />
        </el-form-item>

        <el-form-item label="优先级" prop="priority">
          <el-select v-model="addForm.priority" style="width:100%;">
            <el-option value="high" label="🔴 高" />
            <el-option value="normal" label="🟡 中" />
            <el-option value="low" label="🟢 低" />
          </el-select>
        </el-form-item>

        <el-form-item label="负责人" prop="owner_id">
          <el-select
            v-model="addForm.owner_id"
            clearable
            placeholder="留空 = 自动分配（按进行中线索数挑销售）"
            style="width:100%;"
          >
            <el-option
              v-for="p in pool" :key="p.id" :value="p.id"
              :label="p.name + (p.job ? '（' + p.job + '）' : '')"
            />
          </el-select>
          <div class="hint" style="margin-top:4px;">
            指定负责人需要 lead.assign 权限，否则后端会拒绝。
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doAdd">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 线索详情抽屉 ============ -->
    <el-drawer
      v-model="drawerVisible"
      :title="detail ? ('🎯 ' + detail.name + ' · ' + detail.lead_id) : '线索详情'"
      size="700px"
    >
      <div v-if="detailLoading" style="text-align:center; padding:60px;">
        <el-icon class="is-loading" :size="36"><Loading /></el-icon>
      </div>

      <div v-else-if="detail">
        <!-- 状态徽标 -->
        <div style="margin-bottom:16px;">
          <el-tag :type="stageTagType(detail.stage)" size="large">{{ detail.stage_label }}</el-tag>
          <el-tag size="large" effect="plain" style="margin-left:8px;">
            优先级：{{ detail.priority_label || "-" }}
          </el-tag>
          <el-tag v-if="detail.won" type="success" size="large" style="margin-left:8px;">✅ 已成交</el-tag>
          <el-tag v-if="detail.lost_at" type="danger" size="large" style="margin-left:8px;">❌ 已流失</el-tag>
          <el-button size="small" :icon="Refresh" style="float:right;" @click="loadDetail(detail.lead_id, true)">
            刷新
          </el-button>
        </div>

        <!-- 基本信息 -->
        <el-descriptions title="基本信息" :column="2" border>
          <el-descriptions-item label="线索号">{{ detail.lead_id }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ detail.source_label || "-" }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ detail.phone || "-" }}</el-descriptions-item>
          <el-descriptions-item label="公司">{{ detail.company || "-" }}</el-descriptions-item>
          <el-descriptions-item label="负责人">
            {{ detail.owner_name || "未分配" }}
          </el-descriptions-item>
          <el-descriptions-item label="关联客户">{{ detail.customer_id || "-" }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ fmtTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="最近更新">{{ fmtTime(detail.updated_at) }}</el-descriptions-item>
          <el-descriptions-item label="需求描述" :span="2">
            <span style="white-space:pre-wrap;">{{ detail.need_desc || "-" }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.lost_reason" label="流失原因" :span="2">
            <span style="color:#ef4444;">{{ detail.lost_reason }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.quote_at" label="报价时间" :span="2">
            {{ fmtTime(detail.quote_at) }} · ¥{{ fmtMoney(detail.quote_amount) }}
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.won_at" label="成交时间" :span="2">
            {{ fmtTime(detail.won_at) }} · ¥{{ fmtMoney(detail.deal_amount) }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- ============ 阶段流转：只列出当前阶段允许的下一步 ============ -->
        <el-divider content-position="left">阶段流转</el-divider>
        <div>
          <el-button
            v-for="s in nextStages" :key="s"
            :type="s === 'lost' ? 'danger' : 'primary'"
            :plain="s === 'lost'"
            size="small"
            style="margin:0 8px 8px 0;"
            :loading="savingDetail"
            @click="changeStage(s)"
          >
            → {{ stageLabel(s) }}
          </el-button>
          <span v-if="!nextStages.length" class="hint">
            当前为终态「{{ stageLabel(detail.stage) }}」，没有可流转的下一阶段。
          </span>
        </div>

        <!-- ============ 可编辑操作区 ============ -->
        <el-divider content-position="left">编辑信息</el-divider>
        <el-form :model="editForm" label-width="110px">
          <el-form-item label="报价金额（元）">
            <el-input-number v-model="editForm.quote_amount" :min="0" :step="100" style="width:200px;" />
            <span class="hint" style="margin-left:8px;">填 0 表示未报价</span>
          </el-form-item>

          <el-form-item label="成交金额（元）">
            <el-input-number v-model="editForm.deal_amount" :min="0" :step="100" style="width:200px;" />
            <span class="hint" style="margin-left:8px;">大于 0 会自动标记为已成交</span>
          </el-form-item>

          <el-form-item label="负责人">
            <el-select
              v-model="editForm.owner_id"
              clearable
              placeholder="未分配"
              style="width:220px;"
            >
              <el-option
                v-for="p in pool" :key="p.id" :value="p.id"
                :label="p.name + (p.job ? '（' + p.job + '）' : '')"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="下次跟进时间">
            <el-date-picker
              v-model="editForm.next_follow_at"
              type="datetime"
              placeholder="选择下次跟进时间"
              value-format="YYYY-MM-DDTHH:mm:ss"
              style="width:220px;"
            />
          </el-form-item>

          <el-form-item label="下一步计划">
            <el-input v-model="editForm.next_action" placeholder="如：周五前发送正式报价单" />
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="savingDetail" @click="saveEdit">保存修改</el-button>
            <el-button
              v-if="detail.stage !== 'lost'"
              type="danger"
              plain
              :loading="savingDetail"
              @click="markLost"
            >
              标记流失
            </el-button>
          </el-form-item>
        </el-form>

        <!-- ============ 添加跟进 ============ -->
        <el-divider content-position="left">添加跟进</el-divider>
        <el-input
          v-model="followupForm.content"
          type="textarea"
          :rows="2"
          placeholder="记录本次沟通内容，如：电话沟通，客户对报价有异议，要求打 9 折"
        />
        <div style="margin-top:8px; display:flex; gap:8px;">
          <el-select v-model="followupForm.channel" style="width:150px;">
            <el-option v-for="c in CHANNELS" :key="c.value" :value="c.value" :label="c.label" />
          </el-select>
          <el-button type="primary" :loading="addingFollowup" @click="doAddFollowup">提交跟进</el-button>
        </div>

        <!-- ============ 跟进时间线 ============ -->
        <el-divider content-position="left">跟进时间线</el-divider>
        <el-timeline v-if="followupsAsc.length">
          <el-timeline-item
            v-for="f in followupsAsc"
            :key="f.id"
            :timestamp="fmtTime(f.created_at)"
            placement="top"
            :type="timelineType(f)"
          >
            <div style="font-weight:600;">
              {{ f.user_name || "系统" }}
              <el-tag size="small" effect="plain" style="margin-left:6px;">
                {{ channelLabel(f.channel) }}
              </el-tag>
            </div>
            <div v-if="f.content" style="margin-top:4px; white-space:pre-wrap;">{{ f.content }}</div>
            <div v-if="f.stage_to_label" class="tl-extra">
              🔀 阶段变化：{{ f.stage_from ? stageLabel(f.stage_from) : "—" }} → {{ f.stage_to_label }}
            </div>
            <div v-if="f.quote_change" class="tl-extra" style="color:#f59e0b;">
              💰 报价变化：{{ f.quote_change }}
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无跟进记录" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Search, Refresh, Loading } from "@element-plus/icons-vue";
// 统一用共享客户端：它自带 Authorization 请求头，并会在 401 时跳登录。
// 千万不要在本页 axios.create() —— 自建实例不带 token，会得到 401「未登录」。
// toastError 也来自这里：拦截器已经统一弹过一次错误，用它做去重，
// 避免同一个错误弹两条一模一样的提示。
import api, { toastError } from "../api";

// ============================================================
// 常量：与后端 app/db/models/lead.py 保持一致
// ============================================================
/** 全部阶段（顺序与后端 STAGES 一致） */
const ALL_STAGES = ["new", "contacted", "quoted", "negotiating", "won", "lost"];
/** 漏斗展示的五个阶段（不含流失） */
const FUNNEL_STAGES = ["new", "contacted", "quoted", "negotiating", "won"];

/**
 * 阶段状态机（后端 STAGE_FLOW 的本地镜像）。
 * 只把「当前阶段允许的下一阶段」渲染成按钮，从源头避免非法流转；
 * 万一仍然非法（例如后端规则调整过），PATCH 会返回 400 + 中文 detail，
 * 由 catch 里的 ElMessage.error 原样弹出。
 */
const STAGE_FLOW = {
  new: ["contacted", "lost"],
  contacted: ["quoted", "negotiating", "won", "lost"],
  quoted: ["negotiating", "won", "lost"],
  negotiating: ["won", "lost"],
  won: [],
  lost: ["contacted"], // 允许「重新激活」已流失线索
};

/** 阶段中文名兜底（正常情况下用后端 /leads/stats 返回的 stage_labels） */
const LOCAL_STAGE_LABELS = {
  new: "新建",
  contacted: "已联系",
  quoted: "已报价",
  negotiating: "谈判中",
  won: "已成交",
  lost: "已流失",
};

/** 来源选项（与后端 SOURCES / SOURCE_LABELS 一致） */
const SOURCES = [
  { value: "feishu", label: "飞书咨询" },
  { value: "phone", label: "电话" },
  { value: "referral", label: "转介绍" },
  { value: "website", label: "官网" },
  { value: "other", label: "其他" },
];

/** 跟进渠道（与后端 LeadFollowupRequest.channel 注释一致） */
const CHANNELS = [
  { value: "phone", label: "📞 电话" },
  { value: "feishu", label: "💬 飞书" },
  { value: "wechat", label: "🟢 微信" },
  { value: "meeting", label: "🤝 面谈" },
  { value: "other", label: "📝 其他" },
];

// ============================================================
// 小工具
// ============================================================
/** 金额千分位显示（整数元，平均成交额保留 2 位小数） */
function fmtMoney(v) {
  const n = Number(v || 0);
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/** ISO 时间 → YYYY-MM-DD HH:mm（不引第三方库，直接切字符串） */
function fmtTime(v) {
  if (!v) return "-";
  const s = String(v).replace("T", " ");
  return s.length >= 16 ? s.slice(0, 16) : s;
}

/** 阶段中文名：优先用后端 stage_labels，回退到本地表 */
function stageLabel(stage) {
  if (!stage) return "-";
  return (stats.value.stage_labels && stats.value.stage_labels[stage])
    || LOCAL_STAGE_LABELS[stage]
    || stage;
}

/** 阶段标签配色：won=success、lost=danger、negotiating=warning */
function stageTagType(stage) {
  return {
    new: "info",
    contacted: "primary",
    quoted: "primary",
    negotiating: "warning",
    won: "success",
    lost: "danger",
  }[stage] || "info";
}

/** 漏斗进度条颜色 */
function stageColor(stage) {
  return {
    new: "#9ca3af",
    contacted: "#6366f1",
    quoted: "#7c3aed",
    negotiating: "#f59e0b",
    won: "#22c55e",
  }[stage] || "#9ca3af";
}

/** 渠道中文名 */
function channelLabel(ch) {
  const c = CHANNELS.find((x) => x.value === ch);
  return c ? c.label : (ch || "其他");
}

/** 时间线节点颜色：成交绿 / 流失红 / 其他紫 */
function timelineType(f) {
  if (f.stage_to === "won") return "success";
  if (f.stage_to === "lost") return "danger";
  return "primary";
}

// ============================================================
// 列表 / 统计 / 销售池
// ============================================================
const leads = ref([]);
const loading = ref(false);
const pool = ref([]);

const filters = reactive({ stage: "", owner_id: null, keyword: "", only_open: false });

const stats = ref({
  total: 0,
  open: 0,
  won: 0,
  lost: 0,
  conversion_rate: 0,
  quoted_count: 0,
  quote_sum: 0,
  deal_sum: 0,
  avg_deal: 0,
  by_stage: { new: 0, contacted: 0, quoted: 0, negotiating: 0, won: 0, lost: 0 },
  stage_labels: {},
  owners: [],
});

const byStage = computed(() => stats.value.by_stage || {});

/** 漏斗分母：取五个阶段里的最大值，这样占比最高的一档刚好铺满 */
const funnelMax = computed(() => {
  const bs = byStage.value;
  return Math.max(1, ...FUNNEL_STAGES.map((s) => Number(bs[s] || 0)));
});

function funnelPercent(stage) {
  const n = Number(byStage.value[stage] || 0);
  return Math.round((n / funnelMax.value) * 1000) / 10;
}

function funnelCount(stage) {
  return Number(byStage.value[stage] || 0);
}

/** 拉取线索列表 */
async function load() {
  loading.value = true;
  try {
    const params = {};
    if (filters.stage) params.stage = filters.stage;
    if (filters.owner_id) params.owner_id = filters.owner_id;
    if (filters.keyword && filters.keyword.trim()) params.keyword = filters.keyword.trim();
    if (filters.only_open) params.only_open = true;
    leads.value = await api.get("/leads", { params });
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  } finally {
    loading.value = false;
  }
}

/** 拉取漏斗统计 */
async function loadStats() {
  try {
    stats.value = await api.get("/leads/stats");
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  }
}

/** 拉取销售池（负责人下拉数据源） */
async function loadPool() {
  try {
    const r = await api.get("/leads/pool");
    pool.value = r.pool || [];
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  }
}

async function refreshAll() {
  await Promise.all([load(), loadStats(), loadPool()]);
}

// ============================================================
// 新建线索
// ============================================================
const addVisible = ref(false);
const saving = ref(false);
const addFormRef = ref(null);

const addForm = reactive({
  name: "",
  phone: "",
  company: "",
  source: "other",
  need_desc: "",
  priority: "normal",
  owner_id: null,
});

const addRules = {
  name: [
    { required: true, message: "请输入客户姓名", trigger: "blur" },
    { min: 1, max: 64, message: "姓名长度 1 ~ 64 个字符", trigger: "blur" },
  ],
};

function openAdd() {
  Object.assign(addForm, {
    name: "",
    phone: "",
    company: "",
    source: "other",
    need_desc: "",
    priority: "normal",
    owner_id: null,
  });
  addVisible.value = true;
}

async function doAdd() {
  // 表单校验：姓名必填
  try {
    await addFormRef.value.validate();
  } catch {
    return;
  }

  saving.value = true;
  try {
    const payload = {
      name: (addForm.name || "").trim(),
      phone: (addForm.phone || "").trim(),
      company: (addForm.company || "").trim(),
      source: addForm.source,
      need_desc: (addForm.need_desc || "").trim(),
      priority: addForm.priority,
    };
    // 留空即不传 owner_id —— 后端会自动按负载分配
    if (addForm.owner_id) payload.owner_id = addForm.owner_id;

    const r = await api.post("/leads", payload);
    if (r.auto_assigned) {
      ElMessage.success(`✅ 已创建 ${r.lead_id}，自动分配给 ${r.owner_name || "（无可用销售）"}`);
    } else {
      ElMessage.success(`✅ 已创建 ${r.lead_id}（负责人：${r.owner_name || "未分配"}）`);
    }
    addVisible.value = false;
    await Promise.all([load(), loadStats()]);
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  } finally {
    saving.value = false;
  }
}

// ============================================================
// 详情抽屉
// ============================================================
const drawerVisible = ref(false);
const detail = ref(null);
const detailLoading = ref(false);
const savingDetail = ref(false);
const addingFollowup = ref(false);

/** 可编辑字段（与详情里的表单双向绑定） */
const editForm = reactive({
  quote_amount: 0,
  deal_amount: 0,
  owner_id: null,
  next_action: "",
  next_follow_at: "",
});

const followupForm = reactive({ content: "", channel: "phone" });

/** 详情里允许流转的下一阶段 */
const nextStages = computed(() => STAGE_FLOW[(detail.value && detail.value.stage) || "new"] || []);

/** 时间线按「从早到晚」展示（后端返回的是倒序） */
const followupsAsc = computed(() => {
  const list = (detail.value && detail.value.followups) || [];
  return list.slice().reverse();
});

/** 负责人 id 归一化：null / undefined / "" / 0 一律视为「未分配」 */
function normId(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** 把详情里的值同步进编辑表单 */
function syncEditForm(d) {
  editForm.quote_amount = d.quote_amount || 0;
  editForm.deal_amount = d.deal_amount || 0;
  editForm.owner_id = normId(d.owner_id);
  editForm.next_action = d.next_action || "";
  editForm.next_follow_at = d.next_follow_at ? String(d.next_follow_at).slice(0, 19) : "";
}

/**
 * 加载详情（含跟进记录）。
 * silent=true 用于「保存后再刷新」，这样不会把整块内容切成 loading 骨架。
 */
async function loadDetail(leadId, silent = false) {
  if (!silent) detailLoading.value = true;
  try {
    const d = await api.get("/leads/" + leadId);
    detail.value = d;
    syncEditForm(d);
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  } finally {
    if (!silent) detailLoading.value = false;
  }
}

function openDetail(row) {
  drawerVisible.value = true;
  detail.value = null;
  followupForm.content = "";
  followupForm.channel = "phone";
  return loadDetail(row.lead_id);
}

/**
 * 统一的 PATCH 封装：成功后刷新详情 + 列表 + 统计。
 * 失败时把后端的中文 detail（如「不能从「新建」流转到「已成交」，可选：已联系、已流失」）
 * 用 ElMessage.error 原样弹出来。
 */
async function doPatch(patch, okMsg) {
  savingDetail.value = true;
  try {
    await api.patch("/leads/" + detail.value.lead_id, patch);
    ElMessage.success("✅ " + (okMsg || "已保存"));
    await loadDetail(detail.value.lead_id, true);
    await Promise.all([load(), loadStats()]);
    return true;
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
    return false;
  } finally {
    savingDetail.value = false;
  }
}

/** 阶段切换：lost 走「必须填原因」的分支 */
async function changeStage(stage) {
  if (stage === "lost") return markLost();
  await doPatch({ stage }, `已流转到「${stageLabel(stage)}」`);
}

/** 标记流失：先弹输入框要求填原因，再连同 stage=lost 一起提交 */
async function markLost() {
  let reason = "";
  try {
    const r = await ElMessageBox.prompt(
      "请填写流失原因（必填）。提交后该线索会标记为「已流失」，并记入跟进时间线。",
      "标记流失",
      {
        confirmButtonText: "确认流失",
        cancelButtonText: "取消",
        inputPlaceholder: "如：预算不足 / 已选择竞品 / 联系不上",
        inputValidator: (v) => (v && v.trim() ? true : "流失原因必填"),
      }
    );
    reason = (r.value || "").trim();
  } catch {
    return; // 用户取消
  }
  await doPatch({ stage: "lost", lost_reason: reason }, "已标记流失");
}

/**
 * 组装「只包含改动字段」的 patch。
 * 这样即使用户没有 lead.assign 权限，只要没动负责人就不会触发 403。
 */
function buildPatch() {
  const d = detail.value;
  const p = {};

  if (Number(editForm.quote_amount || 0) !== Number(d.quote_amount || 0)) {
    p.quote_amount = Number(editForm.quote_amount || 0);
  }
  if (Number(editForm.deal_amount || 0) !== Number(d.deal_amount || 0)) {
    p.deal_amount = Number(editForm.deal_amount || 0);
  }

  // 下拉清空时 Element Plus 可能给出 null / undefined / ""，统一按「未分配」处理；
  // 只有真的变了才放进 patch，避免没有 lead.assign 权限的人被 403 挡住其它字段的保存。
  const ownerId = normId(editForm.owner_id);
  if (ownerId !== normId(d.owner_id)) {
    p.owner_id = ownerId;
  }

  const nextAction = (editForm.next_action || "").trim();
  if (nextAction !== (d.next_action || "")) p.next_action = nextAction;

  const nf = editForm.next_follow_at || "";
  const oldNf = d.next_follow_at ? String(d.next_follow_at).slice(0, 19) : "";
  if (nf !== oldNf) p.next_follow_at = nf;

  return p;
}

async function saveEdit() {
  const patch = buildPatch();
  if (!Object.keys(patch).length) {
    ElMessage.info("没有检测到修改");
    return;
  }
  await doPatch(patch, "已保存修改");
}

/** 添加一条手动跟进记录 */
async function doAddFollowup() {
  const content = (followupForm.content || "").trim();
  if (!content) return ElMessage.warning("请填写跟进内容");

  addingFollowup.value = true;
  try {
    await api.post("/leads/" + detail.value.lead_id + "/followups", {
      content,
      channel: followupForm.channel,
    });
    ElMessage.success("✅ 已添加跟进");
    followupForm.content = "";
    await loadDetail(detail.value.lead_id, true);
    await Promise.all([load(), loadStats()]);
  } catch (e) {
    toastError(e?.response?.data?.detail || e.message);
  } finally {
    addingFollowup.value = false;
  }
}

onMounted(refreshAll);
</script>

<style scoped>
.stat-card { text-align: center; padding: 4px; }

.money-line {
  margin: -4px 0 16px;
  font-size: 13px;
  color: #6b7280;
}
.money-line .dot { margin: 0 6px; color: #d1d5db; }

/* 漏斗行：阶段名 + 进度条 + 数量，三栏对齐 */
.funnel-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.funnel-name {
  width: 72px;
  flex-shrink: 0;
  font-size: 13px;
  color: #374151;
}
.funnel-count {
  width: 48px;
  flex-shrink: 0;
  text-align: right;
  font-weight: 600;
  color: #4f46e5;
}

.hint { font-size: 12px; color: #9ca3af; }
.sub-text { font-size: 12px; color: #9ca3af; }
.tl-extra { margin-top: 4px; font-size: 12px; color: #6b7280; }
</style>
