import sqlite3
from datetime import datetime

import pytest

from jarvis.registro import Chamada, Registro, Turno


@pytest.fixture
def registro():
    r = Registro(":memory:")
    yield r
    r.fechar()


def turno(pergunta="que horas são?", resposta="São nove horas.", **extra):
    base = dict(pergunta=pergunta, resposta=resposta, inicio=datetime(2026, 10, 7, 9, 51, 3), duracao_ms=1200, custo_usd=0.0123)
    return Turno(**{**base, **extra})


def test_grava_turno_e_chamadas(registro):
    chamadas = [
        Chamada("mcp__jarvis__agora", {}),
        Chamada("Read", {"file_path": "C:\\fora\\x.txt"}, erro="Caminho fora das pastas liberadas"),
    ]
    numero = registro.gravar(turno(chamadas=chamadas, sessao="abc", modo="voz"))
    (linha,) = registro.ultimos()
    assert linha["id"] == numero and linha["sessao"] == "abc" and linha["modo"] == "voz"
    assert linha["inicio"] == "2026-10-07T09:51:03"
    assert linha["pergunta"] == "que horas são?" and linha["resposta"] == "São nove horas."
    assert linha["custo_usd"] == pytest.approx(0.0123) and linha["n_chamadas"] == 2
    gravadas = registro.chamadas_do_turno(numero)
    assert [c["ferramenta"] for c in gravadas] == ["mcp__jarvis__agora", "Read"]
    assert gravadas[1]["entrada"] == '{"file_path": "C:\\\\fora\\\\x.txt"}'
    assert gravadas[0]["erro"] is None and "fora das pastas" in gravadas[1]["erro"]


def test_ultimos_vem_do_mais_novo_para_o_mais_antigo(registro):
    for n in range(5):
        registro.gravar(turno(pergunta=f"pergunta {n}"))
    assert [t["pergunta"] for t in registro.ultimos(3)] == ["pergunta 4", "pergunta 3", "pergunta 2"]


def test_buscar_na_pergunta_e_na_resposta(registro):
    registro.gravar(turno("cotação do dólar", "Está em R$ 4,98."))
    registro.gravar(turno("que horas são?", "São nove horas."))
    registro.gravar(turno("e o euro?", "Não achei; o dólar eu já tinha dito."))
    assert [t["pergunta"] for t in registro.buscar("dólar")] == ["e o euro?", "cotação do dólar"]
    assert registro.buscar("bitcoin") == []


def test_buscar_trata_curingas_como_texto(registro):
    registro.gravar(turno("caiu 100% hoje", "Sim."))
    registro.gravar(turno("arquivo_final.pdf", "Achei."))
    registro.gravar(turno("outra coisa", "Nada."))
    assert [t["pergunta"] for t in registro.buscar("100%")] == ["caiu 100% hoje"]
    assert [t["pergunta"] for t in registro.buscar("arquivo_final")] == ["arquivo_final.pdf"]
    assert registro.buscar("%") and len(registro.buscar("%")) == 1  # só o turno que tem o símbolo
    assert len(registro.buscar("_")) == 1


def test_buscar_resiste_a_aspas(registro):
    registro.gravar(turno("teste", "ok"))
    assert registro.buscar("'; DROP TABLE turnos; --") == []
    assert len(registro.ultimos()) == 1


def test_dados_persistem_no_arquivo(tmp_path):
    caminho = tmp_path / "sub" / "registro.db"
    r = Registro(caminho)
    r.gravar(turno(chamadas=[Chamada("Glob", {"pattern": "*.pdf"})]))
    r.fechar()
    r2 = Registro(caminho)  # reabrir não recria nem apaga nada
    assert len(r2.ultimos()) == 1 and r2.ultimos()[0]["n_chamadas"] == 1
    r2.fechar()


def test_turno_com_defeito_nao_grava_pela_metade(registro):
    class NaoSerializa:
        def __str__(self):
            raise RuntimeError("defeito")

    with pytest.raises(RuntimeError):
        registro.gravar(turno(chamadas=[Chamada("Read", {"x": NaoSerializa()})]))
    assert registro.ultimos() == []


def test_entrada_nao_json_vira_texto(registro):
    numero = registro.gravar(turno(chamadas=[Chamada("Read", {"quando": datetime(2026, 1, 2)})]))
    assert "2026-01-02" in registro.chamadas_do_turno(numero)[0]["entrada"]


def test_limites_das_consultas(registro):
    for n in range(15):
        registro.gravar(turno(pergunta=f"dólar {n}"))
    assert len(registro.buscar("dólar", limite=50)) == 10
    assert len(registro.ultimos(0)) == 1


def test_esquema_tem_chave_estrangeira(registro):
    with pytest.raises(sqlite3.IntegrityError):
        registro._db.execute("INSERT INTO chamadas (turno_id, ferramenta, entrada) VALUES (999, 'x', '{}')")


def test_total_de_conversas():
    from jarvis.registro import Registro, Turno

    registro = Registro(":memory:")
    assert registro.total() == 0
    for pergunta in ("a", "b", "c"):
        registro.gravar(Turno(pergunta, resposta="ok"))
    assert registro.total() == 3
