# Forge critic으로 후보의 원천 정본 의미 준수를 독립 검증하는 도구
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from pipeline.validate_structure import validate_project, validate_schema


MAX_REVIEW_ATTEMPTS = 3
class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class CanonReviewError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def story_sha256(candidate: Path) -> str:
    story = candidate / "story"
    digest = hashlib.sha256()
    paths = [story / "series.json"]
    for directory in ("volumes", "events", "scenes"):
        paths.extend(sorted((story / directory).glob("*.json")))
    for path in paths:
        relative = path.relative_to(story).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def load_story_bundle(candidate: Path) -> dict[str, Any]:
    story = candidate / "story"
    return {
        "series": json.loads((story / "series.json").read_text(encoding="utf-8")),
        "volumes": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "volumes").glob("*.json"))
        ],
        "events": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "events").glob("*.json"))
        ],
        "scenes": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "scenes").glob("*.json"))
        ],
    }


def build_review_prompt(candidate: Path) -> str:
    canon, manuscript = load_source_material()
    canon_ids = [item["id"] for item in canon["canon"]]
    bundle = load_story_bundle(candidate)
    digest = story_sha256(candidate)
    return f"""너는 Forge의 독립 정본 검증 critic이다.
아래 후보를 수정하거나 다시 쓰지 말고 원천 정본 항목 준수 여부만 엄격히 판정하라.

정본 설정과 확정 사건:
{json.dumps(canon, ensure_ascii=False, indent=2)}

기존 압축 원고:
{manuscript}

검증할 구조 후보:
{json.dumps(bundle, ensure_ascii=False, indent=2)}

후보 story SHA-256:
{digest}

설명이나 코드펜스 없이 다음 계약의 JSON 객체 하나만 반환하라.
- story_sha256: 위 해시를 그대로 복사한다.
- overall_pass: 모든 원천 정본 항목이 pass일 때만 true다.
- verdicts: {json.dumps(canon_ids, ensure_ascii=False)}를 정확히 한 번씩 포함한다.
- 각 verdict는 canon_id, status(pass|fail|uncertain), scene_ids, reason을 가진다.
- scene_ids는 해당 정본 항목을 직접 입증하는 후보 장면 ID만 기록한다.
- 근거가 없거나 충돌하면 pass로 추정하지 말고 fail 또는 uncertain으로 판정한다.
"""


def build_retry_prompt(
    original_prompt: str,
    previous_response: str,
    errors: list[str],
) -> str:
    return f"""{original_prompt}

직전 검토 응답은 형식 계약을 위반했다.
의미 판정은 유지하되 아래 형식 오류를 모두 해결한 전체 JSON 객체를 다시 반환하라.

검토 형식 오류:
{json.dumps(errors, ensure_ascii=False, indent=2)}

직전 응답:
{previous_response}
"""


def review_contract_errors(candidate: Path, review: Any) -> list[str]:
    if not isinstance(review, dict):
        return ["검토 응답의 최상위 값은 객체여야 함"]

    errors: list[str] = []
    if not validate_schema(
        review,
        "canon_review.schema.json",
        "canon-review.json",
        errors,
    ):
        return errors

    verdicts = review["verdicts"]
    canon_ids = [verdict["canon_id"] for verdict in verdicts]
    source_canon, _ = load_source_material()
    expected_canon_ids = {item["id"] for item in source_canon["canon"]}
    if (
        len(canon_ids) != len(set(canon_ids))
        or set(canon_ids) != expected_canon_ids
    ):
        expected = ", ".join(sorted(expected_canon_ids))
        errors.append(f"검토 verdicts는 원천 정본 ID를 정확히 포함해야 함: {expected}")

    scene_ids = {
        path.stem
        for path in (candidate / "story" / "scenes").glob("*.json")
    }
    for verdict in verdicts:
        for scene_id in verdict["scene_ids"]:
            if scene_id not in scene_ids:
                errors.append(
                    f"존재하지 않는 장면을 정본 근거로 참조함: "
                    f"{verdict['canon_id']} -> {scene_id}"
                )

    if review["story_sha256"] != story_sha256(candidate):
        errors.append("검토의 story_sha256 해시가 현재 후보와 다름")

    statuses_pass = all(
        verdict["status"] == "pass"
        for verdict in verdicts
    )
    if review["overall_pass"] != statuses_pass:
        errors.append("overall_pass가 개별 verdict 상태와 일치하지 않음")

    return errors


def review_approval_errors(review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for verdict in review["verdicts"]:
        if verdict["status"] != "pass":
            errors.append(
                f"정본 미통과: {verdict['canon_id']} "
                f"({verdict['status']}) {verdict['reason']}"
            )
    if not review["overall_pass"]:
        errors.append("critic이 후보의 정본 전체 준수를 승인하지 않음")
    return errors


def validate_review(candidate: Path, review: Any) -> list[str]:
    contract_errors = review_contract_errors(candidate, review)
    if contract_errors:
        return contract_errors
    return review_approval_errors(review)


def save_review(candidate: Path, review: dict[str, Any]) -> None:
    review_path = candidate / "canon-review.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".canon-review-",
        suffix=".json.tmp",
        dir=candidate,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(review, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, review_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise CanonReviewError(f"정본 검토 저장 실패: {exc}") from exc


def validate_canon_candidate(candidate: Path, llm: LLM) -> dict[str, Any]:
    candidate = candidate.resolve()
    structure_errors = validate_project(candidate, check_ledger=False)
    if structure_errors:
        raise CanonReviewError(structure_errors)

    original_prompt = build_review_prompt(candidate)
    prompt = original_prompt
    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        response = llm.generate("critic", prompt, temperature=0.0)
        review = extract_json(response)
        contract_errors = review_contract_errors(candidate, review)
        if contract_errors:
            if attempt == MAX_REVIEW_ATTEMPTS:
                raise CanonReviewError(
                    [
                        f"정본 검토 형식 {MAX_REVIEW_ATTEMPTS}회 실패",
                        *contract_errors,
                    ]
                )
            prompt = build_retry_prompt(
                original_prompt,
                response,
                contract_errors,
            )
            continue

        approval_errors = review_approval_errors(review)
        if approval_errors:
            raise CanonReviewError(approval_errors)

        save_review(candidate, review)
        return review

    raise AssertionError("도달할 수 없는 정본 검토 상태")


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forge critic으로 후보의 원천 정본 의미 준수를 검증한다."
    )
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()

    try:
        validate_canon_candidate(args.candidate, create_llm_client())
    except (CanonReviewError, OSError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] 정본 의미 검증 통과: {args.candidate.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
