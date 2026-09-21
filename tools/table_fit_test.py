# -*- coding: utf-8 -*-
"""预览表列宽自适应回归：左右缩放窗口后，右边几列不能被裁掉。

    python table_fit_test.py [课表.pdf]

判定规则（每个宽度都要满足）：
  1. 各列配置宽度之和 <= 表格可视宽度（也就是说没有列被裁掉），或者
     横向滚动条已经出现（说明已经压到最小宽度还是塞不下）；
  2. 每列宽度都不小于它的最小宽度；
  3. 表格右边的「修改 / 新增 / 删除」按钮仍在卡片内，没有被表格覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from timetable import ui_kit as ui            # noqa: E402
from timetable.gui import App                 # noqa: E402


def _find_pdf():
    """课表 PDF 从哪来：命令行参数 → TIMETABLE_PDF 环境变量 → 就近找 sample-timetable.pdf。"""
    cands = [sys.argv[1] if len(sys.argv) > 1 else "", os.environ.get("TIMETABLE_PDF", "")]
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        cands += [os.path.join(up, "sample-timetable.pdf"), os.path.join(up, "课表.pdf")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


PDF = _find_pdf()
if not PDF:
    print("找不到课表 PDF。用法： python table_fit_test.py <你的课表.pdf>")
    print("（也可以设环境变量 TIMETABLE_PDF，或把 PDF 命名为 sample-timetable.pdf 放在项目根目录）")
    sys.exit(2)

# 逻辑宽度：从最小宽度到很宽（物理 = 逻辑 × DPI 缩放）
WIDTHS = [840, 900, 1000, 1100, 1240, 1400, 1600]

app = App()
app.headless = True
app.update()
app.load_pdf(PDF)
app.do_parse()
print("=" * 78)
print("预览表列宽自适应检查（%d 条记录）" % len(app.records))
print("=" * 78)

minw = app.minsize()[0]
bad = 0
for lw in WIDTHS:
    app.geometry("%dx%d" % (max(ui.px(lw), minw), app.winfo_height()))
    app.update()
    app.update_idletasks()

    tree_w = app.tree.winfo_width()
    total = sum(int(app.tree.column(c, "width")) for c in app._cols)
    cols = {c: int(app.tree.column(c, "width")) for c in app._cols}
    under_min = [c for c in app._cols if cols[c] < ui.px(app._col_min[c])]

    fits = total <= tree_w + 2
    problems = []
    if not fits and not app._hs_shown:
        problems.append("列宽合计 %d > 表格宽 %d，右边列被裁掉却没有横向滚动条"
                        % (total, tree_w))
    if under_min:
        problems.append("列宽低于下限: %s" % "、".join(under_min))

    # 右侧按钮（修改 / 新增 / 删除）不能被压没
    buttons = []
    inner = app.tree.master.master          # tree -> grid -> inner
    for w in inner.winfo_children():
        for k in w.winfo_children():
            if hasattr(k, "_w_px"):
                buttons.append(k)
    for b in buttons:
        if b.winfo_ismapped() and (b.winfo_width() <= 1 or b.winfo_height() <= 1):
            problems.append("按钮被压没了 %s" % str(b)[:24])
    if not buttons:
        problems.append("没找到右侧按钮（界面结构变了？）")

    print("\n窗口逻辑宽 %-5d 表格宽 %-5d 列宽合计 %-5d 横向滚动条 %-4s 课程列 %d 教室列 %d 教师列 %d"
          % (lw, tree_w, total, "显示" if app._hs_shown else "隐藏",
             cols["course"], cols["room"], cols["teacher"]))
    if problems:
        bad += len(problems)
        for p in problems:
            print("    !! %s" % p)
    else:
        print("    正常：%s" % ("所有列都放得下" if fits else "已压到最小宽度，用横向滚动条查看"))

app.destroy()

# ---- 超窄窗口：临时解除最小宽度限制，验证「压到最小宽度 + 横向滚动条」这条兜底路径 ----
print()
print("-" * 78)
print("超窄窗口兜底检查（临时把最小宽度放到 560 逻辑像素，正常用不到）")
print("-" * 78)
app2 = App()
app2.headless = True
app2.minsize(ui.px(520), app2.minsize()[1])
app2.update()
app2.geometry("%dx%d" % (ui.px(560), ui.px(700)))
app2.update()
app2.update_idletasks()
tree_w = app2.tree.winfo_width()
cols = {c: int(app2.tree.column(c, "width")) for c in app2._cols}
total = sum(cols.values())
under_min = [c for c in app2._cols if cols[c] < ui.px(app2._col_min[c])]
print("表格宽 %d  列宽合计 %d  横向滚动条 %s" % (tree_w, total, "显示" if app2._hs_shown else "隐藏"))
print("各列: %s" % "  ".join("%s=%d" % (c, cols[c]) for c in app2._cols))
ok = True
if not app2._hs_shown:
    print("    !! 已经压到最小宽度还塞不下，却没有横向滚动条")
    ok = False
if under_min:
    print("    !! 列宽低于下限: %s" % "、".join(under_min))
    ok = False
if app2._hs_shown:
    app2.tree.xview_moveto(1.0)
    app2.update()
    if app2.tree.xview()[1] < 0.999:
        print("    !! 横向滚不到最右 xview=%s" % (app2.tree.xview(),))
        ok = False
    else:
        print("    正常：横向能滚到最右（xview=%s）" % (app2.tree.xview(),))
app2.destroy()
if not ok:
    bad += 1

print()
print("=" * 78)
print("问题总数: %d  ->  %s" % (bad, "通过" if bad == 0 else "需要修复"))
print("=" * 78)
sys.exit(0 if bad == 0 else 1)
