# Forge의 장면별 산문 생성, critic 검증, 순차 승격을 검증하는 테스트
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.generate_prose import (
    ProseGenerationError,
    build_future_element_guard,
    contract_sha256,
    generate_all_prose,
    generate_prose_scene,
    validate_prose_style,
)
from tests.test_generate_candidate import FakeLLM
from tests.test_promote_candidate import approve_candidate
from tests.test_validate_structure import build_project


def approve_story(root: Path) -> None:
    approve_candidate(root)
    (root / "story" / "canon-review.json").write_bytes(
        (root / "canon-review.json").read_bytes()
    )
    (root / "canon-review.json").unlink()


def prose_response(scene_id: str, length: int = 1800) -> str:
    unit = (
        '차가운 바람이 감시 수정구 아래 거리를 훑었다. '
        '"멈춰. 지금 선택해야 해." 경비가 길을 막았다. '
        '"비켜. 이대로면 모두 늦어." 그는 손잡이를 당겼다. '
        '"그 선택의 대가는 알아?" 경비가 다시 물었다. '
        '"알아. 그래도 간다." 문이 열리며 경보가 울렸다. '
    )
    prose = (unit * 100)[:length]
    return json.dumps(
        {"scene_id": scene_id, "prose": prose},
        ensure_ascii=False,
    )


def review_response(scene_id: str, status: str = "pass") -> str:
    passed = status == "pass"
    return json.dumps(
        {
            "scene_id": scene_id,
            "status": status,
            "checks": {
                "objective": passed,
                "state_transition": passed,
                "owned_elements": passed,
                "reveal_order": passed,
                "canon": passed,
                "continuity": passed,
                "prose_quality": passed,
            },
            "issues": [] if passed else ["장면 목표가 충분히 드러나지 않음"],
            "reason": "모든 계약 충족" if passed else "장면 목표 미달",
        },
        ensure_ascii=False,
    )


class GenerateProseTests(unittest.TestCase):
    def test_first_scene_is_generated_reviewed_and_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            prose_path = result / "prose.md"
            review_path = result / "review.json"
            prose = prose_path.read_text(encoding="utf-8")
            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertTrue(prose)
            self.assertEqual("pass", review["status"])
            self.assertEqual(
                hashlib.sha256(prose.encode("utf-8")).hexdigest(),
                review["prose_sha256"],
            )
            self.assertEqual(["generator", "critic"], [call[0] for call in llm.calls])

    def test_critic_failure_is_sent_back_to_generator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01", "fail"),
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertEqual(4, len(llm.calls))
            self.assertIn("장면 목표가 충분히", llm.calls[2][1])
            self.assertIn("직전 산문 후보:\n없음", llm.calls[2][1])

    def test_failure_feedback_survives_next_scene_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            first_llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01", "fail"),
                    prose_response("V1-E01-S01", 50),
                    prose_response("V1-E01-S01", 50),
                ]
            )

            with self.assertRaisesRegex(ProseGenerationError, "3회"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    first_llm,
                    check_scale=False,
                )

            second_llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )
            generate_prose_scene(
                root,
                "V1-E01-S01",
                second_llm,
                check_scale=False,
            )

            self.assertIn("장면 목표가 충분히", second_llm.calls[0][1])
            self.assertIn("최소 2100자를 목표", second_llm.calls[0][1])
            self.assertNotIn("산문 길이 범위 위반", second_llm.calls[0][1])

    def test_persistent_identical_candidates_stop_before_new_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            scene = json.loads(
                (
                    root
                    / "story"
                    / "scenes"
                    / "V1-E01-S01.json"
                ).read_text(encoding="utf-8")
            )
            work = (
                root
                / "runs"
                / "prose-work"
                / "V1-E01-S01"
                / contract_sha256(scene)
            )
            work.mkdir(parents=True)
            repeated = prose_response("V1-E01-S01", 1200)
            for index in range(1, 4):
                (work / f"generator-attempt-{index}.txt").write_text(
                    repeated,
                    encoding="utf-8",
                )
            llm = FakeLLM([])

            with self.assertRaisesRegex(ProseGenerationError, "동일 산문 후보"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    llm,
                    check_scale=False,
                )

            self.assertEqual([], llm.calls)

    def test_second_scene_requires_previous_approved_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)

            with self.assertRaisesRegex(ProseGenerationError, "이전 정본 산문"):
                generate_prose_scene(
                    root,
                    "V2-E01-S01",
                    FakeLLM([]),
                    check_scale=False,
                )

    def test_existing_scene_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            target = root / "prose" / "scenes" / "V1-E01-S01"
            target.mkdir(parents=True)
            (target / "prose.md").write_text("기존 산문", encoding="utf-8")

            with self.assertRaisesRegex(ProseGenerationError, "불완전"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    FakeLLM([]),
                    check_scale=False,
                )

            self.assertEqual(
                "기존 산문",
                (target / "prose.md").read_text(encoding="utf-8"),
            )

    def test_empty_scene_directory_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            target = root / "prose" / "scenes" / "V1-E01-S01"
            target.mkdir(parents=True)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                None,
                llm,
                check_scale=False,
            )

            self.assertEqual("V1-E01-S01", result.name)

    def test_short_prose_is_rejected_before_critic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            short = prose_response("V1-E01-S01", 50)
            llm = FakeLLM(
                [
                    short,
                    json.dumps(
                        {
                            "scene_id": "V1-E01-S01",
                            "addition": "추가 산문",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "scene_id": "V1-E01-S01",
                            "addition": "추가 산문",
                        },
                        ensure_ascii=False,
                    ),
                ]
            )

            with self.assertRaisesRegex(ProseGenerationError, "3회"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    llm,
                    check_scale=False,
                )

            self.assertEqual(["generator"] * 3, [call[0] for call in llm.calls])
            self.assertIn(
                "전체 장면을 다시 구성",
                llm.calls[1][1],
            )

    def test_short_candidate_is_rewritten_as_complete_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            short_prose = ("차가운 바람이 거리를 훑었다. " * 90)[:1200]
            llm = FakeLLM(
                [
                    json.dumps(
                        {
                            "scene_id": "V1-E01-S01",
                            "prose": short_prose,
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "scene_id": "V1-E01-S01",
                            "prose": json.loads(
                                prose_response("V1-E01-S01")
                            )["prose"],
                        },
                        ensure_ascii=False,
                    ),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            prose = (result / "prose.md").read_text(encoding="utf-8")
            self.assertFalse(prose.startswith(short_prose.strip()))
            self.assertIn("전체 장면을 다시 구성", llm.calls[1][1])
            self.assertIn("최소 2100자를 목표", llm.calls[1][1])
            self.assertNotIn("허용 1300-3000자", llm.calls[1][1])
            self.assertNotIn("추가 산문만", llm.calls[1][1])
            self.assertIn("현재 미래 요소 충돌 특별 경계", llm.calls[1][1])
            self.assertIn(short_prose.strip(), llm.calls[1][1])

    def test_scene_id_single_key_response_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            prose = ("차가운 바람이 거리를 훑었다. " * 120)[:1800]
            llm = FakeLLM(
                [
                    json.dumps(
                        {"V1-E01-S01": prose},
                        ensure_ascii=False,
                    ),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertEqual(prose, (result / "prose.md").read_text(encoding="utf-8"))

    def test_nested_scene_id_prose_response_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            prose = ("차가운 바람이 거리를 훑었다. " * 120)[:1800]
            llm = FakeLLM(
                [
                    json.dumps(
                        {"V1-E01-S01": {"prose": prose}},
                        ensure_ascii=False,
                    ),
                    review_response("V1-E01-S01"),
                ]
            )

            result = generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertEqual(prose, (result / "prose.md").read_text(encoding="utf-8"))

    def test_saved_valid_generator_response_is_reviewed_without_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            scene = json.loads(
                (
                    root
                    / "story"
                    / "scenes"
                    / "V1-E01-S01.json"
                ).read_text(encoding="utf-8")
            )
            work = (
                root
                / "runs"
                / "prose-work"
                / "V1-E01-S01"
                / contract_sha256(scene)
            )
            work.mkdir(parents=True)
            (work / "generator-attempt-2.txt").write_text(
                prose_response("V1-E01-S01"),
                encoding="utf-8",
            )
            llm = FakeLLM([review_response("V1-E01-S01")])

            generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertEqual(["critic"], [call[0] for call in llm.calls])

    def test_prompt_distinguishes_available_fact_from_future_element_function(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                ]
            )

            generate_prose_scene(
                root,
                "V1-E01-S01",
                llm,
                check_scale=False,
            )

            self.assertIn(
                "기존 사실을 단순히 다시 언급",
                llm.calls[1][1],
            )
            self.assertIn(
                "following_scene_contracts",
                llm.calls[0][1],
            )
            self.assertIn(
                "의미 공개의 상한",
                llm.calls[0][1],
            )
            self.assertIn(
                "end_state에 도달한 바로",
                llm.calls[0][1],
            )
            self.assertIn(
                "동료의 도움, 환경 변화, 우연한 타이밍",
                llm.calls[0][1],
            )
            self.assertIn(
                "가장 좁은",
                llm.calls[0][1],
            )
            self.assertIn("현재 미래 요소 충돌 특별 경계", llm.calls[0][1])
            self.assertIn("대화 정책은 none", llm.calls[0][1])
            self.assertIn("시도 → 방해나 예상 밖 반응", llm.calls[0][1])
            self.assertIn("같은 공포·깨달음", llm.calls[0][1])
            self.assertIn("정보 전달용 독백", llm.calls[1][1])

    def test_dialogue_required_scene_rejects_description_only_prose(self) -> None:
        context = {
            "scene": {
                "previous_scene_id": "V1-E01-S01",
                "objective": "소라와 협상해 봉인된 문을 연다.",
                "interaction_mode": "interpersonal",
                "dialogue_policy": "required",
            }
        }
        prose = "차가운 복도를 바라보며 공포를 느꼈다. " * 100

        errors = validate_prose_style(context, prose)

        self.assertTrue(any("대화 턴 부족" in error for error in errors), errors)
        self.assertTrue(any("대화 비중 부족" in error for error in errors), errors)

    def test_solo_action_scene_can_omit_dialogue(self) -> None:
        context = {
            "scene": {
                "previous_scene_id": "V1-E01-S01",
                "objective": "붕괴하는 통로에서 탈출한다.",
                "interaction_mode": "solo",
                "dialogue_policy": "none",
            }
        }
        prose = "그는 무너지는 철판을 건너 출구로 달렸다. " * 100

        self.assertEqual([], validate_prose_style(context, prose))

    def test_covert_scene_uses_explicit_optional_dialogue_policy(self) -> None:
        context = {
            "scene": {
                "previous_scene_id": "V1-E02-S06",
                "objective": "정찰대를 피해 증기 파이프 사이에 숨는다.",
                "interaction_mode": "covert",
                "dialogue_policy": "optional",
            }
        }
        prose = "그는 숨을 죽이고 정찰대의 탐조등이 지나가기를 기다렸다. " * 100

        self.assertEqual([], validate_prose_style(context, prose))

    def test_el04_future_guard_forbids_generalized_combat_growth(self) -> None:
        guard = build_future_element_guard(
            {
                "scene": {"objective": "방패의 사각지대를 공격한다."},
                "element_constraints": {
                    "future_forbidden": [
                        {"id": "EL-04", "description": "미래 변칙 검술"}
                    ]
                }
            }
        )

        self.assertIn("EL-04 특별 경계", guard)
        self.assertIn("리아의 도움, 환경, 상대의 실수", guard)
        self.assertIn("새 기술로 명명하지 않는다", guard)
        self.assertIn("기존의 정석적 직선 공격", guard)
        self.assertIn("방패의 틈을 만들거나 돌파하는 원인", guard)

    def test_batch_generates_scenes_in_order_with_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)
            llm = FakeLLM(
                [
                    prose_response("V1-E01-S01"),
                    review_response("V1-E01-S01"),
                    prose_response("V2-E01-S01"),
                    review_response("V2-E01-S01"),
                ]
            )

            results = generate_all_prose(
                root,
                llm,
                limit=2,
                check_scale=False,
            )

            self.assertEqual(
                ["V1-E01-S01", "V2-E01-S01"],
                [path.name for path in results],
            )

    def test_production_generation_rejects_short_volume_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_project(root)
            approve_story(root)

            with self.assertRaisesRegex(ProseGenerationError, "장편 분량 부족"):
                generate_prose_scene(
                    root,
                    "V1-E01-S01",
                    FakeLLM([]),
                )



if __name__ == "__main__":
    unittest.main()
