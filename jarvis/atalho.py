"""Tecla de atalho global: o usuário aperta a combinação em qualquer janela e o Jarvis escuta.

Só Windows. Usa o registro de atalhos do próprio sistema (RegisterHotKey): o Windows avisa
quando aquela combinação é apertada e mais nada. O Jarvis não observa o resto do teclado.
"""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PADRAO = "ctrl+alt+j"
DESLIGADO = ("nao", "não", "n", "0", "false", "desligado", "nenhum")

_MODIFICADORES = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
_NOMES = {0x0002: "Ctrl", 0x0001: "Alt", 0x0004: "Shift", 0x0008: "Win"}
_TECLAS = {
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},  # letras: o código é o da maiúscula
    **{str(n): 0x30 + n for n in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    "espaco": 0x20, "espaço": 0x20, "space": 0x20,
}  # fmt: skip

_SEM_REPETIR = 0x4000  # segurar a tecla conta uma vez só
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012
_JA_EM_USO = 1409


class ErroAtalho(Exception):
    """Combinação inválida ou que o Windows não aceitou; a mensagem é mostrada ao usuário."""


@dataclass(frozen=True)
class Combinacao:
    modificadores: int
    tecla: int
    texto: str  # como mostrar: "Ctrl+Alt+J"


def ler_combinacao(bruto: str | None) -> Combinacao | None:
    """'ctrl+alt+j' → Combinacao. Vazio usa o padrão; 'nao' desliga (None)."""
    texto = (bruto or "").strip().lower() or PADRAO
    if texto in DESLIGADO:
        return None
    partes = [p.strip() for p in texto.split("+")]
    *mods, tecla = partes
    if not tecla or tecla not in _TECLAS or any(m not in _MODIFICADORES for m in mods) or len(set(mods)) != len(mods):
        raise ErroAtalho(
            f"JARVIS_ATALHO inválido: '{bruto}'. Use ctrl, alt, shift ou win e uma tecla (letra, número, f1 a f12 ou espaco), "
            "por exemplo ctrl+alt+j. Para desligar: nao."
        )
    modificadores = 0
    for m in mods:
        modificadores |= _MODIFICADORES[m]
    de_funcao = tecla.startswith("f") and len(tecla) > 1
    if not modificadores & ~_MODIFICADORES["shift"] and not de_funcao:
        # uma letra sozinha (ou só com Shift) como atalho global impediria de digitar essa letra
        raise ErroAtalho(f"JARVIS_ATALHO inválido: '{bruto}'. Junte ctrl, alt ou win à tecla, ou use uma tecla de função (f1 a f12).")
    nomes = [_NOMES[bit] for bit in (0x0002, 0x0001, 0x0004, 0x0008) if modificadores & bit]
    return Combinacao(modificadores, _TECLAS[tecla], "+".join([*nomes, tecla.upper() if len(tecla) <= 3 else "Espaço"]))


class Atalho:
    """Registra a combinação no Windows e chama `ao_apertar` a cada toque (de outra thread)."""

    def __init__(self, combinacao: Combinacao, ao_apertar: Callable[[], None], user32: Any = None, kernel32: Any = None) -> None:
        self.combinacao = combinacao
        self._ao_apertar = ao_apertar
        self._u, self._k = user32, kernel32
        self._thread: threading.Thread | None = None
        self._id_da_thread = 0
        self._pronto = threading.Event()
        self._erro: str | None = None

    def iniciar(self, espera: float = 3.0) -> None:
        """Liga o atalho. Levanta ErroAtalho se o Windows não aceitar a combinação."""
        if self._u is None:
            self._u = ctypes.WinDLL("user32", use_last_error=True)
            self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread = threading.Thread(target=self._laco, daemon=True, name="jarvis-atalho")
        self._thread.start()
        if not self._pronto.wait(espera):
            raise ErroAtalho("o registro do atalho não respondeu")
        if self._erro is not None:
            raise ErroAtalho(self._erro)

    def _laco(self) -> None:
        # O atalho pertence à thread que o registrou: é ela que precisa ficar recebendo os avisos.
        from ctypes import wintypes

        u = self._u
        try:
            self._id_da_thread = int(self._k.GetCurrentThreadId())
            if not u.RegisterHotKey(None, 1, self.combinacao.modificadores | _SEM_REPETIR, self.combinacao.tecla):
                codigo = ctypes.get_last_error()
                self._erro = (
                    f"{self.combinacao.texto} já está em uso por outro programa; escolha outra combinação em JARVIS_ATALHO"
                    if codigo == _JA_EM_USO
                    else f"o Windows recusou o atalho {self.combinacao.texto} (erro {codigo})"
                )
                return
        except Exception as erro:  # noqa: BLE001 - qualquer falha vira mensagem para o usuário
            self._erro = f"não consegui registrar o atalho: {type(erro).__name__}: {erro}"
            return
        finally:
            self._pronto.set()
        mensagem = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(mensagem), None, 0, 0) > 0:
                if mensagem.message == _WM_HOTKEY:
                    try:
                        self._ao_apertar()
                    except Exception:  # noqa: BLE001 - quem recebe o aviso não pode derrubar a escuta
                        pass
        finally:
            u.UnregisterHotKey(None, 1)

    def parar(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._id_da_thread:
            self._u.PostThreadMessageW(self._id_da_thread, _WM_QUIT, 0, 0)
            self._thread.join(1.0)
        self._thread = None
