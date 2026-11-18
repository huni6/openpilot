"""NewUi 3 robot face: know when music plays so the face can dance to it.

Two sources, display only:
- PhoneMusic: the phone's player state, which the Navdy HUD (v186+) adds to its reply to the Navdy bridge.
- BeatTracker: the beat in the cabin microphone. Pure numpy; input is micd's rawAudioData (16 kHz mono int16,
  800 samples every 50 ms). Same method as the web preview: a spectral-flux onset envelope (bass weighted),
  autocorrelation of the last few seconds for the tempo, and a beat clock pulled toward each detected onset.
"""
import json
import math
import os

import numpy as np

RATE = 16000
FRAME, HOP = 512, 256               # 32 ms windows every 16 ms: a 62.5 Hz onset envelope
ENV_HZ = RATE / HOP
WINDOW_S = 6.0                      # tempo from this much recent audio
BASS_HZ, TOP_HZ = 150.0, 8000.0
LOW_HZ = (30.0, 400.0)              # the C4 microphone hears little of the car's speakers above this (measured in the car)
FLOOR_S = 1.5                       # steady sound (engine, fan, hum) is followed this slowly and taken out
MIN_BPM, MAX_BPM, PRIOR_BPM = 80.0, 180.0, 118.0
CONFIDENT = 0.2                     # sure enough to keep dancing; to begin it needs START and a steady tempo
START = 0.3                         # ...held for HOLD estimates (0.5 s apart) in a row (real cabin music reached 0.34-0.42),
STEADY = 0.03                       # ...with those tempo estimates within 3%: music holds its tempo, road noise does not
HOLD = 6
DB_FLOOR, DB_RANGE = -100.0, 70.0   # level mapping, like a web AnalyserNode's byte spectrum
LEAD = 0.025                        # s: the UI shows a beat at its next 50 ms frame, so hand it over half a frame early
CAPTURE_DELAY = 0.03                # s from a sound to micd stamping the chunk that holds it (input buffering)

NAVDY_FEEDBACK = '/dev/shm/navdy-feedback.json'   # the Navdy bridge's latest HUD reply (navdy.navigation.FEEDBACK_PATH)
PHONE_CHECK_S = 1.0                 # read that file at most this often
PHONE_STALE_S = 10.0                # a reply older than this (bridge stopped, HUD gone) says nothing
PHONE_SETTLE_S = 1.5                # "playing" must hold this long before it counts (a moment of buffering or a
                                    # skip is not music); "not playing" counts at once
STEADY_PERIOD = 0.5                 # s per beat (120 BPM) when all we know is that a song is playing


class PhoneMusic:
  """Whether the phone is actually playing music: the player state of the HUD's current track ("music": {"playing":
  true}) in the reply the Navdy bridge saves, not just that a track is known (a paused or stopped track says false).
  Older HUDs send no "music" (not playing); the bridge deletes the file on disconnect."""
  def __init__(self, path: str = NAVDY_FEEDBACK):
    self.path = path
    self.checked = -math.inf
    self.mtime = None
    self.state = False
    self.received = -math.inf       # bridge time.monotonic() of that reply
    self.since: float | None = None  # when it started saying playing

  def playing(self, now: float) -> bool:
    """`now`: time.monotonic() (the same clock the bridge stamps replies with)."""
    if now - self.checked >= PHONE_CHECK_S:
      self.checked = now
      self._read()
    on = self.state and now - self.received < PHONE_STALE_S
    if not on:
      self.since = None
      return False
    if self.since is None:
      self.since = now
    return now - self.since >= PHONE_SETTLE_S

  def _read(self) -> None:
    try:
      mtime = os.stat(self.path).st_mtime_ns
    except OSError:
      self.mtime, self.state = None, False
      return
    if mtime == self.mtime:
      return
    self.mtime = mtime
    try:
      with open(self.path, 'rb') as f:
        saved = json.load(f)
      music = saved['feedback'].get('music')
      received = saved.get('receivedAt')
      self.state = isinstance(music, dict) and music.get('playing') is True
      self.received = float(received) if isinstance(received, (int, float)) and not isinstance(received, bool) else -math.inf
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
      self.state = False


class SteadyBeat:
  """A plain beat clock, for when we know a song is playing but hear no beat to follow."""
  def __init__(self, period: float = STEADY_PERIOD, strength: float = 0.6):
    self.period, self.strength, self.phase = period, strength, 0.0

  def advance(self, dt: float) -> list[float]:
    beats = []
    self.phase += dt / self.period
    while self.phase >= 1.0:
      self.phase -= 1.0
      beats.append(self.strength)
    return beats


class BeatTracker:
  def __init__(self):
    self.window = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    self.bass = (freqs > 0) & (freqs <= BASS_HZ)
    self.full = (freqs > 0) & (freqs <= TOP_HZ)
    self.low = (freqs >= LOW_HZ[0]) & (freqs <= LOW_HZ[1])
    self.floor_k = 1 - math.exp(-1 / (FLOOR_S * ENV_HZ))
    lags = np.arange(int(ENV_HZ * 60 / MAX_BPM) - 1, int(ENV_HZ * 60 / MIN_BPM) + 2)
    self.lags = lags
    bpm = 60 * ENV_HZ / lags
    self.prior = np.exp(-0.5 * (np.log2(bpm / PRIOR_BPM) / 0.6) ** 2)
    self.in_range = (bpm >= MIN_BPM) & (bpm <= MAX_BPM)
    self.reset()

  def reset(self) -> None:
    self.pending = np.zeros(0, np.float32)
    self.prev = None
    self.floor = None
    self.env = np.zeros(int(WINDOW_S * ENV_HZ), np.float32)
    self.count = 0
    self.mean, self.var, self.o1, self.o2 = 0.0, 1e-4, 0.0, 0.0
    self.t = 0.0                    # audio time of the next envelope sample, s
    self.last_onset = -9.0
    self.period, self.phase, self.conf = 0.54, 0.0, 0.0
    self.tempo_at = 0.0
    self.strength = 0.5
    self.onsets = 0
    self.age = 0.0                  # how long ago the frame being processed ended
    self.periods: list[float] = []  # recent raw tempo estimates
    self.confs: list[float] = []    # and their confidences
    self.locked = False             # found music and dancing to it

  @property
  def bpm(self) -> float:
    return 60.0 / self.period

  def feed(self, audio: np.ndarray, age: float = 0.0) -> None:
    """Add microphone samples (int16 or float). `age`: how long ago the last of them was recorded."""
    x = audio.astype(np.float32)
    if audio.dtype == np.int16:
      x /= 32768.0
    self.pending = np.concatenate((self.pending, x))
    frames = (self.pending.size - FRAME) // HOP + 1 if self.pending.size >= FRAME else 0
    for k in range(frames):
      seg = self.pending[k * HOP:k * HOP + FRAME]
      # how long before "now" this frame ended, so an onset nudges the beat clock by when it happened
      self.age = age + (self.pending.size - (k * HOP + FRAME)) / RATE
      self._frame(seg)
    if frames:
      self.pending = self.pending[frames * HOP:]

  def _frame(self, seg: np.ndarray) -> None:
    mag = np.abs(np.fft.rfft(seg * self.window)) * (4.0 / FRAME)
    level = np.clip((20 * np.log10(mag + 1e-9) - DB_FLOOR) / DB_RANGE, 0.0, 1.0)
    if self.prev is None:
      self.prev, self.floor = level, level.copy()
    rise = np.maximum(level - self.prev, 0.0)
    self.prev = level
    # the low band above its own slow floor: kicks and bass notes stand out of the engine and road rumble
    white = np.maximum(level - self.floor, 0.0)
    self.floor += (level - self.floor) * self.floor_k
    o = float(rise[self.bass].mean()) * 0.65 + float(rise[self.full].mean()) * 1.4 + float(white[self.low].mean()) * 3.0
    self._envelope(o)
    self.t += 1 / ENV_HZ
    if self.t >= self.tempo_at:
      self._tempo()
      self.tempo_at = self.t + 0.5

  def _envelope(self, o: float) -> None:
    self.env[self.count % self.env.size] = o
    self.count += 1
    self.mean += (o - self.mean) * 0.03
    self.var += ((o - self.mean) ** 2 - self.var) * 0.03
    sd = math.sqrt(self.var)
    thr = self.mean + 1.4 * sd
    # the previous sample is a peak: an onset (at most two per beat)
    if self.o1 > thr and self.o1 >= o and self.o1 > self.o2 and self.t - self.last_onset > 0.5 * self.period:
      self.last_onset = self.t
      self.onsets += 1
      self.strength = min(1.0, max(0.2, self.o1 / (thr + 2 * sd + 1e-6)))
      if self.conf > 0.2 and self.strength > 0.4:
        # where the beat clock was when the onset happened; a strong hit close to a beat pulls it in
        # (hits halfway between beats, like off-beat hi-hats, are left to the comb fit below)
        at = self.phase - (self.age + 1 / ENV_HZ) / self.period
        err = at - round(at)
        if abs(err) < 0.2:
          self.phase -= err * 0.25
    self.o2, self.o1 = self.o1, o

  def _recent(self, n: int) -> np.ndarray:
    """The last n envelope samples, oldest first."""
    if self.count < self.env.size:
      return self.env[max(0, self.count - n):self.count]
    return np.roll(self.env, -(self.count % self.env.size))[-n:]

  def _tempo(self) -> None:
    n = min(self.count, self.env.size)
    if n < 3 * ENV_HZ:
      return
    a = self._recent(n)
    a = a - a.mean()
    r0 = float(a @ a)
    if r0 <= 1e-9:
      self.conf *= 0.5
      return
    r = np.array([float(a[lag:] @ a[:-lag]) for lag in self.lags]) / r0
    score = np.where(self.in_range, r * self.prior, -np.inf)
    i = int(np.argmax(score))
    best = r[i]
    if 0 < i < len(r) - 1:
      y0, y1, y2 = r[i - 1], r[i], r[i + 1]
      den = y0 - 2 * y1 + y2
      shift = float(np.clip((y0 - y2) / (2 * den), -0.5, 0.5)) if den else 0.0
    else:
      shift = 0.0
    period = (self.lags[i] + shift) / ENV_HZ
    self.periods = [*self.periods[-(HOLD - 1):], period]
    # how far the tempo peak stands out from the rest, and whether it repeats two beats on: music keeps
    # its pulse, while a few road bumps can line up once by chance
    two = int(round(2 * (self.lags[i] + shift)))
    r2 = float(a[two:] @ a[:-two]) / r0 if two < n else 0.0
    conf = float(np.clip((min(best, r2) - float(np.median(r[self.in_range]))) * 2.5, 0.0, 1.0))
    jump = abs(period - self.period) / self.period > 0.12 and conf > 0.35
    self.period = period if jump else self.period + (period - self.period) * 0.4
    self.conf += (conf - self.conf) * 0.5
    self.confs = [*self.confs[-(HOLD - 1):], self.conf]
    steady = len(self.periods) == HOLD and (max(self.periods) - min(self.periods)) / period < STEADY
    playing = self.t - self.last_onset <= max(2.0, 4 * self.period)   # a hit within the last four beats
    if not self.locked and steady and playing and min(self.confs) > START:
      self.locked = True
    elif self.locked and (self.conf < CONFIDENT or not playing):
      self.locked = False                 # the beat faded, or the music stopped (the 6 s window still holds it)
    if self.conf > 0.2:
      self._comb_phase()

  def _comb_phase(self) -> None:
    """Fit the beat grid to the last 4 s of the envelope (every hit counts, off-beats average out)
    and pull the beat clock toward it."""
    n = min(self.count, int(4 * ENV_HZ))
    a = self._recent(n)
    step = self.period * ENV_HZ                          # envelope samples per beat
    offsets = np.arange(int(step))                       # candidate "last beat was this many samples ago"
    ks = np.arange(int(n / step))
    idx = (n - 1 - offsets[:, None] - np.round(ks[None, :] * step)).astype(int)
    valid = idx >= 0
    score = np.where(valid, a[np.clip(idx, 0, n - 1)], 0.0).sum(axis=1) / np.maximum(valid.sum(axis=1), 1)
    since = (int(np.argmax(score)) + 1) / ENV_HZ + self.age   # seconds since the last beat, as of now
    target = since / self.period
    err = (self.phase - target + 0.5) % 1.0 - 0.5
    self.phase -= err * 0.5

  def advance(self, dt: float) -> list[float]:
    """Move the beat clock on by dt (s); the strengths of the beats that fell in it."""
    beats = []
    self.phase += dt / self.period
    while self.phase + LEAD / self.period >= 1.0:
      self.phase -= 1.0
      if self.locked:
        beats.append(self.strength)
    return beats
