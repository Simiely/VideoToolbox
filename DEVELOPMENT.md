# DEVELOPMENT.md · 架构与关键问题

> 面向"要改这个仓库的人/AI"：分层怎么切、一次转码怎么走、踩过的坑是什么。
> 用户向的功能说明在 [README.md](README.md)；规则与红线在 [AGENTS.md](AGENTS.md)。

## 一、分层

```
src/videotool/
├── main.py                入口：定位 ffmpeg → 立刻显示窗口 → 后台探测能力
├── core/                  核心引擎（不依赖 Qt，可单独跑）
│   ├── paths.py           资源路径：开发态 assets/ffmpeg ↔ 打包态 _MEIPASS/ffmpeg
│   ├── binaries.py        定位 ffmpeg/ffprobe、子进程统一封装（无黑窗、容错解码）
│   ├── capabilities.py    能力快照：解析 -encoders + 硬件编码器 1 帧试编码
│   ├── probe.py           ffprobe 读媒体信息
│   ├── specs.py           格式矩阵与参数规格（"能转出什么"的唯一真源）
│   ├── builder.py         EncodeSpec + 媒体信息 + 能力 → ffmpeg 命令 / Pillow 指令
│   ├── runner.py          执行 Plan：跑子进程、解析进度、异常收敛为结果对象
│   ├── queue.py           任务队列：并发、取消、重试（通过回调通知界面）
│   ├── naming.py          输出命名模板与重名避让
│   └── proxy.py           预览代理：把选中片段转成低清小文件供播放器循环
└── ui/                    界面（PySide6）
    ├── main_window.py     主窗口：任务表 + 参数面板 + 进度日志
    ├── settings_panel.py  右侧参数面板（按模式显隐控件）
    ├── spec_form.py       面板控件 ↔ 规格对象的映射（读写字段集中在这里）
    ├── task_controller.py 任务生命周期与队列之间的桥接
    ├── task_table.py      任务表的模型（文本/颜色/tooltip 都在这）
    ├── trim_dialog.py     可视化选段裁切（预览 + 时间轴）
    ├── range_slider.py    自绘范围滑块（Qt 没有内置）
    ├── merge_dialog.py    音视频合轨
    ├── info_dialog.py     媒体参数面板（可一键复制）
    ├── help_text.py       界面帮助文案（静态文本单独存放）
    └── theme.py           深色主题
```

## 二、三条架构契约

写进 `.importlinter`（由 import-linter 执行）：

1. `videotool.core` **不得**依赖 `videotool.ui`
2. `videotool.core` **不得**依赖 `PySide6`
3. 分层单向：`ui` → `core`

再加一条**只能靠 AST 检查**的（`tools/arch_check.py`）：

4. `ui` **不得直接** `import subprocess`

> 为什么拆成两套：import-linter 的 `forbidden` 契约会连带检查**间接导入**（官方文档明示 "Indirect imports will also be checked."），
> 因此"仅禁止直接导入"这类约束表达不出来——而 `ui` 经由 `core` 合法地使用子进程是允许的。
> `arch_check.py` 用 `ast` 只看直接导入，零第三方依赖，作为补充闸门。

## 三、一次转码怎么走

```
文件
 └─(probe)─→ 媒体信息（时长/分辨率/码率/流）
      └─(界面读控件)─→ EncodeSpec：mode、target、编码器、质量控制、裁切、缩放、帧率、音频策略
           └─(builder.build)─→ Plan：ffmpeg 参数数组（或 Pillow 指令）
                └─(runner.run_plan)─→ 子进程 ─→ 进度回调 ─→ 界面进度条 + 日志
                     └─(naming.build_output_path)─→ 输出路径（重名自动 _1/_2）
```

要点：

- **所有编码器先过能力校验**：不存在的自动降级，并在结果的 notes 里说明，不静默失败
- **两遍编码只给确认支持 `-pass` 的软件编码器**；硬件编码器走单遍 VBR，界面上会提示"退化为单遍近似"
- **GIF 走单命令 `split + palettegen + paletteuse`**（先算全局调色板再套用），不是逐帧抖动
- **图片走 Pillow 直接转**，不进 ffmpeg 子进程
- **`run_plan()` 不抛异常**：所有失败收敛成结果对象，界面只按结果分支

## 四、格式矩阵

以 `specs.py` 为准，这里只给轮廓（改代码以代码为准，本文不复制数值）：

| 类别 | 数量 | 说明 |
|---|---|---|
| 视频容器 | 7 | MP4 / MKV / MOV / WebM / AVI / FLV / TS；每容器限定可选编码器族与音频编码 |
| 音频格式 | 6 | MP3 / M4A / Opus / FLAC / WAV / OGG；FLAC、WAV 为无损 |
| 图片格式 | 8 | JPG / PNG / WebP / BMP / TIFF + 三种动图（GIF / 动图 WebP / APNG） |
| 任务模式 | 8 | 视频、音频、GIF、动图 WebP、APNG、图片、图片序列、合轨 |
| 质量控制 | 3 | 恒定质量 / 恒定码率 / 目标体积（两遍） |

## 五、关键问题与方案（一坑一篇）

### 1. 打包后预览播不出画面，且不报错

- **现象**：开发态选段裁切能预览，打包后窗口正常但播放器黑屏；没有任何异常
- **根因**：`QtMultimedia` 的后端插件与它依赖的 FFmpeg 运行库没被 PyInstaller 收集。缺插件时 Qt 只是"没有可用后端"，不抛错
- **解决**：`build/videotool.spec` 的 `EXCLUDES` **不能**排除 `QtMultimedia` / `QtMultimediaWidgets`；并在 `main.py --selftest` 里加了一项"播放器自检"：先造一段 2 秒素材，用播放器读时长 + 收 5 帧，出不了帧就判失败
- **预防**：凡是"漏了不报错、只是功能静默失效"的打包项，都要有可执行的验收动作，不能靠肉眼点一遍

### 2. 循环预览回跳卡顿

- **现象**：拖完选区后循环预览，每次"到结尾跳回开头"画面冻一下
- **根因**：靠 seek 回跳，每次都要从最近关键帧解码过来。实测长 GOP 素材、入点距关键帧 3.67 s 时，每次回跳冻结约 143 ms
- **解决**：`core/proxy.py` 先把选中片段转成一个低清小文件，交给播放器自身循环——全程零 seek
- **预防**：播放器能自己循环，就不要用 seek 模拟循环

### 3. "ui 不得直接调用子进程"这条约束写不进 import-linter

- **现象**：想用 import-linter 表达"ui 只能通过 core 起子进程"，写不出来
- **根因**：`forbidden` 契约连**间接**导入一起查，`ui → core → subprocess` 会被误判违规
- **解决**：拆成两道闸门——间接层用 import-linter（`ui` 依赖 `core` 的分层契约），"仅直接导入"用 `tools/arch_check.py` 走 AST
- **预防**：约束的表达能力要和工具的语义对齐；表达不了就换工具，别把约束写成注释

### 4. 硬件编码器"列表里有"不等于"本机能用"

- **现象**：`ffmpeg -encoders` 里明明有 `h264_nvenc`，选它却直接失败
- **根因**：编码器编译进 ffmpeg 了，但机器没对应显卡/驱动
- **解决**：`capabilities.py` 对每个候选做 **1 帧真实试编码**（`color` 源 256x144 一帧），失败记入 `hw_failed`，界面不显示；探测总耗时约 1~3 秒，放在窗口显示之后后台跑
- **预防**：能力探测要以"能跑通一次"为准，不以"注册表里有"为准

### 5. 两个 ffmpeg 二进制贴着 GitHub 的硬上限

- **现象**：`assets/ffmpeg/` 下两个 exe 分别约 98.09 MiB 与 97.90 MiB
- **根因**：GitHub 对单文件有 100 MiB 硬限制（超过直接拒收），50 MiB 以上只警告
- **风险**：换更大的 ffmpeg 构建（比如加更多编码器或带调试符号的版本）会推不上去
- **方案**：升级前先量体积；真要更大就走 Releases 附件分发，不塞进 Git 历史

### 6. 仓库没有锁依赖版本

- **现状**：没有 `requirements.txt`，`PySide6` / `Pillow` 的版本只隐含在发布包里
- **影响**：换机器或换时间重建，可能装到行为不同的 Qt 版本（尤其多媒体后端）
- **建议**：发布时以实际打包环境的版本为准，补一份 `requirements.txt`（含 PySide6、Pillow 的精确版本）；本次未做，避免凭空写版本号

## 六、验证入口

| 命令 | 覆盖 |
|---|---|
| `python tools/arch_check.py` | 直接导入约束（AST） |
| `python tools/smoke.py` | 引擎主路径：几类格式真实转码 |
| `python tools/ui_smoke.py` | 界面全流程：添加 → 参数 → 开始 → 完成（离屏） |
| `python tools/features_smoke.py` | 时间裁切、分段导出、音视频合轨 |
| `VideoToolbox.exe --selftest <报告路径>` | **打包后**验收：工具链、能力、界面、端到端转码、预览播放器出帧 |

发布前最小组合：`arch_check` + `smoke` + 打包后 `--selftest`。

## 七、已知缺口

- 依赖未锁版本（见五.6）
- 仓库未附 LICENSE
- `docs/` 尚未拆分：本文超 200 行、或 `AGENTS.md` 超 150 词时再按规范拆出 `docs/架构.md` 与 `docs/问题记录/`
