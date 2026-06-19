# 정본 장면 계약에서 산문을 생성하고 critic 승인 뒤 원자적으로 승격하는 도구
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from pipeline.generate_candidate import load_source_material
from pipeline.validate_scale import validate_story_scale
from pipeline.validate_structure import validate_project, validate_schema


MAX_PROSE_ATTEMPTS = 3
MAX_REVIEW_ATTEMPTS = 3
MIN_LENGTH_RATIO = 0.7
MAX_LENGTH_RATIO = 1.5


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class ProseGenerationError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ordered_scene_ids(root: Path) -> list[str]:
    story = root / "story"
    series = read_json(story / "series.json")
    result: list[str] = []
    for volume_id in series["volume_ids"]:
        volume = read_json(story / "volumes" / f"{volume_id}.json")
        for event_id in volume["event_ids"]:
            event = read_json(story / "events" / f"{event_id}.json")
            result.extend(event["scene_ids"])
    return result


def prose_scene_dir(root: Path, scene_id: str) -> Path:
    return root / "prose" / "scenes" / scene_id


def contract_sha256(scene: dict[str, Any]) -> str:
    encoded = json.dumps(
        scene,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approved_prose(root: Path, scene_id: str) -> str:
    directory = prose_scene_dir(root, scene_id)
    prose_path = directory / "prose.md"
    review_path = directory / "review.json"
    try:
        prose = prose_path.read_text(encoding="utf-8")
        review = read_json(review_path)
        scene = read_json(root / "story" / "scenes" / f"{scene_id}.json")
    except (OSError, json.JSONDecodeError) as exc:
        raise ProseGenerationError(
            f"이전 정본 산문 또는 검토를 읽을 수 없음: {scene_id}: {exc}"
        ) from exc

    errors: list[str] = []
    validate_schema(
        review,
        "prose_review.schema.json",
        f"{scene_id}/review.json",
        errors,
    )
    prose_hash = hashlib.sha256(prose.encode("utf-8")).hexdigest()
    if review.get("status") != "pass":
        errors.append(f"이전 산문 critic 미승인: {scene_id}")
    if review.get("prose_sha256") != prose_hash:
        errors.append(f"이전 산문 해시 불일치: {scene_id}")
    if review.get("scene_contract_sha256") != contract_sha256(scene):
        errors.append(f"이전 산문의 장면 계약이 변경됨: {scene_id}")
    if errors:
        raise ProseGenerationError(errors)
    return prose


def select_scene(root: Path, requested_scene_id: str | None) -> str:
    scene_ids = ordered_scene_ids(root)
    if requested_scene_id is None:
        for scene_id in scene_ids:
            if not prose_scene_dir(root, scene_id).exists():
                return scene_id
        raise ProseGenerationError("모든 장면의 정본 산문이 이미 존재함")
    if requested_scene_id not in scene_ids:
        raise ProseGenerationError(f"정본에 없는 장면 ID: {requested_scene_id}")
    return requested_scene_id


def load_scene_context(root: Path, scene_id: str) -> dict[str, Any]:
    story = root / "story"
    scene = read_json(story / "scenes" / f"{scene_id}.json")
    event = read_json(story / "events" / f"{scene['event_id']}.json")
    volume = read_json(story / "volumes" / f"{event['volume_id']}.json")
    series = read_json(story / "series.json")
    canon_review = read_json(story / "canon-review.json")
    relevant_canon = [
        verdict
        for verdict in canon_review["verdicts"]
        if scene_id in verdict["scene_ids"]
    ]
    return {
        "series": series,
        "volume": volume,
        "event": event,
        "scene": scene,
        "relevant_canon": relevant_canon,
    }


def previous_prose_context(root: Path, scene_id: str) -> str:
    scene_ids = ordered_scene_ids(root)
    index = scene_ids.index(scene_id)
    for earlier_scene_id in scene_ids[:index]:
        approved_prose(root, earlier_scene_id)
    if index == 0:
        return ""
    return approved_prose(root, scene_ids[index - 1])


def build_generator_prompt(
    context: dict[str, Any],
    previous_prose: str,
    feedback: list[str] | None = None,
) -> str:
    _, manuscript = load_source_material()
    scene = context["scene"]
    feedback_text = (
        json.dumps(feedback, ensure_ascii=False, indent=2)
        if feedback
        else "없음"
    )
    return f"""너는 Forge의 소설 산문 generator다.
정본 계약을 바꾸지 말고 현재 장면의 산문만 작성하라.

정본 구조 계약:
{json.dumps(context, ensure_ascii=False, indent=2)}

기존 작품의 문체와 세계관 참고 원고:
{manuscript}

직전 승인 산문:
{previous_prose or "첫 장면이므로 없음"}

직전 critic 피드백:
{feedback_text}

요구 사항:
- 장면 목표, start_state에서 end_state로의 변화, owns 요소를 산문 안에서 달성한다.
- 이후 장면의 사건을 미리 완결하지 않는다.
- 정본 설정과 직전 산문의 사실·시점·인물 상태를 유지한다.
- 요약문이나 개요가 아니라 출판 가능한 한국어 소설 산문을 작성한다.
- 목표 분량은 공백 포함 약 {scene['target_chars']}자다.
- 설명이나 코드펜스 없이 JSON 객체 하나만 반환한다.
- 반환 키는 scene_id와 prose만 사용한다.
"""


def parse_prose_response(
    response: str,
    scene_id: str,
    target_chars: int,
) -> str:
    value = extract_json(response)
    if not isinstance(value, dict) or set(value) != {"scene_id", "prose"}:
        raise ProseGenerationError(
            "산문 응답은 scene_id와 prose만 가진 JSON 객체여야 함"
        )
    if value["scene_id"] != scene_id:
        raise ProseGenerationError(
            f"산문 응답 장면 ID 불일치: {value['scene_id']!r}"
        )
    prose = value["prose"]
    if not isinstance(prose, str) or not prose.strip():
        raise ProseGenerationError("산문 응답 prose가 비어 있음")
    minimum = int(target_chars * MIN_LENGTH_RATIO)
    maximum = int(target_chars * MAX_LENGTH_RATIO)
    if not minimum <= len(prose) <= maximum:
        raise ProseGenerationError(
            f"산문 길이 범위 위반: {len(prose)}자 "
            f"(허용 {minimum}-{maximum}자)"
        )
    return prose.strip()


def build_critic_prompt(
    context: dict[str, Any],
    previous_prose: str,
    prose: str,
) -> str:
    canon, _ = load_source_material()
    return f"""너는 Forge의 독립 산문 critic이다.
산문을 수정하거나 다시 쓰지 말고 계약 준수 여부만 엄격히 판정하라.

전체 정본 설정:
{json.dumps(canon, ensure_ascii=False, indent=2)}

현재 장면 계약:
{json.dumps(context, ensure_ascii=False, indent=2)}

직전 승인 산문:
{previous_prose or "첫 장면이므로 없음"}

검증할 산문:
{prose}

설명이나 코드펜스 없이 JSON 객체 하나만 반환하라.
키는 scene_id, status, checks, issues, reason이다.
status는 pass, fail, uncertain 중 하나다.
checks에는 objective, state_transition, owned_elements, canon, continuity,
prose_quality 불리언을 모두 넣는다.
모든 checks가 true일 때만 pass다.
설정 충돌, 장면 목표 누락, 상태 전이 누락, 미래 사건 선취, 요약문 문체,
이전 산문 불연속이 있으면 fail 또는 uncertain으로 판정한다.
"""


def parse_review_response(
    response: str,
    scene: dict[str, Any],
    prose: str,
) -> dict[str, Any]:
    review = extract_json(response)
    if not isinstance(review, dict):
        raise ProseGenerationError("critic 응답의 최상위 값은 객체여야 함")
    review = dict(review)
    review["prose_sha256"] = hashlib.sha256(prose.encode("utf-8")).hexdigest()
    review["scene_contract_sha256"] = contract_sha256(scene)
    errors: list[str] = []
    validate_schema(
        review,
        "prose_review.schema.json",
        f"{scene['id']}/review.json",
        errors,
    )
    if errors:
        raise ProseGenerationError(errors)
    checks_pass = all(review["checks"].values())
    if review["status"] == "pass" and not checks_pass:
        raise ProseGenerationError("critic pass와 checks 결과가 일치하지 않음")
    if review["status"] != "pass" and checks_pass:
        raise ProseGenerationError("critic 비통과 상태와 checks 결과가 일치하지 않음")
    return review


def review_prose(
    llm: LLM,
    context: dict[str, Any],
    previous_prose: str,
    prose: str,
) -> dict[str, Any]:
    original_prompt = build_critic_prompt(context, previous_prose, prose)
    prompt = original_prompt
    last_errors: list[str] = []
    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        response = llm.generate("critic", prompt, temperature=0.0)
        try:
            return parse_review_response(response, context["scene"], prose)
        except ProseGenerationError as exc:
            last_errors = exc.errors
            if attempt == MAX_REVIEW_ATTEMPTS:
                raise
            prompt = f"""{original_prompt}

직전 critic 응답은 형식 계약을 위반했다.
아래 오류를 해결한 전체 JSON 객체를 다시 반환하라.
{json.dumps(last_errors, ensure_ascii=False, indent=2)}
"""
    raise ProseGenerationError(last_errors)


def promote_prose(
    root: Path,
    scene_id: str,
    prose: str,
    review: dict[str, Any],
) -> Path:
    parent = root / "prose" / "scenes"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / scene_id
    if target.exists():
        raise ProseGenerationError(f"정본 산문이 이미 존재함: {scene_id}")
    staging_root = Path(tempfile.mkdtemp(prefix=".prose-", dir=parent))
    staged = staging_root / scene_id
    staged.mkdir()
    try:
        (staged / "prose.md").write_text(prose, encoding="utf-8")
        (staged / "review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staged, target)
    except OSError as exc:
        raise ProseGenerationError(f"산문 정본 승격 실패: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return target


def generate_prose_scene(
    root: Path,
    requested_scene_id: str | None,
    llm: LLM,
    check_scale: bool = True,
) -> Path:
    root = root.resolve()
    structure_errors = validate_project(root)
    if structure_errors:
        raise ProseGenerationError(structure_errors)
    if check_scale:
        scale_errors = validate_story_scale(root)
        if scale_errors:
            raise ProseGenerationError(scale_errors)
    scene_id = select_scene(root, requested_scene_id)
    if prose_scene_dir(root, scene_id).exists():
        raise ProseGenerationError(f"정본 산문이 이미 존재함: {scene_id}")
    previous_prose = previous_prose_context(root, scene_id)
    context = load_scene_context(root, scene_id)
    scene = context["scene"]
    feedback: list[str] | None = None
    last_errors: list[str] = []

    for attempt in range(1, MAX_PROSE_ATTEMPTS + 1):
        response = llm.generate(
            "generator",
            build_generator_prompt(context, previous_prose, feedback),
            temperature=0.8,
        )
        try:
            prose = parse_prose_response(
                response,
                scene_id,
                scene["target_chars"],
            )
        except ProseGenerationError as exc:
            last_errors = exc.errors
            feedback = last_errors
            if attempt == MAX_PROSE_ATTEMPTS:
                break
            continue

        review = review_prose(llm, context, previous_prose, prose)
        if review["status"] == "pass":
            return promote_prose(root, scene_id, prose, review)
        last_errors = [
            *review["issues"],
            review["reason"],
        ]
        feedback = last_errors

    raise ProseGenerationError(
        [
            f"산문 생성 {MAX_PROSE_ATTEMPTS}회 실패: {scene_id}",
            *last_errors,
        ]
    )


def generate_all_prose(
    root: Path,
    llm: LLM,
    limit: int | None = None,
    check_scale: bool = True,
) -> list[Path]:
    results: list[Path] = []
    while limit is None or len(results) < limit:
        try:
            scene_id = select_scene(root.resolve(), None)
        except ProseGenerationError as exc:
            if "모든 장면" in str(exc):
                break
            raise
        results.append(
            generate_prose_scene(
                root,
                scene_id,
                llm,
                check_scale=check_scale,
            )
        )
    return results


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="다음 정본 장면의 산문을 생성하고 critic 승인 뒤 승격한다."
    )
    parser.add_argument("scene_id", nargs="?")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--all",
        action="store_true",
        help="남은 장면을 순서대로 모두 생성",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="--all 실행에서 이번에 생성할 최대 장면 수",
    )
    args = parser.parse_args()

    try:
        if args.all:
            if args.scene_id:
                raise ProseGenerationError("--all과 scene_id를 함께 사용할 수 없음")
            if args.limit is not None and args.limit < 1:
                raise ProseGenerationError("--limit은 1 이상이어야 함")
            results = generate_all_prose(
                args.root,
                create_llm_client(),
                args.limit,
            )
            if not results:
                print("[OK] 생성할 미완료 장면 없음")
                return 0
            print(f"[OK] 산문 정본 {len(results)}개 승격 완료")
            return 0
        result = generate_prose_scene(args.root, args.scene_id, create_llm_client())
    except (ProseGenerationError, OSError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] 산문 정본 승격 완료: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
