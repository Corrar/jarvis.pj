"""Configuração do Jarvis, lida das variáveis de ambiente (arquivo .env)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jarvis.atalho import ErroAtalho, ler_combinacao
from jarvis.bom_dia import JANELA_PADRAO, ler_janela
from jarvis.projetos import ErroProjeto, ler_projetos
from jarvis.protheus import TENANT_PADRAO, conferir_endereco, ler_rotinas
from jarvis.voz.vigilia import ler_palavras

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
ARQUIVO_PERSONA = RAIZ_PROJETO / "persona.md"
PASTA_DADOS = RAIZ_PROJETO / ".jarvis"
PASTA_MEMORIA = RAIZ_PROJETO / "memoria"  # arquivos .md que você pode abrir e editar
ARQUIVO_REGISTRO = PASTA_DADOS / "registro.db"

MODELO_PADRAO = "sonnet"
VOZ_PADRAO = "pt_BR-faber-medium"
WHISPER_PADRAO = "small"
PASTAS_PADRAO = ("Documents", "Desktop", "Downloads")
_DESLIGADO = ("nao", "não", "n", "0", "false", "desligado")


class ErroConfig(Exception):
    """Configuração ausente ou inválida; a mensagem é mostrada ao usuário."""


@dataclass(frozen=True)
class Config:
    modelo: str
    usuario: str | None
    pastas: tuple[Path, ...]
    orcamento_usd: float | None
    voz: str = VOZ_PADRAO
    whisper: str = WHISPER_PADRAO
    programas: str = ""  # JARVIS_PROGRAMAS, no formato nome=comando;nome=comando
    confirmar_acoes: bool = False  # JARVIS_CONFIRMAR=sempre volta a pedir o "s" no terminal
    ver_paginas: bool = True  # JARVIS_TELA=nao desliga ver, clicar e digitar dentro das páginas
    devolver_foco: bool = True  # JARVIS_FOCO=navegador deixa o navegador na frente depois de mexer na página
    atalho: str = ""  # JARVIS_ATALHO: tecla de atalho global ("" = padrão, "nao" = desligada)
    painel: bool = True  # JARVIS_PAINEL=nao não abre a janela do painel
    # JARVIS_TURNO_LIMPO=nao solta a regra do turno limpo: o Jarvis passa a gravar e agir no mesmo
    # turno em que leu conteúdo de fora, sem esperar a próxima fala. Menos atrito, e o risco é um
    # texto lido (página, e-mail, registro do ERP) conseguir disparar ação sozinho. Ligada por padrão.
    turno_limpo: bool = True
    # Inventário diário Fluxo → Protheus (ver inventario.py). Desligado por padrão: ligar em
    # `homologacao` primeiro, e só passar para `producao` depois do aceite (§9 da especificação).
    inv_modo: str = "desligado"
    inv_hora: str = "17:00"
    inv_dias: tuple[str, ...] = ("seg", "ter", "qua", "qui", "sex")
    inv_max_itens: int = 300
    fluxo_url: str = ""
    fluxo_usuario: str = ""
    fluxo_senha: str = ""
    protheus_webapp: str = ""  # JARVIS_PROTHEUS_WEBAPP: o Protheus no navegador, para a calibração
    programas_na_tela: str = ""  # JARVIS_PROGRAMAS_TELA: executáveis a mais em que ele pode ver, clicar e digitar
    projetos: str = ""  # JARVIS_PROJETOS: nome=pasta;nome=pasta, os repositórios em que ele pode programar
    orcamento_trabalho_usd: float = 2.0  # JARVIS_ORCAMENTO_TRABALHO_USD: limite de gasto de cada trabalho de programação
    modelo_trabalho: str = ""  # JARVIS_MODELO_TRABALHO: modelo dos trabalhadores ("" = o mesmo da conversa)
    comandos: str = ""  # JARVIS_COMANDOS: programas a mais que os trabalhadores podem rodar
    terminal: bool = True  # JARVIS_TERMINAL=nao tira do Jarvis a ferramenta de rodar comandos
    palavras: tuple[str, ...] = ("jarvis",)  # JARVIS_PALAVRA: a palavra de ativação; () = desligada (JARVIS_PALAVRA=nao)
    whisper_da_ativacao: str = ""  # JARVIS_WHISPER_ATIVACAO: modelo mais leve só para achar a palavra ("" = o mesmo)
    ouvir_bloqueado: bool = False  # JARVIS_OUVIR_BLOQUEADO=sim atende pelo nome mesmo com a tela do Windows bloqueada
    respostas: tuple[str, ...] = ()  # JARVIS_RESPOSTAS: o que ele diz ao ouvir só o nome, separado por | ("" = as de fábrica)
    cidade: str = ""  # JARVIS_CIDADE: a cidade da previsão do tempo (do bom dia e de "como está o tempo?")
    bom_dia: tuple[int, int] | None = JANELA_PADRAO  # JARVIS_BOM_DIA: as horas em que o cumprimento do dia vale; None = desligado
    orcamento_missao_usd: float = 1.0  # JARVIS_ORCAMENTO_MISSAO_USD: limite de gasto de cada missão em segundo plano
    elevenlabs_chave: str = ""  # JARVIS_ELEVENLABS_KEY: com ela, a fala sai pela ElevenLabs (e pela voz local quando não der)
    elevenlabs_voz: str = ""  # JARVIS_ELEVENLABS_VOZ: nome ou ID da voz na biblioteca ("" = a primeira)
    elevenlabs_modelo: str = "flash"  # JARVIS_ELEVENLABS_MODELO: flash (mais minutos), multilingual (mais natural) ou turbo
    elevenlabs_limite: int = 9000  # JARVIS_ELEVENLABS_LIMITE: letras por mês na nuvem; passou, fica a voz local até o mês virar
    protheus_url: str = ""  # JARVIS_PROTHEUS_URL: endereço do REST do Protheus; sem ele, a leitura fica desligada
    protheus_usuario: str = ""  # JARVIS_PROTHEUS_USER (ou TOTVS_PRD_USER)
    protheus_senha: str = ""  # JARVIS_PROTHEUS_SENHA (ou TOTVS_PRD_PASS)
    protheus_tenant: str = TENANT_PADRAO  # JARVIS_PROTHEUS_TENANT: empresa,filial
    protheus_rotinas: tuple[str, ...] = ()  # JARVIS_PROTHEUS_ROTINAS: as rotinas liberadas para o fwmodel
    so_o_dono: bool = True  # JARVIS_SO_O_DONO=nao: atende qualquer voz mesmo com a voz do dono cadastrada (/minha-voz)
    limiar_da_voz: float = 0.40  # JARVIS_LIMIAR_DA_VOZ: semelhança mínima (0,2 a 0,8) para uma voz contar como a do dono


def pastas_padrao(home: Path) -> tuple[Path, ...]:
    """Documents, Desktop e Downloads que existirem; senão, a pasta do usuário."""
    existentes = tuple(home / nome for nome in PASTAS_PADRAO if (home / nome).is_dir())
    return existentes or (home,)


def _ler_pastas(bruto: str) -> tuple[Path, ...]:
    pastas: list[Path] = []
    for trecho in bruto.split(";"):
        trecho = trecho.strip().strip('"')
        if not trecho:
            continue
        pasta = Path(os.path.expanduser(trecho))
        if not pasta.is_dir():
            raise ErroConfig(f"JARVIS_PASTAS aponta para uma pasta que não existe: {trecho}")
        pastas.append(pasta)
    if not pastas:
        raise ErroConfig("JARVIS_PASTAS está definida, mas vazia.")
    return tuple(pastas)


def _ler_orcamento(bruto: str, nome: str = "JARVIS_ORCAMENTO_USD") -> float:
    try:
        valor = float(bruto.replace(",", "."))
    except ValueError:
        raise ErroConfig(f"{nome} precisa ser um número, recebi: {bruto}") from None
    if valor <= 0:
        raise ErroConfig(f"{nome} precisa ser maior que zero.")
    return valor


MAX_CIDADE = 80  # letras de JARVIS_CIDADE
MAX_RESPOSTA = 120  # letras de cada resposta ao nome: é dita a cada chamado, e o pedido só vem depois dela


def ler_respostas(bruto: str) -> tuple[str, ...]:
    """JARVIS_RESPOSTAS: 'Sim, mestre?|Às ordens, senhor.' Vazio vale as de fábrica."""
    respostas = tuple(dict.fromkeys(" ".join(r.split()) for r in bruto.split("|") if r.strip()))
    longas = [r for r in respostas if len(r) > MAX_RESPOSTA]
    if longas:
        raise ErroConfig(f"JARVIS_RESPOSTAS: cada resposta pode ter até {MAX_RESPOSTA} letras ({longas[0][:30]}… é comprida demais).")
    return respostas


def carregar(env: Mapping[str, str] | None = None, home: Path | None = None) -> Config:
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    if not env.get("ANTHROPIC_API_KEY", "").strip():
        raise ErroConfig(
            "ANTHROPIC_API_KEY não encontrada. Copie .env.example para .env "
            "e cole a sua chave da Anthropic."
        )

    bruto_pastas = env.get("JARVIS_PASTAS", "").strip()
    pastas = _ler_pastas(bruto_pastas) if bruto_pastas else pastas_padrao(home)
    # Caminhos reais (sem atalhos simbólicos), para a guarda comparar com segurança.
    pastas = tuple(Path(os.path.realpath(p)) for p in pastas)

    bruto_orcamento = env.get("JARVIS_ORCAMENTO_USD", "").strip()

    bruto_atalho = env.get("JARVIS_ATALHO", "").strip()
    try:
        ler_combinacao(bruto_atalho)  # só confere; quem registra o atalho é a conversa
    except ErroAtalho as erro:
        raise ErroConfig(str(erro)) from None

    bruto_projetos = env.get("JARVIS_PROJETOS", "").strip()
    try:
        ler_projetos(bruto_projetos)  # só confere; quem usa o cadastro é a conversa
    except ErroProjeto as erro:
        raise ErroConfig(str(erro)) from None
    bruto_trabalho = env.get("JARVIS_ORCAMENTO_TRABALHO_USD", "").strip()

    bruto_palavra = env.get("JARVIS_PALAVRA", "").strip()
    if bruto_palavra.lower() in _DESLIGADO:
        palavras: tuple[str, ...] = ()
    else:
        try:
            palavras = ler_palavras(bruto_palavra)
        except ValueError as erro:
            raise ErroConfig(str(erro)) from None

    try:
        bom_dia = ler_janela(env.get("JARVIS_BOM_DIA", ""))
    except ValueError as erro:
        raise ErroConfig(str(erro)) from None
    bruto_missao = env.get("JARVIS_ORCAMENTO_MISSAO_USD", "").strip()
    try:
        protheus_url = conferir_endereco(env.get("JARVIS_PROTHEUS_URL", ""))
        protheus_rotinas = ler_rotinas(env.get("JARVIS_PROTHEUS_ROTINAS", ""))
    except ValueError as erro:
        raise ErroConfig(str(erro)) from None
    protheus_tenant = env.get("JARVIS_PROTHEUS_TENANT", "").strip() or TENANT_PADRAO

    bruto_elevenlabs_limite = env.get("JARVIS_ELEVENLABS_LIMITE", "").strip()
    elevenlabs_limite = 9000
    if bruto_elevenlabs_limite:
        try:
            elevenlabs_limite = int(bruto_elevenlabs_limite.replace(".", "").replace(" ", ""))
        except ValueError:
            raise ErroConfig(f"JARVIS_ELEVENLABS_LIMITE precisa ser um número de letras, recebi: {bruto_elevenlabs_limite}") from None
        if elevenlabs_limite < 0:
            raise ErroConfig("JARVIS_ELEVENLABS_LIMITE não pode ser negativo.")
    from jarvis import inventario

    inv_modo = env.get("JARVIS_INV_MODO", "").strip().lower() or "desligado"
    if inv_modo not in inventario.MODOS:
        raise ErroConfig(f"JARVIS_INV_MODO precisa ser {', '.join(inventario.MODOS)}, recebi: {inv_modo}")
    try:
        inv_hora = env.get("JARVIS_INV_HORA", "").strip() or "17:00"
        inventario.ler_hora(inv_hora)
        inv_dias = inventario.ler_dias(env.get("JARVIS_INV_DIAS", ""))
    except ValueError as erro:
        raise ErroConfig(str(erro)) from None
    bruto_inv_max = env.get("JARVIS_INV_MAX_ITENS", "").strip()
    inv_max_itens = inventario.MAX_ITENS
    if bruto_inv_max:
        if not bruto_inv_max.isdigit() or not 1 <= int(bruto_inv_max) <= inventario.MAX_ITENS_DURO:
            raise ErroConfig(f"JARVIS_INV_MAX_ITENS precisa ser um número de 1 a {inventario.MAX_ITENS_DURO}, recebi: {bruto_inv_max}")
        inv_max_itens = int(bruto_inv_max)
    if inv_modo != "desligado" and not (env.get("JARVIS_FLUXO_URL", "").strip() and env.get("JARVIS_FLUXO_USER", "").strip()):
        raise ErroConfig("JARVIS_INV_MODO está ligado mas falta JARVIS_FLUXO_URL e JARVIS_FLUXO_USER no .env.")
    from jarvis import webapp

    try:
        protheus_webapp = webapp.conferir_endereco(env.get("JARVIS_PROTHEUS_WEBAPP", ""))
    except ValueError as erro:
        raise ErroConfig(str(erro)) from None
    elevenlabs_modelo = env.get("JARVIS_ELEVENLABS_MODELO", "").strip().lower() or "flash"
    if elevenlabs_modelo not in ("flash", "multilingual", "turbo"):
        raise ErroConfig(f"JARVIS_ELEVENLABS_MODELO precisa ser flash, multilingual ou turbo, recebi: {elevenlabs_modelo}")
    bruto_limiar = env.get("JARVIS_LIMIAR_DA_VOZ", "").strip()
    limiar_da_voz = 0.40
    if bruto_limiar:
        try:
            limiar_da_voz = float(bruto_limiar.replace(",", "."))
        except ValueError:
            raise ErroConfig(f"JARVIS_LIMIAR_DA_VOZ precisa ser um número entre 0,2 e 0,8, recebi: {bruto_limiar}") from None
        if not 0.2 <= limiar_da_voz <= 0.8:
            raise ErroConfig(f"JARVIS_LIMIAR_DA_VOZ precisa ficar entre 0,2 e 0,8, recebi: {bruto_limiar}")
    cidade = " ".join(env.get("JARVIS_CIDADE", "").split())
    if len(cidade) > MAX_CIDADE:
        raise ErroConfig(f"JARVIS_CIDADE pode ter até {MAX_CIDADE} letras.")

    return Config(
        modelo=env.get("JARVIS_MODELO", "").strip() or MODELO_PADRAO,
        usuario=env.get("JARVIS_USUARIO", "").strip() or None,
        pastas=pastas,
        orcamento_usd=_ler_orcamento(bruto_orcamento) if bruto_orcamento else None,
        voz=env.get("JARVIS_VOZ", "").strip() or VOZ_PADRAO,
        whisper=env.get("JARVIS_WHISPER", "").strip() or WHISPER_PADRAO,
        programas=env.get("JARVIS_PROGRAMAS", "").strip(),
        confirmar_acoes=env.get("JARVIS_CONFIRMAR", "").strip().lower() in ("sempre", "sim", "s", "1", "true"),
        ver_paginas=env.get("JARVIS_TELA", "").strip().lower() not in _DESLIGADO,
        devolver_foco=env.get("JARVIS_FOCO", "").strip().lower() != "navegador",
        atalho=bruto_atalho,
        painel=env.get("JARVIS_PAINEL", "").strip().lower() not in _DESLIGADO,
        programas_na_tela=env.get("JARVIS_PROGRAMAS_TELA", "").strip(),
        projetos=bruto_projetos,
        orcamento_trabalho_usd=_ler_orcamento(bruto_trabalho, "JARVIS_ORCAMENTO_TRABALHO_USD") if bruto_trabalho else 2.0,
        modelo_trabalho=env.get("JARVIS_MODELO_TRABALHO", "").strip(),
        comandos=env.get("JARVIS_COMANDOS", "").strip(),
        terminal=env.get("JARVIS_TERMINAL", "").strip().lower() not in _DESLIGADO,
        palavras=palavras,
        whisper_da_ativacao=env.get("JARVIS_WHISPER_ATIVACAO", "").strip(),
        ouvir_bloqueado=env.get("JARVIS_OUVIR_BLOQUEADO", "").strip().lower() in ("sim", "s", "1", "true", "sempre"),
        respostas=ler_respostas(env.get("JARVIS_RESPOSTAS", "")),
        cidade=cidade,
        bom_dia=bom_dia,
        orcamento_missao_usd=_ler_orcamento(bruto_missao, "JARVIS_ORCAMENTO_MISSAO_USD") if bruto_missao else 1.0,
        elevenlabs_chave=env.get("JARVIS_ELEVENLABS_KEY", "").strip(),
        elevenlabs_voz=env.get("JARVIS_ELEVENLABS_VOZ", "").strip(),
        elevenlabs_modelo=elevenlabs_modelo,
        elevenlabs_limite=elevenlabs_limite,
        protheus_url=protheus_url,
        # TOTVS_PRD_* valem como alternativa: é o nome que o usuário já usa no terminal.
        protheus_usuario=(env.get("JARVIS_PROTHEUS_USER", "") or env.get("TOTVS_PRD_USER", "")).strip(),
        protheus_senha=env.get("JARVIS_PROTHEUS_SENHA", "") or env.get("TOTVS_PRD_PASS", ""),
        protheus_tenant=protheus_tenant,
        protheus_rotinas=protheus_rotinas,
        turno_limpo=env.get("JARVIS_TURNO_LIMPO", "").strip().lower() not in _DESLIGADO,
        inv_modo=inv_modo,
        inv_hora=inv_hora,
        inv_dias=inv_dias,
        inv_max_itens=inv_max_itens,
        fluxo_url=env.get("JARVIS_FLUXO_URL", "").strip().rstrip("/"),
        fluxo_usuario=env.get("JARVIS_FLUXO_USER", "").strip(),
        fluxo_senha=env.get("JARVIS_FLUXO_PASS", ""),
        protheus_webapp=protheus_webapp,
        so_o_dono=env.get("JARVIS_SO_O_DONO", "").strip().lower() not in _DESLIGADO,
        limiar_da_voz=limiar_da_voz,
    )


# --- conferir o .env -----------------------------------------------------------------------

ARQUIVO_ENV = RAIZ_PROJETO / ".env"
# Todo ajuste que o Jarvis entende. Linha ligada no .env que não esteja aqui não faz nada —
# e era isso que acontecia calado: nome trocado, ajuste ignorado, nenhum aviso.
CONHECIDOS = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "JARVIS_ATALHO", "JARVIS_BOM_DIA", "JARVIS_CIDADE", "JARVIS_COMANDOS", "JARVIS_CONFIRMAR",
    "JARVIS_ELEVENLABS_KEY", "JARVIS_ELEVENLABS_LIMITE", "JARVIS_ELEVENLABS_MODELO", "JARVIS_ELEVENLABS_VOZ",
    "JARVIS_FLUXO_PASS", "JARVIS_FLUXO_URL", "JARVIS_FLUXO_USER", "JARVIS_FOCO",
    "JARVIS_INV_DIAS", "JARVIS_INV_HORA", "JARVIS_INV_MAX_ITENS", "JARVIS_INV_MODO", "JARVIS_LIMIAR_DA_VOZ", "JARVIS_MODELO", "JARVIS_MODELO_TRABALHO",
    "JARVIS_ORCAMENTO_MISSAO_USD", "JARVIS_ORCAMENTO_TRABALHO_USD", "JARVIS_ORCAMENTO_USD",
    "JARVIS_OUVIR_BLOQUEADO", "JARVIS_PAINEL", "JARVIS_PALAVRA", "JARVIS_PASTAS", "JARVIS_PROGRAMAS",
    "JARVIS_PROGRAMAS_TELA", "JARVIS_PROJETOS", "JARVIS_PROTHEUS_ROTINAS", "JARVIS_PROTHEUS_SENHA",
    "JARVIS_PROTHEUS_TENANT", "JARVIS_PROTHEUS_URL", "JARVIS_PROTHEUS_USER", "JARVIS_PROTHEUS_WEBAPP",
    "JARVIS_RESPOSTAS",
    "JARVIS_SO_O_DONO", "JARVIS_TELA", "JARVIS_TERMINAL", "JARVIS_TURNO_LIMPO", "JARVIS_USUARIO",
    "JARVIS_VOZ", "JARVIS_WHISPER", "JARVIS_WHISPER_ATIVACAO",
    "TOTVS_PRD_PASS", "TOTVS_PRD_USER",
)  # fmt: skip
# Nome que não é do Jarvis mas aparece em .env por costume: não vale avisar sobre eles.
DE_FORA = ("PATH", "PYTHONPATH", "PYTHONUTF8", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "GITHUB_TOKEN")


def ler_linhas_do_env(arquivo: Path = ARQUIVO_ENV) -> list[tuple[int, str]]:
    """Os ajustes ligados do .env: (número da linha, nome). Nenhum valor sai daqui."""
    try:
        bruto = arquivo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    achados = []
    for numero, linha in enumerate(bruto.splitlines(), 1):
        limpa = linha.strip().removeprefix("export ").strip()
        if not limpa or limpa.startswith("#") or "=" not in limpa:
            continue
        nome = limpa.split("=", 1)[0].strip()
        if nome:
            achados.append((numero, nome))
    return achados


def conferir_env(arquivo: Path = ARQUIVO_ENV) -> list[str]:
    """Avisos sobre linhas do .env que o Jarvis não entende, com o palpite do nome certo.

    Existe por um caso real: a chave da ElevenLabs posta com outro nome ficou sem efeito e o
    Jarvis seguiu na voz local sem dizer nada. Ajuste que não é reconhecido tem de aparecer.
    """
    import difflib

    avisos, vistos = [], {}
    for numero, nome in ler_linhas_do_env(arquivo):
        if nome in vistos:
            avisos.append(f"{nome} aparece duas vezes no .env (linhas {vistos[nome]} e {numero}); vale a última.")
        vistos[nome] = numero
        if nome in CONHECIDOS or nome.upper() in DE_FORA:
            continue
        parecidos = difflib.get_close_matches(nome.upper(), CONHECIDOS, n=1, cutoff=0.7)
        if not parecidos:  # tenta de novo ignorando separadores: ELEVEN_LABS_KEY ~ ELEVENLABS_KEY
            achatado = nome.upper().replace("_", "")
            parecidos = [c for c in CONHECIDOS if c.replace("_", "") == achatado]
        palpite = f" Você quis dizer {parecidos[0]}?" if parecidos else ""
        avisos.append(f"linha {numero}: {nome} não é um ajuste que eu conheça, então não faz nada.{palpite}")
    return avisos
