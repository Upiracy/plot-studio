#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧情框架 · Agent 侧命令行

给外部 agent（任何能执行命令的智能体）用的最小工具集：
把页面当作自己界面的一部分时，用这里的命令收发消息、提交改动、驱动界面。

    python agent.py status                     看会话与页面连接状态
    python agent.py state                      读页面当前状态（选中了什么、在哪个视图）
    python agent.py poll --wait 60             等用户说话（长轮询）
    python agent.py poll --wait 60 --loop      持续等，每条消息输出一行 NDJSON
    python agent.py say "…"                    回话给页面
    python agent.py patch ops.json             提交一批改动（结构同上，需用户确认）
    python agent.py cmd center --args '{"id":"ev_1"}'
    python agent.py reset                      清空会话

所有命令输出 JSON，方便 agent 直接解析。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT_FILE = os.path.join(ROOT, ".port")


def parse_args_arg(s):
    """宽容地解析 --args。

    不同 shell 对引号的处理不一样（PowerShell 会把 JSON 里的双引号吃掉），所以三种写法都收：
        --args '{"id":"ev_1"}'     标准 JSON
        --args 'id=ev_1,center=true'  键值对，值会自动识别数字与布尔
        --args '@args.json'        从文件读
    """
    s = (s or "").strip()
    if not s:
        return {}
    if s.startswith("@"):
        with open(s[1:], "r", encoding="utf-8") as f:
            return json.load(f)
    if s.startswith("{"):
        try:
            return json.loads(s)
        except ValueError:
            # PowerShell 可能把双引号吞掉了，试着补回来
            fixed = re.sub(r'([{,]\s*)([A-Za-z_][\w-]*)(\s*:)', r'\1"\2"\3', s)
            try:
                return json.loads(fixed)
            except ValueError:
                raise SystemExit("--args 不是合法 JSON，可改用 id=xxx,center=true 这种写法")
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        elif re.fullmatch(r"-?\d+", v):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def base_url(explicit_port=None):
    port = explicit_port
    if not port:
        try:
            with open(PORT_FILE, "r", encoding="utf-8") as f:
                port = int(f.read().strip())
        except Exception:
            port = 8760
    return "http://127.0.0.1:%d" % port


def call(url, path, data=None, timeout=150):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url + path, data=body, method="POST" if body else "GET")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": "HTTP %s" % e.code}
    except Exception as e:
        return {"error": "无法连接本地服务（先启动 启动.bat）：%s" % e}


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="剧情框架 Agent 侧命令行")
    ap.add_argument("--port", type=int, default=None, help="本地服务端口，默认读 .port")
    sub = ap.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="会话状态")
    sub.add_parser("state", help="页面当前状态")

    p = sub.add_parser("poll", help="等待页面发来的消息")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--wait", type=int, default=0, help="长轮询秒数，0 表示立即返回")
    p.add_argument("--loop", action="store_true", help="持续等待，每条消息输出一行")

    p = sub.add_parser("say", help="回话给页面")
    p.add_argument("text")
    p.add_argument("--patch", default=None, help="附带改动提案的 JSON 文件路径")
    p.add_argument("--cmd", default=None, help="附带界面指令名")
    p.add_argument("--args", default=None, help="界面指令参数 JSON")

    p = sub.add_parser("patch", help="提交一批改动")
    p.add_argument("file", help="含 {summary, ops} 的 JSON 文件，'-' 表示从 stdin 读")
    p.add_argument("--text", default="", help="随改动一起说的话")

    p = sub.add_parser("cmd", help="驱动界面")
    p.add_argument("cmd")
    p.add_argument("--args", default="{}")
    p.add_argument("--text", default="")

    p = sub.add_parser("log", help="取全部会话记录")
    p.add_argument("--since", type=int, default=0)

    sub.add_parser("reset", help="清空会话")

    args = ap.parse_args()
    url = base_url(args.port)

    if args.action == "status":
        emit(call(url, "/api/session/status"))
    elif args.action == "state":
        emit(call(url, "/api/session/state"))
    elif args.action == "log":
        emit(call(url, "/api/session/log?since=%d" % args.since))
    elif args.action == "reset":
        emit(call(url, "/api/session/reset", {}))
    elif args.action == "poll":
        since = args.since
        while True:
            r = call(url, "/api/session/inbox?since=%d&wait=%d" % (since, max(0, min(args.wait, 120))))
            for it in r.get("items", []):
                since = max(since, it["id"])
                emit(it)
            if not args.loop:
                if not r.get("items"):
                    emit({"items": [], "seq": r.get("seq"), "pageConnected": r.get("pageConnected")})
                break
            if not r.get("items"):
                emit({"heartbeat": True, "pageConnected": r.get("pageConnected")})
    elif args.action == "say":
        patch = None
        if args.patch:
            with open(args.patch, "r", encoding="utf-8") as f:
                patch = json.load(f)
        cmds = []
        if args.cmd:
            cmds.append({"cmd": args.cmd, "args": parse_args_arg(args.args)})
        emit(call(url, "/api/session/say", {"text": args.text, "patch": patch, "commands": cmds}))
    elif args.action == "patch":
        raw = sys.stdin.read() if args.file == "-" else open(args.file, "r", encoding="utf-8").read()
        payload = json.loads(raw)
        emit(call(url, "/api/session/say", {
            "text": args.text,
            "patch": {"summary": payload.get("summary", "agent 改动"), "ops": payload.get("ops", [])},
        }))
    elif args.action == "cmd":
        emit(call(url, "/api/session/command",
                  {"cmd": args.cmd, "args": parse_args_arg(args.args), "text": args.text}))


if __name__ == "__main__":
    main()
