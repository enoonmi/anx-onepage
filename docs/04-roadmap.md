# 开发计划（ROADMAP）

> 状态标记：`[x] 本机已验证` / `[~] 已编写未编译` / `[ ] 未开始`
> 原则：**绝不把「写完」当成「能用」**。

## 验证环境

| 能力 | 状态 | 影响 |
|---|---|---|
| MSVC 2022 BuildTools | ✅ | 可真正编译 + 跑单测 |
| PlatformIO | ❌ 装不上（pip 权限/网络、venv ensurepip 失败） | 固件交叉编译跑不了 |
| ESP32-C61 真机 | ❌ | 渲染/功耗/WiFi/TLS 无法验证 |
| Python 3.13 | ✅ | mock WebDAV + 构建脚本 |

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

两个命令：

```powershell
powershell -ExecutionPolicy Bypass -File tests\host\run_tests.ps1   # 317 断言
python tests\host\check_io_layer.py                                  # 编译+链接+冒烟
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
      读 reader 的 `progress.bin`（复用 `EpubReaderUtils` 的 6 字节格式），
      journal 用 `ProgressFile` 式 crash-safe 写法

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

**编译检查揪出的**：`ProgressDocument` 缺 include、`buildUpload` 缺 `anx::`、
桩缺 `std::string` 重载与 `flush()`、`<cmath>` 缺失。

### M5 UI 与设置 `[ ]`
- [ ] 设置页：WebDAV URL / 账号 / 密码 / 仅 WiFi 同步 / 同步开关
- [ ] 同步入口 Activity + 进度显示
- [ ] i18n

## 验收边界

| 里程碑 | 本机验证 | 只能靠真机 |
|---|---|---|
| M1 ✅ | 317 断言 + 真实 HTTP 对拍 | — |
| M2 ✅ | 编译/链接/配置冒烟 | TLS 握手、SD 写入、真实 Nextcloud |
| M3/M4 ✅ | 编译/链接 + 纯逻辑全覆盖 | 翻页后进度真的跳对页 |
| M5 | 编译 | 按键、排版、功耗 |

⚠️ `esp_http_client` 与 `HalStorage` 的桩**故意失败**，host 上不可能伪装成「同步成功」。

## 阻塞点

1. **PlatformIO 装不上** —— 固件整体编译未跑过，`lib/AnxSyncIo/` 从未被 PlatformIO 编译。
2. **无真机** —— 无法验证任何设备行为。
3. **git push 受限** —— MSYS `sh.exe` 在沙箱下建不了命名管道，credential helper 起不来。
   全部走 GitHub MCP，本地 `reset --hard origin/main` 对齐。

## 明确不做

- 设备上传 `database7.db`（会毁手机端笔记/分组/统计，且 `syncFiles()` 会删远端书）
- ESP32 上跑 SQLite（除非证明必需）
- PDF/MOBI/AZW3 原生解析（安读已统一转 EPUB）
