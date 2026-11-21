#!/usr/bin/env python3
"""Render the ten locally produced Teardown Room Field Notes.

Each note is a short sequence of original 1280x720 cards, a local Windows
OneCore voice, WebVTT captions, a poster, a transcript, a download manifest and
a YouTube review draft. The series uses three installed local voices in ten
presenter treatments; no external TTS, third-party footage, music or audio is
used, and nothing is uploaded.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import field_notes
import studio

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "site" / "assets" / "field-notes"
WIDTH, HEIGHT = 1280, 720
GAP_SECONDS = 0.55


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int],
                  font: ImageFont.FreeTypeFont, fill: str, width: int,
                  line_height: int, max_lines: int | None = None) -> int:
    lines = _wrap(draw, text, font, width)
    if max_lines:
        lines = lines[:max_lines]
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _background(accent: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#070a0f")
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        draw.line((0, y, WIDTH, y),
                  fill=(int(7 + 8 * ratio), int(10 + 14 * ratio), int(15 + 22 * ratio)))
    for x in range(0, WIDTH, 64):
        draw.line((x, 0, x, HEIGHT), fill="#0d1822", width=1)
    for y in range(0, HEIGHT, 64):
        draw.line((0, y, WIDTH, y), fill="#0d1822", width=1)
    glow = _rgb(accent)
    draw.ellipse((-180, -260, 520, 420),
                 fill=tuple(max(0, int(channel * 0.22)) for channel in glow))
    draw.ellipse((880, 430, 1420, 980), fill="#1a1620")
    return image


def _slide(note: dict, index: int, caption: dict, pub: dict, preset: dict) -> Image.Image:
    image = _background(preset["accent"])
    draw = ImageDraw.Draw(image)
    mono = _font(15)
    mono_small = _font(12)
    sans = _font(25)
    bold = _font(44, bold=True)
    title_bold = _font(55, bold=True)
    accent = preset["accent"]
    text = "#eaf4ff"
    dim = "#a8bfd2"
    faint = "#7f96aa"

    draw.rectangle((0, 0, 10, HEIGHT), fill=accent)
    draw.text((64, 46), "THE TEARDOWN ROOM", font=mono, fill=accent)
    draw.text((64, 68), f'FIELD NOTE {note["number"]}  /  NARRATED EVIDENCE BRIEF',
              font=mono_small, fill=faint)
    right = f"{index:02d} / {len(note['captions']):02d}"
    draw.text((WIDTH - 64 - draw.textlength(right, font=mono), 46), right, font=mono, fill=faint)
    draw.line((64, 105, WIDTH - 64, 105), fill="#263746", width=1)

    if index == 1:
        y = _draw_wrapped(draw, note["title"], (64, 154), title_bold, text, 1090, 64, 3)
        if y < 300:
            y = 300
        _draw_wrapped(draw, note["kicker"], (64, y + 10), sans, accent, 1040, 38, 2)
    else:
        y = _draw_wrapped(draw, caption["head"], (64, 160), bold, text, 1090, 53, 3)
        if y < 235:
            y = 235
        _draw_wrapped(draw, caption["body"], (64, y + 8), sans, dim, 1040, 38, 5)

    base_y = 586
    draw.line((64, base_y, WIDTH - 64, base_y), fill="#263746", width=1)
    total = len(note["captions"])
    for node in range(total):
        x = 64 + node * ((WIDTH - 128) / max(1, total - 1))
        draw.ellipse((x - 5, base_y - 5, x + 5, base_y + 5),
                     fill=accent if node <= index - 1 else "#24323e")
    draw.text((64, 612), str(pub.get("dossier_id") or "dossier pending"),
              font=mono_small, fill=faint)
    footer = "EVIDENCE FIRST  ·  STORY SECOND  ·  SALE LAST"
    draw.text((WIDTH - 64 - draw.textlength(footer, font=mono_small), 612),
              footer, font=mono_small, fill=faint)
    draw.rectangle((64, HEIGHT - 30, WIDTH - 64, HEIGHT - 26), fill="#101d28")
    progress = int((WIDTH - 128) * (index / total))
    draw.rectangle((64, HEIGHT - 30, 64 + progress, HEIGHT - 26), fill=accent)
    return image


def _speak(caption: dict, path: Path, preset: dict, note: dict) -> tuple[float, str]:
    """Write one soft, presenter-paced OneCore cue plus a short breath."""
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is required for the Windows OneCore voices")
    script = Path(__file__).with_name("onecore_tts.ps1")
    if not script.is_file():
        raise RuntimeError(f"OneCore speech helper is missing: {script}")
    spoken = caption.get("say") or f"{caption['head']}. {caption['body']}"
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-Text", spoken, "-Voice", preset["voice"],
        "-Rate", f"{float(preset['rate']):.2f}",
        "-Pitch", f"{float(preset['pitch']):.3f}",
        "-Out", str(path.resolve()),
    ], check=True, capture_output=True, text=True)
    if not path.is_file():
        raise RuntimeError(f"OneCore speech did not write {path}: {result.stderr}")
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        frames = source.readframes(source.getnframes())
    gap = b"\x00" * int(params.framerate * GAP_SECONDS) * params.sampwidth * params.nchannels
    with wave.open(str(path), "wb") as target:
        target.setparams(params)
        target.writeframes(frames + gap)
    duration = (len(frames) + len(gap)) / (params.framerate * params.sampwidth * params.nchannels)
    return duration, preset["voice"]


def _join_audio(paths: list[Path], out: Path) -> tuple[float, tuple]:
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
        frames = first.readframes(first.getnframes())
    for path in paths[1:]:
        with wave.open(str(path), "rb") as source:
            if source.getparams()[:3] != params[:3]:
                raise RuntimeError("voice cues do not share a WAV format")
            frames += source.readframes(source.getnframes())
    with wave.open(str(out), "wb") as target:
        target.setparams(params)
        target.writeframes(frames)
    return len(frames) / (params.framerate * params.sampwidth * params.nchannels), params


def _tone_filters(tone: str) -> list[str]:
    return {
        "warm": [
            "equalizer=f=180:t=q:w=1:g=1.6",
            "equalizer=f=3200:t=q:w=1:g=-1.2",
            "aecho=0.8:0.5:34:0.09",
        ],
        "bright": [
            "equalizer=f=220:t=q:w=1:g=-0.8",
            "equalizer=f=3400:t=q:w=1:g=1.8",
            "aecho=0.8:0.45:24:0.06",
        ],
        "deep": [
            "equalizer=f=150:t=q:w=1:g=3.0",
            "equalizer=f=2800:t=q:w=1:g=-2.2",
            "aecho=0.8:0.48:52:0.13",
        ],
        "soft": [
            "lowpass=f=7600",
            "equalizer=f=300:t=q:w=1:g=0.8",
            "aecho=0.8:0.45:46:0.14",
        ],
        "crisp": [
            "highpass=f=110",
            "equalizer=f=3900:t=q:w=1:g=0.9",
            "equalizer=f=700:t=q:w=1:g=0.7",
        ],
        "forward": [
            "equalizer=f=1200:t=q:w=1:g=1.1",
            "equalizer=f=3000:t=q:w=1:g=1.0",
            "aecho=0.8:0.4:20:0.04",
        ],
        "wide": [
            "equalizer=f=160:t=q:w=1:g=1.0",
            "lowpass=f=8600",
            "aecho=0.8:0.55:70:0.20",
        ],
        "close": [
            "highpass=f=130",
            "equalizer=f=200:t=q:w=1:g=1.2",
            "aecho=0.8:0.35:18:0.04",
        ],
        "light": [
            "equalizer=f=200:t=q:w=1:g=-1.0",
            "equalizer=f=3600:t=q:w=1:g=1.4",
            "aecho=0.8:0.4:26:0.07",
        ],
        "plain": [
            "equalizer=f=260:t=q:w=1:g=0.5",
            "aecho=0.8:0.42:30:0.06",
        ],
    }.get(tone, [])


def _polish_voice(source: Path, out: Path, duration: float, preset: dict) -> None:
    """Warm, pitch-shift and soften the local voice without changing its timing."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to polish the narration")
    filters = [
        "aresample=44100",
        "highpass=f=80",
        "lowpass=f=9000",
    ]
    filters.extend(_tone_filters(str(preset["tone"])))
    filters.extend([
        "acompressor=threshold=0.10:ratio=2.2:attack=15:release=260",
        "loudnorm=I=-19:TP=-2:LRA=7",
    ])
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-af", ",".join(filters), "-ar", "44100", "-ac", "1",
        "-t", f"{duration:.3f}", str(out),
    ], check=True)


def _ambient_bed(duration: float, out: Path, preset: dict) -> None:
    """Build a low, original sine and pink-noise room bed for this note."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to build the ambient bed")
    fade_out = max(0.0, duration - 4.0)
    base = float(preset["bed"])
    graph = (
        "[0:a]volume=0.10[a];"
        "[1:a]volume=0.07[b];"
        "[2:a]lowpass=f=900,volume=0.05[c];"
        "[a][b][c]amix=inputs=3:duration=longest:normalize=0,"
        f"afade=t=in:st=0:d=3,afade=t=out:st={fade_out:.3f}:d=4,volume=0.30"
    )
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={base:.1f}:duration={duration:.3f}",
        "-f", "lavfi", "-i", f"sine=frequency={base * 1.5:.1f}:duration={duration:.3f}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.015:duration={duration:.3f}",
        "-filter_complex", graph, "-ar", "44100", "-ac", "1",
        "-t", f"{duration:.3f}", str(out),
    ], check=True)


def _mix_voice_and_bed(voice: Path, bed: Path, out: Path, duration: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to mix the narration")
    graph = (
        "[0:a]volume=1.0[v];"
        "[1:a]volume=0.14[b];"
        "[v][b]amix=inputs=2:duration=longest:normalize=0,"
        "alimiter=limit=0.95"
    )
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(voice), "-i", str(bed),
        "-filter_complex", graph, "-ar", "44100", "-ac", "1",
        "-t", f"{duration:.3f}", str(out),
    ], check=True)


def _stamp(seconds: float) -> str:
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    return f"00:{whole // 60:02d}:{whole % 60:02d}.{millis:03d}"


def _vtt(note: dict, durations: list[float]) -> str:
    lines = ["WEBVTT", ""]
    elapsed = 0.0
    for caption, duration in zip(note["captions"], durations):
        lines += [
            f"{_stamp(elapsed)} --> {_stamp(elapsed + duration)}",
            caption.get("say") or f"{caption['head']} {caption['body']}",
            "",
        ]
        elapsed += duration
    return "\n".join(lines)


def _transcript(note: dict, pub: dict) -> str:
    out = [
        f'# Field note {note["number"]} — {note["title"]}',
        "",
        "Narrated captioned evidence brief. Original generated diagrams and a local synthetic voice; no third-party footage, music or audio.",
        f"Dossier: `{pub.get('dossier_id') or 'not yet published'}`",
        f"Voice treatment: {field_notes.voice_label(note)}",
        "",
        note["dek"],
        "",
    ]
    for index, caption in enumerate(note["captions"], start=1):
        out += [f"## {index:02d} · {caption['head']}", "", caption.get("say") or caption["body"], ""]
    return "\n".join(out)


def _youtube(note: dict, pub: dict) -> str:
    return f"""# YouTube metadata draft

**Status:** rendered locally; not uploaded. Uploading needs a separate authorised action and an account.

## Title

The Teardown Room — field note {note['number']}: {note['title']}

## Description

A short narrated captioned field note from The Teardown Room.

{note['dek']}

Voice treatment: {field_notes.voice_label(note)}. It is a synthetic local
Windows voice, not a recorded presenter. No third-party footage, music, audio
or client footage is used.

Dossier: {pub.get('dossier_id') or 'not yet published'}
Evidence: /published/publications/{pub.get('slug') or ''}/
Fact files: /published/facts/
Method: /published/method/
Field notes: /published/watch/

No dossier, no publication. Fixtures and demos are refused. The hash is a
receipt, not a claim that every conclusion is correct.

## Tags

reverse engineering, evidence, teardown, software analysis, fact files,
transparency, static site

## Visibility

Unlisted by default. Do not publish until the operator has reviewed the video,
captions, voiceover and claims.
"""


def build(out: Path = DEFAULT_OUT, only: str | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pubs = studio.published()
    pub = pubs[0] if pubs else {}
    selected = [note for note in field_notes.FIELD_NOTES if only in (None, note["number"])]
    if not selected:
        raise SystemExit(f"no field note numbered {only!r}")
    manifest_notes: list[dict] = []

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to render the field notes")

    for note in selected:
        preset = field_notes.VOICE_PRESETS[note["voice"]]
        note_out = out / note["number"]
        frames = note_out / "frames"
        audio_dir = note_out / "audio"
        frames.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        images: list[Path] = []
        audio_paths: list[Path] = []
        durations: list[float] = []
        voice_name = preset["voice"]

        for index, caption in enumerate(note["captions"], start=1):
            image_path = frames / f"{index:02d}.png"
            _slide(note, index, caption, pub, preset).save(image_path)
            images.append(image_path)
            audio_path = audio_dir / f"{index:02d}.wav"
            duration, voice_name = _speak(caption, audio_path, preset, note)
            audio_paths.append(audio_path)
            durations.append(duration)

        narration = note_out / "narration.wav"
        total_duration, _ = _join_audio(audio_paths, narration)
        soft_voice = note_out / "narration-soft.wav"
        bed = note_out / "ambient-bed.wav"
        final_audio = note_out / "narration-mix.wav"
        _polish_voice(narration, soft_voice, total_duration, preset)
        _ambient_bed(total_duration, bed, preset)
        _mix_voice_and_bed(soft_voice, bed, final_audio, total_duration)
        shutil.copyfile(images[0], note_out / "poster.png")
        (note_out / "field-note.vtt").write_text(_vtt(note, durations), "utf-8")
        (note_out / "transcript.md").write_text(_transcript(note, pub), "utf-8")
        (note_out / "youtube.md").write_text(_youtube(note, pub), "utf-8")

        concat = note_out / "frames.txt"
        lines: list[str] = []
        for path, duration in zip(images, durations):
            lines += [f"file '{path.as_posix()}'", f"duration {duration:.6f}"]
        lines.append(f"file '{images[-1].as_posix()}'")
        concat.write_text("\n".join(lines) + "\n", "utf-8")

        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-i", str(final_audio),
            "-vf", "fps=30,format=yuv420p", "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", str(note_out / "field-note.mp4"),
        ]
        subprocess.run(command, check=True)

        manifest = {
            "number": note["number"],
            "slug": note["slug"],
            "title": note["title"],
            "kicker": note["kicker"],
            "dek": note["dek"],
            "duration_seconds": round(total_duration, 3),
            "width": WIDTH,
            "height": HEIGHT,
            "audio": True,
            "voice": voice_name,
            "voice_treatment": field_notes.voice_label(note),
            "voice_base": preset["voice"],
            "pitch_ratio": preset["pitch"],
            "sound_design": "original low sine/pink-noise ambient bed, mixed under the voice",
            "captions": "field-note.vtt",
            "poster": "poster.png",
            "video": "field-note.mp4",
            "source_dossier": pub.get("dossier_id"),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "note": "Original generated diagrams, local synthetic voiceover and an original ambient bed. Not a recorded teardown. Not uploaded.",
        }
        (note_out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        manifest_notes.append(manifest)

    index = {
        "series": "The Teardown Room — Field Notes",
        "base_voices": 3,
        "treatments": len(field_notes.FIELD_NOTES),
        "source_dossier": pub.get("dossier_id"),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "notes": manifest_notes,
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="render one note by its two-digit number, e.g. 03")
    args = parser.parse_args()
    print(json.dumps(build(only=args.only), ensure_ascii=False, indent=2))
