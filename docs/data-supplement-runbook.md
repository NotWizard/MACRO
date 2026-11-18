# 宏观经济与 Circle 数据更新手册

给接手更新的 Agent：从项目根目录执行本手册即可。范围是**中国宏观看板的 19 张原始表及其衍生数据**，和 **Circle（CRCL）的行情、稳定币、美债、估值、季报与事件**。AI 算力不在本手册范围内；不要调用算力采集或修改其数据库、事实、报告。本流程不新增总控脚本，也不要求安装定时任务。

## 1. 开始前：记住旧库的状态

- 使用项目已配置的 Python 虚拟环境。下面以 .venv312 为例；若旧环境缺包，用该环境的 Python 执行 `-m pip install -r requirements.txt`。Apple Silicon 若出现 pyexpat / Expat 符号错误，执行 `export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`（先确认路径存在）；不要改系统 Python。
- 记录 `data/last_run.json` 的 `ts`、`committed`、`derived`、`tables` 和 `sources`。按第 5 节的只读命令取基线。首次建库没有旧值，记为「无基线」。
- 同一时刻只跑一个宏观写入任务：01、02、03、04 和网页宏观刷新不并行；Circle CLI 不与 Circle 启动采集或网页采集并跑。遇到 busy 先等在跑的任务结束，不删锁、不绕过闸门。
- 查询时分别记录**观察期、发布日期和检查时间**。官方网页、PDF、XLSX 或发布者本人转载均要打开原文确认，保存可定位的链接、数值、单位与口径。搜索摘要、预告值、滚动表的空月份、媒体预测都不入库；未知保留空值。

## 2. 宏观：跑全量，再按源的可用性补数

```bash
.venv312/bin/python scripts/01_fetch_data.py --full
jq '{ts, committed, derived, tables, sources}' data/last_run.json
```

可以先查第 3 节，将当期已核实的显式补充值写进代码，再跑一次全量；若全量已跑过，只定向重跑涉及的表。正常提交会走备份 → 暂存 → 校验 → 衍生计算 → 原子切换，不要再接着跑 02。不要直接对生产 SQLite 执行 UPDATE/INSERT，也不要拿单源短表替换历史。

- **0**：本轮尝试的表通过；可能只是同一期重拉或修订。**2**：至少一张表保留旧数据，查看 `committed` 及失败表原因。**3**：衍生失败，暂存整轮丢弃。**1/busy**：通常未取得锁，也可能是其它启动失败，确认日志和 manifest 有没有换到本轮。
- 记下全量摘要再重试；`--tables` 会覆盖 manifest（例：`.venv312/bin/python scripts/01_fetch_data.py --tables pmi domestic_demand`）。03/04 不写 01 的 manifest，用各自的终端结果和库内值验收。表在 manifest 里没被尝试，与 `kept_previous` 不同。
- 网页「刷新数据」和无参数 CLI 默认按发布窗口，不代表全量。已有本机服务也可用 `GET /api/v1/session` 获取本地令牌，带 `X-API-Token` 调用 `POST /api/v1/refresh?full=true`，用 `GET /api/v1/refresh/stream?job_id=…` 持续订阅到 `done`。不要把令牌写入日志。CLI 不需要服务，也不会主动触发宏观 AI 评论；网页/API 刷新成功提交时可能自动触发，这是独立流程。
- 发布窗口只是调度提示：PMI 常在月末、CPI/PPI 次月上旬、央行金融数据与国民经济运行多在次月中旬、LPR 通常是每月 20 日、ISM 是次月首个工作日附近。核对实际公报；收入/人口为年度，GDP/杠杆率为季度，不追填虚构月份。

### 19 张表：查哪个机构、验什么

| 表 | 自动源与官方核验入口 | 关键口径或失败时的处理 |
|---|---|---|
| money_supply | AKShare 月度货币供应量；[中国人民银行](https://www.pbc.gov.cn/) 货币金融统计 | 对比 M0/M1/M2 各自同比最新期；M1 定义变化按官方说明，不用旧口径接算。 |
| gdp | AKShare/东财；[国家统计局发布](https://www.stats.gov.cn/sj/zxfb/) | 累计季度，日期写累计末季的**首月**（Q3 为 07-01，全年为 10-01）；债务收入比用可用全年 GDP，不拿 Q1×4。 |
| cpi | AKShare/东财；统计局全国居民消费价格 | 同比、实际环比分开核对，不用同比推环比。 |
| ppi | 东财同比长历史；统计局近 3 期原文；[中国房地产估价师与房地产经纪人学会历史表](https://www.cirea.org.cn/content/4776)补环比缺口 | 全国工业生产者**出厂价格**，不是购进价；每期环比须有 `ppi_mom_source`。历史表只补空，原文优先，无来源推算或插值不入库。 |
| pmi | 东财、AKShare；统计局和 [S&P Global PMI 发布日历](https://www.pmi.spglobal.com/Public/Home/PDF/UK_Rel_Dates)（按实际日期找发布稿；日历没有实绩） | 官方制造/非制造、RatingDog 制造/服务是四条序列，分别核对。旧 AKShare/Jin10 财新序列有中段缺口，不插值。 |
| pmi_detail | 统计局「中国采购经理指数运行情况」原始两表 | 滚动约 13 期并累积旧期；看生产、新订单、原材料库存、产成品库存等字段，不把非制造业活动混进制造业分项。 |
| leverage | CNBS 经 AKShare，NIFD 季报补充 | 日期写**季末月**；季度发布后核对六个部门，见第 3 节。 |
| social_finance | AKShare 镜像；央行调查统计司「社会融资规模增量」XLSX | 按**当月增量**亿元，不用存量或年初至今累计；见第 3 节。 |
| lpr | 东财；央行/全国银行间同业拆借中心公告 | 1 年期和 5 年期以上，期次按报价月；公告前旧月保留。 |
| industrial | AKShare/NBS；统计局工业生产发布 | 规上工业增加值；1–2 月合报不能补作两个独立观测。 |
| domestic_demand | 东财社零、固投；NBS 失业率 API → 国民经济发布与就业解读 → 历史公开表 | 社零/固投当月与累计分开；全国和 31 城分别核实。旧网页搬迁后的目录年月不是出版日期，读实际原文日期；1–2 月合报不代表两个月同值。 |
| house_price | AKShare 统计局 70 城数据 | 生产表仅跟踪 10 城样本；按日期与城市去重，核验各城市的新房/二手房，不称全国 70 城广度。 |
| real_estate_activity | 统计局「全国房地产市场基本情况」正文/表格 | 扫最近 15 期并累积旧期；核对累计开发投资、销售额/面积、新开工、竣工、待售、到位资金，不把累计值当单月，1 月通常无独立值。 |
| household_income | NBS「人民生活→全国居民人均收入情况」及总人口 | 年度全国居民人均可支配收入×总人口；核对 `income_per_capita`、`population_10k`、`income_abs`，不可换成城镇专口径。 |
| new_credit | AKShare 的新增人民币贷款镜像；对照央行同口径明细 | 先核对金融机构贷款或社融分项的口径，不互相覆盖。`loan_yoy` 只在去年同月基数为正时计算；`loan_yoy_change` 记录实际金额之差（亿元）。`sf_stock_yoy` 是近 12 月社融增量累计同比，不是官方存量同比。 |
| bond_yield | [中债信息网](https://www.chinabond.com.cn/) 国债收益率曲线 | 10 年期收益率，进行中的月允许是已到日的月度观测，不当完整月。 |
| demographics | 统计局人口公报优先；[世界银行 WDI](https://data.worldbank.org/country/china) 补长历史/结构 | 年度人口、城镇化、出生率、自然增长率、65 岁及以上占比、抚养比逐字段验收。总人口单位为万人，出生/自然增长率为 ‰，占比为 %；不拿不同源的出生/死亡率随意混算。 |
| fiscal | NBS 优先，东财收入兜底；[财政部财政数据](https://www.mof.gov.cn/gkml/caizhengshuju/) | 全国一般公共预算累计收入、支出（亿元及累计同比），不是政府性基金；收入有新月不代表支出同月已发布。 |
| external_demand | NBS 货物进出口，东财海关补空；[ISM 制造业报告](https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/) | 同期同币种，区分当月及累计和美元/人民币口径；美国 ISM 是制造业主指数，不是 S&P PMI 或分项。 |

统计局数据接口如果跳到 WAF 验证页（返回 HTML），改读官方公开发布归档；不要反复请求或把 HTML 当 JSON。没有同口径的已发布数据就保留旧数据并注明。核查官方公布期或历史修订时，不跨发布版本计算环比。

## 3. 宏观：需要 Agent 确认的录入点

**NIFD：** 从 [NIFD](https://www.nifd.cn/)「系列报告 → 宏观杠杆率」找本季报告；主站不可用可用作者/发布机构刊载的研究全文，不从二手摘要拼字段。只改 `scripts/nifd_leverage.py` 的 `NIFD_DATA`，追加 `("YYYY-MM-01", household, non_fin_corp, gov_total, gov_central, gov_local, real_economy, fin_asset, fin_liability)`，MM 为 03/06/09/12。全部为百分数，后两个金融部门值未披露填 `None`。核对中央+地方约等于政府、居民+企业+政府约等于实体，允许小额四舍五入；大跳变看原文。同一报告对上一季有修订时，环比用其同版比较值，勿用库里旧版本反推。CNBS 自动源失败时执行 `.venv312/bin/python scripts/03_supplement_leverage.py`。03 只追加新季度；确认 `leverage` 和 `derived_quarterly` 更新，不把它当历史修订入口。

**社融：** 主源 AKShare 可能滞后或报 TLS 错误。到央行「调查统计司 → 当年统计数据 → 社会融资规模」，如 [2026 年索引](https://www.pbc.gov.cn/diaochatongjisi/116219/116319/2026ntjsj/shrzgm/index.html)（跨年按官方导航切年份），打开「社会融资规模增量统计表」附件，不是存量表。按列名核对总增量、对实体经济人民币贷款、委托贷款、信托贷款、未贴现银行承兑汇票、企业债券、非金融企业境内股票融资。确认金额单位和负号；Excel 月份既可能是数值（`2026.1` 为 10 月）也可能是文本（`"2026.10"`）。全量未补上时，等其退出再跑 `.venv312/bin/python scripts/04_supplement_social_finance.py`。04 只追加比库内更晚且 `total` 非空的月份；「无新增」也可能是下载或格式异常，已发布的月份须回查 XLSX 和 `scripts/pbc_shrzgm.py`，不可报「未发布」。

**以下都在 `scripts/01_fetch_data.py`。** 保留历史条目，新增时在对应行旁记录来源 URL；同一期明确修订才改旧值。补完只重跑相应表：

| 常量 → 表 | 官方资料及录入字段 |
|---|---|
| _ISM_SUPPLEMENT → external_demand | ISM 新闻稿中的 Manufacturing PMI；键为参考月 `YYYY-MM-01`，不是次月发稿日。 |
| _RATINGDOG_SUPPLEMENT → pmi | S&P Global / RatingDog 的制造业 PMI 与服务业商务活动指数原文（两份，允许一方缺失留空）；元组 `(制造业, 服务业)`。不能拿同名综合产出指数，或混进 NBS。若只取得第三方数字，记录核验情况，不把新闻摘要当正式来源。 |
| _MOF_SUPPLEMENT → fiscal | 财政部「全国一般公共预算收支情况」；`date`、`revenue_cum`、`revenue_cum_yoy`、`expenditure_cum`、`expenditure_cum_yoy`，原文未披露的字段空着，保留前期记录。 |
| _UNEMPLOYMENT_SUPPLEMENT → domestic_demand | 统计局国民经济运行稿；`date`、`unemployment_rate` 和 `unemployment_31_city`；用当月值，不用年平均或青年失业率。 |
| _NBS_BIRTH_NATURAL → demographics | 统计局年度国民经济和社会发展统计公报；年度 `YYYY-01-01` 对应 `(出生率‰, 自然增长率‰)`。WDI 子序列超时应保留旧期有效值，用第 5 节对比，不能因总行数不变就略过。 |

## 4. Circle：先核对财报与事件，再采集公开序列

### 官方资料补录

1. 查 [Circle 投资者关系](https://investor.circle.com/) 财报发布、[Circle 新闻稿](https://www.circle.com/pressroom) 和 [SEC EDGAR](https://www.sec.gov/edgar/browse/?CIK=1876042)。季度收入、储备收入、其他收入、GAAP 稀释 EPS 和分发相关总成本以财报**单季列**核验，实际 EPS 与共识分开，无同口径可追溯共识时填 `null`。不把 9 个月累计、调整后 EPS 或新闻预测填入单季实绩。
2. 在 `data/crcl_fundamentals.json` 的 `quarters` 按财季升序追加/修订。财务 `_m` 是百万美元，流通/TPV `_b` 是十亿，链上交易量 `_t` 是万亿。期末 USDC 和平均 USDC 不混用，季度公司披露不以 DefiLlama 最新日替代。CPN 年化 TPV 不是本季度实现值。`reserve_revenue_m + other_revenue_m ≈ total_revenue_m`；`nonreserve_share_pct` = 其他收入/总收入×100；`distribution_cost_ratio_pct` = 同期分发相关成本/总收入×100。对照原文确定分子是否含交易及其他成本；不要向年度单行复制季度口径。一次性确认单独注明，不给真实异常设上限，也不将其称为经常性收入。记录 `source` 和 `updated_at`。
3. 在 `data/crcl_events.json` 核对财报、监管和利率事件。每项填写 `date`（YYYY-MM-DD）、`category`、`title`、`detail`、`source`、`status`。过期的「计划」不自动变「已发生」：确认实际日期和结果才改状态，已知延期按新计划更新，未核实标「待验证」；保留来源和历史。同一事件的提案、表决、生效、执行不是一个日期。FOMC 查 [Fed 决议与日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)；美国立法查 [Congress.gov](https://www.congress.gov/)、[Federal Register](https://www.federalregister.gov/) 和发布部门；欧盟查 [EBA](https://www.eba.europa.eu/)。`flags.fed_cutting` 看最近已经发生的利率轨迹，不用期货预测；`flags.clarity_act_passed` 看最终立法状态，不把程序动议、委员会通过或单院通过当最终通过。

对于经财报核实并确认为一次性的非储备收入，在季度条目填写 `nonreserve_one_off_m`（百万美元）并记录原文。不要用预售总金额或管理层指引代替已确认收入。页面和告警以（其他收入 − 已标注一次性收入）÷（总收入 − 已标注一次性收入）计算调整值，同时保留披露占比。例如 2026Q3 的 ARC 预售确认收入是 181.8 百万美元；剔除上述已知项目不等于剔除所有非经常性收入。

Arc 官方披露维护 `data/crcl_fundamentals.json` 的 `arc`：`as_of` 为观测日、`assets_b` 为链上资产（十亿美元）、`usdc_volume_min_b` 为主网上线以来 USDC 交易量下限（十亿美元）、`daily_transactions_approx` 为披露的约数（笔），`source` 链接到具体页。只更新同一观测口径；Dex 成交量、USDC 转账量、CPN 支付量和链上交易笔数不能相互代填。更新里程碑时区分签约、试点、正式投入商用和交易交割。

### 自动采集

以下直接调用项目既有采集及 JSON 验证，不启动应用，不添加脚本。先维护手工资料，自动采集评估告警时就会使用新数据。

```bash
.venv312/bin/python - <<'PY'
import json
from backend.app.api.v1.crcl import events, fundamentals
from backend.app.core.crcl_collect import collect_all

for check in (fundamentals, events):
    payload = check()
    assert "error" not in payload, payload
result = collect_all(progress_cb=lambda p: print(f"CRCL {p:.0%}", flush=True))
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["status"] == "ok" else 1)
PY
```

采集 USDC（stablecoin=2）、EURC（=50；欧元口径）和稳定币全市场；Treasury.gov CSV 回填近两年的 3M/6M/1Y；CRCL 日线 AKShare 主、yfinance 备；估值用 yfinance。DefiLlama 还提供 Arc DeFi TVL、链上稳定币市值及 DEX 日成交量：只采 2026-09-16 主网上线以后的值，DEX 用已完结 UTC 日；逐序列验收，三项不能相加。收盘/美债只在有效交易日出现；不要把快照采集时刻当行情日。空估值不覆盖旧值，逐项检查 `steps`，顶层 `status=ok` 也可能是部分成功。

也可用页面「采集 CRCL」，或带本地令牌调用 `POST /api/v1/crcl/refresh`，再持续订阅 `GET /api/v1/crcl/refresh/stream?job_id=…` 到终态。应用启动默认也会自动采集一次（`CRCL_STARTUP_COLLECT=0` 可关）；CLI 与页面二选一。

## 5. 验收：看库里的有效值，然后出具交接摘要

以下只读检查可在刷新前后各跑一次。保存两次输出；比较原始表主键覆盖和重点字段的非空点数、末期，同时核对同一期修订是否属于正常发布。缺少 WAL 同目录读权限或系统 sqlite3 不能打开时，优先使用项目 Python 的 `mode=ro` 如下；不要在运行中的数据库上用 `immutable=1` 忽略 WAL。

```bash
.venv312/bin/python - <<'PY'
import sqlite3

tables = """money_supply gdp cpi ppi pmi pmi_detail leverage social_finance
lpr industrial domestic_demand house_price real_estate_activity household_income
new_credit bond_yield demographics fiscal external_demand derived_monthly derived_quarterly""".split()
with sqlite3.connect("file:data/macro_data.db?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    for table in tables:
        print(table, db.execute(f'SELECT COUNT(*), MIN(date), MAX(date) FROM "{table}"').fetchone())
        for _, field, *_ in db.execute(f'PRAGMA table_info("{table}")'):
            if field not in ("date", "city"):
                print(" ", field, db.execute(
                    f'SELECT COUNT("{field}"), MAX(date) FROM "{table}" WHERE "{field}" IS NOT NULL'
                ).fetchone())
with sqlite3.connect("file:data/crcl_monitor.db?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    for query in (
        "SELECT metric, COUNT(*), MAX(date) FROM metric_points GROUP BY metric",
        "SELECT key, updated_at FROM snapshot",
        "SELECT run_id, source, status, message FROM collect_log WHERE run_id = (SELECT run_id FROM collect_log ORDER BY id DESC LIMIT 1) ORDER BY id",
    ):
        for row in db.execute(query):
            print(row)
PY
```

- **宏观**：核对原始表有效期，别用 `derived_monthly` 的最大日期（例如新一月债券已到、其它尚未发布）代表所有数据；核对 `derived_quarterly.hh_debt_to_income`、PMI 制造/服务、PPI 官方环比、财政支出、就业/社融分项、人口结构覆盖。黑盒闸门通过却丢字段时，保留前轮 vintage 作为对照；先查原始响应和合并规则，修复后定向重跑。如果源还没恢复而旧观测确已误删，可在锁内从经完整性校验的更新前 vintage 按原期次仅合并缺失字段，走既有暂存/校验/衍生重算/原子切换流程；勿整库回滚或给新一期套旧值。明确区分「有意移除无官方来源的旧推算」（如 PPI）与意外历史退化。
- **Circle**：按本轮 `run_id` 和实际各序列末期核对；复核最新季度、过期计划项及两个 flags。若服务已开，重新加载宏观首页和 `/crcl-monitor`，或 GET `/api/v1/refresh/status`、`/api/v1/crcl/overview`、`/api/v1/crcl/fundamentals`、`/api/v1/crcl/events`；全量 manifest 只覆盖自动环节。服务未开时，上面的库与直接调用即可完成数据验收，不必启动服务。
- **代码与 JSON 变动**：运行 `CRCL_STARTUP_COLLECT=0 .venv312/bin/python -m pytest -q backend/tests`、`git diff --check`；发现新增来源/口径/回退方式，同步更新本手册、来源参考和 CHANGELOG。一过性的当前期不要写成固定规则。
- **交接格式**：运行时间、宏观全量通过/保留数、后续补数与复验、Circle 各子源及最近有效期（Arc 三条分别核验）、手工季报和事件变更、未发布 / 已发布但取不到 / 已收到但未通过闸门分别列清。附官方出处和回读结果；不得把仅启动任务、HTTP 200、表行数一致当作更新完成。
