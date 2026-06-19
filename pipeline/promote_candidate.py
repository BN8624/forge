# 독립 검증을 통과한 후보 구조를 정본 story 디렉터리로 승격하는 도구
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.validate_structure import validate_project


class CandidateValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


class PromotionError(Exception):
    pass


def recover_incomplete_promotions(root: Path) -> None:
    canonical_story = root / "story"
    for staging_root in sorted(root.glob(".promotion-*")):
        if not staging_root.is_dir():
            continue
        backup_story = staging_root / "story.previous"
        if backup_story.exists() and not canonical_story.exists():
            try:
                os.replace(backup_story, canonical_story)
            except OSError as exc:
                raise PromotionError(
                    f"중단된 승격의 정본 복구 실패: {backup_story}"
                ) from exc
        shutil.rmtree(staging_root, ignore_errors=True)


def promote_candidate(root: Path, candidate: Path) -> None:
    root = root.resolve()
    candidate = candidate.resolve()
    canonical_story = root / "story"
    candidate_story = candidate / "story"

    root.mkdir(parents=True, exist_ok=True)
    recover_incomplete_promotions(root)

    if not candidate_story.is_dir():
        raise CandidateValidationError([f"후보 story 디렉터리 없음: {candidate_story}"])
    if canonical_story.exists() and not canonical_story.is_dir():
        raise PromotionError(f"정본 story 경로가 디렉터리가 아님: {canonical_story}")
    if candidate_story == canonical_story:
        raise PromotionError("현재 정본 story를 승격 후보로 사용할 수 없음")

    staging_root = Path(tempfile.mkdtemp(prefix=".promotion-", dir=root))
    staged_story = staging_root / "story"
    backup_story = staging_root / "story.previous"
    preserve_staging = False

    try:
        shutil.copytree(candidate_story, staged_story)
        errors = validate_project(staging_root)
        if errors:
            raise CandidateValidationError(errors)

        had_canonical = canonical_story.exists()
        if had_canonical:
            os.replace(canonical_story, backup_story)

        try:
            os.replace(staged_story, canonical_story)
        except OSError as promotion_error:
            if had_canonical:
                try:
                    os.replace(backup_story, canonical_story)
                except OSError as rollback_error:
                    preserve_staging = True
                    raise PromotionError(
                        "정본 교체와 복구가 모두 실패함. "
                        f"기존 정본 백업을 보존함: {backup_story}"
                    ) from rollback_error
            raise PromotionError(f"정본 교체 실패: {promotion_error}") from promotion_error
    finally:
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="독립 검증을 통과한 후보 story를 정본으로 승격한다."
    )
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    try:
        promote_candidate(args.root, args.candidate)
    except CandidateValidationError as exc:
        for error in exc.errors:
            print(f"[ERROR] {error}")
        print(f"[FAIL] 후보 검증 실패. {len(exc.errors)}개 오류")
        return 1
    except PromotionError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print("[OK] 후보 story 정본 승격 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
