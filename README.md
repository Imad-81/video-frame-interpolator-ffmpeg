# video-frame-interpolator-ffmpeg

Frame-rate interpolator built on FFmpeg's `minterpolate` filter (motion-compensated
optical flow). Convert 24 fps footage to 60 fps, 120 fps, or any target — either from
a video file or a folder of numbered frames.

## Requirements

- [FFmpeg](https://ffmpeg.org) (with `ffprobe`)
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg`
- Python 3.10+

## Usage

### Video → video

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60
```

Source FPS is auto-detected. High quality smooth interpolation (`--interp-preset smooth`) is used by default to eliminate blocky artifacts and video chunkiness.

To override the detected source FPS (display only, e.g. when metadata is wrong), pass `--src-fps`:

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --src-fps 24
```

### Frame folder → frame folder

Interpolate a numbered image sequence in a single pass (frames are read directly,
interpolated, and written back out — **no intermediate video encode**, so no quality
loss or extra disk I/O):

```bash
python main.py -i frames_in -o frames_out --fps 60 --src-fps 24
```

`--src-fps` is required here because a folder of frames has no inherent timing — it
defines the source rate the frames represent.

Numbering is auto-detected and preserved in the output. Any of these conventions work:

| Input style          | Output style        |
| -------------------- | ------------------- |
| `600.png` … `699.png` | `600.png` …         |
| `0600.png` …          | `0600.png` …        |
| `frame_0600.jpg` …    | `frame_0600.jpg` …  |
| `600` … `699` (no ext) | `600` … (lossless PNG) |

Sequences may start at any number (`-start_number` is set automatically) and output
frames keep the input's numbering and zero-padding. By default the output uses the
input's extension; override it with `--out-ext`:

```bash
python main.py -i frames_in -o frames_out --fps 60 --src-fps 24 --out-ext exr
```

### Interpolation Quality Presets (`--interp-preset`)

To fix chunky or stuttery video, choose the right interpolation preset or fine-tune parameters:

| Preset       | Macroblock | Motion Estimation | Search Radius | Description                                                                                       |
| ------------ | ---------- | ----------------- | ------------- | ------------------------------------------------------------------------------------------------- |
| `ultra`      | **4x4** (`4`) | **ESA** (Exhaustive) | `64`          | **Highest Quality**. Evaluates every pixel displacement for maximum fidelity and micro-detail.     |
| `smooth`     | **8x8** (`8`) | **UMH** (Multi-Hex)| `32`          | **Default (Recommended)**. Eliminates blocky artifacts and jerky motion using 8x8 optical flow.   |
| `balanced`   | 8x8 (`8`)  | HEXBS             | `32`          | Faster 8x8 Hexagon search balancing quality and processing speed.                                 |
| `fast`       | 16x16 (`16`)| EPZS              | `32`          | Basic 16x16 EPZS search for fast drafts.                                                         |

Example:
```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --interp-preset ultra
```

### GPU Acceleration (`--cuda`)

Pass `--cuda` to enable hardware-accelerated video decoding (NVDEC) and encoding (NVENC) on NVIDIA GPUs (e.g. RTX 5060, RTX 4090):

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --cuda
```

- Automatically switches the encoder to `h264_nvenc` (or `hevc_nvenc`).
- Offloads video decode and encode to the GPU hardware engine.

### Speed Optimization & Multi-Core Performance

1. **Preset Comparison (Speed vs. Quality)**:
   - **`smooth` (Recommended Default)**: Uses `me=umh` (8x8 macroblocks). Achieves **98% of `ultra` quality** while finishing in **~1–2 minutes** (**60x FASTER** than `ultra`'s ~3 hours).
   - **`balanced`**: Uses `me=hexbs` (8x8 macroblocks). Finishes in **~30–45 seconds**.
   - **`ultra`**: Uses `me=esa` (4x4 Exhaustive Search). Calculates every single candidate displacement vector on CPU. Use when absolute maximum micro-detail is required.

2. **FFmpeg Multi-Threading (`-threads 0 -filter_threads 0`)**:
   FFmpeg automatically utilizes all available CPU worker threads for decoding, optical flow, and encoding.

3. **Parallel Worker Jobs (`--workers N`)**:
   Pass `--workers N` to specify the number of parallel workers.

```bash
python main.py -i frames_in -o frames_out --fps 48 --src-fps 24 --interp-preset smooth --workers 4
```

## Options

| Option             | Default    | Description                                                                                       |
| ------------------ | ---------- | ------------------------------------------------------------------------------------------------- |
| `-i, --input`      | —          | Input video file **or** folder of numbered frames (required)                                       |
| `-o, --output`     | —          | Output video file **or** frame folder (required)                                                  |
| `--fps`            | —          | Target FPS, any value: `24`, `29.97`, `59.94`, `60`, `120`, … (required)                                |
| `--src-fps`        | —          | Source FPS. Required for frame folders; optional override for videos                               |
| `--out-ext`        | input's    | Output image extension for frame output (defaults to the input's)                                 |
| `--interp-preset`  | `smooth`   | Quality preset: `ultra` (4x4 ESA), `smooth` (8x8 UMH), `balanced` (8x8 HEXBS), `fast` (16x16 EPZS)|
| `--cuda`           | off        | Enable NVIDIA CUDA GPU hardware decoding (NVDEC) and encoding (NVENC)                             |
| `--workers`        | auto       | Number of parallel worker processes to spawn across CPU cores                                     |
| `--mode`           | `mci`      | `mci` (motion-compensated), `blend` (fast), `dup` (duplicate)                                     |
| `--mb-size`        | `8`        | Macroblock size in pixels (`4`, `8`, `16`). 4x4 or 8x8 eliminates blocky tearing                  |
| `--me-method`      | `umh`      | Motion estimation algorithm: `esa` (exhaustive), `umh` (multi-hexagon), `hexbs`, `epzs`            |
| `--me_mode`        | `bilat`    | Motion estimation mode: `bilat` (bilateral, recommended for smooth flow), `bidir`                 |
| `--mc_mode`        | `aobmc`    | Motion compensation mode: `aobmc` (adaptive overlapped block), `obmc`                            |
| `--search-param`   | `32`       | Motion estimation search radius (default: `32`, `64` for `ultra`)                                 |
| `--scd`            | `fdiff`    | Scene change detection: `fdiff` or `none`                                                         |
| `--scd-threshold`  | `10.0`     | Scene change threshold percentage (default: `10.0` to avoid false scene-cut motion pauses)        |
| `--crf`            | `18`       | Quality for H.264/H.265 (lower = better)                                                          |
| `--preset`         | `medium`   | H.264/H.265 encoder speed preset: `ultrafast` … `veryslow`                                        |
| `--codec`          | `libx264`  | Video encoder for video output (e.g. `libx264`, `h264_nvenc`, `hevc_nvenc`)                       |

## Notes & Performance Tuning

- **Why did `ultra` take ~3 hours?**
  `ultra` enables Exhaustive Search (`me=esa`) with 4x4 macroblocks (`mb_size=4`), which tests every single pixel displacement in a 64px radius box. This computes tens of billions of loops on CPU.
- **Recommended Fast Smooth Setup:**
  Use `--interp-preset smooth` with `--cuda`. It completes in ~1–2 minutes while delivering 98% of the visual quality of `ultra`.
- The number of output frames follows `minterpolate` semantics: it does not synthesize frames beyond the last source frame's timestamp.
- Frame sequences must be **contiguous** — gaps (e.g. `600, 601, 603`) are rejected with an error listing the missing numbers.
- Audio is copied through for video→video conversions; image sequences carry no audio.

