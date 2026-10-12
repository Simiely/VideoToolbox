"""界面帮助文案：与逻辑代码分开存放，改文案不必翻主窗口。

只放静态说明文本；依赖运行时状态的部分（如本机硬件编码器清单）
由调用方算好后以参数传入。
"""

from __future__ import annotations

from .theme import COLORS


def encoder_help_html(hardware_text: str) -> str:
    """编码器选择说明的 HTML。

    hardware_text：本机可用硬件编码器的一行描述，由主窗口按能力探测结果生成。
    """
    c = COLORS

    # (编码器, 同画质体积, 速度, 兼容性, 什么时候选它)
    rows = [
        ("H.264 / x264", "中等", "慢", "最好", "拿不准就用它。通用、上传网站、发给别人首选"),
        ("H.265 / x265", "比 H.264 小 30%~50%", "更慢", "较新设备才行", "自己存档、只在电脑/新手机上看"),
        ("AV1", "比 H.265 再小 20%~30%", "很慢", "支持设备少", "极致压体积，愿意等（几分钟到几小时）"),
        ("VP9", "接近 H.265", "慢", "网页友好", "目标格式是 WebM 时用"),
        ("硬件加速", "比软编大 20%~50%", "快 5~20 倍", "同对应格式", "赶时间、批量处理、不在乎体积时"),
    ]
    table_rows = "".join(
        "<tr>"
        f"<td style='padding:4px 8px; color:{c['text']};'><b>{name}</b></td>"
        f"<td style='padding:4px 8px; color:{c['text']};'>{size}</td>"
        f"<td style='padding:4px 8px; color:{c['text']};'>{speed}</td>"
        f"<td style='padding:4px 8px; color:{c['text']};'>{compat}</td>"
        f"<td style='padding:4px 8px; color:{c['muted']};'>{when}</td>"
        "</tr>"
        for name, size, speed, compat, when in rows
    )

    return f"""
        <div style="color:{c['text']};">
          <p style="font-size:15px; font-weight:bold; color:{c['accent']};">结论：追求质量就保持默认</p>
          <p>默认顺序是<b>软件编码优先</b>，第一个就是当前格式下画质最划算的选择（MP4 即 H.264）。软件编码同画质体积最小，只是速度慢；硬件加速只是快，体积会明显变大。</p>
          <p style="color:{c['muted']};">本机可用的硬件编码器：{hardware_text}</p>

          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr style="background-color:{c['input']};">
              <th align="left" style="padding:5px 8px; color:{c['muted']};">编码器</th>
              <th align="left" style="padding:5px 8px; color:{c['muted']};">同画质体积</th>
              <th align="left" style="padding:5px 8px; color:{c['muted']};">速度</th>
              <th align="left" style="padding:5px 8px; color:{c['muted']};">兼容性</th>
              <th align="left" style="padding:5px 8px; color:{c['muted']};">什么时候选它</th>
            </tr>
            {table_rows}
          </table>

          <p style="font-weight:bold; color:{c['accent']}; margin-top:14px;">质量控制怎么选</p>
          <ul>
            <li><b>恒定质量（推荐）</b>：CRF 数值越小画质越好、体积越大。x264 的 20~24、x265 的 24~28 是公认的甜点区间，程序已按编码器填好默认值。</li>
            <li><b>恒定码率</b>：要严格控制文件大小时用，画质会随画面复杂度波动。</li>
            <li><b>指定目标体积</b>：自动做两遍编码逼近目标大小，最精确，但耗时约翻倍；硬件编码器不支持两遍，会自动退化为单遍近似并在日志里提示。</li>
          </ul>

          <p style="font-weight:bold; color:{c['accent']}; margin-top:14px;">这些地方跟编码器无关</p>
          <ul>
            <li><b>GIF / 动图 WebP / APNG</b>：固定用 CPU 编码（调色板生成、libwebp），没有硬件加速。</li>
            <li><b>音频</b>：aac / mp3 / opus 等全部是 CPU 编码，硬件编码器只管视频。</li>
            <li><b>静态图片</b>：由 Pillow 处理，也全部是 CPU。</li>
          </ul>

          <p style="color:{c['muted']}; margin-top:14px;">速度预设：越靠后体积越小、越慢。软编选「中等（推荐）」起步；硬件编码的 P1~P7 是越快数字越小。</p>
        </div>
        """