# Forge가 권별 장편 구조를 생성해 하나의 검증된 후보로 조립하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.expand_structure import (
    StructureExpansionError,
    expand_structure,
    normalize_consumed_setups,
)
from pipeline.validate_canon import story_sha256
from pipeline.validate_scale import validate_story_scale
from pipeline.validate_structure import validate_project
from tests.test_generate_candidate import FakeLLM
from tests.test_validate_structure import build_project


def expanded_volume_response(volume_index: int, scene_target: int = 4_000) -> str:
    volume_id = f"V{volume_index}"
    event_ids = [f"{volume_id}-E{index:02d}" for index in range(1, 5)]
    events = []
    scenes = []
    for event_index, event_id in enumerate(event_ids, start=1):
        scene_ids = [f"{event_id}-S{index:02d}" for index in range(1, 6)]
        events.append(
            {
                "id": event_id,
                "volume_id": volume_id,
                "sequence": event_index,
                "objective": f"{volume_id} 확장 사건 {event_index}",
                "start_state": {},
                "end_state": {},
                "scene_ids": scene_ids,
            }
        )
        for scene_index, scene_id in enumerate(scene_ids, start=1):
            owns = {"changes": [], "setups": [], "payoffs": []}
            if event_index == 1 and scene_index == 1:
                owns["changes"] = [f"CHG-{volume_index}"]
            scenes.append(
                {
                    "id": scene_id,
                    "event_id": event_id,
                    "sequence": scene_index,
                    "objective": f"{volume_id} 확장 장면 {event_index}-{scene_index}",
                    "interaction_mode": "interpersonal",
                    "dialogue_policy": "required",
                    "previous_scene_id": None,
                    "start_state": {},
                    "end_state": {
                        "volume": volume_index,
                        "event": event_index,
                        "scene": scene_index,
                    },
                    "owns": owns,
                    "consumes_setups": [],
                    "target_chars": scene_target,
                }
            )
    return json.dumps(
        {
            "volume": {
                "id": volume_id,
                "index": volume_index,
                "series_id": "SERIES",
                "title": f"{volume_index}권 확장",
                "objective": f"{volume_index}권 목표",
                "start_state": {},
                "end_state": {},
                "event_ids": event_ids,
            },
            "events": events,
            "scenes": scenes,
        },
        ensure_ascii=False,
    )


class ExpandStructureTests(unittest.TestCase):
    def test_unknown_or_canon_setup_references_are_removed(self) -> None:
        response = json.loads(expanded_volume_response(1))
        response["scenes"][0]["consumes_setups"] = ["C13", "SETUP-1", "UNKNOWN"]

        normalize_consumed_setups(response, {"SETUP-1"})

        self.assertEqual(
            ["SETUP-1"],
            response["scenes"][0]["consumes_setups"],
        )

    def test_five_expanded_volumes_are_assembled_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "project"
            output = workspace / "expanded"
            build_project(root)
            llm = FakeLLM(
                [expanded_volume_response(index) for index in range(1, 6)]
            )

            expand_structure(root, output, llm)

            self.assertEqual([], validate_project(output, check_ledger=False))
            self.assertEqual([], validate_story_scale(output))
            self.assertEqual(5, len(llm.calls))
            self.assertTrue(all(call[0] == "generator" for call in llm.calls))

            second_llm = FakeLLM([])
            second_output = workspace / "expanded-second"
            expand_structure(root, second_output, second_llm)
            self.assertEqual([], second_llm.calls)
            self.assertEqual([], validate_story_scale(second_output))

    def test_invalid_volume_is_retried_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "project"
            output = workspace / "expanded"
            build_project(root)
            llm = FakeLLM(
                [
                    expanded_volume_response(1, scene_target=500),
                    expanded_volume_response(1),
                    *[
                        expanded_volume_response(index)
                        for index in range(2, 6)
                    ],
                ]
            )

            expand_structure(root, output, llm)

            self.assertEqual(6, len(llm.calls))
            self.assertIn("권별 확장 오류", llm.calls[1][1])
            self.assertIn("목표 분량", llm.calls[1][1])

    def test_matching_owned_element_summary_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "project"
            output = workspace / "expanded"
            build_project(root)
            responses = []
            for index in range(1, 6):
                value = json.loads(expanded_volume_response(index))
                value["owned_element_ids"] = [f"CHG-{index}"]
                responses.append(json.dumps(value, ensure_ascii=False))

            expand_structure(root, output, FakeLLM(responses))

            self.assertEqual([], validate_project(output, check_ledger=False))

    def test_previously_failed_response_can_be_recovered_after_contract_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "project"
            output = workspace / "expanded"
            build_project(root)
            work = (
                workspace
                / "expansion-work"
                / story_sha256(root)
                / "failures"
            )
            work.mkdir(parents=True)
            for index in range(1, 6):
                value = json.loads(expanded_volume_response(index))
                value["owned_element_ids"] = [f"CHG-{index}"]
                (work / f"V{index}-attempt-1.txt").write_text(
                    json.dumps(value, ensure_ascii=False),
                    encoding="utf-8",
                )
            llm = FakeLLM([])

            expand_structure(root, output, llm)

            self.assertEqual([], llm.calls)
            self.assertEqual([], validate_story_scale(output))

    def test_retry_exhaustion_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "project"
            output = workspace / "expanded"
            build_project(root)
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")
            invalid = expanded_volume_response(1, scene_target=500)

            with self.assertRaisesRegex(StructureExpansionError, "V1.*3회"):
                expand_structure(
                    root,
                    output,
                    FakeLLM([invalid, invalid, invalid]),
                )

            self.assertEqual("기존 후보", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
