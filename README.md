# 공모주 캘린더 — DART 데이터 자동 갱신

`ipo_calendar.html`(공모주 수요예측 캘린더 아티팩트)에 들어가는 DART 데이터를
주기적으로 최신화하는 스크립트.

## 하는 일

`update_dart_data.py`를 실행하면:

1. `corp_codes.json`에 있는 종목 중 아직 `filing_rcept.json`에 기록되지 않은
   종목의 corp_code로 DART에서 `[발행조건확정]증권신고서(지분증권/증권예탁증권)`가
   새로 나왔는지 확인한다.
2. 새로 나온 신고서가 있으면 원문을 받아 `dart_parser.py`로 파싱하고, 그 종목의
   기업개요(`company.json`)도 같이 받아온다.
3. `dartData.json` / `company_profiles.json` / `filing_rcept.json`을 갱신하고,
   `ipo_calendar.html` 안의 `AUTO-GENERATED:*` 마커 사이 블록을 다시 써서 반영한다.
4. 바뀐 게 있으면 표준출력에 요약하고 종료코드 0, 없으면 종료코드 3으로 끝난다.
   (한국 주말·공휴일에는 아무것도 안 하고 종료코드 3만 낸다.)

바뀐 게 있으면(exit 0) `ipo_calendar.html`을 Claude 아티팩트로 재게시해야 실제
페이지에 반영된다 — 이 스크립트 자체는 파일만 갱신하고, 게시는 별도 단계다.

## 실행 방법

```bash
pip install -r requirements.txt
export DART_API_KEY=발급받은키
python update_dart_data.py
```

## 파일 구성

- `dart_parser.py` — DART [발행조건확정]증권신고서 원문(HTML/XML)에서 공모개요·
  수요예측참여내역·가격분포·의무보유확약비율을 뽑는 파서. 지분증권/증권예탁증권
  두 필링 포맷을 모두 처리하며, 실제 7개 이상의 필링으로 교차검증됨(가격×주식수=
  모집총액, 가격분포 합계=수요예측 총합, 확약구간 합계=수요예측 총합).
- `update_dart_data.py` — 위 파서를 이용한 주기 갱신 스크립트(이 저장소의 진입점).
- `corp_codes.json` — 종목명 → DART corp_code 매핑.
- `dartData.json` / `company_profiles.json` / `filing_rcept.json` — 소스 오브
  트루스 데이터 저장소. `ipo_calendar.html`에 임베드되는 JS 객체는 이 파일들에서
  생성된다.
- `ipo_calendar.html` — 실제 배포되는 단일 파일 웹앱(공모주 캘린더).
