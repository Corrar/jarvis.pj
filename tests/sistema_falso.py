"""Um Windows de mentira para testar o controle de janelas sem Windows."""

from __future__ import annotations

from jarvis.janelas import CTRL, END, HOME, MIDIA, PGDN, PGUP, SHIFT, TECLAS_DE_PAGINA, T, W, Janela

SUFIXO = {"chrome.exe": " - Google Chrome", "msedge.exe": " — Microsoft Edge", "firefox.exe": " — Mozilla Firefox"}


class Navegador:
    def __init__(self, processo: str, abas: list[str], ativa: int = 0):
        self.processo, self.abas, self.ativa, self.fechadas = processo, list(abas), ativa, []

    @property
    def titulo(self) -> str:
        return self.abas[self.ativa] + SUFIXO[self.processo]


class Programa:
    def __init__(self, processo: str, titulo: str):
        self.processo, self.titulo = processo, titulo


class SistemaFalso:
    """`janelas` vai da frente para o fundo, como o Windows enumera. A 1 é o terminal do Jarvis."""

    def __init__(self, janelas: dict[int, Navegador | Programa], propria: int = 1):
        self.janelas = dict(janelas)
        self.propria = propria
        self.frente = propria
        self.teclas_enviadas: list[tuple[int, tuple[int, ...]]] = []
        self.pedidos_de_fechamento: list[int] = []
        self.nao_focam: set[int] = set()
        self.rouba_o_foco: int | None = None  # janela que toma a frente logo depois de um focar()
        self.tempo_parado = 0.0
        self.atraso_do_titulo = 0  # quantas leituras ainda mostram o título velho depois de um atalho
        self.nao_fecham: set[str] = set()  # abas que não fecham com Ctrl+W (a página pede confirmação)
        self._velho: dict[int, list] = {}  # janela → [título velho, leituras restantes]
        # dentro da página
        self.retangulos: dict[int, tuple[int, int, int, int]] = {}  # padrão: (0, 0, 1600, 1000)
        self.por_cima: int | None = None  # outra janela cobrindo qualquer ponto da tela
        self.fotos: list[tuple[tuple[int, int, int, int], int, int]] = []
        self.cliques: list[tuple[int, int, int, bool]] = []  # (janela, x, y, duplo)
        self.rodadas: list[tuple[int, int, int, int]] = []  # (janela, x, y, passos)
        self.digitado: list[tuple[int, str]] = []
        self.midia: list[int] = []
        self.sai_da_frente_ao_digitar: int | None = None  # janela que toma a frente depois do primeiro lote de texto
        self.donas: dict[int, int] = {}  # diálogo → janela principal a que pertence

    def listar(self) -> list[Janela]:
        # como no Windows de verdade, diálogo (janela com dona) não entra na lista
        return [Janela(h, j.titulo, j.processo) for h, j in self.janelas.items() if h not in self.donas]

    def titulo(self, hwnd: int) -> str:
        velho = self._velho.get(hwnd)
        if velho and velho[1] > 0:
            velho[1] -= 1
            return velho[0]
        return self.janelas[hwnd].titulo if hwnd in self.janelas else ""

    def ativa(self) -> int:
        return self.frente

    def focar(self, hwnd: int) -> bool:
        if hwnd in self.nao_focam or hwnd not in self.janelas:
            return False
        self.frente = hwnd
        if self.rouba_o_foco is not None and hwnd != self.propria:
            self.frente = self.rouba_o_foco
            return True  # o Windows disse que focou, mas outra janela pulou na frente
        return True

    def teclas(self, *codigos: int) -> None:
        if len(codigos) == 1 and codigos[0] in MIDIA.values():
            self.midia.append(codigos[0])  # tecla de mídia: vale para o sistema todo, com qualquer janela na frente
            return
        self.teclas_enviadas.append((self.frente, codigos))
        alvo = self.janelas.get(self.dona(self.frente))
        if isinstance(alvo, Programa) and (codigos in TECLAS_DE_PAGINA.values() or codigos in ((CTRL, HOME), (CTRL, END))):
            return  # tecla dentro da janela de um programa
        assert isinstance(alvo, Navegador), f"tecla enviada para janela que não é navegador: {self.frente}"
        if codigos in ((CTRL, HOME), (CTRL, END)) or codigos in TECLAS_DE_PAGINA.values():
            return  # tecla dentro da página: não mexe nas abas
        self._velho[self.frente] = [alvo.titulo, self.atraso_do_titulo]
        if codigos == (CTRL, PGDN):
            alvo.ativa = (alvo.ativa + 1) % len(alvo.abas)
        elif codigos == (CTRL, PGUP):
            alvo.ativa = (alvo.ativa - 1) % len(alvo.abas)
        elif codigos == (CTRL, W) and alvo.abas[alvo.ativa] in self.nao_fecham:
            pass
        elif codigos == (CTRL, W):
            alvo.fechadas.append(alvo.abas.pop(alvo.ativa))
            if not alvo.abas:
                del self.janelas[self.frente]  # fechou a última aba: a janela some
            else:
                alvo.ativa = min(alvo.ativa, len(alvo.abas) - 1)
        elif codigos == (CTRL, SHIFT, T):
            alvo.abas.append(alvo.fechadas.pop())
            alvo.ativa = len(alvo.abas) - 1
        else:
            raise AssertionError(f"atalho que o Jarvis não deveria mandar: {codigos}")

    def pedir_fechamento(self, hwnd: int) -> None:
        self.pedidos_de_fechamento.append(hwnd)

    def pausa(self, segundos: float) -> None:
        self.tempo_parado += segundos

    def retangulo(self, hwnd: int) -> tuple[int, int, int, int]:
        return self.retangulos.get(hwnd, (0, 0, 1600, 1000))

    def fotografar(self, caixa: tuple[int, int, int, int], largura: int, altura: int) -> bytes:
        self.fotos.append((caixa, largura, altura))
        return f"JPEG {largura}x{altura} de {self.janelas[self.frente].titulo}".encode()

    def janela_em(self, x: int, y: int) -> int:
        if self.por_cima is not None:
            return self.por_cima
        principal = self.dona(self.frente)
        esquerda, topo, direita, base = self.retangulo(principal)
        return principal if esquerda <= x < direita and topo <= y < base else 0

    def dona(self, hwnd: int) -> int:
        return self.donas.get(hwnd, hwnd)

    def clicar(self, x: int, y: int, duplo: bool) -> None:
        assert self.frente != self.propria, "clique com o terminal do Jarvis na frente"
        self.cliques.append((self.frente, x, y, duplo))

    def roda(self, x: int, y: int, passos: int) -> None:
        assert self.frente != self.propria, "roda do mouse com o terminal do Jarvis na frente"
        self.rodadas.append((self.frente, x, y, passos))

    def digitar(self, texto: str) -> None:
        assert self.frente != self.propria, "texto digitado com o terminal do Jarvis na frente"
        self.digitado.append((self.frente, texto))
        if self.sai_da_frente_ao_digitar is not None:
            self.frente = self.sai_da_frente_ao_digitar


def mesa() -> SistemaFalso:
    """Terminal do Jarvis, dois Chromes, um Edge, bloco de notas e Excel."""
    return SistemaFalso(
        {
            1: Programa("WindowsTerminal.exe", "python -m jarvis --voz"),
            10: Navegador("chrome.exe", ["Gmail", "Vercel", "Instagram", "YouTube"], ativa=0),
            11: Navegador("chrome.exe", ["Documentação do Python"], ativa=0),
            20: Navegador("msedge.exe", ["Bing", "Mercado Livre"], ativa=1),
            30: Programa("notepad.exe", "Sem título - Bloco de Notas"),
            40: Programa("EXCEL.EXE", "romaneio.xlsx - Excel"),
        }
    )
