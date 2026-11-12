# 开发计划（ROADMAP）

> 状态标记：`[x] 本机已验证` / `[~] 已编写未编译` / `[ ] 未开始`
> 原则：**绝不把「写完」当成「能用」**。

## 验证环境

| 能力 | 状态 | 影响 |
|---|---|---|
| MSVC 2022 BuildTools | ✅ | 可真正编译 + 跑单测 |
| PlatformIO | ❌ 装不上（见「阻塞点」） | 固件交叉编译跑不了 |
| Dart / Flutter SDK | ❌ 无 | anx-reader 补丁无法 analyze |
| ESP32-C61 真机 | ❌ | 渲染/功耗/WiFi/TLS 无法验证 |
| Python 3.13 | ✅ | mock WebDAV + 三个检查脚本 |

## 两个库目录

```
lib/AnxSync/          纯 C++，本机可编译单测
  AnxHttpDate           RFC 1123 时间
  AnxPropfindParser     WebDAV multistatus
  AnxPathMap            href -> SD 路径 + 穿越防护
  AnxSyncPlan           差量决策
  AnxProgressJson       progress.json 读写
  AnxProgressLocator    百分比 <-> (spine, 段内偏移)
  AnxProgressBridge     设备位置 <-> 百分比、冲突仲裁、上传文档

lib/AnxSyncIo/        依赖 Arduino/ESP-IDF，桩下编译验证
  AnxWebDavClient       PROPFIND / 流式 GET / PUT
  AnxCredentialStore    URL + 凭据 + 开关
  AnxProgressStore      进度 journal，读 reader 的 progress.bin
```

四个验证命令：

```powershell
powershell -ExecutionPolicy Bypass -File tests\host\run_tests.ps1   # 317 断言
python tests\host\check_io_layer.py                                  # 编译+链接+冒烟
python tests\host\check_ui_refs.py                                   # UI 引用检查
python ..\tools\check_anx_patch.py                                   # anx-reader 引用检查
```

## 里程碑

### M0 规划与地基 `[x]`

### M1 纯逻辑核心 `[x] 本机真编译验证`

- [x] `AnxHttpDate` / `AnxPropfindParser` / `AnxPathMap` / `AnxSyncPlan` / `AnxProgressJson`
- [x] 201 条断言 + mock WebDAV 真实 HTTP 对拍（`E2E OK`）

**被测试揪出的真 bug**：`parseUInt` 位数上限、星期偏移、日号分隔符、`isInsideRoot` 相对路径。

### M2 网络层 `[x] 已编译验证，未上机`

- [x] `AnxWebDavClient`：`listBooks` / `downloadBook` / `putProgress`
- [x] `AnxCredentialStore`
- [x] 下载落 `.part`，字节数匹配 Content-Length 后才改名
- [x] 编译检查抓出：`PathMap`/`RemoteFile` 缺 `anx::`、`makeUniqueNoThrow<char[]>` 不匹配、
      `/Fo:"<dir>\"` 转义 bug（本地被旧 `.obj` 掩盖）、非 ASCII 混入

### M3+M4 进度双向 `[x] 已编译验证，未上机`

- [x] `AnxProgressLocator`：百分比 ↔ (spine 索引, 段内偏移)
- [x] `AnxProgressBridge`：设备位置 ↔ 百分比、`/Books/x.epub` → `file/x.epub`、
      冲突仲裁、上传文档构建
- [x] `AnxProgressStore`：`/.crosspoint/anx_progress.bin` journal，
      读 reader 的 `progress.bin`（复用 `EpubReaderUtils` 的 6 字节格式）

**关键决策**（都有测试锁定）：

| 决策 | 理由 |
|---|---|
| 只传 `reading_percentage` | `last_read_position` 是 foliate-js 的 CFI，设备生成不了；写进去会毁掉手机的精确位置 |
| 边界百分比归到下一章第一页 | 章末与下章首是同一个点，单 float 无法区分；归到下一章让打开就落在对的章节 |
| 时间戳相同则手机赢 | 设备时钟可能陈旧，不能把手机拖回去 |
| 相差 <0.1% 视为相同 | 防止两台设备因舍入差异来回抖动 |
| 不设备造 phone 没有的条目 | 那可能是旁加载或手机已删，猜错都是错的 |
| journal 只在位置真变了才更新时间戳 | 否则每次唤醒都把整库标记为已改，上传洪泛 |

**被测试揪出的真 bug**：边界归属错导致「章末页 ↔ 下章首页」丢失，round-trip 测试
（遍历三章每一页）发现后修正语义并锁定。

### M5 UI 与设置 `[x] 已编写 + 引用检查`

- [x] 设置页：WebDAV URL（含规范化）/ 账号 / 密码 / 仅 WiFi / 唤醒同步 / Sync now
- [x] 同步 Activity：从 `loop()` 推进的状态机（**不是** FreeRTOS 任务 —— Activity 退出时被 delete）
- [x] `SettingAction::AnxSync` + 菜单行 + dispatch
- [x] 13 个 `STR_ANX_SYNC_*` i18n 键，`gen_i18n.py` 28 语言 / 427 键
- [x] `tests/host/check_ui_refs.py` 四项检查（注入三个错误验证过）

## 验收边界

| 里程碑 | 本机验证 | 只能靠真机/完整工具链 |
|---|---|---|
| M1 ✅ | 317 断言 + 真实 HTTP 对拍 | — |
| M2 ✅ | 编译/链接/配置冒烟 | TLS 握手、SD 写入、真实 Nextcloud |
| M3/M4 ✅ | 编译/链接 + 纯逻辑全覆盖 | 翻页后进度真的跳对页 |
| M5 ✅ | 引用检查（非编译） | 按键、排版、功耗 |
| anx-reader ✅ | 引用检查（非 analyze） | 真实合并行为 |

⚠️ `esp_http_client` 与 `HalStorage` 的桩**故意失败**，host 上不可能伪装成「同步成功」。

## anx-reader 侧进度合并 `[x] 已编写 + 引用检查，未 dart analyze`

两个 commit 在 [enoonmi/anx-reader@develop](https://github.com/enoonmi/anx-reader/tree/develop)：

- [x] `3af1d38a` 新增 `lib/service/sync/onepage_progress_merge.dart`（217 行）
- [x] `e2740d20` `sync.dart` 接线（+17 行，0 删除）

- [x] 读 `anx/onepage/progress.json`，只合并 `reading_percentage`
- [x] `lastReadPosition`（CFI）**从不赋值** —— 由检查脚本强制
- [x] 时间戳守卫：只有严格更新才应用；差异 <0.1% 跳过，防来回抖动
- [x] 单查询建索引，不是每本书一次 select
- [x] `tools/check_anx_patch.py` 八项引用检查（脚本本身注入三个错误验证过）
- [x] **两端契约人工核对一致**：路径 / schema / `file_path` / `reading_percentage` / `updated_at`

⚠️ 本机**没有 Dart SDK**（无 `dart`、无 `flutter`、无 `.dart_tool`），
所以**不是 `dart analyze`**。类型、null safety、参数个数全部未验证。

详见 [06-anx-reader-patch.md](06-anx-reader-patch.md)。

## 阻塞点

1. **PlatformIO 装不上** —— `pip install` 因沙箱不允许删除 pip 自己的临时目录而
   `[Errno 13] Permission denied`；`python -m venv` 的 `ensurepip` 同样失败。
   `--target` 和自定义 `TMP` 都试过。固件整体编译未跑过，
   `lib/AnxSyncIo/` 与两个 UI 文件从未被 PlatformIO 编译。
2. **没有 Dart SDK** —— anx-reader 补丁无法 `dart analyze` / `flutter build`。
3. **无真机** —— 无法验证任何设备行为（渲染、按键、功耗、TLS、SD 写入）。
4. **git push 受限** —— MSYS `sh.exe` 在沙箱下建不了命名管道，credential helper
   起不来。全部走 GitHub MCP，本地 `reset --hard origin/<branch>` 对齐。

## 明确不做

- 设备上传 `database7.db`（会毁手机端笔记/分组/统计，且 `syncFiles()` 会删远端书）
- ESP32 上跑 SQLite（除非证明必需）
- PDF/MOBI/AZW3 原生解析（安读已统一转 EPUB）
