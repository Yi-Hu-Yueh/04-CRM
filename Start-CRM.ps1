$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskTemp = Join-Path $PSScriptRoot '.qa\tmp'
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$crmPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $crmPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '無法建立專案虛擬環境。' }
}
& $crmPython -c 'import django' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $crmPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '套件安裝失敗。' }
}
& $crmPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw '資料結構初始化失敗。' }
& $crmPython manage.py needs_bootstrap
if ($LASTEXITCODE -eq 10) {
    & $crmPython manage.py bootstrap
    if ($LASTEXITCODE -ne 0) { throw '第一位員工帳號初始化未完成。' }
} elseif ($LASTEXITCODE -ne 0) { throw '無法檢查員工帳號。' }
$crmUrl = 'http://127.0.0.1:18040/'
try {
    $crmResponse = Invoke-WebRequest -Uri ($crmUrl + 'login/') -UseBasicParsing -TimeoutSec 3
    if ($crmResponse.StatusCode -eq 200 -and $crmResponse.Content -match '登入工作空間') {
        Write-Host ('CRM 已在執行：' + $crmUrl)
        return
    }
} catch {
    # No running CRM instance: start this project's local server below.
}
Write-Host ('CRM 本機網址：' + $crmUrl)
Write-Host '此為本機開發服務，按 Ctrl+C 停止。'
& $crmPython manage.py runserver 127.0.0.1:18040 --noreload
