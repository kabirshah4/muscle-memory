"""End-to-end tests for mm.py. Run: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

MM = os.path.join(os.path.dirname(__file__), "..", "skills", "muscle-memory", "scripts", "mm.py")


class MMTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.store = os.path.join(self.root, "store")
        self.a = self.repo("proj-a")
        self.b = self.repo("proj-b")

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, name):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        subprocess.run(["git", "init", "-q", d], check=True)
        return d

    def mm(self, *args, cwd=None, stdin=None, env=None):
        e = dict(os.environ, MUSCLE_MEMORY_DIR=self.store, **(env or {}))
        r = subprocess.run([sys.executable, MM, *args], cwd=cwd or self.a, input=stdin,
                           capture_output=True, text=True, env=e)
        return r.stdout + r.stderr

    def make_reflex(self, cwd, key="deploy-worker", body='echo "ran in $(pwd)"', glob=False):
        g = ["--global"] if glob else []
        for _ in range(3):
            self.mm("log", key, "deploy the cloudflare worker", "--triggers", "deploy,worker,cloudflare", *g, cwd=cwd)
        script = os.path.join(self.root, key + ".sh")
        with open(script, "w") as f:
            f.write("#!/usr/bin/env bash\n" + body + "\n")
        self.mm("save", key, script, *g, cwd=cwd)

    def hook(self, prompt, cwd):
        return self.mm("hook", cwd=cwd, stdin=json.dumps({"prompt": prompt, "cwd": cwd}))

    # --- learning ---

    def test_compile_threshold(self):
        out = [self.mm("log", "x-task", "do x", "--triggers", "x") for _ in range(3)]
        self.assertIn("2 more", out[0])
        self.assertIn("COMPILE NOW", out[2])

    # --- scoping ---

    def test_project_reflex_does_not_leak(self):
        self.make_reflex(self.a)
        self.assertIn("REFLEX deploy-worker", self.mm("match", "deploy the worker", cwd=self.a))
        self.assertIn("no reflex", self.mm("match", "deploy the worker", cwd=self.b))
        self.assertEqual(self.hook("deploy the worker", self.b).strip(), "")

    def test_subdirectory_uses_project_root(self):
        self.make_reflex(self.a)
        sub = os.path.join(self.a, "src", "deep")
        os.makedirs(sub)
        self.assertIn("REFLEX deploy-worker", self.mm("match", "deploy the worker", cwd=sub))

    def test_global_reflex_matches_everywhere(self):
        self.make_reflex(self.a, glob=True)
        self.assertIn("scope=global", self.mm("match", "deploy the worker", cwd=self.b))

    # --- instinct ---

    def test_promotion_requires_clean_noarg_runs(self):
        self.make_reflex(self.a)
        self.mm("run", "deploy-worker", "some-arg")  # args don't count toward instinct
        for _ in range(2):
            self.mm("run", "deploy-worker")
        self.assertNotIn("INSTINCT", self.mm("list"))
        self.assertIn("promoted to INSTINCT", self.mm("run", "deploy-worker"))

    def test_instinct_runs_in_project_root_without_model(self):
        self.make_reflex(self.a)
        self.mm("promote", "deploy-worker")
        sub = os.path.join(self.a, "sub")
        os.makedirs(sub)
        out = json.loads(self.hook("deploy the worker", sub))
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].rstrip().endswith("ran in " + os.path.realpath(self.a)), out["reason"])

    def test_bypass_and_weak_match(self):
        self.make_reflex(self.a)
        self.mm("promote", "deploy-worker")
        self.assertEqual(self.hook("deploy the worker, no reflex", self.a).strip(), "")
        self.assertNotIn("block", self.hook("worker bees in the garden are buzzing loudly today", self.a))

    def test_failed_instinct_is_demoted(self):
        self.make_reflex(self.a, body="exit 3")
        self.mm("promote", "deploy-worker")
        out = self.hook("deploy the worker", self.a)
        self.assertIn("failed (exit 3) and was demoted", out)
        self.assertNotIn("INSTINCT", self.mm("list"))

    def test_recipe_cannot_be_promoted(self):
        md = os.path.join(self.root, "r.md")
        with open(md, "w") as f:
            f.write("1. do it\n")
        self.mm("log", "recipe-task", "a recipe", "--triggers", "recipe")
        self.mm("save", "recipe-task", md)
        self.assertIn("only script reflexes", self.mm("promote", "recipe-task"))

    # --- stop hook ---

    def transcript(self, prompt, n_calls, name="t.jsonl"):
        p = os.path.join(self.root, name)
        with open(p, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n")
            for i in range(n_calls):
                f.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": f"step {i}"}}]}}) + "\n")
                f.write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "content": "ok"}]}}) + "\n")
        return p

    def stop(self, prompt, n_calls, sid="s1", cwd=None):
        cwd = cwd or self.a
        data = {"transcript_path": self.transcript(prompt, n_calls), "cwd": cwd, "session_id": sid}
        return self.mm("stop-hook", cwd=cwd, stdin=json.dumps(data))

    def test_stop_hook_ignores_small_turns(self):
        self.assertEqual(self.stop("rename a variable", 2).strip(), "")

    def test_stop_hook_asks_new_task_once_per_session(self):
        self.assertIn("If it could recur", self.stop("set up the database fixtures", 5))
        self.assertEqual(self.stop("build the docs site", 5).strip(), "")      # same session
        self.assertIn("If it could recur", self.stop("build the docs site", 5, sid="s2"))

    def test_stop_hook_counts_known_task_without_model(self):
        self.mm("log", "db-fixtures", "set up database fixtures", "--triggers", "database,fixtures,setup")
        self.assertEqual(self.stop("set up the database fixtures", 5).strip(), "")  # silently counted -> 2
        out = json.loads(self.stop("set up the database fixtures", 5))            # -> 3: compile
        self.assertIn("Compile it into a reflex", out["reason"])

    # --- robustness ---

    def test_parallel_logs_do_not_lose_updates(self):
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda _: self.mm("log", "busy-task", "busy", "--triggers", "busy"), range(16)))
        self.assertIn("seen=16", self.mm("list"))

    def test_legacy_store_migrates_to_global_without_instinct(self):
        os.makedirs(os.path.join(self.store, "reflexes"))
        with open(os.path.join(self.store, "reflexes", "old-task.sh"), "w") as f:
            f.write("echo old\n")
        with open(os.path.join(self.store, "index.json"), "w") as f:
            json.dump({"old-task": {"count": 3, "triggers": ["legacy"], "summary": "legacy thing",
                                    "reflex": "old-task.sh", "uses": 5, "fails": 0, "instinct": True}}, f)
        out = self.mm("list", cwd=self.b)
        self.assertIn("[global]", out)
        self.assertIn("old-task", out)
        self.assertNotIn("INSTINCT", out)
        self.assertIn("old", self.mm("run", "old-task", cwd=self.b))


if __name__ == "__main__":
    unittest.main()
