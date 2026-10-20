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
    print(f"===== DART {name}", flush=True)
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
    def hits(kw, k, before=80, after=160):
        i, out = 0, []
        while len(out) < k:
            i = text.find(kw, i)
            if i < 0: break
            out.append(text[max(0, i - before):i + after]); i += len(kw)
        return out
    for h in hits("상장예정주식", 3): print("  [상장예정]", h)
    for h in hits("시가총액", 6): print("  [시총]", h)
    i = text.find("사업의 개요")
    j = text.find("사업의 개요", i + 10)
    print("  [사업의 개요 2nd]", text[j:j + 500] if j >= 0 else "없음")
