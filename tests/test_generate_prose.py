# Forge의 장면별 산문 생성, critic 검증, 순차 승격을 검증하는 테스트
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.generate_prose import (
    ProseGenerationError,
    generate_all_prose,
    generate_prose_scene,
)
from tests.test_generate_candidate import FakeLLM
from tests.test_promote_candidate import approve_candidate
from tests.test_validate_structure import build_project


def approve_story(root: Path) -> None:
    approve_candidate(root)
    (root / "story" / "canon-review.json").write_bytes(
        (root / "canon-review.json").read_bytes()
    )
    (root / "canon-review.json").unlink()


def prose_response(scene_id: str, length: int = 1800) -> str:
    prose = ("차가운 바람이 감시 수정구 아래 거리를 훑었다. " * 100)[:length]
    return json.dumps(
        {"scene_id": scene_id, "prose": prose},
        ensure_ascii=False,
    )


def review_response(scene_id: str, status: str = "pass") -> str:
    passed = status == "pass"
    return json.dumps(
        {
            "scene_id": scene_id,
            "status": status,
            "checks": {
                "objective": passed,
                "state_transition": passed,
                "owned_elements": passed,
                "canon": passed,
                "continuity": passed,
                "prose_quality": passed,
            },
            "issues": [] if passed else ["장면 목표가 충분히 드러나지 않음"],
            "reason": "모든 계약 충족" if passed else "장면 목표 미달",
        },
        ensure_ascii=False,
    )


class GenerateProseTests(unittest.TestCase):
    def test_first_scene_is_generated_reviewed_and_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            prose_path = result / "prose.md"
            review_path = result / "review.json"
            prose = prose_path.read_text(encoding="utf-8")
            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertTrue(prose)
            self.assertEqual("pass", review["status"])
            self.assertEqual(
                hashlib.sha256(prose.encode("utf-8")).hexdigest(),
                review["prose_sha256"],
            )
            self.assertEqual(["generator", "critic"], [call[0] for call in llm.calls])

    def test_critic_failure_is_sent_back_to_generator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01", "fail"),
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertEqual(4, len(llm.calls))
            self.assertIn("장면 목표가 충분히", llm.calls[2][1])

    def test_second_scene_requires_previous_approved_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)

            with self.assertRaisesRegex(ProseGenerationError, "이전 정본 산문"):
                generate_prose_scene(
                    root,
                    "V2-E01-S01",
                    FakeLLM([]),
                    check_scale=False,
                )

    def test_existing_scene_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            target = root / "prose" / "scenes" / "V1-E01-S01"
            target.mkdir(parents=True)
            (target / "prose.md").write_text("기존 산문", encoding="utf-8")

            with self.assertRaisesRegex(ProseGenerationError, "이미 존재"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    FakeLLM([]),
                    check_scale=False,
                )

            self.assertEqual(
                "기존 산문",
                (target / "prose.md").read_text(encoding="utf-8"),
            )

    def test_short_prose_is_rejected_before_critic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM([prose_response("V1-E01-S01", 50)] * 3)

            with self.assertRaisesRegex(ProseGenerationError, "3회"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    llm,
                    check_scale=False,
                )

            self.assertEqual(["generator"] * 3, [call[0] for call in llm.calls])

    def test_batch_generates_scenes_in_order_with_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                    prose_response("V2-E01-S01"),
                    review_response("V2-E01-S01"),
                ]
            )

            results = generate_all_prose(
                root,
                llm,
                limit=2,
                check_scale=False,
            )

            self.assertEqual(
                ["V1-E01-S01", "V2-E01-S01"],
                [path.name for path in results],
            )

    def test_production_generation_rejects_short_volume_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)

            with self.assertRaisesRegex(ProseGenerationError, "장편 분량 부족"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    FakeLLM([]),
                )



if __name__ == "__main__":
    unittest.main()
