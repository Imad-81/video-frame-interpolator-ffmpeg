# video-frame-interpolator-ffmpeg

Frame-rate interpolator built on FFmpeg's `minterpolate` filter (motion-compensated optical flow). Convert 24 fps footage to 60 fps, 120 fps, or any target — supporting video-to-video, frame-sequence-to-frame-sequence, and frame-sequence-to-video pipelines.

---

## Features

- **Multi-Pipeline Support**:
  - Video file $\to$ Video file
  - Frame sequence folder $\to$ Frame sequence folder (single pass, zero intermediate video encode loss)
  - Frame sequence folder $\to$ Video file
- **Motion-Compensated Optical Flow**: Powered by FFmpeg's `minterpolate` filter (`mci`, `blend`, `dup`).
- **Quality Presets**: Built-in presets (`ultra`, `smooth`, `balanced`, `fast`) balancing motion estimation accuracy and rendering speed.
- **Hardware Acceleration**: Optional NVIDIA CUDA acceleration (`--cuda`) with NVDEC decoding and NVENC hardware encoding.
- **Multi-Core Multi-Threading & Parallel Jobs**: Automatically assigns CPU worker threads and supports parallel slice processing (`--workers`).
- **Automatic Framing & Naming**: Preserves source frame naming conventions, start numbers, and zero-padding automatically.

---

## Requirements

- **[FFmpeg](https://ffmpeg.org)** (with `ffprobe`):
  - macOS: `brew install ffmpeg`
  - Ubuntu / Debian: `sudo apt update && sudo apt install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg`
- **Python**: 3.10 or higher

---

## Usage

### 1. Video $\to$ Video

Convert any video file to a target framerate:

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60
```

Source FPS is auto-detected. High quality smooth interpolation (`--interp-preset smooth`) is used by default to eliminate blocky tearing and stutter.

To override the detected source FPS:

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --src-fps 24
```

---

### 2. Frame Sequence $\to$ Frame Sequence

Interpolate an image sequence in a single pass without intermediate video encoding:

```bash
python main.py -i frames_in/ -o frames_out/ --fps 60 --src-fps 24
```

> **Note**: `--src-fps` is required when input is a frame sequence folder because image folders do not possess native timing metadata.

Supported numbering conventions:
- `0001.png`, `0002.png`, ...
- `frame_0600.jpg`, `frame_0601.jpg`, ...
- Non-zero starting numbers are automatically detected and preserved.

To output frames in a different image format:

```bash
python main.py -i frames_in/ -o frames_out/ --fps 60 --src-fps 24 --out-ext webp
```

---

### 3. Frame Sequence $\to$ Video

Convert an image sequence folder directly into an interpolated video file:

```bash
python main.py -i frames_in/ -o output.mp4 --fps 60 --src-fps 24
```

---

### 4. GPU Acceleration (`--cuda`)

Pass `--cuda` to enable NVIDIA NVDEC decoding and NVENC hardware encoding (e.g., RTX series GPUs):

```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --cuda
```

---

## Interpolation Quality Presets

| Preset | Macroblock | Motion Estimation | Search Radius | Description |
| :--- | :--- | :--- | :--- | :--- |
| `ultra` | **4x4** (`4`) | **ESA** (Exhaustive) | `64` | **Highest Quality**: Evaluates every candidate displacement pixel for micro-detail (CPU-intensive). |
| `smooth` | **8x8** (`8`) | **UMH** (Multi-Hex) | `32` | **Default (Recommended)**: Delivers ~98% of `ultra` quality while running dramatically faster. |
| `balanced`| **8x8** (`8`) | **HEXBS** | `32` | Fast hexagon search balancing quality and conversion time. |
| `fast` | **16x16** (`16`)| **EPZS** | `32` | Quick draft interpolation using 16x16 macroblocks. |

Example:
```bash
python main.py -i input.mp4 -o output.mp4 --fps 60 --interp-preset balanced
```

---

## CLI Options Reference

```text
usage: main.py [-h] -i INPUT -o OUTPUT --fps FPS [--src-fps SRC_FPS]
               [--out-ext OUT_EXT]
               [--interp-preset {ultra,smooth,balanced,fast}] [--cuda]
               [--workers WORKERS] [--mode {mci,blend,dup}]
               [--mc_mode {aobmc,obmc}] [--me_mode {bidir,bilat}]
               [--me-method {esa,tss,tdls,ntss,fss,ds,hexbs,epzs,umh}]
               [--mb-size {4,8,16}] [--search-param SEARCH_PARAM]
               [--scd {fdiff,none}] [--scd-threshold SCD_THRESHOLD]
               [--crf CRF]
               [--preset {ultrafast,superfast,veryfast,faster,fast,medium,slow,slower,veryslow}]
               [--codec CODEC]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `-i, --input` | *required* | Input video file path **or** directory of numbered frames |
| `-o, --output` | *required* | Output video file path **or** directory for interpolated frames |
| `--fps` | *required* | Target framerate (e.g. `24`, `30`, `60`, `120`) |
| `--src-fps` | auto / required | Source framerate (required for frame sequences, optional video override) |
| `--out-ext` | source ext | Output image format extension (`png`, `jpg`, `webp`, `exr`, etc.) |
| `--interp-preset` | `smooth` | Preset: `ultra`, `smooth`, `balanced`, `fast` |
| `--cuda` | `False` | Enable NVIDIA CUDA hardware acceleration (NVDEC & NVENC) |
| `--workers` | auto | Number of parallel worker jobs across CPU cores |
| `--mode` | `mci` | Interpolation mode: `mci` (motion-compensated), `blend`, `dup` |
| `--mc_mode` | `aobmc` | Motion compensation mode: `aobmc` (adaptive overlapped block), `obmc` |
| `--me_mode` | `bilat` | Motion estimation mode: `bilat` (bilateral), `bidir` |
| `--me-method` | `umh` | Motion estimation algorithm: `esa`, `umh`, `hexbs`, `epzs`, etc. |
| `--mb-size` | `8` | Macroblock size in pixels (`4`, `8`, `16`) |
| `--search-param` | `32` | Motion estimation search parameter radius (`32`, `64`) |
| `--scd` | `fdiff` | Scene change detection: `fdiff` or `none` |
| `--scd-threshold` | `10.0` | Scene change threshold percentage |
| `--crf` | `18` | Constant Rate Factor (CRF quality: 0–51, lower = higher quality) |
| `--preset` | `medium` | H.264/H.265 encoding speed preset (`ultrafast` ... `veryslow`) |
| `--codec` | `libx264` | Video encoder codec (`libx264`, `libx265`, `h264_nvenc`, etc.) |

---

## Supported Formats

- **Video Containers**: `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.m4v`, `.ts`, etc.
- **Image Sequences**: `.png`, `.jpg`, `.jpeg`, `.webp`, `.exr`, `.tiff`, `.bmp`, `.dpx`, `.tga`

---

## License

MIT License.
