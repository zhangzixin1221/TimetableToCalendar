# -*- coding: utf-8 -*-
"""通过真实的 Windows 消息（MoveWindow）拖拽改变窗口大小，验证「竖直方向能自由缩放 +
内部内容跟着变」。resize_test.py 是直接调 Tk 的 geometry()，这个脚本走的是窗口管理器的
那条路（会触发真正的 WM_SIZE / Configure），更接近用户拖边框的动作。

用法： python resize_wm_test.py
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from timetable import ui_kit as ui
from timetable.gui import App

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()

app = App()
app.update()
hwnd = app.winfo_id()

# 取的是 Tk 内容窗口的父窗口（真正带边框的那个顶层窗口）
GetParent = u32.GetParent
GetParent.restype = ctypes.c_void_p
top = GetParent(hwnd) or hwnd

minw, minh = app.minsize()
print("=" * 78)
print("真实拖拽缩放检查（MoveWindow 走窗口管理器）")
print("=" * 78)
print("最小尺寸 %dx%d  屏幕 %dx%d" % (minw, minh, app.winfo_screenwidth(),
                                      app.winfo_screenheight()))

# 从默认大小开始，先往小缩再往大放，最后回到默认。
# 注意：MoveWindow 设的是「含边框标题栏的外框尺寸」，客户区会小一些（本机差约 71 像素），
# 所以要测「客户区刚好放下全部内容」，外框要开到 1894 左右。
sizes = [(2482, 1894), (2400, 1500), (2400, 1100), (2400, 961), (2200, 900),
         (1682, 961), (3000, 1880), (2482, 1894)]

bad = 0
prev_h = None
for (W, H) in sizes:
    u32.MoveWindow(ctypes.c_void_p(top), 20, 20, W, H, True)
    app.update()
    time.sleep(0.25)
    app.update()

    cw, ch = app.winfo_width(), app.winfo_height()
    inner_h = app._inner.winfo_height()
    tree_h = app.tree.winfo_height()
    log_h = app.txt.winfo_height()
    print("\n目标外框 %dx%d -> 客户区 %dx%d" % (W, H, cw, ch))
    print("    中间区高 %d（内容自然高 %d）滚动条 %s"
          % (inner_h, app._inner.winfo_reqheight(),
             "显示" if app._sb_shown else "隐藏"))
    print("    预览表 %d  日志 %d  顶栏 %d  底栏 %d"
          % (tree_h, log_h, app._hdr.winfo_height(), app._ftr.winfo_height()))

    problems = []
    # 竖直方向必须真的被改动了（不是被锁死）。
    # 请求的外框高减去边框标题栏 ≈ 预期客户区高；只有它明显高于最小高度时才要求变化，
    # 否则窗口本就该停在最小尺寸上。
    if prev_h is not None and (H - 71) > minh + ui.px(4) and abs(ch - prev_h) < 2:
        problems.append("高度没有变化，可能被锁死")
    prev_h = ch
    # 控件不许被压扁
    if tree_h <= ui.px(60):
        problems.append("预览表被压扁 %d" % tree_h)
    if log_h <= ui.px(30):
        problems.append("日志被压扁 %d" % log_h)
    # 滚动条状态必须和内容高度一致
    want_sb = app._inner.winfo_reqheight() > app._canvas.winfo_height() + app._sc_slack
    if want_sb != app._sb_shown:
        problems.append("滚动条状态不对 需要=%s 实际=%s" % (want_sb, app._sb_shown))
    if app._sb_shown and not (app._canvas.yview()[1] <= 1.0):
        problems.append("yview 异常 %s" % (app._canvas.yview(),))
    if problems:
        bad += len(problems)
        for p in problems:
            print("    !! %s" % p)
    else:
        print("    正常")

# 拖到最小尺寸以下：窗口应停在最小尺寸，而不是被压没
u32.MoveWindow(ctypes.c_void_p(top), 20, 20, 800, 400, True)
app.update()
time.sleep(0.25)
app.update()
print("\n尝试拖到 800x400 -> 实际客户区 %dx%d（最小 %dx%d）"
      % (app.winfo_width(), app.winfo_height(), minw, minh))
if app.winfo_height() < minh - 4 or app.winfo_width() < minw - 4:
    print("    !! 窗口小于最小尺寸，说明最小尺寸没生效")
    bad += 1
else:
    print("    正常：停在了最小尺寸，内容靠滚动条查看")

# 内容装不下时：翻到底应该能看到日志卡（而不是滚不动或露白）
if app._sb_shown:
    app._canvas.yview_moveto(1.0)
    app.update()
    time.sleep(0.2)
    app.update()
    cy, chh = app._canvas.winfo_rooty(), app._canvas.winfo_height()
    ly, lh = app._lower.winfo_rooty(), app._lower.winfo_height()
    yv = app._canvas.yview()
    print("\n翻到底：日志卡屏幕 y=%d~%d，画布可视 y=%d~%d，yview=%.3f~%.3f"
          % (ly, ly + lh, cy, cy + chh, yv[0], yv[1]))
    if not (cy <= ly and ly + lh <= cy + chh + 2):
        print("    !! 翻到底后日志卡没有完整落在可视区里")
        bad += 1
    elif yv[1] < 0.999:
        print("    !! 没有滚到底")
        bad += 1
    else:
        print("    正常：日志可见")

app.destroy()
print()
print("=" * 78)
print("问题总数: %d  ->  %s" % (bad, "通过" if bad == 0 else "需要修复"))
print("=" * 78)
sys.exit(0 if bad == 0 else 1)
