"""Exercise the reviewed V3 gateway and persistent frontend log mount.

This CI smoke uses the production nginx.conf, not a copied real-IP rule.
It requires Linux Docker and a prebuilt V3 frontend image.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
NGINX_IMAGE = "nginx:1.27-alpine"
NGINX_CONFIG = ROOT / "infra/nginx/nginx.conf"
PROXY_CONFIG = ROOT / "infra/nginx/proxy_params_api.conf"
LOG_DIR = "/var/log/stf-v3-frontend"


def docker(*args: str, check: bool = True) -> str:
    """Run Docker with separate arguments and return standard output."""
    result = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Docker {args[0]} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


class EchoHandler(BaseHTTPRequestHandler):
    """Return the address Nginx sent to an upstream BFF."""

    def do_GET(self) -> None:
        """Return only the proxy-provided client address."""
        body = json.dumps(
            {"x_real_ip": self.headers.get("X-Real-IP")}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the smoke output free of request URLs and client addresses."""
        del format, args


def request(
    url: str, headers: dict[str, str] | None = None
) -> tuple[int, bytes, Any]:
    """Send one request, including a non-2xx response, to a local service."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def wait_for(
    url: str, expected_status: int | None = None
) -> tuple[int, bytes, Any]:
    """Wait for the newly started container to accept local HTTP."""
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            response = request(url)
            if expected_status is None or response[0] == expected_status:
                return response
            time.sleep(0.25)
        except urllib.error.URLError:
            time.sleep(0.25)
    raise TimeoutError(f"service did not start at {url}")


def check_auth_throttle() -> None:
    """Verify a login burst is refused with 429, which the UI explains.

    Nginx answers ``limit_req`` rejections with 503 by default, and the
    login page shows 503 as "service unavailable".  The gateway must say
    429 so the page shows "too many attempts" instead.
    """
    url = "http://127.0.0.1:8080/api/auth/login"
    with ThreadPoolExecutor(max_workers=30) as pool:
        statuses = list(pool.map(lambda _: request(url)[0], range(30)))
    if 429 not in statuses:
        raise AssertionError("login burst was not throttled")
    unexpected = sorted(set(statuses) - {200, 429})
    if unexpected:
        raise AssertionError(f"login burst returned {unexpected}")


def check_real_ip(image: str) -> None:
    """Verify trusted and untrusted Cloudflare headers through real Nginx."""
    server = ThreadingHTTPServer(("127.0.0.1", 3002), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    name = "stf-pr3-nginx-" + uuid.uuid4().hex[:12]
    try:
        docker(
            "run", "--rm", "-d", "--name", name, "--network", "host",
            "-v", f"{NGINX_CONFIG}:/etc/nginx/nginx.conf:ro",
            "-v", f"{PROXY_CONFIG}:/etc/nginx/proxy_params_api.conf:ro",
            NGINX_IMAGE,
        )
        wait_for("http://127.0.0.1:8080/api/auth/login", 200)
        headers = {
            "CF-Connecting-IP": "198.51.100.10",
            "X-Real-IP": "203.0.113.99",
        }
        status, body, _ = request(
            "http://127.0.0.1:8080/api/auth/login?probe=private",
            headers,
        )
        if status != 200 or json.loads(body)["x_real_ip"] != "198.51.100.10":
            raise AssertionError("trusted tunnel address was not forwarded")

        status, body, _ = request(
            "http://127.0.0.1:8080/gateway-smoke",
            {"CF-Connecting-IP": "198.51.100.11"},
        )
        if status != 200 or json.loads(body)["x_real_ip"] != "198.51.100.11":
            raise AssertionError(
                "root proxy did not forward the client address"
            )

        status, body, _ = request(
            "http://127.0.0.1:8080/api/auth/login",
        )
        if status != 200 or json.loads(body)["x_real_ip"] != "127.0.0.1":
            raise AssertionError("missing tunnel address was not kept local")

        js = (
            "(async()=>{const r=await fetch("
            "'http://host.docker.internal:8080/api/auth/login',"
            "{headers:{'CF-Connecting-IP':'198.51.100.10',"
            "'X-Real-IP':'203.0.113.99'}});"
            "process.stdout.write(await r.text());"
            "if(!r.ok)process.exitCode=1})()"
            ".catch(e=>{console.error(e);process.exitCode=1})"
        )
        body = docker(
            "run", "--rm",
            "--add-host", "host.docker.internal:host-gateway",
            "--entrypoint", "node", image, "-e", js,
        )
        actual = json.loads(body)["x_real_ip"]
        address = ipaddress.ip_address(actual)
        if address.is_loopback or actual in {"198.51.100.10", "203.0.113.99"}:
            raise AssertionError("untrusted client chose the forwarded IP")

        check_auth_throttle()

        logs = docker("logs", name)
        if "method=GET" not in logs:
            raise AssertionError("Nginx emitted no access records")
        if "198.51.100.10" in logs or "probe=private" in logs:
            raise AssertionError("Nginx access log exposed a client IP or URL")
    finally:
        docker("rm", "-f", name, check=False)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def start_frontend(name: str, volume: str, image: str) -> None:
    """Start the production image with its access-log volume on loopback."""
    docker(
        "run", "--rm", "-d", "--name", name, "--network", "host",
        "-v", f"{volume}:{LOG_DIR}",
        "-e", "PORT=3002", "-e", "HOSTNAME=127.0.0.1",
        "-e", "STF_V3_API_BASE_URL=http://127.0.0.1:65534",
        "-e", "PORTAL_PUBLIC_ORIGIN=https://stf-diagnosis.dev",
        image,
    )
    wait_for("http://127.0.0.1:3002/api/health")


def access_records(name: str) -> list[dict[str, Any]]:
    """Read the app's JSONL access records as the image's non-root user."""
    raw = docker(
        "exec", name, "sh", "-c",
        f"cat {LOG_DIR}/access-*.jsonl",
    )
    return [json.loads(line) for line in raw.splitlines()]


def check_volume(image: str) -> None:
    """Prove UID 1001 writes survive removal and recreation of the app."""
    token = uuid.uuid4().hex[:12]
    name = "stf-pr3-front-" + token
    volume = "stf-pr3-access-" + token
    docker("volume", "create", volume)
    try:
        start_frontend(name, volume, image)
        uid = docker("exec", name, "id", "-u")
        if uid != "1001":
            raise AssertionError(f"frontend ran as unexpected UID {uid}")
        first = access_records(name)
        if not first:
            raise AssertionError("frontend wrote no mounted access record")
        first_ids = {row["request_id"] for row in first}
        docker("stop", name)
        start_frontend(name, volume, image)
        second = access_records(name)
        second_ids = {row["request_id"] for row in second}
        if not first_ids < second_ids:
            raise AssertionError("access records did not survive recreation")
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)


def main() -> None:
    """Run both deployment checks against the built frontend image."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-image", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("gateway smoke requires Linux Docker host networking")
    check_real_ip(args.frontend_image)
    check_volume(args.frontend_image)
    print(
        "PASS: trusted real IP, untrusted spoof, auth burst 429, "
        "private logs, "
        "UID 1001 volume"
    )


if __name__ == "__main__":
    main()
