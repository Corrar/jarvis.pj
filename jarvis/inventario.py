"""Inventário diário Fluxo → Protheus: ler o Fluxo, montar a lista e guardar o dia.

Esta parte **não toca na tela do Protheus** e não grava nada em lugar nenhum além da pasta do dia.
A digitação na MATA270 (etapa C da especificação) depende da calibração e é recusada aqui, de
propósito: ver `Digitacao.recusa()`.

Decisões que valem registro:

- **Só duas rotas do Fluxo**, fixas no código: `POST /auth/login` e `GET /stock`. O modelo não passa
  endereço, não escolhe método e não acrescenta parâmetro — mesmo princípio da leitura do Protheus.
- **Sem seguir desvio.** O login leva usuário e senha no corpo; seguir um 3xx mandaria a credencial
  para onde o servidor quisesse.
- **O token não fica guardado.** Vale um dia, e um login por execução custa menos que um segredo
  parado em disco.
- **A senha nunca aparece** em resultado, relatório ou log.
- **Uma execução por dia**, gravada em disco: reiniciar o Jarvis não devolve o direito de rodar.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jarvis.protheus import MASCARA, _SemDesvio, mascarar_texto

log = logging.getLogger(__name__)

PASTA = "Inventario-Fluxo"
ARMAZEM = "02"  # ALMOX; o Fluxo só devolve este, e é o que o Protheus chama de ALMOXARIFADO
FILIAL = "0101"
PREFIXO_DO_DOCUMENTO = "FLX"
MAX_ITENS = 300
TEMPO_MAX = 60.0
MAX_RESPOSTA = 8 << 20  # bytes da resposta do estoque
MAX_ITENS_DURO = 5000  # teto absoluto, acima do configurável: resposta maior que isso é defeito
DIAS = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
MINUTOS_PARA_APROVAR = 60
MODOS = ("desligado", "homologacao", "producao")
# O ambiente que cada modo exige ver na tela antes de digitar (§7, regra 1 da especificação).
AMBIENTE_DO_MODO = {"homologacao": "royale191155", "producao": "royale191154"}
_SKU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,29}$")

# Sem seguir desvio, e sem file://, ftp:// e data:// — o mesmo abridor da leitura do Protheus.
_ABRIDOR = urllib.request.build_opener(_SemDesvio)
_ABRIDOR.handle_open.pop("file", None)
_ABRIDOR.handle_open.pop("ftp", None)
_ABRIDOR.handle_open.pop("data", None)
_ABRIDOR.addheaders = []


class ErroInventario(Exception):
    """Qualquer coisa que impeça o inventário do dia. A mensagem é para o Bruno ler."""


@dataclass(frozen=True)
class Item:
    """Uma linha do estoque do Fluxo, já conferida."""

    codigo: str
    quantidade: Decimal
    nome: str = ""
    unidade: str = ""


@dataclass(frozen=True)
class Linha:
    """Uma linha da SB7, como vai para o CSV e (um dia) para a tela."""

    filial: str
    codigo: str
    local: str
    documento: str
    quantidade: Decimal
    data: date
    nome: str = ""
    unidade: str = ""

    def como_csv(self) -> str:
        quantidade = f"{self.quantidade:f}".replace(".", ",")
        return ";".join(
            (self.filial, self.codigo, self.local, self.documento, quantidade,
             self.data.strftime("%d/%m/%Y"), self.nome, self.unidade)
        )  # fmt: skip


CABECALHO_CSV = "B7_FILIAL;B7_COD;B7_LOCAL;B7_DOC;B7_QUANT;B7_DATA;NOME;UNIDADE"


def documento_do_dia(dia: date, prefixo: str = PREFIXO_DO_DOCUMENTO) -> str:
    """`FLX` + AAMMDD. O tamanho do campo B7_DOC ainda precisa ser confirmado na calibração."""
    return f"{prefixo}{dia:%y%m%d}"


def ler_dias(bruto: str) -> tuple[str, ...]:
    """Os dias da semana em que o inventário roda."""
    pedidos = [d.strip().lower()[:3] for d in re.split(r"[,;\s]+", bruto or "") if d.strip()]
    for dia in pedidos:
        if dia not in DIAS:
            raise ValueError(f"JARVIS_INV_DIAS: '{dia}' não é dia da semana. Use: {', '.join(DIAS)}.")
    return tuple(dict.fromkeys(pedidos)) or DIAS[:5]


def ler_hora(bruto: str) -> tuple[int, int]:
    achado = re.fullmatch(r"\s*([01]?\d|2[0-3])\s*:\s*([0-5]\d)\s*", bruto or "")
    if not achado:
        raise ValueError(f"JARVIS_INV_HORA precisa ser HH:MM entre 00:00 e 23:59, recebi: {bruto}")
    return int(achado.group(1)), int(achado.group(2))


def e_dia_de_rodar(quando: datetime, dias: Sequence[str]) -> bool:
    return DIAS[quando.weekday()] in dias


# ----------------------------------------------------------------------------- ler o Fluxo


def _pedir(url: str, metodo: str, cabecalhos: dict[str, str], corpo: bytes | None = None, espera: float = TEMPO_MAX) -> tuple[int, bytes]:
    requisicao = urllib.request.Request(url, method=metodo, headers=cabecalhos, data=corpo)  # noqa: S310 - rota fixa
    try:
        with _ABRIDOR.open(requisicao, timeout=espera) as resposta:
            return resposta.status or 0, resposta.read(MAX_RESPOSTA)
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read(MAX_RESPOSTA)


class Fluxo:
    """A leitura do estoque do Fluxo Royale. Duas rotas, fixas; nada mais sai daqui."""

    def __init__(
        self,
        endereco: str,
        usuario: str,
        senha: str,
        pedir: Callable[..., tuple[int, bytes]] = _pedir,
    ) -> None:
        self.endereco = str(endereco or "").rstrip("/")
        if self.endereco and urllib.parse.urlsplit(self.endereco).scheme not in ("http", "https"):
            raise ValueError(f"JARVIS_FLUXO_URL tem de ser http ou https, recebi: {endereco}")
        self._usuario, self._senha = usuario, senha
        self._pedir = pedir

    @property
    def pronto(self) -> bool:
        return bool(self.endereco and self._usuario and self._senha)

    def falta(self) -> str:
        if not self.endereco:
            return "falta JARVIS_FLUXO_URL no .env"
        if not self._usuario or not self._senha:
            return "falta JARVIS_FLUXO_USER e JARVIS_FLUXO_PASS no .env"
        return ""

    def _limpar(self, texto: str) -> str:
        """A senha e o token nunca saem daqui para mensagem nenhuma."""
        for segredo in (self._senha, self._usuario):
            if segredo and len(segredo) >= 4:
                texto = texto.replace(segredo, MASCARA)
        return mascarar_texto(texto)[:300]

    def _entrar(self) -> str:
        corpo = json.dumps({"email": self._usuario, "password": self._senha}).encode()
        codigo, resposta = self._pedir(
            f"{self.endereco}/auth/login", "POST",
            {"Content-Type": "application/json", "Accept": "application/json"}, corpo, TEMPO_MAX,
        )  # fmt: skip
        if 300 <= codigo < 400:
            raise ErroInventario("o login do Fluxo respondeu um desvio, e eu não sigo desvio: a senha iria para outro endereço. Confira JARVIS_FLUXO_URL.")
        if codigo != 200:
            raise ErroInventario(f"o login no Fluxo falhou (HTTP {codigo}): {self._limpar(resposta.decode('utf-8', 'replace'))}")
        try:
            dados = json.loads(resposta)
        except ValueError:
            raise ErroInventario("o login no Fluxo respondeu algo que não é JSON.") from None
        token = dados.get("token") or dados.get("access_token") or (dados.get("data") or {}).get("token")
        if not isinstance(token, str) or not token.strip():
            raise ErroInventario("o login no Fluxo não devolveu token. A rota mudou? Confira com quem cuida do Fluxo.")
        return token.strip()

    def estoque(self) -> list[Item]:
        """Login e `GET /stock`. Devolve uma linha por produto do ALMOX, já conferida."""
        if not self.pronto:
            raise ErroInventario(f"Não consigo ler o Fluxo: {self.falta()}.")
        token = self._entrar()  # não é guardado: vale um dia, e um login por execução custa menos
        codigo, resposta = self._pedir(
            f"{self.endereco}/stock", "GET",
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}, None, TEMPO_MAX,
        )  # fmt: skip
        if 300 <= codigo < 400:
            raise ErroInventario("a leitura do estoque respondeu um desvio, e eu não sigo desvio. Confira JARVIS_FLUXO_URL.")
        if codigo != 200:
            raise ErroInventario(f"a leitura do estoque do Fluxo falhou (HTTP {codigo}): {self._limpar(resposta.decode('utf-8', 'replace'))}")
        try:
            dados = json.loads(resposta)
        except ValueError:
            raise ErroInventario("o estoque do Fluxo veio em um formato que não é JSON.") from None
        return conferir_estoque(dados)


def _quantidade(bruto: Any, onde: str) -> Decimal:
    if isinstance(bruto, bool) or bruto is None:
        raise ErroInventario(f"{onde}: a quantidade veio como {bruto!r}, que não é número.")
    try:
        valor = Decimal(str(bruto).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ErroInventario(f"{onde}: a quantidade '{str(bruto)[:20]}' não é número.") from None
    if not valor.is_finite():
        raise ErroInventario(f"{onde}: a quantidade não é um número finito.")
    if valor < 0:
        raise ErroInventario(f"{onde}: a quantidade está negativa ({valor}). Inventário não leva saldo negativo; confira no Fluxo antes.")
    return valor


def conferir_estoque(dados: Any) -> list[Item]:
    """As barreiras da etapa A, sobre o que o Fluxo devolveu."""
    linhas = dados.get("data") if isinstance(dados, dict) else dados
    if isinstance(linhas, dict):
        linhas = linhas.get("stock") or linhas.get("items") or linhas.get("results")
    if not isinstance(linhas, list):
        raise ErroInventario("o estoque do Fluxo não veio como lista. A rota mudou? Confira com quem cuida do Fluxo.")
    if not linhas:
        raise ErroInventario("o Fluxo devolveu o estoque vazio. Não digito inventário vazio: confira o Fluxo.")
    if len(linhas) > MAX_ITENS_DURO:
        raise ErroInventario(f"o Fluxo devolveu {len(linhas)} linhas, acima do teto absoluto de {MAX_ITENS_DURO}. Isso é defeito, não inventário.")
    itens: list[Item] = []
    vistos: dict[str, int] = {}
    for numero, bruta in enumerate(linhas, 1):
        if not isinstance(bruta, dict):
            raise ErroInventario(f"linha {numero} do estoque não é um registro.")
        produto = bruta.get("products") if isinstance(bruta.get("products"), dict) else bruta
        codigo = str(produto.get("sku") or "").strip()
        if not codigo:
            raise ErroInventario(f"linha {numero} do estoque está sem SKU. Corrija no Fluxo antes de inventariar.")
        if not _SKU.match(codigo):
            raise ErroInventario(f"linha {numero}: o SKU '{codigo[:30]}' tem caractere que não vou digitar numa tela de produção.")
        if codigo.upper() in vistos:
            raise ErroInventario(f"o SKU {codigo} aparece duas vezes (linhas {vistos[codigo.upper()]} e {numero}). Corrija no Fluxo: inventário não leva produto repetido.")
        vistos[codigo.upper()] = numero
        itens.append(
            Item(
                codigo=codigo,
                quantidade=_quantidade(bruta.get("quantity_on_hand"), f"linha {numero} ({codigo})"),
                nome=str(produto.get("name") or "").strip()[:80],
                unidade=str(produto.get("unit") or "").strip()[:10],
            )
        )
    return itens


def montar(itens: Sequence[Item], dia: date, armazem: str = ARMAZEM, filial: str = FILIAL, de_para: dict[str, str] | None = None) -> list[Linha]:
    """Do estoque do Fluxo para as linhas da SB7. `de_para` é o SKU → B1_COD, quando houver (P-5)."""
    documento = documento_do_dia(dia)
    saida = []
    for item in itens:
        codigo = (de_para or {}).get(item.codigo, item.codigo)
        if not _SKU.match(codigo):
            raise ErroInventario(f"o de-para transformou {item.codigo} em '{codigo[:30]}', que eu não digito numa tela de produção.")
        saida.append(
            Linha(filial=filial, codigo=codigo, local=armazem, documento=documento,
                  quantidade=item.quantidade, data=dia, nome=item.nome, unidade=item.unidade)
        )  # fmt: skip
    return saida


def conferir_tamanho(linhas: Sequence[Linha], maximo: int) -> None:
    if len(linhas) > maximo:
        raise ErroInventario(
            f"a lista tem {len(linhas)} itens, acima do teto de {maximo} por execução (JARVIS_INV_MAX_ITENS). "
            "Pare e confira: ou o teto está baixo demais, ou o Fluxo trouxe coisa que não devia."
        )


def resumir(linhas: Sequence[Linha]) -> str:
    zeros = sum(1 for linha in linhas if linha.quantidade == 0)
    total = sum((linha.quantidade for linha in linhas), Decimal(0))
    documento = linhas[0].documento if linhas else "—"
    return (
        f"{len(linhas)} itens, armazém {linhas[0].local if linhas else ARMAZEM}, documento {documento}. "
        f"Zeros: {zeros}. Soma das quantidades: {total:f}."
    )


def gravar_csv(linhas: Sequence[Linha], pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / "lista.csv"
    arquivo.write_text("\n".join([CABECALHO_CSV, *(linha.como_csv() for linha in linhas)]) + "\n", encoding="utf-8-sig")
    return arquivo


# ------------------------------------------------------------------- o dia, e a aprovação


@dataclass
class Dia:
    """O que aconteceu hoje. Fica em disco: reiniciar o Jarvis não devolve o direito de rodar."""

    data: str
    estado: str = "montado"  # montado · aprovado · nao_aprovado · digitando · concluido · parado
    itens: int = 0
    documento: str = ""
    confirmados: int = 0
    motivo: str = ""
    pedido_em: str = ""
    passos: list[str] = field(default_factory=list)

    def como_json(self) -> dict[str, Any]:
        return {
            "data": self.data, "estado": self.estado, "itens": self.itens, "documento": self.documento,
            "confirmados": self.confirmados, "motivo": self.motivo, "pedido_em": self.pedido_em,
            "passos": self.passos[-200:],
        }  # fmt: skip


class Registro:
    """O arquivo do dia, em `<pasta do dia>/execucao.json`."""

    def __init__(self, raiz: Path, hoje: Callable[[], date] = date.today) -> None:
        self.raiz, self._hoje = raiz, hoje

    def pasta_do_dia(self, dia: date | None = None) -> Path:
        return self.raiz / PASTA / (dia or self._hoje()).isoformat()

    def _arquivo(self, dia: date | None = None) -> Path:
        return self.pasta_do_dia(dia) / "execucao.json"

    def ler(self, dia: date | None = None) -> Dia | None:
        try:
            dados = json.loads(self._arquivo(dia).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(dados, dict) or not dados.get("data"):
            return None
        return Dia(**{campo: dados.get(campo, padrao) for campo, padrao in (
            ("data", ""), ("estado", "montado"), ("itens", 0), ("documento", ""),
            ("confirmados", 0), ("motivo", ""), ("pedido_em", ""), ("passos", []),
        )})  # fmt: skip

    def gravar(self, dia: Dia) -> None:
        arquivo = self._arquivo(date.fromisoformat(dia.data))
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(dia.como_json(), ensure_ascii=False, indent=1), encoding="utf-8")

    def ja_rodou_hoje(self) -> Dia | None:
        """A execução de hoje, se já terminou (concluída ou parada). `None` se ainda cabe rodar."""
        dia = self.ler()
        return dia if dia is not None and dia.estado in ("concluido", "parado", "nao_aprovado") else None


class Aprovacao:
    """O 'sim' do Bruno para a lista de hoje, com prazo.

    O 'sim' é **digitado ou clicado**, nunca só falado. Fala não confirma gravação em produção: a
    televisão, um colega na sala ou o próprio alto-falante do Jarvis produzem um "sim", e um `s`
    digitado ou um clique no painel não acontecem sozinhos. O Jarvis **pergunta** pela voz; a
    resposta é que precisa vir pela mão. (Mesma regra das ações do terminal.)
    """

    def __init__(self, minutos: int = MINUTOS_PARA_APROVAR, agora: Callable[[], datetime] = datetime.now) -> None:
        self.minutos, self._agora = minutos, agora
        self.pedida_em: datetime | None = None
        self.impressao = ""  # a lista aprovada; se mudar, a aprovação cai

    def pedir(self, impressao: str) -> None:
        self.pedida_em, self.impressao = self._agora(), impressao

    def expirou(self) -> bool:
        return self.pedida_em is None or (self._agora() - self.pedida_em).total_seconds() > self.minutos * 60

    def vale_para(self, impressao: str) -> tuple[bool, str]:
        """A aprovação pendente ainda vale para esta lista?"""
        if self.pedida_em is None:
            return False, "não pedi aprovação para hoje ainda"
        if self.expirou():
            return False, f"a aprovação venceu (o prazo é de {self.minutos} minutos)"
        if impressao != self.impressao:
            return False, "a lista mudou depois que eu pedi; peço de novo"
        return True, ""


def impressao_da_lista(linhas: Sequence[Linha]) -> str:
    """Um resumo curto que muda se qualquer item mudar: é o que amarra a aprovação à lista."""
    import hashlib

    corpo = "\n".join(linha.como_csv() for linha in linhas)
    return hashlib.sha256(corpo.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------------- etapa C


class Digitacao:
    """A digitação na MATA270. Ainda não existe, e recusa dizendo exatamente o que falta.

    Não é cautela de fachada: sem o roteiro calibrado não há o que digitar. Os nomes e a ordem dos
    campos, o caminho do menu, quais campos são obrigatórios, se cada item grava ao confirmar e
    quais mensagens são normais — nada disso dá para adivinhar de fora, e errar aqui escreve
    lançamento torto no inventário de produção.
    """

    FALTA = (
        "o roteiro calibrado da tela (roteiros/mata270.yaml)",
        "a resposta sobre SmartClient instalado ou navegador, que muda como eu leio a tela",
        "o usuário do Protheus que eu devo usar (P-3)",
        "o de-para SKU → B1_COD, ou a confirmação de que o B7_COD aceita o SKU do Fluxo (P-5)",
    )

    def __init__(self, modo: str = "desligado", roteiro: Path | None = None) -> None:
        self.modo, self.roteiro = modo, roteiro

    @property
    def pronta(self) -> bool:
        return bool(self.roteiro and self.roteiro.exists())

    def recusa(self) -> str:
        return (
            "Não digito nada no Protheus ainda. Falta: "
            + "; ".join(self.FALTA)
            + ". A lista do dia está pronta e conferida na pasta do dia — dá para importar ou digitar à mão por ela."
        )

    def executar(self, linhas: Sequence[Linha]) -> None:
        raise ErroInventario(self.recusa())


# -------------------------------------------------------------------- o relógio e o relatório


class Vigia:
    """Acorda de minuto em minuto e dispara o inventário na hora marcada, uma vez por dia."""

    def __init__(
        self,
        hora: tuple[int, int],
        dias: Sequence[str],
        disparar: Callable[[], None],
        ja_rodou: Callable[[], bool],
        agora: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.hora, self.dias = hora, tuple(dias)
        self._disparar, self._ja_rodou, self._agora = disparar, ja_rodou, agora
        self._ultimo = ""  # a data em que já disparou, para não disparar duas vezes no mesmo minuto

    def hora_de_rodar(self, quando: datetime | None = None) -> bool:
        quando = quando or self._agora()
        if quando.date().isoformat() == self._ultimo or not e_dia_de_rodar(quando, self.dias):
            return False
        # Vale a partir da hora marcada, não só no minuto exato: se a máquina estava desligada
        # às 17:00 e o Jarvis subiu às 17:20, o inventário do dia ainda acontece.
        return (quando.hour, quando.minute) >= self.hora and not self._ja_rodou()

    def conferir(self) -> bool:
        if not self.hora_de_rodar():
            return False
        self._ultimo = self._agora().date().isoformat()
        self._disparar()
        return True

    async def rodar(self, intervalo: float = 30.0) -> None:
        import asyncio

        while True:
            try:
                self.conferir()
            except Exception:  # noqa: BLE001 - o relógio não pode morrer por causa de uma execução
                log.exception("Falha ao disparar o inventário do dia")
            await asyncio.sleep(intervalo)


def relatorio(dia: Dia, linhas: Sequence[Linha], modo: str, pasta: Path) -> Path:
    """O relatório do dia, em Markdown, na pasta do dia."""
    agora = datetime.now()
    partes = [
        f"# Inventário Fluxo → Protheus — {dia.data}",
        "",
        f"- Modo: **{modo}**",
        f"- Documento: **{dia.documento or '—'}**",
        f"- Itens na lista: **{dia.itens}**",
        f"- Confirmados no Protheus: **{dia.confirmados}**",
        f"- Estado: **{dia.estado}**",
    ]
    if dia.motivo:
        partes.append(f"- Motivo: {dia.motivo}")
    partes += ["", "## Lembrete", "", "O acerto de inventário (MATA340) **não foi feito**: isso é com o fiscal ou o almoxarifado, na mão.", ""]
    if dia.passos:
        partes += ["## Passos", "", *(f"- {passo}" for passo in dia.passos[-60:]), ""]
    if linhas:
        partes += ["## Primeiros itens", "", "| Produto | Quantidade | Armazém |", "|---|---|---|"]
        partes += [f"| {l.codigo} | {l.quantidade:f} | {l.local} |" for l in linhas[:20]]
        if len(linhas) > 20:
            partes.append(f"| … | mais {len(linhas) - 20} itens no lista.csv | |")
        partes.append("")
    partes.append(f"_Gerado em {agora:%d/%m/%Y %H:%M}._")
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / "relatorio.md"
    arquivo.write_text("\n".join(partes), encoding="utf-8")
    return arquivo


class Inventario:
    """Junta as peças: lê o Fluxo, monta, grava a pasta do dia e segura o dia em disco."""

    def __init__(
        self,
        fluxo: Fluxo,
        registro: Registro,
        modo: str = "desligado",
        maximo: int = MAX_ITENS,
        digitacao: Digitacao | None = None,
        hoje: Callable[[], date] = date.today,
    ) -> None:
        self.fluxo, self.registro, self.modo, self.maximo = fluxo, registro, modo, maximo
        self.digitacao = digitacao or Digitacao(modo)
        self._hoje = hoje
        self.aprovacao = Aprovacao()
        self.linhas: list[Linha] = []

    @property
    def ligado(self) -> bool:
        return self.modo != "desligado"

    def montar_o_dia(self) -> Dia:
        """Etapa A inteira: lê, confere, monta, grava a pasta do dia. Não encosta na tela."""
        hoje = self._hoje()
        ja = self.registro.ja_rodou_hoje()
        if ja is not None:
            raise ErroInventario(f"o inventário de hoje já foi {ja.estado} ({ja.itens} itens, documento {ja.documento}). Não rodo de novo no mesmo dia.")
        itens = self.fluxo.estoque()
        self.linhas = montar(itens, hoje)
        conferir_tamanho(self.linhas, self.maximo)
        pasta = self.registro.pasta_do_dia(hoje)
        gravar_csv(self.linhas, pasta)
        dia = Dia(data=hoje.isoformat(), estado="montado", itens=len(self.linhas),
                  documento=self.linhas[0].documento if self.linhas else "")  # fmt: skip
        dia.passos.append(f"{datetime.now():%H:%M} lista montada: {resumir(self.linhas)}")
        self.registro.gravar(dia)
        return dia

    def pergunta(self, dia: Dia) -> str:
        """O que o Jarvis fala e escreve às 17:00."""
        onde = "homologação" if self.modo == "homologacao" else "produção"
        base = f"Inventário de hoje pronto: {resumir(self.linhas)}"
        if not self.digitacao.pronta:
            return f"{base} A lista está na pasta do dia. {self.digitacao.recusa()}"
        self.aprovacao.pedir(impressao_da_lista(self.linhas))
        dia.pedido_em = datetime.now().isoformat(timespec="seconds")
        self.registro.gravar(dia)
        return f"{base} Posso digitar no Protheus de {onde}? Responda com /inventario aprovar nos próximos {self.aprovacao.minutos} minutos."

    def aprovar(self) -> str:
        """O Bruno digitou a aprovação. Só aqui a digitação pode começar."""
        dia = self.registro.ler()
        if dia is None or not self.linhas:
            raise ErroInventario("não há lista montada para hoje. Rode /inventario primeiro.")
        vale, por_que = self.aprovacao.vale_para(impressao_da_lista(self.linhas))
        if not vale:
            raise ErroInventario(f"não posso usar essa aprovação: {por_que}.")
        dia.estado = "aprovado"
        dia.passos.append(f"{datetime.now():%H:%M} aprovado pelo Bruno")
        self.registro.gravar(dia)
        try:
            self.digitacao.executar(self.linhas)
        except ErroInventario as erro:
            dia.estado, dia.motivo = "parado", str(erro)
            dia.passos.append(f"{datetime.now():%H:%M} parou: {erro}")
            self.registro.gravar(dia)
            relatorio(dia, self.linhas, self.modo, self.registro.pasta_do_dia())
            raise
        dia.estado, dia.confirmados = "concluido", len(self.linhas)
        self.registro.gravar(dia)
        relatorio(dia, self.linhas, self.modo, self.registro.pasta_do_dia())
        return f"Inventário concluído: {dia.confirmados} de {dia.itens} itens."

    def situacao(self) -> str:
        if not self.ligado:
            return "Inventário diário desligado (JARVIS_INV_MODO=desligado no .env)."
        if not self.fluxo.pronto:
            return f"Inventário diário em {self.modo}, mas não consigo ler o Fluxo: {self.fluxo.falta()}."
        dia = self.registro.ler()
        onde = self.registro.pasta_do_dia()
        agora = f"Hoje: {dia.estado}, {dia.itens} itens, documento {dia.documento}." if dia else "Hoje: ainda não montei a lista."
        falta = "" if self.digitacao.pronta else f" {self.digitacao.recusa()}"
        return f"Inventário diário em {self.modo}, armazém {ARMAZEM}, teto de {self.maximo} itens. {agora} Pasta: {onde}.{falta}"
