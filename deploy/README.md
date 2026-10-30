# 当前生产部署：GitHub Pages + 1Panel HTTPS

后端：https://tcgtoolkit.rinne.love
前端：https://oscarzhang-735.github.io/TCG-Toolkit/
服务器：ubuntu@99.79.33.22，部署目录 `/opt/tcg-http`。

## 已配置

1Panel/OpenResty 管理域名、证书和 HTTPS，代理到 `http://127.0.0.1:18080`。
生产 compose.yaml 只管理后端，不启动 Caddy，不接管 80/443。
后端端口仅绑定本机；沿用 `tcg-http-data` 卷，用户、收藏和汇率数据保留。
允许的 Origin 为后端 HTTPS 域名和 GitHub Pages Origin（不含仓库路径）。
.env 保存 API_DOMAIN、TCG_PROXY_IPS 和 RAPIDAPI_KEY；代理信任目前为容器网桥网关 172.19.0.1。
若重建网络导致网关变化，应同步更新 TCG_PROXY_IPS，不能使用 *。
bootstrap 镜像及 current.json 已记录，首次 Actions 发布可以备份并回退到当前版本。
旧 compose.http.yaml 仅用于历史 HTTP 直连部署；Caddyfile 保留但不参与当前部署。

## GitHub 设置

仓库 Settings → Pages → Source：GitHub Actions。
Settings → Environments：创建 production。
Settings → Secrets and variables → Actions → Variables，添加：

| 名称 | 值 |
| --- | --- |
| API_BASE_URL | https://tcgtoolkit.rinne.love |
| DEPLOY_HOST | 99.79.33.22 |
| DEPLOY_USER | ubuntu |
| DEPLOY_PATH | /opt/tcg-http |

production environment Secrets：
- DEPLOY_SSH_KEY：允许 ubuntu 登录服务器的 SSH 私钥全文；建议后续使用专用部署密钥。
- DEPLOY_KNOWN_HOSTS：已验证的 99.79.33.22 host key 条目；可以从本机已信任的 known_hosts 提取。

工作流通过 sudo -n python3 执行服务器部署脚本，使用当前 ubuntu 已有的免密码 sudo 权限。
backend job 使用 packages:read 的临时 GITHUB_TOKEN，经 SSH 标准输入执行服务器端 `sudo docker login ghcr.io`，每次部署刷新认证，无需长期 PAT 或公开镜像。
令牌随工作流结束失效；如包未继承仓库权限，应在包设置的 Manage Actions access 中给本仓库 Read 权限。不要将 RapidAPI Key 放入前端或 GitHub Variables。
如果此前禁用了 Test and release，设置完成后重新启用。推送 main 自动运行，也可在 main 手动运行。
提交与推送由用户执行，本次未自动推送。

## 发布、备份与回滚

PR 仅测试；main 测试 → 同 SHA 静态产物和 GHCR 镜像 → 后端 → Pages。
后端通过不可变 digest 部署，生产流程串行且不取消执行中的部署。
服务器先拉取镜像，再停 web 写入并复制 /app/data 至 backups，最后更新并验证健康、HTTPS、版本、匿名拒绝及 CORS。
验收成功才部署 Pages；前端发布失败保留健康后端，可重跑失败 job。

同数据库 schema 的更新失败会自动恢复上一镜像；schema 发生变化时停止 web，报告备份位置供人工处理。
手动回滚：

```sh
sudo python3 /opt/tcg-http/releases/当前SHA/deploy.py /opt/tcg-http --rollback
```

跨 schema 回滚须先停服、另存失败后的数据库，再经运维确认恢复匹配的旧备份，保持文件权限。
不要删除数据卷。限制部署目录权限并将数据库备份另存到服务器外；备份保留期限由运维管理。
当前数据库不自动与本地同步；首次迁移需停本地 web，`docker compose cp web:/app/data ./backup-data`，
安全传输后在远端停服、备份原卷，再恢复数据。浏览器历史和偏好不在数据库中。

## 认证与测试边界

邮箱注册版本使用用户数据库 schema v8。发布前在服务器 `/opt/tcg-http/.env` 配置
`TCG_SMTP_HOST`、`TCG_SMTP_PORT`（可留空）、`TCG_SMTP_TLS`（默认 `starttls`）、
`TCG_SMTP_USERNAME`、`TCG_SMTP_PASSWORD`、`TCG_SMTP_FROM` 和 `TCG_EMAIL_CODE_SECRET`。
参数说明和密钥生成见 [开发文档](../docs/developer.md#邮箱注册与验证)。SMTP 及验证码密钥只保存在服务端。
未配置时老账户继续可用，邮件注册和绑定返回 503，不视为健康检查失败。

部署沿用备份 → 后端 → Pages 顺序。v8 在 v7 凭据清理成功后迁移，保留用户、会话、收藏和卡牌数据。
老账户无需立即补绑；旧页面的无邮箱注册请求会返回 422，刷新后使用新表单。
v8 升级后不能直接运行只支持 v7 的镜像；跨 schema 回滚须使用上面的人工恢复流程。
上线后用受控测试邮箱验收真实 SMTP 投递、验证码注册、邮箱登录及老账户补绑；自动化测试仅模拟邮件传输。

Bearer 令牌存 localStorage，服务端保存摘要，7 天过期，退出撤销并同步其他标签页。
旧 Cookie 不再认证，更新后的旧页面需刷新并重新登录。
同一 GitHub 用户下的 Pages 仓库共享 Origin；仓库路径不能隔离 localStorage。
CORS 支持 GET/POST/DELETE/OPTIONS 和 Authorization、Content-Type，不使用跨站 Cookie。
后续 API 更新应先扩展后端，再发布前端，最后移除旧接口，以兼容缓存中的旧页面。
当前 HTTPS 和两个 Origin 已验证；GHCR 拉取和完整 Actions/Pages 发布需要完成上述 GitHub 配置后验收。
