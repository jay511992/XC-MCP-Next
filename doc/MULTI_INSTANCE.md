# 多 IDE 实例 MCP（1.1.0）

本功能是**多个 IDE 进程分别服务不同工程/客户端**，不是让一个 IDE 进程同时拥有多个独立的活动工程。29 个 MCP 工具的名称和既有参数保持兼容。

## 1. 实例与端口

- 每个 IDE 进程有独立的 HTTP 服务，只绑定 `127.0.0.1`。
- 未设置 `XC_MCP_PORT`：首选 `18766`；被占用时由操作系统分配空闲端口，**不是固定顺延到 18767**。
- `XC_MCP_PORT=0`：直接由操作系统分配端口。
- 显式指定非零 `XC_MCP_PORT`：端口冲突则启动失败，不悄悄换端口。
- `instanceId` 由 PID 和进程创建时间组成。同一进程内稳定，IDE 重启后变化，避免 PID 复用造成误认。
- `xc_status` / `xc_server status` 提供实际 `port`、`pid`、`instanceId`；启动横幅也显示实际地址和 ID。

每个实例在 `%LOCALAPPDATA%\xc-mcp\instances` 发布 `<instanceId>.json` 和 `<instanceId>.token`。**MCP 已取消 token 认证**；`.token` 仅为旧客户端兼容保留，服务器不校验。可通过 `XC_MCP_INSTANCE_DIR` 把登记目录改到指定位置；IDE 与客户端必须使用同一个目录。

登记文件只是发现入口，客户端用 `GET /health` 核验 ID、PID、创建时间、exe 和地址，并取得**实时工程信息**。崩溃残留、损坏或身份不符的记录不算存活实例，不根据文件名猜测目标。

## 2. 发现与明确选择

在本项目目录执行（PowerShell）：

```powershell
python -I -B bridge\instances.py list --json
python -I -B bridge\instances.py resolve --project 'C:\work\项目A\项目A.xcproj' --json
python -I -B bridge\instances.py resolve --instance '<从 list 取得的 ID>' --json
```

输出含 `instanceId`、`pid`、`startedAt`、`url`、`port`、`ideExe`、`hwnd`、`projectFile`、`projectDir`、`projectName`、`tokenFile`。`tokenFile` 是兼容字段，连接不需要它。`hwnd` 是经过进程归属核验的 **Win32 HWND**，不是 XCGUI 的内部 HWINDOW。

选择规则：

| 情况 | 行为 |
| --- | --- |
| 没有选择器，恰好一个存活实例 | 自动绑定该实例，保留单实例便捷用法 |
| 没有选择器，零个或多个存活实例 | 明确报错，不默认连接 18766 或列表第一项 |
| `--project` | 按完整 Windows 工程路径匹配；规范化分隔符、大小写和 `.`/`..`，不按文件名模糊匹配 |
| 同一个工程在多个 IDE 中打开 | 报匹配不唯一，追加 `--instance` |
| `--instance` | 绑定该 IDE 进程；不指定工程时跟随该进程当前活动工程 |
| 同时指定项目、ID、URL | 条件必须全部一致，不能静默忽略其中一项 |
| 目标退出/重启，或按工程绑定后切换工程 | 原绑定报错；不重新发现并换到别的实例 |

参数优先于同名环境变量；不同选择器之间仍需交叉验证。不要只清空一个选择器却忘记环境里的另一个。

## 3. stdio 接入（多实例推荐）

每个客户端独立启动自己的桥接进程，不共享同一对 stdin/stdout。桥接会发现地址和实例身份；连接不要求 token，无需配置任何凭据。

以下两个工程路径是示例，替换为实际 `.xcproj` 完整路径：

```json
{
  "mcpServers": {
    "xc-ide-A": {
      "type": "stdio",
      "command": "python",
      "args": [
        "I:\\xcide\\xc-plugin\\XC-MCP-Next\\bridge\\stdio_bridge.py",
        "--project", "C:\\work\\项目A\\项目A.xcproj"
      ]
    },
    "xc-ide-B": {
      "type": "stdio",
      "command": "python",
      "args": [
        "I:\\xcide\\xc-plugin\\XC-MCP-Next\\bridge\\stdio_bridge.py",
        "--project", "C:\\work\\项目B\\项目B.xcproj"
      ]
    }
  }
}
```

也可将 `--project` 换成 `--instance`，或两者同时指定。`mcp.json` 提供的是“只有一个存活实例时自动连接”的简洁配置，多实例时请按上面拆分连接并增加选择器。

桥接在第一次请求时绑定；之后不自动更换实例。IDE 重启后，即使仍打开同一个工程，也需要在客户端重新建立 MCP 连接/重启桥接进程。这是防误操作设计。旧端点若仍返回 401，桥接兼容性地在原目标重读一次 token；新服务器不要求 token。409 或断线不会转发到另一 IDE。

## 4. HTTP 直连

先执行 `list` 或 `resolve`，将各自的 `url` 配置给客户端；token 不需要配置。以下端口和 ID 均为示意，不能假设自动分配的第二个端口是 18767：

```json
{
  "mcpServers": {
    "xc-ide-A": {
      "type": "http",
      "url": "http://127.0.0.1:18766/mcp",
      "headers": {
        "X-XC-Instance": "<A 的 instanceId>"
      }
    },
    "xc-ide-B": {
      "type": "http",
      "url": "http://127.0.0.1:18767/mcp",
      "headers": {
        "X-XC-Instance": "<B 的 instanceId>"
      }
    }
  }
}
```

如需把 HTTP 连接限制在一个工程，还应发送 `X-XC-Project`，其值为完整工程路径的 UTF-8 百分号编码：

```powershell
python -c "import urllib.parse; print(urllib.parse.quote(r'C:\work\项目A\项目A.xcproj', safe=''))"
```

将输出作为 `X-XC-Project` 头值。服务器执行前校验，工具响应前再次校验，健康信息也按快照校验；不匹配返回 409。仅填写 URL 的旧 HTTP 配置仍可用，但不会自动获得项目保护，建议带上身份/工程头或改用 stdio。

传输仍是无会话的 JSON HTTP MCP，协议版本 `2024-11-05`：`POST /mcp` 返回 JSON，通知返回 202 空正文；不提供 GET SSE 推送流，`GET /mcp` 返回 405。`GET /health` 返回无需 token 的实时实例元数据；Host/Origin 和可选目标头仍生效。

## 5. 等待与定向编译

```powershell
python -I -B wait_ready.py 40 --project 'C:\work\项目A\项目A.xcproj'
python -I -B wait_ready.py 40 --instance '<A_ID>'
$env:XC_IDE_ROOT = 'I:\xcide'
python -I -B xcbuild\xcbuild.py build --project 'C:\work\项目A\项目A.xcproj' --instance '<A_ID>' --json
```

- `wait_ready` 只有目标实例/工程匹配才成功；其他实例在线不算目标就绪。返回码 0=就绪、1=等待超时、2=配置/歧义/身份错误。
- `wait_ready 0` 表示只做一轮验证（最多 2 秒），不轮询；正数表示总等待截止时间。
- `xcbuild` 找到唯一匹配工程时，仅正常关闭该目标。找不到工程实例且未指定 ID/URL 时，可创建自己管理的新 IDE。
- 显式 ID/URL 无匹配或选择不唯一时拒绝，不擅自创建替代实例。
- 关闭前核对持有进程句柄的 PID、创建时间、exe 和 HWND 归属，并再次核对当前工程；只发送目标窗口的 `WM_CLOSE`。出现保存对话框/超时则停止，不强杀。
- 新进程使用 `XC_MCP_PORT=0`，就绪必须对应新启动的确切 PID/创建时间/工程，不看别的端口是否返回 200。
- `build` 完成后清理自己启动的编译 IDE；`watch` 复用受控会话并在退出时清理；`run` 成功后有意保留调试 IDE，并报告其 ID/PID/URL；`doctor` 只读，不启动或结束 IDE。
- `XC_IDE_ROOT` 必须对应要管理的安装目录，不能用 A 的安装配置关闭 B 安装的进程。

编译仍通过 IDE 的原生翻译器和 F7 完成，没有改造编译器或修补 IDE exe。一个进程生命周期内重复 F7 的“跳过编译”限制仍存在，所以 xcbuild 需要定向重启。

## 6. 环境变量与安全边界

| 变量 | 使用方 / 含义 |
| --- | --- |
| `XC_MCP_PORT` | IDE 插件：默认首选 18766；0=自动分配；非零=固定端口 |
| `XC_MCP_INSTANCE_DIR` | IDE 与所有 Python 客户端：共同的登记目录 |
| `XC_MCP_PROJECT` | 客户端：工程完整路径选择器 |
| `XC_MCP_INSTANCE` | 客户端：实例 ID 选择器 |
| `XC_MCP_URL` | 客户端：显式本机 HTTP 地址；继续支持无登记的旧服务 |
| `XC_MCP_TOKEN` | 兼容字段，服务器不校验；可省略 |
| `XC_IDE_ROOT` | xcbuild：安装目录，含 `炫彩IDE.exe` |

MCP 服务**不需要 token**。兼容工具仍会读取 `XC_MCP_TOKEN`、所选实例 `tokenFile` 或旧 `%LOCALAPPDATA%\xc-mcp\token` 并发送 `X-XC-Token`，但当前服务器忽略这些头，不会因此返回 401。旧客户端无需修改即可继续连接；新客户端可完全省略 token。

非法 Host 或任何 Origin 头返回 403；目标实例/工程不匹配返回 409。Python 客户端只连接字面量回环地址，不走系统代理，不跟随重定向。旧服务若没有可验证身份，显式 URL 仍可做旧式调用，但 xcbuild 拒绝管理其进程。

这不是多用户权限隔离或文件系统沙箱，也**不再有身份认证**：任何能访问本机回环端口的同机进程都可调用 MCP；原工具的路径白名单和显式 `allowOutsideProject` 行为不变。所有待管理 IDE 都应升级插件，未升级实例不参与新登记发现。不要将端口暴露到局域网/公网，也不要在工作过程中手动切换正在编译的目标工程。

## 7. 测试与复现

```powershell
python -I -B test\test_opt2.py --offline
python -I -B test\test_instances.py
python -I -B test\test_native_transport.py
```

分别覆盖旧离线回归、Python 发现/绑定/进程管理、真实原生 HTTP/MCP 加测试 ABI。原生测试自行编译测试宿主，不会启动或关闭真实 IDE。临时目录使用 `PI_SCRATCH_DIR`，否则放项目 `Temp/tests`。

真实双 IDE 验收使用 `test/test_multi_ide_live.py`，必须显式提供隔离 fixture。目录应在项目 Temp 或会话 scratch，包含 `.xcmcp-test` 标记和 `fixture.json`；A/B 工程都必须位于该 fixture 根之下：

```json
{
  "root": "<隔离测试根的绝对路径>",
  "ideA": "<测试 IDE A 安装目录>",
  "ideB": "<测试 IDE B 安装目录>",
  "instanceDir": "<隔离测试根>\\instances",
  "projects": {
    "A": "<隔离测试根>\\project-A\\A.xcproj",
    "B": "<隔离测试根>\\project-B\\B.xcproj"
  }
}
```

将示意路径替换为真实路径。两个 IDE 以相同 `XC_MCP_INSTANCE_DIR` 启动并分别打开两个最小控制台工程；各工程 `main.xc` 采用 UTF-8-BOM/CRLF，含 `// MCP_ISOLATED_A` 或 `// MCP_ISOLATED_B` 标记。示例主体可仅为 `函数 整型 入口函数()` 后缩进 `返回 0`，选择本机可用的 MSVC/SDK。

```powershell
python -I -B test\test_multi_ide_live.py --fixture '<测试根>\fixture.json' --baseline '<升级前 tools/list 数组文件>' --build
```

`--build` 明确允许定向重启、编译隔离工程 A。该脚本向 A 的测试源码添加注释，检查 B 不变，执行 HTTP/stdio 各两客户端、每客户端 50 次查询、无 token/错误 token 放行、目标头校验、等待隔离与编译清理；报告写入 fixture 内的 `verification-live.json`。不提供 `--build` 时不重启/编译，但仍执行隔离测试文件写入。

本轮结果和未执行项目见 [验收记录](MULTI_INSTANCE_VERIFICATION.md)。不要直接运行带业务工程硬编码路径或批量 `taskkill /IM` 的历史脚本。
