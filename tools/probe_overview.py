"""진단용(확인 후 삭제) — 공모개요(밸류에이션·상장후 주식수·사업내용) 출처 구조 확인."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import api_get, load_key, fetch_filing_xml  # noqa: E402
from update_calendar_data import fetch, table_rows  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

def clean(s):
    return re.sub(r"\s+", " ", s).strip()

# 1) 38 상세 페이지: 짧은 라벨|값 쌍 전부
for no in ("2332", "2348"):
    print(f"===== 38 no={no}")
    soup = BeautifulSoup(fetch(f"https://www.38.co.kr/html/fund/?o=v&no={no}"), "html.parser")
    seen = set()
    for t in soup.find_all("table"):
        for row in table_rows(t):
            if 2 <= len(row) <= 6 and sum(len(c) for c in row) < 200:
                line = " | ".join(row)
                if line not in seen:
                    seen.add(line); print("  ", line)

key = load_key()
cc = json.load(open("corp_codes.json", encoding="utf-8"))
di = json.load(open("demandInfo.json", encoding="utf-8"))
KW = re.compile(r"PER|EV/EBITDA|PSR|PBR|주가수익비율|적용\s*배수|할인율|평가\s*시가총액|상장\s*예정\s*주식|상장예정주식|유통가능|사업의\s*개요|주요\s*제품|희망공모가액")
for name in ("이에스티", "씨아이에스케미칼"):
    print(f"===== DART {name}")
    code = cc.get(name)
    if code:
        c = json.loads(api_get(f"https://opendart.fss.or.kr/api/company.json?crtfc_key={key}&corp_code={code}").decode("utf-8"))
        print("  company.json:", {k: c.get(k) for k in ("corp_name", "induty_code", "corp_cls", "est_dt")})
    rc = di[name]["rcept_no"]
    fetch_filing_xml(key, rc, "/tmp/f.xml")
    soup = BeautifulSoup(open("/tmp/f.xml", "rb").read(), "html.parser")
    # 표 중 키워드 포함 표: 앞 12행
    n = 0
    for t in soup.find_all("table"):
        txt = clean(t.get_text(" "))
        if KW.search(txt) and len(txt) < 4000:
            rows = table_rows(t)
            print(f"  -- table ({len(rows)} rows)")
            for r in rows[:14]:
                print("     ", " | ".join(clean(c) for c in r)[:220])
            n += 1
            if n >= 14: break
    # 본문 문단: 사업 개요 첫 부분
    text = clean(soup.get_text(" "))
    for kw in ("사업의 개요", "회사의 개요", "주요 제품", "상대가치", "비교회사"):
        i = text.find(kw)
        print(f"  [{kw}] ", text[i:i+600] if i >= 0 else "없음")
