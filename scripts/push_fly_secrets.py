"""Create and push hotel-curator's Fly secrets from a git-ignored .env.fly.

    python scripts/push_fly_secrets.py init            # fresh passwords
    python scripts/push_fly_secrets.py check
    python scripts/push_fly_secrets.py push --dry-run  # names only
    python scripts/push_fly_secrets.py push [--app hotel-curator-db]
    python scripts/push_fly_secrets.py origin-tls --cert origin.pem \
        --key origin.key --ca origin-pull-ca.pem [--replace]  # Cloudflare

CREDENTIALS: this script handles the database passwords and, for
Cloudflare mode, the origin certificate's private key. Values reach
``flyctl secrets import`` on stdin, never on a command line (where ``ps``
and shell history would see them), and are never printed. ``.env.fly`` is
created with mode 0600 and is git-ignored (``.env.*``). Secrets are
staged: they apply on the app's next deploy (scripts/deploy.py).

Changing a password after the database volume exists also needs
``ALTER ROLE`` inside the database: docs/deploy/fly.md.
"""

import argparse
import base64
import binascii
import os
import re
import secrets
import ssl
import stat
import subprocess  # nosec B404 - runs flyctl only
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.fly"
DB_APP = "hotel-curator-db"
API_APP = "hotel-curator-api"
WEB_APP = "hotel-curator"
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
ORIGIN_SECRETS: dict[str, str] = {
    "CURATOR_ORIGIN_CERT_B64": "CERTIFICATE",
    "CURATOR_ORIGIN_KEY_B64": "PRIVATE KEY",
    "CURATOR_ORIGIN_PULL_CA_B64": "CERTIFICATE",
}
"""The web app's Cloudflare-mode secrets (base64 PEM) and their PEM type;
all three or none (web/nginx/15-curator-site.sh)."""
PEM_LABELS = {
    "CERTIFICATE": "CERTIFICATE",
    "PRIVATE KEY": "(?:RSA |EC )?PRIVATE KEY",
}
"""BEGIN labels nginx loads without a passphrase (not OpenSSH or
encrypted keys)."""

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


def pem_problem(name: str, encoded: str, label: str) -> str | None:
    """Why ``encoded`` is not a base64 PEM of type ``label``, if it is not.

    CRLF line endings (a file saved on Windows) are accepted, as openssl
    and nginx accept them.
    """
    try:
        pem = base64.b64decode(encoded, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError):
        return f"{name}: not base64 of a text PEM file"
    pem = pem.replace("\r\n", "\n")
    if "ENCRYPTED" in pem:
        return f"{name}: encrypted; nginx needs it without a passphrase"
    if not re.search(rf"^-----BEGIN {PEM_LABELS[label]}-----$", pem, re.M):
        return f"{name}: not a PEM {label.lower()}"
    return None


def _same_pem(first: str, second: str) -> bool:
    def text(encoded: str) -> str:
        decoded = base64.b64decode(encoded).decode("ascii", "replace")
        return "".join(decoded.split())

    return text(first) == text(second)


def _origin_problems(values: Secrets) -> list[str]:
    present = [key for key in ORIGIN_SECRETS if key in values]
    if present and len(present) != len(ORIGIN_SECRETS):
        missing = ", ".join(k for k in ORIGIN_SECRETS if k not in values)
        return [f"the three origin secrets go together; missing {missing}"]
    found = [pem_problem(k, values[k], ORIGIN_SECRETS[k]) for k in present]
    problems = [problem for problem in found if problem]
    cert, ca = "CURATOR_ORIGIN_CERT_B64", "CURATOR_ORIGIN_PULL_CA_B64"
    if not problems and present and _same_pem(values[cert], values[ca]):
        problems.append(f"{cert} and {ca} are the same file")
    return problems


def problems_in(values: Secrets) -> list[str]:
    """Everything that would make the deployed apps fail to connect."""
    required = {key for keys in SECRETS.values() for key in keys}
    known = required | ORIGIN_SECRETS.keys()
    problems = [
        f"{key}: not a known secret" for key in values if key not in known
    ]
    problems += [f"{key}: missing" for key in sorted(required - values.keys())]
    problems += _origin_problems(values)
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
        keys = SECRETS.get(app) or tuple(ORIGIN_SECRETS)
        command = [flyctl, "secrets", "import", "--app", app, "--stage"]
        print(f"$ {' '.join(command)}  < {', '.join(keys)} (on stdin)")
        if dry_run:
            continue
        # CREDENTIALS: values go to flyctl's stdin only.
        payload = "".join(f"{key}={values[key]}\n" for key in keys)
        runner(command, payload)


def tls_problem(cert: Path, key: Path, ca: Path) -> str | None:
    """What would stop nginx loading these files, as OpenSSL sees them."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(cert, key)
    except (ssl.SSLError, OSError):
        return "the key does not belong to the certificate (see --cert, --key)"
    try:
        context.load_verify_locations(cafile=ca)
    except (ssl.SSLError, OSError):
        return "the CA file is not a certificate OpenSSL can load"
    return None


def origin_tls_values(cert: Path, key: Path, ca: Path) -> Secrets:
    """Base64 of the three PEM files, checked like the pushed values and
    loaded the way nginx will load them.

    Raises:
        ValueError: Naming what is wrong, never file content.
    """
    values = {
        name: base64.b64encode(path.read_bytes()).decode("ascii")
        for name, path in zip(ORIGIN_SECRETS, (cert, key, ca), strict=True)
    }
    problems = _origin_problems(values)
    if not problems:
        problem = tls_problem(cert, key, ca)
        problems = [problem] if problem else []
    if problems:
        raise ValueError("; ".join(problems))
    return values


def _check_owned_file(path: Path) -> None:
    """Refuse a file this script would not rewrite safely in place."""
    info = path.lstat()
    link = f"{path.name} is a link: point --env-file at the file"
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(link)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path.name} is not a regular file")
    if info.st_nlink > 1:
        raise ValueError(link)
    if info.st_uid != os.getuid():
        raise ValueError(f"{path.name} belongs to another user")
    if info.st_mode & 0o077:
        raise ValueError(f"{path.name} is readable by others: chmod 600 it")


def append_env_lines(
    path: Path, values: Secrets, *, replace: bool = False
) -> None:
    """Add ``KEY=VALUE`` lines to an existing env file, keys not yet in it.

    A temporary 0600 file beside it is created first and doubles as a
    lock: a second run at the same time finds it and stops. The new
    content is parsed again before it replaces the old file; on any
    failure the old file stays as it was. A missing final newline is
    supplied.

    With ``replace``, lines for these keys are dropped first.

    Raises:
        ValueError: If a key is already there (without ``replace``), or the
            file is a link, not the user's own, or readable by anyone else.
        FileExistsError: If another run holds the temporary file.
    """
    _check_owned_file(path)
    temporary = path.with_name(f".{path.name}.new")
    # CREDENTIALS: the key goes only into a 0600 file, never to stdout.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        text = read_env_file(path)
        if replace:
            text = "\n".join(
                line
                for line in text.split("\n")
                if line.partition("=")[0].strip() not in values
            )
        clash = sorted(values.keys() & parse_env(text).keys())
        if clash:
            raise ValueError(
                f"already in {path.name}: {', '.join(clash)} "
                "(--replace swaps them)"
            )
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{k}={v}\n" for k, v in values.items())
        parse_env(text)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(text)
        temporary.replace(path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)
        raise


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "action", choices=("init", "check", "push", "origin-tls")
    )
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument(
        "--app", choices=(*SECRETS, WEB_APP), action="append", dest="apps"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--flyctl", default=os.environ.get("FLYCTL", "flyctl"))
    for name in ("cert", "key", "ca"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="origin-tls: replace origin secrets already in the file",
    )
    return parser.parse_args(argv)


def _init(env_file: Path) -> int:
    try:
        write_env_file(env_file, generate())
    except FileExistsError:
        print(f"{env_file} exists; not overwritten", file=sys.stderr)
        return 1
    print(f"wrote {env_file} (mode 0600, git-ignored)")
    return 0


def _origin_tls(args: argparse.Namespace) -> int:
    if None in (args.cert, args.key, args.ca):
        print("error: origin-tls needs --cert, --key and --ca", file=sys.stderr)
        return 1
    try:
        values = origin_tls_values(args.cert, args.key, args.ca)
        append_env_lines(args.env_file, values, replace=args.replace)
    except FileExistsError:
        print(
            "error: another origin-tls run is in progress (or remove the "
            f"stale .{args.env_file.name}.new)",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    done = "replaced" if args.replace else "added"
    print(f"{done} {', '.join(values)} in {args.env_file}")
    return 0


def _check_and_push(args: argparse.Namespace) -> int:
    try:
        values = parse_env(read_env_file(args.env_file))
    except ValueError as error:  # the message names a line, never a value
        print(f"error: {args.env_file}: {error}", file=sys.stderr)
        return 1
    problems = problems_in(values)
    with_web = ORIGIN_SECRETS.keys() <= values.keys()
    if WEB_APP in (args.apps or []) and not with_web:
        problems.append(f"{WEB_APP}: no origin secrets (run origin-tls)")
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.action == "push":
        apps = args.apps or [*SECRETS, *([WEB_APP] if with_web else [])]
        push(values, apps, flyctl=args.flyctl, dry_run=args.dry_run)
    print(f"{args.env_file}: {len(values)} secrets, consistent")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    args = _parse_args(argv)
    if args.action == "init":
        return _init(args.env_file)
    if args.action == "origin-tls":
        return _origin_tls(args)
    return _check_and_push(args)


if __name__ == "__main__":
    sys.exit(main())
