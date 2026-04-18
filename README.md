# 財報自動分析程式 Financial Report Analyzer

這是一個零第三方依賴的 Python 財報分析工具。它讀取多期財務資料 CSV，計算常見財務比率、趨勢與風險旗標，並輸出 Markdown 分析報告。

適合用來快速檢查公司多年財報趨勢，例如營收成長、毛利率、淨利率、ROE、流動比率、負債權益比與自由現金流。

## 快速開始

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
