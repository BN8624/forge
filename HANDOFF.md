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

1. Forge가 정본 장면 계약을 바탕으로 산문 생성 후보를 만든다.
2. critic이 산문 후보의 장면 계약, 상태 연속성, 정본 설정 준수를 독립 검증한다.
3. 통과한 산문만 정본으로 승격하고 실패 범위는 프롬프트나 하네스를 조정해 재생성한다.

## 실행 명령

```powershell
python -m unittest discover -s tests -v
python pipeline\generate_candidate.py
python pipeline\validate_canon.py runs\candidate
python pipeline\validate_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
```

실제 생성과 검증 전에 `.env`에 `GENERATOR_MODEL`과 `CRITIC_MODEL`을 명시해야 한다.
