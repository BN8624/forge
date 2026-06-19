# 검증된 구조 문서에서 정본 상태 원장을 결정적으로 재구성하는 도구
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.validate_structure import OWNER_KEYS, validate_project, validate_schema


class StateRebuildError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_state_ledger(root: Path) -> dict[str, Any]:
    story = root / "story"
    series = read_json(story / "series.json")
    applied_element_ids: list[str] = []
    last_scene: dict[str, Any] | None = None

    for volume_id in series["volume_ids"]:
        volume = read_json(story / "volumes" / f"{volume_id}.json")
        for event_id in volume["event_ids"]:
            event = read_json(story / "events" / f"{event_id}.json")
            for scene_id in event["scene_ids"]:
                scene = read_json(story / "scenes" / f"{scene_id}.json")
                for owner_key in OWNER_KEYS:
                    applied_element_ids.extend(scene["owns"][owner_key])
                last_scene = scene

    if last_scene is None:
        raise StateRebuildError(["상태 원장을 파생할 장면이 없음"])

    return {
        "last_scene_id": last_scene["id"],
        "state": last_scene["end_state"],
        "applied_element_ids": applied_element_ids,
    }


def encode_ledger(ledger: dict[str, Any]) -> bytes:
    return (
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def rebuild_state(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors = validate_project(root, check_ledger=False)
    if errors:
        raise StateRebuildError(errors)

    ledger = derive_state_ledger(root)
    schema_errors: list[str] = []
    validate_schema(
        ledger,
        "state_ledger.schema.json",
        "state/current.json",
        schema_errors,
    )
    if schema_errors:
        raise StateRebuildError(schema_errors)

    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = state_dir / "current.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".current-",
        suffix=".json.tmp",
        dir=state_dir,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(encode_ledger(ledger))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, ledger_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise StateRebuildError([f"상태 원장 저장 실패: {exc}"]) from exc

    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(
        description="검증된 구조 문서에서 상태 원장을 재구성한다."
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()

    try:
        ledger = rebuild_state(args.root)
    except StateRebuildError as exc:
        for error in exc.errors:
            print(f"[ERROR] {error}")
        print(f"[FAIL] 상태 원장 재구성 실패. {len(exc.errors)}개 오류")
        return 1

    print(f"[OK] 상태 원장 재구성 완료: {ledger['last_scene_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
