# -*- coding: utf-8 -*-
"""OCR 交叉验证（crosscheck）的回归测试。

    python tools\\test_crosscheck.py

背景：即使 PDF 有文字层，也可能出现「假汉字」——网页打印会把常用字写成
康熙部首/部首补充区的字符（`⻄` 不是 `西`），或字体子集缺失吐出 `cid:`。
我们已用 normalize_text() 做归一，但归一之后就"看不出原来错在哪"，
所以再加一层独立复核：OCR 从像素重新认字，与文字层比对。

本文件钉住四件事：
  1. 可疑迹象能被识别（部首/cid/替换符），健康文件不误报；
  2. 比对逻辑本身正确（清洗、相似度、OCR 框内取文字）；
  3. 真的造一份"假汉字 PDF"：能检测、能复核、结论是"一致"（说明归一没改错字）；
  4. OCR 自己没把握（置信度低）时不计入"不一致"（否则会误导用户改好数据）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import pymupdf                                                # noqa: E402

from timetable import crosscheck as cc                          # noqa: E402
from timetable import presets                                   # noqa: E402
from timetable.layout import REGISTRY                            # noqa: E402
from timetable.ocr_source import available                       # noqa: E402
from timetable.parser import parse_pdf                           # noqa: E402

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


print("[1] 可疑迹象识别")
check("健康文字层不误报", cc.suspicion({"chars": 3000})[0] is False)
need, why = cc.suspicion({"radical_chars": 141, "radical_spans": 119})
check("部首字符会被识别为可疑", need and "康熙部首" in why[0], why[0][:40])
check("cid: 会被识别为可疑", cc.suspicion({"cid_chars": 5})[0])
check("替换符会被识别为可疑", cc.suspicion({"replacement_chars": 3})[0])
check("空健康度不报可疑", cc.suspicion(None)[0] is False)

print("\n[2] 比对工具函数")
check("清洗会去掉不换行空格/全角空格/零宽字符",
      cc._clean("教西A-301\xa0\xa0 \u3000B\u200b2") == "教西A-301 B2",
      repr(cc._clean("教西A-301\xa0\xa0 \u3000B\u200b2")))
check("相同字符串相似度=1", cc._similar("大学物理", "大学物理") == 1.0)
check("全角半角差异不算不一致",
      cc._similar("体育（篮球）", "体育(篮球)") > 0.9,
      cc._similar("体育（篮球）", "体育(篮球)"))
check("完全不同相似度低", cc._similar("大学物理", "马克思主义学院") < 0.4)
spans = [dict(x0=10, y0=10, x1=60, y1=20, base=18, size=9, text="大学"),
         dict(x0=60, y0=10, x1=110, y1=20, base=18, size=9, text="物理")]
check("按 OCR 框取文字层内容（中心落在框内即算）",
      cc._spans_inside(spans, (5, 5, 115, 25)) == "大学物理",
      cc._spans_inside(spans, (5, 5, 115, 25)))
check("框外的 span 不会被算进来",
      cc._spans_inside(spans, (5, 5, 55, 25)) == "大学")

print("\n[3] 真造一份「假汉字」PDF：检测 + 复核")
ok, why = available()
if not ok:
    check("本地 OCR 可用（否则跳过实时用例）", False, why)
else:
    # PyMuPDF 写入时会规范化部分部首字（⼀→一、⼯→工），但 ⻄(U+2EC4) 会保留，
    # 正好用来复现「看着是西、码位不是西」这种最常见的假汉字。
    fake = os.path.join(ROOT, "fixtures", "_crosscheck-fake.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=260)
    page.insert_text((24, 40), "⻄北⼯业⼤学学⽣课表", fontname="china-s", fontsize=16)
    # 表头必须**一个格子一个 span**（真实课表就是这样；写成一行会被当成一个 span，
    # 那样任何版式都认不出「星期一」这个独立表头）。
    for i, day in enumerate(("星期一", "星期二", "星期三", "星期四")):
        page.insert_text((40 + i * 95, 70), day, fontname="china-s", fontsize=11)
    page.insert_text((24, 100), "高等数学 06", fontname="china-s", fontsize=11)
    page.insert_text((24, 116), "(1~16周)(1-2节) 教西D-101 陈明远", fontname="china-s",
                     fontsize=9)
    doc.save(fake)
    doc.close()

    raw = pymupdf.open(fake)
    raw_text = raw[0].get_text()
    raw.close()
    has_radical = any(0x2E80 <= ord(c) <= 0x2EFF for c in raw_text)
    check("造出来的 PDF 里确实有部首区字符（否则这条用例没意义）", has_radical,
          [hex(ord(c)) for c in raw_text if 0x2E80 <= ord(c) <= 0x2EFF][:3])

    lay = REGISTRY.get("generic")
    pt = presets.get_preset(None, lay)
    res = parse_pdf(fake, layout=lay, period_times=pt)
    health = res.stats.get("text_layer") or {}
    check("解析时统计到了部首字符数", health.get("radical_chars", 0) >= 1, health)
    need, reasons = cc.suspicion(health)
    check("据此判定为「需要 OCR 复核」", need, reasons)

    rep = cc.crosscheck(fake, lay)
    check("复核真的跑了", rep.get("checked") is True)
    check("比对了若干处文字", (rep.get("matched") or 0) >= 3, rep.get("matched"))
    # 归一之后文字层已经是「西北工业大学」，OCR 从像素读出来的也应当是它 →
    # 两边一致恰恰说明我们的归一没把字改错。
    check("归一后的文字层与 OCR 读数一致（说明映射没改错字）",
          (rep.get("agree") or 0) >= 0.8, rep.get("agree"))
    check("不一致项带置信度（便于判断可信度）",
          all("conf" in d for d in (rep.get("diffs") or [])))

    # run_if_needed 要能把结论写回 result（CLI/GUI 共用入口）
    res2 = parse_pdf(fake, layout=lay, period_times=pt)
    rep2 = cc.run_if_needed(fake, res2, lay, mode="auto", log=None)
    check("run_if_needed 自动触发并记录 stats",
          rep2.get("checked") is True and "crosscheck" in (res2.stats or {}))

    # mode=off 必须完全不跑
    res3 = parse_pdf(fake, layout=lay, period_times=pt)
    rep3 = cc.run_if_needed(fake, res3, lay, mode="off", log=None)
    check("mode=off 时不跑 OCR", rep3.get("checked") is False, rep3.get("reasons"))

    os.remove(fake)

print("\n[4] 默认「每份都复核」的行为")
healthy = os.path.join(ROOT, "fixtures", "nwpu-sample.pdf")
if os.path.isfile(healthy) and ok:
    lay = REGISTRY.get("generic")
    res = parse_pdf(healthy, layout=lay, period_times=presets.get_preset(None, lay))
    # 默认模式是 on：即使文字层毫无可疑迹象，也要跑一遍复核（用户要求「不论什么课表都验」）
    rep_on = cc.run_if_needed(healthy, res, lay, mode="on", log=None)
    check("mode=on 时健康课表也会复核（默认行为）",
          rep_on.get("checked") is True, rep_on.get("reasons"))
    check("健康课表复核结果应当是「一致」（不能天天报假警）",
          not rep_on.get("diffs") and (rep_on.get("agree") or 0) >= 0.9,
          "agree=%s diffs=%d" % (rep_on.get("agree"), len(rep_on.get("diffs") or [])))
    # auto 仍然保留：健康课表下会跳过（想快的人用）
    res2 = parse_pdf(healthy, layout=lay, period_times=presets.get_preset(None, lay))
    rep_auto = cc.run_if_needed(healthy, res2, lay, mode="auto", log=None)
    check("mode=auto 时健康课表跳过复核（省时间）",
          rep_auto.get("checked") is False, rep_auto.get("reasons"))

print("\n[5] 切行差异不算字符错误（否则 always-on 会天天报警）")
check("一方是另一方的子串 → 判为切行差异（OCR 截断了）",
      cc._looks_like_split("学院：航海学院", "航海学院"))
check("长度相当但字不同 → 判为真差异",
      not cc._looks_like_split("(1~16周)", "(1~18周)"))
check("内容完全不同的长串 → 判为真差异（不能因为长度差大就放过）",
      not cc._looks_like_split("大学物理", "马克思主义学院"))

print("\n" + "=" * 74)
print("%d 项检查，%s" % (checks, "全部通过" if ok_all else "存在失败项"))
sys.exit(0 if ok_all else 1)