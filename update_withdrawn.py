"""
공모 철회 확인 — 상장 전 딜마다 DART 공시 목록에 "철회신고서"가 올라왔는지 보고,
있으면 deals.json의 그 딜에 withdrawn(철회신고서 접수일, YYYY-MM-DD)을 붙인 뒤
ipo_calendar.html의 AUTO-GENERATED:DEALS 블록을 다시 만든다.

38커뮤니케이션은 철회한 딜을 한동안 옛 일정 그대로 목록에 둔다(2026-11-11 씨앤엠·제이앤티지:
DART에는 철회신고서가 올라왔는데 38 목록·상세페이지에는 "철회" 표시 없이 청약·납입 일정이 그대로였다).
그래서 철회 여부는 DART로 판단한다.

- DART 웹 공시검색(dart.fss.or.kr/dsab007/detailSearch.ax)을 쓴다. API 키가 필요 없다.
- 같은 회사가 예전에 다른 공모를 철회했던 기록과 헷갈리지 않도록, 가장 최근
  증권신고서(지분증권) 접수일 이후에 올라온 철회신고서만 이번 딜의 철회로 본다.
- 한 번 붙인 표시는 지우지 않는다(같은 이름으로 재추진하면 update_calendar_data.py가 새 일정으로 받는다).
- 실패해도 0으로 끝난다(다른 갱신을 막지 않게).
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from update_calendar_data import deals_to_js, replace_block, save_json

HERE = os.path.dirname(os.path.abspath(__file__))
DEALS_PATH = os.path.join(HERE, "deals.json")
CORP_PATH = os.path.join(HERE, "corp_codes.json")
HTML_PATH = os.path.join(HERE, "ipo_calendar.html")
SEARCH_URL = "https://dart.fss.or.kr/dsab007/detailSearch.ax"
LOOKBACK_DAYS = 180  # 수요예측 시작일 이 만큼 전부터 공시를 본다(최초 증권신고서가 들어가도록)


def search(corp_name, corp_code, start, end):
    """DART 공시검색 결과를 (보고서명, 접수일 YYYY-MM-DD) 목록으로 돌려준다(최신순)."""
    data = urllib.parse.urlencode({
        "currentPage": 1, "maxResults": 100, "textCrpNm": corp_name, "textCrpCik": corp_code,
        "startDate": start, "endDate": end, "sort": "date", "series": "desc", "finalReport": "recent",
    }).encode()
    req = urllib.request.Request(SEARCH_URL, data=data, headers={
        "User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://dart.fss.or.kr/dsab007/main.do",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    out = []
    for tr in BeautifulSoup(html, "html.parser").select("tbody tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) < 5:
            continue
        m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", tds[4])
        if m:
            out.append((tds[2], f"{m[1]}-{m[2]}-{m[3]}"))
    return out


def withdrawn_date(rows):
    """가장 최근 증권신고서(지분증권) 이후의 철회신고서 접수일. 없으면 None."""
    filings = [d for name, d in rows if "증권신고서" in name and "지분증권" in name]
    withdraws = [d for name, d in rows if name.startswith("철회신고서")]
    if not withdraws:
        return None
    last_w = max(withdraws)
    if filings and last_w < max(filings):
        return None
    return last_w


def main():
    deals = json.load(open(DEALS_PATH, encoding="utf-8"))
    corp = json.load(open(CORP_PATH, encoding="utf-8"))
    today = datetime.date.today()
    found = []
    for dl in deals:
        if dl.get("withdrawn") or (dl.get("list") and dl["list"] <= today.isoformat()):
            continue
        code = corp.get(dl["name"])
        if not code:
            continue
        start = datetime.date.fromisoformat(dl["demand"][0]) - datetime.timedelta(days=LOOKBACK_DAYS)
        corp_name = re.sub(r"\s*\(.*?\)\s*$", "", dl["name"])
        # DART가 연달아 물으면 가끔 빈 목록을 돌려준다(2026-11-11 실측: 같은 종목이 실행마다 0건/5건).
        # 상장 전 딜이면 증권신고서가 적어도 하나는 있어야 하므로, 빈 목록이면 쉬었다가 다시 묻는다.
        rows = []
        for attempt in range(4):
            try:
                rows = search(corp_name, code, start.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
            except Exception as e:  # noqa: BLE001 — 한 종목 실패로 전체를 멈추지 않는다
                print(f"DART 공시검색 실패: {dl['name']}: {e}")
            if rows:
                break
            time.sleep(3 * (attempt + 1))
        if not rows:
            print(f"DART 공시 목록을 못 받음: {dl['name']}")
            continue
        wd = withdrawn_date(rows)
        if wd:
            dl["withdrawn"] = wd
            found.append(f"{dl['name']}({wd})")
        time.sleep(1.5)
    if not found:
        print("철회 없음")
        return
    save_json(DEALS_PATH, deals)
    html = open(HTML_PATH, encoding="utf-8").read()
    open(HTML_PATH, "w", encoding="utf-8").write(replace_block(html, "DEALS", deals_to_js(deals)))
    print("공모 철회:", ", ".join(found))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"철회 확인 실패: {e}")
    sys.exit(0)
