"""接管本机 Chrome 的极简 CDP 驱动（纯标准库，无需外部依赖）。

握手 + 文本帧收发，够用即可：导航、执行 JS、取 cookie。
"""
import base64, json, os, socket, struct, subprocess, time, urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\u00uk\AppData\Local\Google\Chrome\User Data"
PORT = 9222
BASE = f"http://127.0.0.1:{PORT}"


class WS:
    def __init__(self, url, timeout=30):
        rest = url[len("ws://"):]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.sock.recv(4096)
            if not c:
                raise RuntimeError("握手失败：连接被关闭")
            buf += c
        head, rest_bytes = buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("握手失败：" + head.decode("utf-8", "replace")[:200])
        self.buf = rest_bytes

    def _exact(self, n):
        while len(self.buf) < n:
            c = self.sock.recv(65536)
            if not c:
                raise RuntimeError("连接中断")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        n = len(data)
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(hdr) + mask + payload)

    def recv(self):
        while True:
            b0, b1 = self._exact(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            payload = self._exact(length)
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")
            if opcode == 0x8:
                raise RuntimeError("服务端关闭连接")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Session:
    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self._id = 0

    def cmd(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                return msg

    def val(self, expression):
        r = self.cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                          "awaitPromise": True})
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return "EXC: " + str(res["exceptionDetails"].get("text", ""))[:150]
        return res.get("result", {}).get("value")

    def cookies(self):
        return self.cmd("Network.getAllCookies").get("result", {}).get("cookies", [])

    def close(self):
        self.ws.close()


def http_get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.load(r)


def http_put(path):
    req = urllib.request.Request(BASE + path, method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def port_alive():
    try:
        http_get("/json/version")
        return True
    except Exception:
        return False


def ensure_chrome():
    if port_alive():
        return True
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--profile-directory=Default", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(1)
        if port_alive():
            return True
    return False


def new_tab(url):
    return http_put("/json/new?" + url)
