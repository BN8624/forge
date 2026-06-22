# 모델 응답을 검증된 구조 후보 디렉터리로 게시하는 생성기 테스트
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.generate_candidate import (
    CandidateGenerationError,
    generate_candidate,
    load_source_material,
    normalize_owned_element_references,
    validate_source_identity,
)
from pipeline.validate_structure import validate_project
from tests.test_validate_structure import build_project


class FakeLLM:
    def __init__(self, response: str | list[str]):
        self.responses = response if isinstance(response, list) else [response]
        self.calls: list[tuple[str, str, float | None]] = []

    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str:
        self.calls.append((role, prompt, temperature))
        if not self.responses:
            raise AssertionError("준비된 모델 응답이 없음")
        return self.responses.pop(0)


def project_bundle(root: Path) -> dict:
    story = root / "story"
    return {
        "series": json.loads((story / "series.json").read_text(encoding="utf-8")),
        "volumes": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "volumes").glob("*.json"))
        ],
        "events": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "events").glob("*.json"))
        ],
        "scenes": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((story / "scenes").glob("*.json"))
        ],
    }


class GenerateCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_directory = tempfile.TemporaryDirectory()
        self.source_patcher = patch(
            "pipeline.generate_candidate.CURRENT_REFERENCE_ROOT",
            Path(self.source_directory.name) / "missing-current",
        )
        self.source_patcher.start()

    def tearDown(self) -> None:
        self.source_patcher.stop()
        self.source_directory.cleanup()

    def test_owned_elements_are_limited_to_declared_series_elements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            bundle = project_bundle(root)
            scene = bundle["scenes"][0]
            scene["owns"] = {
                "changes": ["C1_VOID_VESSEL"],
                "setups": ["CHG-1"],
                "payoffs": ["UNKNOWN"],
            }

            normalize_owned_element_references(bundle)

            self.assertEqual(
                {
                    "changes": ["CHG-1"],
                    "setups": [],
                    "payoffs": [],
                },
                scene["owns"],
            )

    def test_current_generated_world_precedes_legacy_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "current"
            current.mkdir()
            (current / "canon_bible.json").write_text(
                json.dumps(
                    {
                        "title": "새 세계",
                        "premise": "새 전제",
                        "canon": [{"id": "C1", "text": "새 정본"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (current / "compressed_manuscript.md").write_text(
                "새 참고 원고",
                encoding="utf-8",
            )

            with patch(
                "pipeline.generate_candidate.CURRENT_REFERENCE_ROOT",
                current,
            ):
                canon, manuscript = load_source_material()

            self.assertEqual("새 세계", canon["title"])
            self.assertEqual("새 참고 원고", manuscript)

    def test_generated_world_title_and_premise_are_exact_contracts(self) -> None:
        bundle = {
            "series": {
                "title": "다른 제목",
                "premise": "다른 전제",
            }
        }
        canon = {
            "title": "원천 제목",
            "premise": "원천 전제",
        }

        errors = validate_source_identity(bundle, canon)

        self.assertEqual(2, len(errors))
        self.assertTrue(any("title" in error for error in errors))
        self.assertTrue(any("premise" in error for error in errors))

    def test_valid_response_creates_valid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            llm = FakeLLM(json.dumps(project_bundle(fixture), ensure_ascii=False))

            generate_candidate("다섯 권으로 완결되는 성장 서사", output, llm)

            self.assertEqual([], validate_project(output))
            self.assertEqual(1, len(llm.calls))
            self.assertEqual("generator", llm.calls[0][0])
            self.assertIn("다섯 권으로 완결되는 성장 서사", llm.calls[0][1])
            self.assertIn('"id": "C1"', llm.calls[0][1])
            self.assertIn("카엘은 왼쪽 팔이 없다.", llm.calls[0][1])
            self.assertIn("# 에테르노의 그림자", llm.calls[0][1])

    def test_empty_instruction_still_uses_existing_world(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            llm = FakeLLM(json.dumps(project_bundle(fixture), ensure_ascii=False))

            generate_candidate("", output, llm)

            prompt = llm.calls[0][1]
            self.assertIn('"id": "C21"', prompt)
            self.assertIn("기존 압축 원고", prompt)
            self.assertIn("추가 지시 없음", prompt)
            self.assertIn("각 정본 항목은 최소 한 장면", prompt)
            self.assertIn("조건, 독점 주체, 부작용", prompt)

    def test_invalid_json_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate"
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")

            with self.assertRaises(CandidateGenerationError):
                generate_candidate(
                    "기획",
                    output,
                    FakeLLM(["JSON이 아님"] * 3),
                )

            self.assertEqual("기존 후보", sentinel.read_text(encoding="utf-8"))

    def test_invalid_structure_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture, duplicate_owner=True)
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")
            response = json.dumps(project_bundle(fixture), ensure_ascii=False)
            llm = FakeLLM([response] * 3)

            with self.assertRaises(CandidateGenerationError):
                generate_candidate("기획", output, llm)

            self.assertEqual("기존 후보", sentinel.read_text(encoding="utf-8"))

    def test_duplicate_document_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            bundle = project_bundle(fixture)
            bundle["volumes"].append(bundle["volumes"][0])
            response = json.dumps(bundle, ensure_ascii=False)
            llm = FakeLLM([response] * 3)

            with self.assertRaisesRegex(CandidateGenerationError, "중복"):
                generate_candidate("기획", output, llm)

            self.assertFalse(output.exists())

    def test_publish_failure_restores_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")
            llm = FakeLLM(json.dumps(project_bundle(fixture), ensure_ascii=False))
            real_replace = os.replace
            call_count = 0

            def fail_second_replace(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("게시 실패")
                real_replace(source, destination)

            with patch(
                "pipeline.generate_candidate.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaises(CandidateGenerationError):
                    generate_candidate("기획", output, llm)

            self.assertEqual("기존 후보", sentinel.read_text(encoding="utf-8"))

    def test_validation_errors_are_sent_back_for_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            invalid = workspace / "invalid"
            valid = workspace / "valid"
            output = workspace / "candidate"
            build_project(invalid, duplicate_owner=True)
            build_project(valid)
            llm = FakeLLM(
                [
                    json.dumps(project_bundle(invalid), ensure_ascii=False),
                    json.dumps(project_bundle(valid), ensure_ascii=False),
                ]
            )

            generate_candidate("추가 지시 없음", output, llm)

            self.assertEqual([], validate_project(output))
            self.assertEqual(2, len(llm.calls))
            self.assertIn("구조 검증 오류", llm.calls[1][1])
            self.assertIn("CHG-1", llm.calls[1][1])

    def test_retry_exhaustion_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            invalid = workspace / "invalid"
            output = workspace / "candidate"
            build_project(invalid, duplicate_owner=True)
            response = json.dumps(project_bundle(invalid), ensure_ascii=False)
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")

            with self.assertRaisesRegex(CandidateGenerationError, "3회"):
                generate_candidate(
                    "",
                    output,
                    FakeLLM([response, response, response]),
                )

            self.assertEqual("기존 후보", sentinel.read_text(encoding="utf-8"))

    def test_state_continuity_is_normalized_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            bundle = project_bundle(fixture)
            for volume in bundle["volumes"]:
                volume["start_state"] = {"wrong": volume["id"]}
                volume["end_state"] = {"wrong": volume["id"]}
            for event in bundle["events"]:
                event["start_state"] = {"wrong": event["id"]}
                event["end_state"] = {"wrong": event["id"]}
            for scene in bundle["scenes"][1:]:
                scene["start_state"] = {"wrong": scene["id"]}
            llm = FakeLLM(json.dumps(bundle, ensure_ascii=False))

            generate_candidate("", output, llm)

            self.assertEqual([], validate_project(output))
            self.assertEqual(1, len(llm.calls))

    def test_late_setup_owner_is_moved_before_payoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            fixture = workspace / "fixture"
            output = workspace / "candidate"
            build_project(fixture)
            bundle = project_bundle(fixture)
            bundle["series"]["elements"].extend(
                [
                    {"id": "SET-1", "kind": "setup", "description": "복선"},
                    {
                        "id": "PAY-1",
                        "kind": "payoff",
                        "description": "회수",
                        "resolves": "SET-1",
                    },
                ]
            )
            bundle["scenes"][1]["owns"]["payoffs"] = ["PAY-1"]
            bundle["scenes"][1]["consumes_setups"] = ["SET-1"]
            bundle["scenes"][4]["owns"]["setups"] = ["SET-1"]
            llm = FakeLLM(json.dumps(bundle, ensure_ascii=False))

            generate_candidate("", output, llm)

            self.assertEqual([], validate_project(output))
            first_scene = json.loads(
                (
                    output
                    / "story"
                    / "scenes"
                    / f"{bundle['scenes'][0]['id']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(["SET-1"], first_scene["owns"]["setups"])


if __name__ == "__main__":
    unittest.main()
