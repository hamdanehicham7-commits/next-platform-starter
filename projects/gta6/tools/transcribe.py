#!/usr/bin/env python3
"""Word-timed transcript with faster-whisper.

usage: transcribe.py <audio.wav> --out transcript.json [--model large-v3] [--lang en] [--prompt "..."]
"""
import argparse
import json
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel


def load_wav16k(path):
    """Mono 16 kHz 16-bit WAV (as written by analyze.py) -> float32 array."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit("expected mono 16 kHz 16-bit WAV (run analyze.py first)")
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--prompt", default=None)
    a = ap.parse_args()

    model = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=4)
    segments, info = model.transcribe(
        load_wav16k(a.audio),
        language=a.lang,
        initial_prompt=a.prompt,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False,
    )
    out = {"language": info.language, "segments": []}
    for s in segments:
        seg = {
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": [
                {"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)}
                for w in (s.words or [])
            ],
        }
        out["segments"].append(seg)
        print(f"[{seg['start']:7.2f} - {seg['end']:7.2f}] {seg['text']}", flush=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"language: {info.language}, segments: {len(out['segments'])} -> {a.out}")


if __name__ == "__main__":
    main()
