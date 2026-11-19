"""Build the single-file Windows executable (PyInstaller onefile) and the zip.

Usage (from this folder):
    python -m venv .venv-build --system-site-packages
    .venv-build\\Scripts\\python.exe -m pip install pyinstaller
    .venv-build\\Scripts\\python.exe -X utf8 package_portable.py

The build venv supplies PyInstaller; the app itself needs nothing but Windows.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP_NAME = "模拟输入小助手"
SOURCE = BASE / "模拟输入小助手.py"
ICON = BASE / "icon.ico"
DIST = BASE / "dist"
BUILD = BASE / "build"
EXE = DIST / (APP_NAME + ".exe")
GUIDE = DIST / "使用说明.txt"
ZIP = DIST / (APP_NAME + "-便携版.zip")
RELEASE_ZIP = BASE / "release" / (APP_NAME + ".zip")
LEGACY_DIR = DIST / APP_NAME  # old embedded-runtime layout

GUIDE_TEXT = """模拟输入小助手（便携版）
════════════════════════════════════════

单文件程序。双击「模拟输入小助手.exe」运行，无需 Python，也无需 .NET。

【快速上手】
1  拖文件到窗口，或点「浏览」选择 UTF-8 文本
2  选输入方式
     · 本机文本：Unicode 字符，支持中文
     · VM 控制台：真实键盘扫描码，仅英文、数字、半角符号
3  点「开始输入」，在倒计时内切到目标窗口并放好光标
   默认参数：字符间隔 0.01 秒、开始倒计时 2 秒

【运行中控制】
· F8：暂停 / 继续
· F9：停止
· 切走窗口：自动暂停，切回后自动续打，已输入内容不重复
· 主按钮：开始输入 → 暂停 → 继续，三种状态循环

【首次运行必读】
· 未签名：Windows 可能提示「已保护你的电脑」
  → 点「更多信息」→「仍要运行」，同一份 exe 只需放行一次
· 首次启动把运行时解压到临时目录，约 1–3 秒属正常
· 主按钮变灰「无法输入」：exe 目录带 Low 完整性标签，按键被静默丢弃
  → 把 exe 复制到桌面或 D:\\ 再运行

【排错】
· 目标程序以管理员身份运行时，本工具也要以管理员身份运行
· 编辑器的自动括号 / 缩进会改写最终文字，需逐字一致请先关闭
· 详细运行记录写在 exe 旁边的「模拟输入.log」
"""


def run(command: list[str]) -> None:
    print("$ " + " ".join(command))
    subprocess.run(command, cwd=BASE, check=True)


def build() -> None:
    if sys.platform != "win32":
        raise RuntimeError("只能在 Windows 上打包")
    if not SOURCE.is_file():
        raise RuntimeError(f"找不到源码：{SOURCE}")
    try:
        version = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            cwd=BASE, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RuntimeError(
            "当前 Python 里没有 PyInstaller，请先安装："
            "python -m pip install pyinstaller（可用清华镜像）"
        ) from exc
    print(f"PyInstaller {version}")

    for stale in (EXE, ZIP, GUIDE):
        stale.unlink(missing_ok=True)
    for stale_dir in (BUILD, LEGACY_DIR):
        if stale_dir.exists():
            shutil.rmtree(stale_dir)
    DIST.mkdir(exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
    ]
    if ICON.is_file():
        command += ["--icon", str(ICON)]
    command.append(str(SOURCE))
    run(command)

    if not EXE.is_file():
        raise RuntimeError(f"打包失败，没有生成 {EXE}")
    size_mb = EXE.stat().st_size / 1024 / 1024
    print(f"单文件程序：{EXE}  ({size_mb:.1f} MB)")

    GUIDE.write_text(GUIDE_TEXT, encoding="utf-8")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in (EXE, GUIDE):
            archive.write(path, f"{APP_NAME}/{path.name}")
    print(f"便携压缩包：{ZIP}  ({ZIP.stat().st_size / 1024 / 1024:.1f} MB)")

    RELEASE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZIP, RELEASE_ZIP)
    print(f"已同步：{RELEASE_ZIP}")


if __name__ == "__main__":
    build()
