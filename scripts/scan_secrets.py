"""Redacted credential guard for tracked, staged and nonignored new files.

Only locations and finding types are reported; file contents are never printed.
Ignored local configuration, private credential files and archive directories are
not opened. This targeted guard cannot prove that arbitrary credentials are absent.
--history optionally scans added lines without changing Git history.
"""
from pathlib import Path, PurePosixPath
import argparse
import json
import re
import subprocess
from urllib.parse import unquote


RULES = (
    ("provider_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("credential_url", re.compile(
        r"(?:[?&]|&amp;)(?:access_code|code|access_token|api_key|token|client_secret|password)="
        r"([^&\s\"'<>]+)", re.I,
    )),
    ("credential_url", re.compile(
        r"\b(?:https?|postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|redis)://"
        r"[^\s/:@]+:((?:\$?\{[^}\r\n]+\})|[^\s/@]+)@", re.I,
    )),
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
)

SECRET_NAME = (
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*_)?"
    r"(?:api_key|access_token|auth_token|client_secret|jwt_secret(?:_key)?|secret_key|token|secret|password|passwd)"
    r"|admitad_export_code)"
)
NAMED_SECRET = re.compile(
    r"(?<![\w?&])(?P<name>" + SECRET_NAME + r")(?P<key_quote>[\"']?)\s*"
    r"(?P<separator>(?:(?::\s*(?:str(?:\s*\|\s*None)?|Optional\[str\]))?\s*=|:))\s*"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|\$\{[^}\r\n]*\}|[^\s,;#\]\)\}\"'\x60\\]+)", re.I,
)
PLACEHOLDER = re.compile(
    r"(?:REPLACE_ME(?:_[A-Z0-9_]+)?|CHANGE_ME(?:_[A-Z0-9_]+)?|YOUR_[A-Z0-9_]+"
    r"|EXAMPLE(?:_[A-Z0-9_]+)?|SYNTHETIC(?:_[A-Z0-9_]+)?|REDACTED"
    r"|(?:REPLACE|CHANGE|YOUR|EXAMPLE|SYNTHETIC|TEST|FAKE|FIXTURE)[-_][A-Z0-9_-]+"
    r"|\$\{[A-Z_][A-Z0-9_]*(?::[^}]*)?\}|\$[A-Z_][A-Z0-9_]*"
    r"|\{[A-Z_][A-Z0-9_]*\}|<[A-Z_][A-Z0-9_]*>|\[REDACTED\]|\*{3,})",
    re.I,
)
# Explicit diagnostic fixtures contain no real credentials. A substring such as
# "example" inside an otherwise credential-shaped value is never an exemption.
DIAGNOSTIC_PLACEHOLDERS = frozenset({"isolated-fixture", "secret-test-value", "private-id", "private-code"})
EXCLUDED_DIRECTORIES = frozenset({".tmp", "tmp", "archive", "archives", ".ssh"})
EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")


class ScanError(RuntimeError):
    pass


def is_placeholder(value: str) -> bool:
    value = unquote(value).strip()
    return (
        not value
        or value == "{}"
        or bool(re.fullmatch(r"<[^<>\r\n]+>", value))
        or bool(PLACEHOLDER.fullmatch(value))
        or bool(re.fullmatch(r"\$?\{[^}\r\n]+\}", value))
        or value in DIAGNOSTIC_PLACEHOLDERS
    )


def looks_like_credential(value: str) -> bool:
    """Recognize opaque literals without treating variable names as secrets."""
    if not re.fullmatch(r"[A-Za-z0-9_+/=.!@%-]{16,}", value):
        return False
    return (
        bool(re.fullmatch(r"[A-Za-z0-9]{32,}", value))
        or (any(c.isdigit() for c in value) and any(c.isalpha() for c in value))
        or (any(c.islower() for c in value) and any(c.isupper() for c in value))
    )


def findings(line: str, filename: str = "") -> list[str]:
    found = []
    for name, rule in RULES:
        for match in rule.finditer(line):
            value = match.group(match.lastindex or 0)
            if is_placeholder(value):
                continue
            # Short query codes commonly describe public product identifiers.
            if name == "credential_url" and len(unquote(value)) < 8:
                continue
            if name not in found:
                found.append(name)
            break
    for match in NAMED_SECRET.finditer(line):
        if filename.endswith(".sh") and (
            match["key_quote"]
            or (match.start() and line[match.start() - 1] not in " \t;|&(")
        ):
            # A shell variable assignment cannot quote its name or start inside
            # a grep/awk pattern. Such text references a configuration field.
            continue
        raw = match["value"]
        quoted = raw.startswith(("'", '"'))
        value = raw[1:-1] if quoted else raw
        # In a source expression such as findings("API_KEY=" + variable), the
        # quote after '=' closes the surrounding string; it opens no value.
        if quoted and not match["key_quote"] and match.start() and line[match.start() - 1] == raw[0]:
            continue
        if is_placeholder(value):
            continue
        # "token" also names parser/size tokens and "secret" can label prose.
        # Dedicated credential fields (password, API keys, *_token, *_secret,
        # Admitad codes) reject every literal; ambiguous bare names retain the
        # opaque-value check so ordinary noncredential data does not fail CI.
        if match["name"].lower() in {"token", "secret"} and not looks_like_credential(value):
            continue
        if not quoted and value.startswith(("{", "[", "$")):
            continue
        if quoted and value.startswith("$(") and value.endswith(")"):
            continue
        if not quoted and line.lstrip().startswith("#") and match["separator"].strip() == ":" and not looks_like_credential(value):
            continue
        # Unquoted Python expressions are references, not embedded credentials.
        # Environment-file literals and quoted source/JSON literals are checked.
        if not quoted and (re.search(r"[().\[]", value) or value.lower() in {"none", "null", "false", "true"}):
            continue
        if quoted and raw[0] == '"' and filename.endswith(".sh") and re.search(r"\$(?:[A-Za-z0-9_]|\{|\()", value):
            continue
        if not quoted:
            env_file = PurePosixPath(filename).name.lower().startswith(".env") or filename.lower().endswith(".env")
            reference = value.removesuffix("!").removesuffix("?").isidentifier()
            if (not env_file or line.lstrip().startswith("#")) and reference and not looks_like_credential(value):
                continue
        if "credential_assignment" not in found:
            found.append("credential_assignment")
    return found


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ScanError("Git metadata could not be read")
    return result.stdout


def path_policy(filename: str) -> str:
    parts = PurePosixPath(filename).parts
    if not parts or PurePosixPath(filename).is_absolute() or ".." in parts:
        return "skip"
    if any(part.lower() == ".ssh" for part in parts):
        return "private"
    if any(part.lower() in EXCLUDED_DIRECTORIES for part in parts):
        return "excluded"
    name = parts[-1].lower()
    if (
        name.endswith(".env")
        or (name.startswith(".env") and not name.endswith(EXAMPLE_SUFFIXES))
        or name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "key.properties"}
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
    ):
        return "private"
    return "scan"


def scan_data(data: bytes, filename: str, scope: str):
    if b"\0" in data:
        return
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        for kind in findings(line, filename):
            yield {"commit": scope, "path": filename, "line": number, "type": kind}


def scan_tree(root: Path):
    filenames = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8", errors="replace")
    for filename in sorted(set(filenames.split("\0")) - {""}):
        policy = path_policy(filename)
        if policy == "private":
            yield {"commit": "WORKTREE", "path": filename, "line": 0, "type": "private_credential_file"}
            continue
        if policy == "excluded":
            yield {"commit": "WORKTREE", "path": filename, "line": 0, "type": "unscanned_tracked_artifact"}
            continue
        if policy != "scan":
            continue
        path = root / filename
        if path.is_symlink() or not path.is_file():
            continue
        # Avoid following a parent-directory link to files outside the checkout.
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        yield from scan_data(path.read_bytes(), filename, "WORKTREE")


def scan_staged(root: Path):
    filenames = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8", errors="replace")
    for filename in sorted(set(filenames.split("\0")) - {""}):
        policy = path_policy(filename)
        if policy == "private":
            yield {"commit": "INDEX", "path": filename, "line": 0, "type": "private_credential_file"}
        elif policy == "excluded":
            yield {"commit": "INDEX", "path": filename, "line": 0, "type": "unscanned_tracked_artifact"}
        elif policy == "scan":
            yield from scan_data(git(root, "show", ":" + filename), filename, "INDEX")


def scan_history(root: Path):
    # Exclusions are passed to Git so private/local files are never loaded, even
    # in historical commits. The optional history scan does not rewrite commits.
    exclusions = [
        ":(exclude,glob)**/" + directory + "/**" for directory in EXCLUDED_DIRECTORIES
    ] + [
        ":(exclude,glob)**/*.env", ":(exclude,glob)**/.env*",
        ":(exclude,glob)**/id_rsa", ":(exclude,glob)**/id_dsa",
        ":(exclude,glob)**/id_ecdsa", ":(exclude,glob)**/id_ed25519",
        ":(exclude,glob)**/credentials", ":(exclude,glob)**/*.pem",
        ":(exclude,glob)**/*.key", ":(exclude,glob)**/key.properties",
        ":(exclude,glob)**/*.p12", ":(exclude,glob)**/*.pfx",
        ":(exclude,glob)**/*.jks", ":(exclude,glob)**/*.keystore",
    ]
    patch = git(root, "log", "--all", "--format=COMMIT:%H", "--no-ext-diff", "--no-renames", "-p", "--unified=0", "--", ".", *exclusions)
    commit, filename, number = "", "", 0
    for line in patch.decode("utf-8", errors="replace").splitlines():
        if line.startswith("COMMIT:"):
            commit = line[7:]
        elif line.startswith("+++ b/"):
            filename = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            number = int(match[1]) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if path_policy(filename) == "scan":
                for kind in findings(line[1:], filename):
                    yield {"commit": commit, "path": filename, "line": number, "type": kind}
            number += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode("utf-8").strip())
        results = list(scan_history(root) if args.history else (*scan_tree(root), *scan_staged(root)))
    except (OSError, ScanError):
        print("Credential guard could not complete; no file contents were reported.")
        return 2
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    scope = "history" if args.history else "tracked/staged/nonignored new files"
    print(f"Findings: {len(results)}; scope: {scope}")
    return 1 if results else 0


if __name__ == "__main__":
    raise SystemExit(main())
