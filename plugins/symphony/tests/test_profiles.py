import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugins.symphony.symphony.routing import (
    Assessment,
    profiles_for,
    resolve_tier,
    route_for,
    snapshot_for,
)
from plugins.symphony.symphony.runtime import handle
from plugins.symphony.symphony.store import StateStore


def roster(*slugs, hidden=()):
    models = [{"slug": slug, "visibility": "list"} for slug in slugs]
    models += [{"slug": slug, "visibility": "hide"} for slug in hidden]
    return {"models": models}


class ProfileDataTests(unittest.TestCase):
    OFFICIAL_EFFORTS = {
        "codex": {
            "gpt-5.5": {"low", "medium", "high", "xhigh"},
            "gpt-6-luna": {"low", "medium", "high", "xhigh", "max"},
            "gpt-6-sol": {"low", "medium", "high", "xhigh", "max"},
            "gpt-6-astra": {"low", "medium", "high", "xhigh", "max"},
        },
        "claude": {
            "sonnet": {"low", "medium", "high", "xhigh", "max"},
            "opus": {"low", "medium", "high", "xhigh", "max"},
        },
    }

    def test_every_profile_covers_all_four_tiers(self):
        for provider in ("codex", "claude"):
            for profile in profiles_for(provider):
                with self.subTest(provider=provider, profile=profile["id"]):
                    self.assertEqual(
                        set(profile["tiers"]),
                        {"economy", "balanced", "capable", "strongest"},
                    )

    def test_every_tier_model_declares_its_efforts(self):
        for provider in ("codex", "claude"):
            for profile in profiles_for(provider):
                for tier, model in profile["tiers"].items():
                    with self.subTest(provider=provider, profile=profile["id"], tier=tier):
                        self.assertIn(model, profile["efforts"])
                        self.assertTrue(profile["efforts"][model])

    def test_shipped_efforts_are_provider_supported(self):
        for provider in ("codex", "claude"):
            for profile in profiles_for(provider):
                for model, efforts in profile["efforts"].items():
                    with self.subTest(provider=provider, profile=profile["id"], model=model):
                        self.assertTrue(set(efforts) <= self.OFFICIAL_EFFORTS[provider][model])

    def test_the_last_profile_is_an_unconditional_floor(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                floor = profiles_for(provider)[-1]
                self.assertFalse(floor.get("requires_all"))
                self.assertFalse(floor.get("requires_any"))

    def test_an_unprobed_account_routes_through_the_floor(self):
        route = route_for(Assessment("small", "complex"))
        floor = profiles_for("codex")[-1]
        resolved = resolve_tier(route, snapshot_for("codex", None))
        self.assertEqual(resolved["lead_model"], floor["tiers"]["strongest"])


class EntitlementProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.state_root = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def heartbeat(self, environ, provider="codex"):
        payload = {
            "session_id": f"{provider}-session",
            "cwd": str(self.project),
            "hook_event_name": "SessionStart",
        }
        if provider == "codex":
            payload.update({"turn_id": "turn-1", "model": "codex-model"})
        handle(payload, environ)
        return StateStore(self.state_root).load(self.project).activation.get(provider, {})

    def codex_home(self, payload):
        home = self.root / "codex-home"
        home.mkdir(exist_ok=True)
        (home / "models_cache.json").write_text(json.dumps(payload))
        return {"SYMPHONY_STATE_DIR": str(self.state_root), "CODEX_HOME": str(home)}

    def test_a_complete_roster_selects_the_full_profile(self):
        environ = self.codex_home(
            roster("gpt-6-astra", "gpt-6-sol", "gpt-6-luna")
        )
        self.assertEqual(self.heartbeat(environ).get("profile"), "full")

    def test_a_missing_model_falls_back_to_the_base_profile(self):
        environ = self.codex_home(roster("gpt-6-sol", "gpt-6-luna"))
        self.assertEqual(self.heartbeat(environ).get("profile"), "base")

    def test_a_hidden_model_does_not_count_as_entitlement(self):
        environ = self.codex_home(
            roster("gpt-6-sol", "gpt-6-luna", hidden=("gpt-6-astra",))
        )
        self.assertEqual(self.heartbeat(environ).get("profile"), "base")

    def test_an_unreadable_roster_records_no_profile(self):
        environ = {"SYMPHONY_STATE_DIR": str(self.state_root), "CODEX_HOME": str(self.root / "absent")}
        self.assertFalse(self.heartbeat(environ).get("profile"))

    def test_a_pinned_profile_skips_the_probe_entirely(self):
        environ = {
            "SYMPHONY_STATE_DIR": str(self.state_root),
            "CODEX_HOME": str(self.root / "absent"),
            "SYMPHONY_PROFILE": "full",
        }
        self.assertEqual(self.heartbeat(environ).get("profile"), "full")

    def test_the_probe_runs_once_and_is_reused_within_a_session(self):
        environ = self.codex_home(
            roster("gpt-6-astra", "gpt-6-sol", "gpt-6-luna")
        )
        self.heartbeat(environ)
        # Remove the roster: a second heartbeat in the same session must not
        # re-probe, so the recorded answer survives.
        (Path(environ["CODEX_HOME"]) / "models_cache.json").unlink()
        self.assertEqual(self.heartbeat(environ).get("profile"), "full")

    def test_a_claude_plan_selects_its_profile_without_reading_credentials(self):
        environ = {"SYMPHONY_STATE_DIR": str(self.state_root)}
        completed = unittest.mock.Mock(stdout=json.dumps({"subscriptionType": "team"}))
        with patch("plugins.symphony.symphony.runtime.subprocess.run", return_value=completed) as run:
            profile = self.heartbeat(environ, "claude").get("profile")
        self.assertEqual(profile, "opus")
        self.assertEqual(run.call_args.args[0], ["claude", "auth", "status"])

    def test_an_unknown_claude_plan_uses_the_floor_profile(self):
        environ = {"SYMPHONY_STATE_DIR": str(self.state_root)}
        completed = unittest.mock.Mock(stdout=json.dumps({"subscriptionType": "pro"}))
        with patch("plugins.symphony.symphony.runtime.subprocess.run", return_value=completed):
            self.assertEqual(self.heartbeat(environ, "claude").get("profile"), "sonnet")

    def test_a_failing_probe_never_breaks_the_hook(self):
        environ = {"SYMPHONY_STATE_DIR": str(self.state_root)}
        with patch(
            "plugins.symphony.symphony.runtime.subprocess.run", side_effect=OSError("no binary")
        ):
            activation = self.heartbeat(environ, "claude")
        self.assertEqual(activation.get("state"), "guarded")
        self.assertFalse(activation.get("profile"))


if __name__ == "__main__":
    unittest.main()


class AgentFileContractTests(unittest.TestCase):
    """The Claude agent filenames are part of the routing contract.

    A profile naming a model with no matching file blocks every spawn of that
    role permanently, and the bad value is persisted into the recorded route,
    so it survives retries. This is the check that stops that shipping.
    """

    def generator(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "scripts" / "generate_agents.py"
        spec = importlib.util.spec_from_file_location("generate_agents", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_every_selectable_route_has_a_packaged_agent(self):
        generator = self.generator()
        agents = Path(__file__).resolve().parents[1] / "agents"
        present = {path.name for path in agents.glob("*.md")}
        required = {
            f"symphony-{role}-{model}-{effort}.md"
            for role, model, effort in generator.required_agents()
        }
        self.assertEqual(required - present, set(), "a profile names a model with no agent file")
        self.assertEqual(present - required, set(), "an agent file no profile can select")

    def test_each_agent_declares_the_model_and_effort_its_name_promises(self):
        generator = self.generator()
        agents = Path(__file__).resolve().parents[1] / "agents"
        problems = [
            problem
            for path in sorted(agents.glob("*.md"))
            for problem in generator.contract_errors(path.name, path.read_text())
        ]
        self.assertEqual(problems, [])

    def test_the_floor_profile_can_spawn_an_assessor(self):
        # The assessor opens the run, so a floor-profile account that cannot
        # spawn one is an account Symphony can never govern at all.
        agents = Path(__file__).resolve().parents[1] / "agents"
        floor = profiles_for("claude")[-1]
        expected = f"symphony-assessor-{floor['tiers']['strongest']}-high.md"
        self.assertTrue((agents / expected).is_file(), f"{expected} is missing")


class ClampGateTests(unittest.TestCase):
    """A weaker model must not be substituted silently; a weaker effort may be."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.state_root = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def environ(self, profile):
        return {"SYMPHONY_STATE_DIR": str(self.state_root), "SYMPHONY_PROFILE": profile}

    def payload(self, **extra):
        return {
            "session_id": "codex-session",
            "cwd": str(self.project),
            "turn_id": "turn-1",
            "model": "codex-model",
            **extra,
        }

    def send(self, environ, **extra):
        return handle(self.payload(**extra), environ)

    def output(self, result):
        return json.loads(result.stdout) if result.stdout else {}

    # Each profile's strongest tier, which a small/complex route selects.
    STRONGEST = {"full": "gpt-6-astra", "base": "gpt-5.5"}

    def open_and_spawn_lead(self, profile, size="small", complexity="complex"):
        environ = self.environ(profile)
        self.send(environ, hook_event_name="SessionStart")
        self.send(
            environ,
            hook_event_name="PreToolUse",
            tool_name="spawn_agent",
            tool_input={
                "message": "SYMPHONY_ROLE: assessor\nShip it",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            },
        )
        marker = json.dumps(
            {"size": size, "complexity": complexity, "risk": "normal", "rationale": "x", "topology": "direct"}
        )
        return environ, self.send(
            environ,
            hook_event_name="PreToolUse",
            tool_name="spawn_agent",
            tool_input={
                "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                "model": self.STRONGEST[profile],
                "reasoning_effort": "high",
            },
        )

    def test_a_fully_entitled_account_is_never_gated(self):
        _, result = self.open_and_spawn_lead("full")
        self.assertNotEqual(self.output(result).get("decision"), "block")

    def test_a_tier_clamp_blocks_and_names_the_control(self):
        _, result = self.open_and_spawn_lead("base")
        output = self.output(result)
        self.assertEqual(output.get("decision"), "block")
        self.assertIn("gpt-5.5", output["reason"])
        self.assertIn("gpt-6-astra", output["reason"])
        self.assertIn("$symphony:symphony proceed", output["reason"])

    def test_accepting_the_clamp_unblocks_the_rest_of_the_session(self):
        environ, blocked = self.open_and_spawn_lead("base")
        self.assertEqual(self.output(blocked).get("decision"), "block")

        accepted = self.send(
            environ, hook_event_name="UserPromptSubmit", prompt="$symphony:symphony proceed"
        )
        self.assertIn("accepted", self.output(accepted)["hookSpecificOutput"]["additionalContext"].lower())

        marker = json.dumps(
            {"size": "small", "complexity": "complex", "risk": "normal", "rationale": "x", "topology": "direct"}
        )
        retried = self.send(
            environ,
            hook_event_name="PreToolUse",
            tool_name="spawn_agent",
            tool_input={
                "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                "model": "gpt-5.5",
                "reasoning_effort": "high",
            },
        )
        self.assertNotIn("decision", self.output(retried))

    def test_acceptance_does_not_survive_into_a_new_session(self):
        environ, _ = self.open_and_spawn_lead("base")
        self.send(environ, hook_event_name="UserPromptSubmit", prompt="$symphony:symphony proceed")

        store = StateStore(self.state_root)
        self.assertEqual(store.load(self.project).activation["codex"]["accepted_profile"], "base")

        handle(
            {**self.payload(session_id="codex-session-2"), "hook_event_name": "SessionStart"},
            environ,
        )
        self.assertFalse(
            store.load(self.project).activation["codex"].get("accepted_profile"),
            "a clamp accepted in one session must be re-asked in the next",
        )

    def test_proceed_without_a_clamped_route_says_so(self):
        environ = self.environ("full")
        result = self.send(
            environ, hook_event_name="UserPromptSubmit", prompt="$symphony:symphony proceed"
        )
        self.assertIn("no clamped route", self.output(result)["hookSpecificOutput"]["additionalContext"])
