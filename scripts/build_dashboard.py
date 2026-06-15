#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快乐减脂趋势看板 v4(克制简约 · Apple Health 风)。
~/Documents/cut-buddy-data/{weight,meals,workouts,days}.csv + profile.json → dashboard.html。
设计:单一克制绿、统一字号刻度、tabular-nums 数字对齐、8px 间距、去衬线去暖纸。
内容:饮食按松松配额判断三大营养素是否达标、给调整建议。
模块:今日状态 → 体重趋势 → 饮食 → 活动日历 → 身体成分 → 阶段回顾。
"""
import os, csv, json, html, datetime, statistics, collections, calendar as _cal, re, shutil, subprocess, random

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
# D13 大体重起始配额下调(仅减脂期),与 tracker.py 同步:BMI 分档压低碳水起始区间;
# 档位下限=上限-0.3;此档休息日碳水改 -0.3(女版配额表大体重行训练/休息差≈0.3)。
BMI_CARB_CAP = [(32, {"男": 2.0, "女": 1.7}), (28, {"男": 2.5, "女": 2.1})]
def quota_for(weight, profile, day):
    if not weight: return None
    sex, phase = profile.get("sex", "女"), profile.get("phase", "减脂")
    cfg = QUOTA_CFG.get((phase, sex)) or QUOTA_CFG[("减脂", "女")]
    clo, chi = cfg["carb"]
    bmi_tier = None
    height_cm = profile.get("height_cm")
    if phase == "减脂" and height_cm:
        bmi = weight / (height_cm / 100.0) ** 2
        for th, caps in BMI_CARB_CAP:
            if bmi > th:
                cap = caps.get(sex, caps["女"])
                if cap < chi:
                    chi = cap; clo = min(clo, round(chi - 0.3, 1)); bmi_tier = "BMI>%d" % th
                break
    rest_step = 0.3 if bmi_tier else 0.5
    if day != "training": clo, chi = clo - rest_step, chi - rest_step
    plo, phi = cfg["protein"]
    return {"carb_g": round((clo+chi)/2*weight), "protein_g": round((plo+phi)/2*weight),
            "carb_low": round(clo*weight), "carb_high": round(chi*weight),
            "protein_low": round(plo*weight), "protein_high": round(phi*weight), "bmi_tier": bmi_tier}

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
PARTS = ["肩", "腿", "胸", "背", "臀", "手臂", "臂", "核心", "腹", "全身"]
def strength_part(note):
    """从备注里提取力量训练的部位(练肩→肩),没有就返回空。"""
    note = note or ""
    found = []
    for p in PARTS:
        if p in note and not any(p in f for f in found): found.append(p)
    return "/".join(found)
def day_activities(wos, detail=False):
    labs = []
    for w in wos:
        if w.get("is_rest") == "1": labs.append("休息"); continue
        lab = LABEL.get(w.get("type", ""), w.get("type", "其他"))
        # detail 模式下,力量带上部位(练腿→力量·腿),供日历显示;不影响其他逻辑
        if detail and lab == "力量":
            part = strength_part(w.get("note", ""))
            if part: lab = "力量·" + part
        labs.append(lab)
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
    # "今天"取最近有任何记录(称重/饮食/运动)的那天,不只看称重——否则当天只记了吃/练、没称重,看板会卡在昨天
    daily = {d: w for (d, w, _, _) in series}
    cand = [series[-1][0]]
    if mbd: cand.append(max(pdate(d) for d in mbd))
    if wbd: cand.append(max(pdate(d) for d in wbd))
    L = max(cand)
    weighed_today = L in daily
    latest_w = daily.get(L, series[-1][1])   # 当天没称重就沿用最近一次体重(数字不空,但下方会标注)
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
    elif status == "平台": coach_w += "连续两三周没动。松松铁律:先跑「不掉秤9查」排查执行(定量没?混合菜拆了没?隐形脂肪?有氧没做?水果没扣主食?外食/喝酒?)——确认是真没缺口了,再每天少吃约150kcal(≈100g米饭+1全蛋)或每周加约1000kcal有氧;碳水降到2g/kg为止,别激进砍。"
    elif status == "升": coach_w += "略升先别慌，多半是水分或执行，看两周再判断。"
    else: coach_w += "数据还不到一周，先记着，趋势会清晰起来。"
    # 减脂终点:接近健康 BMI 时提示考虑转增肌(松松:别追更低体重/体脂)
    if profile.get("phase", "减脂") == "减脂" and profile.get("height_cm") and (ma_now or latest_w):
        bmi = (ma_now or latest_w) / (profile["height_cm"] / 100.0) ** 2
        bmi_floor = 20.0 if profile.get("sex", "女") == "女" else 22.0
        if bmi <= bmi_floor + 1:
            coach_w += "另外:你 BMI 已约 %.1f,接近松松说的减脂终点(女≈20–21/男≈22–23)。到这一带就别追更低体重了——腰腹脂肪本就最后掉,再硬减容易掉肌肉/像'骷髅兵',可考虑转增肌。" % bmi

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
        "minx": ms(series[0][0]), "maxx": ms(L),
        "lo4w": max(ms(series[0][0]), ms(L - datetime.timedelta(days=28))),
        "lo4m": max(ms(series[0][0]), ms(L - datetime.timedelta(days=120))),
        "kcalDaily": diet["kcalDaily"], "kcalAvg": diet["kcalAvg"],
        "proteinDaily": diet["proteinDaily"], "fatDaily": diet["fatDaily"], "showDietChart": diet["show_trend"],
        "bf": body["bf_series"], "fat": body["fat_series"], "lean": body["lean_series"], "showBody": body["has"]}
    return {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "today": {"weight": latest_w, "ma7": ma_now, "trend_label": trend_label, "one": one, "speed": speed},
            "trend": {"chg": chg_line, "cause": cause_line, "adj": adj_line, "coach": coach_w},
            "ms": mstone, "diet": diet, "calendar": cal, "body": body,
            "supp": supp, "recap": recap, "is_sunday": L.isoweekday() == 7,
            "charts": charts, "celebrate": celebrate}

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
        base = re.split(r'[（(]', nm)[0].strip()   # 去掉"(喷6下)""(各一勺)"这类备注再判断
        if any(k in base for k in ("食用油", "调和油", "蚝油", "酱油", "生抽", "老抽", "沙拉酱", "蛋黄酱", "番茄酱")): return True
        return len(base) <= 3 and (base.endswith("油") or base.endswith("酱") or base.endswith("盐"))
    photos_dir = os.path.join(DATA_DIR, "photos")
    def find_photo(mn):
        for ext in ("jpg", "jpeg", "png", "webp", "heic"):
            fn = "%s-%s.%s" % (L.isoformat(), mn, ext)
            if os.path.exists(os.path.join(photos_dir, fn)): return "photos/" + fn
        return ""
    def mealcard(mn, items):
        sub = day_nutrition(items)
        # 显示折叠:混合菜原料共用 dish 名→只显示一次菜名;无 dish 的单品显示本名;独立调料不单列
        shown, seen = [], set()
        for i in items:
            label = i.get("dish") or i["food"]
            if not i.get("dish") and is_condiment(i["food"]): continue
            if label not in seen: seen.add(label); shown.append(label)
        if not shown: shown = [i["food"] for i in items]
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
        fat_lo = round(fatT * 0.75)                     # 脂肪保底下限(松松:吃太少→激素/经期紊乱)
        def tag_macro(act, low, high, soft_high=False):  # 碳水/蛋白:区间判断(下限-上限)
            if act < low: return ("还差 %dg到下限" % round(low - act), "mut")
            if act <= high: return ("区间内 ✓", "ok")
            # 碳水超上限=硬警告(填平缺口);蛋白超上限=软(松松:蛋白可上探保肌、不易长胖)
            return ("已充足", "ok") if soft_high else ("超上限 %dg" % round(act - high), "warn")
        def tag_fat(act):                              # 脂肪:下限–目标–上限
            if act < fat_lo: return ("偏低·别戒油", "mut")   # 太低伤激素/经期
            if act <= fatT: return ("很稳", "ok")
            if act <= fat_hi: return ("适中", "ok")
            return ("超上限·后面少油", "warn")
        ct = tag_macro(tot["carb"], q["carb_low"], q["carb_high"])
        pt = tag_macro(tot["protein"], q["protein_low"], q["protein_high"], soft_high=True)
        ftg = tag_fat(tot["fat"])
        so_far = [
            {"name": "碳水", "act": round(tot["carb"]), "lo": q["carb_low"], "hi": q["carb_high"], "rng": "%d–%d" % (q["carb_low"], q["carb_high"]), "tag": ct[0], "cls": ct[1]},
            {"name": "蛋白", "act": round(tot["protein"]), "lo": q["protein_low"], "hi": q["protein_high"], "rng": "%d–%d" % (q["protein_low"], q["protein_high"]), "tag": pt[0], "cls": pt[1]},
            {"name": "脂肪", "act": round(tot["fat"]), "lo": fat_lo, "hi": fat_hi, "rng": "%d–%d" % (fat_lo, fat_hi), "tag": ftg[0], "cls": ftg[1]},
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
        # 晚餐已记 = 今天三顿正餐吃完、没有"下一餐"了 → 不再前瞻提示(全天结论交给"目前整体")
        if "晚餐" in bymeal:
            next_meal = ""

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
        # 脂肪刺客 / 高脂肉(松松:看不见的脂肪最易低估)
        FAT_TRAPS = ("五花", "排骨", "肥牛", "肥羊", "培根", "香肠", "肉肠", "午餐肉", "肉丸", "油条", "蛋挞", "麦芬", "坚果", "花生", "薯片", "炸", "酥", "奶油", "黄油", "糖醋", "锅包", "红烧肉", "扣肉", "鸡皮", "鸭皮")
        traps = list(dict.fromkeys(f for f in foods_today if any(k in f for k in FAT_TRAPS)))
        if traps:
            coach_d += "留意「%s」属高脂肉/糖油——松松说这类'看不见的脂肪'极占配额(宽油炒蛋1个就20–30g、两把坚果40g),记得如实计脂、别当瘦肉算。" % "、".join(traps[:3])
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
        cells.append({"day": d, "acts": day_activities(wbd.get(ds, []), detail=True), "today": (datetime.date(y, m, d) == L)})
    while len(cells) % 7: cells.append(None)
    weeks = [cells[i:i+7] for i in range(0, len(cells), 7)]
    t_acts = day_activities(wbd.get(L.isoformat(), []), detail=True)
    note = next((w["note"] for w in wbd.get(L.isoformat(), []) if w.get("note")), "")
    today_line = ("今天已完成 · " + " + ".join(t_acts) + ("　" + note if note else "")) if t_acts else ""
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
        expl += "减脂期力量配重别降(配重守住=肌肉守住,大项配重掉=缺口太大或恢复不够的信号);有氧按体重来——≥80kg 靠吃就够、≤70kg 每周约 2 小时、70–80kg 看饿不饿,有氧每周别超 4 小时、放力量之后。"
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

def build_tip():
    t = random.choice(TIPS)   # 每次刷新随机一条,看板常看常新
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
    """夸夸池:每条都由真实记录驱动(不空夸),凑齐当下成立的所有夸点,随机抽一条。"""
    pr = []
    phase = profile.get("phase", "减脂")
    latest_w = series[-1][1] if series else None
    today = mbd.get(L.isoformat(), [])
    is_train = any(w.get("is_strength") == "1" for w in wbd.get(L.isoformat(), []))
    # ① 单顿蛋白堆得好
    if diet.get("has_today"):
        tp = round(sum(float(m["protein"] or 0) for m in today))
        if tp >= 35:
            pr.append("今天吃得很会——蛋白质已经堆到 %dg，保肌满分。" % tp)
    # ② 节奏(分阶段:减脂稳降/增肌稳涨/维持纹丝不动)
    if rate is not None:
        if phase == "减脂" and -0.7 <= rate <= -0.08:
            pr.append("最近 7 日均稳稳往下，约 %.2f kg/周——不快不慢、不掉肌肉，这个减脂节奏你拿捏得特别好。" % abs(rate))
        elif phase == "增肌" and 0.05 <= rate <= 0.45:
            pr.append("周均稳稳往上 +%.2f kg/周——这是长肌肉的节奏，不是堆脂肪的速度，涨得很干净。" % rate)
        elif phase == "维持" and abs(rate) <= 0.15:
            pr.append("体重稳得像定海神针——维持期要的就是这份稳，说明你已经会吃了。")
    # ③ 训练频率(近两周)
    cut = L - datetime.timedelta(days=14)
    tdays = set(ds for ds, wos in wbd.items() if pdate(ds) >= cut and any(w.get("is_rest") != "1" for w in wos))
    sdays = set(ds for ds, wos in wbd.items() if pdate(ds) >= cut and any(w.get("is_strength") == "1" for w in wos))
    if len(tdays) >= 4:
        extra = "（力量 %d 天）" % len(sdays) if sdays else ""
        pr.append("最近运动很勤——近两周练了 %d 天%s，还坚持举铁保肌肉，很专业。" % (len(tdays), extra))
    # ④ 连续称重打卡(streak)
    sdates = {x[0] for x in series}
    streak, d = 0, L
    while d in sdates: streak += 1; d -= datetime.timedelta(days=1)
    if streak >= 5:
        pr.append("连续 %d 天称重打卡——肯每天面对数字的人，没有减不下来的。" % streak)
    elif sum(1 for i in range(7) if (L - datetime.timedelta(days=i)) in sdates) >= 4:
        pr.append("已经坚持记录好几天了——肯记录、肯面对数字，这件事本身就赢了一半。")
    # ⑤ 聪明食物
    foods = [m["food"] for m in today]
    if any(("魔芋" in f or "鸡胸" in f or "鸡蛋" in f) for f in foods):
        pr.append("食物也选得聪明——魔芋、鸡胸、鸡蛋这种高蛋白低脂的，又顶饱又不增负担。")
    # ⑥ 蔬菜到位(松松:~100g 蔬菜≈2g 纤维,先菜后饭压胰岛素)
    veg = sum(float(m["veg_g"] or 0) for m in today)
    if veg >= 100:
        pr.append("今天蔬菜吃了 %dg——先菜后饭压胰岛素这件事，你已经做在习惯里了。" % round(veg))
    # ⑦ 全天蛋白进区间
    if today and latest_w:
        q = quota_for(latest_w, profile, "training" if is_train else "rest")
        totp = sum(float(m["protein"] or 0) for m in today)
        if q and totp >= q["protein_low"]:
            pr.append("全天蛋白 %dg 已经进区间——肌肉的口粮给足了，掉的才是真脂肪。" % round(totp))
    # ⑧ 练后窗口抓住了
    if is_train and any(("练后" in m.get("meal", "")) for m in today):
        pr.append("练后窗口的碳水安排上了——30 分钟内的这顿是全天最值钱的一餐，教科书级执行。")
    # ⑨ 累计成果(从起点算)
    start_w = profile.get("start_weight_kg")
    if start_w and latest_w:
        diffkg = round(start_w - latest_w, 1)
        if phase == "减脂" and diffkg >= 1:
            pr.append("从起点到现在已经甩掉 %.1f kg——相当于 %d 瓶 500ml 矿泉水，都是你一笔一笔记出来的。" % (diffkg, round(diffkg * 2)))
        elif phase == "增肌" and -diffkg >= 1:
            pr.append("从起点到现在已经涨了 %.1f kg——增肌是按月磨的慢功夫，你磨出来了。" % -diffkg)
    # ⑩ 里程碑在望
    ms_list = profile.get("milestones") or []
    if latest_w and ms_list:
        sign = -1 if phase == "增肌" else 1
        todo = [m for m in ms_list if sign * (latest_w - m) > 0]
        if todo:
            dist = abs(latest_w - todo[0])
            if dist <= 1:
                pr.append("离下一个里程碑 %skg 只差 %.1f kg 了——胜利就在眼前，按现在的节奏走就行。" % (f1(todo[0]), dist))
    # ⑪ 周均连降(减脂)——两周口径的"真在变好"
    wks = weekly_avgs(series)
    if phase == "减脂" and len(wks) >= 3 and wks[-1]["avg"] < wks[-2]["avg"] < wks[-3]["avg"]:
        pr.append("周均已经连降两周——按松松的两周口径，这是板上钉钉的真下降，不是水分把戏。")
    # ⑫ 体脂在掉(近4周,需体脂数据)
    bfs = [(d, bf) for (d, w, bf, _) in series if bf is not None and (L - d).days <= 28]
    if len(bfs) >= 2 and bfs[0][1] - bfs[-1][1] >= 0.5:
        pr.append("近 4 周体脂率从 %.1f%% 走到 %.1f%%——肌肉守住、脂肪在掉，这就是减脂质量好的样子。" % (bfs[0][1], bfs[-1][1]))
    # ⑬ 有氧适量(近7天,松松:每周<4小时、心率120最优)
    c7 = L - datetime.timedelta(days=7)
    cardio = sum(float(w.get("cardio_min") or 0) + (float(w.get("duration_min") or 0) if w.get("is_cardio") == "1" else 0)
                 for ds, wos in wbd.items() if pdate(ds) > c7 for w in wos)
    if 60 <= cardio <= 240:
        pr.append("近一周有氧约 %d 分钟——量刚刚好，没超松松说的每周 4 小时上限，加得很克制。" % round(cardio))
    # ⑭ 睡眠到位(今/昨,通用恢复常识,不署松松名)
    dbd = days_by_date()
    for ds in (L.isoformat(), (L - datetime.timedelta(days=1)).isoformat()):
        rec = dbd.get(ds)
        if rec and rec.get("sleep_h"):
            try:
                sh = float(rec["sleep_h"])
                if sh >= 7:
                    pr.append("睡够了 %s 小时——恢复到位,训练和食欲都会更听话,这也是减脂的一部分。" % f1(sh)); break
            except ValueError: pass
    if not pr:
        pr.append("愿意为自己花心思、肯坚持，这份认真本身就很值得夸。")
    return {"line": random.choice(pr)}

def build_supp(profile, series, mbd, wbd, L, rate, diet):
    # 每次刷新随机出 夸夸/小课堂 之一,内容也随机——让"记一笔刷新一次"常有新鲜感
    if random.random() < 0.5:
        p = build_praise(profile, series, mbd, wbd, L, rate, diet)
        return {"kind": "praise", "line": p["line"]}
    t = build_tip()
    return {"kind": "tip", "title": "松松小课堂", "tip_t": t["title"], "tip_b": t["body"]}

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
    a = a.split("·")[0]  # "力量·肩" → 按 "力量" 取色
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
            # 空餐位:手机视图保留(提醒补记),一页分享视图收起(别让"暂未记录"占高度+显得没记全)
            out += ("<div class='meal empty trim-wide'><div class='mvis'><div class='mtile'>%s</div></div>"
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

def diet_right_html(di):  # 截至目前(横向条) + 这一餐(下一餐已抽出为整行长条)
    if not di["has_today"] or not di["so_far"]:
        return "<div class='note'>记一餐后，这里会显示截至目前的营养进度、这一餐判断和下一餐建议。</div>"
    items = "<div class='sf-item'><span class='sk'>热量</span><b>%d</b><i>kcal</i></div>" % di["kcal"]
    for m in di["so_far"]:
        # 进度条:浅轨 + 目标区间[下限,上限]两根刻度 + 实际填充(按达标状态着色),一眼看出"到没到/超没超"
        mx = max(m["hi"] * 1.45, m["act"] * 1.08, 1)
        fill = min(100, m["act"] / mx * 100)
        lop, hip = m["lo"] / mx * 100, m["hi"] / mx * 100
        bar = ("<div class='sf-bar'><i class='sf-fill %s' style='width:%.1f%%'></i>"
               "<i class='sf-tick' style='left:%.1f%%'></i><i class='sf-tick' style='left:%.1f%%'></i></div>") % (
               m["cls"], fill, lop, hip)
        items += ("<div class='sf-row'><div class='sf-item'><span class='sk'>%s</span><b>%d</b><i>/ %s g</i>"
                  "<em class='%s'>%s</em></div>%s</div>") % (m["name"], m["act"], esc(m["rng"]), m["cls"], esc(m["tag"]), bar)
    # 右上只陈列「数据」(热量 + 碳蛋脂配额条);这一餐/下一餐/松松点评统一为下方横条(见 render)
    return "<div class='sf-h'>截至目前 · %s配额区间</div><div class='sf-strip'>%s</div>" % (
            di["day_kind"], items)

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
def c_coach(text, cls="", tag="松松点评"):       # 绿底框:松松点评专属;标签=绿色药丸(coach-tag),与这一餐/下一餐的纯文字标签区分
    return "<div class='coach %s'><span class='coach-tag'>%s</span>%s</div>" % (cls, esc(tag), esc(text))
def c_plain(text, cls="", tag=""):              # 无底色的标签+文字行(这一餐/下一餐用);绿框留给松松点评
    return "<div class='pstrip %s'><b class='ptag'>%s</b>%s</div>" % (cls, esc(tag), esc(text))
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

    d_left = diet_html(di)
    d_coach = di["coach"]; tr_cause = tr["cause"]; recap_txt = recap
    foot = "掉秤搭子 cut-buddy · 一笔一笔记出来的"   # 落款不带本地路径,截图分享出去也干净
    show_trend, show_pattern = di["show_trend"], di["show_pattern"]

    # 饮食卡弹性内容预算:精炼一页视图(只 CSS 隐藏,手机滚动视图仍保留全部)
    # "最近7天/30天"长期趋势在宽屏一页里始终精炼掉(速度点评已覆盖趋势);≥5 张餐卡再撤松松点评
    d_right = diet_right_html(di)
    # 数据的解释统一为三条横条(同款盒/同字号):这一餐(恒保留)→ 下一餐 → 松松点评
    # fitDiet() 按实测高度收起时:先收松松点评(.coach-strip)、再收下一餐(.next-strip)
    judge_strip = c_plain(di["meal_judge"], cls="judge-strip", tag=di["judge_label"]) if di.get("meal_judge") else ""
    next_strip  = c_plain(di["next_meal"],  cls="next-strip",  tag="下一餐")           if di.get("next_meal")  else ""
    # 饮食长期趋势:只留文字点评不画图(7天均值+30天模式,数据不足自动隐藏)
    trend_block = ("<div class='dyn trim-wide'>%s<div class='body-t'>%s</div></div>" % (
        c_sub("最近 7 天"), esc(di["week_text"]))) if show_trend else ""
    pattern_block = ("<div class='dyn trim-wide'>%s<div class='body-t'>%s</div></div>" % (
        c_sub("最近 30 天"), esc(di["pattern_text"]))) if show_pattern else ""
    body_chart = "<div class='cv sm'><canvas id='cBody' role='img' aria-label='身体成分趋势:瘦体重(虚线)与脂肪量(实线)双轴折线图'></canvas></div>" if bo["has"] else ""

    # —— 用统一组件拼装整页(Bento 层级:英雄卡=第一眼焦点) ——
    # 里程碑卡:进度条 + 速度点评一句(msnote 给旗标下方的标签留空隙,防文字压标签)
    c_milestone = c_card(milestone_html(mst) + "<div class='speed msnote'>%s</div>" % esc(t["speed"]),
                         "里程碑 · 三个目标")
    stats = (c_stat("今日体重", f1(t["weight"]), "kg") + c_stat("7 日均重", f1(t["ma7"]), "kg", "ma")
             + "<div class='stat tr'><div class='l'>趋势</div>%s</div>" % c_tag(t["trend_label"]))
    c_hero = ("<div class='card hero'><div class='upd'>更新至 %s</div><h1>%s</h1><div class='answer'>%s</div>"
              "<div class='stats'>%s</div><div class='one'>%s</div></div>") % (
        esc(D["updated"]), esc(mst["question"]), esc(mst["answer"]), stats, esc(t["one"]))
    weight_kv = ("<div class='kv trim-wide'><div class='row'><span class='k'>今日变化</span><span>%s</span></div>"
                 "<div class='row'><span class='k'>可能原因</span><span>%s</span></div>"
                 "<div class='row'><span class='k'>是否调整</span><span>%s</span></div></div>") % (
                 esc(tr["chg"]), esc(tr_cause), esc(tr["adj"]))
    c_weight = c_card("<div class='range' id='range'></div><div class='cv'><canvas id='cWeight' role='img' aria-label='体重趋势:单日散点(弱化)与7日均线(主线)'></canvas></div>%s%s" % (
        weight_kv, c_coach(tr["coach"])), "体重趋势", cls="chartgrow")
    c_diet = c_card("<div class='diet-grid'><div class='diet-left'>%s</div><div class='diet-right'>%s</div></div>%s%s%s%s%s" % (
        d_left, d_right, judge_strip, next_strip, c_coach(d_coach, cls="coach-strip"), trend_block, pattern_block), "饮食 · 今日吃饭小日记 🍱", cls="dietcard span2")
    cal_today = ("<div class='body-t' style='margin-bottom:12px'>%s</div>" % esc(ca["today_line"])) if ca["today_line"] else ""
    c_calendar = c_card(cal_today + cal_html(ca) + c_coach(ca["expl"], "trim-wide"), "活动日历 · %d 年 %d 月" % (ca["year"], ca["month"]), cls="grow")
    c_bodycomp = c_card("<div class='body-t'>%s</div>%s%s" % (
        esc(bo["summary"]), body_chart, c_coach(bo["coach"], "trim-wide")), "身体成分", cls="chartgrow")
    # 右下角位:周日=本周小结(每周复盘一次),平时=随机夸夸/小课堂
    if D.get("is_sunday"):
        c_supp = c_card("<div class='body-t'>%s</div>" % esc(recap_txt), "本周小结 · 周日复盘")
    else:
        c_supp = c_card(supp_html(supp), cls=("praise-card" if supp["kind"] == "praise" else ""))
    # Bento 两段式·全量一页(≥1360px 一屏透底,窄屏退化单列):
    #  R1 第一眼:英雄卡 | 里程碑(含速度点评)
    #  R2 三栏:[体重趋势+身体成分] [饮食(含长期趋势点评)] [日历+夸夸/周日小结]
    band1 = "<div class='b1'>%s%s</div>" % (c_hero, c_milestone)
    band2 = ("<div class='b3'>"
             "<div class='bcell'>%s%s</div><div class='bcell narrowdiet'>%s</div><div class='bcell'>%s%s</div></div>"
             % (c_weight, c_bodycomp, c_diet, c_calendar, c_supp))
    body_html = band1 + band2

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
@media(prefers-reduced-motion:reduce){.cbcard{animation:none;transform:translate(-50%,-50%);opacity:1;}}
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
 // 尊重系统"减弱动态效果":不放烟花,只静态展示祝贺卡
 if(!matchMedia('(prefers-reduced-motion: reduce)').matches){frame();go();}
})();
</script>"""

TEMPLATE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>快乐减脂趋势看板</title>
<script src="./assets/chart.umd.min.js"></script>
<style>
:root{
 /* 色板 · 清新草绿终版(近白底大留白,绿只做正反馈点缀,见 docs/DESIGN-SYSTEM.md) */
 --bg:#FAFCFA;--card:#FFFFFF;--ink:#28382E;--t2:#5C7265;--t3:#74877B;--line:#EAF2EC;
 --accent:#16A34A;--accent-d:#15803D;--accent-l:#22C55E;--soft:#E7F5EC;--warn:#B5651D;
 --pos:#15803D;--pos-d:#15803D;--pos-soft:#E3F3E9;          /* 正向反馈:掉秤/达标/训练日 */
 --weak:#D7EDDE;--line2:#0D9488;--praise:#F1FAF4;
 --shadow:0 2px 12px rgba(70,60,45,.08);
 /* 状态色:日历格子 & 活动 chip —— 全部集中一处,杜绝撞色 */
 --cell:#F7FAF7;--cell-today:#FFFFFF;
 --chip-train:var(--pos-soft);--chip-train-t:var(--pos-d);
 --chip-cardio:#DFF1EE;--chip-cardio-t:#0B7468;
 --chip-rest:#EFF3EF;--chip-rest-t:var(--t3);
 --chip-other:#ECF1EC;--chip-other-t:var(--t2);
 /* 间距刻度 / 圆角(组件统一引用) */
 --s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;
 --r:16px;--r-sm:12px;
}
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.65 'Nunito Sans','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
 font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;letter-spacing:.005em;}
h1,.stat .v,.hero .answer,.sf-item b,.node .flag{font-family:'Varela Round','Nunito Sans','PingFang SC','Hiragino Sans GB',sans-serif;}
.wrap{max-width:540px;margin:0 auto;padding:30px 18px 80px;}
.card{background:var(--card);border-radius:var(--r);box-shadow:var(--shadow);padding:22px;margin-bottom:16px;
 animation:rise .5s cubic-bezier(.2,.7,.2,1) both;}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
@media(prefers-reduced-motion:reduce){.card{animation:none}}
/* 英雄卡:整页第一眼焦点(问句+答案+今日体重大数字) */
.hero .upd{font-size:11px;color:var(--t3);letter-spacing:.04em;margin-bottom:8px;}
.hero h1{font-size:21px;font-weight:700;letter-spacing:-.01em;margin:0;}
.hero .answer{font-size:28px;font-weight:700;color:var(--accent-d);margin:2px 0 16px;letter-spacing:-.01em;}
.msbar{position:relative;margin:56px 24px 4px;}
.track2{position:relative;height:8px;background:var(--weak);border-radius:8px;}
.fill2{position:absolute;left:0;top:0;height:100%;background:linear-gradient(90deg,var(--accent-l),var(--accent-d));border-radius:8px;}
.here{position:absolute;top:4px;width:13px;height:13px;border-radius:50%;background:#fff;border:3px solid var(--accent);box-shadow:0 1px 4px rgba(40,56,46,.22);transform:translate(-50%,-50%);z-index:4;}
.here b{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);font-size:11px;font-weight:700;color:var(--accent-d);white-space:nowrap;}
.node{position:absolute;top:4px;z-index:2;}
.node .pole{position:absolute;left:0;top:-22px;width:2px;height:22px;background:var(--weak);transform:translateX(-50%);border-radius:2px;}
.node .flag{position:absolute;left:0;top:-45px;transform:translateX(-50%);min-width:34px;height:25px;padding:0 9px;
 display:flex;align-items:baseline;justify-content:center;gap:1px;border-radius:8px;background:#fff;border:1.5px solid var(--weak);
 color:var(--t2);font-size:14px;font-weight:700;box-shadow:0 2px 5px rgba(40,56,46,.08);}
.node .flag i{font-style:normal;font-size:9px;font-weight:600;color:var(--t3);}
.node .nl{position:absolute;top:24px;left:0;transform:translateX(-50%);font-size:10.5px;color:var(--t3);white-space:nowrap;}
.node.done .flag{background:var(--pos);border-color:var(--pos);color:#fff;} .node.done .flag i{color:#D3E8DA;}
.node.done .pole{background:var(--pos);} .node.done .nl{color:var(--pos-d);}
.node.now .flag{border-color:var(--accent);border-width:2px;color:var(--accent-d);box-shadow:0 0 0 4px var(--soft),0 2px 5px rgba(40,56,46,.08);}
.node.now .flag i{color:var(--accent-d);} .node.now .pole{background:var(--accent);} .node.now .nl{color:var(--accent-d);font-weight:700;}
.node.start .flag{background:#F2F6F2;border-color:var(--weak);color:var(--t2);} .node.start .flag i{color:var(--t3);}
.node.start .pole{background:var(--weak);} .node.start .nl{color:var(--t3);}
/* 首尾节点贴边对齐，避免旗子/文字飘到卡片外 */
.node.start .flag,.node.start .nl{transform:translateX(0);left:-2px;}
.node.end .flag,.node.end .nl{transform:translateX(-100%);left:2px;}
@media(max-width:600px){
 .msbar{margin:52px 16px 4px;}
 .node .flag{min-width:30px;height:23px;padding:0 7px;font-size:13px;}
 .node .nl{font-size:10px;}
 .here b{font-size:10px;}
}
.tip-t{font-size:15px;font-weight:650;color:var(--ink);margin-bottom:5px;}
.tip-b{font-size:14.5px;line-height:1.7;color:var(--t2);}  /* 与 .body-t 同刻度:同级卡片正文必须同字号 */
.speed{margin-top:11px;font-size:13.5px;line-height:1.65;color:var(--t2);padding-left:12px;border-left:2px solid var(--soft);}
/* 里程碑进度条下的点评:给旗标下方标签让出空间;在大卡里属主要内容,字号用正文级(双类压过紧凑块覆写) */
.speed.msnote{margin-top:38px;font-size:14.5px;line-height:1.7;}
/* 字号刻度约定:正文级(body-t/tip-b/judge2)14.5 · 结论行(one)15 · 次级说明(coach/advice2/speed)13.5 · 提示(note)12.5 */
/* Bento 三段式(分享视图):默认(窄屏)容器透明化、卡片单列竖排;≥1360px 进入便当盒布局——
   R1 英雄卡|里程碑 → R2 趋势大图|夸夸+身体成分 → R3 饮食|7天趋势|日历+小结。
   各格末卡弹性补高 → 每段底边恒对齐,整页截图即一张层级清晰的分享宽图。 */
.b1,.b2,.b3,.bcell{display:contents;}
@media(min-width:880px){
 .wrap{max-width:1000px;}
 .span2 .stats{gap:48px;}
}
@media(min-width:1360px){
 .wrap{max-width:1680px;padding-left:28px;padding-right:28px;}
 /* b1 与 b3 共用同一三列栅格:英雄卡占第1列、里程碑跨第2-3列,上下列边界严丝合缝 */
 .b1{display:grid;grid-template-columns:29fr 45fr 26fr;gap:0 14px;align-items:stretch;}
 .b1>.hero{grid-column:1;} .b1>.card:nth-child(2){grid-column:2 / 4;}
 .b2{display:grid;grid-template-columns:6fr 6fr;gap:0 14px;align-items:stretch;}
 .b3{display:grid;grid-template-columns:29fr 45fr 26fr;gap:0 14px;align-items:stretch;}
 .bcell{display:flex;flex-direction:column;min-width:0;}
 .bcell>.card:last-child{flex:1;}
 /* .grow 卡(日历)吃掉该列富余高度,表格行随之等比撑开填满——保证三列底边对齐,且无中间白洞 */
 .bcell>.card.grow{flex:1;display:flex;flex-direction:column;}
 .bcell>.card.grow table.cal{flex:1;}
 .bcell:has(>.card.grow)>.card:last-child:not(.grow){flex:0 0 auto;}
 .trim-wide{display:none;}  /* 饮食卡弹性预算:一页视图下让位的低优先内容(手机滚动视图不受影响) */
 .narrowdiet{min-height:0;}
 .narrowdiet>.card.dietcard{min-height:0;overflow:hidden;}  /* 饮食卡锁到行高、超出裁切;由 fitDiet() 按实测高度决定收哪块 */
 .b1>.card{display:flex;flex-direction:column;justify-content:center;}
 .b1>.hero{justify-content:flex-start;}
 .hero h1{font-size:23px;}
 .hero .answer{font-size:31px;margin-bottom:8px;}
 .hero .stat .v{font-size:42px;}
 .hero .stat.ma .v{font-size:24px;}
 .cv{height:205px;}
 /* 窄格里的饮食卡:内部改单列(双列会挤破) */
 /* 饮食列加宽后内部恢复左右并排(餐清单|配额表),卡子高度减半 */
 .narrowdiet .diet-grid{grid-template-columns:1.05fr 1fr;gap:16px;}
 /* —— 一页视图密度:内容精炼后字号回大,层级靠英雄区撑,目标笔记本一屏透底 —— */
 .wrap{padding-top:16px;padding-bottom:24px;}
 .card{padding:15px 18px;margin-bottom:12px;}
 .mt{margin-bottom:11px;}  /* 字号见基础 .mt(在后,生效) */
 .coach{margin-top:10px;padding:8px 12px;font-size:15px;line-height:1.6;}
 .pstrip{margin-top:9px;font-size:15px;line-height:1.6;padding-left:12px;}
 .speed{margin-top:9px;font-size:13.5px;line-height:1.6;}
 .one{font-size:17px;}
 .stats{margin-bottom:11px;gap:26px;}
 .msbar{margin:50px 20px 2px;}
 .kv{margin-top:9px;} .kv .row{padding:5px 0;font-size:15px;}  /* 三行上下排,标签恒对齐 */
 .kv .k{font-size:13px;}
 .range{margin-bottom:5px;}
 .cv.sm{height:96px;}
 /* 同列多图均衡伸缩:带 chartgrow 的卡平分该格富余高度,各自图表随卡长高(谁也不独吞留白) */
 .bcell>.card.chartgrow{display:flex;flex-direction:column;flex:1 1 auto;}
 .card.chartgrow .cv{flex:1 1 auto;height:auto;min-height:84px;}
 .card.chartgrow .cv:not(.sm){min-height:185px;}
 .body-t{font-size:16px;line-height:1.62;}
 .tip-t{font-size:16.5px;margin-bottom:4px;} .tip-b{font-size:16px;line-height:1.62;}
 .pz-big{font-size:18.5px;line-height:1.55;margin-top:8px;}
 .judge2{margin-top:10px;font-size:14.5px;line-height:1.62;} .advice2{margin-top:8px;font-size:13.5px;line-height:1.58;}
 .diet-grid{gap:16px;}
 .diary{gap:8px;}
 .meal{padding:8px 11px;gap:10px;}
 .mtile{width:36px;height:36px;font-size:18px;border-radius:9px;} .mthumb{width:38px;height:38px;border-radius:9px;}
 .mn{margin-bottom:2px;font-size:12px;} .mfi{font-size:16px;} .mlist{line-height:1.5;} .mnut{margin-top:3px;font-size:12.5px;}
 .macro{margin-top:10px;padding:9px 12px;} .mr{padding:3px 0;} .mv{font-size:17px;} .mk{font-size:14.5px;}
 .ms{font-size:12.5px;}
 .diet-right{padding:12px 14px;} .sf-strip{gap:9px;} .sf-item b{font-size:21px;} .sf-item .sk{font-size:13.5px;}
 table.cal{border-spacing:3px;} table.cal td{height:32px;padding:3px 4px;border-radius:7px;}
 .dn{font-size:10px;} .chip{font-size:9.5px;padding:1.5px 5px;margin-top:2px;line-height:1.5;}
 .note{padding:9px 12px;font-size:12.5px;line-height:1.6;}
 .foot{margin-top:9px;}
}
.mt{font-size:17px;font-weight:700;color:var(--ink);letter-spacing:.01em;margin:0 0 16px;}
/* 今日状态 */
.stats{display:flex;align-items:flex-end;gap:30px;margin-bottom:14px;}
.stat .l{font-size:11px;color:var(--t3);letter-spacing:.03em;margin-bottom:8px;}
.stat .v{font-size:34px;font-weight:680;line-height:.9;letter-spacing:-.02em;}
.stat .v .u{font-size:13px;font-weight:500;color:var(--t3);margin-left:3px;}
.stat.ma .v{font-size:22px;font-weight:600;color:var(--accent);}
.stat.tr{margin-left:auto;text-align:right;}
.tag{display:inline-block;background:var(--pos);color:#fff;border-radius:99px;padding:4px 12px;font-size:12px;font-weight:600;}
.one{font-size:16.5px;line-height:1.55;color:var(--t2);}
/* 体重三行 */
.kv{margin-top:14px;}
.kv .row{display:flex;align-items:baseline;gap:14px;padding:9px 0;border-top:1px solid var(--line);font-size:14px;}
.kv .row:first-child{border-top:none;padding-top:2px;}
.kv .k{color:var(--t3);font-size:12px;letter-spacing:.04em;width:56px;flex:none;}
.cv{position:relative;height:220px;margin:6px 0 2px;} .cv.sm{height:150px;}
.range{display:flex;gap:8px;margin-bottom:8px;}
.range button{background:transparent;border:1px solid var(--line);color:var(--t3);border-radius:8px;padding:4px 12px;font-size:12px;cursor:pointer;
 transition:border-color .2s ease-out,color .2s ease-out;}
.range button:hover{border-color:var(--accent);color:var(--accent-d);}
.range button.on{background:var(--accent-d);color:#fff;border-color:var(--accent-d);}
.sub{font-size:11px;color:var(--t3);font-weight:600;letter-spacing:.13em;text-transform:uppercase;margin:20px 0 10px;}
/* 饮食日记 · 2×2 方格 */
.diet-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:stretch;}
/* 右侧"截至目前"拉伸填满列高,营养行均匀分布,底边与左侧餐卡齐平(不再上下留白) */
.diet-right{display:flex;flex-direction:column;}
.diet-right .sf-strip{flex:1;justify-content:space-between;}
@media(max-width:600px){.diet-grid{grid-template-columns:1fr;gap:16px;}}
.diary{display:flex;flex-direction:column;gap:10px;}
.meal{display:flex;gap:11px;align-items:flex-start;padding:12px;border:1px solid var(--line);border-radius:13px;background:#FBFDFB;}
.mvis{flex:none;}
.mtile{width:50px;height:50px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:25px;
 background:linear-gradient(135deg,#F2F8F3,#EAF4EC);border:1px solid #E2EEE5;}
.mthumb{width:54px;height:54px;border-radius:13px;object-fit:cover;border:1px solid var(--line);
 filter:saturate(1.05) contrast(1.02);display:block;}
.mbody{min-width:0;flex:1;}
.mn{font-size:11px;color:var(--t3);letter-spacing:.1em;text-transform:uppercase;font-weight:600;margin-bottom:4px;}
.mlist{line-height:1.6;}
.mfi{font-size:14px;font-weight:600;}
.sep{color:var(--t3);opacity:.55;margin:0 7px;font-weight:400;}
.mnut{font-size:11px;color:var(--t3);margin-top:5px;}
.meal.empty{background:#F7FAF7;} .meal.empty .mtile{opacity:.5;} .meal.empty .mbody{opacity:.6;}
.mf-empty{font-size:14px;color:var(--t3);}
.praise-card{background:var(--praise);border:1.5px solid var(--accent);position:relative;overflow:hidden;}
.praise-card .pz-deco{position:absolute;right:8px;top:-6px;font-size:50px;opacity:.22;transform:rotate(8deg);pointer-events:none;}
.pz-h{position:relative;font-size:11.5px;font-weight:800;letter-spacing:.1em;color:var(--accent-d);}
.pz-big{position:relative;font-size:17px;line-height:1.62;font-weight:650;color:#2C4A36;margin-top:10px;}
/* 三大营养素达标 */
.macro{margin-top:16px;border:1px solid var(--line);border-radius:12px;padding:13px 15px;background:#FBFDFB;}
.mttl{font-size:11px;color:var(--t3);letter-spacing:.06em;margin-bottom:9px;}
.mr{display:grid;grid-template-columns:48px 1fr auto;align-items:baseline;gap:10px;padding:5px 0;}
.mk{color:var(--t2);font-size:13px;}
.mv{font-size:16px;font-weight:650;letter-spacing:-.01em;} .mv .sl{font-size:12px;font-weight:400;color:var(--t3);margin-left:5px;}
.ms{font-size:12px;font-weight:600;justify-self:end;}
.ms.ok{color:var(--pos);} .ms.low{color:var(--t3);} .ms.warn{color:var(--warn);}
.note{padding:14px;background:#F7FAF7;border:1px dashed var(--weak);border-radius:12px;font-size:12.5px;color:var(--t3);line-height:1.7;}
.body-t{font-size:16px;line-height:1.7;color:var(--t2);}
.coach{margin-top:14px;background:#F6FAF7;border:1px solid #E4EFE7;border-radius:12px;padding:11px 14px;font-size:15px;line-height:1.72;color:var(--t2);}
.pstrip{margin-top:11px;font-size:15px;line-height:1.72;color:var(--t2);padding-left:14px;}  /* 这一餐/下一餐:无底色文字行,左缩进对齐点评框内文字 */
.ptag{font-weight:700;color:var(--ink);margin-right:7px;}  /* 小标题:仅加粗,不加底色 */
.coach-tag{display:inline-block;font-size:14px;font-weight:700;color:var(--accent-d);background:var(--pos-soft);padding:3px 10px;border-radius:7px;margin-right:8px;letter-spacing:.02em;}
/* 饮食 · 截至目前(横向铺平) */
.diet-right{padding:14px 16px;background:#FBFDFB;border:1px solid var(--line);border-radius:13px;}
.sf-h{font-size:11px;color:var(--t3);font-weight:600;letter-spacing:.13em;text-transform:uppercase;margin:0 0 12px;}
.sf-strip{display:flex;flex-direction:column;gap:13px;}
.sf-row{display:flex;flex-direction:column;gap:6px;}
.sf-item{display:flex;align-items:baseline;gap:5px;}
/* 碳蛋脂进度条:浅轨 + 目标区间两刻度 + 实际填充(按达标色) */
.sf-bar{position:relative;height:7px;background:#EAF2EC;border-radius:6px;}
.sf-fill{position:absolute;left:0;top:0;height:100%;border-radius:6px;background:var(--pos);transition:width .3s ease-out;}
.sf-fill.warn{background:var(--warn);}  /* 超上限才换色(橙·警示);未达下限沿用默认绿,靠刻度线+文字区分 */
.sf-tick{position:absolute;top:-2px;width:2px;height:11px;background:var(--accent-d);border-radius:1px;transform:translateX(-1px);opacity:.5;}
.sf-item .sk{font-size:12px;color:var(--t3);}
.sf-item b{font-size:21px;font-weight:700;letter-spacing:-.01em;}
.sf-item i{font-size:11px;color:var(--t3);font-style:normal;}
.sf-item em{font-size:11px;font-weight:600;font-style:normal;margin-left:1px;}
.sf-item em.ok{color:var(--pos);} .sf-item em.mut{color:var(--t3);} .sf-item em.warn{color:var(--warn);}
.judge2{margin-top:15px;font-size:14.5px;line-height:1.7;color:var(--ink);}
.advice2{margin-top:11px;font-size:13.5px;line-height:1.65;color:var(--t2);}
.advice2.full{background:#F6FAF7;border:1px solid #E4EFE7;border-radius:12px;padding:9px 14px;}  /* 下一餐:整行长条 */
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
Chart.defaults.font.family="'Nunito Sans','PingFang SC','Hiragino Sans GB',sans-serif";
function fd(m){return new Date(m).toISOString().slice(5,10);}
// 悬浮提示标题统一显示成"月-日"(否则会弹出原始时间戳那串大数字)
Chart.defaults.plugins.tooltip.callbacks=Chart.defaults.plugins.tooltip.callbacks||{};
Chart.defaults.plugins.tooltip.callbacks.title=function(its){return (its&&its.length)?fd(its[0].parsed.x):'';};
const GRID='#EAF2EC',TICK='#74877B';
const AX={type:'linear',ticks:{color:TICK,maxTicksLimit:6,font:{size:10},callback:v=>fd(v)},grid:{color:GRID,drawTicks:false},border:{display:false}};
function yax(o){return Object.assign({ticks:{color:TICK,font:{size:10}},grid:{color:GRID,drawTicks:false},border:{display:false}},o||{});}
const LEG={labels:{color:TICK,boxWidth:10,boxHeight:10,font:{size:10},usePointStyle:true,pointStyle:'circle'}};
let wc;
function drawWeight(lo,hi){
  const cfg={data:{datasets:[
    {type:'line',label:'目标',data:[{x:D.minx,y:D.target},{x:D.maxx,y:D.target}],borderColor:'#C2DCC9',borderDash:[5,5],borderWidth:1,pointRadius:0},
    {type:'scatter',label:'每日',data:D.weightDaily,pointRadius:2.2,pointBackgroundColor:'#D7EDDE',borderColor:'#C2DCC9'},
    {type:'line',label:'7 日均',data:D.ma7,borderColor:'#16A34A',borderWidth:2.5,pointRadius:0,tension:.3,fill:true,backgroundColor:'rgba(22,163,74,0.07)'},
  ]},options:{maintainAspectRatio:false,plugins:{legend:Object.assign({},LEG,{labels:Object.assign({},LEG.labels,{filter:i=>i.text!=='目标'})})},
    scales:{x:Object.assign({},AX,{min:lo,max:hi}),y:yax({})}}};
  if(wc)wc.destroy(); wc=new Chart(document.getElementById('cWeight'),cfg);
}
// 档位:近4周(默认)/近4月/全部;数据不够长的档位与「全部」重合,自动隐藏
const ranges=[['近 4 周',D.lo4w],['近 4 月',D.lo4m]].filter(r=>r[1]>D.minx);
ranges.push(['全部',D.minx]);
drawWeight(ranges[0][1],D.maxx);
const rd=document.getElementById('range');
ranges.forEach(([t,lo],i)=>{const b=document.createElement('button');b.textContent=t;if(i===0)b.classList.add('on');
  b.onclick=()=>{[...rd.children].forEach(c=>c.classList.remove('on'));b.classList.add('on');drawWeight(lo,D.maxx);};rd.appendChild(b);});
// 饮食长期趋势已改为文字点评(v2.1),不再画 7 天小图
if(D.showBody && D.fat.length){
  new Chart(document.getElementById('cBody'),{data:{datasets:[
    {type:'line',label:'瘦体重 kg',data:D.lean,borderColor:'#0D9488',borderWidth:2.5,pointRadius:0,yAxisID:'y',borderDash:[6,4],tension:.3},
    {type:'line',label:'脂肪量 kg',data:D.fat,borderColor:'#15803D',borderWidth:2.5,pointRadius:1.6,pointBackgroundColor:'#15803D',yAxisID:'y1',tension:.3},
  ]},options:{maintainAspectRatio:false,plugins:{legend:LEG},scales:{x:AX,
    y:yax({position:'left',grace:'12%'}),
    y1:yax({position:'right',grace:'12%',grid:{drawOnChartArea:false}})}}});
}
// 一屏自适应:宽屏下内容比窗口高就整体等比缩小,打开即见全部、零滚动(窄屏/手机不缩)
function cbFit(){
  if(innerWidth<1360){document.body.style.zoom='';return;}
  document.body.style.zoom='';
  var h=document.documentElement.scrollHeight;
  var z=Math.max(0.72,Math.min(1,innerHeight/h));
  document.body.style.zoom=(z<1)?z:'';
}
// 饮食卡内容超出行高时,先收「松松点评」、再收「下一餐」(渲染后实测高度,替代餐数估算)
function fitDiet(){
  var card=document.querySelector('.card.dietcard'); if(!card) return;
  var coach=card.querySelector('.coach-strip'), next=card.querySelector('.next-strip');
  if(coach)coach.style.display=''; if(next)next.style.display='';
  if(innerWidth<1360) return;
  if(card.scrollHeight>card.clientHeight+1 && coach) coach.style.display='none';
  if(card.scrollHeight>card.clientHeight+1 && next) next.style.display='none';
}
function cbAll(){ document.body.style.zoom=''; fitDiet(); cbFit(); }
addEventListener('resize',function(){clearTimeout(window.__cbft);window.__cbft=setTimeout(cbAll,120);});
addEventListener('load',cbAll);
cbAll();
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
