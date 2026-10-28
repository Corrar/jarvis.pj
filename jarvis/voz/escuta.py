"""Escuta: grava do microfone e transcreve na própria máquina (faster-whisper)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TAXA = 16_000  # o Whisper trabalha em 16 kHz, mono
DURACAO_MINIMA = 0.3  # segundos; menos que isso é toque acidental
GANHO_DO_NIVEL = 9.0  # fala normal fica perto de 0,1 de volume; o medidor do painel vai de 0 a 1
# A janela curta da transcrição (ver Transcritor._transcrever_curto).
JANELA_CURTA = 10  # segundos; o mínimo que o modelo recebe, por mais curta que seja a fala
FOLGA_DA_JANELA = 4  # segundos a mais que a fala
MAX_DA_JANELA_CURTA = 20.0  # segundos de áudio; mais que isso vai pela transcrição comum
QUADROS_POR_SEGUNDO = 100  # do espectrograma do Whisper
MAX_DE_FICHAS = 224  # tamanho máximo do texto de uma fala curta, na unidade do modelo


def reamostrar(audio: Any, origem: int, destino: int = TAXA) -> Any:
    """Converte a taxa de amostragem. Simples, mas suficiente para fala."""
    import numpy as np

    if origem == destino or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    janela = int(origem // destino)
    if janela > 1:  # suaviza antes de reduzir, para não criar chiado
        audio = np.convolve(audio, np.ones(janela) / janela, mode="same")
    tamanho = int(round(len(audio) * destino / origem))
    posicoes = np.linspace(0, len(audio) - 1, tamanho)
    return np.interp(posicoes, np.arange(len(audio)), audio).astype(np.float32)


class Gravador:
    """Grava do microfone padrão entre comecar() e terminar()."""

    def __init__(self, sd: Any = None) -> None:
        if sd is None:
            import sounddevice as sd
        self._sd = sd
        self._fluxo: Any = None
        self._blocos: list[Any] = []
        self._taxa = TAXA
        self._trava = threading.Lock()
        self.nivel = 0.0  # volume do último pedaço gravado, de 0 a 1 (para o painel)

    @property
    def gravando(self) -> bool:
        return self._fluxo is not None

    def _receber(self, dados: Any, _quadros: int, _tempo: Any, _estado: Any) -> None:
        with self._trava:
            self._blocos.append(dados.copy())
        try:
            self.nivel = min(1.0, float((dados * dados).mean()) ** 0.5 * GANHO_DO_NIVEL)
        except Exception:  # noqa: BLE001 - o medidor não pode atrapalhar a gravação
            self.nivel = 0.0

    def _abrir(self, taxa: int) -> Any:
        return self._sd.InputStream(samplerate=taxa, channels=1, dtype="float32", callback=self._receber)

    def comecar(self) -> None:
        if self._fluxo is not None:
            return
        with self._trava:
            self._blocos = []
        try:
            self._taxa = TAXA
            fluxo = self._abrir(TAXA)
        except self._sd.PortAudioError:
            # Microfone que não aceita 16 kHz: grava na taxa dele e converte depois.
            self._taxa = int(self._sd.query_devices(kind="input")["default_samplerate"])
            fluxo = self._abrir(self._taxa)
        fluxo.start()
        self._fluxo = fluxo

    def terminar(self) -> Any:
        """Para a gravação e devolve o áudio em float32, mono, 16 kHz."""
        import numpy as np

        fluxo, self._fluxo = self._fluxo, None
        self.nivel = 0.0
        if fluxo is not None:
            fluxo.stop()
            fluxo.close()
        with self._trava:
            blocos, self._blocos = self._blocos, []
        if not blocos:
            return np.zeros(0, dtype=np.float32)
        return reamostrar(np.concatenate(blocos).reshape(-1), self._taxa)


class Transcritor:
    def __init__(self, modelo: str, pasta: Path, fabrica: Any = None, avisar: Any = print) -> None:
        if fabrica is None:
            from faster_whisper import WhisperModel as fabrica
        pasta.mkdir(parents=True, exist_ok=True)
        # CPU com inteiros de 8 bits: roda em qualquer máquina, sem placa de vídeo.
        opcoes = {"device": "cpu", "compute_type": "int8", "download_root": str(pasta)}
        try:
            # Modelo já baixado: carrega do disco, sem consultar a internet.
            self._modelo = fabrica(modelo, local_files_only=True, **opcoes)
        except Exception:  # noqa: BLE001 - primeira vez (ou download incompleto): baixa
            avisar(f"Baixando o modelo de transcrição {modelo}; leva alguns minutos e não mostra progresso…")
            self._modelo = fabrica(modelo, **opcoes)
        self._trava = threading.Lock()  # a vigília e a gravação pelo atalho usam o mesmo modelo
        # A janela curta usa peças do modelo que a transcrição comum não mostra. Sem elas (outra versão da
        # biblioteca), ou depois de uma falha, fica a transcrição comum.
        self._curta = all(hasattr(self._modelo, peca) for peca in ("hf_tokenizer", "feature_extractor", "encode")) and hasattr(
            getattr(self._modelo, "model", None), "generate"
        )
        self._fichas: Any = None  # o tradutor de texto em fichas do modelo, montado no primeiro uso

    def transcrever(self, audio: Any, rapido: bool = False, dica: str = "") -> str:
        """Transcreve o áudio (16 kHz).

        `rapido` troca um pouco de precisão por velocidade: serve para procurar a palavra de ativação.
        `dica` são palavras que podem aparecer (o nome do Jarvis): com ela a transcrição as escreve
        do jeito certo, em vez de algo parecido.

        Fala curta passa pela janela curta (ver `_transcrever_curto`), várias vezes mais rápida. Se
        ela não der conta (áudio comprido, resultado duvidoso, biblioteca diferente da esperada),
        vale a transcrição de sempre.
        """
        if len(audio) < TAXA * DURACAO_MINIMA:
            return ""
        if self._curta and len(audio) <= TAXA * MAX_DA_JANELA_CURTA:
            try:
                with self._trava:
                    texto = self._transcrever_curto(audio, rapido, dica)
            except Exception:  # noqa: BLE001 - a janela curta usa peças internas da biblioteca: se mudaram, fica a de sempre
                log.exception("A transcrição de janela curta falhou; daqui em diante uso a transcrição comum")
                self._curta, texto = False, None
            if texto is not None:
                return texto
        return self._transcrever_inteiro(audio, rapido, dica)

    def _transcrever_curto(self, audio: Any, rapido: bool, dica: str) -> str | None:
        """Transcreve uma fala curta sem pagar pelos trinta segundos que o Whisper sempre processa.

        O modelo trabalha numa janela fixa de 30 s, por mais curta que seja a fala: é esse o custo
        de cada transcrição. Aqui a janela é só a fala e uma folga. Medido com o modelo `small`
        em português, com o mesmo acerto: de três a quatro vezes mais rápido.

        Devolve None quando o resultado não merece confiança (a cuidadosa então repete do jeito
        de sempre) e "" quando não há fala no áudio. Rápida ou cuidadosa, aqui a procura pelo texto
        é a mesma: com a janela curta o que custa é ouvir, não escrever, e a procura mais larga
        acerta mais vezes o nome dito sozinho.
        """
        import zlib

        import numpy as np
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        modelo = self._modelo
        # Só o que é voz, como na transcrição comum: silêncio e barulho o Whisper costuma "inventar".
        falas = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=160))
        if not falas:
            return ""
        voz = np.concatenate([audio[fala["start"] : fala["end"]] for fala in falas])
        if len(voz) < TAXA * DURACAO_MINIMA:
            return ""
        if self._fichas is None:
            self._fichas = Tokenizer(modelo.hf_tokenizer, modelo.model.is_multilingual, task="transcribe", language="pt")
        fichas = self._fichas
        janela = min(30, max(JANELA_CURTA, int(np.ceil(len(voz) / TAXA)) + FOLGA_DA_JANELA))
        tracos = pad_or_trim(modelo.feature_extractor(voz), janela * QUADROS_POR_SEGUNDO)
        ouvido = modelo.encode(tracos)
        comeco: list[int] = []
        if dica.strip():
            comeco = [fichas.sot_prev, *fichas.encode(" " + dica.strip())]
        comeco = [*comeco, *fichas.sot_sequence, fichas.no_timestamps]
        saida = modelo.model.generate(
            ouvido,
            [comeco],
            beam_size=5,
            max_length=len(comeco) + MAX_DE_FICHAS,
            return_scores=True,
            return_no_speech_prob=True,
        )[0]
        escritas = list(saida.sequences_ids[0])
        texto = fichas.decode(escritas).strip()
        nota = float(saida.scores[0]) * len(escritas) / (len(escritas) + 1) if escritas else 0.0
        if float(saida.no_speech_prob) > 0.6 and nota < -1.0:
            return ""  # o próprio modelo diz que não era fala
        if not rapido and texto:
            bruto = texto.encode("utf-8")
            if nota < -1.0 or len(bruto) / len(zlib.compress(bruto)) > 2.4:
                return None  # texto pouco provável, ou repetido em laço: a transcrição comum tenta de outro jeito
        return texto

    def _transcrever_inteiro(self, audio: Any, rapido: bool, dica: str) -> str:
        opcoes: dict[str, Any] = {}
        if dica:
            opcoes["hotwords"] = dica
        if rapido:
            # Uma passada só e sem marcar o tempo de cada palavra: a procura tem de ser curta e de duração previsível.
            opcoes.update(temperature=0.0, without_timestamps=True)
        with self._trava:
            segmentos, _info = self._modelo.transcribe(
                audio,
                language="pt",
                beam_size=1 if rapido else 5,
                vad_filter=True,  # descarta silêncio, que o Whisper costuma "inventar"
                condition_on_previous_text=False,
                **opcoes,
            )
            return " ".join(s.text.strip() for s in segmentos).strip()
