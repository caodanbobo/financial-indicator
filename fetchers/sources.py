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
    df = df[df["DFII10"] != "."]
    last = df.iloc[-1]
    return {"tips": {"value": float(last["DFII10"]), "date": str(last["observation_date"])}}


def fetch_fx_macro():
    """汇率（复用 fetch_fx），拆成宏观区块需要的两条。"""
    fx = fetch_fx()
    return {
        "usd_cny": {"value": fx["USD"], "date": fx["date"]},
        "jpy_cny": {"value": fx["JPY"], "date": fx["date"]},
    }
