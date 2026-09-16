# 数字菜单执行规则（M3）

所有命令使用 `python3 <skill-dir>/scripts/prompt_gap.py --project <project> <command> --data <file.json>`，也可从 stdin 传 JSON。用户不需要记忆命令。

| select 返回 action | Agent 下一步 |
|---|---|
| enable | 先告知只改本项目 AGENTS.md、.gitignore 和本地留档目录，执行 enable，展示已开启；返回 menu。首次启用说明现有历史不会自动补齐。 |
| disable | 执行 disable，说明历史仍可查看，返回 menu。 |
| inspect | 使用返回的 task_id 调 inspect、check；按执行顺序读取必要材料，展示记录。没有调用时说明尚未生成。 |
| review | 读取 review.md 执行按需审核；默认最近三次，不足三次照常，告诉用户范围。先给 report.md，完成后展示 view=task 菜单。 |
| task_menu | 调 menu，传 view=task 与已选 task_id；展示审核、反馈重审、能力研究及原始记录选项。 |
| revise | 请用户说明纠正意见，追加 feedback；按 review.md 创建新版本并关联 supersedes，旧版不覆盖。 |
| research | 用户选择此项后才读 research.md；确认具体模型/能力、查缓存或联网研究。选择前不预先搜索。 |
| history | 调 menu，data 为 view=history、返回的 page。展示标题、日期、调用次数和反馈摘要；保留新 menu_id。 |
| home | 重新调 menu。 |
| help | 说明开启后由 Agent 留档，关闭不删历史；主动查看报告时才分析，能力研究需单独选择。 |
| backfill | 说明缺失记录不能复原。让用户确认只补当前上下文还是先选已有历史；未启用先询问是否启用，不能因为点击补录而擅自开启。按 capture.md 用 backfilled 记录可见事实，缺失当时理解写明“未留存，无法确定”，不写成实时记录。 |
| exit | 结束，不修改任何状态。 |

菜单阶段不读取全量图片。用户选中任务才读取该任务记录和必要图片。历史编号固定绑定菜单快照中的 task_id，期间新任务插入不会让编号串任务。

安装后 `/P` 自动补全是否出现取决于宿主加载，必须在 M4 单独验证；脚本测试不等于 UI 验证。M3 的判断由当前 Agent 执行，不能声称本地脚本自动理解图像。
