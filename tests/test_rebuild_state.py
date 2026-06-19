# 검증된 구조에서 상태 원장을 결정적으로 재구성하고 안전하게 저장하는 테스트
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.rebuild_state import StateRebuildError, rebuild_state
from pipeline.validate_structure import validate_project
from tests.test_validate_structure import build_project, write_json


def add_setup_and_payoff(root: Path) -> None:
    series_path = root / "story" / "series.json"
    series = json.loads(series_path.read_text(encoding="utf-8"))
    series["elements"].extend(
        [
            {"id": "SET-1", "kind": "setup", "description": "검증용 복선"},
            {
                "id": "PAY-1",
                "kind": "payoff",
                "description": "검증용 회수",
                "resolves": "SET-1",
            },
        ]
    )
    write_json(series_path, series)

    first_scene_path = root / "story" / "scenes" / "V1-E01-S01.json"
    first_scene = json.loads(first_scene_path.read_text(encoding="utf-8"))
    first_scene["owns"]["setups"] = ["SET-1"]
    write_json(first_scene_path, first_scene)

    second_scene_path = root / "story" / "scenes" / "V2-E01-S01.json"
    second_scene = json.loads(second_scene_path.read_text(encoding="utf-8"))
    second_scene["owns"]["payoffs"] = ["PAY-1"]
    second_scene["consumes_setups"] = ["SET-1"]
    write_json(second_scene_path, second_scene)


class RebuildStateTests(unittest.TestCase):
    def test_rebuild_writes_expected_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            add_setup_and_payoff(root)

            rebuild_state(root)

            ledger = json.loads(
                (root / "state" / "current.json").read_text(encoding="utf-8")
            )
            self.assertEqual("V5-E01-S01", ledger["last_scene_id"])
            self.assertEqual({"phase": 5}, ledger["state"])
            self.assertEqual(
                [
                    "CHG-1",
                    "SET-1",
                    "CHG-2",
                    "PAY-1",
                    "CHG-3",
                    "CHG-4",
                    "CHG-5",
                ],
                ledger["applied_element_ids"],
            )
            self.assertEqual([], validate_project(root))

    def test_rebuild_is_byte_identical_on_reexecution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)

            rebuild_state(root)
            first = (root / "state" / "current.json").read_bytes()
            rebuild_state(root)
            second = (root / "state" / "current.json").read_bytes()

            self.assertEqual(first, second)

    def test_invalid_structure_preserves_existing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root, duplicate_owner=True)
            ledger_path = root / "state" / "current.json"
            write_json(
                ledger_path,
                {
                    "last_scene_id": None,
                    "state": {"preserved": True},
                    "applied_element_ids": [],
                },
            )
            before = ledger_path.read_bytes()

            with self.assertRaises(StateRebuildError):
                rebuild_state(root)

            self.assertEqual(before, ledger_path.read_bytes())

    def test_replace_failure_preserves_existing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            ledger_path = root / "state" / "current.json"
            write_json(
                ledger_path,
                {
                    "last_scene_id": None,
                    "state": {"preserved": True},
                    "applied_element_ids": [],
                },
            )
            before = ledger_path.read_bytes()

            with patch(
                "pipeline.rebuild_state.os.replace",
                side_effect=OSError("교체 실패"),
            ):
                with self.assertRaises(StateRebuildError):
                    rebuild_state(root)

            self.assertEqual(before, ledger_path.read_bytes())

    def test_validator_rejects_wrong_applied_elements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            rebuild_state(root)
            ledger_path = root / "state" / "current.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["applied_element_ids"] = list(reversed(ledger["applied_element_ids"]))
            write_json(ledger_path, ledger)

            errors = validate_project(root)

            self.assertTrue(
                any("applied_element_ids" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
