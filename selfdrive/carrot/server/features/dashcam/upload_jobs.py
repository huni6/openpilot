from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ...services.params import HAS_PARAMS, Params
from . import upload
from .catalog import segment_file_summary
from .paths import route_name, segment_dir, segment_index


UPLOAD_JOB_KEEP_COUNT = 12
UPLOAD_JOB_MAX_LOG_CHARS = 60000
_jobs: dict[str, dict[str, Any]] = {}


class UploadCanceled(Exception):
  pass


def jobs() -> dict[str, dict[str, Any]]:
  return _jobs


def has_running_job() -> bool:
  return any(job.get("status") == "running" for job in _jobs.values())


def running_job() -> dict[str, Any] | None:
  for job in _jobs.values():
    if job.get("status") == "running":
      return job
  return None


def touch(job: dict[str, Any]) -> None:
  job["updated_at"] = time.time()


def append(job: dict[str, Any], text: Any) -> None:
  if text is None:
    return
  chunk = str(text).replace("\r\n", "\n").replace("\r", "\n")
  if not chunk:
    return
  cur = job.get("log") or ""
  if cur and not cur.endswith("\n") and not chunk.startswith("\n"):
    cur += "\n"
  job["log"] = (cur + chunk)[-UPLOAD_JOB_MAX_LOG_CHARS:]
  touch(job)


def progress(
  job: dict[str, Any],
  *,
  message: str | None = None,
  current: int | None = None,
  total: int | None = None,
  percent: int | None = None,
) -> None:
  if message is not None:
    job["message"] = str(message)
  if current is not None:
    job["step_current"] = max(0, int(current))
  if total is not None:
    job["step_total"] = max(0, int(total))
  if percent is None:
    c = job.get("step_current")
    t = job.get("step_total")
    if isinstance(c, int) and isinstance(t, int) and t > 0:
      percent = int(max(0, min(100, round((c / t) * 100))))
  job["progress"] = percent
  touch(job)


def is_cancel_requested(job: dict[str, Any] | None) -> bool:
  return bool(job and job.get("cancel_requested"))


def ensure_not_canceled(job: dict[str, Any] | None) -> None:
  if is_cancel_requested(job):
    raise UploadCanceled("upload canceled")


def start_active_file(
  job: dict[str, Any] | None,
  key: str,
  *,
  segment: str,
  name: str,
  size: int,
  mode: str,
) -> None:
  if not job:
    return
  active = job.setdefault("active_files", {})
  active[key] = {
    "key": key,
    "segment": segment,
    "name": name,
    "size": max(0, int(size or 0)),
    "sent": 0,
    "percent": 0 if int(size or 0) > 0 else None,
    "mode": mode,
    "started_at": time.time(),
  }
  touch(job)


def update_active_file(job: dict[str, Any] | None, key: str, *, sent: int | None = None) -> None:
  if not job:
    return
  active = job.get("active_files")
  if not isinstance(active, dict) or key not in active:
    return
  item = active[key]
  if sent is not None:
    item["sent"] = max(0, int(sent or 0))
  size = int(item.get("size") or 0)
  if size > 0:
    item["percent"] = max(0, min(100, round((int(item.get("sent") or 0) / size) * 100)))
  touch(job)


def finish_active_file(job: dict[str, Any] | None, key: str) -> None:
  if not job:
    return
  active = job.get("active_files")
  if isinstance(active, dict):
    active.pop(key, None)
    touch(job)


def cancel_job(job_id: str) -> dict[str, Any]:
  job = _jobs.get(job_id)
  if not job:
    return {"ok": False, "error": "job not found"}
  if job.get("status") in ("done", "failed", "canceled"):
    return {"ok": True, "already_done": True, **snapshot(job)}
  job["cancel_requested"] = True
  progress(job, message="Canceling upload")
  append(job, "Cancel requested")
  return {"ok": True, **snapshot(job)}


def snapshot(job: dict[str, Any]) -> dict[str, Any]:
  active = job.get("active_files") or {}
  active_files = list(active.values()) if isinstance(active, dict) else []
  active_files.sort(key=lambda item: (float(item.get("started_at") or 0), str(item.get("key") or "")))
  return {
    "ok": True,
    "id": job["id"],
    "action": job["action"],
    "status": job["status"],
    "done": job["status"] in ("done", "failed", "canceled"),
    "cancel_requested": bool(job.get("cancel_requested")),
    "log": job.get("log") or "",
    "progress": job.get("progress"),
    "message": job.get("message") or "",
    "step_current": job.get("step_current"),
    "step_total": job.get("step_total"),
    "bytes_uploaded": int(job.get("bytes_uploaded") or 0),
    "bytes_total": int(job.get("bytes_total") or 0),
    "files_uploaded": int(job.get("files_uploaded") or 0),
    "files_total": int(job.get("files_total") or 0),
    "active_files": active_files,
    "error": job.get("error"),
    "created_at": job.get("created_at"),
    "updated_at": job.get("updated_at"),
    "result": job.get("result"),
  }


def finish(
  job: dict[str, Any],
  *,
  ok: bool,
  result: dict[str, Any] | None = None,
  error: str | None = None,
  status: str | None = None,
) -> None:
  job["status"] = status or ("done" if ok else "failed")
  job["result"] = result or {"ok": bool(ok)}
  job["error"] = error or (None if ok else job["result"].get("error"))
  if ok:
    job["progress"] = 100
  touch(job)
  prune()


def prune() -> None:
  finished = [job for job in _jobs.values() if job.get("status") in ("done", "failed", "canceled")]
  if len(finished) <= UPLOAD_JOB_KEEP_COUNT:
    return
  finished.sort(key=lambda job: float(job.get("updated_at") or 0), reverse=True)
  for old in finished[UPLOAD_JOB_KEEP_COUNT:]:
    _jobs.pop(old["id"], None)


def create_job(segments: list[str], action: str = "dashcam_upload") -> dict[str, Any]:
  job_id = uuid.uuid4().hex[:12]
  now = time.time()
  job = {
    "id": job_id,
    "action": action,
    "segments": list(segments),
    "status": "running",
    "log": "",
    "progress": 0,
    "message": "",
    "step_current": 0,
    "step_total": len(segments),
    "error": None,
    "result": None,
    "cancel_requested": False,
    "active_files": {},
    "created_at": now,
    "updated_at": now,
  }
  _jobs[job_id] = job
  prune()
  return job


async def run_upload_segments(segments: list[str], job: dict[str, Any] | None = None) -> dict[str, Any]:
  params = Params() if HAS_PARAMS else None
  meta = upload.upload_metadata(params)
  car_selected = meta.get("carName") or "none"
  dongle_id = meta.get("dongleId") or "unknown"
  directory = f"{car_selected} {dongle_id}".strip()
  remote_base_path = f"routes/{directory}/".replace("\\", "/")
  total = len(segments)
  results: list[Any] = [None] * total  # filled by index so order matches input

  if job:
    job["upload_meta"] = meta
    job["remote_base_path"] = remote_base_path
    job["partial_results"] = []
    progress(job, message="Preparing upload", current=0, total=total, percent=0)

  ensure_not_canceled(job)

  # Upload segments in parallel with bounded concurrency. Each
  # upload_folder_to_ftp() opens its own FTP connection, so concurrent calls
  # are safe. Concurrency is kept small because the NAS is shared across all
  # users; tune with CARROT_FTP_CONCURRENCY (default 3).
  try:
    concurrency = max(1, min(6, int(os.environ.get("CARROT_FTP_CONCURRENCY", "3") or "3")))
  except Exception:
    concurrency = 3
  sem = asyncio.Semaphore(concurrency)
  completed = 0
  bytes_uploaded = 0
  bytes_total = 0
  files_uploaded = 0
  files_total = 0
  counter_lock = threading.Lock()

  async def upload_one(idx0: int, segment: str) -> None:
    nonlocal completed, bytes_uploaded, bytes_total, files_uploaded, files_total
    idx = idx0 + 1
    files: list[Any] = []
    async with sem:
      if is_cancel_requested(job):
        return
      if job:
        append(job, f"[{idx}/{total}] {segment}")
      try:
        segment_path = segment_dir(segment)
        files = await asyncio.to_thread(segment_file_summary, segment_path)
        manifest = await asyncio.to_thread(upload.segment_upload_files, segment_path)
        segment_bytes = sum(int(item.get("size") or 0) for item in manifest)
        segment_files = len(manifest)
        bytes_total += segment_bytes
        files_total += segment_files
        if job:
          job["bytes_total"] = bytes_total
          job["files_total"] = files_total
          touch(job)
        active_key = f"ftp:{idx0}"

        def on_file_start(name: str, size: int) -> None:
          start_active_file(job, active_key, segment=segment, name=name, size=size, mode="ftp")

        def on_file_progress(name: str, sent: int, size: int, delta: int) -> None:
          nonlocal bytes_uploaded
          with counter_lock:
            bytes_uploaded += max(0, int(delta or 0))
            current_bytes_uploaded = bytes_uploaded
          if job:
            job["bytes_uploaded"] = current_bytes_uploaded
            update_active_file(job, active_key, sent=sent)

        def on_file_done(name: str, size: int) -> None:
          nonlocal files_uploaded
          with counter_lock:
            files_uploaded += 1
            current_files_uploaded = files_uploaded
          if job:
            job["files_uploaded"] = current_files_uploaded
            finish_active_file(job, active_key)

        ok = await asyncio.to_thread(
          upload.upload_folder_to_ftp,
          segment_path,
          directory,
          segment,
          (lambda: is_cancel_requested(job)) if job else None,
          on_file_start if job else None,
          on_file_progress if job else None,
          on_file_done if job else None,
        )
        results[idx0] = {
          "segment": segment,
          "route": route_name(segment),
          "segmentIndex": segment_index(segment),
          "ok": bool(ok),
          "remotePath": f"{remote_base_path}{segment}",
          "files": files,
        }
        if job:
          append(job, f"[{idx}/{total}] {segment} OK")
      except Exception as e:
        finish_active_file(job, f"ftp:{idx0}")
        if is_cancel_requested(job):
          return  # canceled mid-upload — handled after gather
        results[idx0] = {
          "segment": segment,
          "route": route_name(segment),
          "segmentIndex": segment_index(segment),
          "ok": False,
          "remotePath": f"{remote_base_path}{segment}",
          "files": files,
          "error": str(e),
        }
        if job:
          append(job, f"[{idx}/{total}] {segment} FAILED: {e}")
    # post-upload bookkeeping runs synchronously (atomic between awaits)
    completed += 1
    if job:
      job["partial_results"] = [r for r in results if r is not None]
      job["bytes_uploaded"] = bytes_uploaded
      job["bytes_total"] = bytes_total
      job["files_uploaded"] = files_uploaded
      job["files_total"] = files_total
      progress(job, message=f"Uploaded {completed}/{total}", current=completed, total=total)

  await asyncio.gather(*(upload_one(i, seg) for i, seg in enumerate(segments)))

  ensure_not_canceled(job)
  results = [r for r in results if r is not None]
  ok_count = sum(1 for item in results if item["ok"])
  uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  response_payload = {
    "ok": ok_count == len(results),
    "uploaded": ok_count,
    "total": len(results),
    "filesUploaded": files_uploaded,
    "filesTotal": files_total,
    "bytesUploaded": bytes_uploaded,
    "bytesTotal": bytes_total,
    "uploadedAt": uploaded_at,
    "remoteBasePath": remote_base_path,
    "meta": meta,
    "results": results,
    "message": f"{ok_count}/{len(results)} uploaded",
  }
  response_payload["shareText"] = upload.upload_share_text(response_payload)

  if job:
    progress(job, message="Sending notification", current=total, total=total, percent=98)
  ensure_not_canceled(job)
  response_payload["discord"] = await upload.send_discord_webhook(
    upload.discord_webhook_url(params),
    response_payload,
  )
  return response_payload


async def run_logs_put_upload_segments(segments: list[str], job: dict[str, Any] | None = None) -> dict[str, Any]:
  params = Params() if HAS_PARAMS else None
  meta = upload.upload_metadata(params)
  car_selected = meta.get("carName") or "none"
  dongle_id = meta.get("dongleId") or "unknown"
  directory = f"{car_selected} {dongle_id}".strip()
  base_url = upload.logs_upload_url()
  remote_base_path = upload.logs_upload_url_for_path(f"{directory}/".replace("\\", "/"), base_url)
  total = len(segments)
  results: list[Any] = [None] * total

  if job:
    job["upload_meta"] = meta
    job["remote_base_path"] = remote_base_path
    job["partial_results"] = []
    progress(job, message="Preparing Q upload", current=0, total=total, percent=0)

  ensure_not_canceled(job)

  try:
    concurrency = max(1, min(6, int(os.environ.get("CARROT_LOGS_UPLOAD_CONCURRENCY", "3") or "3")))
  except Exception:
    concurrency = 3
  sem = asyncio.Semaphore(concurrency)
  completed = 0
  files_uploaded = 0
  files_total = 0
  bytes_uploaded = 0
  bytes_total = 0
  timeout_seconds = upload.logs_upload_timeout_seconds()
  timeout = ClientTimeout(total=timeout_seconds, sock_connect=20, sock_read=timeout_seconds)

  async with ClientSession(timeout=timeout) as session:
    async def upload_one(idx0: int, segment: str) -> None:
      nonlocal completed, files_uploaded, files_total, bytes_uploaded, bytes_total
      idx = idx0 + 1
      files: list[dict[str, Any]] = []
      async with sem:
        if is_cancel_requested(job):
          return
        if job:
          append(job, f"[{idx}/{total}] {segment} Q PUT")
        try:
          segment_path = segment_dir(segment)
          manifest = await asyncio.to_thread(upload.segment_upload_files, segment_path)
          files_total += len(manifest)
          bytes_total += sum(int(item.get("size") or 0) for item in manifest)
          if job:
            job["files_total"] = files_total
            job["bytes_total"] = bytes_total
            touch(job)
          files = [
            {
              "name": item.get("name"),
              "size": item.get("size"),
              "sizeLabel": item.get("sizeLabel"),
            }
            for item in manifest
          ]
          for item in manifest:
            ensure_not_canceled(job)
            name = str(item.get("name") or "").strip()
            local_path = str(item.get("path") or "")
            if not name or not local_path:
              continue
            remote_file_path = f"{directory}/{segment}/{name}".replace("\\", "/")
            active_key = f"put:{idx0}"
            file_size = int(item.get("size") or 0)
            file_sent = 0
            start_active_file(job, active_key, segment=segment, name=name, size=file_size, mode="logs_put")

            def note_bytes(count: int) -> None:
              nonlocal bytes_uploaded, file_sent
              delta = max(0, int(count or 0))
              bytes_uploaded += delta
              file_sent += delta
              if job:
                job["bytes_uploaded"] = bytes_uploaded
                update_active_file(job, active_key, sent=file_sent)
                touch(job)

            try:
              await upload.put_file_to_logs_upload(
                local_path,
                remote_file_path,
                session,
                base_url=base_url,
                should_cancel=(lambda: is_cancel_requested(job)) if job else None,
                on_progress=note_bytes,
              )
            finally:
              finish_active_file(job, active_key)
            files_uploaded += 1
            if job:
              job["files_uploaded"] = files_uploaded
              touch(job)
          results[idx0] = {
            "segment": segment,
            "route": route_name(segment),
            "segmentIndex": segment_index(segment),
            "ok": True,
            "uploadMode": "logs_put",
            "remotePath": upload.logs_upload_url_for_path(f"{directory}/{segment}/".replace("\\", "/"), base_url),
            "files": files,
          }
          if job:
            append(job, f"[{idx}/{total}] {segment} Q PUT OK ({len(files)} files)")
        except Exception as e:
          if is_cancel_requested(job):
            return
          results[idx0] = {
            "segment": segment,
            "route": route_name(segment),
            "segmentIndex": segment_index(segment),
            "ok": False,
            "uploadMode": "logs_put",
            "remotePath": upload.logs_upload_url_for_path(f"{directory}/{segment}/".replace("\\", "/"), base_url),
            "files": files,
            "error": str(e),
          }
          if job:
            append(job, f"[{idx}/{total}] {segment} Q PUT FAILED: {e}")
      completed += 1
      if job:
        job["partial_results"] = [r for r in results if r is not None]
        job["files_uploaded"] = files_uploaded
        job["files_total"] = files_total
        job["bytes_uploaded"] = bytes_uploaded
        job["bytes_total"] = bytes_total
        progress(job, message=f"Q uploaded {completed}/{total}", current=completed, total=total)

    await asyncio.gather(*(upload_one(i, seg) for i, seg in enumerate(segments)))

  ensure_not_canceled(job)
  results = [r for r in results if r is not None]
  ok_count = sum(1 for item in results if item["ok"])
  uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  response_payload = {
    "ok": ok_count == len(results),
    "uploaded": ok_count,
    "total": len(results),
    "filesUploaded": files_uploaded,
    "filesTotal": files_total,
    "bytesUploaded": bytes_uploaded,
    "bytesTotal": bytes_total,
    "uploadedAt": uploaded_at,
    "uploadMode": "logs_put",
    "uploadUrl": base_url,
    "remoteBasePath": remote_base_path,
    "meta": meta,
    "results": results,
    "message": f"{ok_count}/{len(results)} uploaded",
  }
  response_payload["shareText"] = upload.upload_share_text(response_payload)
  return response_payload


async def run_job(job: dict[str, Any]) -> None:
  try:
    result = await run_upload_segments(list(job.get("segments") or []), job)
    finish(job, ok=bool(result.get("ok")), result=result)
  except UploadCanceled as exc:
    results = list(job.get("partial_results") or [])
    uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok_count = sum(1 for item in results if item.get("ok"))
    total = len(job.get("segments") or [])
    result = {
      "ok": False,
      "canceled": True,
      "uploaded": ok_count,
      "total": total,
      "filesUploaded": int(job.get("files_uploaded") or 0),
      "filesTotal": int(job.get("files_total") or 0),
      "bytesUploaded": int(job.get("bytes_uploaded") or 0),
      "bytesTotal": int(job.get("bytes_total") or 0),
      "uploadedAt": uploaded_at,
      "remoteBasePath": job.get("remote_base_path") or "",
      "meta": job.get("upload_meta") or {},
      "results": results,
      "message": f"Canceled {ok_count}/{total}",
      "error": str(exc),
    }
    result["shareText"] = upload.upload_share_text(result)
    append(job, "CANCELED")
    progress(job, message="Upload canceled", percent=0)
    finish(job, ok=False, result=result, error=str(exc), status="canceled")
  except Exception as exc:
    result = {"ok": False, "error": str(exc)}
    append(job, f"FAILED: {exc}")
    finish(job, ok=False, result=result, error=str(exc))


async def run_logs_put_job(job: dict[str, Any]) -> None:
  try:
    result = await run_logs_put_upload_segments(list(job.get("segments") or []), job)
    finish(job, ok=bool(result.get("ok")), result=result)
  except UploadCanceled as exc:
    results = list(job.get("partial_results") or [])
    uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok_count = sum(1 for item in results if item.get("ok"))
    total = len(job.get("segments") or [])
    result = {
      "ok": False,
      "canceled": True,
      "uploaded": ok_count,
      "total": total,
      "filesUploaded": int(job.get("files_uploaded") or 0),
      "filesTotal": int(job.get("files_total") or 0),
      "bytesUploaded": int(job.get("bytes_uploaded") or 0),
      "bytesTotal": int(job.get("bytes_total") or 0),
      "uploadedAt": uploaded_at,
      "uploadMode": "logs_put",
      "remoteBasePath": job.get("remote_base_path") or "",
      "meta": job.get("upload_meta") or {},
      "results": results,
      "message": f"Canceled {ok_count}/{total}",
      "error": str(exc),
    }
    result["shareText"] = upload.upload_share_text(result)
    append(job, "CANCELED")
    progress(job, message="Q upload canceled", percent=0)
    finish(job, ok=False, result=result, error=str(exc), status="canceled")
  except Exception as exc:
    result = {"ok": False, "error": str(exc)}
    append(job, f"FAILED: {exc}")
    finish(job, ok=False, result=result, error=str(exc))
