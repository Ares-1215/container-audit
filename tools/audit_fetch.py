# -*- coding: utf-8 -*-
"""移櫃確認稽核：抓 HCT 報表平台三種報表，逐櫃比對是否在彰化/秀水場區，結果上傳 Supabase。

用法：
  python audit_fetch.py --date 20260817 [--lookback 14] [--station 4106] [--dry-run]
  不給 --date 就查昨天。--dry-run 只輸出 JSON 到 stdout/檔案，不上傳。
"""
import argparse
import html as htmlmod
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path

BASE = ("http://nls.hct.com.tw:8083/old/AA005?MemberShip="
        "89219%2c%e9%99%b3%e4%bf%a1%e5%8b%9d%2c8023%2c%e9%81%8b%e6%8c%87"
        "%2c8008%2c%e9%81%8b%e5%8b%99%2c0908%2c%e5%85%ac%e5%8f%b8%2c0%2c43")
ZONE = {"4106", "4150", "4108"}      # 彰化 / 秀水 / 伸港
ZONE_KEYWORDS = ("彰", "秀", "伸")    # 拆封班次名稱關鍵字（使用者的人工判讀規則）
SSL_CTX = ssl._create_unverified_context()  # 公司網路 TLS 攔截，Supabase 需略過驗證

CONFIG = {}
cfg_path = Path(__file__).parent / "config.local.json"
if cfg_path.exists():
    CONFIG = json.loads(cfg_path.read_text(encoding="utf-8"))


def post(body_pairs, retries=3):
    body = urllib.parse.urlencode(body_pairs).encode("utf-8")
    req = urllib.request.Request(BASE, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  重試 {i+1}: {e}", file=sys.stderr)
            time.sleep(2)


def query_report(rpt_id, rpt_cond, params, page=None):
    pairs = list(params.items()) + [
        ("submitQ", "查詢"), ("SAMPLE_FILE", "null"), ("RPT_COND", rpt_cond),
        ("RPT_ID", str(rpt_id)), ("TITLE_CLASS", "運務"), ("MERGE_TITLE", ""),
        ("DYNAMIC_FIELD", "N"), ("Query", "Y"), ("DropdownNameOfEditMode", "")]
    if page:
        pairs += [("Page", str(page)),
                  ("notNeedDesMemberShip", "Y"),
                  ("MemberShip", "89219,陳信勝,8023,運指,8008,運務,0908,公司,0,43")]
    return post(pairs)


TAG_RE = re.compile(r"<[^>]+>")


def parse_rows(page_html):
    """回傳 (rows, cur_page, total_pages)；rows 含表頭。"""
    m = re.search(r"<div  id ='BlockCenterDiv'.*?</table>", page_html, re.S)
    rows = []
    if m:
        for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
            cells = [htmlmod.unescape(TAG_RE.sub("", c.group(1))).strip()
                     for c in re.finditer(r"<td[^>]*>(.*?)</td>", tr.group(1), re.S)]
            if cells:
                rows.append(cells)
    pm = re.search(r"第&nbsp;(\d+)/(\d+)&nbsp;頁", page_html)
    cur, total = (int(pm.group(1)), int(pm.group(2))) if pm else (1, 1)
    return rows, cur, total


def query_all_pages(rpt_id, rpt_cond, params, max_pages=20):
    html1 = query_report(rpt_id, rpt_cond, params)
    rows, _, total = parse_rows(html1)
    data = rows[1:] if rows else []
    header = rows[0] if rows else []
    for p in range(2, min(total, max_pages) + 1):
        rp, _, _ = parse_rows(query_report(rpt_id, rpt_cond, params, page=p))
        data += rp[1:]
    return header, data


def parse_md_hm(txt, base: date):
    """'08/18 02:55' → datetime；年份以查詢日為基準處理跨年。"""
    m = re.match(r"(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", txt or "")
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    y = base.year
    if base.month == 12 and mo == 1:
        y += 1
    elif base.month == 1 and mo == 12:
        y -= 1
    return datetime(y, mo, d, h, mi)


def fetch_confirms(qdate: str, stations):
    """RPT 139 移櫃確認查詢（可多站，合併去重）。"""
    rows, seen = [], set()
    for st in stations:
        _, rs = query_all_pages(139, "P1,P2,P3,",
                                {"P1": qdate, "P2": qdate, "P3": st})
        for c in rs:
            key = tuple(c[:9])
            if key not in seen:
                seen.add(key)
                rows.append(c)
    base = datetime.strptime(qdate, "%Y%m%d").date()
    confirms = []
    for c in rows:
        if len(c) < 9:
            continue
        containers = [x.strip() for x in c[8].split(",") if x.strip()]
        confirms.append({
            "move_date": c[0], "move_station": c[1], "truck": c[2], "driver": c[3],
            "confirm_station": c[4], "confirm_staff": c[5], "confirm_time_raw": c[6],
            "confirm_ts": (parse_md_hm(c[6], base).isoformat()
                           if parse_md_hm(c[6], base) else None),
            "box_count": int(c[7]) if c[7].isdigit() else len(containers),
            "containers": containers})
    return confirms


def fetch_gate_events(container: str, d_from: str, d_to: str):
    """RPT 51 車廂光罩明細。"""
    _, rows = query_all_pages(51, "P1,P2,P3,",
                              {"P1": container, "P2": d_from, "P3": d_to})
    events = []
    for c in rows:
        if len(c) < 11:
            continue
        try:
            ts = datetime.strptime(c[1] + " " + c[2], "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        st_code = c[3].split()[0] if c[3] else ""
        events.append({"ts": ts.isoformat(), "station": c[3], "station_code": st_code,
                       "kind": c[4], "truck": c[5], "driver": c[7], "trip": c[8],
                       "mask_type": c[10]})
    events.sort(key=lambda e: e["ts"])
    return events


def fetch_unseal(container: str, day: str):
    """RPT 79 拆封櫃明細（單日）。"""
    _, rows = query_all_pages(79, "P1,P2,P3,P4,P5,P6,P7,P8,P9,P10,", {
        "P1": day, "P2": container, "P3": "0000", "P4": "0", "P5": "0",
        "P6": "0000", "P7": "0000", "P8": "1N", "P9": "1N", "P10": "1"})
    out = []
    for c in rows:
        if len(c) < 20 or c[0] in ("作業類別",):
            continue
        rec = {"day": day, "work_type": c[0], "trip": c[1], "from": c[3], "to": c[4],
               "depart": c[17], "arrive": c[19]}
        zone_hit = (c[3][:4] in ZONE or c[4][:4] in ZONE
                    or any(k in c[1] for k in ZONE_KEYWORDS))
        rec["zone_hit"] = zone_hit
        out.append(rec)
    return out


def build_intervals(events, win_start: datetime):
    """把彰化/秀水的進離站配對成在站區間。回傳 [(start,end,station)]，end=None 表示尚未離站。"""
    ivs = []
    open_by_st = {}
    for e in events:
        if e["station_code"] not in ZONE:
            continue
        ts = datetime.fromisoformat(e["ts"])
        st = e["station_code"]
        if e["kind"] == "進站":
            open_by_st.setdefault(st, []).append(ts)
        elif e["kind"] == "離站":
            if open_by_st.get(st):
                ivs.append((open_by_st[st].pop(), ts, st))
            else:
                ivs.append((win_start, ts, st))  # 進站發生在回溯窗以前
    for st, opens in open_by_st.items():
        for ts in opens:
            ivs.append((ts, None, st))
    return ivs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(date.today() - timedelta(days=1)).strftime("%Y%m%d"))
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--stations", default="4106,4150",
                    help="移櫃確認查詢站所，逗號分隔(預設彰化+秀水)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grace", type=int, default=60,
                    help="確認時間離在站區間邊界多少分鐘內仍算綠燈(預設60)")
    ap.add_argument("--unseal-days", type=int, default=None,
                    help="紅燈候選的拆封回查天數(預設=lookback)")
    args = ap.parse_args()

    qd = datetime.strptime(args.date, "%Y%m%d").date()
    d_from = (qd - timedelta(days=args.lookback)).strftime("%Y%m%d")
    d_to = min(qd + timedelta(days=1), date.today()).strftime("%Y%m%d")
    win_start = datetime.combine(qd - timedelta(days=args.lookback), datetime.min.time())
    unseal_days = args.unseal_days if args.unseal_days is not None else args.lookback

    stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    print(f"查詢 {args.date} 站所 {'+'.join(stations)}（光罩回溯 {d_from}~{d_to}）...")
    confirms = fetch_confirms(args.date, stations)
    all_containers = sorted({c for cf in confirms for c in cf["containers"]})
    print(f"移櫃確認 {len(confirms)} 筆，不重複櫃號 {len(all_containers)} 個")

    gate_cache = {}
    for i, cn in enumerate(all_containers, 1):
        gate_cache[cn] = fetch_gate_events(cn, d_from, d_to)
        zone_n = sum(1 for e in gate_cache[cn] if e["station_code"] in ZONE)
        print(f"  [{i}/{len(all_containers)}] 光罩 {cn}: {len(gate_cache[cn])} 筆(場區 {zone_n})")

    unseal_cache = {}

    def get_unseal(cn):
        """由查詢日往回逐日查拆封，找到場區紀錄即停。"""
        if cn in unseal_cache:
            return unseal_cache[cn]
        recs = []
        for k in range(unseal_days + 1):
            day = (qd - timedelta(days=k)).strftime("%Y%m%d")
            rows = fetch_unseal(cn, day)
            recs += rows
            if any(r["zone_hit"] for r in rows):
                break
        unseal_cache[cn] = recs
        return recs

    items = []
    for ci, cf in enumerate(confirms):
        cts = datetime.fromisoformat(cf["confirm_ts"]) if cf["confirm_ts"] else None
        for cn in cf["containers"]:
            events = gate_cache.get(cn, [])
            zone_events = [e for e in events if e["station_code"] in ZONE]
            ivs = build_intervals(events, win_start)
            verdict, reason = "red", "回溯期內查無彰化/秀水光罩與拆封紀錄"
            hit_iv, lag_min = None, None
            if cts:
                for s, e, st in ivs:
                    if s <= cts and (e is None or cts <= e):
                        hit_iv, lag_min = (s, e, st), 0
                        break
                if not hit_iv:
                    # 寬限期：距任一區間邊界 grace 分鐘內視為作業時差
                    best = None
                    for s, e, st in ivs:
                        for edge in (s, e):
                            if edge is None:
                                continue
                            dm = abs((cts - edge).total_seconds()) / 60
                            if dm <= args.grace and (best is None or dm < best[1]):
                                best = ((s, e, st), dm)
                    if best:
                        hit_iv, lag_min = best[0], round(best[1])
            unseal = []
            if hit_iv:
                st_name = {"4106": "彰化", "4150": "秀水", "4108": "伸港"}.get(hit_iv[2], hit_iv[2])
                verdict = "green"
                reason = (f"確認時間落在{st_name}在站區間 "
                          f"{hit_iv[0]:%m/%d %H:%M}~"
                          + (f"{hit_iv[1]:%m/%d %H:%M}" if hit_iv[1] else "（未離站）"))
                if lag_min:
                    reason += f"（邊界誤差 {lag_min} 分內）"
            elif zone_events:
                last = zone_events[-1]
                verdict = "yellow"
                reason = (f"回溯期內有場區光罩（最近：{last['ts'][5:16].replace('T',' ')} "
                          f"{last['station']} {last['kind']}），但確認時間不在在站區間內")
            else:
                unseal = get_unseal(cn)
                hits = [r for r in unseal if r["zone_hit"]]
                if hits:
                    h = hits[0]
                    verdict = "yellow"
                    reason = f"光罩無場區紀錄，但 {h['day'][4:6]}/{h['day'][6:8]} 拆封班次「{h['trip']}」({h['from']}→{h['to']})有含彰/秀"
            items.append({
                "confirm_idx": ci, "container_no": cn, "verdict": verdict, "reason": reason,
                "evidence": {
                    "gate_events": events[-60:],
                    "intervals": [[s.isoformat(), e.isoformat() if e else None, st]
                                  for s, e, st in ivs],
                    "unseal": unseal[:30]}})
        done = sum(1 for x in items)
        print(f"  比對進度 {done} 項", end="\r")

    summary = {v: sum(1 for it in items if it["verdict"] == v)
               for v in ("green", "yellow", "red")}
    summary["confirms"] = len(confirms)
    summary["containers"] = len(all_containers)
    summary["items"] = len(items)
    print(f"\n結果：綠 {summary['green']}／黃 {summary['yellow']}／紅 {summary['red']}")

    payload = {
        "action": "upload_run", "ingest": CONFIG.get("ingest_token", ""),
        "date": qd.isoformat(), "station": "+".join(stations),
        "lookback": args.lookback, "summary": summary,
        "confirms": [dict(cf, items=[{k: it[k] for k in
                                      ("container_no", "verdict", "reason", "evidence")}
                                     for it in items if it["confirm_idx"] == i])
                     for i, cf in enumerate(confirms)]}

    out_file = Path(__file__).parent / f"run_{args.date}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已存 {out_file}")

    if args.dry_run:
        return
    url = CONFIG["edge_url"]
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        print("上傳結果:", r.read().decode("utf-8")[:300])


if __name__ == "__main__":
    main()
