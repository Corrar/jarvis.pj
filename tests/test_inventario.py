"""Inventário diário Fluxo → Protheus: etapa A, o dia em disco, a aprovação e a parada na etapa C."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis import inventario
from jarvis.inventario import (
    Aprovacao,
    Digitacao,
    ErroInventario,
    Fluxo,
    Inventario,
    Item,
    Registro,
    Vigia,
    conferir_estoque,
    documento_do_dia,
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
        {"quantity_on_hand": 12.5, "products": {"sku": "ABC-1", "name": "Parafuso", "unit": "PC"}},
        {"quantity_on_hand": "0", "products": {"sku": "XYZ.2", "name": "Chapa", "unit": "KG"}},
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
    assert linhas[0].filial == "0101" and linhas[0].local == "02" and linhas[0].documento == "FLX261023"
    assert linhas[0].como_csv() == "0101;ABC-1;02;FLX261023;12,5;23/10/2026;Parafuso;PC", "decimal com vírgula"
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
    assert ler_dias("seg, ter;qua") == ("seg", "ter", "qua") and ler_dias("") == ("seg", "ter", "qua", "qui", "sex")
    assert ler_hora("17:00") == (17, 0) and ler_hora(" 9:05 ") == (9, 5)
    for ruim in ("25:00", "17:60", "17h", "", "abc"):
        with pytest.raises(ValueError):
            ler_hora(ruim)
    with pytest.raises(ValueError, match="não é dia"):
        ler_dias("lua")
    assert e_dia_de_rodar(datetime(2026, 10, 23, 17, 0), ("sex",)) and not e_dia_de_rodar(datetime(2026, 10, 24, 17, 0), ("sex",))


# --- o dia em disco -------------------------------------------------------------------------


def _inventario(tmp_path, rede=None, modo="homologacao", maximo=300, digitacao=None, hoje=HOJE):
    return Inventario(
        Fluxo("https://fluxo.royale.com.br/api", "u", "senha-do-fluxo", pedir=rede or Rede()),
        Registro(tmp_path, hoje=lambda: hoje),
        modo=modo, maximo=maximo, digitacao=digitacao, hoje=lambda: hoje,
    )  # fmt: skip


def test_monta_o_dia_e_grava_a_pasta(tmp_path):
    alvo = _inventario(tmp_path)
    dia = alvo.montar_o_dia()
    pasta = tmp_path / inventario.PASTA / "2026-10-23"
    assert dia.estado == "montado" and dia.itens == 2 and dia.documento == "FLX261023"
    assert (pasta / "lista.csv").exists() and (pasta / "execucao.json").exists()
    assert alvo.registro.ler().itens == 2


def test_uma_vez_por_dia_sobrevive_a_reinicio(tmp_path):
    """Fechar e abrir o Jarvis não devolve o direito de lançar inventário de novo."""
    alvo = _inventario(tmp_path)
    dia = alvo.montar_o_dia()
    dia.estado = "concluido"
    alvo.registro.gravar(dia)
    outro = _inventario(tmp_path)  # como se o Jarvis tivesse sido reiniciado
    with pytest.raises(ErroInventario, match="já foi concluido"):
        outro.montar_o_dia()


def test_dia_apenas_montado_ainda_pode_ser_refeito(tmp_path):
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
    alvo = _inventario(tmp_path, digitacao=Digitacao(roteiro=tmp_path / "existe.yaml"))
    (tmp_path / "existe.yaml").write_text("roteiro de mentira", encoding="utf-8")
    alvo.montar_o_dia()
    with pytest.raises(ErroInventario, match="não pedi aprovação"):
        alvo.aprovar()


def test_a_pergunta_so_pede_aprovacao_quando_ha_o_que_digitar(tmp_path):
    """Sem roteiro calibrado não existe digitação, então pedir 'sim' seria ritual vazio."""
    alvo = _inventario(tmp_path)
    pergunta = alvo.pergunta(alvo.montar_o_dia())
    assert "Posso digitar" not in pergunta and "Não digito nada no Protheus ainda" in pergunta
    assert alvo.aprovacao.pedida_em is None


def test_com_roteiro_a_pergunta_pede_aprovacao_e_diz_o_ambiente(tmp_path):
    roteiro = tmp_path / "mata270.yaml"
    roteiro.write_text("campos: []", encoding="utf-8")
    alvo = _inventario(tmp_path, modo="producao", digitacao=Digitacao(roteiro=roteiro))
    pergunta = alvo.pergunta(alvo.montar_o_dia())
    assert "Posso digitar no Protheus de produção" in pergunta and "60 minutos" in pergunta
    assert alvo.aprovacao.pedida_em is not None


# --- etapa C: a parada ----------------------------------------------------------------------


def test_a_digitacao_recusa_e_diz_o_que_falta(tmp_path):
    alvo = _inventario(tmp_path)
    alvo.montar_o_dia()
    alvo.aprovacao.pedir(impressao_da_lista(alvo.linhas))
    with pytest.raises(ErroInventario) as caiu:
        alvo.aprovar()
    for falta in ("roteiro calibrado", "SmartClient", "usuário do Protheus", "de-para"):
        assert falta in str(caiu.value)
    dia = alvo.registro.ler()
    assert dia.estado == "parado" and dia.confirmados == 0
    assert (alvo.registro.pasta_do_dia() / "relatorio.md").exists(), "mesmo parando, o relatório sai"


def test_o_relatorio_lembra_que_o_acerto_nao_foi_feito(tmp_path):
    linhas = montar(conferir_estoque(ESTOQUE), HOJE)
    dia = inventario.Dia(data=HOJE.isoformat(), estado="parado", itens=2, documento="FLX261023")
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
