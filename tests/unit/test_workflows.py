"""The GitHub workflows' security properties, and the flyctl wrapper.

actionlint and zizmor (CI's security job) check syntax and common mistakes;
these tests pin the decisions specific to this project (ADR 011): what can
deploy, from where, with which secrets, and with which pinned tools.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTIONS = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
SHA_PIN = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
DIGEST_PIN = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")
WRAPPER = ROOT / "scripts" / "flyctl_docker.sh"
TOOLS = ROOT / "deploy" / "tools"


def _pinned_image(tool: str) -> str:
    """The image a deploy/tools/<tool>/Dockerfile pins."""
    lines = (TOOLS / tool / "Dockerfile").read_text().splitlines()
    (image,) = [
        line.removeprefix("FROM ") for line in lines if line.startswith("FROM ")
    ]
    return image


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    # YAML 1.1 reads the key "on" as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [
        s for job in data.get("jobs", {}).values() for s in job.get("steps", [])
    ]
    return steps + data.get("runs", {}).get("steps", [])


DEPLOY = _load(ROOT / ".github" / "workflows" / "deploy.yml")
CI = _load(ROOT / ".github" / "workflows" / "ci.yml")


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.name)
def test_every_action_is_local_or_pinned_to_a_commit(path: Path) -> None:
    data = _load(path)
    uses = [s["uses"] for s in _steps(data) if "uses" in s]
    uses += [j["uses"] for j in data.get("jobs", {}).values() if "uses" in j]
    for use in uses:
        assert use.startswith("./") or SHA_PIN.fullmatch(use), use


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_read_only_and_never_run_untrusted_triggers(
    path: Path,
) -> None:
    data = _load(path)
    assert data["permissions"] == {"contents": "read"}
    for job in data["jobs"].values():
        assert job.get("permissions", {"contents": "read"}) == {
            "contents": "read"
        }
    triggers = set(data["on"])
    assert not triggers & {"pull_request_target", "workflow_run"}


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.name)
def test_no_expression_is_pasted_into_a_shell_script(path: Path) -> None:
    # Values reach scripts through env, so none can inject shell code.
    for step in _steps(_load(path)):
        assert "${{" not in step.get("run", ""), step.get("name")


def test_deploy_runs_only_from_ci_for_main_in_the_production_environment() -> (
    None
):
    # No manual trigger: a deploy always follows the commit's own CI run.
    assert set(DEPLOY["on"]) == {"workflow_call"}
    (job,) = DEPLOY["jobs"].values()
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"]["name"] == "production"
    assert job["concurrency"] == {
        "group": "deploy-production",
        "cancel-in-progress": False,
    }


def test_ci_deploys_after_every_other_job_on_a_push_to_main() -> None:
    deploy = CI["jobs"]["deploy"]
    others = set(CI["jobs"]) - {"deploy"}
    assert set(deploy["needs"]) == others
    # Skipped until the owner has set up the environment and turned deploys
    # on: a run would otherwise create "production" without its reviewer.
    assert deploy["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        " && vars.CURATOR_DEPLOY == 'true'"
    )
    assert deploy["uses"] == "./.github/workflows/deploy.yml"
    assert "secrets" not in deploy  # the environment holds them
    # On main each commit has its own run, never cancelled by a newer push
    # (it may be deploying or waiting for approval); elsewhere runs cancel.
    assert CI["concurrency"] == {
        "group": "ci-${{ github.ref == 'refs/heads/main' && github.sha"
        " || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    }


def test_deploy_steps_run_in_order_only_for_mains_newest_commit() -> None:
    steps = next(iter(DEPLOY["jobs"].values()))["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    order = [
        names.index("Still the newest commit on main?"),
        names.index("flyctl (pinned image, version checked)"),
        names.index("Check both deploys can go ahead (dry run)"),
        names.index("Deploy the API"),
        names.index("Deploy the web app"),
        names.index("Smoke test"),
    ]
    assert order == sorted(order)
    guarded = steps[order[0] + 1 :]
    assert all(
        s["if"] == "steps.newest.outputs.deploy == 'true'" for s in guarded
    )
    guard = steps[order[0]]["run"]
    assert "git ls-remote origin refs/heads/main" in guard
    assert '"$head" = "$GITHUB_SHA"' in guard


def test_each_token_reaches_only_the_step_that_uses_it() -> None:
    steps = next(iter(DEPLOY["jobs"].values()))["steps"]
    tokens = {
        s.get("name"): v
        for s in steps
        for v in s.get("env", {}).values()
        if "secrets." in str(v)
    }
    web, api = (
        "${{ secrets.FLY_API_TOKEN_WEB }}",
        "${{ secrets.FLY_API_TOKEN_API }}",
    )
    assert tokens == {
        "Check both deploys can go ahead (dry run)": web,
        "Deploy the API": api,
        "Deploy the web app": web,
    }
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert text.count("secrets.") == 3
    check = next(s for s in steps if s.get("name", "").startswith("Check both"))
    assert check["run"].count("--dry-run") == 2


@pytest.mark.parametrize(
    ("tool", "repository"),
    [("flyctl", "flyio/flyctl:"), ("trivy", "ghcr.io/aquasecurity/trivy:")],
)
def test_tool_images_are_pinned_by_digest_where_dependabot_sees_them(
    tool: str, repository: str
) -> None:
    image = _pinned_image(tool)
    assert DIGEST_PIN.fullmatch(image) and image.startswith(repository)
    workflows = "".join(p.read_text() for p in WORKFLOWS)
    assert f"deploy/tools/{tool}/Dockerfile" in workflows
    assert "@sha256:" not in workflows  # no second copy of a pin
    scan = next(
        s
        for s in CI["jobs"]["containers"]["steps"]
        if "Trivy" in s.get("name", "")
    )
    assert "--exit-code 1" in scan["run"]


def test_the_trivy_step_runs_the_pinned_scanner_on_both_images(
    tmp_path: Path,
) -> None:
    # Run the step's own script with a docker that only records its calls.
    step = next(
        s
        for s in CI["jobs"]["containers"]["steps"]
        if "Trivy" in s.get("name", "")
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "CALL $*"\n')
    docker.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    out = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    calls = [line.split()[1:] for line in out.splitlines()]
    scanner = _pinned_image("trivy")
    assert len(calls) == 4
    for call in calls:
        assert call[call.index("trivy-cache:/root/.cache/") + 1] == scanner
    targets = [call[-1] for call in calls]
    assert targets == [
        "hotel-curator-api:local",
        "hotel-curator-api:local",
        "hotel-curator-web:local",
        "hotel-curator-web:local",
    ]
    assert ["--exit-code", "1"] == calls[1][-3:-1] == calls[3][-3:-1]


def test_dependabot_covers_every_ecosystem_and_the_tool_images() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert ecosystems == {
        "github-actions",
        "uv",
        "npm",
        "docker",
        "docker-compose",
    }
    docker = next(
        u for u in config["updates"] if u["package-ecosystem"] == "docker"
    )
    assert "/deploy/tools/*" in docker["directories"]
    assert all(u["cooldown"]["default-days"] >= 14 for u in config["updates"])


def _fake_docker(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
    docker.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def test_the_wrapper_runs_flyctl_with_the_token_by_name_only(
    tmp_path: Path,
) -> None:
    env = {**_fake_docker(tmp_path), "FLY_API_TOKEN": "fo1_secret-token-value"}
    out = subprocess.run(
        [str(WRAPPER), "deploy", str(ROOT / "web"), "--remote-only"],
        cwd=ROOT / "web",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert out[:4] == ["run", "--rm", "--env", "FLY_API_TOKEN"]
    assert f"{ROOT}:{ROOT}" in out
    assert out[out.index("--workdir") + 1] == str(ROOT / "web")
    assert out[-4:] == [
        _pinned_image("flyctl"),
        "deploy",
        str(ROOT / "web"),
        "--remote-only",
    ]
    assert "fo1_secret-token-value" not in "\n".join(out)


@pytest.mark.parametrize(
    ("image", "cwd", "message"),
    [
        ("flyio/flyctl:latest", ROOT, "not pinned by digest"),
        ("evil/flyctl:v1@sha256:" + "0" * 64, ROOT, "not pinned by digest"),
        ("", Path("/"), "inside the repository"),
    ],
)
def test_the_wrapper_refuses_unpinned_images_and_outside_paths(
    tmp_path: Path, image: str, cwd: Path, message: str
) -> None:
    env = {
        **_fake_docker(tmp_path),
        **({"FLYCTL_IMAGE": image} if image else {}),
    }
    result = subprocess.run(
        [str(WRAPPER), "version"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert message in result.stderr
