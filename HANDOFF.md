# Forge 다음 세션 인수인계

## 현재 상태

- 작업은 사용자 요청으로 중지됐다.
- `STOP_AFTER_RUN`이 존재한다.
- 실행 중인 `complete_series.py` 프로세스는 없다.
- 구조 확장 런타임 오류는 수정됐다.
- 현재 자동 완주는 `stopped` 상태이며 다음 장면은 `V1-E01-S04`다.
- 사용자 승인 전에는 자동 완주를 재개하지 않는다.
- 신규 게임 시나리오는 Forge 추천 분량이 3단계 이상이면 자동 승인하고, 1~2단계만 승인 대기한다.
- `--game-scenario`는 세계관 원고와 `exports/game-scenario.json`, `exports/game-scenario.md`를 생성한 뒤 `scenario_complete`로 끝난다.
- 장편 산문 자동 생성은 기본 `complete_series.py` 또는 `--new-world` 경로에만 남아 있다.

## 현재 작품과 정본

- 작품은 `심연의 잔향: 침몰하는 도시의 기록자`다.
- `reference/current`에 선택된 S1 세계관과 게임 시나리오 기획이 있다.
- `story`는 독립 critic 승인을 받은 장편 5권 구조이며 현재 106장면이다.
- 모든 장면은 `interaction_mode`와 `dialogue_policy`를 명시한다.
- 현재 정본 산문은 `V1-E01-S01`부터 `V1-E01-S03`까지 3장면이다.
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

- 현재 구조와 표본 3장면은 새 상호작용 계약을 통과했다.
- 다음 작업은 사용자가 테스트를 시작할 때 대시보드에서 실행해 `V1-E01-S04`부터 V1 완성까지 진행하는 것이다.
- 동일 산문 후보가 3회 이상 반복되면 새 API 호출 없이 계약 결함으로 중단한다.
- 네트워크 timeout과 연결 오류는 transient 오류로 재시도한다.

## 가변 분량과 게임 시나리오 생성

- 시놉시스 후보는 `recommended_volume_count`, `volume_arc`, `volume_count_reason`을 가진다.
- 게임 시나리오 모드에서는 이 값을 권수보다 시나리오 단계 수 참고값으로 사용한다.
- 3단계 이상 추천은 자동 승인되고 1~2단계 추천은 `volume_approval` 상태에서 기다린다.
- 사용자가 분량을 지정하면 Forge가 시놉시스를 해당 단계 수에 맞게 보강한다.
- 승인 분량은 `selected-synopsis.json`의 `approved_volume_count`가 정본이다.
- 구조 스키마와 검증기는 V1부터 V99까지 연속된 권 ID를 허용한다.
- 한 권씩 완성하는 `volume_complete` 흐름은 장편 산문 경로에만 적용된다.

## 검증과 커밋

마지막 확인 결과는 다음과 같다.

```text
python -m unittest discover -s tests -v
115 tests passed

python -m compileall -q -f lib pipeline tests
passed

python -m pip check
No broken requirements found.
```

가변 권수와 권별 완성 기능은 이번 세션 커밋에 포함된다.

작업 트리는 생성 구조 교체와 산문 백업 때문에 크게 변경돼 있다. 생성된 `story`, `reference`, `state`, `prose` 변경을 임의로 되돌리거나 하네스 문서 커밋에 섞지 않는다.

## 참고 경로

- 현재 상태는 `runs/complete-series/status.json`이다.
- 구조 확장 후보는 `runs/expanded-candidate`다.
- 확장 작업 캐시는 `runs/expansion-work`다.
- 과거 산문은 `runs/prose-backups`다.
- 상세 결정 기록은 `context-notes.md`다.
- 남은 작업은 `checklist.md`다.
