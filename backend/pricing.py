"""Credit pricing for Voxa — internal credits are 1:1 with Ai33 credits.

Rates measured 2026-09-26 against api.ai33.pro by creating tiny real tasks and
reading the credit delta (16-char "Hello world test" for TTS, 1.9s mp3 for STT).
Entries marked "estimate" were not measurable at the time (provider down,
endpoint gone, or params too varied) — they are set conservatively so the
owner's Ai33 pool is never under-billed. Tune freely.
"""
from __future__ import annotations

# --- text to speech: credits per character, by voice provider ---------------
TTS_PER_CHAR = {
    "edge": 0.6,        # measured 0.50
    "elevenlabs": 1.2,  # measured 1.06
    "minimax": 1.2,     # measured 1.06
    "kokoro": 0.8,      # estimate — provider was under maintenance during measurement
    "vbee": 1.2,        # measured 1.06
    "fishaudio": 1.2,   # measured 1.06
    "clone": 1.2,       # estimate — cloned voices bill like premium TTS
}
DEFAULT_PER_CHAR = 1.2


def provider_of(voice_id: str) -> str:
    """Map a voice_id like 'elevenlabs_abc123' to its provider key."""
    vid = (voice_id or "").strip().lower()
    for prov in TTS_PER_CHAR:
        if vid.startswith(prov + "_") or vid.startswith(prov + "-"):
            return prov
    return "default"


def tts_cost(text: str, voice_id: str) -> float:
    rate = TTS_PER_CHAR.get(provider_of(voice_id), DEFAULT_PER_CHAR)
    return round(len(text or "") * rate, 2)


def dialogue_cost(text: str, speakers) -> float:
    """Multi-speaker dialogue bills at the priciest voice used."""
    rate = DEFAULT_PER_CHAR
    try:
        rates = [
            TTS_PER_CHAR.get(provider_of(s.get("voice_id", "")), DEFAULT_PER_CHAR)
            for s in (speakers or [])
            if isinstance(s, dict)
        ]
        if rates:
            rate = max(rates)
    except Exception:
        pass
    return round(len(text or "") * rate, 2)


# --- audio tools: credits per MB of uploaded audio ---------------------------
# (Duration parsing would need ffmpeg; file size is exact and dependency-free.)
STT_PER_MB = 300.0            # measured ~260/MB on a 1.9s mp3
DUBBING_PER_MB = 900.0        # estimate
VOICE_CHANGER_PER_MB = 900.0  # estimate
VOICE_ISOLATE_PER_MB = 600.0  # estimate


def audio_mb_cost(total_bytes: int, per_mb: float) -> float:
    return round((total_bytes or 0) / (1024 * 1024) * per_mb, 2)


# --- flat fees ---------------------------------------------------------------
SFX_FLAT = 250.0            # measured 200
MUSIC_FLAT = 500.0          # estimate — upstream endpoint currently returns 410 Gone
CLONE_CREATE_FLAT = 1000.0  # estimate — creation endpoint errored during measurement

# Image pricing is read live from ai33 (/v1i/models -> presented_credits);
# this is only the fallback when that lookup fails.
IMAGE_FALLBACK_PER_IMAGE = 400.0
