# -*- coding: utf-8 -*-
"""分析截图：缩放后界面有没有“没画出来”的区域。

判断依据：
  1. 近黑像素（亮度 < 25）——Tk 的控件都是主题配色，正常渲染不该出现近黑；
     出现成片近黑通常是控件没重绘（露出未初始化的黑底）。
  2. 逐行统计“前景像素”（偏离背景色的像素），打印纵向分布，
     正常情况下从上到下都该有内容（标题带 / 配置卡 / 预览表 / 日志）。
用法： python analyze_shot.py shot.png
"""
import os
import sys
from collections import Counter

from PIL import Image

path = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
im = Image.open(path).convert("RGB")
w, h = im.size
px = im.load()

cnt = Counter()
for y in range(0, h, 3):
    for x in range(0, w, 3):
        cnt[px[x, y]] += 1
top = cnt.most_common(4)
print("图片 %dx%d" % (w, h))
print("主色：" + "  ".join("%s %d次" % (c, n) for c, n in top))
bg = top[0][0]


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


dark = 0
dark_rows = Counter()
rows = []
for y in range(h):
    ink = 0
    for x in range(0, w, 2):
        c = px[x, y]
        if lum(c) < 10:          # 纯黑：主题最深的导航蓝是 (16,24,40)，亮度 23.5，不会误判
            dark += 1
            dark_rows[y] += 1
        if abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2]) > 24:
            ink += 1
    rows.append(ink)

print("纯黑像素（亮度<10，采样全图）: %d" % dark)
if dark_rows:
    ys = sorted(dark_rows)
    print("    出现在行 %d ~ %d，最多的前几行: %s"
          % (ys[0], ys[-1], dark_rows.most_common(5)))

bands = 24
print("纵向内容分布（每格 = 1/%d 高度，数字是前景像素数）:" % bands)
for i in range(bands):
    y0, y1 = h * i // bands, h * (i + 1) // bands
    seg = rows[y0:y1]
    print("    %2d %5d-%5d  %6d  %s"
          % (i, y0, y1, max(seg), "#" * int(max(seg) / max(1, w / 2 / 40))))

empty = [i for i in range(bands) if max(rows[h * i // bands:h * (i + 1) // bands]) == 0]
if empty:
    print("空白横带: %s（数量 %d）" % (empty, len(empty)))
else:
    print("空白横带: 无 —— 从上到下都有内容")
