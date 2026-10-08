# 登录服务（账号密码 + 邀请码注册）

放在 Marzban 前面的一个小服务：朋友用邀请码注册账号，之后用账号密码登录，App 拿到各自的订阅链接。

- 只用 Python 标准库，服务器上不用装任何包（Ubuntu 自带 `python3` 即可）。
- Marzban 的管理员账号只存在服务器上，不会进 App。
- 密码只存哈希（scrypt）。登录有限流，同一账号连续输错 5 次会锁 15 分钟。

## 一、准备一个专用的 Marzban 管理员

不要用你的主管理员账号，单独建一个，出问题可以随时删掉：

```bash
marzban cli admin create
```

按提示输入名字（例如 `authsvc`）和密码，**问是否 sysadmin 时选 No**。这个账号只能管理它自己创建的用户。

## 二、安装服务

```bash
mkdir -p /opt/flclash-auth
curl -fsSL https://raw.githubusercontent.com/lylcloudflare/FlClashsub/main/server/auth_server.py \
  -o /opt/flclash-auth/auth_server.py
```

如果仓库是私有的，上面的下载会失败：在电脑上把 `server/auth_server.py` 下载下来，用 `scp` 传到 `/opt/flclash-auth/`。

写配置文件（把 `你的域名`、`密码` 换成你自己的）：

```bash
cat > /etc/flclash-auth.env <<'EOF'
MARZBAN_URL=https://127.0.0.1:8000
MARZBAN_INSECURE=1
MARZBAN_ADMIN_USER=authsvc
MARZBAN_ADMIN_PASS=这里填刚才设置的密码
SUB_URL_PREFIX=https://你的域名:8000
AUTH_CERT=/var/lib/marzban/certs/fullchain.pem
AUTH_KEY=/var/lib/marzban/certs/key.pem
AUTH_PORT=9000
EOF
chmod 600 /etc/flclash-auth.env
```

说明：`MARZBAN_INSECURE=1` 只用于本机 127.0.0.1 之间的连接（证书是按域名签的，用 IP 访问会不匹配），不影响对外的 HTTPS。

## 三、开机自启

```bash
cat > /etc/systemd/system/flclash-auth.service <<'EOF'
[Unit]
Description=FlClash auth service
After=network-online.target docker.service

[Service]
EnvironmentFile=/etc/flclash-auth.env
ExecStart=/usr/bin/python3 /opt/flclash-auth/auth_server.py serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now flclash-auth
systemctl status flclash-auth --no-pager
```

看到 `active (running)` 就成功了。放行端口（CloudCone 控制台的防火墙也要放行 TCP 9000）：

```bash
ufw allow 9000/tcp
```

验证（在你自己的电脑上执行）：

```
curl https://你的域名:9000/healthz
```

应该返回 `{"ok": true}`。

**证书续期后**需要重启一次服务才会用上新证书：`systemctl restart flclash-auth`。

## 四、管理命令

先建一个简短的命令 `flauth`，之后都用它：

```bash
cat > /usr/local/bin/flauth <<'EOF'
#!/bin/sh
set -a; . /etc/flclash-auth.env; set +a
exec python3 /opt/flclash-auth/auth_server.py "$@"
EOF
chmod +x /usr/local/bin/flauth
```

| 命令 | 作用 |
|---|---|
| `flauth invite-create --uses 1 --gb 100 --days 30 --note 小王` | 生成 1 个邀请码：可用 1 次，新用户 100GB，有效期 30 天 |
| `flauth invite-create --count 5 --gb 50 --days 30` | 一次生成 5 个 |
| `flauth invite-create --valid-days 7` | 邀请码本身 7 天内有效 |
| `flauth invite-list` | 查看邀请码和剩余次数 |
| `flauth invite-del 邀请码` | 作废一个邀请码 |
| `flauth user-list` | 查看已注册的账号 |
| `flauth user-passwd 账号` | 给某个账号重置密码 |
| `flauth user-del 账号 --marzban` | 删除账号，同时删除 Marzban 里对应的用户 |

邀请码长这样：`QWFHQ-ETM9J`，输入时不区分大小写，横线可有可无。

注册时服务会在 Marzban 里自动建同名用户（默认开通 vless、trojan、vmess、shadowsocks），流量和天数取自邀请码。续期、加流量仍然在 Marzban 面板里改。

## 五、可选配置

写在 `/etc/flclash-auth.env` 里，改完 `systemctl restart flclash-auth`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MARZBAN_PROXIES` | `vless,trojan,vmess,shadowsocks` | 新用户开通哪些协议，要和 Core 配置里的入站对应 |
| `MARZBAN_VLESS_FLOW` | `xtls-rprx-vision` | VLESS Reality 的 flow，留空则不设置 |
| `AUTH_HOST` | `::` | 监听地址，服务器不支持 IPv6 时自动改用 IPv4 |
| `AUTH_DB` | `/opt/flclash-auth/data.db` | 数据文件位置，备份这一个文件即可 |

## 接口（给 App 用）

所有请求都是 `POST`，JSON 格式。

- `/api/register`：`{"username", "password", "invite"}`
- `/api/login`：`{"username", "password"}`
- `/api/password`：`{"username", "password", "new_password"}`
- `GET /healthz`

登录和注册成功返回：`{"ok": true, "username", "subscription_url", "status", "used", "limit", "expire"}`。
失败返回：`{"ok": false, "error": "错误代码", "message": "说明"}`，常见代码有 `bad_credentials`、`invalid_invite`、`username_taken`、`too_many_attempts`、`upstream_error`。

## 本地测试

```bash
cd server && python3 -m unittest -v test_auth_server
```
