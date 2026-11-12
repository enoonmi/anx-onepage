# 开发计划（ROADMAP）

> 状态标记：`[x] 本机已验证` / `[~] 已编写未编译` / `[ ] 未开始`
> 原则：**绝不把「写完」当成「能用」**。每个交付物都要标注它是被编译跑过，还是只写出来了。

## 验证环境的现实

本机（Windows，`D:\code\anx-onepage`）现状：

| 能力 | 状态 | 影响 |
|---|---|---|
| MSVC 2022 BuildTools（cl 14.44 / 14.29） | ✅ 已装 | **可以把纯逻辑模块真正编译 + 跑单测** |
| PlatformIO | `~/.platformio/penv` 存在但无 `pio.exe` | 固件整体交叉编译暂不可行 |
| ESP32-C61 真机 | ❌ 无 | 不能在设备上验证渲染/功耗/WiFi |
| 固件 `simulator/` | ✅ 存在，`shims/esp_http_client.h` 是 **libcurl 真实后端** | 网络层理论上可在桌面真跑（需 CMake+SDL2，本机未通） |
| Python 3.13 | ✅ | `tools/mock_webdav.py` 已用它跑通真实 HTTP |
| Node.js v22.19.0 | ✅ | 可用于脚本/校验 |

**结论**：本项目可以在本机做到「纯逻辑层真编译 + 真单测 + 真实 HTTP 对拍」，但**不能**声称
固件在真机上工作。所有设备相关结论一律标 `[~]`。

## 分层与可测性设计

为了让新增代码在没有 PlatformIO / 真机时也能验证，按**可移植性**分层：

```
lib/AnxSync/                      ← 纯 C++（仅 std），本机可编译单测 ✅ 已完成
├── AnxHttpDate.{h,cpp}           RFC 1123 时间解析/格式化        ✅ 240 行，已验证
├── AnxPropfindParser.{h,cpp}     WebDAV multistatus 解析         ✅ 429 行，已验证
├── AnxPathMap.{h,cpp}            href→SD 路径 + 穿越防护         ✅ 124 行，已验证
├── AnxSyncPlan.{h,cpp}           差量决策（纯函数）              ✅ 110 行，已验证
└── AnxProgressJson.{h,cpp}       progress.json 读写/合并         ✅ 420 行，已验证

待做（依赖 Arduino/ESP-IDF，本机无法验证）
├── AnxCredentialStore.{h,cpp}    凭据存储（照抄 OpdsServerStore） [ ]
├── AnxWebDavClient.{h,cpp}       PROPFIND/GET/PUT，走 HttpDownloader [ ]
└── UI：设置页 + 同步 Activity    [ ]
```

`tests/host/` 里 201 条断言在 MSVC 下真编译真运行，全部通过；`anx_e2e.cpp` 用
`tools/mock_webdav.py` 提供的**真实 HTTP 响应**跑通 `PROPFIND → 解析 → 决策` 全链路。

## 里程碑

### M0 规划与地基 `[x]`
- [x] 协议摸清（见 [01-anx-webdav-protocol.md](01-anx-webdav-protocol.md)）
- [x] 架构定稿（见 [02-architecture.md](02-architecture.md)）
- [x] 三仓库就位
- [x] 本文件

### M1 离线可测的核心逻辑 `[x] 全部本机真编译验证`
已在 `enoonmi/crosspoint-onepage@main` 提交（21 个文件、约 3000 行）：

- [x] `AnxHttpDate`：RFC 1123 → Unix 时间
  - GMT / UTC / `+HHMM` / `+HH:MM` / RFC 850 / 无星期几种形式
  - 闰年、闰秒容忍、拒绝畸形输入而不是静默返回 0
- [x] `AnxPropfindParser`：`207 Multi-Status`
  - `Depth:1` 时目录自身条目通过 `isCollection` 暴露，调用方过滤
  - `<d:href>` 逐段 percent-decode；`%2F` 默认保持编码（不误当目录分隔符）
  - 命名空间前缀 `d:` / `D:` / 无前缀都能吃；自闭合标签、XML 实体
  - 根元素必须是 `<multistatus>`，否则判为非法（区分「空目录」与「登录页」）
- [x] `AnxPathMap`：`anx/data/file/<hash>.epub` → `/Books/<hash>.epub`
  - 拒绝 `..`、路径分隔符、前导点、非 `.epub`、退化名
  - 临时文件 `.name.epub.part`（下载完成再改名，防半截文件）
  - `stripBaseUrl` 兼容返回绝对 URL 的服务器
  - `isInsideRoot` 穿越防护
- [x] `AnxSyncPlan`：远端 × 本地 → Download / Replace / Skip / Ignore
  - 以 `size + mtime` 判定；服务器不给 mtime 时退化为比 size
  - **本地独有书籍只上报不删除**（防止服务端误删后不可恢复）
- [x] `AnxProgressJson`：`anx/onepage/progress.json` schema v1
  - 百分比钳制到 `[0,1]`；未知字段忽略；接受字符串形式的数字
  - `upsert` 按时间戳拒绝陈旧写入
- [x] 单元测试 201 条 + `run_tests.ps1` / `run_tests.cmd`
- [x] `tools/mock_webdav.py`：纯标准库 WebDAV，带 Basic Auth
- [x] `anx_e2e.cpp`：真实 HTTP 响应 → 解析 → 决策，输出 `E2E OK`

**M1 期间被测试揪出的真 bug**（如果直接烧进设备就会出问题）：

| 问题 | 后果 |
|---|---|
| `parseUInt` 达到位数上限时 return false | 所有 `+0100` 时区解析失败，导致时间全错 |
| 星期计算偏移量算错 | `formatRfc1123` 输出 `Thu` 而非 `Sun` |
| 日号后分隔符判断写反 | `03:49:37 -0500` 这类带负时区的响应解析失败 |
| `isInsideRoot` 未拒绝相对路径与根目录本身 | 穿越防护不完整 |

### M2 网络层接入 `[~] 依赖 shim/设备`
- [ ] `AnxWebDavClient`：`PROPFIND` + `GET`
  - Basic Auth 走 `HttpDownloader` 已有的 username/password 形参
  - **风险点**：`esp_http_client` 需自定义 method，要确认 `HTTP_METHOD_PROPFIND`
    在真实 ESP-IDF 与 simulator 的 libcurl shim 里是否都被支持
- [ ] 流式下载到 SD（临时文件 → 校验 → rename）
- [ ] 失败重试与取消
- [ ] 前置：平台能编译（装 PlatformIO 或打通 simulator 的 CMake）

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
| M1 ✅ | 编译通过 + 201 断言全绿 + 真实 HTTP 对拍 | — |
| M2 | 用 libcurl shim 打本地 mock server（需先打通构建） | 真实路由器/Nextcloud 环境 |
| M3 | 纯逻辑（进度映射的输入输出） | 翻页后进度是否真的跳对页 |
| M4 | JSON 往返 + 与 anx-reader 的字段约定 | 手机端是否真的显示同步进度 |
| M5 | 代码规范/编译 | 按键、排版、功耗 |

## 当前的阻塞点

1. **PlatformIO 未安装** —— 固件整体交叉编译（`pio run -e onepage`）跑不了，
   所以 M2 之后的所有代码都只能「写完待验证」。这是下一步最该解决的。
2. **本机无真机** —— 渲染、功耗、WiFi 稳定性无法验证。
3. **git push 受限** —— 本机出口对 `github.com:443` 间歇不通，且 `credential.helper`
   依赖的 MSYS `sh.exe` 在沙箱下无法创建命名管道。当前用 GitHub MCP 推送，
   本地已 `reset --hard origin/main` 对齐。

## 明确不做

- 不设备上传 `database7.db`（会毁手机端数据，理由见 [02-architecture.md](02-architecture.md)）
- 不在 ESP32 上跑 SQLite 解析（除非 M3 证明必需）
- 不碰 PDF/MOBI/AZW3 原生解析（安读已统一转 EPUB）
- 不在无真机验证的情况下宣称任何设备侧行为可用
