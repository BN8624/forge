# 검증된 후보 구조만 정본으로 승격하고 실패 시 기존 정본을 보존하는 테스트
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.promote_candidate import (
    CandidateValidationError,
    PromotionError,
    promote_candidate,
    recover_incomplete_promotions,
)
from tests.test_validate_structure import build_project, write_json


def story_snapshot(root: Path) -> dict[str, bytes]:
    story = root / "story"
    return {
        str(path.relative_to(story)): path.read_bytes()
        for path in sorted(story.rglob("*"))
        if path.is_file()
    }


def set_series_title(root: Path, title: str) -> None:
    path = root / "story" / "series.json"
    series = json.loads(path.read_text(encoding="utf-8"))
    series["title"] = title
    write_json(path, series)


class PromoteCandidateTests(unittest.TestCase):
    def test_valid_candidate_replaces_canonical_story(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            candidate = Path(directory) / "candidate"
            build_project(root)
            build_project(candidate)
            set_series_title(root, "기존 정본")
            set_series_title(candidate, "승격 후보")

            promote_candidate(root, candidate)

            promoted = json.loads(
                (root / "story" / "series.json").read_text(encoding="utf-8")
            )
            self.assertEqual("승격 후보", promoted["title"])

    def test_invalid_candidate_preserves_canonical_story(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            candidate = Path(directory) / "candidate"
            build_project(root)
            build_project(candidate, duplicate_owner=True)
            before = story_snapshot(root)

            with self.assertRaises(CandidateValidationError):
                promote_candidate(root, candidate)

            self.assertEqual(before, story_snapshot(root))

    def test_replacement_failure_restores_canonical_story(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            candidate = Path(directory) / "candidate"
            build_project(root)
            build_project(candidate)
            before = story_snapshot(root)
            real_replace = os.replace
            call_count = 0

            def fail_second_replace(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("교체 실패")
                real_replace(source, destination)

            with patch(
                "pipeline.promote_candidate.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaises(PromotionError):
                    promote_candidate(root, candidate)

            self.assertEqual(before, story_snapshot(root))

    def test_interrupted_replacement_is_recovered_on_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            build_project(root)
            before = story_snapshot(root)
            staging_root = root / ".promotion-interrupted"
            staging_root.mkdir()
            os.replace(root / "story", staging_root / "story.previous")

            recover_incomplete_promotions(root)

            self.assertEqual(before, story_snapshot(root))
            self.assertFalse(staging_root.exists())


if __name__ == "__main__":
    unittest.main()
