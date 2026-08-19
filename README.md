# 移櫃確認稽核（container-audit）

查核荷米斯APP「移櫃確認」回報的櫃號，該時段是否真的在彰化(4106)/秀水(4150)場區。

- 網頁：https://ares-1215.github.io/container-audit/ （通行碼進入）
- 資料來源：HCT報表查詢平台（內網）三報表交叉比對
  - 【運務】荷米斯APP-移櫃確認查詢（RPT_ID 139）
  - 【運務】報表-車廂光罩明細（RPT_ID 51）
  - 【運務】拆封櫃明細查詢（RPT_ID 79）

## 判定邏輯（證據並列＋燈號）

| 燈號 | 條件 |
|---|---|
| ✓ 正常 | 確認時間落在「光罩進站彰化/秀水 ~ 離站」區間內（含邊界 60 分寬限） |
| ⚠ 待複判 | 回溯期內有場區光罩或拆封班次含「彰/秀」，但確認時間對不上 |
| ✕ 查無紀錄 | 回溯期內光罩與拆封都查無場區紀錄（最可疑） |

## 每日查核（公司電腦執行）

```
C:\Users\26516\AppData\Local\Programs\Python\Python312\python.exe tools\audit_fetch.py --date 20260817
```

- 不給 `--date` 預設查昨天；`--stations` 移櫃確認查詢站所(預設 `4106,4150`＝彰化+秀水)；`--lookback` 回溯天數(預設14)；`--grace` 寬限分鐘(預設60)；`--dry-run` 不上傳
- 需要 `tools/config.local.json`（含 edge_url 與 ingest_token，不進版控）
- 跑完自動上傳 Supabase（Edge Function `caudit`，同日重跑會整日覆蓋）
