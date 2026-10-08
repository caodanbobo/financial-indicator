# -*- coding: utf-8 -*-
"""信号灯计算引擎：纯函数，输入配置 + 读数，输出灯态/门槛/距离。

灯态：red 🔴 停止区 / yellow 🟡 黄灯 / green 🟢 击球区 / green2 🟢🟢 重仓区
失真期由 main 统一处理（灯态强制为 gray），本模块只负责数值判断。
"""

ZONE_LABELS = {
    "red": "🔴 停止区",
    "yellow": "🟡 黄灯 · 正常定投",
    "green": "🟢 击球区",
    "green2": "🟢🟢 重仓区",
    "gray": "⬜ 失真期 · 只记录不触发",
}

NEXT_ZONE = {"red": "yellow", "yellow": "green", "green": "green2", "green2": None}


def eval_spread_card(card_cfg, dividend, anchor):
    """息差型卡片。返回 dict：zone/spread/距离下一档/绝对门槛等。

    规则（阈值均为息差，单位 pp）：
      🔴 息差 < red_below（< clearout_below 时额外标"清仓线"）
      🟡 red_below ≤ 息差 < green_at
      🟢 green_at ≤ 息差 < green2_at
      🟢🟢 息差 ≥ green2_at
    """
    spread = round(dividend - anchor, 4)
    red_below = card_cfg["red_below"]
    green_at = card_cfg["green_at"]
    green2_at = card_cfg["green2_at"]
    clearout_below = card_cfg.get("clearout_below")

    clearout = clearout_below is not None and spread < clearout_below
    if spread < red_below:
        zone = "red"
    elif spread >= green2_at:
        zone = "green2"
    elif spread >= green_at:
        zone = "green"
    else:
        zone = "yellow"

    # 距下一档门槛的距离（息差口径，pp）
    distance = None
    nxt = NEXT_ZONE[zone]
    if nxt == "yellow":
        distance = round(red_below - spread, 2)
    elif nxt == "green":
        distance = round(green_at - spread, 2)
    elif nxt == "green2":
        distance = round(green2_at - spread, 2)

    return {
        "zone": zone,
        "clearout": clearout,
        "spread": round(spread, 2),
        "distance_next": distance,          # None 表示已在最高档
        "thresholds_abs": {                 # 换算成股息率绝对门槛
            "red_below": round(anchor + red_below, 2),
            "green_at": round(anchor + green_at, 2),
            "green2_at": round(anchor + green2_at, 2),
        },
        "thresholds_cfg": {"red_below": red_below, "green_at": green_at, "green2_at": green2_at},
    }


def eval_pe_card(card_cfg, pe):
    """PE 型卡片（值越低越好）。

    规则：🔴 PE ≥ red_at（≥ clearout_above 标"清仓线"）
          🟡 green_at < PE < red_at
          🟢 green2_at < PE ≤ green_at
          🟢🟢 PE ≤ green2_at
    """
    red_at = card_cfg["red_at"]
    green_at = card_cfg["green_at"]
    green2_at = card_cfg["green2_at"]
    clearout_above = card_cfg.get("clearout_above")

    clearout = clearout_above is not None and pe >= clearout_above
    if pe >= red_at:
        zone = "red"
    elif pe <= green2_at:
        zone = "green2"
    elif pe <= green_at:
        zone = "green"
    else:
        zone = "yellow"

    # 距下一档：PE 需要再降多少
    distance = None
    nxt = NEXT_ZONE[zone]
    if nxt == "yellow":
        distance = round(pe - red_at + 0.01, 2)   # 需跌破 red_at 才出红灯区
    elif nxt == "green":
        distance = round(pe - green_at, 2)
    elif nxt == "green2":
        distance = round(pe - green2_at, 2)

    return {
        "zone": zone,
        "clearout": clearout,
        "distance_next": distance,
        "thresholds_cfg": {"red_at": red_at, "green_at": green_at, "green2_at": green2_at},
    }
