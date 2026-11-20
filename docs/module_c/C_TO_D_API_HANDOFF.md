# Module C → Module D｜HTTP API 交接文档

> 适用：`feature/module-c` 当前 HEAD，数据 `zhongshan_pilot_v0.1` / **DEMO_ONLY**。
> 本文所有请求示例都在本机两个真实 uvicorn 进程（B + C）上跑过；
> 云侧 AgentArts 联调**尚未开始**，见 `AGENTARTS_DEPLOYMENT_CHECKLIST.md`。
> C 只有一条计算路径（`StructuredRunner → TaskController → HarnessService → BClient → B`），
> MCP 工具保留为旁路入口；D 只需要 HTTP，不需要理解 B 的内部概念。

---

## 0. 一页速览

| 项 | 值 |
|---|---|
| C 基址 | `http://127.0.0.1:8080`（示例端口，可改） |
| B 基址 | `http://127.0.0.1:8000`，由 `SHELTERPLAN_B_BASE_URL` 指向 |
| 提交 | `POST /v1/sessions/{sid}/turns` → **202**，body 立即是 `RUNNING` 快照 |
| 取结果 | `GET /v1/turns/{turn_id}` 轮询，建议 500 ms |
| 五状态 | `RUNNING` / `NEEDS_CLARIFICATION` / `SUCCEEDED` / `FAILED` / `CANCELLED` |
| 数据声明 | `data_status=DEMO_ONLY`；声明同时在 `warnings`、`message`、`/v1/capabilities` |
| 几何 | **B 不含几何**；图层是 24 行以 `shelter_id` 为键的表，D 自备底图关联 |
| 进程模型 | 单进程、内存会话 + 原子落盘；无跨进程锁、无鉴权（本地 demo） |

---

## 1. 依赖与启动

### 1.1 依赖

- Python **3.11/3.12**；B 的运行时与 C 的运行时都需要：
  `fastapi`, `pydantic`, `uvicorn`, `httpx`, `PyYAML`, `numpy`, `scipy`, `pyarrow`,
  `networkx`, `pytest`，C 另需 `mcp`。版本约束见 `requirements.txt` /
  `requirements-b.txt` / `requirements-c.txt`。
- **版本陷阱**：`pyarrow 26` 要求 numpy 2，而 B 需要 numpy 1.26（B 侧锁定
  `pyarrow>=16,<19`）。同一解释器若已装 pyarrow 26，B 会返回
  `DATA_INVALID: pyarrow requires NumPy 2.0 or newer`——请为 B+C 建独立虚拟环境，
  按三个 requirements 文件安装即可避开。
- 模型：默认走 `LLMAdapter`（本地 Ollama `/v1`，`gemma4:e2b`）。没有模型服务时
  `IntentExtractor` 会落到 `rule_extract`，功能可用但每轮多一次连接超时；
  CI/演示可用确定性替身 `RuleLLM`（`LLM_MODE=rule`）。

### 1.2 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `SHELTERPLAN_B_BASE_URL` | `http://127.0.0.1:8000` | B 地址 |
| `SHELTERPLAN_DATA_VERSION` | `zhongshan_pilot_v0.1` | 数据版本 |
| `SHELTERPLAN_DATA_STATUS` | `DEMO_ONLY` | 准入状态（**不是**对话可改字段） |
| `SHELTERPLAN_TRACE_DIR` | `outputs/module_c/trace` | Trace 与默认 state 目录 |
| `SHELTERPLAN_JOB_TIMEOUT_S` | `180` | B 作业超时 |
| `SHELTERPLAN_LLM_BASE_URL` / `_MODEL` | `http://127.0.0.1:11434/v1` / `gemma4:e2b` | 模型端点 |
| `NO_PROXY` / `no_proxy` | 建议 `127.0.0.1,localhost` | 防止企业代理劫持 loopback |

### 1.3 启动（两个终端，均在仓库根目录执行）

```bash
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost   # 防企业代理劫持 loopback
PY=python    # 需满足三个 requirements 的版本约束（见 §1.1）

# 终端 1：Module B（求解器，不要暴露公网）
$PY -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 终端 2：Module C（D 对接的就是这个）
SHELTERPLAN_B_BASE_URL=http://127.0.0.1:8000 \
$PY -m uvicorn agent.api:app --host 127.0.0.1 --port 8080
```

MCP 仍然保留（可作为节点入口或本地调试，不替代 HTTP）：

```bash
$PY -m agent.mcp_server --transport stdio
$PY -m agent.mcp_server --transport http --host 127.0.0.1 --port 8010
$PY -m agent.mcp_server --list-tools     # 恰好 4 个粗粒度工具
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/v1/capabilities
```

`/v1/capabilities` 返回 `statuses`、`task_types`、`data_version`、`data_status`、
`limits`（`turn_timeout_s=180`、`poll_interval_hint_ms=500`、`max_text_chars=4000`）
与 `disclosure`（DEMO_ONLY 声明文本）。D 应以这个响应做能力探测，不要硬编码任务类型。

---

## 2. 接口总表

| 方法 | 路径 | 成功码 | 说明 |
|---|---|---|---|
| GET | `/healthz` | 200 | 存活、arm、会话数、`interrupted_turns_on_start`、`state_dir`、`region_mode` |
| GET | `/v1/capabilities` | 200 | 能力探测 + 数据声明 + 可用区域摘要 |
| GET | `/v1/regions`（兼容 `/api/regions`） | 200 | 区域目录（自动兼容 Module A v2 三区域与 v0.1 单区域） |
| GET | `/v1/regions/{region_id}/layers`（兼容 `/api/regions/...`） | 200 / 404 | 区域图层清单（含 CRS/格式/状态；自动补齐 manifest 底图） |
| GET | `/v1/regions/{region_id}/scenarios`（兼容 `/api/regions/...`） | 200 / 404 | 区域情景目录（附带 C 参数映射 `normalized_changes`） |
| POST | `/v1/sessions` | 201 | 建会话，可传可选 `region_id` 绑定区域，返回 `session_id` |
| GET | `/v1/sessions/{sid}` | 200 | 参数快照、`region_id`、lineage、result_ids、pending_clarification、轮次列表 |
| POST | `/v1/sessions/{sid}/reset` | 200 | 重置参数/结果/澄清/幂等键/历史轮次（保留已绑定的 `region_id`） |
| POST | `/v1/sessions/{sid}/turns` | **202** | 提交一轮，立即返回 `RUNNING` envelope |
| GET | `/v1/turns/{turn_id}` | 200 | 轮询，返回五状态之一 |
| POST | `/v1/turns/{turn_id}/cancel` | 200 / 409 | 取消（见 §9） |
| GET | `/v1/results/{rid}/layers?session_id=&variant=auto` | 200 / 403 / 404 / 422 | 场所负载表（无几何；支持 `auto\|baseline\|optimized\|fixed\|adaptive`） |
| GET | `/v1/results/{rid}/evidence/{section}?session_id=&offset=&limit=` | 200 / 403 / 404 / 422 | 分页证据 |

错误码：`404` 未知 session/turn/region/result，`409` 会话已存在、区域 OD 不可用或轮次已结束，`403` 跨会话取结果，
`422` 请求体或参数不合法（如 `text` 为空或 >4000 字符、非法 `variant`/`section`），`503` 上游 B 超时或不可达。

---

## 3. 会话、轮次与轮询（可直接执行的 curl）

```bash
C=http://127.0.0.1:8080
J='Content-Type: application/json'

# 3.1 建会话
SID=$(curl -s -X POST "$C/v1/sessions" -H "$J" -d '{}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
echo "session_id=$SID"

# 3.2 提交一轮：202，body 里的 status 一定是 RUNNING
TID=$(curl -s -X POST "$C/v1/sessions/$SID/turns" -H "$J" \
  -d '{"text":"评估一下现状。","client_turn_id":"demo-1"}' \
  | python -c "import sys,json;d=json.load(sys.stdin);assert d['status']=='RUNNING';print(d['turn_id'])")
echo "turn_id=$TID"

# 3.3 轮询到终态（RUNNING 之外即结束）
for i in $(seq 1 120); do
  R=$(curl -s "$C/v1/turns/$TID")
  S=$(printf '%s' "$R" | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
  if [ "$S" != "RUNNING" ]; then printf '%s' "$R" | python -m json.tool; break; fi
  sleep 0.5
done

# 3.4 同一 client_turn_id 重发，返回同一个 turn_id（幂等）
curl -s -X POST "$C/v1/sessions/$SID/turns" -H "$J" \
  -d '{"text":"评估一下现状。","client_turn_id":"demo-1"}' | python -m json.tool

# 3.5 会话视图（参数是 C 的完整快照，B 的 parent_scenario_id 不做参数继承）
curl -s "$C/v1/sessions/$SID" | python -m json.tool
```

**为什么是 202 + 轮询**：一次规划轮次包含模型解析与 B 求解（本机 p50 约 7.6 s、
p95 约 22.6 s），长请求会被网关提前断开。POST 立即返回 `RUNNING` 快照，
worker 完成后更新同一个 envelope；D 只按 `turn_id` 轮询。

---

## 4. 澄清与后续补参

一次请求缺少必要参数时，`status=NEEDS_CLARIFICATION`，`result_id=null`，
**不会调用 B**；`clarification` 块告诉 D 要问什么。下一轮只补缺失字段，任务自动续跑。

```bash
# 第 1 轮：只说要优化，缺预算
T1=$(curl -s -X POST "$C/v1/sessions/$SID/turns" -H "$J" \
  -d '{"text":"帮我做个扩容优化。"}' | python -c "import sys,json;print(json.load(sys.stdin)['turn_id'])")
curl -s "$C/v1/turns/$T1" | python -m json.tool
# -> status=NEEDS_CLARIFICATION, clarification.fields=["budget_units"],
#    clarification.pending_task_type="optimize"

# 第 2 轮：补预算，还缺候选
T2=$(curl -s -X POST "$C/v1/sessions/$SID/turns" -H "$J" \
  -d '{"text":"预算是2。"}' | python -c "import sys,json;print(json.load(sys.stdin)['turn_id'])")
curl -s "$C/v1/turns/$T2" | python -m json.tool
# -> clarification.fields=["improvement_options"]；预算 2 已暂存到会话视图

# 第 3 轮：补候选，自动续跑
T3=$(curl -s -X POST "$C/v1/sessions/$SID/turns" -H "$J" \
  -d '{"text":"zs_fed820d94e 扩容300人，成本1。"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['turn_id'])")
curl -s "$C/v1/turns/$T3" | python -m json.tool
# -> status=SUCCEEDED, resumed_from_clarification=true, result_id/... 有值
```

D 的规则：
- `clarification.fields` 是**字段名列表**，不要解析 `message` 里的中文来推断；
- `clarification.question` 可直接展示；`resume_hint` 提示用户"下一轮只补这些字段"；
- `pending_task_type` 说明挂起的是什么任务；用户切换任务/取消/超时（TTL 1800 s）
  会自动关闭挂起；连续 3 次无效回复后也会关闭并说明，不会无限追问；
- 用户说"撤销上一步 / 恢复预算 / 不要停用 zs_x"由 C 处理，D 无需特殊逻辑；
  对象不明时 C 会返回 `clarification.fields=["undo_target"]`。

---

## 5. 响应 envelope（`GET /v1/turns/{turn_id}`）

顶层字段固定存在（未产生的值可能为 `null`）：

| 字段 | 类型 | 含义 / D 的用法 |
|---|---|---|
| `turn_id`, `session_id` | str | 轮询键与会话归属 |
| `status` | enum | 五状态之一；**只按这个字段判断终态** |
| `user_text`, `created_at`, `updated_at` | str | 展示与排序 |
| `scenario_id` | str \| null | C 的内容寻址情景快照 |
| `task_id`, `job_id`, `result_id` | str \| null | 分别对应 C 的任务、B 的异步作业、B 的结果 |
| `data_version`, `data_status` | str | 恒为 `zhongshan_pilot_v0.1` / `DEMO_ONLY` |
| `metrics` | object | 仅数字指标，如 `served_demand`、`total_demand`、`weighted_walk_time_min`、`coverage_ratio`；**不得自行换算成"覆盖率 100%"等结论** |
| `map` | object \| null | `available=false` + `layers_href`（见 §7） |
| `warnings` | string[] | 至少含一条 DEMO_ONLY 声明；可能含 B 的 warning |
| `message` | str | 可直接展示的中文回答（已含声明与结果编号） |
| `clarification` | object \| null | `{fields, question, pending_task_type, resume_hint}`，仅澄清态有值 |
| `error` | object \| null | `{code, message, class, recoverable}`，仅 `FAILED` 有值 |
| `timings` | object | `queued_at`、`started_at`、`queue_ms`、`run_ms` |
| `llm` | object | `calls`、`prompt_tokens`、`completion_tokens` |
| `intent`, `task_type` | str \| null | 便于前端埋点，不参与判错 |
| `resumed_from_clarification` | bool | 仅续跑成功的轮次为 `true` |
| `cancel_requested`, `state_persisted` | bool | 调试字段 |

`SUCCEEDED` 时 `result_id` 必非空，`metrics` 至少含三项核心指标；
`NEEDS_CLARIFICATION` / `CANCELLED` / `FAILED` 时 `result_id` 为 `null`
（**没有产生可引用的规划结果**）。

---

## 6. 错误字段与判错规则

字段结构固定，文案由 C 的 `contract.py` / `compose_error` 生成（下为示意）：

```json
{
  "status": "FAILED",
  "error": {
    "code": "CONTRACT_VIOLATION",
    "message": "数据准入状态不由对话决定：数据准入（demo / verified）是部署配置…",
    "class": "policy",
    "recoverable": false
  }
}
```

| `error.code` | 含义 | D 的建议展示 | 可重试 |
|---|---|---|---|
| `CONTRACT_VIOLATION` | 试图去掉 DEMO_ONLY / 伪称已核验 / 绕过质检 / 开 `verified` | **已拒绝**（不是"出错了"），会话状态不变 | 否（改请求内容） |
| `GUARD_INVALID_PARAM` / `GUARD_INVALID_OPTION` | 参数或扩容候选不合规（如负成本） | 未执行，指出哪个值不合法 | 否 |
| `GUARD_UNCLEAR_OPERATION` | 句子支撑不了模型给出的数值变换 | 转为 `NEEDS_CLARIFICATION`，请用户给目标值或明确倍数；**不会带猜测值提交 B** | 否（补充信息） |
| `SCENARIO_INVALID` / `CANDIDATE_INVALID` / `B_*` | B 拒绝请求 | 服务拒绝了该请求 | 否 |
| `JOB_TIMEOUT` / `TRANSPORT_ERROR` | 暂时失败 | 可重试（`recoverable=true`） | 是 |
| `INTERRUPTED_BY_RESTART` | 进程重启时该轮还在 `RUNNING` | 请重新发起（B 作业不可续跑） | 否 |
| `INTERNAL_ERROR` | C 内部异常 | 通用错误 | 否 |

`error.class` 取值 `guard` / `policy` / `upstream_transient` / `upstream_rejected` /
`integrity` / `internal`；`recoverable` 只在 `upstream_transient` 为 `true`。
**越权/非法请求不会改变会话状态**：拒绝前后 `params` 与 `result_ids` 逐项相同，
正在挂起的澄清也不会被清掉（`test_contract_refusal_is_a_failed_envelope_and_changes_nothing`、
`test_refusal_keeps_an_open_clarification`）。

---

## 7. 结果字段、图层与 ID 关联

B 的结果文档是 5–11 MB，**C 只投影 ≤20 KB 的 summary**。D 拿到的 `metrics`
是投影后的数字，不直接从 B 取整包。

```bash
RID=<某次 SUCCEEDED 的 result_id>
curl -s "$C/v1/results/$RID/layers?session_id=$SID&variant=auto" | python -m json.tool
curl -s "$C/v1/results/$RID/evidence/diagnosis?session_id=$SID&offset=0&limit=20" \
  | python -m json.tool
```

`/layers` 返回（`optimize` / `stress_test` 在 `variant=auto` 下主图层 `layers[0]` 直接返回**优化后**或**固定方案承压后**的场所负载，并附带 `shelter_utilization_baseline` 与 `shelter_utilization_delta` 前后对比图层，以及 `shelter_utilization_adaptive`（若存在自适应重规划）；也可通过 `?variant=baseline|optimized|fixed|adaptive` 显式指定视角）：

```json
{
  "result_id": "...",
  "variant": "optimized",
  "layers": [{
    "layer_id": "shelter_utilization",
    "variant": "optimized",
    "kind": "table",
    "geometry": null,
    "rows": [{"shelter_id": "zs_1b4df7920b", "load_persons": 0, "utilization": 0.0}, "..." ],
    "note": "Module B 的结果不含几何；本层以 shelter_id 为键，Module D 可用自己的底图关联。"
  }],
  "deficits": {"count": 0, "sample": []},
  "selected_option_ids": ["CAP_1_FED820"],
  "data_version": "zhongshan_pilot_v0.1",
  "data_status": "DEMO_ONLY"
}
```

此外，`optimize` 任务的轮次结果 `metrics` 中除了 `served_demand`（优化前基线）、`total_demand`、`optimized_served_demand` 外，还会同步给出 `coverage_ratio`（优化前覆盖率）、`optimized_coverage_ratio`（优化后覆盖率）、`coverage_delta_persons`（覆盖增量人数）与 `optimized_weighted_walk_time_min`，供前端 KPI 卡片直接区分优化前后指标。

要点：

- 本机数据有 **24 个 shelter**，本层最多 24 行；`geometry` 恒为 `null`；
- D 必须**自备底图**，用 `shelter_id` 做关联键；无法关联的 id 要显式标注，
  不要画在错误位置，也不要编造坐标；
- `session_id` 是必填 query：跨会话取别人的结果返回 **403**
  （`session_has_result` 成员校验），防止 `result_id` 枚举；
- `map.available=false` 是诚实声明，不代表接口故障。

---

## 8. DEMO_ONLY 数据声明（D 必须展示）

- `data_status` 恒为 `DEMO_ONLY`，由 `SHELTERPLAN_DATA_STATUS` / 可信配置决定，
  **不是**用户或模型可以指定的字段；`analysis_mode`、`data_status`、`data_version`
  都不在可变更参数集合内。
- 一轮成功回答里声明出现三处：`warnings` 中的
  `DEMO_ONLY：基于演示数据，未经真实现场核验，仅供演示与算法评估。`、
  `message` 的 `【数据声明】DEMO_ONLY — …`、以及 `/v1/capabilities.disclosure`。
- `solver_status=FEASIBLE` 或 `optimality_proven=false` 时，C 明确写
  "得到可行解，但未证明最优"；D **不得**把任何结果渲染成"已证明最优"或
  "可执行应急指令"。
- 用户要求去掉声明、说成已核验、以管理员身份绕过质检时，C 返回
  `CONTRACT_VIOLATION` 的 `FAILED`（见 §6），D 显示"已拒绝"并保留原有会话。
- 只有 A 侧出具验收 manifest 后才可能切到 `VERIFIED`；当前**没有任何途径**
  通过对话或 API 参数切换。

---

## 9. 重启、取消、并发与单进程限制

| 场景 | 行为 | D 要做的事 |
|---|---|---|
| 进程重启 | 启动时 `mark_interrupted()` 把残留 `RUNNING` 改为 `FAILED` + `INTERRUPTED_BY_RESTART`；参数、result_ids、对话记忆 `memory`（`last_task_type`/`last_options`/`last_result_id`）、`client_turns` 幂等键、区域绑定与终态轮次从盘上恢复，且支持重启后直接 `GET /v1/sessions/{sid}` 或续发轮次懒加载恢复（无需先重调 `POST /v1/sessions`） | 轮询不会永远 404；看到该错误码提示"请重新发起" |
| B 重启 | B 的 job 表是内存态，**计算不可续跑**；C 不假装恢复 | 重新提交，不要复用旧 `job_id` |
| 取消（排队中） | `POST /v1/turns/{tid}/cancel` → `cancel_accepted=true`，状态变 `CANCELLED` | 显示已取消 |
| 取消（已开始） | 返回 200 但 `cancel_accepted=false`，附"Module B 求解不可中断" | 继续轮询到终态 |
| 取消（已结束） | **409** | 按钮置灰 |
| 聊天里说"算了/取消" | 关闭挂起澄清，`status=CANCELLED`，不执行任务、不产生结果 | 与 HTTP 取消分开处理 |
| 并发 | 每个 session 一把 `RLock`，同会话轮次串行；不同会话最多 `max_workers=2` 并行 | 同一 session 不要并发提交多轮 |
| 单进程 | 会话在内存 + 每会话 JSON 原子落盘，**无跨进程锁**；多副本会各存一份 | 上线前必须粘性会话或外部状态存储 |
| 鉴权 | 本地 demo 无认证，CORS `allow_origins=["*"]` | 上线由网关补 Bearer/OAuth、收紧 CORS、强制 HTTPS |

---

## 10. 真实端到端业务演示

一条命令启动两个真实 uvicorn 进程（B + C），跑通
**现状评估 → 修改条件 → 扩容优化 → 方案比较**，并在第二个会话演示
**澄清 → 补预算 → 补候选 → 续跑成功**：

```bash
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost

python examples/c_to_d_business_flow.py                 # 确定性模型替身（LLM_MODE=rule）
python examples/c_to_d_business_flow.py --real-llm      # 本地 Ollama 真实模型
```

脚本会打印每一步的状态/结果编号/已服务人数，并把**完整 envelope**写到
`outputs/module_c/c_to_d_demo/business_flow_rule.json`（真实 LLM 模式写
`business_flow_real.json`），状态目录与 Trace 在
`outputs/module_c/c_to_d_demo/run-<mode>-<时间戳>/` 下。任何一步失败脚本以非零码退出。

同一流程被固化为 pytest 用例（CI 可跑）：

```bash
python -m pytest tests/module_c/test_business_flow_e2e.py -q
```

它断言四步都是 `SUCCEEDED`、任务类型依次为
`assess / assess / optimize / compare`、`data_status=DEMO_ONLY`、
`warnings` 含声明、澄清三步收敛，以及会话最终参数
（`ratio=0.3`、`walk=20`、`budget=2`）与至少 4 个 `result_id`。

> 演示中可被替换的**只有模型**（`RuleLLM` ↔ `LLMAdapter`）。B 是真实进程、
> 真实求解，HTTP 是真实 socket，状态与结果编号都是真实产生的。

### 10.1 本轮实际运行结果（2026-11-13，两种模型模式各跑一次）

| 步骤 | rule 模式 | real-llm 模式 | `served_demand`（两种模式一致） |
|---|---|---|---|
| 1 现状评估 | `SUCCEEDED` / `assess` | `SUCCEEDED` / `assess` | 6456.10 |
| 2 修改条件 | `SUCCEEDED` / `assess` | `SUCCEEDED` / `assess` | 11332.00 |
| 3 扩容优化 | `SUCCEEDED` / `optimize` | `SUCCEEDED` / `optimize` | 11332.00 |
| 4 方案比较 | `SUCCEEDED` / `compare` | `SUCCEEDED` / `compare` | 11332.00 |
| 澄清 c1 / c2 / c3 | 澄清 → 澄清 → 成功 | 澄清 → 澄清 → 成功 | 前两轮无结果、第三轮产生 `result_id` |

- c1 `clarification.fields=['budget_units']`、c2 `['improvement_options']`、
  c3 `resumed_from_clarification=true`；两种模式一致。
- 完整 envelope（含 `turn_id`、`result_id`、`metrics`、`warnings`、`timings`）：
  `outputs/module_c/c_to_d_demo/business_flow_rule.json` 与
  `business_flow_real.json`；进程状态目录与 Trace 在
  `outputs/module_c/c_to_d_demo/run-<mode>-<时间戳>/`。
- 上表是**最终代码**重跑的结果（rule / real 各一次），归档在
  `docs/module_c/evidence/c_to_d_business_flow_{rule,real}.json`。
- 全量自动化测试含同一流程：**`260 passed / 0 failed`（430.31s）**，
  日志 `/tmp/v23_full_final2.log`；其中 `test_business_flow_e2e.py` 会独立启动
  两个进程并断言同一条流程。

---

## 11. 合并依赖与跨模块阻塞项

完整清单在 `AGENTARTS_DEPLOYMENT_CHECKLIST.md` §9，摘要：

- **B 必须先修**：`engine.py` 浮点下界导致 `FEASIBLE` 降级、
  `parent_scenario_id` 不继承参数、同 `scenario_id` 多快照反查不稳、
  解释一致性原语未接线上 API、无鉴权/CORS/HTTPS、错误体两套形状。
- **C 已兼容**：完整快照重发、`FEASIBLE ≠ OPTIMAL` 话术、≤20 KB 投影、
  错误码归一化、`INTERRUPTED_BY_RESTART`、无几何图层、`DEMO_ONLY` 越权拒绝。
- **A**：数据仍 DEMO_ONLY、栅格为 LFS 指针，未验收 → 全线保持演示声明。
- **D**：按本文字段契约渲染五状态/澄清/取消三态、自备底图按 `shelter_id` 关联。
- **平台**：AgentArts 资源、网络、HTTPS、鉴权、超时、状态恢复、Trace 关联
  **全部未取得/未联调**，不得声称云侧完成。

---

## 12. 已知限制（不隐藏）

1. 无鉴权、无速率限制、CORS 全开 —— 仅本地 demo，公网部署由网关补齐。
2. 单进程、单写者状态文件，没有跨进程锁与 TTL 清理。
3. B 不含几何；图层只是表格，D 必须自备底图。
4. `RUNNING` 的 B 作业在重启后不可续跑，只能重新发起。
5. 模型输出有波动，已用 `rule_extract` 兜底；换模型需要重测意图/槽位正确率。
6. 非作者 ≥20 条自然语言输入尚未收到，独立验证为 **PENDING**
   （冻结工具 `freeze_holdout.py` 与模板已就绪，见 `HARNESS_V2_2_REPORT.md` §6）。
7. 旧 47 条冻结用例只作为 `post-fix regression`，不参与本轮功能验收，
   历史报告与原始 Trace 全部保留、不覆盖。

### 12.1 非作者验证：准备就绪，输入 PENDING

工具与模板已复核可运行（`--help`、≥20 条校验、拒绝二次冻结、sidecar sha256）：

```bash
# 队友把模板另存为 v22_holdout.json 并写 ≥20 条自己的话（不看 agent/ 与旧用例）
python -m tests.module_c.freeze_holdout \
    tests/module_c/cases/v22_holdout.json --author "<队友姓名>"

# 冻结之后才允许跑；--allow-unfrozen 是必须的（run_v21 默认拿 v21_freeze.json 校验传入文件）
python -m tests.module_c.run_v21 \
    --cases tests/module_c/cases/v22_holdout.json --allow-unfrozen \
    --arms harness_opt --reps 1 --label "non-author" \
    --output outputs/module_c/v23-holdout
```

**本轮状态**：`tests/module_c/cases/` 下只有 `v22_holdout_TEMPLATE.json`，
没有任何非作者输入文件，也没有冻结 sidecar → 结果栏为 **PENDING**，未运行、未计分。
本轮新增的 16 个测试项（15 条语义回归 + 1 条端到端）都是**作者自建回归**，
只计入"作者自建回归集"，**不得**标为非作者独立测试。
收到输入后按上面两步执行，保留 `report.json`、逐例 Trace 与首次成绩，不回改。

### 12.2 旧 47 条：只作 post-fix regression

本轮**没有**为提分修改冻结用例或判分代码（`v21_freeze.json` 的 cases/grading
双 sha256 未变），也没有按用例编号加规则。用同一份 47 条、真实 LLM + 真实 B
（仅 arm D `harness_opt`）跑两次，输出写新目录，V2.1/V2.2 的历史报告与 Trace
一律不动：

| 运行 | `design.label` | 通过 | 证据（已归档） |
|---|---|---:|---|
| V2.3 中途 | `post-fix regression (V2.3 close-out)` | 44/47 = 93.62% | `docs/module_c/evidence/v23_postfix_run1_{report.json,summary.md,taxonomy.md}` |
| **最终代码** | `post-fix regression (V2.3 final)` | **46/47 = 97.87%** | `docs/module_c/evidence/v23_postfix_final_{report.json,summary.md,taxonomy.md}` |

- 最终代码的逐例分类：`passed 46 / measurement 1 / 其余 0`；唯一失败
  `V21-B-04` 是**冻结判分器** `asks_or_refuses()` 不认 C 的正确拒绝话术，
  答案是 `CONTRACT_VIOLATION` 且会话状态未变 —— 属测量层，不是产品缺陷。
- 这两次一律标为 `post-fix regression`，**不是盲测、不是独立泛化成绩**；
  V2.1 盲测 `38/47`、`37/47` 与 V2.2 回归 `38/47`、`41/47` 原样保留，不与本轮并列。
- 回归只是验证手段：本轮功能验收看的是 260 条自动化测试、两种模型模式的
  端到端演示和本文的字段契约，不拿旧集分数当成果。

复现命令（都写新目录，不覆盖历史证据）：

```bash
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
PY=python

$PY -m pytest tests/module_c -q                            # 260 passed
$PY examples/c_to_d_business_flow.py                      # 真实两进程，rule 模式
$PY examples/c_to_d_business_flow.py --real-llm           # 真实两进程，真实 LLM
$PY -m tests.module_c.run_v21 --arms harness_opt --reps 1 --rep-start 5 \
    --label "post-fix regression (V2.3 final)" \
    --output outputs/module_c/v23-postfix-final-replay
$PY -m tests.module_c.classify_v22_trace \
    --report outputs/module_c/v23-postfix-final-replay/report.json \
    --trace outputs/module_c/v23-postfix-final-replay/trace \
    --output outputs/module_c/v23-postfix-final-replay/trace_taxonomy.md
$PY -m tests.module_c.recompute_v2                        # 历史 V2 指标只读复核
```
