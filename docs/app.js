const outputColumns = [
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
];

const sourceColumns = outputColumns.filter((column) => column !== "period");

const strictSourceTerms = {
  total_assets: ["資產總計", "資產總額", "資產合計"],
  total_liabilities: ["負債總計", "負債總額", "負債合計"],
  shareholders_equity: ["權益總計", "權益總額", "權益合計"],
  current_assets: ["流動資產合計", "流動資產總計", "流動資產總額"],
  current_liabilities: ["流動負債合計", "流動負債總計", "流動負債總額"],
};

const strictCodeTerms = {
  total_assets: ["1XXX"],
  total_liabilities: ["2XXX"],
  shareholders_equity: ["3XXX"],
};

const balanceSheetFields = new Set([
  "total_assets",
  "total_liabilities",
  "shareholders_equity",
  "current_assets",
  "current_liabilities",
  "inventory",
]);

let latestReport = "";
let latestCsv = "";
let latestJson = "";
let selectedFiles = [];

const form = document.querySelector("#analysis-form");
const imagesInput = document.querySelector("#images");
const clearImages = document.querySelector("#clear-images");
const preview = document.querySelector("#image-preview");
const statusEl = document.querySelector("#status");
const reportEl = document.querySelector("#report");
const downloadReport = document.querySelector("#download-report");
const downloadCsv = document.querySelector("#download-csv");
const downloadJson = document.querySelector("#download-json");

imagesInput.addEventListener("change", () => {
  selectedFiles = mergeFiles(selectedFiles, Array.from(imagesInput.files));
  imagesInput.value = "";
  renderPreview();
});

clearImages.addEventListener("click", () => {
  selectedFiles = [];
  imagesInput.value = "";
  renderPreview();
});

function renderPreview() {
  preview.innerHTML = "";
  for (const file of selectedFiles) {
    const image = document.createElement("img");
    image.alt = file.name;
    image.src = URL.createObjectURL(file);
    preview.append(image);
  }
}

function mergeFiles(existing, incoming) {
  const files = [...existing];
  const seen = new Set(existing.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of incoming) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!seen.has(key)) {
      files.push(file);
      seen.add(key);
    }
  }
  return files;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("分析中，請稍候...");
  setDownloads(false);

  try {
    const apiKey = document.querySelector("#api-key").value.trim();
    const company = document.querySelector("#company").value.trim();
    const model = document.querySelector("#model").value.trim();
    const files = selectedFiles;

    if (!files.length) {
      throw new Error("請先選擇財報截圖。");
    }

    const extraction = await callGemini(apiKey, model, company, files);
    const sourceErrors = validateSources(extraction);
    if (sourceErrors.length) {
      latestJson = JSON.stringify(extraction, null, 2);
      latestReport = renderFailureReport(sourceErrors);
      reportEl.textContent = latestReport;
      downloadJson.disabled = false;
      downloadReport.disabled = false;
      setStatus("來源驗證失敗，未產生正式分析。", "error");
      return;
    }

    const rows = normalizeRows(extraction.periods || []);
    const auditWarnings = auditRows(rows);
    const metrics = calculateMetrics(rows);
    latestCsv = toCsv(rows);
    latestJson = JSON.stringify(extraction, null, 2);
    latestReport = renderReport(company, metrics, auditWarnings, extraction.warnings || []);
    reportEl.textContent = latestReport;
    setDownloads(true);

    if (auditWarnings.length) {
      setStatus("已產生報告，但有資料品質警示。", "warn");
    } else {
      setStatus("分析完成。");
    }
  } catch (error) {
    reportEl.textContent = String(error.stack || error.message || error);
    setStatus(error.message || String(error), "error");
  }
});

downloadReport.addEventListener("click", () => downloadText("screenshot_report.md", latestReport));
downloadCsv.addEventListener("click", () => downloadText("extracted_financials.csv", latestCsv));
downloadJson.addEventListener("click", () => downloadText("extracted_raw.json", latestJson));

function setStatus(text, type = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${type}`.trim();
}

function setDownloads(enabled) {
  downloadReport.disabled = !enabled && !latestReport;
  downloadCsv.disabled = !enabled;
  downloadJson.disabled = !enabled && !latestJson;
}

async function callGemini(apiKey, model, company, files) {
  const parts = [{ text: buildPrompt(company) }];
  for (const file of files) {
    parts.push({ inline_data: await fileToInlineData(file) });
  }

  const payload = {
    contents: [{ role: "user", parts }],
    generationConfig: {
      responseMimeType: "application/json",
      responseSchema: buildGeminiSchema(),
    },
  };

  const endpoint = apiKey
    ? `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`
    : "./api/gemini";

  const headers = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["x-goog-api-key"] = apiKey;
  }

  const body = apiKey
    ? JSON.stringify(payload)
    : JSON.stringify({
        model,
        contents: payload.contents,
        generationConfig: payload.generationConfig,
      });

  const response = await fetch(endpoint, {
    method: "POST",
    headers,
    body,
  });

  const text = await response.text();
  if (!response.ok) {
    if (!apiKey && response.status === 404) {
      throw new Error("找不到 Cloudflare API proxy。若仍使用 GitHub Pages，請輸入 Gemini API key；若使用 Cloudflare Pages，請確認 functions/api/gemini.js 已部署。");
    }
    throw new Error(`Gemini API ${response.status}: ${text}`);
  }

  const data = JSON.parse(text);
  const output = data.candidates?.[0]?.content?.parts?.find((part) => part.text)?.text;
  if (!output) {
    throw new Error("Gemini 沒有回傳文字結果。");
  }
  return JSON.parse(output);
}

function buildGeminiSchema() {
  const numberFields = Object.fromEntries(
    sourceColumns.map((column) => [column, { type: "NUMBER", nullable: true }]),
  );
  const stringFields = Object.fromEntries(
    sourceColumns.map((column) => [column, { type: "STRING", nullable: true }]),
  );
  const evidenceValue = {
    type: "OBJECT",
    properties: {
      column_header: { type: "STRING", nullable: true },
      amount_text: { type: "STRING", nullable: true },
      row_values_text: { type: "STRING", nullable: true },
    },
    required: ["column_header", "amount_text", "row_values_text"],
    propertyOrdering: ["column_header", "amount_text", "row_values_text"],
  };
  const evidenceFields = Object.fromEntries(sourceColumns.map((column) => [column, evidenceValue]));

  return {
    type: "OBJECT",
    properties: {
      company: { type: "STRING", nullable: true },
      currency_unit: { type: "STRING", nullable: true },
      periods: {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            period: { type: "STRING" },
            ...numberFields,
            sources: {
              type: "OBJECT",
              properties: stringFields,
              required: sourceColumns,
              propertyOrdering: sourceColumns,
            },
            codes: {
              type: "OBJECT",
              properties: stringFields,
              required: sourceColumns,
              propertyOrdering: sourceColumns,
            },
            evidence: {
              type: "OBJECT",
              properties: evidenceFields,
              required: sourceColumns,
              propertyOrdering: sourceColumns,
            },
          },
          required: [...outputColumns, "sources", "codes", "evidence"],
          propertyOrdering: [...outputColumns, "sources", "codes", "evidence"],
        },
      },
      warnings: { type: "ARRAY", items: { type: "STRING" } },
    },
    required: ["company", "currency_unit", "periods", "warnings"],
    propertyOrdering: ["company", "currency_unit", "periods", "warnings"],
  };
}

function buildPrompt(company) {
  return `You are a strict accounting data extraction system for ${company}.

Read the screenshots carefully. They may be Traditional Chinese financial statements containing balance sheet and income statement pages.
Return only the JSON object required by the schema.
Do not invent, infer, calculate, or repair missing financial statement numbers. Only copy visible numbers.

Step 1: Anchor the period headers.
- Locate period headers above numeric columns.
- Taiwan statements usually place the latest period in the leftmost numeric column.
- For Taiwan ROC years, 114Q1 is later than 113Q1. Never reverse them.
- Use compact quarter period labels in the JSON period field. For example,
  use "114Q1" instead of "2025-03-31" or "民國 114 年 3 月 31 日".
- The amount must come from the intersection of the same account row and the selected period column.

Step 2: Extract only requested top-level accounts.
- For every numeric field, return exact source label, account code, and evidence.
- evidence.column_header: exact period header above the chosen amount.
- evidence.amount_text: exact visible amount copied from the chosen cell.
- evidence.row_values_text: full visible numeric row for that account, left to right.
- total_assets: 資產總計 / 資產總額, code 1XXX.
- total_liabilities: 負債總計 / 負債總額, code 2XXX.
- shareholders_equity: 權益總計 / 權益總額, code 3XXX.
- current_assets: 流動資產合計, code 11XX when visible.
- current_liabilities: 流動負債合計, code 21XX when visible.
- revenue: 營業收入淨額 / 營業收入合計, code 4000.
- gross_profit: 營業毛利 / 營業毛利淨額, code 5950.
- operating_income: 營業利益 / 營業淨利, code 6900.
- net_income: 本期淨利歸屬於母公司業主, code 8610.
- Use null when a field is not visible.

Step 3: Self-check before returning JSON.
- Verify selected period columns are not swapped.
- Verify source label, code, and amount are from the same row.
- Do not make the accounting equation work by inventing a number.`;
}

function fileToInlineData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const base64 = String(reader.result).split(",")[1];
      resolve({ mime_type: file.type || "image/png", data: base64 });
    };
    reader.readAsDataURL(file);
  });
}

function validateSources(extraction) {
  const errors = [];
  for (const row of extraction.periods || []) {
    const period = row.period || "unknown";
    for (const [field, terms] of Object.entries(strictSourceTerms)) {
      const source = row.sources?.[field];
      const code = row.codes?.[field];
      const evidence = row.evidence?.[field] || {};
      const value = row[field];

      if (value === null || value === undefined) {
        errors.push(`${period}: ${field} 缺值。`);
        continue;
      }
      if (!matchesAny(source, terms)) {
        errors.push(`${period}: ${field} 來源科目「${source}」不符合 ${terms.join(" / ")}。`);
      }
      if (strictCodeTerms[field] && !matchesAny(code, strictCodeTerms[field]) && !hasStrongRowEvidence(source, evidence)) {
        errors.push(`${period}: ${field} 來源代碼「${code}」不符合 ${strictCodeTerms[field].join(" / ")}。`);
      }
      if (!headerMatchesPeriod(field, period, evidence.column_header)) {
        errors.push(`${period}: ${field} 欄位表頭「${evidence.column_header}」未能語義對應。`);
      }
      if (!valueMatchesAmountText(value, evidence.amount_text)) {
        errors.push(`${period}: ${field} 數值 ${value} 與原始文字「${evidence.amount_text}」不一致。`);
      }
      if (!normalizeAmount(evidence.row_values_text).includes(normalizeAmount(evidence.amount_text))) {
        errors.push(`${period}: ${field} 完整列證據未包含金額「${evidence.amount_text}」。`);
      }
    }
  }
  return errors;
}

function hasStrongRowEvidence(source, evidence) {
  const normalizedSource = String(source || "").replace(/\s/g, "");
  const rowText = String(evidence?.row_values_text || "").replace(/\s/g, "");
  return Boolean(normalizedSource && rowText.includes(normalizedSource));
}

function normalizeRows(rows) {
  return [...rows].sort((a, b) => comparePeriod(a.period, b.period));
}

function comparePeriod(a, b) {
  const [ay, aq] = parsePeriod(a);
  const [by, bq] = parsePeriod(b);
  return ay - by || aq - bq || String(a).localeCompare(String(b));
}

function parsePeriod(period) {
  const text = String(period || "");
  const isoDate = text.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (isoDate) {
    const year = Number(isoDate[1]);
    const month = Number(isoDate[2]);
    const day = Number(isoDate[3]);
    if (month === 3 && day === 31) return [year, 1];
    if (month === 6 && day === 30) return [year, 2];
    if (month === 9 && day === 30) return [year, 3];
    if (month === 12 && day === 31) return [year, 4];
  }

  const yearMatch = text.match(/(\d{2,4})/);
  const quarterMatch = text.match(/Q([1-4])|第?([1-4])季/i);
  let year = yearMatch ? Number(yearMatch[1]) : 0;
  if (year > 0 && year < 1911) year += 1911;
  let quarter = quarterMatch ? Number(quarterMatch[1] || quarterMatch[2]) : 0;
  if (!quarter && /3月31日/.test(text)) quarter = 1;
  if (!quarter && /6月30日/.test(text)) quarter = 2;
  if (!quarter && /9月30日/.test(text)) quarter = 3;
  if (!quarter && /12月31日/.test(text)) quarter = 4;
  return [year, quarter];
}

function headerMatchesPeriod(field, period, header) {
  if (!header) return false;
  const [year, quarter] = parsePeriod(period);
  const text = String(header).replace(/\s/g, "");
  const rocYear = year >= 1911 ? year - 1911 : year;
  if (!text.includes(String(rocYear)) && !text.includes(String(year))) return false;
  if (balanceSheetFields.has(field)) {
    return (
      (quarter === 1 && text.includes("3月31日")) ||
      (quarter === 2 && text.includes("6月30日")) ||
      (quarter === 3 && text.includes("9月30日")) ||
      (quarter === 4 && text.includes("12月31日"))
    );
  }
  if (quarter === 1) return text.includes("1月1日") && text.includes("3月31日");
  if (quarter === 2) return text.includes("4月1日") && text.includes("6月30日");
  if (quarter === 3) return text.includes("7月1日") && text.includes("9月30日");
  return text.includes("10月1日") && text.includes("12月31日");
}

function matchesAny(value, terms) {
  const normalized = String(value || "").replace(/\s/g, "");
  return terms.some((term) => normalized.includes(term.replace(/\s/g, "")));
}

function normalizeAmount(value) {
  return String(value || "").replace(/[,$()\s]/g, "");
}

function valueMatchesAmountText(value, text) {
  if (value === null || value === undefined) return !text;
  const normalized = normalizeAmount(text);
  if (!normalized) return false;
  return Number(normalized) === Number(value);
}

function auditRows(rows) {
  const warnings = [];
  for (const row of rows) {
    const balanceTotal = Number(row.total_liabilities || 0) + Number(row.shareholders_equity || 0);
    const totalAssets = Number(row.total_assets || 0);
    if (totalAssets && Math.abs(totalAssets - balanceTotal) / Math.abs(totalAssets) > 0.01) {
      warnings.push(`${row.period}: 資產總計與負債加權益不一致。`);
    }
    if (Number(row.current_assets) > totalAssets) {
      warnings.push(`${row.period}: 流動資產大於總資產。`);
    }
    if (Number(row.current_liabilities) > Number(row.total_liabilities)) {
      warnings.push(`${row.period}: 流動負債大於總負債。`);
    }
  }
  return warnings;
}

function calculateMetrics(rows) {
  let previous = null;
  return rows.map((row) => {
    const metric = {
      period: row.period,
      revenue_growth: previous ? growthRate(row.revenue, previous.revenue) : null,
      gross_margin: divide(row.gross_profit, row.revenue),
      operating_margin: divide(row.operating_income, row.revenue),
      net_margin: divide(row.net_income, row.revenue),
      roe: divide(row.net_income, row.shareholders_equity),
      roa: divide(row.net_income, row.total_assets),
      current_ratio: divide(row.current_assets, row.current_liabilities),
      debt_to_equity: divide(row.total_liabilities, row.shareholders_equity),
      free_cash_flow:
        row.operating_cash_flow != null && row.capital_expenditure != null
          ? row.operating_cash_flow - row.capital_expenditure
          : null,
    };
    previous = row;
    return metric;
  });
}

function growthRate(current, previous) {
  if (!previous || previous < 0) return null;
  return (Number(current) - Number(previous)) / Math.abs(Number(previous));
}

function divide(a, b) {
  if (!b) return null;
  const result = Number(a) / Number(b);
  return Number.isFinite(result) ? result : null;
}

function pct(value) {
  return value == null ? "N/A" : `${(value * 100).toFixed(1)}%`;
}

function num(value) {
  return value == null ? "N/A" : Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function renderReport(company, metrics, auditWarnings, extractionWarnings) {
  const latest = metrics[metrics.length - 1];
  const lines = [
    `# 財報分析報告：${company}`,
    "",
    `最新期間：**${latest.period}**`,
    "",
    "## 重點摘要",
    "",
    `- 最新一期營收成長率為 ${pct(latest.revenue_growth)}。`,
    `- 毛利率為 ${pct(latest.gross_margin)}，營業利益率為 ${pct(latest.operating_margin)}，淨利率為 ${pct(latest.net_margin)}。`,
    `- ROE 為 ${pct(latest.roe)}，ROA 為 ${pct(latest.roa)}；採單期淨利 / 期末權益與資產，未做年化。`,
    `- 流動比率為 ${num(latest.current_ratio)}，負債權益比為 ${num(latest.debt_to_equity)}。`,
    "",
    "## 關鍵指標",
    "",
    "| 期間 | 營收成長率 | 毛利率 | 營業利益率 | 淨利率 | ROE | ROA | 流動比率 | 負債權益比 | 自由現金流 |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];

  for (const row of metrics) {
    lines.push(
      `| ${row.period} | ${pct(row.revenue_growth)} | ${pct(row.gross_margin)} | ${pct(row.operating_margin)} | ${pct(row.net_margin)} | ${pct(row.roe)} | ${pct(row.roa)} | ${num(row.current_ratio)} | ${num(row.debt_to_equity)} | ${num(row.free_cash_flow)} |`,
    );
  }

  if (auditWarnings.length) {
    lines.push("", "## 資料品質警示", "", ...auditWarnings.map((warning) => `- ${warning}`));
  }
  if (extractionWarnings.length) {
    lines.push("", "## 截圖辨識提醒", "", ...extractionWarnings.map((warning) => `- ${warning}`));
  }
  lines.push("", "## 注意事項", "", "本報告由瀏覽器端 AI/OCR 萃取生成，正式使用前請人工覆核原始財報截圖。");
  return `${lines.join("\n")}\n`;
}

function renderFailureReport(errors) {
  return `# 財報截圖萃取失敗\n\n以下欄位未通過來源與證據驗證，因此未產生正式分析報告：\n\n${errors
    .map((error) => `- ${error}`)
    .join("\n")}\n`;
}

function toCsv(rows) {
  const escape = (value) => {
    if (value === null || value === undefined) return "";
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [outputColumns.join(","), ...rows.map((row) => outputColumns.map((column) => escape(row[column])).join(","))].join("\n");
}

function downloadText(filename, content) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
