# Forge 구조 검증기의 정상 경로와 소유권 실패를 검증하는 테스트
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.validate_structure import validate_project


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def build_project(
    root: Path,
    duplicate_owner: bool = False,
    volume_count: int = 5,
) -> None:
    elements = [
        {"id": f"CHG-{index}", "kind": "change", "description": f"{index}권 상태 변화"}
        for index in range(1, volume_count + 1)
    ]
    write_json(
        root / "story" / "series.json",
        {
            "id": "SERIES",
            "title": "테스트",
            "premise": "다섯 단계로 변하는 인물의 이야기",
            "theme": "선택과 책임",
            "ending": "마지막 선택의 대가를 받아들인다.",
            "volume_ids": [
                f"V{index}"
                for index in range(1, volume_count + 1)
            ],
            "elements": elements,
        },
    )

    previous_scene_id = None
    for index in range(1, volume_count + 1):
        volume_id = f"V{index}"
        event_id = f"{volume_id}-E01"
        scene_id = f"{event_id}-S01"
        start = {"phase": index - 1}
        end = {"phase": index}
        write_json(
            root / "story" / "volumes" / f"{volume_id}.json",
            {
                "id": volume_id,
                "index": index,
                "series_id": "SERIES",
                "title": f"{index}권",
                "objective": f"{index}번째 변화",
                "start_state": start,
                "end_state": end,
                "event_ids": [event_id],
            },
        )
        write_json(
            root / "story" / "events" / f"{event_id}.json",
            {
                "id": event_id,
                "volume_id": volume_id,
                "sequence": 1,
                "objective": f"{index}권의 핵심 사건",
                "start_state": start,
                "end_state": end,
                "scene_ids": [scene_id],
            },
        )
        changes = [f"CHG-{index}"]
        if duplicate_owner and index == 2:
            changes.append("CHG-1")
        write_json(
            root / "story" / "scenes" / f"{scene_id}.json",
            {
                "id": scene_id,
                "event_id": event_id,
                "sequence": 1,
                "objective": f"{index}권의 상태를 바꾼다.",
                "interaction_mode": "solo",
                "dialogue_policy": "none",
                "previous_scene_id": previous_scene_id,
                "start_state": start,
                "end_state": end,
                "owns": {"changes": changes, "setups": [], "payoffs": []},
                "consumes_setups": [],
                "target_chars": 2000,
            },
        )
        previous_scene_id = scene_id


class ValidateStructureTests(unittest.TestCase):
    def test_valid_five_volume_project_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            self.assertEqual([], validate_project(root))

    def test_valid_three_volume_project_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root, volume_count=3)
            self.assertEqual([], validate_project(root))

    def test_duplicate_owner_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root, duplicate_owner=True)
            errors = validate_project(root)
            self.assertTrue(any("CHG-1 (현재 2개)" in error for error in errors), errors)

    def test_series_additional_property_fails_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            path = root / "story" / "series.json"
            series = json.loads(path.read_text(encoding="utf-8"))
            series["unexpected"] = True
            write_json(path, series)

            errors = validate_project(root)

            self.assertTrue(
                any("series.schema.json" in error and "unexpected" in error for error in errors),
                errors,
            )

    def test_scene_wrong_type_fails_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            path = root / "story" / "scenes" / "V1-E01-S01.json"
            scene = json.loads(path.read_text(encoding="utf-8"))
            scene["target_chars"] = "2000"
            write_json(path, scene)

            errors = validate_project(root)

            self.assertTrue(
                any(
                    "scene.schema.json" in error and "target_chars" in error
                    for error in errors
                ),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
