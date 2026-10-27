#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI-HFSS · AEDT 脚本按钮：打开 DSH 的 AEDT 内嵌对话（focus 页面）。

在 AEDT 里加按钮（3 步）
------------------------
1. 先启动 DSH 桌面版，把它 Web 界面的端口记下来，设置环境变量
   AIHFSS_DSH_PORT（或在下面 DEFAULT_DSH_PORT 处改死）。默认 59991。
   —— 端口不是猜的：DSH 地址栏 http://127.0.0.1:<port>/ 里的那个 <port>。
2. AEDT 菜单 Tools → Run Script… 选择本文件运行一次：脚本会读取当前
   工程/设计，POST 给 DSH，并打开 ?aihfss=focus&session=... 的无侧栏对话页。
3. 想变成常驻按钮：把
   "<python.exe>" "<本文件路径>"
   做成一个快捷方式/命令，在 AEDT 里用 Tools → Customize…（部分版本是
   右键功能区 → Customize the Ribbon）挂到自定义工具栏或快速访问工具栏。
   不同 AEDT 版本菜单位置不同；找不到时每次用 Tools → Run Script 也一样能用。

说明
----
* 只用 CPython 标准库，不依赖 PyAEDT，也不需要事先装任何包。
* 工程来源优先级：AEDT 注入的 oProject/oDesign 全局 → oDesktop 的当前工程
  → 命令行参数 → 环境变量 AEDT_PROJECT / AEDT_DESIGN。
* 失败时打印原因（Windows 上再弹一个消息框），并给出手工命令。
* 本脚本只上报上下文 + 请求打开会话，不修改工程、不启动求解。
"""

import json
import os
import sys
import traceback
import webbrowser
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    # Only used for the optional Windows message box. Some AEDT script hosts
    # expose a restricted interpreter; the script must still run there.
    import ctypes
except Exception:  # pragma: no cover - restricted AEDT script host
    ctypes = None

# ---------------------------------------------------------------------------
# 可改的常量
# ---------------------------------------------------------------------------
# DSH Web 端口。默认 59991；实际端口以 DSH 地址栏为准，可用环境变量
# AIHFSS_DSH_PORT（或 DSH_HOST_PORT）覆盖，脚本不用改。
DEFAULT_DSH_PORT = 59991
# DSH 的宿主护栏只接受 loopback 调用，所以这里固定回环地址。
DSH_HOST = "127.0.0.1"
OPEN_PATH = "/api/ai-hfss/aedt/open"
HTTP_TIMEOUT_SECONDS = 15


def _say(message):
    """Print without ever dying on a narrow Windows console code page."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("ascii", "replace").decode("ascii"))
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _notify(title, message):
    """Best-effort modal notice; never let it mask the real error."""
    _say("[ai-hfss] " + title + ": " + message)
    if (
        ctypes is None
        or os.name != "nt"
        or os.environ.get("AIHFSS_NO_GUI", "").strip() not in ("", "0", "false", "False")
    ):
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000010)  # MB_ICONERROR
    except Exception:
        # The print above already carried the message.
        pass


def _call(obj, method):
    """Call an AEDT COM method only if it exists; AEDT versions differ."""
    if obj is None:
        return None
    fn = getattr(obj, method, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _text(value):
    return "" if value is None else str(value).strip()


def _from_aedt_globals():
    """Read the project/design AEDT injects into the script globals."""
    project = globals().get("oProject")
    design = globals().get("oDesign")
    desktop = globals().get("oDesktop")
    if project is None and desktop is not None:
        # Older script hosts only inject oDesktop; ask it for the active project.
        project = _call(desktop, "GetActiveProject")
    if design is None and project is not None:
        design = _call(project, "GetActiveDesign")

    project_path = ""
    if project is not None:
        raw_path = _text(_call(project, "GetPath"))
        raw_name = _text(_call(project, "GetName"))
        if raw_path.lower().endswith(".aedt"):
            project_path = raw_path
        elif raw_path and raw_name:
            if not raw_name.lower().endswith(".aedt"):
                raw_name += ".aedt"
            project_path = os.path.join(raw_path, raw_name)
        else:
            project_path = raw_path or raw_name

    design_name = _text(_call(design, "GetName"))
    return project_path, design_name


def _positional_args():
    """Positional args only: skip flags and the value that belongs to --prompt."""
    argv = sys.argv[1:]
    values = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--prompt":
            index += 2
            continue
        if item.startswith("--prompt=") or item.startswith("-"):
            index += 1
            continue
        values.append(item)
        index += 1
    return values


def _from_argv_or_env():
    positional = _positional_args()
    project = positional[0] if len(positional) >= 1 else ""
    design = positional[1] if len(positional) >= 2 else ""
    project = project or os.environ.get("AEDT_PROJECT", "").strip()
    design = design or os.environ.get("AEDT_DESIGN", "").strip()
    return project, design


def _prompt_from_args():
    argv = sys.argv[1:]
    for index, item in enumerate(argv):
        if item == "--prompt" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--prompt="):
            return item.split("=", 1)[1]
    return os.environ.get("AIHFSS_PROMPT", "").strip()


def _dsh_port():
    for name in ("AIHFSS_DSH_PORT", "DSH_HOST_PORT"):
        raw = os.environ.get(name, "").strip()
        if raw == "":
            continue
        try:
            port = int(raw)
        except ValueError:
            raise RuntimeError(name + "=" + repr(raw) + " 不是整数端口")
        if not 1 <= port <= 65535:
            raise RuntimeError(name + "=" + str(port) + " 超出端口范围")
        return port
    return DEFAULT_DSH_PORT


def _post_open(port, payload):
    """POST the hand-off request and return the decoded JSON object."""
    url = "http://" + DSH_HOST + ":" + str(port) + OPEN_PATH
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urlrequest.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    # Host guard: mutating routes require x-ai-hfss=1 from a loopback socket.
    request.add_header("x-ai-hfss", "1")
    # Never route a loopback call through a corporate/system HTTP proxy.
    opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
    except urlerror.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise RuntimeError("DSH 返回 HTTP " + str(exc.code) + ": " + detail)
    except urlerror.URLError as exc:
        raise RuntimeError(
            "连不上 DSH（" + url + "）：" + str(exc.reason) + "\n"
            "确认 DSH 桌面版已启动，且 AIHFSS_DSH_PORT 指向它的 Web 端口。"
        )
    try:
        result = json.loads(raw)
    except ValueError:
        raise RuntimeError("DSH 返回的不是 JSON：" + raw[:300])
    if not isinstance(result, dict) or result.get("ok") is not True:
        code = result.get("code") if isinstance(result, dict) else None
        raise RuntimeError("DSH 拒绝打开会话（code=" + str(code) + "）：" + raw[:500])
    return result


def _open_url(url):
    """Prefer the Windows shell (opens the default app), then any browser."""
    starter = getattr(os, "startfile", None)
    if starter is not None:
        try:
            starter(url)
            return "os.startfile"
        except OSError:
            pass
    try:
        if webbrowser.open(url, new=2):
            return "webbrowser.open"
    except Exception:
        pass
    return "manual"


def _manual_hint(port, payload):
    json_text = json.dumps(payload, ensure_ascii=False).replace("'", "''")
    command = (
        'Invoke-RestMethod -Method Post -Uri "http://'
        + DSH_HOST
        + ":"
        + str(port)
        + OPEN_PATH
        + '" -Headers @{"x-ai-hfss"="1"} -ContentType "application/json" -Body \''
        + json_text
        + "'"
    )
    return (
        "手工打开（PowerShell，复制成一行）：\n  "
        + command
        + "\n然后把返回 focus_url 里的 ?aihfss=focus&session=... 粘到 DSH 地址后面。\n"
        + "（端口不对时先设 AIHFSS_DSH_PORT，再重新运行本脚本。）"
    )


def main():
    payload = {}
    try:
        project_path, design_name = _from_aedt_globals()
        if not project_path:
            project_path, design_name = _from_argv_or_env()
        if not project_path:
            raise RuntimeError(
                "无法从 AEDT 读取当前工程。\n"
                "请先在 AEDT 里打开工程，或手工传入：\n"
                '  python ai_hfss_chat.py "C:\\path\\to\\Project.aedt" "HFSSDesign1"\n'
                "也可以设置环境变量 AEDT_PROJECT / AEDT_DESIGN。"
            )
        port = _dsh_port()
        payload["project_path"] = project_path
        if design_name:
            payload["design_name"] = design_name
        prompt = _prompt_from_args()
        if prompt:
            payload["prompt"] = prompt

        _say("[ai-hfss] 上报工程：" + project_path + (" / " + design_name if design_name else ""))
        result = _post_open(port, payload)
        focus_url = _text(result.get("focus_url"))
        if not focus_url:
            raise RuntimeError("DSH 返回缺少 focus_url：" + json.dumps(result, ensure_ascii=False)[:300])
        how = _open_url(focus_url)
        session_id = _text(result.get("session_id"))
        _say("[ai-hfss] 已创建会话 " + (session_id or "(unknown)") + "，打开：" + focus_url + "（" + how + "）")
        if how == "manual":
            _say("[ai-hfss] 自动打开失败，请手动访问上面的 URL。")
        return 0
    except Exception as exc:
        port = DEFAULT_DSH_PORT
        try:
            port = _dsh_port()
        except Exception:
            pass
        message = str(exc) + "\n\n" + _manual_hint(port, payload)
        _notify("AI-HFSS 打开失败", message)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    main()
