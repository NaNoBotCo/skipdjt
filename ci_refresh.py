#!/usr/bin/env python3
"""Weekly refresh, run by GitHub Actions.

Lives here rather than inside the workflow YAML for two reasons: the logic is
version-controlled and testable like any other code, and the workflow file
itself stays short enough to paste by hand (adding workflow files needs a
GitHub permission the CLI doesn't have).

Sequence: rebuild from fresh fares -> refuse to publish if the result looks
wrong -> commit and push only if something actually changed.

The sanity gate is the important part. Without it, an API hiccup at 09:17 on
some Monday could quietly replace a working site with a broken one, and the
first sign would be a month of zero commissions.

Run locally to dry-run the checks without pushing:
    python3 ci_refresh.py --check-only
"""
import json
import re
import subprocess
import sys

MARKER = "749581"
MIN_ROUTES = 10
MIN_LINKS = 20


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def sanity_problems():
    """Everything that must be true before this is allowed to go public."""
    try:
        html = open("docs/index.html").read()
        data = json.load(open("docs/data.json"))
    except (OSError, ValueError) as e:
        return [f"could not read the build output: {e}"]

    cards = html.count('<article class="card"')
    routes = len(data.get("routes", []))
    attributed = html.count(f"marker={MARKER}")
    unattributed = [u for u in re.findall(r'aviasales\.com[^"]+', html)
                    if "marker=" not in u]

    print(f"  routes={routes} cards={cards} attributed={attributed} "
          f"unattributed={len(unattributed)}")

    problems = []
    if routes < MIN_ROUTES:
        problems.append(f"only {routes} routes returned (expected {MIN_ROUTES}+) "
                        f"— the fare API may be degraded")
    if cards != routes:
        problems.append(f"{cards} cards rendered for {routes} routes — mismatch")
    if attributed < MIN_LINKS:
        problems.append(f"only {attributed} attributed links (expected {MIN_LINKS}+)")
    if unattributed:
        problems.append(f"{len(unattributed)} booking links are MISSING the "
                        f"affiliate marker — the site would earn nothing")
    if "It is not always cheaper" not in html:
        problems.append("the method note is gone — honesty text was dropped")
    return problems


def main():
    check_only = "--check-only" in sys.argv

    if not check_only:
        print("Rebuilding from fresh fares...")
        r = run(sys.executable, "build.py")
        sys.stdout.write(r.stdout[-2000:])
        if r.returncode != 0:
            sys.stderr.write(r.stderr[-2000:])
            sys.exit("build.py failed — nothing published, live site untouched.")

    print("\nChecking the build before publishing...")
    problems = sanity_problems()
    if problems:
        print("\nREFUSING TO PUBLISH:")
        for p in problems:
            print(f"  - {p}")
        sys.exit("\nThe live site is unchanged, which is the safe outcome.")
    print("  looks sane.")

    if check_only:
        print("\n--check-only: stopping before commit.")
        return

    run("git", "config", "user.name", "skipdjt-bot")
    run("git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", "docs/")

    if run("git", "diff", "--staged", "--quiet").returncode == 0:
        print("\nFares unchanged — nothing to publish.")
        return

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run("git", "commit", "-m", f"Weekly fare refresh {stamp}")
    p = run("git", "push")
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        sys.exit("push failed")
    print("\nPublished refreshed fares.")


if __name__ == "__main__":
    main()
