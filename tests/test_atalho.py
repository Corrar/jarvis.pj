import ctypes
import queue
import threading
from types import SimpleNamespace

import pytest

from jarvis import atalho
from jarvis.atalho import Atalho, Combinacao, ErroAtalho, ler_combinacao


@pytest.mark.parametrize(
    "bruto, esperado",
    [
        (None, Combinacao(0x0003, 0x4A, "Ctrl+Alt+J")),
        ("", Combinacao(0x0003, 0x4A, "Ctrl+Alt+J")),
        (" Ctrl + Alt + J ", Combinacao(0x0003, 0x4A, "Ctrl+Alt+J")),
        ("alt+ctrl+j", Combinacao(0x0003, 0x4A, "Ctrl+Alt+J")),
        ("ctrl+shift+espaco", Combinacao(0x0006, 0x20, "Ctrl+Shift+Espaço")),
        ("f9", Combinacao(0, 0x78, "F9")),
        ("shift+f12", Combinacao(0x0004, 0x7B, "Shift+F12")),
        ("win+alt+1", Combinacao(0x0009, 0x31, "Alt+Win+1")),
    ],
)
def test_ler_combinacao(bruto, esperado):
    assert ler_combinacao(bruto) == esperado


@pytest.mark.parametrize("bruto", ["nao", "NÃO", " desligado ", "0"])
def test_atalho_desligado(bruto):
    assert ler_combinacao(bruto) is None


@pytest.mark.parametrize("bruto", ["j", "shift+j", "ctrl+alt", "ctrl+", "ctrl+alt+enter", "meta+j", "ctrl+ctrl+j", "ctrl+f13", "+"])
def test_combinacao_invalida(bruto):
    with pytest.raises(ErroAtalho, match="JARVIS_ATALHO inválido"):
        ler_combinacao(bruto)


class Windows:
    """user32 e kernel32 de mentira: guardam o atalho registrado e entregam os avisos pela fila de mensagens."""

    def __init__(self, recusa: int | None = None):
        self.recusa, self.registrado, self.cancelado = recusa, None, False
        self.fila: queue.Queue[int] = queue.Queue()
        self.thread_do_registro = None

    def RegisterHotKey(self, janela, identificador, modificadores, tecla):  # noqa: N802
        if self.recusa is not None:
            ctypes.set_last_error(self.recusa)
            return 0
        self.registrado = (janela, identificador, modificadores, tecla)
        self.thread_do_registro = threading.get_ident()
        return 1

    def GetMessageW(self, mensagem, janela, minimo, maximo):  # noqa: N802
        assert threading.get_ident() == self.thread_do_registro  # o aviso chega na thread que registrou
        codigo = self.fila.get()
        mensagem._obj.message = codigo
        return 0 if codigo == 0x0012 else 1

    def UnregisterHotKey(self, janela, identificador):  # noqa: N802
        self.cancelado = True
        return 1

    def PostThreadMessageW(self, thread, codigo, w, l):  # noqa: N802
        self.fila.put(codigo)
        return 1

    def GetCurrentThreadId(self):  # noqa: N802
        return 77


@pytest.fixture
def sem_get_last_error(monkeypatch):
    erros = {"ultimo": 0}
    monkeypatch.setattr(ctypes, "set_last_error", lambda c: erros.__setitem__("ultimo", c), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: erros["ultimo"], raising=False)


def test_registra_avisa_a_cada_toque_e_cancela_ao_parar(sem_get_last_error):
    windows, toques = Windows(), []
    chegou = threading.Semaphore(0)
    escuta = Atalho(ler_combinacao("ctrl+alt+j"), lambda: (toques.append(1), chegou.release()), windows, windows)
    escuta.iniciar()
    assert windows.registrado == (None, 1, 0x0003 | 0x4000, 0x4A)  # sem repetição ao segurar
    windows.fila.put(0x0312)
    windows.fila.put(0x0113)  # outra mensagem qualquer: ignorada
    windows.fila.put(0x0312)
    assert chegou.acquire(timeout=2) and chegou.acquire(timeout=2)
    assert toques == [1, 1]
    escuta.parar()
    assert windows.cancelado and escuta._thread is None


def test_combinacao_ja_usada_por_outro_programa(sem_get_last_error):
    windows = Windows(recusa=1409)
    with pytest.raises(ErroAtalho, match=r"Ctrl\+Alt\+J já está em uso por outro programa"):
        Atalho(ler_combinacao(None), lambda: None, windows, windows).iniciar()
    assert not windows.cancelado


def test_outra_recusa_do_windows(sem_get_last_error):
    windows = Windows(recusa=5)
    with pytest.raises(ErroAtalho, match="recusou o atalho F9 .erro 5."):
        Atalho(ler_combinacao("f9"), lambda: None, windows, windows).iniciar()


def test_falha_de_quem_recebe_o_aviso_nao_derruba_a_escuta(sem_get_last_error):
    windows, toques = Windows(), []
    chegou = threading.Semaphore(0)

    def receber():
        toques.append(1)
        chegou.release()
        if len(toques) == 1:
            raise RuntimeError("o laço da conversa já fechou")

    escuta = Atalho(ler_combinacao(None), receber, windows, windows)
    escuta.iniciar()
    windows.fila.put(0x0312)
    windows.fila.put(0x0312)
    assert chegou.acquire(timeout=2) and chegou.acquire(timeout=2)
    escuta.parar()


def test_sem_windows_a_falha_vira_mensagem(sem_get_last_error):
    quebrado = SimpleNamespace(GetCurrentThreadId=lambda: 1)  # sem RegisterHotKey
    with pytest.raises(ErroAtalho, match="não consegui registrar o atalho"):
        Atalho(ler_combinacao(None), lambda: None, quebrado, quebrado).iniciar()


def test_padrao_documentado():
    assert atalho.PADRAO == "ctrl+alt+j"
