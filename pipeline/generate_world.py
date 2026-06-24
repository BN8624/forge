# 새로운 장편 세계관과 21개 정본 항목, 압축 참고 원고를 생성·검증·게시하는 도구
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
from pipeline.validate_structure import validate_schema


MAX_WORLD_ATTEMPTS = 5
EXPECTED_CANON_IDS = {f"C{index}" for index in range(1, 22)}


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class WorldGenerationError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def build_world_prompt(
    instruction: str = "",
    volume_count: int = 5,
    game_scenario: bool = False,
) -> str:
    optional_instruction = instruction.strip() or "추가 지시 없음. 장르와 소재도 스스로 결정한다."
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "world_source.schema.json").read_text(
            encoding="utf-8"
        )
    )
    if game_scenario:
        target_guidance = f"""이 세계관은 정확히 {volume_count}단계의 게임 시나리오로 확장될 원천 정본이다.
플레이어 역할, 핵심 플레이 반복, 선택 구조, 세력 반응, 성장 보상, 실패 비용,
중반 전환과 최종 결말을 모두 검증 가능한 사실 문장으로 만든다.
모호한 분위기나 소설 문체보다 게임 제작자가 바로 사용할 수 있는 규칙, 임무,
분기, NPC 관계, 장소, 시스템 제약을 우선한다."""
        manuscript_guidance = f"""manuscript는 최소 3,000자의 게임 시나리오 참고 원고다.
도입, {volume_count}단계에 걸친 목표 변화, 플레이어 선택지, 반복 플레이 루프,
주요 NPC와 세력 반응, 실패와 보상, 최종 결말을 모두 포함한다.
출판 산문처럼 장면을 길게 묘사하지 말고 기획서와 시나리오 바이블 사이의 밀도로 쓴다."""
    else:
        target_guidance = f"""이 세계관은 정확히 {volume_count}권의 장편 구조와 완결된 산문으로 확장될 원천 정본이다.
인물 이름, 장소, 사회 체제, 초자연 규칙, 갈등, 반전, 결말을 독창적으로 만든다.
핵심 규칙은 모호한 분위기가 아니라 이후 critic이 판정할 수 있는 사실 문장으로 쓴다."""
        manuscript_guidance = f"""manuscript는 최소 3,000자의 압축 참고 원고다.
도입, {volume_count}권에 걸친 상승과 전환, 최종 결말을 모두 포함하며 출판 산문의 문체,
대화, 감각 묘사를 보여준다. 구조 문서나 항목 설명처럼 쓰지 않는다."""
    return f"""너는 Forge의 신규 세계관 창작자다.
기존 작품, 에테르노, 카엘, 리아, 발타자르, 영혼의 조각을 사용하지 말고
완전히 새로운 한국어 창작 세계관을 창작하라.

사용자 선택 지시:
{optional_instruction}

{target_guidance}

canon 21개는 다음 역할을 고르게 담당한다.
- C1-C4: 주인공과 핵심 관계, 결핍.
- C5-C8: 세계의 물리·사회·초자연 규칙.
- C9-C12: 주요 장소, 세력, 적대자와 위협.
- C13-C16: 중반의 발견과 과거 진실.
- C17-C19: 후반 반전과 대가.
- C20-C21: 최종 결말에서 반드시 성립할 사실.

{manuscript_guidance}

설명이나 코드펜스 없이 JSON 객체 하나만 반환한다.
스키마:
{json.dumps(schema, ensure_ascii=False, indent=2)}
"""


def validate_world_source(
    value: Any,
    expected_identity: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not validate_schema(
        value,
        "world_source.schema.json",
        "world-source.json",
        errors,
    ):
        return errors
    canon_ids = [item["id"] for item in value["canon"]]
    if (
        len(canon_ids) != len(set(canon_ids))
        or set(canon_ids) != EXPECTED_CANON_IDS
    ):
        errors.append("신규 세계관 canon은 C1-C21을 정확히 한 번씩 포함해야 함")
    forbidden = ("에테르노", "카엘", "리아", "발타자르", "영혼의 조각")
    combined = " ".join(
        [
            value["title"],
            value["premise"],
            value["manuscript"],
            *(item["text"] for item in value["canon"]),
        ]
    )
    found = [term for term in forbidden if term in combined]
    if found:
        errors.append(f"기존 세계관 고유어 재사용 금지: {', '.join(found)}")
    if expected_identity:
        for key in ("title", "genre"):
            expected = expected_identity.get(key)
            if isinstance(expected, str) and value.get(key) != expected:
                errors.append(
                    f"선택 시놉시스 {key} 불일치: "
                    f"기대 {expected!r}, 실제 {value.get(key)!r}"
                )
    return errors


def extend_short_manuscript(
    value: dict[str, Any],
    llm: LLM,
    game_scenario: bool = False,
) -> dict[str, Any]:
    manuscript = value.get("manuscript")
    if not isinstance(manuscript, str) or not 1000 <= len(manuscript) < 3000:
        return value
    addition_instruction = (
        """추가 원고는 기존 결말 이후의 장기적 여파, 인물 관계의 잔향, 새 사회의
변화를 구체적 장면과 감각으로 확장하되 새로운 핵심 설정이나 반전을 만들지 않는다."""
        if not game_scenario
        else """추가 원고는 플레이어 목표, 선택지, 실패 비용, 보상, NPC와 세력 반응을
구체적으로 보강하되 새로운 핵심 설정이나 반전을 만들지 않는다."""
    )
    manuscript_type = "게임 시나리오 참고 원고" if game_scenario else "세계관 참고 원고"
    response = llm.generate(
        "generator",
        f"""너는 Forge의 신규 {manuscript_type} generator다.
아래 원고는 세계관과 결말은 완성됐지만 최소 분량보다 짧다.
기존 문장을 수정하거나 반복하지 말고 마지막 문장 뒤에 자연스럽게 이어질
한국어 원고만 추가하라.

세계관 제목: {value.get("title", "")}
장르: {value.get("genre", "")}
톤: {value.get("tone", "")}
부족 분량: 최소 {3000 - len(manuscript) + 500}자

현재 원고:
{manuscript}

{addition_instruction}
설명이나 코드펜스 없이 manuscript_addition 키만 가진 JSON 객체를 반환한다.
""",
        temperature=0.8,
    )
    addition_value = extract_json(response)
    if (
        isinstance(addition_value, dict)
        and set(addition_value) == {"manuscript_addition"}
        and isinstance(addition_value["manuscript_addition"], str)
        and addition_value["manuscript_addition"].strip()
    ):
        extended = dict(value)
        extended["manuscript"] = (
            manuscript.rstrip()
            + "\n\n"
            + addition_value["manuscript_addition"].strip()
        )
        return extended
    return value


def materialize_world(value: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    canon_bible = {
        "title": value["title"],
        "genre": value["genre"],
        "tone": value["tone"],
        "premise": value["premise"],
        "canon": value["canon"],
    }
    (output / "canon_bible.json").write_text(
        json.dumps(canon_bible, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "compressed_manuscript.md").write_text(
        value["manuscript"].strip() + "\n",
        encoding="utf-8",
    )
    metadata = {
        key: value[key]
        for key in ("title", "genre", "tone", "premise")
    }
    (output / "world.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def publish_world(staged: Path, output: Path) -> None:
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
        raise WorldGenerationError(f"신규 세계관 게시 실패: {exc}") from exc
    shutil.rmtree(backup, ignore_errors=True)


def generate_world(
    instruction: str,
    output: Path,
    llm: LLM,
    expected_identity: dict[str, Any] | None = None,
) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".world-", dir=output.parent))
    staged = staging_root / "world"
    volume_count = (
        expected_identity.get("approved_volume_count", 5)
        if expected_identity
        else 5
    )
    game_scenario = bool(
        expected_identity
        and any(
            key in expected_identity
            for key in ("player_role", "core_loop", "choice_structure")
        )
    )
    original_prompt = build_world_prompt(instruction, volume_count, game_scenario)
    prompt = original_prompt
    last_errors: list[str] = []
    try:
        for attempt in range(1, MAX_WORLD_ATTEMPTS + 1):
            response = llm.generate("generator", prompt, temperature=0.9)
            value = extract_json(response)
            if isinstance(value, dict):
                value = extend_short_manuscript(value, llm, game_scenario)
            last_errors = validate_world_source(value, expected_identity)
            if not last_errors:
                materialize_world(value, staged)
                publish_world(staged, output)
                return output
            if attempt == MAX_WORLD_ATTEMPTS:
                break
            prompt = f"""{original_prompt}

직전 응답은 신규 세계관 계약 검증에 실패했다.
아래 오류를 모두 해결한 전체 JSON 객체를 처음부터 다시 반환하라.

오류:
{json.dumps(last_errors, ensure_ascii=False, indent=2)}

직전 응답:
{response}
"""
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    raise WorldGenerationError(
        [f"신규 세계관 생성 {MAX_WORLD_ATTEMPTS}회 실패", *last_errors]
    )


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="기존 작품과 무관한 신규 장편 세계관 원천을 생성한다."
    )
    parser.add_argument("--instruction-file", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reference" / "current",
    )
    args = parser.parse_args()
    try:
        instruction = (
            args.instruction_file.read_text(encoding="utf-8")
            if args.instruction_file
            else ""
        )
        generate_world(instruction, args.output, create_llm_client())
    except (OSError, RuntimeError, WorldGenerationError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[OK] 신규 세계관 생성 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
