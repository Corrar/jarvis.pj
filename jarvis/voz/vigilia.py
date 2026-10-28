"""Vigília: o microfone fica aberto e o Jarvis acorda quando ouve o próprio nome.

Tudo acontece nesta máquina. O som passa por três etapas:

1. `Cortador` separa a fala do silêncio pelo volume. Silêncio não custa nada: nada é
   transcrito enquanto ninguém fala.
2. Cada fala é transcrita assim que dá para dizer o que ela é: meio segundo depois de uma
   fala curta parar, ou aos três segundos de uma comprida. Se não começa com a palavra de
   ativação, é descartada: não é gravada em disco nem sai da máquina.
3. Se começa, o que vem depois da palavra é o pedido ("Jarvis, abre o YouTube"). Se a pessoa
   disse só "Jarvis", o Jarvis responde ("Sim?") e a fala seguinte é o pedido.

A vigília segue ouvindo enquanto o Jarvis fala. Ela sabe o volume do que ele toca a cada
instante e aprende com que volume isso chega ao microfone: a voz dele não é fala de
ninguém, e só uma voz bem mais alta que ela é ouvida. É assim que "Jarvis, para" corta uma
resposta.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from jarvis.voz.escuta import GANHO_DO_NIVEL, TAXA, reamostrar

log = logging.getLogger(__name__)

PALAVRA_PADRAO = "jarvis"
QUADRO = 0.03  # segundos; o volume é medido em pedaços deste tamanho
JANELA = 3.0  # segundos do começo de cada trecho transcritos para procurar a palavra
ESPERA_DO_PEDIDO = 10.0  # segundos para começar a falar o pedido, contados de quando ele começa a responder ao nome
ATRASO_MAXIMO = 20.0  # segundos de som esperando na fila; passou disso, o atrasado é descartado
CALADO_PARA_RESPONDER = 0.3  # segundos sem voz no microfone para responder ao nome sem falar por cima de ninguém
FIM_DE_FALA = 1.0  # segundos de silêncio que encerram uma fala (o mesmo do Cortador): ela acabou esse tanto antes de ser entregue
PAUSA_CEDO = 0.45  # segundos de silêncio com que uma fala curta é olhada antes do fim (a `pausa_cedo` do Cortador)
ANTES_DA_FALA = 0.3  # segundos de som de antes que vêm no começo de cada trecho (o `antes` do Cortador)
CAUDA_DA_FALA = 0.5  # segundos de silêncio que ficam no fim de uma fala entregue (a `cauda` do Cortador)
ECO_DA_RESPOSTA = 0.8  # segundos depois de ele terminar a resposta ao nome em que o que começa a soar pode ser ela, voltando da sala
MAIS_RESPOSTAS = 1  # quantas vezes ele responde de novo ao mesmo chamado, se a pessoa repetir o nome
FOLGA_DEPOIS_DE_ENGANO = 10.0  # segundos sem se calar de novo depois de se calar à toa; dobra a cada engano seguido
FOLGA_MAXIMA = 60.0
PALAVRAS_ANTES_DO_NOME = 8  # numa fala por cima da voz do Jarvis, até onde o nome pode estar (antes dele vem o resto da frase do Jarvis)
LIMPO_MINIMO = 0.3  # segundos de fala, depois de ele se calar, para valer a pena ver se mandam parar
PALAVRAS_DE_PARADA = 4  # até quantas palavras tem uma ordem de parar dita por cima dele ("pode parar por favor")
# O que pode vir antes do nome sem deixar de ser um chamado: "ei, Jarvis", "bom dia, Jarvis".
CHAMADAS = frozenset({"ei", "hey", "ok", "okay", "oi", "ola", "alo", "oh", "ah", "opa", "fala"})
VOCATIVOS = ("ô", "ó")  # "ô, Jarvis" chama; "o Jarvis", sem acento, é alguém falando dele
# Chamadas de duas palavras. Só o par inteiro vale: "bom, Jarvis não funcionou" não é um chamado.
CHAMADAS_EM_PAR = frozenset({("bom", "dia"), ("boa", "tarde"), ("boa", "noite"), ("e", "ai")})


# --- a palavra de ativação ---------------------------------------------------------------------


def _simples(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def ler_palavras(bruto: str) -> tuple[str, ...]:
    """JARVIS_PALAVRA: 'jarvis' ou 'jarvis;jarves;sexta-feira'. Vazio vale o padrão."""
    # Só letras e números, como a transcrição é comparada: "Sexta-Feira" vira "sexta feira".
    limpas = (" ".join(re.findall(r"[a-z0-9]+", _simples(p))) for p in re.split(r"[;,]", bruto))
    palavras = tuple(dict.fromkeys(p for p in limpas if p))
    if any(len(p.replace(" ", "")) < 4 for p in palavras):
        raise ValueError("JARVIS_PALAVRA: cada palavra de ativação precisa de pelo menos 4 letras, para não acordar à toa.")
    return palavras or (PALAVRA_PADRAO,)


def _distancia(a: str, b: str) -> int:
    """Quantas letras é preciso trocar, tirar ou pôr para uma palavra virar a outra."""
    anterior = list(range(len(b) + 1))
    for i, letra in enumerate(a, 1):
        atual = [i]
        for j, outra in enumerate(b, 1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (letra != outra)))
        anterior = atual
    return anterior[-1]


def _parece(ouvido: str, palavra: str, colada: bool = False, curta: bool = False) -> bool:
    """A transcrição erra uma letra do nome ("jarves", "jarviz", "djarvis"); mais do que isso já é outra palavra."""
    if ouvido == palavra:
        return True
    if colada or curta or abs(len(ouvido) - len(palavra)) > 2:
        # Duas palavras coladas só valem se formarem o nome exato ("já vi" não é "jarvis"),
        # e nome curto não tem letra sobrando para errar ("nina" não é "mina").
        return False
    return _distancia(ouvido, palavra) <= (1 if len(palavra) < 8 else 2)


def _casa(simples: Sequence[str], inicio: int, palavra: str) -> int:
    """Quantas palavras do texto, a partir de `inicio`, formam a palavra de ativação (0 se não formam)."""
    partes = palavra.split()
    alvo = "".join(partes)
    curta = min(len(parte) for parte in partes) < 6  # "oi nina": cada palavra do nome conta por si
    for largura in dict.fromkeys((len(partes), 1, 2)):  # "jar vis": a transcrição às vezes parte o nome em dois
        junto = "".join(simples[inicio : inicio + largura])
        if inicio + largura <= len(simples) and _parece(junto, alvo, colada=largura > len(partes), curta=curta):
            return largura
    soltas = 0  # "J.A.R.V.I.S.": letra por letra
    while inicio + soltas < len(simples) and len(simples[inicio + soltas]) == 1:
        soltas += 1
    return soltas if soltas >= 3 and "".join(simples[inicio : inicio + soltas]) == alvo else 0


def frase_simples(texto: str) -> str:
    """Só as letras e os números, sem acento e em minúsculas: para comparar frases curtas."""
    return " ".join(re.findall(r"[a-z0-9]+", _simples(texto)))


def separar_pedido(texto: str, palavras: Sequence[str] = (PALAVRA_PADRAO,), solto: bool = False) -> str | None:
    """O pedido que vem depois da palavra de ativação.

    Devolve None se o texto não começa chamando o Jarvis, "" se a pessoa disse só o nome e,
    nos outros casos, o resto da frase como foi transcrito.

    `solto`: a fala foi gravada por cima da voz do próprio Jarvis, e a transcrição pode trazer,
    antes do nome, o fim da frase que ele dizia. Aí o nome vale também algumas palavras adiante.
    """
    pedacos = list(re.finditer(r"[^\W_]+", texto, flags=re.UNICODE))
    originais = [p.group().lower() for p in pedacos]
    simples = [_simples(o) for o in originais]
    # Uma chamada antes do nome: "ei, Jarvis", "ô Jarvis", "bom dia, Jarvis", "e aí, Jarvis". Ou duas simples: "ok, ei, Jarvis".
    if tuple(simples[:2]) in CHAMADAS_EM_PAR:
        puladas = 2
    else:
        puladas = 0
        while puladas < min(2, len(simples)) and (simples[puladas] in CHAMADAS or originais[puladas] in VOCATIVOS):
            puladas += 1
    for inicio in range(puladas, -1, -1):  # também sem pular: a palavra escolhida pode começar com uma chamada ("oi nina")
        for palavra in palavras:
            largura = _casa(simples, inicio, palavra)
            if largura:
                return texto[pedacos[inicio + largura - 1].end() :].strip(" \t\n,.;:!?…-–—\"'")
    if solto:
        for inicio in range(puladas + 1, min(len(simples), PALAVRAS_ANTES_DO_NOME)):
            for palavra in palavras:
                largura = _casa(simples, inicio, palavra)
                if largura:
                    return texto[pedacos[inicio + largura - 1].end() :].strip(" \t\n,.;:!?…-–—\"'")
    return None


# --- cortar a fala pelo silêncio ---------------------------------------------------------------

COMECO, PAUSA, FIM = "comeco", "pausa", "fim"  # os acontecimentos que o Cortador conta


class Cortador:
    """Recebe o som aos pedaços e conta o que aconteceu com a fala.

    `alimentar` devolve acontecimentos:
    - (COMECO, som): uma fala chegou a `janela` segundos e continua. Quem recebe olha o começo
      e responde com `aceitar()` (é um chamado: segue gravando o pedido) ou `dispensar()`
      (não é: o resto dessa fala é ignorado).
    - (PAUSA, som, falados): uma fala curta parou há meio segundo. Ainda pode continuar, mas já
      dá para ir vendo o que foi dito, em vez de esperar o silêncio inteiro. Quem recebe pode
      responder com `aceitar()`, `dispensar()`, `encerrar()` (era só o nome: a fala acaba aqui)
      ou não responder (espera o fim).
    - (FIM, som, conferido, falados): a fala terminou. `conferido` diz se ela passou por
      `aceitar()`. `falados` conta os quadros de fala: se é o mesmo da última PAUSA, nada foi
      dito depois dela.

    `tocado` é o volume do que o próprio Jarvis está tocando na caixa de som naquele instante
    (zero se está calado). A voz dele que volta pelo microfone não é fala de ninguém: o cortador
    aprende com que volume ela chega e só começa uma fala com quem passar bem acima disso. Com
    fone de ouvido ela nem chega, e qualquer voz é ouvida.

    O que é fala é decidido pelo volume, contra o fundo da sala:
    - O fundo é medido só fora das falas, pelos momentos mais altos dos últimos segundos. Com
      TV ou música, que sobem e descem, o limiar fica acima dos picos, não no meio deles.
    - Fala dispensada que não para (dez segundos sem um segundo de pausa) é fundo: TV, música,
      uma conversa comprida. O limiar sobe para o nível dela, e só uma voz mais alta chama.
    - Dentro de uma fala dispensada, uma voz bem mais alta que ela começa uma fala nova.
    - No pedido, silêncio é o que fica bem abaixo da voz de quem pediu, e há um teto de duração:
      o que vai para a conversa é o pedido, não o que a sala disse depois.
    """

    def __init__(
        self,
        taxa: int = TAXA,
        silencio: float = 1.0,  # segundos de silêncio que encerram a fala
        minimo: float = 0.25,  # fala mais curta que isso é estalo, não palavra
        janela: float = JANELA,  # segundos de fala que bastam para conferir se é um chamado
        fundo_max: float = 10.0,  # fala dispensada que passa disso sem pausa é som de fundo
        pedido_max: float = 15.0,  # teto de um pedido, contando o nome
        antes: float = 0.3,  # segundos guardados antes do começo, para não cortar a primeira sílaba
        piso: float = 0.004,  # volume abaixo do qual nada é fala, por mais silenciosa que seja a sala
        fator: float = 2.0,  # quantas vezes acima do fundo a fala precisa estar
        salto: float = 3.0,  # quantas vezes acima de uma fala dispensada outra voz precisa estar
    ) -> None:
        self.taxa = taxa
        self._quadro = max(1, int(taxa * QUADRO))
        self._silencio = max(1, round(silencio / QUADRO))
        self._minimo = max(1, round(minimo / QUADRO))
        self._janela = max(1, round(janela / QUADRO))
        self._fundo_max = max(1, round(fundo_max / QUADRO))
        self._pedido_max = max(1, round(pedido_max / QUADRO))
        self._cauda = max(1, round(0.5 / QUADRO))  # o que fica depois do último som forte: o fim de uma frase costuma ser baixo
        self._pausa = max(1, round(0.3 / QUADRO))  # calado há mais que isso: talvez a fala tenha acabado
        self._pausa_cedo = max(1, round(0.45 / QUADRO))  # calado há isso numa fala curta: já dá para conferir
        self._retomada = max(1, round(0.4 / QUADRO))  # pausa que basta para a fala seguinte, numa conversa dispensada, ser conferida de novo
        self._retomadas_max = 2  # quantas vezes isso vale na mesma conversa; depois, só com o segundo inteiro de silêncio
        self._aprender = max(1, round(0.6 / QUADRO))  # quadros dele falando sozinho até saber com que volume ele chega ao microfone
        self._sobra_do_eco = 2.0  # quantas vezes acima da voz dele alguém precisa falar para ser ouvido por cima
        self._tocado_min = 0.02  # som dele mais baixo que isso não serve para medir como ele chega ao microfone
        self._segundo = max(1, round(1.0 / QUADRO))
        self._piso, self._fator, self._salto = piso, fator, salto
        self._sobra: Any = None
        self._recentes: deque[Any] = deque(maxlen=max(1, round(antes / QUADRO)))  # o som logo antes de uma fala
        self._sala: deque[float] = deque(maxlen=max(1, round(3.0 / QUADRO)))  # volumes fora das falas: o fundo
        self._ultimos: deque[float] = deque(maxlen=3)  # os três últimos volumes fora das falas
        self._fundo = piso / fator
        self._medindo = max(1, round(1.0 / QUADRO))  # quadros que faltam para conhecer a sala; só na primeira abertura
        self._estado = "parado"  # parado, falando (ainda não conferida), pedido, ignorado
        self._trecho: list[Any] = []
        self._volumes: list[float] = []  # de cada quadro da fala em curso (na ignorada, só os últimos)
        self._duracao = 0  # quadros desde o começo da fala
        self._altos = 0  # quadros seguidos acima do limiar
        self._calados = 0
        self._falados = 0
        self._anunciado, self._anunciada_em = False, 0
        self._voz = 0.0  # o volume de quem fez o pedido
        self._ao_fundo = 0.0  # o volume da fala dispensada que segue ao fundo
        self._saltos = 0  # quadros seguidos bem acima dela
        self._por_cima: tuple[int, list[float]] | None = None  # a fala em curso nasceu por cima de um fundo dispensado: (duração dele, volumes)
        self._salto_em: int | None = None  # a fala foi anunciada antes da hora porque uma voz mais alta entrou neste quadro
        self._pausa_avisada = False  # a pausa em curso já foi anunciada
        self._antes = 0  # quantos quadros do começo do trecho são o som de antes da fala
        # A voz do próprio Jarvis no microfone. `_ganho` é quanto do volume que ele toca chega ao microfone
        # (perto de zero com fone de ouvido), aprendido das vezes em que ele fala e ninguém mais.
        self._ganho: float | None = None
        self._razoes: deque[float] = deque(maxlen=max(1, round(3.0 / QUADRO)))
        self._tocado = 0.0  # o volume do que ele está tocando agora
        self._calado_ha = 10**9  # quadros desde que ele tocou alguma coisa
        self._eco = 0.0  # quanto a voz dele deve estar somando, agora, ao barulho da sala
        self._sobre_eco = False  # a fala em curso começou com ele falando
        self._razoes_da_fala: list[float] = []  # o mesmo que `_razoes`, medido dentro da fala em curso
        self._razoes_passadas: list[float] = []  # e dentro da última fala que começou por cima dele
        self._depois_de_pausa = False  # a fala em curso é a continuação, depois de uma pausa, de uma conversa dispensada
        self._retomadas = 0  # quantas continuações desta conversa já foram conferidas
        self.sobre_ele = False  # o último acontecimento contado é de uma fala que começou por cima da voz do Jarvis
        self.limpo_desde = 0  # e, nessa fala, a amostra a partir da qual ele já não tocava (o que vem depois é só de quem falou)
        self._limpos = 0  # quadros da fala em curso desde o último em que ele tocava
        self._desde_a_medida = 0
        self.nivel = 0.0  # volume do último pedaço, de 0 a 1

    @property
    def em_fala(self) -> bool:
        """Há uma fala em curso que ainda pode virar pedido (a dispensada não conta)."""
        return self._estado in ("falando", "pedido")

    @property
    def limiar(self) -> float:
        return max(self._piso, self._fundo * self._fator)

    @property
    def sobre_o_jarvis(self) -> bool:
        """Há uma voz firme (um quarto de segundo) que começou por cima da fala do Jarvis."""
        return self._estado in ("falando", "pedido") and self._sobre_eco and self._falados >= self._minimo

    def encerrar(self) -> None:
        """A fala em curso já disse o que tinha a dizer (era só o nome): o que vier depois é outra fala."""
        if self._estado in ("falando", "pedido"):
            self._parar()

    def zerar(self) -> None:
        """Esquece a fala que estava no meio (o microfone foi fechado, ou o Jarvis ficou surdo). O fundo da sala fica."""
        self._sobra = None
        self._recentes.clear()
        self._por_cima = None
        self._parar()
        self._razoes_passadas = []

    def _parar(self, dispensada: bool = False) -> None:
        """A fala em curso acabou.

        Se ela nasceu por cima de um som de fundo dispensado, é para ele que se volta. Se era a
        continuação de uma conversa dispensada, só se volta a ela quando esta parte também foi
        dispensada: um chamado, ou um segundo de silêncio, encerram a conversa.
        """
        por_cima, self._por_cima = self._por_cima, None
        depois, self._depois_de_pausa = self._depois_de_pausa, False
        passou, calados = self._duracao, self._calados
        self._razoes_passadas = self._razoes_da_fala  # só tem o que medir na fala que começou por cima da voz dele
        self._razoes_da_fala = []
        self._estado = "parado"
        self._trecho, self._volumes = [], []
        self._duracao = self._altos = self._calados = self._falados = self._saltos = 0
        self._anunciado, self._salto_em, self._pausa_avisada, self._sobre_eco = False, None, False, False
        if por_cima is not None and (dispensada or not depois):
            self._estado = "ignorado"
            self._duracao, self._volumes = por_cima[0] + passou, por_cima[1]
            if depois:
                self._calados = calados  # o silêncio que já corria continua contando
        else:
            self._retomadas = 0  # a conversa acabou

    def engano(self) -> None:
        """A última fala que começou por cima da voz do Jarvis não era um chamado.

        Pode ter sido a própria voz dele, chegando mais alta do que se esperava (aumentaram o
        volume da caixa, ou ele saiu do fone para a caixa). O que foi medido nela entra na conta,
        e da próxima vez não passa. Chamado na hora em que se vê que não era um chamado.
        """
        import numpy as np

        # A fala pode ainda estar em curso (o aviso veio numa pausa ou na janela) ou já ter acabado.
        medidas = self._razoes_da_fala or self._razoes_passadas
        if medidas and self._ganho is not None:
            self._razoes.extend(medidas[-self._razoes.maxlen // 2 :])
            self._ganho = float(np.percentile(self._razoes, 95))
        self._razoes_passadas, self._razoes_da_fala = [], []

    def aceitar(self) -> None:
        """A fala anunciada é um chamado: segue gravando até o fim do pedido."""
        if self._estado != "falando":
            return
        import numpy as np

        self._salto_em = None
        # O volume de quem pede é o do meio dos quadros de fala até aqui. Não o das sílabas mais fortes:
        # o nome dito com ênfase faria o resto do pedido, em voz normal, passar por silêncio. E só os da fala:
        # o som de logo antes e o da pausa em curso são da sala, e puxariam esse volume para o barulho dela.
        fala = self._volumes[self._antes : len(self._volumes) - self._calados]
        fortes = [v for v in fala if v > self._corte()]
        self._voz = float(np.median(fortes)) if fortes else 0.0
        self._estado = "pedido"

    def dispensar(self) -> None:
        """A fala anunciada não é um chamado: o som dela não interessa mais."""
        if self._estado != "falando":
            return
        if self._salto_em is not None:
            # O que veio antes da voz mais alta não chamava. Ele vira o fundo, e a fala recomeça nela.
            import numpy as np

            corte = self._corte()
            fundo = self._volumes[: self._salto_em][-self._sala.maxlen :]
            fortes = [v for v in fundo if v > corte]
            inicio = max(0, self._salto_em - self._recentes.maxlen)
            self._por_cima = (self._salto_em, fundo)
            self._depois_de_pausa = False  # agora há um som de fundo soando por baixo da fala
            self._ao_fundo = float(np.median(fortes)) if fortes else self.limiar
            self._trecho, self._volumes = self._trecho[inicio:], self._volumes[inicio:]
            self._antes = self._salto_em - inicio
            self._duracao = len(self._trecho)
            self._falados, self._calados, self._altos, self._saltos = 3, 0, 3, 0
            self._anunciado, self._salto_em = False, None
            return
        if self._por_cima is not None:
            # Era um som por cima de um fundo já dispensado, e também não chamava: junta-se ao fundo,
            # que passa a ter o volume dele (senão o mesmo som "saltaria" de novo em seguida).
            import numpy as np

            altos = [v for v in self._volumes if v > self._corte()]
            self._por_cima = (self._por_cima[0], self._volumes[-self._sala.maxlen :])
            self._parar(dispensada=True)
            if altos:
                self._ao_fundo = max(self._ao_fundo, float(np.median(altos)))
            return
        self._estado = "ignorado"
        self._trecho = []
        self._volumes = self._volumes[-self._sala.maxlen :]
        self._ao_fundo, self._saltos = 0.0, 0

    def _acima_dele(self) -> float:
        """O volume que alguém precisa passar para ser ouvido por cima da voz do Jarvis (zero se ele está calado).

        O que o microfone ouve dele é a sala mais a voz dele; a folga é sobre a soma. Com a voz dele
        mal passando do barulho da sala, uma folga só sobre ela deixaria as sílabas mais fortes passarem.
        """
        return (self._fundo + self._eco) * self._sobra_do_eco if self._eco > 0 else 0.0

    def _corte(self) -> float:
        """Abaixo disto, um quadro da fala em curso é silêncio."""
        corte = self.limiar * 0.7  # para continuar basta menos do que para começar
        if self._por_cima is not None:
            corte = max(corte, self._ao_fundo * 1.5)  # a fala nasceu por cima de um fundo: voltar a ele é calar
        if self._eco:
            corte = max(corte, (self._fundo + self._eco) * 1.3)  # o Jarvis segue falando: o que está no volume dele não é a pessoa
        if self._estado == "pedido":
            corte = max(corte, self._voz * 0.25)  # no pedido, também o que fica bem abaixo da voz de quem pediu
        return corte

    def alimentar(self, audio: Any, tocado: float = 0.0) -> list[tuple[Any, ...]]:
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if self._sobra is not None and len(self._sobra):
            audio = np.concatenate([self._sobra, audio])
        acontecimentos: list[tuple[Any, ...]] = []
        inteiros = len(audio) // self._quadro * self._quadro
        for i in range(0, inteiros, self._quadro):
            acontecimento = self._quadro_novo(audio[i : i + self._quadro], tocado)
            if acontecimento is not None:
                acontecimentos.append(acontecimento)
        self._sobra = audio[inteiros:].copy()
        return acontecimentos

    # --- um quadro de cada vez ---------------------------------------------------------------

    def _medir_fundo(self) -> None:
        """O fundo são os momentos mais altos da sala nos últimos segundos.

        Sobe devagar (vale a janela inteira) e desce depressa (vale o último segundo, com folga):
        quando a música para, a voz normal volta a ser ouvida em um segundo, não em três.
        """
        import numpy as np

        if not self._sala:
            return
        volumes = np.fromiter(self._sala, dtype=np.float32)
        recente = volumes[-self._segundo :]
        self._fundo = float(min(np.percentile(volumes, 90), np.percentile(recente, 90) * 1.5))

    def _comecar(self) -> None:
        """Três quadros seguidos acima do limiar: começou uma fala. Leva junto o som de logo antes."""
        import numpy as np

        self._estado = "falando"
        self._trecho = list(self._recentes)
        self._volumes = [float(np.sqrt(np.mean(q * q))) for q in self._trecho]
        self._antes = max(0, len(self._trecho) - 3)  # quadros de antes de a fala começar (os três últimos já são ela)
        self._duracao = len(self._trecho)
        self._falados, self._calados, self._altos, self._saltos = 3, 0, 3, 0
        self._anunciado, self._salto_em = False, None
        self._sobre_eco, self._razoes_da_fala = self._tocado > 0, []
        self._limpos = 0 if self._tocado > 0 else len(self._trecho)
        self._recentes.clear()

    def _quadro_novo(self, quadro: Any, tocado: float = 0.0) -> tuple[Any, ...] | None:
        import numpy as np

        volume = float(np.sqrt(np.mean(quadro * quadro)))
        self.nivel = min(1.0, volume * GANHO_DO_NIVEL)

        # O que o microfone deve estar ouvindo da voz do próprio Jarvis neste instante (zero se ele está calado; o
        # resto do som dele morrendo na sala, logo depois de ele se calar, já vem contado em `tocado`).
        self._tocado = tocado
        self._calado_ha = 0 if tocado > 0 else self._calado_ha + 1
        self._eco = self._ganho * tocado if tocado > 0 and self._ganho is not None else 0.0

        if self._estado == "parado" and tocado > 0 and not self._medindo:
            return self._quadro_com_o_jarvis_falando(quadro, volume, tocado)

        if self._estado == "parado":
            self._recentes.append(quadro)
            # Para a sala vale o menor dos três últimos volumes: um estalo de um ou dois quadros (tecla,
            # mouse) some da conta, e um som que dura fica. Sem isso, digitar faria o limiar passar da voz.
            self._ultimos.append(volume)
            if len(self._ultimos) == self._ultimos.maxlen:
                self._sala.append(min(self._ultimos))
            if self._medindo:
                # O primeiro segundo de microfone aberto só mede a sala; nada é fala ainda.
                self._medindo -= 1
                if not self._medindo:
                    self._medir_fundo()
                return None
            self._altos = self._altos + 1 if volume > self.limiar else 0
            if self._altos >= 3:  # 90 ms acima do limiar
                if self._sala:
                    self._sala.pop()  # o último valor já era a fala
                self._ultimos.clear()
                self._comecar()
            else:
                self._desde_a_medida += 1
                if self._desde_a_medida >= 5:
                    self._desde_a_medida = 0
                    self._medir_fundo()
            return None

        if tocado > 0 and not self._sobre_eco and (self._ganho is None or volume <= (self._fundo + self._eco) * 1.3):
            # Ele começou a falar com esta fala já em curso (a resposta ao nome saindo enquanto a pessoa chamava
            # de novo). O que chega no volume dele é ele: não é fala, nem vai para a transcrição, senão o "Sim?"
            # dele sairia escrito no meio do que a pessoa disse. Enquanto não se sabe com que volume ele chega
            # ao microfone, tudo o que soa junto com ele é tratado assim.
            quadro, volume = np.zeros_like(quadro), 0.0
        self._duracao += 1
        self._volumes.append(volume)
        if tocado >= self._tocado_min and self._sobre_eco and len(self._razoes_da_fala) < self._razoes.maxlen:
            self._razoes_da_fala.append(max(0.0, volume - self._fundo) / tocado)
        calado_ate_aqui = self._calados
        if volume > self._corte():
            self._altos += 1
            if self._altos >= 3:  # um estalo isolado não interrompe o silêncio; fala, sim
                self._falados += self._altos if self._altos == 3 else 1
                self._calados = 0
            else:
                self._calados += 1
        else:
            self._altos = 0
            self._calados += 1

        if self._estado == "ignorado":
            return self._quadro_ignorado(quadro, volume, calado_ate_aqui)

        self._trecho.append(quadro)
        self._limpos = 0 if tocado > 0 else self._limpos + 1
        self.limpo_desde = max(0, len(self._trecho) - self._limpos) * self._quadro
        if self._calados >= self._silencio:
            quadros = self._trecho[: len(self._trecho) - self._calados + self._cauda]
            valeu, conferido, falados = self._falados >= self._minimo, self._estado == "pedido", self._falados
            self.sobre_ele = self._sobre_eco
            self._parar()
            return (FIM, np.concatenate(quadros), conferido, falados) if valeu else None
        if self._calados == 0:
            self._pausa_avisada = False
        if self._estado == "pedido":
            if self._duracao >= self._pedido_max:  # ninguém dita um pedido tão comprido: é a sala falando por cima
                completo, falados = np.concatenate(self._trecho), self._falados
                self.sobre_ele = self._sobre_eco
                self._parar()
                return (FIM, completo, True, falados)
        elif not self._anunciado:
            if self._calados == self._pausa_cedo and not self._pausa_avisada and self._falados >= self._minimo:
                # Fala curta que parou há meio segundo: quem recebe já pode ir vendo se é um chamado.
                self._pausa_avisada, self.sobre_ele = True, self._sobre_eco
                return (PAUSA, np.concatenate(self._trecho), self._falados)
            # Passou da janela e a pessoa segue falando: avisa já. Se está calada, pode ser o fim; espera para ver.
            if self._duracao >= self._janela and self._calados < self._pausa:
                self._anunciado, self._anunciada_em, self.sobre_ele = True, self._duracao, self._sobre_eco
                return (COMECO, np.concatenate(self._trecho))
            if (self._por_cima is None or self._depois_de_pausa) and self._entrou_voz_mais_alta(volume):
                # Pode ser alguém chamando por cima de uma fala da TV que mal começou. Confere já o que veio
                # antes: se era um chamado, tudo segue junto; se não era, a fala recomeça na voz nova.
                self._anunciado, self._anunciada_em, self.sobre_ele = True, self._duracao, self._sobre_eco
                self._salto_em = len(self._trecho) - 3
                return (COMECO, np.concatenate(self._trecho[: self._salto_em]))
        elif self._duracao >= self._anunciada_em + self._silencio:
            # Anunciada e ninguém respondeu (não devia acontecer): trata como dispensada.
            self.dispensar()
        return None

    def _quadro_com_o_jarvis_falando(self, quadro: Any, volume: float, tocado: float) -> None:
        """Sem fala em curso e com o Jarvis falando: o microfone ouve a voz dele.

        Ela não entra na conta da sala, e só começa uma fala quem passar bem acima dela. Enquanto
        ainda não se sabe com que volume ela chega ao microfone, ninguém começa: aprende-se primeiro.
        """
        import numpy as np

        self._recentes.append(quadro)
        self._ultimos.clear()
        # Quanto do que ele toca chega ao microfone, descontado o ruído que a sala já tinha.
        razao = max(0.0, volume - self._fundo) / tocado if tocado >= self._tocado_min else None
        if self._ganho is None:
            if razao is not None:
                self._razoes.append(razao)
                if len(self._razoes) >= self._aprender:
                    self._ganho = float(np.percentile(self._razoes, 95))
            return None
        if volume > max(self.limiar, self._acima_dele()):
            self._altos += 1
        else:
            self._altos = 0
            if razao is not None:  # só ele falando: serve para acompanhar o volume da caixa
                self._razoes.append(razao)
                self._desde_a_medida += 1
                if self._desde_a_medida >= 5:
                    self._desde_a_medida = 0
                    self._ganho = float(np.percentile(self._razoes, 95))
        if self._altos >= 3:
            self._comecar()
        return None

    def _entrou_voz_mais_alta(self, volume: float) -> bool:
        """Dentro de uma fala ainda não conferida: três quadros seguidos bem acima do que ela vinha sendo."""
        import numpy as np

        # Só a fala: o som de logo antes dela (o fim da voz do Jarvis, por exemplo) não é "o que ela vinha sendo".
        antes = self._volumes[self._antes : len(self._volumes) - self._saltos - 1]
        fortes = [v for v in antes if v > self._corte()]
        if len(fortes) < 10:  # menos de 0,3 s de fala: ainda não dá para dizer o que é "mais alto"
            self._saltos = 0
            return False
        self._saltos = self._saltos + 1 if volume > float(np.median(fortes)) * self._salto else 0
        return self._saltos >= 3

    def _quadro_ignorado(self, quadro: Any, volume: float, calado_ate_aqui: int = 0) -> tuple[Any, ...] | None:
        """Uma fala que não é chamado segue ao fundo: espera ela acabar, de olho em uma voz mais alta.

        Uma pausa curta já basta para a fala seguinte ser conferida: quem chamou e não foi
        entendido repete o nome em seguida, e não pode encontrar o microfone surdo.
        """
        import numpy as np

        self._recentes.append(quadro)
        del self._volumes[: -self._sala.maxlen]
        if self._calados >= self._silencio:
            self._parar()  # acabou em silêncio: era conversa, e a sala continua a mesma
            return None
        retomar = self._altos == 3 and calado_ate_aqui - 2 >= self._retomada and self._retomadas < self._retomadas_max
        if retomar and self._duracao < self._fundo_max:
            # Voltaram a falar depois de uma pausa. É uma fala nova, mas da mesma conversa: se também
            # for dispensada, o tempo dela se soma (conversa que não acaba vira o fundo da sala). Só as
            # primeiras: quem repete um chamado repete uma ou duas vezes; o que segue é conversa mesmo,
            # e conferir cada frase dela seria transcrever a sala o tempo todo.
            conversa = (self._duracao, self._volumes[: len(self._volumes) - 3])
            self._retomadas += 1
            self._comecar()
            self._por_cima, self._depois_de_pausa, self._ao_fundo = conversa, True, 0.0
            return None
        if self._duracao >= self._fundo_max and self._calado_ha >= self._sala.maxlen:
            # Não para nunca: é TV, música. Passa a ser o fundo da sala. (Com o Jarvis falando, ou recém-calado,
            # não: o que soa nesses segundos tem a voz dele, e a sala passaria a ter o volume dela.)
            self._sala.clear()
            self._sala.extend(self._volumes)
            self._medir_fundo()
            self._parar()
            return None
        if self._duracao % 10 == 0 or not self._ao_fundo:
            # Sem contar os quadros do próprio salto em andamento: eles não são "o fundo".
            altos = [v for v in self._volumes[: len(self._volumes) - self._saltos - 1] if v > self.limiar * 0.7]
            self._ao_fundo = float(np.median(altos)) if altos else self.limiar
        # (A voz do próprio Jarvis pode saltar por cima de um fundo baixo, mas não chega a contar como fala: ver _corte.)
        self._saltos = self._saltos + 1 if volume > self._ao_fundo * self._salto else 0
        if self._saltos >= 3:  # alguém falou bem mais alto que o fundo: pode ser um chamado
            fundo = (self._duracao, self._volumes[: len(self._volumes) - 3])
            self._comecar()
            self._por_cima = fundo
        return None


# --- a vigília ---------------------------------------------------------------------------------


class Vigia:
    """Mantém o microfone aberto e avisa quando alguém chama o Jarvis.

    - `transcrever(audio, rapido, dica)` devolve o texto de um trecho em 16 kHz. `dica` são as
      palavras de ativação, para a transcrição escrevê-las do jeito certo.
    - `ao_acordar()`: ouviu o nome, com ou sem pedido junto. Vem na hora: é o momento de calar
      o que estiver falando.
    - `ao_atender()`: ouviu só o nome e a pessoa está esperando. É a hora de responder ("Sim?").
    - `ao_pedir(texto)`: o pedido, sem o nome.
    - `ao_sobrepor()` / `ao_engano()`: alguém falou por cima da fala do Jarvis (ele deve se calar
      para ouvir) / não era um chamado (pode continuar de onde parou).
    - `tocado()`: o volume do que o Jarvis está tocando agora na caixa de som (zero se calado).
      `ditas()`: as frases que ele disse nos últimos segundos. `falando()`: ele tem o que falar
      (mesmo entre uma frase e outra). `impedido()`: não é para ouvir (tela bloqueada).
    - `imediato(pedido)`: este pedido não espera o fim da frase ("para"): é entregue assim que
      for entendido.
    - `proprias`: as frases com que o Jarvis responde ao nome. Ouvi-las de volta, logo depois
      de respondidas, não é pedido.
    - `ao_desistir()`: o chamado anunciado não deu em pedido (ninguém falou, ou o começo parecia
      um chamado e o todo não era).
    - `leve`: a transcrição rápida usa um modelo menor que a caprichada. Sem isso, um chamado
      curto é transcrito uma vez só, com capricho.
    - `dono(audio)`: é o dono quem fala? Devolve (sim ou não, a nota). Com isso, um chamado ou um
      pedido de outra voz é ignorado (e fica em `ouvidos`, marcado). Uma ordem de parar não passa
      por isso: parar é seguro, e a voz de quem manda parar costuma vir coberta pela dele.

    Os avisos saem de uma thread própria.
    """

    def __init__(
        self,
        transcrever: Callable[..., str],
        ao_pedir: Callable[[str], None],
        ao_acordar: Callable[[], None] | None = None,
        ao_atender: Callable[[], None] | None = None,
        ao_desistir: Callable[[], None] | None = None,
        ao_nivel: Callable[[float], None] | None = None,
        ao_sobrepor: Callable[[], None] | None = None,
        ao_engano: Callable[[], None] | None = None,
        palavras: Sequence[str] = (PALAVRA_PADRAO,),
        falando: Callable[[], bool] = lambda: False,
        tocado: Callable[[], float] = lambda: 0.0,
        ditas: Callable[[], Sequence[str]] = lambda: (),
        falou_ha: Callable[[], float] = lambda: float("inf"),
        impedido: Callable[[], bool] = lambda: False,
        imediato: Callable[[str], bool] = lambda _pedido: False,
        proprias: Sequence[str] = (),
        leve: bool = False,
        sd: Any = None,
        relogio: Callable[[], float] = time.monotonic,
        dono: Callable[[Any], tuple[bool, float]] | None = None,
    ) -> None:
        self._transcritor, self._ao_pedir = transcrever, ao_pedir
        self._dono = dono
        self.recusados = 0  # chamados e pedidos ignorados por não serem da voz do dono
        self.calou_para_ouvir = 0  # quantas vezes ele se calou por uma voz que entrou por cima da dele
        self.enganos = 0  # e em quantas delas não era com ele (a própria voz, um barulho)
        self._ao_acordar, self._ao_atender, self._ao_desistir, self._ao_nivel = ao_acordar, ao_atender, ao_desistir, ao_nivel
        self._ao_sobrepor, self._ao_engano = ao_sobrepor, ao_engano
        self.palavras = tuple(palavras)
        self._dica = ", ".join(p.title() for p in self.palavras)
        self._falando, self._tocado, self._ditas, self._impedido, self.leve = falando, tocado, ditas, impedido, leve
        self._imediato = imediato
        self._falou_ha = falou_ha
        self._chamado_em = float("-inf")  # quando ouviu o nome sozinho pela última vez
        self._mais_respostas = 0  # quantas vezes já respondeu de novo a este chamado
        self._proprias = frozenset(frase_simples(frase) for frase in proprias)
        # As palavras de cada resposta: a de uma resposta comprida voltando pela sala costuma vir pela metade.
        self._palavras_das_proprias = [{p for p in frase.split() if len(p) >= 3} for frase in self._proprias]
        self._sd, self._relogio = sd, relogio
        self._fila: queue.Queue[Any] = queue.Queue()
        self._fluxo: Any = None
        self._taxa = TAXA
        self._cortador = Cortador()
        self._thread: threading.Thread | None = None
        self._trava = threading.Lock()
        self._espera_ate = 0.0  # ouviu só o nome: até quando a próxima fala vale como pedido
        self._atender = False  # falta responder ao nome, assim que der para falar sem ser por cima de ninguém
        self._respondeu_em = float("-inf")  # quando respondeu ao nome pela última vez
        self._captado_em = float("-inf")  # quando o microfone captou o pedaço de som que está sendo tratado
        self._adiantado: tuple[int, str | None] | None = None  # o que a fala em curso dizia na última pausa: (quadros de fala, texto)
        self._nome_ouvido = False  # avisou que ouviu o nome e ainda não entregou o pedido nem desistiu dele
        self._eco_da_resposta = False  # acabou de responder ao nome: a primeira coisa ouvida pode ser a própria resposta
        self._sobreposta = False  # o Jarvis está calado para ouvir uma voz que entrou por cima da dele
        self._sobreposicao_vista = False  # a fala em curso já fez (ou já dispensou) isso: não cala de novo por ela
        self._folga = 0.0  # depois de se calar à toa, quanto tempo ele fica sem se calar de novo
        self._sem_calar_ate = 0.0
        self._suspensa = False  # outra gravação está com o microfone
        self._ultimo_som = 0.0  # quando chegou o último pedaço do microfone
        # O microfone neste instante, medido na chegada do som: a fila pode estar atrasada (uma transcrição
        # segura a thread que a trata), e "há alguém falando agora?" não pode esperar por ela.
        self._voz_em = float("-inf")  # quando chegou o último pedaço com volume de fala
        self._limiar_de_agora = float("inf")  # o volume a partir do qual um pedaço conta como fala
        self.atenta = False  # a conversa quer ouvir chamados (falso durante uma confirmação no teclado)
        self.pausada = False  # o usuário pediu para não ouvir
        self.ouvidos: deque[str] = deque(maxlen=8)  # o que foi ouvido e não era chamado; só na memória
        self.tempos: deque[float] = deque(maxlen=12)  # quanto levou cada transcrição recente, em segundos
        self.erro: str = ""

    # --- microfone --------------------------------------------------------------------------

    @property
    def aberta(self) -> bool:
        return self._fluxo is not None

    @property
    def suspensa(self) -> bool:
        """Outra gravação (a do atalho) está com o microfone; ele volta sozinho quando ela acabar."""
        return self._suspensa

    @property
    def esperando_pedido(self) -> bool:
        return self._espera_ate > 0

    def _receber(self, dados: Any, _quadros: int, _tempo: Any, _estado: Any) -> None:
        # Roda na thread do áudio. Se era para ouvir, e o que o Jarvis tocava, é anotado aqui, na hora em que o
        # som entrou: quem trata o pedaço pode estar atrasado, e vale o que era verdade naquele instante.
        try:
            ouvir = self.atenta and not self.pausada and not self._impedido()
            tocado = float(self._tocado()) if ouvir else 0.0
        except Exception:  # noqa: BLE001 - na dúvida, não ouviu
            ouvir, tocado = False, 0.0
        agora = self._relogio()
        self._ultimo_som = agora
        try:
            if float((dados * dados).mean()) ** 0.5 > self._limiar_de_agora:
                self._voz_em = agora
        except Exception:  # noqa: BLE001 - sem a medida, vale o que a fila disser quando for tratada
            pass
        self._fila.put((ouvir, tocado, dados.copy(), agora))

    def _abrir(self) -> None:
        sd = self._sd
        if sd is None:
            import sounddevice as sd

            self._sd = sd
        try:
            taxa = TAXA
            fluxo = sd.InputStream(samplerate=TAXA, channels=1, dtype="float32", callback=self._receber)
        except sd.PortAudioError:
            # Microfone que não aceita 16 kHz: ouve na taxa dele e converte cada trecho depois.
            taxa = int(sd.query_devices(kind="input")["default_samplerate"])
            fluxo = sd.InputStream(samplerate=taxa, channels=1, dtype="float32", callback=self._receber)
        if taxa != self._cortador.taxa:
            self._cortador = Cortador(taxa)  # outra taxa, outro cortador; na mesma, ele já conhece a sala
        self._taxa = taxa
        self._ultimo_som = self._relogio()
        fluxo.start()
        self._fluxo = fluxo

    def _fechar(self, morto: bool = False) -> None:
        fluxo, self._fluxo = self._fluxo, None
        if fluxo is not None:
            try:
                # Fluxo que parou de mandar som é largado sem cerimônia: esperar por ele pode nunca acabar.
                (fluxo.abort if morto and hasattr(fluxo, "abort") else fluxo.stop)()
                fluxo.close()
            except Exception:  # noqa: BLE001 - fechando; o microfone pode ter sido desconectado
                log.exception("Falha ao fechar o microfone da vigília")
        self._esvaziar()

    def _esvaziar(self) -> None:
        while True:
            try:
                self._fila.get_nowait()
            except queue.Empty:
                break
        self._cortador.zerar()

    def iniciar(self) -> None:
        """Abre o microfone e começa a ouvir. Levanta a exceção do áudio se não conseguir."""
        with self._trava:
            if self._fluxo is None:
                self._abrir()
            if self._thread is None:
                self._thread = threading.Thread(target=self._laco, daemon=True, name="jarvis-vigilia")
                self._thread.start()

    def suspender(self) -> None:
        """Solta o microfone (outra gravação vai usá-lo). `retomar` abre de novo."""
        with self._trava:
            self._suspensa = True
            self._fechar()
        self._esquecer_chamado()

    def _reabrir(self) -> None:
        try:
            self._abrir()
            self.erro = ""
        except Exception as erro:  # noqa: BLE001 - sem microfone a conversa continua pelo teclado e pelo atalho
            self.erro = str(erro) or type(erro).__name__
            log.exception("Não consegui abrir o microfone da vigília")

    def retomar(self) -> None:
        with self._trava:
            self._suspensa = False
            if self._thread is None or self._fluxo is not None or self.pausada:
                return
            self._reabrir()

    def pausar(self) -> None:
        """O usuário não quer ser ouvido agora: fecha o microfone até `continuar`."""
        self.pausada = True
        with self._trava:
            self._fechar()
        self._esquecer_chamado()

    def continuar(self) -> None:
        self.pausada = False
        self.retomar()

    def conferir_microfone(self, mudo: float = 5.0) -> bool:
        """Para chamar de tempos em tempos: reabre o microfone se ele caiu. Devolve se está aberto.

        O microfone cai sem avisar quando o cabo sai ou o Windows volta de uma suspensão: os
        pedaços de som simplesmente param de chegar.
        """
        with self._trava:
            if self._thread is None or self.pausada or self._suspensa:
                return self._fluxo is not None
            if self._fluxo is not None and self._relogio() - self._ultimo_som > mudo:
                log.warning("O microfone parou de mandar som; reabrindo")
                self._fechar(morto=True)
                self._largar()  # quem esperava um pedido fica sabendo que ele não foi ouvido
            if self._fluxo is None:
                self._reabrir()
            if self._fluxo is None and not self._falando():
                # Não abriu. A lista de microfones é lida uma vez só, quando o áudio sobe: um microfone
                # ligado depois (fone Bluetooth que conectou agora) só aparece relendo. Com o Jarvis calado
                # e nenhuma gravação em curso, reler não interrompe nada.
                self._reler_dispositivos()
                self._reabrir()
            return self._fluxo is not None

    def _reler_dispositivos(self) -> None:
        sd = self._sd
        try:
            if sd is not None and hasattr(sd, "_terminate") and hasattr(sd, "_initialize"):
                sd._terminate()
                sd._initialize()
        except Exception:  # noqa: BLE001 - sem reler, fica como estava
            log.exception("Não consegui reler a lista de dispositivos de áudio")

    def parar(self) -> None:
        with self._trava:
            self._fechar()
            thread, self._thread = self._thread, None
        if thread is not None:
            self._fila.put(None)
            thread.join(3)

    # --- avisos -------------------------------------------------------------------------------

    def _avisar(self, aviso: Callable[..., None] | None, *dados: Any) -> None:
        if aviso is not None:
            try:
                aviso(*dados)
            except Exception:  # noqa: BLE001 - quem recebe o aviso não pode derrubar a escuta
                log.exception("Falha em quem acompanha a vigília")

    def _desfazer_sobreposicao(self, engano: bool) -> None:
        """A voz que entrou por cima da fala do Jarvis já foi entendida: era um chamado, ou um engano."""
        if not self._sobreposta:
            return
        self._sobreposta = False
        if engano:
            self.enganos += 1
            # Calou-se à toa. Para não ficar gaguejando a cada barulho, passa um tempo sem se calar de novo
            # (um chamado de verdade continua valendo: quem cala nesse caso é o nome, não o volume).
            self._folga = min(FOLGA_MAXIMA, max(FOLGA_DEPOIS_DE_ENGANO, self._folga * 2))
            self._sem_calar_ate = self._relogio() + self._folga
            self._avisar(self._ao_engano)
        else:
            self._folga = 0.0

    def _ouviu_o_nome(self) -> None:
        self._nome_ouvido = True
        self._desfazer_sobreposicao(engano=False)
        self._avisar(self._ao_acordar)

    def _e_o_dono(self, trecho: Any, texto: str) -> bool:
        """A voz deste trecho é a do dono? Sem conferência, ou se ela falhar, é (ninguém fica trancado para fora)."""
        if self._dono is None or trecho is None:
            return True
        try:
            aceito, nota = self._dono(trecho)
        except Exception:  # noqa: BLE001 - modelo com problema: vale como antes, sem a conferência
            log.exception("A conferência da voz do dono falhou; o chamado vale")
            return True
        if aceito:
            return True
        self.recusados += 1
        self.ouvidos.append(f"(outra voz, {nota:.2f}) {texto}".strip())
        return False

    def _acordar(self, trecho: Any = None, texto: str = "") -> None:
        """Ouviu só o nome: a próxima fala é o pedido."""
        self._adiantado = None
        if self._suspensa or self.pausada:
            return  # enquanto se transcrevia, o microfone foi para outra gravação ou foi desligado: não há a quem responder
        if not self._e_o_dono(trecho, texto):
            self._largar()
            return
        self._espera_ate = self._relogio() + ESPERA_DO_PEDIDO
        self._atender = True
        self._chamado_em, self._mais_respostas = self._relogio(), 0
        self._ouviu_o_nome()

    def _entregar(self, pedido: str, trecho: Any = None) -> None:
        if not self._e_o_dono(trecho, pedido):
            if self._espera_ate:
                self._falta_responder(repetiu_o_nome=False)  # continua esperando o pedido do dono
            else:
                self._largar()
            return
        self._nome_ouvido, self._espera_ate, self._atender, self._adiantado = False, 0.0, False, None
        self._desfazer_sobreposicao(engano=False)
        self._avisar(self._ao_pedir, pedido)

    def _largar(self) -> None:
        """O chamado que foi anunciado não deu em pedido: avisa, para quem mostrava "ouvindo" voltar ao que fazia."""
        havia = self._nome_ouvido or bool(self._espera_ate)
        self._nome_ouvido, self._espera_ate, self._atender, self._adiantado = False, 0.0, False, None
        if havia:
            self._avisar(self._ao_desistir)

    def _esquecer_chamado(self) -> None:
        """O microfone foi fechado: o chamado que estava pela metade se perde, e a fala pausada para ouvir volta."""
        self._nome_ouvido, self._espera_ate, self._atender, self._adiantado = False, 0.0, False, None
        self._desfazer_sobreposicao(engano=True)

    def _separar(self, texto: str) -> str | None:
        """O pedido deste texto, se ele chama o Jarvis.

        Numa fala gravada por cima da voz dele, a transcrição costuma trazer antes do nome o fim da
        frase que ele dizia. Aí o nome vale também no meio, mas só para chamar a atenção ("Jarvis!")
        ou mandar parar: "deixa o Jarvis terminar", dito por cima dele, não é um pedido.
        """
        pedido = separar_pedido(texto, self.palavras)
        if pedido is None and self._cortador.sobre_ele:
            solto = separar_pedido(texto, self.palavras, solto=True)
            if solto is not None and (solto == "" or self._imediato(solto)):
                return solto
        return pedido

    def _mandou_parar(self, trecho: Any) -> str | None:
        """Numa fala por cima da voz dele que não trouxe o nome: o que foi dito depois de ele se calar manda parar?

        Quem diz "Jarvis, para" por cima da fala dele tem o nome coberto pela voz dele, e a transcrição
        costuma errá-lo. Mas ele se cala para ouvir assim que a voz entra, e o "para" já chega limpo.
        Nesse resto não há palavra dele: uma ordem de parar ali é de quem falou. Devolve a ordem, ou None.
        """
        if not self._cortador.sobre_ele:
            return None
        limpo = trecho[int(self._cortador.limpo_desde * TAXA / self._taxa) :]
        if len(limpo) < LIMPO_MINIMO * TAXA:
            return None  # ele não chegou a se calar, ou nada foi dito depois
        palavras = frase_simples(self._transcrever(limpo, rapido=False)).split()
        if len(palavras) > PALAVRAS_DE_PARADA:
            return None
        # O começo pode ser o fim do nome, ainda meio coberto ("…vis, para"): vale o fim da frase.
        for quantas in range(len(palavras), 0, -1):
            ordem = " ".join(palavras[-quantas:])
            if self._imediato(ordem):
                return ordem
        return None

    # --- transcrição --------------------------------------------------------------------------

    def _transcrever(self, trecho: Any, rapido: bool) -> str:
        inicio = time.perf_counter()
        try:
            return self._transcritor(trecho, rapido=rapido, dica=self._dica).strip()
        finally:
            self.tempos.append(time.perf_counter() - inicio)

    def _nao_era(self, texto: str) -> None:
        if texto:
            self.ouvidos.append(texto)
        if self._cortador.sobre_ele and self._parece_dele(texto):
            # O que entrou "por cima" da voz dele era ele mesmo, chegando mais alto do que se esperava:
            # o cortador aprende com isso. Se era gente comentando alguma coisa, não há o que aprender
            # (e aprender deixaria a vigília surda, por uns segundos, a quem falasse naquele volume).
            self._cortador.engano()

    def _parece_dele(self, texto: str) -> bool:
        """A transcrição de uma fala que começou por cima da voz do Jarvis repete o que ele dizia?"""
        ouvidas = [p for p in frase_simples(texto).split() if len(p) >= 3]
        if not ouvidas:
            return True  # nada que se entenda: não era uma pessoa dizendo alguma coisa
        try:
            dele = {p for frase in self._ditas() for p in frase_simples(frase).split()}
        except Exception:  # noqa: BLE001 - sem saber o que ele dizia, vale a hipótese mais segura para ele não gaguejar
            return True
        return sum(p in dele for p in ouvidas) * 2 >= len(ouvidas)

    def _chama(self, trecho: Any) -> bool:
        """O começo deste trecho (16 kHz) chama o Jarvis? Transcreve depressa só a primeira janela."""
        texto = self._transcrever(trecho[: int(JANELA * TAXA)], rapido=True)
        if self._separar(texto) is not None:
            return True
        self._nao_era(texto)
        return False

    def _ler(self, trecho: Any, conferido: bool = False) -> str | None:
        """O que este trecho (16 kHz) diz, se for um chamado; None se não for.

        O que vira ação é sempre a transcrição caprichada, e ela precisa trazer o nome no começo.
        Com um modelo leve para a procura, ele olha primeiro e poupa a caprichada no que não chama;
        se ouviu só o nome, a palavra dele basta, para a resposta sair logo.
        """
        if self.leve and not conferido:
            rapido = self._transcrever(trecho[: int(JANELA * TAXA)], rapido=True)
            pedido = self._separar(rapido)
            if pedido is None:
                self._nao_era(rapido)
                return None
            if pedido == "" and len(trecho) <= JANELA * TAXA:
                return rapido
        completo = self._transcrever(trecho, rapido=False)
        if self._separar(completo) is None:
            self._nao_era(completo)
            return None
        return completo

    # --- o que fazer com o som ----------------------------------------------------------------

    def _laco(self) -> None:
        while True:
            item = self._fila.get()
            if item is None:
                return
            try:
                self._bloco(*item)
            except Exception:  # noqa: BLE001 - um trecho com problema não encerra a vigília
                log.exception("Falha ao tratar o som da vigília")

    def _bloco(self, ouvir: bool, tocado: float, bloco: Any, quando: float | None = None) -> None:
        """Trata um pedaço de som. `ouvir`, `tocado` e `quando` valem para a hora em que ele foi captado."""
        self._captado_em = self._relogio() if quando is None else quando
        if not ouvir:
            # Não era para ouvir (confirmação no teclado, tela bloqueada): o que estava pela metade se perde.
            self._cortador.zerar()
            self._sobreposicao_vista = False
            self._desfazer_sobreposicao(engano=True)
            self._largar()
            return
        if self._fila.qsize() * len(bloco) > ATRASO_MAXIMO * self._taxa:
            log.warning("A vigília ficou para trás; descartei o som acumulado")
            self._esvaziar()
            self._sobreposicao_vista = False
            self._desfazer_sobreposicao(engano=True)
            self._largar()
            return
        for acontecimento in self._cortador.alimentar(bloco, tocado):
            self._acontecimento(*acontecimento)
        agora = self._relogio()
        if not self._cortador.em_fala:
            # A voz acabou (ou foi dispensada) e não era um chamado. Se a vigília emperrar antes de chegar
            # aqui, a fala não fica presa: a pausa dela vence sozinha (ver Falante.pausar).
            self._desfazer_sobreposicao(engano=True)
            self._sobreposicao_vista = False
        elif self._cortador.sobre_o_jarvis and not self._sobreposicao_vista and not self._espera_ate:
            self._sobreposicao_vista = True  # uma vez por fala
            if agora >= self._sem_calar_ate:
                # Alguém falou firme por cima da fala do Jarvis: ele se cala para ouvir o que é.
                self._sobreposta = True
                self.calou_para_ouvir += 1
                self._avisar(self._ao_sobrepor)
        self._limiar_de_agora = self._cortador.limiar
        if self._atender:
            # Falta responder ao nome. Com a fila em dia, responde se a pessoa não emendou outra fala; se emendou,
            # não há o que responder agora (é o pedido; se não for, `_falta_responder` cuida). Com som em atraso
            # na fila, vale o microfone deste instante: calado, responde já. Esperar a fila esvaziar deixaria sem
            # resposta justamente quem, por não ouvir resposta, chama de novo: cada chamado é mais som para tratar.
            em_dia = self._fila.empty()
            if em_dia:
                self._atender = False
            if (not self._cortador.em_fala) if em_dia else self._calado_agora(agora):
                self._atender, self._respondeu_em = False, agora
                self._espera_ate = agora + ESPERA_DO_PEDIDO  # o prazo conta de quando ele responde
                self._eco_da_resposta = True
                self._avisar(self._ao_atender)
        if self._espera_ate:
            self._avisar(self._ao_nivel, self._cortador.nivel)
            if agora > self._espera_ate and not self._cortador.em_fala and not self._atender:
                self._largar()

    def _calado_agora(self, agora: float) -> bool:
        """Ninguém fala ao microfone neste instante (pelo som que acabou de chegar, não pelo que a fila já tratou)."""
        return agora - self._voz_em >= CALADO_PARA_RESPONDER

    def _falta_responder(self, repetiu_o_nome: bool) -> None:
        """Esperava-se o pedido e veio outra coisa (o nome de novo, um barulho). Se a pessoa não ouviu resposta, responde.

        - Ainda não respondeu a este chamado (ela falava quando daria para responder): responde.
        - Já respondeu: só responde de novo se ela repetiu o nome numa fala que terminou depois da
          resposta (sinal de que não a escutou), e só uma vez por chamado. Barulho não conta: o
          barulho mais provável depois da resposta é a própria resposta, voltando pela sala.
        """
        if self._respondeu_em < self._chamado_em:
            self._atender = True
        elif repetiu_o_nome and self._captado_em > self._respondeu_em + FIM_DE_FALA and self._mais_respostas < MAIS_RESPOSTAS:
            self._mais_respostas += 1
            self._atender = True

    def _parece_resposta(self, texto: str) -> bool:
        """O texto é uma das respostas dele ao nome, inteira ou o pedaço dela que a transcrição pegou?

        Pedaço: as palavras (de três letras ou mais) estão todas numa mesma resposta; com quatro ou mais,
        uma pode faltar. "com o que eu
        posso te ajudar" é o fim de "Sim, mestre. Com o que eu posso te ajudar?"; "abre a planilha", não.
        """
        simples = frase_simples(texto)
        if simples in self._proprias:
            return True
        palavras = [p for p in simples.split() if len(p) >= 3]
        if not palavras:
            return False
        folga = 1 if len(palavras) >= 4 else 0  # numa frase mais longa, uma palavra mal transcrita não desfaz a semelhança
        return any(sum(p not in dela for p in palavras) <= folga for dela in self._palavras_das_proprias)

    def _sem_a_resposta(self, texto: str) -> str:
        """O texto sem as frases do começo que são a resposta dele ao nome ("Sim, mestre… ajudar? Que horas são?")."""
        frases = [f.strip() for f in re.split(r"(?<=[.?!…])\s+", texto.strip()) if f.strip()]
        while frases and self._parece_resposta(frases[0]):
            frases.pop(0)
        return " ".join(frases)

    def _eco_da_propria_resposta(self, trecho: Any, depois: float) -> bool:
        """Esta fala é a resposta dele ao nome ("Sim?", "Pois não?") voltando pelo microfone?

        Vale se o que ele disse por último foi uma resposta ao nome e a fala começou junto com ela, ou
        logo depois de ela acabar (o som que ainda corre pela sala). A transcrição, que recebe o nome
        como dica, costuma escrever esse eco como "Jarvis": sem isto, ele responderia à própria resposta
        para sempre. `depois`: segundos de silêncio que a fala já tem no fim (quanto ela esperou para ser entregue).
        """
        try:
            ditas = list(self._ditas())
            fim_dele = self._relogio() - float(self._falou_ha())
        except Exception:  # noqa: BLE001 - sem saber o que ele disse, não é eco
            return False
        if not ditas or frase_simples(ditas[-1]) not in self._proprias:
            return False
        if self._cortador.sobre_ele:
            return True  # começou com ele falando
        if fim_dele == float("-inf") or self._respondeu_em == float("-inf"):
            return False  # ele nunca falou, ou nunca respondeu ao nome: não há eco de resposta
        chegou = self._captado_em if self._captado_em != float("-inf") else self._relogio()
        comecou = chegou - len(trecho) / TAXA + ANTES_DA_FALA - (depois - CAUDA_DA_FALA if depois > CAUDA_DA_FALA else 0.0)
        return self._respondeu_em - ANTES_DA_FALA <= comecou <= fim_dele + ECO_DA_RESPOSTA

    def _acontecimento(self, tipo: str, som: Any, *resto: Any) -> None:
        if tipo == COMECO:
            # Fala comprida: confere o começo enquanto a pessoa ainda fala.
            if self._espera_ate:
                self._cortador.aceitar()
            elif self._chama(reamostrar(som, self._taxa)):
                self._cortador.aceitar()
                self._ouviu_o_nome()
            else:
                self._cortador.dispensar()
        elif tipo == PAUSA:
            self._pausa(reamostrar(som, self._taxa), int(resto[0]))
        else:
            self.tratar(reamostrar(som, self._taxa), bool(resto[0]) if resto else False, int(resto[1]) if len(resto) > 1 else -1)

    def _pausa(self, trecho: Any, falados: int) -> None:
        """Uma fala curta parou há meio segundo: vê já o que ela diz, em vez de esperar o silêncio inteiro."""
        if self._espera_ate:
            # É o pedido que se esperava. Transcreve desde já: se nada mais for dito, é só entregar. E já vale
            # como pedido para o cortador: o fim dele é medido pela voz de quem pede, não pelo barulho da sala.
            self._cortador.aceitar()
            self._adiantado = (falados, self._transcrever(trecho, rapido=False))
            return
        texto = self._ler(trecho)
        if texto is None:
            self._adiantado = None  # o que se viu nesta pausa não pode valer para outra fala com o mesmo tanto de voz
            ordem = self._mandou_parar(trecho)
            if ordem is not None:
                self._cortador.encerrar()
                self._ouviu_o_nome()
                self._entregar(ordem)
                return
            self._cortador.dispensar()
            return
        pedido = self._separar(texto) or ""
        if not pedido and self._eco_da_propria_resposta(trecho, PAUSA_CEDO):
            self._adiantado = None
            self._cortador.dispensar()  # a resposta dele voltando pela sala, escrita como o nome
            return
        self._adiantado = (falados, texto)
        if not pedido:
            self._cortador.encerrar()  # só o nome: o que vier agora é o pedido
            self._acordar(trecho, texto)
        elif self._imediato(pedido):
            self._cortador.encerrar()  # "Jarvis, para": não há por que esperar o resto do silêncio
            self._ouviu_o_nome()
            self._entregar(pedido)
        else:
            self._cortador.aceitar()  # nome e pedido: segue até o silêncio, caso a frase continue
            self._ouviu_o_nome()

    def tratar(self, trecho: Any, conferido: bool = False, falados: int = -1) -> None:
        """Uma fala (16 kHz) terminou: é o pedido que se esperava, um chamado, ou nada.

        `conferido`: o começo dela já foi visto e tinha o nome. `falados`: quantos quadros de fala
        ela tem; se é o mesmo da última pausa, a transcrição daquela hora ainda vale.
        """
        adiantado, self._adiantado = self._adiantado, None
        pronto = adiantado is not None and adiantado[0] == falados and falados >= 0
        if self._espera_ate:
            texto = adiantado[1] if pronto else self._transcrever(trecho, rapido=False)
            if texto and self._respondeu_em > self._chamado_em and not self._parece_resposta(texto):
                # O fim da resposta dele, ainda soando na sala, pode ter entrado colado no começo do pedido.
                # (Se o texto todo é a resposta, fica como está: logo abaixo ele é reconhecido como eco.)
                texto = self._sem_a_resposta(texto)
            if not texto:
                self._falta_responder(repetiu_o_nome=False)
                return  # era barulho; continua esperando até o prazo
            if self._eco_da_resposta:
                # A primeira coisa ouvida depois de ele responder ao nome. Se é igual à resposta, é ela mesma
                # voltando pelo microfone, por mais que a transcrição tenha demorado.
                self._eco_da_resposta = False
                if self._parece_resposta(texto):
                    return
            repetiu = separar_pedido(texto, self.palavras)  # "Jarvis… Jarvis, abre o YouTube"
            if repetiu and self._parece_resposta(repetiu):
                repetiu = ""  # chamou de novo e a resposta dele entrou junto ("Jarvis… Sim?"): não é um pedido
            eco = repetiu == "" or (repetiu is None and self._parece_resposta(texto))
            if eco and self._eco_da_propria_resposta(trecho, FIM_DE_FALA):
                return  # a resposta dele voltando pela sala (às vezes escrita como o nome): não é chamado nem pedido
            if repetiu == "":
                self._espera_ate = self._relogio() + ESPERA_DO_PEDIDO
                self._falta_responder(repetiu_o_nome=True)
                return
            self._entregar(repetiu if repetiu is not None else texto, trecho)
            return
        texto = adiantado[1] if pronto else self._ler(trecho, conferido)
        pedido = self._separar(texto) if texto is not None else None
        if pedido:
            self._entregar(pedido, trecho)
        elif pedido == "":
            if self._eco_da_propria_resposta(trecho, FIM_DE_FALA):
                self._largar()
                return  # a resposta dele voltando pela sala, escrita como o nome
            self._acordar(trecho, texto)
        else:
            self._largar()  # se o começo parecia um chamado e o todo não confirmou, quem foi avisado fica sabendo
