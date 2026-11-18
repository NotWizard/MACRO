"""Offline checks for the NBS static-release parsers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from nbs_release import (  # noqa: E402
    parse_employment_release,
    parse_pmi_release,
    parse_real_estate_release,
    release_month,
)


def test_parse_pmi_release_combines_headline_and_component_tables():
    page = """
    <html><body>
      <table>
        <tr><th>月份</th><th>PMI</th><th>生产</th><th>新订单</th>
            <th>原材料库存</th><th>从业人员</th><th>供应商配送时间</th></tr>
        <tr><td>2026年8月</td><td>49.8</td><td>50.4</td><td>50.6</td>
            <td>48.1</td><td>48.7</td><td>50.1</td></tr>
        <tr><td>2026年9月</td><td>50.1</td><td>51.7</td><td>50.5</td>
            <td>48.2</td><td>48.4</td><td>50.1</td></tr>
      </table>
      <table>
        <tr><th>月份</th><th>新出口订单</th><th>进口</th><th>采购量</th>
            <th>购进价格</th><th>出厂价格</th><th>产成品库存</th>
            <th>在手订单</th><th>活动预期</th></tr>
        <tr><td>2026年8月</td><td>50.1</td><td>48.6</td><td>50.5</td>
            <td>56.6</td><td>50.4</td><td>48.4</td><td>46.7</td><td>53.8</td></tr>
        <tr><td>2026年9月</td><td>50.0</td><td>49.2</td><td>51.0</td>
            <td>60.8</td><td>54.0</td><td>47.6</td><td>46.0</td><td>53.8</td></tr>
      </table>
    </body></html>
    """
    frame = parse_pmi_release(page)
    assert list(frame["date"]) == ["2026-08-01", "2026-09-01"]
    assert frame.iloc[-1]["new_orders"] == 50.5
    assert frame.iloc[-1]["finished_goods_inventory"] == 47.6


def test_release_month_handles_cumulative_annual_and_half_year_titles():
    assert release_month("2026年1—9月份全国房地产市场基本情况") == "2026-09-01"
    assert release_month("2026年1—2月份全国房地产市场基本情况") == "2026-02-01"
    assert release_month("2025年上半年全国房地产市场基本情况") == "2025-06-01"
    assert release_month("2025年全国房地产市场基本情况") == "2025-12-01"


def test_employment_migrated_release_uses_original_dateline_and_monthly_rates():
    page = """<html><meta charset="utf-8"><body>
      <p>2022/11/15 10:00</p>
      <p>1-10月份，全国城镇调查失业率平均值为5.6%。10月份，全国城镇调查失业率为5.5%。
      31个大城市城镇调查失业率为6.0%。</p></body></html>"""
    assert parse_employment_release(page.encode(), "10月份国民经济保持恢复态势",
                                    "https://www.stats.gov.cn/sj/zxfb/202302/t20230203_1901651.html") == [
        {"date": "2022-10-01", "unemployment_rate": 5.5, "unemployment_31_city": 6.0}
    ]


def test_parse_real_estate_release_maps_cumulative_values_and_yoy():
    page = """
    <html><body><table>
      <tr><th>指标</th><th>绝对量</th><th>同比增长（%）</th></tr>
      <tr><td>房地产开发投资（亿元）</td><td>53364</td><td>-20.6</td></tr>
      <tr><td>房屋新开工面积（万平方米）</td><td>34031</td><td>-24.9</td></tr>
      <tr><td>房屋竣工面积（万平方米）</td><td>23271</td><td>-25.1</td></tr>
      <tr><td>新建商品房销售面积（万平方米）</td><td>56483</td><td>-13.2</td></tr>
      <tr><td>新建商品房销售额（亿元）</td><td>53799</td><td>-13.9</td></tr>
    </table></body></html>
    """
    row = parse_real_estate_release(
        page, "2026年1—9月份全国房地产市场基本情况")
    assert row is not None
    assert row["date"] == "2026-09-01"
    assert row["investment_cum"] == 53364
    assert row["new_start_area_yoy"] == -24.9
    assert row["sales_area_cum"] == 56483
    assert row["sales_value_yoy"] == -13.9
