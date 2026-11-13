# GTA VI clips

Tools that turn one source video into vertical 1080x1920 clips, each with a
16:9 thumbnail, a 9:16 cover, a title and a description.

Source: "Grand Theft Auto VI Launch Trailer" (Rockstar Games),
https://youtu.be/rz1B4B2yBZY — the video file itself is not in the repo
(`source/` is git-ignored). Put it at `source/source.mp4`.

## Setup

    pip install faster-whisper numpy pillow fonttools
    # ffmpeg with libass is required

## Steps

    python3 tools/analyze.py source/source.mp4 --project .      # shots, contact sheets, audio
    python3 tools/transcribe.py work/audio16k.wav --out work/transcript.json --lang en
    # write clips.json (format documented at the top of tools/build.py)
    python3 tools/build.py --project . --preview                # quick 540x960 check
    python3 tools/build.py --project .                          # final clips + thumbnails + output/clips.md

Fonts in `fonts/` (Anton, Montserrat ExtraBold) are under the SIL Open Font License.
