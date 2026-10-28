"""Create and deploy hotel-curator's three Fly apps (ADR 011).

    python scripts/deploy.py plan                # print the commands only
    python scripts/deploy.py setup [--org ORG]   # apps, volume, private IP
    python scripts/deploy.py deploy [db|api|web]  # default: all, in order
    python scripts/deploy.py status

Every flyctl command is printed before it runs; ``--dry-run`` prints
without running. ``setup`` skips what already exists, so running it again
is safe. Deploys go in dependency order: the database, then the API (its
release command migrates and seeds the database), then the web app.
Push secrets first: scripts/push_fly_secrets.py. Needs flyctl logged in
(``flyctl auth login``) or ``FLY_API_TOKEN`` set.
"""

import argparse
import json
import os
import subprocess  # nosec B404 - runs flyctl only
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGION = "lhr"
DB_VOLUME = "pg_data"
DB_VOLUME_GB = 1
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
PRIVATE_IP_TYPE = "private_v6"
SKIPPED_MODELS = frozenset({"v1-roberta-base"})
"""Model folders the API image deletes (Dockerfile), so never checked."""


@dataclass(frozen=True)
class FlyApp:
    """One Fly app: where its config and build context live."""

    key: str
    name: str
    config: Path
    context: Path
    public: bool


APPS: tuple[FlyApp, ...] = (
    FlyApp("db", "hotel-curator-db", Path("deploy/postgres/fly.toml"),
           Path("deploy/postgres"), public=False),
    FlyApp("api", "hotel-curator-api", Path("fly.toml"), Path(),
           public=False),
    FlyApp("web", "hotel-curator", Path("web/fly.toml"), Path("web"),
           public=True),
)  # fmt: skip
"""In deploy order."""
APP_BY_KEY = {app.key: app for app in APPS}

Runner = Callable[[list[str]], str]


def run_command(command: list[str]) -> str:
    """Run a command from the repository root; return its stdout."""
    # The command is flyctl with arguments built here, never user text.
    done = subprocess.run(  # noqa: S603  # nosec B603
        command, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE
    )
    return done.stdout


class Fly:
    """flyctl, with every command printed first and an optional dry run."""

    def __init__(
        self, flyctl: str, *, dry_run: bool, runner: Runner = run_command
    ) -> None:
        self.flyctl = flyctl
        self.dry_run = dry_run
        self._runner = runner

    def run(self, *args: str) -> str:
        """Print, then run (unless a dry run) a flyctl command."""
        command = [self.flyctl, *args]
        print(f"$ {' '.join(command)}", flush=True)
        return "" if self.dry_run else self._runner(command)

    def query(self, *args: str) -> list[dict[str, Any]]:
        """Run a read-only ``--json`` listing, even in a dry run."""
        output = self._runner([self.flyctl, *args, "--json"])
        # Anything but a JSON list of objects stops here (flyctl prints []
        # for an empty listing): treating silence as "nothing" would make
        # status report "no public address" without having looked.
        unexpected = SystemExit(
            f"unexpected answer from flyctl {' '.join(args)}"
        )
        try:
            items = json.loads(output)
        except ValueError:
            raise unexpected from None
        if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items
        ):
            raise unexpected
        return items


def field(item: dict[str, Any], name: str) -> Any:
    """A field from flyctl JSON, whose key case differs between commands."""
    lowered = {key.lower(): value for key, value in item.items()}
    return lowered.get(name.lower())


def setup(fly: Fly, org: str) -> None:
    """Create the apps, the database volume and the API's private IP."""
    existing = {field(app, "name") for app in fly.query("apps", "list")}
    for app in APPS:
        if app.name in existing:
            print(f"# {app.name}: exists")
        else:
            fly.run("apps", "create", app.name, "--org", org)
    db, api = APP_BY_KEY["db"].name, APP_BY_KEY["api"].name
    volumes = (
        fly.query("volumes", "list", "--app", db) if db in existing else []
    )
    if any(field(v, "name") == DB_VOLUME for v in volumes):
        print(f"# {db}: volume {DB_VOLUME} exists")
    else:
        fly.run(
            "volumes", "create", DB_VOLUME, "--app", db, "--region", REGION,
            "--size", str(DB_VOLUME_GB), "--yes",
        )  # fmt: skip
    ips = fly.query("ips", "list", "--app", api) if api in existing else []
    if any(field(ip, "type") == PRIVATE_IP_TYPE for ip in ips):
        print(f"# {api}: private IPv6 exists")
    else:
        fly.run("ips", "allocate-v6", "--private", "--app", api)


def lfs_pointers(models_dir: Path) -> list[Path]:
    """Served model files that are Git LFS pointers, not weights."""
    found = [
        path
        for path in sorted(models_dir.glob("*/model.onnx"))
        if path.parent.name not in SKIPPED_MODELS
    ]
    if not found:
        return [models_dir / "<winner>" / "model.onnx"]
    return [
        p for p in found if p.read_bytes()[:64].startswith(LFS_POINTER_PREFIX)
    ]


def require_weights(models_dir: Path) -> None:
    """Stop before uploading a build that would fail on missing weights."""
    missing = lfs_pointers(models_dir)
    if missing:
        names = ", ".join(str(p) for p in missing)
        raise SystemExit(f"no model weights at {names}: run git lfs pull")


def deploy(fly: Fly, app: FlyApp) -> None:
    """Build remotely and deploy one app on a single machine.

    Paths are absolute: flyctl changes into the build context before it
    reads ``--config``, so a relative config path would be looked up inside
    the context.
    """
    args = [
        "deploy", str(ROOT / app.context), "--config", str(ROOT / app.config),
        "--app", app.name, "--remote-only", "--ha=false",
    ]  # fmt: skip
    if not app.public:
        args.append("--no-public-ips")
    fly.run(*args)


def public_ip_problems(fly: Fly) -> list[str]:
    """Any public address on the API or the database is a problem."""
    problems = []
    for app in APPS:
        if app.public:
            continue
        for ip in fly.query("ips", "list", "--app", app.name):
            if field(ip, "type") != PRIVATE_IP_TYPE:
                address = field(ip, "address")
                problems.append(f"{app.name} has a public address {address}")
    return problems


def status(fly: Fly) -> int:
    """Show each app's machines; fail if a private app is public."""
    for app in APPS:
        fly.run("status", "--app", app.name)
    problems = public_ip_problems(fly)
    for problem in problems:
        print(f"error: {problem} (flyctl ips release)", file=sys.stderr)
    return 1 if problems else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("plan", "setup", "deploy", "status"))
    parser.add_argument("apps", nargs="*", metavar="{db,api,web}")
    parser.add_argument("--org", default=os.environ.get("FLY_ORG", "personal"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--flyctl", default=os.environ.get("FLYCTL", "flyctl"))
    args = parser.parse_args(argv)
    unknown = set(args.apps) - APP_BY_KEY.keys()
    if unknown or (args.apps and args.action != "deploy"):
        parser.error(f"app names go after deploy only: {sorted(args.apps)}")
    return args


def plan(flyctl: str, org: str) -> None:
    """Print the full first-deploy sequence without running anything."""
    fly = Fly(flyctl, dry_run=True, runner=lambda _command: "[]")
    setup(fly, org)
    for app in APPS:
        deploy(fly, app)
    for app in APPS:
        fly.run("status", "--app", app.name)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    args = _parse_args(argv)
    if args.action == "plan":
        plan(args.flyctl, args.org)
        return 0
    fly = Fly(args.flyctl, dry_run=args.dry_run)
    if args.action == "setup":
        setup(fly, args.org)
    elif args.action == "deploy":
        chosen = [app for app in APPS if app.key in (args.apps or APP_BY_KEY)]
        if APP_BY_KEY["api"] in chosen:
            require_weights(ROOT / "artifacts" / "models")
        for app in chosen:
            deploy(fly, app)
    else:
        return status(fly)
    return 0


if __name__ == "__main__":
    sys.exit(main())
