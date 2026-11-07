# 多 IDE MCP 验收记录

日期：2026-11-07。对应已批准目标：`.pi/goal/支持多-ide-实例的本机-mcp-20261107-1323.md`。

> **后续变更（1.2.0）**：token 认证已取消。下文第 6 项及“原生测试”中关于 401/token 的陈述保留为 1.1.0 历史记录；当前行为见末尾“取消 token 认证（1.2.0）”一节。

## 结论与逐项证据

| 合同项 | 结果 | 本轮观察到的证据 |
| --- | --- | --- |
| 1. 构建与测试 | 已满足 | `build.bat` 成功；原生 14 项、客户端 47 项、旧离线 4 项全部通过；真实双 IDE 脚本退出 0；部署文件与 out 产物 SHA-256 一致 |
| 2. 真实双实例 | 已满足 | 两个真实 IDE 同时打开 A/B 两套隔离工程，分别为 PID 34704/31980、端口 52501/52532；健康信息的 exe、PID、实际 HWND 和工程均核对；18766 被保留的旧实例占用，自动回退成功 |
| 3. 发现与选择 | 已满足 | 实机验证 ID/完整工程路径选择、实时身份及歧义拒绝；失效登记被排除；原生 ABI 测试验证工程变化与健康快照竞争，Python 测试验证旧快照不参与选址 |
| 4. HTTP/stdio 隔离 | 已满足 | HTTP 两客户端各 50 次、stdio 两客户端各 50 次；均重复使用 RPC ID=1，状态/工程始终匹配各自目标；HTTP 复用同一连接；旧 A 绑定在进程退出后报错而非转到 B |
| 5. 定向等待/编译 | 已满足 | wait_ready 不接受邻居工程；xcbuild 只重启 A，用新 PID 36836 编译成功且自动清理；B PID/工程/源码 SHA-256 不变，编译期间通过 92 轮连续检查 |
| 6. 工具兼容/鉴权 | 已满足 | 两个 IDE 均列出原 29 工具，其名称及 inputSchema 与升级前可达旧端点一致；初始化、通知、读文件、状态、工程、写文件、编译及输出链路通过；401/403/409、keep-alive 及显式 URL/token 兼容测试通过 |
| 7. 说明与配置 | 已满足 | README、多实例指南、xcbuild 说明和 mcp.json 已更新，包含双 HTTP/stdio 配置、选择器、错误情况、定向重启及连接重建规则 |

默认首选端口空闲时优先使用它的行为由原生端口测试覆盖；实机期间不结束占用 18766 的无关旧 IDE，因此实机走的是占用回退分支。工程切换和健康快照竞争采用原生测试宿主的可控 ABI 切换验证，不宣称本轮做过桌面手动切换工程的 UI 测试。

## 构建产物

- 1.1.0 历史产物：`out/plug_xcmcp.dll`，x64，MCP server version `1.1.0`。
- 当前 1.2.0 产物见文末更新记录。
- 授权部署：`I:\xcide\data\plug\plug_xcmcp.dll`。
- 最终构建/部署 SHA-256：

```text
988434041e34ef327b6da68d0293c0bf0b3513baec4d86b562fdf07ee80589c1
```

实际构建使用本机 IDE 自带 VC2022 14.41.34120、Windows SDK 10.0.19041.0；Python 为 3.14.8。

## 执行的检查

```powershell
cmd /c build.bat
python -I -B test\test_native_transport.py
python -I -B test\test_instances.py
python -I -B test\test_opt2.py --offline
python -I -B test\test_multi_ide_live.py --fixture '<scratch>\multi-ide-live\fixture.json' --baseline '<scratch>\baseline\reachable-tools-list.json' --build
```

构建命令执行时增加 `/d` 禁用 cmd AutoRun 并重定向 stderr；Python 的 `-B` 用于避免缓存，不改变测试集合。所有临时目录和编译测试产物均重定向到本会话 scratch。

### 原生测试：14/14

使用真实 `http.cpp`、`instance.cpp`、`mcp.cpp`、JSON 实现，仅替换 IDE ABI，不操作真实 IDE：

- 独占端口、首选端口占用回退、显式端口冲突失败；
- 8 个进程同时首次创建默认 token，凭据一致，重启不覆盖；
- PID/创建时间/实时工程身份、UTF-8 工程路径规范化及目标头；
- 401 token、403 Host/Origin、Bearer 兼容；
- 空闲连接不卡住接收线程、keep-alive、pipelining、通知；
- 两客户端同 ID 各 50 次请求；
- handler 停止时仍返回完整响应，关闭空闲连接可及时退出；
- stop/start 两代服务共享同一个 IDE 执行锁；
- 健康快照期间工程改变时返回 409，而不是另一工程的 200；
- 异常不终止服务，帧限制及 JSON 错误响应。

### 客户端测试：47/47；旧离线回归：4/4

使用临时登记目录、本机模拟 HTTP 端点及 mock 进程 API。覆盖实时发现、损坏/失效登记、身份不符、无/多匹配、明确 URL、token 现读/401 重试、代理/重定向禁用、固定绑定、stdio 错误与通知、wait_ready 截止时间和零秒单次探测、定向 PID/创建时间/exe/窗口核验、保存提示超时不强杀、自启清理、watch 会话管理及中文 stderr UTF-8。

旧 `test/test_opt2.py --offline` 文件未改，四项全部通过。真实 Win32 正常关闭、工程加载后绑定、编译与清理另由实机测试覆盖；保存提示和 watch 异常路径仅做了模拟测试。

## 最终实机记录

测试 A 使用授权安装 `I:\xcide`，测试 B 使用本会话 scratch 内的独立副本。未修改 IDE exe，未修改已有业务工程或模块源码。下列 ID/端口是当时的验证数据，不是永久客户端配置。

| 对象 | PID | instanceId | 端口 |
| --- | --- | --- | --- |
| 初始 A | 34704 | `34704-01dd6e8af1d40675` | 52501 |
| B | 31980 | `31980-01dd6e8af5b9572a` | 52532 |
| xcbuild 自启 A | 36836 | `36836-01dd6e8b42b60986` | 53610 |

A 编译结果：`ok=true`、`compiled=true`、`skipped=false`、`errorCount=0`；启动 6.3 秒、编译 3.0 秒、总计 18.6 秒。不是仅依据 F7 投递成功作判断。

B 在 A 编译期间的 92 轮检查均保持 PID、工程一致，测试源码 SHA-256 始终为：

```text
cde51211e21f4534ec98dd3ed2675a88cda0bf271a3876051c97030bf0e7f1eb
```

完整实机脚本记录了 12 项 PASS，退出码为 0。A 旧绑定拒绝在退出后重新选中 B；自启编译实例退出后不在存活枚举中。测试结束后 B 也按已验证的身份正常关闭，测试登记目录枚举结果为空（崩溃残留文件即使存在也不算在线）。原有无关 IDE PID 24500 从未被列入重启/关闭目标。

## 本轮修正与未执行范围

- 修正了空闲连接导致停止等待过久的问题，并回归验证。
- 修正零秒等待仅给 10ms 网络预算导致的偶发误判，改为一次最多 2 秒、不重试。
- 修正 `python -I` 下 xcbuild stderr 仍使用系统代码页的问题，两条输出流都显式 UTF-8。
- 一次只读复核发现的“重启前后执行锁不共享”和“health 快照缺少复核”均已修复，并增加对应原生测试；未开展重复宽泛评审。
- 首次短生命周期启动脚本结束时，工具宿主回收了测试 IDE；之后改为受管理的后台启动。失效登记没有导致误选其他实例。
- 未运行硬编码业务路径或含批量 `taskkill /IM` 的历史脚本，包括原 `test_all29.py`、旧业务编译/模块管理 E2E；不将这些历史结果计入本轮通过数。
- 未测试远程接入、多用户权限隔离、SSE 推送或新版 MCP 协议，这些均不在本轮目标内。
- 目录没有 Git 元数据，使用 scratch 源码/DLL 基线保护原文件，不声称运行过成功的 git diff 检查。

## 收尾核验

- 授权 IDE 已重新打开升级前的工程，恢复后的 PID 为 `32872`，实例 ID 为 `32872-01dd6e8deb8886a7`；验证时地址为 `http://127.0.0.1:63663/mcp`，重启后端口/ID可能变化，应重新发现。
- 恢复前后核对原工程 3 个文件的 SHA-256，均未变化；恢复阶段仅查询状态，不编译或编辑原工程。
- 使用项目 `mcp.json` 原样启动 stdio 桥接，初始化、通知、29 工具清单、状态调用均通过，返回 PID 与恢复实例一致；默认实例枚举 CLI 成功。
- 原有无关 IDE PID `24500` 仍运行并占用 18766，未被升级过程关闭或修改其插件；没有遗留本轮测试 IDE。
- 7 个相关 Python 文件内存语法检查通过，8 个文档 JSON 示例可解析，6 个本地文档链接指向存在的文件。
- 工具声明函数与源码基线一致；编译诊断函数/正则 AST 与基线一致；原 `test_opt2.py` 内容与基线逐字节一致。
- 再次核对 `out/plug_xcmcp.dll` 与授权部署 DLL，均为上文最终 SHA-256。
- 本项目未改写任何客户端的全局 MCP 配置；多实例接入需由使用方按指南指定工程或实例。

## 取消 token 认证（1.2.0）

变更后 `http.cpp` 不再解析或校验 `X-XC-Token` / `Authorization`；实例 token 文件仅为旧客户端兼容元数据。Host、Origin、实例 ID、工程保护未变。

本轮实际执行：

- `build.bat` 成功；原生 HTTP/MCP 测试 **15/15**、客户端测试 **47/47**、旧离线回归 **4/4** 通过。
- 原生测试明确验证：无 token、错误 token、旧 Bearer 头均返回 **200**；带 Origin/非法 Host 返回 **403**；错误实例/工程仍返回 **409**。
- 部署 `out/plug_xcmcp.dll` 到 `I:\xcide\data\plug\`，两者 SHA-256 均为 `946d88910d22ac62152ae5ed73b82a796d0c943850b4490166073037483e67c1`。
- 用无 token 的 HTTP 请求验证原工程实例：`/health`、`initialize`（serverInfo version `1.2.0`）及 `tools/call xc_status` 均 **200**；`toolCount=29`、`mcpLogPane=true`、`mcpLogVisible=true`。
- 同一实例的错误 token 和 `Authorization: Bearer wrong-token` 均为 **200**；`Origin`/非法 Host 为 **403**；错误实例/工程为 **409**。
- 验证实例 PID `33040`、instanceId `33040-01dd6ea27aee6f6f`、临时端口 `21442`；该进程是本次验证实例，工具调用结束后未作为常驻服务保留。部署 DLL 已保留。
