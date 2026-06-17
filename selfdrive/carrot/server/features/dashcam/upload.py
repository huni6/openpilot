import asyncio
import base64
import os
import subprocess
from ftplib import FTP
from typing import Any, Callable
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout

from openpilot.system.hardware import HARDWARE

from ...config import DASHCAM_DEFAULT_DISCORD_KEY, DASHCAM_DEFAULT_DISCORD_WEBHOOK
from .paths import file_size_label


LOGS_UPLOAD_URL_DEFAULT = "https://logs.carrotpilot.app/upload/routes"


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


def logs_upload_url_for_path(remote_path: str, base_url: str | None = None) -> str:
  endpoint = (base_url or logs_upload_url()).strip() or LOGS_UPLOAD_URL_DEFAULT
  separator = "&" if "?" in endpoint else "?"
  return f"{endpoint}{separator}path={quote(remote_path, safe='')}"


def logs_upload_put_url(remote_path: str, base_url: str | None = None) -> str:
  return logs_upload_url_for_path(remote_path.strip("/"), base_url)


def logs_upload_timeout_seconds() -> float:
  try:
    return max(30.0, float(os.environ.get("CARROT_LOGS_UPLOAD_TIMEOUT", "600") or "600"))
  except Exception:
    return 600.0


async def put_file_to_logs_upload(
  local_path: str,
  remote_path: str,
  session: ClientSession,
  *,
  base_url: str | None = None,
  should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
  def check_cancel() -> None:
    if should_cancel and should_cancel():
      raise RuntimeError("upload canceled")

  check_cancel()
  url = logs_upload_put_url(remote_path, base_url)

  async def file_chunks():
    with open(local_path, "rb") as f:
      while True:
        check_cancel()
        chunk = await asyncio.to_thread(f.read, 1024 * 1024)
        if not chunk:
          break
        yield chunk

  headers = {"Content-Type": "application/octet-stream"}
  try:
    headers["Content-Length"] = str(os.path.getsize(local_path))
  except OSError:
    pass

  check_cancel()
  async with session.put(
    url,
    data=file_chunks(),
    headers=headers,
  ) as resp:
    if not 200 <= resp.status < 300:
      text = await resp.text()
      detail = text.strip()[:500]
      suffix = f": {detail}" if detail else ""
      raise RuntimeError(f"HTTP {resp.status}{suffix}")
    check_cancel()
  return {"ok": True, "url": url}


def upload_folder_to_ftp(
  local_folder: str,
  directory: str,
  remote_path: str,
  should_cancel: Callable[[], bool] | None = None,
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
        with open(local_path, "rb") as f:
          # 1MB chunks (vs ftplib's 8KB default) cut per-chunk Python/syscall
          # overhead for large camera files.
          ftp.storbinary(f"STOR {filename}", f, blocksize=1024 * 1024)
        check_cancel()
    return True
  finally:
    try:
      ftp.quit()
    except Exception:
      pass
