# 권별 산문 읽기 페이지의 순서와 HTTP 응답을 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.serve_prose import load_volume, make_handler, render_volume


class ServeProseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "story" / "volumes").mkdir(parents=True)
        (self.root / "story" / "events").mkdir()
        (self.root / "prose" / "scenes" / "V1-E01-S01").mkdir(parents=True)
        (self.root / "prose" / "scenes" / "V1-E01-S02").mkdir(parents=True)
        self.write_json(
            "story/series.json",
            {"id": "SERIES", "title": "시험 시리즈", "volume_ids": ["V1"]},
        )
        self.write_json(
            "story/volumes/V1.json",
            {"id": "V1", "title": "첫 권", "event_ids": ["V1-E01"]},
        )
        self.write_json(
            "story/events/V1-E01.json",
            {
                "id": "V1-E01",
                "objective": "도시에 진입한다.",
                "scene_ids": ["V1-E01-S01", "V1-E01-S02"],
            },
        )
        (self.root / "prose" / "scenes" / "V1-E01-S01" / "prose.md").write_text(
            "첫 장면 첫 문단.\n\n첫 장면 둘째 문단.", encoding="utf-8"
        )
        (self.root / "prose" / "scenes" / "V1-E01-S02" / "prose.md").write_text(
            "둘째 장면.", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_json(self, relative_path: str, value: dict) -> None:
        (self.root / relative_path).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    def test_render_volume_uses_canonical_scene_order(self) -> None:
        page = render_volume(load_volume(self.root, "V1")).decode("utf-8")

        self.assertIn("시험 시리즈", page)
        self.assertNotIn("도시에 진입한다.", page)
        self.assertLess(page.index("첫 장면 첫 문단."), page.index("둘째 장면."))
        self.assertIn("<p>첫 장면 둘째 문단.</p>", page)
        self.assertIn('name="viewport"', page)

    def test_missing_prose_is_rejected(self) -> None:
        (self.root / "prose" / "scenes" / "V1-E01-S02" / "prose.md").unlink()

        with self.assertRaisesRegex(FileNotFoundError, "V1-E01-S02"):
            load_volume(self.root, "V1")

    def test_handler_factory_returns_request_handler(self) -> None:
        handler = make_handler(b"<html></html>")

        self.assertTrue(hasattr(handler, "do_GET"))


if __name__ == "__main__":
    unittest.main()
