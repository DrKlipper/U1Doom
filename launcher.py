"""U1 DOOM: a small web page to set up and start DOOM on the Snapmaker U1.

Runs on this computer and talks to the printer over SSH (paramiko). The page
is only reachable from this computer (127.0.0.1): it drives an SSH session
with root rights on the printer.

    .venv/bin/python launcher.py      (or ./start.sh)
"""
import json
import posixpath
import shlex
import socket
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "launcher"
SETTINGS = PAGE / "settings.json"
REMOTE = "/userdata/doom"   # survives reboots, not mounted noexec
PORT = 8070
U1_PASSWORD = "snapmaker"   # the U1's root password while Root Access is on
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
         ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png"}


def local_files():
    files = {"doom": ROOT / "build" / "doom", "run.sh": ROOT / "printer" / "run.sh",
             "bt.py": ROOT / "printer" / "bt.py"}
    files.update({p.name: p for p in sorted((ROOT / "wad").glob("*.wad"))})
    return {name: path for name, path in files.items() if path.exists()}


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


class Printer:
    def __init__(self):
        self.client = None
        self.host = None
        self.error = None
        self.task = None        # what runs in the background, for the page
        self.lock = threading.Lock()
        # The U1's dropbear closes fresh channels at once ("Channel closed", at
        # times the whole session) when commands start or end at the same moment
        # on one connection, and it turns away quick new connections. So every
        # command waits for the one before.
        self.channel_lock = threading.Lock()

    def connected(self):
        transport = self.client and self.client.get_transport()
        return bool(transport and transport.is_active())

    def connect(self, host, password=""):
        with self.lock:
            if self.client:
                self.client.close()
            self.client, self.host, self.error = None, host, None
            client = paramiko.SSHClient()
            # The U1 makes a new host key at every boot, so there is nothing to pin.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                try:   # this computer's keys and agent first
                    client.connect(host, username="root", timeout=5, look_for_keys=True,
                                   allow_agent=True, password=None)
                except paramiko.SSHException:
                    # Key refused, or no key and agent at all: paramiko raises a plain
                    # SSHException ("No authentication methods available") for the latter.
                    client.connect(host, username="root", timeout=5, look_for_keys=False,
                                   allow_agent=False, password=password or U1_PASSWORD)
                self.client = client
            except paramiko.ssh_exception.NoValidConnectionsError as exc:
                refused = all(isinstance(e, ConnectionRefusedError) for e in exc.errors.values())
                self.error = "ssh_off" if refused else "unreachable"
            except paramiko.AuthenticationException:
                self.error = "auth"
            except (socket.timeout, OSError, paramiko.SSHException):
                self.error = "unreachable"

    def run(self, command, timeout=60):
        with self.channel_lock:
            _, out, err = self.client.exec_command(command, timeout=timeout)
            status = out.channel.recv_exit_status()
            return status, out.read().decode(errors="replace"), err.read().decode(errors="replace")

    def remote_files(self):
        with self.channel_lock:
            sftp = self.client.open_sftp()
            try:
                return {a.filename: a for a in sftp.listdir_attr(REMOTE)}
            except FileNotFoundError:
                return {}
            finally:
                sftp.close()

    def install(self):
        """Copies what is missing or changed to the printer; runs in a thread."""
        try:
            remote = self.remote_files()
            self.run(f"mkdir -p {REMOTE}")
            with self.channel_lock:
                sftp = self.client.open_sftp()
            for name, path in local_files().items():
                stat = path.stat()
                have = remote.get(name)
                if have and have.st_size == stat.st_size and int(have.st_mtime) == int(stat.st_mtime):
                    continue
                target = posixpath.join(REMOTE, name)
                self.task = {"name": "install", "file": name, "done": 0, "total": stat.st_size}
                with self.channel_lock:   # file by file, so the page can poll in between
                    sftp.put(str(path), target, callback=lambda done, total: self.task.update(done=done))
                    sftp.chmod(target, 0o755 if name in ("doom", "run.sh") else 0o644)
                    # Same time stamp on both sides: the page compares size and time.
                    sftp.utime(target, (stat.st_atime, stat.st_mtime))
            with self.channel_lock:
                sftp.close()
            self.task = None
        except Exception as exc:   # shown on the page
            self.task = {"name": "install", "error": str(exc)}

    def bluetooth(self, *args, timeout=60):
        _, out, err = self.run("python3 " + shlex.join([f"{REMOTE}/bt.py", *args]), timeout=timeout)
        try:
            return json.loads(out)
        except ValueError:
            return {"error": "bt_failed", "detail": (err or out)[-400:]}


printer = Printer()


def print_state(host):
    """Klipper's print state from Moonraker, without SSH."""
    try:
        url = f"http://{host}/printer/objects/query?print_stats"
        with urllib.request.urlopen(url, timeout=2) as answer:
            return json.load(answer)["result"]["status"]["print_stats"]["state"]
    except (OSError, ValueError, KeyError):
        return None


def state():
    result = {"host": printer.host or load_settings().get("host", ""), "connected": printer.connected(),
              "error": printer.error, "task": printer.task, "files": [], "running": []}
    remote = {}
    if result["connected"]:
        result["print_state"] = print_state(printer.host)
        _, out, _ = printer.run("for p in doom gui bluealsa; do pidof $p >/dev/null && echo $p; done")
        result["running"] = out.split()
        remote = printer.remote_files()
    for name, path in local_files().items():
        stat, have = path.stat(), remote.get(name)
        if not result["connected"]:
            where = "unknown"
        elif not have:
            where = "missing"
        elif have.st_size == stat.st_size and int(have.st_mtime) == int(stat.st_mtime):
            where = "current"
        else:
            where = "changed"
        result["files"].append({"name": name, "size": stat.st_size, "printer": where})
    result["wads"] = [name for name in remote if name.endswith(".wad")]
    return result


def start(wad):
    if wad not in printer.remote_files() or not wad.endswith(".wad"):
        return {"error": "wad_missing"}
    if print_state(printer.host) in ("printing", "paused"):
        return {"error": "printing"}
    # setsid: DOOM keeps running when this SSH command returns.
    printer.run(f"setsid {REMOTE}/run.sh {shlex.quote(wad)} > {REMOTE}/doom.log 2>&1 < /dev/null &")
    return {}


def stop():
    # run.sh brings the display app back; the second call is only for the
    # case that run.sh is gone, too.
    printer.run("killall doom; sleep 1; pidof gui >/dev/null || lmd_cli -S gui")
    return {}


def log():
    _, out, _ = printer.run(f"tail -n 60 {REMOTE}/doom.log 2>/dev/null")
    return {"log": out}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            pass   # the browser left before the answer (reload, tab closed)

    def trusted(self):
        # DNS rebinding: only our own names. CSRF: POSTs must be JSON, which a
        # foreign page cannot send here without a preflight we never answer.
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return False
        return self.command == "GET" or self.headers.get("Content-Type") == "application/json"

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (paramiko.SSHException, EOFError, socket.timeout):
            # The printer went away (reboot, Root Access off): show it as offline.
            # The page gets the offline state right away; state() keeps
            # "error": "lost" until the next connect.
            printer.client, printer.error = None, "lost"
            self.send(200, state() if self.path == "/api/state" else {"error": "lost"})

    def do_GET(self):
        if not self.trusted():
            return self.send(403, {"error": "forbidden"})
        if self.path == "/api/state":
            return self.send(200, state())
        if self.path == "/api/log":
            return self.send(200, log() if printer.connected() else {"log": ""})
        if self.path == "/api/bt":
            return self.send(200, printer.bluetooth("status") if printer.connected() else {"error": "offline"})
        name = "index.html" if self.path == "/" else self.path.lstrip("/")
        file = (PAGE / name).resolve()
        if PAGE not in file.parents or not file.is_file() or file.suffix not in TYPES:
            return self.send(404, {"error": "not_found"})
        self.send(200, file.read_bytes(), TYPES[file.suffix])

    def do_POST(self):
        if not self.trusted():
            return self.send(403, {"error": "forbidden"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/connect":
            host = body.get("host", "").strip()
            SETTINGS.write_text(json.dumps({"host": host}) + "\n")
            printer.connect(host, body.get("password", ""))
            return self.send(200, state())
        if not printer.connected():
            return self.send(409, {"error": "offline"})
        if self.path == "/api/install":
            if not printer.task:
                printer.task = {"name": "install", "file": "", "done": 0, "total": 1}
                threading.Thread(target=printer.install, daemon=True).start()
            return self.send(200, {})
        if self.path == "/api/start":
            return self.send(200, start(body.get("wad", "")))
        if self.path == "/api/stop":
            return self.send(200, stop())
        if self.path == "/api/bt":
            args = [body.get("command", "")] + ([body["address"]] if body.get("address") else [])
            if args[0] not in ("scan", "pair", "connect", "disconnect", "remove"):
                return self.send(400, {"error": "unknown_command"})
            return self.send(200, printer.bluetooth(*args, timeout=90))
        self.send(404, {"error": "not_found"})

    def log_message(self, *args):
        pass


def main():
    host = load_settings().get("host")
    if host:
        printer.connect(host)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"U1 DOOM: {url}  (Strg+C beendet)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
