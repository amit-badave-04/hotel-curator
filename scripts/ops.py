"""Park and resume hotel-curator's three Fly apps; show their state.

    python scripts/ops.py status            # each app's machines
    python scripts/ops.py stop [--dry-run]  # web, then API, then database
    python scripts/ops.py start [--dry-run] # database, then API, then web

Parking (ADR 011, docs/RUNBOOK.md) leaves the database volume, the images
and any dedicated IPv4 (Cloudflare mode) to pay for. ``stop`` scales the
web app and the API to zero machines, so no visitor's request can start
them again (both start on demand), and stops the database machine, which
keeps its volume. It first prints the flags
that redeploy the web app in its current mode, since nothing records the
mode once the machines are gone (scripts/deploy.py then refuses a web
deploy without them). ``start`` starts the database machine, then scales
the API and the web app back to one machine each from their latest
release (assumption A17), and checks that the web machine kept the
settings its deploy passed. Run the smoke test afterwards (``make smoke``).

Every flyctl command is printed before it runs; ``--dry-run`` prints them
without running any (listings still run: they change nothing). flyctl runs
from a directory without a fly.toml, so it uses each app's deployed config,
never a local file (the web app may be in Cloudflare mode). Needs flyctl
logged in or ``FLY_API_TOKEN`` set; ``FLYCTL`` names another flyctl.
"""

import argparse
import functools
import os
import re
import shlex
import sys
from collections.abc import Sequence

from deploy import (
    APP_BY_KEY,
    REGION,
    ROOT,
    Fly,
    field,
    run_command,
    status,
    web_redeploy_flags,
)

FLYCTL_DIR = ROOT / "deploy"
"""Where flyctl runs: inside the repository (scripts/flyctl_docker.sh
mounts it), with no fly.toml."""
SCALED = ("web", "api")
"""Apps scaled to zero by ``stop``, front door first."""
MACHINE_ID = re.compile(r"[0-9a-f]{8,20}")
RUNNING = "started"
STOPPED = "stopped"


def db_machines(fly: Fly, state: str) -> list[str]:
    """Ids of the database machines in ``state``.

    Raises:
        SystemExit: If the database app has no machines, or flyctl answers
            with an id this script would not pass on.
    """
    db = APP_BY_KEY["db"].name
    machines = fly.query("machine", "list", "--app", db)
    if not machines and not fly.dry_run:
        raise SystemExit(f"{db} has no machines: deploy it first")
    ids = [str(field(machine, "id")) for machine in machines]
    if not all(MACHINE_ID.fullmatch(machine) for machine in ids):
        raise SystemExit(f"unexpected machine id from flyctl for {db}")
    return [
        machine
        for machine, item in zip(ids, machines, strict=True)
        if field(item, "state") == state
    ]


def stop(fly: Fly) -> None:
    """Scale the web app and the API to zero; stop the database machine.

    The database listing is checked before anything changes, so a refusal
    never leaves the site half parked.
    """
    db = APP_BY_KEY["db"].name
    running = db_machines(fly, RUNNING)
    flags = web_redeploy_flags(fly)
    if flags:
        # Once the machines are gone, nothing on Fly records the mode.
        command = ["scripts/deploy.py", "deploy", "web", *flags]
        print(f"# while parked, deploy the web app with: {shlex.join(command)}")
    for key in SCALED:
        name = APP_BY_KEY[key].name
        fly.run("scale", "count", "0", "--app", name, "--yes")
    for machine in running:
        fly.run("machine", "stop", machine, "--app", db)


def start(fly: Fly) -> None:
    """Start the database, then one API and one web machine.

    Raises:
        SystemExit: If the restored web machine lost the settings its
            deploy passed (assumption A17): redeploy it as the message says.
    """
    db = APP_BY_KEY["db"].name
    for machine in db_machines(fly, STOPPED):
        fly.run("machine", "start", machine, "--app", db)
    for key in reversed(SCALED):
        name = APP_BY_KEY[key].name
        fly.run(
            "scale", "count", "1", "--app", name, "--region", REGION, "--yes"
        )
    if fly.dry_run:
        return
    flags = web_redeploy_flags(fly)
    print(f"# {APP_BY_KEY['web'].name} restored: {shlex.join(flags or ['-'])}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("status", "stop", "start"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--flyctl", default=os.environ.get("FLYCTL", "flyctl"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    args = _parse_args(argv)
    runner = functools.partial(run_command, cwd=FLYCTL_DIR)
    # A path such as scripts/flyctl_docker.sh means the repository's, as in
    # deploy.py (which runs from the root), not one under FLYCTL_DIR; a bare
    # name is looked up on PATH.
    flyctl = args.flyctl
    if os.sep in flyctl:
        flyctl = str((ROOT / flyctl).resolve())
    fly = Fly(flyctl, dry_run=args.dry_run, runner=runner)
    if args.action == "status":
        return status(fly)
    if args.action == "stop":
        stop(fly)
    else:
        start(fly)
    return 0


if __name__ == "__main__":
    sys.exit(main())
