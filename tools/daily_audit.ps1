# 每日自動查核：跑前一天的移櫃稽核並上傳（Windows 排程工作 ContainerAudit-Daily 呼叫）
$py = 'C:\Users\26516\AppData\Local\Programs\Python\Python312\python.exe'
$script = 'C:\Users\26516\container-audit\tools\audit_fetch.py'
$log = 'C:\Users\26516\container-audit\tools\daily_audit.log'
$env:PYTHONIOENCODING = 'utf-8'

# 日誌超過 500KB 就輪替
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 500KB)) {
    Move-Item $log "$log.old" -Force
}

$d = (Get-Date).AddDays(-1).ToString('yyyyMMdd')
Add-Content $log "`n===== [$(Get-Date -Format 'yyyy-MM-dd HH:mm')] 自動查核 $d ====="
cmd /c "`"$py`" -u `"$script`" --date $d >> `"$log`" 2>&1"
Add-Content $log "===== exit $LASTEXITCODE ====="
