#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧情框架 本地启动器

为什么需要它（见 docs/adr/0002-local-launcher-file-as-truth.md）：
  file:// 页面的 origin 是 opaque origin，浏览器会直接拒绝 File System Access API，
  且 Origin: null 的跨域请求会被 CORS 拦截。因此页面必须由 http://127.0.0.1 托管，
  AI 请求也统一经由本进程转发，以绕开各家 API 的 CORS 差异并让 Key 留在本地。

它只做四件事：
  1. 静态托管剧情框架目录
  2. 读写 工程/*.json（原子写入）
  3. 监听工程文件变更并通过 SSE 主动推送给页面
  4. 把 /api/ai 的请求转发给配置的 OpenAI 兼容接口（流式透传）

仅使用 Python 标准库，Python 3.8+ 均可运行。
"""

import json
import os
import sys
import time
import socket
import hashlib
import threading
import webbrowser
import mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(ROOT, "工程")
CONFIG_PATH = os.path.join(ROOT, "config.json")
DEFAULT_PORT = 8760
SERVER_VERSION = "1.0.0"

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def safe_project_path(name):
    """把工程名限制在 工程/ 目录内，防止路径穿越。"""
    if not name:
        return None
    name = os.path.basename(unquote(str(name)).replace("\\", "/"))
    if not name.lower().endswith(".json"):
        name += ".json"
    if name.startswith("."):
        return None
    return os.path.join(PROJECT_DIR, name)


def read_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def file_fingerprint(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            h.update(f.read())
    except OSError:
        return None
    return "%d-%d-%s" % (st.st_mtime_ns, st.st_size, h.hexdigest())


def list_projects():
    if not os.path.isdir(PROJECT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(PROJECT_DIR)):
        if name.lower().endswith(".json") and not name.startswith("."):
            path = os.path.join(PROJECT_DIR, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append({"file": name, "size": st.st_size, "mtime": int(st.st_mtime * 1000)})
    return out


# --------------------------------------------------------------------------
# 会话桥（Agent Bridge）
#
# 让这个页面可以被外部 agent 加载为它界面的一部分：页面里的对话框不再直连模型，
# 而是把消息投进这里，agent 通过 HTTP 长轮询取走；agent 的回复、改动提案与界面指令
# 同样经由这里回推给页面。双方都只依赖 HTTP，不需要 WebSocket。
# --------------------------------------------------------------------------

class Session:
    AGENT_TTL = 90          # 秒。超过这个时间没有任何 agent 动作，就认为没人应答

    def __init__(self):
        self.cv = threading.Condition()
        self.seq = 0
        self.log = []          # [{id, from, text, patch, commands, context, ts}]
        self.state = {}        # 页面最近一次上报的状态
        self.reported_at = 0
        self.agent_at = 0      # agent 最近一次活动的时刻

    def touch_agent(self):
        with self.cv:
            self.agent_at = time.time()

    def add(self, sender, text="", patch=None, commands=None, context=None):
        with self.cv:
            self.seq += 1
            item = {
                "id": self.seq, "from": sender, "text": text or "",
                "patch": patch, "commands": commands or [],
                "context": context, "ts": int(time.time() * 1000),
            }
            self.log.append(item)
            if len(self.log) > 800:
                self.log = self.log[-800:]
            self.cv.notify_all()
            return item

    def since(self, sid):
        with self.cv:
            return [m for m in self.log if m["id"] > sid]

    def wait_since(self, sid, timeout):
        """长轮询：等到有比 sid 更新的消息，或超时。"""
        deadline = time.time() + timeout
        with self.cv:
            while True:
                items = [m for m in self.log if m["id"] > sid]
                if items:
                    return items
                remain = deadline - time.time()
                if remain <= 0:
                    return []
                self.cv.wait(remain)

    def report(self, state):
        with self.cv:
            self.state = state or {}
            self.reported_at = time.time()

    def status(self):
        with self.cv:
            age = time.time() - self.reported_at if self.reported_at else None
            agent_age = time.time() - self.agent_at if self.agent_at else None
            return {
                "seq": self.seq,
                "pageConnected": age is not None and age < 12,
                "lastReportAge": age,
                "agentListening": agent_age is not None and agent_age < self.AGENT_TTL,
                "lastAgentAge": agent_age,
                "pendingFromPage": len([m for m in self.log if m["from"] == "user"]),
                "state": self.state,
            }


SESSION = Session()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "PlotStudio/" + SERVER_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/watch" in (self.path or ""):
            return
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------------- 基础响应 ----------------

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------------- 路由 ----------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            return self._json(200, {"ok": True, "version": SERVER_VERSION, "root": ROOT})
        if path == "/api/config":
            cfg = read_config()
            # 不回传完整 key，只回传是否已配置
            return self._json(200, {
                "baseURL": cfg.get("baseURL", ""),
                "model": cfg.get("model", ""),
                "hasKey": bool(cfg.get("apiKey")),
                "keyTail": (cfg.get("apiKey", "")[-4:] if cfg.get("apiKey") else ""),
            })
        if path == "/api/list":
            return self._json(200, {"projects": list_projects()})
        if path == "/api/project":
            fn = (query.get("file") or [""])[0]
            fp = safe_project_path(fn)
            if not fp:
                return self._json(400, {"error": "非法工程名"})
            if not os.path.exists(fp):
                return self._json(404, {"error": "工程不存在", "file": os.path.basename(fp)})
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                return self._json(500, {"error": "读取失败: %s" % e})
            return self._send(200, text)
        if path == "/api/watch":
            fn = (query.get("file") or [""])[0]
            return self._watch(fn)

        # ---- 会话桥 ----
        if path == "/api/session/status":
            return self._json(200, SESSION.status())
        if path == "/api/session/state":
            return self._json(200, SESSION.status().get("state") or {})
        if path == "/api/session/log":
            since = self._int_arg(query, "since", 0)
            return self._json(200, {"items": SESSION.since(since)})
        if path == "/api/session/inbox":
            # 供 agent 使用：取走页面发来的消息。wait>0 时转入长轮询，避免空转。
            SESSION.touch_agent()
            since = self._int_arg(query, "since", 0)
            wait = min(self._int_arg(query, "wait", 0), 120)
            items = SESSION.wait_since(since, wait) if wait > 0 else SESSION.since(since)
            SESSION.touch_agent()
            only = (query.get("from") or ["user"])[0]
            if only != "all":
                items = [m for m in items if m["from"] == only]
            return self._json(200, {"items": items, "seq": SESSION.seq,
                                    "pageConnected": SESSION.status()["pageConnected"]})
        if path == "/api/session/stream":
            return self._session_stream(self._int_arg(query, "since", 0))

        return self._static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/project":
            body = self._read_body()
            fp = safe_project_path(body.get("file"))
            if not fp:
                return self._json(400, {"error": "非法工程名"})
            data = body.get("data")
            if data is None:
                return self._json(400, {"error": "缺少 data"})
            try:
                text = json.dumps(data, ensure_ascii=False, indent=2)
            except (TypeError, ValueError) as e:
                return self._json(400, {"error": "序列化失败: %s" % e})
            try:
                atomic_write(fp, text)
            except OSError as e:
                return self._json(500, {"error": "写入失败: %s" % e})
            return self._json(200, {
                "ok": True,
                "file": os.path.basename(fp),
                "fingerprint": file_fingerprint(fp),
                "bytes": len(text.encode("utf-8")),
            })

        if path == "/api/delete":
            body = self._read_body()
            fp = safe_project_path(body.get("file"))
            if not fp or not os.path.exists(fp):
                return self._json(404, {"error": "工程不存在"})
            try:
                os.remove(fp)
            except OSError as e:
                return self._json(500, {"error": "删除失败: %s" % e})
            return self._json(200, {"ok": True})

        if path == "/api/config":
            body = self._read_body()
            cfg = read_config()
            for key in ("baseURL", "model", "apiKey"):
                if key in body and body[key] is not None:
                    cfg[key] = str(body[key]).strip()
            try:
                write_config(cfg)
            except OSError as e:
                return self._json(500, {"error": "配置写入失败: %s" % e})
            return self._json(200, {"ok": True, "hasKey": bool(cfg.get("apiKey"))})

        if path == "/api/ai":
            return self._ai_proxy()

        # ---- 会话桥 ----
        if path == "/api/session/send":
            body = self._read_body()
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "缺少 text"})
            item = SESSION.add("user", text=text, context=body.get("context"))
            return self._json(200, {"ok": True, "id": item["id"]})
        if path == "/api/session/say":
            body = self._read_body()
            SESSION.touch_agent()
            item = SESSION.add("agent", text=body.get("text") or "",
                               patch=body.get("patch"), commands=body.get("commands"))
            return self._json(200, {"ok": True, "id": item["id"]})
        if path == "/api/session/command":
            body = self._read_body()
            cmd = body.get("cmd")
            if not cmd:
                return self._json(400, {"error": "缺少 cmd"})
            SESSION.touch_agent()
            item = SESSION.add("agent", text=body.get("text") or "",
                               commands=[{"cmd": cmd, "args": body.get("args") or {}}])
            return self._json(200, {"ok": True, "id": item["id"]})
        if path == "/api/session/report":
            SESSION.report(self._read_body())
            return self._json(200, {"ok": True})
        if path == "/api/session/reset":
            SESSION.__init__()
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "未知接口"})

    do_HEAD = do_GET

    # ---------------- 文件变更监听 ----------------

    def _watch(self, file_name):
        fp = safe_project_path(file_name)
        if not fp:
            return self._json(400, {"error": "非法工程名"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last = file_fingerprint(fp)
        last_beat = time.time()
        try:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                time.sleep(0.4)
                cur = file_fingerprint(fp)
                if cur != last:
                    last = cur
                    payload = json.dumps({
                        "file": os.path.basename(fp),
                        "exists": cur is not None,
                        "at": int(time.time() * 1000),
                    }, ensure_ascii=False)
                    self.wfile.write(("event: changed\ndata: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                    last_beat = time.time()
                elif time.time() - last_beat > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_beat = time.time()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    # ---------------- 会话桥 ----------------

    def _int_arg(self, query, name, default=0):
        try:
            return int((query.get(name) or [default])[0])
        except (TypeError, ValueError):
            return default

    def _session_stream(self, since):
        """把会话里的新消息实时推给页面。页面只渲染 from=agent 的条目。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(("event: hello\ndata: %s\n\n" % json.dumps({"seq": SESSION.seq})).encode("utf-8"))
            self.wfile.flush()
            while True:
                items = SESSION.wait_since(since, 15)
                if items:
                    for it in items:
                        since = max(since, it["id"])
                        payload = json.dumps(it, ensure_ascii=False)
                        self.wfile.write(("event: item\ndata: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    # ---------------- AI 代理 ----------------

    def _ai_proxy(self):
        """转发到 OpenAI 兼容接口，流式透传响应体。"""
        import urllib.request
        import urllib.error

        body = self._read_body()
        cfg = read_config()

        base = (body.get("baseURL") or cfg.get("baseURL") or "").rstrip("/")
        key = body.get("apiKey") or cfg.get("apiKey") or ""
        if not base:
            return self._json(400, {"error": "尚未配置接口地址 (baseURL)"})
        if not key:
            return self._json(400, {"error": "尚未配置 API Key"})

        payload = body.get("payload") or {}
        url = base + "/chat/completions"
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("Accept", payload.get("stream") and "text/event-stream" or "application/json")

        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:2000]
            except Exception:
                pass
            return self._json(e.code, {"error": "上游返回 %s" % e.code, "detail": detail})
        except Exception as e:
            return self._json(502, {"error": "无法连接上游: %s" % e})

        ctype = resp.headers.get("Content-Type", "application/json")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if "text/event-stream" in ctype:
            self.send_header("Connection", "keep-alive")
        else:
            length = resp.headers.get("Content-Length")
            if length:
                self.send_header("Content-Length", length)
        self.end_headers()

        try:
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass

    # ---------------- 静态文件 ----------------

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = unquote(path).lstrip("/").replace("\\", "/")
        target = os.path.normpath(os.path.join(ROOT, rel))
        if not target.startswith(ROOT) or not os.path.isfile(target):
            return self._send(404, "<h1>404</h1><p>未找到 " + rel + "</p>",
                              ctype="text/html; charset=utf-8")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            with open(target, "rb") as f:
                data = f.read()
        except OSError as e:
            return self._send(500, "读取失败: %s" % e)
        return self._send(200, data, ctype=ctype)


def pick_port(preferred):
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main():
    os.makedirs(PROJECT_DIR, exist_ok=True)

    argv_port = None
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                argv_port = int(sys.argv[i + 1])
            except ValueError:
                pass
    port = pick_port(argv_port or DEFAULT_PORT)
    url = "http://127.0.0.1:%d/" % port

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    # 把实际端口写到 .port，agent.py 靠它自动找到本进程
    try:
        with open(os.path.join(ROOT, ".port"), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass

    print("=" * 58)
    print("  剧情框架 已启动")
    print("  地址 : %s" % url)
    print("  工程 : %s" % PROJECT_DIR)
    print("  会话 : %s?agent=1  （agent 界面模式）" % url)
    print("  关闭 : 在此窗口按 Ctrl+C，或直接关闭窗口")
    print("=" * 58)

    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        try:
            os.remove(os.path.join(ROOT, ".port"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
