# Forge 핸드오프

## 현재 상태

Forge 독립 프로젝트 생성, 구조 검증기, 후보 정본 승격, 상태 원장 재구성 파이프라인 구현이 끝났다. 아직 모델 호출이나 신규 원고 생성은 시작하지 않았다.

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
- 숨은 모델 폴백 제거.
- Atelier 핵심 자료의 읽기 전용 참고 사본 보관.

## 다음 구현 순서

1. 구조 문서 후보 생성기를 구현한다.
2. 5권 전체의 신규 시리즈 초안을 구조 문서로 생성한다.
3. 권, 사건, 장면 순서로 세분화한 뒤 산문 생성을 시작한다.

## 실행 명령

```powershell
python -m unittest discover -s tests -v
python pipeline\validate_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
```

현재 `story/series.json`이 없으므로 두 번째 명령은 신규 구조 문서를 만든 뒤 사용하는 명령이다.
