"""Guarda de acesso: decide, antes de cada ferramenta rodar, se a chamada pode seguir.

É a segunda camada de proteção. A primeira é a configuração do agente
(agente.py), que só entrega ao modelo as ferramentas da lista e nega o resto.
A guarda confere o que a configuração não cobre:

- *onde* a leitura acontece (só nas pastas liberadas);
- *quando* uma gravação pode acontecer (só em turno que não leu conteúdo externo).
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Pastas que nunca são lidas, em qualquer nível dentro de uma pasta liberada.
PASTAS_SENSIVEIS = (
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    ".kube",
    ".docker",
    ".claude",
    "AppData",
)

# Arquivos que nunca são lidos, pelo nome.
ARQUIVOS_SENSIVEIS = (
    ".env",
    ".env.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.ppk",
    "*.kdbx",
    "id_rsa*",
    "id_ed25519*",
)

FERRAMENTAS_DE_LEITURA = ("Read", "Glob", "Grep")

# Trazem para a conversa texto que não foi o usuário que escreveu.
NATIVAS_QUE_CONTAMINAM = ("Read", "Glob", "Grep", "WebSearch")
# Destas, as que trazem conteúdo (página, arquivo). O Glob traz só nomes de arquivo.
NATIVAS_COM_CONTEUDO = ("Read", "Grep", "WebSearch")

_PASTAS_SENSIVEIS_MIN = {nome.lower() for nome in PASTAS_SENSIVEIS}


def regras_de_negacao() -> list[str]:
    """As mesmas listas acima, no formato de regra de permissão do Claude Code.

    Cobrem o que a guarda não enxerga: arquivos sensíveis que uma busca (Grep/Glob)
    encontraria *dentro* de uma pasta liberada.
    """
    # "//**/" = em qualquer nível, em qualquer unidade de disco.
    regras = [f"Read(//**/{pasta}/**)" for pasta in PASTAS_SENSIVEIS]
    regras += [f"Read(//**/{padrao})" for padrao in ARQUIVOS_SENSIVEIS]
    return regras


def resolver(bruto: str, cwd: Path) -> Path:
    caminho = Path(os.path.expanduser(bruto))
    if not caminho.is_absolute():
        caminho = cwd / caminho
    # realpath segue atalhos simbólicos e resolve "..", mesmo se o arquivo não existir.
    return Path(os.path.realpath(caminho))


def recusa_de_caminho(bruto: str, pastas: Sequence[Path], cwd: Path) -> str | None:
    caminho = resolver(bruto, cwd)

    raiz = next((p for p in pastas if caminho == p or caminho.is_relative_to(p)), None)
    if raiz is None:
        liberadas = "; ".join(str(p) for p in pastas)
        return f"Caminho fora das pastas liberadas ({liberadas}): {caminho}"

    relativo = caminho.relative_to(raiz)
    for parte in relativo.parts:
        if parte.lower() in _PASTAS_SENSIVEIS_MIN:
            return f"Pasta protegida, o Jarvis não lê: {parte}"

    nome = caminho.name.lower()
    for padrao in ARQUIVOS_SENSIVEIS:
        if fnmatch.fnmatchcase(nome, padrao.lower()):
            return f"Arquivo protegido, o Jarvis não lê: {caminho.name}"

    return None


def _padrao_escapa(padrao: str) -> bool:
    """Padrão de Glob que aponta para fora da pasta de busca."""
    normalizado = padrao.replace("\\", "/")
    if normalizado.startswith(("/", "~")):
        return True
    if len(normalizado) >= 2 and normalizado[1] == ":":  # C:...
        return True
    return ".." in normalizado.split("/")


def avaliar(
    ferramenta: str,
    entrada: dict[str, Any],
    pastas: Sequence[Path],
    cwd: Path,
    permitidas: Sequence[str],
) -> str | None:
    """Devolve o motivo da recusa, ou None se a chamada pode seguir."""
    if ferramenta not in permitidas:
        return f"Ferramenta fora da lista do Jarvis: {ferramenta}"

    if ferramenta not in FERRAMENTAS_DE_LEITURA:
        return None

    if ferramenta == "Read":
        bruto = entrada.get("file_path")
        if not isinstance(bruto, str) or not bruto.strip():
            return "Read sem file_path."
        return recusa_de_caminho(bruto, pastas, cwd)

    # Glob e Grep: sem "path", a busca acontece na pasta de trabalho.
    bruto = entrada.get("path")
    if bruto is not None and not isinstance(bruto, str):
        return f"{ferramenta} com path inválido."
    recusa = recusa_de_caminho(bruto or str(cwd), pastas, cwd)
    if recusa:
        return recusa

    if ferramenta == "Glob":
        padrao = entrada.get("pattern")
        if isinstance(padrao, str) and _padrao_escapa(padrao):
            return (
                "Padrão de Glob com caminho absoluto ou '..'. "
                "Use 'path' para a pasta e um padrão relativo."
            )

    return None


class Guarda:
    """Hook PreToolUse do Agent SDK, com o estado do turno em andamento.

    Regra do turno limpo: depois que o modelo lê conteúdo externo, o que grava ou
    age sem perguntar fica bloqueado até o usuário falar de novo. Um texto
    malicioso lido nesse turno não consegue, sozinho, plantar uma lembrança nem
    disparar uma ação; o usuário precisa confirmar com as próprias palavras. Isso
    barra o efeito automático, não a persuasão: a sugestão lida continua no
    contexto quando o usuário responder.

    São dois níveis:
    - memória (`gravam`): bloqueada por qualquer leitura externa, inclusive nomes de arquivo;
    - ações diretas (`agem`): bloqueadas só por leitura de conteúdo (página, arquivo,
      conversa antiga). Achar um arquivo pelo nome e abri-lo no mesmo turno é permitido.

    As ações dentro da página do navegador (`na_pagina`: rolar, clicar, digitar) são a
    exceção: existem para ser usadas depois de ver a página, então a leitura não as
    bloqueia. O que as contém é outro: só alcançam a janela do navegador (janelas.py) e
    têm um limite por turno, para uma página maliciosa não manter o Jarvis clicando.
    """

    def __init__(
        self,
        pastas: Sequence[Path],
        cwd: Path,
        permitidas: Sequence[str],
        contaminam: Sequence[str] = NATIVAS_QUE_CONTAMINAM,
        gravam: Sequence[str] = (),
        conteudo: Sequence[str] = NATIVAS_COM_CONTEUDO,
        agem: Sequence[str] = (),
        na_pagina: Sequence[str] = (),
        limite_na_pagina: int = 40,
        turno_limpo: bool = True,
    ) -> None:
        self._pastas = pastas
        self._cwd = cwd
        self._permitidas = permitidas
        self._contaminam = frozenset(contaminam)
        self._gravam = frozenset(gravam)
        self._conteudo = frozenset(conteudo)
        self._agem = frozenset(agem)  # vazio quando as ações pedem confirmação digitada
        self._na_pagina = frozenset(na_pagina)
        self._limite_na_pagina = limite_na_pagina
        # Com JARVIS_TURNO_LIMPO=nao o usuário abre mão desta regra: gravar e agir deixam de
        # esperar a fala seguinte. O resto do guarda (pastas, ferramentas liberadas, limite
        # dentro da página) continua valendo — isso não é negociável por ajuste.
        self._turno_limpo = turno_limpo
        self.contaminado_por: str | None = None
        self.leu_conteudo_por: str | None = None
        self.acoes_na_pagina = 0

    def novo_turno(self) -> None:
        """Chamado quando o usuário fala: o turno começa limpo."""
        self.contaminado_por = None
        self.leu_conteudo_por = None
        self.acoes_na_pagina = 0

    def contaminar(self, origem: str) -> None:
        """Uma ferramenta acabou de trazer conteúdo de fora para a conversa (a saída de um comando).

        Para as ferramentas das listas `contaminam` isso acontece sozinho, na hora da chamada.
        Esta é para a que só traz conteúdo depois de rodar, e que pode nem rodar (se o usuário
        não confirmar).
        """
        self.contaminado_por = origem
        self.leu_conteudo_por = origem

    def decidir(self, ferramenta: str, entrada: dict[str, Any]) -> str | None:
        """Devolve o motivo da recusa, ou None se a chamada pode seguir."""
        motivo = avaliar(ferramenta, entrada, self._pastas, self._cwd, self._permitidas)
        if motivo is not None:
            return motivo
        if self._turno_limpo and ferramenta in self._gravam and self.contaminado_por is not None:
            return (
                f"Gravação bloqueada: neste turno você já leu conteúdo externo ({self.contaminado_por}). "
                "Diga ao usuário o que pretende gravar e peça que ele confirme; na fala seguinte dele a gravação é liberada."
            )
        if self._turno_limpo and ferramenta in self._agem and self.leu_conteudo_por is not None:
            return (
                f"Ação bloqueada: neste turno você já leu conteúdo externo ({self.leu_conteudo_por}). "
                "Diga ao usuário, em uma frase, exatamente o que pretende fazer e peça que ele confirme; "
                "na fala seguinte dele a ação é liberada."
            )
        if ferramenta in self._na_pagina:
            if self.acoes_na_pagina >= self._limite_na_pagina:
                return (
                    f"Limite de {self._limite_na_pagina} ações dentro da página neste turno. Pare aqui: diga ao usuário "
                    "o que já foi feito e o que falta, e espere a próxima fala dele."
                )
            self.acoes_na_pagina += 1
        curto = ferramenta.rsplit("__", 1)[-1]  # nome curto, para a mensagem
        if ferramenta in self._contaminam:
            self.contaminado_por = curto
        if ferramenta in self._conteudo:
            self.leu_conteudo_por = curto
        return None

    async def hook(self, dados: dict[str, Any], _tool_use_id: str | None, _contexto: Any) -> dict[str, Any]:
        try:
            motivo = self.decidir(str(dados.get("tool_name", "")), dados.get("tool_input") or {})
        except Exception as erro:  # noqa: BLE001 - qualquer falha da guarda vira recusa (falha fechada)
            motivo = f"A guarda do Jarvis falhou ao avaliar a chamada ({type(erro).__name__})."

        if motivo is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": motivo,
            }
        }
