# -*- coding: utf-8 -*-
"""CLI 端到端矩阵测试：把「整个程序」当黑盒跑一遍。

    python tools\\test_cli_e2e.py

与单元/回归测试的分工：那些测试直接 import 模块、喂构造好的数据；这份测试
**只通过命令行**（subprocess 跑 src/main.py）验证真实用户会走的路径：
三种输入 × 各种开关 × 退出码 × 关键日志/产物。退出码是契约的一部分：
  0 成功 / 1 解析或生成失败 / 2 参数或输入文件问题 / 3 配置自检拦住。

为了控制时间：只有第一条用例开 OCR 复核（默认行为），其余显式 --crosscheck off；
节假日走本地缓存（首次联网的结果已缓存），断网用例用 file:/// 假镜像。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(ROOT, "src", "main.py")
FIX = os.path.join(ROOT, "fixtures")
PDF = os.path.join(FIX, "nwpu-sample.pdf")
HOL = os.path.join(FIX, "holidays-test.txt")

TMP = tempfile.mkdtemp(prefix="tt-e2e-")
ok_all = True
ran = 0


def run(args, expect, must=(), must_not=(), label=None):
    """跑一条 CLI，校验退出码与输出关键字。"""
    global ok_all, ran
    ran += 1
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([sys.executable, "-X", "utf8", MAIN] + args,
                       cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    out = (p.stdout.decode("utf-8", "replace")
           + p.stderr.decode("utf-8", "replace"))
    good = p.returncode == expect
    for m in must:
        if m not in out:
            good = False
    for m in must_not:
        if m in out:
            good = False
    tag = "PASS" if good else "FAIL"
    if not good:
        ok_all = False
    print("  [%s] %-52s exit=%d(期望%d)"
          % (tag, label or " ".join(args)[:50], p.returncode, expect))
    if not good:
        for m in must:
            if m not in out:
                print("        缺关键字：%r" % m)
        for m in must_not:
            if m in out:
                print("        不该出现：%r" % m)
        if p.returncode != expect:
            tail = [l for l in out.splitlines() if l.strip()][-4:]
            for l in tail:
                print("        | " + l[:100])
    return out


def w(name):
    return os.path.join(TMP, name)


print("=" * 78)
print("CLI 端到端矩阵（工作目录 %s）" % ROOT)
print("=" * 78)

# ---------------------------------------------------------------- A. 帮助与清单
print("\n[A] 帮助 / 清单 / 模板（不需要课表）")
run(["--cli", "-h"], 0, must=["--week1", "--crosscheck"], label="--cli -h 列出全部参数")
run(["--cli", "--list-layouts"], 0, must=["generic", "inline-grid", "flat-table"],
    label="--list-layouts 列出内置版式")
run(["--cli", "--layout-template", w("tpl.json")], 0, must=["已导出版式模板"],
    label="--layout-template 导出版式模板")
run(["--cli", "--xlsx-template", w("tpl.xlsx")], 0, must=["已导出 Excel 模板"],
    label="--xlsx-template 导出 Excel 模板")
run(["--cli"], 2, must=["要给定课表文件"], label="不给输入文件 → 退出码 2")

# ---------------------------------------------------------------- B. PDF 主路径
print("\n[B] PDF（有文字层）主路径")
ics1 = w("main.ics")
out = run(["--cli", PDF, "-o", ics1, "--week1", "2026-08-31"], 0,
          must=["OCR 复核", "一致", "已生成"],
          label="默认全流程（含 OCR 复核 + 联网节假日）")
data = open(ics1, "rb").read()
good_ics = (b"\r\n" in data and data.count(b"BEGIN:VEVENT") > 20
            and b"SUMMARY:" in data and b"EXDATE" in data)
ran += 1
print("  [%s] 产物 .ics 结构（CRLF / VEVENT>20 / 有 EXDATE）"
      % ("PASS" if good_ics else "FAIL"))
if not good_ics:
    ok_all = False

run(["--cli", PDF, "-o", w("off.ics"), "--crosscheck", "off", "--no-holidays"], 0,
    must=["已生成"], must_not=["OCR 复核：比对"],
    label="--crosscheck off + --no-holidays")
run(["--cli", PDF, "-o", w("m_none.ics"), "--merge", "none",
     "--crosscheck", "off", "--no-holidays"], 0, must=["已生成"],
    label="--merge none")
run(["--cli", PDF, "-o", w("m_art.ics"), "--merge", "artifact",
     "--crosscheck", "off", "--no-holidays"], 0, must=["已生成"],
    label="--merge artifact")
run(["--cli", PDF, "-o", w("quiet.ics"), "--alarm", "0", "--night", "off",
     "--crosscheck", "off", "--no-holidays", "-q"], 0,
    must=["已生成"], must_not=["夜间汇总"],
    label="--alarm 0 --night off -q（静默且无提醒）")
xout = w("round.xlsx")
run(["--cli", PDF, "--xlsx-out", xout, "--crosscheck", "off", "--no-holidays"], 0,
    must=["已把", "条课程导出"], label="--xlsx-out 导出核对表")
run(["--cli", xout, "-o", w("from_xlsx.ics"), "--crosscheck", "off",
     "--no-holidays"], 0, must=["Excel/CSV", "已生成"],
    label="导出的 Excel 再当输入（闭环）")
run(["--cli", PDF, "--dump-layout"], 0, must=["表头", "详情行"],
    label="--dump-layout 打印版面结构")
run(["--cli", PDF, "--auto-layout", "-o", w("auto.ics"),
     "--crosscheck", "off", "--no-holidays"], 0, must=["自动识别版式", "已生成"],
    label="--auto-layout 自动挑版式")

# ---------------------------------------------------------------- C. 节假日
print("\n[C] 节假日：联网 / 文件 / 断网降级")
run(["--cli", PDF, "-o", w("hol_file.ics"), "--crosscheck", "off",
     "--holidays-file", HOL], 0, must=["中秋节", "已生成"],
    label="--holidays-file 用本地文件")
sav = w("hol.txt")
run(["--cli", PDF, "-o", w("hol_save.ics"), "--crosscheck", "off",
     "--save-holidays", sav], 0, must=["已把这批节假日写成"],
    label="--save-holidays 把联网结果落盘")
ran += 1
saved_ok = os.path.isfile(sav) and "放假" in open(sav, encoding="utf-8").read()
print("  [%s] 落盘文件内容是可编辑 DSL" % ("PASS" if saved_ok else "FAIL"))
if not saved_ok:
    ok_all = False
run(["--cli", PDF, "-o", w("hol_dead.ics"), "--crosscheck", "off",
     "--holidays-url", "file:///nonexistent-mirror/{year}.json",
     "--holidays-refresh"], 0,
    must=["本次仍会生成日历"], label="断网/坏镜像 → 降级但照常生成（exit 0）")

# ---------------------------------------------------------------- D. Excel
print("\n[D] Excel / CSV")
run(["--cli", w("tpl.xlsx"), "-o", w("tpl.ics"), "--crosscheck", "off",
     "--no-holidays"], 0, must=["Excel/CSV", "已生成"], label="模板（含示例行）直接生成")
bad_x = w("bad.xlsx")
import openpyxl
wb = openpyxl.Workbook()
wb.active.append(["随便", "写点", "东西"])
wb.save(bad_x)
run(["--cli", bad_x, "-o", w("bad.ics")], 2, must=["没找到表头行"],
    label="无表头的 Excel → 退出码 2 且说明原因")
csv_p = w("t.csv")
with open(csv_p, "w", encoding="utf-8-sig", newline="") as f:
    f.write("课程名称,星期,节次,周次,教师,上课地点\n高等数学,星期一,1-2,1-16,张三,教三201\n")
run(["--cli", csv_p, "-o", w("t.ics"), "--crosscheck", "off", "--no-holidays"], 0,
    must=["已生成"], label="CSV 输入")

# ---------------------------------------------------------------- E. 图片/扫描件
print("\n[E] 图片 / 无文字层 PDF（OCR 主路径）")
import pymupdf
png = w("page1.png")
doc = pymupdf.open(PDF)
doc[0].get_pixmap(dpi=120).save(png)
scan = w("scan.pdf")
o = pymupdf.open()
pg = o.new_page(width=doc[0].rect.width, height=doc[0].rect.height)
pg.insert_image(pg.rect, filename=png)
o.save(scan)
o.close()
doc.close()
run(["--cli", png, "-o", w("img.ics"), "--crosscheck", "off", "--no-holidays"], 0,
    must=["走 OCR", "已生成"], label="图片输入 → OCR 解析")
run(["--cli", scan, "-o", w("scan.ics"), "--crosscheck", "off", "--no-holidays"], 0,
    must=["OCR 复核：跳过", "已生成"],
    label="扫描件 PDF → OCR 解析，且复核明确跳过")

# ---------------------------------------------------------------- F. 版式工具
print("\n[F] 版式配置")
# 外部版式 JSON：从内置 generic 复制一份改 id（模板文件只有 2 节作息，
# 拿它跑完整课表必然报「缺节次时间」—— 那是正确行为，不该当成功用例）
import json as _json
sys.path.insert(0, os.path.join(ROOT, "src"))
from timetable.layout import REGISTRY as _REG          # noqa: E402
lay_json = w("copy-generic.json")
_d = _REG.get("generic").to_dict()
_d["id"] = "e2e-copy"
_d["name"] = "e2e 副本"
with open(lay_json, "w", encoding="utf-8") as f:
    _json.dump(_d, f, ensure_ascii=False)
run(["--cli", PDF, "--layout", lay_json, "-o", w("lay.ics"),
     "--crosscheck", "off", "--no-holidays"], 0, must=["已生成"],
    label="--layout 载入外部版式 JSON（generic 的副本）")
run(["--cli", PDF, "--layout", w("tpl.json"), "-o", w("lay2.ics"),
     "--crosscheck", "off", "--no-holidays"], 3, must=["没有设置上下课时间"],
    label="作息只有 2 节的版式跑完整课表 → 明确报错（exit 3）")
run(["--cli", PDF, "--layout", "不存在的版式"], 2, must=["没有版式"],
    label="未知版式 id → 退出码 2")
run(["--cli", w("tpl.xlsx"), "--dump-layout"], 2, must=["只对 PDF 有意义"],
    label="--dump-layout 用于 Excel → 退出码 2 并说明")

# ---------------------------------------------------------------- G. doctor 门禁
print("\n[G] 配置自检门禁（不报错只算错的那类）")
run(["--cli", PDF, "-o", w("d1.ics"), "--week1", "2026-09-01",
     "--crosscheck", "off", "--no-holidays"], 3, must=["不是星期一"],
    label="第 1 周周一不是周一 → 退出码 3")
run(["--cli", PDF, "-o", w("d2.ics"), "--week1", "2026-09-01", "--force",
     "--crosscheck", "off", "--no-holidays"], 0, must=["已生成"],
    label="--force 可越过门禁")
run(["--cli", PDF, "-o", w("d3.ics"), "--week1", "2025-09-01",
     "--crosscheck", "off", "--no-holidays"], 3, must=["对不上"],
    label="日期推出来的学期与 PDF 不符 → 退出码 3")

# ---------------------------------------------------------------- H. 错误路径
print("\n[H] 错误路径")
run(["--cli", w("不存在.pdf")], 2, must=["找不到文件"], label="文件不存在 → 退出码 2")
junk = w("junk.pdf")
d = pymupdf.open()
p = d.new_page()
p.insert_text((50, 50), "这只是一段随便的文字，不是课表", fontname="china-s")
d.save(junk)
d.close()
run(["--cli", junk, "-o", w("junk.ics"), "--crosscheck", "off"], 1,
    must=["表头"], label="非课表 PDF → 退出码 1 且明确说明（没找到星期表头）")

# ---------------------------------------------------------------- I. 打包自检入口
print("\n[I] --selftest（打包后也能跑的自检入口）")
rep = w("report.txt")
run(["--selftest", PDF, w("st.ics"), rep], 0, label="--selftest 三参数")
ran += 1
rep_ok = os.path.isfile(rep) and "RESULT: OK" in open(rep, encoding="utf-8").read()
print("  [%s] 报告里写明 RESULT: OK" % ("PASS" if rep_ok else "FAIL"))
if not rep_ok:
    ok_all = False
    if os.path.isfile(rep):
        print("        报告尾部：" + open(rep, encoding="utf-8").read()[-200:])

print("\n" + "=" * 78)
print("共 %d 条用例，%s" % (ran, "全部通过" if ok_all else "存在失败"))
print("=" * 78)
sys.exit(0 if ok_all else 1)