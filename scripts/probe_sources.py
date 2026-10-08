#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0：数据源可行性探测脚本。

每个数据源一个 probe 函数，单个失败不影响其他。
用法：.venv/bin/python scripts/probe_sources.py

网络说明：本机 macOS 系统代理（127.0.0.1:7892）会被 requests 自动拾取，
而东财等境内接口经该代理会被拒，因此脚本启动时设置 NO_PROXY='*' 强制直连；
CoinGecko 等被墙的境外源再单独走系统代理（自动探测，失败则直连）。
"""

import os
import urllib.request

# 必须先抓系统代理再屏蔽：一旦设置 NO_PROXY='*'，getproxies() 只会返回 {'no': '*'}
_SYSTEM_PROXIES = urllib.request.getproxies()

# 必须在 import akshare/requests 发请求之前设置，保证境内接口不走系统代理
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import time
import traceback
import urllib.request
from io import StringIO

import pandas as pd
import requests

RESULTS = []  # (指标, 数据源, 状态, 最新读数, 读数日期, 耗时s, 备注)


def record(indicator, source, ok, value, date, elapsed, note=""):
    RESULTS.append((indicator, source, "✅" if ok else "❌", value, date, f"{elapsed:.1f}s", note))


def run(name, fn):
    """统一执行 probe：计时、兜异常。"""
    t0 = time.time()
    try:
        fn(time.time() - t0)
    except Exception as e:  # noqa: BLE001
        elapsed = time.time() - t0
        short = f"{type(e).__name__}: {str(e)[:120]}"
        record(name, "(见备注)", False, "-", "-", elapsed, short)
        traceback.print_exc()


# ---------------------------------------------------------------- helpers


def system_proxy():
    """返回脚本启动时抓到的系统代理（proxies dict 或 None）。"""
    http = _SYSTEM_PROXIES.get("https") or _SYSTEM_PROXIES.get("http")
    if http:
        if not http.startswith("http"):
            http = "http://" + http
        return {"http": http, "https": http}
    return None


def get_json(url, timeout=20, prefer_proxy=False):
    """GET JSON；prefer_proxy=True 时先走系统代理，失败再直连；否则相反。每种方式最多试 2 次。"""
    proxy = system_proxy()
    attempts = [(True, proxy), (False, None)] if (prefer_proxy and proxy) else [(False, None), (True, proxy)]
    last = None
    for use_proxy, p in attempts:
        if use_proxy and not p:
            continue
        n_try = 3 if use_proxy else 2
        for _ in range(n_try):
            try:
                r = requests.get(url, proxies=p if use_proxy else None, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
    raise last


# ---------------------------------------------------------------- probes


def probe_cn10y(elapsed):
    import akshare as ak

    t0 = time.time()
    df = ak.bond_zh_us_rate()
    s = df[["日期", "中国国债收益率10年"]].dropna()
    last = s.iloc[-1]
    record("中国10Y国债收益率", "akshare bond_zh_us_rate (东财)", True,
           f"{last['中国国债收益率10年']:.4f}%", str(last["日期"]), time.time() - t0,
           "节假日返回 NaN 行，需 dropna")


def probe_cn10y_backup(elapsed):
    import akshare as ak

    t0 = time.time()
    today = pd.Timestamp.today()
    df = ak.bond_china_yield(start_date=(today - pd.Timedelta(days=15)).strftime("%Y%m%d"),
                             end_date=today.strftime("%Y%m%d"))
    sub = df[df["曲线名称"] == "中债国债收益率曲线"][["日期", "10年"]]
    last = sub.iloc[-1]
    record("中国10Y国债收益率(备选)", "akshare bond_china_yield (中债)", True,
           f"{last['10年']:.4f}%", str(last["日期"]), time.time() - t0)


def probe_csindex(code, name):
    import akshare as ak

    t0 = time.time()
    df = ak.stock_zh_index_value_csindex(symbol=code)
    df = df.sort_values("日期")
    last = df.iloc[-1]
    record(f"指数估值 {code} {name}", "akshare stock_zh_index_value_csindex", True,
           f"PE1={last['市盈率1']} PE2={last['市盈率2']} 股息率1={last['股息率1']}% 股息率2={last['股息率2']}%",
           str(last["日期"]), time.time() - t0,
           "无 PB 字段；股息率1/2 为不同股本口径")


def probe_etf_sina(code, name):
    import akshare as ak

    t0 = time.time()
    symbol = ("sh" if code.startswith("5") else "sz") + code
    df = ak.fund_etf_hist_sina(symbol=symbol)
    last = df.iloc[-1]
    record(f"ETF价格 {code} {name}", "akshare fund_etf_hist_sina (新浪)", True,
           f"收盘 {last['close']}", str(last["date"]), time.time() - t0)


def probe_fund_008163(elapsed):
    import akshare as ak

    t0 = time.time()
    df = ak.fund_open_fund_info_em(symbol="008163", indicator="单位净值走势")
    last = df.iloc[-1]
    record("基金净值 008163", "akshare fund_open_fund_info_em (东财)", True,
           f"净值 {last['单位净值']}", str(last["净值日期"]), time.time() - t0)


def probe_gold_silver_cny(elapsed):
    import akshare as ak

    t0 = time.time()
    g = ak.spot_golden_benchmark_sge().iloc[-1]
    s = ak.spot_silver_benchmark_sge().iloc[-1]
    record("人民币金价(上金所基准价)", "akshare spot_golden_benchmark_sge", True,
           f"{g['晚盘价']} 元/g", str(g["交易时间"]), time.time() - t0, "SGE 更新有延迟")
    record("人民币银价(上金所基准价)", "akshare spot_silver_benchmark_sge", True,
           f"{s['晚盘价']} 元/kg", str(s["交易时间"]), time.time() - t0, "SGE 更新有延迟")


def probe_gold_silver_usd(elapsed):
    import akshare as ak

    t0 = time.time()
    df = ak.futures_foreign_commodity_realtime(symbol=["XAU", "XAG"])
    vals = dict(zip(df["名称"], df["最新价"]))
    date = str(df.iloc[0]["日期"])
    gold, silver = vals.get("伦敦金"), vals.get("伦敦银")
    ratio = float(gold) / float(silver) if gold and silver else None
    record("国际金银(XAU/XAG)", "akshare futures_foreign_commodity_realtime (新浪)", True,
           f"金 ${gold} / 银 ${silver} / 金银比 {ratio:.1f}", date, time.time() - t0, "盘中实时")


def probe_tips(elapsed):
    t0 = time.time()
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10", timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df = df[df["DFII10"] != "."]
    last = df.iloc[-1]
    record("美国10Y TIPS实际利率", "FRED fredgraph.csv?id=DFII10", True,
           f"{last['DFII10']}%", last["observation_date"], time.time() - t0)


def probe_fx(elapsed):
    import akshare as ak

    t0 = time.time()
    df = None
    last_err = None
    for _ in range(3):  # safe.gov.cn 偶发 SSL 失败，重试
        try:
            df = ak.currency_boc_safe()
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if df is None:
        raise last_err
    last = df.iloc[-1]
    record("汇率 USD/CNY JPY/CNY", "akshare currency_boc_safe (外管局/中行)", True,
           f"USD {float(last['美元'])/100:.4f} / 100JPY {last['日元']}", str(last["日期"]),
           time.time() - t0, "牌价单位为 100 外币兑人民币")


def probe_btc(elapsed):
    t0 = time.time()
    data = get_json("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd",
                    prefer_proxy=True)
    record("BTC/ETH 价格", "CoinGecko simple/price", True,
           f"BTC ${data['bitcoin']['usd']} / ETH ${data['ethereum']['usd']}",
           pd.Timestamp.now().strftime("%Y-%m-%d"), time.time() - t0,
           "本机需走代理（直连超时）")


def probe_ahr999(elapsed):
    t0 = time.time()
    data = get_json("https://9992100.xyz/api/ahr999")
    dt = pd.Timestamp.utcfromtimestamp(data["updated_at_unix"]).strftime("%Y-%m-%d %H:%M UTC")
    record("Ahr999 指数", "9992100.xyz/api/ahr999 (第三方免费)", True,
           f"ahr999={data['ahr999']:.4f} (BTC ${data['price_usd']:.0f})", dt,
           time.time() - t0, "第三方小站，可持续性待观察；含 gma200 可自算校验")


def probe_defillama(elapsed):
    t0 = time.time()
    data = get_json("https://yields.llama.fi/pools", timeout=90)
    pools = data["data"]
    picks = []
    for proj, sym, chain in [("sky-lending", "SUSDS", "Ethereum"),
                             ("sky-lending", "SDAI", "Ethereum"),
                             ("ondo-yield-assets", "USDY", "Ethereum")]:
        hit = [p for p in pools if p.get("project") == proj
               and str(p.get("symbol", "")).upper() == sym and p.get("chain") == chain]
        if hit:
            picks.append(f"{sym}={hit[0]['apy']:.2f}%")
    record("美债RWA利率(sDAI/sUSDS/USDY)", "DefiLlama yields.llama.fi/pools", bool(picks),
           " / ".join(picks), pd.Timestamp.now().strftime("%Y-%m-%d"), time.time() - t0,
           "响应约 11MB，较慢")


def probe_im(elapsed):
    import akshare as ak

    t0 = time.time()
    spot = ak.stock_zh_index_daily(symbol="sh000852").iloc[-1]
    # 选远季合约：季月为 3/6/9/12，取当前之后的第二个季月
    today = pd.Timestamp.today()
    quarters = []
    y, m = today.year, today.month
    for yy in (y, y + 1):
        for mm in (3, 6, 9, 12):
            if (yy, mm) > (y, m):
                quarters.append((yy, mm))
    fy, fm = quarters[1] if len(quarters) > 1 else quarters[0]
    code = f"IM{str(fy)[2:]}{fm:02d}"
    fut = ak.futures_zh_daily_sina(symbol=code).iloc[-1]
    basis = (float(spot["close"]) - float(fut["close"])) / float(spot["close"]) * 100
    record("IM股指期货贴水", "akshare stock_zh_index_daily + futures_zh_daily_sina (新浪)", True,
           f"现货 {spot['close']:.0f} / {code} 收盘 {fut['close']} / 贴水 {basis:.2f}%",
           str(spot["date"]), time.time() - t0, "年化计算留待正式开发")


# ---------------------------------------------------------------- main

def main():
    probes = [
        ("中国10Y国债收益率", lambda e: probe_cn10y(e)),
        ("中国10Y国债收益率(备选)", lambda e: probe_cn10y_backup(e)),
        ("指数估值 000922", lambda e: probe_csindex("000922", "中证红利")),
        ("指数估值 H30269", lambda e: probe_csindex("H30269", "红利低波")),
        ("指数估值 932315", lambda e: probe_csindex("932315", "红利质量")),
        ("ETF 515450", lambda e: probe_etf_sina("515450", "红利低波50")),
        ("ETF 515180", lambda e: probe_etf_sina("515180", "中证红利")),
        ("ETF 159209", lambda e: probe_etf_sina("159209", "红利质量")),
        ("基金 008163", lambda e: probe_fund_008163(e)),
        ("金价银价 CNY", lambda e: probe_gold_silver_cny(e)),
        ("金价银价 USD", lambda e: probe_gold_silver_usd(e)),
        ("TIPS", lambda e: probe_tips(e)),
        ("汇率", lambda e: probe_fx(e)),
        ("BTC/ETH", lambda e: probe_btc(e)),
        ("Ahr999", lambda e: probe_ahr999(e)),
        ("RWA利率", lambda e: probe_defillama(e)),
        ("IM贴水", lambda e: probe_im(e)),
    ]
    for name, fn in probes:
        print(f"probe: {name} ...", flush=True)
        run(name, fn)

    df = pd.DataFrame(RESULTS, columns=["指标", "数据源", "状态", "最新读数", "读数日期", "耗时", "备注"])
    print("\n" + "=" * 100)
    print(df.to_string(index=False))
    print("=" * 100)
    n_ok = (df["状态"] == "✅").sum()
    print(f"成功 {n_ok}/{len(df)}")


if __name__ == "__main__":
    main()
