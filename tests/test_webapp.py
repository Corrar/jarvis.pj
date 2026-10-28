"""A janela própria do Jarvis no Protheus WebApp: medir a tela (C-0) e gravar a calibração.

Os testes que abrem navegador rodam contra um Protheus WebApp de mentira servido aqui mesmo, com
shadow DOM, caixa de aviso e a variante desenhada em `canvas`. Sem navegador na máquina, pulam.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jarvis import webapp
from jarvis.webapp import Calibracao, ErroWebApp, Medida, Navegador, Passo, Roteiro, conferir_endereco, mesma_origem

SENHA = "segredo-que-nao-pode-sair"

ENTRADA = """<!doctype html><html lang=pt-BR><title>Protheus WebApp - Entrada</title><body>
<h1>TOTVS Protheus — royale191155 (homologacao)</h1>
<label for=amb>Ambiente</label><input id=amb value="HOMOLOG">
<label for=usr>Usuario</label><input id=usr>
<label for=sen>Senha</label><input id=sen type=password>
<button id=entrar onclick="location.href='/mata270'">Entrar</button></body></html>"""

MATA270 = """<!doctype html><html lang=pt-BR><title>MATA270 - Digitacao de Inventario</title><body>
<div id=barra>Empresa 01 / Filial 0101 — royale191155</div>
<button id=incluir onclick="document.getElementById('form').hidden=false">Incluir</button>
<div id=form hidden>
  <campo-protheus rotulo="Documento" nome="B7_DOC"></campo-protheus>
  <campo-protheus rotulo="Produto" nome="B7_COD"></campo-protheus>
  <label for=loc>Armazem</label><input id=loc name=B7_LOCAL>
  <label for=qt>Quantidade</label><input id=qt name=B7_QUANT>
  <button id=confirmar onclick="avisar()">Confirmar</button>
  <button id=fora onclick="location.href=window.__fora">Ir para outro lugar</button>
</div>
<script>
window.__fora = 'http://127.0.0.1:8957/';
class CampoProtheus extends HTMLElement {
  connectedCallback() {
    const s = this.attachShadow({mode:'open'});
    s.innerHTML = `<label for=i>${this.getAttribute('rotulo')}</label><input id=i name="${this.getAttribute('nome')}">`;
  }
}
customElements.define('campo-protheus', CampoProtheus);
function avisar() {
  const d = document.createElement('div');
  d.setAttribute('role','alertdialog');
  d.textContent = 'Produto nao cadastrado no armazem informado.';
  document.body.appendChild(d);
}
</script></body></html>"""

DESENHADA = """<!doctype html><html lang=pt-BR><title>Protheus WebApp</title><body style="margin:0">
<canvas id=tela width=1200 height=800 style="width:100vw;height:100vh"></canvas></body></html>"""


def _chromium():
    """O navegador que existe nesta máquina, ou None."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    for caminho in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"):
        return str(caminho)
    try:  # o caminho normal: o `playwright install` pôs no lugar certo
        with sync_playwright() as pw:
            return pw.chromium.executable_path if Path(pw.chromium.executable_path).exists() else None
    except Exception:  # noqa: BLE001
        return None


CHROMIUM = _chromium()
sem_navegador = pytest.mark.skipif(not CHROMIUM, reason="não há navegador para automação nesta máquina")


@pytest.fixture
def webapp_falso():
    """Sobe o Protheus de mentira e devolve o endereço."""
    servidores = []

    class Serve(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            corpo = {"/": ENTRADA, "/mata270": MATA270, "/canvas": DESENHADA}.get(self.path.split("?")[0], ENTRADA)
            dados = corpo.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *_a):
            pass

    def subir(porta=8953):
        servidor = ThreadingHTTPServer(("127.0.0.1", porta), Serve)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        servidores.append(servidor)
        return f"http://127.0.0.1:{porta}"

    yield subir
    for servidor in servidores:
        servidor.shutdown()
        servidor.server_close()


# O processo auxiliar do Playwright escreve no descritor de saída; com a captura do pytest ligada,
# ele enche o cano e trava. `capfd.disabled()` devolve a saída ao terminal só durante a cena.
def _rodar(cena, capfd):
    with capfd.disabled():
        asyncio.run(cena())


def _sessao(endereco, tmp_path, caminho=""):
    navegador = Navegador(endereco + caminho, tmp_path / "perfil", visivel=False, executavel=CHROMIUM)
    return Calibracao(navegador, tmp_path / "roteiros")


# --- sem navegador ---------------------------------------------------------------------------


def test_endereco_conferido():
    assert conferir_endereco(" https://protheus.royale.com.br:8080/webapp/ ") == "https://protheus.royale.com.br:8080/webapp/"
    for ruim in ("file:///etc/passwd", "ftp://x/y", "só um texto"):
        with pytest.raises(ValueError, match="http"):
            conferir_endereco(ruim)
    with pytest.raises(ValueError, match="não leva usuário nem senha"):
        conferir_endereco("https://user:senha@protheus.royale.com.br")


def test_mesma_origem_compara_esquema_host_e_porta():
    assert mesma_origem("https://a.com:8080/x", "https://a.com:8080/y/z")
    for outro in ("https://a.com:9090/x", "http://a.com:8080/x", "https://b.com:8080/x"):
        assert not mesma_origem("https://a.com:8080/x", outro), outro


def test_sem_endereco_configurado_recusa(tmp_path):
    with pytest.raises(ErroWebApp, match="JARVIS_PROTHEUS_WEBAPP"):
        Navegador("", tmp_path)


def test_o_veredito_do_c0_manda_parar_quando_a_tela_e_desenho():
    desenho = Medida(elementos=3, campos=0, botoes=0, canvas=1, area_do_canvas=0.98)
    assert desenho.desenhada_em_canvas and "PARE AQUI" in desenho.veredito()
    pagina = Medida(elementos=120, campos=5, botoes=3, com_shadow=2)
    assert not pagina.desenhada_em_canvas and "Dá para ancorar por elemento" in pagina.veredito()
    assert "shadow DOM" in pagina.veredito()
    vazia = Medida(elementos=10)
    assert "Não achei campo nenhum" in vazia.veredito()


def test_o_roteiro_sai_em_yaml_com_o_que_ficou_por_responder():
    roteiro = Roteiro(endereco="https://x/webapp")
    roteiro.anotar([
        Passo(acao="digitar", rotulo="Produto", valor="ABC-1", tipo="text", ordem=2),
        Passo(acao="dialogo", texto="Produto nao cadastrado."),
        Passo(acao="clicar", rotulo="Confirmar", tipo="button"),
    ])
    texto = roteiro.como_yaml()
    assert 'rotulo: "Produto"' in texto and "- \"Produto\"" in texto
    assert "Produto nao cadastrado." in texto and "classificacao: a_decidir" in texto
    for pendente in ("um_item_por_inclusao", "b7_cod_aceita_o_sku_do_fluxo", "mensagem_de_duplicidade"):
        assert pendente in texto
    assert "senha não está aqui" in texto, "o arquivo avisa que senha não entra nele"


def test_aspas_no_rotulo_nao_quebram_o_yaml():
    texto = Passo(acao="clicar", rotulo='Incluir "novo"', tipo="button").como_yaml()
    assert '\\"novo\\"' in texto


# --- com navegador ----------------------------------------------------------------------------


@sem_navegador
def test_mede_a_tela_e_grava_o_caminho_inteiro(tmp_path, webapp_falso, capfd):
    """A sessão de verdade: medir, gravar login e MATA270, e salvar o roteiro."""

    async def cena():
        endereco = webapp_falso(8953)
        sessao = _sessao(endereco, tmp_path)
        medida = await sessao.comecar()
        assert medida.campos >= 3 and medida.canvas == 0 and not medida.desenhada_em_canvas

        pagina = sessao.navegador.pagina
        await pagina.fill("#usr", "jarvis")
        await pagina.fill("#sen", SENHA)
        await pagina.click("#entrar")
        await pagina.wait_for_load_state()
        await sessao.colher()
        await pagina.click("#incluir")
        campos = pagina.locator("campo-protheus")
        await campos.nth(0).locator("input").fill("FLX261023")  # dentro de shadow DOM
        await campos.nth(1).locator("input").fill("ABC-1")  # idem
        await pagina.fill("#loc", "02")
        await pagina.fill("#qt", "12,5")
        await pagina.click("#confirmar")
        await pagina.wait_for_timeout(300)
        arquivo = await sessao.terminar()

        texto = arquivo.read_text(encoding="utf-8")
        assert arquivo.name == "mata270.yaml"
        assert SENHA not in texto, "a senha do Protheus não entra no roteiro"
        assert '"***"' in texto, "o campo de senha entra como campo, só o conteúdo não"
        # Os dois primeiros estão dentro de shadow DOM: `change` não atravessa, só `input` atravessa.
        # A tela de entrada também entra: os passos ficam no sessionStorage e atravessam a
        # navegação para a MATA270. Antes disso, tudo o que o Bruno fizesse no login se perdia.
        assert sessao.roteiro.campos == ["Usuario", "Senha", "Documento", "Produto", "Armazem", "Quantidade"]
        assert "FLX261023" in texto and "ABC-1" in texto
        assert "Produto nao cadastrado no armazem informado." in sessao.roteiro.mensagens



    _rodar(cena, capfd)


@sem_navegador
def test_tela_em_canvas_para_a_sessao(tmp_path, webapp_falso, capfd):
    """C-0: sem elemento legível não há o que ancorar, e seguir seria fingir que dá."""

    async def cena():
        sessao = _sessao(webapp_falso(8954), tmp_path, "/canvas")
        with pytest.raises(ErroWebApp, match="PARE AQUI"):
            await sessao.comecar()
        assert sessao.navegador.pagina is None, "a janela é fechada junto"



    _rodar(cena, capfd)


@sem_navegador
def test_a_janela_nao_vai_para_outro_endereco(tmp_path, webapp_falso, capfd):
    """Esta janela tem a sessão do ERP aberta: um desvio para outro host para a sessão."""

    async def cena():
        endereco = webapp_falso(8955)
        webapp_falso(8957)  # o "outro lugar": outro servidor de verdade, em outra porta
        sessao = _sessao(endereco, tmp_path)
        await sessao.comecar()
        pagina = sessao.navegador.pagina
        await pagina.click("#entrar")
        await pagina.wait_for_load_state()
        await sessao.colher()
        await pagina.click("#incluir")
        try:
            await pagina.click("#fora")  # tenta ir para 127.0.0.1:8999, que é outra origem
            await pagina.wait_for_timeout(600)
        except Exception:  # noqa: BLE001 - a janela some debaixo do clique, que é o esperado
            pass
        assert sessao.navegador.desvio.startswith("http://127.0.0.1:8957"), sessao.navegador.desvio
        with pytest.raises(ErroWebApp, match="tentou ir para"):
            await sessao.navegador.medir()
        await sessao.navegador.fechar()



    _rodar(cena, capfd)


@sem_navegador
def test_fechar_duas_vezes_nao_estoura(tmp_path, webapp_falso, capfd):

    async def cena():
        sessao = _sessao(webapp_falso(8956), tmp_path)
        await sessao.comecar()
        await sessao.navegador.fechar()
        await sessao.navegador.fechar()


    _rodar(cena, capfd)



# --- ligado no Jarvis -------------------------------------------------------------------------


def test_o_endereco_do_webapp_entra_no_conferidor_do_env():
    from jarvis.config import CONHECIDOS, ErroConfig, carregar

    assert "JARVIS_PROTHEUS_WEBAPP" in CONHECIDOS
    assert carregar({"ANTHROPIC_API_KEY": "x"}).protheus_webapp == ""
    with pytest.raises(ErroConfig, match="http"):
        carregar({"ANTHROPIC_API_KEY": "x", "JARVIS_PROTHEUS_WEBAPP": "file:///etc/passwd"})


def test_o_comando_sem_endereco_configurado_explica(capsys):
    from jarvis.config import carregar
    from jarvis.cli import tratar_calibracao

    sessao = asyncio.run(tratar_calibracao(carregar({"ANTHROPIC_API_KEY": "x"}), None, ""))
    assert sessao is None and "JARVIS_PROTHEUS_WEBAPP" in capsys.readouterr().out


def test_fim_sem_sessao_nao_estoura(capsys):
    from jarvis.config import carregar
    from jarvis.cli import tratar_calibracao

    assert asyncio.run(tratar_calibracao(carregar({"ANTHROPIC_API_KEY": "x"}), None, "fim")) is None
    assert "Não há calibração em andamento" in capsys.readouterr().out


def test_falha_de_rede_vira_erro_explicado_e_nao_derruba(tmp_path, capfd):
    """O que aconteceu de verdade: o endereço respondeu vazio e o Jarvis inteiro caiu junto."""
    navegador = Navegador("https://protheus.royale.com.br:10853/webapp", tmp_path / "perfil")
    for marca, trecho in (
        ("net::ERR_EMPTY_RESPONSE at https://x", "tente JARVIS_PROTHEUS_WEBAPP=http://"),
        ("net::ERR_CONNECTION_REFUSED", "nada atende"),
        ("net::ERR_NAME_NOT_RESOLVED", "não existe nesta rede"),
        ("net::ERR_SSL_PROTOCOL_ERROR", "tente JARVIS_PROTHEUS_WEBAPP=http://"),
        ("Timeout 30000ms exceeded", "não terminou de carregar"),
        ("algo que eu nunca vi", "algo que eu nunca vi"),
    ):
        assert trecho in navegador._explicar(Exception(marca)), marca
    assert "10853" in navegador._explicar(Exception("net::ERR_EMPTY_RESPONSE")), "a porta aparece, é o que ele vai conferir"


def test_o_palpite_olha_o_esquema_que_ja_esta_configurado(tmp_path):
    """O defeito real: com o endereço já em http, ele mandava trocar para http de novo."""
    em_http = Navegador("http://protheus.royale.com.br:10853/webapp", tmp_path / "a")
    recado = em_http._explicar(Exception("net::ERR_EMPTY_RESPONSE"))
    assert "tente JARVIS_PROTHEUS_WEBAPP=https://" in recado and "O endereço está em http" in recado
    em_https = Navegador("https://protheus.royale.com.br:10853/webapp", tmp_path / "b")
    assert "tente JARVIS_PROTHEUS_WEBAPP=http://" in em_https._explicar(Exception("net::ERR_EMPTY_RESPONSE"))


def test_calibracao_que_falha_volta_ao_prompt(capsys, monkeypatch):
    """Nenhuma falha de navegador pode matar a conversa: o Jarvis fica, a calibração não."""
    from jarvis import cli
    from jarvis.config import carregar

    cfg = carregar({"ANTHROPIC_API_KEY": "x", "JARVIS_PROTHEUS_WEBAPP": "https://127.0.0.1:1/webapp"})

    class Quebrada:
        navegador = type("N", (), {"fechar": staticmethod(lambda: asyncio.sleep(0))})()

        async def comecar(self):
            raise RuntimeError("defeito qualquer do navegador")

        async def colher(self):
            raise RuntimeError("a janela sumiu")

        async def terminar(self):
            raise RuntimeError("não deu para salvar")

    for pedido, sessao in (("", None), ("", Quebrada()), ("fim", Quebrada())):
        if sessao is None:  # com monkeypatch: o original volta no fim do teste
            monkeypatch.setattr(cli.webapp, "Calibracao", lambda *a, **k: Quebrada())
        assert asyncio.run(cli.tratar_calibracao(cfg, sessao, pedido)) is None
        assert "[calibração]" in capsys.readouterr().out


def test_resposta_vazia_sobe_a_escada_de_tentativas(tmp_path):
    """O caso real: o servidor fecha a conexão com o que o navegador novo traz por padrão."""
    tentativas: list[list[str]] = []

    class PaginaFalsa:
        def on(self, *_a):
            pass

        async def goto(self, *_a, **_k):
            # O Chrome instalado não existe aqui; das opções do navegador que veio junto, a terceira passa.
            if len(tentativas) < 3:
                raise RuntimeError("net::ERR_EMPTY_RESPONSE at https://x")

    class ContextoFalso:
        pages = [PaginaFalsa()]

        async def close(self):
            pass

    class ChromiumFalso:
        async def launch_persistent_context(self, _perfil, **opcoes):
            if opcoes.get("channel"):
                raise RuntimeError("Executable doesn't exist at chrome.exe")
            tentativas.append(opcoes["args"])
            return ContextoFalso()

    class PlaywrightFalso:
        chromium = ChromiumFalso()

        async def stop(self):
            pass

    navegador = Navegador("https://protheus.royale.com.br:10853/webapp", tmp_path / "perfil",
                          abrir_playwright=lambda: type("P", (), {"start": lambda _s: _async(PlaywrightFalso())})())  # fmt: skip

    asyncio.run(navegador.abrir())
    assert len(tentativas) == 4, "três degraus até funcionar, mais a abertura de verdade"
    assert not any("--disable-http2" in a or "Kyber" in " ".join(a) for a in tentativas[0]), "a primeira vai como veio"
    assert any("PostQuantumKyber" in a for a in tentativas[1]), "o segundo degrau tira a criptografia pós-quântica"
    assert navegador.tentativa == "sem HTTP/2"


def test_erro_que_nao_melhora_afrouxando_nao_repete(tmp_path):
    """Nome que não resolve não melhora mexendo no TLS: insistir só faria o usuário esperar."""
    tentativas = []

    class PaginaFalsa:
        def on(self, *_a):
            pass

        async def goto(self, *_a, **_k):
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    class ContextoFalso:
        pages = [PaginaFalsa()]

        async def close(self):
            pass

    class ChromiumFalso:
        async def launch_persistent_context(self, _perfil, **opcoes):
            tentativas.append(opcoes.get("channel", ""))
            return ContextoFalso()

    class PlaywrightFalso:
        chromium = ChromiumFalso()

        async def stop(self):
            pass

    navegador = Navegador("https://naoexiste.royale.com.br/webapp", tmp_path / "perfil",
                          abrir_playwright=lambda: type("P", (), {"start": lambda _s: _async(PlaywrightFalso())})())  # fmt: skip
    with pytest.raises(ErroWebApp, match="não existe nesta rede"):
        asyncio.run(navegador.abrir())
    assert len(tentativas) == 1, "uma só, com o Chrome instalado; o resto não mudaria nada"


async def _async(valor):
    return valor


@sem_navegador
def test_modo_minimo_abre_sem_nenhum_ajuste(tmp_path, webapp_falso, capfd):
    """`/calibrar testar`: separa 'o servidor não abre' de 'alguma coisa que eu faço atrapalha'."""

    async def cena():
        navegador = Navegador(webapp_falso(8958), tmp_path / "perfil", visivel=False, executavel=CHROMIUM)
        await navegador.abrir(minimo=True)
        medida = await navegador.medir()
        assert medida.campos >= 3 and "Protheus" in medida.titulo
        await navegador.fechar()

    _rodar(cena, capfd)


def test_o_chrome_instalado_e_a_primeira_tentativa(tmp_path):
    """O que resolve de verdade: se o servidor conversa com o Chrome do usuário, conversa aqui."""
    canais = []

    class PaginaFalsa:
        def on(self, *_a):
            pass

        async def goto(self, *_a, **_k):
            if canais[-1] != "chrome":
                raise RuntimeError("net::ERR_EMPTY_RESPONSE at https://x")

    class ContextoFalso:
        pages = [PaginaFalsa()]

        async def close(self):
            pass

    class ChromiumFalso:
        async def launch_persistent_context(self, _perfil, **opcoes):
            canais.append(opcoes.get("channel", ""))
            return ContextoFalso()

    class PlaywrightFalso:
        chromium = ChromiumFalso()

        async def stop(self):
            pass

    navegador = Navegador("https://protheus.royale.com.br:10853/webapp", tmp_path / "perfil",
                          abrir_playwright=lambda: type("P", (), {"start": lambda _s: _async(PlaywrightFalso())})())  # fmt: skip
    asyncio.run(navegador.abrir())
    # Procura escondido e, achando logo de cara, abre a janela de verdade com a mesma opção.
    assert canais == ["chrome", "chrome"], "o Chrome instalado é a primeira tentativa"
    assert navegador.tentativa == webapp.CHROME_DO_USUARIO


def test_a_procura_usa_janela_fora_da_tela_e_nao_modo_escondido(tmp_path):
    """O defeito real: o WebApp da TOTVS abre com janela e fecha a conexão com navegador escondido."""
    vistos = []

    class PaginaFalsa:
        def on(self, *_a):
            pass

        async def goto(self, *_a, **_k):
            pass

    class ContextoFalso:
        pages = [PaginaFalsa()]

        async def close(self):
            pass

    class ChromiumFalso:
        async def launch_persistent_context(self, _perfil, **opcoes):
            vistos.append((opcoes["headless"], opcoes["args"]))
            return ContextoFalso()

    class PlaywrightFalso:
        chromium = ChromiumFalso()

        async def stop(self):
            pass

    navegador = Navegador("https://protheus.royale.com.br:10853/webapp", tmp_path / "perfil", visivel=True,
                          abrir_playwright=lambda: type("P", (), {"start": lambda _s: _async(PlaywrightFalso())})())  # fmt: skip
    asyncio.run(navegador.abrir())
    procura, aberta = vistos[0], vistos[-1]
    assert procura[0] is False, "procurar não usa modo escondido"
    assert "--window-position=-32000,-32000" in procura[1], "a janela da procura vai para fora da tela"
    assert "--window-position=-32000,-32000" not in aberta[1], "a janela de verdade aparece na tela"


def test_a_barra_do_fim_e_preservada():
    """`/webapp` e `/webapp/` são endereços diferentes, e o WebApp da TOTVS só abre com a barra."""
    com_barra = "https://royale191155.protheus.cloudtotvs.com.br:10853/webapp/"
    assert conferir_endereco(f"  {com_barra} ") == com_barra, "a barra do fim é do usuário, não minha"
    assert conferir_endereco("https://x.com.br:10853/webapp") == "https://x.com.br:10853/webapp"
    assert mesma_origem(com_barra, "https://royale191155.protheus.cloudtotvs.com.br:10853/webapp/outra")


@sem_navegador
def test_o_diagnostico_relata_cada_combinacao(tmp_path, webapp_falso, capfd):
    """O relatório que eu peço ao usuário: uma linha por combinação, com o erro cru."""

    async def cena():
        linhas = await webapp.diagnosticar(webapp_falso(8959), tmp_path / "perfis", executavel=CHROMIUM)
        texto = "\n".join(linhas)
        assert "Endereço testado:" in linhas[0]
        assert len(linhas) == 5, "uma linha por combinação, mais o cabeçalho"
        for linha in linhas[1:]:
            assert linha.lstrip().startswith(("OK", "FALHA")), linha
        # Aqui não há tela, então janela normal não abre; o que precisa valer é que a escondida
        # abre e que o relatório traz o título da página e o endereço final.
        assert "OK" in texto and "Protheus WebApp" in texto, texto
        assert "janela escondida" in texto and "endereço final" in texto

    _rodar(cena, capfd)


def test_nao_diz_que_falhou_quando_a_primeira_tentativa_funcionou(tmp_path):
    """Defeito real: com o seu Chrome acertando de primeira, ele dizia que a primeira falhou."""

    class PaginaFalsa:
        def on(self, *_a):
            pass

        async def goto(self, *_a, **_k):
            pass

    class ContextoFalso:
        pages = [PaginaFalsa()]

        async def close(self):
            pass

    class ChromiumFalso:
        async def launch_persistent_context(self, _perfil, **_opcoes):
            return ContextoFalso()

    class PlaywrightFalso:
        chromium = ChromiumFalso()

        async def stop(self):
            pass

    navegador = Navegador("https://protheus.royale.com.br:10853/webapp/", tmp_path / "perfil",
                          abrir_playwright=lambda: type("P", (), {"start": lambda _s: _async(PlaywrightFalso())})())  # fmt: skip
    asyncio.run(navegador.abrir())
    assert navegador.tentativa == webapp.CHROME_DO_USUARIO
    assert navegador.falhou_antes is False, "nada falhou: o aviso não pode aparecer"
