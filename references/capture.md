# 执行留档（M2）

Python 3.10+，核心无第三方依赖，无网络调用。Pillow 可选，仅补充部分图片元数据；拿不到的尺寸/ICC 为 unknown，不阻断字节留存。PNG 可直接读尺寸，ICC 未检查会标 not_inspected。

CLI：`python3 <skill-dir>/scripts/prompt_gap.py --project <root> <command> --data <json-file>`。data 也可用 `-` 从 stdin 传入；不将认证信息放命令行。Python 调用可导入 Recorder 并使用 `Recorder(root).run(command, data)`。使用 run 以获得进程锁及脱敏，不直接调用内部写方法。

## 调用前

1. `status`：enabled 才开始新记录。`enable` 仅用户选择后执行；可选 storage_root 指定独立素材目录，已有目录不自动迁移。默认 `.prompt-gap/` 并加入忽略。**忽略不移除已经被 Git 跟踪的文件**，已有历史须另行检查；不要自动提交日志。
2. `task`：`{"title":"任务名称","conversation_ref":"可见对话标识或出处"}` → task_id。一个用户目标一个任务，不同对话不隐式共用。已有目标复用显式 ID。
3. `requirement`：task_id、user_text（原话及明确继承内容）、interpretation（当时可审核理解）、可选 source/assumptions/requirements/confirmation → requirement_version。没有变化复用版本；有纠正新增版本，不覆盖旧文件。不要求内部思维链。
4. 准备最终实际参数，执行 begin 成功后才调用同一份参数。begin 的例子：

```json
{
  "task_id": "由工具返回",
  "requirement_version": "001",
  "execution_note": "首次执行，要求白底两个红圆，不含文字",
  "transport": "codex_builtin",
  "task_type": "text_to_image",
  "capture_mode": "live",
  "capture_scope": "tool_boundary",
  "request_snapshot": {"prompt": "白底画两个红色圆形，不含文字。"},
  "bindings": {"prompt_pointers": ["/prompt"]},
  "inputs": [],
  "missing_evidence": ["内置工具内部模型版本和隐藏载荷不可见"]
}
```

返回 attempt_id。图生图为 image_to_image，inputs 按实际顺序提供 path、role、controls、must_not_control、source 等；input_pointers 指向实际载荷中的对应字段。每个本地素材按哈希去重保存字节、路径、尺寸与职责。无法定位的图片用 missing_reason、可见来源描述记录，不猜文件路径。使用最近会话图片时记录实际 num_last_images_to_include，并尽可能关联当时图片；关联不全必须标缺口。

内置/API 参数都原样存入 request_snapshot（安全脱敏除外），不自创工具参数。API 用 transport=api、capture_scope=api_payload。调用方填写任意 JSON Pointer bindings：prompt_pointers、input_pointers、requested_model_pointer。没有 bindings 保存快照并标缺失，不猜供应商结构。多消息保留角色与顺序；prompt.txt 只是多个文本按双换行连接，完整结构以快照为准。

## 调用后

`finish` 接受 task_id、attempt_id、generation_status、response_snapshot（可见返回）、outputs 数组，以及可用的 usage/cost/returned_model/provider_request_id/provider_job_id/error/missing_evidence。

outputs 每项用 `{"path":"本地已下载图片","role":"original_output"}`；下载失败保留 URL（脱敏）和 missing_reason，不把任务 SUCCESS 当图片已保存。状态 succeeded/failed/unknown/cancelled 与 archive_status 分开。确认远端取消才可 cancelled 且 cancellation_confirmed=true；费用未知留 null。

异步提交/轮询/下载用 `event`：task_id、attempt_id、type、details，沿用同一次 attempt。初始提交响应也写进 submitted.details，不等终态才保存。允许事件见 recording.md。真正重新提交或回退模型必须新 begin 并设置 parent_attempt_id；此记录器不会替你重试。

完全相同 finish 幂等；冲突默认拒绝。仅 unknown 结果或 succeeded 但归档不完整可以显式恢复：读取 result.json 的 completion_fingerprint，新 finish 传 supersedes=该值，保留旧结果在 result-history。恢复不调用模型。不允许重写已经完整结束的成功、失败或取消结果。

`observe` 记录当时自检；`feedback` 记录用户原话；`processing` 记录原图到处理图的 source/output 哈希、操作、参数、遮罩与对应路径（由原处理工具提供）。共同字段 task_id、content、source，可选 attempt_id/output_id；observe 必需 attempt_id。原图保留，不以处理图替换。事实记录不是审核真值。

## 安全、恢复与覆盖

- 不向记录器提供密钥/认证头/Cookie。已知敏感字段和签名 URL 自动脱敏；特殊字段用 redact_pointers，指向整个命令 JSON，如 `/request_snapshot/custom_credential`。redactions 标出修改处。**不能保证识别任意文本或任意供应商自定义密钥**，调用方先排除鉴权；正文必要敏感信息由用户决定脱敏。
- begin 冻结请求与提示词，check 核验它们与素材哈希；不是安全防篡改系统。check 也检查 pending/unknown、缺图和未完成暂存目录。
- 中断后先 inspect/check/read events 查状态，不因 pending 自动再生图。记录失败如实告诉用户；生图已提交则先恢复返回证据，避免重复收费。
- rebuild 可重建索引，list 每页 5 条；inspect 只给选定任务索引与路径。记录只输出简短状态，不回灌整份请求。
- coverage 初始为 agent_protocol_unverified。只有真实入口验证后才用 coverage 更新 transport/state/evidence；M2 合成测试不能标真实入口 verified。完全绕开协议的调用无法被 check 检出。
- disable 阻止新的任务/需求/begin；允许已提交调用 finish 和后续观察/反馈，历史保留。再次 enable 不删历史。
- 不自动安装到项目、不运行生成、不在日常留档时分析、无后台服务。用户选择审核后才按 review.md 生成报告。

## 本地目录

```text
.prompt-gap/config.json
<storage_root>/index.json
<storage_root>/media/<sha256>                 # 原始字节，按内容去重
<storage_root>/tasks/<task_id>/task.json
  requirements/001.json
  feedback.jsonl / processing.jsonl
  attempts/<attempt_id>/
    request.json / prompt.txt / integrity.json
    result.json / result-history/<fingerprint>.json
    events.jsonl / observations.jsonl
```

config 与索引可更新；需求新增版本，请求不被正常命令改写；观察追加。每项目锁保护并发，单文件原子替换；不是跨文件事务数据库。进程崩溃可能留下暂存文件或落后索引，可检测/重建；不自动删证据。
