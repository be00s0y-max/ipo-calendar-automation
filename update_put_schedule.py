"""
공모주 캘린더 — 메자닌 조기상환(풋옵션) 일정 갱신 스크립트.

DART 발행결정 공시에는 조기상환청구권(풋옵션) 행사일 필드가 없다 — 이 정보는
예탁결제원(KSD) SEIBRO에만 있다. mezz_deals.json에 이미 잡혀있는 종목마다
SEIBRO 모바일 사이트에서 이름으로 검색해 채권 상세정보를 찾고, "조기상환
옵션행사 스케쥴" 표에서 오늘 이후 가장 가까운 PUT 행 하나만 가져와 그 종목
데이터에 덧붙인다(다음 풋 도래일만 — 그 뒤로도 매달/분기 반복되는 종목이 있어
전부 다 캘린더에 올리면 너무 붐빈다).

흐름(모바일 사이트의 실제 검색 절차를 그대로 따라감 — 공식 API가 아니라
세션 기반 화면 흐름이라 페이지 구조가 바뀌면 깨질 수 있음):
  1. GET /common/selectIssueList.do?searchNm=<회사명>&searchCode=11
     → "ISIN:종목명|ISIN:종목명|..." 형식으로 그 회사 채권 전체가 나온다.
  2. 종목명이 "{회사명}{회차}{종류}(...)" 패턴(공백 있을 수도 없을 수도)과
     일치하는 걸 정규식으로 골라 ISIN을 찾는다.
  3. GET /cnts/bond/selectDetailSearch.do?txt_sch=<종목명>&txt_code=<ISIN>
     → "조기상환 옵션행사 스케쥴" 표(유형/행사시작일/행사종료일/조기상환일)를
     파싱해서 오늘 이후 가장 가까운 PUT 행을 찾는다.

주의: 최근 발행결정된 지 얼마 안 된 종목은 아직 SEIBRO에 등록 전이라(보통
납입일 전후에 등록되는 것으로 보임) 1번 검색에서 아예 안 잡힐 수 있다 —
오류가 아니라 "아직 없음"으로 보고 건너뛴다.
"""
import json
import os
import re
import sys
import time
import datetime
import urllib.request
import urllib.parse
import http.cookiejar

SCRATCH = os.path.dirname(os.path.abspath(__file__))
MEZZ_DATA_PATH = os.path.join(SCRATCH, "mezz_deals.json")
HTML_PATH = os.path.join(SCRATCH, "ipo_calendar.html")

BASE = "https://m.seibro.or.kr"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))


def http_get(path, params=None, retries=3):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with _opener.open(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def find_isin(name, round_, kind):
    """회사명으로 SEIBRO 채권 검색 후, 회차·종류가 일치하는 종목의 ISIN을 찾는다."""
    body = http_get("/common/selectIssueList.do", {"searchNm": name, "searchCode": "11"})
    body = body.strip()
    if not body:
        return None
    pat = re.compile(rf"^{re.escape(name)}\s*{re.escape(round_)}{re.escape(kind)}(\(|$)")
    for entry in body.split("|"):
        if ":" not in entry:
            continue
        isin, bond_name = entry.split(":", 1)
        if pat.match(bond_name):
            return isin, bond_name
    return None


PUT_ROW_RE = re.compile(
    r'<td class="tc">([^<]*)</td>\s*'
    r'<td class="tc">([^<]*)</td>\s*'
    r'<td class="tc">([^<]*)</td>\s*'
    r'<td class="tc">([^<]*)</td>'
)


def slashdate_to_iso(s):
    s = s.strip()
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def next_put_window(isin, bond_name, today_iso):
    html = http_get("/cnts/bond/selectDetailSearch.do", {"txt_sch": bond_name, "txt_code": isin})
    m = re.search(r"조기상환 옵션행사 스케쥴.*?<tbody>(.*?)</tbody>", html, re.S)
    if not m:
        return None
    rows = []
    for typ, start, end, redeem in PUT_ROW_RE.findall(m.group(1)):
        if typ.strip().upper() != "PUT":
            continue
        start_iso = slashdate_to_iso(start)
        end_iso = slashdate_to_iso(end)
        redeem_iso = slashdate_to_iso(redeem)
        if not start_iso:
            continue
        rows.append((start_iso, end_iso, redeem_iso))
    upcoming = [r for r in rows if r[0] >= today_iso]
    if upcoming:
        return sorted(upcoming, key=lambda r: r[0])[0]
    return sorted(rows, key=lambda r: r[0])[-1] if rows else None


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def deals_to_js(deals):
    lines = ["const mezzDeals = ["]
    for d in deals:
        lines.append("  " + json.dumps(d, ensure_ascii=False) + ",")
    lines.append("];")
    return "\n".join(lines)


def replace_block(html, marker, new_body):
    start_re = re.compile(rf"// AUTO-GENERATED:{marker}:START.*?\n")
    end_re = re.compile(rf"\n// AUTO-GENERATED:{marker}:END")
    sm = start_re.search(html)
    em = end_re.search(html)
    if not sm or not em or em.start() < sm.end():
        raise RuntimeError(f"marker {marker} not found in HTML — 수동으로 마커를 다시 확인해야 함")
    return html[:sm.end()] + new_body + html[em.start():]


def regenerate_html(mezz_deals):
    html = open(HTML_PATH, encoding="utf-8").read()
    ordered = sorted(
        mezz_deals.values(),
        key=lambda d: d.get("sub_date") or d.get("board_date") or "",
    )
    html = replace_block(html, "MEZZ_DEALS", deals_to_js(ordered))
    open(HTML_PATH, "w", encoding="utf-8").write(html)


def main():
    today_iso = datetime.date.today().isoformat()
    mezz_deals = load_json(MEZZ_DATA_PATH, {})
    if not mezz_deals:
        print("mezz_deals.json이 비어있음 — 먼저 update_mezzanine_data.py를 실행해야 함.")
        sys.exit(3)

    changed = []
    not_found = []
    errors = []

    for key_id, dl in mezz_deals.items():
        name, round_, kind = dl.get("name"), str(dl.get("round") or ""), dl.get("kind")
        if not name or not round_ or not kind:
            continue
        try:
            found = find_isin(name, round_, kind)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name} {round_}회{kind}: 검색 실패 ({e})")
            continue
        if not found:
            not_found.append(f"{name} {round_}회{kind}")
            continue
        isin, bond_name = found
        try:
            window = next_put_window(isin, bond_name, today_iso)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name} {round_}회{kind}: 상세조회 실패 ({e})")
            continue
        time.sleep(0.5)  # SEIBRO에 너무 빠르게 연달아 요청하지 않도록

        if not window:
            # SEIBRO에 등록은 됐지만(=isin을 찾음) 풋옵션 행이 아예 없는 종목 —
            # "아직 미확인"과 구분해서 "확인했는데 풋옵션 자체가 없음"으로 기록.
            if "put_start" not in dl or dl.get("put_start") is not None:
                dl["put_start"] = dl["put_end"] = dl["put_redemption"] = None
                changed.append(f"{name} {round_}회{kind}: 풋옵션 없음(확인됨)")
            continue

        put_start, put_end, put_redeem = window
        if (dl.get("put_start"), dl.get("put_end"), dl.get("put_redemption")) != (put_start, put_end, put_redeem):
            dl["put_start"] = put_start
            dl["put_end"] = put_end
            dl["put_redemption"] = put_redeem
            changed.append(f"{name} {round_}회{kind}: 풋 {put_start}~{put_end} (상환 {put_redeem})")

    if changed:
        save_json(MEZZ_DATA_PATH, mezz_deals)
        regenerate_html(mezz_deals)

    print(f"확인 대상: {len(mezz_deals)}개 메자닌 종목")
    if changed:
        print(f"새로 반영됨 ({len(changed)}건):")
        for c in changed[:30]:
            print("  - " + c)
    else:
        print("새로운 변경 없음.")
    if not_found:
        print(f"SEIBRO 미등록(건너뜀) {len(not_found)}건: " + ", ".join(not_found[:10]) + (" ..." if len(not_found) > 10 else ""))
    if errors:
        print("오류:")
        for e in errors:
            print("  ! " + e)

    sys.exit(0 if changed else 3)


if __name__ == "__main__":
    main()
