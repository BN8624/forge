# 기존 정본 자료에서 검증 가능한 5권 구조 문서 후보를 생성하는 도구
from __future__ import annotations

import argparse
import json
import os
import re
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
from pipeline.validate_structure import validate_project


DOCUMENT_IDS = {
    "volumes": re.compile(r"^V[1-5]$"),
    "events": re.compile(r"^V[1-5]-E\d{2}$"),
    "scenes": re.compile(r"^V[1-5]-E\d{2}-S\d{2}$"),
}
CANON_PATH = PROJECT_ROOT / "reference" / "legacy" / "canon_bible.json"
MANUSCRIPT_PATH = PROJECT_ROOT / "reference" / "legacy" / "compressed_manuscript.md"
MAX_GENERATION_ATTEMPTS = 3


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class CandidateGenerationError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def load_schema_text(schema_name: str) -> str:
    path = PROJECT_ROOT / "schemas" / schema_name
    return path.read_text(encoding="utf-8")


def load_source_material() -> tuple[dict[str, Any], str]:
    try:
        canon = json.loads(CANON_PATH.read_text(encoding="utf-8"))
        manuscript = MANUSCRIPT_PATH.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateGenerationError(f"기존 세계관 자료 로드 실패: {exc}") from exc
    if not isinstance(canon, dict) or not isinstance(canon.get("canon"), list):
        raise CandidateGenerationError("canon_bible.json 형식이 올바르지 않음")
    if not manuscript.strip():
        raise CandidateGenerationError("compressed_manuscript.md가 비어 있음")
    return canon, manuscript


def build_prompt(instruction: str = "") -> str:
    canon, manuscript = load_source_material()
    schemas = {
        name: json.loads(load_schema_text(name))
        for name in (
            "series.schema.json",
            "volume.schema.json",
            "event.schema.json",
            "scene.schema.json",
        )
    }
    optional_instruction = instruction.strip() or "추가 지시 없음"
    return f"""아래 기존 작품을 정확히 5권인 장편소설 구조로 재설계하라.

정본 설정과 확정 사건:
{json.dumps(canon, ensure_ascii=False, indent=2)}

기존 압축 원고:
{manuscript}

추가 사용자 지시:
{optional_instruction}

반환 형식은 설명이나 코드펜스 없는 JSON 객체 하나다.
최상위 키는 series, volumes, events, scenes만 사용한다.
series는 객체이며 나머지는 객체 배열이다.
모든 권, 사건, 장면 문서를 빠짐없이 포함하고 ID와 참조를 일치시킨다.
각 이야기 요소는 정확히 한 장면의 owns가 소유해야 한다.
복선 setup은 이를 회수하는 payoff보다 앞선 장면에 배치한다.
모든 start_state와 end_state를 계층과 장면 순서에 맞게 연결한다.
canon_bible.json의 C1-C21은 모두 유지하며 충돌하는 설정을 만들지 않는다.
compressed_manuscript.md의 기존 인물, 세계관, 사건, 결말을 유지한다.
기존 10권 설계는 입력이 아니며 5권별 사건 배치는 스스로 새로 결정한다.

문서 스키마:
{json.dumps(schemas, ensure_ascii=False, indent=2)}
"""


def build_retry_prompt(
    original_prompt: str,
    previous_response: str,
    errors: list[str],
) -> str:
    return f"""{original_prompt}

직전 응답은 구조 검증에 실패했다.
아래 오류를 모두 해결한 완전한 JSON 번들을 처음부터 다시 반환하라.
부분 수정, 패치, 설명은 반환하지 않는다.

구조 검증 오류:
{json.dumps(errors, ensure_ascii=False, indent=2)}

직전 응답:
{previous_response}
"""


def require_bundle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateGenerationError("모델 응답의 최상위 값은 객체여야 함")
    expected_keys = {"series", "volumes", "events", "scenes"}
    if set(value) != expected_keys:
        raise CandidateGenerationError(
            "모델 응답 최상위 키는 series, volumes, events, scenes여야 함"
        )
    if not isinstance(value["series"], dict):
        raise CandidateGenerationError("series는 객체여야 함")
    for key in ("volumes", "events", "scenes"):
        if not isinstance(value[key], list):
            raise CandidateGenerationError(f"{key}는 배열이어야 함")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def materialize_bundle(bundle: dict[str, Any], root: Path) -> None:
    write_json(root / "story" / "series.json", bundle["series"])

    directory_names = {
        "volumes": "volumes",
        "events": "events",
        "scenes": "scenes",
    }
    for collection, pattern in DOCUMENT_IDS.items():
        seen: set[str] = set()
        for index, document in enumerate(bundle[collection]):
            if not isinstance(document, dict):
                raise CandidateGenerationError(
                    f"{collection}[{index}]는 객체여야 함"
                )
            document_id = document.get("id")
            if not isinstance(document_id, str) or not pattern.fullmatch(document_id):
                raise CandidateGenerationError(
                    f"안전하지 않거나 잘못된 {collection} ID: {document_id!r}"
                )
            if document_id in seen:
                raise CandidateGenerationError(
                    f"{collection} 문서 ID 중복: {document_id}"
                )
            seen.add(document_id)
            write_json(
                root / "story" / directory_names[collection] / f"{document_id}.json",
                document,
            )


def documents_by_id(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        document["id"]: document
        for document in documents
        if isinstance(document, dict) and isinstance(document.get("id"), str)
    }


def normalize_state_continuity(bundle: dict[str, Any]) -> None:
    volumes = documents_by_id(bundle["volumes"])
    events = documents_by_id(bundle["events"])
    scenes = documents_by_id(bundle["scenes"])
    previous_state: Any = None

    for volume_index, volume_id in enumerate(bundle["series"].get("volume_ids", [])):
        volume = volumes.get(volume_id)
        if volume is None:
            continue
        first_event_state: Any = None
        last_event_state: Any = None

        for event_id in volume.get("event_ids", []):
            event = events.get(event_id)
            if event is None:
                continue
            first_scene_state: Any = None
            last_scene_state: Any = None

            for scene_id in event.get("scene_ids", []):
                scene = scenes.get(scene_id)
                if scene is None:
                    continue
                if previous_state is None:
                    previous_state = scene.get(
                        "start_state",
                        volume.get("start_state", {}),
                    )
                scene["start_state"] = previous_state
                first_scene_state = (
                    scene["start_state"]
                    if first_scene_state is None
                    else first_scene_state
                )
                last_scene_state = scene.get("end_state", {})
                previous_state = last_scene_state

            if first_scene_state is not None:
                event["start_state"] = first_scene_state
                event["end_state"] = last_scene_state
                first_event_state = (
                    first_scene_state
                    if first_event_state is None
                    else first_event_state
                )
                last_event_state = last_scene_state

        if first_event_state is not None:
            volume["start_state"] = first_event_state
            volume["end_state"] = last_event_state


def normalize_previous_scene_ids(bundle: dict[str, Any]) -> None:
    previous_scene_id: str | None = None
    for scene in ordered_scenes(bundle):
        scene["previous_scene_id"] = previous_scene_id
        previous_scene_id = scene["id"]


def ordered_scenes(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    volumes = documents_by_id(bundle["volumes"])
    events = documents_by_id(bundle["events"])
    scenes = documents_by_id(bundle["scenes"])
    result: list[dict[str, Any]] = []
    for volume_id in bundle["series"].get("volume_ids", []):
        volume = volumes.get(volume_id, {})
        for event_id in volume.get("event_ids", []):
            event = events.get(event_id, {})
            for scene_id in event.get("scene_ids", []):
                scene = scenes.get(scene_id)
                if scene is not None:
                    result.append(scene)
    return result


def normalize_setup_order(bundle: dict[str, Any]) -> None:
    scenes = ordered_scenes(bundle)
    scene_order = {scene["id"]: index for index, scene in enumerate(scenes)}
    setup_owners: dict[str, dict[str, Any]] = {}
    payoff_requirements: dict[str, list[int]] = {}

    for scene in scenes:
        for setup_id in scene.get("owns", {}).get("setups", []):
            setup_owners[setup_id] = scene
        for setup_id in scene.get("consumes_setups", []):
            payoff_requirements.setdefault(setup_id, []).append(
                scene_order[scene["id"]]
            )

    for element in bundle["series"].get("elements", []):
        if not isinstance(element, dict) or element.get("kind") != "payoff":
            continue
        setup_id = element.get("resolves")
        payoff_id = element.get("id")
        for scene in scenes:
            if payoff_id in scene.get("owns", {}).get("payoffs", []):
                payoff_requirements.setdefault(setup_id, []).append(
                    scene_order[scene["id"]]
                )

    for setup_id, requirement_indexes in payoff_requirements.items():
        owner = setup_owners.get(setup_id)
        if owner is None or not requirement_indexes:
            continue
        first_requirement = min(requirement_indexes)
        if scene_order[owner["id"]] < first_requirement or first_requirement == 0:
            continue
        owner["owns"]["setups"].remove(setup_id)
        scenes[first_requirement - 1]["owns"]["setups"].append(setup_id)


def publish_candidate(staged_candidate: Path, output: Path) -> None:
    backup = staged_candidate.parent / "candidate.previous"
    had_output = output.exists()
    if had_output:
        os.replace(output, backup)
    try:
        os.replace(staged_candidate, output)
    except OSError as publish_error:
        if had_output:
            try:
                os.replace(backup, output)
            except OSError as rollback_error:
                raise CandidateGenerationError(
                    f"후보 게시와 기존 출력 복구가 모두 실패함: {backup}"
                ) from rollback_error
        raise CandidateGenerationError(f"후보 게시 실패: {publish_error}") from publish_error


def generate_candidate(
    instruction: str,
    output: Path,
    llm: LLM,
) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".candidate-", dir=output.parent))
    staged_candidate = staging_root / "candidate"
    preserve_staging = False

    try:
        original_prompt = build_prompt(instruction)
        prompt = original_prompt
        last_errors: list[str] = []
        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            response = llm.generate("generator", prompt, temperature=0.7)
            shutil.rmtree(staged_candidate, ignore_errors=True)
            try:
                bundle = extract_json(response)
                if not bundle:
                    raise CandidateGenerationError(
                        "모델 응답에서 JSON 객체를 추출하지 못함"
                    )
                bundle = require_bundle(bundle)
                normalize_state_continuity(bundle)
                normalize_previous_scene_ids(bundle)
                normalize_setup_order(bundle)
                materialize_bundle(bundle, staged_candidate)
                last_errors = validate_project(
                    staged_candidate,
                    check_ledger=False,
                )
                if last_errors:
                    raise CandidateGenerationError(last_errors)
            except CandidateGenerationError as exc:
                last_errors = exc.errors
                if attempt == MAX_GENERATION_ATTEMPTS:
                    raise CandidateGenerationError(
                        [
                            f"구조 후보 생성 {MAX_GENERATION_ATTEMPTS}회 실패",
                            *last_errors,
                        ]
                    ) from exc
                prompt = build_retry_prompt(
                    original_prompt,
                    response,
                    last_errors,
                )
                continue
            break

        try:
            publish_candidate(staged_candidate, output)
        except CandidateGenerationError as exc:
            if "복구가 모두 실패" in str(exc):
                preserve_staging = True
            raise
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
        description="기존 정본 자료에서 검증된 5권 구조 문서 후보를 생성한다."
    )
    parser.add_argument(
        "--instruction-file",
        type=Path,
        help="정본 자료 외에 추가할 선택적 지시 파일",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "runs" / "candidate",
    )
    args = parser.parse_args()

    try:
        instruction = (
            args.instruction_file.read_text(encoding="utf-8")
            if args.instruction_file
            else ""
        )
        generate_candidate(instruction, args.output, create_llm_client())
    except (OSError, CandidateGenerationError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] 구조 후보 생성 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
