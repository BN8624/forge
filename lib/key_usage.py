# Forge에서 키와 모델별 일일 요청 수를 집계하는 트래커
"""구글 AI Studio 무료 쿼터는 키×모델마다 RPD 1,500이고 **태평양 자정에 리셋**된다.
이 모듈은 콜 성공마다 (태평양날짜, 키지문, 모델) 카운트를 올려 runs/key_usage.json에
영속화하고, 한도 근접 키를 소진(exhausted)으로 판정해 풀이 스킵하게 한다.

- 자동 복귀: 카운트가 *태평양 날짜 버킷*이라 자정 지나면 새 버킷=0 → 별도 복구 불필요.
- 보안: API 키 원문은 디스크에 안 쓴다. sha256 앞 10자 지문으로만 식별.
- RPM(분당)은 llm.py의 키별 4초 페이서가 막으므로 여기선 RPD만 다룬다.
"""

import hashlib
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from config import PROJECT_ROOT

PACIFIC = ZoneInfo("America/Los_Angeles")
RPD_LIMIT = 1500    # 키×모델당 일일 요청 상한(무료 티어)
RPD_RESERVE = 50    # 안전 여유 — 이만큼 남기고 차단(경합·계측오차 대비)
USAGE_PATH = PROJECT_ROOT / "runs" / "key_usage.json"

_lock = threading.Lock()


def pacific_date() -> str:
    """현재 태평양 날짜(YYYY-MM-DD) = RPD 리셋 버킷."""
    return datetime.now(PACIFIC).date().isoformat()


def fingerprint(api_key: str) -> str:
    """키 지문(원문 대신 디스크에 저장). 충돌 무시할 만큼 짧고 고유."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:10]


def _bucket(date: str, api_key: str, model: str) -> str:
    return f"{date}|{fingerprint(api_key)}|{model}"


def _load() -> dict:
    if not USAGE_PATH.exists():
        return {}
    try:
        return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USAGE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(USAGE_PATH)  # 원자적 교체


def record(api_key: str, model: str, n: int = 1) -> int:
    """콜 1건(기본)을 (오늘, 키, 모델) 버킷에 더하고 영속화. 갱신된 카운트 반환.

    빈 키(실제 키에 바인딩 안 된 경우)는 추적 대상이 아니라 무시한다."""
    if not api_key:
        return 0
    with _lock:
        data = _load()
        key = _bucket(pacific_date(), api_key, model)
        data[key] = data.get(key, 0) + n
        _save(data)
        return data[key]


def count(api_key: str, model: str) -> int:
    """오늘(태평양) 이 키×모델의 누적 콜 수."""
    with _lock:
        return _load().get(_bucket(pacific_date(), api_key, model), 0)


def is_exhausted(api_key: str, model: str,
                 limit: int | None = None, reserve: int | None = None) -> bool:
    """오늘 이 키×모델이 (한도-여유) 이상이면 소진으로 본다 → 풀이 스킵.

    기본값은 호출시점에 모듈 상수에서 읽는다(테스트·런타임 조정 반영)."""
    limit = RPD_LIMIT if limit is None else limit
    reserve = RPD_RESERVE if reserve is None else reserve
    return count(api_key, model) >= (limit - reserve)


def report(date: str | None = None) -> str:
    """해당 날짜(기본 오늘 태평양)의 키×모델별 사용량 표 산문."""
    date = date or pacific_date()
    with _lock:
        data = _load()
    rows = []
    for key, n in sorted(data.items()):
        d, fp, model = key.split("|", 2)
        if d != date:
            continue
        cap = RPD_LIMIT - RPD_RESERVE
        status = "SOAKED" if n >= cap else f"{n}/{cap}"
        rows.append((fp, model, n, status))
    if not rows:
        return f"[USAGE] {date}: 기록 없음"
    lines = [f"[USAGE] {date} (태평양, 한도 {RPD_LIMIT}-여유 {RPD_RESERVE})"]
    for fp, model, n, status in rows:
        lines.append(f"  {fp}  {model:<22} {n:>5}  {status}")
    return "\n".join(lines)


def summary(date: str | None = None) -> dict:
    """대시보드용 구조화 요약. 해당 날짜(기본 오늘)의 키×모델 행 + 합계·최대."""
    date = date or pacific_date()
    with _lock:
        data = _load()
    rows = []
    for key, n in data.items():
        d, fp, model = key.split("|", 2)
        if d == date:
            rows.append({"fp": fp[:6], "model": model, "n": n})
    rows.sort(key=lambda r: -r["n"])
    return {"date": date, "cap": RPD_LIMIT - RPD_RESERVE, "rows": rows,
            "total": sum(r["n"] for r in rows),
            "max": rows[0]["n"] if rows else 0}


def main() -> int:
    from config import force_utf8_stdout
    force_utf8_stdout()
    print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
