# 页面片段

`grading_pcgcn_qr.png` 用 Python qrcode 8.2 生成，内容为用户反馈的标签链接 `https://card.pcgcard.cn/card?type=1&query=10198854`；用于照片解码、机构识别和保存的浏览器回归。

`pcgcn_certificate.json`：直连 PCG-CN 官方公开 `api/card/card/search_card_result/` 查询编号 `10198854` 的结果。移除了物流单号及账号关联字段；用于卡名、等级、分项、图片及等级分布解析测试。

`grading_psa_qr.png` 是用 Python qrcode 8.2 生成的测试二维码，内容为 `https://www.psacard.com/cert/98428602/`。用于浏览器照片上传、Canvas 与内置 jsQR 的真实解码验收，不是评级卡实物照片。

2026-11-14 通过 proxy-go 从 PSA 官方 `https://www.psacard.com/en-CA/cert/{number}/psa` 获取：

- `psa_certificate.html`：编号 `98428602`，保留 `main` 与两个评级数量面板；卡名 CHITOSE TUTAMACHI、等级 GEM MT 10，含两张证书图片地址。
- `psa_not_found.html`：编号 `000000000000`，保留实际 HTTP 200 错误页的 `html#__next_error__` 和 Next.js 明确的 404 标记。
- 图片仅保存 URL；评级数量为采集时快照，普通解析测试不访问外网。

2026-11-14 通过 proxy-go 从 CGC 官方 `https://www.cgccards.com/certlookup/6091052028/` 获取：

- `cgc_certificate.html`：保留 intro（包括隐藏的无记录、限流模板）、证书图片和 Population 模板。证书编号 `6091052028`，卡名 Tsuyogaru Otona no Secret Labo Mayu，Weiss Schwarz Rose、2025、Japanese、Yuzu Soft、R01-026SP、Special，等级 PRISTINE 10，Grade Date 为 2025-11-19。
- 证书图片为 Obverse、Reverse，原图地址来自图片链接的 `href`，描述来自图片的 `title`；仅保存地址，不下载图片。
- Population 在该 HTML 中只有 JavaScript 异步模板，未提供实际数量；解析结果保留为 `null`。隐藏的无记录、限流模板也存在于正常证书页，不能仅因模板存在而判定查询失败。
- 该样本供 `tests/test_cgc.py` 离线解析测试使用。
- `cgc_lookup_pending.html`：同日请求编号 `0000000000` 返回的 HTTP 200 入口页片段，只有预填编号与待执行的查询表单，没有证书字段或明确错误状态；用于验证不会将隐藏模板误判为证书不存在。

2026-11-12 从 ARS 官方 `https://ars-grading.com/grading/searchNumber?id=...` 获取，仅保留 `main` 内容：

- `ars_certificate.html`：编号 `047088950114`，包含日英卡片信息、高亮等级和统计；无卡片图片。
- `ars_not_found.html`：编号 `000000000000`，HTTP 200 中明确的无记录提示。
- 统计为采集时快照，测试不访问外网。

2026-10-27 从 `https://yuyu-tei.jp/sell/{game}/s/search` 获取：

- `wsr_1.html`：`search_word=OS01`。
- `lo_1.html`、`lo_2.html`：`search_word=LO`，第一页和第二页。
- `chaos_1.html`、`chaos_2.html`：`search_word=YZ`，第一页和末页。
- `empty.html`：WSR，`search_word=ZZZ_NO_MATCH_927481`。

保留真实商品节点（前两件及每种库存文案的样例）、分页栏和无结果提示，
以最小 main/cards-list 容器包裹。删去了页面导航等无关内容。
这些片段用于离线解析测试，不代表当前价格或库存。

2026-10-29 从 `https://yuyu-tei.jp/sell/wsr/card/OS01/10240` 获取：

- `product_wsr.html`：商品页标题（稀有度 + 名称）和印刷编号，删去库存、购物车控件和推荐商品。
