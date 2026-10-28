"""Painel do Jarvis: uma janela própria que mostra o que ele está fazendo e o quanto já sabe.

A janela é uma página local (painel.html) aberta no navegador em modo aplicativo, sem abas
nem barra de endereço. Quem desenha é a página; este módulo só conta para ela o que está
acontecendo, por um fluxo de eventos:

- o estado: em espera, ouvindo, entendendo, pensando, agindo ou falando;
- o volume do microfone enquanto ele ouve, e o contorno da voz enquanto ele fala;
- as legendas (o que o usuário disse e o que o Jarvis respondeu);
- o conhecimento: cada fato, preferência e lição da memória e o número de conversas. A rede
  neural da página é desenhada a partir disso e cresce quando chega um item novo;
- os trabalhos de programação em segundo plano e o passo em que cada um está.

A página também tem uma caixa de texto: o que for digitado nela vai para a conversa, como
um pedido digitado no terminal (mas só como conversa: comandos como "sair" não valem ali).

Os ajustes da página (cor, tema, movimento e a voz: ligada ou não, qual e a que velocidade) são
escolhidos nela e guardados aqui, em um arquivo: o endereço da página muda a cada execução,
então o navegador não tem onde lembrar deles. Quem liga e desliga a voz de verdade é quem
criou o painel, avisado por `ao_aparencia`.

O Jarvis também pode abrir cartões na página (`paineis`): previsão, lista, notícia, números.
O que vai para eles passa por um filtro: só os campos que a página sabe desenhar, com tamanho
limitado, e nenhum endereço de imagem (a página não busca nada fora).

O servidor só escuta nesta máquina (127.0.0.1) e exige um código secreto no endereço,
sorteado a cada execução. O pedido digitado e a troca de aparência só são aceitos se vierem
da própria página (mesma origem, corpo em JSON), o que barra outras páginas abertas no
navegador; e continua valendo o que vale para qualquer pedido: comando de terminal e skill
de fora só rodam com o "s" digitado no terminal.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import secrets
import sys
import threading
import time
import unicodedata
import webbrowser
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jarvis import __version__
from jarvis.janelas import TITULO_DO_PAINEL
from jarvis.memoria import CATEGORIAS, Memoria
from jarvis.registro import Registro

log = logging.getLogger(__name__)

PAGINA = Path(__file__).with_name("painel.html")
TITULO_DA_JANELA = TITULO_DO_PAINEL  # o controle de janelas ignora janelas com este título (ver janelas.py)
TAMANHO_DA_JANELA = (960, 620)  # o desenho da página é deitado

ESTADOS = ("espera", "ouvindo", "entendendo", "pensando", "agindo", "falando")
FASES = ("ouvindo", "entendendo", "pensando", "agindo")  # o que a conversa informa; "falando" vem do som
MAX_LEGENDA = 600
PASSO_DO_ENVELOPE = 0.04  # segundos por ponto do contorno da voz
LETRAS_POR_SEGUNDO = 16.0  # ritmo de leitura, para "falar" um texto quando não há som

_FILA_MAX = 400  # eventos esperando um navegador que parou de ler; passou disso, a conexão é encerrada
MAX_PEDIDO = 2000  # letras de um pedido digitado na página
MAX_CORPO = 6 * MAX_PEDIDO + 200  # bytes do corpo que traz o pedido (em JSON, uma letra pode ocupar até seis: \u00e3)
MAX_DESCARTE = 1 << 20  # bytes de um corpo recusado que ainda são lidos e jogados fora, para a recusa chegar inteira a quem mandou

POLITICA_DA_PAGINA = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

# Os ajustes da página. Só valem estes nomes: é o que a página sabe desenhar e o que vai para o arquivo.
APARENCIAS: dict[str, tuple[str, ...]] = {
    "cor": ("azul", "ciano", "verde", "violeta", "ambar", "vermelho"),
    "tema": ("escuro", "claro"),
    "movimento": ("lento", "medio", "rapido"),
}
VELOCIDADE_MIN, VELOCIDADE_MAX = 0.6, 1.6  # a régua "Velocidade" da página
APARENCIA_PADRAO: dict[str, Any] = {"cor": "azul", "tema": "escuro", "movimento": "medio", "voz": True, "voz_nome": "", "velocidade": 1.03}
MAX_APARENCIA = 400  # bytes do corpo que traz uma troca de ajuste
MAX_VOZES, MAX_NOME_DE_VOZ = 60, 80

# Os cartões que o Jarvis abre na página.
MAX_PAINEIS = 3
VIDA_DOS_PAINEIS = 60.0  # segundos: a página fecha os cartões sozinha depois disso
ICONES_DOS_PAINEIS = (
    "sol", "nublado", "chuva", "tempestade", "neve", "vento", "agenda", "noticias", "email",
    "seguranca", "analise", "sistema", "musica", "localizacao", "financas", "padrao",
)  # fmt: skip
_TEXTOS_DOS_PAINEIS = {  # campo de texto: tamanho máximo
    "categoria": 120, "titulo": 120, "badge": 40, "destaque": 40, "sub": 120, "imagem": 120,
    "manchete": 120, "quando": 40, "texto": 400, "fonte": 120, "acao": 40,
}  # fmt: skip
MAX_LINK = 300


def limpar_pedido(bruto: object) -> str:
    """O texto digitado na página, pronto para a conversa: sem caracteres de controle e com os espaços aparados.

    Devolve "" para o que não é texto, é vazio ou passa do tamanho.
    """
    if not isinstance(bruto, str):
        return ""
    visivel = "".join(" " if letra in "\r\n\t" else letra for letra in bruto if letra in "\r\n\t" or unicodedata.category(letra)[0] != "C")
    texto = " ".join(visivel.split())
    return texto if len(texto) <= MAX_PEDIDO else ""


def validar_aparencia(bruto: object, vozes: Sequence[str] = ()) -> dict[str, Any] | None:
    """O que a página pediu para mudar nos ajustes, se for tudo conhecido; senão, None.

    Aceita uma parte dos ajustes ({"cor": "verde"}), nunca um nome ou um valor de fora do combinado:
    cor, tema e movimento das listas fixas; `voz` verdadeiro ou falso; `voz_nome` vazio (a voz padrão)
    ou uma das `vozes` oferecidas; `velocidade` um número dentro da régua, guardado com duas casas.
    """
    if not isinstance(bruto, dict) or not bruto:
        return None
    limpa: dict[str, Any] = {}
    for nome, valor in bruto.items():
        if nome in APARENCIAS:
            if not isinstance(valor, str) or valor not in APARENCIAS[nome]:
                return None
        elif nome == "voz":
            if not isinstance(valor, bool):
                return None
        elif nome == "voz_nome":
            if not isinstance(valor, str) or (valor != "" and valor not in vozes):
                return None
        elif nome == "velocidade":
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
                return None
            if not VELOCIDADE_MIN <= valor <= VELOCIDADE_MAX:
                return None
            valor = round(float(valor), 2)
        else:
            return None
        limpa[nome] = valor
    return limpa


def ler_aparencia(arquivo: Path | None, vozes: Sequence[str] = ()) -> dict[str, Any]:
    """Os ajustes guardados. Arquivo ausente ou ilegível dá no padrão; de um arquivo bom, só vale o que é conhecido."""
    aparencia = dict(APARENCIA_PADRAO)
    if arquivo is None:
        return aparencia
    try:
        guardada = json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return aparencia
    except (OSError, ValueError, RecursionError):
        log.warning("Não consegui ler os ajustes do painel em %s; uso os de fábrica", arquivo)
        return aparencia
    if isinstance(guardada, dict):
        for nome in APARENCIA_PADRAO:
            if nome in guardada:
                valido = validar_aparencia({nome: guardada[nome]}, vozes)  # uma voz que não existe mais volta para a padrão
                if valido is not None:
                    aparencia.update(valido)
    return aparencia


def _texto_de_painel(bruto: object, teto: int) -> str:
    """Um texto de cartão: só o que se vê, em uma linha, até `teto` letras. Número vira texto; o resto vira ""."""
    if isinstance(bruto, bool) or not isinstance(bruto, (str, int, float)):
        return ""
    if isinstance(bruto, float) and not math.isfinite(bruto):
        return ""
    visivel = "".join(" " if letra in "\r\n\t" else letra for letra in str(bruto)[: teto * 4] if letra in "\r\n\t" or unicodedata.category(letra)[0] != "C")
    return " ".join(visivel.split())[:teto].strip()


def _pares_de_painel(bruto: object, quantos: int, teto_r: int, teto_v: int) -> list[dict[str, str]]:
    """Linhas rótulo/valor (os "itens" e a "lista" de um cartão)."""
    pares = []
    for item in bruto if isinstance(bruto, list) else []:
        if not isinstance(item, dict):
            continue
        r = _texto_de_painel(item.get("r", item.get("rotulo")), teto_r)
        v = _texto_de_painel(item.get("v", item.get("valor")), teto_v)
        if r or v:
            pares.append({"r": r, "v": v})
        if len(pares) == quantos:
            break
    return pares


def limpar_paineis(bruto: object) -> list[dict[str, Any]]:
    """Os cartões prontos para a página: no máximo três, só com os campos que ela desenha, cada um no seu limite.

    Fica de fora o que não é do combinado, e em especial qualquer endereço de imagem: a página não
    busca nada fora. O `link` de uma notícia só passa se for http(s); ele só é aberto se o usuário clicar.
    """
    cartoes: list[dict[str, Any]] = []
    for item in bruto if isinstance(bruto, list) else []:
        if not isinstance(item, dict):
            continue
        cartao: dict[str, Any] = {}
        for campo, teto in _TEXTOS_DOS_PAINEIS.items():
            texto = _texto_de_painel(item.get(campo), teto)
            if texto:
                cartao[campo] = texto
        for campo, quantos, teto_r, teto_v in (("itens", 8, 40, 40), ("lista", 6, 120, 200)):
            pares = _pares_de_painel(item.get(campo), quantos, teto_r, teto_v)
            if pares:
                cartao[campo] = pares
        link = item.get("link")
        if isinstance(link, str) and len(link) <= MAX_LINK and link.lower().startswith(("http://", "https://")):
            if all(unicodedata.category(letra)[0] not in "CZ" for letra in link) and not any(letra in link for letra in "\"'<>`\\"):
                cartao["link"] = link
        barra = item.get("barra")
        if isinstance(barra, (int, float)) and not isinstance(barra, bool) and math.isfinite(barra):
            cartao["barra"] = max(0, min(100, round(float(barra), 1)))
        if item.get("icone") in ICONES_DOS_PAINEIS:
            cartao["icone"] = item["icone"]
        grafico, rotulos = item.get("grafico"), item.get("graficoRotulos")
        if isinstance(grafico, list):
            rotulos = rotulos if isinstance(rotulos, list) else []
            pontos = [  # cada número com o seu rótulo: tirar um ponto torto não desalinha os outros
                (round(float(n), 2), _texto_de_painel(rotulos[k] if k < len(rotulos) else "", 12))
                for k, n in enumerate(grafico[:48])
                if isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)
            ][:24]
            if len(pontos) > 1:
                cartao["grafico"] = [n for n, _ in pontos]
                if any(r for _, r in pontos):
                    cartao["graficoRotulos"] = [r for _, r in pontos]
        previsao = []
        for dia in item.get("previsao") if isinstance(item.get("previsao"), list) else []:
            if not isinstance(dia, dict):
                continue
            um = {campo: _texto_de_painel(dia.get(campo), 12) for campo in ("dia", "max", "min")}
            if dia.get("icone") in ICONES_DOS_PAINEIS:
                um["icone"] = dia["icone"]
            if any(um.values()):
                previsao.append(um)
            if len(previsao) == 8:
                break
        if previsao:
            cartao["previsao"] = previsao
        if item.get("escuro") is True:
            cartao["escuro"] = True
        if cartao and cartao != {"escuro": True}:
            cartoes.append(cartao)
        if len(cartoes) == MAX_PAINEIS:
            break
    return cartoes


def gravar_aparencia(arquivo: Path, aparencia: dict[str, Any]) -> bool:
    """Grava por inteiro ou não grava: escreve ao lado e troca de nome. Falha vira registro, não erro."""
    provisorio = arquivo.with_name(arquivo.name + ".tmp")
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        provisorio.write_text(json.dumps(aparencia, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(provisorio, arquivo)
        return True
    except OSError:
        log.exception("Não consegui guardar a aparência do painel em %s", arquivo)
        try:
            provisorio.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def conhecimento(memoria: Memoria, registro: Registro) -> dict[str, Any]:
    """O retrato do que o Jarvis sabe: um item por anotação da memória e o número de conversas."""
    itens = [
        {"c": categoria, "n": item.nome, "t": item.titulo}
        for categoria in CATEGORIAS
        for item in memoria.listar(categoria)
    ]
    try:
        conversas = registro.total()
    except Exception:  # noqa: BLE001 - o painel nunca derruba a conversa
        log.exception("Não consegui contar as conversas para o painel")
        conversas = 0
    return {"itens": itens, "conversas": conversas}


def contorno_da_voz(audio: Any, taxa: int, passo: float = PASSO_DO_ENVELOPE) -> list[float]:
    """Volume do áudio a cada `passo` segundos, de 0 a 1: é o que faz a rede pulsar junto com a fala."""
    import numpy as np

    amostras = np.asarray(audio, dtype=np.float32).reshape(-1)
    por_ponto = max(1, int(taxa * passo))
    pontos = len(amostras) // por_ponto
    if pontos == 0:
        return []
    blocos = amostras[: pontos * por_ponto].reshape(pontos, por_ponto)
    volume = np.sqrt((blocos**2).mean(axis=1))
    teto = float(volume.max())
    if teto <= 0:
        return [0.0] * pontos
    return [round(float(v), 2) for v in np.clip(volume / teto, 0.0, 1.0)]


def contorno_de_texto(texto: str, passo: float = PASSO_DO_ENVELOPE) -> list[float]:
    """Sem som (modo texto), um contorno com o ritmo da leitura: sobe nas sílabas, cai nas pausas."""
    pontos_por_letra = 1.0 / (LETRAS_POR_SEGUNDO * passo)
    contorno: list[float] = []
    for i, letra in enumerate(texto[:MAX_LEGENDA]):
        if letra in ".!?;:":
            contorno += [0.0] * int(pontos_por_letra * 5)
        elif letra in ", \n":
            contorno += [0.15] * max(1, int(pontos_por_letra))
        else:
            forte = 1.0 if letra.lower() in "aeiouáéíóúâêôãõ" else 0.55
            contorno += [round(forte * (0.75 + 0.25 * ((i * 7919) % 10) / 9), 2)] * max(1, int(pontos_por_letra))
    return contorno


class PainelMudo:
    """O painel desligado: aceita os mesmos avisos e não faz nada. Evita um `if` em cada chamada."""

    endereco = ""
    ao_aparencia: Callable[[dict[str, Any]], None] | None = None

    @property
    def aparencia(self) -> dict[str, Any]:
        return dict(APARENCIA_PADRAO)

    def fase(self, nome: str | None, detalhe: str = "") -> None: ...
    def som(self, audio: Any = None, taxa: int = 0) -> None: ...
    def silencio(self) -> None: ...
    def falar_texto(self, texto: str) -> None: ...
    def nivel(self, valor: float) -> None: ...
    def legenda(self, quem: str, texto: str) -> None: ...
    def conhecimento(self, retrato: dict[str, Any]) -> None: ...
    def trabalhos(self, lista: list[dict[str, Any]]) -> None: ...
    def paineis(self, lista: list[dict[str, Any]]) -> int:
        return 0

    def aceitar_pedidos(self, receber: Callable[[str], str]) -> None: ...
    def fechar(self) -> None: ...


MUDO = PainelMudo()


class Painel:
    """Guarda o retrato atual e o distribui a quem estiver com a página aberta."""

    def __init__(self, atalho: str = "", com_voz: bool = False, arquivo: Path | None = None, vozes: Sequence[str] = ()) -> None:
        """`arquivo` é onde os ajustes escolhidos na página ficam guardados; sem ele, valem só enquanto o Jarvis roda.

        `vozes` são os nomes das vozes que a página oferece além da padrão. Depois de cada troca de ajuste
        aceita, `ao_aparencia(ajustes)` é chamada, se houver: é por ela que a voz de verdade é ligada,
        desligada, trocada ou muda de velocidade.
        """
        self._trava = threading.Lock()
        self._arquivo = arquivo
        self._vozes = tuple(dict.fromkeys(v for v in vozes if isinstance(v, str) and 0 < len(v) <= MAX_NOME_DE_VOZ))[:MAX_VOZES]
        self._trava_da_aparencia = threading.RLock()  # uma troca de cada vez, do retrato ao arquivo e ao aviso
        self.ao_aparencia: Callable[[dict[str, Any]], None] | None = None
        self._paineis: list[dict[str, Any]] = []
        self._paineis_em = 0.0
        self._assinantes: list[queue.Queue[str | None]] = []
        self._fase: str | None = None
        self._detalhe = ""
        self._falando = False
        self._retrato: dict[str, Any] = {
            "estado": "espera",
            "detalhe": "",
            "conhecimento": {"itens": [], "conversas": 0},
            "trabalhos": [],
            "voce": "",
            "jarvis": "",
            "atalho": atalho,
            "voz": com_voz,
            "versao": __version__,
            "titulo": TITULO_DA_JANELA,
            "digitar": False,
            "aparencia": ler_aparencia(arquivo, self._vozes),
            "vozes": list(self._vozes),
        }
        self._receber: Callable[[str], str] | None = None  # quem recebe o que é digitado na página
        self._relogio_de_texto: threading.Timer | None = None
        self._servidor: ThreadingHTTPServer | None = None
        self.codigo = secrets.token_urlsafe(18)
        self.endereco = ""

    # --- avisos da conversa e do som (podem vir de qualquer thread) -------------

    def fase(self, nome: str | None, detalhe: str = "") -> None:
        """Em que ponto a conversa está: None (esperando), ouvindo, entendendo, pensando ou agindo."""
        if nome is not None and nome not in FASES:
            raise ValueError(f"fase desconhecida: {nome}")
        with self._trava:
            self._fase, self._detalhe = nome, detalhe[:120]
            self._publicar_estado()

    def som(self, audio: Any = None, taxa: int = 0) -> None:
        """Uma frase começou a tocar. Com o áudio, a rede pulsa no contorno da voz."""
        contorno: list[float] = []
        if audio is not None and taxa:
            try:
                contorno = contorno_da_voz(audio, taxa)
            except Exception:  # noqa: BLE001 - sem contorno a página usa um ritmo genérico
                log.exception("Não consegui medir o contorno da voz")
        with self._trava:
            self._falando = True
            self._publicar_estado()
            self._enviar({"tipo": "voz", "contorno": contorno, "passo": PASSO_DO_ENVELOPE})

    def silencio(self) -> None:
        """Acabou a fala (ou ela foi interrompida)."""
        with self._trava:
            if self._relogio_de_texto is not None:
                self._relogio_de_texto.cancel()
                self._relogio_de_texto = None
            if self._falando:
                self._falando = False
                self._enviar({"tipo": "voz", "contorno": [], "passo": PASSO_DO_ENVELOPE, "fim": True})
                self._publicar_estado()

    def falar_texto(self, texto: str) -> None:
        """Modo texto: a rede "diz" a resposta pelo tempo que levaria para ler."""
        contorno = contorno_de_texto(texto)
        if not contorno:
            return
        with self._trava:
            if self._relogio_de_texto is not None:
                self._relogio_de_texto.cancel()
            self._falando = True
            self._publicar_estado()
            self._enviar({"tipo": "voz", "contorno": contorno, "passo": PASSO_DO_ENVELOPE})
            self._relogio_de_texto = threading.Timer(len(contorno) * PASSO_DO_ENVELOPE, self.silencio)
            self._relogio_de_texto.daemon = True
            self._relogio_de_texto.start()

    def nivel(self, valor: float) -> None:
        """Volume do microfone agora, de 0 a 1."""
        with self._trava:
            self._enviar({"tipo": "nivel", "v": round(max(0.0, min(1.0, float(valor))), 2)})

    def legenda(self, quem: str, texto: str) -> None:
        if quem not in ("voce", "jarvis"):
            raise ValueError(f"legenda de quem? {quem}")
        texto = " ".join(texto.split())[:MAX_LEGENDA]
        with self._trava:
            self._retrato[quem] = texto
            if quem == "voce":
                self._retrato["jarvis"] = ""  # pergunta nova: a resposta antiga sai da tela
            self._enviar({"tipo": "legenda", "quem": quem, "texto": texto})

    def conhecimento(self, retrato: dict[str, Any]) -> None:
        with self._trava:
            if retrato == self._retrato["conhecimento"]:
                return
            self._retrato["conhecimento"] = retrato
            self._enviar({"tipo": "conhecimento", **retrato})

    def trabalhos(self, lista: list[dict[str, Any]]) -> None:
        """Os trabalhos de programação em segundo plano: projeto, título, estado e o passo de agora."""
        with self._trava:
            if lista == self._retrato["trabalhos"]:
                return
            self._retrato["trabalhos"] = lista
            self._enviar({"tipo": "trabalhos", "lista": lista})

    def aceitar_pedidos(self, receber: Callable[[str], str]) -> None:
        """Liga a caixa de texto da página. O que for digitado nela chega a `receber(texto)`, de outra thread.

        `receber` devolve o que foi feito do pedido: "agora" (vai ser atendido já), "fila" (espera
        o pedido em andamento), "parou" (era uma ordem de parar) ou "cheio" (há pedidos demais esperando).
        """
        with self._trava:
            self._receber = receber
            self._retrato["digitar"] = True
            self._enviar({"tipo": "digitar", "sim": True})

    def paineis(self, lista: list[dict[str, Any]]) -> int:
        """Abre cartões na página (no máximo três), no lugar dos que estiverem abertos. Uma lista vazia fecha os que houver.

        A página os fecha sozinha depois de um minuto; quem abre a página nesse meio-tempo ainda os vê.
        Devolve quantos cartões foram para a página (o que não era do combinado fica de fora).
        """
        limpa = limpar_paineis(lista)
        with self._trava:
            self._paineis, self._paineis_em = limpa, time.monotonic()
            self._enviar({"tipo": "paineis", "lista": limpa})
        return len(limpa)

    @property
    def aparencia(self) -> dict[str, Any]:
        """Os ajustes de agora (uma cópia): cor, tema, movimento, voz, voz_nome e velocidade."""
        with self._trava:
            return dict(self._retrato["aparencia"])

    def _mudar_aparencia(self, mudancas: dict[str, Any]) -> dict[str, Any]:
        """Aplica o que a página pediu (já validado), avisa as outras páginas abertas, guarda e avisa `ao_aparencia`."""
        with self._trava_da_aparencia:
            with self._trava:
                nova = {**self._retrato["aparencia"], **mudancas}
                if nova == self._retrato["aparencia"]:
                    return nova
                self._retrato["aparencia"] = nova
                self._enviar({"tipo": "aparencia", **nova})
            if self._arquivo is not None:
                gravar_aparencia(self._arquivo, nova)
            avisar = self.ao_aparencia
            if avisar is not None:
                try:
                    avisar(dict(nova))  # fora da trava do painel: quem recebe pode demorar (trocar de voz, por exemplo)
                except Exception:  # noqa: BLE001 - o ajuste já vale na página; a falha de quem o aplica fica no registro
                    log.exception("Não consegui aplicar o ajuste escolhido no painel")
            return nova

    def _estado(self) -> str:
        if self._fase == "ouvindo":
            return "ouvindo"  # enquanto o usuário fala, nada passa na frente
        if self._falando:
            return "falando"
        return self._fase or "espera"

    def _publicar_estado(self) -> None:
        estado = self._estado()
        detalhe = self._detalhe if estado == self._fase else ""
        if (estado, detalhe) != (self._retrato["estado"], self._retrato["detalhe"]):
            self._retrato["estado"], self._retrato["detalhe"] = estado, detalhe
            self._enviar({"tipo": "estado", "estado": estado, "detalhe": detalhe})

    def _enviar(self, evento: dict[str, Any]) -> None:
        """Entrega a todos os navegadores ligados. Quem chama já está com a trava."""
        linha = json.dumps(evento, ensure_ascii=False)
        for fila in list(self._assinantes):
            try:
                fila.put_nowait(linha)
            except queue.Full:  # navegador travado: solta a conexão em vez de acumular
                self._assinantes.remove(fila)

    # --- quem assina (a página) ------------------------------------------------

    def assinar(self) -> tuple[queue.Queue[str | None], str]:
        """Uma fila nova, já com o retrato completo como primeiro evento."""
        fila: queue.Queue[str | None] = queue.Queue(maxsize=_FILA_MAX)
        with self._trava:
            idade = time.monotonic() - self._paineis_em
            vivos = self._paineis if idade < VIDA_DOS_PAINEIS else []
            primeiro = json.dumps({"tipo": "tudo", **self._retrato, "paineis": vivos, "paineis_idade": round(idade, 1) if vivos else 0}, ensure_ascii=False)
            self._assinantes.append(fila)
        return fila, primeiro

    def cancelar(self, fila: queue.Queue[str | None]) -> None:
        with self._trava:
            if fila in self._assinantes:
                self._assinantes.remove(fila)

    @property
    def assinantes(self) -> int:
        with self._trava:
            return len(self._assinantes)

    # --- servidor ----------------------------------------------------------------

    def iniciar(self) -> str:
        """Sobe o servidor local e devolve o endereço da página."""
        painel = self

        class Pedido(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # nada no terminal
                pass

            def _recusar(self, codigo: int) -> None:
                self._esgotar_corpo()
                self.send_response(codigo)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

            def _esgotar_corpo(self) -> None:
                """Lê e joga fora o corpo que veio com um pedido recusado, se for pequeno.

                No Windows, fechar a conexão com dados por ler derruba a resposta no caminho: o
                navegador veria uma conexão cortada em vez do motivo da recusa.

                O tamanho anunciado não serve de guia sozinho: é justamente quando ele vem vazio,
                torto ou negativo (o 411 e o 413) que o corpo chegou e ninguém o leu. Nesse caso
                esvazia o que já está no soquete, por tempo curto, sem acreditar no cabeçalho.
                """
                if self.command != "POST" or getattr(self, "_corpo_lido", False):
                    return
                self._corpo_lido = True
                bruto = (self.headers.get("Content-Length") or "").strip()
                anunciado = bruto.isdigit() and 0 < int(bruto) <= MAX_DESCARTE
                if bruto.isdigit() and int(bruto) == 0:
                    return  # disse que não há corpo e o cabeçalho está são: nada a esvaziar
                if bruto.isdigit() and int(bruto) > MAX_DESCARTE:
                    return  # abuso declarado: aí a conexão cai mesmo
                falta = int(bruto) if anunciado else MAX_DESCARTE
                try:
                    self.connection.settimeout(2 if anunciado else 0.3)  # sem tamanho, não espera por corpo que não vem
                    while falta > 0:
                        # `read1` com tamanho desconhecido: devolve o que já chegou, sem ficar
                        # preso esperando completar os bytes que ninguém prometeu mandar.
                        lido = self.rfile.read(min(falta, 65536)) if anunciado else self.rfile.read1(min(falta, 65536))
                        if not lido:
                            break
                        falta -= len(lido)
                except (ValueError, OSError):
                    pass

            def _para_ca(self) -> bool:
                # Só atende se o pedido veio para este endereço: barra páginas da internet que
                # apontam um nome de domínio para 127.0.0.1 para ler o que o painel mostra.
                porta = self.server.server_address[1]
                return self.headers.get("Host") in (f"127.0.0.1:{porta}", f"localhost:{porta}")

            def do_GET(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
                if not self._para_ca():
                    return self._recusar(403)
                if self.path == f"/{painel.codigo}/":
                    return self._pagina()
                if self.path == f"/{painel.codigo}/eventos":
                    return self._eventos()
                return self._recusar(404)

            def do_POST(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
                """O que a página manda: um pedido digitado ou uma troca de aparência. Mais nada entra por aqui."""
                porta = self.server.server_address[1]
                if not self._para_ca():
                    return self._recusar(403)
                caminho = self.path.encode()
                if secrets.compare_digest(caminho, f"/{painel.codigo}/pedido".encode()):
                    tratar, teto = self._pedido, MAX_CORPO
                elif secrets.compare_digest(caminho, f"/{painel.codigo}/aparencia".encode()):
                    tratar, teto = self._aparencia, MAX_APARENCIA
                else:
                    return self._recusar(404)
                # Tem de vir da própria página: outra página aberta no navegador não manda ordem ao Jarvis.
                # (O corpo em JSON reforça: de outra origem, o navegador nem envia sem pedir licença antes.)
                if self.headers.get("Origin") not in (f"http://127.0.0.1:{porta}", f"http://localhost:{porta}"):
                    return self._recusar(403)
                if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                    return self._recusar(415)
                try:
                    tamanho = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    return self._recusar(411)
                if tamanho < 0 or tamanho > teto:
                    return self._recusar(413)
                try:
                    self.connection.settimeout(5)  # corpo que não chega não prende a thread
                    self._corpo_lido = True
                    dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
                except (ValueError, OSError, RecursionError):  # o último: milhares de colchetes aninhados
                    return self._recusar(400)
                tratar(dados)

            def _pedido(self, dados: object) -> None:
                """Um pedido digitado na página, a caminho da conversa."""
                bruto = dados.get("texto") if isinstance(dados, dict) else None
                if isinstance(bruto, str) and len(bruto) > MAX_CORPO:
                    return self._recusar(413)
                texto = limpar_pedido(bruto)
                if not texto:
                    return self._recusar(413 if isinstance(bruto, str) and len(" ".join(bruto.split())) > MAX_PEDIDO else 400)
                receber = painel._receber
                if receber is None:
                    return self._recusar(503)
                try:
                    quando = receber(texto)
                except Exception:  # noqa: BLE001 - a conversa não pôde receber; a página avisa quem digitou
                    log.exception("Não consegui entregar à conversa o pedido digitado no painel")
                    return self._recusar(503)
                if quando == "cheio":
                    return self._recusar(429)
                self._responder({"quando": quando})

            def _aparencia(self, dados: object) -> None:
                """Um ajuste trocado na página (cor, tema, movimento, voz): só nomes e valores do combinado."""
                mudancas = validar_aparencia(dados, painel._vozes)
                if mudancas is None:
                    return self._recusar(400)
                self._responder(painel._mudar_aparencia(mudancas))

            def _responder(self, dados: dict[str, Any]) -> None:
                corpo = json.dumps(dados).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(corpo)

            def _pagina(self) -> None:
                try:
                    corpo = PAGINA.read_bytes()
                except OSError:
                    return self._recusar(500)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                # A página não carrega nada de fora nem fala com outro endereço. As letras vêm dentro dela (font-src data:).
                self.send_header("Content-Security-Policy", POLITICA_DA_PAGINA)
                self.end_headers()
                self.wfile.write(corpo)

            def _eventos(self) -> None:
                fila, primeiro = painel.assinar()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(f"retry: 1500\ndata: {primeiro}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            linha = fila.get(timeout=10)
                        except queue.Empty:
                            self.wfile.write(b": vivo\n\n")  # mantém a conexão e percebe navegador fechado
                            self.wfile.flush()
                            continue
                        if linha is None:
                            self.wfile.write(b'data: {"tipo":"fim"}\n\n')
                            self.wfile.flush()
                            return
                        self.wfile.write(f"data: {linha}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass  # o navegador fechou
                finally:
                    painel.cancelar(fila)

        class Servidor(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                # Navegador que fecha a conexão no meio não é assunto para o terminal do usuário.
                log.debug("Conexão do painel encerrada com erro", exc_info=True)

        servidor = Servidor(("127.0.0.1", 0), Pedido)
        servidor.daemon_threads = True
        self._servidor = servidor
        threading.Thread(target=servidor.serve_forever, daemon=True, name="jarvis-painel").start()
        self.endereco = f"http://127.0.0.1:{servidor.server_address[1]}/{self.codigo}/"
        return self.endereco

    def fechar(self) -> None:
        with self._trava:
            if self._relogio_de_texto is not None:
                self._relogio_de_texto.cancel()
            filas = list(self._assinantes)
            for fila in filas:
                try:
                    fila.put_nowait(None)  # avisa a página de que o Jarvis encerrou
                except queue.Full:
                    pass
        # Um instante para o aviso sair antes de o programa terminar; sem ele a página só
        # perceberia pela queda da conexão.
        limite = time.monotonic() + 0.4
        while self._servidor is not None and time.monotonic() < limite and any(not fila.empty() for fila in filas):
            time.sleep(0.02)
        servidor, self._servidor = self._servidor, None
        if servidor is not None:
            threading.Thread(target=servidor.shutdown, daemon=True).start()


def abrir_janela(
    endereco: str,
    iniciar: Callable[..., Any] | None = None,
    navegar: Callable[[str], bool] = webbrowser.open,
    sistema: str = sys.platform,
) -> str:
    """Abre a página como janela própria (modo aplicativo do Chrome ou do Edge). Devolve como abriu.

    Sem Chrome nem Edge, ou fora do Windows, cai no navegador padrão; se nem isso der, devolve "".
    """
    if sistema == "win32":
        iniciar = iniciar or os.startfile  # type: ignore[attr-defined]
        largura, altura = TAMANHO_DA_JANELA
        for navegador in ("chrome", "msedge"):
            try:
                iniciar(navegador, arguments=f"--app={endereco} --window-size={largura},{altura}")
                return navegador
            except OSError:
                continue
    try:
        return "navegador padrão" if navegar(endereco) else ""
    except Exception:  # noqa: BLE001
        return ""
