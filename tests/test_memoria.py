import pytest

from jarvis.memoria import MAX_ARQUIVOS, ErroMemoria, Memoria, nome_de_arquivo


@pytest.fixture
def memoria(tmp_path):
    return Memoria(tmp_path / "memoria")


def test_salvar_cria_um_arquivo_markdown_legivel(memoria):
    nome = memoria.salvar("Horário de trabalho", "Trabalha das 7h às 17h, de segunda a sexta.")
    assert nome == "horario-de-trabalho"
    arquivo = memoria.pasta / "horario-de-trabalho.md"
    assert arquivo.read_text(encoding="utf-8") == "# Horário de trabalho\n\nTrabalha das 7h às 17h, de segunda a sexta.\n"
    assert memoria.ler("Horário de trabalho") == arquivo.read_text(encoding="utf-8")
    assert memoria.ler("horario-de-trabalho") == arquivo.read_text(encoding="utf-8")


def test_salvar_de_novo_com_o_mesmo_titulo_substitui(memoria):
    memoria.salvar("Café", "Toma café sem açúcar.")
    memoria.salvar("Café", "Parou de tomar café.")
    assert len(memoria.listar()) == 1
    assert "Parou de tomar café." in memoria.ler("cafe")


def test_listar_e_indice(memoria):
    assert memoria.listar() == [] and memoria.indice() == ""
    memoria.salvar("Projeto Jarvis", "Assistente de desktop em Python.\nSegunda linha.")
    memoria.salvar("Apelido", "Prefere ser chamado de chefe.")
    assert [i.nome for i in memoria.listar()] == ["apelido", "projeto-jarvis"]
    assert memoria.indice() == (
        "- apelido: Apelido — Prefere ser chamado de chefe.\n"
        "- projeto-jarvis: Projeto Jarvis — Assistente de desktop em Python."
    )


def test_indice_respeita_o_limite_e_avisa_do_que_ficou_de_fora(memoria):
    for n in range(30):
        memoria.salvar(f"Assunto {n:02d}", "Conteúdo de tamanho médio para ocupar espaço no índice.")
    indice = memoria.indice(limite=400)
    linhas = indice.splitlines()
    assert len(indice) < 520
    assert linhas[-1].startswith("- (mais ") and "memoria_listar" in linhas[-1]
    restantes = int(linhas[-1].split()[2])
    assert restantes + len(linhas) - 1 == 30


def test_apagar(memoria):
    memoria.salvar("Apelido", "Chefe.")
    assert memoria.apagar("Apelido") == "apelido"
    assert memoria.listar() == []
    with pytest.raises(ErroMemoria, match="Não há nada chamado"):
        memoria.apagar("apelido")
    with pytest.raises(ErroMemoria, match="Não há nada chamado"):
        memoria.ler("apelido")


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("Café às 9h", "cafe-as-9h"),
        ("  Vários   espaços  ", "varios-espacos"),
        ("../../Windows/System32", "windows-system32"),
        ("C:\\Users\\voce\\.ssh\\id_rsa", "c-users-voce-ssh-id-rsa"),
        ("a" * 200, "a" * 60),
    ],
)
def test_nome_de_arquivo_nunca_sai_da_pasta(memoria, titulo, esperado):
    assert nome_de_arquivo(titulo) == esperado
    arquivo = memoria._arquivo(titulo)
    assert arquivo.parent == memoria.pasta and arquivo.suffix == ".md"


@pytest.mark.parametrize("titulo", ["", "   ", "…!?", "///"])
def test_titulo_sem_letras_e_recusado(memoria, titulo):
    with pytest.raises(ErroMemoria):
        memoria.salvar(titulo, "conteúdo")


def test_limites_de_tamanho(memoria):
    with pytest.raises(ErroMemoria, match="obrigatórios"):
        memoria.salvar("Título", "   ")
    with pytest.raises(ErroMemoria, match="Título com mais"):
        memoria.salvar("t" * 81, "conteúdo")
    with pytest.raises(ErroMemoria, match="Conteúdo com mais"):
        memoria.salvar("Título", "c" * 2001)
    assert memoria.listar() == []


@pytest.mark.parametrize(
    "segredo",
    ["minha chave é sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"],
)
def test_segredo_nao_e_guardado(memoria, segredo):
    with pytest.raises(ErroMemoria, match="chave ou senha"):
        memoria.salvar("Acesso", segredo)
    assert memoria.listar() == []


def test_limite_de_arquivos(memoria, monkeypatch):
    monkeypatch.setitem(MAX_ARQUIVOS, "fato", 2)
    memoria.salvar("Um", "1")
    memoria.salvar("Dois", "2")
    with pytest.raises(ErroMemoria, match="Já existem 2 arquivos"):
        memoria.salvar("Três", "3")
    memoria.salvar("Um", "atualizado")  # atualizar um existente continua permitido


def test_arquivo_editado_a_mao_ou_ilegivel_nao_derruba_a_listagem(memoria):
    memoria.pasta.mkdir(parents=True)
    (memoria.pasta / "solto.md").write_text("sem título de markdown\nsegunda linha", encoding="utf-8")
    (memoria.pasta / "vazio.md").write_text("", encoding="utf-8")
    (memoria.pasta / "binario.md").write_bytes(b"\xff\xfe\x00\x81")
    (memoria.pasta / "ignorado.txt").write_text("não é lembrança", encoding="utf-8")
    itens = {i.nome: i for i in memoria.listar()}
    assert set(itens) == {"solto", "vazio"}
    assert itens["solto"].titulo == "sem título de markdown" and itens["solto"].resumo == "segunda linha"
    assert itens["vazio"].titulo == "vazio"


# --- lições (erros / não fazer) -------------------------------------------


def test_licao_fica_em_pasta_propria_e_nao_se_mistura_com_os_fatos(memoria):
    memoria.salvar("Apelido", "Prefere ser chamado de chefe.")
    nome = memoria.salvar("Listas longas", "Ao listar arquivos, dizer quantos são e citar três.", categoria="licao")
    assert nome == "listas-longas"
    assert (memoria.pasta / "licoes" / "listas-longas.md").is_file()
    assert [i.nome for i in memoria.listar()] == ["apelido"]
    assert [i.nome for i in memoria.listar("licao")] == ["listas-longas"]
    assert "listas-longas" not in memoria.indice()


def test_licoes_entram_inteiras_no_prompt(memoria):
    assert memoria.licoes() == ""
    memoria.salvar("Listas longas", "Ao listar arquivos, dizer quantos são.\nCitar só três exemplos.", categoria="licao")
    memoria.salvar("Fontes", "Citar as fontes pelo nome, sem comentar o lembrete da busca.", categoria="licao")
    assert memoria.licoes() == (
        "- Fontes: Citar as fontes pelo nome, sem comentar o lembrete da busca.\n"
        "- Listas longas: Ao listar arquivos, dizer quantos são. Citar só três exemplos."
    )


def test_licao_tem_limite_menor(memoria):
    memoria.salvar("Curta", "x" * 400, categoria="licao")
    with pytest.raises(ErroMemoria, match="mais de 400"):
        memoria.salvar("Longa", "x" * 401, categoria="licao")
    memoria.salvar("Fato longo", "x" * 401)  # como fato cabe


def test_ler_e_apagar_por_categoria(memoria):
    memoria.salvar("Fontes", "Citar pelo nome.", categoria="licao")
    with pytest.raises(ErroMemoria):
        memoria.ler("fontes")  # não existe como fato
    assert "Citar pelo nome." in memoria.ler("fontes", "licao")
    assert memoria.apagar("Fontes", "licao") == "fontes"
    assert memoria.listar("licao") == []


def test_categoria_desconhecida_e_recusada(memoria):
    for chamada in (
        lambda: memoria.salvar("a", "b", categoria="../fora"),
        lambda: memoria.ler("a", "segredos"),
        lambda: memoria.listar("x"),
    ):
        with pytest.raises(ErroMemoria, match="Categoria desconhecida"):
            chamada()
    assert not memoria.pasta.exists()


def test_preferencia_fica_em_pasta_propria_e_entra_inteira_no_prompt(memoria):
    assert memoria.preferencias() == ""
    nome = memoria.salvar("Respostas", "Prefere respostas curtas, sem listas.", categoria="preferencia")
    memoria.salvar("Navegador", "Abrir sites sempre no Chrome.", categoria="preferencia")
    assert nome == "respostas" and (memoria.pasta / "preferencias" / "respostas.md").is_file()
    assert memoria.listar("fato") == [] and memoria.listar("licao") == []
    assert memoria.preferencias() == "- Navegador: Abrir sites sempre no Chrome.\n- Respostas: Prefere respostas curtas, sem listas."
    assert memoria.indice() == ""  # não se mistura com o índice dos fatos
    assert memoria.apagar("Respostas", "preferencia") == "respostas"


def test_preferencia_tem_o_limite_curto(memoria):
    memoria.salvar("Curta", "x" * 400, categoria="preferencia")
    with pytest.raises(ErroMemoria, match="400"):
        memoria.salvar("Longa", "x" * 401, categoria="preferencia")


def test_as_tres_categorias():
    from jarvis.memoria import CATEGORIAS, MAX_ARQUIVOS, MAX_CONTEUDO

    assert list(CATEGORIAS) == ["fato", "preferencia", "licao"]
    assert set(MAX_CONTEUDO) == set(MAX_ARQUIVOS) == set(CATEGORIAS)
