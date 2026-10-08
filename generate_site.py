# -*- coding: utf-8 -*-
"""静态页生成：单个 index.html，CSS 全内联，无外部依赖，手机一屏截完。"""

import html
import json
import os

ZONE_COLORS = {
    "red": "#e5484d",
    "yellow": "#e8a13a",
    "green": "#2ba471",
    "green2": "#0e8a5f",
    "gray": "#9aa0a6",
}


def _e(s):
    return html.escape(str(s))


def sparkline(points, thresholds=(), width=320, height=56, line_color="#64748b"):
    """内联 SVG 迷你折线图（零依赖）。points: [(date_str, value)...]；
    thresholds: [(value, color, label)...] 画虚线。返回 SVG 字符串，无数据返回空。"""
    pts = [(d, v) for d, v in points if v is not None]
    if len(pts) < 2:
        return ""
    vals = [v for _, v in pts] + [t[0] for t in thresholds]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    n = len(pts)
    step = width / (n - 1)
    left_pad = 2
    right_pad = 46  # 右侧留给阈值标注

    def xy(i, v):
        x = left_pad + i * (width - left_pad - right_pad) / (n - 1)
        y = height - 4 - (v - lo) / (hi - lo) * (height - 8)
        return x, y

    poly = " ".join(f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, (_, v) in enumerate(pts))
    svg = [f'<svg viewBox="0 0 {width} {height}" class="spark" role="img">']
    for tv, color, label in thresholds:
        if not (lo <= tv <= hi):
            continue
        y = xy(0, tv)[1]
        svg.append(f'<line x1="{left_pad}" y1="{y:.1f}" x2="{width - right_pad}" y2="{y:.1f}" '
                   f'stroke="{color}" stroke-width="1" stroke-dasharray="4 3" opacity="0.7"/>')
        svg.append(f'<text x="{width - right_pad + 3}" y="{y + 3:.1f}" font-size="8.5" '
                   f'fill="{color}">{_e(label)}</text>')
    svg.append(f'<polyline points="{poly}" fill="none" stroke="{line_color}" '
               f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>')
    lx, ly = xy(n - 1, pts[-1][1])
    svg.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{line_color}"/>')
    svg.append("</svg>")
    cap = f'<div class="spark-cap">近{len(pts)}天　{pts[0][0][5:]} ~ {pts[-1][0][5:]}　最新 {pts[-1][1]:g}</div>'
    return "".join(svg) + cap


def _macro_html(macro):
    """避险/宏观区块。"""
    rows = []
    for r in macro["rows"]:
        cls = " class='macro-hl'" if r.get("highlight") else ""
        meta = f"数据日期 {r['date']}"
        if r.get("stale"):
            meta += "　<b class='stale'>⚠ 数据陈旧</b>"
        if r.get("note"):
            meta += f"　{_e(r['note'])}"
        rows.append(
            f"<tr{cls}><td>{_e(r['label'])}</td><td class='macro-val'>{_e(r['value_text'])}</td>"
            f"<td class='macro-meta'>{meta}</td></tr>"
        )
    return ('<div class="card"><div class="card-title">避险 / 宏观</div>'
            '<table class="macro">' + "".join(rows) + "</table></div>")


def _card_html(card):
    zone = card["zone"]
    color = ZONE_COLORS[zone]
    lines = []

    # 读数主行
    lines.append(
        f'<div class="reading"><span class="metric">{_e(card["metric_label"])}</span>'
        f'<span class="value">{_e(card["value_text"])}</span></div>'
    )
    if card.get("spread_text"):
        lines.append(f'<div class="sub">{_e(card["spread_text"])}</div>')

    # 灯态 + 距离（文案由 main 组好，含失真期/清仓线标记）
    lines.append(f'<div class="zone" style="background:{color}">{_e(card["zone_text"])}</div>')
    if card.get("distance_text"):
        lines.append(f'<div class="distance">{_e(card["distance_text"])}</div>')

    # 趋势小图（内联 SVG，无外部依赖）
    if card.get("trend_svg"):
        lines.append(card["trend_svg"])

    # 门槛表
    if card.get("threshold_text"):
        lines.append(f'<div class="thresholds">{_e(card["threshold_text"])}</div>')

    # 数据日期 / 陈旧标记
    meta = f'数据日期 {card["data_date"]}'
    if card.get("stale"):
        meta += "　<b class='stale'>⚠ 数据陈旧（抓取失败，显示上次读数）</b>"
    if card.get("note"):
        meta += f'　{_e(card["note"])}'
    lines.append(f'<div class="meta">{meta}</div>')

    return (
        f'<div class="card" style="border-left:6px solid {color}">'
        f'<div class="card-title">{_e(card["name"])}</div>'
        + "".join(lines)
        + "</div>"
    )


def render(snapshot, out_path, extra_html=""):
    """snapshot: main.py 构建好的展示字典。extra_html 追加在指标卡片之后（本地组合模块用）。"""
    cards_html = "".join(_card_html(c) for c in snapshot["cards"])
    anchor = snapshot["anchor"]
    anchor_meta = f'数据日期 {anchor["date"]}'
    if anchor.get("stale"):
        anchor_meta += "　<b class='stale'>⚠ 数据陈旧</b>"
    anchor_trend = anchor.get("trend_svg", "")
    macro_html = _macro_html(snapshot["macro"]) if snapshot.get("macro") else ""

    distortion_banner = ""
    if snapshot.get("distortion"):
        dm = snapshot.get("distortion_months", [5, 6, 7])
        distortion_banner = (
            f'<div class="distortion">⬜ 当前为分红季失真期（{dm[0]}–{dm[-1]}月）'
            "：股息率读数系统性虚高，只记录、不触发动作</div>"
        )

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>投资指标仪表盘</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,"PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif;
        background:#f6f7f8; color:#1f2328; padding:16px; }}
  .wrap {{ max-width:640px; margin:0 auto; }}
  header {{ background:#fff; border-radius:12px; padding:14px 16px; margin-bottom:12px;
           box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .snap {{ font-size:13px; color:#6b7280; }}
  .anchor {{ font-size:20px; font-weight:600; margin-top:4px; }}
  .anchor small {{ font-size:12px; color:#6b7280; font-weight:400; }}
  .distortion {{ background:#f3f4f6; border:1px dashed #9aa0a6; color:#4b5563;
                border-radius:10px; padding:10px 14px; font-size:13px; margin-bottom:12px; }}
  .card {{ background:#fff; border-radius:12px; padding:14px 16px; margin-bottom:12px;
          box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .card-title {{ font-size:16px; font-weight:600; margin-bottom:8px; }}
  .reading {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .metric {{ font-size:14px; color:#4b5563; }}
  .value {{ font-size:26px; font-weight:700; }}
  .sub {{ font-size:13px; color:#4b5563; margin-top:2px; }}
  .zone {{ color:#fff; font-size:17px; font-weight:700; text-align:center;
          border-radius:8px; padding:8px 0; margin:10px 0 6px; }}
  .distance {{ font-size:14px; color:#1f2328; margin-bottom:4px; }}
  .thresholds {{ font-size:12px; color:#6b7280; line-height:1.7; margin-top:6px;
                border-top:1px solid #eef0f2; padding-top:6px; }}
  .meta {{ font-size:12px; color:#9aa0a6; margin-top:6px; }}
  .stale {{ color:#d97706; }}
  footer {{ font-size:12px; color:#9aa0a6; text-align:center; margin-top:8px; }}
  /* ---- 趋势小图（内联 SVG） ---- */
  .spark {{ width:100%; height:auto; margin-top:8px; }}
  .spark-cap {{ font-size:10px; color:#b0b4b9; margin-top:1px; }}
  /* ---- 避险/宏观区块 ---- */
  table.macro {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.macro td {{ padding:6px 4px; border-bottom:1px solid #f5f6f7; vertical-align:baseline; }}
  table.macro tr:last-child td {{ border-bottom:none; }}
  .macro-val {{ font-size:17px; font-weight:700; white-space:nowrap; }}
  .macro-meta {{ font-size:11px; color:#9aa0a6; text-align:right; }}
  tr.macro-hl td {{ background:#fdf6e7; }}
  /* ---- 组合模块（仅本地页） ---- */
  .pf-card {{ border-left:6px solid #6b7280; }}
  .pf-title {{ font-size:16px; font-weight:600; margin-bottom:8px; }}
  .pf-title2 {{ font-size:14px; font-weight:600; margin:12px 0 6px; }}
  .pf-sum {{ font-size:13px; color:#4b5563; margin-bottom:8px; }}
  .pf-sum small {{ color:#9aa0a6; }}
  table.pf {{ width:100%; border-collapse:collapse; font-size:12px; margin-bottom:6px; }}
  table.pf th {{ text-align:left; color:#9aa0a6; font-weight:400; padding:3px 4px;
                border-bottom:1px solid #eef0f2; }}
  table.pf td {{ padding:4px; border-bottom:1px solid #f5f6f7; }}
  tr.pf-yellow td {{ background:#fdf6e7; }}
  tr.pf-red td {{ background:#fdecec; }}
  tr.pf-stale td {{ color:#b0b4b9; }}
  .pf-redline {{ font-size:13px; color:#e5484d; font-weight:600; margin:6px 0; }}
  .pf-advice-title {{ font-size:14px; font-weight:600; margin:10px 0 2px; }}
  .pf-advice-mode {{ font-size:12px; color:#6b7280; margin-bottom:4px; }}
  ul.pf-advice {{ font-size:13px; padding-left:20px; }}
  .pf-note {{ font-size:12px; color:#9aa0a6; margin-top:8px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="snap">快照 {_e(snapshot["generated_at"])}</div>
    <div class="anchor">10Y 国债 {_e(anchor["value_text"])}　<small>{anchor_meta}</small></div>
    {anchor_trend}
  </header>
  {distortion_banner}
  {cards_html}
  {macro_html}
  {extra_html}
  <footer>信号灯规则与阈值见个人投资笔记 · 数据为公开行情整理，不构成投资建议</footer>
</div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)


if __name__ == "__main__":
    # 冒烟：用样例数据渲染一页看效果
    sample = {
        "generated_at": "样例 2026-10-08 09:00",
        "anchor": {"value_text": "1.68%", "date": "2026-09-30", "stale": False},
        "distortion": False,
        "cards": [
            {"name": "515450 红利低波50", "metric_label": "股息率TTM（口径1）", "value_text": "4.39%",
             "spread_text": "息差 = 4.39% − 1.68% = 2.71%", "zone": "yellow",
             "zone_text": "🟡 黄灯 · 正常定投",
             "distance_text": "距 🟢击球区（息差≥3.2%）还差 0.49pp",
             "threshold_text": "门槛（股息率=10Y+X）：🔴<3.18% 🟡3.18–4.88% 🟢≥4.88% 🟢🟢≥5.98%",
             "data_date": "2026-09-30", "stale": False, "note": ""},
            {"name": "515180 中证红利", "metric_label": "股息率TTM（口径1）", "value_text": "4.28%",
             "spread_text": "息差 = 4.28% − 1.68% = 2.60%", "zone": "yellow",
             "zone_text": "🟡 黄灯 · 正常定投",
             "distance_text": "距 🟢击球区（息差≥3.0%）还差 0.40pp",
             "threshold_text": "门槛（股息率=10Y+X）：🔴<3.18% 🟡3.18–4.68% 🟢≥4.68% 🟢🟢≥5.68%",
             "data_date": "2026-09-30", "stale": False, "note": "PB 未纳入（免费数据源暂缺）"},
            {"name": "159209 红利质量", "metric_label": "PE-TTM（口径2）", "value_text": "15.78",
             "spread_text": "", "zone": "green",
             "zone_text": "🟢 击球区",
             "distance_text": "距 🟢🟢重仓区（PE≤13）还需降 2.78",
             "threshold_text": "门槛（PE-TTM）：🔴≥20 🟡16–20 🟢13–16 🟢🟢≤13",
             "data_date": "2026-09-30", "stale": False, "note": ""},
        ],
    }
    render(sample, "site/index.html")
    print("written site/index.html (sample)")
