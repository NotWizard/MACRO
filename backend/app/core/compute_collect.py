"""Read-only public sources → validated, append-only observations.

No licensed rental index, unverified consensus, paid endpoint or invented history.
Each issuer/month is isolated, so a source failure never erases last-good facts.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import httpx
from lxml import html

from backend.app.core import compute_store as store
from backend.app.core.compute_catalog import ENTITIES, GPU_CONFIGS, RENTAL_HISTORY
from backend.app.core.locking import refresh_lock
from backend.app.schemas.compute import Observation, ObservationBatch, today

CACHE = store.ROOT / "data" / "ai_compute_cache"
_rate_lock = threading.Lock()
_last_request = 0.0

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfSales"],
    "gross_profit": ["GrossProfit"], "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"], "eps": ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"],
    "cash_capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipment"],
    "finance_lease_additions": ["FinanceLeaseRightOfUseAssetObtainedInExchangeForFinanceLeaseLiability", "RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability"],
    "finance_lease_principal": ["FinanceLeasePrincipalPayments"],
    "inventory_total": ["InventoryNet", "Inventories"],
    "inventory_raw": ["InventoryRawMaterialsNetOfReserves", "InventoryRawMaterialsAndSuppliesNetOfReserves"],
    "inventory_wip": ["InventoryWorkInProcessNetOfReserves"],
    "inventory_finished": ["InventoryFinishedGoodsNetOfReserves"],
    "trade_receivables": ["AccountsReceivableNetCurrent", "TradeReceivables"],
}
STOCK = {"inventory_total", "inventory_raw", "inventory_wip", "inventory_finished", "trade_receivables"}
# Amazon's filing tags the gross purchases line this way. The taxonomy label
# is broader than PP&E, so this fallback is not safe for other issuers.
AMAZON_PPE_TAG = "PaymentsToAcquireProductiveAssets"


def fetch(url, cache_key, *, body=None, force=False, ttl=21600):
    global _last_request
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{cache_key}.json"
    if not force and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text())
    headers = {"User-Agent": os.getenv("COMPUTE_SEC_USER_AGENT", "MACRO-Personal-Research/1.0"), "Accept-Encoding": "gzip, deflate"}
    with _rate_lock:
        delay = .3 - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(25, connect=10), follow_redirects=True, headers=headers) as client:
        response = client.post(url, json=body) if body is not None else client.get(url)
        response.raise_for_status()
        if cache_key.startswith("tw_"):
            text = response.content.decode("big5", errors="replace")
            value = {"html": text, "url": str(response.url)}
        else:
            value = response.json()
    # Runtime cache is just a transport optimization; observation vintages live in SQLite.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)
    return value


def _point(entity, metric, value, end, published, url, locator, *, start=None, frequency="quarterly", basis="US-GAAP", scope="consolidated", method="api", excerpt="", source_id="sec"):
    from backend.app.core.compute_catalog import METRICS
    return Observation(entity=entity, metric=metric, period_start=start, period_end=end, frequency=frequency,
                       value=value, unit=METRICS[metric]["unit"], currency=ENTITIES[entity]["currency"],
                       basis=basis, scope=scope, source_url=url, source_locator=locator, source_excerpt=excerpt,
                       published_at=published, source_id=source_id, method=method)


def sec_points(entity, payload, *, as_of=None):
    cutoff = as_of or today().isoformat()
    start_limit = f"{int(cutoff[:4]) - 7}-01-01"
    currency = ENTITIES[entity]["currency"]
    cik = ENTITIES[entity]["cik"]
    candidates = {}
    cumulative = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        facts = payload.get("facts", {}).get(taxonomy, {})
        for metric, tags in TAGS.items():
            if entity == "AMZN" and taxonomy == "us-gaap" and metric == "cash_capex":
                tags = [*tags, AMAZON_PPE_TAG]
            for priority, tag in enumerate(tags):
                concept = facts.get(tag, {})
                units = concept.get("units", {})
                unit_key = currency + "/shares" if metric == "eps" else currency
                for fact in units.get(unit_key, []):
                    end, published, start = fact.get("end", ""), fact.get("filed", ""), fact.get("start")
                    if not start_limit <= end <= cutoff or not published or published > cutoff:
                        continue
                    if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "20-F/A", "6-K"}:
                        continue
                    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start else 0
                    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"
                    locator = f"{taxonomy}:{tag}; accession={fact.get('accn')}; start={start}; end={end}; filed={published}"
                    value = float(fact["val"])
                    if metric in {"cash_capex", "finance_lease_principal"}:
                        value = abs(value)
                    basis = "US-GAAP" if taxonomy == "us-gaap" else "IFRS"
                    info = dict(entity=entity, metric=metric, value=value, end=end, published=published,
                                url=url, locator=locator, start=start, basis=basis, source_id="sec:" + entity)
                    rank = (published, -priority)
                    if metric in STOCK:
                        info.update(start=None, frequency="quarterly")
                    elif 65 <= days <= 105:
                        info["frequency"] = "quarterly"
                    elif 330 <= days <= 380:
                        info["frequency"] = "annual"
                    else:
                        if start and 105 < days < 330 and metric != "eps":
                            key = (metric, start, end, basis)
                            if key not in cumulative or rank > cumulative[key][0]:
                                cumulative[key] = (rank, info)
                        continue
                    key = (metric, info["start"], end, info["frequency"], basis)
                    if key not in candidates or rank > candidates[key][0]:
                        candidates[key] = (rank, info)
    # Single-quarter cashflow from same-year cumulative flows; never difference EPS or stocks.
    direct = [x[1] for x in candidates.values()]
    flow_pool = [x for x in direct if x["metric"] not in STOCK and x["metric"] != "eps"] + [x[1] for x in cumulative.values()]
    derived = []
    for current in flow_pool:
        if current.get("frequency") == "quarterly":
            continue
        previous = [x for x in flow_pool if x["metric"] == current["metric"] and x["basis"] == current["basis"]
                    and x["start"] == current["start"] and x["end"] < current["end"]
                    and (current["metric"] != "cash_capex" or x["url"] == current["url"])
                    and 65 <= (date.fromisoformat(current["end"]) - date.fromisoformat(x["end"])).days <= 105]
        if not previous:
            continue
        prior = max(previous, key=lambda x: x["end"])
        if any(x["metric"] == current["metric"] and x["end"] == current["end"] and x.get("frequency") == "quarterly" for x in direct):
            continue
        new_start = (date.fromisoformat(prior["end"]) + timedelta(days=1)).isoformat()
        derived.append({**current, "value": current["value"] - prior["value"], "start": new_start,
                        "published": max(current["published"], prior["published"]), "frequency": "quarterly",
                        "locator": "YTD difference; " + current["locator"] + "; minus " + prior["locator"]})
    out = []
    for info in direct + derived:
        try:
            out.append(_point(**info))
        except ValueError:
            # Negative cash-capex differences can signal a definition change; do not invent a repair.
            continue
    if not out:
        raise ValueError(f"{entity} 没有通过期间、单位和会计范围检查的事实")
    return out


def parse_tw(text, year, month, source_url):
    if f"{year - 1911}年{month}月份" not in text:
        raise ValueError("月表标题与请求年月不符（可能是拦截页或错误期间）")
    match = re.search(r"出表日期[：:]\s*(\d{3})/(\d{1,2})/(\d{1,2})", text)
    if not match:
        raise ValueError("月营收页面缺少出表日期，不假设历史发布时间")
    published = date(int(match[1]) + 1911, int(match[2]), int(match[3])).isoformat()
    codes = {e["code"]: k for k, e in ENTITIES.items() if e["code"]}
    out = []
    end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    for row in html.fromstring(text).xpath("//tr"):
        cells = ["".join(x.itertext()).strip() for x in row.xpath("./td")]
        if len(cells) < 5 or cells[0] not in codes:
            continue
        number = cells[2].replace(",", "").strip()
        if not re.fullmatch(r"\d+(\.\d+)?", number):
            continue
        entity = codes[cells[0]]
        out.append(_point(entity, "monthly_revenue", float(number) * 1000, end, published,
                          source_url, f"公司代號 {cells[0]}；當月營收；原始單位千新台幣", frequency="monthly",
                          basis="TIFRS", method="parser", source_id="tw:" + cells[0]))
    if not out:
        raise ValueError("未找到已配置公司的月营收行")
    return out


def collect_gpu(force=False):
    data = fetch("https://api.runpod.io/graphql", "runpod", body={"query": "query { gpuTypes { id displayName memoryInGb secureCloud securePrice } }"}, force=force, ttl=3600)
    if data.get("errors"):
        raise ValueError("Runpod GraphQL schema/error: " + str(data["errors"])[:250])
    out = []
    day = today().isoformat()
    exact = {"RUNPOD_H100_SXM": "H100 SXM", "RUNPOD_H200": "H200 SXM", "RUNPOD_B200": "B200", "RUNPOD_B300": "B300"}
    for entity, display in exact.items():
        row = next((r for r in data.get("data", {}).get("gpuTypes", []) if r["displayName"] == display), None)
        if row and row.get("secureCloud") and row.get("securePrice") is not None and row["securePrice"] > 0:
            out.append(_point(entity, "gpu_hour_price", row["securePrice"], day, day, "https://api.runpod.io/graphql",
                              f"gpuTypes id={row['id']}; secureCloud=true; securePrice; single-GPU catalog quote",
                              frequency="daily", basis="reported", scope="Secure Cloud / global catalog / on-demand starting quote", source_id="gpu:runpod"))
    if not out:
        raise ValueError("Runpod 未返回合法的精确型号 Secure 报价")
    return out


def collect_azure(force=False):
    url = "https://prices.azure.com/api/retail/prices"
    params = "serviceName eq 'Virtual Machines' and armRegionName eq 'eastus' and armSkuName eq 'Standard_ND96isr_H100_v5'"
    request = str(httpx.URL(url, params={"$filter": params}))
    data = fetch(request, "azure_h100", force=force, ttl=3600)
    items = list(data.get("Items", []))
    for n in range(5):
        nxt = data.get("NextPageLink")
        if not nxt:
            break
        if not nxt.startswith("https://prices.azure.com/"):
            raise ValueError("Azure 分页域名异常")
        data = fetch(nxt, f"azure_h100_page{n}", force=force, ttl=3600)
        items.extend(data.get("Items", []))
    candidates = [r for r in items if r.get("type") == "Consumption" and r.get("unitOfMeasure") == "1 Hour"
                  and r.get("currencyCode") == "USD" and r.get("isPrimaryMeterRegion") is True
                  and not any(t in (r.get("skuName", "") + r.get("productName", "")).lower() for t in ["windows", "spot", "low priority", "dev/test", "devtest"])
                  and r.get("effectiveStartDate", "")[:10] <= today().isoformat()]
    if len(candidates) != 1:
        raise ValueError(f"Azure 精确 Linux 按需配置不唯一或缺失（{len(candidates)} 条）")
    row = candidates[0]
    day = today().isoformat()
    return [_point("AZURE_H100_8", "gpu_hour_price", row["retailPrice"] / 8, day, day, request,
                   f"meterId={row['meterId']}; 8 GPU VM; Linux on-demand East US; retailPrice/8; effective={row['effectiveStartDate']}",
                   frequency="daily", basis="reported", scope="East US / Linux / on-demand / 8-GPU VM incl host resources", source_id="gpu:azure")]


def rental_history_points(entity, slug, data):
    meta = data.get("meta", {})
    if meta.get("license", "").upper() != "CC-BY-4.0" or meta.get("slug") != slug:
        raise ValueError("租价数据集型号或开放授权不符")
    published = meta["last_snapshot"][:10]
    url = f"https://getdeploying.com/dataset/gpu-prices/{slug}.json"
    out = []
    for row in data.get("data", []):
        if row.get("billing_type") != "ON_DEMAND" or row.get("reservation_months") is not None:
            continue
        value = row.get("median_price")
        if value is None or value <= 0 or row.get("provider_count", 0) < 1:
            continue
        out.append(_point(entity, "gpu_hour_price", value, row["date"], published, url,
            f"ON_DEMAND; median_price; week={row['date']}; CC BY 4.0",
            frequency="weekly", basis="reported", scope="GetDeploying / ON_DEMAND / listing-median / weekly",
            source_id="getdeploying:" + slug,
            excerpt=f"GetDeploying, CC BY 4.0; provider_count={row['provider_count']}; offering_count={row.get('offering_count')}; provisional={row['date'] == meta.get('provisional_week')}; advertised prices, not transactions"))
    if len(out) < 2:
        raise ValueError("租价历史不足两个真实观察点")
    return out


def collect_all(sources=("sec", "tw", "gpu", "rental_history"), history_months=36, force=False, progress_cb=None, stop_event=None):
    try:
        with refresh_lock(store.ROOT / "data" / ".ai_compute.lock"):
            return _collect(sources, history_months, force, progress_cb, stop_event)
    except BlockingIOError:
        return {"status": "busy", "message": "已有 AI 算力采集在运行"}


def _collect(sources, months, force, progress, stop):
    tasks = []
    if "rental_history" in sources:
        for entity, slug, _ in RENTAL_HISTORY:
            url = f"https://getdeploying.com/dataset/gpu-prices/{slug}.json"
            tasks.append(("rental_history:" + slug, lambda e=entity, s=slug, u=url:
                rental_history_points(e, s, fetch(u, "getdeploy_" + s, force=force, ttl=86400))))
    if "sec" in sources:
        for key, e in ENTITIES.items():
            if e["cik"]:
                url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{e['cik']:010d}.json"
                tasks.append(("sec:" + key, lambda k=key, u=url: sec_points(k, fetch(u, "sec_" + k, force=force))))
    if "tw" in sources:
        current = today().year * 12 + today().month - 2
        # Include 14 warm-up months for a full requested window of 3M YoY.
        for offset in range(months + 14 if months else 1):
            n = current - offset
            y, m = n // 12, n % 12 + 1
            if y < 2013:
                break
            for market in ("sii", "otc"):
                url = f"https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{y-1911}_{m}_0.html"
                cache_key = f"tw_{market}_{y}_{m}"
                tasks.append((f"tw:{market}:{y}-{m:02d}", lambda u=url, k=cache_key, yy=y, mm=m: parse_tw(fetch(u, k, force=force, ttl=86400)["html"], yy, mm, u)))
    if "gpu" in sources:
        tasks += [("gpu:runpod", lambda: collect_gpu(force)), ("gpu:azure", lambda: collect_azure(force))]
    results = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="compute-source") as pool:
        futures = {pool.submit(fn): name for name, fn in tasks}
        for i, f in enumerate(as_completed(futures), 1):
            name = futures[f]
            if stop and stop.is_set():
                for pending in futures:
                    pending.cancel()
                break
            try:
                points = f.result()
                result = store.import_batch(ObservationBatch(observations=points))
                record = dict(source=name, status="ok", inserted=result["inserted"], message=f"校验 {len(points)} 条事实")
            except Exception as exc:
                record = dict(source=name, status="error", inserted=0, message=f"{type(exc).__name__}: {str(exc)[:450]}")
            store.log(**record)
            results.append(record)
            if progress:
                progress(i / max(len(tasks), 1))
    good = sum(r["status"] == "ok" for r in results)
    return dict(status="ok" if good == len(tasks) else "partial" if good else "error", steps=results,
                inserted=sum(r["inserted"] for r in results), completed=good, total=len(tasks),
                message=f"{good}/{len(tasks)} 个采集任务成功；失败保留上一版本")
