# 모바일 대시보드의 렌더링과 백그라운드 명령 생성을 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.dashboard import DashboardController, DashboardError, render_dashboard
from tests.test_generate_synopses import candidates_response, review_response


class FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid

    def poll(self):
        return None

    def terminate(self):
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
        self.assertIn("새 후보 5개 다시 만들기", page)
        self.assertIn("선택 기획·권수 승인", page)
        self.assertIn("3권 이상은 자동 진행", page)
        self.assertIn('id="volume-count"', page)
        self.assertIn("권수 계약", page)
        self.assertIn("권별 진행", page)
        self.assertIn("중단됨 · 재개 시", page)
        self.assertIn("마지막 실행 시간", page)
        self.assertIn("마음에 들 때까지 후보 5개를 다시", page)
        self.assertIn("완료되면 이 영역이 새 후보로 교체", page)
        self.assertIn("후보 생성 취소", page)
        self.assertIn("권 지정", page)
        self.assertIn("다음 권 이어서 만들기", page)
        self.assertIn('data-token="secret-token"', page)

    def test_generate_concepts_starts_background_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "STOP_AFTER_RUN").write_text("", encoding="utf-8")
            fake = FakePopen()
            controller = DashboardController(root, fake)

            job = controller.generate_concepts("탐험 중심", 4)

            command = fake.calls[0][0]
            self.assertEqual("concepts", job["kind"])
            self.assertIn("pipeline/generate_synopses.py", command)
            self.assertIn("--candidates-only", command)
            self.assertIn("--output", command)
            self.assertIn("--instruction-file", command)
            self.assertEqual(["--volume-count", "4"], command[-2:])
            self.assertTrue((root / "STOP_AFTER_RUN").exists())
            self.assertEqual("running", controller.status()["job"]["status"])

    def test_running_concept_generation_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakePopen()
            controller = DashboardController(Path(directory), fake)
            controller.generate_concepts("")

            job = controller.cancel_concepts()

            self.assertEqual("cancelled", job["status"])
            self.assertEqual("cancelled", controller.status()["job"]["status"])

    def test_finished_external_process_unlocks_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DashboardController(root, FakePopen())
            controller.state_path.parent.mkdir(parents=True, exist_ok=True)
            controller.state_path.write_text(
                json.dumps(
                    {
                        "kind": "concepts",
                        "status": "running",
                        "pid": 99999,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("pipeline.dashboard.process_alive", return_value=False):
                job = controller.status()["job"]

            self.assertEqual("failed", job["status"])

    def test_completed_detached_series_is_reported_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DashboardController(root, FakePopen())
            controller.state_path.parent.mkdir(parents=True, exist_ok=True)
            controller.state_path.write_text(
                json.dumps(
                    {
                        "kind": "series",
                        "status": "running",
                        "pid": 99999,
                        "started_at": "2026-06-22T01:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            active_path = root / "runs" / "new-world" / "active.json"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "completed_at": "2026-06-22T02:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("pipeline.dashboard.process_alive", return_value=False):
                job = controller.status()["job"]

            self.assertEqual("complete", job["status"])

    def test_newer_stopped_completion_supersedes_stale_failed_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DashboardController(root, FakePopen())
            controller.state_path.parent.mkdir(parents=True, exist_ok=True)
            controller.state_path.write_text(
                json.dumps(
                    {
                        "kind": "series",
                        "status": "failed",
                        "started_at": "2026-06-22T01:00:00+00:00",
                        "finished_at": "2026-06-22T02:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completion = root / "runs" / "complete-series" / "status.json"
            completion.parent.mkdir(parents=True)
            completion.write_text(
                json.dumps(
                    {
                        "stage": "stopped",
                        "updated_at": "2026-06-23T01:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            job = controller.status()["job"]

            self.assertEqual("stopped", job["status"])
            self.assertEqual("stopped", job["superseded_by_stage"])
            self.assertNotIn("return_code", job)
            self.assertEqual(
                "2026-06-23T01:00:00+00:00",
                job["finished_at"],
            )

    def test_dashboard_token_survives_controller_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            first = DashboardController(root, FakePopen())
            second = DashboardController(root, FakePopen())

            self.assertEqual(first.token, second.token)

    def test_status_reports_exact_scene_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "story" / "volumes").mkdir(parents=True)
            (root / "story" / "events").mkdir(parents=True)
            (root / "prose" / "scenes" / "V1-E01-S01").mkdir(parents=True)
            (root / "story" / "series.json").write_text(
                json.dumps(
                    {
                        "title": "진행 시험",
                        "volume_ids": ["V1"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "story" / "volumes" / "V1.json").write_text(
                json.dumps({"event_ids": ["V1-E01"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "story" / "events" / "V1-E01.json").write_text(
                json.dumps(
                    {"scene_ids": ["V1-E01-S01", "V1-E01-S02"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            scene = root / "prose" / "scenes" / "V1-E01-S01"
            (scene / "prose.md").write_text("승인 산문", encoding="utf-8")
            (scene / "review.json").write_text(
                json.dumps({"status": "pass"}, ensure_ascii=False),
                encoding="utf-8",
            )
            status_path = root / "runs" / "complete-series" / "status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps(
                    {
                        "stage": "prose",
                        "scene_id": "V1-E01-S02",
                        "attempt": 2,
                        "generated_this_run": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            progress = DashboardController(root, FakePopen()).status()["progress"]

            self.assertEqual("진행 시험", progress["target_title"])
            self.assertEqual(2, progress["total_scenes"])
            self.assertEqual(1, progress["approved_scenes"])
            self.assertEqual(2, progress["current_scene_number"])
            self.assertEqual("V1-E01-S02", progress["current_scene_id"])
            self.assertEqual(2, progress["attempt"])

    def test_progress_reports_recommended_and_approved_volume_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            concept = root / "runs" / "new-world" / "concept"
            concept.mkdir(parents=True)
            (concept / "selected-synopsis.json").write_text(
                json.dumps(
                    {
                        "title": "권수 시험",
                        "recommended_volume_count": 4,
                        "approved_volume_count": 4,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (concept / "concept-selection.json").write_text(
                json.dumps({"volume_approval": "auto"}, ensure_ascii=False),
                encoding="utf-8",
            )

            progress = DashboardController(root, FakePopen()).status()["progress"]

            self.assertEqual(4, progress["recommended_volume_count"])
            self.assertEqual(4, progress["approved_volume_count"])

    def test_progress_supports_legacy_five_volume_arc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            concept = root / "runs" / "new-world" / "concept"
            concept.mkdir(parents=True)
            (concept / "selected-synopsis.json").write_text(
                json.dumps(
                    {
                        "title": "기존 기획",
                        "five_volume_arc": ["1권", "2권", "3권", "4권", "5권"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            progress = DashboardController(root, FakePopen()).status()["progress"]

            self.assertEqual(5, progress["recommended_volume_count"])

    def test_stopped_progress_keeps_prose_phase_percentage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "story" / "volumes").mkdir(parents=True)
            (root / "story" / "events").mkdir(parents=True)
            (root / "prose" / "scenes" / "V1-E01-S01").mkdir(parents=True)
            (root / "story" / "series.json").write_text(
                json.dumps(
                    {"title": "중단 시험", "volume_ids": ["V1"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "story" / "volumes" / "V1.json").write_text(
                json.dumps({"event_ids": ["V1-E01"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "story" / "events" / "V1-E01.json").write_text(
                json.dumps(
                    {"scene_ids": ["V1-E01-S01", "V1-E01-S02"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            scene = root / "prose" / "scenes" / "V1-E01-S01"
            (scene / "prose.md").write_text("승인 산문", encoding="utf-8")
            (scene / "review.json").write_text(
                json.dumps({"status": "pass"}, ensure_ascii=False),
                encoding="utf-8",
            )
            status_path = root / "runs" / "complete-series" / "status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps(
                    {"stage": "stopped", "scene_id": "V1-E01-S02"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            progress = DashboardController(root, FakePopen()).status()["progress"]

            self.assertEqual(5, progress["phase"])
            self.assertEqual(65.0, progress["percent"])

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

            controller.start_series("S4", 4)

            command = fake.calls[0][0]
            self.assertIn("pipeline/complete_series.py", command)
            self.assertIn("--reuse-concept", command)
            self.assertIn("S4", command)
            self.assertIn("--approve-short", command)
            self.assertIn("--replace-active", command)
            self.assertEqual(["--volume-count", "4"], command[-2:])

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

    def test_active_world_allows_new_concept_generation_with_replacement(self) -> None:
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

            controller.generate_concepts("")

            self.assertIn("--candidates-only", fake.calls[0][0])


if __name__ == "__main__":
    unittest.main()
