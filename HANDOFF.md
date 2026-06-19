# Forge 핸드오프

## 현재 상태

Forge가 기존 세계관에서 5권 구조를 생성하고 critic 독립 검증을 통과한 후보를 정본으로 승격했다. 상태 원장 재구성까지 끝났다.

프로젝트 경로는 `C:\Users\USER\forge`이며 Golem 저장소와 완전히 분리되어 있다.

GitHub 원격 저장소는 `https://github.com/BN8624/forge`다.

## 완료 항목

- 5권 구조를 강제하는 시리즈, 권, 사건, 장면, 상태 원장 스키마.
- 계층, 순번, 상태 연속성, 이전 장면 연결 검증.
- 설정 변경, 복선, 회수의 단일 소유권 검증.
- 복선보다 회수가 먼저 등장하는 구조 차단.
- 후보 `story`의 독립 검증과 실패 시 정본 보존.
- 같은 파일시스템 내 정본 디렉터리 교체와 중단된 교체 복구.
- 검증된 구조에서 상태 원장을 결정적으로 재구성하고 원자적으로 저장.
- 상태 원장 재실행의 바이트 동일성 및 적용 요소 목록 검증.
- 기획 브리프에서 완전한 5권 구조 번들을 생성하고 검증된 후보만 게시.
- 파싱, 구조 검증, 게시 실패 시 기존 후보 출력 보존.
- critic의 C1-C21 장면별 판정과 후보 해시 검증.
- 승인 파일이 없거나 후보 변경으로 오래된 경우 정본 승격 차단.
- 승인 근거를 `story/canon-review.json`에 정본 구조와 함께 보존.
- 숨은 모델 폴백 제거.
- Atelier 핵심 자료의 읽기 전용 참고 사본 보관.

## 다음 구현 순서

1. Forge가 현재 5권 정본을 권별 최소 8만 자 규모의 사건·장면 구조로 확장한다.
2. critic이 확장 구조의 C1-C21 추적성, 상태 연속성, 장편 규모를 독립 검증한다.
3. 확장 구조 승격 뒤 장면별 산문 배치 생성을 시작한다.

## 실행 명령

```powershell
python -m unittest discover -s tests -v
python pipeline\generate_candidate.py
python pipeline\validate_canon.py runs\candidate
python pipeline\validate_structure.py
python pipeline\validate_scale.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
python pipeline\generate_prose.py
```

실제 생성과 검증 전에 `.env`에 `GENERATOR_MODEL`과 `CRITIC_MODEL`을 명시해야 한다.
