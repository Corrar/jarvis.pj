import asyncio
import os
from pathlib import Path

import pytest

from jarvis import guarda

PERMITIDAS = ["Read", "Glob", "Grep", "WebSearch", "mcp__jarvis__agora"]


@pytest.fixture
def area(tmp_path):
    """Uma pasta liberada, com um arquivo comum, e uma pasta de fora."""
    liberada = Path(os.path.realpath(tmp_path / "Documents"))
    fora = Path(os.path.realpath(tmp_path / "fora"))
    (liberada / "projeto").mkdir(parents=True)
    (liberada / "projeto" / "notas.txt").write_text("oi", encoding="utf-8")
    (liberada / "projeto" / ".env").write_text("SEGREDO=1", encoding="utf-8")
    (liberada / ".ssh").mkdir()
    (liberada / ".ssh" / "config").write_text("x", encoding="utf-8")
    fora.mkdir()
    (fora / "segredo.txt").write_text("x", encoding="utf-8")
    return liberada, fora


def avaliar(ferramenta, entrada, liberada):
    return guarda.avaliar(ferramenta, entrada, [liberada], liberada, PERMITIDAS)


def test_leitura_dentro_da_pasta_liberada_passa(area):
    liberada, _ = area
    assert avaliar("Read", {"file_path": str(liberada / "projeto" / "notas.txt")}, liberada) is None


def test_caminho_relativo_parte_da_pasta_de_trabalho(area):
    liberada, _ = area
    assert avaliar("Read", {"file_path": "projeto/notas.txt"}, liberada) is None


def test_leitura_fora_e_recusada(area):
    liberada, fora = area
    motivo = avaliar("Read", {"file_path": str(fora / "segredo.txt")}, liberada)
    assert motivo and "fora das pastas liberadas" in motivo


def test_ponto_ponto_nao_escapa(area):
    liberada, _ = area
    motivo = avaliar("Read", {"file_path": str(liberada / ".." / "fora" / "segredo.txt")}, liberada)
    assert motivo and "fora das pastas liberadas" in motivo


def test_atalho_simbolico_para_fora_e_recusado(area):
    liberada, fora = area
    atalho = liberada / "atalho"
    try:
        atalho.symlink_to(fora, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar atalho simbólico neste sistema")
    motivo = avaliar("Read", {"file_path": str(atalho / "segredo.txt")}, liberada)
    assert motivo and "fora das pastas liberadas" in motivo


def test_pasta_sensivel_e_recusada_mesmo_dentro(area):
    liberada, _ = area
    motivo = avaliar("Read", {"file_path": str(liberada / ".ssh" / "config")}, liberada)
    assert motivo and "Pasta protegida" in motivo


def test_pasta_sensivel_ignora_maiusculas(area):
    liberada, _ = area
    motivo = avaliar("Read", {"file_path": str(liberada / "appdata" / "x.txt")}, liberada)
    assert motivo and "Pasta protegida" in motivo


@pytest.mark.parametrize("nome", [".env", ".env.local", "chave.PEM", "id_rsa", "id_ed25519.pub", "cofre.kdbx"])
def test_arquivo_sensivel_e_recusado(area, nome):
    liberada, _ = area
    motivo = avaliar("Read", {"file_path": str(liberada / "projeto" / nome)}, liberada)
    assert motivo and "Arquivo protegido" in motivo


def test_read_sem_caminho_e_recusado(area):
    liberada, _ = area
    assert avaliar("Read", {}, liberada)


def test_busca_sem_path_usa_a_pasta_de_trabalho(area):
    liberada, _ = area
    assert avaliar("Grep", {"pattern": "oi"}, liberada) is None
    assert avaliar("Glob", {"pattern": "**/*.txt"}, liberada) is None


def test_busca_com_path_fora_e_recusada(area):
    liberada, fora = area
    assert avaliar("Grep", {"pattern": "x", "path": str(fora)}, liberada)
    assert avaliar("Glob", {"pattern": "*", "path": str(fora)}, liberada)


@pytest.mark.parametrize("padrao", ["/etc/**", "~/**", "C:\\Users\\**", "../fora/*", "a/../../fora/*"])
def test_glob_com_padrao_que_escapa_e_recusado(area, padrao):
    liberada, _ = area
    motivo = avaliar("Glob", {"pattern": padrao}, liberada)
    assert motivo and "Padrão de Glob" in motivo


def test_ferramenta_fora_da_lista_e_recusada(area):
    liberada, _ = area
    for nome in ("Bash", "Write", "Edit", "WebFetch", "mcp__outro__qualquer"):
        motivo = avaliar(nome, {"command": "echo oi"}, liberada)
        assert motivo and "fora da lista" in motivo


def test_ferramenta_sem_caminho_passa(area):
    liberada, _ = area
    assert avaliar("WebSearch", {"query": "previsão do tempo"}, liberada) is None
    assert avaliar("mcp__jarvis__agora", {}, liberada) is None


def test_hook_nega_no_formato_do_sdk(area):
    liberada, fora = area
    hook = guarda.Guarda([liberada], liberada, PERMITIDAS).hook
    saida = asyncio.run(
        hook({"tool_name": "Read", "tool_input": {"file_path": str(fora / "segredo.txt")}}, "id1", None)
    )
    decisao = saida["hookSpecificOutput"]
    assert decisao["hookEventName"] == "PreToolUse"
    assert decisao["permissionDecision"] == "deny"
    assert "fora das pastas liberadas" in decisao["permissionDecisionReason"]


def test_hook_deixa_passar_devolvendo_vazio(area):
    liberada, _ = area
    hook = guarda.Guarda([liberada], liberada, PERMITIDAS).hook
    saida = asyncio.run(
        hook({"tool_name": "Read", "tool_input": {"file_path": str(liberada / "projeto" / "notas.txt")}}, "id1", None)
    )
    assert saida == {}


def test_hook_falha_fechado(area, monkeypatch):
    liberada, _ = area

    def explode(*_args, **_kwargs):
        raise RuntimeError("defeito")

    monkeypatch.setattr(guarda, "avaliar", explode)
    hook = guarda.Guarda([liberada], liberada, PERMITIDAS).hook
    saida = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}, "id1", None))
    assert saida["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_regras_de_negacao_sao_bem_formadas():
    regras = guarda.regras_de_negacao()
    # Em qualquer nível: uma pasta .ssh dentro de uma pasta liberada também fica fora das buscas.
    assert "Read(//**/.ssh/**)" in regras
    assert "Read(//**/.env)" in regras
    for regra in regras:
        # O Claude Code separa as regras por vírgula e espaço.
        assert regra.startswith("Read(") and regra.endswith(")")
        assert "," not in regra and " " not in regra


# --- regra do turno limpo -------------------------------------------------

SALVAR, APAGAR, HISTORICO = "mcp__jarvis__memoria_salvar", "mcp__jarvis__memoria_apagar", "mcp__jarvis__historico_buscar"


def vigia(liberada):
    permitidas = [*PERMITIDAS, SALVAR, APAGAR, HISTORICO]
    return guarda.Guarda(
        [liberada],
        liberada,
        permitidas,
        contaminam=[*guarda.NATIVAS_QUE_CONTAMINAM, HISTORICO],
        gravam=[SALVAR, APAGAR],
    )


def test_gravar_em_turno_limpo_passa(area):
    liberada, _ = area
    g = vigia(liberada)
    assert g.decidir(SALVAR, {"titulo": "x", "conteudo": "y"}) is None
    assert g.decidir("mcp__jarvis__agora", {}) is None  # não contamina
    assert g.decidir(APAGAR, {"nome": "x"}) is None


@pytest.mark.parametrize(
    "leitura, entrada",
    [
        ("WebSearch", {"query": "cotação"}),
        ("Read", {"file_path": "projeto/notas.txt"}),
        ("Grep", {"pattern": "oi"}),
        ("Glob", {"pattern": "*.txt"}),
        (HISTORICO, {"termo": "dólar"}),
    ],
)
def test_depois_de_ler_conteudo_externo_a_gravacao_e_bloqueada(area, leitura, entrada):
    liberada, _ = area
    g = vigia(liberada)
    assert g.decidir(leitura, entrada) is None
    for ferramenta in (SALVAR, APAGAR):
        motivo = g.decidir(ferramenta, {"titulo": "x", "conteudo": "y", "nome": "x"})
        assert motivo and "Gravação bloqueada" in motivo and f"({leitura.rsplit('__', 1)[-1]})" in motivo
    assert g.decidir("Read", {"file_path": "projeto/notas.txt"}) is None  # ler continua liberado


def test_fala_do_usuario_libera_a_gravacao_de_novo(area):
    liberada, _ = area
    g = vigia(liberada)
    g.decidir("WebSearch", {"query": "x"})
    assert g.decidir(SALVAR, {"titulo": "x", "conteudo": "y"})
    g.novo_turno()
    assert g.decidir(SALVAR, {"titulo": "x", "conteudo": "y"}) is None


def test_leitura_recusada_nao_contamina(area):
    liberada, fora = area
    g = vigia(liberada)
    assert g.decidir("Read", {"file_path": str(fora / "segredo.txt")})  # recusada: nada foi lido
    assert g.decidir(SALVAR, {"titulo": "x", "conteudo": "y"}) is None


def test_hook_aplica_a_regra_do_turno_limpo(area):
    liberada, _ = area
    g = vigia(liberada)
    asyncio.run(g.hook({"tool_name": "WebSearch", "tool_input": {"query": "x"}}, "1", None))
    saida = asyncio.run(g.hook({"tool_name": SALVAR, "tool_input": {"titulo": "x", "conteudo": "y"}}, "2", None))
    assert saida["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- ações diretas (sem a pergunta no terminal) ---------------------------------

ABRIR, MOVER = "mcp__jarvis__abrir_site", "mcp__jarvis__mover"


def vigia_direto(liberada, agem=(ABRIR, MOVER)):
    permitidas = [*PERMITIDAS, SALVAR, HISTORICO, ABRIR, MOVER]
    return guarda.Guarda(
        [liberada],
        liberada,
        permitidas,
        contaminam=[*guarda.NATIVAS_QUE_CONTAMINAM, HISTORICO],
        gravam=[SALVAR],
        conteudo=[*guarda.NATIVAS_COM_CONTEUDO, HISTORICO],
        agem=agem,
    )


def test_ordem_direta_executa_sem_bloqueio(area):
    liberada, _ = area
    g = vigia_direto(liberada)
    assert g.decidir(ABRIR, {"url": "https://vercel.com"}) is None
    assert g.decidir("mcp__jarvis__agora", {}) is None
    assert g.decidir(MOVER, {"itens": []}) is None


def test_achar_pelo_nome_e_agir_no_mesmo_turno_e_permitido(area):
    """Glob devolve só nomes: bloqueia a memória, mas não a ação."""
    liberada, _ = area
    g = vigia_direto(liberada)
    assert g.decidir("Glob", {"pattern": "**/*.pdf"}) is None
    assert g.decidir(MOVER, {"itens": []}) is None
    assert "Gravação bloqueada" in g.decidir(SALVAR, {"titulo": "x", "conteudo": "y"})


@pytest.mark.parametrize(
    "leitura, entrada",
    [("WebSearch", {"query": "site oficial"}), ("Read", {"file_path": "projeto/notas.txt"}), ("Grep", {"pattern": "oi"}), (HISTORICO, {"termo": "x"})],
)
def test_depois_de_ler_conteudo_a_acao_direta_e_bloqueada(area, leitura, entrada):
    liberada, _ = area
    g = vigia_direto(liberada)
    assert g.decidir(leitura, entrada) is None
    for acao in (ABRIR, MOVER):
        motivo = g.decidir(acao, {"url": "https://x", "itens": []})
        assert motivo and "Ação bloqueada" in motivo and f"({leitura.rsplit('__', 1)[-1]})" in motivo
    g.novo_turno()  # o usuário respondeu
    assert g.decidir(ABRIR, {"url": "https://x"}) is None


def test_com_confirmacao_digitada_a_guarda_nao_bloqueia_acoes(area):
    liberada, _ = area
    g = vigia_direto(liberada, agem=())  # quem decide é o "s" do usuário
    g.decidir("WebSearch", {"query": "x"})
    assert g.decidir(ABRIR, {"url": "https://x"}) is None
    assert g.leu_conteudo_por == "WebSearch"


def test_turno_limpo_solto_deixa_gravar_e_agir_depois_de_ler(tmp_path):
    """JARVIS_TURNO_LIMPO=nao: o usuário abre mão da regra para ter menos atrito."""
    solto = guarda.Guarda([tmp_path], tmp_path, ["X"], gravam=["X"], agem=["X"], turno_limpo=False)
    solto.contaminar("WebFetch")
    assert solto.decidir("X", {}) is None


def test_mesmo_solto_o_resto_do_guarda_continua(tmp_path):
    """O ajuste solta a regra do turno, não as pastas liberadas nem a lista de ferramentas."""
    solto = guarda.Guarda([tmp_path], tmp_path, ["Read"], gravam=["Read"], agem=["Read"], turno_limpo=False)
    solto.contaminar("WebFetch")
    assert solto.decidir("Read", {"file_path": "/etc/passwd"}) is not None, "pasta de fora continua recusada"
    assert solto.decidir("Bash", {"command": "ls"}) is not None, "ferramenta não liberada continua recusada"
