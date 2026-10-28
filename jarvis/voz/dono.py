"""A voz do dono: o Jarvis aprende como o usuário fala e só atende pelo nome a essa voz.

Cada fala vira um vetor (uma "assinatura" da voz, calculada nesta máquina por um modelo de
reconhecimento de locutor, sem internet depois de baixado). O cadastro guarda os vetores de
algumas frases ditas pelo usuário; a cada chamado, o vetor do que foi ouvido é comparado ao
cadastro. Parecido o bastante, é ele; senão, o chamado é ignorado (e aparece no /ouvido).

Nada de áudio é guardado: só os vetores, em um arquivo da pasta de dados. O cadastro melhora
com o uso: um chamado aceito com folga entra no cadastro, e a voz do usuário vai sendo
aprendida nas condições reais (o microfone dele, a sala dele).

Medido com vozes sintéticas em português (ver tests): a mesma voz pontua 0,5 ou mais contra
o cadastro; outra voz, 0,35 no máximo, mesmo em frases de uma palavra só. O limiar padrão,
0,40, fica entre os dois; com ruído forte a própria voz pode cair perto dele, por isso o
cadastro aprende e o limiar é ajustável (JARVIS_LIMIAR_DA_VOZ).
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.voz.escuta import TAXA

log = logging.getLogger(__name__)

MODELO = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
ENDERECO = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{MODELO}"
TAMANHO_MINIMO = 20 * 1024 * 1024  # bytes; o modelo tem uns 26 MB: menos que isso é download pela metade
LIMIAR = 0.40  # semelhança mínima para ser o dono
LIMIAR_MINIMO, LIMIAR_MAXIMO = 0.20, 0.80
APRENDER = 0.60  # um chamado aceito com esta folga entra no cadastro
MAX_VETORES = 40  # quantos vetores o cadastro guarda (os primeiros são os do cadastro feito à mão)
DURACAO_MINIMA = 0.4  # segundos de voz para dar para comparar
DURACAO_DO_CADASTRO = 1.0  # segundos mínimos de cada frase do cadastro
PARECIDAS_NO_CADASTRO = 0.35  # as frases do cadastro precisam parecer umas com as outras (mesma pessoa, mesmo microfone)
FRASES_DE_CADASTRO = (
    "Jarvis, que horas são agora?",
    "Jarvis, abre o YouTube para mim, por favor.",
    "Me lembre de ligar para o dentista às três da tarde.",
    "Jarvis, como está o tempo hoje?",
)


class ErroDono(Exception):
    """Falha no cadastro ou na conferência da voz; a mensagem é mostrada ao usuário."""


def baixar(endereco: str, destino: Path, avisar: Callable[[str], None] = print, espera: float = 60.0) -> None:
    """Baixa um arquivo grande, em pedaços, para um nome provisório; só no fim ele vira o definitivo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    provisorio = destino.with_suffix(destino.suffix + ".parte")
    avisar(f"Baixando o modelo de reconhecimento de voz ({destino.name}, uns 26 MB)…")
    pedido = urllib.request.Request(endereco, headers={"User-Agent": "jarvis-desktop"})
    with urllib.request.urlopen(pedido, timeout=espera) as resposta, provisorio.open("wb") as saida:  # noqa: S310 - endereço fixo, https
        while True:
            pedaco = resposta.read(1 << 20)
            if not pedaco:
                break
            saida.write(pedaco)
    if provisorio.stat().st_size < TAMANHO_MINIMO:
        provisorio.unlink(missing_ok=True)
        raise ErroDono("O download do modelo veio pela metade. Confira a internet e tente de novo.")
    provisorio.replace(destino)


class VozDoDono:
    """O cadastro da voz do usuário e a conferência de cada chamado."""

    def __init__(
        self,
        pasta_de_modelos: Path,
        arquivo: Path,
        limiar: float = LIMIAR,
        extrator: Callable[[Path], Any] | None = None,
        baixar_modelo: Callable[[str, Path, Callable[[str], None]], None] = baixar,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pasta_de_modelos, self.arquivo = pasta_de_modelos, arquivo
        self.limiar = min(LIMIAR_MAXIMO, max(LIMIAR_MINIMO, float(limiar)))
        self._extrator_fabrica, self._baixar, self._relogio = extrator, baixar_modelo, relogio
        self._extrator: Any = None
        self._trava = threading.Lock()
        self._vetores: Any = None  # matriz (n x d) dos vetores do cadastro
        self._perfil: Any = None  # a média deles, normalizada
        self._gravado_em = float("-inf")
        self.aceitos, self.recusados = 0, 0
        self.notas: deque[tuple[float, bool]] = deque(maxlen=12)  # as últimas conferências: (nota, aceito)
        self._carregar()

    # --- cadastro em disco ----------------------------------------------------------------

    def _carregar(self) -> None:
        import numpy as np

        try:
            with np.load(self.arquivo) as dados:
                vetores = np.asarray(dados["vetores"], dtype=np.float32)
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001 - arquivo estragado: é como não ter cadastro
            log.exception("Não consegui ler o cadastro da voz em %s", self.arquivo)
            return
        if vetores.ndim == 2 and len(vetores):
            self._definir(vetores)

    def _definir(self, vetores: Any) -> None:
        import numpy as np

        self._vetores = vetores[-MAX_VETORES:]
        media = self._vetores.mean(axis=0)
        self._perfil = media / (np.linalg.norm(media) + 1e-9)

    def _gravar(self) -> None:
        import numpy as np

        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        provisorio = self.arquivo.with_suffix(".npz.novo")
        with provisorio.open("wb") as saida:
            np.savez(saida, vetores=self._vetores)
        provisorio.replace(self.arquivo)
        self._gravado_em = self._relogio()

    @property
    def tem_perfil(self) -> bool:
        return self._perfil is not None

    @property
    def quantos(self) -> int:
        return 0 if self._vetores is None else int(len(self._vetores))

    def apagar(self) -> None:
        with self._trava:
            self._vetores = self._perfil = None
            self.arquivo.unlink(missing_ok=True)

    # --- o modelo --------------------------------------------------------------------------

    @property
    def modelo(self) -> Path:
        return self.pasta_de_modelos / MODELO

    @property
    def pronto(self) -> bool:
        return self._extrator is not None

    def preparar(self, avisar: Callable[[str], None] = print) -> None:
        """Baixa o modelo (uma vez) e o carrega. Levanta ErroDono com a razão, se não der."""
        with self._trava:
            if self._extrator is not None:
                return
            if not self.modelo.is_file():
                try:
                    self._baixar(ENDERECO, self.modelo, avisar)
                except ErroDono:
                    raise
                except Exception as erro:  # noqa: BLE001 - rede, disco
                    raise ErroDono(f"Não consegui baixar o modelo de reconhecimento de voz ({type(erro).__name__}: {erro}).") from None
            try:
                self._extrator = (self._extrator_fabrica or _extrator_sherpa)(self.modelo)
            except ImportError as erro:
                raise ErroDono(f"Falta a biblioteca de reconhecimento de voz ({erro.name}). Rode: pip install -r requirements-voz.txt") from None
            except Exception as erro:  # noqa: BLE001
                raise ErroDono(f"Não consegui carregar o modelo de reconhecimento de voz ({type(erro).__name__}: {erro}).") from None

    def vetor(self, audio: Any) -> Any:
        """A assinatura de uma fala (16 kHz, float32), normalizada."""
        import numpy as np

        if self._extrator is None:
            raise ErroDono("O modelo de reconhecimento de voz não está carregado.")
        som = np.asarray(audio, dtype=np.float32).reshape(-1)
        if len(som) < DURACAO_MINIMA * TAXA:
            raise ErroDono("Fala curta demais para reconhecer a voz.")
        vetor = np.asarray(self._extrator(som), dtype=np.float32).reshape(-1)
        return vetor / (np.linalg.norm(vetor) + 1e-9)

    # --- cadastrar e conferir --------------------------------------------------------------

    def cadastrar(self, amostras: list[Any]) -> int:
        """Guarda as assinaturas das frases do cadastro (substitui o cadastro anterior). Devolve quantas ficaram."""
        import numpy as np

        if not amostras:
            raise ErroDono("Nenhuma gravação para cadastrar.")
        vetores = []
        for amostra in amostras:
            som = np.asarray(amostra, dtype=np.float32).reshape(-1)
            if len(som) < DURACAO_DO_CADASTRO * TAXA:
                raise ErroDono("Uma das gravações ficou curta demais (menos de um segundo de fala). Grave de novo.")
            vetores.append(self.vetor(som))
        matriz = np.stack(vetores)
        if len(matriz) > 1:
            parecencas = matriz @ matriz.T
            minima = float(min(parecencas[i, j] for i in range(len(matriz)) for j in range(i + 1, len(matriz))))
            if minima < PARECIDAS_NO_CADASTRO:
                raise ErroDono(
                    f"As gravações ficaram diferentes demais entre si (semelhança {minima:.2f}). Grave de novo, no mesmo lugar, "
                    "com o mesmo microfone e sem barulho em volta."
                )
        with self._trava:
            self._definir(matriz)
            self._gravar()
            self.aceitos = self.recusados = 0
            self.notas.clear()
        return len(matriz)

    def conferir(self, audio: Any) -> tuple[bool, float]:
        """É o dono falando? Devolve (sim ou não, a semelhança com o cadastro).

        Sem cadastro, ou sem o modelo carregado, todo mundo é o dono (não há com o que comparar).
        Um chamado aceito com folga entra no cadastro, para o Jarvis conhecer cada vez melhor a voz.
        """
        import numpy as np

        with self._trava:
            if self._perfil is None or self._extrator is None:
                return True, float("nan")
            vetor = self.vetor(audio)
            nota = float(np.dot(self._perfil, vetor))
            aceito = nota >= self.limiar
            self.notas.append((nota, aceito))
            if aceito:
                self.aceitos += 1
                if nota >= APRENDER:
                    self._definir(np.vstack([self._vetores, vetor[None, :]]))
                    if self._relogio() - self._gravado_em > 60.0:  # não grava a cada chamado
                        try:
                            self._gravar()
                        except OSError:
                            log.exception("Não consegui gravar o cadastro da voz")
            else:
                self.recusados += 1
            return aceito, nota


def _extrator_sherpa(modelo: Path) -> Callable[[Any], Any]:
    import sherpa_onnx

    extrator = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(modelo), num_threads=2))

    def calcular(som: Any) -> Any:
        fluxo = extrator.create_stream()
        fluxo.accept_waveform(TAXA, som)
        fluxo.input_finished()
        return extrator.compute(fluxo)

    return calcular
