# 临时脚本：用 Streamlit 自带的 AppTest 无头跑一遍每个页面，只看有没有抛异常
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

DEMO = os.path.join("data", "raw", "试玩数据-合成-100份.csv")
V1V2 = os.path.join("out", "_tmp_V1V2.csv")

if not os.path.exists(V1V2):
    from playpulse.io_load import load_auto
    df = load_auto(DEMO).df.copy()
    v2 = df.copy()
    v2["build_version"] = "V2"
    m = v2["level_id"] == 2
    v2.loc[m, "duration_sec"] = (v2.loc[m, "duration_sec"] * 0.75).round().astype(int)
    v2.loc[m, "stuck_sec"] = (v2.loc[m, "stuck_sec"] * 0.6).round().astype(int)
    pd.concat([df, v2], ignore_index=True).to_csv(V1V2, index=False, encoding="utf-8-sig")

CASES = [
    (os.path.join(ROOT, "app.py"), {"pp_path": DEMO, "pp_project": "开箱物语"}),
    (os.path.join(ROOT, "pages", "1_版本对比.py"), {"pp_path": V1V2}),
    (os.path.join(ROOT, "pages", "2_基准线_PlayBench.py"),
     {"pp_path": DEMO, "pp_project": "开箱物语"}),
    (os.path.join(ROOT, "pages", "3_口径说明.py"), {}),
    (os.path.join(ROOT, "pages", "4_追问.py"), {"pp_path": DEMO}),
]

bad = 0
for path, state in CASES:
    try:
        at = AppTest.from_file(path, default_timeout=120)
        for k, v in state.items():
            at.session_state[k] = v
        at.run()
    except Exception as e:
        print("[FAIL] %-28s 运行期异常: %s" % (path, str(e)[:300]))
        bad += 1
        continue
    errs = [e.value for e in at.exception]
    if errs:
        bad += 1
        print("[FAIL] %-28s 页面内异常: %s" % (path, errs[0][:400]))
    else:
        print("[ OK ] %-28s 元素 %d 个 / 表格 %d 个"
              % (path, len(at.get("markdown")) if False else len(at.main), len(at.get("dataframe"))))

print("\n结论：%s" % ("全部页面无异常" if not bad else "%d 个页面有问题" % bad))
sys.exit(1 if bad else 0)
