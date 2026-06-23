# Forge 모델 호출의 역할별 생성 설정을 검증하는 테스트
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

LIB_ROOT = Path(__file__).resolve().parent.parent / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from llm import GENERATOR_MAX_OUTPUT_TOKENS, LLMClient, _is_transient


class LLMClientTests(unittest.TestCase):
    def test_httpx_read_timeout_is_transient(self) -> None:
        read_timeout = type("ReadTimeout", (Exception,), {"__module__": "httpx"})

        self.assertTrue(_is_transient(read_timeout("응답을 기다리다 연결이 끊김")))

    def test_generator_requests_explicit_output_budget(self) -> None:
        client = LLMClient.__new__(LLMClient)
        client.max_calls = None
        client.call_count = 0
        client._generate_with = MagicMock(return_value="산문")

        with patch("llm.get_model", return_value="generator-model"):
            result = client.generate("generator", "prompt", temperature=0.8)

        self.assertEqual("산문", result)
        config = client._generate_with.call_args.args[3]
        self.assertEqual(
            GENERATOR_MAX_OUTPUT_TOKENS,
            config["max_output_tokens"],
        )

    def test_critic_does_not_force_generator_output_budget(self) -> None:
        client = LLMClient.__new__(LLMClient)
        client.max_calls = None
        client.call_count = 0
        client._generate_with = MagicMock(return_value="검토")

        with patch("llm.get_model", return_value="critic-model"):
            result = client.generate("critic", "prompt", temperature=0.0)

        self.assertEqual("검토", result)
        config = client._generate_with.call_args.args[3]
        self.assertNotIn("max_output_tokens", config)


if __name__ == "__main__":
    unittest.main()
