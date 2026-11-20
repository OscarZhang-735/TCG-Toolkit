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
| `TCG_SEARCH_PROXY_URL` | Hobby Station 搜索和购物车，以及游游亭搜索、商品详情、凭据验证和购物车的加密转发接口；显式设为空可直连 | `http://62.234.212.90:8080/encrypted-proxy` |
| `ZYTE_API_KEY` | PSA 与 CGC 网页查询使用的 Zyte Extract API 密钥 | 空 |
| `PROXY_GO_API` | 独立 `proxy-go` 客户端网关地址，支持基础 URL 或完整 `/v1/fetch` 地址 | 空 |
| `PROXY_GO_API_KEY` | `proxy-go` 服务端 API 密钥，仅填写密钥本身 | 空 |
| `PGHOST` / `PGPORT` | PostgreSQL 地址；Compose 内使用服务名 | `db` / `5432`（Compose） |
| `PGDATABASE` / `PGUSER` / `PGPASSWORD` | 应用数据库、非超级用户角色及密码 | Compose 默认库和角色为 `tcg`；密码必填 |
| `TCG_PG_ADMIN_PASSWORD` | PostgreSQL 初始化管理员密码，仅传给 db | 必填 |
| `TCG_POSTGRES_IMAGE` | 随后端一起发布的 PostgreSQL 镜像 | 固定 PostgreSQL 17 digest |
| `TCG_DB_SUBNET` | 内部数据库网络 | `10.205.0.0/24` |
| `TCG_PROXY_IPS` | 受信任的反向代理来源 IP，不要设为 `*` | `127.0.0.1` |
| `TCG_SUBNET` | Docker 网络子网，与本地网络冲突时修改 | `10.203.0.0/24` |
| `TCG_SUBSCRIPTIONS_ENABLED` | 启动卡牌订阅每小时到期扫描及邮件重试线程 | 直接运行关闭；Compose 开启 |
| `TCG_HOBBYSTATION_SUBSCRIPTIONS_DIRECT` | 为 `true` 时，后台定时任务直连 Hobby Station 商品页面。手动订阅使用当前网页选择的连接方式 | 本地 Compose 开启；其他部署默认沿用服务端代理配置 |
| `TCG_SITE_URL` | 订阅邮件底部“前往 TCG Toolkit 查看”链接使用的网页地址（自动追加 `#favorites`）；须为 http(s) 地址，未设置或无效时使用公开站点 | `https://oscarzhang-735.github.io/TCG-Toolkit/` |
| `TZ` | 根目录 Compose 必填，设为宿主机 IANA 时区，例如 `America/Toronto`；Linux 生产部署使用挂载的宿主机 `/etc/localtime` | `.env.example` 示例为 `America/Toronto` |

`.env` 已被 Git 忽略，请勿提交密钥。公网部署请参阅 [部署手册](../deploy/README.md)。

### proxy-go 代理客户端

`ProxyGoClient` 用于需要处理风控或验证的 GET/HEAD 请求，由业务代码显式调用。PSA 与 CGC 证书查询通过此客户端抓取网页；游游亭加密代理、直连开关和 ARS 查询保持原有行为。

在 `.env` 中设置 `PROXY_GO_API` 与 `PROXY_GO_API_KEY`。配置优先级为构造参数、进程环境、当前工作目录的 `.env`；显式空值会报告配置错误，不会继续读取较低优先级的值。主机直接运行 Python 时可使用 `http://127.0.0.1:18087/v1/fetch`。Docker Desktop 中的 Web 连接宿主机时改用 `http://host.docker.internal:18087/v1/fetch`，并确保 Proxy-Go 监听容器可达的地址。迁移到服务器后更新地址和密钥即可；PSA/CGC 已改用 Zyte；三份 Compose 向 Web 服务传入 `ZYTE_API_KEY`。

```python
from tcg_toolkit.external import ProxyGoClient, ProxyGoGatewayError

with ProxyGoClient() as client:
    try:
        response = client.get(
            "https://www.psacard.com/en-CA/cert/98428602/psa",
            country="US",
        )
    except ProxyGoGatewayError as exc:
        print(exc.status_code, exc.detail, exc.request_id)
        print(exc.proxy_total_bytes, exc.proxy_attempts)
        raise

    metadata = response.extensions["proxy_go"]
    print(metadata["request_id"], metadata["proxy_traffic"])
    response.raise_for_status()  # 检查目标站状态，而非网关状态。
    html = response.content      # 已解码的正文 bytes。
```

也可使用 `client.head(url, country="US")` 或 `client.fetch(url, country="US", method="GET")`。仅支持 GET/HEAD，不接受目标站自定义请求头、Cookie 或请求体。默认国家为 `US`，默认超时 220 秒、连接超时 5 秒，忽略系统代理环境变量。客户端不自动重试；验证处理和代理重试由 Proxy-Go 执行。

网关非 200 状态抛出 `ProxyGoGatewayError`，保留 `status_code`、`detail`、`request_id`、`proxy_total_bytes` 和 `proxy_attempts`；早期认证或参数错误可能没有后三项。网络错误沿用 HTTPX 异常。网关返回 200 时，客户端将 JSON 中的目标 `status_code`、响应头和 Base64 正文还原为 `httpx.Response`，请求 ID 与完整流量统计位于 `response.extensions["proxy_go"]`。目标 4xx/5xx 仍返回响应，由调用方检查；目标重定向不会自动跟随，可读取 `Location` 后自行决定是否继续请求。

### 模块边界与扩展

Python 源码位于 `src/tcg_toolkit/`。`web.py` 在启动时打开并检查数据库，将同一个连接池交给认证、订阅任务和图片清理任务，并组装各领域的 HTTP 路由。业务路由通过 `request.app.state.database` 获取数据库。命令行入口为 `tcg-search` 和 `tcg-db`，Python 搜索调用继续从 `tcg_toolkit` 导入。

| 领域 | 职责与依赖 |
| --- | --- |
| `catalog/` | `search.py` 根据来源和游戏选择 `providers/` 下的商户搜索器。`identity.py` 定义商品身份，`games.py` 提供库存与报价共用的游戏名称。`prices.py` 按 `MERCHANTS` 登记报价来源。 |
| `cart/` | 三家商户分别处理各自的购物车协议，共用 `operations.py` 的写入预占、重放及会话锁。 |
| `collection/` | `cards.py`、`graded_cards.py` 和 `favorites.py` 管理普通卡、评级卡及收藏。三类藏品复用 `groups.py` 的分组规则，保留各自的表、权限范围和错误码。`transfer.py` 管理普通卡的 JSON/XLSX 导入导出。 |
| `grading/` | `adapters.py` 读取各机构的证书，`lookup.py` 维护机构注册表并提供查询服务，`concurrency.py` 管理并发和取消。证书查询接口与评级卡库调用同一查询服务。 |
| `subscriptions/` | `service.py` 管理订阅与检查间隔，`checks.py` 在同一事务内更新订阅、收藏和待发通知。`schedule.py` 计算时间，`worker.py` 扫描到期任务并重试投递。 |
| `auth/`、`mail/`、`media/` | 认证模块管理账户、会话和验证码。认证和订阅共用邮件模板与 SMTP 投递。头像和评级卡照片共用 COS 对象存储和清理队列。 |
| `currency/`、`external/` | 汇率客户端与兑换接口归入 `currency/`。`external/` 提供商品和证书适配器使用的外部 HTTP 传输客户端。 |
| `persistence/`、`migrations/` | 前者提供连接、事务、ORM 映射与 schema 校验。Alembic 从原迁移目录读取已有版本，`tcg-db` 和应用启动检查使用同一套版本规则。 |
| `static/` | `auth.js` 和 `app.js` 组装浏览器界面。功能模块归入 `account/`、`cart/`、`catalog/`、`collection/`、`subscriptions/`，跨功能组件归入 `shared/`。Pages 构建保留这些目录并为模块引用添加版本号。 |

新增搜索游戏时，在对应商家的注册表中登记搜索器，并更新前端 `static/catalog/search-sources.js` 中的来源与游戏对应表。

### 按商家搜索

HTTP、CLI 和 Python 共用 `create_searcher(source, game, *, stock=False)` 选择搜索器。每页结果（含空页）都在顶层提供 `source`。默认读取列表中的库存，开启 `stock` 后允许额外查询详情页。目前只有 Card Max 会为此发出额外请求。

```python
from tcg_toolkit import create_searcher

with create_searcher("cardmax", "sev", stock=True) as searcher:
    page = searcher.search_page("BP22-014")
    print(page.to_dict()["source"])  # cardmax
```

登录后可调用 `GET /api/search?source=hobbystation&game=lycee&keyword=LO-4899&page=1`。省略 `source` 时默认使用 `yuyutei`。Hobby Station 支持 `lycee`、`ws` 和 `wsr`，Card Max 支持 `ws` 和 `sev`。不支持的来源或游戏组合返回 422。各来源使用相同的商品字段和分页结构，调用方可按 `next_page` 继续查询至其为 `null`。

Hobby Station 搜索使用站点的部分匹配模式（`match_type=1`），经现有加密代理访问，代理的 `allowed_hosts` 需包含 `www.hobbystation-single.jp`。库存符号如 ◎ 表示有货，`stock_quantity` 为 `null`。页面标记售罄时数量为 0。Lycee 商品编号如 `LO-LO-4899` 返回印刷编号 `LO-4899`，同名异版保留各自的编号和商品链接。Hobby Station 的 `cart` 字段为 `null`。

Card Max 使用服务器直连，例如 `GET /api/search?source=cardmax&game=sev&keyword=BP22-014&stock=true`。默认只读取搜索列表，列表包含售罄单卡（如实测的 `GBF/S134-132S`），库存返回 `unknown` 和 `null`。可用 `stock=true`、CLI 的 `--stock` 或 Python 搜索器构造参数 `stock=True` 开启详情查询，每条单卡结果额外请求一次详情页。`残りあとN個` 返回具体数量，`売り切れ` 返回 `out_of_stock` 和 0。WS 可输入完整编号，如 `AMG/W121-019SP`。Card Max 的 WSR 分类目前仅有盒装商品。

网页搜索可多选来源，前端 `static/catalog/search-sources.js` 维护与 `SEARCH_SOURCES` 一致的来源与游戏对应表，只对受支持的游戏和来源组合发请求，不支持的组合静默跳过。选中 Card Max 时显示“解析 Card Max 库存”选项，开启后仅对 Card Max 请求附带 `stock=true`。各来源的结果都可直接收藏，收藏页可按来源筛选。三家店的收藏均可单张或批量订阅。购物车数量控件支持游游亭、Hobby Station 和 Card Max，按来源操作相应购物车。Hobby Station 收藏保存原站图片地址，而非本服务的图片转发地址。新增来源时需同步更新该文件。

命令行可使用 `tcg-search "LO-4899" --source hobbystation --game lycee` 或 `tcg-search "BP22-014" --source cardmax --game sev`，同样支持 `--page` 和 `--all`。

命令行搜索统一使用 `tcg-search "关键词" --game wsr`，多游戏可重复 `--game`，例如 `tcg-search "BP01" --game ws --game sev --all`。旧 `tcg-search 游戏 关键词` 格式已移除。内部模块调用方应使用上述新路径，不提供旧模块路径的兼容转发。数据库迁移链继续保留，以支持已有数据库升级。

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

持久化实体统一定义在 `src/tcg_toolkit/persistence/models.py`，与搜索结果及 API 输入模型分开。
业务代码使用 SQLAlchemy 2 的 `select()`、实体属性和 PostgreSQL upsert 表达式，不拼接 SQL，也不使用 `text()` 接收用户输入。

`Database.session()` 为每次操作创建独立 Session：正常退出提交，异常退出回滚并释放连接。Session 不跨线程共享。
连接池最多 4 个连接、不额外溢出，并在取连接时检查可用性；数据库错误隐藏 SQL 参数。

```python
from sqlalchemy import select
from tcg_toolkit.persistence.models import OwnedCard

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
已有 PostgreSQL schema 1 首次运行时先登记 Alembic 基线 `0001`（如尚未登记），再执行 `0002` 新增订阅、每日任务和待发邮件表，再执行 `0003` 增加独立通知邮箱字段，再执行 `0004` 创建收藏分组表、`0005` 取消默认组并使收藏默认未分组，随后执行 `0006` 至 `0008` 的头像、清理队列和昵称迁移，以及 `0009` 的独立订阅间隔、`0010` 的用户统一间隔、`0011` 的卡牌库分组、`0012` 的价格字段、`0013` 的价格币种、`0014` 的单会话迁移、`0015` 的评级卡快照表迁移、`0016` 的评级卡上传图片字段、`0017` 的评级卡价格、`0018` 的评级卡分组和 `0019` 的评级卡显示名称，升级到 schema 19，保留用户和业务数据。`0014` 将每账号的会话收敛为一条，规则见下文。
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

## 收藏夹后端

同一账号可在一个分组中收藏多个店铺的商品。同一卡号在不同店铺分别保存。调用 `POST /api/favorites` 时可直接提交搜索结果中的一条 `item`，后端按 `product_url` 识别来源。也可在请求体中传入搜索页顶层的 `source`。来源与商品域名不符、商品链接格式错误或店铺不支持该游戏时返回 422。

| `source` | 支持的 `game` | 收藏刷新 |
| --- | --- | --- |
| `yuyutei` | `ws`、`wsr`、`lycee`、`sev`、`pm`、`chaos` | 匹配商品链接，快照带有购物车品相时同时匹配品相 |
| `hobbystation` | `ws`、`wsr`、`lycee` | 从列表读取价格和库存，`◎` 返回 `in_stock`、数量为 `null`。图片使用后端转发地址 |
| `cardmax` | `ws`、`sev` | 从列表匹配商品并读取价格，再请求该商品的详情页读取库存 |

收藏夹提供单层可选分组：收藏默认未分组（`group_id` 为 `null`），也可加入一个用户新建的分组。没有默认组，注册时不创建分组。分组可以创建、改名和删除，删除时在同一事务内将其中收藏改为未分组，保留收藏 ID、快照、创建／刷新时间及独立订阅。

WebUI 收藏页顶部为分组栏：“全部”及各分组（附数量），可新建分组，选中分组后可改名或删除；未分组收藏只在“全部”中出现。页面加载全部收藏后在前端按组筛选，“刷新全部”只刷新当前所选分组。存在分组时，每张收藏卡牌提供“移动到分组”按钮，可选择某个分组或“未分组”；卡片右下角及列表“分组”列显示所在分组名。搜索页新增的收藏为未分组。接口需要 Bearer 登录，写接口沿用可信 `Origin` 和 JSON 检查，响应均为 `Cache-Control: no-store`。

| 接口 | 请求与响应 |
| --- | --- |
| `GET /api/favorites/groups` | 返回 `{"items":[分组记录,…]}`；按创建时间、ID 升序 |
| `POST /api/favorites/groups` | `{"name":"待购"}`；返回 201 和分组记录 |
| `PUT /api/favorites/groups/{group_id}` | `{"name":"新名称"}`；返回 200 和更新后的分组记录 |
| `DELETE /api/favorites/groups/{group_id}` | JSON `{}`；将其中收藏改为未分组后删除分组，返回 204 |
| `GET /api/favorites?group_id=…` | 返回该组的 `{"items":[收藏记录,…]}`；不传参数时仍返回全部收藏，保留创建时间、内部排序号倒序 |
| `POST /api/favorites` | 卡牌快照字段，可选 `source` 和 `group_id`。省略 `group_id` 或传 `null` 表示未分组。返回 200 和收藏记录 |
| `PUT /api/favorites/{favorite_id}/group` | `{"group_id":"目标组 ID"}`，`null` 表示移出分组；返回 200 和移动后的收藏记录 |
| `POST /api/favorites/{favorite_id}/refresh` | JSON `{}`。按原店铺、游戏和卡号搜索，匹配商品链接后更新快照。商品未找到返回 404，上游请求或解析失败返回 502，超时返回 504。失败时保留原快照 |
| `DELETE /api/favorites/{favorite_id}` | JSON `{}`。删除当前用户的收藏，返回 204 |

分组记录为 `id`、`name`、`created_at`。收藏记录为 `id`、`source`、`group_id`（未分组为 `null`）、`item`、`created_at`、`refreshed_at`。`item` 使用卡牌快照结构。时间为 Unix 秒。历史游游亭收藏也返回 `source: "yuyutei"`，无需为来源迁移数据。重复收藏返回原记录，移动分组使用专用 PUT 接口。

名称去除首尾空白后须为 1–100 个字符。同一用户内名称唯一、区分大小写。无效输入返回 422；名称冲突返回 409 `favorite_group_name_taken`。不存在或属于其他用户的分组统一返回 404 `favorite_group_not_found`，收藏仍使用 404 `favorite_not_found`。

分组写入、新增收藏和移动收藏先锁定用户行，再操作分组及收藏。数据库通过复合外键限制收藏只能引用同用户的分组。

部署前执行 `tcg-db migrate`：迁移 `0004` 创建分组表和外键；`0005` 将 `group_id` 改为可空，把原默认组中的收藏改为未分组，删除默认组及 `is_default` 列，保留自定义组及其收藏，更新到 schema 5。历史收藏升级后均为未分组。回退沿用恢复匹配备份的流程。

## 评级卡证书查询

### WebUI 与保存接口

登录后的 `#graded` 页面使用后端 `PROVIDERS` 注册表获取机构名称、扫码 URL 规则和预期查询时限。`GET /api/grading/providers` 返回 `{"items":[{"id":"psa","name":"PSA","timeout_seconds":225,"scan_patterns":["…"]}, …]}`；PSA、CGC 为 225 秒，ARS、PCG-CN 为 40 秒。这是界面等待提示与浏览器超时的依据，底层 HTTP 超时仍按各机构客户端设置。

以下接口均需本站 Bearer Token，响应含 `Cache-Control: no-store`。写接口还要求可信 `Origin` 和 `Content-Type: application/json`，包括 DELETE。

| 方法与路径 | 输入与结果 |
| --- | --- |
| `GET /api/graded-cards` | 返回 `{"items":[记录, …]}`，按添加时间倒序，同秒按写入顺序倒序 |
| `POST /api/graded-cards` | `{"provider":"psa","cert_number":"98428602","request_id":"可选"}`；还可传入 `price`、`currency` 和 `group_id`（分组不存在时查询前返回 404 `graded_group_not_found`；查询期间分组被删则保存为未分组），查询成功后保存并返回 201 和记录 |
| `GET /api/graded-cards/groups` | 返回本账号评级卡分组 `{"items":[{id,name,created_at}, …]}`，按创建时间排序 |
| `POST /api/graded-cards/groups` | `{"name":"…"}`（去除首尾空白，1–100 字），返回 201；同名返回 409 `graded_group_name_taken` |
| `PUT /api/graded-cards/groups/{id}` | `{"name":"…"}` 改名，返回 200；不存在返回 404 `graded_group_not_found` |
| `DELETE /api/graded-cards/groups/{id}` | JSON `{}`；删除分组，其中的评级卡保留并变为未分组，返回 204 |
| `PUT /api/graded-cards/{id}/group` | `{"group_id":"…"}` 移入分组，`null` 移出分组，返回 200 和记录 |
| `PUT /api/graded-cards/{id}/display-name` | 提交 `{"display_name":"…"}`（去除首尾空白，≤ 100 字）设置可选显示名称，返回 200 和 `{"display_name":…}`；空白或 `null` 清除，恢复显示证书卡名。重新查询不会覆盖 |
| `PUT /api/graded-cards/{id}/price` | 提交 `{"price":"12.34","currency":"CNY"}` 更新手动记录的价格与币种，返回 200 和这两个字段。传入 `price: null` 清除价格 |
| `POST /api/graded-cards/{id}/refresh` | JSON `{}` 或 `{"request_id":"…"}`；重新查询，成功返回 200 和更新后的记录 |
| `DELETE /api/graded-cards/lookups/{request_id}` | JSON `{}`；取消本账号该 `request_id` 的查询，返回 204；查询已保存则 409 `graded_lookup_finished` |
| `DELETE /api/graded-cards/{id}` | JSON `{}`；删除本账号记录，成功返回 204；该卡上传的图片进入 COS 删除队列 |
| `POST /api/graded-cards/detect-card` | 请求体为图片（`image/jpeg`、`image/png`、`image/webp`，≤ 12 MB）；返回 `{"width":…,"height":…,"corners":[[x,y]×4] 或 null}`，坐标为该图片像素，顺序为左上、右上、右下、左下 |
| `POST /api/graded-cards/calibrate-image` | 请求体为图片；返回同尺寸的亮度校准 JPEG（`image/jpeg`），不保存任何内容 |
| `PUT /api/graded-cards/{id}/images/{front\|back}?corners=x1,y1,…,x4,y4` | 请求体为图片；按四个角透视校正截取为 WebP（长边 ≤ 1600 像素）存入 COS，替换该面图片，返回 200 和记录 |
| `DELETE /api/graded-cards/{id}/images/{front\|back}` | JSON `{}`；移除上传的图片，恢复机构图片或占位，返回 200 和记录 |

记录字段为 `id`、`provider`、`cert_number`、`source_url`、`title`、`grade`、`fields`、`english_fields`、`population`、`images`、`created_at`、`refreshed_at`、`price`、`currency`；时间为 Unix 秒。`title` 与两组字段由 `grading.present()` 在读取时按注册表生成，旧快照同样适用：`GradingProvider.title_field` 指定作为名称的字段（ARS 为原版 `カード名称`，PCG-CN 为原名 `Original Name`，缺失时回退到解析器的 `title`）；`english_field` 判定只是译文的字段（ARS 为英文标签），这些字段放入 `english_fields`，WebUI 详情中默认折叠。直接查询接口 `GET /api/grading/...` 的返回保持解析器原样。证书编号为字符串，服务端只去除首尾空白并接受 1–32 位 ASCII 数字。前端额外支持移除数字间的空格和连字符、从机构官网链接提取编号。

同一账号的 `(provider, cert_number)` 唯一，重复新增返回 409 `{"detail":{"type":"graded_card_duplicate","id":"已有记录ID"}}`。已有重复在查询前直接返回；同时新增由数据库唯一约束仲裁。不同账号可分别保存同一证书。访问不存在或属于其他账号的记录均返回 404 `graded_card_not_found`，查询失败不会写入新记录或覆盖旧快照。

数据库版本 **15**（Alembic `0015`）新增 `graded_cards` 表，保存每张证书最近一次成功查询的 JSON 快照；版本 **16**（`0016`）为其增加可空的 `front_image_url`、`back_image_url`，保存用户上传的图片地址，与查询快照分开，重新查询不会覆盖。版本 **17**（`0017`）新增可空的 `price` 和默认 `JPY` 的 `currency`。价格为非负金额，最多 2 位小数，币种支持 JPY、CNY 和 CAD。用户可在添加及详情页填写价格，清空金额并保存可清除价格。仪表盘按当前显示币种汇总筛选结果中价格可换算的卡片。升级前备份，使用匹配版本运行 `tcg-db migrate`；生产共同发布流程会执行迁移。此迁移保留既有账号、会话和卡牌数据，不提供自动降级。

查询与数据库操作分开执行，等待机构响应时不持有数据库连接。

`request_id` 为浏览器生成的 8–64 位字母、数字或连字符，归属当前账号。取消时服务端关闭该查询正在使用的上游 HTTP 客户端（进行中的读取立即失败，释放并发名额），并通过一次性令牌保证已取消的查询不会保存，原请求返回 409 `grading_lookup_cancelled`。先到达的取消会让随后到达的同 ID 查询直接结束；取消与保存的结果在进程内保留 5 分钟，取消晚于保存时返回 409 `graded_lookup_finished`，前端据此重新加载列表。取消登记和并发上限一样是进程内状态。直接查询、新增和刷新共享进程内并发上限：每账号 3 项、全进程 8 项，超限返回 429 `grading_busy`。多进程部署时各进程分别计数。

前端 `graded-cards.js` 统一管理新增和刷新队列，每标签页并行 2 项；浏览器等待上限为机构 `timeout_seconds` 加 30 秒。超时、网络或服务错误由用户显式重试；机构或编号错误提示修改输入。刷新失败仍显示旧数据。查询中的占位卡与已保存卡片布局、尺寸一致（列表中同列对齐），显示不确定加载环、已等待时间和“最多可能需要几分钟”，并提供“取消查询”：前端中止请求并调用上述取消接口；重新查询同样可取消，已保存数据不变。退出或更换账号会清空本页状态并丢弃迟到响应；关闭页面不会发送取消，服务器已开始的查询仍会完成并保存。队列不持久化，成功记录从数据库重新加载。

`graded-cards-ui.js` 提供列表/网格、详情、筛选、排序、图片预览和删除确认。

**上传与扫描卡牌图片。** 详情页点击正面或反面图片打开选项：查看大图（有图片时）、上传图片、扫描卡牌（`capture=environment` 调用摄像头拍照）、移除上传的图片（已上传时）。浏览器先把照片缩到长边 ≤ 2400 像素并重新编码为 JPEG（同时应用 EXIF 方向），再调用 `detect-card`；截取对话框 `graded-crop.js` 在照片上叠加 SVG 四角框，预置识别结果（识别失败则为整张照片稍向内缩），可用鼠标、触摸拖动或方向键（Shift 加速）调整，“整张图片”“自动识别”可重置。保存时把同一张 JPEG 与四角坐标发给 `PUT …/images/{side}`，服务端透视校正截取。记录的 `images` 统一为正面、反面两项 `{url, alt, side, thumbnail_url, uploaded}`：上传图片优先；机构未标注正反面的图片（如 PCG-CN）按页面顺序依次作为正面、反面。

边框识别位于 `collection/card_detect.py`（依赖 `opencv-python-headless`，Pillow 先解码并应用 EXIF 方向）：在长边 900 像素的副本上，用多组 Canny 边缘及 Otsu 阈值图找轮廓，取凸包拟合四边形（圆角卡砖退化为外接旋转矩形），筛选面积占照片 8%–98.5%、短长边比 0.45–0.92、填充度 ≥ 0.8、两个以上角不贴照片边缘的候选，再按面积 × 矩形度 × 四边梯度支撑度评分取最高者。除轮廓外还用 Hough 直线组合四边形：反光和保护袋常使卡砖外框断开、无法形成闭合轮廓，但四条边仍是长直线。候选还需四角在 72°–108° 之间、对边长度比 ≥ 0.72（排除由卡砖上边与卡牌下边拼成的形状）；边缘支撑度取四边中最弱一边的平均梯度强度与强边缘覆盖率之和。选出最高分后，若有包住它、得分不低于 80% 且支撑度不低于 70% 的更大四边形则取之，因此卡砖照片优先截取整块卡砖（不被内部卡牌边框截短，也不被保护袋上较弱的折边拉长），裸卡截取卡牌。测试用 `tests/card_photos.py` 合成不同背景、旋转和透视的照片验证角点误差（≤ 4%），实物照片的效果仍需在设备上确认，失败时可手动调整。

**亮度校准（可选）。** 截取对话框的“亮度校准”按钮把当前照片发给 `calibrate-image`，用返回的同尺寸 JPEG 替换预览和待上传图片，四角坐标不变；按钮随即变为“撤销亮度校准”，原图和校准图都保留在浏览器内存中，撤销与再次应用无需重新请求。处理位于 `collection/card_enhance.py`：① 高光区检测——同时满足较亮（HSV 明度 > 165 起计）、低饱和，并且比所在表面更亮（比文字闭运算后的局部表面亮，或比排除饱和高光后的大范围“漫反射白”参考亮），以免把白色卡面、白色衣服误判为反光；② 在高光区内用局部暗通道先验去除白色反光“薄雾”，按反光外较暗表面（墨色、画面）的暗通道估计遮罩强度，上限 0.6；③ 局部光照归一化——以局部亮包络估计光照，把较暗区域提升到光照充足水平（增益 0.9–1.25）；④ CLAHE 轻度增强对比度。大尺度模糊在缩小副本上计算，2400 像素照片约 0.5–1 秒。测试用合成的“画面上覆盖反光”场景验证反光下画面变暗、对比度提高、未受影响区域基本不变及白色卡面不被误判。

上传图片沿用头像的 COS 存储桶 `TCG_AVATAR_COS_URL`，对象为 `/tcgtoolkit/graded/<随机32位十六进制>.webp`；替换、移除和删除卡片时旧对象进入头像共用的 `avatar_cleanup` 删除队列（后台任务重试）。未配置存储桶时上传返回 503 `graded_image_storage_failed`。识别与上传每账号每分钟共 30 次。错误代码：415/422 `graded_image_invalid`、413 `graded_image_too_large`、422 `graded_image_dimensions`（每边 > 6000 像素）、422 `graded_image_crop`（坐标无效或截取过小）、404 `graded_card_not_found`／`graded_image_side`、429 `rate_limited`。筛选区在没有卡片时也保留（卡牌库与收藏页同样在加载完成后始终显示筛选区）。评级卡页面的“多选”与卡牌库一致：可勾选或全选当前筛选后显示的卡片，选择栏提供批量“重新查询”（逐张交给前端查询队列，同时最多两项，已在查询中的卡片跳过）和“删除”（确认后逐张调用 `DELETE /api/graded-cards/{id}`，完成的取消选中，失败的保持选中）；存在分组时选择栏还提供“移动到分组”（逐张调用 `PUT /api/graded-cards/{id}/group`）；游游亭批量查价不适用于评级卡。记录的 `display_name` 为用户设置的可选显示名称：设置后网格、列表、详情标题、删除确认和图片说明都显示该名称（鼠标悬停标题可看到证书卡名，详情页另显示“证书卡名”），搜索同时匹配显示名称和证书卡名，“卡名”排序按显示名称。详情页的价格与显示名称编辑位于正反面图片下方，两行按“标签 | 输入 | 保存”对齐，各自保存。评级卡分组与卡牌库分组相互独立（`graded_card_groups` 表，记录的 `group_id` 字段），界面复用 `group-ui.js` 的分组栏和对话框：选中分组后统计、筛选项、计数和列表只显示该组，“添加评级卡”默认放入当前分组；存在分组时列表显示“分组”列，网格卡片右下角和详情显示所在分组。评级卡图片为静态图片，不使用其他页面的鼠标跟随倾斜效果，点击仍可放大预览。`grading-scan.js` 在浏览器本地解码照片和摄像头帧，优先使用支持 QR 的 `BarcodeDetector`，否则按需加载自带的 Apache-2.0 `jsQR 1.4.0`（许可及来源位于 `static/collection/jsqr.js` 文件头）。不上传照片或扫码得到的 URL；只提交机构 ID 与编号。摄像头需 HTTPS/localhost 和权限，关闭弹窗或退出登录会释放视频轨道。

### 评级卡验收

在上文专用 PostgreSQL 测试环境中运行：

```powershell
python -m pytest -q tests/test_graded_cards.py tests/test_database.py tests/test_grading.py tests/test_cgc.py tests/test_ars.py
node --test tests/graded_cards.test.mjs tests/grading_scan.test.mjs
```

后端覆盖持久化、排序、账号隔离、写请求保护、失败保留快照、并发重复、刷新期间删除以及查询连接释放；前端覆盖队列、重试、超时、会话切换和扫码错误处理。全量回归命令见[测试](#测试)。

可重复的真实 Chromium 验收使用实际 WebUI、HTTP API 和隔离 PostgreSQL，仅替换邮件投递、机构查询和远端图片：

```powershell
python -m pip install -e ".[test,web,browser-test]"
python -m playwright install chromium
$env:TCG_BROWSER_TEST="1"
python -m pytest -q tests/test_graded_webui.py
$env:TCG_BROWSER_TEST="0"
```

该测试验证无卡片时保留筛选区（评级卡、卡牌库、收藏页）、官网链接识别、保存后重载、去重、ARS 原版卡名与英文信息折叠、PCG-CN 原名、评级卡图片无倾斜效果、查询中占位卡与已保存卡片尺寸一致、取消查询后服务端上游请求被关闭且不保存、真实 PNG 照片及合成视频帧经 Canvas/jsQR 解码、摄像头权限拒绝提示与关闭后轨道释放、ARS 无图片显示、筛选、排序、详情和图片预览、失败后刷新重试、删除确认、中英文及状态提示翻译、390px 移动布局和退出清理。截图默认保存在 `data/graded-webui-acceptance/`，可用 `TCG_BROWSER_OUTPUT` 改路径。合成视频不调用电脑实体摄像头；实物标签的对焦和扫码效果需在目标设备检查。真实机构网络验收使用下文独立开关。

2026-11-14 本地收尾验收结果（PostgreSQL 17 专用测试库）：

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 846 通过、5 项可选测试跳过；新增 schema 14→15 数据保留用例另行通过 |
| Node 全量回归 | 207 通过；修正扫码测试预期及 Canvas 替身，清理测试遗留计时器 |
| Chromium WebUI | 通过上述浏览器流程；修复切换语言后成功提示仍保留旧语言的问题 |
| 真实 PSA / CGC 查询 | 两项均 HTTP 200，各返回两张证书图片，经 Zyte 完成，PSA 约 14 秒、CGC 约 39 秒 |
| 真实 ARS 查询 | HTTP 200，保留编号前导零，等级 10，图片列表为空，直连约 1 秒完成 |

首次全量运行因 C 盘空间和内存分配不足中断；将本轮 `TEMP` / `TMP` 指向 D 盘后重新完成全量回归。上述通过数来自成功重跑，不含中断运行。测试未修改生产数据库。

### 即时证书查询

PCG-CN 使用机构 ID `pcgcn`，通过 `GET /api/grading/pcgcn/certificates/{cert_number}` 查询，也支持评级卡页面的新增、刷新和官网链接/二维码识别。后端直连 `https://card.pcgcard.cn/api/card/card/search_card_result/`，参数为 `pageNo=1&pageSize=1&proof_number=编号`，不使用 Proxy-Go、Token 或环境代理；20 秒超时，不重试、不跟随重定向。无需数据库迁移。

官方返回 `code=2000` 且证书编号匹配时，映射卡名、原文名称、系列、卡号、年份、语言、等级、Centering/Corners/Edges/Surface 分项与评级说明。等级分布置于 `population.grades`，总数为 `total_grading`。图片按官网轮播顺序保留，官网未标明正反面，`side` 为 `null`；页面按现有规则将前两张依次展示。物流单号和账号关联字段不保存。明确的 `code=400, data=null, msg=卡片不存在` 返回 404，其余异常结构为 `pcgcn_unreadable`；超时、限流和网络错误分别为 `pcgcn_timeout`、`pcgcn_rate_limited`、`pcgcn_unavailable`。

验收编号 `10198854`：SHIKIBE MAYU、CHAOS JPN. RIDDLE JOKER 1.0、YZ-411、2018，两张图片。离线用例见 `tests/test_pcgcn.py`，页面集成见 `tests/test_graded_webui.py`。

`GET /api/grading/{provider}/certificates/{cert_number}` 使用本站 Bearer 登录验证，响应为 `Cache-Control: no-store`。机构支持小写 `psa`、`cgc`、`ars` 和 `pcgcn`，实现统一位于 `grading/adapters.py`，通过适配器映射选择机构。证书编号作为字符串处理，去除首尾空白，接受 1–32 位 ASCII 数字，保留前导零。

配置服务端 `ZYTE_API_KEY`。PSA 查询通过 Zyte Extract API 抓取 `https://www.psacard.com/en-CA/cert/{cert_number}/psa`，请求 HTTP 响应正文并跟随目标重定向。密钥优先读取进程环境，再读取当前工作目录的 `.env`；显式空值报告配置错误。请求超时 220 秒、连接超时 5 秒，不自动重试，忽略系统代理环境变量。三份 Compose 均传入 `ZYTE_API_KEY`，修改配置后重新创建 Web 容器。

请求示例（`TCG_LOGIN_TOKEN` 为本站登录 Token）：

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/api/grading/psa/certificates/98428602' -Headers @{ Authorization = "Bearer $env:TCG_LOGIN_TOKEN" }
```

返回 `provider: "psa"`、`cert_number`、`source_url`、`certificate`、`images`。`source_url` 为上述抓取地址。`certificate` 根据网页生成：

- `title`：页面 `h1` 标题。
- `grade`：评级文本，例如 `"GEM MT 10"`。
- `fields`：Item Information 区域的原始标签和值，保留额外字段。
- `population`：`{"at_grade": 整数或null, "higher": 整数或null}`，分别为同等级和更高等级数量；页面未提供时为 `null`。

`images` 为 `[{"url": 图片src, "alt": 图片alt, "side": "front"或"back"或null, "thumbnail_url": 缩略图URL或null}, …]`。PSA 仅提取 `Cert image N` 证书图片，按 `Cert image 1/2` 标记正反面，缩略图字段为 `null`；不包含 Logo 或销售缩略图，也不下载图片。此接口返回上述网页结构，不再返回 PSA 官方 API 的 JSON。CGC 按 Obverse/Reverse 标记正反面，保留页面上与原图不同的缩略图 URL；ARS 返回空图片列表。

错误响应为 `{"detail":"错误代码"}`：

| HTTP | 错误代码 | 含义 |
| --- | --- | --- |
| 401 | `authentication_required` | 未登录本站 |
| 422 | `unsupported_grading_provider` / `invalid_cert_number` | 机构或编号错误 |
| 404 | `grading_certificate_not_found` | 页面明确返回无记录 |
| 429 | `grading_busy` | 当前账号或进程的并发查询已达上限 |
| 503 | `psa_not_configured` | Zyte 密钥未配置 |
| 504 | `psa_timeout` | Zyte 报告超时或网络请求超时 |
| 502 | `psa_authentication_failed` | Zyte 返回 401/403 |
| 502 | `psa_rate_limited` | Zyte 限流或目标站返回 429 |
| 502 | `psa_unreadable` | 非 HTML、页面无法解析或证书编号不匹配 |
| 502 | `psa_unavailable` | 其他上游请求错误 |

网关 HTTP 200 后仍检查目标站状态及页面内容。服务器日志记录 Zyte API 错误状态及原因，或成功响应的目标状态；客户端错误响应不包含凭据或上游错误正文。

`tests/test_grading.py` 使用模拟响应验证 PSA 网页解析和错误映射。`tests/test_grading_live.py` 按 PSA、CGC、ARS 参数化，从对应 TCG 接口调用开始访问真实官网；PSA/CGC 经过 Zyte，ARS 直连。每个机构默认跳过，独立开关启用。运行时使用上文的专用测试 PostgreSQL 配置及可用的 `ZYTE_API_KEY`，PSA 示例：

```powershell
$env:TCG_LIVE_PSA_TEST="1"
python -m pytest -q -s tests/test_grading_live.py
```

可通过 `TCG_LIVE_PSA_OUTPUT` 指定保存返回 JSON 的文件路径。验收编号 `98428602` 对应 CHITOSE TUTAMACHI、卡号 3955、2024 年、GEM MT 10，以及两张证书图片。

### CGC 网页查询

`GET /api/grading/cgc/certificates/{cert_number}` 沿用上述登录、编号校验和 `no-store` 规则。使用同一 Zyte 配置抓取 `https://www.cgccards.com/certlookup/{cert_number}/`。默认超时 220 秒、连接超时 5 秒，客户端不自动重试，Zyte 跟随目标重定向。

返回 `provider: "cgc"`、`cert_number`、`source_url`、`certificate`、`images`；`source_url` 为抓取地址。`certificate.title` 取 `Card Name`，`grade` 取 `Grade`，`fields` 保留证书信息的原始标签和值。当前页面的 Population 区域只有 JavaScript 异步模板，没有实际数量，因此 `population` 返回 `{"at_grade": null, "higher": null}`，不把缺失值当作零。

`images` 仅从证书图片区读取：`url` 为图片链接 `a.href` 指向的原图，`alt` 为图片的 `title`。返回 Obverse、Reverse 图片地址，不下载图片，也不包含 Add to Registry 示例图或 Logo。

配置、网络、网关和解析错误沿用 PSA 的 HTTP 状态映射，错误代码分别为 `cgc_not_configured`、`cgc_timeout`、`cgc_authentication_failed`、`cgc_rate_limited`、`cgc_unreadable`、`cgc_unavailable`。服务器日志保留请求 ID、目标状态、代理字节和尝试次数，客户端错误响应不公开凭据。

成功页面也包含未激活的“无记录”和限流模板，解析器不会据此报错。若返回的只是预填编号、等待 JavaScript 查询的入口页，返回 `502 cgc_unreadable`，不将其推断为证书不存在。

`tests/test_cgc.py` 使用真实页面片段进行离线测试，来源见 [fixtures 说明](../tests/fixtures/README.md)。测试证书 `6091052028` 对应 Tsuyogaru Otona no Secret Labo Mayu、Weiss Schwarz Rose、2025 年、Japanese、Yuzu Soft、卡号 R01-026SP、Special、PRISTINE 10，Grade Date 为 2025-11-19，含 Obverse 和 Reverse 两张图片。

真实端到端测试使用独立开关和输出路径：

```powershell
$env:TCG_LIVE_CGC_TEST="1"
python -m pytest -q -s tests/test_grading_live.py
```

可通过 `TCG_LIVE_CGC_OUTPUT` 指定保存返回 JSON 的文件路径。两个机构开关均为 `1` 时，该命令同时验证 PSA 与 CGC。

### ARS 网页查询

`GET /api/grading/ars/certificates/{cert_number}` 沿用上述登录、编号校验和 `no-store` 规则。使用普通 HTTP 请求 `https://ars-grading.com/grading/searchNumber?id={cert_number}`，不需要 Token 或浏览器。单次超时 20 秒，不重试、不跟随重定向。

返回 `provider: "ars"`、`cert_number`、`source_url`、`certificate`、`images`。ARS 的 `certificate` 结构为：

- `fields`：日英信息表的原始标签和值，保留额外字段。
- `grade`：唯一高亮等级的 `data-grade` 字符串，例如 `"10"` 或 `"10+"`。
- `population.total_grading` / `total_submission`：页面的鉴定总数和提交总数（整数）。
- `population.grades`：等级到原始统计文本的映射，例如 `{"10": "1（0）"}`；`population.remarks` 保留页面对统计的说明，不推断括号内数量。

首期 `images` 固定为 `[]`，因为实测查询页没有卡片图片，不返回网站 Logo。

明确的无记录提示返回 404 `grading_certificate_not_found`；超时为 504 `ars_timeout`；429 为 502 `ars_rate_limited`；其他 HTTP/网络错误为 502 `ars_unavailable`；非 HTML、编号回显不匹配、关键结构缺失或多项高亮为 502 `ars_unreadable`。错误响应不包含上游正文。

验收编号 `047088950114`：学生兼研究者 式部 茉優 / Student and Researcher, Mayu Shikibe，Lycee Overture，2019，LO-1606-S，Special Rare，高亮等级 10。采集时鉴定总数 1、提交总数 2；这些统计可能随上游更新。模拟用例在 `tests/test_ars.py`，正常及无记录页面来源见 `tests/fixtures/README.md`。

真实 ARS 查询可设置 `$env:TCG_LIVE_ARS_TEST="1"` 后运行 `python -m pytest -q -s tests/test_grading_live.py`；`TCG_LIVE_ARS_OUTPUT` 可指定返回 JSON 的保存路径。三个机构开关可同时启用。

## 商户查价接口

`GET /api/prices?merchant=yuyutei&game=ws&card_number=ABC-001` 查询指定商户的全部匹配报价。三个参数必填；卡号去除首尾空白后须为 1–100 字符，匹配时忽略大小写但保留完整编号及版本后缀。接口需要 Bearer 登录，响应均为 `Cache-Control: no-store`。

游游亭支持六个游戏，Hobby Station 支持 Lycee、WS、WSR，Card Max 支持 WS、SEV。游戏输入兼容卡牌库的名称和别名，例如 `WS`、`Lycee`、`ChaosTCG`；响应使用标准小写标识。`ws_en`、`WS EN`、`sev_en`、`SEV EN` 和其他不支持的游戏会被拒绝，不会回退到日版查询。

响应示例（示意价格）：

```json
{
  "merchant": "yuyutei",
  "game": "ws",
  "card_number": "ABC-001",
  "items": [{
    "name": "Card",
    "card_number": "ABC-001",
    "rarity": "SR",
    "price": 100,
    "currency": "JPY",
    "condition": "normal",
    "stock_status": "in_stock",
    "stock_quantity": 2,
    "product_url": "https://yuyu-tei.jp/sell/ws/card/set/001"
  }]
}
```

接口遍历所有搜索分页，返回普通品相的挂牌价格，包括缺货商品；不同版本、稀有度分别返回，同一商品链接去重并保留首次出现的顺序。`rarity` 和 `stock_quantity` 可为 `null`，`stock_status` 为 `in_stock`、`out_of_stock` 或 `unknown`。明确标记为普通品相时使用搜索页报价；品相不明时通过商品详情确认普通品相报价。伤品排除，零价格保留。没有匹配商品时返回 200 和 `items: []`。

HS 的 WS 和 WSR 报价支持店铺编号和官方完整卡号。遇到店铺省略系列的编号时，通过官方卡表核对版本，存在歧义时跳过。Card Max 使用标记 NM 的报价，只为匹配商品读取详情库存。

`GET /api/prices/merchants` 返回每个来源的 `id`、显示名称和支持的游戏。可选来源为 `yuyutei`、`hobbystation` 和 `cardmax`。

错误的 `detail` 为：未知商户 422 `unsupported_merchant`；商户不支持该游戏 422 `unsupported_merchant_game`；上游请求失败 502 `price_source_unavailable`；页面或商品详情无法解析 502 `price_source_unreadable`；上游超时 504 `price_source_timeout`。缺少参数或卡号校验失败返回标准 422 校验错误。商户及游戏能力检查在请求上游前完成；必要分页或详情请求失败时整次查价失败，不返回部分结果。

添加或编辑卡牌时可选择查价来源，默认游游亭。查价按钮按所选店铺支持的游戏启用。每次查询只展示该店的报价，同稀有度优先排列。点击报价会填入单价和币种，保存后写入卡牌库。切换来源、游戏或卡号会清除旧结果并取消进行中的请求。

卡牌库多选栏提供“查价”（批量查价）：弹窗中选择数据来源（按 `/api/prices/merchants` 列出，默认游游亭，并显示该来源支持的游戏）以及已有价格的卡牌“保留原价格”（默认）或“替换为查到的价格”，并预告将查价的张数及因已有价格或游戏不支持而跳过的张数。开始后逐张处理选中卡牌：按游戏、编号和稀有度（忽略大小写）匹配报价，同稀有度有多条报价时取最低价、优先有货，以报价币种通过 `PUT /api/cards/{card_id}` 写入单价（保留其他字段和分组）；同一游戏和编号在一次批量中只请求一次。弹窗显示进度，可“停止”（当前卡牌完成后停止，运行中不能用 Esc 或关闭按钮关闭），完成后按“已填入价格”“已有价格，未替换”“游戏不支持”“无同稀有度报价”“查询或保存失败”分别计数。已填入或按选择保留原价的卡牌取消选中，其余（含停止时未查询的卡牌）保持选中，便于重试或手动填写。

## 卡牌库价格字段

`POST /api/cards` 可在原卡牌字段中加入可选的 `price`，表示手动记录的单张价格，例如 `"price": 12.34`。允许数值或十进制数字字符串，范围为 0–999999999999.99，最多两位小数；负数、布尔值、非有限数和超出精度的输入返回 422。省略或传 `null` 表示尚未设置价格，零表示价格为 0。可选的 `currency` 指定单价币种，取值 `JPY`、`CNY`、`CAD`（不区分大小写，省略或 `null` 为 `JPY`），其他值返回 422；后端只保存原始金额和币种，不做换算。

卡牌新增、列表、编辑和移动接口返回 `price`（JSON 数值或 `null`）和 `currency`。`PUT /api/cards/{card_id}` 提交完整卡牌内容，`price` 显式传 `null` 或省略时清空，省略 `currency` 时使用 `JPY`。调用方调整数量时也须提交要保留的价格和币种。价格不参与卡牌去重，移动卡牌和删除分组保留价格。

JSON/XLSX 导入导出均支持 `price` 和 `currency`，导出时依次位于数量之后，表头支持 `price`／`Price`／“价格”及 `currency`／`Currency`／“币种”；导入缺少币种列时为 `JPY`，未知币种使整份导入失败。未设置价格时 JSON 为 `null`、XLSX 为空单元格；已设置价格以数值单元格导出。旧文件省略价格仍可导入，重复卡牌被跳过时保留原价格，任意行价格不合法则整份导入失败。`fields=price` 可单独选择价格字段。

迁移 `0012` 升级到 schema 12，为卡牌新增可空的 `NUMERIC(14,2)` 字段和非负约束；历史卡牌价格为 `null`，其他数据及分组保持原样。迁移 `0013` 升级到 schema 13，新增非空 `currency` 字段（默认 `JPY`）及取值约束，已有卡牌均记为 `JPY`。

WebUI 的添加／编辑卡牌对话框在“拥有量”同一行提供选填的“单价”输入（非负、最多两位小数，由浏览器校验），右侧可选币种，新卡牌默认 JPY；对话框与注册表单一致，用红色星号标出必填项（游戏、编号、名称、稀有度、拥有量），选填项不再附加“可留空”提示，以十进制字符串提交以保留精度，清空即发送 `null`；编辑对话框总是提交当前价格，单独调整数量时保留原价格。导出对话框可以选择“价格”和“币种”等字段，保存后按当前有效选择恢复；尚未配置或配置损坏时默认全选。

卡牌库价格与搜索、收藏一致跟随侧栏的显示币种：单价按汇率换算后显示，下方小字附录入的原金额（录入币种与显示币种相同时也显示）；卡牌库按需通过 `/api/currency/convert` 获取录入币种的汇率。滑条与价格排序按换算成日元后的金额比较，切换显示币种时已选范围不变、标签随之换算；取不到汇率的卡牌只显示原金额，不参与价格范围筛选，价格排序时排在最后。筛选区复刻收藏页的价格滑条：滑块按当前分组内已设置价格的不同取值分档，两端表示不限，与稀有度、排序及搜索同时生效，“重置”恢复不限；标签以显示币种表示。未设置价格的卡牌在不限时照常显示，设置任一边界后被排除。少于两个不同价格时滑条禁用。排序新增“价格升序／降序”，未设置价格的卡牌在两种方向下都排在最后。卡牌库的列表和网格与收藏页对齐，原购物车位置改为“持有”数量步进器（样式同购物车步进器）：`−`／`+` 各调整一张，也可直接输入数字后按回车或离开输入框保存，Esc 恢复原值；输入内容在列表重绘时保留，保存失败的原因显示在页面状态行。原“调整数量”弹窗随之移除。列表列依次为游戏、稀有度、编号、名称、单价、持有、备注、分组和操作，未设置单价显示显示币种占位，如“JPY -”；顶部统计在“卡牌种类”“总张数”旁新增“总价值”：对当前显示（随分组和筛选）且持有数量大于 0 的卡牌按单价 × 持有数量换算成显示币种合计，下方注明“已计入 15/50 种”，即有可比较价格的种类数／卡牌种类数，未设置单价或取不到汇率的卡牌不计入，全部不计入时显示“JPY -”；网格卡片依次为编号标签、图片、名称、单价、“持有”行，底部左侧为移动／编辑／删除，右下角为分组。为让各列在较窄窗口内不出现横向滚动，卡牌库表格的备注列最小宽度降为 64px、名称列降为 190px，单元格左内边距为 14px。

## 卡牌库分组

卡牌库采用与收藏夹相同的单层可选分组，分组独立保存在 `card_groups` 中。每张卡牌属于一个分组或未分组；历史卡牌和新建时未指定分组的卡牌为未分组；导入的新卡牌按文件中的分组名加入分组（见下文）。

WebUI 卡牌库沿用收藏页的分组栏样式与交互：页面顶部为“全部”及各分组（附卡牌条目数），可新建分组，选中分组后可改名或删除；未分组卡牌只在“全部”中出现。页面加载全部卡牌和分组后在前端按组筛选，统计、筛选项、计数及列表标题都跟随当前分组。存在分组时，每张卡牌提供“移动到分组”按钮，列表“分组”列和网格卡片右下角显示所在分组；“多选”与收藏页一致：可勾选或全选当前显示的卡牌，再逐张批量“移动到分组”（存在分组时可用）或“删除”（需确认，无法撤销）；完成的卡牌取消选中，失败的卡牌保持选中以便重试。收藏页的刷新、订阅类批量操作依赖商户商品链接，不适用于卡牌库。添加与编辑卡牌使用同一对话框、同一组字段：存在分组时都显示“分组”选择，新增时默认为当前所选分组，编辑时默认为卡牌所在分组，保存时一并提交。导出对话框可选择分组，默认为当前所选分组，并通过 `group_id` 参数只导出该组卡牌。

分组栏、名称对话框、删除确认及移动对话框由 `group-ui.js` 统一实现，收藏页和卡牌库分别以 `favorite`、`cards` 元素 ID 前缀挂载。

所有接口需要 Bearer 登录，写接口检查可信 `Origin` 和 JSON Content-Type，响应禁止缓存。

| 接口 | 请求与响应 |
| --- | --- |
| `GET /api/cards/groups` | 返回 `{"items":[分组记录,…]}`；按创建时间、ID 升序 |
| `POST /api/cards/groups` | `{"name":"套牌"}`；返回 201 和分组记录 |
| `PUT /api/cards/groups/{group_id}` | `{"name":"新名称"}`；返回 200 和分组记录 |
| `DELETE /api/cards/groups/{group_id}` | JSON `{}`；将其中卡牌改为未分组后删除分组，返回 204 |
| `GET /api/cards?group_id=…` | 返回该组卡牌；省略参数时返回全部卡牌，保持创建时间、内部排序号倒序 |
| `POST /api/cards` | 原卡牌字段加可选 `group_id`；省略或 `null` 表示未分组，返回 201 和卡牌记录 |
| `PUT /api/cards/{card_id}/group` | `{"group_id":"目标组 ID"}`；`null` 表示移出分组，返回 200 和卡牌记录；必须提供该字段 |
| `GET /api/cards/export?format=xlsx\|json&group_id=…` | 导出指定组卡牌，可与现有 `games`、`fields` 参数组合；`fields` 可选 `group` |

分组记录包含 `id`、`name`、`created_at`（Unix 秒）。卡牌接口的记录增加 `group_id`，未分组为 `null`。`PUT /api/cards/{card_id}` 可选携带 `group_id`：传入时在同一事务中把卡牌移入该组（`null` 为移出分组，不属于本账号的分组返回 404 `card_group_not_found`），省略时保留原分组，兼容已有编辑请求；仅移动分组可使用专用接口 `PUT /api/cards/{card_id}/group`。移动及删除分组保留卡牌 ID、数量、备注、图片和创建／更新时间。卡牌去重仍按账号内规范化后的游戏、卡号、稀有度判断，跨组重复新增仍返回 409 `card_duplicate`。

组名去除首尾空白后须为 1–100 个字符，同账号内唯一且区分大小写。无效输入返回 422；重名返回 409 `card_group_name_taken`；不存在或属于其他账号的分组返回 404 `card_group_not_found`，卡牌不存在仍返回 404 `card_not_found`。收藏夹分组不能作为卡牌库分组使用。

分组写入、新增和移动卡牌先锁定用户行。删除分组与解除卡牌归属在同一事务内完成；复合外键保证卡牌只能引用同账号分组。迁移 `0011` 新增分组表、可空 `group_id`、复合外键及索引，升级到 schema 11，保留历史卡牌的全部字段和排序。

导出增加 `group` 字段，位于备注之后，记录分组名称而非 ID，使文件可在不同账号间恢复分组；未分组时 JSON 为 `null`，XLSX 为空单元格。XLSX 表头为 `group`、`Group` 或“分组”，三者导入时均可识别；导出对话框的“字段”中可取消“分组”。

导入时“分组”列可省略。新导入的卡牌按组名（去除首尾空白，区分大小写）加入当前账号的同名分组，不存在的分组自动创建，新分组按文件中首次出现的顺序排列；空白或缺失为未分组。跳过的重复卡牌保留原分组和内容，只被重复卡牌引用的分组不会创建。组名须为不超过 100 字符的文本，XLSX 中的整数按文本处理；不合法时整份文件以 422 `cards_import_row` 拒绝，不写入任何卡牌或分组。导入在锁定用户行后插入卡牌并在同一事务中创建分组、设置归属，响应仍为 `{"imported":…,"skipped":…}`。

## 登录会话与并发

每账号最多保存一条登录会话，`sessions_user` 是 `sessions.user_id` 上的唯一索引。登录在用户行锁内校验密码、撤销该账号旧会话并创建新会话，三者在同一事务中完成。并发登录按取得锁的顺序执行，最后一次成功签发的会话有效；密码错误不会影响已有会话。注册成功自动创建首个会话。

会话从签发起固定 7 天有效，不自动续期。旧 Token 再次请求受保护接口返回 `401 authentication_required`；退出仅撤销请求携带的 Token，旧设备的退出请求不会注销新会话。改密和密码重置继续撤销该账号全部会话。

迁移 `0014` 对已有会话按 `expires_at` 降序保留每账号一条；同秒签发的会话按 `token_hash` 降序确定保留项，再建立唯一索引。单会话账号保持登录，多会话账号其余设备在下次请求时需要重新登录。重复执行迁移不会更换保留的会话。

前端用 `localStorage` 共享同源标签页的 Token，收到其他标签的存储事件后恢复对应会话。受保护请求同时校验开始时的会话代次和 Token；旧会话迟到的响应被丢弃，避免在存储事件到达前清除新会话。登录和退出响应也检查请求开始时的 Token。

单会话减少多个设备同时操作的机会；同会话的多个标签页仍可并发，已经通过鉴权的在途请求会继续完成。购物车的会话锁与幂等记录、卡牌和收藏的事务锁继续负责业务并发一致性。

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
当前使用 PostgreSQL schema 16。业务接口保留现有 SMTP 配置；旧 SQLite schema v8 不再由应用读取。

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

默认使用 `sessionStorage`，由 `static/cart/yuyutei-client.js` 提供独立客户端模块。
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

三个购物车页均可在列表或网格中单张获取库存，也可以点页首的“获取库存”查询当前店铺整车。游游亭通过 `/api/yuyutei/product?gid=…&ver=…&cid=…&stock=true&kizu=…` 读取指定品相的当前库存。Hobby Station 和 Card Max 分别通过 `GET /api/hobbystation/product/stock` 和 `GET /api/cardmax/product/stock` 查询，参数 `product_url` 取自购物车行。这两个只读接口使用本站 Bearer 登录，复用订阅的商品详情查询，无需店铺购物车凭据。批量查询最多并发三项，按商品去重，游游亭区分品相。查询失败可单独重试。库存查询保留购物车原有数量。

首次打开购物车时，页面优先使用已有的商品信息。Hobby Station 和 Card Max 缺少卡号、稀有度或游戏时，会从各自的商品详情补全。搜索页按勾选的店铺分别显示购物车数量和小计，每个摘要可以打开或刷新对应的购物车。

Hobby Station 的搜索结果和购物车在有货时统一显示“有货”。悬停可看详情页给出的具体张数或库存标记。Card Max 和游游亭有确切数量时直接展示数量。三家都区分售罄和未知。


## Hobby Station 销售购物车后端

所有接口均需本站 Bearer 登录、受信任的 `Origin` 和 JSON 请求体。每次请求在 `credentials.eccube` 传入会话 Cookie 的值。加购需要读取商品详情页；若触发店铺访问检查，还需提供浏览器当前的 `credentials.eccube_bot_check`。后端只向商品详情页发送该 Cookie，并从同一会话的页面读取操作令牌。购物车沿用搜索的加密代理或直连配置。

| 方法与路径 | 除 `credentials` 外的 JSON 字段 | 行为 |
| --- | --- | --- |
| `POST /api/hobbystation/cart/query` | 无 | 查询销售购物车，返回 `items`、`total_quantity`、`subtotal`、`currency`、`complete` 和 `warnings` |
| `POST /api/hobbystation/cart` | `product_url`、可选 `counter`（1–99，默认 1） | 从搜索结果的商品详情链接读取规格和表单，增加指定张数，支持 `Idempotency-Key` |
| `PUT /api/hobbystation/cart/quantity` | `product_class_id`、`quantity`（0–99） | 把已有行调到目标数量，逐次回读；0 移除整行 |
| `DELETE /api/hobbystation/cart` | `product_class_id` | 移除指定行；行不存在时返回 404 |
| `DELETE /api/hobbystation/cart/clear` | 无 | 清空当前会话的销售购物车；已经为空时直接返回成功 |

加购请求示例使用占位凭据。

```json
{
  "credentials": {"eccube": "<session-cookie>", "eccube_bot_check": "<browser-access-cookie>"},
  "product_url": "https://www.hobbystation-single.jp/lo/product/detail/305273",
  "counter": 1
}
```

商品行同时返回字符串 `product_id` 和 `product_class_id`。修改或删除时使用后者。写接口成功时返回已核对的 `cart`。数量调整中途失败时，`last_verified_quantity` 表示最后一次已确认的数量，重新查询可获得当前状态。凭据限于本次请求；幂等记录保存业务参数摘要和结果。

加购响应中的 `done: false` 如果附有库存不足或单次限购提示，接口返回 HTTP 409 和 `hobbystation_stock_exceeded`。商品旁的气泡按 `detail.reason` 显示原因：`stock` 表示购买数量超过可用库存，`purchase_limit` 表示超过单次限购上限。旧响应缺少原因时显示“已达到库存或购买上限”。其他业务拒绝仍返回 `hobbystation_cart_rejected`。

WebUI 的搜索结果和收藏夹中，Hobby Station 商品使用与游游亭相同的数量控件。`static/cart/hs-cart.js` 把该接口适配到 `cart.js` 的购物车状态：商品键为 `gid: 'hs'` 加商品 ID，购物车行的 `type` 为 `product_class_id`。加购回复自带核对后的购物车，因此不再重新查询。凭据错误、访问检查等账户级问题以页面提示显示。

侧边栏的“购物车”按来源展开为 `#cart`（游游亭）、`#cart-hobbystation` 和 `#cart-cardmax`。三者共用 `static/cart/cart-page.js` 与 `#cart-panel`，进入时重新读取所选店铺的购物车。列表和网格都支持查询库存、调整数量、确认移除和前往对应官网结算。Hobby Station 商品图片通过 `/api/images/hobbystation/…` 转发显示。菜单上的数字为三家购物车数量之和，展开后显示各店数量。竖屏手机上，三个购物车直接列在分组下拉中。

用户中心的“Hobby Station 登录凭据”面板要求同时填写 `eccube` 和 `eccube_bot_check`，并附打码的 DevTools 截图（`static/hs-help-cookies.webp`）。前端 `static/cart/hobbystation-client.js` 把两个值按当前账号存入本标签页的 `sessionStorage`（`tcg.hobbystation.credentials`），切换账号、退出登录或关闭标签页即清除；`static/cart/hs-cart-settings.js` 保存后调用 `/query` 验证，页面重新加载时会再验证一次。

## Card Max 销售购物车

在用户中心填写 `tempid` 和 `login_id` 的值，再分别填写会员 Cookie 的名称（如 `123_key`）和值。保存并验证后，即可在搜索结果和收藏中使用加减按钮或输入目标数量，也可在“购物车 → Card Max”查看整车。凭据保存在当前标签页的 sessionStorage，与本站登录账户绑定。

后端直连 `www.cardmax.jp`。所有接口要求本站 Bearer 登录、可信 Origin 和 JSON 请求体。每次请求都提交 `credentials`，其中 `tempid` 和 `login_id` 填 Cookie 值，`member_key` 填完整的 `<会员编号>_key=<值>`。后端仅发送这三项 Cookie，并核对购物车页的会话标识和会员登录状态。

| 方法与路径 | 业务字段 | 返回结果 |
| --- | --- | --- |
| `POST /api/cardmax/cart/query` | 无 | 商品行、数量、日元小计及解析状态 |
| `POST /api/cardmax/cart` | `product_url`、可选 `counter`（1–99） | 核对后的加购增量和购物车；支持 `Idempotency-Key` |
| `PUT /api/cardmax/cart/quantity` | `brandcode`、`quantity`（0–99） | 目标数量及最新购物车，设为 0 移除该行 |
| `DELETE /api/cardmax/cart` | `brandcode` | 移除指定行并回读核对 |
| `DELETE /api/cardmax/cart/clear` | 无 | 清空并确认当前会话的购物车 |

`brandcode` 是保留前导零的字符串，可从查询结果读取。商品页和购物车使用 EUC-JP 表单，修改时提交该行最新的原数量和选项。购物车行价格是整行小计，后端按数量还原单价。对于需要另选规格的商品或同编号匹配多行的购物车，请在 Card Max 页面操作。写入后的数量与预期不符时返回 409 和实际数量。

## 卡牌订阅后端

### 邮件样式与本地预览

邮件分为验证码和订阅通知两类（`mail/templates.py`），共用品牌行、页脚及适配窄屏的表格布局，配色沿用网页的中性灰，使用内联样式，无外部字体、图片或脚本依赖。两类邮件的主题、HTML 与纯文本均为中英双语，中文在前、英文在后；验证码申请中的 `language` 参数仍被接受，但不再影响邮件语言。五种验证码用途共用验证码模板，正文只说明用途、有效期和验证码。订阅通知标题下只显示本批最近一次检查时间；每张卡显示名称（链接到详情页）、稀有度标签、编号及价格／库存的前后对比：变好为绿色、变差为琥珀色，未变化项只显示当前值；邮件底部（HTML 与纯文本）提供前往本站收藏夹的链接，地址由 `TCG_SITE_URL` 决定。两类邮件均发送 `multipart/alternative`，同时保留纯文本正文。

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

网页入口位于用户中心的“订阅通知邮箱”卡片（`static/account/notification-email-ui.js`）：显示当前生效地址及来源（账户邮箱／自定义／未设置），展开后输入新地址、发送验证码并确认；使用自定义邮箱时可一键改用账户邮箱。登录、绑定账户邮箱或修改设置后，收藏夹的订阅按钮和提示按当前生效地址更新，无需重新登录。迁移 `0003` 将两个新字段初始化为空，现有用户继续使用注册邮箱。部署此版本前执行 `tcg-db migrate`，本次代码变更本身不修改运行中的 Docker 数据库。


订阅独立于收藏夹，按收藏的商户商品链接跟踪报价。游游亭跟踪普通品相 `kizu=0`，Hobby Station 和 Card Max 跟踪对应商品详情页。所有接口需要 Bearer 登录；写入沿用可信 `Origin` 和 `Content-Type: application/json` 检查，响应均为 `Cache-Control: no-store`。

网页目前只在收藏夹页面提供入口：每张已收藏卡牌的刷新按钮旁有铃铛按钮，前端状态由 `static/subscriptions/subscriptions.js` 管理，按游戏和去掉尾部 `/` 的链接与收藏匹配。没有有效通知邮箱时铃铛禁用，并在页面上提示前往用户中心；有效时提示中显示收件地址。绑定邮箱或设置通知邮箱后无需重新登录。已订阅卡牌显示最近检查时间，`last_error` 非空时提示上次检查失败。收藏夹筛选面板额外提供“订阅”筛选（全部／已订阅／未订阅）；共享筛选面板仅在传入 `isSubscribed` 时显示该项，搜索页不受影响。取消收藏接口在同一个数据库事务中删除收藏和当前用户对应的订阅。匹配使用游戏和去掉尾部 `/` 的链接。任一步失败都会回滚。

| 接口 | 请求与响应 |
| --- | --- |
| `POST /api/subscriptions` | `{"game":"wsr","product_url":"https://yuyu-tei.jp/sell/wsr/card/OS01/10240"}`；继承当前用户的查询间隔；返回 200 和订阅记录 |
| `GET /api/subscriptions` | 返回 `{"items":[订阅记录,…]}`，仅包含当前用户的订阅 |
| `GET /api/subscriptions/settings` | 返回当前用户的 `{"interval_hours":24,"next_check_at":1800086400}`，无需已有订阅或已验证邮箱 |
| `PUT /api/subscriptions/settings` | `{"interval_hours":6}`；统一修改当前用户全部已有及未来订阅的间隔，返回与 GET 相同的设置 |
| `DELETE /api/subscriptions/{id}` | 发送 JSON `{}`；取消自己的订阅，成功返回 204，不存在或属于其他用户返回 404 |

订阅记录为 `id`、`item`、`created_at`、`checked_at`、`last_error`、`next_check_at`。`item` 包含 `game`、`name`、`card_number`、`rarity`、`image_url`、`product_url`、`price`、`currency`、`stock_quantity`、`stock_status`、`stock_text`；时间均为 Unix 秒。`checked_at` 为最近检查尝试所属的计划扫描时间（整点扫描即该整点，启动扫描为实际开始时间），失败时仍保留上次有效 `item`，错误代码放入 `last_error`。

创建时要求存在有效通知邮箱（已验证自定义邮箱或已验证注册邮箱），否则返回 403 `verified_email_required`。支持游游亭六个游戏、Hobby Station 的 Lycee、WS、WSR 和 Card Max 的 WS、SEV。使用收藏模块的 HTTPS 商品链接校验规则；去掉尾部 `/` 并统一主机名。查询参数、片段、其他域名、额外路径或编码路径不接受。名称、价格、库存由服务端获取，不采信客户端快照。

相同用户、游戏、链接重复创建返回原记录，不再次抓取、不重置基准或已有调度；初次读取失败不创建订阅。上游超时返回 504 `product_timeout`；HTTP 错误返回 502 `product_unavailable`；无法确定商品价格或有货状态返回 502 `product_unreadable`。

设置 `TCG_SUBSCRIPTIONS_ENABLED=true` 后，FastAPI 启动后台线程，启动时扫描一次，此后按进程本地时间每小时整点扫描 `next_check_at <= 本轮开始时间` 的订阅。整点扫描即使因唤醒抖动或等待邮件锁而晚 10 分钟内开始，`checked_at` 和下次时间仍按该整点计算，避免 `HH:00:01` 开始的扫描让下一次错过 `HH:00:00` 而整体顺延一小时。每轮仅对到期订阅按游戏和链接去重，同卡未到期订阅不更新。Hobby Station 和 Card Max 只提供有货标记时，保存有货状态和空数量。与上次成功检查（首次为订阅时）的日元价格、库存数量和有货/缺货状态比较，元数据或库存文案变化不会触发邮件。未知库存、404 和解析失败保留基准，其他卡牌继续处理。

成功检查订阅时，复用本次抓取结果同步同用户、同游戏和同商品链接的收藏，匹配链接时忽略尾部 `/`。即使价格、库存没有变化，也更新收藏快照和 `refreshed_at`（与订阅 `checked_at` 相同）；保留收藏 ID、分组、创建时间、原商品链接和购物车信息。游游亭伤品收藏（`cart.kizu=1`）不使用普通品相数据更新；检查失败时保留收藏原数据和刷新时间。仅更新已有收藏，不自动创建收藏。

按用户事务一并保存订阅快照、对应收藏及邮件待发记录，每轮每位用户最多生成一封中英双语汇总，同一天可有多封，列出所有成功检测到的变化及详情链接；无变化不发。投递时读取当前有效通知邮箱发送纯文本及 HTML 邮件；发送前剔除已经取消的订阅，剔除后为空则标记 `cancelled`。SMTP 在数据库事务外执行，沿用 `TCG_SMTP_*`，使用商户公开页面，无须购物车凭据。

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
XLSX 的 `Cards` 工作表以字段名作为表头，包含游戏、卡号、稀有度、名称、数量、备注、分组名、图片 URL、
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
