"""O bom dia: na primeira vez do dia em que o usuário aparece, o Jarvis cumprimenta e conta o que importa.

"Aparecer" é o Jarvis subir (ele sobe junto com o Windows) ou a tela ser desbloqueada. O
cumprimento é um pedido montado aqui e atendido pela conversa como qualquer outro: o modelo
consulta o tempo (ferramenta clima), pesquisa as notícias de tecnologia e do mercado, lê as
rotinas do dia e os trabalhos terminados, fala tudo e põe cartões no painel.

Uma vez por dia: a data do último cumprimento fica em um arquivo. E só dentro da janela de
horas de JARVIS_BOM_DIA (padrão das 5h às 14h): quem liga o computador às 23h não ganha um
"bom dia".
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

JANELA_PADRAO = (5, 14)  # horas: de … até (exclusivo)
_DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")


def ler_janela(bruto: str) -> tuple[int, int] | None:
    """JARVIS_BOM_DIA: vazio = das 5h às 14h; "nao" desliga; "sempre" = qualquer hora; "6-12" = das 6h às 12h."""
    texto = str(bruto or "").strip().lower()
    if not texto or texto in ("sim", "s", "1", "true"):
        return JANELA_PADRAO
    if texto in ("nao", "não", "n", "0", "false", "desligado"):
        return None
    if texto == "sempre":
        return (0, 24)
    partes = texto.replace("h", "").split("-")
    try:
        de, ate = int(partes[0]), int(partes[1])
    except (ValueError, IndexError):
        raise ValueError(f"JARVIS_BOM_DIA: use nao, sempre ou um intervalo de horas como 6-12 (recebi: {bruto}).") from None
    if not (0 <= de < ate <= 24):
        raise ValueError(f"JARVIS_BOM_DIA: o intervalo precisa ir de 0 a 24, com o começo antes do fim (recebi: {bruto}).")
    return (de, ate)


def cumprimento(agora: datetime) -> str:
    if agora.hour < 12:
        return "Bom dia"
    if agora.hour < 18:
        return "Boa tarde"
    return "Boa noite"


class Saudacao:
    """Quando cumprimentar, e o pedido que vira o cumprimento."""

    def __init__(self, arquivo: Path, janela: tuple[int, int] | None = JANELA_PADRAO, relogio: Callable[[], datetime] | None = None) -> None:
        self.arquivo, self.janela, self._relogio = arquivo, janela, relogio or datetime.now
        self.ultimo = ""  # a data (AAAA-MM-DD) do último cumprimento
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            self.ultimo = str(dados.get("ultimo") or "") if isinstance(dados, dict) else ""
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.exception("Não consegui ler %s; o bom dia vale como se fosse o primeiro", arquivo)

    @property
    def ligada(self) -> bool:
        return self.janela is not None

    def devida(self, agora: datetime | None = None) -> bool:
        """É hora de cumprimentar: ligado, dentro da janela e ainda não feito hoje."""
        if self.janela is None:
            return False
        agora = agora or self._relogio()
        return self.janela[0] <= agora.hour < self.janela[1] and self.ultimo != agora.strftime("%Y-%m-%d")

    def marcar(self, agora: datetime | None = None) -> None:
        agora = agora or self._relogio()
        self.ultimo = agora.strftime("%Y-%m-%d")
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text(json.dumps({"ultimo": self.ultimo}), encoding="utf-8")
        except OSError:
            log.exception("Não consegui gravar a data do bom dia em %s", self.arquivo)

    def pedido(self, usuario: str | None, cidade: str, agenda: Any = None, oficina: Any = None, agora: datetime | None = None) -> str:
        """O pedido que a conversa atende: o que dizer e em que ordem, com o que o Jarvis já sabe."""
        agora = agora or self._relogio()
        nome = f" ao usuário, {usuario}," if usuario else " ao usuário"
        linhas = [
            f"[bom dia] É {_DIAS[agora.weekday()]}, {agora:%d/%m/%Y}, {agora:%H:%M}: a primeira vez que o usuário aparece hoje. "
            f"Faça o cumprimento do dia, falado, direto e em até doze frases, nesta ordem, sem perguntar nada e sem esperar resposta:",
            f"1. Diga \"{cumprimento(agora)}\"{nome} em uma frase.",
        ]
        if cidade:
            linhas.append(f"2. O tempo de hoje em {cidade}: use a ferramenta clima e diga a temperatura de agora, a condição, a mínima e a máxima e se vai chover.")
        else:
            linhas.append(
                "2. Pule o tempo (a cidade não está configurada) e diga, em uma frase só e só hoje, que para ter a previsão no bom dia é "
                "preciso pôr JARVIS_CIDADE no arquivo .env."
            )
        data = f"{agora:%d/%m/%Y}"
        linhas += [
            f"3. As três notícias mais importantes de tecnologia de hoje e duas ou três do mercado financeiro (Ibovespa, dólar, juros, e o que "
            f"mais estiver movimentando). Pesquise na web agora, em português, e faça de duas a quatro buscas, sempre com a data de hoje no "
            f"texto da busca, por exemplo: \"notícias tecnologia {data}\", \"tecnologia hoje {agora.day} de {_MESES[agora.month - 1]}\", \"mercado financeiro hoje "
            f"Ibovespa dólar {data}\". Prefira veículos de notícia (g1, UOL, Folha, Estadão, Valor, InfoMoney, Exame, TecMundo, Olhar Digital, "
            "Canaltech) e cite o veículo de cada uma. Só o que a pesquisa trouxe, com a data que ela mostra: se não achar nada de hoje, diga as mais "
            "recentes que achou e de que dia são (ontem, anteontem), em vez de pular; se a pesquisa falhar de vez, diga que não conseguiu e siga.",
        ]
        if agenda is not None:
            rotinas = agenda.do_dia(agora)
            if rotinas:
                lista = "; ".join(f"{r.quando:%H:%M} {r.tipo}: {r.texto}" for r in rotinas)
                linhas.append(f"4. Os compromissos de hoje, que estão marcados com você: {lista}.")
            else:
                linhas.append("4. Não há lembretes nem tarefas marcados para hoje; diga isso em poucas palavras.")
        terminados = _terminados(oficina, agora)
        if terminados:
            linhas.append("5. Os trabalhos de programação que terminaram desde ontem: " + "; ".join(terminados) + ". Diga como cada um acabou.")
        linhas += [
            f"{6 if terminados else 5}. Termine com uma frase motivadora para o dia, curta e sua, sem dizer que é motivadora.",
            "Mostre no painel (painel_mostrar) até três cartões com o que você apurou: o tempo, as notícias de tecnologia e o mercado, "
            "com os dados reais da pesquisa. Fale como quem conta as novidades a alguém que acabou de chegar.",
        ]
        return "\n".join(linhas)


def _terminados(oficina: Any, agora: datetime) -> list[str]:
    if oficina is None:
        return []
    desde = (agora - timedelta(days=1)).isoformat(timespec="seconds")
    saida = []
    try:
        for trabalho in oficina.listar(20):
            if trabalho.fim and trabalho.fim >= desde:
                saida.append(oficina.linha(trabalho))
    except Exception:  # noqa: BLE001 - a oficina não pode impedir o bom dia
        log.exception("Não consegui listar os trabalhos terminados para o bom dia")
    return saida


async def vigiar_desbloqueio(bloqueado: Callable[[], bool], ao_desbloquear: Callable[[], None], intervalo: float = 5.0) -> None:
    """Chama `ao_desbloquear` toda vez que a tela do Windows sai do bloqueio (o usuário voltou)."""
    estava = False
    while True:
        await asyncio.sleep(intervalo)
        try:
            esta = bool(bloqueado())
        except Exception:  # noqa: BLE001 - a consulta falhou: nada muda
            log.exception("Não consegui consultar o bloqueio da tela")
            continue
        if estava and not esta:
            try:
                ao_desbloquear()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao reagir ao desbloqueio da tela")
        estava = esta
