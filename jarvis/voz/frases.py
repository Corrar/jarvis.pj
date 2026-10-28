"""Texto → frases prontas para falar.

O modelo entrega a resposta aos poucos. O Separador devolve cada frase assim que
ela fecha, para a fala começar antes de a resposta inteira chegar.
"""

from __future__ import annotations

import re

# Ponto depois destas palavras não fecha frase.
_ABREVIACOES = {"sr", "sra", "dr", "dra", "prof", "profa", "etc", "ex", "av", "nº", "pág", "tel", "ltda", "cia"}

# Fim de frase (pontuação + aspas ou parênteses de fechamento + espaço) ou quebra de linha.
_FRONTEIRA = re.compile(r"([.!?…]+[\"')\]»”]*)(\s+)|(\n+)")

# Frase longa demais sem pontuação final: corta na última vírgula para não atrasar a fala.
_LIMITE = 240


def _dinheiro(achado: re.Match[str]) -> str:
    moeda = "reais" if achado.group(1) == "R$" else "dólares"
    return f"{achado.group(2).replace('.', '')} {moeda}"  # 1.234,56 → 1234,56


def limpar_para_fala(texto: str) -> str:
    """Tira o que só faz sentido na tela: marcações, marcadores de lista, links."""
    t = re.sub(r"```.*?```", " ", texto, flags=re.S)
    t = re.sub(r"(R\$|US\$)\s?(\d[\d.]*(?:,\d+)?)", _dinheiro, t)  # R$ 4,98 → 4,98 reais
    t = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", t)  # [texto](link) → texto
    t = re.sub(r"https?://\S+", "link", t)
    t = re.sub(r"^\s*(?:[-*•–]|\d{1,2}[.)])\s+", "", t, flags=re.M)  # marcador de lista
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)  # título
    t = re.sub(r"[*`~|]", "", t)
    t = t.replace("_", " ")  # Relatorio_Royale → Relatorio Royale
    return re.sub(r"\s+", " ", t).strip()


def tem_o_que_falar(texto: str) -> bool:
    return any(c.isalnum() for c in texto)


class Separador:
    """Acumula pedaços de texto e devolve as frases que já fecharam."""

    def __init__(self) -> None:
        self._resto = ""

    def alimentar(self, trecho: str) -> list[str]:
        self._resto += trecho
        return self._extrair()

    def descarregar(self) -> list[str]:
        """Fim do bloco de texto: devolve o que sobrou, mesmo sem pontuação final."""
        frases = self._extrair()
        sobra = self._resto.strip()
        self._resto = ""
        if sobra:
            frases.append(sobra)
        return frases

    def _extrair(self) -> list[str]:
        frases: list[str] = []
        inicio = 0
        for achado in _FRONTEIRA.finditer(self._resto):
            if achado.group(3) is None and self._ponto_nao_fecha(self._resto[inicio : achado.end(1)]):
                continue
            fim = achado.end(1) if achado.group(3) is None else achado.start(3)
            frase = self._resto[inicio:fim].strip()
            if frase:
                frases.append(frase)
            inicio = achado.end()
        self._resto = self._resto[inicio:]

        while len(self._resto) > _LIMITE:
            corte = max(self._resto.rfind(", ", 0, _LIMITE), self._resto.rfind("; ", 0, _LIMITE))
            if corte <= 0:
                break
            frases.append(self._resto[: corte + 1].strip())
            self._resto = self._resto[corte + 2 :]
        return frases

    @staticmethod
    def _ponto_nao_fecha(candidata: str) -> bool:
        if not candidata.endswith("."):
            return False
        palavras = candidata[:-1].split()
        if not palavras:
            return True
        ultima = palavras[-1].lower().strip("(\"'")
        if ultima in _ABREVIACOES:
            return True
        # "1." no começo da linha é numeração de lista, não fim de frase.
        return len(palavras) == 1 and ultima.isdigit() and len(ultima) <= 2
