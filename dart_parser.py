"""
DART 증권신고서(지분증권) 파싱 — [발행조건확정] 버전에서
공모개요/청약일정, 수요예측 참여 규모, 가격대별 신청현황, 의무보유확약기간별
참여내역을 뽑아낸다. 표 인덱스가 아니라 헤더 텍스트 패턴으로 표를 찾는다.
"""
import re
import json
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def load_tables(path):
    content = open(path, encoding="utf-8", errors="ignore").read()
    soup = BeautifulSoup(content, "html.parser")
    return soup.find_all("table")


def table_rows(t):
    """rowspan/colspan을 펼쳐서 grid[row][col] = text 형태로 반환."""
    grid = []
    pending = {}  # col -> (text, remaining_rows)
    for tr in t.find_all("tr"):
        row = []
        col = 0
        cells = tr.find_all(["td", "th"])
        ci = 0
        while ci < len(cells) or col in pending:
            if col in pending:
                text, remaining = pending[col]
                row.append(text)
                if remaining <= 1:
                    del pending[col]
                else:
                    pending[col] = (text, remaining - 1)
                col += 1
                continue
            c = cells[ci]
            text = c.get_text(" ", strip=True)
            colspan = int(c.get("colspan", 1) or 1)
            rowspan = int(c.get("rowspan", 1) or 1)
            for k in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[col + k] = (text, rowspan - 1)
            col += colspan
            ci += 1
        grid.append(row)
    return grid


def find_table(tables, *, first_cell=None, contains_row_label=None):
    for t in tables:
        rows = table_rows(t)
        if not rows:
            continue
        if first_cell and rows[0][:1] and rows[0][0].strip() == first_cell:
            return rows
        if contains_row_label:
            flat = [cell for row in rows for cell in row]
            if contains_row_label in flat:
                return rows
    return None


def find_all_tables(tables, *, contains_row_label):
    out = []
    for t in tables:
        rows = table_rows(t)
        if not rows:
            continue
        flat = [cell for row in rows for cell in row]
        if contains_row_label in flat:
            out.append(rows)
    return out


def find_table_by_first_cell_startswith(tables, prefix):
    """row[0]가 prefix로 시작하는 행이 있는 표를 찾는다 — 각주 번호(주1/주2)가
    회사마다 달라 정확히 일치시키기 어려운 라벨(예: "경쟁률")에 쓴다."""
    for t in tables:
        rows = table_rows(t)
        if not rows:
            continue
        if any(row and row[0].startswith(prefix) for row in rows):
            return rows
    return None


def num(s):
    if s is None:
        return None
    s = s.strip()
    if s in ("", "-", "−"):
        return None
    s = s.replace(",", "").replace("원", "").replace("주", "").replace("%", "")
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return s


def parse_offering_summary_vertical(tables):
    """항목/내용 표에서 공모개요·청약일정을 뽑는다 (지분증권 필링의 기본 포맷).

    이 표는 정정 증권신고서 안에서 "정정 전/정정 후" 형태로 여러 번 반복
    등장하는 경우가 흔한데(예: 케이앤에스아이앤씨), 앞쪽 표는 아직 가격이
    확정되기 전 초안이라 확정가액 칸이 비어 있고 뒤쪽(마지막) 표라야 실제
    갱신된 값이 들어있다. 그래서 첫 번째가 아니라 "마지막으로 일치하는 표"를
    쓴다. 헤더 텍스트도 "항 목"/"항  목"처럼 회사마다 공백 수가 달라
    정규식으로 느슨하게 찾는다.
    """
    header_re = re.compile(r"항\s*목")
    value_re = re.compile(r"내\s*용")
    matched = None
    for t in tables:
        rows_ = table_rows(t)
        if rows_ and rows_[0][:1] and header_re.fullmatch(rows_[0][0].strip()):
            matched = rows_  # 마지막 일치본을 쓰기 위해 계속 덮어씀
    rows = matched
    if not rows:
        return None

    # "내용" 칸이 항상 마지막 열은 아니다(예: 딜리셔스는 "내용" 뒤에 "비고" 열이
    # 하나 더 있어 row[-1]이 "비고"가 되어버림) — 헤더에서 "내용" 칸의 실제
    # 위치를 찾아 그 인덱스로 값을 읽는다.
    value_idx = -1
    for i, cell in enumerate(rows[0]):
        if value_re.fullmatch(cell.strip()):
            value_idx = i
            break

    out = {}
    for row in rows:
        joined = " ".join(row)
        val = row[value_idx] if -len(row) <= value_idx < len(row) else row[-1]
        if "모집 또는 매출주식의 수" in joined:
            m = re.search(r"([\d,]+)\s*주", val)
            if m:
                out["offer_shares"] = num(m.group(1))
        if "모집총액" in joined and re.search(r"예정(가액|총액)", joined):
            m = re.search(r"([\d,]+)\s*원", val)
            if m:
                out["offer_amount_expected"] = num(m.group(1))
        if "모집총액" in joined and re.search(r"확정(가액|총액)", joined):
            m = re.search(r"([\d,]+)\s*원", val)
            if m:
                out["offer_amount_final"] = num(m.group(1))
            elif "offer_amount_expected" in out:
                # "확정가액" 칸이 숫자 없이 각주 번호만 있는 경우(예: 케이앤에스아이앤씨) —
                # 각주를 확인해보면 "확정모집가액은 X원 단일가"라고 명시되어 있어, 이 표(마지막
                # 일치본)의 예정가액이 곧 확정가액과 같다는 뜻이다. 그래서 예정가액으로 대체한다.
                out["offer_amount_final"] = out["offer_amount_expected"]
        has_inst = any("기관투자자" in c for c in row)
        if has_inst and "개시일" in row:
            idx = row.index("개시일")
            out.setdefault("subscription", {})["inst_start"] = row[idx + 1] if idx + 1 < len(row) else None
        if has_inst and "종료일" in row and out.get("subscription", {}).get("inst_start") and "inst_end" not in out.get("subscription", {}):
            idx = row.index("종료일")
            out.setdefault("subscription", {})["inst_end"] = row[idx + 1] if idx + 1 < len(row) else None
        if "납  입  기  일" in joined or "납입기일" in joined.replace(" ", ""):
            m = re.search(r"(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)", val)
            if m:
                out["payment_date"] = m.group(1)
    return out


def parse_offering_summary_compact(tables):
    """증권예탁증권(KDR) 필링에서 쓰는 대안 포맷.

    항목/내용 세로형 표 대신, 한 줄짜리 가로형 표 두 종류를 쓴다:
    - '증권의종류/증권수량/액면가액/모집(매출)가액/모집(매출)총액/모집(매출)방법'
      헤더의 표 (에이치엘지노믹스 실측: 발행주식수·모집총액이 여기 있음)
    - '청약기일/납입기일/청약공고일/배정공고일/배정기준일' 헤더의 청약일정표

    두 표 모두 [발행조건확정] 문서 안에서 두 번(정정 전 초안 -> 확정본) 반복
    등장한다(에이치엘지노믹스 실측: 첫 표는 희망공모가 하단인 18,500원 기준
    총액, 마지막 표가 확정가 21,500원 기준 총액). 그래서 마지막 일치본을
    "확정" 값으로, 그보다 앞선 일치본이 있다면 그 첫 값을 "예정" 값으로 쓴다.
    """
    price_header = ['증권의종류', '증권수량', '액면가액', '모집(매출)가액', '모집(매출)총액', '모집(매출)방법']
    price_matches = []
    for t in tables:
        rows = table_rows(t)
        if rows and rows[0][:len(price_header)] == price_header and len(rows) > 1:
            price_matches.append(rows)

    sched_header = ['청약기일', '납입기일', '청약공고일', '배정공고일', '배정기준일']
    sched_matches = []
    for t in tables:
        rows = table_rows(t)
        if rows and rows[0][:len(sched_header)] == sched_header and len(rows) > 1:
            sched_matches.append(rows)

    if not price_matches and not sched_matches:
        return None

    out = {}
    if price_matches:
        last_data = price_matches[-1][1]
        out["offer_shares"] = num(last_data[1])
        out["offer_amount_final"] = num(last_data[4])
        if len(price_matches) > 1:
            out["offer_amount_expected"] = num(price_matches[0][1][4])

    if sched_matches:
        last_data = sched_matches[-1][1]
        sub_cell = last_data[0]
        parts = re.split(r"\s*~\s*", sub_cell)
        if len(parts) == 2:
            out["subscription"] = {"inst_start": parts[0].strip(), "inst_end": parts[1].strip()}
        elif sub_cell not in ("", "-"):
            out["subscription"] = {"inst_start": sub_cell.strip()}
        if len(last_data) > 1 and last_data[1] not in ("", "-"):
            out["payment_date"] = last_data[1]

    return out or None


def parse_offering_summary(tables):
    """지분증권 표준 포맷을 먼저 시도하고, 없으면(증권예탁증권/KDR형) 대안 포맷으로 폴백.

    인제니아처럼 세로형 "항목/정정전/정정후" 표가 존재하긴 하지만 그 표는
    청약일정 라벨("기관투자자"/"개시일"/"종료일")만 우연히 걸리고 정작
    발행주식수·모집총액은 없는 경우가 있다(그 필드들은 KDR형 전용 가로형
    표에만 있음). 그래서 "확정 발행규모(주식수+모집총액)가 둘 다 있는지"를
    세로형 결과를 신뢰할지 판단하는 기준으로 쓴다 — 부족하면 가로형(대안)
    결과로 완전히 교체한다(두 포맷을 항목별로 병합하면 서로 다른 정정
    회차의 값이 섞일 위험이 있어, 통째로 한쪽만 쓴다).
    """
    vertical = parse_offering_summary_vertical(tables)
    if vertical and vertical.get("offer_shares") and vertical.get("offer_amount_final"):
        return vertical
    compact = parse_offering_summary_compact(tables)
    if compact:
        return compact
    return vertical


def parse_demand_summary(tables):
    """(가) 수요예측 참여 규모 — 합계 열에서 전체 건수/수량/경쟁률.
    각주 번호("경쟁률(주1)" vs "(주2)" 등)가 회사·주간사마다 달라서 그 라벨로
    표를 찾지 않고, row[0]=="건수"인 행이 있는 표로 찾는다(더 안정적)."""
    rows = find_table_by_first_cell_startswith(tables, "건수")
    if not rows:
        return None
    count_row = next((r for r in rows if r[0] == "건수"), None)
    qty_row = next((r for r in rows if r[0] == "수량"), None)
    rate_row = next((r for r in rows if r[0].startswith("경쟁률")), None)
    if not (count_row and qty_row and rate_row):
        return None
    return {
        "total_count": num(count_row[-1]),
        "total_qty": num(qty_row[-1]),
        "competition_rate": num(rate_row[-1]),
    }


def parse_price_band_summary(tables):
    """가격대별 참여건수/신청수량 비율 요약표.

    회사(주간사)마다 "비고" 열이 있기도 하고 없기도 해서(니어스랩엔 있고
    해치텍엔 없음), 고정된 열 위치 대신 헤더 행에서 "참여건수"/"신청수량"이
    있는 열 번호를 찾아 그 열 기준으로 읽는다. "가격 미제시"(띄어쓰기 있음)와
    "가격미제시"(붙여씀, 예: 기도산업/에이치엘지노믹스) 둘 다 나오므로 정규식으로 찾는다.
    """
    band_re = re.compile(r"가격\s*미제시")
    rows = None
    for t in tables:
        rows_ = table_rows(t)
        if rows_ and any(band_re.fullmatch(c.strip()) for row in rows_ for c in row):
            rows = rows_
            break
    if not rows or len(rows) < 3:
        return None
    header_row = rows[1]  # ['구분', '참여건수(건)', '비율', '신청수량(주)', '비율'] 형태
    try:
        count_idx = next(i for i, c in enumerate(header_row) if c.startswith("참여건수"))
        qty_idx = next(i for i, c in enumerate(header_row) if c.startswith("신청수량"))
    except StopIteration:
        return None
    out = []
    for row in rows[2:]:
        if len(row) <= max(count_idx, qty_idx) + 1:
            continue
        out.append({
            "band": row[0],
            "count": num(row[count_idx]),
            "count_pct": row[count_idx + 1],
            "qty": num(row[qty_idx]),
            "qty_pct": row[qty_idx + 1],
        })
    return out


def parse_lockup_breakdown(tables):
    """의무보유확약기간별 수요예측 참여내역.

    국내/외국 투자자가 표 두 개(96유형: 국내, 98유형: 외국+합계)로 나뉘어
    있는데, 뒤쪽 표의 마지막 "합계" 열 그룹이 이미 국내+외국 전체를 합산한
    값이다(직접 검증: 6개월 확약 행에서 국내 1건+외국(기타) 1건 = 합계 2건,
    미확약 행에서도 국내 1505건+외국(기타+거래실적유) 258건 = 합계 1763건과
    정확히 일치). 그래서 두 표를 따로 합산하면 두 번 세게 되므로, "합계" 열이
    있는 표를 찾아 그 마지막 (건수,수량) 그룹만 읽는다.
    """
    all_rows = find_all_tables(tables, contains_row_label="6개월 확약")
    if not all_rows:
        return None
    periods = ["6개월 확약", "3개월 확약", "1개월 확약", "15일 확약", "미확약"]

    # "합계"가 헤더에 있는 표(그 표의 마지막 3열이 합계 건수/수량/가격)를 우선 사용
    total_rows = None
    for rows in all_rows:
        header_flat = [c for row in rows[:3] for c in row]
        if "합계" in header_flat:
            total_rows = rows
            break
    if total_rows is None:
        total_rows = all_rows[0]  # 합계 열이 없는 표뿐이면(=단일 표) 그대로 사용

    out = {}
    for row in total_rows:
        if row[0] not in periods:
            continue
        # 마지막 그룹이 (건수, 수량, 신청가격) 3열 — 합계 열이 없는 단일 표라면
        # 마지막 그룹이 곧 유일한(=전체) 그룹이라 어느 쪽이든 맞다.
        count, qty = num(row[-3]), num(row[-2])
        out[row[0]] = {
            "count": count if isinstance(count, (int, float)) else 0,
            "qty": qty if isinstance(qty, (int, float)) else 0,
        }
    return out


def _int(s):
    return int(s.replace(",", "")) if s else None


def parse_demand_terms(path):
    """수요예측 참여 조건 — 희망공모가 밴드, 신청 수량/가격 단위, 기관 최고한도(주).
    최초 증권신고서·정정신고서 모두에 들어 있어 수요예측 전에 쓸 수 있다.
    2026-10 씨앤엠·제이앤티지 신고서 실측 기준:
      표 "주당 희망공모가액 | 10,000원 ~ 13,000원"
      표 "수량단위 | 1,000주" / "가격단위 | 100원"
      표 "구분 | 최고한도 | 최저한도" → "기관투자자 | ...또는 2,059,500주(...) | 1,000주"
      (또는 "수요예측참가신청관련사항 | 1) 최고한도 : ... 또는 3,182,xxx주")
    표기 변형(2026-10 실측): 엠비디 "...나눈주식수 및 1,500,000주(...) 중 적은 수량"(표가 아닌 문단),
    아이엠스팩1호 '... 또는 "3,750,000주" 중 적은 수량', 스팩 밴드는 "2,000원" 단일가.
    못 찾은 항목은 None으로 둔다(추정하지 않음)."""
    tables = load_tables(path)
    band = qty_unit = price_unit = inst_max = None
    band_re = re.compile(r"([\d,]+)\s*원\s*~\s*([\d,]+)\s*원")
    unit_re = re.compile(r"([\d,]+)\s*(주|원)")
    # "또는"/"및" 다음 숫자, 사이에 따옴표가 낄 수 있다
    or_num = r"(?:또는|및)\s*[\"“”']?\s*([\d,]+)\s*주"
    max_re = re.compile(r"최고\s*한도[^\d]{0,200}?" + or_num)
    single_re = re.compile(r"^\s*([\d,]+)\s*원\s*$")
    for t in tables:
        rows = table_rows(t)
        header = " ".join(rows[0]) if rows else ""
        for r in rows:
            if len(r) < 2:
                continue
            label = re.sub(r"\s+", "", r[0])
            val = r[1]
            if band is None and ("희망공모가" in label or "공모희망가" in label):
                m = band_re.search(val)
                if m:
                    band = [_int(m.group(1)), _int(m.group(2))]
                else:
                    m = single_re.match(val)  # 스팩 등 단일 희망가
                    if m:
                        band = [_int(m.group(1)), _int(m.group(1))]
            if qty_unit is None and label == "수량단위":
                m = unit_re.search(val)
                if m:
                    qty_unit = _int(m.group(1))
            if price_unit is None and label == "가격단위":
                m = unit_re.search(val)
                if m:
                    price_unit = _int(m.group(1))
            if inst_max is None and "최고한도" in re.sub(r"\s+", "", header) and label == "기관투자자":
                m = re.search(or_num, r[1])
                if m:
                    inst_max = _int(m.group(1))
        if inst_max is None:
            flat = " ".join(c for r in rows for c in r)
            m = max_re.search(flat)
            if m:
                inst_max = _int(m.group(1))
    if inst_max is None:
        # 문장형: "최고한도는 수요예측 대상 수량 2,059,500주를 초과할 수 없습니다"
        text = BeautifulSoup(open(path, encoding="utf-8", errors="ignore").read(), "html.parser").get_text(" ")
        m = re.search(r"최고\s*한도는[^.\d]{0,40}([\d,]+)\s*주", text) or max_re.search(text)
        if m:
            inst_max = _int(m.group(1))
    return {"band": band, "qty_unit": qty_unit, "price_unit": price_unit, "inst_max": inst_max}


def parse_overview(path):
    """공모개요 — 주관사 밸류에이션 요약, 상장예정주식수, 사업의 개요 첫 문장.
    2026-10 이에스티·씨아이에스케미칼·래블업·엘리스그룹 신고서 실측 기준:
      '인수인의 의견' 요약표 "평가방법 | 상대가치법" / "평가모형 | PER"(또는 EV/EBITDA) /
      "② | 유사기업 PER | 31.65배"(엘리스는 "② | 유사기업 EV/EBITDA | 배 | 27.13") /
      "주당 평가가액 | 16,649원" / "④ | 주당 평가가액에 대한 할인율 | 36.04% ~ 25.23%"
      문장 "상장예정주식수는 13,914,200주", "상장예정주식수(보통주) 14,541,508주" — 정정 전/후가
      같이 실린 신고서가 있어 마지막 값을 쓴다.
      "사업의 개요" 뒤 첫 "당사는 …니다." 문장.
    못 찾은 항목은 None(추정하지 않음)."""
    content = open(path, encoding="utf-8", errors="ignore").read()
    soup = BeautifulSoup(content, "html.parser")
    out = {"val_model": None, "val_multiple": None, "val_fair": None, "val_discount": None,
           "listed_shares": None, "biz": None}
    num_re = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(배|원|원/주)?$")
    summary = None
    for t in soup.find_all("table"):
        if t.find("table") is not None:
            continue  # 표를 감싼 바깥 표
        rows = table_rows(t)
        labels = {re.sub(r"\s+", "", r[0]) for r in rows if r}
        if "평가방법" in labels and "평가모형" in labels:
            summary = rows  # 정정 전/후 두 개면 뒤엣것
    if summary:
        for r in summary:
            cells = [re.sub(r"\s+", " ", c).strip() for c in r]
            joined = re.sub(r"\s+", "", " ".join(cells))
            if cells and re.sub(r"\s+", "", cells[0]) == "평가모형" and len(cells) > 1:
                out["val_model"] = cells[1]
                continue
            model = out["val_model"]
            if model and out["val_multiple"] is None and re.search(r"(유사기업|비교회사|비교기업|적용)", joined) \
                    and re.sub(r"\s+", "", model) in joined and "적용산식" not in joined:
                for c in cells[2:] if cells[0] in "①②③④⑤⑥⑦" else cells[1:]:
                    m = num_re.match(c)
                    if m and "." in m.group(1) or (m and m.group(2) == "배"):
                        out["val_multiple"] = float(m.group(1).replace(",", ""))
                        break
            if out["val_fair"] is None and "주당평가가액" in joined and "할인율" not in joined:
                for c in cells[1:]:
                    m = num_re.match(c)
                    if m and m.group(2) != "배" and "." not in m.group(1):
                        out["val_fair"] = int(m.group(1).replace(",", ""))
                        break
            if out["val_discount"] is None and "할인율" in joined:
                m = re.search(r"([\d.]+)\s*%\s*~\s*([\d.]+)\s*%", " ".join(cells))
                if m:
                    out["val_discount"] = [float(m.group(1)), float(m.group(2))]
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    shares = re.findall(r"상장\s*예정\s*주식\s*수(?:\([^)]{0,30}\))?[^\d]{0,40}?([\d,]{6,})\s*주", text)
    if shares:
        out["listed_shares"] = _int(shares[-1])
    i = text.find("사업의 개요")
    while i >= 0 and out["biz"] is None:
        m = re.search(r"(?:당사|동사)는\s[^.]{10,400}?니다\.", text[i:i + 8000])
        if m:
            out["biz"] = m.group(0)
        i = text.find("사업의 개요", i + 6)
    return out


def parse_filing(path):
    tables = load_tables(path)
    return {
        "offering": parse_offering_summary(tables),
        "demand_summary": parse_demand_summary(tables),
        "price_bands": parse_price_band_summary(tables),
        "lockup": parse_lockup_breakdown(tables),
    }


if __name__ == "__main__":
    import sys
    result = parse_filing(sys.argv[1])
    out_path = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1] + ".parsed.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("wrote", out_path)
