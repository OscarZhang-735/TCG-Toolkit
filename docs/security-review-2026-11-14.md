# TCG Toolkit 安全检测报告

检测日期：2026 年 11 月 14 日（America/Toronto）。

代码基线：`f2c5639fd6c318f11ac9ed4c87cf0e787c8b6d5e`。报告面向项目维护者，记录本次源码审查、本地内存验证及依赖漏洞核对的结果。业务代码未修复，未执行文件删除、Git commit、push 或 PR。

本次确认一项中风险导入资源耗尽问题、一项中风险条件性 SSRF，以及一项低风险邮箱枚举问题。另确认旧 `.venv` 中的依赖漏洞仍可复现，但文档指定的 `venv` 未复现这些漏洞。头像存储的实际权限暴露仍待核实。

## 检测范围与判定口径

覆盖 SQL 注入、命令注入、XSS、路径穿越、SSRF、鉴权与越权、CSRF、会话与验证码、文件上传、Excel 公式注入、资源耗尽、敏感信息及相关部署配置。

- **已确认问题**：已定位完整代码路径，并通过本地受控输入复现相应行为。
- **已确认条件性问题**：代码行为已复现，但实际攻击还依赖明确的网络或运行环境条件。该条件列在问题本身，不等同于已经攻破生产环境。
- **待核实风险**：代码事实已确认，但实际权限、网络策略或部署状态不足以判断是否可被利用。
- 风险等级按本项目的攻击前提和影响判断；不是对每项问题重新计算的 CVSS 分数。

验证使用内存 SQLite、假 SMTP、HTTP MockTransport、FastAPI TestClient 和小规模解析探针。SQL 查询另以 PostgreSQL 方言检查参数绑定。未运行真实 PostgreSQL 集成测试或并发锁验证，未向线上服务、真实 COS 桶或第三方目标发送攻击请求，也未进行压力测试。依赖核对针对相关组件，不代表完整依赖清单的漏洞扫描。

### 环境区别

| 组件 | 文档指定的 `venv` | 遗留 `.venv` |
| --- | --- | --- |
| FastAPI | 0.162.0 | 0.115.6 |
| Starlette | 1.10.0 | 0.41.3 |
| Uvicorn | 0.54.0 | 0.34.0 |
| openpyxl | 3.1.5 | 3.1.5 |

[开发说明](developer.md) 指定使用 `venv`。上述版本来自本地已安装环境，不代表线上容器的实际版本。源码依赖声明允许一段版本范围，不能据此认定部署环境已经更新。

## 风险总览

| 编号 | 风险等级 | 状态 | 问题 | 主要前提 |
| --- | --- | --- | --- | --- |
| SEC-01 | 中 | 已确认 | XLSX 稀疏行导致无界遍历 | 普通登录用户可导入文件 |
| SEC-02 | 中 | 已确认条件性问题 | PM 明文上游的跨域重定向可触达内网 | 能篡改上游 HTTP 响应或控制上游 |
| SEC-03 | 低 | 已确认 | 注册和密码重置可枚举邮箱 | 邮件功能已配置，匿名访问相应接口 |
| ENV-01 | 高 | 旧环境已确认；部署使用情况待核实 | 旧 Starlette 的 Range DoS、Windows UNC 风险及 Host 限流绕过 | 使用旧 `.venv` 运行；各子项另有条件 |
| CFG-01 | 待核实；若匿名写入开放则为高风险 | 待核实风险 | COS 写入和删除没有请求签名 | 取决于真实桶 ACL、桶策略及来源网络限制 |

## 已确认问题

### SEC-01 XLSX 稀疏行导致无界遍历

**风险等级：中。状态：已确认，标准 `venv` 可复现。**

位置：[card_transfer.py](../src/tcg_toolkit/card_transfer.py) 第 101–113 行；入口为 [cards.py](../src/tcg_toolkit/cards.py) 第 124–134 行的 `POST /api/cards/import?format=xlsx`。

`sheet.reset_dimensions()` 之后调用 `sheet.iter_rows()`，仅限制列数，没有限制逻辑行号或遍历行数。openpyxl 会为稀疏行号之间的空白区域逐行产生结果。10 MiB 上传上限和 64 MiB ZIP 解压体积上限均不能约束这种逻辑展开。

**复现步骤：**

1. 在内存中构造 XLSX，第一行为合法字段名，第二行为一条合法卡牌数据。
2. 修改 `xl/worksheets/sheet1.xml`，将第二行及其单元格的行号改为 `1000001`，保持文件体积很小。
3. 调用实际的 `xlsx_import_rows()`，观察只有一条数据却遍历百万行。
4. HTTP 入口使用普通账户 Bearer token、可信 Origin 和 XLSX Content-Type；路由将解析交给通用线程池。复现解析问题不需要实际导入数据库。

在项目根目录，用 `venv\Scripts\python.exe -B -` 从标准输入执行以下 Python 代码即可进行内存复现：

```python
import re
import sys
import time
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import xlsxwriter

sys.path.insert(0, "src")
from tcg_toolkit.card_transfer import xlsx_import_rows

source = BytesIO()
with xlsxwriter.Workbook(source, {"in_memory": True}) as book:
    sheet = book.add_worksheet("Cards")
    sheet.write_row(0, 0, ["game", "card_number", "rarity", "name"])
    sheet.write_row(1, 0, ["WS", "AUDIT-001", "R", "Audit card"])

modified = BytesIO()
ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
with ZipFile(BytesIO(source.getvalue())) as src, ZipFile(modified, "w", ZIP_DEFLATED) as dst:
    for entry in src.infolist():
        content = src.read(entry.filename)
        if entry.filename == "xl/worksheets/sheet1.xml":
            tree = ET.fromstring(content)
            row = tree.find("s:sheetData/s:row[@r='2']", ns)
            row.set("r", "1000001")
            for cell in row:
                cell.set("r", re.sub(r"\d+$", "1000001", cell.get("r")))
            content = ET.tostring(tree, encoding="utf-8")
        dst.writestr(entry, content)

payload = modified.getvalue()
started = time.perf_counter()
rows = list(xlsx_import_rows(payload))
print("bytes:", len(payload), "data rows:", len(rows))
print("reported row:", rows[0][0], "seconds:", time.perf_counter() - started)
```

**实际结果：** 原始审查样本为 5,352 字节，解压 13,351 字节，唯一数据行位于第 1,000,001 行，解析约 0.484 秒。字节数和耗时随样本及机器变化。十亿行索引也被解析器接受；该极端样本仅受控生成前 100,000 个空行验证，没有执行完整十亿行处理。

**影响：** 已登录攻击者可用很小的文件长期消耗 CPU；并发导入还会占用其他 API 共用的线程池。

**修复建议：** 为导入设置业务可接受的逻辑行号和总遍历行数上限，超限明确拒绝。检查必须包含空行，不能只计数有效卡牌。可在正式解析前检查工作表 XML 行号；不要仅截断数据后返回成功。

**后续验收：** 正常稀疏表格仍可导入；超限行号在有界工作量内返回错误，且没有部分数据写入。

### SEC-02 PM 明文上游可被重定向到内网

**风险等级：中。状态：代码路径已确认，利用依赖上游响应可被篡改。**

位置：[official.py](../src/tcg_toolkit/official.py) 第 146 行和第 177–178 行；入口为 `GET /api/official/pm/search?word=...`。

PM 搜索请求发往明文 HTTP 地址，客户端启用 `follow_redirects=True`，未逐跳校验重定向的协议、目标主机或地址范围。

**复现步骤：**

1. 用 MockTransport 模拟固定 PM 上游，返回 `302 Location: http://127.0.0.1:8000/api/health`。
2. 对第二次模拟请求返回能被 PM 解析器接受的 HTML。
3. 调用实际 `search_pm()`，检查请求记录是否包含 loopback 地址。所有响应均由内存 mock 提供。

以下代码使用标准 `venv`，可从项目根目录以标准输入执行，不产生真实网络请求：

```python
import sys
from unittest.mock import patch
import httpx

sys.path.insert(0, "src")
from tcg_toolkit import official

seen = []
def respond(request):
    seen.append(str(request.url))
    if request.url.host == "www.p-memories.com":
        return httpx.Response(302, headers={
            "Location": "http://127.0.0.1:8000/api/health"
        })
    assert str(request.url) == "http://127.0.0.1:8000/api/health"
    return httpx.Response(200, text='<input name="keyword_card">')

original_client = httpx.Client
transport = httpx.MockTransport(respond)
def mock_client(**kwargs):
    return original_client(transport=transport, trust_env=False, **kwargs)

with patch("tcg_toolkit.official.httpx.Client", side_effect=mock_client):
    print(official.search_pm("audit"))
print(seen)
```

**实际结果：** 原始 API 级 mock 验证在认证替身下返回 200，请求记录包含第二次对 loopback 的访问。上述函数级复现展示相同的重定向路径。真实内网返回内容可能导致解析器最终返回 502，但这发生在请求已经发出之后。

**攻击前提与影响：** 攻击者需要控制上游或能够篡改这条 HTTP 链路。满足该条件时，可诱导后端向其可达的内网服务发起 GET 请求。普通用户不能仅通过 `word` 参数直接指定任意 URL；本次也未证明任意内网响应可以被完整读取。

**修复建议：** 优先使用经过证书验证的 HTTPS 上游；禁用不必要的重定向，或对每一跳限制协议、主机及目标地址。若上游必须使用 HTTP，仍应拒绝跨域及指向 loopback、私网或链路本地地址的重定向。

**后续验收：** 模拟跳转至 `127.0.0.1`、私网及其他非许可主机时，不发出第二次请求；正常上游查询保持可用。

### SEC-03 注册与密码重置可枚举邮箱

**风险等级：低。状态：已确认。**

位置：[email_verification.py](../src/tcg_toolkit/auth/email_verification.py) 第 55–60 行、第 70–79 行。

注册验证码接口直接区分邮箱是否存在。密码重置对不存在或未经验证的邮箱提前返回，没有走后续邮箱配额逻辑，形成第二种状态差异。

**复现前置条件：** 使用隔离测试实例、假 SMTP，以及至少 32 字节的测试验证码密钥。准备一个已验证的测试账户邮箱和一个不存在的合法邮箱。请求带有可信 Origin 和 `Content-Type: application/json`；非浏览器客户端可以自行设置 Origin，此步骤不要求登录。每组实验使用干净的邮箱配额和新的内存 IP 限流器，避免前一组的发送记录及共享的 `email-code` 限流键干扰结果。

**复现步骤与结果：**

| 步骤 | 接口与输入 | 已注册且验证的邮箱 | 不存在的邮箱 |
| --- | --- | --- | --- |
| 1 | `POST /api/auth/register/email-code`，JSON 为 `{"email":"测试邮箱","language":"en"}` | `409 email_taken` | `200`，返回 challenge 信息 |
| 2 | 在干净配额下调用 `POST /api/auth/password/reset/email-code`，使用同样 JSON | `200` | `200` |
| 3 | 60 秒内再次调用同一重置接口 | `429` | `200` |

**实际结果：** 两条枚举路径均在标准 `venv`、内存 SQLite 和假 SMTP 下复现。测试没有给真实邮箱发信。

**影响：** 未登录访问者可以推断账户邮箱是否存在，用于定向猜测或钓鱼。该差异本身不绕过密码或验证码，不构成直接账户接管。

**修复建议：** 若产品需要隐藏账户存在性，注册验证码采用一致的公开结果；密码重置对存在和不存在的邮箱应用一致的外部响应及配额行为。正常注册失败原因的展示应与该隐私目标一致。

**后续验收：** 两类邮箱在首次与短时间重复请求时，对外状态码、响应结构和限流行为一致；正常注册和密码恢复仍可完成。

## 已确认的旧环境漏洞

### ENV-01 遗留 Starlette 存在已知漏洞

**风险等级：高。状态：仅旧 `.venv` 已复现；实际部署是否使用它待核实。**

[web.py](../src/tcg_toolkit/web.py) 第 149 行挂载 `StaticFiles`；[dependencies.py](../src/tcg_toolkit/auth/dependencies.py) 第 34–36 行使用 `request.url.path` 构造认证限流键。这些路径与旧 Starlette 的漏洞相关。

| 子项 | 复现步骤 | 旧 `.venv` 结果 | 标准 `venv` 结果 |
| --- | --- | --- | --- |
| Range 解析耗尽 | 调用 `FileResponse._parse_range_header("bytes=" + "0" * n + "a-", 1000000)`，分别取 `n=2000,4000,8000`，记录异常前耗时 | 约 0.013、0.049、0.196 秒，输入翻倍时耗时约四倍 | 同组样本约 0.00002–0.00004 秒，未复现该退化 |
| Windows UNC 路径 | 构造静态请求路径，其中含双反斜杠开头的 `audit.invalid` UNC 路径；mock `os.path.realpath`，只捕获参数并立即中断，不调用真实解析 | UNC 路径进入 `realpath` 前的捕获点 | UNC 路径未进入解析器 |
| Host 限流绕过 | 采用假登录服务始终返回失败；同一客户端连续发送 12 次登录，分别使用普通 Host 和每次变化的 `Host: example.com/arbitrary{i}?` | 普通请求第 11 次起 429；变化 Host 的 12 次均进入登录服务并返回 401，形成 12 个不同限流键 | 两组请求均在第 11 次起 429 |

Host 复现请求使用 `/api/auth/login`、合法格式的测试用户名和密码、可信 Origin、JSON 请求体；每组使用新的 `RateLimiter`，避免串扰。测试应用不进入连接真实数据库的 lifespan。

**攻击前提：** Range 问题需要运行受影响依赖并暴露文件服务；UNC 问题只影响 Windows，凭据泄露还取决于出站 SMB 和系统认证策略；Host 绕过要求入口接受并保留异常 Host。UNC 探针没有建立 SMB 连接，没有捕获或泄露任何真实凭据。

**修复建议：** 核实部署启动命令和实际解释器，统一使用已验证的依赖环境。更新旧环境时成套更新 FastAPI 与 Starlette，避免违反兼容约束；旧 FastAPI 0.115.6 要求 `starlette>=0.40.0,<0.42.0`。认证限流的 action 可从实际路由路径或固定路由标识获取，避免依赖由 Host 重建的 URL。仅为解决这些问题，不需要删除旧目录。

**后续验收：** 在实际启动服务的解释器中核对版本并重复上述受控探针；不能仅以另一个虚拟环境的版本作为生产已修复的证据。

官方公告分别确认受影响范围和修复版本：

- [GHSA-7f5h-v6xp-fcq8，Range DoS](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8)：Starlette 0.39.0 至 0.49.0 受影响，0.49.1 修复。
- [GHSA-wqp7-x3pw-xc5r，Windows UNC SSRF 与 NTLM 风险](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r)：Starlette 1.1.0 之前受影响，1.1.0 修复。
- [GHSA-86qp-5c8j-p5mr，Host 导致 URL 路径混淆](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr)：Starlette 1.0.0 及以前受影响，1.0.1 修复。

## 待核实风险

### CFG-01 COS 头像写入和删除没有请求签名

**风险等级：待核实；若公网允许匿名写入或删除，则为高风险。状态：未确认真实桶权限暴露。**

位置：[avatar.py](../src/tcg_toolkit/auth/avatar.py) 第 56–57 行和第 104 行。

**已确认事实：** 上传使用没有 Authorization 签名的 PUT；清理使用没有签名的 DELETE。桶地址配置要求 HTTPS origin，不能携带预签名查询参数。应用内的身份检查和图片校验不会自动成为对象存储的权限控制。

**待核实条件：** 真实桶 ACL、桶策略、来源 IP 或网络限制，以及配置地址是否由额外鉴权网关处理。源码本身不能证明公网任意访问者都具有写入或删除权限。

**核实步骤：**

1. 只读核对实际 `TCG_AVATAR_COS_URL` 对应的服务及桶策略，不在报告或日志中复制密钥。
2. 检查匿名主体是否被授予 `PutObject`、`DeleteObject`，以及资源前缀、来源地址等条件是否有效。
3. 在本地对 `httpx.put` 和 `httpx.delete` 使用 mock，确认实际代码未携带签名；该步骤不向真实桶发送请求。
4. 若确需验证远端权限，先明确授权使用独立测试桶或测试前缀；本轮未执行。删除对象仍须取得用户手动批准。

**潜在影响：** 若没有其他有效权限限制，攻击者可能绕过应用认证写入或删除头像对象，并造成存储滥用。

**修复建议：** 由服务端使用最小权限身份签名，或采用短时、限定方法及对象路径的预签名请求；桶保持私有写入权限。若依赖额外网关或来源策略，应记录并验证该边界。实际方案应在桶配置核实后确定。

**后续验收：** 未授权来源不能写入或删除测试对象；应用通过明确授权身份完成操作。不能把“应用上传成功”当作桶权限安全的证明。

## 其他检查结果

| 类别 | 本次证据与结论 |
| --- | --- |
| SQL 注入 | 扫描 48 个业务 Python 文件，未发现将用户输入拼接成原始 SQL 的可达路径；卡牌及分组查询用 PostgreSQL 方言编译后，注入字符串仅存在于绑定参数。内存登录注入探针被拒绝。数据库迁移中的固定 SQL 不属于用户输入链路。 |
| 鉴权与越权 | 标准 `venv` 中，4 个匿名数据接口返回 401；10 项跨账户卡牌、分组、收藏及订阅操作返回 404；3 项列表按当前账户隔离。 |
| 会话与验证码 | 伪造或已被新登录替换的 token 被拒绝；跨账户密码恢复证明无效；5 次错误验证码后，即使提供正确验证码也被拒绝。 |
| CSRF 与内容类型 | 非可信 Origin 返回 403，错误 Content-Type 返回 415；浏览器认证请求使用 Bearer，且省略 Cookie 凭据。 |
| XSS | 未发现用户输入可达的动态 HTML 或 eval 执行路径；名称、备注、昵称通过 `textContent` 等文本方式渲染。商品链接有固定 HTTPS 域名及路径校验。 |
| 命令注入 | 未发现 HTTP 输入通向 shell 执行的链路；前端 cURL 功能仅解析凭据，并拒绝 shell 元字符等异常输入。 |
| 路径穿越 | 标准 `venv` 的静态入口对编码点、斜杠及反斜杠载荷返回 404，`.env` 和项目配置文件不可通过所测路径读取。旧环境 UNC 风险单独列于 ENV-01。 |
| Excel 公式注入 | `=1+1` 和 `=HYPERLINK(...)` 导出为字符串，生成的工作表没有公式节点；导入公式返回 422。 |
| 头像内容校验 | 伪装为 PNG 的 SVG、4097 像素超尺寸头像均在外部上传前返回 422；正常图片重编码为 WebP，对象名由服务端 UUID 生成。桶权限另见 CFG-01。 |
| 代理凭据传输 | 游游亭 HTTP 网关请求在应用层使用 AES-GCM 加密 Cookie、CSRF 和正文；缺少密钥时发送前失败。因此未将该 HTTP 入口报告为明文凭据泄露。 |
| 部署入口 | 生产 compose 配置绑定 loopback，并依赖外部 1Panel/OpenResty 提供 HTTPS；仓库没有该外部反代的实际配置，不能据此判断生产限流或请求体上限。 |

相关前端现有测试通过，覆盖会话隔离、凭据解析、导入导出、头像及查询逻辑。各组包含重叠用例，不将分组通过数相加为独立安全测试总数。

## 建议处理顺序

1. 核实实际运行环境与 COS 权限；如正在使用旧 `.venv` 或存在公网匿名写权限，优先处理对应高风险条件。
2. 限制 XLSX 逻辑行数，修复 SEC-01。
3. 限制 PM 上游重定向并确认 TLS 使用条件，处理 SEC-02。
4. 根据账户隐私要求统一验证码接口的可观察行为，处理 SEC-03。

以上为修复建议和后续验收条件，本次仅保存报告，未落实业务代码或配置变更。
