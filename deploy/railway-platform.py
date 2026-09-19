"""Run the demo data-plane components in one Railway trial service.

Each app remains a separate OS process and keeps its original FastAPI module.
The supervisor exits if any child dies so Railway can restart the whole group.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/app/services")
children: list[tuple[str, subprocess.Popen]] = []


def start(name: str, directory: str, command: list[str], **extra_env: str) -> None:
    env = os.environ.copy()
    env.update(extra_env)
    if directory == "broker":
        # Control Plane uses BROKER_URL for HTTP, but Celery treats that legacy
        # variable as its own broker URL and would try an HTTP transport.
        env.pop("BROKER_URL", None)
    env["PYTHONPATH"] = str(ROOT / directory)
    process = subprocess.Popen(command, cwd=ROOT / directory, env=env, start_new_session=True)
    children.append((name, process))
    print(f"Started {name} (pid {process.pid})", flush=True)


def stop_all(_signum=None, _frame=None) -> None:
    for _, process in children:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    for _, process in children:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

try:
    for name, directory, port, extra in (
        ("control-plane", "control-plane", 8001, {}),
        ("gateway", "gateway", 8002, {}),
        ("mcp-gateway", "mcp-gateway", 8005, {}),
        ("agent-gateway", "agent-gateway", 8004, {}),
        ("utility-tools", "mcp-tools", 8101, {"TOOLSET": "utility"}),
        ("knowledge-tools", "mcp-tools", 8102, {"TOOLSET": "knowledge"}),
    ):
        start(name, directory, [
            sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
            "--port", str(port),
        ], **extra)

    start("celery-worker", "broker", [
        sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker",
        "-B", "--loglevel=info", "--concurrency=1", "--pool=solo",
    ])

    while True:
        for name, process in children:
            code = process.poll()
            if code is not None:
                print(f"{name} exited with status {code}; stopping platform", flush=True)
                stop_all()
                sys.exit(code or 1)
        time.sleep(2)
except BaseException:
    stop_all()
    raise
