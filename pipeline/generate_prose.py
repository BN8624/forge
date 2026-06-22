# 정본 장면 계약에서 산문을 생성하고 critic 승인 뒤 원자적으로 승격하는 도구
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIB_ROOT = PROJECT_ROOT / "lib"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.jsonutil import extract_json
from pipeline.generate_candidate import load_source_material
from pipeline.validate_scale import validate_story_scale
from pipeline.validate_structure import validate_project, validate_schema


MAX_PROSE_ATTEMPTS = 3
MAX_REVIEW_ATTEMPTS = 3
MIN_LENGTH_RATIO = 0.7
MAX_LENGTH_RATIO = 1.5
PROSE_CONTRACT_VERSION = 2
DIALOGUE_PATTERN = re.compile(
    r'“[^”\n]+”|"[^"\n]+"|^—[^\n]+$',
    re.MULTILINE,
)
SOLO_ACTION_TERMS = (
    "추격",
    "도주",
    "탈출",
    "잠입",
    "전투",
    "붕괴",
    "추락",
    "등반",
    "수색",
)


class LLM(Protocol):
    def generate(
        self,
        role: str,
        prompt: str,
        temperature: float | None = None,
    ) -> str: ...


class ProseGenerationError(Exception):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ordered_scene_ids(root: Path) -> list[str]:
    story = root / "story"
    series = read_json(story / "series.json")
    result: list[str] = []
    for volume_id in series["volume_ids"]:
        volume = read_json(story / "volumes" / f"{volume_id}.json")
        for event_id in volume["event_ids"]:
            event = read_json(story / "events" / f"{event_id}.json")
            result.extend(event["scene_ids"])
    return result


def prose_scene_dir(root: Path, scene_id: str) -> Path:
    return root / "prose" / "scenes" / scene_id


def prose_scene_complete(root: Path, scene_id: str) -> bool:
    directory = prose_scene_dir(root, scene_id)
    return (
        (directory / "prose.md").is_file()
        and (directory / "review.json").is_file()
    )


def contract_sha256(scene: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "prose_contract_version": PROSE_CONTRACT_VERSION,
            "scene": scene,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dialogue_required(context: dict[str, Any]) -> bool:
    scene = context["scene"]
    if scene.get("previous_scene_id") is None:
        return False
    objective = str(scene.get("objective", ""))
    return not any(term in objective for term in SOLO_ACTION_TERMS)


def validate_prose_style(
    context: dict[str, Any],
    prose: str,
) -> list[str]:
    errors: list[str] = []
    dialogue = DIALOGUE_PATTERN.findall(prose)
    dialogue_chars = sum(len(block) for block in dialogue)
    dialogue_ratio = dialogue_chars / max(1, len(prose))
    if dialogue_required(context):
        if len(dialogue) < 4:
            errors.append(
                f"대화 턴 부족: {len(dialogue)}개. "
                "대화 가능한 장면은 최소 4개 발화가 필요함"
            )
        if dialogue_ratio < 0.08:
            errors.append(
                f"대화 비중 부족: {dialogue_ratio:.1%}. "
                "대화 가능한 장면은 최소 8%가 필요함"
            )
    return errors


def merge_feedback(
    existing: list[str] | None,
    added: list[str],
) -> list[str]:
    merged = list(existing or [])
    for item in added:
        if item and item not in merged:
            merged.append(item)
    return merged


def read_failure_feedback(work_dir: Path) -> list[str]:
    path = work_dir / "last-failure.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def write_failure_feedback(work_dir: Path, errors: list[str]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "last-failure.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def next_artifact_index(work_dir: Path, pattern: str) -> int:
    return len(list(work_dir.glob(pattern))) + 1


def approved_prose(root: Path, scene_id: str) -> str:
    directory = prose_scene_dir(root, scene_id)
    prose_path = directory / "prose.md"
    review_path = directory / "review.json"
    try:
        prose = prose_path.read_text(encoding="utf-8")
        review = read_json(review_path)
        scene = read_json(root / "story" / "scenes" / f"{scene_id}.json")
    except (OSError, json.JSONDecodeError) as exc:
        raise ProseGenerationError(
            f"이전 정본 산문 또는 검토를 읽을 수 없음: {scene_id}: {exc}"
        ) from exc

    errors: list[str] = []
    validate_schema(
        review,
        "prose_review.schema.json",
        f"{scene_id}/review.json",
        errors,
    )
    prose_hash = hashlib.sha256(prose.encode("utf-8")).hexdigest()
    if review.get("status") != "pass":
        errors.append(f"이전 산문 critic 미승인: {scene_id}")
    if review.get("prose_sha256") != prose_hash:
        errors.append(f"이전 산문 해시 불일치: {scene_id}")
    if review.get("scene_contract_sha256") != contract_sha256(scene):
        errors.append(f"이전 산문의 장면 계약이 변경됨: {scene_id}")
    if errors:
        raise ProseGenerationError(errors)
    return prose


def select_scene(root: Path, requested_scene_id: str | None) -> str:
    scene_ids = ordered_scene_ids(root)
    if requested_scene_id is None:
        for scene_id in scene_ids:
            if not prose_scene_complete(root, scene_id):
                return scene_id
        raise ProseGenerationError("모든 장면의 정본 산문이 이미 존재함")
    if requested_scene_id not in scene_ids:
        raise ProseGenerationError(f"정본에 없는 장면 ID: {requested_scene_id}")
    return requested_scene_id


def load_scene_context(root: Path, scene_id: str) -> dict[str, Any]:
    story = root / "story"
    scene = read_json(story / "scenes" / f"{scene_id}.json")
    event = read_json(story / "events" / f"{scene['event_id']}.json")
    volume = read_json(story / "volumes" / f"{event['volume_id']}.json")
    series = read_json(story / "series.json")
    canon_review = read_json(story / "canon-review.json")
    canon, _ = load_source_material()
    canon_by_id = {
        item["id"]: item
        for item in canon["canon"]
    }
    scene_ids = ordered_scene_ids(root)
    scene_order = {
        ordered_scene_id: index
        for index, ordered_scene_id in enumerate(scene_ids)
    }
    current_index = scene_order[scene_id]
    following_scene_contracts = []
    for following_scene_id in scene_ids[current_index + 1:current_index + 3]:
        following_scene = read_json(
            story / "scenes" / f"{following_scene_id}.json"
        )
        following_scene_contracts.append(
            {
                "id": following_scene["id"],
                "objective": following_scene["objective"],
                "start_state": following_scene["start_state"],
                "end_state": following_scene["end_state"],
            }
        )
    relevant_canon = [
        verdict
        for verdict in canon_review["verdicts"]
        if scene_id in verdict["scene_ids"]
    ]
    available_canon_ids: set[str] = set()
    future_canon_ids: set[str] = set()
    for verdict in canon_review["verdicts"]:
        indexes = [
            scene_order[verdict_scene_id]
            for verdict_scene_id in verdict["scene_ids"]
            if verdict_scene_id in scene_order
        ]
        if indexes and min(indexes) <= current_index:
            available_canon_ids.add(verdict["canon_id"])
        elif indexes:
            future_canon_ids.add(verdict["canon_id"])

    element_owner_index: dict[str, int] = {}
    for ordered_scene_id in scene_ids:
        ordered_scene = read_json(
            story / "scenes" / f"{ordered_scene_id}.json"
        )
        for owner_key in ("changes", "setups", "payoffs"):
            for element_id in ordered_scene["owns"][owner_key]:
                element_owner_index[element_id] = scene_order[ordered_scene_id]
    element_by_id = {
        element["id"]: element
        for element in series["elements"]
    }
    current_owned_ids = {
        element_id
        for owner_key in ("changes", "setups", "payoffs")
        for element_id in scene["owns"][owner_key]
    }
    available_element_ids = {
        element_id
        for element_id, owner_index in element_owner_index.items()
        if owner_index <= current_index
    }
    future_element_ids = {
        element_id
        for element_id, owner_index in element_owner_index.items()
        if owner_index > current_index
    }
    return {
        "series": series,
        "volume": volume,
        "event": event,
        "scene": scene,
        "following_scene_contracts": following_scene_contracts,
        "relevant_canon": relevant_canon,
        "canon_constraints": {
            "current": [
                canon_by_id[verdict["canon_id"]]
                for verdict in relevant_canon
            ],
            "available": [
                canon_by_id[canon_id]
                for canon_id in sorted(available_canon_ids)
            ],
            "future_forbidden": [
                canon_by_id[canon_id]
                for canon_id in sorted(future_canon_ids)
            ],
        },
        "element_constraints": {
            "current_owned": [
                element_by_id[element_id]
                for element_id in sorted(current_owned_ids)
            ],
            "available": [
                element_by_id[element_id]
                for element_id in sorted(available_element_ids)
            ],
            "future_forbidden": [
                element_by_id[element_id]
                for element_id in sorted(future_element_ids)
            ],
            "rule": (
                "available canon에 이미 공개된 물리적 사실은 언급할 수 있다. "
                "다만 future_forbidden 요소가 뜻하는 기능, 인과, 기술, "
                "성장 가능성, 복선 의미를 새로 부여하거나 활용하면 안 된다."
            ),
        },
    }


def previous_prose_context(root: Path, scene_id: str) -> str:
    scene_ids = ordered_scene_ids(root)
    index = scene_ids.index(scene_id)
    for earlier_scene_id in scene_ids[:index]:
        approved_prose(root, earlier_scene_id)
    if index == 0:
        return ""
    return approved_prose(root, scene_ids[index - 1])


def build_future_element_guard(context: dict[str, Any]) -> str:
    future_ids = {
        element["id"]
        for element in context["element_constraints"]["future_forbidden"]
    }
    guards: list[str] = []
    if "EL-04" in future_ids:
        guards.append(
            "- EL-04 특별 경계: 환상통이나 순간적인 변칙 움직임이 objective에 "
            "있어도 왼팔 부재·신체 불균형을 무기, 검술 원리, 예측 불가 궤적, "
            "성장 가능성으로 해석하지 않는다. 일회성 통증 반응은 가능하지만 "
            "승리의 결정 원인은 리아의 도움, 환경, 상대의 실수에 둔다. "
            "현재 장면에서 이를 깨달음이나 새 기술로 명명하지 않는다."
        )
        if "방패" in context["scene"]["objective"]:
            guards.append(
                "- EL-04 방패 장면 필수 해결법: 리아의 개입, 지형 붕괴, "
                "장치 오작동, 지휘관의 판단 실수 중 하나가 먼저 방패를 "
                "무력화하거나 고정된 틈을 만든다. 카엘은 그 뒤 기존의 정석적 "
                "직선 공격으로 노출된 적을 제압한다. 카엘의 왼팔 부재, "
                "신체 불균형, 환상통, 비정상 궤적, 변칙 움직임은 방패의 틈을 "
                "만들거나 돌파하는 원인이 되어서는 안 된다."
            )
    return "\n".join(guards) or "- 추가 특별 경계 없음."


def build_generator_prompt(
    context: dict[str, Any],
    previous_prose: str,
    feedback: list[str] | None = None,
    previous_candidate: str = "",
) -> str:
    _, manuscript = load_source_material()
    scene = context["scene"]
    feedback_text = (
        json.dumps(feedback, ensure_ascii=False, indent=2)
        if feedback
        else "없음"
    )
    return f"""너는 Forge의 소설 산문 generator다.
정본 계약을 바꾸지 말고 현재 장면의 산문만 작성하라.

정본 구조 계약:
{json.dumps(context, ensure_ascii=False, indent=2)}

기존 작품의 문체와 세계관 참고 원고:
{manuscript}

직전 승인 산문:
{previous_prose or "첫 장면이므로 없음"}

직전 critic 피드백:
{feedback_text}

직전 산문 후보:
{previous_candidate or "없음"}

현재 미래 요소 충돌 특별 경계:
{build_future_element_guard(context)}

요구 사항:
- 장면 목표, start_state에서 end_state로의 변화, owns 요소를 산문 안에서 달성한다.
- canon_constraints.current만 이 장면에서 새로 드러낼 수 있다.
- canon_constraints.future_forbidden과 element_constraints.future_forbidden의
  사실·기술·회수·변화를 미리 공개하거나 실행하지 않는다.
- canon_constraints.available에 이미 공개된 사실은 계속 언급할 수 있다.
  예를 들어 C1이 available이면 왼팔 부재 자체는 묘사 가능하지만,
  미래 EL-03의 신체적 불균형을 전투 변수·성장 가능성으로 해석하거나
  미래 EL-04의 변칙 검술로 활용해서는 안 된다.
- element_constraints.current_owned가 비어 있으면 새 setup, payoff, change를
  이 장면의 성과처럼 도입하지 않는다.
- 이후 장면의 사건을 미리 완결하지 않는다.
- following_scene_contracts의 objective에 예약된 정보·결단·행동은 현재
  장면에서 명시적으로 공개하거나 달성하지 않는다.
- scene.end_state는 현재 장면의 절대 종료선이다. end_state에 도달한 바로
  그 순간 장면을 끝내며, 그 뒤의 이동 결과·새 장소 진입·안도·불안·
  후속 반응을 한 문장도 덧붙이지 않는다.
- 마지막 문단은 현재 objective의 성취까지만 묘사한다. 그 성취로 열린
  다음 공간의 풍경이나 다음 행동의 시작은 following_scene_contracts에
  예약된 것으로 보고 묘사하지 않는다.
- 현재 scene.objective를 의미 공개의 상한으로 취급한다. 목표에 인물·장소·
  적대자가 언급되어도 future_forbidden에 있는 정체, 환경 특징, 힘의 원천,
  기술 원리, 성장 의미를 덧붙이지 않는다.
- scene.objective의 단어가 future_forbidden 요소와 일부 겹쳐도 그 요소의
  기능·인과·기술을 사용할 권한은 생기지 않는다. current_owned가 아닌
  미래 요소와 겹치는 목표는 동료의 도움, 환경 변화, 우연한 타이밍,
  상대의 실수처럼 해당 요소와 무관한 원인으로 달성한다.
- 특히 목표에 변칙적 움직임이나 빈틈 공략이 있어도, future_forbidden에
  신체 결함을 이용한 전투 기술이나 방패 돌파가 있으면 신체 불균형,
  비정상 궤적, 예측 불가 검술을 해결 원인으로 사용하지 않는다.
- objective 자체가 future_forbidden의 일부 행동을 요구하면 가장 좁은
  일회성 사건으로만 수행한다. 우발적 경련, 순간적 통증, 동료가 만든
  빈틈처럼 현재 상황에 한정하고 이를 기술·각성·성장·재사용 가능한 원리로
  명명하거나 일반화하지 않는다. 미래 요소의 다른 구성 요소나 대상까지
  결합해 완성된 기능을 미리 구현하지 않는다.
- future_forbidden의 설명은 정확한 문구뿐 아니라 동의어, 은유, 추측,
  예감, 상징적 복선으로도 암시하지 않는다.
- current_owned가 비어 있으면 기존 결함이나 능력을 미래의 무기·가능성·
  계승·성장 징후로 재해석하지 않는다.
- 현재 장면이 계시·기억·사념의 감각만 담당하고 다음 장면이 그 의미를
  해석한다면, 현재 장면에서는 목소리의 내용이 이해 불가능한 파편이나
  감각으로만 전달되게 한다.
- 정본 설정과 직전 산문의 사실·시점·인물 상태를 유지한다.
- 요약문이나 개요가 아니라 출판 가능한 한국어 소설 산문을 작성한다.
- 장면은 `즉시 목표 제시 → 인물의 시도 → 방해나 예상 밖 반응 →
  선택 또는 전술 변화 → end_state를 만드는 결과`의 진행 비트를 가진다.
- 상황·감각·내면 묘사를 두 문단 이상 연속으로 쌓지 않는다. 다음 문단에서는
  인물의 행동, 상대의 반응, 대화, 새 단서 중 하나로 상황을 바꾼다.
- 같은 공포·깨달음·세계 규칙을 비유만 바꾸어 다시 설명하지 않는다.
  한 번 전달한 의미는 반복하지 말고 인물의 다음 행동과 대가로 전진한다.
- 대화 가능한 인물이 함께 있으면 산문 분량의 약 15-35%를 대화로 구성하고
  최소 4개 발화를 주고받는다. 대화는 설정 강의가 아니라 요구, 거절,
  의심, 협상, 갈등, 관계 변화 또는 행동 결정을 만든다.
- 설정을 공개해야 하는 장면도 한 인물의 긴 설명으로 처리하지 않는다.
  질문, 불신, 끼어듦, 거짓말, 즉각적인 위험이나 선택 속에서 나누어 드러낸다.
- 첫 장면처럼 혼자인 경우에도 내면 독백으로 분량을 채우지 않는다.
  구체적 행동을 세 번 이상 시도하고, 최소 한 번 실패하거나 예상 밖 결과를
  겪은 뒤 다음 행동을 선택하게 한다.
- 장면 끝에서 인물이나 관계, 위험, 계획, 보유 정보 중 최소 하나가 시작과
  명확히 달라야 한다. 단순히 상황을 이해하거나 감정을 오래 느끼는 것으로
  상태 전이를 대신하지 않는다.
- 목표 분량은 공백 포함 약 {scene['target_chars']}자다.
- 직전 산문 후보가 있으면 버리지 말고 장면 목표와 상태 전이를 유지하면서
  critic 피드백을 반영해 전체 장면을 다시 구성한다. 끝에 묘사 문단만
  덧붙여 분량을 채우지 않는다.
- 설명이나 코드펜스 없이 JSON 객체 하나만 반환한다.
- 반환 키는 scene_id와 prose만 사용한다.
"""


def parse_prose_response(
    response: str,
    scene_id: str,
    target_chars: int,
    enforce_length: bool = True,
) -> str:
    value = extract_json(response)
    if (
        isinstance(value, dict)
        and set(value) == {scene_id}
        and isinstance(value[scene_id], str)
    ):
        prose = value[scene_id]
    elif (
        isinstance(value, dict)
        and set(value) == {scene_id}
        and isinstance(value[scene_id], dict)
        and set(value[scene_id]) == {"prose"}
    ):
        prose = value[scene_id]["prose"]
    elif isinstance(value, dict) and set(value) == {"scene_id", "prose"}:
        if value["scene_id"] != scene_id:
            raise ProseGenerationError(
                f"산문 응답 장면 ID 불일치: {value['scene_id']!r}"
            )
        prose = value["prose"]
    else:
        raise ProseGenerationError(
            "산문 응답은 scene_id와 prose 객체 또는 장면 ID 단일 키 "
            "문자열·prose 객체여야 함"
        )
    if not isinstance(prose, str) or not prose.strip():
        raise ProseGenerationError("산문 응답 prose가 비어 있음")
    minimum = int(target_chars * MIN_LENGTH_RATIO)
    maximum = int(target_chars * MAX_LENGTH_RATIO)
    if enforce_length and not minimum <= len(prose) <= maximum:
        raise ProseGenerationError(
            f"산문 길이 범위 위반: {len(prose)}자 "
            f"(허용 {minimum}-{maximum}자)"
        )
    return prose.strip()


def build_critic_prompt(
    context: dict[str, Any],
    previous_prose: str,
    prose: str,
) -> str:
    canon, _ = load_source_material()
    return f"""너는 Forge의 독립 산문 critic이다.
산문을 수정하거나 다시 쓰지 말고 계약 준수 여부만 엄격히 판정하라.

전체 정본 설정:
{json.dumps(canon, ensure_ascii=False, indent=2)}

현재 장면 계약:
{json.dumps(context, ensure_ascii=False, indent=2)}

직전 승인 산문:
{previous_prose or "첫 장면이므로 없음"}

검증할 산문:
{prose}

설명이나 코드펜스 없이 JSON 객체 하나만 반환하라.
키는 scene_id, status, checks, issues, reason이다.
status는 pass, fail, uncertain 중 하나다.
checks에는 objective, state_transition, owned_elements, reveal_order, canon,
continuity, prose_quality 불리언을 모두 넣는다.
모든 checks가 true일 때만 pass다.
설정 충돌, 장면 목표 누락, 상태 전이 누락, 미래 사건 선취, 요약문 문체,
이전 산문 불연속이 있으면 fail 또는 uncertain으로 판정한다.
prose_quality는 문장 미려함만 뜻하지 않는다. 아래 중 하나라도 해당하면
반드시 false다.
- 상황·감각·내면 묘사가 두 문단 넘게 이어지며 행동이나 대화로 상황이 변하지 않음.
- 같은 공포, 깨달음, 규칙, 결심을 표현만 바꾸어 반복함.
- 장면에 시도, 방해·반전, 선택·전술 변화, 관찰 가능한 결과가 없음.
- 다른 인물이 있는데 대화가 거의 없거나, 대화가 정보 전달용 독백이나
  일방적인 설정 강의임.
- 대화가 인물의 행동, 관계, 정보 신뢰도, 계획 중 아무것도 바꾸지 않음.
- end_state가 사건의 결과가 아니라 서술자의 설명이나 주인공의 이해만으로 성립함.
빠른 진행을 위해 매 2-3문단마다 행동, 반응, 새 단서, 선택 중 하나가
발생해야 한다. 장면의 마지막은 반복 성찰이 아니라 결정 또는 결과로 끝낸다.
following_scene_contracts의 목표를 현재 산문이 미리 달성하면
reveal_order를 false로 판정한다.
future_forbidden의 핵심 의미를 동의어, 은유, 추측, 예감으로 암시해도
reveal_order를 false로 판정한다. scene.objective에 필요 없는 힘의 원천,
장소 고유 특성, 미래 기술 가능성, 계승 의미를 덧붙이면 통과시키지 않는다.
특히 canon_constraints.future_forbidden 또는
element_constraints.future_forbidden을 미리 드러내면 reveal_order를 false로
판정한다. 현재 장면 owns에 없는 요소를 새로 설치·회수·변화시키면
owned_elements를 false로 판정한다.
단, canon_constraints.available에 있는 기존 사실을 단순히 다시 언급한 것만으로
미래 요소 선취로 판정하지 않는다. 미래 요소가 정의하는 새 기능·인과·기술·
성장 의미를 부여했을 때만 reveal_order 또는 owned_elements를 false로 판정한다.
"""


def parse_review_response(
    response: str,
    scene: dict[str, Any],
    prose: str,
) -> dict[str, Any]:
    review = extract_json(response)
    if not isinstance(review, dict):
        raise ProseGenerationError("critic 응답의 최상위 값은 객체여야 함")
    review = dict(review)
    review["prose_sha256"] = hashlib.sha256(prose.encode("utf-8")).hexdigest()
    review["scene_contract_sha256"] = contract_sha256(scene)
    errors: list[str] = []
    validate_schema(
        review,
        "prose_review.schema.json",
        f"{scene['id']}/review.json",
        errors,
    )
    if errors:
        raise ProseGenerationError(errors)
    checks_pass = all(review["checks"].values())
    if review["status"] == "pass" and not checks_pass:
        raise ProseGenerationError("critic pass와 checks 결과가 일치하지 않음")
    if review["status"] != "pass" and checks_pass:
        raise ProseGenerationError("critic 비통과 상태와 checks 결과가 일치하지 않음")
    return review


def review_prose(
    llm: LLM,
    context: dict[str, Any],
    previous_prose: str,
    prose: str,
    failure_dir: Path | None = None,
) -> dict[str, Any]:
    original_prompt = build_critic_prompt(context, previous_prose, prose)
    prompt = original_prompt
    last_errors: list[str] = []
    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        response = llm.generate("critic", prompt, temperature=0.0)
        try:
            return parse_review_response(response, context["scene"], prose)
        except ProseGenerationError as exc:
            last_errors = exc.errors
            if failure_dir is not None:
                failure_dir.mkdir(parents=True, exist_ok=True)
                (failure_dir / f"critic-attempt-{attempt}.txt").write_text(
                    response,
                    encoding="utf-8",
                )
            if attempt == MAX_REVIEW_ATTEMPTS:
                raise
            prompt = f"""{original_prompt}

직전 critic 응답은 형식 계약을 위반했다.
아래 오류를 해결한 전체 JSON 객체를 다시 반환하라.
{json.dumps(last_errors, ensure_ascii=False, indent=2)}
"""
    raise ProseGenerationError(last_errors)


def promote_prose(
    root: Path,
    scene_id: str,
    prose: str,
    review: dict[str, Any],
) -> Path:
    parent = root / "prose" / "scenes"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / scene_id
    if target.exists():
        raise ProseGenerationError(f"정본 산문이 이미 존재함: {scene_id}")
    staging_root = Path(tempfile.mkdtemp(prefix=".prose-", dir=parent))
    staged = staging_root / scene_id
    staged.mkdir()
    try:
        (staged / "prose.md").write_text(prose, encoding="utf-8")
        (staged / "review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staged, target)
    except OSError as exc:
        raise ProseGenerationError(f"산문 정본 승격 실패: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return target


def generate_prose_scene(
    root: Path,
    requested_scene_id: str | None,
    llm: LLM,
    check_scale: bool = True,
) -> Path:
    root = root.resolve()
    structure_errors = validate_project(root)
    if structure_errors:
        raise ProseGenerationError(structure_errors)
    if check_scale:
        scale_errors = validate_story_scale(root)
        if scale_errors:
            raise ProseGenerationError(scale_errors)
    scene_id = select_scene(root, requested_scene_id)
    target_directory = prose_scene_dir(root, scene_id)
    if prose_scene_complete(root, scene_id):
        raise ProseGenerationError(f"정본 산문이 이미 존재함: {scene_id}")
    if target_directory.exists():
        if any(target_directory.iterdir()):
            raise ProseGenerationError(
                f"불완전한 산문 디렉터리가 존재함: {scene_id}"
            )
        target_directory.rmdir()
    previous_prose = previous_prose_context(root, scene_id)
    context = load_scene_context(root, scene_id)
    scene = context["scene"]
    feedback: list[str] | None = None
    previous_candidate = ""
    last_errors: list[str] = []
    work_dir = (
        root
        / "runs"
        / "prose-work"
        / scene_id
        / contract_sha256(scene)
    )
    feedback = read_failure_feedback(work_dir)
    if work_dir.exists():
        recovery_paths = sorted(
            work_dir.glob("generator-attempt-*.txt"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for recovery_path in recovery_paths:
            try:
                recovered_response = recovery_path.read_text(encoding="utf-8")
                previous_candidate = parse_prose_response(
                    recovered_response,
                    scene_id,
                    scene["target_chars"],
                    enforce_length=False,
                )
                break
            except (OSError, ProseGenerationError):
                continue
        if previous_candidate:
            try:
                parse_prose_response(
                    json.dumps(
                        {"scene_id": scene_id, "prose": previous_candidate},
                        ensure_ascii=False,
                    ),
                    scene_id,
                    scene["target_chars"],
                )
                style_errors = validate_prose_style(
                    context,
                    previous_candidate,
                )
                if style_errors:
                    raise ProseGenerationError(style_errors)
                recovered_review = review_prose(
                    llm,
                    context,
                    previous_prose,
                    previous_candidate,
                    failure_dir=work_dir,
                )
                if recovered_review["status"] == "pass":
                    return promote_prose(
                        root,
                        scene_id,
                        previous_candidate,
                        recovered_review,
                    )
                last_errors = [
                    *recovered_review["issues"],
                    recovered_review["reason"],
                ]
                feedback = merge_feedback(feedback, last_errors)
                write_failure_feedback(work_dir, feedback)
                previous_candidate = ""
            except ProseGenerationError as exc:
                last_errors = exc.errors
                feedback = merge_feedback(feedback, last_errors)
                write_failure_feedback(work_dir, feedback)

    for attempt in range(1, MAX_PROSE_ATTEMPTS + 1):
        minimum = int(scene["target_chars"] * MIN_LENGTH_RATIO)
        if previous_candidate and len(previous_candidate) < minimum:
            feedback = merge_feedback(
                feedback,
                [
                (
                    f"산문 길이 부족: {len(previous_candidate)}자. "
                    f"최소 {minimum}자를 충족하도록 전체 장면을 다시 구성하라. "
                    "기존 끝에 묘사나 독백을 덧붙이지 않는다."
                ),
                ],
            )
            write_failure_feedback(work_dir, feedback)
            previous_candidate = ""
        response = llm.generate(
            "generator",
            build_generator_prompt(
                context,
                previous_prose,
                feedback,
                previous_candidate,
            ),
            temperature=0.8,
        )
        try:
            prose = parse_prose_response(
                response,
                scene_id,
                scene["target_chars"],
            )
        except ProseGenerationError as exc:
            last_errors = exc.errors
            try:
                previous_candidate = parse_prose_response(
                    response,
                    scene_id,
                    scene["target_chars"],
                    enforce_length=False,
                )
            except ProseGenerationError:
                previous_candidate = ""
            work_dir.mkdir(parents=True, exist_ok=True)
            artifact_index = next_artifact_index(
                work_dir,
                "generator-attempt-*.txt",
            )
            (work_dir / f"generator-attempt-{artifact_index}.txt").write_text(
                response,
                encoding="utf-8",
            )
            feedback = merge_feedback(feedback, last_errors)
            write_failure_feedback(work_dir, feedback)
            if attempt == MAX_PROSE_ATTEMPTS:
                break
            continue

        style_errors = validate_prose_style(context, prose)
        if style_errors:
            work_dir.mkdir(parents=True, exist_ok=True)
            artifact_index = next_artifact_index(
                work_dir,
                "style-rejected-prose-*.md",
            )
            (work_dir / f"style-rejected-prose-{artifact_index}.md").write_text(
                prose,
                encoding="utf-8",
            )
            last_errors = style_errors
            feedback = merge_feedback(feedback, style_errors)
            write_failure_feedback(work_dir, feedback)
            previous_candidate = ""
            if attempt == MAX_PROSE_ATTEMPTS:
                break
            continue

        review = review_prose(
            llm,
            context,
            previous_prose,
            prose,
            failure_dir=work_dir,
        )
        if review["status"] == "pass":
            return promote_prose(root, scene_id, prose, review)
        work_dir.mkdir(parents=True, exist_ok=True)
        artifact_index = next_artifact_index(
            work_dir,
            "critic-rejected-prose-*.md",
        )
        (work_dir / f"critic-rejected-prose-{artifact_index}.md").write_text(
            prose,
            encoding="utf-8",
        )
        (work_dir / f"critic-rejected-review-{artifact_index}.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        last_errors = [
            *review["issues"],
            review["reason"],
        ]
        feedback = merge_feedback(feedback, last_errors)
        write_failure_feedback(work_dir, feedback)
        previous_candidate = ""

    raise ProseGenerationError(
        [
            f"산문 생성 {MAX_PROSE_ATTEMPTS}회 실패: {scene_id}",
            *last_errors,
        ]
    )


def generate_all_prose(
    root: Path,
    llm: LLM,
    limit: int | None = None,
    check_scale: bool = True,
) -> list[Path]:
    results: list[Path] = []
    while limit is None or len(results) < limit:
        try:
            scene_id = select_scene(root.resolve(), None)
        except ProseGenerationError as exc:
            if "모든 장면" in str(exc):
                break
            raise
        results.append(
            generate_prose_scene(
                root,
                scene_id,
                llm,
                check_scale=check_scale,
            )
        )
    return results


def create_llm_client() -> LLM:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from llm import LLMClient

    return LLMClient()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="다음 정본 장면의 산문을 생성하고 critic 승인 뒤 승격한다."
    )
    parser.add_argument("scene_id", nargs="?")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--all",
        action="store_true",
        help="남은 장면을 순서대로 모두 생성",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="--all 실행에서 이번에 생성할 최대 장면 수",
    )
    args = parser.parse_args()

    try:
        if args.all:
            if args.scene_id:
                raise ProseGenerationError("--all과 scene_id를 함께 사용할 수 없음")
            if args.limit is not None and args.limit < 1:
                raise ProseGenerationError("--limit은 1 이상이어야 함")
            results = generate_all_prose(
                args.root,
                create_llm_client(),
                args.limit,
            )
            if not results:
                print("[OK] 생성할 미완료 장면 없음")
                return 0
            print(f"[OK] 산문 정본 {len(results)}개 승격 완료")
            return 0
        result = generate_prose_scene(args.root, args.scene_id, create_llm_client())
    except (ProseGenerationError, OSError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] 산문 정본 승격 완료: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
