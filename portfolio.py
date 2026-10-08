# -*- coding: utf-8 -*-
"""组合记账与再平衡模块（纯本地，隐私数据绝不出本机）。

输入 portfolio.yaml（按总金额记账），输出占比/偏离灯色/月度分流建议的 HTML 区块。
渲染产物只能进 local/ 目录（已 gitignore），绝不能进 site/。
"""

import html
import json
import os
import time
from datetime import date, datetime

import yaml

from fetchers import sources


# ---------------------------------------------------------------- 数据加载

def load_portfolio(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("holdings", []) if data else []


def get_fx(cache_path, retries=2):
    """当天内复用缓存的汇率；缓存过期才抓（currency_boc_safe 很慢）。"""
    today = date.today().isoformat()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("cached_on") == today:
                return cache, False
        except Exception:  # noqa: BLE001
            pass
    last = None
    for _ in range(retries + 1):
        try:
            fx = sources.fetch_fx()
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    else:
        raise RuntimeError(f"汇率抓取失败: {last}")
    fx["cached_on"] = today
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(fx, f, ensure_ascii=False, indent=2)
    return fx, True


def to_cny(holding, fx):
    cur = holding.get("currency", "CNY")
    amount = float(holding["amount"])
    if cur == "CNY":
        return amount
    return amount * fx[cur]


# ---------------------------------------------------------------- 计算

def compute(holdings, cfg, fx):
    """返回计算结果 dict（供渲染与测试）。"""
    today = date.today()
    stale_days = cfg["stale_days"]

    rows = []
    for h in holdings:
        cny = to_cny(h, fx)
        upd = datetime.strptime(str(h["updated"]), "%Y-%m-%d").date()
        rows.append({
            **h,
            "cny": cny,
            "layer": h.get("layer", "onshore"),
            "stale": (today - upd).days > stale_days,
        })

    onshore = [r for r in rows if r["layer"] == "onshore"]
    frozen = [r for r in rows if r["layer"] == "frozen"]
    on_total = sum(r["cny"] for r in onshore)
    fr_total = sum(r["cny"] for r in frozen)

    # 在岸盘各类别占比与偏离
    cats = []
    for cat, (lo, hi) in cfg["targets"].items():
        amount = sum(r["cny"] for r in onshore if r.get("category") == cat)
        pct = amount / on_total * 100 if on_total else 0
        if lo <= pct <= hi:
            status, dev = "ok", 0.0
        else:
            dev = round(lo - pct, 1) if pct < lo else round(pct - hi, 1)  # 正=超配，负=低配
            if abs(dev) >= 10:
                status = "red"
            elif abs(dev) >= 5:
                status = "yellow"
            else:
                status = "ok"
        cats.append({"cat": cat, "amount": amount, "pct": pct, "lo": lo, "hi": hi,
                     "status": status, "dev": dev,
                     "shortfall": max(0.0, lo / 100 * on_total - amount)})

    # 单一 ETF 红线
    redlines = []
    limit = cfg["single_etf_limit"]
    for r in onshore:
        if r.get("etf") and on_total and r["cny"] / on_total * 100 > limit:
            redlines.append(f"{r['name']} 占在岸盘 {r['cny']/on_total*100:.1f}%，超过单一 ETF {limit}% 红线")

    # 月度分流建议
    monthly = cfg["monthly_amount"]
    gap_cats = [c for c in cats if c["shortfall"] > 0]
    advice = []
    if gap_cats:
        total_gap = sum(c["shortfall"] for c in gap_cats)
        for c in gap_cats:
            amt = monthly * c["shortfall"] / total_gap
            advice.append({"text": f"{c['cat']}：¥{amt:,.0f}（低配 {abs(c['dev']):.1f}pp，距下限还差 ¥{c['shortfall']:,.0f}）",
                           "amount": amt})
        advice_mode = f"存在低配篮子，本月 ¥{monthly:,.0f} 按缺口比例优先补低配"
    else:
        for cat, ratio in cfg["monthly_split"].items():
            amt = monthly * ratio
            advice.append({"text": f"{cat}：¥{amt:,.0f}", "amount": amt})
        advice_mode = f"无低配篮子，按既定分流（{cfg.get('monthly_split_note', '')}）"

    return {
        "rows": rows, "onshore": onshore, "frozen": frozen,
        "on_total": on_total, "fr_total": fr_total,
        "cats": cats, "redlines": redlines,
        "advice": advice, "advice_mode": advice_mode, "monthly": monthly,
        "fx": fx,
    }


# ---------------------------------------------------------------- 渲染

STATUS_ICON = {"ok": "✅", "yellow": "🟡", "red": "🔴"}


def _e(s):
    return html.escape(str(s))


def render_html(result, cfg):
    """渲染组合区块 HTML（CSS 类在 generate_site 中统一定义）。"""
    r = result
    fx = r["fx"]
    h = []

    h.append('<div class="pf-title">组合占比 vs 目标（本地模块，数据不出本机）</div>')
    h.append(
        f'<div class="pf-sum">在岸盘 <b>¥{r["on_total"]:,.0f}</b>　'
        f'冻结层 <b>¥{r["fr_total"]:,.0f}</b>（不参与再平衡）　'
        f'<small>汇率 USD {fx["USD"]:.4f} / JPY {fx["JPY"]:.4f}（{fx["date"]}）</small></div>'
    )

    # 在岸盘占比表
    h.append('<table class="pf"><tr><th>类别</th><th>金额</th><th>占比</th>'
             "<th>目标区间</th><th>状态</th></tr>")
    for c in r["cats"]:
        icon = STATUS_ICON[c["status"]]
        if c["status"] == "ok" and c["dev"] == 0:
            tip = ""
        elif c["status"] == "ok":
            tip = f"偏离 {abs(c['dev']):.1f}pp"
        elif c["status"] == "yellow":
            tip = f"偏离 {abs(c['dev']):.1f}pp，当月增量资金补低配" if c["dev"] < 0 \
                else f"超配 {abs(c['dev']):.1f}pp，增量先停投本类"
        else:
            tip = f"偏离 {abs(c['dev']):.1f}pp，允许卖出纠偏" if c["dev"] < 0 \
                else f"超配 {abs(c['dev']):.1f}pp，允许卖出补低配"
        h.append(
            f"<tr class='pf-{c['status']}'><td>{_e(c['cat'])}</td>"
            f"<td>¥{c['amount']:,.0f}</td><td>{c['pct']:.1f}%</td>"
            f"<td>{c['lo']}–{c['hi']}%</td><td>{icon} {_e(tip)}</td></tr>"
        )
    h.append("</table>")

    # 红线
    for line in r["redlines"]:
        h.append(f'<div class="pf-redline">🔴 {_e(line)}</div>')

    # 月度分流建议
    h.append(f'<div class="pf-advice-title">本月可投 ¥{r["monthly"]:,.0f} → 建议</div>')
    h.append(f'<div class="pf-advice-mode">{_e(r["advice_mode"])}</div>')
    h.append('<ul class="pf-advice">')
    for a in r["advice"]:
        h.append(f"<li>{_e(a['text'])}</li>")
    h.append("</ul>")

    # 冻结层
    if r["frozen"]:
        h.append('<div class="pf-title2">冻结层（只显示，不参与在岸再平衡）</div>')
        h.append('<table class="pf"><tr><th>标的</th><th>平台</th><th>金额</th>'
                 "<th>折算人民币</th><th>占冻结层</th><th>更新</th></tr>")
        for x in r["frozen"]:
            cur = x.get("currency", "CNY")
            amt = f'{x["amount"]:,.2f} {cur}' if cur != "CNY" else f'¥{x["amount"]:,.0f}'
            share = x["cny"] / r["fr_total"] * 100 if r["fr_total"] else 0
            cls = " class='pf-stale'" if x["stale"] else ""
            h.append(
                f"<tr{cls}><td>{_e(x['name'])}</td><td>{_e(x.get('platform',''))}</td>"
                f"<td>{amt}</td><td>¥{x['cny']:,.0f}</td><td>{share:.1f}%</td>"
                f"<td>{_e(x['updated'])}</td></tr>"
            )
        h.append("</table>")

    # 持仓明细（在岸盘）
    h.append('<div class="pf-title2">在岸盘持仓明细</div>')
    h.append('<table class="pf"><tr><th>标的</th><th>平台</th><th>类别</th>'
             "<th>金额</th><th>折算人民币</th><th>更新</th></tr>")
    for x in r["onshore"]:
        cur = x.get("currency", "CNY")
        amt = f'{x["amount"]:,.2f} {cur}' if cur != "CNY" else f'¥{x["amount"]:,.0f}'
        cls = " class='pf-stale'" if x["stale"] else ""
        stale_mark = " ⏳" if x["stale"] else ""
        h.append(
            f"<tr{cls}><td>{_e(x['name'])}</td><td>{_e(x.get('platform',''))}</td>"
            f"<td>{_e(x.get('category',''))}</td><td>{amt}</td>"
            f"<td>¥{x['cny']:,.0f}</td><td>{_e(x['updated'])}{stale_mark}</td></tr>"
        )
    h.append("</table>")
    h.append(f'<div class="pf-note">⏳ = 超过 {cfg["stale_days"]} 天未更新，金额可能已失真；'
             "每月核对日（每月第一个周末）更新 portfolio.yaml</div>")

    return '<div class="card pf-card">' + "".join(h) + "</div>"


def build_section(cfg):
    """主入口：读持仓 + 汇率 → 返回组合区块 HTML。无持仓文件时返回提示区块。"""
    pcfg = cfg["portfolio"]
    holdings = load_portfolio(pcfg["file"])
    if holdings is None:
        return ('<div class="card pf-card"><div class="pf-title">组合模块</div>'
                "<div class='pf-note'>未找到 portfolio.yaml。请复制 portfolio.example.yaml "
                "为 portfolio.yaml 并填入真实持仓（该文件已被 .gitignore 排除，不会上传）。</div></div>"), None
    if not holdings:
        return ('<div class="card pf-card"><div class="pf-title">组合模块</div>'
                "<div class='pf-note'>portfolio.yaml 为空。</div></div>"), None

    need_fx = any(h.get("currency", "CNY") != "CNY" for h in holdings)
    fx = {"USD": 7.0, "JPY": 0.05, "date": "未用", "cached_on": "-"}
    if need_fx:
        fx, fresh = get_fx(pcfg["fx_cache"])
        print(f"fx: {'fetched' if fresh else 'cached'} USD={fx['USD']} JPY={fx['JPY']} ({fx['date']})")
    result = compute(holdings, pcfg, fx)
    return render_html(result, pcfg), result
