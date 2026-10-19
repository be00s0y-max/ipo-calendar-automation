"""진단용(확인 후 삭제) — 수요예측 조건/확정 공모개요를 못 읽은 신고서의 원문 구조 확인."""
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import api_get, load_key, EQUITY_FILING_RE, FILING_NAME_RE  # noqa: E402
from dart_parser import table_rows  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

KW = re.compile(r"최고|한도|희망|공모가|모집가|확정|단위")
key = load_key()
cc = json.load(open("corp_codes.json", encoding="utf-8"))
for name, want in [("엠비디", "terms"), ("아이엠스팩1호", "terms"), ("진코스텍", "final")]:
    data = json.loads(api_get(
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={key}&corp_code={cc[name]}&bgn_de=20260101&end_de=20261231&page_count=100").decode("utf-8"))
    items = [it for it in data.get("list", []) if (FILING_NAME_RE if want == "final" else EQUITY_FILING_RE).search(it["report_nm"])]
    it = max(items, key=lambda x: x["rcept_no"])
    print("=" * 80, "\n", name, it["report_nm"], it["rcept_no"])
    raw = api_get(f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={key}&rcept_no={it['rcept_no']}")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        print("files:", [(n, zf.getinfo(n).file_size) for n in zf.namelist()])
        content = zf.read(sorted(zf.namelist(), key=lambda n: -zf.getinfo(n).file_size)[0]).decode("utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    n = 0
    for ti, t in enumerate(soup.find_all("table")):
        rows = table_rows(t)
        if not rows or len(rows) > 25:
            continue
        flat = " ".join(c for r in rows for c in r)
        if KW.search(flat) and len(flat) < 1500:
            print(f"[table {ti}]")
            for r in rows[:10]:
                print("   | " + " | ".join(c[:70] for c in r))
            n += 1
            if n >= 30:
                break
    for m in re.finditer(r"[^.]{0,80}최고\s*한도[^.]{0,120}", soup.get_text(" ")):
        print("   * " + re.sub(r"\s+", " ", m.group(0)))
