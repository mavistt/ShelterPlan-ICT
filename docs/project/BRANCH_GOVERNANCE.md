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
| feature/shelterplan-eval | e93a133；相对 integration ahead 6、behind 0 | 唯一完整 Eval 交付分支，待统一 PR |
| feature/shelterplan-eval-v1 | Stage 0/1 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feature/shelterplan-eval-stage2 | Stage 1.5/2 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feature/shelterplan-eval-stage3-pilot | Stage 3 祖先 | 暂不删除，待统一 PR 合并后清理 |
| feat/composite-recovery-ui-bridge | B/C/UI 最新候选 | 保留，须在临时集成工作树验收 |
| feature/compositional-skill-harness | B/C 历史候选 | 保留，检查独立提交依赖 |
| feat/map-first-resilience-ui、abcd | UI 历史祖先 | 保留，待最新 UI/B/C 验收后再判断 |
| feature/benchmark-grounded | 历史 Benchmark 只读资产 | 保留，不作为当前 A v2 数据源 |

## Eval 交付链

e93a133 的祖先链已经包含 Stage 0–3.1：

14d686a → a572d19 → bbcba8a → 84a6cbd → 53313e1 → ecda3b7 → e93a133

因此不需要把三个 Stage 分支长期并列保留。统一 PR 的目标应为：

- Head：feature/shelterplan-eval
- Base：integration/abc-shiqi-v2
- 类型：Draft，直到团队完成 B/C 联调验收

## 当前权限与未执行动作

本次 GitHub 连接返回仓库权限 push=false、admin=false、maintain=false。远端分支 SHA 已核对为 e93a133，但当前连接不能创建或关闭 PR，也不能删除分支。

在权限可用前，以下动作保持待办，不以本地状态冒充完成：

1. 创建完整 Eval Draft PR。
2. 在新 PR 可见后关闭旧 PR #11、#12，并注明由完整 PR 替代。
3. 确认合并前保留全部提交，再删除三个 Stage/Eval 祖先分支。
4. 统一 PR 合并到 integration 后，再删除 feature/shelterplan-eval。

手动入口：
https://github.com/mavistt/ShelterPlan-ICT/compare/integration/abc-shiqi-v2...feature/shelterplan-eval?expand=1

## 数据与代码保护

Eval 分支审计 diff 未发现 data/ 或 configs/module_a_v2_b/ 的改动。A v2 原始数据、Manifest 和历史 Benchmark 冻结资产继续保持只读语义。B/C/UI 分支不能在未验收时覆盖 integration。

