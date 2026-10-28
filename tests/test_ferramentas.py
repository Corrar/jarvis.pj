import asyncio
import re
from datetime import datetime

import pytest

from jarvis import ferramentas
from jarvis.memoria import Memoria
from jarvis.registro import Registro, Turno


@pytest.fixture
def caixa(tmp_path):
    memoria, registro = Memoria(tmp_path / "memoria"), Registro(":memory:")
    lista = ferramentas.criar_ferramentas(memoria, registro)
    return {f.name: f for f in lista}, memoria, registro


def chamar(ferramenta, **args):
    saida = asyncio.run(ferramenta.handler(args))
    (bloco,) = saida["content"]
    return bloco["text"], bool(saida.get("is_error"))


def test_nomes_e_classificacao(caixa):
    por_nome, _, _ = caixa
    assert list(por_nome) == [
        "agora", "info_sistema", "memoria_salvar", "memoria_ler", "memoria_listar", "memoria_apagar", "historico_buscar",
    ]
    assert set(ferramentas.GRAVAM) <= set(por_nome)
    # as outras só existem com controle de janelas, com a oficina de trabalhos ou com a previsão do tempo
    assert set(ferramentas.CONTAMINAM) - set(por_nome) == {
        "janelas_listar", "tela_ver", "programa_ver", "projeto_situacao", "trabalho_situacao", "skills_listar", "clima", "missao_situacao", "protheus_catalogo", "protheus_modelo", "protheus_saude",
    }  # fmt: skip
    assert ferramentas.nomes_liberados(list(por_nome.values()))[0] == "mcp__jarvis__agora"
    # Quem grava não pode estar marcado como somente leitura.
    for nome in ferramentas.GRAVAM:
        anotacoes = por_nome[nome].annotations
        assert getattr(anotacoes, "readOnlyHint", getattr(anotacoes, "read_only_hint", None)) is False


def test_agora_e_info_sistema(caixa):
    por_nome, _, _ = caixa
    texto, erro = chamar(por_nome["agora"])
    assert not erro and re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", texto)
    assert "feira" in texto or "sábado" in texto or "domingo" in texto
    texto, erro = chamar(por_nome["info_sistema"])
    assert not erro and "Sistema:" in texto and "Disco" in texto


def test_ciclo_da_memoria(caixa):
    por_nome, memoria, _ = caixa
    assert chamar(por_nome["memoria_listar"]) == ("A memória está vazia.", False)
    texto, erro = chamar(por_nome["memoria_salvar"], titulo="Apelido", conteudo="Prefere ser chamado de chefe.")
    assert not erro and "'apelido'" in texto and "'fato'" in texto
    assert (memoria.pasta / "apelido.md").is_file()
    assert chamar(por_nome["memoria_listar"]) == ("[fato]\n- apelido: Apelido — Prefere ser chamado de chefe.", False)
    assert chamar(por_nome["memoria_ler"], nome="apelido") == ("# Apelido\n\nPrefere ser chamado de chefe.\n", False)
    texto, erro = chamar(por_nome["memoria_apagar"], nome="Apelido")
    assert not erro and not (memoria.pasta / "apelido.md").exists()


def test_erros_da_memoria_voltam_como_erro_da_ferramenta(caixa):
    por_nome, _, _ = caixa
    assert chamar(por_nome["memoria_ler"], nome="nada")[1] is True
    assert chamar(por_nome["memoria_apagar"], nome="nada")[1] is True
    texto, erro = chamar(por_nome["memoria_salvar"], titulo="Chave", conteudo="sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx")
    assert erro and "chave ou senha" in texto
    assert chamar(por_nome["memoria_salvar"])[1] is True  # sem argumentos


def test_historico_buscar(caixa):
    por_nome, _, registro = caixa
    assert chamar(por_nome["historico_buscar"], termo="d")[1] is True
    texto, erro = chamar(por_nome["historico_buscar"], termo="dólar")
    assert not erro and "Nenhuma conversa" in texto
    registro.gravar(Turno("cotação do dólar", resposta="Está em R$ 4,98.", inicio=datetime(2026, 10, 7, 9, 58)))
    texto, erro = chamar(por_nome["historico_buscar"], termo="dólar")
    assert not erro
    assert texto == "[07/10/2026 09:58]\nUsuário: cotação do dólar\nJarvis: Está em R$ 4,98."


def test_licao_pelas_ferramentas(caixa):
    por_nome, memoria, _ = caixa
    texto, erro = chamar(por_nome["memoria_salvar"], titulo="Fontes", conteudo="Citar pelo nome.", categoria="licao")
    assert not erro and "'licao'" in texto
    assert (memoria.pasta / "licoes" / "fontes.md").is_file()
    chamar(por_nome["memoria_salvar"], titulo="Apelido", conteudo="Chefe.")
    listagem, _ = chamar(por_nome["memoria_listar"])
    assert listagem == "[fato]\n- apelido: Apelido — Chefe.\n\n[licao]\n- fontes: Fontes — Citar pelo nome."
    assert chamar(por_nome["memoria_ler"], nome="fontes", categoria="licao")[0] == "# Fontes\n\nCitar pelo nome.\n"
    assert chamar(por_nome["memoria_ler"], nome="fontes")[1] is True  # não é fato
    assert chamar(por_nome["memoria_salvar"], titulo="x", conteudo="y", categoria="outra")[1] is True
    assert chamar(por_nome["memoria_apagar"], nome="fontes", categoria="licao")[1] is False
    assert not (memoria.pasta / "licoes" / "fontes.md").exists()


# --- ações: nada roda sem a confirmação ------------------------------------


class Perguntas:
    """Faz o papel do usuário: registra o que foi perguntado e responde o que o teste mandar."""

    def __init__(self, resposta: bool):
        self.resposta, self.feitas = resposta, []

    async def __call__(self, titulo, linhas):
        self.feitas.append((titulo, list(linhas)))
        return self.resposta


@pytest.fixture
def area(tmp_path):
    import os

    pasta = tmp_path / "Documents"
    (pasta / "sub").mkdir(parents=True)
    (pasta / "a.txt").write_text("a", encoding="utf-8")
    return type(pasta)(os.path.realpath(pasta))


def com_acoes(area, tmp_path, confirmar, sistema=None, **extras):
    from jarvis.acoes import Acoes
    from jarvis.janelas import Janelas
    from sistema_falso import mesa

    abertos = []
    acoes = Acoes([area], abrir=abertos.append, iniciar=abertos.append, navegar=abertos.append, lixeira=abertos.append, **extras)
    janelas = Janelas(sistema or mesa(), acoes.programas)
    lista = ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), acoes, confirmar, janelas)
    return {f.name: f for f in lista}, abertos


def test_sem_acoes_as_ferramentas_de_acao_nao_existem(caixa):
    por_nome, _, _ = caixa
    assert not set(ferramentas.AGEM) & set(por_nome)


def test_toda_ferramenta_de_acao_esta_classificada(area, tmp_path):
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True))
    leitura = {"agora", "info_sistema", "memoria_salvar", "memoria_ler", "memoria_listar", "memoria_apagar", "historico_buscar", "janelas_listar"}
    # Toda ferramenta que age está em uma das duas classes: bloqueada depois de ler conteúdo, ou de dentro da página.
    # sem oficina, terminal, agenda, previsão do tempo nem missões nesta sessão
    de_programar = {*ferramentas.DE_TRABALHO, *ferramentas.DE_SKILL, *ferramentas.DE_TERMINAL, *ferramentas.DE_ROTINA, *ferramentas.DE_CLIMA, *ferramentas.DE_MISSAO, *ferramentas.DE_PROTHEUS}
    classificadas = {*ferramentas.AGEM, *ferramentas.NA_PAGINA, *ferramentas.SO_EM_TURNO_LIMPO, "tela_ver"}
    assert set(por_nome) - leitura == classificadas - de_programar
    assert not set(ferramentas.AGEM) & set(ferramentas.NA_PAGINA)
    assert not set(ferramentas.SO_EM_TURNO_LIMPO) & {*ferramentas.AGEM, *ferramentas.NA_PAGINA}
    assert set(ferramentas.CONTAMINAM) - de_programar <= set(por_nome)
    for nome in set(ferramentas.AGEM) - de_programar:
        anotacoes = por_nome[nome].annotations
        assert getattr(anotacoes, "readOnlyHint", getattr(anotacoes, "read_only_hint", None)) is False


def test_confirmada_a_acao_roda(area, tmp_path):
    usuario = Perguntas(True)
    por_nome, _ = com_acoes(area, tmp_path, usuario)
    texto, erro = chamar(por_nome["mover"], itens=[{"origem": str(area / "a.txt"), "destino": str(area / "sub")}])
    assert not erro and "movido" in texto
    assert (area / "sub" / "a.txt").is_file() and not (area / "a.txt").exists()
    ((titulo, linhas),) = usuario.feitas
    assert titulo == "mover 1 item" and str(area / "sub" / "a.txt") in linhas[0]


def test_recusada_a_acao_nao_roda(area, tmp_path):
    usuario = Perguntas(False)
    por_nome, abertos = com_acoes(area, tmp_path, usuario)
    texto, erro = chamar(por_nome["mover"], itens=[{"origem": str(area / "a.txt"), "destino": str(area / "sub")}])
    assert erro and "não confirmou" in texto
    assert (area / "a.txt").is_file() and not (area / "sub" / "a.txt").exists()
    texto, erro = chamar(por_nome["apagar"], caminhos=[str(area / "a.txt")])
    assert erro and abertos == []
    texto, erro = chamar(por_nome["abrir_site"], url="https://exemplo.com.br")
    assert erro and abertos == []
    assert len(usuario.feitas) == 3


def test_sem_como_confirmar_nada_roda(area, tmp_path):
    por_nome, abertos = com_acoes(area, tmp_path, None)
    for nome, args in (
        ("abrir_site", {"url": "https://exemplo.com.br"}),
        ("abrir_arquivo", {"caminho": str(area / "a.txt")}),
        ("abrir_programa", {"nome": "calculadora"}),
        ("criar_pasta", {"caminho": str(area / "nova")}),
        ("mover", {"itens": [{"origem": str(area / "a.txt"), "destino": str(area / "b.txt")}]}),
        ("copiar", {"itens": [{"origem": str(area / "a.txt"), "destino": str(area / "b.txt")}]}),
        ("apagar", {"caminhos": [str(area / "a.txt")]}),
        ("fechar_aba", {"titulo": "instagram"}),
        ("reabrir_aba", {}),
        ("focar_janela", {"janela": "excel"}),
        ("fechar_janela", {"janela": "excel"}),
    ):
        texto, erro = chamar(por_nome[nome], **args)
        assert erro and "Nada foi feito" in texto, nome
    assert abertos == [] and sorted(p.name for p in area.iterdir()) == ["a.txt", "sub"]


def test_pedido_invalido_nem_chega_ao_usuario(area, tmp_path):
    usuario = Perguntas(True)
    por_nome, abertos = com_acoes(area, tmp_path, usuario)
    fora = tmp_path / "fora.txt"
    fora.write_text("x", encoding="utf-8")
    assert chamar(por_nome["mover"], itens=[{"origem": str(fora), "destino": str(area)}])[1] is True
    assert chamar(por_nome["abrir_site"], url="file:///C:/Windows/System32/calc.exe")[1] is True
    assert chamar(por_nome["abrir_programa"], nome="powershell")[1] is True
    assert chamar(por_nome["apagar"], caminhos=[])[1] is True
    assert usuario.feitas == [] and abertos == [] and fora.exists()


def test_falha_do_sistema_vira_erro_legivel(area, tmp_path):
    def nao_abre(_alvo):
        raise FileNotFoundError("programa não instalado")

    from jarvis.acoes import Acoes

    acoes = Acoes([area], iniciar=nao_abre)
    lista = ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), acoes, Perguntas(True))
    por_nome = {f.name: f for f in lista}
    texto, erro = chamar(por_nome["abrir_programa"], nome="Excel")
    assert erro and "Não consegui abrir um programa" in texto and "não instalado" in texto


# --- janelas pelas ferramentas ------------------------------------------------


def test_fechar_aba_pela_ferramenta(area, tmp_path):
    from sistema_falso import mesa

    sistema, usuario = mesa(), Perguntas(True)
    por_nome, _ = com_acoes(area, tmp_path, usuario, sistema=sistema)
    texto, erro = chamar(por_nome["fechar_aba"], titulo="instagram")
    assert not erro and texto == "Fechei a aba 'instagram' no chrome."
    assert sistema.janelas[10].abas == ["Gmail", "Vercel", "YouTube"]
    texto, erro = chamar(por_nome["reabrir_aba"])
    assert not erro and "Instagram" in sistema.janelas[10].abas
    texto, erro = chamar(por_nome["fechar_aba"], titulo="netflix")
    assert erro and "Não consegui fechar uma aba do navegador" in texto and "não achei" in texto
    assert [t for t, _ in usuario.feitas] == ["fechar uma aba do navegador", "reabrir a última aba fechada", "fechar uma aba do navegador"]
    texto, erro = chamar(por_nome["fechar_aba"])  # "feche essa aba"
    assert not erro and texto == "Fechei a aba que estava à mostra no chrome."
    # a aba reaberta era a que estava à mostra; a procura pelo netflix voltou para ela
    assert usuario.feitas[-1] == ("fechar uma aba do navegador", ["Instagram - Google Chrome"])
    assert por_nome["fechar_aba"].input_schema.get("required", []) == []


def test_listar_e_fechar_janela_pela_ferramenta(area, tmp_path):
    from sistema_falso import mesa

    sistema = mesa()
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=sistema)
    texto, erro = chamar(por_nome["janelas_listar"])
    assert not erro
    assert texto.splitlines() == [
        "chrome: Gmail - Google Chrome",
        "chrome: Documentação do Python - Google Chrome",
        "edge: Mercado Livre — Microsoft Edge",
        "notepad: Sem título - Bloco de Notas",
        "excel: romaneio.xlsx - Excel",
    ]
    assert chamar(por_nome["fechar_janela"], janela="chrome")[1] is True  # duas janelas, sem 'todas'
    assert chamar(por_nome["fechar_janela"], janela="chrome", todas=True)[1] is False
    assert chamar(por_nome["focar_janela"], janela="excel")[1] is False
    assert sistema.pedidos_de_fechamento == [10, 11] and sistema.frente == 40


def test_defeito_na_camada_do_windows_vira_erro_legivel_e_vai_para_o_log(area, tmp_path, caplog):
    """A parte que fala com o Windows nunca rodou em teste de verdade: se quebrar, o usuário precisa saber onde olhar."""
    from sistema_falso import mesa

    class Quebrado(type(mesa())):
        def teclas(self, *codigos):
            raise TypeError("argumento errado para keybd_event")

    sistema = Quebrado(mesa().janelas)
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=sistema)
    texto, erro = chamar(por_nome["fechar_aba"], titulo="gmail")
    assert erro and "erro interno (TypeError: argumento errado" in texto and "jarvis.log" in texto
    assert "Falha inesperada ao fechar uma aba do navegador" in caplog.text
    assert sistema.frente == 1  # mesmo assim devolveu o foco ao terminal

    def listar_quebrado():
        raise AttributeError("função inexistente na user32")

    sistema.listar = listar_quebrado
    texto, erro = chamar(por_nome["janelas_listar"])
    assert erro and "AttributeError" in texto
    texto, erro = chamar(por_nome["fechar_aba"], titulo="gmail")  # quebra ainda no planejamento
    assert erro and "Erro interno ao preparar a ação" in texto and "Nada foi feito" in texto


# --- dentro da página, pelas ferramentas ------------------------------------------------


def chamar_cru(ferramenta, **args):
    return asyncio.run(ferramenta.handler(args))


def test_tela_ver_devolve_a_foto_como_imagem(area, tmp_path):
    import base64

    from sistema_falso import mesa

    sistema, usuario = mesa(), Perguntas(True)
    por_nome, _ = com_acoes(area, tmp_path, usuario, sistema=sistema)
    saida = chamar_cru(por_nome["tela_ver"])
    texto, imagem = saida["content"]
    assert not saida.get("is_error")
    assert "1264 por 790" in texto["text"] and "conteúdo de fora" in texto["text"]
    assert "Gmail" not in texto["text"]  # o título da aba não vai em texto; o modelo vê a foto
    assert imagem["type"] == "image" and imagem["mimeType"] == "image/jpeg"
    assert base64.b64decode(imagem["data"]) == b"JPEG 1264x790 de Gmail - Google Chrome"
    assert usuario.feitas == []  # ver não pede confirmação: não muda nada na página


def test_clicar_e_digitar_devolvem_a_foto_de_depois(area, tmp_path):
    from sistema_falso import mesa

    sistema, usuario = mesa(), Perguntas(True)
    por_nome, _ = com_acoes(area, tmp_path, usuario, sistema=sistema)
    texto, erro = chamar(por_nome["tela_clicar"], x=10, y=10)
    assert erro and "Veja a janela antes" in texto and usuario.feitas == []
    chamar_cru(por_nome["tela_ver"])
    saida = chamar_cru(por_nome["tela_clicar"], x=632, y=395)
    assert [b["type"] for b in saida["content"]] == ["text", "image"]
    assert saida["content"][0]["text"].startswith("Cliquei em (632, 395).")
    assert sistema.cliques == [(10, 800, 500, False)]
    saida = chamar_cru(por_nome["tela_digitar"], texto="lofi", enter=True)
    assert [b["type"] for b in saida["content"]] == ["text", "image"] and sistema.digitado == [(10, "lofi")]
    saida = chamar_cru(por_nome["tela_tecla"], tecla="esc")
    assert [b["type"] for b in saida["content"]] == ["text", "image"]
    assert [t for t, _ in usuario.feitas] == ["clicar na janela", "digitar na janela", "apertar uma tecla na janela"]
    assert len(sistema.fotos) == 4  # a primeira e uma depois de cada ação


def test_rolar_e_midia_sem_ter_visto_a_pagina_devolvem_so_texto(area, tmp_path):
    from sistema_falso import mesa

    sistema = mesa()
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=sistema)
    assert chamar(por_nome["rolar"], direcao="baixo") == ("Rolei a página para baixo.", False)
    assert chamar(por_nome["midia"], comando="tocar_pausar") == ("Enviei o comando de mídia: tocar_pausar.", False)
    assert chamar(por_nome["tela_tecla"], tecla="k") == ("Apertei a tecla k.", False)
    assert sistema.fotos == []  # ninguém olhou a página: o turno continua limpo
    texto, erro = chamar(por_nome["rolar"], direcao="diagonal")
    assert erro and "direção" in texto


def test_rolar_depois_de_ver_devolve_a_pagina_rolada(area, tmp_path):
    from sistema_falso import mesa

    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=mesa())
    chamar_cru(por_nome["tela_ver"])
    saida = chamar_cru(por_nome["rolar"], direcao="baixo", telas=2)
    assert [b["type"] for b in saida["content"]] == ["text", "image"]


def test_recusa_do_usuario_vale_para_a_pagina(area, tmp_path):
    from sistema_falso import mesa

    sistema = mesa()
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(False), sistema=sistema)  # JARVIS_CONFIRMAR=sempre, e ele disse não
    chamar_cru(por_nome["tela_ver"])
    texto, erro = chamar(por_nome["tela_clicar"], x=5, y=5)
    assert erro and "não confirmou" in texto and sistema.cliques == []
    texto, erro = chamar(por_nome["rolar"], direcao="baixo")
    assert erro and sistema.rodadas == []


def test_pagina_reservada_e_falhas_viram_erro_legivel(area, tmp_path):
    from sistema_falso import Navegador, Programa, SistemaFalso

    sistema = SistemaFalso({1: Programa("WindowsTerminal.exe", "jarvis"), 10: Navegador("chrome.exe", ["Nubank", "Gmail"])})
    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=sistema)
    texto, erro = chamar(por_nome["tela_ver"])
    assert erro and "banco, pagamento ou senha" in texto
    sistema.janelas[10].ativa = 1
    sistema.frente = 1  # o terminal voltou para a frente, e o Windows não deixa trazer o navegador
    sistema.nao_focam = {10}
    texto, erro = chamar(por_nome["tela_ver"])
    assert erro and "Não consegui ver a página" in texto and "não deixou" in texto

    def sem_pillow(*_a):
        raise OSError("falta a biblioteca Pillow; rode: pip install -r requirements.txt")

    sistema.nao_focam, sistema.fotografar = set(), sem_pillow
    texto, erro = chamar(por_nome["tela_ver"])
    assert erro and "Pillow" in texto


def test_com_a_tela_desligada_so_sobram_rolar_e_midia(area, tmp_path):
    from jarvis.acoes import Acoes
    from jarvis.janelas import Janelas
    from sistema_falso import mesa

    lista = ferramentas.criar_ferramentas(
        Memoria(tmp_path / "m"), Registro(":memory:"), Acoes([area]), Perguntas(True), Janelas(mesa()), ver_paginas=False
    )
    nomes = {f.name for f in lista}
    assert {"rolar", "midia", "fechar_aba"} <= nomes
    assert not {"tela_ver", "programa_ver", "tela_clicar", "tela_digitar", "tela_tecla"} & nomes


def test_esquemas_das_ferramentas_de_pagina(area, tmp_path):
    from jarvis.janelas import MIDIA, TECLAS_DE_PAGINA
    from sistema_falso import mesa

    por_nome, _ = com_acoes(area, tmp_path, Perguntas(True), sistema=mesa())
    clicar = por_nome["tela_clicar"].input_schema
    assert clicar["required"] == ["x", "y"] and clicar["properties"]["x"]["type"] == "integer"
    assert por_nome["tela_tecla"].input_schema["properties"]["tecla"]["enum"] == list(TECLAS_DE_PAGINA)
    assert por_nome["midia"].input_schema["properties"]["comando"]["enum"] == list(MIDIA)
    assert por_nome["rolar"].input_schema["properties"]["direcao"]["enum"] == ["baixo", "cima", "topo", "fim"]
    assert por_nome["tela_ver"].input_schema["required"] == []


def test_preferencia_pelas_ferramentas(caixa):
    por_nome, memoria, _ = caixa
    texto, erro = chamar(por_nome["memoria_salvar"], titulo="Navegador", conteudo="Abrir sempre no Chrome.", categoria="preferencia")
    assert not erro and "'preferencia'" in texto
    assert (memoria.pasta / "preferencias" / "navegador.md").is_file()
    assert chamar(por_nome["memoria_listar"])[0] == "[preferencia]\n- navegador: Navegador — Abrir sempre no Chrome."
    assert por_nome["memoria_salvar"].input_schema["properties"]["categoria"]["enum"] == ["fato", "preferencia", "licao"]
    texto, erro = chamar(por_nome["memoria_salvar"], titulo="x", conteudo="y", categoria="manual")
    assert erro and "Categoria desconhecida" in texto


def test_programa_ver_pela_ferramenta(area, tmp_path):
    from sistema_falso import Programa, mesa

    sistema, usuario = mesa(), Perguntas(True)
    sistema.janelas[50] = Programa("cmd.exe", "Prompt de Comando")
    por_nome, _ = com_acoes(area, tmp_path, usuario, sistema=sistema)
    saida = chamar_cru(por_nome["programa_ver"], programa="excel")
    assert [b["type"] for b in saida["content"]] == ["text", "image"] and not saida.get("is_error")
    assert "romaneio" not in saida["content"][0]["text"]  # o título da janela não vai em texto
    assert sistema.frente == 40 and usuario.feitas == []
    saida = chamar_cru(por_nome["tela_digitar"], texto="42", enter=True)
    assert [b["type"] for b in saida["content"]] == ["text", "image"] and sistema.digitado == [(40, "42")]
    saida = chamar_cru(por_nome["tela_ver"])
    assert [b["type"] for b in saida["content"]] == ["text", "image"] and sistema.frente == 40
    texto, erro = chamar(por_nome["programa_ver"], programa="prompt de comando")
    assert erro and "ficam sempre de fora" in texto
    assert por_nome["programa_ver"].input_schema["required"] == ["programa"]
    assert "salvar" in por_nome["tela_tecla"].input_schema["properties"]["tecla"]["enum"]


# --- cartões no painel --------------------------------------------------------------------------------


def test_painel_mostrar_so_existe_com_painel_e_entrega_os_cartoes(tmp_path):
    memoria, registro = Memoria(tmp_path / "memoria"), Registro(":memory:")
    assert "painel_mostrar" not in {f.name for f in ferramentas.criar_ferramentas(memoria, registro)}
    recebidos = []

    def mostrar(lista):
        recebidos.append(lista)
        return f"{len(lista)} no painel."

    por_nome = {f.name: f for f in ferramentas.criar_ferramentas(memoria, registro, mostrar_paineis=mostrar)}
    mostrar_ = por_nome["painel_mostrar"]
    cartoes = [{"titulo": "Maragogi", "categoria": "Previsão · Clima", "icone": "sol", "destaque": "27°"}]
    assert chamar(mostrar_, paineis=cartoes) == ("1 no painel.", False)
    assert chamar(mostrar_, paineis=[]) == ("0 no painel.", False)  # lista vazia fecha os cartões
    assert recebidos == [cartoes, []]
    for torto in ({}, {"paineis": "clima"}, {"paineis": None}):
        texto, erro = chamar(mostrar_, **torto)
        assert erro and "paineis" in texto
    assert len(recebidos) == 2


def test_painel_mostrar_nao_derruba_a_conversa_se_o_painel_falhar(tmp_path, caplog):
    def mostrar(lista):
        raise OSError("porta fechada")

    lista = ferramentas.criar_ferramentas(Memoria(tmp_path / "memoria"), Registro(":memory:"), mostrar_paineis=mostrar)
    with caplog.at_level("ERROR"):
        texto, erro = chamar({f.name: f for f in lista}["painel_mostrar"], paineis=[{"titulo": "A"}])
    assert erro and "porta fechada" in texto and "cartões" in caplog.text


def test_painel_mostrar_descreve_o_cartao_como_a_pagina_o_desenha(tmp_path):
    from jarvis.painel import ICONES_DOS_PAINEIS, MAX_PAINEIS, limpar_paineis

    lista = ferramentas.criar_ferramentas(Memoria(tmp_path / "memoria"), Registro(":memory:"), mostrar_paineis=lambda _l: "")
    ferramenta = {f.name: f for f in lista}["painel_mostrar"]
    esquema = ferramenta.input_schema
    assert esquema["required"] == ["paineis"] and esquema["properties"]["paineis"]["maxItems"] == MAX_PAINEIS
    cartao = esquema["properties"]["paineis"]["items"]
    assert cartao["properties"]["icone"]["enum"] == list(ICONES_DOS_PAINEIS)
    assert cartao["properties"]["previsao"]["items"]["properties"]["icone"]["enum"] == list(ICONES_DOS_PAINEIS)
    assert "imagemUrl" not in cartao["properties"]  # a página não busca imagem de fora
    # Tudo o que a ferramenta oferece ao modelo é campo que o painel aceita, e nada do que ele aceita ficou de fora.
    cheio = {
        "titulo": "T", "categoria": "C", "icone": "sol", "badge": "b", "destaque": "1", "sub": "s", "itens": [{"r": "a", "v": "b"}],
        "barra": 50, "grafico": [1, 2], "graficoRotulos": ["a", "b"], "previsao": [{"dia": "seg.", "icone": "sol", "max": "1", "min": "0"}],
        "lista": [{"r": "a", "v": "b"}], "manchete": "m", "imagem": "i", "link": "https://exemplo.com/a", "quando": "2h", "texto": "t",
        "fonte": "f", "acao": "a", "escuro": True,
    }  # fmt: skip
    assert set(cheio) == set(cartao["properties"])
    assert limpar_paineis([cheio]) == [cheio]
    # Não age na máquina: não entra nas classes que a guarda bloqueia depois de uma pesquisa (é quando há o que mostrar).
    for classe in (ferramentas.AGEM, ferramentas.NA_PAGINA, ferramentas.SO_EM_TURNO_LIMPO, ferramentas.GRAVAM, ferramentas.SEMPRE_COM_S, ferramentas.CONTAMINAM):
        assert "painel_mostrar" not in classe
    assert ferramentas.DE_PAINEL == ("painel_mostrar",)
    assert "nunca invente" in ferramenta.description
