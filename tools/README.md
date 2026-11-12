# tools/

本目录放**不烧进设备**的辅助脚本，用于在没有真机 / 私有 WebDAV 时联调协议。

## 已有

| 脚本 | 用途 | 状态 |
|---|---|---|
| `mock_webdav.py` | 纯 Python 标准库的 WebDAV 服务器，摆出安读的 `anx/` 目录结构，带 Basic Auth | ✅ 已用于端到端对拍 |

## 规划中

| 脚本 | 用途 | 状态 |
|---|---|---|
| `make_fixture.py` | 自动造最小 EPUB + 按安读规则生成哈希文件名与 `progress.json` 样例 | 待做 |
| `bridge.py` | 备选方案 B2：把 `anx/onepage/progress.json` 合并回 `database7.db` 并 PUT（若不做 B1 的安读侧 patch 则用它） | 待做 |
| `propfind_probe.py` | 打印真实 WebDAV 服务器的 PROPFIND 响应，确认 href 编码 / getlastmodified 格式差异 | 待做 |

## mock_webdav.py 用法

```powershell
# 准备目录：anx/data/file/、anx/data/cover/、anx/onepage/
python tools\mock_webdav.py --port 18080 --root tools\_fixtures --user reader --password secret

# 抓一份真实 PROPFIND 响应
curl.exe -sS -o out.xml -X PROPFIND -H "Depth: 1" -u reader:secret `
  http://127.0.0.1:18080/anx/data/file/

# 验证认证生效（应返回 401）
curl.exe -sS -o NUL -w "HTTP %{http_code}`n" -X PROPFIND -H "Depth: 1" `
  http://127.0.0.1:18080/anx/data/file/
```

支持方法：`OPTIONS` / `PROPFIND`（Depth 0、1）/ `GET` / `HEAD` / `PUT` / `DELETE` / `MKCOL`。

## 配置

复制 [.env.example](.env.example) 为 `.env` 并填写真实凭据。`.env` 已在仓库根 `.gitignore` 里排除。

## 注意

- 真实 WebDAV 的 `PROPFIND` 响应里 `<d:href>` 常带 URL 编码，且第一条是目录自身；
  `AnxPropfindParser` 已处理，目录条目通过 `isCollection` 暴露给调用方过滤。
- Nextcloud 等服务器可能返回**绝对 URL** 形式的 href，`PathMap::stripBaseUrl` 覆盖了这种情况。
- Nextcloud / 坚果云等 HTTPS 服务多为自签或私有 CA，固件侧 mbedTLS 需内置 CA。
- 别用 `curl.exe`（Windows 版）测 HTTPS —— 它走 schannel，会复现
  `SEC_E_NO_CREDENTIALS`。用 Python `requests` 或 `git -c http.sslBackend=openssl`。
