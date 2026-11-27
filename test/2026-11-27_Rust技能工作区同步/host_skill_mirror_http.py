"""Production Web Host: saved library selection, scoped mirroring and model discovery."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-11-27_RustAgent工具对照"))
from knowledge_media_http import Browser, LocalServices, fixture_environment, model_catalog_host, call


def skill(parent, name, body):
    folder = parent / name
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "SKILL.md"
    file.write_text(f"---\nname: {name}\ndescription: Fixture skill\n---\n{body}\n", encoding="utf-8")
    return file


class Mock(LocalServices):
    def calls(self, body):
        messages = body["messages"]
        if any(message.get("content") == "Archive another fixture skill" for message in messages):
            result = next((json.loads(message["content"]) for message in messages
                           if message.get("tool_call_id") == "mirror-archive"), None)
            if result is None:
                return [call("create_skill", "mirror-archive", {"source_dir": "fixture-archived"})]
            assert result["success"] and "sync_warning" not in result, result
            return "Fixture archive succeeded"
        if any(message.get("tool_call_id") == "mirror-search" for message in messages):
            result = next(
                json.loads(message["content"]) for message in messages
                if message.get("tool_call_id") == "mirror-search"
            )
            assert result["success"], result
            assert {item["skill_id"] for item in result["results"]} == {"fixture-shared", "fixture-builtin"}, result
            return "Fixture search succeeded"
        system = next(message["content"] for message in messages if message["role"] == "system")
        assert "fixture-shared" in system and "fixture-builtin" in system, system
        assert "fixture-not-selected" not in system, system
        return [call("search_skills", "mirror-search", {"keywords": ["fixture-shared", "fixture-builtin"]})]


def names(browser, scope):
    return {item["name"] for item in browser.api("GET", "/api/skills" + scope)["skills"]}


def finished(browser, task, scope):
    identity = task.get("data", task)["task_id"]
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        row = browser.api("GET", f"/api/tasks/{identity}{scope}")["data"]
        if row["status"] == "succeeded":
            return
        assert row["status"] not in ("failed", "cancelled"), row
        time.sleep(.03)
    raise AssertionError(f"Agent task {identity} did not complete")


def main(binary):
    with TemporaryDirectory() as folder, ThreadingHTTPServer(("127.0.0.1", 0), Mock) as model:
        tmp = Path(folder).resolve()
        env = fixture_environment(tmp)
        env["ASTRION_RESOURCE_ROOT"] = str(HERE.parents[1])
        data = Path(env["DATA_DIR"])
        data.mkdir()
        (data / "personalization.json").write_text('{"auto_generate_title":false}', encoding="utf-8")
        global_library = tmp / "fixture-bundled"
        env["AGENT_SKILLS_DIR"] = str(global_library)
        private_library = Path(env["ASTRION_DATA_ROOT"]) / "host" / "agentskills"
        bundled = skill(global_library, "fixture-builtin", "Bundled origin")
        skill(global_library, "fixture-shared", "Bundled fallback")
        skill(global_library, "fixture-not-selected", "Should not be mirrored")
        private = skill(private_library, "fixture-shared", "Private override")
        private_extra = private.parent / "reference.txt"
        private_extra.write_text("Private linked material", encoding="utf-8")
        one = Path(env["HOST_PROJECT_PATH"])
        two = tmp / "second"
        two.mkdir()
        blocked = tmp / "blocked"
        blocked_astron = blocked / ".astrion"
        blocked_astron.mkdir(parents=True)
        collision = blocked_astron / "skills"
        collision.write_text("Keep this local file", encoding="utf-8")
        registry = Path(env["HOST_WORKSPACES_FILE"])
        value = json.loads(registry.read_text())
        value["workspaces"].extend([
            {"workspace_id": "second", "label": "Second", "path": str(two)},
            {"workspace_id": "blocked", "label": "Blocked", "path": str(blocked)},
        ])
        registry.write_text(json.dumps(value), encoding="utf-8")
        (Path(env["DEPLOY_CONFIG_DIR"]) / "custom_models.json").write_text(json.dumps({"models": [{
            "model_name": "chat-local", "model_id": "chat-local",
            "url": f"http://127.0.0.1:{model.server_port}/v1", "apikey": "fixture-only",
        }]}), encoding="utf-8")
        live = threading.Thread(target=model.serve_forever, daemon=True)
        live.start()
        model.errors = []
        s1, s2 = "?workspace_id=default", "?workspace_id=second"
        one_skills, two_skills = (project / ".astrion" / "skills" for project in (one, two))
        local = skill(one_skills, "fixture-local", "Local files must never be treated as managed")
        same = skill(one_skills, "fixture-not-selected", "An untracked local collision")
        selected = ["fixture-builtin", "fixture-shared"]
        try:
            with model_catalog_host(binary, env, tmp / "host1.log") as url:
                app = Browser(url)
                catalog = app.api("GET", "/api/personalization" + s1)["skills_catalog"]
                assert {(item["id"], item["source"]) for item in catalog} == {
                    ("fixture-builtin", "global"), ("fixture-shared", "private"), ("fixture-not-selected", "global"),
                }
                assert app.api("POST", "/api/personalization" + s1, {"enabled_skills": selected})["success"]
                assert names(app, s1) == {"fixture-local", *selected}
                assert (one_skills / "fixture-shared" / "SKILL.md").read_bytes() == private.read_bytes()
                assert (one_skills / "fixture-shared" / "reference.txt").read_bytes() == private_extra.read_bytes()
                assert same.exists() and local.exists()
                assert not two_skills.exists(), "settings wrote outside the selected workspace"
                # First Agent admission in a different registered workspace, before touching its menu.
                task = app.api("POST", "/api/tasks" + s2, {"message": "Check skills", "model_key": "chat-local"}, status=202)
                finished(app, task, s2)
                assert not model.errors, model.errors
                assert names(app, s2) == set(selected)
                # A project skill added after mirroring must take precedence immediately.
                project_skill = skill(two / ".agents" / "skills", "fixture-builtin", "Local project skill")
                assert names(app, s2) == {"fixture-shared"}
                assert "Local project skill" in project_skill.read_text()
                project_skill.unlink()
                assert names(app, s2) == set(selected)
                mirror = two_skills / "fixture-builtin" / "SKILL.md"
                mirror.unlink()
                mirror.parent.rmdir()
                assert names(app, s2) == set(selected)
                assert mirror.read_bytes() == bundled.read_bytes()
                # A locally edited mirror remains intact on disk even after deselection.
                modified = skill(one_skills, "fixture-shared", "Retain this local edit")
                assert app.api("POST", "/api/personalization" + s1, {"enabled_skills": []})["success"]
                assert names(app, s1) == {"fixture-local"}
                assert "Retain this local edit" in modified.read_text()
                assert not (one_skills / "fixture-builtin").exists()
                assert (two_skills / "fixture-builtin").exists(), "another workspace was updated before use"
                assert names(app, s2) == set()
                assert not (two_skills / "fixture-builtin").exists()
                assert not (two_skills / "fixture-shared").exists()
                assert app.api("POST", "/api/personalization" + s1, {"enabled_skills": selected})["success"]
                assert names(app, s1) == {"fixture-local", *selected}
                assert "Retain this local edit" in modified.read_text()
                assert names(app, s2) == set(selected)
                fault = app.api("POST", "/api/personalization?workspace_id=blocked", {"enabled_skills": selected})
                assert fault["success"] and fault["skill_sync_warning"]
                assert str(blocked) not in fault["skill_sync_warning"]
                app.api("GET", "/api/skills?workspace_id=blocked", status=500)
                assert collision.read_text() == "Keep this local file"

                # The archive tool changes the private library without another settings save,
                # so cached knowledge in a different workspace must become due for refresh.
                skill(one, "fixture-archived", "New private archive")
                chat = app.api("POST", "/api/conversations" + s1, {"settings": {
                    "model_key": "chat-local", "work_mode": "execute", "permission_mode": "unrestricted",
                    "execution_mode": "sandbox", "network_permission": "restricted",
                    "run_mode": "fast", "thinking_mode": False, "reasoning_effort": None,
                }}, status=201)
                task = app.api("POST", "/api/tasks" + s1, {
                    "message": "Archive another fixture skill", "model_key": "chat-local",
                    "conversation_id": chat["conversation_id"],
                }, status=202)
                finished(app, task, s1)
                assert not model.errors, model.errors
                assert (private_library / "fixture-archived" / "SKILL.md").is_file()
                assert not (two_skills / "fixture-archived").exists()
                assert names(app, s2) == {*selected, "fixture-archived"}
                selected.append("fixture-archived")

            # On restart, refresh only the previously owned unchanged copy.
            skill(global_library, "fixture-builtin", "Bundled source updated")
            with model_catalog_host(binary, env, tmp / "host2.log") as url:
                app = Browser(url)
                assert names(app, s1) == {"fixture-local", *selected}
                assert (one_skills / "fixture-builtin" / "SKILL.md").read_bytes() == bundled.read_bytes()
                assert "Retain this local edit" in modified.read_text()
                assert "An untracked local collision" in same.read_text()
                assert local.exists()
        finally:
            model.shutdown()
            live.join()
    print("Host skill mirroring: scoped preferences, lazy Agent discovery, private files, preservation and restart refresh passed")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--binary", required=True, type=Path)
    main(cli.parse_args().binary.resolve(strict=True))
