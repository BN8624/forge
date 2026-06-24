# 신규 세계관 원천의 생성 계약, 재시도, 원자적 게시를 검증하는 테스트
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.generate_world import (
    WorldGenerationError,
    build_world_prompt,
    generate_world,
    validate_world_source,
)
from tests.test_generate_candidate import FakeLLM


def world_source(title: str = "유리바다의 항해자") -> dict:
    return {
        "title": title,
        "genre": "해양 과학 판타지",
        "tone": "장엄하고 서늘함",
        "premise": (
            "바다가 유리처럼 굳어가는 행성에서 기억을 지도에 새기는 항해사와 "
            "조수 소녀가 침몰한 달의 신호를 추적한다. 두 사람은 도시 국가들이 "
            "숨긴 재난의 원인과 자신들의 탄생에 얽힌 비밀을 밝히며 세계의 마지막 "
            "조류를 되돌릴 선택을 해야 한다."
        ),
        "canon": [
            {"id": f"C{index}", "text": f"새 세계의 검증 가능한 정본 사실 {index}번이다."}
            for index in range(1, 22)
        ],
        "manuscript": (
            "# 유리바다의 항해자\n\n"
            + "얼어붙은 파도 아래에서 푸른 신호가 맥박쳤다. "
            "항해사는 기억 나침반을 들어 멀리 침몰한 달을 바라보았다. "
        )
        * 45,
    }


class GenerateWorldTests(unittest.TestCase):
    def test_game_scenario_prompt_uses_game_scenario_contract(self) -> None:
        prompt = build_world_prompt("잠입 게임", 4, game_scenario=True)

        self.assertIn("4단계의 게임 시나리오", prompt)
        self.assertIn("플레이어 선택지", prompt)
        self.assertIn("장편 구조와 완결된 산문", build_world_prompt("", 4))

    def test_valid_world_is_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "current"
            value = world_source()

            generate_world("", output, FakeLLM(json.dumps(value, ensure_ascii=False)))

            canon = json.loads(
                (output / "canon_bible.json").read_text(encoding="utf-8")
            )
            metadata = json.loads((output / "world.json").read_text(encoding="utf-8"))
            self.assertEqual(value["title"], canon["title"])
            self.assertEqual(21, len(canon["canon"]))
            self.assertEqual(value["genre"], metadata["genre"])
            self.assertIn(
                "유리바다의 항해자",
                (output / "compressed_manuscript.md").read_text(encoding="utf-8"),
            )

    def test_invalid_first_world_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "current"
            invalid = world_source("에테르노 재탕")
            valid = world_source()
            llm = FakeLLM(
                [
                    json.dumps(invalid, ensure_ascii=False),
                    json.dumps(valid, ensure_ascii=False),
                ]
            )

            generate_world("새 해양 세계", output, llm)

            self.assertEqual(2, len(llm.calls))
            self.assertIn("기존 세계관 고유어", llm.calls[1][1])
            self.assertIn("새 해양 세계", llm.calls[0][1])

    def test_failed_generation_preserves_existing_world(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "current"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("기존 세계", encoding="utf-8")

            with self.assertRaises(WorldGenerationError):
                generate_world("", output, FakeLLM(["{}"] * 5))

            self.assertEqual("기존 세계", sentinel.read_text(encoding="utf-8"))

    def test_canon_ids_must_be_exact(self) -> None:
        value = world_source()
        value["canon"][-1]["id"] = "C1"

        errors = validate_world_source(value)

        self.assertTrue(any("C1-C21" in error for error in errors), errors)

    def test_selected_synopsis_title_and_genre_are_binding(self) -> None:
        value = world_source()

        errors = validate_world_source(
            value,
            {"title": "다른 제목", "genre": "다른 장르"},
        )

        self.assertTrue(any("title 불일치" in error for error in errors), errors)
        self.assertTrue(any("genre 불일치" in error for error in errors), errors)

    def test_short_manuscript_is_extended_by_generator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "current"
            value = world_source()
            value["manuscript"] = value["manuscript"][:1800]
            addition = "새 공동체는 기억을 소유하지 않고 서로 증언했다. " * 80
            llm = FakeLLM(
                [
                    json.dumps(value, ensure_ascii=False),
                    json.dumps(
                        {"manuscript_addition": addition},
                        ensure_ascii=False,
                    ),
                ]
            )

            generate_world("", output, llm)

            manuscript = (output / "compressed_manuscript.md").read_text(
                encoding="utf-8"
            )
            self.assertGreaterEqual(len(manuscript.strip()), 3000)
            self.assertEqual(["generator", "generator"], [call[0] for call in llm.calls])


if __name__ == "__main__":
    unittest.main()
