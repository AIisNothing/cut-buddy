#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快乐减脂趋势看板 v4(克制简约 · Apple Health 风)。
~/Documents/cut-buddy-data/{weight,meals,workouts,days}.csv + profile.json → dashboard.html。
设计:单一克制绿、统一字号刻度、tabular-nums 数字对齐、8px 间距、去衬线去暖纸。
内容:饮食按松松配额判断三大营养素是否达标、给调整建议。
模块:今日状态 → 体重趋势 → 饮食 → 活动日历 → 身体成分 → 阶段回顾。
"""
import os, csv, json, html, datetime, statistics, collections, calendar as _cal, re, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.normpath(os.path.join(HERE, "..", "references"))
if not os.path.isdir(REF_DIR): REF_DIR = os.path.join(HERE, "references")
DATA_DIR = os.environ.get("CUTBUDDY_DIR", os.path.expanduser("~/Documents/cut-buddy-data"))
EPOCH = datetime.date(1970, 1, 1)
HEIGHTS = [150, 155, 160, 165, 170, 175, 180]

def P(n): return os.path.join(DATA_DIR, n)
def ms(d): return (d - EPOCH).days * 86400000
def pdate(s):
    y, m, dd = map(int, s.split("-")); return datetime.date(y, m, dd)
def read_csv(n):
    p = P(n)
    if not os.path.exists(p): return []
    with open(p, encoding="utf-8") as f: return list(csv.DictReader(f))
def f1(x):
    if x is None: return "—"
    try:
        v = float(x); return str(int(v)) if v == int(v) else ("%.1f" % v)
    except (TypeError, ValueError): return "—"
def esc(s): return html.escape(str(s))

def _fill_profile(p):
    # 建档只问核心项(称呼/性别/身高/当前体重/目标里程碑/阶段),其余按公式补默认,缺字段不崩
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
def _persist_celebrated(profile, n):
    # 记录"已庆祝到第几个里程碑"到 profile.json，避免重复弹彩蛋
    try:
        p = P("profile.json"); raw = json.load(open(p, encoding="utf-8")); raw["celebrated_done"] = n
        with open(p, "w", encoding="utf-8") as f: json.dump(raw, f, ensure_ascii=False, indent=2)
        profile["celebrated_done"] = n
    except Exception: pass
def load_quota():
    p = os.path.join(REF_DIR, "松松配额表_女.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
# 松松经验化配额(g/kg)。训练日基准;休息日碳水整体 -0.5。target=区间中点。
QUOTA_CFG = {
    ("减脂", "女"): {"carb": (2.0, 3.0), "protein": (1.2, 1.5)},
    ("减脂", "男"): {"carb": (2.0, 3.0), "protein": (1.5, 2.0)},
    ("增肌", "女"): {"carb": (3.0, 3.5), "protein": (1.5, 2.0)},
    ("增肌", "男"): {"carb": (3.5, 4.5), "protein": (1.5, 2.0)},
    ("维持", "女"): {"carb": (2.5, 3.0), "protein": (1.2, 1.5)},
    ("维持", "男"): {"carb": (2.5, 3.5), "protein": (1.5, 2.0)},
}
def quota_for(weight, profile, day):
    if not weight: return None
    sex, phase = profile.get("sex", "女"), profile.get("phase", "减脂")
    cfg = QUOTA_CFG.get((phase, sex)) or QUOTA_CFG[("减脂", "女")]
    clo, chi = cfg["carb"]
    if day != "training": clo, chi = clo - 0.5, chi - 0.5
    plo, phi = cfg["protein"]
    return {"carb_g": round((clo+chi)/2*weight), "protein_g": round((plo+phi)/2*weight),
            "carb_low": round(clo*weight), "carb_high": round(chi*weight),
            "protein_low": round(plo*weight), "protein_high": round(phi*weight)}

def weight_series():
    out = []
    for r in read_csv("weight.csv"):
        if not r.get("weight_kg"): continue
        try:
            out.append((pdate(r["date"]), float(r["weight_kg"]),
                        float(r["body_fat_pct"]) if r.get("body_fat_pct") else None,
                        float(r["lean_mass_kg"]) if r.get("lean_mass_kg") else None))
        except ValueError: pass
    out.sort(); return out
def ma7_at(series, d):
    vs = [w for (dd, w, _, _) in series if 0 <= (d - dd).days < 7]
    return round(statistics.mean(vs), 2) if vs else None
def weekly_avgs(series):
    wk = collections.defaultdict(list)
    for (d, w, _, _) in series: wk[d.isocalendar()[:2]].append(w)
    return [{"iso": k, "avg": round(statistics.mean(v), 2), "lo": round(min(v), 2)} for k, v in sorted(wk.items())]
def reg_rate(series, days=28):
    if not series: return None
    L = series[-1][0]
    pts = [((d-EPOCH).days, w) for (d, w, _, _) in series if 0 <= (L-d).days < days]
    if len(pts) < 3: return None
    n = len(pts); mx = sum(p[0] for p in pts)/n; my = sum(p[1] for p in pts)/n
    den = sum((p[0]-mx)**2 for p in pts)
    return round(sum((p[0]-mx)*(p[1]-my) for p in pts)/den*7, 2) if den else None

def day_nutrition(rows):
    g = lambda k: round(sum(float(m[k] or 0) for m in rows), 1)
    return {"kcal": g("kcal"), "protein": g("protein"), "fat": g("fat"),
            "carb": g("carb"), "fructose": g("fructose_g"), "veg": g("veg_g")}
def meals_by_date():
    by = collections.defaultdict(list)
    for r in read_csv("meals.csv"): by[r["date"]].append(r)
    return by
def workouts_by_date():
    by = collections.defaultdict(list)
    for r in read_csv("workouts.csv"): by[r["date"]].append(r)
    return by
def days_by_date():
    return {r["date"]: r for r in read_csv("days.csv")}

LABEL = {"力量": "力量", "羽毛球": "羽毛球", "爬坡": "爬坡", "跳舞": "跳舞", "散步": "散步",
         "跑步": "跑步", "骑行": "骑行", "椭圆机": "椭圆", "游泳": "游泳", "核心": "核心",
         "HIIT": "HIIT", "混合有氧": "有氧", "瑜伽": "瑜伽", "休息": "休息", "铁三": "铁三"}
def day_activities(wos):
    labs = []
    for w in wos:
        if w.get("is_rest") == "1": labs.append("休息"); continue
        labs.append(LABEL.get(w.get("type", ""), w.get("type", "其他")))
        if w.get("cardio_min") and str(w.get("cardio_min")).strip() not in ("", "0"): labs.append("爬坡")
    seen = []; [seen.append(x) for x in labs if x not in seen]
    return seen
MEAL_EMOJI = {"早餐": "🥣", "早饭": "🥣", "午餐": "🍚", "午饭": "🍚", "晚餐": "🍲", "晚饭": "🍲",
              "练前": "🍌", "训练前": "🍌", "练后": "🥤", "训练后": "🥤", "加餐": "🍪", "夜宵": "🌙"}
def meal_emoji(name):
    for k, v in MEAL_EMOJI.items():
        if k in name: return v
    return "🍽️"

def build_all():
    profile = load_profile()
    series = weight_series()
    mbd = meals_by_date(); wbd = workouts_by_date(); dbd = days_by_date()
    if not series: return {"empty": True}
    L = series[-1][0]; latest_w = series[-1][1]
    daily = {d: w for (d, w, _, _) in series}
    ma_now = ma7_at(series, L)
    ma_prev = ma7_at(series, L - datetime.timedelta(days=7))
    delta7 = round(ma_now - ma_prev, 2) if (ma_now and ma_prev) else None
    prev_w = None
    for off in range(1, 8):
        if (L - datetime.timedelta(days=off)) in daily:
            prev_w = daily[L - datetime.timedelta(days=off)]; break
    day_change = round(latest_w - prev_w, 2) if prev_w is not None else None
    wks = weekly_avgs(series); rate = reg_rate(series)
    plateau = len(wks) >= 3 and wks[-1]["avg"] >= wks[-3]["avg"] - 0.2

    phase = profile.get("phase", "减脂")
    gain = (phase == "增肌")
    n_days = len(series)
    single_up = day_change is not None and day_change > 0
    if n_days < 7:
        status, trend_label = "数据不足", "数据不足"
        one = "数据还不到 7 天，先记着，趋势会清晰起来。"
    elif gain:
        # 增肌:涨=好
        if rate is None: status, trend_label, one = "观察", "数据不足", "再记几天，趋势会清晰。"
        elif rate >= 0.3: status, trend_label, one = "增", "稳步增长", "体重稳稳往上走，增肌节奏对了。"
        elif rate >= 0.1: status, trend_label, one = "增", "缓慢增长", "在往上走，吃够练到位，保持。"
        elif rate > -0.1: status, trend_label, one = "观察", "基本持平", "7 日均没怎么动，吃够点、再观察一周。"
        else: status, trend_label, one = "降", "不升反降", "体重在掉，热量多半不够，适当多吃点碳水。"
    else:
        # 减脂 / 维持:降=好
        if plateau and (rate is None or rate > -0.1): status, trend_label = "平台", "可能平台"
        elif rate is not None and rate <= -0.5: status, trend_label = "降", "明显下降"
        elif rate is not None and rate <= -0.1: status, trend_label = "降", "缓慢下降"
        elif rate is not None and rate < 0.1: status, trend_label = "观察", "基本持平"
        else: status, trend_label = "升", "略升观察"
        if status == "降": one = "单日涨没关系，7 日均还在往下。" if single_up else "趋势还在往下，今天不用调整。"
        elif status == "观察": one = "7 日均基本持平，再观察一周，先不下结论。"
        elif status == "平台": one = "7 日均两三周没动，可能平台，可轻微调一调。"
        else: one = "7 日均略升，先别慌，看看最近饮食和睡眠。"

    # 速度判断(按阶段)
    if gain:
        if rate is None or rate <= 0.02:
            speed = "最近基本没涨——增肌要有热量盈余，可小幅加点碳水，再看一两周。"
        else:
            pct = rate / (ma_now or latest_w) * 100
            speed = "最近增重速度约 %.2f kg/周（每周约 %.1f%%）。" % (rate, pct)
            if pct >= 1.0: speed += "偏快了，涨太猛容易堆脂肪，建议小幅减点碳水放慢。"
            elif pct >= 0.25: speed += "落在合理区间（每周约 0.25–0.5%），长肌肉少长脂，保持就好。"
            else: speed += "偏慢，想快点可每天多吃约 150–300 kcal 碳水，再看一两周。"
    elif rate is None or rate >= -0.02:
        speed = "最近 7 日均基本没怎么动，先观察一两周，别急着调。"
    else:
        d2 = abs(rate) * 2 / (ma_now or latest_w) * 100
        speed = "最近减重速度约 %.2f kg/周（每两周约降 %.1f%%）。" % (abs(rate), d2)
        if d2 >= 3:
            speed += "按松松的框架这偏快了——两周降超 3% 缺口太大、容易掉肌肉，建议小幅加点碳水，别太猛。"
        elif d2 >= 1.5:
            speed += "正落在松松说的理想区间（两周降约 2%），不快不慢、护着肌肉，保持就好。"
        elif d2 >= 0.6:
            speed += "比松松 2% 的理想线稍慢，但胜在稳、可持续、不掉肌肉。想快一点可小幅再减点碳水或加点有氧；不调也完全合理。"
        else:
            speed += "偏慢、接近持平。想推进的话，松松的做法是每天少吃约 150 kcal 或每周加约 1000 kcal 有氧，再看一两周。"

    y = L - datetime.timedelta(days=1)
    y_acts = day_activities(wbd.get(y.isoformat(), []))
    df = dbd.get(L.isoformat(), {})
    high_salt = df.get("high_salt") == "1"; period = df.get("period") == "1"
    if day_change is None:
        chg_line, cause_line = "还没有可比较的前一次体重", "记一次就能解释波动"
    else:
        big = abs(day_change) >= 0.8
        chg_line = "较前次 %s%.1f kg，%s" % ("+" if day_change > 0 else "", day_change, "波动偏大" if big else "正常波动")
        if big: cause_line = "单日波动本来就大，以 7 日均为准"
        elif day_change > 0:
            if any(a in ("力量", "羽毛球", "爬坡", "跳舞", "跑步") for a in y_acts): cause_line = "昨天有训练，可能训练后储水"
            elif high_salt: cause_line = "可能高盐 / 外食后水分滞留"
            elif period: cause_line = "经期前后周期性水分波动，不是长胖"
            else: cause_line = "日常水分波动"
        else:
            cause_line = "昨天偏休息，水分释放较多" if ("休息" in y_acts or not y_acts) else "日常水分波动"
    if status in ("降", "观察"): adj_line = "不调整，继续看 7 日均重"
    elif status == "平台": adj_line = "可轻微调，别激进砍碳水"
    elif status == "升": adj_line = "先观察趋势，不急补救"
    else: adj_line = "数据积累中，先不调整"
    if rate is not None and rate < -0.8: adj_line = "降得偏快，补够蛋白、睡好"

    coach_w = "松松看体重只看 1–2 周趋势、不看单日。"
    if status == "降": coach_w += "你 7 日均稳稳向下、没到平台，按现在的节奏走就好。"
    elif status == "观察": coach_w += "近期基本持平，再观察一周，先别急着调。"
    elif status == "平台": coach_w += "连续两三周没动，可考虑碳水降 0.3–0.5g/kg（蛋白脂肪不动），或加点有氧。"
    elif status == "升": coach_w += "略升先别慌，多半是水分或执行，看两周再判断。"
    else: coach_w += "数据还不到一周，先记着，趋势会清晰起来。"

    mstone = build_milestones(profile, latest_w, ma_now)
    # 里程碑彩蛋:仅在"新跨过"里程碑时弹一次(首次建档不为既有进度庆祝)
    celebrate = None
    if "celebrated_done" not in profile:
        _persist_celebrated(profile, mstone["done"])
    elif mstone["done"] > profile["celebrated_done"]:
        reached = [s["goal"] for s in mstone["steps"] if s["reached"]]
        if reached:
            celebrate = {"goal": reached[-1], "name": mstone["name"], "phase": mstone["phase"], "final": mstone["success"]}
        _persist_celebrated(profile, mstone["done"])
    diet = build_diet(profile, mbd, wbd, L, latest_w)
    cal = build_calendar(L, wbd)
    body = build_body(series)
    supp = build_supp(profile, series, mbd, wbd, L, rate, diet)
    recap = build_recap(series, mbd, wbd, L, rate)

    charts = {
        "weightDaily": [{"x": ms(d), "y": w} for (d, w, _, _) in series],
        "ma7": [{"x": ms(d), "y": ma7_at(series, d)} for (d, w, _, _) in series],
        "target": mstone["next_goal"],
        "minx": ms(series[0][0]), "maxx": ms(L), "defaultLo": max(ms(series[0][0]), ms(L - datetime.timedelta(days=120))),
        "kcalDaily": diet["kcalDaily"], "kcalAvg": diet["kcalAvg"],
        "proteinDaily": diet["proteinDaily"], "fatDaily": diet["fatDaily"], "showDietChart": diet["show_trend"],
        "bf": body["bf_series"], "fat": body["fat_series"], "lean": body["lean_series"], "showBody": body["has"]}
    return {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "today": {"weight": latest_w, "ma7": ma_now, "trend_label": trend_label, "one": one, "speed": speed},
            "trend": {"chg": chg_line, "cause": cause_line, "adj": adj_line, "coach": coach_w},
            "ms": mstone, "diet": diet, "calendar": cal, "body": body,
            "supp": supp, "recap": recap, "charts": charts, "celebrate": celebrate}

def build_diet(profile, mbd, wbd, L, latest_w):
    todays = mbd.get(L.isoformat(), [])
    is_train = any(r.get("is_strength") == "1" for r in wbd.get(L.isoformat(), []))
    q = quota_for(latest_w, profile, "training" if is_train else "rest")
    fatT = round(profile.get("fat_g_per_kg", 0.8) * latest_w)
    fat_hi = profile["fat_budget"]["training"][1] if is_train else profile["fat_budget"]["normal"][1]
    tot = day_nutrition(todays)

    bymeal = collections.OrderedDict()
    for m in todays: bymeal.setdefault(m["meal"], []).append(m)
    def is_condiment(nm):
        if nm in ("食用油", "蚝油", "酱油", "生抽", "老抽", "盐", "糖", "沙拉酱", "蛋黄酱", "番茄酱"): return True
        return len(nm) <= 3 and (nm.endswith("油") or nm.endswith("酱") or nm.endswith("盐"))
    photos_dir = os.path.join(DATA_DIR, "photos")
    def find_photo(mn):
        for ext in ("jpg", "jpeg", "png", "webp", "heic"):
            fn = "%s-%s.%s" % (L.isoformat(), mn, ext)
            if os.path.exists(os.path.join(photos_dir, fn)): return "photos/" + fn
        return ""
    def mealcard(mn, items):
        sub = day_nutrition(items)
        shown = [i["food"] for i in items if not is_condiment(i["food"])] or [i["food"] for i in items]
        return {"emoji": meal_emoji(mn), "meal": mn, "foods_list": shown, "empty": False,
                "kcal": round(sub["kcal"]), "protein": f1(sub["protein"]), "fat": f1(sub["fat"]), "carb": f1(sub["carb"]),
                "pre": ("练前" in mn or "练后" in mn), "photo": find_photo(mn)}
    MAIN = ["早餐", "午餐", "晚餐"]; SEQ = ["早餐", "练前", "午餐", "练后", "晚餐", "加餐", "夜宵"]
    cards = []
    for mn in SEQ:
        if mn in bymeal: cards.append(mealcard(mn, bymeal[mn]))
        elif mn in MAIN: cards.append({"emoji": meal_emoji(mn), "meal": mn, "empty": True})
    for mn in bymeal:
        if mn not in SEQ: cards.append(mealcard(mn, bymeal[mn]))

    # 截至目前进度（不提前下全天判定）+ 这一餐判断 + 下一餐建议
    so_far = []; meal_judge = next_meal = day_kind = ""; judge_label = "这一餐"
    if todays and q:
        day_kind = "训练日" if is_train else "休息日"
        def tag_macro(act, low, high, soft_high=False):  # 碳水/蛋白:区间判断(下限-上限)
            if act < low: return ("还差 %dg到下限" % round(low - act), "mut")
            if act <= high: return ("区间内 ✓", "ok")
            # 碳水超上限=硬警告(填平缺口);蛋白超上限=软(松松:蛋白可上探保肌、不易长胖)
            return ("已充足", "ok") if soft_high else ("超上限 %dg" % round(act - high), "warn")
        def tag_fat(act):                              # 脂肪:目标 + 上限
            if act <= fatT: return ("很稳", "ok")
            if act <= fat_hi: return ("适中", "ok")
            return ("偏多·后面少油", "warn")
        ct = tag_macro(tot["carb"], q["carb_low"], q["carb_high"])
        pt = tag_macro(tot["protein"], q["protein_low"], q["protein_high"], soft_high=True)
        ftg = tag_fat(tot["fat"])
        so_far = [
            {"name": "碳水", "act": round(tot["carb"]), "rng": "%d–%d" % (q["carb_low"], q["carb_high"]), "tag": ct[0], "cls": ct[1]},
            {"name": "蛋白", "act": round(tot["protein"]), "rng": "%d–%d" % (q["protein_low"], q["protein_high"]), "tag": pt[0], "cls": pt[1]},
            {"name": "脂肪", "act": round(tot["fat"]), "rng": "≤%d" % round(fat_hi), "tag": ftg[0], "cls": ftg[1]},
        ]
        if len(bymeal) <= 1:
            mn = list(bymeal.keys())[0] if bymeal else "这餐"
            p, ft, c = tot["protein"], tot["fat"], tot["carb"]
            ps = "蛋白很够" if p >= 20 else ("蛋白适中" if p >= 10 else "蛋白偏少")
            fs = "脂肪很清淡" if ft <= 12 else ("脂肪适中" if ft <= 22 else "脂肪偏多")
            cs = "碳水偏少" if c < 40 else ("碳水适中" if c <= 90 else "碳水偏多")
            tail = ("作为%s完全没问题。" % mn) if (ft <= 24 and p >= 12) else "整体可以。"
            meal_judge = "%s，%s，%s——%s" % (ps, fs, cs, tail); judge_label = "这一餐"
        else:
            cj = ("碳水还没到下限" if tot["carb"] < q["carb_low"]
                  else ("碳水已达标" if tot["carb"] <= q["carb_high"] else "碳水超上限了"))
            pj = ("蛋白还差一点到下限" if tot["protein"] < q["protein_low"]
                  else ("蛋白已达标" if tot["protein"] <= q["protein_high"] else "蛋白很充足"))
            fj = "脂肪还在预算内" if tot["fat"] <= fat_hi else "脂肪偏多了、后面少油"
            meal_judge = "，".join([cj, pj, fj]) + "。" + (" 水果今天够了，后面不再叠。" if tot["fructose"] >= 30 else "")
            judge_label = "目前整体"
        # 下一餐:按"距区间缺口"给具体、可执行的话(补什么、少什么、大概多少)
        def carb_portion(g):
            if g < 18: return "半碗米饭/半块馍"
            if g <= 45: return "约 1 碗米饭 或 1 块帝王馍"
            return "约 %.0f 碗米饭" % round(g / 38.0)
        c_low = q["carb_low"] - tot["carb"]; c_tgt = q["carb_g"] - tot["carb"]; c_high = q["carb_high"] - tot["carb"]
        p_low = q["protein_low"] - tot["protein"]; p_tgt = q["protein_g"] - tot["protein"]
        f_room = fat_hi - tot["fat"]
        nb = []
        if c_high <= 0: nb.append("主食别再加了(碳水已到上限)")
        elif c_tgt <= 0: nb.append("主食少量就行(碳水已达标,顶多再 %dg 到上限)" % round(c_high))
        elif c_low > 0: nb.append("主食要吃够:还差 %dg 碳水到下限(%s)" % (round(c_low), carb_portion(c_low)))
        else: nb.append("主食适量:再约 %dg 碳水到目标(%s)" % (round(c_tgt), carb_portion(c_tgt)))
        if p_low > 0: nb.append("重点补蛋白:差 %dg 到下限,一份瘦肉/鸡胸/虾(约一掌 ~70g)" % round(p_low))
        elif p_tgt > 0: nb.append("蛋白再补约 %dg 到目标(小半掌瘦肉)" % round(p_tgt))
        else: nb.append("蛋白已够,正常一份即可、不用堆")
        if f_room <= 6: nb.append("脂肪只剩 %dg,这顿少油、别高脂肉/坚果" % round(max(f_room, 0)))
        elif f_room <= 14: nb.append("脂肪还有 %dg 空间,正常油盐、别上高脂肉" % round(f_room))
        else: nb.append("脂肪空间充足,正常做饭就行")
        nb.append("再配一份蔬菜")
        next_meal = "；".join(nb) + "。"

    if todays and q:
        foods_today = [m["food"] for m in todays]
        bits = []
        bits.append("蛋白已经很足（%dg）、保肌没问题" % round(tot["protein"]) if tot["protein"] >= q["protein_g"] * 0.6
                    else "蛋白还能再补点（目前 %dg）" % round(tot["protein"]))
        bits.append("脂肪很干净（%dg）" % round(tot["fat"]) if tot["fat"] <= fatT * 0.7
                    else ("脂肪还在预算内" if tot["fat"] <= fat_hi else "脂肪偏多了、后面少油"))
        if tot["carb"] < q["carb_g"] * 0.6:
            bits.append("碳水才 %dg、%s还有大空间" % (round(tot["carb"]), day_kind))
        coach_d = "看你今天：" + "，".join(bits) + "。"
        smart = list(dict.fromkeys(f for f in foods_today if any(k in f for k in ("魔芋", "鸡胸", "鸡蛋", "黄瓜", "生菜", "蔬菜", "西兰花", "瘦肉"))))
        if smart:
            coach_d += "像 %s 这种高蛋白低脂、又顶饱的，正是松松推荐的好食材。" % "、".join(smart[:3])
        if is_train:
            coach_d += "今天是训练日，把碳水主力放在练后那一餐（全天最大一份、练后 30 分钟内）。"
        # 日内碳水分配(松松第5节):非练后/练前的单顿碳水占全天≥40% → 提醒别堆
        if q["carb_g"]:
            over = [(mn, round(day_nutrition(its)["carb"]), round(day_nutrition(its)["carb"] / q["carb_g"] * 100))
                    for mn, its in bymeal.items()
                    if "练后" not in mn and "练前" not in mn and day_nutrition(its)["carb"] / q["carb_g"] >= 0.40]
            if over:
                mn, mc, pct = max(over, key=lambda x: x[1])
                coach_d += (("不过「%s」一顿就占了全天碳水的 %d%%——松松说最大碳水该留给练后那餐（40–50%%），其它餐别堆，下次把主食往练后挪。" % (mn, pct))
                            if is_train else
                            ("提醒：今天没练、没有练后窗口，「%s」却占了全天碳水 %d%%——休息日碳水要摊匀到各餐（每顿别超约 1/3）、先吃菜再吃主食，别集中在一顿。" % (mn, pct)))
    else:
        coach_d = "记几餐后，这里会按松松框架，对你当天实际的吃法做点评。"

    meal_days = len(mbd)
    last7 = sum(1 for i in range(7) if mbd.get((L-datetime.timedelta(days=i)).isoformat()))
    show_trend = last7 >= 3
    if show_trend:
        nut = [day_nutrition(mbd[(L-datetime.timedelta(days=i)).isoformat()]) for i in range(7) if mbd.get((L-datetime.timedelta(days=i)).isoformat())]
        avg = lambda k: round(statistics.mean([v[k] for v in nut]))
        week_text = "平均热量约 %d kcal，蛋白 %d g，脂肪 %d g；高脂 %d 天，有蔬果 %d 天。" % (
            avg("kcal"), avg("protein"), avg("fat"), sum(1 for v in nut if v["fat"] >= 55), sum(1 for v in nut if v["veg"] > 0))
    else:
        week_text = "数据积累中。记满 3–7 天，这里会出现平均热量、蛋白是否稳定、脂肪是否常接近上限。"
    show_pattern = meal_days >= 14
    if show_pattern:
        fat_by = collections.defaultdict(float); cut = L - datetime.timedelta(days=30)
        for ds, rows in mbd.items():
            if pdate(ds) >= cut:
                for m in rows: fat_by[m["food"]] += float(m["fat"] or 0)
        top = [k for k, v in sorted(fat_by.items(), key=lambda x: -x[1])[:5] if v > 0]
        pattern_text = "最近主要脂肪来源：" + "、".join(top) + "。同一天别叠太多就好。"
    else:
        pattern_text = "数据积累中。记满 2–3 周，这里会总结最常见高脂来源、更容易掉秤的晚餐模式。"

    kcalDaily = []; proteinDaily = []; fatDaily = []
    for i in range(29, -1, -1):
        d = L - datetime.timedelta(days=i); rows = mbd.get(d.isoformat(), [])
        if rows:
            v = day_nutrition(rows)
            kcalDaily.append({"x": ms(d), "y": v["kcal"]}); proteinDaily.append({"x": ms(d), "y": v["protein"]}); fatDaily.append({"x": ms(d), "y": v["fat"]})
    return {"cards": cards, "so_far": so_far, "day_kind": day_kind, "meal_judge": meal_judge,
            "judge_label": judge_label, "next_meal": next_meal, "kcal": round(tot["kcal"]), "coach": coach_d,
            "has_today": bool(todays), "has_quota": bool(q),
            "week_text": week_text, "show_trend": show_trend, "pattern_text": pattern_text, "show_pattern": show_pattern,
            "kcalDaily": kcalDaily, "kcalAvg": round(statistics.mean([p["y"] for p in kcalDaily])) if kcalDaily else None,
            "proteinDaily": proteinDaily, "fatDaily": fatDaily}

def build_calendar(L, wbd):
    y, m = L.year, L.month
    first_wd, ndays = _cal.monthrange(y, m)
    cells = [None] * first_wd
    for d in range(1, ndays + 1):
        ds = datetime.date(y, m, d).isoformat()
        cells.append({"day": d, "acts": day_activities(wbd.get(ds, [])), "today": (datetime.date(y, m, d) == L)})
    while len(cells) % 7: cells.append(None)
    weeks = [cells[i:i+7] for i in range(0, len(cells), 7)]
    t_acts = day_activities(wbd.get(L.isoformat(), []))
    note = next((w["note"] for w in wbd.get(L.isoformat(), []) if w.get("note")), "")
    today_line = ("今天已完成 · " + " + ".join(t_acts) + ("　" + note if note else "")) if t_acts \
        else "今天还没记活动（休息、或忘开手表都行）"
    iso = L.isocalendar()[:2]; tdays = set(); strg = 0
    for ds, wos in wbd.items():
        if pdate(ds).isocalendar()[:2] == iso:
            for w in wos:
                if w.get("is_rest") == "1": continue
                tdays.add(ds)
                if w.get("is_strength") == "1": strg += 1
    n = len(tdays)
    if n == 0:
        expl = "本周还没记到运动，动了随手记一句就好。"
    else:
        expl = "本周训练 %d 天" % n + ("（力量 %d 次）" % strg if strg else "") + "。"
        if n >= 6: expl += "逼近松松说的上限（6 次极限、7 次自残），记得留 1–2 天完全休息，中枢神经也要恢复。"
        elif n >= 3: expl += "落在松松说的合适区间（每周 3–5 次）。"
        else: expl += "量不大，松松看来完全 OK。"
        expl += "你体重不到 70kg，松松建议每周约 2 小时有氧，羽毛球 + 力量后爬坡基本够；减脂期力量配重别降，配重守住=肌肉守住。"
    return {"year": y, "month": m, "weeks": weeks, "today_line": today_line, "expl": expl}

def build_body(series):
    lean = [(d, l) for (d, w, b, l) in series if l is not None]
    fat = [(d, round(w - l, 2)) for (d, w, b, l) in series if l is not None]
    bf = [(d, b) for (d, w, b, l) in series if b is not None]
    def trend(pts, days=28):
        if len(pts) < 4: return None
        L = pts[-1][0]
        rec = [v for (d, v) in pts if 0 <= (L-d).days < days]; old = [v for (d, v) in pts if days <= (L-d).days < days*2]
        if len(rec) < 2 or len(old) < 2: return None
        return round(statistics.mean(rec) - statistics.mean(old), 2)
    ft = trend(fat); lt = trend(lean)
    if ft is None:
        summary = "数据还不够，积累 2–4 周再看。体脂秤只看长期趋势，不看单日。"
    else:
        fdir = "缓慢下降" if ft < -0.2 else ("基本稳定" if ft <= 0.2 else "略升")
        ldir = "基本稳定" if (lt is None or abs(lt) <= 0.5) else ("略降" if lt < 0 else "略升")
        summary = "近 4 周脂肪量%s，肌肉量%s。" % (fdir, ldir)
        if ft < -0.2 and (lt is None or lt >= -0.5): summary += "掉的主要是脂肪，肌肉守住了。"
        elif lt is not None and lt < -0.5: summary += "肌肉略降，补够蛋白、别再压低热量。"
    coach_b = "松松说家用体脂秤只看 2–4 周趋势、不看单日。"
    if ft is None:
        coach_b += "数据攒够 2–4 周再看减重质量。"
    elif ft < -0.2 and (lt is None or lt >= -0.5):
        coach_b += "你近 4 周掉的主要是脂肪、肌肉守住，这就是减脂质量好的样子。"
    elif lt is not None and lt < -0.5:
        coach_b += "肌肉有点往下，补够蛋白、别再压低热量，优先保肌。"
    else:
        coach_b += "看 2–4 周的方向就好，别被单日体脂率带着焦虑。"
    # 图表只画近 4 周(28 天)——与"近 4 周"话术一致,也合松松"体脂秤只看 2-4 周趋势"
    _lasts = [p[-1][0] for p in (bf, fat, lean) if p]
    _cut = (max(_lasts) - datetime.timedelta(days=28)) if _lasts else None
    def _w(pts): return [(d, v) for d, v in pts if (_cut is None or d >= _cut)]
    return {"summary": summary, "coach": coach_b, "bf_series": [{"x": ms(d), "y": v} for d, v in _w(bf)],
            "fat_series": [{"x": ms(d), "y": v} for d, v in _w(fat)],
            "lean_series": [{"x": ms(d), "y": v} for d, v in _w(lean)], "has": ft is not None}

def build_milestones(profile, latest_w, ma_now):
    name = profile.get("name", "")
    phase = profile.get("phase", "减脂")
    mss = profile.get("milestones") or [60, 58, 55]
    cur = ma_now if ma_now is not None else latest_w
    start = profile.get("start_weight_kg", cur or 65)
    final = mss[-1]
    span = (final - start) or 1                       # 有符号:减脂为负、增肌为正
    dirn = 1 if final >= start else -1                # 朝目标的方向
    def pos(w):
        return round(max(0.0, min(100.0, (w - start) / span * 100)), 1)
    def reached_(m):                                  # 是否已"越过"该里程碑(朝目标方向)
        return cur is not None and (cur - m) * dirn >= 0
    success = reached_(final)
    done = sum(1 for m in mss if reached_(m))
    steps = []; next_set = False
    for m in mss:
        reached = reached_(m)
        now = (not reached) and (not next_set)
        if now: next_set = True
        steps.append({"goal": m, "reached": reached, "now": now,
                      "dist": round(cur - m, 1) if cur is not None else None, "pos": pos(m)})
    next_goal = next((s["goal"] for s in steps if s["now"]), final)
    verb = {"增肌": "增肌", "维持": "维持"}.get(phase, "减肥")
    question = "%s今天%s成功了没？" % (name, verb) if phase != "维持" else "%s今天维持得怎么样？" % name
    answer = ("成功了 🎉" if success else "还没有～") if phase != "维持" else "稳着呢～"
    return {"name": name, "phase": phase, "question": question, "answer": answer,
            "cur": round(cur, 1) if cur is not None else None, "start": start,
            "cur_pos": pos(cur) if cur is not None else 0,
            "done": done, "total": len(mss), "steps": steps,
            "next_goal": next_goal, "success": success}

def build_stage(profile, latest_w, ma_now, rate, ms):
    cur = ma_now if ma_now is not None else latest_w
    start = profile.get("start_weight_kg", 65); dropped = round(start - cur, 1)
    if ms["success"]:
        return {"summary": "三个阶段都完成啦，当前 %s kg，已降 %s kg。" % (f1(cur), f1(dropped)),
                "eta": "可以进入维持期，或设新的目标。"}
    nxt = ms["next_goal"]; remaining = round(cur - nxt, 1)
    summary = "起点以来已降 %s kg，当前 %s kg，正在向第 %d 个目标 %s kg 前进。" % (
        f1(dropped), f1(cur), ms["done"] + 1, f1(nxt))
    if rate is None or rate >= 0:
        eta = "最近暂不足以预测时间，不预测、不焦虑，继续记录就好。"
    else:
        mo_lo = max(1, round(remaining/(abs(rate)*1.4)/4.3)); mo_hi = round(remaining/(abs(rate)*0.7)/4.3)
        eta = "按最近 ~%.2f kg/周，到 %s kg 大约还要 %d–%d 个月。参考区间，不是 deadline。" % (abs(rate), f1(nxt), mo_lo, mo_hi)
    return {"summary": summary, "eta": eta}

TIPS = [
    ("看两周，别看单日", "单日体重涨跌大多是水和食糜——一斤脂肪要多吃约 4.5kg 米饭才长得出来。判断有没有瘦，看 7 日均和腰围，别被单天吓到。"),
    ("混合菜要拆开算", "「土豆烧牛肉」别按一整盘记，拆成 瘦肉 / 肥肉 / 主食型菜 / 直接吃的油 四部分。App 里的混合菜数据基本都是错的。"),
    ("脂肪不用算克数", "早饭有蛋黄牛奶 + 正餐带点油的瘦肉菜，同时不碰高脂肉和糖油混合物，脂肪就自然合适，不必逐项称。"),
    ("水果≈糖水", "一个大苹果的糖差不多≈半餐主食。水果比甜品好，但别当无限量健康零食，果糖一天别超 30–50g。"),
    ("减脂期力量别降配重", "维持你大项的配重，缺口靠饮食做出来。降配重去冲心率反而容易掉肌肉——配重守住，就是肌肉守住。"),
    ("留够休息日", "每周练 3–5 次最合适，6 次是极限，7 次是自残。至少留 1–2 天完全休息，中枢神经也要恢复。"),
    ("有氧按体重来", "70kg 以下容易饿，每周约 2 小时有氧帮你多吃点；80kg 以上靠吃就够，有氧能省则省。心率 120–150 即可。"),
    ("练后那餐放最大碳水", "训练后 30 分钟内，把全天最大一份碳水 + 30–50g 蛋白放这吃，利用胰岛素窗口多长肌肉、少长脂肪。"),
    ("什么才算瘦肉", "脂肪率<5% 才算：去皮鸡鸭、无白色脂肪层的猪牛羊、鱼虾贝、内脏。裹粉油炸的、肉丸肉馅都不算瘦肉。"),
    ("别极端节食", "吃太少会伤基础代谢，缺口反而消失，越饿越不掉。温和缺口 10–20% 才可持续，也更护肌肉。"),
    ("高脂肉黑名单", "鸡鸭皮、大排、糖醋里脊、锅包肉、排骨、肥牛肥羊、午餐肉肉丸肉肠——减脂期当偶尔解馋可以，别当日常。"),
    ("体脂秤只看长期", "家用体脂秤单日误差能盖过几周的真实变化。体脂率、肌肉量只看 2–4 周趋势，别天天盯着焦虑。"),
    ("局部减脂是伪命题", "嫌腿粗多半是体脂高、不是肌肉。马甲线靠降体脂不靠多练腹；女生练力量也不会变壮，放心练。"),
    ("不掉秤先查执行", "两周不掉先别怀疑方案：是不是没定量？水果没扣主食？偷吃了高脂肉/糖油？有氧没做？都排除了才考虑缺口不够。"),
]

def build_tip(L):
    t = TIPS[L.toordinal() % len(TIPS)]
    return {"title": t[0], "body": t[1]}

def build_recap(series, mbd, wbd, L, rate):
    iso = L.isocalendar()[:2]
    wks = weekly_avgs(series)
    this = wks[-1]["avg"] if wks else None
    last = wks[-2]["avg"] if len(wks) >= 2 else None
    parts = []
    if this is not None:
        if last is not None:
            d = round(this - last, 2)
            parts.append("本周周均 %s kg（%s%.2f）" % (f1(this), "↘ " if d < 0 else "↗ +", abs(d) if d < 0 else d))
        else:
            parts.append("本周周均 %s kg" % f1(this))
    tdays = set(ds for ds, wos in wbd.items()
                if pdate(ds).isocalendar()[:2] == iso and any(w.get("is_rest") != "1" for w in wos))
    parts.append("训练 %d 天" % len(tdays))
    # 日均只统计"完整的过去天"——今天没过完，不计入,以免拉低平均
    md = [day_nutrition(mbd[ds]) for ds in mbd
          if pdate(ds).isocalendar()[:2] == iso and ds != L.isoformat()]
    if len(md) >= 1:
        parts.append("完整记录的 %d 天日均约 %d kcal、蛋白 %d g" % (
            len(md), round(statistics.mean([m["kcal"] for m in md])), round(statistics.mean([m["protein"] for m in md]))))
    txt = "，".join(parts) + "。"
    if rate is not None and rate <= -0.1:
        txt += " 趋势在稳稳往下，按现在的节奏走就好。"
    elif rate is not None and rate > 0.1:
        txt += " 本周略升，看看是不是水分或执行的问题，先别慌。"
    else:
        txt += " 继续记录，攒够两周再下判断。"
    return txt

def build_praise(profile, series, mbd, wbd, L, rate, diet):
    pr = []
    if diet.get("has_today"):
        tp = round(sum(float(m["protein"] or 0) for m in mbd.get(L.isoformat(), [])))
        if tp >= 35:
            pr.append("今天吃得很会——一顿就把蛋白质堆到 %dg，是全天目标的一大半，保肌满分。" % tp)
    if rate is not None and -0.7 <= rate <= -0.08:
        pr.append("最近 7 日均稳稳往下，约 %.2f kg/周——不快不慢、不掉肌肉，这个减脂节奏你拿捏得特别好。" % abs(rate))
    cut = L - datetime.timedelta(days=14)
    tdays = set(ds for ds, wos in wbd.items() if pdate(ds) >= cut and any(w.get("is_rest") != "1" for w in wos))
    sdays = set(ds for ds, wos in wbd.items() if pdate(ds) >= cut and any(w.get("is_strength") == "1" for w in wos))
    if len(tdays) >= 4:
        extra = "（力量 %d 天）" % len(sdays) if sdays else ""
        pr.append("最近运动很勤——近两周练了 %d 天%s，减脂期还坚持举铁保肌肉，很专业。" % (len(tdays), extra))
    sdates = {x[0] for x in series}
    if sum(1 for i in range(7) if (L - datetime.timedelta(days=i)) in sdates) >= 4:
        pr.append("已经坚持记录好几天了——肯记录、肯面对数字，这件事本身就赢了一半。")
    foods = [m["food"] for m in mbd.get(L.isoformat(), [])]
    if any(("魔芋" in f or "鸡胸" in f or "鸡蛋" in f) for f in foods):
        pr.append("食物也选得聪明——魔芋、鸡胸、鸡蛋这种高蛋白低脂的，又顶饱又不增负担。")
    if not pr:
        pr.append("愿意为自己花心思、肯坚持，这份认真本身就很值得夸。")
    return {"line": pr[L.toordinal() % len(pr)]}

ANCHOR = datetime.date(2026, 6, 9)
def build_supp(profile, series, mbd, wbd, L, rate, diet):
    if (L - ANCHOR).days % 2 == 0:
        p = build_praise(profile, series, mbd, wbd, L, rate, diet)
        return {"kind": "praise", "line": p["line"]}
    t = build_tip(L)
    return {"kind": "tip", "title": "松松小课堂 · 每日一条", "tip_t": t["title"], "tip_b": t["body"]}

# ---------- 渲染 ----------
def cal_html(cal):
    head = "".join("<th>%s</th>" % w for w in ["一", "二", "三", "四", "五", "六", "日"])
    rows = ""
    for wk in cal["weeks"]:
        tds = ""
        for c in wk:
            if c is None: tds += "<td class='e'></td>"; continue
            chips = "".join("<span class='chip c-%s'>%s</span>" % (_chip(a), esc(a)) for a in c["acts"])
            tds += "<td class='%s'><span class='dn'>%d</span>%s</td>" % ("td" if c["today"] else "", c["day"], chips)
        rows += "<tr>%s</tr>" % tds
    return "<table class='cal'><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, rows)
def _chip(a):
    return {"力量": "a", "核心": "a", "羽毛球": "b", "爬坡": "b", "跑步": "b", "散步": "b", "跳舞": "b",
            "骑行": "b", "椭圆": "b", "游泳": "b", "有氧": "b", "休息": "r"}.get(a, "o")

def milestone_html(ms):
    names = ["第一目标", "第二目标", "第三目标", "第四目标", "第五目标"]
    nodes = ("<div class='node start' style='left:0%%'><div class='flag'>%d<i>kg</i></div>"
             "<div class='pole'></div><div class='nl'>起点</div></div>") % ms["start"]
    n = len(ms["steps"])
    for i, s in enumerate(ms["steps"]):
        cls = "done" if s["reached"] else ("now" if s["now"] else "")
        if i == n - 1: cls = (cls + " end").strip()   # 末节点贴右对齐，防溢出
        lab = "已达成" if s["reached"] else (names[i] if i < len(names) else "目标")
        nodes += ("<div class='node %s' style='left:%s%%'><div class='flag'>%d<i>kg</i></div>"
                  "<div class='pole'></div><div class='nl'>%s</div></div>") % (cls, s["pos"], s["goal"], lab)
    return ("<div class='msbar'><div class='track2'><div class='fill2' style='width:%s%%'></div>"
            "<div class='here' style='left:%s%%'><b>当前 %s</b></div>%s</div></div>") % (
            ms["cur_pos"], ms["cur_pos"], f1(ms["cur"]), nodes)

def diet_html(di):  # 左侧:今日吃饭小日记(左图标 · 右食物列表)
    if not di["has_today"]:
        return "<div class='note'>今天还没记饮食。随手发一餐，这里就会出现你的今日吃饭小日记 🍱</div>"
    out = "<div class='diary'>"
    for c in di["cards"]:
        if c.get("empty"):
            out += ("<div class='meal empty'><div class='mvis'><div class='mtile'>%s</div></div>"
                    "<div class='mbody'><div class='mn'>%s</div><div class='mf-empty'>暂未记录</div></div></div>") % (
                    c["emoji"], esc(c["meal"]))
            continue
        vis = ("<img class='mthumb' src='%s' alt='' loading='lazy'>" % c["photo"]) if c.get("photo") \
            else ("<div class='mtile'>%s</div>" % c["emoji"])
        foods = "<span class='sep'>｜</span>".join("<span class='mfi'>%s</span>" % esc(f) for f in c["foods_list"])
        nut = "%d kcal · 碳水 %s · 蛋白 %s · 脂肪 %s" % (c["kcal"], c["carb"], c["protein"], c["fat"])
        out += ("<div class='meal'><div class='mvis'>%s</div><div class='mbody'>"
                "<div class='mn'>%s</div><div class='mlist'>%s</div><div class='mnut'>%s</div></div></div>") % (
                vis, esc(c["meal"]), foods, nut)
    return out + "</div>"

def diet_right_html(di):  # 截至目前(横向条) + 这一餐 + 下一餐
    if not di["has_today"] or not di["so_far"]:
        return "<div class='note'>记一餐后，这里会显示截至目前的营养进度、这一餐判断和下一餐建议。</div>"
    items = "<div class='sf-item'><span class='sk'>热量</span><b>%d</b><i>kcal</i></div>" % di["kcal"]
    for m in di["so_far"]:
        items += ("<div class='sf-item'><span class='sk'>%s</span><b>%d</b><i>/ %s g</i>"
                  "<em class='%s'>%s</em></div>") % (m["name"], m["act"], esc(m["rng"]), m["cls"], esc(m["tag"]))
    return ("<div class='sf-h'>截至目前 · %s配额区间</div><div class='sf-strip'>%s</div>"
            "<div class='judge2'><span class='jt'>%s</span>%s</div>"
            "<div class='advice2'><span class='jt'>下一餐</span>%s</div>") % (
            di["day_kind"], items, esc(di["judge_label"]), esc(di["meal_judge"]), esc(di["next_meal"]))

def supp_html(s):
    if s["kind"] == "praise":
        return ("<div class='pz-deco'>🌟</div><div class='pz-h'>今日份夸夸</div>"
                "<div class='pz-big'>%s</div>") % esc(s["line"])
    return "<div class='mt'>%s</div><div class='tip-t'>%s</div><div class='tip-b'>%s</div>" % (
        esc(s["title"]), esc(s["tip_t"]), esc(s["tip_b"]))

# ===== 通用组件(统一零件,保证全站一致) =====
def c_card(inner, title=None, cls="span2"):   # 统一卡片:可带标题
    h = ("<div class='mt'>%s</div>" % esc(title)) if title else ""
    return "<div class='card %s'>%s%s</div>" % (cls.strip(), h, inner)
def c_coach(text):                            # 松松点评盒
    return "<div class='coach'><span class='coach-tag'>松松点评</span>%s</div>" % esc(text)
def c_sub(title):                             # 小节小标题
    return "<div class='sub'>%s</div>" % esc(title)
def c_stat(label, value, unit="", cls=""):    # 大数字块
    u = ("<span class='u'>%s</span>" % esc(unit)) if unit else ""
    return "<div class='stat %s'><div class='l'>%s</div><div class='v'>%s%s</div></div>" % (cls, esc(label), esc(value), u)
def c_tag(text):                              # 趋势药丸
    return "<span class='tag'>%s</span>" % esc(text)


def render(D):
    if D.get("empty"):
        return "<html><body style='font-family:-apple-system,sans-serif;padding:40px'>还没有体重数据，先记录几天。</body></html>"
    t, tr, di, ca, bo, mst = D["today"], D["trend"], D["diet"], D["calendar"], D["body"], D["ms"]
    supp, recap = D["supp"], D["recap"]

    d_left, d_right = diet_html(di), diet_right_html(di)
    d_coach = di["coach"]; tr_cause = tr["cause"]; recap_txt = recap
    foot = "数据本地存储 · ~/Documents/cut-buddy-data"
    show_trend, show_pattern = di["show_trend"], di["show_pattern"]

    # 数据不足时整段隐藏(数据够了自动回来)
    trend_block = (c_sub("最近 7 天") + "<div class='body-t'>%s</div><div class='cv sm'><canvas id='cKcal'></canvas></div><div class='cv sm'><canvas id='cMacro'></canvas></div>" % esc(di["week_text"])) if show_trend else ""
    pattern_block = (c_sub("最近 30 天") + "<div class='body-t'>%s</div>" % esc(di["pattern_text"])) if show_pattern else ""
    body_chart = "<div class='cv sm'><canvas id='cBody'></canvas></div>" if bo["has"] else ""

    # —— 用统一组件拼装整页 ——
    head = "<div class='head'><div class='upd'>更新至 %s</div><h1>%s</h1><div class='answer'>%s</div></div>" % (
        esc(D["updated"]), esc(mst["question"]), esc(mst["answer"]))
    c_milestone = c_card(milestone_html(mst), "里程碑 · 三个目标")
    stats = (c_stat("今日体重", f1(t["weight"]), "kg") + c_stat("7 日均重", f1(t["ma7"]), "kg", "ma")
             + "<div class='stat tr'><div class='l'>趋势</div>%s</div>" % c_tag(t["trend_label"]))
    c_status = c_card("<div class='stats'>%s</div><div class='one'>%s</div><div class='speed'>%s</div>" % (
        stats, esc(t["one"]), esc(t["speed"])), "今日状态")
    weight_kv = ("<div class='kv'><div class='row'><span class='k'>今日变化</span><span>%s</span></div>"
                 "<div class='row'><span class='k'>可能原因</span><span>%s</span></div>"
                 "<div class='row'><span class='k'>是否调整</span><span>%s</span></div></div>") % (
                 esc(tr["chg"]), esc(tr_cause), esc(tr["adj"]))
    c_weight = c_card("<div class='range' id='range'></div><div class='cv'><canvas id='cWeight'></canvas></div>%s%s" % (
        weight_kv, c_coach(tr["coach"])), "体重趋势")
    c_diet = c_card("<div class='diet-grid'><div class='diet-left'>%s</div><div class='diet-right'>%s</div></div>%s%s%s" % (
        d_left, d_right, c_coach(d_coach), trend_block, pattern_block), "饮食 · 今日吃饭小日记 🍱")
    c_calendar = c_card("<div class='body-t' style='margin-bottom:12px'>%s</div>%s%s" % (
        esc(ca["today_line"]), cal_html(ca), c_coach(ca["expl"])), "活动日历 · %d 年 %d 月" % (ca["year"], ca["month"]), cls="")
    c_bodycomp = c_card("<div class='body-t'>%s</div>%s%s" % (
        esc(bo["summary"]), body_chart, c_coach(bo["coach"])), "身体成分", cls="")
    c_supp = c_card(supp_html(supp), cls=("praise-card" if supp["kind"] == "praise" else ""))
    c_recap = c_card("<div class='body-t'>%s</div>" % esc(recap_txt), "本周小结", cls="")
    row2 = "<div class='row2'><div class='col'>%s</div><div class='col'>%s%s%s</div></div>" % (
        c_calendar, c_bodycomp, c_supp, c_recap)
    body_html = head + c_milestone + c_status + c_weight + c_diet + row2

    out = TEMPLATE.replace("/*DATA*/", json.dumps(D["charts"], ensure_ascii=False)).replace("__BODY__", body_html)
    out = out.replace("数据本地存储 · ~/Documents/cut-buddy-data", foot)
    out = out.replace("__CELEBRATE__", celebrate_html(D["celebrate"]) if D.get("celebrate") else "")
    return out

def celebrate_html(c):
    verb = {"增肌": "增到", "维持": "维持到"}.get(c.get("phase"), "减到")
    line = "所有目标都达成啦，太强了 👑" if c.get("final") else "稳稳的——这是你趋势走出来的，不是单日运气。"
    return (CELEBRATE_TMPL.replace("__NAME__", esc(str(c.get("name", ""))))
            .replace("__VERB__", verb).replace("__GOAL__", esc(str(c["goal"])))
            .replace("__LINE__", esc(line)))

CELEBRATE_TMPL = r"""
<div id="celebrate" style="position:fixed;inset:0;z-index:9999;
 background:radial-gradient(120% 90% at 50% 18%, rgba(18,33,24,.92), rgba(8,12,10,.95));">
<canvas id="fwc" style="position:fixed;inset:0;display:block;"></canvas>
<div onclick="cbHide()" style="position:fixed;right:18px;top:16px;z-index:2;width:38px;height:38px;border-radius:50%;
 cursor:pointer;background:rgba(255,255,255,.14);color:#fff;border:1px solid rgba(255,255,255,.2);font-size:20px;line-height:36px;text-align:center;">✕</div>
<div class="cbcard">
 <div style="font-size:54px;filter:drop-shadow(0 4px 12px rgba(0,0,0,.4))">🎉</div>
 <div style="font-size:12px;letter-spacing:.35em;color:#8FE0B6;font-weight:700;margin-top:12px;">里程碑达成</div>
 <div style="font-size:29px;font-weight:800;margin:10px 0 2px;">恭喜__NAME__！__VERB__ <span style="color:#7CE0AA">__GOAL__ kg</span></div>
 <div style="font-size:15px;color:#D7E5DC;line-height:1.75;margin-top:8px;">__LINE__</div>
</div>
<div style="position:fixed;left:0;right:0;bottom:16px;text-align:center;color:rgba(255,255,255,.4);font-size:12px;">点 ✕ / 空白处 / 按 Esc 关闭，回到看板</div>
</div>
<style>
.cbcard{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.85);opacity:0;text-align:center;color:#fff;
 background:rgba(22,33,26,.5);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.16);
 border-radius:26px;padding:34px 40px;box-shadow:0 24px 70px rgba(0,0,0,.45);max-width:88vw;
 animation:cbpop .7s cubic-bezier(.2,.9,.2,1.1) .2s forwards;}
@keyframes cbpop{to{transform:translate(-50%,-50%) scale(1);opacity:1;}}
</style>
<script>
(function(){
 var ov=document.getElementById('celebrate'),cv=document.getElementById('fwc'),ctx=cv.getContext('2d');
 var W,H;function sz(){W=cv.width=innerWidth;H=cv.height=innerHeight;}sz();addEventListener('resize',sz);
 var CL=['#7CE0AA','#FFD58A','#FF9E7D','#A9C8FF','#F5A3C7','#FFF1A8','#9FE3C0','#FFC2D1'];
 var rand=function(a,b){return a+Math.random()*(b-a);},pick=function(a){return a[Math.floor(Math.random()*a.length)];};
 var parts=[],rk=[],raf=null;
 function burst(x,y){var n=Math.floor(rand(46,78)),c1=pick(CL),c2=pick(CL);for(var i=0;i<n;i++){var ang=rand(0,6.283),sp=rand(1.2,6.8);parts.push({x:x,y:y,vx:Math.cos(ang)*sp,vy:Math.sin(ang)*sp,life:rand(55,95),age:0,color:Math.random()<.5?c1:c2,size:rand(1.4,3)});}}
 function launch(){rk.push({x:rand(W*.18,W*.82),y:H+10,vy:-rand(8.5,11.5),ty:rand(H*.12,H*.46),color:pick(CL)});}
 function go(){for(var k=0;k<6;k++)setTimeout(launch,k*180);}
 function frame(){ctx.fillStyle='rgba(8,12,10,0.22)';ctx.fillRect(0,0,W,H);
  for(var i=rk.length-1;i>=0;i--){var r=rk[i];r.y+=r.vy;r.vy+=0.08;ctx.globalAlpha=.9;ctx.fillStyle=r.color;ctx.beginPath();ctx.arc(r.x,r.y,2.2,0,7);ctx.fill();if(r.vy>=-1.2||r.y<=r.ty){burst(r.x,r.y);rk.splice(i,1);}}
  for(var j=parts.length-1;j>=0;j--){var p=parts[j];p.age++;p.vy+=0.05;p.vx*=0.985;p.vy*=0.985;p.x+=p.vx;p.y+=p.vy;var a=Math.max(0,1-p.age/p.life);ctx.globalAlpha=a;ctx.fillStyle=p.color;ctx.beginPath();ctx.arc(p.x,p.y,p.size,0,7);ctx.fill();if(p.age>=p.life)parts.splice(j,1);}
  ctx.globalAlpha=1;raf=requestAnimationFrame(frame);}
 window.cbHide=function(){ov.style.display='none';if(raf){cancelAnimationFrame(raf);raf=null;}};
 ov.addEventListener('click',function(e){if(e.target===ov||e.target===cv)cbHide();});
 addEventListener('keydown',function(e){if(e.key==='Escape')cbHide();});
 frame();go();
})();
</script>"""

TEMPLATE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>快乐减脂趋势看板</title>
<script src="./assets/chart.umd.min.js"></script>
<style>
:root{
 /* 色板 */
 --bg:#F4F6F3;--card:#FFFFFF;--ink:#1A1C19;--t2:#5C6159;--t3:#9AA09A;--line:#EAEDE8;
 --accent:#3C8C66;--accent-d:#2E7355;--soft:#E9F2EC;--warn:#C8824E;
 /* 状态色:日历格子 & 活动 chip —— 全部集中一处,杜绝撞色 */
 --cell:#FAFBFA;--cell-today:#FFFFFF;
 --chip-train:var(--soft);--chip-train-t:var(--accent-d);
 --chip-cardio:#F3EBDF;--chip-cardio-t:#9A6E3C;
 --chip-rest:#EEF0ED;--chip-rest-t:var(--t3);
 --chip-other:#EDEFEC;--chip-other-t:var(--t2);
 /* 间距刻度 / 圆角(组件统一引用) */
 --s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;
 --r:16px;--r-sm:12px;
}
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro SC","SF Pro Text","PingFang SC","Microsoft YaHei",sans-serif;
 font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;letter-spacing:.005em;}
.wrap{max-width:540px;margin:0 auto;padding:30px 18px 80px;}
.head{margin-bottom:22px;}
.head h1{font-size:19px;font-weight:650;letter-spacing:-.01em;margin:0 0 3px;}
.head .sb{color:var(--t3);font-size:12px;} .head .up{color:var(--t3);font-size:11px;margin-top:3px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:20px;margin-bottom:12px;
 animation:rise .5s cubic-bezier(.2,.7,.2,1) both;}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.head .upd{font-size:11px;color:var(--t3);letter-spacing:.04em;margin-bottom:7px;}
.head h1{font-size:23px;font-weight:700;letter-spacing:-.01em;margin:0;}
.head .answer{font-size:24px;font-weight:700;color:var(--accent);margin-top:2px;letter-spacing:-.01em;}
.msbar{position:relative;margin:56px 24px 4px;}
.track2{position:relative;height:8px;background:#E9EDE6;border-radius:8px;}
.fill2{position:absolute;left:0;top:0;height:100%;background:var(--accent);border-radius:8px;}
.here{position:absolute;top:4px;width:13px;height:13px;border-radius:50%;background:#fff;border:3px solid var(--accent);box-shadow:0 1px 4px rgba(0,0,0,.18);transform:translate(-50%,-50%);z-index:4;}
.here b{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);font-size:11px;font-weight:700;color:var(--accent-d);white-space:nowrap;}
.node{position:absolute;top:4px;z-index:2;}
.node .pole{position:absolute;left:0;top:-22px;width:2px;height:22px;background:#C9D0C7;transform:translateX(-50%);border-radius:2px;}
.node .flag{position:absolute;left:0;top:-45px;transform:translateX(-50%);min-width:34px;height:25px;padding:0 9px;
 display:flex;align-items:baseline;justify-content:center;gap:1px;border-radius:8px;background:#fff;border:1.5px solid #C9D0C7;
 color:var(--t2);font-size:14px;font-weight:700;box-shadow:0 2px 5px rgba(40,46,30,.10);}
.node .flag i{font-style:normal;font-size:9px;font-weight:600;color:var(--t3);}
.node .nl{position:absolute;top:24px;left:0;transform:translateX(-50%);font-size:10.5px;color:var(--t3);white-space:nowrap;}
.node.done .flag{background:var(--accent);border-color:var(--accent);color:#fff;} .node.done .flag i{color:#dbe7df;}
.node.done .pole{background:var(--accent);} .node.done .nl{color:var(--accent);}
.node.now .flag{border-color:var(--accent);border-width:2px;color:var(--accent-d);box-shadow:0 0 0 4px var(--soft),0 2px 5px rgba(40,46,30,.10);}
.node.now .flag i{color:var(--accent);} .node.now .pole{background:var(--accent);} .node.now .nl{color:var(--accent-d);font-weight:700;}
.node.start .flag{background:#EEF0EA;border-color:#D6DBD2;color:var(--t2);} .node.start .flag i{color:var(--t3);}
.node.start .pole{background:#CCD2C9;} .node.start .nl{color:var(--t3);}
/* 首尾节点贴边对齐，避免旗子/文字飘到卡片外 */
.node.start .flag,.node.start .nl{transform:translateX(0);left:-2px;}
.node.end .flag,.node.end .nl{transform:translateX(-100%);left:2px;}
@media(max-width:600px){
 .msbar{margin:52px 16px 4px;}
 .node .flag{min-width:30px;height:23px;padding:0 7px;font-size:13px;}
 .node .nl{font-size:10px;}
 .here b{font-size:10px;}
}
.tip-t{font-size:14px;font-weight:650;color:var(--ink);margin-bottom:5px;}
.tip-b{font-size:13.5px;line-height:1.72;color:var(--t2);}
.speed{margin-top:11px;font-size:13px;line-height:1.65;color:var(--t2);padding-left:12px;border-left:2px solid var(--soft);}
.row2{display:block;}
@media(min-width:880px){
 .wrap{max-width:1000px;}
 .span2 .stats{gap:48px;}
 .row2{display:flex;gap:14px;align-items:flex-start;}
 .row2>.col{flex:1;min-width:0;}
}
.mt{font-size:11px;font-weight:600;color:var(--t3);letter-spacing:.13em;text-transform:uppercase;margin:0 0 16px;}
/* 今日状态 */
.stats{display:flex;align-items:flex-end;gap:30px;margin-bottom:14px;}
.stat .l{font-size:11px;color:var(--t3);letter-spacing:.03em;margin-bottom:8px;}
.stat .v{font-size:34px;font-weight:680;line-height:.9;letter-spacing:-.02em;}
.stat .v .u{font-size:13px;font-weight:500;color:var(--t3);margin-left:3px;}
.stat.ma .v{font-size:22px;font-weight:600;color:var(--accent);}
.stat.tr{margin-left:auto;text-align:right;}
.tag{display:inline-block;background:var(--soft);color:var(--accent-d);border-radius:8px;padding:5px 11px;font-size:12px;font-weight:600;}
.one{font-size:15px;line-height:1.55;color:var(--t2);}
/* 体重三行 */
.kv{margin-top:14px;}
.kv .row{display:flex;align-items:baseline;gap:14px;padding:9px 0;border-top:1px solid var(--line);font-size:14px;}
.kv .row:first-child{border-top:none;padding-top:2px;}
.kv .k{color:var(--t3);font-size:12px;letter-spacing:.04em;width:56px;flex:none;}
.cv{position:relative;height:220px;margin:6px 0 2px;} .cv.sm{height:150px;}
.range{display:flex;gap:8px;margin-bottom:8px;}
.range button{background:transparent;border:1px solid var(--line);color:var(--t3);border-radius:8px;padding:4px 12px;font-size:12px;cursor:pointer;}
.range button.on{background:var(--ink);color:#fff;border-color:var(--ink);}
.sub{font-size:11px;color:var(--t3);font-weight:600;letter-spacing:.13em;text-transform:uppercase;margin:20px 0 10px;}
/* 饮食日记 · 2×2 方格 */
.diet-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:center;}
@media(max-width:600px){.diet-grid{grid-template-columns:1fr;gap:16px;}}
.diary{display:flex;flex-direction:column;gap:10px;}
.meal{display:flex;gap:11px;align-items:flex-start;padding:12px;border:1px solid var(--line);border-radius:13px;background:#FCFDFC;}
.mvis{flex:none;}
.mtile{width:50px;height:50px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:25px;
 background:linear-gradient(135deg,#F1F6EF,#E9F1EB);border:1px solid #E4ECE5;}
.mthumb{width:54px;height:54px;border-radius:13px;object-fit:cover;border:1px solid var(--line);
 filter:saturate(1.05) contrast(1.02);display:block;}
.mbody{min-width:0;flex:1;}
.mn{font-size:11px;color:var(--t3);letter-spacing:.1em;text-transform:uppercase;font-weight:600;margin-bottom:4px;}
.mlist{line-height:1.6;}
.mfi{font-size:14px;font-weight:600;}
.sep{color:var(--t3);opacity:.55;margin:0 7px;font-weight:400;}
.mnut{font-size:11px;color:var(--t3);margin-top:5px;}
.meal.empty{background:#FAFBF9;} .meal.empty .mtile{opacity:.5;} .meal.empty .mbody{opacity:.6;}
.mf-empty{font-size:14px;color:var(--t3);}
.praise-card{background:linear-gradient(135deg,#FFF3DB,#FFE6CB);border:1px solid #F6D5B3;position:relative;overflow:hidden;}
.praise-card .pz-deco{position:absolute;right:8px;top:-6px;font-size:50px;opacity:.22;transform:rotate(8deg);pointer-events:none;}
.pz-h{position:relative;font-size:11.5px;font-weight:800;letter-spacing:.1em;color:#C2772E;}
.pz-big{position:relative;font-size:16.5px;line-height:1.62;font-weight:650;color:#7C4A1E;margin-top:10px;}
/* 三大营养素达标 */
.macro{margin-top:16px;border:1px solid var(--line);border-radius:12px;padding:13px 15px;background:#FCFDFC;}
.mttl{font-size:11px;color:var(--t3);letter-spacing:.06em;margin-bottom:9px;}
.mr{display:grid;grid-template-columns:48px 1fr auto;align-items:baseline;gap:10px;padding:5px 0;}
.mk{color:var(--t2);font-size:13px;}
.mv{font-size:16px;font-weight:650;letter-spacing:-.01em;} .mv .sl{font-size:12px;font-weight:400;color:var(--t3);margin-left:5px;}
.ms{font-size:12px;font-weight:600;justify-self:end;}
.ms.ok{color:var(--accent);} .ms.low{color:var(--t3);} .ms.warn{color:var(--warn);}
.verdict{margin-top:13px;font-size:14.5px;line-height:1.65;color:var(--ink);}
.advice{margin-top:10px;font-size:13px;line-height:1.6;color:var(--t2);padding-left:12px;border-left:2px solid var(--soft);}
.note{padding:14px;background:#FAFBFA;border:1px dashed var(--line);border-radius:12px;font-size:12.5px;color:var(--t3);line-height:1.7;}
.body-t{font-size:14px;line-height:1.7;color:var(--t2);}
.coach{margin-top:14px;background:#F2F6F1;border:1px solid #E3EDE5;border-radius:12px;padding:11px 14px;font-size:13px;line-height:1.72;color:var(--t2);}
.coach-tag{display:inline-block;font-size:10px;font-weight:700;color:var(--accent-d);background:#E1EFE6;padding:2px 8px;border-radius:6px;margin-right:8px;letter-spacing:.05em;}
/* 饮食 · 截至目前(横向铺平) */
.diet-right{padding:14px 16px;background:#FCFDFC;border:1px solid var(--line);border-radius:13px;}
.sf-h{font-size:11px;color:var(--t3);font-weight:600;letter-spacing:.13em;text-transform:uppercase;margin:0 0 12px;}
.sf-strip{display:flex;flex-direction:column;gap:11px;}
.sf-item{display:flex;align-items:baseline;gap:5px;}
.sf-item .sk{font-size:12px;color:var(--t3);}
.sf-item b{font-size:18px;font-weight:700;letter-spacing:-.01em;}
.sf-item i{font-size:11px;color:var(--t3);font-style:normal;}
.sf-item em{font-size:11px;font-weight:600;font-style:normal;margin-left:1px;}
.sf-item em.ok{color:var(--accent);} .sf-item em.mut{color:var(--t3);} .sf-item em.warn{color:var(--warn);}
.judge2{margin-top:15px;font-size:14px;line-height:1.7;color:var(--ink);}
.advice2{margin-top:11px;font-size:13.5px;line-height:1.65;color:var(--t2);}
.jt{display:inline-block;font-size:11px;font-weight:700;color:var(--accent-d);background:var(--soft);padding:2px 8px;border-radius:6px;margin-right:8px;}
/* 日历 */
table.cal{width:100%;border-collapse:separate;border-spacing:4px;}
table.cal th{color:var(--t3);font-weight:500;padding:0 0 6px;font-size:10px;}
table.cal td{background:var(--cell);border-radius:9px;height:50px;vertical-align:top;padding:5px;width:14.28%;}
table.cal td.e{background:transparent;}
table.cal td.td{background:var(--cell-today);box-shadow:inset 0 0 0 1.5px var(--accent);}
.dn{font-size:10px;color:var(--t3);}
.chip{display:block;font-size:9.5px;padding:1px 4px;border-radius:5px;margin-top:2px;line-height:1.5;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.c-a{background:var(--chip-train);color:var(--chip-train-t);} .c-b{background:var(--chip-cardio);color:var(--chip-cardio-t);}
.c-r{background:var(--chip-rest);color:var(--chip-rest-t);} .c-o{background:var(--chip-other);color:var(--chip-other-t);}
.foot{text-align:center;color:var(--t3);font-size:10px;letter-spacing:.05em;margin-top:22px;}
</style></head><body><div class="wrap">

__BODY__

<div class="foot">数据本地存储 · ~/Documents/cut-buddy-data</div>
</div>
<script>
const D=/*DATA*/;
function fd(m){return new Date(m).toISOString().slice(5,10);}
// 悬浮提示标题统一显示成"月-日"(否则会弹出原始时间戳那串大数字)
Chart.defaults.plugins.tooltip.callbacks=Chart.defaults.plugins.tooltip.callbacks||{};
Chart.defaults.plugins.tooltip.callbacks.title=function(its){return (its&&its.length)?fd(its[0].parsed.x):'';};
const GRID='#EEF1EC',TICK='#9AA09A';
const AX={type:'linear',ticks:{color:TICK,maxTicksLimit:6,font:{size:10},callback:v=>fd(v)},grid:{color:GRID,drawTicks:false},border:{display:false}};
function yax(o){return Object.assign({ticks:{color:TICK,font:{size:10}},grid:{color:GRID,drawTicks:false},border:{display:false}},o||{});}
const LEG={labels:{color:TICK,boxWidth:10,boxHeight:10,font:{size:10},usePointStyle:true,pointStyle:'circle'}};
let wc;
function drawWeight(lo,hi){
  const cfg={data:{datasets:[
    {type:'line',label:'目标',data:[{x:D.minx,y:D.target},{x:D.maxx,y:D.target}],borderColor:'#CBD2C9',borderDash:[5,5],borderWidth:1,pointRadius:0},
    {type:'scatter',label:'每日',data:D.weightDaily,pointRadius:1.4,pointBackgroundColor:'#CBD2C9',borderColor:'#CBD2C9'},
    {type:'line',label:'7 日均',data:D.ma7,borderColor:'#3C8C66',borderWidth:2.5,pointRadius:0,tension:.3},
  ]},options:{maintainAspectRatio:false,plugins:{legend:Object.assign({},LEG,{labels:Object.assign({},LEG.labels,{filter:i=>i.text!=='目标'})})},
    scales:{x:Object.assign({},AX,{min:lo,max:hi}),y:yax({})}}};
  if(wc)wc.destroy(); wc=new Chart(document.getElementById('cWeight'),cfg);
}
drawWeight(D.defaultLo,D.maxx);
const rd=document.getElementById('range');
[['近 4 月',D.defaultLo],['全部',D.minx]].forEach(([t,lo],i)=>{const b=document.createElement('button');b.textContent=t;if(i===0)b.classList.add('on');
  b.onclick=()=>{[...rd.children].forEach(c=>c.classList.remove('on'));b.classList.add('on');drawWeight(lo,D.maxx);};rd.appendChild(b);});
if(D.showDietChart && D.kcalDaily.length){
  new Chart(document.getElementById('cKcal'),{data:{datasets:[
    {type:'bar',label:'每日热量',data:D.kcalDaily,backgroundColor:'#D7E5DC',borderRadius:4},
    D.kcalAvg?{type:'line',label:'均值',data:[{x:D.kcalDaily[0].x,y:D.kcalAvg},{x:D.kcalDaily[D.kcalDaily.length-1].x,y:D.kcalAvg}],borderColor:'#3C8C66',borderWidth:1.5,pointRadius:0}:null
  ].filter(Boolean)},options:{maintainAspectRatio:false,plugins:{legend:LEG},scales:{x:AX,y:yax({})}}});
  new Chart(document.getElementById('cMacro'),{data:{datasets:[
    {type:'line',label:'蛋白 g',data:D.proteinDaily,borderColor:'#3C8C66',borderWidth:2,pointRadius:2,tension:.3},
    {type:'line',label:'脂肪 g',data:D.fatDaily,borderColor:'#C8824E',borderWidth:2,pointRadius:2,tension:.3},
  ]},options:{maintainAspectRatio:false,plugins:{legend:LEG},scales:{x:AX,y:yax({})}}});
}
if(D.showBody && D.fat.length){
  new Chart(document.getElementById('cBody'),{data:{datasets:[
    {type:'line',label:'瘦体重 kg',data:D.lean,borderColor:'#3C8C66',borderWidth:2,pointRadius:0,yAxisID:'y'},
    {type:'line',label:'脂肪量 kg',data:D.fat,borderColor:'#C8824E',borderWidth:2,pointRadius:1.4,pointBackgroundColor:'#C8824E',yAxisID:'y1'},
  ]},options:{maintainAspectRatio:false,plugins:{legend:LEG},scales:{x:AX,
    y:yax({position:'left',grace:'12%'}),
    y1:yax({position:'right',grace:'12%',grid:{drawOnChartArea:false}})}}});
}
</script>
__CELEBRATE__
</body></html>"""

def ensure_chart_asset():
    # 把随 skill 发布的离线 Chart.js 拷到数据目录的 assets/(看板用相对路径引用),只在缺失时拷
    dst = P("assets/chart.umd.min.js")
    if os.path.exists(dst): return
    skill_asset = os.path.normpath(os.path.join(HERE, "..", "assets", "chart.umd.min.js"))
    if os.path.exists(skill_asset):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(skill_asset, dst)

def main():
    ensure_chart_asset()
    D = build_all()
    with open(P("dashboard.html"), "w", encoding="utf-8") as f: f.write(render(D))
    print("OK", "" if D.get("empty") else "| " + D["today"]["trend_label"])

if __name__ == "__main__":
    main()
