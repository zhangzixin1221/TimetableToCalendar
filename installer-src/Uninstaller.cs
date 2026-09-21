// 课表转日历 —— 卸载程序（独立小程序，约 15 KB）
//
// 由安装器在安装时写到安装目录下，取名 Uninstall.exe，并登记到注册表。
// 刻意不内嵌任何载荷，所以体积很小 —— 安装器虽然也能卸载，但它带着 26 MB 载荷，
// 复制进安装目录会白白多占 26 MB。
//
// 卸载要真的删干净，所以流程分两步：
//   ① 被点开的这个进程（位于安装目录里）只负责：确认、删快捷方式、删注册表、
//      把「自己」复制到 %TEMP% 再启动副本，然后**立刻退出**；
//   ② 副本进程在 %TEMP% 里跑，此时安装目录中没有任何进程占用，它带重试地删目录，
//      删完再把自己删掉。
//
// 为什么必须这样：Windows 不允许删除「正在运行的程序所在目录」，而且 cmd 进程如果继承了
// 安装目录作为工作目录，rmdir 也会失败（实测：工作目录在目标里 → 删不掉）；
// 另外从「设置 → 应用」卸载时工作目录正是安装目录，所以原来的实现会留下残留文件夹。

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Uninstaller
{
    const string REG_SUBKEY = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\TimetableToCalendar";
    const string LNK_NAME   = "TimetableToCalendar.lnk";

    [STAThread]
    static int Main(string[] args)
    {
        bool silent = false, fromTemp = false, madeParent = false;
        string dirArg = null;
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            if (string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase)) silent = true;
            else if (string.Equals(a, "--from-temp", StringComparison.OrdinalIgnoreCase)) fromTemp = true;
            else if (string.Equals(a, "--parent-created", StringComparison.OrdinalIgnoreCase)) madeParent = true;
            else if (a.StartsWith("--dir=", StringComparison.OrdinalIgnoreCase))
                dirArg = a.Substring("--dir=".Length).Trim('"');
        }

        try
        {
            if (fromTemp) return FinishRemoval(dirArg, silent, madeParent);

            string dir = Path.GetDirectoryName(Application.ExecutablePath);

            // 安装时记下的「父目录是我们建的」→ 删完主目录后如果它空了就一起删掉
            string fromReg = null;
            using (var k = Registry.CurrentUser.OpenSubKey(REG_SUBKEY))
            {
                if (k != null)
                {
                    fromReg = (string)k.GetValue("InstallLocation");
                    object pc = k.GetValue("ParentCreated");
                    madeParent = pc is int && (int)pc == 1;
                }
            }
            if (!string.IsNullOrEmpty(fromReg) && Directory.Exists(fromReg)) dir = fromReg;

            if (!Directory.Exists(dir))
            {
                if (!silent)
                    MessageBox.Show("没有找到安装目录，可能已经卸载过了。", "课表转日历",
                                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }

            // 程序还在跑的话，删不掉被占用的 exe，会留下半个目录
            string running = RunningFrom(dir);
            if (running != null)
            {
                if (!silent)
                    MessageBox.Show("程序正在运行，请先关闭「课表转日历」再卸载。\n\n" + running,
                                    "课表转日历", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 3;
            }

            if (!silent)
            {
                var res = MessageBox.Show(
                    "确定要卸载「课表转日历」吗？\n\n将删除安装目录：\n" + dir +
                    "\n\n（你自己另存的 .ics 课表文件不受影响）",
                    "课表转日历", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                if (res != DialogResult.Yes) return 2;
            }

            TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), LNK_NAME));
            TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), LNK_NAME));
            try { Registry.CurrentUser.DeleteSubKeyTree(REG_SUBKEY, false); } catch { }

            // 复制自己到 %TEMP% 再启动：这样安装目录里就没有任何进程了
            string tmp = Path.Combine(Path.GetTempPath(),
                                      "ttc-uninstall-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(Application.ExecutablePath, tmp, true);
            var psi = new ProcessStartInfo(tmp)
            {
                Arguments = "--from-temp --dir=\"" + dir + "\""
                            + (silent ? " --silent" : "")
                            + (madeParent ? " --parent-created" : ""),
                UseShellExecute = false,
                CreateNoWindow = true,
                // 工作目录一定要在安装目录之外，否则副本删不掉那个目录
                WorkingDirectory = Path.GetTempPath(),
            };
            Process.Start(psi);
            return 0;                      // 立刻退出，让出安装目录
        }
        catch (Exception ex)
        {
            if (silent) Console.Error.WriteLine("卸载失败: " + ex.Message);
            else MessageBox.Show("卸载失败：\n\n" + ex.Message, "课表转日历",
                                 MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    /// <summary>副本进程：真正删除安装目录，删完再删自己。</summary>
    static int FinishRemoval(string dir, bool silent, bool madeParent)
    {
        if (string.IsNullOrEmpty(dir)) return 1;

        // 带重试：万一有文件刚被释放（杀进程、杀毒软件扫描），多试几次
        for (int i = 0; i < 40 && Directory.Exists(dir); i++)
        {
            try { Directory.Delete(dir, true); } catch { }
            if (Directory.Exists(dir)) Thread.Sleep(250);
        }
        bool ok = !Directory.Exists(dir);

        // 父目录是我们安装时新建的、而且现在已经空了，就顺手删掉，不留空文件夹
        if (ok && madeParent)
        {
            try
            {
                string parent = Path.GetDirectoryName(dir.TrimEnd('\\'));
                if (!string.IsNullOrEmpty(parent) && Directory.Exists(parent)
                    && Directory.GetFileSystemEntries(parent).Length == 0)
                    Directory.Delete(parent, false);
            }
            catch { }
        }

        if (!silent)
            MessageBox.Show(ok ? "已卸载。" : "卸载完成，但有文件没能删除，请手动删掉：\n" + dir,
                            "课表转日历", MessageBoxButtons.OK,
                            ok ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
        else if (!ok) Console.Error.WriteLine("有文件没能删除: " + dir);

        SelfDelete();
        return ok ? 0 : 1;
    }

    /// <summary>删掉自己（正在运行的 exe 删不掉，交给一条短命的 cmd 稍后删）。</summary>
    static void SelfDelete()
    {
        try
        {
            string me = Application.ExecutablePath;
            Process.Start(new ProcessStartInfo("cmd.exe",
                "/c ping -n 2 127.0.0.1 >nul & del /f /q \"" + me + "\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetTempPath(),
            });
        }
        catch { }
    }

    static void TryDelete(string path) { try { if (File.Exists(path)) File.Delete(path); } catch { } }

    /// <summary>检查安装目录里的程序是否正在运行，返回它的路径（没在运行返回 null）。</summary>
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
                    if (p.Id == me) continue;   // 自己就在这个目录里，别把自己当成“正在运行”
                    string path = p.MainModule.FileName;
                    if (path != null && path.StartsWith(full, StringComparison.OrdinalIgnoreCase))
                        return path;
                }
                catch { }
                finally { p.Dispose(); }
            }
        }
        catch { }
        return null;
    }
}
