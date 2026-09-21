# -*- coding: utf-8 -*-
"""界面烟雾测试：不点鼠标，直接驱动界面里的逻辑走完整流程。

    python smoke_test.py [课表.pdf]

会真的创建窗口（以便验证控件都能建起来），但把弹窗静音，然后：
  ① 解析 PDF → ② 生成 .ics → ③ 用第三方库展开并核对夜间汇总 → ④ 关窗。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from timetable.gui import App, parse_weeks_expr, weeks_to_expr  # noqa: E402
from timetable.ics import sanity_check_ics                     # noqa: E402

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
    print("找不到课表 PDF。用法： python smoke_test.py <你的课表.pdf>")
    print("（也可以设环境变量 TIMETABLE_PDF，或把 PDF 命名为 sample-timetable.pdf 放在项目根目录）")
    raise SystemExit(2)

ok = True


def check(label, cond, extra=""):
    global ok
    if not cond:
        ok = False
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + extra) if extra else ""))


print("=" * 78)
print("界面烟雾测试")
print("=" * 78)

# ---- 周次表达式（界面上编辑周次时用的） ----
print("\n[1] 周次表达式解析")
cases = [("1~8", list(range(1, 9))), ("18", [18]), ("2,4,6", [2, 4, 6]),
         ("2~4(双)", [2, 4]), ("2~4（单）", [3]), ("1~4,6", [1, 2, 3, 4, 6]),
         ("1~16", list(range(1, 17)))]
for expr, want in cases:
    got = parse_weeks_expr(expr)
    check("%-10s -> %s" % (expr, weeks_to_expr(got)), got == want,
          "" if got == want else "期望 %s" % want)

# ---- 建窗口 ----
print("\n[2] 创建窗口并走完整流程")
app = App()
app.headless = True                      # 弹窗只写日志
app.withdraw()                           # 不真的显示出来
print("  窗口创建成功，标题：%s" % app.title())

app.load_pdf(PDF)
out = os.path.join(tempfile.gettempdir(), "smoke_timetable.ics")
app.var_out.set(out)
if os.path.exists(out):
    os.remove(out)

check("解析课表", app.do_parse(), "记录 %d 条" % len(app.records))
check("表格行数与记录数一致", len(app.tree.get_children()) == len(app.records),
      "%d 行" % len(app.tree.get_children()))

# 改一条：把第一行的周次改掉，确认表格会刷新且能生成
first = app.records[0]
before = weeks_to_expr(first.all_weeks())
first.segments = [type(first.segments[0])(weeks=parse_weeks_expr("1~4"),
                                          teacher=first.segments[0].teacher)]
app._refresh_table()
check("修改周次后表格刷新",
      weeks_to_expr(app.records[0].all_weeks()) == "1~4",
      "%s -> %s" % (before, weeks_to_expr(app.records[0].all_weeks())))

check("生成 .ics", app.do_generate())
check("输出文件存在", os.path.isfile(out),
      "%.1f KB" % (os.path.getsize(out) / 1024.0) if os.path.isfile(out) else "缺失")

# ---- 核对生成物 ----
print("\n[3] 核对生成物")
# 必须用二进制读取：文本模式会把 CRLF 归一化成 LF，导致折行检查误报
with open(out, "rb") as fh:
    text = fh.read().decode("utf-8")
st = sanity_check_ics(text)
check("CRLF 换行", st["crlf_only"])
check("无超过 75 字节的行", st["over_75"] == 0, "%d 行" % st["over_75"])
check("标签配对", st["balanced"], "VEVENT %d/%d" % (st["vevent"], st["vevent_end"]))
check("每个日程都带提醒", st["valarm"] == st["vevent"],
      "VALARM %d / VEVENT %d" % (st["valarm"], st["vevent"]))

try:
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(text.encode("utf-8"))
    tz = dt.timezone(dt.timedelta(hours=8))
    occ = recurring_ical_events.of(cal).between(dt.datetime(2026, 8, 1, tzinfo=tz),
                                                dt.datetime(2027, 2, 1, tzinfo=tz))
    cls = [o for o in occ if not str(o["UID"]).startswith("night-")]
    ngt = [o for o in occ if str(o["UID"]).startswith("night-")]
    check("第三方库可正常展开", len(cls) > 0 and len(ngt) > 0,
          "上课 %d 次、夜间提醒 %d 条" % (len(cls), len(ngt)))

    by_date = {}
    for o in cls:
        by_date.setdefault(o["DTSTART"].dt.date(), []).append(o)
    night_by_date = {o["DTSTART"].dt.date(): o for o in ngt}
    bad = 0
    for d, cs in by_date.items():
        o = night_by_date.get(d - dt.timedelta(days=1))
        if o is None:
            bad += 1
            continue
        listed = set()
        for line in str(o["DESCRIPTION"]).replace("\\n", "\n").split("\n")[1:]:
            m = __import__("re").match(
                r"^\d+\.\s+(\d{2}:\d{2})-(\d{2}:\d{2})\s+(.+?)\s+｜", line.strip())
            if m:
                listed.add((m.group(1), m.group(2), m.group(3).strip()))
        actual = {(c["DTSTART"].dt.strftime("%H:%M"), c["DTEND"].dt.strftime("%H:%M"),
                   str(c["SUMMARY"])) for c in cs}
        if listed != actual:
            bad += 1
    check("夜间汇总内容与第二天课程一致", bad == 0, "%d 处不符" % bad)
except ImportError:
    print("  （跳过独立展开：未安装 icalendar / recurring-ical-events）")

app.destroy()
print("\n" + "=" * 78)
print("界面烟雾测试通过" if ok else "存在失败项")
print("=" * 78)
sys.exit(0 if ok else 1)
