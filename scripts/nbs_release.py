"""Read structured tables from the public NBS release archive.

The data.stats.gov.cn API is intermittently protected by a WAF that returns a
302 challenge to programmatic clients.  The same monthly releases are also
published as stable HTML pages on www.stats.gov.cn.  This module discovers
those pages from the release index and parses the two tables we need:

* China manufacturing PMI and its component indices (13 rolling months);
* real-estate investment, sales and construction (one cumulative snapshot for
  each monthly release).

Only published NBS values are parsed.  No estimate, carry-forward or imputation
is performed here.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from urllib.parse import urljoin

import pandas as pd
import requests
from lxml import html as lxml_html

NBS_RELEASE_INDEX = "https://www.stats.gov.cn/sj/zxfb/"
CIREA_PPI_HISTORY = "https://www.cirea.org.cn/content/4776"
EMPLOYMENT_HISTORY = {
    "unemployment_rate": "https://www.gotohui.com/gongzi/show-3713271",
    "unemployment_31_city": "https://www.gotohui.com/gongzi/show-281503",
}
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月")


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    return response


def find_releases(keyword: str | re.Pattern, limit: int = 1, max_pages: int = 24) -> list[dict]:
    """Return newest NBS release pages whose title contains ``keyword``."""
    if limit < 1:
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for page_index in range(max_pages):
        page_url = NBS_RELEASE_INDEX + (
            "" if page_index == 0 else f"index_{page_index}.html"
        )
        try:
            doc = lxml_html.fromstring(_get(page_url).content)
        except requests.RequestException:
            if not found:
                raise
            break
        for anchor in doc.xpath("//a[@title]"):
            title = (anchor.get("title") or "").strip()
            href = (anchor.get("href") or "").strip()
            if not href or not (keyword in title if isinstance(keyword, str) else keyword.search(title)):
                continue
            url = urljoin(page_url, href)
            if url in seen:
                continue
            seen.add(url)
            found.append({"title": title, "url": url})
        if len(found) >= limit:
            break
    return found[:limit]


def _number(value) -> float | None:
    text = re.sub(r"[,\s\u3000\xa0]", "", str(value))
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _date_rows(frame: pd.DataFrame) -> list[tuple[str, list[float | None]]]:
    rows = []
    for _, row in frame.iterrows():
        match = _DATE_RE.match(re.sub(r"[\s\u3000\xa0]", "", str(row.iloc[0])))
        if match:
            rows.append((
                f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-01",
                [_number(value) for value in row.iloc[1:]],
            ))
    return rows


def parse_pmi_release(page: str) -> pd.DataFrame:
    """Parse the headline and components from one NBS PMI release."""
    tables = pd.read_html(StringIO(page))
    dated = [(table, _date_rows(table)) for table in tables]
    headline = next(
        (rows for table, rows in dated
         if len(table.columns) >= 6 and rows and rows[0][1] and rows[0][1][0] is not None),
        None,
    )
    components = next(
        (rows for table, rows in dated
         if len(table.columns) >= 8 and rows and len(rows[0][1]) >= 8),
        None,
    )
    if not headline or not components:
        raise ValueError("NBS PMI release tables not recognised")

    headline_cols = [
        "pmi_official", "production", "new_orders",
        "raw_material_inventory", "employees", "supplier_delivery_time",
    ]
    component_cols = [
        "new_export_orders", "imports", "purchases", "input_prices",
        "ex_factory_prices", "finished_goods_inventory", "backlog_orders",
        "business_expectations",
    ]
    by_date = {date: values for date, values in components}
    rows = []
    for date, values in headline:
        extra = by_date.get(date)
        if extra is None or len(values) < len(headline_cols):
            continue
        row = {"date": date, **dict(zip(headline_cols, values))}
        row.update(dict(zip(component_cols, extra)))
        rows.append(row)
    if not rows:
        raise ValueError("NBS PMI release contained no complete monthly rows")
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def release_month(title: str) -> str | None:
    """Map a housing release title to its data month.

    ``1—9月份`` is a September cumulative release; an annual release is mapped to
    December.  Chinese New Year uses a combined ``1—2月份`` release.
    """
    match = re.search(r"(\d{4})年(?:1[—\-–])?(\d{1,2})月份?", title)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-01"
    match = re.search(r"(\d{4})年上半年", title)
    if match:
        return f"{int(match.group(1)):04d}-06-01"
    match = re.search(r"(\d{4})年全国房地产市场基本情况", title)
    if match:
        return f"{int(match.group(1)):04d}-12-01"
    return None


_REAL_ESTATE_FIELDS = {
    "房地产开发投资": ("investment_cum", "investment_yoy"),
    "房屋施工面积": ("construction_area_cum", "construction_area_yoy"),
    "房屋新开工面积": ("new_start_area_cum", "new_start_area_yoy"),
    "房屋竣工面积": ("completed_area_cum", "completed_area_yoy"),
    "新建商品房销售面积": ("sales_area_cum", "sales_area_yoy"),
    "新建商品房销售额": ("sales_value_cum", "sales_value_yoy"),
    "商品房待售面积": ("unsold_area", "unsold_area_yoy"),
    "房地产开发企业本年到位资金": ("funding_cum", "funding_yoy"),
}


def parse_real_estate_release(page: str, title: str) -> dict | None:
    """Parse one NBS cumulative real-estate release into a wide row."""
    date = release_month(title)
    if date is None:
        return None
    table = next(
        (frame for frame in pd.read_html(StringIO(page))
         if len(frame.columns) >= 3
         and frame.astype(str).apply(
             lambda col: col.str.contains("房地产开发投资", regex=False).any()
         ).any()),
        None,
    )
    if table is None:
        raise ValueError(f"real-estate table not recognised: {title}")

    row: dict[str, str | float | None] = {"date": date}
    for _, source in table.iterrows():
        label = re.sub(r"[\s\u3000\xa0]", "", str(source.iloc[0]))
        for prefix, (value_col, yoy_col) in _REAL_ESTATE_FIELDS.items():
            if label.startswith(prefix):
                row[value_col] = _number(source.iloc[1])
                row[yoy_col] = _number(source.iloc[2])
                break
    return row if len(row) > 1 else None


def fetch_pmi_details() -> pd.DataFrame:
    releases = find_releases("中国采购经理指数运行情况", limit=1)
    if not releases:
        raise LookupError("NBS PMI release not found")
    page = _get(releases[0]["url"]).content.decode("utf-8", errors="replace")
    return parse_pmi_release(page)


def fetch_ppi_headlines(limit: int = 3) -> pd.DataFrame:
    """Actual national ex-factory rates from NBS release titles, not estimates."""
    rows = []
    for release in find_releases("月份工业生产者出厂价格", limit=limit):
        title = release["title"]
        month = _DATE_RE.match(title)
        if not month:
            continue
        rates = {}
        for key, label in (("ppi_yoy", "同比"), ("ppi_mom", "环比")):
            m = re.search(rf"{label}\s*(持平|上涨|下降)\s*(\d+(?:\.\d+)?%?)?", title)
            if m and (m[1] == "持平" or m[2] and m[2].endswith("%")):
                rates[key] = 0.0 if m[1] == "持平" else float(m[2][:-1]) * (-1 if m[1] == "下降" else 1)
        if len(rates) == 2:
            rows.append({"date": f"{month[1]}-{int(month[2]):02d}-01",
                         **rates, "ppi_mom_source": release["url"]})
    return pd.DataFrame(rows, columns=["date", "ppi_yoy", "ppi_mom", "ppi_mom_source"])


def fetch_ppi_history() -> pd.DataFrame:
    """CIREA's published national PPI history; keep the original month text."""
    doc = lxml_html.fromstring(_get(CIREA_PPI_HISTORY).content)
    rows = {}
    for table in doc.xpath("//table"):
        trs = table.xpath(".//tr")
        if not trs:
            continue
        heading = re.sub(r"\s+", "", trs[0].text_content())
        if "全国同比" not in heading or "全国环比" not in heading:
            continue
        for tr in trs[1:]:
            cells = [td.text_content().strip() for td in tr.xpath("./td")]
            for i in (0, 3):
                if len(cells) < i + 3:
                    continue
                m = re.fullmatch(r"(\d{4})\.(0?[1-9]|1[0-2])", cells[i])
                mom = _number(cells[i + 2])
                if m and mom is not None and -15 <= mom <= 15:
                    day = f"{m[1]}-{int(m[2]):02d}-01"
                    rows[day] = {"date": day, "ppi_mom": mom,
                                 "ppi_mom_source": CIREA_PPI_HISTORY}
    return pd.DataFrame(rows.values(), columns=["date", "ppi_mom", "ppi_mom_source"])


_EMPLOYMENT_LABELS = {
    "unemployment_rate": r"全国城镇调查失业率",
    "unemployment_31_city": r"31个大城市(?:的)?城镇调查失业率",
}


def parse_employment_release(page: bytes, title: str, url: str) -> list[dict]:
    """Only actual observed rates, not YTD averages or percentage-point deltas."""
    doc = lxml_html.fromstring(page)
    # Many releases prior to the site migration live under /202302/ even
    # though their publication date is still available in the article header.
    publication = " ".join(doc.xpath('//meta[@name="PubDate"]/@content'))
    if not publication:
        # Use the visible dateline (a whole text node), not dates mentioned
        # in the article or the URL path of a migrated page.
        dateline = re.compile(r"\s*\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}\s*")
        publication = next((s for s in doc.xpath("//body//text()") if dateline.fullmatch(s)), "")
    published = re.search(r"(\d{4})[/-](\d{1,2})[/-]\d{1,2}", publication)
    if not published:
        published = re.search(r"/(\d{4})(\d{2})/", url)
    if not published:
        return []
    ym = re.match(r"(?:(\d{4})年)?(?:1[—–-])?(\d{1,2})月份?", title)
    last_month = int(published[2]) - 1 or 12
    year = int(published[1]) - int(last_month == 12)
    if ym:
        if int(ym[2]) != last_month or (ym[1] and int(ym[1]) != year):
            return []
    elif not ((last_month == 3 and "一季度" in title)
              or (last_month == 6 and "上半年" in title)
              or (last_month == 9 and "前三季度" in title)
              or (last_month == 12 and title.startswith(f"{year}年"))):
        return []
    # Read article paragraphs and data table rows only, excluding site chrome.
    text = re.sub(r"\s+", "", "\n".join(doc.xpath("//p//text()")))
    rows = {}
    for column, label in _EMPLOYMENT_LABELS.items():
        match = re.search(rf"{label}(?:为|是|降至|升至)(\d(?:\.\d+)?)%", text)
        if match:
            rate = float(match[1])
        else:
            rate = None
            for tr in doc.xpath("//tr"):
                td = [re.sub(r"\s+", "", cell.text_content()) for cell in tr.xpath("./td")]
                if len(td) >= 2 and re.fullmatch(rf"(?:[一二三四五六七八九十]+、)?{label}(?:[（(]％?%?[）)])?", td[0]):
                    rate = _number(td[1])
                    break
        if rate is not None and 2 <= rate <= 12:
            day = f"{year}-{last_month:02d}-01"
            rows.setdefault(day, {"date": day})[column] = rate
        if last_month == 2:
            pair = re.search(rf"1[、—–和]2月份[，,]?{label}分别为(\d(?:\.\d+)?)%[、，,和](\d(?:\.\d+)?)%", text)
            if pair:
                for month, number in enumerate(pair.groups(), 1):
                    if 2 <= float(number) <= 12:
                        day = f"{year}-{month:02d}-01"
                        rows.setdefault(day, {"date": day})[column] = float(number)
    return list(rows.values())


def fetch_employment_history(limit: int = 52) -> pd.DataFrame:
    """Fill historical gaps from public tables; official releases win overlaps."""
    columns = ["date", *_EMPLOYMENT_LABELS]
    rows = {}
    for column, url in EMPLOYMENT_HISTORY.items():
        try:
            doc = lxml_html.fromstring(_get(url).content)
            for tr in doc.xpath("//table[1]//tr"):
                cells = [td.text_content().strip() for td in tr.xpath("./td")]
                if len(cells) != 3 or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", cells[1]):
                    continue
                rate = _number(cells[2])
                if rate is not None and 2 <= rate <= 12:
                    day = cells[1] + "-01"
                    rows.setdefault(day, {"date": day})[column] = rate
        except (requests.RequestException, ValueError):
            continue

    def read(release):
        try:
            return parse_employment_release(_get(release["url"]).content, **release)
        except (requests.RequestException, ValueError):
            return []

    try:
        monthly = re.compile(r"^(?:\d{4}年)?(?:(?:1[—–-])?\d{1,2}月份?|一季度|上半年|前三季度)?\s*(?:国民经济|经济运行|经济发展)")
        # A full historical backfill is bounded to the archive through 2022.
        releases = find_releases(monthly, limit=limit, max_pages=62 if limit > 4 else 12)
    except requests.RequestException:
        releases = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for observations in executor.map(read, reversed(releases)):
            for row in observations:
                rows.setdefault(row["date"], {}).update(row)
    return pd.DataFrame(rows.values(), columns=columns)


def fetch_real_estate_activity(limit: int = 15) -> pd.DataFrame:
    releases = find_releases("全国房地产市场基本情况", limit=limit, max_pages=24)
    rows = []
    for release in releases:
        page = _get(release["url"]).content.decode("utf-8", errors="replace")
        row = parse_real_estate_release(page, release["title"])
        if row is not None:
            rows.append(row)
    if not rows:
        raise LookupError("NBS real-estate releases not found")
    return (pd.DataFrame(rows)
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True))
