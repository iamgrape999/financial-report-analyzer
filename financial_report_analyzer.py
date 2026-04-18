#!/usr/bin/env python3
"""
Financial report auto analyzer.

Reads a CSV file containing multi-period financial statements, calculates
profitability, liquidity, leverage, efficiency, and cash-flow indicators, then
writes a Markdown report with trend notes and risk flags.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


REQUIRED_COLUMNS = {
    "period",
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "current_assets",
    "current_liabilities",
    "inventory",
}


@dataclass(frozen=True)
class PeriodData:
    period: str
    revenue: float
    gross_profit: float
    operating_income: float
    net_income: float
    total_assets: float
    total_liabilities: float
    shareholders_equity: float
    current_assets: float
    current_liabilities: float
    inventory: float
    operating_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None


@dataclass(frozen=True)
class MetricRow:
    period: str
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    net_margin: Optional[float]
    roe: Optional[float]
    roa: Optional[float]
    current_ratio: Optional[float]
    quick_ratio: Optional[float]
    debt_to_equity: Optional[float]
    asset_turnover: Optional[float]
    ocf_to_net_income: Optional[float]
    free_cash_flow: Optional[float]
    revenue_growth: Optional[float]
    net_income_growth: Optional[float]
    period_basis: str


def parse_money(value: str) -> float:
    cleaned = value.strip().replace(",", "")
    if cleaned in {"", "-", "N/A", "NA", "null", "None"}:
        return 0.0
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned)


def parse_optional_money(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned in {"", "-", "N/A", "NA", "null", "None"}:
        return None
    return parse_money(cleaned)


def safe_divide(numerator: float, denominator: float) -> Optional[float]:
    if denominator == 0:
        return None
    result = numerator / denominator
    if math.isfinite(result):
        return result
    return None


def period_sort_key(period: str) -> tuple[int, int, str]:
    text = period.strip()
    compact = text.upper().replace(" ", "")

    quarter_match = re.search(r"(\d{2,4})\D*Q([1-4])", compact)
    if not quarter_match:
        quarter_match = re.search(r"(\d{2,4})\D*第?([1-4])季", text)

    year_match = re.search(r"(\d{2,4})", compact)
    year = int(year_match.group(1)) if year_match else 0
    if 1 <= year < 1911:
        year += 1911

    quarter = int(quarter_match.group(2)) if quarter_match else 0
    return (year, quarter, text)


def period_basis(period: str) -> str:
    compact = period.upper().replace(" ", "")
    if re.search(r"Q[1-4]|第?[1-4]季", compact):
        return "單季"
    return "期間"


def load_csv(path: Path) -> list[PeriodData]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"CSV is missing required columns: {missing_text}")

        rows = []
        for index, row in enumerate(reader, start=2):
            try:
                rows.append(
                    PeriodData(
                        period=row["period"].strip(),
                        revenue=parse_money(row["revenue"]),
                        gross_profit=parse_money(row["gross_profit"]),
                        operating_income=parse_money(row["operating_income"]),
                        net_income=parse_money(row["net_income"]),
                        total_assets=parse_money(row["total_assets"]),
                        total_liabilities=parse_money(row["total_liabilities"]),
                        shareholders_equity=parse_money(row["shareholders_equity"]),
                        current_assets=parse_money(row["current_assets"]),
                        current_liabilities=parse_money(row["current_liabilities"]),
                        inventory=parse_money(row["inventory"]),
                        operating_cash_flow=parse_optional_money(
                            row.get("operating_cash_flow")
                        ),
                        capital_expenditure=parse_optional_money(
                            row.get("capital_expenditure")
                        ),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Invalid value at CSV row {index}: {exc}") from exc

    if len(rows) < 2:
        raise ValueError("At least two periods are required for trend analysis.")

    return sorted(rows, key=lambda row: period_sort_key(row.period))


def calculate_metrics(rows: Iterable[PeriodData]) -> list[MetricRow]:
    metrics = []
    previous: Optional[PeriodData] = None

    for row in rows:
        metrics.append(
            MetricRow(
                period=row.period,
                gross_margin=safe_divide(row.gross_profit, row.revenue),
                operating_margin=safe_divide(row.operating_income, row.revenue),
                net_margin=safe_divide(row.net_income, row.revenue),
                roe=safe_divide(row.net_income, row.shareholders_equity),
                roa=safe_divide(row.net_income, row.total_assets),
                current_ratio=safe_divide(row.current_assets, row.current_liabilities),
                quick_ratio=safe_divide(
                    row.current_assets - row.inventory, row.current_liabilities
                ),
                debt_to_equity=safe_divide(row.total_liabilities, row.shareholders_equity),
                asset_turnover=safe_divide(row.revenue, row.total_assets),
                ocf_to_net_income=(
                    safe_divide(row.operating_cash_flow, row.net_income)
                    if row.operating_cash_flow is not None
                    else None
                ),
                free_cash_flow=(
                    row.operating_cash_flow - row.capital_expenditure
                    if row.operating_cash_flow is not None
                    and row.capital_expenditure is not None
                    else None
                ),
                revenue_growth=(
                    safe_divide(row.revenue - previous.revenue, previous.revenue)
                    if previous
                    else None
                ),
                net_income_growth=(
                    safe_divide(row.net_income - previous.net_income, previous.net_income)
                    if previous
                    else None
                ),
                period_basis=period_basis(row.period),
            )
        )
        previous = row

    return metrics


def percentage(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def number(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def direction(
    current: Optional[float],
    previous: Optional[float],
    higher_is_better: bool = True,
) -> str:
    if current is None or previous is None:
        return "資料不足"
    delta = current - previous
    if abs(delta) < 0.005:
        return "大致持平"
    improved = delta > 0 if higher_is_better else delta < 0
    return "改善" if improved else "轉弱"


def build_findings(metrics: list[MetricRow]) -> list[str]:
    latest = metrics[-1]
    previous = metrics[-2]
    findings = []

    findings.append(
        f"最新一期營收成長率為 {percentage(latest.revenue_growth)}，"
        f"相較前一期趨勢{direction(latest.revenue_growth, previous.revenue_growth)}。"
    )
    findings.append(
        f"淨利率為 {percentage(latest.net_margin)}，"
        f"較前一期{direction(latest.net_margin, previous.net_margin)}。"
    )
    findings.append(
        f"ROE 為 {percentage(latest.roe)}，ROA 為 {percentage(latest.roa)}；"
        f"此處採 {latest.period_basis} 淨利 / 期末權益與資產，未做年化。"
    )
    findings.append(
        f"流動比率為 {number(latest.current_ratio)}，"
        f"較前一期{direction(latest.current_ratio, previous.current_ratio)}。"
    )
    findings.append(
        f"負債權益比為 {number(latest.debt_to_equity)}，"
        f"較前一期{direction(latest.debt_to_equity, previous.debt_to_equity, higher_is_better=False)}。"
    )
    findings.append(
        f"自由現金流為 {number(latest.free_cash_flow)}，營業現金流對淨利比為 "
        f"{number(latest.ocf_to_net_income)}；若來源截圖未包含現金流量表，這兩項會顯示 N/A。"
    )

    return findings


def build_risk_flags(metrics: list[MetricRow]) -> list[str]:
    latest = metrics[-1]
    flags = []

    if latest.revenue_growth is not None and latest.revenue_growth < 0:
        flags.append("最新一期營收衰退。")
    if latest.net_margin is not None and latest.net_margin < 0.05:
        flags.append("淨利率低於 5%，面對成本波動的緩衝較小。")
    if latest.current_ratio is not None and latest.current_ratio < 1:
        flags.append("流動比率低於 1，可能有短期償債壓力。")
    if latest.debt_to_equity is not None and latest.debt_to_equity > 2:
        flags.append("負債權益比高於 2，槓桿偏高。")
    if latest.ocf_to_net_income is not None and latest.ocf_to_net_income < 0.8:
        flags.append("營業現金流相對淨利偏弱，需檢查盈餘品質。")
    if latest.free_cash_flow is not None and latest.free_cash_flow < 0:
        flags.append("扣除資本支出後自由現金流為負。")

    return flags or ["目前未觸發主要規則式風險旗標。"]


def audit_rows(rows: list[PeriodData]) -> list[str]:
    warnings = []

    for row in rows:
        balance_total = row.total_liabilities + row.shareholders_equity
        if row.total_assets:
            gap_ratio = abs(row.total_assets - balance_total) / abs(row.total_assets)
            if gap_ratio > 0.01:
                warnings.append(
                    f"{row.period}：資產總計與負債加權益不一致，請回查資產負債表欄位。"
                )

        if row.current_assets > row.total_assets:
            warnings.append(
                f"{row.period}：流動資產大於總資產，疑似抓錯資產總計或流動資產欄位。"
            )
        elif row.total_assets and row.current_assets / row.total_assets > 0.85:
            warnings.append(
                f"{row.period}：流動資產佔總資產超過 85%，若公司並非高度流動資產型產業，"
                "請人工回查是否把資產總計抓成了其他小計。"
            )

        if row.current_liabilities > row.total_liabilities:
            warnings.append(
                f"{row.period}：流動負債大於總負債，疑似抓錯負債總計或流動負債欄位。"
            )

        if row.shareholders_equity and row.net_income / row.shareholders_equity > 0.2:
            warnings.append(
                f"{row.period}：單期 ROE 超過 20%，可能是高獲利，也可能是權益總計抓錯；"
                "請以原始資產負債表覆核。"
            )

    sorted_periods = [row.period for row in sorted(rows, key=lambda item: period_sort_key(item.period))]
    input_periods = [row.period for row in rows]
    if input_periods != sorted_periods:
        warnings.append(
            "輸入期間已依年份與季度重新排序，避免把舊期間誤判為最新期間。"
        )

    return warnings


def render_report(company: str, metrics: list[MetricRow], source: Path) -> str:
    latest = metrics[-1]
    findings = build_findings(metrics)
    risk_flags = build_risk_flags(metrics)

    lines = [
        f"# 財報分析報告：{company}",
        "",
        f"資料來源：`{source}`",
        f"最新期間：**{latest.period}**",
        "",
        "## 重點摘要",
        "",
    ]
    lines.extend(f"- {finding}" for finding in findings)

    lines.extend(
        [
            "",
            "## 關鍵指標",
            "",
            "| 期間 | 營收成長率 | 毛利率 | 營業利益率 | 淨利率 | ROE | ROA | 流動比率 | 負債權益比 | 自由現金流 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for row in metrics:
        lines.append(
            "| "
            f"{row.period} | "
            f"{percentage(row.revenue_growth)} | "
            f"{percentage(row.gross_margin)} | "
            f"{percentage(row.operating_margin)} | "
            f"{percentage(row.net_margin)} | "
            f"{percentage(row.roe)} | "
            f"{percentage(row.roa)} | "
            f"{number(row.current_ratio)} | "
            f"{number(row.debt_to_equity)} | "
            f"{number(row.free_cash_flow)} |"
        )

    lines.extend(
        [
            "",
            "## 風險旗標",
            "",
        ]
    )
    lines.extend(f"- {flag}" for flag in risk_flags)

    lines.extend(
        [
            "",
            "## 建議追問",
            "",
            "- 哪個營收項目變化最大？主要來自價格、銷量，還是產品組合？",
            "- 利潤率變化是因為產品組合、原物料成本、定價，還是營業槓桿？",
            "- 自由現金流變弱是暫時性營運資金變化，還是長期資本支出需求？",
            "- 若成長放緩，債務到期日與利息費用是否仍可控？",
            "",
            "## 注意事項",
            "",
            "本報告為規則式自動分析，投資決策前仍應搭配財報附註、管理階層討論、產業背景與市場資料交叉檢查。",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze financial statement CSV data.")
    parser.add_argument("input_csv", type=Path, help="Path to the financial statement CSV file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("financial_analysis_report.md"),
        help="Output Markdown report path.",
    )
    parser.add_argument(
        "-c",
        "--company",
        default="Company",
        help="Company name to display in the report.",
    )
    args = parser.parse_args()

    rows = load_csv(args.input_csv)
    metrics = calculate_metrics(rows)
    report = render_report(args.company, metrics, args.input_csv)
    audit_warnings = audit_rows(rows)
    if audit_warnings:
        warning_lines = "\n".join(f"- {warning}" for warning in audit_warnings)
        report += "\n## 資料品質警示\n\n" + warning_lines + "\n"
    args.output.write_text(report, encoding="utf-8-sig")
    print(f"Wrote report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
