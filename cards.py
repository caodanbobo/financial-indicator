# -*- coding: utf-8 -*-
"""社交卡片 PNG 导出：每个信息块一张竖版卡片（800px 宽，Pillow 绘制，零外部资源）。

spec 由 main.social_card_specs 构建，三种版式：
  anchor    大读数 + 走势
  indicator 读数 + 区位灯 + 走势 + 门槛（可附明细行）
  table     纯明细表
找不到 CJK 字体时跳过导出并告警，不影响页面生成。
"""

import os
import re

from PIL import Image, ImageDraw, ImageFont

from generate_site import ZONE_COLORS

W = 800
MARGIN = 56
CHART_RIGHT_PAD = 92  # 走势图右侧留给阈值标注
BG = "#ffffff"
INK = "#1f2328"
GRAY = "#4b5563"
MUTED = "#9aa0a6"
LINE = "#eef0f2"
SERIES = "#64748b"

_FONT_FILES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]

# emoji 在 Pillow 里渲染不可靠，卡片用纯文字 + 色块表达灯态
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")

_font_path = next((p for p in _FONT_FILES if os.path.exists(p)), None)


def _clean(s):
    return _EMOJI.sub("", str(s)).strip()


def _font(size):
    if not _font_path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(_font_path, size)
    except OSError:
        return ImageFont.load_default()


def _hex(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _dashed(dr, x1, y1, x2, y2, fill, width=2, dash=9, gap=7):
    dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if dist <= 0:
        return
    dx, dy = (x2 - x1) / dist, (y2 - y1) / dist
    pos = 0.0
    while pos < dist:
        end = min(pos + dash, dist)
        dr.line([x1 + dx * pos, y1 + dy * pos, x1 + dx * end, y1 + dy * end],
                fill=fill, width=width)
        pos = end + gap


def _wrap(dr, text, font, max_w):
    """按字符贪心换行（中文无空格）。"""
    lines, cur = [], ""
    for ch in str(text):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and dr.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def _chart_h(points, thresholds):
    pts = [(d, v) for d, v in points if v is not None]
    return 240 if len(pts) >= 2 else 0


def _draw_chart(dr, y, points, thresholds):
    """走势小图。返回绘制后的 y。"""
    pts = [(d, v) for d, v in points if v is not None]
    if len(pts) < 2:
        return y
    h = _chart_h(pts, thresholds)
    x = MARGIN
    w = W - MARGIN * 2 - CHART_RIGHT_PAD
    vals = [v for _, v in pts] + [t[0] for t in thresholds]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad

    def xy(i, v):
        px = x + i * w / (len(pts) - 1)
        py = y + h - (v - lo) / (hi - lo) * h
        return px, py

    for tv, color, label in thresholds:
        if not (lo <= tv <= hi):
            continue
        _, ty = xy(0, tv)
        _dashed(dr, x, ty, x + w, ty, _hex(color), width=2)
        dr.text((x + w + 8, ty), _clean(label), font=_font(18), fill=_hex(color), anchor="lm")

    poly = [xy(i, v) for i, (_, v) in enumerate(pts)]
    dr.line(poly, fill=_hex(SERIES), width=3, joint="curve")
    lx, ly = poly[-1]
    dr.ellipse([lx - 6, ly - 6, lx + 6, ly + 6], fill=_hex(SERIES))
    return y + h


def _draw_rows(dr, y, rows):
    f = _font(24)
    fm = _font(19)
    for r in rows:
        dr.line([MARGIN, y, W - MARGIN, y], fill=_hex(LINE), width=1)
        y += 12
        dr.text((MARGIN, y), _clean(r["label"]), font=f, fill=_hex(INK), anchor="la")
        dr.text((W - MARGIN, y), _clean(r.get("value", "")), font=_font(26),
                fill=_hex(INK), anchor="ra")
        y += 34
        if r.get("meta"):
            dr.text((W - MARGIN, y), _clean(r["meta"]), font=fm, fill=_hex(MUTED), anchor="ra")
        y += 30
    return y


def _layout_body(dr, spec, y):
    """在 (0, y) 起绘制主体内容，返回内容底部 y。"""
    f_title = _font(40)
    f_label = _font(24)
    f_value = _font(72)
    f_body = _font(26)
    f_chip = _font(28)
    f_small = _font(22)

    dr.text((MARGIN, y), _clean(spec["title"]), font=f_title, fill=_hex(INK), anchor="la")
    y += 64

    kind = spec.get("kind", "indicator")
    if kind in ("anchor", "indicator"):
        if spec.get("metric_label"):
            dr.text((MARGIN, y), _clean(spec["metric_label"]), font=f_label,
                    fill=_hex(GRAY), anchor="la")
            y += 40
        dr.text((MARGIN, y), _clean(spec.get("value_text", "—")), font=f_value,
                fill=_hex(INK), anchor="la")
        y += 100

        if kind == "indicator":
            color = _hex(spec.get("zone_color") or ZONE_COLORS["gray"])
            chip_text = _clean(spec.get("zone_text", ""))
            if chip_text:
                lines = _wrap(dr, chip_text, f_chip, W - MARGIN * 2 - 44)
                chip_h = 22 + len(lines) * 36
                tw = max(dr.textlength(ln, font=f_chip) for ln in lines)
                dr.rounded_rectangle([MARGIN, y, MARGIN + tw + 44, y + chip_h],
                                     radius=12, fill=color)
                ty = y + chip_h / 2 - (len(lines) - 1) * 18
                for ln in lines:
                    dr.text((MARGIN + 22, ty), ln, font=f_chip, fill="#ffffff", anchor="lm")
                    ty += 36
                y += chip_h + 18
            if spec.get("distance_text"):
                for ln in _wrap(dr, _clean(spec["distance_text"]), f_body, W - MARGIN * 2):
                    dr.text((MARGIN, y), ln, font=f_body, fill=_hex(INK), anchor="la")
                    y += 36
            if spec.get("sub_text"):
                for ln in _wrap(dr, _clean(spec["sub_text"]), f_label, W - MARGIN * 2):
                    dr.text((MARGIN, y), ln, font=f_label, fill=_hex(GRAY), anchor="la")
                    y += 34

        ch = _chart_h(spec.get("points", []), spec.get("spark_thresholds", []))
        if ch:
            y += 14
            y = _draw_chart(dr, y, spec.get("points", []), spec.get("spark_thresholds", []))
            y += 10

        if kind == "indicator" and spec.get("threshold_text"):
            for ln in _wrap(dr, _clean(spec["threshold_text"]), f_small, W - MARGIN * 2):
                dr.text((MARGIN, y), ln, font=f_small, fill=_hex(MUTED), anchor="la")
                y += 30

    if spec.get("rows"):
        y += 16
        y = _draw_rows(dr, y, spec["rows"])

    return y + 24


def _meta_line(spec):
    meta = f"数据日期 {spec.get('date', '—')}"
    if spec.get("stale"):
        meta += "　⚠ 数据陈旧"
    if spec.get("note"):
        meta += f"　{_clean(spec['note'])}"
    return _clean(meta)


def _render(spec, min_h=1080, standalone=True):
    """画一个信息块。standalone=True 为独立卡片（底部钉免责声明），
    False 为长图用的紧凑块（内容随高度收，meta 紧跟内容）。"""
    tall = Image.new("RGB", (W, 2600), _hex(BG))
    dr = ImageDraw.Draw(tall)

    zone_color = spec.get("zone_color") or ZONE_COLORS.get(spec.get("zone", "gray"), ZONE_COLORS["gray"])
    dr.rectangle([0, 0, W, 10], fill=_hex(zone_color))

    y_end = _layout_body(dr, spec, 56)

    if standalone:
        h = max(min_h, y_end + 110)
        img = Image.new("RGB", (W, h), _hex(BG))
        img.paste(tall.crop((0, 0, W, min(y_end + 10, 2600))), (0, 0))
        dr = ImageDraw.Draw(img)
        fy = h - 88
        dr.line([MARGIN, fy - 18, W - MARGIN, fy - 18], fill=_hex(LINE), width=1)
        dr.text((MARGIN, fy), _meta_line(spec), font=_font(20), fill=_hex(MUTED), anchor="la")
        dr.text((MARGIN, fy + 30), "数据为公开行情整理，不构成投资建议 · 投资指标仪表盘",
                font=_font(19), fill=_hex(MUTED), anchor="la")
        return img

    y_end += 10
    dr.line([MARGIN, y_end, W - MARGIN, y_end], fill=_hex(LINE), width=1)
    y_end += 12
    dr.text((MARGIN, y_end), _meta_line(spec), font=_font(20), fill=_hex(MUTED), anchor="la")
    y_end += 34
    img = Image.new("RGB", (W, y_end + 12), _hex(BG))
    img.paste(tall.crop((0, 0, W, y_end)), (0, 0))
    return img


def export(specs, out_dir):
    """把 specs 逐张导出为 out_dir/<id>.png，返回写出的路径列表。"""
    if _font_path is None:
        print("[warn] 未找到中文字体，跳过社交卡片 PNG 导出（页面不受影响）")
        return []
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for spec in specs:
        path = os.path.join(out_dir, f"{spec['id']}.png")
        _render(spec).save(path)
        written.append(path)
    return written


def export_long(specs, out_path, generated_at):
    """全部信息块拼成一张竖版长图（灰底 + 圆角白卡），返回输出路径。"""
    if _font_path is None:
        print("[warn] 未找到中文字体，跳过长图导出（页面不受影响）")
        return None

    side, gap = 40, 24
    width = W + side * 2
    blocks = [_render(s, min_h=0, standalone=False) for s in specs]

    header_h = 150
    total_h = header_h + sum(b.height + gap for b in blocks) + 70
    img = Image.new("RGB", (width, total_h), _hex("#f6f7f8"))
    dr = ImageDraw.Draw(img)

    dr.text((side, 40), "投资指标仪表盘", font=_font(42), fill=_hex(INK), anchor="la")
    dr.text((side, 102), f"快照 {generated_at}", font=_font(22), fill=_hex(MUTED), anchor="la")

    y = header_h
    for b in blocks:
        mask = Image.new("L", b.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, b.size[0] - 1, b.size[1] - 1],
                                               radius=14, fill=255)
        img.paste(b, (side, y), mask)
        y += b.height + gap

    dr.text((side, y + 8), "数据为公开行情整理，不构成投资建议 · 投资指标仪表盘",
            font=_font(20), fill=_hex(MUTED), anchor="la")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path)
    return out_path
