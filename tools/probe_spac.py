"""진단용(확인 후 삭제) — DART에서 스팩(기업인수목적회사) 회사명이 어떻게 적히는지 확인."""
import datetime
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import api_get, load_key  # noqa: E402

key = load_key()
end = datetime.date.today()
begin = end - datetime.timedelta(days=85)
seen = {}
page = 1
while True:
    data = json.loads(api_get(
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={key}&bgn_de={begin:%Y%m%d}&end_de={end:%Y%m%d}&pblntf_ty=C&page_count=100&page_no={page}"
    ).decode("utf-8"))
    if data.get("status") != "000":
        break
    for it in data.get("list", []):
        nm = it.get("corp_name", "")
        if re.search(r"스팩|기업인수|인수목적", nm) and "증권신고서" in it.get("report_nm", ""):
            seen.setdefault((nm, it["corp_code"]), it["report_nm"])
    if page >= data.get("total_page", 1):
        break
    page += 1
print("##### 최근 85일 스팩 증권신고서 제출사")
for (nm, cc), rn in sorted(seen.items()):
    print("   ", nm, cc, rn)
raw = api_get(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}")
with zipfile.ZipFile(io.BytesIO(raw)) as zf:
    xml = zf.read(zf.namelist()[0]).decode("utf-8", errors="ignore")
print("##### corpCode.xml 기존 매핑 확인")
for cc in ["02032155", "02030166"]:
    m = re.search(r"<corp_code>" + cc + r"</corp_code>\s*<corp_name>([^<]*)</corp_name>", xml)
    print("   ", cc, m.group(1) if m else None)
