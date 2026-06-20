# 승인된 권별 산문을 iPhone 도서 앱용 EPUB으로 내보내는 도구
import argparse
import html
import io
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.serve_prose import ROOT, load_volume, prose_html


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

BOOK_CSS = """body {
  font-family: serif;
  line-height: 1.8;
  word-break: keep-all;
  margin: 5%;
}
h1, h2 { line-height: 1.3; }
.title-page { text-align: center; margin-top: 35%; }
.scene { margin: 0 0 3em; }
.scene-number { color: #666; font-size: .8em; letter-spacing: .12em; }
p { margin: 0 0 1em; }
"""


def xhtml_page(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko" xml:lang="ko">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="book.css"/>
</head>
<body>{body}</body>
</html>
"""


def epub_bytes(volume: dict) -> bytes:
    title = f'{volume["series_title"]} {volume["id"]} {volume["title"]}'
    identifier = html.escape(f'urn:forge:{volume["series_id"]}:{volume["id"]}')
    escaped_title = html.escape(title)
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="book.css" media-type="text/css"/>',
    ]
    spine = ['<itemref idref="title"/>']
    nav_items = []
    chapters = []
    scene_number = 0

    for event_number, event in enumerate(volume["events"], start=1):
        filename = f"event-{event_number}.xhtml"
        chapter_id = f"event-{event_number}"
        manifest.append(
            f'<item id="{chapter_id}" href="{filename}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{chapter_id}"/>')
        nav_items.append(f'<li><a href="{filename}">{event_number}부</a></li>')
        scene_parts = [f"<h1>{event_number}부</h1>"]
        for scene in event["scenes"]:
            scene_number += 1
            scene_parts.append(
                f'<section class="scene" id="{scene["id"]}">'
                f'<p class="scene-number">장면 {scene_number}</p>'
                f'{prose_html(scene["prose"])}</section>'
            )
        chapters.append((filename, xhtml_page(f"{event_number}부", "".join(scene_parts))))

    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{identifier}</dc:identifier>
    <dc:title>{escaped_title}</dc:title>
    <dc:language>ko</dc:language>
    <meta property="dcterms:modified">2026-06-20T00:00:00Z</meta>
  </metadata>
  <manifest>{"".join(manifest)}</manifest>
  <spine>{"".join(spine)}</spine>
</package>
"""
    navigation = xhtml_page(
        "목차",
        '<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">'
        f'<h1>목차</h1><ol>{"".join(nav_items)}</ol></nav>',
    )
    title_page = xhtml_page(
        title,
        '<section class="title-page">'
        f'<p>{html.escape(volume["series_title"])}</p>'
        f'<h1>{html.escape(volume["title"])}</h1>'
        f'<p>{html.escape(volume["id"])}</p></section>',
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/nav.xhtml", navigation)
        archive.writestr("OEBPS/title.xhtml", title_page)
        archive.writestr("OEBPS/book.css", BOOK_CSS)
        for filename, chapter in chapters:
            archive.writestr(f"OEBPS/{filename}", chapter)
    return output.getvalue()


def export_epub(root: Path, volume_id: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(epub_bytes(load_volume(root, volume_id)))
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="승인된 권별 산문 EPUB 내보내기")
    parser.add_argument("--volume", default="V1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = args.output or ROOT / "exports" / f"{args.volume}.epub"
    export_epub(ROOT, args.volume, output)
    print(f"{output}에 {args.volume} EPUB을 생성했습니다.")


if __name__ == "__main__":
    main()
