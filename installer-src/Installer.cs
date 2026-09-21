//     课表转日历 —— 单文件安装包（文件名、安装目录一律英文：
//     TimetableToCalendar-Setup.exe / %ProgramDir%\TimetableToCalendar\TimetableToCalendar.exe）
//
// 用 Windows 自带的 C# 编译器 (csc.exe) 编译，不依赖任何第三方安装包制作工具。
// 载荷（LZMA2 压缩的应用本体 + 官方 7-Zip 的解压内核 7za.exe + 小卸载器）
// 全部作为资源内嵌在最终这一个 exe 里。
//
// 双击运行 = 图形界面安装；也支持命令行：
//     TimetableToCalendar-Setup.exe --silent --dir="D:\Tools"     静默安装
//     TimetableToCalendar-Setup.exe --uninstall                   卸载
//     TimetableToCalendar-Setup.exe --uninstall --silent          静默卸载
//     TimetableToCalendar-Setup.exe --uitest report.txt           界面自检（写报告）
//     TimetableToCalendar-Setup.exe --pathcheck report.txt        安装位置规则自检（写报告）
//
// 安装位置会自动补一个以项目名命名的子目录：只选到 D:\Tools 这种父目录时，
// 实际装到 D:\Tools\TimetableToCalendar（已经以 TimetableToCalendar 结尾就不再套一层）。
//
// 安装时把内嵌的小卸载器写到安装目录下改名「卸载.exe」，并在
// HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall 下登记，
// 这样「设置 → 应用」里能看到并卸载。全部写 HKCU，不需要管理员权限。
//
// 界面与主程序统一：圆角 + 纯色，配色同一套；声明 DPI 感知，高分屏下原生清晰。

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

// ==================================================================== 主题与工具
static class Theme
{
    public const string AppName = "课表转日历";

    /// <summary>DPI 缩放系数：96 DPI 为 1.0，192 DPI 为 2.0。所有像素尺寸都要过 S()。</summary>
    public static double Scale = 1.0;

    public static int S(int v) { return (int)Math.Round(v * Scale); }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern bool SetProcessDPIAware();
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern IntPtr GetDC(IntPtr hWnd);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern int GetDeviceCaps(IntPtr hdc, int index);
    [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    /// <summary>必须在创建任何窗口之前调用。</summary>
    public static void Init()
    {
        try
        {
            SetProcessDPIAware();               // 不声明的话会被系统位图放大，字全糊
            IntPtr dc = GetDC(IntPtr.Zero);
            int dpi = GetDeviceCaps(dc, 88);    // LOGPIXELSX
            ReleaseDC(IntPtr.Zero, dc);
            if (dpi > 0) Scale = Math.Max(1.0, dpi / 96.0);
        }
        catch { Scale = 1.0; }
    }

    /// <summary>Windows 11 上给窗口加圆角；老系统上调用无效但不报错。</summary>
    public static void RoundWindow(IntPtr handle)
    {
        try
        {
            int pref = 2;                       // DWMWCP_ROUND
            DwmSetWindowAttribute(handle, 33, ref pref, sizeof(int));  // DWMWA_WINDOW_CORNER_PREFERENCE
        }
        catch { }
    }

    // 与主程序（tkinter）共用同一套配色
    public static readonly Color Navy     = Color.FromArgb(0x10, 0x18, 0x28);
    public static readonly Color Accent   = Color.FromArgb(0x4F, 0x46, 0xE5);
    public static readonly Color AccentHi = Color.FromArgb(0x43, 0x38, 0xCA);
    public static readonly Color AccentLo = Color.FromArgb(0x37, 0x30, 0xA3);
    public static readonly Color Bg       = Color.FromArgb(0xF5, 0xF6, 0xFA);
    public static readonly Color Card     = Color.White;
    public static readonly Color Border   = Color.FromArgb(0xE4, 0xE7, 0xEC);
    public static readonly Color Border2  = Color.FromArgb(0xD0, 0xD5, 0xDD);
    public static readonly Color Text     = Color.FromArgb(0x10, 0x18, 0x28);
    public static readonly Color Text2    = Color.FromArgb(0x66, 0x70, 0x85);
    public static readonly Color Text3    = Color.FromArgb(0x98, 0xA2, 0xB3);
    public static readonly Color OnNavy2  = Color.FromArgb(0xB9, 0xC0, 0xD4);

    public static Font F(float size, bool bold = false)
    {
        return new Font("Microsoft YaHei UI", size,
                        bold ? FontStyle.Bold : FontStyle.Regular, GraphicsUnit.Point);
    }

    /// <summary>圆角路径。GDI+ 有抗锯齿，所以这里的圆角是平滑的。</summary>
    public static GraphicsPath Round(Rectangle r, int radius)
    {
        int d = Math.Max(1, radius * 2);
        var p = new GraphicsPath();
        if (r.Width <= d || r.Height <= d) { p.AddRectangle(r); return p; }
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }
}

// ==================================================================== 圆角按钮
class ModernButton : Button
{
    public Color Fill, FillHover, FillPressed, Edge = Color.Empty;
    public Color Fg = Color.White;
    bool _hover, _down;

    public ModernButton(string text, Color fill, Color hover, Color pressed)
    {
        Text = text; Fill = fill; FillHover = hover; FillPressed = pressed;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        Font = Theme.F(9f, true);
        Cursor = Cursors.Hand;
        Height = Theme.S(38);
        BackColor = Theme.Bg;
    }

    protected override void OnMouseEnter(EventArgs e) { _hover = true;  Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { _hover = false; _down = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { _down = true;  Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e)   { _down = false; Invalidate(); base.OnMouseUp(e); }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        // 必须先铺满背景：本控件是 AllPaintingInWmPaint | UserPaint，OnPaint 要负责
        // 画全部内容。不铺的话圆角之外那块是未初始化的缓冲（黑），GDI+ 抗锯齿会把黑
        // 混进圆角边缘 —— 视觉上就是暗角。
        g.Clear(BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        Color fill = !Enabled ? Theme.Border2 : _down ? FillPressed : _hover ? FillHover : Fill;
        var rect = new Rectangle(0, 0, Width - 1, Height - 1);
        using (var path = Theme.Round(rect, Theme.S(9)))
        {
            using (var b = new SolidBrush(fill)) g.FillPath(b, path);
            if (Edge != Color.Empty)
                using (var p = new Pen(Edge)) g.DrawPath(p, path);
        }
        Color fg = Enabled ? Fg : Theme.Text3;
        TextRenderer.DrawText(g, Text, Font, rect, fg,
            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPrefix);
    }
}

// ==================================================================== 圆角复选框
class ModernCheck : CheckBox
{
    public ModernCheck(string text, bool isChecked)
    {
        Text = text; Checked = isChecked;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Font = Theme.F(9f);
        ForeColor = Theme.Text;
        Cursor = Cursors.Hand;
        Height = Theme.S(24);
        BackColor = Theme.Card;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(BackColor);

        int s = Theme.S(16), y = (Height - s) / 2;
        var box = new Rectangle(0, y, s, s);
        using (var path = Theme.Round(box, Theme.S(4)))
        {
            if (Checked)
                using (var b = new SolidBrush(Theme.Accent)) g.FillPath(b, path);
            else
            {
                using (var b = new SolidBrush(Color.White)) g.FillPath(b, path);
                using (var p = new Pen(Theme.Border2)) g.DrawPath(p, path);
            }
        }
        if (Checked)
        {
            using (var pen = new Pen(Color.White, Math.Max(1.6f, Theme.S(2) * 0.8f)))
            {
                pen.StartCap = LineCap.Round; pen.EndCap = LineCap.Round;
                g.DrawLines(pen, new[]
                {
                    new Point(box.X + Theme.S(4),  box.Y + Theme.S(8)),
                    new Point(box.X + Theme.S(7),  box.Y + Theme.S(11)),
                    new Point(box.X + Theme.S(12), box.Y + Theme.S(5)),
                });
            }
        }
        TextRenderer.DrawText(g, Text, Font,
            new Rectangle(s + Theme.S(8), 0, Width - s - Theme.S(8), Height),
            Enabled ? ForeColor : Theme.Text3,
            TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
    }
}

// ==================================================================== 圆角输入框
class RoundedField : Panel
{
    public TextBox Box;

    public RoundedField(string text, int w, int h)
    {
        Size = new Size(w, h);
        BackColor = Theme.Card;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Box = new TextBox
        {
            Text = text, BorderStyle = BorderStyle.None, BackColor = Theme.Card,
            ForeColor = Theme.Text, Font = Theme.F(9.5f),
        };
        Controls.Add(Box);
        Box.Location = new Point(Theme.S(10), (h - Box.PreferredHeight) / 2);
        Box.Width = w - Theme.S(20);
        Box.GotFocus += (s, e) => Invalidate();
        Box.LostFocus += (s, e) => Invalidate();
    }

    public string Value { get { return Box.Text; } set { Box.Text = value; } }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.Clear(BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        Color edge = Box.Focused ? Theme.Accent : Theme.Border2;
        using (var path = Theme.Round(new Rectangle(0, 0, Width - 1, Height - 1), Theme.S(8)))
        using (var p = new Pen(edge))
        {
            g.FillPath(Brushes.White, path);
            g.DrawPath(p, path);
        }
    }
}

// ==================================================================== 顶部标题带
class HeaderPanel : Panel
{
    public HeaderPanel()
    {
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        BackColor = Theme.Navy;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        var r = ClientRectangle;
        using (var b = new SolidBrush(Theme.Navy)) g.FillRectangle(b, r);

        g.SmoothingMode = SmoothingMode.AntiAlias;
        using (var accent = new SolidBrush(Theme.Accent))
            g.FillRectangle(accent, Theme.S(22), Theme.S(24), Theme.S(4), Theme.S(30));

        TextRenderer.DrawText(g, "课表转日历", Theme.F(17f, true),
            new Rectangle(Theme.S(36), Theme.S(22), r.Width - Theme.S(56), Theme.S(32)),
            Color.White, TextFormatFlags.Left | TextFormatFlags.VerticalCenter);
        TextRenderer.DrawText(g, "把教务系统的课表 PDF 转成可导入手机日历的 .ics",
            Theme.F(9f),
            new Rectangle(Theme.S(38), Theme.S(56), r.Width - Theme.S(66), Theme.S(24)),
            Theme.OnNavy2, TextFormatFlags.Left);

        using (var accent = new SolidBrush(Theme.Accent))
            g.FillRectangle(accent, 0, r.Height - Theme.S(3), r.Width, Theme.S(3));
    }
}

// ==================================================================== 圆角卡片
class RoundedCard : Panel
{
    public RoundedCard(int w, int h)
    {
        Size = new Size(w, h);
        BackColor = Theme.Bg;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        // 圆角之外那圈要铺成所放容器的底色，否则是未初始化的黑，抗锯齿混进来就是暗角
        g.Clear(BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        using (var path = Theme.Round(new Rectangle(0, 0, Width - 1, Height - 1), Theme.S(12)))
        {
            g.FillPath(Brushes.White, path);
            using (var p = new Pen(Theme.Border)) g.DrawPath(p, path);
        }
    }
}

// ==================================================================== 圆角进度条
class ThinProgress : Control
{
    double _value;
    public ThinProgress()
    {
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Height = Theme.S(6);
        BackColor = Theme.Bg;
    }
    public double Value
    {
        get { return _value; }
        set { _value = Math.Max(0, Math.Min(1, value)); Invalidate(); }
    }
    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        var r = new Rectangle(0, 0, Width - 1, Height - 1);
        using (var path = Theme.Round(r, Height / 2))
        using (var b = new SolidBrush(Theme.Border))
            g.FillPath(b, path);
        int w = (int)(r.Width * _value);
        if (w > 2)
        {
            using (var path = Theme.Round(new Rectangle(0, 0, w, r.Height), Height / 2))
            using (var b = new SolidBrush(Theme.Accent))
                g.FillPath(b, path);
        }
    }
}

// ==================================================================== 主程序
static class Program
{
    // 落盘的东西一律用英文名：中文路径在某些工具链、备份脚本、云同步里都容易出问题
    const string APP_EXE     = "TimetableToCalendar.exe";
    const string APP_FOLDER  = "TimetableToCalendar";   // 安装目录名（父目录下自动新建的子目录）
    const string UNINST_EXE  = "Uninstall.exe";
    const string LNK_NAME    = "TimetableToCalendar.lnk";
    const string REG_SUBKEY  = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\TimetableToCalendar";
    const string PAYLOAD_RES = "payload.7z";
    const string ZA_RES      = "sevenza.exe";
    const string UNINST_RES  = "uninstaller.exe";

    /// <summary>
    /// 默认安装位置：优先 D 盘的 D:\Tools\TimetableToCalendar（和用户把工具放 D 盘的习惯一致），
    /// 没有 D 盘就退到 %LOCALAPPDATA%\Programs\TimetableToCalendar。全是英文路径。
    /// </summary>
    internal static string DefaultDir()
    {
        try
        {
            foreach (var d in DriveInfo.GetDrives())
            {
                if (d.Name.StartsWith("D:", StringComparison.OrdinalIgnoreCase)
                    && d.IsReady)
                    return @"D:\Tools\" + APP_FOLDER;
            }
        }
        catch { }
        return Path.Combine(Environment.GetFolderPath(
                   Environment.SpecialFolder.LocalApplicationData),
               "Programs", APP_FOLDER);
    }

    /// <summary>
    /// 把用户填/选的安装位置规范成「父目录\TimetableToCalendar」。
    ///
    /// 用户往往只选到 D:\Tools 或 C:\Program Files 这样的**父目录**，直接装在那儿会把
    /// 几百个程序文件散落在父目录里，卸载时也不好收拾。所以统一在这一层套一个以项目名
    /// 命名的子目录：
    ///     D:\Tools                       -> D:\Tools\TimetableToCalendar
    ///     D:\Tools\                      -> D:\Tools\TimetableToCalendar
    ///     D:\                            -> D:\TimetableToCalendar
    ///     D:\Tools\TimetableToCalendar    -> 原样（重复安装/升级不再套一层）
    /// </summary>
    internal static string NormalizeDir(string dir)
    {
        if (string.IsNullOrEmpty(dir)) return dir;
        string d = Environment.ExpandEnvironmentVariables(dir).Trim().Trim('"').Trim();
        if (d.Length == 0) return d;
        try { d = Path.GetFullPath(d); } catch { }
        d = d.TrimEnd('\\', '/');
        if (d.Length == 0) return APP_FOLDER;          // 极端情况：填了个 "\"
        // "D:" 这种只剩盘符的写法必须补回斜杠，否则 Path.Combine 会得到
        // "D:TimetableToCalendar"（盘符相对路径，含义完全不对）
        if (d.Length == 2 && d[1] == ':') d += "\\";
        // 已经是「…\TimetableToCalendar」结尾就不再套一层，否则升级会装成两层
        if (string.Equals(Path.GetFileName(d), APP_FOLDER,
                          StringComparison.OrdinalIgnoreCase)) return d;
        return Path.Combine(d, APP_FOLDER);
    }

    [STAThread]
    static int Main(string[] args)
    {
        Theme.Init();          // 必须最先做：声明 DPI 感知并算出缩放系数

        bool silent    = HasFlag(args, "--silent");
        bool uninstall = HasFlag(args, "--uninstall");
        bool noDesktop = HasFlag(args, "--no-desktop");
        bool noStart   = HasFlag(args, "--no-startmenu");
        string dirArg  = GetArg(args, "--dir");

        try
        {
            if (uninstall) return DoUninstall(silent);
            if (HasFlag(args, "--uitest")) return UiTest(GetArg(args, "--uitest"));
            if (HasFlag(args, "--pathcheck")) return PathCheck(GetArg(args, "--pathcheck"));
            return DoInstall(silent, dirArg, !noDesktop, !noStart);
        }
        catch (Exception ex)
        {
            if (silent)
            {
                // winexe 没有控制台，stderr 看不见：再写一份日志，方便命令行/自动化排查
                Console.Error.WriteLine("失败: " + ex.Message);
                try
                {
                    File.WriteAllText(
                        Path.Combine(Path.GetTempPath(), "TimetableToCalendar-setup-error.log"),
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\r\n" + ex.Message + "\r\n",
                        System.Text.Encoding.UTF8);
                }
                catch { }
            }
            else MessageBox.Show("安装失败：\n\n" + ex.Message, Theme.AppName,
                                 MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // ------------------------------------------------------------------ 路径规则自检
    /// <summary>
    /// 检查「安装位置自动补 \TimetableToCalendar」这条规则，结果写进报告文件。
    /// 用法： TimetableToCalendar-Setup.exe --pathcheck path.txt
    /// </summary>
    static int PathCheck(string report)
    {
        string[] input =
        {
            @"D:\Tools",
            @"D:\Tools\",
            @"D:\Tools\TimetableToCalendar",
            @"D:\Tools\TimetableToCalendar\",
            @"D:\",
            @"C:\Program Files",
            @"%TEMP%\ttc-test",
        };
        string[] expect =
        {
            @"D:\Tools\TimetableToCalendar",
            @"D:\Tools\TimetableToCalendar",
            @"D:\Tools\TimetableToCalendar",
            @"D:\Tools\TimetableToCalendar",
            @"D:\TimetableToCalendar",
            @"C:\Program Files\TimetableToCalendar",
            Path.Combine(Path.GetTempPath().TrimEnd('\\'), "ttc-test", APP_FOLDER),
        };

        var lines = new List<string>();
        lines.Add("安装位置规范化自检");
        lines.Add("");
        int bad = 0;
        for (int i = 0; i < input.Length; i++)
        {
            string got = NormalizeDir(input[i]);
            string again = NormalizeDir(got);       // 必须幂等，否则升级会套娃
            bool ok = string.Equals(got, expect[i], StringComparison.OrdinalIgnoreCase)
                      && string.Equals(again, got, StringComparison.OrdinalIgnoreCase);
            if (!ok) bad++;
            lines.Add((ok ? "  [OK]   " : "  [FAIL] ") + input[i]
                      + "  ->  " + got
                      + (ok ? "" : "   （期望 " + expect[i] + "，再跑一次得到 " + again + "）"));
        }
        lines.Add("");
        lines.Add("RESULT: " + (bad == 0 ? "OK" : "FAIL " + bad + " 项"));

        try { File.WriteAllLines(report, lines.ToArray(), System.Text.Encoding.UTF8); }
        catch (Exception ex) { Console.Error.WriteLine(ex.Message); return 1; }
        return bad == 0 ? 0 : 1;
    }

    // ------------------------------------------------------------------ 界面自检
    /// <summary>
    /// 把窗口建出来但不显示在可见位置，检查控件是否越界或重叠，结果写报告文件。
    /// 界面没法截屏，用这个代替肉眼检查。
    /// </summary>
    static int UiTest(string report)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            using (var f = new SetupForm(DefaultDir(), true, true, (a, b, c, d) => { }))
            {
                f.StartPosition = FormStartPosition.Manual;
                f.Location = new Point(-4000, -4000);
                f.Show();
                Application.DoEvents();

                var sb = new System.Text.StringBuilder();
                var all = new System.Collections.Generic.List<Control>();
                Collect(f, all);

                sb.AppendLine("DPI 缩放: " + Theme.Scale.ToString("0.00"));
                sb.AppendLine("窗口客户区: " + f.ClientSize.Width + "x" + f.ClientSize.Height);
                sb.AppendLine("控件总数: " + all.Count);
                sb.AppendLine();
                int overflow = 0, overlap = 0;
                foreach (var c in all)
                {
                    var r = c.Bounds;
                    bool outOf = r.Right > f.ClientSize.Width + 1 ||
                                 r.Bottom > f.ClientSize.Height + 1 || r.X < -1 || r.Y < -1;
                    if (outOf) overflow++;
                    sb.AppendLine(string.Format("  {0,-14} {1,-20} x={2,4} y={3,4} w={4,4} h={5,3}{6}",
                        c.GetType().Name, Short(c.Text), r.X, r.Y, r.Width, r.Height,
                        outOf ? "   <== 越界" : ""));
                }
                for (int i = 0; i < all.Count; i++)
                    for (int j = i + 1; j < all.Count; j++)
                    {
                        var a = all[i]; var b = all[j];
                        if (a.Parent == b.Parent && a.Bounds.IntersectsWith(b.Bounds))
                        {
                            overlap++;
                            sb.AppendLine(string.Format("  重叠: {0}('{1}') <-> {2}('{3}')",
                                a.GetType().Name, Short(a.Text), b.GetType().Name, Short(b.Text)));
                        }
                    }
                sb.AppendLine();
                sb.AppendLine("越界控件: " + overflow);
                sb.AppendLine("重叠控件对: " + overlap);
                sb.AppendLine("RESULT: " + (overflow == 0 && overlap == 0 ? "OK" : "FAIL"));
                File.WriteAllText(report, sb.ToString(), System.Text.Encoding.UTF8);
                f.Close();
            }
            return 0;
        }
        catch (Exception ex)
        {
            File.WriteAllText(report, "RESULT: FAIL\n" + ex, System.Text.Encoding.UTF8);
            return 1;
        }
    }

    static void Collect(Control parent, System.Collections.Generic.List<Control> into)
    {
        foreach (Control c in parent.Controls) { into.Add(c); Collect(c, into); }
    }

    static string Short(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Length <= 15 ? s : s.Substring(0, 14) + "…";
    }

    // ------------------------------------------------------------------ 安装
    static int DoInstall(bool silent, string dirArg, bool desktop, bool startmenu)
    {
        string dir = string.IsNullOrEmpty(dirArg) ? DefaultDir() : dirArg;
        dir = NormalizeDir(dir);      // 只选到父目录时，自动补上 \TimetableToCalendar

        if (silent)
        {
            InstallCore(dir, desktop, startmenu, null);
            Console.WriteLine("已安装到: " + dir);
            return 0;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        using (var f = new SetupForm(dir, desktop, startmenu, InstallCore))
        {
            if (f.ShowDialog() != DialogResult.OK) return 2;
            dir = f.InstallDir;

            var res = MessageBox.Show(
                "安装完成！\n\n安装位置：" + dir + "\n\n是否立即运行？",
                Theme.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
            if (res == DialogResult.Yes)
                Process.Start(new ProcessStartInfo(Path.Combine(dir, APP_EXE))
                              { WorkingDirectory = dir });
        }
        return 0;
    }

    /// <summary>
    /// 检查目标目录里的 exe 是否正在运行；是的话返回它的完整路径，否则返回 null。
    /// 取不到路径（权限、跨会话等）时保守地按「没在运行」处理，不误报。
    /// </summary>
    static string RunningFrom(string dir)
    {
        try
        {
            string full = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            int me = Process.GetCurrentProcess().Id;
            foreach (var p in Process.GetProcesses())
            {
                try
                {
                    if (p.Id == me) continue;
                    string path = p.MainModule.FileName;
                    if (path != null && path.StartsWith(full, StringComparison.OrdinalIgnoreCase))
                        return path;
                }
                catch { }                       // 系统进程 / 已退出：跳过
                finally { p.Dispose(); }
            }
        }
        catch { }
        return null;
    }

    /// <summary>真正的安装动作：解压载荷、放卸载器、建快捷方式、登记注册表。</summary>
    static void InstallCore(string dir, bool desktop, bool startmenu, Action<string> report)
    {
        // 兜底再规范一次：不管从哪个入口进来，最终目录都必须是「父目录\TimetableToCalendar」。
        // （NormalizeDir 是幂等的，已经以 TimetableToCalendar 结尾就不会再套一层。）
        dir = NormalizeDir(dir);

        // 目标目录里的程序还在跑的话，7za 覆盖不了那个 exe，会失败得莫名其妙
        // （实测就是这么踩到的：报「解压失败，7za.exe 返回 2」）。先查清楚再动手。
        string running = RunningFrom(dir);
        if (running != null)
            throw new Exception("安装目录里的程序正在运行，请先关闭它再安装：\n" + running);

        if (report != null) report("正在创建安装目录…");
        // 记下「父目录是我们刚建出来的」：卸载时如果它空了就一并删掉，不留空文件夹
        string parent = Path.GetDirectoryName(dir.TrimEnd('\\'));
        bool madeParent = !string.IsNullOrEmpty(parent) && !Directory.Exists(parent);
        Directory.CreateDirectory(dir);

        string tmp = Path.Combine(Path.GetTempPath(),
                                  "kebiao_setup_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tmp);
        try
        {
            string zaPath  = Path.Combine(tmp, "7za.exe");
            string arcPath = Path.Combine(tmp, PAYLOAD_RES);
            DumpResource(ZA_RES, zaPath);
            DumpResource(PAYLOAD_RES, arcPath);

            if (report != null) report("正在解压应用文件（约 81 MB）…");
            var psi = new ProcessStartInfo(zaPath,
                "x \"" + arcPath + "\" -o\"" + dir + "\" -y -bso0 -bsp0")
            { UseShellExecute = false, CreateNoWindow = true };
            using (var p = Process.Start(psi))
            {
                p.WaitForExit();
                if (p.ExitCode != 0)
                    throw new Exception("解压失败，7za.exe 返回 " + p.ExitCode);
            }
        }
        finally { TryDeleteDir(tmp); }

        string appExe = Path.Combine(dir, APP_EXE);
        if (!File.Exists(appExe))
            throw new Exception("解压后没有找到 " + APP_EXE + "，安装可能不完整。");

        if (report != null) report("正在创建快捷方式…");
        // 卸载器用一个独立的小程序（约 15 KB）；安装器自己带着 23 MB 载荷，
        // 复制过去会白白多占 23 MB，所以这里从资源里取小卸载器。
        string uninst = Path.Combine(dir, UNINST_EXE);
        DumpResource(UNINST_RES, uninst);

        if (desktop)
            MakeShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), LNK_NAME), appExe, dir);
        if (startmenu)
            MakeShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), LNK_NAME), appExe, dir);

        if (report != null) report("正在登记卸载信息…");
        RegisterUninstall(dir, uninst, madeParent);
    }

    // ------------------------------------------------------------------ 卸载
    static int DoUninstall(bool silent)
    {
        string dir = Path.GetDirectoryName(Application.ExecutablePath);
        string fromReg = null;
        using (var k = Registry.CurrentUser.OpenSubKey(REG_SUBKEY))
            if (k != null) fromReg = (string)k.GetValue("InstallLocation");
        if (!string.IsNullOrEmpty(fromReg) && Directory.Exists(fromReg)) dir = fromReg;

        // 优先交给安装目录里的卸载器：它会把自己复制到 %TEMP% 再删，能删得干干净净
        // （安装器自己没法删掉「自己所在目录」之外的坑：工作目录、占用等，见 Uninstaller.cs 注释）
        string helper = Path.Combine(dir, UNINST_EXE);
        if (File.Exists(helper))
        {
            Process.Start(new ProcessStartInfo(helper,
                "--uninstall" + (silent ? " --silent" : ""))
            { UseShellExecute = false, WorkingDirectory = Path.GetTempPath() });
            return 0;
        }

        if (!silent)
        {
            var res = MessageBox.Show(
                "确定要卸载「" + Theme.AppName + "」吗？\n\n将删除：" + dir,
                Theme.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (res != DialogResult.Yes) return 2;
        }

        TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), LNK_NAME));
        TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), LNK_NAME));
        try { Registry.CurrentUser.DeleteSubKeyTree(REG_SUBKEY, false); } catch { }

        if (Directory.Exists(dir))
            Process.Start(new ProcessStartInfo("cmd.exe",
                "/c cd /d \"%SystemRoot%\" & ping -n 3 127.0.0.1 >nul & rmdir /s /q \"" + dir + "\"")
            {
                UseShellExecute = false, CreateNoWindow = true,
                WorkingDirectory = Path.GetTempPath(),   // 工作目录绝不能留在被删的目录里
            });

        if (silent) Console.WriteLine("已卸载: " + dir);
        else MessageBox.Show("已卸载。", Theme.AppName,
                             MessageBoxButtons.OK, MessageBoxIcon.Information);
        return 0;
    }

    // ------------------------------------------------------------------ 工具
    static void RegisterUninstall(string dir, string uninstExe, bool madeParent)
    {
        using (var k = Registry.CurrentUser.CreateSubKey(REG_SUBKEY))
        {
            if (k == null) return;
            k.SetValue("DisplayName",          Theme.AppName);
            k.SetValue("DisplayVersion",       "1.3");
            k.SetValue("Publisher",            "nwpu-timetable");
            k.SetValue("InstallLocation",      dir);
            k.SetValue("DisplayIcon",          Path.Combine(dir, APP_EXE));
            k.SetValue("UninstallString",      "\"" + uninstExe + "\" --uninstall");
            k.SetValue("QuietUninstallString", "\"" + uninstExe + "\" --uninstall --silent");
            k.SetValue("NoModify", 1, RegistryValueKind.DWord);
            k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            // 父目录是安装时新建的话，卸载后要顺手删掉，别留一个空文件夹
            k.SetValue("ParentCreated", madeParent ? 1 : 0, RegistryValueKind.DWord);
        }
    }

    static void DumpResource(string name, string target)
    {
        using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
        {
            if (s == null) throw new Exception("安装包内缺少资源：" + name);
            using (var fs = File.Create(target))
                s.CopyTo(fs, 1 << 20);
        }
    }

    /// <summary>反射调用 WScript.Shell 建快捷方式（不用 dynamic，避免额外编译期依赖）。</summary>
    static void MakeShortcut(string lnkPath, string targetExe, string workDir)
    {
        try
        {
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            if (t == null) return;
            object sh  = Activator.CreateInstance(t);
            object lnk = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod,
                                        null, sh, new object[] { lnkPath });
            Type lt = lnk.GetType();
            SetProp(lt, lnk, "TargetPath",       targetExe);
            SetProp(lt, lnk, "WorkingDirectory", workDir);
            SetProp(lt, lnk, "Description",      "把课表 PDF 转成可导入手机日历的 .ics");
            SetProp(lt, lnk, "IconLocation",     targetExe + ",0");
            lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
        }
        catch { }
    }

    static void SetProp(Type t, object o, string name, object value)
    {
        t.InvokeMember(name, BindingFlags.SetProperty, null, o, new object[] { value });
    }

    static void TryDelete(string path) { try { if (File.Exists(path)) File.Delete(path); } catch { } }
    static void TryDeleteDir(string path) { try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { } }

    static bool HasFlag(string[] a, string name)
    {
        foreach (var s in a)
            if (string.Equals(s, name, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    static string GetArg(string[] a, string name)
    {
        for (int i = 0; i < a.Length; i++)
        {
            if (string.Equals(a[i], name, StringComparison.OrdinalIgnoreCase) && i + 1 < a.Length)
                return a[i + 1];
            if (a[i].StartsWith(name + "=", StringComparison.OrdinalIgnoreCase))
                return a[i].Substring(name.Length + 1).Trim('"');
        }
        return null;
    }
}

// ==================================================================== 安装向导
class SetupForm : Form
{
    public string InstallDir { get; private set; }

    RoundedField _dir;
    ModernCheck _desktop, _startmenu;
    ModernButton _install, _cancel;
    ThinProgress _bar;
    Label _status;
    Action<string, bool, bool, Action<string>> _installer;
    bool _working;

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        Theme.RoundWindow(Handle);      // Win11 上给窗口加圆角
    }

    public SetupForm(string dir, bool desktop, bool startmenu,
                     Action<string, bool, bool, Action<string>> installer)
    {
        InstallDir = dir;
        _installer = installer;

        Text = Theme.AppName + " —— 安装";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false; MinimizeBox = false;
        AutoScaleMode = AutoScaleMode.None;    // 尺寸全部自己按 DPI 缩放
        Font = Theme.F(9f);
        BackColor = Theme.Bg;
        ClientSize = new Size(Theme.S(560), Theme.S(420));

        Controls.Add(new HeaderPanel { Dock = DockStyle.Top, Height = Theme.S(88) });

        // ---- 安装位置卡片（圆角） ----
        var card = new RoundedCard(Theme.S(520), Theme.S(160))
        {
            Location = new Point(Theme.S(20), Theme.S(108)),
        };
        Controls.Add(card);

        card.Controls.Add(new Label
        {
            Text = "安装位置（会自动新建 TimetableToCalendar 子目录）", AutoSize = true,
            Location = new Point(Theme.S(18), Theme.S(16)),
            Font = Theme.F(9f, true), ForeColor = Theme.Text, BackColor = Theme.Card,
        });

        _dir = new RoundedField(dir, Theme.S(374), Theme.S(34))
        { Location = new Point(Theme.S(20), Theme.S(42)) };
        card.Controls.Add(_dir);
        // 输入框失焦时把最终目录回填，用户不用点安装也能看到真正装到哪儿
        _dir.Box.LostFocus += (s, e) =>
        {
            string v = Program.NormalizeDir(_dir.Value.Trim());
            if (v != _dir.Value) _dir.Value = v;
        };

        // 选目录按钮。放在白色卡片上，所以 BackColor 要跟着卡片走
        var browse = new ModernButton("浏览…", Color.White,
                                      Color.FromArgb(0xF2, 0xF4, 0xF7), Theme.Border)
        {
            BackColor = Theme.Card, Fg = Theme.Text, Edge = Theme.Border2,
            Location = new Point(Theme.S(404), Theme.S(42)),
            Size = new Size(Theme.S(94), Theme.S(34)),
            Font = Theme.F(9f, false),
        };
        browse.Click += (s, e) =>
        {
            using (var d = new FolderBrowserDialog())
            {
                d.Description = "选择安装位置（会在这个位置下新建 TimetableToCalendar 子目录）";
                d.SelectedPath = _dir.Value;
                // 选完立刻把最终目录回填到输入框，用户看到的就是真正要装的位置
                if (d.ShowDialog() == DialogResult.OK)
                    _dir.Value = Program.NormalizeDir(d.SelectedPath);
            }
        };
        card.Controls.Add(browse);

        _desktop = new ModernCheck("创建桌面快捷方式", desktop)
        { Location = new Point(Theme.S(20), Theme.S(88)), Width = Theme.S(300) };
        card.Controls.Add(_desktop);

        _startmenu = new ModernCheck("创建开始菜单快捷方式", startmenu)
        { Location = new Point(Theme.S(20), Theme.S(118)), Width = Theme.S(300) };
        card.Controls.Add(_startmenu);

        // ---- 状态与进度 ----
        _bar = new ThinProgress
        { Location = new Point(Theme.S(22), Theme.S(288)), Width = Theme.S(516) };
        Controls.Add(_bar);

        _status = new Label
        {
            Text = "准备就绪。点右下角「立即安装」开始。",
            Location = new Point(Theme.S(22), Theme.S(302)),
            Size = new Size(Theme.S(516), Theme.S(24)),
            ForeColor = Theme.Text2, BackColor = Theme.Bg, Font = Theme.F(8.5f),
        };
        Controls.Add(_status);

        // ---- 按钮（圆角） ----
        _install = new ModernButton("立即安装", Theme.Accent, Theme.AccentHi, Theme.AccentLo)
        {
            Location = new Point(Theme.S(320), Theme.S(336)),
            Size = new Size(Theme.S(130), Theme.S(44)),
            Font = Theme.F(10f, true),
        };
        _install.Click += OnInstall;
        Controls.Add(_install);

        _cancel = new ModernButton("取消", Color.White, Color.FromArgb(0xF2, 0xF4, 0xF7),
                                   Theme.Border)
        {
            Fg = Theme.Text2, Edge = Theme.Border2,
            Location = new Point(Theme.S(458), Theme.S(336)),
            Size = new Size(Theme.S(80), Theme.S(44)),
        };
        _cancel.Click += (s, e) => { if (!_working) { DialogResult = DialogResult.Cancel; Close(); } };
        Controls.Add(_cancel);

        AcceptButton = _install;
        CancelButton = _cancel;
    }

    void OnInstall(object sender, EventArgs e)
    {
        string d = Program.NormalizeDir(_dir.Value.Trim());
        if (d.Length == 0) { MessageBox.Show("请填写安装位置。", Theme.AppName); return; }
        _dir.Value = d;        // 回填给用户看：真正装到哪儿

        _working = true;
        _install.Enabled = false; _dir.Enabled = false;
        _desktop.Enabled = false; _startmenu.Enabled = false; _cancel.Enabled = false;
        Cursor = Cursors.WaitCursor;

        InstallDir = d;
        int step = 0;
        Action<string> report = msg =>
        {
            _status.Text = msg;
            _bar.Value = Math.Min(0.95, 0.15 + step++ * 0.22);
            _status.Refresh(); _bar.Refresh();
            Application.DoEvents();
        };

        try
        {
            _installer(d, _desktop.Checked, _startmenu.Checked, report);
            _bar.Value = 1.0;
            DialogResult = DialogResult.OK;
            Close();
        }
        catch (Exception ex)
        {
            _working = false;
            Cursor = Cursors.Default;
            _bar.Value = 0;
            _install.Enabled = true; _dir.Enabled = true;
            _desktop.Enabled = true; _startmenu.Enabled = true; _cancel.Enabled = true;
            _status.Text = "安装失败。";
            MessageBox.Show("安装失败：\n\n" + ex.Message, Theme.AppName,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
