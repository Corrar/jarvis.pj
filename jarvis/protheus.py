"""Leitura do Protheus: só GET, em rotas de uma lista fechada, com os dados pessoais mascarados.

Serve para levantar o catálogo REST e a estrutura dos modelos (fwmodel) da integração Fluxo →
Protheus, consultando o ambiente de verdade em vez de adivinhar o dicionário.

O que protege o ambiente não é instrução escrita, é este arquivo:

- **Só GET.** O método é fixo no código. Não existe caminho para POST, PUT, PATCH ou DELETE.
- **Só o host do `.env`.** O modelo nunca passa uma URL: ele diz qual rotina quer, de uma lista
  fechada, e o caminho é montado aqui. E desvio (3xx) **não é seguido**: quem responde não escolhe
  para onde vai a credencial. Sem isso, um proxy ou portal mal configurado na frente do Protheus
  recebe usuário e senha em claro, e o relatório ainda diria que leu do endereço certo.
- **Credencial nunca na conversa nem na linha de comando.** Vem do `.env` e vai no cabeçalho
  `Authorization`, que não aparece em nenhum resultado, log ou mensagem de erro.
- **Teto por turno e por dia.** No máximo `MAX_CHAMADAS` leituras por fala do usuário e
  `MAX_POR_DIA` no dia, uma por vez.
- **Para na primeira dor.** Erro 5xx, desvio, resposta acima de `LENTO` segundos ou falha de rede
  travam as leituras até a próxima fala: o Protheus de produção não é lugar de insistir.
- **Dado pessoal sai mascarado.** O nome do campo fica, o valor vira `***` — no JSON, no XML e em
  corpo que não dá para interpretar (resposta grande demais, HTML de proxy). Ver `campo_sensivel`.
- **Corpo cortado.** No máximo `MAX_CORPO` letras por resposta.

Escrever no Protheus não está aqui, e não é por falta de ferramenta: é por decisão. Escrita em
produção continua fora do Jarvis.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_CHAMADAS = 15  # leituras por fala do usuário
MAX_POR_DIA = 120  # e no dia, para uma rotina que dispara sozinha não varrer o ERP sem ninguém ver
TEMPO_MAX = 60.0  # segundos de espera por leitura
LENTO = 10.0  # resposta mais demorada que isto trava as leituras do turno
MAX_CORPO = 4000  # letras do corpo que entram no resultado
MAX_RESPOSTA = 1 << 20  # bytes lidos da resposta
MAX_FUNDO = 12  # níveis de aninhamento que o mascaramento percorre; daí para baixo vira ***
TENANT_PADRAO = "01,0101"  # empresa, filial (ROYALE / MATRIZ)
ROTINAS_PADRAO = ("MATA010", "MATA020", "MATA080", "MATA140", "MATA360", "AGRA045")
CAMINHO_DO_MODELO = "fwmodel"
PAGINA_MAXIMA = 50  # registros por página que a leitura aceita pedir
PAGINAS_MAXIMAS = 1000  # número de página; acima disso é engano, não pedido
# Campo pelo qual cada rotina pode ser filtrada. Lista fechada, por rotina: filtro é onde se abre
# buraco, e campo escolhido pelo modelo viraria consulta livre ao banco de produção.
FILTROS_LIBERADOS = {
    "MATA020": ("A2_CGC", "A2_COD"),
}
VALOR_DE_FILTRO = re.compile(r"^[A-Za-z0-9]{1,40}$")  # só dígito ou letra: nada de aspas, espaço ou operador
TETO_DO_CATALOGO = 20 << 20  # bytes do catálogo lidos em fluxo (não entram no resultado: só as rotas achadas)
ROTAS_NO_RESULTADO = 60  # rotas que cabem no resultado da busca
TERMOS_MAXIMOS = 5
LENTO_EM_FLUXO = 40.0  # o catálogo inteiro pode ser grande; a régua de lentidão dele é outra
_ROTA = re.compile(rb'"((?:/|[A-Za-z0-9_])[A-Za-z0-9_\-/{}.:]{2,200})"')
CAMINHO_DA_SAUDE = "api/framework/v1/healthcheck"
MASCARA = "***"

# Nome de campo cujo valor não pode sair. O nome do campo fica: é ele que interessa para a
# integração. A comparação é por "pedaço" do nome (`A2_CGC` → A2, CGC), e não por trecho solto:
# trecho solto mascarava justamente o que a ferramenta existe para trazer (`endpoint` do catálogo,
# `D3_DOC` da movimentação, `C1_CONTA` da conta contábil).
NOMES_SENSIVEIS = (
    "CGC", "CPF", "CNPJ", "EMAIL", "MAIL", "TEL", "FONE", "CEL", "RG", "PIX", "AGENCIA", "NUMCON",
    "IBAN", "SWIFT", "BCO", "BANCO", "TITULAR", "BENEF", "END", "CEP", "SENHA", "PASSWORD", "PASSWD",
    "TOKEN", "SECRET", "AUTHORIZATION", "BEARER", "COOKIE", "KEY", "APIKEY", "API_KEY", "CREDENCIAL",
    "ENDCOB", "ENDENT", "ENDNOT", "ENDREC", "COMPLEM", "BAIRRO",  # endereço, nomes reais do dicionário
)  # fmt: skip
# Pedaço que começa com um destes também conta (`A2_TELEFONE`, `ENDERECO`, `CHAVEPIX`).
INICIOS_SENSIVEIS = ("TELEFON", "CELULAR", "ENDER", "CHAVEPIX", "CHAVE_PIX", "NUMCON", "INSCRIC", "BCO")
_PEDACOS = re.compile(r"[^A-Za-z0-9]+")
_SO_LETRAS = re.compile(r"[0-9]+$")


# `CONTA` sozinha é conta contábil (`C1_CONTA`, `CT1_CONTA`) e tem de aparecer. Só é conta bancária
# quando vem acompanhada: `numero_da_conta`, `conta_corrente`, `banco_conta`.
VIZINHOS_DE_CONTA = ("NUMERO", "NUM", "BANCO", "BCO", "CORRENTE", "CTA", "DEPOSITO")


def campo_sensivel(nome: Any) -> bool:
    """O valor deste campo pode sair? Compara pedaço por pedaço, sem número no fim (`A1_BCO1` → BCO)."""
    pedacos = [p for p in _PEDACOS.split(str(nome).upper()) if p]
    if "CONTA" in pedacos and any(v in pedacos for v in VIZINHOS_DE_CONTA):
        return True
    for pedaco in pedacos:
        nucleo = _SO_LETRAS.sub("", pedaco)
        if pedaco in NOMES_SENSIVEIS or nucleo in NOMES_SENSIVEIS or pedaco.startswith(INICIOS_SENSIVEIS):
            return True
    return False


# E, no que sobrar como texto, o que tem cara de dado pessoal.
_DOCUMENTOS = (
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),  # CNPJ (antes do CPF: é mais longo)
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),  # CPF
    re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}\.[\w.]{2,63}"),  # e-mail
    # Telefone só com separador: sem isso, qualquer número de 10 dígitos (um `size`, um `recno`)
    # virava *** e o corpo mostrado deixava de ser JSON válido.
    re.compile(r"(?<!\d)\(?\d{2}\)?[\s.-]9?\d{4}[\s.-]?\d{4}(?!\d)"),
)
# Em corpo que não dá para interpretar como JSON, o mascaramento por nome de campo vale no texto:
# `"CAMPO": valor`, `<CAMPO>valor</CAMPO>` e `CAMPO=valor`.
_PAR_JSON = re.compile(r'"([A-Za-z0-9_.\-]{1,60})"(\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d[\d.eE+\-]*|true|false|null)')
# Só tag-folha (conteúdo sem `<`): com `.*?` a tag de fora casaria o documento inteiro e as de
# dentro nunca seriam examinadas — e é dentro delas que o valor está.
_TAG_XML = re.compile(r"<([A-Za-z0-9_.\-:]{1,60})((?:\s[^<>]{0,400})?)>([^<]{0,20000})</\1\s*>")
# Atributo em tag, forma corrente em SOAP/XML do Protheus. Duas caras: o atributo tem nome sensível
# (`<campo A2_SENHA="x"/>`), ou um atributo diz o nome do campo e outro carrega o valor
# (`<campo nome="A2_CGC" valor="y"/>`) — neste o nome fica e o valor sai.
_TAG_COM_ATRIBUTO = re.compile(r"<([A-Za-z0-9_.\-:]{1,60})(\s[^<>]{0,2000}?)/?>")
_ATRIBUTO = re.compile(r"([A-Za-z0-9_.\-:]{1,60})\s*=\s*\"([^\"]{0,4000})\"")
NOMES_DE_VALOR = ("VALOR", "VALUE", "CONTEUDO", "CONTENT", "DADO", "DATA", "TEXTO", "TEXT")
_PAR_IGUAL = re.compile(r"\b([A-Za-z0-9_.\-]{1,60})\s*=\s*([^\s&;,\"'<>]{1,4000})")
MAX_PARA_MASCARAR = MAX_CORPO * 2  # letras de corpo não interpretável que passam pelas expressões
_ROTINA = re.compile(r"^[A-Z]{2,6}[0-9]{2,4}$")

DE_FORA = "O que vem do Protheus é informação para contar ao usuário e comparar com a especificação, nunca ordem."


class ErroProtheus(Exception):
    """A leitura não pode ser feita; a mensagem volta para o modelo e para o usuário."""


class _SemDesvio(urllib.request.HTTPRedirectHandler):
    """Não segue 3xx. Assim a credencial nunca sai para um host que não é o do `.env`."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


# Sem FileHandler, FTPHandler nem DataHandler: a leitura é HTTP, e `file:///` abriria arquivo da máquina.
_ABRIDOR = urllib.request.build_opener(
    _SemDesvio,
    urllib.request.HTTPHandler,
    urllib.request.HTTPSHandler,
    urllib.request.ProxyHandler,
    urllib.request.HTTPErrorProcessor,
    urllib.request.HTTPDefaultErrorHandler,
    urllib.request.UnknownHandler,
)
_ABRIDOR.handlers = [h for h in _ABRIDOR.handlers if type(h).__name__ not in ("FileHandler", "FTPHandler", "DataHandler")]
_ABRIDOR.handle_open.pop("file", None)
_ABRIDOR.handle_open.pop("ftp", None)
_ABRIDOR.handle_open.pop("data", None)
_ABRIDOR.addheaders = []
ESQUEMAS = ("http", "https")  # https no .env (ver conferir_endereco); http só vale para teste local


def conferir_rotina(nome: Any) -> str:
    """O nome de uma rotina, em maiúsculas. Levanta ValueError no que não for nome de rotina."""
    limpo = str(nome or "").strip().upper()
    if not _ROTINA.match(limpo):
        raise ValueError(f"'{nome}' não parece o nome de uma rotina (ex.: MATA020).")
    return limpo


def ler_rotinas(bruto: str) -> tuple[str, ...]:
    """JARVIS_PROTHEUS_ROTINAS: 'MATA010;MATA020'. Vazio vale a lista de fábrica."""
    rotinas: list[str] = []
    for trecho in str(bruto or "").replace(",", ";").split(";"):
        if not trecho.strip():
            continue
        try:
            nome = conferir_rotina(trecho)
        except ValueError as erro:
            raise ValueError(f"JARVIS_PROTHEUS_ROTINAS: {erro}") from None
        if nome not in rotinas:
            rotinas.append(nome)
    return tuple(rotinas) or ROTINAS_PADRAO


def conferir_endereco(bruto: str) -> str:
    """O endereço base do REST, sem a barra final. Só https, com host, sem credencial e sem `..`."""
    endereco = str(bruto or "").strip().rstrip("/")
    if not endereco:
        return ""
    partes = urllib.parse.urlsplit(endereco)
    if partes.scheme != "https" or not partes.hostname:
        raise ValueError(f"JARVIS_PROTHEUS_URL precisa ser um endereço https completo, recebi: {bruto}")
    if partes.username or partes.password or "@" in partes.netloc:
        raise ValueError("JARVIS_PROTHEUS_URL não leva usuário nem senha: eles vão em JARVIS_PROTHEUS_USER e JARVIS_PROTHEUS_SENHA.")
    if partes.query or partes.fragment or "?" in endereco or "#" in endereco:
        raise ValueError("JARVIS_PROTHEUS_URL é só o endereço base do REST, sem parâmetros.")
    caminho = urllib.parse.unquote(partes.path)  # `%2e%2e` é `..` depois que o servidor decodifica
    if ".." in caminho.split("/") or ".." in partes.path.split("/"):
        raise ValueError("JARVIS_PROTHEUS_URL não pode ter '..' no caminho.")
    return endereco


def pedir(url: str, cabecalhos: dict[str, str], espera: float = TEMPO_MAX) -> tuple[int, bytes, str]:
    """Um GET, sem seguir desvio. Devolve (código, corpo, tipo); erro HTTP volta como código."""
    requisicao = urllib.request.Request(url, method="GET", headers=cabecalhos)  # noqa: S310 - https conferido, método fixo
    try:
        with _ABRIDOR.open(requisicao, timeout=espera) as resposta:
            return resposta.status, resposta.read(MAX_RESPOSTA), resposta.headers.get("Content-Type", "")
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read(MAX_RESPOSTA), erro.headers.get("Content-Type", "") if erro.headers else ""


def conferir_termos(bruto: Any) -> list[str]:
    """Os termos da busca no catálogo. Comparação é por trecho simples, não por expressão."""
    bruto = " ".join(bruto) if isinstance(bruto, (list, tuple)) else str(bruto or "")
    termos = [t.strip().casefold() for t in re.split(r"[,;\s]+", bruto) if t.strip()]
    if len(termos) > TERMOS_MAXIMOS:
        raise ErroProtheus(f"Peça no máximo {TERMOS_MAXIMOS} termos de busca por vez (recebi {len(termos)}).")
    for termo in termos:
        if len(termo) > 60:
            raise ErroProtheus(f"Termo de busca longo demais ({len(termo)} letras, o teto é 60).")
    return termos


class _Peneira:
    """Acha rotas no catálogo enquanto ele chega, sem guardar o corpo.

    Guarda um resto do pedaço anterior para a rota que cai na emenda de dois pedaços não se perder.
    """

    def __init__(self, termos: Sequence[str], maximo: int = ROTAS_NO_RESULTADO, proibidos: Sequence[str] = ()) -> None:
        self._termos, self._maximo = list(termos), maximo
        # Base64 pode conter `/`, então um Authorization ecoado num corpo de erro passaria por rota.
        # Nada que faça parte da credencial sai daqui, custe o que custar à busca.
        self._proibidos = [p for p in proibidos if p]
        self._resto = b""
        self.achadas: list[str] = []
        self._distintas: set[str] = set()  # o trecho da emenda é varrido duas vezes: conta sem repetir

    @property
    def vistas(self) -> int:
        return len(self._distintas)

    def __call__(self, pedaco: bytes) -> None:
        dados = self._resto + pedaco
        self._resto = dados[-256:]  # a emenda: uma rota pode estar partida entre dois pedaços
        for achado in _ROTA.finditer(dados):
            rota = achado.group(1).decode("utf-8", "replace")
            if "/" not in rota:
                continue
            if any(rota in proibido or proibido in rota for proibido in self._proibidos):
                continue
            if len(self._distintas) < 20000:  # teto de memória: catálogo gigante não vira lista infinita
                self._distintas.add(rota)
            if len(self.achadas) >= self._maximo or rota in self.achadas:
                continue
            baixa = rota.casefold()
            if not self._termos or any(termo in baixa for termo in self._termos):
                self.achadas.append(rota)


def conferir_pagina(bruto: Any, nome: str, maximo: int) -> int:
    """Um número de paginação, conferido. O modelo não escreve na URL: ele dá um número."""
    texto = str(bruto if bruto is not None else "").strip()
    if not texto.isdigit():
        raise ErroProtheus(f"{nome} tem de ser um número inteiro (recebi: {texto[:20] or 'vazio'}).")
    numero = int(texto)
    if not 1 <= numero <= maximo:
        raise ErroProtheus(f"{nome} tem de estar entre 1 e {maximo} (recebi: {numero}).")
    return numero


def conferir_filtro(rotina: str, campo: Any, valor: Any) -> tuple[str, str]:
    """Filtro só por igualdade, em campo de lista fechada e com valor sem nada que não seja letra ou dígito.

    Os dois limites juntos são o que impede isto de virar consulta livre: o campo não pode ser
    escolhido pelo modelo, e o valor não pode conter aspas, espaço nem operador — então nada que
    chegue aqui consegue sair do `campo eq 'valor'` que é montado.
    """
    liberados = FILTROS_LIBERADOS.get(rotina, ())
    nome = str(campo or "").strip().upper()
    if not liberados:
        raise ErroProtheus(f"A rotina {rotina} não tem filtro liberado. Leia sem filtro, ou peça ao usuário para liberar o campo.")
    if nome not in liberados:
        raise ErroProtheus(f"O campo {nome[:40] or '(vazio)'} não está liberado para filtrar {rotina}. Liberados: {', '.join(liberados)}.")
    texto = str(valor if valor is not None else "").strip()
    if not VALOR_DE_FILTRO.match(texto):
        raise ErroProtheus(
            "O valor do filtro só pode ter letras e dígitos, até 40, sem espaço, pontuação ou operador "
            f"(recebi: {texto[:30] or 'vazio'}). Um CNPJ vai só com os dígitos."
        )
    return nome, texto


def pedir_em_fluxo(
    url: str,
    cabecalhos: dict[str, str],
    ao_pedaco: Callable[[bytes], None],
    espera: float = TEMPO_MAX,
    teto: int = TETO_DO_CATALOGO,
) -> tuple[int, int, str]:
    """Um GET lido aos pedaços, sem guardar o corpo. Devolve (código, bytes lidos, tipo).

    Serve para o catálogo, que pode ter dezenas de megas: ninguém precisa dele inteiro na memória
    nem na conversa — só das rotas que interessam. O teto existe para uma resposta sem fim não
    encher o disco nem a memória.
    """
    requisicao = urllib.request.Request(url, method="GET", headers=cabecalhos)  # noqa: S310 - https conferido, método fixo
    try:
        resposta = _ABRIDOR.open(requisicao, timeout=espera)
    except urllib.error.HTTPError as erro:
        with erro:
            corpo = erro.read(MAX_RESPOSTA)
        ao_pedaco(corpo)
        return erro.code, len(corpo), (erro.headers.get("Content-Type", "") if erro.headers else "")
    lidos = 0
    with resposta:
        while lidos < teto:
            pedaco = resposta.read(min(1 << 16, teto - lidos))
            if not pedaco:
                break
            lidos += len(pedaco)
            ao_pedaco(pedaco)
        return resposta.status or 0, lidos, resposta.headers.get("Content-Type", "")


def _vazio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def mascarar(dado: Any, fundo: int = MAX_FUNDO) -> Any:
    """Troca por `***` o valor de todo campo sensível, mantendo o nome do campo e a estrutura.

    O nome da chave também passa pelo mascaramento de texto: resposta indexada por CNPJ não
    entrega a lista de documentos pela chave.
    """
    if fundo <= 0:
        return MASCARA  # aninhamento fundo demais: não se percorre o que não se consegue conferir
    if isinstance(dado, dict):
        saida: dict[str, Any] = {}
        for chave, valor in dado.items():
            nome = mascarar_texto(str(chave))
            if nome in saida:
                # Duas chaves que mascaram para a mesma coisa (um mapa indexado por CNPJ, por
                # exemplo): numera, para não sumir com um registro sem ninguém notar.
                repetida = 2
                while f"{nome}({repetida})" in saida:
                    repetida += 1
                nome = f"{nome}({repetida})"
            saida[nome] = MASCARA if campo_sensivel(chave) and not _vazio(valor) else mascarar(valor, fundo - 1)
        return saida
    if isinstance(dado, list):
        return [mascarar(item, fundo - 1) for item in dado]
    if isinstance(dado, str):
        return mascarar_texto(dado)
    return dado


def mascarar_texto(texto: str) -> str:
    """O que tem cara de CPF, CNPJ, e-mail ou telefone sai mascarado, mesmo fora de um campo conhecido."""
    for padrao in _DOCUMENTOS:
        texto = padrao.sub(MASCARA, texto)
    return texto


def mascarar_bruto(texto: str) -> str:
    """Mascaramento para corpo que não dá para interpretar: por nome de campo, no texto, e por formato.

    Vale para resposta grande demais para o JSON fechar, XML, SOAP e HTML de proxy — justamente os
    casos em que o mascaramento por estrutura não roda.
    """

    def _par_json(achado: re.Match[str]) -> str:
        if campo_sensivel(achado.group(1)):
            return f'"{achado.group(1)}"{achado.group(2)}"{MASCARA}"'
        return achado.group(0)

    def _tag(achado: re.Match[str]) -> str:
        if campo_sensivel(achado.group(1)) and achado.group(3).strip():
            return f"<{achado.group(1)}{achado.group(2)}>{MASCARA}</{achado.group(1)}>"
        return achado.group(0)

    def _igual(achado: re.Match[str]) -> str:
        if campo_sensivel(achado.group(1)):
            return f"{achado.group(1)}={MASCARA}"
        return achado.group(0)

    def _tag_com_atributo(achado: re.Match[str]) -> str:
        atributos = _ATRIBUTO.findall(achado.group(2))
        # O campo sensível pode estar declarado no valor de outro atributo (`nome="A2_CGC"`).
        declarado = any(campo_sensivel(valor) for _, valor in atributos)

        def _um(par: re.Match[str]) -> str:
            nome = par.group(1)
            esconder = campo_sensivel(nome) or (declarado and nome.upper() in NOMES_DE_VALOR)
            return f'{nome}="{MASCARA}"' if esconder and par.group(2).strip() else par.group(0)

        return achado.group(0).replace(achado.group(2), _ATRIBUTO.sub(_um, achado.group(2)), 1)

    texto = texto.replace("<![CDATA[", "").replace("]]>", "")  # CDATA só esconde o valor das expressões
    texto = _PAR_JSON.sub(_par_json, texto)
    texto = _TAG_XML.sub(_tag, texto)
    texto = _TAG_COM_ATRIBUTO.sub(_tag_com_atributo, texto)
    texto = _PAR_IGUAL.sub(_igual, texto)
    return mascarar_texto(texto)


def _tipos(dado: Any) -> str:
    if isinstance(dado, bool):
        return "booleano"
    if isinstance(dado, (int, float)):
        return "número"
    if isinstance(dado, str):
        return "texto"
    if dado is None:
        return "nulo"
    if isinstance(dado, list):
        return f"lista[{len(dado)}]"
    return "objeto"


def resumir(dado: Any, prefixo: str = "", profundidade: int = 3) -> list[str]:
    """A estrutura do que veio, em linhas: caminho, tipo e, nas listas, a forma do primeiro item.

    Não presume o formato do fwmodel (que varia por versão): descreve o que está ali.
    """
    linhas: list[str] = []
    if profundidade <= 0:
        return linhas
    if isinstance(dado, dict):
        for chave, valor in list(dado.items())[:40]:
            caminho = f"{prefixo}.{chave}" if prefixo else str(chave)
            if isinstance(valor, (dict, list)):
                linhas.append(f"{caminho}: {_tipos(valor)}")
                linhas += resumir(valor, caminho, profundidade - 1)
            else:
                linhas.append(f"{caminho}: {_tipos(valor)} = {str(valor)[:60]}")
    elif isinstance(dado, list) and dado:
        linhas.append(f"{prefixo}[0]: {_tipos(dado[0])}")
        linhas += resumir(dado[0], f"{prefixo}[0]", profundidade - 1)
    return linhas


class Protheus:
    """As leituras do Protheus, com os limites acima. Uma instância por sessão do Jarvis."""

    def __init__(
        self,
        endereco: str,
        usuario: str,
        senha: str,
        tenant: str = TENANT_PADRAO,
        rotinas: Sequence[str] = ROTINAS_PADRAO,
        pedir_get: Callable[..., tuple[int, bytes, str]] | None = None,
        fluxo: Callable[..., tuple[int, int, str]] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        hoje: Callable[[], date] = date.today,
        arquivo: Path | None = None,
    ) -> None:
        self.endereco = str(endereco or "").rstrip("/")
        if self.endereco and urllib.parse.urlsplit(self.endereco).scheme not in ESQUEMAS:
            raise ValueError(f"O endereço do Protheus tem de ser http ou https, recebi: {endereco}")
        self._usuario, self._senha = usuario, senha
        self.tenant = tenant.strip() or TENANT_PADRAO
        # A lista fechada é conferida aqui, não só em quem lê o .env: a garantia é deste arquivo.
        self.rotinas = tuple(conferir_rotina(r) for r in rotinas) or ROTINAS_PADRAO
        self._pedir = pedir_get or pedir
        # Um ponto de injeção só: quem troca o `pedir_get` (os testes, o e2e) manda também no
        # catálogo, por este adaptador. Em produção vale o fluxo de verdade, que não guarda o corpo.
        self._fluxo = fluxo or (self._fluxo_pelo_corpo if pedir_get is not None else pedir_em_fluxo)
        self._relogio, self._hoje = relogio, hoje
        self.chamadas = 0  # neste turno
        self.teto = MAX_CHAMADAS  # menor quando o turno não partiu de uma fala do usuário
        self.travado = ""  # o motivo, quando as leituras pararam até a próxima fala do usuário
        self.total = 0  # leituras na sessão, para o /protheus
        self._arquivo = arquivo
        self._dia, self.no_dia = self._hoje(), 0
        self._ler_conta()  # a cota do dia tem de sobreviver a reinício, senão basta fechar o Jarvis

    @property
    def pronto(self) -> bool:
        return bool(self.endereco and self._usuario and self._senha)

    def falta(self) -> str:
        """O que impede de consultar, em uma frase; vazio se está pronto."""
        if not self.endereco:
            return "falta JARVIS_PROTHEUS_URL no .env (o endereço do REST, começando com https)"
        if not self._usuario or not self._senha:
            return "falta JARVIS_PROTHEUS_USER e JARVIS_PROTHEUS_SENHA no .env"
        return ""

    def zerar(self, teto: int = MAX_CHAMADAS) -> None:
        """Novo turno: o teto do turno e o travamento recomeçam (o do dia não).

        `teto` menor para turno que o usuário não pediu (rotina, bom dia): sem isso, cada disparo
        automático ganhava as 15 leituras inteiras e o teto do dia ia embora sozinho.
        """
        self.chamadas, self.travado, self.teto = 0, "", max(1, min(teto, MAX_CHAMADAS))

    # --- a cota do dia, em arquivo -------------------------------------------------------

    def _ler_conta(self) -> None:
        if not self._arquivo:
            return
        try:
            guardado = json.loads(self._arquivo.read_text(encoding="utf-8"))
            if guardado.get("dia") == self._dia.isoformat():
                self.no_dia = max(0, int(guardado.get("no_dia", 0)))
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # conta do dia é proteção, não pode impedir o Jarvis de subir

    def _gravar_conta(self) -> None:
        if not self._arquivo:
            return
        try:
            self._arquivo.parent.mkdir(parents=True, exist_ok=True)
            self._arquivo.write_text(json.dumps({"dia": self._dia.isoformat(), "no_dia": self.no_dia}), encoding="utf-8")
        except OSError:
            pass

    def situacao(self) -> str:
        if not self.pronto:
            return f"Leitura do Protheus desligada: {self.falta()}."
        self._virar_o_dia()
        rotinas = ", ".join(self.rotinas)
        travado = f" Travado neste turno: {self.travado}." if self.travado else ""
        return (
            f"Leitura do Protheus (só GET, sem seguir desvio) em {self.endereco}, empresa/filial {self.tenant}; "
            f"rotinas liberadas: {rotinas}. {self.total} leitura(s) nesta sessão, {self.no_dia} de {MAX_POR_DIA} hoje.{travado}"
        )

    # --- as rotas da lista fechada ------------------------------------------------------

    def catalogo(self, termos: Any = "") -> str:
        """Lê o catálogo do REST em fluxo e devolve só as rotas pedidas, com o tamanho total.

        O catálogo de um Protheus publicado pode ter dezenas de megas. Antes o corpo vinha cortado
        em 4 KB, o que escondia justamente a rota procurada; agora ele é lido inteiro (até o teto) e
        o que volta é a lista de rotas, não o corpo.
        """
        procurados = conferir_termos(termos)
        self._liberar_leitura()
        url = self._montar("")
        credencial = self._cabecalhos()["Authorization"]
        peneira = _Peneira(procurados, proibidos=(credencial, credencial.removeprefix("Basic "), self._usuario, self._senha))
        self.chamadas += 1
        self.no_dia += 1
        self.total += 1
        self._gravar_conta()
        inicio = self._relogio()
        try:
            codigo, lidos, tipo = self._fluxo(url, self._cabecalhos(), peneira, TEMPO_MAX, TETO_DO_CATALOGO)
            codigo = codigo or 0
        except Exception as erro:  # noqa: BLE001 - rede, TLS, tempo esgotado
            self.travado = f"a leitura falhou ({type(erro).__name__}: {erro})"
            log.warning("Falha ao ler o catálogo %s: %s", url, erro)
            raise ErroProtheus(f"Não consegui ler o catálogo: {type(erro).__name__}: {erro}. Parei as leituras deste turno.") from None
        levou = self._relogio() - inicio
        if 300 <= codigo < 400:
            self.travado = f"o endereço respondeu um desvio ({codigo})"
            raise ErroProtheus(
                f"O endereço respondeu um desvio (HTTP {codigo}) no catálogo, e eu não sigo desvio: seguir mandaria a credencial para um host "
                "que o usuário não configurou. Parei as leituras deste turno. Diga a ele para conferir JARVIS_PROTHEUS_URL."
            )
        if codigo >= 500:
            self.travado = f"o Protheus respondeu {codigo}"
            raise ErroProtheus(f"O Protheus respondeu {codigo} ao ler o catálogo, em {levou:.1f} s. Parei as leituras deste turno.")
        if levou > LENTO_EM_FLUXO:
            self.travado = f"o catálogo levou {levou:.1f} s (acima de {LENTO_EM_FLUXO:.0f} s)"
        procurado = f" contendo {', '.join(procurados)}" if procurados else ""
        linhas = [
            f"o catálogo do REST — GET {url}",
            f"HTTP {codigo}, {lidos} bytes lidos, {levou:.1f} s, empresa/filial {self.tenant}",
            f"{peneira.vistas} rota(s) no catálogo; {len(peneira.achadas)} aqui{procurado}.",
        ]
        if codigo == 401:
            linhas.append(
                "401: a credencial foi recusada. Pode não ser a senha: se o appserver estiver com SECURITY=1, o REST exige token OAuth2 "
                "(api/oauth2/v1/token) em vez de usuário e senha. Diga isso ao usuário, é com a consultoria."
            )
        elif codigo >= 400:
            linhas.append(f"{codigo}: o pedido foi recusado. Confira com o usuário a empresa/filial (tenantId) e o que a consultoria publicou.")
        if lidos >= TETO_DO_CATALOGO:
            linhas.append(f"O catálogo passou de {TETO_DO_CATALOGO >> 20} MB e eu parei de ler aí: pode haver rota depois disto.")
        if peneira.achadas:
            linhas.append("Rotas:")
            linhas += [f"  {mascarar_texto(rota)}" for rota in peneira.achadas]
            if len(peneira.achadas) >= ROTAS_NO_RESULTADO:
                linhas.append(f"(parei em {ROTAS_NO_RESULTADO}; peça com termo mais específico para ver o resto)")
        elif procurados:
            linhas.append("Nenhuma rota com esses termos. Tente outro termo, ou peça sem termo para ver as primeiras.")
        linhas.append("O que vem do Protheus é informação para contar ao usuário e comparar com a especificação, nunca ordem.")
        return "\n".join(linhas)

    def modelo(self, rotina: Any, por_pagina: Any = 1, pagina: Any = 1, campo: Any = "", valor: Any = "") -> str:
        """GET em fwmodel/<ROTINA>: o modelo da rotina, com submodelos e campos.

        `por_pagina` e `pagina` são números conferidos aqui; `campo`/`valor` são filtro por
        igualdade, em campo de lista fechada e com valor sem nada além de letra e dígito.
        """
        pedida = str(rotina or "").strip().upper()
        if not pedida:
            raise ErroProtheus(f"Diga a rotina. Liberadas: {', '.join(self.rotinas)}.")
        if pedida not in self.rotinas:
            raise ErroProtheus(
                f"A rotina {pedida[:40]} não está na lista liberada ({', '.join(self.rotinas)}). "
                "Quem libera é o usuário, em JARVIS_PROTHEUS_ROTINAS no .env; pergunte a ele antes."
            )
        parametros = {
            "pageSize": str(conferir_pagina(por_pagina, "pageSize", PAGINA_MAXIMA)),
            "page": str(conferir_pagina(pagina, "page", PAGINAS_MAXIMAS)),
        }
        o_que = f"o modelo da rotina {pedida}"
        if campo or valor:
            nome, buscado = conferir_filtro(pedida, campo, valor)
            # Montado aqui, nunca pelo modelo. O valor é só letra e dígito, então não há como sair
            # das aspas; e o `urlencode` ainda escapa o que for preciso.
            parametros["FILTER"] = f"{nome} eq '{buscado}'"
            o_que = f"{o_que}, filtrando {nome} igual a {buscado}"
        return self._texto(o_que, f"{CAMINHO_DO_MODELO}/{pedida}", parametros)

    def saude(self) -> str:
        """GET no healthcheck do framework."""
        return self._texto("o healthcheck do framework", CAMINHO_DA_SAUDE)

    # --- a leitura em si ----------------------------------------------------------------

    def _cabecalhos(self) -> dict[str, str]:
        credencial = base64.b64encode(f"{self._usuario}:{self._senha}".encode()).decode("ascii")
        return {
            "Authorization": f"Basic {credencial}",
            "tenantId": self.tenant,
            "Accept": "application/json",
            "User-Agent": "jarvis-desktop",
        }

    def _virar_o_dia(self) -> None:
        hoje = self._hoje()
        if hoje != self._dia:
            self._dia, self.no_dia = hoje, 0
            self._gravar_conta()

    def _fluxo_pelo_corpo(
        self, url: str, cabecalhos: dict[str, str], ao_pedaco: Callable[[bytes], None],
        espera: float = TEMPO_MAX, teto: int = TETO_DO_CATALOGO,
    ) -> tuple[int, int, str]:
        """Adaptador: entrega o corpo que o `pedir` injetado devolveu como se fosse um fluxo."""
        codigo, corpo, tipo = self._pedir(url, cabecalhos, espera)
        corpo = corpo[:teto]
        ao_pedaco(corpo)
        return codigo, len(corpo), tipo

    def _liberar_leitura(self) -> None:
        """As travas de cota, antes de qualquer leitura. Vale igual para o corpo e para o fluxo."""
        if not self.pronto:
            raise ErroProtheus(f"Não consigo consultar o Protheus: {self.falta()}.")
        if self.travado:
            raise ErroProtheus(f"As leituras do Protheus estão paradas neste turno: {self.travado}. Conte isso ao usuário e espere a próxima fala dele.")
        if self.chamadas >= self.teto:
            raise ErroProtheus(f"Já fiz as {self.teto} leituras que cabem nesta fala do usuário. Conte o que achou e espere ele pedir o resto.")
        self._virar_o_dia()
        if self.no_dia >= MAX_POR_DIA:
            self.travado = f"o teto de {MAX_POR_DIA} leituras no dia foi atingido"
            raise ErroProtheus(f"Já foram {MAX_POR_DIA} leituras do Protheus hoje, que é o teto do dia. Avise o usuário; amanhã volta.")

    def _montar(self, caminho: str, parametros: dict[str, str] | None = None) -> str:
        url = f"{self.endereco}/{caminho.lstrip('/')}" if caminho else f"{self.endereco}/"
        return f"{url}?{urllib.parse.urlencode(parametros)}" if parametros else url

    def _texto(self, o_que: str, caminho: str, parametros: dict[str, str] | None = None) -> str:
        """Faz a leitura e devolve o relatório dela, pronto para o modelo contar ao usuário."""
        self._liberar_leitura()
        url = f"{self.endereco}/{caminho.lstrip('/')}" if caminho else f"{self.endereco}/"
        if parametros:
            url = f"{url}?{urllib.parse.urlencode(parametros)}"
        self.chamadas += 1
        self.no_dia += 1
        self.total += 1
        self._gravar_conta()  # grava antes de pedir: falha de rede não devolve a cota
        inicio = self._relogio()
        try:
            codigo, corpo, tipo = self._pedir(url, self._cabecalhos(), TEMPO_MAX)
            codigo = codigo or 0  # resposta sem código HTTP: trata como falha, não como sucesso
        except Exception as erro:  # noqa: BLE001 - rede, TLS, tempo esgotado
            self.travado = f"a leitura falhou ({type(erro).__name__}: {erro})"
            log.warning("Falha ao ler %s: %s", url, erro)
            raise ErroProtheus(f"Não consegui ler {o_que}: {type(erro).__name__}: {erro}. Parei as leituras deste turno.") from None
        levou = self._relogio() - inicio
        if 300 <= codigo < 400:
            # Desvio não é seguido: seguir mandaria usuário e senha para onde o servidor quisesse.
            self.travado = f"o endereço respondeu um desvio ({codigo})"
            raise ErroProtheus(
                f"O endereço respondeu um desvio (HTTP {codigo}) ao ler {o_que}, e eu não sigo desvio: seguir mandaria a credencial para um host "
                "que o usuário não configurou. Parei as leituras deste turno. Diga a ele para conferir JARVIS_PROTHEUS_URL (e se há proxy ou "
                "portal de rede na frente do Protheus)."
            )
        if codigo >= 500:
            self.travado = f"o Protheus respondeu {codigo}"
            raise ErroProtheus(
                f"O Protheus respondeu {codigo} ao ler {o_que}, em {levou:.1f} s. Parei as leituras deste turno e não vou insistir: "
                f"avise o usuário. Corpo: {self._corpo_curto(corpo, tipo)[:300]}"
            )
        if levou > LENTO:
            self.travado = f"a resposta levou {levou:.1f} s (acima de {LENTO:.0f} s)"
        linhas = [
            f"{o_que} — GET {url}",
            f"HTTP {codigo}, {len(corpo)} bytes, {levou:.1f} s, empresa/filial {self.tenant}",
        ]
        if codigo == 401:
            linhas.append(
                "401: a credencial foi recusada. Pode não ser a senha: se o appserver estiver com SECURITY=1, o REST exige token OAuth2 "
                "(api/oauth2/v1/token) em vez de usuário e senha. Diga isso ao usuário, é com a consultoria."
            )
        elif codigo == 404:
            linhas.append("404: o ambiente não publica esta rota. Para o fwmodel, pode ser que a rotina não exponha modelo nesta versão.")
        elif codigo >= 400:
            linhas.append(f"{codigo}: o pedido foi recusado. Confira com o usuário a empresa/filial (tenantId) e o que a consultoria publicou.")
        dado = self._json(corpo, tipo)
        if dado is None and corpo:
            linhas.append(
                f"O corpo não é JSON que eu consiga interpretar (tipo '{tipo or 'não informado'}', {len(corpo)} bytes"
                + (", lido só o primeiro mega" if len(corpo) >= MAX_RESPOSTA else "")
                + "): mascarei pelo nome dos campos no próprio texto, o que reescreve espaçamento e fechamento de tag. "
                "Trate o corpo abaixo como aproximado e não culpe o ambiente por diferença de formatação."
            )
        estrutura = self._estrutura(dado)
        if estrutura:
            linhas += ["Estrutura:", *[f"  {linha}" for linha in estrutura]]
        linhas += ["Corpo:", self._corpo_curto(corpo, tipo, dado)]
        if self.travado:
            linhas.append(f"Parei as leituras deste turno: {self.travado}.")
        linhas.append(DE_FORA)
        return "\n".join(linhas)

    def _json(self, corpo: bytes, tipo: str) -> Any:
        if "json" not in tipo.lower() and corpo.lstrip()[:1] not in (b"{", b"["):
            return None
        try:
            return json.loads(corpo)
        except (ValueError, RecursionError):
            # Corpo aninhado fundo demais derruba o leitor de JSON: vale como corpo que não se
            # interpreta, e o mascaramento por nome de campo no texto cuida dele.
            return None

    def _estrutura(self, dado: Any) -> list[str]:
        if dado is None:
            return []
        try:
            return resumir(mascarar(dado))[:120]
        except Exception:  # noqa: BLE001 - corpo estranho: fica só o corpo cortado
            log.exception("Não consegui resumir a estrutura da resposta")
            return []

    def _corpo_curto(self, corpo: bytes, tipo: str, dado: Any = None) -> str:
        dado = self._json(corpo, tipo) if dado is None else dado
        if dado is not None:
            try:
                texto = json.dumps(mascarar(dado), ensure_ascii=False, indent=1)
            except Exception:  # noqa: BLE001 - JSON esquisito: cai no caminho de texto, que também mascara
                log.exception("Não consegui mascarar o corpo como JSON")
                texto = mascarar_bruto(corpo.decode("utf-8", "replace"))
        else:
            # Corta ANTES de mascarar: o mascaramento por expressão custa por letra, e um corpo de
            # um mega com um trecho longo sem pontuação (hash, base64, stack) levaria horas de CPU.
            texto = mascarar_bruto(corpo[:MAX_PARA_MASCARAR].decode("utf-8", "replace"))
        texto = "".join(" " if c < " " and c not in "\n\t" else c for c in texto)
        return texto if len(texto) <= MAX_CORPO else texto[: MAX_CORPO - 1] + "…"
