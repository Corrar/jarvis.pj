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
ARQUIVO_DE_EXCLUSOES = "inventario-exclusoes.json"
PLANILHA = "Plan1"
CABECALHO_XLSX = ("Produto", "Armazem", "Documento", "Quantidade", "Data")
MAX_LINHAS_NA_SEMANA = 9999  # a sequência do documento tem 4 dígitos
# Os valores que o Bruno decidiu na rodada manual de 24 a 28/10/2026. O arquivo em disco manda.
EXCLUSOES_PADRAO = {
    "etiquetas": ["MIN-TAI", "INFORMATICA", "FEIRA", "VIAGEM", "FERRAMENTAS", "INSUMOS", "DESPESAS", "CAMISETA"],
    "prefixos_sku": ["FIL-PET"],
    "codigos_inativos_protheus": ["3.05.0115", "3.08.0113", "3.09.0338", "5.01.0250"],
    "codigos_sem_cadastro_protheus": [],
}
# Onde a etiqueta pode estar na resposta do Fluxo. Não sei o nome que ela tem lá, então procuro em
# todos e, se não achar nenhum com a lista de etiquetas ligada, PARO: aplicar zero exclusão em
# silêncio poria no inventário de produção justamente o que o Bruno mandou tirar.
CAMPOS_DE_ETIQUETA = ("tags", "labels", "etiquetas", "categories", "categorias", "product_tags", "grupos")
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


def normalizar(texto: Any) -> str:
    """Para comparar etiqueta: sem acento, sem maiúscula, sem espaço nas pontas (INFORMÁTICA = informatica)."""
    import unicodedata

    cru = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return cru.strip().upper()


@dataclass(frozen=True)
class Item:
    """Uma linha do estoque do Fluxo, já conferida."""

    codigo: str
    quantidade: Decimal
    nome: str = ""
    unidade: str = ""
    etiquetas: tuple[str, ...] = ()


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
    return tuple(dict.fromkeys(pedidos)) or ("sex",)  # semanal, na sexta


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


def _etiquetas(produto: dict[str, Any], linha: dict[str, Any]) -> tuple[str, ...]:
    """As etiquetas do produto, venham no nome que vierem, como texto comparável."""
    achadas: list[str] = []
    for fonte in (produto, linha):
        for campo in CAMPOS_DE_ETIQUETA:
            bruto = fonte.get(campo)
            if bruto is None:
                continue
            valores = bruto if isinstance(bruto, (list, tuple)) else [bruto]
            for valor in valores:
                if isinstance(valor, dict):  # [{"name": "FEIRA"}] é tão comum quanto ["FEIRA"]
                    valor = valor.get("name") or valor.get("nome") or valor.get("label") or valor.get("title")
                if isinstance(valor, str) and valor.strip():
                    achadas.append(normalizar(valor))
    return tuple(dict.fromkeys(achadas))


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
                etiquetas=_etiquetas(produto, bruta),
            )
        )
    return itens


@dataclass
class Exclusoes:
    """As regras do Bruno para o que não entra no inventário. Editáveis no arquivo JSON."""

    etiquetas: tuple[str, ...] = ()
    prefixos_sku: tuple[str, ...] = ()
    codigos_inativos: tuple[str, ...] = ()
    codigos_sem_cadastro: tuple[str, ...] = ()

    @classmethod
    def carregar(cls, arquivo: Path) -> Exclusoes:
        """Lê o arquivo; se não existir, cria com os valores da rodada de 24 a 28/10 e usa esses."""
        try:
            bruto = json.loads(arquivo.read_text(encoding="utf-8"))
        except FileNotFoundError:
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(EXCLUSOES_PADRAO, ensure_ascii=False, indent=2), encoding="utf-8")
            bruto = dict(EXCLUSOES_PADRAO)
        except (OSError, ValueError) as erro:
            raise ErroInventario(
                f"não consegui ler {arquivo.name}: {erro}. Conserte o arquivo ou apague-o para eu recriar."
            ) from None
        if not isinstance(bruto, dict):
            raise ErroInventario(f"{arquivo.name} precisa ser um objeto JSON com as listas de exclusão.")

        def lista(nome: str) -> tuple[str, ...]:
            valores = bruto.get(nome, [])
            if not isinstance(valores, list) or any(not isinstance(v, str) for v in valores):
                raise ErroInventario(f"{arquivo.name}: '{nome}' precisa ser uma lista de textos.")
            return tuple(v.strip() for v in valores if v.strip())

        return cls(
            etiquetas=tuple(normalizar(e) for e in lista("etiquetas")),
            prefixos_sku=tuple(normalizar(p) for p in lista("prefixos_sku")),
            codigos_inativos=lista("codigos_inativos_protheus"),
            codigos_sem_cadastro=lista("codigos_sem_cadastro_protheus"),
        )

    @property
    def usa_etiqueta(self) -> bool:
        return bool(self.etiquetas)

    def motivo(self, item: Item) -> str:
        """Por que este item não entra, ou vazio se entra."""
        for etiqueta in self.etiquetas:
            if etiqueta in item.etiquetas:
                return f"etiqueta {etiqueta}"
        codigo = normalizar(item.codigo)
        for prefixo in self.prefixos_sku:
            if codigo.startswith(prefixo):
                return f"prefixo {prefixo}"
        if item.codigo in self.codigos_inativos:
            return "código inativo no Protheus"
        if item.codigo in self.codigos_sem_cadastro:
            return "código sem cadastro no Protheus"
        return ""


def peneirar(itens: Sequence[Item], exclusoes: Exclusoes) -> tuple[list[Item], dict[str, list[str]]]:
    """Separa o que entra do que sai, com a lista de quem saiu por cada regra.

    Se há regra por etiqueta mas nenhum produto do Fluxo trouxe etiqueta nenhuma, isto é erro, não
    resultado: significa que eu não sei ler a etiqueta nessa resposta, e seguir poria no inventário
    de produção justamente o que o Bruno mandou tirar.
    """
    if exclusoes.usa_etiqueta and not any(item.etiquetas for item in itens):
        raise ErroInventario(
            "as regras de exclusão usam etiqueta, mas nenhum produto do Fluxo veio com etiqueta. "
            f"Eu procuro nos campos {', '.join(CAMPOS_DE_ETIQUETA)}. Ou a rota /stock não manda a etiqueta, "
            "ou ela tem outro nome: descubra qual e me diga, porque sem isso eu excluiria zero produto "
            "sem avisar e o inventário iria com o que deveria ficar de fora."
        )
    mantidos: list[Item] = []
    fora: dict[str, list[str]] = {}
    for item in itens:
        motivo = exclusoes.motivo(item)
        if motivo:
            fora.setdefault(motivo, []).append(item.codigo)
        else:
            mantidos.append(item)
    if not mantidos:
        raise ErroInventario("as exclusões tiraram todos os produtos. Confira o inventario-exclusoes.json antes de seguir.")
    return mantidos, fora


def faixa_da_semana(dia: date, quantas: int) -> tuple[str, str, str]:
    """O prefixo do documento e a faixa da semana: `AA` + semana ISO + sequência de 4 dígitos.

    São 8 caracteres, como os que o Protheus aceitou, e não repetem entre semanas nem entre anos.
    """
    if quantas < 1:
        raise ErroInventario("não há linha nenhuma para numerar.")
    if quantas > MAX_LINHAS_NA_SEMANA:
        raise ErroInventario(
            f"são {quantas} linhas e a sequência do documento só vai até {MAX_LINHAS_NA_SEMANA}. "
            "Pare e me avise: mudar a regra do documento é decisão sua, não minha."
        )
    ano, semana, _dia_da_semana = dia.isocalendar()
    prefixo = f"{ano % 100:02d}{semana:02d}"
    return prefixo, f"{prefixo}0001", f"{prefixo}{quantas:04d}"


def montar(itens: Sequence[Item], dia: date, armazem: str = ARMAZEM, filial: str = FILIAL, de_para: dict[str, str] | None = None) -> list[Linha]:
    """Do estoque do Fluxo para as linhas da SB7, **um documento por linha**.

    Documento único para todas as linhas foi a tentativa que o Protheus recusou na rodada de 26/10.
    """
    prefixo, _inicio, _fim = faixa_da_semana(dia, len(itens))
    saida = []
    for numero, item in enumerate(itens, 1):
        codigo = (de_para or {}).get(item.codigo, item.codigo)
        if not _SKU.match(codigo):
            raise ErroInventario(f"o de-para transformou {item.codigo} em '{codigo[:30]}', que eu não ponho num arquivo de produção.")
        saida.append(
            Linha(filial=filial, codigo=codigo, local=armazem, documento=f"{prefixo}{numero:04d}",
                  quantidade=item.quantidade, data=dia, nome=item.nome, unidade=item.unidade)
        )  # fmt: skip
    return saida


def gravar_xlsx(linhas: Sequence[Linha], pasta: Path) -> Path:
    """A planilha no layout SB7 que o Protheus importou: `Plan1`, cabeçalho na linha 1.

    Produto, Armazém e Documento vão como **texto**: o Excel transformaria `02` em `2` e
    `26440001` em número, e o Protheus recusa os dois.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        raise ErroInventario(
            "falta a biblioteca da planilha. Na pasta do Jarvis, rode: "
            r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        ) from None
    livro = Workbook()
    folha = livro.active
    folha.title = PLANILHA
    folha.append(list(CABECALHO_XLSX))
    for linha in linhas:
        folha.append([linha.codigo, linha.local, linha.documento, float(linha.quantidade), linha.data])
        atual = folha.max_row
        for coluna in ("A", "B", "C"):
            folha[f"{coluna}{atual}"].number_format = "@"  # texto
        folha[f"E{atual}"].number_format = "DD/MM/YYYY"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"inventario-{linhas[0].data:%Y-%m-%d}.xlsx" if linhas else pasta / "inventario.xlsx"
    livro.save(arquivo)
    return arquivo


def gravar_csv_do_protheus(linhas: Sequence[Linha], pasta: Path) -> Path:
    """O CSV no mesmo formato do 'Salvar como' do Excel em português: `;` e decimal com vírgula.

    ⚠ Na rodada de 26/10 as quantidades com decimal entraram truncadas (62,6 virou 62). Se foi a
    vírgula, é aqui que se resolve — e é item da calibração. Até lá, a conferência da importação é
    que pega, e o relatório avisa.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"inventario-{linhas[0].data:%Y-%m-%d}.csv" if linhas else pasta / "inventario.csv"
    corpo = [";".join(CABECALHO_XLSX)]
    for linha in linhas:
        quantidade = f"{linha.quantidade:f}".replace(".", ",")
        corpo.append(";".join((linha.codigo, linha.local, linha.documento, quantidade, f"{linha.data:%d/%m/%Y}")))
    arquivo.write_text("\r\n".join(corpo) + "\r\n", encoding="utf-8-sig")
    return arquivo


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
    faixa: str = ""  # a faixa de documentos da semana, que o acerto precisa reescrever
    acerto_feito: bool = False
    motivo: str = ""
    pedido_em: str = ""
    passos: list[str] = field(default_factory=list)

    def como_json(self) -> dict[str, Any]:
        return {
            "data": self.data, "estado": self.estado, "itens": self.itens, "documento": self.documento,
            "confirmados": self.confirmados, "faixa": self.faixa, "acerto_feito": self.acerto_feito,
            "motivo": self.motivo, "pedido_em": self.pedido_em,
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
            ("confirmados", 0), ("faixa", ""), ("acerto_feito", False),
            ("motivo", ""), ("pedido_em", ""), ("passos", []),
        )})  # fmt: skip

    def gravar(self, dia: Dia) -> None:
        arquivo = self._arquivo(date.fromisoformat(dia.data))
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(dia.como_json(), ensure_ascii=False, indent=1), encoding="utf-8")

    def ja_rodou_na_semana(self, dia: date | None = None) -> Dia | None:
        """O inventário desta semana ISO, se já foi feito. O acerto não tem volta: duas vezes, não."""
        alvo = (dia or self._hoje()).isocalendar()[:2]
        pasta = self.raiz / PASTA
        try:
            nomes = sorted((p.name for p in pasta.iterdir() if p.is_dir()), reverse=True)[:30]
        except OSError:
            return None
        for nome in nomes:
            try:
                quando = date.fromisoformat(nome)
            except ValueError:
                continue
            if quando.isocalendar()[:2] != alvo:
                continue
            anterior = self.ler(quando)
            if anterior is not None and (anterior.acerto_feito or anterior.estado in ("concluido", "parado")):
                return anterior
        return None

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


class Importacao:
    """A importação do arquivo na MATA270. Ainda não existe, e recusa dizendo o que falta.

    Não é cautela de fachada: sem o roteiro calibrado não há o que clicar. O caminho do menu, os
    seis parâmetros do F12, onde fica Outras Ações > Outros > Importar e qual tipo de CSV o layout
    SB7 aceita — nada disso dá para adivinhar de fora, e errar aqui escreve inventário torto em
    produção.
    """

    FALTA = (
        "o roteiro calibrado da MATA270 (caminho do menu, parâmetros do F12, o botão Importar)",
        "qual tipo de CSV o layout SB7 aceita, e por que o decimal entrou truncado em 26/10",
        "como baixar o PDF do Imprimir Browse pelo navegador controlado",
        "se a data base pode ser trocada para a sexta pelo usuário do Jarvis",
    )

    def __init__(self, roteiro: Path | None = None) -> None:
        self.roteiro = roteiro

    @property
    def pronta(self) -> bool:
        return bool(self.roteiro and self.roteiro.exists())

    def recusa(self) -> str:
        return (
            "Não importo nada no Protheus ainda. Falta: " + "; ".join(self.FALTA)
            + ". O arquivo da semana está pronto e conferido na pasta — dá para importar à mão por ele."
        )

    def executar(self, linhas: Sequence[Linha]) -> None:
        raise ErroInventario(self.recusa())


class Acerto:
    """O acerto (MATA340). Além de não existir, é o passo que não tem volta.

    Ele gera requisições e devoluções que mudam saldo e custo. Mesmo depois de calibrado, o Jarvis
    preenche, confere, mostra e **para**: o clique final é do Bruno, com a segunda aprovação
    digitada. E a faixa de documentos fica gravada da execução anterior — em 28/10 estava numa de
    junho —, então ela é sempre reescrita e lida de volta.
    """

    def __init__(self, roteiro: Path | None = None) -> None:
        self.roteiro = roteiro

    @property
    def pronto(self) -> bool:
        return bool(self.roteiro and self.roteiro.exists())

    def recusa(self) -> str:
        return (
            "Não rodo o acerto ainda: falta o roteiro calibrado da MATA340, e o acerto não tem volta. "
            "Quando existir, eu preencho os parâmetros, reescrevo a faixa de documentos da semana, "
            "mostro a tela e paro — o clique é seu."
        )

    def executar(self, dia: Dia) -> None:
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
                log.exception("Falha ao disparar o inventário da semana")
            await asyncio.sleep(intervalo)


def relatorio(dia: Dia, linhas: Sequence[Linha], modo: str, pasta: Path, fora: dict[str, list[str]] | None = None) -> Path:
    """O relatório da semana, em Markdown, na pasta do dia."""
    agora = datetime.now()
    partes = [
        f"# Inventário semanal Fluxo → Protheus — {dia.data}",
        "",
        f"- Modo: **{modo}**",
        f"- Faixa de documentos: **{dia.faixa or '—'}**",
        f"- Linhas no arquivo: **{dia.itens}**",
        f"- Importadas e conferidas: **{dia.confirmados}**",
        f"- Estado: **{dia.estado}**",
        f"- Acerto (MATA340): **{'feito' if dia.acerto_feito else 'NÃO feito'}**",
    ]
    if dia.motivo:
        partes.append(f"- Motivo: {dia.motivo}")
    if fora:
        partes += ["", "## Produtos fora do inventário, por regra", ""]
        for motivo, codigos in sorted(fora.items()):
            partes.append(f"- **{motivo}** — {len(codigos)}: {', '.join(codigos[:40])}" + (" …" if len(codigos) > 40 else ""))
        partes.append("")
    if not dia.acerto_feito:
        partes += ["", "## Lembrete", "",
                   "O acerto (MATA340) **não foi feito**: ele muda saldo e custo e precisa da sua segunda aprovação.", ""]  # fmt: skip
    if dia.passos:
        partes += ["## Passos", "", *(f"- {passo}" for passo in dia.passos[-60:]), ""]
    if linhas:
        partes += ["## Primeiras linhas", "", "| Produto | Documento | Quantidade | Armazém |", "|---|---|---|---|"]
        partes += [f"| {l.codigo} | {l.documento} | {l.quantidade:f} | {l.local} |" for l in linhas[:20]]
        if len(linhas) > 20:
            partes.append(f"| … | mais {len(linhas) - 20} linhas no arquivo | | |")
        partes.append("")
    partes.append(f"_Gerado em {agora:%d/%m/%Y %H:%M}._")
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / "relatorio.md"
    arquivo.write_text("\n".join(partes), encoding="utf-8")
    return arquivo


class Inventario:
    """Junta as peças da semana: lê o Fluxo, peneira, monta o arquivo e segura o que não tem volta."""

    def __init__(
        self,
        fluxo: Fluxo,
        registro: Registro,
        modo: str = "desligado",
        maximo: int = MAX_ITENS,
        importacao: Importacao | None = None,
        acerto: Acerto | None = None,
        hoje: Callable[[], date] = date.today,
    ) -> None:
        self.fluxo, self.registro, self.modo, self.maximo = fluxo, registro, modo, maximo
        self.importacao = importacao or Importacao()
        self.acerto = acerto or Acerto()
        self._hoje = hoje
        self.aprovacao = Aprovacao()
        self.aprovacao_do_acerto = Aprovacao()
        self.linhas: list[Linha] = []
        self.fora: dict[str, list[str]] = {}

    @property
    def ligado(self) -> bool:
        return self.modo != "desligado"

    @property
    def arquivo_de_exclusoes(self) -> Path:
        return self.registro.raiz / PASTA / ARQUIVO_DE_EXCLUSOES

    def montar_o_dia(self) -> Dia:
        """Etapa A da v2: lê, peneira, numera e grava o arquivo da semana. Não encosta na tela."""
        hoje = self._hoje()
        ja = self.registro.ja_rodou_na_semana(hoje)
        if ja is not None:
            feito = "com acerto" if ja.acerto_feito else ja.estado
            raise ErroInventario(
                f"o inventário desta semana já foi feito em {ja.data} ({feito}, {ja.itens} itens, faixa {ja.faixa}). "
                "É semanal, e o acerto não tem volta: não rodo de novo."
            )
        exclusoes = Exclusoes.carregar(self.arquivo_de_exclusoes)
        itens = self.fluxo.estoque()
        mantidos, self.fora = peneirar(itens, exclusoes)
        self.linhas = montar(mantidos, hoje)
        conferir_tamanho(self.linhas, self.maximo)
        pasta = self.registro.pasta_do_dia(hoje)
        gravar_csv(self.linhas, pasta)  # o CSV simples, para você conferir
        gravar_xlsx(self.linhas, pasta)
        gravar_csv_do_protheus(self.linhas, pasta)
        _prefixo, inicio, fim = faixa_da_semana(hoje, len(self.linhas))
        dia = Dia(data=hoje.isoformat(), estado="montado", itens=len(self.linhas),
                  faixa=f"{inicio}-{fim}", documento=inicio)  # fmt: skip
        dia.passos.append(f"{datetime.now():%H:%M} arquivo montado: {resumir(self.linhas)}")
        for motivo, codigos in sorted(self.fora.items()):
            dia.passos.append(f"{datetime.now():%H:%M} fora por {motivo}: {len(codigos)} item(ns)")
        self.registro.gravar(dia)
        return dia

    def texto_das_exclusoes(self) -> str:
        if not self.fora:
            return "Nenhum produto saiu pelas regras de exclusão."
        partes = [f"{len(c)} por {m}" for m, c in sorted(self.fora.items())]
        total = sum(len(c) for c in self.fora.values())
        return f"{total} produto(s) fora: " + "; ".join(partes) + "."

    def pergunta(self, dia: Dia) -> str:
        """O que o Jarvis fala e escreve na sexta."""
        onde = "homologação" if self.modo == "homologacao" else "produção"
        base = (f"Inventário da semana pronto: {resumir(self.linhas)} Faixa de documentos {dia.faixa}. "
                f"{self.texto_das_exclusoes()}")  # fmt: skip
        if not self.importacao.pronta:
            return f"{base} O arquivo está na pasta da semana. {self.importacao.recusa()}"
        self.aprovacao.pedir(impressao_da_lista(self.linhas))
        dia.pedido_em = datetime.now().isoformat(timespec="seconds")
        self.registro.gravar(dia)
        return f"{base} Posso importar no Protheus de {onde}? Responda com /inventario aprovar nos próximos {self.aprovacao.minutos} minutos."

    def aprovar(self) -> str:
        """A primeira aprovação: libera a importação do arquivo. Não libera o acerto."""
        dia = self.registro.ler()
        if dia is None or not self.linhas:
            raise ErroInventario("não há arquivo montado para esta semana. Rode /inventario montar primeiro.")
        vale, por_que = self.aprovacao.vale_para(impressao_da_lista(self.linhas))
        if not vale:
            raise ErroInventario(f"não posso usar essa aprovação: {por_que}.")
        dia.estado = "aprovado"
        dia.passos.append(f"{datetime.now():%H:%M} importação aprovada pelo Bruno")
        self.registro.gravar(dia)
        try:
            self.importacao.executar(self.linhas)
        except ErroInventario as erro:
            dia.estado, dia.motivo = "parado", str(erro)
            dia.passos.append(f"{datetime.now():%H:%M} parou: {erro}")
            self.registro.gravar(dia)
            relatorio(dia, self.linhas, self.modo, self.registro.pasta_do_dia(), self.fora)
            raise
        dia.estado, dia.confirmados = "importado", len(self.linhas)
        self.registro.gravar(dia)
        relatorio(dia, self.linhas, self.modo, self.registro.pasta_do_dia(), self.fora)
        return f"Importação concluída: {dia.confirmados} linhas. O acerto é à parte: /inventario acertar."

    def acertar(self) -> str:
        """A segunda aprovação, só para o acerto. É o passo que muda saldo e custo."""
        dia = self.registro.ler()
        if dia is None:
            raise ErroInventario("não há inventário desta semana.")
        if dia.acerto_feito:
            raise ErroInventario(f"o acerto desta semana já foi feito (faixa {dia.faixa}). Duas vezes, não.")
        if dia.estado != "importado":
            raise ErroInventario(
                f"o inventário está em '{dia.estado}'. O acerto só depois da importação conferida: "
                "é ele que mexe em saldo e custo."
            )
        self.acerto.executar(dia)  # hoje sempre recusa; quando existir, para antes do clique
        dia.acerto_feito = True
        self.registro.gravar(dia)
        relatorio(dia, self.linhas, self.modo, self.registro.pasta_do_dia(), self.fora)
        return "Acerto concluído."

    def situacao(self) -> str:
        if not self.ligado:
            return "Inventário semanal desligado (JARVIS_INV_MODO=desligado no .env)."
        if not self.fluxo.pronto:
            return f"Inventário semanal em {self.modo}, mas não consigo ler o Fluxo: {self.fluxo.falta()}."
        dia = self.registro.ler()
        onde = self.registro.pasta_do_dia()
        if dia:
            agora = (f"Esta semana: {dia.estado}, {dia.itens} itens, faixa {dia.faixa}"
                     + (", acerto feito." if dia.acerto_feito else ", acerto pendente."))  # fmt: skip
        else:
            agora = "Esta semana: ainda não montei o arquivo."
        falta = "" if self.importacao.pronta else f" {self.importacao.recusa()}"
        return (f"Inventário semanal em {self.modo}, armazém {ARMAZEM}, teto de {self.maximo} itens. "
                f"{agora} Pasta: {onde}. Exclusões: {self.arquivo_de_exclusoes}.{falta}")  # fmt: skip
