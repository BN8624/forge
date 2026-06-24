# Forge 다음 세션 인수인계

## 현재 상태

- 게임 시나리오 생성은 완료됐다.
- 완료 상태 파일은 `runs/complete-series/status.json`이며 현재 stage는 `scenario_complete`다.
- 선택 후보는 `S3`, 제목은 `금기 유물의 큐레이터`다.
- 결과물은 `exports/game-scenario.json`과 `exports/game-scenario.md`다.
- 아이폰에서 바로 읽는 URL은 `http://node.tail3e9e21.ts.net:8765/game-scenario`다.
- 대시보드 URL은 `http://node.tail3e9e21.ts.net:8765/dashboard`다.
- Forge 대시보드 서버는 `100.89.73.83:8765`에서 `pipeline/serve_prose.py`로 실행 중이다.
- 실행 중인 `complete_series.py` 생성 프로세스는 없다.
- `STOP_AFTER_RUN` 파일은 없다.

## 최근 방향 전환

- 5권 또는 10권 장편 산문 자동 완주는 현재 목적에 비해 비용과 실패 가능성이 컸다.
- `--game-scenario`는 더 이상 장편 구조, 산문, EPUB 생성으로 이어지지 않는다.
- `--game-scenario`는 시놉시스 선택, 세계관 원고 생성, 게임 시나리오 패키지 작성 뒤 `scenario_complete`로 종료한다.
- 장편 산문 자동 생성은 기본 `complete_series.py` 또는 `--new-world` 경로에만 남아 있다.
- 대시보드는 게임 시나리오 모드에서 산문 승인, 권별 진행 대신 시나리오 진행 단계, 분량 참고, 산출물 수를 표시한다.

## 결과물 보기

- 모바일 HTML 보기 페이지는 `/game-scenario`다.
- 원본 Markdown 다운로드는 `/game-scenario.md`다.
- HTML은 `exports/game-scenario.md`를 읽어 모바일 화면에 맞게 렌더링한다.
- 서재(`/`)와 준비 화면에도 `게임 시나리오 보기` 링크가 노출된다.

## 현재 작업 트리 주의

- 작업 트리에는 이전 생성 실패와 실험 실행에서 생긴 `story`, `prose`, `reference/current` 변경과 새 장면 파일이 많이 남아 있다.
- 이 변경들은 이번 코드·문서 커밋에 포함하지 않았다.
- 사용자가 명시적으로 정리하라고 하기 전까지 임의로 되돌리거나 삭제하지 않는다.
- 문서와 코드 커밋은 이미 생성 산출물 변경을 제외하고 진행했다.

## 주요 커밋

- `c004c9c 아이폰용 게임 시나리오 보기 추가`.
- `0f31e37 게임 시나리오 패키지 생성으로 전환`.
- `e294486 새 작품 초기 진행 정보 분리`.
- `b4eb129 자동 새로고침에서 사용자 후보 선택 보존`.
- `d760fdb 후보 생성에 사용자 지정 권수 적용`.

## 검증 결과

최근 전체 검증 결과는 다음과 같다.

```text
python -m unittest discover -s tests -v
123 tests passed

python -m compileall -q -f lib pipeline tests
passed

python -m pip check
No broken requirements found.
```

아이폰용 보기 추가 후 별도 확인 결과는 다음과 같다.

```text
python -m unittest tests.test_serve_prose -v
7 tests passed

python -m compileall -q -f pipeline tests
passed

http://node.tail3e9e21.ts.net:8765/game-scenario
HTTP 200, 금기 유물의 큐레이터 표시 확인
```

## 참고 경로

- 현재 상태는 `runs/complete-series/status.json`이다.
- 게임 시나리오 결과물은 `exports/game-scenario.md`와 `exports/game-scenario.json`이다.
- 대시보드 작업 상태는 `runs/dashboard/job.json`이다.
- 대시보드 로그는 `runs/dashboard/job.log`다.
- 상세 결정 기록은 `context-notes.md`다.
- 남은 작업은 `checklist.md`다.
