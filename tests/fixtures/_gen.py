#!/usr/bin/env python3
"""生成 3 秒 320x240 测试视频：testsrc + 正弦音轨 + drawtext 中文（字体缺则退回 testsrc 计数器）。"""
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent
OUT = FIXTURE_DIR / "test_3s.mp4"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

def has_font(font_path: str) -> bool:
    return Path(font_path).exists()

# 优先尝试 Windows 中文字体
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\msyh.ttf",
]

FONT = next((f for f in FONT_CANDIDATES if has_font(f)), None)

def run(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    return p.returncode, p.stdout, p.stderr

def main() -> int:
    if OUT.exists():
        print(f"已存在 {OUT}，跳过")
        return 0

    if FONT:
        # 带中文 drawtext
        # Windows 路径在 ffmpeg filter 中需要转义冒号和反斜杠
        # 使用正斜杠，冒号前加反斜杠转义
        font_file = FONT.replace('\\', '/')
        # 对于 filter 中的 fontfile，需要特殊转义
        font_file_escaped = font_file.replace(':', r'\:')
        vf = f"drawtext=text='测试视频 中文OCR':fontsize=24:fontcolor=white:x=50:y=50:fontfile={font_file_escaped}"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k",
            "-shortest", str(OUT)
        ]
    else:
        # 退回纯 testsrc（有计数器数字，OCR 也能识别）
        print("未找到中文字体，退回纯 testsrc（数字计数器）")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k",
            "-shortest", str(OUT)
        ]

    code, out, err = run(cmd)
    if code != 0:
        # 如果带字体失败，尝试不带字体
        if FONT:
            print(f"带字体生成失败，尝试不带字体: {err}")
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "64k",
                "-shortest", str(OUT)
            ]
            code, out, err = run(cmd)
            if code != 0:
                print(f"ffmpeg 失败: {err}")
                return code
        else:
            print(f"ffmpeg 失败: {err}")
            return code
    print(f"生成完成: {OUT} ({OUT.stat().st_size} bytes)")
    return 0

if __name__ == "__main__":
    sys.exit(main())