# M5 收尾记录与本轮事故复盘

## M5 完成情况

**设置入口** `AnxSyncSettingsActivity`（`src/activities/settings/`）

| 行 | 内容 |
|---|---|
| WebDAV URL | `setBaseUrl()` 规范化：去空白、去尾部斜杠、去误粘的 `/anx` |
| Username / Password | 键盘输入，逐项即存 |
| Sync on Wi-Fi only | 开关 |
| Sync when waking | 开关 |
| Sync now | 未配置时报明确错误，而不是让用户等一次失败的 TLS 握手 |

**同步执行** `AnxSyncActivity`

一个从 `loop()` 推进的状态机，不是 FreeRTOS 任务。理由写进了头文件：
这个固件的 Activity 在退出时被 `delete`，而现有 Activity 没有任何一个跑会
活过一屏的任务；用状态机就没有任务句柄泄漏、也不会有任务写进已释放对象的问题。

阶段：`NeedWifi → Listing → Downloading（每轮一本）→ UploadingProgress → Done/Failed`

- 复用 `WifiSelectionActivity`（和 `CalibreConnectActivity` 同一套写法）
- 单本书失败不中止整轮，失败数在结束时显示
- Back 随时可退出；已下载的书保留，下轮从剩余部分继续

**接入**：`SettingAction::AnxSync` + `systemSettings` 菜单行 + dispatch +
13 个 `STR_ANX_SYNC_*` 键。

## 验证

| 项 | 结果 |
|---|---|
| 纯逻辑测试 | `317 checks, 0 failures` |
| IO 层编译链接 | `COMPILE_EXIT=0 LINK_EXIT=0 IO LAYER SMOKE OK RUN_EXIT=0` |
| UI 引用检查 | `UI REFERENCE CHECK OK` |
| i18n 生成 | 28 语言 / 427 键 / exit 0 |

`tests/host/check_ui_refs.py` 检查四件事：include 能解析、每个 `tr(STR_*)` 都存在、
每个 `GUI/renderer/mappedInput/Storage` 方法都在头文件里、每个 `.cpp` 定义都在 `.h` 声明。
**它本身被验证过**：注入三个错误（假 StrId、假方法、假 include）全部被抓到。

⚠️ **这不是编译**。`GfxRenderer` 会拉进 `EpdFont`，后者用 GCC 的
`__attribute__((packed))`，MSVC 不支持，所以 UI 层在这台机器上无法编译。
签名与类型错误仍然未经验证。

## 本轮事故复盘：两次把文件写坏

这是本轮最严重的问题，两次都是我的操作导致的，必须记录。

### 事故 1：english.yaml 被截断

用 `push_files` 覆盖 `lib/I18n/translations/english.yaml` 时，只提供了新增的 13 行，
结果整份 430 行的翻译表被替换成 25 行，丢掉约 406 条字符串。

**怎么发现的**：推送后没有直接继续，而是 `git fetch` 后比对行数——
`remote: 25 / local: 430`。

**怎么恢复的**：本地工作区还是完整的，用 `create_or_update_file` 把 431 行全文写回，
再 `git diff origin/main` 确认零差异、`gen_i18n.py` 跑通。

### 事故 2：SettingsActivity.cpp 被截断

同样的问题：`create_or_update_file` 只传了前 120 行，文件从 429 行变成 103 行。
而且这次还混入了 UTF-8 BOM（`ef bb bf`），会破坏编译。

**怎么发现的**：`git show origin/main:... | Measure-Object -Line` → `103 vs 429`。

**怎么恢复的**：从 `184283d`（最后一次完整且含 AnxSync 的版本）取字节，
剥 BOM、归一化行尾、断言 6 个关键符号都在，再完整写回。

### 根因与对策

根因不是工具，是我在推送大文件时没有「先读全文、再整体写」，
而是凭记忆分段拼内容。

**以后必须**：
1. 推送已有文件前，先 `read` 完整内容（或从 `git show` 取），不在脑子里拼。
2. 推送后**立刻**比对行数/字节数与远端，而不是相信返回值。
3. 检测 BOM：`read_bytes()[:3] == b'\xef\xbb\xbf'`。
4. 偏好用 `git` 生成 diff 确认改动最小（`5 insertions(+), 0 deletions(-)`）。

这三个检查在本轮后半段救回了两次事故。

## 仍然阻塞

1. **PlatformIO 装不上**（pip 权限/网络失败、venv 的 ensurepip 在沙箱下起不来）。
   `lib/AnxSyncIo/` 与两个 UI 文件**从未被 PlatformIO 编译过**。
2. **无真机** —— 渲染、按键、功耗、TLS、SD 写入全部未验证。
3. **git push 认证不通** —— MSYS `sh.exe` 在沙箱下建不了命名管道，
   credential helper 起不来。全部走 GitHub MCP。
