"""
공모주 캘린더 — DART 데이터 주기 갱신 스크립트.

실행할 때마다:
  1. corp_codes.json에 있는 종목들 중 아직 확정 신고서를 못 찾은 종목의 corp_code로
     DART list.json을 조회해 [발행조건확정]증권신고서(지분증권/증권예탁증권)가
     새로 나왔는지 확인한다.
  2. 새로 나온 신고서가 있으면 document.xml(ZIP)을 받아 압축을 풀고, 이미 검증된
     dart_parser.parse_filing()으로 파싱한다. 그 종목의 company.json(기업개요)도
     같이 받아온다.
  3. dartData.json / company_profiles.json / filing_rcept.json(소스 오브 트루스)을
     갱신하고, ipo_calendar.html 안의 AUTO-GENERATED 마커 사이 블록을 다시 써서
     실제 페이지에 반영한다.
  4. 뭐가 바뀌었는지 표준출력에 요약하고, 하나라도 바뀌었으면 종료코드 0,
     아무 변화도 없었으면 종료코드 3으로 끝낸다(예약 작업이 "재게시 필요 없음"을
     구분할 수 있게).

corp_codes.json은 9/29에 손으로 만든 19개 종목에서 늘어나지 않아, 그 뒤 새로 생긴
딜은 DART 조회 대상에서 빠져 있었다(확정공모가 등이 계속 비어 있던 원인). 그래서
1번 전에 deals.json에 있는데 corp_code가 없는 종목을 DART 발행공시 목록에서 회사명으로
찾아 corp_codes.json에 자동으로 채운다(discover_corp_codes). 이름이 정확히 맞는 것만
넣고, 못 찾은 종목(스팩처럼 DART 정식 회사명이 다른 경우 등)은 표준출력에 남긴다.

주의: 이미 만들어진 필드(예: 이미 dartData.json에 있는 7개 종목)는 재확인하지
않는다 — [발행조건확정]은 종목당 보통 한 번만 나오는 최종 문서라 재조회가
불필요하고, 매번 전체를 다시 긁으면 API 호출이 낭비된다.
"""
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
import io

SCRATCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRATCH)
from dart_parser import parse_filing, parse_demand_terms, parse_overview  # noqa: E402

CORP_CODES_PATH = os.path.join(SCRATCH, "corp_codes.json")
DEALS_PATH = os.path.join(SCRATCH, "deals.json")
DART_DATA_PATH = os.path.join(SCRATCH, "dartData.json")
PROFILES_PATH = os.path.join(SCRATCH, "company_profiles.json")
RCEPT_PATH = os.path.join(SCRATCH, "filing_rcept.json")
# 수요예측 참여 조건(희망공모가 밴드·수량/가격 단위·기관 최고한도) — 수요예측 전에
# 필요한 값이라 [발행조건확정]이 아니라 그때그때 최신 지분증권 신고서(정정 포함)에서 뽑는다.
DEMAND_INFO_PATH = os.path.join(SCRATCH, "demandInfo.json")
HTML_PATH = os.path.join(SCRATCH, "ipo_calendar.html")

FILING_NAME_RE = re.compile(r"\[발행조건확정\]증권신고서\((지분증권|증권예탁증권)\)")
# 최초 신고서·정정·발행조건확정 모두 이 이름을 포함한다 — 회사명→corp_code 찾기용.
EQUITY_FILING_RE = re.compile(r"증권신고서\((지분증권|증권예탁증권)\)")
# corp_code 없이 list.json을 부르면 DART가 조회 기간을 3개월로 제한해서 그 안으로 잡는다.
DISCOVER_DAYS = 85


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
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def find_new_filing(key, corp_code):
    """corp_code 기준 전체 공시 목록에서 [발행조건확정]증권신고서를 찾는다.
    corp_code로 조회하면 3개월 제한이 없어(전수 조회) 날짜 범위를 넉넉히 잡는다."""
    url = (
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={key}&corp_code={corp_code}&bgn_de=20260101&end_de=20261231"
        "&page_count=100"
    )
    data = json.loads(api_get(url).decode("utf-8"))
    if data.get("status") != "000":
        return None
    for item in data.get("list", []):
        if FILING_NAME_RE.search(item.get("report_nm", "")):
            return item["rcept_no"], item["report_nm"]
    return None


def normalize_corp_name(name):
    """'멜콘(구.에스앤에프솔루션)' / '(주)멜콘' / '주식회사 멜콘' → '멜콘'."""
    name = re.sub(r"\(\s*구\s*[.)]?[^)]*\)", "", name or "")
    name = name.replace("(주)", "").replace("㈜", "").replace("주식회사", "")
    return re.sub(r"\s+", "", name).lower()


# 스팩은 38과 DART 이름이 다르다(2026-10 DART 실측):
#   "SK증권스팩14호" ↔ "에스케이증권제14호기업인수목적", "KB스팩34호" ↔ "KB제34호스팩",
#   "아이엠스팩1호" ↔ "아이엠기업인수목적1호", "한화플러스스팩6호" ↔ "한화플러스제6호기업인수목적"
# 그래서 (증권사, 회차)로 맞춘다. 증권사는 한글 표기를 영문 약칭으로 모은다.
SPAC_RE = re.compile(r"스팩|기업인수목적")
SPAC_BRAND_ALIAS = {"에스케이": "sk", "엔에이치": "nh", "케이비": "kb", "아이엠": "im", "디비": "db", "아이비케이": "ibk"}


def spac_key(name):
    """스팩이면 (증권사, 회차), 아니면 None."""
    name = name or ""
    if not SPAC_RE.search(name):
        return None
    m = re.search(r"(\d+)\s*호", name)
    if not m:
        return None
    brand = re.sub(r"\(\s*구\s*[.)]?[^)]*\)", "", name)
    brand = re.sub(r"제?\s*\d+\s*호|스팩|기업인수목적|주식회사|\(주\)|㈜|\s", "", brand)
    brand = re.sub(r"(금융투자|투자증권|증권)$", "", brand).lower()
    return (SPAC_BRAND_ALIAS.get(brand, brand), int(m.group(1)))


def discover_corp_codes(key, corp_codes):
    """deals.json에 있지만 corp_codes.json에 없는 종목의 corp_code를, 최근
    DISCOVER_DAYS일 발행공시(pblntf_ty=C) 중 지분증권 증권신고서를 낸 회사명과
    대조해 찾는다. 같은 이름에 corp_code가 둘 이상이면 넣지 않는다.
    반환: (새로 찾은 {종목명: corp_code}, 못 찾은 종목명 목록)"""
    import datetime
    deals = load_json(DEALS_PATH, [])
    missing = [dl["name"] for dl in deals if dl["name"] not in corp_codes]
    if not missing:
        return {}, []
    end = datetime.date.today()
    begin = end - datetime.timedelta(days=DISCOVER_DAYS)
    by_name = {}
    by_spac = {}
    page = 1
    while True:
        url = (
            "https://opendart.fss.or.kr/api/list.json"
            f"?crtfc_key={key}&bgn_de={begin:%Y%m%d}&end_de={end:%Y%m%d}"
            f"&pblntf_ty=C&page_count=100&page_no={page}"
        )
        data = json.loads(api_get(url).decode("utf-8"))
        if data.get("status") == "013":  # 조회된 데이터 없음
            break
        if data.get("status") != "000":
            raise RuntimeError(f"DART list.json 오류: {data.get('status')} {data.get('message')}")
        for item in data.get("list", []):
            if EQUITY_FILING_RE.search(item.get("report_nm", "")):
                by_name.setdefault(normalize_corp_name(item.get("corp_name")), set()).add(item["corp_code"])
                sk = spac_key(item.get("corp_name"))
                if sk:
                    by_spac.setdefault(sk, set()).add(item["corp_code"])
        if page >= data.get("total_page", 1):
            break
        page += 1
    found, unmatched = {}, []
    for name in missing:
        sk = spac_key(name)
        codes = by_spac.get(sk, set()) if sk else by_name.get(normalize_corp_name(name), set())
        if len(codes) == 1:
            found[name] = next(iter(codes))
        else:
            unmatched.append(name)
    return found, unmatched


def list_equity_filings(key, corp_code):
    """corp_code의 지분증권 증권신고서(최초·정정·발행조건확정) 목록, 최신순."""
    url = (
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={key}&corp_code={corp_code}&bgn_de=20260101&end_de=20261231"
        "&page_count=100"
    )
    data = json.loads(api_get(url).decode("utf-8"))
    if data.get("status") != "000":
        return []
    items = [it for it in data.get("list", []) if EQUITY_FILING_RE.search(it.get("report_nm", ""))]
    return sorted(items, key=lambda it: it["rcept_no"], reverse=True)  # 접수번호는 날짜+일련번호라 클수록 최신


TERM_FIELDS = ("band", "qty_unit", "price_unit", "inst_max")
# 공모개요(캘린더 종목 패널): 주관사 밸류에이션·상장예정주식수·사업의 개요 첫 문장. 스팩은 해당 없음.
# 신고서 하나 읽는 데 30초가량 걸려서, 최신 신고서마다 한 번만 찾고(ov_rcept) 수요예측 시작이
# 60일 넘게 지난 딜은 건너뛴다. 못 찾은 항목은 다음 신고서(정정 등)가 올라올 때 다시 본다.
OVERVIEW_FIELDS = ("val_model", "val_multiple", "val_fair", "val_discount", "listed_shares", "biz")
OVERVIEW_RECENT_DAYS = 60
MAX_TERM_DOCS = 4  # 최신 신고서에 없는 항목은 이전 신고서에서 이만큼까지 거슬러 찾는다


def update_demand_info(key, corp_codes, demand_info, changed, errors):
    """모든 딜의 수요예측 참여 조건을 최신 지분증권 신고서에서 채운다.
    - 최신 신고서가 바뀌었거나, 지난번에 못 채운 항목이 있으면 다시 본다.
    - 최신 신고서에 없는 항목(예: 진코스텍 발행조건확정은 바뀐 부분만 담은 31KB짜리)은
      이전 신고서들에서 빈 항목만 채운다.
    - 스팩은 공모가가 2,000원으로 고정이라 밴드를 못 찾으면 2,000원으로 둔다."""
    for dl in load_json(DEALS_PATH, []):
        name = dl["name"]
        cc = corp_codes.get(name)
        if not cc:
            continue
        prev = demand_info.get(name) or {}
        try:
            filings = list_equity_filings(key, cc)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: 참여조건용 신고서 목록 조회 실패 ({e})")
            continue
        if not filings:
            continue
        recent = dl["demand"][0] >= (time.strftime("%Y-%m-%d", time.localtime(time.time() - OVERVIEW_RECENT_DAYS * 86400)))
        want_ov = recent and not spac_key(name) and prev.get("ov_rcept") != filings[0]["rcept_no"]
        fields = TERM_FIELDS + OVERVIEW_FIELDS if want_ov else TERM_FIELDS
        complete = all(prev.get(f) is not None for f in TERM_FIELDS)
        if complete and not want_ov and prev.get("rcept_no") == filings[0]["rcept_no"]:
            continue
        terms = {f: None for f in fields}
        failed = []
        for it in filings[:MAX_TERM_DOCS]:
            # 한 문서가 안 받아져도(DART가 ZIP 대신 오류 응답을 주는 경우) 다음 신고서로 넘어간다
            try:
                tmp_path = os.path.join(SCRATCH, f"_tmp_terms_{cc}.xml")
                fetch_filing_xml(key, it["rcept_no"], tmp_path)
                got = parse_demand_terms(tmp_path)
                if want_ov:
                    got.update(parse_overview(tmp_path))
                os.remove(tmp_path)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{it['rcept_no']}({e})")
                continue
            for f in fields:
                if terms[f] is None and got.get(f) is not None:
                    terms[f] = got[f]
            if all(terms[f] is not None for f in fields):
                break
        if failed:
            errors.append(f"{name}: 참여조건 신고서 일부 못 받음 — " + ", ".join(failed))
        for f in fields:  # 이번에 못 받은 항목은 지난번 값을 지킨다
            if terms[f] is None and prev.get(f) is not None:
                terms[f] = prev[f]
        if terms["band"] is None and spac_key(name):
            terms["band"] = [2000, 2000]
        for k, v in prev.items():  # 이번에 안 본 항목(공모개요 등)은 그대로 둔다
            terms.setdefault(k, v)
        if want_ov and not failed:
            terms["ov_rcept"] = filings[0]["rcept_no"]
        terms.update({"rcept_no": filings[0]["rcept_no"], "report_nm": filings[0]["report_nm"]})
        if terms != prev:
            demand_info[name] = terms
            missing = [f for f in TERM_FIELDS if terms[f] is None]
            changed.append(f"{name} 수요예측 조건 ({filings[0]['report_nm']})" + (f" — 못 찾음: {', '.join(missing)}" if missing else ""))


def fetch_filing_xml(key, rcept_no, tmp_path):
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={key}&rcept_no={rcept_no}"
    raw = api_get(url)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        xml_name = next((n for n in names if n.lower().endswith((".xml", ".htm", ".html"))), names[0])
        content = zf.read(xml_name)
    open(tmp_path, "wb").write(content)


def fetch_company_profile(key, corp_code):
    url = f"https://opendart.fss.or.kr/api/company.json?crtfc_key={key}&corp_code={corp_code}"
    data = json.loads(api_get(url).decode("utf-8"))
    if data.get("status") != "000":
        return None
    return {
        "stock_code": data.get("stock_code", "").strip(),
        "ceo_nm": data.get("ceo_nm", ""),
        "corp_cls": data.get("corp_cls", ""),
        "adres": data.get("adres", ""),
        "hm_url": data.get("hm_url", ""),
        "phn_no": data.get("phn_no", ""),
        "est_dt": data.get("est_dt", ""),
    }


def js_str(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def replace_block(html, marker, new_body):
    # START 마커 줄 뒤에 다른 텍스트가 붙어있을 수 있어(같은 줄에 설명이 이어지는
    # 경우), "그 줄이 끝나는 지점"부터 END 마커 직전까지를 교체 대상으로 삼는다.
    start_re = re.compile(rf"// AUTO-GENERATED:{marker}:START.*?\n")
    end_re = re.compile(rf"\n// AUTO-GENERATED:{marker}:END")
    sm = start_re.search(html)
    em = end_re.search(html)
    if not sm or not em or em.start() < sm.end():
        raise RuntimeError(f"marker {marker} not found in HTML — 수동으로 마커를 다시 확인해야 함")
    return html[:sm.end()] + new_body + html[em.start():]


def regenerate_html(dart_data, profiles, rcept, demand_info):
    html = open(HTML_PATH, encoding="utf-8").read()

    dart_body = "const dartData = " + js_str(dart_data) + ";"
    html = replace_block(html, "DART_DATA", dart_body)

    filing_urls = {name: f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}" for name, rc in rcept.items()}
    filing_body = "const DART_FILING_URL = " + js_str(filing_urls) + ";"
    html = replace_block(html, "FILING_URL", filing_body)

    profile_body = "const COMPANY_PROFILE = " + js_str(profiles) + ";"
    html = replace_block(html, "COMPANY_PROFILE", profile_body)

    demand_body = "const DEMAND_INFO = " + js_str(demand_info) + ";"
    html = replace_block(html, "DEMAND_INFO", demand_body)

    open(HTML_PATH, "w", encoding="utf-8").write(html)


# 2026년 대한민국 법정공휴일(대체공휴일 포함) — ipo_calendar.html의 KR_HOLIDAYS_2026과
# 동일한 목록. 예약 작업의 cron 자체는 평일(월~금)만 걸어두지만, 공휴일이 평일에
# 걸리는 경우(예: 8/17 광복절 대체공휴일)까지 거르려면 여기서 한 번 더 확인해야 한다.
KR_HOLIDAYS_2026 = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01", "2026-03-02",
    "2026-05-05", "2026-05-24", "2026-05-25", "2026-06-06", "2026-08-15", "2026-08-17",
    "2026-09-24", "2026-09-25", "2026-09-26", "2026-10-03", "2026-10-05", "2026-10-09",
    "2026-12-25",
}


def is_kr_business_day():
    import datetime
    # KST(UTC+9) 기준 오늘 — 클라우드 실행 환경 시간대에 의존하지 않는다.
    now_kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    if now_kst.weekday() >= 5:  # 5=토, 6=일
        return False, now_kst.date().isoformat()
    return now_kst.date().isoformat() not in KR_HOLIDAYS_2026, now_kst.date().isoformat()


def main():
    is_biz, today_str = is_kr_business_day()
    if not is_biz:
        print(f"{today_str}은 영업일이 아님(주말/공휴일) — 조회 건너뜀.")
        sys.exit(3)

    key = load_key()
    corp_codes = load_json(CORP_CODES_PATH, {})
    dart_data = load_json(DART_DATA_PATH, {})
    profiles = load_json(PROFILES_PATH, {})
    rcept = load_json(RCEPT_PATH, {})
    demand_info = load_json(DEMAND_INFO_PATH, {})

    discovered, unmatched = {}, []
    try:
        discovered, unmatched = discover_corp_codes(key, corp_codes)
    except Exception as e:  # noqa: BLE001 — 못 찾아도 기존 종목 조회는 계속한다
        print(f"corp_code 자동 찾기 실패 ({e})")
    if discovered:
        corp_codes.update(discovered)
        save_json(CORP_CODES_PATH, corp_codes)

    pending = [name for name in corp_codes if name not in rcept]
    changed = []
    errors = []

    for name in pending:
        cc = corp_codes[name]
        try:
            found = find_new_filing(key, cc)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: 목록 조회 실패 ({e})")
            continue
        if not found:
            continue
        rcept_no, report_nm = found
        try:
            tmp_path = os.path.join(SCRATCH, f"_tmp_{cc}.xml")
            fetch_filing_xml(key, rcept_no, tmp_path)
            parsed = parse_filing(tmp_path)
            os.remove(tmp_path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: 신고서 파싱 실패 ({e})")
            continue
        rcept[name] = rcept_no
        dart_data[name] = parsed
        changed.append(f"{name} ({report_nm}, {rcept_no})")

        if name not in profiles:
            try:
                prof = fetch_company_profile(key, cc)
                if prof:
                    profiles[name] = prof
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: 기업개요 조회 실패 ({e})")

    update_demand_info(key, corp_codes, demand_info, changed, errors)

    if changed:
        save_json(DART_DATA_PATH, dart_data)
        save_json(PROFILES_PATH, profiles)
        save_json(RCEPT_PATH, rcept)
        save_json(DEMAND_INFO_PATH, demand_info)
        regenerate_html(dart_data, profiles, rcept, demand_info)

    if discovered:
        print("corp_code 새로 찾음: " + ", ".join(f"{n}({c})" for n, c in discovered.items()))
    if unmatched:
        print("corp_code 못 찾음(DART 회사명이 다르거나 신고서가 3개월보다 오래됨): " + ", ".join(unmatched))
    print(f"확인 대상: {len(pending)}개 종목")
    if changed:
        print("새로 반영됨:")
        for c in changed:
            print("  - " + c)
    else:
        print("새로 반영된 신고서 없음.")
    if errors:
        print("오류:")
        for e in errors:
            print("  ! " + e)

    sys.exit(0 if changed or discovered else 3)


if __name__ == "__main__":
    main()
