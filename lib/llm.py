# Forge의 모델 API 호출과 키별 속도 제한을 담당하는 래퍼
"""API 콜 래퍼. 쿼터 4원칙 중 1·4를 여기서 강제한다.

- 순차 호출 (단일 스레드 전제, 동시 호출 없음)
- 슬라이딩 윈도우 RPM 리미터 (키별 60초 14건, RPM 15 보호)
- 429 백오프 재시도 (RPM/RPD 구분)
- 500/502/503/504 + 네트워크 에러 재시도
- 빈 응답 재시도
- 회차당 콜 수 상한 (가드레일)
"""

import re
import socket
import threading
import time
from collections import deque
from contextlib import contextmanager
from queue import Queue

from config import get_api_key, get_api_keys, get_model

# 슬라이딩 윈도우 RPM 리미터: 키별로 최근 WINDOW_SEC초 안에 보낸 요청 수를 세서,
# 상한(RPM_TARGET)에 차 있으면 가장 오래된 요청이 창을 빠질 때까지만 대기한다.
# 여유 있으면 대기 0(풀속도). 429를 *맞기 전에* 막고, 평상시엔 최대 허용속도로 돈다.
# RPM 상한 15에 여유 1을 둬 14로 잡는다(버스트·시계 지터 마진).
RPM_TARGET = 14          # 키당 60초 윈도우 허용 요청 수(상한 15 - 여유 1)
WINDOW_SEC = 60.0        # RPM 측정 윈도우
# 슬라이딩 윈도우만으론 같은 키 콜이 순간 분출(burst)할 수 있어 구글측 순간 RPM에
# 걸린다 → 키별 연속 콜 사이 최소 간격을 둬 분출을 평탄화한다(정적 4초 절벽의 1초 바닥판).
MIN_GAP_SEC = 1.0        # 같은 키 연속 콜 최소 간격

# 키마다 쿼터(RPM 15)가 독립이므로 페이서도 키별 락·윈도우로 나눈다. 같은 키의 콜만
# 직렬화·계수되고, 다른 키는 서로 안 막아 진짜 병렬로 겹친다.
# (단일키 모드면 키 1개짜리 페이서 = 전역 리미터와 동일 동작.)
_pacer_registry_lock = threading.Lock()  # _pacers dict 자체를 보호
_pacers: dict[str, dict] = {}             # api_key -> {"lock": Lock, "calls": deque}


def _get_pacer(api_key: str) -> dict:
    """api_key에 대응하는 페이서(락+최근콜 타임스탬프 윈도우)를 반환한다(없으면 생성)."""
    with _pacer_registry_lock:
        pacer = _pacers.get(api_key)
        if pacer is None:
            pacer = {"lock": threading.Lock(), "calls": deque()}
            _pacers[api_key] = pacer
        return pacer

# 오픈라우터 유료 단가 (USD / 1M tokens, 2026-06 기준). thinking은 출력으로 과금.
# 실제 사용은 AI Studio 무료지만, "유료였다면 얼마"를 REPORT에 환산 표기한다.
OPENROUTER_PRICES = {
    "generator": {"input": 0.06, "output": 0.33},   # gemma-4-26b-a4b
    "critic":    {"input": 0.12, "output": 0.36},   # gemma-4-31b
}
MAX_RETRIES = 4
BACKOFF_RPM_SEC = 20.0   # RPM 초과 지수백오프: 20→40→80→160s
BACKOFF_500_SEC = 5.0    # 서버 5xx 지수백오프: 5→10→20→40s
# 지수백오프 MAX_RETRIES회 뒤에도 429/5xx면 콜을 죽이지 않고 2분마다 무한 재시도한다.
# (키당 1콜/2분 = 720/일 < RPD 1500이라 쿼터 안전. 서버 폭풍·일시 RPM은 시간이 약.)
SLOW_RETRY_SEC = 120.0   # 백오프 소진 후 무한 재시도 간격
EMPTY_RETRIES = 2        # 빈 응답 재시도 상한
GENERATOR_MAX_OUTPUT_TOKENS = 32768


class AllKeysExhausted(Exception):
    """캠페인이 쓰는 모든 키×모델이 오늘 RPD 한도에 닿음(태평양 자정 후 자동 복귀)."""


class KeyPool:
    """API 키 풀(Queue). 워커가 키 하나를 체크아웃해 LLMClient에 바인딩하고
    끝나면 반납한다(워커=키). 키 N개면 동시 N워커만 키를 쥐고, 초과 워커는
    빈 키가 날 때까지 블록한다. 키 1개면 사실상 직렬(=단일키 모드).

    models를 주면 RPD 소진(결정23) 키를 스킵한다 — 캠페인이 쓰는 모델 중 하나라도
    오늘 한도에 닿은 키는 빌려주지 않고, 전부 소진이면 AllKeysExhausted.
    """

    def __init__(self, keys: list[str] | None = None,
                 models: list[str] | None = None):
        keys = keys if keys is not None else get_api_keys()
        if not keys:
            raise RuntimeError("KeyPool needs at least one API key")
        self._keys = list(keys)
        self._q: Queue = Queue()
        for k in keys:
            self._q.put(k)
        self.size = len(keys)
        self.models = list(models or [])

    def _exhausted(self, key: str) -> bool:
        if not self.models:
            return False
        import key_usage
        return any(key_usage.is_exhausted(key, m) for m in self.models)

    def _all_exhausted(self) -> bool:
        return bool(self.models) and all(self._exhausted(k) for k in self._keys)

    @contextmanager
    def checkout(self, timeout: float | None = None):
        """소진 안 된 키 하나를 빌린다(종료 시 자동 반납). 빈 키 없으면 블록,
        모든 키 소진이면 AllKeysExhausted."""
        while True:
            key = self._q.get(timeout=timeout)
            if not self._exhausted(key):
                break
            self._q.put(key)            # 소진 키는 큐 끝으로 되돌림(다음/다음날 재평가)
            if self._all_exhausted():
                raise AllKeysExhausted(
                    f"all {self.size} keys hit RPD for models {self.models}")
            time.sleep(0.2)             # 일부만 소진일 때 타이트 루프 완화
        try:
            yield key
        finally:
            self._q.put(key)


class CallBudgetExceeded(Exception):
    """회차당 API 콜 상한 초과."""


class ReplayExhausted(Exception):
    """재생할 녹음이 바닥남 (코드가 바뀌어 콜 순서가 달라졌을 때도 발생)."""


class ReplayLLM:
    """녹음(llm_calls.jsonl)을 역할별 순서대로 재생 (콜 0).

    실제 런에서 이상 동작이 났을 때, 같은 응답으로 orchestrator·게이트·rollback
    경로를 그대로 재현해 회귀를 잡는 용도. API 키·네트워크 불필요.
    """

    def __init__(self, record_path):
        import json
        from pathlib import Path

        self.queues: dict[str, list[str]] = {}
        for line in Path(record_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            self.queues.setdefault(entry["role"], []).append(entry["response"])
        self.call_count = 0
        self.max_calls = None
        self.record_path = None  # 재생 중에는 녹음하지 않는다
        self.tokens: dict[str, int] = {"input": 0, "output": 0, "thinking": 0}

    def generate(self, role: str, prompt: str, temperature: float | None = None) -> str:
        queue = self.queues.get(role)
        if not queue:
            raise ReplayExhausted(f"no recorded response left for role {role!r}")
        self.call_count += 1
        return queue.pop(0)


class DailyQuotaExceeded(Exception):
    """RPD(일일 한도) 초과 — 오늘은 더 이상 콜 불가."""


class EmptyResponse(Exception):
    """모델이 빈 응답을 반환."""


def _is_rate_limit(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 429:
        return True
    return "429" in str(err) or "RESOURCE_EXHAUSTED" in str(err)


def _is_daily_quota(err: Exception) -> bool:
    """RPD 초과 여부. 일일 한도는 백오프로 복구 불가."""
    msg = str(err).lower()
    daily_markers = (
        "daily", "per day", "daily_limit", "requests per day",
        "generatecontentrequestsperday", "rpd",
    )
    minute_markers = (
        "per minute", "inputtokenspermodelperminute",
        "input_token_count", "tokens/minute", "retrydelay",
    )
    return any(k in msg for k in daily_markers) and not any(
        k in msg for k in minute_markers)


def _is_transient(err: Exception) -> bool:
    """서버 에러(5xx) 또는 네트워크 에러."""
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int) and code >= 500:
        return True
    if bool(re.search(r"\b(500|502|503|504)\b", str(err))):
        return True
    # SDK가 소켓 에러를 자기 예외로 감싸면 isinstance에 안 걸린다 —
    # 메시지 패턴으로도 잡는다 (WinError 10054 즉사 실관측, 2026-06-12 배치)
    msg = str(err).lower()
    if any(k in msg for k in ("winerror 10054", "connection reset",
                              "connection aborted", "connection refused",
                              "timed out", "deadline exceeded")):
        return True
    return isinstance(err, (ConnectionError, TimeoutError, socket.error,
                            OSError))


class LLMClient:
    def __init__(self, max_calls: int | None = None,
                 api_key: str | None = None):
        # import을 여기서 해서, API를 안 쓰는 코드(게이트 등)는 SDK 없이도 돈다
        from google import genai

        # api_key를 주입하면 그 키에 바인딩(워커=키). 안 주면 풀의 첫 키(단일키 모드).
        self._api_key = api_key or get_api_key()
        self._client = genai.Client(api_key=self._api_key)
        self.call_count = 0
        self.max_calls = max_calls
        # 녹음: 경로를 지정하면 콜마다 응답 전문을 jsonl로 기록 (replay 재현용)
        self.record_path = None
        self.tokens: dict[str, int] = {"input": 0, "output": 0, "thinking": 0}
        self.tokens_by_role: dict[str, dict[str, int]] = {
            "generator": {"input": 0, "output": 0, "thinking": 0},
            "critic": {"input": 0, "output": 0, "thinking": 0},
        }

    def _record(self, role: str, model: str, prompt: str, response: str,
                tokens: dict | None = None, finish_reason: str | None = None) -> None:
        """콜 1건 녹음. 실패해도 콜을 막지 않는다.

        prompt는 크기·머리만 (다이어트 효과 측정용), response는 전문 (replay용).
        tokens(콜당 input/output/thinking)·finish_reason은 출력한도·분산성 잘림 관측용.
        """
        if not self.record_path:
            return
        try:
            import json
            from datetime import datetime
            entry = {"t": datetime.now().isoformat(timespec="seconds"),
                     "role": role, "model": model,
                     "prompt_chars": len(prompt), "prompt_head": prompt[:300],
                     "tokens": tokens, "finish_reason": finish_reason,
                     "response": response}
            with open(self.record_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def cost_usd(self) -> dict[str, float]:
        """오픈라우터 유료 단가 환산 비용(USD). 역할별 + 합계."""
        costs: dict[str, float] = {}
        for role, toks in self.tokens_by_role.items():
            price = OPENROUTER_PRICES.get(role)
            if not price:
                continue
            costs[role] = (toks["input"] * price["input"]
                           + (toks["output"] + toks["thinking"]) * price["output"]
                           ) / 1_000_000
        costs["total"] = sum(costs.values())
        return costs

    def _wait_interval(self) -> None:
        """이 클라이언트가 쥔 키의 슬라이딩 윈도우를 잡고, 최근 60초 요청이 상한
        (RPM_TARGET)에 차 있으면 가장 오래된 요청이 창을 빠질 때까지만 대기한다.
        여유 있으면 대기 0(풀속도). 키별 락이라 다른 키 워커는 안 막힌다."""
        pacer = _get_pacer(getattr(self, "_api_key", ""))
        with pacer["lock"]:
            win = pacer["calls"]
            now = time.monotonic()
            while win and win[0] <= now - WINDOW_SEC:  # 창 밖 요청 폐기
                win.popleft()
            if len(win) >= RPM_TARGET:                 # 상한 차면 한 슬롯 빌 때까지
                time.sleep(win[0] + WINDOW_SEC - now)
                now = time.monotonic()
                while win and win[0] <= now - WINDOW_SEC:
                    win.popleft()
            if win:                                    # 분출 평탄화: 직전 콜과 최소 간격
                gap = now - win[-1]
                if gap < MIN_GAP_SEC:
                    time.sleep(MIN_GAP_SEC - gap)
                    now = time.monotonic()
            win.append(now)

    def generate(self, role: str, prompt: str, temperature: float | None = None) -> str:
        """role('generator'|'critic')의 모델로 1콜. 텍스트를 반환한다.

        가용성 폴백(결정16): generator(손) 콜이 429/5xx 재시도 소진으로 죽으면 critic
        모델로 1회 강등 재시도한다. 폴백 대상이 generator와 같으면(31단독·26단독) no-op.
        """
        if self.max_calls is not None and self.call_count >= self.max_calls:
            raise CallBudgetExceeded(
                f"call budget exhausted ({self.call_count}/{self.max_calls})"
            )
        config = {}
        if temperature is not None:
            config["temperature"] = temperature
        if role == "generator":
            config["max_output_tokens"] = GENERATOR_MAX_OUTPUT_TOKENS
        model = get_model(role)
        try:
            return self._generate_with(role, model, prompt, config)
        except RuntimeError as err:  # 429/5xx 재시도 소진 (다른 에러는 그대로 전파)
            fallback = get_model("critic")
            if role != "generator" or fallback == model:
                raise  # 손 콜이 아니거나 강등 대상이 같음(31단독/26단독) → no-op
            print(f"[FALLBACK] {model} infra-exhausted -> demoting to "
                  f"{fallback} (1 try): {err}")
            return self._generate_with(role, fallback, prompt, config)

    def _generate_with(self, role: str, model: str, prompt: str,
                       config: dict) -> str:
        """주어진 model로 콜 1건을 재시도 루프로 돈다.

        429(RPM)·5xx는 지수백오프 MAX_RETRIES회 → 그 뒤 2분마다 무한 재시도(안 죽임).
        빈응답은 유한 재시도(EMPTY_RETRIES 소진 시 EmptyResponse).
        일일쿼터(RPD)·기타 예외(4xx 등)는 즉시 전파.
        """
        last_err: Exception | None = None
        empty_count = 0
        attempt = 0          # 429/5xx 지수백오프 지수(소진 후 2분 무한 재시도)
        while True:
            self._wait_interval()
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config or None,
                )
                self.call_count += 1
                # 이 키×모델의 오늘(태평양) RPD를 1 올린다 — 풀의 소진 판정 근거.
                try:
                    import key_usage
                    key_usage.record(getattr(self, "_api_key", ""), model)
                except Exception:  # noqa: BLE001 - 계측 실패가 콜을 막지 않게
                    pass
                usage = getattr(resp, "usage_metadata", None)
                call_tokens = {"input": 0, "output": 0, "thinking": 0}
                if usage:
                    by_role = self.tokens_by_role.setdefault(
                        role, {"input": 0, "output": 0, "thinking": 0})
                    for key, attr in (("input", "prompt_token_count"),
                                      ("output", "candidates_token_count"),
                                      ("thinking", "thoughts_token_count")):
                        n = getattr(usage, attr, 0) or 0
                        call_tokens[key] = n
                        self.tokens[key] += n
                        by_role[key] += n
                try:
                    finish_reason = str(resp.candidates[0].finish_reason)
                except Exception:  # noqa: BLE001
                    finish_reason = None
                text = getattr(resp, "text", None)
                if text and text.strip():
                    self._record(role, model, prompt, text, call_tokens,
                                 finish_reason)
                if not text or not text.strip():
                    empty_count += 1
                    if empty_count > EMPTY_RETRIES:
                        raise EmptyResponse(f"empty response from {model} "
                                            f"({empty_count} times)")
                    wait = 4.0 * empty_count
                    print(f"[WAIT] empty response, retrying in {wait:.0f}s "
                          f"(attempt {empty_count}/{EMPTY_RETRIES})")
                    time.sleep(wait)
                    continue
                return text
            except EmptyResponse:
                raise
            except Exception as err:  # noqa: BLE001 - SDK 예외 타입이 유동적
                last_err = err
                # 429(RPM)·5xx 둘 다: 지수백오프 MAX_RETRIES회 → 그 뒤 2분마다 무한
                # 재시도(절대 안 죽임). RPD 일일쿼터만 즉시 차단(2분 대기로 안 풀림 —
                # 풀이 다른 키로 넘긴다). 그 외 예외(4xx 등)는 그대로 전파.
                if _is_rate_limit(err):
                    if _is_daily_quota(err):
                        raise DailyQuotaExceeded(
                            f"daily quota exhausted for {model}: {err}"
                        ) from err
                    base, label = BACKOFF_RPM_SEC, "rate limited (429)"
                elif _is_transient(err):
                    base, label = BACKOFF_500_SEC, "server 5xx"
                else:
                    raise
                if attempt < MAX_RETRIES:
                    wait = base * (2 ** attempt)
                    attempt += 1
                    print(f"[WAIT] {label}, retrying in {wait:.0f}s "
                          f"(attempt {attempt}/{MAX_RETRIES})")
                else:
                    wait = SLOW_RETRY_SEC
                    print(f"[WAIT] {label}, slow retry in {wait:.0f}s "
                          f"(never gives up)")
                time.sleep(wait)
