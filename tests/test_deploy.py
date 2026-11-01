"""Deployment control flow: shared backend/database release, backup and rollback."""
import importlib.util
import json
import sys
import types
from pathlib import Path
import pytest


@pytest.fixture
def deploy_module(monkeypatch):
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=2))
    spec = importlib.util.spec_from_file_location("deploy", "deploy/deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


@pytest.mark.parametrize("failure,new_schema,recover", [
    (None,1,False), ("health",1,True), ("health",2,False),
    ("migration",1,True), ("backup",1,False)])
def test_postgres_release_backup_and_recovery(tmp_path, monkeypatch, deploy_module, failure, new_schema, recover):
    module = deploy_module
    old = {"image":"ghcr.io/test/app@sha256:"+"1"*64, "sha":"1"*40,
           "directory":str(tmp_path / "old"), "engine":"postgresql", "schema":1}
    (tmp_path / "current.json").write_text(json.dumps(old))
    calls = []
    migrated = [False]

    def fake_run(*args, output=None, **kwargs):
        calls.append(args)
        if "config" in args:
            return json.dumps({"services":{"web":{"environment":{"API_DOMAIN":"api.example.com"}},
                                            "db":{"image":"postgres:17@sha256:"+"a"*64}}})
        if output:
            if failure == "backup": raise RuntimeError("backup failed")
            Path(output).write_bytes(b"PGDMP test dump")
        if "run" in args and args[-1] == "status":
            return json.dumps({"engine":"postgresql", "schema":new_schema if migrated[0] else 1})
        if "run" in args and args[-1] == "migrate":
            if failure == "migration": raise RuntimeError("migration failed")
            migrated[0] = True
        return ""

    def probe(api, sha):
        if failure == "health" and sha != old["sha"]: raise RuntimeError("health failed")

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module, "probe", probe)
    monkeypatch.setattr(sys, "argv", ["deploy.py",str(tmp_path),"ghcr.io/test/app@sha256:"+"2"*64,"2"*40])
    if failure:
        with pytest.raises(RuntimeError): module.main()
        assert json.loads((tmp_path / "current.json").read_text()) == old
    else:
        module.main()
        current = json.loads((tmp_path / "current.json").read_text())
        assert current["engine"] == "postgresql" and current["schema"] == 1
        assert "postgres_image" in current
        assert json.loads((tmp_path / "previous.json").read_text()) == old
    assert any("pg_dump" in call[-1] for call in calls)
    assert any("up" in call and str(tmp_path / "old" / "compose.yaml") in call for call in calls) == recover
    assert all("down" not in call and "rm" not in call for call in calls)
    if failure == "backup":
        assert any("start" in call for call in calls)
        assert not any("run" in call and call[-1] == "migrate" for call in calls)
    if not failure:
        migration_index = next(i for i,c in enumerate(calls) if "run" in c and c[-1] == "migrate")
        start_index = next(i for i,c in enumerate(calls) if "up" in c and c[-1] == "web")
        assert migration_index < start_index


def test_cross_engine_rollback_is_rejected_before_changes(tmp_path, monkeypatch, deploy_module):
    current = {"image":"ghcr.io/test/app@sha256:"+"2"*64, "sha":"2"*40,
               "directory":str(tmp_path / "new"), "engine":"postgresql", "schema":1}
    old = {"image":"ghcr.io/test/app@sha256:"+"1"*64, "sha":"1"*40,
           "directory":str(tmp_path / "old"), "schema":8}
    (tmp_path / "current.json").write_text(json.dumps(current))
    (tmp_path / "previous.json").write_text(json.dumps(old))
    calls = []
    def fake_run(*args, output=None, **kwargs):
        calls.append(args)
        if "config" in args: return json.dumps({"services":{"web":{"environment":{"API_DOMAIN":"api.example.com"}}}})
        if "run" in args and args[-1] == "status":
            return json.dumps({"engine":"postgresql", "schema":1})
        if output: Path(output).write_bytes(b"PGDMP backup")
        return ""
    monkeypatch.setattr(deploy_module, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["deploy.py",str(tmp_path),"--rollback"])
    with pytest.raises(RuntimeError, match="engine/schema"):
        deploy_module.main()
    assert any("start" in c for c in calls)
    assert not any("up" in c or ("run" in c and c[-1] == "migrate") for c in calls)


@pytest.mark.parametrize('failure,recover', [(None,False), ('migration',True), ('health',False)])
def test_first_ssh_cutover_backs_up_sqlite_and_keeps_cross_engine_boundary(
        tmp_path, monkeypatch, deploy_module, failure, recover):
    old = {"image":"ghcr.io/test/app@sha256:"+"1"*64, "sha":"1"*40,
           "directory":str(tmp_path / "old"), "schema":8}
    (tmp_path / 'current.json').write_text(json.dumps(old))
    image = 'sha256:' + '2'*64
    calls, migrated = [], [False]

    def fake_run(*args, **kwargs):
        calls.append(args)
        if 'inspect' in args: return image
        if 'config' in args:
            return json.dumps({'services':{'web':{'environment':{'API_DOMAIN':'api.example.com'}},
                                           'db':{'image':'postgres:17'}}})
        if 'run' in args and args[-1] == 'status':
            return json.dumps({'engine':'postgresql', 'schema':1 if migrated[0] else 0})
        if 'run' in args and args[-1] == 'migrate':
            if failure == 'migration': raise RuntimeError('migration failed')
            migrated[0] = True
        return ''

    def probe(api, sha):
        if failure == 'health' and sha != old['sha']: raise RuntimeError('health failed')

    monkeypatch.setattr(deploy_module, 'run', fake_run)
    monkeypatch.setattr(deploy_module, 'schema', lambda _: 8)
    monkeypatch.setattr(deploy_module, 'probe', probe)
    monkeypatch.setattr(sys, 'argv', ['deploy.py',str(tmp_path),image,'2'*40,'--local-image'])
    if failure:
        with pytest.raises(RuntimeError): deploy_module.main()
        assert json.loads((tmp_path / 'current.json').read_text()) == old
    else:
        deploy_module.main()
        assert json.loads((tmp_path / 'current.json').read_text())['engine'] == 'postgresql'
    assert any('cp' in c and 'web:/app/data' in c for c in calls)
    assert any('up' in c and str(tmp_path / 'old' / 'compose.yaml') in c for c in calls) == recover
    assert not any('pull' in c and 'web' in c for c in calls)
