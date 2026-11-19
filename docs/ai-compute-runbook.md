# AI 算力全景：本地 Agent 更新，网页只展示

侧栏入口：AI 算力全景（`/ai-compute`）。数据存于独立 `data/ai_compute.db`；不参与宏观评分，不改 CRCL。

## 职责边界

当前本地 Agent 负责采集、查原文、补历史、撰写经营判断与六板块解读，最后发布报告。网页只通过 GET 读取结果，支持历史范围、公司选择和来源查看；没有采集、导入、重新分析、prompt设置按钮。应用内不调用模型，也不生成程序结论。AI设置中的宏观模型/模板与本页无关。

本地 skill：`.agents/skills/ai-compute-collect/SKILL.md`。用户选择 Codex / Claude Code 等具备网页能力的 Agent 执行，可直接编辑 skill 的研究规则。没有 Agent 报告时结论区留空并说明待更新；旧的应用内模型报告不再展示。

## 更新数据

```bash
.venv312/bin/python scripts/ai_compute.py collect --months 36
.venv312/bin/python scripts/ai_compute.py collect --sources rental_history
.venv312/bin/python scripts/ai_compute.py contract
.venv312/bin/python scripts/ai_compute.py overview
```

默认来源：SEC标准财务、台湾月营收、Runpod/Azure当前固定配置报价、GetDeploying周度租价历史。默认回补月营收36个月并预热14个月；SEC逐tag获取可得历史。失败保留旧值，修订追加。

特色数据由Agent查官方IR/8-K/6-K/PDF后写事实批次，先校验再导入：

```bash
.venv312/bin/python scripts/ai_compute.py import --file <事实JSON>
.venv312/bin/python scripts/ai_compute.py import --file <事实JSON> --apply
```

字段字典和两套JSON Schema以contract为准。事实包含公司、期间、频率、原币、GAAP/非GAAP、范围、发布日期、原文URL、表页/行与摘录。未知不填零，整批校验后原子写入，重复去重。

## 发布本地 Agent 结论

Agent读取最新overview，亲自撰写报告JSON：`schema_version: ai-compute-report-v2`、`data_hash`、`as_of`、`agent`、`overall`；三项summary（earnings/outlook/conditions）各含headline/text；六项panels（earnings/demand/manufacturing/rental/power/quality）各含headline/evidence/watch；sources为title/url对象。网页总判断与三项依据全部展开，来源显示公司/财季/文档名称，不用“参考1”。旧结构报告不显示，需由Agent重新撰写，不自动拼凑旧文案。

```bash
.venv312/bin/python scripts/ai_compute.py report --file <报告JSON>
.venv312/bin/python scripts/ai_compute.py report --file <报告JSON> --apply
```

报告与最新事实hash绑定。数据变化后，旧报告明确标记待复核；不得只换hash跳过研究。所有结论一次发布，不调用应用AI模型，不需要应用启动，也不发生额外的应用内模型费用。当前可参考 `data/ai_compute_reports/2026-11-17.json`，它是本地Agent的研究产出，不是内置模板或演示文案。

## 图表与历史缺口

- 客户投入默认「资本开支（CapEx）」，只用单季事实。现金资本开支是现金流表的毛购置，不等同于纯 AI 或云业务支出。SEC 自动采集已补 AMZN 专用 `PaymentsToAcquireProductiveAssets`（2026 Q3 772.59 亿美元，净额 758.78 亿美元是另一口径）。其他发行人不复用该标签。微软、Meta 的公司披露 CapEx 分别计入新增融资租赁、租赁还本；曲线不相加、不用通用公式重建。融资租赁另图。CoreWeave（含资本化内部软件）、Nebius（含无形资产）及 AWS、Google Cloud 分部历史，见 `data/ai_compute_evidence/2026-11-19-customer-capex.json`；以后按对应财报续补。
- 制造与交付默认看「订单与排期」。首批已核实 49 点：戴尔的在手订单、新接订单、AI 服务器已确认收入分别从 FY2024 Q3、FY2024 Q4、FY2025 Q1 起，均至 FY2027 Q2；HPE AI Systems 新接与积压是 FY2024 Q4 至 FY2026 Q3，不含 AI 网络，未找到连续同口径营收。来源逐点保存在 `data/ai_compute_evidence/2026-11-19-manufacturing-orders.json`。历史出货不替代收入；没有连续订单就保留缺口，单次交付窗口写入本地 Agent 解读，原有营收曲线照常展示。此项按官方财报手动续补，不加入默认自动采集。
- EPS第四季度缺口：SEC标准companyfacts常只有全年值，不能用全年减九个月推EPS。Agent从官方Q4业绩稿直接取单季GAAP/非GAAP列，核对普通股/ADR和拆股调整。本轮新增34项官方事实，六家核心公司在近三年已披露单季EPS序列中均无跨季缺口。
- 年度与单季不混线；未找到的季度仍断线，不插值。EPS同比上年基数非正时不计算增长百分比，这不影响绝对EPS曲线。
- 只有一期的数据不画折线，不再设置“单期披露”“单家报价快照”“目标期指引与预期”固定展示栏目。这些仍保留为Agent研究依据，重要变化写进板块解读，后续按真实披露回补。
- 三月营收同比用最近三个月合计/上年相同三个月合计减一。DIO用平均库存/同期成本×实际天数；DSO用平均贸易应收/同期收入×天数。缺输入则不生成。
- 金额统一原币“亿”：原始数值÷1e8，39.367 billion USD显示393.67亿美元；KPI、坐标、悬浮值和来源抽屉同口径。数据库仍存原币基本单位，EPS/股和美元/GPU时不缩放。台积电EPS按TWD普通股，USD指引独立。不把总收入/DC/HPC/CMBU等同纯AI收入；纬创与纬颖不相加。租赁新增资产、现金PP&E、租赁还本分开。

## 租价历史：已验证的免费来源

公开文件（非带key的付费API）：

`https://getdeploying.com/dataset/gpu-prices/nvidia-h100.json`

H200/B200/B300替换slug。官方页面 https://getdeploying.com/dataset/gpu-prices 明确声明数据文件CC BY4.0。导入时校验meta.license与型号，只取ON_DEMAND、无预留期的median_price，保留provider_count/offering_count/provisional信息。网页署名GetDeploying并链接CC BY4.0，原始JSON可从来源抽屉访问。

本轮实测四个型号各53个周观察，范围2025-11-17至2026-11-16，共212项。日期为该周周一，当前周可修订；取周一时点全部挂牌的中位数，不是该周每日价格的中位数，缺周不补。它是同型号跨配置、跨供应商挂牌统计，不是固定配置成交指数，供应商组成变化也会改变中位数。

Runpod Secure与Azure H100八卡Linux报价保留为单独快照，不与上述历史拼接。GPU真实利用率、成交量仍未知。

若文件下载失败，skill允许Agent打开公开表格/图表，逐期读取或截图估读（必须标estimate、精度、图像日期及原文定位）。备用参考Lantern Works公开CC BY4.0样本仅有短期历史，不能无缝拼到另一供应商统计上。GetDeploying带key的API和Ornn/Silicon Data需独立权限，不自动购买或抓取。

### 更早历史的实际边界

GetDeploying同源延长：2026-11-17核验，免费文件加date_start仍只有53周，H100的tracked_since=2024-07-08只是源站追踪起点，非已下载历史。官方API文档提供date_start/分页和100请求免费试用（需工作邮箱与key）；用户提供合法访问及存储展示权限后，可逐页取更早同口径median_price再导入。当前未注册、未调用这个API，但这不影响通过其他公开网页取得独立长期曲线。

公开趋势页的图表说明从2025-04起，但统计量是provider_median_price，与当前median_price不同；本地直读403，不绕过。更长的R. Mittal月度目录重建虽有H100至2023-10，其许可说明上游价格再分发权利不确定，本轮不导入。Internet Archive CDX未成功取得旧文件。具体入口与核验要求见skill的references/sources.md。已有周观察永久保存在SQLite，未来滚动采集不会删掉窗口之外的数据。

### 已完成的浏览器长期曲线回补

2026-11-17实际用可视Chrome访问`https://gpu-index.semianalysis.com/`，公开图表无需登录、key、付费；切换历史表Midpoint/p25–p75并核对数值。浏览器页面自身加载`/api/public-data`（HTTP200），保存原始响应、截图与规范化事实，非只检查下载接口后结束。

已导入2913个日期点：H100 1133点自2023-10-10、A100 1254点自2023-06-13、B200 526点自2025-06-08，均截至2026-11-17。网页“长期合成基准 · SemiAnalysis”与GetDeploying周价分图，默认显示完整已取得历史，不设全局年份开关；真实缺日仍断线。

本地事实文件：`data/ai_compute_evidence/2026-11-17-semianalysis-history.json`。原始响应：同目录`2026-11-17-semianalysis-public-raw.json`，SHA256=`63a29d465a3a2d94beaa87d8ba91c006510649854c1407324c655b2d9c34b432`；截图：`2026-11-17-semianalysis-browser.png`。这些文件与 SQLite 不随仓库分发；新 checkout 没有该曲线时，按 [skill 的浏览器规则](../.agents/skills/ai-compute-collect/references/sources.md#浏览器长期曲线已成功取得并落盘)核验来源后自行导入，核验失败则留缺。已有本地历史可直接复用，只核对修订与新增日期。此来源不加入默认 collect。

口径：SemiAnalysis Spot-Contract Composite为供应商加权并按适用范围结合合约调查的合成基准，不是挂牌中位数、纯现货或逐笔成交。日期点是当前公开的历史版本，可能重建/修订；published_at记录取得版本时间，不能伪造历史发布日期。不将原表的季度/半年合约区间插值成日数据，sold out和空白也不填零。没有声称开放数据许可，按用户要求保留原文署名用于本地研究；不擅自对外批量再分发。

## 电力：不定期披露写进解读

Agent每次研究产品订单、制造排期、交付、现场调试、接电投运，不把它们混成同一阶段。施耐德官方Lead Times Guide本轮PDF实读：2026-11-03美国版，第4页LVDO A/B/C/D/F按单制造平均50周，第5页SURESET 20–50周；从释放制造起算、仅供规划，不是客户订单承诺。记录在`data/ai_compute_evidence/2026-11-17-power-lead-times.md`，已写进power.evidence/watch，不新增快照图表。未取得可比前版不能声称交期变长/变短，未知项目排期不拿集团backlog或IT容量代替。

## 网页接口与验收

仅保留GET `/api/v1/compute/overview?months=36` 和GET `/api/v1/compute/contract`。原collect/import/analyze/prompt/jobs网页写接口已经移除。旧“5/6生成中”和模型超时状态不再属于本页工作流。

验收包括：网页零写请求、无内置模型依赖、无报告时不产生程序判断、报告hash校验、六块结构化解读原子发布、EPS连续性、真实53周租价与授权校验、金额实际换算、已移除栏目不可见、具名来源、亮暗/窄屏/来源抽屉。更新后应用重建前端并重启服务，用户可直接查看。
