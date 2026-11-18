# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-11-18 首次补齐四件套 + 接入 repo-kit 机器层（`monorepo`，kit 2.8.0 / spec 2.1.3）
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加

## 技术栈

- **Python 3.14** + **PySide6**（界面、预览播放）+ **Pillow**（静态图片）；仓库**未锁依赖版本**，见 DEVELOPMENT「已知缺口」
- **ffmpeg full build**（gyan.dev 版）随包分发：`assets/ffmpeg/ffmpeg.exe` + `ffprobe.exe`，各约 98 MiB
- 打包：**PyInstaller onedir**，配置在 `build/videotool.spec`；产物 `dist/VideoToolbox/VideoToolbox.exe`
- 架构契约：**import-linter**（`core` 不依赖 `ui` / PySide6）+ `tools/arch_check.py`（AST 精确查"仅直接导入"）
- 目标环境：Windows 10 / 11 x64

## 架构红线

1. `src/videotool/core/` **不得**导入 `PySide6`，也**不得**导入 `src/videotool/ui/` —— 核心要能脱离 Qt 跑（`import-linter` 强制）
2. `src/videotool/ui/` **不得直接** `import subprocess` —— 子进程一律经 core（`tools/arch_check.py` 强制）
3. 子进程调用必须带 `_no_window_kwargs()`，否则打包后每次跑 ffmpeg 都弹黑窗
4. `specs.py` 是"能转出什么"的唯一真源；改它必须同步 `DEVELOPMENT.md`（机器层会拦截）

## 关键坑

- **打包别排除 `QtMultimedia` / `QtMultimediaWidgets`**：选段裁切的预览播放器要用，它的 FFmpeg 后端依赖 PySide6 自带 avcodec/avformat 运行库，这些 DLL 也得一起进包。漏了**不报错，只是播不出画面**——用 `--selftest` 验
- **UPX 不能开**：会破坏部分 Qt DLL
- **硬件编码器"列表里有"≠"本机能用"**：`capabilities.py` 对每个候选做 1 帧真实试编码，失败记进 `hw_failed` 并在界面隐藏
- **import-linter 的 `forbidden` 连间接导入一起查**（官方文档明示），表达不了"仅直接"约束，所以"ui 不得直接 import subprocess"这条只能靠 `arch_check.py`
- **ffmpeg 输出编码不确定**：Windows 上可能是 UTF-8 也可能是本地代码页，统一走 `binaries._decode()` 容错解码
- **循环预览别靠 seek 回跳**：长 GOP 素材入点距关键帧 3.67 s 时每次回跳冻结约 143 ms；改为播一个小体积预览代理（`core/proxy.py`），全程零 seek
- **`assets/ffmpeg/*.exe` 各约 98 MiB**，贴着 GitHub 单文件 100 MiB 硬上限：升级 ffmpeg 前先量体积，超了推不上去

## 常用命令

```powershell
# 运行（开发态）
$env:PYTHONPATH = "src"; python src/videotool/main.py

# 架构与冒烟（全部走真实 ffmpeg）
python tools/arch_check.py
python tools/smoke.py
python tools/ui_smoke.py
python tools/features_smoke.py

# 构建 + 打包后自检
pyinstaller --noconfirm --clean build/videotool.spec
dist\VideoToolbox\VideoToolbox.exe --selftest $env:TEMP\vt.txt

# 文档与版本锁
python tools/repo-kit/check_repo.py --root .
pwsh tools/update-repo-kit.ps1 -Check
```

## 约定

- 注释、界面文案、提交信息用中文；文件名用英文（`_internal`、`ffmpeg` 等运行时路径不能改）
- `core` 保持无 Qt、无界面概念；界面文案集中在 `ui/help_text.py`，不散落在主窗口
- 一坑一篇写进 `DEVELOPMENT.md`「关键问题与方案」；通用经验再提炼到 knowledge-base 用户库
