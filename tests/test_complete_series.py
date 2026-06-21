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
    promote_with_prose_backup,
    validate_all_prose,
)
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


if __name__ == "__main__":
    unittest.main()
