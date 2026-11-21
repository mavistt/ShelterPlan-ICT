# Skill Graph 消融与缓存观察

## 是否执行 Skill Graph

执行了。B 的直接 TaskRequest 和 C Harness 最终都进入 B 的 Skill Graph；T01/T02 结果 trace 中记录了技能节点及耗时。当前公开接口没有关闭 Graph 的模式，因此“Graph on vs Graph off”对照为 NOT_RUN，不能声称 Skill Graph 提升了任务成功率或降低了调用次数。

## 可观察到的缓存行为

T18 在相同 Scenario 上顺序运行两次。首轮 8/8 节点未命中缓存；重复轮 3/8 命中（S03、S04、S07），其他 5 个节点重算。两轮目标、选项、状态及结果语义相同。首轮墙钟 9702.94 ms，重复轮 6127.42 ms，差 3575.52 ms（约 36.9%）。任务总计 6 次工具调用。

这是一个暖缓存重复观察，不是受控 Graph 消融；操作系统/进程暖机、求解波动、单样本顺序效应都可能影响耗时。只报告实际 trace 和差值，不把它归因为 Graph 的净性能收益。

完整节点输入/输出哈希、缓存标记、耗时和重算节点在 `traces/V3-18-SKILL-CACHE-REPEAT.json`。
