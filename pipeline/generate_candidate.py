# 사용자 기획 브리프에서 검증 가능한 구조 문서 후보를 생성하는 도구
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


def build_prompt(brief: str) -> str:
    schemas = {
        name: json.loads(load_schema_text(name))
        for name in (
            "series.schema.json",
            "volume.schema.json",
            "event.schema.json",
            "scene.schema.json",
        )
    }
    return f"""다음 기획 브리프로 정확히 5권인 장편소설 구조 후보를 작성하라.

기획 브리프:
{brief}

반환 형식은 설명이나 코드펜스 없는 JSON 객체 하나다.
최상위 키는 series, volumes, events, scenes만 사용한다.
series는 객체이며 나머지는 객체 배열이다.
모든 권, 사건, 장면 문서를 빠짐없이 포함하고 ID와 참조를 일치시킨다.
각 이야기 요소는 정확히 한 장면의 owns가 소유해야 한다.
복선 setup은 이를 회수하는 payoff보다 앞선 장면에 배치한다.
모든 start_state와 end_state를 계층과 장면 순서에 맞게 연결한다.

문서 스키마:
{json.dumps(schemas, ensure_ascii=False, indent=2)}
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
    brief: str,
    output: Path,
    llm: LLM,
) -> Path:
    if not brief.strip():
        raise CandidateGenerationError("기획 브리프가 비어 있음")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".candidate-", dir=output.parent))
    staged_candidate = staging_root / "candidate"
    preserve_staging = False

    try:
        response = llm.generate("generator", build_prompt(brief), temperature=0.7)
        bundle = extract_json(response)
        if not bundle:
            raise CandidateGenerationError("모델 응답에서 JSON 객체를 추출하지 못함")
        materialize_bundle(require_bundle(bundle), staged_candidate)
        errors = validate_project(staged_candidate, check_ledger=False)
        if errors:
            raise CandidateGenerationError(errors)
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
        description="기획 브리프에서 검증된 구조 문서 후보를 생성한다."
    )
    parser.add_argument("brief_file", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "runs" / "candidate",
    )
    args = parser.parse_args()

    try:
        brief = args.brief_file.read_text(encoding="utf-8")
        generate_candidate(brief, args.output, create_llm_client())
    except (OSError, CandidateGenerationError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] 구조 후보 생성 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
