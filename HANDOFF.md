# Forge 다음 세션 인수인계

## 현재 상태

- 작업은 사용자 요청으로 중지됐다.
- `STOP_AFTER_RUN`이 존재한다.
- 실행 중인 `complete_series.py` 프로세스는 없다.
- 마지막 실패 시각은 2026-06-23 01:41 KST다.
- 마지막 오류는 구조 확장 중 발생한 `'list' object has no attribute 'get'`다.
- 현재 산문 생성은 시작하면 안 된다.

## 현재 작품과 정본

- 작품은 `심연의 잔향: 침몰하는 도시의 기록자`다.
- `reference/current`에 선택된 S1 세계관과 게임 시나리오 기획이 있다.
- `story`는 장편 확장 전 5권 골격이며 현재 24장면이다.
- 모든 장면은 `interaction_mode`와 `dialogue_policy`를 명시한다.
- 현재 정본 산문은 0장면이다.
- 직전 산문 백업은 `runs/prose-backups/20260622T152439.798912Z`다.

## 이번 세션에서 바뀐 하네스

- 정적 장면 목표 대신 행동, 방해, 선택, 결과를 요구한다.
- 대화 필요 여부를 objective 키워드로 추측하지 않는다.
- `interaction_mode`는 `solo`, `covert`, `interpersonal`, `group` 중 하나다.
- `dialogue_policy`는 `none`, `optional`, `required` 중 하나다.
- 대화 최소량은 `required` 장면에만 적용한다.
- 장면 전체 생성 재시도는 기본 5회다.
- 산문 실패 피드백과 실패 산출물을 실행 사이에 누적 보존한다.
- 구조 critic 거부 사유는 구조 generator에 최대 3회 되돌려 보낸다.
- 구조 확장 캐시는 story 해시와 사용자·critic 지시를 함께 해시한다.
- generator 출력 한도는 32,768토큰이다.
- `owns`에는 `series.elements`에 선언된 문자열 ID만 허용한다.

## 아직 해결하지 못한 문제

구조 확장 응답 처리 중 리스트를 객체로 간주해 `.get()`을 호출하는 런타임 오류가 남았다. 현재 상태 파일에는 스택 트레이스가 없다.

다음 세션은 자동 실행보다 먼저 오류 위치를 재현해야 한다.

1. `STOP_AFTER_RUN`은 유지한다.
2. 구조 확장만 전경 실행해 전체 스택 트레이스를 확보한다.

```powershell
python pipeline\expand_structure.py --root . --output runs\expanded-candidate
```

3. 모델 응답의 배열·객체 타입이 어긋난 위치를 확인한다.
4. 잘못된 타입을 런타임 예외로 터뜨리지 말고 검증 오류로 반환해 Forge가 재생성하게 한다.
5. 관련 회귀 테스트와 전체 테스트를 실행한다.
6. 5권 확장 구조가 구조·규모·독립 정본 critic을 통과한 뒤에만 승격한다.
7. 표본 3장면을 생성해 `dialogue_policy`가 실제로 작동하는지 확인한다.
8. 사용자 승인 없이 자동 완주를 다시 시작하지 않는다.

## 검증과 커밋

마지막 확인 결과는 다음과 같다.

```text
python -m unittest discover -s tests -v
101 tests passed

python -m compileall -q -f lib pipeline tests
passed

python -m pip check
No broken requirements found.
```

하네스 최신 커밋은 `dfa888b 구조 확장 캐시에 critic 지시 반영`이다.

작업 트리는 생성 구조 교체와 산문 백업 때문에 크게 변경돼 있다. 생성된 `story`, `reference`, `state`, `prose` 변경을 임의로 되돌리거나 하네스 문서 커밋에 섞지 않는다.

## 참고 경로

- 현재 상태는 `runs/complete-series/status.json`이다.
- 구조 확장 후보는 `runs/expanded-candidate`다.
- 확장 작업 캐시는 `runs/expansion-work`다.
- 과거 산문은 `runs/prose-backups`다.
- 상세 결정 기록은 `context-notes.md`다.
- 남은 작업은 `checklist.md`다.
