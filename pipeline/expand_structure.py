# Forge가 승인된 정본을 권별 장편 사건·장면 구조로 확장하는 도구
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIB_ROOT = PROJECT_ROOT / "lib"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.jsonutil import extract_json
from pipeline.generate_candidate import (
    materialize_bundle,
    normalize_previous_scene_ids,
    normalize_setup_order,
    normalize_state_continuity,
    publish_candidate,
)
from pipeline.generate_candidate import load_source_material
from pipeline.validate_scale import (
    MIN_VOLUME_SCENES,
    MIN_VOLUME_TARGET_CHARS,
    validate_story_scale,
)
from pipeline.validate_canon import story_sha256
from pipeline.validate_structure import OWNER_KEYS, validate_project, validate_schema


MIN_EVENTS_PER_VOLUME = 4
MIN_SCENE_TARGET_CHARS = 2_000
MAX_SCENE_TARGET_CHARS = 5_000
MAX_EXPANSION_ATTEMPTS = 3


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class StructureExpansionError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def expansion_contract_sha256(root: Path, instruction: str) -> str:
    digest = hashlib.sha256()
    digest.update(story_sha256(root).encode("ascii"))
    digest.update(b"\0")
    digest.update(instruction.strip().encode("utf-8"))
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_story_bundle(root: Path) -> dict[str, Any]:
    story = root / "story"
    return {
        "series": read_json(story / "series.json"),
        "volumes": [
            read_json(path)
            for path in sorted((story / "volumes").glob("*.json"))
        ],
        "events": [
            read_json(path)
            for path in sorted((story / "events").glob("*.json"))
        ],
        "scenes": [
            read_json(path)
            for path in sorted((story / "scenes").glob("*.json"))
        ],
    }


def documents_by_id(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {document["id"]: document for document in documents}


def current_volume_context(
    bundle: dict[str, Any],
    volume_id: str,
) -> dict[str, Any]:
    volumes = documents_by_id(bundle["volumes"])
    events = documents_by_id(bundle["events"])
    scenes = documents_by_id(bundle["scenes"])
    volume = volumes[volume_id]
    volume_events = [events[event_id] for event_id in volume["event_ids"]]
    volume_scenes = [
        scenes[scene_id]
        for event in volume_events
        for scene_id in event["scene_ids"]
    ]
    owned_ids: list[str] = []
    for scene in volume_scenes:
        for owner_key in OWNER_KEYS:
            owned_ids.extend(scene["owns"][owner_key])
    return {
        "volume": volume,
        "events": volume_events,
        "scenes": volume_scenes,
        "owned_element_ids": owned_ids,
    }


def build_expansion_prompt(
    bundle: dict[str, Any],
    volume_id: str,
    instruction: str,
) -> str:
    canon, manuscript = load_source_material()
    context = current_volume_context(bundle, volume_id)
    series = bundle["series"]
    element_by_id = {
        element["id"]: element
        for element in series["elements"]
    }
    owned_elements = [
        element_by_id[element_id]
        for element_id in context["owned_element_ids"]
    ]
    setup_elements = [
        element
        for element in series["elements"]
        if element["kind"] == "setup"
    ]
    volume_summaries = [
        {
            "id": volume["id"],
            "title": volume["title"],
            "objective": volume["objective"],
            "start_state": volume["start_state"],
            "end_state": volume["end_state"],
        }
        for volume in bundle["volumes"]
    ]
    return f"""너는 Forge의 장편 구조 확장 generator다.
현재 {len(bundle["series"]["volume_ids"])}권 정본 중 {volume_id} 한 권만 장편 규모로 확장하라.
이야기의 사실과 결말을 바꾸지 말고 기존 권의 핵심 사건을 세분화하라.

정본 설정과 확정 사건:
{json.dumps(canon, ensure_ascii=False, indent=2)}

기존 압축 원고:
{manuscript}

전체 권별 아크:
{json.dumps(volume_summaries, ensure_ascii=False, indent=2)}

확장할 현재 권 계약과 기존 앵커 장면:
{json.dumps(context, ensure_ascii=False, indent=2)}

이 권이 정확히 한 번 소유해야 하는 이야기 요소:
{json.dumps(owned_elements, ensure_ascii=False, indent=2)}

consumes_setups에 사용할 수 있는 전체 setup 요소:
{json.dumps(setup_elements, ensure_ascii=False, indent=2)}

추가 사용자 지시:
{instruction.strip() or "없음"}

반환 형식은 설명이나 코드펜스 없는 JSON 객체 하나다.
최상위 키는 volume, events, scenes만 사용한다.

구조 요구 사항:
- 사건은 최소 {MIN_EVENTS_PER_VOLUME}개다.
- 장면은 최소 {MIN_VOLUME_SCENES}개다.
- 장면별 target_chars는 {MIN_SCENE_TARGET_CHARS}자 이상
  {MAX_SCENE_TARGET_CHARS}자 이하다.
- 권 전체 target_chars 합계는 최소 {MIN_VOLUME_TARGET_CHARS}자다.
- 여행, 조사, 갈등, 선택, 실패, 회복, 후폭풍을 인과적으로 확장하되
  분량 채우기용 반복 장면은 만들지 않는다.
- 각 장면 objective는 즉시 목표, 방해·갈등, 구체적 행동이나 선택,
  관찰 가능한 결과를 포함한다. 묘사·설명·소개·학습만으로 끝내지 않는다.
- 설정 공개는 인물 사이의 요구와 저항, 거래, 추궁, 실패, 전투,
  구조 행동의 결과로 일어나야 한다. 정보 전달만을 위한 정지 장면이나
  두 장면 연속 회상·관찰·설명 장면을 만들지 않는다.
- 대화 가능한 인물이 함께 있는 장면은 관계나 전술을 바꾸는 대화가
  일어나도록 objective에 이해관계 충돌을 포함한다.
- 각 장면은 interaction_mode와 dialogue_policy를 실제 장면 작동 방식에 맞게
  결정한다. interaction_mode는 solo, covert, interpersonal, group 중 하나다.
  dialogue_policy는 none, optional, required 중 하나다.
- 협상, 추궁, 관계 변화, 명령 불복, 이해관계 충돌이 장면 결과를 만드는
  경우에만 required를 사용한다. 혼자 행동하거나 들키지 않는 것이 핵심인
  잠입·은신·정찰 장면은 none 또는 optional로 둔다.
- 인물이 둘 이상 등장한다는 이유만으로 required를 사용하지 않는다.
- 기존 권 목표와 시작·종료 역할을 유지하고 다음 권 사건을 선취하지 않는다.
- event ID는 {volume_id}-E01부터 연속 순번을 사용한다.
- scene ID는 각 사건의 S01부터 연속 순번을 사용한다.
- 위 이야기 요소 ID만 owns에 넣고 각각 정확히 한 번 소유한다.
- 다른 권의 요소나 새 요소 ID를 만들지 않는다.
- consumes_setups는 위 목록의 setup ID만 사용한다. C1-C21 같은 canon ID,
  change ID, payoff ID는 절대 넣지 않는다. 해당 setup을 실제로 회수하거나
  소비하지 않는 장면은 빈 배열을 사용한다.
- start_state, 상위 end_state, previous_scene_id는 조립 단계에서 Forge가
  정규화하므로 유효한 객체와 필드는 제공하되 복제 정확성에 집착하지 않는다.
"""


def build_retry_prompt(
    original_prompt: str,
    previous_response: str,
    errors: list[str],
) -> str:
    return f"""{original_prompt}

직전 권별 확장 응답은 하네스 검증에 실패했다.
아래 오류를 모두 해결한 volume, events, scenes 전체 JSON 객체를 다시 반환하라.
부분 패치나 설명은 반환하지 않는다.

권별 확장 오류:
{json.dumps(errors, ensure_ascii=False, indent=2)}

직전 응답:
{previous_response}
"""


def require_volume_response(value: Any) -> dict[str, Any]:
    required_keys = {"volume", "events", "scenes"}
    allowed_keys = {*required_keys, "owned_element_ids"}
    if (
        not isinstance(value, dict)
        or not required_keys.issubset(value)
        or not set(value).issubset(allowed_keys)
    ):
        raise StructureExpansionError(
            "권별 응답은 volume, events, scenes와 선택적 "
            "owned_element_ids만 가진 객체여야 함"
        )
    if not isinstance(value["volume"], dict):
        raise StructureExpansionError("volume은 객체여야 함")
    if not isinstance(value["events"], list) or not isinstance(value["scenes"], list):
        raise StructureExpansionError("events와 scenes는 배열이어야 함")
    return value


def owned_element_ids(scenes: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for scene in scenes:
        owns = scene.get("owns", {})
        if not isinstance(owns, dict):
            continue
        for owner_key in OWNER_KEYS:
            values = owns.get(owner_key, [])
            if isinstance(values, list):
                result.extend(
                    value
                    for value in values
                    if isinstance(value, str)
                )
    return result


def normalize_consumed_setups(
    response: dict[str, Any],
    setup_ids: set[str],
) -> None:
    for scene in response["scenes"]:
        if not isinstance(scene, dict):
            continue
        consumed = scene.get("consumes_setups")
        if not isinstance(consumed, list):
            continue
        scene["consumes_setups"] = [
            setup_id
            for setup_id in consumed
            if setup_id in setup_ids
        ]


def validate_volume_response(
    response: dict[str, Any],
    volume_id: str,
    volume_index: int,
    expected_owned_ids: list[str],
    setup_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    volume = response["volume"]
    validate_schema(
        volume,
        "volume.schema.json",
        volume_id,
        errors,
    )
    if (
        volume.get("id") != volume_id
        or volume.get("index") != volume_index
        or volume.get("series_id") != "SERIES"
    ):
        errors.append(f"권 식별자, 순번 또는 series_id 불일치: {volume_id}")

    events = response["events"]
    scenes = response["scenes"]
    if len(events) < MIN_EVENTS_PER_VOLUME:
        errors.append(
            f"사건 수 부족: {volume_id} "
            f"{len(events)}개/{MIN_EVENTS_PER_VOLUME}개"
        )
    if len(scenes) < MIN_VOLUME_SCENES:
        errors.append(
            f"장면 수 부족: {volume_id} "
            f"{len(scenes)}개/{MIN_VOLUME_SCENES}개"
        )

    event_by_id: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            errors.append(f"events[{index - 1}]는 객체여야 함")
            continue
        event_id = event.get("id")
        validate_schema(
            event,
            "event.schema.json",
            str(event_id),
            errors,
        )
        expected_id = f"{volume_id}-E{index:02d}"
        if (
            event_id != expected_id
            or event.get("volume_id") != volume_id
            or event.get("sequence") != index
        ):
            errors.append(f"사건 ID, 소속 또는 순번 불일치: {event_id}")
        if event_id in event_by_id:
            errors.append(f"사건 ID 중복: {event_id}")
        elif isinstance(event_id, str):
            event_by_id[event_id] = event

    if volume.get("event_ids") != [
        f"{volume_id}-E{index:02d}"
        for index in range(1, len(events) + 1)
    ]:
        errors.append(f"volume.event_ids가 사건 배열 순서와 다름: {volume_id}")

    scene_by_id: dict[str, dict[str, Any]] = {}
    total_chars = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            errors.append("scenes 항목은 객체여야 함")
            continue
        scene_id = scene.get("id")
        validate_schema(
            scene,
            "scene.schema.json",
            str(scene_id),
            errors,
        )
        if scene_id in scene_by_id:
            errors.append(f"장면 ID 중복: {scene_id}")
        elif isinstance(scene_id, str):
            scene_by_id[scene_id] = scene
        target_chars = scene.get("target_chars")
        if isinstance(target_chars, int):
            total_chars += target_chars
            if not MIN_SCENE_TARGET_CHARS <= target_chars <= MAX_SCENE_TARGET_CHARS:
                errors.append(
                    f"장면 목표 분량 범위 위반: {scene_id} {target_chars}자"
                )
        for setup_id in scene.get("consumes_setups", []):
            if setup_id not in setup_ids:
                errors.append(
                    f"정의되지 않은 setup 소비: {scene_id} -> {setup_id}"
                )

    for event_index, event_id in enumerate(volume.get("event_ids", []), start=1):
        event = event_by_id.get(event_id)
        if event is None:
            continue
        expected_scene_ids = [
            f"{event_id}-S{index:02d}"
            for index in range(1, len(event.get("scene_ids", [])) + 1)
        ]
        if event.get("scene_ids") != expected_scene_ids:
            errors.append(f"scene_ids가 연속 순번이 아님: {event_id}")
        for scene_index, scene_id in enumerate(event.get("scene_ids", []), start=1):
            scene = scene_by_id.get(scene_id)
            if scene is None:
                errors.append(f"사건이 없는 장면을 참조함: {event_id} -> {scene_id}")
                continue
            if (
                scene.get("event_id") != event_id
                or scene.get("sequence") != scene_index
            ):
                errors.append(f"장면 소속 또는 순번 불일치: {scene_id}")

    referenced_scene_ids = {
        scene_id
        for event in events
        if isinstance(event, dict)
        for scene_id in event.get("scene_ids", [])
    }
    if referenced_scene_ids != set(scene_by_id):
        errors.append(f"사건 참조와 장면 배열 집합 불일치: {volume_id}")

    if total_chars < MIN_VOLUME_TARGET_CHARS:
        errors.append(
            f"권 목표 분량 부족: {volume_id} "
            f"{total_chars}자/{MIN_VOLUME_TARGET_CHARS}자"
        )

    actual_owned_ids = owned_element_ids(
        [scene for scene in scenes if isinstance(scene, dict)]
    )
    if (
        len(actual_owned_ids) != len(set(actual_owned_ids))
        or sorted(actual_owned_ids) != sorted(expected_owned_ids)
    ):
        errors.append(
            f"권 이야기 요소 소유권 집합 불일치: {volume_id} "
            f"기대 {sorted(expected_owned_ids)}, 실제 {sorted(actual_owned_ids)}"
        )
    declared_owned_ids = response.get("owned_element_ids")
    if declared_owned_ids is not None and (
        not isinstance(declared_owned_ids, list)
        or sorted(declared_owned_ids) != sorted(expected_owned_ids)
    ):
        errors.append(
            f"응답 owned_element_ids 불일치: {volume_id} "
            f"기대 {sorted(expected_owned_ids)}, 실제 {declared_owned_ids}"
        )
    return errors


def generate_expanded_volume(
    llm: LLM,
    bundle: dict[str, Any],
    volume_id: str,
    instruction: str,
    failure_dir: Path | None = None,
) -> dict[str, Any]:
    context = current_volume_context(bundle, volume_id)
    expected_owned_ids = context["owned_element_ids"]
    setup_ids = {
        element["id"]
        for element in bundle["series"]["elements"]
        if element["kind"] == "setup"
    }
    volume_index = bundle["series"]["volume_ids"].index(volume_id) + 1
    original_prompt = build_expansion_prompt(
        bundle,
        volume_id,
        instruction,
    )
    prompt = original_prompt
    last_errors: list[str] = []
    for attempt in range(1, MAX_EXPANSION_ATTEMPTS + 1):
        response_text = llm.generate("generator", prompt, temperature=0.7)
        try:
            value = extract_json(response_text)
            response = require_volume_response(value)
            normalize_consumed_setups(response, setup_ids)
            last_errors = validate_volume_response(
                response,
                volume_id,
                volume_index,
                expected_owned_ids,
                setup_ids,
            )
            if last_errors:
                raise StructureExpansionError(last_errors)
            return response
        except StructureExpansionError as exc:
            last_errors = exc.errors
            if failure_dir is not None:
                failure_dir.mkdir(parents=True, exist_ok=True)
                (failure_dir / f"{volume_id}-attempt-{attempt}.txt").write_text(
                    response_text,
                    encoding="utf-8",
                )
            if attempt == MAX_EXPANSION_ATTEMPTS:
                raise StructureExpansionError(
                    [
                        f"{volume_id} 장편 확장 {MAX_EXPANSION_ATTEMPTS}회 실패",
                        *last_errors,
                    ]
                ) from exc
            prompt = build_retry_prompt(
                original_prompt,
                response_text,
                last_errors,
            )
    raise StructureExpansionError(last_errors)


def expand_structure(
    root: Path,
    output: Path,
    llm: LLM,
    instruction: str = "",
) -> Path:
    root = root.resolve()
    output = output.resolve()
    structure_errors = validate_project(root)
    if structure_errors:
        raise StructureExpansionError(structure_errors)
    source_bundle = load_story_bundle(root)
    work_dir = (
        output.parent
        / "expansion-work"
        / expansion_contract_sha256(root, instruction)
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    expanded_bundle = {
        "series": source_bundle["series"],
        "volumes": [],
        "events": [],
        "scenes": [],
    }

    for volume_id in source_bundle["series"]["volume_ids"]:
        cache_path = work_dir / f"{volume_id}.json"
        response: dict[str, Any] | None = None
        context = current_volume_context(source_bundle, volume_id)
        setup_ids = {
            element["id"]
            for element in source_bundle["series"]["elements"]
            if element["kind"] == "setup"
        }
        volume_index = (
            source_bundle["series"]["volume_ids"].index(volume_id) + 1
        )
        if cache_path.exists():
            try:
                cached = require_volume_response(
                    json.loads(cache_path.read_text(encoding="utf-8"))
                )
                cache_errors = validate_volume_response(
                    cached,
                    volume_id,
                    volume_index,
                    context["owned_element_ids"],
                    setup_ids,
                )
                if not cache_errors:
                    response = cached
            except (OSError, json.JSONDecodeError, StructureExpansionError):
                response = None
        if response is None:
            failure_paths = sorted(
                (work_dir / "failures").glob(f"{volume_id}-attempt-*.txt"),
                reverse=True,
            )
            for failure_path in failure_paths:
                try:
                    recovered = require_volume_response(
                        json.loads(failure_path.read_text(encoding="utf-8"))
                    )
                    recovery_errors = validate_volume_response(
                        recovered,
                        volume_id,
                        volume_index,
                        context["owned_element_ids"],
                        setup_ids,
                    )
                    if not recovery_errors:
                        response = recovered
                        cache_path.write_text(
                            json.dumps(
                                recovered,
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        break
                except (
                    OSError,
                    json.JSONDecodeError,
                    StructureExpansionError,
                ):
                    continue
        if response is None:
            response = generate_expanded_volume(
                llm,
                source_bundle,
                volume_id,
                instruction,
                failure_dir=work_dir / "failures",
            )
            cache_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        expanded_bundle["volumes"].append(response["volume"])
        expanded_bundle["events"].extend(response["events"])
        expanded_bundle["scenes"].extend(response["scenes"])

    normalize_state_continuity(expanded_bundle)
    normalize_previous_scene_ids(expanded_bundle)
    normalize_setup_order(expanded_bundle)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".expanded-", dir=output.parent))
    staged_candidate = staging_root / "candidate"
    preserve_staging = False
    try:
        materialize_bundle(expanded_bundle, staged_candidate)
        errors = validate_project(staged_candidate, check_ledger=False)
        errors.extend(validate_story_scale(staged_candidate))
        if errors:
            raise StructureExpansionError(errors)
        try:
            publish_candidate(staged_candidate, output)
        except Exception as exc:
            if "복구가 모두 실패" in str(exc):
                preserve_staging = True
            if isinstance(exc, StructureExpansionError):
                raise
            raise StructureExpansionError(str(exc)) from exc
    finally:
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)
    return output


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forge 정본을 권별 장편 구조 후보로 확장한다."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "runs" / "expanded-candidate",
    )
    parser.add_argument("--instruction-file", type=Path)
    args = parser.parse_args()
    try:
        instruction = (
            args.instruction_file.read_text(encoding="utf-8")
            if args.instruction_file
            else ""
        )
        expand_structure(
            args.root,
            args.output,
            create_llm_client(),
            instruction,
        )
    except (StructureExpansionError, OSError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[OK] 장편 구조 확장 후보 생성 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
