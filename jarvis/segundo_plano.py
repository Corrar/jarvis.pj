"""Segundo plano (só Windows): o Jarvis sem janela à vista, com um ícone perto do relógio.

As peças são pequenas e cada uma aceita um substituto, para os testes rodarem sem Windows:

- `Console`: a janela de terminal do próprio Jarvis. Esconde, mostra, e o X deixa de fechar.
- `Instancia`: um Jarvis só por instalação. Dois ouviriam o mesmo microfone e responderiam juntos.
- `Bloqueio`: sabe se a tela está bloqueada; com ela bloqueada o nome do Jarvis não é ouvido.
- `InicioAutomatico`: uma linha em "Executar ao entrar" do usuário. Sem serviço, sem administrador.
- `lancar`: sobe o Jarvis num terminal próprio, já escondido. É o que o `iniciar.pyw` chama.
- `assumir_console`: primeira coisa do modo segundo plano; garante um terminal que some de verdade.
- `Bandeja`: o ícone, o menu dele e os avisos.

Este módulo é carregado antes de todo o resto (o terminal precisa sumir logo), então só usa
a biblioteca padrão; o ícone (pystray, Pillow) é importado quando é ligado.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
NOME = "Jarvis"
ARGUMENTO = "--segundo-plano"
PROPRIO, CLASSICO = "--console=proprio", "--console=classico"

_SW_HIDE, _SW_SHOW, _SW_RESTORE = 0, 5, 9
_CLASSE_DO_CONSOLE = "ConsoleWindowClass"  # o terminal clássico; dentro do Windows Terminal a classe é outra
_GA_ROOTOWNER = 3
_SC_CLOSE = 0xF060
_ENTRADA = -10  # STD_INPUT_HANDLE
_EDICAO_RAPIDA, _OPCOES_ESTENDIDAS = 0x0040, 0x0080  # ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS
_JA_EXISTE = 183
_ACESSO_NEGADO = 5
_SYNCHRONIZE = 0x00100000


# --- a janela do terminal ----------------------------------------------------------------------


def _bibliotecas() -> tuple[Any, Any]:
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    HWND, BOOL = wintypes.HWND, wintypes.BOOL
    k.GetConsoleWindow.argtypes, k.GetConsoleWindow.restype = [], HWND
    k.SetConsoleTitleW.argtypes, k.SetConsoleTitleW.restype = [wintypes.LPCWSTR], BOOL
    k.GetStdHandle.argtypes, k.GetStdHandle.restype = [ctypes.c_int], wintypes.HANDLE
    k.GetConsoleMode.argtypes, k.GetConsoleMode.restype = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], BOOL
    k.SetConsoleMode.argtypes, k.SetConsoleMode.restype = [wintypes.HANDLE, wintypes.DWORD], BOOL
    u.GetClassNameW.argtypes, u.GetClassNameW.restype = [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int
    u.GetAncestor.argtypes, u.GetAncestor.restype = [HWND, wintypes.UINT], HWND
    u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [HWND], BOOL
    u.IsIconic.argtypes, u.IsIconic.restype = [HWND], BOOL
    u.ShowWindow.argtypes, u.ShowWindow.restype = [HWND, ctypes.c_int], BOOL
    u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = [HWND], BOOL
    u.GetSystemMenu.argtypes, u.GetSystemMenu.restype = [HWND, BOOL], wintypes.HMENU
    u.DeleteMenu.argtypes, u.DeleteMenu.restype = [wintypes.HMENU, wintypes.UINT, wintypes.UINT], BOOL
    u.DrawMenuBar.argtypes, u.DrawMenuBar.restype = [HWND], BOOL
    return u, k


class Console:
    """A janela de terminal em que o Jarvis roda.

    No terminal clássico do Windows ela é uma janela de verdade e some por inteiro. Dentro do
    Windows Terminal o que existe é uma janela de fachada: esconder e mostrar ainda funcionam,
    mas quem obedece é o Windows Terminal, do jeito dele (minimiza em vez de sumir).
    """

    def __init__(self, user32: Any = None, kernel32: Any = None) -> None:
        if user32 is None:
            user32, kernel32 = _bibliotecas()
        self._u, self._k = user32, kernel32
        self.hwnd = int(kernel32.GetConsoleWindow() or 0)
        self.real = bool(self.hwnd) and self._classe(self.hwnd) == _CLASSE_DO_CONSOLE
        dona = 0 if self.real or not self.hwnd else int(user32.GetAncestor(self.hwnd, _GA_ROOTOWNER) or 0)
        # A janela que o usuário vê: a do console, ou a do programa de terminal que o hospeda.
        self.janela = self.hwnd if self.real else (dona if dona != self.hwnd else 0)
        self._a_vista = bool(self.hwnd)

    def _classe(self, hwnd: int) -> str:
        texto = ctypes.create_unicode_buffer(64)
        self._u.GetClassNameW(hwnd, texto, 64)
        return texto.value

    @property
    def existe(self) -> bool:
        return bool(self.hwnd)

    @property
    def visivel(self) -> bool:
        if not self.hwnd:
            return False
        return bool(self._u.IsWindowVisible(self.hwnd)) if self.real else self._a_vista

    def esconder(self) -> bool:
        if not self.hwnd:
            return False
        self._u.ShowWindow(self.hwnd, _SW_HIDE)
        self._a_vista = False
        return True

    def mostrar(self) -> bool:
        """Mostra o terminal e o traz para frente."""
        if not self.hwnd:
            return False
        self._u.ShowWindow(self.hwnd, _SW_SHOW)
        if self.real and not self._u.IsWindowVisible(self.hwnd):
            # Processo que nasceu com a janela escondida: o Windows pode trocar o primeiro "mostrar" por "esconder".
            self._u.ShowWindow(self.hwnd, _SW_SHOW)
        alvo = self.janela or self.hwnd
        if self._u.IsIconic(alvo):
            self._u.ShowWindow(alvo, _SW_RESTORE)
        self._u.SetForegroundWindow(alvo)
        self._a_vista = True
        return True

    def alternar(self) -> bool:
        """O clique no ícone: esconde se está à vista, mostra se não está. Devolve se ficou à vista."""
        if self.visivel and not self._u.IsIconic(self.janela or self.hwnd):
            self.esconder()
            return False
        return self.mostrar()

    def sem_fechar(self) -> None:
        """Tira o X da janela: fechar o terminal derrubaria o Jarvis; para sumir com ele existe o ícone."""
        if not self.real:
            return
        menu = self._u.GetSystemMenu(self.hwnd, False)
        if menu:
            self._u.DeleteMenu(menu, _SC_CLOSE, 0)  # MF_BYCOMMAND
            self._u.DrawMenuBar(self.hwnd)

    def titulo(self, texto: str) -> None:
        if self.hwnd:
            self._k.SetConsoleTitleW(texto)

    def sem_selecao(self) -> None:
        """Desliga a "edição rápida" do terminal.

        Com ela, um clique na janela começa uma seleção de texto, e o Windows congela tudo o que o
        programa escreve até alguém apertar Enter. Escondido nesse estado, o Jarvis ficaria parado
        e invisível.
        """
        if not self.hwnd:
            return
        from ctypes import wintypes

        entrada = self._k.GetStdHandle(_ENTRADA)
        modo = wintypes.DWORD(0)
        if entrada and self._k.GetConsoleMode(entrada, ctypes.byref(modo)):
            self._k.SetConsoleMode(entrada, (modo.value & ~_EDICAO_RAPIDA) | _OPCOES_ESTENDIDAS)


class ConsoleAusente:
    """Fora do Windows, ou sem terminal: nada para esconder nem mostrar."""

    hwnd = janela = 0
    real = existe = visivel = False

    def esconder(self) -> bool:
        return False

    def mostrar(self) -> bool:
        return False

    def alternar(self) -> bool:
        return False

    def sem_fechar(self) -> None:
        pass

    def titulo(self, texto: str) -> None:
        pass

    def sem_selecao(self) -> None:
        pass


def console_atual(sistema: str = sys.platform) -> Any:
    if sistema != "win32":
        return ConsoleAusente()
    try:
        return Console()
    except Exception:  # noqa: BLE001 - sem a API de janelas o Jarvis segue; só não esconde o terminal
        log.exception("Não consegui localizar a janela do terminal")
        return ConsoleAusente()


# --- um Jarvis só ------------------------------------------------------------------------------


def _nome_da_trava(raiz: Path) -> str:
    return "Local\\Jarvis-" + hashlib.sha1(str(raiz).lower().encode("utf-8")).hexdigest()[:16]


class Instancia:
    """Segura uma trava com nome enquanto este Jarvis roda. `unica` é falso se já havia outro.

    A trava vale por pasta de instalação: duas cópias em pastas diferentes não se enxergam.
    """

    def __init__(
        self, raiz: Path = RAIZ, kernel32: Any = None, sistema: str = sys.platform, ultimo_erro: Callable[[], int] | None = None
    ) -> None:
        self.unica = True
        self._alca: Any = None
        if sistema != "win32":
            return
        try:
            if kernel32 is None:
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                kernel32.CreateMutexW.restype = wintypes.HANDLE
            self._alca = kernel32.CreateMutexW(None, False, _nome_da_trava(raiz))
            erro = ultimo_erro() if ultimo_erro is not None else ctypes.get_last_error()  # type: ignore[attr-defined]
            # Já existe; ou existe e nem se deixa abrir (o outro Jarvis roda como administrador e este, não).
            self.unica = not ((self._alca and erro == _JA_EXISTE) or (not self._alca and erro == _ACESSO_NEGADO))
        except Exception:  # noqa: BLE001 - sem a trava o Jarvis sobe do mesmo jeito
            log.exception("Não consegui criar a trava de instância única")


def ja_esta_rodando(raiz: Path = RAIZ, kernel32: Any = None, sistema: str = sys.platform) -> bool:
    """Há um Jarvis desta instalação rodando agora? Só olha; não segura a trava."""
    if sistema != "win32":
        return False
    try:
        if kernel32 is None:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.OpenMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes, kernel32.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
        alca = kernel32.OpenMutexW(_SYNCHRONIZE, False, _nome_da_trava(raiz))
        if not alca:
            return False
        kernel32.CloseHandle(alca)
        return True
    except Exception:  # noqa: BLE001
        log.exception("Não consegui consultar a trava de instância única")
        return False


# --- tela bloqueada ----------------------------------------------------------------------------


class Bloqueio:
    """Diz se a tela do Windows está bloqueada (Win+L, ou a tela de entrada).

    Com a tela bloqueada o dono não está no computador, e quem disser o nome do Jarvis não deve
    ser obedecido. A consulta é a de sempre para isso: a área de trabalho que recebe o teclado
    só é a "Default" com a sessão aberta; na tela de bloqueio é outra, que um programa comum
    nem consegue abrir.

    Se a consulta não puder ser feita (fora do Windows, API ausente), nada é bloqueado.
    """

    def __init__(self, user32: Any = None, sistema: str = sys.platform, relogio: Callable[[], float] | None = None, validade: float = 2.0) -> None:
        import time

        self._relogio, self._validade = relogio or time.monotonic, validade
        self._u = user32
        self._visto_em, self._ultimo = float("-inf"), False
        self.disponivel = False
        if sistema != "win32":
            return
        try:
            if self._u is None:
                from ctypes import wintypes

                self._u = ctypes.WinDLL("user32", use_last_error=True)
                self._u.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                self._u.OpenInputDesktop.restype = wintypes.HANDLE
                self._u.CloseDesktop.argtypes, self._u.CloseDesktop.restype = [wintypes.HANDLE], wintypes.BOOL
                self._u.GetUserObjectInformationW.argtypes = [
                    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                ]  # fmt: skip
                self._u.GetUserObjectInformationW.restype = wintypes.BOOL
            self._consultar()
            self.disponivel = True
        except Exception:  # noqa: BLE001 - sem a consulta o Jarvis segue ouvindo
            log.exception("Não consegui consultar se a tela está bloqueada; a palavra de ativação vale mesmo com ela bloqueada")

    def _consultar(self) -> bool:
        alca = self._u.OpenInputDesktop(0, False, 0x80000000)  # GENERIC_READ
        if not alca:
            return True  # a área de trabalho da vez não se deixa abrir: é a da tela de bloqueio
        try:
            from ctypes import wintypes

            nome = ctypes.create_unicode_buffer(256)
            precisa = wintypes.DWORD(0)
            leu = self._u.GetUserObjectInformationW(alca, 2, nome, ctypes.sizeof(nome), ctypes.byref(precisa))  # UOI_NAME
            return not (leu and nome.value.lower() == "default")
        finally:
            self._u.CloseDesktop(alca)

    def __call__(self) -> bool:
        """A tela está bloqueada agora? A resposta vale por dois segundos (é perguntada a cada pedaço de som)."""
        if not self.disponivel:
            return False
        agora = self._relogio()
        if agora - self._visto_em >= self._validade:
            self._visto_em = agora
            try:
                self._ultimo = self._consultar()
            except Exception:  # noqa: BLE001
                self._ultimo = False
        return self._ultimo


# --- subir escondido ---------------------------------------------------------------------------


def _python_com_terminal(executavel: str, raiz: Path = RAIZ, em_ambiente: bool | None = None) -> str:
    """O Python que vai rodar o Jarvis: com terminal (mesmo escondido) e com as bibliotecas dele.

    Dois cliques no iniciar.pyw usam o Python do sistema; se o Jarvis foi instalado na `.venv`
    da própria pasta (o jeito do README), é o Python de lá que tem as bibliotecas.
    """
    if em_ambiente is None:
        em_ambiente = sys.prefix != sys.base_prefix
    do_projeto = raiz / ".venv" / "Scripts" / "python.exe"
    if not em_ambiente and do_projeto.exists():
        return str(do_projeto)
    vizinho = Path(executavel).with_name("python.exe")
    return str(vizinho) if vizinho.exists() else executavel


def _janela_escondida() -> Any:
    info = subprocess.STARTUPINFO()  # type: ignore[attr-defined]  # só existe no Windows
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    info.wShowWindow = _SW_HIDE
    return info


def lancar(
    extras: Sequence[str] = (),
    executavel: str = sys.executable,
    raiz: Path = RAIZ,
    abrir: Callable[..., Any] = subprocess.Popen,
    escondida: Callable[[], Any] = _janela_escondida,
) -> None:
    """Sobe o Jarvis em segundo plano, num terminal próprio que já nasce escondido."""
    abrir(
        [_python_com_terminal(executavel, raiz), "-m", "jarvis", ARGUMENTO, PROPRIO, *extras],
        cwd=str(raiz),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10),
        startupinfo=escondida(),
        close_fds=True,
    )


def _relancar_no_console_classico(
    argumentos: Sequence[str],
    executavel: str = sys.executable,
    raiz: Path = RAIZ,
    abrir: Callable[..., Any] = subprocess.Popen,
    escondida: Callable[[], Any] = _janela_escondida,
) -> None:
    """Recomeça dentro do terminal clássico do Windows, pedido pelo nome: esse some por inteiro."""
    conhost = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe"
    resto = [a for a in argumentos if a not in (PROPRIO, CLASSICO)]
    abrir(
        [str(conhost), _python_com_terminal(executavel, raiz), "-m", "jarvis", *resto, CLASSICO],
        cwd=str(raiz),
        startupinfo=escondida(),
        close_fds=True,
    )


def _esperar_o_outro(segundos: float = 45.0, rodando: Callable[[], bool] = ja_esta_rodando, pausa: Callable[[float], None] | None = None) -> bool:
    """Espera o Jarvis recomeçado dar sinal de vida (a trava de instância única). Falso se ele não aparecer."""
    import time

    prazo = time.monotonic() + segundos
    while time.monotonic() < prazo:
        if rodando():
            return True
        (pausa or time.sleep)(0.5)
    return rodando()


def assumir_console(
    argumentos: Sequence[str],
    console: Any = None,
    lancar: Callable[..., None] = lancar,
    relancar: Callable[..., None] = _relancar_no_console_classico,
    avisar: Callable[[str], None] = print,
    subiu: Callable[[], bool] = _esperar_o_outro,
) -> None:
    """Primeira coisa do modo segundo plano: deixa o Jarvis num terminal dele, escondido.

    - Chamado à mão, num terminal do usuário: sobe outro processo, em terminal próprio, e devolve
      o terminal ao usuário (sai com código 0).
    - No terminal próprio: se ele some de verdade, esconde e segue. Se caiu dentro do Windows
      Terminal, que só minimiza, recomeça uma vez no terminal clássico e só sai quando o novo
      der sinal de vida; se não der, fica neste mesmo, minimizado. Melhor à vista do que sumido.
    """
    if PROPRIO not in argumentos and CLASSICO not in argumentos:
        lancar([a for a in argumentos if a != ARGUMENTO])
        avisar("O Jarvis está subindo em segundo plano. Procure o ícone dele perto do relógio (pode estar na setinha ^).")
        raise SystemExit(0)
    if console is None:
        console = console_atual()
    console.esconder()
    if not console.real and CLASSICO not in argumentos:
        try:
            relancar(argumentos)
        except OSError:
            log.exception("Não consegui recomeçar no terminal clássico; sigo neste")
            return
        if subiu():
            raise SystemExit(0)
        log.warning("O recomeço no terminal clássico não deu sinal de vida; sigo neste terminal")


# --- iniciar com o Windows -----------------------------------------------------------------------

_CHAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"
_CHAVE_DAS_ESCOLHAS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def comando_de_inicio(executavel: str = sys.executable, raiz: Path = RAIZ) -> str:
    """A linha que o Windows roda ao entrar: o iniciar.pyw, pelo Python sem janela."""
    sem_janela = Path(executavel).with_name("pythonw.exe")
    programa = sem_janela if sem_janela.exists() else Path(executavel)
    return f'"{programa}" "{raiz / "iniciar.pyw"}"'


class InicioAutomatico:
    """Liga e desliga o início junto com o Windows, para o usuário atual (sem administrador)."""

    def __init__(self, registro: Any = None, comando: str | None = None, nome: str = NOME) -> None:
        if registro is None:
            import winreg as registro  # type: ignore[no-redef]  # só existe no Windows
        self._r = registro
        self.comando = comando or comando_de_inicio()
        self._nome = nome

    def _valor(self) -> str | None:
        try:
            with self._r.OpenKey(self._r.HKEY_CURRENT_USER, _CHAVE, 0, self._r.KEY_QUERY_VALUE) as chave:
                return str(self._r.QueryValueEx(chave, self._nome)[0])
        except OSError:
            return None

    def _desligado_nas_configuracoes(self) -> bool:
        """O usuário desligou o item em Configurações → Aplicativos → Inicialização (ou no Gerenciador de Tarefas)?

        O Windows guarda essa escolha à parte, sem apagar a linha: um valor binário cujo primeiro byte é ímpar.
        """
        try:
            with self._r.OpenKey(self._r.HKEY_CURRENT_USER, _CHAVE_DAS_ESCOLHAS, 0, self._r.KEY_QUERY_VALUE) as chave:
                dados = self._r.QueryValueEx(chave, self._nome)[0]
        except OSError:
            return False
        return isinstance(dados, bytes) and len(dados) > 0 and dados[0] % 2 == 1

    def ligado(self) -> bool:
        return self._valor() is not None and not self._desligado_nas_configuracoes()

    def _gravar(self) -> None:
        with self._r.CreateKeyEx(self._r.HKEY_CURRENT_USER, _CHAVE, 0, self._r.KEY_SET_VALUE) as chave:
            self._r.SetValueEx(chave, self._nome, 0, self._r.REG_SZ, self.comando)

    def ligar(self) -> None:
        self._gravar()
        try:  # se estava desligado nas Configurações do Windows, a escolha antiga sai do caminho
            with self._r.OpenKey(self._r.HKEY_CURRENT_USER, _CHAVE_DAS_ESCOLHAS, 0, self._r.KEY_SET_VALUE) as chave:
                self._r.DeleteValue(chave, self._nome)
        except OSError:
            pass  # não havia escolha guardada

    def desligar(self) -> None:
        try:
            with self._r.OpenKey(self._r.HKEY_CURRENT_USER, _CHAVE, 0, self._r.KEY_SET_VALUE) as chave:
                self._r.DeleteValue(chave, self._nome)
        except FileNotFoundError:
            pass  # já não estava

    def conferir(self) -> None:
        """Se está ligado mas aponta para outra pasta (o Jarvis foi movido), corrige o endereço."""
        valor = self._valor()
        if valor is not None and valor != self.comando:
            self._gravar()  # só o endereço: se o usuário desligou pelo Windows, continua desligado


# --- avisos sem terminal -----------------------------------------------------------------------


def caixa(texto: str, erro: bool = True, sistema: str = sys.platform) -> None:
    """Uma caixa de mensagem do Windows: o jeito de falar com o usuário quando o terminal está escondido."""
    if sistema != "win32":
        print(texto, file=sys.stderr if erro else sys.stdout)
        return
    try:
        estilo = (0x10 if erro else 0x40) | 0x10000 | 0x40000  # ícone | na frente | acima das outras
        ctypes.windll.user32.MessageBoxW(None, texto, NOME, estilo)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - sem a caixa, fica o registro no arquivo
        log.error("Mensagem que não pôde ser mostrada: %s", texto)


# --- o ícone perto do relógio ------------------------------------------------------------------

ESTADOS = {
    # nome: (cor do ícone, o que aparece ao passar o mouse)
    "carregando": ((125, 130, 140), "Jarvis: carregando…"),
    "pronto": ((0, 140, 255), "Jarvis: pronto"),
    "ouvindo": ((0, 190, 110), "Jarvis: ouvindo o seu pedido…"),
    "ocupado": ((255, 160, 0), "Jarvis: trabalhando no seu pedido"),
    "atencao": ((225, 55, 55), "Jarvis: preciso da sua confirmação no terminal"),
    "surdo": ((125, 130, 140), "Jarvis: microfone desligado"),
}


def desenhar_icone(cor: tuple[int, int, int], riscado: bool = False, tamanho: int = 64) -> Any:
    """Um disco colorido com um J branco (riscado, se o microfone está desligado). Desenhado na hora: não depende de arquivo."""
    from PIL import Image, ImageDraw

    escala = 4  # desenha grande e reduz, para as bordas saírem lisas
    lado = tamanho * escala
    imagem = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    lapis = ImageDraw.Draw(imagem)
    lapis.ellipse((lado * 0.03, lado * 0.03, lado * 0.97, lado * 0.97), fill=(*cor, 255))
    branco, traco = (255, 255, 255, 255), int(lado * 0.12)
    haste = lado * 0.60
    lapis.line([(lado * 0.38, lado * 0.25), (haste + traco / 2, lado * 0.25)], fill=branco, width=traco)  # o topo
    lapis.line([(haste, lado * 0.25), (haste, lado * 0.60)], fill=branco, width=traco)  # a haste
    lapis.arc((lado * 0.30, lado * 0.40, haste + traco / 2, lado * 0.78), start=0, end=180, fill=branco, width=traco)  # a curva
    if riscado:
        lapis.line([(lado * 0.20, lado * 0.80), (lado * 0.80, lado * 0.20)], fill=(*cor, 255), width=int(traco * 1.5))
        lapis.line([(lado * 0.20, lado * 0.80), (lado * 0.80, lado * 0.20)], fill=(235, 70, 70, 255), width=int(traco * 0.7))
    return imagem.resize((tamanho, tamanho), Image.LANCZOS)


class Bandeja:
    """O ícone do Jarvis perto do relógio.

    As ações do menu chegam depois, com `ligar`: o ícone sobe antes de os modelos de voz
    carregarem, para o usuário ver que o Jarvis está a caminho. `leituras` são perguntas de
    sim ou não que o menu faz na hora de abrir (o terminal está à vista? o microfone, ligado?).
    """

    def __init__(self, pystray: Any = None, desenhar: Callable[..., Any] = desenhar_icone) -> None:
        self._pystray, self._desenhar = pystray, desenhar
        self._icone: Any = None
        self._imagens: dict[str, Any] = {}
        self._estado, self._dica = "carregando", ""
        self.acoes: dict[str, Callable[[], None]] = {"sair": lambda: os._exit(0)}  # antes da conversa existir, sair é só sair
        self.leituras: dict[str, Callable[[], bool]] = {}

    # --- menu -----------------------------------------------------------------------------------

    def _ler(self, nome: str) -> bool:
        try:
            return bool(self.leituras[nome]())
        except Exception:  # noqa: BLE001 - o menu abre mesmo que uma leitura falhe
            return False

    def _agir(self, nome: str) -> Callable[..., None]:
        def agir(*_quem: Any) -> None:
            acao = self.acoes.get(nome)
            if acao is None:
                return
            try:
                acao()
            except Exception:  # noqa: BLE001 - um clique com erro não derruba o ícone
                log.exception("Falha na ação '%s' do ícone", nome)
            self.atualizar()

        return agir

    def _tem(self, nome: str) -> Callable[..., bool]:
        return lambda *_item: nome in self.acoes

    def _menu(self) -> Any:
        p = self._pystray
        return p.Menu(
            p.MenuItem(
                lambda *_item: "Esconder o terminal" if self._ler("terminal") else "Mostrar o terminal",
                self._agir("terminal"),
                default=True,  # é o que o clique simples no ícone faz
                visible=self._tem("terminal"),
            ),
            p.MenuItem("Abrir o painel", self._agir("painel"), visible=self._tem("painel")),
            p.MenuItem("Microfone ligado", self._agir("microfone"), checked=lambda *_item: self._ler("microfone"), visible=self._tem("microfone")),
            p.MenuItem("Iniciar com o Windows", self._agir("inicio"), checked=lambda *_item: self._ler("inicio"), visible=self._tem("inicio")),
            p.Menu.SEPARATOR,
            p.MenuItem("Sair", self._agir("sair")),
        )

    # --- vida -----------------------------------------------------------------------------------

    def iniciar(self) -> None:
        """Põe o ícone na barra. Levanta ImportError se faltar a biblioteca do ícone."""
        if self._pystray is None:
            import pystray

            self._pystray = pystray
        self._icone = self._pystray.Icon("jarvis", self._imagem(self._estado), ESTADOS[self._estado][1], self._menu())
        threading.Thread(target=self._rodar, daemon=True, name="jarvis-icone").start()

    def _rodar(self) -> None:
        try:
            self._icone.run()
        except Exception:  # noqa: BLE001 - sem ícone o Jarvis continua ouvindo; fica o registro
            log.exception("O ícone perto do relógio parou")

    def _imagem(self, estado: str) -> Any:
        if estado not in self._imagens:
            self._imagens[estado] = self._desenhar(ESTADOS[estado][0], riscado=estado == "surdo")
        return self._imagens[estado]

    def ligar(self, acoes: dict[str, Callable[[], None]], leituras: dict[str, Callable[[], bool]]) -> None:
        self.acoes.update(acoes)
        self.leituras.update(leituras)
        self.atualizar()

    def atualizar(self) -> None:
        """Refaz o menu (um item mudou de nome ou de marca)."""
        if self._icone is not None:
            try:
                self._icone.update_menu()
            except Exception:  # noqa: BLE001
                log.exception("Não consegui atualizar o menu do ícone")

    def estado(self, nome: str, dica: str = "") -> None:
        """Troca a cor do ícone e o texto que aparece ao passar o mouse."""
        if nome not in ESTADOS or (nome == self._estado and dica == self._dica):
            return
        self._estado, self._dica = nome, dica
        if self._icone is None:
            return
        try:
            self._icone.icon = self._imagem(nome)
            self._icone.title = (dica or ESTADOS[nome][1])[:120]  # o Windows corta o texto do ícone em 127 letras
        except Exception:  # noqa: BLE001
            log.exception("Não consegui trocar o ícone")

    def avisar(self, texto: str) -> None:
        """Um balão de aviso do Windows, saindo do ícone."""
        if self._icone is None:
            return
        try:
            self._icone.notify(texto[:250], NOME)
        except Exception:  # noqa: BLE001 - nem todo sistema tem balão; o aviso também foi falado e escrito
            log.exception("Não consegui mostrar o aviso no ícone")

    def parar(self) -> None:
        icone, self._icone = self._icone, None
        if icone is not None:
            try:
                icone.stop()
            except Exception:  # noqa: BLE001 - encerrando
                log.exception("Falha ao tirar o ícone")


class BandejaMuda:
    """Sem ícone (modo normal, no terminal): aceita os mesmos avisos e não faz nada."""

    def ligar(self, acoes: Any, leituras: Any) -> None:
        pass

    def atualizar(self) -> None:
        pass

    def estado(self, nome: str, dica: str = "") -> None:
        pass

    def avisar(self, texto: str) -> None:
        pass

    def parar(self) -> None:
        pass
