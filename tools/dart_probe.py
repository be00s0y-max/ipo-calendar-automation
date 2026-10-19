"""
진단용 — 최근 지분증권 증권신고서 몇 건을 받아, 공모가 밴드·신청 수량/가격 단위·
기관 최고한도가 원문에서 어떤 표/문장으로 나오는지 로그에 찍는다. 파서를 실제
원문 구조에 맞춰 짜기 위한 것이고, 데이터 파일은 건드리지 않는다.
(이 클라우드 작업 환경에서는 DART 접속이 막혀 있어 GitHub Actions에서 돌린다.)
"""
import datetime
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_dart_data import api_get, load_key  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from dart_parser import table_rows  # noqa: E402

KEYWORDS = re.compile(r"희망공모|공모희망|밴드|수량\s*단위|가격\s*단위|호가|최고\s*한도|최고한도|최저\s*신청|최고\s*신청|신청\s*한도|참여\s*한도")
EQUITY = re.compile(r"증권신고서\(지분증권\)")


def main():
    key = load_key()
    end = datetime.date.today()
    begin = end - datetime.timedelta(days=40)
    url = (
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={key}&bgn_de={begin:%Y%m%d}&end_de={end:%Y%m%d}&pblntf_ty=C&page_count=100&page_no=1"
    )
    data = json.loads(api_get(url).decode("utf-8"))
    print("list status", data.get("status"), data.get("total_count"))
    picked = []
    for item in data.get("list", []):
        nm = item.get("report_nm", "")
        if EQUITY.search(nm) and "스팩" not in item.get("corp_name", "") and "기업인수목적" not in item.get("corp_name", ""):
            picked.append(item)
        if len(picked) >= 2:
            break
    for item in picked:
        print("=" * 100)
        print(item["corp_name"], item["corp_code"], item["report_nm"], item["rcept_no"])
        raw = api_get(f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={key}&rcept_no={item['rcept_no']}")
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            name = sorted(zf.namelist(), key=lambda n: -zf.getinfo(n).file_size)[0]
            content = zf.read(name).decode("utf-8", errors="ignore")
        soup = BeautifulSoup(content, "html.parser")
        print("--- 표")
        for ti, t in enumerate(soup.find_all("table")):
            rows = table_rows(t)
            flat = " ".join(c for r in rows for c in r)
            if KEYWORDS.search(flat):
                print(f"[table {ti}]")
                for r in rows[:14]:
                    print("   | " + " | ".join(c[:60] for c in r))
        print("--- 문장")
        seen = 0
        for p in soup.find_all(["p", "span", "td"]):
            if p.find("table"):
                continue
            txt = p.get_text(" ", strip=True)
            if 8 < len(txt) < 400 and KEYWORDS.search(txt):
                print("   * " + txt)
                seen += 1
                if seen >= 40:
                    break


if __name__ == "__main__":
    main()
