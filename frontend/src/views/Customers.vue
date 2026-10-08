<template>
  <div>
    <div class="page-title">👥 客户资产</div>

    <el-card shadow="never" style="margin-bottom:16px;">
      <el-form inline>
        <el-form-item label="关键词">
          <el-input
            v-model="filters.keyword"
            placeholder="客户ID / 姓名 / 手机号 / 邮箱"
            clearable
            style="width:240px;"
            @keyup.enter="load"
            @clear="load"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="VIP 等级">
          <el-select v-model="filters.vip_level" clearable placeholder="全部" style="width:120px;" @change="load">
            <el-option value="normal" label="普通" />
            <el-option value="silver" label="白银" />
            <el-option value="gold" label="黄金" />
            <el-option value="diamond" label="钻石" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级">
          <el-select v-model="filters.risk_level" clearable placeholder="全部" style="width:120px;" @change="load">
            <el-option value="normal" label="🟢 正常" />
            <el-option value="suspicious" label="🟠 疑似" />
            <el-option value="high_risk" label="🔴 高风险" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
          <el-button @click="openAdd">+ 新增客户</el-button>
          <el-button type="success" @click="openImport">📥 批量导入</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table :data="customers" v-loading="loading" stripe @row-click="openDetail" style="cursor:pointer;">
        <el-table-column label="客户 ID" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.customer_id }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="phone" label="手机号" width="130" />
        <el-table-column label="VIP" width="90">
          <template #default="{ row }">
            <el-tag :type="vipType(row.vip_level)" size="small">{{ vipText(row.vip_level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="110">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" size="small">
              {{ riskIcon(row.risk_level) }} {{ riskText(row.risk_level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_orders" label="订单" width="70" align="center" />
        <el-table-column prop="total_refunds" label="退款" width="70" align="center" />
        <el-table-column prop="total_complaints" label="投诉" width="70" align="center" />
        <el-table-column prop="total_tickets" label="工单" width="70" align="center" />
        <!-- 客户归属：谁负责这个客户；来源于哪条线索 -->
        <el-table-column label="负责人" width="110">
          <template #default="{ row }">
            <span v-if="row.owner_name">{{ row.owner_name }}</span>
            <span v-else style="color:#c0c4cc;">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="来源线索" width="150">
          <template #default="{ row }">
            <span v-if="row.lead_id" style="font-family:monospace; font-size:12px;">{{ row.lead_id }}</span>
            <span v-else style="color:#c0c4cc;">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click.stop="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawerVisible" :title="detail ? ('👤 ' + detail.name + ' (' + detail.customer_id + ')') : ''" size="820px">
      <div v-if="detailLoading" style="text-align:center; padding:60px;">
        <el-icon class="is-loading" :size="36"><Loading /></el-icon>
      </div>
      <div v-else-if="detail">
        <div style="margin-bottom:16px;">
          <el-tag :type="vipType(detail.vip_level)" size="large">{{ vipText(detail.vip_level) }}</el-tag>
          <el-tag :type="riskType(detail.risk_level)" size="large" style="margin-left:8px;">
            {{ riskIcon(detail.risk_level) }} {{ riskText(detail.risk_level) }} (风险分: {{ detail.risk_score }})
          </el-tag>
        </div>

        <el-descriptions title="基本信息" :column="2" border>
          <el-descriptions-item label="手机号">{{ detail.phone }}</el-descriptions-item>
          <el-descriptions-item label="邮箱">{{ detail.email || "-" }}</el-descriptions-item>
          <el-descriptions-item label="地址" :span="2">{{ detail.address || "-" }}</el-descriptions-item>
        </el-descriptions>

        <el-row :gutter="12" style="margin-top:16px;">
          <el-col :span="6"><el-statistic title="订单" :value="detail.total_orders" /></el-col>
          <el-col :span="6"><el-statistic title="退款" :value="detail.total_refunds" /></el-col>
          <el-col :span="6"><el-statistic title="投诉" :value="detail.total_complaints" /></el-col>
          <el-col :span="6"><el-statistic title="工单" :value="detail.total_tickets" /></el-col>
        </el-row>

        <el-alert v-if="riskDetail && riskDetail.reasons && riskDetail.reasons.length" type="warning" style="margin-top:16px;">
          <template #title>
            ⚠️ 风险原因：{{ riskDetail.reasons.join("、") }}
          </template>
        </el-alert>

        <el-divider />
        <el-tabs v-model="detailTab">
          <el-tab-pane label="📦 订单历史" name="orders">
            <el-table :data="customerOrders" size="small" stripe empty-text="无订单">
              <el-table-column prop="order_id" label="订单号" width="140" />
              <el-table-column prop="product_name" label="商品" />
              <el-table-column prop="amount" label="金额" width="90">
                <template #default="{ row }">¥{{ row.amount }}</template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="100" />
              <el-table-column label="保修" width="120">
                <template #default="{ row }">
                  <el-tag :type="row.in_warranty ? 'success' : 'info'" size="small">
                    {{ row.in_warranty ? "✅ 在保" : "已过保" }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="🎫 工单" name="tickets">
            <el-table :data="customerTickets" size="small" stripe empty-text="无工单">
              <el-table-column prop="ticket_id" label="工单号" width="140" />
              <el-table-column prop="ticket_type" label="类型" width="90" />
              <el-table-column prop="status" label="状态" width="100" />
              <el-table-column prop="assigned_to" label="工程师" width="90" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="💰 退款记录" name="refunds">
            <el-table :data="customerRefunds" size="small" stripe empty-text="无退款">
              <el-table-column prop="refund_id" label="退款单" width="140" />
              <el-table-column prop="amount" label="金额" width="100">
                <template #default="{ row }">¥{{ row.amount }}</template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="120" />
              <el-table-column prop="reason" label="原因" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>

    <!-- 新增客户对话框 -->
    <el-dialog v-model="addVisible" title="➕ 新增客户" width="500">
      <el-form :model="addForm" label-width="90px">
        <el-form-item label="姓名"><el-input v-model="addForm.name" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="addForm.phone" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="addForm.email" /></el-form-item>
        <el-form-item label="地址"><el-input v-model="addForm.address" /></el-form-item>
        <el-form-item label="VIP 等级">
          <el-select v-model="addForm.vip_level">
            <el-option value="normal" label="普通" />
            <el-option value="silver" label="白银" />
            <el-option value="gold" label="黄金" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" @click="doAdd">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 批量导入向导 ============ -->
    <el-dialog v-model="importVisible" title="📥 批量导入客户" width="900px" top="5vh">
      <el-steps :active="importStep" align-center finish-status="success" style="margin-bottom:24px;">
        <el-step title="上传文件" description="xlsx / xls / csv" />
        <el-step title="确认字段" description="调整列映射" />
        <el-step title="导入完成" description="查看结果" />
      </el-steps>

      <!-- Step 1: 上传 -->
      <div v-if="importStep === 0">
        <el-upload
          drag
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onImportFileChange"
          accept=".xlsx,.xls,.csv"
          style="width:100%;"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处 或 <em>点击选择</em></div>
          <template #tip>
            <div class="el-upload__tip">
              支持 xlsx / xls / csv；第一行需为列名
              <el-link type="primary" @click.stop="downloadTemplate" style="margin-left:12px;">
                📄 下载模板
              </el-link>
            </div>
          </template>
        </el-upload>
        <div v-if="importFileName" style="margin-top:12px; color:#4f46e5;">
          📎 {{ importFileName }}
        </div>
      </div>

      <!-- Step 2: 映射 + 预览 -->
      <div v-else-if="importStep === 1">
        <el-alert type="info" :closable="false" style="margin-bottom:12px;">
          <template #title>
            共 {{ importData.total_rows }} 行数据。系统已自动识别列名，如需修改请在下拉框中选择。
          </template>
        </el-alert>

        <div style="margin-bottom:16px;">
          <div style="font-weight:600; margin-bottom:8px;">字段映射</div>
          <el-table :data="mappingRows" size="small" border>
            <el-table-column prop="header" label="Excel 列名" width="180" />
            <el-table-column label="映射到系统字段">
              <template #default="{ row }">
                <el-select v-model="row.field" clearable placeholder="（忽略此列）" size="small" style="width:100%;">
                  <el-option
                    v-for="f in importData.field_defs || []"
                    :key="f.key"
                    :value="f.key"
                    :label="f.label + (f.required ? ' *' : '')"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.field" size="small" type="success">已映射</el-tag>
                <el-tag v-else size="small" type="info">忽略</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div>
          <div style="font-weight:600; margin-bottom:8px;">数据预览（前 10 行）</div>
          <el-table :data="importData.preview || []" size="small" max-height="240" border>
            <el-table-column
              v-for="f in mappedFieldDefs"
              :key="f.key"
              :prop="f.key"
              :label="f.label"
              :width="140"
            />
          </el-table>
        </div>
      </div>

      <!-- Step 3: 结果 -->
      <div v-else-if="importStep === 2">
        <el-result
          :icon="importResult.failed > 0 ? 'warning' : 'success'"
          :title="`导入完成`"
          :sub-title="`共 ${importResult.total} 行，成功 ${importResult.success}，跳过 ${importResult.skipped}，失败 ${importResult.failed}`"
        >
        </el-result>

        <el-row :gutter="12" style="margin-top:16px;">
          <el-col :span="6"><el-statistic title="总计" :value="importResult.total" /></el-col>
          <el-col :span="6"><el-statistic title="✅ 成功" :value="importResult.success" value-style="color:#16a34a;" /></el-col>
          <el-col :span="6"><el-statistic title="⏭️ 跳过" :value="importResult.skipped" value-style="color:#f59e0b;" /></el-col>
          <el-col :span="6"><el-statistic title="❌ 失败" :value="importResult.failed" value-style="color:#ef4444;" /></el-col>
        </el-row>

        <div v-if="importResult.errors && importResult.errors.length" style="margin-top:20px;">
          <div style="font-weight:600; margin-bottom:8px;">失败详情</div>
          <el-table :data="importResult.errors" size="small" border max-height="200">
            <el-table-column prop="row" label="行号" width="80" />
            <el-table-column prop="reason" label="原因" />
          </el-table>
        </div>
      </div>

      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <template v-if="importStep === 1">
          <el-button @click="importStep = 0">上一步</el-button>
          <el-button type="primary" :loading="importing" @click="doConfirmImport">确认导入</el-button>
        </template>
        <template v-else-if="importStep === 2">
          <el-button type="primary" @click="finishImport">完成并刷新列表</el-button>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { Loading, UploadFilled, Search } from "@element-plus/icons-vue";
// 统一用共享客户端：它自带 Authorization 请求头与统一错误提示。
// 千万不要在本页 axios.create() —— 自建实例不带 token，会得到 401「未登录」。
import api from "../api";

const customers = ref([]);
const loading = ref(false);
const filters = reactive({ vip_level: "", risk_level: "", keyword: "" });

function vipText(v) { return { normal: "普通", silver: "白银", gold: "黄金", diamond: "钻石" }[v] || v; }
function vipType(v) { return { normal: "info", silver: "", gold: "warning", diamond: "danger" }[v] || "info"; }
function riskText(v) { return { normal: "正常", suspicious: "疑似", high_risk: "高风险" }[v] || v; }
function riskIcon(v) { return { normal: "🟢", suspicious: "🟠", high_risk: "🔴" }[v] || "❔️"; }
function riskType(v) { return { normal: "success", suspicious: "warning", high_risk: "danger" }[v] || "info"; }

async function load() {
  loading.value = true;
  try {
    const params = {};
    if (filters.vip_level) params.vip_level = filters.vip_level;
    if (filters.risk_level) params.risk_level = filters.risk_level;
    if (filters.keyword && filters.keyword.trim()) params.keyword = filters.keyword.trim();
    customers.value = await api.get("/customers", { params });
  } finally { loading.value = false; }
}

// ============ 详情 ============
const drawerVisible = ref(false);
const detail = ref(null);
const detailLoading = ref(false);
const detailTab = ref("orders");
const customerOrders = ref([]);
const customerTickets = ref([]);
const customerRefunds = ref([]);
const riskDetail = ref(null);

async function openDetail(row) {
  drawerVisible.value = true;
  detailLoading.value = true;
  detailTab.value = "orders";
  try {
    const [d, o, t, r, risk] = await Promise.all([
      api.get("/customers/" + row.customer_id),
      api.get("/customers/" + row.customer_id + "/orders").catch(() => ({ items: [] })),
      api.get("/customers/" + row.customer_id + "/tickets").catch(() => ({ items: [] })),
      api.get("/customers/" + row.customer_id + "/refunds").catch(() => ({ items: [] })),
      api.get("/customers/" + row.customer_id + "/risk").catch(() => null),
    ]);
    detail.value = d;
    customerOrders.value = o.items || [];
    customerTickets.value = t.items || [];
    customerRefunds.value = r.items || [];
    riskDetail.value = risk;
  } finally { detailLoading.value = false; }
}

// ============ 新增 ============
const addVisible = ref(false);
const addForm = reactive({ name: "", phone: "", email: "", address: "", vip_level: "normal" });

function openAdd() {
  Object.assign(addForm, { name: "", phone: "", email: "", address: "", vip_level: "normal" });
  addVisible.value = true;
}

async function doAdd() {
  if (!addForm.name || !addForm.phone) return ElMessage.warning("姓名和手机号必填");
  await api.post("/customers", addForm);
  ElMessage.success("创建成功");
  addVisible.value = false;
  load();
}

// ============ 批量导入 ============
const importVisible = ref(false);
const importStep = ref(0);
const importFileName = ref("");
const importToken = ref("");
const importData = ref({});
const mappingRows = ref([]);
const importResult = ref({ total: 0, success: 0, skipped: 0, failed: 0, errors: [] });
const importing = ref(false);

const mappedFieldDefs = computed(() => {
  const keys = new Set(mappingRows.value.filter(r => r.field).map(r => r.field));
  return (importData.value.field_defs || []).filter(f => keys.has(f.key));
});

function openImport() {
  importVisible.value = true;
  importStep.value = 0;
  importFileName.value = "";
  importToken.value = "";
  importData.value = {};
  mappingRows.value = [];
  importResult.value = { total: 0, success: 0, skipped: 0, failed: 0, errors: [] };
}

function downloadTemplate() {
  window.open("/api/customers/import/template", "_blank");
}

async function onImportFileChange(file) {
  importFileName.value = file.name;
  const fd = new FormData();
  fd.append("file", file.raw);

  try {
    const d = await api.post("/customers/import/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    importToken.value = d.token;
    importData.value = d;
    // 生成 mapping rows
    mappingRows.value = (d.headers || []).map(h => ({
      header: h,
      field: d.auto_mapping[h] || "",
    }));
    importStep.value = 1;
  } catch (e) {
    // 拦截器提示
  }
}

async function doConfirmImport() {
  importing.value = true;
  try {
    const mapping = {};
    for (const row of mappingRows.value) {
      if (row.field) mapping[row.header] = row.field;
    }
    const r = await api.post("/customers/import/confirm", {
      token: importToken.value,
      mapping,
    });
    importResult.value = r;
    importStep.value = 2;
  } catch (e) {
    // 拦截器提示
  } finally {
    importing.value = false;
  }
}

function finishImport() {
  importVisible.value = false;
  load();
}

onMounted(load);
</script>
