# 炫彩IDE 插件 ABI 实证结论

目标：`I:\炫彩ide\炫彩IDE.exe` 3.3.9（x64，5,245,952 B）
实证日期：2026-11-02
样本插件：`I:\炫彩ide\data\plug\mcp\plug_mcp.dll`（471,040 B，无源码）

## 1. 加载与入口

| 项 | 结论 |
|---|---|
| 扫描目录 | `<IDE根>\data\plug\`，含其直接子目录 |
| 文件名要求 | **必须 `plug_` 前缀**，否则完全不被扫描 |
| 已加载插件查询 | IDE 内「查看已加载插件」，输出到 [调试输出] 视图 |

导出入口（4 个，IDE 都会尝试）：

```c
extern "C" __declspec(dllexport) BOOL OnInitPlug(void* funSet, HWND hMainFrame, int ver, int rsv);
extern "C" __declspec(dllexport) BOOL OnPlugInit(void* funSet, HWND hMainFrame, int ver, int rsv);
extern "C" __declspec(dllexport) BOOL OnExitPlug(void);
extern "C" __declspec(dllexport) BOOL OnPlugExit(void);
```

实测参数（2026-11-02，IDE 3.3.9）：

```
OnPlugInit funSet=0x7FF79C990230  hMainFrame=0x121  ver=3  rsv=3
OnInitPlug funSet=0x7FF79C990230  hMainFrame=0x121  ver=3  rsv=1
```

`funSet` 是 IDE 侧函数指针，其 RVA 为 `0x1B0230`（`0x7FF79C990230 - 0x7FF79C7E0000 = 0x1B0230`）。

## 2. funSet 调用约定

```c
struct plug_data_ {
    int   type;      // +0
    int   id;        // +4
    void* v[31];     // +8, +0x10, +0x18, ...
};
int funSet(plug_data_* p);
```

反汇编（RVA `0x1B0230`）：

```asm
mov  rsi, rcx
test rcx, rcx
je   err
mov  eax, dword [rcx]          ; type
dec  eax
cmp  eax, 0x14                 ; type - 1 <= 20  =>  type in 1..21
ja   err
lea  rdx, [0x140000000]        ; 模块基址，函数内自算，不是第二个参数
mov  eax, dword [rdx + rax*4 + 0x1B0CEC]
add  rax, rdx
jmp  rax
```

- `type` 有效范围 **1..21**，`type = 0` 与 `type > 21` 直接返回。
- 跳转表位于 RVA `0x1B0CEC`，21 项，每项为 RVA。
- **返回值恒为 0**：所有分支最终 `jmp 0x1B0CCC`，该处为 `xor eax,eax; ret`。
  因此**不能用返回值判断成功**，只能看出参指针是否被填充。

### type 枚举

| type | 名称 | 分支 RVA |
|---|---|---|
| 0 | error | — |
| 1 | bindEvent | 0x1B0279 |
| 2 | debug_addString | 0x1B02B1 |
| 3 | getSymbol | 0x1B02C6 |
| 4 | getSymbolEx | 0x1B0362 |
| 5 | getView | 0x1B0410 |
| 6 | getHELE | 0x1B05DA |
| 7 | getCurClassName | 0x1B079E |
| 8 | getProject | 0x1B07F0 |
| 9 | code_update | 0x1B0CCC（直通返回） |
| 10 | getParent | 0x1B08B8 |
| 11 | getClassInherit | 0x1B08DE |
| 12 | importFile | 0x1B09AA |
| 13 | threadTask_add | 0x1B09E2 |
| 14 | threadTask_release | 0x1B09F3 |
| 15 | createEditor | 0x1B0A04 |
| 16 | getCodeEditor | 0x1B0B82 |
| 17 | module_info | 0x1B0BDE |
| 18 | enable_ai | 0x1B0BF2 |
| 19 | task_string | 0x1B0C00 |
| 20 | task_plug | 0x1B0CA4 |
| 21 | task_plug_string | 0x1B0CB9 |

## 3. 已实测通过的接口

### type 6 · getHELE

入参 `id` = 窗格 ID，范围 `[1000, 1101]`；出参 `v[0]`（+8）= XCGUI 元素句柄。

实测（IDE 已完全启动后调用）：

| 窗格 ID | 用途 | 句柄 |
|---|---|---|
| 1001 | 项目视图 | 0x16A |
| 1002 | 文档大纲 | 0x18C |
| 1004 | 属性 | 0x206 |
| 1012 | 调试输出 | 0x296 |
| 1030 | （不存在） | 0 |
| 1101 | 调试输出·编辑器 | 0x29E |
| 2002 | MCP 窗格（未被本插件创建） | 0 |

### type 8 · getProject

入参 `v[0]`、`v[1]` 作为查找依据（可为空 = 取当前激活工程）。
出参复用同一批偏移，**调用前必须保存入参**。

实测输出：

| 偏移 | 内容 |
|---|---|
| v[0] (+8) | 工程名 `启动器` |
| v[1] (+0x10) | 工程文件 `E:\AIWork\gw2\插件启动器\启动器\启动器.xcproj` |
| v[2] (+0x18) | 工程目录 `E:\AIWork\gw2\插件启动器\启动器` |

返回的是 IDE 内部 `std::wstring` 缓冲的**临时指针**，必须立即拷贝。

### type 2 · debug_addString

入参 `v[0]`（+8）= `const wchar_t*`，追加一行到 [调试输出] 窗格。

### type 1 · bindEvent

入参 `v[0]`（+8）= 回调指针，写入全局事件表 `0x1404B9290`。

## 4. 关键时序约束

**在 `OnInitPlug` / `OnPlugInit` 内直接调用 funSet 一定失败**（所有出参为空）。
原因：此时 IDE 的窗格与桥接尚未就绪。

实测：初始化后延迟 5 秒再调用，全部接口正常。
推荐的正确做法是注册 `bindEvent` 回调，在 `event_type_createView`（事件号 11）里做窗格相关工作。当前 x64 `Data` 布局实测：`v[0]` 为 `hMainFrame`，`v[1]` 为返回给宿主的 `arrayPanes[0]`，不要覆盖 `v[0]`；旧 AI 示例中按 10 创建窗格会误入 UI 属性事件。

## 5. XCGUI 桥接

拿到窗格句柄后，直接用 XCGUI 公开 API 读写，**无需逆向 IDE 内部偏移**。

已实测通过（读 [调试输出] 窗格）：

```
XEdit_GetLength(hEdit)              -> 2691
XEdit_GetRowCount(hEdit)            -> 37
XEdit_GetText(hEdit, buf, cch)      -> 2691  (完整文本)
XEdit_GetTextRow(hEdit, i, buf, cch)-> 单行文本
XEdit_GetText_Temp(hEdit)           -> const wchar_t* 内部缓冲
```

链接库：`I:\炫彩ide\data\XC-Module\核心[官方]\炫彩界面库\x64\XCGUI.lib`（456,314 B，x64）。

## 6. 编译

```
cl /std:c++17 /EHa /utf-8 /LD /MT /O2
   /I<IDE>\data\VC\VC2022\14.41.34120\include
   /I<IDE>\data\VC\Windows Kits\10\Include\10.0.19041.0\{ucrt,um,shared}
   link /DLL /LIBPATH:... XCGUI.lib kernel32.lib user32.lib
```

必须 **x64**（IDE 为 x64）。

注意事项：
- `build.bat` 中不可用名为 `CL` 的变量保存 `cl.exe` 路径 —— `CL` 是 MSVC 读取的环境变量，会被当成编译选项。
- 多条源文件时不可使用 `/Fo.`（触发 `D8036`），改用 `cd` 到输出目录。

## 7. 与旧版官方 SDK 的差异

官方 SDK（`炫语言手册\用炫彩开发炫彩插件-嵌入代码实现.zip` 内 `plug_test\plug.h`）定义的 **ver=1 ABI 是 `IIDE_Plug*` 纯虚基类 vtable**，在 3.3.9 上**已失效** —— IDE 传入的第一个参数是函数指针，不是 vtable 指针。
现行 ABI 为本文档描述的 `plug_data_*` + `type` 分派模型。


## 8. 各 type 实测布局（2026-11-02 验证）

所有偏移相对 `plug_data_` 结构体起始（`type` 在 +0，`id` 在 +4，`v[]` 从 +8 起）。

### type 3 · getSymbol（按名字查）

| 偏移 | 方向 | 含义 |
|---|---|---|
| +0x08 | 入 | `const wchar_t*` 符号名 |
| +0x10 | 出 | 结构体起点 |

结构体（相对 +0x10）：

| 偏移 | 实测值示例 | 含义 |
|---|---|---|
| +0x00 | 1002 | 符号类型（1002=函数，1001=类） |
| +0x04 | 16 | 起始行号 |
| +0x08 | 19 | 起始列 |
| +0x0C | 7 | 结束列 |
| +0x10 | 12 | 附加字段 |
| +0x14 | 0 | 附加字段 |
| +0x18 | 指针 | 名称 |
| +0x20 | 指针 | 别名 |
| +0x28 | 指针 | 文件路径（按名字查时为空） |

### type 4 · getSymbolEx（三重定位）

| 偏移 | 方向 | 含义 |
|---|---|---|
| +0x04 | 入 | 行号 |
| +0x08 | 入 | 文件全路径 `const wchar_t*` |
| +0x10 | 入 | 符号名 `const wchar_t*` |
| +0x18 | 出 | 结构体起点（布局同 type 3） |

返回值非 0 表示命中。

### type 5 · getView（按文件名取视图）

| 偏移 | 方向 | 实测 |
|---|---|---|
| +0x08 | 入 | 文件名 |
| +0x10 | 出 | nType（8 = xc 源码视图） |
| +0x18 | 出 | `const wchar_t*` 文件名 |
| +0x20 | 出 | hView |
| +0x28 | 出 | **hEle（XCGUI 编辑器元素句柄）** |

### type 11 · getClassInherit

| 偏移 | 方向 | 实测 |
|---|---|---|
| +0x08 | 入 | 类名 |
| +0x10 | 出 | `const wchar_t*` 父类名（实测 `启动器窗口类` → `窗口类`） |

### type 12 · importFile

| 偏移 | 方向 | 含义 |
|---|---|---|
| +0x08 | 入 | 文件全路径 `const wchar_t*` |
| +0x10 | 入 | 低 32 位 = 是否打开（1/0） |

返回非 0 表示成功。**注意：对已在项目内的文件重复调用会累积重复节点。**

### type 16 · getCodeEditor

| 偏移 | 方向 | 实测 |
|---|---|---|
| +0x08 | 出 | `const wchar_t*` 当前文件全路径 |
| +0x10 | 出 | **hEle（编辑器元素句柄）** |

传 `+0x08` 为文件名可指定查询，但实测只认全路径。

### type 7 · getCurClassName

出参 `+0x08` 为指针（在 IDE 模块地址空间内，直接当 `wchar_t*` 读会失败），未使用。

### type 10 · getParent

入参 `id`(+4)、`v[0]`(+8)、`v[1]`(+0x10)，出参 `v[2]`(+0x18)。实测 rc=0 未返回数据，未使用。

## 9. 编译与菜单命令的实现方式

`type` 分派表里没有编译能力（`xc_build` / `xc_menu_cmd` 在原插件里走的是**逆向 RVA 直接调用**）：

- 菜单派发器 RVA `0x1A84E0`，签名 `(void* this, int menuId)`，`menuId` 取值 `0x171` 编译 / `0x174` 重新编译 / `0x172` 编译全部
- 低层构建入口 RVA `0x247290`，签名 `(void*, int, int, void*)`

两者的 `this` 语义未确定，直接调用会崩 IDE。**本工程改用键盘消息投递**：

```cpp
HWND hwnd = XWnd_GetHWND((HWINDOW)g_hMainFrame);   // XCGUI 窗口句柄 → 真实 HWND
PostMessageW(hwnd, WM_KEYDOWN, VK_F7, ...);        // F7 = 编译
PostMessageW(hwnd, WM_KEYUP,   VK_F7, ...);
```

实测有效：投递后 IDE 输出 `编译完成, 耗时: 4768ms`、`错误列表: 错误(0)`。
Ctrl+F5 = 编译并运行。

**关键点**：`g_hMainFrame`（实测值 289）不是 Win32 HWND，`IsWindow()` 返回 false，必须用 `XWnd_GetHWND` 转换（转换后实测为 `2100316` 之类的真实句柄）。


## 10. type 17 · module_info（第二轮补测）

入参三选一：

| 偏移 | 含义 |
|---|---|
| +0x08 | 模块文件全路径 |
| +0x10 | 模块文件名（如 `module_xcgui.h`） |
| +0x18 | 模块名称（需配合 +0x20 的作者名） |
| +0x20 | 作者名（配合模块名） |

出参 8 个 `const wchar_t*`：

| 偏移 | 实测值示例 |
|---|---|
| +0x08 | 文件全路径 |
| +0x10 | `module_xcgui.h` |
| +0x18 | `炫彩界面库` |
| +0x20 | `XCGUI`（作者） |
| +0x28 | `4.0.0`（版本） |
| +0x30 | `2026-03-31`（日期） |
| +0x38 | 备注/更新记录（可能很长） |
| +0x40 | MD5（实测形如 `d5bff07c3f0342fdc25f9de6427468ed`） |

只传模块名（不带作者）会返回 rc=0。

## 11. XEdit_SetText 的破坏性（重要陷阱）

`XEdit_SetText(hEle, text)` 有两个实测问题：

1. **会直接写盘**（不是只改内存缓冲），调用后磁盘文件立即变化；
2. **会在文件开头多写一个字符**（实测多出 `s`，导致文件从 13925 B 变 13926 B，IDE 报 `未知数据类型: s动态数组`）。

因此本工程的 `xc_editor_set` **不使用** `XEdit_SetText`，改为直接写磁盘文件，并保持原有编码契约：

- 检测原文件是否带 UTF-8 BOM，保持
- 检测原文件的换行风格（读原文件前 8KB 找 `\r\n`），保持
- 新文件按扩展名默认：`.xc`/`.xh`/`.xcs` → BOM + CRLF

实测写回同样内容后，文件大小与字节完全不变，编译零错误。

## 12. 仍然未实现的部分

无。24 个工具全部实现，`xc_probe_cmd` 的推断依据见第 13 节。

已知不可实现/已降级的部分：

- **XTree 遍历**：`XTree_GetFirstChildItem(h, nullptr)` 稳定触发访问违例，SEH 无法捕获，`xc_tree_probe` 已降级为文件系统视图。
- **`XEdit_SetText` 写入**：会在文件开头多插一个字符，弃用；`xc_editor_set` 改为直接写盘并保持 BOM/CRLF。
- **同一 IDE 生命周期内的重复编译**：见第 14 节，需重启进程。


## 13. type 20 / 21 与 xc_probe_cmd

`xc_probe_cmd` 的实现依据：原插件描述"默认仅放行已验证语义的 cmd_id 20/21"，而 `funSet` 的 `type` 有效范围正是 1..21，其中 20/21 为 `task_plug` / `task_plug_string`。

因此 `xc_probe_cmd` 直接把 `cmd_id` 当作 `type` 传给 `funSet`：

```
{type = cmd_id, id = arg0}
```

安全门控：默认只放行 20/21，其余必须 `allow_unsafe=true`（实测部分 type 会导致 IDE 崩溃）。
返回 `rc` 与 128 字节结构体 dump，用于诊断。

## 14. 无界面编译（xcbuild）的实证结论

目标：不看见 IDE 界面也能完成「写代码 → 编译 → 拿错误」。逐条结论均为实测，不是推断。

### 14.1 转译器不可剥离

- `炫彩IDE.exe`（5,245,952 B，2025-10-03，**Delphi 编译**：段表含 `_RDATA` / `.fptable`）**没有 Export 段**，无法 `LoadLibrary` 取函数。
- `I:\炫彩ide\` 与 `data\tool\` 下不存在任何独立 `.xc → .cpp` 转译器。
- 自研转译器 = 重写整个炫语言编译器前端（类型映射、类继承与 `[绑定信息]` 注解、`计次循环`/`列表循环` 展开、`到循环尾`、内置函数映射 `xcl_readFile` / `text_split` / `xcl_mkStr` 等），投入不合理。
- **结论：`.xc → .cpp` 必须在 IDE 进程内完成。「完全不启动进程」不可行，「不看见界面」可行。**

### 14.2 「跳过编译」判定在进程内存

IDE 输出 `🚀*跳过编译, 因为代码无任何改变, 不需要做无用功!` 的触发条件**不是**代码内容。已逐项排除：

| 尝试 | 结果 |
|---|---|
| 改磁盘 `.xc` 内容 | SKIPPED |
| `xc_open_in_ide` 重载 buffer（7222→7242） | SKIPPED |
| `XEdit_AddText(h, L" ")` 弄脏 buffer | SKIPPED |
| `os.utime` 刷新 mtime | SKIPPED |
| 删除 `main__.obj` | SKIPPED |
| `Ctrl+F5`（编译+调试） | SKIPPED |
| `SendInput` + `SetForegroundWindow` | SKIPPED |
| **重启后不改任何代码直接 F7** | **COMPILED** |
| **同一生命周期再 F7** | **SKIPPED** |

**判定：标志是进程内的「上次编译快照」，启动后第一次 F7 必编译，之后一律跳过。** 因此 xcbuild 每次编译前重启 IDE。

### 14.3 时序约束

`/health` 就绪（约 6.5s）后**必须再等约 9s**（工程加载完成）才能 open/F7，否则会读到半加载状态并产生假错误（如 `变量 "全局_插件路径" 不是类类型`）。xcbuild 用 `SETTLE = 9.0`。

### 14.4 编辑器 buffer 不落盘

用 100ms 轮询监控 `main.xc`，三次「写入新代码 → 重启 IDE → 编译」全程只有自己写入的 3 次变更事件，编译前后 md5 完全一致。**IDE 不会把 buffer 写回磁盘**，`xc_editor_touch` 这类内存操作对源文件零污染。该工具因对「跳过编译」无效，已从插件移除。

### 14.5 错误信号

三类错误都带 `.xc` 行号（IDE 把 C++ 行号映射回源文件）：

- `🐛解析错误: <path>(<L>), 列(<c1>-<c2>)` + 下一行原因
- `🐛检查错误: <path>(<L>), 列(<c1>-<c2>)` + 下一行 `未定义:xxx`
- `<path>(<L>): 错误 Cxxxx: …` + `提示: …`

**关键陷阱**：C++ 层错误时 IDE 最终仍打印 `----- 🐞错误列表: 错误(0)-----`，真实信号是 `🐞*编译失败, 错误码: 2`。判定必须同时看 `failCode`、`错误列表` 计数与错误行，只看最后一行会误判成功。

### 14.6 IDE 实际使用的编译环境

IDE 打印的包含/库目录来自 `C:\Program Files (x86)\Windows Kits\10\...\10.0.26100.0` 与 `I:\炫彩ide\data\VC\VC2022\14.41.34120`（`cl.exe` / `lib.exe` / `link.exe` 均在 `bin\Hostx64\x64`）。

补充：`main__.cpp` 也可手工脱离 IDE 编译链接（已实测产出可运行 GUI 程序），但**前提是已有 `.cpp`**，故不改变 14.1 的结论。

## 16. 模块管理工具（第 24-28 个工具）

**背景**：写炫语言功能代码常需要模块（线程池、网络、压缩…）。IDE 自带 285+ 个模块，
但 AI 无法知道有哪些、也不知道怎么加。这 5 个工具把「发现 → 加入 → 新建」打通。

### 16.1 模块是怎么被找到的（关键机制）

`.xcproj` 的 `<modules>` 里 `path` 是**裸文件名**：

```xml
<modules>
    <item path="module_base.h" name="基础模块" />
    <item path="module_xcgui.h" name="炫彩界面库" />
</modules>
```

工程目录里**并没有**这些 .h —— IDE 用全局索引定位。这与 `xc_module_info`（type 17）
的行为一致：只传 `module_base.h` 就能拿到
`I:\炫彩ide\data\XC-Module\核心[官方]\基础模块\module_base.h`。

模块库根目录**只有两位**，是硬约束边界（用户明确要求，不扫工程目录、不扫其它位置）：

- `<IDE 根>\data\XC-Module`
- `<IDE 根>\data\XC-Module-dev`

`<IDE 根>` 由插件运行时推导（`src/tools.cpp` 的 `ideRootDirW()`）：

```
GetModuleFileNameW(g_hSelf)            -> <IDE 根>\data\plug\plug_xcmcp.dll
parentDirW                             -> <IDE 根>\data\plug
向上最多 6 级找名字 == "data" 的目录   -> <IDE 根>\data
parentDirW                             -> <IDE 根>
```

找不到 `data` 层时依次退回：环境变量 `XC_IDE_ROOT` → 字面量 `I:\炫彩ide`。
**不能用「固定上溯三层」**：原 `plug_mcp.dll` 位于 `data\plug\mcp\`，固定层数会把
`data` 误当 IDE 根，导致扫到 0 个模块。

配套函数：`parentDirW` / `baseNameOfW` / `ideRootDirW` / `moduleRootsW(bool, std::vector<std::wstring>&)`。
`scanAllModules` 与 `doModuleList` 都走 `moduleRootsW`；`doModuleList` 额外返回
`ideRoot` / `rootDir` / `devDir` 三个字段供核对。

本机实测（2026-11-02）：`ideRoot=I:\炫彩ide`、`rootDir=I:\炫彩ide\data\XC-Module`、
`devDir=I:\炫彩ide\data\XC-Module-dev`，`all=true` 时 `totalScanned=293`（插件实际扫描计数，
含嵌套目录内的同名文件；`find` 口径为 283 + 2 = 285），与改动前一致。

扫描到的 `module_*.h` 约 **293 个**（排除 `obj/`），21 个顶层分类目录
（常规 86、22897350-易加加模块 28、网络 21、炫扩展 20…）。

### 16.2 模块头注解格式

```
#pragma once
//@模块名称  基础模块
//@版本  1.0.0
//@日期  2026-11-02
//@作者  XC-MCP-Next
//@模块备注  只把这一行读进来当 remark
//@依赖  module_base.h
//@src "module_xxx.cpp"
//@模块信息结束
```

解析规则：从文件头起逐行扫 `//@` 键值，遇到 `//@模块信息结束` 停止（也受 320 行上限约束）；
文件是 UTF-8-BOM，读的时候要先剥 3 字节 BOM，否则第一个键名会带上 `\xef\xbb\xbf`。

### 16.3 编码铁律（踩过的坑）

`xc_module_create` 首版生成的文件**没有 BOM、没有 CRLF** —— 根因是 `writeFileKeepEnc`
只为 `.xc/.xh/.xcs` 的新文件启用默认 BOM+CRLF，`.h/.cpp` 不在名单里。实测：

```
module_zztest.h bom= False crlf= 0 bytes= 453     # 修复前
module_zztest.h bom= True crlf= 19 bytes= 475     # 修复后
```

修法是把 `.h/.hpp/.cpp/.cc` 也加进默认名单。**中文 `//@别名` 在没有 BOM 时会乱码**，
所以这条是硬要求，不是风格问题。

### 16.4 端到端实证（2026-11-02）

在真实工程 `E:\AIWork\gw2\插件启动器\启动器` 上跑通：

1. `xc_module_create name=zztest alias=测试模块` → 生成 `module_zztest.h`(475 B) / `.cpp`(76 B)
2. `xc_module_add file=module_zztest.h` → xcproj 从 2867 B 变 2922 B，`<modules>` 多一条
3. 重启 IDE（加载新 xcproj）
4. `main.xc` 追加 `函数 整型 模块调用测试()` / `返回 测试模块_初始化()`
5. 编译 → 输出出现 **`module_zztest.cpp`**，`----- 🐞错误列表: 错误(0)-----`
6. 故意传参 `测试模块_初始化(9)` → 精确报错：
   `main.xc(317): 错误 C2660: “测试模块_初始化”: 函数不接受 1 个参数`
   `module_zztest.h(18): 提示: 参见“测试模块_初始化”的声明`

说明：模块骨架能被 IDE 识别、`//@别名` 能在炫语言里调用、编译真实发生、错误能定位到新代码。

### 16.5 写新模块时的注意点

- 炫语言用 `//@别名` 调用；没写别名就只能用 C++ 名（如 `xcl_zztest_init`）。
- 炫语言**不支持重载**，同功能多态要写成 `func()` / `func2()`。
- `.cpp` 里的 C++ 标识符必须是英文，中文只允许出现在 `//@别名`。
- 改完 `.xcproj` 必须重启 IDE 才生效（工具返回值里带了这句提示）。
- 若要让模块把 C++ 代码也带上，按 `//@src "module_xxx.cpp"` 声明（create 已自动写好）。

### 16.6 参数与返回字段的三处修正（2026-11-02 回归暴露）

29 工具全量回归暴露三个口径问题，均已修复并重测通过（40/40）：

| 现象 | 根因 | 修正 |
|---|---|---|
| `xc_read_file` 返回 `size=13922`，磁盘是 13925 B | `readFileUtf8` 会剥掉 3 字节 UTF-8 BOM，`size` 是剥后的内容长度 | 保留 `size`（去 BOM）语义，**新增 `rawSize`（`GetFileAttributesExW` 取磁盘原始字节）**、`bom`、`crlf` 三个字段；断言改用 `rawSize` |
| `xc_module_info path=module_base.h` 返回「未找到该模块」 | `path` 走 ABI type 17 的**全路径分支**（`0x1402C6F60`），裸文件名不在该分支 | 裸文件名必须用 **`name`** 参数（`+0x10` 分支 `0x1402C7160`，走 IDE 全局模块索引）。两个分支实测均返回完整 8 字段 |
| `xc_list_projects root=E:\AIWork\gw2` 返回 `count:0` | 原实现只扫 root 的直接子目录一层，`.xcproj` 在 `插件启动器\启动器\` 这种二级目录下 | 改为递归 `scanProjectsRec`：`depth` 默认 6、上限 12，跳过 `obj/bin/node_modules/.git/.vs/x64/x86/Debug/Release` 等；返回新增 `dirsScanned` / `maxDepth`。实测 `E:\AIWork\gw2` → `count:5`（启动器 / 插件宿主 / 插件宿主32 / 示例插件 / 示例插件32），`dirsScanned:17` |

递归扫描的目录过滤用 `skipProjDirW`（`_wcsicmp` 忽略大小写），避免进 `obj` 后把中间产物目录里的临时工程也当成真工程。

## 17. 模块生成位置（`location`）与索引时机

### 17.1 `xc_module_create` 的 `location` 三值

| location | 落点 | `dir` 的含义 |
| --- | --- | --- |
| `project`（默认） | `getProjectRaw` 拿到的工程目录 | 绝对目录（可为空 = 工程根） |
| `dev` | `moduleRootsW(true)[1]` = `<IDE 根>\data\XC-Module-dev` | 库内相对子目录，自动创建 |
| `main` | `moduleRootsW(true)[0]` = `<IDE 根>\data\XC-Module` | 库内相对子目录，自动创建 |

实现要点：
- 相对子目录处理 = `/`→`\`、剥前导分隔符、逐段 `CreateDirectoryW`（`ensureDirW`）。
- 返回新增 `location` / `moduleRoot` / `moduleDir`，便于调用方核对实际落点。
- 非法值返回 `location 只能是 project / dev / main`，不落盘。

### 17.2 新模块文件必须重启 IDE 才进全局索引

实测（2026-11-02）：`location=dev` 生成 `module_devprobe.h` 后立即
`xc_module_info name=module_devprobe.h` → `{"error":"未找到该模块"}`；
**重启 IDE 后**同一调用返回完整 8 字段（`module=开发库探针`、
`path=I:\炫彩ide\data\XC-Module-dev\module_devprobe.h`），
子目录里的 `module_devprobe2.h` 同样可查。

结论：IDE 的模块全局索引在**启动时建立**，运行期间新增/删除模块文件不会被感知。
这与 `.xcproj` 的 `<modules>` 改动需要重启是同一个原因，两者都要重启才生效。

### 17.3 端到端验证数据（`test/test_create_dev.py` 21/21）

1. `xc_module_create name=devE2E alias=开发库端到端 location=dev addToProject=true`
   → `.h` 490 B / `.cpp` 76 B / BOM true / CRLF 19，`moduleRoot=...\XC-Module-dev`
2. 重启 IDE → `xc_module_info` 收录成功
3. `main.xc` 追加 `函数 整型 开发库端到端测试()` / `返回 开发库端到端_初始化()`（13925 → 14009 B）
4. `xc_build` → 输出含 `module_devE2E.cpp`、`错误(0)`
5. 清理后 `main.xc` 13925 B / md5 `36f26227294078b1de3b2cea29326555`，
   `启动器.xcproj` 2867 B / md5 `e338f51c6e62ad20d095dc834c66d168`

另：`xc_module_add` → `xc_module_remove` 往返后 `.xcproj` 字节 md5 精确复原（2867 B 不变），
即两条工具对该文件是**字节幂等**的。

## 17. 预编译库注解与依赖反查（2026-11-02 端到端实证）

### 17.1 `//@lib` / `//@复制文件` / `//@包含目录` 的真实效果

模块头里这三类注解由 IDE 在**编译期**消费，不是给编译器看的注释：

| 注解 | 作用点 | 实测证据 |
|---|---|---|
| `//@lib  "x64\x.lib"` | 链接命令行 `/LIBPATH` + 追加 lib 名 | 无此注解时调用 lib 符号会 `LNK2019`；有则链接通过 |
| `//@复制文件 @当前模块路径 "x64\x.dll"` | 链接成功后的**生成后事件** | 输出 `⚡*生成后事件 > 复制文件到exe目录: ...\x64\probe_lib.dll 成功` |
| `//@包含目录 @当前模块路径 "native\include"` | 编译命令行 `/I` | 输出 `📁E:\AIWork\gw2\插件启动器\启动器\native\include` |
| `//@src "module_x.cpp"` | 把该 cpp 加入编译列表 | 输出里出现 `module_probelib.cpp`，并产出 `Debug64\obj\module_probelib.obj` |

C++ 模块头要按平台分支写，`.xc` 模块才用 `//@lib64` / `//@复制文件64`：

```cpp
#ifdef _WIN64
//@lib  "x64\x.lib"
//@复制文件 @当前模块路径 "x64\x.dll"
#else
//@lib  "x86\x.lib"
//@复制文件 @当前模块路径 "x86\x.dll"
#endif
```

注意 `//@lib` 后是**两个空格**（IDE 按固定列解析）。

### 17.2 模块头的 `//@别名` 解析非常严格

给模块补导出函数时，`//@别名` 后面的 C++ 声明必须是**纯声明**：

```cpp
//@别名  探针库_取值()
int xcl_probe_lib_value();          // 正确
// extern "C" int xcl_probe_lib_value();   // 错误：解析器不认，别名不注册
```

写成 `extern "C"` 前缀会让模块头解析器跳过该声明，`main.xc` 里调别名报
`🐛检查错误: main.xc(317), 列(4-12)` / `未定义:探针库_取值`。
符号本身用什么链接方式由 `.cpp` 决定；本次用纯 C++ 符号（`?xcl_probe_lib_value@@YAHXZ`）两端一致。

### 17.3 用脚本改模块头时必须先做换行规范化

生成的头文件是 **CRLF**。Python 里按 `"//@分组}\n"` 找锚点会直接失配（实际是 `"//@分组}\r\n"`），
表现为「脚本执行成功但文件没变化」。正确做法：

```python
t = open(p, "rb").read().decode("utf-8-sig").replace("\r\n", "\n")
...  # 在 LF 视图上做文本操作
open(p, "wb").write(b"\xef\xbb\xbf" + t.replace("\n", "\r\n").encode("utf-8"))
```

### 17.4 依赖注解的实际分布（`I:\炫彩ide\data\XC-Module`）

`//@依赖` 共 213 处，其中 24 处带引号。写法极不统一，解析必须同时容忍：

- `module_base.h`（最常见，133 处，无引号）
- `"module_cxxcore.h"`（19 处，带引号）
- `module_base.h, module_com.h`（逗号分隔，3 处）
- `module_base.h,module_yyy.h,module_com.h,module_e_md5.h`（逗号无空格，1 处）
- `module_winhttp.h, module_yyjsonobj.h`
- `无`（5 处，必须跳过）

因此 `parseModuleHeader` 用 `, ; 空格 \t " ` 作分隔符，并显式过滤 `无` 与空项。
`xc_module_search dependsOn=module_base.h` 实测命中 **168** 个模块（`scanned:293`），
`dependsOn=module_kyozy_std.h` 命中 1 个（`module_kyozy_coost.h`）。

### 17.5 `xc_module_search` 的打分与 limit

| 命中位置 | 加分 |
|---|---|
| 模块名（`//@模块名称`） | 100 |
| 文件名 | 80 |
| 备注 | 40 |
| 依赖项（`deps`） | 60 |
| `dependsOn` 精确或子串 | 200 |

`limit` 默认 30、**上限 500**（早期上限 200 会把 168 条依赖命中截断成 30，已放宽）。

## 18. 交付前安全评审与修复记录

2026-11-02 对 XC-MCP-Next 做过一次三方评审（架构 / 安全 / 验收边界），确认 5 个数据安全缺陷 + 若干技术债。以下为已修复项与验证方式；未修复项在 18.7 单列。

### 18.1 覆盖保护（B1）

`doModuleCreate` 原先只在返回值里报 `headerExisted`，不阻止写入，同名二次生成会静默覆盖已有 `module_<name>.h/.cpp`。现改为：目标文件存在且未传 `force=true` 时直接返回 `{ok:false, error, needForce:true}`，一个字节都不动。

判定同时看 `.h` 与 `.cpp`（任一存在即拦），因为单独缺一个也属于覆盖场景。

验证：同名二次 create → `ok:false`；带 `force=true` → `ok:true` 且 md5 变化；不带 force 时 md5 与首次一致。

### 18.2 目录穿越拦截（B2）

`location=dev|main` 时 `dir` 被当作模块库内相对子目录，但原实现只做 `/`→`\` 和剥前导分隔符，`dir="..\..\..\Windows"` 会写到库外。现改为：

1. 按 `\` 切段，任一段等于 `..` 直接拒绝；
2. 任一段含 `:` 直接拒绝（挡盘符与 NTFS 数据流）；
3. 拼完整路径后 `GetFullPathNameW` 规范化，再断言以 `<moduleRoot>\` 为前缀（`_wcsnicmp`，忽略大小写）。

三道都过才 `ensureDirW`。验证：`..\..\..\_escape_probe` → `ok:false` 且库外目录不存在；`C:\Windows` → `ok:false`；`验证分类/子目录` → 正常创建。

### 18.3 原子写（B3）

`writeFileKeepEnc` 原先 `CreateFileW(CREATE_ALWAYS)` 直接截断目标再写，中断（崩溃 / kill / 磁盘满）会留下 0 字节或半截文件——受影响的是 `.xcproj`、`main.xc`、模块头。

现改为 tmp + rename：

1. 写 `<path>.xcmcp_tmp`；
2. `FlushFileBuffers`；
3. `MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)`；
4. 任一步失败删 tmp 并返回错误，原文件保持原状。

验证：`xc_module_add` → `xc_module_remove` 往返后 `.xcproj` md5 回到 `e338f51c6e62ad20d095dc834c66d168` / 2867 B，且目录里没有 `.xcmcp_tmp` 残留。

### 18.4 IDE 根目录推导去掉字面量兜底（B4）

`ideRootDirW()` 原三级降级：向上找 `data` → `XC_IDE_ROOT` → 字面量 `I:\炫彩ide`。第三级在他人机器上会静默指向不存在的盘符，配合 `ensureDirW` 还会在错误位置建目录。

现改为两级：向上找 `data`（最多 6 级，`_wcsicmp` 忽略大小写）→ `XC_IDE_ROOT`（且校验其下确有 `data` 目录）。都失败返回空串并写日志；`moduleRootsW` 遇到空串直接返回空列表，调用方报「找不到 XC-Module 目录」。

验证：本机 `ideRoot` 仍为 `I:\xcide`，`totalScanned` 仍为 293。

### 18.5 关闭期连接线程收尾（B5）

`http::stop()` 原只 `WaitForSingleObject(accept 线程, 3000)` 就 `WSACleanup()`，正在 `handleOne` 里的连接线程继续用已失效的 socket。现在加 `volatile LONG g_connCount`：`connProc` 进入 `InterlockedIncrement`、退出 `InterlockedDecrement`；`stop()` 在 `WSACleanup()` 前最多等 3 秒（300 × 10ms）直到计数归零。

### 18.6 移除脏写的输出窗格清空（R7）

`xc_get_output` 的 `clear=true` 分支原先调 `XEdit_SetText(h, L"")`。该 API 在本 IDE 上会「在文件开头多插一个字符」并直接写盘（曾把 `main.xc` 从 13925 B 改成 13926 B、首行变成 `s动态数组<文本型>`）。现已删除该调用，固定返回 `cleared:false`，并在带 `clear` 时回一条 `clearNote` 说明禁用原因。

### 18.7 R1 鉴权（token + Origin + Host + CORS 收紧 + Content-Type）

> **1.2.0 起 superseded**：token 认证已取消；旧 `X-XC-Token`/`Authorization` 头仅为兼容并被忽略，tokenFile 也仅保留为兼容元数据。以下内容记录 1.1.0 及更早版本的历史实现。

`http.cpp` 7,740 → 13,239 字符。实现要点：

- 首行 `#define _CRT_RAND_S`（**必须是文件第一个 token**，否则 `rand_s` 报 `C3861`）。
- token 来源优先级：环境变量 `XC_MCP_TOKEN` → `%LOCALAPPDATA%\xc-mcp\token` → 随机生成并写文件（`rand_s` 32 字节 hex）。
- `sendResponse` 删除 `Access-Control-Allow-Origin: *` / `Allow-Headers: *` / `Allow-Methods`，改发 `X-Content-Type-Options: nosniff` / `Cache-Control: no-store`。
- `handleRequest` 校验链：请求带任何 `Origin` 头（含空值）→ 403；`Host` 不在 `127.0.0.1:<port>` / `localhost:<port>` → 403；token（`X-XC-Token` 或 `Authorization: Bearer`）不符 → 401；`OPTIONS` → 204；POST 的 `Content-Type` 不含 `application/json` → 415；`Content-Length > 4MB` → 413（解析阶段即拒绝），裸体 `> 8MB` → 413。
- `GET /health` 同样要 token。

调用方同步：`mcp.py` 新增 `${file:...}` / `${env:...}` 展开（`_expand_value` / `_expand_map`），`servers.json` 的 `xc_ide` 改用 `"headers": {"X-XC-Token": "${file:%LOCALAPPDATA%\\xc-mcp\\token}"}`；`test/` 下 17 个脚本统一改用 `mkreq()`（自动读 token 文件并塞头）。

验证 `test_r1_auth.py` **19/19**：无 token 401 / 错 token 401 / `X-XC-Token` 与 Bearer 均 200 / 带 Origin 403（含同源）/ 伪造 Host 403 / `text/plain` 与无 Content-Type 415 / 5MB 413 / `tools/call xc_status` 仍 200。

> 顺带修的一个连接层问题：`connProc` 原在 `shutdown(SD_SEND)` 后立刻 `closesocket`，客户端还在发大 body 时会收 RST（`WinError 10053`）。现加 `SO_RCVTIMEO=2s` 后循环 `recv` 排空缓冲再关，413 能被客户端正常读到。

### 18.8 R2 路径白名单

新增 `bool pathAllowed(const std::wstring& pathIn, bool allowOutside, std::string& err)`：`GetFullPathNameW` 规范化 + `/`→`\`，允许根 = 当前工程目录 + `moduleRootsW(true)` 的两个模块库 + 插件自身目录；前缀比较要求下一字符是 `\`（防 `/proj2` 命中 `/proj`）。`doReadFile` / `doEditorSet` 新增 `allowOutsideProject` 参数，越界时返回错误并列出全部允许根。

### 18.9 R5 宿主内存写入门禁

新增 `bool ideWritableAt(long long rva, size_t len, std::string* why)`：校验 `IMAGE_DOS_HEADER` / `IMAGE_NT_HEADERS64` 签名 → `rva + len <= OptionalHeader.SizeOfImage` → 逐区 `VirtualQuery` 要求 `MEM_COMMIT`、非 `PAGE_GUARD`/`PAGE_NOACCESS`、protect 为可写。新增 `std::string ideImageTag()`（`ts=%08X,image=%X`）。`readParseCount()` / `writeParseCount(int, std::string*)` 均先过门禁；失败时 `xc_reset_parse_count` 返回 `{ok:false, wrote:false, error, ideImage}`，一个字节都不写。`xc_status` 增返 `ideImage` / `parseCountWritable` / `parseCountBlockReason`。

实测：`ideImage = ts=69DB26DC,image=597000`、`parseCountWritable: true`。

### 18.10 R11 `xc_probe_cmd` 二次确认

新增 `confirm` 参数：非白名单 `cmd_id`（默认只放行 `20` / `21`）必须同时给 `allow_unsafe=true` 与 `confirm="I-UNDERSTAND-THE-RISK"`，否则返回 `allow_unsafe 已给但缺少二次确认`。

### 18.11 R1x `<modules>` XML 安全

新增 `xmlEsc` / `xmlUnesc` / `xmlAttr(line,key)`；**重写 `writeProjectModuleItems`**：先解析 `<modules>` 内所有原始 `<item ...>` 行（自闭合与 `<item></item>` 两种都支持），写回时按 `path` 匹配原行、**保留其额外属性**，只替换 `name="..."` 的值；未匹配则新建标准行；`path` / `name` 一律 `xmlEsc`。`doModuleRemove` 读入后对 `items` 做 `xmlUnesc`。

> 编译坑：这三个函数定义在 `writeProjectModuleItems` **之后**，必须先加前向声明，否则报 `C3861: “xmlEsc”: 找不到标识符` + `C2668: replace 对重载函数的调用不明确`。

### 18.12 R6x `xc_build` 语义澄清

返回值新增 `status: dispatched|failed` 与 `compiled: null`，note 明确「`ok`/`status` 只表示快捷键已投递，不代表编译成功；必须用 `xc_get_output` 看是『错误列表』还是『跳过编译』」。

### 18.13 R12 JSON-RPC batch

`mcp::handle` 拆为 `handle` + `handleOne`：顶层数组逐条处理，通知项（无 `id`）跳过不产出响应，全为通知时返回空串（HTTP 层回 202）；空数组回 `-32600`。

### 18.14 R8 孤立代理字符净化

新增 `sanitizeUtf8`：逐码点校验（拒绝超长编码 `C0/C1`、代理区 `D800–DFFF`、`> 0x10FFFF`、截断序列），非法部分替换为 `EF BF BD`（U+FFFD）。`tools/call` 的文本输出统一过一遍，保证响应是合法 UTF-8。

### 18.15 其余技术债

| ID | 修法 |
|---|---|
| R9 | `xc_module_remove` 新增 `project` 参数；未传才回退 `getProjectPaths`，错误文案改为「取不到工程文件（IDE 未打开工程，且未传 project）」 |
| R10 | `xc_module_create` 的 `dir` 为空时回退读 `subdir` |
| R13 | 载荷上限 4MB/8MB → 413（原 32MB 静默 return 无响应） |
| R17 | 新文件判定只看「文件是否存在」，不看调用方传的 `bom`/`crlf`，避免 `existed` 误判导致强制 BOM |

### 18.16 仍未修复（明确声明）

| ID | 问题 | 现状 |
|---|---|---|
| R1-residual | token 文件对同用户任意进程可读 | 设计接受：防浏览器与跨用户，不防同用户 |
| R11-residual | `allow_unsafe=true` + `confirm` 一旦给出即可调任意 ABI 分支 | 设计接受：二次确认是唯一闸门 |
| R16 | `plug_abi.cpp` 调用点 SEH 覆盖不全 | 未做，`xc_probe_cmd` 的崩溃风险仍存在 |

### 18.17 复现命令

```bat
cd I:\xcide\xc-plugin\XC-MCP-Next
build.bat
python test\test_r1_auth.py     :: 19 项，鉴权与请求校验
python test\test_p0fix.py       :: 18 项，B1/B2/B3/B4/R7
python test\test_all29.py       :: 40 项全量回归
python test\test_opt2.py        :: 12 项，keep-alive/BOM/缓存/桥接
python test\test_modlib.py      :: 32 项模块库回归
```

> 跑测试前需先部署：`python ide_start.py kill` → 复制 `out\plug_xcmcp.dll` 到 `I:\xcide\data\plug\` → `python ide_start.py start`（DLL 被运行中的 IDE 占用，不 kill 会 `Device or resource busy`）。

## 19. 第二轮优化（opt2：keep-alive + 扫描缓存 + 健壮性）

对外契约（29 个工具、参数、返回字段、端口、token 位置、Origin/Host 规则）全部不变。

### 19.1 HTTP 连接复用与管线化

`http.cpp` 由「一连接一请求」改为每连接循环处理：`readRequest` 维护读缓冲，
按 `Content-Length` 精确切分，管线化的多个请求逐条响应；响应统一带
`Connection: keep-alive`（客户端要 `close`、HTTP/1.0、或并发连接 > 64 时回 `close`）。
单连接最多 1000 个请求。`SO_RCVTIMEO` 15s，空闲连接超时关闭；`TCP_NODELAY` 去掉 Nagle 延迟。
`sendResponse` 在 body ≤ 16KB 时把头和体合并成一次 `send`，减少小响应系统调用。

`xc_server stop` 由 handler 线程触发时传 `stop(fromHandler=true)`：跳过等待连接线程与
`WSACleanup`（否则会等待自己/卸载 winsock），真正的清理留给 host 线程。

### 19.2 请求解析与异常安全

- 请求头单次解析为小写名字 → 原值表（`Headers::find/get/has`）；token 比较不再被大小写化。
- `readRequest` 强制 64KB 头上限、4MB `Content-Length`（超限直接 413 并关闭）、8MB 缓冲上限；
  POST 无 `Content-Length` 时回完响应即关闭，避免下一请求边界错位。
- `handleRequest` 全程 `try/catch`，handler 抛异常回 500 而不是拖垮连接线程（进程仍存活）。
- `tools::call` 外层再包一层 `try/catch`，工具内部异常转成 `jsonErr("工具内部异常: ...")`。
- `mcp::handle` 输出过 `sanitizeUtf8`：合法 UTF-8 直接原样返回（零拷贝），
  非法字节替换为 U+FFFD。

### 19.3 模块扫描缓存

模块库主库有约 2090 个子目录、1.7 万个目录项，`FindFirstFile` 全量枚举约 26ms/次。
两级缓存：

| 缓存 | 键 | 失效条件 |
|---|---|---|
| 目录清单 `DirListing` | 目录全路径 | 目录 `LastWriteTime` 变化（增删/改名条目） |
| 头文件解析 `ModCacheEntry` | 文件全路径 | 文件 `LastWriteTime` 或大小变化 |

目录 mtime 校验用 `GetFileAttributesExW`；清单未变时不再枚举，只对 294 个模块文件
逐个 stat 判断内容是否改动。新增/删除模块立即生效（目录 mtime 变），改动文件内容也立即生效
（文件 mtime 变）。`xc_status` 增返 `modCacheHits` / `modCacheMisses`。
缓存分别以 4096 / 8192 条为上限兜底清理。

实测（本机，配平后净成本）：`xc_module_list` 51.9ms → 15.9ms，
`xc_module_search` 49.1ms → 14.7ms。剩余成本是 1350 次目录/文件元数据查询（约 16ms），
已是「保持实时性」前提下的下限。

### 19.4 工具链 token

`bridge/stdio_bridge.py`、`xcbuild/xcbuild.py`、`wait_ready.py`、`test/` 脚本统一：
`XC_MCP_TOKEN` 环境变量优先，否则现读 `%LOCALAPPDATA%\xc-mcp\token`；请求带
`X-XC-Token`，遇 401 重读 token 再试一次（IDE 重启换 token 后无需重启客户端）。
`xcbuild.py` 的 `_find_ide_root()` 增加 `XC_IDE_ROOT` 与脚本位置上溯，`health()` 也带 token。
`parse_output` 收紧为「必须出现『编译完成』且无『跳过编译』且错误码为空且错误数为 0」才算成功。

### 19.5 其他修正

- `xc_read_file` 的 `bom` 字段此前恒为 false，现按文件头 `EF BB BF` 如实返回。
- `xc_editor_set` 新增 `bom` 参数（字符串 `"1/true/yes/on"` 或布尔），可强制加/去 BOM；
  未传时保持原编码。
- `w2u` 的缓冲区长度改为按 `MultiByteToWideChar` 返回值分配（原来多分配一个 `wchar_t` 未 `resize`）。
- `xc_server start` 不再丢失 handler；`start` 失败会复位 winsock 状态。

### 19.6 验证

- `test/test_opt2.py` 12/12：token 优先级、`parse_output` 判定、IDE 根环境变量、
  桥接批量/标量错误、同 socket 连续请求、管线化两请求、700KB 分片 body、Origin/Host/token、
  超限 `Content-Length` 413、BOM/CRLF 往返与 `bom` 覆盖、模块缓存新增/修改/删除、桥接实连。
- `test/test_all29.py` 40/40、`test/test_batch_sanitize.py` 23/23。
- 基准：`test/bench_opt2.py base|new 60`，输出 `doc/perf-opt2-*.json`。
