# 5권 완주 오케스트레이터의 재실행, 재개, 산문 백업 복구를 검증하는 테스트
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.complete_series import (
    backup_invalid_prose_suffix,
    complete_series,
    ensure_reviewed_candidate,
    prepare_new_world,
    promote_with_prose_backup,
    validate_all_prose,
)
from pipeline.validate_canon import CanonReviewError
from pipeline.generate_prose import contract_sha256, ordered_scene_ids
from tests.test_generate_candidate import FakeLLM
from tests.test_generate_prose import approve_story, prose_response, review_response
from tests.test_validate_structure import build_project


def approve_prose(root: Path, scene_id: str) -> None:
    scene = json.loads(
        (root / "story" / "scenes" / f"{scene_id}.json").read_text(encoding="utf-8")
    )
    prose = ("차가운 바람이 감시 수정구 아래 거리를 훑었다. " * 120)[:1800]
    review = json.loads(review_response(scene_id))
    review["prose_sha256"] = hashlib.sha256(prose.encode("utf-8")).hexdigest()
    review["scene_contract_sha256"] = contract_sha256(scene)
    directory = root / "prose" / "scenes" / scene_id
    directory.mkdir(parents=True)
    (directory / "prose.md").write_text(prose, encoding="utf-8")
    (directory / "review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class CompleteSeriesTests(unittest.TestCase):
    def make_project(self, root: Path) -> list[str]:
        build_project(root)
        approve_story(root)
        return ordered_scene_ids(root)

    def test_canon_rejection_is_sent_back_to_structure_generator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            instructions: list[str] = []

            def create_candidate(instruction: str) -> None:
                instructions.append(instruction)

            with (
                patch(
                    "pipeline.complete_series.approved_candidate",
                    return_value=False,
                ),
                patch(
                    "pipeline.complete_series.validate_canon_candidate",
                    side_effect=[
                        CanonReviewError("C19 최종 보스 결정 순서 위반"),
                        {"status": "pass"},
                    ],
                ),
            ):
                ensure_reviewed_candidate(
                    candidate,
                    FakeLLM([]),
                    create_candidate,
                    "기본 지시",
                )

            self.assertEqual(2, len(instructions))
            self.assertEqual("기본 지시", instructions[0])
            self.assertIn("C19 최종 보스 결정 순서 위반", instructions[1])
            self.assertIn("전체 구조를 다시 작성", instructions[1])

    def test_completed_project_reuses_all_approved_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene_ids = self.make_project(root)
            for scene_id in scene_ids:
                approve_prose(root, scene_id)

            with patch(
                "pipeline.complete_series.validate_story_scale",
                return_value=[],
            ):
                result = complete_series(root, FakeLLM([]))

            self.assertTrue(result["complete"])
            self.assertEqual(0, result["generated"])
            self.assertEqual(5, len(result["epubs"]))
            self.assertEqual([], validate_all_prose(root))
            status = json.loads(
                (
                    root / "runs" / "complete-series" / "status.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("complete", status["stage"])

    def test_incomplete_project_resumes_only_missing_last_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene_ids = self.make_project(root)
            for scene_id in scene_ids[:-1]:
                approve_prose(root, scene_id)
            last_scene_id = scene_ids[-1]
            llm = FakeLLM(
                [
                    prose_response(last_scene_id),
                    review_response(last_scene_id),
                ]
            )

            with patch(
                "pipeline.complete_series.validate_story_scale",
                return_value=[],
            ):
                result = complete_series(root, llm)

            self.assertEqual(1, result["generated"])
            self.assertEqual(["generator", "critic"], [call[0] for call in llm.calls])
            self.assertEqual([], validate_all_prose(root))

    def test_failed_structure_promotion_restores_prose_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "root"
            candidate = workspace / "candidate"
            self.make_project(root)
            approve_prose(root, ordered_scene_ids(root)[0])
            build_project(candidate)
            series_path = candidate / "story" / "series.json"
            series = json.loads(series_path.read_text(encoding="utf-8"))
            series["title"] = "변경된 구조"
            series_path.write_text(
                json.dumps(series, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with patch(
                "pipeline.complete_series.promote_candidate",
                side_effect=RuntimeError("승격 실패"),
            ):
                with self.assertRaisesRegex(RuntimeError, "승격 실패"):
                    promote_with_prose_backup(root, candidate)

            self.assertTrue((root / "prose" / "scenes").is_dir())
            self.assertTrue(
                (root / "prose" / "scenes" / ordered_scene_ids(root)[0] / "prose.md").is_file()
            )

    def test_stop_file_prevents_starting_next_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene_ids = self.make_project(root)
            for scene_id in scene_ids[:-1]:
                approve_prose(root, scene_id)
            (root / "STOP_AFTER_RUN").write_text("", encoding="utf-8")
            llm = FakeLLM([])

            with patch(
                "pipeline.complete_series.validate_story_scale",
                return_value=[],
            ):
                result = complete_series(root, llm)

            self.assertFalse(result["complete"])
            self.assertEqual(0, result["generated"])
            self.assertEqual([], llm.calls)
            status = json.loads(
                (
                    root / "runs" / "complete-series" / "status.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("stopped", status["stage"])
            self.assertEqual(scene_ids[-1], status["next_scene_id"])

    def test_invalid_review_backs_up_only_invalid_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene_ids = self.make_project(root)
            for scene_id in scene_ids:
                approve_prose(root, scene_id)
            invalid_id = scene_ids[2]
            review_path = root / "prose" / "scenes" / invalid_id / "review.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["status"] = "fail"
            review_path.write_text(
                json.dumps(review, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            backup = backup_invalid_prose_suffix(root)

            self.assertIsNotNone(backup)
            self.assertTrue(
                (root / "prose" / "scenes" / scene_ids[1] / "prose.md").is_file()
            )
            self.assertFalse((root / "prose" / "scenes" / invalid_id).exists())
            self.assertTrue((backup / "scenes" / invalid_id / "prose.md").is_file())
            self.assertTrue(
                (backup / "scenes" / scene_ids[-1] / "prose.md").is_file()
            )

    def test_new_world_is_archived_once_and_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene_ids = self.make_project(root)
            approve_prose(root, scene_ids[0])
            source = {
                "title": "새 세계",
                "premise": "완전히 새로운 장편 전제",
                "canon": [],
            }

            def create_world(_instruction, output, _llm):
                output.mkdir(parents=True)
                (output / "canon_bible.json").write_text(
                    json.dumps(source, ensure_ascii=False),
                    encoding="utf-8",
                )
                (output / "compressed_manuscript.md").write_text(
                    "새 원고",
                    encoding="utf-8",
                )

            with (
                patch(
                    "pipeline.complete_series.generate_world",
                    side_effect=create_world,
                ) as generator,
                patch(
                    "pipeline.complete_series.load_source_material",
                    return_value=(source, "새 원고"),
                ),
            ):
                backup, regenerate = prepare_new_world(
                    root,
                    FakeLLM([]),
                    "",
                )

                self.assertTrue(regenerate)
                self.assertTrue((backup / "story" / "series.json").is_file())
                self.assertTrue((backup / "prose" / "scenes").is_dir())

                series_path = root / "story" / "series.json"
                series = json.loads(series_path.read_text(encoding="utf-8"))
                series["title"] = source["title"]
                series["premise"] = source["premise"]
                series_path.write_text(
                    json.dumps(series, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                resumed_backup, resumed_regenerate = prepare_new_world(
                    root,
                    FakeLLM([]),
                    "",
                )

            self.assertEqual(backup, resumed_backup)
            self.assertFalse(resumed_regenerate)
            self.assertEqual(1, generator.call_count)

    def test_game_scenario_selects_concept_before_world_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            source = {
                "title": "선택된 게임 세계",
                "premise": "게임 시나리오 후보 평가에서 선택된 장편 전제",
                "canon": [],
            }

            def create_concept(_instruction, output, _llm):
                output.mkdir(parents=True)
                for name, value in (
                    ("synopsis-candidates.json", {"candidates": []}),
                    ("synopsis-review.json", {"selected_id": "S4"}),
                    ("selected-synopsis.json", {"id": "S4", "title": "선택 기획"}),
                    (
                        "concept-selection.json",
                        {
                            "selected_id": "S4",
                            "selected_by": "critic",
                            "critic_recommendation": "S4",
                        },
                    ),
                ):
                    (output / name).write_text(
                        json.dumps(value, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return "critic이 선택한 구속 입력"

            def create_world(world_instruction, output, _llm, selected):
                self.assertEqual("critic이 선택한 구속 입력", world_instruction)
                self.assertEqual("S4", selected["id"])
                output.mkdir(parents=True)
                (output / "canon_bible.json").write_text(
                    json.dumps(source, ensure_ascii=False),
                    encoding="utf-8",
                )
                (output / "compressed_manuscript.md").write_text(
                    "선택된 원고",
                    encoding="utf-8",
                )

            with (
                patch(
                    "pipeline.complete_series.generate_game_concept",
                    side_effect=create_concept,
                ),
                patch(
                    "pipeline.complete_series.generate_world",
                    side_effect=create_world,
                ),
                patch(
                    "pipeline.complete_series.load_source_material",
                    return_value=(source, "선택된 원고"),
                ),
            ):
                _, regenerate = prepare_new_world(
                    root,
                    FakeLLM([]),
                    "",
                    game_scenario=True,
                )

            self.assertTrue(regenerate)
            self.assertTrue(
                (root / "reference" / "current" / "synopsis-review.json").is_file()
            )
            active = json.loads(
                (root / "runs" / "new-world" / "active.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("game-scenario", active["mode"])
            self.assertEqual("S4", active["selected_synopsis_id"])


if __name__ == "__main__":
    unittest.main()
