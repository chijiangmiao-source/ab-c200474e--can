"""标准库 HTTP 服务：页面入口、健康检查与仿真 API。"""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import (
    DATA_FIELD_START,
    ERROR_DELIM_BITS,
    ERROR_FLAG_BITS,
    IFS_BITS,
    RECOVERY_SEQUENCES,
    SUSPEND_BITS,
    TEC_BUSOFF,
    TEC_PASSIVE,
    BusEngine,
)
from .frame import FIELD_LABELS, STANDARD_FRAME_FIELDS
from .validate import (
    MAX_NODES,
    MAX_REQUESTS,
    ValidationError,
    validate_scenario,
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "CanReview/1.0"

    def log_message(self, fmt, *args):  # 精简访问日志
        if os.environ.get("QUIET"):
            return
        super().log_message(fmt, *args)

    # ------------------------------------------------------------------ #
    def _send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel):
        path = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(os.path.abspath(STATIC_DIR) + os.sep) and \
                path != os.path.abspath(STATIC_DIR):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ext = os.path.splitext(path)[1]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json({"status": "ok", "service": "can-review"})
            return
        if path == "/api/config":
            self._send_json({
                "max_nodes": MAX_NODES,
                "max_requests": MAX_REQUESTS,
                "tec_passive": TEC_PASSIVE,
                "tec_busoff": TEC_BUSOFF,
                "recovery_sequences": RECOVERY_SEQUENCES,
                "idle_seq_bits": 11,
                "error_flag_bits": ERROR_FLAG_BITS,
                "error_delim_bits": ERROR_DELIM_BITS,
                "suspend_bits": SUSPEND_BITS,
                "ifs_bits": IFS_BITS,
                "data_field_start": DATA_FIELD_START,
                "fields": [
                    {"name": n, "width": w, "label": FIELD_LABELS[n]}
                    for n, w in STANDARD_FRAME_FIELDS
                ],
            })
            return
        if path == "/" or path == "/index.html":
            self._send_static("index.html")
            return
        if path.startswith("/static/"):
            self._send_static(path[len("/static/"):])
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/simulate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_json(
                {"errors": [{"field": "$", "message": f"JSON 解析失败：{e}"}]},
                HTTPStatus.BAD_REQUEST,
            )
            return
        # 校验先独立执行：校验失败即“清除旧结论”（不返回任何仿真结果）
        try:
            validate_scenario(payload)
        except ValidationError as e:
            self._send_json({"errors": e.errors}, HTTPStatus.BAD_REQUEST)
            return
        engine = BusEngine(payload)
        self._send_json({"result": engine.run()})


def build_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="星载 CAN 总线审查回放服务")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args(argv)
    httpd = build_server(args.host, args.port)
    print(f"CAN review service on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
