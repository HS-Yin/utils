"""Build the single-file Windows executable (PyInstaller onefile) and the zip.

Usage:  E:\\software\\python310\\python.exe -X utf8 package_portable.py
The build venv (see README) supplies PyInstaller; the app itself needs nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "模拟输入小助手"
SOURCE = ROOT / "01-模拟输入小助手" / "模拟输入小助手.py"
ICON = ROOT / "keyboard_buddy.ico"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
EXE = DIST / (APP_NAME + ".exe")
GUIDE = DIST / "使用说明.txt"
ZIP = DIST / (APP_NAME + "-便携版.zip")
RELEASE_ZIP = ROOT / "01-模拟输入小助手" / "release" / (APP_NAME + ".zip")
LEGACY_DIR = DIST / APP_NAME  # old embedded-runtime layout

GUIDE_TEXT = (
    "模拟输入小助手（便携版）\n\n"
    "双击“模拟输入小助手.exe”即可运行：单文件，不需要安装 Python，也不需要 .NET。\n"
    "这个 exe 没有代码签名，Windows 可能提示“已保护你的电脑”：点“更多信息 → 仍要运行”即可，同一份 exe 只需放行一次。\n"
    "若主按钮显示“无法输入”且状态栏提示低完整性：说明 exe 放在受控目录（如沙箱工作区）里，\n"
    "  Windows 会丢弃按键，请把 exe 复制到桌面或 D:\\ 等普通目录再运行。\n"
    "默认是“本机文本”模式：发送 Unicode 字符，支持中文，适合在本机编辑器里输入。\n"
    "“VM 控制台”模式发送真实键盘扫描码，适合往 VMware / VirtualBox 控制台里输入：\n"
    "  仅支持英文、数字、半角符号、Tab 与换行；VM 内请使用美式键盘并关闭 Caps Lock。\n"
    "字符间隔与开始倒计时默认 0.01 秒 / 2 秒，可按需调整。\n"
    "把文件从资源管理器直接拖到窗口上即可作为输入，也可以点“浏览”选择。\n"
    "点“开始输入”后切到目标编辑器；没切过去不会打字，会锁定你第一次切到的那个窗口。\n"
    "输入中主按钮会变成“暂停”：点它或按 F8 可暂停/继续；点“停止”或按 F9 结束本次输入。\n"
    "切走窗口会自动暂停，切回后自动继续，已经输入过的字符不会重复。\n"
    "目标编辑器的自动括号或缩进可能改变输出。\n"
    "单文件版每次启动会把运行时解压到临时目录，首次启动约 1–3 秒，之后更快，属正常现象。\n"
    "若目标编辑器以管理员身份运行，本工具也要以管理员身份运行。\n"
    "运行细节会记录在 exe 旁边的“模拟输入.log”，便于排查问题。\n"
)


def run(command: list[str]) -> None:
    print("$ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def build() -> None:
    if sys.platform != "win32":
        raise RuntimeError("只能在 Windows 上打包")
    if not SOURCE.is_file():
        raise RuntimeError(f"找不到源码：{SOURCE}")
    try:
        version = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            cwd=ROOT, check=True, capture_output=True, text=True,
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
