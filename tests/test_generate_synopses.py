# 게임 시나리오 시놉시스 후보 생성과 critic 선택 계약을 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.generate_synopses import (
    SynopsisGenerationError,
    choose_game_concept,
    generate_concept_candidates,
    generate_game_concept,
    validate_candidates,
    validate_review,
)
from tests.test_generate_candidate import FakeLLM


def candidates_response() -> dict:
    return {
        "candidates": [
            {
                "id": f"S{index}",
                "title": f"서로 다른 게임 원작 {index}",
                "genre": f"장르 {index}",
                "logline": f"주인공이 세계의 위기와 대가를 마주하는 장편 기획 {index}",
                "player_role": f"역할 {index}",
                "core_loop": f"탐색하고 선택하고 귀환하는 반복 {index}",
                "progression": f"관계와 능력을 함께 해금하는 성장 {index}",
                "factions": [f"세력 {index}-A", f"세력 {index}-B"],
                "choice_structure": f"선택이 지역과 결말을 바꾸는 구조 {index}",
                "recommended_volume_count": 3 + (index % 3),
                "volume_arc": [
                    f"{volume}권 전환 {index}"
                    for volume in range(1, 3 + (index % 3) + 1)
                ],
                "volume_count_reason": f"사건 밀도에 적합한 권수 {index}",
                "game_fit": f"게임 콘텐츠로 확장 가능한 이유 {index}",
            }
            for index in range(1, 6)
        ]
    }


def review_response(selected_id: str = "S3") -> dict:
    ranking = [selected_id, *[f"S{i}" for i in range(1, 6) if f"S{i}" != selected_id]]
    return {
        "status": "pass",
        "selected_id": selected_id,
        "ranking": ranking,
        "evaluations": [
            {
                "id": f"S{index}",
                "scores": {
                    "novel": 8,
                    "core_loop": 8,
                    "player_agency": 8,
                    "content_scale": 8,
                    "differentiation": 8,
                },
                "strengths": ["서사와 플레이가 연결됨"],
                "risks": ["중반 반복을 변주해야 함"],
            }
            for index in range(1, 6)
        ],
        "selection_reason": "S3가 장편과 게임 구조의 결합이 가장 강하다.",
        "development_directives": ["주인공의 선택 대가를 세계 규칙으로 고정한다."],
    }


class GenerateSynopsesTests(unittest.TestCase):
    def test_candidates_only_does_not_approve_or_start_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            selected = generate_concept_candidates(
                "",
                output,
                FakeLLM(
                    [
                        json.dumps(candidates_response(), ensure_ascii=False),
                        json.dumps(review_response(), ensure_ascii=False),
                    ]
                ),
            )

            self.assertEqual("S3", selected["id"])
            self.assertNotIn("approved_volume_count", selected)
            self.assertFalse((output / "concept-selection.json").exists())

    def test_candidates_only_can_replace_previous_candidate_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            first = candidates_response()
            second = candidates_response()
            second["candidates"][0]["title"] = "새로 뽑은 첫 후보"

            generate_concept_candidates(
                "",
                output,
                FakeLLM(
                    [
                        json.dumps(first, ensure_ascii=False),
                        json.dumps(review_response(), ensure_ascii=False),
                    ]
                ),
            )
            generate_concept_candidates(
                "",
                output,
                FakeLLM(
                    [
                        json.dumps(second, ensure_ascii=False),
                        json.dumps(review_response(), ensure_ascii=False),
                    ]
                ),
            )

            published = json.loads(
                (output / "synopsis-candidates.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "새로 뽑은 첫 후보",
                published["candidates"][0]["title"],
            )

    def test_user_volume_count_applies_to_all_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            candidates = candidates_response()
            for candidate in candidates["candidates"]:
                candidate["recommended_volume_count"] = 10
                candidate["volume_arc"] = [
                    f"{index}권" for index in range(1, 11)
                ]

            generate_concept_candidates(
                "",
                output,
                FakeLLM(
                    [
                        json.dumps(candidates, ensure_ascii=False),
                        json.dumps(review_response(), ensure_ascii=False),
                    ]
                ),
                10,
            )

            published = json.loads(
                (output / "synopsis-candidates.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                all(
                    candidate["recommended_volume_count"] == 10
                    and len(candidate["volume_arc"]) == 10
                    for candidate in published["candidates"]
                )
            )

    def test_candidates_and_critic_selection_are_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            candidates = candidates_response()
            review = review_response()
            llm = FakeLLM(
                [
                    json.dumps(candidates, ensure_ascii=False),
                    json.dumps(review, ensure_ascii=False),
                ]
            )

            instruction = generate_game_concept("", output, llm)

            selected = json.loads(
                (output / "selected-synopsis.json").read_text(encoding="utf-8")
            )
            self.assertEqual("S3", selected["id"])
            self.assertEqual(3, selected["approved_volume_count"])
            self.assertIn(selected["title"], instruction)
            self.assertIn("선택 대가", instruction)
            self.assertEqual(["generator", "critic"], [call[0] for call in llm.calls])

    def test_invalid_candidate_response_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            valid = candidates_response()
            llm = FakeLLM(
                [
                    json.dumps({"candidates": []}, ensure_ascii=False),
                    json.dumps(valid, ensure_ascii=False),
                    json.dumps(review_response(), ensure_ascii=False),
                ]
            )

            generate_game_concept("전투보다 탐험 중심", output, llm)

            self.assertIn("정확히 5개", llm.calls[1][1])
            self.assertIn("전투보다 탐험 중심", llm.calls[0][1])

    def test_failed_critic_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concept"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("기존 선택", encoding="utf-8")
            responses = [
                json.dumps(candidates_response(), ensure_ascii=False),
                "{}",
                "{}",
                "{}",
            ]

            with self.assertRaises(SynopsisGenerationError):
                generate_game_concept("", output, FakeLLM(responses))

            self.assertEqual("기존 선택", sentinel.read_text(encoding="utf-8"))

    def test_candidate_ids_must_be_exact(self) -> None:
        value = candidates_response()
        value["candidates"][-1]["id"] = "S1"

        errors = validate_candidates(value)

        self.assertTrue(any("S1-S5" in error for error in errors), errors)

    def test_selected_id_must_match_first_ranking(self) -> None:
        value = review_response()
        value["selected_id"] = "S2"

        errors = validate_review(value)

        self.assertTrue(any("ranking 첫 항목" in error for error in errors), errors)

    def test_malformed_ranking_is_reported_without_crashing(self) -> None:
        value = review_response()
        value["ranking"] = {"S1": 1}

        errors = validate_review(value)

        self.assertTrue(any("critic ranking" in error for error in errors), errors)

    def test_user_can_override_critic_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "synopsis-candidates.json").write_text(
                json.dumps(candidates_response(), ensure_ascii=False),
                encoding="utf-8",
            )
            (output / "synopsis-review.json").write_text(
                json.dumps(review_response(), ensure_ascii=False),
                encoding="utf-8",
            )

            instruction = choose_game_concept(output, "S5", "user")

            selected = json.loads(
                (output / "selected-synopsis.json").read_text(encoding="utf-8")
            )
            selection = json.loads(
                (output / "concept-selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual("S5", selected["id"])
            self.assertEqual("user", selection["selected_by"])
            self.assertEqual("S3", selection["critic_recommendation"])
            self.assertIn("S5 후보를 선택", instruction)

    def test_short_recommendation_requires_user_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            candidates = candidates_response()
            candidates["candidates"][2]["recommended_volume_count"] = 2
            candidates["candidates"][2]["volume_arc"] = ["1권", "2권"]
            (output / "synopsis-candidates.json").write_text(
                json.dumps(candidates, ensure_ascii=False),
                encoding="utf-8",
            )
            (output / "synopsis-review.json").write_text(
                json.dumps(review_response("S3"), ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SynopsisGenerationError, "사용자 승인"):
                choose_game_concept(output)

            selection = json.loads(
                (output / "concept-selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual("required", selection["volume_approval"])
            self.assertIsNone(selection["approved_volume_count"])
            selected = json.loads(
                (output / "selected-synopsis.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("approved_volume_count", selected)

    def test_user_volume_override_revises_arc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "synopsis-candidates.json").write_text(
                json.dumps(candidates_response(), ensure_ascii=False),
                encoding="utf-8",
            )
            (output / "synopsis-review.json").write_text(
                json.dumps(review_response(), ensure_ascii=False),
                encoding="utf-8",
            )
            revision = {
                "logline": "4권에 맞춰 보강한 시놉시스",
                "recommended_volume_count": 4,
                "volume_arc": ["1권", "2권", "3권", "4권"],
                "volume_count_reason": "4단계 변화가 필요함",
            }

            choose_game_concept(
                output,
                volume_count=4,
                llm=FakeLLM(json.dumps(revision, ensure_ascii=False)),
            )

            selected = json.loads(
                (output / "selected-synopsis.json").read_text(encoding="utf-8")
            )
            self.assertEqual(4, selected["approved_volume_count"])
            self.assertEqual(4, len(selected["volume_arc"]))


if __name__ == "__main__":
    unittest.main()
