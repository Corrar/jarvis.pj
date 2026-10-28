"""A guarda do trabalhador: o que o agente de programação pode rodar e onde pode mexer.

O trabalhador roda em uma cópia de trabalho do projeto. Esta guarda confere cada chamada
antes de ela acontecer:

- arquivos (ler, escrever, editar, procurar): só dentro da cópia de trabalho;
- comandos: só os do projeto. Instalar dependências no projeto, rodar testes, build e lint,
  git local e utilitários de arquivo. Nada global, nada que publique, nenhum outro shell,
  nenhum caminho fora da cópia.

O que isto é e o que não é: um filtro de comandos, não uma caixa fechada. Quem pode rodar os
testes do projeto roda o código que está no projeto, com os privilégios do usuário. O filtro
mantém o trabalhador nos trilhos (nada global, nada publicado, nada fora da pasta por engano)
e recusa o que não conhece. O que contém o resultado é a cópia de trabalho em ramo próprio e
a revisão do usuário antes de juntar o ramo ao código dele.

Também fica de fora da conferência: opção que manda um utilitário seguir atalhos simbólicos
ao descer pastas (cp -L, grep -R, diff -r). Os argumentos e os padrões (*.txt) são conferidos
um a um, atalhos inclusive; o que o programa encontra sozinho lá dentro, não.
"""

from __future__ import annotations

import glob
import ntpath
import os
import posixpath
import re
import sys
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from jarvis.guarda import _padrao_escapa

MAX_COMANDO = 2000

# Variáveis que podem vir na frente do comando (CI=1 npm test).
VARIAVEIS_LIBERADAS = frozenset({"CI", "NODE_ENV", "FORCE_COLOR", "NO_COLOR", "TZ", "LANG", "LC_ALL", "PORT", "DEBUG", "PYTHONPATH"})

# Só leem. Destes, os de LEEM_SKILLS também alcançam a pasta das skills instaladas.
LEITORES = frozenset({
    "ls", "dir", "cat", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "grep", "egrep", "fgrep", "rg", "tree", "stat",
    "file", "du", "diff", "cmp", "jq", "find", "which", "type", "basename", "dirname", "realpath", "date", "sleep", "pwd",
    "test", "[", "true", "false", "echo", "printf",
})  # fmt: skip
LEEM_SKILLS = frozenset({"cat", "head", "tail", "ls", "wc"})
ESCRITORES = frozenset({"mkdir", "rmdir", "touch", "cp", "mv", "rm", "tee", "chmod"})
SO_TEXTO = frozenset({"echo", "printf"})  # os argumentos são texto, nunca caminho
# Não têm opção que grave em arquivo. Os outros programas (sort -o, git --output, pytest --junitxml...)
# podem gravar no caminho que recebem: para eles, .git e .claude/settings não entram nem como argumento.
SEM_SAIDA_EM_ARQUIVO = frozenset({
    "ls", "dir", "cat", "head", "tail", "wc", "cut", "tr", "grep", "egrep", "fgrep", "rg", "stat", "file", "du", "diff",
    "cmp", "jq", "find", "which", "type", "basename", "dirname", "realpath", "test", "[",
})  # fmt: skip

# Ferramentas de projeto: testes, lint, build e gerenciadores que instalam dentro do projeto.
FERRAMENTAS = frozenset({
    "pytest", "ruff", "black", "isort", "mypy", "flake8", "pylint", "coverage", "tox", "uv", "poetry", "pipenv", "pdm",
    "eslint", "prettier", "tsc", "vitest", "jest", "mocha", "vite", "webpack", "rollup", "esbuild", "turbo", "nx",
    "cargo", "rustc", "go", "gofmt", "dotnet", "mvn", "gradle", "gradlew", "make", "cmake", "composer", "php",
    "phpunit", "bundle", "rake", "ruby", "deno",
})  # fmt: skip

# O que nunca roda, com o motivo que volta para o trabalhador.
_MOTIVOS = {
    "outro shell ou comando embrulhado: rode o comando direto": (
        "sh", "bash", "zsh", "fish", "dash", "cmd", "powershell", "pwsh", "eval", "exec", "source", ".", "xargs", "env",
        "nohup", "timeout", "watch", "time", "sudo", "su", "runas", "start", "open", "xdg-open", "explorer", "script",
        "command", "builtin", "nice", "stdbuf", "winpty",
    ),
    "sem rede fora do gerenciador de pacotes": ("curl", "wget", "ssh", "scp", "sftp", "ftp", "nc", "ncat", "telnet", "rsync"),
    "publicar e falar com o GitHub é com o usuário": ("gh", "hub", "glab"),
    "instalação no sistema não é comando de projeto": (
        "winget", "choco", "scoop", "apt", "apt-get", "brew", "yum", "dnf", "pacman", "snap", "msiexec", "pipx", "uvx", "bunx",
    ),
    "mexe no sistema, não no projeto": (
        "reg", "regedit", "sc", "net", "netsh", "taskkill", "kill", "pkill", "killall", "shutdown", "reboot", "chown", "ln",
        "mklink", "dd", "mkfs", "format", "del", "rd", "diskpart", "schtasks", "crontab", "systemctl", "service", "wsl",
        "setx", "icacls", "attrib",
    ),
    "contêineres ficam fora do escopo do trabalho": ("docker", "podman", "kubectl", "helm"),
    "para ler e alterar arquivos use as ferramentas de leitura e de edição": (
        "sed", "awk", "gawk", "perl", "patch", "tar", "zip", "unzip", "vi", "vim", "nano",
    ),
}  # fmt: skip
NEGADOS = {programa: motivo for motivo, programas in _MOTIVOS.items() for programa in programas}

GIT_LIBERADOS = frozenset({
    "status", "diff", "log", "show", "add", "commit", "restore", "rm", "mv", "ls-files", "grep", "blame", "rev-parse",
    "merge-base", "describe", "shortlog", "reset", "clean", "revert", "apply", "branch", "ls-tree", "cat-file", "rev-list",
    "show-ref", "for-each-ref", "check-ignore", "diff-tree", "name-rev",
})  # fmt: skip
_GIT_DO_USUARIO = ("push", "pull", "fetch", "clone", "remote", "submodule")
_GIT_DE_RAMO = ("checkout", "switch", "worktree", "merge", "rebase", "cherry-pick", "stash", "tag")
_RAMO_SO_LISTA = ("--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at")
_RAMO_MUDA = (
    "-d", "-D", "-m", "-M", "-c", "-C", "-f", "-u", "--delete", "--move", "--copy", "--force", "--set-upstream",
    "--unset-upstream", "--edit-description",
)  # fmt: skip

NPM_LIBERADOS = frozenset({
    "install", "i", "ci", "add", "uninstall", "remove", "rm", "un", "update", "up", "run", "run-script", "test", "t",
    "ls", "list", "outdated", "audit", "dedupe", "prune", "rebuild", "exec", "x", "pkg", "why", "explain", "view", "info",
})  # fmt: skip
# pnpm, yarn e bun: subcomandos que instalam, publicam ou configuram fora do projeto.
_FORA_DO_PROJETO = frozenset({
    "global", "dlx", "create", "publish", "login", "logout", "adduser", "link", "unlink", "config", "upgrade-interactive",
    "self-update", "setup", "env", "npm", "upgrade", "init", "store", "token", "owner", "node",
})  # fmt: skip
_COM_VALOR = ("--prefix", "-C", "--dir", "--cwd", "--filter", "-w", "--workspace", "-F")  # a palavra seguinte não é o subcomando
SERVIDORES = frozenset({"dev", "start", "serve", "watch", "preview", "storybook"})  # ficam rodando até alguém parar
_FICA_RODANDO = ("--watch", "--watchAll", "--serve")
_INSTALA_FORA = ("-g", "--global", "--system", "--user", "--break-system-packages")
_SUBCOMANDOS_NEGADOS = {
    "cargo": ("install", "uninstall", "login", "logout", "publish", "owner", "yank"),
    "go": ("install",),
    "dotnet": ("tool", "workload", "nuget"),
    "uv": ("tool", "python", "self", "publish", "cache"),
    "poetry": ("self", "config", "publish"),
    "pdm": ("self", "config", "publish"),
    "composer": ("global", "self-update", "config"),
    "deno": ("install", "uninstall", "upgrade", "publish", "eval", "repl", "compile"),
    "mvn": ("deploy", "release:perform"),
    "gradle": ("publish",),
    "gradlew": ("publish",),
    "bundle": ("config",),
}
# Rodam outro comando dentro do ambiente do projeto: o comando de dentro é conferido também.
_EMBRULHAM = {("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("bundle", "exec")}
_SEM_ARQUIVO = ("--version", "-V", "-VV", "-v", "--help", "-h")

_GLOB = "*?["
MAX_DO_PADRAO = 2000  # arquivos que um padrão (*.py) pode alcançar e ainda ser conferido um a um
_OPERADORES = ("&>>", "&>", "&&", "||", "|&", ";;", "<<", "<&", "<>", ">>", ">&", ">|", ";", "|", "<", ">", "&")  # os longos antes


class _Recusa(Exception):
    """Interrompe a análise com o motivo da recusa."""


def _separar(comando: str, sistema: str = "") -> list[tuple[str, str]]:
    """Divide o comando como o bash faria: ('p', palavra) e ('o', operador).

    Só aceita o que a análise sabe acompanhar. Variáveis, substituição de comando, subshell,
    here-doc, chaves e segundo plano são recusados aqui, porque mudariam o comando depois de
    conferido. Dentro de aspas simples tudo é literal e pode.
    """
    saida: list[tuple[str, str]] = []
    palavra: list[str] | None = None
    literal = False  # a palavra tem trecho entre aspas ou escapado
    i, fim = 0, len(comando)

    def fechar() -> None:
        nonlocal palavra, literal
        if palavra is not None:
            saida.append(("p", "".join(palavra)))
        palavra, literal = None, False

    while i < fim:
        c = comando[i]
        if c in " \t":
            fechar()
            i += 1
        elif c in "\r\n":
            fechar()
            saida.append(("o", ";"))
            i += 1
        elif c == "\\":
            if i + 1 >= fim:
                raise _Recusa("barra invertida solta no fim")
            if comando[i + 1] != "\n":  # barra + quebra de linha é só continuação
                if sistema == "win32":
                    # No bash a barra invertida some; em outro shell do Windows ela seria um caminho. Sem ambiguidade.
                    raise _Recusa("no Windows, escreva os caminhos com / e não use barra invertida fora de aspas")
                palavra = [*(palavra or []), comando[i + 1]]
                literal = True
            i += 2
        elif c == "'":
            fecha = comando.find("'", i + 1)
            if fecha < 0:
                raise _Recusa("aspas sem fechar")
            palavra = [*(palavra or []), comando[i + 1 : fecha]]
            literal = True
            i = fecha + 1
        elif c == '"':
            i += 1
            trecho: list[str] = []
            while True:
                if i >= fim:
                    raise _Recusa("aspas sem fechar")
                d = comando[i]
                if d == '"':
                    break
                if d == "\\" and i + 1 < fim and comando[i + 1] in '"\\$`\n':
                    if comando[i + 1] != "\n":
                        trecho.append(comando[i + 1])
                    i += 2
                    continue
                if d in "$`":
                    raise _Recusa("sem variáveis nem substituição de comando; para um $ ou uma crase literal use aspas simples")
                trecho.append(d)
                i += 1
            palavra = [*(palavra or []), "".join(trecho)]
            literal = True
            i += 1
        elif c in "$`":
            raise _Recusa("sem variáveis nem substituição de comando ($ e crase): escreva os valores por extenso")
        elif c in "()":
            raise _Recusa("sem parênteses: nada de subshell nem de substituição de processo")
        elif c in "{}":
            raise _Recusa("sem chaves fora de aspas: escreva cada caminho por extenso ou ponha o texto entre aspas simples")
        elif c == "#" and palavra is None:
            raise _Recusa("sem comentário (#) dentro do comando")
        elif c in ";&|<>":
            if c in "<>" and palavra is not None and not literal and "".join(palavra).isdigit():
                palavra = None  # o número de 2>arquivo é o descritor, não um argumento
            fechar()
            operador = next(o for o in _OPERADORES if comando.startswith(o, i))
            i += len(operador)
            if operador == "<<":
                raise _Recusa("sem here-doc: para criar arquivo use a ferramenta de escrita; mensagem de commit vai com -m")
            if operador == ";;":
                raise _Recusa("construção de shell fora do aceito (;;)")
            if operador == "&":
                raise _Recusa("nada em segundo plano (&)")
            saida.append(("o", "|" if operador == "|&" else operador))
        else:
            palavra = [*(palavra or []), c]
            i += 1
    fechar()
    return saida


def _fora(caminho: str, raizes: Sequence[Path], sistema: str) -> bool:
    modulo = ntpath if sistema == "win32" else posixpath
    alvo = modulo.normcase(caminho)
    for raiz in raizes:
        base = modulo.normcase(str(raiz))
        if alvo == base or alvo.startswith(base.rstrip("\\/") + modulo.sep):
            return False
    return True


def _resolver(token: str, onde: str, sistema: str) -> str:
    """O caminho completo e sem '..' que este pedaço do comando indica, a partir da pasta `onde`."""
    if sistema == "win32":
        token = token.replace("/", "\\")
        achado = re.match(r"^\\(?:cygdrive\\)?([a-zA-Z])(\\.*)?$", token)  # /c/Users/... do Git Bash → C:\Users\...
        if achado:
            token = f"{achado.group(1).upper()}:{achado.group(2) or chr(92)}"
        return ntpath.normpath(token if ntpath.isabs(token) or re.match(r"^[a-zA-Z]:", token) else ntpath.join(onde, token))
    return posixpath.normpath(token if token.startswith("/") else posixpath.join(onde, token))


def _parece_caminho_de_risco(token: str) -> bool:
    """Absoluto, da pasta do usuário ou com '..': é o tipo de caminho que pode sair da cópia de trabalho."""
    if token.startswith(("/", "\\", "~")) or re.match(r"^[a-zA-Z]:", token):
        return True
    return ".." in re.split(r"[\\/]", token)


def _itens_de_lista(texto: str, sistema: str) -> list[str]:
    """Os itens de um argumento que pode ser uma lista de caminhos: 'src:/tmp/lib' → src e /tmp/lib."""
    if sistema != "win32":
        return re.split(r"[,:]", texto)
    itens: list[str] = []
    for trecho in re.split(r"[,;]", texto):
        partes = trecho.split(":")  # o Git Bash converte listas com ':' antes de entregar ao programa
        i = 0
        while i < len(partes):
            if len(partes[i]) == 1 and partes[i].isalpha() and i + 1 < len(partes) and partes[i + 1].startswith(("/", "\\")):
                itens.append(f"{partes[i]}:{partes[i + 1]}")  # a letra da unidade: C:/pasta
                i += 2
            else:
                itens.append(partes[i])
                i += 1
    return itens


def e_do_git(parte: str) -> bool:
    """'.git' e os outros nomes que o Windows resolve para a mesma pasta."""
    nome = parte.lower().rstrip(" .")
    return nome == ".git" or nome.startswith(("git~", ".git:"))


def reservado(partes: Sequence[str]) -> str | None:
    """Motivo para não escrever neste caminho (dado em partes, a partir da raiz da cópia), se houver."""
    if any(e_do_git(parte) for parte in partes):
        return "a pasta .git não é mexida à mão; use os comandos do git"
    nomes = [parte.lower().rstrip(" .") for parte in partes]
    if len(nomes) >= 2 and nomes[-2] == ".claude" and nomes[-1].startswith("settings"):
        return "as configurações do Claude Code do projeto (.claude/settings) quem muda é o usuário"
    return None


def _nome_do_programa(bruto: str) -> str:
    return re.sub(r"\.(exe|cmd|bat)$", "", re.split(r"[\\/]", bruto)[-1].lower())


class _Analise:
    def __init__(self, copia: Path, leitura: Sequence[Path], extras: Collection[str], sistema: str) -> None:
        self.copia, self.sistema = copia, sistema
        self.leitura = [copia, *leitura]
        self.extras = {e.lower() for e in extras}
        self.onde = str(copia)  # a pasta de onde o comando em análise roda (ver avaliar_comando)
        self.destino: str | None = None  # para onde o `cd` em análise iria, se desse certo

    # --- caminhos -----------------------------------------------------------------------

    def caminho(self, token: str, escrever: bool = False, com_skills: bool = False) -> str | None:
        """Motivo da recusa se este caminho sai da cópia de trabalho; None se pode."""
        if token in ("/dev/null", "NUL", "nul"):
            return None
        fora = f"caminho fora da cópia de trabalho: {token}"
        if token.startswith("~"):
            return fora
        completo = _resolver(token, self.onde, self.sistema)
        raizes = self.leitura if com_skills and not escrever else [self.copia]
        if _fora(completo, raizes, self.sistema):
            return fora
        if self.sistema == sys.platform and _fora(os.path.realpath(completo), raizes, self.sistema):
            return fora  # um atalho de dentro da cópia que aponta para fora
        if self.sistema == sys.platform and any(c in token for c in _GLOB):
            # O shell troca o padrão pelos arquivos que existem; um deles pode ser um atalho para fora.
            relativo = not token.startswith(("/", "\\")) and not re.match(r"^[a-zA-Z]:", token)
            achados = glob.glob(os.path.join(glob.escape(self.onde), token) if relativo else completo)
            if len(achados) > MAX_DO_PADRAO:
                return f"o padrão alcança arquivos demais para conferir: {token}"
            if any(_fora(os.path.realpath(achado), raizes, self.sistema) for achado in achados):
                return fora
        if escrever:
            modulo = ntpath if self.sistema == "win32" else posixpath
            return reservado(re.split(r"[\\/]", modulo.relpath(completo, str(self.copia))))
        return None

    def e_a_raiz(self, token: str) -> bool:
        modulo = ntpath if self.sistema == "win32" else posixpath
        return modulo.normcase(_resolver(token, self.onde, self.sistema)) == modulo.normcase(str(self.copia))

    def e_reservado(self, token: str) -> str | None:
        """Motivo da recusa se o token dá em um caminho da cópia que ninguém grava (.git, .claude/settings)."""
        if not token or token.startswith(("-", "~")):
            return None
        completo = _resolver(token, self.onde, self.sistema)
        if _fora(completo, [self.copia], self.sistema):
            return None
        modulo = ntpath if self.sistema == "win32" else posixpath
        return reservado(re.split(r"[\\/]", modulo.relpath(completo, str(self.copia))))

    def argumentos(self, args: Sequence[str], com_skills: bool = False, a_fundo: bool = False, pode_gravar: bool = True) -> str | None:
        """Confere todo pedaço de argumento que possa ser um caminho para fora da cópia.

        `a_fundo` é para os utilitários de arquivo, que aceitam opções grudadas (-rf/caminho,
        -o-f/caminho): ali vale qualquer trecho da opção que comece como um caminho de risco.
        `pode_gravar` vale para todo programa que tem alguma opção de saída em arquivo.
        """
        for arg in args:
            if "://" in arg:
                return "endereço de rede no comando: instale pelo nome do pacote"
            for parte in re.split(r"[\\/]", arg):
                if parte.startswith(".") and any(c in parte for c in _GLOB):
                    return f"padrão que alcança arquivos ocultos, como a pasta .git: {arg}"
            candidatos = [] if arg.startswith("-") else [arg]
            candidatos += arg.split("=")[1:]  # --saida=caminho
            if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
                candidatos.append(arg[2:])  # -o/caminho, -C../pasta
                if a_fundo:
                    candidatos += [arg[i:] for i in range(3, len(arg)) if arg.startswith(("/", "\\", "~", ".."), i)]
            for candidato in candidatos:
                for pedaco in {candidato, *_itens_de_lista(candidato, self.sistema)}:
                    # Nos utilitários de arquivo, todo argumento é conferido: um nome simples pode ser um atalho para fora.
                    if pedaco and (_parece_caminho_de_risco(pedaco) or a_fundo):
                        motivo = self.caminho(pedaco, com_skills=com_skills)
                        if motivo:
                            return motivo
                    if pode_gravar and (motivo := self.e_reservado(pedaco)):
                        return motivo
        return None

    # --- regras por programa ------------------------------------------------------------

    def git(self, args: Sequence[str]) -> str | None:
        while args and args[0] in ("--no-pager", "-P", "--no-optional-locks"):
            args = args[1:]
        if not args or args[0] in ("--version", "--help", "version", "help"):
            return None
        if args[0].startswith("-"):
            return "git: opções antes do subcomando (-C, -c, --git-dir) não são aceitas"
        sub, resto = args[0], args[1:]
        if sub in _GIT_DO_USUARIO:
            return f"git {sub}: o trabalho fica no ramo local; quem publica e sincroniza é o usuário"
        if sub in _GIT_DE_RAMO:
            return f"git {sub}: fique no ramo deste trabalho; para desfazer um arquivo use `git restore`"
        if sub not in GIT_LIBERADOS:
            return f"git {sub}: fora dos comandos de git liberados"
        if sub == "branch":
            muda = any(a.startswith(_RAMO_MUDA) for a in resto if a.startswith("-"))
            nomeia = any(not a.startswith("-") for a in resto) and not any(a.startswith(_RAMO_SO_LISTA) for a in resto)
            if muda or nomeia:
                return "git branch: só para listar; não crie, apague nem renomeie ramos"
        if sub == "grep" and any(a.startswith(("-O", "--open-files-in-pager")) for a in resto):
            return "git grep: sem abrir os arquivos em outro programa"
        if any(a == "--output" or a.startswith("--output=") for a in resto):
            return f"git {sub} --output: para guardar a saída use o redirecionamento (> arquivo)"
        return None

    def gerenciador(self, programa: str, args: Sequence[str]) -> str | None:
        """npm, pnpm, yarn e bun."""
        if any(a in ("-g", "--global") or a.startswith("--location") for a in args):
            return f"{programa}: instalação global não é comando de projeto"
        if programa == "bun" and (motivo := self.codigo_solto(programa, args, ("e", "p"), ("--eval", "--print"))):
            return motivo
        posicoes = []  # onde estão as palavras que não são opção nem valor de opção
        pular = False
        for i, arg in enumerate(args):
            if pular:
                pular = False
            elif arg in _COM_VALOR:
                pular = True
            elif arg != "--" and not arg.startswith("-"):
                posicoes.append(i)
        if not posicoes:
            return None
        sub = args[posicoes[0]]
        if programa == "npm" and sub not in NPM_LIBERADOS:
            return f"npm {sub}: fora dos comandos de npm liberados" + (" (é um servidor que fica rodando)" if sub == "start" else "")
        if programa != "npm" and sub in _FORA_DO_PROJETO:
            return f"{programa} {sub}: fora dos comandos de projeto"
        if sub in ("exec", "x"):
            return self.npx(args[posicoes[0] + 1 :])
        roteiro = args[posicoes[1]] if sub in ("run", "run-script") and len(posicoes) > 1 else sub
        if set(re.split(r"[:_.-]", roteiro)) & SERVIDORES:
            return f"{programa} {roteiro}: é um servidor que fica rodando; para conferir o trabalho use os testes e o build"
        return None

    def npx(self, args: Sequence[str]) -> str | None:
        nome = ""
        for arg in args:
            if arg in ("-y", "--yes", "-p", "--package", "-c", "--call") or arg.startswith("--package="):
                return "npx: só para ferramentas já instaladas no projeto, sem baixar pacotes"
            if arg != "--" and not arg.startswith("-"):
                nome = arg
                break
        if not nome:
            return "npx: diga a ferramenta"
        pasta = Path(self.onde)
        while "/" not in nome and "\\" not in nome:
            if any((pasta / "node_modules" / ".bin" / (nome + fim)).exists() for fim in ("", ".cmd", ".ps1")):
                return None
            if pasta == self.copia or pasta.parent == pasta:
                break
            pasta = pasta.parent
        return f"npx {nome}: essa ferramenta não está instalada no projeto (rode `npm install` ou `npm ci` antes)"

    def codigo_solto(self, programa: str, args: Sequence[str], curtas: Sequence[str], longas: Sequence[str]) -> str | None:
        """Código passado na linha de comando (node -e): não fica em arquivo nenhum para o usuário revisar."""
        for arg in args:
            curta = re.fullmatch(r"-[a-zA-Z]+", arg) and any(letra in arg for letra in curtas)
            if curta or any(arg == longa or arg.startswith(longa + "=") for longa in longas):
                return f"{programa} {arg}: escreva o código em um arquivo do projeto e rode o arquivo"
        return None

    def python(self, programa: str, args: Sequence[str], do_projeto: bool) -> str | None:
        modulo = roteiro = None
        pular = False
        for i, arg in enumerate(args):  # as opções do Python vêm antes do arquivo; depois dele é tudo do programa
            if pular:
                pular = False
            elif arg in ("-W", "-X", "--check-hash-based-pycs"):
                pular = True
            elif re.fullmatch(r"-[a-zA-Z]*c", arg):
                return f"{programa} -c: escreva o código em um arquivo do projeto e rode o arquivo"
            elif re.fullmatch(r"-[a-zA-Z]*m", arg):
                modulo = args[i + 1] if i + 1 < len(args) else ""
                break
            elif arg == "-":
                return f"{programa} -: escreva o código em um arquivo do projeto e rode o arquivo"
            elif not arg.startswith("-"):
                roteiro = arg
                break
        if modulo is None and roteiro is None:
            return None if any(a in _SEM_ARQUIVO for a in args) else f"{programa}: diga o arquivo ou o módulo a rodar"
        if modulo in ("pip", "ensurepip"):
            return self.pip(f"{programa} -m pip", args, do_projeto)
        if modulo in ("http.server", "webbrowser", "idlelib", "smtpd"):
            return f"{programa} -m {modulo}: fora dos comandos de projeto"
        return None

    def pip(self, como: str, args: Sequence[str], do_projeto: bool) -> str | None:
        if not do_projeto:
            return (
                f"{como}: isso instalaria fora do projeto. Crie o ambiente (`python -m venv .venv`) e use o Python "
                "de dentro dele (.venv/Scripts/python -m pip no Windows, .venv/bin/python -m pip nos outros)"
            )
        if any(a in _INSTALA_FORA for a in args):
            return f"{como}: instale só no ambiente do projeto, sem --user nem --system"
        return None

    def ferramenta(self, programa: str, args: Sequence[str]) -> str | None:
        posicionais = [a for a in args if not a.startswith("-")]
        sub = posicionais[0] if posicionais else ""
        if sub in _SUBCOMANDOS_NEGADOS.get(programa, ()):
            return f"{programa} {sub}: instala ou publica fora do projeto"
        if programa in ("uv", "pipenv", "pdm", "poetry", "composer", "deno") and any(a in _INSTALA_FORA for a in args):
            return f"{programa}: instalação fora do projeto não é comando de projeto"
        # Opções que mandam a ferramenta rodar outro programa ou um trecho de shell.
        if programa == "go" and any(a.lstrip("-").split("=")[0] in ("exec", "toolexec") for a in args if a.startswith("-")):
            return "go -exec/-toolexec: sem opção que roda outro programa"
        if programa == "make" and any(a == "-" or a == "-E" or a.startswith("--eval") for a in args):
            return "make --eval: as regras ficam no Makefile do projeto, não na linha de comando"
        if programa == "cargo" and any(a.startswith("--config") for a in args):
            return "cargo --config: a configuração fica nos arquivos do projeto, não na linha de comando"
        if programa == "go" and sub == "env" and "-w" in args:
            return "go env -w: muda a configuração do usuário, não a do projeto"
        if programa == "go" and sub == "run" and any("@" in a for a in posicionais):
            return "go run pacote@versão: baixa e roda código de fora; rode só o que está no projeto"
        if programa == "deno" and any(a.startswith(("npm:", "jsr:")) for a in posicionais):
            return "deno: baixa e roda código de fora; rode só o que está no projeto"
        if (programa, sub) in _EMBRULHAM:
            dentro = list(args[args.index(sub) + 1 :])
            while dentro and (dentro[0].startswith("-") or dentro[0] == "--"):
                dentro = dentro[1:]
            return self.comando(dentro) if dentro else None
        if programa in ("php", "ruby") and (motivo := self.codigo_solto(programa, args, ("r", "e"), ())):
            return motivo
        if sub in SERVIDORES:
            return f"{programa} {sub}: é um servidor que fica rodando; para conferir o trabalho use os testes e o build"
        return None

    def utilitario(self, programa: str, args: Sequence[str]) -> str | None:
        posicionais = [a for a in args if not a.startswith("-")]
        if programa == "cd":
            if len(posicionais) != 1 or len(args) != 1:
                return "cd: diga uma pasta de dentro do projeto"
            motivo = self.caminho(posicionais[0])
            if motivo is None:
                self.destino = _resolver(posicionais[0], self.onde, self.sistema)
                if self.sistema == sys.platform:
                    self.destino = os.path.realpath(self.destino)  # o shell fica na pasta real, não no atalho
            return motivo
        if programa == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls") for a in args):
            return "find: sem -exec nem -delete; liste e depois aja sobre os arquivos"
        if any(a.startswith(("--pre", "--hostname-bin", "--compress-program")) for a in args):
            return f"{programa}: sem opção que roda outro programa"
        if programa == "sleep" and any(not re.fullmatch(r"\d{1,2}(\.\d+)?s?", a) for a in args):
            return "sleep: no máximo 99 segundos"
        if programa == "cp" and any(a.startswith("--target-directory") or re.fullmatch(r"-[a-zA-Z]*t[a-zA-Z]*", a) for a in args):
            return "cp: sem -t; escreva o destino por último"
        if programa in ESCRITORES:
            for i, arg in enumerate(posicionais):
                origem_de_copia = programa == "cp" and i < len(posicionais) - 1
                motivo = self.caminho(arg, escrever=not origem_de_copia, com_skills=origem_de_copia)
                if motivo:
                    return motivo
                if programa in ("rm", "mv", "rmdir") and self.e_a_raiz(arg):
                    return f"{programa}: não na raiz da cópia de trabalho"
        return None

    # --- um comando simples -------------------------------------------------------------

    def comando(self, tokens: Sequence[str]) -> str | None:
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            nome, valor = tokens[0].split("=", 1)
            if nome not in VARIAVEIS_LIBERADAS:
                return f"variável de ambiente fora da lista: {nome}"
            if motivo := self.argumentos([valor]):
                return motivo
            tokens = tokens[1:]
        if not tokens:
            return None  # só a variável: não roda nada
        bruto, args = tokens[0], tokens[1:]
        programa = _nome_do_programa(bruto)
        por_caminho = "/" in bruto or "\\" in bruto
        com_skills = por_caminho  # um script pode ler os arquivos da própria skill
        conferir: Sequence[str] = args
        motivo: str | None = None

        if por_caminho:
            # Um programa indicado por caminho: script ou ferramenta do próprio projeto (ou de uma skill instalada).
            if self.argumentos([bruto], com_skills=True):
                return f"programa fora da cópia de trabalho: {bruto}"
            if re.fullmatch(r"python[\d.]*|py", programa):
                motivo = self.python(programa, args, do_projeto=True)
            elif programa in ("pip", "pip3"):
                motivo = self.pip(programa, args, do_projeto=True)
            elif programa in ("node", "bun"):
                motivo = self.codigo_solto(programa, args, ("e", "p"), ("--eval", "--print"))
        elif programa in NEGADOS:
            return f"{programa}: {NEGADOS[programa]}"
        elif programa == "git":
            motivo = self.git(args)
            if "commit" in args:  # a mensagem do commit é texto livre, não caminho
                conferir = [a for i, a in enumerate(args) if not (i and re.fullmatch(r"-[a-zA-Z]*m|--message", args[i - 1]))]
        elif programa in ("npm", "pnpm", "yarn", "bun"):
            motivo = self.gerenciador(programa, args)
        elif programa == "npx":
            motivo = self.npx(args)
        elif programa in ("pip", "pip3"):
            motivo = self.pip(programa, args, do_projeto=False)
        elif re.fullmatch(r"python[\d.]*|py", programa):
            motivo = self.python(programa, args, do_projeto=False)
            com_skills = True
        elif programa == "node":
            motivo = self.codigo_solto(programa, args, ("e", "p"), ("--eval", "--print"))
            if motivo is None and all(a.startswith("-") for a in args) and not any(a in (*_SEM_ARQUIVO, "--test") for a in args):
                motivo = "node: diga o arquivo a rodar"
            com_skills = True
        elif programa in LEITORES or programa in ESCRITORES or programa == "cd":
            motivo = self.utilitario(programa, args)
            com_skills = programa in LEEM_SKILLS
            if programa in SO_TEXTO:
                conferir = []
            elif programa == "cp":
                conferir = [a for a in args if a.startswith("-")]  # origens e destino já conferidos, cada um com a sua regra
        elif programa in FERRAMENTAS:
            motivo = self.ferramenta(programa, args)
        elif programa not in self.extras:
            return (
                f"{programa}: programa fora da lista dos comandos de projeto. Use as ferramentas do projeto "
                "(npm, python, pytest, git local...) e, se precisar mesmo dele, diga isso no relatório"
            )
        if motivo:
            return motivo
        if programa not in LEITORES and programa not in ESCRITORES and any(a in _FICA_RODANDO for a in args):
            return f"{programa}: sem modo que fica rodando (--watch); rode uma vez só"
        return self.argumentos(
            conferir,
            com_skills=com_skills,
            a_fundo=programa in LEITORES or programa in ESCRITORES,
            pode_gravar=por_caminho or programa not in SEM_SAIDA_EM_ARQUIVO,
        )


def avaliar_comando(
    comando: Any, copia: Path, leitura: Sequence[Path] = (), extras: Collection[str] = (), sistema: str = sys.platform
) -> str | None:
    """Devolve o motivo da recusa de um comando do Bash, ou None se ele pode rodar."""
    if not isinstance(comando, str) or not comando.strip():
        return "Comando recusado: comando vazio."
    if len(comando) > MAX_COMANDO:
        return f"Comando recusado: mais de {MAX_COMANDO} caracteres. Divida em passos."
    analise = _Analise(copia, list(leitura), extras, sistema)
    try:
        partes = _separar(comando, sistema)
        # Cada comando simples: o operador que vem antes dele, as palavras e os redirecionamentos.
        simples: list[tuple[str, list[str], list[tuple[str, str]]]] = [(";", [], [])]
        i = 0
        while i < len(partes):
            tipo, valor = partes[i]
            if tipo == "p":
                simples[-1][1].append(valor)
            elif valor in ("&&", "||", ";", "|"):
                simples.append((valor, [], []))
            else:
                if i + 1 >= len(partes) or partes[i + 1][0] != "p":
                    raise _Recusa("redirecionamento sem destino")
                simples[-1][2].append((valor, partes[i + 1][1]))
                i += 1
            i += 1
        if not any(palavras or desvios for _, palavras, desvios in simples):
            raise _Recusa("comando vazio")

        # Em que pasta cada comando roda? Um `cd` pode falhar (pasta que não existe), pode ser
        # pulado (depois de || ou &&) e, dentro de um pipe, não vale para o que vem depois. Então a
        # análise guarda todas as pastas em que o shell pode estar (`todas`) e confere cada comando
        # a partir de cada uma. Só em uma sequência feita apenas de && tudo o que roda vem depois
        # de todos os `cd` anteriores terem dado certo: aí as pastas `vigentes` são exatas.
        todas = {str(copia)}
        vigentes = {str(copia)}
        exato = True
        i = 0
        while i < len(simples):
            fim = i + 1
            while fim < len(simples) and simples[fim][0] == "|":
                fim += 1
            antes = simples[i][0]
            if antes == ";":
                exato = True
            elif antes == "||":
                exato = False
            if antes != "&&" or not exato:
                vigentes = set(todas)
            destinos: set[str] = set()
            for onde in sorted(vigentes):
                for _, palavras, desvios in simples[i:fim]:
                    analise.onde, analise.destino = onde, None
                    for operador, alvo in desvios:
                        if operador in (">&", "<&") and re.fullmatch(r"\d+|-", alvo):
                            continue  # 2>&1
                        if motivo := analise.caminho(alvo, escrever=operador != "<"):
                            raise _Recusa(motivo)
                    if palavras and (motivo := analise.comando(palavras)):
                        raise _Recusa(motivo)
                    if analise.destino is not None and fim - i == 1:
                        destinos.add(analise.destino)  # um `cd` sozinho, fora de pipe: pode ter mudado a pasta
            if destinos:
                todas |= destinos
                vigentes = destinos if exato else set(todas)
                if len(todas) > 16:
                    raise _Recusa("cd demais no mesmo comando: rode um passo de cada vez")
            i = fim
    except _Recusa as recusa:
        return f"Comando recusado: {recusa}."
    return None


# --- ferramentas de arquivo e o gancho ---------------------------------------------------

LEITURA = {"Read": ("file_path",), "Glob": ("path",), "Grep": ("path",)}
ESCRITA = {"Write": ("file_path",), "Edit": ("file_path",), "NotebookEdit": ("notebook_path",)}
LIVRES = frozenset({"TodoWrite", "Skill"})  # não tocam em arquivo nem rodam comando por conta própria


class GuardaDoTrabalho:
    """Confere cada chamada de ferramenta de um trabalhador. Falha fechada: na dúvida, recusa."""

    def __init__(self, copia: Path, leitura: Sequence[Path] = (), extras: Collection[str] = (), sistema: str = sys.platform) -> None:
        self.copia = Path(os.path.realpath(copia))
        self.leitura = [Path(os.path.realpath(p)) for p in leitura]
        self.extras, self.sistema = extras, sistema
        self.recusas: list[str] = []  # vai para o relatório do trabalho

    def _dentro(self, bruto: Any, escrever: bool) -> str | None:
        if not isinstance(bruto, str) or not bruto.strip():
            return "Caminho vazio."
        caminho = Path(os.path.expanduser(bruto))
        completo = Path(os.path.realpath(caminho if caminho.is_absolute() else self.copia / caminho))  # segue atalhos
        raizes = [self.copia] if escrever else [self.copia, *self.leitura]
        if not any(completo == r or completo.is_relative_to(r) for r in raizes):
            return f"Caminho fora da cópia de trabalho: {bruto}"
        if escrever and (motivo := reservado(completo.relative_to(self.copia).parts)):
            return f"Recusado: {motivo}."
        return None

    def decidir(self, ferramenta: str, entrada: dict[str, Any]) -> str | None:
        if ferramenta in LIVRES:
            return None
        if ferramenta == "Bash":
            if entrada.get("run_in_background"):
                return "Comando recusado: nada em segundo plano."
            return avaliar_comando(entrada.get("command"), self.copia, self.leitura, self.extras, self.sistema)
        if ferramenta in ESCRITA or ferramenta in LEITURA:
            escrever = ferramenta in ESCRITA
            for campo in (ESCRITA if escrever else LEITURA)[ferramenta]:
                valor = entrada.get(campo)
                if valor is None and ferramenta in ("Glob", "Grep"):
                    continue  # sem `path`, a busca é na cópia de trabalho
                motivo = self._dentro(valor, escrever)
                if motivo:
                    return motivo
            for campo in ("pattern",) if ferramenta == "Glob" else ("glob",) if ferramenta == "Grep" else ():
                padrao = entrada.get(campo)
                if isinstance(padrao, str) and _padrao_escapa(padrao):
                    return f"Padrão de busca que sai da cópia de trabalho: {padrao}"
            return None
        return f"Ferramenta fora do que o trabalhador pode usar: {ferramenta}"

    async def hook(self, dados: dict[str, Any], _tool_use_id: str | None, _contexto: Any) -> dict[str, Any]:
        ferramenta = str(dados.get("tool_name", ""))
        try:
            motivo = self.decidir(ferramenta, dados.get("tool_input") or {})
        except Exception as erro:  # noqa: BLE001 - qualquer falha da guarda vira recusa
            motivo = f"A guarda do trabalho falhou ao avaliar a chamada ({type(erro).__name__})."
        if motivo is None:
            return {}
        self.recusas.append(f"{ferramenta}: {motivo}"[:300])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": motivo}}
