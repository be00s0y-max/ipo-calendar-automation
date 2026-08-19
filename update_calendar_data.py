"""
공모주 캘린더 — 38커뮤니케이션에서 딜 목록(수요예측일/청약일/납입일/상장일/
공모금액/주간사)을 다시 긁어와 ipo_calendar.html의 deals 배열을 갱신한다.

DART 스크립트(update_dart_data.py)와 역할이 다르다: 그건 이미 아는 종목의
"상세정보"를 채우는 것이고, 이건 "종목 자체의 목록·일정"을 최신화하는 것 —
새 종목이 파이프라인에 들어오거나, 기존 종목의 일정이 밀리는 걸 잡아낸다.

동작:
  1. https://www.38.co.kr/html/fund/?o=r (수요예측일정 목록) 1페이지를 긁어서
     종목명·수요예측일·공모금액·주간사·상세페이지 링크(no=)를 얻는다.
  2. 종목별 상세페이지(?o=v&no=N)에서 공모청약일·납입일·상장일을 보충한다.
  3. deals.json(소스 오브 트루스)과 비교해서 새 종목/일정 변경을 찾아낸다.
  4. 바뀐 게 있으면 deals.json을 갱신하고 ipo_calendar.html의
     AUTO-GENERATED:DEALS 블록을 재생성한다.

38.co.kr은 구형 TLS 설정을 쓰는지 기본 SSL 컨텍스트로 접속하면 handshake
실패가 나서, 암호화 수준을 낮춘 컨텍스트를 쓴다.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

SCRATCH = os.path.dirname(os.path.abspath(__file__))
DEALS_PATH = os.path.join(SCRATCH, "deals.json")
HTML_PATH = os.path.join(SCRATCH, "ipo_calendar.html")

LIST_URL = "https://www.38.co.kr/html/fund/?o=r"
DETAIL_URL = "https://www.38.co.kr/html/fund/?o=v&no={no}&l=&page=1"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.set_ciphers("DEFAULT:@SECLEVEL=1")
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                return resp.read().decode("euc-kr", errors="ignore")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def table_rows(t):
    grid = []
    for tr in t.find_all("tr"):
        grid.append([c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])])
    return grid


def dot_to_dash(s):
    """'2026.08.12' -> '2026-08-12'"""
    s = s.strip()
    m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", s)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def parse_list_page():
    """수요예측일정 목록에서 종목명/수요예측일/공모금액/주간사/no(상세페이지ID)를 뽑는다."""
    html = fetch(LIST_URL)
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        first = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
        if len(first) == 6 and first[0].strip() == "종목명":
            target = t
            break
    if target is None:
        raise RuntimeError("38 수요예측일정 목록 표를 찾지 못함 — 페이지 구조가 바뀌었을 수 있음")

    out = []
    for tr in target.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) != 6:
            continue
        name_cell = cells[0]
        name = name_cell.get_text(" ", strip=True)
        if not name:
            continue
        link = name_cell.find("a")
        href = link.get("href") if link else None
        no_match = re.search(r"no=(\d+)", href) if href else None
        if not no_match:
            continue
        demand_raw = cells[1].get_text(" ", strip=True)  # "2026.09.08~09.14"
        m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s*~\s*(\d{1,2})\.(\d{1,2})", demand_raw)
        if not m:
            continue
        y, m1, d1, m2, d2 = m.groups()
        demand = [f"{y}-{int(m1):02d}-{int(d1):02d}", f"{y}-{int(m2):02d}-{int(d2):02d}"]
        deal_size_raw = cells[4].get_text(" ", strip=True).replace(",", "")
        deal_size_m = int(deal_size_raw) if deal_size_raw.isdigit() else None
        uw = cells[5].get_text(" ", strip=True)
        out.append({"name": name, "no": no_match.group(1), "demand": demand, "dealSizeM": deal_size_m, "uw": uw})
    return out


def parse_detail_schedule(no):
    """종목 상세페이지에서 공모청약일/납입일/상장일을 뽑는다.

    회사마다 '주요일정' 표의 중첩 구조·행 순서가 달라서(예: NH스팩34호는
    실제 일정표 말고도 사이드바/관련기사 표에 "수요예측일" 등 같은 라벨
    텍스트가 재사용되면서 엉뚱한 값과 매칭됨. rows[:6] 위치 인덱싱은 물론,
    "첫 번째로 매칭되는 표"를 쓰는 방식도 신뢰할 수 없음을 실측으로 확인함)
    — 대신 "주요일정"을 포함한 표 후보를 텍스트 길이 오름차순(가장 작고
    깔끔한 표부터)으로 보면서, 라벨 매칭 + 수요예측일 값이 실제 날짜
    형태(YYYY.MM.DD)인지까지 검증해 진짜 일정표를 고른다.
    """
    html = fetch(DETAIL_URL.format(no=no))
    soup = BeautifulSoup(html, "html.parser")

    labels = ["수요예측일", "공모청약일", "배정공고일", "납입일", "환불일", "상장일"]
    candidates = [t for t in soup.find_all("table") if "주요일정" in t.get_text(" ", strip=True)]
    candidates.sort(key=lambda t: len(t.get_text(" ", strip=True)))

    sched = {}
    for t in candidates:
        cur = {}
        for row in table_rows(t):
            if len(row) < 2:
                continue
            for cell in row[:-1]:
                cell = cell.strip()
                for lab in labels:
                    if lab not in cur and (cell == lab or cell.startswith(lab)):
                        cur[lab] = row[-1]
                        break
        if cur.get("수요예측일") and re.search(r"\d{4}\.\d{1,2}\.\d{1,2}", cur["수요예측일"]):
            sched = cur
            break

    sub = None
    if sched.get("공모청약일"):
        m = re.findall(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", sched["공모청약일"])
        if len(m) >= 2:
            sub = [f"{m[0][0]}-{int(m[0][1]):02d}-{int(m[0][2]):02d}", f"{m[1][0]}-{int(m[1][1]):02d}-{int(m[1][2]):02d}"]
        elif len(m) == 1:
            d = f"{m[0][0]}-{int(m[0][1]):02d}-{int(m[0][2]):02d}"
            sub = [d, d]

    pay = dot_to_dash(sched["납입일"]) if sched.get("납입일") else None

    list_date = None
    if sched.get("상장일"):
        list_date = dot_to_dash(sched["상장일"])

    return {"sub": sub, "pay": pay, "list": list_date}


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def deals_to_js(deals):
    lines = ["const deals = ["]
    for d in deals:
        list_val = f'"{d["list"]}"' if d.get("list") else "null"
        pay_val = f'"{d["pay"]}"' if d.get("pay") else "null"
        sub_val = json.dumps(d["sub"]) if d.get("sub") else "null"
        size_val = d["dealSizeM"] if d.get("dealSizeM") is not None else "null"
        lines.append(
            f'  {{ name: "{d["name"]}", uw: "{d["uw"]}", '
            f'demand: ["{d["demand"][0]}","{d["demand"][1]}"], '
            f'sub: {sub_val}, '
            f'pay: {pay_val}, '
            f'list: {list_val}, dealSizeM: {size_val} }},'
        )
    lines.append("];")
    return "\n".join(lines)


def replace_block(html, marker, new_body):
    start_re = re.compile(rf"// AUTO-GENERATED:{marker}:START.*?\n")
    end_re = re.compile(rf"\n// AUTO-GENERATED:{marker}:END")
    sm = start_re.search(html)
    em = end_re.search(html)
    if not sm or not em or em.start() < sm.end():
        raise RuntimeError(f"marker {marker} not found in HTML")
    return html[:sm.end()] + new_body + html[em.start():]


def main():
    listed = parse_list_page()
    scraped = []
    for item in listed:
        sched = parse_detail_schedule(item["no"])
        scraped.append({
            "name": item["name"],
            "uw": item["uw"],
            "demand": item["demand"],
            "sub": sched["sub"],
            "pay": sched["pay"],
            "list": sched["list"],
            "dealSizeM": item["dealSizeM"],
        })

    old_deals = load_json(DEALS_PATH, [])
    # 병합이지 교체가 아니다 — 38 목록 1페이지엔 최근 20~30건만 보이고 오래된
    # 완료 딜은 다음 페이지로 밀려난다. 그렇다고 캘린더에서 그 딜을 지우면
    # 락업해제 추적(상장 후 최대 6개월)이 끊기므로, 기존에 이미 알고 있던
    # 딜은 계속 갖고 있고 "1페이지에 보이는 딜"만 최신 값으로 덮어쓴다.
    merged = {d["name"]: d for d in old_deals}

    new_names = []
    changed = []
    for d in scraped:
        prev = merged.get(d["name"])
        if prev is None:
            new_names.append(d["name"])
        elif (prev.get("demand") != d["demand"] or prev.get("sub") != d["sub"]
              or prev.get("pay") != d["pay"] or prev.get("list") != d["list"]):
            changed.append(d["name"])
        merged[d["name"]] = d

    if not new_names and not changed:
        print(f"확인 대상: {len(scraped)}개 종목 (전체 보유 {len(merged)}개)")
        print("변경 없음 (신규 종목 없고 일정 변경도 없음).")
        sys.exit(3)

    # 날짜 오름차순으로 정렬해서 캘린더가 보던 순서와 맞춘다
    all_deals = sorted(merged.values(), key=lambda d: d["demand"][0])

    save_json(DEALS_PATH, all_deals)
    html = open(HTML_PATH, encoding="utf-8").read()
    html = replace_block(html, "DEALS", deals_to_js(all_deals))
    open(HTML_PATH, "w", encoding="utf-8").write(html)

    print(f"확인 대상: {len(scraped)}개 종목 (전체 보유 {len(all_deals)}개)")
    if new_names:
        print("신규 종목:", ", ".join(new_names))
    if changed:
        print("일정 변경:", ", ".join(changed))
    sys.exit(0)


if __name__ == "__main__":
    main()
