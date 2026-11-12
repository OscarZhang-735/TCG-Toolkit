# TCG Toolkit 开发与运维说明

面向开发者和自行部署的维护者。普通用户请看项目根目录的 [README](../README.md)。

## 运行与配置

### Docker 运维

常用命令：

```powershell
docker compose ps              # 查看状态
docker compose logs -f web     # 查看日志
docker compose stop            # 停止
docker compose start           # 再次启动
docker compose up -d --build   # 更新代码后重建
```

用户、会话、收藏、卡牌、验证码、购物车幂等记录和汇率缓存统一保存在 PostgreSQL 的 `postgres-data` 数据卷。
`db → migrate → web` 依次启动，迁移失败时 Web 不启动。Web 不再挂载 SQLite 卷。备份使用 `pg_dump`，恢复流程见[部署手册](../deploy/README.md)。
升级现有本地安装时，先停止旧 Web、复制 `/app/data` 备份并保留旧卷，再配置 PostgreSQL 密码和启动新版本。此次切换不导入旧数据。

如果 Windows 占用了 8000 端口，在 `.env` 中设置 `TCG_PORT=18080`，并同步把 `TCG_TRUSTED_ORIGINS`
改为 `http://localhost:18080`，然后访问 http://localhost:18080 。

### 直接运行 Python

需要 Python 3.11 或更新版本，以及可连接的 PostgreSQL 17。先在进程环境设置 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`；直接运行时 `PGHOST` 应为实际数据库地址。

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -e ".[web]"
Copy-Item .env.example .env
python -m tcg_toolkit.db_cli migrate
uvicorn tcg_toolkit.web:app --app-dir src --port 8000 --env-file .env
```

打开 http://localhost:8000 。应用启动只检查 schema，不会自动建表；迁移命令从进程环境读取 PostgreSQL 连接信息。`uvicorn --env-file` 仅影响 Web 进程。

### 配置项

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TCG_TRUSTED_ORIGINS` | 允许登录的访问地址，多个用逗号分隔，不含路径 | 空（不允许登录） |
| `TCG_PORT` | Docker 映射到本机的端口 | `8000` |
| `RAPIDAPI_KEY` | 汇率接口密钥；不填时只能显示日元 | 空 |
| `TCG_SEARCH_PROXY_URL` | 游游亭转发接口（搜索、商品详情、凭据验证和购物车）；显式设为空可直连 | `http://62.234.212.90:8080/encrypted-proxy` |
| `PGHOST` / `PGPORT` | PostgreSQL 地址；Compose 内使用服务名 | `db` / `5432`（Compose） |
| `PGDATABASE` / `PGUSER` / `PGPASSWORD` | 应用数据库、非超级用户角色及密码 | Compose 默认库和角色为 `tcg`；密码必填 |
| `TCG_PG_ADMIN_PASSWORD` | PostgreSQL 初始化管理员密码，仅传给 db | 必填 |
| `TCG_POSTGRES_IMAGE` | 随后端一起发布的 PostgreSQL 镜像 | 固定 PostgreSQL 17 digest |
| `TCG_DB_SUBNET` | 内部数据库网络 | `10.205.0.0/24` |
| `TCG_PROXY_IPS` | 受信任的反向代理来源 IP，不要设为 `*` | `127.0.0.1` |
| `TCG_SUBNET` | Docker 网络子网，与本地网络冲突时修改 | `10.203.0.0/24` |
| `TCG_SUBSCRIPTIONS_ENABLED` | 启动卡牌订阅每小时到期扫描及邮件重试线程 | 直接运行关闭；Compose 开启 |
| `TCG_SITE_URL` | 订阅邮件底部“前往 TCG Toolkit 查看”链接使用的网页地址（自动追加 `#favorites`）；须为 http(s) 地址，未设置或无效时使用公开站点 | `https://oscarzhang-735.github.io/TCG-Toolkit/` |
| `TZ` | 根目录 Compose 必填，设为宿主机 IANA 时区，例如 `America/Toronto`；Linux 生产部署使用挂载的宿主机 `/etc/localtime` | `.env.example` 示例为 `America/Toronto` |

`.env` 已被 Git 忽略，请勿提交密钥。公网部署请参阅 [部署手册](../deploy/README.md)。

### 测试

```powershell
docker compose -f compose.test.yaml up -d --wait
$env:PGHOST="127.0.0.1"
$env:PGPORT="15433"
$env:PGDATABASE="tcg_test"
$env:PGUSER="tcg_test"
$env:PGPASSWORD="local-test-only"
python -m pytest -q tests
node --test (Get-ChildItem tests/*.test.mjs).FullName
```

测试使用真实 PostgreSQL 和每项独立 schema，要求库名以 `_test` 结尾。测试结束释放连接并清理自己创建的 schema；数据库卷不自动删除。
GitHub Actions 在现有 test job 内启动 PostgreSQL service，不增加数据库发布工作流。

### ORM 与事务

持久化实体统一定义在 `src/tcg_toolkit/db_models.py`，与搜索结果及 API 输入模型分开。
业务代码使用 SQLAlchemy 2 的 `select()`、实体属性和 PostgreSQL upsert 表达式，不拼接 SQL，也不使用 `text()` 接收用户输入。

`Database.session()` 为每次操作创建独立 Session：正常退出提交，异常退出回滚并释放连接。Session 不跨线程共享。
连接池最多 4 个连接、不额外溢出，并在取连接时检查可用性；数据库错误隐藏 SQL 参数。

```python
from sqlalchemy import select
from tcg_toolkit.db_models import OwnedCard

with store.session() as session:
    card = session.scalar(select(OwnedCard).where(
        OwnedCard.id == card_id, OwnedCard.user_id == user_id
    ).with_for_update())
    if card is not None:
        card.notes = notes
```

用户数据查询必须包含用户范围。锁定后的实体在同一事务内修改；唯一冲突使用 `begin_nested()` 保存点及显式 `flush()`，
使冲突查询可以继续执行。注册用户名/邮箱冲突映射为原有 409，其他约束错误继续抛出并回滚。
验证码失败次数需要提交后再返回错误；SMTP 和上游 HTTP 请求在事务之外进行。
购物车幂等预占和汇率刷新预算使用原子 upsert，不能替换为先查后插。
API 响应继续显式选择公开字段，不直接序列化 ORM 实体，避免带出密码或令牌摘要。

### Alembic 迁移

`tcg-db migrate` 使用 Alembic，并在同一个 PostgreSQL 事务锁和事务内执行接管、升级及版本校验。
已有 PostgreSQL schema 1 首次运行时先登记 Alembic 基线 `0001`（如尚未登记），再执行 `0002` 新增订阅、每日任务和待发邮件表，再执行 `0003` 增加独立通知邮箱字段，再执行 `0004` 创建收藏分组表、`0005` 取消默认组并使收藏默认未分组，随后执行 `0006` 至 `0008` 的头像、清理队列和昵称迁移，以及 `0009` 的独立订阅间隔和 `0010` 的用户统一间隔迁移，升级到 schema 10，保留用户、会话和业务数据。
新空库通过基线迁移初始化。原 `001_initial.sql` 保持不可变，仅供基线使用；业务代码不再执行 SQL 字符串。
`schema_migrations` 保留为发布版本兼容标记，现有 JSON 状态接口和回退判断继续可用。

修改实体后，在开发数据库上生成、审阅迁移：

```powershell
alembic revision --autogenerate -m "describe schema change"
python -m tcg_toolkit.db_cli migrate
python -m tcg_toolkit.db_cli check
```

新迁移使用 Alembic `op` 和 SQLAlchemy 类型/表达式；审阅自动生成结果，尤其是约束及数据转换。
实际 schema 变化时同步增加 `db_cli.SCHEMA_VERSION`，在迁移中写入对应 `SchemaMigration` 兼容标记。
部署仍通过 `tcg-db migrate` 执行，不能用 `Base.metadata.create_all()` 或直接 `alembic upgrade` 绕过发布锁和兼容校验。
`tcg-db check` 检查已应用版本及 ORM 元数据差异；Web 启动只检查版本，不自动建表。

## 收藏分组后端

收藏夹提供单层可选分组：收藏默认未分组（`group_id` 为 `null`），也可加入一个用户新建的分组。没有默认组，注册时不创建分组。分组可以创建、改名和删除，删除时在同一事务内将其中收藏改为未分组，保留收藏 ID、快照、创建／刷新时间及独立订阅。

WebUI 收藏页顶部为分组栏：“全部”及各分组（附数量），可新建分组，选中分组后可改名或删除；未分组收藏只在“全部”中出现。页面加载全部收藏后在前端按组筛选，“刷新全部”只刷新当前所选分组。存在分组时，每张收藏卡牌提供“移动到分组”按钮，可选择某个分组或“未分组”；卡片右下角及列表“分组”列显示所在分组名。搜索页新增的收藏为未分组。接口需要 Bearer 登录，写接口沿用可信 `Origin` 和 JSON 检查，响应均为 `Cache-Control: no-store`。

| 接口 | 请求与响应 |
| --- | --- |
| `GET /api/favorites/groups` | 返回 `{"items":[分组记录,…]}`；按创建时间、ID 升序 |
| `POST /api/favorites/groups` | `{"name":"待购"}`；返回 201 和分组记录 |
| `PUT /api/favorites/groups/{group_id}` | `{"name":"新名称"}`；返回 200 和更新后的分组记录 |
| `DELETE /api/favorites/groups/{group_id}` | JSON `{}`；将其中收藏改为未分组后删除分组，返回 204 |
| `GET /api/favorites?group_id=…` | 返回该组的 `{"items":[收藏记录,…]}`；不传参数时仍返回全部收藏，保留创建时间、内部排序号倒序 |
| `POST /api/favorites` | 原卡牌快照字段加可选 `group_id`；省略或 `null` 表示未分组；返回 200 和收藏记录 |
| `PUT /api/favorites/{favorite_id}/group` | `{"group_id":"目标组 ID"}`，`null` 表示移出分组；返回 200 和移动后的收藏记录 |

分组记录为 `id`、`name`、`created_at`。收藏记录为 `id`、`group_id`（未分组为 `null`）、`item`、`created_at`、`refreshed_at`；`item` 保持原卡牌快照结构，不包含 `group_id`。时间为 Unix 秒。重复收藏返回原记录，即使指定不同分组也不会隐式移动或覆盖快照；移动使用专用 PUT 接口。原取消收藏和刷新接口继续适用。

名称去除首尾空白后须为 1–100 个字符。同一用户内名称唯一、区分大小写。无效输入返回 422；名称冲突返回 409 `favorite_group_name_taken`。不存在或属于其他用户的分组统一返回 404 `favorite_group_not_found`，收藏仍使用 404 `favorite_not_found`。

分组写入、新增收藏和移动收藏先锁定用户行，再操作分组及收藏。数据库通过复合外键限制收藏只能引用同用户的分组。

部署前执行 `tcg-db migrate`：迁移 `0004` 创建分组表和外键；`0005` 将 `group_id` 改为可空，把原默认组中的收藏改为未分组，删除默认组及 `is_default` 列，保留自定义组及其收藏，更新到 schema 5。历史收藏升级后均为未分组。回退沿用恢复匹配备份的流程。

## 邮箱注册与验证

新用户必须验证邮箱才能创建账户；用户名和密码规则保持不变。注册成功仍返回 `{user, token}` 并自动登录。
登录请求的 `username` 字段接受用户名或已验证邮箱。`user` 和 `/api/auth/me` 增加 `email`、`email_verified`；
老账户分别为 `null`、`false`，可正常登录及在用户中心补绑。当前不提供更换邮箱或免密登录。

### SMTP 配置

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TCG_SMTP_HOST` | SMTP 主机 | 空 |
| `TCG_SMTP_PORT` | SMTP 端口 | 留空时 STARTTLS 为 587、SSL 为 465 |
| `TCG_SMTP_TLS` | `starttls` 或 `ssl`，均校验证书；不支持明文回退 | `starttls` |
| `TCG_SMTP_USERNAME` | SMTP 登录名 | 空 |
| `TCG_SMTP_PASSWORD` | SMTP 密码或服务商授权码，与登录名一起设置；受信任的免认证中继可同时留空 | 空 |
| `TCG_SMTP_FROM` | 发件地址，例如 `TCG Toolkit <noreply@example.com>` | 空 |
| `TCG_EMAIL_CODE_SECRET` | 持久保存的随机 HMAC 密钥，至少 32 字节 | 空 |

用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成密钥并保存到服务端 `.env`，不要每次启动重新生成。
更换密钥会使已发送验证码失效，但不会注销既有会话。发件地址需符合所用 SMTP 服务的发信权限。
配置由启动时读取，修改后重启服务。本地和部署 Compose 均传入这些变量，不进入前端静态产物。
主机、发件人、TLS、端口或密钥缺失／无效时，邮件操作返回 `503 email_unavailable`；既有账户登录不受影响。
SMTP 连接和 socket 操作超时为 10 秒，发送失败返回 `503 email_send_failed`，不自动重发。

### API 流程

所有写入要求受信任 Origin 和 JSON；所有认证响应均带 `Cache-Control: no-store`。

| 接口 | JSON 请求 | 成功响应 |
| --- | --- | --- |
| `POST /api/auth/register/email-code` | `email`，可选 `language`（`en` 或 `zh-CN`，默认 `en`） | `challenge_id, expires_in, retry_after`（秒） |
| `POST /api/auth/register` | `username, password, confirm_password, email, challenge_id, code` | `201 {user, token}` |
| `POST /api/auth/email/bind-code` | `email, current_password`，可选 `language`；需 Bearer | `challenge_id, expires_in, retry_after` |
| `POST /api/auth/email/bind` | `challenge_id, code`；需发起验证码的账户 Bearer | 更新后的 `user` |
| `POST /api/auth/password/change/email-code` | 可选 `language`；需 Bearer，只向当前账户已验证邮箱发送 | `challenge_id, expires_in, retry_after` |
| `POST /api/auth/password/change` | `method, new_password, confirm_password`；`method=password` 时提供 `current_password`，`method=email` 时提供 `challenge_id, code`；需 Bearer | `204`，所有会话撤销 |
| `POST /api/auth/password/reset/email-code` | `email`，可选 `language`；无需登录 | 相同发送响应结构；未知或未验证邮箱不发信 |
| `POST /api/auth/password/reset` | `email, challenge_id, code, new_password, confirm_password`；无需登录 | `204`，所有会话撤销 |

`code` 是保留前导零的六位数字字符串；验证码 10 分钟有效，最多错误 5 次，成功后不可重用。
邮箱去除首尾空白、规范化后转为小写，不折叠点号或 `+` 别名，不执行 DNS 查询。
同邮箱各用途共享 60 秒发送间隔及每小时 5 次配额（发送失败也计入）；配额和验证码摘要持久化。
IP 限制为各发送接口每分钟 5 次、注册／绑定验证及登录接口每分钟 10 次；IP 限制为现有进程内实现。
`429` 的 `Retry-After` 表示需等待秒数，CORS 向跨域前端开放此响应头。

错误码包括 `username_taken`、`email_taken`、`email_already_bound`（409）；
`invalid_code`、`code_expired`、`code_attempts_exhausted`、`invalid_password`（400）；
`validation_error`（422）、`rate_limited`（429）及上述邮件错误（503）。
邮箱改密要求已验证邮箱，否则返回 `400 verified_email_required`。旧密码改密不依赖 SMTP 配置。
用户名或邮箱冲突不消耗有效验证码；成功重发使同邮箱、用途和绑定账户的旧码失效，发送失败保留旧码。
验证记录绑定用途和账户，注册／绑定与验证码消费在同一事务完成。响应不回显敏感输入；不要在代理或日志中记录认证请求体。

新密码仍为 8–18 个字符，必须与确认密码一致。邮箱改密和忘记密码分别使用 `password_change`、`password_reset` 用途，
不能混用，也不能用注册或绑定验证码改密。忘记密码的发码成功提示保持统一，不直接说明邮箱是否存在；
未注册或未验证邮箱获得不可使用的随机 challenge ID，不保存记录、不发送邮件。
改密事务同时更新 Argon2id 摘要、消费验证码、撤销全部会话，并作废该用户其余待发送或有效验证码。
登录的密码校验和会话创建也在写事务内完成，避免并发登录在改密后留下旧密码会话。
验证码邮件晚到时不会重新激活已作废记录。前端改密成功清除本地会话，返回登录页，并提示使用新密码登录。
当前使用 PostgreSQL schema 10。业务接口保留现有 SMTP 配置；旧 SQLite schema v8 不再由应用读取。

自动化测试注入内存发信器，仍经过真实验证码校验；没有生产环境绕过开关。
真实投递验收：配置 SMTP 后，向自己的测试邮箱发送验证码，确认邮件语言、到达时间、验证码注册和邮箱登录，再确认重发旧码失效。

## Shadowverse Evolve 搜索器（Python）

`SEVSearcher` 使用 `/sell/sev/s/search?search_word=`，复用现有搜索结果结构和分页方法。
已接入网页搜索、收藏刷新、卡牌库和游游亭购物车；网页使用 SEV 标识，卡牌库查询使用日版官网 `https://shadowverse-evolve.com/cardlist/?cardno=`，按完整卡号精确匹配（如 `CP04-021`）。CLI 暂未接入。

日版 `SEV` 与英文版 `SEV EN` 是独立卡牌库品牌。同卡号、同稀有度可以分别保存，数量与游戏筛选互不合并。
日版稀有度映射键为 `sev`，常规等级为 `BR/SR/GR/LG`，终极稀有为 `UR`；英文版映射键为 `sev_en`（也接受 `SEV EN`），对应 `B/S/G/L` 和 `U`。
英文版卡牌库通过 `https://en.shadowverse-evolve.com/cards/?cardno=` 精确查询完整卡号，核对详情页卡号后填入名称、稀有度和图片；不接入日版价格搜索、收藏刷新或购物车。已有 `SEV` 数据保持原样；需要改为英文版的记录可在卡牌库中手动修改游戏。

```python
from tcg_toolkit import SEVSearcher

with SEVSearcher() as searcher:
    page = searcher.search_page("BP01")
    print(page.to_dict())
```

## 游游亭购物车接口与实现

### API 概览

所有接口需要本站登录返回的 `Authorization: Bearer <token>`。写入还需要
`Origin` 与 `TCG_TRUSTED_ORIGINS` 中某个地址完全一致，以及 `Content-Type: application/json`。
接口按本站当前登录用户隔离，不接收用户 ID；所有响应均为 `Cache-Control: no-store`。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/yuyutei/cart` | 使用本次请求的凭据，向游游亭购物车增加指定数量 |
| `POST /api/yuyutei/cart/query` | 读取当前用户的游游亭购物车，返回商品明细、总张数和商品小计 |
| `DELETE /api/yuyutei/cart` | 按商品标识定位最新购物车行并移除整条记录，随后核对结果 |
| `PUT /api/yuyutei/cart/quantity` | 将某条购物车记录调低到指定数量（WebUI 的 `−`） |
| `GET /api/yuyutei/product?gid=&ver=&cid=` | 读取商品页的印刷编号和稀有度（不需要游游亭凭据，经搜索转发接口获取） |

### 浏览器端凭据

默认使用 `sessionStorage`，由 `static/yuyutei-client.js` 提供独立客户端模块。
已有登录生命周期负责激活用户、退出/会话过期时清除、切换账户时清除旧凭据并取消或忽略旧请求；
同一账户刷新页面可继续使用当前标签页中的凭据。不写入 `localStorage`，不提供持久化回退。
浏览器禁用 sessionStorage 时保存操作会明确失败。sessionStorage 可被本站 JavaScript 读取，不是加密保险箱。

用户中心的配置界面和搜索页的加购控件都使用该模块。开发调用接口：

```javascript
import {yuyuteiClient} from './yuyutei-client.js';
// 正常页面由 auth.js 完成 activateUser(user.id, authenticatedRequest)。
yuyuteiClient.saveCredentials({cookie: '<Cookie 值>', csrf_token: '<同一会话 Token>'});
const cart = await yuyuteiClient.query();
const key = crypto.randomUUID(); // 同一次加购重试时保留此键
await yuyuteiClient.add({gid: 56, ver: 'OS01', cid: '10044', counter: 1}, key);
await yuyuteiClient.remove({gid: 56, ver: 'OS01', cid: '10044', kizu: 0, type: 'normal'});
await yuyuteiClient.setQuantity({gid: 56, ver: 'OS01', cid: '10044', kizu: 0, type: 'normal'}, 2);
yuyuteiClient.clearCredentials();
```

客户端模块的加购方法要求调用方提供幂等键，不自动重试。可使用 `createYuyuteiClient` 注入存储和请求函数进行独立测试。
所有购物车请求通过 JSON 的 `credentials` 字段显式提交凭据；本站登录仍使用 Authorization Bearer。
后端只在该请求生命周期内使用凭据，不保存、不回显，不从本站 Cookie 或 URL 查询参数回退读取。
校验失败统一返回 `invalid_yuyutei_request`，不回显凭据、原始请求体或无关字段。

`credentials.cookie` 可填写完整 Cookie 请求头值，或仅填写 `laravel_session` 的原始值。
后端只提取并发送 `laravel_session`；不要对 `%3D` 等转义进行解码。
`credentials.csrf_token` 使用对应游游亭会话页面的 `<meta name="csrf-token">` 值。
可在该页面控制台运行 `document.querySelector('meta[name="csrf-token"]').content` 获取（通常 40 个字符）。
Cookie 中的 `XSRF-TOKEN` 是 Laravel 加密后的值，与该 Token 不同，不能代替使用。
使用 HTTPS 传输，并避免在代理、调试或访问日志中记录请求体。

旧 `/api/yuyutei/credentials` 接口和 `GET /api/yuyutei/cart` 已移除。查询改为：

```http
POST /api/yuyutei/cart/query
Authorization: Bearer <本站登录 token>
Origin: http://localhost:8000
Content-Type: application/json

{"credentials":{"cookie":"<Cookie 值>","csrf_token":"<Token>"}}
```

加购请求示例：

```http
POST /api/yuyutei/cart
Authorization: Bearer <本站登录 token>
Origin: http://localhost:8000
Content-Type: application/json
Idempotency-Key: <为本次加购生成的唯一标识，例如 UUID>

{"gid":56,"ver":"OS01","cid":"10044","counter":1,"kizu":0,"credentials":{"cookie":"<Cookie 值>","csrf_token":"<Token>"}}
```

- `gid` 为游戏 ID：WS=7、ChaosTCG=8、PM=11、Lycee=34、WSR=56。
- `ver`、`cid` 分别对应游游亭商品页面的 `cart_ver`、`cart_cid`，不是印刷卡号。`cid` 使用数字字符串。
- `counter` 为本次增加的张数，默认 1，允许 1–99 的整数；库存与购买限制由游游亭判断。
- `kizu` 对应商品的 `cart_kizu`，允许 0 或 1，默认 0。
- 服务端固定 `mode=add`、`type=sell`，生成当前 UTC `time` 及商品 Referer；不能传入自定义地址或更改操作。
- 凭据直接发送至 `https://yuyu-tei.jp/api/cart_order_edit`，不经过搜索代理，不跟随重定向。

服务端在会话锁内先读取数量，再发送一次加购，最后重新读取数量。
只有上游返回 `SUCCESS` 且观察到的增量等于请求量，才返回 HTTP 200：

```json
{"status":"SUCCESS","gid":56,"ver":"OS01","cid":"10044","counter":1,"kizu":0,"requested_quantity":1,"before_quantity":0,"after_quantity":1,"observed_delta":1,"verification":"verified"}
```

`counter` / `requested_quantity` 是本次请求量；`before_quantity`、`after_quantity` 为目标商品的前后数量，
`observed_delta` 为差值。核验按 `gid + ver + cid + kizu` 汇总普通与预订记录，不要求页面存在删除按钮。
增量不符返回 409 `yuyutei_quantity_mismatch`、`verification: mismatch` 和前后数量，不擅自归因于库存不足。
上游已成功但后续查询失败时，返回对应错误码、`upstream_status: SUCCESS`、`verification: unknown`、
`outcome: unknown`、`after_quantity: null`，不自动重试；加购前无法完整读取购物车时返回 `outcome: not_sent`，不发送 POST。
核验只能证明观察到的数量变化，外部客户端同时操作仍可能影响差值。接口只负责加购，不提交订单或付款。

错误返回在 `detail` 中：

| HTTP | `detail` | 处理方式 |
| --- | --- | --- |
| 401 | `authentication_required` | 重新登录本站 |
| 403 / 415 | `untrusted_origin` / `json_required` | 检查 Origin 或 JSON Content-Type |
| 422 | `invalid_yuyutei_request` 或 `invalid_idempotency_key` | 修正输入；不会回显提交内容 |
| 409 | `type: yuyutei_credentials_rejected` | 上游返回 401/403/419，检查会话凭据或站点访问限制 |
| 409 | `type: yuyutei_cart_rejected`，附 `code: ERROR1` 等 | 上游业务拒绝；错误码不自行解释为库存不足 |
| 502 / 504 | `type: yuyutei_unavailable / yuyutei_invalid_response / yuyutei_timeout`，`outcome: unknown` | 加购结果不确定，先检查游游亭购物车 |

每个请求最多发送一次上游 POST，不自动重试。

### 加购幂等控制

建议每次独立加购生成一个 `Idempotency-Key`，重试同一次操作时复用它。允许 1–128 位字母、数字及 `_.:-`。
此请求头可选以兼容已有调用；不提供时，每次调用均视为独立加购，不受重复提交保护。

- 键按本站登录用户隔离；服务端在接触上游前将占用记录提交到 PostgreSQL，原子地防止同键并发执行。
- 同键、相同规范化参数（包含默认值）返回已保存的 HTTP 状态和响应体，并附 `Idempotency-Replayed: true`；不会再次访问上游。
- 同键不同参数返回 409 `idempotency_key_conflict`；处理中返回 409 `cart_operation_pending`、`outcome: unknown`。
- 成功、业务拒绝、超时、核验结果未知均保存并重放。重放是该次操作的历史结果，不是购物车当前状态。
- 重启后记录仍有效；若进程在占用后、结果持久化前崩溃，保留待核对状态，不自动过期或重发上游请求。
- 凭据不参与幂等摘要；每次请求仍需携带格式有效的凭据，即使凭据变化，同键也仍对应原操作。结果未知时先查询真实购物车核对，不要换新键直接重试。

当前没有自动清理幂等记录或解除待核对状态的接口。无需幂等重放的新操作应使用新键。
记录保存在版本 6 引入的 `cart_operations` 表中，仅保存请求摘要和结果，不包含 Cookie 或 CSRF Token。

### 查询与移除购物车商品

`POST /api/yuyutei/cart/query` 使用本次 JSON 提交的会话读取游游亭 `/cart/sell`，返回：

```json
{"items":[{"gid":56,"game":"wsr","ver":"OS01","cid":"10044","kizu":0,"type":"normal","name":"C 甘え上手 七海","quantity":3,"unit_price":30,"currency":"JPY","product_url":"https://yuyu-tei.jp/sell/wsr/card/OS01/10044","removable":true}],"total_quantity":3,"subtotal":90,"currency":"JPY","complete":true,"warnings":[]}
```

`image_url` 为购物车行中的缩略图（仅 https，缺失时为 `null`）。`quantity` 为当前购物车中该商品的数量；`subtotal` 为商品单价乘数量的总和，不含运费或结算调整。
空购物车返回 `items: []`、`total_quantity: 0`、`subtotal: 0`、`complete: true`。
查询不依赖删除按钮；按钮缺失时商品仍可展示，`removable: false`。
单条商品无法解析时保留其他商品，返回 `complete: false`、`warnings: [{"row_index": 0, "code": "unrecognized_cart_item"}]`，
并将 `total_quantity` 和 `subtotal` 设为 `null`。`row_index` 从 0 开始，只用于定位本次页面的异常行。
所有行均无法解析时也不冒充空购物车。整个页面结构或会话校验不通过时仍返回错误。
加购数量核验和删除继续使用严格解析，不使用不完整查询结果推断商品不存在；删除还要求有效的删除按钮参数。
每次读取还会将页面的 CSRF Token 与本次提交的 Token 比对：不一致返回 409 `yuyutei_credentials_rejected`，
缺失或重复的 Token 返回 502。不会自动接受失效 Cookie 产生的新匿名会话，也不会用其空购物车证明删除成功。
Token 发生变化时需要更新浏览器中该会话的 Cookie 和 Token。

移除商品：

```http
DELETE /api/yuyutei/cart
Authorization: Bearer <本站登录 token>
Origin: http://localhost:8000
Content-Type: application/json

{"gid":56,"ver":"OS01","cid":"10044","kizu":0,"type":"normal","credentials":{"cookie":"<Cookie 值>","csrf_token":"<Token>"}}
```

`kizu` 默认为 0，`type` 默认为 `normal`，预订商品使用 `reserved`；可直接使用查询结果中的这些标识。
该操作移除整条记录的全部数量，不接收 `counter` 或 `CART_ITEM_ID`。
后端先读取最新页面，按 `gid + ver + cid + kizu + type` 唯一匹配商品，从该行读取实际删除标识，
然后调用 `/api/cart_service`（`mode=remove`、`status=sell`）。确认上游响应结构有效后，重新读取购物车；
目标已不存在才返回 `status: SUCCESS`、`removed`、`removed_quantity` 和最新的 `cart`。

未匹配到目标返回 404 `yuyutei_cart_item_not_found`；多条匹配返回 409 `yuyutei_cart_item_ambiguous`，均不发送删除请求。
删除后仍找到目标返回 409 `yuyutei_removal_not_confirmed`。上游超时、页面解析失败等错误沿用上表，
删除请求发送后发生错误时结果可能不确定，应查询购物车后再决定，不自动重试。
删除后的核验若发现会话失效，返回 409 `yuyutei_credentials_rejected` 并标记 `outcome: unknown`，不宣称删除成功。
当前单进程部署按游游亭会话的 CSRF Token 摘要串行处理增、删、查，锁覆盖定位、删除和结果核验全过程；
两个本站账户配置同一游游亭会话也共用锁，避免本站请求交错使用行标识。不同会话可并行，操作结束后释放锁记录。
该锁不跨进程或服务实例，也无法约束游游亭网页上的操作；执行删除期间应避免同时在其他客户端修改同一购物车。
如改为多进程或多实例部署，需要共享的会话锁。上游没有提供已验证的原子条件删除接口，本地锁不能消除外部修改的竞态。

新 PostgreSQL schema 不创建 `yuyutei_credentials` 表，不持久化第三方 Cookie 或 CSRF token。此次使用空库，不复制 SQLite 数据或执行旧版凭据清理。
保留账户、会话、收藏、个人卡牌和 `cart_operations` 幂等记录。新数据库不创建凭据表。
历史外部备份、数据库副本不会被自动修改，已泄露或曾存入不受信任备份的游游亭会话需要自行作废。

### 调低购物车数量

游游亭只提供整行移除，因此调低数量通过「移除整行 → 重新加购目标数量」完成：

```http
PUT /api/yuyutei/cart/quantity
Authorization: Bearer <本站登录 token>
Origin: http://localhost:8000
Content-Type: application/json

{"gid":56,"ver":"OS01","cid":"10044","kizu":0,"type":"normal","quantity":2,"credentials":{"cookie":"<Cookie 值>","csrf_token":"<Token>"}}
```

购物车三个写接口成功时统一提供 `cart`，结构与查询接口一致，来自本次操作核验的购物车快照；同时保留各操作原有的结果字段。加购的幂等重放返回原操作保存的快照，不代表重放时刻的购物车状态；需要刷新时调用查询接口。历史幂等记录仍按原响应重放，可能不含 `cart`。

- `quantity` 是目标数量（0–98），不是差值，因此重复提交同一请求不会多减；不需要 `Idempotency-Key`。
- 目标等于当前数量时直接返回成功、不访问上游写接口；大于当前数量返回 409 `yuyutei_quantity_increase_unsupported`（加购请使用 `POST /api/yuyutei/cart`）。
- `quantity: 0` 等同于移除整行；若目标记录已不存在，返回成功、前后数量均为 0 及当前购物车，不发送上游写请求。普通 DELETE 在记录不存在时仍返回 404。其余情况在同一会话锁内严格读取并唯一定位当前行，调用 `/api/cart_service`，使用 `mode=add`、`counter=目标数量−当前数量` 直接减少，再读取购物车核对；不再先删除再加回。
  成功返回 `before_quantity`、`after_quantity` 和最新的 `cart`。
- 上游超时、响应异常或写入后核验失败时返回 `outcome: unknown`，应重新查询购物车，不自动重试或删除重建。核验数量不符返回 409 `yuyutei_quantity_mismatch` 和实际数量。
- 核验按商品参数及 `type` 区分普通与预订记录，只核对被调整的那条记录。

凭据配置页支持粘贴浏览器 Network 的 Copy as cURL（bash / cmd），点击“保存并验证”。只提取 laravel_session 与 X-CSRF-TOKEN，在浏览器中解析并通过现有账户隔离机制保存至 sessionStorage，不执行命令、不上传原始请求；保存后与 Cookie + CSRF 方式相同，读取一次购物车验证凭据（只提交提取出的两项）。成功后清空粘贴内容；解析失败保留原凭据。退出或切换账户会清空表单。

购物车页在“刷新购物车”左侧提供“获取库存”，列表和网格中的每张卡牌也可单独获取。通过 `/api/yuyutei/product?gid=…&ver=…&cid=…&stock=true&kizu=0` 重新读取详情页隐藏库存字段，不使用编号/稀有度的长期缓存。批量查询按商品和品相去重，最多并发三项；失败可重试，无法识别品相对应库存时显示未知，不视为零库存。库存仅为查询时的快照，不修改购物车数量。


## 卡牌订阅后端

### 邮件样式与本地预览

邮件分为验证码和订阅通知两类（`email_templates.py`），共用品牌行、页脚及适配窄屏的表格布局，配色沿用网页的中性灰，使用内联样式，无外部字体、图片或脚本依赖。两类邮件的主题、HTML 与纯文本均为中英双语，中文在前、英文在后；验证码申请中的 `language` 参数仍被接受，但不再影响邮件语言。五种验证码用途共用验证码模板，正文只说明用途、有效期和验证码。订阅通知标题下只显示本批最近一次检查时间；每张卡显示名称（链接到详情页）、稀有度标签、编号及价格／库存的前后对比：变好为绿色、变差为琥珀色，未变化项只显示当前值；邮件底部（HTML 与纯文本）提供前往本站收藏夹的链接，地址由 `TCG_SITE_URL` 决定。两类邮件均发送 `multipart/alternative`，同时保留纯文本正文。

运行 `python scripts/preview_emails.py` 生成示例预览（不会发送邮件），再运行 `python -m http.server 18765 --bind 127.0.0.1 --directory data/email-preview`，访问 `http://127.0.0.1:18765`。预览使用与实际邮件相同的模板；浏览器预览不等同于所有邮箱客户端的最终显示效果。

### 通知邮箱设置

默认使用账户的已验证注册邮箱，不复制邮箱值。可以设置独立的通知邮箱；验证成功前继续使用原来的收件地址。该设置只影响订阅邮件，不改变登录、密码恢复或账户邮箱。多个账户可以在各自完成验证后使用同一个通知邮箱。

以下接口均需 Bearer 登录，写接口沿用可信 Origin 和 JSON 检查；响应不缓存。

| 接口 | 请求与效果 |
| --- | --- |
| `GET /api/subscriptions/notification-email` | 查询当前生效邮箱与默认/自定义状态 |
| `POST /api/subscriptions/notification-email/code` | `{"email":"alerts@example.com","language":"zh-CN"}`，向目标邮箱发送验证码；返回 `challenge_id`、`expires_in`、`retry_after` |
| `PUT /api/subscriptions/notification-email` | `{"challenge_id":"…","code":"123456"}`，验证后启用该邮箱 |
| `DELETE /api/subscriptions/notification-email` | JSON `{}`，恢复注册邮箱并使未完成的通知邮箱验证码失效 |

查询、确认和恢复接口返回 `{"email":"实际收件邮箱","custom_email":null,"using_registration_email":true}`；启用自定义邮箱后 `custom_email` 为该地址，`using_registration_email` 为 `false`。没有可用的已验证邮箱时 `email` 为 `null`，不能新增订阅；已有待发邮件按原规则取消。

验证码复用 `TCG_EMAIL_CODE_SECRET` 和现有 SMTP 配置，10 分钟有效、最多 5 次输错，目的绑定为 `notification_email` 并绑定当前用户，不能用于注册或密码操作。沿用每邮箱 60 秒冷却、每小时 5 次限制，新增每账户每分钟最多 5 次申请限制。错误沿用 `invalid_code`、`code_expired`、`code_attempts_exhausted`、`rate_limited` 和 `email_send_failed`。确认新地址或恢复默认地址会使同一账户其他未完成的通知邮箱验证失效。

尚未发送或等待重试的汇总在实际投递时使用最新有效地址；已完成投递的邮件不会重发。

网页入口位于用户中心的“订阅通知邮箱”卡片（`static/notification-email-ui.js`）：显示当前生效地址及来源（账户邮箱／自定义／未设置），展开后输入新地址、发送验证码并确认；使用自定义邮箱时可一键改用账户邮箱。登录、绑定账户邮箱或修改设置后，收藏夹的订阅按钮和提示按当前生效地址更新，无需重新登录。迁移 `0003` 将两个新字段初始化为空，现有用户继续使用注册邮箱。部署此版本前执行 `tcg-db migrate`，本次代码变更本身不修改运行中的 Docker 数据库。


订阅独立于收藏夹，监控普通品相 `kizu=0`。所有接口需要 Bearer 登录；写入沿用可信 `Origin` 和 `Content-Type: application/json` 检查，响应均为 `Cache-Control: no-store`。

网页目前只在收藏夹页面提供入口：每张已收藏卡牌的刷新按钮旁有铃铛按钮，前端状态由 `static/subscriptions.js` 管理，按游戏和去掉尾部 `/` 的链接与收藏匹配。没有有效通知邮箱时铃铛禁用，并在页面上提示前往用户中心；有效时提示中显示收件地址。绑定邮箱或设置通知邮箱后无需重新登录。已订阅卡牌显示最近检查时间，`last_error` 非空时提示上次检查失败。收藏夹筛选面板额外提供“订阅”筛选（全部／已订阅／未订阅）；共享筛选面板仅在传入 `isSubscribed` 时显示该项，搜索页不受影响。取消收藏接口在同一个数据库事务中删除收藏和当前用户对应的订阅。匹配使用游戏和去掉尾部 `/` 的链接。任一步失败都会回滚。

| 接口 | 请求与响应 |
| --- | --- |
| `POST /api/subscriptions` | `{"game":"wsr","product_url":"https://yuyu-tei.jp/sell/wsr/card/OS01/10240"}`；继承当前用户的查询间隔；返回 200 和订阅记录 |
| `GET /api/subscriptions` | 返回 `{"items":[订阅记录,…]}`，仅包含当前用户的订阅 |
| `GET /api/subscriptions/settings` | 返回当前用户的 `{"interval_hours":24,"next_check_at":1800086400}`，无需已有订阅或已验证邮箱 |
| `PUT /api/subscriptions/settings` | `{"interval_hours":6}`；统一修改当前用户全部已有及未来订阅的间隔，返回与 GET 相同的设置 |
| `DELETE /api/subscriptions/{id}` | 发送 JSON `{}`；取消自己的订阅，成功返回 204，不存在或属于其他用户返回 404 |

订阅记录为 `id`、`item`、`created_at`、`checked_at`、`last_error`、`next_check_at`。`item` 包含 `game`、`name`、`card_number`、`rarity`、`image_url`、`product_url`、`price`、`currency`、`stock_quantity`、`stock_status`、`stock_text`；时间均为 Unix 秒。`checked_at` 为最近检查尝试所属的计划扫描时间（整点扫描即该整点，启动扫描为实际开始时间），失败时仍保留上次有效 `item`，错误代码放入 `last_error`。

创建时要求存在有效通知邮箱（已验证自定义邮箱或已验证注册邮箱），否则返回 403 `verified_email_required`。只接受六个受支持游戏的游游亭 HTTPS 详情 URL，游戏路径必须匹配（`lycee` 对应 `lo`）；去掉尾部 `/` 并统一主机名。查询参数、片段、其他域名、额外路径或编码路径不接受。名称、价格、库存由服务端获取，不采信客户端快照。

相同用户、游戏、链接重复创建返回原记录，不再次抓取、不重置基准或已有调度；初次读取失败不创建订阅。上游超时返回 504 `product_timeout`；HTTP 错误返回 502 `product_unavailable`；缺少或无法确定普通品相价格/库存返回 502 `product_unreadable`。

设置 `TCG_SUBSCRIPTIONS_ENABLED=true` 后，FastAPI 启动后台线程，启动时扫描一次，此后按进程本地时间每小时整点扫描 `next_check_at <= 本轮开始时间` 的订阅。整点扫描即使因唤醒抖动或等待邮件锁而晚 10 分钟内开始，`checked_at` 和下次时间仍按该整点计算，避免 `HH:00:01` 开始的扫描让下一次错过 `HH:00:00` 而整体顺延一小时。每轮仅对到期订阅按游戏和链接去重，同卡未到期订阅不更新。与上次成功检查（首次为订阅时）的日元价格、库存数量和有货/缺货状态比较，元数据或库存文案变化不会触发邮件。未知库存、404 和解析失败保留基准，其他卡牌继续处理。

按用户事务保存新快照和邮件待发记录，每轮每位用户最多生成一封中英双语汇总，同一天可有多封，列出所有成功检测到的变化及详情链接；无变化不发。投递时读取当前有效通知邮箱发送纯文本及 HTML 邮件；发送前剔除已经取消的订阅，剔除后为空则标记 `cancelled`。SMTP 在数据库事务外执行，沿用 `TCG_SMTP_*`，无须游游亭登录凭据。

首次投递失败后等待 60、300、1800 秒再重试，后台最多每 30 秒扫描到期邮件，累计 4 次失败转为 `failed`。重启恢复未耗尽的待发重试，不自动重发终态失败邮件。SMTP 已接收但成功状态尚未提交时进程崩溃，可能重复发送同一 Message-ID。邮件表中的变化记录不因取消订阅而移除，保留用于排查。

用户中心的“订阅查询间隔”统一设置该账户全部订阅的间隔，默认 24 小时、最低 1 小时。设置独立于通知邮箱，无订阅时也可提前设置。`interval_hours` 必须是 1 至 2147483647 的整数（PostgreSQL integer 范围），不接受小数、布尔值或字符串。

间隔仅存储在 `users.subscription_interval_hours`，每条订阅只保留 `next_check_at`。首次抓取开始时记录 `checked_at`，下次时间为 `checked_at + 用户间隔 * 3600`。PUT 在一个事务中修改用户设置并重算该用户全部订阅的下次时间；重复保存不会延后任务，已到期的等待下一次扫描。创建时读取用户最新设置。创建订阅请求不允许携带 `interval_hours`（422），原逐条 PATCH 接口已移除（405）。

设置响应的 `next_check_at` 是预计下次查询时间（Unix 秒）：取该用户最早的订阅 `next_check_at`，向上取到首个本地整点扫描；已到期则为当前时间之后的下一个整点（恰在整点时为该整点）。无订阅时为 `null`。用户中心在标题下显示“每 N 小时查询一次”和该时间（按浏览器本机时区显示为 `2026/11/11 22:00 (UTC-5)`，偏移随夏令时变化），每次进入用户中心刷新；表单默认收起，提供 1/3/6/12 小时及 1/3 天快捷选项和自定义小时数，值未变化时不可保存。

成功或失败的检查均将下次时间设为本轮开始时间加用户间隔；失败保留原快照并记录错误。间隔按经过的秒数计算，不受夏令时影响。由于每小时整点扫描，正常情况下实际执行可比到期时间晚不足一小时；抓取和锁等待也可能延迟执行。

停机积压在启动时只检查一次，不逐次补跑。数据库会话锁串行化检查和投递；抢锁失败保留扫描期限，锁释放后重试。每用户事务原子保存快照、下次检查时间及邮件，已提交的记录不重复处理，异常或强制终止留下的到期记录在后续扫描继续处理。保存前重新锁定订阅，跳过已取消或延长间隔后不再到期的记录，使用用户的最新间隔。停止时等待当前网络操作结束并退出线程，随后关闭数据库。

迁移 `0009` 保留历史任务及邮件并允许同日多轮汇总；迁移 `0010` 升级到 schema 10，将所有用户统一重置为 24 小时，按 `checked_at + 86400` 重算订阅下次时间，移除订阅的独立间隔字段。快照、任务、邮件和重试状态均保留。发布时停止所有旧 Worker，执行 `tcg-db migrate`，再启动新版本，不能混跑新旧调度。

数据库状态可通过只读 SQL 查询：

```sql
SELECT id, local_date, state, started_at, finished_at, last_error
FROM subscription_runs ORDER BY started_at DESC LIMIT 20;

SELECT id, user_id, run_id, run_date, state, attempts, next_attempt_at, last_error
FROM subscription_emails WHERE state IN ('pending', 'failed')
ORDER BY created_at DESC LIMIT 100;

SELECT id, user_id, product_url, checked_at, next_check_at, last_error
FROM subscriptions WHERE last_error IS NOT NULL;

SELECT id, user_id, next_check_at
FROM subscriptions WHERE next_check_at <= EXTRACT(EPOCH FROM now())
ORDER BY next_check_at;

SELECT id, subscription_interval_hours FROM users;
```

`subscription_runs.state` 为 `running/complete/failed`；`subscription_emails.state` 为 `pending/sent/failed/cancelled`。任务完成可以同时存在个别卡牌抓取错误，请结合订阅的 `last_error` 查看。日志包含有效时区、下一次执行时间、每轮唯一卡牌数、用户卡牌变化数、抓取失败数以及邮件投递状态，不输出 SMTP 密码。

## 卡牌库导出

卡牌库页的“导出全部”支持 XLSX 和 JSON，不受页面筛选影响。
`GET /api/cards/export?format=xlsx|json` 使用现有 Bearer 登录，仅导出当前用户的全部卡牌，
顺序与卡牌列表接口一致，响应为附件且禁止缓存。JSON 为 `{"items":[卡牌记录,…]}`，
XLSX 的 `Cards` 工作表以字段名作为表头，包含游戏、卡号、稀有度、名称、数量、备注、图片 URL、
记录 ID 和创建／更新时间（Unix 秒）。零数量记录也会导出；空库仍可下载。
XLSX 文本不解析为公式，超过 15 位的数量以文本保留精度；文本超过单元格 32767 字符上限时
返回 422 `export_xlsx_text_too_long`，页面提示改用 JSON。Web 安装依赖包含 XlsxWriter。

## WS 英文版卡牌库

`WS EN` 是独立于 `WS` 的卡牌库品牌，接口标识及稀有度映射键为 `ws_en`。
添加卡牌时通过 `/api/official/ws_en/search?word=BD%2FWE34` 查询英文官网
`https://en.ws-tcg.com/cardlist/searchresults/?keyword=`，支持常规关键词匹配，无需完整卡号。
候选列表显示前 5 条及总匹配数，可填入名称、稀有度和图片；徽章沿用 WS 图标。
不接入游游亭价格搜索、收藏刷新或购物车。日英文卡牌独立存储、筛选和统计。

本地前端在 `TCG_ALLOW_PROXY_SWITCH=true` 时默认关闭代理，使用浏览器 `localStorage` 的 `tcg.proxy-enabled` 保存上次选择，刷新页面或重新登录后恢复。偏好按浏览器站点保存，不按账户区分。关闭此配置、使用远程 API 前端或无法读取开关能力时，保持服务端默认代理模式，不采用或覆盖浏览器偏好；实际代理地址仍由 `TCG_SEARCH_PROXY_URL` 决定，显式为空时直连。后台订阅检查不受浏览器开关影响。

游游亭购物车使用与搜索相同的代理开关。代理需支持方法、正文、Cookie、CSRF 请求头、状态码和 Set-Cookie 的透传（Lite-Proxy 协议）；不会在代理失败时回退直连或自动重试购物车写入。Cookie 仍按游游亭域名维护，并仅保留在当前操作内。当前默认代理入口为 HTTP，该传输段不加密。

## 加密代理接口

设置 `TCG_PROXY_KEY_FILE` 为 32 字节原始密钥文件路径后，代理使用 `/encrypted-proxy`。`TCG_SEARCH_PROXY_URL` 可填写服务根地址或 `/encrypted-proxy` 地址，不再支持旧 `/proxy`。启用代理必须配置密钥文件，否则请求失败且不会发送网络请求；代理关闭时仍直连。加密失败不会回退明文或自动重试。

协议与 lite-proxy v1 客户端一致：HKDF-SHA256 派生请求／响应密钥，AES-GCM 加密目标 URL、请求头、正文和响应，并校验响应 request_id。密钥不应提交到仓库；容器使用时需只读挂载密钥，并将容器内路径传入该环境变量。外层 HTTP 仍可暴露代理地址、流量大小及时间，加密不对代理运营方隐藏内容。
