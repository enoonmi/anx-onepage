# anx-reader 侧进度合并补丁

## 现状

**两个 commit 已推到 [enoonmi/anx-reader@develop](https://github.com/enoonmi/anx-reader/tree/develop)：**

| commit | 内容 |
|---|---|
| `3af1d38a` | 新增 `lib/service/sync/onepage_progress_merge.dart`（217 行） |
| `e2740d20` | `lib/providers/sync.dart` 接线（+17 行，0 删除） |

## 为什么单独一个文件而不是走正常同步

anx-reader 的同步把 `database*.db` 当成整个世界：上传是 `VACUUM INTO` 快照，
下载是直接替换本地文件；`syncFiles()` 还会删除远端 `anx/data/file` 里不在
`tb_books` 中的书。一个有自己的（必然不同的）书库的电子阅读器，参与这个协议只能
二选一：毁掉手机的笔记/分组/统计，或者被手机清空。

所以设备写到正常同步**根本不会看**的路径：

```
anx/onepage/progress.json
```

它在 `anx/data/` 之外 → `syncFiles()` 不会清理；名字不是 `database*.db` →
`determineSyncDirection()` 不会误判成数据库。

## 只合并百分比

`tb_books.last_read_position` 是 foliate-js 生成的 EPUB CFI。设备没有 DOM，
产不出合法的 CFI，写进去会让手机丢失精确位置。所以只传 `reading_percentage`，
**`lastReadPosition` 从不赋值** —— 这一点由检查脚本强制（见下）。

## 冲突处理

| 规则 | 理由 |
|---|---|
| 只有当条目的 `updated_at` **严格新于**书的 `update_time` 才应用 | 在抽屉里放着的设备不能把手机拖回去 |
| 差异 < 0.1% 视为未变，跳过 | 否则每次写入都会 bump `update_time`，书看起来「已改」，两台设备无限来回 |

## 接线

`mergeFromClient()` 在 `syncData()` 里紧跟 `syncFiles()`，包在**自己的**
try/catch 里：设备文档缺失或损坏，绝不能让一次已经成功的手机同步失败。

## 验证

**这台机器没有 Dart SDK**（`dart`、`flutter` 都没有，也没有 `.dart_tool`），
所以**不是 `dart analyze`**。

替代品是 `tools/check_anx_patch.py`，它检查：

| 检查项 | 目的 |
|---|---|
| `package:anx_reader/...` import 都能解析 | 抓错路径/改名 |
| `AnxLog.*` 方法存在 | 抓拼错的方法名 |
| `bookDao.*` 方法存在 | 同上 |
| `SyncClientBase.*` 方法存在 | 特别是 `readFile` 不存在，只能 `downloadFile` |
| 每个 `book.<field> =` 都对应真实字段 | 抓改名/臆造字段 |
| `lastReadPosition` 从未被赋值 | 这是与手机的核心契约 |
| merge 真的接进了 `sync.dart` | 否则是死代码 |
| 远端路径与固件 `PathMap::remoteProgressPath()` 一致 | 两端路径必须相同 |

**检查脚本本身被验证过**：注入三个错误（假方法 `updateBookTypo`、假字段
`readingPercentageTypo`、非法写 `lastReadPosition`），三个全部被抓到。

```
ANX PATCH REFERENCE CHECK OK
```

## 仍然未验证

- **类型、null safety、参数个数** —— 需要 Flutter 才能查。
- **实际运行行为** —— 没有设备、没有真机、没有 Dart 运行时。

## 一个实现细节值得说明

`SyncClientBase` **没有** 「把文件读进内存」 的接口，只有 `downloadFile`。
所以文档先下载到临时目录再读，读完全文后尽力删除。这份文档只有几百字节，
多一跳没有代价。这是看接口时发现的，不是猜的。
