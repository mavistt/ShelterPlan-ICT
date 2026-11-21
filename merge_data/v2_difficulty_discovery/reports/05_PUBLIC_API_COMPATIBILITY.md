# 05 — B/C 公开接口兼容性

## 限制位置

- B 引擎 `backend/engine.py`：预算 MIP 内部上限 64，合计时限 10 秒；超上限返回 `MODEL_SIZE_LIMIT`。
- B `backend/models.py` 的公开 `TaskRequest.improvement_options`：Pydantic `max_length=12`。文档 `docs/module_b/API_HANDOFF_TO_C.md` 同时说明 POST body 上限 1 MiB、异步 job/result 状态与最多 100 个 active jobs。
- Module C Harness `agent/harness/guards.py::validate_improvement_options` 也在调用 B 前拒绝超过 12 项。MCP 和 Workflow 最终进入相同 Harness guard；虽然 MCP schema 使用普通数组，不能绕过 guard。

## 判断与建议

本轮不改变 12 项限制。它是业务请求边界而非求解器能力数字；合成 64 项在小图上的亚秒结果不足以证明公开 API 应放宽。B/C 当前还需考虑请求大小、异步 job 生命周期、排队/并发、10 秒 solver 总时限、FEASIBLE 状态呈现、运行内存、请求重试幂等性和结果访问权限。

若 C Harness 需要大候选压测，应使用独立、受控的内部压力任务接口：只接收版本化 fixture ID/有限配置，限制候选上限与并发，创建独立 job/trace，保留最佳界和阶段状态，并清理过期任务。不可把候选拆成多次普通 optimize 请求来冒充同一联合优化，因为分块会丢失全局预算、互斥与组合选择。

Module C 当前是未合并 feature branch。本轮只审查，不复制或改写 C。既有 `docs/integration/B_C_CODE_REVIEW.md` 记录了两个 P1：MCP/service 结果读取未先检查 session 对 result 的成员关系；自然语言数量可能未绑定到目标参数字段。另有取消竞态、缺认证/CORS、状态保留和依赖锁冲突等上线风险。先修复并回归这些问题，再做 C Harness 联合集成；MCP stdio/Streamable HTTP 也需真实 SDK transport 验收。

