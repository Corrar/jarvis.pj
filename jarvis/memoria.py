"""Memória de longo prazo: pastas de arquivos Markdown, um assunto por arquivo.

São arquivos de texto comuns. Dá para abrir, corrigir ou apagar qualquer um deles
em um editor; o Jarvis lê as pastas de novo a cada sessão.

Categorias (cada uma é uma pasta):
- fato:        o que o Jarvis sabe sobre o usuário. Fica em memoria/. No prompt entra só o índice.
- preferencia: como o usuário quer que o Jarvis trabalhe e responda. Fica em memoria/preferencias/.
               No prompt entra o texto inteiro, porque é regra a seguir.
- licao:       erros que ele já cometeu e o que fazer no lugar. Fica em memoria/licoes/.
               No prompt entra o texto inteiro, pelo mesmo motivo.

Para criar outra categoria, acrescente uma linha em CATEGORIAS.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# categoria → subpasta dentro de memoria/ ("" = a própria pasta)
CATEGORIAS = {"fato": "", "preferencia": "preferencias", "licao": "licoes"}

MAX_TITULO = 80
# preferência e lição são curtas: vão inteiras no prompt, toda sessão
MAX_CONTEUDO = {"fato": 2000, "preferencia": 400, "licao": 400}
MAX_ARQUIVOS = {"fato": 200, "preferencia": 40, "licao": 40}
MAX_INDICE = 4000  # caracteres do índice de fatos que entram no prompt de sistema

# Segredos não vão para a memória: ela é texto puro no disco e entra no prompt.
_PARECE_SEGREDO = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")


class ErroMemoria(Exception):
    """Pedido inválido; a mensagem volta para o modelo como erro da ferramenta."""


@dataclass(frozen=True)
class Lembranca:
    nome: str  # nome do arquivo, sem .md
    titulo: str
    resumo: str  # primeira linha do conteúdo
    conteudo: str = ""  # texto completo, sem o título


def nome_de_arquivo(texto: str) -> str:
    """'Café às 9h' → 'cafe-as-9h'. Só letras, números e hífen: não há como sair da pasta."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    nome = re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")[:60].strip("-")
    if not nome:
        raise ErroMemoria("O título precisa ter ao menos uma letra ou número.")
    return nome


class Memoria:
    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta

    def pasta_de(self, categoria: str = "fato") -> Path:
        if categoria not in CATEGORIAS:
            raise ErroMemoria(f"Categoria desconhecida: '{categoria}'. Use uma de: {', '.join(CATEGORIAS)}.")
        return self.pasta / CATEGORIAS[categoria] if CATEGORIAS[categoria] else self.pasta

    def _arquivo(self, nome: str, categoria: str = "fato") -> Path:
        return self.pasta_de(categoria) / f"{nome_de_arquivo(nome)}.md"

    def salvar(self, titulo: str, conteudo: str, categoria: str = "fato") -> str:
        """Cria ou substitui a lembrança com este título. Devolve o nome do arquivo."""
        pasta = self.pasta_de(categoria)
        titulo = " ".join(titulo.split())
        conteudo = conteudo.strip()
        if not titulo or not conteudo:
            raise ErroMemoria("Título e conteúdo são obrigatórios.")
        if len(titulo) > MAX_TITULO:
            raise ErroMemoria(f"Título com mais de {MAX_TITULO} caracteres.")
        if len(conteudo) > MAX_CONTEUDO[categoria]:
            raise ErroMemoria(
                f"Conteúdo com mais de {MAX_CONTEUDO[categoria]} caracteres. Resuma ou divida em duas."
            )
        if _PARECE_SEGREDO.search(titulo) or _PARECE_SEGREDO.search(conteudo):
            raise ErroMemoria("Isso parece uma chave ou senha. Segredos não são guardados na memória.")

        arquivo = self._arquivo(titulo, categoria)
        pasta.mkdir(parents=True, exist_ok=True)
        if not arquivo.exists() and len(list(pasta.glob("*.md"))) >= MAX_ARQUIVOS[categoria]:
            raise ErroMemoria(
                f"Já existem {MAX_ARQUIVOS[categoria]} arquivos nesta categoria. Apague ou junte alguns antes de criar outro."
            )
        arquivo.write_text(f"# {titulo}\n\n{conteudo}\n", encoding="utf-8")
        return arquivo.stem

    def ler(self, nome: str, categoria: str = "fato") -> str:
        arquivo = self._arquivo(nome, categoria)
        if not arquivo.is_file():
            raise ErroMemoria(f"Não há nada chamado '{nome}' na categoria '{categoria}'.")
        return arquivo.read_text(encoding="utf-8")

    def apagar(self, nome: str, categoria: str = "fato") -> str:
        arquivo = self._arquivo(nome, categoria)
        if not arquivo.is_file():
            raise ErroMemoria(f"Não há nada chamado '{nome}' na categoria '{categoria}'.")
        arquivo.unlink()
        return arquivo.stem

    def listar(self, categoria: str = "fato") -> list[Lembranca]:
        pasta = self.pasta_de(categoria)
        if not pasta.is_dir():
            return []
        itens = []
        for arquivo in sorted(pasta.glob("*.md")):
            try:
                linhas = [l.strip() for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip()]
            except (OSError, UnicodeDecodeError):
                continue  # arquivo ilegível não derruba a sessão
            titulo = linhas[0].lstrip("# ").strip() if linhas else arquivo.stem
            resumo = linhas[1] if len(linhas) > 1 else ""
            itens.append(Lembranca(arquivo.stem, titulo or arquivo.stem, resumo[:120], " ".join(linhas[1:])))
        return itens

    def indice(self, limite: int = MAX_INDICE, categoria: str = "fato") -> str:
        """Uma linha por lembrança, para o prompt de sistema. Vazio se não houver nenhuma."""
        linhas, usado = [], 0
        itens = self.listar(categoria)
        for item in itens:
            linha = f"- {item.nome}: {item.titulo}" + (f" — {item.resumo}" if item.resumo else "")
            if usado + len(linha) > limite:
                linhas.append(f"- (mais {len(itens) - len(linhas)} lembranças; use memoria_listar)")
                break
            linhas.append(linha)
            usado += len(linha) + 1
        return "\n".join(linhas)

    def inteiras(self, categoria: str) -> str:
        """Todas as anotações de uma categoria, com o texto completo, para o prompt de sistema."""
        limite = MAX_CONTEUDO[categoria]
        return "\n".join(f"- {item.titulo}: {item.conteudo[:limite]}" for item in self.listar(categoria))

    def licoes(self) -> str:
        """As lições inteiras, para o prompt de sistema. Vazio se não houver nenhuma."""
        return self.inteiras("licao")

    def preferencias(self) -> str:
        """As preferências inteiras, para o prompt de sistema. Vazio se não houver nenhuma."""
        return self.inteiras("preferencia")
