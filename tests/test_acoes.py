import os
from pathlib import Path

import pytest

from jarvis.acoes import EXTENSOES_EXECUTAVEIS, MAX_ITENS, Acoes, ErroAcao, chave


@pytest.fixture
def area(tmp_path):
    docs = Path(os.path.realpath(tmp_path / "Documents"))
    baixados = Path(os.path.realpath(tmp_path / "Downloads"))
    fora = Path(os.path.realpath(tmp_path / "fora"))
    for pasta in (docs / "projeto", baixados, fora):
        pasta.mkdir(parents=True)
    (docs / "a.txt").write_text("a", encoding="utf-8")
    (docs / "projeto" / "b.txt").write_text("b", encoding="utf-8")
    (baixados / "relatorio.pdf").write_text("pdf", encoding="utf-8")
    (baixados / "instalador.exe").write_text("mz", encoding="utf-8")
    (fora / "segredo.txt").write_text("x", encoding="utf-8")
    return docs, baixados, fora


class Sistema:
    """No lugar do sistema operacional: só anota o que seria aberto ou apagado."""

    def __init__(self):
        self.abertos, self.programas, self.sites, self.lixeira = [], [], [], []

    def acoes(self, pastas, **extras):
        return Acoes(
            pastas,
            abrir=self.abertos.append,
            iniciar=self.programas.append,
            navegar=self.sites.append,
            lixeira=self.lixeira.append,
            **extras,
        )


@pytest.fixture
def sistema():
    return Sistema()


@pytest.fixture
def acoes(area, sistema):
    docs, baixados, _ = area
    return sistema.acoes([docs, baixados])


def par(origem, destino):
    return {"origem": str(origem), "destino": str(destino)}


# --- planejar não mexe em nada ---------------------------------------------


def test_planejar_nao_executa(area, acoes, sistema):
    docs, baixados, _ = area
    acoes.mover([par(docs / "a.txt", baixados)])
    acoes.apagar([str(docs / "a.txt")])
    acoes.criar_pasta(str(docs / "nova"))
    acoes.abrir_site("https://exemplo.com.br")
    acoes.abrir_arquivo(str(docs / "a.txt"))
    acoes.abrir_programa("calculadora")
    assert (docs / "a.txt").is_file() and not (docs / "nova").exists()
    assert sistema.abertos == sistema.programas == sistema.sites == sistema.lixeira == []


# --- abrir ------------------------------------------------------------------


def test_abrir_site(acoes, sistema):
    plano = acoes.abrir_site("  https://www.infomoney.com.br/cotacoes/dolar  ")
    assert plano.titulo == "abrir um site"
    assert plano.linhas == [
        "https://www.infomoney.com.br/cotacoes/dolar  (no navegador padrão)",
        "(domínio: www.infomoney.com.br)",
    ]
    assert plano.executar() == ["Abri https://www.infomoney.com.br/cotacoes/dolar no navegador padrão."]
    assert sistema.sites == ["https://www.infomoney.com.br/cotacoes/dolar"]


def test_abrir_site_no_navegador_pedido(area, sistema):
    docs, _, _ = area
    acoes = sistema.acoes([docs], navegar_em=lambda comando, url: sistema.programas.append((comando, url)))
    assert acoes.navegadores == ["chrome", "edge", "firefox"]
    plano = acoes.abrir_site("https://vercel.com", navegador=" Chrome ")
    assert plano.linhas[0] == "https://vercel.com  (no chrome)"
    assert plano.executar() == ["Abri https://vercel.com no chrome."]
    acoes.abrir_site("https://vercel.com/docs?a=1&b=%20x", navegador="edge").executar()
    assert sistema.programas == [("chrome", "https://vercel.com"), ("msedge", "https://vercel.com/docs?a=1&b=%20x")]
    assert sistema.sites == []  # não caiu no navegador padrão
    for ruim in ("opera", "calculadora", "powershell", 7):
        with pytest.raises(ErroAcao, match="Navegador desconhecido"):
            acoes.abrir_site("https://vercel.com", navegador=ruim)
    assert len(sistema.programas) == 2


def test_navegador_que_nao_esta_na_lista_de_programas_nao_e_oferecido(area):
    docs, _, _ = area
    acoes = Acoes([docs])
    del acoes.programas["firefox"]
    assert acoes.navegadores == ["chrome", "edge"]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "exemplo.com.br",
        "ftp://exemplo.com.br/arquivo",
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(1)",
        "ms-settings:privacy",
        "C:\\Windows\\System32\\cmd.exe",
        "https://",
        "https://banco.com.br@malicioso.example/login",
        "https://usuario:senha@exemplo.com.br",
        "https://exemplo.com.br/a b",
        'https://exemplo.com.br/" --outra-opcao "',
        "https://exemplo.com.br/a\tb",
        "https://exemplo.com.br/a|calc",
        "https://exemplo.com.br/`x`",
        "https://exemplo.com.br/" + "a" * 2000,
        None,
        42,
    ],
)
def test_abrir_site_recusa_o_que_nao_e_http(acoes, sistema, url):
    with pytest.raises(ErroAcao):
        acoes.abrir_site(url)
    assert sistema.sites == []


def test_abrir_arquivo_e_pasta(area, acoes, sistema):
    docs, baixados, _ = area
    plano = acoes.abrir_arquivo(str(baixados / "relatorio.pdf"))
    assert plano.titulo == "abrir um arquivo" and plano.linhas == [str(baixados / "relatorio.pdf")]
    plano.executar()
    pasta = acoes.abrir_arquivo(str(docs / "projeto"))
    assert pasta.titulo == "abrir uma pasta"
    pasta.executar()
    assert sistema.abertos == [str(baixados / "relatorio.pdf"), str(docs / "projeto")]


def test_abrir_arquivo_recusa_executaveis(area, acoes, sistema):
    _, baixados, _ = area
    with pytest.raises(ErroAcao, match="executáveis ou scripts"):
        acoes.abrir_arquivo(str(baixados / "instalador.exe"))
    for extensao in (".bat", ".ps1", ".lnk", ".vbs", ".msi", ".JS", ".Cmd", ".py", ".url", ".hta", ".scr"):
        alvo = baixados / f"arquivo{extensao}"
        alvo.write_text("x", encoding="utf-8")
        with pytest.raises(ErroAcao, match="executáveis ou scripts"):
            acoes.abrir_arquivo(str(alvo))
    assert sistema.abertos == []
    assert {".exe", ".bat", ".cmd", ".ps1", ".lnk", ".msi", ".vbs", ".js"} <= EXTENSOES_EXECUTAVEIS


def test_abrir_arquivo_respeita_a_guarda(area, acoes, sistema):
    docs, _, fora = area
    (docs / ".env").write_text("SEGREDO=1", encoding="utf-8")
    for alvo, trecho in ((fora / "segredo.txt", "fora das pastas"), (docs / ".env", "protegido"), (docs / "nao-existe.pdf", "Não existe")):
        with pytest.raises(ErroAcao, match=trecho):
            acoes.abrir_arquivo(str(alvo))
    assert sistema.abertos == []


def test_abrir_programa_so_da_lista(area, sistema):
    docs, _, _ = area
    acoes = sistema.acoes([docs], programas={"vs code": "C:\\Apps\\Code.exe"})
    plano = acoes.abrir_programa("  Bloco de Notas ")
    assert plano.linhas == ["bloco de notas  (comando: notepad)"]
    plano.executar()
    acoes.abrir_programa("VS Code").executar()
    assert sistema.programas == ["notepad", "C:\\Apps\\Code.exe"]
    for nome in ("powershell", "cmd", "notepad C:\\x.txt", "calc & del", "", None):
        with pytest.raises(ErroAcao):
            acoes.abrir_programa(nome)
    with pytest.raises(ErroAcao, match="JARVIS_PROGRAMAS"):
        acoes.abrir_programa("regedit")
    assert len(sistema.programas) == 2
    assert chave("Explorador de Arquivos") == "explorador de arquivos" and chave(" CÁLCULO  x ") == "calculo x"


# --- criar pasta --------------------------------------------------------------


def test_criar_pasta(area, acoes):
    docs, _, fora = area
    plano = acoes.criar_pasta(str(docs / "2026" / "outubro"))
    assert not (docs / "2026").exists()
    assert plano.executar() == [f"Pasta criada: {docs / '2026' / 'outubro'}"]
    assert (docs / "2026" / "outubro").is_dir()
    with pytest.raises(ErroAcao, match="Já existe"):
        acoes.criar_pasta(str(docs / "projeto"))
    with pytest.raises(ErroAcao, match="fora das pastas"):
        acoes.criar_pasta(str(fora / "nova"))
    with pytest.raises(ErroAcao, match="protegida"):
        acoes.criar_pasta(str(docs / ".ssh"))


# --- mover e copiar -----------------------------------------------------------


def test_mover_para_pasta_existente_mantem_o_nome(area, acoes):
    docs, baixados, _ = area
    plano = acoes.mover([par(baixados / "relatorio.pdf", docs / "projeto")])
    assert plano.titulo == "mover 1 item"
    assert plano.linhas == [f"{baixados / 'relatorio.pdf'}  →  {docs / 'projeto' / 'relatorio.pdf'}"]
    assert plano.executar() == [f"movido: relatorio.pdf → {docs / 'projeto' / 'relatorio.pdf'}"]
    assert (docs / "projeto" / "relatorio.pdf").read_text(encoding="utf-8") == "pdf"
    assert not (baixados / "relatorio.pdf").exists()


def test_mover_renomeia_e_cria_a_pasta_de_destino(area, acoes):
    docs, baixados, _ = area
    acoes.mover([par(baixados / "relatorio.pdf", docs / "PDFs" / "2026" / "outubro.pdf")]).executar()
    assert (docs / "PDFs" / "2026" / "outubro.pdf").is_file()


def test_mover_varios_de_uma_vez_e_pasta_inteira(area, acoes):
    docs, baixados, _ = area
    plano = acoes.mover([par(docs / "a.txt", baixados), par(docs / "projeto", baixados / "projeto-antigo")])
    assert plano.titulo == "mover 2 itens"
    assert len(plano.executar()) == 2
    assert (baixados / "a.txt").is_file() and (baixados / "projeto-antigo" / "b.txt").is_file()
    assert not (docs / "projeto").exists()


def test_nunca_sobrescreve(area, acoes):
    docs, baixados, _ = area
    (baixados / "a.txt").write_text("outro", encoding="utf-8")
    for metodo in (acoes.mover, acoes.copiar):
        with pytest.raises(ErroAcao, match="nada é sobrescrito"):
            metodo([par(docs / "a.txt", baixados)])
        with pytest.raises(ErroAcao, match="nada é sobrescrito"):
            metodo([par(docs / "a.txt", baixados / "a.txt")])
    assert (baixados / "a.txt").read_text(encoding="utf-8") == "outro"


def test_destino_que_aparece_depois_da_confirmacao_nao_e_sobrescrito(area, acoes):
    docs, baixados, _ = area
    plano = acoes.mover([par(docs / "a.txt", baixados / "novo.txt"), par(docs / "projeto" / "b.txt", baixados / "b.txt")])
    (baixados / "novo.txt").write_text("apareceu", encoding="utf-8")  # entre confirmar e executar
    resultado = plano.executar()
    assert resultado[0].startswith("FALHOU:") and resultado[1].startswith("movido:")
    assert (baixados / "novo.txt").read_text(encoding="utf-8") == "apareceu"
    assert (docs / "a.txt").is_file() and (baixados / "b.txt").is_file()


@pytest.mark.parametrize("caso", ["origem_fora", "destino_fora", "origem_some", "raiz", "dentro_de_si", "sensivel", "ponto_ponto"])
def test_mover_e_copiar_recusam(area, acoes, caso):
    docs, baixados, fora = area
    (docs / ".ssh").mkdir()
    (docs / ".ssh" / "id_rsa").write_text("chave", encoding="utf-8")
    pedidos = {
        "origem_fora": (par(fora / "segredo.txt", docs), "fora das pastas"),
        "destino_fora": (par(docs / "a.txt", fora / "a.txt"), "fora das pastas"),
        "origem_some": (par(docs / "nao-existe.txt", baixados), "Não existe"),
        "raiz": (par(docs, baixados / "tudo"), "pasta liberada inteira"),
        "dentro_de_si": (par(docs / "projeto", docs / "projeto" / "copia"), "dentro da própria origem"),
        "sensivel": (par(docs / ".ssh" / "id_rsa", baixados / "x.txt"), "protegid"),
        "ponto_ponto": (par(docs / "a.txt", docs / ".." / "fora" / "a.txt"), "fora das pastas"),
    }
    pedido, trecho = pedidos[caso]
    for metodo in (acoes.mover, acoes.copiar):
        with pytest.raises(ErroAcao, match=trecho):
            metodo([pedido])
    assert (docs / "a.txt").is_file() and (fora / "segredo.txt").is_file() and not (fora / "a.txt").exists()


def test_destino_nao_pode_virar_arquivo_sensivel(area, acoes):
    docs, _, _ = area
    with pytest.raises(ErroAcao, match="protegido"):
        acoes.mover([par(docs / "a.txt", docs / ".env")])
    with pytest.raises(ErroAcao, match="protegida"):
        acoes.copiar([par(docs / "a.txt", docs / "AppData" / "a.txt")])


def test_lote_invalido_e_recusado_inteiro(area, acoes):
    docs, baixados, fora = area
    with pytest.raises(ErroAcao):
        acoes.mover([par(docs / "a.txt", baixados), par(fora / "segredo.txt", baixados)])
    with pytest.raises(ErroAcao, match="mesmo destino"):
        acoes.mover([par(docs / "a.txt", baixados / "x.txt"), par(docs / "projeto" / "b.txt", baixados / "x.txt")])
    for ruim in ([], None, "a.txt", [{"origem": str(docs / "a.txt")}], ["a.txt"], [par(docs / "a.txt", baixados)] * (MAX_ITENS + 1)):
        with pytest.raises(ErroAcao):
            acoes.mover(ruim)
    assert (docs / "a.txt").is_file()


def test_copiar_arquivo_e_pasta(area, acoes):
    docs, baixados, _ = area
    plano = acoes.copiar([par(docs / "a.txt", baixados), par(docs / "projeto", baixados / "copia")])
    assert plano.titulo == "copiar 2 itens"
    assert all(linha.startswith("copiado:") for linha in plano.executar())
    assert (docs / "a.txt").is_file() and (baixados / "a.txt").read_text(encoding="utf-8") == "a"
    assert (docs / "projeto" / "b.txt").is_file() and (baixados / "copia" / "b.txt").is_file()


def test_plano_grande_e_resumido_mas_executa_tudo(area, acoes):
    docs, baixados, _ = area
    for n in range(40):
        (baixados / f"foto{n:02d}.jpg").write_text("x", encoding="utf-8")
    plano = acoes.mover([par(baixados / f"foto{n:02d}.jpg", docs / "Fotos" / f"foto{n:02d}.jpg") for n in range(40)])
    assert plano.titulo == "mover 40 itens"
    assert len(plano.linhas) == 16 and plano.linhas[-1] == "… e mais 25"
    assert len(plano.executar()) == 40 and len(list((docs / "Fotos").iterdir())) == 40


# --- apagar -------------------------------------------------------------------


def test_apagar_manda_para_a_lixeira_e_nunca_remove_direto(area, acoes, sistema):
    docs, _, _ = area
    plano = acoes.apagar([str(docs / "a.txt"), str(docs / "projeto"), str(docs / "a.txt")])
    assert plano.titulo == "mandar 2 itens para a Lixeira"  # repetido conta uma vez
    assert plano.linhas == [str(docs / "a.txt"), f"{docs / 'projeto'}  (pasta inteira)"]
    assert plano.executar() == [f"na Lixeira: {docs / 'a.txt'}", f"na Lixeira: {docs / 'projeto'}"]
    assert sistema.lixeira == [str(docs / "a.txt"), str(docs / "projeto")]
    assert (docs / "a.txt").exists()  # quem tira do lugar é a Lixeira do sistema, aqui falsa


def test_apagar_recusa(area, acoes, sistema):
    docs, _, fora = area
    for alvo, trecho in ((fora / "segredo.txt", "fora das pastas"), (docs, "pasta liberada inteira"), (docs / "nao-existe", "Não existe")):
        with pytest.raises(ErroAcao, match=trecho):
            acoes.apagar([str(alvo)])
    for ruim in ([], None, "a.txt", [str(docs / "a.txt")] * (MAX_ITENS + 1)):
        with pytest.raises(ErroAcao):
            acoes.apagar(ruim)
    assert sistema.lixeira == []


def test_falha_da_lixeira_e_relatada_por_item(area, sistema):
    docs, _, _ = area

    def lixeira(caminho):
        if caminho.endswith("a.txt"):
            raise PermissionError("arquivo em uso")
        sistema.lixeira.append(caminho)

    acoes = Acoes([docs], lixeira=lixeira)
    resultado = acoes.apagar([str(docs / "a.txt"), str(docs / "projeto" / "b.txt")]).executar()
    assert resultado[0].startswith("FALHOU:") and "em uso" in resultado[0]
    assert resultado[1].startswith("na Lixeira:")


def test_lixeira_de_verdade(area):
    pytest.importorskip("send2trash")
    docs, _, _ = area
    acoes = Acoes([docs])
    try:
        resultado = acoes.apagar([str(docs / "a.txt")]).executar()
    except OSError as erro:  # ambiente sem Lixeira (alguns servidores)
        pytest.skip(f"sem Lixeira neste sistema: {erro}")
    if resultado[0].startswith("FALHOU"):
        pytest.skip(f"sem Lixeira neste sistema: {resultado[0]}")
    assert not (docs / "a.txt").exists()


# --- pasta do próprio Jarvis ---------------------------------------------------


def test_pasta_protegida_nao_e_tocada_mesmo_dentro_de_pasta_liberada(area, sistema):
    docs, baixados, _ = area
    projeto = docs / "jarvis-desktop"
    (projeto / "memoria").mkdir(parents=True)
    (projeto / "persona.md").write_text("x", encoding="utf-8")
    acoes = sistema.acoes([docs, baixados], protegidas=[projeto])
    for tentativa in (
        lambda: acoes.apagar([str(projeto / "persona.md")]),
        lambda: acoes.apagar([str(projeto)]),
        lambda: acoes.mover([par(projeto / "persona.md", baixados)]),
        lambda: acoes.mover([par(docs / "a.txt", projeto / "memoria" / "plantado.md")]),
        lambda: acoes.copiar([par(docs / "a.txt", projeto)]),
        lambda: acoes.criar_pasta(str(projeto / "nova")),
        lambda: acoes.abrir_arquivo(str(projeto / "persona.md")),
    ):
        with pytest.raises(ErroAcao, match="próprio Jarvis"):
            tentativa()
    assert sistema.lixeira == [] and (projeto / "persona.md").is_file()


def test_atalho_simbolico_para_fora_nao_engana(area, acoes):
    docs, _, fora = area
    atalho = docs / "atalho"
    try:
        atalho.symlink_to(fora, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar atalho simbólico neste sistema")
    with pytest.raises(ErroAcao, match="fora das pastas"):
        acoes.apagar([str(atalho / "segredo.txt")])
    with pytest.raises(ErroAcao, match="fora das pastas"):
        acoes.mover([par(docs / "a.txt", atalho / "a.txt")])


def test_erro_estranho_da_lixeira_vira_falha_do_item_e_nao_derruba_o_lote(area, monkeypatch):
    """No Windows a biblioteca da Lixeira pode soltar um erro COM, que não é OSError."""
    import sys
    import types

    from jarvis import acoes as modulo

    class ErroCom(Exception):
        pass

    def falha(caminho):
        if caminho.endswith("a.txt"):
            raise ErroCom("Not implemented.")

    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=falha))
    docs, _, _ = area
    (docs / "b.txt").write_text("b", encoding="utf-8")
    resultado = modulo.Acoes([docs]).apagar([str(docs / "a.txt"), str(docs / "b.txt")]).executar()
    assert resultado[0].startswith("FALHOU") and "Lixeira recusou" in resultado[0] and "ErroCom" in resultado[0]
    assert resultado[1].startswith("na Lixeira:")
