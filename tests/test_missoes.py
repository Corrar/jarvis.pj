"""Missões em segundo plano: o agente da missão, as ferramentas da conversa, o aviso do fim e a configuração."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from jarvis import agente, cli, ferramentas, missoes
from jarvis.config import Config, carregar
from jarvis.memoria import Memoria
from jarvis.missoes import CONCLUIDA, FALHOU, INTERROMPIDA, LIMITE, PARADA, RODANDO, Missao, Missoes, frase_do_fim
from jarvis.painel import MUDO
from jarvis.registro import Registro
from test_trabalhos import Agente

PEDIDO = "Pesquise os cinco melhores notebooks até cinco mil reais e me diga qual compensa mais."


def cfg(tmp_path, **extra) -> Config:
    pasta = tmp_path / "Documents"
    pasta.mkdir(exist_ok=True)
    return Config(**{**dict(modelo="sonnet", usuario="Bruno", pastas=(pasta,), orcamento_usd=None, cidade="Curitiba"), **extra})


def fim(texto="O que compensa é o modelo X.\nDetalhes: ...\nFontes: https://a.com", subtype="success", custo=0.2):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s", total_cost_usd=custo, result=texto)


def passo(nome, **entrada):
    return AssistantMessage(content=[ToolUseBlock(id="t", name=nome, input=entrada)], model="m")


async def rapido(agente_falso):
    yield AssistantMessage(content=[TextBlock("Vou pesquisar.")], model="m")
    yield passo("WebSearch", query="melhores notebooks 2026")
    yield UserMessage(content=[ToolResultBlock(tool_use_id="t", content="resultado", is_error=False)])
    yield passo("Read", file_path="C:/x/notas.txt")
    yield UserMessage(content=[ToolResultBlock(tool_use_id="t", content="Caminho fora das pastas liberadas: C:/x/notas.txt", is_error=True)])
    yield fim()


async def demorado(agente_falso):
    yield passo("WebSearch", query="x")
    await agente_falso.interrompido.wait()
    yield fim("parei no meio")


async def sem_resultado(agente_falso):
    yield AssistantMessage(content=[TextBlock("Até aqui achei A.")], model="m")


async def com_erro(agente_falso):
    yield AssistantMessage(content=[], model="m", error="rate_limit")
    yield fim("", subtype="error_during_execution")


async def estoura(agente_falso):
    yield fim("parcial", subtype="error_max_budget_usd")


async def quebra(agente_falso):
    raise RuntimeError("o binário caiu")
    yield  # noqa: RET504 - gerador


def montar(tmp_path, roteiro=rapido, **extra):
    Agente.criados.clear()
    terminadas, mudancas = [], []
    memoria = Memoria(tmp_path / "m")
    proprias = ferramentas.criar_ferramentas(memoria, Registro(":memory:"), clima=object(), cidade="Curitiba", mostrar_paineis=lambda lista: "ok")
    m = Missoes(
        cfg(tmp_path), memoria, tmp_path / ".jarvis", proprias, orcamento_usd=1.5,
        fabrica=lambda options: Agente(options, roteiro), ao_mudar=lambda: mudancas.append(1), ao_terminar=terminadas.append, **extra,
    )  # fmt: skip
    return m, terminadas, mudancas


async def esperar_fim(m, codigo, tempo=10.0):
    prazo = asyncio.get_running_loop().time() + tempo
    while m.achar(codigo).estado == RODANDO and asyncio.get_running_loop().time() < prazo:
        await asyncio.sleep(0.01)
    return m.achar(codigo)


# --- a missão ---------------------------------------------------------------------------------------


def test_so_as_ferramentas_de_leitura_entram_na_missao(tmp_path):
    m, *_ = montar(tmp_path)
    assert sorted(f.name for f in m.proprias) == sorted(["agora", "info_sistema", "memoria_ler", "memoria_listar", "clima", "painel_mostrar"])
    assert not {"memoria_salvar", "memoria_apagar", "historico_buscar"} & {f.name for f in m.proprias}
    assert not set(missoes.PROPRIAS) & {*ferramentas.AGEM, *ferramentas.GRAVAM, *ferramentas.SO_EM_TURNO_LIMPO, *ferramentas.NA_PAGINA}


def test_rodar_ate_o_fim_e_guardar(tmp_path):
    m, terminadas, mudancas = montar(tmp_path)

    async def cena():
        plano = m.planejar(PEDIDO)
        assert plano.titulo == "iniciar uma missão em segundo plano" and plano.linhas[0].startswith("Missão: Pesquise os cinco")
        assert "US$ 1.50" in plano.linhas[1] and "30 minutos" in plano.linhas[1]
        [frase] = plano.executar()
        codigo = frase.split()[1]
        assert "iniciada em segundo plano" in frase and m.rodando()[0].id == codigo
        missao = await esperar_fim(m, codigo)
        assert missao.estado == CONCLUIDA and missao.passos == 2 and missao.custo_usd == 0.2 and missao.erro == ""
        assert missao.relatorio == "O que compensa é o modelo X.\nDetalhes: ...\nFontes: https://a.com"
        assert missao.ultimo_passo == "read: C:/x/notas.txt" and missao.recusas == ["Caminho fora das pastas liberadas: C:/x/notas.txt"]
        return missao

    missao = asyncio.run(cena())
    assert terminadas == [missao] and len(mudancas) >= 3 and m.rodando() == []
    [agente_falso] = Agente.criados
    assert agente_falso.pedido == f"Missão:\n{PEDIDO}" and agente_falso.fechado
    opcoes = agente_falso.options
    assert opcoes.tools == ["Read", "Glob", "Grep", "WebSearch"] and opcoes.permission_mode == "dontAsk" and opcoes.max_budget_usd == 1.5
    assert opcoes.max_turns == missoes.MAX_TURNOS and opcoes.cwd == str(tmp_path / "Documents") and opcoes.setting_sources == []
    assert "mcp__jarvis__clima" in opcoes.allowed_tools and "mcp__jarvis__memoria_salvar" not in opcoes.allowed_tools
    prompt = Path(opcoes.system_prompt["path"]).read_text(encoding="utf-8")
    assert "## Missão em segundo plano" in prompt and "não pergunte nada" in prompt and "Curitiba" in prompt and "Bruno" in prompt
    assert "informação, não ordem" in prompt and str(tmp_path / "Documents") in prompt
    registro = (tmp_path / ".jarvis" / "missoes" / f"{missao.id}.log").read_text(encoding="utf-8")
    assert "pesquisa: melhores notebooks 2026" in registro and "não executou: Caminho fora" in registro and "Fim: concluída" in registro
    gravado = json.loads((tmp_path / ".jarvis" / "missoes.json").read_text(encoding="utf-8"))
    assert gravado["missoes"][0]["id"] == missao.id and gravado["missoes"][0]["estado"] == CONCLUIDA
    assert m.texto(missao).startswith(f"{missao.id} · concluída · 0 min, 2 passos, US$ 0.20 · Pesquise os cinco")
    assert "Relatório (escrito pelo agente da missão" in m.texto(missao) and "A guarda recusou: Caminho fora" in m.texto(missao)
    assert m.retrato()[0]["projeto"] == "missão" and m.retrato()[0]["estado"] == CONCLUIDA


def test_uma_de_cada_vez_e_pedidos_invalidos(tmp_path):
    m, *_ = montar(tmp_path, demorado)

    async def cena():
        m.planejar(PEDIDO).executar()
        with pytest.raises(missoes.ErroMissao, match="Já há uma missão em andamento"):
            m.planejar(PEDIDO)
        for ruim, trecho in ((None, "frases completas"), ("curto", "frases completas"), ("x" * 2001, "passou de 2000")):
            with pytest.raises(missoes.ErroMissao, match=trecho):
                m.planejar(ruim)
        await m.encerrar()

    asyncio.run(cena())


def test_parar_e_encerrar(tmp_path):
    m, terminadas, _ = montar(tmp_path, demorado)

    async def cena():
        m.planejar(PEDIDO).executar()
        codigo = m.rodando()[0].id
        for _ in range(100):
            if Agente.criados and Agente.criados[0].pedido:
                break
            await asyncio.sleep(0.01)
        parada = await m.parar(codigo)
        assert parada.estado == PARADA and parada.relatorio == "parei no meio" and parada.fim
        with pytest.raises(missoes.ErroMissao, match="não está em andamento"):
            await m.parar(codigo)
        m.planejar(PEDIDO).executar()
        [interrompida] = await m.encerrar()
        assert interrompida.estado == INTERROMPIDA

    asyncio.run(cena())
    assert [t.estado for t in terminadas] == [PARADA, INTERROMPIDA]


def test_parar_quem_nao_responde_e_cancelada(tmp_path):
    async def surda(agente_falso):
        await asyncio.sleep(30)
        yield fim()

    m, terminadas, _ = montar(tmp_path, surda)

    async def cena():
        m.planejar(PEDIDO).executar()
        codigo = m.rodando()[0].id
        parada = await m.parar(codigo, espera=0.2)
        assert parada.estado == PARADA

    asyncio.run(cena())
    assert len(terminadas) == 1


@pytest.mark.parametrize(
    ("roteiro", "estado", "erro", "relatorio"),
    [
        (sem_resultado, FALHOU, "o agente encerrou sem dizer o resultado", "Até aqui achei A."),
        (com_erro, FALHOU, "Limite de uso da API atingido. Espere um pouco e tente de novo.", ""),
        (estoura, LIMITE, "chegou ao limite de gasto de US$ 1.50", "parcial"),
        (quebra, FALHOU, "RuntimeError: o binário caiu", ""),
    ],
    ids=["sem resultado", "erro da api", "limite de gasto", "quebra"],
)
def test_fins_ruins(tmp_path, roteiro, estado, erro, relatorio):
    m, terminadas, _ = montar(tmp_path, roteiro)

    async def cena():
        m.planejar(PEDIDO).executar()
        return await esperar_fim(m, m.rodando()[0].id)

    missao = asyncio.run(cena())
    assert (missao.estado, missao.erro, missao.relatorio) == (estado, erro, relatorio)
    assert terminadas == [missao]


def test_tempo_maximo(tmp_path):
    async def eterna(agente_falso):
        await asyncio.sleep(30)
        yield fim()

    m, *_ = montar(tmp_path, eterna, tempo_max=0.05)

    async def cena():
        m.planejar(PEDIDO).executar()
        return await esperar_fim(m, m.rodando()[0].id)

    missao = asyncio.run(cena())
    assert missao.estado == LIMITE and "tempo máximo" in missao.erro


def test_registro_sobrevive_a_reinicio_e_marca_a_que_ficou_no_meio(tmp_path):
    pasta = tmp_path / ".jarvis"
    pasta.mkdir()
    (pasta / "missoes.json").write_text(
        json.dumps({"missoes": [
            {"id": "aaa", "pedido": "ficou no meio", "inicio": "2026-10-09T08:00:00", "estado": RODANDO},
            {"id": "bbb", "pedido": "acabou", "inicio": "2026-10-09T09:00:00", "estado": CONCLUIDA, "fim": "2026-10-09T09:10:00", "relatorio": "ok"},
            {"id": "ccc", "pedido": "torta"},
            "nada",
        ]}),  # fmt: skip
        encoding="utf-8",
    )
    m, *_ = montar(tmp_path)
    assert [x.id for x in m.listar()] == ["bbb", "aaa"]
    assert m.achar("aaa").estado == INTERROMPIDA and m.achar("aaa").erro == "o Jarvis foi fechado no meio da missão"
    assert m.achar("acabou").id == "bbb" and m.achar().id == "bbb" and m.achar("BBB").id == "bbb"
    with pytest.raises(missoes.ErroMissao, match="Não há missão"):
        m.achar("zzz")
    (pasta / "missoes.json").write_text("{", encoding="utf-8")
    assert montar(tmp_path)[0].listar() == []
    vazia, *_ = montar(tmp_path)
    (pasta / "missoes.json").unlink()
    vazia2, *_ = montar(tmp_path)
    with pytest.raises(missoes.ErroMissao, match="Ainda não há missões"):
        vazia2.achar()


def test_so_as_ultimas_ficam_no_registro(tmp_path):
    m, *_ = montar(tmp_path)
    for i in range(missoes.GUARDADAS + 5):
        m._missoes[f"m{i:03d}"] = Missao(id=f"m{i:03d}", pedido=f"pedido {i}", inicio=f"2026-10-09T08:{i:02d}:00", estado=CONCLUIDA)
    m._gravar()
    assert len(m.listar(100)) == missoes.GUARDADAS and m.listar(1)[0].id == f"m{missoes.GUARDADAS + 4:03d}"


def test_achar_com_mais_de_uma_parecida(tmp_path):
    m, *_ = montar(tmp_path)
    m._missoes["a1"] = Missao(id="a1", pedido="notebooks baratos", inicio="2026-10-09T08:00:00", estado=CONCLUIDA)
    m._missoes["a2"] = Missao(id="a2", pedido="notebooks caros", inicio="2026-10-09T09:00:00", estado=CONCLUIDA)
    with pytest.raises(missoes.ErroMissao, match="Mais de uma missão combina"):
        m.achar("notebooks")


def test_frase_do_fim():
    feita = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=CONCLUIDA, relatorio="O modelo X compensa.\nFontes: a.com")
    assert frase_do_fim(feita) == f"Terminei a missão: {feita.titulo}. O modelo X compensa. Fontes: a.com"
    longa = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=CONCLUIDA, relatorio="Frase um. " * 100)
    dita = frase_do_fim(longa)
    assert len(dita) < missoes.MAX_FALADO + 120 and dita.endswith("Frase um. O resto está no terminal.")
    sem_ponto = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=CONCLUIDA, relatorio="x" * 1000)
    assert frase_do_fim(sem_ponto).endswith("… O resto está no terminal.")
    vazia = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=CONCLUIDA)
    assert frase_do_fim(vazia) == f"Terminei a missão: {vazia.titulo}."
    falhou = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=FALHOU, erro="caiu")
    assert frase_do_fim(falhou) == f'A missão "{falhou.titulo}" não chegou ao fim (falhou: caiu). Não ficou relatório.'
    limite = Missao(id="a", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=LIMITE, relatorio="parcial")
    assert frase_do_fim(limite) == f'A missão "{limite.titulo}" não chegou ao fim (parou em um limite). parcial'


def test_relatorio_limpo():
    assert missoes.limpo_do_relatorio("a\x07\x00 b\n\n\nc  d\n") == "a b\nc d"
    assert len(missoes.limpo_do_relatorio("x" * 5000)) == 1000  # cada linha vai até mil letras
    assert len(missoes.limpo_do_relatorio("\n".join(["x" * 900] * 10))) == missoes.MAX_RELATORIO


def test_descrever_passos():
    assert missoes.descrever(ToolUseBlock(id="t", name="WebSearch", input={"query": "a b"})) == "pesquisa: a b"
    assert missoes.descrever(ToolUseBlock(id="t", name="Grep", input={"pattern": "x", "path": "p"})) == "grep: p"
    assert missoes.descrever(ToolUseBlock(id="t", name="mcp__jarvis__clima", input="x")) == "clima"


# --- as ferramentas da conversa ------------------------------------------------------------------------


class Perguntas:
    def __init__(self, resposta=True):
        self.resposta, self.feitas = resposta, []

    async def __call__(self, titulo, linhas):
        self.feitas.append((titulo, list(linhas)))
        return self.resposta


async def chamar(ferramenta, **args):
    saida = await ferramenta.handler(args)
    return saida["content"][0]["text"], bool(saida.get("is_error"))


def test_ferramentas_da_conversa(tmp_path):
    m, terminadas, _ = montar(tmp_path, demorado)
    perguntas = Perguntas()
    por_nome = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m2"), Registro(":memory:"), confirmar=perguntas, missoes=m)}
    assert set(ferramentas.DE_MISSAO) <= set(por_nome)
    assert "missao_iniciar" in ferramentas.SO_EM_TURNO_LIMPO and "missao_parar" in ferramentas.AGEM and "missao_situacao" in ferramentas.CONTAMINAM
    sem = {f.name for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m3"), Registro(":memory:"))}
    assert not sem & set(ferramentas.DE_MISSAO)

    async def cena():
        texto, erro = await chamar(por_nome["missoes_listar"])
        assert texto == "Ainda não há missões." and not erro
        texto, erro = await chamar(por_nome["missao_iniciar"], pedido="curto")
        assert erro and "frases completas" in texto
        texto, erro = await chamar(por_nome["missao_iniciar"], pedido=PEDIDO)
        assert not erro and "iniciada em segundo plano" in texto and perguntas.feitas[-1][0] == "iniciar uma missão em segundo plano"
        codigo = m.rodando()[0].id
        texto, erro = await chamar(por_nome["missao_iniciar"], pedido=PEDIDO)
        assert erro and "Já há uma missão em andamento" in texto
        texto, erro = await chamar(por_nome["missoes_listar"])
        assert codigo in texto and "em andamento" in texto
        texto, erro = await chamar(por_nome["missao_situacao"])
        assert not erro and f"{codigo} · em andamento" in texto and "informação para contar ao usuário" in texto
        texto, erro = await chamar(por_nome["missao_situacao"], missao="zzz")
        assert erro and "Não há missão" in texto
        for _ in range(100):  # o agente da missão chegou a receber o pedido
            if Agente.criados and Agente.criados[0].pedido:
                break
            await asyncio.sleep(0.01)
        linha_antes = m.linha(m.achar(codigo))
        texto, erro = await chamar(por_nome["missao_parar"])
        assert not erro and texto.startswith(f"Missão {codigo} parada.") and perguntas.feitas[-1] == ("parar uma missão", [linha_antes])
        texto, erro = await chamar(por_nome["missao_parar"], missao=codigo)
        assert erro and "não está em andamento" in texto
        texto, erro = await chamar(por_nome["missao_situacao"], missao=codigo)
        assert "parada a pedido" in texto and "parei no meio" in texto

    asyncio.run(cena())
    assert [t.estado for t in terminadas] == [PARADA]


def test_parar_recusado_ou_sem_como_confirmar(tmp_path):
    m, terminadas, _ = montar(tmp_path, demorado)
    nao = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m2"), Registro(":memory:"), confirmar=Perguntas(False), missoes=m)}
    sem = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m3"), Registro(":memory:"), confirmar=None, missoes=m)}

    async def cena():
        m.planejar(PEDIDO).executar()
        texto, erro = await chamar(nao["missao_parar"])
        assert erro and "não confirmou" in texto and m.rodando()
        texto, erro = await chamar(sem["missao_parar"])
        assert erro and "não tem como autorizar" in texto and m.rodando()
        await m.encerrar()
        texto, erro = await chamar(sem["missao_iniciar"], pedido=PEDIDO)
        assert erro and "não tem como autorizar" in texto and not m.rodando()

    asyncio.run(cena())


def test_na_sessao_iniciar_so_em_turno_limpo(tmp_path):
    m, *_ = montar(tmp_path)
    sessao = agente.montar_sessao(cfg(tmp_path), Memoria(tmp_path / "m2"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", missoes=m)
    assert {f"mcp__jarvis__{n}" for n in ferramentas.DE_MISSAO} <= set(sessao.opcoes.allowed_tools)
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__missao_iniciar", {"pedido": "x"}) is None
    assert vigia.decidir("mcp__jarvis__missao_situacao", {}) is None  # o relatório é conteúdo de fora
    assert "Ação bloqueada" in (vigia.decidir("mcp__jarvis__missao_iniciar", {"pedido": "x"}) or "")
    assert "Ação bloqueada" in (vigia.decidir("mcp__jarvis__abrir_site", {"endereco": "x"}) or "")
    prompt = (tmp_path / ".jarvis" / "prompt-sistema.md").read_text(encoding="utf-8")
    assert "Missões: um pedido longo de leitura e pesquisa" in prompt and "US$ 1.50" in prompt


def test_a_guarda_da_missao_barra_pastas_e_gravacao(tmp_path):
    m, *_ = montar(tmp_path)
    vigia = m.guarda()
    assert vigia.decidir("Read", {"file_path": str(tmp_path / "Documents" / "a.txt")}) is None
    assert "fora das pastas" in (vigia.decidir("Read", {"file_path": str(tmp_path / "fora.txt")}) or "")
    assert "não está liberada" in (vigia.decidir("mcp__jarvis__memoria_salvar", {}) or "").lower() or vigia.decidir("mcp__jarvis__memoria_salvar", {}) is not None
    assert vigia.decidir("mcp__jarvis__abrir_site", {"endereco": "x"}) is not None  # nem existe na missão


# --- na conversa ------------------------------------------------------------------------------------------


class VozFalsa:
    muda = False

    def __init__(self):
        self.dito = []

    def narrar(self, texto):
        self.dito.append(texto)

    def fim_de_bloco(self):
        self.dito.append("|")


def test_avisar_fim_de_missao(tmp_path, capsys, monkeypatch):
    m, *_ = montar(tmp_path)
    missao = Missao(id="abc", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=CONCLUIDA, fim="2026-10-09T08:05:00", relatorio="O modelo X compensa.\nFontes: a.com")
    voz, bipes = VozFalsa(), []
    monkeypatch.setattr(cli, "_bipe", bipes.append)
    terminadas = [missao]
    asyncio.run(cli.avisar_fim_de_missao(m, terminadas, voz, MUDO))
    assert terminadas == [] and voz.dito == [f"Terminei a missão: {missao.titulo}. O modelo X compensa. Fontes: a.com", "|"]
    saida = capsys.readouterr().out
    assert "[missão] Terminei a missão" in saida and "    Relatório (escrito pelo agente da missão" in saida and "    Fontes: a.com" in saida
    voz.muda = True
    asyncio.run(cli.avisar_fim_de_missao(m, [missao], voz, MUDO))
    assert bipes == [988]


def test_comando_missoes(tmp_path, capsys):
    m, *_ = montar(tmp_path)
    cli.mostrar_missoes(m)
    assert "Ainda não há missões" in capsys.readouterr().out
    m._missoes["abc"] = Missao(id="abc", pedido=PEDIDO, inicio="2026-10-09T08:00:00", estado=RODANDO, ultimo_passo="pesquisa: x")
    cli.mostrar_missoes(m)
    saida = capsys.readouterr().out
    assert "abc · em andamento" in saida and "agora: pesquisa: x" in saida and "Detalhes de uma: /missoes <código>" in saida
    cli.mostrar_missoes(m, "abc")
    assert "Pedido: Pesquise os cinco" in capsys.readouterr().out
    cli.mostrar_missoes(m, "zzz")
    assert "Não há missão" in capsys.readouterr().out


def test_configuracao_do_orcamento(tmp_path):
    base = {"ANTHROPIC_API_KEY": "k", "JARVIS_PASTAS": str(tmp_path)}
    assert carregar(base, tmp_path).orcamento_missao_usd == 1.0
    assert carregar({**base, "JARVIS_ORCAMENTO_MISSAO_USD": "2,5"}, tmp_path).orcamento_missao_usd == 2.5
    with pytest.raises(Exception, match="JARVIS_ORCAMENTO_MISSAO_USD"):
        carregar({**base, "JARVIS_ORCAMENTO_MISSAO_USD": "x"}, tmp_path)
