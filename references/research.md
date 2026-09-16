# 用户选择后的模型能力研究

只有用户在菜单选择“调研模型的某项能力”，或明确提出研究，才联网。程序的 user_authorized=true 只是保存授权声明，不代替真实用户选择。

1. 从所选记录确认请求模型、返回模型、版本和 Provider；不可见时明确 unknown，必要时问用户一个问题。模型别名不能冒充确定版本。
2. 明确研究哪项需求，如“图生图保留衬衫纽扣位置”，而非笼统问模型好不好。先用 research-list 按 model/version/provider/capability 精确匹配读缓存；检查获取日期、版本、任务适配和新证据，不把旧缓存永久当真。
3. 无适用缓存时，使用当前可用且已授权的搜索工具，遵守该工具/技能规则。先做有限查询，建议约 2～5 分钟；没有直接证据就报告不足。要扩到论文级长研究时让用户选择，不无限搜索。
4. 分开记录：official（官方支持边界）、benchmark（专业测评方法/样本/版本）、project_experiment（本项目可追溯尝试）。记录具体来源、发表时间、取得时间、结论与任务相似度。总偏好分、一般物体计数不等于商品小扣子保真；一次失败不证明能力缺失。
5. verdict 只能为 unsupported / unstable / insufficient_evidence。unsupported 必须有任务匹配、版本匹配的直接支持；测评分数低通常只能支持不稳定，不应写绝对不具备能力。引用相互矛盾、版本不明或任务不匹配就明确不足。
6. 用 research-save 保存新缓存，旧缓存不改；对用户说明先优化哪一段请求、为何值得换模型，或证据不足不能推荐。建议不构成实际换模型/付费验证授权。

research-save 必需：user_authorized=true、model、version、provider、capability、verdict、conclusion、limitations、sources。每个 source 记录 kind、reference（可追溯 URL 或本地证据路径）、retrieved_at、published_at（不明写 unknown）、model_match、task_match、finding。没有证据允许 sources=[]，但 verdict 必须 insufficient_evidence。时间用 ISO 日期；脚本存储不代替联网核实。

研究缓存只保存证据和边界，不强制影响既有审核报告。需要结合新证据修订报告时新建审核版本，不改旧版，也不把研究时的新解释冒充生成当时理解。
