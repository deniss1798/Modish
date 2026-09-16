"""Small redacted repository scan. Reports locations only, never matched text.

This targeted guard is not proof that arbitrary credentials are absent.
--history scans added lines in reachable Git history, without rewriting it.
"""
from pathlib import Path
import argparse
import re
import subprocess


RULES = (
    ("provider_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("credential_url", re.compile(r"[?&](?:access_code|code|access_token|api_key)=([^&\s\"'<>]+)", re.I)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


def findings(line: str) -> list[str]:
    found = []
    for name, rule in RULES:
        for match in rule.finditer(line):
            value = match.group(match.lastindex or 0).lower()
            if any(marker in value for marker in (
                "replace_me", "change_me", "your_", "example", "synthetic", "redacted", "{", "$",
            )):
                continue
            found.append(name)
            break
    return found


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], encoding="utf-8", errors="replace")


def scan_tree(root: Path):
    for filename in git("ls-files", "-z").split("\0"):
        path = root / filename
        if not filename or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for kind in findings(line):
                yield {"commit": "WORKTREE", "path": filename, "line": number, "type": kind}


def scan_history():
    commit, filename, number = "", "", 0
    for line in git("log", "--all", "--format=COMMIT:%H", "--no-ext-diff", "--no-renames", "-p", "--unified=0").splitlines():
        if line.startswith("COMMIT:"):
            commit = line[7:]
        elif line.startswith("+++ b/"):
            filename = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            number = int(match[1]) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            for kind in findings(line[1:]):
                yield {"commit": commit, "path": filename, "line": number, "type": kind}
            number += 1


def main() -> int:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").strip())
    results = list(scan_history() if args.history else scan_tree(root))
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    print(f"Findings: {len(results)}; scope: {'history' if args.history else 'tracked working tree'}")
    return 1 if results else 0


if __name__ == "__main__":
    raise SystemExit(main())
