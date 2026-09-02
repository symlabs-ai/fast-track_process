"""O gate de fechamento reencontra a referência que o próprio close arquivou.

`ft close` move `docs/<nome>` para `.ft/cycles/<ciclo>/<nome>` e só então tenta
o merge. Se o merge falha, a retomada precisa passar pelo mesmo gate — e sem
este fallback ela falharia dizendo que o artefato "não foi encontrado", quando
o ciclo apenas o guardou. A saída restante seria `--force`, que desliga o gate
inteiro em vez de reencontrá-lo.
"""

from __future__ import annotations

from pathlib import Path

from ft.cli.main import _resolve_close_reference_path as resolve


def test_arquivo_vivo_tem_precedencia(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "feature.md").write_text("PB-001", encoding="utf-8")
    archived = tmp_path / ".ft" / "cycles" / "cycle-01"
    archived.mkdir(parents=True)
    (archived / "feature.md").write_text("PB-999", encoding="utf-8")

    assert resolve(tmp_path, "docs/feature.md", "cycle-01") == "docs/feature.md"


def test_encontra_o_artefato_ja_arquivado(tmp_path: Path) -> None:
    archived = tmp_path / ".ft" / "cycles" / "cycle-07"
    archived.mkdir(parents=True)
    (archived / "feature.md").write_text("PB-001", encoding="utf-8")

    assert (
        resolve(tmp_path, "docs/feature.md", "cycle-07")
        == ".ft/cycles/cycle-07/feature.md"
    )


def test_sem_arquivo_nenhum_devolve_o_path_declarado(tmp_path: Path) -> None:
    """A mensagem de falha deve apontar o endereço que o processo declara."""
    assert resolve(tmp_path, "docs/feature.md", "cycle-07") == "docs/feature.md"


def test_id_de_ciclo_invalido_nao_monta_path(tmp_path: Path) -> None:
    forjado = tmp_path / ".ft" / "cycles" / "x"
    forjado.mkdir(parents=True)
    (forjado / "feature.md").write_text("PB-001", encoding="utf-8")

    assert resolve(tmp_path, "docs/feature.md", "../x") == "docs/feature.md"
    assert resolve(tmp_path, "docs/feature.md", None) == "docs/feature.md"


def test_referencia_fora_de_docs_mantem_o_caminho_completo(tmp_path: Path) -> None:
    archived = tmp_path / ".ft" / "cycles" / "cycle-02" / "reports"
    archived.mkdir(parents=True)
    (archived / "decisao.md").write_text("PB-001", encoding="utf-8")

    assert (
        resolve(tmp_path, "reports/decisao.md", "cycle-02")
        == ".ft/cycles/cycle-02/reports/decisao.md"
    )


def test_path_inseguro_nao_recebe_fallback(tmp_path: Path) -> None:
    assert resolve(tmp_path, "../fora.md", "cycle-01") == "../fora.md"
