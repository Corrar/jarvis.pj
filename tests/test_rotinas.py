"""Rotinas: a agenda, o despertador, as ferramentas e a entrega na conversa."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from jarvis import cli, ferramentas, rotinas
from jarvis.memoria import Memoria
from jarvis.painel import MUDO
from jarvis.registro import Registro
from jarvis.rotinas import Agenda, Despertador, ErroRotina, proxima_ocorrencia

SEXTA = datetime(2026, 10, 9, 9, 0)  # sexta-feira


class Relogio:
    def __init__(self, agora=SEXTA):
        self.agora = agora

    def __call__(self):
        return self.agora

    def avancar(self, **kw):
        self.agora += timedelta(**kw)


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def bipes(monkeypatch):
    tocados = []
    monkeypatch.setattr(cli, "_bipe", tocados.append)
    return tocados


@pytest.fixture
def agenda(tmp_path, relogio):
    return Agenda(tmp_path / "rotinas.json", relogio=relogio)


# --- próxima ocorrência ----------------------------------------------------------------------------


def test_proxima_ocorrencia_diaria_hoje_se_ainda_nao_passou_senao_amanha():
    assert proxima_ocorrencia("diaria", (10, 0), [], SEXTA) == datetime(2026, 10, 9, 10, 0)
    assert proxima_ocorrencia("diaria", (8, 0), [], SEXTA) == datetime(2026, 10, 10, 8, 0)
    assert proxima_ocorrencia("diaria", (9, 0), [], SEXTA) == datetime(2026, 10, 10, 9, 0)  # na hora exata, é a próxima


def test_proxima_ocorrencia_dias_uteis_pula_o_fim_de_semana():
    assert proxima_ocorrencia("dias_uteis", (8, 0), [], SEXTA) == datetime(2026, 10, 12, 8, 0)  # segunda


def test_proxima_ocorrencia_semanal_e_mensal():
    assert proxima_ocorrencia("semanal", (19, 30), [1, 5], SEXTA) == datetime(2026, 10, 10, 19, 30)  # sábado
    assert proxima_ocorrencia("semanal", (19, 30), [4], SEXTA) == datetime(2026, 10, 9, 19, 30)  # a própria sexta, mais tarde
    assert proxima_ocorrencia("mensal", (9, 0), [], SEXTA, 31) == datetime(2026, 10, 31, 9, 0)
    assert proxima_ocorrencia("mensal", (9, 0), [], datetime(2026, 10, 31, 10, 0), 31) == datetime(2026, 12, 31, 9, 0)  # novembro não tem 31
    assert proxima_ocorrencia("mensal", (9, 0), [], SEXTA, 9) == datetime(2026, 11, 9, 9, 0)  # hoje às 9 já passou


# --- a agenda ----------------------------------------------------------------------------------------


def test_criar_lembrete_de_uma_vez_e_listar(agenda, tmp_path):
    rotina = agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    assert rotina.tipo == "lembrete" and rotina.proxima == "2026-10-09T15:30" and not rotina.repetida
    assert rotina.descricao == "em 09/10/2026 às 15:30"
    assert agenda.listar() == [rotina] and agenda.proxima() == datetime(2026, 10, 9, 15, 30)
    gravado = json.loads((tmp_path / "rotinas.json").read_text(encoding="utf-8"))
    assert gravado["rotinas"][0]["texto"] == "ligar para o dentista"
    assert "hoje 15:30" in agenda.texto() and "ligar para o dentista" in agenda.texto()
    # Outra Agenda no mesmo arquivo vê a mesma rotina.
    assert [r.id for r in Agenda(tmp_path / "rotinas.json").listar()] == [rotina.id]


def test_criar_repetidas(agenda):
    diaria = agenda.criar("abrir o e-mail e me dizer o que chegou", "tarefa", hora="8:00", repeticao="diaria")
    assert diaria.proxima == "2026-10-10T08:00" and diaria.hora == "08:00" and diaria.descricao == "todo dia às 08:00"
    uteis = agenda.criar("conferir o caixa", "lembrete", hora="18h", repeticao="dias_uteis")
    assert uteis.proxima == "2026-10-09T18:00" and uteis.descricao == "nos dias úteis às 18:00"
    semanal = agenda.criar("tirar o lixo", "lembrete", hora="19:30", repeticao="semanal", dias=["terça", 5, 5])
    assert semanal.dias == [1, 5] and semanal.proxima == "2026-10-10T19:30" and semanal.descricao == "terça e sábado às 19:30"
    mensal = agenda.criar("pagar o aluguel", "lembrete", hora="09:00", repeticao="mensal", em="2026-10-05")
    assert mensal.proxima == "2026-11-05T09:00" and mensal.descricao == "todo mês às 09:00 (dia 5)"
    assert [r.id for r in agenda.listar()] == [uteis.id, diaria.id, semanal.id, mensal.id]  # pela próxima vez


@pytest.mark.parametrize(
    ("args", "trecho"),
    [
        (dict(texto="", tipo="lembrete", em="2026-10-09T15:30"), "Faltou o texto"),
        (dict(texto="x" * 301, tipo="lembrete", em="2026-10-09T15:30"), "até 300 letras"),
        (dict(texto="x", tipo="ordem", em="2026-10-09T15:30"), "Tipo inválido"),
        (dict(texto="x", tipo="lembrete"), "informe 'em'"),
        (dict(texto="x", tipo="lembrete", em="amanhã"), "Data inválida"),
        (dict(texto="x", tipo="lembrete", em="2026-10-09T08:00"), "já passou"),
        (dict(texto="x", tipo="lembrete", em="2026-10-09T09:00"), "já passou"),
        (dict(texto="x", tipo="lembrete", em="2028-10-09T09:00"), "Mais de um ano"),
        (dict(texto="x", tipo="lembrete", repeticao="diaria"), "informe 'hora'"),
        (dict(texto="x", tipo="lembrete", repeticao="diaria", hora="25:00"), "Hora inválida"),
        (dict(texto="x", tipo="lembrete", repeticao="diaria", hora="cedo"), "Hora inválida"),
        (dict(texto="x", tipo="lembrete", repeticao="sempre", hora="08:00"), "Repetição inválida"),
        (dict(texto="x", tipo="lembrete", repeticao="semanal", hora="08:00"), "informe 'dias'"),
        (dict(texto="x", tipo="lembrete", repeticao="semanal", hora="08:00", dias=[7]), "Dia da semana inválido"),
        (dict(texto="x", tipo="lembrete", repeticao="semanal", hora="08:00", dias="seg"), "lista de dias"),
        (dict(texto="x", tipo="lembrete", repeticao="mensal", hora="08:00", em="x"), "Data inválida"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_pedidos_invalidos(agenda, args, trecho):
    with pytest.raises(ErroRotina, match=trecho):
        agenda.criar(**args)
    assert agenda.listar() == []


def test_limite_de_rotinas(agenda):
    for i in range(rotinas.MAX_ROTINAS):
        agenda.criar(f"r{i}", "lembrete", em="2026-10-09T15:30")
    with pytest.raises(ErroRotina, match="apague alguma"):
        agenda.criar("mais uma", "lembrete", em="2026-10-09T15:30")


def test_achar_e_apagar(agenda):
    a = agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    b = agenda.criar("ligar para a mãe", "lembrete", em="2026-10-09T16:30")
    assert agenda.achar(a.id) is a and agenda.achar("DENTISTA") is a and agenda.achar(a.id.upper()) is a
    with pytest.raises(ErroRotina, match="Mais de uma"):
        agenda.achar("ligar")
    with pytest.raises(ErroRotina, match="Não há rotina"):
        agenda.achar("padaria")
    with pytest.raises(ErroRotina, match="Diga qual"):
        agenda.achar("")
    assert agenda.apagar("mãe") is b and agenda.listar() == [a]
    assert Agenda(agenda.arquivo).listar() == [a]


def test_do_dia_e_texto_sem_nada(agenda, relogio):
    assert agenda.texto() == "Nenhuma rotina marcada."
    hoje = agenda.criar("hoje", "lembrete", em="2026-10-09T23:59")
    agenda.criar("amanhã", "lembrete", em="2026-10-10T00:01")
    assert agenda.do_dia() == [hoje]
    assert "amanhã 00:01" in agenda.texto()


def test_vencidas_apaga_a_de_uma_vez_e_adianta_a_repetida(agenda, relogio):
    uma = agenda.criar("uma vez", "lembrete", em="2026-10-09T09:30")
    diaria = agenda.criar("todo dia", "tarefa", hora="09:45", repeticao="diaria")
    assert agenda.vencidas() == []
    relogio.avancar(minutes=50)  # 09:50
    vencidas = agenda.vencidas()
    assert [(r.id, r.texto, atraso) for r, atraso in vencidas] == [
        (uma.id, "uma vez", timedelta(minutes=20)),
        (diaria.id, "todo dia", timedelta(minutes=5)),
    ]
    assert vencidas[1][0].proxima == "2026-10-09T09:45"  # a cópia entregue diz a hora que venceu
    assert [r.id for r in agenda.listar()] == [diaria.id]
    assert diaria.proxima == "2026-10-10T09:45" and diaria.vezes == 1 and diaria.ultima == "2026-10-09T09:45"
    assert agenda.vencidas() == []  # cada vencimento sai uma vez só
    assert Agenda(agenda.arquivo).listar()[0].proxima == "2026-10-10T09:45"  # gravado


def test_vencidas_depois_de_dias_desligado_pula_para_a_proxima_sem_acumular(agenda, relogio):
    diaria = agenda.criar("todo dia", "lembrete", hora="10:00", repeticao="diaria")
    relogio.avancar(days=3, hours=2)  # 12/10 11:00
    vencidas = agenda.vencidas()
    assert len(vencidas) == 1 and vencidas[0][1] == timedelta(days=3, hours=1)
    assert diaria.proxima == "2026-10-13T10:00"


def test_arquivo_estragado_ou_item_mal_formado_nao_derruba(tmp_path, caplog):
    arquivo = tmp_path / "rotinas.json"
    arquivo.write_text("{isso não é json", encoding="utf-8")
    assert Agenda(arquivo).listar() == []
    arquivo.write_text(
        json.dumps({"rotinas": [
            {"id": "ok", "texto": "válida", "tipo": "lembrete", "proxima": "2026-10-09T15:30", "criada_em": "2026-10-09T09:00"},
            {"id": "x", "texto": "sem data"},
            {"id": "y", "texto": "data torta", "tipo": "lembrete", "proxima": "ontem", "criada_em": ""},
            {"id": "z", "texto": "tipo torto", "tipo": "ordem", "proxima": "2026-10-09T15:30", "criada_em": ""},
            {"id": "w", "texto": "campo a mais", "tipo": "tarefa", "proxima": "2026-10-09T15:30", "criada_em": "", "extra": 1},
        ]}),  # fmt: skip
        encoding="utf-8",
    )
    assert sorted(r.id for r in Agenda(arquivo).listar()) == ["ok", "w"]
    arquivo.write_text("[]", encoding="utf-8")
    assert Agenda(arquivo).listar() == []


def test_ao_mudar_e_chamado_e_uma_falha_nele_nao_atrapalha(agenda):
    chamadas = []

    def avisar():
        chamadas.append(1)
        raise RuntimeError("painel fora do ar")

    agenda.ao_mudar = avisar
    r = agenda.criar("x", "lembrete", em="2026-10-09T15:30")
    agenda.apagar(r.id)
    assert chamadas == [1, 1]


# --- o despertador ------------------------------------------------------------------------------------


def test_despertador_entrega_as_vencidas_e_aguenta_falhas(agenda, relogio, caplog):
    entregues = []

    def cumprir(rotina, atraso):
        entregues.append((rotina.texto, atraso))
        if rotina.texto == "quebra":
            raise RuntimeError("falhou ao entregar")

    despertador = Despertador(agenda, cumprir, relogio)
    agenda.criar("quebra", "lembrete", em="2026-10-09T09:10")
    agenda.criar("segue", "lembrete", em="2026-10-09T09:10")
    assert despertador.conferir() == 0
    relogio.avancar(minutes=11)
    assert despertador.conferir() == 2
    assert [t for t, _ in entregues] == ["quebra", "segue"] and "Falha ao entregar" in caplog.text
    assert despertador.conferir() == 0


def test_despertador_roda_no_laco(agenda, relogio):
    entregues = []

    async def cena():
        tarefa = asyncio.ensure_future(Despertador(agenda, lambda r, a: entregues.append(r.texto), relogio).rodar(0.01))
        agenda.criar("já", "lembrete", em="2026-10-09T09:01")
        relogio.avancar(minutes=2)
        for _ in range(100):
            if entregues:
                break
            await asyncio.sleep(0.01)
        tarefa.cancel()

    asyncio.run(cena())
    assert entregues == ["já"]


def test_despertador_sobrevive_a_agenda_quebrada(agenda, relogio, caplog):
    agenda.vencidas = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disco cheio"))
    assert Despertador(agenda, lambda r, a: None, relogio).conferir() == 0
    assert "Falha ao conferir" in caplog.text


# --- as ferramentas ------------------------------------------------------------------------------------


def _resposta(saida):
    return saida["content"][0]["text"], bool(saida.get("is_error"))


def test_ferramentas_de_rotina_so_existem_com_agenda(tmp_path, agenda):
    sem = {f.name for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"))}
    assert not sem & set(ferramentas.DE_ROTINA)
    com = {f.name for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), agenda=agenda)}
    assert set(ferramentas.DE_ROTINA) <= com
    assert {"rotina_criar", "rotina_apagar"} <= set(ferramentas.SO_EM_TURNO_LIMPO)  # só a fala do usuário marca ou desmarca
    assert "rotinas_listar" not in ferramentas.CONTAMINAM  # o que está lá foi o usuário que marcou


def test_ferramentas_criam_listam_e_apagam(tmp_path, agenda):
    por_nome = {f.name: f for f in ferramentas.criar_ferramentas(Memoria(tmp_path / "m"), Registro(":memory:"), agenda=agenda)}

    async def cena():
        texto, erro = _resposta(await por_nome["rotina_criar"].handler({"texto": "ligar para o dentista", "tipo": "lembrete", "em": "2026-10-09T15:30"}))
        assert not erro and texto.startswith("Marcado: lembrete ") and "em 09/10/2026 às 15:30: ligar para o dentista" in texto
        texto, erro = _resposta(await por_nome["rotina_criar"].handler({"texto": "abrir o e-mail", "tipo": "tarefa", "hora": "08:00", "repeticao": "dias_uteis"}))
        assert not erro and "nos dias úteis às 08:00" in texto
        texto, erro = _resposta(await por_nome["rotina_criar"].handler({"texto": "x", "tipo": "tarefa", "hora": "08:00", "repeticao": "semanal"}))
        assert erro and "informe 'dias'" in texto
        texto, erro = _resposta(await por_nome["rotinas_listar"].handler({}))
        assert not erro and "ligar para o dentista" in texto and "abrir o e-mail" in texto
        texto, erro = _resposta(await por_nome["rotina_apagar"].handler({"rotina": "dentista"}))
        assert not erro and texto.startswith("Desmarcado: lembrete ")
        texto, erro = _resposta(await por_nome["rotina_apagar"].handler({"rotina": "dentista"}))
        assert erro and "Não há rotina" in texto
        texto, _ = _resposta(await por_nome["rotinas_listar"].handler({}))
        assert "dentista" not in texto and "abrir o e-mail" in texto

    asyncio.run(cena())
    assert len(agenda.listar()) == 1


def test_na_sessao_marcar_so_vale_em_turno_limpo(tmp_path, agenda):
    from jarvis import agente
    from test_agente import cfg

    sessao = agente.montar_sessao(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), pasta_dados=tmp_path / ".jarvis", agenda=agenda)
    assert {f"mcp__jarvis__{n}" for n in ferramentas.DE_ROTINA} <= set(sessao.opcoes.allowed_tools)
    vigia = sessao.guarda
    assert vigia.decidir("mcp__jarvis__rotina_criar", {"texto": "x"}) is None
    assert vigia.decidir("mcp__jarvis__rotinas_listar", {}) is None
    assert vigia.decidir("mcp__jarvis__rotina_criar", {"texto": "x"}) is None  # listar não contamina
    assert vigia.decidir("Read", {"file_path": str(tmp_path / "Documents" / "a.txt")}) is None
    assert "Ação bloqueada" in (vigia.decidir("mcp__jarvis__rotina_criar", {"texto": "x"}) or "")  # leu um arquivo: uma rotina não nasce daí
    assert "Ação bloqueada" in (vigia.decidir("mcp__jarvis__rotina_apagar", {"rotina": "x"}) or "")
    assert vigia.decidir("mcp__jarvis__rotinas_listar", {}) is None
    prompt = (tmp_path / ".jarvis" / "prompt-sistema.md").read_text(encoding="utf-8")
    assert "Rotinas: você marca lembretes e tarefas" in prompt and "Há 0 marcada(s)" in prompt


# --- a entrega na conversa -------------------------------------------------------------------------------


def test_entregar_rotina_na_hora(agenda, relogio):
    entrada = cli.Entrada()
    lembrete = agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    tarefa = agenda.criar("abrir o e-mail e me dizer o que chegou", "tarefa", hora="08:00", repeticao="diaria")
    cli.entregar_rotina(lembrete, timedelta(minutes=1), entrada)
    cli.entregar_rotina(tarefa, timedelta(minutes=0), entrada)
    assert entrada.tirar() == ("lembrete", "Lembrete: ligar para o dentista")
    assert entrada.tirar() == ("rotina", "[rotina todo dia às 08:00] abrir o e-mail e me dizer o que chegou")
    assert entrada.tirar() is None


def test_entregar_rotina_atrasada(agenda, relogio, capsys, monkeypatch):
    entrada = cli.Entrada()
    lembrete = agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    tarefa = agenda.criar("abrir o e-mail", "tarefa", em="2026-10-09T15:31")
    diaria = agenda.criar("conferir o caixa", "lembrete", hora="18:00", repeticao="dias_uteis")

    class Hoje(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 10, 8, 0)

    monkeypatch.setattr(cli, "datetime", Hoje)
    cli.entregar_rotina(lembrete, timedelta(hours=16, minutes=30), entrada)  # lembrete de uma vez: dito mesmo tarde
    cli.entregar_rotina(tarefa, timedelta(hours=16, minutes=29), entrada)  # tarefa de uma vez: 12 h é o limite
    cli.entregar_rotina(diaria, timedelta(hours=14), entrada)  # repetida: 3 h é o limite; a próxima vem de qualquer jeito
    assert entrada.tirar() == ("lembrete", "Lembrete atrasado, era para ontem às 15:30: ligar para o dentista")
    assert entrada.tirar() is None
    saida = capsys.readouterr().out
    assert "Ficou para trás (tarefa de 09/10 15:31" in saida and "Ficou para trás (lembrete de 09/10 18:00" in saida
    # Dentro do limite, vai normalmente.
    cli.entregar_rotina(tarefa, timedelta(hours=11), entrada)
    cli.entregar_rotina(diaria, timedelta(hours=2), entrada)
    assert entrada.tirar() == ("rotina", "[rotina em 09/10/2026 às 15:31] abrir o e-mail")
    assert entrada.tirar() == ("lembrete", "Lembrete: conferir o caixa")


def test_lembrar_fala_mostra_e_avisa(capsys, bipes):
    class VozFalsa:
        muda = False

        def __init__(self):
            self.dito = []

        def narrar(self, t):
            self.dito.append(t)

        def fim_de_bloco(self):
            self.dito.append("|")

    class PainelFalso:
        def __init__(self):
            self.feito = []

        def legenda(self, quem, texto):
            self.feito.append(("legenda", quem, texto))

        def paineis(self, lista):
            self.feito.append(("paineis", lista))
            return len(lista)

        def falar_texto(self, texto):
            self.feito.append(("falar_texto", texto))

    class BandejaFalsa:
        def __init__(self):
            self.avisos = []

        def avisar(self, texto):
            self.avisos.append(texto)

    voz, painel, bandeja = VozFalsa(), PainelFalso(), BandejaFalsa()
    asyncio.run(cli.lembrar("Lembrete: ligar para o dentista", voz, painel, bandeja))
    assert voz.dito == ["Lembrete: ligar para o dentista", "|"] and bandeja.avisos == ["Lembrete: ligar para o dentista"]
    assert painel.feito == [
        ("legenda", "jarvis", "Lembrete: ligar para o dentista"),
        ("paineis", [{"titulo": "Lembrete", "categoria": "Rotina · Agenda", "icone": "agenda", "texto": "ligar para o dentista"}]),
    ]
    assert "[lembrete] Lembrete: ligar para o dentista" in capsys.readouterr().out and bipes == []
    # Sem voz (ou muda): bipe e o texto "falado" pelo painel.
    voz.muda = True
    asyncio.run(cli.lembrar("Lembrete: x", voz, painel, None))
    assert bipes == [988] and painel.feito[-1] == ("falar_texto", "Lembrete: x") and voz.dito == ["Lembrete: ligar para o dentista", "|"]
    asyncio.run(cli.lembrar("Lembrete: y", None, MUDO, None))  # sem painel nem voz: só o terminal
    assert bipes == [988, 988]


def test_lembrar_aguenta_painel_quebrado(capsys, bipes, caplog):
    class PainelQuebrado:
        def legenda(self, *a):
            pass

        def paineis(self, lista):
            raise RuntimeError("servidor caiu")

        def falar_texto(self, t):
            pass

    asyncio.run(cli.lembrar("Lembrete: z", None, PainelQuebrado(), None))
    assert "Não consegui mostrar o lembrete no painel" in caplog.text and "[lembrete] Lembrete: z" in capsys.readouterr().out


def test_anunciar_rotinas(agenda, capsys):
    cli.anunciar_rotinas(agenda)
    assert capsys.readouterr().out == ""
    agenda.criar("ligar para o dentista", "lembrete", em="2026-10-09T15:30")
    agenda.criar("amanhã", "lembrete", em="2026-10-10T08:00")
    cli.anunciar_rotinas(agenda)
    saida = capsys.readouterr().out
    assert "Rotinas de hoje:" in saida and "15:30 · lembrete · ligar para o dentista" in saida and "amanhã" not in saida

