"""Inventário diário Fluxo → Protheus: etapa A, o dia em disco, a aprovação e a parada na etapa C."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis import inventario
from jarvis.inventario import (
    Acerto,
    Aprovacao,
    Exclusoes,
    Importacao,
    ErroInventario,
    Fluxo,
    Inventario,
    Item,
    Registro,
    Vigia,
    conferir_estoque,
    documento_do_dia,
    faixa_da_semana,
    gravar_csv_do_protheus,
    gravar_xlsx,
    normalizar,
    peneirar,
    e_dia_de_rodar,
    gravar_csv,
    impressao_da_lista,
    ler_dias,
    ler_hora,
    montar,
    relatorio,
    resumir,
)

HOJE = date(2026, 10, 23)  # uma sexta
ESTOQUE = {
    "data": [
        {"quantity_on_hand": 12.5, "products": {"sku": "ABC-1", "name": "Parafuso", "unit": "PC", "tags": ["PRODUCAO"]}},
        {"quantity_on_hand": "0", "products": {"sku": "XYZ.2", "name": "Chapa", "unit": "KG", "tags": ["PRODUCAO"]}},
    ]
}


class Rede:
    """O Fluxo de mentira: guarda o que foi pedido e devolve o que o teste mandar."""

    def __init__(self, login=(200, {"token": "tok-123"}), estoque=(200, ESTOQUE)):
        self.login, self.estoque, self.pedidos = login, estoque, []

    def __call__(self, url, metodo, cabecalhos, corpo=None, espera=60.0):
        self.pedidos.append((metodo, url, dict(cabecalhos), corpo))
        codigo, dados = self.login if url.endswith("/auth/login") else self.estoque
        return codigo, dados if isinstance(dados, bytes) else json.dumps(dados).encode()


def _fluxo(rede=None):
    return Fluxo("https://fluxo.royale.com.br/api", "jarvis@royale", "senha-do-fluxo", pedir=rede or Rede())


# --- etapa A: ler e conferir ----------------------------------------------------------------


def test_le_o_estoque_com_login_e_um_get():
    rede = Rede()
    itens = _fluxo(rede).estoque()
    assert [i.codigo for i in itens] == ["ABC-1", "XYZ.2"]
    assert itens[0].quantidade == Decimal("12.5") and itens[1].quantidade == Decimal("0")
    metodos = [(m, u.rsplit("/", 1)[-1]) for m, u, _c, _b in rede.pedidos]
    assert metodos == [("POST", "login"), ("GET", "stock")], "só as duas rotas, e o estoque só com GET"
    assert rede.pedidos[1][2]["Authorization"] == "Bearer tok-123"


def test_o_token_nao_fica_guardado():
    """Vale um dia; um login por execução custa menos que um segredo parado em disco."""
    alvo = _fluxo()
    alvo.estoque()
    guardado = json.dumps({k: str(v) for k, v in vars(alvo).items()})
    assert "tok-123" not in guardado


def test_a_senha_nunca_aparece_na_mensagem_de_erro():
    rede = Rede(login=(401, {"erro": "senha invalida para senha-do-fluxo", "token": "x"}))
    with pytest.raises(ErroInventario) as caiu:
        _fluxo(rede).estoque()
    assert "senha-do-fluxo" not in str(caiu.value) and "401" in str(caiu.value)


def test_desvio_no_login_para_a_execucao():
    """Seguir um 3xx mandaria usuário e senha para o endereço que o servidor escolhesse."""
    with pytest.raises(ErroInventario, match="não sigo desvio"):
        _fluxo(Rede(login=(302, {}))).estoque()
    with pytest.raises(ErroInventario, match="não sigo desvio"):
        _fluxo(Rede(estoque=(301, {}))).estoque()


def test_endereco_do_fluxo_so_http_ou_https():
    with pytest.raises(ValueError, match="http ou https"):
        Fluxo("file:///etc/passwd", "u", "s")


@pytest.mark.parametrize(
    ("estoque", "trecho"),
    [
        ({"data": []}, "vazio"),
        ({"data": [{"quantity_on_hand": -3, "products": {"sku": "A"}}]}, "negativa"),
        ({"data": [{"quantity_on_hand": "x", "products": {"sku": "A"}}]}, "não é número"),
        ({"data": [{"quantity_on_hand": None, "products": {"sku": "A"}}]}, "não é número"),
        ({"data": [{"quantity_on_hand": 1, "products": {"sku": ""}}]}, "sem SKU"),
        ({"data": [{"quantity_on_hand": 1, "products": {"sku": "A"}}, {"quantity_on_hand": 2, "products": {"sku": "a"}}]}, "duas vezes"),
        ({"data": [{"quantity_on_hand": 1, "products": {"sku": "A;DROP"}}]}, "caractere"),
        ({"data": "nao e lista"}, "não veio como lista"),
    ],
    ids=["vazio", "negativa", "texto", "nulo", "sem sku", "repetido", "sku torto", "formato"],
)
def test_as_barreiras_da_etapa_a(estoque, trecho):
    """Qualquer uma delas interrompe antes de qualquer tela, com motivo que o Bruno entende."""
    with pytest.raises(ErroInventario, match=trecho):
        conferir_estoque(estoque)


def test_lista_acima_do_teto_para_antes_da_tela():
    itens = [Item(codigo=f"P{n}", quantidade=Decimal(1)) for n in range(11)]
    with pytest.raises(ErroInventario, match="acima do teto"):
        inventario.conferir_tamanho(montar(itens, HOJE), 10)


# --- a lista e o arquivo --------------------------------------------------------------------


def test_a_linha_sai_no_formato_da_sb7(tmp_path):
    linhas = montar(conferir_estoque(ESTOQUE), HOJE)
    assert linhas[0].filial == "0101" and linhas[0].local == "02"
    # Um documento por linha: documento único para todas foi a tentativa que o Protheus recusou.
    assert [l.documento for l in linhas] == ["26430001", "26430002"]
    assert linhas[0].como_csv() == "0101;ABC-1;02;26430001;12,5;23/10/2026;Parafuso;PC", "decimal com vírgula"
    arquivo = gravar_csv(linhas, tmp_path)
    texto = arquivo.read_text(encoding="utf-8-sig")
    assert texto.splitlines()[0] == inventario.CABECALHO_CSV and texto.count(";") == 7 * 3
    assert "2 itens" in resumir(linhas) and "Zeros: 1" in resumir(linhas)


def test_de_para_troca_o_codigo_e_confere_o_que_saiu():
    linhas = montar(conferir_estoque(ESTOQUE), HOJE, de_para={"ABC-1": "000123"})
    assert linhas[0].codigo == "000123"
    with pytest.raises(ErroInventario, match="de-para"):
        montar(conferir_estoque(ESTOQUE), HOJE, de_para={"ABC-1": "00 01;23"})


def test_documento_e_dias_e_hora():
    assert documento_do_dia(date(2026, 1, 5)) == "FLX260105"
    assert faixa_da_semana(date(2026, 10, 30), 2128) == ("2644", "26440001", "26442128")
    assert ler_dias("seg, ter;qua") == ("seg", "ter", "qua") and ler_dias("") == ("sex",), "semanal, na sexta"
    assert ler_hora("17:00") == (17, 0) and ler_hora(" 9:05 ") == (9, 5)
    for ruim in ("25:00", "17:60", "17h", "", "abc"):
        with pytest.raises(ValueError):
            ler_hora(ruim)
    with pytest.raises(ValueError, match="não é dia"):
        ler_dias("lua")
    assert e_dia_de_rodar(datetime(2026, 10, 23, 17, 0), ("sex",)) and not e_dia_de_rodar(datetime(2026, 10, 24, 17, 0), ("sex",))


# --- o dia em disco -------------------------------------------------------------------------


def _inventario(tmp_path, rede=None, modo="homologacao", maximo=300, importacao=None, hoje=HOJE):
    return Inventario(
        Fluxo("https://fluxo.royale.com.br/api", "u", "senha-do-fluxo", pedir=rede or Rede()),
        Registro(tmp_path, hoje=lambda: hoje),
        modo=modo, maximo=maximo, importacao=importacao, hoje=lambda: hoje,
    )  # fmt: skip


def test_monta_o_dia_e_grava_a_pasta(tmp_path):
    alvo = _inventario(tmp_path)
    dia = alvo.montar_o_dia()
    pasta = tmp_path / inventario.PASTA / "2026-10-23"
    assert dia.estado == "montado" and dia.itens == 2 and dia.faixa == "26430001-26430002"
    assert (pasta / "lista.csv").exists() and (pasta / "execucao.json").exists()
    assert (pasta / "inventario-2026-10-23.xlsx").exists(), "a planilha do layout SB7"
    assert (pasta / "inventario-2026-10-23.csv").exists(), "e o CSV que vai para o Protheus"
    assert alvo.registro.ler().itens == 2


def test_uma_vez_por_semana_sobrevive_a_reinicio(tmp_path):
    """É semanal, e o acerto não tem volta: fechar e abrir o Jarvis não devolve o direito."""
    alvo = _inventario(tmp_path)
    dia = alvo.montar_o_dia()
    dia.estado = "concluido"
    alvo.registro.gravar(dia)
    outro = _inventario(tmp_path)  # como se o Jarvis tivesse sido reiniciado
    with pytest.raises(ErroInventario, match="já foi feito"):
        outro.montar_o_dia()


def test_semana_apenas_montada_ainda_pode_ser_refeita(tmp_path):
    """Montar não é lançar: se só montou, pode montar de novo (a lista pode ter mudado)."""
    alvo = _inventario(tmp_path)
    alvo.montar_o_dia()
    assert _inventario(tmp_path).montar_o_dia().estado == "montado"


# --- a aprovação ----------------------------------------------------------------------------


def test_aprovacao_vence_e_cai_se_a_lista_mudar():
    agora = [datetime(2026, 10, 23, 17, 0)]
    alvo = Aprovacao(minutos=60, agora=lambda: agora[0])
    assert alvo.vale_para("abc") == (False, "não pedi aprovação para hoje ainda")
    alvo.pedir("abc")
    assert alvo.vale_para("abc")[0]
    assert alvo.vale_para("outra")[1] == "a lista mudou depois que eu pedi; peço de novo"
    agora[0] = datetime(2026, 10, 23, 18, 1)
    assert "venceu" in alvo.vale_para("abc")[1]


def test_a_impressao_muda_quando_qualquer_item_muda():
    uma = montar(conferir_estoque(ESTOQUE), HOJE)
    outra = montar(conferir_estoque({"data": [{"quantity_on_hand": 12.6, "products": {"sku": "ABC-1"}}]}), HOJE)
    assert impressao_da_lista(uma) != impressao_da_lista(outra)
    assert impressao_da_lista(uma) == impressao_da_lista(montar(conferir_estoque(ESTOQUE), HOJE))


def test_sem_aprovacao_nao_digita(tmp_path):
    alvo = _inventario(tmp_path, importacao=Importacao(roteiro=tmp_path / "existe.yaml"))
    (tmp_path / "existe.yaml").write_text("roteiro de mentira", encoding="utf-8")
    alvo.montar_o_dia()
    with pytest.raises(ErroInventario, match="não pedi aprovação"):
        alvo.aprovar()


def test_a_pergunta_so_pede_aprovacao_quando_ha_o_que_digitar(tmp_path):
    """Sem roteiro calibrado não existe digitação, então pedir 'sim' seria ritual vazio."""
    alvo = _inventario(tmp_path)
    pergunta = alvo.pergunta(alvo.montar_o_dia())
    assert "Posso importar" not in pergunta and "Não importo nada no Protheus ainda" in pergunta
    assert alvo.aprovacao.pedida_em is None


def test_com_roteiro_a_pergunta_pede_aprovacao_e_diz_o_ambiente(tmp_path):
    roteiro = tmp_path / "mata270.yaml"
    roteiro.write_text("campos: []", encoding="utf-8")
    alvo = _inventario(tmp_path, modo="producao", importacao=Importacao(roteiro=roteiro))
    pergunta = alvo.pergunta(alvo.montar_o_dia())
    assert "Posso importar no Protheus de produção" in pergunta and "60 minutos" in pergunta
    assert alvo.aprovacao.pedida_em is not None


# --- etapa C: a parada ----------------------------------------------------------------------


def test_a_importacao_recusa_e_diz_o_que_falta(tmp_path):
    alvo = _inventario(tmp_path)
    alvo.montar_o_dia()
    alvo.aprovacao.pedir(impressao_da_lista(alvo.linhas))
    with pytest.raises(ErroInventario) as caiu:
        alvo.aprovar()
    for falta in ("roteiro calibrado", "decimal entrou truncado", "Imprimir Browse", "data base"):
        assert falta in str(caiu.value)
    dia = alvo.registro.ler()
    assert dia.estado == "parado" and dia.confirmados == 0
    assert (alvo.registro.pasta_do_dia() / "relatorio.md").exists(), "mesmo parando, o relatório sai"


def test_o_relatorio_lembra_que_o_acerto_nao_foi_feito(tmp_path):
    linhas = montar(conferir_estoque(ESTOQUE), HOJE)
    dia = inventario.Dia(data=HOJE.isoformat(), estado="parado", itens=2, faixa="26430001-26430002")
    arquivo = relatorio(dia, linhas, "homologacao", tmp_path)
    texto = arquivo.read_text(encoding="utf-8")
    assert "MATA340" in texto and "não foi feito" in texto and "ABC-1" in texto


# --- o relógio ------------------------------------------------------------------------------


def _vigia(agora, dias=("sex",), ja_rodou=False, hora=(17, 0)):
    disparos = []
    alvo = Vigia(hora, dias, disparar=lambda: disparos.append(1), ja_rodou=lambda: ja_rodou, agora=lambda: agora[0])
    return alvo, disparos


def test_dispara_na_hora_marcada_e_so_uma_vez():
    agora = [datetime(2026, 10, 23, 17, 0)]
    alvo, disparos = _vigia(agora)
    assert alvo.conferir() and len(disparos) == 1
    agora[0] = datetime(2026, 10, 23, 17, 30)
    assert not alvo.conferir() and len(disparos) == 1, "não dispara duas vezes no mesmo dia"


def test_maquina_desligada_as_17_ainda_roda_quando_o_jarvis_sobe():
    """Se a máquina estava desligada às 17:00 e o Jarvis subiu 17:40, o inventário do dia acontece."""
    agora = [datetime(2026, 10, 23, 17, 40)]
    alvo, disparos = _vigia(agora)
    assert alvo.conferir() and len(disparos) == 1


def test_nao_dispara_antes_da_hora_fora_do_dia_nem_se_ja_rodou():
    agora = [datetime(2026, 10, 23, 16, 59)]
    assert not _vigia(agora)[0].conferir(), "antes da hora"
    agora[0] = datetime(2026, 10, 24, 17, 0)  # sábado
    assert not _vigia(agora)[0].conferir(), "fora do dia configurado"
    agora[0] = datetime(2026, 10, 23, 17, 0)
    assert not _vigia(agora, ja_rodou=True)[0].conferir(), "já rodou hoje"


# --- ligado no Jarvis -----------------------------------------------------------------------


def test_o_comando_chega_ao_inventario_de_verdade(tmp_path, monkeypatch):
    """A trava que faltava: o objeto é construído numa função e usado em outra."""
    import asyncio
    import inspect

    from jarvis import cli

    assert "inventario_do_dia" in inspect.signature(cli._laco_da_conversa).parameters, (
        "o laço da conversa precisa receber o inventário; sem isso o comando estoura em NameError"
    )
    alvo = _inventario(tmp_path)
    asyncio.run(cli.tratar_inventario(alvo, "montar"))
    assert alvo.registro.ler().itens == 2
    asyncio.run(cli.tratar_inventario(alvo, "aprovar"))  # recusa, mas não estoura
    assert alvo.registro.ler().estado in ("montado", "parado")


def test_o_inventario_nao_e_ferramenta_do_modelo():
    """Ele roda pelo relógio e pelo comando digitado; o modelo não dispara lançamento em ERP."""
    from jarvis import ferramentas

    nomes = " ".join(dir(ferramentas))
    assert "inventario" not in nomes.lower()


# --- v2: exclusões, documento por semana, acerto --------------------------------------------


def test_exclusoes_saem_por_etiqueta_prefixo_e_codigo():
    """As regras que o Bruno decidiu na rodada de 24 a 28/10, medidas e não supostas."""
    itens = [
        Item("5.01.0255", Decimal(1), etiquetas=("FEIRA",)),
        Item("FIL-PET-9", Decimal(2), etiquetas=("PRODUCAO",)),
        Item("3.05.0115", Decimal(3), etiquetas=("PRODUCAO",)),
        Item("5.01.0300", Decimal(4), etiquetas=("PRODUCAO",)),
    ]
    regras = Exclusoes(etiquetas=("FEIRA",), prefixos_sku=("FIL-PET",), codigos_inativos=("3.05.0115",))
    ficam, fora = peneirar(itens, regras)
    assert [i.codigo for i in ficam] == ["5.01.0300"]
    assert fora == {"etiqueta FEIRA": ["5.01.0255"], "prefixo FIL-PET": ["FIL-PET-9"],
                    "código inativo no Protheus": ["3.05.0115"]}  # fmt: skip


def test_etiqueta_compara_sem_acento_e_sem_maiuscula():
    """INFORMÁTICA = informatica: a etiqueta vem escrita de um jeito no Fluxo e de outro na regra."""
    assert normalizar(" informática ") == "INFORMATICA"
    item = conferir_estoque({"data": [{"quantity_on_hand": 1, "products": {"sku": "A", "tags": [{"name": "Informática"}]}}]})[0]
    assert item.etiquetas == ("INFORMATICA",)
    _ficam, fora = peneirar([item, Item("B", Decimal(1), etiquetas=("OUTRA",))], Exclusoes(etiquetas=("INFORMATICA",)))
    assert fora == {"etiqueta INFORMATICA": ["A"]}


def test_varias_etiquetas_basta_uma_na_lista():
    item = Item("A", Decimal(1), etiquetas=("PRODUCAO", "VIAGEM"))
    _ficam, fora = peneirar([item, Item("B", Decimal(1), etiquetas=("PRODUCAO",))], Exclusoes(etiquetas=("VIAGEM",)))
    assert list(fora) == ["etiqueta VIAGEM"]


def test_sem_etiqueta_nenhuma_no_fluxo_e_erro_e_nao_silencio():
    """O risco que importa: excluir zero em silêncio poria em produção o que o Bruno tirou."""
    with pytest.raises(ErroInventario, match="nenhum produto do Fluxo veio com etiqueta"):
        peneirar([Item("A", Decimal(1)), Item("B", Decimal(2))], Exclusoes(etiquetas=("FEIRA",)))
    # Sem regra por etiqueta, a ausência dela não é problema.
    ficam, _fora = peneirar([Item("A", Decimal(1))], Exclusoes(prefixos_sku=("XYZ",)))
    assert len(ficam) == 1


def test_exclusoes_nascem_do_arquivo_e_o_arquivo_e_criado(tmp_path):
    arquivo = tmp_path / inventario.ARQUIVO_DE_EXCLUSOES
    regras = Exclusoes.carregar(arquivo)
    assert arquivo.exists(), "cria com os valores da rodada manual, para o Bruno editar"
    assert "MIN-TAI" in regras.etiquetas and "FIL-PET" in regras.prefixos_sku
    assert "3.05.0115" in regras.codigos_inativos
    arquivo.write_text('{"etiquetas": ["SO ESTA"], "prefixos_sku": []}', encoding="utf-8")
    assert Exclusoes.carregar(arquivo).etiquetas == ("SO ESTA",), "o arquivo manda, não o padrão"
    arquivo.write_text("{isso não é json", encoding="utf-8")
    with pytest.raises(ErroInventario, match="não consegui ler"):
        Exclusoes.carregar(arquivo)
    arquivo.write_text('{"etiquetas": "deveria ser lista"}', encoding="utf-8")
    with pytest.raises(ErroInventario, match="lista de textos"):
        Exclusoes.carregar(arquivo)


def test_tudo_excluido_e_erro():
    with pytest.raises(ErroInventario, match="tiraram todos"):
        peneirar([Item("A", Decimal(1), etiquetas=("FEIRA",))], Exclusoes(etiquetas=("FEIRA",)))


def test_documento_e_unico_por_linha_e_por_semana():
    """Mesmo documento para todas falhou em 26/10; faixa reaproveitada misturou com inventário antigo."""
    uma = montar([Item(f"P{n}", Decimal(1)) for n in range(3)], date(2026, 10, 30))
    outra = montar([Item("P0", Decimal(1))], date(2026, 11, 6))
    assert [l.documento for l in uma] == ["26440001", "26440002", "26440003"]
    assert outra[0].documento == "26450001", "a semana seguinte não repete a faixa"
    assert len({l.documento for l in uma}) == 3 and len(uma[0].documento) == 8
    assert faixa_da_semana(date(2026, 1, 2), 5)[0] == "2601", "a virada do ano usa a semana ISO"


def test_mais_linhas_do_que_a_sequencia_aguenta_para():
    with pytest.raises(ErroInventario, match="só vai até"):
        faixa_da_semana(HOJE, inventario.MAX_LINHAS_NA_SEMANA + 1)


def test_a_planilha_sai_no_layout_que_o_protheus_importou(tmp_path):
    """Produto, armazém e documento como texto: o Excel faria 02 virar 2 e o Protheus recusa."""
    openpyxl = pytest.importorskip("openpyxl")
    linhas = montar([Item("5.01.0255", Decimal("62.6"), "Chapa", "KG")], date(2026, 10, 30))
    arquivo = gravar_xlsx(linhas, tmp_path)
    folha = openpyxl.load_workbook(arquivo)[inventario.PLANILHA]
    assert [c.value for c in folha[1]] == list(inventario.CABECALHO_XLSX)
    assert [c.value for c in folha[2]][:4] == ["5.01.0255", "02", "26440001", 62.6]
    assert folha["B2"].number_format == "@" and folha["C2"].number_format == "@"


def test_o_csv_do_protheus_mantem_o_decimal(tmp_path):
    """Em 26/10 as quantidades com decimal entraram truncadas: 62,6 não pode virar 62."""
    linhas = montar([Item("A", Decimal("62.6")), Item("B", Decimal("0.6"))], date(2026, 10, 30))
    texto = gravar_csv_do_protheus(linhas, tmp_path).read_text(encoding="utf-8-sig")
    assert "62,6" in texto and "0,6" in texto and texto.startswith("Produto;Armazem;Documento")


def test_o_acerto_exige_importacao_antes_e_nao_roda_duas_vezes(tmp_path):
    """O acerto muda saldo e custo: segunda aprovação, e nunca duas vezes na mesma semana."""
    alvo = _inventario(tmp_path)
    dia = alvo.montar_o_dia()
    with pytest.raises(ErroInventario, match="só depois da importação"):
        alvo.acertar()
    dia.estado = "importado"
    alvo.registro.gravar(dia)
    with pytest.raises(ErroInventario, match="Não rodo o acerto ainda"):
        alvo.acertar()
    dia.acerto_feito = True
    alvo.registro.gravar(dia)
    with pytest.raises(ErroInventario, match="já foi feito"):
        alvo.acertar()


def test_a_aprovacao_da_importacao_nao_serve_para_o_acerto(tmp_path):
    """São dois OK separados: aprovar o arquivo não é aprovar o que mexe em saldo."""
    alvo = _inventario(tmp_path)
    alvo.montar_o_dia()
    alvo.aprovacao.pedir(inventario.impressao_da_lista(alvo.linhas))
    assert alvo.aprovacao_do_acerto.pedida_em is None
    assert alvo.aprovacao_do_acerto.vale_para("qualquer")[0] is False


def test_semana_com_acerto_feito_bloqueia_a_semana_inteira(tmp_path):
    """Trava por semana ISO, não por dia: segunda-feira seguinte é outra semana."""
    alvo = _inventario(tmp_path, hoje=date(2026, 10, 30))  # sexta, semana 44
    dia = alvo.montar_o_dia()
    dia.acerto_feito = True
    alvo.registro.gravar(dia)
    mesma_semana = _inventario(tmp_path, hoje=date(2026, 10, 31))  # sábado, ainda semana 44
    with pytest.raises(ErroInventario, match="já foi feito"):
        mesma_semana.montar_o_dia()
    outra = _inventario(tmp_path, hoje=date(2026, 11, 6))  # sexta seguinte, semana 45
    assert outra.montar_o_dia().faixa.startswith("2645")


def test_o_relatorio_mostra_quem_saiu_por_cada_regra(tmp_path):
    linhas = montar(conferir_estoque(ESTOQUE), HOJE)
    dia = inventario.Dia(data=HOJE.isoformat(), estado="montado", itens=2)
    texto = inventario.relatorio(dia, linhas, "homologacao", tmp_path,
                                 {"etiqueta FEIRA": ["A", "B"], "prefixo FIL-PET": ["C"]}).read_text(encoding="utf-8")  # fmt: skip
    assert "etiqueta FEIRA** — 2" in texto and "prefixo FIL-PET** — 1" in texto
