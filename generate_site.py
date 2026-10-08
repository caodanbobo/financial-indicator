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


def render(snapshot, out_path):
    """snapshot: main.py 构建好的展示字典。"""
    cards_html = "".join(_card_html(c) for c in snapshot["cards"])
    anchor = snapshot["anchor"]
    anchor_meta = f'数据日期 {anchor["date"]}'
    if anchor.get("stale"):
        anchor_meta += "　<b class='stale'>⚠ 数据陈旧</b>"

    distortion_banner = ""
    if snapshot.get("distortion"):
        months = "–".join(str(m) for m in snapshot.get("distortion_months", [5, 6, 7]))
        distortion_banner = (
            f'<div class="distortion">⬜ 当前为分红季失真期（{months}月）'
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
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="snap">快照 {_e(snapshot["generated_at"])}</div>
    <div class="anchor">10Y 国债 {_e(anchor["value_text"])}　<small>{anchor_meta}</small></div>
  </header>
  {distortion_banner}
  {cards_html}
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
