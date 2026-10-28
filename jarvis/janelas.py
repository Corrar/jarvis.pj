"""Janelas abertas e o que há dentro delas: listar, trazer para frente, fechar, mexer nas
abas do navegador e, dentro da janela do navegador ou de um programa liberado, rolar, ver,
clicar e digitar.

Só Windows. Tudo passa pelo teclado e pelo mouse, como o usuário faria, e sempre depois
de conferir que a janela certa está em primeiro plano:

- abas: quatro atalhos fixos (Ctrl+W, Ctrl+PageDown, Ctrl+PageUp e Ctrl+Shift+T);
- rolagem: a roda do mouse sobre a janela do navegador, ou Ctrl+Home e Ctrl+End;
- mídia: as teclas de mídia do teclado (tocar/pausar, próxima, volume);
- dentro da janela: uma foto da janela (do navegador, ou de um programa da lista de
  liberados) vai para o modelo, que escolhe o ponto do clique; o clique só sai se o ponto
  cair na janela fotografada. Texto digitado e teclas (de uma lista fixa) só vão para essa
  mesma janela. Terminais, o Explorador de Arquivos e ferramentas do sistema ficam sempre
  de fora, e em cada turno o alvo é uma janela só.

Há ainda um toque de Alt, sem efeito em programa nenhum, usado como último recurso
para o Windows aceitar a troca de janela.

A lógica (classe Janelas) não fala com o Windows diretamente; ela usa um "sistema"
com poucas operações. O de verdade é SistemaWindows; os testes usam um falso.
"""

from __future__ import annotations

import ctypes
import io
import ntpath
import os
import re
import time
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Protocol

from jarvis.acoes import ErroAcao, Plano, chave

# Teclas (códigos virtuais do Windows)
CTRL, SHIFT, ALT, PGUP, PGDN, W, T = 0x11, 0x10, 0x12, 0x21, 0x22, 0x57, 0x54
ENTER, HOME, END = 0x0D, 0x24, 0x23

# Teclas que o modelo pode pedir dentro da janela em uso. Lista fechada: teclas soltas e
# cinco atalhos de edição com Ctrl. Nada com Alt ou Win, e nenhum atalho de aba ou de janela.
TECLAS_DE_PAGINA = {
    "enter": (ENTER,), "esc": (0x1B,), "tab": (0x09,), "espaco": (0x20,), "apagar": (0x08,), "delete": (0x2E,),
    "esquerda": (0x25,), "cima": (0x26,), "direita": (0x27,), "baixo": (0x28,),
    "inicio": (HOME,), "fim": (END,), "pagina_acima": (PGUP,), "pagina_abaixo": (PGDN,),
    "f2": (0x71,), "f5": (0x74,),
    # atalhos do player do YouTube: tela cheia, tocar/pausar, mudo, voltar e avançar 10 s
    "f": (0x46,), "k": (0x4B,), "m": (0x4D,), "j": (0x4A,), "l": (0x4C,),
    # edição
    "salvar": (CTRL, 0x53), "desfazer": (CTRL, 0x5A), "copiar": (CTRL, 0x43), "colar": (CTRL, 0x56),
    "selecionar_tudo": (CTRL, 0x41),
}  # fmt: skip
# Teclas de mídia do teclado: valem para o que estiver tocando, em qualquer janela.
MIDIA = {
    "tocar_pausar": 0xB3, "proxima": 0xB0, "anterior": 0xB1,
    "mudo": 0xAD, "volume_menos": 0xAE, "volume_mais": 0xAF,
}  # fmt: skip
# Teclas do bloco de navegação (não do teclado numérico) e de mídia.
ESTENDIDAS = (PGUP, PGDN, HOME, END, 0x25, 0x26, 0x27, 0x28, 0x2E, *MIDIA.values())

MAX_ABAS = 60  # quantas abas percorrer, no máximo, procurando a pedida
PAUSA = 0.15  # segundos para o navegador reagir a um atalho
ESPERAS = 3  # pausas a mais esperando o título da janela mudar depois de um atalho
VOLTA = 3  # títulos seguidos repetindo os do começo para concluir que as abas deram a volta

# Dentro da página
ESPERA_DA_PAGINA = 0.4  # segundos para a página se desenhar antes da foto
ESPERA_DO_CLIQUE = 0.8  # segundos para a página reagir a um clique ou a um texto
LADO_MAX = 1280  # a foto vai para o modelo com no máximo este lado...
PIXELS_MAX = 1_000_000  # ...e esta área: acima disso a API encolhe a imagem e as coordenadas deixam de bater
PASSOS_POR_TELA = 6  # "dentes" da roda do mouse por tela rolada (uns três quartos da janela)
MAX_TELAS = 10
MAX_ESPERA = 5  # segundos que o modelo pode pedir para a página carregar antes da foto
MAX_TEXTO = 300
LOTE_DE_TEXTO = 20  # confere a janela em primeiro plano a cada tantas letras digitadas

# Páginas em que o Jarvis não olha, não clica e não digita: banco, pagamento e senha.
# A conferência é pelo título da aba (palavras inteiras, sem acento).
PAGINAS_RESERVADAS = (
    "banco", "bank", "banking", "nubank", "itau", "bradesco", "santander", "caixa economica", "caixa tem",
    "sicredi", "sicoob", "picpay", "paypal", "mercado pago", "pagamento", "pagamentos", "checkout",
    "pix", "cartao de credito", "fatura", "senha", "senhas", "password", "gov br",
)  # fmt: skip
# Na janela de um programa (uma planilha chamada "banco de horas" é comum) valem só os termos de senha.
JANELAS_RESERVADAS = ("senha", "senhas", "password", "passwords")

# Programas em que o Jarvis nunca vê, clica nem digita, mesmo que estejam na lista de
# liberados: digitar em um terminal é rodar comando; dois cliques no Explorador abrem
# qualquer arquivo; o resto são ferramentas do sistema, de acesso remoto e cofres de senha.
NUNCA_NA_TELA = frozenset({
    "cmd", "powershell", "powershell_ise", "pwsh", "windowsterminal", "wt", "conhost", "openconsole", "bash", "wsl",
    "mintty", "alacritty", "wezterm-gui", "hyper", "python", "pythonw", "py", "code", "devenv", "pycharm64", "idea64",
    "explorer", "regedit", "taskmgr", "mmc", "msconfig", "control", "systemsettings", "securityhealthui", "consent",
    "msiexec", "mstsc", "anydesk", "teamviewer", "rustdesk", "keepass", "keepassxc", "1password", "bitwarden", "lastpass", "dashlane",
})  # fmt: skip

# A janela do painel do Jarvis é uma janela de navegador (modo aplicativo). Ela fica fora de
# tudo: não é listada, vista, clicada nem fechada a pedido.
TITULO_DO_PAINEL = "Jarvis · Painel"

NAVEGADORES = {
    "chrome.exe": "chrome",
    "msedge.exe": "edge",
    "firefox.exe": "firefox",
    "brave.exe": "brave",
    "opera.exe": "opera",
    "vivaldi.exe": "vivaldi",
}


@dataclass(frozen=True)
class Janela:
    hwnd: int
    titulo: str
    processo: str  # nome do executável, como chrome.exe

    @property
    def navegador(self) -> str | None:
        return NAVEGADORES.get(self.processo.lower())

    @property
    def programa(self) -> str:
        return self.navegador or os.path.splitext(self.processo)[0].lower()


@dataclass(frozen=True)
class Foto:
    """A janela do navegador como o modelo a vê."""

    dados: bytes  # JPEG
    largura: int
    altura: int


@dataclass(frozen=True)
class Captura:
    """De onde veio a última foto, para transformar um ponto da imagem em ponto da tela."""

    hwnd: int
    caixa: tuple[int, int, int, int]  # região da tela fotografada: esquerda, topo, direita, base
    largura: int  # tamanho da imagem entregue ao modelo
    altura: int
    de_programa: bool = False  # janela de um programa liberado, não do navegador


def reservada(titulo: str, de_programa: bool = False) -> str | None:
    """Devolve o termo que faz desta uma página de banco, pagamento ou senha; None se não for."""
    palavras = " " + re.sub(r"[^a-z0-9]+", " ", chave(pagina(titulo))).strip() + " "
    termos = JANELAS_RESERVADAS if de_programa else PAGINAS_RESERVADAS
    return next((termo for termo in termos if f" {termo} " in palavras), None)


def pagina(titulo: str) -> str:
    """'Instagram - Google Chrome' → 'Instagram' (tira o nome do navegador do fim do título)."""
    for separador in (" — ", " – ", " - "):
        if separador in titulo:
            return titulo.rsplit(separador, 1)[0]
    return titulo


class Sistema(Protocol):
    propria: int  # janela do terminal onde o Jarvis roda (0 se desconhecida)

    def listar(self) -> list[Janela]: ...  # janelas de programa visíveis, da frente para o fundo
    def titulo(self, hwnd: int) -> str: ...  # "" se a janela não existe mais
    def ativa(self) -> int: ...  # janela em primeiro plano agora
    def focar(self, hwnd: int) -> bool: ...
    def teclas(self, *codigos: int) -> None: ...  # aperta juntas e solta
    def pedir_fechamento(self, hwnd: int) -> None: ...
    def pausa(self, segundos: float) -> None: ...
    def retangulo(self, hwnd: int) -> tuple[int, int, int, int]: ...  # parte visível da janela, em pixels da tela
    def fotografar(self, caixa: tuple[int, int, int, int], largura: int, altura: int) -> bytes: ...  # JPEG já no tamanho pedido
    def janela_em(self, x: int, y: int) -> int: ...  # janela (ou a dona dela) que está neste ponto da tela
    def dona(self, hwnd: int) -> int: ...  # a janela principal a que esta pertence (um diálogo responde pela dona)
    def clicar(self, x: int, y: int, duplo: bool) -> None: ...
    def roda(self, x: int, y: int, passos: int) -> None: ...  # roda do mouse; positivo sobe
    def digitar(self, texto: str) -> None: ...


class Janelas:
    def __init__(self, sistema: Sistema, apelidos: dict[str, str] | None = None, na_tela: Iterable[str] = ()) -> None:
        self._s = sistema
        # nome falado → executável, vindo da lista de programas ("bloco de notas" → notepad)
        self._apelidos = {n: os.path.splitext(ntpath.basename(c))[0].lower() for n, c in (apelidos or {}).items()}
        # Programas em que ele pode ver, clicar e digitar: os da lista de programas e os de
        # JARVIS_PROGRAMAS_TELA, menos os que ficam sempre de fora. Navegador tem caminho próprio.
        extras = {os.path.splitext(ntpath.basename(n.strip()))[0].lower() for n in na_tela if n.strip()}
        navegadores = set(NAVEGADORES.values()) | {os.path.splitext(exe)[0] for exe in NAVEGADORES}
        self.operaveis = frozenset((set(self._apelidos.values()) | extras) - NUNCA_NA_TELA - navegadores)
        self._alvo: int | None = None  # janela de programa escolhida neste turno; o alvo não muda até o usuário falar
        self._captura: Captura | None = None  # a última foto deste turno
        self._devolver = False  # mexeu no navegador neste turno: no fim, o foco volta ao terminal

    # --- consulta ---------------------------------------------------------

    def listar(self) -> list[Janela]:
        """Janelas abertas, sem as do próprio Jarvis (o terminal e o painel)."""
        return [j for j in self._s.listar() if j.hwnd != self._s.propria and not j.titulo.startswith(TITULO_DO_PAINEL)]

    def fechar_painel(self) -> int:
        """Pede o fechamento da janela do painel, ao encerrar. Devolve quantas achou."""
        do_painel = [j for j in self._s.listar() if j.titulo.startswith(TITULO_DO_PAINEL)]
        for janela in do_painel:
            self._s.pedir_fechamento(janela.hwnd)
        return len(do_painel)

    def _navegadores(self, qual: str | None = None) -> list[Janela]:
        return [j for j in self.listar() if j.navegador and (qual is None or j.navegador == qual)]

    def _combinam(self, termo: Any) -> list[Janela]:
        """Janelas cujo título ou programa bate com o termo."""
        if not isinstance(termo, str) or len(chave(termo)) < 2:
            raise ErroAcao("Diga qual janela: uma palavra do título ou o nome do programa.")
        alvo = chave(termo)
        executavel = self._apelidos.get(alvo)
        return [
            j
            for j in self.listar()
            if alvo in chave(j.titulo) or alvo == j.programa or (executavel and executavel == os.path.splitext(j.processo)[0].lower())
        ]

    # --- teclas, sempre com a janela conferida ----------------------------

    def _na_frente(self, hwnd: int) -> bool:
        """A janela está em primeiro plano, ela mesma ou um diálogo dela (Salvar como, um aviso)."""
        ativa = self._s.ativa()
        return ativa == hwnd or (ativa != 0 and self._s.dona(ativa) == hwnd)

    def _atalho(self, hwnd: int, *codigos: int, com_dialogos: bool = False) -> None:
        """Manda um atalho para a janela. Atalho de aba exige a própria janela na frente; tecla
        de dentro da janela também vale para um diálogo dela."""
        if not (self._na_frente(hwnd) if com_dialogos else self._s.ativa() == hwnd):
            raise OSError("a janela saiu do primeiro plano; não enviei nenhuma tecla")
        self._s.teclas(*codigos)
        self._s.pausa(PAUSA)

    def _titulo_depois(self, hwnd: int, antes: str) -> str:
        """Título da janela depois de um atalho. O navegador pode atualizar com atraso: espera um pouco ele mudar."""
        for _ in range(ESPERAS):
            atual = self._s.titulo(hwnd)
            if atual != antes:
                return atual
            self._s.pausa(PAUSA)
        return self._s.titulo(hwnd)

    def _trazer(self, hwnd: int) -> bool:
        """Traz a janela para frente e espera um instante para ela aceitar teclas."""
        if not self._s.focar(hwnd) and not self._na_frente(hwnd):  # com um diálogo aberto, quem vem é o diálogo
            return False
        self._s.pausa(PAUSA)
        return True

    def _voltar_ao_terminal(self) -> None:
        if self._s.propria:
            self._s.focar(self._s.propria)

    def definir_terminal(self, hwnd: int) -> None:
        """Em segundo plano o terminal nasce escondido: quem sabe qual janela é a dele é quem o escondeu."""
        self._s.propria = hwnd

    def trazer_terminal(self) -> bool:
        """Traz para frente a janela onde o Jarvis roda (a tecla de atalho, no modo texto)."""
        return bool(self._s.propria) and self._s.focar(self._s.propria)

    def _ir_para_aba(self, hwnd: int, alvo: str) -> bool:
        """Percorre as abas da janela até o título bater. Sem achar, volta à aba em que começou."""
        vistos = [self._s.titulo(hwnd)]
        if alvo in chave(pagina(vistos[0])):
            return True
        for _ in range(MAX_ABAS):
            self._atalho(hwnd, CTRL, PGDN)
            atual = self._titulo_depois(hwnd, vistos[-1])
            if not atual:  # a janela fechou no meio do caminho
                return False
            if alvo in chave(pagina(atual)):
                return True
            vistos.append(atual)
            # Deu a volta quando os últimos títulos repetem os primeiros. Um só não
            # basta: abas vizinhas podem ter o mesmo título.
            if len(vistos) > VOLTA and vistos[-VOLTA:] == vistos[:VOLTA]:
                # Passou da aba inicial; volta até ela. Se o título nunca mudou, desfaz todos
                # os passos: podem ser várias abas com o mesmo título, e assim acerta a certa.
                passos, abas = len(vistos) - 1, len(vistos) - VOLTA
                for _ in range(passos if abas == 1 else (VOLTA - 1) % abas):
                    self._atalho(hwnd, CTRL, PGUP)
                return False
        return False

    # --- ações ------------------------------------------------------------

    def _fechar_a_atual(self, janela: Janela, qual: str) -> str:
        """Ctrl+W na aba à mostra, conferindo pelo título da janela se ela fechou mesmo."""
        antes = self._s.titulo(janela.hwnd)
        self._atalho(janela.hwnd, CTRL, W)
        if self._titulo_depois(janela.hwnd, antes) == antes:
            return (
                f"Mandei fechar {qual} no {janela.navegador}, mas o título da janela não mudou. "
                "Pode haver outra aba igual, ou a página pediu confirmação para sair. Avise o usuário."
            )
        return f"Fechei {qual} no {janela.navegador}."

    def _navegador_em_uso(self, aba: Any = None) -> Janela:
        """Traz para frente o navegador com que o usuário está mexendo e devolve a janela.

        Sem `aba`, é o navegador usado por último. Com `aba`, procura a aba pelo título em
        todas as janelas de navegador e deixa essa aba à mostra.
        """
        navegadores = self._navegadores()
        if not navegadores:
            raise ErroAcao("Não há nenhuma janela de navegador aberta.")
        if aba is None or (isinstance(aba, str) and not aba.strip()):
            if not self._trazer(navegadores[0].hwnd):
                raise OSError("o Windows não deixou trazer o navegador para frente")
            return navegadores[0]
        if not isinstance(aba, str) or len(chave(aba)) < 2:
            raise ErroAcao("Diga qual aba: uma palavra do título da página.")
        alvo = chave(aba)
        # Primeiro as janelas em que a aba pedida já está à mostra.
        navegadores.sort(key=lambda j: alvo not in chave(pagina(j.titulo)))
        alguma_veio = False
        for janela in navegadores:
            if not self._trazer(janela.hwnd):
                continue
            alguma_veio = True
            if self._ir_para_aba(janela.hwnd, alvo):
                return janela
        if not alguma_veio:
            raise OSError("o Windows não deixou trazer o navegador para frente")
        raise OSError(f"não achei nenhuma aba com '{aba}' no título")

    def fechar_aba(self, termo: Any = None) -> Plano:
        """Com um termo, fecha a aba cujo título o contém. Sem termo, a que está à mostra no navegador da frente."""
        navegadores = self._navegadores()
        if not navegadores:
            raise ErroAcao("Não há nenhuma janela de navegador aberta.")
        sem_termo = termo is None or (isinstance(termo, str) and not termo.strip())
        if not sem_termo and (not isinstance(termo, str) or len(chave(termo)) < 2):
            raise ErroAcao("Diga qual aba: uma palavra do título da página.")

        def executar() -> list[str]:
            try:
                janela = self._navegador_em_uso(None if sem_termo else termo)
                return [self._fechar_a_atual(janela, "a aba que estava à mostra" if sem_termo else f"a aba '{termo}'")]
            finally:
                self._voltar_ao_terminal()

        if sem_termo:
            linha = navegadores[0].titulo
        else:
            a_mostra = [j for j in navegadores if chave(termo) in chave(pagina(j.titulo))]
            linha = a_mostra[0].titulo if a_mostra else f"procurar '{termo}' entre as abas abertas"
        return Plano("fechar uma aba do navegador", [linha], executar)

    def reabrir_aba(self, navegador: Any = None) -> Plano:
        qual = chave(navegador) if isinstance(navegador, str) and navegador.strip() else None
        navegadores = self._navegadores(qual)
        if not navegadores:
            raise ErroAcao("Não há nenhuma janela desse navegador aberta." if qual else "Não há nenhuma janela de navegador aberta.")
        janela = navegadores[0]  # a usada por último

        def executar() -> list[str]:
            try:
                if not self._trazer(janela.hwnd):
                    raise OSError("o Windows não deixou trazer o navegador para frente")
                self._atalho(janela.hwnd, CTRL, SHIFT, T)
                return [f"Reabri a última aba fechada no {janela.navegador}."]
            finally:
                self._voltar_ao_terminal()

        return Plano("reabrir a última aba fechada", [f"no {janela.navegador}"], executar)

    def focar(self, termo: Any) -> Plano:
        candidatas = self._combinam(termo)
        if not candidatas:
            raise ErroAcao(f"Não há janela aberta que combine com '{termo}'.")
        janela = candidatas[0]

        def executar() -> list[str]:
            if not self._s.focar(janela.hwnd):
                raise OSError("o Windows não deixou trazer a janela para frente")
            self._devolver = False  # o usuário pediu esta janela na frente: ela fica
            return [f"Trouxe para frente a janela do {janela.programa}."]

        return Plano("trazer uma janela para frente", [janela.titulo], executar)

    def fechar_janela(self, termo: Any, todas: Any = False) -> Plano:
        candidatas = self._combinam(termo)
        if not candidatas:
            raise ErroAcao(f"Não há janela aberta que combine com '{termo}'.")
        if len(candidatas) > 1 and todas is not True:
            raise ErroAcao(
                f"{len(candidatas)} janelas combinam com '{termo}'. Use um termo mais específico, "
                "ou marque 'todas' se o usuário pediu para fechar todas."
            )

        def executar() -> list[str]:
            for janela in candidatas:
                self._s.pedir_fechamento(janela.hwnd)
            programas = sorted({j.programa for j in candidatas})
            quantas = "a janela" if len(candidatas) == 1 else f"{len(candidatas)} janelas"
            return [f"Pedi para fechar {quantas} ({', '.join(programas)}). Se houver algo sem salvar, o programa pergunta antes."]

        titulo = "fechar uma janela" if len(candidatas) == 1 else f"fechar {len(candidatas)} janelas"
        return Plano(titulo, [j.titulo for j in candidatas[:15]], executar)


    # --- dentro da página -------------------------------------------------

    def fim_do_turno(self, devolver_foco: bool = True) -> None:
        """A conversa chama ao terminar cada resposta: a foto perde a validade e o foco volta ao terminal."""
        self._captura = None
        self._alvo = None
        if self._devolver and devolver_foco:
            self._voltar_ao_terminal()
        self._devolver = False

    def _recusar_reservada(self, titulo: str, de_programa: bool = False) -> None:
        termo = reservada(titulo, de_programa)
        if termo is not None:
            raise ErroAcao(
                f"{'A janela' if de_programa else 'A aba à mostra'} parece ser de banco, pagamento ou senha ('{termo}' no título). "
                "O Jarvis não olha nem mexe nesse tipo de página; diga isso ao usuário."
            )

    def _fotografar(self, hwnd: int, de_programa: bool = False) -> Foto:
        titulo = self._s.titulo(hwnd)
        if not titulo:
            raise OSError("a janela fechou")
        self._recusar_reservada(titulo, de_programa)
        self._s.pausa(ESPERA_DA_PAGINA)
        esquerda, topo, direita, base = caixa = self._s.retangulo(hwnd)
        largura, altura = direita - esquerda, base - topo
        if largura < 200 or altura < 200:
            raise OSError("a janela está pequena demais para ver o que há nela")
        escala = min(1.0, LADO_MAX / largura, LADO_MAX / altura, (PIXELS_MAX / (largura * altura)) ** 0.5)
        na_foto = (max(1, int(largura * escala)), max(1, int(altura * escala)))  # para baixo: nunca passa do limite
        dados = self._s.fotografar(caixa, *na_foto)
        self._captura = Captura(hwnd, caixa, *na_foto, de_programa)
        self._devolver = True
        return Foto(dados, *na_foto)

    def ver(self, aba: Any = None, esperar: Any = None) -> Foto:
        """Foto da janela do navegador, com a aba pedida (ou a que estiver) à mostra.

        `esperar` são segundos a mais antes da foto, para uma página que acabou de ser aberta.
        """
        esperar = self._espera(esperar)
        if self._alvo is not None:
            # Neste turno o alvo é a janela de um programa: a foto é dela, e não se troca de alvo.
            if aba is not None and not (isinstance(aba, str) and not aba.strip()):
                raise ErroAcao("Neste turno você está na janela de um programa; abas são do navegador. Peça ao usuário em uma nova fala.")
            if not self._trazer(self._alvo):
                raise OSError("o Windows não deixou trazer a janela do programa para frente")
            hwnd = self._alvo
        else:
            hwnd = self._navegador_em_uso(aba).hwnd
        if esperar:
            self._s.pausa(esperar)
        return self._fotografar(hwnd, de_programa=self._alvo is not None)

    @staticmethod
    def _espera(esperar: Any) -> float:
        if esperar is None:
            return 0.0
        if isinstance(esperar, bool) or not isinstance(esperar, (int, float)) or not (0 <= esperar <= MAX_ESPERA):
            raise ErroAcao(f"'esperar' vai de 0 a {MAX_ESPERA} segundos.")
        return float(esperar)

    def ver_programa(self, nome: Any, esperar: Any = None) -> Foto:
        """Foto da janela de um programa liberado. Ela passa a ser o alvo deste turno."""
        esperar = self._espera(esperar)
        candidatas = self._combinam(nome)
        if not candidatas:
            raise ErroAcao(f"Não há janela aberta que combine com '{nome}'.")
        liberadas = [j for j in candidatas if j.programa in self.operaveis]
        if not liberadas:
            primeira = candidatas[0]
            if primeira.navegador:
                raise ErroAcao("Isso é um navegador: para ele, use tela_ver.")
            if primeira.programa in NUNCA_NA_TELA:
                raise ErroAcao(
                    f"O Jarvis não vê nem mexe na janela do {primeira.programa}: terminais, o Explorador de Arquivos e "
                    "ferramentas do sistema ficam sempre de fora. Diga isso ao usuário."
                )
            raise ErroAcao(
                f"O programa '{primeira.programa or 'dessa janela'}' não está na lista dos que o Jarvis pode operar. "
                "O usuário pode liberar em JARVIS_PROGRAMAS_TELA, no arquivo .env. Diga isso a ele."
            )
        janela = liberadas[0]
        if self._alvo is not None and self._alvo != janela.hwnd:
            raise ErroAcao("Neste turno você já está em outra janela. Para trocar de programa, o usuário precisa pedir em uma nova fala.")
        if self._alvo is None and self._captura is not None:
            raise ErroAcao("Neste turno você já viu o navegador. Para ir a um programa, o usuário precisa pedir em uma nova fala.")
        if not self._trazer(janela.hwnd):
            raise OSError("o Windows não deixou trazer a janela do programa para frente")
        if esperar:
            self._s.pausa(esperar)
        foto = self._fotografar(janela.hwnd, de_programa=True)
        self._alvo = janela.hwnd
        return foto

    def rever(self) -> Foto | None:
        """Foto nova da mesma janela, depois de uma ação. None se não havia foto ou ela não vale mais."""
        captura = self._captura
        if captura is None or not self._na_frente(captura.hwnd):
            return None
        return self._fotografar(captura.hwnd, captura.de_programa)

    def _captura_em_uso(self) -> Captura:
        if self._captura is None:
            raise ErroAcao("Veja a janela antes (tela_ver ou programa_ver): o clique e o texto usam a foto mais recente deste turno.")
        return self._captura

    def _conferir(self, captura: Captura) -> None:
        """Antes de agir: a janela fotografada ainda está lá, no mesmo lugar, na frente, e não virou página reservada."""
        titulo = self._s.titulo(captura.hwnd)
        if not titulo:
            raise OSError("a janela fechou")
        self._recusar_reservada(titulo, captura.de_programa)
        if self._s.retangulo(captura.hwnd) != captura.caixa:
            raise OSError("a janela mudou de lugar ou de tamanho; veja de novo antes de continuar")
        if not self._na_frente(captura.hwnd) and not self._trazer(captura.hwnd):
            raise OSError("o Windows não deixou trazer a janela para frente")

    def clicar(self, x: Any, y: Any, duplo: Any = False) -> Plano:
        captura = self._captura_em_uso()
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise ErroAcao("x e y são números inteiros: o ponto na foto mais recente.")
        if not (0 <= x < captura.largura and 0 <= y < captura.altura):
            raise ErroAcao(f"Ponto fora da foto, que tem {captura.largura} por {captura.altura}.")

        def executar() -> list[str]:
            self._conferir(captura)
            esquerda, topo, direita, base = captura.caixa
            na_tela_x = esquerda + min(direita - esquerda - 1, int((x + 0.5) * (direita - esquerda) / captura.largura))
            na_tela_y = topo + min(base - topo - 1, int((y + 0.5) * (base - topo) / captura.altura))
            if self._s.janela_em(na_tela_x, na_tela_y) != captura.hwnd:
                raise OSError("há outra janela por cima desse ponto; não cliquei")
            if not self._na_frente(captura.hwnd):
                raise OSError("a janela saiu do primeiro plano; não cliquei")
            self._s.clicar(na_tela_x, na_tela_y, duplo is True)
            self._s.pausa(ESPERA_DO_CLIQUE)
            self._devolver = True
            return [f"Cliquei{' duas vezes' if duplo is True else ''} em ({x}, {y})."]

        titulo = "dar dois cliques na janela" if duplo is True else "clicar na janela"
        return Plano(titulo, [f"ponto ({x}, {y}) da foto, em: {pagina(self._s.titulo(captura.hwnd))}"], executar)

    def digitar(self, texto: Any, enter: Any = False) -> Plano:
        captura = self._captura_em_uso()
        if not isinstance(texto, str) or not texto:
            raise ErroAcao("Diga o texto a digitar.")
        if len(texto) > MAX_TEXTO:
            raise ErroAcao(f"Texto longo demais: o limite é de {MAX_TEXTO} caracteres por vez.")
        if any(ord(c) < 32 or ord(c) == 127 for c in texto):
            raise ErroAcao("O texto não pode ter quebra de linha nem caractere de controle. Para confirmar, use 'enter'.")

        def executar() -> list[str]:
            self._conferir(captura)
            for inicio in range(0, len(texto), LOTE_DE_TEXTO):
                if not self._na_frente(captura.hwnd):
                    raise OSError("a janela saiu do primeiro plano; parei de digitar")
                self._s.digitar(texto[inicio : inicio + LOTE_DE_TEXTO])
            self._s.pausa(PAUSA)
            if enter is True:
                self._atalho(captura.hwnd, ENTER, com_dialogos=True)
            self._s.pausa(ESPERA_DO_CLIQUE)
            self._devolver = True
            return ["Digitei o texto e apertei Enter." if enter is True else "Digitei o texto."]

        return Plano("digitar na janela", [texto + ("  [Enter]" if enter is True else "")], executar)

    def tecla(self, nome: Any) -> Plano:
        if not isinstance(nome, str) or nome not in TECLAS_DE_PAGINA:
            raise ErroAcao("Tecla desconhecida. Use uma destas: " + ", ".join(TECLAS_DE_PAGINA) + ".")
        if self._captura is None and not self._navegadores():
            raise ErroAcao("Não há nenhuma janela de navegador aberta.")

        def executar() -> list[str]:
            if self._captura is not None:
                self._conferir(self._captura)
                hwnd = self._captura.hwnd
            else:
                hwnd = self._navegador_em_uso().hwnd
                self._recusar_reservada(self._s.titulo(hwnd))
            self._atalho(hwnd, *TECLAS_DE_PAGINA[nome], com_dialogos=True)
            self._s.pausa(ESPERA_DO_CLIQUE)
            self._devolver = True
            return [f"Apertei a tecla {nome}."]

        return Plano("apertar uma tecla na janela", [nome], executar)

    def rolar(self, direcao: Any, telas: Any = 1, aba: Any = None) -> Plano:
        if direcao not in ("baixo", "cima", "topo", "fim"):
            raise ErroAcao("A direção é baixo, cima, topo ou fim.")
        if telas is None:
            telas = 1
        if isinstance(telas, bool) or not isinstance(telas, int) or not (1 <= telas <= MAX_TELAS):
            raise ErroAcao(f"'telas' vai de 1 a {MAX_TELAS}.")
        if aba is not None and (not isinstance(aba, str) or (aba.strip() and len(chave(aba)) < 2)):
            raise ErroAcao("Diga qual aba: uma palavra do título da página.")
        com_aba = isinstance(aba, str) and bool(aba.strip())
        if self._alvo is not None and com_aba:
            raise ErroAcao("Neste turno você está na janela de um programa; abas são do navegador. Peça ao usuário em uma nova fala.")
        if self._alvo is None and not self._navegadores():
            raise ErroAcao("Não há nenhuma janela de navegador aberta.")

        def executar() -> list[str]:
            if self._alvo is not None:
                if not self._trazer(self._alvo):
                    raise OSError("o Windows não deixou trazer a janela do programa para frente")
                hwnd = self._alvo
            else:
                hwnd = self._navegador_em_uso(aba).hwnd
            self._devolver = True
            if direcao in ("topo", "fim"):
                self._atalho(hwnd, CTRL, HOME if direcao == "topo" else END, com_dialogos=True)
                return [f"Fui para o {direcao} da página."]
            esquerda, topo, direita, base = self._s.retangulo(hwnd)
            x, y = (esquerda + direita) // 2, topo + (base - topo) * 3 // 5
            if self._s.janela_em(x, y) != hwnd:
                raise OSError("há outra janela por cima; não rolei")
            if not self._na_frente(hwnd):
                raise OSError("a janela saiu do primeiro plano; não rolei")
            passos = telas * PASSOS_POR_TELA
            self._s.roda(x, y, passos if direcao == "cima" else -passos)
            self._s.pausa(ESPERA_DA_PAGINA)
            return [f"Rolei a página para {direcao}."]

        quanto = "uma tela" if telas == 1 else f"{telas} telas"
        linha = f"para {direcao}, {quanto}" if direcao in ("baixo", "cima") else f"até o {direcao}"
        return Plano("rolar a página", [linha + (f", na aba '{aba}'" if aba and aba.strip() else "")], executar)

    def midia(self, comando: Any, vezes: Any = 1) -> Plano:
        if not isinstance(comando, str) or comando not in MIDIA:
            raise ErroAcao("Comando de mídia desconhecido. Use um destes: " + ", ".join(MIDIA) + ".")
        if vezes is None:
            vezes = 1
        if isinstance(vezes, bool) or not isinstance(vezes, int) or not (1 <= vezes <= 20):
            raise ErroAcao("'vezes' vai de 1 a 20.")
        if not comando.startswith("volume"):
            vezes = 1  # repetir tocar/pausar ou mudo desfaz o que acabou de fazer

        def executar() -> list[str]:
            for _ in range(vezes):
                self._s.teclas(MIDIA[comando])
                self._s.pausa(0.05)
            return [f"Enviei o comando de mídia: {comando}."]

        return Plano("comando de mídia", [comando + (f", {vezes} vezes" if vezes > 1 else "")], executar)


# Estruturas do SendInput (digitação de texto). Tipos de tamanho fixo: 40 bytes em 64 bits.
class _Tecla(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t),
    ]  # fmt: skip


class _Mouse(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t),
    ]  # fmt: skip


class _Uniao(ctypes.Union):
    _fields_ = [("ki", _Tecla), ("mi", _Mouse)]


class _Entrada(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _Uniao)]


def unidades_utf16(texto: str) -> list[int]:
    """O texto como o Windows o recebe: uma unidade de 16 bits por vez (emoji viram duas)."""
    bruto = texto.encode("utf-16-le")
    return [int.from_bytes(bruto[i : i + 2], "little") for i in range(0, len(bruto), 2)]


def foto_da_tela(caixa: tuple[int, int, int, int], largura: int, altura: int) -> bytes:
    """Fotografa uma região da tela e devolve um JPEG no tamanho pedido."""
    try:
        from PIL import Image, ImageGrab
    except ImportError as erro:
        raise OSError("falta a biblioteca Pillow; rode: pip install -r requirements.txt") from erro
    imagem = ImageGrab.grab(bbox=caixa, all_screens=True)
    if imagem.size != (largura, altura):
        imagem = imagem.resize((largura, altura), Image.LANCZOS)
    saida = io.BytesIO()
    imagem.convert("RGB").save(saida, format="JPEG", quality=80)
    return saida.getvalue()


class SistemaWindows:
    """As operações acima, com a API do Windows (user32), via ctypes, e a foto da tela pelo Pillow."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self._ct, self._wt = ctypes, wintypes
        u = self._u = ctypes.WinDLL("user32", use_last_error=True)
        k = self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self._dwm = ctypes.WinDLL("dwmapi")
        except OSError:
            self._dwm = None

        HWND, DWORD, BOOL = wintypes.HWND, wintypes.DWORD, wintypes.BOOL
        self._ENUM = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes, u.EnumWindows.restype = [self._ENUM, wintypes.LPARAM], BOOL
        u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [HWND], BOOL
        u.IsIconic.argtypes, u.IsIconic.restype = [HWND], BOOL
        u.GetWindowTextLengthW.argtypes, u.GetWindowTextLengthW.restype = [HWND], ctypes.c_int
        u.GetWindowTextW.argtypes, u.GetWindowTextW.restype = [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
        u.GetWindowThreadProcessId.restype = DWORD
        u.GetWindow.argtypes, u.GetWindow.restype = [HWND, wintypes.UINT], HWND
        u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = [HWND, ctypes.c_int], wintypes.LONG
        u.GetForegroundWindow.argtypes, u.GetForegroundWindow.restype = [], HWND
        u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = [HWND], BOOL
        u.BringWindowToTop.argtypes, u.BringWindowToTop.restype = [HWND], BOOL
        u.ShowWindow.argtypes, u.ShowWindow.restype = [HWND, ctypes.c_int], BOOL
        u.AttachThreadInput.argtypes, u.AttachThreadInput.restype = [DWORD, DWORD, BOOL], BOOL
        u.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = BOOL
        u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, DWORD, ctypes.c_size_t]
        u.keybd_event.restype = None
        k.GetCurrentThreadId.argtypes, k.GetCurrentThreadId.restype = [], DWORD
        k.GetConsoleWindow.argtypes, k.GetConsoleWindow.restype = [], HWND
        k.OpenProcess.argtypes, k.OpenProcess.restype = [DWORD, BOOL, DWORD], wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)]
        k.QueryFullProcessImageNameW.restype = BOOL
        k.CloseHandle.argtypes, k.CloseHandle.restype = [wintypes.HANDLE], BOOL
        if self._dwm is not None:
            self._dwm.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
            self._dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        # Mouse, texto e medidas
        u.GetWindowRect.argtypes, u.GetWindowRect.restype = [HWND, ctypes.POINTER(wintypes.RECT)], BOOL
        u.GetSystemMetrics.argtypes, u.GetSystemMetrics.restype = [ctypes.c_int], ctypes.c_int
        u.WindowFromPoint.argtypes, u.WindowFromPoint.restype = [wintypes.POINT], HWND
        u.GetAncestor.argtypes, u.GetAncestor.restype = [HWND, wintypes.UINT], HWND
        u.GetCursorPos.argtypes, u.GetCursorPos.restype = [ctypes.POINTER(wintypes.POINT)], BOOL
        u.SetCursorPos.argtypes, u.SetCursorPos.restype = [ctypes.c_int, ctypes.c_int], BOOL
        u.mouse_event.argtypes, u.mouse_event.restype = [DWORD, DWORD, DWORD, DWORD, ctypes.c_size_t], None
        u.SendInput.argtypes, u.SendInput.restype = [wintypes.UINT, ctypes.POINTER(_Entrada), ctypes.c_int], wintypes.UINT

        # Com a tela em 125% ou 150%, o Windows mente as medidas para programas antigos. Pedimos
        # pixels de verdade, os mesmos da foto da tela; sem isso o clique cairia fora do lugar.
        self._por_thread = None
        try:
            u.SetProcessDpiAwarenessContext.argtypes, u.SetProcessDpiAwarenessContext.restype = [wintypes.HANDLE], BOOL
            u.SetProcessDpiAwarenessContext(-4)  # por monitor, versão 2; falha sem dano se já estava definido
        except (AttributeError, OSError):
            try:
                u.SetProcessDPIAware()  # Windows anterior ao 10
            except (AttributeError, OSError):
                pass
        try:
            u.SetThreadDpiAwarenessContext.argtypes, u.SetThreadDpiAwarenessContext.restype = [wintypes.HANDLE], wintypes.HANDLE
            self._por_thread = u.SetThreadDpiAwarenessContext
        except AttributeError:
            pass  # Windows anterior ao 10: vale o ajuste do processo
        self._pixels_de_verdade()

        # Qual janela é o terminal do Jarvis. No console clássico é a própria janela do
        # console. No Windows Terminal o console é uma janela escondida, então vale a que
        # estava em primeiro plano quando o Jarvis subiu, a menos que seja um navegador
        # (o usuário já tinha trocado de janela): aí é melhor não adivinhar.
        self._console = int(k.GetConsoleWindow() or 0)
        frente = int(u.GetForegroundWindow() or 0)
        if self._console and u.IsWindowVisible(self._console):
            self.propria = self._console
        elif frente and self._processo(frente).lower() not in NAVEGADORES:
            self.propria = frente
        else:
            self.propria = 0

    def titulo(self, hwnd: int) -> str:
        # Janela fechada pode continuar existindo por um tempo, escondida e com o título antigo.
        if not self._u.IsWindowVisible(hwnd):
            return ""
        tamanho = self._u.GetWindowTextLengthW(hwnd)
        if tamanho <= 0:
            return ""
        texto = self._ct.create_unicode_buffer(tamanho + 1)
        self._u.GetWindowTextW(hwnd, texto, tamanho + 1)
        return texto.value

    def _processo(self, hwnd: int) -> str:
        pid = self._wt.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, self._ct.byref(pid))
        processo = self._k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not processo:
            return ""
        try:
            caminho = self._ct.create_unicode_buffer(1024)
            tamanho = self._wt.DWORD(1024)
            if not self._k.QueryFullProcessImageNameW(processo, 0, caminho, self._ct.byref(tamanho)):
                return ""
            return ntpath.basename(caminho.value)
        finally:
            self._k.CloseHandle(processo)

    def _oculta(self, hwnd: int) -> bool:
        """Janela "camuflada": existe para o sistema, mas não aparece para o usuário."""
        if self._dwm is None:
            return False
        valor = self._wt.DWORD(0)
        ok = self._dwm.DwmGetWindowAttribute(hwnd, 14, self._ct.byref(valor), self._ct.sizeof(valor))  # DWMWA_CLOAKED
        return ok == 0 and valor.value != 0

    def listar(self) -> list[Janela]:
        achadas: list[Janela] = []

        def visitar(hwnd: Any, _extra: Any) -> bool:
            h = int(hwnd or 0)
            if (
                h
                and h != self._console
                and self._u.IsWindowVisible(h)
                and not self._u.GetWindow(h, 4)  # GW_OWNER: caixas de diálogo ficam de fora
                and not (self._u.GetWindowLongW(h, -20) & 0x80)  # WS_EX_TOOLWINDOW
                and not self._oculta(h)
            ):
                titulo = self.titulo(h)
                if titulo:
                    achadas.append(Janela(h, titulo, self._processo(h)))
            return True

        self._u.EnumWindows(self._ENUM(visitar), 0)
        return achadas

    def ativa(self) -> int:
        hwnd = int(self._u.GetForegroundWindow() or 0)
        if hwnd and not self._u.IsWindowVisible(hwnd):
            # Um diálogo que acabou de fechar pode continuar "em primeiro plano" por um instante,
            # já escondido: quem está de fato na frente é a janela a que ele pertencia.
            dona = int(self._u.GetAncestor(hwnd, 3) or 0)
            return dona if dona != hwnd and self._u.IsWindowVisible(dona) else 0
        return hwnd

    def _veio(self, hwnd: int) -> bool:
        """Espera um instante a janela chegar ao primeiro plano."""
        for _ in range(5):
            if self.ativa() == hwnd:
                return True
            time.sleep(0.05)
        return False

    def _pedir_frente_ligado(self, hwnd: int) -> None:
        """O Windows só deixa trocar o primeiro plano quem está ligado à janela ativa."""
        u = self._u
        eu = self._k.GetCurrentThreadId()
        dona = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        ligado = bool(dona and dona != eu and u.AttachThreadInput(eu, dona, True))
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            if ligado:
                u.AttachThreadInput(eu, dona, False)

    def _pedir_frente_com_alt(self, hwnd: int) -> None:
        """Quem gerou a última tecla pode trocar o primeiro plano. Dois toques de Alt: o segundo desfaz o primeiro."""
        for _ in range(2):
            self._u.keybd_event(ALT, 0, 0, 0)
            self._u.keybd_event(ALT, 0, 2, 0)
        self._u.SetForegroundWindow(hwnd)

    def focar(self, hwnd: int) -> bool:
        if self._u.IsIconic(hwnd):
            self._u.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.5)  # a janela leva um instante para voltar e aceitar teclas; cedo demais, elas se perdem
        for pedir in (self._u.SetForegroundWindow, self._pedir_frente_ligado, self._pedir_frente_com_alt):
            pedir(hwnd)
            if self._veio(hwnd):
                return True
        return False

    def teclas(self, *codigos: int) -> None:
        apertadas: list[int] = []
        try:
            for codigo in codigos:
                self._u.keybd_event(codigo, 0, 1 if codigo in ESTENDIDAS else 0, 0)  # 1 = KEYEVENTF_EXTENDEDKEY
                apertadas.append(codigo)
        finally:  # nunca deixa uma tecla presa
            for codigo in reversed(apertadas):
                self._u.keybd_event(codigo, 0, (1 if codigo in ESTENDIDAS else 0) | 2, 0)  # 2 = KEYEVENTF_KEYUP

    def pedir_fechamento(self, hwnd: int) -> None:
        self._u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: o programa fecha como no X da janela

    # --- mouse, texto e foto ------------------------------------------------

    def _pixels_de_verdade(self) -> None:
        if self._por_thread is not None:
            self._por_thread(-4)

    def retangulo(self, hwnd: int) -> tuple[int, int, int, int]:
        self._pixels_de_verdade()
        r = self._wt.RECT()
        # A moldura visível: sem a borda invisível de redimensionar que o GetWindowRect inclui.
        visivel = self._dwm is not None and self._dwm.DwmGetWindowAttribute(hwnd, 9, self._ct.byref(r), self._ct.sizeof(r)) == 0
        if not visivel and not self._u.GetWindowRect(hwnd, self._ct.byref(r)):
            raise OSError("não consegui medir a janela")
        # Só a parte que cabe na área de trabalho (todas as telas juntas).
        x0, y0 = self._u.GetSystemMetrics(76), self._u.GetSystemMetrics(77)
        x1, y1 = x0 + self._u.GetSystemMetrics(78), y0 + self._u.GetSystemMetrics(79)
        return (max(r.left, x0), max(r.top, y0), min(r.right, x1), min(r.bottom, y1))

    def fotografar(self, caixa: tuple[int, int, int, int], largura: int, altura: int) -> bytes:
        self._pixels_de_verdade()
        return foto_da_tela(caixa, largura, altura)

    def janela_em(self, x: int, y: int) -> int:
        self._pixels_de_verdade()
        alvo = self._u.WindowFromPoint(self._wt.POINT(x, y))
        # A dona de quem está no ponto: um menu aberto pela página conta como a janela do navegador.
        return int(self._u.GetAncestor(alvo, 3) or 0) if alvo else 0  # GA_ROOTOWNER

    def dona(self, hwnd: int) -> int:
        return int(self._u.GetAncestor(hwnd, 3) or 0) if hwnd else 0  # GA_ROOTOWNER

    def clicar(self, x: int, y: int, duplo: bool = False) -> None:
        self._pixels_de_verdade()
        if not self._u.SetCursorPos(x, y):
            raise OSError("não consegui mover o ponteiro do mouse")
        time.sleep(0.1)  # a página percebe o ponteiro antes do clique
        for _ in range(2 if duplo else 1):
            self._u.mouse_event(0x0002, 0, 0, 0, 0)  # botão esquerdo desce
            self._u.mouse_event(0x0004, 0, 0, 0, 0)  # e sobe
            time.sleep(0.05)

    def roda(self, x: int, y: int, passos: int) -> None:
        self._pixels_de_verdade()
        antes = self._wt.POINT()
        guardou = bool(self._u.GetCursorPos(self._ct.byref(antes)))
        if not self._u.SetCursorPos(x, y):
            raise OSError("não consegui mover o ponteiro do mouse")
        time.sleep(0.05)
        um_dente = (120 if passos > 0 else -120) & 0xFFFFFFFF
        for _ in range(abs(passos)):
            self._u.mouse_event(0x0800, 0, 0, um_dente, 0)  # MOUSEEVENTF_WHEEL
            time.sleep(0.02)
        time.sleep(0.15)
        if guardou:
            self._u.SetCursorPos(antes.x, antes.y)  # o ponteiro volta para onde o usuário o deixou

    def digitar(self, texto: str) -> None:
        for unidade in unidades_utf16(texto):
            par = (_Entrada * 2)()
            for entrada, sobe in zip(par, (0, 0x0002)):
                entrada.type = 1  # teclado
                entrada.u.ki = _Tecla(0, unidade, 0x0004 | sobe, 0, 0)  # KEYEVENTF_UNICODE: a letra, não a tecla
            if self._u.SendInput(2, par, self._ct.sizeof(_Entrada)) != 2:
                raise OSError("o Windows recusou a digitação")
            time.sleep(0.01)

    def pausa(self, segundos: float) -> None:
        time.sleep(segundos)
