"""Projetos do usuário: nome → pasta, situação pelo git e um diário de cada um.

O cadastro vem de JARVIS_PROJETOS, no .env: é o usuário que diz em quais pastas o Jarvis
pode programar. Cada projeto tem de ser a raiz de um repositório git, porque é o git que
isola o trabalho (uma cópia de trabalho em um ramo novo) e permite desfazer.

Os comandos git daqui são fixos, com argumentos em lista e sem shell. Nenhum deles publica
nada: não há push, pull nem fetch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.acoes import ErroAcao, chave

TEMPO_DO_GIT = 60  # segundos
PREFIXO_DOS_RAMOS = "jarvis/"
# Os commits do Jarvis saem com esta assinatura, para não se confundirem com os do usuário.
ASSINATURA = {
    "GIT_AUTHOR_NAME": "Jarvis",
    "GIT_AUTHOR_EMAIL": "jarvis@localhost",
    "GIT_COMMITTER_NAME": "Jarvis",
    "GIT_COMMITTER_EMAIL": "jarvis@localhost",
}
_SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0  # não pisca um console a cada comando


# O que o commit de fechamento não leva, mesmo que o projeto tenha esquecido de ignorar.
NAO_VAI_PARA_O_COMMIT = ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


class ErroProjeto(ErroAcao):
    """Pedido inválido ou falha do git; a mensagem volta para o modelo ou para o usuário."""


@dataclass(frozen=True)
class Projeto:
    nome: str
    pasta: Path

    @property
    def slug(self) -> str:
        return fatia(self.nome)


def fatia(texto: str, limite: int = 40) -> str:
    """'Corrigir login (v2)!' → 'corrigir-login-v2': serve de nome de pasta e de ramo."""
    return re.sub(r"[^a-z0-9]+", "-", chave(texto)).strip("-")[:limite].strip("-") or "trabalho"


def ler_projetos(bruto: str) -> dict[str, Projeto]:
    """JARVIS_PROJETOS: 'royale vision=C:\\dev\\royale-vision; dash=C:\\dev\\dash'. Chave: o nome sem acento."""
    projetos: dict[str, Projeto] = {}
    for parte in bruto.split(";"):
        nome, sinal, pasta = parte.partition("=")
        nome, pasta = " ".join(nome.split()), pasta.strip().strip('"').strip("'")
        if not sinal or not nome or not pasta:
            if parte.strip():
                raise ErroProjeto(f"JARVIS_PROJETOS: não entendi '{parte.strip()}'. O formato é nome=pasta, separados por ponto e vírgula.")
            continue
        caminho = Path(os.path.realpath(Path(pasta).expanduser()))
        if not caminho.is_dir():
            raise ErroProjeto(f"JARVIS_PROJETOS: a pasta do projeto '{nome}' não existe: {pasta}")
        if chave(nome) in projetos:
            raise ErroProjeto(f"JARVIS_PROJETOS: o projeto '{nome}' aparece duas vezes.")
        projetos[chave(nome)] = Projeto(nome, caminho)
    return projetos


def achar(projetos: dict[str, Projeto], nome: Any) -> Projeto:
    """O projeto com este nome. Aceita o nome sem acento e um pedaço dele, se só um combinar."""
    if not isinstance(nome, str) or not nome.strip():
        raise ErroProjeto("Diga o nome do projeto.")
    if not projetos:
        raise ErroProjeto("Nenhum projeto cadastrado. O usuário cadastra em JARVIS_PROJETOS, no arquivo .env (nome=pasta).")
    procurado = chave(nome)
    if procurado in projetos:
        return projetos[procurado]
    parecidos = [p for c, p in projetos.items() if procurado in c or c in procurado]
    if len(parecidos) == 1:
        return parecidos[0]
    nomes = ", ".join(p.nome for p in projetos.values())
    raise ErroProjeto(f"Não há projeto chamado '{nome}'. Cadastrados: {nomes}.")


# --- git ---------------------------------------------------------------------------------


def git(pasta: Path, *argumentos: str, tempo: float = TEMPO_DO_GIT, assinar: bool = False) -> str:
    """Roda `git` com estes argumentos nesta pasta e devolve a saída. Sem shell."""
    ambiente = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(ASSINATURA if assinar else {})}
    try:
        feito = subprocess.run(  # noqa: S603 - argumentos fixos, em lista
            ["git", "-c", "core.quotepath=off", *argumentos],
            cwd=pasta,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=tempo,
            env=ambiente,
            creationflags=_SEM_JANELA,
        )
    except FileNotFoundError:
        raise ErroProjeto("o git não está instalado ou não está no PATH") from None
    except subprocess.TimeoutExpired:
        raise ErroProjeto(f"o git demorou mais de {tempo:.0f} segundos em '{argumentos[0]}'") from None
    if feito.returncode != 0:
        mensagem = (feito.stderr or feito.stdout).strip().splitlines()
        raise ErroProjeto(mensagem[-1] if mensagem else f"git {argumentos[0]} falhou")
    return feito.stdout.strip()


def conferir_repositorio(projeto: Projeto) -> None:
    """O projeto tem de ser a raiz de um repositório git com ao menos um commit."""
    try:
        raiz = git(projeto.pasta, "rev-parse", "--show-toplevel")
    except ErroProjeto as erro:
        if "PATH" in str(erro):
            raise
        raise ErroProjeto(
            f"A pasta do projeto '{projeto.nome}' não é um repositório git. Rode `git init` e faça um commit nela: "
            "é o git que separa o trabalho do Jarvis do seu."
        ) from None
    if Path(os.path.realpath(raiz)) != projeto.pasta:
        raise ErroProjeto(f"O projeto '{projeto.nome}' aponta para uma subpasta do repositório. Cadastre a raiz: {raiz}")
    try:
        git(projeto.pasta, "rev-parse", "--verify", "HEAD")
    except ErroProjeto:
        raise ErroProjeto(f"O repositório do projeto '{projeto.nome}' ainda não tem nenhum commit.") from None


def ramos_do_jarvis(projeto: Projeto) -> list[str]:
    """Os ramos que os trabalhos deixaram no repositório, esperando a revisão do usuário."""
    ramos = git(projeto.pasta, "branch", "--list", f"{PREFIXO_DOS_RAMOS}*", "--format=%(refname:short)").splitlines()
    return [r.strip() for r in ramos if r.strip()]


def situacao(projeto: Projeto) -> dict[str, Any]:
    """Ramo atual, mudanças pendentes, últimos commits e ramos que o Jarvis deixou para revisar."""
    conferir_repositorio(projeto)
    pendentes = [linha for linha in git(projeto.pasta, "status", "--porcelain").splitlines() if linha.strip()]
    return {
        "ramo": git(projeto.pasta, "branch", "--show-current") or "(sem ramo: HEAD solto)",
        "pendentes": len(pendentes),
        "commits": git(projeto.pasta, "log", "-5", "--pretty=format:%h %s (%cr)").splitlines(),
        "ramos_do_jarvis": ramos_do_jarvis(projeto),
    }


def criar_copia(projeto: Projeto, destino: Path, ramo: str, partida: str | None = None) -> str:
    """Cria a cópia de trabalho em um ramo novo e devolve o commit de partida.

    Sem `partida`, começa do último commit do ramo em que o usuário está. Com ela, continua
    de um ramo que um trabalho anterior deixou (só ramos do Jarvis).
    """
    conferir_repositorio(projeto)
    if partida is not None and partida not in ramos_do_jarvis(projeto):
        raise ErroProjeto(f"Não há ramo do Jarvis chamado '{partida}' no projeto '{projeto.nome}'.")
    base = git(projeto.pasta, "rev-parse", "--verify", f"refs/heads/{partida}" if partida else "HEAD")
    destino.parent.mkdir(parents=True, exist_ok=True)
    git(projeto.pasta, "worktree", "add", "-b", ramo, str(destino), base, tempo=180)
    return base


def apagar_ramo(projeto: Projeto, ramo: str) -> None:
    """Apaga um ramo do Jarvis (o de um trabalho que não deixou commit nenhum, por exemplo)."""
    if not ramo.startswith(PREFIXO_DOS_RAMOS) or ramo not in ramos_do_jarvis(projeto):
        raise ErroProjeto(f"'{ramo}' não é um ramo do Jarvis neste projeto.")
    git(projeto.pasta, "branch", "-D", ramo)


def remover_copia(projeto: Projeto, destino: Path) -> bool:
    """Tira a cópia de trabalho do disco. O ramo, com os commits, fica no repositório."""
    try:
        git(projeto.pasta, "worktree", "remove", "--force", str(destino), tempo=180)
    except ErroProjeto:
        shutil.rmtree(destino, ignore_errors=True)  # arquivo preso por outro programa: tira o que der
        try:
            git(projeto.pasta, "worktree", "prune")
        except ErroProjeto:
            pass
    return not destino.exists()


def fechar_copia(copia: Path, mensagem: str) -> bool:
    """Guarda em um commit o que o trabalhador deixou sem commit. Devolve True se havia algo.

    Dependências instaladas e caches ficam de fora, estejam ou não no .gitignore do projeto.
    """
    if not git(copia, "status", "--porcelain").strip():
        return False
    fora = [f":(exclude,glob)**/{nome}/**" for nome in NAO_VAI_PARA_O_COMMIT]
    git(copia, "add", "-A", "--", ".", *fora)
    if not git(copia, "diff", "--cached", "--name-only").strip():
        return False
    git(copia, "commit", "--no-verify", "-m", mensagem[:200], assinar=True)
    return True


def resumo_do_ramo(copia: Path, base: str) -> dict[str, Any]:
    """O que o ramo tem a mais que o ponto de partida: commits e arquivos alterados."""
    commits = [c for c in git(copia, "log", "--pretty=format:%h %s", f"{base}..HEAD").splitlines() if c.strip()]
    arquivos = [a for a in git(copia, "diff", "--stat=100", f"{base}..HEAD").splitlines() if a.strip()]
    return {"commits": commits, "arquivos": arquivos[:-1][:40], "total": arquivos[-1].strip() if arquivos else ""}


# --- diário --------------------------------------------------------------------------------


class Diario:
    """Um arquivo de texto por projeto com o que cada trabalho fez e deixou pendente."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta

    def arquivo(self, projeto: Projeto) -> Path:
        return self.pasta / f"{projeto.slug}.md"

    def anotar(self, projeto: Projeto, titulo: str, texto: str, quando: datetime | None = None) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        quando = quando or datetime.now()
        with self.arquivo(projeto).open("a", encoding="utf-8") as saida:
            saida.write(f"## {quando:%d/%m/%Y %H:%M} · {titulo}\n\n{texto.strip()}\n\n")

    def recente(self, projeto: Projeto, limite: int = 3000) -> str:
        """O fim do diário (as anotações mais novas), cortado no começo de uma anotação."""
        try:
            texto = self.arquivo(projeto).read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(texto) <= limite:
            return texto.strip()
        corte = texto.find("\n## ", len(texto) - limite)
        return texto[corte:].strip() if corte >= 0 else texto[-limite:].strip()
