"""Create and inspect the Northflank service that hosts the app.

The image is built by Northflank from this repository, so the pins in
``requirements-deploy.txt`` and the cache fetched by ``deploy.fetch_cache``
decide what the running service serves. Everything here is a thin wrapper over
``https://api.northflank.com/v1``; the token comes from ``NORTHFLANK_TOKEN``.

Usage::

    python -m deploy.northflank

Prints the public URL once the service exists, then waits for the first build.
``NORTHFLANK_PROJECT`` overrides the default project name.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.northflank.com/v1"
REPO_URL = "https://github.com/ByteByBody/trend-detective"
BRANCH = "main"
PROJECT = "trend-detective"
SERVICE = "trend-detective"
APP_PORT = 7860
#: 0.5 vCPU and 1 GB: $12 a month, inside the free Sandbox credit, and the
#: service never sleeps.
DEPLOYMENT_PLAN = "nf-compute-50"


def api_url(path: str) -> str:
    """Absolute URL for a path under the versioned API root."""
    return f"{API_ROOT}/{path.lstrip('/')}"


def service_payload(
    name: str,
    repo_url: str = REPO_URL,
    branch: str = BRANCH,
    plan: str = DEPLOYMENT_PLAN,
    port: int = APP_PORT,
) -> dict:
    """The combined service that builds this repo and serves it publicly."""
    return {
        "name": name,
        "description": "Climate trend map that shows its own false alarms.",
        "billing": {"deploymentPlan": plan},
        "buildSource": "git",
        "vcsData": {
            "projectUrl": repo_url,
            "projectType": "github",
            "projectBranch": branch,
        },
        "buildSettings": {
            "dockerfile": {"dockerFilePath": "/Dockerfile", "dockerWorkDir": "/"}
        },
        "deployment": {"instances": 1},
        "ports": [
            {
                "name": "http",
                "internalPort": port,
                "public": True,
                "protocol": "HTTP",
            }
        ],
        "healthChecks": [
            {
                "protocol": "HTTP",
                "type": "readinessProbe",
                "path": "/health",
                "port": port,
                "initialDelaySeconds": 15,
                "periodSeconds": 30,
                "timeoutSeconds": 5,
                "failureThreshold": 10,
            }
        ],
    }


def public_url(service: dict) -> str:
    """The public address of a service, from its ports when the API reports one."""
    for port in service.get("ports", []):
        if not port.get("public"):
            continue
        for domain in port.get("domains", []):
            if domain.startswith("https://"):
                return domain
            if domain.startswith("http://"):
                return "https://" + domain[len("http://") :]
    return f"https://{service.get('name', SERVICE)}.northflank.app"


def call(path: str, token: str, body: dict | None = None, method: str | None = None):
    """One API call, returning the decoded ``data`` field."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        api_url(path),
        data=data,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"{request.full_url} failed with {error.code}: {detail}")
    return payload.get("data", payload)


def ensure_project(token: str, name: str = PROJECT) -> str:
    """The project id, creating the project when it is new."""
    projects = call("/projects", token)
    for project in projects:
        if project.get("name") == name:
            return project["id"]
    created = call("/projects", token, {"name": name})
    print(f"project created: {created['id']}")
    return created["id"]


def ensure_service(token: str, project: str, name: str = SERVICE) -> dict:
    """The service, creating it from this repository when it is new."""
    for service in call(f"/projects/{project}/services", token):
        if service.get("name") == name:
            return call(f"/projects/{project}/services/{service['id']}", token)
    return call(f"/projects/{project}/services/combined", token, service_payload(name))


def wait_for_build(token: str, project: str, service_id: str, minutes: int = 20) -> dict:
    """Poll the service until its first build settles."""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        service = call(f"/projects/{project}/services/{service_id}", token)
        status = service.get("status", {}).get("build", {}).get("status", "UNKNOWN")
        print(f"build: {status}", flush=True)
        if status in {"SUCCESS", "FAILURE", "ABORTED", "CRASHED"}:
            return service
        time.sleep(20)
    return call(f"/projects/{project}/services/{service_id}", token)


def main() -> None:
    token = os.environ.get("NORTHFLANK_TOKEN")
    if not token:
        raise SystemExit("Set NORTHFLANK_TOKEN to a Northflank API token.")
    project_name = os.environ.get("NORTHFLANK_PROJECT", PROJECT)

    project = ensure_project(token, project_name)
    service = ensure_service(token, project, SERVICE)
    service_id = service.get("id") or service.get("name")
    print(public_url(service))
    settled = wait_for_build(token, project, service_id)
    print(f"build: {settled.get('status', {}).get('build', {}).get('status')}")
    print(public_url(settled))


if __name__ == "__main__":
    main()
