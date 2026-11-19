import importlib.util
from pathlib import Path
import re
import pytest

spec = importlib.util.spec_from_file_location("build_pages", "scripts/build_pages.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_public_artifact(tmp_path):
    output = tmp_path / "pages"
    module.build(output, "https://api.example.com")
    assert 'https://api.example.com' in (output / "api-config.js").read_text()
    assert (output / ".nojekyll").exists()
    assert all(p.suffix in {".html", ".css", ".js", ".svg", ".png", ".webp"} or p.name == ".nojekyll" for p in output.rglob("*") if p.is_file())
    assert {"favicon.svg", "favicon-32.png", "apple-touch-icon.png"} <= {p.name for p in output.iterdir()}
    html = (output / "index.html").read_text(encoding="utf-8")
    for value in re.findall(r'(?:src|href)="([^"#]+)"', html):
        assert not value.startswith("/")
        if value.startswith("./") and value != "./":
            assert (output / value.split("?")[0]).exists()


LOCAL_REF = re.compile(r"""(?:(?:src|href)="|\bfrom\s+['"]|\bimport\s*\(\s*['"])((?:\./|\.\./)[\w./-]+\.(?:js|css|svg|png|webp))([^"']*)""")


def test_every_local_reference_is_stamped(tmp_path):
    output = tmp_path / "pages"
    version = module.build(output, "https://api.example.com")
    refs = [(path, name, query) for path in output.rglob("*") if path.suffix in {".html", ".js"}
            for name, query in LOCAL_REF.findall(path.read_text(encoding="utf-8"))]
    assert any(name == "./app.js" and source.name == "auth.js" for source, name, _ in refs)  # dynamic import
    assert any(name.startswith("../") for _, name, _ in refs)
    for source, name, query in refs:
        assert query == f"?v={version}", (source, name, query)
        assert (source.parent / name).is_file(), (source, name)


def test_version_follows_content(tmp_path):
    first = module.build(tmp_path / "a", "https://api.example.com")
    assert module.build(tmp_path / "b", "https://api.example.com") == first
    assert module.build(tmp_path / "c", "https://other.example.com") != first


@pytest.mark.parametrize("url", ["", "http://api.example.com", "https://api.example.com/path", "https://user@api.example.com"])
def test_invalid_api(tmp_path, url):
    with pytest.raises(ValueError):
        module.build(tmp_path / "pages", url)
