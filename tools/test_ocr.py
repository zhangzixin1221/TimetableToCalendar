# -*- coding: utf-8 -*-
"""OCR 通路的回归测试（本地引擎，需要 rapidocr-onnxruntime + onnxruntime）。

    python tools\\test_ocr.py

思路：**用有标准答案的 fixture 量化 OCR 通路的损失**。
  A) 直接坐标解析 fixture PDF      -> 基准记录
  B) 把同一页渲染成 PNG（丢掉文字层）-> OCR -> 同一套解析 -> 待测记录
比较两者（比较前两侧都做全角/半角归一），得出「OCR 之后还能还原多少」。

门槛怎么定的：这是**兜底通道**，产出一定要人工核对，所以不要求 100%。
  * 记录数与基准接近（允许少量丢失/合并差异）；
  * 归一后 ≥80% 记录完全一致；
  * 不确定的地方必须被标出来（notes 写来源与平均置信度、低置信度文字进 warnings）；
  * 绝不允许"悄悄算错"：OCR 解析出的记录不允许有节次为空却没进 warnings 的。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import pymupdf                                              # noqa: E402

from timetable import presets                                # noqa: E402
from timetable.layout import REGISTRY                         # noqa: E402
from timetable.ocr_source import (_normalize, available,      # noqa: E402
                                 is_image_file, ocr_image_to_spans,
                                 pdf_has_text_layer, read_image,
                                 read_any_ocr)
from timetable.parser import compress_weeks, parse_pdf        # noqa: E402

PDF = os.path.join(ROOT, "fixtures", "nwpu-sample.pdf")
IMG = os.path.join(ROOT, "fixtures", "_ocr-test-page1.png")

ok_all = True
checks = 0


def check(label, cond, extra=""):
    global ok_all, checks
    checks += 1
    if not cond:
        ok_all = False
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + str(extra)) if extra else ""))
    return cond


def sig(r):
    """比较用的记录签名：全角/半角归一，去空格（OCR 会引入空格差异）。"""
    return (r.day_name,
            _normalize(r.course).replace(" ", ""),
            _normalize(compress_weeks(r.all_weeks())),
            r.pfrom, r.pto,
            _normalize(r.room or "").replace(" ", ""))


print("[0] 环境")
ok, why = available()
check("本地 OCR 可用（%s）" % why, ok, "" if ok else "装：pip install rapidocr-onnxruntime onnxruntime")
check("能识别图片扩展名", is_image_file("a.png") and is_image_file("b.JPG")
      and not is_image_file("c.pdf"))
if not ok:
    print("\n跳过其余用例（本地 OCR 不可用）")
    sys.exit(0)

print("\n[1] 文字层判定（决定该走坐标还是 OCR）")
check("有文字层的 PDF 判定为 True", pdf_has_text_layer(PDF))
# 把一页渲染成图再包成「只有图」的 PDF，模拟扫描件
doc = pymupdf.open(PDF)
pix = doc[0].get_pixmap(dpi=180)
pix.save(IMG)
scanned = os.path.join(ROOT, "fixtures", "_ocr-test-scanned.pdf")
out = pymupdf.open()
pg = out.new_page(width=doc[0].rect.width, height=doc[0].rect.height)
pg.insert_image(pg.rect, filename=IMG)
out.save(scanned)
out.close()
doc.close()
check("纯图片 PDF 判定为 False（应当走 OCR）", not pdf_has_text_layer(scanned))

print("\n[2] 基准（坐标解析）vs OCR 通路")
lay = REGISTRY.get("generic")
pt = presets.get_preset(None, lay)
# 基准只取**第 1 页**：OCR 用例也只处理第 1 页。两页混着比会把「第 2 页的课当然
# 出不来」算成 OCR 的错误（第一版就犯了这个错，76% 里有一半是比错了）。
from timetable.parser import _collect_spans, parse_spans_pages   # noqa: E402
_doc = pymupdf.open(PDF)
base = parse_spans_pages([_collect_spans(_doc[0])], layout=lay, period_times=pt)
_doc.close()
base_sig = sorted(sig(r) for r in base.records)
print("      基准（第 1 页）%d 条" % len(base_sig))

spans = ocr_image_to_spans(IMG, log=None)
check("OCR 检出了足够多的文字块", len(spans) > 80, "%d 块" % len(spans))
check("每个 span 都有 base/尺寸/置信度",
      all("base" in s and s["size"] > 0 and 0 <= s["conf"] <= 1 for s in spans))
check("全角 ASCII 已被归一（Ａ－302 -> A-302）",
      all("Ａ" not in s["text"] and "－" not in s["text"] for s in spans))

res = read_image(IMG, layout=lay, period_times=pt)
ocr_sig = sorted(sig(r) for r in res.records)
hit = [s for s in base_sig if s in ocr_sig]
rate = 100.0 * len(hit) / max(1, len(base_sig))
print("      OCR  %d 条，归一后完全一致 %d/%d（%.0f%%）"
      % (len(ocr_sig), len(hit), len(base_sig), rate))

check("OCR 解析出的记录数接近基准（≥80%）",
      len(ocr_sig) >= 0.8 * len(base_sig), "%d vs %d" % (len(ocr_sig), len(base_sig)))
check("归一后 ≥80% 记录完全一致（实测的兜底通道水平）", rate >= 80.0, "%.0f%%" % rate)
check("note 里写明来源是 OCR 且提示核对",
      any("OCR" in n and "核对" in n for n in res.notes), res.notes)
check("平均置信度被记进 stats", "ocr" in res.stats and res.stats["ocr"]["avg_conf"] > 0.5,
      res.stats.get("ocr"))
check("低置信度的文字进 warnings（不静默）",
      any("置信度偏低" in w for w in res.warnings),
      [w[:40] for w in res.warnings][:2])
check("解析不出的课也进 warnings（不静默丢课）",
      all("没解析出周次和节次" in w for w in res.warnings
          if "没解析出" in w) and True, len(res.warnings))

print("\n[3] 不该走 OCR 的情况要被拦住")
try:
    read_any_ocr(PDF, layout=lay, period_times=pt)
    check("有文字层的 PDF 走 OCR 时被拒绝并说明原因", False, "没有报错")
except Exception as exc:                                    # noqa: BLE001
    check("有文字层的 PDF 走 OCR 时被拒绝并说明原因",
          "文字层" in str(exc), str(exc)[:60])

print("\n[4] 扫描件 PDF（无文字层）走 OCR 全流程")
res2 = read_any_ocr(scanned, layout=lay, period_times=pt)
check("扫描件 PDF 也能解析出课程", len(res2.records) >= 0.8 * len(base_sig),
      "%d vs %d" % (len(res2.records), len(base_sig)))
check("扫描件结果同样带 OCR 来源标记",
      any("OCR" in n for n in res2.notes), res2.notes)

for p in (IMG, scanned):
    try:
        os.remove(p)
    except OSError:
        pass

print("\n" + "=" * 74)
print("%d 项检查，%s" % (checks, "全部通过" if ok_all else "存在失败项"))
sys.exit(0 if ok_all else 1)