# 모델 응답에서 JSON 객체를 안전하게 추출하는 유틸
"""Forge 자족용으로 복사한 JSON 추출기다.
모델이 코드펜스나 산문에 섞어 낸 응답에서 첫 JSON 객체만 추출한다. 실패 시 {}."""

import json
import re

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text):
    """모델 응답에서 첫 JSON 객체를 뽑는다(코드펜스 우선, 없으면 첫 { ~ 균형 }). 실패 시 {}."""
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}
