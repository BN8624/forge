# 아이폰에서 시놉시스 후보를 검토하고 Forge 자동 완주를 시작하는 대시보드
from __future__ import annotations

import html
import json
import os
import secrets
import subprocess
import sys
import threading
import ctypes
from time import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(
            process_terminate,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 15)
    except OSError:
        return False
    return True


class DashboardError(Exception):
    pass


class DashboardController:
    def __init__(
        self,
        root: Path,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.root = root.resolve()
        self.popen_factory = popen_factory
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.state_path = self.root / "runs" / "dashboard" / "job.json"
        self.log_path = self.root / "runs" / "dashboard" / "job.log"
        self.token_path = self.root / "runs" / "dashboard" / "token.txt"
        self.token = self._load_token()

    def _load_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            return token
        token = secrets.token_urlsafe(24)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token + "\n", encoding="utf-8")
        return token

    def _current_job(self) -> dict[str, Any] | None:
        job = read_json_if_exists(self.state_path)
        if not job:
            return None
        if job.get("status") != "running":
            if job.get("kind") == "series" and job.get("status") == "failed":
                completion = read_json_if_exists(
                    self.root / "runs" / "complete-series" / "status.json"
                ) or {}
                completion_updated = str(completion.get("updated_at", ""))
                job_finished = str(
                    job.get("finished_at") or job.get("started_at") or ""
                )
                stage = completion.get("stage")
                if (
                    completion_updated
                    and completion_updated > job_finished
                    and stage in {
                        "volume_approval",
                        "volume_complete",
                        "stopped",
                        "prose",
                        "final_validation",
                        "complete",
                    }
                ):
                    job.pop("return_code", None)
                    job.update(
                        {
                            "status": (
                                "complete" if stage == "complete" else "stopped"
                            ),
                            "superseded_by_stage": stage,
                            "finished_at": completion_updated,
                        }
                    )
                    write_json(self.state_path, job)
            return job
        return_code: int | None = None
        if self.process is not None and self.process.pid == job.get("pid"):
            return_code = self.process.poll()
        elif not process_alive(int(job.get("pid", 0))):
            return_code = 0 if self._external_job_succeeded(job) else 1
        if return_code is None:
            return job
        completion = read_json_if_exists(
            self.root / "runs" / "complete-series" / "status.json"
        ) or {}
        completion_stage = completion.get("stage")
        if (
            job.get("kind") == "series"
            and return_code == 0
            and completion_stage
            in {"volume_approval", "volume_complete", "stopped"}
        ):
            status = "awaiting_approval"
        else:
            status = "complete" if return_code == 0 else "failed"
        job.update(
            {
                "status": status,
                "return_code": return_code,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_json(self.state_path, job)
        self.process = None
        return job

    def _external_job_succeeded(self, job: dict[str, Any]) -> bool:
        started_at = str(job.get("started_at", ""))
        if job.get("kind") == "concepts":
            review_path = (
                self.root
                / "runs"
                / "new-world"
                / "concept"
                / "synopsis-review.json"
            )
            if not review_path.exists():
                return False
            modified_at = datetime.fromtimestamp(
                review_path.stat().st_mtime,
                timezone.utc,
            ).isoformat()
            return modified_at >= started_at
        if job.get("kind") == "series":
            completion = read_json_if_exists(
                self.root / "runs" / "complete-series" / "status.json"
            ) or {}
            if (
                completion.get("stage")
                in {"volume_approval", "volume_complete", "stopped", "complete"}
                and str(completion.get("updated_at", "")) >= started_at
            ):
                return True
            active = read_json_if_exists(
                self.root / "runs" / "new-world" / "active.json"
            ) or {}
            return bool(
                active.get("status") == "complete"
                and str(active.get("completed_at", "")) >= started_at
            )
        return False

    def _start(self, kind: str, command: list[str]) -> dict[str, Any]:
        with self.lock:
            current = self._current_job()
            if current and current.get("status") == "running":
                raise DashboardError("이미 Forge 작업이 실행 중입니다.")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("w", encoding="utf-8") as log:
                process = self.popen_factory(
                    command,
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self.process = process
            job = {
                "kind": kind,
                "status": "running",
                "pid": process.pid,
                "command": command,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            write_json(self.state_path, job)
            return job

    def generate_concepts(
        self,
        instruction: str,
        volume_count: int | None = None,
    ) -> dict[str, Any]:
        if volume_count is not None and not 1 <= volume_count <= 10:
            raise DashboardError("권수는 1-10 사이여야 합니다.")
        command = [
            sys.executable,
            "pipeline/generate_synopses.py",
            "--candidates-only",
            "--output",
            str(self.root / "runs" / "new-world" / "concept"),
        ]
        instruction = instruction.strip()
        if instruction:
            instruction_path = self.root / "runs" / "dashboard" / "instruction.txt"
            instruction_path.parent.mkdir(parents=True, exist_ok=True)
            instruction_path.write_text(instruction + "\n", encoding="utf-8")
            command.extend(["--instruction-file", str(instruction_path)])
        if volume_count is not None:
            command.extend(["--volume-count", str(volume_count)])
        return self._start("concepts", command)

    def cancel_concepts(self) -> dict[str, Any]:
        with self.lock:
            job = self._current_job()
            if not job or job.get("status") != "running":
                raise DashboardError("취소할 후보 생성 작업이 없습니다.")
            if job.get("kind") != "concepts":
                raise DashboardError("작품 제작 작업은 후보 생성 취소로 중단할 수 없습니다.")
            pid = int(job.get("pid", 0))
            if self.process is not None and self.process.pid == pid:
                self.process.terminate()
            elif process_alive(pid) and not terminate_process(pid):
                raise DashboardError("후보 생성 프로세스를 종료하지 못했습니다.")
            job.update(
                {
                    "status": "cancelled",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            write_json(self.state_path, job)
            self.process = None
            return job

    def start_series(
        self,
        selected_id: str,
        volume_count: int | None = None,
    ) -> dict[str, Any]:
        (self.root / "STOP_AFTER_RUN").unlink(missing_ok=True)
        candidates = self._load_concept().get("candidates", [])
        if selected_id not in {
            candidate.get("id") for candidate in candidates if isinstance(candidate, dict)
        }:
            raise DashboardError(f"선택할 수 없는 후보입니다: {selected_id}")
        command = [
            sys.executable,
            "pipeline/complete_series.py",
            "--game-scenario",
            "--reuse-concept",
            "--selected-synopsis",
            selected_id,
            "--approve-short",
            "--replace-active",
        ]
        if volume_count is not None:
            if not 1 <= volume_count <= 10:
                raise DashboardError("권수는 1-10 사이여야 합니다.")
            command.extend(["--volume-count", str(volume_count)])
        return self._start("series", command)

    def resume_series(self) -> dict[str, Any]:
        active = read_json_if_exists(
            self.root / "runs" / "new-world" / "active.json"
        ) or {}
        if active.get("status") != "active":
            raise DashboardError("재개할 신규 작품이 없습니다.")
        (self.root / "STOP_AFTER_RUN").unlink(missing_ok=True)
        return self._start(
            "series",
            [sys.executable, "pipeline/complete_series.py", "--game-scenario"],
        )

    def _load_concept(self) -> dict[str, Any]:
        concept_root = self.root / "runs" / "new-world" / "concept"
        candidates = read_json_if_exists(
            concept_root / "synopsis-candidates.json"
        ) or {}
        review = read_json_if_exists(concept_root / "synopsis-review.json") or {}
        selected = read_json_if_exists(
            concept_root / "selected-synopsis.json"
        ) or {}
        selection = read_json_if_exists(
            concept_root / "concept-selection.json"
        ) or {}
        return {
            "candidates": candidates.get("candidates", []),
            "review": review,
            "selected": selected,
            "selection": selection,
        }

    def _ordered_scene_ids(self) -> list[str]:
        series = read_json_if_exists(self.root / "story" / "series.json") or {}
        scene_ids: list[str] = []
        for volume_id in series.get("volume_ids", []):
            volume = read_json_if_exists(
                self.root / "story" / "volumes" / f"{volume_id}.json"
            ) or {}
            for event_id in volume.get("event_ids", []):
                event = read_json_if_exists(
                    self.root / "story" / "events" / f"{event_id}.json"
                ) or {}
                scene_ids.extend(
                    scene_id
                    for scene_id in event.get("scene_ids", [])
                    if isinstance(scene_id, str)
                )
        return scene_ids

    def _approved_scene_count(self, scene_ids: list[str]) -> int:
        approved = 0
        for scene_id in scene_ids:
            directory = self.root / "prose" / "scenes" / scene_id
            if not (directory / "prose.md").is_file():
                continue
            review = read_json_if_exists(directory / "review.json") or {}
            if review.get("status") == "pass":
                approved += 1
        return approved

    def _progress(self, job: dict[str, Any] | None) -> dict[str, Any]:
        completion = read_json_if_exists(
            self.root / "runs" / "complete-series" / "status.json"
        ) or {}
        active = read_json_if_exists(
            self.root / "runs" / "new-world" / "active.json"
        ) or {}
        concept = self._load_concept()
        series = read_json_if_exists(self.root / "story" / "series.json") or {}
        stage = str(completion.get("stage", "idle"))
        scene_ids = self._ordered_scene_ids()
        total_scenes = len(scene_ids)
        approved_scenes = self._approved_scene_count(scene_ids)
        current_scene_id = completion.get("scene_id")
        current_scene_number = None
        if isinstance(current_scene_id, str) and current_scene_id in scene_ids:
            current_scene_number = scene_ids.index(current_scene_id) + 1
        stage_labels = {
            "idle": "대기 중",
            "starting": "자동 완주를 준비하는 중",
            "synopsis_selection": "선택한 기획을 확정하는 중",
            "world_generation": "세계관과 정본을 생성하는 중",
            "structure_candidate": "승인 권수의 기본 구조를 생성·검증하는 중",
            "structure_expansion": "권별 구조를 장편 규모로 확장·검증하는 중",
            "prose": "장면 산문을 생성하고 critic 검증하는 중",
            "volume_approval": "1~2권 추천을 사용자 승인 대기 중",
            "volume_complete": "한 권 완성 후 검토 대기 중",
            "final_validation": "전권 구조·산문·EPUB을 최종 검증하는 중",
            "complete": "전체 권 자동 완주 완료",
            "failed": "자동 완주 실패",
            "stopped": "현재 장면 뒤 중단됨",
        }
        phase_by_stage = {
            "starting": 1,
            "synopsis_selection": 1,
            "world_generation": 2,
            "structure_candidate": 3,
            "structure_expansion": 4,
            "prose": 5,
            "stopped": 5,
            "volume_complete": 5,
            "final_validation": 6,
            "complete": 7,
        }
        if stage in {"prose", "stopped", "volume_complete"} and total_scenes:
            percent = round(40 + (approved_scenes / total_scenes) * 50, 1)
        else:
            percent_by_stage = {
                "idle": 0,
                "starting": 1,
                "synopsis_selection": 3,
                "world_generation": 8,
                "structure_candidate": 18,
                "structure_expansion": 30,
                "final_validation": 95,
                "complete": 100,
            }
            percent = percent_by_stage.get(stage, 0)
        target_title = (
            active.get("title")
            or concept["selected"].get("title")
            or series.get("title")
        )
        recommended_volume_count = concept["selected"].get(
            "recommended_volume_count"
        )
        if recommended_volume_count is None:
            legacy_arc = concept["selected"].get("five_volume_arc")
            if isinstance(legacy_arc, list) and legacy_arc:
                recommended_volume_count = len(legacy_arc)
        approval_required = (
            concept["selection"].get("volume_approval") == "required"
        )
        approved_volume_count = None
        if not approval_required:
            approved_volume_count = (
                active.get("approved_volume_count")
                or concept["selected"].get("approved_volume_count")
                or len(series.get("volume_ids", []))
                or None
            )
        started_at = str((job or {}).get("started_at", ""))
        elapsed_seconds = None
        if started_at:
            try:
                finished_at = str((job or {}).get("finished_at", ""))
                end_timestamp = (
                    datetime.fromisoformat(finished_at).timestamp()
                    if finished_at and (job or {}).get("status") != "running"
                    else time()
                )
                elapsed_seconds = max(
                    0,
                    int(
                        end_timestamp
                        - datetime.fromisoformat(started_at).timestamp()
                    ),
                )
            except ValueError:
                pass
        return {
            "target_title": target_title,
            "stage": stage,
            "stage_label": stage_labels.get(stage, stage),
            "phase": phase_by_stage.get(stage),
            "phase_total": 7,
            "percent": percent,
            "total_scenes": total_scenes,
            "approved_scenes": approved_scenes,
            "current_scene_id": current_scene_id,
            "current_scene_number": current_scene_number,
            "current_volume": (
                current_scene_id[:2]
                if isinstance(current_scene_id, str)
                else None
            ),
            "attempt": completion.get("attempt"),
            "generated_this_run": completion.get("generated_this_run"),
            "completed_volume": completion.get("completed_volume"),
            "next_volume": completion.get("next_volume"),
            "recommended_volume_count": recommended_volume_count,
            "approved_volume_count": approved_volume_count,
            "updated_at": completion.get("updated_at"),
            "elapsed_seconds": elapsed_seconds,
        }

    def status(self) -> dict[str, Any]:
        concept = self._load_concept()
        series = read_json_if_exists(self.root / "story" / "series.json") or {}
        completion = read_json_if_exists(
            self.root / "runs" / "complete-series" / "status.json"
        ) or {}
        active_world = read_json_if_exists(
            self.root / "runs" / "new-world" / "active.json"
        ) or {}
        job = self._current_job()
        log_tail = ""
        if job and job.get("status") == "failed" and self.log_path.exists():
            log_tail = self.log_path.read_text(
                encoding="utf-8", errors="replace"
            )[-2000:]
        return {
            "job": job,
            "concept": concept,
            "current_series": {
                "title": series.get("title"),
                "volume_count": len(series.get("volume_ids", [])),
            },
            "completion": completion,
            "active_world": active_world,
            "progress": self._progress(job),
            "log_tail": log_tail,
        }


def render_dashboard(token: str) -> bytes:
    safe_token = html.escape(token, quote=True)
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Forge 게임 원작 대시보드</title>
<style>
:root{{--bg:#0d1117;--panel:#151b23;--line:#2a3441;--text:#eef3f8;--muted:#98a8b9;--accent:#f1b24a;--ok:#61d095;}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 80% 0,#253042 0,transparent 34%),var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;line-height:1.55}}
main{{width:min(100% - 28px,760px);margin:auto;padding:max(28px,env(safe-area-inset-top)) 0 max(70px,env(safe-area-inset-bottom))}}
header{{padding:20px 4px 28px}} .eyebrow{{color:var(--accent);font-size:.75rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase}}
h1{{font-size:clamp(2rem,10vw,4rem);line-height:1.02;margin:8px 0 14px;letter-spacing:-.05em}} h2{{margin:0 0 14px;font-size:1.3rem}} h3{{margin:0;font-size:1.2rem}}
.lead,.muted{{color:var(--muted)}} .panel,.card{{background:color-mix(in srgb,var(--panel) 92%,transparent);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:14px;box-shadow:0 18px 50px #0004}}
textarea,input[type=number]{{width:100%;background:#0b1016;color:var(--text);border:1px solid var(--line);border-radius:12px;padding:13px;font:inherit}} textarea{{min-height:92px;resize:vertical}} input[type=number]{{margin-top:10px}}
button,.link{{border:0;border-radius:12px;padding:13px 16px;font:inherit;font-weight:800;cursor:pointer;text-decoration:none;display:inline-flex;justify-content:center;align-items:center}}
button.primary{{background:var(--accent);color:#201500}} button.secondary,.link{{background:#243040;color:var(--text)}} button:disabled{{opacity:.42;cursor:not-allowed}}
.actions{{display:grid;grid-template-columns:1fr;gap:10px;margin-top:12px}} .status{{display:flex;gap:9px;align-items:center;margin:0 0 8px}} .dot{{width:9px;height:9px;border-radius:50%;background:var(--muted)}} .dot.running{{background:var(--accent);box-shadow:0 0 0 5px #f1b24a20}} .dot.complete{{background:var(--ok)}} .dot.failed{{background:#ff6b6b}}
.progress{{height:10px;background:#0b1016;border:1px solid var(--line);border-radius:999px;overflow:hidden;margin:14px 0}} .progress i{{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#ffdc8a);border-radius:inherit}} .metrics{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .metric{{background:#0b1016;border-radius:11px;padding:10px}} .metric b{{display:block;color:var(--muted);font-size:.7rem;margin-bottom:3px}} .metric span{{font-weight:800}}
.cards{{display:grid;gap:14px}} .card{{position:relative;margin:0}} .card.selected{{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 18px 50px #0005}}
.topline{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} .genre{{color:var(--accent);font-size:.8rem;font-weight:700}} .badge{{background:#f1b24a20;color:#ffd488;padding:4px 8px;border-radius:999px;font-size:.72rem;font-weight:800;white-space:nowrap}}
.facts{{display:grid;gap:9px;margin:16px 0}} .facts div{{border-left:2px solid var(--line);padding-left:10px}} .facts b{{display:block;color:var(--muted);font-size:.72rem;margin-bottom:2px}}
details{{border-top:1px solid var(--line);padding-top:12px}} summary{{font-weight:700;cursor:pointer}} ol{{padding-left:22px}} label.choice{{display:flex;gap:10px;align-items:center;margin-top:14px;font-weight:800}} input[type=radio]{{width:20px;height:20px;accent-color:var(--accent)}}
.empty{{text-align:center;padding:34px 12px;color:var(--muted)}} pre{{white-space:pre-wrap;word-break:break-word;color:#ffaaaa;font-size:.78rem}} nav{{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}}
@media(min-width:680px){{.actions{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body data-token="{safe_token}">
<main>
<header><div class="eyebrow">Forge Control Room</div><h1>게임이 될 소설을 고른다.</h1><p class="lead">Forge가 적정 권수를 판단합니다. 3권 이상은 자동 진행하고 1~2권만 승인을 기다리며, 한 번에 한 권씩 완성합니다.</p><nav><a class="link" href="/">현재 전권 서재</a></nav></header>
<section class="panel">
  <h2>1. 시놉시스 후보 만들기</h2>
  <p class="muted">비워 두면 장르와 게임 형식까지 Forge가 결정합니다. 마음에 들 때까지 후보 5개를 다시 만들 수 있으며 현재 작품은 그대로 유지됩니다.</p>
  <textarea id="instruction" placeholder="예. 전투보다 탐험과 관계 선택이 중심인 한국적 SF"></textarea>
  <input id="volume-count" type="number" min="1" max="10" placeholder="선택 사항. 직접 지정할 권수 1-10">
  <div class="actions"><button class="primary" id="generate">새 후보 5개 다시 만들기</button><button class="secondary" id="refresh">상태 새로고침</button></div>
</section>
<section class="panel" id="job"></section>
<section><h2>2. 후보 샘플 확인</h2><div class="cards" id="cards"><div class="panel empty">아직 생성된 후보가 없습니다.</div></div></section>
<section class="panel">
  <h2>3. 선택한 기획으로 시작</h2>
  <p class="muted">1~2권 추천을 승인하거나 후보를 바꿀 때 사용합니다. 첫 실행은 1권 EPUB 완성 뒤 멈춥니다.</p>
  <div class="actions"><button class="primary" id="start" disabled>선택 기획·권수 승인</button><button class="secondary" id="resume" disabled>다음 권 이어서 만들기</button></div>
</section>
<pre id="error"></pre>
</main>
<script>
const token=document.body.dataset.token; let state=null; let selectedConceptId=null; let conceptSignature=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function api(path,body){{const r=await fetch(path,{{method:body?'POST':'GET',headers:body?{{'Content-Type':'application/json','X-Forge-Token':token}}:{{}},body:body?JSON.stringify(body):undefined}});const data=await r.json();if(!r.ok)throw new Error(data.error||'요청 실패');return data}}
function duration(sec){{if(sec==null)return'-';const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60);return h?`${{h}}시간 ${{m}}분`:`${{m}}분`}}
function jobView(s){{const j=s.job||{{status:'idle'}};const p=s.progress||{{}};const label=j.status==='running'?(j.kind==='concepts'?'후보 5개를 생성하고 critic 평가하는 중':p.stage_label):j.status==='failed'?'작업 실패':j.status==='complete'?'최근 작업 완료':p.stage_label;const scene=p.current_scene_id?`${{p.current_scene_number||'?'}} / ${{p.total_scenes||'?'}} · ${{p.current_scene_id}}`:`${{p.approved_scenes||0}} / ${{p.total_scenes||0}} 승인`;const volumes=`${{p.recommended_volume_count?`${{p.recommended_volume_count}}권 추천`:'추천 전'}} · ${{p.approved_volume_count?`${{p.approved_volume_count}}권 확정`:'승인 대기'}}`;const nextVolume=p.current_volume||p.next_volume||'-';const volumeStep=p.stage==='stopped'?`중단됨 · 재개 시 ${{nextVolume}}`:p.completed_volume?`${{p.completed_volume}} 완료 · 다음 ${{p.next_volume||'없음'}}`:`${{nextVolume}} 진행`;const timeLabel=j.status==='running'?'실행 시간':'마지막 실행 시간';document.querySelector('#job').innerHTML=`<div class="status"><i class="dot ${{esc(j.status)}}"></i><strong>${{esc(label||'대기 중')}}</strong></div><h3>${{esc(p.target_title||s.current_series?.title||'대상 작품 없음')}}</h3><div class="progress"><i style="width:${{Number(p.percent||0)}}%"></i></div><div class="metrics"><div class="metric"><b>예상 전체 진행률</b><span>${{esc(p.percent??0)}}% · 단계 ${{esc(p.phase||'-')}}/${{esc(p.phase_total||7)}}</span></div><div class="metric"><b>권수 계약</b><span>${{esc(volumes)}}</span></div><div class="metric"><b>산문 승인</b><span>${{esc(p.approved_scenes||0)}} / ${{esc(p.total_scenes||0)}}</span></div><div class="metric"><b>권별 진행</b><span>${{esc(volumeStep)}}</span></div><div class="metric"><b>현재 장면</b><span>${{esc(scene)}}</span></div><div class="metric"><b>현재 권·시도</b><span>${{esc(p.current_volume||'-')}} · ${{esc(p.attempt||1)}}회</span></div><div class="metric"><b>${{esc(timeLabel)}}</b><span>${{esc(duration(p.elapsed_seconds))}}</span></div><div class="metric"><b>마지막 상태 갱신</b><span>${{esc(p.updated_at?new Date(p.updated_at).toLocaleTimeString('ko-KR'): '-')}}</span></div></div>`;document.querySelector('#error').textContent=s.log_tail||''}}
function cardsView(s){{const holder=document.querySelector('#cards');if(s.job?.status==='running'&&s.job?.kind==='concepts'){{holder.innerHTML='<div class="panel empty">기존 후보를 보존한 채 새 후보 5개와 critic 평가를 생성 중입니다. 완료되면 이 영역이 새 후보로 교체됩니다.</div>';return}}const list=s.concept?.candidates||[];const review=s.concept?.review||{{}};const recommended=review.selected_id;const signature=JSON.stringify(list.map(c=>[c.id,c.title]));if(signature!==conceptSignature){{conceptSignature=signature;selectedConceptId=null}}const selectedId=list.some(c=>c.id===selectedConceptId)?selectedConceptId:recommended;const scores=Object.fromEntries((review.evaluations||[]).map(x=>[x.id,x]));const countIndex=(s.job?.command||[]).indexOf('--volume-count');const fixedCount=countIndex>=0?Number(s.job.command[countIndex+1]):null;if(!list.length){{holder.innerHTML='<div class="panel empty">아직 생성된 후보가 없습니다.</div>';return}}holder.innerHTML=list.map(c=>{{const e=scores[c.id]||{{}};const arc=c.volume_arc||c.five_volume_arc||[];const count=c.recommended_volume_count||arc.length||s.current_series?.volume_count||5;const countLabel=fixedCount===count?`${{count}}권 지정`:`${{count}}권 추천`;const reason=c.volume_count_reason||'기존 기획의 권수 계약을 유지합니다.';return `<article class="card" data-id="${{esc(c.id)}}"><div class="topline"><div><div class="genre">${{esc(c.genre)}} · ${{esc(c.id)}} · ${{esc(countLabel)}}</div><h3>${{esc(c.title)}}</h3></div>${{c.id===recommended?'<span class="badge">FORGE 추천</span>':''}}</div><p>${{esc(c.logline)}}</p><div class="facts"><div><b>플레이어 역할</b>${{esc(c.player_role)}}</div><div><b>핵심 루프</b>${{esc(c.core_loop)}}</div><div><b>권수 근거</b>${{esc(reason)}}</div><div><b>선택 구조</b>${{esc(c.choice_structure)}}</div></div><details><summary>${{esc(count)}}권 전개와 critic 평가</summary><ol>${{arc.map(x=>`<li>${{esc(x)}}</li>`).join('')}}</ol><p><b>강점.</b> ${{esc((e.strengths||[]).join(' · '))}}</p><p><b>위험.</b> ${{esc((e.risks||[]).join(' · '))}}</p></details><label class="choice"><input type="radio" name="concept" value="${{esc(c.id)}}" ${{c.id===selectedId?'checked':''}}>이 기획 선택</label></article>`}}).join('');markSelection(false)}}
function markSelection(remember=true){{const chosen=document.querySelector('input[name=concept]:checked');if(remember&&chosen)selectedConceptId=chosen.value;const busy=state?.job?.status==='running';const hasActive=state?.active_world?.status==='active';document.querySelectorAll('.card').forEach(x=>x.classList.toggle('selected',chosen&&x.dataset.id===chosen.value));const start=document.querySelector('#start');const chosenTitle=chosen?.closest('.card')?.querySelector('h3')?.textContent;start.textContent=chosen?`${{chosen.value}} · ${{chosenTitle}} 시작`:'선택 기획·권수 승인';start.disabled=!chosen||busy;document.querySelector('#resume').disabled=busy||!hasActive}}
function render(s){{state=s;jobView(s);cardsView(s);const conceptBusy=s.job?.status==='running'&&s.job?.kind==='concepts';const otherBusy=s.job?.status==='running'&&!conceptBusy;const generate=document.querySelector('#generate');generate.disabled=otherBusy;generate.textContent=conceptBusy?'후보 생성 취소':'새 후보 5개 다시 만들기';markSelection()}}
async function refresh(){{try{{render(await api('/api/dashboard'))}}catch(e){{document.querySelector('#error').textContent=e.message}}}}
document.querySelector('#refresh').onclick=refresh;document.querySelector('#cards').onchange=()=>markSelection(true);
document.querySelector('#generate').onclick=async()=>{{const raw=document.querySelector('#volume-count').value;try{{if(state?.job?.status==='running'&&state?.job?.kind==='concepts'){{await api('/api/dashboard/concepts/cancel',{{}})}}else{{await api('/api/dashboard/concepts',{{instruction:document.querySelector('#instruction').value,volume_count:raw||null}})}}await refresh()}}catch(e){{alert(e.message)}}}};
document.querySelector('#start').onclick=async()=>{{const chosen=document.querySelector('input[name=concept]:checked');if(!chosen)return;const chosenTitle=chosen.closest('.card')?.querySelector('h3')?.textContent||'';const raw=document.querySelector('#volume-count').value;if(!confirm(`${{chosen.value}} · ${{chosenTitle}} 기획과 ${{raw||'Forge 추천'}} 권수를 승인할까요? 현재 작품은 백업됩니다.`))return;try{{await api('/api/dashboard/start',{{selected_id:chosen.value,volume_count:raw||null}});await refresh()}}catch(e){{alert(e.message)}}}};
document.querySelector('#resume').onclick=async()=>{{if(!confirm('중단된 신규 작품 생성을 이어서 실행할까요?'))return;try{{await api('/api/dashboard/resume',{{}});await refresh()}}catch(e){{alert(e.message)}}}};
refresh();setInterval(refresh,3000);
</script>
</body></html>"""
    return page.encode("utf-8")
