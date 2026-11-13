#!/usr/bin/env python3
"""Analyze a source video: info, scene cuts, contact sheets, 16 kHz audio.

usage: analyze.py <video> --project <project dir> [--every 2.0] [--scene 0.30]

Writes into <project>/work/:
  info.json      duration, size, fps
  scenes.json    list of shots [{i, start, end, len}]
  sheets/*.jpg   contact sheets (one frame every --every seconds, time-stamped)
  audio16k.wav   mono 16 kHz audio for transcription
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def probe(video):
    out = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json", str(video),
    ]).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {
        "width": st["width"],
        "height": st["height"],
        "fps": round(float(num) / float(den), 3),
        "duration": round(float(d["format"]["duration"]), 3),
    }


def scene_cuts(video, threshold):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-an",
         "-vf", f"scale=480:-2,select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", p.stderr)]


def contact_sheets(video, out_dir, duration, every, cols=5, rows=4, thumb_w=384):
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("sheet_*.jpg"):
        f.unlink()
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    label = (
        f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':x=6:y=6:fontsize=20:"
        "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=4"
    )
    vf = f"fps=1/{every},scale={thumb_w}:-2,{label},tile={cols}x{rows}:padding=4:margin=4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vf", vf, "-q:v", "3", str(out_dir / "sheet_%02d.jpg")])
    return sorted(str(p) for p in out_dir.glob("sheet_*.jpg"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--project", required=True)
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--scene", type=float, default=0.30)
    a = ap.parse_args()

    video = Path(a.video).resolve()
    work = Path(a.project).resolve() / "work"
    work.mkdir(parents=True, exist_ok=True)

    info = probe(video)
    info["source"] = str(video)
    (work / "info.json").write_text(json.dumps(info, indent=2))

    cuts = [0.0] + scene_cuts(video, a.scene) + [info["duration"]]
    shots = []
    for i, (s, e) in enumerate(zip(cuts, cuts[1:])):
        if e - s > 0.04:
            shots.append({"i": len(shots), "start": s, "end": e, "len": round(e - s, 3)})
    (work / "scenes.json").write_text(json.dumps(shots, indent=1))

    sheets = contact_sheets(video, work / "sheets", info["duration"], a.every)

    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", str(work / "audio16k.wav")])

    print(f"{info['width']}x{info['height']} @ {info['fps']} fps, {info['duration']} s")
    print(f"shots: {len(shots)}  (median {sorted(x['len'] for x in shots)[len(shots)//2]} s)")
    print(f"sheets: {len(sheets)} in {work / 'sheets'}")


if __name__ == "__main__":
    main()
