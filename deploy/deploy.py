"""Run on the VPS: python3 releases/SHA/deploy.py ROOT IMAGE SHA [--rollback]."""
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error


def run(*args, output=None, **kwargs):
    if output is not None:
        with Path(output).open("xb") as stream:
            subprocess.run(args, stdout=stream, check=True, **kwargs)
        return ""
    return subprocess.check_output(args, text=True, **kwargs).strip()


def schema(folder):
    path = folder / "data" / "users.sqlite3"
    if not path.exists():
        return 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def probe(api, sha):
    def request(path, method="GET", headers=None):
        req = urllib.request.Request(api + path, method=method, headers=headers or {})
        try:
            return urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as error:
            return error
    with request("/api/version") as response:
        assert response.status == 200 and json.load(response) == {"sha": sha}
    with request("/api/health") as response:
        assert response.status == 200
    with request("/api/auth/me") as response:
        assert response.status == 401
    origin = "https://oscarzhang-735.github.io"
    with request("/api/auth/login", "OPTIONS", {"Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"}) as response:
        assert response.status == 200 and response.headers.get("Access-Control-Allow-Origin") == origin


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("image", nargs="?")
    parser.add_argument("sha", nargs="?")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--local-image", action="store_true",
                        help="First SSH deployment: use an already loaded sha256 image ID")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "deploy.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    state_file = root / "current.json"
    current = json.loads(state_file.read_text()) if state_file.exists() else None
    if args.rollback:
        target = json.loads((root / "previous.json").read_text())
    else:
        if args.local_image:
            assert re.fullmatch(r"sha256:[a-f0-9]{64}", args.image or "")
            assert run("docker", "image", "inspect", args.image, "--format", "{{.Id}}") == args.image
        else:
            assert re.fullmatch(r"ghcr.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", args.image or "")
        assert re.fullmatch(r"[a-f0-9]{40}", args.sha or "")
        target = dict(image=args.image, sha=args.sha, engine="postgresql",
                      directory=str(Path(__file__).resolve().parent))
        if args.local_image:
            # Compose interprets a bare sha256 ID as a registry image name.
            # Use a source-specific local tag and retain/verify its exact ID.
            target["image_id"] = args.image
            target["image"] = "tcg-toolkit:bootstrap-" + args.sha
            run("docker", "tag", args.image, target["image"])

    for release in (current, target):
        if release and release.get("image_id"):
            if run("docker", "image", "inspect", release["image"], "--format", "{{.Id}}") != release["image_id"]:
                raise RuntimeError("Bootstrap image tag does not match its recorded image ID")

    def compose(release, *commands, output=None):
        env = {**os.environ, "TCG_IMAGE": release["image"]}
        return run("docker", "compose", "--env-file", str(root / ".env"), "-f",
                   str(Path(release["directory"]) / "compose.yaml"), *commands, env=env, output=output)

    def pg_schema(release):
        # The image owns schema inspection, including legacy PostgreSQL releases.
        state = json.loads(compose(release, "run", "--no-deps", "-T", "migrate", "tcg-db", "status"))
        return int(state["schema"])

    config = json.loads(compose(target, "config", "--format", "json"))
    domain = config["services"]["web"]["environment"]["API_DOMAIN"]
    assert re.fullmatch(r"[a-zA-Z0-9.-]+", domain)
    api = "https://" + domain
    if not target.get("image_id"):
        compose(target, "pull", "web")
    # The database is part of this backend release, never a separate Actions job.
    if target.get("engine") == "postgresql":
        compose(target, "pull", "db")
        target["postgres_image"] = config["services"]["db"]["image"]
    backup = root / "backups" / (str(time.time_ns()) + "-" + target["sha"])
    backup.mkdir(parents=True, mode=0o700)
    before_engine = current.get("engine", "sqlite") if current else "postgresql"
    before = 0
    if current:
        compose(current, "stop", "web")
        try:
            if before_engine == "sqlite":
                compose(current, "cp", "web:/app/data", str(backup))
                before = schema(backup)
            else:
                before = pg_schema(current)
                compose(current, "exec", "-T", "db", "sh", "-ec",
                        'exec pg_dump -U "$POSTGRES_USER" -d "$APP_DB_NAME" -Fc --no-owner --no-acl',
                        output=backup / "database.dump")
            (backup / "release.json").write_text(json.dumps(current))
            if args.rollback and (target.get("engine", "sqlite") != before_engine or target.get("schema") != before):
                raise RuntimeError("Database engine/schema differs: use the documented manual restore procedure")
        except Exception:
            compose(current, "start", "web")
            raise
    web_started = False
    try:
        compose(target, "up", "-d", "--wait", "--wait-timeout", "120", "db")
        if before_engine == "sqlite" and pg_schema(target) != 0:
            raise RuntimeError("First PostgreSQL cutover requires an empty database; existing data was left untouched")
        # Explicitly run on every release; never reuse a previous completed migrate container.
        compose(target, "run", "--no-deps", "-T", "migrate", "tcg-db", "migrate")
        target["schema"] = pg_schema(target)
        web_started = True
        compose(target, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "web")
        for attempt in range(12):
            try:
                probe(api, target["sha"])
                break
            except Exception:
                if attempt == 11:
                    raise
                time.sleep(5)
    except Exception:
        if web_started:
            compose(target, "stop", "web")
        same_schema = False
        if current and before_engine == "postgresql":
            try:
                same_schema = pg_schema(target) == before
            except Exception:
                pass
        # Before a new web starts, the old SQLite store has not been touched.
        safe_legacy_restart = before_engine == "sqlite" and not web_started
        if current and (same_schema or safe_legacy_restart):
            compose(current, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "web")
            probe(api, current["sha"])
        else:
            print("Automatic rollback unavailable; web remains stopped. Backup:", backup, file=sys.stderr)
        raise
    if current and current["sha"] != target["sha"]:
        (root / "previous.json").write_text(json.dumps(current))
    temporary = root / "current.next.json"
    temporary.write_text(json.dumps(target))
    temporary.replace(state_file)
    print("Deployed", target["sha"], "backup:", backup)


if __name__ == "__main__":
    main()
