"""A palavra de ativação e o segundo plano dentro da conversa: do "Jarvis, …" ao turno, e o ícone."""

import asyncio
import json
import threading
from datetime import datetime

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from jarvis import cli, segundo_plano
from jarvis.config import Config
from jarvis.memoria import Memoria
from jarvis.painel import MUDO
from jarvis.registro import Registro
from jarvis.voz import ErroVoz

# --- peças falsas -----------------------------------------------------------------------------------


class VigiaFalsa:
    def __init__(self):
        self.atenta, self.pausada, self.erro, self.palavras = False, False, "", ("jarvis",)
        self.aberta, self.suspensa, self.esperando_pedido = True, False, False
        self.ouvidos, self.eventos, self.tempos = [], [], []

    def conferir_microfone(self):
        self.eventos.append("conferir")
        return self.aberta

    def pausar(self):
        self.pausada = True
        self.eventos.append("pausar")

    def continuar(self):
        self.pausada = False
        self.eventos.append("continuar")


class VozFalsa:
    """A voz sem áudio: guarda o que seria dito e os avisos que a vigília usaria para falar com a conversa."""

    def __init__(self, sem_microfone=0, ensaio=None):
        self.vigia, self.avisos, self.dito, self.eventos = None, {}, [], []
        self.sem_microfone = sem_microfone  # quantas vezes o microfone ainda falha ao abrir
        self.falando, self.calada, self.prontas, self.ensaio, self.ensaios = False, False, [], ensaio, []
        self.acabou_ha = float("inf")  # segundos desde que terminou de falar
        self.muda, self.voz_atual, self.ajustes = False, "", []  # os ajustes de voz do painel

    def vozes(self):
        return []

    def emudecer(self, sim):
        self.muda = bool(sim)
        self.ajustes.append(("muda", self.muda))

    def velocidade(self, valor):
        self.ajustes.append(("velocidade", valor))

    def trocar_voz(self, nome):
        self.voz_atual = nome

    def vigiar(self, palavras, ao_pedir, impedido=None, **avisos):
        self.impedido = impedido
        if self.sem_microfone:
            self.sem_microfone -= 1
            raise ErroVoz("Não consegui abrir o microfone para a palavra de ativação: nenhum dispositivo")
        self.vigia = VigiaFalsa()
        self.vigia.palavras = tuple(palavras)
        self.avisos = {"ao_pedir": ao_pedir, **avisos}

    def parar_vigilia(self):
        self.eventos.append("parar_vigilia")
        self.vigia = None

    def narrar(self, texto):
        if not self.calada and not self.muda:
            self.dito.append(texto)

    def dizer(self, frase):
        if not self.muda:
            self.dito.append(frase)

    def preparar(self, frases):
        self.prontas += list(frases)

    def fim_de_bloco(self):
        pass

    def interromper(self):
        self.eventos.append("calou")
        self.falando = False

    def calar(self):
        self.eventos.append("calou até o próximo pedido")
        self.falando, self.calada = False, True

    def falou_ha(self):
        return 0.0 if self.falando else self.acabou_ha

    def soltar(self):
        self.calada = False

    def pausar_fala(self):
        self.eventos.append("pausou")

    def retomar_fala(self):
        self.eventos.append("retomou")

    def ensaiar_chamado(self, palavra):
        self.ensaios.append(palavra)
        if isinstance(self.ensaio, Exception):
            raise self.ensaio
        return self.ensaio

    def observar(self, *_):
        pass


class BandejaFalsa:
    def __init__(self):
        self.estados, self.acoes, self.leituras, self.avisos, self.atualizacoes, self.parou = [], {}, {}, [], 0, False

    def ligar(self, acoes, leituras):
        self.acoes.update(acoes)
        self.leituras.update(leituras)

    def iniciar(self):
        self.estados.append(("carregando", ""))

    def estado(self, nome, dica=""):
        if not self.estados or self.estados[-1] != (nome, dica):
            self.estados.append((nome, dica))

    def avisar(self, texto):
        self.avisos.append(texto)

    def atualizar(self):
        self.atualizacoes += 1

    def parar(self):
        self.parou = True

    @property
    def ultimo(self):
        return self.estados[-1][0]


class ConsoleFalso:
    def __init__(self, visivel=False, existe=True):
        self.visivel, self.existe, self.janela, self.real, self.feito = visivel, existe, 4242, True, []

    def mostrar(self):
        self.feito.append("mostrar")
        self.visivel = True
        return self.existe

    def esconder(self):
        self.feito.append("esconder")
        self.visivel = False
        return self.existe

    def alternar(self):
        if self.visivel:
            self.esconder()
            return False
        return self.mostrar()

    def titulo(self, texto):
        self.feito.append(("titulo", texto))

    def sem_fechar(self):
        self.feito.append("sem_fechar")

    def sem_selecao(self):
        self.feito.append("sem_selecao")


class PainelQueAnota:
    endereco = "http://127.0.0.1:1/"

    def __init__(self):
        self.avisos = []

    def __getattr__(self, nome):
        return lambda *dados: self.avisos.append((nome, *dados))

    def fases(self):
        return [a[1] for a in self.avisos if a[0] == "fase"]


class JanelasFalsas:
    def __init__(self):
        self.feito = []

    def definir_terminal(self, hwnd):
        self.feito.append(("terminal", hwnd))

    def trazer_terminal(self):
        self.feito.append("frente")
        return True

    def fim_do_turno(self, devolver_foco=True):
        self.feito.append(("fim", devolver_foco))

    def fechar_painel(self):
        return 0


class GuardaFalsa:
    contaminado_por = None

    def __init__(self):
        self.turnos = 0

    def novo_turno(self):
        self.turnos += 1


class SessaoFalsa:
    def __init__(self, janelas=None, protheus=None):
        self.janelas, self.guarda, self.opcoes, self.avisos = janelas, GuardaFalsa(), object(), []
        self.protheus = protheus


def fim():
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s", total_cost_usd=0.01)


class Cliente:
    """No lugar do ClaudeSDKClient: responde "Feito." e deixa o teste agir no meio do turno."""

    def __init__(self, durante=None):
        self.perguntas, self.durante, self.interrupcoes = [], durante, 0

    async def interrupt(self):
        self.interrupcoes += 1

    def __call__(self, options=None):  # a conversa cria o cliente com as opções da sessão
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def query(self, texto):
        self.perguntas.append(texto)

    async def receive_response(self):
        if self.durante is not None:
            await self.durante(self.perguntas[-1])
        yield AssistantMessage(content=[TextBlock(text="Feito.")], model="m")
        yield fim()


@pytest.fixture
def bipes(monkeypatch):
    tocados = []
    monkeypatch.setattr(cli, "_bipe", tocados.append)
    return tocados


@pytest.fixture
def teclado(monkeypatch):
    """O que o usuário digita: uma fila que o teste alimenta. Uma exceção na fila é levantada na leitura."""
    filas = {}

    async def ler(_prompt):
        fila = filas.setdefault(asyncio.get_running_loop(), asyncio.Queue())
        valor = await fila.get()
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr(cli, "ler_linha", ler)
    return lambda valor: filas.setdefault(asyncio.get_running_loop(), asyncio.Queue()).put_nowait(valor)


def cfg(tmp_path, **extra):
    # Sem o cumprimento do dia (bom_dia=None): ele tem testes próprios; aqui atrapalharia a contagem de turnos.
    return Config(**{**dict(modelo="sonnet", usuario=None, pastas=(tmp_path,), orcamento_usd=None, painel=False, atalho="nao", terminal=False, bom_dia=None), **extra})


class ClienteDemorado(Cliente):
    """Cada resposta só termina quando o teste solta (`soltar.set()`) ou quando o turno é interrompido."""

    def __init__(self, surdo_a=0):
        super().__init__()
        self.soltar, self.interrompido, self.surdo_a = asyncio.Event(), asyncio.Event(), surdo_a

    async def interrupt(self):
        self.interrupcoes += 1
        if self.interrupcoes > self.surdo_a:  # as primeiras tentativas podem se perder
            self.interrompido.set()

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="Vou começar.")], model="m")
        soltou, cortou = asyncio.ensure_future(self.soltar.wait()), asyncio.ensure_future(self.interrompido.wait())
        await asyncio.wait({soltou, cortou}, return_when=asyncio.FIRST_COMPLETED)
        for espera in (soltou, cortou):
            espera.cancel()
        if self.interrompido.is_set():
            self.interrompido.clear()
            # Como o binário de verdade faz: a ferramenta em andamento volta "recusada", em inglês, e o turno acaba com erro.
            yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__jarvis__terminal_executar", input={"comando": "x"})], model="m")
            yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="The user doesn't want to proceed with this tool use.", is_error=True)])
            yield ResultMessage(
                subtype="error_during_execution", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="s", total_cost_usd=0.01
            )
            return
        self.soltar.clear()
        yield AssistantMessage(content=[TextBlock(text="Feito.")], model="m")
        yield fim()


async def ate(condicao, tempo=3.0):
    """Espera a conversa chegar a um ponto (sem dormir tempo fixo)."""
    prazo = asyncio.get_running_loop().time() + tempo
    while not condicao():
        if asyncio.get_running_loop().time() > prazo:
            raise AssertionError("a conversa não chegou ao ponto esperado")
        await asyncio.sleep(0.005)


# --- esperar a entrada --------------------------------------------------------------------------------


def test_esperar_entrada_devolve_o_pedido_falado(teclado):
    async def cena():
        entrada = cli.Entrada()
        asyncio.get_running_loop().call_later(0.01, entrada.por, "voz", "abre o YouTube")
        return await cli.esperar_entrada("> ", entrada=entrada), len(entrada)

    assert asyncio.run(cena()) == (("voz", "abre o YouTube"), 0)


def test_pedido_falado_que_chega_junto_com_a_linha_fica_para_a_proxima(teclado):
    async def cena():
        entrada = cli.Entrada()
        entrada.por("voz", "que horas são")
        teclado("digitado")
        primeiro = await cli.esperar_entrada("> ", entrada=entrada)
        segundo = await cli.esperar_entrada("> ", entrada=entrada)
        return primeiro, segundo

    assert asyncio.run(cena()) == (("linha", "digitado"), ("voz", "que horas são"))


def test_o_atalho_passa_na_frente_do_pedido_falado_e_nada_se_perde(teclado):
    async def cena():
        entrada, tecla = cli.Entrada(), asyncio.Event()
        entrada.por("voz", "pausa o vídeo")
        tecla.set()
        return [await cli.esperar_entrada("> ", tecla, entrada=entrada) for _ in range(2)]

    assert asyncio.run(cena()) == [("atalho", ""), ("voz", "pausa o vídeo")]


def test_pedidos_da_voz_e_do_painel_saem_na_ordem_em_que_chegaram(teclado):
    """ "Abre a planilha" e depois "apaga a primeira linha dela": trocar a ordem seria fazer outra coisa."""

    async def cena():
        entrada = cli.Entrada()
        entrada.por("painel", "abre a planilha")
        primeiro = await cli.esperar_entrada("> ", entrada=entrada)
        for origem, texto in (("painel", "P1: abre o arquivo"), ("voz", "V1: que horas são"), ("painel", "P2: apaga a primeira linha dele"), ("voz", "V2: fecha")):
            entrada.por(origem, texto)
        teclado("no terminal")  # chega junto com os quatro: passa na frente, e nenhum deles muda de lugar
        return primeiro, [await cli.esperar_entrada("> ", entrada=entrada) for _ in range(5)], entrada.tirar()

    primeiro, depois, sobra = asyncio.run(cena())
    assert primeiro == ("painel", "abre a planilha") and sobra is None
    assert depois == [
        ("linha", "no terminal"),
        ("painel", "P1: abre o arquivo"),
        ("voz", "V1: que horas são"),
        ("painel", "P2: apaga a primeira linha dele"),
        ("voz", "V2: fecha"),
    ]


# --- a ponte entre a vigília e a conversa ---------------------------------------------------------------


def test_chamado_liga_a_vigilia_e_ela_ouve_o_tempo_todo(bipes):
    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis", "jarbas"), bandeja=bandeja)
        assert not chamado.ligado
        chamado.ligar()
        # Ouve desde já, e segue ouvindo com a conversa ocupada: é assim que "Jarvis, para" funciona.
        assert chamado.ligado and voz.vigia.palavras == ("jarvis", "jarbas") and voz.vigia.atenta is True
        assert bandeja.ultimo == "ocupado"
        chamado.atender(True)
        assert voz.vigia.atenta is True and bandeja.estados[-1] == ("pronto", 'Jarvis: diga "Jarvis" para chamar')
        chamado.atender(False)
        assert voz.vigia.atenta is True and bandeja.ultimo == "ocupado"
        assert voz.avisos["proprias"] == cli.RESPOSTAS_AO_NOME  # a vigília sabe o que ele responde, para não tomar por pedido
        # O pedido sai da thread do microfone e chega à fila da conversa.
        threading.Thread(target=voz.avisos["ao_pedir"], args=("abre o site",)).start()
        await ate(lambda: len(chamado.pedidos) == 1)
        assert chamado.pedidos.tirar() == ("voz", "abre o site")
        await ate(lambda: bipes == [660])  # entendi

    asyncio.run(cena())


def test_durante_uma_confirmacao_no_teclado_o_microfone_fica_surdo(bipes):
    async def cena():
        voz = VozFalsa()
        chamado = cli.Chamado(voz, ("jarvis",))
        chamado.ouvir(False)  # a pergunta apareceu antes de o microfone abrir
        chamado.ligar()
        assert voz.vigia.atenta is False
        chamado.ouvir(False)  # duas perguntas ao mesmo tempo
        chamado.ouvir(True)
        assert voz.vigia.atenta is False  # ainda falta responder uma
        chamado.ouvir(True)
        assert voz.vigia.atenta is True
        chamado.ouvir(True)  # de sobra: não desanda a conta
        chamado.ouvir(False)
        assert voz.vigia.atenta is False

    asyncio.run(cena())


def test_ouviu_o_nome_cala_na_hora_responde_e_espera_o_pedido(bipes, capsys):
    async def cena():
        voz, bandeja, painel = VozFalsa(), BandejaFalsa(), PainelQueAnota()
        chamado = cli.Chamado(voz, ("jarvis",), painel, bandeja)
        chamado.ligar()
        chamado.atender(True)
        voz.falando = True
        voz.avisos["ao_acordar"]()  # vem da thread do microfone
        assert voz.eventos == ["calou"]  # calou ali mesmo, sem esperar a vez no laço da conversa
        await ate(lambda: bandeja.ultimo == "ouvindo")
        assert painel.fases() == ["ouvindo"] and bipes == []
        voz.avisos["ao_atender"]()
        assert voz.dito == [cli.RESPOSTAS_AO_NOME[0]]  # a resposta falada, em vez de um bipe
        for _ in range(4):
            voz.avisos["ao_atender"]()
        assert voz.dito == list(cli.RESPOSTAS_AO_NOME[:4]) + [cli.RESPOSTAS_AO_NOME[4]]  # varia, para não soar gravado
        voz.avisos["ao_nivel"](0.4)
        voz.avisos["ao_nivel"](0.9)  # logo em seguida: o painel não precisa de tanto
        assert [a for a in painel.avisos if a[0] == "nivel"] == [("nivel", 0.4)]
        voz.avisos["ao_desistir"]()
        await ate(lambda: bipes == [440])
        assert bandeja.ultimo == "pronto" and painel.fases() == ["ouvindo", None]

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "ouvindo o seu pedido" in saida and "não ouvi o pedido" in saida


def test_voz_por_cima_da_dele_pausa_a_fala_e_o_engano_a_devolve(bipes):
    async def cena():
        voz = VozFalsa()
        chamado = cli.Chamado(voz, ("jarvis",))
        chamado.ligar()
        voz.avisos["ao_sobrepor"]()
        voz.avisos["ao_engano"]()
        assert voz.eventos == ["pausou", "retomou"]

    asyncio.run(cena())


def test_para_so_e_ordem_de_parar_quando_ha_o_que_parar(bipes, capsys, monkeypatch):
    async def cena():
        voz = VozFalsa()
        chamado = cli.Chamado(voz, ("jarvis",))
        chamado.ligar()
        imediato = voz.avisos["imediato"]
        # Calado e sem pedido em andamento, "Jarvis, para" é um pedido como outro qualquer (parar a música, por exemplo).
        assert imediato("para") is False
        voz.avisos["ao_pedir"]("para")
        await ate(lambda: len(chamado.pedidos) == 1)
        assert chamado.pedidos.tirar() == ("voz", "para")
        # Falando: é para ele se calar, e não vai para a conversa.
        voz.falando = True
        assert imediato("Para!") is True and imediato("para o vídeo") is False
        voz.avisos["ao_pedir"]("Chega.")
        await ate(lambda: "(parei)" in capsys.readouterr().out)
        assert voz.eventos[-1] == "calou" and len(chamado.pedidos) == 0
        # Disse só o nome (ele se calou), ouviu o "Sim?" e então disse "para": continua sendo só parar.
        voz.falando = True
        voz.avisos["ao_acordar"]()
        assert voz.falando is False and imediato("pare") is True
        voz.avisos["ao_pedir"]("pare")
        await asyncio.sleep(0.05)
        assert len(chamado.pedidos) == 0 and not chamado.andamento.parada.is_set()
        # Passado um tempo, volta a ser um pedido.
        monkeypatch.setattr(cli, "JANELA_DA_PARADA", 0.0)
        assert imediato("pare") is False
        # Ele acabou de falar sozinho, um instante atrás: o "para" dito no fim da frase dele ainda era para ela…
        voz.acabou_ha = 1.0
        assert imediato("para") is True
        voz.avisos["ao_pedir"]("para")
        await asyncio.sleep(0.05)
        assert len(chamado.pedidos) == 0
        # …mas alguns segundos depois já é outro assunto ("toquei a música" … "Jarvis, para": é a música).
        voz.acabou_ha = 4.0
        assert imediato("para") is False

    asyncio.run(cena())


@pytest.mark.parametrize(
    "dito",
    [
        "para", "Pare!", "Chega.", "Silêncio", "pode parar", "Para de falar.", "cancela", "já chega", "para tudo", "por favor, para",
        "Pode parar de falar, por favor.", "para com isso agora", "ok, para", "para aí", "cancela tudo", "fica quieto", "cala a boca", "então para",
    ],
)  # fmt: skip
def test_palavras_que_mandam_parar(dito):
    assert cli.eh_parada(dito)


@pytest.mark.parametrize(
    "dito",
    [
        "para o vídeo", "que horas são", "para a Maria, manda um oi", "", "parabéns", "chega mais perto", "cancela a reunião de amanhã",
        "pode falar", "isso", "por favor", "agora", "para de tocar a música", "para tudo que você está fazendo e abre o site", "fica",
        "para para para para para para para",
    ],
)  # fmt: skip
def test_o_que_nao_e_ordem_de_parar(dito):
    assert not cli.eh_parada(dito)


def test_microfone_desligado_no_icone(bipes):
    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.alternar_microfone()  # sem vigília ainda: nada a fazer
        chamado.ligar()
        chamado.atender(True)
        await asyncio.to_thread(chamado.alternar_microfone)  # o clique vem da thread do ícone
        await ate(lambda: bandeja.ultimo == "surdo")
        assert voz.vigia.eventos == ["pausar"] and not chamado.ligado
        await asyncio.to_thread(chamado.alternar_microfone)
        await ate(lambda: bandeja.ultimo == "pronto")
        assert voz.vigia.eventos == ["pausar", "continuar"] and chamado.ligado

    asyncio.run(cena())


def test_sem_microfone_na_subida_ele_segue_tentando(bipes, capsys):
    async def cena():
        voz, bandeja = VozFalsa(sem_microfone=3), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        with pytest.raises(ErroVoz):
            chamado.ligar()
        chamado.atender(True)
        assert bandeja.estados[-1] == ("surdo", "Jarvis: sem microfone para a palavra de ativação")
        chamado.ouvir(False)  # uma confirmação no teclado em curso quando o microfone aparece
        zelo = asyncio.ensure_future(chamado.zelar(intervalo=0.001))
        await ate(lambda: voz.vigia is not None)
        assert chamado.ligado and voz.vigia.atenta is False  # o microfone novo nasce surdo, como estava combinado
        chamado.ouvir(True)
        assert voz.vigia.atenta is True
        await ate(lambda: bandeja.ultimo == "pronto")
        zelo.cancel()

    asyncio.run(cena())
    assert "Microfone encontrado" in capsys.readouterr().out


def test_o_zelo_reabre_o_microfone_que_caiu_e_o_icone_acompanha(bipes):
    class TelaBloqueada:
        agora = False

        def __call__(self):
            return self.agora

    async def cena():
        voz, bandeja, tela = VozFalsa(), BandejaFalsa(), TelaBloqueada()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja, bloqueio=tela)
        chamado.ligar()
        chamado.atender(True)
        zelo = asyncio.ensure_future(chamado.zelar(intervalo=0.001))
        await ate(lambda: "conferir" in voz.vigia.eventos)  # de tempos em tempos confere se o microfone segue vivo
        assert bandeja.ultimo == "pronto"
        voz.vigia.aberta = False  # o cabo saiu
        await ate(lambda: bandeja.estados[-1] == ("surdo", "Jarvis: sem microfone para a palavra de ativação"))
        assert chamado.ligado  # o usuário não desligou nada: o item do menu segue marcado
        voz.vigia.suspensa = True  # fechado porque o atalho está gravando não é microfone que caiu
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.vigia.aberta, voz.vigia.suspensa = True, False
        tela.agora = True  # Win+L
        await ate(lambda: bandeja.estados[-1] == ("surdo", "Jarvis: tela bloqueada; não atendo pelo nome"))
        tela.agora = False
        await ate(lambda: bandeja.ultimo == "pronto")
        # O zelo só refaz o estado de espera. "Ouvindo o pedido" e "precisa de confirmação" não são dele para apagar.
        voz.vigia.esperando_pedido = True
        bandeja.estado("ouvindo")
        await asyncio.sleep(0.02)
        assert bandeja.ultimo == "ouvindo"
        voz.vigia.esperando_pedido = False
        chamado.atender(False)
        bandeja.estado("atencao")
        await asyncio.sleep(0.02)
        assert bandeja.ultimo == "atencao"
        voz.vigia.conferir_microfone = lambda: 1 / 0  # uma falha no zelo não derruba nada
        await asyncio.sleep(0.01)
        assert not zelo.done()
        zelo.cancel()

    asyncio.run(cena())


def test_comando_ouvido(capsys):
    cli.mostrar_ouvido(None)
    assert "desligada nesta sessão" in capsys.readouterr().out
    voz = VozFalsa()
    voz.vigiar(("jarvis",), lambda t: None)
    cli.mostrar_ouvido(voz)
    assert "aberto, esperando: jarvis" in capsys.readouterr().out
    voz.vigia.ouvidos += ["Jarbas, abre o site", "vamos almoçar"]
    voz.vigia.pausada, voz.vigia.erro = True, "dispositivo desconectado"
    cli.mostrar_ouvido(voz)
    saida = capsys.readouterr().out
    assert "desligado por você" in saida and "· Jarbas, abre o site" in saida and "dispositivo desconectado" in saida
    assert "JARVIS_PALAVRA" in saida  # a dica de como ensinar a grafia nova
    voz.vigia.pausada, voz.vigia.aberta = False, False
    cli.mostrar_ouvido(voz)
    saida = capsys.readouterr().out
    assert "Microfone fechado" in saida and "transcrição tem levado" not in saida
    voz.vigia.tempos += [0.4, 0.8]
    cli.mostrar_ouvido(voz)
    saida = capsys.readouterr().out
    assert "Cada transcrição tem levado 0.6 s" in saida and "JARVIS_WHISPER_ATIVACAO" not in saida
    voz.vigia.tempos += [4.0, 4.0]
    cli.mostrar_ouvido(voz)
    saida = capsys.readouterr().out
    assert "a mais demorada das últimas, 4.0 s" in saida and "JARVIS_WHISPER_ATIVACAO=base" in saida  # lenta: a saída é um modelo menor

    voz.vigia.calou_para_ouvir, voz.vigia.enganos = 3, 1
    cli.mostrar_ouvido(voz)
    saida = capsys.readouterr().out
    assert "Calou-se para ouvir uma voz por cima da dele 3 vez(es); em 1 não era com ele" in saida and "controle automático de ganho" not in saida
    voz.vigia.enganos = 2
    cli.mostrar_ouvido(voz)
    assert "controle automático de ganho" in capsys.readouterr().out  # vários enganos: a dica de que é a própria voz dele

    class Chamado:
        tela_bloqueada = True

    cli.mostrar_ouvido(voz, Chamado())
    saida = capsys.readouterr().out
    assert "tela está bloqueada" in saida and "JARVIS_OUVIR_BLOQUEADO=sim" in saida  # a saída, se a consulta errar nesta máquina


# --- a pergunta que exige o teclado ----------------------------------------------------------------------


def test_pergunta_traz_o_terminal_escondido_e_depois_ele_some():
    fundo, janelas = cli.Fundo(ConsoleFalso(visivel=False), BandejaFalsa()), JanelasFalsas()
    with cli.pergunta_a_vista(fundo, janelas):
        assert fundo.console.visivel and janelas.feito == ["frente"] and fundo.bandeja.ultimo == "atencao"
    assert not fundo.console.visivel and fundo.console.feito == ["mostrar", "esconder"] and fundo.bandeja.ultimo == "ocupado"


def test_pergunta_com_o_terminal_ja_a_vista_ou_sem_segundo_plano_nao_mexe_em_nada():
    fundo = cli.Fundo(ConsoleFalso(visivel=True), BandejaFalsa())
    with cli.pergunta_a_vista(fundo, JanelasFalsas()):
        pass
    assert fundo.console.feito == [] and fundo.bandeja.estados == []  # o usuário abriu; fica aberto
    with cli.pergunta_a_vista(None):
        pass
    sem_terminal = cli.Fundo(ConsoleFalso(existe=False), BandejaFalsa())
    with cli.pergunta_a_vista(sem_terminal):
        pass
    assert sem_terminal.console.feito == []


def test_pergunta_esconde_de_novo_mesmo_se_a_resposta_falhar_e_aguenta_janela_teimosa():
    class Teimosa(JanelasFalsas):
        def trazer_terminal(self):
            raise OSError("o Windows não deixou")

    fundo = cli.Fundo(ConsoleFalso(visivel=False), BandejaFalsa())
    with pytest.raises(RuntimeError), cli.pergunta_a_vista(fundo, Teimosa()):
        assert fundo.console.visivel
        raise RuntimeError("resposta interrompida")
    assert not fundo.console.visivel


# --- o laço da conversa ------------------------------------------------------------------------------------


def _laco(tmp_path, monkeypatch, cliente, voz=None, chamado=None, fundo=None, janelas=None, digitados=None, andamento=None):
    monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
    sessao = SessaoFalsa(janelas)
    registro = Registro(":memory:")
    return sessao, registro, cli._laco_da_conversa(
        cfg(tmp_path), Memoria(tmp_path / "m"), registro, voz, sessao, None, MUDO, None, None, None, chamado, fundo, digitados, andamento
    )


def test_pedido_pelo_nome_vira_turno_e_o_microfone_segue_ouvindo(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        atencao = []

        async def durante(_pergunta):
            atencao.append((voz.vigia.atenta, bandeja.ultimo, chamado.andamento.em_turno))

        cliente = Cliente(durante)
        janelas = JanelasFalsas()
        sessao, registro, laco = _laco(tmp_path, monkeypatch, cliente, voz, chamado, cli.Fundo(ConsoleFalso(visivel=True), bandeja), janelas)
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("abre o YouTube")
        await ate(lambda: len(cliente.perguntas) == 1 and bandeja.ultimo == "pronto")
        # Durante o turno o ícone mostra "ocupado", mas o microfone segue ouvindo.
        assert cliente.perguntas == ["abre o YouTube"] and atencao == [(True, "ocupado", True)]
        assert bipes == [660] and sessao.guarda.turnos == 1 and not chamado.andamento.em_turno
        assert janelas.feito == [("fim", False)]  # pedido falado não puxa o foco para o terminal
        assert registro.ultimos(1)[0]["modo"] == "voz"
        voz.avisos["ao_pedir"]("sair")  # um "sair" entendido errado não desliga o Jarvis: vai para a conversa
        await ate(lambda: len(cliente.perguntas) == 2 and bandeja.ultimo == "pronto")
        assert cliente.perguntas[1] == "sair" and not conversa.done()
        teclado("sair")  # digitado, sim
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "você (voz) > abre o YouTube" in saida and "jarvis > Feito." in saida


def test_jarvis_para_no_meio_do_pedido_larga_o_que_ele_fazia(tmp_path, monkeypatch, teclado, bipes, capsys):
    encerrados = []
    monkeypatch.setattr(cli, "encerrar_comandos", lambda: encerrados.append(1))
    monkeypatch.setattr(cli, "INSISTIR_NA_PARADA", 0.02)

    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        cliente = ClienteDemorado(surdo_a=2)  # os dois primeiros pedidos de interrupção se perdem
        fundo = cli.Fundo(ConsoleFalso(visivel=False), bandeja)
        _, registro, laco = _laco(tmp_path, monkeypatch, cliente, voz, chamado, fundo)
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("organiza a pasta de downloads")
        await ate(lambda: chamado.andamento.em_turno and "Vou começar." in voz.dito)
        voz.falando = True
        assert voz.avisos["imediato"]("para") is True  # no meio de um pedido, "para" não espera o fim da frase
        voz.avisos["ao_acordar"]()
        assert voz.calada and voz.falando is False  # calado na hora, e o resto deste pedido não é narrado
        voz.avisos["ao_pedir"]("Para.")
        await ate(lambda: not chamado.andamento.em_turno)
        assert cliente.interrupcoes == 3 and encerrados == [1]  # insistiu até pegar, e o comando em andamento foi encerrado
        assert cliente.perguntas == ["organiza a pasta de downloads"]  # "para" não virou pergunta
        ultimo = registro.ultimos(1)[0]
        assert ultimo["erro"] == "interrompido a pedido" and ultimo["resposta"] == "Vou começar."
        assert bandeja.avisos == [] and "Não consegui responder agora. O motivo está no terminal." not in voz.dito  # parar não é erro
        assert voz.calada is False and bipes == [660]  # só o bipe do primeiro pedido; o "para" não toca nada
        # E a conversa segue: o pedido seguinte é atendido normalmente.
        voz.avisos["ao_pedir"]("que horas são")
        await ate(lambda: len(cliente.perguntas) == 2)
        cliente.soltar.set()
        await ate(lambda: bandeja.ultimo == "pronto" and not chamado.andamento.em_turno)
        assert registro.ultimos(1)[0]["resposta"] == "Vou começar.\nFeito." and registro.ultimos(1)[0]["erro"] is None
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "(parando, a seu pedido…)" in saida and "(parei)" in saida and "[erro]" not in saida
    assert "não executou" not in saida and "doesn't want" not in saida  # o que o binário diz da ferramenta cortada não interessa


def test_pedido_novo_no_meio_de_outro_espera_a_vez_e_cala_a_narracao(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        voz, bandeja, painel = VozFalsa(), BandejaFalsa(), PainelQueAnota()
        chamado = cli.Chamado(voz, ("jarvis",), painel, bandeja)
        chamado.ligar()
        cliente = ClienteDemorado()
        _, _, laco = _laco(tmp_path, monkeypatch, cliente, voz, chamado, cli.Fundo(ConsoleFalso(), bandeja))
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("organiza a pasta de downloads")
        await ate(lambda: chamado.andamento.em_turno and "Vou começar." in voz.dito)
        voz.avisos["ao_acordar"]()
        voz.avisos["ao_pedir"]("que horas são")  # não é ordem de parar: o primeiro pedido segue
        await ate(lambda: cli.ESPERE in voz.dito)
        assert voz.calada and cliente.interrupcoes == 0 and cliente.perguntas == ["organiza a pasta de downloads"]
        assert bipes == [660, 660]
        assert painel.fases()[-2:] == ["ouvindo", "pensando"] and bandeja.ultimo == "ocupado"  # ouviu, anotou, e voltou ao que fazia
        cliente.soltar.set()  # o primeiro termina…
        await ate(lambda: len(cliente.perguntas) == 2)  # …e o segundo começa em seguida
        assert "Feito." not in voz.dito  # o fim do primeiro não foi narrado: a pessoa já tinha passado adiante
        assert voz.calada is False
        cliente.soltar.set()
        await ate(lambda: voz.dito.count("Feito.") == 1 and bandeja.ultimo == "pronto")
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    assert "(anotado; faço em seguida: que horas são)" in capsys.readouterr().out


def test_para_dito_no_fim_de_um_pedido_nao_larga_o_pedido_seguinte(tmp_path, monkeypatch, teclado, bipes, capsys):
    """O "para" era para o que ele fazia naquela hora. Se aquilo acabou e outro pedido, que esperava a vez, começou, não é com ele."""
    monkeypatch.setattr(cli, "INSISTIR_NA_PARADA", 0.02)

    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        cliente = ClienteDemorado()
        _, registro, laco = _laco(tmp_path, monkeypatch, cliente, voz, chamado)
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("primeiro pedido")
        await ate(lambda: chamado.andamento.em_turno)
        voz.avisos["ao_pedir"]("segundo pedido")  # espera a vez
        await ate(lambda: len(chamado.pedidos) == 1)
        serie_do_primeiro = chamado.andamento.serie
        cliente.soltar.set()  # o primeiro termina…
        await ate(lambda: len(cliente.perguntas) == 2 and chamado.andamento.em_turno)  # …e o segundo começa
        chamado._chegou("para", True, serie_do_primeiro)  # o "para" dito no fim do primeiro só agora chega à conversa
        await asyncio.sleep(0.1)
        assert cliente.interrupcoes == 0 and chamado.andamento.em_turno and not chamado.andamento.parada.is_set()
        chamado._chegou("para", True, chamado.andamento.serie)  # já este é para o que está em andamento
        await ate(lambda: not chamado.andamento.em_turno)
        assert cliente.interrupcoes == 1 and registro.ultimos(1)[0]["erro"] == "interrompido a pedido"
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())


def test_fora_de_um_pedido_a_narracao_nunca_fica_calada(bipes):
    """Um chamado cala o resto do pedido em andamento, não o que vier depois (o aviso de um trabalho que terminou, por exemplo)."""

    async def cena():
        voz = VozFalsa()
        andamento = cli.Andamento(voz)
        andamento.calar()
        assert voz.eventos == ["calou"] and not voz.calada  # sem pedido em andamento, só corta a fala
        with andamento.turno():
            andamento.calar()
            assert voz.calada  # no meio de um pedido, cala também o que ele ainda for narrar
        assert not voz.calada  # acabou o pedido: volta a falar
        voz.calada = True  # mesmo que algo o tenha deixado calado,
        with andamento.turno():
            assert not voz.calada  # o pedido seguinte começa falando
        assert andamento.serie == 2 and cli.Andamento().parar() is False  # e sem voz nenhuma, parar não quebra

    asyncio.run(cena())


def test_chamado_que_nao_deu_em_pedido_devolve_a_narracao(bipes, capsys):
    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        with chamado.andamento.turno():
            voz.avisos["ao_acordar"]()  # o começo de uma fala pareceu um chamado
            assert voz.calada
            voz.avisos["ao_desistir"]()  # e não era
            await ate(lambda: bipes == [440])
            assert not voz.calada and bandeja.ultimo == "ocupado"

    asyncio.run(cena())
    assert "não ouvi o pedido" in capsys.readouterr().out


def test_para_com_ele_so_falando_cala_e_nao_vira_pergunta(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        cliente = Cliente()
        _, _, laco = _laco(tmp_path, monkeypatch, cliente, voz, chamado)
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.falando = True  # o pedido já acabou; ele ainda está lendo a resposta
        voz.avisos["ao_pedir"]("para")
        await asyncio.sleep(0.05)
        assert set(voz.eventos) == {"calou"} and voz.calada is False and cliente.perguntas == [] and bipes == []
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    assert "(parei)" in capsys.readouterr().out


# --- rotinas no laço da conversa ----------------------------------------------------------------------------


def test_lembrete_e_dito_sem_turno_e_a_tarefa_marcada_vira_turno(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        voz, bandeja, cliente = VozFalsa(), BandejaFalsa(), Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        sessao, registro, entrada = SessaoFalsa(), Registro(":memory:"), cli.Entrada()
        fundo = cli.Fundo(ConsoleFalso(visivel=True), bandeja)
        laco = cli._laco_da_conversa(
            cfg(tmp_path), Memoria(tmp_path / "m"), registro, voz, sessao, None, MUDO, None, None, None, None, fundo, None, None, entrada
        )
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.estados and bandeja.ultimo == "pronto")
        entrada.por("lembrete", "Lembrete: ligar para o dentista")
        await ate(lambda: bandeja.avisos == ["Lembrete: ligar para o dentista"])
        assert voz.dito == ["Lembrete: ligar para o dentista"] and cliente.perguntas == []  # dito, sem gastar um turno
        entrada.por("rotina", "[rotina todo dia às 08:00] abre o e-mail e me diz o que chegou")
        await ate(lambda: len(cliente.perguntas) == 1 and bandeja.ultimo == "pronto")
        assert cliente.perguntas == ["[rotina todo dia às 08:00] abre o e-mail e me diz o que chegou"]
        assert sessao.guarda.turnos == 1 and registro.ultimos(1)[0]["modo"] == "voz"
        entrada.por("rotina", "sair")  # uma tarefa chamada "sair" não desliga o Jarvis
        await ate(lambda: len(cliente.perguntas) == 2 and bandeja.ultimo == "pronto")
        assert not conversa.done()
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "[lembrete] Lembrete: ligar para o dentista" in saida and "você (rotina) > [rotina todo dia às 08:00]" in saida


def test_missao_terminada_e_contada_na_conversa(tmp_path, monkeypatch, teclado, bipes, capsys):
    from jarvis.missoes import CONCLUIDA, Missao, Missoes

    async def cena():
        voz, bandeja, cliente = VozFalsa(), BandejaFalsa(), Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        sessao, registro, entrada, aviso = SessaoFalsa(), Registro(":memory:"), cli.Entrada(), asyncio.Event()
        missoes = Missoes(cfg(tmp_path), Memoria(tmp_path / "m"), tmp_path / ".jarvis", [])
        terminadas = []
        fundo = cli.Fundo(ConsoleFalso(visivel=True), bandeja)
        laco = cli._laco_da_conversa(
            cfg(tmp_path), Memoria(tmp_path / "m"), registro, voz, sessao, None, MUDO, None, aviso, [], None, fundo, None, None, entrada, None, None,
            missoes, terminadas,
        )  # fmt: skip
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.estados and bandeja.ultimo == "pronto")
        missao = Missao(id="abc", pedido="pesquise os melhores notebooks e me diga qual compensa", inicio="2026-10-09T08:00:00", estado=CONCLUIDA, relatorio="O X compensa.")
        terminadas.append(missao)
        aviso.set()
        await ate(lambda: voz.dito and bandeja.ultimo == "pronto")
        assert voz.dito == ["Terminei a missão: pesquise os melhores notebooks e me diga qual compensa. O X compensa."] and cliente.perguntas == []
        assert terminadas == []
        teclado("/missoes")
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "[missão] Terminei a missão" in saida and "Ainda não há missões" in saida  # a de mentira não passou pelo registro


# --- o bom dia no laço da conversa ------------------------------------------------------------------------


def test_na_partida_o_bom_dia_vira_o_primeiro_turno_uma_vez_so(tmp_path, monkeypatch, teclado, capsys):
    from jarvis import bom_dia

    class SemBloqueio:
        disponivel = False

    monkeypatch.setattr(cli.segundo_plano, "Bloqueio", SemBloqueio)
    pedidos = []

    async def rodar(hora):
        cliente = Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        agora = datetime(2026, 10, 9, hora, 0)
        monkeypatch.setattr(bom_dia, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: agora)}))
        conversa = asyncio.ensure_future(
            cli.conversar(cfg(tmp_path, bom_dia=(5, 14), cidade="Curitiba", usuario="Bruno"), Memoria(tmp_path / "m"), Registro(":memory:"), None, None)
        )
        await asyncio.sleep(0.3)
        conversa.cancel()
        await asyncio.gather(conversa, return_exceptions=True)
        pedidos.append(list(cliente.perguntas))

    asyncio.run(rodar(8))
    asyncio.run(rodar(9))  # mesmo dia: já cumprimentou
    asyncio.run(rodar(20))  # fora da janela
    assert len(pedidos[0]) == 1 and pedidos[0][0].startswith("[bom dia] É sexta-feira, 09/10/2026, 08:00") and 'Diga "Bom dia" ao usuário, Bruno' in pedidos[0][0]
    assert "Curitiba" in pedidos[0][0] and pedidos[1] == [] and pedidos[2] == []
    assert json.loads((cli.PASTA_DADOS / "bom-dia.json").read_text()) == {"ultimo": "2026-10-09"}
    saida = capsys.readouterr().out
    assert "você (bom dia) > [bom dia] É sexta-feira" in saida and saida.count("você (bom dia)") == 1


def test_bomdia_digitado_e_desligado(tmp_path, monkeypatch, teclado, capsys):
    pedidos = []

    async def cena(bom_dia_da_sessao):
        cliente = Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        laco = cli._laco_da_conversa(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), None, SessaoFalsa(), None, MUDO, bom_dia=bom_dia_da_sessao)
        conversa = asyncio.ensure_future(laco)
        teclado("/bomdia")
        await asyncio.sleep(0.1)
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena(lambda: pedidos.append(1)))
    assert pedidos == [1]
    asyncio.run(cena(None))
    assert "O cumprimento do dia não está ligado nesta sessão." in capsys.readouterr().out


# --- pedidos digitados no painel --------------------------------------------------------------------------


def test_pedido_digitado_no_painel_vira_turno_mas_nao_vale_como_comando(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        voz = VozFalsa()
        digitados = cli.Digitados()
        cliente, janelas = Cliente(), JanelasFalsas()
        _, registro, laco = _laco(tmp_path, monkeypatch, cliente, voz, None, None, janelas, digitados)
        conversa = asyncio.ensure_future(laco)
        await asyncio.sleep(0.01)
        voz.calada = True  # um chamado calou a narração no último instante do pedido anterior
        assert await asyncio.to_thread(digitados.receber, "abre a planilha de custos") == "agora"  # vem da thread do painel
        await ate(lambda: len(cliente.perguntas) == 1 and "Feito." in voz.dito)  # pedido novo: a resposta é narrada
        assert voz.eventos == ["calou"]  # entrada nova cala a resposta anterior
        assert await asyncio.to_thread(digitados.receber, "sair") in ("agora", "fila")
        await ate(lambda: len(cliente.perguntas) == 2)
        assert cliente.perguntas == ["abre a planilha de custos", "sair"] and not conversa.done()  # "sair" pelo painel é conversa
        await ate(lambda: registro.total() == 2)
        assert registro.ultimos(1)[0]["modo"] == "texto"
        assert janelas.feito == [("fim", False), ("fim", False)]  # e o foco não é puxado para o terminal
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())
    assert "você (painel) > abre a planilha de custos" in capsys.readouterr().out


def test_pedidos_digitados_esperam_a_vez_e_ha_um_teto(tmp_path, monkeypatch, teclado, bipes):
    async def cena():
        andamento = cli.Andamento()
        digitados = cli.Digitados(andamento, maximo=2)  # dois esperando a vez, no máximo
        cliente = ClienteDemorado()
        _, registro, laco = _laco(tmp_path, monkeypatch, cliente, digitados=digitados, andamento=andamento)
        conversa = asyncio.ensure_future(laco)
        await asyncio.sleep(0.01)
        assert digitados.receber("primeiro") == "agora"
        await ate(lambda: andamento.em_turno)
        assert digitados.receber("segundo") == "fila" and digitados.receber("terceiro") == "fila"
        assert digitados.receber("quarto") == "cheio"  # dois já esperam: não cabe mais
        for feitos in (1, 2, 3):  # cada um termina e o seguinte começa, na ordem em que chegaram
            await ate(lambda feitos=feitos: len(cliente.perguntas) == feitos)
            cliente.soltar.set()
            await ate(lambda feitos=feitos: registro.total() == feitos)
        assert cliente.perguntas == ["primeiro", "segundo", "terceiro"]
        await ate(lambda: not andamento.em_turno and not digitados.ocupado)
        assert digitados.receber("quinto") == "agora"  # a fila esvaziou
        await ate(lambda: len(cliente.perguntas) == 4)
        cliente.soltar.set()
        await ate(lambda: registro.total() == 4)
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())


def test_para_digitado_no_painel_larga_o_pedido_em_andamento(tmp_path, monkeypatch, teclado, bipes, capsys):
    monkeypatch.setattr(cli, "encerrar_comandos", lambda: None)
    monkeypatch.setattr(cli, "INSISTIR_NA_PARADA", 0.02)

    async def cena(voz):
        andamento = cli.Andamento(voz)
        digitados = cli.Digitados(andamento)
        cliente = ClienteDemorado()
        _, registro, laco = _laco(tmp_path, monkeypatch, cliente, voz, digitados=digitados, andamento=andamento)
        conversa = asyncio.ensure_future(laco)
        await asyncio.sleep(0.01)
        assert digitados.receber("para") == "agora"  # sem nada em andamento, é um pedido como outro qualquer
        await ate(lambda: andamento.em_turno)
        assert await asyncio.to_thread(digitados.receber, "Para!") == "parou"
        await ate(lambda: not andamento.em_turno)
        assert cliente.interrupcoes == 1 and cliente.perguntas == ["para"]
        assert registro.ultimos(1)[0]["erro"] == "interrompido a pedido"
        if voz is not None:
            assert "calou até o próximo pedido" in voz.eventos  # a fala foi cortada junto
            voz.falando = True  # e, com ele só lendo uma resposta, "para" no painel cala
            assert await asyncio.to_thread(digitados.receber, "chega") == "parou"
            await ate(lambda: voz.eventos[-1] == "calou")
            assert cliente.perguntas == ["para"]
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena(None))  # sem voz: o painel é o único jeito de parar
    assert "(parei)" in capsys.readouterr().out
    asyncio.run(cena(VozFalsa()))


def test_painel_fechado_junto_com_a_conversa_nao_aceita_mais_pedidos():
    async def cena():
        return cli.Digitados()

    digitados = asyncio.run(cena())  # o laço da conversa já acabou
    assert digitados.receber("abre o site") == "cheio"
    assert digitados.receber("abre o site") == "cheio" and digitados._esperando == 0  # e a conta de quem espera não fica presa


def test_a_conversa_liga_a_caixa_de_texto_do_painel(tmp_path, monkeypatch, teclado, bipes, capsys):
    painel = PainelQueAnota()
    monkeypatch.setattr(cli, "ligar_painel", lambda *a, **k: painel)

    async def cena():
        cliente = Cliente()
        _, conversa = _conversa(tmp_path, monkeypatch, cliente, None, None)
        tarefa = asyncio.ensure_future(conversa)
        await ate(lambda: any(a[0] == "aceitar_pedidos" for a in painel.avisos))
        receber = next(a[1] for a in painel.avisos if a[0] == "aceitar_pedidos")
        assert await asyncio.to_thread(receber, "abre o site") == "agora"  # como o servidor do painel chamaria
        await ate(lambda: cliente.perguntas == ["abre o site"])
        teclado("sair")
        await asyncio.wait_for(tarefa, 3)

    asyncio.run(cena())
    assert "você (painel) > abre o site" in capsys.readouterr().out


# --- o ensaio da partida -------------------------------------------------------------------------------------


def test_ensaio_da_partida_aquece_a_transcricao_e_so_fala_se_ela_for_lenta(bipes, capsys):
    async def cena(ensaio):
        voz = VozFalsa(ensaio=ensaio)
        chamado = cli.Chamado(voz, ("jarvis",))
        chamado.ligar()
        await chamado.ensaiar()
        return voz.ensaios

    assert asyncio.run(cena((0.4, "Jarvis."))) == ["Jarvis"]
    assert capsys.readouterr().out == ""  # rápida: nada a dizer
    asyncio.run(cena((2.6, "Jarvis.")))
    saida = capsys.readouterr().out
    assert "leva 2.6 s" in saida and "JARVIS_WHISPER_ATIVACAO=base" in saida
    asyncio.run(cena(None))  # não deu para ensaiar
    asyncio.run(cena(RuntimeError("modelo caiu")))  # ou o ensaio falhou: a conversa segue
    assert capsys.readouterr().out == ""


def test_digitado_continua_valendo_e_o_foco_so_volta_para_terminal_a_vista(tmp_path, monkeypatch, teclado, bipes):
    async def cena(visivel):
        cliente, janelas = Cliente(), JanelasFalsas()
        fundo = cli.Fundo(ConsoleFalso(visivel=visivel), BandejaFalsa())
        _, _, laco = _laco(tmp_path, monkeypatch, cliente, fundo=fundo, janelas=janelas)
        teclado("que horas são")
        teclado(EOFError())
        await asyncio.wait_for(laco, 3)
        assert cliente.perguntas == ["que horas são"]
        assert fundo.bandeja.estados[0][0] == "pronto" and "ocupado" in [e[0] for e in fundo.bandeja.estados]
        return janelas.feito

    assert asyncio.run(cena(True)) == [("fim", True)]
    assert asyncio.run(cena(False)) == [("fim", False)]  # terminal escondido: o foco não vai para o nada


def test_comandos_ouvido_e_esconder(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena():
        cliente = Cliente()
        fundo = cli.Fundo(ConsoleFalso(visivel=True), BandejaFalsa())
        _, _, laco = _laco(tmp_path, monkeypatch, cliente, fundo=fundo)
        for linha in ("/ouvido", "/esconder", "/trabalhos", "sair"):
            teclado(linha)
        await asyncio.wait_for(laco, 3)
        assert cliente.perguntas == [] and fundo.console.feito == ["esconder"] and fundo.bandeja.atualizacoes == 1
        # Fora do segundo plano, /esconder explica em vez de esconder.
        _, _, laco = _laco(tmp_path, monkeypatch, cliente)
        teclado("/esconder")
        teclado("sair")
        await asyncio.wait_for(laco, 3)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "palavra de ativação está desligada" in saida and "Só no modo segundo plano" in saida


# --- a conversa inteira, em segundo plano ------------------------------------------------------------------


def _conversa(tmp_path, monkeypatch, cliente, voz, fundo, janelas=None, **extra):
    guardado = {}

    def montar(cfg_, memoria, registro, **pecas):
        guardado.update(pecas)
        guardado["sessao"] = SessaoFalsa(janelas)
        return guardado["sessao"]

    monkeypatch.setattr(cli, "montar_sessao", montar)
    monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
    monkeypatch.setattr(cli, "PASTA_DADOS", tmp_path / "dados")
    conversa = cli.conversar(cfg(tmp_path, **extra), Memoria(tmp_path / "m"), Registro(":memory:"), voz, fundo)
    return guardado, conversa


def test_com_painel_o_modelo_recebe_a_ferramenta_dos_cartoes_e_sem_painel_nao(tmp_path, monkeypatch, teclado, bipes):
    import dataclasses

    abertos = []
    monkeypatch.setattr(cli, "abrir_janela", lambda endereco: abertos.append(endereco) or "chrome")

    async def cena(com_painel):
        guardado = {}

        def montar(cfg_, memoria, registro, **pecas):
            guardado.update(pecas)
            return SessaoFalsa(None)

        monkeypatch.setattr(cli, "montar_sessao", montar)
        monkeypatch.setattr(cli, "ClaudeSDKClient", Cliente())
        config = dataclasses.replace(cfg(tmp_path), painel=com_painel)
        tarefa = asyncio.ensure_future(cli.conversar(config, Memoria(tmp_path / f"m{com_painel}"), Registro(":memory:"), None, None))
        await ate(lambda: "mostrar_paineis" in guardado)
        mostrar = guardado["mostrar_paineis"]
        if com_painel:
            await ate(lambda: len(abertos) == 1)  # o painel nasceu depois da sessão; a ferramenta o alcança quando é usada
            assert mostrar([{"titulo": "Maragogi", "destaque": "27°"}]) == "1 cartão no painel."
            assert len(abertos) == 2 and abertos[0] == abertos[1]  # ninguém com a página aberta: a janela é aberta para mostrar
        else:
            assert mostrar is None
        teclado("sair")
        await asyncio.wait_for(tarefa, 5)

    asyncio.run(cena(True))
    asyncio.run(cena(False))


def test_em_segundo_plano_o_icone_comanda_e_sair_encerra_tudo(tmp_path, monkeypatch, teclado, bipes, capsys):
    class Inicio:
        ligado_ = False

        def ligado(self):
            return self.ligado_

        def ligar(self):
            self.ligado_ = True

        def desligar(self):
            self.ligado_ = False

    forcadas = []
    monkeypatch.setattr(cli, "forcar_saida", lambda: forcadas.append(1))

    async def cena():
        voz, bandeja, console, janelas, inicio = VozFalsa(), BandejaFalsa(), ConsoleFalso(visivel=False), JanelasFalsas(), Inicio()
        fundo = cli.Fundo(console, bandeja, inicio)
        guardado, conversa = _conversa(tmp_path, monkeypatch, Cliente(), voz, fundo, janelas)
        tarefa = asyncio.ensure_future(conversa)
        await ate(lambda: voz.vigia is not None and bandeja.ultimo == "pronto")
        assert voz.prontas[: len(cli.RESPOSTAS_AO_NOME)] == list(cli.RESPOSTAS_AO_NOME)  # as respostas ao nome já ficam sintetizadas
        await ate(lambda: voz.ensaios == ["Jarvis"])  # e a transcrição, aquecida
        assert janelas.feito == [("terminal", 4242)]  # a janela do terminal é a que o segundo plano escondeu
        assert sorted(bandeja.acoes) == ["inicio", "microfone", "sair", "terminal"]  # painel desligado: sem o item
        assert bandeja.leituras["terminal"]() is False and bandeja.leituras["microfone"]() is True
        await asyncio.to_thread(bandeja.acoes["terminal"])  # um clique no ícone, de outra thread
        assert console.visivel and bandeja.leituras["terminal"]() is True
        await asyncio.to_thread(bandeja.acoes["inicio"])
        assert inicio.ligado_ and bandeja.leituras["inicio"]() is True
        await asyncio.to_thread(bandeja.acoes["inicio"])
        assert not inicio.ligado_
        await asyncio.to_thread(bandeja.acoes["microfone"])
        assert voz.vigia.pausada
        await asyncio.to_thread(bandeja.acoes["sair"])
        await asyncio.to_thread(bandeja.acoes["sair"])  # o segundo clique, por pressa, não atropela o encerramento
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(tarefa, 3)
        assert voz.eventos[-1] == "parar_vigilia" and voz.vigia is None
        assert forcadas == []
        monkeypatch.setattr(cli, "ESPERA_PARA_SAIR", 0.0)
        await asyncio.sleep(0.01)
        await asyncio.to_thread(bandeja.acoes["sair"])  # pedido de novo, tempos depois, e ele ainda aqui: sai à força
        assert forcadas == [1]

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert 'Palavra de ativação: diga "Jarvis"' in saida and "ícone perto do relógio" in saida and "/ouvido" in saida


def test_em_segundo_plano_a_confirmacao_traz_o_terminal_e_ele_some_depois(tmp_path, monkeypatch, teclado, bipes, capsys):
    monkeypatch.setattr(cli, "TEMPO_PARA_VER", 0.0)

    async def cena():
        voz, bandeja, console, janelas = VozFalsa(), BandejaFalsa(), ConsoleFalso(visivel=False), JanelasFalsas()
        guardado, respostas, visto = {}, [], []

        async def durante(_pergunta):
            # No meio do turno o Jarvis quer rodar um comando: a pergunta precisa aparecer para o usuário.
            antes = voz.vigia.atenta
            voz.calada = True  # alguém chamou no meio da resposta: mesmo assim o aviso da confirmação é falado
            pergunta = asyncio.ensure_future(guardado["confirmar_comando"]("rodar um comando no terminal", ["git status"]))
            await ate(lambda: console.visivel)
            visto.append((bandeja.ultimo, list(janelas.feito), antes, voz.vigia.atenta))
            teclado("s")
            respostas.append(await pergunta)

        guardado, conversa = _conversa(tmp_path, monkeypatch, Cliente(durante), voz, cli.Fundo(console, bandeja), janelas)
        tarefa = asyncio.ensure_future(conversa)
        await ate(lambda: voz.vigia is not None and bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("roda o git status")
        await ate(lambda: respostas and bandeja.ultimo == "pronto")
        assert respostas == [True]
        # Apareceu, veio para frente, o ícone pediu atenção; e enquanto a pergunta esperava o teclado o microfone ficou surdo.
        assert visto == [("atencao", [("terminal", 4242), "frente"], True, False)]
        assert voz.vigia.atenta is True  # respondida, volta a ouvir
        assert console.feito == ["mostrar", "esconder"] and not console.visivel  # respondido, some de novo
        assert "Preciso da sua confirmação no teclado." in voz.dito
        tarefa.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarefa

    asyncio.run(cena())
    assert "O Jarvis quer rodar um comando no terminal" in capsys.readouterr().out


def test_sem_microfone_a_conversa_segue_e_o_zelo_fica_tentando(tmp_path, monkeypatch, teclado, bipes, capsys):
    zelos = []

    async def zelar(self, *_):
        zelos.append(self)
        await asyncio.sleep(3600)

    monkeypatch.setattr(cli.Chamado, "zelar", zelar)

    async def cena():
        voz = VozFalsa(sem_microfone=1)
        _, conversa = _conversa(tmp_path, monkeypatch, Cliente(), voz, None)
        tarefa = asyncio.ensure_future(conversa)
        await ate(lambda: zelos)
        teclado("oi")  # sem a palavra de ativação, o teclado segue valendo
        teclado("sair")
        await asyncio.wait_for(tarefa, 3)

    asyncio.run(cena())
    assert len(zelos) == 1
    assert "Por enquanto sem a palavra de ativação" in capsys.readouterr().out


def test_erro_do_turno_nao_fica_so_no_terminal_escondido(tmp_path, monkeypatch, teclado, bipes, capsys):
    class ClienteComErro(Cliente):
        async def receive_response(self):
            yield AssistantMessage(content=[], model="m", error="rate_limit")
            yield fim()

    async def cena():
        voz, bandeja = VozFalsa(), BandejaFalsa()
        chamado = cli.Chamado(voz, ("jarvis",), bandeja=bandeja)
        chamado.ligar()
        _, _, laco = _laco(tmp_path, monkeypatch, ClienteComErro(), voz, chamado, cli.Fundo(ConsoleFalso(), bandeja))
        conversa = asyncio.ensure_future(laco)
        await ate(lambda: bandeja.ultimo == "pronto")
        voz.avisos["ao_pedir"]("que horas são")
        await ate(lambda: bandeja.avisos and voz.dito)
        assert bandeja.avisos[0].startswith("Não consegui responder: ")  # um aviso do Windows, com o motivo
        assert voz.dito == ["Não consegui responder agora. O motivo está no terminal."]  # e uma frase falada, fixa
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    asyncio.run(cena())


def test_palavra_desligada_ou_sem_voz_nao_abre_microfone(tmp_path, monkeypatch, teclado, bipes, capsys):
    async def cena(voz, **extra):
        _, conversa = _conversa(tmp_path, monkeypatch, Cliente(), voz, None, **extra)
        teclado("sair")
        await asyncio.wait_for(conversa, 3)

    voz = VozFalsa()
    asyncio.run(cena(voz, palavras=()))  # JARVIS_PALAVRA=nao
    assert voz.avisos == {} and voz.vigia is None
    asyncio.run(cena(None))
    assert "Palavra de ativação" not in capsys.readouterr().out


def test_a_conversa_entrega_o_bloqueio_de_tela_a_vigilia(tmp_path, monkeypatch, teclado, bipes, capsys):
    class TelaBloqueada:
        disponivel = True

        def __call__(self):
            return True

    monkeypatch.setattr(segundo_plano, "Bloqueio", TelaBloqueada)

    async def cena(**extra):
        voz = VozFalsa()
        _, conversa = _conversa(tmp_path, monkeypatch, Cliente(), voz, None, **extra)
        teclado("sair")
        await asyncio.wait_for(conversa, 3)
        return voz

    voz = asyncio.run(cena())
    assert isinstance(voz.impedido, TelaBloqueada) and voz.impedido() is True
    assert "Com a tela bloqueada (Win+L) ele não atende." in capsys.readouterr().out
    voz = asyncio.run(cena(ouvir_bloqueado=True))  # JARVIS_OUVIR_BLOQUEADO=sim: sem a consulta
    assert voz.impedido is None and "tela bloqueada" not in capsys.readouterr().out


def test_carregar_voz_insiste_na_subida_com_o_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda s: esperas.append(s))
    esperas, pedidos = [], []

    def carregar(voz, whisper, pasta, whisper_da_vigilia=""):
        pedidos.append((voz, whisper, whisper_da_vigilia))
        if len(pedidos) < 3:
            raise ErroVoz("Não consegui preparar a voz: dispositivo de som ainda não está pronto")
        return "voz"

    assert cli.carregar_voz(cfg(tmp_path, whisper_da_ativacao="base"), tentativas=4, carregar_modelos=carregar) == "voz"
    assert len(pedidos) == 3 and esperas == [10.0, 10.0] and pedidos[0][2] == "base"
    pedidos.clear()
    with pytest.raises(ErroVoz):  # no terminal, uma tentativa só: o erro aparece na hora
        cli.carregar_voz(cfg(tmp_path), carregar_modelos=carregar)

    def sem_biblioteca(*_, **__):
        pedidos.append("x")
        raise ErroVoz("Falta instalar as bibliotecas de voz (piper). Rode: pip install -r requirements-voz.txt")

    pedidos.clear()
    with pytest.raises(ErroVoz):  # biblioteca que falta não aparece esperando
        cli.carregar_voz(cfg(tmp_path), tentativas=4, carregar_modelos=sem_biblioteca)
    assert pedidos == ["x"]
    assert cli.carregar_voz(cfg(tmp_path, palavras=(), whisper_da_ativacao="base"), carregar_modelos=lambda *a, **k: k) == {"whisper_da_vigilia": ""}


# --- a partida -------------------------------------------------------------------------------------------


def test_preparar_fundo_da_nome_tira_o_x_e_sobe_o_icone(monkeypatch):
    class Icone(BandejaFalsa):
        def iniciar(self):
            self.estados.append(("carregando", ""))

    class Inicio:
        conferido = False

        def conferir(self):
            self.conferido = True

    console, bandeja, inicio = ConsoleFalso(), Icone(), Inicio()
    fundo = cli.preparar_fundo(console, bandeja, inicio)
    assert fundo.console is console and fundo.bandeja is bandeja and fundo.inicio is inicio
    assert console.feito == [("titulo", "Jarvis"), "sem_fechar", "sem_selecao"] and bandeja.ultimo == "carregando"


def test_sem_a_biblioteca_do_icone_o_erro_aparece_numa_caixa(monkeypatch):
    caixas = []
    monkeypatch.setattr(segundo_plano, "caixa", lambda texto, erro=True: caixas.append(texto))

    class SemBiblioteca(BandejaFalsa):
        def iniciar(self):
            raise ImportError("No module named 'pystray'", name="pystray")

    console = ConsoleFalso()
    assert cli.preparar_fundo(console, SemBiblioteca(), object()) is None
    assert "pystray" in caixas[0] and "pip install -r requirements.txt" in caixas[0]
    assert "mostrar" in console.feito  # e o terminal reaparece: sem ícone não haveria como chegar a ele


class InicioFalso:
    def __init__(self, erro=None):
        self.estado, self.erro = None, erro

    def ligar(self):
        if self.erro:
            raise self.erro
        self.estado = "ligado"

    def desligar(self):
        self.estado = "desligado"


def test_iniciar_com_windows_liga_e_ja_sobe(capsys):
    lancados, inicio = [], InicioFalso()
    assert cli.ajustar_inicio(True, "win32", inicio, lambda: lancados.append(1), lambda: False) == 0
    assert inicio.estado == "ligado" and lancados == [1]
    saida = capsys.readouterr().out
    assert "sobe junto com o Windows" in saida and "ícone aparece perto do relógio" in saida


def test_iniciar_com_windows_com_um_jarvis_ja_rodando_nao_sobe_outro(capsys):
    lancados, inicio = [], InicioFalso()
    assert cli.ajustar_inicio(True, "win32", inicio, lambda: lancados.append(1), lambda: True) == 0
    assert inicio.estado == "ligado" and lancados == []
    assert "Já há um Jarvis rodando" in capsys.readouterr().out


def test_desligar_o_inicio_e_os_casos_de_erro(capsys):
    inicio = InicioFalso()
    assert cli.ajustar_inicio(False, "win32", inicio, None, None) == 0 and inicio.estado == "desligado"
    assert "não sobe mais junto com o Windows" in capsys.readouterr().out
    assert cli.ajustar_inicio(True, "win32", InicioFalso(PermissionError("acesso negado")), None, None) == 1
    assert "não deixou mudar" in capsys.readouterr().err

    def sem_python():
        raise OSError("python.exe sumiu")

    assert cli.ajustar_inicio(True, "win32", InicioFalso(), sem_python, lambda: False) == 0  # ligou; só não subiu agora
    assert "iniciar.pyw" in capsys.readouterr().out
    assert cli.ajustar_inicio(True, "linux") == 2
    assert "só existe no Windows" in capsys.readouterr().err


def test_fora_do_windows_o_segundo_plano_explica_e_sai(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli.main(["--segundo-plano"]) == 2
    assert "só existe no Windows" in capsys.readouterr().err
    assert cli.main(["--iniciar-com-windows"]) == 2
    assert cli.main(["--nao-iniciar-com-windows"]) == 2


def test_segundo_jarvis_na_mesma_instalacao_nao_sobe(monkeypatch, capsys, tmp_path):
    class Ocupada:
        unica = False

    monkeypatch.setattr(cli, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(segundo_plano, "Instancia", Ocupada)
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **_: None)
    assert cli.main([]) == 1
    erro = capsys.readouterr().err
    assert "já está rodando" in erro and "ícone" in erro


def test_opcoes_nao_aceitam_abreviacao(capsys):
    """"--seg" valendo por "--segundo-plano" rodaria o segundo plano sem esconder o terminal."""
    with pytest.raises(SystemExit) as fim_:
        cli.analisador_de_argumentos().parse_args(["--seg"])
    assert fim_.value.code == 2
    opcoes = cli.analisador_de_argumentos().parse_args(["--segundo-plano", "--console=proprio", "--sem-painel"])
    assert opcoes.segundo_plano and opcoes.console == "proprio" and opcoes.sem_painel
    capsys.readouterr()


def test_saida_a_forca_encerra_antes_os_comandos_em_andamento(monkeypatch):
    feito = []
    monkeypatch.setattr(cli, "encerrar_comandos", lambda: feito.append("comandos"))
    monkeypatch.setattr(cli.os, "_exit", lambda codigo: feito.append(("saiu", codigo)))
    cli.forcar_saida()
    assert feito == ["comandos", ("saiu", 0)]
    assert cli.ESPERA_PARA_SAIR >= 8  # tempo de um encerramento normal (fechar trabalhos, git) antes de valer a força


def test_na_partida_a_missao_que_ficou_no_meio_e_avisada_uma_vez(tmp_path, monkeypatch, teclado, capsys):
    from datetime import datetime, timedelta

    class SemBloqueio:
        disponivel = False

    monkeypatch.setattr(cli.segundo_plano, "Bloqueio", SemBloqueio)
    ontem = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    velha = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    cli.PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    (cli.PASTA_DADOS / "missoes.json").write_text(
        json.dumps({"missoes": [
            {"id": "aaa", "pedido": "pesquise notebooks", "inicio": ontem, "estado": "rodando"},
            {"id": "bbb", "pedido": "pesquise carros", "inicio": velha, "estado": "rodando"},
        ]}),  # fmt: skip
        encoding="utf-8",
    )

    async def cena():
        cliente = Cliente()
        monkeypatch.setattr(cli, "ClaudeSDKClient", cliente)
        conversa = asyncio.ensure_future(cli.conversar(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), None, None))
        await asyncio.sleep(0.2)
        teclado("/missoes")
        teclado("sair")
        await asyncio.wait_for(conversa, 5)

    asyncio.run(cena())
    saida = capsys.readouterr().out
    assert "[aviso] A missão aaa (pesquise notebooks) foi interrompida quando o Jarvis fechou" in saida
    assert "A missão bbb" not in saida  # velha demais para valer a pena avisar
    assert "aaa · interrompida" in saida and "bbb · interrompida" in saida  # mas o /missoes lista as duas
