"""Fixtures globais da suíte.

FT_HOME isolado por teste: nenhum teste pode ler ou escrever no ~/.ft real.
Como os subprocessos (run_ft) herdam os.environ, o isolamento vale também
para os testes E2E que invocam a CLI.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_ft_home(tmp_path_factory, monkeypatch):
    """Redireciona ~/.ft para um diretório temporário exclusivo do teste."""
    home = tmp_path_factory.mktemp("ft_home")
    monkeypatch.setenv("FT_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def skip_api_health_check(monkeypatch):
    """Testes nunca batem na API real."""
    monkeypatch.setenv("FT_SKIP_HEALTH_CHECK", "1")
