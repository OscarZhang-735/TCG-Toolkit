"""Build a public-only Pages artifact into a new directory."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

# GitHub Pages serves every file with max-age=600, and a hard reload does not cover
# modules imported later (auth.js loads app.js after sign-in). Stamping every local
# reference with a content hash gives each release fresh URLs. All references must
# carry the same stamp, or the browser would load a module twice and split its state.
HTML_REF = re.compile(r'((?:src|href)=")((?:\./|\.\./)[\w./-]+\.(?:js|css|svg|png|webp))(")')
ASSETS = {".html", ".js", ".css", ".svg", ".png", ".webp"}
JS_REF = re.compile(r"""((?:\bfrom\s+|\bimport\s*\(\s*)(['"]))((?:\./|\.\./)[\w./-]+\.js)(\2)""")


def stamp(output):
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.suffix in ASSETS)
    digest = hashlib.sha256()  # icons are binary; hash bytes, rewrite only text files
    for path in files:
        digest.update(path.relative_to(output).as_posix().encode() + b"\0" + path.read_bytes())
    version = digest.hexdigest()[:12]
    for path in files:
        if path.suffix not in {".html", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            text = HTML_REF.sub(lambda m: f"{m[1]}{m[2]}?v={version}{m[3]}", text)
        elif path.suffix == ".js":
            text = JS_REF.sub(lambda m: f"{m[1]}{m[3]}?v={version}{m[4]}", text)
        path.write_text(text, encoding="utf-8", newline="\n")
    return version


def build(output, api):
    url = urlsplit(api)
    if url.scheme != "https" or not url.hostname or url.path not in ("", "/") or url.query or url.fragment or url.username:
        raise ValueError("API_BASE_URL must be an HTTPS origin")
    output.mkdir(parents=True, exist_ok=False)
    static = Path(__file__).resolve().parents[1] / "src/tcg_toolkit/static"
    for source in static.rglob("*"):
        if source.is_file() and source.suffix in ASSETS:
            target = output / source.relative_to(static)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (output / "api-config.js").write_text("export const API_BASE_URL = " + json.dumps(api.rstrip("/")) + ";\n")
    version = stamp(output)
    (output / ".nojekyll").touch()
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", required=True)
    args = parser.parse_args()
    print("version", build(args.output, args.api))
