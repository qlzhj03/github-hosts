# github-hosts

让 Linux 稳定访问 GitHub 的一组小脚本：自动探测当前可用的 GitHub IP，写入 `/etc/hosts`，并用 systemd 定时器每 10 分钟复核刷新。

项目地址：<https://github.com/qlzhj03/github-hosts>

适用于「DNS 被污染 / GitHub 某些边缘 IP 间歇性超时」导致的主站、Gist、API 或静态资源打不开。

## 工作原理

1. 通过阿里 DoH、腾讯 DoH 以及内置种子 IP，收集每个域名的候选地址；
2. 用 `curl --resolve` 对每个候选做真实 HTTPS 请求（含 TLS 证书校验），失败重试 3 次，按延迟选最优；
3. 只更新 `/etc/hosts` 中 `BEGIN/END github-hosts` 标记块，不改动其他条目；
4. 每次写入前自动备份到 `/etc/hosts.github-hosts-backup-<时间戳>`；
5. 某个域名所有候选都探测失败时，保留上一次可用 IP，避免把配置写坏；
6. `gist.github.com` 与 `github.com` 共用前端，探测失败时会复用 `github.com` 已验证的 IP。

覆盖域名：`github.com`、`www.github.com`、`api.github.com`、`codeload.github.com`、`gist.github.com`，以及 `github.githubassets.com`、头像、Camo、Release 下载等 7 个静态/资源域名，共 12 个。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `github-hosts.py` | 核心脚本：探测 IP、写入/还原 hosts |
| `install.sh` | 立即应用一次，并安装 systemd 服务与 10 分钟定时器 |
| `uninstall.sh` | 停止定时器、删除 unit、还原 `/etc/hosts` |

依赖：`python3`、`curl`、systemd。

## 安装

```bash
git clone https://github.com/qlzhj03/github-hosts.git ~/github-hosts
cd ~/github-hosts
sudo bash install.sh
```

安装后会立即探测并写入一次，然后启用 `github-hosts.timer`。如果之前装过旧名 `workbuddy-github-hosts` 的版本，`install.sh` 会自动停用并删除旧 unit，同时把 hosts 里的旧标记块迁移为新标记块。

## 常用命令

```bash
# 只看探测结果，不修改系统（无需 root）
python3 github-hosts.py check

# 手动刷新一次
sudo python3 github-hosts.py apply

# 查看下次刷新时间 / 刷新日志
systemctl list-timers github-hosts.timer
journalctl -u github-hosts.service -n 30
```

验证：

```bash
getent ahostsv4 github.com
getent ahostsv4 gist.github.com
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com
curl -sS -o /dev/null -w '%{http_code}\n' https://gist.github.com
```

`github.com` 返回 `200`、`gist.github.com` 返回 `302` 即为正常。

## 卸载 / 回滚

```bash
sudo bash uninstall.sh
```

该命令会删除 `github-hosts` 以及旧 `workbuddy-github-hosts` 的 systemd unit，并移除 hosts 标记块；历史备份仍保留在 `/etc/hosts.github-hosts-backup-*`（旧名时期的备份在 `/etc/hosts.workbuddy-backup-*`），可手动恢复。

## 已知限制

`raw.githubusercontent.com` 在部分网络下是 **SNI 阻断**：TCP 可以连接，但 TLS 握手被重置，因此换任何 IP 都无效，脚本不会去固定它。访问 raw 文件时使用镜像前缀：

```
https://ghfast.top/https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>
https://gh-proxy.com/https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>
https://ghproxy.net/https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>
```

镜像为第三方中转，不要用它传输私有仓库内容、Token 或其他敏感文件。需要直连 raw 时请使用自己的代理。

另外，IP 会随时间变化；如果某天全部域名都探测失败，通常是当前网络整体不通，定时器会保留上次可用值，等网络恢复后自动更新。
