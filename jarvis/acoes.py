"""Ações: o que o Jarvis faz na máquina além de ler.

Cada ação passa por três etapas, nesta ordem:

1. planejar  — valida o pedido e monta um Plano. Pedido inválido nem chega ao usuário.
2. confirmar — quem chama mostra o Plano e espera o usuário digitar "s" (ver ferramentas.py).
3. executar  — só roda com a confirmação; devolve uma linha por item, com o que deu certo e o que não.

Nenhuma ação apaga de verdade (vai para a Lixeira), sobrescreve um arquivo existente,
sai das pastas liberadas ou roda um executável que não esteja na lista de programas.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unicodedata
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jarvis import guarda

MAX_ITENS = 200
MAX_LINHAS_NO_PLANO = 15

# Abrir um destes é executar código. Arquivo assim não é aberto, venha de onde vier.
EXTENSOES_EXECUTAVEIS = frozenset(
    ".exe .msi .msix .msp .mst .appx .bat .cmd .com .scr .pif .cpl .msc .dll .xll .ps1 .psm1 .vbs .vbe .vb "
    ".js .jse .ws .wsf .wsh .wsc .sct .hta .chm .jar .lnk .url .website .reg .inf .scf .gadget .diagcab "
    ".application .appref-ms .settingcontent-ms .library-ms .iso .img .vhd .vhdx "
    ".py .pyw .sh .command .app".split()
)

# Nome falado → comando. Sem argumentos: o Jarvis abre o programa, não passa instruções a ele.
PROGRAMAS_PADRAO = {
    "calculadora": "calc",
    "bloco de notas": "notepad",
    "explorador de arquivos": "explorer",
    "paint": "mspaint",
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "excel": "excel",
    "word": "winword",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
}


# Programas da lista que aceitam um endereço para abrir.
NAVEGADORES = ("chrome", "edge", "firefox")


class ErroAcao(Exception):
    """Pedido recusado na validação; a mensagem volta para o modelo como erro da ferramenta."""


@dataclass
class Plano:
    titulo: str  # "mover 3 itens"
    linhas: list[str]  # o que será feito, uma linha por item
    executar: Callable[[], list[str]]  # roda a ação; devolve uma linha de resultado por item


def chave(nome: str) -> str:
    """'Bloco de Notas' → 'bloco de notas' (sem acento, minúsculas, um espaço)."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


def ler_programas(bruto: str) -> dict[str, str]:
    """JARVIS_PROGRAMAS: 'vscode=code; spotify=C:\\caminho\\Spotify.exe'."""
    programas: dict[str, str] = {}
    for trecho in bruto.split(";"):
        if "=" not in trecho:
            continue
        nome, comando = trecho.split("=", 1)
        if chave(nome) and comando.strip():
            programas[chave(nome)] = comando.strip().strip('"')
    return programas


def _abrir_no_sistema(alvo: str) -> None:
    """Abre com o programa padrão, como um duplo clique."""
    if sys.platform == "win32":
        os.startfile(alvo)  # noqa: S606 - alvo já validado; sem shell e sem argumentos
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", alvo])  # noqa: S603


def _iniciar_programa(comando: str) -> None:
    if sys.platform == "win32":
        os.startfile(comando)  # noqa: S606 - comando vem da lista de programas, sem argumentos
    else:
        subprocess.Popen([comando])  # noqa: S603


def _abrir_site(url: str) -> None:
    if not webbrowser.open(url):
        raise OSError("nenhum navegador respondeu")


def _abrir_site_em(comando: str, url: str) -> None:
    """Abre o endereço em um navegador específico. O endereço já foi validado e vai entre aspas."""
    if sys.platform == "win32":
        os.startfile(comando, arguments=f'"{url}"')  # noqa: S606
    else:
        subprocess.Popen([comando, url])  # noqa: S603


def _mandar_para_lixeira(caminho: str) -> None:
    try:
        from send2trash import send2trash
    except ImportError as erro:
        raise OSError("falta a biblioteca send2trash; rode: pip install -r requirements.txt") from erro
    try:
        send2trash(caminho)
    except OSError:
        raise
    except Exception as erro:  # noqa: BLE001 - no Windows a biblioteca às vezes deixa escapar um erro COM
        raise OSError(f"a Lixeira recusou o item ({type(erro).__name__}: {erro})") from erro


def _resumir(linhas: list[str]) -> list[str]:
    if len(linhas) <= MAX_LINHAS_NO_PLANO:
        return linhas
    return [*linhas[:MAX_LINHAS_NO_PLANO], f"… e mais {len(linhas) - MAX_LINHAS_NO_PLANO}"]


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


class Acoes:
    def __init__(
        self,
        pastas: Sequence[Path],
        protegidas: Sequence[Path] = (),
        programas: dict[str, str] | None = None,
        abrir: Callable[[str], None] = _abrir_no_sistema,
        iniciar: Callable[[str], None] = _iniciar_programa,
        navegar: Callable[[str], None] = _abrir_site,
        lixeira: Callable[[str], None] = _mandar_para_lixeira,
        navegar_em: Callable[[str, str], None] = _abrir_site_em,
    ) -> None:
        self._pastas = [Path(os.path.realpath(p)) for p in pastas]
        self._cwd = self._pastas[0]
        # Pastas que as ações nunca tocam, mesmo dentro de uma pasta liberada (o próprio Jarvis).
        self._protegidas = [Path(os.path.realpath(p)) for p in protegidas]
        self.programas = {**PROGRAMAS_PADRAO, **(programas or {})}
        self._abrir, self._iniciar, self._navegar, self._lixeira = abrir, iniciar, navegar, lixeira
        self._navegar_em = navegar_em

    @property
    def navegadores(self) -> list[str]:
        return [n for n in NAVEGADORES if n in self.programas]

    # --- validação de caminho ---------------------------------------------

    def _caminho(self, bruto: Any, papel: str) -> Path:
        if not isinstance(bruto, str) or not bruto.strip():
            raise ErroAcao(f"Falta o caminho de {papel}.")
        recusa = guarda.recusa_de_caminho(bruto.strip(), self._pastas, self._cwd)
        if recusa:
            raise ErroAcao(recusa)
        caminho = guarda.resolver(bruto.strip(), self._cwd)
        for protegida in self._protegidas:
            if caminho == protegida or caminho.is_relative_to(protegida):
                raise ErroAcao(f"A pasta do próprio Jarvis não pode ser alterada: {caminho}")
        return caminho

    def _existente(self, bruto: Any, papel: str) -> Path:
        caminho = self._caminho(bruto, papel)
        if not caminho.exists():
            raise ErroAcao(f"Não existe: {caminho}")
        if caminho in self._pastas:
            raise ErroAcao(f"Uma pasta liberada inteira não pode ser movida, copiada nem apagada: {caminho}")
        return caminho

    def _destino_final(self, origem: Path, bruto: Any) -> Path:
        destino = self._caminho(bruto, "destino")
        if destino.is_dir():  # "mover para a pasta X" → X\nome-do-arquivo
            destino = self._caminho(str(destino / origem.name), "destino")
        if destino.exists():
            raise ErroAcao(f"Já existe um item no destino (nada é sobrescrito): {destino}")
        if destino == origem or destino.is_relative_to(origem):
            raise ErroAcao(f"O destino fica dentro da própria origem: {destino}")
        return destino

    def _pares(self, itens: Any) -> list[tuple[Path, Path]]:
        if not isinstance(itens, list) or not itens:
            raise ErroAcao("Informe ao menos um item, com origem e destino.")
        if len(itens) > MAX_ITENS:
            raise ErroAcao(f"No máximo {MAX_ITENS} itens por vez.")
        pares, destinos = [], set()
        for item in itens:
            if not isinstance(item, dict):
                raise ErroAcao("Cada item precisa de origem e destino.")
            origem = self._existente(item.get("origem"), "origem")
            destino = self._destino_final(origem, item.get("destino"))
            if destino in destinos:
                raise ErroAcao(f"Dois itens iriam para o mesmo destino: {destino}")
            destinos.add(destino)
            pares.append((origem, destino))
        return pares

    # --- abrir ------------------------------------------------------------

    def abrir_site(self, url: Any, navegador: Any = None) -> Plano:
        if not isinstance(url, str) or not url.strip():
            raise ErroAcao("Falta o endereço do site.")
        url = url.strip()
        partes = urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ErroAcao("Só abro endereços que começam com http:// ou https://.")
        if partes.username or partes.password or len(url) > 2000:
            raise ErroAcao("Endereço recusado: tem usuário e senha embutidos ou é longo demais.")
        if any(c.isspace() or ord(c) < 32 or c in '"<>^|`' for c in url):
            raise ErroAcao("Endereço recusado: tem espaço, aspas ou outro caractere que não cabe em um endereço.")

        comando = None
        if navegador:
            if not isinstance(navegador, str) or chave(navegador) not in self.navegadores:
                raise ErroAcao(f"Navegador desconhecido: '{navegador}'. Use um de: {', '.join(self.navegadores)}.")
            navegador = chave(navegador)
            comando = self.programas[navegador]

        def executar() -> list[str]:
            if comando is None:
                self._navegar(url)
                return [f"Abri {url} no navegador padrão."]
            self._navegar_em(comando, url)
            return [f"Abri {url} no {navegador}."]

        onde = f"no {navegador}" if comando else "no navegador padrão"
        return Plano("abrir um site", [f"{url}  ({onde})", f"(domínio: {partes.hostname})"], executar)

    def abrir_arquivo(self, bruto: Any) -> Plano:
        caminho = self._caminho(bruto, "arquivo")
        if not caminho.exists():
            raise ErroAcao(f"Não existe: {caminho}")
        if caminho.is_file() and caminho.suffix.lower() in EXTENSOES_EXECUTAVEIS:
            raise ErroAcao(f"Não abro arquivos executáveis ou scripts ({caminho.suffix}): {caminho.name}")
        tipo = "uma pasta" if caminho.is_dir() else "um arquivo"

        def executar() -> list[str]:
            self._abrir(str(caminho))
            return [f"Abri {caminho}."]

        return Plano(f"abrir {tipo}", [str(caminho)], executar)

    def abrir_programa(self, nome: Any) -> Plano:
        if not isinstance(nome, str) or not chave(nome):
            raise ErroAcao("Falta o nome do programa.")
        comando = self.programas.get(chave(nome))
        if comando is None:
            conhecidos = ", ".join(sorted(self.programas))
            raise ErroAcao(
                f"'{nome}' não está na lista de programas. Conhecidos: {conhecidos}. "
                "O usuário pode acrescentar outros em JARVIS_PROGRAMAS, no arquivo .env."
            )

        def executar() -> list[str]:
            self._iniciar(comando)
            return [f"Abri o programa {chave(nome)}."]

        return Plano("abrir um programa", [f"{chave(nome)}  (comando: {comando})"], executar)

    # --- organizar arquivos -----------------------------------------------

    def criar_pasta(self, bruto: Any) -> Plano:
        caminho = self._caminho(bruto, "pasta")
        if caminho.exists():
            raise ErroAcao(f"Já existe: {caminho}")

        def executar() -> list[str]:
            caminho.mkdir(parents=True, exist_ok=False)
            return [f"Pasta criada: {caminho}"]

        return Plano("criar uma pasta", [str(caminho)], executar)

    def mover(self, itens: Any) -> Plano:
        return self._transferir(itens, "mover", "movido", shutil.move)

    def copiar(self, itens: Any) -> Plano:
        def copia(origem: str, destino: str) -> None:
            if os.path.isdir(origem):
                shutil.copytree(origem, destino)
            else:
                shutil.copy2(origem, destino)

        return self._transferir(itens, "copiar", "copiado", copia)

    def _transferir(self, itens: Any, verbo: str, feito: str, operacao: Callable[[str, str], Any]) -> Plano:
        pares = self._pares(itens)

        def executar() -> list[str]:
            resultado = []
            for origem, destino in pares:
                try:
                    if destino.exists():  # apareceu entre a confirmação e a execução
                        raise FileExistsError("o destino passou a existir")
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    operacao(str(origem), str(destino))
                    resultado.append(f"{feito}: {origem.name} → {destino}")
                except OSError as erro:
                    resultado.append(f"FALHOU: {origem} ({erro})")
            return resultado

        linhas = [f"{origem}  →  {destino}" for origem, destino in pares]
        return Plano(f"{verbo} {_plural(len(pares), 'item', 'itens')}", _resumir(linhas), executar)

    def apagar(self, caminhos: Any) -> Plano:
        if not isinstance(caminhos, list) or not caminhos:
            raise ErroAcao("Informe ao menos um caminho.")
        if len(caminhos) > MAX_ITENS:
            raise ErroAcao(f"No máximo {MAX_ITENS} itens por vez.")
        alvos = list(dict.fromkeys(self._existente(c, "item") for c in caminhos))

        def executar() -> list[str]:
            resultado = []
            for alvo in alvos:
                try:
                    self._lixeira(str(alvo))
                    resultado.append(f"na Lixeira: {alvo}")
                except OSError as erro:
                    resultado.append(f"FALHOU: {alvo} ({erro})")
            return resultado

        linhas = [f"{alvo}" + ("  (pasta inteira)" if alvo.is_dir() else "") for alvo in alvos]
        return Plano(f"mandar {_plural(len(alvos), 'item', 'itens')} para a Lixeira", _resumir(linhas), executar)
