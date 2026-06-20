# 권별 목표 글자 수 합계로 장편 규모 계약을 검증하는 테스트
import tempfile
import unittest
from pathlib import Path

from pipeline.validate_scale import validate_story_scale
from tests.test_validate_structure import build_project


class ValidateScaleTests(unittest.TestCase):
    def test_small_volume_structure_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)

            errors = validate_story_scale(root)

            self.assertEqual(10, len(errors))
            self.assertEqual(
                5,
                sum("장편 분량 부족" in error for error in errors),
            )
            self.assertEqual(
                5,
                sum("장면 수 부족" in error for error in errors),
            )

    def test_custom_small_fixture_can_pass_explicit_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)

            self.assertEqual(
                [],
                validate_story_scale(
                    root,
                    minimum_volume_chars=2_000,
                    minimum_volume_scenes=1,
                ),
            )


if __name__ == "__main__":
    unittest.main()
