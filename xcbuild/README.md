# xcbuild — 定向实例的无界面编译

通过炫彩 IDE 原生 `.xc → C++ → MSVC` 工具链编译工程，返回结构化诊断。窗口可以隐藏，但 IDE 进程仍需存在；不修改 IDE exe 或重写翻译器。

1.1.0 起支持多 IDE 实例，**不再按进程名结束所有 IDE，也不再把 18766 在线当作目标就绪**。

## 依赖与安装

- 炫彩 IDE 及其可用的 MSVC/SDK。
- 本项目新版 `plug_xcmcp.dll` 已部署到要使用的安装目录 `data/plug`。
- Python 3，仅标准库；本轮使用 Python 3.14.8 验证。
- 保留项目目录结构，脚本需要共享模块 `../bridge/instances.py`。

自动从本项目位置定位安装目录，也可显式设置：

```powershell
$env:XC_IDE_ROOT = 'I:\xcide'
python -I -B bridge\instances.py list --json
```

多实例规则、HTTP/stdio 配置、登记目录及免 token 行为详见 [多实例接入](../doc/MULTI_INSTANCE.md)。

## 用法

以下命令在本项目目录运行；也可使用 `xcbuild/xcbuild.bat`：

```powershell
python -I -B xcbuild\xcbuild.py build --project 'C:\work\应用\应用.xcproj' --json
python -I -B xcbuild\xcbuild.py build --project 'C:\work\应用\应用.xcproj' --instance '<目标ID>' --json -v
python -I -B xcbuild\xcbuild.py watch --project 'C:\work\应用\应用.xcproj' --interval 1.5
python -I -B xcbuild\xcbuild.py run --project 'C:\work\应用\应用.xcproj' --json
python -I -B xcbuild\xcbuild.py doctor --instance '<目标ID>'
```

所有子命令支持 `--project`/`-p`、`--instance`、`--url`、`--instance-dir`。它们分别优先于 `XC_MCP_PROJECT`、`XC_MCP_INSTANCE`、`XC_MCP_URL`、`XC_MCP_INSTANCE_DIR`，组合时需同时匹配。选项放在子命令后。

未指定工程时，从当前目录向上寻找 `.xcproj`；同一层有多个工程文件则要求显式指定。工程参数也可来自 `XC_MCP_PROJECT`。

返回码：`0` 成功，`1` 编译失败，`2` 环境、参数、身份或安全关闭错误。stdout 的 JSON 和 stderr 的中文诊断均为 UTF-8，包括 `python -I` 模式。

## 目标选择和生命周期

1. 按工程完整路径、可选 ID/URL 发现并验证目标，忽略失效登记。多个匹配报错，不取第一项。
2. 若工程没有对应实例，且没有显式 ID/URL，则启动自己管理的新 IDE。显式 ID/URL 失效不会悄悄变成另起实例。
3. 已有目标只通过已核对的 PID、进程创建时间、exe 路径和 Win32 HWND 定向关闭，并在关闭前复核工程。
4. 只发送该窗口的 `WM_CLOSE`。保存对话框、窗口不符或关闭超时会停止，不强杀、不转而操作另一个同名进程。请提前保存目标 IDE 中的编辑。
5. 新进程设 `XC_MCP_PORT=0`，就绪必须匹配 `Popen` 创建的确切 PID/创建时间、exe、目标工程和窗口。随后才触发 F7、读取该实例的输出。

`XC_IDE_ROOT` 必须与目标 exe 的安装目录一致；不能借 A 安装的配置关闭 B 安装的 IDE。无身份信息的旧 HTTP 服务可用于兼容读取，但不能用于进程重启管理。

| 命令 | 结束时行为 |
| --- | --- |
| `build` | 清理自己启动的编译 IDE；不恢复被替换的旧进程 PID |
| `watch` | 持续复用同一受控会话，每轮定向重启；退出时清理；路由/启动异常则停止 |
| `run` | 成功后有意保留用于调试的 IDE，并输出其 ID/PID/URL |
| `doctor` | 只读检查安装及已选实例，不启动或结束进程 |

独立 MCP 客户端绑定旧进程后，不会自动跳到新进程。需要继续使用重启后的 IDE 时，显式重新建立该客户端的 MCP 连接。

## 为什么仍要重启目标 IDE

历史实测表明，IDE 生命周期中的首次 F7 会真实编译，之后可能只打印：

```text
跳过编译, 因为代码无任何改变
```

改磁盘时间、重开文件或重复按 F7 不能可靠复位这个进程内标志。xcbuild 因此每次只重启**目标** IDE，再等待工程加载；其他工程的 IDE 保持运行。初始化通常约 6 秒，另留 9 秒稳定期，实际编译耗时视工程而定。

原 `xc_build` 工具返回 `ok:true` 仅表示快捷键已投递。xcbuild 还会等待输出并校验完成/失败/跳过信号，不能仅凭投递成功就报告编译成功。

## 结构化结果

成功示例（ID、PID、端口只是示意）：

```json
{
  "ok": true,
  "skipped": false,
  "compiled": true,
  "failCode": null,
  "errorCount": 0,
  "errors": [],
  "project": "应用",
  "projectFile": "C:\\work\\应用\\应用.xcproj",
  "instanceId": "<本轮编译实例 ID>",
  "pid": 12345,
  "url": "http://127.0.0.1:23456/mcp",
  "bootSeconds": 6.3,
  "buildSeconds": 3.0,
  "totalSeconds": 18.6
}
```

`build` 返回时该自启编译实例已经清理；其中 ID/URL 是本次编译的归属证据，不表示它仍在线。

诊断保留源 `.xc` 行号：

| kind | 原始信号 | 含义 |
| --- | --- | --- |
| `parse` | `解析错误: main.xc(行), 列(起-止)` | 语法/词法错误 |
| `check` | `检查错误: main.xc(行), 列(起-止)` | 语义检查错误 |
| `cpp` | `main.xc(行): 错误 Cxxxx: ...` | MSVC 错误，可附 `hints` |

C++ 编译失败时 IDE 仍可能打印 `错误列表: 错误(0)`，因此同时检查编译完成、失败码及诊断，避免误报成功。输出解析器及诊断正则本轮保持原有语义。

## 验证范围

本轮完成真实双实例：定向重启并编译隔离工程 A，工程 B 的 PID、当前工程及测试源码 SHA-256 不变，持续响应检查正常。还覆盖进程身份不符、保存提示超时不强杀、启动转交到别的进程不冒认、自启清理及 watch 会话管理的离线回归。

详细命令、次数与实测数据见 [本轮验收记录](../doc/MULTI_INSTANCE_VERIFICATION.md)。历史业务工程的编译错误示例和 watch 实机测试没有在本轮重新运行，不将其计为本轮通过项。
