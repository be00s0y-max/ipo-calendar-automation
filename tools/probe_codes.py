"""
진단용(확인 후 삭제) — 1) 38 종목 상세페이지에 종목코드·확정공모가가 어떤 형태로
나오는지, 2) DART 고유번호 목록(corpCode.xml)·기업개요에 상장 전 회사의 종목코드가
들어 있는지를 로그로 찍는다. 이 클라우드 작업 환경에선 두 사이트 접속이 막혀 있다.
"""
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_calendar_data import fetch, DETAIL_URL, table_rows  # noqa: E402
from update_dart_data import api_get, load_key  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

NAMES = ["크리에이츠", "씨앤엠", "제이앤티지", "래블업", "SK증권스팩14호", "엘리스그룹"]
KW = re.compile(r"종목코드|확정공모가|희망공모가|청약한도|공모가")

deals = json.load(open("deals.json", encoding="utf-8"))
print("##### 38 상세페이지")
for dl in deals:
    if dl["name"] not in NAMES:
        continue
    print("=====", dl["name"], dl.get("no"))
    soup = BeautifulSoup(fetch(DETAIL_URL.format(no=dl["no"])), "html.parser")
    seen = set()
    for t in soup.find_all("table"):
        for r in table_rows(t):
            line = " | ".join(c[:50] for c in r)
            if KW.search(line) and line not in seen and len(line) < 400:
                seen.add(line)
                print("   ", line)

print("##### DART corpCode.xml")
key = load_key()
raw = api_get(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}")
with zipfile.ZipFile(io.BytesIO(raw)) as zf:
    xml = zf.read(zf.namelist()[0]).decode("utf-8", errors="ignore")
for nm in NAMES + ["씨앤엠", "에이치엘지노믹스", "니어스랩"]:
    for m in re.finditer(r"<list>(?:(?!</list>).)*?<corp_name>" + re.escape(nm) + r"</corp_name>(?:(?!</list>).)*?</list>", xml, re.S):
        print("   ", re.sub(r"\s+", " ", m.group(0))[:300])

print("##### DART company.json")
for cc in ["00618173", "01403198"]:
    d = json.loads(api_get(f"https://opendart.fss.or.kr/api/company.json?crtfc_key={key}&corp_code={cc}").decode("utf-8"))
    print("   ", cc, d.get("corp_name"), "stock_code=", repr(d.get("stock_code")), "corp_cls=", d.get("corp_cls"))
