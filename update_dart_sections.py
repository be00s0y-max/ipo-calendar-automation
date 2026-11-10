"""
참여의견 탭용 DART 신고서 본문 요약 수집.

수요예측이 끝난 지 7일이 안 됐거나 아직 남은 종목마다 demandInfo.json의 최신
지분증권 신고서 접수번호(rcept_no)로 DART 웹 화면(dart.fss.or.kr)의 목차를 읽어
아래 세 부분의 글자를 뽑아 줄인 뒤, ipo_calendar.html의 DART_TEXT 블록에 넣는다.

  - risk : 요약정보 > 핵심투자위험 (항목 제목 + 앞 두 문장씩)
  - use  : 제1부 > 자금의 사용목적 (자금조달·사용계획 앞부분)
  - biz  : 제2부 > 사업의 내용 (표를 뺀 본문 앞부분)

[발행조건확정] 신고서는 목차가 "증권발행조건확정" 하나뿐이라 본문이 없다. 그때는 demandInfo의
ov_rcept(공모개요를 뽑은 본 신고서)로 다시 읽는다.
DART 웹 화면은 API 키가 필요 없다. 접수번호가 바뀌지 않은 종목은 dartSections.json에
저장해 둔 값을 그대로 쓴다. 실패해도 다른 갱신을 막지 않도록 항상 종료코드 0으로 끝낸다.
"""
import datetime
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.request
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from update_dart_data import replace_block, js_str  # noqa: E402

DEALS_PATH = os.path.join(HERE, "deals.json")
DEMAND_INFO_PATH = os.path.join(HERE, "demandInfo.json")
CACHE_PATH = os.path.join(HERE, "dartSections.json")
HTML_PATH = os.path.join(HERE, "ipo_calendar.html")

UA = {"User-Agent": "Mozilla/5.0"}
LIMITS = {"biz": 3500, "risk": 4500, "use": 1200}


def get(url):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001
            if i == 2:
                raise
            print(f"  재시도 {i + 1}: {e}")
            time.sleep(2 * (i + 1))


def html_to_text(src):
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", src)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>|</div>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def toc(rcept_no):
    """신고서 목차: [(제목, viewer.do 주소)]"""
    page = get(f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}")
    out = []
    for block in re.split(r"var node\d+ = \{\};", page)[1:]:
        f = dict(re.findall(r"node\d+\['(\w+)'\] = \"([^\"]*)\"", block))
        if {"text", "dcmNo", "eleId", "offset", "length"} <= f.keys():
            url = (f"https://dart.fss.or.kr/report/viewer.do?rcpNo={rcept_no}&dcmNo={f['dcmNo']}"
                   f"&eleId={f['eleId']}&offset={f['offset']}&length={f['length']}&dtd={f.get('dtd', 'dart4.xsd')}")
            out.append((f["text"].strip(), url))
    return out


def find(entries, pattern, after=None):
    started = after is None
    for title, url in entries:
        if not started:
            started = re.search(after, title) is not None
            continue
        if re.search(pattern, title):
            return url
    return None


def cut(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    i = text.rfind("다.", 0, limit)
    return text[: i + 2 if i > limit * 0.6 else limit].strip() + " …"


def squash_cells(text):
    return re.sub(r"\s*\|\s*\n?\s*", " | ", text)


def section_risk(text):
    """핵심투자위험: 가./나./다. 항목마다 제목과 앞 두 문장"""
    text = squash_cells(text)
    parts = re.split(r"(?=(?:^|\s|\|)\s*[가-하]\.\s)", text)
    items = []
    for p in parts:
        p = p.strip(" |\n")
        m = re.match(r"([가-하]\.\s[^\n|]{2,60}?)(?:\n|\s(?=[0-9A-Za-z가-힣(]))", p)
        if not m:
            continue
        head = m.group(1).strip()
        body = p[m.end():].replace("|", " ")
        sents = re.findall(r".+?다\.", re.sub(r"\s+", " ", body))
        items.append(head + " — " + " ".join(s.strip() for s in sents[:2]))
    return cut("\n".join(items), LIMITS["risk"]) if items else cut(text, LIMITS["risk"])


def section_use(text):
    i = text.find("자금의 사용")
    text = text[i:] if i >= 0 else text
    return cut(squash_cells(text), LIMITS["use"])


def section_biz(text):
    # 표(칸 구분 "|"가 든 줄)는 빼고 본문 문장만 남긴다
    # 그림 안 글자 같은 짧은 조각(12자 미만)도 뺀다
    lines = [ln.strip() for ln in text.split("\n") if "|" not in ln and len(ln.strip()) >= 12]
    return cut("\n".join(lines), LIMITS["biz"])


def fetch(rcept_no):
    entries = toc(rcept_no)
    out = {}
    jobs = [
        ("risk", find(entries, r"핵심투자위험"), section_risk),
        ("use", find(entries, r"자금의\s*사용목적"), section_use),
        ("biz", find(entries, r"사업의\s*내용", after=r"제2부"), section_biz),
    ]
    for key, url, fn in jobs:
        if not url:
            continue
        time.sleep(0.5)
        out[key] = fn(html_to_text(get(url)))
    return out


def main():
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).date().isoformat()
    deals = json.load(open(DEALS_PATH, encoding="utf-8"))
    deals = deals if isinstance(deals, list) else deals.get("deals", [])
    demand_info = json.load(open(DEMAND_INFO_PATH, encoding="utf-8"))
    cache = json.load(open(CACHE_PATH, encoding="utf-8")) if os.path.exists(CACHE_PATH) else {}

    since = (datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()
    keep = {}
    for dl in deals:
        name = dl.get("name")
        demand = dl.get("demand") or []
        if len(demand) < 2 or demand[1] < since:
            continue  # 참여의견을 쓸 일이 없는 종목은 빼서 페이지가 커지지 않게
        info = demand_info.get(name) or {}
        cands = [rc for rc in (info.get("rcept_no"), info.get("ov_rcept")) if rc]
        cands = list(dict.fromkeys(cands))
        if not cands:
            continue
        old = cache.get(name)
        if old and old.get("rcept_no") in cands and any(old.get(k) for k in LIMITS):
            keep[name] = {**old, **{k: cut(old[k], LIMITS[k]) for k in LIMITS if old.get(k)}}
            continue
        for rc in cands:
            try:
                print(f"{name}: {rc} 신고서 본문 읽는 중")
                sec = fetch(rc)
            except Exception as e:  # noqa: BLE001
                print(f"  실패: {e}")
                continue
            if any(sec.get(k) for k in LIMITS):
                sec["rcept_no"] = rc
                keep[name] = sec
                print("  " + ", ".join(f"{k} {len(sec.get(k, ''))}자" for k in LIMITS))
                break
            print("  본문 없음(발행조건확정 등)")
        if name not in keep and old and any(old.get(k) for k in LIMITS):
            keep[name] = old

    json.dump(keep, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    html = open(HTML_PATH, encoding="utf-8").read()
    html = replace_block(html, "DART_TEXT", "const DART_TEXT = " + js_str(keep) + ";")
    open(HTML_PATH, "w", encoding="utf-8").write(html)
    print(f"DART_TEXT: {len(keep)}개 종목")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"참여의견용 DART 본문 수집 실패: {e}")
    sys.exit(0)
