# 승인된 권별 산문을 모바일 읽기 페이지로 제공하는 읽기 전용 서버
import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_volume(root: Path, volume_id: str) -> dict:
    series = read_json(root / "story" / "series.json")
    volume = read_json(root / "story" / "volumes" / f"{volume_id}.json")
    events = []

    for event_id in volume["event_ids"]:
        event = read_json(root / "story" / "events" / f"{event_id}.json")
        scenes = []
        for scene_id in event["scene_ids"]:
            prose_path = root / "prose" / "scenes" / scene_id / "prose.md"
            if not prose_path.exists():
                raise FileNotFoundError(f"승인된 산문이 없습니다: {scene_id}")
            scenes.append(
                {
                    "id": scene_id,
                    "prose": prose_path.read_text(encoding="utf-8").strip(),
                }
            )
        events.append({"id": event_id, "scenes": scenes})

    return {
        "series_id": series["id"],
        "series_title": series["title"],
        "id": volume["id"],
        "title": volume["title"],
        "events": events,
    }


def prose_html(prose: str) -> str:
    paragraphs = [part.strip() for part in prose.split("\n\n") if part.strip()]
    return "".join(f"<p>{html.escape(part)}</p>" for part in paragraphs)


def render_volume(volume: dict, epub_filename: str | None = None) -> bytes:
    toc = []
    content = []
    scene_number = 0

    for event_number, event in enumerate(volume["events"], start=1):
        toc.append(
            f'<li><a href="#{html.escape(event["id"])}">'
            f"{event_number}부</a></li>"
        )
        content.append(
            f'<section class="event" id="{html.escape(event["id"])}">'
            f"<h2>{event_number}부</h2>"
            "</section>"
        )
        for scene in event["scenes"]:
            scene_number += 1
            content.append(
                f'<article class="scene" id="{html.escape(scene["id"])}">'
                f'<p class="scene-number">장면 {scene_number}</p>'
                f'{prose_html(scene["prose"])}'
                "</article>"
            )

    title = f'{volume["series_title"]} · {volume["id"]} {volume["title"]}'
    download = (
        f'<p><a class="download" href="/{html.escape(epub_filename)}" download>'
        "EPUB 내려받기</a></p>"
        if epub_filename
        else ""
    )
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{html.escape(title)}</title>
<style>
:root {{
  color-scheme: light dark;
  --paper: #f7f2e8;
  --ink: #28241e;
  --muted: #756c60;
  --line: #d8cdbd;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-serif, "Noto Serif KR", "Apple SD Gothic Neo", serif;
  line-height: 1.9;
  word-break: keep-all;
}}
main {{
  width: min(100% - 40px, 720px);
  margin: 0 auto;
  padding: max(48px, env(safe-area-inset-top)) 0 96px;
}}
h1 {{ margin: 0 0 8px; font-size: clamp(2rem, 8vw, 3.25rem); line-height: 1.2; }}
.subtitle, .scene-number {{ color: var(--muted); }}
details {{
  margin: 40px 0 72px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
}}
summary {{ cursor: pointer; font-weight: 700; }}
ol {{ padding-left: 24px; }}
a {{ color: inherit; text-underline-offset: 4px; }}
.download {{
  display: inline-block;
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  text-decoration: none;
}}
.event {{ margin: 84px 0 40px; padding-top: 20px; border-top: 1px solid var(--line); }}
.event h2 {{ margin-bottom: 4px; font-size: 1.75rem; }}
.scene {{ margin: 0 0 72px; scroll-margin-top: 24px; }}
.scene-number {{ margin: 0 0 20px; font-size: .82rem; letter-spacing: .12em; }}
.scene p:not(.scene-number) {{ margin: 0 0 1.15em; font-size: clamp(1.08rem, 4.7vw, 1.2rem); }}
@media (prefers-color-scheme: dark) {{
  :root {{ --paper: #181714; --ink: #e9e2d5; --muted: #aaa094; --line: #403b34; }}
}}
</style>
</head>
<body>
<main>
  <header>
    <p class="subtitle">{html.escape(volume["id"])}</p>
    <h1>{html.escape(volume["title"])}</h1>
    <p class="subtitle">{html.escape(volume["series_title"])}</p>
    {download}
  </header>
  <details>
    <summary>목차</summary>
    <ol>{"".join(toc)}</ol>
  </details>
  {"".join(content)}
</main>
</body>
</html>
"""
    return page.encode("utf-8")


def make_handler(page: bytes, epub: bytes | None = None, epub_filename: str = "book.epub"):
    class ProseHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                body, content_type, status = b"ok", "text/plain; charset=utf-8", 200
            elif path == "/":
                body, content_type, status = page, "text/html; charset=utf-8", 200
            elif epub is not None and path == f"/{epub_filename}":
                body, content_type, status = epub, "application/epub+zip", 200
            else:
                body, content_type, status = b"not found", "text/plain; charset=utf-8", 404

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if content_type == "application/epub+zip":
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{epub_filename}"'
                )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return ProseHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="승인된 권별 산문 읽기 서버")
    parser.add_argument("--volume", default="V1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    from pipeline.export_epub import epub_bytes

    volume = load_volume(ROOT, args.volume)
    epub_filename = f"{args.volume}.epub"
    epub = epub_bytes(volume)
    page = render_volume(volume, epub_filename)
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(page, epub, epub_filename)
    )
    print(f"http://{args.host}:{args.port} 에서 {args.volume} 산문을 제공합니다.")
    server.serve_forever()


if __name__ == "__main__":
    main()
