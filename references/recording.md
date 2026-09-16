# PromptGap 记录协议 v0.1

状态：M2 留档核心与 M3 按需报告已实现。实际调用步骤见 [执行留档](capture.md)，审核步骤见 [按需审核](review.md)。

## 对象和稳定标识

- project：当前项目根目录解析后的范围。config 包含 schema_version、enabled、storage_root、各入口 coverage。
- task：同一个用户目标，task_id、title、created_at、updated_at、conversation_ref（可空）。不同对话不共享隐式“当前任务”。
- requirement：版本化原话、出处、Agent 当时理解、假设、需求 ID、优先级、验收方式、确认状态。无修改复用版本。
- attempt：一次实际生成提交，attempt_id、task_id、parent_attempt_id、requirement_version、execution_note。
- asset：稳定文件或副本，asset_id、相对路径、sha256、媒体类型、尺寸、ICC 可见性、来源和是否补录。
- observation/feedback：时间、来源、内容、对应 attempt/output；只能追加，不重写原始输入。
- review：独立版本的 report.md 和 diagnosis.json，引用分析范围和证据。
- review 不要求存在错误或至少两次尝试；成功、失败、未评价任务均可查看。未发现偏差时问题集合可为空，注明已核验范围，不强迫填写根因。

schema_version 首期固定为 1；未知版本读取时报告不支持，不能默默按当前格式解释。

## request.json

必需字段：schema_version、task_id、attempt_id、parent_attempt_id、requirement_version、recorded_at、execution_note、capture_mode、transport、task_type、request_snapshot、inputs、missing_evidence。

- capture_mode：live、backfilled、synthetic。
- transport：codex_builtin 或 api。
- task_type：text_to_image 或 image_to_image。
- request_snapshot：实际可见工具/API 载荷；有多角色 messages 时完整保存角色与顺序。鉴权排除，脱敏另记 redactions。
- response_snapshot：响应结构由调用方提供，格式不限，不要求 data、images、job_id 等固定字段。
- bindings：调用方给出的提示词、素材、模型和外部 ID 位置；不是供应商注册表。详细职责及示例见 [通用 API 记录](api-recording.md)。
- capture_scope：api_payload、tool_boundary 或 summary，说明实际观察到哪一层。tool_boundary 的提示词不能宣称是黑盒内部最终请求。
- inputs：按实际输入顺序的 asset 引用、职责、controls/must_not_control、预处理。调用本身已是文生图时允许空数组。
- prompt.txt：实际提示词文本；多消息请求的结构以 request_snapshot 为权威，prompt.txt 不冒充结构化完整载荷。
- 模型分 requested_model 和 returned_model；内置不可见时为 null 并在 missing_evidence 说明。
- 图片上传映射关联本地哈希与实际请求字段；敏感签名 URL 不写入普通日志，标记脱敏与素材身份，不假称逐字原始请求。

必须先持久化请求，再调用生成工具。recorded_at 只表示记录时间；不能替代远端接收时间。

## result.json 与 events.jsonl

result 包含 attempt_id、generation_status、archive_status、outputs、error、provider_request_id、provider_job_id、returned_model、usage、cost、started_at、ended_at、missing_evidence。

- generation_status：pending、succeeded、failed、unknown、cancelled。
- archive_status：pending、complete、partial、failed、not_applicable。
- cost/usage 未取得用 null，附原因；未报账不等于零。
- cancellation 只有远端确认才记 cancelled；仅发出取消请求写事件，状态仍可能 unknown。
- API job success 与图片归档、视觉是否通过三者分开。
- 一个请求返回多图，outputs 每项独立 output_id、asset_id、原始/处理后关系与检查结论。

事件最小字段：event_id、recorded_at、type、attempt_id、可用外部 ID、脱敏 details。recorded_at 是本地记录时间；真实事件发生时间若已取得另传 timestamp。

允许事件：submitted、poll、response_received、download_started、download_completed、download_failed、cancel_requested、transport_error、retry_observed、fallback_observed。

轮询和下载重试属于同一 attempt；重新提交生成建立新 attempt，关联父调用。超时不得触发记录器自行重发。隐藏 SDK 重试标记未知。

## 文件写入与恢复

- begin 冻结请求；finish 补齐结果；event/observe/feedback 追加事件。CLI 参数见 capture.md。
- 重复 finish 相同结果幂等；不同结果默认拒绝。unknown 或成功但归档不全允许显式 supersedes 恢复，旧结果保留 result-history，不能改写已完整终结的结果。
- 索引是缓存，可从任务重建；并发写入使用唯一编号及必要写保护。
- 文件引用在报告生成前核验；改写、缺失、不可读分别报告。
- 后处理记录 source/output 哈希、操作、参数及遮罩若已取得；原图不可替换。
- 日常只输出 attempt ID 和记录状态，不能打印 base64、密钥或整份日志回上下文。

## 原话与自检

原话只保存与当前生成目标相关的内容，但必须保存“再生成”所继承的要求。缺失历史不允许猜补。

执行说明是可审核决定，不要求内部推理。用户未逐项确认时 confirmation=not_explicitly_confirmed；不自动暂停生成要求确认。

自检记录是当时观察，不是事实真值；复盘可指出自检看错。发现修改的用户反馈先追加事实，再由下一轮引用新需求版本。

## M2 协议测试范围

请求逐字往返；多图顺序；消息结构；需求版本关联；多输出；同步/异步；下载失败；重试/回退；并发；中断；幂等完成；脱敏；缺图；索引重建；关闭恢复不删记录。

新增 API 字段变化测试：同一通用路径接受嵌套/平铺/Unicode/未知字段，保存原始数据而不重命名；bindings 可定位对应信息，未提供关联就标缺失，不猜测。JSON 语义保真不等于原始 HTTP 字节保真，若仅取得解析后的 JSON 必须如实注明。
