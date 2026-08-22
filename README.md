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

Source FPS is auto-detected. To override the detected value (display only, e.g. when
metadata is wrong), pass `--src-fps`:

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

### Frame folder → video

```bash
python main.py -i frames_in -o output.mp4 --fps 60 --src-fps 24
```

When `--output` ends in a video extension it is encoded as a video; a path with no
extension (or an image extension) is treated as a frame folder.

## Options

| Option          | Default  | Description                                                        |
| --------------- | -------- | ------------------------------------------------------------------ |
| `-i, --input`   | —        | Input video file **or** folder of numbered frames (required)        |
| `-o, --output`  | —        | Output video file **or** frame folder (required)                    |
| `--fps`         | —        | Target FPS, any value: `24`, `29.97`, `59.94`, `60`, `120`, … (required) |
| `--src-fps`     | —        | Source FPS. Required for frame folders; optional override for videos |
| `--out-ext`     | input's  | Output image extension for frame output (defaults to the input's)  |
| `--mode`        | `mci`    | `mci` (motion-compensated), `blend` (fast), `dup` (duplicate)      |
| `--mc_mode`     | `aobmc`  | Motion compensation: `aobmc`, `obmc`                                |
| `--me_mode`     | `bidir`  | Motion estimation: `bidir`, `bilat`                                 |
| `--crf`         | `18`     | Quality for H.264/H.265 (lower = better)                            |
| `--preset`      | `medium` | Encoder preset: `ultrafast` … `veryslow`                            |
| `--codec`       | `libx264`| Video encoder for video output                                     |

## Notes

- **MCI mode is slow.** Motion-compensated interpolation is computationally intensive;
  for a quick preview or draft use `--mode blend` or `--mode dup`.
- The number of output frames follows `minterpolate` semantics: it does not synthesize
  frames beyond the last source frame's timestamp, so a 24 fps→60 fps clip of *N* source
  frames yields slightly fewer than `N × 60 / 24` frames. This matches FFmpeg's native
  behavior for both video and image-sequence inputs.
- Frame sequences must be **contiguous** — gaps (e.g. `600, 601, 603`) are rejected with
  an error listing the missing numbers.
- Audio is copied through for video→video conversions; image sequences carry no audio.
