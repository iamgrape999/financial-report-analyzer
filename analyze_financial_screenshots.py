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

SOURCE_COLUMNS = [column for column in OUTPUT_COLUMNS if column != "period"]

SOURCE_FIELD_SCHEMA = {
    column: {"type": ["string", "null"]} for column in SOURCE_COLUMNS
}

GEMINI_SOURCE_FIELD_SCHEMA = {
    column: {"type": "STRING", "nullable": True} for column in SOURCE_COLUMNS
}

CODE_FIELD_SCHEMA = {
    column: {"type": ["string", "null"]} for column in SOURCE_COLUMNS
}

GEMINI_CODE_FIELD_SCHEMA = {
    column: {"type": "STRING", "nullable": True} for column in SOURCE_COLUMNS
}

STRICT_SOURCE_TERMS = {
    "total_assets": ["資產總計", "資產總額", "資產合計"],
    "total_liabilities": ["負債總計", "負債總額", "負債合計"],
    "shareholders_equity": ["權益總計", "權益總額", "權益合計"],
    "current_assets": ["流動資產合計", "流動資產總計", "流動資產總額"],
    "current_liabilities": ["流動負債合計", "流動負債總計", "流動負債總額"],
}

STRICT_CODE_TERMS = {
    "total_assets": ["1XXX"],
    "total_liabilities": ["2XXX"],
    "shareholders_equity": ["3XXX"],
    "current_assets": ["11XX"],
    "current_liabilities": ["21XX"],
}

OPTIONAL_SOURCE_TERMS = {
    "revenue": ["營業收入", "營業收入合計", "營業收入淨額"],
    "gross_profit": ["營業毛利", "營業毛利淨額", "毛利"],
    "operating_income": ["營業利益", "營業利益合計"],
    "net_income": ["本期淨利", "本期淨利歸屬於母公司業主", "淨利"],
    "inventory": ["存貨", "存貨合計"],
    "operating_cash_flow": ["營業活動之淨現金流入", "營業活動現金流量"],
    "capital_expenditure": ["取得不動產、廠房及設備", "資本支出"],
}

DISALLOWED_EQUITY_SOURCE_TERMS = ["股本", "保留盈餘", "母公司業主權益", "非控制權益"]


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
                | {
                    "period": {"type": "string"},
                    "sources": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": SOURCE_FIELD_SCHEMA,
                        "required": SOURCE_COLUMNS,
                    },
                    "codes": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": CODE_FIELD_SCHEMA,
                        "required": SOURCE_COLUMNS,
                    },
                },
                "required": OUTPUT_COLUMNS + ["sources", "codes"],
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
                    "sources": {
                        "type": "OBJECT",
                        "properties": GEMINI_SOURCE_FIELD_SCHEMA,
                        "required": SOURCE_COLUMNS,
                        "propertyOrdering": SOURCE_COLUMNS,
                    },
                    "codes": {
                        "type": "OBJECT",
                        "properties": GEMINI_CODE_FIELD_SCHEMA,
                        "required": SOURCE_COLUMNS,
                        "propertyOrdering": SOURCE_COLUMNS,
                    },
                },
                "required": OUTPUT_COLUMNS + ["sources", "codes"],
                "propertyOrdering": OUTPUT_COLUMNS + ["sources", "codes"],
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
You are a strict accounting data extraction system for {company_hint}.

Read the screenshots carefully. They may be Traditional Chinese financial
statements containing balance sheet and income statement pages.

Return only the JSON object required by the schema.

Do not invent, infer, calculate, or repair missing financial statement numbers.
Only copy numbers that are visible in the screenshots.

Step 1: Anchor the period headers.
- First locate the period headers above the numeric columns.
- Taiwan financial statements usually place the latest period in the leftmost
  numeric column.
- Identify the exact numeric column for the latest period and the exact numeric
  column for the year-earlier comparison period before extracting amounts.
- For Taiwan ROC years, 114Q1 is later than 113Q1. Never reverse them.
- The amount must come from the intersection of the same account row and the
  selected period column. Do not visually jump to nearby rows or columns.

Step 2: Extract only the requested top-level accounts.
- Extract one row per comparable period.
- Use the period label shown in the statement, such as 2025Q1 or 114Q1.
- Convert parenthesized amounts to negative numbers.
- Preserve the statement unit. If the report says amounts are in thousands,
  return the numbers exactly as shown in that unit and set currency_unit.
- Use null when a field amount is not visible in the screenshots.
- For every numeric field, return its exact source account label in sources.
  Example: total_assets sources must be 資產總計, not a nearby subtotal.
  If you cannot point to a specific account label, set the value to null and
  the source to null.
- Also return the exact account code shown at the left of that same source row
  in codes, such as 1XXX, 11XX, 2XXX, 21XX, or 3XXX. If no code is visible, set
  the code to null.
- If a valid source account label is found, read the amount from the same row
  under each requested period column. Do not return a source label with a null
  value unless that period's amount is genuinely not visible.
- For Taiwan quarterly statements with columns such as 114Q1, 113Q4, and
  113Q1, extract the latest quarter and the year-earlier comparison quarter
  when income statement data is available for those two periods.

Requested income statement accounts:
- revenue: 營業收入淨額 / 營業收入合計, code 4000.
- gross_profit: 營業毛利 / 營業毛利淨額, code 5950.
- operating_income: 營業利益 / 營業淨利, code 6900.
- net_income: 本期淨利歸屬於母公司業主, code 8610.

Requested balance sheet accounts:
- total_assets must be read from the explicit row 資產總計 / 資產總額.
  Never infer it from liabilities plus equity.
- total_assets must use account code 1XXX.
- total_liabilities must be read from the explicit row 負債總計 / 負債總額.
- total_liabilities must use account code 2XXX.
- shareholders_equity must be read from the explicit row 權益總計 / 權益總額.
  Use total equity because downstream checks require 資產總計 = 負債總計 + 權益總計.
  Do not use 股本, 保留盈餘, 母公司業主權益, or 非控制權益 unless the row is clearly
  the total equity row.
- shareholders_equity must use account code 3XXX.
- current_assets must be read from 流動資產合計.
- current_assets must use account code 11XX.
- current_liabilities must be read from 流動負債合計.
- current_liabilities must use account code 21XX.
- inventory means inventories.

Step 3: Self-check before returning JSON.
- Verify the selected period columns are not swapped.
- Verify the source account label and account code are from the same row as the
  amount.
- Verify:
  total_assets approximately equals total_liabilities + shareholders_equity.
  current_assets is not greater than total_assets.
  current_liabilities is not greater than total_liabilities.
- If any check fails, do not make the equation work by inventing a number.
  Keep the copied value, add a warning, and let the downstream validator decide.
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


def normalize_source(source: Any) -> str:
    if source is None:
        return ""
    return str(source).replace(" ", "").replace("　", "").strip()


def source_matches(source: Any, allowed_terms: list[str]) -> bool:
    normalized = normalize_source(source)
    return any(term.replace(" ", "") in normalized for term in allowed_terms)


def code_matches(code: Any, allowed_terms: list[str]) -> bool:
    normalized = normalize_source(code).upper()
    return any(term.upper() == normalized for term in allowed_terms)


def validate_sources(extraction: dict[str, Any]) -> list[str]:
    errors = []
    periods = extraction.get("periods", [])

    for row in periods:
        period = row.get("period", "unknown period")
        sources = row.get("sources") or {}
        codes = row.get("codes") or {}

        for field, allowed_terms in STRICT_SOURCE_TERMS.items():
            value = row.get(field)
            source = sources.get(field)
            code = codes.get(field)
            if value is None:
                if source:
                    errors.append(
                        f"{period}：{field} 來源科目為「{source}」，但數值缺失；"
                        "請回查截圖該科目在此期間的金額是否清楚可讀。"
                    )
                else:
                    errors.append(f"{period}：{field} 缺值，必須由明確會計科目萃取。")
                continue
            if not source_matches(source, allowed_terms):
                allowed = " / ".join(allowed_terms)
                errors.append(
                    f"{period}：{field} 來源科目為「{source}」，不符合必須來源「{allowed}」。"
                )
            if not code_matches(code, STRICT_CODE_TERMS[field]):
                allowed_codes = " / ".join(STRICT_CODE_TERMS[field])
                errors.append(
                    f"{period}：{field} 來源代碼為「{code}」，不符合必須代碼「{allowed_codes}」。"
                )

        equity_source = normalize_source(sources.get("shareholders_equity"))
        if any(term in equity_source for term in DISALLOWED_EQUITY_SOURCE_TERMS):
            errors.append(
                f"{period}：shareholders_equity 來源不可只用「{sources.get('shareholders_equity')}」，"
                "必須是權益總計 / 權益總額。"
            )

        for field, allowed_terms in OPTIONAL_SOURCE_TERMS.items():
            value = row.get(field)
            source = sources.get(field)
            if value is None and source:
                errors.append(f"{period}：{field} 數值為空，但來源科目卻填了「{source}」。")
            if value is not None and source and not source_matches(source, allowed_terms):
                allowed = " / ".join(allowed_terms)
                errors.append(
                    f"{period}：{field} 來源科目為「{source}」，建議來源應為「{allowed}」。"
                )

    return errors


def write_sources(extraction: dict[str, Any], output_json: Path) -> None:
    source_rows = []
    for row in extraction.get("periods", []):
        source_rows.append(
            {
                "period": row.get("period"),
                "sources": row.get("sources") or {},
                "codes": row.get("codes") or {},
            }
        )
    output_json.write_text(
        json.dumps(source_rows, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def write_raw_extraction(extraction: dict[str, Any], output_json: Path) -> None:
    output_json.write_text(
        json.dumps(extraction, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


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
        "--sources-output",
        type=Path,
        default=Path("extracted_sources.json"),
        help="Where to save source account labels for extracted fields.",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=Path("extracted_raw.json"),
        help="Where to save the full raw structured extraction.",
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

    write_raw_extraction(extraction, args.raw_output)
    source_errors = validate_sources(extraction)
    write_sources(extraction, args.sources_output)
    if source_errors:
        error_report = "\n".join(f"- {error}" for error in source_errors)
        args.output.write_text(
            "# 財報截圖萃取失敗\n\n"
            "以下欄位未能對應到允許的明確會計科目，因此未產生正式分析報告：\n\n"
            f"{error_report}\n",
            encoding="utf-8-sig",
        )
        print(f"Wrote raw extraction to {args.raw_output}")
        print(f"Wrote source labels to {args.sources_output}")
        print(f"Wrote validation failure report to {args.output}")
        raise RuntimeError(
            "Source account validation failed. Review the failure report before uploading."
        )

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
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
