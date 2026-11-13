#!/usr/bin/env python3
"""Render vertical clips + thumbnails + a titles/descriptions sheet from clips.json.

usage: build.py --project <project dir> [--only 01,04] [--preview] [--no-video] [--no-thumbs]

clips.json (in the project dir):
{
  "source": "source/source.mp4",
  "credit": "Footage: ... (c) ...",
  "handle": "@account",                    optional, small tag at the bottom
  "chip": "GTA VI",                        small tag on thumbnails
  "clips": [{
    "id": "01", "slug": "short-name",
    "start": 12.30, "end": 27.80,          seconds in the source
    "layout": "square",                    full (9:16 crop) | tall (4:5) | square (1:1) | wide (16:9)
    "focus": [[0, 0.5], [4.2, 0.35], [7.0, 0.6, "pan"]],
                                           [clip time, x centre 0..1, "cut" (default) | "pan"]
    "hook": ["LINE ONE", "LINE TWO"],      headline on the clip; last line is accented
    "captions": "auto" | [[s, e, "text"], ...] | null     (times relative to the clip)
    "thumb": {"t": 15.2, "lines": ["BIG", "WORDS"], "focus_x": 0.5, "side": "left"},
    "title": "...", "description": "...", "tags": ["#gta6", ...]
  }]
}
"""
import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 1080, 1920
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
YELLOW, PINK, ORANGE = (255, 214, 10), (255, 46, 136), (255, 138, 0)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(map(str, cmd))[:400]}\n{p.stderr[-2500:]}")
    return p


def probe(video):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", str(video)]).stdout
    st = json.loads(out)["streams"][0]
    n, d = st["r_frame_rate"].split("/")
    return st["width"], st["height"], float(n) / float(d)


# ---------------------------------------------------------------- text (ASS)

def ass_color(rgb):
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def caption_pages(words, max_words=3, max_chars=18, gap=0.55):
    """Group word dicts {w,s,e} into short pages."""
    pages, cur = [], []
    for w in words:
        if cur:
            chars = sum(len(x["w"]) for x in cur) + len(cur) + len(w["w"])
            ends_sentence = cur[-1]["w"][-1:] in ".?!"
            if (len(cur) >= max_words or chars > max_chars
                    or w["s"] - cur[-1]["e"] > gap or ends_sentence):
                pages.append(cur)
                cur = []
        cur.append(w)
    if cur:
        pages.append(cur)
    return pages


def words_for_clip(transcript, start, end, min_prob=0.35):
    out = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            if w["s"] >= start - 0.05 and w["e"] <= end + 0.05 and w.get("p", 1) >= min_prob and w["w"]:
                out.append({"w": w["w"], "s": max(0.0, w["s"] - start), "e": min(end - start, w["e"] - start)})
    return out


def manual_words(lines):
    """[[s, e, "some text"], ...] -> evenly timed words."""
    out = []
    for s, e, text in lines:
        toks = text.split()
        weights = [len(t) + 2 for t in toks]
        total = sum(weights)
        t = s
        for tok, wt in zip(toks, weights):
            d = (e - s) * wt / total
            out.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3)})
            t += d
    return out


GEOMETRY = {            # layout: (picture height, picture top, caption centre y)
    "full": (1920, 0, 1400),
    "tall": (1350, 285, 1420),
    "square": (1080, 500, 1440),
    "wide": (608, 656, 1360),
}
ANTON_LINE = 1.505      # libass font size / em (hhea ascent + descent)
ANTON_ASC = 1.176       # ascent / em
ANTON_CAP = 0.859       # cap height / em


def hook_layout(lines, layout, fonts_dir):
    """Em size and cap-top y of each headline line, sized to fill the band above the picture."""
    fg_h, fg_y, _ = GEOMETRY[layout]
    if fg_y >= 440:
        bottom = fg_y - 34
        band_h = min(bottom - 200, 400)
        top = None
    else:
        top, band_h, bottom = 215, 285, None
    anton = str(Path(fonts_dir) / "Anton-Regular.ttf")
    em = 210
    while em > 40:
        f = ImageFont.truetype(anton, em)
        pitch = ANTON_CAP * em * 1.2
        if max(f.getlength(t) for t in lines) <= 950 and pitch * len(lines) - ANTON_CAP * em * 0.2 <= band_h:
            break
        em -= 2
    pitch = ANTON_CAP * em * 1.2
    block = pitch * len(lines) - ANTON_CAP * em * 0.2
    y0 = top if top is not None else bottom - block
    return em, [y0 + i * pitch for i in range(len(lines))]


def write_ass(path, clip, dur, words, fonts, fonts_dir, handle=None):
    layout = clip.get("layout", "square")
    cap_y = clip.get("cap_y", GEOMETRY[layout][2])
    hook = clip.get("hook") or []

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{fonts['display']},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,8,5,8,30,30,0,1
Style: Cap,{fonts['caption']},98,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,7,3,5,40,40,0,1
Style: Tag,{fonts['caption']},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,3,1,2,30,30,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        em, tops = hook_layout(hook, layout, fonts_dir)
        size = round(em * ANTON_LINE)
        hook_end = clip.get("hook_until", dur)
        for i, (line, cap_top) in enumerate(zip(hook, tops)):
            col = ass_color(YELLOW) if (i == len(hook) - 1 and len(hook) > 1) else ass_color(WHITE)
            y = round(cap_top - (ANTON_ASC - ANTON_CAP) * em)
            d0 = i * 90
            pop = (f"{{\\an8\\pos({W // 2},{y})\\fs{size}\\c{col}&\\fscx70\\fscy70\\alpha&HFF&"
                   f"\\t({d0},{d0 + 1},\\alpha&H00&)\\t({d0},{d0 + 160},\\fscx105\\fscy105)"
                   f"\\t({d0 + 160},{d0 + 260},\\fscx100\\fscy100)}}")
            ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{pop}{line}")
    pages = caption_pages(words)
    for n, page in enumerate(pages):
        page_end = min(dur, page[-1]["e"] + 0.30)
        if n + 1 < len(pages):
            page_end = min(page_end, pages[n + 1][0]["s"])
        for i, w in enumerate(page):
            s = w["s"]
            e = page[i + 1]["s"] if i + 1 < len(page) else page_end
            if e - s < 0.04:
                continue
            toks = []
            for j, x in enumerate(page):
                t = x["w"].upper().replace("{", "(").replace("}", ")")
                toks.append(f"{{\\c{ass_color(YELLOW)}&}}{t}{{\\c&H00FFFFFF&}}" if j == i else t)
            pop = "{\\fscx92\\fscy92\\t(0,70,\\fscx100\\fscy100)}" if i == 0 else ""
            ev.append(f"Dialogue: 2,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{\\pos({W // 2},{cap_y})}}{pop}" + " ".join(toks))
    if handle:
        ev.append(f"Dialogue: 0,{ass_time(0)},{ass_time(dur)},Tag,,0,0,0,,{{\\alpha&H50&}}{handle}")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- video

def focus_expr(focus, iw, cw):
    """ffmpeg expression for crop x from focus keyframes [[t, x, mode], ...]."""
    if not focus:
        focus = [[0, 0.5]]
    keys = [(float(k[0]), float(k[1]), (k[2] if len(k) > 2 else "cut")) for k in focus]

    def px(f):
        return round(min(max(f * iw - cw / 2, 0), iw - cw), 1)

    expr = str(px(keys[-1][1]))
    for i in range(len(keys) - 1, 0, -1):
        t0, f0, _ = keys[i - 1]
        t1, f1, mode = keys[i]
        if mode == "pan" and t1 > t0:
            seg = f"{px(f0)}+({px(f1)}-{px(f0)})*(t-{t0})/({t1}-{t0})"
        else:
            seg = str(px(f0))
        expr = f"if(lt(t,{t1}),{seg},{expr})"
    return expr


def shade_png(path, fg_h):
    """Top/bottom dark gradients so text stays readable on bright footage."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = img.load()
    top, bot = 560, 760
    strength = 150 if fg_h >= 1350 else 90
    for y in range(H):
        a = 0
        if y < top:
            a = int(strength * (1 - y / top) ** 1.5)
        elif y > H - bot:
            a = int((strength + 30) * ((y - (H - bot)) / bot) ** 1.5)
        if a:
            for x in range(W):
                px[x, y] = (0, 0, 0, a)
    img.save(path)


def render_clip(src, clip, out_mp4, work, fonts, fonts_dir, transcript, handle, preview=False):
    iw, ih, fps = probe(src)
    start, end = float(clip["start"]), float(clip["end"])
    dur = round(end - start, 3)
    layout = clip.get("layout", "square")
    fg_h, fg_y, _ = GEOMETRY[layout]
    cw = min(iw, int(round(ih * W / fg_h / 2)) * 2)
    xexpr = focus_expr(clip.get("focus"), iw, cw)

    caps = clip.get("captions")
    if caps == "auto":
        words = words_for_clip(transcript, start, end)
    elif isinstance(caps, list):
        words = manual_words(caps)
    else:
        words = []

    ass = work / f"clip{clip['id']}.ass"
    write_ass(ass, clip, dur, words, fonts, fonts_dir, handle)
    shade = work / f"shade_{fg_h}.png"
    if not shade.exists():
        shade_png(shade, fg_h)

    zoom = float(clip.get("zoom", 1.0))                 # extra punch-in on the foreground
    fg = f"crop=w={cw}:h={ih}:x='{xexpr}':y=0,scale={W}:{fg_h}:flags=lanczos"
    if zoom > 1.0:
        fg = (f"crop=w={int(cw / zoom / 2) * 2}:h={int(ih / zoom / 2) * 2}:"
              f"x='({xexpr})+{(cw - cw / zoom) / 2:.1f}':y={(ih - ih / zoom) / 2:.1f},scale={W}:{fg_h}:flags=lanczos")
    ass_esc = str(ass).replace(":", "\\:")
    fdir_esc = str(fonts_dir).replace(":", "\\:")
    fc = (
        f"[0:v]fps={fps:.3f},split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
        f"scale={W}:{H}:flags=bicubic,eq=brightness=-0.16:saturation=1.2[bg];"
        f"[b]{fg}[fg];"
        f"[bg][fg]overlay=0:{fg_y}[v1];"
        f"[v1][1:v]overlay=0:0[v2];"
        f"color=c=0xFFD60A:s={W}x10:r={fps:.3f}[bar];"
        f"[v2][bar]overlay=x='-{W}+{W}*t/{dur}':y={H - 10}:shortest=1[v3];"
        f"[v3]ass='{ass_esc}':fontsdir='{fdir_esc}'[v4];"
        f"[v4]fade=t=in:st=0:d=0.12,fade=t=out:st={max(0, dur - 0.18):.3f}:d=0.18"
        + (",scale=540:960" if preview else "") + "[v];"
        f"[0:a]afade=t=in:st=0:d=0.10,afade=t=out:st={max(0, dur - 0.35):.3f}:d=0.35,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[au]"
    )
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
           "-loop", "1", "-i", str(shade),
           "-filter_complex", fc, "-map", "[v]", "-map", "[au]", "-t", f"{dur:.3f}",
           "-c:v", "libx264", "-preset", "veryfast" if preview else "medium",
           "-crf", "26" if preview else "20", "-maxrate", "9M", "-bufsize", "18M",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_mp4)]
    run(cmd)
    return dur


# ---------------------------------------------------------------- thumbnails

def grab_frame(src, t, out_png):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src),
         "-frames:v", "1", "-q:v", "1", str(out_png)])
    return Image.open(out_png).convert("RGB")


def cover(img, w, h, fx=0.5, fy=0.5, zoom=1.0):
    s = max(w / img.width, h / img.height) * zoom
    im = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = round(min(max(fx * im.width - w / 2, 0), im.width - w))
    y = round(min(max(fy * im.height - h / 2, 0), im.height - h))
    return im.crop((x, y, x + w, y + h))


def grade(img):
    img = ImageEnhance.Contrast(img).enhance(1.14)
    img = ImageEnhance.Color(img).enhance(1.32)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))


def side_shade(w, h, side, strength=215):
    """Alpha mask that darkens one side (left/right/bottom/top) for the text."""
    g = Image.new("L", (256, 1) if side in ("left", "right") else (1, 256))
    for i in range(256):
        v = int(strength * max(0.0, 1 - i / 185) ** 1.35)
        if side in ("right", "bottom"):
            g.putpixel((255 - i, 0) if side == "right" else (0, 255 - i), v)
        else:
            g.putpixel((i, 0) if side == "left" else (0, i), v)
    return g.resize((w, h), Image.BILINEAR)


def gradient(w, h, c1, c2):
    g = Image.new("RGB", (1, 256))
    for i in range(256):
        g.putpixel((0, i), tuple(round(c1[k] + (c2[k] - c1[k]) * i / 255) for k in range(3)))
    return g.resize((max(1, w), max(1, h)), Image.BILINEAR)


def fit_font(font_path, lines, max_w, max_h, start=400):
    size = start
    while size > 20:
        f = ImageFont.truetype(str(font_path), size)
        widths = [f.getbbox(t)[2] - f.getbbox(t)[0] for t in lines]
        line_h = int(size * 1.02)
        if max(widths) <= max_w and line_h * len(lines) <= max_h:
            return f, line_h
        size -= 4
    return ImageFont.truetype(str(font_path), 20), 22


def draw_title(canvas, lines, box, align, font_path, accent_last=True, tilt=0):
    """Big stroked lines inside box=(x0,y0,x1,y1); the last line gets a pink-to-orange fill."""
    x0, y0, x1, y1 = box
    f, line_h = fit_font(font_path, lines, x1 - x0, y1 - y0)
    sw = max(5, int(f.size * 0.055))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    total_h = line_h * len(lines)
    y = y0 + (y1 - y0 - total_h) // 2 if align[1] == "m" else (y1 - total_h if align[1] == "b" else y0)
    for i, t in enumerate(lines):
        bb = f.getbbox(t)
        tw = bb[2] - bb[0]
        x = x0 if align[0] == "l" else (x1 - tw if align[0] == "r" else x0 + (x1 - x0 - tw) // 2)
        pos = (x - bb[0], y - bb[1] + (line_h - (bb[3] - bb[1])) // 2)
        ds.text((pos[0] + 4, pos[1] + 12), t, font=f, fill=(0, 0, 0, 230), stroke_width=sw + 4, stroke_fill=(0, 0, 0, 230))
        d.text(pos, t, font=f, fill=WHITE, stroke_width=sw, stroke_fill=BLACK)
        if accent_last and i == len(lines) - 1 and len(lines) > 1:
            mask = Image.new("L", canvas.size, 0)
            ImageDraw.Draw(mask).text(pos, t, font=f, fill=255)
            grad = Image.new("RGB", canvas.size)
            grad.paste(gradient(canvas.width, bb[3] - bb[1] + 8, YELLOW, ORANGE), (0, pos[1] + bb[1] - 4))
            layer.paste(grad, (0, 0), mask)
        y += line_h
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    if tilt:
        c = ((x0 + x1) // 2, (y0 + y1) // 2)
        layer = layer.rotate(tilt, resample=Image.BICUBIC, center=c)
        shadow = shadow.rotate(tilt, resample=Image.BICUBIC, center=c)
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(layer)


def draw_chip(canvas, text, xy, font_path, size=54):
    f = ImageFont.truetype(str(font_path), size)
    bb = f.getbbox(text)
    w, h = bb[2] - bb[0] + size, int(size * 1.5)
    x, y = xy
    chip = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(chip)
    d.rounded_rectangle((x + 4, y + 6, x + w + 4, y + h + 6), radius=h // 4, fill=(0, 0, 0, 150))
    grad = gradient(w, h, PINK, ORANGE)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 4, fill=255)
    chip.paste(grad, (x, y), mask)
    d.text((x + size // 2 - bb[0], y + (h - (bb[3] - bb[1])) // 2 - bb[1]), text, font=f, fill=WHITE)
    canvas.alpha_composite(chip)


def make_thumb(frame, clip, out_jpg, fonts_dir, chip):
    """1280x720 thumbnail."""
    th = clip.get("thumb", {})
    side = th.get("side", "left")
    lines = th.get("lines") or clip.get("hook") or []
    tw, thh = 1280, 720
    img = grade(cover(frame, tw, thh, th.get("focus_x", 0.5), th.get("focus_y", 0.5), th.get("zoom", 1.0)))
    canvas = img.convert("RGBA")
    dark = Image.new("RGBA", (tw, thh), (6, 4, 18, 255))
    canvas.paste(dark, (0, 0), side_shade(tw, thh, side))
    canvas.paste(dark, (0, 0), side_shade(tw, thh, "bottom", 120))
    anton = Path(fonts_dir) / "Anton-Regular.ttf"
    if lines:
        if side == "left":
            box, align = (56, 150, 700, 664), "lm"
        elif side == "right":
            box, align = (580, 150, 1224, 664), "rm"
        else:
            box, align = (70, 400, 1210, 680), "cb"
        draw_title(canvas, lines, box, align, anton, tilt=th.get("tilt", 0))
    if chip:
        draw_chip(canvas, chip, (56, 48) if side != "right" else (1280 - 56 - 230, 48), anton)
    canvas.paste(gradient(14, tw, PINK, YELLOW).rotate(90, expand=True), (0, thh - 14))
    canvas.convert("RGB").save(out_jpg, quality=92, optimize=True)


def make_cover(frame, clip, out_jpg, fonts_dir, chip):
    """1080x1920 vertical cover for Shorts / Reels / TikTok."""
    th = clip.get("thumb", {})
    lines = th.get("lines") or clip.get("hook") or []
    img = grade(cover(frame, W, H, th.get("cover_x", th.get("focus_x", 0.5)), 0.5))
    canvas = img.convert("RGBA")
    dark = Image.new("RGBA", (W, H), (6, 4, 18, 255))
    canvas.paste(dark, (0, 0), side_shade(W, H, "bottom", 235))
    canvas.paste(dark, (0, 0), side_shade(W, H, "top", 120))
    anton = Path(fonts_dir) / "Anton-Regular.ttf"
    if lines:
        draw_title(canvas, lines, (70, 1040, 1010, 1500), "cb", anton)
    if chip:
        draw_chip(canvas, chip, (70, 250), anton, size=60)
    canvas.convert("RGB").save(out_jpg, quality=92, optimize=True)


# ---------------------------------------------------------------- sheet

def write_sheet(spec, out_dir, done):
    lines = ["# GTA VI clips — titles, descriptions, thumbnails", ""]
    for c in spec["clips"]:
        base = f"clip{c['id']}_{c['slug']}"
        d = done.get(c["id"], {})
        lines += [
            f"## Clip {c['id']} — {c.get('title', '')}",
            "",
            f"- **Video:** `{base}.mp4`" + (f" ({d['dur']:.1f} s)" if d.get("dur") else ""),
            f"- **Thumbnail (16:9):** `{base}_thumb.jpg`  ·  **Cover (9:16):** `{base}_cover.jpg`",
            f"- **Source time:** {c['start']:.2f}–{c['end']:.2f} s",
            "",
            "**Title**",
            "",
            c.get("title", ""),
            "",
            "**Description**",
            "",
            c.get("description", ""),
            "",
            " ".join(c.get("tags", [])),
            "",
            spec.get("credit", ""),
            "",
        ]
    (out_dir / "clips.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-thumbs", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).resolve()
    spec = json.loads((proj / "clips.json").read_text(encoding="utf-8"))
    src = (proj / spec["source"]).resolve()
    work, out = proj / "work", proj / "output"
    work.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    fonts_dir = proj / "fonts"
    fonts = {"display": "Anton", "caption": spec.get("caption_font", "Montserrat ExtraBold")}
    tpath = work / "transcript.json"
    transcript = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else {"segments": []}
    only = {x.strip() for x in a.only.split(",") if x.strip()}

    done = {}
    for c in spec["clips"]:
        if only and c["id"] not in only:
            continue
        base = f"clip{c['id']}_{c['slug']}"
        info = {}
        if not a.no_video:
            mp4 = out / (base + ("_preview.mp4" if a.preview else ".mp4"))
            info["dur"] = render_clip(src, c, mp4, work, fonts, fonts_dir, transcript, spec.get("handle"), a.preview)
            print(f"clip {c['id']}: {mp4.name}  {info['dur']:.1f} s  {mp4.stat().st_size / 1e6:.1f} MB", flush=True)
        if not a.no_thumbs:
            t = float(c.get("thumb", {}).get("t", (c["start"] + c["end"]) / 2))
            frame = grab_frame(src, t, work / f"frame{c['id']}.png")
            make_thumb(frame, c, out / f"{base}_thumb.jpg", fonts_dir, spec.get("chip"))
            make_cover(frame, c, out / f"{base}_cover.jpg", fonts_dir, spec.get("chip"))
            print(f"clip {c['id']}: thumbnail + cover", flush=True)
        done[c["id"]] = info
    write_sheet(spec, out, done)
    print(f"sheet: {out / 'clips.md'}")


if __name__ == "__main__":
    main()
