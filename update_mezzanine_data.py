"""
공모주 캘린더 — 메자닌(CB/BW/EB) 데이터 갱신 스크립트.

IPO 파이프라인과 달리 정해진 종목 리스트(corp_codes.json)가 없다 — 메자닌은
"이미 상장된 아무 회사"나 발행할 수 있어서, 매 실행마다 DART 공시 전체를
시장 전역으로 훑어 새로 나온 발행결정을 찾는다.

실행할 때마다:
  1. DART list.json을 corp_code 없이(=전체 상장사 대상) pblntf_ty=B(주요사항보고)로
     최근 며칠치 조회하고, report_nm이 "전환사채권발행결정"/"신주인수권부사채권
     발행결정"/"교환사채권발행결정"인 건만 골라낸다("[기재정정]" 접두는 정정
     공시라 함께 잡되, 아래 4번에서 같은 키로 덮어써서 최신 내용으로 갱신된다).
  2. 그렇게 찾은 (corp_code, 종류) 조합마다 DART의 종류별 구조화 API
     (cvbdIsDecsn/bdwtIsDecsn/exbdIsDecsn)를 호출해 발행조건 전체를 가져온다 —
     IPO 쪽처럼 첨부 XML을 직접 파싱할 필요 없이 이미 구조화된 JSON이라 더 쉽다.
  3. mezz_deals.json(소스 오브 트루스)을 갱신하고, ipo_calendar.html의
     AUTO-GENERATED:MEZZ_DEALS 마커 사이 블록을 다시 쓴다.
  4. 뭐가 바뀌었는지 표준출력에 요약하고, 하나라도 바뀌었으면 종료코드 0,
     아무 변화도 없었으면 종료코드 3으로 끝낸다.

주의: DART에는 조기상환청구권(풋옵션) 행사일 필드가 없다(예탁원 채권권리행사
정보 쪽 데이터라 별도 소스 필요) — 여기서는 발행 시점에 정해지는 조건
(만기·표면금리·전환/행사/교환가액·전환청구기간 등)까지만 다룬다.
"""
import json
import os
import re
import sys
import time
import datetime
import urllib.request
import urllib.error

SCRATCH = os.path.dirname(os.path.abspath(__file__))
MEZZ_DATA_PATH = os.path.join(SCRATCH, "mezz_deals.json")
HTML_PATH = os.path.join(SCRATCH, "ipo_calendar.html")

# 시장 전역 스캔 시 며칠치를 볼지 — 하루 2번(평일) 도는 걸 감안하면 이틀이면
# 충분하지만, 주말·공휴일 사이 공백과 API/네트워크 일시 실패를 감안해 넉넉히 잡는다.
SCAN_DAYS = 6

REPORT_PATTERNS = {
    "CB": re.compile(r"전환사채권발행결정"),
    "BW": re.compile(r"신주인수권부사채권발행결정"),
    "EB": re.compile(r"교환사채권발행결정"),
}
ENDPOINT = {
    "CB": "cvbdIsDecsn",
    "BW": "bdwtIsDecsn",
    "EB": "exbdIsDecsn",
}


def load_key():
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY 환경변수가 없음 — 클라우드 환경에 시크릿으로 등록되어 있는지 확인할 것")
    return key


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def api_get(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def scan_market(key):
    """최근 SCAN_DAYS일 동안의 주요사항보고서 전체를 훑어, CB/BW/EB 발행결정
    공시를 낸 (corp_code, 종류) 조합을 찾는다. 반환: {(corp_code, kind): corp_name}"""
    end = datetime.date.today()
    begin = end - datetime.timedelta(days=SCAN_DAYS)
    found = {}
    page = 1
    while True:
        url = (
            "https://opendart.fss.or.kr/api/list.json"
            f"?crtfc_key={key}&bgn_de={begin:%Y%m%d}&end_de={end:%Y%m%d}"
            f"&pblntf_ty=B&page_count=100&page_no={page}"
        )
        data = api_get(url)
        if data.get("status") == "013":  # 조회된 데이터 없음
            break
        if data.get("status") != "000":
            raise RuntimeError(f"DART list.json 오류: {data.get('status')} {data.get('message')}")
        for item in data.get("list", []):
            report_nm = item.get("report_nm", "")
            for kind, pat in REPORT_PATTERNS.items():
                if pat.search(report_nm):
                    found[(item["corp_code"], kind)] = item.get("corp_name", "")
        total_page = data.get("total_page", 1)
        if page >= total_page:
            break
        page += 1
    return found, begin, end


def kdate(s):
    """'2026년 08월 21일' -> '2026-08-21'. 빈 값/'-'는 그대로 둔다."""
    if not s or s == "-":
        return None
    m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", s)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def num(s):
    if not s or s == "-":
        return None
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None


def fetch_kind(key, corp_code, kind, begin, end):
    url = (
        f"https://opendart.fss.or.kr/api/{ENDPOINT[kind]}.json"
        f"?crtfc_key={key}&corp_code={corp_code}&bgn_de={begin:%Y%m%d}&end_de={end:%Y%m%d}"
    )
    data = api_get(url)
    if data.get("status") == "013":
        return []
    if data.get("status") != "000":
        raise RuntimeError(f"DART {ENDPOINT[kind]} 오류: {data.get('status')} {data.get('message')}")
    return data.get("list", [])


def to_entry(raw, kind, corp_name):
    # CB/EB는 cv_*/ex_* 필드명이 다르지만 의미(전환·교환 비율/가액/청구기간)는
    # 같아서 하나의 공통 필드로 합쳐 저장한다. BW(신주인수권부사채)는 사채 자체는
    # 만기 상환되고 신주인수권만 별도 행사되는 구조라 ex_rt/ex_prc/expd_* 필드를 쓴다.
    if kind == "CB":
        conv_rate, conv_price = raw.get("cv_rt"), raw.get("cv_prc")
        conv_bgd, conv_edd = raw.get("cvrqpd_bgd"), raw.get("cvrqpd_edd")
    elif kind == "EB":
        conv_rate, conv_price = raw.get("ex_rt"), raw.get("ex_prc")
        conv_bgd, conv_edd = raw.get("exrqpd_bgd"), raw.get("exrqpd_edd")
    else:  # BW
        conv_rate, conv_price = raw.get("ex_rt"), raw.get("ex_prc")
        conv_bgd, conv_edd = raw.get("expd_bgd"), raw.get("expd_edd")

    return {
        "name": corp_name,
        "kind": kind,
        "round": raw.get("bd_tm"),
        "bondName": raw.get("bd_knd"),
        "amount": num(raw.get("bd_fta")),
        "board_date": kdate(raw.get("bddd")),
        "sub_date": kdate(raw.get("sbd")),
        "pay_date": kdate(raw.get("pymd")),
        "maturity_date": kdate(raw.get("bd_mtd")),
        "coupon_rate": raw.get("bd_intr_ex"),
        "ytm_rate": raw.get("bd_intr_sf"),
        "method": raw.get("bdis_mthn"),
        "underwriter": raw.get("rpmcmp") if raw.get("rpmcmp") not in (None, "-") else None,
        "conv_rate": conv_rate,
        "conv_price": num(conv_price) if conv_price else None,
        "conv_period_start": kdate(conv_bgd),
        "conv_period_end": kdate(conv_edd),
        "refixing": raw.get("rs_sm_atn"),
        "rcept_no": raw.get("rcept_no"),
    }


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
    key = load_key()
    mezz_deals = load_json(MEZZ_DATA_PATH, {})

    try:
        found, scan_begin, scan_end = scan_market(key)
    except Exception as e:  # noqa: BLE001
        print(f"시장 전역 조회 실패: {e}")
        sys.exit(1)

    changed = []
    errors = []
    for (corp_code, kind), corp_name in found.items():
        try:
            raws = fetch_kind(key, corp_code, kind, scan_begin, scan_end)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{corp_name}({kind}): 상세조회 실패 ({e})")
            continue
        for raw in raws:
            entry = to_entry(raw, kind, corp_name)
            key_id = f"{corp_code}_{kind}_{entry['round']}"
            prev = mezz_deals.get(key_id)
            if prev != entry:
                mezz_deals[key_id] = entry
                changed.append(f"{corp_name} {entry['round']}회 {kind} (발행 {entry['amount']:,}원)" if entry["amount"] else f"{corp_name} {entry['round']}회 {kind}")

    if changed:
        save_json(MEZZ_DATA_PATH, mezz_deals)
        regenerate_html(mezz_deals)

    print(f"확인 대상: 최근 {SCAN_DAYS}일 시장 전역 (CB/BW/EB 발행결정 {len(found)}건 발견)")
    if changed:
        print(f"새로 반영됨 ({len(changed)}건):")
        for c in changed[:30]:
            print("  - " + c)
        if len(changed) > 30:
            print(f"  ... 외 {len(changed) - 30}건")
    else:
        print("새로운 변경 없음.")
    if errors:
        print("오류:")
        for e in errors:
            print("  ! " + e)

    sys.exit(0 if changed else 3)


if __name__ == "__main__":
    main()
