# ShelterPlan Module A

本仓库当前只包含 ShelterPlan 的 Module A：避难场所数据清洗、试点 GIS 数据准备、步行时间矩阵和独立质量审计。

## 快速开始

```bash
cd /data2/liyutong/ShelterPlan_DataGIS_Starter
source .venv/bin/activate
bash scripts/run_stage_a_demo.sh
python -m pytest tests/ -q
```

## 重要说明

当前输入为 `data/raw/zhongshan_shelters.xls`，包含 453 条 2024 年记录。东区演示流程当前使用 24 个未核验场所点，因此 A7 判定为 `DEMO_ONLY`，不能解释为完整真实试点覆盖率。

详细阶段状态、文件用途和交接方式见 [`docs/module_a.md`](docs/module_a.md)。

Module B/C/D 暂不在本仓库实现。
