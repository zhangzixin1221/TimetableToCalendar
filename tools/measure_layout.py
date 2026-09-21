# -*- coding: utf-8 -*-
"""打印各区块的自然高度，用于设计最小窗口尺寸。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from timetable import ui_kit as ui
from timetable.gui import App

app = App()
app.update()
app.update_idletasks()

print("SCALE = %.2f  屏幕 %dx%d  窗口 %dx%d  请求 %dx%d  最小 %dx%d"
      % (ui.SCALE, app.winfo_screenwidth(), app.winfo_screenheight(),
         app.winfo_width(), app.winfo_height(),
         app.winfo_reqwidth(), app.winfo_reqheight(),
         app.minsize()[0], app.minsize()[1]))

for name in ("_hdr", "_cfg", "_lower", "_ftr"):
    w = getattr(app, name, None)
    if w is not None:
        print("%-8s 类=%-6s 请求高=%4d 实际高=%4d"
              % (name, w.winfo_class(), w.winfo_reqheight(), w.winfo_height()))

for name, w in (("tree", app.tree), ("txt", app.txt)):
    print("%-8s 请求高=%4d 实际高=%4d" % (name, w.winfo_reqheight(), w.winfo_height()))

app.destroy()
