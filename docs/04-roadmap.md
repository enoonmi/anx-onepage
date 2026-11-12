# 开发计划（ROADMAP）

> 状态标记：`[x] 本机已验证` / `[~] 已编写未编译` / `[ ] 未开始`
> 原则：**绝不把「写完」当成「能用」**。每个交付物都要标注它是被编译跑过，还是只写出来了。

## 验证环境的现实

本机（Windows，`D:\code\anx-onepage`）现状：

| 能力 | 状态 | 影响 |
|---|---|---|
| MSVC 2022 BuildTools（cl 14.44 / 14.29） | ✅ 已装 | **可以真正编译 + 跑单测** |
| PlatformIO | ❌ 装不上（pip 权限/网络、venv 的 ensurepip 失败） | 固件整体交叉编译跑不了 |
| ESP32-C61 真机 | ❌ 无 | 不能在设备上验证渲染/功耗/WiFi/TLS |
| 固件 `simulator/` | ✅ 存在，`shims/esp_http_client.h` 是 libcurl 后端 | 需 CMake+SDL2，本机未通 |
| Python 3.13 | ✅ | `tools/mock_webdav.py` 已用它跑通真实 HTTP |
| Node.js v22.19.0 | ✅ | 可用于脚本/校验 |

**结论**：本项目可以在本机做到「纯逻辑层真编译 + 真单测 + 真实 HTTP 对拍」与
「IO 层真编译 + 真链接」，但**不能**声称固件在真机上工作。所有设备相关结论一律标 `[~]`。

## 分层与可测性设计

按**可移植性**分两个库目录：

```
lib/AnxSync/                      ← 纯 C++（仅 std），本机可编译单测 ✅ 已完成
├── AnxHttpDate.{h,cpp}           RFC 1123 时间解析/格式化        ✅ 201 断言覆盖
├── AnxPropfindParser.{h,cpp}     WebDAV multistatus 解析         ✅
├── AnxPathMap.{h,cpp}            href→SD 路径 + 穿越防护         ✅
├── AnxSyncPlan.{h,cpp}           差量决策（纯函数）              ✅
└── AnxProgressJson.{h,cpp}       progress.json 读写/合并         ✅

lib/AnxSyncIo/                    ← 依赖 Arduino/ESP-IDF，桩下编译验证 ✅ 已完成
├── AnxCredentialStore.{h,cpp}    凭据存储                        ✅ 编译+冒烟
└── AnxWebDavClient.{h,cpp}       PROPFIND/GET/PUT                ✅ 编译

待做（依赖 activity 框架，本机无法验证）
└── UI：设置页 + 同步 Activity    [ ]
```

两个命令分别跑两层：

```powershell
# 纯逻辑：201 断言
powershell -ExecutionPolicy Bypass -File tests\host\run_tests.ps1

# IO 层：编译 + 链接 + 配置冒烟
python tests\host\check_io_layer.py
```

## 里程碑

### M0 规划与地基 `[x]`
- [x] 协议摸清（见 [01-anx-webdav-protocol.md](01-anx-webdav-protocol.md)）
- [x] 架构定稿（见 [02-architecture.md](02-architecture.md)）
- [x] 三仓库就位
- [x] 本文件

### M1 纯逻辑核心 `[x] 全部本机真编译验证`
21 个文件、约 3000 行，在 `enoonmi/crosspoint-onepage@main`：

- [x] `AnxHttpDate`：RFC 1123 → Unix 时间（GMT/UTC/`+HHMM`/`+HH:MM`/RFC 850/无星期；闰年；拒绝畸形输入）
- [x] `AnxPropfindParser`：`207 Multi-Status`（目录条目经 `isCollection` 暴露；逐段 percent-decode；`%2F` 保持编码；前缀 `d:`/`D:`/无 都吃；根元素必须是 multistatus）
- [x] `AnxPathMap`：`anx/data/file/<hash>.epub` → `/Books/<hash>.epub`（拒绝 `..`、分隔符、前导点、非 EPUB；临时文件 `.part`；`stripBaseUrl`；`isInsideRoot`）
- [x] `AnxSyncPlan`：远端×本地 → Download/Replace/Skip/Ignore（size+mtime；**本地独有只上报不删**）
- [x] `AnxProgressJson`：schema v1（百分比钳制；未知字段忽略；`upsert` 拒绝陈旧写入）
- [x] 单元测试 201 条 + `run_tests.ps1` / `run_tests.cmd`
- [x] `tools/mock_webdav.py`：纯标准库 WebDAV，带 Basic Auth
- [x] `anx_e2e.cpp`：真实 HTTP 响应 → 解析 → 决策，输出 `E2E OK`

**M1 期间被测试揪出的真 bug**（直接烧进设备就会出问题）：

| 问题 | 后果 |
|---|---|
| `parseUInt` 达到位数上限时 return false | 所有 `+0100` 时区解析失败，时间全错 |
| 星期计算偏移量算错 | `formatRfc1123` 输出 `Thu` 而非 `Sun` |
| 日号后分隔符判断写反 | `03:49:37 -0500` 这类响应解析失败 |
| `isInsideRoot` 未拒绝相对路径与根目录 | 穿越防护不完整 |

### M2 网络层接入 `[x] 已编译验证，未上机`

代码在 `lib/AnxSyncIo/`：

- [x] `AnxWebDavClient`：`listBooks()`（PROPFIND）、`downloadBook()`（流式 GET）、
      `putProgress()`（PUT），沿用 `KOReaderSyncClient` 的 client/auth/event 模式
      与 `HttpDownloader` 的手动 open/read 循环
- [x] `AnxCredentialStore`：URL/账号/密码/开关，持久化到 `/.crosspoint/anx.json`
- [x] 下载落 `/Books/.<name>.part`，**字节数匹配 Content-Length 后才改名**
- [x] 遗留 `.part` 清理、取消标志、TLS 前的最小堆检查

**为什么拆成两个库目录**：PlatformIO 会编译库目录下所有 `.cpp`。把 IO 代码放进
`lib/AnxSync/` 会让 host 测试立刻编不过（缺 Arduino 头）。实测踩过的坑，不是洁癖。

**验证方式与边界**：`tests/host/check_io_layer.py` 用 `tests/host/stubs/` 的桩
编译、链接并跑冒烟测试：

```
COMPILE_EXIT=0  LINK_EXIT=0  IO LAYER SMOKE OK  RUN_EXIT=0
```

编译检查抓出的真错误：

| 问题 | 说明 |
|---|---|
| `PathMap` / `RemoteFile` 缺 `anx::` 限定 | 全集编译错误 |
| `makeUniqueNoThrow<char[]>` 模板不匹配 | 改用 `std::unique_ptr<char[]>` |
| `/Fo:"<dir>\"` 尾部反斜杠转义引号 | MSVC 报 `D8003`，本地被旧 `.obj` 掩盖 |
| 源文件里的破折号被编码破坏 | 非 ASCII 混入，已修 |

⚠️ **这不等于同步能用**：`esp_http_client` 与 `HalStorage` 的桩是**故意做成失败**的，
host 上任何一次运行都不可能伪装成「同步成功」。真机验证仍是空白。

### M3 进度：手机 → 设备 `[~]`
- [ ] 进度源读取（先支持安读侧导出的精简 JSON，绕开 SQLite）
- [ ] 接 `ProgressMapper::toCrossPoint()`
- [ ] 落到 `.crosspoint/epub_<hash>/progress.bin`

### M4 进度：设备 → 手机 `[~]`
- [ ] `ProgressMapper::toSavedProgress()` → `AnxProgressJson`
- [ ] `PUT anx/onepage/progress.json`
- [ ] anx-reader 侧 merge patch（只改 `reading_percentage`，保留 CFI）

### M5 UI 与设置 `[~]`
- [ ] 设置页：WebDAV URL / 账号 / 密码 / 仅 WiFi 同步 / 同步开关
- [ ] 同步入口 Activity + 进度显示
- [ ] i18n（改 `lib/I18n/translations/*.yaml`，跑 `gen_i18n.py`）

## 每步的验收标准

| 里程碑 | 本机能验的 | 只能靠真机的 |
|---|---|---|
| M1 ✅ | 编译 + 201 断言全绿 + 真实 HTTP 对拍 | — |
| M2 ✅ | 编译 + 链接 + 配置冒烟（桩故意失败） | 真实 Nextcloud、TLS 握手、SD 写入 |
| M3 | 纯逻辑（进度映射的输入输出） | 翻页后进度是否真的跳对页 |
| M4 | JSON 往返 + 与 anx-reader 的字段约定 | 手机端是否真的显示同步进度 |
| M5 | 代码规范/编译 | 按键、排版、功耗 |

## 当前的阻塞点

1. **PlatformIO 装不上** —— `pip install platformio` 因权限/网络失败；`python -m venv`
   因 `ensurepip` 在沙箱下起不来而失败。固件整体交叉编译（`pio run -e onepage`）
   仍然跑不了，**`lib/AnxSyncIo/` 从未被 PlatformIO 编译过**。这是现在最该解决的。
2. **本机无真机** —— 渲染、功耗、WiFi 稳定性、TLS 握手无法验证。
3. **git push 受限** —— `credential.helper` 依赖的 MSYS `sh.exe` 在沙箱下无法创建
   命名管道（`couldn't create signal pipe, Win32 error 5`），任何 helper 都起不来。
   当前全部用 GitHub MCP 推送，本地 `reset --hard origin/main` 对齐。

## 明确不做

- 不设备上传 `database7.db`（会毁手机端数据，理由见 [02-architecture.md](02-architecture.md)）
- 不在 ESP32 上跑 SQLite 解析（除非 M3 证明必需）
- 不碰 PDF/MOBI/AZW3 原生解析（安读已统一转 EPUB）
- 不在无真机验证的情况下宣称任何设备侧行为可用
