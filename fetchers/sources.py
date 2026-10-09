# -*- coding: utf-8 -*-
"""各数据源抓取函数。每个函数返回 dict，失败抛异常由调用方兜底（降级到历史读数）。

接口名以 akshare 1.19.1 实测为准（见 阶段0_数据源实测报告.md）。
"""

from . import network  # noqa: F401  副作用：设置 NO_PROXY，必须先于 akshare

import akshare as ak
import pandas as pd


def fetch_anchor():
    """中国10年期国债收益率（%）。节假日返回 NaN 行，必须 dropna 后取最后有效行。"""
    df = ak.bond_zh_us_rate()
    s = df[["日期", "中国国债收益率10年"]].dropna()
    if s.empty:
        raise ValueError("bond_zh_us_rate 无有效数据")
    last = s.iloc[-1]
    return {"value": round(float(last["中国国债收益率10年"]), 4),
            "date": str(last["日期"])}


def fetch_index_value(index_code):
    """中证指数官网估值。返回 PE1/PE2/股息率1/股息率2 及数据日期（T+1 正常）。

    注意：接口只返回最近约 20 个交易日，且无 PB 字段。
    """
    df = ak.stock_zh_index_value_csindex(symbol=index_code)
    df = df.sort_values("日期")
    if df.empty:
        raise ValueError(f"csindex 估值无数据: {index_code}")
    last = df.iloc[-1]
    return {
        "pe1": float(last["市盈率1"]),
        "pe2": float(last["市盈率2"]),
        "dividend1": float(last["股息率1"]),
        "dividend2": float(last["股息率2"]),
        "date": str(last["日期"]),
    }


def fetch_fx():
    """USD/JPY 兑人民币汇率（外币 1 单位 = 多少人民币）。

    currency_boc_safe 很慢（20s~160s）且偶发 SSL 失败，调用方应加重试并做本地缓存。
    返回 {"USD": 6.7351, "JPY": 0.042727, "date": "2026-09-30"}。
    """
    df = ak.currency_boc_safe()
    last = df.iloc[-1]
    return {
        "USD": float(last["美元"]) / 100,
        "JPY": float(last["日元"]) / 100,
        "date": str(last["日期"]),
    }


def fetch_gold_cny():
    """上金所黄金基准价（元/g）。SGE 更新有延迟，如实返回数据日期。

    返回 {"gold_cny": {"value": 895.6, "date": "2026-09-28"}}。
    """
    df = ak.spot_golden_benchmark_sge()
    last = df.iloc[-1]
    price = last["晚盘价"] if pd.notna(last["晚盘价"]) else last["早盘价"]
    return {"gold_cny": {"value": float(price), "date": str(last["交易时间"])}}


def fetch_gold_silver_usd():
    """国际现货金银（美元/盎司，新浪伦敦金银，盘中实时）。"""
    df = ak.futures_foreign_commodity_realtime(symbol=["XAU", "XAG"])
    vals = dict(zip(df["名称"], df["最新价"]))
    d = str(df.iloc[0]["日期"])
    return {
        "gold_usd": {"value": float(vals["伦敦金"]), "date": d},
        "silver_usd": {"value": float(vals["伦敦银"]), "date": d},
    }


def fetch_tips():
    """美国 10Y TIPS 实际利率（FRED DFII10 免 key CSV）。缺失值是字符串 '.'。"""
    import requests
    from io import StringIO

    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10", timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    vals = pd.to_numeric(df["DFII10"], errors="coerce")
    df = df[vals.notna()]
    last = df.iloc[-1]
    return {"tips": {"value": float(vals[vals.notna()].iloc[-1]), "date": str(last["observation_date"])}}


def fetch_fx_macro():
    """汇率（复用 fetch_fx），拆成宏观区块需要的两条。"""
    fx = fetch_fx()
    return {
        "usd_cny": {"value": fx["USD"], "date": fx["date"]},
        "jpy_cny": {"value": fx["JPY"], "date": fx["date"]},
    }


# ---------------------------------------------------------------- 加密冻结层

def fetch_crypto_prices():
    """BTC/ETH 美元价（CoinGecko 免 key）。本机需走系统代理，Actions 直连。"""
    from . import network

    data = network.get_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd",
        prefer_proxy=True)
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    return {
        "btc_usd": {"value": float(data["bitcoin"]["usd"]), "date": today},
        "eth_usd": {"value": float(data["ethereum"]["usd"]), "date": today},
    }


def fetch_ahr999():
    """Ahr999 指数（9992100.xyz 第三方免费 API，含 BTC 价与 200 日定投成本可交叉校验）。"""
    from . import network

    data = network.get_json("https://9992100.xyz/api/ahr999")
    d = pd.Timestamp.utcfromtimestamp(data["updated_at_unix"]).strftime("%Y-%m-%d")
    return {"ahr999": {"value": round(float(data["ahr999"]), 4), "date": d}}


def fetch_rwa():
    """美债 RWA 利率（DefiLlama 免 key，响应约 11MB，超时给足）。"""
    from . import network

    data = network.get_json("https://yields.llama.fi/pools", timeout=90)
    pools = data["data"]
    out = {}
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    for key, proj, sym, chain in [("susds_apy", "sky-lending", "SUSDS", "Ethereum"),
                                  ("sdai_apy", "sky-lending", "SDAI", "Ethereum"),
                                  ("usdy_apy", "ondo-yield-assets", "USDY", "Ethereum")]:
        hit = [p for p in pools if p.get("project") == proj
               and str(p.get("symbol", "")).upper() == sym and p.get("chain") == chain]
        if not hit:
            raise ValueError(f"DefiLlama 中未找到 {proj}/{sym}/{chain}")
        out[key] = {"value": round(float(hit[0]["apy"]), 2), "date": today}
    return out


# ---------------------------------------------------------------- IM 贴水

def _third_friday(year, month):
    """合约到期日：当月第三个周五。"""
    import calendar

    c = calendar.Calendar(firstweekday=calendar.MONDAY)
    fridays = [d for d in c.itermonthdates(year, month)
               if d.weekday() == 4 and d.month == month]
    return fridays[2]


def fetch_im(cfg_im):
    """中证1000 现货 + IM 远季合约 → 年化贴水。

    远季合约 = 当前之后的第二个季月（3/6/9/12）合约，如 IM2703 = 2027-03。
    """
    today = pd.Timestamp.today()
    spot_df = ak.stock_zh_index_daily(symbol=cfg_im["spot_index"])
    spot = spot_df.iloc[-1]

    quarters = []
    y, m = today.year, today.month
    for yy in (y, y + 1):
        for mm in (3, 6, 9, 12):
            if (yy, mm) > (y, m):
                quarters.append((yy, mm))
    fy, fm = quarters[1] if len(quarters) > 1 else quarters[0]
    code = f"IM{str(fy)[2:]}{fm:02d}"

    fut_df = ak.futures_zh_daily_sina(symbol=code)
    fut = fut_df.iloc[-1]

    expiry = _third_friday(fy, fm)
    data_date = pd.Timestamp(str(fut["date"])).date()
    days = max((expiry - data_date).days, 1)
    discount = (float(spot["close"]) - float(fut["close"])) / float(spot["close"])
    ann = discount / days * 365 * 100

    d = str(spot["date"])
    return {
        "im_spot": {"value": float(spot["close"]), "date": d},
        "im_fut": {"value": float(fut["close"]), "date": str(fut["date"]), "code": code},
        "im_discount_ann": {"value": round(ann, 2), "date": d},
    }


def fetch_csi1000_pe(cfg_im):
    """中证1000 PE-TTM 与五年分位（乐咕乐股月度数据，61 个点的五年窗口）。"""
    df = ak.stock_index_pe_lg(symbol=cfg_im["pe_index_name"])
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期")
    cur = float(df["滚动市盈率"].iloc[-1])
    last5 = df[df["日期"] >= df["日期"].max() - pd.Timedelta(days=365 * 5)]
    pct = float((last5["滚动市盈率"] <= cur).mean() * 100)
    d = df["日期"].iloc[-1].strftime("%Y-%m-%d")
    return {
        "csi1000_pe": {"value": round(cur, 2), "date": d},
        "csi1000_pe_pct5": {"value": round(pct, 0), "date": d},
    }


# ---------------------------------------------------------------- 历史序列（分位计算用）

def fetch_dfii10_series():
    """FRED DFII10 全历史（2003 至今）。返回 [(date_str, value)...] 升序。"""
    import requests
    from io import StringIO

    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10", timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    vals = pd.to_numeric(df["DFII10"], errors="coerce")  # 缺失值 '.' 或空 → NaN
    df = df[vals.notna()]
    return [(str(d), float(v)) for d, v in zip(df["observation_date"], vals[vals.notna()])]


def fetch_gs_ratio_series():
    """金银比历史日线：新浪伦敦金/伦敦银（futures_foreign_hist，2006 至今）。

    返回 [(date_str, ratio)...] 升序。数据量小（~5k 行），每次构建直接拉，无需缓存。
    """
    g = ak.futures_foreign_hist(symbol="XAU")[["date", "close"]]
    s = ak.futures_foreign_hist(symbol="XAG")[["date", "close"]]
    df = g.merge(s, on="date", suffixes=("_g", "_s"))
    df = df[(df["close_g"] > 0) & (df["close_s"] > 0)]
    return [(str(d), round(float(a) / float(b), 2))
            for d, a, b in zip(df["date"], df["close_g"], df["close_s"])]
