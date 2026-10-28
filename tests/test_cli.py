import asyncio
import json
import threading
import time

import pytest

from claude_agent_sdk import ToolUseBlock

from jarvis import cli


def test_descrever_mostra_o_alvo_da_chamada():
    assert cli.descrever(ToolUseBlock(id="1", name="Read", input={"file_path": "C:/a.txt"})) == "Read: C:/a.txt"
    assert cli.descrever(ToolUseBlock(id="2", name="Grep", input={"pattern": "nota", "path": "C:/x"})) == "Grep: nota"
    assert cli.descrever(ToolUseBlock(id="3", name="WebSearch", input={"query": "dólar hoje"})) == "WebSearch: dólar hoje"
    assert cli.descrever(ToolUseBlock(id="4", name="mcp__jarvis__agora", input={})) == "agora"
    salvar = ToolUseBlock(id="5", name="mcp__jarvis__memoria_salvar", input={"titulo": "Apelido", "conteudo": "x"})
    assert cli.descrever(salvar) == "memoria_salvar: Apelido"
    site = ToolUseBlock(id="6", name="mcp__jarvis__abrir_site", input={"url": "https://exemplo.com.br"})
    assert cli.descrever(site) == "abrir_site: https://exemplo.com.br"
    assert cli.descrever(ToolUseBlock(id="7", name="mcp__jarvis__mover", input={"itens": []})) == "mover"
    assert cli.descrever(ToolUseBlock(id="8", name="mcp__jarvis__tela_clicar", input={"x": 640, "y": 0})) == "tela_clicar: (640, 0)"
    assert cli.descrever(ToolUseBlock(id="9", name="mcp__jarvis__rolar", input={"direcao": "baixo", "telas": 2})) == "rolar: baixo"
    assert cli.descrever(ToolUseBlock(id="10", name="mcp__jarvis__midia", input={"comando": "tocar_pausar"})) == "midia: tocar_pausar"
    assert cli.descrever(ToolUseBlock(id="11", name="mcp__jarvis__tela_digitar", input={"texto": "lofi", "enter": True})) == "tela_digitar: lofi"
    assert cli.descrever(ToolUseBlock(id="12", name="mcp__jarvis__tela_tecla", input={"tecla": "k"})) == "tela_tecla: k"
    assert cli.descrever(ToolUseBlock(id="13", name="mcp__jarvis__tela_ver", input={})) == "tela_ver"


def test_resumir_erro_tira_as_marcacoes():
    assert cli.resumir_erro("<tool_use_error>Error: No such tool available: Bash.</tool_use_error>") == (
        "Error: No such tool available: Bash."
    )
    assert cli.resumir_erro("PreToolUse:Read hook error: Caminho fora das pastas liberadas (C:/a): C:/b") == (
        "Caminho fora das pastas liberadas (C:/a): C:/b"
    )
    assert cli.resumir_erro([{"type": "text", "text": "falhou\nsegunda linha"}]) == "falhou"
    assert cli.resumir_erro(None) == "sem detalhe"
    assert len(cli.resumir_erro("x" * 500)) == 200


def test_ler_linha_devolve_o_texto_e_propaga_fim_de_entrada(monkeypatch):
    respostas = iter(["olá", EOFError()])

    def falso_input(_prompt):
        valor = next(respostas)
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr("builtins.input", falso_input)

    async def cenario():
        primeira = await cli.ler_linha("> ")
        try:
            await cli.ler_linha("> ")
        except EOFError:
            return primeira, True
        return primeira, False

    assert asyncio.run(cenario()) == ("olá", True)


def test_comando_memoria(tmp_path, capsys):
    from jarvis.memoria import Memoria

    memoria = Memoria(tmp_path / "memoria")
    cli.mostrar_memoria(memoria)
    assert "vazia" in capsys.readouterr().out
    memoria.salvar("Apelido", "Prefere ser chamado de chefe.")
    cli.mostrar_memoria(memoria)
    saida = capsys.readouterr().out
    assert "1 fato(s)" in saida and "apelido: Apelido — Prefere ser chamado de chefe." in saida
    assert "lição" not in saida
    memoria.salvar("Fontes", "Citar pelo nome.", categoria="licao")
    cli.mostrar_memoria(memoria)
    saida = capsys.readouterr().out
    assert "1 lição(ões)" in saida and "fontes: Fontes — Citar pelo nome." in saida
    assert "preferência" not in saida
    memoria.salvar("Navegador", "Abrir no Chrome.", categoria="preferencia")
    cli.mostrar_memoria(memoria)
    saida = capsys.readouterr().out
    assert "1 preferência(s)" in saida and "navegador: Navegador — Abrir no Chrome." in saida
    assert saida.index("fato(s)") < saida.index("preferência(s)") < saida.index("lição(ões)")


def test_comando_historico(capsys):
    from datetime import datetime

    from jarvis.registro import Chamada, Registro, Turno

    registro = Registro(":memory:")
    cli.mostrar_historico(registro)
    assert "Ainda não há conversas" in capsys.readouterr().out
    registro.gravar(Turno("primeira", resposta="resposta\nem duas linhas", inicio=datetime(2026, 10, 7, 9, 0), custo_usd=0.01))
    registro.gravar(
        Turno("segunda", modo="voz", inicio=datetime(2026, 10, 7, 9, 5), erro="falhou", chamadas=[Chamada("Read", {})])
    )
    cli.mostrar_historico(registro)
    linhas = capsys.readouterr().out.splitlines()
    assert linhas[0].strip() == "07/10 09:00 [texto] primeira"
    assert "resposta em duas linhas" in linhas[1] and "US$ 0.0100" in linhas[1]
    assert linhas[2].strip() == "07/10 09:05 [voz] segunda"
    assert "(sem resposta)" in linhas[3] and "1 ferramenta(s)" in linhas[3] and "com erro" in linhas[3]


# --- confirmação digitada ---------------------------------------------------


def entradas(monkeypatch, *linhas):
    """Simula o teclado: cada chamada de input() devolve a próxima linha (ou trava, se for um Event)."""
    fila = iter(linhas)

    def falso_input(_prompt=""):
        valor = next(fila)
        if hasattr(valor, "wait"):
            valor.wait()
            valor = next(fila)
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr("builtins.input", falso_input)


@pytest.mark.parametrize("resposta, esperado", [("s", True), ("S", True), (" sim ", True), ("n", False), ("", False), ("talvez", False)])
def test_so_s_ou_sim_confirmam(monkeypatch, capsys, resposta, esperado):
    entradas(monkeypatch, resposta)
    aceito = asyncio.run(cli.confirmar_no_terminal("mover 2 itens", ["a → b", "c → d"]))
    assert aceito is esperado
    saida = capsys.readouterr().out
    assert "O Jarvis quer mover 2 itens:" in saida and "a → b" in saida and "c → d" in saida
    assert ("Não executei." in saida) is (not esperado)
    assert "conteúdo externo" not in saida


def test_confirmacao_avisa_quando_o_turno_leu_conteudo_externo(monkeypatch, capsys):
    entradas(monkeypatch, "s")
    asyncio.run(cli.confirmar_no_terminal("abrir um site", ["https://x"], leu_conteudo_externo="WebSearch"))
    assert "leu conteúdo externo (WebSearch)" in capsys.readouterr().out


def test_fim_de_entrada_conta_como_nao(monkeypatch):
    entradas(monkeypatch, EOFError())
    assert asyncio.run(cli.confirmar_no_terminal("apagar", ["x"])) is False


def test_sem_resposta_nao_executa_e_a_linha_atrasada_vai_para_o_proximo_pedido(monkeypatch, capsys):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "resposta atrasada", "segunda linha")

    async def cenario():
        aceito = await cli.confirmar_no_terminal("apagar 1 item", ["x"], tempo=0.2)
        trava.set()  # o usuário finalmente digita
        proxima = await cli.ler_linha("você > ")
        depois = await cli.ler_linha("você > ")
        return aceito, proxima, depois

    assert asyncio.run(cenario()) == (False, "resposta atrasada", "segunda linha")
    assert "Sem resposta; não executei." in capsys.readouterr().out


def test_anunciar_mostra_o_plano_sem_perguntar(monkeypatch, capsys):
    def nao_pode_ler(_prompt=""):
        raise AssertionError("execução direta não lê o teclado")

    monkeypatch.setattr("builtins.input", nao_pode_ler)
    cli.anunciar("mover 2 itens", ["a → b", "c → d"])
    saida = capsys.readouterr().out
    assert saida == "  >> mover 2 itens:\n       a → b\n       c → d\n"
    assert "Confirma" not in saida


# --- tecla de atalho ----------------------------------------------------------------


class VozFalsa:
    def __init__(self, texto="abre o youtube"):
        self.texto, self.eventos = texto, []

    def comecar_gravacao(self):
        self.eventos.append("comecou")

    def terminar_gravacao(self):
        self.eventos.append("terminou")
        return self.texto

    def interromper(self):
        self.eventos.append("calou")


@pytest.fixture
def bipes(monkeypatch):
    tocados = []
    monkeypatch.setattr(cli, "_bipe", tocados.append)
    return tocados


def test_esperar_entrada_devolve_a_linha_do_terminal(monkeypatch):
    entradas(monkeypatch, "olá")

    async def cenario():
        return await cli.esperar_entrada("> ", asyncio.Event())

    assert asyncio.run(cenario()) == ("linha", "olá")


def test_esperar_entrada_devolve_o_atalho_e_a_linha_fica_para_depois(monkeypatch):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "digitado depois")

    async def cenario():
        tecla = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, tecla.set)
        primeiro = await cli.esperar_entrada("> ", tecla)
        assert not tecla.is_set()  # o toque foi consumido
        trava.set()  # agora o usuário digita no terminal
        segundo = await cli.esperar_entrada("> ", tecla)
        return primeiro, segundo

    assert asyncio.run(cenario()) == (("atalho", ""), ("linha", "digitado depois"))


def test_esperar_entrada_com_tempo_esgotado(monkeypatch):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "x")

    async def cenario():
        resultado = await cli.esperar_entrada("> ", asyncio.Event(), tempo=0.05)
        trava.set()
        await cli.ler_linha("> ")  # esvazia a leitura pendente
        return resultado

    assert asyncio.run(cenario()) == ("tempo", "")


def test_fim_de_entrada_passa_adiante(monkeypatch):
    entradas(monkeypatch, EOFError())

    async def cenario():
        await cli.esperar_entrada("> ", asyncio.Event())

    with pytest.raises(EOFError):
        asyncio.run(cenario())


def test_ouvir_pelo_atalho_bipa_antes_de_gravar_e_para_no_segundo_toque(monkeypatch, bipes, capsys):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "linha que não interessa")
    voz = VozFalsa()

    async def cenario():
        tecla = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, tecla.set)  # o segundo toque
        texto = await cli.ouvir(voz, tecla, sinal=True)
        trava.set()
        await cli.ler_linha("> ")
        return texto

    assert asyncio.run(cenario()) == "abre o youtube"
    assert voz.eventos == ["comecou", "terminou"]
    assert bipes == [880, 660]  # um aviso ao começar, outro ao terminar
    assert "transcrevendo" in capsys.readouterr().out


def test_ouvir_pelo_enter_continua_igual_e_sem_bipe(monkeypatch, bipes):
    entradas(monkeypatch, "")
    voz = VozFalsa("")

    async def cenario():
        return await cli.ouvir(voz)

    assert asyncio.run(cenario()) == ""
    assert voz.eventos == ["comecou", "terminou"] and bipes == []


def test_gravacao_para_sozinha_depois_do_tempo_maximo(monkeypatch, bipes, capsys):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "x")
    monkeypatch.setattr(cli, "TEMPO_MAX_DE_FALA", 0.05)
    voz = VozFalsa("fala longa")

    async def cenario():
        texto = await cli.ouvir(voz, asyncio.Event(), sinal=True)
        trava.set()
        await cli.ler_linha("> ")
        return texto

    assert asyncio.run(cenario()) == "fala longa"
    assert "passou de um minuto" in capsys.readouterr().out


class RegistroFalso:
    def __init__(self, combinacao, ao_apertar, falha=None):
        self.combinacao, self.ao_apertar, self.falha, self.ligado = combinacao, ao_apertar, falha, False

    def iniciar(self):
        if self.falha:
            from jarvis.atalho import ErroAtalho

            raise ErroAtalho(self.falha)
        self.ligado = True


def test_ligar_atalho_registra_e_o_toque_dispara_o_sinal(capsys):
    async def cenario():
        tecla, registro = cli.ligar_atalho("", True, asyncio.get_running_loop(), RegistroFalso, "win32")
        assert registro.ligado and registro.combinacao.texto == "Ctrl+Alt+J" and not tecla.is_set()
        registro.ao_apertar()  # o Windows avisa (de outra thread, na vida real)
        await asyncio.sleep(0.01)
        return tecla.is_set()

    assert asyncio.run(cenario()) is True
    assert "Ctrl+Alt+J começa e termina a gravação" in capsys.readouterr().out


def test_ligar_atalho_no_modo_texto_diz_o_que_a_tecla_faz(capsys):
    async def cenario():
        return cli.ligar_atalho("f9", False, asyncio.get_running_loop(), RegistroFalso, "win32")

    tecla, _ = asyncio.run(cenario())
    assert tecla is not None and "F9 traz o terminal para frente" in capsys.readouterr().out


def test_atalho_desligado_ou_fora_do_windows_nao_registra_nada(capsys):
    async def cenario():
        laco = asyncio.get_running_loop()
        return cli.ligar_atalho("nao", True, laco, RegistroFalso, "win32"), cli.ligar_atalho("", True, laco, RegistroFalso, "linux")

    assert asyncio.run(cenario()) == ((None, None), (None, None))
    assert capsys.readouterr().out == ""


def test_atalho_recusado_pelo_windows_vira_aviso_e_o_jarvis_segue(capsys):
    def fabrica(combinacao, ao_apertar):
        return RegistroFalso(combinacao, ao_apertar, falha="Ctrl+Alt+J já está em uso por outro programa")

    async def cenario():
        return cli.ligar_atalho("", True, asyncio.get_running_loop(), fabrica, "win32")

    assert asyncio.run(cenario()) == (None, None)
    assert "[aviso] Tecla de atalho desligada: Ctrl+Alt+J já está em uso" in capsys.readouterr().out


# --- painel ------------------------------------------------------------------------------


class PainelQueAnota:
    """Guarda, na ordem, tudo o que a conversa contou ao painel."""

    def __init__(self):
        self.avisos = []

    def fase(self, nome, detalhe=""):
        self.avisos.append(("fase", nome, detalhe))

    def legenda(self, quem, texto):
        self.avisos.append(("legenda", quem, texto))

    def falar_texto(self, texto):
        self.avisos.append(("falar_texto", texto))

    def nivel(self, valor):
        self.avisos.append(("nivel", valor))

    def silencio(self):
        self.avisos.append(("silencio",))

    def fases(self):
        return [a[1] for a in self.avisos if a[0] == "fase"]


class ClienteFalso:
    def __init__(self, mensagens):
        self.mensagens, self.perguntas = mensagens, []

    async def query(self, texto):
        self.perguntas.append(texto)

    async def receive_response(self):
        for mensagem in self.mensagens:
            yield mensagem


def fim(custo=0.01):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s", total_cost_usd=custo)


def test_responder_conta_ao_painel_cada_passo_do_turno(capsys):
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolResultBlock, UserMessage

    cliente = ClienteFalso([
        AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__jarvis__abrir_site", input={"url": "https://exemplo.com.br"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="Abri.", is_error=False)]),
        AssistantMessage(content=[TextBlock(text="Abri o site.")], model="m"),
        fim(),
    ])  # fmt: skip
    painel, aprendizados = PainelQueAnota(), []
    turno = asyncio.run(cli.responder(cliente, "abre o site", painel=painel, aprendeu=lambda: aprendizados.append(1)))
    assert turno.resposta == "Abri o site." and cliente.perguntas == ["abre o site"]
    assert painel.avisos == [
        ("silencio",),  # a resposta anterior, se ainda estava sendo "dita", sai de cena
        ("legenda", "voce", "abre o site"),
        ("fase", "pensando", ""),
        ("fase", "agindo", "abrir_site: https://exemplo.com.br"),
        ("fase", "pensando", ""),
        ("legenda", "jarvis", "Abri o site."),
        ("falar_texto", "Abri o site."),  # sem voz, a rede "diz" o texto
        ("fase", None, ""),
    ]
    assert aprendizados == []  # abrir site não muda a memória


def test_salvar_na_memoria_faz_a_rede_crescer_na_hora():
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolResultBlock, UserMessage

    cliente = ClienteFalso([
        AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__jarvis__memoria_salvar", input={"titulo": "Navegador", "conteudo": "Chrome."})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="Salvo.", is_error=False)]),
        AssistantMessage(content=[ToolUseBlock(id="t2", name="mcp__jarvis__memoria_salvar", input={"titulo": "x", "conteudo": "y"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t2", content="Gravação bloqueada", is_error=True)]),
        AssistantMessage(content=[ToolUseBlock(id="t3", name="mcp__jarvis__memoria_apagar", input={"nome": "navegador"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t3", content="Apagado.", is_error=False)]),
        AssistantMessage(content=[TextBlock(text="Anotado.")], model="m"),
        fim(),
    ])  # fmt: skip
    aprendizados = []
    asyncio.run(cli.responder(cliente, "prefiro o chrome", painel=PainelQueAnota(), aprendeu=lambda: aprendizados.append(1)))
    assert aprendizados == [1, 1]  # o salvar que deu certo e o apagar; o bloqueado não conta


def test_com_voz_a_legenda_acompanha_o_texto_chegando():
    from claude_agent_sdk import AssistantMessage, TextBlock
    from claude_agent_sdk.types import StreamEvent

    class VozQueNarra(VozFalsa):
        def narrar(self, trecho):
            self.eventos.append(("narrar", trecho))

        def fim_de_bloco(self):
            self.eventos.append("fim_de_bloco")

    def pedaco(texto):
        return StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": texto}})

    cliente = ClienteFalso([pedaco("São três "), pedaco("e meia."), AssistantMessage(content=[TextBlock(text="São três e meia.")], model="m"), fim()])
    painel, voz = PainelQueAnota(), VozQueNarra()
    asyncio.run(cli.responder(cliente, "que horas são?", voz=voz, modo="voz", painel=painel))
    legendas = [a[2] for a in painel.avisos if a[:2] == ("legenda", "jarvis")]
    assert legendas == ["São três ", "São três e meia.", "São três e meia."]
    assert not [a for a in painel.avisos if a[0] == "falar_texto"]  # com voz, quem anima a rede é o som de verdade
    assert ("narrar", "São três ") in voz.eventos


def test_responder_sem_painel_continua_igual(capsys):
    from claude_agent_sdk import AssistantMessage, TextBlock

    turno = asyncio.run(cli.responder(ClienteFalso([AssistantMessage(content=[TextBlock(text="Oi.")], model="m"), fim()]), "oi"))
    assert turno.resposta == "Oi." and "jarvis > Oi." in capsys.readouterr().out


def test_ouvir_mostra_ouvindo_com_o_volume_e_depois_entendendo(monkeypatch, bipes):
    import threading

    trava = threading.Event()
    entradas(monkeypatch, trava, "x")

    class VozComNivel(VozFalsa):
        def nivel_do_microfone(self):
            return 0.5

    painel, voz = PainelQueAnota(), VozComNivel()

    async def cenario():
        tecla = asyncio.Event()
        asyncio.get_running_loop().call_later(0.25, tecla.set)
        texto = await cli.ouvir(voz, tecla, sinal=True, painel=painel)
        trava.set()
        await cli.ler_linha("> ")
        return texto

    assert asyncio.run(cenario()) == "abre o youtube"
    assert painel.fases() == ["ouvindo", "entendendo"]  # com texto, quem segue é o "pensando" do responder
    niveis = [a for a in painel.avisos if a[0] == "nivel"]
    assert len(niveis) >= 2 and niveis[0] == ("nivel", 0.5)
    assert painel.avisos.index(("fase", "entendendo", "")) > painel.avisos.index(niveis[-1])  # parou de medir ao parar de gravar


def test_ouvir_sem_fala_volta_para_a_espera(monkeypatch, bipes):
    entradas(monkeypatch, "")
    painel, voz = PainelQueAnota(), VozFalsa("")
    voz.nivel_do_microfone = lambda: 0.0
    asyncio.run(cli.ouvir(voz, painel=painel))
    assert painel.fases() == ["ouvindo", "entendendo", None]


class SessaoFalsa:
    def __init__(self, janelas=None):
        self.janelas = janelas


def test_ligar_painel_sobe_o_servidor_abre_a_janela_e_liga_a_voz(tmp_path, capsys):
    from jarvis.config import Config
    from jarvis.memoria import Memoria
    from jarvis.painel import Painel
    from jarvis.registro import Registro

    memoria = Memoria(tmp_path / "m")
    memoria.salvar("Apelido", "Chefe.")
    cfg = Config(modelo="sonnet", usuario=None, pastas=(tmp_path,), orcamento_usd=None)
    abertos, observado = [], []

    class Voz(VozComAjustes):
        def observar(self, ao_tocar, ao_calar):
            observado.append((ao_tocar, ao_calar))

    class Janelas:
        def trazer_terminal(self):
            observado.append("terminal")

    voz = Voz(outras=("pt_BR-cadu-medium",))

    async def cenario():
        painel = cli.ligar_painel(cfg, memoria, Registro(":memory:"), voz, SessaoFalsa(Janelas()), abrir=lambda url: abertos.append(url) or "chrome")
        await asyncio.sleep(0)
        return painel

    painel = asyncio.run(cenario())
    try:
        assert isinstance(painel, Painel) and abertos == [painel.endereco]
        assert painel._retrato["conhecimento"]["itens"] == [{"c": "fato", "n": "apelido", "t": "Apelido"}]
        assert observado[0] == (painel.som, painel.silencio)
        saida = capsys.readouterr().out
        assert "Painel aberto em janela própria (chrome)" in saida and "voz está desligada" not in saida
        # Os ajustes da engrenagem: guardados na pasta de dados, com as vozes baixadas à escolha, e postos na voz já na partida.
        assert painel._arquivo == cli.PASTA_DADOS / "painel.json" and painel._retrato["vozes"] == ["pt_BR-cadu-medium"]
        assert voz.ajustes == [("muda", False), ("velocidade", 1.03)] and voz.voz_atual == ""
        # E cada troca feita na página chega à voz de verdade.
        painel._mudar_aparencia({"voz": False, "velocidade": 1.4})
        assert voz.ajustes[2:] == [("muda", True), ("velocidade", 1.4)]
        assert json.loads((cli.PASTA_DADOS / "painel.json").read_text(encoding="utf-8"))["voz"] is False
    finally:
        painel.fechar()


def test_painel_que_guardou_a_voz_desligada_sobe_com_ela_desligada_e_avisa(tmp_path, capsys):
    from jarvis.config import Config
    from jarvis.memoria import Memoria
    from jarvis.registro import Registro

    cli.PASTA_DADOS.mkdir(parents=True)
    (cli.PASTA_DADOS / "painel.json").write_text(json.dumps({"voz": False, "velocidade": 0.8, "voz_nome": "pt_BR-cadu-medium"}), encoding="utf-8")
    cfg = Config(modelo="sonnet", usuario=None, pastas=(tmp_path,), orcamento_usd=None)
    voz = VozComAjustes(outras=("pt_BR-cadu-medium",))

    async def cenario():
        return cli.ligar_painel(cfg, Memoria(tmp_path / "m"), Registro(":memory:"), voz, SessaoFalsa(), abrir=None)

    painel = asyncio.run(cenario())
    try:
        assert voz.ajustes[:2] == [("muda", True), ("velocidade", 0.8)]
        assert voz.trocou.wait(3) and voz.voz_atual == "pt_BR-cadu-medium"  # a outra voz carrega à parte, sem segurar a partida
        assert "A voz está desligada no painel" in capsys.readouterr().out
    finally:
        painel.fechar()


class VozComAjustes:
    """A parte da voz que os ajustes do painel alcançam."""

    def __init__(self, outras=(), erro=None):
        self.muda, self.voz_atual, self.ajustes, self._outras, self._erro = False, "", [], list(outras), erro
        self.trocou = threading.Event()

    def observar(self, ao_tocar, ao_calar):
        pass

    def vozes(self):
        return list(self._outras)

    def emudecer(self, sim):
        self.muda = sim
        self.ajustes.append(("muda", sim))

    def velocidade(self, valor):
        self.ajustes.append(("velocidade", valor))

    def trocar_voz(self, nome):
        try:
            if self._erro is not None:
                raise self._erro
            self.voz_atual = nome
        finally:
            self.trocou.set()


def test_ajustar_voz_liga_desliga_muda_a_velocidade_e_troca_de_voz_a_parte():
    voz = VozComAjustes(outras=("pt_BR-cadu-medium",))
    cli.ajustar_voz(voz, {"voz": True, "velocidade": 1.2, "voz_nome": ""})
    assert voz.ajustes == [("muda", False), ("velocidade", 1.2)] and not voz.trocou.is_set()  # a mesma voz: nada a carregar
    cli.ajustar_voz(voz, {"voz": False, "velocidade": "torta", "voz_nome": "pt_BR-cadu-medium"})
    assert voz.ajustes[2:] == [("muda", True)]  # velocidade que não é número fica como estava
    assert voz.trocou.wait(3) and voz.voz_atual == "pt_BR-cadu-medium"
    cli.ajustar_voz(voz, {})  # sem nada dito: ligada, velocidade natural, a voz de sempre
    assert voz.ajustes[3:] == [("muda", False), ("velocidade", 1.0)]


def test_voz_que_nao_carrega_vira_aviso_e_a_anterior_continua(capsys):
    from jarvis.voz import ErroVoz

    voz = VozComAjustes(erro=ErroVoz("Não consegui carregar a voz pt_BR-x: arquivo corrompido"))
    cli.ajustar_voz(voz, {"voz_nome": "pt_BR-x"})
    assert voz.trocou.wait(3)
    time.sleep(0.05)
    assert voz.voz_atual == "" and "[aviso] Não consegui carregar a voz pt_BR-x" in capsys.readouterr().out
    voz = VozComAjustes(erro=RuntimeError("defeito"))
    cli.ajustar_voz(voz, {"voz_nome": "pt_BR-y"})  # defeito inesperado não derruba nada
    assert voz.trocou.wait(3)


def test_mostrar_no_painel_abre_os_cartoes_e_a_janela_se_estiver_fechada(monkeypatch):
    from jarvis.painel import MUDO, Painel

    assert "desligado" in cli.mostrar_no_painel(MUDO, [{"titulo": "Clima"}])
    abertos = []
    monkeypatch.setattr(cli, "abrir_janela", lambda endereco: abertos.append(endereco) or "chrome")
    painel = Painel()
    painel.endereco = "http://127.0.0.1:1/x/"
    assert cli.mostrar_no_painel(painel, [{"titulo": "Maragogi", "destaque": "27°"}]) == "1 cartão no painel."
    assert abertos == [painel.endereco] and painel._paineis == [{"titulo": "Maragogi", "destaque": "27°"}]
    fila, _ = painel.assinar()  # com a página aberta, não abre outra janela
    assert cli.mostrar_no_painel(painel, [{"titulo": "A"}, {"titulo": "B"}]) == "2 cartões no painel."
    assert abertos == [painel.endereco]
    assert "Nenhum cartão" in cli.mostrar_no_painel(painel, [{"cor": "azul"}, "torto"])
    assert cli.mostrar_no_painel(painel, []) == "Cartões fechados." and painel._paineis == []
    painel.cancelar(fila)
    monkeypatch.setattr(cli, "abrir_janela", lambda endereco: "")
    assert "não abriu" in cli.mostrar_no_painel(painel, [{"titulo": "A"}])


def test_painel_desligado_ou_sem_navegador(tmp_path, capsys):
    from jarvis.config import Config
    from jarvis.memoria import Memoria
    from jarvis.painel import MUDO
    from jarvis.registro import Registro

    base = dict(modelo="sonnet", usuario=None, pastas=(tmp_path,), orcamento_usd=None)
    memoria, registro = Memoria(tmp_path / "m"), Registro(":memory:")

    def nao_pode(_url):
        raise AssertionError("com o painel desligado, nada é aberto")

    assert cli.ligar_painel(Config(**base, painel=False), memoria, registro, None, SessaoFalsa(), abrir=nao_pode) is MUDO
    painel = cli.ligar_painel(Config(**base), memoria, registro, None, SessaoFalsa(), abrir=lambda url: "")
    try:
        assert f"Painel: abra {painel.endereco}" in capsys.readouterr().out
    finally:
        painel.fechar()


def test_falha_ao_subir_o_painel_vira_aviso(tmp_path, capsys, monkeypatch):
    from jarvis.config import Config
    from jarvis.memoria import Memoria
    from jarvis.painel import MUDO, Painel
    from jarvis.registro import Registro

    def sem_porta(self):
        raise OSError("sem porta livre")

    monkeypatch.setattr(Painel, "iniciar", sem_porta)
    cfg = Config(modelo="sonnet", usuario=None, pastas=(tmp_path,), orcamento_usd=None)
    assert cli.ligar_painel(cfg, Memoria(tmp_path / "m"), Registro(":memory:"), None, SessaoFalsa()) is MUDO
    assert "[aviso] Painel desligado: sem porta livre" in capsys.readouterr().out
