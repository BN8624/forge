# 정본 구조가 권당 장편 분량 계약을 충족하는지 검증하는 도구
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


MIN_VOLUME_TARGET_CHARS = 80_000


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_story_scale(
    root: Path,
    minimum_volume_chars: int = MIN_VOLUME_TARGET_CHARS,
) -> list[str]:
    story = root / "story"
    series = read_json(story / "series.json")
    errors: list[str] = []
    for volume_id in series["volume_ids"]:
        volume = read_json(story / "volumes" / f"{volume_id}.json")
        total_chars = 0
        scene_count = 0
        for event_id in volume["event_ids"]:
            event = read_json(story / "events" / f"{event_id}.json")
            for scene_id in event["scene_ids"]:
                scene = read_json(story / "scenes" / f"{scene_id}.json")
                total_chars += scene["target_chars"]
                scene_count += 1
        if total_chars < minimum_volume_chars:
            errors.append(
                f"권 장편 분량 부족: {volume_id} "
                f"{total_chars}자/{minimum_volume_chars}자, {scene_count}장면"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forge 정본 구조의 권별 장편 목표 분량을 검증한다."
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = validate_story_scale(args.root.resolve())
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        print(f"[FAIL] {len(errors)}개 권이 장편 규모 미달")
        return 1
    print("[OK] Forge 권별 장편 규모 검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
