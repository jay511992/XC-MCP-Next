# 当前错误提示弹窗：`xc_get_dialogs`

`xc_get_dialogs` 是只读 MCP 工具，用于获取**当前连接的炫彩 IDE 进程**中仍可见的提示／错误消息框原文。它与 `xc_get_output` 独立：前者读弹窗，后者读底部调试输出面板。

当前工具清单共 **30 个**；原 29 个工具的名称和输入 schema 不变。新增工具通过原有 HTTP 和 stdio 桥接访问，不新增端口或全局客户端配置。

## 调用

先通过实例发现选择正确的 IDE；多实例时不能假定固定端口属于某个工程。以下 JSON-RPC 发给已经绑定目标的 `/mcp` 连接，stdio 也发送相同请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "xc_get_dialogs",
    "arguments": { "maxChars": 16000 }
  }
}
```

`maxChars` 可省略，默认 **16000**，允许 **128..65536 的整数**，表示每个弹窗正文的 Unicode 字符数上限，不是 UTF-8 字节数。标题另有 1024 字符上限。不会截断 UTF-16 代理对或 UTF-8 字符；标题或正文受限时，该项 `truncated:true`，整体 `complete:false`。

不接受 `clear`、`close`、点击或其他参数。不合法参数返回 `invalid_arguments`，不会执行窗口操作。

## 返回

与现有工具一致，下面的 JSON 位于 MCP 返回的 `result.content[0].text` 中，而不是 JSON-RPC 外层。

```json
{
  "ok": true,
  "complete": true,
  "pid": 1234,
  "count": 1,
  "truncated": false,
  "dialogs": [
    {
      "hwnd": "0x123456",
      "pid": 1234,
      "className": "#32770",
      "source": "win32",
      "title": "提示",
      "text": "main.xc(12): 未找到符号\r\n请检查模块依赖。",
      "readable": true,
      "truncated": false
    }
  ],
  "errors": []
}
```

| 字段 | 含义 |
| --- | --- |
| `ok` | 本次读取没有失败；**不表示编译成功，也不表示没有业务错误** |
| `complete` | 没有读取失败且标题／正文未被截断 |
| `pid` | 目标进程 PID，与当前 MCP 服务进程一致 |
| `count` | `dialogs` 数组长度；失败时也可能包含未完成识别的候选窗口，不能作为错误条数 |
| `hwnd` | 带 `0x` 前缀的字符串，避免 JSON 浮点数丢失句柄精度；不提供后续点击／关闭接口 |
| `source` | `win32`（原生消息框）或 `xcgui`（炫彩自绘提示框） |
| `title` / `text` | 当前标题／正文的 UTF-8 原文，不把标题或按钮标签当作正文 |
| `readable` | 该项正文成功读取；失败项为 `false`，带 `error` 诊断码 |
| `truncated` | 标题或正文触及长度限制；整体字段为各项的汇总 |
| `errors` | 本次读取故障的结构化诊断，包含 `code`、`message`，适用时包含 `hwnd` |

没有匹配的当前弹窗时，返回 `ok:true`、`complete:true`、`count:0`、`dialogs:[]`、`errors:[]`。关闭后的弹窗不会被缓存或再次返回。

读取失败时，MCP 外层的 `result.isError` 为 `true`；仍可解析上述结构，不要将 `count:0` 单独理解为没有弹窗。

### 无响应示例

```json
{
  "ok": false,
  "complete": false,
  "pid": 1234,
  "count": 1,
  "truncated": false,
  "dialogs": [
    {
      "hwnd": "0x123456",
      "pid": 1234,
      "className": "#32770",
      "source": "win32",
      "title": "",
      "text": "",
      "readable": false,
      "truncated": false,
      "candidate": true,
      "error": "ui_timeout"
    }
  ],
  "errors": [
    { "code": "ui_timeout", "message": "IDE UI 未在 1800ms 内完成读取；没有返回过期正文" }
  ]
}
```

`candidate:true` 表示只完成了窗口元数据发现，未完成提示框识别／正文读取；**不是缓存中的旧弹窗**。窗口在快照过程中关闭或隐藏时，可能返回带 `window_disappeared` 的诊断项，但不会返回其旧正文；下一次查询按当前可见窗口重新发现。

## 范围和安全边界

- 支持有静态文本／只读编辑框和普通确认按钮的原生 Windows 消息框，以及本机 XCGUI 的 `XMsg_Create` / `XC_MessageBox` 模态提示框和同类自绘布局。按提示框结构读取原文，不依赖标题含“错误”，也不自动判断错误等级；信息、警告和错误提示都由调用方解释。
- 通用炫彩布局的正文来自可见文本形状 `XShapeText_GetText` 或只读编辑框；窗口标题单独读取。隐藏节点、按钮文字、菜单、工具提示、主窗口与输出面板不作为正文。带可编辑输入、列表、单／多选设置等交互的普通编辑／设置对话框不作为错误提示框。
- **本机 `XMsg_Create` / `XC_MessageBox` 是特殊情况**：正文在 `CMessageBox::OnWndDrawWindow` 中直接绘制，不在文本形状里，公开 API 没有正文 getter。对当前已验证的 XCGUI 构建，插件通过版本和对象结构守卫读取该对象自己的原始正文，不把标题或按钮冒充正文。守卫包括 PE 时间戳／映像大小、导出函数代码签名、HWND 与对象索引、模态类型及继承类型向量、`CMessageBox` 类表和字符串边界；只在所属 UI 线程做有界只读复制，不写对象、不挂钩绘制、不触发重绘。其它版本或结构不匹配返回 `xcgui_layout_unverified`，不猜测私有布局，也不误报没有弹窗。通用文本形状路径不依赖该私有适配。
- 只枚举当前 MCP 服务 PID 的可见顶层窗口，读取前后核验 HWND、PID、UI 线程和窗口类。其他 IDE 或其他程序的弹窗不会混入。
- 控件访问只在插件初始化时登记的 IDE UI 线程进行。其他 UI 线程的候选弹窗返回 `ui_thread_unavailable`，不冒险跨线程访问炫彩对象。
- 后台请求的发现和等待预算为 **1800ms**；安全上限为 **32 个候选窗口、每个窗口 512 个控件节点**。UI 阻塞或 getter 无法及时结束时，客户端收到明确失败，不无限等待。后台不会把即将失效的栈指针／文字缓冲传给 UI；超时任务取消发布，UI 若随后恢复也不会向调用方返回旧正文。
- 读取不发送确认、关闭、编辑、编译或保存指令，不改变弹窗内容。不做历史记录、OCR、截图识别或自动修复。

常见诊断：`ui_timeout`、`reader_unavailable`、`window_disappeared`、`body_read_failed`、`body_unavailable`、`title_read_failed`、`text_read_failed`、`xcgui_api_unavailable`、`xcgui_layout_unverified`、`ui_thread_unavailable`、`node_limit`、`window_limit`、`read_deadline`。这些诊断不代表 IDE 报错本身，而是本次读取不完整或失败。

## 验证与部署

隔离验证使用专门的测试进程、真实 Windows 消息框和 **IDE 同版本的 XCGUI.dll**，链接生产工具实现；不通过标题或模拟 getter 伪造正文。HTTP 和 stdio 均使用生产 MCP/桥接链路。

```powershell
.\build.bat
python -I -B test\test_dialogs.py
python -I -B test\test_opt2.py --offline
python -I -B test\test_instances.py
python -I -B test\test_native_transport.py
```

测试临时目录、DLL 副本、日志和实例登记仅放在 `PI_SCRATCH_DIR` 或项目 `Temp/tests`。不直接运行含业务路径或全局终止 IDE 的历史测试。

## 本轮验收记录

- `build.bat`：退出码 0，生成 `out\plug_xcmcp.dll`。
- `python -I -B test\test_dialogs.py`：**12/12 通过**。真实原生消息框和本机 XCGUI 信息框均核对独立的中文多行正文，不是标题回退；连续读取不改变正文，关闭后返回空列表。
- HTTP、stdio、两个同时持有不同错误框的测试宿主均验证通过；只返回当前宿主 PID 的弹窗。真实 `#32768` 菜单和 `tooltips_class32` 可见性由 fixture 确认，未与主窗口输出混入。
- Unicode／代理对截断、非法参数、正文读取失败、UI 阻塞、慢 `WM_GETTEXT`、关闭竞态、`GroupBox` 装饰、UI 已发布而后台超期醒来、XCGUI 版本拒绝均通过。超期唤醒使用仅测试构建定义的 `XCMCP_DIALOG_TEST_WORKER_DELAY` 做确定性延迟；正式 `build.bat` 不定义该宏。版本拒绝只修改隔离宿主私有 DLL 副本的 PE 时间戳，不修改磁盘 DLL、生产源码或任何真实 IDE。
- `python -I -B test\test_opt2.py --offline`：**4/4 通过**；`python -I -B test\test_instances.py`：**47/47 通过**；`python -I -B test\test_native_transport.py`：**15/15 通过**。
- 剔除新增工具后，原 29 个工具 `{名称: inputSchema}` 的规范化 JSON SHA-256 为 `e0f0ef7e6ff672d2a31702bf7e9096458d856062528b2052ade985e03132ad8e`，与变更前一致。`xc_status.toolCount=30`，且在 `tools/list` 前先查询也成立。

构建产物是 `out\plug_xcmcp.dll`。**本功能开发／验收不会覆盖已部署插件或重启现有 IDE**；现有旧插件可能仍列出 29 个工具。需要上线时由使用者按项目部署说明更新目标插件并自行安排重启。
