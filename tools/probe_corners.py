# -*- coding: utf-8 -*-
"""在截图里找出靛蓝色圆角块，量它圆角处的最暗像素。

用法：
    python probe_corners.py <截图.png> [要跳过的顶部行数]

思路：先按行聚类（行间空隙 > 12px 视为不同元素），把标题带里的主色装饰和下方
真正的按钮分开；再在每个元素内按列聚类。

判据：圆角块是纯色 #4F46E5（亮度约 90.8）画在浅色底上，它的抗锯齿边缘只在
「填充色 ↔ 底色」之间过渡，所以块内最暗像素**不应比填充色更暗**。如果圆角外是
未初始化的黑（暗角），最暗值会明显低于 90.8。
"""
import os
import sys
from PIL import Image

FILL = (0x4F, 0x46, 0xE5)
FILL_LUM = 0.299 * FILL[0] + 0.587 * FILL[1] + 0.114 * FILL[2]   # ≈ 90.8


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def is_indigo(c):
    r, g, b = c[:3]
    return b > r + 40 and b > 140 and r < 150


def cluster(vals, gap):
    vals = sorted(set(vals))
    out, cur = [], [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= gap:
            cur.append(v)
        else:
            out.append((cur[0], cur[-1]))
            cur = [v]
    out.append((cur[0], cur[-1]))
    return out


def main(path, skip_top=0):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    px = im.load()
    print("截图: %s  %dx%d" % (path, W, H))

    pts = [(x, y) for y in range(skip_top, H) for x in range(W) if is_indigo(px[x, y])]
    if not pts:
        print("  没有找到靛蓝色块（跳过顶部 %d 行）" % skip_top)
        return 1

    bands = cluster([y for _, y in pts], 12)
    print("  找到 %d 个靛蓝元素（按行聚类，已跳过顶部 %d 行）" % (len(bands), skip_top))
    bad = 0
    for (y0, y1) in bands:
        sub = [(x, y) for (x, y) in pts if y0 <= y <= y1]
        for (x0, x1) in cluster([x for x, _ in sub], 12):
            blk = [(x, y) for (x, y) in sub if x0 <= x <= x1]
            ys = [y for _, y in blk]
            ymin, ymax = min(ys), max(ys)
            n = max(8, (ymax - ymin) // 4)
            region = [(x, y) for (x, y) in blk if x < x0 + n and y < ymin + n]
            if not region:
                region = blk
            darkest = min(region, key=lambda p: lum(px[p[0], p[1]]))
            d = lum(px[darkest[0], darkest[1]])
            whole = min(lum(px[x, y]) for x, y in blk)
            flag = ""
            if d < FILL_LUM - 12:
                flag = "   <== 偏暗"
                bad += 1
            print("    元素 x=%4d..%4d y=%4d..%4d  (%dx%d)  圆角区最暗 %.1f @ %s   整体最暗 %.1f%s"
                  % (x0, x1, ymin, ymax, x1 - x0 + 1, ymax - ymin + 1, d, darkest, whole, flag))
    print("  填充色 #4F46E5 亮度 = %.1f    偏暗元素数 = %d" % (FILL_LUM, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "probe.png"
    skip = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    sys.exit(main(p, skip))
