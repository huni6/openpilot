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
UPLOAD_PROGRESS_PUBLISH_INTERVAL = 0.5
UPLOAD_PROGRESS_PUBLISH_BYTES = 8 * 1024 * 1024
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


class UploadProgressTracker:
  def __init__(self, job: dict[str, Any] | None):
    self.job = job
    self.lock = threading.Lock()
    self.bytes_uploaded = 0
    self.bytes_total = 0
    self.files_uploaded = 0
    self.files_total = 0
    self.active_files: dict[str, dict[str, Any]] = {}
    self.last_publish_at = 0.0
    self.bytes_since_publish = 0

  def _publish_locked(self, force: bool = False) -> None:
    if not self.job:
      return
    now = time.monotonic()
    if (
      not force
      and now - self.last_publish_at < UPLOAD_PROGRESS_PUBLISH_INTERVAL
      and self.bytes_since_publish < UPLOAD_PROGRESS_PUBLISH_BYTES
    ):
      return
    self.job["bytes_uploaded"] = self.bytes_uploaded
    self.job["bytes_total"] = self.bytes_total
    self.job["files_uploaded"] = self.files_uploaded
    self.job["files_total"] = self.files_total
    self.job["active_files"] = {key: dict(value) for key, value in self.active_files.items()}
    self.last_publish_at = now
    self.bytes_since_publish = 0
    touch(self.job)

  def add_totals(self, byte_count: int, file_count: int) -> None:
    with self.lock:
      self.bytes_total += max(0, int(byte_count or 0))
      self.files_total += max(0, int(file_count or 0))
      self._publish_locked(force=True)

  def start_file(self, key: str, *, segment: str, name: str, size: int, mode: str) -> None:
    with self.lock:
      size = max(0, int(size or 0))
      self.active_files[key] = {
        "key": key,
        "segment": segment,
        "name": name,
        "size": size,
        "sent": 0,
        "percent": 0 if size > 0 else None,
        "mode": mode,
        "started_at": time.time(),
      }
      self._publish_locked(force=True)

  def add_bytes(self, key: str, count: int, *, sent: int | None = None) -> None:
    with self.lock:
      delta = max(0, int(count or 0))
      self.bytes_uploaded += delta
      self.bytes_since_publish += delta
      item = self.active_files.get(key)
      if item is not None:
        if sent is not None:
          item["sent"] = max(0, int(sent or 0))
        size = int(item.get("size") or 0)
        if size > 0:
          item["percent"] = max(0, min(100, round((int(item.get("sent") or 0) / size) * 100)))
      self._publish_locked()

  def finish_file(self, key: str) -> None:
    with self.lock:
      self.files_uploaded += 1
      self.active_files.pop(key, None)
      self._publish_locked(force=True)

  def clear_file(self, key: str) -> None:
    with self.lock:
      self.active_files.pop(key, None)
      self._publish_locked(force=True)

  def publish(self, force: bool = False) -> None:
    with self.lock:
      self._publish_locked(force=force)

  def counts(self) -> dict[str, int]:
    with self.lock:
      return {
        "bytesUploaded": self.bytes_uploaded,
        "bytesTotal": self.bytes_total,
        "filesUploaded": self.files_uploaded,
        "filesTotal": self.files_total,
      }


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
  payload = result or {"ok": bool(ok)}
  try:
    payload.setdefault("elapsedSeconds", max(0.0, time.time() - float(job.get("created_at") or time.time())))
  except Exception:
    pass
  job["status"] = status or ("done" if ok else "failed")
  job["result"] = payload
  job["error"] = error or (None if ok else payload.get("error"))
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
  tracker = UploadProgressTracker(job)

  async def upload_one(idx0: int, segment: str) -> None:
    nonlocal completed
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
        tracker.add_totals(segment_bytes, segment_files)
        active_key = f"ftp:{idx0}"

        def on_file_start(name: str, size: int) -> None:
          tracker.start_file(active_key, segment=segment, name=name, size=size, mode="ftp")

        def on_file_progress(name: str, sent: int, size: int, delta: int) -> None:
          tracker.add_bytes(active_key, delta, sent=sent)

        def on_file_done(name: str, size: int) -> None:
          tracker.finish_file(active_key)

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
        tracker.clear_file(f"ftp:{idx0}")
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
      tracker.publish(force=True)
      progress(job, message=f"Uploaded {completed}/{total}", current=completed, total=total)

  await asyncio.gather(*(upload_one(i, seg) for i, seg in enumerate(segments)))

  ensure_not_canceled(job)
  results = [r for r in results if r is not None]
  ok_count = sum(1 for item in results if item["ok"])
  uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  counts = tracker.counts()
  response_payload = {
    "ok": ok_count == len(results),
    "uploaded": ok_count,
    "total": len(results),
    **counts,
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
  remote_base_path = upload.logs_upload_path(f"routes/{directory}/")
  total = len(segments)
  results: list[Any] = [None] * total

  if job:
    job["upload_meta"] = meta
    job["remote_base_path"] = remote_base_path
    job["partial_results"] = []
    progress(job, message="Preparing Q upload", current=0, total=total, percent=0)

  ensure_not_canceled(job)

  try:
    concurrency = max(1, min(10, int(os.environ.get("CARROT_LOGS_UPLOAD_CONCURRENCY", "10") or "10")))
  except Exception:
    concurrency = 10
  sem = asyncio.Semaphore(concurrency)
  completed = 0
  tracker = UploadProgressTracker(job)
  timeout_seconds = upload.logs_upload_timeout_seconds()
  timeout = ClientTimeout(total=timeout_seconds, sock_connect=20, sock_read=timeout_seconds)
  debug_timing = upload.logs_upload_debug_enabled()

  async with ClientSession(timeout=timeout, auto_decompress=False) as session:
    async def upload_one(idx0: int, segment: str) -> None:
      nonlocal completed
      idx = idx0 + 1
      files: list[dict[str, Any]] = []
      async with sem:
        if is_cancel_requested(job):
          return
        if job:
          append(job, f"[{idx}/{total}] {segment} Q PUT")
        try:
          segment_path = segment_dir(segment)
          summary_files = await asyncio.to_thread(segment_file_summary, segment_path)
          manifest = [
            {
              **item,
              "path": os.path.join(segment_path, str(item.get("name") or "")),
            }
            for item in summary_files
            if item.get("name")
          ]
          tracker.add_totals(sum(int(item.get("size") or 0) for item in manifest), len(manifest))
          files = [
            {
              "name": item.get("name"),
              "size": item.get("size"),
              "sizeLabel": item.get("sizeLabel"),
            }
            for item in manifest
          ]
          async def upload_file(file_idx: int, item: dict[str, Any]) -> Exception | None:
            ensure_not_canceled(job)
            name = str(item.get("name") or "").strip()
            local_path = str(item.get("path") or "")
            if not name or not local_path:
              return None
            remote_file_path = upload.logs_upload_path(f"{directory}/{segment}/{name}")
            active_key = f"put:{idx0}:{file_idx}"
            file_size = int(item.get("size") or 0)
            file_sent = 0
            tracker.start_file(active_key, segment=segment, name=name, size=file_size, mode="logs_put")

            def note_bytes(count: int) -> None:
              nonlocal file_sent
              delta = max(0, int(count or 0))
              file_sent += delta
              tracker.add_bytes(active_key, delta, sent=file_sent)

            try:
              detail = await upload.put_file_to_logs_upload(
                local_path,
                remote_file_path,
                session,
                base_url=base_url,
                should_cancel=(lambda: is_cancel_requested(job)) if job else None,
                on_progress=note_bytes,
              )
              if 0 <= file_idx < len(files):
                files[file_idx]["uploadSeconds"] = detail.get("totalSeconds")
                files[file_idx]["putSeconds"] = detail.get("putSeconds")
                files[file_idx]["presignSeconds"] = detail.get("presignSeconds")
                files[file_idx]["chunks"] = detail.get("chunks")
              if debug_timing and job:
                total_s = max(0.001, float(detail.get("totalSeconds") or 0))
                put_s = max(0.001, float(detail.get("putSeconds") or 0))
                speed = upload.file_size_label(int(file_size / put_s))
                append(
                  job,
                  f"[{idx}/{total}] {segment}/{name} Q PUT {upload.file_size_label(file_size)} "
                  f"total={total_s:.2f}s presign={float(detail.get('presignSeconds') or 0):.2f}s "
                  f"put={put_s:.2f}s read={float(detail.get('readSeconds') or 0):.2f}s "
                  f"chunks={int(detail.get('chunks') or 0)} speed={speed}/s",
                )
            except Exception as exc:
              tracker.clear_file(active_key)
              return exc
            tracker.finish_file(active_key)
            return None

          file_errors = await asyncio.gather(
            *(upload_file(file_idx, item) for file_idx, item in enumerate(manifest)),
          )
          errors = [err for err in file_errors if err is not None]
          if errors:
            raise errors[0]
          results[idx0] = {
            "segment": segment,
            "route": route_name(segment),
            "segmentIndex": segment_index(segment),
            "ok": True,
            "uploadMode": "logs_put",
            "remotePath": upload.logs_upload_path(f"routes/{directory}/{segment}/"),
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
            "remotePath": upload.logs_upload_path(f"routes/{directory}/{segment}/"),
            "files": files,
            "error": str(e),
          }
          if job:
            append(job, f"[{idx}/{total}] {segment} Q PUT FAILED: {e}")
      completed += 1
      if job:
        job["partial_results"] = [r for r in results if r is not None]
        tracker.publish(force=True)
        progress(job, message=f"Q uploaded {completed}/{total}", current=completed, total=total)

    await asyncio.gather(*(upload_one(i, seg) for i, seg in enumerate(segments)))

  ensure_not_canceled(job)
  results = [r for r in results if r is not None]
  ok_count = sum(1 for item in results if item["ok"])
  uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  counts = tracker.counts()
  response_payload = {
    "ok": ok_count == len(results),
    "uploaded": ok_count,
    "total": len(results),
    **counts,
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
