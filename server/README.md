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
MARZBAN_URL=https://localhost:8000
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

说明：`MARZBAN_INSECURE=1` 只用于本机之间的连接（证书是按域名签的，用 localhost 访问会不匹配），不影响对外的 HTTPS。写 `localhost` 而不是 `127.0.0.1`，是因为面板可能只监听 IPv6，`localhost` 会自动两种都试。

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

账号名也不区分大小写：注册时统一存成小写，`Alice` 和 `alice` 是同一个账号。

注册时服务会在 Marzban 里自动建同名用户（默认开通 vless、trojan、vmess、shadowsocks），流量和天数取自邀请码。续期、加流量仍然在 Marzban 面板里改。

## 五、可选配置

写在 `/etc/flclash-auth.env` 里，改完 `systemctl restart flclash-auth`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MARZBAN_PROXIES` | `vless,trojan,vmess,shadowsocks` | 想给新用户开通哪些协议。服务会先问面板哪些协议真有入站，只开通有的；面板上没有的会自动跳过，并在日志里提示 |
| `MARZBAN_VLESS_FLOW` | `xtls-rprx-vision` | VLESS Reality 的 flow，留空则不设置 |
| `AUTH_HOST` | `::` | 监听地址，服务器不支持 IPv6 时自动改用 IPv4 |
| `AUTH_DB` | `/opt/flclash-auth/data.db` | 数据文件位置，备份这一个文件即可 |

## 出问题时怎么查

先看日志，里面有面板返回的真实原因：

```bash
journalctl -u flclash-auth -n 40 --no-pager
```

再对照 App 里弹出的提示：

| App 提示 | 说明 | 怎么查 |
|---|---|---|
| 无法连接服务器，请检查网络后重试 | App 连不到登录服务本身 | 在电脑上执行 `curl https://你的域名:9000/healthz`，要返回 `{"ok": true}`。不通就查：服务是否在运行（`systemctl status flclash-auth`）、`ufw` 和 CloudCone 控制台是否放行 9000、域名是否有 A 记录（没有 IPv6 的用户需要）、Cloudflare 是否开了橙色云朵（要关） |
| 服务暂时不可用，请稍后再试 | 登录服务连不上 Marzban，或 Marzban 拒绝了请求 | 看上面的日志。常见原因：管理员账号或密码写错、`MARZBAN_URL` 不对、面板里没有任何可用协议的入站 |
| 登录失败，请稍后再试 | 服务返回的内容 App 看不懂 | 多半是 `BRANDING_API_BASE` 填的地址不是登录服务（比如指到了面板的 8000 端口，或被网关返回了网页） |
| 账号或密码错误 / 邀请码无效、已用完或已过期 | 正常的业务提示 | 检查输入；`flauth invite-list` 看邀请码 |
| 输错次数太多，请 15 分钟后再试 | 同一账号连续输错 5 次被锁定 | 等 15 分钟，或 `flauth user-passwd 账号` 重置密码 |

App 里**完全没有登录入口**：说明打包时没有配 `BRANDING_API_BASE`，或者装的是配置之前打出来的旧包；另外只有在 App 里还没有任何订阅时才会弹出，已有订阅的话要先删掉。

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
