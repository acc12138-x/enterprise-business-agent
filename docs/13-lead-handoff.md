# 转人工客服 + 客户线索（CRM）

本文说明两块功能的**设计、数据模型、状态机与使用方式**。

- **转人工客服**：把「工作流中断」升级为一条**可运营的工单**（谁接、接没接、接了多久）
- **客户线索**：把「陌生咨询 → 成交」做成一条**有归属人、有阶段、有下一步**的可追踪链路

---

## 一、转人工客服

### 1.1 改造前的状态（了解动机）

改造前「转人工」只有骨架：

```
用户说「转人工」
  → 意图命中 human
  → graph.py 的 HITL_DIRECT_INTENTS 直接路由到 hitl_gate
  → interrupt() 挂起工作流
  → 用户回「同意」→ resume → 回一句「✅ 已确认转人工，客服稍后接入。」
```

**问题**：`gateway/skills/notify_human.py` 只是个返回 dict 的空壳 ——
**没有任何人真的收到通知**，也没有坐席、队列、接管的概念。
用户以为有人在处理，其实没有。

### 1.2 改造后的完整链路

```
用户说「转人工」
   ↓
工作流跑完（intent=human / complaint，或规则声明 need_human）
   ↓
入口层 after_turn() 判断需要转人工
   ├── create_handoff()   建工单（status=queued）
   ├── 落 conversation_messages（用户消息 + AI 回复 + 系统提示）
   └── dispatch(handoff_created) → 私聊客服群/坐席
   ↓
坐席在「人工客服台」点【认领】
   ├── status=claimed，记录 claimed_by / claimed_at
   ├── 通知用户「客服已接入」
   └── ★ 此后该会话 is_ai_muted() 为真
   ↓
用户后续每条消息 → before_turn() 发现已被接管
   → 【不启动工作流】，只落库 + 回「人工客服正在为您服务」
   ↓
坐席回复 → reply() → 落库（role=human_agent）+ 直发飞书
   ↓
坐席点【结束接管】→ close()
   ├── status=closed
   ├── 通知用户「已转回智能助手」
   └── 插入一条 system 消息（避免用户以为断线）
   ↓
AI 恢复
```

### 1.3 状态机

```
queued（待接入）──认领──► claimed（已接管）──结束──► closed（已结束）
   │
   └── 超过 handoff_timeout_seconds（默认 300 秒）无人认领
        → timeout（已超时）+ 升级通知主管 + 告知用户
```

### 1.4 AI 静默是怎么实现的（关键设计）

**在入站入口短路，而不是在图里加节点。**

理由：接管期间**根本不该启动工作流** ——
既浪费 LLM 调用，又会把 checkpoint 状态搅乱。

```python
# openai_compat.py / stream.py 的入口
muted = handoff_service.before_turn(thread_id, user_msg, sender_open_id)
if muted:
    # 不跑图，直接回「人工客服正在为您服务」
    ...
```

`before_turn()` 顺带落库用户消息；`after_turn()` 落库 AI 回复并判断是否需要转人工。

> ⚠️ **这是本次改动里风险最高的一处**（碰到线上飞书链路）。
> 缓解：短路逻辑**只在 status=claimed 时生效**，其余路径一行不改。

### 1.5 通知配置

事件路由在 `app/services/feishu_router.py`：

| 事件 | 私聊给 | 广播到群 |
|---|---|---|
| `handoff_created` | `agent` + `supervisor` | `cs_group` |
| `handoff_timeout` | `supervisor` | `cs_group` + `supervisor_group` |

群 webhook 走 `.env`（不进仓库）：

```bash
# .env
FEISHU_WEBHOOK_CS_GROUP=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
```

未配置的群会被自动跳过，不影响私聊通知。

### 1.6 数据模型

**`human_handoffs`（转人工工单）**

| 字段 | 说明 |
|---|---|
| `handoff_id` | `HT-YYYYMMDD-0001` |
| `thread_id` | 会话 id（关联 LangGraph checkpoint） |
| `sender_open_id` | 用户 |
| `customer_id` / `lead_id` | 关联的客户 / 线索（可空） |
| `trigger` | `human_intent` / `complaint` / `rule_need_human` / `need_human_action` |
| `reason` | 给坐席看的原因 |
| `summary` | 自动截取的最近几轮对话 |
| `last_user_msg` | 最后一条用户消息 |
| `status` | `queued` / `claimed` / `closed` / `timeout` |
| `claimed_by` / `claimed_by_name` / `claimed_at` | 谁接的、什么时候 |
| `closed_at` / `close_note` | 结单时间与说明 |
| `notify_result` | 通知结果 JSON（排查「到底通知到谁了」） |

**`conversation_messages`（会话消息）**

入站/出站**按条**落库（**不是逐 token**，否则 IO 会爆）。
`role` 取值：`user` / `assistant` / `human_agent` / `system`。

> 为什么单独建表而不是直接读 checkpoint：
> 坐席台、会话监控、常见问题分析需要**统一的、可查询的**消息表；
> 且接管期间不跑图，消息本来就进不了 checkpoint。

### 1.7 API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/handoffs?status=&limit=` | `handoff.view` | 队列列表 |
| GET | `/handoffs/stats` | `handoff.view` | 状态分布 / 平均响应时长 / 按坐席 |
| GET | `/handoffs/{id}` | `handoff.view` | 详情 + 完整消息流 |
| POST | `/handoffs` | `handoff.view` | 手动建单（后台兜底） |
| POST | `/handoffs/{id}/claim` | `handoff.claim` | 认领（他人已认领会失败） |
| POST | `/handoffs/{id}/reply` | `handoff.reply` | 以人工身份回复并推送 |
| POST | `/handoffs/{id}/close` | `handoff.claim` | 结束接管、切回 AI |

> ⚠️ `/handoffs/stats` 必须声明在 `/handoffs/{handoff_id}` **之前**，
> 否则 `stats` 会被当成 id 匹配掉。

### 1.8 使用方式（管理后台「人工客服台」）

三栏布局：**左队列 / 中消息流 / 右上下文**。

1. 顶部看板：待接入 / 我接管的 / 已结束 / 已超时 / 平均响应时长
2. 左侧点开一条 → 中间显示完整会话（接管前消息来自 checkpoint，接管后来自新表，按时间合并）
3. 点【认领】→ 回复框可用 → 输入后【发送】（`Ctrl+Enter`）
4. 处理完点【结束接管】→ 填写结单说明

> 页面用 **5 秒轮询**刷新队列（没有 WebSocket）。
> 刷新时不会打断正在输入的回复框，也不会取消当前选中的工单。

---

## 二、客户线索与任务进度

### 2.1 要回答的四个问题

| 业务问题 | 对应字段 |
|---|---|
| 谁负责跟进？ | `owner_id` / `owner_name` |
| 聊到哪一步？ | `stage` |
| 报没报价？ | `quoted` / `quote_amount` / `quote_at` |
| 最后成没成交？ | `won` / `deal_amount` / `won_at` / `lost_at` / `lost_reason` |

### 2.2 阶段状态机

```
new（新建）→ contacted（已联系）→ quoted（已报价）→ negotiating（谈判中）
                                                        ├→ won（已成交）✅ 终态
                                                        └→ lost（已流失）❌ 终态
```

非法流转会被拒绝并返回中文提示，例如：

```
不能从「新建」流转到「已成交」，可选：已联系、已流失
```

- **标记流失必须填 `lost_reason`**，否则拒绝
- `lost` 允许重新激活回 `contacted`
- `deal_amount > 0` 会自动置为已成交

### 2.3 进度自动留痕（核心设计）

**每次阶段变化、报价变化、成交，都会自动写一条 `lead_followups`。**
所以「进度」不需要人工补记，天然可追溯。

实际产生的时间线示例：

```
线索创建，自动分配给 小林
阶段：新建 → 已联系
报价：0 → 80000 元
阶段：已联系 → 已成交
```

### 2.4 归属与自动分配

- **销售池** = 拥有 `lead.edit` 权限的**在线**用户
  （不新建表、不复用 `engineers` —— 销售和工程师是两类人，用权限界定最省事）
- **自动分配排序**：
  1. 岗位含「销售」的人优先
     （否则新装系统里管理员 id 最小，会把线索全接走）
  2. 进行中线索数升序（负载均衡，复用工程师派单的思路）
  3. id 升序（保证结果稳定可复现）

### 2.5 AI 自动捕获线索

`app/intent/data/intents.yaml` 新增 `lead` 意图（优先级 150）：

```
关键词：合作 / 采购 / 批量 / 批发 / 代理 / 加盟 / 经销 / 报价 / 询价 /
        商务 / 洽谈 / 长期合作 / 供应商 / 渠道 / 大客户 / 企业采购 / 产品价格
正则：  批量|大量|长期 + 采购|订购|购买|合作
        （报价|询价|价格）+（多少|怎么|方案|单）
        （寻求|想|希望|有意）+ 合作
        （产品|设备|机器|系统|方案）+（价格|价位|报价）
```

命中后由 `app/workflows/nodes/lead_capture.py`：

1. 从原话里正则抽取**手机号**与**公司名**
2. 建线索（`source=feishu`，记录 `source_thread_id` 便于回溯原始对话）
3. 自动分配销售并通知
4. 回确认话术；**缺联系方式时主动索要**

> **幂等**：同一会话已有未结束线索时不重复创建，只做确认回复。
> 可用 `LEAD_AUTO_CAPTURE=false` 关闭自动捕获。

**设成 `slots: []`（无必填槽位）是有意的** —— 命中即建，避免因为缺手机号而丢掉线索。

### 2.6 数据模型

**`leads`**

| 字段组 | 字段 |
|---|---|
| 标识 | `lead_id`(`LD-YYYYMMDD-0001`)、`customer_id`、`name`、`phone`、`company` |
| 归属 | `owner_id`、`owner_name` |
| 阶段 | `stage`、`priority` |
| 需求 | `source`、`need_desc` |
| 报价 | `quoted`、`quote_amount`(元)、`quote_at` |
| 成交 | `won`、`deal_amount`(元)、`won_at`、`lost_at`、`lost_reason` |
| 下一步 | `next_action`、`next_follow_at` |
| 溯源 | `source_thread_id`、`source_msg` |

**`lead_followups`（跟进记录）**

`lead_id` / `user_id` / `user_name` / `channel` / `content` /
`stage_from` → `stage_to` / `quote_change` / `created_at`

**`customers` 扩展**：`owner_id` / `owner_name` / `lead_id`（客户归属与来源线索）

> ⚠️ `create_all` **不会给已存在的表加列**，所以必须有 `scripts/migrate_crm.py`
> 手写 `ALTER TABLE`。该脚本**幂等**，可重复执行。

### 2.7 API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/leads?stage=&owner_id=&keyword=&only_open=` | `lead.view` | 列表 |
| GET | `/leads/stats` | `lead.view` | 漏斗统计 |
| GET | `/leads/pool` | `lead.view` | 销售池（负责人下拉） |
| POST | `/leads` | `lead.edit` | 新建（指定负责人需 `lead.assign`） |
| GET | `/leads/{id}` | `lead.view` | 详情 + 跟进时间线 |
| PATCH | `/leads/{id}` | `lead.edit` | 更新（改负责人需 `lead.assign`） |
| POST | `/leads/{id}/followups` | `lead.edit` | 追加跟进记录 |

`/leads/stats` 返回：

```json
{
  "total": 12, "open": 5, "won": 4, "lost": 3,
  "by_stage": {"new":2,"contacted":1,"quoted":1,"negotiating":1,"won":4,"lost":3},
  "conversion_rate": 57.1, "quoted_count": 6,
  "quote_sum": 320000, "deal_sum": 280000, "avg_deal": 70000.0,
  "owners": [{"owner_name":"小林","open":2,"won":3,"lost":1,"total":6,"deal_amount":200000}]
}
```

### 2.8 使用方式（管理后台「线索管理」）

1. 顶部四张卡：总线索 / 进行中 / 已成交 / 成交率（%），下附报价总额、成交总额、平均成交额
2. 漏斗条：按 `新建 → 已联系 → 已报价 → 谈判中 → 已成交` 展示各阶段数量
3. 筛选：阶段 / 负责人 / 关键词 / 只看进行中
4. 表格列：线索号、客户/公司、电话、来源、**负责人**、**阶段**、**已报价**、**已成交**、下次跟进、创建时间
5. 点【详情】打开抽屉：基本信息 + 可改阶段/报价/成交/负责人/下一步 + **跟进时间线** + 添加跟进

---

## 三、权限点

新增 6 个权限点（总数 21 → **27**）：

| 权限点 | 说明 | admin | supervisor | agent | engineer |
|---|---|---|---|---|---|
| `lead.view` | 查看线索 | ✅ | ✅ | ✅ | — |
| `lead.edit` | 编辑线索/跟进 | ✅ | ✅ | ✅ | — |
| `lead.assign` | 分配线索负责人 | ✅ | ✅ | — | — |
| `handoff.view` | 查看转人工队列 | ✅ | ✅ | ✅ | — |
| `handoff.claim` | 认领/结束接管 | ✅ | ✅ | ✅ | — |
| `handoff.reply` | 以人工身份回复 | ✅ | ✅ | ✅ | — |

> **客服（`agent`）就是坐席** —— 这个角色在本系统里的语义正是"客服"，
> 所以直接给它加 `handoff.*` 与 `lead.*`，**不新建角色、不新建表**。

> ✅ 权限从 DB **每请求实时重算**（`deps.py` → `get_user_by_id` → `effective_permissions`），
> 所以**加权限点不需要用户重新登录**，改代码 + 重启即可。

---

## 四、部署与迁移

```bash
# 1) 备份（务必）
cp data/app.db data/app.db.bak-$(date +%Y%m%d-%H%M%S)

# 2) 迁移（幂等，可重复执行）
python scripts/migrate_crm.py --seed

# 3) 重启服务
docker compose -f docker-compose.prod.yml up -d --build
```

`migrate_crm.py` 做三件事：

1. 建新表 `leads` / `lead_followups` / `human_handoffs` / `conversation_messages`
2. 给 `customers` 补 `owner_id` / `owner_name` / `lead_id`（先探测列是否存在）
3. `--seed` 时补两个示例销售（小林 / 小陈），让自动分配有池子

---

## 五、排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 转人工后没人收到通知 | 坐席没配 `feishu_open_id`，或 `FEISHU_WEBHOOK_CS_GROUP` 未配置 | 看 `human_handoffs.notify_result` 与 `notifications` 表 |
| 坐席回复发送失败但界面成功 | `sent=false` 是「已落库但推送失败」，前端会提示 | 看 `conversation_messages.meta` 里的 error |
| 工单一直是 queued | 没有坐席在线（`users.status=online`）或有 `handoff.*` 权限的人 | 检查人员状态与角色 |
| AI 一直在说话，坐席插不上 | 会话没被 `claimed` | `is_ai_muted()` 只在 `claimed` 时为真 |
| 线索自动分配给了管理员 | 池子里没有岗位含「销售」的人 | 给销售建账号并设 `job=销售` |
| 同一会话建了一堆线索 | 前一条线索被标成 won/lost 后又说了「合作」 | 这是预期行为（未结束线索才去重） |
| `平均响应` 是负数 | `claimed_at - created_at` 写反了 | 已在 `handoff_service.stats()` 修正 |

---

## 六、测试覆盖

`tests/test_crm.py`（新增 17 个用例）：

- 权限：未登录访问 `/leads`、`/handoffs` 必须 401
- 线索：创建 + 自动分配、非法阶段流转被拒、流失必填原因、报价联动
  `quoted`、成交联动 `won`、**自动留痕 ≥ 4 条**、手动跟进、按阶段筛选、404
- 转人工：建单幂等、认领、**他人无法重复认领**、未认领不能回复、
  回复落库为 `human_agent`、结束插入系统提示、状态筛选、404
- **AI 静默**：`queued` 不静默 / `claimed` 静默 / `closed` 恢复
- **静默回话模式**：`never` / `first` / `always` / 未知值回退，
  以及回归守卫「任何模式下都**不能返回空串**」
- **闲置接管兜底**：闲置的被自动结束且 AI 恢复；**活跃中的不能被误关**

另有三个测试文件是这轮线上故障换来的：

| 文件 | 守什么 |
|---|---|
| `tests/test_graph_routing.py` | 路由函数返回的每个值都必须在条件边映射表里登记；对不依赖 LLM 的意图**真的 invoke 一次图** |
| `tests/test_thread_id.py` | 同一会话标识稳定映射；缺 `session_key` 时按发送者聚合（**绝不能随机**）；截断碰撞必须被识别 |
| `tests/test_hitl_decision.py` | 「同意/拒绝」的各种写法，含飞书**引用回复前缀**；普通提问不能被误判成决策 |

---

## 七、这一段踩过的坑（第二轮，全部来自真实环境）

第一轮（功能开发期）的坑见下方「五、排查」表。下面是**部署上线后**暴露出来的，
每一条都对应一次线上现象 —— 也正因为它们是真实环境才暴露的，
本地自测（结构性检查）发现不了。

### 7.1 新增意图后漏登记条件边映射表

**现象**：飞书里发「我们公司想谈长期合作，手机 138xxx」，
只显示网关那句 `⚠️ Something went wrong while processing your request.`；
而同一时间闲聊、报修、退款全部正常。

**根因**：给 lead 意图加了节点 `g.add_node("lead_capture_node", ...)`、
加了边 `g.add_edge("lead_capture_node", END)`、也改了
`route_after_slot` 的返回值，**唯独忘了把它登记进
`slot_filling` 的条件边 `path_map`**。LangGraph 在命中该路由时直接抛
`KeyError: 'lead_capture_node'` → 后端 500。

**为什么只有 lead 失败**：只有它走这条新路由。

**修复**：补进 `path_map`，并写清注释「`route_*` 返回的每个值都必须登记」。

**教训（最重要的一条）**：
> 当时的冒烟测试只断言了「节点存在于 `g.nodes`」——
> **"节点在不在图里"和"命中时能不能正确路由"是两件事**。
> 现在 `tests/test_graph_routing.py` 用两道防线守住：
> 静态扫 `graph.py` 校验路由表完整性 + 对 LLM-free 意图真的跑一遍图。

顺带修了测试自身的缺陷：会话 id 写死导致**不可重复运行**（上一轮的线索
会污染下一轮），改成每次 `uuid`。

### 7.2 会话线程号不稳定，HITL 断点恢复失效

**现象**：用户回「同意」确认转人工，收到的是
「抱歉，知识库中没有找到与您问题相关的内容」。

**排查**：日志显示同一段对话出现三个不同线程号 ——
`openclaw-oc_b9674...`、`openclaw-7a88f2c7`、`openclaw-ce973909`。
后两个 8 位 hex 正是 `uuid4().hex[:8]`。

**根因（两处）**：

```python
if not session_key:
    return f"openclaw-{uuid.uuid4().hex[:8]}"        # ① 随机 → 每次都是新会话
_session_map[key] = f"openclaw-{key[:20]}"           # ② 截断 → 可能碰撞
```

网关有时不传 `session_key`（实测**每次都不传**），于是那一轮被当成全新会话：
检测不到挂起的中断 → 重跑 AI → intent 落到 `qa` → 走 RAG → 无结果。

②的碰撞实测：`agent:main:feishu:group:oc_AAAA` 与 `...oc_BBBB`
**都映射到 `openclaw-agent:main:feishu:gr`**（前 20 字符全是固定前缀）。

**修复**：缺 `session_key` 时退化为按 **`sender:<open_id>`** 聚合；
保留原截断规则（不影响既有会话 id）但加反查表，发现碰撞就改用 md5 区分；
两条退化路径都打日志。

### 7.3 用空字符串表示「不回复」，被网关判定为生成失败

**现象**：接管期间用户每发一条都收到
`⚠️ Agent couldn't generate a response. Please try again.`

**根因**：以为「返回空串」＝「不说话」。实际上网关把**非空的 completion**
当成一次成功生成，`content=""` 会被判定为失败。

**而且这个坑会持续复现**：坐席认领后如果没人点「结束接管」，
该会话一直是 `claimed`，之后每条消息都进静默分支、每次都失败。

**修复**：静默统一返回 `SILENT = "\u200b"`（零宽空格，非空所以网关收下，
不可打印所以用户看不见），并加回归守卫断言「任何模式下都不能返回空串」。

### 7.4 坐席忘了点「结束接管」→ 会话被永久静音

**现象**：同上。坐席认领过、没结束，AI 再也不会回话。

**修复**：`patrol_timeouts()` 增加兜底 —— `claimed` 且
`HANDOFF_CLAIM_TTL_HOURS`（默认 4 小时）内**没有任何新消息**就自动结束并转回 AI。

> ⚠️ 判定依据是**该会话最后一条消息的时间**，不是 `human_handoffs.updated_at`：
> 坐席回复只写 `conversation_messages`，不碰 `human_handoffs`，
> 用 `updated_at` 会把**正在服务中**的会话误判成闲置。

### 7.5 飞书凭据的字段名对不上，且被静默忽略

**现象**：所有出站消息失败并报「App ID/Secret 未配置或获取 token 失败」，
但 `.env.production` 里明明写了 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，
启动和运行期**都没有任何报错**。

**根因**：`feishu_client` 优先读 `settings.feishu_app_id`，
但 `Settings` 里**只定义了 `openclaw_feishu_app_id`**；
又因为配了 `extra="ignore"`，写 `FEISHU_APP_ID` 会被 pydantic-settings
**静默忽略** —— 报错只说"未配置"，完全指不到原因。

**修复**：补上 `feishu_app_id` / `feishu_app_secret` 两个字段，两个名字都支持。

> ⚠️ 别被误导：这个故障**只影响出站**（通知发不出去），入站照常收得到 ——
> 现象上很像"后端坏了"，实际是发信环节没配好。
> 诊断用 `python scripts/feishu_doctor.py`。

### 7.6 总结：结构性检查 ≠ 行为验证

这轮所有 bug 都有同一个特征：**我改完只做了结构性检查**
（字段对不对、节点在不在、函数能不能 import），
**没有做行为验证**（真的跑一遍、真的发一条消息）。

对应的补救就是上面那三个新测试文件 —— 它们不检查"东西在不在"，
而是**真的调用一次**：真的 invoke 图、真的解析一次决策、真的走一遍线程映射。

---

## 八、这一段新增的配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 空 | 飞书自建应用凭据，**出站消息必需** |
| `HANDOFF_TIMEOUT_SECONDS` | `300` | 没人认领多久算超时（秒）|
| `HANDOFF_PATROL_INTERVAL` | `60` | 超时巡检间隔（秒）|
| `HANDOFF_CLAIM_TTL_HOURS` | `4` | 认领后无新消息多久自动结束接管（小时）|
| `HANDOFF_MUTED_MODE` | `never` | 接管期间回话策略：`never` / `first` / `always` |
| `LEAD_AUTO_CAPTURE` | `true` | 是否开启 AI 自动捕获线索 |
| `FEISHU_WEBHOOK_CS_GROUP` | 空 | 客服群 webhook（`handoff_created` / `handoff_timeout` 的群广播）|
