"""O navegador próprio do Jarvis para o Protheus WebApp: medir a tela e gravar a calibração.

Este arquivo **não digita inventário**. Ele faz duas coisas da sessão de calibração:

- **C-0, medir:** a tela do WebApp é feita de elementos legíveis ou de um `canvas`? A resposta
  decide se a etapa C anda pelo elemento (determinístico) ou volta para leitura por imagem.
- **Gravar:** o Bruno faz o caminho na mão e o Jarvis anota cada passo — o rótulo do campo, a
  ordem, a tecla e a espera — e no fim escreve `roteiros/mata270.yaml`.

Decisões que valem registro:

- **Navegador próprio, perfil próprio.** Não é o Chrome do dia a dia do Bruno: sem as abas dele,
  sem os logins dele, sem os cookies dele. Se a automação se perder, ela se perde sozinha.
- **Endereço preso.** A janela só navega para o endereço configurado no `.env`. Qualquer desvio
  para outro host fecha a sessão — é o mesmo princípio da leitura do Protheus, e aqui vale mais,
  porque esta janela tem a sessão do ERP aberta.
- **Senha nunca é gravada.** Campo de senha entra no roteiro como campo; o conteúdo, nunca, nem
  mascarado parcialmente.
- **Âncora por rótulo e ordem, nunca por id gerado.** O WebApp remonta identificadores a cada
  abertura; roteiro preso a id quebra na segunda execução e não avisa.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ESPERA_PADRAO = 15.0  # segundos que a especificação dá por passo
# Quando o servidor aceita a conexão e fecha sem responder, o Jarvis tenta de novo afrouxando o que
# os navegadores novos apertaram. Cada degrau tem um motivo medido no mundo real:
TENTATIVAS = (
    ("como veio", ()),
    # Do Chrome 124 em diante o aperto de mão TLS leva criptografia pós-quântica, e a primeira
    # mensagem fica grande demais para servidor e balanceador antigos, que fecham a conexão.
    ("sem criptografia pós-quântica no TLS", ("--disable-features=X25519MLKEM768,PostQuantumKyber,TLS13EarlyData",)),
    # Appserver que anuncia HTTP/2 por ALPN e não entrega.
    ("sem HTTP/2", ("--disable-http2",)),
    ("sem pós-quântica e sem HTTP/2", ("--disable-features=X25519MLKEM768,PostQuantumKyber,TLS13EarlyData", "--disable-http2")),
)
# Último recurso, e o mais forte: o Chrome que o usuário já tem instalado. Se o servidor conversa
# com ele no dia a dia, conversa aqui — o que importava era o perfil separado (sem as abas, os
# logins e os cookies dele), não o binário. O navegador que vem com o Playwright é mais novo que o
# instalado, e servidor antigo às vezes recusa o que o navegador novo traz.
CHROME_DO_USUARIO = "o Chrome que você já tem instalado"
# Para procurar sem poluir a tela, a janela vai para fora dela — e não para o modo escondido.
# Navegador escondido se anuncia como tal (o "HeadlessChrome" no User-Agent, entre outras marcas), e
# servidor com proteção na frente fecha a conexão com ele. Foi o que aconteceu com o WebApp da TOTVS:
# o mesmo Chrome, com o mesmo perfil novo, abre a página com janela e não abre escondido.
FORA_DA_TELA = ("--window-position=-32000,-32000", "--window-size=1280,900")
MAX_PASSOS = 2000
MAX_TEXTO = 200
MASCARA = "***"
TIPOS_SEM_VALOR = ("password",)
# Campo cujo rótulo cheira a senha, mesmo que o tipo não diga.
_ROTULO_DE_SENHA = re.compile(r"senha|password|pass|token|chave", re.IGNORECASE)


class ErroWebApp(Exception):
    """Qualquer coisa que impeça a sessão de calibração."""


class _SemChrome(Exception):
    """O Chrome instalado não existe nesta máquina. Não é erro de rede: a escada segue."""


def mesma_origem(endereco: str, outro: str) -> bool:
    """Mesmo esquema, host e porta. É o que prende a janela ao WebApp configurado."""
    um, dois = urllib.parse.urlsplit(endereco), urllib.parse.urlsplit(outro)
    return (um.scheme, um.hostname, um.port) == (dois.scheme, dois.hostname, dois.port)


def _vale_tentar_de_novo(erro: Exception) -> bool:
    """Só o 'aceitou e fechou sem responder' melhora afrouxando o navegador. O resto, não."""
    return any(marca in str(erro) for marca in ("ERR_EMPTY_RESPONSE", "ERR_CONNECTION_RESET", "ERR_SSL_PROTOCOL_ERROR", "ERR_CONNECTION_CLOSED"))


def conferir_endereco(bruto: str) -> str:
    # A barra do fim NÃO é tirada: `/webapp` e `/webapp/` são endereços diferentes para o servidor,
    # e o WebApp da TOTVS responde a `/webapp` mandando o navegador para outro lugar. Tirar a barra
    # "para limpar" foi o que impediu a página de abrir.
    endereco = str(bruto or "").strip()
    if not endereco:
        return ""
    partes = urllib.parse.urlsplit(endereco)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ValueError(f"JARVIS_PROTHEUS_WEBAPP precisa ser um endereço http(s) completo, recebi: {bruto}")
    if partes.username or partes.password:
        raise ValueError("JARVIS_PROTHEUS_WEBAPP não leva usuário nem senha no endereço.")
    return endereco


@dataclass
class Medida:
    """O resultado do C-0."""

    elementos: int = 0
    campos: int = 0
    botoes: int = 0
    canvas: int = 0
    area_do_canvas: float = 0.0  # fração da janela ocupada pelo maior canvas
    com_shadow: int = 0
    titulo: str = ""

    @property
    def desenhada_em_canvas(self) -> bool:
        """A tela é um desenho, não uma página: não há elemento para ancorar."""
        return self.canvas > 0 and self.area_do_canvas >= 0.5 and self.campos == 0

    def veredito(self) -> str:
        if self.desenhada_em_canvas:
            return (
                f"PARE AQUI (C-0). A tela é desenhada num canvas que ocupa {self.area_do_canvas:.0%} da janela e não "
                "tem campo legível. Pelo elemento não dá; a etapa C teria de voltar para leitura por imagem, que é o "
                "caminho que a 0.20 desaconselhou. Leve isto para o desenvolvedor antes de seguir."
            )
        if self.campos == 0:
            return (
                "Não achei campo nenhum nesta tela. Ou ela ainda não terminou de carregar, ou o conteúdo está dentro "
                "de um quadro (iframe) que eu não alcancei. Espere carregar e meça de novo."
            )
        sombra = f", {self.com_shadow} dentro de shadow DOM" if self.com_shadow else ""
        return (
            f"Dá para ancorar por elemento: {self.campos} campo(s) e {self.botoes} botão(ões) legíveis{sombra}. "
            "O roteiro vai por rótulo e ordem, nunca por id gerado."
        )


@dataclass
class Passo:
    """Um passo gravado: o que o Bruno fez, descrito de um jeito que dê para repetir."""

    acao: str  # clicar · digitar · tecla · dialogo · navegar
    rotulo: str = ""
    tipo: str = ""
    ordem: int = 0
    valor: str = ""
    texto: str = ""
    espera: float = 0.0
    quando: str = ""

    def como_yaml(self, recuo: str = "  ") -> str:
        campos = [f"{recuo}- acao: {self.acao}"]
        for nome, valor in (("rotulo", self.rotulo), ("tipo", self.tipo), ("valor", self.valor), ("texto", self.texto)):
            if valor:
                campos.append(f'{recuo}  {nome}: "{str(valor)[:MAX_TEXTO].replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"')
        if self.ordem:
            campos.append(f"{recuo}  ordem: {self.ordem}")
        if self.espera:
            campos.append(f"{recuo}  espera: {self.espera:.1f}")
        return "\n".join(campos)


# O que roda dentro da página: descreve o alvo por rótulo e ordem, atravessa shadow DOM e nunca
# deixa sair o conteúdo de campo de senha.
_ESPIAO = """
(() => {
  if (window.__jarvisEspiao) return 'ja';
  window.__jarvisEspiao = true;
  // Os passos ficam no sessionStorage, não numa variável: o caminho real troca de página (entrada
  // → MATA270), e variável morre junto com a página. sessionStorage é da origem e atravessa.
  const CHAVE = '__jarvisPassos';
  const lidos = () => { try { return JSON.parse(sessionStorage.getItem(CHAVE) || '[]'); } catch (e) { return []; } };
  const guardar = (lista) => { try { sessionStorage.setItem(CHAVE, JSON.stringify(lista.slice(-2000))); } catch (e) {} };
  const texto = (e) => (e && (e.innerText || e.textContent) || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
  const ehSenha = (a) => a.type === 'password' ||
    /senha|password|pass|token|chave/i.test((a.name || '') + ' ' + (a.id || '') + ' ' + (a.placeholder || '') + ' ' + (a.getAttribute('aria-label') || ''));
  const rotulo = (a) => {
    const porAria = a.getAttribute && (a.getAttribute('aria-label') || a.getAttribute('title') || a.getAttribute('placeholder'));
    if (porAria) return porAria.trim().slice(0, 200);
    if (a.labels && a.labels.length) return texto(a.labels[0]);
    const raiz = a.getRootNode();
    if (a.id && raiz.querySelector) {
      const marca = raiz.querySelector('label[for="' + CSS.escape(a.id) + '"]');
      if (marca) return texto(marca);
    }
    let pai = a.parentElement, voltas = 0;
    while (pai && voltas++ < 4) {
      const marca = pai.querySelector && pai.querySelector('label');
      if (marca && texto(marca)) return texto(marca);
      pai = pai.parentElement;
    }
    return texto(a).slice(0, 80) || (a.name || '').slice(0, 80);
  };
  const ordem = (a) => {
    try {
      const iguais = Array.from((a.getRootNode().querySelectorAll || document.querySelectorAll).call(a.getRootNode(), a.tagName));
      return iguais.indexOf(a) + 1;
    } catch (e) { return 0; }
  };
  const anotar = (p) => { const lista = lidos(); if (lista.length < 2000) { lista.push(Object.assign({ quando: Date.now() }, p)); guardar(lista); } };
  const alvoReal = (ev) => (ev.composedPath && ev.composedPath()[0]) || ev.target;
  document.addEventListener('click', (ev) => {
    const a = alvoReal(ev); if (!a || !a.tagName) return;
    anotar({ acao: 'clicar', rotulo: rotulo(a), tipo: (a.tagName || '').toLowerCase(), ordem: ordem(a) });
  }, true);
  // `change` NÃO atravessa shadow DOM (composed: false) e o WebApp põe campo lá dentro: sem o
  // `input` (composed: true), justamente os campos do Protheus não seriam gravados.
  const digitando = new Map();
  const descrever = (a) => ({ acao: 'digitar', rotulo: rotulo(a), tipo: (a.type || a.tagName || '').toLowerCase(),
                              ordem: ordem(a), valor: ehSenha(a) ? '***' : String(a.value == null ? '' : a.value).slice(0, 200) });
  const soltar = (a) => { if (digitando.has(a)) { anotar(digitando.get(a)); digitando.delete(a); } };
  document.addEventListener('input', (ev) => {
    const a = alvoReal(ev); if (!a || !a.tagName) return;
    digitando.set(a, descrever(a));  // guarda o último valor; só vira passo quando o campo é deixado
  }, true);
  document.addEventListener('change', (ev) => { const a = alvoReal(ev); if (a && a.tagName) { digitando.set(a, descrever(a)); soltar(a); } }, true);
  document.addEventListener('focusout', (ev) => { const a = alvoReal(ev); if (a) soltar(a); }, true);
  window.__jarvisFechar = () => { Array.from(digitando.keys()).forEach(soltar); };
  // Antes de sair da página, o campo ainda em edição vira passo: senão o último some na navegação.
  window.addEventListener('beforeunload', () => window.__jarvisFechar(), true);
  window.addEventListener('pagehide', () => window.__jarvisFechar(), true);
  document.addEventListener('keydown', (ev) => {
    if (['Enter', 'Tab', 'Escape', 'F12'].includes(ev.key) || ev.key.startsWith('F')) {
      const a = alvoReal(ev);
      anotar({ acao: 'tecla', valor: ev.key, rotulo: a && a.tagName ? rotulo(a) : '' });
    }
  }, true);
  const olho = new MutationObserver((mudancas) => {
    for (const m of mudancas) for (const no of m.addedNodes) {
      if (!no.querySelector && !no.matches) continue;
      const caixa = (no.matches && no.matches('[role=dialog],[role=alertdialog],[role=alert]')) ? no
        : (no.querySelector && no.querySelector('[role=dialog],[role=alertdialog],[role=alert]'));
      if (caixa && texto(caixa)) anotar({ acao: 'dialogo', texto: texto(caixa) });
    }
  });
  olho.observe(document.documentElement, { childList: true, subtree: true });
  return 'ok';
})()
"""

_MEDIR = """
(() => {
  const todos = [];
  const juntar = (raiz, sombra) => {
    raiz.querySelectorAll('*').forEach((e) => {
      todos.push({ tag: e.tagName.toLowerCase(), tipo: (e.type || '').toLowerCase(), sombra: sombra,
                   largura: e.getBoundingClientRect().width, altura: e.getBoundingClientRect().height });
      if (e.shadowRoot) juntar(e.shadowRoot, true);
    });
  };
  juntar(document, false);
  const campo = (e) => ['input', 'select', 'textarea'].includes(e.tag) && !['hidden', 'submit', 'button'].includes(e.tipo);
  const telas = todos.filter((e) => e.tag === 'canvas');
  const area = telas.length ? Math.max(...telas.map((e) => (e.largura * e.altura))) / (window.innerWidth * window.innerHeight) : 0;
  return { elementos: todos.length, campos: todos.filter(campo).length,
           botoes: todos.filter((e) => e.tag === 'button' || e.tipo === 'submit' || e.tipo === 'button').length,
           canvas: telas.length, area_do_canvas: area,
           com_shadow: todos.filter((e) => e.sombra && campo(e)).length, titulo: document.title };
})()
"""


class Navegador:
    """A janela própria do Jarvis, presa ao endereço do WebApp."""

    def __init__(
        self,
        endereco: str,
        perfil: Path,
        visivel: bool = True,
        abrir_playwright: Callable[[], Any] | None = None,
        executavel: str = "",
    ) -> None:
        self.endereco = conferir_endereco(endereco)
        if not self.endereco:
            raise ErroWebApp("falta JARVIS_PROTHEUS_WEBAPP no .env (o endereço do Protheus no navegador).")
        self.perfil, self.visivel = perfil, visivel
        # Normalmente vazio: o `playwright install chromium` põe o navegador no lugar certo. Serve
        # para apontar um Chrome já instalado, e para os testes, que usam o que existe na máquina.
        self.executavel = executavel
        self._abrir = abrir_playwright
        self._pw: Any = None
        self._contexto: Any = None
        self.pagina: Any = None
        self.desvio = ""  # o endereço para onde ela tentou ir, quando tentou
        self.tentativa = ""  # qual degrau fez a página abrir
        self.falhou_antes = False  # houve degrau que falhou antes do que funcionou?

    async def abrir(self, minimo: bool = False, avisar: Callable[[str], None] | None = None) -> None:
        """Abre a janela no endereço configurado.

        Primeiro **procura em silêncio** qual configuração o servidor aceita (janelas escondidas,
        que não piscam na tela), e só então abre a janela de verdade com a que funcionou. Sem isso
        o usuário via cinco janelas abrirem e fecharem sem conseguir ler nada.

        `minimo` abre sem nenhum ajuste meu, como o navegador comum: serve para separar
        "o servidor não abre" de "alguma coisa que eu faço atrapalha".
        """
        if self.pagina is not None:
            return
        conta = avisar or (lambda _t: None)
        if minimo:
            await self._subir((), minimo=True)
            self.tentativa = TENTATIVAS[0][0]
            return
        nome, extras, canal = await self._descobrir(conta)
        self.tentativa = nome
        await self._subir(extras, canal=canal)

    async def _descobrir(self, conta: Callable[[str], None]) -> tuple[str, tuple[str, ...], str]:
        """Qual configuração o servidor aceita. Tudo escondido: nada pisca na tela do usuário."""
        erro_final: Exception | None = None
        # O Chrome instalado vem primeiro: se o servidor conversa com ele no dia a dia, conversa
        # aqui, e aí não há motivo para o usuário esperar as tentativas com o navegador que veio junto.
        for nome, extras, canal in [(CHROME_DO_USUARIO, (), "chrome"), *((n, e, "") for n, e in TENTATIVAS)]:
            conta(f"testando: {nome}…")
            try:
                await self._subir(extras, canal=canal, escondido=True)
                await self.fechar()
                return nome, tuple(extras), canal
            except ErroWebApp:
                raise
            except _SemChrome as erro:
                log.info("%s", erro)  # não é erro de rede: a escada segue com o que veio junto
                await self.fechar()
            except Exception as erro:  # noqa: BLE001 - cada degrau pode falhar; o último manda
                erro_final = erro
                self.falhou_antes = True
                await self.fechar()
                if not _vale_tentar_de_novo(erro):
                    break  # nome que não resolve ou porta fechada não melhoram trocando o navegador
        raise ErroWebApp(self._explicar(erro_final or Exception("falha desconhecida")))

    async def _subir(self, extras: Sequence[str], minimo: bool = False, canal: str = "", escondido: bool = False) -> None:
        """Uma tentativa: sobe o navegador com estes ajustes e abre o endereço."""
        abrir = self._abrir
        if abrir is None:  # com um driver injetado, a biblioteca de verdade não é necessária
            try:
                # A API assíncrona, não a síncrona: o Jarvis roda em asyncio, e a síncrona recusa
                # funcionar dentro de um laço asyncio — estouraria na primeira execução de verdade.
                from playwright.async_api import async_playwright
            except ImportError:
                raise ErroWebApp(
                    "falta a biblioteca do navegador. Na pasta do Jarvis, rode: "
                    r".\.venv\Scripts\python.exe -m pip install -r requirements-calibracao.txt "
                    r"e depois .\.venv\Scripts\python.exe -m playwright install chromium"
                ) from None
            abrir = async_playwright
        self._pw = await abrir().start()
        self.perfil.mkdir(parents=True, exist_ok=True)
        base = [] if minimo else [
            "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
            "--disable-background-networking", "--disable-sync", "--disable-component-update",
            "--disable-default-apps", "--disable-domain-reliability", "--no-pings",
            "--metrics-recording-only", "--disable-breakpad",
        ]  # fmt: skip
        # `escondido` é para procurar sem incomodar; com janela de verdade, só que fora da tela.
        # O modo headless fica para quem já pediu uma sessão sem janela (os testes).
        fora = FORA_DA_TELA if (escondido and self.visivel) else ()
        opcoes: dict[str, Any] = {"headless": not self.visivel, "args": [*base, *extras, *fora]}
        if canal:
            opcoes["channel"] = canal  # o navegador instalado na máquina, não o que veio junto
        elif self.executavel:
            opcoes["executable_path"] = self.executavel
        try:
            # Perfil próprio: sem as abas, os logins e os cookies do Bruno.
            self._contexto = await self._pw.chromium.launch_persistent_context(str(self.perfil), **opcoes)
        except Exception as erro:  # noqa: BLE001 - a mensagem do Playwright não ajuda quem não é programador
            if canal:  # sem o Chrome instalado, a escada segue com o navegador que veio junto
                raise _SemChrome(f"não achei {CHROME_DO_USUARIO}: {str(erro)[:120]}") from None
            if "Executable doesn't exist" in str(erro):
                raise ErroWebApp(
                    "a biblioteca do navegador está instalada, mas o navegador dela não. Na pasta do Jarvis, rode: "
                    r".\.venv\Scripts\python.exe -m playwright install chromium"
                ) from None
            raise
        self.pagina = self._contexto.pages[0] if self._contexto.pages else await self._contexto.new_page()
        self.pagina.on("framenavigated", self._conferir_navegacao)
        await self.pagina.goto(self.endereco, timeout=int(ESPERA_PADRAO * 1000 * 2))

    def _explicar(self, erro: Exception) -> str:
        """A mensagem do Playwright em português, com o palpite do que costuma ser."""
        texto = str(erro)
        partes = urllib.parse.urlsplit(self.endereco)
        porta = f":{partes.port}" if partes.port else ""
        # O palpite tem de olhar o que já está configurado: mandar "troque para http" quando o
        # endereço JÁ é http manda o usuário para o lado errado — foi o que aconteceu de verdade.
        outro = "https" if partes.scheme == "http" else "http"
        trocado = f"{outro}://{partes.hostname}{porta}{partes.path}"
        palpites = (
            ("ERR_EMPTY_RESPONSE",
             f"o servidor aceitou a conexão em {partes.hostname}{porta} e fechou sem responder. "
             f"O endereço está em {partes.scheme}; quase sempre é o outro: tente JARVIS_PROTHEUS_WEBAPP={trocado}. "
             "Se não for isso, essa porta pode ser de outro serviço do appserver."),
            ("ERR_CONNECTION_REFUSED", f"nada atende em {partes.hostname}{porta}. Confira a porta com a consultoria."),
            ("ERR_NAME_NOT_RESOLVED", f"o nome {partes.hostname} não existe nesta rede. Confira o endereço, ou se precisa de VPN."),
            ("ERR_CONNECTION_TIMED_OUT", f"{partes.hostname}{porta} não respondeu a tempo. Pode ser firewall ou VPN."),
            ("ERR_CERT", "o certificado do servidor não foi aceito pelo navegador. Isso é com a consultoria."),
            ("ERR_SSL", f"a conversa de segurança falhou em {partes.hostname}{porta}: tente JARVIS_PROTHEUS_WEBAPP={trocado}."),
            ("Timeout", f"{partes.hostname}{porta} não terminou de carregar a tempo."),
        )
        for marca, explicacao in palpites:
            if marca in texto:
                return f"não consegui abrir o Protheus: {explicacao}"
        return f"não consegui abrir o Protheus em {self.endereco}: {texto[:200]}"

    def _conferir_navegacao(self, quadro: Any) -> None:
        """A trava de endereço: marca o desvio, e a próxima chamada recusa e fecha a janela.

        Aqui não há interceptação de pedido. Interceptar e reemitir cada pedido é o que um
        servidor exigente recusa — e foi o que impediu o WebApp da TOTVS de abrir, enquanto o
        mesmo endereço abria no navegador comum. O preço é que a página de outro endereço chega a
        carregar antes de eu reagir; em troca, a ferramenta funciona. O que importa continua
        protegido: a sessão do ERP é daquela origem e outra página não a lê, a senha é digitada
        pelo usuário e não enviada por mim, e a sessão para na primeira recolha depois do desvio.
        """
        """A janela só vai para o endereço configurado. Esta janela tem a sessão do ERP aberta."""
        try:
            destino = quadro.url
            # `chrome-error://` é a página que o próprio navegador mostra depois que o filtro
            # recusou: guardar isso apagaria o endereço que de fato foi tentado.
            if quadro.parent_frame is not None or not destino or destino.startswith(("about:", "data:", "chrome-error:", "chrome:")):
                return
        except Exception:  # noqa: BLE001 - o quadro pode ter morrido
            return
        if not mesma_origem(self.endereco, destino) and not self.desvio:
            self.desvio = destino  # fechar aqui penduraria o navegador; quem recusa é a próxima chamada

    async def medir(self) -> Medida:
        """C-0: a tela é feita de elemento legível ou de um desenho?"""
        self._exigir_aberta()
        bruto = await self.pagina.evaluate(_MEDIR)
        return Medida(**{campo: bruto.get(campo, 0) for campo in
                         ("elementos", "campos", "botoes", "canvas", "area_do_canvas", "com_shadow", "titulo")})  # fmt: skip

    async def espiar(self) -> None:
        """Põe o espião na página. Chamado a cada navegação, porque a página nova não o tem."""
        self._exigir_aberta()
        await self.pagina.evaluate(_ESPIAO)

    async def colher(self) -> list[Passo]:
        """Pega o que o espião anotou desde a última colheita."""
        self._exigir_aberta()
        try:
            brutos = await self.pagina.evaluate(
                "(() => { if (window.__jarvisFechar) window.__jarvisFechar(); "  # campo ainda em edição vira passo
                "let p = []; try { p = JSON.parse(sessionStorage.getItem('__jarvisPassos') || '[]'); "
                "sessionStorage.removeItem('__jarvisPassos'); } catch (e) {} return p; })()"
            )
        except Exception as erro:  # noqa: BLE001 - a página pode ter trocado no meio
            log.info("Não colhi passos desta vez: %s", erro)
            return []
        passos = []
        anterior = 0.0
        for bruto in brutos or []:
            quando = float(bruto.get("quando") or 0) / 1000
            passos.append(
                Passo(
                    acao=str(bruto.get("acao") or "clicar"),
                    rotulo=str(bruto.get("rotulo") or "")[:MAX_TEXTO],
                    tipo=str(bruto.get("tipo") or "")[:40],
                    ordem=int(bruto.get("ordem") or 0),
                    valor=str(bruto.get("valor") or "")[:MAX_TEXTO],
                    texto=str(bruto.get("texto") or "")[:MAX_TEXTO],
                    espera=round(quando - anterior, 1) if anterior and quando > anterior else 0.0,
                    quando=datetime.now().isoformat(timespec="seconds"),
                )
            )
            anterior = quando or anterior
        return passos

    def _exigir_aberta(self) -> None:
        if self.desvio and self.pagina is not None:
            raise ErroWebApp(f"a janela tentou ir para {self.desvio}, que não é o endereço configurado. Parei a sessão.")
        if self.pagina is None:
            raise ErroWebApp("a janela do Protheus não está aberta." + (f" Ela tentou ir para {self.desvio} e eu fechei." if self.desvio else ""))

    async def fechar(self) -> None:
        for alvo, nome in ((self._contexto, "contexto"), (self._pw, "playwright")):
            try:
                if alvo is not None:
                    await (alvo.close() if nome == "contexto" else alvo.stop())
            except Exception:  # noqa: BLE001 - fechando
                log.info("Falha ao fechar o %s do navegador", nome)
        self._contexto = self._pw = self.pagina = None


async def diagnosticar(endereco: str, perfil: Path, executavel: str = "") -> list[str]:
    """Testa as combinações que importam e devolve o relatório, com o erro cru de cada uma.

    Existe porque adivinhar pela descrição do sintoma já custou seis palpites errados: aqui o que
    volta é o que o navegador disse, sem a minha tradução no meio.
    """
    casos = [
        ("navegador que veio junto, janela normal", {"canal": "", "escondido": False}),
        ("navegador que veio junto, janela escondida", {"canal": "", "escondido": True}),
        ("seu Chrome, janela normal", {"canal": "chrome", "escondido": False}),
        ("seu Chrome, janela escondida", {"canal": "chrome", "escondido": True}),
    ]
    linhas = [f"Endereço testado: {endereco}"]
    for nome, como in casos:
        navegador = Navegador(endereco, perfil / nome.replace(",", "").replace(" ", "-"), visivel=not como["escondido"], executavel=executavel)
        try:
            await navegador._subir((), canal=str(como["canal"]))
            titulo = await navegador.pagina.title()
            atual = navegador.pagina.url
            linhas.append(f"  OK   {nome}: título '{titulo[:60]}', endereço final {atual[:90]}")
        except Exception as erro:  # noqa: BLE001 - é um diagnóstico: o erro cru é o que interessa
            linhas.append(f"  FALHA {nome}: {type(erro).__name__}: {str(erro).splitlines()[0][:160]}")
        finally:
            await navegador.fechar()
    return linhas


@dataclass
class Roteiro:
    """O que sai da sessão: os passos, as mensagens vistas e o que ficou por responder."""

    rotina: str = "MATA270"
    endereco: str = ""
    medida: Medida = field(default_factory=Medida)
    passos: list[Passo] = field(default_factory=list)
    mensagens: list[str] = field(default_factory=list)

    def anotar(self, novos: Sequence[Passo]) -> None:
        for passo in novos:
            if len(self.passos) >= MAX_PASSOS:
                return
            if passo.acao == "dialogo":
                if passo.texto and passo.texto not in self.mensagens:
                    self.mensagens.append(passo.texto)
            self.passos.append(passo)

    @property
    def campos(self) -> list[str]:
        """Os rótulos dos campos preenchidos, na ordem em que foram preenchidos."""
        vistos: list[str] = []
        for passo in self.passos:
            if passo.acao == "digitar" and passo.rotulo and passo.rotulo not in vistos:
                vistos.append(passo.rotulo)
        return vistos

    def como_yaml(self) -> str:
        linhas = [
            "# Roteiro da calibração da MATA270 no Protheus WebApp.",
            "# Gravado pelo Jarvis com o Bruno conduzindo. Revise antes do primeiro uso.",
            "# Âncora é rótulo e ordem, nunca id gerado: o WebApp remonta id a cada abertura.",
            "# A senha não está aqui, e não deve ser acrescentada: ela entra pelo .env na execução.",
            f"rotina: {self.rotina}",
            f'endereco: "{self.endereco}"',
            f"gravado_em: {datetime.now():%Y-%m-%d %H:%M}",
            "medida:",
            f"  campos_legiveis: {self.medida.campos}",
            f"  botoes: {self.medida.botoes}",
            f"  canvas: {self.medida.canvas}",
            f"  dentro_de_shadow_dom: {self.medida.com_shadow}",
            f"  da_para_ancorar_por_elemento: {str(not self.medida.desenhada_em_canvas).lower()}",
            "campos_na_ordem:",
            *(f'  - "{c}"' for c in self.campos),
            "passos:",
            *(p.como_yaml() for p in self.passos),
            "mensagens_vistas:",
            "  # Marque cada uma como normal (segue) ou erro (cancela o item e para).",
            *(f'  - texto: "{m}"\n    classificacao: a_decidir' for m in self.mensagens),
            "por_responder:",
            "  - um_item_por_inclusao_ou_confirmacao_no_fim: a_decidir   # especificação §6",
            "  - b7_doc_tamanho: a_decidir",
            "  - b7_cod_aceita_o_sku_do_fluxo: a_decidir                 # P-5",
            "  - zero_e_aceito: a_decidir",
            "  - mensagem_de_duplicidade: a_decidir",
        ]
        return "\n".join(linhas) + "\n"

    def salvar(self, pasta: Path) -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / f"{self.rotina.lower()}.yaml"
        arquivo.write_text(self.como_yaml(), encoding="utf-8")
        return arquivo


class Calibracao:
    """A sessão: abre, mede, grava enquanto o Bruno trabalha, e salva o roteiro."""

    def __init__(self, navegador: Navegador, pasta: Path, rotina: str = "MATA270", relogio: Callable[[], float] = time.monotonic) -> None:
        self.navegador, self.pasta, self._relogio = navegador, pasta, relogio
        self.roteiro = Roteiro(rotina=rotina, endereco=navegador.endereco)
        self.comecou = 0.0

    async def comecar(self, avisar: Callable[[str], None] | None = None) -> Medida:
        await self.navegador.abrir(avisar=avisar)
        medida = await self.navegador.medir()
        self.roteiro.medida = medida
        if medida.desenhada_em_canvas:
            await self.navegador.fechar()
            raise ErroWebApp(medida.veredito())
        await self.navegador.espiar()
        self.comecou = self._relogio()
        return medida

    async def colher(self) -> int:
        """Chamada de tempos em tempos enquanto o Bruno trabalha. Devolve quantos passos novos."""
        try:
            await self.navegador.espiar()  # a página pode ter trocado: o espião é reposto
        except ErroWebApp:
            raise
        except Exception as erro:  # noqa: BLE001
            log.info("Não repus o espião: %s", erro)
        novos = await self.navegador.colher()
        self.roteiro.anotar(novos)
        return len(novos)

    async def terminar(self) -> Path:
        try:
            await self.colher()
        finally:
            await self.navegador.fechar()
        return self.roteiro.salvar(self.pasta)

    def situacao(self) -> str:
        minutos = (self._relogio() - self.comecou) / 60 if self.comecou else 0
        return (
            f"Calibração em andamento há {minutos:.0f} min: {len(self.roteiro.passos)} passo(s), "
            f"{len(self.roteiro.campos)} campo(s) preenchido(s), {len(self.roteiro.mensagens)} mensagem(ns) do Protheus. "
            "Digite /calibrar fim quando terminar."
        )
