"""AI compute observation contract. Scopes describe disclosures, not an AI market total."""
from __future__ import annotations

ENTITIES: dict[str, dict] = {}


def _entity(key, name, group, *, cik=None, code=None, currency="USD", note="", tier="core"):
    ENTITIES[key] = dict(id=key, name=name, group=group, cik=cik, code=code,
                         currency=currency, note=note, tier=tier)


for key, name, group, cik in [
    ("NVDA", "NVIDIA", "芯片", 1045810), ("AMD", "AMD", "芯片", 2488),
    ("AVGO", "Broadcom", "互联", 1730168), ("MU", "Micron", "存储", 723125),
    ("TSM", "台积电", "晶圆封装", 1046179), ("ASX", "日月光投控", "晶圆封装", 1122411),
    ("MRVL", "Marvell", "互联", 1835632), ("ANET", "Arista", "互联", 1596532),
    ("COHR", "Coherent", "互联", 820318), ("AMKR", "Amkor", "晶圆封装", 1047127),
    ("MSFT", "Microsoft", "云客户", 789019), ("AMZN", "Amazon", "云客户", 1018724),
    ("GOOGL", "Alphabet", "云客户", 1652044), ("META", "Meta", "云客户", 1326801),
    ("ORCL", "Oracle", "云客户", 1341439), ("NBIS", "Nebius", "新云", 1513845),
    ("DELL", "Dell", "OEM", 1571996), ("SMCI", "Supermicro", "OEM", 1375365),
    ("HPE", "HPE", "OEM", 1645590), ("ETN", "Eaton", "电气设备", 1551182),
    ("VRT", "Vertiv", "电气设备", 1674101), ("DLR", "Digital Realty", "部署", 1297996),
    ("D", "Dominion", "部署", 715957), ("GEV", "GE Vernova", "电气设备", 1996810),
]:
    _entity(key, name, group, cik=cik, currency="TWD" if key in {"TSM", "ASX"} else "USD",
            note="公司/披露分部口径，不等同纯 AI 业务。")
for key, name, group, currency in [
    ("CRWV", "CoreWeave", "新云", "USD"), ("SKHYNIX", "SK hynix", "存储", "KRW"),
    ("SAMSUNG", "Samsung", "存储", "KRW"), ("SCHNEIDER", "Schneider Electric", "电气设备", "EUR"),
    ("SIEMENS_ENERGY", "Siemens Energy", "电气设备", "EUR"),
    ("HITACHI_ENERGY", "Hitachi Energy", "高压设备", "USD"),
    ("HD_HYUNDAI", "HD Hyundai Electric", "高压设备", "KRW"),
    ("HYOSUNG", "Hyosung Heavy Industries", "高压设备", "KRW"),
]:
    _entity(key, name, group, currency=currency, note="特色经营指标由官方表格或 Agent 提取；未披露值留空。")
for code, name, group, tier in [
    ("6669", "纬颖", "整机 ODM", "core"), ("2382", "广达", "整机 ODM", "core"),
    ("2317", "鸿海", "整机 ODM", "core"), ("3231", "纬创", "整机 ODM", "secondary"),
    ("2356", "英业达", "整机 ODM", "secondary"), ("4938", "和硕", "整机 ODM", "secondary"),
    ("2308", "台达电", "电源散热", "core"), ("3017", "奇鋐", "电源散热", "core"),
    ("2301", "光宝科", "电源散热", "secondary"), ("3324", "双鸿", "电源散热", "secondary"),
    ("3653", "健策", "电源散热", "secondary"), ("2421", "建准", "电源散热", "secondary"),
    ("6282", "康舒", "电源散热", "secondary"), ("6412", "群电", "电源散热", "secondary"),
    ("2368", "金像电", "PCB / 材料", "core"), ("2383", "台光电", "PCB / 材料", "core"),
    ("3044", "健鼎", "PCB / 材料", "secondary"), ("6274", "台燿", "PCB / 材料", "secondary"),
]:
    _entity("TW" + code, name, group, code=code, currency="TWD", tier=tier,
            note="合并总营收，非 AI 单项。纬创/纬颖按集团关系对照，不相加。" if code in {"6669", "3231"}
            else "合并总营收，受价格、产品组合、汇率及并表影响。")

METRICS: dict[str, dict] = {}


def _metric(key, label, unit, description, *, derived=False, nonnegative=False):
    METRICS[key] = dict(id=key, label=label, unit=unit, description=description,
                        derived=derived, nonnegative=nonnegative)


for key, label, desc in [
    ("revenue", "公司营收", "公司合并收入；分部/AI 收入另列，不跨产业链求和。"),
    ("segment_revenue", "相关业务收入", "仅使用原始披露范围，HPC、Data Center 不自动等于 AI。"),
    ("cloud_revenue", "云业务收入", "按公司披露层级；不把更大分部替代 Azure 独立收入。"),
    ("cloud_operating_income", "云业务营业利润", "未单列的业务利润留空。"),
    ("cost_of_revenue", "销售成本", "报告成本口径，包含在成本内确认的库存计提。"),
    ("gross_profit", "毛利额", "同口径收入减销售成本。"),
    ("operating_income", "营业利润", "经营结果，与投资收益、税及股数变化分开。"),
    ("net_income", "合并净利润", "现金流桥匹配合并范围，EPS 另用归普通股口径。"),
    ("operating_cash_flow", "经营现金流", "单季流量；累计数须同年、同范围拆季。"),
    ("cash_capex", "现金资本开支", "当季实际用现金购置或建设物业与设备（PP&E）的支出，按现金流表购置额、不扣处置收入；个别公司含资本化软件或无形资产，见来源。通常是集团口径，不等于纯 AI 支出。"),
    ("reported_capex", "公司披露资本开支", "业绩公告或法说会直接公布的当季 CapEx。微软计入当季开始的融资租赁，Meta 计入当季支付的融资租赁本金；各公司的定义不同，不由网页相加重建。"),
    ("finance_lease_additions", "新增融资租赁资产", "当期通过新融资租赁取得的资产金额，通常不是当期现金支出；不与租赁还本相加。"),
    ("finance_lease_principal", "融资租赁还本", "当期归还融资租赁的本金，是现金流出，不代表当期取得了同额设备。"),
    ("inventory_total", "总存货", "资产负债表期末净额，不能累计差分。"),
    ("inventory_raw", "原材料 / 耗材", "保持公司原始分类，Micron 原料与耗材合并。"),
    ("inventory_wip", "在制品", "未单列不填零；不等同生产周期或良率。"),
    ("inventory_finished", "产成品", "金额变化需与出货、成本和减值交叉核对。"),
    ("inventory_other", "其他 / 在途存货", "保留原始附注分类，不强制拆成三桶。"),
    ("trade_receivables", "贸易应收", "排除已确认的融资、税及代采应收。"),
    ("new_orders", "新接订单", "当期新增的已记入订单簿金额，按公司原披露口径；不含仅在洽谈的项目，不等于营收。"),
    ("backlog", "期末在手订单", "公司期末已接但尚未履约的订单余额。保留业务范围、取消或延期条件；不等于下季度营收。"),
    ("ai_server_revenue", "已确认营收", "仅限公司明确披露的 AI 服务器或 AI 系统当季确认收入，不以出货金额、集团营收或指引替代。"),
    ("rpo", "剩余履约义务", "合同期限、业务范围及定义变更必须保留。"),
    ("supply_commitments", "披露供应承诺", "不与存货或负债相加，条件与到期日另存。"),
    ("inventory_provision", "库存 / 采购义务计提", "原披露范围；计提、释放和核销不能混用。"),
    ("capex_guidance", "目标期资本投入指引", "公司原始定义的投入指引，不能将现金与新增租赁混用。"),
    ("monthly_revenue", "月度合并营收", "台湾官方法定合并月收入，原千元统一换算到 TWD。"),
    ("revenue_guidance", "目标期收入指引中点", "公司指引，保留上下限及目标期；不是实际或共识。"),
]:
    _metric(key, label, "currency", desc, nonnegative=key not in {"operating_income", "net_income", "gross_profit", "operating_cash_flow"})
_metric("eps", "稀释 EPS", "currency/share", "直接披露的单季 EPS，不用全年减九个月机械推导。")
_metric("eps_consensus", "固定目标期 EPS 共识", "currency/share", "锁定供应商、目标期及已验证 basis；口径未知只保留候选。")
_metric("gpu_hour_price", "每 GPU 小时公开报价", "USD/GPU-hour", "精确型号、配置和合同的挂牌价，不是成交价、利用率或整个市场均价。", nonnegative=True)
_metric("gpu_rental_composite", "现货与合约合成基准", "USD/GPU-hour", "SemiAnalysis公开Spot-Contract Composite，供应商加权并结合适用合约调查；按日期公布的基准，非单家挂牌、成交价或纯现货价。历史为当前可见版本，非当时已知值。", nonnegative=True)
_metric("it_capacity_mw", "运营 IT 容量", "MW", "保留权益/100%范围；不是实际耗电或 GPU 利用率。", nonnegative=True)
_metric("occupied_it_mw", "已占用 IT 容量", "MW", "出租/占用容量，不是电表负荷。", nonnegative=True)
_metric("book_to_bill", "订单销售比", "ratio", "同范围同期间订单 / 收入；季度与 TTM 不混用。", nonnegative=True)
for key, label, desc in [
    ("orders_yoy", "订单同比", "公司披露的订单变化；未披露的季度不插值。"),
    ("backlog_yoy", "积压订单同比", "与新增订单、交付收入分开。"),
    ("gross_margin", "毛利率", "同范围毛利 / 营收 × 100。"),
    ("operating_margin", "营业利润率", "同范围营业利润 / 营收 × 100。"),
    ("cloud_operating_margin", "云业务营业利润率", "仅在云收入与利润的披露范围一致时计算。"),
    ("revenue_yoy", "营收同比", "同一财季、同币种、同范围收入同比；额外周与重分类单列。"),
    ("eps_yoy", "稀释 EPS 同比", "同口径单季稀释EPS同比；上年EPS非正时不计算百分比。"),
    ("segment_revenue_yoy", "相关业务收入同比", "同一分部定义同比，重分类断线。"),
    ("cloud_revenue_yoy", "云业务收入同比", "reported 与恒汇率须分开；硬件销售与租赁服务范围保留。"),
    ("monthly_revenue_yoy", "月营收同比", "本月 / 上年同月 − 1。"),
    ("monthly_revenue_3m_yoy", "三个月合计营收同比", "最近三月合计 / 上年相同三月合计 − 1，不是同比简单平均。"),
]:
    _metric(key, label, "%", desc, derived=key not in {"orders_yoy", "backlog_yoy", "cloud_revenue_yoy"})
_metric("dio", "平均存货周转天数", "days", "期初期末平均库存 / 同期销售成本 × 财季实际天数。", derived=True)
_metric("dso", "平均贸易应收周转天数", "days", "期初期末平均贸易应收 / 同期收入 × 财季实际天数；总收入为信用销售代理。", derived=True)

PANELS = [
    dict(id="earnings", title="盈利现状与预期", question="盈利规模与增长速度，正在怎样变化？", entities=["NVDA", "AMD", "AVGO", "MU", "TSM", "MRVL"],
         charts=[("收入与增速", ["revenue", "segment_revenue", "revenue_yoy", "segment_revenue_yoy"]), ("经营利润率", ["gross_margin", "operating_margin"]), ("单季 EPS 与同比", ["eps", "eps_consensus", "eps_yoy"])],
         caveat="实际、指引、共识分线；同比增速降低与收入绝对下降分别判断。"),
    dict(id="demand", title="客户需求与资本投入", question="大客户实际花了多少资本开支，云收入有没有跟上？", entities=["MSFT", "AMZN", "GOOGL", "META", "ORCL", "CRWV", "NBIS"],
         charts=[("资本开支（CapEx）", ["cash_capex", "reported_capex"]), ("融资租赁", ["finance_lease_additions", "finance_lease_principal"]), ("云业务兑现", ["cloud_revenue", "cloud_revenue_yoy", "cloud_operating_margin"]), ("利润与现金承接", ["net_income", "operating_cash_flow"]), ("合同承接", ["rpo", "backlog"])],
         caveat="优先用现金资本开支比较；公司披露值受租赁与确认时点影响，不跨公司直接加总。集团投入不是独立云业务或纯 AI 投入。Meta 不填云分部收入，未披露的 Azure 独立利润留空。"),
    dict(id="manufacturing", title="制造与交付", question="新订单有没有接上？在手订单何时能兑现为营收？", entities=["DELL", "HPE", "TW6669", "TW2382", "TW2317", "TW3231", "TW2356", "TW2308", "TW3017", "TW2301", "TW3324", "TW2368", "TW2383", "TW3044", "TW6274", "TSM", "ASX", "SKHYNIX", "MU", "SAMSUNG", "AVGO", "MRVL", "ANET", "COHR", "AMKR", "SMCI", "TW3653", "TW2421", "TW6282", "TW6412", "TW4938"],
         charts=[("订单与排期", ["new_orders", "backlog", "ai_server_revenue"]), ("月度合并营收", ["monthly_revenue"]), ("月度营收增速", ["monthly_revenue_yoy", "monthly_revenue_3m_yoy"]), ("季度业务与利润", ["segment_revenue", "segment_revenue_yoy", "gross_margin"])],
         caveat="期末在手订单是余额，不与当季订单或营收相加；没有连续披露的量产或交付窗口见板块解读。合并月营收不是 AI 单项或出货量；纬创与纬颖不加总。"),
    dict(id="rental", title="算力租赁与使用条件", question="同一配置的算力报价与可观察供给怎样变化？", entities=[],
         charts=[("按需挂牌周价 · GetDeploying", ["gpu_hour_price"]), ("长期合成基准 · SemiAnalysis", ["gpu_rental_composite"])], caveat="挂牌周中位数与现货/合约合成基准口径不同，分图展示，不拼接、不等同成交或利用率。"),
    dict(id="power", title="电力与部署条件", question="设备订单是否兑现为可运营的 IT 容量？", entities=["ETN", "SCHNEIDER", "VRT", "DLR", "D", "GEV", "SIEMENS_ENERGY", "HITACHI_ENERGY", "HD_HYUNDAI", "HYOSUNG"],
         charts=[("设备交付", ["revenue", "segment_revenue", "segment_revenue_yoy"]), ("订单与积压同比", ["orders_yoy", "backlog_yoy"]), ("订单销售比", ["book_to_bill"]), ("运营与占用容量", ["it_capacity_mw", "occupied_it_mw"])],
         caveat="合同 MW、运营 IT MW 与实际耗电不同。Vertiv 订单披露频率变化保留，旧交期不视作当前。"),
    dict(id="quality", title="库存与盈利质量", question="扩张占用的资金，能否随交付和回款释放？", entities=["NVDA", "AMD", "MU", "TSM", "TW6669", "DELL", "SMCI", "MSFT"],
         charts=[("库存分类", ["inventory_raw", "inventory_wip", "inventory_finished", "inventory_other", "inventory_total"]), ("库存与贸易回款天数", ["dio", "dso"]), ("利润与经营现金", ["net_income", "operating_cash_flow"])],
         caveat="未单列分类不填零；DIO 用销售成本，DSO 用贸易应收。云服务器属 PP&E，不能用云商存货推 GPU 闲置。"),
]

GPU_CONFIGS = [
    ("RUNPOD_H100_SXM", "Runpod · H100 SXM · Secure", "H100 SXM", "H100 SXM", 1),
    ("RUNPOD_H200", "Runpod · H200 · Secure", "H200", "H200", 1),
    ("RUNPOD_B200", "Runpod · B200 · Secure", "B200", "B200", 1),
    ("RUNPOD_B300", "Runpod · B300 · Secure", "B300", "B300", 1),
    ("AZURE_H100_8", "Azure · H100 ×8 · East US · Linux 按需", "H100 SXM", "Standard_ND96isr_H100_v5", 8),
]
for key, name, group, _, _ in GPU_CONFIGS:
    _entity(key, name, group, note="公开目录报价；配套、地区、供应商和合同固定。")
PANELS[3]["entities"] = [x[0] for x in GPU_CONFIGS]
RENTAL_HISTORY = [("GETDEPLOYING_" + gpu, "nvidia-" + gpu.lower(), gpu) for gpu in ("H100", "H200", "B200", "B300")]
for key, slug, name in RENTAL_HISTORY:
    _entity(key, name, "市场租价历史", note="GetDeploying · 同型号按需挂牌周中位数；跨配置、跨供应商统计，不代表成交或利用率。")
PANELS[3]["entities"] = [x[0] for x in RENTAL_HISTORY] + PANELS[3]["entities"]
for gpu in ("H100", "A100", "B200"):
    key = "SEMIANALYSIS_" + gpu
    _entity(key, gpu, "长期租价基准", note="SemiAnalysis公开Spot-Contract Composite；当前版本历史，非GetDeploying挂牌中位数，不代表单笔成交。")
    PANELS[3]["entities"].append(key)
ENTITIES["TSM"]["code"] = "2330"
ENTITIES["ASX"]["code"] = "3711"

SOURCES = [
    dict(id="semianalysis_public", name="SemiAnalysis 公开长期曲线", method="浏览器公开页面取数", url="https://gpu-index.semianalysis.com/", cadence="Agent按需核验并追加", history="2026-11-17浏览器取得H100自2023-10-10、A100自2023-06-13、B200自2025-06-08的日期点；合成基准，非免费开放许可证数据集", status="available"),
    dict(id="rental_history", name="GetDeploying 租价历史", method="开放 JSON 数据集", url="https://getdeploying.com/dataset/gpu-prices", cadence="周度，每日修订本周", history="免费 CC BY 4.0，滚动53周；按需挂牌中位数，保留样本数与暂定标记", status="available"),
    dict(id="sec", name="SEC 标准财务", method="API", url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces", cadence="按申报更新", history="按公司/tag 回填，保留修订；季度派生需要预热", status="available"),
    dict(id="tw", name="台湾月营收", method="API / 固定表解析", url="https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv", cadence="月度，发布窗口补采", history="默认回补 36 个月，基期额外预取；现存历史非 PIT", status="available"),
    dict(id="gpu", name="GPU 公开报价", method="API", url="https://api.runpod.io/graphql", cadence="日度快照", history="首采起积累，不回填假历史", status="available"),
    dict(id="agent", name="官方特色经营披露", method="Agent / 固定表", url="https://www.sec.gov/edgar/search/", cadence="季报、电话会与重大公告", history="原文逐期提取；目标/合同/实际分开", status="available"),
    dict(id="consensus", name="盈利共识", method="条件接口", url="https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_eps_trend.html", cadence="供应商更新", history="须验口径、财季、权限；历史从合格快照积累", status="conditional"),
    dict(id="licensed_rental", name="Ornn / Silicon Data 基准", method="授权后接入", url="https://www.silicondata.com/products/silicon-index", cadence="供应商定义", history="自动监控/展示许可待确认，当前不调用", status="disabled"),
]
