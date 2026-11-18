# VideoToolbox · Windows 视频转码 / 压缩工具箱

> 类似小丸工具箱的自用替代品：PySide6 界面 + 内置 ffmpeg，**解压即用**，不装运行库、不联网。
> 批量队列、硬件编码自动探测、视频转 GIF、选段裁切、音视频合轨、图片转换，一套界面里做完。

## 下载

- 最新版：**v1.0.0**（2026-10-12）
- 附件：`VideoToolbox-1.0.0-win64.zip`（onedir 打包，内含 `ffmpeg.exe` / `ffprobe.exe`）
- 地址：[Releases](https://github.com/Simiely/VideoToolbox/releases/latest)

## 快速开始

1. 解压 zip，双击 `VideoToolbox.exe`（**不要**单独挪走 exe，`_internal/` 必须和它同级）
2. 把文件拖进窗口，或「添加文件」批量选
3. 右侧选输出格式、编码器、质量控制，必要时在时间轴上圈出要保留的片段
4. 点「开始」，进度与日志在下方；输出默认落在源文件同目录

> 程序找不到 ffmpeg 时会弹窗提示：把 `ffmpeg.exe`、`ffprobe.exe` 放进程序目录的 `ffmpeg/` 文件夹，或加入系统 PATH 后重试。

## 能做什么

| 任务类型 | 说明 |
|---|---|
| 视频转视频 | MP4 / MKV / MOV / WebM / AVI / FLV / TS 互转，可改分辨率、帧率、码率、音量 |
| 提取 / 转码音频 | MP3 / M4A / Opus / FLAC / WAV / OGG |
| 视频转 GIF | 单命令 `split + palettegen + paletteuse` 高质量调色板方案 |
| 动图 | 动图 WebP、APNG（保留透明） |
| 单帧 / 图片转换 | JPG / PNG / WebP / BMP / TIFF，含视频抽帧 |
| 图片序列转视频 | 指定起始索引与帧数 |
| 音视频合轨 | 外部音频合进视频，默认视频流直接复制（不重编码、秒完成） |
| 批量队列 | 多任务并发调度、取消、重试，单任务进度与日志 |

## 编码器

- **软件编码（默认优先）**：`libx264`、`libx265`、`libvpx-vp9`、`libsvtav1`、`libaom-av1`
- **硬件编码**：NVIDIA NVENC、Intel QSV、AMD AMF、MediaFoundation（H.264 / H.265 / AV1，按厂商支持）
- 硬件编码器是否可用由程序**实测**决定（列表里存在 ≠ 本机能用），不可用时自动降级并写进日志
- 软编同画质体积最小、速度慢；硬编快 5~20 倍、体积大 20%~50%。拿不准就用默认的软件编码

## 质量控制

| 模式 | 用途 |
|---|---|
| 恒定质量（CRF） | 推荐。x264 甜点 20~24、x265 24~28，程序按编码器预填 |
| 恒定码率 | 要严格控制文件大小时用，画质随画面复杂度波动 |
| 指定目标体积 | 自动两遍编码逼近目标大小；硬件编码不支持两遍，退化为单遍近似并在日志提示 |

## 从源码运行

```powershell
# 依赖：Python 3.14 + PySide6 + Pillow（仓库未锁版本，见 DEVELOPMENT「已知缺口」）
$env:PYTHONPATH = "src"
python src/videotool/main.py
```

## 构建

```powershell
pyinstaller --noconfirm --clean build/videotool.spec
# 产物：dist/VideoToolbox/VideoToolbox.exe，ffmpeg 在 dist/VideoToolbox/_internal/ffmpeg/
```

打包配置里有两处不能动：**不能排除 `QtMultimedia` / `QtMultimediaWidgets`**（选段裁切的预览播放器要用），**不能开 UPX**（会破坏 Qt DLL）。

## 自检

窗口程序没有控制台，所以自检把结果写文件：

```powershell
VideoToolbox.exe --selftest C:\Temp\report.txt
```

它会离屏跑一遍：工具链定位 → 能力探测 → 界面构建 → 真实转码（视频 + 图片）→ 预览播放器出帧。**发布前跑一次**，能挡住"打包漏了 QtMultimedia 插件"这类不报错但功能坏掉的问题。

## 文档

| 文件 | 给谁看 |
|---|---|
| [AGENTS.md](AGENTS.md) | AI / 未来的你：技术栈、架构红线、关键坑、常用命令 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 架构分层、一次转码的数据流、关键问题与方案 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

## 许可

仓库未附许可证文件，默认保留所有权利。
