@echo off
rem 移櫃確認稽核：run_audit.bat [YYYYMMDD]  不給日期=查昨天
set PYTHONIOENCODING=utf-8
"C:\Users\26516\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0audit_fetch.py" %*
pause
