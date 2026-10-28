"""Voz da nuvem (ElevenLabs), com a voz local (Piper) de reserva.

A ElevenLabs sintetiza cada frase pela API e devolve o som pronto; a qualidade é bem maior que a
do Piper, mas custa crédito (o plano grátis dá uns 10 mil por mês) e precisa de internet. Por isso
esta peça nunca é a única: toda frase que a nuvem não entregar, por qualquer motivo, sai pela voz
local na hora, sem o usuário esperar nem ficar sem resposta.

Quando a nuvem é deixada de lado:
- sem chave, ou chave recusada (401): desligada até reiniciar, com aviso;
- crédito do mês acabou (a API diz, ou o limite de letras do usuário foi atingido): até o mês virar;
- a API falhou ou demorou: a frase sai local; três falhas seguidas e ela descansa cinco minutos.

As frases curtas e fixas ("Sim, mestre…") ficam guardadas em disco depois da primeira vez: não
gastam crédito de novo. O gasto do mês fica anotado em um arquivo e aparece no /custo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ENDERECO = "https://api.elevenlabs.io/v1"
MODELOS = {"flash": "eleven_flash_v2_5", "multilingual": "eleven_multilingual_v2", "turbo": "eleven_turbo_v2_5"}
MODELO_PADRAO = "flash"
TAXA = 22050  # a mesma das vozes do Piper: a caixa de som não precisa ser reaberta ao trocar de uma para a outra
LIMITE_PADRAO = 9000  # letras por mês que o Jarvis gasta na nuvem (o plano grátis dá 10 mil créditos)
MAX_GUARDADA = 48  # letras; frase até esse tamanho fica guardada em disco depois de sintetizada
ESPERA = 6.0  # segundos por pedido: mais que isso, a frase sai pela voz local
DESCANSO = 5 * 60.0  # segundos sem tentar a nuvem depois de três falhas seguidas
FALHAS_PARA_DESCANSAR = 3
VELOCIDADE_MINIMA, VELOCIDADE_MAXIMA = 0.7, 1.2  # o que a API aceita em voice_settings.speed
_ID_DE_VOZ = re.compile(r"^[A-Za-z0-9]{20}$")  # o ID da ElevenLabs tem 20; aceitar mais deixa passar cópia torta


class ErroNuvem(Exception):
    """A nuvem não serve agora; a mensagem diz por quê."""


def pedir(metodo: str, caminho: str, chave: str, corpo: dict[str, Any] | None = None, espera: float = ESPERA) -> tuple[int, bytes]:
    """Um pedido à API. Devolve (código, corpo); erros HTTP voltam como código, não como exceção."""
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    pedido = urllib.request.Request(
        f"{ENDERECO}{caminho}",
        data=dados,
        method=metodo,
        headers={"xi-api-key": chave, "Content-Type": "application/json", "Accept": "*/*", "User-Agent": "jarvis-desktop"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=espera) as resposta:  # noqa: S310 - endereço fixo, https
            return resposta.status, resposta.read()
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read()


class SinteseNuvem:
    """Sintetiza pela ElevenLabs; o que não der, sai pela `reserva` (a voz local)."""

    def __init__(
        self,
        chave: str,
        reserva: Callable[[str], tuple[Any, int]],
        pasta: Path,
        voz: str = "",
        modelo: str = MODELO_PADRAO,
        limite: int = LIMITE_PADRAO,
        pedir_api: Callable[..., tuple[int, bytes]] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        hoje: Callable[[], datetime] = datetime.now,
        avisar: Callable[[str], None] = print,
    ) -> None:
        self.chave, self.reserva, self.pasta = chave.strip(), reserva, pasta
        self.voz_pedida, self.modelo_id = voz.strip(), MODELOS.get(modelo.strip().lower(), modelo.strip() or MODELOS[MODELO_PADRAO])
        self.limite = max(0, int(limite))
        self._pedir = pedir_api or pedir
        self._relogio, self._hoje, self._avisar = relogio, hoje, avisar
        self.velocidade = 1.0  # o Falante ajusta; a reserva recebe o mesmo
        self.taxa = TAXA
        self._trava = threading.Lock()
        self.voz_id, self.voz_nome = "", ""
        self.desligada = ""  # o motivo, quando a nuvem foi desligada de vez nesta sessão
        self.dormindo_ate = 0.0  # até quando ela descansa depois de falhas seguidas
        self._falhas = 0
        self.mes, self.gastas = "", 0  # o mês (AAAA-MM) e quantas letras a nuvem já falou nele
        self.creditos: tuple[int, int] | None = None  # (usados, limite) segundo a própria API, quando ela conta
        self.ultimo_motivo = ""  # por que a última frase saiu pela reserva (vazio: saiu pela nuvem)
        self._registro = pasta / "elevenlabs.json"
        self._guardadas = pasta / "elevenlabs-frases"
        self._carregar()

    # --- registro -----------------------------------------------------------------------

    def _carregar(self) -> None:
        try:
            dados = json.loads(self._registro.read_text(encoding="utf-8"))
            if isinstance(dados, dict) and dados.get("mes") == self._mes_atual():
                self.mes, self.gastas = str(dados["mes"]), int(dados.get("gastas", 0))
            if isinstance(dados, dict) and dados.get("voz_pedida") == self.voz_pedida and dados.get("voz_id"):
                self.voz_id, self.voz_nome = str(dados["voz_id"]), str(dados.get("voz_nome") or "")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            log.exception("Não consegui ler %s", self._registro)
        if not self.mes:
            self.mes, self.gastas = self._mes_atual(), 0

    def _gravar(self) -> None:
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            self._registro.write_text(
                json.dumps({"mes": self.mes, "gastas": self.gastas, "voz_pedida": self.voz_pedida, "voz_id": self.voz_id, "voz_nome": self.voz_nome}),
                encoding="utf-8",
            )
        except OSError:
            log.exception("Não consegui gravar %s", self._registro)

    def _mes_atual(self) -> str:
        return self._hoje().strftime("%Y-%m")

    def _virar_o_mes(self) -> None:
        mes = self._mes_atual()
        if mes != self.mes:
            self.mes, self.gastas, self.creditos = mes, 0, None
            self._gravar()

    # --- estado ---------------------------------------------------------------------------

    @property
    def ativa(self) -> bool:
        """A nuvem está em uso agora (ligada, com crédito e fora do descanso)."""
        return not self.desligada and self._relogio() >= self.dormindo_ate and not self._sem_credito()

    def _sem_credito(self) -> bool:
        self._virar_o_mes()
        if self.gastas >= self.limite:
            return True
        return self.creditos is not None and self.creditos[0] >= self.creditos[1]

    def situacao(self) -> str:
        """Uma linha para o /custo e para a partida."""
        if self.desligada:
            return f"Voz da nuvem (ElevenLabs) desligada: {self.desligada}"
        voz = f", voz {self.voz_nome or self.voz_id or 'a primeira da biblioteca'}"
        creditos = f"; a API conta {self.creditos[0]} de {self.creditos[1]} créditos usados" if self.creditos else ""
        gasto = f"{self.gastas} de {self.limite} letras neste mês{creditos}"
        if self._sem_credito():
            return f"Voz da nuvem (ElevenLabs): crédito do mês acabou ({gasto}); até o mês virar, a voz local{voz}."
        if self._relogio() < self.dormindo_ate:
            return f"Voz da nuvem (ElevenLabs): descansando depois de falhas seguidas ({gasto}){voz}."
        return f"Voz da nuvem (ElevenLabs) ligada: {gasto}{voz}."

    # --- preparar -------------------------------------------------------------------------

    def preparar(self) -> None:
        """Confere a chave, acha a voz e lê o crédito. Levanta ErroNuvem se não der; o resto segue local."""
        if not self.chave:
            raise ErroNuvem("sem chave (JARVIS_ELEVENLABS_KEY vazia)")
        codigo, corpo = self._pedir("GET", "/user/subscription", self.chave)
        if codigo == 401 and b"missing_permissions" in corpo:
            # Chave restrita (só texto para fala): sem ler o crédito, mas a fala pode funcionar.
            log.info("A chave da ElevenLabs não pode ler a assinatura; sigo sem a conta de créditos")
            self.creditos = None
        elif codigo == 401:
            raise ErroNuvem("a chave foi recusada pela ElevenLabs (401); confira JARVIS_ELEVENLABS_KEY")
        elif codigo == 200:
            try:
                dados = json.loads(corpo)
                self.creditos = (int(dados["character_count"]), int(dados["character_limit"]))
            except (ValueError, KeyError, TypeError):
                log.warning("A ElevenLabs respondeu a assinatura em um formato que não entendi")
        elif codigo >= 500 or codigo == 0:
            raise ErroNuvem(f"a ElevenLabs não respondeu ({codigo})")
        if not self.voz_id or (self.voz_pedida and not _ID_DE_VOZ.match(self.voz_pedida) and not self.voz_nome):
            self._achar_voz()

    def _achar_voz(self) -> None:
        codigo, corpo = self._pedir("GET", "/voices", self.chave)
        if self.voz_pedida and _ID_DE_VOZ.match(self.voz_pedida) and codigo != 200:
            # Sem poder listar (chave restrita), o ID vale pela palavra de quem o escreveu.
            self.voz_id, self.voz_nome = self.voz_pedida, ""
            self._gravar()
            return
        if codigo == 401 and b"missing_permissions" in corpo:
            raise ErroNuvem("a chave não pode listar as vozes (permissão restrita): ponha o ID da voz em JARVIS_ELEVENLABS_VOZ, ou crie a chave sem restrições")
        if codigo != 200:
            raise ErroNuvem(f"não consegui listar as vozes ({codigo})")
        try:
            vozes = [(str(v["voice_id"]), str(v.get("name") or "")) for v in json.loads(corpo)["voices"]]
        except (ValueError, KeyError, TypeError):
            raise ErroNuvem("a lista de vozes veio em um formato que não entendi") from None
        if not vozes:
            raise ErroNuvem("a sua biblioteca de vozes está vazia")
        if self.voz_pedida:
            pedida = self.voz_pedida.casefold()
            achadas = (
                [v for v in vozes if v[0] == self.voz_pedida]  # o ID, conferido contra a biblioteca
                or [v for v in vozes if v[1].casefold() == pedida]
                or [v for v in vozes if pedida in v[1].casefold()]
            )
            if not achadas:
                # Voz errada não vale desligar a nuvem: usa a primeira e conta o que houve. O
                # usuário pediu para o Jarvis fazer o que dá, não para parar por causa de um ajuste.
                nomes = ", ".join(f"{n} ({i})" for i, n in vozes[:8])
                self._avisar(
                    f"[voz] Não há voz '{self.voz_pedida}' na sua biblioteca da ElevenLabs"
                    # A dica do tamanho só para quem claramente tentou pôr um ID (longo e com
                    # dígito no meio): para um nome como "Joaquim" ela confundiria em vez de ajudar.
                    + (f" — o que você pôs tem {len(self.voz_pedida)} caracteres e os ID de lá têm 20,"
                       " parece que vieram caracteres a mais na cópia"
                       if _ID_DE_VOZ.match(self.voz_pedida) is None and self.voz_pedida.isalnum()
                       and len(self.voz_pedida) >= 15 and any(c.isdigit() for c in self.voz_pedida) else "")
                    + f". Uso {vozes[0][1] or vozes[0][0]} por enquanto. As suas: {nomes}."
                )
                achadas = [vozes[0]]
            self.voz_id, self.voz_nome = achadas[0]
        else:
            self.voz_id, self.voz_nome = vozes[0]
        self._gravar()

    def testar(self) -> str:
        """/nuvem: tenta de novo (mesmo desligada), sintetiza uma frase curta e conta o que aconteceu."""
        self.desligada, self.dormindo_ate, self._falhas = "", 0.0, 0
        try:
            self.preparar()
        except ErroNuvem as erro:
            self.desligada = str(erro)
            return f"Não deu: {erro}."
        except Exception as erro:  # noqa: BLE001
            self.desligada = f"{type(erro).__name__}: {erro}"
            return f"Não deu: {type(erro).__name__}: {erro}."
        inicio = self._relogio()
        audio, taxa = self("Teste da voz da nuvem.")
        levou = self._relogio() - inicio
        if self.ultimo_motivo:
            return f"A API respondeu, mas a frase saiu pela voz local: {self.ultimo_motivo}. {self.situacao()}"
        return f"Funcionou: {len(audio) / taxa:.1f} s de áudio em {levou:.1f} s, modelo {self.modelo_id}. {self.situacao()}"

    # --- sintetizar -----------------------------------------------------------------------

    def __call__(self, frase: str) -> tuple[Any, int]:
        """O som da frase: pela nuvem se der, senão pela reserva. Nunca levanta por culpa da nuvem."""
        velocidade = float(self.velocidade)
        if getattr(self.reserva, "velocidade", None) not in (None, velocidade):
            self.reserva.velocidade = velocidade  # type: ignore[attr-defined]
        if not self.ativa or not self.voz_id:
            self.ultimo_motivo = self.desligada or ("crédito do mês acabou" if self._sem_credito() else "descansando depois de falhas")
            return self.reserva(frase)
        guardada = self._ler_guardada(frase, velocidade)
        if guardada is not None:
            self.ultimo_motivo = ""
            return guardada
        try:
            audio = self._sintetizar(frase, velocidade)
        except ErroNuvem as erro:
            self.ultimo_motivo = str(erro)
            return self.reserva(frase)
        self.ultimo_motivo = ""
        self._guardar(frase, velocidade, audio)
        return audio, TAXA

    def _sintetizar(self, frase: str, velocidade: float) -> Any:
        import numpy as np

        with self._trava:
            if self.gastas + len(frase) > self.limite:
                raise ErroNuvem("crédito do mês acabou")
            corpo = {
                "text": frase,
                "model_id": self.modelo_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": min(VELOCIDADE_MAXIMA, max(VELOCIDADE_MINIMA, velocidade))},
            }
            try:
                codigo, dados = self._pedir("POST", f"/text-to-speech/{self.voz_id}?output_format=pcm_{TAXA}", self.chave, corpo)
            except Exception as erro:  # noqa: BLE001 - rede fora, tempo esgotado
                self._falhou(f"a ElevenLabs não respondeu ({type(erro).__name__})")
                raise ErroNuvem(f"a ElevenLabs não respondeu ({type(erro).__name__})") from None
            if codigo == 200 and dados:
                self._falhas = 0
                self.gastas += len(frase)
                if self.creditos is not None:
                    self.creditos = (self.creditos[0] + len(frase), self.creditos[1])
                self._gravar()
                return np.frombuffer(dados[: len(dados) - len(dados) % 2], dtype="<i2").copy()
            motivo = _motivo(codigo, dados)
            if codigo == 404 and b"voice" in dados.lower():
                # Voz que não existe não melhora tentando de novo: sem isto, o ID torto derrubava
                # TODA frase para a voz local, para sempre, e só o /nuvem contava o porquê.
                errada = self.voz_id
                if self._trocar_por_uma_que_existe(errada):
                    self._avisar(
                        f"[voz] A voz {errada} não existe na sua conta da ElevenLabs. Passei a usar "
                        f"{self.voz_nome or self.voz_id}. Corrija ou apague JARVIS_ELEVENLABS_VOZ no .env."
                    )
                    raise ErroNuvem(f"a voz {errada} não existe; troquei para {self.voz_nome or self.voz_id}")
                self.desligada = motivo
                self._avisar(f"[voz] Voz da nuvem desligada: {motivo}. Sigo com a voz local.")
            elif codigo == 401:
                self.desligada = motivo
                self._avisar(f"[voz] Voz da nuvem desligada: {motivo}. Sigo com a voz local.")
            elif codigo == 429 and "quota" in motivo.lower():
                self.gastas = self.limite  # até o mês virar, local
                self._gravar()
                self._avisar("[voz] O crédito do mês na ElevenLabs acabou: sigo com a voz local até o mês virar.")
            else:
                self._falhou(motivo)
            raise ErroNuvem(motivo)

    def _trocar_por_uma_que_existe(self, errada: str) -> bool:
        """Pede a biblioteca e assume a primeira voz que não seja a que não existe."""
        self.voz_pedida, self.voz_id, self.voz_nome = "", "", ""
        try:
            self._achar_voz()
        except (ErroNuvem, OSError):
            return False
        return bool(self.voz_id) and self.voz_id != errada

    def _falhou(self, motivo: str) -> None:
        self._falhas += 1
        log.warning("A voz da nuvem falhou: %s", motivo)
        if self._falhas >= FALHAS_PARA_DESCANSAR:
            self._falhas = 0
            self.dormindo_ate = self._relogio() + DESCANSO
            self._avisar(f"[voz] A voz da nuvem falhou {FALHAS_PARA_DESCANSAR} vezes seguidas ({motivo}); por cinco minutos fico na voz local.")

    # --- frases guardadas -----------------------------------------------------------------

    def _chave_da_guardada(self, frase: str, velocidade: float) -> Path | None:
        if len(frase) > MAX_GUARDADA:
            return None
        resumo = hashlib.sha256(f"{self.voz_id}|{self.modelo_id}|{velocidade:.2f}|{frase}".encode()).hexdigest()[:24]
        return self._guardadas / f"{resumo}.pcm"

    def _ler_guardada(self, frase: str, velocidade: float) -> tuple[Any, int] | None:
        import numpy as np

        arquivo = self._chave_da_guardada(frase, velocidade)
        if arquivo is None:
            return None
        try:
            dados = arquivo.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            log.exception("Não consegui ler a frase guardada %s", arquivo)
            return None
        if len(dados) < 2:
            return None
        return np.frombuffer(dados[: len(dados) - len(dados) % 2], dtype="<i2").copy(), TAXA

    def _guardar(self, frase: str, velocidade: float, audio: Any) -> None:
        arquivo = self._chave_da_guardada(frase, velocidade)
        if arquivo is None:
            return
        try:
            self._guardadas.mkdir(parents=True, exist_ok=True)
            arquivo.write_bytes(audio.astype("<i2").tobytes())
        except OSError:
            log.exception("Não consegui guardar a frase %r", frase)


def _motivo(codigo: int, dados: bytes) -> str:
    """O que a API disse, em uma linha curta."""
    texto = ""
    try:
        erro = json.loads(dados or b"{}")
        detalhe = erro.get("detail", erro) if isinstance(erro, dict) else erro
        if isinstance(detalhe, dict):
            texto = str(detalhe.get("message") or detalhe.get("status") or "")
        elif detalhe:
            texto = str(detalhe)
    except ValueError:
        texto = dados[:120].decode("utf-8", "replace")
    texto = " ".join(texto.split())[:160]
    if codigo == 401:
        return "chave recusada (401)" + (f": {texto}" if texto else "")
    if codigo == 429:
        return f"limite da ElevenLabs (429): {texto or 'quota ou excesso de pedidos'}"
    if codigo == 422:
        return f"pedido recusado (422): {texto or 'voz ou modelo inválido'}"
    return f"resposta {codigo}" + (f": {texto}" if texto else "")
