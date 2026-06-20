# 권별 EPUB의 필수 구조와 정본 장면 순서를 검증하는 테스트
import io
import unittest
import zipfile

from pipeline.export_epub import epub_bytes


class ExportEpubTests(unittest.TestCase):
    def sample_volume(self) -> dict:
        return {
            "series_id": "SERIES",
            "series_title": "시험 시리즈",
            "id": "V1",
            "title": "첫 권",
            "events": [
                {
                    "id": "V1-E01",
                    "scenes": [
                        {"id": "V1-E01-S01", "prose": "첫 장면."},
                        {"id": "V1-E01-S02", "prose": "둘째 장면."},
                    ],
                }
            ],
        }

    def test_epub_has_required_container_and_uncompressed_mimetype(self) -> None:
        with zipfile.ZipFile(io.BytesIO(epub_bytes(self.sample_volume()))) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertEqual(
                archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED
            )
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIn("META-INF/container.xml", archive.namelist())
            self.assertIn("OEBPS/content.opf", archive.namelist())
            self.assertIn("OEBPS/nav.xhtml", archive.namelist())

    def test_epub_preserves_scene_order(self) -> None:
        with zipfile.ZipFile(io.BytesIO(epub_bytes(self.sample_volume()))) as archive:
            chapter = archive.read("OEBPS/event-1.xhtml").decode("utf-8")

        self.assertLess(chapter.index("첫 장면."), chapter.index("둘째 장면."))
        self.assertIn("장면 1", chapter)
        self.assertIn("장면 2", chapter)


if __name__ == "__main__":
    unittest.main()
