# anx-onepage

把 [Anx Reader（安读）](https://github.com/Anxcye/anx-reader) 的书库通过 WebDAV 单向同步到
[壹页 OnePage](https://oshwhub.com/movecall/project_ucjwpaya) 墨水屏阅读器，并打通阅读进度双向同步。

硬件：ESP32-C61（RISC-V）+ 16MB Flash + 2MB PSRAM + 4.26" 墨水屏 + MicroSD（启动必需）。
固件基于 [CrossPoint Reader](https://github.com/crosspoint-reader/crosspoint-reader) 的 OnePage 移植版，
已经自带 EPUB 2/3 渲染、中文排版、HTTPS 下载、进度百分比↔页码映射 —— 本项目只做**安读协议适配层**。

## 仓库结构

本仓库（`enoonmi/anx-onepage`）只放**文档、脚本与集成说明**；代码在两个 fork 里，各自独立版本控制。

| 仓库 | 说明 | 上游 |
|---|---|---|
| [enoonmi/crosspoint-onepage](https://github.com/enoonmi/crosspoint-onepage) | 固件主战场，新增 `AnxWebDavClient` 等 | [MoveCall/crosspoint-onepage](https://github.com/MoveCall/crosspoint-onepage) |
| [enoonmi/anx-reader](https://github.com/enoonmi/anx-reader) | 安读侧补丁（B1：合并设备上报的进度） | [Anxcye/anx-reader](https://github.com/Anxcye/anx-reader) |
| [enoonmi/anx-onepage](https://github.com/enoonmi/anx-onepage) | 本仓库：docs / tools / 集成记录 | — |

本地目录布局：

```
D:\code\anx-onepage\
├── README.md          本文件
├── docs\              协议、架构、开发环境笔记
├── tools\             mock WebDAV server / 桥接脚本（待测）
├── firmware\          clone of enoonmi/crosspoint-onepage（含嵌套 submodule）
└── anx-reader\        clone of enoonmi/anx-reader
```

`firmware\` 与 `anx-reader\` 是**独立的 git 仓库**（父仓库 `.gitignore` 已排除），不是 submodule —— 便于各自
独立 commit / push / 跟上游 cherry-pick，也避免嵌套 submodule 拖慢同步。

## 快速开始

```powershell
cd D:\code\anx-onepage

# 关键：Windows 系统级 gitconfig 强制 schannel，会报
#   "schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS"
# 让所有 git 子进程（含 submodule）改走 openssl：
$env:GIT_CONFIG_COUNT = 1
$env:GIT_CONFIG_KEY_0  = 'http.sslBackend'
$env:GIT_CONFIG_VALUE_0 = 'openssl'

git clone --recursive https://github.com/enoonmi/crosspoint-onepage.git firmware
git clone https://github.com/enoonmi/anx-reader.git anx-reader
```

编译固件（Windows 需 [pioarduino](https://github.com/pioarduino/pioarduino)）：

```powershell
cd firmware
pio run -e onepage
```

烧录前先用 `esptool --chip esp32c61 -p <PORT> flash-id` 读 Flash 厂商：Winbond 走正常 stub，
Puya PY25Q128HA 必须 `--no-stub`。详见 [docs/03-dev-environment.md](docs/03-dev-environment.md)。

## 目标与路线

- [x] 确认硬件/固件已覆盖阅读引擎、中文、HTTPS、进度映射
- [x] 建立三个仓库并 fork 到 GitHub 账户
- [ ] **Milestone 1**：刷机验中文排版（不改代码，先确认体感）
- [ ] **Milestone 2**：`AnxWebDavClient` —— PROPFIND 列 `anx/data/file/`、下载 EPUB 到 SD、从 OPF 读书名（纯单向，零 SQLite、零进度）
- [ ] **Milestone 3**：手机→设备进度，接 `ProgressMapper::toCrossPoint()`
- [ ] **Milestone 4**：设备→手机进度，安读侧 merge patch（方案 B1）

## 关键约束（务必先读）

1. 设备**绝不上传 `anx/database*.db`** —— 安读是整库覆盖同步，会冲掉手机端笔记/分组/统计，且
   `syncFiles()` 会按新库删除远端书籍文件。
2. 设备**不往 `anx/data/file|cover/` 写任何东西**，只 GET。
3. 进度回写走 `anx/onepage/progress.json`（独立子目录，不叫 `database*.db`），由安读侧合并。
4. `last_read_position` 是 EPUB CFI，设备侧只更新 `reading_percentage`，CFI 保持手机端原值。
5. 同步完立刻断 WiFi 进深度睡眠，1000mAh 电池扛不住长连接。

详见 [docs/01-anx-webdav-protocol.md](docs/01-anx-webdav-protocol.md) 与
[docs/02-architecture.md](docs/02-architecture.md)。

## 许可

- 固件 fork：MIT（保留 CrossPoint 版权声明）
- 安读 fork：见上游 [LICENSE](https://github.com/Anxcye/anx-reader/blob/main/LICENSE)
- 硬件设计：CC-BY-SA-4.0 / CERN-OHL-S（[壹页项目页](https://oshwhub.com/movecall/project_ucjwpaya)）
- 本仓库文档：CC-BY-SA-4.0
