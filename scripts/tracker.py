#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
减脂过程管理引擎(松松方法论)。数据存 ~/Documents/cut-buddy-data/。
分工:Claude 负责把大白话解析成 payload JSON 并用陪跑语气回复;
本脚本负责确定性计算(配额查表/营养换算/7日均·周均/平台期)与读写文件。

子命令:
  quota   --weight 60.0 [--day training|rest]      # 查当日松松配额(克数)
  log     --file payload.json | --json '<...>'      # 记录一天(体重/餐/运动/状态),自动存食物库
  today   [--date YYYY-MM-DD]                        # 输出当日总结+趋势(给 Claude 引用)
  weekly                                             # 输出周趋势表
  food    --list | --get "山姆虾饼"                   # 查个人食物库
  correct --date D --field weight_kg --value 60.0    # 更正 weight/days 字段
  delete  --table meals --date D [--index N]         # 删除某条/某日记录
  render                                             # 重新生成看板
  cardio  --rest-hr 70 --hr 130 [--weight 60] [--minutes 45] [--hours-per-week 2]  # 有氧心率消耗(Excel sheet16)
  1rm     --weight 50 --reps 10 [--sex 女]           # 最大力量预测(Excel sheet24 九公式)+训练容量参考
依赖:仅标准库。Python 3.9+。
"""
import os, sys, csv, json, argparse, datetime, statistics, collections, math

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.normpath(os.path.join(HERE, "..", "references"))
DATA_DIR = os.environ.get("CUTBUDDY_DIR", os.path.expanduser("~/Documents/cut-buddy-data"))
HEIGHTS = [150, 155, 160, 165, 170, 175, 180]

def P(name): return os.path.join(DATA_DIR, name)
def today_str(): return datetime.date.today().isoformat()
def pdate(s):
    y, m, d = map(int, s.split("-")); return datetime.date(y, m, d)

# ---------- 通用 CSV ----------
SCHEMA = {
    "weight.csv":  ["date", "weight_kg", "body_fat_pct", "lean_mass_kg", "note"],
    "days.csv":    ["date", "sleep_h", "period", "bloat", "bowel", "high_salt", "note"],
    "meals.csv":   ["date", "meal", "food", "dish", "grams", "kcal", "protein", "fat", "carb", "veg_g", "fructose_g", "source", "note"],
    "workouts.csv":["date", "type", "duration_min", "intensity", "is_strength", "is_cardio", "is_ball", "is_rest", "cardio_min", "load", "performance", "fatigue", "energy_kcal", "distance_km", "raw_type", "note"],
    "foods.csv":   ["name", "brand", "per100_kcal", "per100_protein", "per100_fat", "per100_carb", "per100_fructose", "unit_g", "source", "added"],
}
def read_csv(name):
    p = P(name)
    if not os.path.exists(p): return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))
def write_csv(name, rows):
    fields = SCHEMA[name]
    with open(P(name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
def append_csv(name, row):
    rows = read_csv(name); rows.append(row); write_csv(name, rows)
def upsert_csv(name, key, row):
    rows = read_csv(name)
    for i, r in enumerate(rows):
        if r.get(key) == row.get(key):
            r.update({k: v for k, v in row.items() if v != "" and v is not None}); write_csv(name, rows); return
    rows.append(row); write_csv(name, rows)

def _fill_profile(p):
    # 建档只问核心项,其余按公式补默认,缺字段不崩
    p.setdefault("sex", "女"); p.setdefault("trainer_type", "力训者")
    p.setdefault("phase", "减脂"); p.setdefault("fat_g_per_kg", 1.0 if p["phase"] == "增肌" else 0.8); p.setdefault("fructose_max_g", 50)
    ms = p.get("milestones") or []
    final = ms[-1] if ms else (p.get("stage_goal_kg") or p.get("start_weight_kg") or 60)
    p.setdefault("stage_goal_kg", ms[0] if ms else final)
    p.setdefault("target_low", round(final - 1.2, 1)); p.setdefault("target_high", round(final + 1.5, 1))
    if "fat_budget" not in p:
        w = p.get("start_weight_kg") or p.get("stage_goal_kg") or 60
        t = round(p["fat_g_per_kg"] * w)
        p["fat_budget"] = {"normal": [round(t*0.8), round(t*1.15)], "training": [t, round(t*1.25)], "min": round(t*0.7)}
    return p
def load_profile():
    with open(P("profile.json"), encoding="utf-8") as f: return _fill_profile(json.load(f))

# ---------- 配额查表 ----------
def load_quota_table():
    with open(os.path.join(REF_DIR, "松松配额表_女.json"), encoding="utf-8") as f: return json.load(f)
def _nearest(val, opts): return min(opts, key=lambda x: abs(x - val))
# 松松经验化配额(g/kg)。训练日基准;休息日碳水整体 -0.5。target=区间中点。
QUOTA_CFG = {
    ("减脂", "女"): {"carb": (2.0, 3.0), "protein": (1.2, 1.5)},
    ("减脂", "男"): {"carb": (2.0, 3.0), "protein": (1.5, 2.0)},
    ("增肌", "女"): {"carb": (3.0, 3.5), "protein": (1.5, 2.0)},
    ("增肌", "男"): {"carb": (3.5, 4.5), "protein": (1.5, 2.0)},
    ("维持", "女"): {"carb": (2.5, 3.0), "protein": (1.2, 1.5)},
    ("维持", "男"): {"carb": (2.5, 3.5), "protein": (1.5, 2.0)},
}
# D13 大体重起始配额下调(仅减脂期):BMI 分档压低碳水起始区间(松松:大体重低配额——体重变大主要是脂肪,
# 单位体重基代/活动消耗都更低)。档位下限=上限-0.3 保留递减空间;此档休息日碳水改 -0.3(女版配额表大体重行
# 训练/休息差≈0.3,沿用 -0.5 会跌穿表值)。起始偏保守是松松本意("算出来偏低是故意的"),两周趋势反馈再校正。
BMI_CARB_CAP = [(32, {"男": 2.0, "女": 1.7}), (28, {"男": 2.5, "女": 2.1})]
def quota_calc(weight, sex, phase, day, height_cm=None):
    cfg = QUOTA_CFG.get((phase, sex)) or QUOTA_CFG[("减脂", "女")]
    clo, chi = cfg["carb"]
    bmi_tier = None
    if phase == "减脂" and height_cm:
        bmi = weight / (height_cm / 100.0) ** 2
        for th, caps in BMI_CARB_CAP:
            if bmi > th:
                cap = caps.get(sex, caps["女"])
                if cap < chi:
                    chi = cap; clo = min(clo, round(chi - 0.3, 1)); bmi_tier = "BMI>%d" % th
                break
    rest_step = 0.3 if bmi_tier else 0.5
    if day != "training": clo, chi = clo - rest_step, chi - rest_step  # 休息日碳水下调
    plo, phi = cfg["protein"]
    return {"carb_low": round(clo*weight), "carb_high": round(chi*weight), "carb_g": round((clo+chi)/2*weight),
            "protein_low": round(plo*weight), "protein_high": round(phi*weight), "protein_g": round((plo+phi)/2*weight),
            "phase": phase, "sex": sex, "bmi_tier": bmi_tier}
def quota(weight, profile, day):
    """按 性别×阶段×训练日(×BMI 档)返回碳蛋脂配额区间(下限/目标/上限)。"""
    if not weight: return None
    return quota_calc(weight, profile.get("sex", "女"), profile.get("phase", "减脂"), day,
                      profile.get("height_cm"))
def fat_target(weight, profile):
    return round(profile.get("fat_g_per_kg", 0.8) * weight)

# ---------- 营养换算 ----------
def kj_to_kcal(kj): return kj / 4.184
def resolve_item(item, foodlib):
    """item -> (grams, kcal, protein, fat, carb, fructose, source, save_food_or_None)"""
    name = item.get("food", "").strip()
    brand = item.get("brand", "")
    per100 = item.get("per100")
    save = None
    if per100:  # 优先营养表
        kcal100 = per100.get("kcal")
        if kcal100 is None and "kj" in per100: kcal100 = kj_to_kcal(per100["kj"])
        pr100, ft100, cb100 = per100.get("protein", 0), per100.get("fat", 0), per100.get("carb", 0)
        # 只给了碳蛋脂、没给 kcal/kj 时,按 碳×4+蛋×4+脂×9 反算(否则会把热量记成 0)
        if kcal100 is None: kcal100 = cb100 * 4 + pr100 * 4 + ft100 * 9
        p100 = {"kcal": kcal100, "protein": pr100, "fat": ft100, "carb": cb100,
                "fructose": per100.get("fructose", 0)}
        source = item.get("source", "营养表")
        if name:  # 存入食物库供下次复用
            save = {"name": name, "brand": brand,
                    "per100_kcal": round(p100["kcal"], 1), "per100_protein": p100["protein"],
                    "per100_fat": p100["fat"], "per100_carb": p100["carb"],
                    "per100_fructose": p100["fructose"], "unit_g": item.get("unit_g", ""),
                    "source": source, "added": today_str()}
    else:  # 查食物库
        hit = foodlib.get(name.lower())
        if hit:
            p100 = {"kcal": float(hit["per100_kcal"] or 0), "protein": float(hit["per100_protein"] or 0),
                    "fat": float(hit["per100_fat"] or 0), "carb": float(hit["per100_carb"] or 0),
                    "fructose": float(hit["per100_fructose"] or 0)}
            if not item.get("unit_g") and hit.get("unit_g"):
                item["unit_g"] = float(hit["unit_g"])
            source = "食物库"
        else:
            return None  # 无法计算,交给 Claude 估算或提示补
    # 克数
    grams = item.get("grams")
    if grams is None:
        cnt = item.get("count", 1); ug = item.get("unit_g")
        grams = cnt * float(ug) if ug else None
    if grams is None: return None
    f = grams / 100.0
    return (round(grams, 1), round(p100["kcal"] * f, 1), round(p100["protein"] * f, 1),
            round(p100["fat"] * f, 1), round(p100["carb"] * f, 1), round(p100["fructose"] * f, 1),
            source, save)

# ---------- 趋势计算 ----------
def weight_series():
    rows = [r for r in read_csv("weight.csv") if r.get("weight_kg")]
    out = []
    for r in rows:
        try: out.append((pdate(r["date"]), float(r["weight_kg"]),
                         float(r["body_fat_pct"]) if r.get("body_fat_pct") else None,
                         float(r["lean_mass_kg"]) if r.get("lean_mass_kg") else None))
        except ValueError: pass
    out.sort(); return out
def ma7(series, d):
    vals = [w for (dd, w, _, _) in series if 0 <= (d - dd).days < 7]
    return round(statistics.mean(vals), 2) if vals else None
def weekly(series):
    wk = collections.defaultdict(list)
    for (d, w, _, _) in series: wk[d.isocalendar()[:2]].append(w)
    out = []
    for k in sorted(wk):
        vs = wk[k]
        out.append({"week": "%d-W%02d" % k, "iso": k, "avg": round(statistics.mean(vs), 2),
                    "lo": round(min(vs), 2), "hi": round(max(vs), 2), "n": len(vs)})
    return out
def rate_kg_week(series, days=28):
    if not series: return None
    last = series[-1][0]
    pts = [((d - datetime.date(1970,1,1)).days, w) for (d, w, _, _) in series if 0 <= (last - d).days < days]
    if len(pts) < 3: return None
    n = len(pts); mx = sum(p[0] for p in pts)/n; my = sum(p[1] for p in pts)/n
    den = sum((p[0]-mx)**2 for p in pts)
    if den == 0: return None
    return round(sum((p[0]-mx)*(p[1]-my) for p in pts)/den * 7, 2)

# ---------- 命令:log ----------
def cmd_log(payload):
    date = payload.get("date") or today_str()
    foodlib = {r["name"].lower(): r for r in read_csv("foods.csv")}
    msg = []
    if payload.get("weight_kg") is not None:
        upsert_csv("weight.csv", "date", {"date": date, "weight_kg": payload["weight_kg"],
                   "body_fat_pct": payload.get("body_fat_pct", ""),
                   "lean_mass_kg": payload.get("lean_mass_kg", ""), "note": payload.get("weight_note", "")})
        msg.append("体重 %.2fkg" % float(payload["weight_kg"]))
    if payload.get("day"):
        d = payload["day"]
        upsert_csv("days.csv", "date", {"date": date, "sleep_h": d.get("sleep_h", ""),
                   "period": 1 if d.get("period") else "", "bloat": 1 if d.get("bloat") else "",
                   "bowel": 1 if d.get("bowel") else "", "high_salt": 1 if d.get("high_salt") else "",
                   "note": d.get("note", "")})
    saved_foods = []
    for it in payload.get("meals", []):
        r = resolve_item(it, foodlib)
        if r is None:
            msg.append("⚠️未能计算[%s](缺营养表/不在食物库)" % it.get("food", "?")); continue
        grams, kcal, pro, fat, carb, fru, source, save = r
        append_csv("meals.csv", {"date": date, "meal": it.get("meal", ""), "food": it.get("food", ""),
                   "dish": it.get("dish", ""),  # 显示用菜名(混合菜原料共用一个 dish,看板折叠成一行)
                   "grams": grams, "kcal": kcal, "protein": pro, "fat": fat, "carb": carb,
                   "veg_g": grams if it.get("veg") else "", "fructose_g": fru,
                   "source": source, "note": it.get("note", "")})
        if save and save["name"].lower() not in foodlib:
            append_csv("foods.csv", save); foodlib[save["name"].lower()] = save
            saved_foods.append(save["name"])
    for w in payload.get("workouts", []):
        append_csv("workouts.csv", {"date": date, "type": w.get("type", ""), "duration_min": w.get("duration_min", ""),
                   "intensity": w.get("intensity", ""), "is_strength": 1 if w.get("is_strength") else 0,
                   "is_cardio": 1 if w.get("is_cardio") else 0, "is_ball": 1 if w.get("is_ball") else 0,
                   "is_rest": 1 if w.get("is_rest") else 0, "cardio_min": w.get("cardio_min", ""),
                   "load": w.get("load", ""), "performance": w.get("performance", ""),
                   "fatigue": w.get("fatigue", ""), "raw_type": "", "note": w.get("note", "")})
    out = {"date": date, "logged": msg, "saved_foods": saved_foods}
    return out

# ---------- 命令:today ----------
def cmd_today(date):
    profile = load_profile()
    series = weight_series()
    # 当日饮食合计
    meals = [r for r in read_csv("meals.csv") if r["date"] == date]
    tot = {k: round(sum(float(m[k] or 0) for m in meals), 1) for k in ["kcal", "protein", "fat", "carb", "fructose_g", "veg_g"]}
    # 当日是否训练日
    wk = [r for r in read_csv("workouts.csv") if r["date"] == date]
    is_training = any(r.get("is_strength") == "1" for r in wk)
    day = "training" if is_training else "rest"
    # 当前体重(取当日或最近)
    wt = None
    for (d, w, _, _) in series:
        if d.isoformat() == date: wt = w
    if wt is None and series: wt = series[-1][1]
    q = quota(wt, profile, day) if wt else None
    fatT = fat_target(wt, profile) if wt else None
    # 趋势
    d0 = pdate(date)
    m7 = ma7(series, d0) if series else None
    wks = weekly(series)
    this_avg = last_avg = None
    if wks:
        ic = d0.isocalendar()[:2]
        for w in wks:
            if w["iso"] == ic: this_avg = w["avg"]
        idx = [i for i, w in enumerate(wks) if w["iso"] == ic]
        if idx and idx[0] > 0: last_avg = wks[idx[0]-1]["avg"]
    rate = rate_kg_week(series)
    # 平台期信号(近3周周均无降)
    plateau = None
    if len(wks) >= 3:
        recent = [w["avg"] for w in wks[-3:]]
        plateau = recent[-1] >= recent[0] - 0.2
    out = {
        "date": date, "is_training_day": is_training,
        "weight_kg": wt, "ma7": m7,
        "this_week_avg": this_avg, "last_week_avg": last_avg,
        "delta_vs_last_week": round(this_avg - last_avg, 2) if (this_avg and last_avg) else None,
        "rate_kg_per_week": rate,
        "intake": tot,
        "quota": q, "fat_target_g": fatT,
        "fat_budget": profile["fat_budget"], "fructose_max_g": profile.get("fructose_max_g", 50),
        "vs_quota": ({"carb": round(tot["carb"] - q["carb_g"], 1),
                      "protein": round(tot["protein"] - q["protein_g"], 1),
                      "fat": round(tot["fat"] - fatT, 1)} if q else None),
        "plateau_2_3wk": plateau,
        "target_band": [profile["target_low"], profile["target_high"]],
        "dist_to_target_high": round(m7 - profile["target_high"], 1) if m7 else None,
    }
    return out

def cmd_weekly():
    return {"weekly": weekly(weight_series())}

def cmd_food(args):
    rows = read_csv("foods.csv")
    if args.list: return {"foods": [r["name"] for r in rows]}
    if args.get:
        hit = [r for r in rows if args.get.lower() in r["name"].lower()]
        return {"match": hit}
    return {"count": len(rows)}

def cmd_correct(args):
    if args.field in SCHEMA["weight.csv"]:
        upsert_csv("weight.csv", "date", {"date": args.date, args.field: args.value}); name = "weight.csv"
    else:
        upsert_csv("days.csv", "date", {"date": args.date, args.field: args.value}); name = "days.csv"
    return {"corrected": {name: {args.date: {args.field: args.value}}}}

def cmd_delete(args):
    rows = read_csv(args.table + ".csv")
    if args.index is not None:
        same = [i for i, r in enumerate(rows) if r["date"] == args.date]
        if 0 <= args.index < len(same): rows.pop(same[args.index])
    else:
        rows = [r for r in rows if r["date"] != args.date]
    write_csv(args.table + ".csv", rows)
    return {"deleted": {args.table: args.date, "index": args.index}}

# ---------- 命令:cardio(有氧心率消耗,松松 Excel sheet16 心率法) ----------
# 每kg体重每小时活动热量 = 活动心率÷静息心率×6.4 − 6.2;总消耗 = ×体重×大体重衰减系数。
# 衰减系数誊自 Excel 原表列公式:≤85kg×1.0、90×0.98、95×0.96、100×0.94、105×0.92、110+×0.90
# (原表 115kg 列 0.92 破坏单调、疑笔误,按 0.90 处理)。原表验证范围:静息 60–85、运动 120–170。
def cardio_calc(rest_hr, hr, weight):
    per_kg = hr / rest_hr * 6.4 - 6.2
    decay = 1.0
    for th, d in [(110, 0.90), (105, 0.92), (100, 0.94), (95, 0.96), (90, 0.98)]:
        if weight >= th: decay = d; break
    return per_kg, decay, per_kg * weight * decay

def cmd_cardio(args):
    weight = args.weight
    if weight is None:
        series = weight_series()
        if series: weight = series[-1][1]
    if weight is None:
        return {"error": "缺体重:传 --weight 或先记一条体重"}
    per_kg, decay, kcal_h = cardio_calc(args.rest_hr, args.hr, weight)
    if per_kg <= 0:
        return {"error": "运动心率太接近静息心率,该公式不适用(几乎没有额外消耗)"}
    out = {"rest_hr": args.rest_hr, "hr": args.hr, "weight_kg": weight,
           "kcal_per_kg_per_h": round(per_kg, 2), "big_weight_decay": decay,
           "kcal_per_hour": round(kcal_h)}
    if args.minutes:
        out["minutes"] = args.minutes
        out["kcal_session"] = round(kcal_h * args.minutes / 60)
    if args.hours_per_week:
        extra = kcal_h * args.hours_per_week / 7
        out["hours_per_week"] = args.hours_per_week
        out["daily_extra_eat_kcal"] = round(extra)  # 给原本无有氧的方案加饮食:每周消耗÷7
        out["food_equiv_per_100kcal"] = "80g熟米饭 / 80g瘦熟肉 / 1个苹果(香蕉/柑橘) / 1.5个鸡蛋 / 大半盒全脂牛奶 / 20g坚果"
    notes = []
    if not (60 <= args.rest_hr <= 85): notes.append("静息心率超出原表验证范围(60–85),结果仅供参考")
    if not (120 <= args.hr <= 170): notes.append("运动心率超出原表验证范围(120–170),结果仅供参考")
    notes.append("减脂目的有氧建议心率≈120(脂肪氧化峰值);每周<4小时、放在力量之后")
    out["notes"] = notes
    return out

# ---------- 命令:1rm(最大力量预测,松松 Excel sheet24 九公式) ----------
# 来源论文:Accuracy of 1RM Prediction Equations Before and After Resistance Training in Three Different Lifts。
# 适用:自由卧推/深蹲,输入做组配重 + 该配重的力竭次数(动作全程接近完全标准)。
RM_FORMULAS = {
    "Adams":    lambda w, r: w / (1 - 0.02 * r),
    "Brown":    lambda w, r: (r * 0.0328 + 0.9849) * w,
    "Brzycki":  lambda w, r: w / (1.0278 - 0.0278 * r),
    "Lander":   lambda w, r: w / (1.013 - 0.0267123 * r),
    "Lombardi": lambda w, r: r ** 0.1 * w,
    "Mayhew":   lambda w, r: w / (0.522 + 0.419 * math.exp(-0.055 * r)),
    "O'Connor": lambda w, r: 0.025 * w * r + w,
    "Wathen":   lambda w, r: w / (0.488 + 0.538 * math.exp(-0.075 * r)),
    "Welday":   lambda w, r: 0.0333 * r * w + w,
}
RM_FEMALE = ["Brown", "Brzycki", "Lander"]   # Excel 标注:对女性较准确
RM_MALE = ["Lombardi"]                       # Excel 标注:对男性较准确

def cmd_1rm(args):
    w, r = args.weight, args.reps
    if r < 1 or w <= 0: return {"error": "配重需>0、力竭次数需≥1"}
    sex = args.sex
    if sex is None:
        try: sex = load_profile().get("sex")
        except Exception: pass
    est = {}
    for name, f in RM_FORMULAS.items():
        try:
            v = f(w, r)
            est[name] = round(v, 1) if v > 0 else None
        except (ZeroDivisionError, OverflowError):
            est[name] = None
    vals = [v for v in est.values() if v]
    out = {"weight": w, "reps": r, "estimates": est,
           "median_1rm": round(statistics.median(vals), 1) if vals else None}
    if sex == "女":
        sv = [est[n] for n in RM_FEMALE if est.get(n)]
        if sv: out["recommended_1rm"] = {"sex": "女", "formulas": RM_FEMALE, "value": round(statistics.mean(sv), 1)}
    elif sex == "男":
        sv = [est[n] for n in RM_MALE if est.get(n)]
        if sv: out["recommended_1rm"] = {"sex": "男", "formulas": RM_MALE, "value": round(statistics.mean(sv), 1)}
    notes = ["适用自由卧推/深蹲;输入'做组配重+该配重力竭次数',动作全程接近完全标准"]
    if r > 12: notes.append("力竭次数>12 时各公式误差明显变大,建议用 6–12 次的组来估")
    out["notes"] = notes
    out["volume_ref"] = {  # 松松训练容量参数(框架§8/三分化表)
        "周频率": "每周 3–5 次合适,6 次极限;每周至少留 1–2 天完全休息",
        "分化": "新手三分化(3 天 1 循环),有底子四分化",
        "组数": "大肌群≈9 组、小肌群≈6 组;单次≤20 组、约 1–1.5 小时(三分化单次约 21 组)",
        "强度": "以 6–12RM 为主;五大项(卧推/划船/硬拉/深蹲/推举)偶尔 3–5RM 增力组",
    }
    return out

def cmd_render():
    import subprocess
    bd = os.path.join(HERE, "build_dashboard.py")
    if not os.path.exists(bd): bd = os.path.normpath(os.path.join(HERE, "..", "build_dashboard.py"))
    subprocess.run([sys.executable, bd], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"rendered": P("dashboard.html")}

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    q = sub.add_parser("quota"); q.add_argument("--weight", type=float, required=True)
    q.add_argument("--day", choices=["training", "rest"], default="training")
    l = sub.add_parser("log"); l.add_argument("--file"); l.add_argument("--json")
    t = sub.add_parser("today"); t.add_argument("--date", default=today_str())
    sub.add_parser("weekly")
    fo = sub.add_parser("food"); fo.add_argument("--list", action="store_true"); fo.add_argument("--get")
    c = sub.add_parser("correct"); c.add_argument("--date", required=True); c.add_argument("--field", required=True); c.add_argument("--value", required=True)
    d = sub.add_parser("delete"); d.add_argument("--table", required=True); d.add_argument("--date", required=True); d.add_argument("--index", type=int)
    sub.add_parser("render")
    ca = sub.add_parser("cardio"); ca.add_argument("--rest-hr", type=float, required=True)
    ca.add_argument("--hr", type=float, required=True); ca.add_argument("--weight", type=float)
    ca.add_argument("--minutes", type=float); ca.add_argument("--hours-per-week", type=float)
    rm = sub.add_parser("1rm"); rm.add_argument("--weight", type=float, required=True)
    rm.add_argument("--reps", type=int, required=True); rm.add_argument("--sex", choices=["女", "男"])
    a = ap.parse_args()

    if a.cmd == "quota":
        pr = load_profile()
        out = quota(a.weight, pr, a.day)
        out["fat_g"] = fat_target(a.weight, pr)
    elif a.cmd == "log":
        payload = json.loads(a.json) if a.json else json.load(open(a.file, encoding="utf-8"))
        out = cmd_log(payload)
        out["today"] = cmd_today(payload.get("date") or today_str())
        cmd_render()
    elif a.cmd == "today": out = cmd_today(a.date)
    elif a.cmd == "weekly": out = cmd_weekly()
    elif a.cmd == "food": out = cmd_food(a)
    elif a.cmd == "correct": out = cmd_correct(a); cmd_render()
    elif a.cmd == "delete": out = cmd_delete(a); cmd_render()
    elif a.cmd == "render": out = cmd_render()
    elif a.cmd == "cardio": out = cmd_cardio(a)
    elif a.cmd == "1rm": out = cmd_1rm(a)
    else: ap.print_help(); return
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
