# -*- coding: utf-8 -*-
"""网络环境处理（阶段 0 实测踩坑后的方案，详见 阶段0_数据源实测报告.md）。

- 本机 macOS 系统代理会被 requests 自动拾取，而东财等境内接口经代理会被拒，
  因此启动时设置 NO_PROXY='*' 强制直连；必须在发任何请求前设置。
- 系统代理要先于 NO_PROXY 抓取，否则 getproxies() 只会返回 {'no': '*'}。
"""

import os
import urllib.request

_SYSTEM_PROXIES = urllib.request.getproxies()

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def system_proxy():
    """返回启动时抓到的系统代理（proxies dict 或 None）。"""
    http = _SYSTEM_PROXIES.get("https") or _SYSTEM_PROXIES.get("http")
    if http:
        if not http.startswith("http"):
            http = "http://" + http
        return {"http": http, "https": http}
    return None


def get_json(url, timeout=20, prefer_proxy=False):
    """GET JSON，代理/直连自动互备。

    prefer_proxy=True（如 CoinGecko，本机直连被墙）：先系统代理后直连；
    否则先直连后代理。Actions 上无系统代理，自然走直连。
    """
    import requests

    proxy = system_proxy()
    attempts = [(True, proxy), (False, None)] if (prefer_proxy and proxy) else [(False, None), (True, proxy)]
    last = None
    for use_proxy, p in attempts:
        if use_proxy and not p:
            continue
        for _ in range(2):
            try:
                r = requests.get(url, proxies=p if use_proxy else None, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
    raise last
