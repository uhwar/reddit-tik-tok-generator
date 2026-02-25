"""
Video Generator Module
Handles TTS, subtitle generation, and video composition for Reddit story videos.
Output: 1080x1920 (9:16) YouTube Shorts / TikTok format.
Uses moviepy 2.x API.
"""

import os
import re
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
import imageio_ffmpeg
from moviepy import (
    VideoFileClip, AudioFileClip, CompositeVideoClip,
    ImageClip, concatenate_videoclips
)
import whisper

# Directories
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BG_VIDEO_DIR = os.path.join(BACKEND_DIR, 'background_videos')
OUTPUT_DIR = os.path.join(os.path.dirname(BACKEND_DIR), 'output_videos')

# Output resolution: YouTube Shorts / TikTok 9:16
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# Font path - full path required on Windows for Pillow/moviepy
FONT_PATH = 'C:/Windows/Fonts/impact.ttf'

# gTTS speaks ~150 words per minute
WORDS_PER_MINUTE = 150

# Duration modes: label -> max seconds, allow_split controls whether long
# stories are split into parts (True) or truncated to 1 video (False)
DURATION_MODES = {
    "1": {"label": "Under 2 minutes", "max_seconds": 120, "allow_split": False},
    "2": {"label": "Under 3 minutes", "max_seconds": 180, "allow_split": False},
    "3": {"label": "Under 5 minutes", "max_seconds": 300, "allow_split": True},
}

# Available TTS voices via gTTS
VOICE_OPTIONS = {
    "1": {"name": "US English (Female)",        "lang": "en", "tld": "com"},
    "2": {"name": "UK English (Female)",         "lang": "en", "tld": "co.uk"},
    "3": {"name": "Australian English (Female)", "lang": "en", "tld": "com.au"},
    "4": {"name": "Indian English (Female)",     "lang": "en", "tld": "co.in"},
    "5": {"name": "Canadian English (Female)",   "lang": "en", "tld": "ca"},
}


def list_background_videos():
    """Return list of available background video files."""
    os.makedirs(BG_VIDEO_DIR, exist_ok=True)
    exts = ('.mp4', '.mov', '.avi', '.mkv')
    return [f for f in os.listdir(BG_VIDEO_DIR) if f.lower().endswith(exts)]


def estimate_duration_seconds(text):
    """Estimate TTS duration in seconds based on word count at ~150 wpm."""
    word_count = len(text.split())
    return (word_count / WORDS_PER_MINUTE) * 60


def split_text_into_parts(text, max_seconds):
    """
    Split narration text into parts that each fit within max_seconds.
    Splits on sentence boundaries where possible.
    Returns a list of text strings, one per part.
    """
    max_words = int((max_seconds / 60) * WORDS_PER_MINUTE)

    # Split into sentences first
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    parts = []
    current_words = []
    current_count = 0

    for sentence in sentences:
        s_words = sentence.split()
        if current_count + len(s_words) > max_words and current_words:
            parts.append(' '.join(current_words))
            current_words = s_words
            current_count = len(s_words)
        else:
            current_words.extend(s_words)
            current_count += len(s_words)

    if current_words:
        parts.append(' '.join(current_words))

    return parts


def _truncate_to_duration(text, max_seconds):
    """
    Keep as many complete sentences as fit within max_seconds.
    Always returns at least one sentence so the video is never empty.
    """
    max_words = int((max_seconds / 60) * WORDS_PER_MINUTE)
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    kept = []
    count = 0
    for sentence in sentences:
        w = len(sentence.split())
        if count + w > max_words and kept:
            break
        kept.append(sentence)
        count += w
    return ' '.join(kept) if kept else sentences[0]


# Narration playback speed multiplier (1.5 = 50% faster, matching TikTok pacing)
NARRATION_SPEED = 1.5


def _speed_up_audio(input_path, output_path, speed=NARRATION_SPEED):
    """
    Speed up audio by `speed` factor using ffmpeg's atempo filter.
    Uses the imageio-ffmpeg binary directly — no system ffmpeg needed.
    atempo supports 0.5-2.0; chain two filters for speeds > 2.0.
    """
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    # Build atempo filter chain (each atempo capped at 2.0)
    if speed <= 2.0:
        atempo = f"atempo={speed}"
    else:
        # e.g. 3x = atempo=2.0,atempo=1.5
        atempo = f"atempo=2.0,atempo={speed/2.0:.4f}"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", input_path,
        "-filter:a", atempo,
        "-vn",
        output_path
    ]
    import subprocess
    subprocess.run(cmd, check=True, capture_output=True)


def generate_tts(text, voice_key, output_path, speed=NARRATION_SPEED):
    """Generate TTS audio using gTTS with the selected voice, then speed it up."""
    voice = VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS["1"])
    tts = gTTS(text=text, lang=voice["lang"], tld=voice["tld"])
    raw_path = output_path + ".raw.mp3"
    tts.save(raw_path)
    if speed != 1.0:
        _speed_up_audio(raw_path, output_path, speed=speed)
        try:
            os.remove(raw_path)
        except Exception:
            pass
    else:
        os.rename(raw_path, output_path)
    return output_path


_whisper_model = None

def _get_whisper_model():
    """Load Whisper model once and cache it."""
    global _whisper_model
    if _whisper_model is None:
        print("  Loading Whisper model (first run only)...")
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def transcribe_with_whisper(audio_path):
    """
    Transcribe audio using Whisper with word-level timestamps.
    Decodes the MP3 via moviepy (avoids Whisper's ffmpeg subprocess which
    fails when ffmpeg is not on PATH). Passes float32 numpy array directly.
    Returns a flat list of {word, start, end} dicts.
    """
    model = _get_whisper_model()

    # Decode audio using moviepy's AudioFileClip (uses imageio-ffmpeg binary,
    # avoids Whisper's own ffmpeg subprocess call which fails on Windows PATH)
    WHISPER_SR = 16000  # Whisper expects 16 kHz mono float32
    clip = AudioFileClip(audio_path)
    native_sr = clip.fps  # e.g. 22050 or 44100
    # to_soundarray returns (n_samples, n_channels) at native sample rate
    raw = clip.to_soundarray(fps=native_sr)
    clip.close()

    # Mix to mono
    if raw.ndim == 2:
        mono = raw.mean(axis=1)
    else:
        mono = raw

    # Resample from native_sr to WHISPER_SR via linear interpolation
    original_len = len(mono)
    target_len = int(original_len * WHISPER_SR / native_sr)
    x_orig = np.linspace(0, 1, original_len)
    x_new  = np.linspace(0, 1, target_len)
    audio_array = np.interp(x_new, x_orig, mono).astype(np.float32)

    # Normalise to [-1, 1] (Whisper expects this range)
    peak = np.abs(audio_array).max()
    if peak > 0:
        audio_array = audio_array / peak

    result = model.transcribe(audio_array, word_timestamps=True, language="en",
                              fp16=False)
    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })
    return words


def build_subtitle_chunks_from_words(whisper_words, max_words_per_chunk=14):
    """
    Group Whisper word-timestamp dicts into subtitle chunks.
    Breaks at sentence-ending punctuation first, then at max_words_per_chunk.
    Returns list of {text, start, end} dicts with exact timestamps.
    """
    if not whisper_words:
        return []

    chunks = []
    current_words = []
    current_start = None

    for w in whisper_words:
        word = w["word"]
        if not word:
            continue
        if current_start is None:
            current_start = w["start"]
        current_words.append(w)

        # Break on sentence-ending punctuation or max word count
        ends_sentence = word.endswith(('.', '!', '?'))
        at_max = len(current_words) >= max_words_per_chunk

        if ends_sentence or at_max:
            text = ' '.join(cw["word"] for cw in current_words)
            chunks.append({
                "text": text,
                "start": current_start,
                "end": current_words[-1]["end"],
            })
            current_words = []
            current_start = None

    # Flush any remaining words
    if current_words:
        text = ' '.join(cw["word"] for cw in current_words)
        chunks.append({
            "text": text,
            "start": current_start,
            "end": current_words[-1]["end"],
        })

    return chunks


def _wrap_text(text, font, draw, max_px_width, max_lines=3):
    """
    Word-wrap text so each line fits within max_px_width pixels.
    Returns a list of line strings (at most max_lines).
    All words are always included — if the last line overflows slightly
    that is acceptable to avoid dropping words.
    """
    words = text.split()
    lines = []
    current = []

    for i, word in enumerate(words):
        test = ' '.join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        w = bbox[2] - bbox[0]
        if w > max_px_width and current:
            lines.append(' '.join(current))
            if len(lines) >= max_lines - 1:
                # Last allowed line — absorb ALL remaining words so nothing is dropped
                current = words[i:]
                break
            current = [word]
        else:
            current.append(word)

    if current:
        lines.append(' '.join(current))

    return lines


def _draw_subtitle_image(text, vid_w, font_path, font_size=66, stroke_width=5):
    """
    Render a subtitle string to a transparent RGBA numpy array using PIL.
    Properly word-wraps into up to 3 lines within safe horizontal margins.
    Drawn once per chunk — far faster than TextClip which redraws every frame.
    """
    font = ImageFont.truetype(font_path, font_size)

    # Safe horizontal margins: 80px each side inside the 1080px frame
    H_MARGIN = 80
    box_w = vid_w - (H_MARGIN * 2)

    # Measure line height using a tall reference character
    dummy_img = Image.new('RGBA', (box_w, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)
    ref_bbox = dummy_draw.textbbox((0, 0), 'Agpqy', font=font, stroke_width=stroke_width)
    line_h = ref_bbox[3] - ref_bbox[1]
    line_spacing = int(line_h * 0.25)  # 25% extra between lines

    lines = _wrap_text(text, font, dummy_draw, box_w, max_lines=3)
    n_lines = len(lines)

    # Total image height: lines + spacing + top/bottom padding for descenders
    V_PAD = stroke_width + 14
    total_h = n_lines * line_h + (n_lines - 1) * line_spacing + V_PAD * 2

    img = Image.new('RGBA', (box_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = V_PAD
    for line in lines:
        lb = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        line_w = lb[2] - lb[0]
        x = (box_w - line_w) // 2  # center each line

        # Black stroke/outline
        draw.text((x, y), line, font=font,
                  fill=(0, 0, 0, 255),
                  stroke_width=stroke_width,
                  stroke_fill=(0, 0, 0, 255))
        # White fill
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

        y += line_h + line_spacing

    return np.array(img)


def build_subtitle_clips(whisper_chunks, audio_duration, vid_w, vid_h):
    """
    Build ImageClip subtitle objects from Whisper word-timestamp chunks.
    Each chunk has exact {text, start, end} from real audio timestamps.
    Clips are strictly non-overlapping: each end == next start.
    Positioned at 85% down the screen.
    """
    if not whisper_chunks:
        return []

    subtitle_y = int(vid_h * 0.85) - 40
    clips = []

    for i, chunk in enumerate(whisper_chunks):
        start = chunk["start"]
        # End = next chunk's start (hard cut, no overlap), or audio end
        if i + 1 < len(whisper_chunks):
            end = whisper_chunks[i + 1]["start"]
        else:
            end = audio_duration
        end = max(end, start + 0.05)  # minimum 50ms safety

        frame = _draw_subtitle_image(chunk["text"], vid_w, FONT_PATH)
        clip = (
            ImageClip(frame, is_mask=False)
            .with_start(start)
            .with_end(end)
            .with_position(('center', subtitle_y))
        )
        clips.append(clip)

    return clips


def crop_to_916(clip):
    """Crop clip to 9:16 aspect ratio, center-cropped."""
    target_ratio = 9 / 16
    w, h = clip.size
    current_ratio = w / h

    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x1 = (w - new_w) // 2
        return clip.cropped(x1=x1, x2=x1 + new_w)
    else:
        new_h = int(w / target_ratio)
        y1 = (h - new_h) // 2
        return clip.cropped(y1=y1, y2=y1 + new_h)


def _render_single_video(narration_text, voice_key, bg_video_path, output_path):
    """
    Core render: TTS + subtitles + background -> one MP4 file.
    Returns output_path on success, None on failure.
    """
    audio_path = os.path.join(BACKEND_DIR, '_temp_narration.mp3')
    audio_clip = None
    final = None
    try:
        generate_tts(narration_text, voice_key, audio_path)
        audio_clip = AudioFileClip(audio_path)
        audio_duration = audio_clip.duration

        bg = VideoFileClip(bg_video_path)
        if bg.duration < audio_duration:
            loops = int(audio_duration / bg.duration) + 1
            bg = concatenate_videoclips([bg] * loops)
        max_start = max(0, bg.duration - audio_duration)
        start = random.uniform(0, max_start)
        bg = bg.subclipped(start, start + audio_duration)
        bg = crop_to_916(bg)
        bg = bg.resized((OUTPUT_WIDTH, OUTPUT_HEIGHT))

        # Whisper transcription for exact word-level timestamps
        print("  Transcribing audio for subtitle sync...")
        whisper_words = transcribe_with_whisper(audio_path)
        whisper_chunks = build_subtitle_chunks_from_words(whisper_words)
        subtitle_clips = build_subtitle_clips(whisper_chunks, audio_duration, OUTPUT_WIDTH, OUTPUT_HEIGHT)

        final = CompositeVideoClip([bg] + subtitle_clips, size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))
        final = final.with_audio(audio_clip)
        final = final.with_duration(audio_duration)

        final.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            fps=30,
            threads=8,
            preset='ultrafast',
            logger=None
        )
        return output_path
    except Exception as e:
        print(f"  Render error: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        if audio_clip:
            try: audio_clip.close()
            except Exception: pass
        if final:
            try: final.close()
            except Exception: pass
        if os.path.exists(audio_path):
            try: os.remove(audio_path)
            except Exception: pass


def generate_video(story, voice_key, bg_video_filename, output_filename=None, max_seconds=120, allow_split=False):
    """
    Full pipeline: TTS -> subtitles -> video composition.

    If allow_split is False (default for under-2-min / under-3-min modes):
      - Story is truncated at sentence boundaries to fit within max_seconds.
      - Always outputs exactly 1 video.
    If allow_split is True (under-5-min mode):
      - Story is split into numbered parts, each within max_seconds.

    Returns:
        List of output video paths (one per part), or empty list on failure.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    bg_video_path = os.path.join(BG_VIDEO_DIR, bg_video_filename)
    if not os.path.exists(bg_video_path):
        print(f"  Background video not found: {bg_video_path}")
        return []

    # Build and clean narration text
    narration_text = f"{story['title']}. {story['full_body']}"
    narration_text = re.sub(r'\*+', '', narration_text)
    narration_text = re.sub(r'#+\s*', '', narration_text)
    narration_text = re.sub(r'\[.*?\]\(.*?\)', '', narration_text)
    narration_text = re.sub(r'\n+', ' ', narration_text).strip()

    # Base filename
    if not output_filename:
        safe_title = re.sub(r'[^\w\s-]', '', story['title'])[:40].strip().replace(' ', '_')
        output_filename = safe_title

    estimated = estimate_duration_seconds(narration_text)

    if not allow_split:
        # Truncate to 1 video: keep only as many sentences as fit within max_seconds
        if estimated > max_seconds:
            narration_text = _truncate_to_duration(narration_text, max_seconds)
            new_est = estimate_duration_seconds(narration_text)
            print(f"  Story truncated to ~{new_est:.0f}s to fit 1 video (was ~{estimated:.0f}s)")
        else:
            print(f"  Story fits in one video (~{estimated:.0f}s)")
        parts = [narration_text]
    else:
        parts = split_text_into_parts(narration_text, max_seconds) if estimated > max_seconds else [narration_text]
        total_parts = len(parts)
        if total_parts > 1:
            print(f"  Story is ~{estimated:.0f}s — splitting into {total_parts} parts (max {max_seconds}s each)")
        else:
            print(f"  Story fits in one video (~{estimated:.0f}s)")

    total = len(parts)
    output_paths = []
    for i, part_text in enumerate(parts, 1):
        if total > 1:
            part_narration = f"Part {i} of {total}. {part_text}"
            fname = f"{output_filename}_part{i}.mp4"
        else:
            part_narration = part_text
            fname = f"{output_filename}.mp4"

        out = os.path.join(OUTPUT_DIR, fname)
        print(f"\n  Rendering {'part ' + str(i) + '/' + str(total) if total > 1 else 'video'}: {fname}")
        result = _render_single_video(part_narration, voice_key, bg_video_path, out)
        if result:
            output_paths.append(result)
        else:
            print(f"  Failed to render part {i}")

    return output_paths
