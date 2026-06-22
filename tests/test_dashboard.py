# 모바일 대시보드의 렌더링과 백그라운드 명령 생성을 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.dashboard import DashboardController, DashboardError, render_dashboard
from tests.test_generate_synopses import candidates_response, review_response


class FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid

    def poll(self):
        return None


class FakePopen:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return FakeProcess()


class DashboardTests(unittest.TestCase):
    def test_dashboard_is_mobile_and_contains_workflow_controls(self) -> None:
        page = render_dashboard("secret-token").decode("utf-8")

        self.assertIn('name="viewport"', page)
        self.assertIn("후보 5개 만들기", page)
        self.assertIn("선택한 기획으로 5권 시작", page)
        self.assertIn("중단 작업 재개", page)
        self.assertIn('data-token="secret-token"', page)

    def test_generate_concepts_starts_background_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakePopen()
            controller = DashboardController(Path(directory), fake)

            job = controller.generate_concepts("탐험 중심")

            command = fake.calls[0][0]
            self.assertEqual("concepts", job["kind"])
            self.assertIn("pipeline/generate_synopses.py", command)
            self.assertIn("--instruction-file", command)
            self.assertEqual("running", controller.status()["job"]["status"])

    def test_selected_candidate_starts_reused_concept_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            concept = root / "runs" / "new-world" / "concept"
            concept.mkdir(parents=True)
            (concept / "synopsis-candidates.json").write_text(
                json.dumps(candidates_response(), ensure_ascii=False),
                encoding="utf-8",
            )
            (concept / "synopsis-review.json").write_text(
                json.dumps(review_response(), ensure_ascii=False),
                encoding="utf-8",
            )
            fake = FakePopen()
            controller = DashboardController(root, fake)

            controller.start_series("S4")

            command = fake.calls[0][0]
            self.assertIn("pipeline/complete_series.py", command)
            self.assertIn("--reuse-concept", command)
            self.assertEqual("S4", command[-1])

    def test_unknown_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory), FakePopen())

            with self.assertRaises(DashboardError):
                controller.start_series("S9")

    def test_active_world_can_be_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_path = root / "runs" / "new-world" / "active.json"
            active_path.parent.mkdir(parents=True)
            active_path.write_text(
                json.dumps({"status": "active"}, ensure_ascii=False),
                encoding="utf-8",
            )
            fake = FakePopen()
            controller = DashboardController(root, fake)

            controller.resume_series()

            self.assertEqual(
                ["pipeline/complete_series.py", "--game-scenario"],
                fake.calls[0][0][-2:],
            )

    def test_active_world_blocks_new_concept_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_path = root / "runs" / "new-world" / "active.json"
            active_path.parent.mkdir(parents=True)
            active_path.write_text(
                json.dumps({"status": "active"}, ensure_ascii=False),
                encoding="utf-8",
            )
            controller = DashboardController(root, FakePopen())

            with self.assertRaises(DashboardError):
                controller.generate_concepts("")


if __name__ == "__main__":
    unittest.main()
