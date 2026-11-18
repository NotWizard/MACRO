# 数据补充运行手册（Data Supplement Runbook）

> 目的：列出所有「靠手工 / Agent 补充」的数据，写明**从哪取 / 怎么取 / 取什么**，
> 供 Agent（Claude Code / Codex）按本手册定期补充进数据库。
>
> **为什么不全自动**：NIFD（国家金融与发展实验室）无公开 API，首页/列表页 JS 渲染、
> 无法稳定发现最新报告期，详情页数字解析易碎 → 硬塞进自动刷新易污染数据。
> 故采用「Agent 读取官方报告 → 提取数值 → 走闸门入库」的半自动方式。

---

## 0. 硬编码数据清单（Hardcoded Data Inventory）

### 交接：让 Agent 实际更新一次（不含 AI 算力）

1. **先核对基线**：从项目根目录读 `data/last_run.json`，记下现轮 `ts`；打开数据库时用 `sqlite3 -readonly data/macro_data.db`，按表记下 `COUNT(*)`、`MAX(date)`，以及本轮关心字段的 `MAX(date) WHERE 字段 IS NOT NULL`。单看表最大日期会掩盖某个分项未更新。数据与页面映射见 [覆盖审计](macro-data-coverage.md)。
2. **查官方发布，再改显式补充**：NIFD → `scripts/nifd_leverage.py:NIFD_DATA`；ISM、RatingDog PMI、财政收支、失业率 → 本手册表格列出的 `01_fetch_data.py` 补充常量；CRCL 季报与事件 → §9.2。记录原文链接、发布日期、参考期、单位和修改内容；未发布/未核实留空，日期到了也不自动把事件标为已发生。
3. **只跑一轮全量**：从项目根目录运行 `.venv312/bin/python scripts/01_fetch_data.py --full`；Apple Silicon 如果报 Expat 符号错误，先设置 `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`。网页「刷新数据」和无参数 CLI 只是发布窗口内的增量，不是全量；定向重试用 `--tables 表名 [表名 ...]`，不受日历限制。01 已经把原始与衍生表一起重算提交，不再跟跑 02。不要与 01/02/03/04 或网页宏观刷新并行。
4. **按状态验收**：退出 0 = 本轮选中的表通过闸门（不代表每张表都有新月份）；2 = 至少一表保留旧数据，看 `committed` 是部分落库还是完全未落库；3 = 衍生失败，全部未落库。比对新的 manifest 时间、`tables` / `sources`、库内最新有效期并跑 `PRAGMA integrity_check;`，逐表报告推进、未发布、源失败或闸门拒收。定向重试会生成自己的 manifest，先留存全量结果摘要。03/04 独立补充不更新 01 的 manifest，以脚本输出和库内实际期数核验。网站已打开时刷新页面读取 CLI 写入的库；只有页面/API 发起并成功落库的宏观刷新会自动触发 AI 评论重算。
5. **CRCL 单独运行**：在 CRCL 页点「采集 CRCL」，检查返回成功/失败项、各日序列末期及估值时间，再复核手工 JSON。宏观的 19 张表更新不包含 CRCL，更不包含算力。

### 显式补充数据

全仓排查（scripts/ analysis/ backend/ frontend/）后，**硬编码的时序数据**包括：

| 指标 | 硬编码位置 | 内容 | 当前到 | 更新方式 |
|---|---|---|---|---|
| NIFD 宏观杠杆率（居民/非金/政府/中央/地方/实体） | `scripts/nifd_leverage.py` `NIFD_DATA`（单一真相源，`01`/`03` 均 import） | 季度杠杆率(%) | 2026-06 (2026Q2) | §1 Agent 补充 |
| 美国 ISM 制造业 PMI | `scripts/01_fetch_data.py` `_ISM_SUPPLEMENT`（Jin10 源冻结于 2025-08 后的官方值补充表） | 月度 PMI（点） | 2026-09 | §7 Agent 逐月补官方值后跑 01 |
| RatingDog（原财新）制造/服务 PMI | `scripts/01_fetch_data.py` `_RATINGDOG_SUPPLEMENT` | 月度 PMI（点） | 2026-06 至 2026-09 | S&P Global 发布页（Investing.com 镜像）逐月复核；§10 |
| 2026-09 国家财政预算收支 | `scripts/01_fetch_data.py` `_MOF_SUPPLEMENT` | 累计亿元 / 累计同比 % | 2026-09 | 财政部前三季度发布；§10 |
| 全国/31 城调查失业率 | `scripts/01_fetch_data.py` `_UNEMPLOYMENT_SUPPLEMENT` | 月度 % | 2026-07 至 2026-09 | NBS 月度发布会；§10 |

**非时序数据（配置/分类逻辑，无需定期更新）**：
- 城市清单 `CITIES`（`frontend/src/pages/RealEstate.vue`）/ `_DEFAULT_CITIES`（`backend/app/api/v1/real_estate.py`）/ `01_fetch_data.py` 房价城市列表 —— 配置。
- `analysis/cycle_*.py`、`real_estate.py` 的 `conditions`/`choices`/`scores` —— 周期分类阈值/逻辑配置。

---

## 1. NIFD 宏观杠杆率（季度）—— 主要手工项

| 项 | 内容 |
|---|---|
| 目标表 | `leverage`（列：`date, household, non_fin_corp, gov_total, gov_central, gov_local, real_economy, fin_asset, fin_liability`） |
| 代码位置 | `scripts/nifd_leverage.py` 的 `NIFD_DATA`（单一真相源；`01`/`03` 均 `from nifd_leverage import nifd_supplement_df`） |
| 当前补到 | **2026-06（2026Q2）** |
| 数据源 | NIFD 季报 `http://www.nifd.cn/SeriesReport/Details/<id>`（实测可抓、含"杠杆率"） |
| 已知报告期 URL | 2025Q1=4712、2025Q2=4728、2025Q3=4800、2025Q4=4851、2026Q1=4896、2026Q2=4976；**2026Q3 及以后**需到 NIFD 网站「系列报告→宏观杠杆率」找最新 id（约季后 1 个月发布） |

**取什么**（%）：居民部门、非金融企业部门、政府部门、中央政府、地方政府、实体经济部门（`fin_asset`/`fin_liability` 可缺，填 None）。

**Agent 补充步骤**：
1. 到 NIFD「系列报告→宏观杠杆率」找**最新一期**季报 URL（取最新 id）。
2. 读取报告页正文，提取上述 6 个部门杠杆率数值。
3. 在 `scripts/nifd_leverage.py` 的 `NIFD_DATA` 末尾追加一行：
   `("YYYY-MM-01", household, non_fin_corp, gov_total, gov_central, gov_local, real_economy, fin_asset, fin_liability)`
   （`MM` = 季末月 03/06/09/12；如 2026Q2 → `2026-06-01`）。
4. 只补 NIFD 时跑 `.venv312/bin/python scripts/03_supplement_leverage.py`；若要连同 CNBS 重新比对，跑 `.venv312/bin/python scripts/01_fetch_data.py --tables leverage`。两者都已包含暂存校验与衍生重算。
5. 验证：`leverage` 表 `MAX(date)` 已更新；`derived_quarterly.hh_debt_to_income` 末行非空。

**校验规则**（防录错）：
- 各部门值应在合理区间：household 50–70、non_fin_corp 150–190、gov_total 50–75、real_economy 290–320。
- 宏观杠杆率 ≈ 居民 + 非金 + 政府（±1pp）。
- 单季变化应在 ±几 pp 内（异常大跳变需人工复核）。

---

## 2. 居民可支配收入 `household_income`（NBS，年度）—— 现已自动，监控

| 项 | 内容 |
|---|---|
| 目标表 | `household_income`（`income_per_capita, population_10k, income_abs`） |
| 源 | akshare `macro_china_nbs_nation`（NBS）。现到 2025（年度，正常）。 |
| 若 NBS 再封 | 参考 `01_fetch_data.py` 中 NBS 目录路径修正（「人民生活→全国居民人均收入情况」）；备选 World Bank。 |
| 取什么 | 人均可支配收入、总人口 → 计算 `income_abs`。 |

> 年度数据，NBS 每年初发布上年值；无需每季度补充。

---

## 3. GDP 季度参考期

| 项 | 内容 |
|---|---|
| 现状 | 累计季度序列，`date` 用该季首月：Q1→01-01、Q2→04-01、Q3→07-01、全年→10-01；不是发布日期。 |
| 源 | AKShare `macro_china_gdp`，采集脚本按累计期末季解析；无需另手工录一份。 |
| 校验 | 看最新季度的累计同比与全年 Q4 锚；债务收入比使用可用全年 GDP，不用 Q1×4。 |

---

## 4. PPI 环比 —— 国家统计局实际发布

- `--tables ppi`：东财保留同比长历史，国家统计局发布归档补最近 3 期全国出厂价格实际环比和同比，记录 `ppi_mom_source`。只有同比不能唯一确定环比，首次成功刷新会移除无法追溯到官方环比的旧推算值，缺期留空；之后自动保留已积累的官方环比历史。
- 如需继续补历史，只能核对官方全国出厂价格、参考月及原文（不是购进价格、累计增速或分行业环比），不能展开同比或用 0 填缺。2026-11-18 只读验证 [2026-10 官方稿](https://www.stats.gov.cn/sj/zxfb/202611/t20261109_1965794.html)：同比 +5.7%、环比 +0.7%，没有因此更新生产库。

---

## 5. 社融 `social_finance`（case 2：主源滞后，有备用源）

| 项 | 内容 |
|---|---|
| 现状 | DB 到 2026-09；主源 akshare `macro_china_shrzgm`（MOFCOM 镜像）在 Python 3.14 下仍可能因 `ssl.PROTOCOL_TLSv1_2` 缺失失败，`fetch_social_finance` 会先保留 staging 中的完整历史，再合并 PBoC 官方 XLSX 的新月份。 |
| 备用源 | PBoC 调查统计司 XLSX——**已抽为单一真相源 `scripts/pbc_shrzgm.py`**（主源 01 的 fetch_social_finance 与其共用）。列表页 `http://www.pbc.gov.cn/diaochatongjisi/116219/116319/.../shrzgm/index.html` → 最新 `attachDir/*.xlsx`。 |
| 独立补充脚本 | `scripts/04_supplement_social_finance.py`——脱离主管线的 CNBS 级联故障（`[Errno 9]`），单独经闸门补齐；主源滞后多期时优先跑它。 |
| 怎么取 | GET 列表页→正则取最新 xlsx→`pandas.read_excel(header=None)`→数据行约第 11 行起；月份为 float（2026.05→5 月；注意 2026.1==10 月）；列对齐 DB（社会融资规模增量/人民币贷款/委托贷款/信托贷款/未贴现承兑/企业债券/股票）。 |
| 校验 | 2026.05 total=20293 亿、2026.06=33645 亿（已实测）；仅追加 date>主源max 的行。 |
| 处理 | 01 自动走主备源；主源不可用或旧于库内、央行又无更新时，保持旧历史并报告保留。确认实际已发布但仍有缺口，再独立跑 04 或按原文排查；不要把空结果报成功。 |

---

## 6. GDP 季度（已常态化支持累计季度）

| 项 | 内容 |
|---|---|
| 现状 | `gdp` 表含累计季度行（「第1-2季度」→ 末季 04-01 入库），DB 到 2026-04（2026 上半年累计）。 |
| 源 | akshare `macro_china_gdp` / 东财 `RPT_ECONOMY_GDP`。 |
| 说明 | `fetch_gdp` 的 `parse_quarter` 已支持累计季度（正则 `^(\d{4})年第(\d)(?:-(\d))?季度`，累计取末季）；`02` 的 `hh_debt_to_income` 年化基数优先用该年 Q4(10月)累计行，当年无 Q4 用上年全年。 |

---

## 7. 定期更新窗口与手动触发（供 Agent 手动触发，非定时任务）

> 发布日历只决定普通增量刷新会尝试什么，不是某指标一定已发布的保证。错过窗口或做人工补充验证时用 `--tables` 或 `--full`；本次不要求安装定时任务。

| 频率 | 指标 | 发布窗口（次月） | 触发动作 |
|---|---|---|---|
| 月 | M2/新增信贷 (PBoC) | ~10-15 日 | 跑 `01_fetch_data.py` |
| 月 | 工业 / 社零 / 固投 / 10 城房价跟踪样本（NBS 70 城发布） / 房地产活动 | ~15 日 | 跑 `01_fetch_data.py` |
| 月 | CPI / PPI / PMI / PMI 分项 | ~9-10 日 / PMI 月末 | 跑 `01_fetch_data.py` |
| 月 | 社融 | ~15 日（主源滞后时自动走 PBoC XLSX 备用源） | 跑 `01_fetch_data.py` |
| 月 | LPR | ~20 日 | 跑 `01_fetch_data.py` |
| 月 | 美国 ISM 制造业 PMI | ~次月首个工作日 | 核对 ISM 官方新闻稿，在 `_ISM_SUPPLEMENT` 按**参考月**补值后跑 `01_fetch_data.py --tables external_demand`；不是录下月发布日期 |
| 季 | GDP（累计季度）、NIFD | 季度结束后 | GDP 自动采集；NIFD 按 §1 补充 |
| 季 | Circle 季报拆解 | Circle IR 财报发布后 | 按 §9.2 核对并补 JSON |
| 年 | 居民收入 / 人口 | 次年公报、WDI 发布后 | 核对两源的不同参考年；年频表可单独运行 `--tables household_income demographics` |

---

## 8. 数据获取方式区分（API 直连 vs Agent 网页获取）

> 原则：优先结构化数据；若自动来源滞后、缺值或格式不稳定，Agent 检查发布机构正文/PDF，只录可复核数值。沿用显式补充，不直接改生产库。

| 数据 | 获取方式 | 来源 |
|---|---|---|
| M2/M1/M0、新增信贷 | **API** | akshare（新浪/东财） |
| CPI / PMI / PPI 同比 | **API** | 东财数据中心 `RPT_ECONOMY_*`；PPI 最近几期优先 NBS |
| PPI 环比 | **官方发布档案** | 解析 NBS 最新 3 期，累积已有官方环比，未覆盖留空 |
| PMI 分项 | **API / HTML 表格** | NBS `www.stats.gov.cn/sj/zxfb/` 官方发布归档 |
| GDP（累计季度） | **API** | akshare `macro_china_gdp` |
| 财政收支 | **API + 官方补充** | NBS 主源；东财 `RPT_ECONOMY_INCOME` + 财政部发布页兜底 |
| 外需 货物进出口 | **API + 兜底** | NBS 主源；AKShare `macro_china_hgjck()` / 东财海关接口兜底 |
| 社零 / 固投 | **API** | 东财 `RPT_ECONOMY_TOTAL_RETAIL` / `RPT_ECONOMY_ASSET_INVEST` |
| 房地产活动 | **HTML 表格** | NBS 月度房地产发布归档（最近 15 期） |
| 全国/31 城失业率 | **API + 官方补充** | NBS API；最近三期使用发布会公布值 |
| 社融 | **API** 主源 + **文件下载** 备用 | akshare `shrzgm` + PBoC 调查统计司 XLSX（程序化解析 Excel，非读网页） |
| 债券收益率 | **API** | 中债信息网直连 |
| 人口 总人口/城镇化率 | **API** | akshare `macro_china_nbs_nation`（NBS） |
| 人口 出生率/自然增长率（2025） | **Agent 网页获取** | 《2025年国民经济和社会发展统计公报》stats.gov.cn（Web 搜索读取官方值 5.63‰ / -2.41‰） |
| 宏观杠杆率 NIFD | **Agent 网页获取** | NIFD 季报 nifd.cn / PDF（录入 `scripts/nifd_leverage.py:NIFD_DATA`） |
| 美国 ISM 制造业 PMI | **Agent 网页获取** | ISM 官方 / PR Newswire 月度发布（Workflow 逐月读取录入 `_ISM_SUPPLEMENT`） |
| CRCL 稳定币流通量/总盘/美债收益率/行情估值 | **API** | DefiLlama / Treasury.gov / AKShare / yfinance（启动+手动自动采集，见 §9.1） |
| CRCL 季报拆解/事件日历/标志位 | **手工 JSON** | Circle 新闻稿/财报会、Fed 日历、立法进展（`data/crcl_fundamentals.json`、`data/crcl_events.json`，见 §9.2） |

**手动触发命令**（项目根目录）：
```bash
.venv312/bin/python scripts/01_fetch_data.py --full
# 复核手工补充值后，也可只刷相关表：
.venv312/bin/python scripts/01_fetch_data.py --tables pmi fiscal external_demand domestic_demand
```

接口方式：本机同源 `GET /api/v1/session` 取得令牌，携带 `X-API-Token` 请求 `POST /api/v1/refresh?full=true`，再订阅 `GET /api/v1/refresh/stream?job_id=…` 直到 `done`；POST 的 `running` 不是落库成功。默认子进程硬超时 1560 秒、取数预算 1500 秒；若环境仍配置旧值 300 秒，全量优先走 CLI 或先确认配置。不要在日志或交接材料粘贴令牌。

验收用字段自己的有效期，不以 `MAX(date)`、固定债务比率或 HTTP 200 代替数据正确性。每表可同月修订；最新有效期倒退、无效日期或空源不应覆盖旧表。

---

## 9. CRCL 监控体系（Circle）—— API 自动 + 手工 JSON 混合

> 页面：`/crcl-monitor`；规范：`docs/CRCL监控体系.md`；进度：`docs/crcl_monitor_progress.md`。
> 存储：`data/crcl_monitor.db`（独立于 macro_data.db，互不影响）。

### 9.1 自动采集（API，无需手工）

| 数据 | 源 | 端点 / 接口 | 频率 |
|---|---|---|---|
| USDC 流通量历史 | DefiLlama | `stablecoins.llama.fi/stablecoincharts/all?stablecoin=2`（已去跨链桥重复） | 日 |
| EURC 流通量历史 | DefiLlama | `stablecoins.llama.fi/stablecoincharts/all?stablecoin=50`（欧元计价） | 日 |
| 稳定币总盘历史 | DefiLlama | `stablecoins.llama.fi/stablecoincharts/all` | 日 |
| 短端美债收益率 3M/6M/1Y | Treasury.gov | `home.treasury.gov/.../daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv`（回填 2 年；fiscaldata JSON API 实测不可用，弃用） | 日 |
| CRCL 日线 | AKShare 主 / yfinance 备 | `ak.stock_us_daily(symbol='CRCL')`；主源失败或无有效点时转 `yf.Ticker('CRCL').history(period='max', auto_adjust=False)` | 日 |
| CRCL 估值快照 | yfinance | `Ticker('CRCL').info`（marketCap/trailingPE/forwardPE/P-S/52 周） | 每次采集 |

> **环境注意**：yfinance 为惰性 import 的备用源。若 CRCL 采集结果中 `yfinance_valuation` 报
> `ModuleNotFoundError`，说明 venv 未装全依赖——在项目根目录跑
> `.venv312/bin/pip install -r requirements.txt` 后重试（2026-08-30 实测补装即恢复）。

触发：应用启动自动一次（`CRCL_STARTUP_COLLECT=0` 可关）+ 页内「采集 CRCL」。接口为令牌保护的 `POST /api/v1/crcl/refresh`，终态在对应 `GET /api/v1/crcl/refresh/stream?job_id=…`。空估值不覆盖已有快照；美债按年隔离失败；部分失败会在结果中列出。核验可读 `GET /api/v1/crcl/logs`、`/overview`，按本轮 `run_id` 对照。
代码：`backend/app/core/crcl_collect.py`；告警引擎 `crcl_alerts.py`（5 规则，状态变化写历史）。

### 9.2 手工补充（Agent / 人工，季度维护）

| 项 | 目标文件 | 当前补到 | 数据源 | 更新时机 |
|---|---|---|---|---|
| 季报拆解 | `data/crcl_fundamentals.json` → `quarters[]` | 2026Q1+Q2 | Circle 财报新闻稿 + 财报会转录 | 每季财报后（2/5/8/11 月初） |
| 标志位 | 同上 → `flags` | 已填 | Fed 决议 / 立法进展 | 事件发生时 |
| 宏观事件与里程碑 | `data/crcl_events.json` | 2026-10-25 手工快照；含待核实的旧计划 | Fed / SEC / Circle IR、立法原文 | 滚动维护 |

**Agent 补充步骤（每季财报）**：
1. 读 Circle 官方新闻稿（circle.com/pressroom）+ 财报会转录（Motley Fool 等）。
2. 提取：总收入 / 储备收入 / 其他收入 / EPS 实际与预期 / 分发成本 / USDC 期末流通 / CPN 统计。
3. 在 `quarters` 按 period 升序追加一条；派生字段：`nonreserve_share_pct` = 其他收入 ÷ 总收入 ×100；`distribution_cost_ratio_pct` = 分发成本 ÷ 总收入 ×100。
4. 逐项复核已过日期仍标为「计划」的事件（例如当前文件中的 2026Q3 财报），用官方原文确认实际日期和结果，并更新 details/status；延期或无证据须保留不确定性。刷新 `/crcl-monitor`，并运行 `.venv312/bin/python -m pytest -q backend/tests/test_crcl_json_schema.py backend/tests/test_crcl_alerts.py`。

**校验规则**（防录错）：
- `reserve_revenue_m + other_revenue_m ≈ total_revenue_m`（±1%）。
- `total_revenue_m` 当前量级 600–900；单季环比变化 >±20% 需复核。
- `nonreserve_share_pct` 应在 0–20；`distribution_cost_ratio_pct` 应在 40–75。
- EURC 无需手工（已自动采集）；若手工值与自动序列冲突，以自动序列为准。

---

## 自动化边界说明

- **AKShare `macro_cnbs`** 会在 CNBS 更新后自动带上（无需动作），但滞后大（现到 2024-12），不可依赖。
- **不建议**把 NIFD 抓取硬塞进自动刷新：发现最新期 + 解析数字不可靠，静默失败会污染数据库。
- 推荐节奏：每季度 NIFD 报告发布后（约季后 1 个月），由 Agent 按本手册 §1 补充一期。

---

## 10. 2026-10 NBS WAF 波动期间补齐口径

2026-10-25 实测 `data.stats.gov.cn` 查询接口会 302 到 `/waf_text_verify.html`，返回 HTML
而不是 JSON。以下路径只使用公开、可复核的官方发布值；失败时保持上一份通过闸门的数据，
不得用插值或“中性值”冒充真实观测。

| 表 | 自动获取 | 当前覆盖 | 解析失败行为 |
|---|---|---|---|
| `pmi_detail` | 读最新一期 PMI 发布页两张表 | 每次读 13 个滚动月；旧期累积保留 | 空结果或有效期倒退保留旧表 |
| `real_estate_activity` | 扫描最近 15 期「全国房地产市场基本情况」发布页 | 旧期累积保留；含开发投资、销售、新开工、竣工等 | 解析异常记录失败；旧表不覆盖 |
| `domestic_demand` | 东财社零 + 东财固投；失业率优先 NBS API | 社零/固投长历史；失业率 2026-07 至 09 | 闸门要求社零与固投均实际存在 |
| `fiscal` | NBS 优先；东财收入兜底；财政部支出补充 | 截至 2026-09 | 合并旧表后再校验，防止历史被截断 |
| `external_demand` | NBS 优先；东财海关兜底 | 贸易截至 2026-09，ISM 补充至 2026-09 | 只填 NBS 空值，不覆盖已存在官方值 |

**RatingDog PMI 缺口**：旧 AKShare/Jin10 接口停在 2025-09；2026-06 至 2026-09 已按
S&P Global 发布值补齐。2025-10 至 2026-05 的免费中段历史尚未取得，图中会保留真实缺口，
不以线性插值伪造。每月发布后先核对 S&P Global 原稿中的参考月（制造业与服务业分别核对），再按该月的 `YYYY-MM-01` 追加，不用发布日期。
