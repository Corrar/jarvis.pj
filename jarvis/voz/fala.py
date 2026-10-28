"""Fala: fila de frases → síntese (Piper) → reprodução, sem travar a conversa."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_DA_FRASE_PRONTA = 48  # letras; frase até esse tamanho fica guardada depois de sintetizada
MAX_DE_FRASES_PRONTAS = 40
_SO_PREPARAR = -1  # na fila de textos: sintetizar e guardar, sem tocar
PAUSA_MAXIMA = 12.0  # segundos; uma pausa que ninguém desfez acaba sozinha, para a fala nunca ficar presa
QUADRO_DO_SOM = 0.03  # segundos; o volume do que toca é medido em pedaços deste tamanho
ATRASO_DA_SAIDA = 0.6  # segundos que o som pode levar, depois de mandado tocar, para sair da caixa (caixa Bluetooth: até meio segundo)
CAUDA_DO_SOM = 0.35  # segundos em que o som ainda chega ao microfone depois de parar de tocar
CAUDA_CHEIA = 0.1  # desses, os primeiros em que ele ainda vale inteiro; daí em diante cai pela metade a cada MEIA_CAUDA
MEIA_CAUDA = 0.05
SOM_MINIMO = 0.005  # abaixo disto o que toca é silêncio
RETOMADA_MINIMA = 1.0  # segundos; frase menor que isto, cortada por uma pausa, toca de novo inteira
RETOMADA_FOLGA = 0.3  # segundos que voltam ao retomar do meio: o que a caixa não chegou a soltar e uma sílaba de embalo
BLOCO_DA_SAIDA = 0.05  # segundos de som por escrita na saída: é o tempo máximo que um "para" leva para pegar

Audio = Any  # numpy.ndarray de int16, mono
Sintetizar = Callable[[str], tuple[Audio, int]]  # frase → (áudio, taxa de amostragem)
Tocar = Callable[[Audio, int], None]  # toca e só volta quando terminar (ou for parado)

_URL_VOZ = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "{familia}/{idioma}/{nome}/{qualidade}/{idioma}-{nome}-{qualidade}{extensao}?download=true"
)
_PADRAO_VOZ = re.compile(r"^(?P<familia>[a-z]{2})_(?P<regiao>[A-Z]{2})-(?P<nome>[^-]+)-(?P<qualidade>[^-]+)$")


def medir_volumes(audio: Audio, taxa: int) -> Any:
    """O volume de cada pedaço de 30 ms do áudio, na mesma escala em que o microfone é medido (0 a 1)."""
    import numpy as np

    som = np.asarray(audio)
    inteiro = np.issubdtype(som.dtype, np.integer)
    som = som.astype(np.float32).reshape(-1)
    if inteiro:
        som = som / 32768.0
    quadro = max(1, int(taxa * QUADRO_DO_SOM))
    if len(som) % quadro:
        som = np.concatenate([som, np.zeros(quadro - len(som) % quadro, dtype=np.float32)])
    return np.sqrt(np.mean(som.reshape(-1, quadro) ** 2, axis=1))


def volume_tocado(volumes: Any, ponto: float) -> float:
    """O volume do que sai da caixa quando o áudio (medido por `medir_volumes`) está em `ponto` segundos.

    Vale o ponto mais alto desde `ATRASO_DA_SAIDA` antes: o som mandado tocar demora a sair.
    """
    if len(volumes) == 0:
        return 0.0
    primeiro = min(len(volumes) - 1, max(0, int((ponto - ATRASO_DA_SAIDA) / QUADRO_DO_SOM)))
    ultimo = min(len(volumes), max(primeiro + 1, int(ponto / QUADRO_DO_SOM) + 2))
    volume = float(volumes[primeiro:ultimo].max())
    return volume if volume >= SOM_MINIMO else 0.0


def volume_no_ar(volumes: Any, inicio: float, fim: float | None, agora: float) -> float:
    """O volume do que o microfone pode estar ouvindo de um áudio que começou a tocar em `inicio`.

    `fim` é quando ele parou de tocar (None: ainda toca). Depois de parar, o que resta é o som
    morrendo na sala: vale inteiro por um instante e cai depressa. Quem responde logo depois dele
    ("Sim?" … "que horas são?") não pode ficar esperando esse resto acabar para ser ouvido.
    """
    if fim is None:
        return volume_tocado(volumes, agora - inicio)
    passado = agora - fim
    if passado > CAUDA_DO_SOM:
        return 0.0
    volume = volume_tocado(volumes, fim - inicio) * 0.5 ** (max(0.0, passado - CAUDA_CHEIA) / MEIA_CAUDA)
    return volume if volume >= SOM_MINIMO else 0.0


class Falante:
    """Duas etapas em paralelo: enquanto uma frase toca, a seguinte já está sendo sintetizada."""

    def __init__(
        self, sintetizar: Sintetizar, tocar: Tocar, parar_som: Callable[[], None], relogio: Callable[[], float] = time.monotonic
    ) -> None:
        self._sintetizar = sintetizar
        self._tocar = tocar
        self._parar_som = parar_som
        self._relogio = relogio
        self._som: tuple[float, Any, float | None] | None = None  # o que toca: (quando começou, volume quadro a quadro, quando parou)
        self._pausas = 0  # conta as pausas, para a que expira não desfazer uma mais nova
        self._calou_em = float("-inf")  # quando a fala acabou (ou foi cortada) pela última vez
        self._textos: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._audios: queue.Queue[tuple[int, Audio, int, str] | None] = queue.Queue()
        self._ditas: deque[tuple[float, str]] = deque(maxlen=8)  # as últimas frases que começaram a tocar, e quando
        self._estado = threading.Condition()
        self._geracao = 0  # muda a cada interrupção; o que for de geração antiga é descartado
        self._pendentes = 0
        self._prontas: dict[str, tuple[Audio, int]] = {}  # frases curtas já sintetizadas: saem na hora
        self._preparadas: list[str] = []  # as que foram pedidas de antemão: são refeitas se a voz mudar
        self._voz_n = 0  # muda quando a voz ou a velocidade mudam: o que foi sintetizado antes não é guardado
        self._segurando = False  # pausado: nada toca até retomar (ou até ser interrompido)
        self._tocando = False
        self._cortada = False  # a frase que tocava foi cortada por uma pausa e deve tocar de novo
        self.ultimo_erro: Exception | None = None
        # Quem quiser acompanhar a fala (o painel): uma frase começou a tocar; a fala acabou.
        self.ao_tocar: Callable[[Audio, int], None] | None = None
        self.ao_calar: Callable[[], None] | None = None
        for alvo, nome in ((self._laco_sintese, "jarvis-sintese"), (self._laco_som, "jarvis-som")):
            threading.Thread(target=alvo, daemon=True, name=nome).start()

    @property
    def falando(self) -> bool:
        """Há frase sendo preparada ou tocando."""
        with self._estado:
            return self._pendentes > 0

    def ditas(self, janela: float = 20.0) -> list[str]:
        """As frases que ele disse (ou começou a dizer) nos últimos segundos, da mais antiga para a mais nova.

        Serve para quem ouve o microfone reconhecer a voz dele pelo que foi dito.
        """
        desde = self._relogio() - janela
        return [frase for quando, frase in list(self._ditas) if quando >= desde]

    def falou_ha(self) -> float:
        """Há quantos segundos ele terminou de falar: zero se está falando, infinito se ainda não falou."""
        with self._estado:
            return 0.0 if self._pendentes > 0 else self._relogio() - self._calou_em

    def falar(self, frase: str) -> None:
        with self._estado:
            self._pendentes += 1
            geracao = self._geracao
        self._textos.put((geracao, frase))

    def interromper(self) -> None:
        """Cala na hora e descarta o que estava na fila."""
        with self._estado:
            self._geracao += 1
            self._segurando = self._cortada = False
            self._estado.notify_all()
        self._parar_som()
        self._avisar(self.ao_calar)

    def pausar(self, maximo: float = PAUSA_MAXIMA) -> None:
        """Cala na hora, mas guarda a fila. `retomar` recomeça da frase que foi cortada.

        Quem pausou e não voltou não prende a fala: depois de `maximo` segundos ela retoma sozinha.
        """
        with self._estado:
            if self._segurando:
                return
            self._segurando = True
            self._cortada = self._tocando
            self._pausas += 1
            esta = self._pausas
        self._parar_som()
        # A frase pode estar a um instante de começar a tocar: aí o corte acima não pega nada. Confere de novo.
        for espera, acao in ((0.05, self._reforcar_pausa), (0.25, self._reforcar_pausa), (maximo, lambda: self._vencer_pausa(esta))):
            relogio = threading.Timer(espera, acao)
            relogio.daemon = True
            relogio.start()
        self._avisar(self.ao_calar)

    def _vencer_pausa(self, qual: int) -> None:
        with self._estado:
            vencida = self._segurando and self._pausas == qual
        if vencida:
            log.warning("A fala ficou pausada por tempo demais; retomando")
            self.retomar()

    def _reforcar_pausa(self) -> None:
        with self._estado:
            precisa = self._segurando and self._tocando
            self._cortada = self._cortada or precisa
        if precisa:
            self._parar_som()

    def retomar(self) -> None:
        with self._estado:
            self._segurando = False
            self._estado.notify_all()

    def tocado(self) -> float:
        """O volume (de 0 a 1) do que está saindo da caixa de som agora; zero se nada toca.

        É uma estimativa por cima: o som demora um pouco a sair e a sumir, então vale o ponto
        mais alto de uma janela em volta do instante. Serve para quem ouve o microfone saber
        quanto do que chega é a própria voz do Jarvis. Não espera por nada: pode ser chamada
        da thread do áudio.
        """
        som = self._som
        if som is None:
            return 0.0
        inicio, volumes, fim = som
        return volume_no_ar(volumes, inicio, fim, self._relogio())

    def preparar(self, frases: Any) -> None:
        """Sintetiza de antemão frases curtas e fixas ("Sim?"), para saírem sem a espera da síntese.

        Vai pela mesma fila da fala: a síntese não é feita em duas threads ao mesmo tempo.
        """
        for frase in frases:
            if frase not in self._preparadas:
                self._preparadas.append(frase)
            self._textos.put((_SO_PREPARAR, frase))

    def velocidade(self, valor: float) -> None:
        """Muda a velocidade da fala, se esta voz souber variar. Vale da próxima frase sintetizada em diante."""
        if getattr(self._sintetizar, "velocidade", None) in (None, valor):
            return
        self._sintetizar.velocidade = valor  # type: ignore[attr-defined]
        self._voz_mudou()

    def trocar_sintese(self, sintetizar: Sintetizar) -> None:
        """Passa a falar com outra voz. Vale da próxima frase sintetizada em diante."""
        self._sintetizar = sintetizar
        self._voz_mudou()

    def _voz_mudou(self) -> None:
        # As frases guardadas foram feitas com a voz de antes: fora, e as pedidas de antemão são refeitas.
        self._voz_n += 1
        self._prontas.clear()
        for frase in self._preparadas:
            self._textos.put((_SO_PREPARAR, frase))

    def amostra(self, frase: str, tempo: float = 30.0) -> tuple[Audio, int] | None:
        """O áudio de uma frase curta, sem tocar. Passa pela fila da síntese; None se não ficar pronto a tempo."""
        self.preparar([frase])
        limite = time.monotonic() + tempo
        while frase not in self._prontas:
            if time.monotonic() >= limite:
                return None
            time.sleep(0.05)
        return self._prontas[frase]

    def esperar(self, tempo: float | None = None) -> bool:
        """Espera a fila esvaziar. Devolve False se o tempo acabar antes."""
        with self._estado:
            return self._estado.wait_for(lambda: self._pendentes == 0, timeout=tempo)

    def fechar(self) -> None:
        self.interromper()
        self._textos.put(None)
        self._audios.put(None)

    def _vale(self, geracao: int) -> bool:
        with self._estado:
            return geracao == self._geracao

    def _concluir(self) -> None:
        with self._estado:
            self._pendentes -= 1
            acabou = self._pendentes == 0
            if acabou:
                self._calou_em = self._relogio()
            self._estado.notify_all()
        if acabou:
            self._avisar(self.ao_calar)

    @staticmethod
    def _avisar(aviso: Callable[..., None] | None, *dados: Any) -> None:
        if aviso is not None:
            try:
                aviso(*dados)
            except Exception:  # noqa: BLE001 - quem observa não pode atrapalhar a fala
                log.exception("Falha em quem acompanha a fala")

    def _laco_sintese(self) -> None:
        while (item := self._textos.get()) is not None:
            geracao, frase = item
            voz_n = self._voz_n
            if geracao == _SO_PREPARAR:
                try:
                    if frase not in self._prontas:
                        pronta = self._sintetizar(frase)
                        if voz_n == self._voz_n:  # a voz mudou no meio: esta já é velha
                            self._prontas[frase] = pronta
                except Exception:  # noqa: BLE001 - sem a frase pronta, ela é sintetizada na hora de falar
                    log.exception("Falha ao preparar a frase %r", frase)
                continue
            if not self._vale(geracao):
                self._concluir()
                continue
            try:
                audio, taxa = self._prontas.get(frase) or self._sintetizar(frase)
                if len(frase) <= MAX_DA_FRASE_PRONTA and len(self._prontas) < MAX_DE_FRASES_PRONTAS and voz_n == self._voz_n:
                    self._prontas.setdefault(frase, (audio, taxa))  # frase curta costuma se repetir
            except Exception as erro:  # noqa: BLE001 - uma frase com defeito não derruba a fala
                self.ultimo_erro = erro
                log.exception("Falha ao sintetizar: %r", frase)
                self._concluir()
                continue
            if len(audio) == 0:
                self._concluir()
                continue
            self._audios.put((geracao, audio, taxa, frase))

    def _laco_som(self) -> None:
        while (item := self._audios.get()) is not None:
            geracao, audio, taxa, frase = item
            desde = 0  # de onde a frase (re)começa: do meio, se uma pausa a cortou com mais de um segundo
            try:
                while self._liberado(geracao):
                    pedaco = audio[desde:] if desde else audio
                    self._ditas.append((self._relogio(), frase))
                    self._avisar(self.ao_tocar, pedaco, taxa)
                    try:
                        inicio, volumes = self._relogio(), medir_volumes(pedaco, taxa)
                        self._som = (inicio, volumes, None)
                    except Exception:  # noqa: BLE001 - sem a medida do volume a fala sai do mesmo jeito
                        log.exception("Não consegui medir o volume da frase")
                        inicio, self._som = self._relogio(), None
                    try:
                        self._tocar(pedaco, taxa)
                    finally:
                        fim = self._relogio()
                        if self._som is not None:
                            self._som = (inicio, volumes, fim)
                    with self._estado:
                        self._tocando = False
                        de_novo, self._cortada = self._cortada, False
                    if not de_novo:
                        break  # tocou até o fim (ou foi interrompida de vez)
                    desde = _retomar_de(desde, len(audio), fim - inicio, taxa)
            except Exception as erro:  # noqa: BLE001
                self.ultimo_erro = erro
                log.exception("Falha ao tocar áudio")
            finally:
                with self._estado:
                    self._tocando = False
                self._concluir()

    def _liberado(self, geracao: int) -> bool:
        """Espera a pausa acabar. Devolve se a frase ainda é para tocar (não houve interrupção)."""
        with self._estado:
            self._estado.wait_for(lambda: not self._segurando or geracao != self._geracao)
            if geracao != self._geracao:
                return False
            self._tocando = True
            return True


def _retomar_de(desde: int, tamanho: int, tocado: float, taxa: int) -> int:
    """De onde uma frase cortada por uma pausa recomeça.

    Frase curta, ou cortada logo no começo, toca de novo inteira. Frase comprida recomeça um pouco
    antes de onde parou: quem ouviu metade não precisa ouvir tudo outra vez.
    """
    if tamanho < RETOMADA_MINIMA * taxa:
        return 0
    ponto = desde + int(tocado * taxa) - int(RETOMADA_FOLGA * taxa)
    if ponto < int(0.5 * taxa):
        return 0  # cortada logo no começo: inteira
    if ponto >= tamanho:
        return max(desde, tamanho - taxa)  # pelo relógio já tinha acabado (a caixa atrasou): só o último segundo
    return ponto


class SaidaDeSom:
    """A caixa de som, aberta uma vez e mantida aberta: as frases saem emendadas.

    Abrir a saída a cada frase (como `sounddevice.play` faz) leva um instante que o Windows cobra
    entre uma frase e outra, e que soa como a fala travando. Aqui a saída fica aberta e o som é
    escrito em pedaços de 50 ms: `parar` pega no pedaço seguinte, e a frase seguinte começa
    sem pausa. Se o dispositivo mudar ou falhar, a saída é fechada e reaberta na frase seguinte.
    """

    def __init__(self, sd: Any = None) -> None:
        if sd is None:
            import sounddevice as sd
        self._sd = sd
        self._fluxo: Any = None
        self._taxa = 0
        self._parar = threading.Event()
        self._trava = threading.Lock()

    def _abrir(self, taxa: int) -> Any:
        with self._trava:
            if self._fluxo is not None and self._taxa == taxa:
                return self._fluxo
            self._fechar()
            fluxo = self._sd.OutputStream(samplerate=taxa, channels=1, dtype="int16", latency="low")
            fluxo.start()
            self._fluxo, self._taxa = fluxo, taxa
            return fluxo

    def _fechar(self) -> None:
        fluxo, self._fluxo = self._fluxo, None
        if fluxo is not None:
            try:
                fluxo.abort()
                fluxo.close()
            except Exception:  # noqa: BLE001 - já estava quebrado
                log.debug("Falha ao fechar a saída de som", exc_info=True)

    def tocar(self, audio: Audio, taxa: int) -> None:
        """Toca até o fim, ou até `parar`. Uma falha na saída tenta uma vez com a saída reaberta."""
        import numpy as np

        som = np.ascontiguousarray(np.asarray(audio).reshape(-1), dtype=np.int16)
        self._parar.clear()
        for tentativa in range(2):
            try:
                fluxo = self._abrir(taxa)
                bloco = max(1, int(taxa * BLOCO_DA_SAIDA))
                for i in range(0, len(som), bloco):
                    if self._parar.is_set():
                        return
                    fluxo.write(som[i : i + bloco])
                return
            except Exception:  # noqa: BLE001 - a saída caiu (fone desligado, dispositivo trocado): reabre e tenta de novo
                with self._trava:
                    self._fechar()
                if tentativa:
                    raise
                log.warning("A saída de som falhou; reabrindo", exc_info=True)

    def parar(self) -> None:
        self._parar.set()

    def fechar(self) -> None:
        self._parar.set()
        with self._trava:
            self._fechar()


def _baixar(url: str, destino: Path, avisar: Callable[[str], None]) -> None:
    """Baixa para um arquivo temporário e só renomeia no fim: download interrompido não deixa lixo."""
    parcial = destino.with_name(destino.name + ".parte")
    with urllib.request.urlopen(url, timeout=60) as resposta, parcial.open("wb") as saida:  # noqa: S310
        total = int(resposta.headers.get("Content-Length") or 0)
        baixado, proximo_aviso = 0, 0
        while bloco := resposta.read(1 << 20):
            saida.write(bloco)
            baixado += len(bloco)
            if total > 5_000_000 and baixado >= proximo_aviso:  # arquivo pequeno não precisa de progresso
                avisar(f"  {destino.name}: {baixado / 1e6:.0f} de {total / 1e6:.0f} MB")
                proximo_aviso = baixado + total // 5
    parcial.replace(destino)


def localizar_voz(voz: str, pasta: Path, avisar: Callable[[str], None] = print) -> Path:
    """Devolve o caminho do modelo .onnx, baixando a voz na primeira vez.

    `voz` é um nome do catálogo do Piper (pt_BR-faber-medium) ou o caminho de um .onnx seu.
    """
    if voz.lower().endswith(".onnx"):
        modelo = Path(voz).expanduser()
        if not modelo.is_file():
            raise FileNotFoundError(f"Arquivo de voz não encontrado: {modelo}")
        return modelo

    achado = _PADRAO_VOZ.match(voz)
    if not achado:
        raise ValueError(f"Nome de voz inválido: {voz!r}. O formato é idioma-nome-qualidade, como pt_BR-faber-medium.")
    partes = achado.groupdict()
    partes["idioma"] = f"{partes['familia']}_{partes['regiao']}"

    pasta.mkdir(parents=True, exist_ok=True)
    modelo = pasta / f"{voz}.onnx"
    for arquivo, extensao in ((modelo, ".onnx"), (pasta / f"{voz}.onnx.json", ".onnx.json")):
        if not arquivo.is_file() or arquivo.stat().st_size == 0:
            avisar(f"Baixando a voz {voz} ({extensao})…")
            _baixar(_URL_VOZ.format(extensao=extensao, **partes), arquivo, avisar)
    return modelo


VELOCIDADE_MINIMA, VELOCIDADE_MAXIMA = 0.5, 2.0  # da fala, em vezes a velocidade natural da voz


class SintesePiper:
    """Uma voz do Piper, pronta para falar. `velocidade` (1 = a natural da voz) vale para as frases seguintes."""

    def __init__(self, modelo: Path) -> None:
        from piper import PiperVoice

        self._voz = PiperVoice.load(modelo)
        self.taxa = self._voz.config.sample_rate
        self.velocidade = 1.0

    def __call__(self, frase: str) -> tuple[Audio, int]:
        import numpy as np

        ajuste = None
        velocidade = min(VELOCIDADE_MAXIMA, max(VELOCIDADE_MINIMA, float(self.velocidade)))
        if abs(velocidade - 1.0) > 0.005:
            from piper import SynthesisConfig

            # No Piper a medida é o comprimento de cada som: mais curto, fala mais depressa.
            natural = float(getattr(self._voz.config, "length_scale", None) or 1.0)
            ajuste = SynthesisConfig(length_scale=natural / velocidade)
        pedacos = [p.audio_int16_array for p in self._voz.synthesize(frase, syn_config=ajuste)]
        if not pedacos:
            return np.zeros(0, dtype=np.int16), self.taxa
        return np.concatenate(pedacos), self.taxa


def sintetizador_piper(modelo: Path) -> Sintetizar:
    return SintesePiper(modelo)


def saida_de_som() -> tuple[Tocar, Callable[[], None]]:
    saida = SaidaDeSom()
    return saida.tocar, saida.parar
