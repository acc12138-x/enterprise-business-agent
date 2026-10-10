# OpenClaw Skill 与 AI 工作流

本文讲两块**容易被混淆**的东西，以及工作流的设计意图。

---

## 一、先分清两个 "Skill"

项目里有两种完全不同的东西都被叫过 "skill"，混在一起分析必然出错：

| | **OpenClaw Skill** | **图节点（业务能力）** |
|---|---|---|
| 是什么 | 暴露给**外部网关**的工具，带 JSON Schema | 工作流内部的执行单元 |
| 在哪 | `app/gateway/skills/` | `app/workflows/nodes/` |
| 谁调用 | **OpenClaw 网关**按 schema 调用 | **LangGraph** 按边路由 |
| 怎么发现 | `GET /admin/skills` 返回全部 schema | 图结构本身（`build_graph()`）|
| 数量 | **5 个** | **15 个** |

**方向也不同**：Skill 是「网关 → 后端」的**拉**；图节点是后端内部的**编排**。
转人工这个能力恰好两条路都沾 —— 也因此曾经出过一次不一致（见 §4.1）。

---

## 二、OpenClaw Skill 层（5 个）

注册表在 [`app/gateway/skills/__init__.py`](../app/gateway/skills/__init__.py)：

- `list_skill_schemas()` —— 全部 JSON Schema，供 `GET /admin/skills` 暴露给网关注册工具
- `invoke_skill(name, **kwargs)` —— 本地直调入口，用于自测

| Skill | 描述 | 实现 |
|---|---|---|
| `search_kb` | 在企业售后知识库中检索相关信息 | 走 `HybridRetriever`：混合召回 → **扩展到父块** → 余弦重排 → 返回带 score 的命中 |
| `query_order` | 根据手机号查询订单状态 | 走订单服务 |
| `create_ticket` | 创建售后工单**并自动分派**工程师 | 内置 `assign_engineer()`，与图里的派单算法共用逻辑 |
| `assign_ticket` | 改派工单给指定工程师（**需人工审批**）| schema 描述里就写明了要审批 |
| `notify_human` | 转人工客服 | 内部转调 `handoff_service.create_handoff()`（见 §4.1）|

> **Skill 的执行路径**：正式路径是网关按 schema 调过来；
> `invoke_skill()` 只是本地自测与后端内部复用，不是生产链路。

---

## 三、AI 工作流（15 节点）

### 3.1 全图

```
                            ┌──────────┐
                            │  intent  │ ← 入口：意图识别 + 槽位抽取
                            └────┬─────┘
              route_after_intent │
        ┌────────────────────────┴────────────────────────┐
        │ human / complaint                         其他意图
        ▼                                                 ▼
   ┌──────────┐                                   ┌──────────────┐
   │hitl_gate │                                   │ slot_filling │ ← 缺槽位则追问并 END
   └────┬─────┘                                   └──────┬───────┘
        │                             route_after_slot   │
        │     ┌──────────┬───────────┬───────────┬───────┴────┬──────────┐
        │     ▼          ▼           ▼           ▼            ▼          ▼
        │  chitchat  lead_capture  ticket   refund_apply  my_tickets  engineer_query
        │  (闲聊)    (线索捕获)    _node      (退款)        _node       (查工程师)
        │     │          │           │           │            │          │
        │     └──────────┴───────────┴───────────┴────────────┴──────────┘
        │                     这些节点直接 END（不经 HITL）
        │
        │            ┌──────────────────┐
        └───────────▶│ context_collect  │ ← 聚合订单 / 物流 / 商品 / 客户
                     └────────┬─────────┘
              route_after_context │
                     ┌──────────┴─────────┐
                     ▼                    ▼
              ┌────────────┐      ┌────────────┐
              │ order_node │      │ rule_match │ ← 声明式规则引擎
              └─────┬──────┘      └──────┬─────┘
                    │         route_after_rule │
                    │            ┌───────────┴──────────┐
                    │            ▼                      ▼
                    │      ┌─────────────┐        ┌────────────┐
                    │      │ action_exec │        │ rag_search │
                    │      └──────┬──────┘        └──────┬─────┘
                    │             │                      ▼
                    │             │               ┌───────────┐
                    │             │               │ generate  │
                    │             │               └─────┬─────┘
                    └─────────────┴─────────────────────┘
                                  ▼
                            ┌──────────┐
                            │hitl_gate │ ← 需人工确认的路径统一收口
                            └────┬─────┘
                                 ▼
                                END
```

### 3.2 四个路由函数

| 函数 | 返回 |
|---|---|
| `route_after_intent` | `slot_filling` / `hitl_gate` |
| `route_after_slot` | `context_collect` / `ticket_node` / `refund_apply` / `my_tickets_node` / `chitchat_node` / `lead_capture_node` / `engineer_query` / `rag_search` / `end` |
| `route_after_context` | `order_node` / `rule_match` / `end` |
| `route_after_rule` | `action_exec` / `rag_search` |

> ⚠️ **`route_*` 返回的每个值都必须在条件边的 `path_map` 里登记**。
> 漏一个就会在命中该意图时直接抛 `KeyError` → 后端 500 →
> 飞书里只显示网关的「Something went wrong」。
> 曾经漏登记过 `lead_capture_node`，现由 `tests/test_graph_routing.py` 静态守住。

### 3.3 三个设计特征

**① 规则先行，RAG 兜底**

`context_collect → rule_match →（命中）action_exec /（未命中）rag_search`

能用**确定性规则**答的绝不调 LLM：更快、更准、可解释、省 token。
RAG 只处理规则覆盖不到的「为什么 / 怎么排查」类问题。

**② `hitl_gate` 统一收口**

`generate` 与 `action_exec` **都指向** `hitl_gate`，而不是各自实现一套审核。
要改人工介入策略只改 `need_hitl()` 一处。

**③ 两条 LLM-free 快路径**

`chitchat_node`（闲聊）与 `lead_capture_node`（线索捕获）**直接 END、不调模型**，
毫秒级返回。

### 3.4 HITL 的判定策略

`need_hitl()` 按顺序判断：

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | 意图在 `SKIP_CONFIDENCE_INTENTS` | 直接放行（不做置信度确认）|
| 2 | 意图在 `HIGH_RISK_INTENTS`（`human` / `complaint`）| **需要人工** |
| 3 | 规则声明 `action_result.need_human` | **需要人工** |
| 4 | `flow_status == "rejected"` | 不再追问 |
| 5 | `confidence < CONFIDENCE_THRESHOLD`（0.5）| **需要人工** |

**`SKIP_CONFIDENCE_INTENTS` 里只允许放只读意图**（`order_query` / `logistics` /
`engineer_query`）—— 判错也只是答非所问，没有副作用。

> ⚠️ `ticket`（报修建单）**刻意不在这里**：它会真的写库、真的派单，
> 识别不准却照样建单会产生脏数据，用户还会卡住等一个不存在的工程师。
> `tests/test_hitl_and_skills.py` 里有一条结构性守卫：
> 跳过列表一旦混入会写库的意图就立刻失败。

---

## 四、状态设计

[`app/workflows/state.py`](../app/workflows/state.py) 共 30+ 字段，分 6 组：
会话 / 意图槽位 / 业务上下文 / 规则 / RAG / HITL + 动作。

### 4.1 消息截断（一次真实事故换来的）

```python
MAX_MESSAGES = 10   # 只保留最近 10 条（5 轮对话）

def _append_with_limit(old, new):
    """...防止 checkpointer 累积 state.messages 导致 checkpoint blob
       指数膨胀（曾到 7.88 GB）"""
    return list(new or [])[-MAX_MESSAGES:]
```

**根因**：各节点返回的是**完整** `messages` 列表（已含历史），
LangGraph 把它当 `new` 传给 reducer；若 reducer 里再叠加 `old`，
同一条消息会被重复写入，而每轮又叠加一次 → **指数膨胀**，实测涨到 7.88 GB。

**修法**：只对 `new` 做截断，不叠加 `old`。

> 这条是面试里**最有杀伤力**的一个细节 —— 同时证明你懂 reducer 语义、
> 遇到过真实生产问题、并且会用数字描述故障。

---

## 五、已知的不一致与可改进项

### 5.1 `notify_human` 曾经是空壳（已修）

**问题**：skill 只 `return {"status": "queued"}`，不建单、不通知任何人 ✗
而真实的转人工走的是：

```
意图命中 human → hitl_gate 挂起 → 入口层 after_turn()
→ handoff_service.create_handoff() → 飞书通知坐席
```

同一个业务能力**两套实现、只有一套是真的**。网关若真按该 schema 调用，
用户会以为转了人工，其实没人被通知。

**修复**：skill 内部转调 `handoff_service.create_handoff()`，
两条路径共用一套实现；返回值保持向后兼容（老字段都在），另补 `handoff_id`。
建单失败时返回 `status: "failed"`，**不再假装成功**。

### 5.2 仍然存在的可改进项

| 项 | 影响 | 方向 |
|---|---|---|
| 全串行，无并行分支 | `context_collect`（聚合订单/物流/客户）与 `rule_match` 本可并行，串行多花 30~50% 延迟 | 用 LangGraph 的并行边或 `Send` |
| 没有子图 | 退款 / 工单流程已较复杂，平铺在主图里 | 抽成子图（原来那个空的 `subgraphs/` 目录已删除 —— 规划了却没用，留着只会误导）|
| 意图引擎的兜底是硬编码 | 长尾说法覆盖靠不断补规则 | 把兜底也配置化 |

---

## 六、面试怎么讲这两块

| 被问 | 回答要点 |
|---|---|
| 工作流怎么编排的？ | LangGraph StateGraph 15 节点，**三层收口**：入口意图分流 → 规则优先 RAG 兜底 → `hitl_gate` 统一管人工介入；另有两条 LLM-free 快路径 |
| 为什么规则在 RAG 前面？ | 能用确定性规则答的绝不调模型 —— 更快、更准、可解释、省 token；RAG 只兜规则覆盖不到的部分 |
| HITL 怎么实现的？ | `hitl_gate` 是所有需确认路径的收口；`interrupt()` 挂起 → 状态落 SQLite Checkpointer → `Command(resume=...)` 恢复；含超时自动取消 |
| 踩过什么坑？ | **checkpoint 涨到 7.88 GB** —— messages 在 reducer 里被重复叠加，改成截断最近 10 条 |
| 你的 5 个 Skill 都真实现了吗？ | 4 个是真实工具；第 5 个转人工**原本是空壳**，我发现它和真实路径不一致后改成了内部转调同一个服务 —— **如实说比含糊强** |
| 有测试保障吗？ | 201 个用例。其中 `test_graph_routing.py` 用**静态校验 + 真实 invoke** 两道防线守住路由表完整性 —— 这条是漏登记映射表导致线上 500 之后加的 |
