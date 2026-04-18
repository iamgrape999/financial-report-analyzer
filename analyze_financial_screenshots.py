#!/usr/bin/env python3
"""
Analyze financial statement screenshots.

This script sends one or more financial report screenshots to a vision-capable
AI API, extracts the fields required by financial_report_analyzer.py, writes a
normalized CSV file, then generates the Markdown analysis report.
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
    audit_rows,
    calculate_metrics,
    load_csv,
    period_sort_key,
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

GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "company": {"type": "STRING", "nullable": True},
        "currency_unit": {"type": "STRING", "nullable": True},
        "periods": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "period": {"type": "STRING"},
                    "revenue": {"type": "NUMBER", "nullable": True},
                    "gross_profit": {"type": "NUMBER", "nullable": True},
                    "operating_income": {"type": "NUMBER", "nullable": True},
                    "net_income": {"type": "NUMBER", "nullable": True},
                    "total_assets": {"type": "NUMBER", "nullable": True},
                    "total_liabilities": {"type": "NUMBER", "nullable": True},
                    "shareholders_equity": {"type": "NUMBER", "nullable": True},
                    "current_assets": {"type": "NUMBER", "nullable": True},
                    "current_liabilities": {"type": "NUMBER", "nullable": True},
                    "inventory": {"type": "NUMBER", "nullable": True},
                    "operating_cash_flow": {"type": "NUMBER", "nullable": True},
                    "capital_expenditure": {"type": "NUMBER", "nullable": True},
                },
                "required": OUTPUT_COLUMNS,
                "propertyOrdering": OUTPUT_COLUMNS,
            },
        },
        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["company", "currency_unit", "periods", "warnings"],
    "propertyOrdering": ["company", "currency_unit", "periods", "warnings"],
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


def image_to_inline_data(path: Path) -> dict[str, str]:
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"mime_type": mime_type, "data": encoded}


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
- For Taiwan ROC years, 114Q1 is later than 113Q1. Do not reverse them.
- Convert parenthesized amounts to negative numbers.
- Preserve the statement unit. If the report says amounts are in thousands,
  return the numbers exactly as shown in that unit and set currency_unit.
- Use null when a field is not visible in the screenshots.
- revenue means operating revenue.
- gross_profit means gross profit.
- operating_income means operating income.
- net_income means net income attributable to owners of the parent when shown;
  otherwise use net income.
- total_assets must be read from the explicit row 資產總計 / 資產總額.
  Never infer it from liabilities plus equity.
- total_liabilities must be read from the explicit row 負債總計 / 負債總額.
- shareholders_equity must be read from the explicit row 權益總計 / 權益總額.
  Do not use 股本, 保留盈餘, or 母公司業主權益 unless it is clearly the total equity row.
- current_assets must be read from 流動資產合計.
- current_liabilities must be read from 流動負債合計.
- inventory means inventories.
- After extraction, verify:
  total_assets approximately equals total_liabilities + shareholders_equity.
  current_assets is not greater than total_assets.
  current_liabilities is not greater than total_liabilities.
  If any check fails, set the uncertain field to null and add a warning.
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


def call_gemini(images: list[Path], company: Optional[str], model: str) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Please set GEMINI_API_KEY before running screenshot analysis.")

    parts: list[dict[str, Any]] = [{"text": build_prompt(company)}]
    for image in images:
        parts.append({"inline_data": image_to_inline_data(image)})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA,
        },
    }

    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error {exc.code}: {details}") from exc

    data = json.loads(raw)
    output_text = extract_gemini_text(data)
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


def extract_gemini_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates", [])
    for candidate in candidates:
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if isinstance(text, str):
                return text
    raise RuntimeError("Gemini response did not contain output text.")


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

    periods = sorted(periods, key=lambda row: period_sort_key(str(row.get("period", ""))))

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
        "--provider",
        choices=["gemini", "openai"],
        default=os.environ.get("FINANCIAL_IMAGE_PROVIDER", "gemini"),
        help="AI provider to use for screenshot extraction.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("FINANCIAL_IMAGE_MODEL"),
        help="Vision-capable model to use. Defaults to gemini-2.5-flash for Gemini or gpt-4.1-mini for OpenAI.",
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
    parser.add_argument(
        "--fail-on-audit-warning",
        action="store_true",
        help="Exit with an error after writing outputs if data-quality audit warnings are found.",
    )
    args = parser.parse_args()

    images = find_images(args.images)
    model = args.model
    if not model:
        model = "gemini-2.5-flash" if args.provider == "gemini" else "gpt-4.1-mini"

    if args.provider == "gemini":
        extraction = call_gemini(images, args.company, model)
    else:
        extraction = call_openai(images, args.company, model)
    write_csv(extraction, args.csv_output)

    company = args.company or extraction.get("company") or "Company"
    rows = load_csv(args.csv_output)
    metrics = calculate_metrics(rows)
    report = render_report(company, metrics, args.csv_output)

    audit_warnings = audit_rows(rows)
    if audit_warnings:
        audit_lines = "\n".join(f"- {warning}" for warning in audit_warnings)
        report += "\n## 資料品質警示\n\n" + audit_lines + "\n"

    warnings = extraction.get("warnings") or []
    if warnings:
        warning_lines = "\n".join(f"- {warning}" for warning in warnings)
        report += "\n## 截圖辨識提醒\n\n" + warning_lines + "\n"

    args.output.write_text(report, encoding="utf-8-sig")
    print(f"Wrote extracted data to {args.csv_output}")
    print(f"Wrote report to {args.output}")
    if audit_warnings and args.fail_on_audit_warning:
        raise RuntimeError(
            "Data-quality audit warnings were found. Review the report before uploading."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
