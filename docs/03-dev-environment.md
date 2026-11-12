# 开发环境与已知坑（Windows）

## 工具链

| 项 | 状态 | 备注 |
|---|---|---|
| git | 2.50.0.windows.1 (`D:\Program Files\Git\cmd\git.exe`) | 系统级 gitconfig 强制 `http.sslbackend=schannel` |
| Python | 3.13.14 | 满足固件要求（>=3.10） |
| PlatformIO / pioarduino | **未安装** | 需 `pip install platformio`（C61 建议 pioarduino 分支），装在 `%USERPROFILE%\.platformio`，超出工作区沙箱 |
| esptool | 未确认 | `pip install esptool` |
| gh CLI | 未安装 | 本轮用 GitHub MCP 完成 fork/建仓 |
| GCM (git-credential-manager) | 在 PATH 外但被 gitconfig 引用 | 见下 |

## 坑 1：schannel + Windows 凭据管理器 → 所有 git HTTPS 失败

现象：

```
fatal: unable to access 'https://github.com/...': schannel: AcquireCredentialsHandle failed:
SEC_E_NO_CREDENTIALS (0x8009030E)
```

排查结论：

- `D:\Program Files\Git\etc\gitconfig` 有 `http.sslbackend = schannel` 与
  `credential.helper = manager`；
- `cmdkey /list` 里存在 `LegacyGeneric:target=git:https://github.com` / User `enoonmi` 条目；
- `Invoke-WebRequest https://api.github.com/rate_limit` 也报「基础连接已经关闭」；
- `git -c credential.helper= ls-remote` 仍失败，临时 HOME + `GIT_CONFIG_NOSYSTEM=1` 也失败；
- **`git -c http.sslBackend=openssl -c credential.helper= ls-remote` 成功**（返回 HEAD SHA）。

→ 根因是 schannel 凭据句柄，不是网络不通（443 可连、无系统代理）。

**推荐做法**（不改系统配置、不影响其他项目，环境变量会传给所有 git 子进程包括 submodule）：

```powershell
$env:GIT_CONFIG_COUNT = 2
$env:GIT_CONFIG_KEY_0  = 'http.sslBackend';  $env:GIT_CONFIG_VALUE_0  = 'openssl'
$env:GIT_CONFIG_KEY_1  = 'credential.helper'; $env:GIT_CONFIG_VALUE_1 = 'manager'
git clone --recursive https://github.com/enoonmi/crosspoint-onepage.git firmware
```

`credential.helper=manager` 保留是为了 push 时能取到凭据管理器里已有的 `enoonmi` 凭据。
若 push 仍报认证失败，需要更新那条凭据条目（应放 GitHub PAT，不是网页密码），或改用
`https://<PAT>@github.com/...` 形式的 remote。

**注意**：本地网络对 `github.com:443` 会间歇性整体不通（`Test-NetConnection` 返回 False），
表现为 21s 超时。这与上面的 schannel 问题无关，重试或换时间即可。本轮 fork/建仓/首次提交
改走 GitHub MCP 通道完成（见「推送说明」）。

## 坑 2：MSYS `sh.exe` 无法创建信号管道（DSH 沙箱限制）

现象（clone 与 `git submodule` 都可能出现，但 clone 本体仍成功）：

```
0 [main] sh (8160) D:\Program Files\Git\usr\bin\sh.exe:
*** fatal error - couldn't create signal pipe, Win32 error 5
```

这是当前 harness 沙箱禁止命名管道导致的（工具说明里明确写了 Node `stdio:pipe` / MSYS 同类限制），
**不是仓库或网络问题**。直接执行 `sh.exe -c 'echo'` 也复现。

影响：`git submodule update --init --recursive` 依赖 sh，会失败并返回空状态。

**绕法**：不用 `git submodule`，改成**显式 git clone 到 gitlink 指定的路径**，再 checkout 到 gitlink 的 SHA。

```powershell
$fw  = 'D:\code\anx-onepage\firmware'
$sdk = Join-Path $fw 'open-onepage-sdk'
$sha = '16b6755ded7341861d277f7f8c75efb6bff41337'   # 来自 gitlink

git clone https://github.com/MoveCall/onepage-reader-sdk-arduino.git $sdk
git -C $sdk checkout $sha
```

校验方法：`git -C firmware submodule status` 在该环境下不可靠；直接看
`git -C $sdk rev-parse HEAD` 是否等于 SHA。

⚠️ 用 `git -C $sdk rev-parse --short HEAD` 检查时，若 `$sdk/.git` 不存在，git 会**向上找到父仓库**
并返回父仓库的 HEAD（本轮就出现过 `16477d0` 的假阳性）——必须确认 `$sdk/.git` 真实存在。

## 坑 3：submodule URL 是 SSH，且 gitlink 未随 fork 改变

`firmware/.gitmodules`：

```ini
[submodule "open-onepage-sdk"]
    path = open-onepage-sdk
    url = git@github.com:MoveCall/onepage-reader-sdk-arduino.git
```

即使 fork 了 `crosspoint-onepage`，submodule 仍指向 **MoveCall 上游**（不是 `enoonmi` 的 fork），
且用 SSH。已在本地 `.git/config` 覆盖为 HTTPS（只影响本地，不提交到 fork）：

```powershell
git -C firmware config submodule.open-onepage-sdk.url https://github.com/MoveCall/onepage-reader-sdk-arduino.git
```

若以后要改 SDK，需要另行 fork `MoveCall/onepage-reader-sdk-arduino` 并更新 `.gitmodules`。

## 坑 4：偶发 443 连接超时

某几次 clone / 全部 push 尝试报 `Failed to connect to github.com port 443 after 21046 ms`。
`Test-NetConnection github.com -Port 443` 在故障窗口内返回 False，而 GitHub MCP 通道正常。
属本地出口网络问题，不是认证或仓库问题。

## 坑 5：anx-reader 默认分支是 `develop`

本地 clone 落在 `develop` @ `0c7fc1ad`（`release: v1.15.0-alpha.35`），不是 `main`。
做 B1 patch 时基于 `develop` 建分支，别假设 main。

## 坑 6：Flash 厂商决定烧录方式

来自固件 README：

```bash
esptool --chip esp32c61 -p <PORT> flash-id   # 读 Manufacturer byte
```

- Winbond (`ef`)：`--flash-mode dio --flash-freq 80m --flash-size 16MB`，可压缩，约 95s
- Puya PY25Q128HA (`85`)：**必须 `--no-stub -b 115200`**，约 380s，否则 stub 压缩会崩

以 `Hash of data verified` 为准，别只看 esptool 退出码。

## 仓库与分支速查

| 本地路径 | remote | 分支 |
|---|---|---|
| `D:\code\anx-onepage\firmware` | `enoonmi/crosspoint-onepage` | `main` |
| `D:\code\anx-onepage\anx-reader` | `enoonmi/anx-reader` | `develop` |
| `D:\code\anx-onepage` | `enoonmi/anx-onepage` | `main`（本仓库） |

上游追踪（建议加，便于 cherry-pick 引擎修复；此 port 与上游**无共享 git 历史，不能 merge**）：

```powershell
git -C firmware remote add upstream https://github.com/MoveCall/crosspoint-onepage.git
git -C anx-reader remote add upstream https://github.com/Anxcye/anx-reader.git
```

## 推送说明（本轮特殊情况）

本地 `git push` 因 `github.com:443` 间歇性不通未能完成。首次提交由 GitHub MCP 通道
（`push_files`）分两次推送到 `enoonmi/anx-onepage@main`，内容与本地 commit `7d5fe1e`
（7 个文件、532 行）一致。等网络恢复后，本地仓库与远端 **SHA 不同但内容相同**，
需要先同步一次再继续本地提交：

```powershell
# 网络恢复后（任选其一，二选一，勿直接 push 造成分叉）
git -C D:\code\anx-onepage fetch origin
git -C D:\code\anx-onepage reset --hard origin/main   # 丢弃本地 7d5fe1e，采用远端内容
```
