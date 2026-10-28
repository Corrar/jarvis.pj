"""Voz do Jarvis: escuta (microfone → texto) e fala (texto → som), tudo local."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.voz.frases import Separador, limpar_para_fala, tem_o_que_falar

DEPENDENCIAS = "pip install -r requirements-voz.txt"


class ErroVoz(Exception):
    """Problema de áudio ou de modelo; a mensagem é mostrada ao usuário."""


class Voz:
    """Junta as três peças. Recebe-as prontas, o que permite testar sem áudio de verdade."""

    def __init__(self, falante: Any, gravador: Any, transcritor: Any, ouvinte: Any = None) -> None:
        self._falante = falante
        self._gravador = gravador
        self._transcritor = transcritor
        self._ouvinte = ouvinte or transcritor  # quem transcreve para a palavra de ativação (pode ser um modelo menor)
        self._separador = Separador()
        self._calada = False  # alguém chamou no meio da resposta: o resto dela não é falado
        self.muda = False  # o usuário desligou a voz no painel: ele não fala nada até religarem
        self._pasta_de_vozes: Path | None = None  # onde ficam as vozes baixadas, e qual delas é a de sempre
        self._voz_padrao: Path | None = None
        self._voz_atual = ""  # o nome da voz em uso, se não for a de sempre
        self._velocidade = 1.0
        self._trava_da_voz = threading.Lock()  # uma troca de voz de cada vez
        self.vigia: Any = None  # a escuta contínua, quando a palavra de ativação está ligada
        self.dono: Any = None  # o cadastro da voz do usuário (voz/dono.py), quando a conferência está ligada
        self.nuvem: Any = None  # a voz da nuvem (voz/nuvem.py), quando há chave da ElevenLabs; a local fica de reserva

    @classmethod
    def carregar(
        cls, voz: str, whisper: str, pasta: Path, avisar: Callable[[str], None] = print, whisper_da_vigilia: str = "", nuvem: dict[str, Any] | None = None
    ) -> Voz:
        """Carrega os modelos. Na primeira vez baixa a voz e o modelo de transcrição.

        `nuvem` (chave, voz, modelo, limite) liga a ElevenLabs por cima da voz local, que fica de reserva.
        """
        # Sem modo desenvolvedor o Windows não cria atalhos simbólicos; o download
        # funciona do mesmo jeito, então o aviso longo da biblioteca só atrapalha.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
            import piper  # noqa: F401
            import sounddevice  # noqa: F401
        except ImportError as erro:
            raise ErroVoz(f"Falta instalar as bibliotecas de voz ({erro.name}). Rode: {DEPENDENCIAS}") from erro
        except OSError as erro:  # sounddevice sem a biblioteca de áudio do sistema
            raise ErroVoz(f"A biblioteca de áudio não carregou: {erro}") from erro

        from jarvis.voz import escuta, fala

        try:
            avisar(f"Carregando a voz {voz}…")
            modelo_da_voz = fala.localizar_voz(voz, pasta / "vozes", avisar)
            sintetizar: Any = fala.sintetizador_piper(modelo_da_voz)
            da_nuvem = None
            if nuvem and nuvem.get("chave"):
                from jarvis.voz.nuvem import ErroNuvem, SinteseNuvem

                da_nuvem = SinteseNuvem(
                    nuvem["chave"], sintetizar, pasta, voz=nuvem.get("voz", ""), modelo=nuvem.get("modelo", "flash"),
                    limite=int(nuvem.get("limite", 9000)), avisar=avisar,
                )  # fmt: skip
                try:
                    da_nuvem.preparar()
                except ErroNuvem as erro:
                    da_nuvem.desligada = str(erro)
                    avisar(f"[aviso] Voz da nuvem (ElevenLabs) desligada: {erro}. Sigo com a voz local {voz}.")
                except Exception as erro:  # noqa: BLE001 - rede, formato: a voz local segue
                    da_nuvem.desligada = f"{type(erro).__name__}: {erro}"
                    avisar(f"[aviso] Voz da nuvem (ElevenLabs) desligada: {erro}. Sigo com a voz local {voz}.")
                else:
                    avisar(da_nuvem.situacao())
                sintetizar = da_nuvem
            avisar(f"Carregando a transcrição (whisper {whisper})…")
            transcritor = escuta.Transcritor(whisper, pasta / "whisper", avisar=avisar)
            ouvinte = None
            if whisper_da_vigilia and whisper_da_vigilia != whisper:
                avisar(f"Carregando a transcrição da palavra de ativação (whisper {whisper_da_vigilia})…")
                ouvinte = escuta.Transcritor(whisper_da_vigilia, pasta / "whisper", avisar=avisar)
            tocar, parar_som = fala.saida_de_som()
            pronta = cls(fala.Falante(sintetizar, tocar, parar_som), escuta.Gravador(), transcritor, ouvinte)
            pronta._pasta_de_vozes, pronta._voz_padrao, pronta.nuvem = pasta / "vozes", modelo_da_voz, da_nuvem
            return pronta
        except ErroVoz:
            raise
        except Exception as erro:  # noqa: BLE001 - rede, arquivo, modelo: tudo vira mensagem clara
            raise ErroVoz(f"Não consegui preparar a voz: {erro}") from erro

    # --- fala -------------------------------------------------------------

    def narrar(self, trecho: str) -> None:
        """Recebe a resposta aos pedaços e manda falar cada frase que fechar."""
        for frase in self._separador.alimentar(trecho):
            self._falar(frase)

    def fim_de_bloco(self) -> None:
        for frase in self._separador.descarregar():
            self._falar(frase)

    def _falar(self, frase: str) -> None:
        limpa = limpar_para_fala(frase)
        if tem_o_que_falar(limpa) and not self._calada and not self.muda:
            self._falante.falar(limpa)

    def interromper(self) -> None:
        self._separador = Separador()
        self._falante.interromper()

    def dizer(self, frase: str) -> None:
        """Fala uma frase curta e fixa, do jeito que está ("Sim?"). As que já foram ditas antes saem na hora."""
        if not self.muda:
            self._falante.falar(frase)

    # --- os ajustes de voz do painel ------------------------------------------

    def emudecer(self, sim: bool) -> None:
        """Desliga (ou religa) a fala. Mudo, ele não diz nada: nem a resposta, nem o "Sim?". Continua ouvindo."""
        self.muda = bool(sim)
        if self.muda:
            self.interromper()

    def velocidade(self, valor: float) -> None:
        """A velocidade da fala, em vezes a natural da voz. Vale da próxima frase em diante."""
        self._velocidade = float(valor)
        mudar = getattr(self._falante, "velocidade", None)
        if mudar is not None:
            mudar(self._velocidade)

    @property
    def voz_atual(self) -> str:
        """O nome da voz em uso; vazio se é a de sempre."""
        return self._voz_atual

    def vozes(self) -> list[str]:
        """Os nomes das outras vozes já baixadas nesta máquina (a de sempre não entra)."""
        pasta, padrao = self._pasta_de_vozes, self._voz_padrao
        if pasta is None or not pasta.is_dir():
            return []
        nomes = []
        for modelo in sorted(pasta.glob("*.onnx")):
            try:
                completa = modelo.stat().st_size > 0 and modelo.with_name(modelo.name + ".json").is_file()
            except OSError:
                completa = False
            if completa and modelo != padrao:
                nomes.append(modelo.stem)
        return nomes

    def trocar_voz(self, nome: str) -> None:
        """Passa a falar com outra voz baixada ("" volta para a de sempre). Carregar leva um instante.

        Levanta ErroVoz se a voz não existe ou não carrega; a voz em uso continua valendo.
        """
        from jarvis.voz import fala

        with self._trava_da_voz:
            if nome == self._voz_atual:
                return
            if nome and nome not in self.vozes():
                raise ErroVoz(f"Não tenho a voz {nome!r} baixada.")
            modelo = self._voz_padrao if not nome else (self._pasta_de_vozes / f"{nome}.onnx" if self._pasta_de_vozes else None)
            if modelo is None:
                raise ErroVoz("Esta sessão não sabe onde ficam as vozes.")
            try:
                sintese = fala.sintetizador_piper(modelo)
            except Exception as erro:  # noqa: BLE001 - arquivo corrompido, biblioteca: vira mensagem clara
                raise ErroVoz(f"Não consegui carregar a voz {nome or 'padrão'}: {erro}") from erro
            sintese.velocidade = self._velocidade  # type: ignore[attr-defined]
            if self.nuvem is not None:  # a voz local trocada vira a reserva da nuvem
                self.nuvem.reserva = sintese
                sintese = self.nuvem
            self._falante.trocar_sintese(sintese)
            self._voz_atual = nome

    def preparar(self, frases: Any) -> None:
        """Deixa prontas as frases curtas que precisam sair sem demora (a resposta ao nome)."""
        preparar = getattr(self._falante, "preparar", None)
        if preparar is not None:
            preparar(frases)

    def pausar_fala(self) -> None:
        """Cala sem perder o fio: alguém falou por cima e é preciso ouvir. `retomar_fala` continua da frase cortada."""
        self._falante.pausar()

    def retomar_fala(self) -> None:
        self._falante.retomar()

    def calar(self) -> None:
        """Cala e fica calado até `soltar`: o que a resposta em curso ainda mandar narrar não é falado.

        É o que acontece quando alguém chama o Jarvis no meio de uma resposta. O que é dito com
        `dizer` (a resposta ao nome) sai do mesmo jeito.
        """
        self._calada = True
        self.interromper()

    def soltar(self) -> None:
        """Um pedido novo começou: a narração volta a valer."""
        self._calada = False

    def ditas(self) -> list[str]:
        """O que ele disse nos últimos segundos, frase a frase."""
        ditas = getattr(self._falante, "ditas", None)
        return list(ditas()) if ditas is not None else []

    def falou_ha(self) -> float:
        """Há quantos segundos ele terminou de falar (zero se está falando; infinito se não falou)."""
        falou_ha = getattr(self._falante, "falou_ha", None)
        return float(falou_ha()) if falou_ha is not None else (0.0 if self.falando else float("inf"))

    def tocado(self) -> float:
        """O volume do que está saindo da caixa de som agora (zero se nada toca)."""
        tocado = getattr(self._falante, "tocado", None)
        return float(tocado()) if tocado is not None else 0.0

    def esperar_fala(self, tempo: float | None = None) -> bool:
        return self._falante.esperar(tempo)

    def fechar(self) -> None:
        self._falante.fechar()

    def observar(self, ao_tocar: Callable[[Any, int], None] | None, ao_calar: Callable[[], None] | None) -> None:
        """Quem quer acompanhar a fala: recebe o áudio de cada frase quando ela começa e um aviso quando acaba."""
        self._falante.ao_tocar, self._falante.ao_calar = ao_tocar, ao_calar

    def nivel_do_microfone(self) -> float:
        """Volume do que está sendo gravado agora, de 0 a 1."""
        return float(getattr(self._gravador, "nivel", 0.0) or 0.0)

    @property
    def falando(self) -> bool:
        return bool(getattr(self._falante, "falando", False))

    # --- escuta -----------------------------------------------------------

    def comecar_gravacao(self) -> None:
        self.interromper()  # não grava a própria voz saindo da caixa de som
        if self.vigia is not None:
            self.vigia.suspender()  # um microfone, um dono de cada vez
        try:
            self._gravador.comecar()
        except Exception as erro:  # noqa: BLE001
            if self.vigia is not None:
                self.vigia.retomar()
            raise ErroVoz(f"Não consegui abrir o microfone: {erro}") from erro

    def terminar_gravacao(self) -> str:
        """Para de gravar e devolve o que foi dito (vazio se não houve fala)."""
        audio = self.terminar_gravacao_bruta()
        try:
            return self._transcritor.transcrever(audio)
        except Exception as erro:  # noqa: BLE001
            raise ErroVoz(f"Falha ao transcrever: {erro}") from erro

    def terminar_gravacao_bruta(self) -> Any:
        """Para de gravar e devolve o áudio (16 kHz, float32), para o cadastro da voz."""
        try:
            return self._gravador.terminar()
        except Exception as erro:  # noqa: BLE001
            raise ErroVoz(f"Falha ao transcrever: {erro}") from erro
        finally:
            if self.vigia is not None:
                self.vigia.retomar()

    # --- palavra de ativação ------------------------------------------------

    def _ouvir_trecho(self, audio: Any, rapido: bool = False, dica: str = "") -> str:
        # A procura pelo nome vai para o modelo da vigília; o pedido inteiro, para o modelo principal.
        return (self._ouvinte if rapido else self._transcritor).transcrever(audio, rapido=rapido, dica=dica)

    def vigiar(
        self, palavras: Any, ao_pedir: Callable[[str], None], fabrica: Any = None, impedido: Callable[[], bool] | None = None, **avisos: Any
    ) -> None:
        """Abre o microfone de vez e passa a chamar `ao_pedir(texto)` quando alguém chamar o Jarvis pelo nome.

        `impedido()` verdadeiro suspende a escuta (a tela bloqueada, por exemplo). Enquanto o Jarvis fala ela
        continua, mas só ouve quem falar mais alto do que a voz dele chega ao microfone.
        """
        if fabrica is None:
            from jarvis.voz.vigilia import Vigia as fabrica

        vigia = fabrica(
            self._ouvir_trecho,
            ao_pedir,
            palavras=palavras,
            falando=lambda: self.falando,
            tocado=self.tocado,
            ditas=self.ditas,
            falou_ha=self.falou_ha,
            impedido=impedido or (lambda: False),
            leve=self._ouvinte is not self._transcritor,
            dono=self.dono.conferir if self.dono is not None else None,
            **avisos,
        )
        try:
            vigia.iniciar()
        except Exception as erro:  # noqa: BLE001
            raise ErroVoz(f"Não consegui abrir o microfone para a palavra de ativação: {erro}") from erro
        self.vigia = vigia

    def ensaiar_chamado(self, palavra: str) -> tuple[float, str] | None:
        """Diz o nome com a voz do Jarvis (sem tocar) e passa pela transcrição da palavra de ativação.

        Devolve (segundos que a transcrição levou, o que ela entendeu), ou None se não deu para
        ensaiar. Feito uma vez na partida, deixa o modelo aquecido (o primeiro chamado de verdade
        não paga a carga dele) e mostra quanto tempo esta máquina leva para reconhecer um chamado.
        """
        import numpy as np

        from jarvis.voz.escuta import TAXA, reamostrar

        amostra = getattr(self._falante, "amostra", None)
        pronta = amostra(f"{palavra.title()}.") if amostra is not None else None
        if pronta is None:
            return None
        audio, taxa = pronta
        som = np.asarray(audio)
        inteiro = np.issubdtype(som.dtype, np.integer)
        som = som.astype(np.float32).reshape(-1)
        if inteiro:
            som = som / 32768.0
        pausa = np.zeros(int(0.3 * TAXA), dtype=np.float32)
        som = np.concatenate([pausa, reamostrar(som, taxa), pausa])
        leve, dica = self._ouvinte is not self._transcritor, palavra.title()
        self._ouvir_trecho(som, rapido=leve, dica=dica)  # a primeira vez carrega o modelo: não é ela que se mede
        inicio = time.perf_counter()
        texto = self._ouvir_trecho(som, rapido=leve, dica=dica)
        return time.perf_counter() - inicio, texto

    def parar_vigilia(self) -> None:
        vigia, self.vigia = self.vigia, None
        if vigia is not None:
            vigia.parar()
