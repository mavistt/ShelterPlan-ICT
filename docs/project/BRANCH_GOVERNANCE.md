# ShelterPlan 分支治理与当前审计

审计日期：2026-11-26

## 目标

- main 是稳定正式版本。
- integration/abc-shiqi-v2 是唯一长期集成主线。
- Feature 分支只承载一个可审查的短期交付；验收合并后再删除。
- 不 force push，不改写历史，不把未验收的 B/C/UI 分支强行并入长期主线。

## 已核对事实

远端仓库为 mavistt/ShelterPlan-ICT。

| 分支 | 当前事实 | 处置 |
| --- | --- | --- |
| integration/abc-shiqi-v2 | 远端基线 14d686a | 长期集成主线 |
| feature/shelterplan-eval | 当前本地审计 head `079fa520`；相对 `integration/abc-shiqi-v2` 为 ahead 15、behind 0（远端状态需以推送/API 再核对） | 本轮唯一 A / ShelterPlan-Eval 交付分支 |
| feature/shelterplan-eval-v1 | Stage 0/1 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feature/shelterplan-eval-stage2 | Stage 1.5/2 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feature/shelterplan-eval-stage3-pilot | Stage 3 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feat/composite-recovery-ui-bridge | b5781d4；相对 integration ahead 7、behind 0；包含 c42e967 及 Trace/演示/验收修复 | 已推送候选，待创建 B/C/D PR |
| feature/compositional-skill-harness | feat/composite-recovery-ui-bridge 的祖先 | 无独立未包含提交，待候选 PR 建立后清理 |
| feat/map-first-resilience-ui、abcd | feat/composite-recovery-ui-bridge 的祖先 | 无独立未包含提交，待候选 PR 建立后清理 |
| feature/benchmark-grounded | 历史 Benchmark 只读资产 | 保留，不作为当前 A v2 数据源 |

## Eval 交付链

4a93324 的祖先链已经包含 Stage 0–3.1、显式 C region/data_version 绑定和整数化自然语言提示：

14d686a → a572d19 → bbcba8a → 84a6cbd → 53313e1 → ecda3b7 → e93a133 → eb0cbd9 → 7e55bb0 → 7c20870 → 791786d → 4a93324

因此不需要把三个 Stage 分支长期并列保留。统一 PR 的目标应为：

- Head：feature/shelterplan-eval
- Base：integration/abc-shiqi-v2
- 类型：Draft，直到团队完成 B/C 联调验收

## 当前权限与未执行动作

当前 GitHub 连接可以通过 Git 推送，但 GitHub Connector 的 PR/ref 管理仍返回 `403 Resource not accessible by integration`；因此创建/关闭 PR 和删除分支必须实际尝试并按返回结果报告。

以下动作仍保持待办，不以本地状态冒充完成：

1. 创建完整 Eval Draft PR。
2. 在新 PR 可见后关闭旧 PR #11、#12，并注明由完整 PR 替代。
3. 确认合并前保留全部提交，再删除三个 Stage/Eval 祖先分支。
4. 统一 PR 合并到 integration 后，再删除 feature/shelterplan-eval。

手动入口：
https://github.com/mavistt/ShelterPlan-ICT/compare/integration/abc-shiqi-v2...feature/shelterplan-eval?expand=1

B/C/D 候选手动入口：
https://github.com/mavistt/ShelterPlan-ICT/compare/integration/abc-shiqi-v2...feat/composite-recovery-ui-bridge?expand=1

## 数据与代码保护

Eval 分支审计 diff 未发现 data/ 或 configs/module_a_v2_b/ 的改动。A v2 原始数据、Manifest 和历史 Benchmark 冻结资产继续保持只读语义。B/C/UI 分支不能在未验收时覆盖 integration。

## 2026-11-26 A-only handoff

本轮只维护 `feature/shelterplan-eval`，不创建新的 Stage 分支，不合并、关闭或删除其他分支和 PR。A 交付以 `reports/final_release/` 为唯一发布包，包含冻结任务集、A 侧难度证据、纯评测契约、Oracle 证明边界和复现命令。B/C/D 整合由后续团队工作负责，不作为本轮 A 验收条件。

