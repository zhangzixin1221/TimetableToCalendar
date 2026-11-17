# -*- coding: utf-8 -*-
"""「时刻制」课表的解析回归（纯函数级，不造 PDF）。

    python tools\\test_timebased.py

为什么单独一份、且**不造 PDF**：
    用户提供的真实样本（安徽师范大学学生课表照片）暴露了三处与之前所有 fixture 都
    不同的结构 —— 3 位课程代码、用上下课时刻（`800-935`）代替节次、教室与教师粘连
    无空格。这些是「各式各样课表」的典型难点。
    本想把它们做成一份合成 PDF，但 MuPDF 的 `insert_text` 对同一页的连续调用会按
    自己的 leading 排版（实测每行固定 7.33pt，完全无视传入的 y），`insert_textbox`
    又不能稳定控制基线，为了造出「可解析的假 PDF」要跟排版引擎反复搏斗 —— 那是
    在测 MuPDF，不是测我们的解析逻辑。
    所以这里直接喂 span / 文本给解析函数：更精确、更快、不依赖 PDF 生成的脾气。
    端到端那条路由另外三份真实结构的 PDF fixture 覆盖（make_fixture*.py）。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from timetable import presets                                  # noqa: E402
from timetable.layout import Layout, REGISTRY, split_room_teacher  # noqa: E402
from timetable.parser import (_course_from_head, _group_into_lines,  # noqa: E402
                             _parse_detail, resolve_periods, Record,
                             Segment, compress_weeks)
from timetable.parser import _entry_has_detail                  # noqa: E402

ok_all = True


def check(label, cond, extra=""):
    global ok_all
    if not cond:
        ok_all = False
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + str(extra)) if extra else ""))
    return cond


LAY = REGISTRY.get("generic")
RX = LAY.compiled()
PT = presets.get_preset(None, LAY)      # 通用作息：1 节 08:00-08:45，2 节 08:55-09:40 …

print("[1] 详情行：时刻代替节次")
d = _parse_detail("（1~9周）（950-1125）2号楼2", LAY, RX)
check("周次 1~9", d and d["weeks"] == list(range(1, 10)), d and d["weeks"])
check("时刻 950-1125 → 09:50-11:25",
      d and d["clock"] == ("09:50", "11:25"), d and d["clock"])
check("没有节次（待反查）", d and d["pfrom"] is None, d and d["pfrom"])
check("head 为空（周次在最前）", d and d["head"] == "", d and d["head"])
check("rest 只剩教室", d and d["rest"] == "2号楼2", d and d["rest"])

d2 = _parse_detail("（10~17周）（3-4节）2号楼2", LAY, RX)
check("写了节次就直采 3-4 节", d2 and (d2["pfrom"], d2["pto"]) == (3, 4),
      d2 and (d2["pfrom"], d2["pto"]))
check("写了节次就不该再记时刻", d2 and d2["clock"] is None, d2 and d2["clock"])

d3 = _parse_detail("800-935", LAY, RX)
check("没有周次的裸时刻不算详情行（它是正文的一部分，由周次行带出）",
      d3 is None, d3)
# 但时刻正则本身要能认这种写法（它由 weeks 行一起触发）
m3 = RX["clock_range_re"].search("（1~8周）（800-935）") if RX["clock_range_re"] else None
check("clock_range_re 能匹配 800-935", m3 is not None,
      m3.group(0) if m3 else None)
from timetable.parser import _hhmm_digits                      # noqa: E402
check("800 → 08:00、935 → 09:35、1430 → 14:30",
      (_hhmm_digits("800"), _hhmm_digits("935"), _hhmm_digits("1430"))
      == ("08:00", "09:35", "14:30"))
d4 = _parse_detail("（1~17周）（1430-1655）", LAY, RX)
check("1430-1655 → 14:30-16:55",
      d4 and d4["clock"] == ("14:30", "16:55"), d4 and d4["clock"])

print("\n[2] 课程标题：3~4 位课程代码")
for title, course, code in (
        ("大学英语（Ⅱ）078", "大学英语（Ⅱ）", "078"),
        ("电磁学001", "电磁学", "001"),
        ("数学物理方法001", "数学物理方法", "001"),
        ("高等数学06", "高等数学", "06"),
        ("马克思主义基本原理1234", "马克思主义基本原理", "1234")):
    m = RX["title_re"].match(title)
    got_c = got_k = None
    if m:
        got_c = re.sub(r"[（(【\[]\s*$", "", title[:m.start("code")].strip()).strip()
        got_k = m.group("code")
    check("%-22s → 课程=%r 代码=%r" % (title, course, code),
          (got_c, got_k) == (course, code), (got_c, got_k))

print("\n[3] 教室与教师粘连（无空格）")
cases = [
    ("4号楼2040401钱渝", ("4号楼2040401", "钱渝")),
    ("10205*温建萍", ("10205*", "温建萍")),
    ("2010205*黄武英", ("2010205*", "黄武英")),
    ("2号楼2020104*郭文梅", ("2号楼2020104*", "郭文梅")),
    ("1号楼", ("1号楼", "")),                    # 纯地点，不能拆出「号楼」当教师
    ("1号楼20", ("1号楼20", "")),
    ("翱翔体育馆", ("翱翔体育馆", "")),            # 纯汉字地点，不能拆出「馆」
    ("教西D-101 张三", ("教西D-101", "张三")),     # 有空格的老写法仍要对
    ("实验大楼C座5楼 向礼琴", ("实验大楼C座5楼", "向礼琴")),
    ("金伟/何淼/朱仁贵", ("金伟/何淼/朱仁贵", "")),  # 只有教师，不瞎拆
]
for raw, want in cases:
    got = split_room_teacher(raw, "auto")
    check("%-24r → %r" % (raw, want), got == want, got)

print("\n[4] 行聚合用基线，不受字形 ascender 影响")
# 同一行的多个 span：bbox.y0 差 1.7（真实 PDF 里很常见），但基线完全相同
line_spans = [
    dict(x0=100, y0=100.0, x1=140, y1=110, base=105.0, size=6.0, text="星期一"),
    # 真实 PDF 里同一行各段之间有一个空白 span（它是「教室/教师」的分隔符来源），
    # 这里照实补上，否则拼出来就是"星期一高等数学"这种粘在一起的字
    dict(x0=140, y0=100.0, x1=144, y1=110, base=105.0, size=6.0, text=" "),
    dict(x0=144, y0=101.7, x1=200, y1=110, base=105.0, size=6.0, text="高等数学"),
]
lines = _group_into_lines(line_spans, 2.5)
check("同基线的三个 span 合成一行", len(lines) == 1, len(lines))
check("拼出的文字正确", lines and lines[0]["text"] == "星期一 高等数学",
      lines and lines[0]["text"])
# 相邻两行：基线差 9，但 bbox.y0 因为字形只差 1.7（这正是把两行误并成一行的那种情况）
two = [
    dict(x0=100, y0=98.4, x1=200, y1=110, base=104.0, size=6.0, text="数学物理方法001"),
    dict(x0=100, y0=105.7, x1=260, y1=115, base=113.0, size=6.0, text="（1~9周）（950-1125）"),
]
lines2 = _group_into_lines(two, 2.5)
check("bbox 只差 7.3 但基线差 9 → 必须分成两行（以前会误并）", len(lines2) == 2, len(lines2))

print("\n[5] 时刻 → 节次反查（以用户作息表为准）")
recs = [Record(day=0, course="大学英语", code="078", pfrom=None, pto=None,
               room="4号楼", clock=("08:00", "09:35"),
               segments=[Segment(weeks=[1])])]
out, unresolved = resolve_periods(recs, PT, tolerance_min=10)
check("08:00-09:35 反查成 1-2 节（通用作息 08:00-08:45 + 08:55-09:40，差 5 分钟）",
      out[0].pfrom == 1 and out[0].pto == 2, (out[0].pfrom, out[0].pto))
check("没有未解析项", unresolved == [], unresolved)

recs2 = [Record(day=0, course="夜课", code="", pfrom=None, pto=None, room="X",
                clock=("23:00", "23:50"), segments=[Segment(weeks=[1])])]
out2, unresolved2 = resolve_periods(recs2, PT, tolerance_min=10)
check("对不上作息表时不猜（保持无节次）", out2[0].pfrom is None, out2[0].pfrom)
check("并明确报告哪门课没反查出来", len(unresolved2) == 1 and "夜课" in unresolved2[0],
      unresolved2)

# 反查必须以**用户设定的表**为准：换一套作息，同一时刻应落到不同节次
custom = {1: ("09:00", "09:50"), 2: ("09:50", "10:40")}
recs3 = [Record(day=0, course="课", code="", pfrom=None, pto=None, room="",
                clock=("09:00", "09:40"), segments=[Segment(weeks=[1])])]
out3, _ = resolve_periods(recs3, custom, tolerance_min=10)
check("换一套作息表，反查结果跟着变（说明用的是用户的表）",
      (out3[0].pfrom, out3[0].pto) == (1, 1), (out3[0].pfrom, out3[0].pto))

print("\n[6] 「有没有正文行」的判据要同时认节次与时刻")
ent_clock = dict(title="大学英语（Ⅱ）078", size=6.0,
                 body=[dict(text="（1~17周）（800-935）4号楼2", size=6.0)])
ent_period = dict(title="电磁学001", size=6.0,
                  body=[dict(text="（1~17周）（1-2节）1号楼", size=6.0)])
ent_empty = dict(title="数学物理方法001", size=6.0, body=[])
check("只有时刻的正文行也算「有正文」（否则会误判成课程名残段）",
      _entry_has_detail(ent_clock, LAY, RX))
check("有节次的正文行算「有正文」", _entry_has_detail(ent_period, LAY, RX))
check("还没有正文的条目才算残段", not _entry_has_detail(ent_empty, LAY, RX))

print("\n" + "=" * 74)
print("全部通过" if ok_all else "存在失败项")
sys.exit(0 if ok_all else 1)