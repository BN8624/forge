# Forge

작품별 적정 권수의 장편소설을 구조 문서에서 산문까지 자동 생산하기 위한 독립 워크플로우다.

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

1. 승인된 전체 권수의 시작부터 결말까지 시리즈 구조를 작성한다.
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
python pipeline\complete_series.py
python pipeline\complete_series.py --new-world
python pipeline\generate_candidate.py
python pipeline\validate_canon.py runs\candidate
python pipeline\validate_structure.py
python pipeline\validate_scale.py
python pipeline\expand_structure.py
python pipeline\promote_candidate.py C:\path\to\candidate
python pipeline\rebuild_state.py
python pipeline\generate_prose.py
python pipeline\generate_prose.py --all
python pipeline\export_epub.py --volume V1
python pipeline\serve_prose.py --host 100.89.73.83 --port 8765
```

## 전 과정 자동화

기본 실행은 현재 정본과 승인 산문을 검사해 완료된 단계는 건너뛰고, 다음
미완성 권의 마지막 장면까지 생성한다. 해당 권의 검증과 EPUB 생성이 끝나면
`volume_complete` 상태로 멈추며, 같은 명령을 다시 실행하면 다음 권을 만든다.

```powershell
python pipeline\complete_series.py
```

완전히 새로운 세계관부터 시작하려면 다음 명령을 사용한다. 이 모드는 기본
5권 구성을 사용하되 산문은 한 번에 한 권씩 완성한다.

```powershell
python pipeline\complete_series.py --new-world
```

이 모드는 기존 작품의 인물·장소·설정을 사용하지 않고 장르, 제목, 인물,
세계 규칙, 적대 세력, 반전, 결말, 21개 검증 정본, 압축 참고 원고를 먼저
생성한다. 이후 구조 생성부터 EPUB까지 같은 실행에서 이어간다. 기존
`story`, `prose`, `state`, `exports`, 생성 세계관 원천은
`runs/world-backups`에 보존된다.

중간 실패나 프로세스 종료 뒤 같은 `--new-world` 명령을 다시 실행하면
진행 중인 신규 세계관과 승인 산문에서 재개한다. 완주 뒤 다시 실행하면 또
다른 신규 세계관을 시작한다. 장면 생성 실행은 모든 모드에서 기본 5회까지
재시도하며, 실패가 반복되면 원인 계약을 수정할 수 있도록 중단한다.

게임 시나리오로 확장하기 좋은 소설 기획의 선택까지 Forge에 맡기려면 다음
명령을 사용한다.

```powershell
python pipeline\complete_series.py --game-scenario
```

Forge generator가 장르, 플레이어 역할, 핵심 플레이 반복, 성장, 세력,
선택 구조가 서로 다른 시놉시스 5개를 만든다. 독립 critic이 소설 완결성,
핵심 반복, 플레이어 행위성, 콘텐츠 확장성, 차별성을 평가해 하나를 선택한다.
각 후보는 추천 권수, 권수 근거, 권별 전개를 함께 제안한다. critic이 선택한
후보가 3권 이상이면 별도 승인 없이 진행하고, 1권 또는 2권이면 사용자 승인을
기다린다. 선택된 제목, 장르, 승인 권수는 이후 세계관 생성에서 변경할 수 없는
계약으로 검증된다.

권수를 직접 지정하면 Forge가 선택 시놉시스를 해당 권수에 맞춰 재설계한 뒤
즉시 확정한다.

```powershell
python pipeline\complete_series.py --game-scenario --volume-count 4
```

명령줄에서 저장된 1~2권 추천을 승인할 때는 다음 옵션을 사용한다.

```powershell
python pipeline\complete_series.py --game-scenario --reuse-concept --approve-short
```

후보 목록, critic 평가, 선택 결과는 각각
`reference/current/synopsis-candidates.json`,
`reference/current/synopsis-review.json`,
`reference/current/selected-synopsis.json`에 보존된다.

iPhone에서 후보를 읽고 직접 시작하려면 Tailscale 전권 서재의 대시보드를
연다.

```text
http://node.tail3e9e21.ts.net:8765/dashboard
```

대시보드에서 선택 지시를 입력하거나 비워 둔 채 후보 5개를 생성할 수 있다.
critic 추천, 후보별 핵심 플레이 반복, 성장, 선택 구조, 추천 권수와 권별
전개를 확인한다. 3권 이상 추천은 자동으로 첫 권 제작에 진입하고, 1~2권
추천만 승인 화면에서 멈춘다. 권수 입력란에 값을 넣으면 그 권수로 시놉시스를
재설계한다. 후보 생성은 현재 작품을 건드리지 않으므로 마음에 들 때까지
새 후보 5개를 반복 생성할 수 있다. 작품 제작은 후보를 선택하고 시작 버튼을
누른 뒤에만 진행된다. 작업은 서버의 백그라운드에서 진행되며 화면이 잠겨도
계속된다.

장르나 핵심 소재만 지정하려면 UTF-8 텍스트 파일을 전달한다.

```powershell
python pipeline\complete_series.py --new-world --instruction-file premise.txt
```

같은 사용자 지시를 후보 생성 단계부터 적용하려면 `--game-scenario`와
`--instruction-file`을 함께 사용한다.

자동 실행 순서는 구조 후보 생성, 원천 정본 critic 검증, 정본 승격, 상태 원장
재구성, 장편 규모 확장, 재검증·재승격, 현재 권 산문 생성·critic 승인, 현재
권 검증과 EPUB 생성 순서다. 마지막 권이 끝난 실행에서는 전체 구조와 산문을
다시 검증하고 전체 EPUB을 확인한다. 진행 상태는
`runs/complete-series/status.json`에 기록된다.

현재 장면이 끝난 뒤 자동 실행을 멈추려면 프로젝트 루트에
`STOP_AFTER_RUN` 빈 파일을 만든다. 다시 시작할 때 파일을 지우고 같은 명령을
실행하면 된다.

한 장면의 전체 생성 실행은 기본 5회까지 재시도한다. 일시적 모델 실패를
성공할 때까지 재시도하려면 다음처럼 실행한다.

```powershell
python pipeline\complete_series.py --scene-retries 0
```

유효한 정본 구조까지 새로 만들려는 경우에만 명시적으로
`--regenerate-structure`를 사용한다. 새 구조가 기존 구조와 다르면 현재 산문은
`runs/prose-backups`에 보존된다.

```powershell
python pipeline\complete_series.py --regenerate-structure
```

후보 생성과 독립 검증에는 `.env`의 `GOOGLE_API_KEY` 계열, `GENERATOR_MODEL`, `CRITIC_MODEL` 설정이 필요하다. 기본 모드는 `reference/current`의 생성 세계관을 우선 사용하고, 없으면 `reference/legacy` 사본을 사용한다.

승격 후보 경로에는 완전한 `story` 디렉터리와 최신 `canon-review.json` 승인이 있어야 한다. 구조 또는 정본 검증 실패 시 현재 정본은 변경되지 않는다.

후보 승격 뒤에는 상태 원장을 재구성한다. 같은 구조에서 재구성한 `state/current.json`은 항상 같은 바이트를 생성한다.

산문 생성 명령은 아직 승인된 산문이 없는 첫 장면을 자동 선택한다. 특정 장면 ID를 인자로 줄 수 있지만 이전 장면들의 승인된 정본 산문이 모두 있어야 한다.
`--all`은 남은 장면을 순서대로 계속 생성하며, `--limit N`으로 한 실행의 장면 수를 제한할 수 있다.
EPUB 내보내기는 지정한 권의 승인된 산문을 `exports/<권 ID>.epub`에 생성한다.
산문 읽기 서버는 기본적으로 완성된 전권과 권별 EPUB을 제공한다. `--volume V1`처럼 지정하면 한 권만 제공한다. Tailscale IP에 바인딩하면 같은 tailnet 기기에서만 접근할 수 있다.
산문 생성 전 각 권의 장면 목표 분량 합계가 최소 8만 자인지 검사한다.
현재 정본이 규모 미달이면 `expand_structure.py`가 권별로 사건·장면을 확장해 `runs/expanded-candidate`에 게시한다. 이 후보도 critic 정본 검증을 다시 통과해야 승격할 수 있다.
