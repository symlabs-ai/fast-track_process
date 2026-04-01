"""
RED PHASE — RF-01: process_id único por processo.

Testa:
- ProcessRegistry impede dois processos com mesmo id
- process_id vazio é rejeitado
- process_id é preservado entre instâncias (persistência)
- Registro de múltiplos IDs distintos é permitido
"""

import pytest
from pathlib import Path

from ft.engine.process_registry import ProcessRegistry


class TestProcessRegistryUniqueId:
    def test_register_new_id_succeeds(self, tmp_path):
        """Registrar um process_id novo deve retornar True."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        assert registry.register("proc-abc-001") is True

    def test_register_duplicate_id_raises(self, tmp_path):
        """Tentar registrar um process_id já existente deve lançar exceção."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        registry.register("proc-abc-001")
        with pytest.raises(ValueError, match="process_id.*já existe|already exists"):
            registry.register("proc-abc-001")

    def test_empty_process_id_rejected(self, tmp_path):
        """process_id vazio deve ser rejeitado."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        with pytest.raises(ValueError, match="process_id.*inválido|invalid"):
            registry.register("")

    def test_none_process_id_rejected(self, tmp_path):
        """process_id None deve ser rejeitado."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        with pytest.raises((ValueError, TypeError)):
            registry.register(None)

    def test_registry_persists_across_instances(self, tmp_path):
        """ID registrado deve ser lembrado em nova instância."""
        reg_path = tmp_path / "registry.yml"
        r1 = ProcessRegistry(reg_path)
        r1.register("proc-001")

        r2 = ProcessRegistry(reg_path)
        with pytest.raises(ValueError):
            r2.register("proc-001")

    def test_multiple_distinct_ids_allowed(self, tmp_path):
        """Registrar IDs distintos deve funcionar sem erro."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        assert registry.register("proc-001") is True
        assert registry.register("proc-002") is True
        assert registry.register("proc-003") is True

    def test_is_registered_returns_true_for_known_id(self, tmp_path):
        """is_registered deve retornar True para ID já registrado."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        registry.register("proc-xyz")
        assert registry.is_registered("proc-xyz") is True

    def test_is_registered_returns_false_for_unknown_id(self, tmp_path):
        """is_registered deve retornar False para ID desconhecido."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        assert registry.is_registered("proc-unknown") is False

    def test_list_ids_returns_all_registered(self, tmp_path):
        """list_ids deve retornar todos os IDs registrados."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        registry.register("proc-001")
        registry.register("proc-002")
        ids = registry.list_ids()
        assert "proc-001" in ids
        assert "proc-002" in ids

    def test_list_ids_empty_when_no_registrations(self, tmp_path):
        """list_ids deve retornar lista vazia quando nenhum ID registrado."""
        registry = ProcessRegistry(tmp_path / "registry.yml")
        assert registry.list_ids() == []
