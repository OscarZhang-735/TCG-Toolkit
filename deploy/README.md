# PostgreSQL 与后端共同发布

生产地址：https://tcgtoolkit.rinne.love
前端：https://oscarzhang-735.github.io/TCG-Toolkit/
服务器：ubuntu@99.79.33.22，部署目录 `/opt/tcg-http`。

## 编排

同一份 Compose 管理 `db`、一次性 `migrate` 和 `web`。PostgreSQL 使用固定的 17 系列镜像 digest，数据库卷为 `tcg-http-postgres-data`。数据库只连接内部网络，不发布 5432 端口；Web 经原网络访问外部服务，继续绑定 `127.0.0.1:18080`。1Panel/OpenResty 继续管理域名、证书及 HTTPS。

首次初始化创建非超级用户角色和应用数据库。管理员密码仅传给 db，应用与迁移使用 `PGPASSWORD`。镜像初始化脚本仅在空数据卷上执行；修改 .env 不会修改已有角色密码。

现有 `.github/workflows/release.yml` 继续负责测试、构建、后端发布和 Pages。PostgreSQL 测试服务加入原 test job；没有数据库专用工作流或独立发布 job。后端发布脚本统一执行数据库备份、数据库启动、schema 迁移和应用验收。

## 配置

参考 `env.example`，在服务器 `/opt/tcg-http/.env` 设置：

- `PGDATABASE=tcg`、`PGUSER=tcg`、随机 `PGPASSWORD` 和另一个随机 `TCG_PG_ADMIN_PASSWORD`。
- `TCG_POSTGRES_IMAGE`：与本次发布匹配的 PostgreSQL 17 digest；大版本升级必须另行安排。
- `TCG_DB_SUBNET=10.205.0.0/24`：数据库内部网络，可按宿主机网络调整。
- 保留 `API_DOMAIN`、`TCG_PROXY_IPS`、RapidAPI、搜索代理、SMTP 参数及稳定的 `TCG_EMAIL_CODE_SECRET`。

`.env` 保持权限 600，不提交到 Git。当前代理网关为 `172.19.0.1`；保留现有 default 网络，若改变网关应同步更新信任 IP。

## 首次通过 SSH 部署

本次使用全新空库，不导入 SQLite。旧账户、会话、收藏和卡牌不会出现在新库中。先准备有效 SMTP 配置，用户重新注册后使用。

1. 本地完成测试，并将当前源码构建成 Linux amd64 镜像。将源码指纹作为 `BUILD_SHA`，bootstrap 的指纹不是 Git commit。
2. 用 `docker save` 导出镜像，通过已验证 host key 的 SSH/SCP 传到服务器，再执行 `sudo docker load -i 镜像归档`。
3. 将 deploy 目录的文件复制到新的 `/opt/tcg-http/releases/源码指纹/`。复制本地 SMTP 参数及验证码密钥到服务器 .env，保留其余生产参数，保存修改前的配置备份。
4. 查询加载后的完整镜像 ID，调用：

```sh
sudo python3 /opt/tcg-http/releases/源码指纹/deploy.py /opt/tcg-http sha256:完整镜像ID 源码指纹 --local-image
```

脚本先停止旧 Web 并复制 `/app/data` 到带时间戳的 backups，再启动独立数据库、运行迁移和启动 Web。旧 `tcg-http-data` 卷保留，不再挂载到新 Web。首次切换发现目标 PostgreSQL 已有 schema 时会中止，防止把已有库当作空库。

备份失败会恢复旧 Web；新 Web 尚未启动时的首次迁移失败也可恢复旧 Web。新 Web 启动后的跨引擎失败保持停服并报告备份位置，避免静默丢失新写入。

## 后续共同发布

保留原 GitHub 配置：production 环境、DEPLOY_SSH_KEY、DEPLOY_KNOWN_HOSTS，以及 DEPLOY_HOST、DEPLOY_USER、DEPLOY_PATH、API_BASE_URL Variables。GHCR 使用工作流临时令牌登录；提交和推送由用户执行。

每次后端发布：

1. 拉取目标应用镜像和同一 Compose 中指定的 PostgreSQL 镜像。
2. 停止 Web 写入，使用当前数据库容器的 `pg_dump -Fc` 生成备份，并保存原 release 元数据。
3. 启动/更新 db，重新运行一次性 migrate；不复用先前已完成的迁移结果。
4. 启动 Web，检查 HTTPS、版本、数据库健康、匿名拒绝及 CORS。
5. 成功后更新 current.json/previous.json，随后由原工作流发布 Pages。

发布状态记录应用镜像、PostgreSQL 镜像、数据库引擎和 schema。Alembic 迁移随应用镜像打包，当前 PostgreSQL schema 为 10。`tcg-db migrate` 为尚未登记的现有 schema 1 登记基线，再执行 `0002` 新增订阅、每日任务和待发邮件表，再执行 `0003` 增加独立通知邮箱字段、`0004` 创建收藏分组、`0005` 使收藏默认未分组（历史收藏均为未分组），随后执行 `0006` 至 `0008` 的头像、清理队列和昵称迁移，以及 `0009` 的独立订阅间隔和 `0010` 的用户统一间隔迁移，保留原有数据。发布脚本通过 `tcg-db status` 查询版本。Web 启动只校验版本，不自动建表。未配置数据库时启动失败，不回退 SQLite。

### 卡牌订阅服务

本次迁移 `0010` 将 schema 升为 10。先停止所有旧 Web/Worker，使用新镜像执行 `tcg-db migrate`，再启动新版本；迁移保留历史任务及待发邮件。所有用户统一重置为 24 小时间隔，每条订阅下一次检查为最近检查开始时间加 24 小时。用户可在用户中心的“订阅查询间隔”或通过 `PUT /api/subscriptions/settings` 的 `interval_hours` 统一设置，最低 1 小时，应用于全部已有及未来订阅；不再允许逐条设置。失败后仍按用户间隔检查。

`TCG_SUBSCRIPTIONS_ENABLED=true` 在现有 Web 进程内启用每小时到期检查和邮件重试，无需额外队列或服务。沿用 `TCG_SMTP_*` 及 `TCG_SEARCH_PROXY_URL`；通知邮件不依赖验证码 HMAC 密钥。订阅邮件默认发送到注册邮箱，也可使用用户单独验证的自定义通知邮箱；自定义地址的验证复用验证码 HMAC 密钥。

两个 Linux 部署 Compose 均只读挂载 `/etc/localtime`，按宿主机本地时间每小时整点扫描到期订阅。部署前使用 `date` 确认宿主机时区；启动日志会打印有效时区和下一次执行时间。更改宿主机时区后重新创建 Web 容器以重新挂载时区文件。根目录的 Docker Desktop Compose 则要求 `.env` 中的 `TZ` 匹配宿主机时区。

服务启动时立即扫描到期订阅，停机积压只检查一次，不逐周期补跑；待发邮件的重试会在启动后恢复。正常停机会先停止线程再关闭数据库，Compose 为 Web 预留 60 秒退出时间，已经入队的邮件保留。强制终止留下的 `running` 记录保留用于排查；已提交订阅保留新基准和下次时间，未提交且到期的订阅将在后续扫描重查。

每位用户每轮检查最多生成一封变化汇总，同一天可以多封，无变化不发。SMTP 失败后分别等待 1、5、30 分钟重试（共最多 4 次投递），耗尽后保留 `failed` 记录；SMTP 接收后进程意外中断，可能重复投递同一封邮件。任务、邮件状态及排查 SQL 见[开发文档](../docs/developer.md#卡牌订阅后端)。网页订阅入口位于收藏夹页面。

## 备份和恢复

发布备份位于 `/opt/tcg-http/backups/<时间戳>-<源码版本>/`，包含 `database.dump` 和 `release.json`。首次 SQLite 切换备份为 `data/` 目录。备份不自动删除；应另行保存到服务器外。

人工备份时，设置 current.json 中的 TCG_IMAGE，并用对应 release 的 Compose：

```sh
docker compose --env-file /opt/tcg-http/.env -f /opt/tcg-http/releases/版本/compose.yaml stop web
docker compose --env-file /opt/tcg-http/.env -f /opt/tcg-http/releases/版本/compose.yaml exec -T db sh -ec 'exec pg_dump -U "$POSTGRES_USER" -d "$APP_DB_NAME" -Fc --no-owner --no-acl' > database.dump
docker compose --env-file /opt/tcg-http/.env -f /opt/tcg-http/releases/版本/compose.yaml start web
```

不要复制运行中的 PostgreSQL 数据目录。恢复时先停止 Web，并另存当前数据库，再创建新的恢复数据库。使用 `pg_restore --exit-on-error --no-owner --no-acl --role 应用角色` 将备份恢复到新库；更改 PGDATABASE，使用匹配的应用镜像检查 schema 和健康后再开放访问。保留原库，避免覆盖尚需核对的数据。

同数据库引擎、同 schema 可运行：

```sh
sudo python3 /opt/tcg-http/releases/当前版本/deploy.py /opt/tcg-http --rollback
```

跨 schema 或 PostgreSQL → SQLite 回退必须人工选择匹配备份和旧 release。切回旧 SQLite 将不包含 PostgreSQL 阶段新增的数据；先保存 PostgreSQL 备份，再恢复旧 Compose 和发布状态。脚本拒绝自动执行这种回退。

## 验收

`/api/health` 只有在 PostgreSQL 可连接且 schema 匹配时才返回 200。SMTP 不参与数据库健康检查；首次部署另行检查邮件配置及 TLS 认证。真实投递应使用受控邮箱验收。

验证重建后的会话、收藏及卡牌持久化、验证码限额、购物车幂等和汇率刷新预算。旧 SQLite 文件、数据卷及备份的清理均须人工批准。

## SSH 数据库连接与迁移容器

数据库固定映射到服务器 `127.0.0.1:15432`，仅通过 SSH 隧道访问。客户端 SSH 配置填写服务器公网地址，数据库主机填写 `127.0.0.1`、端口 `15432`；数据库名、用户名和密码沿用现有值。无需依赖容器内网 IP。

部署脚本的数据库状态查询和迁移均使用 `docker compose run --rm`，命令结束后自动清理本次临时容器（包括失败退出）。输出保留在部署日志中；不会自动清理历史容器。
