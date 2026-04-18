param(
    [Parameter(Mandatory = $true)]
    [string[]]$Images,

    [string]$Company = "Company",
    [string]$Repository = "iamgrape999/financial-report-analyzer",
    [string]$Branch = "main",
    [ValidateSet("gemini", "openai")]
    [string]$Provider = "gemini",
    [string]$Model = "",
    [string]$OutputFolder = "",
    [bool]$RequireCleanAudit = $true,
    [switch]$UploadImages
)

$ErrorActionPreference = "Stop"

function Convert-SecureStringToPlainText {
    param([System.Security.SecureString]$SecureText)

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureText)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

if ($Provider -eq "gemini" -and -not $env:GEMINI_API_KEY) {
    $geminiKey = Read-Host "Gemini API key" -AsSecureString
    $env:GEMINI_API_KEY = Convert-SecureStringToPlainText -SecureText $geminiKey
}

if ($Provider -eq "openai" -and -not $env:OPENAI_API_KEY) {
    $openAiKey = Read-Host "OpenAI API key" -AsSecureString
    $env:OPENAI_API_KEY = Convert-SecureStringToPlainText -SecureText $openAiKey
}

$githubToken = $env:GITHUB_TOKEN
if (-not $githubToken) {
    $secureGitHubToken = Read-Host "GitHub token" -AsSecureString
    $githubToken = Convert-SecureStringToPlainText -SecureText $secureGitHubToken
}

function Get-PythonCommand {
    $bundledPython = "C:\Users\HAN-LI CHANG\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundledPython) {
        return $bundledPython
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python was not found. Please install Python or run this from the Codex workspace."
}

function Convert-ToSafeName {
    param([string]$Text)
    $safe = $Text -replace '[^A-Za-z0-9._-]+', '-'
    $safe = $safe.Trim("-")
    if (-not $safe) {
        return "company"
    }
    return $safe
}

function Convert-ToBase64Utf8 {
    param([string]$Text)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    return [Convert]::ToBase64String($bytes)
}

function Convert-FileToBase64 {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path))
    return [Convert]::ToBase64String($bytes)
}

function Get-GitHubContent {
    param(
        [string]$RepositoryName,
        [string]$Path,
        [hashtable]$Headers,
        [string]$TargetBranch
    )

    $encodedPath = [System.Uri]::EscapeDataString($Path).Replace("%2F", "/")
    $uri = "https://api.github.com/repos/$RepositoryName/contents/$encodedPath`?ref=$TargetBranch"

    try {
        return Invoke-RestMethod -Method Get -Uri $uri -Headers $Headers
    }
    catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) {
            return $null
        }
        throw
    }
}

function Send-GitHubFile {
    param(
        [string]$RepositoryName,
        [string]$LocalPath,
        [string]$RemotePath,
        [hashtable]$Headers,
        [string]$TargetBranch,
        [bool]$Binary = $false
    )

    $existing = Get-GitHubContent `
        -RepositoryName $RepositoryName `
        -Path $RemotePath `
        -Headers $Headers `
        -TargetBranch $TargetBranch

    $message = if ($existing) { "Update $RemotePath" } else { "Add $RemotePath" }
    $content = if ($Binary) {
        Convert-FileToBase64 -Path $LocalPath
    }
    else {
        Convert-ToBase64Utf8 -Text (Get-Content -LiteralPath $LocalPath -Raw -Encoding UTF8)
    }

    $body = @{
        message = $message
        content = $content
        branch  = $TargetBranch
    }

    if ($existing) {
        $body.sha = $existing.sha
    }

    $encodedRemotePath = [System.Uri]::EscapeDataString($RemotePath).Replace("%2F", "/")
    $uri = "https://api.github.com/repos/$RepositoryName/contents/$encodedRemotePath"
    $json = $body | ConvertTo-Json -Depth 5

    Invoke-RestMethod -Method Put -Uri $uri -Headers $Headers -Body $json -ContentType "application/json" | Out-Null
    Write-Host "Uploaded $RemotePath"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$companyName = Convert-ToSafeName -Text $Company

if (-not $OutputFolder) {
    $OutputFolder = "reports/$timestamp-$companyName"
}

$localOutputFolder = Join-Path -Path (Get-Location) -ChildPath $OutputFolder
New-Item -ItemType Directory -Force -Path $localOutputFolder | Out-Null

$csvOutput = Join-Path -Path $localOutputFolder -ChildPath "extracted_financials.csv"
$sourcesOutput = Join-Path -Path $localOutputFolder -ChildPath "extracted_sources.json"
$rawOutput = Join-Path -Path $localOutputFolder -ChildPath "extracted_raw.json"
$reportOutput = Join-Path -Path $localOutputFolder -ChildPath "screenshot_report.md"

$python = Get-PythonCommand
$scriptPath = Join-Path -Path (Get-Location) -ChildPath "analyze_financial_screenshots.py"
$modelArgs = @()
if ($Model) {
    $modelArgs = @("--model", $Model)
}
$auditArgs = @()
if ($RequireCleanAudit) {
    $auditArgs = @("--fail-on-audit-warning")
}

Write-Host "Analyzing screenshots..."
& $python $scriptPath @Images -c $Company --provider $Provider @modelArgs --csv-output $csvOutput --sources-output $sourcesOutput --raw-output $rawOutput -o $reportOutput @auditArgs

if ($LASTEXITCODE -ne 0) {
    throw "Screenshot analysis failed or data-quality audit warnings require review. Local outputs were kept in $localOutputFolder"
}

$headers = @{
    Authorization          = "Bearer $githubToken"
    Accept                 = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Host "Uploading analysis outputs to GitHub..."
Send-GitHubFile `
    -RepositoryName $Repository `
    -LocalPath $csvOutput `
    -RemotePath "$OutputFolder/extracted_financials.csv" `
    -Headers $headers `
    -TargetBranch $Branch

Send-GitHubFile `
    -RepositoryName $Repository `
    -LocalPath $sourcesOutput `
    -RemotePath "$OutputFolder/extracted_sources.json" `
    -Headers $headers `
    -TargetBranch $Branch

Send-GitHubFile `
    -RepositoryName $Repository `
    -LocalPath $rawOutput `
    -RemotePath "$OutputFolder/extracted_raw.json" `
    -Headers $headers `
    -TargetBranch $Branch

Send-GitHubFile `
    -RepositoryName $Repository `
    -LocalPath $reportOutput `
    -RemotePath "$OutputFolder/screenshot_report.md" `
    -Headers $headers `
    -TargetBranch $Branch

if ($UploadImages) {
    Write-Host "Uploading source images to GitHub..."
    foreach ($imagePath in $Images) {
        $resolved = Resolve-Path -LiteralPath $imagePath
        foreach ($item in $resolved) {
            if ((Get-Item -LiteralPath $item).PSIsContainer) {
                Get-ChildItem -LiteralPath $item -File | ForEach-Object {
                    $remotePath = "$OutputFolder/images/$($_.Name)"
                    Send-GitHubFile `
                        -RepositoryName $Repository `
                        -LocalPath $_.FullName `
                        -RemotePath $remotePath `
                        -Headers $headers `
                        -TargetBranch $Branch `
                        -Binary $true
                }
            }
            else {
                $file = Get-Item -LiteralPath $item
                $remotePath = "$OutputFolder/images/$($file.Name)"
                Send-GitHubFile `
                    -RepositoryName $Repository `
                    -LocalPath $file.FullName `
                    -RemotePath $remotePath `
                    -Headers $headers `
                    -TargetBranch $Branch `
                    -Binary $true
            }
        }
    }
}

Write-Host "Done: https://github.com/$Repository/tree/$Branch/$OutputFolder"
