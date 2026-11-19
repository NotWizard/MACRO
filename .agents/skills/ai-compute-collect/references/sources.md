# 原文入口与常见定位

字段名、实体和 schema 以 `scripts/ai_compute.py contract` 为准，不在本文件复制。

| 板块 | 官方入口 | Agent 核验重点 |
| --- | --- | --- |
| 盈利/前瞻 | NVIDIA、AMD、Broadcom、Marvell、Micron、TSMC 的 Investor Relations earnings；SEC 对应 8-K/6-K 附件 | Data Center/AI semiconductor 等独立业务、指引上下界、GAAP 与 adjusted EPS；收入未来目标不能标 actual |
| 客户投入 | Microsoft FY earnings/call、Amazon quarterly results、Alphabet earnings、Meta results、Oracle investor news；CoreWeave/Nebius IR | Azure 与 Microsoft Cloud 不混称；AWS、Google Cloud 与 OCI 原层级；RPO 期限和定义变更；租赁取得/还本和现金支出区别 |
| 制造/交付 | MOPS/TPEx 月营收由接口完成；TSMC quarterly results、SK hynix newsroom、Samsung IR、ASE、Amkor、Arista、Coherent、Dell、HPE、Supermicro IR | 月营收为合并总额；季报分部/HBM/订单按公司原定义。未注明日期的 AI 目标先不导入 |
| 租赁 | GetDeploying CC BY4.0公开JSON文件提供53周趋势；Runpod/Azure提供当前固定配置报价 | 市场统计与单家报价分离，保留样本数和暂定周，不推出利用率；下载失败可由Agent读公开表/图，截图估读须标estimate |
| 电力/部署 | Eaton quarterly analyst presentation、Schneider financial results、Vertiv earnings、GE Vernova quarterly results、Siemens Energy earnings、DLR financial supplement、Dominion earnings slides | Eaton combined Electrical 与 Americas 口径；organic 与 reported；TTM；DLR supplement IT capacity/occupied 定义及权益范围；Dominion ELOA/CLOA/ESA 阶段 |
| 库存/质量 | NVDA/AMD/MU 10-Q/10-K 存货附注（标准标签已采）；其他公司库存/采购承诺附注 | 原料、在制、产成品、其他/在途及计提/核销分别核对；Micron 原料含耗材；应收必须贸易范围 |

首轮已核对的原文记录在 `data/ai_compute_evidence/2026-11-17.json`，可参考格式，不将其日期/数值作为未来默认值。

若 source 返回验证码、WAF、超时或 PDF 下载类型异常，记失败并换同一公司的官方监管附件；不借搜索摘要补数、不绕过登录/付费墙。免费阅读不自动等于允许系统化转载或展示；涉及许可疑问保留条件状态。

## 客户投入：现金资本开支与公司口径

- [Amazon SEC Company Facts](https://data.sec.gov/api/xbrl/companyfacts/CIK0001018724.json) 的 `us-gaap:PaymentsToAcquireProductiveAssets` 已核对 [2026 Q3 财报购置行](https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Third-Quarter-Results/)；只对 AMZN 启用。财报的净现金 CapEx 已扣除处置所得与激励，不能替代总额，也不把集团项称为 AWS 投入。同标签的同财年累计可拆季，不用 TTM 或总投资现金流。
- [微软财报与电话会](https://www.microsoft.com/en-us/investor/earnings) 核对 CFO 的当季总 CapEx 与现金流 Additions to property and equipment；[Meta 财报](https://investor.atmeta.com/financials/) 查公告的当季 CapEx（含租赁还本）与现金流购置、还本两行。只补直接披露的实绩，年度指引或单次预告写解读，不连成实际曲线。
- [CoreWeave 财报](https://investors.coreweave.com/financials/quarterly-results/) 和 [Nebius 财报](https://nebius.com/financials) 分别取含资本化内部软件、含无形资产的现金购置行，保留原范围、单季列与重列说明。不要用资产负债表期末净额相减。
- AWS 取 Amazon 业绩的 Supplemental Financial Information and Business Metrics，Google Cloud 取 Alphabet 分部附注：同一分部的收入和营业利润逐季录入，利润率由程序算。Google Cloud 包括 TPU 系统销售，不等于全是云服务消费。

## 制造与交付：订单数据

- [戴尔历季业绩](https://investors.delltechnologies.com/financial-information/financial-results)：Performance Review 的 **AI Optimized Servers** 页取 Orders、Revenue、Backlog；8-K 附件的 AI-Optimized Servers 行可交叉核对营收。旧图若只写 Shipped，不能接上 Revenue；电话会「截至今天」和财季末 Backlog 也不能混填。历史比较值保留引用的财报发布日期以及原财季。
- [HPE 历季业绩](https://investors.hpe.com/financial/quarterly-results)：财报演示 **AI Demand** 取 AI Systems，不加 Networks for AI；若只披露 Cloud & AI 分部营收，不填 AI 系统营收。口径变更或来源冲突先查脚注。
- [Supermicro 历季业绩](https://ir.supermicro.com/financials/quarterly-results/) 及台湾 ODM 的 IR／法说会：不把年度接单、未承诺订单、项目签约新闻或集团订单接成季度 AI 在手订单线。经核实的单次排期只进解读，注明时间、产品与披露边界。

## 更早租价：2026-11-17 实测

### 浏览器长期曲线：已成功取得并落盘

- 打开[公开SemiAnalysis曲线](https://gpu-index.semianalysis.com/)，无需登录、key或付费。本轮可视Chrome已实际显示图表，并切换Detailed Pricing History的Midpoint/p25–p75，核对早期表格、合约区间和最新值。
- 浏览器正常加载`https://gpu-index.semianalysis.com/api/public-data`，返回status=ok、index数组和contract数组；不是猜测/绕过付费接口。原始响应保存于`data/ai_compute_evidence/2026-11-17-semianalysis-public-raw.json`，浏览器截图为同目录`2026-11-17-semianalysis-browser.png`。SHA256：`63a29d465a3a2d94beaa87d8ba91c006510649854c1407324c655b2d9c34b432`。
- 本轮入库2913个非空日期点：H100 1133点（2023-10-10起）、A100 1254点（2023-06-13起）、B200 526点（2025-06-08起），截至2026-11-17。规范化事实文件为`2026-11-17-semianalysis-history.json`，可用现有import CLI重复校验/去重导入。
- 对应实体`SEMIANALYSIS_H100/A100/B200`，指标`gpu_rental_composite`，unit=USD/GPU-hour，basis=reported，frequency=daily，scope按GPU分别保留，definition_version=`sa-spot-contract-public-v1`。published_at/observed_at记录本次取得时间，不能回填为历史所属日期，避免假装PIT；null不补，日期须唯一且有序，价格正且有限。
- 这是Spot-Contract Composite（供应商加权，H100含适用合约调查混合），不是单家挂牌或逐笔成交。按日期发布的历史可能重建/修订；不把它写成原始成交日价。与GetDeploying的gpu_hour_price分图保留，不改名伪接。没有声称其CC BY许可；保留署名与原文，仅按用户请求用于本地研究，不擅自对外批量再分发。
- 复核例：当前版本2023年有值的H100日期点均值6.5677，与网页2H 2023表格的6.57一致；本轮最新H100为3.42、B200为6.26。后续数值可能变化，不将本轮数值硬编码为未来默认。
- 优先复用已有本地历史；原始响应、快照和规范化指数不随仓库分发。首次在新环境运行或需要追加时，打开网页核对指标定义和可取得历史，取不到就留缺。响应变化就按页面读表/逐点悬浮/截图，不以搜索摘要代替，也不绕验证。区间估读必须标estimate、保留截图/坐标/精度，且独立于精确数值序列。

### GetDeploying旧周数据边界（不等于所有租价历史边界）

- [开放文件](https://getdeploying.com/dataset/gpu-prices)只含滚动53周。H100元数据`tracked_since=2024-07-08`，但实际首行仍为2025-11-17；试加date_start无效。
- [官方API文档](https://getdeploying.com/help/api)：完整周历史可用`GET /api/gpu-price-history?gpu_model=nvidia-h100&billing_type=ON_DEMAND&interval=weekly&date_start=2024-07-08&page_size=100`，需要用户提供合法key、分页获取。当前有工作邮箱注册的100请求试用，不擅自注册或购买。导入前核对展示/存储权限；必须沿用`median_price`，不能改成`provider_median_price`后接同一条线。
- [公开趋势页](https://getdeploying.com/gpu-price-trends)说明图表从2025-04起，但用的是供应商等权中位数，与当前“全部挂牌中位数”不同。当前环境直读页面403，未读取到早期逐周数值；不能把网页的起点写成已经回补的起点。若以后能合法读取表格/图表，仍需单独口径序列，截图估读标estimate。
- [R. Mittal重建历史](https://github.com/rahilmittal1/gpu-price-history)：SXM专门云按需样本H100可至2023-10、H200至2025-01、B200至2025-05，但属于月初目录重建且供应商组成变化。其DATA_LICENSE明确上游价格再分发权利不确定，本轮不导入、不与GetDeploying拼接。以后采用须先确认许可、逐点核查source_version与样本数，并独立展示“重建月度”而非声称原始市场周价。
- Internet Archive CDX本轮TLS失败，未取得可核验旧文件。以后如取得旧开放文件，先验CC BY4.0、统计定义和重叠周数值；同口径才能通过事实JSON追加，冲突保留版本，不覆盖来源。
- GetDeploying目前仍只取得53周，但长期观察已有上述SemiAnalysis独立基准。不要重复把“此源只有53周”说成“全市场没有免费长期曲线”。每次collect已将取得的周数据追加进SQLite，未来窗口继续增长，不回删过期周。

## 电气设备非定期证据

[施耐德Lead Times Guide](https://www.se.com/us/en/download/document/Monthly_Lead/)会更新版本，2026-11-17页面显示2026-11-03的November 2026版。下载原表后记录版本/产品行/地区/交期定义，不沿用旧搜索摘要。产品发布稿的“更快制造/调试”不是客户当前排队周数；集团订单、积压、数据中心容量也不能替代开关/变压器实际排期。把核实结果和缺口写进power.evidence/watch；不要求每次都有固定数量或固定频率的数据点。

本轮已读到官方7页PDF：第4页LVDO A/B/C/D/F按单制造平均50周，第5页SURESET为20–50周；第1页定义从release to manufacturing起算，仅供规划。核验记录见`data/ai_compute_evidence/2026-11-17-power-lead-times.md`。未来必须检查新版本，不将本次数字作为默认值；只有一版不能判断交期变化方向。
