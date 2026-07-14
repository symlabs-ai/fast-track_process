from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import threading
import time

import pytest
import yaml

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY, run_validators


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "tweak"
PROCESS = TEMPLATE / "process.yml"
VALIDATOR = TEMPLATE / "scripts" / "validate_tweak.py"
PRODUCT_HELPER = TEMPLATE / "scripts" / "product.sh"
SERVE_HELPER = TEMPLATE / "scripts" / "serve.sh"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True)


def _project(tmp_path: Path, request: str = "Mude a cor do botão Salvar para azul.") -> Path:
    root = tmp_path
    scripts = root / ".ft" / "process" / "tweak" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(VALIDATOR, scripts / "validate_tweak.py")
    shutil.copy2(PRODUCT_HELPER, scripts / "product.sh")
    _write(
        root,
        ".ft/manifest.yml",
        "schema_version: 2\ndefault_process: mvp-builder\n",
    )
    _write(
        root,
        "project/Makefile",
        "build:\n"
        "\t@printf 'build\\n' >> ../.git/validation-calls.log\n"
        "\t@if test \"$${MUTATE_ON_BUILD:-0}\" = 1; then "
        "printf '/* generated */\\n' >> button.css; fi\n"
        "\t@if test \"$${BACKGROUND_ON_BUILD:-0}\" = 1; then "
        "(trap '' TERM; sleep 0.3; printf '/* late build */\\n' >> button.css) & fi\n\n"
        "\t@if test \"$${DETACHED_ON_BUILD:-0}\" = 1; then "
        "setsid sh -c 'trap \"\" TERM; sleep 0.3; "
        "printf \"/* late detached build */\\\\n\" >> button.css' "
        "</dev/null >/dev/null 2>&1 & fi\n\n"
        "run:\n\t@true\n\n"
        "url:\n\t@echo http://127.0.0.1:8021\n",
    )
    _write(root, "project/button.css", ".save-button { color: red; }\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "baseline",
    )
    _write(root, "docs/feature-request.md", request + "\n")
    return root


def _run_validator(
    root: Path, command: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(root / ".ft/process/tweak/scripts/validate_tweak.py"),
            command,
            *arguments,
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )


def _preflight(root: Path) -> None:
    result = _run_validator(root, "preflight")
    assert result.returncode == 0, result.stderr
    result = _run_validator(root, "begin")
    assert result.returncode == 0, result.stderr


def _call_focal(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(root / ".ft/process/tweak/scripts/product.sh"),
            "focal",
            "--",
            *argv,
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )


def _run_focal(root: Path, *argv: str) -> str:
    result = _call_focal(root, *argv)
    assert result.returncode == 0, result.stderr
    return shlex.join(argv)


def _report(root: Path, changed_paths: list[str], focal_command: str) -> None:
    listed = "\n".join(f"- {path}" for path in changed_paths)
    _write(
        root,
        "docs/tweak-report.md",
        "# Tweak\n\n"
        "Resultado: IMPLEMENTED\n\n"
        "## Arquivos alterados\n\n"
        f"{listed}\n\n"
        "Validação focal: PASS\n"
        f"Comando focal: {focal_command}\n"
        "Risco residual: nenhum conhecido\n",
    )


def test_tweak_graph_has_one_bounded_llm_node_and_no_heavy_phases():
    validate_template_is_pristine(TEMPLATE)
    assert "tweak" in available_templates(entrypoint="feature")
    assert "tweak" not in available_templates(entrypoint="init")

    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert list(graph.nodes) == [
        "tweak.preflight",
        "tweak.implement",
        "tweak.acceptance",
        "tweak.end",
    ]
    delegated = [
        node for node in graph.nodes.values() if node.executor.startswith("llm_")
    ]
    assert len(delegated) == 1
    assert delegated[0].id == "tweak.implement"
    assert delegated[0].context_profile == "tweak.direct"
    assert delegated[0].max_turns == 12
    assert delegated[0].llm_timeout_seconds == 600
    assert delegated[0].env_setup == [
        "python .ft/process/tweak/scripts/validate_tweak.py begin"
    ]
    assert not {
        "discovery",
        "document",
        "review",
        "test_red",
        "test_green",
        "refactor",
    } & {node.type for node in graph.nodes.values()}
    assert graph.nodes["tweak.acceptance"].reject_next == "tweak.implement"

    implementation_command = graph.nodes["tweak.implement"].validators[-1][
        "command_succeeds"
    ]["command"]
    assert "validate_tweak.py implementation" in implementation_command
    assert implementation_command.count("validate_tweak.py implementation") == 2
    assert "product.sh quick" in implementation_command
    assert "product.sh full" not in implementation_command
    assert "product.sh test" not in implementation_command
    assert "e2e" not in implementation_command.lower()

    assert graph.meta["close_policy"] == {
        "backlog": {"mode": "none"},
        "merge": "full",
    }
    canonical = graph.meta["artifact_policy"]["canonical"]
    assert "docs/PROJECT_BACKLOG.md" not in canonical
    assert "docs/FEATURES.md" not in canonical


def test_tweak_environment_disables_every_automatic_retry():
    environment = yaml.safe_load((TEMPLATE / "environment.yml").read_text())

    assert environment == {
        "run_mode": "isolated",
        "max_node_retries": 0,
        "max_gate_retries": 0,
        "max_auto_fix": 0,
    }


def test_tweak_preflight_accepts_a_small_color_change(tmp_path):
    root = _project(tmp_path)

    result = _run_validator(root, "preflight")

    assert result.returncode == 0, result.stderr
    baseline = yaml.safe_load((root / "docs/tweak-baseline.yml").read_text())
    assert baseline["kind"] == "ft.tweak.baseline"
    assert baseline["classification"] == "visual"
    assert baseline["max_files"] == 4
    assert baseline["max_changed_lines"] == 160
    assert baseline["max_file_bytes"] == 256_000
    assert baseline["max_patch_bytes"] == 256_000
    assert (root / "state/tweak-guard.json").is_file()


@pytest.mark.parametrize(
    "demand",
    [
        "Altere a cor do botão Salvar para azul.",
        "Coloque o botão Salvar em azul.",
        "Deixe o botão Salvar azul.",
        "Ajustar o texto do label para Confirmar.",
        "Corrigir o texto do botão Salvar.",
        "Renomeie o label Salvar para Confirmar.",
        "Update the Save button color to blue.",
        "Set the Save button color to blue.",
        "Rename the Save button label to Confirm.",
    ],
)
def test_tweak_preflight_accepts_natural_focal_wording(tmp_path, demand):
    root = _project(tmp_path, request=demand)

    result = _run_validator(root, "preflight")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "demand",
    [
        "Melhore isso.",
        "Corrija o bug.",
        "Corrija o fluxo de login.",
        "Ajuste as permissões.",
        "Ajuste a infra.",
        "Atualize credenciais.",
    ],
)
def test_tweak_preflight_rejects_ambiguous_or_sensitive_wording(tmp_path, demand):
    root = _project(tmp_path, request=demand)

    result = _run_validator(root, "preflight")

    assert result.returncode == 1
    assert "--template feature" in result.stderr


@pytest.mark.parametrize(
    "demand, marker",
    [
        ("Migre o schema do banco e crie uma tabela.", "banco, schema"),
        ("Crie um endpoint novo na API.", "API ou contrato"),
        ("Atualize a dependência do frontend.", "dependências"),
        ("Refatore a autenticação e as permissões.", "autenticação"),
    ],
)
def test_tweak_preflight_rejects_risky_scope(tmp_path, demand, marker):
    root = _project(tmp_path, request=demand)

    result = _run_validator(root, "preflight")

    assert result.returncode == 1
    assert marker.lower() in result.stderr.lower()
    assert "--template feature" in result.stderr
    assert not (root / "docs/tweak-baseline.yml").exists()


def test_tweak_implementation_accepts_one_small_documented_diff(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 0, result.stderr
    assert "1 file(s), 2 changed line(s)" in result.stdout
    receipt = json.loads((root / "state/tweak-focal.json").read_text())
    assert receipt["consumed"] is True
    assert receipt["count"] == 1


def test_tweak_implementation_gate_runs_guard_then_one_quick_build(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)
    node = load_graph(PROCESS).get_node("tweak.implement")

    result = run_validators(
        node,
        str(root),
        work_dir=str(root),
    )

    assert result.passed, result.feedback
    assert (root / ".git/validation-calls.log").read_text(encoding="utf-8") == "build\n"


def test_tweak_implementation_rejects_claimed_focal_pass_without_receipt(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "state/tweak-focal.json" in result.stderr


def test_tweak_implementation_rejects_two_focal_commands_in_one_attempt(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "exatamente um comando focal" in result.stderr


def test_tweak_failed_focal_can_be_replaced_by_one_passing_check(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")

    failed = _call_focal(root, sys.executable, "-c", "raise SystemExit(7)")
    assert failed.returncode == 1
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 0, result.stderr
    receipt = json.loads((root / "state/tweak-focal.json").read_text())
    assert receipt["count"] == 1


def test_tweak_failed_check_does_not_erase_an_earlier_passing_check(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    _run_focal(root, sys.executable, "-c", "pass")
    failed = _call_focal(root, sys.executable, "-c", "raise SystemExit(7)")
    assert failed.returncode == 1
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "exatamente um comando focal" in result.stderr


def test_tweak_new_attempt_resets_focal_counter_after_block(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    _run_focal(root, sys.executable, "-c", "pass")
    _run_focal(root, sys.executable, "-c", "pass")

    begin = _run_validator(root, "begin")
    assert begin.returncode == 0, begin.stderr
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 0, result.stderr


def test_tweak_focal_kills_delayed_background_writer_before_receipt(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    expected = ".save-button { color: blue; }\n"
    _write(root, changed[0], expected)

    command = _run_focal(
        root,
        "bash",
        "-c",
        "(trap '' TERM; sleep 0.3; printf '/* late */\\n' >> button.css) &",
    )
    time.sleep(0.5)
    _report(root, changed, command)

    assert (root / changed[0]).read_text() == expected
    result = _run_validator(root, "implementation")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requer subreaper Linux")
def test_tweak_focal_kills_detached_writer_before_receipt(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    expected = ".save-button { color: blue; }\n"
    _write(root, changed[0], expected)

    command = _run_focal(
        root,
        "bash",
        "-c",
        "setsid sh -c \"trap '' TERM; sleep 0.3; "
        "printf '/* late detached */\\n' >> button.css\" "
        "</dev/null >/dev/null 2>&1 &",
    )
    time.sleep(0.5)
    _report(root, changed, command)

    assert (root / changed[0]).read_text() == expected
    result = _run_validator(root, "implementation")
    assert result.returncode == 0, result.stderr


def test_tweak_implementation_rejects_product_change_after_focal_command(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    _write(root, changed[0], ".save-button { color: navy; }\n")
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "produto mudou depois" in result.stderr


def test_tweak_report_accepts_equivalent_shell_quoting_for_focal_command(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    _run_focal(root, "/bin/echo", "button color")
    _report(root, changed, '/bin/echo "button color"')

    result = _run_validator(root, "implementation")

    assert result.returncode == 0, result.stderr


def test_tweak_implementation_rejects_mode_change_after_focal_command(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    os.chmod(root / changed[0], 0o600)
    _report(root, changed, command)

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "produto mudou depois" in result.stderr


def test_tweak_gate_revalidates_after_quick_build(monkeypatch, tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)
    monkeypatch.setenv("MUTATE_ON_BUILD", "1")
    node = load_graph(PROCESS).get_node("tweak.implement")

    result = run_validators(node, str(root), work_dir=str(root))

    assert not result.passed
    assert "produto mudou depois" in result.feedback


def test_tweak_quick_build_kills_delayed_background_writer(monkeypatch, tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    expected = ".save-button { color: blue; }\n"
    _write(root, changed[0], expected)
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)
    monkeypatch.setenv("BACKGROUND_ON_BUILD", "1")
    node = load_graph(PROCESS).get_node("tweak.implement")

    result = run_validators(node, str(root), work_dir=str(root))
    time.sleep(0.5)

    assert result.passed, result.feedback
    assert (root / changed[0]).read_text() == expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requer subreaper Linux")
def test_tweak_quick_build_kills_detached_writer(monkeypatch, tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/button.css"]
    expected = ".save-button { color: blue; }\n"
    _write(root, changed[0], expected)
    command = _run_focal(root, sys.executable, "-c", "pass")
    _report(root, changed, command)
    monkeypatch.setenv("DETACHED_ON_BUILD", "1")
    node = load_graph(PROCESS).get_node("tweak.implement")

    result = run_validators(node, str(root), work_dir=str(root))
    time.sleep(0.5)

    assert result.passed, result.feedback
    assert (root / changed[0]).read_text() == expected


def test_tweak_implementation_rejects_more_than_four_files(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = [f"project/tweak-{index}.css" for index in range(5)]
    for path in changed:
        _write(root, path, ".x { color: blue; }\n")
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "5 arquivos" in result.stderr
    assert "limite do tweak é 4" in result.stderr


def test_tweak_implementation_rejects_more_than_160_changed_lines(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/oversized.css"]
    _write(root, changed[0], "\n".join(f".x-{index} {{ color: blue; }}" for index in range(161)) + "\n")
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "161 linhas" in result.stderr
    assert "limite do tweak é 160" in result.stderr


def test_tweak_implementation_rejects_large_single_line_file(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/oversized.css"]
    _write(root, changed[0], "x" * 256_001)
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "256001 bytes" in result.stderr
    assert "limite do tweak é 256000" in result.stderr


def test_tweak_implementation_rejects_patch_larger_than_byte_budget(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/large-a.css", "project/large-b.css"]
    for path in changed:
        _write(root, path, "x" * 140_000)
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "patch possui" in result.stderr
    assert "limite do tweak é 256000" in result.stderr


def test_tweak_implementation_rejects_sensitive_path(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    changed = ["project/auth/button.css"]
    _write(root, changed[0], ".button { color: blue; }\n")
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "path proibido" in result.stderr
    assert "project/auth/button.css" in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "project/auth.py",
        "project/api.ts",
        "project/security.ts",
        "project/components/auth-button.tsx",
    ],
)
def test_tweak_implementation_rejects_sensitive_filename_stems(tmp_path, relative):
    root = _project(tmp_path)
    _preflight(root)
    _write(root, relative, "export const value = true;\n")
    _report(root, [relative], "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "path proibido" in result.stderr
    assert relative in result.stderr


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_tweak_implementation_rejects_hidden_git_index_entries(tmp_path, flag):
    root = _project(tmp_path)
    _preflight(root)
    _git(root, "update-index", flag, "project/button.css")
    _report(root, ["project/button.css"], "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "índice Git contém flags/estados" in result.stderr


def test_tweak_escalation_without_product_diff_has_actionable_error(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    _write(root, "docs/tweak-report.md", "Resultado: ESCALATE\n")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert "implementação declarou ESCALATE" in result.stderr
    assert "--template feature" in result.stderr


@pytest.mark.parametrize("target", ["request", "baseline", "guard"])
def test_tweak_implementation_rejects_guarded_artifact_tampering(tmp_path, target):
    root = _project(tmp_path)
    _preflight(root)
    if target == "request":
        path = root / "docs/feature-request.md"
        path.write_text(path.read_text() + "Outra mudança.\n", encoding="utf-8")
    elif target == "baseline":
        path = root / "docs/tweak-baseline.yml"
        path.write_text(path.read_text() + "# alterado\n", encoding="utf-8")
    else:
        path = root / "state/tweak-guard.json"
        payload = json.loads(path.read_text())
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
    changed = ["project/button.css"]
    _write(root, changed[0], ".save-button { color: blue; }\n")
    _report(root, changed, "true")

    result = _run_validator(root, "implementation")

    assert result.returncode == 1
    assert any(
        marker in result.stderr
        for marker in ("demanda original", "baseline do tweak", "guard interno")
    )


def test_tweak_quick_build_neutralizes_inherited_makeflags(tmp_path):
    root = _project(tmp_path)
    _preflight(root)
    environment = os.environ.copy()
    environment.update(
        {
            "MAKEFLAGS": "-n -i",
            "MFLAGS": "-n",
            "GNUMAKEFLAGS": "-n",
        }
    )

    result = subprocess.run(
        ["bash", str(root / ".ft/process/tweak/scripts/product.sh"), "quick"],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (root / ".git/validation-calls.log").read_text(encoding="utf-8") == "build\n"


def _serve_project(tmp_path: Path) -> tuple[Path, int]:
    root = _project(tmp_path)
    scripts = root / ".ft/process/tweak/scripts"
    shutil.copy2(SERVE_HELPER, scripts / "serve.sh")
    _write(
        root,
        "project/Makefile",
        "build:\n\t@true\n\n"
        "run:\n\t@python3 mock_server.py\n\n"
        "url:\n\t@echo http://127.0.0.1:$(PORT)\n",
    )
    _write(
        root,
        "project/mock_server.py",
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "port = int(os.environ['PORT'])\n"
        "base = int(os.environ['TEST_BASE_PORT'])\n"
        "with Path('../attempted-ports.log').open('a') as handle:\n"
        "    handle.write(f'{port}\\n')\n"
        "if port == base:\n"
        "    if os.environ['FIRST_MODE'] == 'collision':\n"
        "        print('OSError: [Errno 98] Address already in use', file=sys.stderr)\n"
        "    else:\n"
        "        print('invalid application configuration', file=sys.stderr)\n"
        "    raise SystemExit(1)\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200 if self.path == '/health' else 404)\n"
        "        self.end_headers()\n"
        "    def log_message(self, _format, *args):\n"
        "        pass\n\n"
        "HTTPServer(('127.0.0.1', port), Handler).serve_forever()\n",
    )

    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    second_port = reservation.getsockname()[1]
    reservation.close()
    return root, second_port - 1


def _run_serve(
    root: Path,
    base_port: int,
    mode: str,
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PORT": str(base_port),
            "TEST_BASE_PORT": str(base_port),
            "FIRST_MODE": mode,
        }
    )
    environment.update(extra_environment or {})
    return subprocess.run(
        ["bash", str(root / ".ft/process/tweak/scripts/serve.sh")],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )


def test_tweak_serve_retries_by_starting_next_port_after_real_collision(tmp_path):
    root, base_port = _serve_project(tmp_path)

    try:
        result = _run_serve(root, base_port, "collision")

        assert result.returncode == 0, result.stderr
        assert (root / "attempted-ports.log").read_text().splitlines() == [
            str(base_port),
            str(base_port + 1),
        ]
        assert (root / ".serve_url").read_text().strip().endswith(
            f":{base_port + 1}"
        )
        assert "port_is_free" not in SERVE_HELPER.read_text(encoding="utf-8")
    finally:
        subprocess.run(
            ["bash", str(root / ".ft/process/tweak/scripts/serve.sh"), "stop"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )


def test_tweak_serve_does_not_retry_a_non_collision_startup_failure(tmp_path):
    root, base_port = _serve_project(tmp_path)

    result = _run_serve(root, base_port, "fatal")

    assert result.returncode == 1
    assert "invalid application configuration" in result.stderr
    assert (root / "attempted-ports.log").read_text().splitlines() == [str(base_port)]
    assert not (root / ".serve.pid").exists()


def test_tweak_serve_does_not_accept_health_from_an_unowned_listener(tmp_path):
    root, _unused_base = _serve_project(tmp_path)

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, _format, *args):
            pass

    occupied = HTTPServer(("127.0.0.1", 0), HealthHandler)
    candidate_port = int(occupied.server_address[1])
    occupied_thread = threading.Thread(target=occupied.serve_forever, daemon=True)
    occupied_thread.start()

    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    ignored_port = int(reservation.getsockname()[1])
    reservation.close()
    _write(
        root,
        "project/mock_server.py",
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "import threading\n"
        "import time\n\n"
        "port = int(os.environ['IGNORED_PORT'])\n"
        "with open('../attempted-ports.log', 'a') as handle:\n"
        "    handle.write(os.environ['PORT'] + '\\n')\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200 if self.path == '/health' else 404)\n"
        "        self.end_headers()\n"
        "    def log_message(self, _format, *args):\n"
        "        pass\n\n"
        "server = HTTPServer(('127.0.0.1', port), Handler)\n"
        "threading.Thread(target=server.serve_forever, daemon=True).start()\n"
        "time.sleep(0.75)\n",
    )

    try:
        result = _run_serve(
            root,
            candidate_port,
            "fatal",
            {"IGNORED_PORT": str(ignored_port)},
        )

        assert result.returncode == 1
        assert (root / "attempted-ports.log").read_text().splitlines() == [
            str(candidate_port)
        ]
        assert not (root / ".serve.pid").exists()
        assert not (root / ".serve_url").exists()
    finally:
        occupied.shutdown()
        occupied.server_close()
        occupied_thread.join(timeout=2)


@pytest.mark.parametrize("control_path", [".serve_url", ".serve.pid", ".serve.log"])
def test_tweak_serve_refuses_control_file_symlinks(tmp_path, control_path):
    root, base_port = _serve_project(tmp_path)
    victim = root / "victim.txt"
    victim.write_text("preserve me\n", encoding="utf-8")
    (root / control_path).symlink_to(victim)

    result = _run_serve(root, base_port, "fatal")

    assert result.returncode == 1
    assert "não pode ser symlink" in result.stderr
    assert (root / control_path).is_symlink()
    assert victim.read_text(encoding="utf-8") == "preserve me\n"
    assert not (root / "attempted-ports.log").exists()
