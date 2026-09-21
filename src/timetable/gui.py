# -*- coding: utf-8 -*-
"""图形界面：选择课表 PDF → 解析 → 预览/修改 → 生成 .ics。

界面用 ui_kit 里的自绘组件做成现代扁平风格（深色标题带 + 白色卡片 + 圆角按钮）。

业务逻辑都放在可直接调用的方法里（load_pdf / do_parse / do_generate），
并且 headless=True 时所有弹窗只写日志不阻塞，这样不点鼠标也能被自动化测试驱动
（见 smoke_test.py）。
"""
from __future__ import annotations

import copy
import datetime as dt
import io
import os
import re
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import doctor
from . import holidays as hol_mod
from . import presets
from . import ui_kit as ui
from .ics import Config, IcsError, build_ics, sanity_check_ics
from .parser import (WEEKDAY_SHORT, ParseResult, Record, Segment, compress_weeks,
                     expand_weeks, parse_pdf, verify_parse)

MERGE_SEGMENTS = [("相连就合并", "all"), ("只合并碎片", "artifact"), ("不合并", "none")]

P = ui.PALETTE


# --------------------------------------------------------------------- 周次表达式
def parse_weeks_expr(text: str):
    """把「1~8」「18」「2,4,6」「2~4(双)」这类写法解析成周次列表。"""
    text = (text or "").strip().replace("，", ",").replace("、", ",")
    if not text:
        return []
    weeks = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        parity = None
        m = re.search(r"[（(]?\s*([单双])\s*[)）]?\s*$", part)
        if m:
            parity = m.group(1)
            part = part[:m.start()].strip()
        part = part.replace("~", "-").replace("—", "-").replace("－", "-")
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            weeks += expand_weeks(int(m.group(1)), int(m.group(2)), parity)
            continue
        m = re.fullmatch(r"(\d+)", part)
        if m:
            weeks += expand_weeks(int(m.group(1)), None, parity)
    return sorted(set(weeks))


def weeks_to_expr(weeks) -> str:
    return compress_weeks(sorted(set(weeks)))


# --------------------------------------------------------------------- 主窗口
class App(tk.Tk):
    def __init__(self):
        # 必须在创建 Tk 之前声明 DPI 感知：本机是 3072x1920 / 192 DPI（200% 缩放），
        # 不声明的话程序会以 96 DPI 渲染后被 Windows 位图放大 2 倍，字全是锯齿。
        ui.enable_dpi_awareness()
        super().__init__()
        ui.init_theme(self)
        self.title("课表 PDF → 手机日历")
        # 初始尺寸先给一个保守值，真正的尺寸在 _apply_minsize() 里按屏幕大小算
        # （默认取屏幕宽高各一半，也就是大约占屏幕面积的四分之一）。
        _w, _h = ui.fit_size(self, ui.px(900), ui.px(600))
        self.geometry("%dx%d" % (_w, _h))
        self.minsize(ui.px(680), ui.px(470))

        self.result: ParseResult | None = None
        self.records: list[Record] = []
        self.period_times = presets.get_preset(presets.DEFAULT_PRESET)
        self.holiday_file = None
        self.headless = False          # True 时弹窗只写日志，便于自动化测试

        self.var_pdf = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_week1 = tk.StringVar(value="2026-08-31")
        self.var_preset = tk.StringVar(value=presets.DEFAULT_PRESET)
        self.var_period_hint = tk.StringVar()
        self.var_alarm_on = tk.BooleanVar(value=True)
        self.var_alarm_min = tk.StringVar(value="20")
        self.var_night_on = tk.BooleanVar(value=True)
        self.var_night_time = tk.StringVar(value="22:00")
        self.var_remark = tk.BooleanVar(value=False)
        self.var_merge = tk.StringVar(value="all")
        self.var_holiday = tk.BooleanVar(value=True)
        self.var_holiday_notice = tk.BooleanVar(value=True)
        self.var_holiday_hint = tk.StringVar()
        self.var_footer = tk.StringVar(value="选择课表 PDF，然后点右下角「解析课表」。")
        self._log_lines = 5      # 日志行数，屏幕太矮时自动让位给预览表
        self._need_h = 0         # 完整布局所需的窗口高度（自然尺寸）

        self._build_ui()
        self._bind_keys()
        self._refresh_period_hint()
        self._refresh_holiday_hint()

    # ------------------------------------------------------------------ 构建界面
    def _build_ui(self):
        # 顺序很重要：先 pack 顶栏和底栏（side=top/bottom），中间区才拿剩余空间。
        # 否则 expand=True 的中间区会先吃掉全部高度，把底栏挤成 1x1。
        self._hdr = ui.Header(
            self,
            title="课表 PDF → 手机日历",
            subtitle="把教务系统的课表转成带提醒的 .ics，可导入 iPhone / iPad / Mac / Android 日历",
            right_factory=self._build_header_right,
        )
        self._hdr.pack(fill="x")
        self._ftr = self._build_footer()

        # 中间区放进「可滚动画布」里，这样窗口无论缩到多小，内容都不会被压扁：
        # 装得下时预览表占满剩余高度（正常情况），装不下时出现纵向滚动条。
        # 顶栏和底栏不参与滚动，始终可见。
        outer = tk.Frame(self, bg=P["bg"])
        outer.pack(fill="both", expand=True)
        self._vsb = ttk.Scrollbar(outer, orient="vertical",
                                  command=self._yview, style="Modern.Vertical.TScrollbar")
        self._canvas = tk.Canvas(outer, bg=P["bg"], highlightthickness=0, bd=0,
                                 yscrollcommand=self._vsb.set)
        # 画布先只占位不 pack：滚动条是否需要，等布局算完再决定（见 _sync_scroll）
        self._canvas.pack(fill="both", expand=True)
        self._sb_shown = False
        self._sc_slack = ui.px(24)   # 允许内容被压缩这么多，不急着显示滚动条

        self._inner = tk.Frame(self._canvas, bg=P["bg"])
        self._inner_id = self._canvas.create_window((0, 0), window=self._inner,
                                                    anchor="nw")
        self._cfg = self._build_config_card(self._inner)
        self._lower = self._build_lower(self._inner)

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # 滚轮：指针在预览表 / 日志里时交给它们自己滚，在别处才翻这一页
        self.bind("<MouseWheel>", self._on_wheel, add="+")
        self._apply_minsize()

    # ------------------------------------------------------------ 滚动与自适应
    def _yview(self, *args):
        """滚动条的 command：内容装得下时忽略请求，避免滑动后空白。"""
        if self._sb_shown:
            self._canvas.yview(*args)

    def _on_inner_configure(self, _event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        # 内层宽度跟着画布走（卡片横向填满），高度取「内容自然高」和「画布高」的较大值：
        # 窗口拉高时预览表跟着变高，窗口压矮时内容保持自然高、由滚动条兜住。
        self._canvas.itemconfigure(self._inner_id, width=event.width)
        self._sync_scroll(event.height)
        # 宽度变化可能让配置卡里的提示文字换行 → 需要的高度变了，再校一次
        self.after_idle(lambda: self._sync_scroll(self._canvas.winfo_height()))

    def _sync_scroll(self, view_h=None):
        if view_h is None:
            view_h = self._canvas.winfo_height()
        try:
            need = self._inner.winfo_reqheight()
        except Exception:
            return
        if view_h <= 1:
            return
        # 差得不多时先让预览表被压一点（它是可伸缩的那一块），别为几像素就冒出滚动条
        slack = self._sc_slack
        want_sb = need > view_h + slack
        self._canvas.itemconfigure(self._inner_id,
                                   height=need if want_sb else view_h)
        if want_sb == self._sb_shown:
            return
        # 只在状态真的改变时动 pack，否则 Configure 会和布局互相触发打转
        self._sb_shown = want_sb
        if want_sb:
            self._vsb.pack(side="right", fill="y", before=self._canvas)
        else:
            self._vsb.pack_forget()
            self._canvas.yview_moveto(0)

    def _on_wheel(self, event):
        """滚轮：预览表和日志自己滚，其余位置翻整页。"""
        w = self.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            if w in (self.tree, self.txt):
                return None
            if w is self:
                break
            try:
                w = w.master
            except Exception:
                break
        if not self._sb_shown:
            return None
        self._canvas.yview_scroll(int(-event.delta / 120) * 3, "units")
        return "break"

    def _apply_minsize(self):
        """按屏幕大小决定「最小尺寸」和「打开时的默认尺寸」。

        两条经验（都踩过）：
        1. 最小尺寸不能按「内容完整可见」来定（那会把竖直方向锁死：内容要 1823 物理像素，
           而屏幕可用高度只有 1879，能拖的范围只剩 56 像素）。最小尺寸只保证顶栏、底栏、
           预览表各留一点可见高度，装不下的部分交给滚动条。
        2. 默认尺寸要跟着屏幕走，别写死。这里取屏幕宽高各一半（面积约为整屏的四分之一），
           再夹到 [最小尺寸, 屏幕可用范围] 之间 —— 大屏小屏、不同缩放下都不会一开窗就糊满屏幕。
        """
        try:
            self.update_idletasks()
            self._need_h = int(self._inner.winfo_reqheight())
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            min_w, min_h = ui.px(680), ui.px(470)
            self.minsize(int(min(min_w, sw - ui.px(20))), int(min(min_h, sh - ui.px(20))))
            # 默认约 1/4 屏（宽高各一半），并保证不小于最小尺寸、不超出屏幕
            w, h = ui.fit_size(self, sw // 2, sh // 2, margin=20)
            w = max(int(w), int(min_w))
            h = max(int(h), int(min_h))
            self.geometry("%dx%d" % (w, h))
            self.update_idletasks()
            self._sync_scroll()
        except Exception:
            self.minsize(ui.px(680), ui.px(470))

    def _on_configure(self, event):
        """窗口尺寸变化时重新核对滚动状态（内容装不下就显示滚动条）。

        只在状态真的变化时才动 pack，避免 Configure 与布局互相触发打转。
        """
        if event is not None and event.widget is not self:
            return
        self._sync_scroll()

    def _build_header_right(self, parent):
        self.pill = ui.Pill(parent, text="尚未解析课表", level="mute", bg=P["navy"])
        self.pill.pack(anchor="e", pady=(6, 0))

    # ------------------------------------------------------------------ 配置卡
    def _build_config_card(self, parent):
        """文件 + 参数合并成一张紧凑的双列卡片，省出纵向空间给预览表。"""
        card = ui.Card(parent, title="配置", subtitle="换学期时只需要改日期和节假日",
                       pad=ui.SPACE["md"])
        card.pack(fill="x", padx=ui.SPACE["lg"], pady=(ui.SPACE["md"], 0))
        root = card.body

        def mk(parent, text, small=False):
            return tk.Label(parent, text=text, bg=P["card"],
                            fg=P["text3"] if small else P["text2"],
                            font=ui.FONTS["small"] if small else ui.FONTS["body"])

        # ---- 文件两行 ----
        top = tk.Frame(root, bg=P["card"])
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        mk(top, "课表 PDF").grid(row=0, column=0, sticky="w", padx=(0, ui.SPACE["md"]))
        ui.RoundedInput(top, textvariable=self.var_pdf,
                        width=ui.px(420)).grid(row=0, column=1, sticky="ew")
        ui.RoundedButton(top, text="浏览…", kind="secondary", height=ui.px(32),
                         command=self._pick_pdf).grid(row=0, column=2,
                                                      padx=(ui.SPACE["sm"], 0))
        mk(top, "输出 .ics").grid(row=1, column=0, sticky="w",
                                  padx=(0, ui.SPACE["md"]), pady=(ui.SPACE["sm"], 0))
        ui.RoundedInput(top, textvariable=self.var_out,
                        width=ui.px(420)).grid(row=1, column=1, sticky="ew",
                                               pady=(ui.SPACE["sm"], 0))
        ui.RoundedButton(top, text="另存为…", kind="secondary", height=ui.px(32),
                         command=self._pick_out).grid(row=1, column=2,
                                                      padx=(ui.SPACE["sm"], 0),
                                                      pady=(ui.SPACE["sm"], 0))

        # ---- 参数：3 行 × 2 列 ----
        mid = tk.Frame(root, bg=P["card"])
        mid.pack(fill="x", pady=(ui.SPACE["md"], 0))
        mid.columnconfigure(1, weight=1)
        mid.columnconfigure(3, weight=1)

        def cell(r, c, text):
            lab = mk(mid, text)
            lab.grid(row=r, column=c, sticky="w",
                     padx=(0 if c == 0 else ui.SPACE["lg"], ui.SPACE["sm"]),
                     pady=ui.px(3))
            return lab

        def box(r, c, span=1):
            f = tk.Frame(mid, bg=P["card"])
            f.grid(row=r, column=c, columnspan=span, sticky="w", pady=ui.px(3))
            return f

        # 行 0：第 1 周周一 ｜ 作息时间
        cell(0, 0, "第 1 周周一")
        w = box(0, 1)
        ui.RoundedInput(w, textvariable=self.var_week1,
                        width=ui.px(124)).pack(side="left")
        mk(w, "须是周一", small=True).pack(side="left", padx=(ui.SPACE["sm"], 0))

        cell(0, 2, "作息时间")
        w = box(0, 3)
        cb = ui.RoundedInput(w, textvariable=self.var_preset, kind="combo",
                             values=list(presets.PRESETS), width=ui.px(300))
        cb.pack(side="left")
        cb.widget.bind("<<ComboboxSelected>>", lambda e: self._on_preset())
        ui.RoundedButton(w, text="节次时间…", kind="ghost", height=ui.px(32),
                         command=self._edit_periods).pack(side="left",
                                                          padx=(ui.SPACE["sm"], 0))

        # 行 1：课前提醒 ｜ 相连节次 + 备注课程
        cell(1, 0, "课前提醒")
        w = box(1, 1)
        ui.Switch(w, text="提前", variable=self.var_alarm_on).pack(side="left")
        ui.RoundedInput(w, textvariable=self.var_alarm_min,
                        width=ui.px(58)).pack(side="left",
                                             padx=(ui.SPACE["sm"], ui.SPACE["xs"]))
        mk(w, "分钟").pack(side="left")

        cell(1, 2, "相连节次")
        w = box(1, 3)
        ui.Segmented(w, MERGE_SEGMENTS, variable=self.var_merge).pack(side="left")
        ui.Switch(w, text="备注课程也加为全天日程",
                  variable=self.var_remark).pack(side="left", padx=(ui.SPACE["lg"], 0))

        # 行 2：每晚汇报 ｜ 节假日
        cell(2, 0, "每晚汇报")
        w = box(2, 1)
        ui.Switch(w, text="", variable=self.var_night_on).pack(side="left")
        ui.RoundedInput(w, textvariable=self.var_night_time,
                        width=ui.px(76)).pack(side="left",
                                             padx=(ui.SPACE["sm"], ui.SPACE["xs"]))
        mk(w, "汇总第二天").pack(side="left")

        cell(2, 2, "节假日")
        w = box(2, 3)
        ui.Switch(w, text="按法定节假日停课", variable=self.var_holiday,
                  command=self._refresh_holiday_hint).pack(side="left")
        ui.Switch(w, text="放假前一晚提醒", variable=self.var_holiday_notice).pack(
            side="left", padx=(ui.SPACE["md"], 0))
        ui.RoundedButton(w, text="载入文件…", kind="ghost", height=ui.px(32),
                         command=self._pick_holiday_file).pack(side="left",
                                                               padx=(ui.SPACE["md"], 0))
        ui.RoundedButton(w, text="导出模板", kind="ghost", height=ui.px(32),
                         command=self._export_holiday_template).pack(
            side="left", padx=(ui.SPACE["sm"], 0))

        # ---- 提示行 ----
        hint = tk.Frame(root, bg=P["card"])
        hint.pack(fill="x", pady=(ui.SPACE["sm"], 0))
        mk(hint, "", small=True)
        tk.Label(hint, textvariable=self.var_period_hint, bg=P["card"],
                 fg=P["text3"], font=ui.FONTS["small"]).pack(side="left")
        tk.Label(hint, textvariable=self.var_holiday_hint, bg=P["card"],
                 fg=P["text3"], font=ui.FONTS["small"]).pack(side="right")
        return card

    # ------------------------------------------------------------------ 下半部分
    def _build_lower(self, parent):
        # 这里刻意不用 PanedWindow。PanedWindow 是按比例压缩两个面板的，窗口变矮时
        # 预览表和日志会一起被压扁（实测最小尺寸下日志只剩 37 像素高）。
        # 改成「日志固定高度 + 预览表占满剩余」，这样无论怎么缩放，日志始终可读，
        # 尺寸变化全部由表格吸收（表格自带滚动条，压扁了也能看），配合
        # _apply_minsize() 保证表格也不会被压没。
        #
        # 关键：日志卡必须比预览卡“先 pack”。tk 的 packer 按登记顺序分配空间，
        # 先登记的先用掉自己请求的高度；若先 pack 预览卡，空间不够时日志卡会被挤成
        # 一条线（实测 1689 高时日志只剩 25 像素）。用 side="bottom" 先登记日志卡，
        # 视觉顺序仍然是「预览在上、日志在下」。
        lg = ui.Card(parent, title="日志")
        lg.pack(side="bottom", fill="x", padx=ui.SPACE["lg"],
                pady=(ui.SPACE["md"], ui.SPACE["md"]))
        self.txt = tk.Text(lg.body, height=self._log_lines, wrap="word", bd=0,
                           highlightthickness=0, bg=P["card"], fg=P["text"],
                           font=ui.FONTS["body"], padx=2, pady=2,
                           selectbackground=P["sel"], selectforeground=P["accent_lo"],
                           state="disabled")
        sb = ttk.Scrollbar(lg.body, orient="vertical", command=self.txt.yview,
                           style="Modern.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("err", foreground=P["err"])
        self.txt.tag_configure("warn", foreground=P["warn"])
        self.txt.tag_configure("ok", foreground=P["ok_fg"])
        self.txt.tag_configure("head", foreground=P["accent_lo"],
                               font=ui.FONTS["body_b"])

        pv = ui.Card(parent, title="课程预览", subtitle="双击一行可修改；周次写成 1~8 或 2,4,6")
        pv.pack(fill="both", expand=True, padx=ui.SPACE["lg"],
                pady=(ui.SPACE["md"], 0))
        inner = tk.Frame(pv.body, bg=P["card"])
        inner.pack(fill="both", expand=True)

        # 右侧按钮先 pack，表格区吃掉剩下的空间
        side = tk.Frame(inner, bg=P["card"])
        side.pack(side="right", fill="y", padx=(ui.SPACE["md"], 0))
        for txt, cmd, kind in (("修改", lambda: self._edit_row(None), "secondary"),
                               ("新增", self._add_row, "secondary"),
                               ("删除", self._del_row, "danger")):
            ui.RoundedButton(side, text=txt, kind=kind, command=cmd,
                             min_width=ui.px(76)).pack(fill="x",
                                                       pady=(0, ui.SPACE["sm"]))

        grid = tk.Frame(inner, bg=P["card"])
        grid.pack(side="left", fill="both", expand=True)

        self._cols = ("day", "course", "code", "period", "time", "weeks", "room", "teacher")
        heads = {"day": "星期", "course": "课程", "code": "代码", "period": "节次",
                 "time": "时间", "weeks": "周次", "room": "教室", "teacher": "教师"}
        # 每列的「理想宽度」与「再窄也不能低于」的宽度（逻辑像素）。
        # 窗口变窄时按比例把各列压缩到理想宽度和最小宽度之间，这样右边几列
        # （教室 / 教师）不会因为被裁掉而看不见；实在塞不下才出现横向滚动条。
        self._col_w = {"day": 46, "course": 250, "code": 44, "period": 62, "time": 100,
                       "weeks": 96, "room": 150, "teacher": 180}
        self._col_min = {"day": 40, "course": 110, "code": 40, "period": 48,
                         "time": 66, "weeks": 56, "room": 72, "teacher": 84}

        self.tree = ttk.Treeview(grid, columns=self._cols, show="headings", height=5,
                                 style="Modern.Treeview", selectmode="browse")
        for c in self._cols:
            self.tree.heading(c, text=heads[c], anchor="w")
            self.tree.column(c, width=ui.px(self._col_w[c]), anchor="w",
                             minwidth=ui.px(self._col_min[c]), stretch=False)
        self.tree.tag_configure("odd", background=P["row_alt"])

        vs = ttk.Scrollbar(grid, orient="vertical", command=self.tree.yview,
                           style="Modern.Vertical.TScrollbar")
        self._hs = ttk.Scrollbar(grid, orient="horizontal", command=self.tree.xview,
                                 style="Modern.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=self._hs.set)
        self._hs_shown = False
        vs.pack(side="right", fill="y")
        self.tree.pack(side="top", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._edit_row)
        self.tree.bind("<Configure>", self._fit_columns)
        return lg

    # ------------------------------------------------------------ 预览表列宽自适应
    def _fit_columns(self, _event=None):
        """按表格当前宽度分配各列宽度，保证「能看见的列都看得全」。

        ttk.Treeview 不会自动压缩列宽：宽度之和超过控件宽度时，右边的列直接被裁掉
        （左右缩放后「教室 / 教师」消失就是这么来的）。这里按比例压缩到最小宽度为止，
        再塞不下才显示横向滚动条。
        """
        if getattr(self, "_fitting", False):
            return
        avail = self.tree.winfo_width()
        if avail <= 1:
            return
        base = [(c, ui.px(self._col_w[c]), ui.px(self._col_min[c])) for c in self._cols]
        total_base = sum(b for _, b, _ in base)
        total_min = sum(m for _, _, m in base)
        if avail >= total_base:
            want = {c: b for c, b, _ in base}
            need_hs = False
        elif avail >= total_min:
            share = float(total_base - avail) / float(total_base - total_min)
            want = {c: max(m, int(round(b - (b - m) * share))) for c, b, m in base}
            # 逐列取整会有几像素误差，可能让最后一列被切掉一点，这里把差值补回去
            diff = avail - sum(want.values())
            if diff:
                for c, b, m in sorted(base, key=lambda t: t[1] - want[t[0]],
                                      reverse=True):
                    if diff == 0:
                        break
                    if diff > 0:
                        add = min(b - want[c], diff)
                        want[c] += add
                        diff -= add
                    else:
                        cut = min(want[c] - m, -diff)
                        want[c] -= cut
                        diff += cut
            need_hs = False
        else:
            want = {c: m for c, _, m in base}
            need_hs = True

        self._fitting = True
        try:
            for c in self._cols:
                if int(self.tree.column(c, "width")) != want[c]:
                    self.tree.column(c, width=want[c])
            if need_hs != self._hs_shown:
                self._hs_shown = need_hs
                if need_hs:
                    self._hs.pack(side="bottom", fill="x", before=self.tree)
                else:
                    self._hs.pack_forget()
                    self.tree.xview_moveto(0)
        finally:
            self._fitting = False

    # ------------------------------------------------------------------ 底部栏
    def _build_footer(self):
        bar = tk.Frame(self, bg=P["card"])
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=P["border"], height=1).pack(fill="x")

        inner = tk.Frame(bar, bg=P["card"])
        inner.pack(fill="x", padx=ui.SPACE["lg"], pady=ui.SPACE["sm"])
        tk.Label(inner, textvariable=self.var_footer, bg=P["card"], fg=P["text2"],
                 font=ui.FONTS["small"]).pack(side="left")

        ui.RoundedButton(inner, text="生成 .ics", kind="primary",
                         font=ui.FONTS["btn_big"], height=ui.px(38),
                         min_width=ui.px(116),
                         command=self.do_generate).pack(side="right")
        ui.RoundedButton(inner, text="解析课表", kind="secondary",
                         font=ui.FONTS["btn_big"], height=ui.px(38),
                         min_width=ui.px(116),
                         command=self.do_parse).pack(side="right",
                                                     padx=(0, ui.SPACE["sm"]))
        ui.RoundedButton(inner, text="打开输出目录", kind="ghost",
                         command=self._open_out_dir).pack(side="right",
                                                          padx=(0, ui.SPACE["sm"]))
        return bar

    def _bind_keys(self):
        self.bind("<F5>", lambda e: self.do_parse())
        self.bind("<Control-s>", lambda e: self.do_generate())
        self.bind("<Control-o>", lambda e: self._pick_pdf())

    # ------------------------------------------------------------------ 基础
    def log(self, msg=""):
        s = str(msg)
        tag = ""
        if any(k in s for k in ("错误", "失败", "⚠")):
            tag = "err"
        elif any(k in s for k in ("警告", "提示：")):
            tag = "warn"
        elif any(k in s for k in ("通过", "完成", "已生成")):
            tag = "ok"
        elif s.startswith("=") or s.endswith("：") and len(s) < 14:
            tag = "head"
        self.txt.configure(state="normal")
        self.txt.insert("end", s + "\n", tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")
        self.update_idletasks()

    def _reveal_log(self):
        """窗口装不下全部内容（出现了滚动条）时，把视图翻到底，让日志露出来。

        只在解析 / 生成结束时调用，不跟着每一行日志走 —— 否则解析过程中视图会被
        一直往回拽，用户没法边看预览表边等。
        """
        if getattr(self, "_sb_shown", False):
            try:
                self._canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _set_status(self, text, level="mute"):
        try:
            self.pill.set(text, level)
        except Exception:
            pass
        self.var_footer.set(text)

    def _info(self, title, msg):
        self.log("[%s] %s" % (title, str(msg).replace("\n", " ")))
        if not self.headless:
            messagebox.showinfo(title, msg, parent=self)

    def _warn(self, title, msg):
        self.log("[%s] %s" % (title, str(msg).replace("\n", " ")))
        if not self.headless:
            messagebox.showwarning(title, msg, parent=self)

    def _error(self, title, msg):
        self.log("[%s] %s" % (title, str(msg).replace("\n", " ")))
        if not self.headless:
            messagebox.showerror(title, msg, parent=self)

    def _confirm(self, title, msg):
        self.log("[%s] %s" % (title, str(msg).replace("\n", " ")))
        if self.headless:
            return True
        return messagebox.askyesno(title, msg, parent=self)

    def _refresh_period_hint(self):
        # 不能把 13 个节次全列出来：那样这行会宽到 1200px，把整张卡片撑爆
        times = self.period_times
        first = times.get(1, ("?", "?"))
        self.var_period_hint.set("第 1 节 %s-%s … 共 %d 节"
                                 % (first[0], first[1], len(times)))

    def _refresh_holiday_hint(self):
        def short(hs, ms):
            h = "、".join("%s %d/%d-%d/%d" % (x.name, x.start.month, x.start.day,
                                              x.end.month, x.end.day) for x in hs)
            m = "、".join("%d/%d" % (x.date.month, x.date.day) for x in ms)
            return "节假日：%s%s" % (h or "无", ("（调休 %s）" % m) if m else "")

        if self.holiday_file:
            try:
                hs, ms = hol_mod.load_holiday_file(self.holiday_file)
                self.var_holiday_hint.set("%s ｜ %s" % (
                    short(hs, ms), os.path.basename(self.holiday_file)))
            except hol_mod.HolidayFileError as exc:
                self.var_holiday_hint.set("节假日文件有问题：%s" % exc)
            return
        if self.var_holiday.get():
            self.var_holiday_hint.set(short(hol_mod.DEFAULT_HOLIDAYS,
                                            hol_mod.DEFAULT_MAKEUP_DAYS))
        else:
            self.var_holiday_hint.set("未处理节假日（放假期间也会排课）")

    def _holidays_for_config(self):
        if self.holiday_file:
            return hol_mod.load_holiday_file(self.holiday_file)
        if self.var_holiday.get():
            return list(hol_mod.DEFAULT_HOLIDAYS), list(hol_mod.DEFAULT_MAKEUP_DAYS)
        return [], []

    def _on_preset(self):
        self.period_times = presets.get_preset(self.var_preset.get())
        self._refresh_period_hint()

    # ------------------------------------------------------------------ 选文件
    def _pick_pdf(self):
        p = filedialog.askopenfilename(
            title="选择课表 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")], parent=self)
        if p:
            self.load_pdf(p)

    def _pick_out(self):
        p = filedialog.asksaveasfilename(title="保存 .ics", defaultextension=".ics",
                                         filetypes=[("iCalendar", "*.ics")],
                                         parent=self)
        if p:
            self.var_out.set(p)

    def _open_out_dir(self):
        out = self.var_out.get()
        d = os.path.dirname(out) if out else ""
        if d and os.path.isdir(d):
            os.startfile(d)          # noqa: S606
        else:
            self._info("提示", "还没有输出目录。")

    def _pick_holiday_file(self):
        p = filedialog.askopenfilename(
            title="选择本学期的节假日安排文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")], parent=self)
        if not p:
            return
        try:
            hs, ms = hol_mod.load_holiday_file(p)
        except hol_mod.HolidayFileError as exc:
            self._error("节假日文件有问题", str(exc))
            return
        if not hs and not ms:
            self._warn("提示", "这个文件里没有任何「放假」或「调休」行。")
            return
        self.holiday_file = p
        self._refresh_holiday_hint()
        self.log("已载入节假日文件：%s" % p)
        for h in hs:
            self.log("    " + h.label())
        for m in ms:
            self.log("    调休 %s" % m.date)

    def _export_holiday_template(self):
        p = filedialog.asksaveasfilename(
            title="导出节假日文件模板", defaultextension=".txt",
            initialfile="节假日安排.txt", filetypes=[("文本文件", "*.txt")],
            parent=self)
        if not p:
            return
        try:
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(hol_mod.HOLIDAY_FILE_TEMPLATE)
        except OSError as exc:
            self._error("写文件失败", str(exc))
            return
        self.log("已导出模板：%s" % p)
        self.log("下学期照着改这个文件，再用「载入节假日文件…」选它即可。")
        self._info("完成", "模板已导出：\n%s\n\n下学期改这个文件即可，不必动代码。" % p)

    # ------------------------------------------------------------------ 解析
    def load_pdf(self, path: str):
        self.var_pdf.set(path)
        if not self.var_out.get():
            self.var_out.set(os.path.splitext(path)[0] + ".ics")
        self._set_status("已选择课表，等待解析", "info")

    def do_parse(self):
        pdf = self.var_pdf.get().strip()
        if not pdf:
            self._warn("提示", "请先选择课表 PDF。")
            return False
        if not os.path.isfile(pdf):
            self._error("错误", "找不到文件：\n%s" % pdf)
            return False

        self.log("")
        self.log("=" * 56)
        self._set_status("正在解析…", "info")
        try:
            self.result = parse_pdf(pdf, log=self.log)
        except Exception as exc:                       # noqa: BLE001
            self.log("解析失败：%s" % exc)
            self._set_status("解析失败", "err")
            self._error("解析失败", str(exc))
            return False

        self.records = copy.deepcopy(self.result.records)

        md = self.result.meta
        chk = verify_parse(self.result)
        self.log("共 %d 条课程安排、合计 %d 次课。" % (chk["records"], chk["instances"]))
        if chk["conflicts"]:
            self._set_status("解析完成，但有 %d 处时间冲突" % len(chk["conflicts"]), "warn")
            self.log("⚠ 检测到 %d 处时间冲突（同一天同一时段有两门课），请检查："
                     % len(chk["conflicts"]))
            for c in chk["conflicts"][:8]:
                self.log("   第%d周%s 第%d节：%s / %s" % c)
        else:
            self._set_status("解析完成 · %d 条 · %d 次课"
                             % (chk["records"], chk["instances"]), "ok")
        for w in self.result.warnings:
            self.log("提示：" + w)
        if md.remark_courses:
            self.log("备注中无固定时间的课程：%s"
                     % "、".join(c["name"] for c in md.remark_courses))
        self._refresh_table()
        self._reveal_log()
        return True

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.records):
            self.tree.insert("", "end", iid=str(i), values=self._row_values(r),
                             tags=("odd",) if i % 2 else ())

    def _row_values(self, r: Record):
        pt = self.period_times
        try:
            t = "%s-%s" % (pt[r.pfrom][0], pt[r.pto][1])
        except KeyError:
            t = "（未设置）"
        return (r.day_name, r.course, r.code, "%d-%d节" % (r.pfrom, r.pto), t,
                weeks_to_expr(r.all_weeks()), r.room, "；".join(r.teacher_desc()))

    # ------------------------------------------------------------------ 表格编辑
    def _selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _edit_row(self, _event=None):
        idx = self._selected_index()
        if idx is None:
            self._info("提示", "请先在表格里选中一行。")
            return
        dlg = _RecordDialog(self, self.records[idx], self.period_times)
        self.wait_window(dlg)
        if dlg.saved:
            self.records[idx] = dlg.result_record()
            self.tree.item(str(idx), values=self._row_values(self.records[idx]))
            self.log("已修改：" + self.records[idx].course)

    def _add_row(self):
        r = Record(day=0, course="新课程", code="", pfrom=1, pto=2, room="",
                   segments=[Segment(weeks=list(range(1, 17)), teacher="")])
        dlg = _RecordDialog(self, r, self.period_times)
        self.wait_window(dlg)
        if dlg.saved:
            self.records.append(dlg.result_record())
            self._refresh_table()
            self.log("已新增：" + self.records[-1].course)

    def _del_row(self):
        idx = self._selected_index()
        if idx is None:
            return
        name = self.records[idx].course
        if self._confirm("确认", "删除「%s」？" % name):
            self.records.pop(idx)
            self._refresh_table()
            self.log("已删除：" + name)

    def _edit_periods(self):
        dlg = _PeriodDialog(self, self.period_times)
        self.wait_window(dlg)
        if dlg.saved:
            self.period_times = dlg.result_times()
            self.var_preset.set("（自定义）")
            self._refresh_period_hint()
            self._refresh_table()
            self.log("已更新节次时间。")

    # ------------------------------------------------------------------ 生成
    def _build_config(self) -> Config:
        try:
            week1 = dt.date.fromisoformat(self.var_week1.get().strip())
        except ValueError:
            raise IcsError("「第 1 周周一」请按 2026-08-31 的格式填写。")
        if week1.weekday() != 0:
            if not self._confirm("确认",
                                 "%s 不是周一。继续的话所有课程会整体偏移，确定吗？" % week1):
                raise IcsError("已取消。")
        try:
            alarm_min = int(self.var_alarm_min.get().strip() or "0")
        except ValueError:
            raise IcsError("「提前几分钟」请填整数。")

        nh, nm = 22, 0
        if self.var_night_on.get():
            m = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*",
                             self.var_night_time.get())
            if not m:
                raise IcsError("「每晚提醒时间」请按 22:00 的格式填写。")
            nh, nm = int(m.group(1)), int(m.group(2))
            if not (0 <= nh <= 23 and 0 <= nm <= 59):
                raise IcsError("「每晚提醒时间」不是合法时间。")

        md = self.result.meta if self.result else None
        try:
            hol_list, makeup_list = self._holidays_for_config()
        except hol_mod.HolidayFileError as exc:
            raise IcsError(str(exc))
        return Config(
            week1_monday=week1,
            period_times=dict(self.period_times),
            class_alarm_min=(alarm_min
                             if (self.var_alarm_on.get() and alarm_min > 0) else None),
            night_summary=bool(self.var_night_on.get()),
            night_hour=nh, night_minute=nm,
            include_remark_courses=bool(self.var_remark.get()),
            merge_mode=self.var_merge.get(),
            calendar_name=(md.calendar_name() if md else "课表"),
            holidays=hol_list,
            makeup_days=makeup_list,
            holiday_notice=bool(self.var_holiday_notice.get()),
        )

    def do_generate(self):
        if not self.records:
            self._warn("提示", "还没有课程数据，请先点「解析课表」。")
            return False
        out = self.var_out.get().strip()
        if not out:
            self._warn("提示", "请先指定输出 .ics 路径。")
            return False
        try:
            cfg = self._build_config()
        except IcsError as exc:
            self.log("参数有误：%s" % exc)
            self._set_status("参数有误", "err")
            self._warn("参数有误", str(exc))
            return False

        fake = ParseResult(records=self.records,
                           meta=self.result.meta if self.result else ParseResult().meta)

        findings = doctor.diagnose(cfg, fake)
        self.log("")
        self.log("配置自检：")
        for f in findings:
            self.log("  " + str(f))
        errs = [f for f in findings if f.level == "error"]
        if errs and not self.headless:
            msg = "配置自检发现 %d 个问题：\n\n%s\n\n仍然继续生成吗？" % (
                len(errs), "\n".join("· " + f.title for f in errs))
            if not self._confirm("配置可能不对", msg):
                self.log("已按你的选择取消生成。")
                self._set_status("已取消", "warn")
                return False

        try:
            text, stats = build_ics(fake, cfg)
        except IcsError as exc:
            self.log("生成失败：%s" % exc)
            self._set_status("生成失败", "err")
            self._error("生成失败", str(exc))
            return False
        except Exception as exc:                       # noqa: BLE001
            self.log(traceback.format_exc())
            self._set_status("生成失败", "err")
            self._error("生成失败", str(exc))
            return False

        try:
            with io.open(out, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as exc:
            self.log("写文件失败：%s" % exc)
            self._set_status("写文件失败", "err")
            self._error("写文件失败", str(exc))
            return False

        chk = sanity_check_ics(text)
        self.log("")
        self.log("日历名称：%s" % cfg.calendar_name)
        self.log("第 1 周周一：%s（%s）"
                 % (cfg.week1_monday, WEEKDAY_SHORT[cfg.week1_monday.weekday()]))
        self.log("课程日程 %d 个，覆盖 %d 次课%s"
                 % (stats["class_events"], stats["instances"],
                    "，每节课提前 %d 分钟提醒" % cfg.class_alarm_min
                    if cfg.class_alarm_min else "，未开启课前提醒"))
        if cfg.night_summary:
            self.log("夜间汇总 %d 个（每晚 %02d:%02d 提醒第二天）"
                     % (stats["night_events"], cfg.night_hour, cfg.night_minute))
        if stats["remark_events"]:
            self.log("备注课程全天日程 %d 个" % stats["remark_events"])
        self.log("结构自检：CRLF=%s 超长行=%d VEVENT=%d/%d 配对=%s"
                 % (chk["crlf_only"], chk["over_75"], chk["vevent"],
                    chk["vevent_end"], chk["balanced"]))
        self.log("已生成：%s（%.1f KB）" % (out, stats["bytes"] / 1024.0))
        self._reveal_log()

        total = stats["class_events"] + stats["night_events"] + stats["remark_events"]
        self._set_status("已生成 %d 个日程 · %s" % (total, os.path.basename(out)), "ok")
        self._info("完成", "已生成：\n%s\n\n共 %d 个日程。" % (out, total))
        return True


# ---------------------------------------------------------------------- 对话框基类
class _BaseDialog(tk.Toplevel):
    """统一外观：深色标题条 + 白色内容区 + 右下角按钮。"""

    def __init__(self, master, title, subtitle=""):
        super().__init__(master)
        self.configure(bg=P["card"])
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.saved = False

        head = tk.Frame(self, bg=P["navy"])
        head.pack(fill="x")
        tk.Label(head, text=title, bg=P["navy"], fg="#FFFFFF",
                 font=ui.FONTS["h"]).pack(anchor="w", padx=ui.SPACE["lg"],
                                          pady=(ui.SPACE["md"], 0))
        tk.Label(head, text=subtitle or " ", bg=P["navy"], fg="#B9C0D4",
                 font=ui.FONTS["small"]).pack(anchor="w", padx=ui.SPACE["lg"],
                                              pady=(0, ui.SPACE["md"]))
        tk.Frame(self, bg=P["accent"], height=2).pack(fill="x")

        self.body = tk.Frame(self, bg=P["card"])
        self.body.pack(fill="both", expand=True, padx=ui.SPACE["lg"],
                       pady=ui.SPACE["lg"])

        self.buttons = tk.Frame(self, bg=P["card"])
        self.buttons.pack(fill="x", padx=ui.SPACE["lg"], pady=(0, ui.SPACE["lg"]))
        ui.RoundedButton(self.buttons, text="保存", kind="primary",
                         command=self._save_hook).pack(side="right")
        ui.RoundedButton(self.buttons, text="取消", kind="secondary",
                         command=self.destroy).pack(side="right",
                                                    padx=(0, ui.SPACE["sm"]))
        self.bind("<Escape>", lambda e: self.destroy())

    def _save_hook(self):
        raise NotImplementedError

    def center_on(self, master):
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


# ---------------------------------------------------------------------- 课程编辑
class _RecordDialog(_BaseDialog):
    FIELDS = [("day", "星期", "combo"), ("course", "课程", "entry"),
              ("code", "课程代码", "entry"), ("period", "节次（如 1-2）", "entry"),
              ("weeks", "周次（如 1~8 或 2,4,6）", "entry"),
              ("room", "教室", "entry"), ("teacher", "教师", "entry")]

    def __init__(self, master, rec: Record, period_times):
        super().__init__(master, "修改课程", "节次时间取决于「作息时间」设置")
        self._rec = rec

        vals = {
            "day": rec.day_name, "course": rec.course, "code": rec.code,
            "period": "%d-%d" % (rec.pfrom, rec.pto),
            "weeks": weeks_to_expr(rec.all_weeks()),
            "room": rec.room,
            "teacher": "；".join(rec.teacher_desc()),
        }
        self.vars = {}
        self.body.columnconfigure(1, weight=1)
        for i, (key, label, kind) in enumerate(self.FIELDS):
            tk.Label(self.body, text=label, bg=P["card"], fg=P["text2"],
                     font=ui.FONTS["body"]).grid(row=i, column=0, sticky="w",
                                                 pady=ui.px(5))
            v = tk.StringVar(value=vals[key])
            if kind == "combo":
                w = ui.RoundedInput(self.body, textvariable=v, kind="combo",
                                    values=WEEKDAY_SHORT, width=ui.px(320),
                                    height=ui.px(32))
            else:
                w = ui.RoundedInput(self.body, textvariable=v,
                                    width=ui.px(320), height=ui.px(32))
            w.grid(row=i, column=1, sticky="ew", padx=(ui.SPACE["md"], 0),
                   pady=ui.px(5))
            self.vars[key] = v

        row = len(self.FIELDS)
        tk.Label(self.body,
                 text="教师留空则不显示；周次写成 1~8、2,4,6 或 2~4(双) 都可以。",
                 bg=P["card"], fg=P["text3"], font=ui.FONTS["small"]).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(ui.SPACE["sm"], 0))
        self.bind("<Return>", lambda e: self._save_hook())
        self.center_on(master)

    def _save_hook(self):
        try:
            day = WEEKDAY_SHORT.index(self.vars["day"].get())
            m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", self.vars["period"].get())
            if not m:
                raise ValueError("节次要写成 1-2 这样")
            pfrom, pto = int(m.group(1)), int(m.group(2))
            if pfrom > pto:
                pfrom, pto = pto, pfrom
            weeks = parse_weeks_expr(self.vars["weeks"].get())
            if not weeks:
                raise ValueError("周次不能为空，例如 1~8")
            course = self.vars["course"].get().strip()
            if not course:
                raise ValueError("课程名不能为空")
        except ValueError as exc:
            messagebox.showwarning("格式有误", str(exc), parent=self)
            return

        teachers = [t.strip()
                    for t in re.split(r"[；;、,，/]", self.vars["teacher"].get())
                    if t.strip()]
        self._new = Record(day=day, course=course,
                           code=self.vars["code"].get().strip(),
                           pfrom=pfrom, pto=pto,
                           room=self.vars["room"].get().strip(),
                           segments=[Segment(weeks=weeks,
                                             teacher=(teachers[0] if teachers else ""))],
                           notes=list(self._rec.notes))
        self.saved = True
        self.destroy()

    def result_record(self) -> Record:
        return self._new


# ---------------------------------------------------------------------- 节次时间
class _PeriodDialog(_BaseDialog):
    def __init__(self, master, times):
        super().__init__(master, "编辑节次上下课时间", "格式 HH:MM；留空表示不设置")
        self.vars = {}
        g = self.body

        for half, col0 in ((0, 1), (1, 5)):
            tk.Label(g, text="开始", bg=P["card"], fg=P["text3"],
                     font=ui.FONTS["small"]).grid(row=0, column=col0, pady=(0, ui.px(4)))
            tk.Label(g, text="结束", bg=P["card"], fg=P["text3"],
                     font=ui.FONTS["small"]).grid(row=0, column=col0 + 2,
                                                  pady=(0, ui.px(4)))

        # 左列放第 1-7 节，右列放第 8-13 节（列号不重叠）
        for p in range(1, 14):
            left = p <= 7
            r = (p - 1) % 7 + 1
            c = 0 if left else 4
            a, b = times.get(p, ("", ""))
            va, vb = tk.StringVar(value=a), tk.StringVar(value=b)
            tk.Label(g, text="第 %d 节" % p, bg=P["card"], fg=P["text2"],
                     font=ui.FONTS["body"]).grid(row=r, column=c, sticky="e",
                                                 padx=(0 if left else ui.SPACE["lg"], 6),
                                                 pady=ui.px(2))
            ui.RoundedInput(g, textvariable=va, width=ui.px(84),
                            height=ui.px(28)).grid(row=r, column=c + 1,
                                                   pady=ui.px(2))
            tk.Label(g, text="–", bg=P["card"], fg=P["text3"]).grid(row=r, column=c + 2,
                                                                    padx=ui.px(3))
            ui.RoundedInput(g, textvariable=vb, width=ui.px(84),
                            height=ui.px(28)).grid(row=r, column=c + 3,
                                                   pady=ui.px(2))
            self.vars[p] = (va, vb)

        self.center_on(master)

    def _save_hook(self):
        out = {}
        pat = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$")
        for p, (va, vb) in self.vars.items():
            a, b = va.get().strip(), vb.get().strip()
            if not a and not b:
                continue
            ma, mb = pat.match(a), pat.match(b)
            if not (ma and mb):
                messagebox.showwarning("格式有误",
                                       "第 %d 节的时间请按 08:30 的格式填写。" % p,
                                       parent=self)
                return
            out[p] = ("%02d:%02d" % (int(ma.group(1)), int(ma.group(2))),
                      "%02d:%02d" % (int(mb.group(1)), int(mb.group(2))))
        if not out:
            messagebox.showwarning("提示", "至少设置一个节次的时间。", parent=self)
            return
        self._out = out
        self.saved = True
        self.destroy()

    def result_times(self):
        return self._out


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
