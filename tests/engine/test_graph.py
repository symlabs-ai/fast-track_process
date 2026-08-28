"""Unit tests for ft.engine.graph."""

import pytest

from ft.engine.graph import Node, ProcessGraph, load_graph

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_graph(nodes_data: list[dict]) -> ProcessGraph:
    """Helper: build a ProcessGraph from raw node dicts."""
    nodes = []
    for n in nodes_data:
        nodes.append(
            Node(
                id=n["id"],
                type=n.get("type", "build"),
                title=n.get("title", n["id"]),
                executor=n.get("executor", "python"),
                outputs=n.get("outputs", []),
                write_scope=n.get("write_scope", []),
                next=n.get("next"),
                sprint=n.get("sprint"),
                parallel_group=n.get("parallel_group"),
                branches=n.get("branches"),
                condition=n.get("condition"),
                reject_next=n.get("reject_next"),
                fix_review=n.get("fix_review"),
            )
        )
    return ProcessGraph(nodes, {"id": "test", "title": "Test"})


def simple_graph() -> ProcessGraph:
    return make_graph(
        [
            {"id": "a", "type": "build", "next": "b"},
            {"id": "b", "type": "gate", "next": "c"},
            {"id": "c", "type": "end"},
        ]
    )


# ---------------------------------------------------------------------------
# ProcessGraph — resolve_next
# ---------------------------------------------------------------------------


class TestResolveNext:
    def test_linear_chain(self):
        g = simple_graph()
        assert g.resolve_next("a") == "b"
        assert g.resolve_next("b") == "c"
        assert g.resolve_next("c") is None

    def test_end_node_returns_none(self):
        g = simple_graph()
        assert g.resolve_next("c") is None

    def test_decision_with_matching_branch(self):
        g = make_graph(
            [
                {
                    "id": "d",
                    "type": "decision",
                    "condition": "status",
                    "branches": {"pass": "ok", "fail": "err"},
                    "next": "ok",
                },
                {"id": "ok", "type": "build", "next": "done"},
                {"id": "err", "type": "build", "next": "done"},
                {"id": "done", "type": "end"},
            ]
        )
        assert g.resolve_next("d", {"status": "pass"}) == "ok"
        assert g.resolve_next("d", {"status": "fail"}) == "err"

    def test_decision_fallback_to_next(self):
        g = make_graph(
            [
                {
                    "id": "d",
                    "type": "decision",
                    "condition": "status",
                    "branches": {"pass": "ok"},
                    "next": "fallback",
                },
                {"id": "ok", "type": "build", "next": "done"},
                {"id": "fallback", "type": "build", "next": "done"},
                {"id": "done", "type": "end"},
            ]
        )
        assert g.resolve_next("d", {"status": "unknown"}) == "fallback"


# ---------------------------------------------------------------------------
# ProcessGraph — get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_first_node_is_ready(self):
        g = simple_graph()
        status = g.get_status(set())
        assert status["a"] == "ready"
        assert status["b"] == "blocked"
        assert status["c"] == "blocked"

    def test_after_completing_a_b_is_ready(self):
        g = simple_graph()
        status = g.get_status({"a"})
        assert status["a"] == "done"
        assert status["b"] == "ready"
        assert status["c"] == "blocked"

    def test_all_done(self):
        g = simple_graph()
        status = g.get_status({"a", "b", "c"})
        assert all(s == "done" for s in status.values())


# ---------------------------------------------------------------------------
# ProcessGraph — sprint helpers
# ---------------------------------------------------------------------------


class TestSprintHelpers:
    def setup_method(self):
        self.g = make_graph(
            [
                {"id": "a", "sprint": "s1", "next": "b"},
                {"id": "b", "sprint": "s1", "next": "c"},
                {"id": "c", "sprint": "s2", "next": "d"},
                {"id": "d", "type": "end"},
            ]
        )

    def test_get_sprints(self):
        assert self.g.get_sprints() == ["s1", "s2"]

    def test_get_sprint_nodes(self):
        s1 = self.g.get_sprint_nodes("s1")
        assert [n.id for n in s1] == ["a", "b"]

    def test_sprint_of(self):
        assert self.g.sprint_of("a") == "s1"
        assert self.g.sprint_of("c") == "s2"
        assert self.g.sprint_of("d") is None


# ---------------------------------------------------------------------------
# ProcessGraph — validation
# ---------------------------------------------------------------------------


class TestGraphValidation:
    def test_codex_auth_requires_known_route_and_codex_executor(self):
        graph = ProcessGraph(
            [
                Node(
                    id="image",
                    type="build",
                    title="Image",
                    executor="llm_codex",
                    codex_auth="chatgpt",
                    next="done",
                ),
                Node(id="done", type="end", title="Done"),
            ],
            {"id": "test"},
        )
        assert graph.get_node("image").codex_auth == "chatgpt"

        with pytest.raises(ValueError, match="codex_auth invalido"):
            ProcessGraph(
                [
                    Node(
                        id="image",
                        type="build",
                        title="Image",
                        executor="llm_codex",
                        codex_auth="api",
                        next="done",
                    ),
                    Node(id="done", type="end", title="Done"),
                ],
                {"id": "test"},
            )

        with pytest.raises(ValueError, match="executor Codex"):
            ProcessGraph(
                [
                    Node(
                        id="image",
                        type="build",
                        title="Image",
                        executor="llm_claude",
                        codex_auth="chatgpt",
                        next="done",
                    ),
                    Node(id="done", type="end", title="Done"),
                ],
                {"id": "test"},
            )

    def test_missing_next_target_raises(self):
        with pytest.raises(ValueError, match="nao existe"):
            make_graph(
                [
                    {"id": "a", "next": "nonexistent"},
                    {"id": "b", "type": "end"},
                ]
            )

    def test_missing_reject_next_target_raises(self):
        with pytest.raises(ValueError, match="reject_next.*nao existe"):
            make_graph(
                [
                    {
                        "id": "a",
                        "type": "human_gate",
                        "next": "b",
                        "reject_next": "missing",
                    },
                    {"id": "b", "type": "end"},
                ]
            )

    def test_fix_review_must_be_a_reachable_review(self):
        graph = make_graph(
            [
                {
                    "id": "fix",
                    "type": "build",
                    "next": "check",
                    "fix_review": "audit",
                },
                {"id": "check", "type": "gate", "next": "audit"},
                {"id": "audit", "type": "review", "next": "done"},
                {"id": "done", "type": "end"},
            ]
        )

        assert graph.get_node("fix").fix_review == "audit"

        # Um gate determinístico também pode fechar a auditoria de um fix: ele
        # valida o recibo e reexecuta as provas sem gastar um turno de LLM.
        gate_audit = make_graph(
            [
                {
                    "id": "fix",
                    "type": "build",
                    "next": "check",
                    "fix_review": "check",
                },
                {"id": "check", "type": "gate", "next": "done"},
                {"id": "done", "type": "end"},
            ]
        )
        assert gate_audit.get_node("fix").fix_review == "check"

        with pytest.raises(ValueError, match="fix_review.*nao e review nem gate"):
            make_graph(
                [
                    {
                        "id": "fix",
                        "type": "build",
                        "next": "check",
                        "fix_review": "check",
                    },
                    {"id": "check", "type": "document", "next": "done"},
                    {"id": "done", "type": "end"},
                ]
            )

        with pytest.raises(ValueError, match="nao e alcancavel"):
            make_graph(
                [
                    {
                        "id": "fix",
                        "type": "build",
                        "next": "done",
                        "fix_review": "audit",
                    },
                    {"id": "audit", "type": "review", "next": "done"},
                    {"id": "done", "type": "end"},
                ]
            )

    def test_no_end_node_raises(self):
        with pytest.raises(ValueError, match="exatamente 1 node type=end"):
            make_graph(
                [
                    {"id": "a", "next": "b"},
                    {"id": "b", "type": "build"},
                ]
            )

    def test_multiple_end_nodes_raises(self):
        with pytest.raises(ValueError, match="exatamente 1 node type=end"):
            make_graph(
                [
                    {"id": "a", "next": "b"},
                    {"id": "b", "type": "end"},
                    {"id": "c", "type": "end"},
                ]
            )

    def test_invalid_validation_mode_raises(self):
        nodes = [
            Node(
                id="a",
                type="gate",
                title="A",
                next="done",
                validation_mode="fastish",
            ),
            Node(id="done", type="end", title="Done"),
        ]
        with pytest.raises(ValueError, match="validation_mode invalido"):
            ProcessGraph(nodes, {"id": "test"})

    def test_llm_episode_budget_requires_named_episode(self):
        nodes = [
            Node(
                id="a",
                type="build",
                title="A",
                next="done",
                llm_episode_budget_seconds=60,
            ),
            Node(id="done", type="end", title="Done"),
        ]
        with pytest.raises(ValueError, match="orçamento sem llm_episode"):
            ProcessGraph(nodes, {"id": "test"})

    def test_structured_review_path_is_only_valid_on_review(self):
        nodes = [
            Node(
                id="a",
                type="build",
                title="A",
                next="done",
                review_route_path="docs/review.yml",
            ),
            Node(id="done", type="end", title="Done"),
        ]
        with pytest.raises(ValueError, match="nao e review"):
            ProcessGraph(nodes, {"id": "test"})


# ---------------------------------------------------------------------------
# load_graph — YAML file
# ---------------------------------------------------------------------------


class TestLoadGraph:
    def test_loads_codex_auth(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: auth_process\ntitle: Auth\nnodes:\n"
            "  - {id: image, type: build, title: Image, executor: codex, codex_auth: chatgpt, next: end}\n"
            "  - {id: end, type: end, title: Done}\n",
            encoding="utf-8",
        )

        assert load_graph(p).get_node("image").codex_auth == "chatgpt"

    def test_loads_fail_fast_validation_mode(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: test\ntitle: Test\nnodes:\n"
            "  - {id: gate, type: gate, title: Gate, validation_mode: fail_fast, next: end}\n"
            "  - {id: end, type: end, title: Done}\n"
        )

        graph = load_graph(p)

        assert graph.get_node("gate").validation_mode == "fail_fast"

    def test_load_inline_process(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: test_process\nversion: '0.1.0'\ntitle: Test\nnodes:\n"
            "  - {id: a, type: discovery, title: A, next: b}\n"
            "  - {id: b, type: document, title: B, next: c}\n"
            "  - {id: c, type: build, title: C, next: d}\n"
            "  - {id: d, type: gate, title: D, next: e}\n"
            "  - {id: e, type: end, title: Done}\n"
        )
        g = load_graph(str(p))
        assert g.meta["id"] == "test_process"
        assert len(g.nodes) == 5

    def test_load_process_with_sprints(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: test_v2\nversion: '0.2.0'\ntitle: Test v2\nnodes:\n"
            "  - {id: a, type: discovery, title: A, sprint: sprint-01-discovery, next: b}\n"
            "  - {id: b, type: document, title: B, sprint: sprint-01-discovery, next: c}\n"
            "  - {id: c, type: build, title: C, sprint: sprint-02-build, next: d}\n"
            "  - {id: d, type: end, title: Done}\n"
        )
        g = load_graph(str(p))
        assert len(g.get_sprints()) == 2
        assert "sprint-01-discovery" in g.get_sprints()

    def test_loads_optional_hyper_mode_context_fields(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: context_process\nversion: '1.0.0'\ntitle: Context\nnodes:\n"
            "  - id: context\n"
            "    type: document\n"
            "    title: Context\n"
            "    hyper_mode_docs: [docs/PRD.md, docs/FEATURES.md]\n"
            "    hyper_mode_full_docs: [docs/FEATURES.md]\n"
            "    hyper_mode_preview_lines: 12\n"
            "    hyper_mode_full_max_lines: 80\n"
            "    preserve_outputs_on_reentry: true\n"
            "    next: end\n"
            "  - {id: end, type: end, title: End}\n",
            encoding="utf-8",
        )

        node = load_graph(p).get_node("context")

        assert node.hyper_mode_docs == ["docs/PRD.md", "docs/FEATURES.md"]
        assert node.hyper_mode_full_docs == ["docs/FEATURES.md"]
        assert node.hyper_mode_preview_lines == 12
        assert node.hyper_mode_full_max_lines == 80
        assert node.preserve_outputs_on_reentry is True

    def test_loads_context_profile(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: context_process\nversion: '1.0.0'\ntitle: Context\nnodes:\n"
            "  - id: context\n"
            "    type: discovery\n"
            "    title: Context\n"
            "    context_profile: feature_delta.discovery\n"
            "    next: end\n"
            "  - {id: end, type: end, title: End}\n",
            encoding="utf-8",
        )

        node = load_graph(p).get_node("context")

        assert node.context_profile == "feature_delta.discovery"

    def test_loads_llm_timeout_seconds(self, tmp_path):
        p = tmp_path / "process.yml"
        p.write_text(
            "id: timeout_process\nversion: '1.0.0'\ntitle: Timeout\nnodes:\n"
            "  - id: implement\n"
            "    type: build\n"
            "    title: Implement\n"
            "    executor: codex\n"
            "    llm_timeout_seconds: 600\n"
            "    outputs: [src/]\n"
            "    next: end\n"
            "  - {id: end, type: end, title: End}\n",
            encoding="utf-8",
        )

        assert load_graph(p).get_node("implement").llm_timeout_seconds == 600

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_graph("nonexistent.yml")
