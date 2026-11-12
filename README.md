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
| [enoonmi/crosspoint-onepage](https://github.com/enoonmi/crosspoint-onepage) | 固件主战场，新增 `lib/AnxSync/` 与 `lib/AnxSyncIo/` | [MoveCall/crosspoint-onepage](https://github.com/MoveCall/crosspoint-onepage) |
| [enoonmi/anx-reader](https://github.com/enoonmi/anx-reader) | 安读侧补丁（合并设备上报的进度） | [Anxcye/anx-reader](https://github.com/Anxcye/anx-reader) |
| [enoonmi/anx-onepage](https://github.com/enoonmi/anx-onepage) | 本仓库：docs / tools / 集成记录 | — |

本地目录布局：

```
D:\code\anx-onepage\
├── README.md          本文件
├── docs\              协议、架构、开发环境、路线图、事故复盘
├── tools\             mock WebDAV / 三个检查脚本
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

## 当前进度

**M1–M5 与 anx-reader 侧补丁都已完成**，五项本机验证全部通过：

| 验证 | 结果 |
|---|---|
| 纯逻辑单测（固件） | `317 checks, 0 failures` |
| IO 层编译 + 链接 + 冒烟 | `IO LAYER SMOKE OK` |
| UI 引用检查 | `UI REFERENCE CHECK OK` |
| anx-reader 补丁引用检查 | `ANX PATCH REFERENCE CHECK OK` |
| **跨语言契约对拍** | `CONTRACT VERIFIED` |

```powershell
cd firmware
powershell -ExecutionPolicy Bypass -File tests\host\run_tests.ps1   # 317 断言
python tests\host\check_io_layer.py                                  # 编译+链接+冒烟
python tests\host\check_ui_refs.py                                   # UI 引用检查
python ..\tools\check_anx_patch.py                                   # anx-reader 引用检查
python ..\tools\verify_contract.py                                   # 两端契约对拍
```

最后一项值得单独说明：`verify_contract.py` 会**编译并运行固件真实的进度管线**，
把产出的 `progress.json` 交给**按手机端规则实现的解析器**再读一遍，确认两端
在路径、schema、字段名、单位、时间戳语义上真的对得上。这不是文档里的断言，
是可执行的检查。

代码在 [enoonmi/crosspoint-onepage@main](https://github.com/enoonmi/crosspoint-onepage) 与
[enoonmi/anx-reader@develop](https://github.com/enoonmi/anx-reader/tree/develop)。

> ⚠️ **以上全部是开发机验证，零真机。** 三条硬限制：
> 1. **PlatformIO 装不上**（沙箱不允许删除 pip 的临时目录）→ 固件整体交叉编译
>    `pio run -e onepage` 从未跑过，`lib/AnxSyncIo/` 与两个 UI 文件从未被
>    PlatformIO 编译，签名/类型错误未验证。
> 2. **没有 Dart SDK** → anx-reader 补丁从未 `dart analyze`，类型与 null safety 未验证。
> 3. **没有真机** → 渲染、按键、功耗、TLS 握手、SD 写入全部未验证。

## 路线

- [x] 确认硬件/固件已覆盖阅读引擎、中文、HTTPS、进度映射
- [x] 建立三个仓库并 fork 到 GitHub 账户
- [x] **M1**：纯逻辑核心（时间/解析/路径/决策/JSON）+ 317 断言 + 真实 HTTP 对拍
- [x] **M2**：`AnxWebDavClient` + `AnxCredentialStore`（编译链接验证）
- [x] **M3/M4**：进度双向（`AnxProgressLocator/Bridge/Store`，边界语义由测试锁定）
- [x] **M5**：设置页 + 同步 Activity + 菜单接入 + 13 个 i18n 键
- [x] **anx-reader patch**：`onepage_progress_merge.dart` + `sync.dart` 接线
- [x] **跨语言契约对拍**
- [ ] 真机验证（需硬件）
- [ ] PlatformIO 交叉编译（需可用的工具链环境）

详细拆解与阻塞点见 [docs/04-roadmap.md](docs/04-roadmap.md)。

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
