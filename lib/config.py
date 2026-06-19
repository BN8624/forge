# Forge의 API 키와 모델 설정을 읽는 구성 모듈
""".env 파일에서 API 키를 읽는다. 외부 패키지 의존성 없음.

사용법:
    from config import get_api_key
    key = get_api_key()

직접 실행하면 키가 제대로 읽히는지 점검한다:
    python config.py
"""

import os
import re
import sys
from pathlib import Path

# Forge 자족 사본이며 프로젝트 루트는 lib/의 부모다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
# 종료 예약 플래그: 이 파일이 있으면 진행 중인 회차까지만 돌고 새 작업을 안 잡는다
# (대시보드 버튼이 만들고, orchestrator 자동재시도·배치 루프가 확인)
STOP_FILE = PROJECT_ROOT / "STOP_AFTER_RUN"
PLACEHOLDER = "your-api-key-here"

# 숨은 모델 폴백을 금지한다. 사용할 모델은 .env에 반드시 명시한다.
DEFAULT_MODELS = {
    "generator": "",
    "critic": "",
}


def force_utf8_stdout() -> None:
    """윈도우 콘솔 cp949 인코딩 사고 방지 + 줄 단위 버퍼링. 진입점에서 호출.

    line_buffering: 대시보드가 배치/오케스트레이터를 띄울 때 stdout이 로그
    파일로 향하는데, 기본 블록 버퍼링이면 프로세스가 비정상 종료할 때 로그가
    통째로 증발한다 (낮 배치 2회 무흔적 사망 실관측 — 빈 launch 로그).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
        except (AttributeError, ValueError):
            pass


def load_env(path: Path | None = None) -> bool:
    """KEY=VALUE 형식의 .env 파일을 읽어 환경변수로 넣는다.

    이미 설정된 환경변수는 덮어쓰지 않는다. 파일이 없으면 False.
    기본값은 호출시점에 ENV_PATH에서 읽는다(테스트 격리·import 바인딩 함정 방지).
    """
    path = ENV_PATH if path is None else path
    if not path.exists():
        return False
    # utf-8-sig: 윈도우 메모장이 붙이는 BOM까지 처리
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def get_api_keys() -> list[str]:
    """병렬용 API 키 풀을 반환한다(번호 순).

    GOOGLE_API_KEY_1..N 을 번호 순으로 모은다(AI Studio = 프로젝트당 키 1개,
    쿼터 독립 → 워커=키 병렬). 번호 키가 하나도 없으면 단일 GOOGLE_API_KEY로
    폴백(하위호환). 빈 값·플레이스홀더는 제외. 하나도 없으면 RuntimeError.
    """
    load_env()
    numbered: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        m = re.fullmatch(r"GOOGLE_API_KEY_(\d+)", name)
        if not m:
            continue
        value = value.strip()
        if value and value != PLACEHOLDER:
            numbered.append((int(m.group(1)), value))
    if numbered:
        numbered.sort()
        return [v for _, v in numbered]
    single = os.environ.get("GOOGLE_API_KEY", "").strip()
    if single and single != PLACEHOLDER:
        return [single]
    raise RuntimeError(
        "No API keys set. Copy .env.example to .env and put your API key(s) in "
        "GOOGLE_API_KEY_1..N (or the single GOOGLE_API_KEY)."
    )


def get_api_key() -> str:
    """단일 API 키를 반환한다(풀의 첫 키). 없으면 RuntimeError."""
    return get_api_keys()[0]


def get_model(role: str) -> str:
    """역할('generator'|'critic')에 해당하는 모델 ID를 반환한다."""
    if role not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {role!r} (use 'generator' or 'critic')")
    load_env()
    env_name = f"{role.upper()}_MODEL"
    model = os.environ.get(env_name, DEFAULT_MODELS[role]).strip()
    if not model:
        raise RuntimeError(
            f"{env_name} is required. Forge does not use an implicit model fallback."
        )
    return model


def main() -> int:
    if not ENV_PATH.exists():
        print("[ERROR] .env file not found.")
        print("        Copy .env.example to .env and put your API key in it.")
        return 1
    try:
        key = get_api_key()
    except RuntimeError:
        print("[ERROR] GOOGLE_API_KEY is missing or still the placeholder.")
        print("        Open .env and replace 'your-api-key-here' with your real key.")
        return 1
    masked = key[:4] + "..." + key[-4:] if len(key) >= 12 else "(too short?)"
    print(f"[OK] GOOGLE_API_KEY loaded: {masked} ({len(key)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
