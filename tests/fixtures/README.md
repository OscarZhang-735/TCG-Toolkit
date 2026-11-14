# 页面片段

2026-11-14 通过 proxy-go 从 PSA 官方 `https://www.psacard.com/en-CA/cert/{number}/psa` 获取：

- `psa_certificate.html`：编号 `98428602`，保留 `main` 与两个评级数量面板；卡名 CHITOSE TUTAMACHI、等级 GEM MT 10，含两张证书图片地址。
- `psa_not_found.html`：编号 `000000000000`，保留实际 HTTP 200 错误页的 `html#__next_error__` 和 Next.js 明确的 404 标记。
- 图片仅保存 URL；评级数量为采集时快照，普通解析测试不访问外网。

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
