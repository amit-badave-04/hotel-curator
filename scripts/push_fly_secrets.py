"""Create and push hotel-curator's Fly secrets from a git-ignored .env.fly.

    python scripts/push_fly_secrets.py init            # fresh passwords
    python scripts/push_fly_secrets.py check
    python scripts/push_fly_secrets.py push --dry-run  # names only
    python scripts/push_fly_secrets.py push [--app hotel-curator-db]

CREDENTIALS: this script handles the database passwords. Values reach
``flyctl secrets import`` on stdin, never on a command line (where ``ps``
and shell history would see them), and are never printed. ``.env.fly`` is
created with mode 0600 and is git-ignored (``.env.*``). Secrets are
staged: they apply on the app's next deploy (scripts/deploy.py).

Changing a password after the database volume exists also needs
``ALTER ROLE`` inside the database: docs/deploy/fly.md.
"""

import argparse
import os
import re
import secrets
import subprocess  # nosec B404 - runs flyctl only
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.fly"
DB_APP = "hotel-curator-db"
API_APP = "hotel-curator-api"
DB_HOST = f"{DB_APP}.internal"
DB_PORT = 5432
DB_NAME = "curator"
"""Must match POSTGRES_DB in deploy/postgres/fly.toml."""
OWNER_ROLE = "curator_owner"
APP_ROLE = "curator_app"
PASSWORD_BYTES = 32
PASSWORD_PATTERN = re.compile(r"[A-Za-z0-9_-]{24,}")
"""What ``secrets.token_urlsafe`` produces; safe inside a URL unescaped."""
ALTERED_BY_FLYCTL = frozenset("#'\"")
"""``flyctl secrets import`` cuts a value at ``#`` and strips quotes."""
TLS_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})
KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
PRINTABLE_ASCII = re.compile(r"[!-~]")

SECRETS: dict[str, tuple[str, ...]] = {
    DB_APP: (
        "POSTGRES_PASSWORD",
        "CURATOR_OWNER_PASSWORD",
        "CURATOR_APP_PASSWORD",
    ),
    API_APP: ("CURATOR_DATABASE_URL", "CURATOR_MIGRATION_DATABASE_URL"),
}
"""Secrets per app, in the order the apps are deployed."""

Secrets = dict[str, str]
Runner = Callable[[list[str], str], None]


def database_url(role: str, password: str) -> str:
    """The URL the API uses to reach the private database as ``role``."""
    return (
        f"postgresql+psycopg://{role}:{password}@{DB_HOST}:{DB_PORT}"
        f"/{DB_NAME}?sslmode=require"
    )


def generate() -> Secrets:
    """Fresh random passwords and the two URLs built from them.

    ``token_urlsafe`` output needs no percent-encoding inside a URL.
    """
    owner = secrets.token_urlsafe(PASSWORD_BYTES)
    app = secrets.token_urlsafe(PASSWORD_BYTES)
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(PASSWORD_BYTES),
        "CURATOR_OWNER_PASSWORD": owner,
        "CURATOR_APP_PASSWORD": app,
        "CURATOR_DATABASE_URL": database_url(APP_ROLE, app),
        "CURATOR_MIGRATION_DATABASE_URL": database_url(OWNER_ROLE, owner),
    }


def write_env_file(path: Path, values: Secrets) -> None:
    """Write ``KEY=VALUE`` lines to a new file readable only by its owner.

    Raises:
        FileExistsError: If ``path`` exists; rotating needs more than a new
            file (see the module docstring).
    """
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    # CREDENTIALS: O_EXCL refuses to overwrite; 0o600 keeps others out.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)


def parse_env(text: str) -> Secrets:
    """Parse strict ``KEY=VALUE`` lines; ``#`` comments and blanks skipped.

    Lines end at ``\n`` only (``str.splitlines`` would also split inside
    a value at characters such as ``\x0b``, turning part of a secret into
    a "key").

    Raises:
        ValueError: On a malformed line, a duplicate key, or a value with
            anything but printable ASCII, ``#`` or quotes (the line number
            is given, never the value).
    """
    values: Secrets = {}
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.removesuffix("\r").strip(" \t")
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not KEY_PATTERN.fullmatch(key):
            raise ValueError(f"line {number}: expected KEY=VALUE")
        if key in values:
            raise ValueError(f"line {number}: {key} given twice")
        if not value or not all(PRINTABLE_ASCII.match(ch) for ch in value):
            raise ValueError(
                f"line {number}: {key} is empty or has a space or a "
                "character outside printable ASCII"
            )
        if ALTERED_BY_FLYCTL & set(value):
            raise ValueError(
                f"line {number}: {key} has # or a quote, which flyctl "
                "would change"
            )
        values[key] = value
    return values


def read_env_file(path: Path) -> str:
    """The file's text, with no newline translation.

    ``read_text`` would turn a lone ``\r`` inside a value into a line
    break; decoding errors are reported by line number, because
    ``UnicodeDecodeError`` quotes the offending byte.

    Raises:
        ValueError: If the file is not UTF-8.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        line = data[: error.start].count(b"\n") + 1
        raise ValueError(f"line {line}: not UTF-8") from None


def _url_problems(name: str, url: str, role: str, password: str) -> list[str]:
    # Messages name the part that is wrong, never what it holds: a mistyped
    # URL can put part of a password in any field, and urlsplit's own
    # errors quote the text they choke on.
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return [f"{name}: not a valid URL"]
    sslmode = parse_qs(parts.query).get("sslmode", [""])[-1]
    expected = {
        "scheme": (parts.scheme, "postgresql+psycopg"),
        "host": (parts.hostname, DB_HOST),
        "port": (port, DB_PORT),
        "database": (parts.path.lstrip("/"), DB_NAME),
        "user": (parts.username, role),
    }
    problems = [
        f"{name}: {field} must be {want!r}"
        for field, (got, want) in expected.items()
        if got != want
    ]
    if sslmode not in TLS_SSLMODES:
        problems.append(
            f"{name}: sslmode must be one of {sorted(TLS_SSLMODES)}"
        )
    if unquote(parts.password or "") != password:
        problems.append(f"{name}: password differs from the {role} password")
    return problems


def problems_in(values: Secrets) -> list[str]:
    """Everything that would make the deployed apps fail to connect."""
    known = {key for keys in SECRETS.values() for key in keys}
    problems = [
        f"{key}: not a known secret" for key in values if key not in known
    ]
    problems += [f"{key}: missing" for key in sorted(known - values.keys())]
    if problems:
        return problems
    passwords = [values[key] for key in SECRETS[DB_APP]]
    if len(set(passwords)) != len(passwords):
        problems.append("the three database passwords must differ")
    problems += [
        f"{key}: needs 24 or more of A-Z, a-z, 0-9, _ and -"
        for key in SECRETS[DB_APP]
        if not PASSWORD_PATTERN.fullmatch(values[key])
    ]
    for key, role, password_key in (
        ("CURATOR_DATABASE_URL", APP_ROLE, "CURATOR_APP_PASSWORD"),
        (
            "CURATOR_MIGRATION_DATABASE_URL",
            OWNER_ROLE,
            "CURATOR_OWNER_PASSWORD",
        ),
    ):
        problems += _url_problems(key, values[key], role, values[password_key])
    return problems


def run_flyctl(command: list[str], stdin: str) -> None:
    """Run flyctl with ``stdin`` as its input; stop on failure."""
    # The command is flyctl with arguments built here, never user text.
    subprocess.run(  # noqa: S603  # nosec B603
        command, input=stdin, text=True, check=True
    )


def push(
    values: Secrets,
    apps: Sequence[str],
    *,
    flyctl: str,
    dry_run: bool,
    runner: Runner = run_flyctl,
) -> None:
    """Stage each app's secrets with ``flyctl secrets import``."""
    for app in apps:
        keys = SECRETS[app]
        command = [flyctl, "secrets", "import", "--app", app, "--stage"]
        print(f"$ {' '.join(command)}  < {', '.join(keys)} (on stdin)")
        if dry_run:
            continue
        # CREDENTIALS: values go to flyctl's stdin only.
        payload = "".join(f"{key}={values[key]}\n" for key in keys)
        runner(command, payload)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("init", "check", "push"))
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument(
        "--app", choices=tuple(SECRETS), action="append", dest="apps"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--flyctl", default=os.environ.get("FLYCTL", "flyctl"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    args = _parse_args(argv)
    if args.action == "init":
        try:
            write_env_file(args.env_file, generate())
        except FileExistsError:
            print(f"{args.env_file} exists; not overwritten", file=sys.stderr)
            return 1
        print(f"wrote {args.env_file} (mode 0600, git-ignored)")
        return 0
    try:
        values = parse_env(read_env_file(args.env_file))
    except ValueError as error:  # the message names a line, never a value
        print(f"error: {args.env_file}: {error}", file=sys.stderr)
        return 1
    problems = problems_in(values)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.action == "push":
        apps = args.apps or list(SECRETS)
        push(values, apps, flyctl=args.flyctl, dry_run=args.dry_run)
    print(f"{args.env_file}: {len(values)} secrets, consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
