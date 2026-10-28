"""O painel: servidor local, o pedido digitado na página, os ajustes guardados, os cartões, composição do estado e retrato do conhecimento."""

import http.client
import json
import logging
import re
import time
from urllib.parse import urlsplit

import pytest

from jarvis import painel as modulo
from jarvis.janelas import TITULO_DO_PAINEL
from jarvis.memoria import Memoria
from jarvis.painel import (
    APARENCIA_PADRAO,
    APARENCIAS,
    ICONES_DOS_PAINEIS,
    MAX_APARENCIA,
    MAX_PEDIDO,
    MUDO,
    Painel,
    PainelMudo,
    abrir_janela,
    conhecimento,
    contorno_de_texto,
    ler_aparencia,
    limpar_paineis,
    limpar_pedido,
    validar_aparencia,
)
from jarvis.registro import Registro, Turno


@pytest.fixture
def painel():
    p = Painel(atalho="Ctrl+Alt+J", com_voz=True)
    p.iniciar()
    yield p
    p.fechar()


def pedir(painel, caminho, host=None, metodo="GET"):
    endereco = urlsplit(painel.endereco)
    conexao = http.client.HTTPConnection("127.0.0.1", endereco.port, timeout=5)
    conexao.putrequest(metodo, caminho, skip_host=True)
    conexao.putheader("Host", host or f"127.0.0.1:{endereco.port}")
    conexao.endheaders()
    resposta = conexao.getresponse()
    return conexao, resposta


class Fluxo:
    """Lê o fluxo de eventos como o navegador faria."""

    def __init__(self, painel):
        self.conexao, self.resposta = pedir(painel, urlsplit(painel.endereco).path + "eventos")
        assert self.resposta.status == 200 and self.resposta.getheader("Content-Type").startswith("text/event-stream")

    def proximo(self):
        while True:
            linha = self.resposta.fp.readline().decode("utf-8")
            assert linha, "o fluxo fechou"
            if linha.startswith("data: "):
                return json.loads(linha[6:])

    def fechar(self):
        self.resposta.close()  # a resposta segura a conexão aberta; sem isto o servidor não percebe a saída
        self.conexao.close()


def esperar(condicao, tempo=3.0):
    limite = time.time() + tempo
    while time.time() < limite:
        if condicao():
            return True
        time.sleep(0.02)
    return False


# --- servidor ---------------------------------------------------------------------------


def test_endereco_e_local_e_tem_codigo_secreto(painel):
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/[\w-]{20,}/", painel.endereco)
    outro = Painel()
    assert outro.codigo != painel.codigo  # sorteado a cada execução


def test_pagina_vem_com_as_travas_do_navegador(painel):
    conexao, resposta = pedir(painel, urlsplit(painel.endereco).path)
    corpo = resposta.read().decode("utf-8")
    conexao.close()
    assert resposta.status == 200 and resposta.getheader("Content-Type") == "text/html; charset=utf-8"
    politica = resposta.getheader("Content-Security-Policy")
    assert "default-src 'none'" in politica and "connect-src 'self'" in politica and "form-action 'none'" in politica
    assert "frame-ancestors 'none'" in politica  # nenhuma outra página consegue mostrar o painel dentro dela
    regras = dict(regra.strip().split(" ", 1) for regra in politica.split(";"))
    assert regras == {  # nada de fora: as letras e as imagens só de dentro da própria página, e nada de avaliar texto como código
        "default-src": "'none'",
        "script-src": "'unsafe-inline'",
        "style-src": "'unsafe-inline'",
        "img-src": "data:",
        "font-src": "data:",
        "connect-src": "'self'",
        "base-uri": "'none'",
        "form-action": "'none'",
        "frame-ancestors": "'none'",
    }
    assert resposta.getheader("Cache-Control") == "no-store" and resposta.getheader("Referrer-Policy") == "no-referrer"
    assert f"<title>{TITULO_DO_PAINEL}</title>" in corpo and 'new EventSource("eventos")' in corpo


@pytest.mark.parametrize("caminho", ["/", "/eventos", "/errado/", "/errado/eventos", "/../", "/favicon.ico"])
def test_sem_o_codigo_nada_e_entregue(painel, caminho):
    conexao, resposta = pedir(painel, caminho)
    assert resposta.status == 404 and resposta.read() == b""
    conexao.close()


def test_codigo_certo_em_outro_caminho_tambem_nao(painel):
    base = urlsplit(painel.endereco).path
    for caminho in (base[:-1], base + "painel.html", base + "eventos/", base + "?x=1"):
        conexao, resposta = pedir(painel, caminho)
        assert resposta.status == 404, caminho
        conexao.close()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:80", "127.0.0.1", "127.0.0.1:1", "[::1]:8080", ""])
def test_pedido_que_veio_por_outro_nome_e_recusado(painel, host):
    """Uma página da internet que aponta um domínio para 127.0.0.1 chega com o nome dela no pedido."""
    conexao, resposta = pedir(painel, urlsplit(painel.endereco).path, host=host or "x")
    assert resposta.status == 403
    conexao.close()


@pytest.mark.parametrize("metodo", ["PUT", "DELETE", "PATCH"])
def test_metodos_que_o_painel_nao_conhece(painel, metodo):
    conexao, resposta = pedir(painel, urlsplit(painel.endereco).path, metodo=metodo)
    assert resposta.status == 501
    conexao.close()


# --- o pedido digitado na página -------------------------------------------------------------


def enviar(painel, corpo, caminho=None, origem="", tipo="application/json", host=None, tamanho=None):
    """Manda um pedido como a página faria. `origem=""` usa a da própria página."""
    endereco = urlsplit(painel.endereco)
    dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
    conexao = http.client.HTTPConnection("127.0.0.1", endereco.port, timeout=5)
    conexao.putrequest("POST", caminho or endereco.path + "pedido", skip_host=True)
    conexao.putheader("Host", host or f"127.0.0.1:{endereco.port}")
    if origem is not None:
        conexao.putheader("Origin", origem or f"http://127.0.0.1:{endereco.port}")
    if tipo:
        conexao.putheader("Content-Type", tipo)
    if tamanho != "":
        conexao.putheader("Content-Length", str(len(dados) if tamanho is None else tamanho))
    conexao.endheaders()
    conexao.send(dados)
    resposta = conexao.getresponse()
    corpo_da_resposta = resposta.read()
    conexao.close()
    return resposta.status, corpo_da_resposta


@pytest.fixture
def recebidos(painel):
    """Liga a caixa de texto do painel a uma lista. O que a conversa responderia vem de `recebidos.quando`."""

    class Recebidos(list):
        quando = "agora"

    lista = Recebidos()

    def receber(texto):
        lista.append(texto)
        return lista.quando

    painel.aceitar_pedidos(receber)
    return lista


def test_pedido_digitado_na_pagina_chega_a_conversa(painel, recebidos):
    estado, corpo = enviar(painel, {"texto": "  abre a planilha\tde custos \n"})
    assert estado == 200 and json.loads(corpo) == {"quando": "agora"}
    assert recebidos == ["abre a planilha de custos"]
    recebidos.quando = "fila"  # a conversa está ocupada: a página avisa quem digitou
    assert json.loads(enviar(painel, {"texto": "e depois fecha"})[1]) == {"quando": "fila"}
    recebidos.quando = "cheio"
    assert enviar(painel, {"texto": "mais um"})[0] == 429
    assert enviar(painel, {"texto": "olá"}, origem=f"http://localhost:{urlsplit(painel.endereco).port}", host=f"localhost:{urlsplit(painel.endereco).port}")[0] == 429
    assert len(recebidos) == 4  # pelo nome localhost também é a própria página


def test_sem_conversa_ligada_o_painel_nao_aceita_pedido(painel):
    assert enviar(painel, {"texto": "abre o site"})[0] == 503


@pytest.mark.parametrize("origem", ["https://evil.example", "http://127.0.0.1", "http://127.0.0.1:1", "null", None, "http://evil.example:80"])
def test_pedido_que_nao_veio_da_propria_pagina_e_recusado(painel, recebidos, origem):
    """Outra página aberta no navegador (ou um arquivo local, que chega como "null") não manda ordem ao Jarvis."""
    assert enviar(painel, {"texto": "apaga tudo"}, origem=origem)[0] == 403
    assert recebidos == []


@pytest.mark.parametrize("tipo", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_pedido_que_um_formulario_de_outra_pagina_conseguiria_mandar_e_recusado(painel, recebidos, tipo):
    """Só JSON: é o tipo que o navegador não deixa outra página enviar sem pedir licença antes."""
    assert enviar(painel, b'{"texto": "apaga tudo"}', tipo=tipo)[0] == 415
    assert recebidos == []


def test_pedido_por_outro_nome_ou_sem_o_codigo_e_recusado(painel, recebidos):
    base = urlsplit(painel.endereco).path
    assert enviar(painel, {"texto": "x"}, host="evil.example")[0] == 403
    for caminho in ("/pedido", "/errado/pedido", base, base + "pedido/", base + "eventos", base + "pedido?x=1"):
        assert enviar(painel, {"texto": "x"}, caminho=caminho)[0] == 404, caminho
    assert recebidos == []


@pytest.mark.parametrize(
    ("corpo", "estado"),
    [
        (b"isto nao e json", 400),
        (b"\xff\xfe", 400),
        (b"[1, 2]", 400),
        (b'"so um texto"', 400),
        ({"outro": "campo"}, 400),
        ({"texto": 42}, 400),
        ({"texto": ["abre", "o site"]}, 400),
        ({"texto": ""}, 400),
        ({"texto": "   \n\t "}, 400),
        ({"texto": "\x00\x1b\u200b"}, 400),
        ({"texto": "a" * (MAX_PEDIDO + 1)}, 413),
        (b" " * 20_000, 413),
        (b"[" * 12000, 400),  # colchetes aninhados aos milhares: quem lê o JSON desiste, e isso é só mais um pedido torto
    ],
)
def test_pedido_torto_e_recusado_sem_chegar_a_conversa(painel, recebidos, corpo, estado):
    assert enviar(painel, corpo)[0] == estado
    assert recebidos == []


def test_pedido_sem_tamanho_declarado_e_recusado(painel, recebidos):
    assert enviar(painel, {"texto": "x"}, tamanho="")[0] == 411
    assert enviar(painel, {"texto": "x"}, tamanho="-5")[0] == 413
    assert enviar(painel, {"texto": "x"}, tamanho="muito")[0] == 411
    assert recebidos == []


def test_pedido_no_limite_do_tamanho_passa(painel, recebidos):
    assert enviar(painel, {"texto": "ã" * MAX_PEDIDO})[0] == 200  # letras acentuadas ocupam mais bytes e cabem do mesmo jeito
    assert recebidos == ["ã" * MAX_PEDIDO]


def test_conversa_que_falha_ao_receber_nao_derruba_o_painel(painel):
    def quebrada(_texto):
        raise RuntimeError("a conversa caiu")

    painel.aceitar_pedidos(quebrada)
    assert enviar(painel, {"texto": "abre o site"})[0] == 503
    conexao, resposta = pedir(painel, urlsplit(painel.endereco).path)  # e a página continua sendo servida
    assert resposta.status == 200 and TITULO_DO_PAINEL.encode() in resposta.read()
    conexao.close()


def test_navegador_que_larga_a_conexao_no_meio_nao_suja_o_terminal(painel, capsys):
    conexao, resposta = pedir(painel, urlsplit(painel.endereco).path)
    assert resposta.status == 200
    conexao.sock.setsockopt(__import__("socket").SOL_SOCKET, __import__("socket").SO_LINGER, __import__("struct").pack("ii", 1, 0))
    conexao.close()  # sem ler a página: o servidor vê a conexão cair
    time.sleep(0.2)
    saida = capsys.readouterr()
    assert "Exception" not in saida.err and "Traceback" not in saida.err


def test_limpar_pedido_tira_o_que_nao_e_texto_visivel():
    assert limpar_pedido("  abre\to  YouTube\r\n agora ") == "abre o YouTube agora"
    assert limpar_pedido("apaga\x00 a \x1b[31mpasta\u200b\u202e") == "apaga a [31mpasta"  # controle e invisíveis somem
    assert limpar_pedido("ação, coração e 日本語 😀") == "ação, coração e 日本語 😀"
    assert limpar_pedido(None) == "" and limpar_pedido(b"abre") == "" and limpar_pedido("x" * (MAX_PEDIDO + 1)) == ""


def test_a_pagina_so_mostra_a_caixa_quando_a_conversa_aceita_pedidos(painel):
    fluxo = Fluxo(painel)
    assert fluxo.proximo()["digitar"] is False
    painel.aceitar_pedidos(lambda texto: "agora")
    assert fluxo.proximo() == {"tipo": "digitar", "sim": True}
    fluxo.fechar()
    novo = Fluxo(painel)
    assert novo.proximo()["digitar"] is True  # quem abre a página depois já recebe a caixa ligada
    novo.fechar()


# --- os ajustes escolhidos na página (cor, tema, movimento e voz) ------------------------------------------


@pytest.fixture
def com_arquivo(tmp_path):
    """Um painel que guarda os ajustes em um arquivo e oferece duas vozes, como o de verdade."""
    p = Painel(com_voz=True, arquivo=tmp_path / "dados" / "painel.json", vozes=("faber", "edresson"))
    p.iniciar()
    yield p
    for fila in list(p._assinantes):  # filas que o teste abriu e ninguém lê: sem isto o encerramento espera por elas
        p.cancelar(fila)
    p.fechar()


def mudar(painel, corpo, **mais):
    estado, resposta = enviar(painel, corpo, caminho=urlsplit(painel.endereco).path + "aparencia", **mais)
    return estado, json.loads(resposta) if resposta else None


def test_ajustes_de_fabrica_vao_no_retrato(painel):
    assert APARENCIA_PADRAO == {"cor": "azul", "tema": "escuro", "movimento": "medio", "voz": True, "voz_nome": "", "velocidade": 1.03}
    assert all(APARENCIA_PADRAO[nome] in valores for nome, valores in APARENCIAS.items())
    fluxo = Fluxo(painel)
    tudo = fluxo.proximo()
    assert tudo["aparencia"] == APARENCIA_PADRAO and tudo["vozes"] == [] and tudo["voz"] is True
    fluxo.fechar()
    assert painel.aparencia == APARENCIA_PADRAO
    painel.aparencia["cor"] = "verde"  # é uma cópia: mexer nela não muda o painel
    assert painel.aparencia["cor"] == "azul"


def test_as_vozes_oferecidas_vao_para_a_pagina(com_arquivo):
    assert json.loads(com_arquivo.assinar()[1])["vozes"] == ["faber", "edresson"]
    estranho = Painel(vozes=["faber", "faber", "", 7, "x" * 81, "ana"])  # repetida, vazia, não texto e comprida demais ficam de fora
    assert json.loads(estranho.assinar()[1])["vozes"] == ["faber", "ana"]
    assert len(json.loads(Painel(vozes=[f"voz{i}" for i in range(200)]).assinar()[1])["vozes"]) == 60


def test_trocar_um_ajuste_guarda_e_avisa_as_outras_paginas(com_arquivo):
    a, b = Fluxo(com_arquivo), Fluxo(com_arquivo)
    a.proximo(), b.proximo()
    estado, resposta = mudar(com_arquivo, {"cor": "verde"})  # só um dos ajustes: os outros ficam como estavam
    assert estado == 200 and resposta == {**APARENCIA_PADRAO, "cor": "verde"}
    assert a.proximo() == b.proximo() == {"tipo": "aparencia", **APARENCIA_PADRAO, "cor": "verde"}
    estado, resposta = mudar(com_arquivo, {"tema": "claro", "movimento": "rapido", "voz": False, "voz_nome": "edresson", "velocidade": 1.25})
    esperada = {"cor": "verde", "tema": "claro", "movimento": "rapido", "voz": False, "voz_nome": "edresson", "velocidade": 1.25}
    assert estado == 200 and resposta == esperada and a.proximo() == {"tipo": "aparencia", **esperada}
    arquivo = com_arquivo._arquivo
    assert json.loads(arquivo.read_text(encoding="utf-8")) == esperada  # a pasta nem existia: foi criada
    assert [f.name for f in arquivo.parent.iterdir()] == ["painel.json"]  # o arquivo provisório não fica para trás
    a.fechar(), b.fechar()
    novo = Fluxo(com_arquivo)
    assert novo.proximo()["aparencia"] == esperada and com_arquivo.aparencia == esperada  # quem abre a página depois já os recebe assim
    novo.fechar()


def test_ajuste_igual_ao_atual_nao_gera_evento_gravacao_nem_aviso(com_arquivo):
    fila, _ = com_arquivo.assinar()
    avisos = []
    com_arquivo.ao_aparencia = avisos.append
    assert mudar(com_arquivo, {"cor": "azul", "voz": True, "velocidade": 1.03}) == (200, APARENCIA_PADRAO)
    assert fila.empty() and not com_arquivo._arquivo.exists() and avisos == []


def test_velocidade_fica_na_regua_e_com_duas_casas(com_arquivo):
    assert mudar(com_arquivo, {"velocidade": 0.6})[1]["velocidade"] == 0.6 and mudar(com_arquivo, {"velocidade": 1.6})[1]["velocidade"] == 1.6
    assert mudar(com_arquivo, {"velocidade": 1})[1]["velocidade"] == 1.0  # número inteiro também é número
    assert mudar(com_arquivo, {"velocidade": 1.23456})[1]["velocidade"] == 1.23
    for fora in (0.59, 1.61, 0, -1, 16, "1.2", True, None, [1.2], float("inf")):
        assert mudar(com_arquivo, b'{"velocidade": Infinity}' if fora == float("inf") else {"velocidade": fora})[0] == 400, fora
    assert mudar(com_arquivo, b'{"velocidade": NaN}')[0] == 400
    assert com_arquivo.aparencia["velocidade"] == 1.23


def test_quem_criou_o_painel_e_avisado_de_cada_troca(com_arquivo):
    """É por `ao_aparencia` que a voz de verdade é ligada, desligada, trocada e muda de velocidade."""
    avisos = []

    def aplicar(ajustes):
        avisos.append(ajustes)
        assert com_arquivo.aparencia == ajustes  # fora da trava do painel: dá para consultá-lo...
        com_arquivo.legenda("jarvis", "voz trocada")  # ...e para publicar nele
        ajustes["cor"] = "mexido"  # o que chega é uma cópia

    com_arquivo.ao_aparencia = aplicar
    assert mudar(com_arquivo, {"voz": False})[0] == 200 and mudar(com_arquivo, {"voz_nome": "faber", "velocidade": 0.8})[0] == 200
    assert mudar(com_arquivo, {"voz_nome": "faber"})[0] == 200  # igual: sem aviso
    assert [(a["voz"], a["voz_nome"], a["velocidade"]) for a in avisos] == [(False, "", 1.03), (False, "faber", 0.8)]
    assert com_arquivo.aparencia["cor"] == "azul" and com_arquivo._retrato["jarvis"] == "voz trocada"


def test_falha_de_quem_aplica_o_ajuste_fica_no_registro_e_o_ajuste_vale(com_arquivo, caplog):
    def quebrada(_ajustes):
        raise RuntimeError("não achei a voz")

    com_arquivo.ao_aparencia = quebrada
    with caplog.at_level(logging.ERROR, logger="jarvis.painel"):
        assert mudar(com_arquivo, {"voz_nome": "edresson"}) == (200, {**APARENCIA_PADRAO, "voz_nome": "edresson"})
    assert "Não consegui aplicar o ajuste" in caplog.text and json.loads(com_arquivo._arquivo.read_text(encoding="utf-8"))["voz_nome"] == "edresson"
    assert mudar(com_arquivo, {"cor": "ciano"})[0] == 200  # e o painel segue atendendo


def test_ajustes_sobrevivem_a_reiniciar_o_jarvis(tmp_path):
    arquivo = tmp_path / "painel.json"
    primeiro = Painel(arquivo=arquivo, vozes=("faber",))
    primeiro.iniciar()
    assert mudar(primeiro, {"cor": "violeta", "tema": "claro", "voz": False, "voz_nome": "faber", "velocidade": 1.4})[0] == 200
    primeiro.fechar()
    guardado = {"cor": "violeta", "tema": "claro", "movimento": "medio", "voz": False, "voz_nome": "faber", "velocidade": 1.4}
    segundo = Painel(arquivo=arquivo, vozes=("faber",))  # outra execução: outro endereço, outro código, o mesmo arquivo
    assert segundo.codigo != primeiro.codigo and segundo.aparencia == guardado
    assert json.loads(segundo.assinar()[1])["aparencia"] == guardado
    assert Painel(arquivo=arquivo, vozes=("outra",)).aparencia == {**guardado, "voz_nome": ""}  # a voz guardada sumiu: volta para a padrão


@pytest.mark.parametrize(
    "conteudo",
    [None, "", "isto nao e json", "[1, 2]", '"azul"', "null", '{"cor": "rosa", "tema": 3, "movimento": null, "voz": "sim", "voz_nome": 5, "velocidade": "rápida"}', "[" * 50_000, b"\xff\xfe\x00"],
    # Nomes curtos: o do teste vai para uma variável de ambiente, que no Windows não passa de 32 mil letras.
    ids=["ausente", "vazio", "texto", "lista", "palavra", "nulo", "valores-tortos", "colchetes-sem-fim", "bytes-soltos"],
)
def test_arquivo_ausente_ou_estragado_da_nos_ajustes_de_fabrica(tmp_path, conteudo):
    arquivo = tmp_path / "painel.json"
    if isinstance(conteudo, bytes):
        arquivo.write_bytes(conteudo)
    elif conteudo is not None:
        arquivo.write_text(conteudo, encoding="utf-8")
    assert ler_aparencia(arquivo) == APARENCIA_PADRAO
    assert Painel(arquivo=arquivo).aparencia == APARENCIA_PADRAO


def test_do_arquivo_so_vale_o_que_e_conhecido(tmp_path):
    arquivo = tmp_path / "painel.json"
    arquivo.write_text(json.dumps({"cor": "ambar", "tema": "noturno", "forma": "cubo", "outra": "coisa", "movimento": "Rapido", "voz": False, "velocidade": 9}), encoding="utf-8")
    assert ler_aparencia(arquivo) == {**APARENCIA_PADRAO, "cor": "ambar", "voz": False}  # "forma" é de uma versão antiga: ignorada
    assert ler_aparencia(None) == APARENCIA_PADRAO and ler_aparencia(tmp_path) == APARENCIA_PADRAO  # sem arquivo; uma pasta no lugar dele
    assert ler_aparencia(arquivo) is not APARENCIA_PADRAO


def test_sem_arquivo_os_ajustes_valem_enquanto_o_jarvis_roda(painel):
    assert mudar(painel, {"movimento": "lento"}) == (200, {**APARENCIA_PADRAO, "movimento": "lento"})
    assert painel.aparencia["movimento"] == "lento"


def test_falha_ao_gravar_os_ajustes_nao_derruba_nada(tmp_path, caplog):
    estorvo = tmp_path / "estorvo"
    estorvo.write_text("um arquivo onde devia haver uma pasta", encoding="utf-8")
    p = Painel(arquivo=estorvo / "painel.json")
    p.iniciar()
    try:
        fila, _ = p.assinar()
        avisos = []
        p.ao_aparencia = avisos.append
        with caplog.at_level(logging.ERROR, logger="jarvis.painel"):
            assert mudar(p, {"tema": "claro"}) == (200, {**APARENCIA_PADRAO, "tema": "claro"})  # vale para esta execução
        assert json.loads(fila.get_nowait()) == {"tipo": "aparencia", **APARENCIA_PADRAO, "tema": "claro"}
        assert "Não consegui guardar a aparência" in caplog.text and estorvo.read_text(encoding="utf-8").startswith("um arquivo")
        assert len(avisos) == 1  # e quem aplica o ajuste foi avisado do mesmo jeito
        p.cancelar(fila)
    finally:
        p.fechar()


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"cor": "rosa"},
        {"cor": "Azul"},
        {"cor": "azul "},
        {"cor": 3},
        {"cor": None},
        {"cor": ["azul"]},
        {"tema": "azul"},  # valor de outro ajuste
        {"forma": "cubo"},  # a forma da rede não é mais ajuste
        {"cor": "verde", "brilho": "alto"},  # um nome desconhecido estraga o pedido inteiro
        {"cor": "verde", "movimento": "parado"},
        {"voz": "sim"},
        {"voz": 1},
        {"voz": None},
        {"voz_nome": "uma que o Jarvis não tem"},
        {"voz_nome": 3},
        {"voz_nome": None},
        {"velocidade": "1.2"},
        {"velocidade": 2},
        {"__proto__": "x"},
        ["cor", "verde"],
        "verde",
        None,
        b"isto nao e json",
        b"\xff\xfe",
        b"[" * 300,
    ],
)
def test_ajuste_desconhecido_e_recusado_sem_mudar_nada(com_arquivo, corpo):
    fila, _ = com_arquivo.assinar()
    avisos = []
    com_arquivo.ao_aparencia = avisos.append
    assert mudar(com_arquivo, corpo) == (400, None)
    assert com_arquivo.aparencia == APARENCIA_PADRAO and fila.empty() and not com_arquivo._arquivo.exists() and avisos == []


def test_validar_aparencia():
    assert validar_aparencia({"cor": "ciano", "movimento": "lento"}) == {"cor": "ciano", "movimento": "lento"}
    for nome, valores in APARENCIAS.items():
        assert all(validar_aparencia({nome: valor}) == {nome: valor} for valor in valores)
    assert validar_aparencia({"voz": False, "voz_nome": "", "velocidade": 1.449}) == {"voz": False, "voz_nome": "", "velocidade": 1.45}
    assert validar_aparencia({"voz_nome": "faber"}, ("faber",)) == {"voz_nome": "faber"} and validar_aparencia({"voz_nome": "faber"}) is None
    assert validar_aparencia({}) is None and validar_aparencia({"cor": "x"}) is None and validar_aparencia("cor") is None
    assert validar_aparencia({1: "azul"}) is None and validar_aparencia({"cor": {"azul": 1}}) is None


def test_troca_de_ajuste_tem_as_mesmas_travas_do_pedido(com_arquivo):
    porta, base = urlsplit(com_arquivo.endereco).port, urlsplit(com_arquivo.endereco).path
    boa = {"cor": "vermelho"}
    for origem in ("https://evil.example", "http://127.0.0.1", f"http://127.0.0.1:{porta + 1}", "null", None):
        assert mudar(com_arquivo, boa, origem=origem)[0] == 403, origem  # outra página aberta no navegador não mexe no painel (nem na voz)
    assert mudar(com_arquivo, boa, host="evil.example")[0] == 403
    for tipo in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
        assert mudar(com_arquivo, b'{"cor": "vermelho"}', tipo=tipo)[0] == 415, tipo
    for caminho in ("/aparencia", "/errado/aparencia", base + "aparencia/", base + "aparencia?x=1", base + "Aparencia"):
        assert enviar(com_arquivo, boa, caminho=caminho)[0] == 404, caminho
    assert mudar(com_arquivo, boa, tamanho="")[0] == 411 and mudar(com_arquivo, boa, tamanho="-1")[0] == 413
    assert mudar(com_arquivo, b'{"cor": "vermelho"' + b" " * MAX_APARENCIA + b"}")[0] == 413  # JSON certo, mas grande demais para o que é
    assert mudar(com_arquivo, b" " * 20_000)[0] == 413  # maior até que um pedido: recusado (o corpo é lido e jogado fora, para a recusa chegar inteira)
    assert com_arquivo.aparencia == APARENCIA_PADRAO and not com_arquivo._arquivo.exists()
    conexao, resposta = pedir(com_arquivo, base + "aparencia")  # e não há o que ler ali
    assert resposta.status == 404
    conexao.close()
    assert mudar(com_arquivo, boa, origem=f"http://localhost:{porta}", host=f"localhost:{porta}") == (200, {**APARENCIA_PADRAO, "cor": "vermelho"})


def test_trocas_ao_mesmo_tempo_deixam_o_arquivo_igual_ao_retrato(com_arquivo):
    import threading

    vistos = []
    com_arquivo.ao_aparencia = vistos.append
    turmas = [threading.Thread(target=lambda c=cor: [mudar(com_arquivo, {"cor": c, "movimento": m}) for m in APARENCIAS["movimento"]]) for cor in APARENCIAS["cor"]]
    for t in turmas:
        t.start()
    for t in turmas:
        t.join()
    assert json.loads(com_arquivo._arquivo.read_text(encoding="utf-8")) == com_arquivo.aparencia == vistos[-1]  # o último aviso é o estado final


# --- os cartões que o Jarvis abre na página ----------------------------------------------------------------

CLIMA = {
    "categoria": "Previsão · Clima",
    "titulo": "Maragogi",
    "icone": "nublado",
    "badge": "+1,2°",
    "destaque": "26,7°",
    "sub": "Predomínio de sol",
    "itens": [{"r": "Mínima", "v": "23,3°"}, {"r": "Máxima", "v": "27,5°"}],
    "grafico": [31, 29, 25.5],
    "graficoRotulos": ["16h", "19h", "22h"],
    "previsao": [{"dia": "qua.", "icone": "sol", "max": "31°", "min": "21°"}, {"dia": "qui.", "icone": "tempestade", "max": "34°", "min": "22°"}],
    "barra": 62,
    "fonte": "Climatempo",
    "acao": "Próximos dias",
}
NOTICIA = {"categoria": "Radar · Notícias", "titulo": "Tecnologia", "imagem": "imagem da manchete", "manchete": "Novos modelos", "link": "https://g1.globo.com/tecnologia/", "quando": "2h", "texto": "Resumo.", "fonte": "g1"}
SISTEMA = {"categoria": "Sistema · Núcleo", "titulo": "Diagnóstico", "escuro": True, "lista": [{"r": "Núcleo neural", "v": "Operacional"}, {"r": "Sensores", "v": "Conectados"}]}


def test_cartao_bem_feito_passa_inteiro_pelo_filtro():
    assert limpar_paineis([CLIMA, NOTICIA, SISTEMA]) == [CLIMA, NOTICIA, SISTEMA]
    assert json.loads(json.dumps(limpar_paineis([CLIMA]))) == [CLIMA]  # e é JSON simples


def test_filtro_dos_cartoes_so_deixa_o_que_a_pagina_desenha():
    sujo = {
        **CLIMA,
        "titulo": "  Maragogi\n\x00\u200b  ",  # aparado, em uma linha, sem invisíveis
        "imagemUrl": "https://exemplo.com/foto.png",  # a página não busca imagem nenhuma
        "onclick": "alert(1)",
        "html": "<img src=x onerror=alert(1)>",
        "icone": "caveira",  # só os desenhos que a página tem
        "escuro": "sim",  # só verdadeiro de verdade
        "barra": 250,
        "itens": [{"r": "Mínima", "v": 23.3}, {"rotulo": "Chuva", "valor": 30}, "torto", {"x": 1}, {"r": "", "v": ""}],
        "grafico": [31, "29", None, True, float("nan"), 25.5, 20],
        "graficoRotulos": ["16h", "19h", "22h", "01h", "04h", "07h", "10h"],
        "previsao": [{"dia": "qua.", "icone": "furacão", "max": "31°", "min": "21°", "extra": 1}, "torto", {}],
    }
    limpo = limpar_paineis([sujo])[0]
    assert limpo["titulo"] == "Maragogi" and "imagemUrl" not in limpo and "onclick" not in limpo and "html" not in limpo and "icone" not in limpo and "escuro" not in limpo
    assert limpo["barra"] == 100 and limpar_paineis([{"titulo": "x", "barra": -5}])[0]["barra"] == 0
    assert limpo["itens"] == [{"r": "Mínima", "v": "23.3"}, {"r": "Chuva", "v": "30"}]
    assert limpo["grafico"] == [31.0, 25.5, 20.0] and limpo["graficoRotulos"] == ["16h", "07h", "10h"]  # cada número com o seu rótulo
    assert limpo["previsao"] == [{"dia": "qua.", "max": "31°", "min": "21°"}]
    assert set(limpo) <= {"categoria", "titulo", "badge", "destaque", "sub", "itens", "lista", "imagem", "manchete", "link", "quando", "texto", "barra", "icone", "grafico", "graficoRotulos", "previsao", "escuro", "fonte", "acao"}


def test_filtro_dos_cartoes_limita_quantidade_e_tamanho():
    assert [c["titulo"] for c in limpar_paineis([{"titulo": str(i)} for i in range(9)])] == ["0", "1", "2"]
    assert limpar_paineis([{}, "x", None, 3, {"escuro": True}, {"desconhecido": 1}, {"titulo": "   "}]) == []  # cartão sem nada a mostrar não é cartão
    for torto in (None, "cartões", 7, {"titulo": "x"}, [[{"titulo": "x"}]]):
        assert limpar_paineis(torto) == []
    grande = limpar_paineis([{
        "titulo": "t" * 500, "texto": "x" * 5000, "badge": "b" * 500, "lista": [{"r": "r" * 500, "v": "v" * 500}] * 40, "itens": [{"r": "a", "v": "b"}] * 40,
        "grafico": list(range(100)), "graficoRotulos": ["rótulo comprido demais"] * 100, "previsao": [{"dia": "d" * 50, "max": "1", "min": "0"}] * 30,
    }])[0]  # fmt: skip
    assert len(grande["titulo"]) == 120 and len(grande["texto"]) == 400 and len(grande["badge"]) == 40
    assert len(grande["lista"]) == 6 and len(grande["lista"][0]["r"]) == 120 and len(grande["lista"][0]["v"]) == 200 and len(grande["itens"]) == 8
    assert len(grande["grafico"]) == 24 and len(grande["graficoRotulos"]) == 24 and all(len(r) <= 12 for r in grande["graficoRotulos"])
    assert len(grande["previsao"]) == 8 and len(grande["previsao"][0]["dia"]) == 12
    assert len(json.dumps(limpar_paineis([grande] * 3))) < 12_000  # o que vai para a página tem teto


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///C:/Windows", "//exemplo.com", "exemplo.com", " https://exemplo.com", "https://exemplo.com/a b",
     "https://exemplo.com/\n", "https://exemplo.com/\"onmouseover=\"x", "https://exemplo.com/<script>", "https://" + "a" * 300, 42, None, ["https://exemplo.com"]],
)  # fmt: skip
def test_link_de_cartao_so_passa_se_for_http_limpo(link):
    assert "link" not in limpar_paineis([{"titulo": "x", "link": link}])[0]


def test_link_bom_passa():
    for link in ("https://g1.globo.com/tecnologia/", "http://exemplo.com.br/a?b=1&c=2#d", "HTTPS://EXEMPLO.COM"):
        assert limpar_paineis([{"titulo": "x", "link": link}])[0]["link"] == link


def test_paineis_chegam_limpos_a_pagina_e_ficam_um_minuto_no_retrato(painel, monkeypatch):
    fluxo = Fluxo(painel)
    primeiro = fluxo.proximo()
    assert primeiro["paineis"] == [] and primeiro["paineis_idade"] == 0
    painel.paineis([{**CLIMA, "imagemUrl": "https://exemplo.com/x.png"}, NOTICIA, SISTEMA, {"titulo": "o quarto fica de fora"}])
    assert fluxo.proximo() == {"tipo": "paineis", "lista": [CLIMA, NOTICIA, SISTEMA]}
    fluxo.fechar()
    agora = time.monotonic()
    with monkeypatch.context() as relogio:  # o relógio volta ao normal antes de o painel ser fechado: fechar conta tempo
        relogio.setattr(modulo.time, "monotonic", lambda: agora + 20)
        fila, retrato = painel.assinar()
        depois = json.loads(retrato)  # quem abre a página vinte segundos depois ainda os vê, e sabe há quanto tempo estão abertos
        painel.cancelar(fila)
        assert depois["paineis"] == [CLIMA, NOTICIA, SISTEMA] and 19 <= depois["paineis_idade"] <= 21
        relogio.setattr(modulo.time, "monotonic", lambda: agora + 61)
        fila, retrato = painel.assinar()
        tarde = json.loads(retrato)
        painel.cancelar(fila)
        assert tarde["paineis"] == [] and tarde["paineis_idade"] == 0  # passou o minuto: a página já os fechou


def test_lista_vazia_de_paineis_fecha_os_abertos(painel):
    fila, _ = painel.assinar()
    painel.paineis([CLIMA])
    painel.paineis([])
    painel.paineis("isto não é uma lista")
    assert [json.loads(fila.get_nowait())["lista"] for _ in range(3)] == [[CLIMA], [], []]
    assert json.loads(painel.assinar()[1])["paineis"] == []
    painel.cancelar(fila)


def test_so_escuta_nesta_maquina(painel):
    assert painel._servidor.server_address[0] == "127.0.0.1"


# --- fluxo de eventos -----------------------------------------------------------------------


def test_quem_chega_recebe_o_retrato_completo_e_depois_as_mudancas(painel):
    painel.legenda("voce", "que horas são?")
    painel.fase("pensando")
    fluxo = Fluxo(painel)
    tudo = fluxo.proximo()
    assert tudo["tipo"] == "tudo" and tudo["estado"] == "pensando" and tudo["voce"] == "que horas são?"
    assert tudo["atalho"] == "Ctrl+Alt+J" and tudo["voz"] is True and tudo["titulo"] == TITULO_DO_PAINEL
    assert tudo["conhecimento"] == {"itens": [], "conversas": 0}
    painel.fase("agindo", "abrir_site: https://exemplo.com.br")
    assert fluxo.proximo() == {"tipo": "estado", "estado": "agindo", "detalhe": "abrir_site: https://exemplo.com.br"}
    painel.legenda("jarvis", "São  três\ne meia.")
    assert fluxo.proximo() == {"tipo": "legenda", "quem": "jarvis", "texto": "São três e meia."}
    painel.nivel(0.456)
    assert fluxo.proximo() == {"tipo": "nivel", "v": 0.46}
    fluxo.fechar()


def test_ao_encerrar_a_pagina_e_avisada(painel):
    fluxo = Fluxo(painel)
    fluxo.proximo()
    painel.fechar()
    assert fluxo.proximo() == {"tipo": "fim"}
    fluxo.fechar()


def test_navegador_que_fecha_deixa_de_ser_assinante(painel):
    fluxo = Fluxo(painel)
    fluxo.proximo()
    assert painel.assinantes == 1
    fluxo.fechar()
    for _ in range(3):
        painel.nivel(0.5)  # a escrita seguinte percebe a conexão fechada
        time.sleep(0.05)
    assert esperar(lambda: (painel.nivel(0.1), painel.assinantes == 0)[1])


def test_navegador_travado_e_solto_em_vez_de_acumular():
    painel = Painel()
    fila, _ = painel.assinar()
    for i in range(modulo._FILA_MAX + 5):
        painel.nivel(i % 2)
    assert painel.assinantes == 0 and fila.qsize() == modulo._FILA_MAX


def test_dois_navegadores_recebem_o_mesmo(painel):
    a, b = Fluxo(painel), Fluxo(painel)
    a.proximo(), b.proximo()
    painel.fase("pensando")
    assert a.proximo() == b.proximo() == {"tipo": "estado", "estado": "pensando", "detalhe": ""}
    a.fechar(), b.fechar()


# --- composição do estado ------------------------------------------------------------------------


def estados(painel):
    fila, primeiro = painel.assinar()
    vistos = []

    def ler():
        while not fila.empty():
            evento = json.loads(fila.get_nowait())
            if evento["tipo"] == "estado":
                vistos.append(evento["estado"])
        return vistos

    return ler


def test_falar_passa_na_frente_de_pensar_e_ouvir_na_frente_de_tudo():
    painel = Painel()
    ler = estados(painel)
    painel.fase("pensando")
    painel.som()  # a fala começa enquanto a resposta ainda está chegando
    painel.fase("agindo", "mover")  # ele segue falando; a ação não toma a frente
    painel.fase(None)  # o turno acabou, mas a voz ainda está tocando
    painel.silencio()
    assert ler() == ["pensando", "falando", "espera"]
    painel.som()
    painel.fase("ouvindo")  # o usuário apertou o atalho no meio da fala
    painel.silencio()  # a fala foi cortada
    painel.fase("entendendo")
    painel.fase("pensando")
    painel.fase(None)
    assert ler()[3:] == ["falando", "ouvindo", "entendendo", "pensando", "espera"]


def test_estado_repetido_nao_gera_evento():
    painel = Painel()
    ler = estados(painel)
    painel.fase("pensando"), painel.fase("pensando"), painel.silencio(), painel.fase(None), painel.fase(None)
    assert ler() == ["pensando", "espera"]


def test_detalhe_so_aparece_com_a_fase_dele():
    painel = Painel()
    fila, _ = painel.assinar()
    painel.fase("agindo", "x" * 500)
    evento = json.loads(fila.get_nowait())
    assert evento["estado"] == "agindo" and len(evento["detalhe"]) == 120
    painel.som()
    assert json.loads(fila.get_nowait()) == {"tipo": "estado", "estado": "falando", "detalhe": ""}


def test_fase_desconhecida_e_erro_de_programa():
    with pytest.raises(ValueError):
        Painel().fase("falando")  # quem diz que está falando é o som, não a conversa
    with pytest.raises(ValueError):
        Painel().legenda("alguem", "x")


def test_pergunta_nova_tira_a_resposta_antiga_da_tela():
    painel = Painel()
    painel.legenda("voce", "primeira")
    painel.legenda("jarvis", "resposta " * 200)
    assert len(painel._retrato["jarvis"]) == modulo.MAX_LEGENDA
    painel.legenda("voce", "segunda")
    assert painel._retrato["jarvis"] == "" and painel._retrato["voce"] == "segunda"


# --- a voz ------------------------------------------------------------------------------------------


def test_contorno_da_voz_acompanha_o_volume():
    np = pytest.importorskip("numpy")
    taxa = 16000
    t = np.arange(taxa) / taxa
    forte, fraco, mudo = np.sin(2 * np.pi * 220 * t[:8000]) * 20000, np.sin(2 * np.pi * 220 * t[:4000]) * 5000, np.zeros(4000)
    contorno = modulo.contorno_da_voz(np.concatenate([forte, fraco, mudo]).astype(np.int16), taxa)
    assert len(contorno) == 25  # um ponto a cada 40 ms de um segundo de áudio
    assert max(contorno) == 1.0 and all(0.0 <= v <= 1.0 for v in contorno)
    assert contorno[2] > 0.9 and 0.15 < contorno[15] < 0.35 and contorno[-1] == 0.0
    assert modulo.contorno_da_voz(np.zeros(0, dtype=np.int16), taxa) == []
    assert modulo.contorno_da_voz(np.zeros(8000, dtype=np.int16), taxa) == [0.0] * 12


def test_som_publica_o_contorno_e_o_silencio_encerra():
    np = pytest.importorskip("numpy")
    painel = Painel()
    fila, _ = painel.assinar()
    painel.som((np.sin(np.arange(4000) / 5) * 9000).astype(np.int16), 16000)
    eventos = [json.loads(fila.get_nowait()) for _ in range(2)]
    assert eventos[0] == {"tipo": "estado", "estado": "falando", "detalhe": ""}
    assert eventos[1]["tipo"] == "voz" and len(eventos[1]["contorno"]) == 6 and eventos[1]["passo"] == 0.04
    painel.som(object(), 16000)  # áudio estranho: fala sem contorno, sem erro
    assert json.loads(fila.get_nowait())["contorno"] == []
    painel.silencio()
    assert json.loads(fila.get_nowait()) == {"tipo": "voz", "contorno": [], "passo": 0.04, "fim": True}
    assert json.loads(fila.get_nowait())["estado"] == "espera"
    painel.silencio()  # já estava calado: nada novo
    assert fila.empty()


def test_contorno_de_texto_tem_o_ritmo_da_leitura():
    contorno = contorno_de_texto("Abri o site. Pronto!")
    assert 20 < len(contorno) < 80 and max(contorno) <= 1.0 and min(contorno) == 0.0
    assert contorno_de_texto("") == []
    assert len(contorno_de_texto("a" * 5000)) == len(contorno_de_texto("a" * modulo.MAX_LEGENDA))


def test_no_modo_texto_a_rede_fala_e_se_cala_sozinha(monkeypatch):
    monkeypatch.setattr(modulo, "PASSO_DO_ENVELOPE", 0.001)
    painel = Painel()
    ler = estados(painel)
    painel.falar_texto("Pronto.")
    assert painel._retrato["estado"] == "falando"
    assert esperar(lambda: painel._retrato["estado"] == "espera")
    assert ler() == ["falando", "espera"]
    painel.falar_texto("")  # nada a dizer: nada muda
    assert ler() == ["falando", "espera"]
    painel.falar_texto("Uma resposta bem mais longa que a anterior.")
    painel.silencio()  # o usuário falou por cima
    assert painel._retrato["estado"] == "espera" and painel._relogio_de_texto is None


# --- conhecimento ---------------------------------------------------------------------------------------


def test_retrato_do_conhecimento_vem_da_memoria_e_do_registro(tmp_path):
    memoria, registro = Memoria(tmp_path / "memoria"), Registro(":memory:")
    assert conhecimento(memoria, registro) == {"itens": [], "conversas": 0}
    memoria.salvar("Apelido", "Prefere ser chamado de chefe.")
    memoria.salvar("Navegador", "Abrir sempre no Chrome.", categoria="preferencia")
    memoria.salvar("Listas", "Citar três e dizer quantos são.", categoria="licao")
    registro.gravar(Turno("oi", resposta="olá"))
    registro.gravar(Turno("tudo bem?", resposta="tudo"))
    assert conhecimento(memoria, registro) == {
        "itens": [
            {"c": "fato", "n": "apelido", "t": "Apelido"},
            {"c": "preferencia", "n": "navegador", "t": "Navegador"},
            {"c": "licao", "n": "listas", "t": "Listas"},
        ],
        "conversas": 2,
    }
    assert "chefe" not in json.dumps(conhecimento(memoria, registro))  # o conteúdo da anotação não vai para a página


def test_registro_com_defeito_nao_derruba_o_painel(tmp_path):
    registro = Registro(":memory:")
    registro.fechar()
    assert conhecimento(Memoria(tmp_path / "m"), registro) == {"itens": [], "conversas": 0}


def test_conhecimento_so_e_publicado_quando_muda():
    painel = Painel()
    fila, _ = painel.assinar()
    retrato = {"itens": [{"c": "fato", "n": "a", "t": "A"}], "conversas": 3}
    painel.conhecimento(retrato)
    painel.conhecimento(dict(retrato))
    assert json.loads(fila.get_nowait()) == {"tipo": "conhecimento", **retrato} and fila.empty()
    painel.conhecimento({**retrato, "conversas": 4})
    assert json.loads(fila.get_nowait())["conversas"] == 4


# --- abrir a janela -----------------------------------------------------------------------------------------


def test_no_windows_abre_como_aplicativo_do_chrome():
    abertos = []
    como = abrir_janela("http://127.0.0.1:5000/abc/", iniciar=lambda prog, arguments: abertos.append((prog, arguments)), sistema="win32")
    assert como == "chrome"
    assert abertos == [("chrome", "--app=http://127.0.0.1:5000/abc/ --window-size=960,620")]  # a página é desenhada deitada


def test_sem_chrome_tenta_o_edge_e_depois_o_navegador_padrao():
    tentativas = []

    def so_edge(prog, arguments):
        tentativas.append(prog)
        if prog == "chrome":
            raise FileNotFoundError(prog)

    assert abrir_janela("http://x/", iniciar=so_edge, sistema="win32") == "msedge" and tentativas == ["chrome", "msedge"]

    def nenhum(prog, arguments):
        raise OSError(prog)

    navegados = []
    assert abrir_janela("http://x/", iniciar=nenhum, navegar=lambda u: navegados.append(u) or True, sistema="win32") == "navegador padrão"
    assert navegados == ["http://x/"]


def test_fora_do_windows_ou_sem_navegador():
    assert abrir_janela("http://x/", navegar=lambda u: True, sistema="linux") == "navegador padrão"
    assert abrir_janela("http://x/", navegar=lambda u: False, sistema="linux") == ""

    def quebra(_u):
        raise RuntimeError("sem ambiente gráfico")

    assert abrir_janela("http://x/", navegar=quebra, sistema="linux") == ""


# --- o painel desligado e a página ----------------------------------------------------------------------------


def test_painel_mudo_aceita_os_mesmos_avisos():
    publicos = {n for n in vars(Painel) if not n.startswith("_") and n not in ("assinar", "cancelar", "assinantes", "iniciar")}
    assert publicos <= set(vars(PainelMudo)), publicos - set(vars(PainelMudo))
    MUDO.fase("pensando"), MUDO.som(), MUDO.silencio(), MUDO.falar_texto("x"), MUDO.nivel(1), MUDO.legenda("voce", "x")
    MUDO.conhecimento({}), MUDO.fechar(), MUDO.aceitar_pedidos(lambda texto: "agora"), MUDO.paineis([{"titulo": "x"}]), MUDO.trabalhos([])
    assert MUDO.endereco == "" and MUDO.aparencia == APARENCIA_PADRAO and MUDO.ao_aparencia is None
    MUDO.aparencia["cor"] = "verde"  # cópia: o painel desligado não guarda nada
    assert MUDO.aparencia == APARENCIA_PADRAO
    MUDO.ao_aparencia = None  # quem liga o painel pode atribuir sem perguntar se ele existe


def test_a_pagina_nao_busca_nada_fora_da_maquina():
    pagina = modulo.PAGINA.read_text(encoding="utf-8")
    enderecos = set(re.findall(r"https?://[^\s'\"<>)]+", pagina))
    assert enderecos <= {"http://www.w3.org/2000/svg"}, enderecos  # só o nome do formato SVG, que não é baixado
    assert "<script src" not in pagina and "<link" not in pagina and "@import" not in pagina
    assert pagina.count("<script") == 1 and "<iframe" not in pagina and "<img" not in pagina
    # As letras vêm embutidas (a licença delas acompanha o programa); nada mais é carregado por endereço.
    assert set(re.findall(r"url\((.{0,23})", pagina)) == {"data:font/woff2;base64,"}
    assert pagina.count("@font-face") == 5 and "IBM Plex" in modulo.PAGINA.with_name("LICENCA-IBM-Plex.txt").read_text(encoding="utf-8")
    for estado in modulo.ESTADOS:
        assert f"  {estado}:" in pagina, estado  # todo estado que o Jarvis publica tem desenho na página
    assert "innerHTML" not in pagina and "insertAdjacentHTML" not in pagina and "document.write" not in pagina  # texto de fora nunca vira HTML
    assert "eval(" not in pagina and "new Function" not in pagina  # a política da página nem deixaria
    # O que a página manda vai para o próprio painel, em JSON, e para mais lugar nenhum: o pedido digitado e a aparência.
    assert sorted(re.findall(r'fetch\("(\w+)", \{ method: "POST", headers: \{ "Content-Type": "application/json" \}', pagina)) == ["aparencia", "pedido"]
    assert pagina.count("fetch(") == 2 and "XMLHttpRequest" not in pagina and "sendBeacon" not in pagina and "WebSocket" not in pagina
    assert "<form" not in pagina  # nada de formulário: a política da página proíbe enviar um
    # A aparência não fica no navegador: o endereço muda a cada execução, e quem a guarda é o painel.py.
    assert "localStorage" not in pagina and "sessionStorage" not in pagina and "indexedDB" not in pagina and "document.cookie" not in pagina


def test_a_pagina_conhece_os_mesmos_ajustes_que_o_servidor():
    pagina = modulo.PAGINA.read_text(encoding="utf-8")
    roteiro = pagina[pagina.index("<script>") :]
    for constante, nome in (("CORES", "cor"), ("TEMAS", "tema"), ("MOVIMENTOS", "movimento")):
        trecho = roteiro[roteiro.index(f"const {constante} = {{") :]
        trecho = trecho[: trecho.index("};")]
        nomes = tuple(n for n in re.findall(r"([a-z]+): (?:\{|\")", trecho) if n != "nome")  # "nome" é o rótulo de cada cor
        assert nomes == APARENCIAS[nome], constante
    for cor in APARENCIAS["cor"][1:]:
        assert f':root[data-cor="{cor}"]' in pagina, cor  # a primeira é a que a página traz de fábrica
    assert 'data-tema="escuro" data-cor="azul"' in pagina and ':root[data-tema="claro"]' in pagina
    de_fabrica = re.search(r"const ajustes = (\{[^}]*\});", roteiro).group(1)
    assert json.loads(re.sub(r"(\w+):", r'"\1":', de_fabrica)) == APARENCIA_PADRAO
    assert 'min="0.6" max="1.6" step="0.05"' in pagina  # a régua da velocidade é a do servidor
    assert (modulo.VELOCIDADE_MIN, modulo.VELOCIDADE_MAX) == (0.6, 1.6)
    assert "forma" not in de_fabrica and "localStorage" not in pagina


def test_a_pagina_desenha_os_mesmos_icones_que_o_servidor_aceita():
    pagina = modulo.PAGINA.read_text(encoding="utf-8")
    trecho = pagina[pagina.index("const ICONES = (light) => {") :]
    trecho = trecho[trecho.index("return {") : trecho.index("\n  };")]
    assert tuple(re.findall(r"^    (\w+): ", trecho, flags=re.M)) == ICONES_DOS_PAINEIS
    # Cartão não traz imagem de fora: a página nem conhece endereço de imagem, e o link só abre se for http(s), em outra janela.
    assert "imagemUrl" not in pagina and "new Image" not in pagina and ".src =" not in pagina
    assert 'a.target = "_blank"; a.rel = "noopener noreferrer"' in pagina and "/^https?:\\/\\//i.test(p.link)" in pagina


def test_a_pagina_guarda_as_pecas_de_que_os_outros_dependem():
    pagina = modulo.PAGINA.read_text(encoding="utf-8")
    for peca in ('id="pedir"', 'id="texto"', 'id="enviar"', 'id="aviso"', 'id="obras"', 'id="voce"', 'id="jarvis"'):
        assert pagina.count(peca) == 1, peca
    assert 'id="pedir" hidden' in pagina  # a caixa de pedido nasce escondida: quem a mostra é o evento "digitar"
    for tipo in ("tudo", "aparencia", "paineis", "digitar", "trabalhos", "estado", "nivel", "voz", "legenda", "conhecimento", "fim"):
        assert f'case "{tipo}"' in pagina, tipo
    for aviso in (
        "pedido comprido demais",
        "há pedidos demais esperando; aguarde um pouco",
        "a conversa não está recebendo pedidos agora",
        "não consegui enviar",
        "o Jarvis não respondeu; ele ainda está aberto?",
        "anotado: faço assim que terminar o que está em andamento",
        '"parei"',
    ):
        assert aviso in pagina, aviso
    for estado_do_trabalho in ("rodando", "concluido", "falhou", "limite", "parado", "interrompido"):
        assert re.search(rf"\b{estado_do_trabalho}: \"", pagina), estado_do_trabalho  # cada estado de trabalho tem nome no cartão
    assert "prefers-reduced-motion" in pagina and "document.hidden" in pagina
    # A rede: os 240 neurônios do desenho, e mais alguns com o que ele aprende.
    assert "MIN_NOS = 240, MAX_NOS = 420" in pagina and "MIN_NOS + itens + Math.round(2 * Math.sqrt(conversas))" in pagina
    assert "Sistemas online. Todos os núcleos operando normalmente. Como posso ajudar, senhor?" in pagina  # a saudação do desenho, só na tela
    assert "speechSynthesis" not in pagina and "window.claude" not in pagina  # quem fala é o Jarvis de verdade; a página não inventa resposta


def test_recusa_sem_tamanho_anunciado_ainda_esvazia_o_corpo(com_arquivo):
    """Fechar com dados por ler faz o Windows cortar a conexão, e a recusa não chega a quem mandou.

    O caso que escapava: `Content-Length` vazio (411) ou negativo (413). O corpo veio do mesmo
    jeito, o cálculo dava zero, nada era lido — e o navegador via conexão cortada em vez do motivo.
    """
    porta = urlsplit(com_arquivo.endereco).port
    for tamanho, esperado in (("", 411), ("-1", 413), ("abc", 411)):
        codigo = mudar(com_arquivo, {"cor": "vermelho"}, tamanho=tamanho,
                       origem=f"http://127.0.0.1:{porta}", host=f"127.0.0.1:{porta}")[0]
        assert codigo == esperado, tamanho
    assert com_arquivo.aparencia == APARENCIA_PADRAO
