"""Skills de fora para os trabalhadores: baixar de um repositório, mostrar ao usuário e instalar com o ok dele.

Uma skill é uma pasta com um SKILL.md (instruções) e, às vezes, scripts. Quem as usa são os
trabalhadores de programação, dentro dos projetos. Por isso instalar uma é como instalar um
programa de terceiros: o Jarvis baixa, mostra o que veio e só instala se o usuário digitar
que sim (ver ferramentas.py). Nada daqui roda o que foi baixado.

As skills instaladas ficam em uma pasta no formato de plugin local do Claude Code:

    <pasta>/.claude-plugin/plugin.json
    <pasta>/skills/<nome>/SKILL.md
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jarvis.acoes import ErroAcao
from jarvis.projetos import fatia

NOME_DO_PLUGIN = "jarvis"
HOSPEDEIROS = ("github.com", "gitlab.com", "bitbucket.org")
EXECUTAVEIS = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".bat", ".cmd", ".exe", ".rb", ".pl", ".php")
MAX_ARQUIVOS = 300
MAX_BYTES = 8 * 1024 * 1024
MAX_SKILLS_NO_REPOSITORIO = 300
TEMPO_DO_CLONE = 120  # segundos
NOME_VALIDO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_PEDACO = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_REGISTRO = ".origem.json"
_SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0


class ErroSkill(ErroAcao):
    """Pedido inválido ou falha ao baixar; a mensagem volta para o modelo ou para o usuário."""


def limpo(texto: Any, limite: int = 300) -> str:
    """Texto de terceiros em uma linha só, sem caracteres de controle (ele vai aparecer no terminal)."""
    visivel = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(texto))
    visivel = " ".join(visivel.split())
    return visivel if len(visivel) <= limite else visivel[: limite - 1] + "…"


@dataclass(frozen=True)
class Origem:
    endereco: str  # o que o git clona
    ramo: str | None
    subpasta: str  # "" ou "skills/pdf"

    @property
    def texto(self) -> str:
        return self.endereco.removesuffix(".git") + (f" (pasta {self.subpasta})" if self.subpasta else "")


def ler_origem(bruto: Any) -> Origem:
    """Aceita https://github.com/dono/repo, com ou sem /tree/<ramo>/<pasta>, e o atalho dono/repo."""
    if not isinstance(bruto, str) or not bruto.strip():
        raise ErroSkill("Diga de onde vem a skill: o endereço do repositório, por exemplo https://github.com/dono/repositorio.")
    texto = bruto.strip()
    if re.fullmatch(rf"{_PEDACO}/{_PEDACO}", texto):
        texto = f"https://github.com/{texto}"
    partes = urlsplit(texto)
    hospedeiro = (partes.hostname or "").lower().removeprefix("www.")
    if partes.scheme != "https" or hospedeiro not in HOSPEDEIROS or partes.username or partes.password or partes.port:
        raise ErroSkill(f"Só instalo skills de repositórios https em {', '.join(HOSPEDEIROS)}. Recebi: {limpo(texto, 120)}")
    caminho = [p for p in partes.path.split("/") if p]
    if caminho[2:3] == ["-"]:  # gitlab.com/dono/repo/-/tree/ramo/pasta
        del caminho[2]
    if len(caminho) < 2 or partes.query or partes.fragment or not all(re.fullmatch(_PEDACO, p) for p in caminho):
        raise ErroSkill(f"Não entendi o endereço do repositório: {limpo(texto, 120)}")
    dono, repositorio, resto = caminho[0], caminho[1].removesuffix(".git"), caminho[2:]
    ramo = None
    if resto:
        if resto[0] not in ("tree", "src", "blob") or len(resto) < 2:
            raise ErroSkill(f"Não entendi o endereço do repositório: {limpo(texto, 120)}")
        ramo, resto = resto[1], resto[2:]
        if resto and resto[-1] == "SKILL.md":
            resto = resto[:-1]
    if any(p in (".", "..") for p in resto) or not repositorio:
        raise ErroSkill(f"Não entendi o endereço do repositório: {limpo(texto, 120)}")
    return Origem(f"https://{hospedeiro}/{dono}/{repositorio}.git", ramo, "/".join(resto))


def clonar(origem: Origem, destino: Path) -> str:
    """Baixa só a ponta do repositório, sem senha, sem atalhos simbólicos e sem filtros. Devolve o commit."""
    comando = [
        "git", "-c", "credential.helper=", "-c", "core.symlinks=false", "-c", "core.hooksPath=", "-c", "protocol.file.allow=never",
        "clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags",
        *(["--branch", origem.ramo] if origem.ramo else []),
        "--", origem.endereco, str(destino),
    ]  # fmt: skip
    ambiente = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_ASKPASS": ""}
    try:
        feito = subprocess.run(  # noqa: S603 - argumentos em lista, sem shell
            comando, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TEMPO_DO_CLONE, env=ambiente,
            creationflags=_SEM_JANELA, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except FileNotFoundError:
        raise ErroSkill("o git não está instalado ou não está no PATH") from None
    except subprocess.TimeoutExpired:
        raise ErroSkill(f"o repositório demorou mais de {TEMPO_DO_CLONE} segundos para baixar") from None
    if feito.returncode != 0:
        linhas = (feito.stderr or feito.stdout).strip().splitlines()
        motivo = limpo(linhas[-1] if linhas else "o git falhou", 200)
        if re.search(r"could not read Username|Authentication failed|not found|terminal prompts disabled", motivo):
            motivo = "o repositório não existe ou é privado (só baixo repositórios públicos)"
        raise ErroSkill(f"Não consegui baixar {origem.texto}: {motivo}")
    ponta = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"], cwd=destino, capture_output=True, text=True, timeout=30, creationflags=_SEM_JANELA
    )
    return ponta.stdout.strip()[:12] or "desconhecido"


def ler_cabecalho(arquivo: Path) -> dict[str, str]:
    """`name` e `description` do começo de um SKILL.md (o trecho entre as linhas ---)."""
    try:
        linhas = arquivo.read_text(encoding="utf-8", errors="replace")[:20_000].splitlines()
    except OSError:
        return {}
    if not linhas or linhas[0].strip() != "---":
        return {}
    campos: dict[str, str] = {}
    atual = None
    for linha in linhas[1:]:
        if linha.strip() == "---":
            break
        achado = re.match(r"^([A-Za-z_-]+):\s*(.*)$", linha)
        if achado:
            atual = achado.group(1).lower()
            valor = achado.group(2).strip()
            campos[atual] = "" if valor in (">", "|", ">-", "|-") else valor.strip("\"'")
        elif atual and linha.startswith((" ", "\t")):  # continuação de um valor em várias linhas
            campos[atual] = (campos[atual] + " " + linha.strip()).strip()
    return campos


@dataclass
class Achada:
    nome: str  # nome da pasta em que será instalada
    descricao: str
    pasta: Path
    arquivos: list[str]  # caminhos relativos, com /
    tamanho: int

    @property
    def scripts(self) -> list[str]:
        return [a for a in self.arquivos if a.lower().endswith(EXECUTAVEIS)]


def _examinar(pasta: Path, nome_reserva: str) -> Achada:
    cabecalho = ler_cabecalho(pasta / "SKILL.md")
    nome = fatia(cabecalho.get("name") or nome_reserva, 48)
    arquivos, tamanho = [], 0
    for raiz, pastas, nomes in os.walk(pasta):
        pastas[:] = [p for p in pastas if p != ".git" and not (Path(raiz) / p).is_symlink()]
        for arquivo in nomes:
            caminho = Path(raiz) / arquivo
            if caminho.is_symlink() or not caminho.is_file():
                raise ErroSkill(f"A skill '{nome}' traz um atalho simbólico ({limpo(arquivo, 60)}); não instalo.")
            arquivos.append(caminho.relative_to(pasta).as_posix())
            tamanho += caminho.stat().st_size
            if len(arquivos) > MAX_ARQUIVOS or tamanho > MAX_BYTES:
                raise ErroSkill(f"A skill '{nome}' é grande demais (mais de {MAX_ARQUIVOS} arquivos ou de {MAX_BYTES // 1024 // 1024} MB).")
    return Achada(nome, limpo(cabecalho.get("description", ""), 300), pasta, sorted(arquivos), tamanho)


def procurar(raiz: Path, subpasta: str = "") -> list[Path]:
    """As pastas que têm um SKILL.md, a partir de `subpasta` do repositório baixado."""
    base = Path(os.path.realpath(raiz / subpasta))
    raiz = Path(os.path.realpath(raiz))
    if not (base == raiz or base.is_relative_to(raiz)) or not base.is_dir():
        raise ErroSkill(f"O repositório não tem a pasta '{limpo(subpasta, 100)}'.")
    achadas: list[Path] = []
    for atual, pastas, nomes in os.walk(base):
        pastas[:] = sorted(p for p in pastas if p not in (".git", "node_modules") and not (Path(atual) / p).is_symlink())
        if "SKILL.md" in nomes:
            achadas.append(Path(atual))
            pastas[:] = []  # o que está dentro de uma skill é dela
            if len(achadas) > MAX_SKILLS_NO_REPOSITORIO:
                break
    return achadas


@dataclass
class Proposta:
    """Uma skill baixada, esperando o sim do usuário. `temporaria` é apagada no fim, instale ou não."""

    origem: Origem
    commit: str
    skill: Achada
    temporaria: Path
    substitui: bool = False

    def linhas(self) -> list[str]:
        scripts = self.skill.scripts
        amostra = ", ".join(limpo(s, 60) for s in scripts[:6]) + (f" e mais {len(scripts) - 6}" if len(scripts) > 6 else "")
        return [
            f"Skill: {self.skill.nome}" + (" (substitui a instalada com este nome)" if self.substitui else ""),
            f"O que ela diz que faz: {self.skill.descricao or '(sem descrição)'}",
            f"Origem: {self.origem.texto}, commit {self.commit}",
            f"Arquivos: {len(self.skill.arquivos)} ({max(1, self.skill.tamanho // 1024)} KB)",
            f"Scripts que os trabalhadores poderão rodar: {amostra}" if scripts else "Scripts: nenhum, só instruções.",
            f"Para ler antes de aceitar: {self.skill.pasta / 'SKILL.md'}",
            "É conteúdo de terceiros: os trabalhadores vão seguir estas instruções dentro dos seus projetos.",
        ]


@dataclass(frozen=True)
class Instalada:
    nome: str
    descricao: str
    origem: str
    commit: str
    quando: str
    scripts: int


class Skills:
    def __init__(self, pasta: Path, clonar: Callable[[Origem, Path], str] = clonar) -> None:
        self.pasta = pasta  # raiz do plugin local
        self._clonar = clonar

    @property
    def _skills(self) -> Path:
        return self.pasta / "skills"

    def instaladas(self) -> list[Instalada]:
        lista = []
        if not self._skills.is_dir():
            return lista
        for pasta in sorted(self._skills.iterdir()):
            if not (pasta / "SKILL.md").is_file() or not NOME_VALIDO.fullmatch(pasta.name):
                continue
            try:
                registro = json.loads((pasta / _REGISTRO).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                registro = {}
            scripts = sum(1 for a in pasta.rglob("*") if a.is_file() and a.name.lower().endswith(EXECUTAVEIS))
            lista.append(
                Instalada(
                    pasta.name,
                    limpo(ler_cabecalho(pasta / "SKILL.md").get("description", ""), 300),
                    str(registro.get("origem", "desconhecida")),
                    str(registro.get("commit", "")),
                    str(registro.get("quando", "")),
                    scripts,
                )
            )
        return lista

    def nomes(self) -> list[str]:
        return [s.nome for s in self.instaladas()]

    def preparar(self, origem_bruta: Any, nome: Any = None) -> Proposta:
        """Baixa o repositório e escolhe a skill. Não instala nada; quem decide é o usuário."""
        origem = ler_origem(origem_bruta)
        temporaria = Path(tempfile.mkdtemp(prefix="jarvis-skill-"))
        try:
            commit = self._clonar(origem, temporaria / "repositorio")
            pastas = procurar(temporaria / "repositorio", origem.subpasta)
            if not pastas:
                raise ErroSkill(f"Não achei nenhuma skill (arquivo SKILL.md) em {origem.texto}.")
            reserva = origem.endereco.removesuffix(".git").rsplit("/", 1)[-1]
            raiz = Path(os.path.realpath(temporaria / "repositorio"))
            candidatas = {}
            for pasta in pastas:
                cabecalho = ler_cabecalho(pasta / "SKILL.md")
                candidatas[fatia(cabecalho.get("name") or (reserva if pasta == raiz else pasta.name), 48)] = pasta
            if isinstance(nome, str) and nome.strip():
                escolhida = candidatas.get(fatia(nome, 48))
                if escolhida is None:
                    raise ErroSkill(f"O repositório não tem a skill '{limpo(nome, 60)}'. Tem: {', '.join(sorted(candidatas)[:40])}.")
            elif len(candidatas) == 1:
                escolhida = next(iter(candidatas.values()))
            else:
                raise ErroSkill(
                    f"O repositório tem {len(candidatas)} skills. Pergunte ao usuário qual ele quer e chame de novo com 'skill'. "
                    f"Nomes: {', '.join(sorted(candidatas)[:60])}."
                )
            skill = _examinar(escolhida, reserva if escolhida == raiz else escolhida.name)
            if not NOME_VALIDO.fullmatch(skill.nome):
                raise ErroSkill(f"A skill tem um nome que não serve de nome de pasta: {limpo(skill.nome, 60)}")
            return Proposta(origem, commit, skill, temporaria, substitui=(self._skills / skill.nome).exists())
        except BaseException:
            shutil.rmtree(temporaria, ignore_errors=True)
            raise

    def instalar(self, proposta: Proposta) -> str:
        destino = self._skills / proposta.skill.nome
        (self.pasta / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        manifesto = self.pasta / ".claude-plugin" / "plugin.json"
        if not manifesto.exists():
            dados = {"name": NOME_DO_PLUGIN, "description": "Skills que o usuário instalou no Jarvis", "version": "1.0.0"}
            manifesto.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(proposta.skill.pasta, destino, symlinks=False, ignore=shutil.ignore_patterns(".git"))
        registro = {"origem": proposta.origem.texto, "commit": proposta.commit, "quando": datetime.now().isoformat(timespec="seconds")}
        (destino / _REGISTRO).write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"Skill '{proposta.skill.nome}' instalada. Os próximos trabalhos já podem usá-la."

    def descartar(self, proposta: Proposta) -> None:
        shutil.rmtree(proposta.temporaria, ignore_errors=True)

    def conferir(self, nome: Any) -> str:
        """O nome de pasta de uma skill instalada, a partir do nome que o usuário disse."""
        if not isinstance(nome, str) or not nome.strip():
            raise ErroSkill("Diga o nome da skill.")
        alvo = fatia(nome, 48)
        if alvo not in self.nomes():
            instaladas = ", ".join(self.nomes()) or "nenhuma"
            raise ErroSkill(f"Não há skill instalada chamada '{limpo(nome, 60)}'. Instaladas: {instaladas}.")
        return alvo

    def remover(self, nome: Any) -> str:
        alvo = self.conferir(nome)
        shutil.rmtree(self._skills / alvo)
        return f"Skill '{alvo}' removida."


def skills_do_projeto(copia: Path) -> list[str]:
    """As skills que o próprio projeto traz em .claude/skills (nome da pasta de cada uma)."""
    pasta = copia / ".claude" / "skills"
    if not pasta.is_dir():
        return []
    return sorted(p.name for p in pasta.iterdir() if (p / "SKILL.md").is_file() and NOME_VALIDO.fullmatch(p.name))
