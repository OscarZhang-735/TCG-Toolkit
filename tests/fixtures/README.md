# 页面片段

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
