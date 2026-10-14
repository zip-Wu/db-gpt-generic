"""Opt-in Docker execution for analysis tools; never falls back to host execution."""

import asyncio
import json
import os
from pathlib import Path


def enabled():
    return os.getenv("DBGPT_SANDBOX_BACKEND") == "docker"


def _host_path(path):
    path = Path(path).resolve()
    root = Path("/app/pilot")
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("Sandbox files must be under /app/pilot") from None
    return str(Path(os.environ["DBGPT_SANDBOX_HOST_PILOT"]) / relative)


def _execute(command, cwd, env, timeout):
    import docker

    env = env or {}
    cwd = str(Path(cwd).resolve())
    if not Path(cwd).is_relative_to(Path("/app/pilot/tmp")):
        raise ValueError("Sandbox working directory must be under /app/pilot/tmp")
    volumes = {_host_path(cwd): {"bind": cwd, "mode": "rw"}}
    paths = [env.get("FILE_PATH"), env.get("FILES_JSON")]
    if env.get("FILES_JSON"):
        with open(env["FILES_JSON"], encoding="utf-8") as handle:
            paths.extend(json.load(handle).values())
    for path in paths:
        if path and not Path(path).resolve().is_relative_to(Path(cwd)):
            volumes[_host_path(path)] = {
                "bind": str(Path(path).resolve()),
                "mode": "ro",
            }
    allowed = {
        "FILE_PATH",
        "FILES_JSON",
        "PLOT_DIR",
        "OUTPUT_DIR",
        "ANALYZE_IDS",
        "ANALYZE_NAMES",
    }
    safe_env = {k: v for k, v in env.items() if k in allowed}
    safe_env.update({"HOME": "/tmp", "MPLCONFIGDIR": "/tmp/matplotlib"})
    client = docker.from_env()
    container = None
    try:
        # Require an existing offline image, never implicitly pull one.
        image = os.environ.get("DBGPT_SANDBOX_IMAGE", "dbgpt-sandbox-python:latest")
        client.images.get(image)
        container = client.containers.create(
            image,
            command=command,
            working_dir=cwd,
            environment=safe_env,
            volumes=volumes,
            network_disabled=True,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="1g",
            nano_cpus=2_000_000_000,
            pids_limit=128,
            tmpfs={"/tmp": "rw,nosuid,nodev,size=256m"},
            labels={"dbgpt.role": "analysis-sandbox"},
        )
        container.start()
        try:
            result = container.wait(timeout=timeout)
        except Exception:
            container.kill()
            return None, b"", b"Execution timed out or Docker wait failed"
        return (
            result["StatusCode"],
            container.logs(stdout=True, stderr=False)[-200000:],
            container.logs(stdout=False, stderr=True)[-200000:],
        )
    finally:
        if container is not None:
            container.remove(force=True)
        client.close()


async def run(command, cwd, env=None, timeout=60):
    return await asyncio.to_thread(_execute, command, cwd, env, timeout)
