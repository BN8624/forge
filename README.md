# Forge

5권 장편소설을 구조 문서에서 산문까지 자동 생산하기 위한 독립 워크플로우다.

공개 저장소는 <https://github.com/BN8624/forge>다.

## 정본 계층

1. `story/series.json`
2. `story/volumes/V1.json`
3. `story/events/V1-E01.json`
4. `story/scenes/V1-E01-S01.json`
5. `state/current.json`
6. 검증을 통과한 산문

상위 문서는 하위 문서의 계약을 정의한다. 하위 문서는 상위 계약을 임의로 변경할 수 없다.

## 기본 파이프라인

1. 시작부터 결말까지의 5권 시리즈 구조를 작성한다.
2. 각 권을 큰 사건으로 분해한다.
3. 큰 사건을 세부 사건과 장면으로 분해한다.
4. 소유권, 상태 연속성, 복선과 회수 관계를 정적 검증한다.
5. 앞 장면의 정본 산문과 현재 상태 원장을 참고해 다음 장면 후보를 생성한다.
6. 후보를 독립 검증하고 통과한 결과만 정본으로 승격한다.
7. 승격된 장면의 효과만 상태 원장에 반영한다.

`reference/legacy`는 Atelier에서 가져온 참고자료이며 Forge의 정본이 아니다.

## 실행 명령

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python pipeline\generate_candidate.py
python pipeline\validate_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
```

후보 생성에는 `.env`의 `GOOGLE_API_KEY` 계열과 `GENERATOR_MODEL` 설정이 필요하다. Forge는 `reference/legacy/canon_bible.json`과 `compressed_manuscript.md`를 자동 입력으로 사용하며 기본 출력은 `runs/candidate`다.

승격 후보 경로에는 완전한 `story` 디렉터리가 있어야 한다. 후보는 임시 스냅샷에서 독립 검증되며 검증 실패 시 현재 정본은 변경되지 않는다.

후보 승격 뒤에는 상태 원장을 재구성한다. 같은 구조에서 재구성한 `state/current.json`은 항상 같은 바이트를 생성한다.
