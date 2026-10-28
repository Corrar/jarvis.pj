"""A oficina de trabalhos, com repositórios git de verdade e um agente de mentira no lugar do SDK."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from jarvis import trabalhos
from jarvis.acoes import ErroAcao
from jarvis.projetos import Projeto
from jarvis.skills import Skills
from jarvis.trabalhos import CONCLUIDO, FALHOU, INTERROMPIDO, LIMITE, PARADO, RODANDO, ErroTrabalho, Oficina, Trabalho

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="precisa do git instalado")

TAREFA = "Crie a função dobro em app.py e um teste para ela."


def git(pasta, *argumentos):
    ambiente = {**os.environ, "GIT_AUTHOR_NAME": "Dono", "GIT_AUTHOR_EMAIL": "dono@x", "GIT_COMMITTER_NAME": "Dono", "GIT_COMMITTER_EMAIL": "dono@x"}
    return subprocess.run(["git", *argumentos], cwd=pasta, check=True, capture_output=True, text=True, env=ambiente).stdout.strip()


@pytest.fixture
def projeto(tmp_path):
    pasta = Path(os.path.realpath(tmp_path / "royale-vision"))
    (pasta / ".claude" / "skills" / "padrao-da-casa").mkdir(parents=True)
    (pasta / ".claude" / "skills" / "padrao-da-casa" / "SKILL.md").write_text("---\nname: padrao-da-casa\ndescription: x\n---\n", encoding="utf-8")
    (pasta / "app.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    git(pasta, "init", "-q", "-b", "main")
    git(pasta, "add", "-A")
    git(pasta, "commit", "-q", "-m", "primeiro commit")
    return Projeto("Royale Vision", pasta)


def resultado(subtipo="success", texto="Fiz a função e o teste; os testes passam.", custo=0.42, **extra):
    return ResultMessage(subtype=subtipo, duration_ms=1, duration_api_ms=1, is_error=subtipo != "success", num_turns=3, session_id="s",
                         total_cost_usd=custo, result=texto, **extra)  # fmt: skip


def usa(nome, **entrada):
    return AssistantMessage(content=[ToolUseBlock(id="t1", name=nome, input=entrada)], model="m")


class Agente:
    """No lugar do ClaudeSDKClient: segue um roteiro de mensagens e pode mexer na cópia de trabalho."""

    criados: list[Agente] = []

    def __init__(self, options, roteiro):
        self.options, self.roteiro = options, roteiro
        self.pedido = ""
        self.interrompido = asyncio.Event()
        self.fechado = False
        Agente.criados.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.fechado = True

    async def query(self, texto):
        self.pedido = texto

    async def interrupt(self):
        self.interrompido.set()

    async def receive_response(self):
        async for mensagem in self.roteiro(self):
            yield mensagem


def oficina_com(projeto, tmp_path, roteiro, **extra):
    Agente.criados.clear()
    terminados, mudancas = [], []
    oficina = Oficina(
        {"royale vision": projeto},
        tmp_path / ".jarvis",
        "sonnet",
        orcamento_usd=1.5,
        ao_mudar=lambda: mudancas.append(1),
        ao_terminar=terminados.append,
        fabrica=lambda options: Agente(options, roteiro),
        **extra,
    )
    return oficina, terminados, mudancas


async def esperar_fim(oficina, codigo, tempo=20.0):
    await asyncio.wait_for(oficina._execucoes[codigo].tarefa, tempo)
    return oficina.achar(codigo)


def codigo_de(frase):
    return frase.split()[1]


# --- o caminho feliz ---------------------------------------------------------------------------


def test_trabalho_do_comeco_ao_fim(projeto, tmp_path):
    async def roteiro(agente):
        copia = Path(agente.options.cwd)
        assert copia.is_dir() and (copia / "app.py").exists() and copia != projeto.pasta
        yield usa("Read", file_path=str(copia / "app.py"))
        (copia / "app.py").write_text("def soma(a, b):\n    return a + b\n\n\ndef dobro(a):\n    return 2 * a\n", encoding="utf-8")
        yield usa("Edit", file_path=str(copia / "app.py"))
        yield usa("Bash", command="git add -A && git commit -m 'cria a função dobro'")
        git(copia, "add", "-A")
        git(copia, "commit", "-q", "-m", "cria a função dobro")
        (copia / "test_app.py").write_text("from app import dobro\n\n\ndef test_dobro():\n    assert dobro(2) == 4\n", encoding="utf-8")
        yield usa("Bash", command="git push")
        yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="<tool_use_error>Comando recusado: git push</tool_use_error>", is_error=True)])
        yield AssistantMessage(content=[TextBlock("Relatório parcial")], model="m")
        yield resultado()

    async def cena():
        oficina, terminados, mudancas = oficina_com(projeto, tmp_path, roteiro)
        plano = oficina.planejar("royale vision", TAREFA)
        assert plano.titulo == "iniciar um trabalho de programação" and any("Royale Vision" in linha for linha in plano.linhas)
        (frase,) = plano.executar()
        codigo = codigo_de(frase)
        assert "segundo plano" in frase and oficina.achar().estado == RODANDO and oficina.rodando()
        trabalho = await esperar_fim(oficina, codigo)
        return oficina, trabalho, terminados, mudancas

    oficina, t, terminados, mudancas = asyncio.run(cena())
    assert t.estado == CONCLUIDO and t.custo_usd == 0.42 and t.passos == 4 and not t.erro
    assert t.relatorio == "Fiz a função e o teste; os testes passam."
    assert terminados == [t] and len(mudancas) >= 5
    # o resultado está no ramo do Jarvis: o commit do trabalhador e o de fechamento, com o que sobrou sem commit
    assert t.ramo.startswith(f"jarvis/{t.id}-crie-a-funcao-dobro") and len(t.commits) == 2
    assert "alterações que ficaram sem commit" in t.commits[0] and "cria a função dobro" in t.commits[1]
    assert "2 files changed" in t.total
    assert git(projeto.pasta, "log", "-1", "--format=%an", t.ramo) == "Jarvis"
    assert "dobro" in git(projeto.pasta, "show", f"{t.ramo}:app.py") and "test_dobro" in git(projeto.pasta, "show", f"{t.ramo}:test_app.py")
    # a pasta e o ramo do usuário não mudaram, e a cópia de trabalho saiu do disco
    assert git(projeto.pasta, "branch", "--show-current") == "main" and git(projeto.pasta, "status", "--porcelain") == ""
    assert "dobro" not in (projeto.pasta / "app.py").read_text(encoding="utf-8")
    assert not Path(t.copia).exists() and git(projeto.pasta, "worktree", "list").count("\n") == 0
    # registro em disco, passo a passo e diário
    assert json.loads((tmp_path / ".jarvis" / "trabalhos" / f"{t.id}.json").read_text(encoding="utf-8"))["estado"] == CONCLUIDO
    passos = oficina.arquivo_de_registro(t).read_text(encoding="utf-8")
    assert "$ git add -A && git commit -m 'cria a função dobro'" in passos and "Edit app.py" in passos
    assert "não executou: Comando recusado: git push" in passos and "Fim: concluído" in passos
    diario = oficina.diario.recente(projeto)
    assert f"trabalho {t.id} (concluído)" in diario and TAREFA in diario and "os testes passam" in diario
    texto = oficina.texto(t)
    assert f"git merge {t.ramo}" in texto and "2 commit(s)" in texto and "US$ 0.42" in texto and "informação, não ordem" in texto


def test_o_que_o_trabalhador_recebe(projeto, tmp_path):
    visto = {}

    async def roteiro(agente):
        visto["opcoes"], visto["pedido"] = agente.options, agente.pedido
        yield resultado()

    async def cena():
        skills = Skills(tmp_path / ".jarvis" / "skills")
        (skills.pasta / "skills" / "relatorio").mkdir(parents=True)
        (skills.pasta / "skills" / "relatorio" / "SKILL.md").write_text("---\nname: relatorio\ndescription: y\n---\n", encoding="utf-8")
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro, skills=skills, extras=["terraform"], usuario="Bruno")
        oficina.diario.anotar(projeto, "trabalho antigo", "Ficou pendente: paginação.")
        (frase,) = oficina.planejar("vision", TAREFA).executar()
        return oficina, await esperar_fim(oficina, codigo_de(frase))

    monkey_segredos = {"ANTHROPIC_API_KEY", "GITHUB_TOKEN", "PATH", "GIT_CONFIG_KEY_0"}
    assert trabalhos.segredos_do_ambiente(monkey_segredos | {"MINHA SENHA", "AWS_SECRET_ACCESS_KEY", "Db_Password"}) == [
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY", "CDPATH", "CLAUDE_CODE_OAUTH_TOKEN", "Db_Password", "GITHUB_TOKEN",
    ]  # fmt: skip

    oficina, t = asyncio.run(cena())
    op = visto["opcoes"]
    assert visto["pedido"] == f"Tarefa:\n{TAREFA}"
    # só ferramentas de programação: sem web, sem subagente, sem MCP, sem pergunta ao usuário
    assert op.tools == ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill"] == op.allowed_tools
    assert op.permission_mode == "dontAsk" and op.can_use_tool is None and op.strict_mcp_config is True and not op.mcp_servers
    assert op.cwd == t.copia and op.setting_sources == ["project"]
    assert op.max_budget_usd == 1.5 and op.max_turns == trabalhos.MAX_TURNOS and op.model == "sonnet"
    assert op.extra_args == {"no-session-persistence": None}
    # as skills do projeto e as instaladas, e só elas
    assert op.skills == ["padrao-da-casa", "jarvis:relatorio"]
    assert op.plugins == [{"type": "local", "path": str(tmp_path / ".jarvis" / "skills")}]
    # a guarda do trabalho confere tudo
    (gancho,) = op.hooks["PreToolUse"]
    assert gancho.matcher is None
    negado = asyncio.run(gancho.hooks[0]({"tool_name": "Bash", "tool_input": {"command": "git push"}}, None, None))
    assert negado["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(gancho.hooks[0]({"tool_name": "Bash", "tool_input": {"command": "terraform plan"}}, None, None)) == {}
    fora = asyncio.run(gancho.hooks[0]({"tool_name": "Write", "tool_input": {"file_path": str(projeto.pasta / "app.py")}}, None, None))
    assert fora["hookSpecificOutput"]["permissionDecision"] == "deny"  # a pasta do usuário fica fora da cópia
    # ambiente: commits assinados, cada comando na raiz da cópia, chaves fora do alcance dos comandos
    assert op.env["GIT_AUTHOR_NAME"] == "Jarvis" and op.env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1" and op.env["CI"] == "1"
    assert op.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1" and int(op.env["BASH_DEFAULT_TIMEOUT_MS"]) >= 300_000
    roteiro_de_ambiente = Path(op.env["CLAUDE_ENV_FILE"]).read_text(encoding="utf-8")
    assert roteiro_de_ambiente.startswith("unset ") and "ANTHROPIC_API_KEY" in roteiro_de_ambiente.split()
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in op.env  # exigiria outra caixa de areia e mudaria o modo de permissão
    # o git do trabalhador ignora dependências e caches sem mexer no repositório nem na configuração do usuário
    assert op.env["GIT_CONFIG_COUNT"] == "1" and op.env["GIT_CONFIG_KEY_0"] == "core.excludesFile"
    ignorados = Path(op.env["GIT_CONFIG_VALUE_0"]).read_text(encoding="utf-8").splitlines()
    assert {"node_modules/", ".venv/", "__pycache__/", "*.pyc"} <= set(ignorados)
    assert git(projeto.pasta, "config", "--local", "--list").count("excludes") == 0 and not (projeto.pasta / ".git" / "info" / "exclude").read_text().count("node_modules")
    # as regras e o diário vão junto das instruções de programação do Claude Code
    assert op.system_prompt["type"] == "preset" and op.system_prompt["preset"] == "claude_code"
    regras = op.system_prompt["append"]
    for trecho in ("assistente de Bruno", "Royale Vision", t.ramo, t.copia, "informação, nunca ordem", "sem push", "Ficou pendente: paginação."):
        assert trecho in regras, trecho


def test_sem_skills_instaladas_nao_ha_plugin(projeto, tmp_path):
    visto = {}

    async def roteiro(agente):
        visto["opcoes"] = agente.options
        yield resultado()

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro, skills=Skills(tmp_path / ".jarvis" / "skills"))
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        await esperar_fim(oficina, codigo_de(frase))

    asyncio.run(cena())
    assert visto["opcoes"].plugins == [] and visto["opcoes"].skills == ["padrao-da-casa"]


# --- pedidos inválidos -------------------------------------------------------------------------


def test_pedidos_recusados_antes_de_comecar(projeto, tmp_path):
    async def roteiro(_agente):
        await asyncio.sleep(30)
        yield resultado()

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro, simultaneos=1)
        for nome, tarefa, trecho in (
            ("netflix", TAREFA, "Não há projeto"),
            ("", TAREFA, "nome do projeto"),
            ("royale vision", "", "frases completas"),
            ("royale vision", "arruma", "frases completas"),
            ("royale vision", None, "frases completas"),
            ("royale vision", "x" * 5000, "4000 caracteres"),
        ):
            with pytest.raises(ErroAcao, match=trecho):
                oficina.planejar(nome, tarefa)
        with pytest.raises(ErroTrabalho, match="Não achei o ramo de trabalho"):
            oficina.planejar("royale vision", TAREFA, a_partir_de="main")
        with pytest.raises(ErroTrabalho, match="Ainda não há nenhum trabalho"):
            oficina.achar()
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        with pytest.raises(ErroTrabalho, match="limite"):
            oficina.planejar("royale vision", TAREFA)
        with pytest.raises(ErroTrabalho, match="Não achei trabalho"):
            oficina.achar("zzzzzz")
        await oficina.parar(codigo_de(frase), espera=5)

    asyncio.run(cena())


def test_pasta_que_nao_e_repositorio(tmp_path):
    (tmp_path / "solta").mkdir()
    oficina = Oficina({"solta": Projeto("Solta", tmp_path / "solta")}, tmp_path / ".jarvis", "sonnet")
    with pytest.raises(ErroAcao, match="não é um repositório git"):
        oficina.planejar("solta", TAREFA)
    assert oficina.listar() == []


# --- limites, falhas e paradas -----------------------------------------------------------------


@pytest.mark.parametrize(
    "final, estado, trecho",
    [
        (resultado("error_max_budget_usd", texto=None), LIMITE, "limite de gasto de US$ 1.50"),
        (resultado("error_max_turns", texto=None), LIMITE, "limite de 200 passos"),
        (resultado("error_during_execution", texto=None, errors=["explodiu"]), FALHOU, "explodiu"),
    ],
)
def test_limites_e_falhas_guardam_o_que_foi_feito(projeto, tmp_path, final, estado, trecho):
    async def roteiro(agente):
        (Path(agente.options.cwd) / "meio.py").write_text("x = 1\n", encoding="utf-8")
        yield AssistantMessage(content=[TextBlock("Cheguei até a metade.")], model="m")
        yield final

    async def cena():
        oficina, terminados, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return await esperar_fim(oficina, codigo_de(frase)), terminados

    t, terminados = asyncio.run(cena())
    assert t.estado == estado and trecho in t.erro and terminados == [t]
    assert t.relatorio == "Cheguei até a metade." and len(t.commits) == 1
    assert git(projeto.pasta, "show", f"{t.ramo}:meio.py") == "x = 1"


def test_erro_da_api_vira_falha_com_mensagem(projeto, tmp_path):
    async def roteiro(_agente):
        yield AssistantMessage(content=[TextBlock("texto do binário")], model="m", error="billing_error")
        yield resultado("success", texto="")

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return oficina, await esperar_fim(oficina, codigo_de(frase))

    oficina, t = asyncio.run(cena())
    assert t.estado == FALHOU and "sem crédito" in t.erro
    assert t.commits == [] and "Não deixou nenhuma alteração" in oficina.texto(t)
    assert t.ramo not in git(projeto.pasta, "branch", "--list")  # ramo vazio não fica no repositório


def test_se_o_fechamento_falha_o_ramo_e_a_copia_ficam(projeto, tmp_path, monkeypatch):
    """Sem conseguir conferir o que há no ramo, nada é apagado: nem o ramo, nem a cópia de trabalho."""

    async def roteiro(agente):
        copia = Path(agente.options.cwd)
        (copia / "valioso.py").write_text("x = 1\n", encoding="utf-8")
        git(copia, "add", "-A")
        git(copia, "commit", "-q", "-m", "trabalho valioso")
        yield resultado()

    def quebrado(*_a, **_k):
        raise trabalhos.ErroProjeto("index.lock: arquivo em uso")

    async def cena():
        oficina, terminados, _ = oficina_com(projeto, tmp_path, roteiro)
        monkeypatch.setattr(trabalhos, "resumo_do_ramo", quebrado)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return oficina, await esperar_fim(oficina, codigo_de(frase)), terminados

    oficina, t, terminados = asyncio.run(cena())
    assert terminados == [t] and t.estado == CONCLUIDO and t.commits == []
    assert "não consegui fechar a cópia de trabalho (index.lock: arquivo em uso)" in t.erro and t.ramo in t.erro
    assert t.ramo in git(projeto.pasta, "branch", "--list", "jarvis/*")  # o ramo, com o commit, continua lá
    assert "trabalho valioso" in git(projeto.pasta, "log", "-1", "--format=%s", t.ramo)
    assert Path(t.copia).is_dir()


def test_agente_que_cai_ou_nao_responde(projeto, tmp_path):
    async def cai(_agente):
        raise RuntimeError("o processo morreu")
        yield  # pragma: no cover

    async def some(_agente):
        return
        yield  # pragma: no cover

    async def cena(roteiro):
        oficina, terminados, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return await esperar_fim(oficina, codigo_de(frase)), terminados

    t, terminados = asyncio.run(cena(cai))
    assert t.estado == FALHOU and "RuntimeError: o processo morreu" in t.erro and terminados == [t] and Agente.criados[0].fechado
    t, _ = asyncio.run(cena(some))
    assert t.estado == FALHOU and "sem dizer o resultado" in t.erro


def test_parar_pede_ao_agente_e_guarda_o_que_havia(projeto, tmp_path):
    async def roteiro(agente):
        (Path(agente.options.cwd) / "meio.py").write_text("x = 1\n", encoding="utf-8")
        yield usa("Bash", command="npm test")
        await agente.interrompido.wait()
        yield resultado("error_during_execution", texto=None, terminal_reason="aborted_tools")

    async def cena():
        oficina, terminados, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        while oficina.achar().passos == 0:
            await asyncio.sleep(0.01)
        parado = await oficina.parar()
        with pytest.raises(ErroTrabalho, match="não está em andamento"):
            await oficina.parar(parado.id)
        return parado, terminados

    t, terminados = asyncio.run(cena())
    assert t.estado == PARADO and not t.erro and terminados == [t]
    assert len(t.commits) == 1 and not Path(t.copia).exists()


def test_pedido_de_parar_que_se_perde_e_repetido(projeto, tmp_path):
    pedidos = []

    async def roteiro(agente):
        yield usa("Bash", command="npm test")
        while len(pedidos) < 3:  # os dois primeiros pedidos chegam cedo demais e se perdem
            agente.interrompido.clear()
            await agente.interrompido.wait()
            pedidos.append(1)
        yield resultado("error_during_execution", texto=None)

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        while oficina.achar().passos == 0:
            await asyncio.sleep(0.01)
        return await oficina.parar(espera=2.0)

    t = asyncio.run(cena())
    assert t.estado == PARADO and len(pedidos) == 3


def test_parar_antes_de_o_agente_subir(projeto, tmp_path):
    chamado = []

    async def roteiro(agente):
        chamado.append(agente.pedido)
        yield resultado()

    async def cena():
        oficina, terminados, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return await oficina.parar(codigo_de(frase), espera=10), terminados  # ainda criando a cópia de trabalho

    t, terminados = asyncio.run(cena())
    assert t.estado == PARADO and chamado == [] and terminados == [t] and t.commits == []
    assert not Path(t.copia).exists() and git(projeto.pasta, "branch", "--list", "jarvis/*") == ""


def test_parar_agente_que_nao_obedece_cancela(projeto, tmp_path):
    async def roteiro(agente):
        yield usa("Bash", command="npm test")
        await asyncio.sleep(60)  # ignora o pedido de interrupção
        yield resultado()

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        while oficina.achar().passos == 0:
            await asyncio.sleep(0.01)
        return await oficina.parar(espera=0.2)

    t = asyncio.run(cena())
    assert t.estado == PARADO and Agente.criados[0].fechado and t.fim


def test_tempo_maximo(projeto, tmp_path):
    async def roteiro(_agente):
        await asyncio.sleep(60)
        yield resultado()

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro, tempo_max=0.2)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        return await esperar_fim(oficina, codigo_de(frase))

    t = asyncio.run(cena())
    assert t.estado == LIMITE and "tempo máximo" in t.erro


def test_encerrar_interrompe_os_que_estao_rodando(projeto, tmp_path):
    async def roteiro(agente):
        (Path(agente.options.cwd) / "meio.py").write_text("x = 1\n", encoding="utf-8")
        yield usa("Bash", command="npm test")
        await agente.interrompido.wait()
        yield resultado("error_during_execution", texto=None)

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro)
        oficina.planejar("royale vision", TAREFA).executar()
        oficina.planejar("royale vision", "Outra tarefa completa para o segundo trabalhador.").executar()
        while any(t.passos == 0 for t in oficina.rodando()):
            await asyncio.sleep(0.01)
        return oficina, await oficina.encerrar()

    oficina, parados = asyncio.run(cena())
    assert len(parados) == 2 and all(t.estado == INTERROMPIDO and t.commits for t in parados)
    assert oficina.rodando() == [] and asyncio.run(oficina.encerrar()) == []


# --- continuidade ------------------------------------------------------------------------------


def test_continuar_de_um_trabalho_anterior(projeto, tmp_path):
    async def primeiro(agente):
        (Path(agente.options.cwd) / "etapa1.py").write_text("x = 1\n", encoding="utf-8")
        yield resultado(texto="Etapa 1 feita; falta a etapa 2.")

    visto = {}

    async def segundo(agente):
        copia = Path(agente.options.cwd)
        visto["tinha_etapa1"] = (copia / "etapa1.py").exists()
        visto["regras"] = agente.options.system_prompt["append"]
        (copia / "etapa2.py").write_text("y = 2\n", encoding="utf-8")
        yield resultado(texto="Etapa 2 feita.")

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, primeiro)
        (frase,) = oficina.planejar("royale vision", TAREFA).executar()
        t1 = await esperar_fim(oficina, codigo_de(frase))
        oficina.fabrica = lambda options: Agente(options, segundo)
        plano = oficina.planejar("royale vision", "Continue: faça a etapa 2 do que ficou pendente.", a_partir_de=t1.id)
        assert any(f"Continua do ramo {t1.ramo}" in linha for linha in plano.linhas)
        (frase,) = plano.executar()
        return t1, await esperar_fim(oficina, codigo_de(frase))

    t1, t2 = asyncio.run(cena())
    assert visto["tinha_etapa1"] and "falta a etapa 2" in visto["regras"]  # o diário leva o relatório anterior
    assert t2.partida == t1.ramo and t2.base == git(projeto.pasta, "rev-parse", t1.ramo)
    assert len(t2.commits) == 1 and git(projeto.pasta, "show", f"{t2.ramo}:etapa1.py") == "x = 1"
    assert not (projeto.pasta / "etapa1.py").exists()


def test_recuperar_trabalho_que_ficou_pelo_caminho(projeto, tmp_path):
    from jarvis.projetos import criar_copia

    pasta = tmp_path / ".jarvis"
    copia = pasta / "copias" / "royale-vision-abc123"
    base = criar_copia(projeto, copia, "jarvis/abc123-pela-metade")
    (copia / "meio.py").write_text("x = 1\n", encoding="utf-8")
    orfao = Trabalho(id="abc123", projeto="Royale Vision", tarefa=TAREFA, ramo="jarvis/abc123-pela-metade", copia=str(copia),
                     inicio="2026-10-07T10:00:00", base=base)  # fmt: skip
    (pasta / "trabalhos").mkdir(parents=True)
    (pasta / "trabalhos" / "abc123.json").write_text(json.dumps(trabalhos.asdict(orfao)), encoding="utf-8")
    (pasta / "trabalhos" / "quebrado.json").write_text("{nao é json", encoding="utf-8")

    oficina = Oficina({"royale vision": projeto}, pasta, "sonnet")
    assert oficina.achar("abc123").estado == RODANDO
    (recuperado,) = oficina.recuperar()
    assert recuperado.estado == INTERROMPIDO and "fechado no meio" in recuperado.erro and len(recuperado.commits) == 1
    assert not copia.exists() and git(projeto.pasta, "show", "jarvis/abc123-pela-metade:meio.py") == "x = 1"
    assert oficina.recuperar() == [] and Oficina({"royale vision": projeto}, pasta, "sonnet").achar("abc123").estado == INTERROMPIDO


def test_na_partida_tira_copias_que_sobraram(projeto, tmp_path):
    from jarvis.projetos import criar_copia

    pasta = tmp_path / ".jarvis"
    sobra = pasta / "copias" / "royale-vision-aaa111"  # de um trabalho que terminou, mas a pasta não saiu
    criar_copia(projeto, sobra, "jarvis/aaa111-terminado")
    perdida = pasta / "copias" / "qualquer-coisa"  # sem trabalho nenhum
    perdida.mkdir()
    (perdida / "x.txt").write_text("x", encoding="utf-8")
    terminado = Trabalho(id="aaa111", projeto="Royale Vision", tarefa=TAREFA, ramo="jarvis/aaa111-terminado", copia=str(sobra),
                         inicio="2026-10-07T10:00:00", fim="2026-10-07T10:05:00", estado=CONCLUIDO)  # fmt: skip
    (pasta / "trabalhos").mkdir(parents=True)
    (pasta / "trabalhos" / "aaa111.json").write_text(json.dumps(trabalhos.asdict(terminado)), encoding="utf-8")
    (sobra / "esquecido.py").write_text("y = 2\n", encoding="utf-8")  # ficou sem commit na cópia
    oficina = Oficina({"royale vision": projeto}, pasta, "sonnet")
    assert oficina.recuperar() == []
    assert not sobra.exists()
    assert perdida.exists()  # pasta que não é de nenhum trabalho conhecido: o Jarvis não apaga
    assert git(projeto.pasta, "worktree", "list").count("\n") == 0 and "jarvis/aaa111-terminado" in git(projeto.pasta, "branch", "--list")
    assert git(projeto.pasta, "show", "jarvis/aaa111-terminado:esquecido.py") == "y = 2"  # nada se perdeu


# --- consulta ----------------------------------------------------------------------------------


def test_achar_listar_e_retrato(projeto, tmp_path):
    async def roteiro(_agente):
        yield resultado(texto="ok")

    async def cena():
        oficina, _, _ = oficina_com(projeto, tmp_path, roteiro)
        codigos = []
        for tarefa in ("Primeira tarefa completa do dia.", "Segunda tarefa completa do dia."):
            (frase,) = oficina.planejar("royale vision", tarefa).executar()
            codigos.append(codigo_de(frase))
            await esperar_fim(oficina, codigos[-1])
            await asyncio.sleep(1.1)  # o início tem precisão de segundos
        return oficina, codigos

    oficina, (a, b) = asyncio.run(cena())
    assert [t.id for t in oficina.listar()] == [b, a]
    assert oficina.achar().id == b and oficina.achar(a).id == a and oficina.achar(a.upper()).id == a
    assert oficina.achar("vision").id == b  # pelo nome do projeto: o mais recente dele
    linha = oficina.linha(oficina.achar(a))
    assert a in linha and "Royale Vision" in linha and "concluído" in linha and "Primeira tarefa" in linha
    retrato = oficina.retrato()
    assert [r["id"] for r in retrato] == [b, a] and retrato[0]["estado"] == CONCLUIDO and retrato[0]["projeto"] == "Royale Vision"
    assert oficina.retrato(recentes=0) == []  # terminados há mais tempo saem do painel


def test_descrever_cada_passo(tmp_path):
    copia = tmp_path / "copia"
    assert trabalhos.descrever(ToolUseBlock("1", "Bash", {"command": "npm   test\n  -- --ci"}), copia) == "$ npm test -- --ci"
    assert trabalhos.descrever(ToolUseBlock("1", "Edit", {"file_path": str(copia / "src" / "a.py")}), copia) == "Edit src/a.py"
    assert trabalhos.descrever(ToolUseBlock("1", "Read", {"file_path": "/etc/passwd"}), copia) == "Read /etc/passwd"
    assert trabalhos.descrever(ToolUseBlock("1", "Grep", {"pattern": "def \x1b[31m dobro"}), copia) == "Grep def [31m dobro"
    assert trabalhos.descrever(ToolUseBlock("1", "Skill", {"skill": "jarvis:relatorio"}), copia) == "Skill jarvis:relatorio"
    assert trabalhos.descrever(ToolUseBlock("1", "Glob", {}), copia) == "Glob"
