"""End-to-end tests for mm.py: every test drives the real CLI in a subprocess.

Run: python3 -m unittest discover -v tests
"""
import json
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

MM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "muscle-memory", "scripts", "mm.py"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
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

    def env(self, extra=None):
        return dict(os.environ, MUSCLE_MEMORY_DIR=self.store, **(extra or {}))

    def mm(self, *args, cwd=None, stdin=None, env=None):
        r = subprocess.run([sys.executable, MM, *args], cwd=cwd or self.a, input=stdin,
                           capture_output=True, text=True, env=self.env(env))
        return r.stdout + r.stderr

    def rc(self, *args, cwd=None):
        return subprocess.run([sys.executable, MM, *args], cwd=cwd or self.a, capture_output=True,
                              text=True, env=self.env()).returncode

    def promote_tty(self, key="deploy-worker", answer="yes", cwd=None):
        """Run `promote` on a pseudo-terminal, answering the confirmation like a person would."""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd or self.a)
            os.environ["MUSCLE_MEMORY_DIR"] = self.store
            os.execv(sys.executable, [sys.executable, MM, "promote", key])
        out, sent = b"", False
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if not sent and b"Type 'yes'" in out:
                os.write(fd, (answer + "\n").encode())
                sent = True
        os.waitpid(pid, 0)
        os.close(fd)
        return out.decode(errors="replace")

    def index(self, glob=False):
        for dirpath, _, files in os.walk(self.store):
            if "index.json" in files and ("global" in dirpath) == glob:
                with open(os.path.join(dirpath, "index.json")) as f:
                    return json.load(f), dirpath
        return {}, None

    def make_reflex(self, cwd=None, key="deploy-worker", body='echo "ran in $(pwd)"', glob=False):
        cwd = cwd or self.a
        g = ["--global"] if glob else []
        for _ in range(3):
            self.mm("log", *g, key, "deploy the cloudflare worker", "--triggers", "deploy,worker,cloudflare", cwd=cwd)
        script = os.path.join(self.root, key + ".sh")
        with open(script, "w") as f:
            f.write("#!/usr/bin/env bash\n" + body + "\n")
        self.mm("save", *g, key, script, cwd=cwd)
        return script

    def hook(self, prompt, cwd=None):
        cwd = cwd or self.a
        return self.mm("hook", cwd=cwd, stdin=json.dumps({"prompt": prompt, "cwd": cwd}))

    def use_via_claude(self, prompt="deploy the worker", times=3, key="deploy-worker", cwd=None):
        """Simulate Claude: the prompt hook hints, then Claude runs the reflex with no args."""
        for _ in range(times):
            self.hook(prompt, cwd)
            self.mm("run", key, cwd=cwd)

    def make_instinct(self, prompt="deploy the worker", **kw):
        self.make_reflex(**kw)
        self.use_via_claude(prompt)
        out = self.promote_tty()
        self.assertIn("instinct ON", out)
        return out


class Learning(Base):
    def test_compile_threshold(self):
        out = [self.mm("log", "x-task", "do x", "--triggers", "x") for _ in range(2)]
        self.assertIn("1 more", out[0])
        self.assertIn("COMPILE NOW", out[1])

    def test_save_summary_replaces_specific_one(self):
        script = self.make_reflex()
        self.mm("save", "deploy-worker", script, "--summary", "deploy a worker to an environment")
        self.assertIn("deploy a worker to an environment", self.mm("list"))
        self.assertIn("different names or values", self.hook("deploy the worker"))

    def test_save_with_missing_source_keeps_existing_reflex(self):
        self.make_reflex()
        out = self.mm("save", "deploy-worker", "/nope/missing.sh")
        self.assertIn("no such file", out)
        self.assertIn("ran in", self.mm("run", "deploy-worker"))

    def test_save_onto_itself_keeps_reflex(self):
        self.make_reflex()
        _, d = self.index()
        self.mm("save", "deploy-worker", os.path.join(d, "reflexes", "deploy-worker.sh"))
        self.assertIn("ran in", self.mm("run", "deploy-worker"))

    def test_save_replaces_other_variant(self):
        self.make_reflex()
        md = os.path.join(self.root, "r.md")
        with open(md, "w") as f:
            f.write("1. do it\n")
        self.mm("save", "deploy-worker", md)
        _, d = self.index()
        self.assertEqual(sorted(os.listdir(os.path.join(d, "reflexes"))), ["deploy-worker.md"])
        self.assertIn("recipe", self.mm("list"))
        self.assertNotIn("instinct-in", self.mm("list"))


class Scoping(Base):
    def test_project_reflex_does_not_leak(self):
        self.make_reflex(self.a)
        self.assertIn("REFLEX deploy-worker", self.mm("match", "deploy the worker", cwd=self.a))
        self.assertIn("no reflex", self.mm("match", "deploy the worker", cwd=self.b))
        self.assertEqual(self.hook("deploy the worker", self.b).strip(), "")

    def test_subdirectory_and_worktree_share_project(self):
        self.make_reflex(self.a)
        sub = os.path.join(self.a, "src", "deep")
        os.makedirs(sub)
        self.assertIn("REFLEX deploy-worker", self.mm("match", "deploy the worker", cwd=sub))
        subprocess.run(["git", "-C", self.a, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        wt = os.path.join(self.root, "wt")
        subprocess.run(["git", "-C", self.a, "worktree", "add", "-q", wt], check=True, capture_output=True)
        self.assertIn("REFLEX deploy-worker", self.mm("match", "deploy the worker", cwd=wt))

    def test_global_reflex_matches_everywhere(self):
        self.make_reflex(self.a, glob=True)
        self.assertIn("scope=global", self.mm("match", "deploy the worker", cwd=self.b))

    def test_global_flag_targets_global_entry(self):
        self.mm("log", "--global", "tidy", "global tidy", "--triggers", "tidy")
        self.mm("log", "tidy", "project tidy", "--triggers", "tidy")
        self.assertIn("forgot tidy (global)", self.mm("forget", "--global", "tidy"))
        out = self.mm("list")
        self.assertIn("project tidy", out)
        self.assertNotIn("global tidy", out)

    def test_run_executes_from_project_root_and_passes_args(self):
        self.make_reflex(body='echo "ran in $(pwd) args=$*"')
        sub = os.path.join(self.a, "sub")
        os.makedirs(sub)
        out = self.mm("run", "deploy-worker", "--global", "x", cwd=sub)
        self.assertIn(f"ran in {self.a} args=--global x", out)


class Instinct(Base):
    def test_clean_runs_make_eligible_never_promote(self):
        self.make_reflex()
        self.mm("run", "deploy-worker", "some-arg")  # runs with args don't count
        self.use_via_claude(times=1)
        self.assertNotIn("ELIGIBLE", self.mm("list"))
        self.hook("deploy the worker")
        self.assertIn("ELIGIBLE", self.mm("run", "deploy-worker"))
        self.assertNotIn("INSTINCT", self.mm("list"))
        self.assertIn("promote deploy-worker", self.hook("deploy the worker"))

    def test_runs_without_a_prompt_never_become_eligible(self):
        self.make_reflex()
        for _ in range(4):
            self.mm("run", "deploy-worker")  # no hook prompt recorded -> nothing to approve
        self.assertNotIn("ELIGIBLE", self.mm("list"))

    def test_promote_refuses_before_eligible(self):
        self.make_reflex()
        self.assertIn("not eligible yet", self.promote_tty())
        self.assertNotIn("INSTINCT", self.mm("list"))

    def test_promote_refuses_without_terminal(self):
        self.make_reflex()
        self.use_via_claude()
        out = self.mm("promote", "deploy-worker")
        self.assertIn("run it yourself in a terminal", out)
        self.assertNotIn("INSTINCT", self.mm("list"))

    def test_promote_shows_script_and_phrasings_and_can_be_declined(self):
        self.make_reflex()
        self.use_via_claude()
        out = self.promote_tty(answer="no")
        self.assertIn('echo "ran in $(pwd)"', out)
        self.assertIn("deploy the worker", out)
        self.assertIn("not approved", out)
        self.assertNotIn("INSTINCT", self.mm("list"))

    def test_instinct_runs_on_approved_phrasing_without_model(self):
        self.make_instinct()
        sub = os.path.join(self.a, "sub")
        os.makedirs(sub)
        out = json.loads(self.hook("deploy the worker please", sub))
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].rstrip().endswith("ran in " + self.a), out["reason"])

    def test_reordered_phrasing_does_not_fire(self):
        self.make_instinct()
        self.assertNotIn('"decision": "block"', self.hook("worker the deploy"))
        self.assertIn('"decision": "block"', self.hook("Deploy the worker."))

    def test_instinct_never_fires_on_negation_question_or_other_wording(self):
        self.make_instinct()
        for prompt in ("do not deploy the cloudflare worker", "rollback the cloudflare worker deploy",
                       "why does deploy worker keep failing?", "deploy the worker to staging",
                       "deploy the worker, no reflex",
                       "please can you just deploy the worker for me now i want you to do it again"):
            self.assertNotIn('"block"', self.hook(prompt), prompt)

    def test_editing_script_revokes_approval(self):
        self.make_instinct()
        _, d = self.index()
        with open(os.path.join(d, "reflexes", "deploy-worker.sh"), "a") as f:
            f.write("echo changed\n")
        self.assertIn("needs re-approval", self.mm("list"))
        self.assertNotIn('"block"', self.hook("deploy the worker"))
        self.assertIn("ELIGIBLE", self.mm("list"))

    def test_failed_instinct_is_demoted(self):
        self.make_reflex(body='[ -f fail-now ] && exit 3; echo ok')
        self.use_via_claude()
        self.promote_tty()
        open(os.path.join(self.a, "fail-now"), "w").close()
        out = self.hook("deploy the worker")
        self.assertIn("failed (exit 3) and was demoted", out)
        self.assertNotIn("INSTINCT", self.mm("list"))

    def test_instinct_timeout_kills_process_group(self):
        self.make_reflex(body='if [ -f slow ]; then (sleep 3; touch "$PWD/grandchild-ran") & sleep 30; fi; echo ok')
        self.use_via_claude()
        self.promote_tty()
        open(os.path.join(self.a, "slow"), "w").close()
        fast = os.path.join(self.root, "mm_fast.py")  # same code, 1s timeout, so the test is quick
        with open(MM) as f:
            src = f.read().replace("INSTINCT_TIMEOUT = 110", "INSTINCT_TIMEOUT = 1")
        with open(fast, "w") as f:
            f.write(src)
        r = subprocess.run([sys.executable, fast, "hook"], cwd=self.a, capture_output=True, text=True,
                           input=json.dumps({"prompt": "deploy the worker", "cwd": self.a}), env=self.env())
        self.assertIn("timed out", r.stdout)
        subprocess.run(["sleep", "4"])
        self.assertFalse(os.path.exists(os.path.join(self.a, "grandchild-ran")))

    def test_two_fails_disable_reflex_and_forget_cleans_up(self):
        self.make_reflex(body="exit 1")
        self.mm("run", "deploy-worker")
        self.assertIn("reflex disabled", self.mm("run", "deploy-worker"))
        _, d = self.index()
        self.assertIn("deploy-worker.sh.broken", os.listdir(os.path.join(d, "reflexes")))
        self.mm("forget", "deploy-worker")
        self.assertEqual(os.listdir(os.path.join(d, "reflexes")), [])

    def test_recipe_cannot_be_promoted(self):
        md = os.path.join(self.root, "r.md")
        with open(md, "w") as f:
            f.write("1. do it\n")
        self.mm("log", "recipe-task", "a recipe", "--triggers", "recipe")
        self.mm("save", "recipe-task", md)
        self.assertIn("only script reflexes", self.promote_tty("recipe-task"))


class Hints(Base):
    def test_weak_overlap_gets_no_hint(self):
        self.make_reflex()
        self.assertEqual(self.hook("fix the deploy").strip(), "")
        self.assertEqual(self.hook("worker bees in the garden are buzzing loudly today").strip(), "")

    def test_hint_is_conditional(self):
        self.make_reflex()
        self.assertIn("If that is the task", self.hook("deploy the worker"))

    def test_hook_survives_bad_input(self):
        self.make_reflex()
        for stdin in ("not json", "[]", json.dumps({"prompt": None}), json.dumps({"cwd": 5})):
            r = subprocess.run([sys.executable, MM, "hook"], cwd=self.a, input=stdin, capture_output=True,
                               text=True, env=self.env())
            self.assertEqual(r.returncode, 0, stdin)
            self.assertNotIn("Traceback", r.stderr, stdin)


class Templates(Base):
    def learn(self):
        self.make_reflex(body='echo "deployed to $1"')
        for env in ("staging", "prod"):
            self.hook(f"deploy the worker to {env}")
            self.mm("run", "deploy-worker", env)

    def test_args_from_the_prompt_become_an_approvable_template(self):
        self.learn()
        self.assertIn("ELIGIBLE", self.mm("list"))
        out = self.promote_tty()
        self.assertIn("deploy the worker to {1}", out)
        self.assertIn("instinct ON", out)
        r = json.loads(self.hook("Deploy the worker to canary."))
        self.assertEqual(r["decision"], "block")
        self.assertIn("deployed to canary", r["reason"])

    def test_unsafe_or_different_wording_never_fires(self):
        self.learn()
        self.promote_tty()
        for p in ("deploy the worker to $(touch x)", "deploy the worker to ../etc", "deploy the worker to a b",
                  "please deploy the worker to prod", "don't deploy the worker to prod",
                  "deploy the worker to prod?"):
            self.assertNotIn('"decision": "block"', self.hook(p), p)

    def test_arg_not_in_prompt_resets_streak(self):
        self.make_reflex()
        self.hook("deploy the worker to staging")
        self.mm("run", "deploy-worker", "staging")
        self.hook("deploy the worker to staging")
        self.mm("run", "deploy-worker", "something-else")
        self.assertNotIn("ELIGIBLE", self.mm("list"))


class Launcher(Base):
    def test_mm_key_runs_script_with_no_model_call(self):
        self.make_reflex(body='echo "args: $*"')
        out = json.loads(self.hook("mm deploy-worker staging --fast"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("args: staging --fast", out["reason"])
        self.assertIn("uses=1", self.mm("list"))

    def test_failure_is_reported_and_recorded(self):
        self.make_reflex(body="echo boom; exit 3")
        self.assertIn("failed (exit 3)", json.loads(self.hook("mm deploy-worker"))["reason"])

    def test_launcher_runs_never_count_toward_instinct(self):
        self.make_reflex()
        for _ in range(3):
            self.hook("deploy the worker")         # leaves a pending prompt
            self.hook("mm deploy-worker")          # explicit launch must not turn it into a phrasing
        self.assertNotIn("ELIGIBLE", self.mm("list"))
        self.assertIn("instinct-in=2", self.mm("list"))

    def test_unknown_key_or_plain_prompt_falls_through(self):
        self.make_reflex()
        self.assertEqual(self.hook("mm what do you think").strip(), "")
        self.assertNotIn("block", self.hook("mm deploy-worker'unclosed"))  # bad quoting: not launched


class StopHook(Base):
    def transcript(self, prompt, n_calls, extra=None, name="t.jsonl"):
        p = os.path.join(self.root, name)
        with open(p, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n")
            f.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": "meta"}}) + "\n")
            for i in range(n_calls):
                f.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": f"step {i}"}}]}}) + "\n")
                f.write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "content": "ok"}]}}) + "\n")
            for cmd in extra or []:
                f.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}) + "\n")
            f.write("not json\n")
        return p

    def stop(self, prompt, n_calls, sid="s1", extra=None, env=None):
        data = {"transcript_path": self.transcript(prompt, n_calls, extra), "cwd": self.a, "session_id": sid}
        return self.mm("stop-hook", stdin=json.dumps(data), env=env)

    def test_ignores_small_turns(self):
        self.assertEqual(self.stop("rename a variable", 1).strip(), "")

    def test_opt_in_ask_new_task_once_per_session(self):
        ask = {"MUSCLE_MEMORY_ASK": "1"}
        self.assertIn("If it could recur", self.stop("set up the database fixtures", 5, env=ask))
        self.assertEqual(self.stop("build the docs site", 5, env=ask).strip(), "")
        self.assertIn("If it could recur", self.stop("build the docs site", 5, sid="s2", env=ask))

    def test_new_task_is_learned_silently_then_compiled(self):
        p = "set up the database fixtures for tests"
        self.assertEqual(self.stop(p, 5).strip(), "")                  # logged with no model turn
        self.assertIn("1 one-off task seen once", self.mm("list"))
        out = json.loads(self.stop("set up database fixtures for the tests", 5, sid="s2"))
        self.assertIn("Compile it into a reflex", out["reason"])
        self.assertIn(p, out["reason"])                                # Claude is told what the task was

    def test_silent_log_ignores_contentless_prompts(self):
        self.stop("do it", 5)
        self.assertIn("no tasks recorded", self.mm("list"))

    def test_counts_known_task_and_asks_to_compile_once(self):
        self.mm("log", "db-fixtures", "set up database fixtures", "--triggers", "database,fixtures,setup")
        out = json.loads(self.stop("set up the database fixtures", 5))            # -> 2: compile
        self.assertIn("Compile it into a reflex", out["reason"])
        self.assertEqual(self.stop("set up the database fixtures", 5).strip(), "")  # not asked again
        self.assertIn("seen=2 ", self.mm("list"))

    def test_long_detailed_prompt_for_known_task_is_counted(self):
        self.mm("log", "csv-summary", "summarize every csv in data into data/summary.json",
                "--triggers", "csv,summary,json,data,rows,columns")
        self.stop("Write data/summary.json describing each CSV under data/: number of rows, columns, and min, "
                  "max and mean of every numeric column.", 5)
        self.assertIn("seen=2 ", self.mm("list"))

    def test_unrelated_task_with_one_shared_word_is_not_counted(self):
        self.mm("log", "db-fixtures", "set up database fixtures", "--triggers", "database,fixtures,setup")
        self.stop("setup ci pipeline caching", 5, sid="x")
        self.assertIn("seen=1 ", self.mm("list"))

    def test_turn_that_used_mm_is_skipped_in_both_forms(self):
        for cmd in ('python3 /x/mm.py log k "s"', 'MM=/x/scripts/mm.py; python3 "$MM" run k'):
            self.assertEqual(self.stop("set up the database fixtures", 5, sid=cmd, extra=[cmd]).strip(), "", cmd)


class Robustness(Base):
    def test_parallel_writes_do_not_lose_updates(self):
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda i: self.mm("log", f"task-{i % 4}", "busy", "--triggers", "busy"), range(16)))
        out = self.mm("list")
        self.assertEqual(out.count("seen=4 "), 4)

    def test_corrupt_index_is_never_overwritten(self):
        for k in ("a-task", "b-task"):
            self.mm("log", k, k, "--triggers", k)
        _, d = self.index()
        p = os.path.join(d, "index.json")
        with open(p) as f:
            good = f.read()
        with open(p, "w") as f:
            f.write(good[:-5])
        self.assertIn("unreadable", self.mm("log", "c-task", "c"))
        self.assertEqual(self.rc("log", "c-task", "c"), 1)
        with open(p) as f:
            self.assertEqual(f.read(), good[:-5])

    def test_wrong_shape_index_errors_cleanly(self):
        self.mm("log", "a-task", "a")
        _, d = self.index()
        for bad in ("[]", '{"k": 5}'):
            with open(os.path.join(d, "index.json"), "w") as f:
                f.write(bad)
            out = self.mm("list")
            self.assertIn("unreadable", out)
            self.assertNotIn("Traceback", out)

    def test_wrong_field_types_are_tolerated(self):
        self.make_reflex()
        idx, d = self.index()
        idx["deploy-worker"].update(triggers="deploy", summary=None, count="x", phrasings=[1, "a"])
        with open(os.path.join(d, "index.json"), "w") as f:
            json.dump(idx, f)
        for args in (("list",), ("match", "deploy the worker"), ("show", "deploy-worker")):
            self.assertNotIn("Traceback", self.mm(*args), args)
        self.assertNotIn("Traceback", self.hook("deploy the worker"))

    def test_entry_missing_fields_is_tolerated(self):
        self.mm("log", "a-task", "a")
        idx, d = self.index()
        idx["a-task"] = {"summary": "bare"}
        with open(os.path.join(d, "index.json"), "w") as f:
            json.dump(idx, f)
        self.assertIn("a-task", self.mm("list"))

    def test_bad_env_values_fall_back_to_defaults(self):
        self.assertIn("1 more", self.mm("log", "a-task", "a", env={"MUSCLE_MEMORY_THRESHOLD": "three"}))

    def test_usage_errors_and_help(self):
        self.assertEqual(self.rc("log"), 2)
        self.assertIn("usage: mm.py promote", self.mm("promote"))
        self.assertEqual(self.rc("-h"), 0)
        self.assertEqual(self.rc("bogus"), 2)

    def test_legacy_store_merges_into_global(self):
        self.mm("log", "--global", "keep-me", "existing global", "--triggers", "keep")
        os.makedirs(os.path.join(self.store, "reflexes"))
        with open(os.path.join(self.store, "reflexes", "old-task.sh"), "w") as f:
            f.write("echo old\n")
        with open(os.path.join(self.store, "index.json"), "w") as f:
            json.dump({"old-task": {"count": 3, "triggers": ["legacy"], "summary": "legacy thing",
                                    "reflex": "old-task.sh", "uses": 5, "fails": 0, "instinct": True},
                       "keep-me": {"count": 9, "summary": "legacy copy"}}, f)
        out = self.mm("list", cwd=self.b)
        self.assertIn("old-task", out)
        self.assertIn("existing global", out)
        self.assertNotIn("legacy copy", out)
        self.assertNotIn("INSTINCT", out)
        self.assertIn("old", self.mm("run", "old-task", cwd=self.b))

    def test_corrupt_legacy_store_is_moved_aside(self):
        os.makedirs(self.store)
        with open(os.path.join(self.store, "index.json"), "w") as f:
            f.write("{bad")
        self.assertIn("no tasks recorded yet", self.mm("list"))
        self.assertTrue(any(n.startswith("index.json.corrupt-") for n in os.listdir(self.store)))
        r = subprocess.run([sys.executable, MM, "hook"], cwd=self.a, input='{"prompt":"x"}', capture_output=True,
                           text=True, env=self.env())
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
