# 작업 규칙

## 홈페이지(아티팩트) 재게시

홈페이지는 GitHub Pages가 아니라 Claude 아티팩트다:
https://claude.ai/artifact/MyR1BzsALcGLNHkVc2UyHi

master에 머지해도 홈페이지는 바뀌지 않는다. 그래서:

1. `ipo_calendar.html`을 고쳐 master에 머지했으면, 같은 세션에서 위 아티팩트에 그 파일로 다시 게시한다
   (Artifact publish, `url`에 위 주소, `file_path`에 `ipo_calendar.html`). 사용자에게 따로 묻지 않는다.
2. 다시 게시하기 전에 올라가 있는 버전을 읽어(Artifact read) 저장소 파일과 비교한다.
   저장소에 없는 변경이 올라가 있으면 덮어쓰지 말고 사용자에게 먼저 알린다.
3. `records.json`(사용자가 입력한 값)은 건드리지 않는다. 페이지 파일만 게시하면 그대로 남는다.
4. 다시 게시한 뒤에는 페이지를 다시 읽어 고친 부분이 실제로 들어갔는지 확인하고 결과를 알린다.
