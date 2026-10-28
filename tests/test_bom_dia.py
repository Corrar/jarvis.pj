"""A previsão do tempo (clima.py) e o cumprimento do dia (bom_dia.py), e os dois na conversa."""

import asyncio
import json
from datetime import datetime

import pytest

from jarvis import bom_dia, clima, config, ferramentas
from jarvis.bom_dia import Saudacao, ler_janela, vigiar_desbloqueio
from jarvis.clima import Clima, ErroClima
from jarvis.memoria import Memoria
from jarvis.painel import limpar_paineis
from jarvis.registro import Registro

# --- o tempo ----------------------------------------------------------------------------------------

LUGAR = {"results": [{"name": "Curitiba", "latitude": -25.42778, "longitude": -49.27306, "country": "Brasil", "admin1": "Paraná"}]}
PREVISAO = {
    "timezone": "America/Sao_Paulo",
    "current": {
        "time": "2026-10-09T09:15", "temperature_2m": 18.4, "relative_humidity_2m": 72, "apparent_temperature": 17.9,
        "weather_code": 3, "wind_speed_10m": 12.3, "precipitation": 0.0,
    },  # fmt: skip
    "hourly": {
        "time": [f"2026-10-09T{h:02d}:00" for h in range(24)],
        "temperature_2m": [14 + h * 0.5 for h in range(24)],
        "precipitation_probability": [0] * 12 + [40] * 12,
        "weather_code": [3] * 24,
    },
    "daily": {
        "time": ["2026-10-09", "2026-10-10", "2026-10-11"],
        "weather_code": [3, 61, 0],
        "temperature_2m_max": [24.1, 19.0, 26.5],
        "temperature_2m_min": [13.2, 12.0, 11.0],
        "precipitation_probability_max": [40, 90, 5],
    },
}


class Rede:
    """O Open-Meteo falso: responde às duas consultas e conta quantas vezes foi chamado."""

    def __init__(self, lugar=LUGAR, previsao=PREVISAO, erro=None):
        self.lugar, self.previsao, self.erro, self.pedidos = lugar, previsao, erro, []

    def __call__(self, url):
        self.pedidos.append(url)
        if self.erro is not None:
            raise self.erro
        if url.startswith(clima.GEOCODIFICACAO):
            return {"results": []} if "Xyzzy" in url else self.lugar
        assert url.startswith(clima.PREVISAO)
        return self.previsao


def test_previsao_traduz_os_dados_e_guarda_por_meia_hora():
    rede, agora = Rede(), [1000.0]
    tempo = Clima(rede, lambda: agora[0])
    previsao = tempo.previsao("curitiba")
    assert previsao["cidade"] == "Curitiba" and previsao["regiao"] == "Paraná, Brasil" and previsao["condicao"] == "nublado" and previsao["icone"] == "nublado"
    assert previsao["temperatura"] == 18.4 and previsao["umidade"] == 72 and previsao["quando"] == "2026-10-09T09:15"
    assert [h["hora"] for h in previsao["por_hora"]] == [f"{h:02d}h" for h in range(9, 21)]  # da hora de agora, doze horas
    assert previsao["por_hora"][0]["temperatura"] == 18.5 and previsao["por_hora"][3]["chuva"] == 40
    assert [d["dia"] for d in previsao["dias"]] == ["hoje", "sáb", "dom"] and previsao["dias"][1]["condicao"] == "chuva fraca"
    assert "name=curitiba" in rede.pedidos[0] and "latitude=-25.4278" in rede.pedidos[1] and "timezone=auto" in rede.pedidos[1]
    assert tempo.previsao("Curitiba") is previsao and len(rede.pedidos) == 2  # guardada; a cidade não é consultada de novo
    agora[0] += clima.VALIDADE + 1
    assert tempo.previsao("curitiba") is not previsao and len(rede.pedidos) == 3  # venceu: só a previsão é refeita


def test_texto_e_cartao():
    previsao = Clima(Rede()).previsao("Curitiba")
    texto = clima.texto(previsao)
    assert texto.startswith("Curitiba (Paraná, Brasil), às 09:15:\nAgora: 18°, nublado; sensação 18°, umidade 72%, vento 12 km/h.")
    assert "Hoje: mínima 13°, máxima 24°, nublado, chance de chuva 40%." in texto
    assert "Próximas horas: 09h 18°; 10h 19°; 11h 20°; 12h 20° (40% chuva)" in texto
    assert "sáb 10/10: 12° a 19°, chuva fraca; dom 11/10: 11° a 26°, céu limpo" in texto and "Fonte: Open-Meteo" in texto
    cartao = clima.cartao(previsao)
    assert cartao["titulo"] == "Curitiba" and cartao["destaque"] == "18°" and cartao["sub"] == "Nublado" and cartao["icone"] == "nublado"
    assert cartao["itens"] == [
        {"r": "Mínima", "v": "13°"}, {"r": "Máxima", "v": "24°"}, {"r": "Umidade", "v": "72%"}, {"r": "Vento", "v": "12 km/h"}, {"r": "Chuva", "v": "40%"},
    ]  # fmt: skip
    assert len(cartao["grafico"]) == 12 and cartao["graficoRotulos"][0] == "09h"
    assert cartao["previsao"][1] == {"dia": "sáb", "icone": "chuva", "max": "19°", "min": "12°"}
    assert limpar_paineis([cartao])[0]["titulo"] == "Curitiba"  # passa inteiro pela limpeza do painel


def test_erros_viram_mensagens():
    with pytest.raises(ErroClima, match="Diga a cidade"):
        Clima(Rede()).previsao("  ")
    with pytest.raises(ErroClima, match="Não achei a cidade 'Xyzzy'"):
        Clima(Rede(lugar={"results": []})).previsao("Xyzzy")
    with pytest.raises(ErroClima, match="Não consegui consultar a cidade .*OSError: rede fora"):
        Clima(Rede(erro=OSError("rede fora"))).previsao("Curitiba")
    with pytest.raises(ErroClima, match="sem coordenadas"):
        Clima(Rede(lugar={"results": [{"name": "X"}]})).previsao("X")
    with pytest.raises(ErroClima, match="formato que não entendi"):
        Clima(Rede(previsao={"current": "nada", "daily": 5})).previsao("Curitiba")
    tempo = Clima(Rede(previsao={}))  # resposta vazia: sem números, mas sem quebrar
    previsao = tempo.previsao("Curitiba")
    assert previsao["temperatura"] is None and previsao["dias"] == [] and "Agora: –, tempo indefinido" in clima.texto(previsao)
    assert clima.cartao(previsao)["itens"] == [] and clima.cartao(previsao)["destaque"] == "–"


def test_descrever_codigos():
    assert clima.descrever(0) == ("céu limpo", "sol") and clima.descrever(95) == ("trovoadas", "tempestade")
    assert clima.descrever("61") == ("chuva fraca", "chuva") and clima.descrever(None) == ("tempo indefinido", "padrao")
    assert clima.descrever("x") == ("tempo indefinido", "padrao")


def test_ferramenta_clima(tmp_path):
    tempo = Clima(Rede())
    com_cidade = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), clima=tempo, cidade="Curitiba")}
    sem_cidade = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), clima=tempo)}
    sem_clima = {f.name for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"))}
    assert "clima" in com_cidade and "clima" in sem_cidade and "clima" not in sem_clima
    assert "vale a do usuário: Curitiba" in com_cidade["clima"].description and "JARVIS_CIDADE" in sem_cidade["clima"].description
    assert "clima" in ferramentas.CONTAMINAM  # vem de fora: depois dele, agir só na fala seguinte

    async def cena():
        saida = await com_cidade["clima"].handler({})
        assert not saida.get("is_error") and saida["content"][0]["text"].startswith("Curitiba (Paraná, Brasil)")
        saida = await sem_cidade["clima"].handler({})
        assert saida["is_error"] and "JARVIS_CIDADE" in saida["content"][0]["text"]
        saida = await sem_cidade["clima"].handler({"cidade": "Curitiba, PR"})
        assert not saida.get("is_error")
        saida = await com_cidade["clima"].handler({"cidade": "Xyzzy"})
        assert saida["is_error"] and "Não achei" in saida["content"][0]["text"]

    asyncio.run(cena())


def test_ferramenta_clima_com_falha_inesperada(tmp_path, caplog):
    class Quebrado:
        def previsao(self, cidade):
            raise RuntimeError("bug")

    por_nome = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), clima=Quebrado(), cidade="X")}
    saida = asyncio.run(por_nome["clima"].handler({}))
    assert saida["is_error"] and "RuntimeError" in saida["content"][0]["text"] and "Falha ao consultar o clima" in caplog.text


# --- a configuração -----------------------------------------------------------------------------------


def test_ler_janela():
    assert ler_janela("") == (5, 14) and ler_janela("sim") == (5, 14)
    assert ler_janela("nao") is None and ler_janela("Não") is None
    assert ler_janela("sempre") == (0, 24) and ler_janela("6-12") == (6, 12) and ler_janela("7h-13h") == (7, 13)
    for ruim in ("12-6", "x", "5-25", "-1-5", "5"):
        with pytest.raises(ValueError, match="JARVIS_BOM_DIA"):
            ler_janela(ruim)


def test_config_cidade_e_bom_dia(tmp_path):
    base = {"ANTHROPIC_API_KEY": "k", "JARVIS_PASTAS": str(tmp_path)}
    cfg = config.carregar(base, tmp_path)
    assert cfg.cidade == "" and cfg.bom_dia == (5, 14)
    cfg = config.carregar({**base, "JARVIS_CIDADE": "  Curitiba,  PR ", "JARVIS_BOM_DIA": "nao"}, tmp_path)
    assert cfg.cidade == "Curitiba, PR" and cfg.bom_dia is None
    with pytest.raises(config.ErroConfig, match="JARVIS_BOM_DIA"):
        config.carregar({**base, "JARVIS_BOM_DIA": "tarde"}, tmp_path)
    with pytest.raises(config.ErroConfig, match="JARVIS_CIDADE"):
        config.carregar({**base, "JARVIS_CIDADE": "x" * 81}, tmp_path)


# --- a saudação -------------------------------------------------------------------------------------


class Relogio:
    def __init__(self, agora):
        self.agora = agora

    def __call__(self):
        return self.agora


def test_devida_uma_vez_por_dia_dentro_da_janela(tmp_path):
    relogio = Relogio(datetime(2026, 10, 9, 8, 0))
    saudacao = Saudacao(tmp_path / "bom-dia.json", (5, 14), relogio)
    assert saudacao.ligada and saudacao.devida()
    saudacao.marcar()
    assert not saudacao.devida() and json.loads((tmp_path / "bom-dia.json").read_text()) == {"ultimo": "2026-10-09"}
    assert not Saudacao(tmp_path / "bom-dia.json", (5, 14), relogio).devida()  # a data sobrevive a um reinício
    relogio.agora = datetime(2026, 10, 10, 4, 59)
    assert not saudacao.devida()  # dia seguinte, mas antes da janela
    relogio.agora = datetime(2026, 10, 10, 5, 0)
    assert saudacao.devida()
    relogio.agora = datetime(2026, 10, 10, 14, 0)
    assert not saudacao.devida()
    assert not Saudacao(tmp_path / "x.json", None, relogio).devida() and not Saudacao(tmp_path / "x.json", None, relogio).ligada
    assert Saudacao(tmp_path / "x.json", (0, 24), Relogio(datetime(2026, 10, 10, 23, 30))).devida()


def test_arquivo_estragado_nao_impede(tmp_path, caplog):
    (tmp_path / "bom-dia.json").write_text("{", encoding="utf-8")
    saudacao = Saudacao(tmp_path / "bom-dia.json", (5, 14), Relogio(datetime(2026, 10, 9, 8, 0)))
    assert saudacao.devida() and "Não consegui ler" in caplog.text
    (tmp_path / "bom-dia.json").write_text("[1]", encoding="utf-8")
    assert Saudacao(tmp_path / "bom-dia.json", (5, 14), Relogio(datetime(2026, 10, 9, 8, 0))).devida()


def test_marcar_sem_poder_gravar(tmp_path, caplog):
    saudacao = Saudacao(tmp_path / "pasta" / "bom-dia.json", (5, 14), Relogio(datetime(2026, 10, 9, 8, 0)))
    (tmp_path / "pasta").write_text("não é uma pasta")
    saudacao.marcar()
    assert not saudacao.devida() and "Não consegui gravar" in caplog.text


def test_pedido_tem_o_roteiro(tmp_path):
    from jarvis.rotinas import Agenda

    agora = datetime(2026, 10, 9, 8, 30)
    agenda = Agenda(tmp_path / "r.json", relogio=lambda: agora)
    agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    agenda.criar("amanhã", "lembrete", em="2026-10-10T08:00")

    class Trabalho:
        def __init__(self, fim):
            self.fim = fim

    class Oficina:
        def listar(self, n):
            return [Trabalho("2026-10-09T07:00:00"), Trabalho("2026-10-07T07:00:00"), Trabalho("")]

        def linha(self, t):
            return f"abc · Royale · concluído ({t.fim[:10]})"

    pedido = Saudacao(tmp_path / "b.json", (5, 14), lambda: agora).pedido("Bruno", "Curitiba, PR", agenda, Oficina())
    assert pedido.startswith("[bom dia] É sexta-feira, 09/10/2026, 08:30")
    assert '1. Diga "Bom dia" ao usuário, Bruno, em uma frase.' in pedido
    assert "2. O tempo de hoje em Curitiba, PR: use a ferramenta clima" in pedido
    assert "3. As três notícias mais importantes de tecnologia" in pedido and "mercado financeiro" in pedido
    assert '"notícias tecnologia 09/10/2026"' in pedido and '"tecnologia hoje 9 de outubro"' in pedido  # a busca leva a data, sem depender do idioma do sistema
    assert "diga as mais recentes que achou e de que dia são" in pedido
    assert "4. Os compromissos de hoje, que estão marcados com você: 15:30 lembrete: ligar para o dentista." in pedido and "amanhã" not in pedido
    assert "5. Os trabalhos de programação que terminaram desde ontem: abc · Royale · concluído (2026-10-09)." in pedido and "2026-10-07" not in pedido
    assert "6. Termine com uma frase motivadora" in pedido and "painel_mostrar" in pedido

    tarde = datetime(2026, 10, 9, 13, 0)
    pedido = Saudacao(tmp_path / "b.json", (5, 14), lambda: tarde).pedido(None, "", Agenda(tmp_path / "v.json", relogio=lambda: tarde), None)
    assert '1. Diga "Boa tarde" ao usuário em uma frase.' in pedido
    assert "2. Pule o tempo (a cidade não está configurada)" in pedido and "JARVIS_CIDADE" in pedido
    assert "4. Não há lembretes nem tarefas marcados para hoje" in pedido and "5. Termine com uma frase motivadora" in pedido
    assert "trabalhos de programação" not in pedido
    assert bom_dia.cumprimento(datetime(2026, 1, 1, 19, 0)) == "Boa noite"


def test_pedido_aguenta_oficina_quebrada(tmp_path, caplog):
    class Oficina:
        def listar(self, n):
            raise RuntimeError("disco")

    pedido = Saudacao(tmp_path / "b.json", (5, 14), lambda: datetime(2026, 10, 9, 8, 30)).pedido(None, "X", None, Oficina())
    assert "5. Termine" in pedido and "Não consegui listar os trabalhos" in caplog.text


def test_vigiar_desbloqueio_avisa_so_na_volta():
    estados = iter([False, True, True, False, False, True, RuntimeError("api"), False])
    voltas = []

    def bloqueado():
        estado = next(estados)
        if isinstance(estado, Exception):
            raise estado
        return estado

    async def cena():
        tarefa = asyncio.ensure_future(vigiar_desbloqueio(bloqueado, lambda: voltas.append(1), 0.001))
        for _ in range(200):
            if len(voltas) == 2:
                break
            await asyncio.sleep(0.002)
        tarefa.cancel()

    asyncio.run(cena())
    assert voltas == [1, 1]


def test_vigiar_desbloqueio_aguenta_falha_de_quem_reage(caplog):
    estados = iter([True, False, True, False])
    voltas = []

    def reagir():
        voltas.append(1)
        raise RuntimeError("x")

    async def cena():
        tarefa = asyncio.ensure_future(vigiar_desbloqueio(lambda: next(estados), reagir, 0.001))
        for _ in range(200):
            if len(voltas) == 2:
                break
            await asyncio.sleep(0.002)
        tarefa.cancel()

    asyncio.run(cena())
    assert voltas == [1, 1] and "Falha ao reagir" in caplog.text

