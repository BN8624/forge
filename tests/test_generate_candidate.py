# 모델 응답을 검증된 구조 후보 디렉터리로 게시하는 생성기 테스트
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.generate_candidate import CandidateGenerationError, generate_candidate
from pipeline.validate_structure import validate_project
from tests.test_validate_structure import build_project


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str, float | None]] = []

    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str:
        self.calls.append((role, prompt, temperature))
        return self.response


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

    def test_invalid_json_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate"
            output.mkdir()
            sentinel = output / "preserved.txt"
            sentinel.write_text("기존 후보", encoding="utf-8")

            with self.assertRaises(CandidateGenerationError):
                generate_candidate("기획", output, FakeLLM("JSON이 아님"))

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
            llm = FakeLLM(json.dumps(project_bundle(fixture), ensure_ascii=False))

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
            llm = FakeLLM(json.dumps(bundle, ensure_ascii=False))

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


if __name__ == "__main__":
    unittest.main()
