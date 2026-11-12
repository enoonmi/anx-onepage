# 架构设计

## 结论回顾

代码移植不可行（Flutter/Dart + WebView + foliate-js 需要 OS 与浏览器引擎），但
**协议层复用完全可行**：安读的 WebDAV 是标准 HTTP + Basic Auth + PROPFIND，书籍统一是 EPUB，
元数据在 SQLite 单文件里。OnePage/CrossPoint 固件已具备除「安读协议」外的一切。

## 三条路线的选择

| | A 只读客户端 | B 桥接服务 + 瘦终端 | C 完全本地解析（本项目） |
|---|---|---|---|
| 做法 | 直接读 WebDAV、自解 EPUB | 外部服务预处理成纯文本 | 基于 crosspoint-onepage 加适配层 |
| 中文 | 需自己加字库断行 | 服务端预排版 | ✅ 固件已做 |
| 网络 | 需自己写 | 设备只 GET | ✅ `HttpDownloader` 已做 |
| 进度 | 单向百分比 | 双向可做 | ✅ `ProgressMapper` 已做 |

选 **C**：因为固件已经把 A 和 B 里最难的部分都实现了，工作量只剩协议适配，约 1–2 周。

## 分层

```
┌─────────────────────────────────────────────────────────┐
│ UI 层（复用）                                            │
│   书架 = 现有 folder browser / recent books              │
│   阅读 = EpubReaderActivity + 现有进度活动               │
│   新增：设置页「Anx 同步」+ 一个同步入口                 │
├─────────────────────────────────────────────────────────┤
│ 新增适配层 AnxSync（本项目唯一的主要新代码）             │
│   ├─ AnxCredentialStore    URL/账号/密码（照抄 OPDS）   │
│   ├─ AnxWebDavClient       PROPFIND / GET / PUT(json)   │
│   ├─ AnxBookCatalog        远端列表 ↔ 本地 /Books 差量  │
│   └─ AnxProgressBridge     ProgressMapper ↔ JSON         │
├─────────────────────────────────────────────────────────┤
│ 现有能力（不动或极小改动）                               │
│   HttpDownloader           HTTPS + BasicAuth + 流式落 SD │
│   lib/expat                解析 PROPFIND 响应 XML        │
│   lib/Epub                 读 OPF metadata 取书名/作者   │
│   lib/JsonParser           生成/解析 progress.json       │
│   lib/KOReaderSync/ProgressMapper  百分比 ↔ 页码双向映射 │
│   HalStorage               SD 访问（必须经 mutex）       │
└─────────────────────────────────────────────────────────┘
```

## 数据流

### 书籍单向（WebDAV → 设备）

```
唤醒/手动同步
  → WiFi STA 连接（复用 WifiCredentialStore）
  → PROPFIND <base>/anx/data/file/  (Depth:1, BasicAuth)
  → 解析 <d:href>/<d:getlastmodified>/<d:getcontentlength>
  → 与本地 /Books/ 差量比对（按文件名 + mtime/size）
  → 对每个新增/变更项 GET，HttpDownloader::downloadToFile 到 /Books/
  → 打开时由 lib/Epub 从 OPF 读 title/author（文件名是 hash，不能当书名）
  → 断 WiFi，进深度睡眠
```

**MVP 不读 `database7.db`** —— 书名作者从 EPUB 内 OPF 取，省掉在 2MB PSRAM 上解析 SQLite。
等 Milestone 3 需要 `reading_percentage` 时再决定：移植只读 sqlite3，或让安读侧额外导出精简 JSON。

### 进度双向

**手机 → 设备（Milestone 3）**

```
读取进度源（database7.db 或精简 JSON）
  → tb_books.reading_percentage (0.0–1.0)
  → ProgressMapper::toCrossPoint(epub, {xpath:"", percentage}, renderer, ...)
  → (spineIndex, pageNumber) 落到 .crosspoint/epub_<hash>/progress.bin
```

`ProgressMapper.h` 明确写着「percentage 作为主要同步机制，pageNumber 允许近似」，天然适配。
务必用 `ProgressMapper` 而不是直接按百分比乘总页数 —— 它还会做 spine 密度估算与 XPath 生成。

**设备 → 手机（Milestone 4，方案 B1）**

```
退出阅读/定时上报
  → ProgressMapper::toSavedProgress() 得 percentage
  → PUT <base>/anx/onepage/progress.json
  → 安读侧 patch：syncData() 时读该文件，按 file_path 匹配 tb_books，
     只更新 reading_percentage + update_time，保留 last_read_position(CFI)
```

冲突策略：`progress.json` 的 `updated_at` vs `tb_books.update_time` 比大小，新的胜出。

### 为什么不能让设备上传 database7.db

安读 `syncDatabase()` 是**整库覆盖**：上传方向用 `VACUUM INTO` 快照 PUT，下载方向用
`DatabaseSyncManager.safeDownloadDatabase()` 直接替换本地 db。设备若回传精简库：

1. 手机端笔记 `tb_notes`、分组 `tb_groups`、阅读统计 `tb_reading_time` 全部丢失；
2. `syncFiles()` 随后遍历远端 `file/`+`cover/`，把不在新 `tb_books` 里的书籍 `remove()` 掉；
3. `determineSyncDirection()` 扫到 `database*.db` 版本号 > 7 会直接中止同步。

→ 见 [01-anx-webdav-protocol.md](01-anx-webdav-protocol.md) 的「安全写入点」。

## 幂等与失败处理

| 场景 | 策略 |
|---|---|
| 中途断电 / 下载一半 | `HttpDownloader` 写 SD 用临时文件，完成后才落到最终名；参照 WebDAVHandler 的 `.davtmp` 思路 |
| WiFi 失败 | 保留上次成功同步的 mtime，静默跳过，不阻塞阅读 |
| PROPFIND 返回 404 | 提示「未找到 anx/ 目录」，不自动 MKCOL（避免上游 #890 的非标准 201） |
| HTTPS 私有 CA | mbedTLS 需内置 CA（上游 #719 同源问题） |
| 进度上报失败 | 本地队列暂存，下次同步重试；不因网络失败丢失阅读位置 |

## 功耗

1000mAh + 4.26" 墨水屏。同步是耗电大头，设计成「唤醒 → 同步一次 → 断网 → 长期离线阅读 → 深度睡眠」，
进度批量回写，禁止保持长连接。固件已有 `boot_sleep` 活动与电源管理，复用即可。

## 内存约束注意

固件自带 `.skills/SKILL.md` 是**上游 Xteink X4 / ESP32-C3** 的规范，写着「380KB RAM、无 PSRAM、
单 48KB framebuffer」。OnePage 是 **ESP32-C61 + 2MB PSRAM**，屏幕 4.26"，约束更宽松但也不同。
那份文档关于 HAL、日志、RAII、Activity 生命周期、生成文件、commit 规范的部分仍然有效；涉及具体
RAM 数字、framebuffer 大小、SDK 目录名（文档写 `open-x4-sdk/`，实际是 `open-onepage-sdk/`）的地方
**不可照抄**，改代码前以 `platformio.ini` / `sdkconfig.onepage` / 实际板级 SDK 为准。
