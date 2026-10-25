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
from io import StringIO
from urllib.parse import urljoin

import pandas as pd
import requests
from lxml import html as lxml_html

NBS_RELEASE_INDEX = "https://www.stats.gov.cn/sj/zxfb/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月")


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    return response


def find_releases(keyword: str, limit: int = 1, max_pages: int = 24) -> list[dict]:
    """Return newest NBS release pages whose title contains ``keyword``."""
    if limit < 1:
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for page_index in range(max_pages):
        page_url = NBS_RELEASE_INDEX + (
            "" if page_index == 0 else f"index_{page_index}.html"
        )
        doc = lxml_html.fromstring(_get(page_url).content)
        for anchor in doc.xpath("//a[@title]"):
            title = (anchor.get("title") or "").strip()
            href = (anchor.get("href") or "").strip()
            if keyword not in title or not href:
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
