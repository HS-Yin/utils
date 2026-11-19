# 模拟输入小助手

一个 Windows 图形小工具，将 UTF-8 文件中的文字模拟为键盘输入。适合把代码或文本输入到指定窗口；界面提供文件选择、输入速度、倒计时和进度显示。

## 下载成品

[下载 Windows 便携版 ZIP](release/模拟输入小助手.zip)（约 8 MB）。解压后只有一个 `模拟输入小助手.exe` 和一份使用说明：**单文件，双击即用**，不需要安装 Python，也不需要 .NET。

这个 exe 没有代码签名，Windows 可能弹出“已保护你的电脑”提示，点“更多信息 → 仍要运行”即可（同一份 exe 只需放行一次）。单文件版每次启动会把运行时解压到临时目录，首次约 1–3 秒，属正常现象。

## 使用

1. 选择要输入的 UTF-8 文件，或直接把文件拖到窗口上。
2. 选择“本机文本”（支持中文）或“VM 控制台”（发送扫描码，适合虚拟机控制台），并按需调整字符间隔与开始倒计时。
3. 点击“开始输入”，在倒计时结束前切换到目标窗口，并将光标放在输入位置。
4. 输入过程中主按钮会变成“暂停”：点它或按 F8 暂停/继续；点“停止”或按 F9 结束本次输入。切走窗口会自动暂停，切回后自动继续。

程序按文件内容发送按键。若目标编辑器启用了自动补全括号或自动缩进，最终文字可能与原文件不同；需要逐字一致时请先关闭这些功能。

## 常见问题：主按钮显示“无法输入”

程序启动时会自检自身的 Windows 完整性级别。若 exe 位于受控目录（例如沙箱工作区 `F:\DSH\...`），文件会带上 Low 完整性标签，程序随之以低完整性进程运行；此时 Windows 会静默丢弃发往普通程序的按键（界面显示“发送完成”，目标窗口却一个字没有）。把 exe 复制到桌面或 `D:\` 等普通目录再运行即可恢复正常，程序不再显示该提示。

## 源码

主程序是 [模拟输入小助手.py](模拟输入小助手.py)，仅使用 Python 标准库。Windows 上安装带 Tkinter 的 Python 3.10 或更新版本后，可直接运行：

```powershell
python 模拟输入小助手.py
```

## 打包

仓库根目录的 [package_portable.py](../../package_portable.py) 用 PyInstaller 生成单文件 exe 和 zip：

```powershell
python -m venv .venv-build --system-site-packages
.venv-build\Scripts\python.exe -m pip install pyinstaller   # 直连 PyPI 超时可用 -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv-build\Scripts\python.exe -X utf8 package_portable.py
```

`--icon` 依赖根目录的 `keyboard_buddy.ico`，两者都要保留。产物写入根目录的 `dist\`：`模拟输入小助手.exe`（单个 8 MB 文件）、`使用说明.txt`、`模拟输入小助手-便携版.zip`，并自动同步到本目录的 `release\模拟输入小助手.zip`。

本工具的源码和便携版均位于当前目录，不依赖合集仓库中的其他工具。
