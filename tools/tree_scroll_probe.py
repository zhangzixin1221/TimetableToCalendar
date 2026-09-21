# -*- coding: utf-8 -*-
"""验证 ttk.Treeview 是否支持横向滚动（决定「左右缩放后预览表被裁掉」怎么修）。"""
import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from timetable import ui_kit as ui

ui.enable_dpi_awareness()
r = tk.Tk()
ui.init_theme(r)
r.geometry("900x300")

box = tk.Frame(r)
box.pack(fill="both", expand=True)

cols = ("a", "b", "c", "d", "e")
hs = ttk.Scrollbar(box, orient="horizontal")
tree = ttk.Treeview(box, columns=cols, show="headings", height=4,
                    xscrollcommand=hs.set)
hs.configure(command=tree.xview)
hs.pack(side="bottom", fill="x")
tree.pack(side="top", fill="both", expand=True)

for c in cols:
    tree.heading(c, text="列" + c)
    tree.column(c, width=300, minwidth=60, stretch=False)

for i in range(3):
    tree.insert("", "end", values=("x" * 5,) * 5)

r.update()
print("窗口宽 %d  表格宽 %d" % (r.winfo_width(), tree.winfo_width()))
print("各列配置宽 %s" % [tree.column(c, "width") for c in cols])
print("初始 xview = %s" % (tree.xview(),))
tree.xview_moveto(1.0)
r.update()
print("滚到最右 xview = %s" % (tree.xview(),))
tree.xview_moveto(0.0)
r.update()
print("滚回最左 xview = %s" % (tree.xview(),))

# 问一下实际显示宽度（列配置改了以后 displaycolumns 是否跟着变）
tree.column("a", width=100)
r.update()
print("把 a 改成 100 后 xview = %s" % (tree.xview(),))
r.destroy()
