# Forge

5권 장편소설을 구조 문서에서 산문까지 자동 생산하기 위한 독립 워크플로우다.

공개 저장소는 <https://github.com/BN8624/forge>다.

## 정본 계층

1. `story/series.json`
2. `story/volumes/V1.json`
3. `story/events/V1-E01.json`
4. `story/scenes/V1-E01-S01.json`
5. `story/canon-review.json`
6. `state/current.json`
7. 검증을 통과한 산문

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
python pipeline\validate_canon.py runs\candidate
python pipeline\validate_structure.py
python pipeline\validate_scale.py
python pipeline\expand_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
python pipeline\generate_prose.py
python pipeline\generate_prose.py --all
python pipeline\serve_prose.py --volume V1 --host 100.89.73.83 --port 8765
```

후보 생성과 독립 검증에는 `.env`의 `GOOGLE_API_KEY` 계열, `GENERATOR_MODEL`, `CRITIC_MODEL` 설정이 필요하다. Forge는 `reference/legacy/canon_bible.json`과 `compressed_manuscript.md`를 자동 입력으로 사용하며 기본 출력은 `runs/candidate`다.

승격 후보 경로에는 완전한 `story` 디렉터리와 최신 `canon-review.json` 승인이 있어야 한다. 구조 또는 정본 검증 실패 시 현재 정본은 변경되지 않는다.

후보 승격 뒤에는 상태 원장을 재구성한다. 같은 구조에서 재구성한 `state/current.json`은 항상 같은 바이트를 생성한다.

산문 생성 명령은 아직 승인된 산문이 없는 첫 장면을 자동 선택한다. 특정 장면 ID를 인자로 줄 수 있지만 이전 장면들의 승인된 정본 산문이 모두 있어야 한다.
`--all`은 남은 장면을 순서대로 계속 생성하며, `--limit N`으로 한 실행의 장면 수를 제한할 수 있다.
산문 읽기 서버는 지정한 권의 승인된 산문만 정본 순서대로 제공한다. Tailscale IP에 바인딩하면 같은 tailnet 기기에서만 접근할 수 있다.
산문 생성 전 각 권의 장면 목표 분량 합계가 최소 8만 자인지 검사한다.
현재 정본이 규모 미달이면 `expand_structure.py`가 권별로 사건·장면을 확장해 `runs/expanded-candidate`에 게시한다. 이 후보도 critic 정본 검증을 다시 통과해야 승격할 수 있다.
