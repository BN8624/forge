# Forge 핸드오프

## 현재 상태

Forge가 기존 세계관을 5권 126개 장면의 장편 구조로 확장하고 critic 독립 검증 뒤 정본으로 승격했다. 전권 126개 장면, 391,363자의 산문 생성·승인이 완료됐다.

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
- 권별 최소 20개 장면·8만 자 장편 규모 검증과 권별 확장 재개 캐시.
- 장면 산문의 목표·상태·요소·공개 순서·정본·연속성·품질 critic 검증.
- 현재 장면의 `end_state`를 절대 종료선으로 강제해 다음 장면의 장소·행동·감정 선취를 차단.
- objective 어휘가 미래 금지 요소와 겹칠 때 미래 기능을 사용하지 않고 다른 원인으로 목표를 달성하도록 강제.
- V1 Tailscale 모바일 뷰어와 iPhone 도서 앱용 EPUB 내보내기.
- V1-V5 전권 Tailscale 모바일 서재와 권별 EPUB 다운로드.
- 숨은 모델 폴백 제거.
- Atelier 핵심 자료의 읽기 전용 참고 사본 보관.

## 현재 완료 상태

1. V1-V5 전 장면이 critic 승인을 통과했다.
2. 모든 산문과 review 해시가 일치한다.
3. 전권 EPUB과 Tailscale 모바일 서재가 제공된다.

## 실행 명령

```powershell
python pipeline\complete_series.py
python -m unittest discover -s tests -v
python pipeline\generate_candidate.py
python pipeline\validate_canon.py runs\candidate
python pipeline\validate_structure.py
python pipeline\validate_scale.py
python pipeline\expand_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
python pipeline\generate_prose.py
python pipeline\export_epub.py --volume V1
python pipeline\serve_prose.py --host 100.89.73.83 --port 8765
```

실제 생성과 검증 전에 `.env`에 `GENERATOR_MODEL`과 `CRITIC_MODEL`을 명시해야 한다.
일상적인 완주와 재개는 `complete_series.py` 한 명령만 사용하며 개별 단계
명령은 진단 또는 특정 단계 재현용이다.
