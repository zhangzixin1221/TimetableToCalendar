# -*- coding: utf-8 -*-
"""缩放窗口的回归检查：把窗口调整成多个尺寸，看布局、滚动条和圆角底图是否正常。

用法： python resize_test.py

检查项：
  1. 竖直方向真的能自由缩放（最小尺寸远小于「内容自然高度」，且能设到该尺寸）
  2. 窗口里没有 0/1 像素的可见控件（纯装饰的 1px 分隔 Frame 除外）
  3. 内容装得下时不出现滚动条；装不下时滚动条出现，且出现后内容不再被压扁
  4. 卡片的圆角底图跟着尺寸走（9 宫格由多块拼成，取所有图元的并集包围盒）
  5. 顶栏 / 底栏 / 预览表在任何尺寸下都还在窗口里（滚动的是中间内容区）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from timetable import ui_kit as ui
from timetable.gui import App

# 逻辑尺寸（会乘以 DPI 缩放）。含极小尺寸，专门测滚动兜底。
SIZES = [(680, 470), (840, 480), (1000, 600), (1080, 640), (1240, 900), (1400, 950),
         (1600, 1100), (1240, 900), (900, 560), (1920, 1200), (1080, 640)]

print("=" * 78)
print("缩放窗口回归检查")
print("=" * 78)

app = App()
app.update()

minw, minh = app.minsize()
screen_h = app.winfo_screenheight()
need_h = app._inner.winfo_reqheight()
print("窗口最小尺寸 %dx%d（逻辑 %dx%d）" % (minw, minh, minw // 2, minh // 2))
print("屏幕 %dx%d  中间内容自然高 %d" % (app.winfo_screenwidth(), screen_h, need_h))
print("内容自然高 + 顶栏 + 底栏 = %d" % (need_h + app._hdr.winfo_reqheight()
                                        + app._ftr.winfo_reqheight()))

bad = 0

# 竖直方向必须能真正拖动：最小高度要明显小于「内容自然高度」，否则拖不动
if minh > need_h * 0.75:
    print("!! 最小高度 %d 太接近内容自然高 %d，竖直方向会被锁死" % (minh, need_h))
    bad += 1
else:
    print("竖直方向可自由缩放：最小 %d < 内容自然高 %d" % (minh, need_h))

# 打开时的默认尺寸：应当跟着屏幕走，面积约为整屏的四分之一（宽高各一半），
# 并且不小于最小尺寸、不超出屏幕
screen_w = app.winfo_screenwidth()
sw, sh = app.winfo_width(), app.winfo_height()
ratio = float(sw * sh) / float(screen_w * screen_h)
print("默认窗口 %dx%d（逻辑 %dx%d）≈ 屏幕面积的 %.1f%%"
      % (sw, sh, sw // 2, sh // 2, ratio * 100))
if not (0.15 <= ratio <= 0.35):
    print("!! 默认窗口面积占比 %.1f%% 偏离「四分之一屏」太多" % (ratio * 100))
    bad += 1
elif sw < minw - 2 or sh < minh - 2 or sw > screen_w or sh > screen_h:
    print("!! 默认尺寸不在 [最小尺寸, 屏幕] 范围内")
    bad += 1
else:
    print("    默认尺寸合适：约四分之一屏，且不小于最小尺寸")


def find_cards(w, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        if c.winfo_class() == "Frame" and hasattr(c, "_bg_canvas"):
            out.append(c)
        find_cards(c, out)
    return out


def decorative(c):
    """纯装饰的 1px 分隔线：Frame、自己没有可见子控件。"""
    if c.winfo_class() != "Frame":
        return False
    if c.winfo_width() > 1 and c.winfo_height() > 1:
        return False
    return not any(k.winfo_ismapped() for k in c.winfo_children())


def inside(w, root):
    """w 是否是 root（含自身）的后代。"""
    while w is not None:
        if w is root:
            return True
        try:
            w = w.master
        except Exception:
            return False
    return False


for (lw, lh) in SIZES:
    pw, ph = ui.px(lw), ui.px(lh)
    app.geometry("%dx%d" % (pw, ph))
    app.update()
    app.update_idletasks()

    problems = []
    win_w, win_h = app.winfo_width(), app.winfo_height()

    # 1) 被压成 0/1 像素的可见控件
    def walk(w2):
        for c in w2.winfo_children():
            if c.winfo_ismapped() and not decorative(c):
                if c.winfo_width() <= 1 or c.winfo_height() <= 1:
                    problems.append("零尺寸 %s %s" % (c.winfo_class(), str(c)[:26]))
                # 滚动的中间区里，“跑到可视区外”是正常的；其余控件不许出界
                if inside(c, app._inner):
                    lim_h = app._inner.winfo_height()
                else:
                    lim_h = win_h
                if c.winfo_y() + c.winfo_height() > lim_h + 2 or \
                   c.winfo_x() + c.winfo_width() > win_w + 2:
                    problems.append("越界 %s %s" % (c.winfo_class(), str(c)[:26]))
            walk(c)
    walk(app)

    # 2) 圆角底图跟得上尺寸
    for card in find_cards(app):
        cw, ch = card.winfo_width(), card.winfo_height()
        boxes = [card._bg_canvas.bbox(i) for i in card._bg_canvas.find_all()]
        boxes = [b for b in boxes if b]
        if not boxes:
            problems.append("卡片底图缺失")
            continue
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))
        iw, ih = box[2] - box[0], box[3] - box[1]
        if abs(iw - cw) > 3 or abs(ih - ch) > 3:
            problems.append("卡片底图未跟上尺寸 卡片%dx%d 图%dx%d" % (cw, ch, iw, ih))

    # 3) 滚动条状态要和「内容高度 vs 可视高度」一致
    view_h = app._canvas.winfo_height()
    need = app._inner.winfo_reqheight()
    want_sb = need > view_h + app._sc_slack
    if want_sb != app._sb_shown:
        problems.append("滚动条状态不对 需要=%s 实际=%s (内容%d 可视%d)"
                        % (want_sb, app._sb_shown, need, view_h))
    if app._sb_shown and app._vsb.winfo_width() <= 1:
        problems.append("滚动条显示了但宽度是 0")

    # 4) 顶栏 / 底栏 / 预览表必须始终在窗口里
    for name, wdg in (("顶栏", app._hdr), ("底栏", app._ftr),
                      ("预览表", app.tree), ("日志", app.txt)):
        if wdg.winfo_height() <= 1:
            problems.append("%s 被压没了" % name)

    print("\n%4dx%-4d (物理 %dx%d)" % (lw, lh, pw, ph))
    print("    窗口 %dx%d  可视高 %d  内容高 %d  滚动条 %s"
          % (win_w, win_h, view_h, need, "显示" if app._sb_shown else "隐藏"))
    print("    表格 %dx%d  日志 %dx%d  顶栏 %d  底栏 %d"
          % (app.tree.winfo_width(), app.tree.winfo_height(),
             app.txt.winfo_width(), app.txt.winfo_height(),
             app._hdr.winfo_height(), app._ftr.winfo_height()))
    if problems:
        bad += len(problems)
        for p in problems[:6]:
            print("    !! %s" % p)
    else:
        print("    布局正常")

# 5) 拉高窗口时预览表要跟着变高（内容随尺寸变化，而不是固定死）
app.geometry("%dx%d" % (ui.px(1240), ui.px(880)))
app.update()
h1 = app.tree.winfo_height()
app.geometry("%dx%d" % (ui.px(1240), screen_h - ui.px(24)))
app.update()
app.update_idletasks()
h2 = app.tree.winfo_height()
print("\n拉高窗口：预览表 %d -> %d" % (h1, h2))
if h2 < h1:
    print("    !! 窗口变高时预览表没有变高")
    bad += 1
else:
    print("    内容随窗口变化：OK")

# ---- 小屏降级检查：假装屏幕只有 1366x768（200% 缩放下物理 2732x1536）----
print()
print("-" * 78)
print("小屏检查（模拟屏幕 1366x768 逻辑像素）")
print("-" * 78)
_orig_h = App.winfo_screenheight
_orig_w = App.winfo_screenwidth
App.winfo_screenheight = lambda self: 1536
App.winfo_screenwidth = lambda self: 2732
try:
    small = App()
    small.update()
    sw, sh = small.minsize()
    print("最小尺寸 %dx%d  窗口 %dx%d  滚动条 %s"
          % (sw, sh, small.winfo_width(), small.winfo_height(),
             "显示" if small._sb_shown else "隐藏"))
    ok = True
    if sh > 1536 or small.winfo_height() > 1536:
        print("    !! 超出屏幕 最小高 %d 窗口高 %d" % (sh, small.winfo_height()))
        ok = False
    if small.tree.winfo_height() <= ui.px(40) or small.txt.winfo_height() <= ui.px(30):
        print("    !! 控件被压没了 表格 %d 日志 %d"
              % (small.tree.winfo_height(), small.txt.winfo_height()))
        ok = False
    # 屏幕装不下内容 → 必须给滚动条，且能滚到底
    if not small._sb_shown:
        print("    !! 内容装不下却没有滚动条")
        ok = False
    else:
        small._canvas.yview_moveto(1.0)
        small.update()
        if small._canvas.yview()[1] < 0.999:
            print("    !! 滚不到底 yview=%s" % (small._canvas.yview(),))
            ok = False
    if ok:
        print("    降级正常：内容保持自然高 %d，靠滚动条查看 表格 %d 日志 %d"
              % (small._inner.winfo_reqheight(), small.tree.winfo_height(),
                 small.txt.winfo_height()))
    else:
        bad += 1
    small.destroy()
finally:
    App.winfo_screenheight = _orig_h
    App.winfo_screenwidth = _orig_w

app.destroy()
print()
print("=" * 78)
print("问题总数: %d  ->  %s" % (bad, "通过" if bad == 0 else "需要修复"))
print("=" * 78)
sys.exit(0 if bad == 0 else 1)
