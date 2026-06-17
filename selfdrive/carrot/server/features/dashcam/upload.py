import asyncio
import base64
import json
import os
import subprocess
import time
from ftplib import FTP
from typing import Any, Callable
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout

from openpilot.system.hardware import HARDWARE

from ...config import DASHCAM_DEFAULT_DISCORD_KEY, DASHCAM_DEFAULT_DISCORD_WEBHOOK
from .paths import file_size_label


LOGS_UPLOAD_URL_DEFAULT = "https://logs.carrotpilot.app/upload/routes"
LOGS_UPLOAD_CONTENT_TYPE_DEFAULT = "application/octet-stream"
LOGS_UPLOAD_MAX_FILE_SIZE = 40 * 1024 * 1024
LOGS_UPLOAD_ACCEPT_ENCODING = "identity"
LOGS_UPLOAD_CHUNK_SIZE_DEFAULT = 4 * 1024 * 1024


def param_text(params: Any, key: str, default: str = "unknown") -> str:
  try:
    if not params:
      return default
    value = params.get(key)
    if isinstance(value, bytes):
      value = value.decode("utf-8", errors="replace")
    value = str(value or "").strip()
    return value or default
  except Exception:
    return default


def repo_dir() -> str:
  return os.environ.get("CARROT_REPO_DIR", "/data/openpilot")


def git_text(args: list[str], default: str = "") -> str:
  try:
    result = subprocess.run(
      ["git", *args],
      cwd=repo_dir(),
      capture_output=True,
      text=True,
      timeout=4,
    )
    if result.returncode == 0:
      value = (result.stdout or "").strip()
      return value or default
  except Exception:
    pass
  return default


def device_serial(params: Any) -> str:
  for key in ("HardwareSerial", "DeviceSerial", "Serial", "CarrotSerial"):
    value = param_text(params, key, "")
    if value:
      return value
  for env_key in ("CARROT_DEVICE_SERIAL", "DEVICE_SERIAL", "SERIAL"):
    value = os.environ.get(env_key, "").strip()
    if value:
      return value
  try:
    getter = getattr(HARDWARE, "get_serial", None)
    if callable(getter):
      value = str(getter() or "").strip()
      if value:
        return value
  except Exception:
    pass
  return "unknown"


def upload_metadata(params: Any) -> dict[str, str]:
  return {
    "carName": param_text(params, "CarName", "none"),
    "dongleId": param_text(params, "DongleId", "unknown"),
    "serial": device_serial(params),
    "branch": git_text(["branch", "--show-current"], "unknown"),
    "commit": git_text(["rev-parse", "--short", "HEAD"], "unknown"),
    "commitDate": git_text(["show", "-s", "--date=format:%Y-%m-%d %H:%M:%S", "--format=%cd", "HEAD"], "unknown"),
  }


def decode_obfuscated(value: str, key: str) -> str:
  try:
    token = str(value or "").strip()
    key_bytes = str(key or "").encode("utf-8")
    if not token or not key_bytes:
      return ""
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    decoded = bytes(raw[i] ^ key_bytes[i % len(key_bytes)] for i in range(len(raw)))
    return decoded.decode("utf-8", errors="ignore").strip()
  except Exception:
    return ""


def discord_webhook_url(params: Any) -> str:
  for key in ("CARROT_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"):
    value = os.environ.get(key, "").strip()
    if value:
      return value
  for key in ("CarrotDiscordWebhookUrl", "CarrotDiscordWebhookURL", "DiscordWebhookUrl", "DiscordWebhookURL"):
    value = param_text(params, key, "")
    if value:
      return value
  if os.environ.get("CARROT_DISCORD_WEBHOOK_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}:
    return ""
  return decode_obfuscated(DASHCAM_DEFAULT_DISCORD_WEBHOOK, DASHCAM_DEFAULT_DISCORD_KEY)


def upload_message_lines(payload: dict[str, Any], max_results: int | None = None) -> list[str]:
  meta = payload.get("meta") or {}
  commit = str(meta.get("commit") or "").strip()
  commit_date = meta.get("commitDate") or "unknown"
  commit_text = (
    f"[{commit}](https://github.com/ajouatom/openpilot/commit/{commit})"
    if commit and commit != "unknown"
    else "unknown"
  )
  uploaded = [item for item in payload.get("results") or [] if item.get("ok")]
  failed = [item for item in payload.get("results") or [] if not item.get("ok")]
  lines = [
    "# Carrot Dashcam Upload",
    "### Upload",
    f"- Time: {payload.get('uploadedAt') or ''}",
    f"- Path: {payload.get('remoteBasePath') or ''}",
    "### Device",
    f"- Car name: {meta.get('carName') or 'none'}",
    f"- DongleId: {meta.get('dongleId') or 'unknown'}",
    f"- Serial: {meta.get('serial') or 'unknown'}",
    f"- Branch: {meta.get('branch') or 'unknown'}",
    f"- Commit: {commit_text} ({commit_date})",
    "### Result",
  ]

  result_items = uploaded + failed
  visible_items = result_items if max_results is None else result_items[:max_results]
  for item in visible_items:
    if item.get("ok"):
      lines.append(f"- {item.get('segment')} OK")
    else:
      error = str(item.get("error") or "").strip()
      suffix = f": {error}" if error else ""
      lines.append(f"- {item.get('segment')} FAILED{suffix}")

  hidden_count = len(result_items) - len(visible_items)
  if hidden_count > 0:
    lines.append(f"- ... +{hidden_count} more")
  if not result_items:
    lines.append("- none")
  return lines


def upload_share_text(payload: dict[str, Any]) -> str:
  return "\n".join(upload_message_lines(payload)).strip()


def discord_content(payload: dict[str, Any]) -> str:
  content = "\n".join(upload_message_lines(payload, max_results=24)).strip()
  if len(content) <= 1900:
    return content

  content = "\n".join(upload_message_lines(payload, max_results=10)).strip()
  if len(content) <= 1900:
    return content

  return "\n".join(upload_message_lines(payload, max_results=3)).strip()[:1900]


async def send_discord_webhook(url: str, payload: dict[str, Any]) -> dict[str, Any]:
  url = (url or "").strip()
  if not url:
    return {"configured": False, "ok": False, "skipped": True}
  if not url.startswith(("http://", "https://")):
    return {"configured": True, "ok": False, "error": "invalid webhook url"}
  body = {
    "username": "Carrot Dashcam",
    "content": discord_content(payload),
    "allowed_mentions": {"parse": []},
    "flags": 4,
  }
  try:
    timeout = ClientTimeout(total=12)
    async with ClientSession(timeout=timeout) as session:
      async with session.post(url, json=body) as resp:
        text = await resp.text()
        if 200 <= resp.status < 300:
          return {"configured": True, "ok": True, "status": resp.status}
        return {"configured": True, "ok": False, "status": resp.status, "error": text[:500]}
  except Exception as e:
    return {"configured": True, "ok": False, "error": str(e)}


def segment_upload_files(local_folder: str) -> list[dict[str, Any]]:
  out: list[dict[str, Any]] = []
  for root, dirnames, files in os.walk(local_folder):
    dirnames.sort()
    for filename in sorted(files):
      local_path = os.path.join(root, filename)
      if not os.path.isfile(local_path):
        continue
      rel_path = os.path.relpath(local_path, local_folder).replace(os.sep, "/")
      try:
        size = os.path.getsize(local_path)
      except OSError:
        size = 0
      out.append({
        "name": rel_path,
        "path": local_path,
        "size": size,
        "sizeLabel": file_size_label(size),
      })
  return out


def logs_upload_url() -> str:
  return (os.environ.get("CARROT_LOGS_UPLOAD_URL", LOGS_UPLOAD_URL_DEFAULT) or LOGS_UPLOAD_URL_DEFAULT).strip()


def logs_upload_path(remote_path: str) -> str:
  return str(remote_path or "").replace("\\", "/").strip().lstrip("/")


def logs_upload_presign_url(path: str, base_url: str | None = None) -> str:
  endpoint = (base_url or logs_upload_url()).strip() or LOGS_UPLOAD_URL_DEFAULT
  separator = "&" if "?" in endpoint else "?"
  return f"{endpoint}{separator}path={quote(path, safe='')}"


def validate_logs_upload_request(path: str, size: int | None = None) -> None:
  if not path:
    raise RuntimeError("upload path is required")
  if len(path) > 1024:
    raise RuntimeError("upload path is too long")
  if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
    raise RuntimeError("upload path contains control character")
  if size is not None and (size <= 0 or size > LOGS_UPLOAD_MAX_FILE_SIZE):
    raise RuntimeError(f"upload file size must be 1..{LOGS_UPLOAD_MAX_FILE_SIZE} bytes")


def logs_upload_timeout_seconds() -> float:
  try:
    return max(30.0, float(os.environ.get("CARROT_LOGS_UPLOAD_TIMEOUT", "600") or "600"))
  except Exception:
    return 600.0


def logs_upload_chunk_size() -> int:
  try:
    size = int(os.environ.get("CARROT_LOGS_UPLOAD_CHUNK_SIZE", str(LOGS_UPLOAD_CHUNK_SIZE_DEFAULT)) or str(LOGS_UPLOAD_CHUNK_SIZE_DEFAULT))
  except Exception:
    size = LOGS_UPLOAD_CHUNK_SIZE_DEFAULT
  return max(1024 * 1024, min(50 * 1024 * 1024, size))


def logs_upload_debug_enabled() -> bool:
  value = os.environ.get("CARROT_LOGS_UPLOAD_DEBUG", "")
  return value.strip().lower() in ("1", "true", "yes", "on")


async def put_file_to_logs_upload(
  local_path: str,
  remote_path: str,
  session: ClientSession,
  *,
  base_url: str | None = None,
  should_cancel: Callable[[], bool] | None = None,
  on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
  def check_cancel() -> None:
    if should_cancel and should_cancel():
      raise RuntimeError("upload canceled")

  check_cancel()
  endpoint = (base_url or logs_upload_url()).strip() or LOGS_UPLOAD_URL_DEFAULT
  path = logs_upload_path(remote_path)
  try:
    file_size = os.path.getsize(local_path)
  except OSError as exc:
    raise RuntimeError(f"cannot read upload file size: {exc}") from exc
  content_type = LOGS_UPLOAD_CONTENT_TYPE_DEFAULT
  validate_logs_upload_request(path, file_size)
  chunk_size = logs_upload_chunk_size()
  started_at = time.monotonic()
  presign_started_at = started_at

  presign_url = logs_upload_presign_url(path, endpoint)
  check_cancel()
  async with session.post(
    presign_url,
    headers={
      "Accept-Encoding": LOGS_UPLOAD_ACCEPT_ENCODING,
      "Content-Type": content_type,
    },
    allow_redirects=False,
  ) as resp:
    text = await resp.text()
    try:
      presign = json.loads(text) if text else {}
    except Exception as exc:
      detail = text.strip()[:500]
      suffix = f": {detail}" if detail else ""
      raise RuntimeError(f"presign returned invalid JSON HTTP {resp.status} for {path}{suffix}") from exc
    if not 200 <= resp.status < 300 or presign.get("ok") is False:
      detail = str(presign.get("error") or presign.get("message") or text).strip()[:500]
      suffix = f": {detail}" if detail else ""
      raise RuntimeError(f"presign HTTP {resp.status} for {path}{suffix}")
  presign_seconds = time.monotonic() - presign_started_at

  method = str(presign.get("method") or "PUT").upper()
  upload_url = str(presign.get("uploadUrl") or "").strip()
  if method != "PUT":
    raise RuntimeError(f"presign returned unsupported method {method}")
  if not upload_url:
    raise RuntimeError("presign response missing uploadUrl")

  response_headers = presign.get("headers")
  headers = {}
  if isinstance(response_headers, dict):
    for k, v in response_headers.items():
      if not k or v is None:
        continue
      key = str(k)
      if key.lower() in ("accept-encoding", "content-encoding", "content-length", "content-type"):
        continue
      headers[key] = str(v)
  headers["Accept-Encoding"] = LOGS_UPLOAD_ACCEPT_ENCODING
  headers["Content-Type"] = content_type
  headers["Content-Length"] = str(file_size)
  read_seconds = 0.0
  chunk_count = 0

  async def file_chunks():
    nonlocal read_seconds, chunk_count
    with open(local_path, "rb") as f:
      while True:
        check_cancel()
        read_started_at = time.monotonic()
        chunk = await asyncio.to_thread(f.read, chunk_size)
        read_seconds += time.monotonic() - read_started_at
        if not chunk:
          break
        chunk_count += 1
        if on_progress:
          on_progress(len(chunk))
        yield chunk

  check_cancel()
  put_started_at = time.monotonic()
  async with session.put(
    upload_url,
    data=file_chunks(),
    headers=headers,
    allow_redirects=False,
  ) as resp:
    if not 200 <= resp.status < 300:
      text = await resp.text()
      detail = text.strip()[:500]
      suffix = f": {detail}" if detail else ""
      raise RuntimeError(f"upload PUT HTTP {resp.status}{suffix}")
    check_cancel()
  put_seconds = time.monotonic() - put_started_at
  total_seconds = time.monotonic() - started_at
  return {
    "ok": True,
    "endpoint": endpoint,
    "path": presign.get("path") or path,
    "expiresIn": presign.get("expiresIn"),
    "size": file_size,
    "chunkSize": chunk_size,
    "chunks": chunk_count,
    "presignSeconds": presign_seconds,
    "readSeconds": read_seconds,
    "putSeconds": put_seconds,
    "totalSeconds": total_seconds,
  }


def upload_folder_to_ftp(
  local_folder: str,
  directory: str,
  remote_path: str,
  should_cancel: Callable[[], bool] | None = None,
  on_file_start: Callable[[str, int], None] | None = None,
  on_file_progress: Callable[[str, int, int, int], None] | None = None,
  on_file_done: Callable[[str, int], None] | None = None,
) -> bool:
  def check_cancel() -> None:
    if should_cancel and should_cancel():
      raise RuntimeError("upload canceled")

  ftp_server = os.environ.get("CARROT_FTP_SERVER", "shind0.synology.me")
  ftp_port = int(os.environ.get("CARROT_FTP_PORT", "8021"))
  ftp_username = os.environ.get("CARROT_FTP_USERNAME", "carrotpilot")
  ftp_password = os.environ.get("CARROT_FTP_PASSWORD", "Ekdrmsvkdlffjt7710")

  check_cancel()
  ftp = FTP()
  ftp.connect(ftp_server, ftp_port, timeout=20)
  check_cancel()
  ftp.login(ftp_username, ftp_password)
  try:
    check_cancel()
    ftp.cwd("routes")
    routes_root = ftp.pwd()

    def cwd_or_create(path: str) -> None:
      check_cancel()
      ftp.cwd(routes_root)
      for part in [p for p in path.split("/") if p]:
        check_cancel()
        try:
          ftp.cwd(part)
        except Exception:
          ftp.mkd(part)
          ftp.cwd(part)

    base_path = f"{directory}/{remote_path}".strip("/")
    for root, _, files in os.walk(local_folder):
      check_cancel()
      rel_dir = os.path.relpath(root, local_folder)
      remote_dir = base_path if rel_dir == "." else f"{base_path}/{rel_dir.replace(os.sep, '/')}"
      cwd_or_create(remote_dir)
      for filename in files:
        check_cancel()
        local_path = os.path.join(root, filename)
        rel_file = filename if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{filename}"
        try:
          file_size = os.path.getsize(local_path)
        except OSError:
          file_size = 0
        sent = 0

        def note_block(block: bytes) -> None:
          nonlocal sent
          delta = len(block or b"")
          sent += delta
          if on_file_progress:
            on_file_progress(rel_file, sent, file_size, delta)

        if on_file_start:
          on_file_start(rel_file, file_size)
        with open(local_path, "rb") as f:
          # 1MB chunks (vs ftplib's 8KB default) cut per-chunk Python/syscall
          # overhead for large camera files.
          ftp.storbinary(f"STOR {filename}", f, blocksize=1024 * 1024, callback=note_block)
        if on_file_done:
          on_file_done(rel_file, file_size)
        check_cancel()
    return True
  finally:
    try:
      ftp.quit()
    except Exception:
      pass
