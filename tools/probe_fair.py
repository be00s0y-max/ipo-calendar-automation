"""진단용(확인 후 삭제) — 주당 평가가액을 못 읽은 3건의 요약표 행 확인."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import load_key, fetch_filing_xml  # noqa: E402
from dart_parser import table_rows  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

key = load_key()
di = json.load(open("demandInfo.json", encoding="utf-8"))
for name in ("엘리스그룹", "제이앤티지", "케이엠에프"):
    print(f"===== {name}", flush=True)
    fetch_filing_xml(key, di[name]["rcept_no"], "/tmp/f.xml")
    soup = BeautifulSoup(open("/tmp/f.xml", "rb").read(), "html.parser")
    for t in soup.find_all("table"):
        if t.find("table") is not None:
            continue
        rows = table_rows(t)
        labels = {re.sub(r"\s+", "", r[0]) for r in rows if r}
        if "평가방법" in labels and "평가모형" in labels:
            print("  -- summary")
            for r in rows:
                print("    ", [re.sub(r"\s+", " ", c)[:60] for c in r])
