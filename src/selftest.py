# -*- coding: utf-8 -*-
"""端到端自检：解析 → 生成 → 用第三方库独立展开 → 逐条核对。

用法：
    python selftest.py [课表.pdf]

核对内容：
  1. 解析出的记录数与「同一天同一时段不得有两门不同的课」；
  2. .ics 结构：CRLF、无超长行、标签配对、UTF-8；
  3. 用 recurring-ical-events 独立展开所有循环日程，得到的上课次数；
  4. **每条「今晚 22:00 汇总明天」的内容，必须与第二天实际展开出的课程完全一致**；
  5. 同一天展开出的课程之间不重叠。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys

from timetable import holidays as hol_mod
from timetable import presets
from timetable.ics import Config, build_ics, merge_contiguous, sanity_check_ics
from timetable.parser import WEEKDAY_SHORT, compress_weeks, parse_pdf, verify_parse

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
    print("找不到课表 PDF。用法： python selftest.py <你的课表.pdf>")
    print("（也可以设环境变量 TIMETABLE_PDF，或把 PDF 命名为 sample-timetable.pdf 放在项目根目录）")
    raise SystemExit(2)

WEEK1 = dt.date(2026, 8, 31)
TZ = dt.timezone(dt.timedelta(hours=8))

ok_all = True


def check(label, cond, extra=""):
    global ok_all
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok_all = False
    print("  [%s] %s%s" % (mark, label, ("  " + extra) if extra else ""))
    return cond


print("=" * 92)
print("输入：%s" % PDF)
print("=" * 92)

# ---------------------------------------------------------------- 1) 解析
res = parse_pdf(PDF)
md = res.meta
print("\n[1] 解析")
print("    学期=%s  年级=%s  学院=%s  专业=%s  班级=%s  姓名=%s  总学分=%s"
      % (md.term, md.grade, md.college, md.major, md.klass, md.name, md.total_credits))
print("    备注课程：%s" % (md.remark_courses or "无"))
chk = verify_parse(res)
check("解析出课程记录", chk["records"] > 0, "%d 条" % chk["records"])
check("无时间冲突（同一天同一时段只有一门课）", not chk["conflicts"],
      "%d 处冲突" % len(chk["conflicts"]))
check("没有解析失败的条目", not res.warnings, "；".join(res.warnings))

for r in res.records:
    print("      %s 第%-6s %-30s 第%-10s %-24s %s"
          % (r.day_name, "%d-%d节" % (r.pfrom, r.pto), r.course,
             compress_weeks(r.all_weeks()), r.room, "；".join(r.teacher_desc())))

# ---------------------------------------------------------------- 2~5) 各合并模式
for mode in ("all", "artifact", "none"):
    print("\n[2-5] 合并模式 = %s" % mode)
    # 注意：这里对同一个 ParseResult 反复生成 3 次，正好可以验证
    # merge_contiguous 不会就地改写解析结果（否则后两种模式会用被改过的数据）
    cfg = Config(week1_monday=WEEK1,
                 period_times=presets.get_preset(presets.DEFAULT_PRESET),
                 class_alarm_min=20, night_summary=True, night_hour=22, night_minute=0,
                 merge_mode=mode, calendar_name="自检",
                 holidays=hol_mod.DEFAULT_HOLIDAYS,
                 makeup_days=hol_mod.DEFAULT_MAKEUP_DAYS)
    text, stats = build_ics(res, cfg)

    st = sanity_check_ics(text)
    check("CRLF 换行", st["crlf_only"])
    check("无超过 75 字节的行", st["over_75"] == 0, "%d 行" % st["over_75"])
    check("标签配对", st["balanced"], "VEVENT %d/%d" % (st["vevent"], st["vevent_end"]))
    check("每个日程都有提醒", st["valarm"] == st["vevent"],
          "VALARM %d / VEVENT %d" % (st["valarm"], st["vevent"]))
    print("      课程日程 %d 个，夜间汇总 %d 个"
          % (stats["class_events"], stats["night_events"]))

    # ---- 独立展开 ----
    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        print("      （跳过独立展开核对：未安装 icalendar / recurring-ical-events）")
        continue

    cal = icalendar.Calendar.from_ical(text.encode("utf-8"))
    occ = recurring_ical_events.of(cal).between(
        dt.datetime(2026, 8, 1, tzinfo=TZ), dt.datetime(2027, 2, 1, tzinfo=TZ))
    class_occ = [o for o in occ if not str(o["UID"]).startswith("night-")]
    night_occ = [o for o in occ if str(o["UID"]).startswith("night-")]

    by_date = {}
    for o in class_occ:
        by_date.setdefault(o["DTSTART"].dt.date(), []).append(o)

    # 4) 夜间汇总逐条比对
    night_by_date = {o["DTSTART"].dt.date(): o for o in night_occ}
    mismatches = []
    checked = 0
    for d, classes in sorted(by_date.items()):
        o = night_by_date.get(d - dt.timedelta(days=1))
        if o is None:
            mismatches.append((d, "缺少当天的夜间汇总"))
            continue
        checked += 1
        if (o["DTSTART"].dt.hour, o["DTSTART"].dt.minute) != (22, 0):
            mismatches.append((d, "提醒时间不是 22:00"))
        listed = set()
        # icalendar 会把 \n 还原成真正的换行；两种形态都兼容
        desc = str(o["DESCRIPTION"]).replace("\\n", "\n")
        for line in desc.split("\n")[1:]:
            m = re.match(r"^\d+\.\s+(\d{2}:\d{2})-(\d{2}:\d{2})\s+(.+?)\s+｜", line.strip())
            if m:
                listed.add((m.group(1), m.group(2), m.group(3).strip()))
        actual = {(c["DTSTART"].dt.strftime("%H:%M"), c["DTEND"].dt.strftime("%H:%M"),
                   str(c["SUMMARY"])) for c in classes}
        if listed != actual:
            mismatches.append((d, "内容不符 列出=%s 实际=%s" % (sorted(listed), sorted(actual))))
    extra_nights = [d for d in night_by_date
                    if (d + dt.timedelta(days=1)) not in by_date
                    and "放假" not in str(night_by_date[d]["SUMMARY"])]

    check("夜间汇总的内容与第二天课程完全一致", not mismatches,
          "%d 天有课，%d 处不符" % (checked, len(mismatches)))
    check("没有多余的夜间汇总", not extra_nights, "%d 条" % len(extra_nights))
    for d, why in mismatches[:5]:
        print("        %s: %s" % (d, why))

    # 5) 同一天课程不重叠
    overlap = 0
    for d, cs in by_date.items():
        cs.sort(key=lambda o: o["DTSTART"].dt)
        for i in range(len(cs) - 1):
            if cs[i]["DTEND"].dt > cs[i + 1]["DTSTART"].dt:
                overlap += 1
    check("同一天的课程互不重叠", overlap == 0, "%d 处" % overlap)

    # 6) 法定节假日当天不能有课
    hol_dates = set()
    for h in hol_mod.DEFAULT_HOLIDAYS:
        hol_dates.update(h.dates())
    on_holiday = sorted(d for d in by_date if d in hol_dates)
    check("法定节假日当天没有课", not on_holiday,
          "放假 %d 天，其中 %d 天仍有课 %s"
          % (len(hol_dates), len(on_holiday), on_holiday[:3] or ""))

    # 7) 每个法定假期前一晚都有「明天放假」提醒
    notices = sorted((o for o in night_occ if "放假" in str(o["SUMMARY"])),
                     key=lambda o: o["DTSTART"].dt)
    check("每个法定假期都有放假提醒",
          len(notices) == len(hol_mod.DEFAULT_HOLIDAYS),
          "提醒 %d 条 / 假期 %d 个" % (len(notices), len(hol_mod.DEFAULT_HOLIDAYS)))
    for o in notices:
        print("        %s -> %s" % (o["DTSTART"].dt.strftime("%m-%d %H:%M"),
                                    str(o["SUMMARY"])))
    print("      独立展开：上课 %d 次、夜间提醒 %d 条" % (len(class_occ), len(night_occ)))

# ================================================================ 6) 换学期复用
# 工具本身与学期无关，但「第 1 周周一」和「节假日安排」必须跟着学期换。
# 这一节把换学期最容易犯的两种错误固化成用例，确保以后不会退化成静默算错。
print("\n[6] 换学期复用（参数必须跟着学期换，程序要能拦住忘记换的情况）")
import io as _io
import os as _os
import tempfile as _tempfile

import icalendar as _ical
import recurring_ical_events as _rie

from timetable import doctor as _doc


def _cfg(week1, holidays=None, makeup=None):
    return Config(
        week1_monday=week1,
        period_times=presets.get_preset(presets.DEFAULT_PRESET),
        class_alarm_min=20, night_summary=True, night_hour=22, night_minute=0,
        holidays=hol_mod.DEFAULT_HOLIDAYS if holidays is None else holidays,
        makeup_days=hol_mod.DEFAULT_MAKEUP_DAYS if makeup is None else makeup)


f_ok = _doc.diagnose(_cfg(WEEK1), res)
check("正确配置下自检不报错误", not _doc.has_blocking(f_ok),
      "；".join(f.title for f in f_ok if f.level == "error"))

f_stale = _doc.diagnose(_cfg(dt.date(2025, 9, 1)), res)
check("沿用旧学期日期时，能报出「日期与 PDF 学期对不上」",
      _doc.has_blocking(f_stale),
      next((f.title for f in f_stale if f.level == "error"), "")[:70])

f_spring = _doc.diagnose(_cfg(dt.date(2027, 2, 22)), res)
_err_titles = " ".join(f.title for f in f_spring if f.level == "error")
check("换到下学期却沿用旧节假日预设时，能同时报出两个问题",
      "对不上" in _err_titles and "之外" in _err_titles, _err_titles[:70])

# 换成外部节假日文件（模拟下学期）：应能正常生成且节假日被正确扣除
_hf = _os.path.join(_tempfile.gettempdir(), "holiday_selftest.txt")
_io.open(_hf, "w", encoding="utf-8").write(
    "# 测试用：模拟下一个学期\n"
    "放假 2027-04-04 2027-04-06 清明节\n"
    "放假 2027-05-01 2027-05-05 劳动节\n"
    "调休 2027-04-25\n")
_hs, _ms = hol_mod.load_holiday_file(_hf)
check("外部节假日文件解析正确", len(_hs) == 2 and len(_ms) == 1,
      "%d 个假期 / %d 个调休" % (len(_hs), len(_ms)))

_cfg_s = _cfg(dt.date(2027, 2, 22), _hs, _ms)
_text_s, _stats_s = build_ics(res, _cfg_s)
_f_s = _doc.diagnose(_cfg_s, res)
# 注意：这里仍然会报「学期对不上」——因为我们是故意拿一份秋季课表配春季日期，
# 那条错误是自检的正确行为。要验证的是「节假日不在本学期内」这条错误消失了。
_err_s = " ".join(f.title for f in _f_s if f.level == "error")
check("换成本学期节假日文件后，「节假日不在本学期内」的错误消失",
      "之外" not in _err_s, _err_s[:70] or "无其它错误")
check("自检确认新节假日在学期范围内",
      any("落在本学期内" in f.detail for f in _f_s), "")

_tz = dt.timezone(dt.timedelta(hours=8))
_occ_s = _rie.of(_ical.Calendar.from_ical(_text_s.encode("utf-8"))).between(
    dt.datetime(2027, 2, 1, tzinfo=_tz), dt.datetime(2027, 8, 1, tzinfo=_tz))
_cls_s = [o for o in _occ_s if not str(o["UID"]).startswith("night-")]
_hol_s = ({dt.date(2027, 4, 4) + dt.timedelta(days=i) for i in range(3)}
          | {dt.date(2027, 5, 1) + dt.timedelta(days=i) for i in range(5)})
_on_hol_s = sorted({o["DTSTART"].dt.date() for o in _cls_s} & _hol_s)
check("春季学期的法定节假日同样不会排课", not _on_hol_s, str(_on_hol_s))
check("春季学期生成了课程与夜间提醒",
      len(_cls_s) > 0 and len([o for o in _occ_s if str(o["UID"]).startswith("night-")]) > 0,
      "上课 %d 次" % len(_cls_s))

print("\n" + "=" * 92)
print("全部检查通过" if ok_all else "存在失败项，请查看上面的 FAIL")
print("=" * 92)
sys.exit(0 if ok_all else 1)
