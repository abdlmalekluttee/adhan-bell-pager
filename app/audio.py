"""Audio libraries, upload conversion, and playback rendering.

Two libraries share the ``AUDIO_DIR`` volume:

* **Adhan** recordings live at the root of ``AUDIO_DIR`` (unchanged from v1.x).
* **Bell** sounds live in ``AUDIO_DIR/bells``. A few synthesized bells are
  created on first start so the module works before anything is uploaded.

Uploads are converted with ffmpeg to 16-bit mono PCM WAV at the configured
rate, which PJSIP plays without resampling surprises. Repeat counts and fixed
durations are rendered to cached WAV files in pure Python so the SIP engine
always plays one file once.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unicodedata
import wave

from . import config

ALLOWED_UPLOAD = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
LIBRARIES = ("adhan", "bells")


def library_dir(library: str) -> str:
    if library not in LIBRARIES:
        raise ValueError("unknown library")
    d = config.AUDIO_DIR if library == "adhan" else os.path.join(config.AUDIO_DIR, "bells")
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir() -> str:
    d = os.path.join(config.AUDIO_DIR, ".cache")
    os.makedirs(d, exist_ok=True)
    return d


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def safe_name(filename: str) -> str:
    """A filesystem-safe, human-readable base name (keeps Arabic letters)."""
    base = os.path.splitext(os.path.basename(filename or ""))[0]
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[^\w\- ().]+", "_", base, flags=re.UNICODE).strip(" ._")
    return (base or "audio")[:80]


def wav_info(path: str) -> dict:
    info = {"duration": None, "rate": None, "channels": None, "playable": False}
    try:
        with wave.open(path, "rb") as w:
            info["rate"] = w.getframerate()
            info["channels"] = w.getnchannels()
            info["duration"] = round(w.getnframes() / float(w.getframerate()), 2)
            info["playable"] = w.getsampwidth() == 2 and info["duration"] > 0
    except Exception:  # noqa: BLE001
        pass
    return info


def list_files(library: str = "adhan") -> list[dict]:
    d = library_dir(library)
    out = []
    for name in sorted(os.listdir(d), key=str.lower):
        path = os.path.join(d, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        item = {"name": name, "size": os.path.getsize(path), "modified": int(os.path.getmtime(path))}
        if name.lower().endswith(".wav"):
            item.update(wav_info(path))
        else:
            item.update({"duration": None, "rate": None, "channels": None, "playable": False})
        out.append(item)
    return out


def path_for(name: str, library: str = "adhan") -> str | None:
    if not name or name.startswith(".") or name != os.path.basename(name):
        return None
    p = os.path.join(library_dir(library), name)
    return p if os.path.isfile(p) else None


def save_upload(filename: str, src_path: str, library: str = "adhan", *, replace: bool = False) -> dict:
    """Convert an uploaded temp file into a normalised WAV in the library."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD:
        raise ValueError(f"Unsupported file type '{ext or '?'}'. Use WAV, MP3, M4A, AAC, OGG, FLAC or Opus.")
    d = library_dir(library)
    wav_name = f"{safe_name(filename)}.wav"
    wav_path = os.path.join(d, wav_name)
    if os.path.exists(wav_path) and not replace:
        raise FileExistsError(wav_name)
    fd, tmp_out = tempfile.mkstemp(prefix=".convert-", suffix=".wav", dir=d)
    os.close(fd)
    try:
        _convert_to_wav(src_path, tmp_out, ext)
        info = wav_info(tmp_out)
        if not info["playable"]:
            raise ValueError("The file could not be decoded as audio.")
        os.replace(tmp_out, wav_path)
    finally:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
    return {"name": wav_name, **wav_info(wav_path)}


def _convert_to_wav(src: str, dst: str, ext: str) -> None:
    cfg = config.get().audio
    rate = cfg.target_rate
    if not have_ffmpeg():
        if ext != ".wav":
            raise ValueError("ffmpeg is not installed, so only WAV files can be uploaded.")
        shutil.copyfile(src, dst)
        return
    af = f"volume={cfg.gain_db}dB" if cfg.gain_db else "anull"
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-vn", "-ac", "1", "-ar", str(rate), "-af", af, "-acodec", "pcm_s16le", "-f", "wav", dst]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as e:
        raise ValueError("Audio conversion took too long.") from e
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise ValueError(f"Audio conversion failed: {detail[0][:200]}")


def delete_file(name: str, library: str = "adhan") -> bool:
    p = path_for(name, library)
    if p:
        os.remove(p)
        return True
    return False


def adhan_file_usage(name: str) -> list[str]:
    """Where an Adhan file is assigned (so deleting it can be refused)."""
    a = config.get().audio
    used = []
    if a.default_file == name:
        used.append("default Adhan")
    for p, f in a.per_prayer.items():
        if f == name:
            used.append(f"{p} Adhan")
    if a.iqama_file == name:
        used.append("default iqama")
    for p, f in a.iqama_per_prayer.items():
        if f == name:
            used.append(f"{p} iqama")
    return used


def resolve_for_prayer(prayer: str) -> str | None:
    cfg = config.get().audio
    chosen = cfg.default_file if cfg.use_global_for_all else (cfg.per_prayer.get(prayer) or cfg.default_file)
    return path_for(chosen, "adhan")


def resolve_iqama_for_prayer(prayer: str) -> str | None:
    cfg = config.get().audio
    return path_for(cfg.iqama_per_prayer.get(prayer) or cfg.iqama_file, "adhan")


def duration_of(path: str | None) -> float | None:
    if not path or not os.path.isfile(path):
        return None
    return wav_info(path)["duration"]


# --------------------------------------------------------------------------- #
# Playback rendering
# --------------------------------------------------------------------------- #
def render_playback(path: str, mode: str = "once", repeat_count: int = 1,
                    duration_seconds: int = 0, gap_ms: int = 400) -> str:
    """Return a WAV that plays ``path`` once, N times, or looped/trimmed to a duration."""
    if mode == "once" or (mode == "repeat" and repeat_count <= 1):
        return path
    st = os.stat(path)
    key = hashlib.sha256(
        f"{path}|{st.st_mtime_ns}|{st.st_size}|{mode}|{repeat_count}|{duration_seconds}|{gap_ms}".encode()
    ).hexdigest()[:24]
    out = os.path.join(cache_dir(), f"{key}.wav")
    if os.path.exists(out):
        return out
    with wave.open(path, "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    frame_bytes = params.sampwidth * params.nchannels
    gap = b"\x00" * (int(params.framerate * gap_ms / 1000) * frame_bytes)
    if mode == "repeat":
        n = max(1, min(20, int(repeat_count)))
        data = gap.join([frames] * n)
    elif mode == "duration":
        total = int(max(1, min(600, int(duration_seconds))) * params.framerate) * frame_bytes
        chunk = frames + gap
        if not chunk:
            raise ValueError("empty audio")
        data = (chunk * (total // len(chunk) + 1))[:total]
        fade = min(len(data), int(params.framerate * 0.05) * frame_bytes)
        if params.sampwidth == 2 and params.nchannels == 1 and fade:
            data = data[:-fade] + _fade_out(data[-fade:])
    else:
        return path
    tmp = out + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(data)
    os.replace(tmp, out)
    _trim_cache()
    return out


def _fade_out(chunk: bytes) -> bytes:
    n = len(chunk) // 2
    samples = struct.unpack(f"<{n}h", chunk[: n * 2])
    return struct.pack(f"<{n}h", *(int(s * (1 - i / n)) for i, s in enumerate(samples)))


def _trim_cache(limit: int = 60) -> None:
    d = cache_dir()
    files = sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".wav")),
                   key=os.path.getmtime)
    for f in files[:-limit]:
        try:
            os.remove(f)
        except OSError:
            pass


def playback_seconds(path: str | None, mode: str, repeat_count: int, duration_seconds: int) -> float | None:
    base = duration_of(path)
    if base is None:
        return None
    if mode == "repeat":
        n = max(1, int(repeat_count))
        return round(base * n + 0.4 * (n - 1), 1)
    if mode == "duration":
        return float(duration_seconds)
    return base


# --------------------------------------------------------------------------- #
# Built-in bell sounds (synthesized, so no third-party recordings are shipped)
# --------------------------------------------------------------------------- #
BUILTIN_BELLS = {
    "Classic school bell.wav": "ring",
    "Two-tone chime.wav": "chime",
    "Three beeps.wav": "beeps",
}


def ensure_builtin_bells(rate: int = 16000) -> list[str]:
    d = library_dir("bells")
    created = []
    marker = os.path.join(d, ".builtin-created")
    if os.path.exists(marker):
        return created
    for name, kind in BUILTIN_BELLS.items():
        p = os.path.join(d, name)
        if not os.path.exists(p):
            _write_wav(p, rate, _synth(kind, rate))
            created.append(name)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("built-in bell sounds were created; delete this file to recreate missing ones\n")
    return created


def _synth(kind: str, rate: int) -> list[float]:
    out: list[float] = []
    if kind == "ring":
        # Electric bell: a bright partial set struck ~25 times per second for 4 s.
        dur = 4.0
        for i in range(int(rate * dur)):
            t = i / rate
            strike = (t * 25) % 1.0
            env = math.exp(-strike * 6)
            tone = (math.sin(2 * math.pi * 880 * t) * 0.6 + math.sin(2 * math.pi * 1320 * t) * 0.3
                    + math.sin(2 * math.pi * 2210 * t) * 0.15)
            fade = min(1.0, (dur - t) * 8, t * 40)
            out.append(tone * env * fade * 0.55)
    elif kind == "chime":
        for freq, start in ((784.0, 0.0), (622.3, 0.9)):
            seg_len = int(rate * 1.8)
            base = int(rate * start)
            while len(out) < base + seg_len:
                out.append(0.0)
            for i in range(seg_len):
                t = i / rate
                env = math.exp(-t * 2.2) * min(1.0, t * 200)
                v = (math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * freq * 2.01 * t)) * env
                out[base + i] += v * 0.45
    else:
        for _ in range(3):
            for i in range(int(rate * 0.28)):
                t = i / rate
                env = min(1.0, t * 300, (0.28 - t) * 300)
                out.append(math.sin(2 * math.pi * 1000 * t) * env * 0.5)
            out.extend([0.0] * int(rate * 0.2))
    return out


def _write_wav(path: str, rate: int, samples: list[float]) -> None:
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples))
    os.replace(tmp, path)
