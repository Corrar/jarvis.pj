"""Atalho do Jarvis na Área de Trabalho e no menu Iniciar, com ícone — para abrir sem achar a pasta.

Duas decisões que valem registro:

- **Quem diz onde é a Área de Trabalho é o Windows**, não um palpite com `%USERPROFILE%\\Desktop`.
  Com o OneDrive ligado (o caso de muita máquina de empresa) a pasta real é
  `...\\OneDrive\\Área de Trabalho`, e o palpite criaria o atalho numa pasta que o usuário não vê.
- **Os caminhos vão por variável de ambiente**, não escritos dentro do roteiro do PowerShell.
  Nome de usuário com `'` ou `"` quebraria o roteiro, e caminho montado por texto é como se
  escreve, sem querer, um lugar onde dá para injetar comando.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from jarvis.config import RAIZ_PROJETO
from jarvis.segundo_plano import desenhar_icone

log = logging.getLogger(__name__)

NOME = "Jarvis"
EXPLICACAO = "Liga o Jarvis em segundo plano, com o ícone perto do relógio"
ARQUIVO_DO_ICONE = "jarvis.ico"
TAMANHOS = (16, 24, 32, 48, 64, 128, 256)  # o Windows escolhe conforme o lugar; sem os pequenos, fica borrado
AZUL = (0, 140, 255)  # a mesma cor do ícone "pronto" na bandeja
ESPERA = 60.0

# Pede ao Windows as duas pastas e cria os dois atalhos. Imprime uma linha por atalho criado.
_ROTEIRO = r"""
$ErrorActionPreference = 'Stop'
$casca = New-Object -ComObject WScript.Shell
$lugares = @{
  'area'   = [Environment]::GetFolderPath('Desktop')
  'inicio' = [Environment]::GetFolderPath('Programs')
}
foreach ($par in $lugares.GetEnumerator()) {
  if (-not $par.Value) { continue }
  if (-not (Test-Path -LiteralPath $par.Value)) { continue }
  $caminho = Join-Path $par.Value ($env:JV_NOME + '.lnk')
  $atalho = $casca.CreateShortcut($caminho)
  $atalho.TargetPath = $env:JV_ALVO
  $atalho.Arguments = $env:JV_ARGUMENTOS
  $atalho.WorkingDirectory = $env:JV_PASTA
  $atalho.Description = $env:JV_EXPLICACAO
  if ($env:JV_ICONE) { $atalho.IconLocation = $env:JV_ICONE }
  $atalho.Save()
  Write-Output ('CRIADO ' + $caminho)
}
"""

# Apaga os dois, onde estiverem. Imprime uma linha por atalho apagado.
_ROTEIRO_REMOVER = r"""
$ErrorActionPreference = 'Stop'
foreach ($pasta in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  if (-not $pasta) { continue }
  $caminho = Join-Path $pasta ($env:JV_NOME + '.lnk')
  if (Test-Path -LiteralPath $caminho) {
    Remove-Item -LiteralPath $caminho -Force
    Write-Output ('APAGADO ' + $caminho)
  }
}
"""


class ErroAtalho(Exception):
    """Não deu para criar ou apagar o atalho."""


DEPENDENCIA = "claude_agent_sdk"  # sem isto o Jarvis não sobe; é o que separa o Python certo dos outros


def _tem_as_bibliotecas(executavel: Path, rodar: Callable[..., Any] = subprocess.run) -> bool:
    try:
        feito = rodar(  # noqa: S603 - executável vindo da lista de candidatos, argumento fixo
            [str(executavel), "-c", f"import {DEPENDENCIA}"],
            capture_output=True,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return feito.returncode == 0


def candidatos_de_python(raiz: Path, executavel: str = sys.executable) -> list[Path]:
    """Os Python que podem ser o do Jarvis, do mais provável ao menos.

    Quem digita `python -m jarvis` pode estar com outro Python no PATH (o da Loja, um mais novo,
    outro projeto) — o atalho tem de apontar para o que tem as bibliotecas, não para o que digitou.
    """
    lista = [Path(executavel)] if executavel else []
    lista += [raiz / nome / "Scripts" / "python.exe" for nome in (".venv", "venv", "env")]
    for achado in ("python", "python3", "python.exe"):
        caminho = shutil.which(achado)
        if caminho:
            lista.append(Path(caminho))
    try:  # o lançador do Windows sabe de todos os instalados
        feito = subprocess.run(  # noqa: S603 - linha fixa
            ["py", "-0p"], capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for linha in (feito.stdout or "").splitlines():
            pedaco = linha.strip().split("  ")[-1].strip().strip('"')
            if pedaco.lower().endswith(".exe"):
                lista.append(Path(pedaco))
    except (OSError, subprocess.SubprocessError):
        pass
    unicos: list[Path] = []
    for caminho in lista:
        if caminho not in unicos:
            unicos.append(caminho)
    return unicos


def python_do_jarvis(
    raiz: Path,
    executavel: str = sys.executable,
    conferir: Callable[[Path], bool] = _tem_as_bibliotecas,
) -> Path:
    """O primeiro Python da lista que consegue importar as bibliotecas do Jarvis."""
    tentados = []
    for caminho in candidatos_de_python(raiz, executavel):
        if not caminho.exists():
            continue
        tentados.append(caminho)
        if conferir(caminho):
            return caminho
    raise ErroAtalho(
        "não achei nesta máquina um Python com as bibliotecas do Jarvis instaladas"
        + (f" (tentei: {', '.join(str(t) for t in tentados[:4])})" if tentados else "")
        + ". Instale com: pip install -r requirements.txt"
    )


def sem_janela(executavel: Path) -> Path:
    """O `pythonw.exe` ao lado, quando existe: com o `python.exe` fica uma janela preta na tela."""
    calado = executavel.with_name("pythonw.exe")
    return calado if calado.exists() else executavel


def gerar_icone(destino: Path, desenhar: Callable[..., Any] = desenhar_icone) -> Path | None:
    """Grava o `.ico` com o disco azul e o J, em todos os tamanhos. `None` se o Pillow não está aqui.

    O ícone é gravado uma vez, em arquivo, porque o atalho do Windows aponta para um arquivo —
    diferente da bandeja, que desenha na hora e não precisa de nada em disco.
    """
    try:
        imagem = desenhar(AZUL, tamanho=max(TAMANHOS))
        destino.parent.mkdir(parents=True, exist_ok=True)
        imagem.save(destino, format="ICO", sizes=[(t, t) for t in TAMANHOS])
    except (ImportError, OSError, ValueError) as erro:
        # O `.ico` vem pronto no pacote, então desenhar de novo é melhoria, não necessidade:
        # sem Pillow, ou com a pasta só de leitura, o que já está em disco serve.
        log.info("Não redesenhei o ícone (%s); uso o que veio no pacote, se houver", erro)
        return destino if destino.exists() else None
    return destino


def _powershell(roteiro: str, ambiente: Mapping[str, str], espera: float = ESPERA) -> str:
    """Roda o roteiro e devolve a saída. O ambiente leva os caminhos, que nunca entram no texto."""
    linha = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",  # a política da máquina não vale para roteiro passado assim, mas deixo explícito
        "-Command",
        roteiro,
    ]
    try:
        feito = subprocess.run(  # noqa: S603 - linha fixa, nada do usuário vai para ela
            linha,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=espera,
            env={**os.environ, **ambiente},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise ErroAtalho("não achei o powershell.exe nesta máquina") from None
    except subprocess.TimeoutExpired:
        raise ErroAtalho(f"o Windows não respondeu em {espera:.0f} s") from None
    if feito.returncode != 0:
        motivo = (feito.stderr or feito.stdout or "").strip().splitlines()
        raise ErroAtalho(motivo[0][:300] if motivo else f"o powershell saiu com {feito.returncode}")
    return feito.stdout or ""


class Atalhos:
    """Os atalhos do Jarvis: criar, apagar e dizer onde estão."""

    def __init__(
        self,
        raiz: Path = RAIZ_PROJETO,
        comando: str | None = None,
        rodar: Callable[..., str] = _powershell,
        nome: str = NOME,
    ) -> None:
        self.raiz, self._rodar, self.nome = raiz, rodar, nome
        self.alvo = self.argumentos = ""
        if comando:
            self.alvo, _, resto = comando.partition('" "')
            self.alvo, self.argumentos = self.alvo.strip('"'), resto.strip('"')

    def _achar_o_python(self) -> None:
        """Só na hora de criar: apagar o atalho não depende de haver Python com as bibliotecas.

        O Python que tem as bibliotecas não é necessariamente o que digitou o comando — e o que vai
        no atalho é o `pythonw.exe` ao lado dele, para não ficar uma janela preta atrás do Jarvis.
        """
        if self.alvo:
            return
        self.alvo = str(sem_janela(python_do_jarvis(self.raiz)))
        self.argumentos = str(self.raiz / "iniciar.pyw")

    def _ambiente(self, icone: Path | None) -> dict[str, str]:
        return {
            "JV_NOME": self.nome,
            "JV_ALVO": self.alvo,
            "JV_ARGUMENTOS": f'"{self.argumentos}"' if " " in self.argumentos else self.argumentos,
            "JV_PASTA": str(self.raiz),
            "JV_EXPLICACAO": EXPLICACAO,
            "JV_ICONE": f"{icone},0" if icone else "",
        }

    def criar(self) -> list[Path]:
        """Cria os dois atalhos e devolve onde ficaram."""
        self._achar_o_python()
        icone = gerar_icone(self.raiz / ARQUIVO_DO_ICONE)
        saida = self._rodar(_ROTEIRO, self._ambiente(icone))
        feitos = [Path(l[len("CRIADO ") :].strip()) for l in saida.splitlines() if l.startswith("CRIADO ")]
        if not feitos:
            raise ErroAtalho("o Windows não devolveu nenhum atalho criado")
        return feitos

    def remover(self) -> list[Path]:
        """Apaga os atalhos que existirem e devolve quais eram."""
        saida = self._rodar(_ROTEIRO_REMOVER, {"JV_NOME": self.nome})
        return [Path(l[len("APAGADO ") :].strip()) for l in saida.splitlines() if l.startswith("APAGADO ")]


def ajustar_atalhos(criar: bool, sistema: str | None = None, atalhos: Atalhos | None = None) -> int:
    """--criar-atalho e --remover-atalho."""
    if (sistema or sys.platform) != "win32":
        print("[erro] O atalho com ícone só existe no Windows.", file=sys.stderr)
        return 2
    try:
        alvo = atalhos if atalhos is not None else Atalhos()
        feitos = alvo.criar() if criar else alvo.remover()
    except ErroAtalho as erro:
        print(f"[erro] Não consegui {'criar' if criar else 'apagar'} o atalho: {erro}", file=sys.stderr)
        return 1
    if not criar:
        if feitos:
            print("Pronto, apaguei o atalho de: " + ", ".join(str(c.parent) for c in feitos))
        else:
            print("Não havia atalho do Jarvis para apagar.")
        return 0
    for caminho in feitos:
        print(f"Atalho criado: {caminho}")
    print(
        "Agora é só dois cliques no Jarvis na Área de Trabalho. Para deixar na barra de tarefas: "
        "aperte a tecla Windows, escreva Jarvis, clique com o botão direito e escolha Fixar na barra de tarefas."
    )
    return 0
