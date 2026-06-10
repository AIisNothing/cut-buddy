#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析苹果「健康」导出数据(export.xml 或 导出.zip),提取体重/体脂/瘦体重,清洗后并入 weight.csv。
用法: python3 import_apple_health.py <export.xml | 导出.zip>
数据写入 ~/Documents/cut-buddy-data/weight.csv(已有日期只补空字段,不覆盖)。"""
import os, sys, csv, zipfile, statistics
import xml.etree.ElementTree as ET

DATA_DIR = os.environ.get("CUTBUDDY_DIR", os.path.expanduser("~/Documents/cut-buddy-data"))
WPATH = os.path.join(DATA_DIR, "weight.csv")

def open_export(path):
    if path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = next((n for n in z.namelist() if n.endswith("export.xml")), None)
        if not name: raise SystemExit("压缩包里没找到 export.xml(确认是苹果健康导出的 zip)")
        return z.open(name)
    return open(path, "rb")

def to_kg(v, unit):
    return v * 0.45359237 if (unit or "").lower() in ("lb", "lbs", "pound") else v

def parse(path):
    wt, bf, lean = {}, {}, {}
    f = open_export(path)
    for _ev, el in ET.iterparse(f, events=("end",)):
        if el.tag != "Record":
            el.clear(); continue
        t = el.get("type", ""); d = (el.get("startDate") or "")[:10]
        try: val = float(el.get("value"))
        except (TypeError, ValueError): el.clear(); continue
        if d:
            if t == "HKQuantityTypeIdentifierBodyMass":
                kg = to_kg(val, el.get("unit"))
                if 25 <= kg <= 300: wt.setdefault(d, []).append(round(kg, 2))
            elif t == "HKQuantityTypeIdentifierBodyFatPercentage":
                pct = val * 100 if val <= 1 else val
                if 3 <= pct <= 70: bf.setdefault(d, []).append(round(pct, 1))
            elif t == "HKQuantityTypeIdentifierLeanBodyMass":
                kg = to_kg(val, el.get("unit"))
                if 20 <= kg <= 200: lean.setdefault(d, []).append(round(kg, 2))
        el.clear()
    return wt, bf, lean

def main():
    if len(sys.argv) < 2: raise SystemExit("用法: import_apple_health.py <export.xml|导出.zip>")
    wt, bf, lean = parse(sys.argv[1])
    if not wt: raise SystemExit("没解析到体重数据(确认是苹果「健康」导出文件)")
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = {}
    if os.path.exists(WPATH):
        for r in csv.DictReader(open(WPATH, encoding="utf-8")):
            if r.get("date"): rows[r["date"]] = dict(r)
    added = 0
    for d in sorted(wt):
        w = round(statistics.median(wt[d]), 2)
        b = round(statistics.median(bf[d]), 1) if bf.get(d) else ""
        l = round(statistics.median(lean[d]), 2) if lean.get(d) else (round(w * (1 - b / 100), 2) if b != "" else "")
        if d in rows:  # 已有日期:只补空字段,不覆盖用户已记的
            r = rows[d]
            if not r.get("body_fat_pct") and b != "": r["body_fat_pct"] = b
            if not r.get("lean_mass_kg") and l != "": r["lean_mass_kg"] = l
        else:
            rows[d] = {"date": d, "weight_kg": w, "body_fat_pct": b, "lean_mass_kg": l, "note": "苹果健康导入"}
            added += 1
    cols = ["date", "weight_kg", "body_fat_pct", "lean_mass_kg", "note"]
    with open(WPATH, "w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for d in sorted(rows): wr.writerow({k: rows[d].get(k, "") for k in cols})
    ds = sorted(wt)
    print("✓ 导入完成:新增 %d 天,体重区间 %s → %s,共解析到 %d 天体重、%d 天体脂。" % (
        added, ds[0], ds[-1], len(wt), len(bf)))

if __name__ == "__main__":
    main()
