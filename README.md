# XC-MCP-Next

炫彩IDE 插件，对齐原 `plug_mcp.dll`（471,040 B，无源码）的 24 个 `xc_*` MCP 工具，并扩展至 **29 个**（新增 5 个模块管理工具，让 AI 能自己发现/加入/新建模块）。
本工程通过实证逆向 IDE 插件 ABI 后从零实现。

## 多 IDE 实例（1.1.0）

支持多个 IDE 进程分别对接客户端：独立端口、实例发现、按工程完整路径或实例 ID 绑定，HTTP 与 stdio 均可用，29 个工具及既有参数保持兼容。

```powershell
python -I -B bridge\instances.py list --json
python -I -B wait_ready.py 40 --project 'C:\work\项目A\项目A.xcproj'
```

多实例时不再猜测 18766 或选择列表第一项。完整配置与生命周期说明见 [多实例接入指南](doc/MULTI_INSTANCE.md)，本轮实测见 [验收记录](doc/MULTI_INSTANCE_VERIFICATION.md)。下面保留的 18766 配置是单实例兼容示例；全局 mcp-cli/客户端配置不会自动被本项目改写。

## 怎么调用（已验证的两种方式）

### 方式 A：mcp-cli（单实例兼容配置）

本机 DSH 的 MCP 工具默认**不常驻注入**（省 token），所以 `mcp__xc-ide__*` 不会出现在工具列表里。
用 `mcp-cli` 技能按需调用即可，schema 只有 **3937B ≈ 1219 token**：

```bash
MCP="G:/AItools/skill/_融合后/mcp-cli/scripts/mcp.py"

python "$MCP" ping xc_ide              # OK:29 tools
python "$MCP" list xc_ide              # 列出全部工具
python "$MCP" call xc_ide xc_get_project
python "$MCP" call xc_ide xc_symbol name=取宿主路径
python "$MCP" call xc_ide xc_build
python "$MCP" call xc_ide xc_get_output tailLines=6
```

原 `xc_ide` 登记使用以下固定地址；多 IDE 时应为每个连接填入 `instances.py` 返回的实际 URL，或使用按工程选择的 stdio 桥接：

```json
"xc_ide": {
  "transport": "http",
  "url": "http://127.0.0.1:18766/mcp",
  "headers": { "X-XC-Token": "${file:%LOCALAPPDATA%\\xc-mcp\\token}" }
}
```

`${file:...}` / `${env:...}` 由 `mcp.py` 在发请求前展开，token 不进 `servers.json` 明文。

> 注意：Windows 控制台是 GBK，直接看输出里的中文会乱码。加 `PYTHONIOENCODING=utf-8` 或用管道接到 UTF-8 读取即可，数据本身没问题。

### 方式 B：让 DSH 常驻注入

在 DSH 设置里把该连接的工具暴露打开，重启后即可直接调用 `mcp__xc-ide__xc_*`。
代价是每轮多约 3.4k token。

该连接同样要带 token（`custom-xc-ide`）：

```json
{ "headers": { "X-XC-Token": "<读取所选实例 tokenFile 的内容>" } }
```

### 前置条件

炫彩IDE 必须运行（插件寄生在它进程内）。服务就绪判断：

```bash
python I:\xcide\xc-plugin\XC-MCP-Next\wait_ready.py
```

返回 `ready after ...` 表示选中目标就绪。多个实例时必须增加 `--project` 或 `--instance`，不能把其他实例在线当作目标就绪。

## 状态

**29 个工具全部实现并端到端验证通过**（2026-11-02 第四轮；模块链路已用真实工程编译验证）。

| 工具 | 状态 | 实现方式 |
|---|---|---|
| `xc_status` | ✅ | 实际端口、PID、instanceId、自身状态与模块缓存计数 |
| `xc_get_project` | ✅ | ABI type 8 |
| `xc_parse_status` | ✅ | 读 IDE 内存 +0x4B83EC |
| `xc_reset_parse_count` | ✅ | 同上（写 0） |
| `xc_server` | ✅ | 自身控制 |
| `xc_ide_log` | ✅ | ABI type 2 |
| `xc_build` | ✅ | PostMessage F7 → 实测编译成功 |
| `xc_menu_cmd` | ✅ | PostMessage F7 / Ctrl+F5 |
| `xc_get_output` | ✅ | getHELE(1101) + XEdit_GetText |
| `xc_read_file` | ✅ | 文件 IO；返回 `size`(去 BOM)/`rawSize`(含 BOM)/`bom`/`crlf` |
| `xc_editor_get` | ✅ | ABI type 16 取 hEle + XEdit_GetText |
| `xc_editor_set` | ✅ | 直接写盘，保持 BOM/CRLF（**不用 XEdit_SetText**） |
| `xc_new_file` | ✅ | 写盘 + importFile |
| `xc_add_file` | ✅ | ABI type 12 importFile |
| `xc_add_dir_files` | ✅ | importFile 批量 |
| `xc_list_projects` | ✅ | 递归扫描 `.xcproj`（默认 `depth=6`，跳过 obj/bin/x64/Release 等） |
| `xc_open_in_ide` | ✅ | importFile + open |
| `xc_tree_probe` | ✅ | 文件系统视图（XTree API 会崩，见下） |
| `xc_symbol` | ✅ | ABI type 3 / 4 |
| `xc_goto_function` | ✅ | type 3 + XEdit_SetCurPosEx（实测光标移动） |
| `xc_list_functions` | ✅ | 静态解析 .xc |
| `xc_module_info` | ✅ | ABI type 17（8 字段含 MD5）；`path`=全路径 / `name`=裸文件名 |
| `xc_save_all` | ✅ | PostMessage Ctrl+S |
| `xc_probe_cmd` | ✅ | cmd_id 直接作为 funSet 的 type 调用；默认仅放行 20/21，其余需 allow_unsafe |
| `xc_module_list` | ✅ | 扫 `XC-Module`(+`-dev`) 解析 `//@` 头，标 `inProject`；`withDeps` 带出 `//@依赖` |
| `xc_module_search` | ✅ | 按名称/文件/备注/作者/依赖打分（`dependsOn` 可反查谁依赖某模块） |
| `xc_module_add` | ✅ | 改 `.xcproj` 的 `<modules>`，裸文件名靠 IDE 全局索引 |
| `xc_module_remove` | ✅ | 重建 `<modules>` 块 |
| `xc_module_create` | ✅ | 生成 `module_xxx.h/.cpp`（UTF-8-BOM+CRLF）并自动入工程；支持 `libs`/`dlls`/`includes` 写预编译库注解 |

### 参数与返回细节

| 工具 | 关键参数 | 返回重点 |
|---|---|---|
| `xc_read_file` | `path` | `size` = 去 BOM 后字节数；`rawSize` = 磁盘原始字节数（含 3 字节 BOM）；`bom` / `crlf` 供写回时保持原格式 |
| `xc_list_projects` | `root` / `depth`(默认 6，上限 12) | `count` / `dirsScanned` / `projects[{dir, project_file}]` |
| `xc_module_info` | `path`(全路径) 或 `name`(裸文件名 如 `module_base.h`) | `path`/`file`/`module`/`author`/`version`/`date`/`remark`/`extra`(MD5) |
| `xc_module_search` | `query`(可空) / `dependsOn`(可空) / `limit`(30，上限 500) / `all`(true) | `scanned` / 命中项带 `match`、`deps`；只传 `dependsOn` 即反查 |
| `xc_module_add` | `file` 或 `name` | `added` / `moduleCount` |
| `xc_module_create` | `name` / `alias` / `libs` / `libs86` / `dlls` / `dlls86` / `includes` / `addToProject` | `libsX64` / `libsX86` / `dllsX64` / `dllsX86` / `includes` / `missing[]` / `warnings[]` |

## 目录

| 路径 | 说明 |
|---|---|
| `src/plug_abi.h` / `.cpp` | 插件 ABI 声明与封装 |
| `src/plugin.cpp` | 插件入口、启动流程 |
| `src/http.cpp` | 内置 HTTP 服务（winsock，每连接一线程） |
| `src/instance.cpp` | 进程身份、原子实例登记/token 发布与实时工程信息 |
| `bridge/instances.py` | 共享发现、目标校验及实例枚举 CLI |
| `src/mcp.cpp` | JSON-RPC 2.0 协议层 |
| `src/json.cpp` | 手写 JSON |
| `src/tools.cpp` | 29 工具实现 |
| `src/xcgui_min.h` | 用到的 XCGUI 导入声明 |
| `lib/XCGUI.lib` | x64 导入库（取自 IDE 自带模块） |
| `doc/abi.md` | ABI 逆向实证记录 |
| `doc/api_desc.tsv` | 接口说明表 |
| `xcbuild/` | 命令行无界面编译（只定向重启已验证目标，不按同名进程全杀） |
| `out/` | 编译产物 |

## 接入 AI 客户端

插件在每个 IDE 进程中提供独立 HTTP MCP 服务；首选 `http://127.0.0.1:18766/mcp`，占用时自动分配空闲端口，以下 URL 仅为示例。
（协议 `2024-11-05`，`serverInfo.name = xcide-mcp`）。

**前置条件：炫彩IDE 必须处于运行状态**（插件随 IDE 启动加载，约 6 秒后开始监听）。

### 方式一：HTTP 直连（推荐，客户端支持 streamable-http 时）

```json
{
  "mcpServers": {
    "xc-ide": {
      "type": "http",
      "url": "http://127.0.0.1:18766/mcp",
      "headers": { "X-XC-Token": "<token 文件内容>" }
    }
  }
}
```

默认 token 兼容 `%LOCALAPPDATA%\xc-mcp\token`，也可按实例枚举结果的 `tokenFile` 读取。缺失或错误 token 返回 401；建议同时配置 `X-XC-Instance`，项目保护头详见多实例指南。

本工程 `mcp.json` 改为自动发现唯一实例的 stdio 配置，多实例时增加明确选择器。

### 方式二：stdio 桥接（客户端只支持 stdio 时）

Claude Desktop / Cursor 等只吃 stdio 的客户端，用 `bridge/stdio_bridge.py` 中转：

```json
{
  "mcpServers": {
    "xc-ide": {
      "type": "stdio",
      "command": "python",
      "args": ["I:\\xcide\\xc-plugin\\XC-MCP-Next\\bridge\\stdio_bridge.py"]
    }
  }
}
```

桥接已实测：`initialize` / `tools/list`（29 个工具）/ `tools/call` 三项全通。
桥接每次现读 token：`XC_MCP_TOKEN` 优先，其次选中实例的 tokenFile，最后兼容旧 token 文件；401 只对原目标重读重试一次。
首次请求后固定绑定实例。IDE 重启/目标失效后需重新建立 MCP 连接，不自动改连别的 IDE；按工程选择时，工程切换会被拒绝。
没有实例或匹配不唯一时返回明确错误，请用 `--project` / `--instance` 选择。

### 接入 DSH

DSH 可使用各实例实际 URL 配置 HTTP 连接，或按上述 stdio 桥接配置。不要在多实例下把固定 18766 当作某个工程的永久地址。

### 典型调用顺序

1. `xc_get_project` —— 先确认激活工程（项目树显示的未必是实际编译的那个）
2. `xc_editor_get` / `xc_read_file` —— 读代码
3. `xc_symbol` / `xc_list_functions` —— 定位符号
4. `xc_editor_set` —— 改代码（写盘，需重新打开文件或重启才读到）
5. `xc_build` —— 编译
6. `xc_get_output` —— 读编译输出与错误列表

## 构建与部署

```
build.bat
```

产物 `out\plug_xcmcp.dll` → 复制到 `<IDE根>\data\plug\`，重启 IDE。

- 文件名必须以 `plug_` 开头，否则不被扫描
- 默认首选 **18766**（旧插件曾使用 18765），占用则由系统分配空闲端口；也可设置 `XC_MCP_PORT`
- 启动约 6 秒后，使用 `bridge/instances.py list --json` 查询实际地址与工程

验证：

```
python -I -B test\test_opt2.py --offline
python -I -B test\test_instances.py
python -I -B test\test_native_transport.py
```

HTTP 支持 **keep-alive + pipelining**（连续发送、按序响应），同一 IDE 的工具执行串行，不同 IDE 独立。
单连接上限 1000 个请求；超过 64 个连接时不再复用，最多接受 128 个并发连接。实机隔离验收见 `test/test_multi_ide_live.py`；不要直接运行含业务路径或全局 taskkill 的历史测试。

## 无界面编译（xcbuild）

`xcbuild/` 是一个独立命令行工具，**不看 IDE 界面也能编译 `.xc` 工程**，返回结构化错误列表，适合脚本与 AI 代理做「改代码 → 编译 → 拿错误」闭环。

```bat
cd /d "E:\AIWork\gw2\插件启动器\启动器"
xcbuild.bat build          :: 编译一次，成功 0 / 失败 1
xcbuild.bat build --json   :: 结构化输出
xcbuild.bat watch          :: 改完保存自动编译
xcbuild.bat run            :: 编译并启动调试
xcbuild.bat doctor         :: 环境自检
```

关键结论（都是实测，不是推断）：

- **`.xc → .cpp` 翻译器只存在于 `炫彩IDE.exe` 进程内**。该 exe 无 Export 段，`I:\xcide\` 与 `data\tool\` 下也没有任何独立转译器，自研等于重写整个编译器前端 —— 所以「完全不启动 IDE 进程」不可行，「不看见界面」可行。
- IDE 的进程内「上次编译快照」标志会使重复 F7 跳过编译。因此 xcbuild **每次编译前只定向重启目标 IDE**，新进程以独立端口启动，代价约 6.5s 启动 + 9s 工程加载；其他 IDE 不重启。
- 错误全部带 `.xc` 行号（IDE 已把 C++ 行号映射回来）。三类：`解析错误` / `检查错误` / MSVC `错误 Cxxxx`。
- 坑：C++ 层错误时 IDE 最终仍打印 `错误列表: 错误(0)`，真实信号是 `🐞*编译失败, 错误码: 2`；xcbuild 同时校验三个信号，不会误报成功。

### AI 侧直接调用插件工具（推荐）

「无界面编译 + 调试」已内置进插件工具集（`xc_build` / `xc_menu_cmd` / `xc_get_output`），**不需要另起 MCP 服务**：

```bash
MCP="G:/AItools/skill/_融合后/mcp-cli/scripts/mcp.py"

python "$MCP" call xc_ide xc_read_file  "path=E:\\path\\main.xc"
python "$MCP" call xc_ide xc_build                            # 触发 F7 编译（每次重启 IDE 后首次有效）
python "$MCP" call xc_ide xc_get_output "tailLines=20"        # 读编译输出与错误列表
python "$MCP" call xc_ide xc_menu_cmd "command=compile_all"   # Ctrl+F5：编译并启动调试
python "$MCP" call xc_ide xc_get_output "tailLines=40"        # 读原始输出
```

闭环：`xc_read_file` → 改 → `xc_editor_set`（保持 BOM/CRLF）→ `xc_build` → `xc_get_output` 里的 `main.xc(行号): 错误 …` → 再改。

**关键限制**：F7 在**同一 IDE 生命周期内只有第一次生效**；二次调用 `xc_build` 仍返回 `ok:true`（只表示按键已投递），要用 `xc_get_output` 看输出里是否出现「跳过编译」来判断。
需要连续编译时用命令行版 [`xcbuild/`](xcbuild/README.md)，它只管理明确绑定或自行启动的目标进程。

## 模块管理（让 AI 自己补依赖）

写炫语言功能代码时，如果用到某个能力（线程池、网络、压缩、界面…），不必手工找模块——
让 AI 走这三步：

```bash
MCP="G:/AItools/skill/_融合后/mcp-cli/scripts/mcp.py"
python "$MCP" call xc_ide xc_module_search "query=线程"        # 1. 找现成的
python "$MCP" call xc_ide xc_module_add "file=module_threadpool.h"  # 2. 加进工程
python "$MCP" call xc_ide xc_module_create "name=mytool" "alias=我的工具"  # 3. 没有就新建
```

`xc_module_search` 打分规则：模块名 100 > 文件名 80 > 备注 40 > 作者 10。

### 模块库的位置（约束边界）

模块只来自**两个固定目录**，不扫工程目录、不扫其它任何位置：

```
<IDE 根>\data\XC-Module        主库（本机约 290 个 module_*.h）
<IDE 根>\data\XC-Module-dev    开发库（--all / all=true 时才扫）
```

`<IDE 根>` 不是写死的，插件的推导链是：

1. 取自身 DLL 全路径（`GetModuleFileNameW(g_hSelf)`，正常为 `<IDE 根>\data\plug\plug_xcmcp.dll`）
2. 从该目录向上最多 6 级，找**名字等于 `data`** 的那一层（忽略大小写）
3. 命中层的**父目录**即 IDE 根；再到 `<IDE 根>\data\` 下拼 `XC-Module` / `XC-Module-dev`
4. 找不到 `data` 层时读环境变量 `XC_IDE_ROOT`（并校验其下确有 `data` 目录）；仍失败则返回空串并写日志，不会退回任何写死的盘符

返回值里可直接核对：`xc_module_list` 会给出 `ideRoot` / `rootDir` / `devDir` 三个字段。
本机实测：`ideRoot=I:\xcide`、`rootDir=I:\xcide\data\XC-Module`、
`devDir=I:\xcide\data\XC-Module-dev`。

**扫描缓存与实时性**：每个模块头文件按「路径 + 修改时间 + 大小」缓存解析结果，
目录清单按「目录修改时间」缓存。新增/删除模块文件会改变目录修改时间、改动文件内容会改变
文件修改时间，两者都会让对应缓存立刻失效，所以工具看到的始终是磁盘当前状态；
缓存只省掉重复的 `FindFirstFile` 枚举与头文件解析。`xc_status` 的
`modCacheHits` / `modCacheMisses` 可观察命中情况。

> 之所以用「找 data 层」而不是「固定上溯三层」：原 `plug_mcp.dll` 就放在
> `data\plug\mcp\` 子目录里，固定层数会把 `data` 误当成 IDE 根。

### 生成到哪个模块库（`location`）

`xc_module_create` 的 `location` 决定 `.h`/`.cpp` 落在哪里：

| location | 落点 | 用途 |
| --- | --- | --- |
| `project`（默认） | 工程目录 | 只在本工程用的私有模块 |
| `dev` | `<IDE 根>\data\XC-Module-dev` | 开发中的通用模块（一键生成到这里） |
| `main` | `<IDE 根>\data\XC-Module` | 正式主库 |

选 `dev` / `main` 时，`dir` 参数的含义从「绝对目录」变为**库内相对子目录**，会被自动创建：

```bash
python "$MCP" call xc_ide xc_module_create "name=mytool" "alias=我的工具" "location=dev"
python "$MCP" call xc_ide xc_module_create "name=mytool" "alias=我的工具" "location=dev" "dir=我的分类/子类"
```

返回值会回显 `location` / `moduleRoot` / `moduleDir`。非法值直接报
`location 只能是 project / dev / main`。

> **新建的文件必须重启 IDE 才能被全局索引收录。** IDE 的模块索引在启动时建立，
> 刚写到 `XC-Module-dev` 的模块立刻 `xc_module_info` 会返回「未找到该模块」，
> 重启后即可查到（含子目录里的模块）。这与 `.xcproj` 改动需要重启是同一个原因。

`xc_module_create` 生成的是**合法的炫语言模块骨架**，直接可用：

```cpp
#pragma once
//@模块名称  测试模块
//@版本  1.0.0
//@日期  2026-11-02
//@作者  XC-MCP-Next
//@依赖  module_base.h
//@src "module_mytool.cpp"
//@模块信息结束
//@分组{ 测试模块
//@别名  测试模块_初始化()
int xcl_mytool_init();
//@分组}
```

关键约束（已踩平的坑）：

- `.h`/`.cpp` **必须 UTF-8-BOM + CRLF**，否则中文 `//@别名` 会乱码。
- `<modules>` 里的 `path` 是**裸文件名**，工程目录里不需要真的有这个 .h（IDE 靠全局索引定位）。
- 改 `.xcproj` 后**必须重启 IDE**（或在 IDE 内重载工程）才生效——`xc_module_add` 的返回值里有该提示。
- 模块函数在炫语言里用 `//@别名` 调用；没有别名就用 C++ 名。
- 生成模块后要写真实实现时，`.cpp` 里的 C++ **标识符必须英文**，中文只出现在 `//@别名`。
- 新建模块文件（`location=dev` / `main`）后**必须重启 IDE** 才会被全局索引收录、才能参与编译；`xc_module_add` 改 `.xcproj` 同理。

实测链路（已通过）：`xc_module_create` → `xc_module_add` → 重启 IDE → 在 `main.xc` 写
`返回 测试模块_初始化()` → 编译输出包含 `module_zztest.cpp`、`错误(0)`；故意多传一个参数则精确报
`main.xc(317): 错误 C2660: "测试模块_初始化": 函数不接受 1 个参数` 并给出声明位置 `module_zztest.h(18)`。

### 把开源库封装成模块（预编译库）

需要链第三方库（libcurl、sqlite3、OpenSSL…）时，让 `xc_module_create` 直接把注解写进模块头，
不要在 `.cpp` 里手写 `#pragma comment(lib, ...)`（那样换机器/换目录就断）。

```bash
# 64 位库放模块目录的 x64\ 下，32 位放 x86\ 下；裸名会自动展开成两个平台
python "$MCP" call xc_ide xc_module_create "name=curlwrap" "alias=curl封装" \
  "libs=x64\libcurl.lib" "dlls=x64\libcurl.dll" "includes=native\include"
```

生成的头文件注解区：

```cpp
#ifdef _WIN64
//@lib  "x64\libcurl.lib"
//@复制文件 @当前模块路径 "x64\libcurl.dll"
#else
//@lib  "x86\libcurl.lib"
//@复制文件 @当前模块路径 "x86\libcurl.dll"
#endif
//@包含目录 @当前模块路径 "native\include"
```

- `//@lib` 让 IDE 把该 `.lib` 加进链接命令行（注意注解后**两个空格**）。
- `//@复制文件` 在**生成后事件**里把 dll 复制到 exe 目录。
- `//@包含目录` 让模块自己的 `.cpp` 能 `#include` 到库头文件。
- 只给 `libs` 不给 `libs86` 时，会自动推导出 x86 同名文件并列入 `missing[]`；
  模块目录缺 `x64\`/`x86\` 子目录时 `warnings[]` 会提示 32 位分支链接失败。

端到端实测（2026-11-02，`test/test_modlib_e2e2.py` 12/12）：

- 自己用 `cl /c` + `lib` 造一个 `probe_lib.lib`（符号 `?xcl_probe_lib_value@@YAHXZ`），
  dll 与头文件放到 `x64\` 和 `native\include\`
- 按上面命令建模块 → 头文件出现 `#ifdef _WIN64` 双分支 + 4 条 `//@lib` + 2 条 `//@复制文件`
- `main.xc` 写 `返回 探针库_取值()` → 编译输出出现
  `📁...\native\include`（包含目录生效）、`module_probelib.cpp`（源文件参与编译）、
  `⚡*生成后事件 > 复制文件到exe目录: ...\x64\probe_lib.dll 成功`、`错误(0)`，耗时 7147 ms
- 若在 `.cpp` 里调用 lib 符号而 `//@lib` 失效，链接阶段会直接 `LNK2019`；本次无此错误 ⇒ `//@lib` 真正生效

反查依赖（谁用了我）也用同一个工具：

```bash
python "$MCP" call xc_ide xc_module_search "dependsOn=module_base.h"   # 实测 168 条
python "$MCP" call xc_ide xc_module_search "query=线程" "limit=50"
```

## 安全边界

每个 IDE 进程启动独立的本机回环 HTTP 服务，默认首选 18766、占用时分配空闲端口。token、Host、Origin 与目标身份校验均启用。

### 鉴权

默认复用 `%LOCALAPPDATA%\xc-mcp\token`；首次安全创建采用原子发布，不会因另一实例启动而覆盖有效 token。每个实例还发布自己的 tokenFile。请求必须带其一：

| 头 | 值 |
| --- | --- |
| `X-XC-Token` | token 原文 |
| `Authorization` | `Bearer <token>` |

也可用环境变量 `XC_MCP_TOKEN` 覆盖（优先于 token 文件）。取 token：

```bash
type "%LOCALAPPDATA%\xc-mcp\token"
```

### 请求校验

HTTP 帧在进入请求处理前检查：头部过大返回 431，错误/重复 Content-Length 或不支持的 Transfer-Encoding 返回 400，请求正文超过 4MB 返回 413。

| 检查 | 失败响应 |
| --- | --- |
| 带 `Origin` 头（任何值，含空值） | **403** |
| `Host` 不是当前端口的 `127.0.0.1:<port>` / `localhost:<port>` | **403** |
| token 缺失或不匹配 | **401** |
| `X-XC-Instance` 不匹配 | **409**，不执行请求 |
| `X-XC-Project` 与当前工程/健康快照不匹配 | **409** |
| POST 的 `Content-Type` 不含 `application/json` | **415** |

`OPTIONS` 同样要求鉴权和目标校验。`POST /mcp` 返回 JSON，通知返回 202 空正文；`GET /health` 返回实时身份和工程信息。无 GET SSE 推送流，`GET /mcp` 返回 405。

响应头不再发 `Access-Control-Allow-Origin`（原先的 `*` 通配已删除），改发 `X-Content-Type-Options: nosniff` 与 `Cache-Control: no-store`。

> 因为 `Origin` 一律拒绝，**浏览器里的网页无法调用**这些工具；`GET /health` 同样要求 token。

### 路径白名单

`xc_read_file` / `xc_editor_set` 默认只允许这些根之下：

- 当前工程目录
- `<IDE 根>\data\XC-Module` 与 `XC-Module-dev`
- 插件自身目录

越界返回错误并列出全部允许根；确需读写外部路径时显式传 `allowOutsideProject=true`。

### 宿主内存写入门禁

`xc_reset_parse_count` 直接写 IDE 主模块 `+0x4B83EC`，写前校验：

- PE 头签名（`IMAGE_DOS_HEADER` / `IMAGE_NT_HEADERS64`）
- `rva + len <= OptionalHeader.SizeOfImage`
- 逐区 `VirtualQuery` 必须 `MEM_COMMIT`、非 `PAGE_GUARD` / `PAGE_NOACCESS`、protect 为可写

不匹配则拒绝写入并返回 `wrote:false` + 原因 + `ideImage`（`ts=...` 时间戳与 `image=...` SizeOfImage）。`xc_status` 也回报 `parseCountWritable` 与 `ideImage`。

### 底层 ABI 调用

`xc_probe_cmd` 除 `cmd_id` / `arg0` 外，非白名单命令必须同时给 `allow_unsafe=true` 与 `confirm="I-UNDERSTAND-THE-RISK"`，否则拒绝。默认只放行 `20` / `21`。

### 仍有残余风险

- token 文件对同用户任意进程可读，**同用户下不构成安全边界**（防的是浏览器与跨用户）。
- `xc_probe_cmd` 的 `allow_unsafe=true` + `confirm` 一旦给出，即可调任意 ABI 分支（含会写宿主内存的分支），调用方需自行判断。
- 服务固定绑定回环，不要把端口改成 `0.0.0.0`。


## 已知限制

- **XTree 项目树 API 会崩**：`XTree_GetFirstChildItem(h, nullptr)` 触发访问违例（SEH 也捕获不了，疑似栈问题），`xc_tree_probe` 已改为文件系统视图，不支持树节点选中。
- `xc_editor_set` 采用**直接写盘**（读原文件判断 BOM/CRLF 后原样保持），已实测往返零字节差异；`XEdit_SetText` 那条路径因「开头多插一个字符」已弃用，不要再用。
- `xc_build` 依赖 `XWnd_GetHWND` 把 XCGUI 窗口句柄转成真实 HWND，再 PostMessage 投递 F7；IDE 升级后需复验。
- **`xc_build` 在同一 IDE 生命周期内可能只有首次真实编译**，之后返回「跳过编译」。历史绕过尝试未能可靠复位标志；未修改 IDE 二进制。连续编译使用 `xcbuild` 的目标 PID 定向重启。
- 编译测试未发现额外源码回写，不等于可以丢弃用户编辑。定向关闭遇到保存对话框会停止，不强杀。
- 解析计数器偏移 `+0x4B83EC` 是 3.3.9 硬编码值，换版本会失效（写前有 PE 边界 + `VirtualQuery` 门禁，不匹配时拒绝写入而不是崩）。
- 旧 token 头、显式 `XC_MCP_URL` / `XC_MCP_TOKEN` 保持兼容；多实例请更新连接选择器。本项目不自动修改全局 MCP 配置。
- JSON-RPC 的 **batch 请求**已支持（顶层数组，逐条处理，通知项跳过不产出响应）；孤立代理字符在响应文本里会被替换成 U+FFFD，保证输出是合法 UTF-8。
