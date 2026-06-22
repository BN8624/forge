# 게임 시나리오용 소설 시놉시스 후보를 생성하고 critic 선택 결과를 게시하는 도구
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = PROJECT_ROOT / "lib"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.jsonutil import extract_json


CANDIDATE_COUNT = 5
MAX_ATTEMPTS = 3
CANDIDATE_KEYS = {
    "id",
    "title",
    "genre",
    "logline",
    "player_role",
    "core_loop",
    "progression",
    "factions",
    "choice_structure",
    "five_volume_arc",
    "game_fit",
}
SCORE_KEYS = {
    "novel",
    "core_loop",
    "player_agency",
    "content_scale",
    "differentiation",
}


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class SynopsisGenerationError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def candidate_ids() -> set[str]:
    return {f"S{index}" for index in range(1, CANDIDATE_COUNT + 1)}


def build_candidate_prompt(instruction: str = "") -> str:
    optional_instruction = (
        instruction.strip()
        or "추가 지시 없음. 장르, 소재, 게임 형식도 Forge가 스스로 결정한다."
    )
    return f"""너는 Forge의 게임 시나리오 원작 소설 기획 generator다.
완결된 장편소설로 읽히면서도 실제 게임의 지역, 임무, 성장, 세력 선택,
보스, 반복 플레이로 변환하기 좋은 신규 시놉시스 후보를 정확히 5개 만든다.

사용자 지시:
{optional_instruction}

후보끼리는 장르, 주인공 역할, 핵심 플레이 반복, 성장 방식, 갈등 구조가
명확히 달라야 한다. 기존 Forge 작품이나 유명 게임의 이름과 설정을 재사용하지 않는다.
각 후보는 소설의 주인공이 게임의 플레이어 캐릭터가 될 수 있어야 하며,
선택과 실패가 서사 상태를 실제로 바꾸는 구조를 가져야 한다.

설명이나 코드펜스 없이 다음 형식의 JSON 객체 하나만 반환한다.
{{
  "candidates": [
    {{
      "id": "S1",
      "title": "가제",
      "genre": "장르",
      "logline": "주인공, 목표, 방해 세력, 대가, 결말 방향을 포함한 시놉시스",
      "player_role": "플레이어가 반복적으로 수행할 역할",
      "core_loop": "탐색·선택·전투·귀환 등 핵심 플레이 반복",
      "progression": "서사와 결합된 성장 및 해금 방식",
      "factions": ["서로 다른 목표를 가진 세력 설명"],
      "choice_structure": "선택이 지역·동료·결말에 미치는 영향",
      "five_volume_arc": ["1권", "2권", "3권", "4권", "5권"],
      "game_fit": "게임 시나리오로 강한 구체적 이유"
    }}
  ]
}}
ID는 S1-S5를 정확히 한 번씩 사용한다.
"""


def validate_candidates(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != {"candidates"}:
        return ["시놉시스 응답은 candidates 키만 가진 객체여야 함"]
    candidates = value["candidates"]
    if not isinstance(candidates, list) or len(candidates) != CANDIDATE_COUNT:
        return [f"시놉시스 후보는 정확히 {CANDIDATE_COUNT}개여야 함"]
    ids: list[str] = []
    titles: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        label = f"후보 {index}"
        if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_KEYS:
            errors.append(f"{label} 필드가 계약과 다름")
            continue
        ids.append(candidate["id"] if isinstance(candidate["id"], str) else "")
        titles.append(
            candidate["title"] if isinstance(candidate["title"], str) else ""
        )
        for key in CANDIDATE_KEYS - {"factions", "five_volume_arc"}:
            if not isinstance(candidate[key], str) or not candidate[key].strip():
                errors.append(f"{label}.{key}는 비어 있지 않은 문자열이어야 함")
        factions = candidate["factions"]
        if (
            not isinstance(factions, list)
            or len(factions) < 2
            or not all(isinstance(item, str) and item.strip() for item in factions)
        ):
            errors.append(f"{label}.factions는 두 개 이상의 문자열이어야 함")
        arc = candidate["five_volume_arc"]
        if (
            not isinstance(arc, list)
            or len(arc) != 5
            or not all(isinstance(item, str) and item.strip() for item in arc)
        ):
            errors.append(f"{label}.five_volume_arc는 정확히 5개 문자열이어야 함")
    if set(ids) != candidate_ids() or len(ids) != len(set(ids)):
        errors.append("시놉시스 ID는 S1-S5를 정확히 한 번씩 포함해야 함")
    normalized_titles = [title.strip() for title in titles]
    if len(normalized_titles) != len(set(normalized_titles)):
        errors.append("시놉시스 후보 제목은 서로 달라야 함")
    return errors


def build_review_prompt(candidates: dict[str, Any]) -> str:
    return f"""너는 Forge의 독립 게임 시나리오 critic이다.
아래 후보 5개를 모두 비교해 가장 강한 장편소설 원작 하나를 선택한다.

평가 기준:
- novel: 5권 장편의 인물 변화와 완결성.
- core_loop: 반복 플레이가 서사와 자연스럽게 결합되는 정도.
- player_agency: 선택과 실패가 세계 상태를 바꾸는 정도.
- content_scale: 지역, 임무, 적, 동료, 보스로 확장 가능한 정도.
- differentiation: 기존 유명 작품과 구별되는 고유성.

후보:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

설명이나 코드펜스 없이 다음 형식의 JSON 객체 하나만 반환한다.
{{
  "status": "pass",
  "selected_id": "S1",
  "ranking": ["S1", "S2", "S3", "S4", "S5"],
  "evaluations": [
    {{
      "id": "S1",
      "scores": {{
        "novel": 1,
        "core_loop": 1,
        "player_agency": 1,
        "content_scale": 1,
        "differentiation": 1
      }},
      "strengths": ["구체적 강점"],
      "risks": ["구체적 위험"]
    }}
  ],
  "selection_reason": "최종 선택 근거",
  "development_directives": ["세계관 생성에서 반드시 보강할 지시"]
}}
각 점수는 1-10 정수다. ranking과 evaluations는 S1-S5를 정확히 한 번씩
포함해야 하며 selected_id는 ranking 첫 항목과 같아야 한다.
"""


def validate_review(value: Any) -> list[str]:
    errors: list[str] = []
    required = {
        "status",
        "selected_id",
        "ranking",
        "evaluations",
        "selection_reason",
        "development_directives",
    }
    if not isinstance(value, dict) or set(value) != required:
        return ["critic 응답 필드가 계약과 다름"]
    if value["status"] != "pass":
        errors.append("critic status는 pass여야 함")
    ranking = value["ranking"]
    valid_ranking = (
        isinstance(ranking, list)
        and len(ranking) == CANDIDATE_COUNT
        and all(isinstance(item, str) for item in ranking)
        and set(ranking) == candidate_ids()
    )
    if not valid_ranking:
        errors.append("critic ranking은 S1-S5를 정확히 한 번씩 포함해야 함")
    if (
        not isinstance(value["selected_id"], str)
        or not valid_ranking
        or value["selected_id"] != ranking[0]
    ):
        errors.append("selected_id는 ranking 첫 항목이어야 함")
    evaluations = value["evaluations"]
    evaluation_ids: list[str] = []
    if not isinstance(evaluations, list) or len(evaluations) != CANDIDATE_COUNT:
        errors.append(f"critic evaluations는 정확히 {CANDIDATE_COUNT}개여야 함")
    else:
        for index, evaluation in enumerate(evaluations, start=1):
            if not isinstance(evaluation, dict) or set(evaluation) != {
                "id",
                "scores",
                "strengths",
                "risks",
            }:
                errors.append(f"critic 평가 {index} 필드가 계약과 다름")
                continue
            evaluation_ids.append(
                evaluation["id"] if isinstance(evaluation["id"], str) else ""
            )
            scores = evaluation["scores"]
            if not isinstance(scores, dict) or set(scores) != SCORE_KEYS:
                errors.append(f"critic 평가 {index} 점수 필드가 계약과 다름")
            elif not all(
                type(score) is int and 1 <= score <= 10
                for score in scores.values()
            ):
                errors.append(f"critic 평가 {index} 점수는 1-10 정수여야 함")
            for key in ("strengths", "risks"):
                items = evaluation[key]
                if (
                    not isinstance(items, list)
                    or not items
                    or not all(
                        isinstance(item, str) and item.strip() for item in items
                    )
                ):
                    errors.append(f"critic 평가 {index}.{key}는 문자열 목록이어야 함")
        if set(evaluation_ids) != candidate_ids():
            errors.append("critic evaluations는 S1-S5를 정확히 한 번씩 포함해야 함")
    if (
        not isinstance(value["selection_reason"], str)
        or not value["selection_reason"].strip()
    ):
        errors.append("critic selection_reason이 비어 있음")
    directives = value["development_directives"]
    if (
        not isinstance(directives, list)
        or not directives
        or not all(isinstance(item, str) and item.strip() for item in directives)
    ):
        errors.append("critic development_directives는 문자열 목록이어야 함")
    return errors


def generate_validated_json(
    llm: LLM,
    role: str,
    original_prompt: str,
    validator,
    temperature: float,
) -> dict[str, Any]:
    prompt = original_prompt
    last_errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = llm.generate(role, prompt, temperature=temperature)
        value = extract_json(response)
        last_errors = validator(value)
        if not last_errors:
            return value
        if attempt < MAX_ATTEMPTS:
            prompt = f"""{original_prompt}

직전 응답이 계약 검증에 실패했다. 오류를 모두 고친 전체 JSON 객체를 다시 반환하라.

오류:
{json.dumps(last_errors, ensure_ascii=False, indent=2)}

직전 응답:
{response}
"""
    raise SynopsisGenerationError(
        [f"{role} 시놉시스 단계 {MAX_ATTEMPTS}회 실패", *last_errors]
    )


def selected_instruction(
    candidates: dict[str, Any],
    review: dict[str, Any],
) -> str:
    selected = next(
        candidate
        for candidate in candidates["candidates"]
        if candidate["id"] == review["selected_id"]
    )
    return f"""Forge가 게임 시나리오 원작 후보 평가를 통해 아래 기획을 선택했다.
이 선택의 제목, 장르, 핵심 역할, 플레이 반복, 성장, 세력, 선택 구조,
5권 방향을 유지하면서 완전한 세계관과 정본을 작성하라.

선택 시놉시스:
{json.dumps(selected, ensure_ascii=False, indent=2)}

critic 선택 근거:
{review["selection_reason"]}

critic 보완 지시:
{json.dumps(review["development_directives"], ensure_ascii=False, indent=2)}
"""


def choose_game_concept(
    output: Path,
    selected_id: str | None = None,
    selected_by: str = "critic",
) -> str:
    try:
        candidates = json.loads(
            (output / "synopsis-candidates.json").read_text(encoding="utf-8")
        )
        review = json.loads(
            (output / "synopsis-review.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SynopsisGenerationError(f"저장된 시놉시스 결과 읽기 실패: {exc}") from exc
    errors = validate_candidates(candidates)
    errors.extend(validate_review(review))
    if errors:
        raise SynopsisGenerationError(errors)
    choice = selected_id or review["selected_id"]
    if choice not in candidate_ids():
        raise SynopsisGenerationError(f"알 수 없는 시놉시스 ID: {choice}")
    selected = next(
        candidate
        for candidate in candidates["candidates"]
        if candidate["id"] == choice
    )
    (output / "selected-synopsis.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "concept-selection.json").write_text(
        json.dumps(
            {
                "selected_id": choice,
                "selected_by": selected_by,
                "critic_recommendation": review["selected_id"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return selected_instruction(
        candidates,
        {
            **review,
            "selected_id": choice,
            "selection_reason": (
                review["selection_reason"]
                if choice == review["selected_id"]
                else (
                    f"사용자가 critic 추천 {review['selected_id']} 대신 "
                    f"{choice} 후보를 선택했다. critic 평가의 위험과 보완 지시는 유지한다."
                )
            ),
        },
    )


def publish_result(staged: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.parent / f".{output.name}.previous"
    shutil.rmtree(backup, ignore_errors=True)
    had_output = output.exists()
    if had_output:
        os.replace(output, backup)
    try:
        os.replace(staged, output)
    except OSError as exc:
        if had_output and backup.exists() and not output.exists():
            os.replace(backup, output)
        raise SynopsisGenerationError(f"시놉시스 결과 게시 실패: {exc}") from exc
    shutil.rmtree(backup, ignore_errors=True)


def generate_game_concept(
    instruction: str,
    output: Path,
    llm: LLM,
) -> str:
    candidates = generate_validated_json(
        llm,
        "generator",
        build_candidate_prompt(instruction),
        validate_candidates,
        0.9,
    )
    review = generate_validated_json(
        llm,
        "critic",
        build_review_prompt(candidates),
        validate_review,
        0.2,
    )
    selected = next(
        candidate
        for candidate in candidates["candidates"]
        if candidate["id"] == review["selected_id"]
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".synopses-", dir=output.parent))
    staged = staging_root / "concept"
    staged.mkdir()
    try:
        for name, value in (
            ("synopsis-candidates.json", candidates),
            ("synopsis-review.json", review),
            ("selected-synopsis.json", selected),
        ):
            (staged / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        publish_result(staged, output)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return choose_game_concept(output)


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="게임 시나리오용 소설 시놉시스 5개를 만들고 하나를 선택한다."
    )
    parser.add_argument("--instruction-file", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "runs" / "new-world" / "concept",
    )
    args = parser.parse_args()
    try:
        instruction = (
            args.instruction_file.read_text(encoding="utf-8")
            if args.instruction_file
            else ""
        )
        generate_game_concept(instruction, args.output, create_llm_client())
    except (OSError, RuntimeError, SynopsisGenerationError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[OK] 게임 시나리오 시놉시스 선택 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
