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

    # ---- 落历史
    os.makedirs(history_dir, exist_ok=True)
    snapshot = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": anchor,
        "cards": snapshot_cards,
    }
    with open(os.path.join(history_dir, f"{today}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # ---- 生成页面
    page_data = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "anchor": {"value_text": f"{anchor['value']:.2f}%", "date": anchor["date"], "stale": anchor["stale"]},
        "distortion": distortion,
        "distortion_months": cfg["distortion_months"],
        "cards": view_cards,
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
