# -*- coding: utf-8 -*-
"""每日快照主流程：抓取 → 信号灯 → 落历史 → 生成静态页。

用法：.venv/bin/python main.py
单源抓取失败时降级为最近一次历史读数并标"数据陈旧"，不阻塞其他指标。
"""

import argparse
import glob
import json
import os
import time
from datetime import datetime

import yaml

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


def latest_history(history_dir):
    """返回最近一次历史快照 dict（按文件名日期倒序），没有则 None。"""
    files = sorted(glob.glob(os.path.join(history_dir, "*.json")), reverse=True)
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


def build_macro_view(macro, macro_cfg):
    """把宏观读数组装成展示行（含金银比自算与笔记阈值标注）。"""
    rows = []

    def add(key, value_text, note="", highlight=False):
        m = macro.get(key)
        if not m or m["value"] is None:
            return
        rows.append({"label": MACRO_LABELS[key], "value_text": value_text,
                     "date": m["date"], "stale": m["stale"], "note": note,
                     "highlight": highlight})

    add("gold_cny", f"{macro['gold_cny']['value']:,.1f} 元/g" if macro.get("gold_cny", {}).get("value") else "",
        note="上金所基准价，更新有延迟")
    add("gold_usd", f"${macro['gold_usd']['value']:,.2f}" if macro.get("gold_usd", {}).get("value") else "")

    g = macro.get("gold_usd", {}).get("value")
    s = macro.get("silver_usd", {}).get("value")
    if g and s:
        ratio = g / s
        hi = ratio > macro_cfg["gold_silver_ratio_alert"]
        macro["gs_ratio"] = {"value": round(ratio, 1),
                             "date": macro["gold_usd"]["date"],
                             "stale": macro["gold_usd"]["stale"] or macro["silver_usd"]["stale"]}
        add("gs_ratio", f"{ratio:.1f}",
            note=f"市场恐慌区（白银相对低估，>{macro_cfg['gold_silver_ratio_alert']:g}）" if hi else "",
            highlight=hi)

    tips_alert = macro_cfg["tips_alert"]
    t = macro.get("tips", {}).get("value")
    add("tips", f"{t:.2f}%" if t is not None else "",
        note=f"高息环境，常为黄金底部区间（>{tips_alert:g}%）" if t is not None and t > tips_alert else "",
        highlight=t is not None and t > tips_alert)

    add("usd_cny", f"{macro['usd_cny']['value']:.4f}" if macro.get("usd_cny", {}).get("value") else "")
    add("jpy_cny", f"{macro['jpy_cny']['value']:.4f}" if macro.get("jpy_cny", {}).get("value") else "")
    return rows


# ---- 历史趋势 ----

def load_history_series(history_dir, days):
    """按日期升序返回最近 days 天的快照列表 [(date_str, snapshot_dict)...]。"""
    files = sorted(glob.glob(os.path.join(history_dir, "*.json")))[-days:]
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
        view_cards.append(build_card_view(card_cfg, reading, anchor["value"],
                                          distortion, cfg["distortion_months"]))

    # ---- 宏观/避险层
    macro = fetch_macro(hist_macro, retries)
    macro_rows = build_macro_view(macro, cfg["macro"])

    # ---- 落历史
    os.makedirs(history_dir, exist_ok=True)
    snapshot = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": anchor,
        "cards": snapshot_cards,
        "macro": macro,
    }
    with open(os.path.join(history_dir, f"{today}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # ---- 趋势（含今天刚落的快照）
    trend_days = cfg.get("trend", {}).get("days", 90)
    history = load_history_series(history_dir, trend_days)
    trends = build_trends(history, cfg["cards"], trend_days)
    anchor_trend = generate_site.sparkline(trends["anchor"])
    for card_cfg, view in zip([c for c in cfg["cards"] if c["id"] in snapshot_cards], view_cards):
        pts = trends.get(card_cfg["id"], [])
        view["trend_svg"] = generate_site.sparkline(pts, thresholds=trend_thresholds(card_cfg))

    # ---- 生成页面
    page_data = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": {"value_text": f"{anchor['value']:.2f}%", "date": anchor["date"],
                   "stale": anchor["stale"], "trend_svg": anchor_trend},
        "distortion": distortion,
        "distortion_months": cfg["distortion_months"],
        "cards": view_cards,
        "macro": {"rows": macro_rows},
    }
    out_path = os.path.join(cfg["site_dir"], "index.html")
    generate_site.render(page_data, out_path)
    print(f"done. snapshot -> {history_dir}/{today}.json, page -> {out_path}")

    # ---- 本地组合页（含持仓，仅在 --local 时生成，输出到 local/，绝不上传）
    if local:
        local_html, _ = portfolio.build_section(cfg)
        local_path = os.path.join(cfg["portfolio"]["local_dir"], "index.html")
        generate_site.render(page_data, local_path, extra_html=local_html)
        print(f"local page -> {local_path}（含持仓，仅本地，不上传）")


def cli():
    parser = argparse.ArgumentParser(description="每日快照：抓取 → 信号灯 → 静态页")
    parser.add_argument("--local", action="store_true",
                        help="额外生成含持仓的本地组合页 local/index.html（数据不出本机）")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    main(local=args.local)
