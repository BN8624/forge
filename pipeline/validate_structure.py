# Forge 구조 문서의 계층, 상태 연속성, 이야기 요소 소유권을 검증하는 도구
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


VOLUME_ID = re.compile(r"^V([1-9]|[1-9][0-9])$")
EVENT_ID = re.compile(r"^(V(?:[1-9]|[1-9][0-9]))-E(\d{2})$")
SCENE_ID = re.compile(r"^(V(?:[1-9]|[1-9][0-9])-E\d{2})-S(\d{2})$")
OWNER_KEYS = {"changes": "change", "setups": "setup", "payoffs": "payoff"}
SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"


def load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"필수 파일 없음: {path}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"JSON 오류: {path}:{exc.lineno}:{exc.colno} {exc.msg}")
        return None
    if not isinstance(value, dict):
        errors.append(f"최상위 값은 객체여야 함: {path}")
        return None
    return value


@lru_cache(maxsize=None)
def load_schema(schema_name: str) -> dict[str, Any]:
    schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_schema(
    document: dict[str, Any],
    schema_name: str,
    label: str,
    errors: list[str],
) -> bool:
    validator = Draft202012Validator(load_schema(schema_name))
    violations = sorted(
        validator.iter_errors(document),
        key=lambda violation: tuple(str(part) for part in violation.absolute_path),
    )
    for violation in violations:
        location = ".".join(str(part) for part in violation.absolute_path)
        location = f"$.{location}" if location else "$"
        errors.append(
            f"JSON Schema 위반: {label} ({schema_name}) {location}: "
            f"{violation.message}"
        )
    return not violations


def load_document(
    path: Path,
    schema_name: str,
    label: str,
    errors: list[str],
) -> dict[str, Any] | None:
    document = load_json(path, errors)
    if document is None:
        return None
    if not validate_schema(document, schema_name, label, errors):
        return None
    return document


def require_fields(
    document: dict[str, Any],
    fields: tuple[str, ...],
    label: str,
    errors: list[str],
) -> bool:
    missing = [field for field in fields if field not in document]
    if missing:
        errors.append(f"{label} 필수 필드 누락: {', '.join(missing)}")
        return False
    return True


def validate_project(root: Path, check_ledger: bool = True) -> list[str]:
    story = root / "story"
    errors: list[str] = []
    series = load_document(
        story / "series.json",
        "series.schema.json",
        "series",
        errors,
    )
    if series is None:
        return errors
    if not require_fields(
        series,
        ("id", "title", "premise", "theme", "ending", "volume_ids", "elements"),
        "series",
        errors,
    ):
        return errors

    if series["id"] != "SERIES":
        errors.append("series.id는 SERIES여야 함")
    volume_ids = series["volume_ids"]
    expected_volumes = [
        f"V{index}"
        for index in range(1, len(volume_ids) + 1)
    ]
    if volume_ids != expected_volumes:
        errors.append(
            "series.volume_ids는 V1부터 승인된 권수까지 "
            "정확히 한 번씩 순서대로 있어야 함"
        )

    elements: dict[str, dict[str, Any]] = {}
    for index, element in enumerate(series["elements"]):
        label = f"series.elements[{index}]"
        if not isinstance(element, dict) or not require_fields(
            element, ("id", "kind", "description"), label, errors
        ):
            continue
        element_id = element["id"]
        if element_id in elements:
            errors.append(f"이야기 요소 ID 중복: {element_id}")
            continue
        if element["kind"] not in {"change", "setup", "payoff"}:
            errors.append(f"알 수 없는 이야기 요소 종류: {element_id}")
        elements[element_id] = element

    for element_id, element in elements.items():
        if element.get("kind") == "payoff":
            setup_id = element.get("resolves")
            if not setup_id or elements.get(setup_id, {}).get("kind") != "setup":
                errors.append(f"회수 요소의 resolves가 유효한 setup이 아님: {element_id}")

    owners: dict[str, list[str]] = defaultdict(list)
    applied_element_ids: list[str] = []
    scene_order: dict[str, int] = {}
    ordered_scenes: list[dict[str, Any]] = []
    previous_volume_end: Any = None
    previous_scene_id: str | None = None

    for volume_index, volume_id in enumerate(expected_volumes, start=1):
        volume = load_document(
            story / "volumes" / f"{volume_id}.json",
            "volume.schema.json",
            volume_id,
            errors,
        )
        if volume is None:
            continue
        if not require_fields(
            volume,
            (
                "id",
                "index",
                "series_id",
                "title",
                "objective",
                "start_state",
                "end_state",
                "event_ids",
            ),
            volume_id,
            errors,
        ):
            continue
        if volume["id"] != volume_id or volume["index"] != volume_index:
            errors.append(f"권 식별자 또는 순번 불일치: {volume_id}")
        if volume["series_id"] != "SERIES":
            errors.append(f"series_id 불일치: {volume_id}")
        if volume_index > 1 and volume["start_state"] != previous_volume_end:
            errors.append(f"권 상태 단절: {volume_id}.start_state")
        previous_volume_end = volume["end_state"]

        event_ids = volume["event_ids"]
        if not isinstance(event_ids, list) or not event_ids:
            errors.append(f"event_ids는 비어 있지 않은 배열이어야 함: {volume_id}")
            continue
        previous_event_end: Any = None
        for event_index, event_id in enumerate(event_ids, start=1):
            match = EVENT_ID.fullmatch(str(event_id))
            if not match or match.group(1) != volume_id:
                errors.append(f"사건 ID가 권과 맞지 않음: {event_id}")
                continue
            event = load_document(
                story / "events" / f"{event_id}.json",
                "event.schema.json",
                event_id,
                errors,
            )
            if event is None:
                continue
            if not require_fields(
                event,
                ("id", "volume_id", "sequence", "objective", "start_state", "end_state", "scene_ids"),
                event_id,
                errors,
            ):
                continue
            if (
                event["id"] != event_id
                or event["volume_id"] != volume_id
                or event["sequence"] != event_index
            ):
                errors.append(f"사건 식별자, 소속 또는 순번 불일치: {event_id}")
            expected_start = volume["start_state"] if event_index == 1 else previous_event_end
            if event["start_state"] != expected_start:
                errors.append(f"사건 상태 단절: {event_id}.start_state")
            previous_event_end = event["end_state"]

            scene_ids = event["scene_ids"]
            if not isinstance(scene_ids, list) or not scene_ids:
                errors.append(f"scene_ids는 비어 있지 않은 배열이어야 함: {event_id}")
                continue
            previous_local_end: Any = None
            for scene_index, scene_id in enumerate(scene_ids, start=1):
                match = SCENE_ID.fullmatch(str(scene_id))
                if not match or match.group(1) != event_id:
                    errors.append(f"장면 ID가 사건과 맞지 않음: {scene_id}")
                    continue
                scene = load_document(
                    story / "scenes" / f"{scene_id}.json",
                    "scene.schema.json",
                    scene_id,
                    errors,
                )
                if scene is None:
                    continue
                if not require_fields(
                    scene,
                    (
                        "id",
                        "event_id",
                        "sequence",
                        "objective",
                        "interaction_mode",
                        "dialogue_policy",
                        "previous_scene_id",
                        "start_state",
                        "end_state",
                        "owns",
                        "consumes_setups",
                        "target_chars",
                    ),
                    scene_id,
                    errors,
                ):
                    continue
                if (
                    scene["id"] != scene_id
                    or scene["event_id"] != event_id
                    or scene["sequence"] != scene_index
                ):
                    errors.append(f"장면 식별자, 소속 또는 순번 불일치: {scene_id}")
                if scene["previous_scene_id"] != previous_scene_id:
                    errors.append(f"이전 장면 연결 불일치: {scene_id}.previous_scene_id")
                expected_start = event["start_state"] if scene_index == 1 else previous_local_end
                if scene["start_state"] != expected_start:
                    errors.append(f"장면 상태 단절: {scene_id}.start_state")
                previous_local_end = scene["end_state"]
                previous_scene_id = scene_id
                scene_order[scene_id] = len(ordered_scenes)
                ordered_scenes.append(scene)

                owns = scene["owns"]
                if not isinstance(owns, dict):
                    errors.append(f"owns는 객체여야 함: {scene_id}")
                    continue
                for owner_key, kind in OWNER_KEYS.items():
                    owned_ids = owns.get(owner_key)
                    if not isinstance(owned_ids, list):
                        errors.append(f"owns.{owner_key}는 배열이어야 함: {scene_id}")
                        continue
                    if len(owned_ids) != len(set(owned_ids)):
                        errors.append(f"한 장면 안에서 소유권 중복: {scene_id}.owns.{owner_key}")
                    for element_id in owned_ids:
                        applied_element_ids.append(element_id)
                        owners[element_id].append(scene_id)
                        actual_kind = elements.get(element_id, {}).get("kind")
                        if actual_kind != kind:
                            errors.append(
                                f"소유권 종류 불일치: {scene_id} -> {element_id} "
                                f"({kind} 자리에 {actual_kind})"
                            )
            if previous_local_end != event["end_state"]:
                errors.append(f"마지막 장면과 사건 종료 상태 불일치: {event_id}")
        if previous_event_end != volume["end_state"]:
            errors.append(f"마지막 사건과 권 종료 상태 불일치: {volume_id}")

    for element_id in elements:
        owner_list = owners.get(element_id, [])
        if len(owner_list) != 1:
            errors.append(
                f"이야기 요소는 정확히 한 장면이 소유해야 함: {element_id} "
                f"(현재 {len(owner_list)}개)"
            )
    for element_id in owners:
        if element_id not in elements:
            errors.append(f"정의되지 않은 이야기 요소 소유: {element_id}")

    owner_order = {
        element_id: scene_order[owner_list[0]]
        for element_id, owner_list in owners.items()
        if len(owner_list) == 1 and owner_list[0] in scene_order
    }
    for element_id, element in elements.items():
        if element.get("kind") == "payoff" and element_id in owner_order:
            setup_id = element.get("resolves")
            if setup_id in owner_order and owner_order[setup_id] >= owner_order[element_id]:
                errors.append(f"복선보다 회수가 먼저이거나 같은 장면임: {element_id}")

    for scene in ordered_scenes:
        for setup_id in scene["consumes_setups"]:
            if elements.get(setup_id, {}).get("kind") != "setup":
                errors.append(f"유효하지 않은 consumes_setups 참조: {scene['id']} -> {setup_id}")
            elif setup_id in owner_order and owner_order[setup_id] >= scene_order[scene["id"]]:
                errors.append(f"복선 설치 전에 참조함: {scene['id']} -> {setup_id}")

    ledger_path = root / "state" / "current.json"
    if check_ledger and ledger_path.exists():
        ledger = load_document(
            ledger_path,
            "state_ledger.schema.json",
            "state/current.json",
            errors,
        )
        if ledger and ordered_scenes:
            last_scene = ordered_scenes[-1]
            if ledger.get("last_scene_id") != last_scene["id"]:
                errors.append("상태 원장의 last_scene_id가 마지막 구조 장면과 다름")
            if ledger.get("state") != last_scene["end_state"]:
                errors.append("상태 원장의 state가 마지막 구조 장면의 end_state와 다름")
            if ledger.get("applied_element_ids") != applied_element_ids:
                errors.append(
                    "상태 원장의 applied_element_ids가 장면 순서의 소유 요소와 다름"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge 구조 문서를 검증한다.")
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = validate_project(args.root.resolve())
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        print(f"[FAIL] {len(errors)}개 오류")
        return 1
    print("[OK] Forge 구조 문서 검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
