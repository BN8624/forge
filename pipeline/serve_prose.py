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


def render_library(volumes: list[dict]) -> bytes:
    cards = []
    for volume in volumes:
        scene_count = sum(len(event["scenes"]) for event in volume["events"])
        cards.append(
            '<article class="book">'
            f'<p class="volume">{html.escape(volume["id"])}</p>'
            f'<h2>{html.escape(volume["title"])}</h2>'
            f"<p>{scene_count}개 장면</p>"
            f'<p><a href="/{html.escape(volume["id"])}/">읽기</a>'
            f' <a href="/{html.escape(volume["id"])}.epub" download>EPUB</a></p>'
            "</article>"
        )
    series_title = volumes[0]["series_title"]
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{html.escape(series_title)} 전권</title>
<style>
:root {{ color-scheme: light dark; --paper:#f7f2e8; --ink:#28241e; --line:#d8cdbd; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:ui-serif,"Noto Serif KR","Apple SD Gothic Neo",serif; }}
main {{ width:min(100% - 40px,720px); margin:0 auto; padding:max(48px,env(safe-area-inset-top)) 0 80px; }}
h1 {{ font-size:clamp(2rem,8vw,3.2rem); margin:0 0 40px; }}
.book {{ border-top:1px solid var(--line); padding:24px 0; }}
.book h2 {{ margin:4px 0; font-size:1.55rem; }}
.volume {{ opacity:.65; margin:0; }}
a {{ display:inline-block; margin-right:8px; color:inherit; text-underline-offset:4px; }}
@media (prefers-color-scheme:dark) {{ :root {{--paper:#181714;--ink:#e9e2d5;--line:#403b34;}} }}
</style>
</head>
<body><main><p><a href="/dashboard">새 작품 대시보드</a></p><h1>{html.escape(series_title)}</h1>{"".join(cards)}</main></body>
</html>"""
    return page.encode("utf-8")


def load_library_payload(root: Path) -> tuple[bytes, dict[str, bytes], dict[str, bytes]]:
    from pipeline.export_epub import epub_bytes

    series = read_json(root / "story" / "series.json")
    volumes = [load_volume(root, volume_id) for volume_id in series["volume_ids"]]
    pages = {
        volume["id"]: render_volume(volume, f'{volume["id"]}.epub')
        for volume in volumes
    }
    epubs = {volume["id"]: epub_bytes(volume) for volume in volumes}
    return render_library(volumes), pages, epubs


def render_library_pending(root: Path) -> bytes:
    series = read_json(root / "story" / "series.json")
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(series["title"])} 준비 중</title>
<style>
body{{margin:0;background:#181714;color:#e9e2d5;font-family:-apple-system,"Apple SD Gothic Neo",sans-serif}}
main{{width:min(100% - 40px,720px);margin:auto;padding:max(48px,env(safe-area-inset-top)) 0 80px}}
a{{color:#f1b24a}} h1{{line-height:1.15}} p{{line-height:1.7;color:#aaa094}}
</style>
</head>
<body><main><p><a href="/dashboard">진행 대시보드 열기</a></p>
<h1>{html.escape(series["title"])}</h1>
<p>Forge가 장면 산문을 생성하고 critic 검증하는 중입니다. 승인된 전권이 준비되면 이 서재가 자동으로 열립니다.</p>
</main></body></html>"""
    return page.encode("utf-8")


def make_library_handler(
    index_page: bytes,
    pages: dict[str, bytes],
    epubs: dict[str, bytes],
    root: Path | None = None,
    dashboard=None,
):
    class LibraryHandler(BaseHTTPRequestHandler):
        def send_body(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
            filename: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            if filename:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value: dict, status: int = 200) -> None:
            self.send_body(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def dynamic_payload(self) -> tuple[bytes, dict[str, bytes], dict[str, bytes]]:
            if root is None:
                return index_page, pages, epubs
            return load_library_payload(root)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            filename = None
            if path == "/health":
                self.send_body(b"ok", "text/plain; charset=utf-8")
                return
            if path == "/dashboard" and dashboard is not None:
                from pipeline.dashboard import render_dashboard

                self.send_body(
                    render_dashboard(dashboard.token),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/api/dashboard" and dashboard is not None:
                self.send_json(dashboard.status())
                return
            try:
                current_index, current_pages, current_epubs = self.dynamic_payload()
            except (OSError, KeyError, json.JSONDecodeError) as exc:
                if root is not None and path == "/":
                    self.send_body(
                        render_library_pending(root),
                        "text/html; charset=utf-8",
                    )
                    return
                self.send_json(
                    {"error": f"현재 서재를 읽는 중입니다: {exc}"},
                    503,
                )
                return
            if path == "/":
                body, content_type, status = current_index, "text/html; charset=utf-8", 200
            elif path.rstrip("/").lstrip("/") in current_pages:
                volume_id = path.rstrip("/").lstrip("/")
                body = current_pages[volume_id]
                content_type, status = "text/html; charset=utf-8", 200
            elif path.lstrip("/").removesuffix(".epub") in current_epubs:
                volume_id = path.lstrip("/").removesuffix(".epub")
                filename = f"{volume_id}.epub"
                body = current_epubs[volume_id]
                content_type, status = "application/epub+zip", 200
            else:
                body, content_type, status = b"not found", "text/plain; charset=utf-8", 404

            self.send_body(body, content_type, status, filename)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if dashboard is None or path not in (
                "/api/dashboard/concepts",
                "/api/dashboard/start",
                "/api/dashboard/resume",
            ):
                self.send_json({"error": "not found"}, 404)
                return
            if self.headers.get("X-Forge-Token") != dashboard.token:
                self.send_json({"error": "요청 토큰이 올바르지 않습니다."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 16_384:
                    raise ValueError("요청이 너무 큽니다.")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON 객체가 필요합니다.")
                if path == "/api/dashboard/concepts":
                    raw_volume_count = payload.get("volume_count")
                    volume_count = (
                        int(raw_volume_count)
                        if raw_volume_count not in (None, "")
                        else None
                    )
                    result = dashboard.generate_concepts(
                        str(payload.get("instruction", "")),
                        volume_count,
                    )
                elif path == "/api/dashboard/start":
                    raw_volume_count = payload.get("volume_count")
                    volume_count = (
                        int(raw_volume_count)
                        if raw_volume_count not in (None, "")
                        else None
                    )
                    result = dashboard.start_series(
                        str(payload.get("selected_id", "")),
                        volume_count,
                    )
                else:
                    result = dashboard.resume_series()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                from pipeline.dashboard import DashboardError

                if isinstance(exc, DashboardError):
                    self.send_json({"error": str(exc)}, 409)
                    return
                self.send_json({"error": str(exc)}, 500)
                return
            self.send_json({"job": result}, 202)

        def log_message(self, format: str, *args) -> None:
            return

    return LibraryHandler


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
    parser.add_argument("--volume", default="all")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.volume == "all":
        from pipeline.dashboard import DashboardController

        try:
            index_page, pages, epubs = load_library_payload(ROOT)
        except FileNotFoundError:
            index_page, pages, epubs = render_library_pending(ROOT), {}, {}
        handler = make_library_handler(
            index_page,
            pages,
            epubs,
            ROOT,
            DashboardController(ROOT),
        )
        label = "전권"
    else:
        from pipeline.export_epub import epub_bytes

        volume = load_volume(ROOT, args.volume)
        epub_filename = f"{args.volume}.epub"
        epub = epub_bytes(volume)
        page = render_volume(volume, epub_filename)
        handler = make_handler(page, epub, epub_filename)
        label = args.volume
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"http://{args.host}:{args.port} 에서 {label} 산문을 제공합니다.")
    server.serve_forever()


if __name__ == "__main__":
    main()
