# -*- coding: utf-8 -*-
"""总跑器：把整个项目的测试一次跑完，给一张结果表。

    python tools\\run_all_tests.py            # 全跑
    python tools\\run_all_tests.py --fast     # 跳过 GUI/窗口类（无显示环境时用）

这是"最终测试"的入口。每一项都是独立脚本，这里只负责调度与汇总：
任何一个非 0 退出码都算失败，最后给出总数。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(ROOT, "fixtures", "nwpu-sample.pdf")

SUITE = [
    ("版式层（切分/周次/校验/合并/自动识别/真实写法）", ["tools/test_layout.py"]),
    ("时刻制课表（3位代码/时刻区间/粘连/反查）", ["tools/test_timebased.py"]),
    ("Excel/CSV（模板/别名/往返/错误定位）", ["tools/test_xlsx.py"]),
    ("OCR 输入（文字层判定/与基准比对/低置信外露）", ["tools/test_ocr.py"]),
    ("OCR 交叉验证（假汉字/逐处比对/always-on）", ["tools/test_crosscheck.py"]),
    ("联网节假日（合并/跨年/缓存/往返/错误分支）", ["tools/test_holiday_fetch.py"]),
    ("合成 fixture：nwpu 版式 44 项断言", ["tools/make_fixture.py", "--check", "--longname-trap"]),
    ("合成 fixture：inline/flat-table 两版式", ["tools/make_fixture_alt.py", "--check"]),
    ("CLI 端到端矩阵（三输入×开关×退出码）", ["tools/test_cli_e2e.py"]),
    ("端到端自检（解析→生成→第三方库展开核对）", ["src/selftest.py", FIX]),
    ("界面烟雾测试（headless 驱动完整流程）", ["src/smoke_test.py", FIX]),
]
GUI_SUITE = [
    ("窗口缩放回归（geometry）", ["tools/resize_test.py"]),
    ("窗口缩放回归（窗口管理器）", ["tools/resize_wm_test.py"]),
    ("预览表列宽自适应", ["tools/table_fit_test.py", FIX]),
    ("圆角控件边缘暗角", ["tools/check_corners.py"]),
]


def main(argv):
    suite = list(SUITE)
    if "--fast" not in argv:
        suite += GUI_SUITE
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    rows = []
    t_all = time.time()
    for label, cmd in suite:
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, "-X", "utf8"] +
                               [os.path.join(ROOT, c) if i == 0 else c
                                for i, c in enumerate(cmd)],
                               cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=900)
            code = p.returncode
            tail = p.stdout.decode("utf-8", "replace").strip().splitlines()
            last = tail[-1].strip() if tail else ""
        except subprocess.TimeoutExpired:
            code, last = 124, "超时（>900s）"
        rows.append((label, code, time.time() - t0, last[:60]))
        print("  [%s] %-44s %5.1fs  %s"
              % ("PASS" if code == 0 else "FAIL", label, time.time() - t0, last))
    bad = [r for r in rows if r[1] != 0]
    print("\n" + "=" * 78)
    print("共 %d 组测试，通过 %d，失败 %d，总耗时 %.1f 分钟"
          % (len(rows), len(rows) - len(bad), len(bad), (time.time() - t_all) / 60))
    for label, code, dt, last in bad:
        print("  失败：%s（exit %d）%s" % (label, code, last))
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))