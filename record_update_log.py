"""
자동 갱신 전후를 비교해서 "무엇이 바뀌었는지"를 한 줄씩 적고,
ipo_calendar.html 의 UPDATE_LOG 블록 맨 앞에 끼워넣는다.

화면의 "최근 업데이트" 배지를 누르면 이 내용이 뜬다. 시각만 찍혀 있으면
"뭐가 달라졌는지"를 알 수 없어서, 실제 변경분을 사람이 읽는 문장으로 남긴다.

쓰는 법:
  python record_update_log.py <갱신전_html> [<대상_html>]

바뀐 게 없으면 아무것도 쓰지 않고 종료한다(빈 항목이 쌓이면 정작 볼 게
안 보이니까). 종료코드는 항상 0 — 이건 워크플로를 멈출 만한 일이 아니다.
"""
import datetime
import json
import re
import sys
import zoneinfo

KEEP = 20
WD = ["월", "화", "수", "목", "금", "토", "일"]


def block(html, tag):
    m = re.search(rf"// AUTO-GENERATED:{tag}:START\n(.*?)// AUTO-GENERATED:{tag}:END",
                  html, re.S)
    return m.group(1) if m else ""


def deal_names(html):
    return set(re.findall(r'\{\s*name:\s*"([^"]+)"', block(html, "DEALS")))


def dart_names(html):
    return set(re.findall(r'^\s{2}"([^"]+)":\s*\{', block(html, "DART_DATA"), re.M))


def filing_names(html):
    return set(re.findall(r'^\s{2}"([^"]+)":\s*"', block(html, "FILING_URL"), re.M))


def mezz_keys(html):
    """종목명+회차로 식별한다 — 같은 회사가 여러 건을 동시에 발행한다."""
    out = set()
    for line in block(html, "MEZZ_DEALS").splitlines():
        n = re.search(r'"name":\s*"([^"]+)"', line)
        r = re.search(r'"round":\s*"([^"]*)"', line)
        k = re.search(r'"kind":\s*"([^"]+)"', line)
        if n:
            out.add((n.group(1), r.group(1) if r else "", k.group(1) if k else ""))
    return out


def listing(names, limit=6):
    names = sorted(names)
    head = ", ".join(names[:limit])
    return head + (f" 외 {len(names)-limit}건" if len(names) > limit else "")


def summarize(before, after):
    items = []

    new_deals = deal_names(after) - deal_names(before)
    if new_deals:
        items.append(f"신규 공모주 {len(new_deals)}건 — {listing(new_deals)}")

    gone = deal_names(before) - deal_names(after)
    if gone:
        items.append(f"공모주 목록에서 빠짐 {len(gone)}건 — {listing(gone)}")

    new_dart = dart_names(after) - dart_names(before)
    if new_dart:
        items.append(f"수요예측 결과 반영 {len(new_dart)}건 — {listing(new_dart)}")

    new_filing = filing_names(after) - filing_names(before)
    if new_filing:
        items.append(f"확정 증권신고서 공시 {len(new_filing)}건 — {listing(new_filing)}")

    new_mezz = mezz_keys(after) - mezz_keys(before)
    if new_mezz:
        kinds = {}
        for _, _, k in new_mezz:
            kinds[k] = kinds.get(k, 0) + 1
        detail = ", ".join(f"{k} {v}건" for k, v in sorted(kinds.items()))
        names = {n for n, _, _ in new_mezz}
        items.append(f"신규 메자닌 {len(new_mezz)}건 ({detail}) — {listing(names)}")

    # 풋옵션 일정은 종목별 개수만 세도 충분하다(날짜 하나하나는 표에서 본다).
    b_put = len(re.findall(r'"put_start"', block(before, "MEZZ_DEALS")))
    a_put = len(re.findall(r'"put_start"', block(after, "MEZZ_DEALS")))
    if a_put > b_put:
        items.append(f"조기상환(풋옵션) 일정 {a_put - b_put}건 추가 확인")

    return items


def main():
    if len(sys.argv) < 2:
        print("사용법: record_update_log.py <갱신전_html> [<대상_html>]")
        return
    before_path = sys.argv[1]
    after_path = sys.argv[2] if len(sys.argv) > 2 else "ipo_calendar.html"

    before = open(before_path, encoding="utf-8").read()
    after = open(after_path, encoding="utf-8").read()

    items = summarize(before, after)
    if not items:
        print("변경 없음 — 업데이트 내역에 남기지 않습니다.")
        return

    now = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Seoul"))
    stamp = f"{now.month}.{now.day}({WD[now.weekday()]}) {now.strftime('%H:%M')}"

    cur = block(after, "UPDATE_LOG")
    m = re.search(r"const UPDATE_LOG = (\[.*?\]);", cur, re.S)
    try:
        entries = json.loads(m.group(1)) if m else []
    except (json.JSONDecodeError, AttributeError):
        entries = []
    entries.insert(0, {"at": stamp, "items": items})
    entries = entries[:KEEP]

    body = "const UPDATE_LOG = " + json.dumps(entries, ensure_ascii=False, indent=2) + ";\n"
    out = re.sub(
        r"(// AUTO-GENERATED:UPDATE_LOG:START\n).*?(// AUTO-GENERATED:UPDATE_LOG:END)",
        lambda _: "// AUTO-GENERATED:UPDATE_LOG:START\n" + body + "// AUTO-GENERATED:UPDATE_LOG:END",
        after, flags=re.S)
    open(after_path, "w", encoding="utf-8").write(out)

    print(f"업데이트 내역 {len(items)}줄 기록 ({stamp})")
    for t in items:
        print("  - " + t)


if __name__ == "__main__":
    main()
