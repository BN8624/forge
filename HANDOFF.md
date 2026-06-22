# Forge 인수인계

## 현재 정본

현재 작품은 Forge가 신규 세계관부터 자동 생성한 5권 장편 `은색의 기록자와 망각의 태엽`이다.

- 원천 세계관은 `reference/current`에 있다.
- 5권 구조 정본은 `story`에 있다.
- 승인 산문과 critic 결과는 `prose/scenes`에 있다.
- 현재 상태 원장은 `state/current.json`이다.
- 전체 규모는 20개 사건, 108개 장면, 산문 375,597자다.
- 이전 완성본은 `runs/world-backups/20260621T020758.714624Z`에 보존한다.

권별 제목과 산문 규모는 다음과 같다.

- V1 `녹슨 거리의 은색 기록`. 20개 장면, 71,192자.
- V2 `살아있는 저장소의 비명`. 21개 장면, 74,982자.
- V3 `상아탑의 이클립스`. 26개 장면, 83,276자.
- V4 `므네모시네의 붕괴`. 21개 장면, 74,621자.
- V5 `백색 도서관의 파수꾼`. 20개 장면, 71,526자.

## 자동화 원칙

- Forge 모델이 세계관, 구조, 산문을 생성한다.
- 하네스는 계약, 프롬프트, 검증, 재시도, 승격만 담당한다.
- 산문 오류는 직접 고치지 않는다. 원인 계약이나 프롬프트를 보강하고 해당 범위를 다시 생성한다.
- 구조화 문서가 정본이며 산문과 EPUB은 파생 산출물이다.
- 후보는 독립 검증을 통과한 뒤에만 정본으로 승격한다.

## 실행 명령

```powershell
python pipeline\complete_series.py
python pipeline\complete_series.py --new-world
python pipeline\complete_series.py --game-scenario
python -m unittest discover -s tests -v
python pipeline\validate_structure.py
python pipeline\validate_scale.py
python pipeline\serve_prose.py --host 100.89.73.83 --port 8765
```

일반 재검증과 EPUB 재생성은 `python pipeline\complete_series.py`를 사용한다. 완전히 새로운 작품은 `python pipeline\complete_series.py --new-world`를 사용한다.

게임 시나리오 원작 기획의 선택까지 Forge에 맡길 때는
`python pipeline\complete_series.py --game-scenario`를 사용한다. 이 명령은
시놉시스 5개 생성, 독립 critic 평가와 선택, 선택본 기반 세계관 생성,
5권 구조·산문·EPUB 완주를 순서대로 실행한다.

## iPhone 서재

같은 Tailscale 네트워크에 연결된 iPhone에서 `http://node.tail3e9e21.ts.net:8765/`를 연다. 전권 HTML 읽기와 V1-V5 EPUB 다운로드를 제공한다.

2026년 6월 22일 기준 iPhone 390×844 뷰포트에서 제목, EPUB 링크 5개, 가로 넘침 없음을 검증했다.
