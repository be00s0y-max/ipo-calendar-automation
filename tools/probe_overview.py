"""진단용(확인 후 삭제) — 증권신고서 '인수인의 의견' 밸류에이션 표·상장예정주식수·사업의 개요 구조 확인."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import load_key, fetch_filing_xml  # noqa: E402
from update_calendar_data import table_rows  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

def clean(s):
    return re.sub(r"\s+", " ", s).strip()

key = load_key()
di = json.load(open("demandInfo.json", encoding="utf-8"))
VAL = re.compile(r"할인율|적용\s*(PER|EV|PSR|PBR)|주당\s*평가\s*가액|평가\s*시가총액|희망공모가\s*밴드|희망공모가액\s*밴드|공모가\s*밴드")
for name in ("이에스티", "씨아이에스케미칼", "래블업", "엘리스그룹"):
    print(f"===== DART {name}")
    fetch_filing_xml(key, di[name]["rcept_no"], "/tmp/f.xml")
    soup = BeautifulSoup(open("/tmp/f.xml", "rb").read(), "html.parser")
    n = 0
    for t in soup.find_all("table"):
        rows = table_rows(t)
        txt = " ".join(" ".join(r) for r in rows)
        if VAL.search(txt) and len(rows) <= 25 and not txt.startswith("주"):
            print(f"  -- table ({len(rows)} rows)")
            for r in rows:
                print("     ", " | ".join(clean(c) for c in r)[:230])
            n += 1
            if n >= 6: break
    text = clean(soup.get_text(" "))
    for m in list(re.finditer(r"상장\s*예정\s*주식\s*수[^.]{0,160}", text))[:3]:
        print("  [상장예정]", m.group(0)[:200])
    for m in list(re.finditer(r"[^.]{0,80}시가총액[^.]{0,120}", text))[:4]:
        print("  [시총]", m.group(0)[:220])
    i = text.find("사업의 개요")
    j = text.find("사업의 개요", i + 10)
    print("  [사업의 개요 2nd]", text[j:j + 500] if j >= 0 else "없음")
