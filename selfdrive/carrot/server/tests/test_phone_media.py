from openpilot.selfdrive.carrot.server.features.phone_media import (
  _normalize_phone_media,
  _preserve_phone_media_art,
)


def media(**overrides):
  body = {
    "title": "테스트 곡",
    "artist": "테스트 가수",
    "package": "com.example.player",
  }
  body.update(overrides)
  return _normalize_phone_media(body)


def test_preserves_art_for_same_track_progress_update():
  previous = media(
    artBase64="/9j/valid",
    artMime="image/jpeg",
    artHash="art-hash",
    artSource="metadata.bitmap",
    positionMs=1000,
  )
  update = media(positionMs=3000)

  assert _preserve_phone_media_art(update, previous)
  assert update["artBase64"] == previous["artBase64"]
  assert update["artMime"] == "image/jpeg"
  assert update["artHash"] == "art-hash"
  assert update["artSource"] == "metadata.bitmap"


def test_does_not_preserve_art_when_track_changes():
  previous = media(artBase64="/9j/old", artMime="image/jpeg")
  update = media(title="다른 곡")

  assert not _preserve_phone_media_art(update, previous)
  assert update["artBase64"] == ""


def test_new_art_replaces_previous_art():
  previous = media(artBase64="/9j/old", artMime="image/jpeg")
  update = media(artBase64="/9j/new", artMime="image/jpeg")

  assert not _preserve_phone_media_art(update, previous)
  assert update["artBase64"] == "/9j/new"
