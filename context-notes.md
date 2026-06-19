# Forge 결정 기록

## 2026-06-19 프로젝트 분리

- 기존 `atelier`는 참고용으로 보존하고 수정하지 않는다.
- 새 프로젝트는 `C:\Users\USER\forge`에서 독립 실행한다.
- Golem 저장소와 별개의 Git 저장소 및 GitHub 공개 저장소를 사용한다.
- 다른 프로젝트 파일을 런타임에 직접 참조하지 않는다.
- API 설정과 범용 호출 유틸은 Forge 내부로 복사해 자족성을 확보한다.
- 기존 10권 설계와 원고는 새 정본으로 승계하지 않고 `reference/legacy`에 격리한다.
- 신규 작품 목표는 5권이다.

## 2026-06-19 정본 모델

- 구조화 문서가 이야기의 유일한 정본이다.
- 계층은 시리즈, 권, 큰 사건, 장면으로 구성한다.
- 산문은 구조 계약을 입력으로 받아 마지막에 렌더링한다.
- 설정 변경, 복선, 회수에는 정확히 하나의 소유자가 필요하다.
- 장면 후보는 독립 검증 전까지 정본과 상태 원장을 변경할 수 없다.
- 오류는 산문을 수동 수정하지 않고 하네스를 강화한 뒤 올바른 범위를 재생성한다.

## 첫 구현 범위

- 모델 호출 없이 동작하는 JSON 스키마와 결정적 구조 검증기를 먼저 만든다.
- 최소 정상 사례와 소유권 중복 실패 사례로 검증 동작을 증명한다.

## 2026-06-19 이식 결과

- `.env`, `lib/config.py`, `lib/llm.py`, `lib/key_usage.py`, `lib/jsonutil.py`를 Forge 내부로 복사했다.
- 기존 정본 원장, 10권 시리즈 설계, 압축 원고는 `reference/legacy`에 복사했다.
- 10권 설계의 권 배치는 새 정본으로 승계하지 않는다.
- `canon_bible.json`의 C1-C21과 `compressed_manuscript.md`의 기존 세계관·사건·결말은 새 5권 구조의 필수 생성 입력이다.
- 기존 Gemma 모델 ID의 암묵적 폴백을 제거했다.
- `GENERATOR_MODEL`과 `CRITIC_MODEL`이 명시되지 않으면 API 호출 준비 단계에서 실패한다.
- 첫 구조 검증기는 5권 고정, 계층 참조, 순번, 상태 연속성, 이전 장면 연결, 요소 단일 소유권, 복선 선행을 검사한다.

## 검증 기록

- `python -m unittest discover -s tests -v` 통과. 테스트 2개.
- `python -m compileall -q lib pipeline tests` 통과.
- PowerShell `ConvertFrom-Json`으로 스키마 JSON 5개 파싱 통과.
- 참고자료와 결정 기록을 제외한 Forge 파일에서 외부 프로젝트 런타임 경로가 없음을 확인했다.

## 2026-06-19 완전 독립 이전

- Golem 내부에 만들었던 Forge 사본을 사용자 폴더 바로 아래로 복제했다.
- 새 경로는 `C:\Users\USER\forge`다.
- GitHub 저장소는 사용자 요청에 따라 공개 저장소로 생성한다.
- `.env`는 로컬에만 유지하고 Git 추적과 GitHub 업로드에서 제외한다.
- 공개 저장소 `https://github.com/BN8624/forge`를 생성하고 `main` 브랜치를 연결했다.

## 2026-06-19 JSON Schema 검증 연결

- 현재 구조 검증기는 필수 필드 일부와 의미 규칙을 직접 검사하지만 `schemas/*.schema.json`을 실행하지 않는다.
- 속성 타입, 문자열 최소 길이, 허용되지 않은 추가 필드 같은 계약 위반을 놓치지 않도록 Draft 2020-12 검증을 구조 검증의 첫 단계에 연결한다.
- 축약 스키마 구현을 별도로 만들지 않고 표준 `jsonschema` 패키지를 명시적 의존성으로 사용한다.
- 문서가 스키마 검증에 실패하면 해당 문서의 의미 검증은 건너뛰어 잘못된 타입으로 인한 검증기 예외를 막는다.
- `series`, `volume`, `event`, `scene`, `state ledger` 문서 모두 각 Draft 2020-12 스키마를 먼저 통과해야 한다.
- 추가 속성과 잘못된 필드 타입을 거부하는 회귀 테스트를 추가했다.
- `python -m unittest discover -s tests -v` 통과. 테스트 4개.
- `python -m compileall -q lib pipeline tests` 통과.
- 전체 스키마 JSON 파싱과 `Draft202012Validator.check_schema` 검사를 통과했다.

## 2026-06-20 후보 정본 승격

- 이번 범위는 모델 호출로 후보를 만드는 단계가 아니라 이미 준비된 후보 `story` 디렉터리를 검증하고 승격하는 결정적 파이프라인이다.
- 후보는 현재 정본에 덮어쓴 뒤 검사하지 않는다. 프로젝트 내부 임시 디렉터리에 복사한 독립 스냅샷을 `validate_project`로 먼저 검사한다.
- 후보 검증 실패 시 정본은 전혀 변경하지 않는다.
- 정본 교체 중 실패하면 기존 `story` 디렉터리를 백업 위치에서 복구한다.
- 프로세스가 디렉터리 교체 사이에서 중단된 경우 다음 승격 실행이 `.promotion-*` 백업을 탐지해 기존 정본을 복구한다.
- 상태 원장은 이번 승격 대상에 포함하지 않는다. 승격된 구조에서 상태 원장을 재구성하고 재실행 결과를 비교하는 작업은 다음 체크리스트 항목에서 구현한다.
- `python -m unittest discover -s tests -v` 통과. 테스트 8개.
- `python -m compileall -q lib pipeline tests` 통과.

## 2026-06-20 상태 원장 재구성

- 상태 원장은 독립 데이터 입력이 아니라 검증된 구조 문서에서 파생되는 산출물이다.
- `last_scene_id`와 `state`는 마지막 장면의 `id`와 `end_state`를 사용한다.
- `applied_element_ids`는 장면 순서로 수집한다. 같은 장면 안에서는 `changes`, `setups`, `payoffs` 순서를 고정한다.
- 기존 상태 원장은 구조 검증의 입력으로 사용하지 않고, 구조 자체가 유효한지 먼저 검사한 뒤 새 원장을 계산한다.
- JSON 직렬화 형식을 고정하고 임시 파일을 같은 디렉터리에서 `os.replace`해 저장한다.
- 구조 검증 실패 또는 파일 교체 실패 시 기존 `state/current.json`을 보존한다.
- 구조 검증기는 기존 원장의 `last_scene_id`, `state`, `applied_element_ids`를 모두 파생값과 비교한다.
- 변화, 복선, 회수가 섞인 장면 순서와 장면 내 종류 순서를 테스트했다.
- `python -m unittest discover -s tests -v` 통과. 테스트 13개.
- `python -m compileall -q lib pipeline tests` 통과.
- `python pipeline\rebuild_state.py --help` 직접 실행 통과.

## 2026-06-20 구조 후보 생성기

- 기존 `reference/legacy/series_bible_10v.json`은 확장 아이디어 참고자료이며 10권 권 배치는 새 5권 후보에 자동 승계하지 않는다.
- 생성 입력은 `canon_bible.json`, `compressed_manuscript.md`와 선택적 사용자 지시를 결합한다.
- 모델은 `series`, `volumes`, `events`, `scenes`를 담은 단일 JSON 객체를 반환한다.
- 생성기는 응답을 정본 경로가 아닌 임시 후보 디렉터리에 분해하고 기존 `validate_project`로 전체 구조를 검사한다.
- JSON 파싱 실패, 중복 문서 ID, 안전하지 않은 ID, 구조 검증 실패 시 기존 후보 출력은 변경하지 않는다.
- 검증된 임시 후보만 지정 출력 경로로 교체한다. 생성과 정본 승격은 별도 명령으로 유지한다.
- LLM 클라이언트는 함수 인자로 주입할 수 있게 해 실제 API 호출 없이 성공과 실패를 재현한다.
- 실제 CLI는 `brief_file`을 읽고 기본적으로 `runs/candidate`에 후보를 게시한다.
- `google-genai`를 명시적 런타임 의존성으로 추가했다.
- 정상 생성, JSON 파싱 실패, 구조 검증 실패, 중복 ID, 게시 실패 복구를 테스트했다.
- `python -m unittest discover -s tests -v` 통과. 테스트 18개.
- `python -m compileall -q lib pipeline tests` 통과.
- `python -m pip check` 통과.
- `python pipeline\generate_candidate.py --help` 직접 실행 통과.
- 최초 구현 시점에는 `GENERATOR_MODEL`이 없어 실제 모델 호출을 실행하지 않았다.

## 2026-06-20 기존 세계관 입력 정정

- “기존 10권 설계를 승계하지 않는다”를 “기존 세계관을 사용하지 않는다”로 잘못 해석한 결정을 폐기한다.
- 새 작품은 카엘, 리아, 발타자르, 에테르노, 영혼의 조각 5개와 C1-C21의 확정 사건을 유지한다.
- `compressed_manuscript.md`는 기존 사건의 장면 맥락과 인물 표현을 제공하는 필수 참고 입력이다.
- `series_bible_10v.json`의 인물 성장, 세력, 장기 복선 아이디어는 참고할 수 있지만 10권 구조와 추가 설정은 C1-C21보다 우선할 수 없다.
- 5권별 사건 배치나 작품 브리프를 사람이 먼저 작성하지 않는다. Forge 생성 모델이 정본 자료를 읽고 구조 후보를 제안한다.
- 생성 명령은 별도 브리프 없이 실행할 수 있고, 추가 지시는 선택 사항이다.
- `python -m unittest discover -s tests -v` 통과. 테스트 19개.
- `python -m compileall -q -f lib pipeline tests` 통과.
