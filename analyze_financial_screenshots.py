#!/usr/bin/env python3
"""
Analyze financial statement screenshots.

This script sends one or more financial report screenshots to the OpenAI
Responses API, extracts the fields required by financial_report_analyzer.py,
writes a normalized CSV file, then generates the Markdown analysis report.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

from financial_report_analyzer import (
    REQUIRED_COLUMNS,
    calculate_metrics,
    load_csv,
    render_report,
)


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

OUTPUT_COLUMNS = [
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
    "operating_cash_flow",
    "capital_expenditure",
]


EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company": {"type": ["string", "null"]},
        "currency_unit": {"type": ["string", "null"]},
        "periods": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    column: {"type": ["number", "null"]}
                    for column in OUTPUT_COLUMNS
                    if column != "period"
                }
                | {"period": {"type": "string"}},
                "required": OUTPUT_COLUMNS,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["company", "currency_unit", "periods", "warnings"],
}


def find_images(paths: Iterable[Path]) -> list[Path]:
    images: list[Path] = []
    for path in paths:
        if path.is_dir():
            images.extend(
                sorted(
                    item
                    for item in path.iterdir()
                    if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
                )
            )
        elif path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(path)
        else:
            raise ValueError(f"Not an image file or directory: {path}")
    if not images:
        raise ValueError("No supported image files were found.")
    return images


def image_to_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_prompt(company: Optional[str]) -> str:
    company_hint = company or "the company shown in the screenshots"
    return f"""
You are extracting financial statement data for {company_hint}.

Read the screenshots carefully. They may be Traditional Chinese financial
statements containing balance sheet and income statement pages.

Return only the JSON object required by the schema.

Extraction rules:
- Extract one row per comparable period.
- Use the period label shown in the statement, such as 2025Q1 or 114Q1.
- Convert parenthesized amounts to negative numbers.
- Preserve the statement unit. If the report says amounts are in thousands,
  return the numbers exactly as shown in that unit and set currency_unit.
- Use null when a field is not visible in the screenshots.
- revenue means operating revenue.
- gross_profit means gross profit.
- operating_income means operating income.
- net_income means net income attributable to owners of the parent when shown;
  otherwise use net income.
- total_assets means assets total.
- total_liabilities means liabilities total.
- shareholders_equity means equity total.
- current_assets means current assets total.
- current_liabilities means current liabilities total.
- inventory means inventories.
- operating_cash_flow and capital_expenditure are usually not on balance sheet
  or income statement screenshots; use null unless a cash flow statement is
  visible.
- Add warnings for fields that are missing or uncertain.
""".strip()


def call_openai(images: list[Path], company: Optional[str], model: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Please set OPENAI_API_KEY before running screenshot analysis.")

    content: list[dict[str, Any]] = [{"type": "input_text", "text": build_prompt(company)}]
    for image in images:
        content.append(
            {
                "type": "input_image",
                "image_url": image_to_data_url(image),
                "detail": "high",
            }
        )

    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "financial_statement_extraction",
                "schema": EXTRACTION_SCHEMA,
                "strict": True,
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {details}") from exc

    data = json.loads(raw)
    output_text = extract_output_text(data)
    return json.loads(output_text)


def extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]

    chunks: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if not chunks:
        raise RuntimeError("OpenAI response did not contain output text.")
    return "".join(chunks)


def format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def write_csv(extraction: dict[str, Any], output_csv: Path) -> None:
    periods = extraction.get("periods", [])
    if len(periods) < 2:
        raise ValueError("At least two extracted periods are required for trend analysis.")

    with output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in periods:
            writer.writerow({column: format_csv_value(row.get(column)) for column in OUTPUT_COLUMNS})


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze financial statement screenshots.")
    parser.add_argument("images", nargs="+", type=Path, help="Image files or directories.")
    parser.add_argument("-c", "--company", help="Company name for the report.")
    parser.add_argument(
        "--model",
        default=os.environ.get("FINANCIAL_IMAGE_MODEL", "gpt-4.1-mini"),
        help="OpenAI vision-capable model to use.",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("extracted_financials.csv"),
        help="Where to save extracted structured financial data.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("financial_screenshot_report.md"),
        help="Where to save the Markdown analysis report.",
    )
    args = parser.parse_args()

    images = find_images(args.images)
    extraction = call_openai(images, args.company, args.model)
    write_csv(extraction, args.csv_output)

    company = args.company or extraction.get("company") or "Company"
    rows = load_csv(args.csv_output)
    metrics = calculate_metrics(rows)
    report = render_report(company, metrics, args.csv_output)

    warnings = extraction.get("warnings") or []
    if warnings:
        warning_lines = "\n".join(f"- {warning}" for warning in warnings)
        report += "\n## 截圖辨識提醒\n\n" + warning_lines + "\n"

    args.output.write_text(report, encoding="utf-8-sig")
    print(f"Wrote extracted data to {args.csv_output}")
    print(f"Wrote report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
