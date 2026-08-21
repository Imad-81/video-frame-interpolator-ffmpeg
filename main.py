#!/usr/bin/env python3
"""
Video Frame Rate Interpolator using FFmpeg
Converts video framerates (e.g. 24 fps -> 60 fps) using motion-compensated interpolation.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


def interpolate_video(
    input_file: str,
    output_file: str,
    target_fps: int = 60,
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
        target_fps: Desired output frame rate (default: 60)
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
        src_fps = info["fps"]
        print(f"📹 Source Video: {input_file}")
        print(f"📐 Resolution:   {info['width']}x{info['height']}")
        print(f"🎞️  Source FPS:   {src_fps:.2f} fps -> Target FPS: {target_fps} fps")
        print(f"⏱️  Duration:     {format_time(duration)} ({duration:.2f}s)")
        print(f"⚙️  Interpolator: mode={mode}, mc_mode={mc_mode}, me_mode={me_mode}")
    except Exception as e:
        print(f"Warning: Could not probe video duration ({e}). Progress will be limited.")
        duration = 0.0

    # Build the minterpolate filter string
    if mode == "mci":
        # Motion-compensated interpolation (High Quality Optical Flow)
        filter_str = (
            f"minterpolate=fps={target_fps}:"
            f"mi_mode=mci:"
            f"mc_mode={mc_mode}:"
            f"me_mode={me_mode}:"
            f"vsbmc=1:"
            f"scd={scd}:"
            f"scd_threshold={scd_threshold}"
        )
    elif mode == "blend":
        # Frame blending (Fast, lower CPU usage)
        filter_str = f"minterpolate=fps={target_fps}:mi_mode=blend"
    elif mode == "dup":
        # Frame duplication
        filter_str = f"minterpolate=fps={target_fps}:mi_mode=dup"
    else:
        raise ValueError(f"Unknown interpolation mode: {mode}")

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

    print("\n🚀 Starting interpolation... (Note: MCI filter is computationally intensive)")
    start_time = time.time()

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True
    )

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
            print(f"\n\n✨ Successfully generated: {output_file}")
            print(f"⏱️ Total processing time: {format_time(total_time)} ({total_time:.1f}s)")
            return True
        else:
            print(f"\n\n❌ FFmpeg exited with error code {process.returncode}", file=sys.stderr)
            return False

    except KeyboardInterrupt:
        process.kill()
        print("\n\n⚠️ Process cancelled by user.")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Interpolate video frame rate using FFmpeg's minterpolate filter (e.g. 24fps -> 60fps)."
    )
    parser.add_argument("-i", "--input", required=True, help="Path to input video file")
    parser.add_argument("-o", "--output", required=True, help="Path to output video file")
    parser.add_argument("--fps", type=int, default=60, help="Target FPS (default: 60)")
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

    success = interpolate_video(
        input_file=args.input,
        output_file=args.output,
        target_fps=args.fps,
        mode=args.mode,
        mc_mode=args.mc_mode,
        me_mode=args.me_mode,
        crf=args.crf,
        preset=args.preset,
        codec=args.codec,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
