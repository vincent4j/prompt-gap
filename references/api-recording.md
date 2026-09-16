# 供应商无关的 API 留档

PromptGap 不调用生图 API，不管理 endpoint、鉴权、模型注册或轮询策略。这些由用户当时选择的生图 Agent/Skill/客户端完成。

## 每次调用的数据边界

调用方把可见的 request_snapshot、response_snapshot、输入输出文件和事件交给通用记录器。两份 snapshot 保留原结构，未知字段也不能丢弃。额外给出 bindings，说明本次载荷哪些位置对应需要复盘的信息。

JSON bindings 示例：

```json
{
  "prompt_pointers": ["/instruction/text"],
  "input_pointers": ["/assets/0/source"],
  "output_pointers": ["/artifacts/0/location"],
  "requested_model_pointer": "/engine/id",
  "external_job_pointer": "/ticket"
}
```

prompt/input/requested_model 从请求读取；output/external_job 从响应读取。JSON Pointer 使用 RFC 6901 的 `/`、`~0`、`~1` 规则；多消息和多图显式列出有序指针。没有对应字段允许空列表或 null，并记录原因。指针有效只证明字段存在，不证明调用方赋予的职责正确；复盘仍需核对原始快照。

multipart 请求使用有序 part 清单，文本值保留，二进制关联 asset；非 JSON 响应保存脱敏文本/文件引用。脚本不凭厂商名称或常见字段名猜测语义。

## 更换 API 时

生图 Agent 依据新接口本来就需要的知识，传入此次实际请求与关联信息即可；不编辑 PromptGap 核心、不安装新供应商插件。若外层工具不暴露完整请求，只记录可见边界，并在 missing_evidence 声明缺口。

此协议不会自动拦截未接入调用。调用方必须执行通用记录步骤，或由现有脚本在发送/返回边界接入。

## 同步与异步

- 同步：begin → 现有调用 → finish。
- 异步：begin → 现有提交 → append submitted/poll 等事件 → 原流程获取结果 → finish。
- PromptGap 只记录实际发生的状态；不主动轮询、取消、重试或下载。
- 原流程没有保存临时结果时，由 Agent 在权限范围内补归档；若有网络/存储成本单独记录。
- 请求重发建立新 attempt，查询和下载不建立新生成记录。

## 证据与凭据

只接收排除了认证头、Cookie 和凭据的内容。无法保证任意供应商未知字段均能自动识别为秘密，调用方须标记敏感字段；通用检测是补充而非完整保证。

JSON 解析后的结构保持是 semantic snapshot；拿不到 HTTP 原始字节就不能声称 byte-exact。脱敏后记录 redactions 和 capture_scope。可取得的原始提示词单独保存，不能用中文解释替代。

## M1 样例边界

[两个异构 API 样例](../examples/api-recording/fixtures.json) 为合成数据，不访问网络。M2 已用平铺/嵌套结构验证实际持久化；仍不证明生产调用已工作。

可运行 [离线接入演示](../examples/api-recording/record_example.py)：`python3 examples/api-recording/record_example.py`。只创建并清理临时测试项目，不接管 API；接入真实客户端时在 begin 与 finish 之间保留原有提交、轮询和下载流程。
