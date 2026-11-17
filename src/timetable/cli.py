# -*- coding: utf-8 -*-
"""命令行入口。

用法示例：
    python -m timetable.cli 课表.pdf
    python -m timetable.cli 课表.pdf -o 我的课表.ics --week1 2026-08-31 --preset 长安校区
    python -m timetable.cli 课表.pdf --alarm 15 --night 21:30 --remark-courses
    python -m timetable.cli 课表.pdf --alarm 0 --night off        # 不要提醒

换学校 / 换教务系统：
    python -m timetable.cli 课表.pdf --auto-layout          # 挨个版式试，挑最像的
    python -m timetable.cli 课表.pdf --layout my.json       # 用自己写的版式文件
    python -m timetable.cli --list-layouts                  # 看有哪些内置版式
    python -m timetable.cli 课表.pdf --dump-layout          # 打印程序眼里的这一页

节假日联网：
    python -m timetable.cli 课表.pdf --fetch-holidays                  # 按学期自动推年份
    python -m timetable.cli 课表.pdf --fetch-holidays 2026,2027        # 指定年份
    python -m timetable.cli 课表.pdf --fetch-holidays --save-holidays 节假日.txt
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys

from . import doctor
from . import holidays as hol_mod
from . import layout as lay_mod
from . import presets
from .ics import Config, IcsError, build_ics, merge_contiguous, sanity_check_ics
from .parser import (WEEKDAY_SHORT, compress_weeks, detect_layout, parse_pdf,
                     verify_parse)


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


def _make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="timetable", description="把教务系统课表 PDF 转成可导入手机日历的 .ics")
    ap.add_argument("pdf", nargs="?",
                    help="课表文件：PDF（有文字层）或 Excel/CSV（.xlsx/.csv）")
    ap.add_argument("-o", "--out", help="输出 .ics 路径（默认与输入同名）")
    ap.add_argument("--xlsx-out", default=None, metavar="PATH",
                    help="把解析结果导出成 Excel（供人工核对修改；改完可以把它当输入）")
    ap.add_argument("--xlsx-template", nargs="?", const="__DEFAULT__", default=None,
                    metavar="PATH",
                    help="导出一份空的 Excel 模板（带表头、示例行与填写说明）后退出，"
                         "不填路径就用 课表模板.xlsx")
    ap.add_argument("--week1", default="2026-08-31",
                    help="第 1 周的周一日期，格式 YYYY-MM-DD（默认 2026-08-31）")
    ap.add_argument("--preset", default=None,
                    help="作息时间预设；不填用所选版式的默认那套（--list-layouts 能看每套"
                         "版式自带哪些预设）")
    ap.add_argument("--period-times", default=None,
                    help="手动指定作息时间，如 1=08:30-09:15,2=09:25-10:10（覆盖预设）")
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

    g = ap.add_argument_group("版式（换学校 / 换教务系统）")
    g.add_argument("--layout", default=lay_mod.DEFAULT_LAYOUT_ID,
                   help="内置版式 id（%s），或一个版式 JSON 文件路径"
                        % "、".join(lay_mod.REGISTRY.ids()))
    g.add_argument("--auto-layout", action="store_true",
                   help="挨个版式试解析，按「课程条数 − 冲突 − 警告」挑一个（会打印每个版式"
                        "的成绩，务必核对再用）")
    g.add_argument("--list-layouts", action="store_true",
                   help="列出所有可用版式（含 --layout-dir 载入的）后退出")
    g.add_argument("--layout-dir", default=None,
                   help="从这个目录载入所有 *.json 版式（可与 --layout 搭配）")
    g.add_argument("--layout-template", metavar="PATH", nargs="?", const="__DEFAULT__",
                   default=None, help="导出一份版式配置模板（JSON）到 PATH，不填用"
                                      " my-school-layout.json；给别的学校写版式就从这里开始")
    g.add_argument("--dump-layout", action="store_true",
                   help="不生成 .ics，只打印程序从 PDF 里看到的结构（列、行、每条正文行的"
                        "切分结果），用来写/调版式")

    h = ap.add_argument_group("节假日（默认联网获取，带本地缓存）")
    h.add_argument("--no-holidays", action="store_true",
                   help="不处理节假日（放假期间也照常排课）。默认会联网获取当年安排。")
    h.add_argument("--holidays-file", default=None,
                   help="改用本地节假日文件（纯文本，格式见 timetable/holidays.py 的"
                        " HOLIDAY_FILE_TEMPLATE）。指定它就不再联网")
    h.add_argument("--holidays-years", default=None, metavar="年份",
                   help="指定要取哪几个自然年（如 2026 或 2026,2027）；不写就按"
                        "「第 1 周周一 + 总周次」自动推断学期跨了哪几年")
    h.add_argument("--holidays-url", default=None,
                   help="换数据源地址（要含 {year} 占位，支持 https:// 与 file:///，"
                        "内网镜像/离线包用）")
    h.add_argument("--holidays-refresh", action="store_true",
                   help="忽略本地缓存，重新联网取节假日")
    h.add_argument("--save-holidays", default=None, metavar="PATH",
                   help="把这次用的节假日安排写成可编辑的纯文本文件（学校自己调课时，"
                        "改这份文件比改代码方便）")
    h.add_argument("--force", action="store_true",
                   help="即使配置自检发现错误也照常生成")
    h.add_argument("--no-holiday-notice", action="store_true",
                   help="放假前一晚不发「明天放假」提醒")
    h.add_argument("--remark-courses", action="store_true",
                   help="把备注中无固定时间的课程也加为全天日程")
    ap.add_argument("--name", default=None, help="日历名称（默认按课表信息自动生成）")
    ap.add_argument("-q", "--quiet", action="store_true", help="只输出必要信息")
    return ap


def _resolve_layout(args, ap, log) -> lay_mod.Layout:
    """把 --layout-dir / --layout / --auto-layout 收敛成一个版式对象。"""
    if args.layout_dir:
        if not os.path.isdir(args.layout_dir):
            print("错误：--layout-dir 不是目录：%s" % args.layout_dir, file=sys.stderr)
            raise SystemExit(2)
        loaded = lay_mod.load_layout_dir(args.layout_dir)
        if loaded:
            log("已从 %s 载入版式：%s"
                % (args.layout_dir, "、".join(x.id for x in loaded)))
    key = (args.layout or "").strip()
    if key.lower().endswith(".json") or os.path.isfile(key):
        try:
            return lay_mod.load_layout_file(key)
        except lay_mod.LayoutError as exc:
            print("错误：版式文件有问题：%s" % exc, file=sys.stderr)
            raise SystemExit(2)
    try:
        return lay_mod.REGISTRY.get(key)
    except lay_mod.LayoutError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        raise SystemExit(2)


def main(argv=None) -> int:
    ap = _make_parser()
    args = ap.parse_args(argv)
    log = (lambda m: None) if args.quiet else (lambda m: print(m))

    # ---------- 不需要课表的操作 ----------
    if args.list_layouts:
        for lay in lay_mod.REGISTRY.all():
            log("* %-12s %s" % (lay.id, lay.name))
            log("             模式=%s  作息预设=%s"
                % (lay.entry_mode, "、".join(lay.preset_names()) or "（无，要自己填）"))
            if lay.note:
                log("             %s" % lay.note)
        log("")
        log("自己学校的版式：--layout-template 导模板，改完 --layout 那个文件 用；"
            "先看结构用 --dump-layout。")
        return 0
    if args.layout_template:
        path = "my-school-layout.json" if args.layout_template == "__DEFAULT__" \
            else args.layout_template
        lay_mod.export_template(path)
        print("已导出版式模板：%s" % path)
        print("怎么改：先 `--dump-layout 你的课表.pdf` 看程序眼里的结构，"
              "再照着 weeks_re / period_re / title_re 改；字段名写错会当场报错。")
        return 0
    if args.xlsx_template:
        from .xlsx_source import write_xlsx
        path = "课表模板.xlsx" if args.xlsx_template == "__DEFAULT__" \
            else args.xlsx_template
        write_xlsx(path)
        print("已导出 Excel 模板：%s" % path)
        print("  「课表」工作表照着一行一条课填；「填写说明」里有列名别名和格式说明。")
        print("  填好后直接用它当输入：--cli %s" % path)
        return 0
    if not args.pdf:
        ap.print_usage(sys.stderr)
        print("错误：要给定课表文件（PDF 或 .xlsx/.csv）。"
              "只有 --list-layouts / --layout-template / --xlsx-template 可以不填。",
              file=sys.stderr)
        return 2
    if not os.path.isfile(args.pdf):
        print("错误：找不到文件：%s" % args.pdf, file=sys.stderr)
        return 2

    try:
        week1 = dt.date.fromisoformat(args.week1)
    except ValueError:
        print("错误：--week1 需要 YYYY-MM-DD 格式，例如 2026-08-31", file=sys.stderr)
        return 2

    # ---- 选版式 ----
    lay = _resolve_layout(args, ap, log)

    # ---- Excel / CSV 输入：不走 PDF 解析，直接读单元格 ----
    from .xlsx_source import XlsxError, is_table_file, read_any
    is_table = is_table_file(args.pdf)

    # ---- 结构转储（写版式用的工具，不生成 ics）----
    if args.dump_layout:
        if is_table:
            print("错误：--dump-layout 只对 PDF 有意义；Excel 请直接打开表格看。",
                  file=sys.stderr)
            return 2
        from .debug_dump import dump_pdf
        print(dump_pdf(args.pdf, lay))
        return 0

    # ---- 作息：必须在解析**之前**定好 ----
    # 「时刻制」课表（格子里只印 800-935 这种时刻）要靠用户设定的作息表反查第几节，
    # 所以作息得先于 parse_pdf 确定下来。
    names = presets.preset_names(lay)
    preset_name = args.preset or presets.default_preset_name(lay)
    if names and preset_name not in names and not args.period_times:
        print("错误：版式「%s」没有叫「%s」的作息预设，可用：%s"
              % (lay.name, preset_name, "、".join(names)), file=sys.stderr)
        return 2
    period_times = presets.get_preset(preset_name, lay) if names else {}
    if args.period_times:
        try:
            period_times.update(_periods_from_arg(args.period_times))
        except ValueError as exc:
            print("错误：%s" % exc, file=sys.stderr)
            return 2
    if args.period_times:
        log("作息时间：%s（%s）" % (preset_name if names else "版式未自带",
                                    "已用 --period-times 覆盖"))
    elif names:
        log("作息时间：%s" % preset_name)

    # ---- 解析 ----
    try:
        if is_table:
            result = read_any(args.pdf)
            log("输入类型：Excel/CSV（直接读单元格，不经过 PDF 版面解析）")
        else:
            if args.auto_layout:
                lay, _ = detect_layout(args.pdf, log=log,
                                       period_times=period_times)
            result = parse_pdf(args.pdf, log=log, layout=lay,
                               period_times=period_times)
    except XlsxError as exc:
        print("Excel 有问题：%s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:                        # noqa: BLE001
        print("解析失败：%s" % exc, file=sys.stderr)
        return 1

    # ---- 把解析结果导出成 Excel，供人工核对/修改后再导回 ----
    if args.xlsx_out:
        from .xlsx_source import write_xlsx
        write_xlsx(args.xlsx_out, result.records, include_help=False)
        print("已把 %d 条课程导出到：%s" % (len(result.records), args.xlsx_out))
        print("  → 可以在 Excel 里改错行，再用该文件作为输入重新生成（--xlsx-out 可反复用）")
        if not args.out:
            return 0

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
    for w in result.warnings:
        print("  ⚠ %s" % w, file=sys.stderr)
    for n in result.notes:
        log("提示：%s" % n)

    merged = merge_contiguous(result.records, args.merge)
    log("相连节次合并：%d 条记录 -> %d 个日程（模式 %s）"
        % (len(result.records), len(merged), args.merge))
    log("第 1 周周一 = %s，版式 = %s" % (week1, lay.name))

    # ---- 作息已在解析前确定（见上），这里不再重复 ----

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

    # ---- 节假日：默认联网获取（带本地缓存），取不到就降级、不中止 ----
    # 设计要点：节假日缺失只会「少扣几天假」，不该让整个日历出不来。所以联网失败时
    # 只警告 + 继续生成（退出码仍为 0）。真正不可用的场景（内网/离线）用
    # --holidays-file 兜底；--holidays-url 可指向内网镜像或 file:/// 离线包。
    use_holidays = not args.no_holidays
    holiday_list, makeup_list = [], []
    holidays_src = "未启用"
    if args.holidays_file:
        try:
            holiday_list, makeup_list = hol_mod.load_holiday_file(args.holidays_file)
        except hol_mod.HolidayFileError as exc:
            print("节假日文件有问题：%s" % exc, file=sys.stderr)
            return 2
        holidays_src = "文件 %s" % os.path.basename(args.holidays_file)
        log("已读取节假日文件：%s" % args.holidays_file)
    elif use_holidays:
        from .holiday_fetch import HolidayFetchError, fetch_year, holidays_for_range
        weeks = [w for r in result.records for w in r.all_weeks()]
        max_week = max(weeks) if weeks else 19
        spec = (args.holidays_years or "auto").strip()
        try:
            if spec.lower() in ("auto", "", "学期"):
                span_end = week1 + dt.timedelta(weeks=max_week) - dt.timedelta(days=1)
                yh = holidays_for_range(week1, span_end,
                                        url_template=args.holidays_url,
                                        use_cache=not args.holidays_refresh)
                log("联网获取节假日：按学期 %s ~ %s 推到年份 %s"
                    % (week1, span_end, "、".join(str(y) for y in yh.years)))
            else:
                parts = [p.strip() for p in spec.replace("，", ",").split(",") if p.strip()]
                if len(parts) == 1:
                    yh = fetch_year(int(parts[0]), url_template=args.holidays_url,
                                    use_cache=not args.holidays_refresh)
                else:
                    years = [int(p) for p in parts]
                    yh = holidays_for_range(dt.date(years[0], 1, 1),
                                            dt.date(years[-1], 12, 31),
                                            url_template=args.holidays_url,
                                            use_cache=not args.holidays_refresh)
                log("联网获取节假日：%s 年" % "、".join(str(y) for y in yh.years))
        except ValueError:
            print("错误：--holidays-years 要写年份（如 2026）或 2026,2027，"
                  "或干脆不写（按学期自动推）", file=sys.stderr)
            return 2
        except HolidayFetchError as exc:
            # 关键：不中止。把可操作的说明整段打到 stderr，然后当作「本次不处理节假日」
            print(str(exc), file=sys.stderr)
            print("", file=sys.stderr)
            print("→ 本次仍会生成日历，但不会扣除任何节假日（放假当天的课会被排上）。",
                  file=sys.stderr)
            yh = None
        if yh is None:
            holidays_src = "未取到（已降级为不处理）"
            use_holidays = False
        else:
            holiday_list, makeup_list = yh.holidays, yh.makeup_days
            holidays_src = "联网（%s）" % "、".join(str(y) for y in yh.years)
            for line in yh.describe().splitlines()[1:]:
                log("    " + line)
            if yh.from_cache and args.holidays_url:
                # 缓存只按年份存，不记"这份数据是哪个镜像来的"。换了 --holidays-url 却命中
                # 旧缓存的话，用户会以为新镜像生效了。明确说一句，并给出强制重取的办法。
                log("    注意：这次用的是本地缓存，不是 %s 现取的数据。想让它真正生效，"
                    "加 --holidays-refresh 重新联网。" % args.holidays_url)
            if args.save_holidays:
                from .holiday_fetch import to_holiday_file_text
                with io.open(args.save_holidays, "w", encoding="utf-8") as f:
                    f.write(to_holiday_file_text(yh))
                log("已把这批节假日写成可编辑文件：%s（学校自己调课时改这份，"
                    "以后用 --holidays-file 指它）" % args.save_holidays)

    cfg = Config(
        week1_monday=week1,
        period_times=period_times,
        class_alarm_min=alarm if alarm > 0 else None,
        night_summary=night_on,
        night_hour=nh, night_minute=nm,
        night_duration_min=args.night_duration,
        include_remark_courses=args.remark_courses,
        merge_mode=args.merge,
        calendar_name=args.name or md.calendar_name() or "课表",
        holidays=holiday_list,
        makeup_days=makeup_list,
        holiday_notice=not args.no_holiday_notice,
        layout_id=lay.id,
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
        log("节假日（来源：%s）：" % holidays_src
            + hol_mod.describe(holiday_list, makeup_list))
    elif not args.no_holidays:
        log("节假日：本次未处理（见上面的原因）—— 放假当天的课会被排进日历。")

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
