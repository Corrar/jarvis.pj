"""Rotinas: lembretes e tarefas marcados para uma hora, que o Jarvis cumpre sozinho.

Um lembrete é algo que ele diz na hora marcada ("lembre-me de ligar para o dentista às 15h").
Uma tarefa é um pedido que ele atende na hora marcada, como se o usuário o tivesse falado
naquele momento ("todo dia às 8h, abra o e-mail e me diga o que chegou"). As duas podem ser de
uma vez só ou repetidas (todo dia, nos dias úteis, em dias da semana, todo mês).

A agenda fica em um arquivo JSON na pasta de dados; o despertador (`Despertador`) roda no laço
da conversa e entrega cada rotina vencida a quem a cumpre (cli.py). O que faz a rotina ser
segura é quem pode criá-la: só a fala do usuário, em turno que não leu conteúdo de fora (a
ferramenta rotina_criar é "só em turno limpo", ver ferramentas.py). Uma página, um arquivo ou
um relatório nunca marcam nada.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LEMBRETE, TAREFA = "lembrete", "tarefa"
TIPOS = (LEMBRETE, TAREFA)
UMA_VEZ, DIARIA, DIAS_UTEIS, SEMANAL, MENSAL = "", "diaria", "dias_uteis", "semanal", "mensal"
REPETICOES = (DIARIA, DIAS_UTEIS, SEMANAL, MENSAL)
MAX_ROTINAS = 50
MAX_TEXTO = 300
MAX_FUTURO = timedelta(days=366)  # mais que um ano à frente é engano
ATRASO_MAXIMO = timedelta(hours=12)  # rotina de uma vez que venceu com o Jarvis desligado ainda vale dentro disto
ATRASO_DA_REPETIDA = timedelta(hours=3)  # repetida que venceu há mais que isto não é cumprida; fica para a próxima
DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
NOMES = {
    DIARIA: "todo dia",
    DIAS_UTEIS: "nos dias úteis",
    SEMANAL: "toda semana",
    MENSAL: "todo mês",
}


class ErroRotina(Exception):
    """Pedido inválido sobre uma rotina; a mensagem volta para o modelo ou para o usuário."""


@dataclass
class Rotina:
    id: str
    texto: str
    tipo: str  # LEMBRETE ou TAREFA
    proxima: str  # próxima vez, ISO local (sem fuso)
    criada_em: str
    repeticao: str = UMA_VEZ
    hora: str = ""  # "HH:MM" das repetidas
    dias: list[int] = field(default_factory=list)  # dias da semana (0 = segunda) das semanais
    vezes: int = 0  # quantas vezes já foi cumprida
    ultima: str = ""  # a última vez que foi cumprida

    @property
    def repetida(self) -> bool:
        return self.repeticao != UMA_VEZ

    @property
    def quando(self) -> datetime:
        return datetime.fromisoformat(self.proxima)

    @property
    def descricao(self) -> str:
        """"todo dia às 08:00", "sexta e sábado às 19:30", "em 10/10 às 15:00"."""
        quando = self.quando
        if self.repeticao == SEMANAL:
            nomes = [DIAS[d] for d in self.dias]
            return f"{' e '.join(nomes) if len(nomes) <= 2 else ', '.join(nomes)} às {self.hora}"
        if self.repetida:
            return f"{NOMES[self.repeticao]} às {self.hora}" + (f" (dia {quando.day})" if self.repeticao == MENSAL else "")
        return f"em {quando:%d/%m/%Y} às {quando:%H:%M}"

    @classmethod
    def de_json(cls, dados: dict[str, Any]) -> Rotina:
        conhecidos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dados.items() if k in conhecidos})


def _ler_hora(bruto: Any) -> tuple[int, int]:
    texto = str(bruto or "").strip().replace("h", ":")
    partes = texto.split(":")
    try:
        hora = int(partes[0])
        minuto = int(partes[1]) if len(partes) > 1 and partes[1] != "" else 0
    except (ValueError, IndexError):
        raise ErroRotina(f"Hora inválida: '{bruto}'. Use HH:MM, por exemplo 07:30.") from None
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        raise ErroRotina(f"Hora inválida: '{bruto}'. Use HH:MM, por exemplo 07:30.")
    return hora, minuto


def _ler_dias(bruto: Any) -> list[int]:
    if bruto is None:
        return []
    if not isinstance(bruto, list):
        raise ErroRotina("'dias' precisa ser uma lista de dias da semana (0 = segunda … 6 = domingo).")
    dias: list[int] = []
    for dia in bruto:
        if isinstance(dia, str) and dia.strip().lower() in DIAS:
            dia = DIAS.index(dia.strip().lower())
        if not isinstance(dia, int) or isinstance(dia, bool) or not 0 <= dia <= 6:
            raise ErroRotina(f"Dia da semana inválido: {dia!r}. Use 0 = segunda … 6 = domingo.")
        if dia not in dias:
            dias.append(dia)
    return sorted(dias)


def proxima_ocorrencia(repeticao: str, hora: tuple[int, int], dias: list[int], depois_de: datetime, dia_do_mes: int | None = None) -> datetime:
    """A primeira vez, estritamente depois de `depois_de`, em que a repetição cai."""
    candidato = depois_de.replace(hour=hora[0], minute=hora[1], second=0, microsecond=0)
    if repeticao == MENSAL:
        dia = dia_do_mes or depois_de.day
        for meses in range(0, 14):
            ano, mes = divmod(depois_de.month - 1 + meses, 12)
            ano, mes = depois_de.year + ano, mes + 1
            try:
                momento = datetime(ano, mes, dia, hora[0], hora[1])
            except ValueError:
                continue  # mês sem esse dia (31, 30, 29): pula
            if momento > depois_de:
                return momento
        raise ErroRotina("Não achei um mês com esse dia.")
    for passo in range(0, 8):
        momento = candidato + timedelta(days=passo)
        if momento <= depois_de:
            continue
        if repeticao == DIAS_UTEIS and momento.weekday() >= 5:
            continue
        if repeticao == SEMANAL and momento.weekday() not in dias:
            continue
        return momento
    raise ErroRotina("Não achei o próximo dia da repetição.")


class Agenda:
    """As rotinas, guardadas em um arquivo JSON."""

    def __init__(self, arquivo: Path, relogio: Callable[[], datetime] | None = None, ao_mudar: Callable[[], None] | None = None) -> None:
        self.arquivo = arquivo
        self._relogio = relogio or datetime.now
        self.ao_mudar = ao_mudar
        self._rotinas: dict[str, Rotina] = {}
        self._carregar()

    # --- arquivo ----------------------------------------------------------------------

    def _carregar(self) -> None:
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.exception("Não consegui ler as rotinas em %s; começo sem nenhuma", self.arquivo)
            return
        for item in dados.get("rotinas", []) if isinstance(dados, dict) else []:
            try:
                rotina = Rotina.de_json(item)
                rotina.quando  # noqa: B018 - confere a data
                if rotina.tipo in TIPOS and rotina.repeticao in (UMA_VEZ, *REPETICOES):
                    self._rotinas[rotina.id] = rotina
            except (TypeError, ValueError):
                log.warning("Rotina ignorada por estar mal formada: %r", item)

    def _gravar(self) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        conteudo = json.dumps({"rotinas": [asdict(r) for r in self.listar()]}, ensure_ascii=False, indent=1)
        provisorio = self.arquivo.with_suffix(".json.novo")
        provisorio.write_text(conteudo, encoding="utf-8")
        provisorio.replace(self.arquivo)
        if self.ao_mudar is not None:
            try:
                self.ao_mudar()
            except Exception:  # noqa: BLE001 - quem ouve (o painel) não pode atrapalhar a agenda
                log.exception("Falha ao avisar a mudança nas rotinas")

    # --- consulta ---------------------------------------------------------------------

    def listar(self) -> list[Rotina]:
        return sorted(self._rotinas.values(), key=lambda r: (r.proxima, r.criada_em))

    def achar(self, referencia: Any) -> Rotina:
        chave = str(referencia or "").strip().lower()
        if not chave:
            raise ErroRotina("Diga qual rotina: o código dela ou uma palavra do texto.")
        if chave in self._rotinas:
            return self._rotinas[chave]
        parecidas = [r for r in self.listar() if chave in r.texto.lower()]
        if len(parecidas) == 1:
            return parecidas[0]
        if not parecidas:
            raise ErroRotina(f"Não há rotina com o código ou o texto '{referencia}'.")
        raise ErroRotina("Mais de uma rotina combina: " + "; ".join(f"{r.id} ({r.texto[:40]})" for r in parecidas[:5]) + ". Diga o código.")

    def proxima(self) -> datetime | None:
        rotinas = self.listar()
        return rotinas[0].quando if rotinas else None

    def do_dia(self, agora: datetime | None = None) -> list[Rotina]:
        """As rotinas que ainda vão vencer hoje."""
        agora = agora or self._relogio()
        fim = agora.replace(hour=23, minute=59, second=59, microsecond=0)
        return [r for r in self.listar() if agora <= r.quando <= fim]

    def texto(self, agora: datetime | None = None) -> str:
        """A lista, para o modelo e para o terminal."""
        rotinas = self.listar()
        if not rotinas:
            return "Nenhuma rotina marcada."
        agora = agora or self._relogio()
        linhas = []
        for r in rotinas:
            quando = r.quando
            dia = "hoje" if quando.date() == agora.date() else "amanhã" if quando.date() == (agora + timedelta(days=1)).date() else f"{quando:%d/%m}"
            linhas.append(f"{r.id} · {r.tipo} · {r.descricao} (próxima: {dia} {quando:%H:%M}) · {r.texto}")
        return "\n".join(linhas)

    # --- mudar ------------------------------------------------------------------------

    def criar(
        self,
        texto: Any,
        tipo: Any = LEMBRETE,
        em: Any = None,
        hora: Any = None,
        repeticao: Any = UMA_VEZ,
        dias: Any = None,
    ) -> Rotina:
        """Marca uma rotina. `em` (ISO, data e hora) para uma vez; `hora` + `repeticao` para repetidas."""
        texto = " ".join(str(texto or "").split())
        if not texto:
            raise ErroRotina("Faltou o texto da rotina: o que lembrar ou o que fazer.")
        if len(texto) > MAX_TEXTO:
            raise ErroRotina(f"O texto da rotina pode ter até {MAX_TEXTO} letras.")
        tipo = str(tipo or LEMBRETE).strip().lower()
        if tipo not in TIPOS:
            raise ErroRotina(f"Tipo inválido: '{tipo}'. Use {LEMBRETE} ou {TAREFA}.")
        repeticao = str(repeticao or UMA_VEZ).strip().lower()
        if repeticao not in (UMA_VEZ, *REPETICOES):
            raise ErroRotina(f"Repetição inválida: '{repeticao}'. Use {', '.join(REPETICOES)} ou deixe vazio para uma vez só.")
        if len(self._rotinas) >= MAX_ROTINAS:
            raise ErroRotina(f"Já há {MAX_ROTINAS} rotinas marcadas; apague alguma antes de marcar outra.")
        agora = self._relogio().replace(second=0, microsecond=0)
        lista_de_dias = _ler_dias(dias)
        if repeticao == UMA_VEZ:
            if not em:
                raise ErroRotina("Para uma rotina de uma vez só, informe 'em' com a data e a hora (por exemplo 2026-10-09T15:30).")
            try:
                quando = datetime.fromisoformat(str(em).strip()).replace(second=0, microsecond=0, tzinfo=None)
            except ValueError:
                raise ErroRotina(f"Data inválida em 'em': '{em}'. Use o formato 2026-10-09T15:30 (hora local).") from None
            if quando <= agora:
                raise ErroRotina(f"Essa hora já passou (agora é {agora:%d/%m/%Y %H:%M}). Marque uma hora futura.")
            if quando - agora > MAX_FUTURO:
                raise ErroRotina("Mais de um ano à frente: confira a data.")
            hora_texto = f"{quando:%H:%M}"
        else:
            if hora in (None, ""):
                raise ErroRotina("Para uma rotina repetida, informe 'hora' (HH:MM).")
            h = _ler_hora(hora)
            if repeticao == SEMANAL and not lista_de_dias:
                raise ErroRotina("Para a repetição semanal, informe 'dias' (0 = segunda … 6 = domingo).")
            if repeticao != SEMANAL:
                lista_de_dias = []
            dia_do_mes = None
            if repeticao == MENSAL:
                try:
                    dia_do_mes = datetime.fromisoformat(str(em).strip()).day if em else agora.day
                except ValueError:
                    raise ErroRotina(f"Data inválida em 'em': '{em}'.") from None
            quando = proxima_ocorrencia(repeticao, h, lista_de_dias, agora, dia_do_mes)
            hora_texto = f"{h[0]:02d}:{h[1]:02d}"
        codigo = secrets.token_hex(2)
        while codigo in self._rotinas:
            codigo = secrets.token_hex(2)
        rotina = Rotina(
            id=codigo,
            texto=texto,
            tipo=tipo,
            proxima=quando.isoformat(timespec="minutes"),
            criada_em=agora.isoformat(timespec="minutes"),
            repeticao=repeticao,
            hora=hora_texto,
            dias=lista_de_dias,
        )
        self._rotinas[codigo] = rotina
        self._gravar()
        return rotina

    def apagar(self, referencia: Any) -> Rotina:
        rotina = self.achar(referencia)
        del self._rotinas[rotina.id]
        self._gravar()
        return rotina

    def vencidas(self, agora: datetime | None = None) -> list[tuple[Rotina, timedelta]]:
        """As rotinas cuja hora chegou, com o atraso de cada uma, já adiantadas para a próxima vez.

        Cada rotina sai daqui uma vez só por vencimento: a de uma vez é apagada; a repetida vai
        para a próxima ocorrência. Quem chama decide o que fazer com o atraso (ver `cli.py`).
        """
        agora = agora or self._relogio()
        saida: list[tuple[Rotina, timedelta]] = []
        mudou = False
        for rotina in self.listar():
            quando = rotina.quando
            if quando > agora:
                break
            copia = Rotina.de_json(asdict(rotina))
            saida.append((copia, agora - quando))
            rotina.vezes += 1
            rotina.ultima = quando.isoformat(timespec="minutes")
            if rotina.repetida:
                try:
                    dia_do_mes = quando.day if rotina.repeticao == MENSAL else None
                    rotina.proxima = proxima_ocorrencia(rotina.repeticao, _ler_hora(rotina.hora), rotina.dias, agora, dia_do_mes).isoformat(timespec="minutes")
                except ErroRotina:
                    log.warning("A rotina %s não tem próxima ocorrência; apagada", rotina.id)
                    del self._rotinas[rotina.id]
            else:
                del self._rotinas[rotina.id]
            mudou = True
        if mudou:
            self._gravar()
        return saida


class Despertador:
    """Acorda a cada tanto, pega as rotinas vencidas e as entrega a `cumprir` (no laço da conversa)."""

    def __init__(self, agenda: Agenda, cumprir: Callable[[Rotina, timedelta], None], relogio: Callable[[], datetime] | None = None) -> None:
        self._agenda, self._cumprir, self._relogio = agenda, cumprir, relogio or datetime.now

    def conferir(self) -> int:
        """Entrega o que venceu; devolve quantas rotinas foram entregues."""
        try:
            vencidas = self._agenda.vencidas(self._relogio())
        except Exception:  # noqa: BLE001 - a agenda não pode derrubar a conversa
            log.exception("Falha ao conferir as rotinas vencidas")
            return 0
        for rotina, atraso in vencidas:
            try:
                self._cumprir(rotina, atraso)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao entregar a rotina %s", rotina.id)
        return len(vencidas)

    async def rodar(self, intervalo: float = 20.0) -> None:
        while True:
            await asyncio.sleep(intervalo)
            self.conferir()
