#!/usr/bin/env python3
"""
Video / Frame Sequence Rate Interpolator using FFmpeg
Converts video or image-sequence framerates (e.g. 24 fps -> 60 fps)
using motion-compensated interpolation.

Supported pipelines:
  - video file   -> video file
  - frame folder -> frame folder   (single pass, no intermediate video encode)
  - frame folder -> video file
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque


IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
    ".exr", ".webp", ".dpx", ".tga", ".ppm", ".pgm",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".wmv", ".flv",
}


def check_ffmpeg_installed() -> bool:
    """Verifies that ffmpeg and ffprobe are available in PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def get_video_info(input_path: str) -> dict:
    """Extracts duration, resolution, and current framerate using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,width,height,duration,nb_frames:format=duration",
        "-of", "json",
        input_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to probe video info: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    stream = data.get("streams", [{}])[0]
    format_info = data.get("format", {})

    # Extract duration
    duration = float(stream.get("duration") or format_info.get("duration") or 0.0)

    # Extract FPS
    r_fps = stream.get("r_frame_rate", "24/1")
    if "/" in r_fps:
        num, den = map(float, r_fps.split("/"))
        fps = num / den if den != 0 else 24.0
    else:
        fps = float(r_fps)

    return {
        "duration": duration,
        "fps": fps,
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def format_time(seconds: float) -> str:
    """Formats seconds into HH:MM:SS string."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def detect_frame_sequence(directory: str) -> dict:
    """
    Detects a numbered frame sequence inside a folder.

    Recognises names like '600.png', '0600.png', 'frame_00600.jpg' or bare
    numbers such as '600', '601'. Returns metadata needed by FFmpeg's image2
    demuxer (printf pattern, padding, first/last number), or raises ValueError.
    """
    name_re = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)(?P<ext>\.[A-Za-z0-9]+)?$")
    groups = defaultdict(list)  # (prefix, ext) -> [(number, stem)]

    for name in sorted(os.listdir(directory)):
        if not os.path.isfile(os.path.join(directory, name)):
            continue
        m = name_re.match(name)
        if not m:
            continue
        ext = (m.group("ext") or "").lower()
        if ext and ext not in IMAGE_EXTENSIONS:
            continue  # ignore non-image files sharing the same numbering
        groups[(m.group("prefix"), ext)].append((int(m.group("num")), m.group("num")))

    if not groups:
        raise ValueError(
            f"No numbered image frames found in '{directory}'. "
            f"Expected files like '600.png', '601.png', ... "
            f"(supported extensions: {', '.join(sorted(IMAGE_EXTENSIONS))}, or none)."
        )

    # Pick the largest identically-named group of frames
    (prefix, ext), frames = max(groups.items(), key=lambda kv: len(kv[1]))
    pairs = sorted(frames)
    numbers = [n for n, _ in pairs]
    stems = [s for _, s in pairs]

    if len(numbers) < 2:
        raise ValueError(
            f"Need at least 2 frames to interpolate in '{directory}' "
            f"(found {len(numbers)} matching '{prefix}*{ext or ''}')."
        )

    # FFmpeg's image2 demuxer stops at the first missing number: reject gaps
    missing = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers))
    if missing:
        preview = ", ".join(str(n) for n in missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise ValueError(
            f"Frame sequence '{prefix}*{ext or ''}' has gaps in '{directory}' "
            f"(missing: {preview}{more}). Contiguous numbering is required."
        )

    # Zero-padding detection: all stems the same width and smallest starts with '0'
    pad_width = 0
    widths = {len(s) for s in stems}
    if len(widths) == 1 and stems[0].startswith("0") and len(stems[0]) > 1:
        pad_width = widths.pop()

    return {
        "directory": directory,
        "prefix": prefix,
        "extension": ext,                                   # includes dot; '' if extensionless
        "num_pattern": f"%0{pad_width}d" if pad_width else "%d",
        "first": numbers[0],
        "last": numbers[-1],
        "count": len(numbers),
    }


def resolve_output_target(output_path: str, fallback_image_ext: str) -> dict:
    """
    Classifies the output target as an image-sequence folder or a video file.

    Treated as an image-sequence folder when the path is/looks like a directory
    (existing dir, trailing separator, or no file extension). A known image
    extension also selects frame output; anything else is treated as a video.
    """
    ext = os.path.splitext(output_path)[1].lower()
    looks_like_dir = (
        os.path.isdir(output_path)
        or output_path.endswith(os.sep)
        or output_path.endswith("/")
        or not ext
    )
    if looks_like_dir or ext in IMAGE_EXTENSIONS:
        return {"kind": "frames", "dir": output_path, "ext": fallback_image_ext}
    return {"kind": "video", "file": output_path}


def build_minterpolate_filter(
    target_fps: float,
    mode: str,
    mc_mode: str,
    me_mode: str,
    scd: str,
    scd_threshold: float,
) -> str:
    """Builds the minterpolate -vf filter string."""
    if mode == "mci":
        # Motion-compensated interpolation (High Quality Optical Flow)
        return (
            f"minterpolate=fps={target_fps:g}:"
            f"mi_mode=mci:"
            f"mc_mode={mc_mode}:"
            f"me_mode={me_mode}:"
            f"vsbmc=1:"
            f"scd={scd}:"
            f"scd_threshold={scd_threshold}"
        )
    elif mode == "blend":
        # Frame blending (Fast, lower CPU usage)
        return f"minterpolate=fps={target_fps:g}:mi_mode=blend"
    elif mode == "dup":
        # Frame duplication
        return f"minterpolate=fps={target_fps:g}:mi_mode=dup"
    raise ValueError(f"Unknown interpolation mode: {mode}")


def run_ffmpeg_with_progress(cmd: list, duration: float, output_desc: str) -> bool:
    """
    Runs an FFmpeg command while rendering a progress bar from -progress pipe:1.

    duration: expected media duration in seconds (0 disables percentage display).
    Returns True on success.
    """
    print("\n🚀 Starting interpolation... (Note: MCI filter is computationally intensive)")
    start_time = time.time()

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )

    # Drain stderr in a background thread (prevents pipe deadlock) and keep
    # the last lines so they can be shown if FFmpeg fails.
    stderr_tail = deque(maxlen=25)

    def _drain_stderr():
        for line in process.stderr:
            stderr_tail.append(line.rstrip())

    threading.Thread(target=_drain_stderr, daemon=True).start()

    time_pattern = re.compile(r"out_time_ms=(\d+)")
    speed_pattern = re.compile(r"speed=([0-9\.]+x)")

    current_speed = "N/A"

    try:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break

            # Parse out_time_ms to compute progress percentage
            match_time = time_pattern.search(line)
            if match_time and duration > 0:
                out_time_us = int(match_time.group(1))
                current_time_s = out_time_us / 1_000_000.0
                progress_pct = min(100.0, (current_time_s / duration) * 100)
                elapsed = time.time() - start_time
                eta = (elapsed / (progress_pct / 100)) - elapsed if progress_pct > 0 else 0

                bar_length = 30
                filled_length = int(bar_length * progress_pct // 100)
                bar = "█" * filled_length + "░" * (bar_length - filled_length)

                sys.stdout.write(
                    f"\r[{bar}] {progress_pct:5.1f}% | "
                    f"{format_time(current_time_s)}/{format_time(duration)} | "
                    f"Speed: {current_speed} | "
                    f"ETA: {format_time(eta)}"
                )
                sys.stdout.flush()

            match_speed = speed_pattern.search(line)
            if match_speed:
                current_speed = match_speed.group(1)

        process.wait()
        total_time = time.time() - start_time

        if process.returncode == 0:
            print(f"\n\n✨ Successfully generated: {output_desc}")
            print(f"⏱️ Total processing time: {format_time(total_time)} ({total_time:.1f}s)")
            return True
        else:
            print(f"\n\n❌ FFmpeg exited with error code {process.returncode}", file=sys.stderr)
            if stderr_tail:
                print("Last FFmpeg messages:", file=sys.stderr)
                for line in list(stderr_tail)[-10:]:
                    print(f"  {line}", file=sys.stderr)
            return False

    except KeyboardInterrupt:
        process.kill()
        print("\n\n⚠️ Process cancelled by user.")
        return False


def interpolate_video(
    input_file: str,
    output_file: str,
    target_fps: float = 60,
    src_fps: float | None = None,
    mode: str = "mci",
    mc_mode: str = "aobmc",
    me_mode: str = "bidir",
    crf: int = 18,
    preset: str = "medium",
    codec: str = "libx264",
    scd: str = "fdiff",
    scd_threshold: float = 5.0,
) -> bool:
    """
    Interpolates video frame rate using FFmpeg's minterpolate filter.

    Parameters:
        input_file: Path to source video file
        output_file: Path to destination video file
        target_fps: Desired output frame rate
        src_fps:    Optional override of the detected source frame rate (display only)
        mode: Interpolation mode ('mci' for motion compensated, 'blend' for frame blending)
        mc_mode: Motion compensation mode ('aobmc', 'obmc')
        me_mode: Motion estimation mode ('bidir', 'bilat')
        crf: Constant Rate Factor quality for H.264/H.265 (0-51, lower = better, 18 is visually lossless)
        preset: FFmpeg encoder speed preset ('ultrafast' to 'veryslow')
        codec: Video encoder codec ('libx264', 'libx265', 'h264_videotoolbox')
        scd: Scene change detection ('fdiff' or 'none') to prevent artifacts across cuts
        scd_threshold: Scene change threshold percentage (default: 5.0)
    """
    if not check_ffmpeg_installed():
        print("Error: FFmpeg or FFprobe not found. Please install FFmpeg first.", file=sys.stderr)
        print("  macOS:   brew install ffmpeg", file=sys.stderr)
        print("  Ubuntu:  sudo apt install ffmpeg", file=sys.stderr)
        print("  Windows: winget install Gyan.FFmpeg", file=sys.stderr)
        return False

    if not os.path.isfile(input_file):
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        return False

    # Get input video metadata
    try:
        info = get_video_info(input_file)
        duration = info["duration"]
        detected_fps = info["fps"]
        source_fps = src_fps if src_fps is not None else detected_fps
        print(f"📹 Source Video: {input_file}")
        print(f"📐 Resolution:   {info['width']}x{info['height']}")
        print(f"🎞️  Source FPS:   {source_fps:.2f} fps -> Target FPS: {target_fps:g} fps"
              + (f" (detected {detected_fps:.2f}, overridden)" if src_fps is not None else ""))
        print(f"⏱️  Duration:     {format_time(duration)} ({duration:.2f}s)")
        print(f"⚙️  Interpolator: mode={mode}, mc_mode={mc_mode}, me_mode={me_mode}")
    except Exception as e:
        print(f"Warning: Could not probe video duration ({e}). Progress will be limited.")
        duration = 0.0

    # Build the minterpolate filter string
    filter_str = build_minterpolate_filter(target_fps, mode, mc_mode, me_mode, scd, scd_threshold)

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-y",                       # Overwrite output without asking
        "-i", input_file,
        "-vf", filter_str,
        "-c:v", codec,
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",      # Maximum compatibility across players
        "-c:a", "copy",             # Copy audio track without re-encoding
        "-movflags", "+faststart",  # Web optimization (streamable)
        "-progress", "pipe:1",      # Stream progress output
        output_file
    ]

    return run_ffmpeg_with_progress(cmd, duration, output_file)


def count_written_frames(output_dir: str, prefix: str, ext: str) -> int:
    """Counts files in a folder that match '<prefix><digits><ext>'."""
    pattern = re.compile(rf"^{re.escape(prefix)}\d+{re.escape(ext)}$")
    return sum(1 for name in os.listdir(output_dir) if pattern.match(name))


def interpolate_frame_sequence(
    seq: dict,
    target: dict,
    target_fps: float,
    src_fps: float,
    mode: str = "mci",
    mc_mode: str = "aobmc",
    me_mode: str = "bidir",
    crf: int = 18,
    preset: str = "medium",
    codec: str = "libx264",
    scd: str = "fdiff",
    scd_threshold: float = 5.0,
) -> bool:
    """
    Interpolates a numbered frame sequence detected by detect_frame_sequence().

    Runs as a single FFmpeg pass (image2 demuxer -> minterpolate -> image2 muxer),
    so no lossy intermediate video is created. If the resolved output target is a
    video file, the interpolated result is encoded directly into that video.

    Parameters:
        seq:        metadata from detect_frame_sequence()
        target:     {'kind': 'frames', 'dir', 'ext'} or {'kind': 'video', 'file'}
        target_fps: desired output frame rate
        src_fps:    assumed frame rate of the input sequence (frames are time-spaced at this rate)
    """
    to_video = target["kind"] == "video"
    fmt_num = lambda n: seq["num_pattern"] % n  # applies '%04d' or '%d'

    input_pattern = os.path.join(
        seq["directory"], f"{seq['prefix']}{seq['num_pattern']}{seq['extension']}"
    )

    print(f"📹 Source Frames: {input_pattern}")
    print(f"🎞️  Range:         {seq['prefix']}{fmt_num(seq['first'])} -> "
          f"{seq['prefix']}{fmt_num(seq['last'])}  "
          f"({seq['count']} frames, padding '{seq['num_pattern']}', "
          f"ext '{seq['extension'] or '(none)'}')")
    print(f"⏱️  Source FPS:   {src_fps:g} fps -> Target FPS: {target_fps:g} fps")
    est_duration = seq["count"] / src_fps
    expected_out = round(seq["count"] * target_fps / src_fps)
    print(f"📐 Estimated out: ~{expected_out} frames ({est_duration:.2f}s @ {target_fps} fps)")
    print(f"⚙️  Interpolator: mode={mode}, mc_mode={mc_mode}, me_mode={me_mode}")

    filter_str = build_minterpolate_filter(target_fps, mode, mc_mode, me_mode, scd, scd_threshold)

    # Read the sequence with an explicit frame rate and start offset
    cmd = [
        "ffmpeg",
        "-y",                                   # Overwrite output without asking
        "-framerate", f"{src_fps}",             # Time-spacing of the source frames
        "-start_number", str(seq["first"]),     # Sequence may not begin at 0/1
    ]
    if not seq["extension"]:
        cmd += ["-f", "image2"]                 # No extension to infer format from
    cmd += [
        "-i", input_pattern,
        "-vf", filter_str,
        "-progress", "pipe:1",                  # Stream progress output
    ]

    if to_video:
        cmd += [
            "-c:v", codec,
            "-crf", str(crf),
            "-preset", preset,
            "-pix_fmt", "yuv420p",
            "-an",                              # Image sequences carry no audio
            "-movflags", "+faststart",
            target["file"],
        ]
        output_desc = target["file"]
    else:
        out_dir = target["dir"]
        out_ext = target["ext"]
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print(f"Error: Cannot create output directory '{out_dir}': {e}", file=sys.stderr)
            return False
        # Keep the input's naming convention (prefix + padding), numbering from
        # the same first number so output stays aligned with the input range.
        out_first = f"{seq['prefix']}{fmt_num(seq['first'])}{out_ext}"
        out_pattern = os.path.join(out_dir, f"{seq['prefix']}{seq['num_pattern']}{out_ext}")
        cmd += ["-start_number", str(seq["first"])]
        if not out_ext:
            # Extensionless output: force muxer + lossless PNG encoding explicitly
            cmd += ["-f", "image2", "-c:v", "png"]
        cmd += ["-an", out_pattern]
        output_desc = out_dir

    ok = run_ffmpeg_with_progress(cmd, est_duration, output_desc)

    if ok and not to_video:
        written = count_written_frames(target["dir"], seq["prefix"], out_ext)
        last_num = seq["first"] + max(written - 1, 0)
        print(f"🎞️  Frames written: {written} "
              f"({out_first} -> {seq['prefix']}{fmt_num(last_num)}{out_ext})")

    return ok


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Interpolate frame rate using FFmpeg's minterpolate filter (e.g. 24fps -> 60fps). "
            "Input can be a video file OR a folder of numbered frames (600.png, 601.png, ...)."
        )
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Input video file OR folder containing numbered frames (e.g. 600.png, 601.png, ...)")
    parser.add_argument("-o", "--output", required=True,
                        help="Output video file OR folder for the interpolated frames")
    parser.add_argument("--fps", type=float, required=True,
                        help="Target FPS (required, any value e.g. 24, 25, 29.97, 48, 60, 120)")
    parser.add_argument("--src-fps", type=float, default=None,
                        help="Source FPS. Required when --input is a frame folder; optional override "
                             "of a video's detected FPS otherwise.")
    parser.add_argument("--out-ext",
                        help="Output image extension for frame output (default: same as input frames, else .png)")
    parser.add_argument(
        "--mode",
        choices=["mci", "blend", "dup"],
        default="mci",
        help="Interpolation mode: 'mci' (motion compensated optical flow), 'blend' (frame blending), 'dup' (duplicate)",
    )
    parser.add_argument(
        "--mc_mode",
        choices=["aobmc", "obmc"],
        default="aobmc",
        help="Motion compensation mode for MCI (default: aobmc - adaptive overlapped block)",
    )
    parser.add_argument(
        "--me_mode",
        choices=["bidir", "bilat"],
        default="bidir",
        help="Motion estimation mode (default: bidir - bidirectional)",
    )
    parser.add_argument("--crf", type=int, default=18, help="Constant Rate Factor (0-51, default: 18)")
    parser.add_argument(
        "--preset",
        default="medium",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
        help="Encoder speed preset (default: medium)",
    )
    parser.add_argument("--codec", default="libx264", help="Video codec (default: libx264)")

    args = parser.parse_args()

    if not check_ffmpeg_installed():
        print("Error: FFmpeg or FFprobe not found. Please install FFmpeg first.", file=sys.stderr)
        print("  macOS:   brew install ffmpeg", file=sys.stderr)
        print("  Ubuntu:  sudo apt install ffmpeg", file=sys.stderr)
        print("  Windows: winget install Gyan.FFmpeg", file=sys.stderr)
        sys.exit(1)

    if args.fps <= 0:
        print("Error: --fps must be greater than 0.", file=sys.stderr)
        sys.exit(1)
    if args.src_fps is not None and args.src_fps <= 0:
        print("Error: --src-fps must be greater than 0.", file=sys.stderr)
        sys.exit(1)

    input_is_dir = os.path.isdir(args.input)

    try:
        if input_is_dir:
            # ---- Frame-sequence pipeline (frames -> frames, or frames -> video) ----
            if args.src_fps is None:
                raise ValueError(
                    "--src-fps is required when --input is a frame folder "
                    "(frames carry no timing metadata, e.g. --src-fps 24)."
                )
            seq = detect_frame_sequence(args.input)

            fallback_ext = args.out_ext
            if fallback_ext is None:
                # Mirror the input's convention (including no extension at all)
                fallback_ext = seq["extension"]
            if fallback_ext and not fallback_ext.startswith("."):
                fallback_ext = f".{fallback_ext}"
            if fallback_ext and fallback_ext.lower() not in IMAGE_EXTENSIONS:
                raise ValueError(
                    f"Unsupported output image extension '{fallback_ext}'. "
                    f"Choose one of: {', '.join(sorted(IMAGE_EXTENSIONS))} (or none for lossless PNG)."
                )

            target = resolve_output_target(args.output, fallback_ext)
            success = interpolate_frame_sequence(
                seq=seq,
                target=target,
                target_fps=args.fps,
                src_fps=args.src_fps,
                mode=args.mode,
                mc_mode=args.mc_mode,
                me_mode=args.me_mode,
                crf=args.crf,
                preset=args.preset,
                codec=args.codec,
            )
        else:
            # ---- Classic video pipeline (video -> video) ----
            if not os.path.isfile(args.input):
                print(f"Error: Input not found: {args.input}", file=sys.stderr)
                sys.exit(1)
            if os.path.splitext(args.output)[1].lower() in IMAGE_EXTENSIONS or not os.path.splitext(args.output)[1]:
                print(
                    "Error: Video input requires a video output file "
                    "(e.g. output.mp4). Folder output is only supported for frame-folder input.",
                    file=sys.stderr,
                )
                sys.exit(1)

            success = interpolate_video(
                input_file=args.input,
                output_file=args.output,
                target_fps=args.fps,
                src_fps=args.src_fps,
                mode=args.mode,
                mc_mode=args.mc_mode,
                me_mode=args.me_mode,
                crf=args.crf,
                preset=args.preset,
                codec=args.codec,
            )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
