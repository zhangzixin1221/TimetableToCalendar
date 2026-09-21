# -*- coding: utf-8 -*-
"""验证圆角边缘的「暗角」问题是否真的修好了。

原理：圆角矩形是纯色 + 抗锯齿边缘。边缘上那些半透明像素，正常情况下它的 RGB 应该
**仍然等于填充色**，只有 alpha 在变化。如果 RGB 被拉向黑色，那就是透明区域的黑色
渗进来了 —— 视觉上就是暗角。

所以判据很简单：取所有 0 < alpha < 255 的边缘像素，比较它们的 RGB 与填充色的差距。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PIL import Image, ImageDraw

SS = 4
W_PT, H_PT, R_PT = 200, 60, 16
FILL = (0x4F, 0x46, 0xE5)      # #4F46E5 主色
EDGE = (0xD0, 0xD5, 0xDD)      # #D0D5DD 描边色


def old_way(fill, outline=None, ow=1):
    """修复前的写法：RGBA 画布 + 透明黑背景 + 直接缩放。"""
    W, H, r = W_PT * SS, H_PT * SS, R_PT * SS
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(
        [0, 0, W - 1, H - 1], radius=r, fill=fill, outline=outline,
        width=max(1, ow * SS) if outline else 0)
    return im.resize((W_PT, H_PT), Image.LANCZOS)


def new_way(fill, outline=None, ow=1):
    """修复后的写法：蒙版走 L 通道、颜色走不透明 RGB，各自缩放后合成。"""
    W, H, r = W_PT * SS, H_PT * SS, R_PT * SS
    box = [0, 0, W - 1, H - 1]
    stroke = max(1, ow * SS) if outline else 0
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=r, fill=255)
    rgb = Image.new("RGB", (W, H), outline or fill)
    ImageDraw.Draw(rgb).rounded_rectangle(box, radius=r, fill=fill,
                                          outline=outline, width=stroke)
    mask = mask.resize((W_PT, H_PT), Image.LANCZOS)
    rgb = rgb.resize((W_PT, H_PT), Image.LANCZOS)
    out = rgb.convert("RGBA")
    out.putalpha(mask)
    return out


def edge_stats(im, base):
    """统计边缘半透明像素的 RGB 与基准色的偏差。"""
    px = im.load()
    n = 0
    worst = 0
    total = 0
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if 10 < a < 245:                       # 抗锯齿过渡带
                n += 1
                d = max(abs(r - base[0]), abs(g - base[1]), abs(b - base[2]))
                total += d
                worst = max(worst, d)
    return n, (total / n if n else 0), worst


def corner_min(im, region=26):
    """看左上角区域 RGB 的最小值 —— 暗角会让它明显小于填充色。"""
    px = im.load()
    lo = 255
    for y in range(region):
        for x in range(region):
            r, g, b, a = px[x, y]
            if a > 10:
                lo = min(lo, max(r, g, b))
    return lo


print("=" * 74)
print("情况 A：纯色按钮（无描边），填充色 #4F46E5 = (79, 70, 229)")
print("=" * 74)
for name, fn in (("修复前", old_way), ("修复后", new_way)):
    im = fn(FILL)
    n, avg, worst = edge_stats(im, FILL)
    print("  %-6s 边缘像素 %4d 个 | RGB 与填充色平均偏差 %6.2f | 最差 %3d | 左上角最暗值 %3d"
          % (name, n, avg, worst, corner_min(im)))
print("  （偏差越小越好；修复前透明黑渗入会让边缘 RGB 明显偏暗）")

print()
print("=" * 74)
print("情况 B：带描边的次按钮，填充 #FFFFFF，描边 #D0D5DD")
print("=" * 74)
for name, fn in (("修复前", old_way), ("修复后", new_way)):
    im = fn((255, 255, 255), EDGE)
    n, avg, worst = edge_stats(im, EDGE)
    print("  %-6s 边缘像素 %4d 个 | RGB 与描边色平均偏差 %6.2f | 最差 %3d | 左上角最暗值 %3d"
          % (name, n, avg, worst, corner_min(im)))

print()
print("=" * 74)
print("结论")
print("=" * 74)
a_old = corner_min(old_way(FILL))
a_new = corner_min(new_way(FILL))
print("  纯色按钮左上角最暗像素：修复前 %d -> 修复后 %d（填充色最大分量 229）" % (a_old, a_new))
print("  修复前最暗值明显低于填充色 => 圆角处发暗；修复后应与填充色一致（仅 alpha 变化）")
ok = a_new >= 229 - 6
# 只用 ASCII，避免 GBK 控制台编码失败
print("  判定：%s" % ("通过 - 圆角边缘不再有暗角" if ok else "失败 - 仍需检查"))
raise SystemExit(0 if ok else 1)
