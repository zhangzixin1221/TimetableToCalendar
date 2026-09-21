# -*- coding: utf-8 -*-
"""命令行入口。

用法示例：
    python -m timetable.cli 课表.pdf
    python -m timetable.cli 课表.pdf -o 我的课表.ics --week1 2026-08-31 --preset 长安校区
    python -m timetable.cli 课表.pdf --alarm 15 --night 21:30 --remark-courses
    python -m timetable.cli 课表.pdf --alarm 0 --night off        # 不要提醒
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys

from . import doctor
from . import holidays as hol_mod
from . import presets
from .ics import Config, IcsError, build_ics, merge_contiguous, sanity_check_ics
from .parser import WEEKDAY_SHORT, compress_weeks, parse_pdf, verify_parse


def _periods_from_arg(spec: str):
    """'1=08:30-09:15,2=09:25-10:10' -> {1: ('08:30','09:15'), ...}"""
    out = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk or "-" not in chunk:
            raise ValueError("作息时间格式应为 节次=开始-结束，例如 1=08:30-09:15")
        idx, rng = chunk.split("=", 1)
        a, b = rng.split("-", 1)
        out[int(idx.strip())] = (a.strip(), b.strip())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="timetable", description="把教务系统课表 PDF 转成可导入手机日历的 .ics")
    ap.add_argument("pdf", help="课表 PDF 路径")
    ap.add_argument("-o", "--out", help="输出 .ics 路径（默认与 PDF 同名）")
    ap.add_argument("--week1", default="2026-08-31",
                    help="第 1 周的周一日期，格式 YYYY-MM-DD（默认 2026-08-31）")
    ap.add_argument("--preset", default=presets.DEFAULT_PRESET,
                    choices=list(presets.PRESETS), help="作息时间预设")
    ap.add_argument("--period-times", default=None,
                    help="手动指定作息时间，如 1=08:30-09:15,2=09:25-10:10（会覆盖预设）")
    ap.add_argument("--alarm", default="20",
                    help="每节课提前多少分钟提醒，0 表示关闭（默认 20）")
    ap.add_argument("--night", default="22:00",
                    help="每晚汇总第二天课程的时间 HH:MM，off 表示关闭（默认 22:00）")
    ap.add_argument("--night-duration", type=int, default=15,
                    help="夜间提醒日程的时长（分钟，默认 15）")
    ap.add_argument("--merge", choices=["all", "artifact", "none"], default="all",
                    help="相连节次怎么合并：all=相连就合并（默认）；"
                         "artifact=只合并导出器拆出的碎片（如 11-12节+13-13节）；"
                         "none=完全按 PDF 的行")
    ap.add_argument("--holidays", default="on",
                    help="按法定节假日安排扣除停课并处理调休，on/off（默认 on）")
    ap.add_argument("--holidays-file", default=None,
                    help="本学期的节假日安排文件（纯文本，格式见 timetable/holidays.py 里的"
                         " HOLIDAY_FILE_TEMPLATE）；指定后用它替代内置预设")
    ap.add_argument("--force", action="store_true",
                    help="即使配置自检发现错误也照常生成")
    ap.add_argument("--no-holiday-notice", action="store_true",
                    help="放假前一晚不发「明天放假」提醒")
    ap.add_argument("--remark-courses", action="store_true",
                    help="把备注中无固定时间的课程也加为全天日程")
    ap.add_argument("--name", default=None, help="日历名称（默认按课表信息自动生成）")
    ap.add_argument("-q", "--quiet", action="store_true", help="只输出必要信息")
    args = ap.parse_args(argv)

    log = (lambda m: None) if args.quiet else (lambda m: print(m))

    try:
        week1 = dt.date.fromisoformat(args.week1)
    except ValueError:
        print("错误：--week1 需要 YYYY-MM-DD 格式，例如 2026-08-31", file=sys.stderr)
        return 2

    period_times = presets.get_preset(args.preset)
    if args.period_times:
        period_times.update(_periods_from_arg(args.period_times))

    try:
        alarm = int(args.alarm)
    except ValueError:
        print("错误：--alarm 需要整数（分钟）", file=sys.stderr)
        return 2

    night_on, nh, nm = True, 22, 0
    if str(args.night).strip().lower() in ("off", "no", "none", "关闭"):
        night_on = False
    else:
        try:
            hh, mm = str(args.night).split(":")
            nh, nm = int(hh), int(mm)
            if not (0 <= nh <= 23 and 0 <= nm <= 59):
                raise ValueError
        except ValueError:
            print("错误：--night 需要 HH:MM 或 off", file=sys.stderr)
            return 2

    # ---- 解析 ----
    try:
        result = parse_pdf(args.pdf, log=log)
    except Exception as exc:                      # noqa: BLE001
        print("解析失败：%s" % exc, file=sys.stderr)
        return 1

    if week1.weekday() != 0:
        print("提示：%s 不是周一，星期几会整体偏移，请确认。" % week1, file=sys.stderr)

    md = result.meta
    log("课表信息：%s  %s %s %s  学号 %s  姓名 %s"
        % (md.term, md.grade, md.college, md.major, md.sid, md.name))
    chk = verify_parse(result)
    log("自检：%d 条记录、共 %d 次课、时间冲突 %d 处"
        % (chk["records"], chk["instances"], len(chk["conflicts"])))
    for c in chk["conflicts"][:5]:
        print("  冲突：第%d周%s第%d节 %s / %s" % c, file=sys.stderr)

    merged = merge_contiguous(result.records, args.merge)
    log("相连节次合并：%d 条记录 -> %d 个日程（模式 %s）"
        % (len(result.records), len(merged), args.merge))
    log("第 1 周周一 = %s，作息 = %s" % (week1, args.preset))

    use_holidays = str(args.holidays).strip().lower() not in ("off", "no", "none", "关闭", "0")
    holiday_list = hol_mod.DEFAULT_HOLIDAYS if use_holidays else []
    makeup_list = hol_mod.DEFAULT_MAKEUP_DAYS if use_holidays else []
    if args.holidays_file:
        try:
            holiday_list, makeup_list = hol_mod.load_holiday_file(args.holidays_file)
        except hol_mod.HolidayFileError as exc:
            print("节假日文件有问题：%s" % exc, file=sys.stderr)
            return 2
        log("已读取节假日文件：%s" % args.holidays_file)

    cfg = Config(
        week1_monday=week1,
        period_times=period_times,
        class_alarm_min=alarm if alarm > 0 else None,
        night_summary=night_on,
        night_hour=nh,
        night_minute=nm,
        night_duration_min=args.night_duration,
        include_remark_courses=args.remark_courses,
        merge_mode=args.merge,
        calendar_name=args.name or md.calendar_name() or "课表",
        holidays=holiday_list,
        makeup_days=makeup_list,
        holiday_notice=not args.no_holiday_notice,
    )

    # ---- 配置自检：专门抓「换学期忘了改参数」这类不会报错只会算错的问题 ----
    findings = doctor.diagnose(cfg, result)
    log("")
    log("配置自检：")
    for f in findings:
        log("  " + str(f))
    if doctor.has_blocking(findings) and not args.force:
        print("", file=sys.stderr)
        print("配置自检发现错误，已中止。确认无误可加 --force 强制生成。", file=sys.stderr)
        for f in findings:
            if f.level == "error":
                print("  " + str(f), file=sys.stderr)
        return 3
    if use_holidays or args.holidays_file:
        log("节假日：" + hol_mod.describe(holiday_list, makeup_list))

    try:
        text, stats = build_ics(result, cfg)
    except IcsError as exc:
        print("生成失败：%s" % exc, file=sys.stderr)
        return 1

    out = args.out or (os.path.splitext(args.pdf)[0] + ".ics")
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    chk2 = sanity_check_ics(text)
    log("")
    log("课程日程 %d 个（覆盖 %d 次课）%s"
        % (stats["class_events"], stats["instances"],
           "，每节课提前 %d 分钟提醒" % alarm if alarm > 0 else "，未设置课前提醒"))
    if night_on:
        log("夜间汇总 %d 个（每晚 %02d:%02d 提醒第二天）"
            % (stats["night_events"], nh, nm))
    if stats["remark_events"]:
        log("备注课程全天日程 %d 个" % stats["remark_events"])
    if use_holidays:
        log("已按法定节假日扣除停课：放假期间不排课，调休上班日按当天课表执行")
    log("结构自检：CRLF=%s 超长行=%d VEVENT=%d/%d 配对=%s"
        % (chk2["crlf_only"], chk2["over_75"], chk2["vevent"],
           chk2["vevent_end"], chk2["balanced"]))
    print("已生成：%s  (%.1f KB)" % (out, stats["bytes"] / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
