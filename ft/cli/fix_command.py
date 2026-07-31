"""Application service for ``ft fix``.

Argument parsing and cycle selection stay in ``ft.cli.main``. This module owns
the correction workflow once a runner has been selected.
"""

from __future__ import annotations

from pathlib import Path
import py_compile
import re
from typing import Any

from ft.engine import ui
from ft.engine.llm_logs import activate_external_llm_log, clear_external_llm_log


DEFAULT_FIX_PATHS = (
    "project/",
    "src/",
    "tests/",
    "docs/",
    "main.py",
    "app.py",
    "server.py",
    "frontend/",
    ".ft/process/",
)


def single_fix_target_path(instruction: str, root: Path) -> str | None:
    """Return the only existing, safe project path cited by an instruction."""
    candidates: list[str] = []
    pattern = (
        r"(?<![A-Za-z0-9_.])"
        r"((?:project|src|tests|docs|\.ft/process)/"
        r"(?:[A-Za-z0-9_.@%+=-]+/)*[A-Za-z0-9_.@%+=-]+)"
    )
    resolved_root = root.resolve()
    for match in re.finditer(pattern, instruction):
        relative = match.group(1).strip().strip("'\"`.,;:)")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            continue
        target = (resolved_root / path).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:
            continue
        if target.is_file():
            candidates.append(path.as_posix())
    unique = sorted(set(candidates))
    return unique[0] if len(unique) == 1 else None


def validate_fix_capture(runner: Any, capture_path: str | None) -> None:
    """Validate captured Python without replacing it with engine-owned code."""
    if not capture_path:
        return
    root = Path(getattr(runner, "_work_dir", runner.project_root))
    target = root / capture_path
    if target.is_file() and target.suffix == ".py":
        py_compile.compile(str(target), doraise=True)


def _capture_scope(
    runner: Any,
    state: Any,
    instruction: str,
    engine: str,
) -> tuple[Any | None, list[str], str | None]:
    node = None
    if state and state.current_node and state.current_node in runner.graph.nodes:
        node = runner.graph.get_node(state.current_node)

    allowed_paths = list(DEFAULT_FIX_PATHS)
    capture_path: str | None = None
    if engine == "opencode" and node is not None:
        outputs = [
            str(output)
            for output in getattr(node, "outputs", [])
            if not str(output).endswith("/")
        ]
        if (
            getattr(node, "type", None) in {"discovery", "document", "retro"}
            and len(outputs) == 1
        ):
            capture_path = outputs[0]
    elif engine == "opencode":
        capture_path = single_fix_target_path(
            instruction,
            Path(runner.project_root),
        )
    if capture_path:
        allowed_paths = [capture_path]
    return node, allowed_paths, capture_path


def _append_capture_context(prompt: str, root: Path, capture_path: str | None) -> str:
    if not capture_path:
        return prompt
    target = root / capture_path
    if not target.is_file():
        return prompt
    current = target.read_text(encoding="utf-8", errors="ignore")
    return (
        f"{prompt}\n\nARQUIVO ALVO: {capture_path}\n"
        "CONTEUDO ATUAL ENTRE MARCADORES:\n"
        "<<<FT_CURRENT_FILE>>>\n"
        f"{current.rstrip()}\n"
        "<<<FT_END_CURRENT_FILE>>>\n\n"
        "Retorne o conteudo completo atualizado desse unico arquivo. "
        "Nao retorne diff, explicacao, markdown fence ou DONE."
    )


def execute_fix(args: Any, runner: Any) -> None:
    """Apply one directed correction to the already selected cycle."""
    instruction = args.instruction
    state = runner.state_mgr.load()
    if state.pending_approval:
        gate = runner.graph.get_node(state.pending_approval)
        if not gate.reject_next:
            print(
                "ERRO: o human gate atual não declara reject_next; "
                "a engine recusou aplicar um fix sem rota de revisão."
            )
            return
        applied = runner.reject_with_origin_audit(instruction)
        if not applied:
            print(
                "ERRO: a engine não encontrou uma auditoria segura para este "
                "human gate; nenhum fix avulso foi executado."
            )
            return
    else:
        # Auditoria da origem é obrigatória. ``--audit-origin`` continua
        # aceito apenas por compatibilidade com scripts existentes.
        applied = runner.apply_fix(instruction, audit_origin=True)
    if applied:
        runner.run(mode="mvp" if getattr(args, "auto", False) else "step")
        return

    from ft.engine.delegate import delegate_to_llm

    root = Path(runner.project_root)
    state_path = runner.state_mgr.path
    blocked_context = ""
    if state_path.exists():
        loaded = runner.state_mgr.load()
        if loaded.blocked_reason:
            blocked_context = (
                f"\n\nCONTEXTO: O processo parou no node "
                f"'{loaded.current_node}' com o erro:\n"
                f"{loaded.blocked_reason}\n"
            )

    prompt = (
        "O usuário pediu a seguinte correção:\n\n"
        f"{instruction}\n"
        f"{blocked_context}\n"
        "Analise o problema, faça as alterações necessárias nos arquivos do "
        "projeto, e diga DONE quando terminar."
    )

    state = runner.state_mgr.load()
    fix_node = (
        runner.graph.get_node(state.current_node)
        if state and state.current_node and state.current_node in runner.graph.nodes
        else None
    )
    selection = runner._capture_delegation_llm_selection(state, node=fix_node)
    node, allowed_paths, capture_path = _capture_scope(
        runner,
        state,
        instruction,
        selection.engine,
    )
    prompt = _append_capture_context(prompt, root, capture_path)
    if hasattr(runner, "_inject_execution_plan"):
        prompt = runner._inject_execution_plan(prompt)

    print(ui.info(f"Aplicando correção: {instruction}"))
    log_path = activate_external_llm_log(
        runner.state_mgr,
        root,
        state.current_node if state and state.current_node else "fix",
        "fix",
        engine=selection.engine,
    )
    kwargs: dict[str, Any] = {
        "task": prompt,
        "project_root": str(root),
        "allowed_paths": allowed_paths,
        "llm_engine": selection.engine,
        "llm_model": selection.model,
        "llm_effort": selection.effort,
        "log_path": str(log_path),
    }
    if capture_path:
        kwargs["opencode_capture_output_path"] = capture_path
    if hasattr(runner, "_attach_llm_session"):
        runner._attach_llm_session(
            kwargs,
            node=fix_node,
            selection=selection,
        )
    try:
        if (
            "_ft_session_context" in kwargs
            and hasattr(runner, "_delegate_once_with_attached_session")
        ):
            result = runner._delegate_once_with_attached_session(
                delegate_to_llm,
                kwargs,
            )
        else:
            result = delegate_to_llm(**kwargs)
    finally:
        clear_external_llm_log(
            runner.state_mgr,
            expected_path=log_path,
            project_root=root,
        )

    if not result.success:
        print(ui.fail(f"LLM não conseguiu aplicar: {result.output[:300]}"))
        return

    if selection.engine == "opencode" and capture_path:
        try:
            validate_fix_capture(runner, capture_path)
        except Exception as exc:
            print(ui.fail(f"Correção aplicada, mas artefato capturado é inválido: {exc}"))
            return

    print(ui.success("Correção aplicada"))
    state = runner.state_mgr.load()
    if state.node_status not in {"blocked", "ready"}:
        print(ui.info("Para continuar o processo: ft continue --auto"))
        return

    node_id = state.current_node
    node = (
        runner.graph.get_node(node_id)
        if node_id and node_id in runner.graph.nodes
        else node
    )
    if node is not None:
        from ft.engine.runner import run_validators

        print(ui.info("Validando correção..."))
        validation = run_validators(
            node,
            runner.project_root,
            state_dir=str(runner.state_mgr.path.parent),
            work_dir=runner._run_dir,
        )
        runner._print_validation(validation)
        if validation.passed:
            for output_path in node.outputs:
                runner.state_mgr.record_artifact(Path(output_path).stem, output_path)
            runner._maybe_auto_commit(node)
            runner._record_node_summary(
                node,
                "NODE_SUMMARY:\n"
                "- fiz: correção via ft fix\n"
                "- verificado: validators do node passaram\n"
                f"- instrução: {instruction}",
            )
            if node.requires_approval and not runner._auto_approve:
                fixed_state = runner.state_mgr.load()
                fixed_state.node_status = "ready"
                fixed_state.blocked_reason = None
                runner.state_mgr.save()
                print(ui.awaiting_approval(auto=runner._auto_approve))
                runner.state_mgr.set_pending_approval(node.id)
                return

            next_id = runner.graph.resolve_next(node.id)
            runner._advance_state(node.id, next_id)
            print(ui.step_pass(next_id))
            if getattr(args, "auto", False):
                runner.run(mode="mvp")
            return
        feedback = validation.feedback or "validação falhou"
        failed_state = runner.state_mgr.load()
        failed_state.node_status = "blocked"
        failed_state.blocked_reason = (
            "Correção dirigida não passou nos validators do node: "
            f"{feedback}"
        )
        runner.state_mgr.save()
        print(
            ui.warn(
                "Correção aplicada, mas validators ainda falham; "
                "o node permaneceu bloqueado sem nova delegação LLM."
            )
        )
        return

    print(ui.warn("Correção aplicada, mas o node atual não pôde ser resolvido."))
