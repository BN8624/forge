# 기존 세계관 입력부터 5권 산문과 EPUB 완성까지 전 단계를 재개 가능한 방식으로 실행하는 오케스트레이터
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = PROJECT_ROOT / "lib"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.expand_structure import expand_structure
from pipeline.export_epub import export_epub
from pipeline.generate_candidate import generate_candidate
from pipeline.generate_candidate import load_source_material
from pipeline.generate_synopses import generate_game_concept
from pipeline.generate_world import generate_world
from pipeline.generate_prose import (
    ProseGenerationError,
    approved_prose,
    contract_sha256,
    generate_prose_scene,
    ordered_scene_ids,
    select_scene,
)
from pipeline.promote_candidate import promote_candidate
from pipeline.rebuild_state import rebuild_state
from pipeline.validate_canon import (
    story_sha256,
    validate_canon_candidate,
    validate_review,
)
from pipeline.validate_scale import validate_story_scale
from pipeline.validate_structure import validate_project, validate_schema


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class SeriesCompletionError(Exception):
    pass


class LazyLLM:
    def __init__(self) -> None:
        self.client: LLM | None = None

    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str:
        if self.client is None:
            self.client = create_llm_client()
        return self.client.generate(role, prompt, temperature)


def write_status(root: Path, stage: str, **details: Any) -> None:
    status_path = root / "runs" / "complete-series" / "status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".status-",
        suffix=".json.tmp",
        dir=status_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, status_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".json.tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def archive_current_world(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = root / "runs" / "world-backups" / stamp
    archive.mkdir(parents=True, exist_ok=True)
    for relative in (
        Path("story"),
        Path("prose"),
        Path("state"),
        Path("exports"),
        Path("reference") / "current",
    ):
        source = root / relative
        if not source.exists():
            continue
        destination = archive / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    return archive


def structure_matches_source(root: Path) -> bool:
    source, _ = load_source_material()
    if not source.get("title"):
        return True
    series = read_json_if_exists(root / "story" / "series.json")
    return bool(
        series
        and series.get("title") == source["title"]
        and series.get("premise") == source.get("premise")
    )


def prepare_new_world(
    root: Path,
    llm: LLM,
    instruction: str,
    game_scenario: bool = False,
) -> tuple[Path, bool]:
    active_path = root / "runs" / "new-world" / "active.json"
    active = read_json_if_exists(active_path)
    current_source = root / "reference" / "current"
    if active and active.get("status") == "active" and current_source.is_dir():
        return Path(active["backup"]), not structure_matches_source(root)

    backup = archive_current_world(root)
    world_instruction = instruction
    concept_path = root / "runs" / "new-world" / "concept"
    selected: dict[str, Any] | None = None
    if game_scenario:
        write_status(root, "synopsis_selection")
        world_instruction = generate_game_concept(instruction, concept_path, llm)
        selected = read_json_if_exists(concept_path / "selected-synopsis.json")
        if selected is None:
            raise SeriesCompletionError("선택 시놉시스 결과를 읽을 수 없음")
    if selected is None:
        generate_world(world_instruction, current_source, llm)
    else:
        generate_world(world_instruction, current_source, llm, selected)
    if game_scenario:
        for name in (
            "synopsis-candidates.json",
            "synopsis-review.json",
            "selected-synopsis.json",
        ):
            shutil.copy2(concept_path / name, current_source / name)
    source, _ = load_source_material()
    write_json_atomic(
        active_path,
        {
            "status": "active",
            "title": source.get("title"),
            "backup": str(backup),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": "game-scenario" if game_scenario else "new-world",
            "selected_synopsis_id": selected.get("id") if selected else None,
        },
    )
    return backup, True


def finish_new_world(root: Path, result: dict[str, Any]) -> None:
    active_path = root / "runs" / "new-world" / "active.json"
    active = read_json_if_exists(active_path) or {}
    active.update(
        {
            "status": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "scene_count": result.get("scene_count"),
            "epubs": [str(path) for path in result.get("epubs", [])],
        }
    )
    write_json_atomic(active_path, active)


def approved_candidate(candidate: Path) -> bool:
    if validate_project(candidate, check_ledger=False):
        return False
    try:
        review = json.loads(
            (candidate / "canon-review.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return not validate_review(candidate, review)


def prose_exists(root: Path) -> bool:
    return any((root / "prose" / "scenes").glob("*/prose.md"))


def structure_changed(root: Path, candidate: Path) -> bool:
    try:
        return story_sha256(root) != story_sha256(candidate)
    except (OSError, json.JSONDecodeError):
        return True


def promote_with_prose_backup(root: Path, candidate: Path) -> Path | None:
    prose_root = root / "prose"
    backup_path: Path | None = None
    if prose_exists(root) and structure_changed(root, candidate):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = root / "runs" / "prose-backups" / stamp
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(prose_root, backup_path)
    try:
        promote_candidate(root, candidate)
    except Exception:
        if backup_path is not None and backup_path.exists() and not prose_root.exists():
            os.replace(backup_path, prose_root)
        raise
    return backup_path


def backup_invalid_prose_suffix(root: Path) -> Path | None:
    scene_ids = ordered_scene_ids(root)
    scenes_root = root / "prose" / "scenes"
    if not scenes_root.exists():
        return None
    invalid_index: int | None = None
    gap_seen = False
    for index, scene_id in enumerate(scene_ids):
        directory = scenes_root / scene_id
        complete = (
            (directory / "prose.md").is_file()
            and (directory / "review.json").is_file()
        )
        if not directory.exists():
            gap_seen = True
            continue
        if gap_seen or not complete:
            invalid_index = index
            break
        try:
            approved_prose(root, scene_id)
        except ProseGenerationError:
            invalid_index = index
            break

    expected_ids = set(scene_ids)
    unknown_directories = [
        path
        for path in scenes_root.iterdir()
        if path.is_dir() and path.name not in expected_ids
    ]
    directories: list[Path] = []
    if invalid_index is not None:
        directories.extend(
            scenes_root / scene_id
            for scene_id in scene_ids[invalid_index:]
            if (scenes_root / scene_id).exists()
        )
    directories.extend(unknown_directories)
    if not directories:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = root / "runs" / "prose-backups" / stamp / "scenes"
    backup.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        os.replace(directory, backup / directory.name)
    return backup.parent


def ensure_reviewed_candidate(
    candidate: Path,
    llm: LLM,
    create_candidate,
) -> None:
    if approved_candidate(candidate):
        return
    create_candidate()
    validate_canon_candidate(candidate, llm)


def ensure_structure(
    root: Path,
    llm: LLM,
    instruction: str,
    regenerate_structure: bool,
) -> list[Path]:
    backups: list[Path] = []
    structure_errors = validate_project(root)
    if regenerate_structure or structure_errors:
        candidate = root / "runs" / "candidate"
        write_status(root, "structure_candidate")
        if regenerate_structure:
            generate_candidate(instruction, candidate, llm)
            validate_canon_candidate(candidate, llm)
        else:
            ensure_reviewed_candidate(
                candidate,
                llm,
                lambda: generate_candidate(instruction, candidate, llm),
            )
        backup = promote_with_prose_backup(root, candidate)
        if backup is not None:
            backups.append(backup)
        rebuild_state(root)

    scale_errors = validate_story_scale(root)
    if scale_errors:
        expanded = root / "runs" / "expanded-candidate"
        write_status(root, "structure_expansion", errors=scale_errors)
        if regenerate_structure or structure_errors:
            expand_structure(root, expanded, llm, instruction)
            validate_canon_candidate(expanded, llm)
        else:
            ensure_reviewed_candidate(
                expanded,
                llm,
                lambda: expand_structure(root, expanded, llm, instruction),
            )
        backup = promote_with_prose_backup(root, expanded)
        if backup is not None:
            backups.append(backup)
        rebuild_state(root)

    errors = validate_project(root)
    errors.extend(validate_story_scale(root))
    if errors:
        raise SeriesCompletionError("\n".join(errors))
    return backups


def validate_all_prose(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        scene_ids = ordered_scene_ids(root)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"장면 순서 로드 실패: {exc}"]
    for scene_id in scene_ids:
        scene_path = root / "story" / "scenes" / f"{scene_id}.json"
        prose_path = root / "prose" / "scenes" / scene_id / "prose.md"
        review_path = root / "prose" / "scenes" / scene_id / "review.json"
        try:
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            prose = prose_path.read_text(encoding="utf-8")
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"산문 정본 읽기 실패: {scene_id}: {exc}")
            continue
        validate_schema(
            review,
            "prose_review.schema.json",
            f"{scene_id}/review.json",
            errors,
        )
        if review.get("status") != "pass":
            errors.append(f"산문 critic 미승인: {scene_id}")
        checks = review.get("checks")
        if not isinstance(checks, dict) or not all(checks.values()):
            errors.append(f"산문 critic checks 미통과: {scene_id}")
        prose_hash = hashlib.sha256(prose.encode("utf-8")).hexdigest()
        if review.get("prose_sha256") != prose_hash:
            errors.append(f"산문 해시 불일치: {scene_id}")
        if review.get("scene_contract_sha256") != contract_sha256(scene):
            errors.append(f"산문 장면 계약 불일치: {scene_id}")
    return errors


def generate_remaining_prose(
    root: Path,
    llm: LLM,
    scene_retries: int,
) -> int:
    generated = 0
    stop_file = root / "STOP_AFTER_RUN"
    while True:
        try:
            scene_id = select_scene(root, None)
        except ProseGenerationError as exc:
            if "모든 장면" in str(exc):
                return generated
            raise
        if stop_file.exists():
            write_status(
                root,
                "stopped",
                next_scene_id=scene_id,
                generated_this_run=generated,
            )
            return generated
        failures = 0
        while True:
            write_status(
                root,
                "prose",
                scene_id=scene_id,
                generated_this_run=generated,
                attempt=failures + 1,
            )
            try:
                generate_prose_scene(root, scene_id, llm, check_scale=False)
                generated += 1
                break
            except ProseGenerationError as exc:
                failures += 1
                if scene_retries > 0 and failures >= scene_retries:
                    raise SeriesCompletionError(
                        f"{scene_id} 생성 실행 {failures}회 실패\n{exc}"
                    ) from exc
        if stop_file.exists():
            write_status(
                root,
                "stopped",
                last_scene_id=scene_id,
                generated_this_run=generated,
            )
            return generated


def export_all_epubs(root: Path) -> list[Path]:
    series = json.loads((root / "story" / "series.json").read_text(encoding="utf-8"))
    outputs = []
    for volume_id in series["volume_ids"]:
        outputs.append(
            export_epub(root, volume_id, root / "exports" / f"{volume_id}.epub")
        )
    return outputs


def complete_series(
    root: Path,
    llm: LLM,
    instruction: str = "",
    regenerate_structure: bool = False,
    scene_retries: int = 5,
) -> dict[str, Any]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    write_status(root, "starting")
    backups = ensure_structure(
        root,
        llm,
        instruction,
        regenerate_structure,
    )
    prose_backup = backup_invalid_prose_suffix(root)
    if prose_backup is not None:
        backups.append(prose_backup)
    generated = generate_remaining_prose(root, llm, scene_retries)
    if (root / "STOP_AFTER_RUN").exists():
        return {
            "complete": False,
            "generated": generated,
            "backups": backups,
            "epubs": [],
        }

    write_status(root, "final_validation")
    errors = validate_project(root)
    errors.extend(validate_story_scale(root))
    errors.extend(validate_all_prose(root))
    if errors:
        raise SeriesCompletionError("\n".join(errors))
    epubs = export_all_epubs(root)
    scene_count = len(ordered_scene_ids(root))
    write_status(
        root,
        "complete",
        scene_count=scene_count,
        generated_this_run=generated,
        epubs=[str(path) for path in epubs],
        prose_backups=[str(path) for path in backups],
    )
    return {
        "complete": True,
        "scene_count": scene_count,
        "generated": generated,
        "backups": backups,
        "epubs": epubs,
    }


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="선택한 세계관에서 5권 구조·산문·EPUB 완성까지 자동 실행한다."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--instruction-file", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--new-world",
        action="store_true",
        help="기존 세계관과 무관한 새 세계관을 생성한 뒤 5권 완주한다.",
    )
    mode.add_argument(
        "--game-scenario",
        action="store_true",
        help="시놉시스 5개를 생성·평가·선택한 뒤 게임 원작 소설 5권을 완주한다.",
    )
    parser.add_argument(
        "--regenerate-structure",
        action="store_true",
        help="현재 유효한 정본 구조도 새로 생성한다. 기존 산문은 작업 백업한다.",
    )
    parser.add_argument(
        "--scene-retries",
        type=int,
        help=(
            "한 장면의 전체 생성 실행 재시도 횟수. 0이면 성공할 때까지 재시도한다. "
            "기본값은 일반 모드 5회, 신규 세계관 모드 무제한이다."
        ),
    )
    args = parser.parse_args()
    if args.scene_retries is not None and args.scene_retries < 0:
        print("[FAIL] --scene-retries는 0 이상이어야 함")
        return 1
    try:
        instruction = (
            args.instruction_file.read_text(encoding="utf-8")
            if args.instruction_file
            else ""
        )
        llm = LazyLLM()
        regenerate_structure = args.regenerate_structure
        new_world_mode = args.new_world or args.game_scenario
        if new_world_mode:
            _, new_world_regenerate = prepare_new_world(
                args.root.resolve(),
                llm,
                instruction,
                args.game_scenario,
            )
            regenerate_structure = regenerate_structure or new_world_regenerate
        scene_retries = (
            args.scene_retries
            if args.scene_retries is not None
            else (0 if new_world_mode else 5)
        )
        result = complete_series(
            args.root,
            llm,
            instruction,
            regenerate_structure,
            scene_retries,
        )
    except Exception as exc:
        try:
            write_status(args.root.resolve(), "failed", error=str(exc))
        except OSError:
            pass
        print(f"[FAIL] {exc}")
        return 1
    if not result["complete"]:
        print(f"[STOP] 현재 장면 완료 뒤 중단. 이번 실행 {result['generated']}개 승인")
        return 0
    if args.new_world or args.game_scenario:
        finish_new_world(args.root.resolve(), result)
    print(
        f"[OK] 5권 자동 완주 완료. {result['scene_count']}개 장면, "
        f"이번 실행 {result['generated']}개 승인, EPUB {len(result['epubs'])}개"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
