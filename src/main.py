# -*- coding: utf-8 -*-
"""课表 PDF → 手机日历 (.ics)。

三种用法：
    python main.py                        打开图形界面（双击 exe 也是这个）
    python main.py --cli 课表.pdf ...      命令行模式，参数见 python main.py --cli -h
    python main.py --selftest 课表.pdf 输出.ics 报告.txt
                                          静默跑一遍并写报告（用于打包后验证）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _selftest(pdf: str, out_ics: str, report: str) -> int:
    """不开窗口、不依赖控制台输出，把结果写进报告文件（打包成 windowed exe 后
    没有 stdout，只能这样验证）。"""
    import io
    from timetable import holidays as hol_mod
    from timetable import presets
    from timetable.ics import Config, build_ics, sanity_check_ics
    from timetable.parser import parse_pdf, verify_parse

    lines, code = [], 0
    try:
        res = parse_pdf(pdf)
        chk = verify_parse(res)
        lines.append("解析记录数: %d" % chk["records"])
        lines.append("解析出的上课次数: %d" % chk["instances"])
        lines.append("时间冲突数: %d" % len(chk["conflicts"]))
        lines.append("学期: %s  姓名: %s" % (res.meta.term, res.meta.name))

        cfg = Config(period_times=presets.get_preset(presets.DEFAULT_PRESET),
                     class_alarm_min=20, night_summary=True, night_hour=22, night_minute=0,
                     calendar_name="打包自检",
                     holidays=hol_mod.DEFAULT_HOLIDAYS,
                     makeup_days=hol_mod.DEFAULT_MAKEUP_DAYS)
        text, stats = build_ics(res, cfg)
        with io.open(out_ics, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        st = sanity_check_ics(text)
        lines.append("节假日: %s" % hol_mod.describe(cfg.holidays, cfg.makeup_days))
        lines.append("课程日程: %d" % stats["class_events"])
        lines.append("有效上课次数(已扣除放假): %d" % stats["instances"])
        lines.append("夜间汇总: %d" % stats["night_events"])
        lines.append("EXDATE 条数: %d" % text.count("EXDATE"))
        lines.append("放假提醒条数: %d" % text.count("SUMMARY:明天放假"))
        lines.append("CRLF: %s" % st["crlf_only"])
        lines.append("超长行: %d" % st["over_75"])
        lines.append("VEVENT: %d/%d" % (st["vevent"], st["vevent_end"]))
        lines.append("输出文件字节: %d" % os.path.getsize(out_ics))

        # 界面自检：确认 DPI 感知生效、Pillow 圆角真的能渲染
        # （Pillow 缺失时程序会静默退回直角，这里要能发现）
        try:
            import tkinter as tk
            from timetable import ui_kit
            ui_kit.enable_dpi_awareness()
            r = tk.Tk()
            r.withdraw()
            ui_kit.init_theme(r)
            img = ui_kit.rounded_image(160, 48, 12, "#4F46E5", master=r)
            lines.append("界面: Pillow=%s DPI=%.0f 缩放=%.2f 圆角渲染=%s"
                         % (ui_kit.HAS_PIL, r.winfo_fpixels("1i"), ui_kit.SCALE,
                            "OK" if img is not None else "退回直角"))
            lines.append("屏幕: %dx%d（缩放 %.2f）"
                         % (r.winfo_screenwidth(), r.winfo_screenheight(), ui_kit.SCALE))
            r.destroy()
        except Exception as exc:                       # noqa: BLE001
            lines.append("界面自检失败: %s" % exc)
            code = 1

        # 布局自检：真实建一次主窗口，确认「竖直方向能自由缩放 + 内容装不下时靠滚动条兜住」。
        # 详细回归见 resize_test.py（直接改 geometry）和 resize_wm_test.py（走窗口管理器）。
        try:
            from timetable import ui_kit as _uk
            from timetable.gui import App as _App
            a = _App()
            a.update()
            mw, mh = a.minsize()
            need = (a._inner.winfo_reqheight() + a._hdr.winfo_reqheight()
                    + a._ftr.winfo_reqheight())
            lines.append("布局: 最小 %dx%d 内容需要高 %d" % (mw, mh, need))
            bad = []
            # 打开时的默认尺寸：应当跟屏幕走，约四分之一屏（宽高各一半）
            sw, sh = a.winfo_screenwidth(), a.winfo_screenheight()
            dw, dh = a.winfo_width(), a.winfo_height()
            ratio = float(dw * dh) / float(sw * sh)
            lines.append("默认窗口: %dx%d（屏幕 %dx%d，占 %.1f%% 面积）"
                         % (dw, dh, sw, sh, ratio * 100))
            if not (0.15 <= ratio <= 0.35):
                bad.append("默认窗口面积占比 %.1f%% 偏离四分之一屏" % (ratio * 100))
            if dw < mw - 2 or dh < mh - 2:
                bad.append("默认窗口小于最小尺寸")
            # 最小高度必须明显小于内容高度，否则竖直方向又被锁死了
            if mh > need * 0.75:
                bad.append("最小高度 %d 太接近内容高 %d，竖直方向会被锁死" % (mh, need))
            # 缩到最小尺寸：内容保持自然高度，靠滚动条查看，控件不许被压扁
            a.geometry("%dx%d" % (mw, mh))
            a.update()
            tree_h, log_h = a.tree.winfo_height(), a.txt.winfo_height()
            lines.append("最小时: 表格=%d 日志=%d 滚动条=%s"
                         % (tree_h, log_h, "显示" if a._sb_shown else "隐藏"))
            if tree_h < _uk.px(140):
                bad.append("预览表过矮(%d)" % tree_h)
            if log_h < _uk.px(70):
                bad.append("日志过矮(%d)" % log_h)
            if not a._sb_shown:
                bad.append("内容装不下却没有滚动条")
            else:
                a._canvas.yview_moveto(1.0)
                a.update()
                if a._canvas.yview()[1] < 0.999:
                    bad.append("滚动条滚不到底")
            # 拉大窗口：内容要跟着变大，且滚动条要收起来
            a.geometry("%dx%d" % (_uk.px(1400), min(_uk.px(950),
                                                   a.winfo_screenheight() - _uk.px(40))))
            a.update()
            lines.append("放大后: 表格=%d 滚动条=%s"
                         % (a.tree.winfo_height(),
                            "显示" if a._sb_shown else "隐藏"))
            if a.tree.winfo_height() <= tree_h:
                bad.append("窗口放大后预览表没有变高")
            a.destroy()
            if bad:
                lines.append("布局自检: FAIL " + "、".join(bad))
                code = 1
            else:
                lines.append("布局自检: OK")
        except Exception as exc:                       # noqa: BLE001
            lines.append("布局自检失败: %s" % exc)
            code = 1

        lines.append("RESULT: %s" % ("OK" if code == 0 else "FAIL"))
    except Exception as exc:                            # noqa: BLE001
        import traceback
        lines.append("RESULT: FAIL")
        lines.append(str(exc))
        lines.append(traceback.format_exc())
        code = 1

    with io.open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code


def _shot(out_png: str, gw: int = 0, gh: int = 0) -> int:
    """把主窗口截成 PNG，用于「缩放后有没有正确显示」的像素级验证。

    截图必须在程序自己进程里做：本机 200% 缩放，外部截图工具（PowerShell）不是 DPI
    感知的，拿到的坐标是逻辑像素，截出来是半张错位图。
    """
    import time

    from timetable import ui_kit
    from timetable.gui import App

    ui_kit.enable_dpi_awareness()
    app = App()
    app.update()
    if gw and gh:
        # 一定要夹到屏幕内并显式给定位置：窗口有一部分在屏幕外的话，
        # ImageGrab 抓到的窗外区域是黑的，分析截图时会误判成「没画出来」。
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        gw = min(gw, sw - ui_kit.px(30))
        gh = min(gh, sh - ui_kit.px(50))
        app.geometry("%dx%d+%d+%d" % (gw, gh, ui_kit.px(10), ui_kit.px(10)))
        app.update()
        time.sleep(0.4)
        app.update()
    app.deiconify()
    app.lift()
    app.update()
    time.sleep(0.5)
    app.update()

    from PIL import ImageGrab
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out_png)
    print("窗口 %dx%d @(%d,%d) 表格 %dx%d 日志 %dx%d 最小 %dx%d -> %s"
          % (w, h, x, y, app.tree.winfo_width(), app.tree.winfo_height(),
             app.txt.winfo_width(), app.txt.winfo_height(),
             app.minsize()[0], app.minsize()[1], out_png))
    app.destroy()
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        if len(argv) != 4:
            return 2
        return _selftest(argv[1], argv[2], argv[3])
    if argv and argv[0] == "--shot":
        if len(argv) < 2:
            return 2
        gw = int(argv[2]) if len(argv) > 2 else 0
        gh = int(argv[3]) if len(argv) > 3 else 0
        return _shot(argv[1], gw, gh)
    if argv and argv[0] in ("--cli", "-c"):
        from timetable.cli import main as cli_main
        return cli_main(argv[1:])
    from timetable.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
