# -*- coding: utf-8 -*-
"""每日快照主流程：抓取 → 信号灯 → 落历史 → 生成静态页。

用法：.venv/bin/python main.py
单源抓取失败时降级为最近一次历史读数并标"数据陈旧"，不阻塞其他指标。
"""

import argparse
import glob
import json
import os
import re
import time
from datetime import datetime

import yaml
import pandas as pd

import cards
import generate_site
import portfolio
import signals
from fetchers import sources

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_with_retry(fn, retries):
    last = None
    for _ in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise last


SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def latest_history(history_dir):
    """返回最近一次历史快照 dict（按文件名日期倒序），没有则 None。

    只认 YYYY-MM-DD.json，避免 fx_cache.json 之类的缓存文件被当成快照。
    """
    files = sorted((p for p in glob.glob(os.path.join(history_dir, "*.json"))
                    if SNAPSHOT_RE.match(os.path.basename(p))), reverse=True)
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            continue
    return None


def metric_value(card_cfg, index_data):
    return index_data[card_cfg["metric"]]


# ---- 宏观/避险层 ----

def percentile_info(points, current, window_years=10):
    """分位信息：当前值在全历史与近 window_years 年中所处分位 + 窗口区间。

    points: [(date_str, value)...] 升序；current: 当前值。返回 dict 或 None。
    """
    vals = [v for _, v in points]
    if len(vals) < 100 or current is None:
        return None
    pct_all = sum(1 for v in vals if v <= current) / len(vals) * 100
    last_d = pd.Timestamp(points[-1][0][:10])
    cutoff = last_d - pd.Timedelta(days=365 * window_years)
    win = [v for d, v in points if pd.Timestamp(d[:10]) >= cutoff]
    if len(win) < 50:
        win = vals
    pct_win = sum(1 for v in win if v <= current) / len(win) * 100

    def _disp(p):
        # 有值高于当前时不允许四舍五入成 100%（会误导）
        r = round(p)
        return min(r, 99) if p < 100 else 100

    return {"pct_all": _disp(pct_all), "pct_win": _disp(pct_win),
            "win_min": round(min(win), 2), "win_max": round(max(win), 2),
            "window_years": window_years}


def percentile_text(info, unit=""):
    """如：近10年分位 42%（区间 62–126）· 全历史分位 38%"""
    if not info:
        return ""
    lo, hi = info["win_min"], info["win_max"]
    return (f"近{info['window_years']}年分位 {info['pct_win']}%（区间 {lo:g}–{hi:g}{unit}）"
            f" · 全历史分位 {info['pct_all']}%")


MACRO_FETCHERS = [
    (sources.fetch_gold_cny, ["gold_cny"]),
    (sources.fetch_gold_silver_usd, ["gold_usd", "silver_usd"]),
    (sources.fetch_tips, ["tips"]),
    (sources.fetch_fx_macro, ["usd_cny", "jpy_cny"]),
]

MACRO_LABELS = {
    "gold_cny": "黄金（人民币）",
    "gold_usd": "黄金（国际）",
    "gs_ratio": "金银比",
    "tips": "美国10Y TIPS",
    "usd_cny": "USD/CNY",
    "jpy_cny": "JPY/CNY",
}


def fetch_macro(hist_macro, retries):
    """抓取宏观指标，逐 key 降级到历史读数。返回 {key: {value, date, stale}}。"""
    macro = {}
    for fn, keys in MACRO_FETCHERS:
        try:
            got = fetch_with_retry(fn, retries)
            for k in keys:
                macro[k] = {**got[k], "stale": False}
        except Exception as e:  # noqa: BLE001
            for k in keys:
                old = hist_macro.get(k)
                if old:
                    macro[k] = {"value": old["value"], "date": old["date"], "stale": True}
                else:
                    macro[k] = {"value": None, "date": "—", "stale": True}
            print(f"[warn] 宏观抓取失败（{keys}），已降级: {type(e).__name__}: {str(e)[:100]}")
    return macro


def build_macro_view(macro, macro_cfg, pct_infos=None):
    """把宏观读数组装成避险/宏观表的展示行（黄金价格行）。

    金银比 / TIPS 升级为独立指标卡（build_macro_indicator_cards）。
    汇率（usd_cny/jpy_cny）只进历史、不上页面——组合模块本地折算仍在用 fetcher。
    """
    rows = []

    def add(key, value_text, note=""):
        m = macro.get(key)
        if not m or m["value"] is None:
            return
        rows.append({"label": MACRO_LABELS[key], "value_text": value_text,
                     "date": m["date"], "stale": m["stale"], "note": note,
                     "highlight": False, "sub": ""})

    add("gold_cny", f"{macro['gold_cny']['value']:,.1f} 元/g" if macro.get("gold_cny", {}).get("value") else "",
        note="上金所基准价，更新有延迟")
    add("gold_usd", f"${macro['gold_usd']['value']:,.2f}" if macro.get("gold_usd", {}).get("value") else "")
    return rows


def recent_series(series, days):
    """日线序列取近 days 个自然日：[(date_str, value)...]。"""
    if not series:
        return []
    cut = pd.Timestamp(series[-1][0][:10]) - pd.Timedelta(days=days)
    return [(d, v) for d, v in series if pd.Timestamp(d[:10]) >= cut]


def build_macro_indicator_cards(macro, macro_cfg, pct_infos, series_map, trend_days):
    """金银比 / TIPS 指标卡：区位灯 + 距离 + 门槛 + 近 N 天走势（日线序列）。"""
    cards = []

    gs = macro.get("gs_ratio", {}).get("value")
    if gs is not None:
        alert = macro_cfg["gold_silver_ratio_alert"]
        z = signals.eval_macro_zone(gs, alert, "白银低估区 · 市场恐慌", "常态区")
        dist = (f"已在 🟢白银低估区（金银比≥{alert:g}）" if z["distance"] is None
                else f"距 🟢白银低估区（金银比≥{alert:g}）还差 {z['distance']:g}")
        pts = recent_series(series_map.get("gs_ratio"), trend_days)
        ths = [(alert, "#2ba471", f"🟢{alert:g}")]
        cards.append({
            "id": "gs_ratio", "name": "金银比（金/银）", "metric_label": "伦敦金 ÷ 伦敦银",
            "value_text": f"{gs:.1f}",
            "spread_text": percentile_text(pct_infos.get("gs_ratio")),
            "zone": z["zone"], "zone_text": z["zone_text"], "distance_text": dist,
            "threshold_text": f"门槛（金银比）：🟢 ≥{alert:g} 白银低估 · 市场恐慌　🟡 <{alert:g} 常态",
            "trend_svg": generate_site.sparkline(pts, thresholds=ths),
            "_points": pts, "_spark_thresholds": ths,
            "data_date": macro["gs_ratio"]["date"], "stale": macro["gs_ratio"]["stale"], "note": "",
        })

    t = macro.get("tips", {}).get("value")
    if t is not None:
        alert = macro_cfg["tips_alert"]
        z = signals.eval_macro_zone(t, alert, "黄金底部区 · 高息环境", "常态区")
        dist = (f"已在 🟢黄金底部区（TIPS≥{alert:g}%）" if z["distance"] is None
                else f"距 🟢黄金底部区（TIPS≥{alert:g}%）还差 {z['distance']:g}pp")
        pts = recent_series(series_map.get("tips"), trend_days)
        ths = [(alert, "#2ba471", f"🟢{alert:g}%")]
        cards.append({
            "id": "tips", "name": "美国10Y TIPS", "metric_label": "实际利率（DFII10）",
            "value_text": f"{t:.2f}%",
            "spread_text": percentile_text(pct_infos.get("tips"), unit="%"),
            "zone": z["zone"], "zone_text": z["zone_text"], "distance_text": dist,
            "threshold_text": f"门槛（10Y TIPS）：🟢 ≥{alert:g}% 黄金底部区 · 高息环境　🟡 <{alert:g}% 常态",
            "trend_svg": generate_site.sparkline(pts, thresholds=ths),
            "_points": pts, "_spark_thresholds": ths,
            "data_date": macro["tips"]["date"], "stale": macro["tips"]["stale"], "note": "",
        })
    return cards


# ---- 历史趋势 ----

def load_history_series(history_dir, days):
    """按日期升序返回最近 days 天的快照列表 [(date_str, snapshot_dict)...]。"""
    files = sorted(p for p in glob.glob(os.path.join(history_dir, "*.json"))
                   if SNAPSHOT_RE.match(os.path.basename(p)))[-days:]
    out = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                out.append((os.path.basename(path)[:10], json.load(f)))
        except Exception:  # noqa: BLE001
            continue
    return out


def build_trends(history, card_cfgs, trend_days):
    """从历史上凑趋势序列：10Y、各息差卡的息差、PE 卡的 PE。

    返回 {"anchor": [(d, v)], card_id: [(d, v)]}。
    """
    anchor_pts = []
    card_pts = {c["id"]: [] for c in card_cfgs}
    for d, snap in history:
        av = snap.get("anchor", {}).get("value")
        if av is not None:
            anchor_pts.append((d, av))
        cards = snap.get("cards", {})
        for c in card_cfgs:
            rec = cards.get(c["id"], {})
            allv = rec.get("all")
            if not allv or av is None:
                continue
            if c["type"] == "spread":
                v = allv.get(c["metric"])
                if v is not None:
                    card_pts[c["id"]].append((d, round(v - av, 3)))
            else:
                v = allv.get(c["metric"])
                if v is not None:
                    card_pts[c["id"]].append((d, v))
    return {"anchor": anchor_pts, **card_pts}


def trend_thresholds(card_cfg):
    """各卡片趋势图叠加的虚线门槛（息差卡用息差口径，PE 卡用 PE 口径）。"""
    if card_cfg["type"] == "spread":
        return [
            (card_cfg["red_below"], "#e5484d", f"🔴{card_cfg['red_below']:g}"),
            (card_cfg["green_at"], "#2ba471", f"🟢{card_cfg['green_at']:g}"),
            (card_cfg["green2_at"], "#0e8a5f", f"🟢🟢{card_cfg['green2_at']:g}"),
        ]
    return [
        (card_cfg["red_at"], "#e5484d", f"🔴{card_cfg['red_at']:g}"),
        (card_cfg["green_at"], "#2ba471", f"🟢{card_cfg['green_at']:g}"),
        (card_cfg["green2_at"], "#0e8a5f", f"🟢🟢{card_cfg['green2_at']:g}"),
    ]


def build_card_view(card_cfg, reading, anchor_value, distortion, distortion_months):
    """把一张卡片的读数 + 配置算成页面展示 dict。"""
    m = card_cfg["metric"]
    value = reading["value"]
    out = {
        "name": card_cfg["name"],
        "metric_label": card_cfg["metric_label"],
        "data_date": reading["date"],
        "stale": reading["stale"],
        "note": card_cfg.get("note", ""),
    }

    if card_cfg["type"] == "spread":
        r = signals.eval_spread_card(card_cfg, value, anchor_value)
        zone, clearout = r["zone"], r["clearout"]
        out["value_text"] = f"{value:.2f}%"
        out["spread_text"] = f"息差 = {value:.2f}% − {anchor_value:.2f}% = {r['spread']:.2f}%"
        t = r["thresholds_cfg"]
        a = r["thresholds_abs"]
        out["threshold_text"] = (
            f"门槛（股息率 = 10Y {anchor_value:.2f}% + X）："
            f"🔴 <{a['red_below']:.2f}%（X={t['red_below']}） "
            f"🟡 {a['red_below']:.2f}–{a['green_at']:.2f}% "
            f"🟢 ≥{a['green_at']:.2f}%（X={t['green_at']}） "
            f"🟢🟢 ≥{a['green2_at']:.2f}%（X={t['green2_at']}）"
        )
        d = r["distance_next"]
        if d is None:
            out["distance_text"] = "已在最高档（🟢🟢 重仓区）"
        else:
            nxt = {"red": f"🟡黄灯（息差≥{t['red_below']}%）",
                   "yellow": f"🟢击球区（息差≥{t['green_at']}%）",
                   "green": f"🟢🟢重仓区（息差≥{t['green2_at']}%）"}[zone]
            out["distance_text"] = f"距 {nxt} 还差 {d:.2f}pp"
    else:  # pe
        r = signals.eval_pe_card(card_cfg, value)
        zone, clearout = r["zone"], r["clearout"]
        out["value_text"] = f"{value:.2f}"
        out["spread_text"] = ""
        t = r["thresholds_cfg"]
        out["threshold_text"] = (
            f"门槛（PE-TTM）：🔴 ≥{t['red_at']} "
            f"🟡 {t['green_at']}–{t['red_at']} "
            f"🟢 {t['green2_at']}–{t['green_at']} 🟢🟢 ≤{t['green2_at']}"
        )
        d = r["distance_next"]
        if d is None:
            out["distance_text"] = "已在最高档（🟢🟢 重仓区）"
        else:
            nxt = {"red": f"🟡黄灯（PE<{t['red_at']}）",
                   "yellow": f"🟢击球区（PE≤{t['green_at']}）",
                   "green": f"🟢🟢重仓区（PE≤{t['green2_at']}）"}[zone]
            out["distance_text"] = f"距 {nxt} 还需降 {d:.2f}"

    if distortion:
        zone_text = f"⬜ 失真期（{distortion_months[0]}–{distortion_months[-1]}月）· 只记录不触发"
        zone = "gray"
    else:
        zone_text = signals.ZONE_LABELS[zone]
        if clearout:
            zone_text += "　⚠️ 清仓线"
    out["zone"] = zone
    out["zone_text"] = zone_text
    out["clearout"] = clearout and not distortion
    return out


def social_card_specs(page_data, anchor, trends, ahr_pts, cfg):
    """页面各信息块 → 社交卡片 PNG 的绘制规格（cards.export 消费）。"""
    Z = generate_site.ZONE_COLORS

    def plain_rows(rows):
        out = []
        for r in rows:
            m = r.get("m") or {}
            v = m.get("value")
            val = r["fmt"](v) if v is not None else "—"
            meta = f"数据日期 {m.get('date', '—')}"
            if m.get("stale"):
                meta += "　⚠ 数据陈旧"
            out.append({"label": r["label"], "value": val, "meta": meta})
        return out

    def meta_of(date, stale, note=""):
        m = f"数据日期 {date}"
        if stale:
            m += "　⚠ 数据陈旧"
        if note:
            m += f"　{note}"
        return m

    def indicator(id_, title, view, points=(), ths=(), rows=None, note=None):
        return {
            "id": id_, "kind": "indicator", "title": title,
            "metric_label": view.get("metric_label", ""), "value_text": view["value_text"],
            "sub_text": view.get("spread_text", ""), "zone_color": Z[view["zone"]],
            "zone_text": view["zone_text"], "distance_text": view.get("distance_text", ""),
            "threshold_text": view.get("threshold_text", ""),
            "points": points, "spark_thresholds": list(ths),
            "rows": rows or [],
            "date": view.get("data_date") or view.get("date", "—"),
            "stale": view.get("stale", False),
            "note": view.get("note", "") if note is None else note,
        }

    specs = [{
        "id": "anchor", "kind": "anchor", "title": "中国10Y国债收益率",
        "value_text": page_data["anchor"]["value_text"],
        "points": trends["anchor"], "date": anchor["date"], "stale": anchor["stale"],
    }]

    for v in page_data["cards"]:
        specs.append(indicator(v["id"], v["name"], v,
                               points=v.get("_points", []), ths=v.get("_spark_thresholds", [])))

    specs.append({
        "id": "macro", "kind": "table", "title": "避险 / 宏观",
        "rows": [{"label": r["label"], "value": r["value_text"],
                  "meta": meta_of(r["date"], r["stale"], r.get("note", ""))}
                 for r in page_data["macro"]["rows"]],
        "date": page_data["generated_at"][:10], "stale": False,
    })

    for v in page_data["macro_cards"]:
        specs.append(indicator(v["id"], v["name"], v,
                               points=v.get("_points", []), ths=v.get("_spark_thresholds", [])))

    cc = cfg["crypto"]
    ahr_view = {"metric_label": "Ahr999 指数",
                "value_text": page_data["crypto"]["ahr"]["value_text"],
                "zone": page_data["crypto"]["ahr"]["zone"],
                "zone_text": page_data["crypto"]["ahr"]["zone_text"],
                "threshold_text": (f"门槛（Ahr999）：🟢🟢 <{cc['ahr999_bottom']:g} 抄底区　"
                                   f"🟡 {cc['ahr999_bottom']:g}–{cc['ahr999_top']:g} 定投区　"
                                   f"🔴 >{cc['ahr999_top']:g}"),
                "data_date": page_data["crypto"]["ahr"]["date"],
                "stale": page_data["crypto"]["ahr"]["stale"],
                "note": page_data["crypto"]["ahr"]["note"]}
    specs.append(indicator(
        "crypto", "加密（冻结层）", ahr_view,
        points=ahr_pts,
        ths=[(cc["ahr999_bottom"], "#2ba471", f"抄底{cc['ahr999_bottom']:g}"),
             (cc["ahr999_top"], "#e5484d", f"上限{cc['ahr999_top']:g}")],
        rows=plain_rows(page_data["crypto"]["rows"]),
    ))

    ic = cfg["im"]
    im_view = {"value_text": page_data["im"]["ann_text"],
               "zone": page_data["im"]["status_zone"],
               "zone_text": page_data["im"]["status_text"],
               "date": page_data["im"]["date"],
               "stale": page_data["im"]["stale"],
               "note": "",
               "threshold_text": (f"门槛（IM 年化贴水）：<{ic['record_below']:g}% 不记录　"
                                  f"{ic['record_below']:g}–{ic['alert_above']:g}% 正常　>{ic['alert_above']:g}% 关注窗口")}
    specs.append(indicator("im", "IM 贴水监控（月频）", im_view,
                           rows=plain_rows(page_data["im"]["rows"])))
    return specs


def main(local=False):
    cfg = load_config()
    history_dir = cfg["history_dir"]
    retries = cfg["fetch"]["retries"]
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    distortion = now.month in cfg["distortion_months"]

    hist = latest_history(history_dir) or {}
    hist_anchor = hist.get("anchor")
    hist_cards = hist.get("cards", {})
    hist_macro = hist.get("macro", {})

    # ---- 锚：10Y 国债
    try:
        anchor = fetch_with_retry(sources.fetch_anchor, retries)
        anchor["stale"] = False
    except Exception as e:  # noqa: BLE001
        if not hist_anchor:
            raise RuntimeError(f"10Y 抓取失败且无历史可降级: {e}")
        anchor = {"value": hist_anchor["value"], "date": hist_anchor["date"], "stale": True}
        print(f"[warn] 10Y 抓取失败，降级到 {anchor['date']}: {e}")

    # ---- 各卡片：指数估值
    snapshot_cards = {}
    view_cards = []
    for card_cfg in cfg["cards"]:
        cid = card_cfg["id"]
        try:
            data = fetch_with_retry(lambda c=card_cfg: sources.fetch_index_value(c["index_code"]), retries)
            reading = {"value": metric_value(card_cfg, data), "date": data["date"], "stale": False}
            # 顺带把全部口径存进历史，方便日后切换口径
            reading["all"] = data
        except Exception as e:  # noqa: BLE001
            old = hist_cards.get(cid)
            if not old:
                print(f"[warn] {cid} 抓取失败且无历史，跳过: {e}")
                continue
            reading = {"value": old["value"], "date": old["date"], "stale": True}
            print(f"[warn] {cid} 抓取失败，降级到 {old['date']}: {e}")
        snapshot_cards[cid] = reading
        view = build_card_view(card_cfg, reading, anchor["value"],
                               distortion, cfg["distortion_months"])
        view["id"] = cid
        view_cards.append(view)

    # ---- 宏观/避险层
    macro = fetch_macro(hist_macro, retries)

    # 金银比自算（金 ÷ 银），供指标卡与分位使用
    g = macro.get("gold_usd", {}).get("value")
    s = macro.get("silver_usd", {}).get("value")
    if g and s:
        macro["gs_ratio"] = {"value": round(g / s, 1),
                             "date": macro["gold_usd"]["date"],
                             "stale": macro["gold_usd"]["stale"] or macro["silver_usd"]["stale"]}

    # 金银比 / TIPS 历史分位（全历史 + 近 N 年；失败则该指标分位留空，不影响页面）
    win_years = cfg["macro"].get("percentile_window_years", 10)
    pct_infos = {}
    macro_series = {}
    for key, fn, cur in [("gs_ratio", sources.fetch_gs_ratio_series, macro.get("gs_ratio", {}).get("value")),
                         ("tips", sources.fetch_dfii10_series, macro.get("tips", {}).get("value"))]:
        try:
            series = fetch_with_retry(fn, 1)
            macro_series[key] = series
            pct_infos[key] = percentile_info(series, cur, win_years)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {key} 历史分位计算失败，本次不显示分位: {type(e).__name__}: {str(e)[:100]}")

    trend_days = cfg.get("trend", {}).get("days", 90)
    macro_rows = build_macro_view(macro, cfg["macro"], pct_infos)
    macro_cards = build_macro_indicator_cards(macro, cfg["macro"], pct_infos, macro_series, trend_days)

    # ---- 加密冻结层
    crypto = fetch_group(
        [(sources.fetch_crypto_prices, ["btc_usd", "eth_usd"]),
         (sources.fetch_ahr999, ["ahr999"]),
         (sources.fetch_rwa, ["susds_apy", "sdai_apy", "usdy_apy"])],
        hist.get("crypto", {}), retries, "加密")

    # ---- IM 贴水（月频）
    im = fetch_group(
        [(lambda: sources.fetch_im(cfg["im"]), ["im_spot", "im_fut", "im_discount_ann"]),
         (lambda: sources.fetch_csi1000_pe(cfg["im"]), ["csi1000_pe", "csi1000_pe_pct5"])],
        hist.get("im", {}), retries, "IM")

    # ---- 落历史
    os.makedirs(history_dir, exist_ok=True)
    snapshot = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": anchor,
        "cards": snapshot_cards,
        "macro": macro,
        "crypto": crypto,
        "im": im,
    }
    with open(os.path.join(history_dir, f"{today}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # ---- 趋势（含今天刚落的快照）
    history = load_history_series(history_dir, trend_days)
    trends = build_trends(history, cfg["cards"], trend_days)
    anchor_trend = generate_site.sparkline(trends["anchor"])
    views_by_id = {v["id"]: v for v in view_cards}
    for card_cfg in cfg["cards"]:
        v = views_by_id.get(card_cfg["id"])
        if not v:
            continue
        pts = trends.get(card_cfg["id"], [])
        ths = trend_thresholds(card_cfg)
        v["_points"], v["_spark_thresholds"] = pts, ths
        v["trend_svg"] = generate_site.sparkline(pts, thresholds=ths)

    # ahr999 趋势（定投区判定核心指标）
    ahr_pts = [(d, s["crypto"]["ahr999"]["value"]) for d, s in history
               if s.get("crypto", {}).get("ahr999", {}).get("value") is not None]
    cc = cfg["crypto"]
    ahr_trend = generate_site.sparkline(
        ahr_pts,
        thresholds=[(cc["ahr999_bottom"], "#2ba471", f"抄底{cc['ahr999_bottom']:g}"),
                    (cc["ahr999_top"], "#e5484d", f"上限{cc['ahr999_top']:g}")])

    # ---- 加密 / IM 视图
    ahr = crypto.get("ahr999", {})
    ahr_v = ahr.get("value")
    ahr_zone, ahr_text = ahr999_zone(ahr_v, cc) if ahr_v is not None else ("gray", "无数据")
    crypto_view = {
        "rows": [
            {"label": "BTC", "m": crypto.get("btc_usd"), "fmt": lambda v: f"${v:,.0f}"},
            {"label": "ETH", "m": crypto.get("eth_usd"), "fmt": lambda v: f"${v:,.2f}"},
            {"label": "sUSDS APY", "m": crypto.get("susds_apy"), "fmt": lambda v: f"{v:.2f}%"},
            {"label": "sDAI APY", "m": crypto.get("sdai_apy"), "fmt": lambda v: f"{v:.2f}%"},
            {"label": "USDY APY", "m": crypto.get("usdy_apy"), "fmt": lambda v: f"{v:.2f}%"},
        ],
        "ahr": {"value_text": f"{ahr_v:.4f}" if ahr_v is not None else "—",
                "zone": ahr_zone, "zone_text": ahr_text,
                "date": ahr.get("date", "—"), "stale": ahr.get("stale", True),
                "trend_svg": ahr_trend,
                "note": "数据源：9992100.xyz（第三方免费）"},
    }

    ic = cfg["im"]
    ann = im.get("im_discount_ann", {}).get("value")
    im_zone, im_text = im_status(ann, ic)
    fut = im.get("im_fut", {})
    pe, pct5 = im.get("csi1000_pe", {}), im.get("csi1000_pe_pct5", {})
    im_view = {
        "rows": [
            {"label": "中证1000 现货", "m": im.get("im_spot"), "fmt": lambda v: f"{v:,.0f}"},
            {"label": f"远季合约 {fut.get('code', '')}", "m": fut, "fmt": lambda v: f"{v:,.1f}"},
            {"label": "中证1000 PE-TTM", "m": pe, "fmt": lambda v: f"{v:.2f}"},
            {"label": "PE 五年分位", "m": pct5, "fmt": lambda v: f"{v:.0f}%"},
        ],
        "ann_text": f"{ann:.1f}%" if ann is not None else "—",
        "status_zone": im_zone, "status_text": im_text,
        "date": fut.get("date", "—"), "stale": im.get("im_discount_ann", {}).get("stale", True),
    }

    # ---- 生成页面
    page_data = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": {"value_text": f"{anchor['value']:.2f}%", "date": anchor["date"],
                   "stale": anchor["stale"], "trend_svg": anchor_trend},
        "distortion": distortion,
        "distortion_months": cfg["distortion_months"],
        "cards": view_cards,
        "macro": {"rows": macro_rows},
        "macro_cards": macro_cards,
        "crypto": crypto_view,
        "im": im_view,
    }
    out_path = os.path.join(cfg["site_dir"], "index.html")
    generate_site.render(page_data, out_path)
    print(f"done. snapshot -> {history_dir}/{today}.json, page -> {out_path}")

    # ---- 社交卡片 PNG（site/cards/，页面"下载卡片"按钮指向这里）
    specs = social_card_specs(page_data, anchor, trends, ahr_pts, cfg)
    written = cards.export(specs, os.path.join(cfg["site_dir"], "cards"))
    if written:
        print(f"cards -> {len(written)} 张 PNG @ {cfg['site_dir']}/cards/")
    long_path = cards.export_long(specs, os.path.join(cfg["site_dir"], "cards", "long.png"),
                                  page_data["generated_at"])
    if long_path:
        print(f"long -> {long_path}")

    # ---- 本地组合页（含持仓，仅在 --local 时生成，输出到 local/，绝不上传）
    if local:
        local_html, _ = portfolio.build_section(cfg)
        local_path = os.path.join(cfg["portfolio"]["local_dir"], "index.html")
        generate_site.render(page_data, local_path, extra_html=local_html,
                             card_href_prefix="../site/cards/")
        print(f"local page -> {local_path}（含持仓，仅本地，不上传）")


# ---- 加密冻结层 & IM（阶段 4） ----

def fetch_group(specs, hist_section, retries, label):
    """通用分组抓取：specs = [(fn, [key...])]，失败逐 key 降级历史。"""
    out = {}
    for fn, keys in specs:
        try:
            got = fetch_with_retry(fn, retries)
            for k in keys:
                v = dict(got[k])
                v["stale"] = False
                out[k] = v
        except Exception as e:  # noqa: BLE001
            for k in keys:
                old = hist_section.get(k)
                if old and old.get("value") is not None:
                    out[k] = {**old, "stale": True}
                else:
                    out[k] = {"value": None, "date": "—", "stale": True}
            print(f"[warn] {label}抓取失败（{keys}），已降级: {type(e).__name__}: {str(e)[:100]}")
    return out


def ahr999_zone(value, cc):
    """Ahr999 分区：<0.45 抄底区｜0.45–1.2 定投区｜>1.2 超出定投区。"""
    if value < cc["ahr999_bottom"]:
        return "green", f"抄底区 · 可大额买入（<{cc['ahr999_bottom']:g}）"
    if value <= cc["ahr999_top"]:
        return "yellow", f"定投区（{cc['ahr999_bottom']:g}–{cc['ahr999_top']:g}）"
    return "red", f"超出定投区（>{cc['ahr999_top']:g}）"


def im_status(ann, ic):
    """IM 年化贴水状态：<6% 灰（不记录）｜6–10% 正常｜>10% 高亮关注窗口。"""
    if ann is None:
        return "gray", "无数据"
    if ann < ic["record_below"]:
        return "gray", f"贴水 {ann:.1f}% < {ic['record_below']:g}%，不记录"
    if ann > ic["alert_above"]:
        return "red", f"年化贴水 {ann:.1f}% > {ic['alert_above']:g}%：关注窗口（需同时缩量企稳，见笔记 01 篇）"
    return "yellow", f"年化贴水 {ann:.1f}%，正常区间"


def cli():
    parser = argparse.ArgumentParser(description="每日快照：抓取 → 信号灯 → 静态页")
    parser.add_argument("--local", action="store_true",
                        help="额外生成含持仓的本地组合页 local/index.html（数据不出本机）")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    main(local=args.local)
