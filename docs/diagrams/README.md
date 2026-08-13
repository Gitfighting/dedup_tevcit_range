# PolicyMilpECnew 图表索引

基于 `src/main/java/org/example/src/placement_policy/PolicyMilpECnew.java` 的 PlantUML 建模图。

## 图表列表

| 文件 | 类型 | 说明 |
|------|------|------|
| `PolicyMilpECnew_Flowchart.puml` | 流程图 | 写请求、读请求、窗口求解、批量求解主流程 |
| `PolicyMilpECnew_Sequence.puml` | 时序图 | Simulator 与 PolicyMilpECnew、父类交互时序 |
| `PolicyMilpECnew_Class.puml` | 类图 | 类继承关系、内部类、主要方法 |
| `PolicyMilpECnew_Cost.puml` | 计费图 | MILP 目标函数与 Simulator 实际计费对应 |
| `PolicyMilpECnew_TTL.puml` | TTL 图 | 直方图→τ*→MILP→决策→getTevict 生命周期 |
| `PolicyMilpECnew_MILP_Decision.puml` | 活动图 | 单 region MILP 求解详细流程 |

## 渲染方式

使用 PlantUML 或在线工具渲染：

- 命令行: `plantuml docs/diagrams/*.puml`
- VS Code: PlantUML 扩展
- 在线: https://www.plantuml.com/plantuml/uml/
