# CHANGELOG

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号跟随 `src/videotool/__init__.py` 的 `__version__`。

## [未发布]

### 新增

- 补齐四件套文档：`README.md`、`AGENTS.md`、`DEVELOPMENT.md`、`CHANGELOG.md`（此前仓库内没有任何 Markdown）
- 接入 repo-kit 机器层（`monorepo`，kit 2.8.0 / spec 2.1.3）：`tools/repo-kit/`、`schema/taxonomy.json`、`schema/sources.json`、`schema/kit.lock.json`、`.github/workflows/repo-check.yml`
- `schema/sources.json` 把 `src/videotool/core/specs.py` 映射到 `DEVELOPMENT.md`：改格式矩阵必须同步文档，否则检查失败

### 变更

- 程序代码未改动（`__version__` 仍为 `1.0.0`）

## [1.0.0] - 2026-10-12

首发版本：

- 视频转码：MP4 / MKV / MOV / WebM / AVI / FLV / TS；软件编码 x264 / x265 / VP9 / SVT-AV1 / libaom，硬件编码 NVENC / QSV / AMF / MediaFoundation
- 视频转 GIF（`split + palettegen + paletteuse` 调色板方案）、动图 WebP、APNG
- 音频提取与转码：MP3 / M4A / Opus / FLAC / WAV / OGG
- 单帧抽取与静态图片转换（Pillow 路径）、图片序列转视频
- 音视频合轨（视频流默认直接复制）、批量任务队列、可视化选段裁切
- 发布物：`VideoToolbox-1.0.0-win64.zip`（PyInstaller onedir，内置 ffmpeg / ffprobe）
