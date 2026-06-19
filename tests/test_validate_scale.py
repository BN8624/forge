# 권별 목표 글자 수 합계로 장편 규모 계약을 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.validate_scale import validate_story_scale
from tests.test_validate_structure import build_project, write_json


class ValidateScaleTests(unittest.TestCase):
    def test_small_volume_structure_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)

            errors = validate_story_scale(root)

            self.assertEqual(5, len(errors))
            self.assertTrue(all("장편 분량 부족" in error for error in errors))

    def test_volume_at_minimum_target_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            for path in (root / "story" / "scenes").glob("*.json"):
                scene = json.loads(path.read_text(encoding="utf-8"))
                scene["target_chars"] = 80_000
                write_json(path, scene)

            self.assertEqual([], validate_story_scale(root))


if __name__ == "__main__":
    unittest.main()
