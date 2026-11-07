# XC-MCP-Next 性能与体积优化对照（PERF）

本轮只优化「同一语义下的重复计算」，**不改任何对外契约**：29 个工具名、参数名、
`enum` 取值、返回字段与语义、`protocolVersion`、端口 18766、token 文件位置、
`Origin`/`Host` 安全校验逻辑全部保持原样。

## 1. 测量方法

单次 HTTP 往返的耗时里有约 0.31–0.35 ms 是固定成本（每连接一线程 + 排空 + 线程调度），
会随机器状态漂移，直接比中位数会把真实收益淹没。因此采用**配平法**：

```
每次工具调用前后各发一次 ping，取两者均值为本次 HTTP 地板，
报告「净成本 = 工具耗时 − 地板」，地板漂移被抵消。
```

- 每个指标 N=200，两个版本各测两轮独立样本（`base`/`base2`、`opt`/`opt2`）。
- 基线 DLL 由 `_attic/src.bak-perf-20261103170738/` 原样重建（469,504 B，SHA256 `fbb88d83…`）。
- 优化 DLL 为 `out/plug_xcmcp.dll`（SHA256 `6e8d87a8…`）。
- 每个样本都是「关 IDE → 换 DLL → 重启 IDE → 等 `/health` 就绪 → 预热 → 测量」。

## 2. 结果

### 2.1 `tools/list` 体积（AI token 成本）

| 指标 | 基线 | 优化后 | 变化 |
|---|---|---|---|
| 响应体积 | 6017 B | **4431 B** | **-26.4%（73.6%）** |
| 工具数量 | 29 | 29 | 不变 |

### 2.2 净服务端成本（配平后中位数，ms）

| 指标 | 基线 base / base2 | 优化 opt / opt2 | 变化 |
|---|---|---|---|
| `tools/list` | 0.345 / 0.311 | **0.001 / 0.001** | **≈ -100%（降到地板）** |
| `xc_status` | 0.247 / 0.233 | **0.011 / 0.012** | **≈ -95%** |
| `xc_get_project` | 0.005 / 0.007 | 0.008 / 0.004 | 不变（本来就贴地板） |
| `xc_module_list` | 1.426 / 1.398 | 1.381 / 1.394 | 不变（噪声内） |
| `xc_read_file` | 0.017 / 0.014 | 0.011 / 0.008 | 不变（贴地板） |
| `xc_get_output` | 0.008 / 0.007 | 0.007 / 0.005 | 不变（贴地板） |
- 优化 DLL 为 `out/plug_xcmcp.dll`（最终 SHA256 `10b72186…`）。
### 2.3 端到端原始中位数（ms，含 HTTP 地板）

同一轮次内（`base` vs `opt`，N=200）：

| 指标 | 基线 | 优化后 | 变化 |
|---|---|---|---|
| `tools/list` | 0.690 | 0.323 | **-53.2%** |
| `xc_status` | 0.580 | 0.336 | -42.1% |
| `xc_get_project` | 0.316 | 0.307 | -2.8% |
| `xc_module_list` | 1.856 | 1.829 | -1.5% |
| `xc_read_file` | 0.351 | 0.310 | -11.7% |
| `xc_get_output` | 0.337 | 0.305 | -9.5% |

解读：

- `tools/list` 的**服务端计算已被完全消除**（净成本 0.345→0.001 ms），剩余 0.32 ms
  全部是 HTTP 地板，任何工具都无法再往下压。
- `xc_status` 的改善来自同一处（不再为计数重建整份 `tools/list`）。
- `xc_get_project` / `xc_read_file` / `xc_get_output` 在基线上就已经贴地板
  （净成本 ≤ 0.02 ms），所以净成本"不变"是正确结论；它们的原始中位数差异（-2.8% /
  -11.7% / -9.5%）是地板漂移，不是真实收益。
- `xc_module_list` 的耗时来自真实的全盘模块扫描（净 1.4 ms），本轮**没有**给它加缓存。

最终二进制（`10b72186…`，已部署）上的复核结果与上表一致：
`tools/list` raw 0.323 / net -0.001，`xc_status` raw 0.344 / net 0.014，
`xc_module_list` raw 1.828 / net 1.399，`xc_read_file` raw 0.370 / net 0.011，
`xc_get_output` raw 0.349 / net 0.005，`tools/list` 体积 4431 B、同 id 两次逐字节相同。

## 3. 优化点清单

### 3.1 `tools/list` 预编码快照（主要收益）

`src/tools.cpp`：`list()` 只构建一次，序列化结果存入 `g_listCache`（`SRWLOCK` 保护），
`mcp.cpp` 的 `tools/list` 改走 `tools::listCached()`。原来每个请求都要：

1. 重新 `js::parse()` 29 段 JSON schema 文本；
2. 把 `js::Value` 数组逐层深拷贝进 envelope（`std::map` 深拷贝）；
3. 再整体序列化一遍。

现在只有首次请求付这个成本，之后直接内联已编码的 JSON 文本。
`xc_status` 的 `toolCount` 也从「为计数重建整份 list」改成读缓存计数；
`toolCount()` 在缓存尚未建立时会主动建一次，保证 `tools/list` 之前调用 `xc_status`
也返回 29（这一处曾在本轮出现过 0 的缺陷，已修）。

### 3.2 精简 `tools/list` 描述（省 AI token）

原来 29 个工具每条都带一段中文说明，共 1494 B；绝大多数与参数名/枚举完全重复
（例如 `xc_get_output` 的「读取 IDE 底部"调试输出"窗格内容」对参数
`tailLines`/`maxChars`/`clear` 没有任何增量信息）。

现在 `description` 只保留 schema 表达不了的约束，其余省略（空串即不输出该字段）：

| 保留描述的工具 | 描述 |
|---|---|
| `xc_server` | `start\|stop\|status` |
| `xc_menu_cmd` | `compile\|recompile\|compile_all` |
| `xc_read_file` | `读文本;限工程目录` |
| `xc_editor_set` | `写盘;留BOM/CRLF` |
| `xc_add_dir_files` | `按扩展名批量加` |
| `xc_tree_probe` | `dump\|select\|clean` |
| `xc_module_search` | `dependsOn反查依赖` |
| `xc_module_create` | `生成module_xxx.h/.cpp` |
| `xc_probe_cmd` | `需allow_unsafe+confirm` |
| `xc_get_output` | `调试输出窗格` |
| `xc_ide_log` | `写一行到 IDE 面板` |
| `xc_new_file` | `按type建模板` |

其余 17 个工具不带 `description` 字段。**参数语义零丢失**：`properties` 键集合、
`required`、每个属性的 `type` 与 `enum` 与基线逐一相同（见第 4 节校验）。

### 3.3 `js::Value` 零拷贝

- 新增 `set(key, std::string&&)` / `set(key, Value&&)` 右值重载：把整段文件/窗格内容
  写进结果时不再深拷贝一次。
- 新增 `js::rawJson()` + `RAW` 类型：预编码 JSON 文本原样内联，无需重新解析成树。

### 3.4 HTTP 每请求开销

`src/http.cpp`：原 `headerVal()` 每查一个头都要把**整段请求头**复制并小写化一次
（一次请求查 5 个头 = 5 次全头复制）。改为 `Headers::parse()` 单次解析成
`name → value` 表，之后查表。`Host`/`Origin`/token 的判定逻辑逐字未变。

### 3.5 `xc_get_output` 截断方式

`tailLines` 的尾部行截取改为**从尾部反向扫描**，不再先分配整份"行起点数组"
（原来对大输出要建一个 O(行数) 的 `vector<size_t>`）；`maxChars` 截断改为原地
`erase`（原来 `substr` 会再复制一份）。返回的 `text` / `length` / `rows` 语义与基线一致。

> 试过并**放弃**的做法：用 `XEdit_GetRowCount` + `XEdit_GetTextRow` 直接只取末 N 行。
> 实测该控件行数不随 `XEdit_AddText` 刷新（`length` 从 2731 涨到 7381 时 `rows` 恒为 37），
> 按行取会改变 `tailLines` 的返回内容，属于改契约，故回退。

### 3.6 编译警告

`src/json.cpp` 的 `-2147483648` 被 MSVC 当作无符号负号运算（C4146），改为
`-2147483647.0`（钳位边界语义等价）。基线 1 条警告 → 优化后 0 条。

## 4. 语义等价校验

脚本逐项比对基线/优化两份 `tools/list` 与 `initialize` 响应：

- 工具名集合一致（29 vs 29）、工具数量一致；
- 29 个工具逐个比对 `inputSchema.type`、`properties` 键集合、`required` 数组、
  每个属性的 `type` 与 `enum`；
- `protocolVersion`、`serverInfo.name/version`、`capabilities` 一致。

结果：**0 项失败**。连续两次同 id 的 `tools/list` 响应**逐字节相同**
（4431 B，SHA256 一致），证明缓存没有改变输出。

## 5. 刻意没做的事（第一轮的范围）

> 第二轮（opt2）已经补上了下面第 1、3 两条，见第 7 节；第 2 条保持不变。

- **不给 `xc_module_list` / `xc_module_search` / `xc_list_functions` 加跨调用磁盘扫描缓存**：
  结果依赖磁盘与工程状态，缓存会带来陈旧数据风险。
- **不拆 `tools.cpp`**：本轮目标是效率，不掺结构性重构。
- **不压低 HTTP 地板**（约 0.32 ms/请求）：那需要连接复用，改动面与风险都超出本轮范围。
  也正因为如此，`xc_get_project` / `xc_read_file` / `xc_get_output` 的净成本已无优化空间。

## 6. 复现方法

1. 基线：用 `_attic/src.bak-perf-<时间戳>/` 的源码重建 DLL（改 `build.bat` 里的
   `IDEROOT` 指向 `I:\xcide` 即可）。
2. 部署某个 DLL 到 `I:\xcide\data\plug\plug_xcmcp.dll`，重启炫彩IDE。
3. 就绪判定：1.1.0 及更早版本 `/health` 与 `/mcp` 需要 `X-XC-Token`
   （`%LOCALAPPDATA%\xc-mcp\token`）；**1.2.0 起 token 认证已取消**，无需 token 头。
   兼容工具仍可能读取并发送旧 token，仅用于旧端点；新服务会忽略。
4. 测量：对每个工具「ping → 工具 → ping」，用 `工具 − (ping1+ping2)/2` 作为净成本。

## 7. 第二轮（opt2）：keep-alive、模块扫描缓存、健壮性

第一轮的「不做清单」里，连接复用与模块扫描缓存都在第二轮做了；本轮同时修了
HTTP 解析/异常安全、BOM 上报、token 工具链等健壮性问题。对外契约不变
（详见 `doc/abi.md` 第 19 节）。

### 7.1 测量方法

与第一轮相同的配平法：每次调用前后各 ping 一次，取均值作 HTTP 地板。
`test/bench_opt2.py <label> 60`：普通指标 N=60，模块类 N=20，
keep-alive 指标为同一连接 200 次的连续请求。基线 DLL 为 `_attic/baseline-opt2-*.dll`（460.5K），
新版为 `out/plug_xcmcp.dll`。原始数据：`doc/perf-opt2-base.json` / `doc/perf-opt2-new.json`。

### 7.2 结果

| 指标 | 基线 net (ms) | 新版 net (ms) | 变化 |
|---|---|---|---|
| `tools/list` | 0.043 | 0.047 | 噪声内（均已贴地板） |
| `xc_status` | 0.022 | 0.015 | 噪声内 |
| `xc_get_project` | 0.021 | -0.001 | 贴地板 |
| `xc_read_file` | 0.338 | 0.321 | 噪声内 |
| `xc_get_output` | 0.099 | 0.085 | 噪声内 |
| `xc_module_list` | **51.892** | **15.929** | **-69.3%（3.26×）** |
| `xc_module_search` | **49.094** | **14.693** | **-70.1%（3.34×）** |

同连接连续请求（200 次中位数，含地板）：

| 指标 | 基线 | 新版 | 变化 |
|---|---|---|---|
| 同连接 `ping` | 0.440 ms | **0.134 ms** | **-69.6%** |
| 同连接 `tools/list` | 0.507 ms（p90 11.27） | **0.219 ms（p90 0.375）** | **-56.8%** |

解读：

- 基线的 `connProc` 处理完一个请求就关闭连接，客户端每次都要重新 `connect`，
  因此同连接指标在基线上实际是「每次新建连接」；新版的 0.13 ms 才是真正的进程内往返。
  p90 从 11.27 ms 降到 0.375 ms，说明基线偶发的连接建立/线程创建抖动也被消除。
- 模块工具从 ~50 ms 降到 ~15 ms：目录清单缓存消掉了 1.7 万条目 × 1058 个目录的
  `FindFirstFile` 全量枚举；头文件缓存消掉了重复读取与解析。
  剩余 ~16 ms 是 1058 次目录 stat + 294 次文件 stat（本机 I: 盘带杀软过滤，
  单次元数据查询约 10–20 µs），这是「新增/删除/修改立即可见」前提下的下限。
- 其余工具本来就在 HTTP 地板附近，两版差异属于噪声。

### 7.3 复现

```bat
python test\bench_opt2.py base 60   :: 换回基线 DLL 并重启 IDE 后
python test\bench_opt2.py new 60    :: 换回新版 DLL 并重启 IDE 后
```

