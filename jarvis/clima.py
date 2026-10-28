"""Previsão do tempo, do Open-Meteo (serviço aberto, sem chave e sem cadastro).

Duas consultas: a cidade vira coordenadas (geocodificação) e as coordenadas viram a previsão.
Tudo com tempo de espera curto e guardado por meia hora: o bom dia e um "como está o tempo?"
não podem ficar presos nem repetir a consulta a cada frase.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

GEOCODIFICACAO = "https://geocoding-api.open-meteo.com/v1/search"
PREVISAO = "https://api.open-meteo.com/v1/forecast"
ESPERA = 8.0  # segundos por consulta
VALIDADE = 30 * 60.0  # segundos que uma previsão vale
MAX_CIDADE = 80
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

# Códigos do tempo (WMO) usados pelo Open-Meteo: a descrição e o ícone do painel.
_CODIGOS: dict[int, tuple[str, str]] = {
    0: ("céu limpo", "sol"),
    1: ("predomínio de sol", "sol"),
    2: ("parcialmente nublado", "nublado"),
    3: ("nublado", "nublado"),
    45: ("nevoeiro", "nublado"),
    48: ("nevoeiro com geada", "nublado"),
    51: ("garoa fraca", "chuva"),
    53: ("garoa", "chuva"),
    55: ("garoa forte", "chuva"),
    56: ("garoa congelante", "chuva"),
    57: ("garoa congelante forte", "chuva"),
    61: ("chuva fraca", "chuva"),
    63: ("chuva", "chuva"),
    65: ("chuva forte", "chuva"),
    66: ("chuva congelante", "chuva"),
    67: ("chuva congelante forte", "chuva"),
    71: ("neve fraca", "neve"),
    73: ("neve", "neve"),
    75: ("neve forte", "neve"),
    77: ("grãos de neve", "neve"),
    80: ("pancadas de chuva fracas", "chuva"),
    81: ("pancadas de chuva", "chuva"),
    82: ("pancadas de chuva fortes", "tempestade"),
    85: ("pancadas de neve", "neve"),
    86: ("pancadas de neve fortes", "neve"),
    95: ("trovoadas", "tempestade"),
    96: ("trovoadas com granizo", "tempestade"),
    99: ("trovoadas com granizo forte", "tempestade"),
}


class ErroClima(Exception):
    """A previsão não veio; a mensagem diz por quê, para o modelo contar ao usuário."""


def descrever(codigo: Any) -> tuple[str, str]:
    try:
        return _CODIGOS.get(int(codigo), ("tempo indefinido", "padrao"))
    except (TypeError, ValueError):
        return ("tempo indefinido", "padrao")


def buscar_json(url: str, espera: float = ESPERA) -> Any:
    """Uma consulta GET que devolve JSON. Só para os endereços do Open-Meteo."""
    pedido = urllib.request.Request(url, headers={"User-Agent": "jarvis-desktop", "Accept": "application/json"})
    with urllib.request.urlopen(pedido, timeout=espera) as resposta:  # noqa: S310 - endereços fixos, https
        return json.loads(resposta.read(1 << 20).decode("utf-8"))


def _numero(valor: Any) -> float | None:
    try:
        return None if valor is None else float(valor)
    except (TypeError, ValueError):
        return None


def _grau(valor: Any) -> str:
    numero = _numero(valor)
    return "–" if numero is None else f"{numero:.0f}°"


class Clima:
    """Consulta e guarda a previsão de uma cidade."""

    def __init__(self, buscar: Callable[[str], Any] | None = None, relogio: Callable[[], float] | None = None) -> None:
        self._buscar = buscar or buscar_json
        self._relogio = relogio or time.monotonic
        self._trava = threading.Lock()
        self._lugares: dict[str, dict[str, Any]] = {}  # cidade pedida -> lugar achado
        self._previsoes: dict[str, tuple[float, dict[str, Any]]] = {}  # chave do lugar -> (quando, dados)

    def lugar(self, cidade: str) -> dict[str, Any]:
        cidade = " ".join(str(cidade or "").split())[:MAX_CIDADE]
        if not cidade:
            raise ErroClima("Diga a cidade.")
        chave = cidade.casefold()
        with self._trava:
            if chave in self._lugares:
                return self._lugares[chave]
        consulta = urllib.parse.urlencode({"name": cidade, "count": 1, "language": "pt", "format": "json"})
        try:
            dados = self._buscar(f"{GEOCODIFICACAO}?{consulta}")
        except Exception as erro:  # noqa: BLE001 - rede fora, serviço fora: a mensagem vai para o modelo
            raise ErroClima(f"Não consegui consultar a cidade ({type(erro).__name__}: {erro}).") from None
        achados = dados.get("results") if isinstance(dados, dict) else None
        if not achados:
            raise ErroClima(f"Não achei a cidade '{cidade}'. Confira o nome (pode acrescentar o estado ou o país).")
        achado = achados[0]
        lugar = {
            "nome": str(achado.get("name") or cidade),
            "regiao": ", ".join(str(achado[c]) for c in ("admin1", "country") if achado.get(c)),
            "latitude": _numero(achado.get("latitude")),
            "longitude": _numero(achado.get("longitude")),
        }
        if lugar["latitude"] is None or lugar["longitude"] is None:
            raise ErroClima(f"A cidade '{cidade}' veio sem coordenadas.")
        with self._trava:
            self._lugares[chave] = lugar
        return lugar

    def previsao(self, cidade: str) -> dict[str, Any]:
        """A previsão da cidade, em um dicionário pronto para texto e para o cartão do painel."""
        lugar = self.lugar(cidade)
        chave = f"{lugar['latitude']:.3f},{lugar['longitude']:.3f}"
        agora = self._relogio()
        with self._trava:
            guardada = self._previsoes.get(chave)
            if guardada is not None and agora - guardada[0] < VALIDADE:
                return guardada[1]
        consulta = urllib.parse.urlencode(
            {
                "latitude": f"{lugar['latitude']:.4f}",
                "longitude": f"{lugar['longitude']:.4f}",
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            }
        )
        try:
            dados = self._buscar(f"{PREVISAO}?{consulta}")
        except Exception as erro:  # noqa: BLE001
            raise ErroClima(f"Não consegui consultar a previsão ({type(erro).__name__}: {erro}).") from None
        try:
            previsao = _montar(lugar, dados)
        except (TypeError, ValueError, KeyError, AttributeError) as erro:
            raise ErroClima(f"A previsão veio em um formato que não entendi ({type(erro).__name__}).") from None
        with self._trava:
            self._previsoes[chave] = (agora, previsao)
        return previsao


def _montar(lugar: dict[str, Any], dados: dict[str, Any]) -> dict[str, Any]:
    atual = dados.get("current") or {}
    condicao, icone = descrever(atual.get("weather_code"))
    horas = dados.get("hourly") or {}
    dias = dados.get("daily") or {}
    momento = str(atual.get("time") or "")
    hora_de_agora = momento[:13]
    por_hora: list[dict[str, Any]] = []
    for i, quando in enumerate(horas.get("time") or []):
        if str(quando)[:13] < hora_de_agora:
            continue
        if len(por_hora) == 12:
            break
        por_hora.append(
            {
                "hora": f"{str(quando)[11:13]}h",
                "temperatura": _numero((horas.get("temperature_2m") or [None] * (i + 1))[i]),
                "chuva": _numero((horas.get("precipitation_probability") or [None] * (i + 1))[i]),
            }
        )
    proximos: list[dict[str, Any]] = []
    for i, dia in enumerate(dias.get("time") or []):
        descricao, icone_do_dia = descrever((dias.get("weather_code") or [None] * (i + 1))[i])
        data = datetime.fromisoformat(str(dia))
        proximos.append(
            {
                "dia": "hoje" if i == 0 else DIAS[data.weekday()],
                "data": f"{data:%d/%m}",
                "condicao": descricao,
                "icone": icone_do_dia,
                "max": _numero((dias.get("temperature_2m_max") or [None] * (i + 1))[i]),
                "min": _numero((dias.get("temperature_2m_min") or [None] * (i + 1))[i]),
                "chuva": _numero((dias.get("precipitation_probability_max") or [None] * (i + 1))[i]),
            }
        )
    return {
        "cidade": lugar["nome"],
        "regiao": lugar["regiao"],
        "quando": momento,
        "temperatura": _numero(atual.get("temperature_2m")),
        "sensacao": _numero(atual.get("apparent_temperature")),
        "umidade": _numero(atual.get("relative_humidity_2m")),
        "vento": _numero(atual.get("wind_speed_10m")),
        "condicao": condicao,
        "icone": icone,
        "por_hora": por_hora,
        "dias": proximos,
        "fonte": "Open-Meteo",
    }


def texto(previsao: dict[str, Any]) -> str:
    """A previsão em linhas, para o modelo falar e montar o cartão."""
    hoje = previsao["dias"][0] if previsao["dias"] else {}
    linhas = [
        f"{previsao['cidade']}" + (f" ({previsao['regiao']})" if previsao["regiao"] else "") + f", às {previsao['quando'][11:16] or '--:--'}:",
        f"Agora: {_grau(previsao['temperatura'])}, {previsao['condicao']}; sensação {_grau(previsao['sensacao'])}, "
        f"umidade {_grau(previsao['umidade']).replace('°', '%')}, vento {_grau(previsao['vento']).replace('°', ' km/h')}.",
    ]
    if hoje:
        chuva = f", chance de chuva {hoje['chuva']:.0f}%" if hoje.get("chuva") is not None else ""
        linhas.append(f"Hoje: mínima {_grau(hoje['min'])}, máxima {_grau(hoje['max'])}, {hoje['condicao']}{chuva}.")
    if previsao["por_hora"]:
        linhas.append("Próximas horas: " + "; ".join(f"{h['hora']} {_grau(h['temperatura'])}" + (f" ({h['chuva']:.0f}% chuva)" if h.get("chuva") else "") for h in previsao["por_hora"]))
    if len(previsao["dias"]) > 1:
        linhas.append("Próximos dias: " + "; ".join(f"{d['dia']} {d['data']}: {_grau(d['min'])} a {_grau(d['max'])}, {d['condicao']}" for d in previsao["dias"][1:]))
    linhas.append(
        f"Ícone do cartão: {previsao['icone']}. Fonte: {previsao['fonte']}. Para o cartão do painel, use estes números: destaque a temperatura de agora, "
        "grafico com as temperaturas por hora e graficoRotulos com as horas, previsao com os dias (dia, icone, max, min)."
    )
    return "\n".join(linhas)


def cartao(previsao: dict[str, Any]) -> dict[str, Any]:
    """O cartão do painel pronto, para quem não quer que o modelo o monte (o bom dia)."""
    hoje = previsao["dias"][0] if previsao["dias"] else {}
    itens = []
    if hoje:
        itens += [{"r": "Mínima", "v": _grau(hoje.get("min"))}, {"r": "Máxima", "v": _grau(hoje.get("max"))}]
    if previsao.get("umidade") is not None:
        itens.append({"r": "Umidade", "v": f"{previsao['umidade']:.0f}%"})
    if previsao.get("vento") is not None:
        itens.append({"r": "Vento", "v": f"{previsao['vento']:.0f} km/h"})
    if hoje.get("chuva") is not None:
        itens.append({"r": "Chuva", "v": f"{hoje['chuva']:.0f}%"})
    return {
        "titulo": previsao["cidade"],
        "categoria": "Previsão · Clima",
        "icone": previsao["icone"],
        "destaque": _grau(previsao.get("temperatura")),
        "sub": previsao["condicao"].capitalize(),
        "itens": itens,
        "grafico": [h["temperatura"] for h in previsao["por_hora"] if h.get("temperatura") is not None],
        "graficoRotulos": [h["hora"] for h in previsao["por_hora"] if h.get("temperatura") is not None],
        "previsao": [{"dia": d["dia"], "icone": d["icone"], "max": _grau(d.get("max")), "min": _grau(d.get("min"))} for d in previsao["dias"][:7]],
        "fonte": previsao["fonte"],
        "acao": "Próximos dias",
    }
