import asyncio
import sys
from pathlib import Path

import pytest

from jarvis import agente, ferramentas, guarda
from jarvis.config import Config
from jarvis.memoria import Memoria
from jarvis.registro import Registro

PROIBIDAS_NA_FASE_1 = {"Bash", "PowerShell", "Write", "Edit", "NotebookEdit", "WebFetch", "Agent"}


def cfg(tmp_path, **extra) -> Config:
    a, b = tmp_path / "Documents", tmp_path / "Desktop"
    a.mkdir(exist_ok=True)
    b.mkdir(exist_ok=True)
    base = dict(modelo="sonnet", usuario=None, pastas=(a, b), orcamento_usd=None)
    return Config(**{**base, **extra})


def montar(tmp_path, c=None, **opcoes):
    """Sessão com memória e registro em pastas temporárias."""
    memoria = opcoes.pop("memoria", None) or Memoria(tmp_path / "memoria")
    sessao = agente.montar_sessao(c or cfg(tmp_path), memoria, Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", **opcoes)
    return sessao.opcoes


def test_nativas_so_de_leitura_e_proprias_do_jarvis(tmp_path):
    opcoes = montar(tmp_path)
    assert opcoes.tools == ["Read", "Glob", "Grep", "WebSearch"]
    assert not PROIBIDAS_NA_FASE_1 & set(opcoes.tools)
    assert not PROIBIDAS_NA_FASE_1 & set(opcoes.allowed_tools)
    proprias = [t for t in opcoes.allowed_tools if t.startswith("mcp__jarvis__")]
    base = ("agora", "info_sistema", "memoria_salvar", "memoria_ler", "memoria_listar", "memoria_apagar", "historico_buscar")
    de_janela = ("janelas_listar", "fechar_aba", "reabrir_aba", "focar_janela", "fechar_janela")
    # só com a oficina, o terminal, a agenda, o tempo e as missões
    de_programar = (*ferramentas.DE_TRABALHO, *ferramentas.DE_SKILL, *ferramentas.DE_TERMINAL, *ferramentas.DE_ROTINA, *ferramentas.DE_CLIMA, *ferramentas.DE_MISSAO, *ferramentas.DE_PROTHEUS)
    agem = [n for n in ferramentas.AGEM if n not in de_programar]
    sem_janelas = [n for n in (*base, *agem) if n not in de_janela]
    if sys.platform == "win32":
        todas = (*base, "janelas_listar", "tela_ver", "programa_ver", *agem, *ferramentas.NA_PAGINA)
        assert sorted(proprias) == sorted(f"mcp__jarvis__{n}" for n in todas)
    else:  # fora do Windows as ferramentas de janela não são oferecidas ao modelo
        assert sorted(proprias) == sorted(f"mcp__jarvis__{n}" for n in sem_janelas)


def test_com_controle_de_janelas_as_ferramentas_entram_e_a_listagem_contamina(tmp_path):
    from jarvis.janelas import Janelas
    from sistema_falso import mesa

    sessao = agente.montar_sessao(
        cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", janelas=Janelas(mesa())
    )
    for nome in ("janelas_listar", "fechar_aba", "reabrir_aba", "focar_janela", "fechar_janela"):
        assert f"mcp__jarvis__{nome}" in sessao.opcoes.allowed_tools
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__fechar_aba", {"titulo": "instagram"}) is None  # ordem direta
    assert vigia.decidir("mcp__jarvis__janelas_listar", {}) is None
    # títulos de páginas são texto de fora: depois de listar, só age na próxima fala do usuário
    assert "Ação bloqueada" in vigia.decidir("mcp__jarvis__fechar_janela", {"janela": "chrome"})
    assert "Gravação bloqueada" in vigia.decidir("mcp__jarvis__memoria_salvar", {"titulo": "a", "conteudo": "b"})
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__fechar_janela", {"janela": "chrome"}) is None


def test_nunca_pergunta_e_nunca_ignora_permissoes(tmp_path):
    opcoes = montar(tmp_path)
    assert opcoes.permission_mode == "dontAsk"
    assert opcoes.can_use_tool is None


def test_isolado_das_configuracoes_da_maquina(tmp_path):
    opcoes = montar(tmp_path)
    assert opcoes.setting_sources == []
    assert opcoes.strict_mcp_config is True
    assert list(opcoes.mcp_servers) == ["jarvis"]


def test_guarda_registrada_para_todas_as_ferramentas(tmp_path):
    opcoes = montar(tmp_path)
    (matcher,) = opcoes.hooks["PreToolUse"]
    assert matcher.matcher is None  # sem filtro: toda chamada passa pela guarda
    assert len(matcher.hooks) == 1


def test_pastas_viram_cwd_e_diretorios_adicionais(tmp_path):
    c = cfg(tmp_path)
    opcoes = montar(tmp_path, c)
    assert opcoes.cwd == str(c.pastas[0])
    assert opcoes.add_dirs == [str(c.pastas[1])]


def test_prompt_vai_por_arquivo_com_persona_e_pastas(tmp_path):
    c = cfg(tmp_path, usuario="Fulano")
    opcoes = montar(tmp_path, c)
    assert opcoes.system_prompt["type"] == "file"
    texto = Path(opcoes.system_prompt["path"]).read_text(encoding="utf-8")
    assert "Você é o Jarvis" in texto
    assert str(c.pastas[0]) in texto and str(c.pastas[1]) in texto
    assert "Fulano" in texto


def test_orcamento_e_repassado(tmp_path):
    opcoes = montar(tmp_path, cfg(tmp_path, orcamento_usd=2.0))
    assert opcoes.max_budget_usd == 2.0


def test_guarda_conhece_quem_grava_e_quem_contamina(tmp_path):
    sessao = agente.montar_sessao(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis")
    vigia = sessao.guarda
    assert isinstance(vigia, guarda.Guarda)
    assert vigia.decidir("mcp__jarvis__historico_buscar", {"termo": "x"}) is None
    assert "Gravação bloqueada" in vigia.decidir("mcp__jarvis__memoria_salvar", {"titulo": "a", "conteudo": "b"})
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__memoria_apagar", {"nome": "a"}) is None


def test_indice_da_memoria_entra_no_prompt(tmp_path):
    memoria = Memoria(tmp_path / "memoria")
    vazio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "## Memória" in vazio and "Ainda vazia." in vazio
    memoria.salvar("Horário de trabalho", "Trabalha das 7h às 17h.")
    cheio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "- horario-de-trabalho: Horário de trabalho — Trabalha das 7h às 17h." in cheio


def test_avisos_do_binario_vao_para_arquivo(tmp_path):
    dados = tmp_path / ".jarvis"
    opcoes = montar(tmp_path)
    opcoes.stderr("aviso do binário\n")
    opcoes.stderr("outra linha")
    assert (dados / "agente.log").read_text(encoding="utf-8") == "aviso do binário\noutra linha\n"


def test_texto_aos_pedacos_so_no_modo_de_voz(tmp_path):
    assert montar(tmp_path).include_partial_messages is False
    com_voz = montar(tmp_path, voz=True)
    assert com_voz.include_partial_messages is True
    assert "Modo de voz ligado" in Path(com_voz.system_prompt["path"]).read_text(encoding="utf-8")


def test_licoes_entram_no_prompt_antes_do_indice(tmp_path):
    memoria = Memoria(tmp_path / "memoria")
    vazio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "## Lições" in vazio and "Nenhuma ainda." in vazio
    memoria.salvar("Listas longas", "Dizer quantos são e citar três.", categoria="licao")
    cheio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "- Listas longas: Dizer quantos são e citar três." in cheio
    assert cheio.index("## Lições") < cheio.index("## Memória (o que você já sabe")


def test_acoes_nao_alcancam_a_pasta_do_proprio_jarvis(tmp_path, monkeypatch):
    """Mesmo se o usuário liberar uma pasta que contém o projeto."""
    projeto = tmp_path / "Documents" / "jarvis-desktop"
    projeto.mkdir(parents=True)
    (projeto / ".env").write_text("ANTHROPIC_API_KEY=x", encoding="utf-8")
    (projeto / "persona.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(agente, "RAIZ_PROJETO", projeto)
    perguntas = []

    async def sim(titulo, linhas):
        perguntas.append(titulo)
        return True

    sessao = agente.montar_sessao(
        cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", confirmar=sim
    )
    servidor = sessao.opcoes.mcp_servers["jarvis"]
    assert servidor["type"] == "sdk"
    proprias = ferramentas.criar_ferramentas(
        Memoria(tmp_path / "m"), Registro(":memory:"),
        agente.Acoes(cfg(tmp_path).pastas, protegidas=[projeto]), sim,
    )
    apagar = next(f for f in proprias if f.name == "apagar")
    saida = asyncio.run(apagar.handler({"caminhos": [str(projeto / "persona.md")]}))
    assert saida.get("is_error") and "próprio Jarvis" in saida["content"][0]["text"]
    assert perguntas == [] and (projeto / "persona.md").exists()


def test_programas_extras_vem_da_configuracao(tmp_path):
    from jarvis.acoes import ler_programas

    assert ler_programas("VS Code = C:\\Apps\\Code.exe ; spotify=spotify;;lixo; =x") == {
        "vs code": "C:\\Apps\\Code.exe",
        "spotify": "spotify",
    }


def sessao_com(tmp_path, **extra):
    return agente.montar_sessao(
        cfg(tmp_path, **extra), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis"
    )


def test_por_padrao_acoes_sao_diretas_e_protegidas_pelo_turno_limpo(tmp_path):
    sessao = sessao_com(tmp_path)
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__abrir_site", {"url": "https://vercel.com"}) is None
    assert vigia.decidir("WebSearch", {"query": "vercel"}) is None
    for nome in ("abrir_site", "abrir_arquivo", "abrir_programa", "criar_pasta", "mover", "copiar", "apagar"):
        assert "Ação bloqueada" in vigia.decidir(f"mcp__jarvis__{nome}", {}), nome
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__apagar", {"caminhos": []}) is None
    prompt = Path(sessao.opcoes.system_prompt["path"]).read_text(encoding="utf-8")
    assert "Ações: executam direto" in prompt


def test_com_jarvis_confirmar_a_pergunta_volta_e_a_guarda_nao_bloqueia(tmp_path):
    sessao = sessao_com(tmp_path, confirmar_acoes=True)
    vigia = sessao.guarda
    vigia.decidir("WebSearch", {"query": "vercel"})
    assert vigia.decidir("mcp__jarvis__abrir_site", {"url": "https://vercel.com"}) is None
    prompt = Path(sessao.opcoes.system_prompt["path"]).read_text(encoding="utf-8")
    assert "só executa se ele digitar s" in prompt


def test_no_windows_sem_controle_de_janelas_a_sessao_avisa(tmp_path, monkeypatch):
    def quebra(_apelidos, _na_tela=None):
        return None  # é o que janelas_do_sistema devolve quando a camada do Windows falha ao subir

    monkeypatch.setattr(agente, "janelas_do_sistema", quebra)
    monkeypatch.setattr(agente.sys, "platform", "win32")
    sessao = sessao_com(tmp_path)
    assert len(sessao.avisos) == 1 and "janelas" in sessao.avisos[0] and "jarvis.log" in sessao.avisos[0]
    assert "mcp__jarvis__fechar_aba" not in sessao.opcoes.allowed_tools
    assert "mcp__jarvis__abrir_site" in sessao.opcoes.allowed_tools  # o resto continua funcionando


def test_fora_do_windows_nao_ha_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(agente.sys, "platform", "linux")
    assert sessao_com(tmp_path).avisos == []


def test_falha_ao_ligar_as_janelas_nao_derruba_o_jarvis(monkeypatch, caplog):
    import ctypes

    def sem_dll(_nome, **_k):
        raise OSError("user32 não carregou")

    monkeypatch.setattr(agente.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", sem_dll, raising=False)
    assert agente.janelas_do_sistema({}) is None
    assert "Não consegui ligar o controle de janelas" in caplog.text


# --- dentro da página ---------------------------------------------------------------


def sessao_com_janelas(tmp_path, **extra):
    from jarvis.janelas import Janelas
    from sistema_falso import mesa

    controle = Janelas(mesa())
    sessao = agente.montar_sessao(
        cfg(tmp_path, **extra), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", janelas=controle
    )
    return sessao, controle


def test_depois_de_ver_a_pagina_so_valem_as_acoes_de_dentro_dela(tmp_path):
    sessao, controle = sessao_com_janelas(tmp_path)
    assert sessao.janelas is controle
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__abrir_site", {"url": "https://www.youtube.com/results?search_query=lofi"}) is None
    assert vigia.decidir("mcp__jarvis__tela_ver", {}) is None
    for nome in ("tela_clicar", "tela_digitar", "tela_tecla", "rolar", "midia", "tela_ver"):
        assert vigia.decidir(f"mcp__jarvis__{nome}", {}) is None, nome
    # o que a página mostrou não pode abrir programa, mexer em arquivo, fechar janela nem gravar na memória
    for nome in ("abrir_site", "abrir_programa", "abrir_arquivo", "mover", "apagar", "fechar_aba", "fechar_janela", "focar_janela"):
        assert "Ação bloqueada" in vigia.decidir(f"mcp__jarvis__{nome}", {}), nome
    assert "Gravação bloqueada" in vigia.decidir("mcp__jarvis__memoria_salvar", {"titulo": "a", "conteudo": "b"})
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__fechar_aba", {}) is None


def test_rolar_e_midia_nao_contaminam_o_turno(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path)
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__rolar", {"direcao": "baixo"}) is None
    assert vigia.decidir("mcp__jarvis__midia", {"comando": "tocar_pausar"}) is None
    assert vigia.decidir("mcp__jarvis__fechar_aba", {"titulo": "instagram"}) is None
    assert vigia.decidir("mcp__jarvis__memoria_salvar", {"titulo": "a", "conteudo": "b"}) is None


def test_limite_de_acoes_na_pagina_por_turno(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path)
    vigia = sessao.guarda
    for _ in range(40):
        assert vigia.decidir("mcp__jarvis__tela_clicar", {"x": 1, "y": 1}) is None
    assert "Limite de 40 ações" in vigia.decidir("mcp__jarvis__tela_clicar", {"x": 1, "y": 1})
    assert "Limite de 40 ações" in vigia.decidir("mcp__jarvis__tela_ver", {})
    assert "Limite de 40 ações" in vigia.decidir("mcp__jarvis__rolar", {"direcao": "baixo"})
    assert vigia.decidir("mcp__jarvis__agora", {}) is None  # o resto continua respondendo
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__tela_clicar", {"x": 1, "y": 1}) is None


def test_tela_desligada_tira_as_ferramentas_e_avisa_no_prompt(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path, ver_paginas=False)
    permitidas = sessao.opcoes.allowed_tools
    assert "mcp__jarvis__rolar" in permitidas and "mcp__jarvis__midia" in permitidas
    assert not [n for n in permitidas if "tela_ver" in n or "tela_clicar" in n or "tela_digitar" in n]
    assert "JARVIS_TELA=nao" in Path(sessao.opcoes.system_prompt["path"]).read_text(encoding="utf-8")


def test_com_confirmacao_digitada_a_pagina_tambem_pergunta(tmp_path):
    """Com JARVIS_CONFIRMAR=sempre a guarda não bloqueia nada: quem decide é o s do usuário, ação por ação."""
    sessao, _ = sessao_com_janelas(tmp_path, confirmar_acoes=True)
    vigia = sessao.guarda
    vigia.decidir("mcp__jarvis__tela_ver", {})
    assert vigia.decidir("mcp__jarvis__tela_clicar", {"x": 1, "y": 1}) is None
    assert vigia.decidir("mcp__jarvis__fechar_aba", {}) is None


def test_o_binario_nao_guarda_a_conversa_nem_as_fotos_em_disco(tmp_path):
    assert montar(tmp_path).extra_args == {"no-session-persistence": None}


def test_preferencias_entram_inteiras_no_prompt_antes_das_licoes(tmp_path):
    memoria = Memoria(tmp_path / "memoria")
    vazio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "## Preferências" in vazio and vazio.count("Nenhuma ainda.") == 2
    memoria.salvar("Navegador", "Abrir sites sempre no Chrome.", categoria="preferencia")
    cheio = Path(montar(tmp_path, memoria=memoria).system_prompt["path"]).read_text(encoding="utf-8")
    assert "- Navegador: Abrir sites sempre no Chrome." in cheio
    assert cheio.index("## Preferências (como") < cheio.index("## Lições (erros") < cheio.index("## Memória (o que você já sabe")


# --- janelas de programas -------------------------------------------------------------


def test_ir_a_um_programa_so_em_turno_limpo(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path)
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__programa_ver", {"programa": "excel"}) is None  # ordem direta do usuário
    for nome in ("tela_clicar", "tela_digitar", "tela_tecla", "rolar", "tela_ver"):
        assert vigia.decidir(f"mcp__jarvis__{nome}", {}) is None, nome
    # a foto do Excel é conteúdo de fora: daí em diante, nada de ações fora da janela nem de trocar de programa
    assert "Ação bloqueada" in vigia.decidir("mcp__jarvis__programa_ver", {"programa": "word"})
    assert "Ação bloqueada" in vigia.decidir("mcp__jarvis__abrir_programa", {"nome": "calculadora"})
    assert "Gravação bloqueada" in vigia.decidir("mcp__jarvis__memoria_salvar", {"titulo": "a", "conteudo": "b"})


@pytest.mark.parametrize("leitura", ["WebSearch", "Read", "mcp__jarvis__tela_ver", "mcp__jarvis__historico_buscar", "mcp__jarvis__janelas_listar"])
def test_o_que_foi_lido_nao_leva_o_jarvis_a_um_programa(tmp_path, leitura):
    sessao, _ = sessao_com_janelas(tmp_path)
    vigia = sessao.guarda
    entrada = {"file_path": str(cfg(tmp_path).pastas[0] / "a.txt")} if leitura == "Read" else {"query": "x", "termo": "x"}
    assert vigia.decidir(leitura, entrada) is None
    assert "Ação bloqueada" in vigia.decidir("mcp__jarvis__programa_ver", {"programa": "excel"})
    vigia.novo_turno()
    assert vigia.decidir("mcp__jarvis__programa_ver", {"programa": "excel"}) is None


def test_com_confirmacao_digitada_ir_a_um_programa_continua_exigindo_turno_limpo(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path, confirmar_acoes=True)
    vigia = sessao.guarda
    vigia.decidir("WebSearch", {"query": "x"})
    assert vigia.decidir("mcp__jarvis__abrir_site", {"url": "https://x"}) is None  # essa o usuário confirma no teclado
    assert "Ação bloqueada" in vigia.decidir("mcp__jarvis__programa_ver", {"programa": "excel"})


def test_programa_ver_conta_no_limite_do_turno(tmp_path):
    sessao, _ = sessao_com_janelas(tmp_path)
    vigia = sessao.guarda
    vigia.decidir("mcp__jarvis__programa_ver", {"programa": "excel"})
    assert vigia.acoes_na_pagina == 1


def test_prompt_diz_em_quais_programas_ele_pode_mexer(tmp_path):
    from jarvis.acoes import PROGRAMAS_PADRAO
    from jarvis.janelas import Janelas
    from sistema_falso import mesa

    controle = Janelas(mesa(), PROGRAMAS_PADRAO, ["spotify"])
    sessao = agente.montar_sessao(
        cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", janelas=controle
    )
    prompt = Path(sessao.opcoes.system_prompt["path"]).read_text(encoding="utf-8")
    assert "com programa_ver (pelo nome do executável): calc, excel, mspaint, notepad, outlook, powerpnt, spotify, winword." in prompt
    assert "explorer" not in prompt.split("programa_ver (pelo nome")[1].split("\n")[0]


def test_ler_lista_de_programas_a_mais():
    assert agente.ler_lista(" spotify; vlc.exe ,applicationframehost;; ") == ["spotify", "vlc.exe", "applicationframehost"]
    assert agente.ler_lista("") == []


def test_painel_mostrar_entra_so_na_sessao_com_painel_e_vale_depois_de_uma_pesquisa(tmp_path):
    assert "mcp__jarvis__painel_mostrar" not in montar(tmp_path).allowed_tools
    memoria = Memoria(tmp_path / "memoria")
    sessao = agente.montar_sessao(cfg(tmp_path), memoria, Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", mostrar_paineis=lambda lista: "ok")
    assert "mcp__jarvis__painel_mostrar" in sessao.opcoes.allowed_tools
    # Mostrar o que pesquisou é o uso normal: a leitura de conteúdo de fora não bloqueia o cartão (bloqueia gravar na memória).
    guarda = sessao.guarda
    guarda.novo_turno()
    assert guarda.decidir("WebSearch", {"query": "previsão do tempo em Maragogi"}) is None
    assert guarda.decidir("mcp__jarvis__painel_mostrar", {"paineis": [{"titulo": "Maragogi"}]}) is None
    assert guarda.decidir("mcp__jarvis__memoria_salvar", {"titulo": "x", "conteudo": "y"}) is not None
