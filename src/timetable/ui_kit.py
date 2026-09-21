# -*- coding: utf-8 -*-
"""界面组件库：圆角控件 + 统一配色 / 字体 / DPI 适配。

三个关键点：

1. **必须声明 DPI 感知**（创建 Tk 之前调用 enable_dpi_awareness）。
   否则在 200% 缩放的屏幕上，程序会以 96 DPI 渲染后被 Windows 位图放大 2 倍，
   字体和边框全是锯齿。声明后 Tk 按真实 DPI 原生渲染，文字是清晰的。

2. **圆角用 Pillow 渲染**。tkinter 的 Canvas 没有抗锯齿，直接画圆弧一定是锯齿边。
   这里用 Pillow 在 4 倍尺寸上画圆角矩形、再用 LANCZOS 降采样，得到真正平滑的边缘；
   文字仍交给 Tk 绘制（走系统字体引擎，最清晰）。Pillow 缺失时自动退回直角，不会崩。

3. **尺寸全部走 px()**，按 DPI 比例缩放；窗口尺寸再用 fit_size() 限制在屏幕可用范围内，
   这样从 1080p 到 4K、从 100% 到 200% 缩放都能正常显示。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:                                     # 圆角依赖 Pillow；没有就退回直角
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:                        # noqa: BLE001
    HAS_PIL = False

# --------------------------------------------------------------------- 设计变量
PALETTE = {
    "navy":      "#101828",
    "navy_soft": "#344054",
    "accent":    "#4F46E5",
    "accent_hi": "#4338CA",
    "accent_lo": "#3730A3",
    "accent_bg": "#EEF2FF",
    "bg":        "#F5F6FA",
    "card":      "#FFFFFF",
    "border":    "#E4E7EC",
    "border_2":  "#D0D5DD",
    "text":      "#101828",
    "text2":     "#667085",
    "text3":     "#98A2B3",
    "ok":        "#12B76A",
    "ok_bg":     "#ECFDF3",
    "ok_fg":     "#027A48",
    "warn":      "#F79009",
    "warn_bg":   "#FFFAEB",
    "warn_fg":   "#B54708",
    "err":       "#F04438",
    "err_bg":    "#FEF3F2",
    "err_fg":    "#B42318",
    "sel":       "#EEF2FF",
    "row_alt":   "#FAFBFC",
    "ghost_hi":  "#EAECF0",
    "on_navy":   "#FFFFFF",
    "on_navy_2": "#B9C0D4",
    "track_off": "#D0D5DD",
    "knob":      "#FFFFFF",
}

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
BASE_SPACE = dict(SPACE)

SCALE = 1.0


def px(n):
    return int(round(n * SCALE))


def set_scale(dpi):
    global SCALE
    SCALE = max(1.0, dpi / 96.0)
    for k, v in BASE_SPACE.items():
        SPACE[k] = px(v)


def _c(key):
    """颜色解析：以 # 开头是字面色值，否则是 PALETTE 的键。"""
    return key if isinstance(key, str) and key.startswith("#") else PALETTE[key]


FAMILY_CANDIDATES = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"]
MONO_CANDIDATES = ["Cascadia Mono", "Consolas", "Courier New"]
FONTS: dict = {}


def enable_dpi_awareness():
    """必须在创建 Tk 根窗口之前调用。

    用 SetProcessDpiAwareness(1)（系统级）。不要用 (2)（逐显示器）：
    实测在本机上 (2) 会让 Tk 仍然只看到 96 DPI，等于没生效。
    """
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def fit_size(root, w, h, margin=40):
    """把窗口尺寸限制在屏幕可用范围内（入参与返回值都是物理像素）。

    换到小屏笔记本或高缩放屏时，窗口不会大得超出屏幕。
    """
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(w, max(px(720), sw - px(margin)))
        h = min(h, max(px(480), sh - px(margin)))
    except Exception:
        pass
    return int(w), int(h)


def _pick_family(cands, root):
    have = set(tkfont.families(root))
    for c in cands:
        if c in have:
            return c
    return cands[-1]


def init_theme(root: tk.Misc):
    try:
        set_scale(root.winfo_fpixels("1i"))
    except Exception:
        pass

    fam = _pick_family(FAMILY_CANDIDATES, root)
    mono = _pick_family(MONO_CANDIDATES, root)

    FONTS.clear()
    FONTS.update(
        title=tkfont.Font(root=root, family=fam, size=15, weight="bold"),
        subtitle=tkfont.Font(root=root, family=fam, size=9),
        h=tkfont.Font(root=root, family=fam, size=10, weight="bold"),
        body=tkfont.Font(root=root, family=fam, size=9),
        body_b=tkfont.Font(root=root, family=fam, size=9, weight="bold"),
        small=tkfont.Font(root=root, family=fam, size=8),
        btn=tkfont.Font(root=root, family=fam, size=9, weight="bold"),
        btn_big=tkfont.Font(root=root, family=fam, size=10, weight="bold"),
        mono=tkfont.Font(root=root, family=mono, size=9),
    )

    p = PALETTE
    root.configure(bg=p["bg"])
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass

    # 输入框：外层圆角由 RoundedInput 用图片画，控件本身要“无边框”
    st.configure("Flat.TEntry", fieldbackground=p["card"], background=p["card"],
                 foreground=p["text"], bordercolor=p["card"], lightcolor=p["card"],
                 darkcolor=p["card"], insertcolor=p["accent"], relief="flat",
                 borderwidth=0, padding=(0, 0))
    st.configure("Flat.TCombobox", fieldbackground=p["card"], background=p["card"],
                 foreground=p["text"], arrowcolor=p["text2"], bordercolor=p["card"],
                 lightcolor=p["card"], darkcolor=p["card"], relief="flat",
                 borderwidth=0, padding=(0, 0))
    st.map("Flat.TCombobox", fieldbackground=[("readonly", p["card"])])
    root.option_add("*TCombobox*Listbox.background", p["card"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["accent_lo"])
    root.option_add("*TCombobox*Listbox.font", FONTS["body"])

    st.configure("Modern.Treeview", background=p["card"], fieldbackground=p["card"],
                 foreground=p["text"], rowheight=px(28), borderwidth=0,
                 font=FONTS["body"], relief="flat")
    st.configure("Modern.Treeview.Heading", background=p["bg"], foreground=p["text2"],
                 font=FONTS["small"], relief="flat", padding=(px(8), px(6)),
                 borderwidth=0)
    st.map("Modern.Treeview.Heading", background=[("active", p["ghost_hi"])])
    st.map("Modern.Treeview", background=[("selected", p["sel"])],
           foreground=[("selected", p["accent_lo"])])
    st.layout("Modern.Treeview", [("Modern.Treeview.treearea", {"sticky": "nswe"})])

    st.configure("Modern.Vertical.TScrollbar", background=p["border_2"],
                 troughcolor=p["card"], bordercolor=p["card"], arrowcolor=p["text3"],
                 lightcolor=p["border_2"], darkcolor=p["border_2"], relief="flat",
                 arrowsize=px(12), width=px(10))
    st.map("Modern.Vertical.TScrollbar", background=[("active", p["text3"])])
    # 横向滚动条（预览表列宽塞不下时出现）
    st.configure("Modern.Horizontal.TScrollbar", background=p["border_2"],
                 troughcolor=p["card"], bordercolor=p["card"], arrowcolor=p["text3"],
                 lightcolor=p["border_2"], darkcolor=p["border_2"], relief="flat",
                 arrowsize=px(12), width=px(10))
    st.map("Modern.Horizontal.TScrollbar", background=[("active", p["text3"])])
    return p


# --------------------------------------------------------------------- 圆角绘制
_IMG_CACHE: dict = {}


def _rgb(c):
    """'#RRGGBB' -> (r, g, b)。"""
    s = _c(c).lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rounded_image(w, h, r, fill, outline=None, outline_w=1, ss=4, master=None):
    """用 Pillow 在 4 倍尺寸上画圆角矩形再降采样，得到抗锯齿的平滑边缘。

    **关键：颜色和覆盖度必须分开处理。**

    常见写法是用 `Image.new("RGBA", ..., (0,0,0,0))` 建画布再缩放 —— 但透明区域的
    底色是黑色，LANCZOS 插值会把这块黑渗进边缘，圆角处就出现明显的暗角。
    正确做法是把「形状覆盖度」放在 L 通道当蒙版、「颜色」放在不透明 RGB 图上，
    两者各自缩放后再合成。这样 RGB 里根本不存在黑色可渗，边缘只会是
    「颜色 ↔ 完全透明」的干净过渡。

    返回 PhotoImage；没有 Pillow 或尺寸非法时返回 None（调用方退回直角绘制）。
    相同 (解释器/尺寸/圆角/颜色) 只渲染一次，之后走缓存。
    PhotoImage 绑定在具体 Tk 解释器上，所以缓存键里要带上解释器标识。
    """
    if not HAS_PIL:
        return None
    w, h, r = int(w), int(h), int(r)
    if w < 2 or h < 2:
        return None
    r = max(0, min(r, w // 2, h // 2))
    interp = id(master.tk) if master is not None else 0
    key = (interp, w, h, r, str(fill), str(outline), outline_w)
    hit = _IMG_CACHE.get(key)
    if hit is not None:
        return hit

    W, H = w * ss, h * ss
    box = [0, 0, W - 1, H - 1]
    rad = r * ss
    stroke = max(1, outline_w * ss) if outline else 0

    # 1) 蒙版：形状覆盖度（0..255）
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=rad, fill=255)

    # 2) 颜色：整幅先铺「描边色」（没有描边就用填充色），再把本体画上去。
    #    形状之外那圈虽然最终 alpha 为 0，但它的 RGB 会参与边缘插值，
    #    所以要和紧邻的描边色一致，边缘才不会偏色。
    rgb = Image.new("RGB", (W, H), _rgb(outline or fill))
    ImageDraw.Draw(rgb).rounded_rectangle(
        box, radius=rad, fill=_rgb(fill),
        outline=_rgb(outline) if outline else None, width=stroke)

    mask = mask.resize((w, h), Image.LANCZOS)
    rgb = rgb.resize((w, h), Image.LANCZOS)
    out = rgb.convert("RGBA")
    out.putalpha(mask)

    ph = ImageTk.PhotoImage(out, master=master)
    _IMG_CACHE[key] = ph
    return ph


def _draw_round(cv, x, y, w, h, r, fill, outline=None, outline_w=1):
    """在 Canvas 的 (x, y) 处画圆角矩形。

    **用九宫格切片，不按控件尺寸生成整图。**

    早先的写法是按控件实际大小渲染一张圆角图，于是每次缩放窗口都会为每个新尺寸
    生成一张新的 PhotoImage 并永久留在缓存里（大窗口下每张好几 MB）—— 拖动缩放会
    疯狂吃内存、越来越卡。现在只缓存 4 个固定大小的角图（半径 r 见方），中间和四条
    边用纯色矩形 + 1px 直线补：这些地方本来就是纯色直角，不需要抗锯齿。
    """
    w, h = int(w), int(h)
    if w < 2 or h < 2:
        return
    r = max(0, min(int(r), w // 2, h // 2))

    # 中间十字：两块纯色矩形正好覆盖「除四角以外」的全部区域
    cv.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline=fill)
    cv.create_rectangle(x, y + r, x + w, y + h - r, fill=fill, outline=fill)

    if outline:
        # 四条直边用 1px 直线：轴对齐，天然清晰，不需要抗锯齿
        cv.create_line(x + r, y, x + w - r - 1, y, fill=outline)
        cv.create_line(x + r, y + h - 1, x + w - r - 1, y + h - 1, fill=outline)
        cv.create_line(x, y + r, x, y + h - r - 1, fill=outline)
        cv.create_line(x + w - 1, y + r, x + w - 1, y + h - r - 1, fill=outline)

    if r <= 0 or not HAS_PIL:
        return

    tiles = corner_tiles(r, fill, outline, cv)
    if not tiles:
        return
    cv.create_image(x, y, image=tiles["tl"], anchor="nw")
    cv.create_image(x + w - r, y, image=tiles["tr"], anchor="nw")
    cv.create_image(x, y + h - r, image=tiles["bl"], anchor="nw")
    cv.create_image(x + w - r, y + h - r, image=tiles["br"], anchor="nw")


_CORNER_CACHE: dict = {}


def corner_tiles(r, fill, outline, master):
    """生成 4 个 r×r 的圆角角图（抗锯齿）。缓存键只跟半径和颜色有关，尺寸固定。"""
    if not HAS_PIL or r < 1:
        return None
    key = (id(master.tk) if master is not None else 0, int(r), str(fill), str(outline))
    hit = _CORNER_CACHE.get(key)
    if hit is not None:
        return hit

    ss = 4
    n = int(r) * 2 * ss                       # 画一个 2r 见方的「圆角正方形」，
    box = [0, 0, n - 1, n - 1]                # 它的四个角正好是我们要的四个角
    rad = int(r) * ss

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=rad, fill=255)
    rgb = Image.new("RGB", (n, n), _rgb(outline or fill))
    ImageDraw.Draw(rgb).rounded_rectangle(
        box, radius=rad, fill=_rgb(fill),
        outline=_rgb(outline) if outline else None,
        width=max(1, ss) if outline else 0)

    mask = mask.resize((int(r) * 2, int(r) * 2), Image.LANCZOS)
    rgb = rgb.resize((int(r) * 2, int(r) * 2), Image.LANCZOS)
    full = rgb.convert("RGBA")
    full.putalpha(mask)

    r = int(r)
    tiles = {
        "tl": full.crop((0, 0, r, r)),
        "tr": full.crop((r, 0, 2 * r, r)),
        "bl": full.crop((0, r, r, 2 * r)),
        "br": full.crop((r, r, 2 * r, 2 * r)),
    }
    photos = {k: ImageTk.PhotoImage(v, master=master) for k, v in tiles.items()}
    _CORNER_CACHE[key] = photos
    return photos


# --------------------------------------------------------------------- 圆角按钮
def _btn_colors(kind, state, bg):
    p = PALETTE
    if kind == "primary":
        fill = {"normal": p["accent"], "hover": p["accent_hi"],
                "active": p["accent_lo"], "disabled": p["border_2"]}[state]
        return fill, "#FFFFFF", None
    if kind == "danger":
        fill = {"normal": p["err"], "hover": "#D92D20",
                "active": "#B42318", "disabled": p["border_2"]}[state]
        return fill, "#FFFFFF", None
    if kind == "secondary":
        fill = {"normal": p["card"], "hover": p["ghost_hi"],
                "active": p["border"], "disabled": p["bg"]}[state]
        edge = p["border_2"] if state != "hover" else p["text3"]
        fg = p["text"] if state != "disabled" else p["text3"]
        return fill, fg, edge
    fill = {"normal": bg, "hover": p["ghost_hi"],
            "active": p["border"], "disabled": bg}[state]
    fg = p["text2"] if state != "disabled" else p["text3"]
    return fill, fg, None


class RoundedButton(tk.Canvas):
    """圆角按钮。kind: primary / secondary / ghost / danger。

    height / min_width 传物理像素，内部换算成控件尺寸。
    """

    def __init__(self, parent, text="", command=None, kind="primary",
                 width=None, height=None, font=None, bg=None,
                 min_width=None, padx=None, radius=None, **kw):
        self._bg = bg or _bg_of(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bg,
                         cursor="hand2", **kw)
        self._text = text
        self._command = command
        self._kind = kind
        self._font = font or FONTS["btn"]
        self._enabled = True
        self._state = "normal"
        self._radius = px(8) if radius is None else radius

        pad = px(16) if padx is None else padx
        tw = self._font.measure(text)
        w = width or max(int(min_width or 0), tw + pad * 2)
        h = height or px(34)
        self._w_px, self._h_px = int(w), int(h)
        super().configure(width=self._w_px, height=self._h_px)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self._w_px, self._h_px
        fill, fg, edge = _btn_colors(self._kind, self._state, self._bg)
        _draw_round(self, 0, 0, w, h, self._radius, fill, edge)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _on_enter(self, _e=None):
        if self._enabled and self._state == "normal":
            self._state = "hover"
            self._draw()

    def _on_leave(self, _e=None):
        if self._enabled and self._state in ("hover", "active"):
            self._state = "normal"
            self._draw()

    def _on_press(self, _e=None):
        if self._enabled:
            self._state = "active"
            self._draw()

    def _on_release(self, _e=None):
        if not self._enabled:
            return
        self._state = "hover"
        self._draw()
        if self._command:
            self._command()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self._state = "normal" if on else "disabled"
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def set_kind(self, kind):
        self._kind = kind
        self._draw()


# --------------------------------------------------------------------- 圆角开关
class Switch(tk.Canvas):
    """圆角胶囊开关（替代复选框）。variable 传 BooleanVar。"""

    def __init__(self, parent, text="", variable=None, command=None,
                 bg=None, **kw):
        self._bg = bg or _bg_of(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bg,
                         cursor="hand2", **kw)
        self._var = variable if variable is not None else tk.BooleanVar(value=False)
        self._command = command
        self._text = text
        self._hover = False

        self._th = px(20)                       # 轨道高
        self._tw = px(38)                       # 轨道宽
        self._h = max(self._th, px(22))
        w = self._tw + ((px(10) + FONTS["body"].measure(text)) if text else 0)
        self._w_px = int(w)
        super().configure(width=self._w_px, height=self._h)

        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def _toggle(self, _e=None):
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        p = PALETTE
        on = bool(self._var.get())
        h = self._h
        y0 = (h - self._th) // 2

        track = ((p["accent_hi"] if self._hover else p["accent"]) if on
                 else (p["text3"] if self._hover else p["track_off"]))
        _draw_round(self, 0, y0, self._tw, self._th, self._th // 2, track)

        d = self._th - px(4)
        kx = (self._tw - d - px(2)) if on else px(2)
        _draw_round(self, kx, y0 + px(2), d, d, d // 2, p["knob"])

        if self._text:
            self.create_text(self._tw + px(10), h / 2, text=self._text, anchor="w",
                             fill=p["text"] if on else p["text2"],
                             font=FONTS["body"])

    def get(self):
        return bool(self._var.get())


# --------------------------------------------------------------------- 圆角分段
class Segmented(tk.Canvas):
    """圆角分段控件（替代下拉框，选项少时更直观）。"""

    def __init__(self, parent, options, variable=None, command=None,
                 bg=None, font=None, seg_min=None, height=None, **kw):
        self._bg = bg or _bg_of(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bg,
                         cursor="hand2", **kw)
        self._font = font or FONTS["body"]
        self._var = variable if variable is not None else tk.StringVar()
        self._command = command
        self._segs = list(options)
        self._hover = -1
        self._h = (height or px(30)) + px(2)
        seg_min = seg_min or px(72)

        self._widths = [max(seg_min, self._font.measure(lbl) + px(26))
                        for lbl, _ in self._segs]
        self._total = sum(self._widths) + px(4)
        super().configure(width=self._total, height=self._h)

        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self._var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _index_at(self, x):
        acc = px(2)
        for i, w in enumerate(self._widths):
            if acc <= x <= acc + w:
                return i
            acc += w
        return -1

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self._draw()

    def _motion(self, e):
        self._set_hover(self._index_at(e.x))

    def _click(self, e):
        i = self._index_at(e.x)
        if i < 0:
            return
        self._var.set(self._segs[i][1])
        if self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        p = PALETTE
        h = self._h
        _draw_round(self, 0, 0, self._total, h, px(8), p["bg"], p["border"])
        cur = self._var.get()
        acc = px(2)
        for i, (label, value) in enumerate(self._segs):
            w = self._widths[i]
            selected = (value == cur)
            if selected:
                _draw_round(self, acc, px(3), w, h - px(6), px(6), p["card"])
            fg = p["accent_lo"] if selected else (
                p["text"] if i == self._hover else p["text2"])
            self.create_text(acc + w / 2, h / 2, text=label, fill=fg,
                             font=self._font)
            acc += w


# --------------------------------------------------------------------- 圆角标签
class Pill(tk.Canvas):
    """圆角状态标签。level: info / ok / warn / err / mute"""

    COLORS = {
        "info": ("accent_bg", "accent_lo"),
        "ok":   ("ok_bg", "ok_fg"),
        "warn": ("warn_bg", "warn_fg"),
        "err":  ("err_bg", "err_fg"),
        "mute": ("navy_soft", "on_navy_2"),
    }

    def __init__(self, parent, text="", level="mute", bg=None, **kw):
        self._bg = bg or _bg_of(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bg, **kw)
        self._text = text
        self._level = level
        self._h = px(22)
        self._size_to_text()
        self._draw()

    def _size_to_text(self):
        w = FONTS["small"].measure(self._text) + px(22)
        super().configure(width=max(w, px(44)), height=self._h)

    def set(self, text, level=None):
        self._text = text
        if level:
            self._level = level
        self._size_to_text()
        self._draw()

    def _draw(self):
        self.delete("all")
        fill_key, fg_key = self.COLORS.get(self._level, self.COLORS["mute"])
        w = max(self.winfo_reqwidth(), px(44))
        _draw_round(self, 0, 0, w, self._h, self._h // 2, _c(fill_key))
        self.create_text(w / 2, self._h / 2, text=self._text,
                         fill=_c(fg_key), font=FONTS["small"])


# --------------------------------------------------------------------- 圆角输入框
class RoundedInput(tk.Canvas):
    """圆角输入框：圆角底图 + 内部一个无边框的 ttk 控件。

    kind="entry" 放 Entry；kind="combo" 放 readonly Combobox。
    聚焦时边框变主色。
    """

    def __init__(self, parent, textvariable=None, kind="entry", values=None,
                 width=None, height=None, bg=None, font=None, **kw):
        self._bg = bg or _bg_of(parent)
        f = font or FONTS["body"]
        self._h_px = int(height or px(32))
        self._w_px = int(width or px(180))
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bg,
                         width=self._w_px, height=self._h_px, **kw)
        self._focus = False

        if kind == "combo":
            self.widget = ttk.Combobox(self, textvariable=textvariable,
                                       state="readonly", values=values or [],
                                       style="Flat.TCombobox", font=f)
        else:
            self.widget = ttk.Entry(self, textvariable=textvariable,
                                    style="Flat.TEntry", font=f)

        self._win = None
        self.widget.bind("<FocusIn>", lambda e: self._set_focus(True), add="+")
        self.widget.bind("<FocusOut>", lambda e: self._set_focus(False), add="+")
        self.bind("<Button-1>", lambda e: self.widget.focus_set())
        self.bind("<Configure>", self._on_configure)
        self._draw()

    def _on_configure(self, e):
        """跟随布局拉伸：外部用 grid sticky=ew 时控件会被拉宽，圆角底图要重画。"""
        if e.width == self._w_px and e.height == self._h_px:
            return
        self._w_px, self._h_px = max(px(24), e.width), max(px(20), e.height)
        self._draw()

    def _set_focus(self, v):
        self._focus = v
        self._draw()

    def _draw(self):
        self.delete("all")
        p = PALETTE
        edge = p["accent"] if self._focus else p["border_2"]
        _draw_round(self, 0, 0, self._w_px, self._h_px, px(8), p["card"], edge)
        # 圆角底图先画，控件窗口再放上去（后建的元素在上层）
        self._win = self.create_window(
            px(10), self._h_px // 2, window=self.widget, anchor="w",
            width=max(px(20), self._w_px - px(18)),
            height=max(px(16), self._h_px - px(10)))

    def get(self):
        return self.widget.get()

    def set(self, v):
        if isinstance(self.widget, ttk.Combobox):
            self.widget.set(v)
        else:
            self.widget.delete(0, "end")
            self.widget.insert(0, v)


# --------------------------------------------------------------------- 圆角卡片
class Card(tk.Frame):
    """圆角卡片。

    结构：圆角底图（铺满） + 内容框（四周内缩一个圆角半径，这样方形白底不会
    盖住圆角）。内容加到 .body 里，可自由用 grid 或 pack。
    """

    def __init__(self, parent, title=None, subtitle=None, pad=None,
                 bg=None, radius=None, **kw):
        pad = SPACE["lg"] if pad is None else pad
        self._radius = px(12) if radius is None else radius
        self._page_bg = bg or _bg_of(parent)
        super().__init__(parent, bg=self._page_bg, bd=0, highlightthickness=0, **kw)

        # place 可以和 pack/grid 共存，不会冲突
        self._bg_canvas = tk.Canvas(self, bg=self._page_bg,
                                    highlightthickness=0, bd=0)
        self._bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

        wrap = tk.Frame(self, bg=PALETTE["card"])
        wrap.pack(fill="both", expand=True,
                  padx=self._radius, pady=self._radius)

        self.body = tk.Frame(wrap, bg=PALETTE["card"])
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)

        self.bind("<Configure>", self._redraw_bg)

        if title:
            head = tk.Frame(self.body, bg=PALETTE["card"])
            head.pack(fill="x", pady=(0, SPACE["md"]))
            tk.Frame(head, bg=PALETTE["accent"], width=px(3), height=px(15)).pack(
                side="left", padx=(0, SPACE["sm"]))
            tk.Label(head, text=title, bg=PALETTE["card"], fg=PALETTE["text"],
                     font=FONTS["h"]).pack(side="left")
            if subtitle:
                tk.Label(head, text=subtitle, bg=PALETTE["card"],
                         fg=PALETTE["text3"], font=FONTS["small"]).pack(
                    side="left", padx=(SPACE["sm"], 0), pady=(px(2), 0))

    def _redraw_bg(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < px(8) or h < px(8):
            return
        self._bg_canvas.delete("all")
        # 用九宫格切片重画：不再按卡片尺寸生成整图，所以缩放窗口不会堆积大图
        _draw_round(self._bg_canvas, 0, 0, w, h, self._radius,
                    PALETTE["card"], PALETTE["border"])


def hline(parent, bg=None):
    return tk.Frame(parent, bg=PALETTE["border"], height=1)


def _bg_of(widget):
    try:
        return widget.cget("bg")
    except Exception:
        return PALETTE["card"]


# --------------------------------------------------------------------- 顶部标题带
class Header(tk.Frame):
    """深色标题带（纯色，底部一条主色分隔线）。"""

    def __init__(self, parent, title="", subtitle="", right_factory=None):
        super().__init__(parent, bg=PALETTE["navy"])
        inner = tk.Frame(self, bg=PALETTE["navy"])
        inner.pack(fill="x", padx=SPACE["xl"], pady=(SPACE["lg"], SPACE["md"]))

        left = tk.Frame(inner, bg=PALETTE["navy"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, bg=PALETTE["navy"], fg=PALETTE["on_navy"],
                 font=FONTS["title"]).pack(anchor="w")
        if subtitle:
            tk.Label(left, text=subtitle, bg=PALETTE["navy"],
                     fg=PALETTE["on_navy_2"],
                     font=FONTS["subtitle"]).pack(anchor="w", pady=(px(3), 0))

        self.right = tk.Frame(inner, bg=PALETTE["navy"])
        self.right.pack(side="right")
        if right_factory:
            right_factory(self.right)

        tk.Frame(self, bg=PALETTE["accent"], height=px(3)).pack(fill="x")
