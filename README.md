# 財報自動分析程式 Financial Report Analyzer

這是一個財報分析工具。它可以讀取多期財務資料 CSV，也可以讀取財報截圖，計算常見財務比率、趨勢與風險旗標，並輸出 Markdown 分析報告。

適合用來快速檢查公司多年財報趨勢，例如營收成長、毛利率、淨利率、ROE、流動比率、負債權益比與自由現金流。

## CSV 快速開始

如果你的電腦已經安裝 Python：

```powershell
python .\financial_report_analyzer.py .\sample_financials.csv -c "Sample Co" -o .\sample_report.md
```

執行後會產生 `sample_report.md`。

如果你在 Codex 工作區中使用內建 Python：

```powershell
& 'C:\Users\HAN-LI CHANG\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\financial_report_analyzer.py .\sample_financials.csv -c "Sample Co" -o .\sample_report.md
```

## CSV 欄位

輸入檔需要包含下列欄位：

```text
period,revenue,gross_profit,operating_income,net_income,total_assets,total_liabilities,shareholders_equity,current_assets,current_liabilities,inventory,operating_cash_flow,capital_expenditure
```

金額可使用純數字或逗號格式，負數可用 `-1000` 或 `(1000)`。

## 財報截圖分析

如果你有財報截圖，例如資產負債表與損益表的 PNG/JPG，可以使用：

```powershell
$env:GEMINI_API_KEY = "你的 Gemini API key"
python .\analyze_financial_screenshots.py .\balance_sheet.png .\income_statement_1.png .\income_statement_2.png -c "公司名稱" -o .\screenshot_report.md
```

如果截圖放在同一個資料夾，例如 `screenshots`：

```powershell
$env:GEMINI_API_KEY = "你的 Gemini API key"
python .\analyze_financial_screenshots.py .\screenshots -c "公司名稱" -o .\screenshot_report.md
```

程式會產生兩個檔案：

- `extracted_financials.csv`：從截圖辨識出的標準化財報資料
- `screenshot_report.md`：財務比率與趨勢分析報告

截圖分析預設使用 Gemini 視覺模型。API key 請只放在本機環境變數，不要寫進檔案，也不要上傳到 GitHub。

截圖至少需要包含兩個可比較期間。若只提供資產負債表與損益表，現金流相關指標會顯示 `N/A`，這是正常情況。

## 圖片萃取模型與 Prompt

截圖轉資料的上游流程在 `analyze_financial_screenshots.py`：

- 預設 provider：Gemini
- 預設模型：`gemini-2.5-flash`
- 可切換 provider：`--provider gemini` 或 `--provider openai`
- 可切換模型：`--model <model-name>`
- Prompt 位置：`build_prompt()` 函式

目前 prompt 要求模型同時回傳：

- 財報數字
- 每個欄位的來源會計科目
- 每個欄位的會計科目代碼
- 每個欄位的證據：欄位表頭、原始金額文字、完整列數字

核心資產負債表欄位會強制驗證來源科目與代碼，例如 `資產總計` 必須搭配 `1XXX`，`負債總計` 必須搭配 `2XXX`，`權益總計` 必須搭配 `3XXX`。若驗證失敗，程式會產生本機失敗報告並停止上傳 GitHub。

證據驗證會檢查：

- 欄位表頭必須對應該期間，例如 `114Q1`
- 原始金額文字必須與輸出的數值一致
- 完整列數字必須包含該儲存格金額

## 一鍵分析並上傳到 GitHub

如果你想把「截圖分析」與「上傳 GitHub」合併成一段指令，可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File ".\analyze_and_upload_to_github.ps1" -Images ".\screenshots" -Company "公司名稱"
```

如果尚未設定 `GEMINI_API_KEY` 或 `GITHUB_TOKEN`，腳本會提示你輸入，輸入時不會顯示在畫面上。

這會自動完成：

- 讀取截圖
- 產生 `extracted_financials.csv`
- 產生 `extracted_sources.json`
- 產生 `extracted_raw.json`
- 產生 `screenshot_report.md`
- 上傳到 GitHub 的 `reports/<時間>-<公司名稱>/` 資料夾

一鍵腳本預設採嚴格模式。如果程式偵測到時序、資產負債表小計、可疑比率，或核心欄位沒有對應到明確會計科目，會先產生本機報告但停止上傳，請人工覆核後再決定是否上傳。

台灣財報截圖會強制檢查下列來源科目：

- `total_assets` 必須來自 `資產總計` / `資產總額` / `資產合計`
- `total_liabilities` 必須來自 `負債總計` / `負債總額` / `負債合計`
- `shareholders_equity` 必須來自 `權益總計` / `權益總額` / `權益合計`
- `current_assets` 必須來自 `流動資產合計` / `流動資產總計` / `流動資產總額`
- `current_liabilities` 必須來自 `流動負債合計` / `流動負債總計` / `流動負債總額`

若 Gemini 抓到的數字沒有這些來源標籤，程式會直接判定失敗，不上傳 GitHub。

若你已人工確認警示可接受，可關閉嚴格模式：

```powershell
powershell -ExecutionPolicy Bypass -File ".\analyze_and_upload_to_github.ps1" -Images ".\screenshots" -Company "公司名稱" -RequireCleanAudit:$false
```

預設只上傳分析結果，不上傳原始截圖。若你也要把原始圖片放到 GitHub：

```powershell
powershell -ExecutionPolicy Bypass -File ".\analyze_and_upload_to_github.ps1" -Images ".\screenshots" -Company "公司名稱" -UploadImages
```

若你是用環境變數方式執行，完成後請清掉本機環境變數：

```powershell
Remove-Item Env:\GEMINI_API_KEY
Remove-Item Env:\GITHUB_TOKEN
```

## 目前會分析的項目

- 營收成長率
- 毛利率、營業利益率、淨利率
- ROE、ROA
- 流動比率、速動比率
- 負債權益比
- 資產週轉率
- 營業現金流對淨利比
- 自由現金流
- 規則式風險旗標

## 上傳到 GitHub

在有安裝 Git 的環境中，可以用以下步驟建立 GitHub repository：

```powershell
git init
git add .
git commit -m "Initial financial report analyzer"
git branch -M main
git remote add origin https://github.com/<your-account>/<repo-name>.git
git push -u origin main
```

上傳後 GitHub Actions 會自動執行範例分析，確認程式可以正常產生報告。

## 後續可擴充

- 讀取 Excel 財報
- 匯入公開資訊觀測站或 SEC 資料
- 產生圖表與 PDF
- 加入同業比較
- 串接 LLM 產生更自然的中文分析段落
