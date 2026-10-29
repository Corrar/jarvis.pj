"""Conversa no terminal: por texto (padrão) ou por voz (--voz)."""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent
from dotenv import load_dotenv

from jarvis import __version__, atalhos, ferramentas, inventario, segundo_plano, webapp
from jarvis.agente import ler_lista, montar_sessao
from jarvis.atalho import Atalho, ErroAtalho, ler_combinacao
from jarvis.bom_dia import Saudacao, vigiar_desbloqueio
from jarvis.clima import Clima
from jarvis.config import (
    conferir_env,
    ARQUIVO_REGISTRO,
    PASTA_DADOS,
    PASTA_MEMORIA,
    RAIZ_PROJETO,
    Config,
    ErroConfig,
    carregar,
)
from jarvis.erros import ERROS_DA_API
from jarvis.memoria import Memoria
from jarvis.missoes import RODANDO as MISSAO_RODANDO
from jarvis.missoes import Missao, Missoes
from jarvis.missoes import frase_do_fim as frase_do_fim_da_missao
from jarvis.painel import MUDO, Painel, abrir_janela, conhecimento
from jarvis.projetos import ler_projetos
from jarvis.protheus import MAX_CHAMADAS, Protheus
from jarvis.registro import Chamada, Registro, Turno
from jarvis.rotinas import ATRASO_DA_REPETIDA, ATRASO_MAXIMO, LEMBRETE, Agenda, Despertador, Rotina
from jarvis.segundo_plano import BandejaMuda
from jarvis.skills import Skills
from jarvis.terminal import Terminal, encerrar_comandos
from jarvis.trabalhos import CONCLUIDO, RODANDO, ROTULOS, Oficina, Trabalho
from jarvis.voz import ErroVoz, Voz
from jarvis.voz.dono import DURACAO_DO_CADASTRO, FRASES_DE_CADASTRO, ErroDono, VozDoDono
from jarvis.voz.escuta import TAXA
from jarvis.voz.vigilia import frase_simples

log = logging.getLogger(__name__)

SAIR = {"sair", "exit", "quit", "/sair"}

# Como ele responde quando ouve só o nome. Frases curtas e fixas: ficam prontas desde a partida e saem na hora.
RESPOSTAS_AO_NOME = (
    "Sim, mestre. Com o que eu posso te ajudar?",
    "À disposição, senhor. Qual é a minha missão do dia?",
    "Pois não, senhor. Em que posso ser útil?",
    "Estou aqui, mestre. O que vamos fazer?",
    "Às suas ordens, senhor. O que deseja?",
)  # JARVIS_RESPOSTAS no .env troca por outras
ESPERE = "Um instante."  # ouviu um pedido novo com outro em andamento: vai para a fila
PRECISO_DO_TECLADO = "Preciso da sua confirmação no teclado."
# O que, dito depois do nome, só manda parar: cala a fala e larga o que estiver fazendo, sem ir para a conversa.
PARADAS = frozenset({"para", "pare", "parar", "chega", "silencio", "quieto", "fica quieto", "cancela", "cancele", "cancelar", "cala a boca", "stop"})
# Palavras que podem vir junto sem mudar o sentido: "por favor, para", "pode parar de falar", "para com isso agora".
ENFEITES_DA_PARADA = frozenset({"por", "favor", "agora", "ja", "ok", "entao", "ai", "tudo", "isso", "com", "de", "falar", "pode", "so", "jarvis"})
JANELA_DA_PARADA = 8.0  # segundos depois de um chamado calar a fala em que um "para" ainda é só isso, e não um pedido novo
FIM_DA_FALA = 2.0  # segundos depois de a fala acabar sozinha em que um "para" ainda era para ela (o tempo de transcrever)
TRANSCRICAO_LENTA = 1.5  # segundos; se reconhecer um chamado leva mais que isso, vale sugerir um modelo menor
MAX_DIGITADOS = 5  # pedidos digitados no painel esperando a vez


def eh_parada(pedido: str) -> bool:
    """O pedido é só uma ordem de parar ("para", "chega", "por favor, para de falar")?

    Tem de ser a frase inteira: "para o vídeo" e "cancela a reunião" são pedidos.
    """
    return " ".join(p for p in frase_simples(pedido).split() if p not in ENFEITES_DA_PARADA) in PARADAS


class Entrada:
    """Os pedidos que chegam por fora do teclado (pela voz e pelo painel), na ordem em que chegaram.

    Quem espera é avisado sem que nada saia da fila: o pedido só é tirado na hora de ser atendido.
    Assim nenhum se perde nem troca de lugar quando chega junto com uma linha digitada. Só o laço
    da conversa mexe nela.
    """

    def __init__(self) -> None:
        self._itens: collections.deque[tuple[str, str]] = collections.deque()
        self._ha = asyncio.Event()

    def por(self, origem: str, texto: str) -> None:
        self._itens.append((origem, texto))
        self._ha.set()

    async def esperar(self) -> None:
        """Volta quando houver pelo menos um pedido (sem tirá-lo)."""
        await self._ha.wait()

    def tirar(self) -> tuple[str, str] | None:
        """O pedido mais antigo e de onde ele veio ("voz" ou "painel"); None se não há nenhum."""
        if not self._itens:
            return None
        item = self._itens.popleft()
        if not self._itens:
            self._ha.clear()
        return item

    def __len__(self) -> int:
        return len(self._itens)


_leitura: asyncio.Future[str] | None = None  # leitura do teclado em andamento, se houver


async def ler_linha(prompt: str) -> str:
    """input() sem travar o laço de eventos (o SDK precisa dele rodando).

    Só existe uma leitura por vez. Se quem pediu desistir de esperar (tempo
    esgotado numa confirmação), a leitura continua valendo e é entregue a quem
    pedir a próxima linha; assim nunca há duas threads disputando o teclado.

    A thread é daemon: se o programa for encerrado com Ctrl+C, ela não segura a
    saída esperando um Enter.
    """
    global _leitura
    laco = asyncio.get_running_loop()
    if _leitura is not None and _leitura.get_loop() is not laco:
        _leitura = None  # sobra de um laço anterior (acontece nos testes)

    if _leitura is None:
        futuro: asyncio.Future[str] = laco.create_future()
        _leitura = futuro

        def entregar(valor: str | None, erro: BaseException | None) -> None:
            if futuro.done():
                return
            if erro is not None:
                futuro.set_exception(erro)
            else:
                futuro.set_result(valor or "")

        def alvo() -> None:
            try:
                valor = input(prompt)
            except BaseException as erro:  # noqa: BLE001 - EOFError/KeyboardInterrupt vão para o laço
                resultado: tuple[str | None, BaseException | None] = (None, erro)
            else:
                resultado = (valor, None)
            try:
                laco.call_soon_threadsafe(entregar, *resultado)
            except RuntimeError:
                pass  # o laço já fechou

        threading.Thread(target=alvo, daemon=True, name="jarvis-entrada").start()
    else:
        print(prompt, end="", flush=True)  # já há uma leitura esperando; só mostra o novo pedido

    pendente = _leitura
    try:
        # shield: se quem chamou for cancelado, a leitura em si continua valendo.
        return await asyncio.shield(pendente)
    finally:
        if pendente.done() and _leitura is pendente:
            _leitura = None  # entregue; a próxima chamada começa uma leitura nova


def descrever(bloco: ToolUseBlock) -> str:
    """Uma linha dizendo o que o Jarvis está fazendo."""
    entrada = bloco.input or {}
    nome = bloco.name.removeprefix("mcp__jarvis__")
    if nome == "tela_clicar":
        return f"{nome}: ({entrada.get('x')}, {entrada.get('y')})"
    alvo = (
        entrada.get("file_path")
        or entrada.get("pattern")
        or entrada.get("query")
        or entrada.get("path")
        or entrada.get("url")
        or entrada.get("caminho")
        or entrada.get("titulo")
        or entrada.get("nome")
        or entrada.get("termo")
        or entrada.get("direcao")
        or entrada.get("comando")
        or entrada.get("tecla")
        or entrada.get("texto")
        or entrada.get("aba")
        or entrada.get("programa")
        or ""
    )
    alvo = " ".join(str(alvo).split())  # em uma linha só, mesmo que o comando tenha várias
    return f"{nome}: {alvo if len(alvo) <= 300 else alvo[:299] + '…'}" if alvo else nome


def texto_para_o_terminal(texto: str) -> str:
    """Troca por � tudo o que o terminal obedeceria em vez de mostrar: sequências de escape, apagar linha, inverter o texto."""
    return "".join(
        "\ufffd" if caractere not in "\n\t" and unicodedata.category(caractere) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp") else caractere
        for caractere in texto
    )


class SaidaLimpa:
    """O que o Jarvis escreve no terminal passa por aqui.

    A resposta do modelo, o nome de um arquivo, o relatório de um trabalhador e a saída de um
    comando são texto de fora. Se levassem uma sequência de escape até o terminal, poderiam
    esconder ou redesenhar a tela em que o usuário confere um comando antes de digitar s.
    """

    def __init__(self, fluxo: Any) -> None:
        self._fluxo = fluxo

    def write(self, texto: str) -> int:
        self._fluxo.write(texto_para_o_terminal(texto))
        return len(texto)

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._fluxo, nome)


def resumir_erro(conteudo: object, limite: int = 200) -> str:
    """Primeira linha do erro de uma ferramenta, sem as marcações do binário."""
    if isinstance(conteudo, list):
        conteudo = " ".join(str(b.get("text", "")) for b in conteudo if isinstance(b, dict))
    texto = re.sub(r"</?tool_use_error>", "", str(conteudo or "")).strip()
    texto = re.sub(r"^PreToolUse:\w+ hook error: ", "", texto)
    linha = texto.splitlines()[0] if texto else "sem detalhe"
    return linha if len(linha) <= limite else linha[: limite - 1] + "…"


def _custo(valor: float | None) -> str:
    return "ainda sem uso" if valor is None else f"US$ {valor:.4f} (estimativa do SDK)"


def _narrar_evento(voz: Voz, evento: dict[str, Any]) -> bool:
    """Texto chegando aos pedaços vai direto para a fala. Devolve True se era texto."""
    tipo = evento.get("type")
    if tipo == "content_block_delta":
        delta = evento.get("delta") or {}
        if delta.get("type") == "text_delta":
            voz.narrar(delta.get("text", ""))
            return True
    elif tipo in ("content_block_stop", "message_stop"):
        voz.fim_de_bloco()
    return False


MUDAM_A_MEMORIA = ("mcp__jarvis__memoria_salvar", "mcp__jarvis__memoria_apagar")


async def responder(
    cliente: ClaudeSDKClient,
    texto: str,
    voz: Voz | None = None,
    modo: str = "texto",
    custo_anterior: float | None = None,
    painel: Any = MUDO,
    aprendeu: Any = None,
    parada: asyncio.Event | None = None,
) -> Turno:
    """Envia um turno, imprime (e fala) a resposta e devolve o que aconteceu, para o registro.

    `painel` acompanha cada passo (pensando, agindo, a legenda da resposta). `aprendeu` é chamado
    quando uma anotação da memória é gravada ou apagada, para a rede do painel crescer na hora.
    `parada`, se acionada, diz que o turno foi largado a pedido do usuário: o fim dele não é erro.
    """
    turno = Turno(pergunta=texto, modo=modo, custo_acumulado=custo_anterior)
    relogio = time.monotonic()
    chamadas: dict[str, Chamada] = {}
    falas: list[str] = []
    falou_aos_pedacos = False
    parcial = ""  # o texto da resposta em andamento, para a legenda do painel acompanhar a fala

    painel.silencio()  # fala nova do usuário: a resposta anterior sai de cena
    painel.legenda("voce", texto)
    painel.fase("pensando")
    await cliente.query(texto)
    # Sem break aqui: o SDK pede para deixar a iteração terminar sozinha.
    async for mensagem in cliente.receive_response():
        if isinstance(mensagem, StreamEvent):
            if voz is not None and _narrar_evento(voz, mensagem.event):
                falou_aos_pedacos = True
                parcial += (mensagem.event.get("delta") or {}).get("text", "")
                painel.legenda("jarvis", " ".join([*falas, parcial]))
        elif isinstance(mensagem, AssistantMessage):
            if mensagem.error:
                if parada is not None and parada.is_set():
                    continue  # largado a pedido: o que o binário diz do corte não é erro
                # O texto que acompanha um erro é do binário, não do Jarvis; fica a nossa mensagem.
                turno.erro = ERROS_DA_API.get(mensagem.error, f"Falha na API: {mensagem.error}")
                print(f"[erro] {turno.erro}")
                continue
            for bloco in mensagem.content:
                if isinstance(bloco, TextBlock) and bloco.text.strip():
                    print(f"jarvis > {bloco.text.strip()}")
                    falas.append(bloco.text.strip())
                    parcial = ""
                    painel.legenda("jarvis", " ".join(falas))
                    if voz is None or getattr(voz, "muda", False):
                        painel.falar_texto(bloco.text)  # sem som, a rede "diz" o texto no ritmo da leitura
                    elif not falou_aos_pedacos:
                        voz.narrar(bloco.text)  # o texto veio inteiro, sem pedaços
                        voz.fim_de_bloco()
                elif isinstance(bloco, ToolUseBlock):
                    print(f"  · {descrever(bloco)}")
                    chamadas[bloco.id] = Chamada(bloco.name, dict(bloco.input or {}))
                    painel.fase("agindo", descrever(bloco))
            falou_aos_pedacos = False
        elif isinstance(mensagem, UserMessage) and isinstance(mensagem.content, list):
            # Resultados de ferramenta: mostra só os que falharam ou foram recusados.
            for bloco in mensagem.content:
                if not isinstance(bloco, ToolResultBlock):
                    continue
                painel.fase("pensando")  # a ferramenta terminou; ele volta a pensar
                feita = chamadas.get(bloco.tool_use_id)
                if bloco.is_error and parada is not None and parada.is_set():
                    if feita is not None:
                        feita.erro = "interrompido a pedido"  # largado no meio: o aviso do binário não interessa ao usuário
                elif bloco.is_error:
                    resumo = resumir_erro(bloco.content)
                    print(f"    não executou: {resumo}")
                    if feita is not None:
                        feita.erro = resumo
                elif feita is not None and feita.ferramenta in MUDAM_A_MEMORIA and aprendeu is not None:
                    aprendeu()
        elif isinstance(mensagem, ResultMessage):
            turno.sessao = mensagem.session_id
            if mensagem.total_cost_usd is not None:
                # O SDK informa o total da sessão; o custo do turno é a diferença.
                turno.custo_usd = max(0.0, mensagem.total_cost_usd - (custo_anterior or 0.0))
                turno.custo_acumulado = mensagem.total_cost_usd
            if mensagem.subtype == "error_max_budget_usd":
                turno.erro = "limite de gasto da sessão atingido"
                print("[aviso] O limite de gasto da sessão (JARVIS_ORCAMENTO_USD) foi atingido.")
            elif parada is not None and parada.is_set():
                turno.interrompido, turno.erro = True, "interrompido a pedido"
            elif mensagem.subtype != "success":
                turno.erro = "; ".join(mensagem.errors or []) or mensagem.subtype
                print(f"[erro] O turno terminou com erro: {turno.erro}")
    if voz is not None:
        voz.fim_de_bloco()
    painel.fase(None)

    turno.resposta = "\n".join(falas)
    turno.chamadas = list(chamadas.values())
    turno.duracao_ms = int((time.monotonic() - relogio) * 1000)
    return turno


TEMPO_MAX_DE_FALA = 60.0  # segundos; a gravação para sozinha se ninguém a encerrar


async def esperar_entrada(
    prompt: str,
    tecla: asyncio.Event | None = None,
    tempo: float | None = None,
    aviso: asyncio.Event | None = None,
    entrada: Entrada | None = None,
) -> tuple[str, str]:
    """Espera o que vier primeiro: uma linha no terminal, a tecla de atalho, um pedido falado ou digitado no painel, um aviso ou o fim do tempo.

    Devolve ("linha", texto), ("atalho", ""), ("voz", pedido), ("painel", pedido), ("aviso", "")
    ou ("tempo", ""). O pedido falado é o que veio depois da palavra de ativação; o aviso é um
    trabalho em segundo plano que terminou. Se o terminal não ganhar, a leitura dele continua
    valendo e é entregue a quem pedir a próxima linha (ver ler_linha). Os pedidos de `entrada`
    que não foram atendidos desta vez continuam nela, na mesma ordem.
    """
    leitura = asyncio.ensure_future(ler_linha(prompt))
    apertou = asyncio.ensure_future(tecla.wait()) if tecla is not None else None
    avisou = asyncio.ensure_future(aviso.wait()) if aviso is not None else None
    chegou = asyncio.ensure_future(entrada.esperar()) if entrada is not None else None
    try:
        await asyncio.wait({leitura, apertou, avisou, chegou} - {None}, timeout=tempo, return_when=asyncio.FIRST_COMPLETED)
        if leitura.done():
            return "linha", leitura.result()
        if apertou is not None and apertou.done():
            tecla.clear()
            return "atalho", ""
        if chegou is not None and chegou.done() and (pedido := entrada.tirar()) is not None:
            return pedido
        if avisou is not None and avisou.done():
            aviso.clear()
            return "aviso", ""
        return "tempo", ""
    finally:
        for tarefa in (leitura, apertou, avisou, chegou):
            if tarefa is not None and not tarefa.done():
                tarefa.cancel()


def _bipe(frequencia: int) -> None:
    try:
        import winsound

        winsound.Beep(frequencia, 90)
    except Exception:  # noqa: BLE001 - fora do Windows, ou sem saída de som: segue sem o aviso
        pass


async def bipe(frequencia: int) -> None:
    """Aviso sonoro curto: quem usa a tecla de atalho não está olhando para o terminal."""
    await asyncio.to_thread(_bipe, frequencia)


async def _medir_microfone(voz: Voz, painel: Any) -> None:
    """Enquanto grava, leva o volume do microfone ao painel umas doze vezes por segundo."""
    while True:
        painel.nivel(voz.nivel_do_microfone())
        await asyncio.sleep(0.08)


async def ouvir(voz: Voz, tecla: asyncio.Event | None = None, sinal: bool = False, painel: Any = MUDO) -> str:
    """Grava até o próximo Enter (ou toque no atalho) e devolve a transcrição (vazia se não deu certo)."""
    if sinal:
        await bipe(880)  # antes de abrir o microfone, para o aviso não entrar na gravação
    try:
        voz.comecar_gravacao()
    except ErroVoz as erro:
        print(f"[voz] {erro}")
        return ""
    painel.fase("ouvindo")
    medidor = asyncio.ensure_future(_medir_microfone(voz, painel)) if painel is not MUDO else None
    pedido = "  gravando… Enter para parar " if tecla is None else "  gravando… Enter ou o atalho para parar "
    try:
        origem, _ = await esperar_entrada(pedido, tecla, TEMPO_MAX_DE_FALA)
    except EOFError:
        origem = "linha"
    finally:
        if medidor is not None:
            medidor.cancel()
    if origem != "linha":
        print("\n  (a gravação passou de um minuto; parei)" if origem == "tempo" else "")
    print("  transcrevendo…")
    painel.fase("entendendo")
    try:
        texto = await asyncio.to_thread(voz.terminar_gravacao)
    except ErroVoz as erro:
        print(f"[voz] {erro}")
        painel.fase(None)
        return ""
    if not texto:
        painel.fase(None)
    if sinal:
        await bipe(660)
    if not texto:
        print("  não ouvi nada; aperte Enter e fale de novo." if not sinal else "  não ouvi nada; aperte o atalho e fale de novo.")
    return texto


def ligar_atalho(
    bruto: str, com_voz: bool, laco: asyncio.AbstractEventLoop, fabrica: Any = Atalho, sistema: str = sys.platform
) -> tuple[asyncio.Event | None, Any]:
    """Registra a tecla de atalho global. Devolve o sinal que ela dispara e o registro (para desligar no fim)."""
    combinacao = ler_combinacao(bruto)
    if combinacao is None or sistema != "win32":
        return None, None
    tecla = asyncio.Event()
    registro = fabrica(combinacao, lambda: laco.call_soon_threadsafe(tecla.set))
    try:
        registro.iniciar()
    except ErroAtalho as erro:
        print(f"[aviso] Tecla de atalho desligada: {erro}.\n")
        return None, None
    faz = "começa e termina a gravação" if com_voz else "traz o terminal para frente"
    print(f"Tecla de atalho, em qualquer janela: {combinacao.texto} {faz}.\n")
    return tecla, registro


SIM = {"s", "sim", "y", "yes"}
TEMPO_DE_CONFIRMACAO = 90.0  # segundos; sem resposta, a ação não roda
TEMPO_PARA_VER = 0.8  # segundos; ninguém lê um pedido e responde antes disso
TEMPO_PARA_LER = 300.0  # para confirmar a instalação de uma skill, com tempo de abrir e ler o que veio


async def confirmar_no_terminal(
    titulo: str,
    linhas: list[str],
    leu_conteudo_externo: str | None = None,
    voz: Voz | None = None,
    tempo: float = TEMPO_DE_CONFIRMACAO,
    espera_minima: float = 0.0,
) -> bool:
    """Mostra o que o Jarvis quer fazer e só devolve True se o usuário digitar "s".

    `espera_minima`: uma resposta que chega antes disso foi digitada antes de o pedido aparecer
    (um s de sobra da confirmação anterior, por exemplo) e não vale para este pedido.
    """
    print(f"\n  >> O Jarvis quer {titulo}:")
    for linha in linhas:
        print(f"       {linha}")
    if leu_conteudo_externo:
        print(f"     Atenção: neste turno ele leu conteúdo externo ({leu_conteudo_externo}). Confira antes de aceitar.")
    if voz is not None:
        voz.dizer(PRECISO_DO_TECLADO)  # sai mesmo se a narração da resposta foi calada por um chamado
    prazo = time.monotonic() + tempo
    while True:
        mostrado = time.monotonic()
        try:
            if mostrado >= prazo:
                raise asyncio.TimeoutError
            resposta = await asyncio.wait_for(ler_linha("     Confirma? [s/N] "), prazo - mostrado)
        except asyncio.TimeoutError:
            print("\n     Sem resposta; não executei.")
            return False
        except EOFError:
            return False
        if time.monotonic() - mostrado >= espera_minima:
            break
        print("     (ignorei o que foi digitado antes de o pedido aparecer)")
    aceito = resposta.strip().lower() in SIM
    if not aceito:
        print("     Não executei.")
    return aceito


def anunciar(titulo: str, linhas: list[str]) -> None:
    """Execução direta: mostra o que está sendo feito, sem perguntar."""
    print(f"  >> {titulo}:")
    for linha in linhas:
        print(f"       {linha}")


def mostrar_memoria(memoria: Memoria) -> None:
    fatos, preferencias, licoes = memoria.listar("fato"), memoria.listar("preferencia"), memoria.listar("licao")
    if not fatos and not preferencias and not licoes:
        print("A memória está vazia. Peça ao Jarvis: \"lembre que…\".")
        return
    for rotulo, itens, pasta in (
        ("fato(s)", fatos, memoria.pasta_de("fato")),
        ("preferência(s)", preferencias, memoria.pasta_de("preferencia")),
        ("lição(ões)", licoes, memoria.pasta_de("licao")),
    ):
        if not itens:
            continue
        print(f"{len(itens)} {rotulo} em {pasta}:")
        for item in itens:
            print(f"  {item.nome}: {item.titulo}" + (f" — {item.resumo}" if item.resumo else ""))


def mostrar_historico(registro: Registro, limite: int = 10) -> None:
    turnos = registro.ultimos(limite)
    if not turnos:
        print("Ainda não há conversas registradas.")
        return
    for t in reversed(turnos):  # do mais antigo para o mais novo
        quando = datetime.fromisoformat(t["inicio"]).strftime("%d/%m %H:%M")
        custo = f"US$ {t['custo_usd']:.4f}" if t["custo_usd"] is not None else "sem custo"
        extras = f"{t['n_chamadas']} ferramenta(s), {custo}" + (", com erro" if t["erro"] else "")
        resposta = " ".join(t["resposta"].split())
        print(f"  {quando} [{t['modo']}] {t['pergunta'][:70]}")
        print(f"      → {resposta[:90] or '(sem resposta)'}  ({extras})")


@dataclasses.dataclass
class Fundo:
    """O modo segundo plano: o terminal (que fica escondido) e o ícone perto do relógio."""

    console: Any
    bandeja: Any
    inicio: Any = None  # o início junto com o Windows, quando dá para ligar e desligar


class Andamento:
    """O pedido que a conversa está atendendo agora, para quem pode mandar parar (a voz, o painel).

    `calar`, `ha_o_que_parar` e `serie` podem ser usados de qualquer thread; o resto é do laço da conversa.
    """

    def __init__(self, voz: Voz | None = None) -> None:
        self._voz = voz
        self.parada = asyncio.Event()  # mandaram largar o pedido em andamento
        self.em_turno = False
        self.serie = 0  # qual pedido está em andamento (ou foi o último): quem manda parar diz qual era
        self._trava = threading.Lock()  # entre calar a fala (de qualquer thread) e o pedido acabar
        self._calado_em = float("-inf")  # quando um chamado calou a fala pela última vez

    @contextlib.contextmanager
    def turno(self) -> Any:
        """Em volta de cada pedido atendido: enquanto dura, um "para" o interrompe e um pedido novo espera a vez."""
        self.parada.clear()
        with self._trava:
            self.serie += 1
            self.em_turno = True
            if self._voz is not None:
                self._voz.soltar()  # pedido novo: a narração, se foi calada no anterior, volta a valer
        try:
            yield
        finally:
            with self._trava:
                self.em_turno = False
                if self._voz is not None:
                    self._voz.soltar()  # fora de um pedido nada fica calado: o aviso de um trabalho que terminou é falado

    def ha_o_que_parar(self) -> bool:
        """Ele está trabalhando ou falando, acabou de terminar de falar, ou foi calado há pouco por um chamado."""
        if self.em_turno or time.monotonic() - self._calado_em < JANELA_DA_PARADA:
            return True
        return self._voz is not None and self._voz.falou_ha() < FIM_DA_FALA

    def calar(self) -> None:
        """Cala a fala na hora. No meio de um pedido, o que ele ainda mandar narrar também não é falado."""
        if self._voz is None:
            return
        with self._trava:
            if self._voz.falando:
                self._calado_em = time.monotonic()
            if self.em_turno:
                self._voz.calar()
            else:
                self._voz.interromper()

    def parar(self, serie: int | None = None) -> bool:
        """Cala e larga o pedido em andamento. Devolve se havia um pedido para largar.

        `serie`: o pedido que estava em andamento quando mandaram parar. Se esse já acabou e outro
        começou (um que esperava a vez), o que mandaram parar não era ele: só a fala é calada.
        """
        self.calar()
        if not self.em_turno or (serie is not None and serie != self.serie):
            return False
        self.parada.set()
        return True


class Chamado:
    """A palavra de ativação ligada à conversa.

    A vigília avisa de dentro da thread do microfone. O que precisa ser imediato (calar a fala,
    responder "Sim?") é feito ali mesmo; o resto é levado ao laço da conversa. O que foi pedido
    depois do nome entra em `pedidos`; uma ordem de parar larga o pedido em `andamento`.

    O microfone segue ouvindo enquanto o Jarvis fala e trabalha: é assim que "Jarvis, para"
    funciona. Só fica surdo durante uma confirmação no teclado (`ouvir(False)`), com a tela
    bloqueada, ou quando o usuário o desliga no ícone.
    """

    def __init__(
        self,
        voz: Voz,
        palavras: tuple[str, ...],
        painel: Any = MUDO,
        bandeja: Any = None,
        bloqueio: Any = None,
        andamento: Andamento | None = None,
        entrada: Entrada | None = None,
        respostas: tuple[str, ...] = RESPOSTAS_AO_NOME,
    ) -> None:
        self._voz, self.palavras, self._painel = voz, tuple(palavras), painel
        self.respostas = tuple(respostas) or RESPOSTAS_AO_NOME
        self._bandeja = bandeja if bandeja is not None else BandejaMuda()
        self._bloqueio = bloqueio  # diz se a tela está bloqueada; aí o nome não é ouvido
        self.andamento = andamento if andamento is not None else Andamento(voz)
        self._laco = asyncio.get_running_loop()
        self.pedidos = entrada if entrada is not None else Entrada()
        self._a_espera = False
        self._surdo = 0  # confirmações no teclado em andamento
        self._respostas = 0
        self._nivel_em = 0.0
        self._sons: set[asyncio.Future[None]] = set()

    @property
    def nome(self) -> str:
        return self.palavras[0].title()

    @property
    def ligado(self) -> bool:
        """O usuário quer ser ouvido: há vigília e ele não desligou o microfone no ícone."""
        vigia = self._voz.vigia
        return vigia is not None and not vigia.pausada

    def ligar(self) -> None:
        """Abre o microfone de vez. Levanta ErroVoz se não conseguir."""
        self._voz.vigiar(
            self.palavras,
            self._pediu,
            ao_acordar=self._ouviu,
            ao_atender=self._atendeu,
            ao_desistir=self._levar(self._desistiu),
            ao_nivel=self._nivel,
            ao_sobrepor=self._voz.pausar_fala,  # uma voz por cima da dele: cala para ouvir o que é
            ao_engano=self._voz.retomar_fala,  # não era com ele: continua de onde parou
            impedido=self._bloqueio,
            imediato=self._eh_para_ja,
            proprias=self.respostas,
        )
        self._voz.vigia.atenta = not self._surdo
        self._marcar()

    def _levar(self, destino: Any) -> Any:
        def da_outra_thread(*dados: Any) -> None:
            try:
                self._laco.call_soon_threadsafe(destino, *dados)
            except RuntimeError:
                pass  # a conversa já acabou

        return da_outra_thread

    def atender(self, sim: bool) -> None:
        """A conversa diz se está à espera de um pedido (o ícone mostra) ou ocupada com um."""
        self._a_espera = sim
        self._marcar()

    def ouvir(self, sim: bool) -> None:
        """Durante uma confirmação no teclado o microfone fica surdo: nada do que for dito conta."""
        self._surdo = max(0, self._surdo + (-1 if sim else 1))
        vigia = self._voz.vigia
        if vigia is not None:
            vigia.atenta = not self._surdo

    @property
    def tela_bloqueada(self) -> bool:
        return self._bloqueio is not None and bool(self._bloqueio())

    def _marcar(self) -> None:
        vigia = self._voz.vigia
        if not self._a_espera:
            self._bandeja.estado("ocupado")
        elif vigia is None or not (vigia.pausada or vigia.aberta or vigia.suspensa):
            self._bandeja.estado("surdo", "Jarvis: sem microfone para a palavra de ativação")
        elif vigia.pausada:
            self._bandeja.estado("surdo")
        elif self.tela_bloqueada:
            self._bandeja.estado("surdo", "Jarvis: tela bloqueada; não atendo pelo nome")
        else:
            self._bandeja.estado("pronto", f'Jarvis: diga "{self.nome}" para chamar')

    def _voltar(self) -> None:
        """Acabou de ouvir: o painel e o ícone voltam a mostrar o que ele fazia."""
        self._painel.fase("pensando" if self.andamento.em_turno else None)
        self._marcar()

    def alternar_microfone(self) -> None:
        """O item do menu do ícone: fecha o microfone, ou abre de novo. Chamado de outra thread."""
        vigia = self._voz.vigia
        if vigia is None:
            return
        if vigia.pausada:
            vigia.continuar()
        else:
            vigia.pausar()
        self._levar(self._marcar)()

    def _tocar(self, frequencia: int) -> None:
        som = asyncio.ensure_future(bipe(frequencia))
        self._sons.add(som)
        som.add_done_callback(self._sons.discard)

    # --- avisos da vigília, na thread do microfone -------------------------------------------------

    def _eh_para_ja(self, pedido: str) -> bool:
        return eh_parada(pedido) and self.andamento.ha_o_que_parar()

    def _ouviu(self) -> None:
        """Ouviu o nome: cala-se na hora, antes de qualquer outra coisa."""
        self.andamento.calar()
        self._levar(self._mostrar_que_ouve)()

    def _atendeu(self) -> None:
        """Ouviu só o nome e a pessoa espera: responde, para ela saber que pode falar."""
        self._voz.dizer(self.respostas[self._respostas % len(self.respostas)])
        self._respostas += 1
        self._levar(print)("\n  (ouvindo o seu pedido…)")

    def _pediu(self, texto: str) -> None:
        havia, serie = self.andamento.ha_o_que_parar(), self.andamento.serie
        self.andamento.calar()
        self._levar(self._chegou)(texto, havia, serie)

    def _nivel(self, nivel: float) -> None:  # o painel aceita avisos de qualquer thread
        agora = time.monotonic()
        if agora - self._nivel_em >= 0.08:
            self._nivel_em = agora
            self._painel.nivel(nivel)

    # --- e o que deles chega ao laço da conversa ---------------------------------------------------

    def _mostrar_que_ouve(self) -> None:
        self._painel.fase("ouvindo")
        self._bandeja.estado("ouvindo")

    def _chegou(self, texto: str, havia_o_que_parar: bool, serie: int | None = None) -> None:
        if eh_parada(texto) and havia_o_que_parar:
            # Só parar: não vai para a conversa. A fala já foi calada; o pedido que estava em andamento é largado.
            if not self.andamento.parar(serie) and not self.andamento.em_turno:
                print("\n  (parei)\nvocê > ", end="", flush=True)
            self._voltar()
            return
        self._tocar(660)  # entendi
        if self.andamento.em_turno or len(self.pedidos):
            print(f"\n  (anotado; faço em seguida: {texto})")
            self._voz.dizer(ESPERE)
        self.pedidos.por("voz", texto)
        self._voltar()

    def _desistiu(self) -> None:
        print("  (não ouvi o pedido; é só chamar de novo)" + ("\nvocê > " if self._a_espera else "\n"), end="", flush=True)
        self._voz.soltar()  # o chamado não deu em nada: o que ele ainda tiver a dizer do pedido em andamento volta a ser falado
        self._voltar()
        self._tocar(440)

    async def zelar(self, intervalo: float = 5.0) -> None:
        """Enquanto a conversa durar: cuida para que o microfone esteja aberto e o ícone diga a verdade.

        O microfone pode não existir ainda quando o Jarvis sobe junto com o Windows, e pode cair
        depois (cabo, suspensão do computador). Em qualquer dos casos, é só tentar de novo.
        """
        while True:
            await asyncio.sleep(intervalo)
            vigia = self._voz.vigia
            try:
                if vigia is None:
                    try:
                        self.ligar()
                    except ErroVoz:
                        continue
                    print(f'\n[aviso] Microfone encontrado: a palavra de ativação está valendo. Diga "{self.nome}".\nvocê > ', end="", flush=True)
                else:
                    await asyncio.to_thread(vigia.conferir_microfone)
                vigia = self._voz.vigia
                if self._a_espera and not (vigia is not None and vigia.esperando_pedido):
                    self._marcar()  # só o estado de espera é refeito: "ouvindo" e "precisa de confirmação" têm dono
            except Exception:  # noqa: BLE001 - o zelo não pode derrubar a conversa
                log.exception("Falha ao conferir o microfone da palavra de ativação")

    async def ensaiar(self) -> None:
        """Na partida: aquece a transcrição com o próprio nome e diz quanto ela leva nesta máquina."""
        try:
            ensaio = await asyncio.to_thread(self._voz.ensaiar_chamado, self.nome)
        except Exception:  # noqa: BLE001 - o ensaio é um cuidado; sem ele o primeiro chamado só demora mais
            log.exception("Falha no ensaio da palavra de ativação")
            return
        if ensaio is None:
            return
        segundos, texto = ensaio
        log.info("Ensaio da palavra de ativação: %.2f s, entendeu %r", segundos, texto)
        if segundos > TRANSCRICAO_LENTA:
            print(
                f"\n[aviso] Nesta máquina a transcrição leva {segundos:.1f} s para reconhecer um chamado. "
                "Para ele responder mais depressa, ponha JARVIS_WHISPER_ATIVACAO=base no .env (um modelo menor só para achar o nome)."
                "\nvocê > ",
                end="",
                flush=True,
            )


class Digitados:
    """Os pedidos digitados no painel, a caminho do laço da conversa.

    `receber` roda na thread do servidor do painel. Há um teto de pedidos esperando a vez: a
    página é local e protegida, mas nada ali deve poder enfileirar ordens sem fim.
    """

    def __init__(self, andamento: Andamento | None = None, entrada: Entrada | None = None, maximo: int = MAX_DIGITADOS) -> None:
        self._laco = asyncio.get_running_loop()
        self._andamento = andamento if andamento is not None else Andamento()
        self.entrada = entrada if entrada is not None else Entrada()
        self._trava = threading.Lock()
        self._esperando, self._maximo = 0, maximo
        self.ocupado = False  # a conversa está atendendo um pedido agora

    def _parar(self, serie: int) -> None:
        if not self._andamento.parar(serie) and not self._andamento.em_turno:
            print("\n  (parei)\nvocê > ", end="", flush=True)

    def receber(self, texto: str) -> str:
        """Devolve "agora" (vai ser atendido já), "fila" (há outro na frente), "parou" ou "cheio" (não coube).

        "para", "chega" e parecidos, com ele falando ou trabalhando, não entram na fila: param na hora.
        """
        if eh_parada(texto) and self._andamento.ha_o_que_parar():
            try:
                self._laco.call_soon_threadsafe(self._parar, self._andamento.serie)
            except RuntimeError:  # a conversa já acabou
                return "cheio"
            return "parou"
        with self._trava:
            if self._esperando >= self._maximo:
                return "cheio"
            self._esperando += 1
            quando = "fila" if self.ocupado or self._esperando > 1 else "agora"
        try:
            self._laco.call_soon_threadsafe(self.entrada.por, "painel", texto)
        except RuntimeError:  # a conversa já acabou
            self.atendido()
            return "cheio"
        return quando

    def atendido(self) -> None:
        with self._trava:
            self._esperando = max(0, self._esperando - 1)


@contextlib.contextmanager
def pergunta_a_vista(fundo: Fundo | None, janelas: Any = None) -> Any:
    """Uma pergunta que exige o teclado traz o terminal escondido para frente; respondida, ele some de novo.

    Se o terminal já estava à vista (o usuário o abriu pelo ícone), fica como está.
    """
    escondido = fundo is not None and fundo.console.existe and not fundo.console.visivel
    if escondido:
        fundo.console.mostrar()
        if janelas is not None:
            try:  # o Windows nem sempre deixa uma janela recém-mostrada vir para frente; este caminho insiste
                janelas.trazer_terminal()
            except Exception:  # noqa: BLE001 - a janela já está à vista; só não veio para frente
                log.exception("Não consegui trazer o terminal para frente")
        fundo.bandeja.estado("atencao")
        fundo.bandeja.atualizar()
    try:
        yield
    finally:
        if escondido:
            fundo.console.esconder()
            fundo.bandeja.estado("ocupado")
            fundo.bandeja.atualizar()


def mostrar_ouvido(voz: Voz | None, chamado: Chamado | None = None) -> None:
    """/ouvido: o estado do microfone e o que ele ouviu sem tomar por chamado."""
    vigia = voz.vigia if voz is not None else None
    if vigia is None:
        print("A palavra de ativação está desligada nesta sessão (precisa de --voz, de um microfone, e JARVIS_PALAVRA não pode ser nao).")
        return
    if vigia.pausada:
        print("Microfone desligado por você no ícone.")
    elif not vigia.aberta:
        print("Microfone fechado: não consegui abri-lo. Sigo tentando a cada poucos segundos.")
    else:
        print(f"Microfone aberto, esperando: {', '.join(vigia.palavras)}.")
    if vigia.erro:
        print(f"Último problema com o microfone: {vigia.erro}")
    if chamado is not None and chamado.tela_bloqueada:
        print(
            "O Windows diz que a tela está bloqueada, e com ela bloqueada eu não atendo pelo nome. "
            "Se a tela está aberta e este aviso não some, ponha JARVIS_OUVIR_BLOQUEADO=sim no .env."
        )
    tempos = list(getattr(vigia, "tempos", ()))  # cópia: a thread do microfone pode acrescentar no meio da leitura
    if tempos:
        media = sum(tempos) / len(tempos)
        print(f"Cada transcrição tem levado {media:.1f} s (a mais demorada das últimas, {max(tempos):.1f} s).")
        if media > TRANSCRICAO_LENTA:
            print("  Para ele responder mais depressa ao nome, ponha JARVIS_WHISPER_ATIVACAO=base no .env.")
    calou, enganos = getattr(vigia, "calou_para_ouvir", 0), getattr(vigia, "enganos", 0)
    if calou:
        print(f"Calou-se para ouvir uma voz por cima da dele {calou} vez(es); em {enganos} não era com ele e ele continuou de onde parou.")
        if enganos >= 2:
            print(
                "  Se a fala dele fica travando, é a própria voz dele voltando pelo microfone mais forte do que o esperado: abaixe o volume "
                "da caixa ou afaste o microfone dela, e desligue as melhorias de áudio do Windows (controle automático de ganho) no microfone."
            )
    dono = getattr(voz, "dono", None)
    if dono is not None and dono.tem_perfil and dono.pronto:
        notas = ", ".join(f"{nota:.2f}{'' if aceito else ' (ignorado)'}" for nota, aceito in list(dono.notas))
        print(
            f"Voz do dono: {dono.quantos} amostras, limiar {dono.limiar:.2f}; chamados aceitos {dono.aceitos}, ignorados {dono.recusados}."
            + (f" Notas recentes: {notas}." if notas else "")
        )
    ouvidos = list(vigia.ouvidos)
    print("Falas recentes que não começaram com a palavra de ativação (ficam só na memória e somem ao fechar):")
    for texto in ouvidos:
        print(f"  · {texto}")
    if not ouvidos:
        print("  (nenhuma)")
    print("Se o seu chamado aparece aí escrito de outro jeito, acrescente essa grafia em JARVIS_PALAVRA (ex.: jarvis;jarbas).")


async def conversar(cfg: Config, memoria: Memoria, registro: Registro, voz: Voz | None = None, fundo: Fundo | None = None) -> None:
    print(f"Jarvis {__version__} · modelo {cfg.modelo}" + (f" · voz {cfg.voz}" if voz else ""))
    print("Leitura liberada em:")
    for pasta in cfg.pastas:
        print(f"  {pasta}")
    if voz is not None:
        print("Enter vazio começa a gravar e Enter de novo envia. Também dá para digitar.")
    print("Comandos: /memoria, /historico, /custo, /painel, /trabalhos, /missoes, /rotinas, /bomdia, /ouvido, /minha-voz, /nuvem, /ajustes, /protheus, /inventario, /calibrar e sair.")
    if fundo is not None:
        print("Segundo plano: um clique no ícone perto do relógio mostra ou esconde este terminal; /esconder também esconde.")
    print()
    bandeja = fundo.bandeja if fundo is not None else BandejaMuda()

    @contextlib.contextmanager
    def terminal_a_vista() -> Any:
        # Enquanto a pergunta espera o teclado, o microfone fica surdo: nada do que for dito conta.
        if chamado is not None:
            chamado.ouvir(False)
        try:
            with pergunta_a_vista(fundo, sessao.janelas):
                yield
        finally:
            if chamado is not None:
                chamado.ouvir(True)

    async def confirmar(titulo: str, linhas: list[str]) -> bool:
        if cfg.confirmar_acoes:
            with terminal_a_vista():
                return await confirmar_no_terminal(titulo, linhas, sessao.guarda.contaminado_por, voz, espera_minima=TEMPO_PARA_VER)
        anunciar(titulo, linhas)  # ordem dada, ordem executada; a guarda já barrou o turno contaminado
        return True

    async def confirmar_digitando(titulo: str, linhas: list[str]) -> bool:
        # Código de terceiros: sempre com o sim digitado, com tempo para ler o que veio.
        with terminal_a_vista():
            return await confirmar_no_terminal(
                titulo, linhas, sessao.guarda.contaminado_por, voz, tempo=TEMPO_PARA_LER, espera_minima=TEMPO_PARA_VER
            )

    async def confirmar_comando(titulo: str, linhas: list[str]) -> bool:
        # Comando de terminal: sempre com o sim digitado, vendo o comando exato.
        with terminal_a_vista():
            aceito = await confirmar_no_terminal(titulo, linhas, sessao.guarda.contaminado_por, voz, espera_minima=TEMPO_PARA_VER)
            if aceito:
                print("     Rodando, sem janela…")
        return aceito

    terminados: list[Trabalho] = []
    aviso = asyncio.Event()
    painel: Any = MUDO
    chamado: Chamado | None = None

    def terminou(trabalho: Trabalho) -> None:
        terminados.append(trabalho)
        aviso.set()
        bandeja.avisar(frase_do_fim(trabalho))

    missoes_terminadas: list[Missao] = []

    def terminou_missao(missao: Missao) -> None:
        missoes_terminadas.append(missao)
        aviso.set()
        bandeja.avisar(frase_do_fim_da_missao(missao)[:200])

    def mostrar_segundo_plano() -> None:
        painel.trabalhos([*oficina.retrato(), *missoes.retrato()][:3])

    skills = Skills(PASTA_DADOS / "skills")
    agenda = Agenda(PASTA_DADOS / "rotinas.json")
    clima = Clima()
    inventario_do_dia = inventario.Inventario(
        inventario.Fluxo(cfg.fluxo_url, cfg.fluxo_usuario, cfg.fluxo_senha),
        inventario.Registro(cfg.pastas[0]),
        modo=cfg.inv_modo,
        maximo=cfg.inv_max_itens,
    )
    protheus = Protheus(cfg.protheus_url, cfg.protheus_usuario, cfg.protheus_senha, cfg.protheus_tenant, cfg.protheus_rotinas, arquivo=PASTA_DADOS / "protheus.json")
    if cfg.protheus_url:
        print(protheus.situacao() + ("" if protheus.pronto else " Use /protheus para conferir."))
    if inventario_do_dia.ligado:
        print(inventario_do_dia.situacao())
    for aviso in conferir_env():
        print(f"[.env] {aviso}")
        print()
    saudacao = Saudacao(PASTA_DADOS / "bom-dia.json", cfg.bom_dia)
    oficina = Oficina(
        ler_projetos(cfg.projetos),
        PASTA_DADOS,
        cfg.modelo_trabalho or cfg.modelo,
        cfg.orcamento_trabalho_usd,
        extras=ler_lista(cfg.comandos),
        skills=skills,
        usuario=cfg.usuario,
        ao_mudar=mostrar_segundo_plano,
        ao_terminar=terminou,
    )
    # As missões têm as ferramentas de leitura da conversa (tempo, rotinas, painel), e nenhuma que age ou grava.
    missoes = Missoes(
        cfg, memoria, PASTA_DADOS,
        ferramentas.criar_ferramentas(
            memoria, registro, agenda=agenda, clima=clima, cidade=cfg.cidade,
            mostrar_paineis=(lambda lista: mostrar_no_painel(painel, lista)) if cfg.painel else None,
        ),
        cfg.orcamento_missao_usd, cfg.modelo_trabalho or cfg.modelo, ao_mudar=mostrar_segundo_plano, ao_terminar=terminou_missao,
    )  # fmt: skip
    for missao in missoes.listar(5):  # a que ficou no meio na última vez (até um dia atrás): o usuário pode querer pedir de novo
        recente = missao.fim >= (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        if recente and missao.erro == "o Jarvis foi fechado no meio da missão" and not missao.relatorio:
            print(f"[aviso] A missão {missao.id} ({missao.titulo}) foi interrompida quando o Jarvis fechou; peça de novo se ainda quiser.\n")
            break
    anunciar_projetos(oficina)
    terminal = Terminal(Path.home(), oficina.projetos, PASTA_DADOS / "terminal.log") if cfg.terminal else None
    if terminal is not None:
        print("Terminal: ele roda comandos sem abrir janela; cada comando aparece aqui e só roda com o seu s.\n")

    sessao = montar_sessao(
        cfg, memoria, registro, voz=voz is not None, confirmar=confirmar, oficina=oficina, skills=skills,
        confirmar_digitando=confirmar_digitando, terminal=terminal, confirmar_comando=confirmar_comando,
        # O painel nasce logo abaixo: a ferramenta o alcança quando for usada, não agora.
        mostrar_paineis=(lambda lista: mostrar_no_painel(painel, lista)) if cfg.painel else None,
        agenda=agenda, clima=clima, missoes=missoes, protheus=protheus,
    )  # fmt: skip
    for nota in sessao.avisos:
        print(f"[aviso] {nota}\n")
    tecla, atalho = ligar_atalho(cfg.atalho, voz is not None, asyncio.get_running_loop())
    painel = ligar_painel(cfg, memoria, registro, voz, sessao, abrir=None if fundo is not None else abrir_janela)
    mostrar_segundo_plano()

    andamento, entrada = Andamento(voz), Entrada()
    digitados: Digitados | None = None
    if painel is not MUDO:
        digitados = Digitados(andamento, entrada)
        painel.aceitar_pedidos(digitados.receber)

    despertador = Despertador(agenda, lambda rotina, atraso: entregar_rotina(rotina, atraso, entrada))
    anunciar_rotinas(agenda)
    zelos: list[asyncio.Future[None]] = [asyncio.ensure_future(despertador.rodar())]
    if inventario_do_dia.ligado and inventario_do_dia.fluxo.pronto:
        # Às 17:00 (ou na primeira vez que o Jarvis estiver de pé depois disso), a etapa A roda
        # sozinha. A digitação no Protheus não: essa espera a aprovação digitada.
        vigia_do_inventario = inventario.Vigia(
            inventario.ler_hora(cfg.inv_hora),
            cfg.inv_dias,
            disparar=lambda: entrada.por("inventario", "[inventario] hora do inventário do dia"),
            ja_rodou=lambda: inventario_do_dia.registro.ja_rodou_hoje() is not None,
        )
        zelos.append(asyncio.ensure_future(vigia_do_inventario.rodar()))
    if voz is not None:
        voz.preparar((*(cfg.respostas or RESPOSTAS_AO_NOME), ESPERE, PRECISO_DO_TECLADO))  # frases fixas: saem sem a espera da síntese
    if voz is not None and cfg.palavras:
        bloqueio = None if cfg.ouvir_bloqueado else segundo_plano.Bloqueio()
        chamado = Chamado(voz, cfg.palavras, painel, bandeja, bloqueio, andamento, entrada, cfg.respostas or RESPOSTAS_AO_NOME)
        try:
            chamado.ligar()
            print(
                f'Palavra de ativação: diga "{chamado.nome}" e espere a resposta, ou diga "{chamado.nome}" e o pedido de uma vez, em qualquer janela. '
                f'"{chamado.nome}, para" cala a fala e larga o que ele estiver fazendo. '
                "O microfone fica aberto e o som é tratado só nesta máquina."
                + (" Com a tela bloqueada (Win+L) ele não atende.\n" if bloqueio is not None and bloqueio.disponivel else "\n")
            )
        except ErroVoz as erro:
            print(f"[aviso] {erro}. Por enquanto sem a palavra de ativação (sigo tentando); o Enter e a tecla de atalho continuam valendo.\n")
        zelos = [asyncio.ensure_future(chamado.zelar()), asyncio.ensure_future(chamado.ensaiar())]

    if fundo is not None:
        if sessao.janelas is not None:
            sessao.janelas.definir_terminal(fundo.console.janela)  # nasceu escondido: ninguém mais sabe qual é
        _ligar_icone(fundo, painel, chamado)
    despertador.conferir()  # o que venceu enquanto o Jarvis estava desligado

    def cumprimentar(mesmo_assim: bool = False) -> None:
        """O bom dia: na partida e a cada volta ao computador, se ainda não foi feito hoje."""
        if not (mesmo_assim or saudacao.devida()):
            return
        saudacao.marcar()
        entrada.por("bom_dia", saudacao.pedido(cfg.usuario, cfg.cidade, agenda, oficina))

    bloqueio_do_dia = segundo_plano.Bloqueio()
    if saudacao.ligada and bloqueio_do_dia.disponivel:
        zelos.append(asyncio.ensure_future(vigiar_desbloqueio(bloqueio_do_dia, cumprimentar)))
    cumprimentar()
    try:
        await _laco_da_conversa(
            cfg, memoria, registro, voz, sessao, tecla, painel, oficina, aviso, terminados, chamado, fundo, digitados, andamento, entrada, agenda,
            lambda: cumprimentar(True), missoes, missoes_terminadas, inventario_do_dia,
        )  # fmt: skip
    finally:
        for zelo in zelos:
            zelo.cancel()
        try:  # um comando ainda rodando seguraria a saída até acabar
            encerrar_comandos()
        except Exception:  # noqa: BLE001 - encerrando
            log.exception("Falha ao encerrar os comandos em andamento")
        if voz is not None:
            try:
                voz.parar_vigilia()
            except Exception:  # noqa: BLE001 - encerrando
                log.exception("Falha ao fechar o microfone da palavra de ativação")
        try:
            for missao in await missoes.encerrar():
                print(f"Missão {missao.id} ({missao.titulo}) interrompida.")
        except Exception:  # noqa: BLE001 - encerrando
            log.exception("Falha ao encerrar as missões em andamento")
        try:
            interrompidos = await oficina.encerrar()
        except Exception:  # noqa: BLE001 - encerrando
            log.exception("Falha ao encerrar os trabalhos em andamento")
            interrompidos = []
        for trabalho in interrompidos:
            guardado = f"o que estava feito ficou no ramo {trabalho.ramo}" if trabalho.commits else "ele ainda não tinha alterado o código"
            print(f"Trabalho {trabalho.id} ({trabalho.projeto}) interrompido: {guardado}.")
        for trabalho in terminados:  # terminou no último instante, sem dar tempo de avisar
            if trabalho not in interrompidos:
                print(f"\n[trabalho] {frase_do_fim(trabalho)}\n" + "\n".join(f"    {linha}" for linha in oficina.texto(trabalho).splitlines()))
        for missao in missoes_terminadas:
            if missao.estado != "interrompida":
                print(f"\n[missão] {frase_do_fim_da_missao(missao)}\n" + "\n".join(f"    {linha}" for linha in missoes.texto(missao).splitlines()))
        if atalho is not None:
            atalho.parar()
        if voz is not None:
            voz.observar(None, None)
        painel.fechar()
        if painel is not MUDO and sessao.janelas is not None:
            try:
                sessao.janelas.fechar_painel()
            except Exception:  # noqa: BLE001 - encerrando; a janela mostra "desligado" se ficar aberta
                log.exception("Não consegui fechar a janela do painel")


ESPERA_PARA_SAIR = 10.0  # segundos entre o primeiro Sair e um segundo que encerra à força


def forcar_saida() -> None:
    try:
        encerrar_comandos()  # nenhum comando fica rodando sozinho depois que o Jarvis se foi
    finally:
        os._exit(0)


def _ligar_icone(fundo: Fundo, painel: Any, chamado: Chamado | None) -> None:
    """Dá ao menu do ícone o que fazer em cada item. As ações rodam na thread do ícone."""
    laco, conversa = asyncio.get_running_loop(), asyncio.current_task()
    pedido_em: list[float] = []

    def sair() -> None:
        if not pedido_em:
            pedido_em.append(time.monotonic())
            laco.call_soon_threadsafe(conversa.cancel)
        elif time.monotonic() - pedido_em[0] > ESPERA_PARA_SAIR:
            # Já foi pedido e ele não saiu (a conversa está presa em alguma coisa): sai à força.
            log.warning("Sair pedido de novo depois de %s s: encerrando à força", ESPERA_PARA_SAIR)
            forcar_saida()
        # Antes disso, um segundo clique é só pressa: atropelaria o fechamento dos trabalhos em andamento.

    def alternar_inicio() -> None:
        if fundo.inicio.ligado():
            fundo.inicio.desligar()
        else:
            fundo.inicio.ligar()

    acoes: dict[str, Any] = {"sair": sair}
    leituras: dict[str, Any] = {}
    if fundo.console.existe:
        acoes["terminal"], leituras["terminal"] = fundo.console.alternar, lambda: fundo.console.visivel
    if painel is not MUDO:
        acoes["painel"] = lambda: abrir_janela(painel.endereco)
    if chamado is not None:
        acoes["microfone"], leituras["microfone"] = chamado.alternar_microfone, lambda: chamado.ligado
    if fundo.inicio is not None:
        acoes["inicio"], leituras["inicio"] = alternar_inicio, fundo.inicio.ligado
    fundo.bandeja.ligar(acoes, leituras)


async def tratar_calibracao(cfg: Config, sessao: Any, pedido: str) -> Any:
    """`/calibrar` abre a janela e mede; `/calibrar fim` salva o roteiro. Entre os dois, ele grava."""
    if pedido in ("testar", "teste"):
        if not cfg.protheus_webapp:
            print("Falta JARVIS_PROTHEUS_WEBAPP no .env.")
            return sessao
        print("[calibração] Testando as combinações, uma a uma. Vai abrir e fechar janelas; aguarde.")
        try:
            for linha in await webapp.diagnosticar(cfg.protheus_webapp, PASTA_DADOS / "navegador-teste"):
                print(f"[calibração] {linha}")
        except Exception as erro:  # noqa: BLE001 - é um teste: nada aqui derruba a conversa
            log.exception("Falha no diagnóstico do WebApp")
            print(f"[calibração] O diagnóstico falhou: {type(erro).__name__}: {str(erro)[:200]}")
        print("[calibração] Me mande estas linhas: é o que diz onde está o problema.")
        return sessao
    if pedido in ("fim", "parar", "salvar"):
        if sessao is None:
            print("Não há calibração em andamento.")
            return None
        try:
            arquivo = await sessao.terminar()
            print(f"[calibração] Roteiro salvo em {arquivo}. Revise antes do primeiro uso: os campos, as mensagens e o que ficou 'a_decidir'.")
        except Exception as erro:  # noqa: BLE001 - calibração que falha volta ao prompt, não derruba o Jarvis
            log.exception("Falha ao encerrar a calibração")
            print(f"[calibração] {erro if isinstance(erro, webapp.ErroWebApp) else f'{type(erro).__name__}: {str(erro)[:200]}'}")
        return None
    if sessao is not None:
        try:
            novos = await sessao.colher()
        except Exception as erro:  # noqa: BLE001 - a janela pode ter sido fechada na mão
            log.exception("Falha ao recolher os passos da calibração")
            print(f"[calibração] {erro if isinstance(erro, webapp.ErroWebApp) else f'{type(erro).__name__}: {str(erro)[:200]}'}")
            print("A sessão foi encerrada. Comece de novo com /calibrar quando o endereço estiver certo.")
            return None
        print(f"[calibração] +{novos} passo(s). {sessao.situacao()}")
        return sessao
    if not cfg.protheus_webapp:
        print("Falta JARVIS_PROTHEUS_WEBAPP no .env: o endereço do Protheus no navegador (use o de homologação).")
        return None
    nova = webapp.Calibracao(
        webapp.Navegador(cfg.protheus_webapp, PASTA_DADOS / "navegador-protheus", visivel=True),
        PASTA_DADOS / "roteiros",
    )
    try:
        medida = await nova.comecar(avisar=lambda t: print(f"[calibração] {t}"))
    except Exception as erro:  # noqa: BLE001 - endereço, rede, navegador: nada disso derruba a conversa
        log.exception("Falha ao começar a calibração")
        print(f"[calibração] {erro if isinstance(erro, webapp.ErroWebApp) else f'{type(erro).__name__}: {str(erro)[:200]}'}")
        try:
            await nova.navegador.fechar()
        except Exception:  # noqa: BLE001 - fechando
            log.info("A janela já estava fechada")
        return None
    if nova.navegador.falhou_antes:
        # Só quando algo DE FATO falhou antes: dizer "a primeira tentativa falhou" quando a
        # primeira funcionou manda o usuário levar à consultoria um problema que não existe.
        print(f"[calibração] Algumas opções levaram resposta vazia; funcionou na opção '{nova.navegador.tentativa}'. "
              "Vale contar à consultoria: o servidor recusa o que os navegadores novos trazem por padrão.")
    print(f"[calibração] Janela aberta em {cfg.protheus_webapp}. {medida.veredito()}")
    print("Faça o caminho normal, devagar. Digite /calibrar de vez em quando para eu recolher, e /calibrar fim no final.")
    return nova


async def tratar_inventario(alvo: Any, pedido: str, voz: Any = None) -> None:
    """`/inventario` mostra a situação; `/inventario montar` roda a etapa A; `/inventario aprovar` libera a digitação."""
    if not alvo.ligado and pedido not in ("", "situacao"):
        print("O inventário diário está desligado. Ponha JARVIS_INV_MODO=homologacao no .env e reinicie.")
        return
    if pedido in ("", "situacao"):
        print(alvo.situacao())
        return
    if pedido in ("montar", "rodar", "hoje"):
        try:
            dia = await asyncio.to_thread(alvo.montar_o_dia)
        except inventario.ErroInventario as erro:
            print(f"[inventário] {erro}")
            return
        pergunta = alvo.pergunta(dia)
        print(f"[inventário] {pergunta}")
        if voz is not None:
            voz.falar(pergunta)
        return
    if pedido in ("aprovar", "sim", "s"):
        try:
            print("[inventário] " + await asyncio.to_thread(alvo.aprovar))
        except inventario.ErroInventario as erro:
            print(f"[inventário] {erro}")
        return
    if pedido in ("acertar", "acerto"):
        # A segunda aprovação, à parte da primeira: o acerto muda saldo e custo e não tem volta.
        try:
            print("[inventário] " + await asyncio.to_thread(alvo.acertar))
        except inventario.ErroInventario as erro:
            print(f"[inventário] {erro}")
        return
    print("Use: /inventario (situação), /inventario montar, /inventario aprovar, /inventario acertar.")


def mostrar_ajustes(cfg: Config, voz: Any = None) -> None:
    """O que está ligado e o que não está, e por quê. Nenhum valor de chave aparece."""
    ligado = lambda x: "ligada" if x else "desligada"  # noqa: E731
    print("\nAjustes desta sessão (valores não aparecem, só o que está ligado):")
    print(f"  Voz da nuvem (ElevenLabs): {ligado(cfg.elevenlabs_chave)}" + ("" if cfg.elevenlabs_chave else " — falta JARVIS_ELEVENLABS_KEY no .env"))
    if cfg.elevenlabs_chave:
        print(f"    voz escolhida: {cfg.elevenlabs_voz or 'a primeira da biblioteca'}; modelo: {cfg.elevenlabs_modelo}")
        nuvem = getattr(voz, "nuvem", None)
        print("    " + (nuvem.situacao() if nuvem is not None else "não carregou nesta sessão (precisa do modo de voz: --voz ou --segundo-plano)"))
    print(f"  Leitura do Protheus: {ligado(cfg.protheus_url and cfg.protheus_usuario and cfg.protheus_senha)}")
    print(f"  Terminal: {ligado(cfg.terminal)} · Painel: {ligado(cfg.painel)} · Palavra de ativação: {', '.join(cfg.palavras) or 'nenhuma'}")
    print(f"  Turno limpo (ver README): {ligado(cfg.turno_limpo)}")
    avisos = conferir_env()
    if avisos:
        print("  Linhas do .env que não estão valendo:")
        for aviso in avisos:
            print(f"    - {aviso}")
    else:
        print("  Todas as linhas do seu .env são ajustes que eu conheço.")
    print()


def anunciar_projetos(oficina: Oficina) -> None:
    """Na partida: os projetos cadastrados e os trabalhos que ficaram pelo caminho na última vez."""
    if oficina.projetos:
        print("Projetos para programar:")
        for projeto in oficina.projetos.values():
            print(f"  {projeto.nome}: {projeto.pasta}")
        print()
    try:
        perdidos = oficina.recuperar()
    except Exception:  # noqa: BLE001 - não impede a conversa
        log.exception("Falha ao recuperar trabalhos interrompidos")
        perdidos = []
    for trabalho in perdidos:
        guardado = f"o que estava feito ficou no ramo {trabalho.ramo}" if trabalho.commits else "sem alterações no código"
        print(f"[aviso] O trabalho {trabalho.id} ({trabalho.projeto}) foi interrompido quando o Jarvis fechou: {guardado}.\n")


def frase_do_fim(trabalho: Trabalho) -> str:
    """O que o Jarvis diz quando um trabalho termina. Texto fixo: o relatório do trabalhador não entra na fala."""
    if trabalho.estado == CONCLUIDO and trabalho.commits:
        return (
            f"Terminei o trabalho no projeto {trabalho.projeto}. Deixei o resultado em um ramo novo para a sua revisão; "
            "os detalhes estão no terminal."
        )
    if trabalho.estado == CONCLUIDO:
        return f"Terminei o trabalho no projeto {trabalho.projeto}, sem alterar o código. O relatório está no terminal."
    guardado = " O que já estava feito ficou guardado em um ramo." if trabalho.commits else ""
    motivo = f": {trabalho.erro}" if trabalho.erro else ""
    return f"O trabalho no projeto {trabalho.projeto} não chegou ao fim ({ROTULOS.get(trabalho.estado, trabalho.estado)}{motivo}).{guardado}"


async def avisar_fim(oficina: Oficina, terminados: list[Trabalho], voz: Voz | None, painel: Any = MUDO) -> None:
    """Mostra no terminal (e fala) cada trabalho que terminou enquanto o usuário fazia outra coisa."""
    while terminados:
        trabalho = terminados.pop(0)
        frase = frase_do_fim(trabalho)
        print(f"\n[trabalho] {frase}")
        for linha in oficina.texto(trabalho).splitlines():
            print(f"    {linha}")
        print()
        painel.legenda("jarvis", frase)
        if voz is not None and not getattr(voz, "muda", False):
            voz.narrar(frase)
            voz.fim_de_bloco()
        else:
            await bipe(988)
            painel.falar_texto(frase)


async def avisar_fim_de_missao(missoes: Missoes, terminadas: list[Missao], voz: Voz | None, painel: Any = MUDO) -> None:
    """Conta cada missão que acabou: a frase fixa e o começo do relatório em voz alta; o relatório inteiro no terminal."""
    while terminadas:
        missao = terminadas.pop(0)
        frase = frase_do_fim_da_missao(missao)
        print(f"\n[missão] {frase}")
        for linha in missoes.texto(missao).splitlines():
            print(f"    {linha}")
        print()
        painel.legenda("jarvis", frase)
        if voz is not None and not getattr(voz, "muda", False):
            voz.narrar(frase)
            voz.fim_de_bloco()
        else:
            await bipe(988)
            painel.falar_texto(frase)


def mostrar_missoes(missoes: Missoes, qual: str = "") -> None:
    """/missoes lista; /missoes <código ou palavra> mostra uma por extenso."""
    if qual:
        try:
            print(missoes.texto(missoes.achar(qual)))
        except Exception as erro:  # noqa: BLE001 - ErroMissao: só a mensagem
            print(str(erro))
        return
    lista = missoes.listar(10)
    if not lista:
        print("Ainda não há missões. Peça ao Jarvis, por exemplo: \"em segundo plano, pesquise os cinco melhores notebooks até cinco mil reais e me diga qual compensa\".")
        return
    for missao in lista:
        print(f"  {missoes.linha(missao)}")
        if missao.estado == MISSAO_RODANDO:
            print(f"      agora: {missao.ultimo_passo or 'começando'}")
    print("Detalhes de uma: /missoes <código>")


LEITURAS_DA_ROTINA = 5  # leituras do Protheus que um turno automático pode fazer


def entregar_rotina(rotina: Rotina, atraso: Any, entrada: Entrada) -> None:
    """A hora de uma rotina chegou: ela vira um lembrete a dizer ou um pedido na fila da conversa.

    O que venceu com o Jarvis desligado chega aqui com atraso. Um lembrete de uma vez é dito
    mesmo assim, avisando que atrasou; uma tarefa de uma vez só vale até 12 horas depois; e a
    repetida, até 3 horas (a próxima vem de qualquer jeito).
    """
    quando = rotina.quando
    tarde = atraso > (ATRASO_DA_REPETIDA if rotina.repetida else ATRASO_MAXIMO)
    if tarde and (rotina.repetida or rotina.tipo != LEMBRETE):
        print(f"\n[rotina] Ficou para trás ({rotina.tipo} de {quando:%d/%m %H:%M}, o Jarvis estava desligado): {rotina.texto}\nvocê > ", end="", flush=True)
        return
    if rotina.tipo == LEMBRETE:
        if tarde:
            dia = "ontem" if (datetime.now().date() - quando.date()).days == 1 else f"dia {quando:%d/%m}"
            entrada.por("lembrete", f"Lembrete atrasado, era para {dia} às {quando:%H:%M}: {rotina.texto}")
        else:
            entrada.por("lembrete", f"Lembrete: {rotina.texto}")
        return
    entrada.por("rotina", f"[rotina {rotina.descricao}] {rotina.texto}")


async def lembrar(frase: str, voz: Voz | None, painel: Any = MUDO, bandeja: Any = None) -> None:
    """Diz um lembrete: no terminal, no painel (um cartão), no ícone e em voz alta."""
    print(f"\n[lembrete] {frase}\n")
    painel.legenda("jarvis", frase)
    try:
        painel.paineis([{"titulo": "Lembrete", "categoria": "Rotina · Agenda", "icone": "agenda", "texto": frase.partition(": ")[2] or frase}])
    except Exception:  # noqa: BLE001 - o cartão é enfeite
        log.exception("Não consegui mostrar o lembrete no painel")
    if bandeja is not None:
        bandeja.avisar(frase)
    if voz is not None and not getattr(voz, "muda", False):
        voz.narrar(frase)
        voz.fim_de_bloco()
    else:
        await bipe(988)
        painel.falar_texto(frase)


def anunciar_rotinas(agenda: Agenda) -> None:
    """Na partida: o que está marcado para hoje."""
    hoje = agenda.do_dia()
    if hoje:
        print("Rotinas de hoje:")
        for rotina in hoje:
            print(f"  {rotina.quando:%H:%M} · {rotina.tipo} · {rotina.texto}")
        print()


def mostrar_trabalhos(oficina: Oficina, qual: str = "") -> None:
    """/trabalhos lista; /trabalhos <código ou projeto> mostra um por extenso."""
    if qual:
        try:
            print(oficina.texto(oficina.achar(qual)))
        except Exception as erro:  # noqa: BLE001 - ErroTrabalho ou git fora do ar: só a mensagem
            print(str(erro))
        return
    trabalhos = oficina.listar(10)
    if not trabalhos:
        print("Ainda não há trabalhos. Peça ao Jarvis, por exemplo: \"no projeto X, corrija o teste que está falhando\".")
        return
    for trabalho in trabalhos:
        print(f"  {oficina.linha(trabalho)}")
        if trabalho.estado == RODANDO:
            print(f"      agora: {trabalho.ultimo_passo or 'preparando a cópia de trabalho'} ({trabalho.minutos} min, {trabalho.passos} passos)")
    print("Detalhes de um: /trabalhos <código>")


def mostrar_no_painel(painel: Any, lista: list[dict[str, Any]]) -> str:
    """A ferramenta painel_mostrar: põe os cartões no painel e abre a janela dele se estiver fechada.

    Devolve, para o modelo, o que foi feito.
    """
    if painel is MUDO:
        return "O painel está desligado nesta sessão: diga a resposta em vez de mostrar."
    quantos = painel.paineis(lista)
    if not lista:
        return "Cartões fechados."
    if not quantos:
        return "Nenhum cartão tinha o que mostrar: cada um precisa de um título ou de algum conteúdo."
    cartoes = "1 cartão" if quantos == 1 else f"{quantos} cartões"
    if painel.assinantes == 0 and not abrir_janela(painel.endereco):
        return f"{cartoes} no painel, mas a janela dele não abriu: diga ao usuário para abrir o painel pelo ícone ou com /painel."
    return f"{cartoes} no painel."


def ajustar_voz(voz: Voz, ajustes: dict[str, Any]) -> None:
    """Põe na voz de verdade os ajustes de voz do painel: ligada ou não, a velocidade e qual voz.

    Chamada de outra thread, a cada troca feita na página. Carregar outra voz leva um instante e
    por isso vai para uma thread própria: quem trocou na página não fica esperando.
    """
    voz.emudecer(not ajustes.get("voz", True))
    try:
        voz.velocidade(float(ajustes.get("velocidade", 1.0)))
    except (TypeError, ValueError):
        pass
    nome = str(ajustes.get("voz_nome") or "")
    if nome == voz.voz_atual:
        return

    def trocar() -> None:
        try:
            voz.trocar_voz(nome)
        except ErroVoz as erro:
            print(f"\n[aviso] {erro}\nvocê > ", end="", flush=True)
        except Exception:  # noqa: BLE001 - a voz de antes continua valendo
            log.exception("Falha ao trocar de voz")

    threading.Thread(target=trocar, daemon=True, name="jarvis-troca-de-voz").start()


def ligar_painel(cfg: Config, memoria: Memoria, registro: Registro, voz: Voz | None, sessao: Any, abrir: Any = abrir_janela) -> Any:
    """Sobe o painel e abre a janela dele. Qualquer falha vira aviso: o Jarvis segue sem painel.

    `abrir=None` (segundo plano) sobe o painel sem abrir janela; ela fica a um clique, no ícone.
    """
    if not cfg.painel:
        return MUDO
    try:
        combinacao = ler_combinacao(cfg.atalho) if sys.platform == "win32" else None
        painel = Painel(
            atalho=combinacao.texto if combinacao else "",
            com_voz=voz is not None,
            arquivo=PASTA_DADOS / "painel.json",  # os ajustes da engrenagem (cor, tema, movimento, voz) valem de uma sessão para a outra
            vozes=voz.vozes() if voz is not None else (),
        )
        painel.conhecimento(conhecimento(memoria, registro))
        endereco = painel.iniciar()
    except Exception as erro:  # noqa: BLE001
        log.exception("Não consegui subir o painel")
        print(f"[aviso] Painel desligado: {erro}.\n")
        return MUDO
    if voz is not None:
        voz.observar(painel.som, painel.silencio)
        painel.ao_aparencia = lambda ajustes: ajustar_voz(voz, ajustes)
        ajustar_voz(voz, painel.aparencia)  # o que ficou guardado da última vez
        if voz.muda:
            print("[aviso] A voz está desligada no painel: ele responde só por escrito. Para religar: engrenagem do painel, Voz.\n")
    if abrir is None:
        print("Painel: no menu do ícone, Abrir o painel (ou digite /painel).\n")
        return painel
    como = abrir(endereco)
    if como in ("chrome", "msedge"):
        print(f"Painel aberto em janela própria ({como}).\n")
        if sessao.janelas is not None:  # a janela nova toma a frente; o foco volta ao terminal
            asyncio.get_running_loop().call_later(2.5, sessao.janelas.trazer_terminal)
    elif como:
        print(f"Painel aberto no {como}: {endereco}\n")
    else:
        print(f"Painel: abra {endereco} em um navegador desta máquina.\n")
    return painel


INSISTIR_NA_PARADA = 2.0  # segundos entre um pedido de interrupção ao agente e o seguinte, se o primeiro não pegou


async def cumprir(
    cliente: ClaudeSDKClient,
    texto: str,
    voz: Voz | None,
    modo: str,
    custo: float | None,
    painel: Any,
    aprendeu: Any,
    andamento: Andamento,
) -> Turno:
    """Atende um pedido. Se no meio dele mandarem parar ("Jarvis, para"), larga o que estava fazendo.

    Largar é pedir ao agente que interrompa e esperar ele confirmar: só então a conversa aceita o
    pedido seguinte, para a resposta de um não se misturar com a do outro.
    """
    with andamento.turno():
        resposta = asyncio.ensure_future(responder(cliente, texto, voz, modo, custo, painel, aprendeu, andamento.parada))
        parou = asyncio.ensure_future(andamento.parada.wait())
        try:
            await asyncio.wait({resposta, parou}, return_when=asyncio.FIRST_COMPLETED)
            if not resposta.done():
                print("\n  (parando, a seu pedido…)")
                try:  # um comando de terminal em andamento seguraria a interrupção até acabar
                    encerrar_comandos()
                except Exception:  # noqa: BLE001 - a interrupção segue pelo agente
                    log.exception("Falha ao encerrar os comandos em andamento")
                while not resposta.done():
                    try:
                        await asyncio.wait_for(cliente.interrupt(), INSISTIR_NA_PARADA)
                    except Exception:  # noqa: BLE001 - tenta de novo na próxima volta
                        log.warning("O agente não respondeu ao pedido de interrupção; insistindo")
                    await asyncio.wait({resposta}, timeout=INSISTIR_NA_PARADA)
            return await resposta
        finally:
            parou.cancel()
            if not resposta.done():
                resposta.cancel()  # a conversa inteira está sendo encerrada


async def _laco_da_conversa(
    cfg: Config,
    memoria: Memoria,
    registro: Registro,
    voz: Voz | None,
    sessao: Any,
    tecla: asyncio.Event | None,
    painel: Any = MUDO,
    oficina: Oficina | None = None,
    aviso: asyncio.Event | None = None,
    terminados: list[Trabalho] | None = None,
    chamado: Chamado | None = None,
    fundo: Fundo | None = None,
    digitados: Digitados | None = None,
    andamento: Andamento | None = None,
    entrada: Entrada | None = None,
    agenda: Agenda | None = None,
    bom_dia: Any = None,
    missoes: Missoes | None = None,
    missoes_terminadas: list[Missao] | None = None,
    inventario_do_dia: Any = None,
) -> None:
    calibracao: Any = None  # a sessão de calibração em andamento, quando houver
    custo: float | None = None
    if andamento is None:
        andamento = chamado.andamento if chamado is not None else Andamento(voz)
    if entrada is None:  # a voz e o painel põem os pedidos na mesma fila; aqui, quando só um dos dois existe
        entrada = chamado.pedidos if chamado is not None else digitados.entrada if digitados is not None else None

    def a_espera(sim: bool) -> None:
        """A conversa espera um pedido, ou está ocupada com um: o ícone e o painel mostram em que pé está."""
        if digitados is not None:
            digitados.ocupado = not sim
        if chamado is not None:
            chamado.atender(sim)
        elif fundo is not None:
            fundo.bandeja.estado("pronto" if sim else "ocupado")

    def aprendeu() -> None:
        try:
            painel.conhecimento(conhecimento(memoria, registro))
        except Exception:  # noqa: BLE001 - o painel nunca interrompe a conversa
            log.exception("Não consegui atualizar o conhecimento no painel")

    async with ClaudeSDKClient(options=sessao.opcoes) as cliente:
        while True:
            a_espera(True)
            try:
                origem, texto = await esperar_entrada("você > ", tecla, aviso=aviso, entrada=entrada)
            except EOFError:
                break
            finally:
                a_espera(False)
            if origem == "aviso":  # um trabalho ou uma missão em segundo plano terminou
                if oficina is not None and terminados:
                    await avisar_fim(oficina, terminados, voz, painel)
                if missoes is not None and missoes_terminadas:
                    await avisar_fim_de_missao(missoes, missoes_terminadas, voz, painel)
                continue
            if origem == "inventario":  # a hora marcada do inventário do dia chegou
                print()
                await tratar_inventario(inventario_do_dia, "montar", voz)
                print("você > ", end="", flush=True)
                continue
            if origem == "lembrete":  # a hora de um lembrete chegou
                await lembrar(texto, voz, painel, fundo.bandeja if fundo is not None else None)
                continue
            texto = texto.strip()
            modo = "texto"
            pelo_atalho = origem == "atalho"
            pelo_nome = origem == "voz"  # chamou pelo nome e pediu
            pelo_painel = origem == "painel"  # digitou na janela do painel
            pela_rotina = origem in ("rotina", "bom_dia")  # a hora de uma tarefa marcada chegou, ou é o cumprimento do dia
            if pelo_nome:
                print()  # sai da linha do "você > ", que continua esperando
                modo = "voz"
                if texto:
                    print(f"você (voz) > {texto}")
            elif pela_rotina:
                print()
                modo = "voz" if voz is not None else "texto"
                print(f"você ({'bom dia' if origem == 'bom_dia' else 'rotina'}) > {texto if origem == 'rotina' else texto.splitlines()[0]}")
            elif pelo_painel:
                digitados.atendido()
                print()
                if voz is not None:
                    voz.interromper()  # qualquer entrada nova cala a resposta anterior
                if texto:
                    print(f"você (painel) > {texto}")
            elif pelo_atalho:
                print()  # sai da linha do "você > ", que continua esperando
                if voz is None:
                    if sessao.janelas is not None:
                        sessao.janelas.trazer_terminal()
                    continue
                voz.interromper()
                texto = await ouvir(voz, tecla, sinal=True, painel=painel)
                modo = "voz"
                if texto:
                    print(f"você (voz) > {texto}")
            elif voz is not None:
                voz.interromper()  # qualquer entrada nova cala a resposta anterior
                if not texto:
                    texto = await ouvir(voz, tecla, painel=painel)
                    modo = "voz"
                    if texto:
                        print(f"você (voz) > {texto}")
            if not texto:
                continue
            # Comando é coisa digitada no terminal (ou ditada pelo atalho). O que chega pelo nome ou pelo painel vai
            # sempre para a conversa: um "sair" entendido errado, ou vindo de uma página, não pode desligar o Jarvis.
            comando = "" if pelo_nome or pelo_painel or pela_rotina else texto.lower()
            if comando in SAIR:
                break
            if comando == "/custo":
                print(f"Gasto da sessão: {_custo(custo)}")
                if getattr(voz, "nuvem", None) is not None:
                    print(voz.nuvem.situacao())
                continue
            if comando == "/memoria":
                mostrar_memoria(memoria)
                continue
            if comando == "/historico":
                mostrar_historico(registro)
                continue
            if comando.split()[:1] == ["/trabalhos"]:
                if oficina is not None:
                    mostrar_trabalhos(oficina, texto.partition(" ")[2].strip())
                continue
            if comando == "/ouvido":
                mostrar_ouvido(voz, chamado)
                continue
            if comando.split()[:1] in (["/minha-voz"], ["/minhavoz"]):
                await cadastrar_voz(voz, tecla, painel, chamado, apagar=comando.partition(" ")[2].strip() == "apagar")
                continue
            if comando == "/rotinas":
                print(agenda.texto() if agenda is not None else "Esta sessão não tem agenda de rotinas.")
                continue
            if comando.split()[:1] == ["/missoes"]:
                if missoes is not None:
                    mostrar_missoes(missoes, texto.partition(" ")[2].strip())
                continue
            if comando == "/protheus":
                if sessao.protheus is None or not cfg.protheus_url:
                    print("A leitura do Protheus está desligada: ponha JARVIS_PROTHEUS_URL, JARVIS_PROTHEUS_USER e JARVIS_PROTHEUS_SENHA no .env e reinicie.")
                else:
                    print(sessao.protheus.situacao())
                    if sessao.protheus.pronto:
                        print("Testando o healthcheck…")
                        try:
                            print(await asyncio.to_thread(sessao.protheus.saude))
                        except Exception as erro:  # noqa: BLE001 - só a mensagem
                            print(str(erro))
                        sessao.protheus.zerar()
                continue
            if comando.split()[:1] == ["/calibrar"]:
                calibracao = await tratar_calibracao(cfg, calibracao, texto.partition(" ")[2].strip())
                continue
            if comando.split()[:1] == ["/inventario"]:
                await tratar_inventario(inventario_do_dia, texto.partition(" ")[2].strip(), voz)
                continue
            if comando == "/ajustes":
                mostrar_ajustes(cfg, voz)
                continue
            if comando == "/nuvem":
                if getattr(voz, "nuvem", None) is None:
                    print("A voz da nuvem não está ligada nesta sessão: ponha JARVIS_ELEVENLABS_KEY no .env e reinicie (precisa do modo de voz)." if voz is not None else "Precisa do modo de voz (python -m jarvis --voz).")
                else:
                    print("Testando a voz da nuvem…")
                    print(await asyncio.to_thread(voz.nuvem.testar))
                continue
            if comando == "/bomdia":
                if bom_dia is None:
                    print("O cumprimento do dia não está ligado nesta sessão.")
                else:
                    bom_dia()
                continue
            if comando == "/esconder":
                if fundo is None or not fundo.console.esconder():
                    print("Só no modo segundo plano (dois cliques em iniciar.pyw, ou python -m jarvis --segundo-plano).")
                else:
                    fundo.bandeja.atualizar()
                continue
            if comando == "/painel":
                if painel is MUDO:
                    print("O painel está desligado nesta sessão (JARVIS_PAINEL=nao ou --sem-painel).")
                elif not abrir_janela(painel.endereco):
                    print(f"Abra {painel.endereco} em um navegador desta máquina.")
                continue

            sessao.guarda.novo_turno()  # o usuário falou: o turno começa limpo
            if sessao.protheus is not None:
                # O teto de leituras recomeça. Turno que o usuário não pediu (rotina, bom dia) leva
                # cota menor: senão cada disparo automático gastava as 15 e o teto do dia sozinho.
                sessao.protheus.zerar(LEITURAS_DA_ROTINA if pela_rotina else MAX_CHAMADAS)
            try:
                turno = await cumprir(cliente, texto, voz, modo, custo, painel, aprendeu, andamento)
            finally:
                painel.fase(None)
                if sessao.janelas is not None:
                    try:  # a foto perde a validade; o foco volta ao terminal, se a ordem veio dele
                        # Sem levar o foco para um terminal escondido: as teclas do usuário iriam para o nada.
                        a_vista = fundo is None or fundo.console.visivel
                        sessao.janelas.fim_do_turno(cfg.devolver_foco and origem == "linha" and a_vista)
                    except Exception:  # noqa: BLE001 - não interrompe a conversa
                        log.exception("Falha ao encerrar o turno no controle de janelas")
            custo = turno.custo_acumulado
            if turno.interrompido:
                print("  (parei)")
            elif turno.erro and (pelo_nome or pelo_painel or pela_rotina or fundo is not None):
                # Sem o terminal à vista, um erro só escrito passaria em branco.
                if fundo is not None:
                    fundo.bandeja.avisar(f"Não consegui responder: {turno.erro}")
                if voz is not None and not turno.resposta:
                    voz.dizer("Não consegui responder agora. O motivo está no terminal.")
            try:
                registro.gravar(turno)
            except Exception:  # noqa: BLE001 - falha no registro não interrompe a conversa
                log.exception("Não consegui gravar o turno no registro")
            aprendeu()  # mais uma conversa: a rede do painel ganha experiência

    print(f"Sessão encerrada. Gasto: {_custo(custo)}")


def analisador_de_argumentos() -> argparse.ArgumentParser:
    # Sem abreviações: "--seg" não pode valer por "--segundo-plano" aqui e não valer para quem esconde o terminal.
    analisador = argparse.ArgumentParser(prog="jarvis", description="Jarvis, assistente de desktop.", allow_abbrev=False)
    analisador.add_argument("--voz", action="store_true", help="fala e escuta (precisa de requirements-voz.txt)")
    analisador.add_argument("--sem-painel", action="store_true", help="não abre a janela do painel")
    analisador.add_argument(
        "--segundo-plano", action="store_true", help="sobe escondido, com voz e um ícone perto do relógio (Windows)"
    )
    analisador.add_argument("--iniciar-com-windows", action="store_true", help="liga o Jarvis junto com o Windows, escondido, e já o sobe")
    analisador.add_argument("--nao-iniciar-com-windows", action="store_true", help="desfaz o --iniciar-com-windows")
    analisador.add_argument("--criar-atalho", action="store_true", help="põe o Jarvis, com ícone, na Área de Trabalho e no menu Iniciar")
    analisador.add_argument("--remover-atalho", action="store_true", help="desfaz o --criar-atalho")
    analisador.add_argument("--console", choices=("proprio", "classico"), help=argparse.SUPPRESS)  # uso interno do segundo plano
    return analisador


def main(argumentos: list[str] | None = None) -> int:
    opcoes = analisador_de_argumentos().parse_args(argumentos)

    # No Windows, com a saída redirecionada, a codificação padrão não é UTF-8;
    # sem isto um caractere fora dela derruba o programa no meio da resposta.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    # Texto de fora nunca chega ao terminal com caracteres de controle (ver SaidaLimpa).
    if not isinstance(sys.stdout, SaidaLimpa):
        sys.stdout, sys.stderr = SaidaLimpa(sys.stdout), SaidaLimpa(sys.stderr)

    if opcoes.criar_atalho or opcoes.remover_atalho:
        return atalhos.ajustar_atalhos(opcoes.criar_atalho)
    if opcoes.iniciar_com_windows or opcoes.nao_iniciar_com_windows:
        return ajustar_inicio(opcoes.iniciar_com_windows)
    escondido = opcoes.segundo_plano
    if escondido and sys.platform != "win32":
        print("[erro] O segundo plano (ícone perto do relógio) só existe no Windows.", file=sys.stderr)
        return 2

    def falhar(texto: str, codigo: int) -> int:
        """Erro que impede de continuar. Com o terminal escondido, ele aparece numa caixa do Windows."""
        if escondido:
            segundo_plano.caixa(f"{texto}\n\nO Jarvis não subiu.")
        else:
            print(texto, file=sys.stderr)
        return codigo

    # Avisos das bibliotecas (voz, transcrição) vão para o arquivo, não para a conversa.
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=PASTA_DADOS / "jarvis.log", level=logging.WARNING, encoding="utf-8")

    # Um Jarvis por instalação: dois ouviriam o mesmo microfone e responderiam juntos.
    instancia = segundo_plano.Instancia()
    if not instancia.unica:
        ja = "O Jarvis já está rodando nesta máquina. Procure o ícone dele perto do relógio (pode estar na setinha ^): um clique mostra o terminal."
        if escondido:
            segundo_plano.caixa(ja, erro=False)
        else:
            print(ja + " Para abrir outro, escolha Sair no menu do ícone (ou digite sair no terminal dele) primeiro.", file=sys.stderr)
        return 1

    fundo: Fundo | None = None
    if escondido:
        fundo = preparar_fundo()
        if fundo is None:
            return 2

    load_dotenv(RAIZ_PROJETO / ".env")

    try:
        cfg = carregar()
    except ErroConfig as erro:
        return falhar(f"[configuração] {erro}", 2)
    if opcoes.sem_painel:
        cfg = dataclasses.replace(cfg, painel=False)

    voz: Voz | None = None
    if opcoes.voz or escondido:  # escondido, é pela voz que se fala com ele
        try:
            voz = carregar_voz(cfg, tentativas=4 if escondido else 1)
        except ErroVoz as erro:
            return falhar(f"[voz] {erro}", 2)

    memoria = Memoria(PASTA_MEMORIA)
    registro = Registro(ARQUIVO_REGISTRO)
    try:
        asyncio.run(conversar(cfg, memoria, registro, voz, fundo))
    except (KeyboardInterrupt, asyncio.CancelledError):  # Ctrl+C, ou Sair no menu do ícone
        print("\nAté logo.")
    except CLINotFoundError:
        return falhar(
            "[erro] O binário do Claude Code não veio junto com o SDK nesta máquina. "
            "Reinstale com: pip install --force-reinstall claude-agent-sdk",
            1,
        )
    except ProcessError as erro:
        return falhar(f"[erro] O processo do agente caiu: {erro}. Detalhes em .jarvis\\agente.log", 1)
    except ClaudeSDKError as erro:
        return falhar(f"[erro] Falha no Agent SDK: {erro}", 1)
    except Exception as erro:  # noqa: BLE001 - escondido, um erro inesperado não pode sumir junto com o terminal
        if not escondido:
            raise
        log.exception("O Jarvis parou por um erro inesperado")
        return falhar(f"[erro] O Jarvis parou por um erro inesperado: {type(erro).__name__}: {erro}. Detalhes em .jarvis\\jarvis.log", 1)
    finally:
        registro.fechar()
        if voz is not None:
            voz.fechar()
        if fundo is not None:
            fundo.bandeja.parar()
    return 0


def carregar_voz(cfg: Config, tentativas: int = 1, espera: float = 10.0, carregar_modelos: Any = None, dono: Any = None) -> Voz:
    """Carrega a voz. Subindo junto com o Windows, o som pode ainda não estar pronto: tenta mais de uma vez."""
    carregar_modelos = carregar_modelos or Voz.carregar
    for tentativa in range(1, tentativas + 1):
        try:
            extras: dict[str, Any] = {}
            if cfg.elevenlabs_chave:  # a voz da nuvem só entra na conta quando há chave
                extras["nuvem"] = {"chave": cfg.elevenlabs_chave, "voz": cfg.elevenlabs_voz, "modelo": cfg.elevenlabs_modelo, "limite": cfg.elevenlabs_limite}
            voz = carregar_modelos(cfg.voz, cfg.whisper, PASTA_DADOS, whisper_da_vigilia=cfg.whisper_da_ativacao if cfg.palavras else "", **extras)
            if hasattr(voz, "dono") and cfg.palavras:  # a conferência da voz só faz sentido com a palavra de ativação
                voz.dono = ligar_voz_do_dono(cfg, dono)
            return voz
        except ErroVoz as erro:
            if tentativa == tentativas or "Falta instalar" in str(erro):  # biblioteca que falta não aparece esperando
                raise
            log.warning("A voz não carregou (%s); tento de novo em %s s", erro, espera)
            time.sleep(espera)
    raise AssertionError("não chega aqui")


def ligar_voz_do_dono(cfg: Config, dono: Any = None) -> Any:
    """O cadastro da voz do usuário. Com cadastro e JARVIS_SO_O_DONO ligado, o modelo é carregado já: só a voz dele chama."""
    try:
        dono = dono if dono is not None else VozDoDono(PASTA_DADOS / "modelos", PASTA_DADOS / "voz-do-dono.npz", cfg.limiar_da_voz)
    except Exception as erro:  # noqa: BLE001 - sem o cadastro, o Jarvis atende qualquer voz, como sempre
        log.exception("Não consegui preparar o cadastro da voz do dono")
        print(f"[aviso] Reconhecimento da sua voz desligado: {erro}\n")
        return None
    if not dono.tem_perfil:
        print("Voz do dono: não cadastrada. Digite /minha-voz para o Jarvis aprender a sua voz e atender só a você.\n")
        return dono
    if not cfg.so_o_dono:
        print("Voz do dono: cadastrada, mas JARVIS_SO_O_DONO=nao está no .env: ele atende qualquer voz.\n")
        return dono
    try:
        dono.preparar(print)
    except ErroDono as erro:
        print(f"[aviso] Reconhecimento da sua voz desligado por enquanto: {erro}\n")
        return dono
    print(f"Voz do dono: cadastrada ({dono.quantos} amostras). Só a sua voz chama o Jarvis; /minha-voz apagar desliga.\n")
    return dono


async def gravar_amostra(voz: Voz, tecla: asyncio.Event | None = None, painel: Any = MUDO) -> Any:
    """Uma gravação crua para o cadastro da voz: Enter (ou o atalho) começa, Enter (ou o atalho) termina."""
    try:
        origem, _ = await esperar_entrada("  Enter para começar a gravar ", tecla)
    except EOFError:
        return None
    if origem == "atalho":
        pass  # o atalho também serve para começar
    await bipe(880)
    try:
        voz.comecar_gravacao()
    except ErroVoz as erro:
        print(f"[voz] {erro}")
        return None
    painel.fase("ouvindo")
    try:
        await esperar_entrada("  gravando… Enter para parar ", tecla, TEMPO_MAX_DE_FALA)
    except EOFError:
        pass
    finally:
        painel.fase(None)
    try:
        audio = await asyncio.to_thread(voz.terminar_gravacao_bruta)
    except ErroVoz as erro:
        print(f"[voz] {erro}")
        return None
    await bipe(660)
    return audio


async def cadastrar_voz(voz: Voz | None, tecla: asyncio.Event | None = None, painel: Any = MUDO, chamado: Chamado | None = None, apagar: bool = False) -> bool:
    """/minha-voz: grava as frases do cadastro e guarda a assinatura da voz. /minha-voz apagar desfaz."""
    dono = getattr(voz, "dono", None) if voz is not None else None
    if dono is None:
        print("O cadastro da voz precisa do modo de voz (python -m jarvis --voz, ou o segundo plano)." if voz is None else "O cadastro da voz está indisponível nesta sessão.")
        return False
    if apagar:
        dono.apagar()
        print("Cadastro da voz apagado: o Jarvis volta a atender qualquer voz.")
        return True
    try:
        await asyncio.to_thread(dono.preparar, print)
    except ErroDono as erro:
        print(f"[voz] {erro}")
        return False
    print(
        f"Vou aprender a sua voz: são {len(FRASES_DE_CADASTRO)} frases. Para cada uma, aperte Enter, diga a frase com naturalidade "
        "e aperte Enter de novo. Fique no lugar e com o microfone que usa no dia a dia. Nenhum áudio é guardado, só a assinatura da voz."
    )
    if chamado is not None:
        chamado.ouvir(False)
    amostras: list[Any] = []
    try:
        for numero, frase in enumerate(FRASES_DE_CADASTRO, 1):
            for tentativa in range(3):
                print(f"\n  Frase {numero} de {len(FRASES_DE_CADASTRO)}: «{frase}»")
                audio = await gravar_amostra(voz, tecla, painel)
                if audio is None:
                    print("Cadastro cancelado.")
                    return False
                if len(audio) >= DURACAO_DO_CADASTRO * TAXA:
                    amostras.append(audio)
                    break
                print("  Ficou curta demais (menos de um segundo); vamos de novo." if tentativa < 2 else "  Não deu; cadastro cancelado.")
            else:
                return False
        quantas = await asyncio.to_thread(dono.cadastrar, amostras)
    except ErroDono as erro:
        print(f"[voz] {erro}")
        return False
    finally:
        if chamado is not None:
            chamado.ouvir(True)
    print(f"\nPronto: guardei {quantas} amostras da sua voz. De agora em diante só a sua voz chama o Jarvis pelo nome; ele vai aprendendo a cada chamado.")
    print("Se ele deixar de atender você, confira o /ouvido (mostra a nota de cada chamado) e, se precisar, /minha-voz apagar ou JARVIS_LIMIAR_DA_VOZ no .env.")
    return True


def preparar_fundo(console: Any = None, bandeja: Any = None, inicio: Any = None) -> Fundo | None:
    """Segundo plano: o terminal ganha nome e perde o X, e o ícone sobe (ainda "carregando")."""
    console = console if console is not None else segundo_plano.console_atual()
    console.titulo("Jarvis")
    console.sem_fechar()
    console.sem_selecao()
    bandeja = bandeja if bandeja is not None else segundo_plano.Bandeja()
    try:
        bandeja.iniciar()
    except ImportError as erro:
        console.mostrar()
        segundo_plano.caixa(
            f"Falta a biblioteca do ícone ({erro.name}). No terminal, na pasta do Jarvis, rode:\n\npip install -r requirements.txt\n\nO Jarvis não subiu."
        )
        return None
    if inicio is None:
        try:
            inicio = segundo_plano.InicioAutomatico()
            inicio.conferir()  # a pasta do Jarvis mudou de lugar? O Windows passa a chamar a nova
        except Exception:  # noqa: BLE001 - sem o registro, o item "Iniciar com o Windows" só não aparece
            log.exception("Não consegui consultar o início junto com o Windows")
            inicio = None
    return Fundo(console, bandeja, inicio)


def ajustar_inicio(
    ligar: bool, sistema: str | None = None, inicio: Any = None, lancar: Any = segundo_plano.lancar, rodando: Any = segundo_plano.ja_esta_rodando
) -> int:
    """--iniciar-com-windows e --nao-iniciar-com-windows."""
    if (sistema or sys.platform) != "win32":
        print("[erro] Iniciar junto com o sistema só existe no Windows.", file=sys.stderr)
        return 2
    try:
        inicio = inicio if inicio is not None else segundo_plano.InicioAutomatico()
        if ligar:
            inicio.ligar()
        else:
            inicio.desligar()
    except OSError as erro:
        print(f"[erro] O Windows não deixou mudar o início automático: {erro}", file=sys.stderr)
        return 1
    if not ligar:
        print("Pronto: o Jarvis não sobe mais junto com o Windows. Se ele está rodando agora, continua até você escolher Sair no ícone.")
        return 0
    print("Pronto: a partir de agora o Jarvis sobe junto com o Windows, escondido, com um ícone perto do relógio.")
    if rodando():
        print("Já há um Jarvis rodando agora, e ele continua como está. Para passá-lo já para o segundo plano: digite sair nele e dê dois cliques em iniciar.pyw.")
        return 0
    try:
        lancar()
    except OSError as erro:
        print(f"[aviso] Não consegui subi-lo agora ({erro}). Dê dois cliques em iniciar.pyw, ou entre de novo no Windows.")
        return 0
    print("Já o deixei subindo: em alguns segundos (ele carrega a voz) o ícone aparece perto do relógio; pode estar na setinha ^.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
