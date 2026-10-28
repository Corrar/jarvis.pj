"""Leitura do Protheus: só GET, rotas de lista fechada, teto por turno, mascaramento e as travas."""

import asyncio
import base64
import json
import time

import pytest

from jarvis import agente, cli, ferramentas, protheus
from jarvis.config import Config, ErroConfig, carregar
from jarvis.memoria import Memoria
from jarvis.protheus import MASCARA, ErroProtheus, Protheus, conferir_endereco, ler_rotinas, mascarar, mascarar_texto, resumir
from jarvis.registro import Registro

BASE = "https://royale191154.protheus.cloudtotvs.com.br:10807/rest"
CATALOGO = {"services": [{"path": "/fwmodel", "methods": ["GET", "POST"]}, {"path": "/api/framework/v1/healthcheck", "methods": ["GET"]}]}
MODELO = {
    "id": "MATA020",
    "models": [
        {"id": "SA2MASTER", "type": "FIELDS", "fields": [{"field": "A2_COD", "type": "C", "size": 6}, {"field": "A2_CGC", "type": "C", "size": 14}]},
        {"id": "SA2DETAIL", "type": "GRID", "fields": [{"field": "A2_BANCO", "type": "C", "size": 3}]},
    ],
    "resources": [{"A2_COD": "000001", "A2_NOME": "Fornecedor Teste", "A2_CGC": "12.345.678/0001-95", "A2_EMAIL": "contato@fornecedor.com.br"}],
}


class Rede:
    """O REST de mentira: anota cada GET e responde conforme a configuração."""

    def __init__(self, resposta=(200, MODELO, "application/json"), demora=0.2):
        self.resposta, self.demora = resposta, demora
        self.pedidos: list[tuple[str, dict[str, str], float]] = []
        self.estoura: Exception | None = None

    def __call__(self, url, cabecalhos, espera=60.0):
        self.pedidos.append((url, dict(cabecalhos), espera))
        if self.estoura is not None:
            raise self.estoura
        codigo, corpo, tipo = self.resposta
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo).encode()
        return codigo, corpo, tipo


@pytest.fixture
def teclado(monkeypatch):
    """O que o usuário digita: uma fila que o teste alimenta."""
    fila: asyncio.Queue = asyncio.Queue()

    async def ler(_prompt):
        valor = await fila.get()
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr(cli, "ler_linha", ler)
    return fila.put_nowait


def _protheus(rede=None, relogio=None, **extra):
    """O Protheus com rede falsa. `relogio` é a lista de instantes; sem ela, cada leitura leva 0,2 s."""
    passos = iter(relogio) if relogio else None
    marca = [0.0]

    def agora():
        if passos is not None:
            return next(passos)
        marca[0] += 0.2
        return marca[0] - 0.2 if int(marca[0] * 5) % 2 else marca[0]

    return Protheus(BASE, "royale.leitura", "senha-secreta", pedir_get=rede or Rede(), relogio=agora, **extra)


# --- configuração ---------------------------------------------------------------------------


def test_conferir_endereco():
    assert conferir_endereco(f"{BASE}/") == BASE and conferir_endereco("") == ""
    for ruim in ("http://x/rest", "https:///rest", "royale.com/rest", f"{BASE}?x=1", f"{BASE}#f"):
        with pytest.raises(ValueError, match="JARVIS_PROTHEUS_URL"):
            conferir_endereco(ruim)


def test_ler_rotinas():
    assert ler_rotinas("") == protheus.ROTINAS_PADRAO
    assert ler_rotinas("mata020; MATA010 ,mata020") == ("MATA020", "MATA010")
    with pytest.raises(ValueError, match="não parece o nome de uma rotina"):
        ler_rotinas("MATA020; a rotina de compras")


def test_configuracao(tmp_path):
    base = {"ANTHROPIC_API_KEY": "k", "JARVIS_PASTAS": str(tmp_path)}
    cfg = carregar(base, tmp_path)
    assert cfg.protheus_url == "" and cfg.protheus_tenant == "01,0101" and cfg.protheus_rotinas == protheus.ROTINAS_PADRAO
    cfg = carregar({**base, "JARVIS_PROTHEUS_URL": f"{BASE}/", "JARVIS_PROTHEUS_USER": "u", "JARVIS_PROTHEUS_SENHA": "p", "JARVIS_PROTHEUS_TENANT": "02,0201", "JARVIS_PROTHEUS_ROTINAS": "MATA020"}, tmp_path)
    assert (cfg.protheus_url, cfg.protheus_usuario, cfg.protheus_senha, cfg.protheus_tenant, cfg.protheus_rotinas) == (BASE, "u", "p", "02,0201", ("MATA020",))
    # TOTVS_PRD_* valem como alternativa (é o nome que o Bruno já usa no terminal).
    cfg = carregar({**base, "JARVIS_PROTHEUS_URL": BASE, "TOTVS_PRD_USER": "tu", "TOTVS_PRD_PASS": "tp"}, tmp_path)
    assert (cfg.protheus_usuario, cfg.protheus_senha) == ("tu", "tp")
    with pytest.raises(ErroConfig, match="JARVIS_PROTHEUS_URL"):
        carregar({**base, "JARVIS_PROTHEUS_URL": "http://inseguro/rest"}, tmp_path)
    with pytest.raises(ErroConfig, match="não parece o nome de uma rotina"):
        carregar({**base, "JARVIS_PROTHEUS_ROTINAS": "tudo"}, tmp_path)


def test_sem_credencial_nao_le():
    sem = Protheus(BASE, "", "", pedir_get=Rede())
    assert not sem.pronto and "JARVIS_PROTHEUS_USER" in sem.falta() and "desligada" in sem.situacao()
    with pytest.raises(ErroProtheus, match="JARVIS_PROTHEUS_USER"):
        sem.catalogo()
    sem_url = Protheus("", "u", "p", pedir_get=Rede())
    assert not sem_url.pronto and "JARVIS_PROTHEUS_URL" in sem_url.falta()


# --- as três rotas e o que vai no pedido ------------------------------------------------------


def test_catalogo_modelo_e_saude_montam_a_url_e_os_cabecalhos():
    rede = Rede(resposta=(200, CATALOGO, "application/json"))
    p = _protheus(rede)
    texto = p.catalogo()
    assert rede.pedidos[0][0] == f"{BASE}/" and f"GET {BASE}/" in texto and "HTTP 200" in texto
    cabecalhos = rede.pedidos[0][1]
    assert cabecalhos["Authorization"] == "Basic " + base64.b64encode(b"royale.leitura:senha-secreta").decode()
    assert cabecalhos["tenantId"] == "01,0101" and cabecalhos["Accept"] == "application/json"
    assert rede.pedidos[0][2] == protheus.TEMPO_MAX
    assert "/fwmodel" in texto and "healthcheck" in texto  # o catálogo apareceu
    p.modelo("mata020")
    assert rede.pedidos[1][0] == f"{BASE}/fwmodel/MATA020?pageSize=1&page=1"  # uma página de um registro, por padrão
    p.saude()
    assert rede.pedidos[2][0] == f"{BASE}/api/framework/v1/healthcheck"
    assert "empresa/filial 01,0101" in p.situacao() and "3 leitura(s)" in p.situacao()


def test_a_credencial_nunca_aparece_no_resultado():
    rede = Rede(resposta=(200, {"ok": True, "token": "abc"}, "application/json"))
    p = _protheus(rede)
    texto = p.catalogo() + p.saude() + p.situacao()
    assert "senha-secreta" not in texto and "Basic " not in texto and "Authorization" not in texto
    assert '"token": "***"' in p.saude()  # e token no corpo também sai mascarado


def test_rotina_fora_da_lista_e_recusada():
    p = _protheus(rotinas=("MATA020",))
    with pytest.raises(ErroProtheus, match="MATA010 não está na lista liberada"):
        p.modelo("MATA010")
    with pytest.raises(ErroProtheus, match="Diga a rotina"):
        p.modelo("")
    assert p.chamadas == 0  # recusa não gasta leitura


def test_so_existe_get_no_codigo(monkeypatch):
    """A única função que fala com a rede fixa o método; não há caminho para POST."""
    vistos = {}

    class Resposta:
        status, headers = 200, {"Content-Type": "application/json"}

        def read(self, n=None):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def abrir(requisicao, timeout=None):
        vistos["metodo"] = requisicao.get_method()
        vistos["url"] = requisicao.full_url
        vistos["tenant"] = requisicao.get_header("Tenantid")
        return Resposta()

    monkeypatch.setattr(protheus._ABRIDOR, "open", abrir)
    codigo, corpo, tipo = protheus.pedir(f"{BASE}/", {"tenantId": "01,0101"})
    assert (codigo, corpo) == (200, b"{}") and vistos["metodo"] == "GET" and vistos["tenant"] == "01,0101"
    fonte = (protheus.__file__ and open(protheus.__file__, encoding="utf-8").read()) or ""
    for verbo in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
        assert verbo not in fonte, f"{verbo} não deveria existir em protheus.py"


# --- mascaramento ----------------------------------------------------------------------------


def test_mascarar_campos_e_textos():
    dado = {
        "A2_COD": "000001", "A2_NOME": "Fornecedor Teste", "A2_CGC": "12.345.678/0001-95", "A2_EMAIL": "a@b.com",
        "A2_TEL": "1899999999", "A2_BANCO": "001", "A2_AGENCIA": "1234", "A2_NUMCON": "56789", "senha": "x",
        "vazio": "", "nulo": None, "dentro": [{"A1_CPF": "123.456.789-09", "A1_NOME": "Cliente"}],
    }  # fmt: skip
    limpo = mascarar(dado)
    assert limpo["A2_COD"] == "000001" and limpo["A2_NOME"] == "Fornecedor Teste"  # o que não é pessoal fica
    for campo in ("A2_CGC", "A2_EMAIL", "A2_TEL", "A2_BANCO", "A2_AGENCIA", "A2_NUMCON", "senha"):
        assert limpo[campo] == "***", campo
    assert set(limpo) == set(dado)  # nenhum nome de campo se perdeu: é deles que a integração precisa
    assert limpo["vazio"] == "" and limpo["nulo"] is None  # campo vazio não vira "***"
    assert limpo["dentro"][0] == {"A1_CPF": "***", "A1_NOME": "Cliente"}
    # Documento solto em texto livre, sob um campo de nome inocente, também sai.
    assert mascarar({"obs": "falar com 123.456.789-09 ou x@y.com"})["obs"] == "falar com *** ou ***"
    assert mascarar_texto("CNPJ 12345678000195 e 12.345.678/0001-95") == "CNPJ *** e ***"


def test_o_corpo_do_modelo_sai_mascarado_com_os_campos():
    p = _protheus(Rede(resposta=(200, MODELO, "application/json")))
    texto = p.modelo("MATA020")
    assert "A2_CGC" in texto and "12.345.678/0001-95" not in texto and "contato@fornecedor.com.br" not in texto
    assert "SA2MASTER" in texto and "SA2DETAIL" in texto and "Fornecedor Teste" in texto
    assert "Estrutura:" in texto and "models[0].id: texto = SA2MASTER" in texto


def test_resumir_descreve_sem_presumir_formato():
    linhas = resumir({"id": "X", "models": [{"id": "M", "fields": [{"field": "A2_COD"}]}], "n": 2, "ok": True, "nada": None})
    assert "id: texto = X" in linhas and "models: lista[1]" in linhas and "models[0].id: texto = M" in linhas
    assert "n: número = 2" in linhas and "ok: booleano = True" in linhas and "nada: nulo = None" in linhas
    assert resumir({"a": 1}, profundidade=0) == [] and resumir([], "x") == []


def test_corpo_nao_json_e_cortado():
    enorme = "x" * (protheus.MAX_CORPO + 500)
    p = _protheus(Rede(resposta=(200, f"<html>{enorme}\x07</html>".encode(), "text/html")))
    texto = p.saude()
    assert "Estrutura:" not in texto and "\x07" not in texto and texto.rstrip().endswith(protheus.DE_FORA)
    corpo = texto.split("Corpo:\n", 1)[1]
    assert len(corpo.split("\n")[0]) <= protheus.MAX_CORPO


# --- limites e travas -------------------------------------------------------------------------


def test_teto_de_leituras_por_turno_e_o_zerar():
    rede = Rede()
    p = _protheus(rede)
    for _ in range(protheus.MAX_CHAMADAS):
        p.catalogo()
    assert len(rede.pedidos) == protheus.MAX_CHAMADAS
    with pytest.raises(ErroProtheus, match=f"Já fiz as {protheus.MAX_CHAMADAS} leituras"):
        p.catalogo()
    assert len(rede.pedidos) == protheus.MAX_CHAMADAS  # nada passou do teto
    p.zerar()
    p.catalogo()
    assert len(rede.pedidos) == protheus.MAX_CHAMADAS + 1 and p.total == protheus.MAX_CHAMADAS + 1


def test_erro_5xx_trava_as_leituras_do_turno():
    rede = Rede(resposta=(500, b"Internal Server Error", "text/plain"))
    p = _protheus(rede)
    with pytest.raises(ErroProtheus, match="respondeu 500"):
        p.modelo("MATA020")
    assert p.travado and "Travado neste turno" in p.situacao()
    rede.resposta = (200, MODELO, "application/json")
    with pytest.raises(ErroProtheus, match="estão paradas neste turno"):
        p.catalogo()
    assert len(rede.pedidos) == 1  # não insistiu em produção
    p.zerar()
    assert "Travado" not in p.situacao() and "HTTP 200" in p.catalogo()


def test_resposta_lenta_trava_depois_de_entregar():
    p = _protheus(Rede(), relogio=[0.0, protheus.LENTO + 2, 0.0, 0.0])
    texto = p.saude()
    assert "HTTP 200" in texto and "Parei as leituras deste turno" in texto and f"{protheus.LENTO + 2:.1f} s" in texto
    with pytest.raises(ErroProtheus, match="estão paradas"):
        p.saude()


def test_falha_de_rede_trava_e_explica():
    rede = Rede()
    rede.estoura = TimeoutError("timed out")
    p = _protheus(rede)
    with pytest.raises(ErroProtheus, match="TimeoutError: timed out"):
        p.catalogo()
    assert "a leitura falhou" in p.travado


@pytest.mark.parametrize(
    ("codigo", "trecho"),
    [(401, "SECURITY=1"), (404, "não publica esta rota"), (400, "tenantId")],
    ids=["401 pode ser oauth2", "404", "400"],
)
def test_erros_4xx_viram_explicacao_sem_travar(codigo, trecho):
    p = _protheus(Rede(resposta=(codigo, {"detail": "nope"}, "application/json")))
    texto = p.modelo("MATA020")
    assert f"HTTP {codigo}" in texto and trecho in texto and not p.travado  # 4xx é resposta, não dor do servidor


# --- as ferramentas ---------------------------------------------------------------------------


def _ferramentas(tmp_path, p=None, **extra):
    lista = ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), protheus=p, **extra)
    return {f.name: f for f in lista}


def test_as_ferramentas_so_existem_com_endereco_e_credencial(tmp_path):
    assert not set(_ferramentas(tmp_path)) & set(ferramentas.DE_PROTHEUS)
    assert not set(_ferramentas(tmp_path, Protheus("", "", "", pedir_get=Rede()))) & set(ferramentas.DE_PROTHEUS)
    por_nome = _ferramentas(tmp_path, _protheus())
    assert set(ferramentas.DE_PROTHEUS) <= set(por_nome)
    # Trazem conteúdo de fora, então contaminam o turno; e não agem na máquina.
    assert set(ferramentas.DE_PROTHEUS) <= set(ferramentas.CONTAMINAM)
    assert not set(ferramentas.DE_PROTHEUS) & {*ferramentas.AGEM, *ferramentas.GRAVAM, *ferramentas.SO_EM_TURNO_LIMPO, *ferramentas.NA_PAGINA}
    for nome in ferramentas.DE_PROTHEUS:
        assert por_nome[nome].annotations.read_only_hint is True, nome
    # A lista liberada aparece na descrição, e o esquema só aceita essas rotinas.
    assert "MATA020" in por_nome["protheus_modelo"].description and "só com GET" in por_nome["protheus_catalogo"].description
    assert por_nome["protheus_modelo"].input_schema["properties"]["rotina"]["enum"] == list(protheus.ROTINAS_PADRAO)


def test_as_ferramentas_leem_e_devolvem_os_erros(tmp_path):
    rede = Rede(resposta=(200, CATALOGO, "application/json"))
    p = _protheus(rede, rotinas=("MATA020",))
    por_nome = _ferramentas(tmp_path, p)

    async def cena():
        saida = await por_nome["protheus_catalogo"].handler({})
        assert not saida.get("is_error") and "HTTP 200" in saida["content"][0]["text"]
        assert protheus.DE_FORA in saida["content"][0]["text"]  # o resultado é informação, não ordem
        saida = await por_nome["protheus_saude"].handler({})
        assert not saida.get("is_error") and rede.pedidos[-1][0].endswith("/healthcheck")
        saida = await por_nome["protheus_modelo"].handler({"rotina": "MATA010"})
        assert saida["is_error"] and "não está na lista liberada" in saida["content"][0]["text"]
        saida = await por_nome["protheus_modelo"].handler({})
        assert saida["is_error"] and "Diga a rotina" in saida["content"][0]["text"]

    asyncio.run(cena())
    assert len(rede.pedidos) == 2  # as duas recusas não chegaram à rede


def test_falha_inesperada_na_ferramenta_vira_mensagem(tmp_path, caplog):
    class Quebrado(Protheus):
        def catalogo(self, termos=""):
            raise RuntimeError("defeito")

    p = Quebrado(BASE, "u", "p", pedir_get=Rede())
    saida = asyncio.run(_ferramentas(tmp_path, p)["protheus_catalogo"].handler({}))
    assert saida["is_error"] and "RuntimeError" in saida["content"][0]["text"] and "Falha inesperada ao ler o Protheus" in caplog.text


# --- na sessão e na conversa -------------------------------------------------------------------


def cfg(tmp_path, **extra) -> Config:
    pasta = tmp_path / "Documents"
    pasta.mkdir(exist_ok=True)
    return Config(**{**dict(modelo="sonnet", usuario="Bruno", pastas=(pasta,), orcamento_usd=None), **extra})


def test_na_sessao_as_ferramentas_entram_e_contaminam(tmp_path):
    p = _protheus()
    sessao = agente.montar_sessao(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", protheus=p)
    assert {f"mcp__jarvis__{n}" for n in ferramentas.DE_PROTHEUS} <= set(sessao.opcoes.allowed_tools)
    assert sessao.protheus is p
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__protheus_modelo", {"rotina": "MATA020"}) is None
    # Leu o Protheus: ação direta só na fala seguinte do usuário.
    assert "Ação bloqueada" in (vigia.decidir("mcp__jarvis__abrir_site", {"endereco": "x"}) or "")
    assert "Gravação bloqueada" in (vigia.decidir("mcp__jarvis__memoria_salvar", {}) or "")
    prompt = (tmp_path / ".jarvis" / "prompt-sistema.md").read_text(encoding="utf-8")
    assert "Protheus: você lê o Protheus de produção" in prompt and "Escrever no Protheus você não faz" in prompt
    assert "MATA020" in prompt and f"{protheus.MAX_CHAMADAS} leituras" in prompt
    # Sem o Protheus pronto, nada disso entra no prompt.
    limpa = agente.montar_sessao(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis2")
    assert "Protheus" not in (tmp_path / ".jarvis2" / "prompt-sistema.md").read_text(encoding="utf-8")
    assert limpa.protheus is None


def test_cada_fala_do_usuario_zera_o_teto(tmp_path, monkeypatch, teclado, capsys):
    from test_ativacao import Cliente, SessaoFalsa, ate

    from test_ativacao import cfg as cfg_da_conversa

    p = _protheus()
    for _ in range(protheus.MAX_CHAMADAS):
        p.catalogo()
    assert p.chamadas == protheus.MAX_CHAMADAS

    async def cena():
        cliente = Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        sessao = SessaoFalsa(protheus=p)
        laco = cli._laco_da_conversa(cfg_da_conversa(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), None, sessao, None, cli.MUDO)
        conversa = asyncio.ensure_future(laco)
        teclado("que horas são")
        await ate(lambda: cliente.perguntas == ["que horas são"])
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    assert p.chamadas == 0 and not p.travado  # a fala do usuário devolveu as leituras


def test_comando_protheus(tmp_path, monkeypatch, teclado, capsys):
    from test_ativacao import Cliente, SessaoFalsa

    from test_ativacao import cfg as cfg_da_conversa

    async def cena(configurado, p):
        monkeypatch.setattr(cli, "ClaudeSDKClient", Cliente())
        config = cfg_da_conversa(tmp_path, protheus_url=BASE if configurado else "")
        laco = cli._laco_da_conversa(config, Memoria(tmp_path / "m"), Registro(":memory:"), None, SessaoFalsa(protheus=p), None, cli.MUDO)
        conversa = asyncio.ensure_future(laco)
        teclado("/protheus")
        teclado("sair")
        await asyncio.wait_for(conversa, 5)

    asyncio.run(cena(False, None))
    assert "leitura do Protheus está desligada" in capsys.readouterr().out
    p = _protheus(Rede(resposta=(200, {"status": "OK"}, "application/json")))
    asyncio.run(cena(True, p))
    saida = capsys.readouterr().out
    assert "Leitura do Protheus (só GET, sem seguir desvio)" in saida and "Testando o healthcheck" in saida and "HTTP 200" in saida
    assert p.chamadas == 0  # o teste não consome a cota da próxima fala
    travado = _protheus(Rede(resposta=(500, b"erro", "text/plain")))
    asyncio.run(cena(True, travado))
    assert "respondeu 500" in capsys.readouterr().out


# --- o que a auditoria achou: cada furo com o seu teste -------------------------------------------


def test_desvio_nao_e_seguido_e_a_credencial_nao_sai_do_host_configurado():
    """Seguir um 3xx mandaria usuário e senha para onde o servidor quisesse, em claro.

    Dois servidores de verdade: o primeiro responde 302 para o segundo. O segundo não pode receber
    nada. (http, não https, só porque é local: quem valida o esquema é `conferir_endereco`.)
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    recebido = []

    class Desvia(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8942/exfil")
            self.end_headers()

    class Pega(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            recebido.append((self.headers.get("Authorization"), self.path))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    servidores = [ThreadingHTTPServer(("127.0.0.1", porta), h) for porta, h in ((8941, Desvia), (8942, Pega))]
    for s in servidores:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        p = Protheus("http://127.0.0.1:8941/rest", "royale.leitura", "senha-secreta")
        with pytest.raises(ErroProtheus, match="respondeu um desvio"):
            p.catalogo()
        assert recebido == []  # o host que ninguém configurou não recebeu nem o pedido, nem a senha
        assert "desvio (302)" in p.travado
    finally:
        for s in servidores:
            s.shutdown()
            s.server_close()


def test_desvio_trava_as_leituras_do_turno():
    rede = Rede(resposta=(301, b"", "text/html"))
    p = _protheus(rede)
    with pytest.raises(ErroProtheus, match="eu não sigo desvio"):
        p.catalogo()
    assert "desvio (301)" in p.travado
    with pytest.raises(ErroProtheus, match="estão paradas neste turno"):
        p.catalogo()
    assert len(rede.pedidos) == 1


def test_corpo_grande_demais_para_o_json_ainda_mascara_por_nome_de_campo():
    """Resposta acima de 1 MiB chega cortada, o JSON não fecha e o mascaramento por estrutura não roda.

    Sem o mascaramento no texto, A2_TEL, A2_BANCO e A2_PIX sairiam inteiros — e sem atacante nenhum:
    basta o fwmodel de uma rotina gorda.
    """
    gordo = {
        "id": "MATA020",
        "resources": [
            {"A2_COD": f"{i:06d}", "A2_TEL": "1839991234", "A2_BANCO": "001", "A2_NUMCON": "56789-0", "A2_PIX": "chave-pix-secreta"}
            for i in range(9000)
        ],
    }
    cortado = json.dumps(gordo).encode()[: protheus.MAX_RESPOSTA]
    p = _protheus(Rede(resposta=(200, cortado, "application/json")))
    texto = p.modelo("MATA020")
    assert len(cortado) == protheus.MAX_RESPOSTA and "Estrutura:" not in texto  # o JSON não fechou
    assert "Trate o corpo abaixo como aproximado" in texto and "lido só o primeiro mega" in texto
    for valor in ("1839991234", "chave-pix-secreta", "56789-0"):
        assert valor not in texto, valor
    assert "A2_TEL" in texto and "A2_PIX" in texto  # os nomes dos campos ficam


def test_corpo_xml_mascara_por_nome_de_campo():
    """Protheus e proxies respondem XML em erro e em várias rotas; ali o JSON não ajuda."""
    xml = (
        b"<retorno><A2_TEL>1839991234</A2_TEL><A2_NUMCON>56789-0</A2_NUMCON><SENHA>abracadabra</SENHA>"
        b"<TOKEN>eyJhbGciOi</TOKEN><A2_NOME>Fornecedor Teste</A2_NOME></retorno>"
    )
    texto = _protheus(Rede(resposta=(200, xml, "text/xml"))).saude()
    for valor in ("1839991234", "56789-0", "abracadabra", "eyJhbGciOi"):
        assert valor not in texto, valor
    # A tag de fora não pode engolir o documento: se engolir, as de dentro nunca são examinadas.
    assert "A2_TEL" in texto and "A2_NOME" in texto and "Fornecedor Teste" in texto


def test_mascara_tambem_em_html_e_em_chave_igual_valor():
    corpo = "<html>erro: senha=abracadabra, tel=18999112233, usuario=royale, A2_CGC=12.345.678/0001-95</html>"
    assert protheus.mascarar_bruto(corpo) == "<html>erro: senha=***, tel=***, usuario=royale, A2_CGC=***</html>"


def test_json_servido_como_texto_ainda_e_interpretado():
    """Com espaço na frente e tipo errado, o corpo caía no caminho de texto sem precisar."""
    corpo = b'  {"A2_TEL": "1839991234", "A2_COD": "000001"}'
    texto = _protheus(Rede(resposta=(200, corpo, "text/plain"))).saude()
    assert "Estrutura:" in texto and "1839991234" not in texto and "000001" in texto


def test_chave_do_objeto_tambem_e_mascarada():
    """Resposta indexada por documento (mapa CNPJ → nome) entregaria a lista de documentos pela chave."""
    texto = _protheus(Rede(resposta=(200, {"12.345.678/0001-95": "Fornecedor", "123.456.789-09": "Cliente"}, "application/json"))).saude()
    assert "12.345.678/0001-95" not in texto and "123.456.789-09" not in texto
    # Mascarar a chave não pode sumir com um registro: as repetidas são numeradas.
    assert "Fornecedor" in texto and "Cliente" in texto
    assert mascarar({"12.345.678/0001-95": "x", "123.456.789-09": "y"}) == {"***": "x", "***(2)": "y"}


def test_aninhamento_fundo_demais_nao_derruba_nem_vaza():
    """Corpo fundo demais pode derrubar o leitor de JSON; a leitura não pode morrer com ele.

    O corpo é montado em bytes de propósito: `json.dumps` de 3000 níveis já estoura a pilha do
    Python do Windows antes de a leitura começar.
    """
    corpo = b'{"a":' * 3000 + b'{"A2_CGC":"12.345.678/0001-95","A2_TEL":"1839991234"}' + b"}" * 3000
    texto = _protheus(Rede(resposta=(200, corpo, "application/json"))).catalogo()
    assert "HTTP 200" in texto and "12.345.678/0001-95" not in texto and "1839991234" not in texto
    assert mascarar({"a": {"b": {"c": 1}}}, fundo=2) == {"a": {"b": protheus.MASCARA}}


def test_valor_falso_ou_zero_sob_campo_sensivel_tambem_e_mascarado():
    assert mascarar({"SENHA": False, "TOKEN": 0, "A2_NUMCON": 0.0}) == {"SENHA": "***", "TOKEN": "***", "A2_NUMCON": "***"}
    assert mascarar({"A2_CGC": "", "A2_TEL": None}) == {"A2_CGC": "", "A2_TEL": None}  # vazio não vira "***"


def test_campos_reais_do_dicionario_e_o_que_nao_deve_ser_mascarado():
    limpo = mascarar({"A1_BCO1": "001", "A2_ENDCOB": "Rua X, 10", "A2_END": "Rua Y", "obs": "fone 18 99123-4567"})
    assert limpo == {"A1_BCO1": "***", "A2_ENDCOB": "***", "A2_END": "***", "obs": "fone ***"}
    # E o que só *contém* o pedaço no meio de outra palavra continua visível: é estrutura, não dado pessoal.
    assert mascarar({"A2_VENDEDOR": "José", "PENDENTE": "sim", "CLIENTELA": "A"}) == {"A2_VENDEDOR": "José", "PENDENTE": "sim", "CLIENTELA": "A"}


def test_cabecalho_de_autenticacao_ecoado_no_corpo_sai_mascarado():
    """Gateways e handlers de erro do Protheus às vezes devolvem os cabeçalhos recebidos."""
    corpo = {"error": "unauthorized", "requestHeaders": {"Authorization": "Basic dTpzM25oNA==", "tenantId": "01,0101"}}
    texto = _protheus(Rede(resposta=(401, corpo, "application/json"))).saude()
    assert "Basic dTpzM25oNA==" not in texto and "SECURITY=1" in texto and "tenantId" in texto


def test_rotina_torta_e_recusada_no_construtor():
    """A lista fechada é garantia deste arquivo, não de quem lê o .env."""
    with pytest.raises(ValueError, match="não parece o nome de uma rotina"):
        Protheus(BASE, "u", "p", rotinas=("../../api/oauth2/v1/token",), pedir_get=Rede())
    with pytest.raises(ValueError, match="não parece o nome de uma rotina"):
        Protheus(BASE, "u", "p", rotinas=("MATA020/../x",), pedir_get=Rede())
    assert Protheus(BASE, "u", "p", rotinas=("mata020",), pedir_get=Rede()).rotinas == ("MATA020",)


def test_endereco_com_credencial_ou_caminho_torto_e_recusado():
    for ruim, trecho in (
        ("https://u:p@royale.protheus.com/rest", "não leva usuário nem senha"),
        ("https://royale.protheus.com/rest/../../etc", "não pode ter '..'"),
        ("https://royale.protheus.com/rest?", "sem parâmetros"),
        ("https://royale.protheus.com/rest#", "sem parâmetros"),
    ):
        with pytest.raises(ValueError, match=trecho):
            conferir_endereco(ruim)


def test_teto_do_dia_e_a_virada_do_dia():
    """Uma rotina que dispara de hora em hora ganharia 15 leituras a cada disparo, sem ninguém falar."""
    from datetime import date

    dia = [date(2026, 10, 22)]
    rede = Rede()
    p = _protheus(rede, hoje=lambda: dia[0])
    for _ in range(protheus.MAX_POR_DIA):
        p.zerar()  # cada "fala" devolve o teto do turno, mas não o do dia
        p.catalogo()
    p.zerar()
    with pytest.raises(ErroProtheus, match=f"{protheus.MAX_POR_DIA} leituras do Protheus hoje"):
        p.catalogo()
    assert len(rede.pedidos) == protheus.MAX_POR_DIA and f"{protheus.MAX_POR_DIA} de {protheus.MAX_POR_DIA} hoje" in p.situacao()
    dia[0] = date(2026, 10, 23)
    p.zerar()
    assert "HTTP 200" in p.catalogo() and p.no_dia == 1


def test_a_senha_do_protheus_nao_chega_ao_shell_de_ninguem():
    """O `.env` do Jarvis vai para o os.environ; de lá ele não pode escorrer para um shell."""
    from jarvis import trabalhos
    from jarvis.terminal import ambiente_do_comando

    ambiente = {
        "JARVIS_PROTHEUS_SENHA": "senha-de-producao", "TOTVS_PRD_PASS": "outra", "JARVIS_PROTHEUS_USER": "u",
        "JARVIS_ELEVENLABS_KEY": "e", "ANTHROPIC_API_KEY": "a", "PATH": "/bin", "GITHUB_TOKEN": "g",
    }  # fmt: skip
    # O trabalhador roda Bash sem o "s" do usuário: o corte é largo.
    apagadas = trabalhos.segredos_do_ambiente(ambiente)
    for nome in ("JARVIS_PROTHEUS_SENHA", "TOTVS_PRD_PASS", "JARVIS_ELEVENLABS_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        assert nome in apagadas, nome
    # No terminal o usuário aprova o comando, não o ambiente: as credenciais do Jarvis ficam fora.
    limpo = ambiente_do_comando(ambiente)
    assert "senha-de-producao" not in limpo.values() and "outra" not in limpo.values()
    assert not [n for n in limpo if n.upper().startswith(("JARVIS_", "TOTVS_PRD_", "ANTHROPIC"))]
    assert limpo["GITHUB_TOKEN"] == "g" and limpo["PATH"] == "/bin"  # o ambiente dele continua de pé


# ---------------------------------------------------------------- segunda revisão


def _curto(corpo: bytes, tipo: str) -> str:
    return Protheus(endereco=BASE, usuario="u", senha="s")._corpo_curto(corpo, tipo)


def test_corpo_de_um_mega_nao_pendura_o_turno():
    """N3: o mascaramento custa por letra. Antes do corte, um mega de texto sem pontuação levava horas."""
    corpo = (b'{"hash":"' + b"a1b2c3d4e5f6" * 80000 + b'","A2_CGC":"12.345.678/0001-95"}')
    comeco = time.monotonic()
    texto = _curto(corpo, "text/plain")
    gasto = time.monotonic() - comeco
    assert gasto < 3.0, f"mascarar 1 MiB levou {gasto:.1f}s"
    assert "12.345.678/0001-95" not in texto


def test_nome_de_campo_sensivel_e_por_pedaco_nao_por_trecho():
    """N2: `endpoint`, `D3_DOC` e `C1_CONTA` são o dado que a ferramenta existe para trazer."""
    for passa in ("endpoint", "endPoint", "D3_DOC", "F1_DOC", "C1_CONTA", "CT1_CONTA", "contabil",
                  "contador", "chave_primaria", "documentacao", "TELA", "passo", "CELULA", "cepa"):
        assert not protheus.campo_sensivel(passa), passa
    for esconde in ("A2_CGC", "A2_EMAIL", "A2_TEL", "A2_TEL2", "A1_BCO1", "A2_NUMCON", "A2_PIX",
                    "SENHA", "TOKEN", "X-Api-Key", "Set-Cookie", "A2_END", "A2_ENDCOB", "numero_da_conta"):
        assert protheus.campo_sensivel(esconde), esconde
    catalogo = mascarar({"services": [{"endpoint": "/fwmodel", "A2_CGC": "1"}]})
    assert catalogo["services"][0]["endpoint"] == "/fwmodel" and catalogo["services"][0]["A2_CGC"] == MASCARA


def test_chave_repetida_nunca_apaga_o_registro_anterior():
    """N1: um corpo com a chave literal `***` fazia o campo mascarado antes desaparecer em silêncio."""
    limpo = mascarar({"***": "primeiro", "A2_CGC": "12.345.678/0001-95"})
    assert len(limpo) == 2 and "primeiro" in limpo.values()


def test_numero_neutro_de_dez_digitos_nao_virar_telefone():
    """N4: sem separador, `size: 1234567890` saía como *** e o corpo deixava de ser JSON válido."""
    texto = _curto(b'{"size":1899123456,"recno":4733221100}', "application/json")
    assert "1899123456" in texto and MASCARA not in texto
    assert MASCARA in _curto(b'{"obs":"ligar 18 99123-4567"}', "application/json")


def test_esquema_de_arquivo_e_recusado_no_construtor_e_no_abridor():
    """N5: `file:///etc/passwd` no .env abria arquivo da máquina em vez de falhar."""
    with pytest.raises(ValueError, match="http ou https"):
        Protheus(endereco="file:///etc/passwd", usuario="u", senha="s")
    assert "file" not in protheus._ABRIDOR.handle_open and "ftp" not in protheus._ABRIDOR.handle_open


def test_valor_sensivel_em_cdata_e_em_atributo_nao_passa():
    """F1/F2: SOAP esconde valor em CDATA e em atributo, e os dois escapavam das expressões."""
    for corpo in (b'<r><A2_CGC><![CDATA[12.345.678/0001-95]]></A2_CGC></r>',
                  b'<campo nome="A2_NUMCON" valor="11111-1"/>',
                  b'{"A2_NUMCON":["56789-0"]}'):
        texto = _curto(corpo, "text/xml")
        assert "0001-95" not in texto and "11111-1" not in texto and "56789-0" not in texto, corpo


def test_cota_do_dia_sobrevive_a_reinicio(tmp_path):
    """F13: sem gravar, bastava fechar e abrir o Jarvis para zerar o teto do dia."""
    arquivo = tmp_path / "protheus.json"
    relogio = iter(range(1, 10000))
    def um(url, cabecalhos, espera=60.0):
        return 200, b"{}", "application/json"
    primeiro = Protheus(endereco=BASE, usuario="u", senha="s", pedir_get=um,
                        relogio=lambda: next(relogio) / 10, arquivo=arquivo)
    for _ in range(4):
        primeiro.catalogo()
    assert primeiro.no_dia == 4
    segundo = Protheus(endereco=BASE, usuario="u", senha="s", pedir_get=um,
                       relogio=lambda: next(relogio) / 10, arquivo=arquivo)
    assert segundo.no_dia == 4, "a cota do dia voltou a zero depois do reinício"


def test_turno_automatico_leva_cota_menor():
    """F13: cada rotina ganhava as 15 leituras inteiras e levava o teto do dia sozinha."""
    relogio = iter(range(1, 10000))
    alvo = Protheus(endereco=BASE, usuario="u", senha="s",
                    pedir_get=lambda u, c, espera=60.0: (200, b"{}", "application/json"),
                    relogio=lambda: next(relogio) / 10)
    alvo.zerar(cli.LEITURAS_DA_ROTINA)
    for _ in range(cli.LEITURAS_DA_ROTINA):
        alvo.catalogo()
    with pytest.raises(ErroProtheus, match=f"as {cli.LEITURAS_DA_ROTINA} leituras"):
        alvo.catalogo()
    alvo.zerar()  # o usuário falou: volta a cota cheia
    alvo.catalogo()


def test_caminho_com_escape_percentual_e_recusado():
    """F12: `%2e%2e` é `..` depois que o servidor decodifica."""
    with pytest.raises(ValueError, match=r"\.\."):
        conferir_endereco("https://royale.protheus.com.br:10807/rest/%2e%2e/%2e%2e/admin")


# --- paginação, filtro e busca no catálogo ------------------------------------------------


def test_pagina_e_tamanho_de_pagina_sao_numeros_conferidos():
    rede = Rede()
    p = _protheus(rede)
    p.modelo("MATA020", por_pagina=50, pagina=3)
    assert rede.pedidos[0][0] == f"{BASE}/fwmodel/MATA020?pageSize=50&page=3"
    for ruim in ("0", "51", "-1", "abc", "", "1 or 1=1", "2;DROP TABLE", "1.5", None, 1e9):
        with pytest.raises(ErroProtheus, match="pageSize"):
            p.modelo("MATA020", por_pagina=ruim)
    with pytest.raises(ErroProtheus, match="page tem de estar entre"):
        p.modelo("MATA020", pagina=protheus.PAGINAS_MAXIMAS + 1)
    assert len(rede.pedidos) == 1, "nada ruim chegou à rede"


def test_filtro_so_em_campo_liberado_e_so_por_igualdade():
    rede = Rede()
    p = _protheus(rede)
    p.modelo("MATA020", campo="a2_cgc", valor="12345678000195")
    assert rede.pedidos[0][0].endswith("FILTER=A2_CGC+eq+%2712345678000195%27"), rede.pedidos[0][0]
    for campo, valor in (("A2_NOME", "x"), ("A2_CGC", "' or '1'='1"), ("A2_CGC", "1 or 1=1"),
                         ("A2_CGC", "x';DROP--"), ("A2_CGC", ""), ("A2_COD", "a" * 41),
                         ("A2_CGC", "12.345.678/0001-95"), ("", "123")):
        with pytest.raises(ErroProtheus):
            p.modelo("MATA020", campo=campo, valor=valor)
    with pytest.raises(ErroProtheus, match="não tem filtro liberado"):
        p.modelo("MATA010", campo="B1_COD", valor="123")
    assert len(rede.pedidos) == 1, "nenhum filtro torto chegou à rede"


def test_busca_no_catalogo_devolve_rotas_e_nao_o_corpo():
    catalogo = {"services": [{"path": "/fwmodel/MATA020"}, {"path": "/api/framework/v1/healthcheck"},
                             {"path": "/wsRestFluxo/pedido"}, {"path": "/api/ecm/v1/doc"}]}
    p = _protheus(Rede(resposta=(200, catalogo, "application/json")))
    texto = p.catalogo("fwmodel, fluxo")
    assert "/fwmodel/MATA020" in texto and "/wsRestFluxo/pedido" in texto
    assert "/api/ecm/v1/doc" not in texto and "healthcheck" not in texto, "só o que foi pedido"
    assert "Corpo:" not in texto and '"services"' not in texto, "o corpo não vai para a conversa"
    assert "4 rota(s) no catálogo" in texto and "bytes lidos" in texto
    vazio = _protheus(Rede(resposta=(200, catalogo, "application/json"))).catalogo("naoexiste")
    assert "Nenhuma rota com esses termos" in vazio


def test_a_rota_partida_entre_dois_pedacos_nao_se_perde():
    """O catálogo chega aos pedaços: sem costura, a rota na emenda sumiria."""
    corpo = json.dumps({"services": [{"path": "/wsRestFluxo/pedido"}]}).encode()
    peneira = protheus._Peneira(["fluxo"])
    for i in range(0, len(corpo), 3):  # pedaços de três bytes, a pior emenda possível
        peneira(corpo[i : i + 3])
    assert peneira.achadas == ["/wsRestFluxo/pedido"]


def test_credencial_ecoada_no_catalogo_nunca_vira_rota():
    """Base64 pode ter `/` dentro: sem a trava, o Authorization ecoado sairia como se fosse rota."""
    credencial = base64.b64encode(b"royale.leitura:a/b+c=").decode()
    corpo = json.dumps({"erro": "unauthorized", "recebido": {"Authorization": f"Basic {credencial}"},
                        "services": [{"path": "/fwmodel"}]}).encode()
    p = Protheus(BASE, "royale.leitura", "a/b+c=", pedir_get=lambda u, c, e=60.0: (401, corpo, "application/json"))
    texto = p.catalogo()
    assert credencial not in texto and "a/b+c=" not in texto and credencial.split("/")[0] not in texto
    assert "SECURITY=1" in texto


def test_o_fluxo_para_no_teto_de_verdade():
    """Contra um servidor de verdade: o teto corta a leitura no meio da resposta."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    corpo = b'{"services":[' + b'{"path":"/api/x/y"},' * 60000 + b"]}"

    class Serve(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            try:
                self.wfile.write(corpo)
            except OSError:
                pass  # o cliente fechou no teto: é exatamente o que se está provando

        def log_message(self, *_a):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 8943), Serve)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    endereco = "http://127.0.0.1:8943/rest/"
    try:
        pedacos = []
        codigo, lidos, _tipo = protheus.pedir_em_fluxo(endereco, {}, pedacos.append, espera=20.0, teto=4096)
        assert codigo == 200 and lidos == 4096, f"leu {lidos} bytes de {len(corpo)}"
        assert sum(len(p) for p in pedacos) == 4096
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_o_catalogo_avisa_quando_parou_no_teto():
    p = _protheus(Rede())
    p._fluxo = lambda url, cab, ao_pedaco, espera=60.0, teto=0: (ao_pedaco(b'{"path":"/api/x"}'), (200, protheus.TETO_DO_CATALOGO, "application/json"))[1]
    assert "parei de ler" in p.catalogo("api").lower()


def test_busca_no_catalogo_gasta_uma_leitura_e_obedece_as_travas():
    rede = Rede()
    p = _protheus(rede)
    p.zerar(2)
    p.catalogo("x")
    p.catalogo("y")
    with pytest.raises(ErroProtheus, match="as 2 leituras"):
        p.catalogo("z")
    with pytest.raises(ErroProtheus, match="no máximo"):
        p.catalogo("a b c d e f")
