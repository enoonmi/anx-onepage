# 安读 WebDAV 协议速查

来源：`Anxcye/anx-reader` v1.14.0
关键文件：`lib/providers/sync.dart`、`lib/service/sync/webdav_client.dart`、
`lib/service/database_sync_manager.dart`、`lib/dao/database.dart`。

## 目录约定

用户在设置里填的是**可写目录 URL**，应用在其下创建 `anx/`（见 `app_en.arb` 的
`settingsSyncWebdavUrlHint`）。`Sync._createAnxDir()` 建立：

```
<base>/anx/
├── database7.db              # 整库 SQLite 快照（VACUUM INTO），currentDbVersion = 7
├── data/
│   ├── file/<hash>.epub      # 书籍；导入时其他格式已统一转 EPUB
│   └── cover/<hash>.png      # 封面
└── .test/                    # testFullCapabilities() 的临时目录
```

`syncFiles()` 里远端路径前缀固定为 `anx/data/`；`database7.db` 名字来自
`database$currentDbVersion.db`。

## 认证与请求

`WebdavClient` = `webdav_client` + `dio`：Basic Auth（url / username / password），
默认 header `accept-charset: utf-8`、`Content-Type: application/octet-stream`，
connect timeout 8000ms。`_safeEncodePath()` = `Uri.encodeComponent` 后把 `%2F` 还原成 `/`
（即逐段编码、保留斜杠）。

客户端用到的方法：`PROPFIND`（readDir / readProps）、`MKCOL`（mkdirAll）、
`GET`（read2File）、`PUT`（writeFromFile）、`DELETE`（remove）。
`uploadFile()` 默认先 `DELETE` 再 `PUT`；部分服务器不支持 DELETE 会 405（上游 issue #912）。

### PROPFIND 列目录（设备侧要实现的最小子集）

```http
PROPFIND /remote.php/dav/files/user/anx/data/file/ HTTP/1.1
Host: cloud.example.com
Authorization: Basic <base64>
Depth: 1
Content-Type: application/xml

<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:"><d:prop>
  <d:getlastmodified/><d:getcontentlength/><d:resourcetype/>
</d:prop></d:propfind>
```

解析时只需三个字段（用固件自带的 `lib/expat` 即可，不必引 XML DOM）：

| 标签 | 用途 |
|---|---|
| `<d:href>` | 文件名（注意 URL 编码，且响应常含目录自身条目） |
| `<d:getlastmodified>` | RFC 1123 时间，用于增量判断 |
| `<d:getcontentlength>` | 大小，进度条 / 空间预检 |

### 下载书籍

```http
GET /remote.php/dav/files/user/anx/data/file/<hash>.epub HTTP/1.1
Authorization: Basic <base64>
```

直接交给 `HttpDownloader::downloadToFile(url, "/Books/<hash>.epub", ..., username, password)`
流式写 SD，**不要读进内存**。注：`HttpDownloader` 从 URL scheme 自动选 https/http，
https 校验 CA bundle，且已支持 Basic Auth 用户名密码。

## 数据库（Milestone 3+ 才需要）

`app_database.db` / `database7.db` 是标准 SQLite，`PRAGMA user_version = 7`。
`tb_books` 关键列：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `title` / `author` | TEXT | 书名作者；**文件名是 md5 哈希，不能当书名** |
| `file_path` | TEXT | `file/<hash>.epub`，相对 `anx/data/` |
| `cover_path` | TEXT | `cover/<hash>.png` |
| `last_read_position` | TEXT | **EPUB CFI**，foliate-js 的 DOM 定位，设备侧不可生成 |
| `reading_percentage` | REAL | 0.0–1.0，**双方都能理解的进度的唯一载体** |
| `is_deleted` | INTEGER | 软删除 |
| `file_md5` | TEXT | v6 起新增 |
| `group_id` | INTEGER | |

上传前用 `VACUUM INTO` 生成一致性快照，并把文件头 offset 18/19 从 2（WAL）改成 1（Legacy），
保证下载方无需 `-wal` 也能打开。下载方做 `PRAGMA integrity_check` + 必需表 + `tb_books` 非空 +
`user_version <= 7` 校验，失败从备份恢复。

## 同步方向判定（决定设备不能乱写文件）

`determineSyncDirection()`：

1. `safeReadDir('/anx')` 里扫 `database*.db`，若版本号 > 7 → 弹「版本不匹配」并**中止**；
2. `readProps('anx/database7.db')` 取远端 mtime，与本地 db（含 WAL）mtime 比较，差 < 5s 视为无需同步；
3. 远端无 db → 上传；否则按 `lastUploadBookDate` 决定是否弹窗让用户选上传/下载。

→ **设备侧禁止在 `anx/` 下创建任何 `database*.db` 命名的文件**。

`syncFiles()` 会遍历远端 `file/` 与 `cover/`，把不在 `tb_books` 里的文件 `remove()` 掉。
→ **设备只能 GET `anx/data/`，绝不 PUT 进去**；私有文件放 `anx/` 的其他子目录（如
`anx/onepage/`）是安全的，因为该方法只扫 `data/file` 与 `data/cover`。

## 设备侧可用的安全写入点

```
<base>/anx/onepage/progress.json
```

- 不匹配 `database*.db` 前缀 → 不会触发版本检查；
- 不在 `data/file|cover/` → 不会被 `syncFiles()` 删除；
- 安读忽略未知文件 → 不会破坏手机端。

建议内容（待定稿）：

```json
{
  "schema": 1,
  "device": "onepage-c61",
  "updated_at": "2026-11-12T09:55:15+08:00",
  "books": [
    { "file_path": "file/abcdef1234567890.epub",
      "reading_percentage": 0.4231,
      "updated_at": "2026-11-12T09:50:00+08:00" }
  ]
}
```

安读侧（B1）在 `syncDatabase()` 之前/之后读它，按 `file_path` 匹配 `tb_books`，
只更新 `reading_percentage` 与 `update_time`，**不动 `last_read_position`（CFI）**，
避免与 foliate-js 精确定位打架；冲突用 `updated_at` 与本地 `update_time` 比大小。

## 已知上游问题（实现时留意）

来源 `docs/issue-triage.md`：

- #911 整库覆盖、无记录级合并 → 更需要设备别写 db；
- #912 部分 WebDAV 服务器不支持 DELETE → 上传先试 PUT 覆盖；
- #758 远端路径硬编码 `/anx/data/file` → 设备侧也先硬编码，别做可配置；
- #890 ZSpace 的 MKCOL 返回非标准 201 → 设备只 GET/PROPFIND，不 MKCOL；
- #719 Android 不信任用户 CA → 设备侧 mbedTLS 需内置 CA（私有 Nextcloud 场景）。
