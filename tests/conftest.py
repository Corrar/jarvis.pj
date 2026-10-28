import sys
from pathlib import Path

# Deixa os testes importarem os módulos de apoio desta pasta (sistema_falso.py).
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _dados_longe_da_pasta_de_verdade(tmp_path):
    """Nenhum teste lê nem grava a pasta de dados de verdade (.jarvis): os ajustes do painel ficam lá.

    Com um remendo próprio, e não o `monkeypatch` do teste: pedir aquele aqui o faria nascer antes das
    outras peças do teste e ser desfeito depois delas, o que muda a ordem em que cada teste se desmonta.
    """
    try:
        from jarvis import cli
    except ImportError:  # sem as bibliotecas da conversa, os testes dela nem rodam
        yield
        return
    with pytest.MonkeyPatch.context() as remendo:
        remendo.setattr(cli, "PASTA_DADOS", tmp_path / "dados-do-jarvis")
        yield
