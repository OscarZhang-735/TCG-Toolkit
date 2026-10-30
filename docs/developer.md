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

用户、收藏、个人卡牌、购物车幂等记录和汇率数据保存在 `currency-data` 数据卷中，重建镜像不会丢失。备份时先 `docker compose stop web`，
复制数据卷内容后再 `docker compose start web`；不要删除该数据卷。

如果 Windows 占用了 8000 端口，在 `.env` 中设置 `TCG_PORT=18080`，并同步把 `TCG_TRUSTED_ORIGINS`
改为 `http://localhost:18080`，然后访问 http://localhost:18080 。

### 直接运行 Python

需要 Python 3.11 或更新版本。

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -e ".[web]"
Copy-Item .env.example .env
uvicorn tcg_toolkit.web:app --app-dir src --port 8000 --env-file .env
```

打开 http://localhost:8000 。数据默认保存在项目目录的 `data/` 下。

### 配置项

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TCG_TRUSTED_ORIGINS` | 允许登录的访问地址，多个用逗号分隔，不含路径 | 空（不允许登录） |
| `TCG_PORT` | Docker 映射到本机的端口 | `8000` |
| `RAPIDAPI_KEY` | 汇率接口密钥；不填时只能显示日元 | 空 |
| `TCG_SEARCH_PROXY_URL` | 游游亭转发接口（搜索、商品详情、凭据验证和购物车）；显式设为空可直连 | `http://62.234.212.90:8080/proxy` |
| `TCG_USER_DB` | 用户、收藏、个人卡牌与购物车幂等记录数据库路径（直接运行时） | `data/users.sqlite3` |
| `TCG_CURRENCY_CACHE` | 汇率缓存路径（直接运行时） | `data/currency.sqlite3` |
| `TCG_PROXY_IPS` | 受信任的反向代理来源 IP，不要设为 `*` | `127.0.0.1` |
| `TCG_SUBNET` | Docker 网络子网，与本地网络冲突时修改 | `10.203.0.0/24` |

`.env` 已被 Git 忽略，请勿提交密钥。公网部署请参阅 [部署手册](../deploy/README.md)。

### 测试

```powershell
python -m pytest -q tests
node --test tests/*.test.mjs
```

## 邮箱注册与验证

新用户必须验证邮箱才能创建账户；用户名和密码规则保持不变。注册成功仍返回 `{user, token}` 并自动登录。
登录请求的 `username` 字段接受用户名或已验证邮箱。`user` 和 `/api/auth/me` 增加 `email`、`email_verified`；
老账户分别为 `null`、`false`，可正常登录及在用户中心补绑。当前不提供更换邮箱、免密登录或密码找回。

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

`code` 是保留前导零的六位数字字符串；验证码 10 分钟有效，最多错误 5 次，成功后不可重用。
邮箱去除首尾空白、规范化后转为小写，不折叠点号或 `+` 别名，不执行 DNS 查询。
同邮箱两种用途共享 60 秒发送间隔及每小时 5 次配额（发送失败也计入）；配额和验证码摘要持久化。
IP 限制为各发送接口每分钟 5 次、注册／绑定验证及登录接口每分钟 10 次；IP 限制为现有进程内实现。
`429` 的 `Retry-After` 表示需等待秒数，CORS 向跨域前端开放此响应头。

错误码包括 `username_taken`、`email_taken`、`email_already_bound`（409）；
`invalid_code`、`code_expired`、`code_attempts_exhausted`、`invalid_password`（400）；
`validation_error`（422）、`rate_limited`（429）及上述邮件错误（503）。
用户名或邮箱冲突不消耗有效验证码；成功重发使同邮箱、用途和绑定账户的旧码失效，发送失败保留旧码。
验证记录绑定用途和账户，注册／绑定与验证码消费在同一事务完成。响应不回显敏感输入；不要在代理或日志中记录认证请求体。

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

- 键按本站登录用户隔离；服务端在接触上游前将占用记录提交到 SQLite，原子地防止同键并发执行。
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

用户数据库自动升级至版本 7：删除旧 `yuyutei_credentials` 表，并使用 SQLite secure_delete 和 VACUUM 清理现有及空闲页中的旧凭据。
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


## WS 英文版卡牌库

`WS EN` 是独立于 `WS` 的卡牌库品牌，接口标识及稀有度映射键为 `ws_en`。
添加卡牌时通过 `/api/official/ws_en/search?word=BD%2FWE34` 查询英文官网
`https://en.ws-tcg.com/cardlist/searchresults/?keyword=`，支持常规关键词匹配，无需完整卡号。
候选列表显示前 5 条及总匹配数，可填入名称、稀有度和图片；徽章沿用 WS 图标。
不接入游游亭价格搜索、收藏刷新或购物车。日英文卡牌独立存储、筛选和统计。

游游亭购物车使用与搜索相同的代理开关。代理需支持方法、正文、Cookie、CSRF 请求头、状态码和 Set-Cookie 的透传（Lite-Proxy 协议）；不会在代理失败时回退直连或自动重试购物车写入。Cookie 仍按游游亭域名维护，并仅保留在当前操作内。当前默认代理入口为 HTTP，该传输段不加密。
