# Forge critic의 정본 의미 판정과 후보 해시 게이트를 검증하는 테스트
import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.validate_canon import (
    CanonReviewError,
    story_sha256,
    validate_canon_candidate,
    validate_review,
)
from tests.test_generate_candidate import FakeLLM
from tests.test_validate_structure import build_project


def approved_review(candidate: Path) -> dict:
    scene_id = "V1-E01-S01"
    return {
        "story_sha256": story_sha256(candidate),
        "overall_pass": True,
        "verdicts": [
            {
                "canon_id": f"C{index}",
                "status": "pass",
                "scene_ids": [scene_id],
                "reason": f"C{index} 반영 확인",
            }
            for index in range(1, 22)
        ],
    }


class ValidateCanonTests(unittest.TestCase):
    def test_approved_review_is_saved_and_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            review = approved_review(candidate)
            llm = FakeLLM(json.dumps(review, ensure_ascii=False))

            result = validate_canon_candidate(candidate, llm)

            self.assertTrue(result["overall_pass"])
            self.assertEqual(
                review,
                json.loads(
                    (candidate / "canon-review.json").read_text(encoding="utf-8")
                ),
            )
            self.assertEqual([], validate_review(candidate, review))
            self.assertEqual("critic", llm.calls[0][0])

    def test_failed_verdict_is_rejected_and_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            review = approved_review(candidate)
            review["overall_pass"] = False
            review["verdicts"][0]["status"] = "fail"

            with self.assertRaises(CanonReviewError):
                validate_canon_candidate(
                    candidate,
                    FakeLLM(json.dumps(review, ensure_ascii=False)),
                )

            self.assertFalse((candidate / "canon-review.json").exists())

    def test_duplicate_or_missing_canon_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            review = approved_review(candidate)
            review["verdicts"][-1]["canon_id"] = "C1"

            errors = validate_review(candidate, review)

            self.assertTrue(any("C1-C21" in error for error in errors), errors)

    def test_unknown_scene_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            review = approved_review(candidate)
            review["verdicts"][0]["scene_ids"] = ["V1-E99-S99"]

            errors = validate_review(candidate, review)

            self.assertTrue(any("존재하지 않는 장면" in error for error in errors), errors)

    def test_review_becomes_stale_when_story_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            review = approved_review(candidate)
            scene_path = candidate / "story" / "scenes" / "V1-E01-S01.json"
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            scene["objective"] = "변경됨"
            scene_path.write_text(
                json.dumps(scene, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            errors = validate_review(candidate, review)

            self.assertTrue(any("해시" in error for error in errors), errors)

    def test_invalid_first_response_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            build_project(candidate)
            valid = approved_review(candidate)
            invalid = copy.deepcopy(valid)
            invalid["verdicts"] = invalid["verdicts"][:-1]
            llm = FakeLLM(
                [
                    json.dumps(invalid, ensure_ascii=False),
                    json.dumps(valid, ensure_ascii=False),
                ]
            )

            validate_canon_candidate(candidate, llm)

            self.assertEqual(2, len(llm.calls))
            self.assertIn("검토 형식 오류", llm.calls[1][1])


if __name__ == "__main__":
    unittest.main()
