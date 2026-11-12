# tools/

本目录放**不烧进设备**的辅助脚本，用于在没有真机/私有 WebDAV 时联调协议。

## 规划中的脚本

| 脚本 | 用途 | 状态 |
|---|---|---|
| `mock_webdav.py` | 用 `wsgidav` 或标准库起一个本地 WebDAV，摆出 `anx/data/file/*.epub` + `anx/database7.db`，供固件/单测连 | 待做 |
| `make_fixture.py` | 造最小 EPUB + 按安读规则生成哈希文件名与 `progress.json` 样例 | 待做 |
| `bridge.py` | 备选方案 B2：把 `anx/onepage/progress.json` 合并回 `database7.db` 并 PUT（若不做 B1 的安读侧 patch 则用它） | 待做 |
| `propfind_probe.py` | 打印真实 WebDAV 服务器的 PROPFIND 响应，确认 href 编码 / getlastmodified 格式差异 | 待做 |

## 配置

复制 [.env.example](.env.example) 为 `.env` 并填写。`.env` 已在仓库根 `.gitignore` 里排除。

## 注意

- 真实 WebDAV 的 `PROPFIND` 响应里 `<d:href>` 常带 URL 编码，且第一条是目录自身；
  解析时逐段 decode 并跳过 `Depth:1` 的集合项。
- Nextcloud / 坚果云等 HTTPS 服务多为自签或私有 CA，固件侧 mbedTLS 需内置 CA。
- 别用 `curl.exe`（Windows 版）测 HTTPS —— 它走 schannel，会复现
  `SEC_E_NO_CREDENTIALS`。用 Python `requests` 或 `git -c http.sslBackend=openssl`。
