"""Create and deploy hotel-curator's three Fly apps (ADR 011).

    python scripts/deploy.py plan                # print the commands only
    python scripts/deploy.py setup [--org ORG]   # apps, volume, private IP
    python scripts/deploy.py deploy [db|api|web]  # default: all, in order
    python scripts/deploy.py setup --edge cloudflare  # + dedicated IPv4
    python scripts/deploy.py deploy web --edge cloudflare --domain DOMAIN
    python scripts/deploy.py status

The web app has two edge modes (ADR 011): ``fly`` (web/fly.toml) and,
after the Cloudflare cutover, ``cloudflare`` (web/fly.cloudflare.toml, with
nginx answering only DOMAIN and www.DOMAIN). A web deploy reads the mode its
machines run in and refuses to change it unless ``--edge`` says so, so a
routine redeploy cannot drop Cloudflare mode. With no machines after an
earlier deploy (parked by scripts/ops.py) it needs ``--edge``.

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
DEDICATED_IPV4_TYPE = "v4"
"""flyctl's type for a dedicated IPv4; a shared one is "shared_v4"."""
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
PUBLIC_ORIGIN_ARG = "VITE_PUBLIC_ORIGIN"
"""Build argument for the web image: the address in link previews,
robots.txt and the sitemap (web/seo.ts)."""
SERVER_NAMES_SETTING = "CURATOR_SERVER_NAMES"
PUBLIC_HOST_SETTING = "CURATOR_PUBLIC_HOST"
"""Web machine setting that records the host a Fly-mode deploy published
(a domain after a rollback from Cloudflare mode), so a later deploy keeps
it or says why not. Set on every Fly-mode deploy, so the check does not
depend on whether flyctl keeps earlier --env values. nginx does not read
it."""

MODE_SETTINGS = {"fly": PUBLIC_HOST_SETTING, "cloudflare": SERVER_NAMES_SETTING}
"""The setting each edge mode's deploy passes with --env (fly_web,
cloudflare_web): a web machine without it lost its deploy's settings."""
Runner = Callable[[list[str]], str]
Plan = tuple["FlyApp", dict[str, str], dict[str, str]]
"""An app with the settings and build arguments of one deploy."""


def run_command(command: list[str], cwd: Path = ROOT) -> str:
    """Run a command in ``cwd`` (the repository root by default).

    A ``--json`` listing's output is captured and returned. Any other
    command writes straight to the terminal, so a status table or a
    deploy's build log is seen as it happens, and "" is returned.
    """
    listing = command[-1] == "--json"
    # The command is flyctl with arguments built here, never user text.
    done = subprocess.run(  # noqa: S603  # nosec B603
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if listing else None,
    )
    return done.stdout or ""


class Fly:
    """flyctl, with every command printed first and an optional dry run."""

    def __init__(
        self, flyctl: str, *, dry_run: bool, runner: Runner = run_command
    ) -> None:
        self.flyctl = flyctl
        self.dry_run = dry_run
        self._runner = runner

    def run(self, *args: str) -> str:
        """Print, then run (unless a dry run) a flyctl command; its output
        goes to the terminal."""
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


def setup_cloudflare(fly: Fly) -> None:
    """Give the web app a dedicated IPv4 for Cloudflare mode.

    Its TCP passthrough service (web/fly.cloudflare.toml) is not one Fly
    can route on a shared IPv4 (ADR 011, assumption A14). A dedicated IPv4
    is billed monthly.
    """
    web = APP_BY_KEY["web"].name
    ips = fly.query("ips", "list", "--app", web)
    if any(field(ip, "type") == DEDICATED_IPV4_TYPE for ip in ips):
        print(f"# {web}: dedicated IPv4 exists")
    else:
        fly.run("ips", "allocate-v4", "--app", web, "--yes")


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


def deploy(
    fly: Fly,
    app: FlyApp,
    env: dict[str, str] | None = None,
    build_args: dict[str, str] | None = None,
) -> None:
    """Build remotely and deploy one app on a single machine.

    Paths are absolute: flyctl changes into the build context before it
    reads ``--config``, so a relative config path would be looked up inside
    the context. ``env`` adds non-secret settings for this deploy and
    ``build_args`` non-secret build arguments.
    """
    args = [
        "deploy", str(ROOT / app.context), "--config", str(ROOT / app.config),
        "--app", app.name, "--remote-only", "--ha=false",
    ]  # fmt: skip
    if not app.public:
        args.append("--no-public-ips")
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    for key, value in (build_args or {}).items():
        args += ["--build-arg", f"{key}={value}"]
    fly.run(*args)


def fly_web(domain: str | None = None) -> Plan:
    """The web app in Fly mode, published at ``domain`` or its fly.dev name.

    A domain in Fly mode is the rollback from Cloudflare mode: Fly serves
    the domain with its own certificates.
    """
    web = APP_BY_KEY["web"]
    host = domain or fly_host()
    return (
        web,
        {PUBLIC_HOST_SETTING: host},
        {PUBLIC_ORIGIN_ARG: f"https://{host}"},
    )


def fly_host() -> str:
    """The web app's own name on Fly."""
    return f"{APP_BY_KEY['web'].name}.fly.dev"


def cloudflare_web(domain: str) -> Plan:
    """The web app in Cloudflare mode, answering ``domain`` and its www.

    Raises:
        SystemExit: If ``domain`` is not a lower-case DNS name.
    """
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise SystemExit(f"--domain must be a lower-case DNS name: {domain!r}")
    web = dataclasses.replace(APP_BY_KEY["web"], config=CLOUDFLARE_CONFIG)
    env = {SERVER_NAMES_SETTING: f"{domain} www.{domain}"}
    return web, env, {PUBLIC_ORIGIN_ARG: f"https://{domain}"}


def _machine_env(machine: dict[str, Any]) -> dict[str, Any] | None:
    config = field(machine, "config")
    env = field(config, "env") if isinstance(config, dict) else None
    return env if isinstance(env, dict) else None


def _machine_edge(machine: dict[str, Any]) -> str:
    env = _machine_env(machine)
    edge = env.get(EDGE_SETTING) if env is not None else None
    if not isinstance(edge, str):
        raise SystemExit(
            f"unexpected answer from flyctl machine list: no {EDGE_SETTING} "
            "(machines from before the edge modes?): pass --edge fly or "
            "--edge cloudflare --domain <domain>"
        )
    return edge


def _one(values: set[Any], setting: str) -> Any:
    if len(values) > 1:
        raise SystemExit(f"web machines disagree on {setting}: {values}")
    return values.pop() if values else None


def web_now(fly: Fly) -> tuple[str | None, str | None]:
    """The web machines' edge mode and recorded public host.

    Either is None before the first deploy; the host is None when none is
    recorded (the app is published at its fly.dev name).

    Raises:
        SystemExit: If the app has been deployed but has no machines
            (parked by scripts/ops.py): its mode is not recorded anywhere.
    """
    web = APP_BY_KEY["web"].name
    machines = fly.query("machine", "list", "--app", web)
    if not machines and fly.query("releases", "--app", web):
        raise SystemExit(
            f"{web} has no machines (parked?), so the mode it ran in is "
            "unknown: pass --edge fly, --edge fly --domain <domain> or "
            "--edge cloudflare --domain <domain> (scripts/ops.py stop "
            "printed the right one)"
        )
    edges = {_machine_edge(machine) for machine in machines}
    hosts = {
        (_machine_env(machine) or {}).get(PUBLIC_HOST_SETTING) or None
        for machine in machines
    }
    return _one(edges, EDGE_SETTING), _one(hosts, PUBLIC_HOST_SETTING)


def web_redeploy_flags(fly: Fly) -> list[str] | None:
    """The flags that redeploy the web app in the mode its machines run.

    ``None`` when it has no machines.

    Raises:
        SystemExit: If a machine lacks the setting its mode's deploy
            passes, or the machines disagree.
    """
    machines = fly.query("machine", "list", "--app", APP_BY_KEY["web"].name)
    found: set[tuple[str, ...]] = set()
    for machine in machines:
        edge = _machine_edge(machine)
        setting = MODE_SETTINGS.get(edge)
        value = (_machine_env(machine) or {}).get(setting or "")
        if setting is None or not isinstance(value, str) or not value:
            raise SystemExit(
                f"a web machine in mode {edge!r} has no {setting or 'mode'}: "
                "it lost the settings its deploy passed; redeploy it with "
                "scripts/deploy.py deploy web --edge <mode> [--domain <domain>]"
            )
        domain = value.split()[0]
        if edge == "fly" and domain == fly_host():
            found.add(("--edge", "fly"))
        else:
            found.add(("--edge", edge, "--domain", domain))
    flags = _one(found, "their deploy settings")
    return list(flags) if flags else None


def web_target(fly: Fly, edge: str | None, domain: str | None) -> Plan:
    """The web app config to deploy, keeping the running edge mode.

    Raises:
        SystemExit: If ``edge`` is not given and the machines run in
            Cloudflare mode, or in Fly mode at a recorded domain: a deploy
            without flags would quietly change where the site says it is.
    """
    if edge is None:
        edge_now, host_now = web_now(fly)
        if edge_now == "cloudflare":
            raise SystemExit(
                "hotel-curator runs in Cloudflare mode: pass --edge cloudflare "
                "--domain <domain> to keep it, or --edge fly to switch back"
            )
        if host_now and host_now != fly_host():
            raise SystemExit(
                f"hotel-curator is published at {host_now} in Fly mode: pass "
                f"--edge fly --domain {host_now} to keep it, or --edge fly to "
                "publish at its fly.dev name"
            )
    if edge == "cloudflare":
        return cloudflare_web(domain or "")
    return fly_web(domain)


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
    plans: list[Plan] = [
        web_target(fly, edge, domain) if app.key == "web" else (app, {}, {})
        for app in chosen
    ]
    for app, env, build_args in plans:
        deploy(fly, app, env, build_args)


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
    deploying = args.action == "deploy"
    if deploying and args.edge == "cloudflare" and args.domain is None:
        parser.error("--edge cloudflare needs --domain")
    if args.domain is not None and args.edge is None:
        parser.error("--domain goes with --edge fly or --edge cloudflare")
    if args.edge and args.apps and "web" not in args.apps:
        parser.error("--edge applies to the web app: include web")
    if args.edge and args.action not in {"deploy", "setup"}:
        parser.error("--edge goes with deploy or setup")
    if args.action == "setup" and args.domain is not None:
        parser.error("setup takes --edge cloudflare without --domain")
    if args.domain is not None and not DOMAIN_PATTERN.fullmatch(args.domain):
        parser.error(f"--domain must be a lower-case DNS name: {args.domain!r}")
    return args


def plan(flyctl: str, org: str) -> None:
    """Print the full first-deploy sequence without running anything."""
    fly = Fly(flyctl, dry_run=True, runner=lambda _command: "[]")
    setup(fly, org)
    for app in APPS:
        deploy(fly, *(fly_web() if app.key == "web" else (app, {}, {})))
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
        if args.edge == "cloudflare":
            setup_cloudflare(fly)
    elif args.action == "deploy":
        deploy_apps(fly, args.apps, args.edge, args.domain)
    else:
        return status(fly)
    return 0


if __name__ == "__main__":
    sys.exit(main())
