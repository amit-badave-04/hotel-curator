"""Create and deploy hotel-curator's three Fly apps (ADR 011).

    python scripts/deploy.py plan                # print the commands only
    python scripts/deploy.py setup [--org ORG]   # apps, volume, private IP
    python scripts/deploy.py deploy [db|api|web]  # default: all, in order
    python scripts/deploy.py deploy web --edge cloudflare --domain DOMAIN
    python scripts/deploy.py status

The web app has two edge modes (ADR 011): ``fly`` (web/fly.toml) and,
after the Cloudflare cutover, ``cloudflare`` (web/fly.cloudflare.toml, with
nginx answering only DOMAIN and www.DOMAIN). A web deploy reads the mode its
machines run in and refuses to change it unless ``--edge`` says so, so a
routine redeploy cannot drop Cloudflare mode.

Every flyctl command is printed before it runs; ``--dry-run`` prints
without running. ``setup`` skips what already exists, so running it again
is safe. Deploys go in dependency order: the database, then the API (its
release command migrates and seeds the database), then the web app.
Push secrets first: scripts/push_fly_secrets.py. Needs flyctl logged in
(``flyctl auth login``) or ``FLY_API_TOKEN`` set.
"""

import argparse
import dataclasses
import json
import os
import re
import shlex
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
CLOUDFLARE_CONFIG = Path("web/fly.cloudflare.toml")
DOMAIN_PATTERN = re.compile(
    r"(?=.{4,100}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)
"""A lower-case DNS name short enough for nginx's server-name table
(server_names_hash_bucket_size 128 in web/nginx/http.conf)."""
EDGE_SETTING = "CURATOR_EDGE"

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
        print(f"$ {shlex.join(command)}", flush=True)
        return "" if self.dry_run else self._runner(command)

    def query(self, *args: str) -> list[dict[str, Any]]:
        """Run a read-only ``--json`` listing, even in a dry run."""
        try:
            output = self._runner([self.flyctl, *args, "--json"])
        except (
            OSError,
            UnicodeDecodeError,
            subprocess.CalledProcessError,
        ) as error:
            raise SystemExit(
                f"flyctl {' '.join(args)} failed: {error}"
            ) from None
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


def deploy(fly: Fly, app: FlyApp, env: dict[str, str] | None = None) -> None:
    """Build remotely and deploy one app on a single machine.

    Paths are absolute: flyctl changes into the build context before it
    reads ``--config``, so a relative config path would be looked up inside
    the context. ``env`` adds non-secret settings for this deploy.
    """
    args = [
        "deploy", str(ROOT / app.context), "--config", str(ROOT / app.config),
        "--app", app.name, "--remote-only", "--ha=false",
    ]  # fmt: skip
    if not app.public:
        args.append("--no-public-ips")
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    fly.run(*args)


def cloudflare_web(domain: str) -> tuple[FlyApp, dict[str, str]]:
    """The web app in Cloudflare mode, answering ``domain`` and its www.

    Raises:
        SystemExit: If ``domain`` is not a lower-case DNS name.
    """
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise SystemExit(f"--domain must be a lower-case DNS name: {domain!r}")
    web = dataclasses.replace(APP_BY_KEY["web"], config=CLOUDFLARE_CONFIG)
    return web, {"CURATOR_SERVER_NAMES": f"{domain} www.{domain}"}


def _machine_edge(machine: dict[str, Any]) -> str:
    config = field(machine, "config")
    env = field(config, "env") if isinstance(config, dict) else None
    edge = env.get(EDGE_SETTING) if isinstance(env, dict) else None
    if not isinstance(edge, str):
        raise SystemExit(
            f"unexpected answer from flyctl machine list: no {EDGE_SETTING} "
            "(machines from before the edge modes?): pass --edge fly or "
            "--edge cloudflare --domain <domain>"
        )
    return edge


def web_edge_now(fly: Fly) -> str | None:
    """The edge mode the web app's machines run in (None: no machines)."""
    machines = fly.query("machine", "list", "--app", APP_BY_KEY["web"].name)
    edges = {_machine_edge(machine) for machine in machines}
    if len(edges) > 1:
        raise SystemExit(f"web machines disagree on {EDGE_SETTING}: {edges}")
    return edges.pop() if edges else None


def web_target(
    fly: Fly, edge: str | None, domain: str | None
) -> tuple[FlyApp, dict[str, str]]:
    """The web app config to deploy, keeping the running edge mode.

    Raises:
        SystemExit: If the machines run in Cloudflare mode and ``edge`` does
            not say which mode to deploy.
    """
    if edge is None and web_edge_now(fly) == "cloudflare":
        raise SystemExit(
            "hotel-curator runs in Cloudflare mode: pass --edge cloudflare "
            "--domain <domain> to keep it, or --edge fly to switch back"
        )
    if edge == "cloudflare":
        return cloudflare_web(domain or "")
    return APP_BY_KEY["web"], {}


def deploy_apps(
    fly: Fly, keys: Sequence[str], edge: str | None, domain: str | None
) -> None:
    """Deploy the chosen apps in dependency order.

    Everything that can refuse (weights, the web app's edge mode) is
    decided before the first deploy, so a refusal never leaves a partial
    rollout.
    """
    chosen = [app for app in APPS if app.key in (keys or APP_BY_KEY)]
    if APP_BY_KEY["api"] in chosen:
        require_weights(ROOT / "artifacts" / "models")
    plans: list[tuple[FlyApp, dict[str, str]]] = [
        web_target(fly, edge, domain) if app.key == "web" else (app, {})
        for app in chosen
    ]
    for app, env in plans:
        deploy(fly, app, env)


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
    parser.add_argument("--edge", choices=("fly", "cloudflare"))
    parser.add_argument("--domain")
    args = parser.parse_args(argv)
    unknown = set(args.apps) - APP_BY_KEY.keys()
    if unknown or (args.apps and args.action != "deploy"):
        parser.error(f"app names go after deploy only: {sorted(args.apps)}")
    if (args.edge == "cloudflare") != (args.domain is not None):
        parser.error("--edge cloudflare and --domain go together")
    if args.edge and args.apps and "web" not in args.apps:
        parser.error("--edge applies to the web app: include web")
    if args.edge and args.action != "deploy":
        parser.error("--edge goes with deploy")
    if args.domain is not None and not DOMAIN_PATTERN.fullmatch(args.domain):
        parser.error(f"--domain must be a lower-case DNS name: {args.domain!r}")
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
        deploy_apps(fly, args.apps, args.edge, args.domain)
    else:
        return status(fly)
    return 0


if __name__ == "__main__":
    sys.exit(main())
