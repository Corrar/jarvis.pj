"""A palavra de ativação: cortar a fala pelo silêncio, reconhecer o chamado e entregar o pedido."""

import pytest

np = pytest.importorskip("numpy")

from jarvis.voz import Voz  # noqa: E402
from jarvis.voz.escuta import TAXA  # noqa: E402
from jarvis.voz.fala import medir_volumes, volume_no_ar  # noqa: E402
from jarvis.voz.vigilia import COMECO, ESPERA_DO_PEDIDO, FIM, JANELA, PAUSA, Cortador, Vigia, ler_palavras, separar_pedido  # noqa: E402

# --- a palavra ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "pedido"),
    [
        ("Jarvis, abre o YouTube.", "abre o YouTube"),
        ("jarvis abre o youtube", "abre o youtube"),
        ("Ei, Jarvis! Que horas são?", "Que horas são"),
        ("Ok Jarvis, fecha essa aba", "fecha essa aba"),
        ("Jarvis.", ""),
        ("  Jarvis…  ", ""),
        ("Járvis, toca uma música", "toca uma música"),
        ("Jarves, abre o bloco de notas", "abre o bloco de notas"),  # a transcrição troca a vogal
        ("Jarviz abre o site", "abre o site"),
        ("Jar vis, abre o site", "abre o site"),  # ou parte o nome em dois
        ("J.A.R.V.I.S., abre o site", "abre o site"),  # ou soletra
        ("Djarvis, pausa o vídeo", "pausa o vídeo"),
        ("Jarvi, pausa o vídeo", "pausa o vídeo"),
        ("Javis, pausa o vídeo", "pausa o vídeo"),
        ("Garvis, pausa o vídeo", "pausa o vídeo"),
        ("Ô Jarvis, aumenta o volume", "aumenta o volume"),
        ("Oh, Jarvis, aumenta o volume", "aumenta o volume"),
        ("E aí, Jarvis, tudo certo?", "tudo certo"),
        ("Bom dia, Jarvis", ""),
        ("Boa noite, Jarvis, apaga a luz", "apaga a luz"),
        ("Ok, ei, Jarvis, pausa", "pausa"),
        ("Opa, Jarvis!", ""),
        ("Fala, Jarvis. Que horas são?", "Que horas são"),
    ],
)
def test_separar_pedido_reconhece_o_chamado(texto, pedido):
    assert separar_pedido(texto) == pedido


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "abre o YouTube",
        "eu falei com o Jarvis ontem",  # o nome no meio da frase não é chamado
        "com o Jarvis a gente resolve",
        "O Jarvis abre o YouTube sozinho",  # falar dele não é falar com ele
        "E o Jarvis, funcionou?",
        "Aí o Jarvis travou",
        "Jardim, abre o portão",
        "Marvin, abre o YouTube",
        "Já vi isso",
        "Já vimos esse filme",
        "Jarra de suco",
        "Davi, vem cá",
        "Jair, abre a porta",
        "Javier chegou",
        "Chaves, por favor",
        "Serviço de entrega",
        "Jardins suspensos",
        "Jarbas, abre a porta",
        "Travis, abre a porta",
        "Já vis…",
        "Carlos, abre o YouTube",
        "Bom dia a todos",
        "Bom, Jarvis não funcionou",  # "bom" e "boa" só chamam em "bom dia", "boa tarde", "boa noite"
        "Boa, Jarvis fez certo",
        "Dia Jarvis",
        "E Jarvis disse que sim",
        "Fala comigo",
        "A B C D E F",
        "ei ok oi Jarvis, abre",  # só duas palavras de chamada antes do nome
    ],
)
def test_separar_pedido_ignora_o_resto(texto):
    assert separar_pedido(texto) is None


def test_palavra_de_ativacao_escolhida_pelo_usuario():
    palavras = ler_palavras("Sexta-Feira; jarvis")
    assert palavras == ("sexta feira", "jarvis")
    assert separar_pedido("Sexta-feira, que horas são?", ler_palavras("sexta feira")) == "que horas são"
    assert separar_pedido("Sexta feira, que horas são?", ler_palavras("sexta feira")) == "que horas são"
    assert separar_pedido("Jarvis, que horas são?", ler_palavras("sexta feira")) is None
    assert ler_palavras("") == ("jarvis",)
    assert ler_palavras("Jarvis, Jarbas,jarvis") == ("jarvis", "jarbas")
    with pytest.raises(ValueError, match="4 letras"):
        ler_palavras("oi")


def test_palavra_curta_so_vale_exata_e_pode_comecar_com_uma_chamada():
    nina = ler_palavras("nina")
    assert separar_pedido("Nina, abre o site", nina) == "abre o site"
    for parecida in ("Mina de ouro", "Tina, vem cá", "Fina estampa", "Ninar o bebê"):
        assert separar_pedido(parecida, nina) is None, parecida  # nome curto não tem letra sobrando para errar
    assert separar_pedido("Oi Nina, abre o site", ler_palavras("oi nina")) == "abre o site"
    assert separar_pedido("Oi, Tina, tudo bem?", ler_palavras("oi nina")) is None  # cada palavra do nome conta por si
    assert separar_pedido("Ei amigo, que horas são", ler_palavras("ei amigo")) == "que horas são"


def test_por_cima_da_voz_do_jarvis_o_nome_vale_algumas_palavras_adiante():
    """A transcrição de quem fala por cima dele traz antes o fim da frase dele. Só nesse caso o nome vale fora do começo."""
    assert separar_pedido("a previsão para hoje. Jarvis, para.", solto=True) == "para"
    assert separar_pedido("de sol Jarvis", solto=True) == ""
    assert separar_pedido("Jarvis, para", solto=True) == "para"  # no começo continua valendo
    assert separar_pedido("a previsão para hoje. Jarvis, para.") is None
    assert separar_pedido("um dois três quatro cinco seis sete oito nove Jarvis, para", solto=True) is None  # longe demais do começo
    assert separar_pedido("a previsão para hoje é de sol", solto=True) is None
    assert separar_pedido("e por isso a Sexta-Feira, pausa", ler_palavras("sexta-feira"), solto=True) == "pausa"


# --- cortar a fala ----------------------------------------------------------------------------------


def _ruido(segundos, volume, semente=0, taxa=TAXA):
    return (np.random.default_rng(semente).standard_normal(int(segundos * taxa)) * volume).astype(np.float32)


def _fala(segundos, volume=0.08, taxa=TAXA):
    """Um tom que sobe e desce de volume, como sílabas."""
    t = np.arange(int(segundos * taxa)) / taxa
    return (np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)) * volume * 1.4).astype(np.float32)


def _tv(segundos, baixo, alto, periodo=0.7, semente=3, taxa=TAXA):
    """Som de fundo que sobe e desce o tempo todo, sem pausas: TV, música."""
    t = np.arange(int(segundos * taxa)) / taxa
    volume = baixo + (alto - baixo) * (0.5 + 0.5 * np.sin(2 * np.pi * t / periodo)) ** 2
    return (np.random.default_rng(semente).standard_normal(len(t)) * volume).astype(np.float32)


def _montar(*partes, fundo=0.001, taxa=TAXA):
    """Junta silêncios (números, em segundos) e falas (arrays) sobre um ruído de fundo."""
    pedacos = [np.zeros(int(p * taxa), dtype=np.float32) if isinstance(p, (int, float)) else p for p in partes]
    sinal = np.concatenate(pedacos)
    return sinal + _ruido(len(sinal) / taxa, fundo, semente=1, taxa=taxa)


def _cortar(sinal, taxa=TAXA, bloco=0.05, resposta="aceitar", na_pausa=None, cortador=None, tocado=None, firmes=None, **opcoes):
    """Passa o sinal pelo cortador como o microfone faria. Devolve (acontecimentos, cortador).

    Cada acontecimento vira (tipo, instante em segundos, duração em segundos, conferido). A cada
    COMECO responde com `resposta` e a cada PAUSA com `na_pausa` ("aceitar", "dispensar",
    "encerrar", ou None para não responder). As pausas só entram na lista se `na_pausa` não for None.
    `tocado(segundos)` diz o volume do que o Jarvis toca naquele instante. Em `firmes` (uma lista)
    entram os instantes em que o cortador passou a ver uma voz firme por cima da voz do Jarvis.
    """
    cortador = cortador or Cortador(taxa, **opcoes)
    passo = int(bloco * taxa)
    vistos, firme = [], False
    for i in range(0, len(sinal), passo):
        for tipo, som, *resto in cortador.alimentar(sinal[i : i + passo], tocado(i / taxa) if tocado else 0.0):
            if tipo == PAUSA:
                if na_pausa:
                    vistos.append((tipo, round((i + passo) / taxa, 2), round(len(som) / taxa, 2), resto[0]))
                    getattr(cortador, na_pausa)()
                continue
            vistos.append((tipo, round((i + passo) / taxa, 2), round(len(som) / taxa, 2), bool(resto and resto[0])))
            if tipo == COMECO and resposta:
                getattr(cortador, resposta)()
        if firmes is not None and cortador.sobre_o_jarvis and not firme:
            firmes.append(round((i + passo) / taxa, 2))
        firme = cortador.sobre_o_jarvis
    return vistos, cortador


def _fins(vistos):
    return [v for v in vistos if v[0] == FIM]


def test_fala_curta_avisa_na_pausa_e_depois_no_fim():
    """Meio segundo depois de a fala parar já dá para ir vendo o que ela diz; o fim vem com um segundo."""
    cortador, vistos = Cortador(), []
    sinal = _montar(1.5, _fala(1.2), 2.0)
    for i in range(0, len(sinal), 800):
        for tipo, som, *resto in cortador.alimentar(sinal[i : i + 800]):
            vistos.append((tipo, round((i + 800) / TAXA, 2), round(len(som) / TAXA, 2), *resto))
    (pausa, quando_pausa, tamanho, falados), (fim, quando_fim, _, conferido, falados_no_fim) = vistos
    assert pausa == PAUSA and quando_pausa == pytest.approx(1.5 + 1.2 + 0.45, abs=0.1)
    assert tamanho == pytest.approx(0.3 + 1.2 + 0.45, abs=0.1)  # a fala inteira até ali
    assert fim == FIM and quando_fim == pytest.approx(1.5 + 1.2 + 1.0, abs=0.1) and conferido is False
    assert falados == falados_no_fim > 20  # nada foi dito depois da pausa: o que se viu nela ainda vale


def test_na_pausa_da_para_aceitar_dispensar_ou_encerrar():
    sinal = _montar(1.5, _fala(1.2), 2.5, _fala(1.0), 2.0)
    aceita, _ = _cortar(sinal, na_pausa="aceitar")
    assert [(v[0], v[3]) for v in aceita if v[0] == FIM] == [(FIM, True), (FIM, True)]  # seguem até o fim, já conferidas
    dispensada, cortador = _cortar(sinal, na_pausa="dispensar")
    assert [v[0] for v in dispensada] == [PAUSA, PAUSA] and not cortador.em_fala  # nenhuma chega ao fim
    encerrada, _ = _cortar(_montar(1.5, _fala(0.6), 0.7, _fala(1.5), 2.0), na_pausa="encerrar")
    # "Jarvis" … "abre o YouTube", com 0,7 s entre os dois: encerrada no nome, o resto vira outra fala.
    assert [v[0] for v in encerrada] == [PAUSA, PAUSA] and encerrada[1][2] == pytest.approx(0.3 + 1.5 + 0.45, abs=0.15)


def test_fala_que_para_e_continua_avisa_a_cada_pausa():
    sinal = _montar(1.5, _fala(0.6), 0.7, _fala(0.8), 2.0)  # duas pausas antes dos três segundos
    cortador, pausas = Cortador(), []
    for i in range(0, len(sinal), 800):
        pausas += [a for a in cortador.alimentar(sinal[i : i + 800]) if a[0] == PAUSA]
    assert len(pausas) == 2 and pausas[1][2] > pausas[0][2]  # a segunda traz mais fala que a primeira
    assert len(pausas[1][1]) > len(pausas[0][1])


def test_estalo_e_fala_comprida_nao_geram_pausa():
    assert _cortar(_montar(1.5, _fala(0.09, volume=0.3), 3.0), na_pausa="aceitar")[0] == []
    comprida, _ = _cortar(_montar(1.5, _fala(6.0), 2.5), na_pausa="aceitar")
    assert [v[0] for v in comprida] == [COMECO, FIM]  # passou dos três segundos falando: o aviso é o do começo


# --- a voz do próprio Jarvis no microfone ---------------------------------------------------------


def _caixa(*partes, ganho=0.4, atraso=0.15, fundo=0.001, a_mais=0.0):
    """O Jarvis falando pela caixa de som. Devolve (o que o microfone ouve, tocado(segundos)).

    `partes` como em `_montar`: cada som é uma frase que ele toca, no volume em que toca; cada número, um
    silêncio entre frases. O microfone ouve cada frase `atraso` segundos depois, multiplicada por `ganho`
    (zero: fone de ouvido). `tocado` é o que o Falante de verdade diria: sabe quando cada frase começou e
    quando acabou de tocar. `a_mais`: atraso que ele não conhece (caixa Bluetooth), só no que o microfone ouve.
    """
    total = sum(int(p * TAXA) if isinstance(p, (int, float)) else len(p) for p in partes)
    eco, frases, a = np.zeros(total, dtype=np.float32), [], 0
    for parte in partes:
        if isinstance(parte, (int, float)):
            a += int(parte * TAXA)
            continue
        chega = a + int((atraso + a_mais) * TAXA)
        trecho = parte[: max(0, total - chega)]
        eco[chega : chega + len(trecho)] += trecho * ganho
        frases.append((a / TAXA, (a + len(parte)) / TAXA + atraso, medir_volumes(parte, TAXA)))
        a += len(parte)

    def tocado(segundos):
        return max((volume_no_ar(volumes, inicio, fim if segundos >= fim else None, segundos) for inicio, fim, volumes in frases if segundos >= inicio), default=0.0)

    return eco + _ruido(total / TAXA, fundo, semente=1), tocado


def _por_cima(microfone, voz, em):
    """Soma uma voz ao que o microfone ouve, a partir do instante `em`."""
    junto = microfone.copy()
    a = int(em * TAXA)
    junto[a : a + len(voz)] += voz
    return junto


def _sala_medida():
    cortador = Cortador()
    cortador.alimentar(_montar(1.5))
    return cortador


def _fala_em_curso_com_ele_falando(ganho_conhecido, voz_por_cima=0.0):
    """A pessoa fala, e meio segundo depois o Jarvis começa a falar (0,6 s). Devolve o áudio da fala que o cortador entregou."""
    cortador = _sala_medida()
    if ganho_conhecido:  # ele já falou antes: sabe-se com que volume chega ao microfone
        antes, tocado_antes = _caixa(_fala(2.0, volume=0.15), 1.0)
        _cortar(antes, cortador=cortador, tocado=tocado_antes)
        assert cortador._ganho is not None
    dele, tocado_dele = _caixa(0.9, _fala(0.6, volume=0.15), 2.5)
    microfone = _por_cima(dele, _fala(0.6), 0.3)  # a pessoa, de 0,3 a 0,9 s; a voz dele chega de 1,05 a 1,65 s
    if voz_por_cima:
        microfone = _por_cima(microfone, _fala(0.6, volume=voz_por_cima), 1.05)
    entregues = []
    for i in range(0, len(microfone), 800):
        for tipo, som, *_ in cortador.alimentar(microfone[i : i + 800], tocado_dele(i / TAXA)):
            if tipo == FIM:
                entregues.append(som)
    return entregues


@pytest.mark.parametrize("ganho_conhecido", [False, True])
def test_voz_do_jarvis_que_comeca_no_meio_de_uma_fala_nao_entra_nela(ganho_conhecido):
    """A resposta ao nome saiu com a pessoa ainda falando: o "Sim?" dele não pode sair escrito no meio do que ela disse."""
    (som,) = _fala_em_curso_com_ele_falando(ganho_conhecido)
    # A fala acaba onde a pessoa se calou: 0,3 s de antes, 0,6 s dela e meio segundo de cauda. A voz dele não a esticou.
    assert len(som) / TAXA == pytest.approx(0.3 + 0.6 + 0.5, abs=0.1)
    quadros = som[: len(som) // 480 * 480].reshape(-1, 480)
    volumes = np.sqrt((quadros**2).mean(axis=1))
    mudos = np.flatnonzero(volumes == 0.0)
    # A fala dela está lá; do instante em que ele começou a tocar (0,6 s depois de ela começar) em diante, silêncio.
    assert mudos[0] == pytest.approx((0.3 + 0.6) / 0.03, abs=3) and list(mudos) == list(range(mudos[0], len(volumes)))
    assert volumes[9 : mudos[0]].min() > 0.01


def test_quem_fala_mais_alto_que_o_jarvis_no_meio_da_propria_fala_continua_sendo_ouvido():
    """Com o volume dele conhecido, só é apagado o que chega no volume dele: a pessoa, mais alta, fica."""
    (som,) = _fala_em_curso_com_ele_falando(True, voz_por_cima=0.3)
    assert len(som) / TAXA == pytest.approx(0.3 + 0.6 + 0.15 + 0.6 + 0.5, abs=0.15)
    quadros = som[: len(som) // 480 * 480].reshape(-1, 480)
    volumes = np.sqrt((quadros**2).mean(axis=1))
    assert volumes[int(1.15 / 0.03) : int(1.5 / 0.03)].max() > 0.1  # a voz dela, por cima da dele, foi junto


def test_fala_que_comecou_por_cima_do_jarvis_leva_o_som_inteiro():
    """Quem interrompe a fala dele ("Jarvis, para") fala junto com ele: aí nada é apagado, o nome é procurado no meio."""
    cortador = _sala_medida()
    dele, tocado = _caixa(_fala(4.0, volume=0.15), 2.0)
    microfone = _por_cima(dele, _fala(0.8, volume=0.4), 2.0)
    entregues = [som for tipo, som, *_ in _crus(cortador, microfone, tocado) if tipo == FIM]
    assert len(entregues) == 1 and cortador.sobre_ele
    quadros = entregues[0][: len(entregues[0]) // 480 * 480].reshape(-1, 480)
    volumes = np.sqrt((quadros**2).mean(axis=1))
    assert volumes[5:20].min() > 0.02
    assert volumes[-12:].min() > 0.005  # a cauda, em que só ele fala, também foi (nada apagado)


def _crus(cortador, microfone, tocado):
    for i in range(0, len(microfone), 800):
        yield from cortador.alimentar(microfone[i : i + 800], tocado(i / TAXA))


def test_voz_do_jarvis_nao_e_fala_de_ninguem_e_nao_muda_a_sala():
    """Enquanto ele fala, o microfone ouve a voz dele: nada disso é fala de alguém nem vira o barulho da sala."""
    cortador = _sala_medida()
    limiar = cortador.limiar
    microfone, tocado = _caixa(_fala(25.0, volume=0.15))
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=tocado, na_pausa="aceitar")
    assert vistos == [] and not cortador.em_fala and not cortador.sobre_o_jarvis
    assert cortador.limiar == pytest.approx(limiar, rel=0.3)  # vinte e cinco segundos falando e a sala é a mesma


@pytest.mark.parametrize("atraso", [0.02, 0.15, 0.35])
def test_cada_frase_do_jarvis_comeca_sem_parecer_alguem_falando(atraso):
    """Frases curtas e compridas, com silêncio entre elas, e a caixa mais ou menos atrasada: nenhuma vira fala."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(0.4, 0.2), 1.3, _fala(1.5, 0.12), 0.6, _fala(3.0, 0.2), 1.3, _fala(0.5, 0.15), 2.0, atraso=atraso)
    firmes = []
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=tocado, na_pausa="aceitar", firmes=firmes)
    assert vistos == [] and firmes == []


def test_voz_mais_alta_por_cima_do_jarvis_vira_fala():
    microfone, tocado = _caixa(_fala(8.0, volume=0.1))  # ele chega ao microfone com 0,04
    microfone = _por_cima(microfone, _fala(1.2, volume=0.2), em=3.0)  # "Jarvis, para", bem mais alto que ele
    cortador, firmes, vistos = _sala_medida(), [], []
    passo = 800
    for i in range(0, len(microfone), passo):
        for tipo, som, *_resto in cortador.alimentar(microfone[i : i + passo], tocado(i / TAXA)):
            vistos.append((tipo, round((i + passo) / TAXA, 2), round(len(som) / TAXA, 2), cortador.sobre_ele))
        if cortador.sobre_o_jarvis and not firmes:
            firmes.append(round((i + passo) / TAXA, 2))
    assert firmes and firmes[0] == pytest.approx(3.0 + 0.3, abs=0.15)  # um quarto de segundo de voz firme: hora de ele se calar
    assert [v[0] for v in vistos] == [PAUSA, FIM] and all(v[3] for v in vistos)  # e quem recebe sabe que ela nasceu por cima dele
    assert vistos[0][1] == pytest.approx(3.0 + 1.2 + 0.45, abs=0.15) and vistos[0][2] == pytest.approx(0.3 + 1.2 + 0.45, abs=0.2)
    assert not cortador.sobre_o_jarvis  # acabou a voz, voltou a ser só ele


def test_voz_no_volume_do_jarvis_nao_passa_por_cima_dele():
    """O limite que existe: para cortar a fala dele pela voz é preciso chegar ao microfone mais alto do que ele."""
    microfone, tocado = _caixa(_fala(8.0, volume=0.1))
    microfone = _por_cima(microfone, _fala(1.2, volume=0.045), em=3.0)
    firmes = []
    vistos, _ = _cortar(microfone, cortador=_sala_medida(), tocado=tocado, na_pausa="aceitar", firmes=firmes)
    assert vistos == [] and firmes == []


def test_voz_dele_quase_no_volume_da_sala_tambem_nao_vira_fala():
    """Caixa baixa: a voz dele mal passa do barulho da sala, e só uma sílaba mais forte, de vez em quando, passa do limiar."""
    quadro = int(0.03 * TAXA)
    cortador = Cortador()
    cortador.alimentar(_montar(1.5, fundo=0.002))
    microfone = _ruido(30.0, 0.003, semente=2)  # a sala (0,002) mais a voz dele, bem baixa
    for inicio in range(100, len(microfone) // quadro - 10, 300):  # a cada nove segundos, 0,3 s em que ele chega mais forte
        microfone[inicio * quadro : (inicio + 10) * quadro] = _ruido(0.3, 0.0055, semente=inicio)
    assert cortador.limiar < 0.005  # essas sílabas passam do limiar da sala
    firmes = []
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=lambda _segundos: 0.1, na_pausa="dispensar", firmes=firmes)
    assert vistos == [] and firmes == []


def test_com_fone_de_ouvido_o_barulho_da_sala_nao_conta_como_voz_dele():
    """Sala barulhenta e fone de ouvido: o que o microfone ouve enquanto ele fala é a sala, não ele. Voz pouco acima dela é ouvida."""
    cortador = Cortador()
    cortador.alimentar(_montar(1.5, fundo=0.004))
    microfone, tocado = _caixa(_fala(8.0, volume=0.15), ganho=0.0, fundo=0.004)
    microfone = _por_cima(microfone, _ruido(1.2, 0.012, semente=9), em=3.0)  # o limiar da sala anda por 0,009
    firmes = []
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=tocado, na_pausa="aceitar", firmes=firmes)
    assert [v[0] for v in vistos] == [PAUSA, FIM] and firmes


def test_com_fone_de_ouvido_qualquer_voz_e_ouvida_por_cima_do_jarvis():
    microfone, tocado = _caixa(_fala(8.0, volume=0.15), ganho=0.0)  # o som dele não chega ao microfone
    microfone = _por_cima(microfone, _fala(1.2, volume=0.02), em=3.0)  # alguém falando baixo
    firmes = []
    vistos, _ = _cortar(microfone, cortador=_sala_medida(), tocado=tocado, na_pausa="aceitar", firmes=firmes)
    assert [v[0] for v in vistos] == [PAUSA, FIM] and firmes and firmes[0] == pytest.approx(3.3, abs=0.15)


def test_com_tv_ao_fundo_a_voz_do_jarvis_nao_e_a_voz_mais_alta_que_chama():
    """Uma fala da TV foi dispensada e segue ao fundo. O Jarvis começa a falar, bem mais alto que ela: não é alguém chamando."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(4.0, volume=0.2), 1.0, ganho=0.3)
    _cortar(microfone, cortador=cortador, tocado=tocado)  # o cortador já sabe como a voz dele chega
    tv = _fala(8.0, volume=0.012)
    dele, tocado_dele = _caixa(3.5, _fala(4.0, volume=0.2), 0.5, ganho=0.3)  # aos 3,5 s da TV ele começa a falar (chega com 0,06)
    firmes = []
    vistos, _ = _cortar(_montar(tv) + dele, cortador=cortador, tocado=tocado_dele, resposta="dispensar", na_pausa="dispensar", firmes=firmes)
    assert [v[0] for v in vistos] == [COMECO] and firmes == []  # só a TV foi conferida, uma vez


def test_tosse_por_cima_do_jarvis_nao_chega_a_ser_voz_firme():
    microfone, tocado = _caixa(_fala(6.0, volume=0.1))
    microfone = _por_cima(microfone, _fala(0.12, volume=0.3), em=3.0)
    firmes = []
    vistos, _ = _cortar(microfone, cortador=_sala_medida(), tocado=tocado, na_pausa="aceitar", firmes=firmes)
    assert vistos == [] and firmes == []


def test_na_primeira_frase_do_jarvis_ninguem_e_ouvido_por_cima_e_depois_dela_sim():
    """Antes de saber com que volume ele chega ao microfone, o cortador não arrisca: aprende primeiro."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(0.4, volume=0.2), 3.0)  # "Sim?": curto demais para aprender
    firmes = []
    vistos, _ = _cortar(_por_cima(microfone, _fala(1.0, 0.1), em=0.5), cortador=cortador, tocado=tocado, na_pausa="aceitar", firmes=firmes)
    # O pedido emendado no "Sim?" não se perde: assim que ele se cala a fala começa, com o que veio logo antes.
    assert [v[0] for v in vistos] == [PAUSA, FIM]
    assert vistos[0][2] == pytest.approx(1.0 + 0.45, abs=0.15)


def test_resposta_emendada_no_fim_da_fala_do_jarvis_nao_parece_voz_por_cima_de_outra():
    """O fim da voz dele fica no começo do trecho gravado. A pessoa, mais alta, não é "alguém entrando por cima de uma fala"."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(4.0, volume=0.1), 1.0, ganho=0.3)
    _cortar(microfone, cortador=cortador, tocado=tocado)  # ele já falou antes: o cortador sabe como a voz dele chega
    # Caixa bem atrasada (Bluetooth): o som dele ainda sai 0,15 s depois de a conta dizer que acabou.
    microfone, tocado = _caixa(_ruido(1.0, 0.1), 4.0, ganho=0.3, atraso=0.6)
    microfone = _por_cima(microfone, _fala(1.2, volume=0.25), em=1.6)  # a pessoa responde assim que ele se cala, bem mais alto
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=tocado, na_pausa="aceitar")
    assert [v[0] for v in vistos] == [PAUSA, FIM]  # uma fala só, sem a conferência à parte do "começo mais baixo"
    assert vistos[1][2] == pytest.approx(0.3 + 1.2 + 0.5, abs=0.2)


def test_o_fim_da_voz_do_jarvis_ainda_soando_na_sala_nao_comeca_uma_fala():
    """Ele se calou, mas a caixa (Bluetooth) ainda solta o fim da frase por 150 ms. Isso não é alguém começando a falar."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(4.0, volume=0.1), 1.0, ganho=0.3)
    _cortar(microfone, cortador=cortador, tocado=tocado)
    microfone, tocado = _caixa(_ruido(1.0, 0.1), 2.0, ganho=0.3, a_mais=0.15)  # chega ao microfone com 0,03
    em_fala = False
    for i in range(0, len(microfone), 480):
        assert cortador.alimentar(microfone[i : i + 480], tocado(i / TAXA)) == []
        em_fala = em_fala or cortador.em_fala
    assert not em_fala


def test_logo_que_o_jarvis_se_cala_a_voz_normal_volta_a_ser_ouvida():
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(4.0, volume=0.25), 0.5, _fala(1.2, volume=0.0), 2.0)  # ele falando alto, e se cala
    microfone = _por_cima(microfone, _fala(1.2, volume=0.03), em=4.5)  # meio segundo depois, alguém fala baixo
    vistos, _ = _cortar(microfone, cortador=cortador, tocado=tocado, na_pausa="aceitar")
    assert [v[0] for v in vistos] == [PAUSA, FIM]


def test_aumentaram_o_volume_da_caixa_e_o_engano_nao_se_repete():
    """A voz dele passou a chegar três vezes mais alta. Na primeira frase parece alguém; avisado do engano, não mais."""
    cortador = _sala_medida()
    baixo, tocado = _caixa(_fala(4.0, volume=0.1), 1.5)
    assert _cortar(baixo, cortador=cortador, tocado=tocado, na_pausa="dispensar")[0] == []
    enganos = []
    for rodada in range(4):
        alto, tocado = _caixa(_fala(4.0, volume=0.1), 1.5, ganho=1.2)
        for i in range(0, len(alto), 800):
            for tipo, _som, *_resto in cortador.alimentar(alto[i : i + 800], tocado(i / TAXA)):
                if tipo in (PAUSA, COMECO):
                    # Como a vigília faz: vê que não era um chamado (e avisa do engano) e só então dispensa a fala.
                    enganos.append(rodada)
                    cortador.engano()
                    cortador.dispensar()
    assert enganos and set(enganos) == {0}  # só na primeira frase depois da mudança
    assert cortador._ganho == pytest.approx(1.2, rel=0.25)  # e agora ele sabe como a voz dele chega


def test_a_voz_do_jarvis_dispensada_por_engano_nao_vira_o_fundo_da_sala():
    """Doze segundos da voz dele tomada por fala e dispensada: se virasse "fundo", a sala passaria a ter o volume dele."""
    cortador = _sala_medida()
    limiar = cortador.limiar
    baixo, tocado = _caixa(_fala(4.0, volume=0.1), 1.5, ganho=0.05)
    _cortar(baixo, cortador=cortador, tocado=tocado)  # aprendeu com a caixa baixa
    alto, tocado = _caixa(_fala(14.0, volume=0.1), 0.5, ganho=1.2)  # e agora ela está vinte vezes mais alta
    _cortar(alto, cortador=cortador, tocado=tocado, resposta="dispensar", na_pausa="dispensar")  # ninguém avisa do engano
    assert cortador.limiar < limiar * 3
    vistos, _ = _cortar(_montar(1.0, _fala(1.2, volume=0.03), 2.0), cortador=cortador, na_pausa="aceitar")
    assert [v[0] for v in vistos] == [PAUSA, FIM]  # e uma voz normal, um segundo e meio depois de ele se calar, é ouvida


def test_gente_falando_por_cima_do_jarvis_nao_o_deixa_surdo_por_muito_tempo():
    """Depois de um engano com voz de gente, o que se exige para falar por cima dele volta ao normal em poucos segundos."""
    cortador = _sala_medida()
    microfone, tocado = _caixa(_fala(20.0, volume=0.1))
    microfone = _por_cima(microfone, _fala(1.5, volume=0.25), em=3.0)  # alguém comenta alguma coisa, bem alto
    microfone = _por_cima(microfone, _fala(1.2, volume=0.25), em=12.0)  # e, mais tarde, chama no mesmo volume
    passo, pausas = 800, []
    for i in range(0, len(microfone), passo):
        for tipo, _som, *_resto in cortador.alimentar(microfone[i : i + passo], tocado(i / TAXA)):
            if tipo == PAUSA:
                pausas.append(round(i / TAXA))
                cortador.dispensar()
                cortador.engano()
    assert pausas == [5, 14]


# --- quem chamou e não foi entendido repete em seguida -------------------------------------------


def test_chamado_repetido_logo_depois_de_um_que_nao_foi_entendido_e_conferido_de_novo():
    """Antes, a fala dispensada deixava o microfone surdo até um segundo inteiro de silêncio."""
    sinal = _montar(1.5, _fala(0.6), 0.6, _fala(0.6), 0.6, _fala(0.6, volume=0.05), 2.0)  # "Jarvis"… "Jarvis"… "Jarvis", cada vez sem esperar
    vistos, cortador = _cortar(sinal, na_pausa="dispensar")
    assert [v[0] for v in vistos] == [PAUSA, PAUSA, PAUSA]  # as três foram conferidas, mesmo a última, mais baixa
    assert all(v[2] == pytest.approx(0.3 + 0.6 + 0.45, abs=0.15) for v in vistos)  # e cada uma veio sozinha


def test_chamado_entendido_na_segunda_tentativa_e_o_pedido_vem_inteiro():
    sinal = _montar(1.5, _fala(0.6), 0.6, _fala(0.6), 0.7, _fala(1.5), 2.0)  # não entendeu; "Jarvis"; "abre o YouTube"
    cortador, respostas, vistos = Cortador(), iter(["dispensar", "encerrar", "aceitar"]), []
    for i in range(0, len(sinal), 800):
        for tipo, som, *_resto in cortador.alimentar(sinal[i : i + 800]):
            vistos.append((tipo, round(len(som) / TAXA, 2)))
            if tipo == PAUSA:
                getattr(cortador, next(respostas))()
    assert [v[0] for v in vistos] == [PAUSA, PAUSA, PAUSA, FIM]
    assert vistos[3][1] == pytest.approx(0.3 + 1.5 + 0.5, abs=0.15) and not cortador.em_fala


def test_chamado_entendido_na_terceira_tentativa_encerra_a_conversa_dispensada():
    """Depois do chamado entendido, o pedido que vem em seguida é uma fala nova, não a continuação do que foi dispensado."""
    sinal = _montar(1.5, _fala(0.6), 0.6, _fala(0.6), 0.6, _fala(0.6), 0.7, _fala(1.5), 2.0)
    cortador, respostas, vistos = Cortador(), iter(["dispensar", "dispensar", "encerrar", "aceitar"]), []
    for i in range(0, len(sinal), 800):
        for tipo, som, *_resto in cortador.alimentar(sinal[i : i + 800]):
            vistos.append((tipo, round(len(som) / TAXA, 2)))
            if tipo == PAUSA:
                getattr(cortador, next(respostas))()
    assert [v[0] for v in vistos] == [PAUSA, PAUSA, PAUSA, PAUSA, FIM]
    assert vistos[4][1] == pytest.approx(0.3 + 1.5 + 0.5, abs=0.15)


def test_numa_conversa_so_as_primeiras_frases_depois_de_pausa_sao_conferidas():
    """Quem repete um chamado repete uma ou duas vezes. Conferir cada frase de uma conversa seria transcrever a sala sem parar."""
    frases = [parte for _ in range(12) for parte in (_fala(1.6), 0.5)]
    vistos, cortador = _cortar(_montar(1.5, *frases, 2.0), na_pausa="dispensar", resposta="dispensar")
    assert [v[0] for v in vistos] == [PAUSA, PAUSA, PAUSA]  # a primeira e mais duas; o resto da conversa não custa nada
    assert vistos[-1][1] < 1.5 + 3 * 2.1 + 0.5 and not cortador.em_fala
    # Um segundo de silêncio e a conversa acabou: a conta recomeça.
    mais, _ = _cortar(_montar(_fala(1.0), 0.5, _fala(1.0), 2.0), cortador=cortador, na_pausa="dispensar")
    assert [v[0] for v in mais] == [PAUSA, PAUSA]


def test_voz_mais_alta_no_meio_de_uma_frase_de_conversa_conferida_de_novo_e_ouvida():
    """A conversa ao fundo parou meio segundo e voltou; no meio da frase nova, alguém chama mais alto."""
    fundo = _fala(1.2, volume=0.02)
    # A segunda frase do fundo emenda no chamado, e o fundo segue falando por baixo dele e depois dele.
    sinal = _montar(1.5, fundo, 0.5, fundo, _fala(1.5, volume=0.12) + _fala(1.5, volume=0.02), _fala(2.5, volume=0.02), 2.0)
    cortador, vistos, respostas = Cortador(), [], iter(["dispensar", "dispensar", "aceitar"])
    for i in range(0, len(sinal), 800):
        for tipo, som, *resto in cortador.alimentar(sinal[i : i + 800]):
            vistos.append((tipo, round(len(som) / TAXA, 2), bool(resto and resto[0])))
            if tipo in (PAUSA, COMECO):
                getattr(cortador, next(respostas))()
    # A frase do fundo foi conferida (e dispensada) assim que a voz mais alta entrou; o chamado veio inteiro e sozinho,
    # e o fundo que continuou depois dele não foi conferido outra vez: é a mesma conversa já dispensada.
    assert [v[0] for v in vistos] == [PAUSA, COMECO, PAUSA, FIM]
    assert vistos[3][1] == pytest.approx(0.3 + 1.5 + 0.5, abs=0.15) and vistos[3][2] is True


def test_depois_de_um_segundo_de_silencio_a_conversa_dispensada_acabou():
    sinal = _montar(1.5, _fala(1.2), 0.5, _fala(1.2), 1.6, _fala(1.2), 2.0)
    vistos, cortador = _cortar(sinal, na_pausa="dispensar")
    assert [v[0] for v in vistos] == [PAUSA, PAUSA, PAUSA]
    aceitas, _ = _cortar(_montar(1.5, _fala(1.2), 0.5, _fala(1.2), 2.5), na_pausa="aceitar")
    assert [(v[0], v[3]) for v in aceitas if v[0] == FIM] == [(FIM, True)]  # aceita na primeira pausa, a frase segue junta


def test_cortador_entrega_cada_fala_curta_quando_ela_termina():
    vistos, cortador = _cortar(_montar(1.5, _fala(1.2), 1.5, _fala(2.0), 2.0))
    assert [v[0] for v in vistos] == [FIM, FIM]  # falas mais curtas que a janela: só o fim, sem conferência
    assert [v[2] for v in vistos] == pytest.approx([2.0, 2.8], abs=0.2)  # a fala, com 0,3 s antes e 0,5 s depois
    assert not any(v[3] for v in vistos) and not cortador.em_fala


def test_cortador_nao_parte_a_frase_numa_pausa_curta():
    vistos, _ = _cortar(_montar(1.5, _fala(0.6), 0.5, _fala(1.5), 2.0))  # "Jarvis… abre o YouTube"
    fins = _fins(vistos)
    assert len(fins) == 1 and fins[0][2] == pytest.approx(3.1, abs=0.3)  # as duas partes chegam juntas, em uma fala só


def test_cortador_ignora_silencio_ruido_constante_e_estalos():
    assert _cortar(_ruido(20, 0.001))[0] == []
    assert _cortar(_ruido(60, 0.02))[0] == []  # ventilador: o primeiro segundo mede a sala
    estalo = _montar(1.5, _fala(0.09, volume=0.3), 3.0)
    assert _cortar(estalo)[0] == []


def test_cortador_ouve_a_fala_por_cima_do_ventilador_e_a_fala_baixa_na_sala_quieta():
    com_ventilador = _montar(2.0, _fala(1.5, volume=0.1), 2.0, _fala(1.0, volume=0.1), 2.0, fundo=0.012)
    assert len(_fins(_cortar(com_ventilador)[0])) == 2
    baixinho = _montar(1.5, _fala(1.5, volume=0.012), 2.0, fundo=0.0008)
    assert len(_fins(_cortar(baixinho)[0])) == 1


def test_fala_comprida_e_anunciada_na_janela_e_aceita_segue_ate_o_fim_do_pedido():
    sinal = _montar(1.5, _fala(6.0), 2.5)  # "Jarvis, abre o YouTube e procura…": seis segundos
    vistos, _ = _cortar(sinal, resposta="aceitar")
    (comeco, quando, tamanho, _), fim = vistos
    assert comeco == COMECO and quando == pytest.approx(1.5 + JANELA - 0.3, abs=0.25)  # avisou sem esperar a fala acabar
    assert tamanho == pytest.approx(JANELA, abs=0.1)
    assert fim[0] == FIM and fim[3] is True  # conferido: passou pelo aceitar
    assert fim[2] == pytest.approx(6.5, abs=0.3) and fim[1] == pytest.approx(1.5 + 6.0 + 1.0, abs=0.2)


def test_fala_comprida_dispensada_some_e_a_proxima_e_ouvida():
    sinal = _montar(1.5, _fala(6.0), 2.0, _fala(1.5), 2.0)  # uma conversa da sala e, depois, uma fala curta
    vistos, cortador = _cortar(sinal, resposta="dispensar")
    assert [v[0] for v in vistos] == [COMECO, FIM]  # da dispensada, nenhum fim; da seguinte, o fim normal
    assert vistos[1][2] == pytest.approx(2.0, abs=0.3) and not vistos[1][3]
    assert cortador.limiar == pytest.approx(0.004, abs=0.001)  # conversa que acaba não muda o fundo da sala


def test_fala_anunciada_sem_resposta_e_tratada_como_dispensada():
    vistos, cortador = _cortar(_montar(1.5, _fala(8.0), 2.0), resposta=None)
    assert [v[0] for v in vistos] == [COMECO] and not cortador.em_fala  # não acumula som à espera de quem não responde


def test_pedido_tem_teto_de_duracao():
    sinal = _montar(1.5, _fala(40.0))  # depois do nome, a sala não se cala mais
    vistos, cortador = _cortar(sinal, resposta="aceitar", pedido_max=8.0)
    assert vistos[1][0] == FIM and vistos[1][3] is True
    assert vistos[1][2] == pytest.approx(8.0, abs=0.1) and vistos[1][1] == pytest.approx(1.5 + 8.0, abs=0.4)
    assert not cortador.em_fala or len(vistos) > 2  # o resto vira outra fala, a conferir de novo


def test_no_pedido_silencio_e_o_que_fica_bem_abaixo_da_voz_de_quem_pediu():
    """Sala quieta, a pessoa pede, e logo depois uma conversa mais baixa segue ao fundo: o pedido acaba com a voz dela."""
    sinal = _montar(1.5, _fala(4.0, volume=0.08), _ruido(12, 0.008, semente=5))
    vistos, _ = _cortar(sinal, resposta="aceitar")
    fim = _fins(vistos)[0]
    assert fim[3] is True and fim[2] == pytest.approx(4.5, abs=0.4)  # só a fala dela, não os doze segundos seguintes
    assert fim[1] == pytest.approx(1.5 + 4.0 + 1.0, abs=0.3)


def test_nome_dito_com_enfase_nao_faz_o_resto_do_pedido_passar_por_silencio():
    """"JARVIS! …abre o YouTube e procura uma receita": o nome sai quatro vezes mais alto que o pedido."""
    sinal = _montar(1.5, _fala(1.0, volume=0.32), 0.3, _fala(6.0, volume=0.08), 2.5)
    vistos, _ = _cortar(sinal, resposta="aceitar")
    fim = _fins(vistos)[0]
    assert fim[3] is True and fim[2] == pytest.approx(1.0 + 0.3 + 6.0 + 0.8, abs=0.4)  # o pedido inteiro
    assert fim[1] == pytest.approx(1.5 + 7.3 + 1.0, abs=0.3)


def test_aceito_na_pausa_depois_do_nome_o_volume_de_quem_pede_nao_e_o_da_sala():
    """ "Jarvis" … e o pedido, com um som de fundo que sobe e desce. O volume de quem pede é medido na fala, não na pausa."""
    quadro = int(0.03 * TAXA)

    def fundo(segundos, ligados, de):
        """Ruído baixo com picos seis vezes mais baixos que a voz: `ligados` quadros com pico a cada `de`."""
        som = _ruido(segundos, 0.001, semente=5)
        for i in range(0, len(som) - quadro, quadro):
            if (i // quadro) % de < ligados:
                som[i : i + quadro] += _ruido(0.03, 0.006, semente=i)
        return som

    # Na pausa depois do nome os picos são curtos (não chegam a interromper o silêncio); depois do pedido, mais compridos.
    nome_e_pausa = _montar(1.5, _fala(0.3), 0.6, fundo=0.0)
    pedido_e_resto = _montar(_fala(3.0), 5.0, fundo=0.0)
    sinal = np.concatenate([nome_e_pausa + fundo(len(nome_e_pausa) / TAXA, 2, 3), pedido_e_resto + fundo(len(pedido_e_resto) / TAXA, 4, 12)])
    vistos, _ = _cortar(sinal, na_pausa="aceitar")
    fins = _fins(vistos)
    assert len(fins) == 1 and fins[0][3] is True
    assert fins[0][1] == pytest.approx(1.5 + 0.3 + 0.6 + 3.0 + 1.0, abs=0.3)  # acabou um segundo depois do pedido, não no teto


def test_digitar_nao_ensurdece():
    """Teclas são estalos de um ou dois quadros, vários por segundo e altos. Não podem virar "o fundo da sala"."""
    teclas = np.zeros(int(30 * TAXA), dtype=np.float32)
    for inicio in np.arange(0.2, 30, 0.2):  # cinco teclas por segundo
        a = int(inicio * TAXA)
        teclas[a : a + int(0.02 * TAXA)] = 0.06
    fala = np.zeros(len(teclas), dtype=np.float32)
    a = int(20 * TAXA)
    fala[a : a + int(1.5 * TAXA)] = _fala(1.5, volume=0.08)
    vistos, cortador = _cortar(_montar(teclas + fala), resposta="dispensar")
    fins = _fins(vistos)
    assert cortador.limiar < 0.01  # as teclas não entraram na conta da sala
    assert len(fins) == 1 and fins[0][1] == pytest.approx(20 + 1.5 + 1.0, abs=0.4)  # e o chamado no meio da digitação saiu


def test_chamado_por_cima_do_comeco_de_uma_fala_da_tv_e_conferido_a_parte():
    """A TV começa a falar e, um segundo depois, alguém chama bem mais alto: o que veio antes é conferido já, e a voz nova vira outra fala."""
    tv = np.concatenate([np.zeros(int(2.0 * TAXA), dtype=np.float32), _fala(8.0, volume=0.02)])
    chamado = np.zeros(len(tv), dtype=np.float32)
    a = int(3.0 * TAXA)
    chamado[a : a + int(1.5 * TAXA)] = _fala(1.5, volume=0.2)
    vistos, _ = _cortar(_montar(tv + chamado, 2.0), resposta="dispensar")
    primeiro, segundo = vistos[0], vistos[1]
    assert primeiro[0] == COMECO and primeiro[1] == pytest.approx(3.1, abs=0.2)  # conferência antecipada, na entrada da voz
    assert primeiro[2] == pytest.approx(1.3, abs=0.3)  # só com o que veio antes dela
    assert segundo[0] == FIM and not segundo[3]  # a voz nova, sozinha, entregue quando acaba
    assert segundo[1] == pytest.approx(3.0 + 1.5 + 1.0, abs=0.4) and segundo[2] == pytest.approx(2.3, abs=0.4)
    assert len(vistos) == 2  # e a fala da TV, já dispensada, não é conferida de novo
    # A fala nova leva junto o som de logo antes da voz: a primeira sílaba do nome não pode ficar de fora.
    cortador, falas = Cortador(), []
    sinal = _montar(tv + chamado, 2.0)
    for i in range(0, len(sinal), 800):
        for tipo, som, *_ in cortador.alimentar(sinal[i : i + 800]):
            if tipo == COMECO:
                cortador.dispensar()
            else:
                falas.append(som)
    comeco = falas[0][: int(0.15 * TAXA)]
    assert float(np.sqrt(np.mean(comeco**2))) < 0.05  # começa ainda no volume da TV, antes de a voz entrar


def test_voz_que_sobe_no_meio_de_um_chamado_nao_o_parte():
    """"Jarvis…" baixo e o pedido bem mais alto: conferido antes da hora, aceito, e tudo segue como um pedido só."""
    sinal = _montar(1.5, _fala(0.7, volume=0.03), 0.2, _fala(3.0, volume=0.2), 2.5)
    vistos, _ = _cortar(sinal, resposta="aceitar")
    assert vistos[0][0] == COMECO and vistos[0][1] < 1.5 + JANELA - 0.5  # a conferência veio cedo, com o nome
    fins = _fins(vistos)
    assert len(fins) == 1 and fins[0][3] is True
    assert fins[0][2] == pytest.approx(0.3 + 0.7 + 0.2 + 3.0 + 0.5, abs=0.4)  # nome e pedido juntos, do começo ao fim


def test_estalos_depois_da_fala_nao_seguram_o_fim():
    estalos = np.zeros(int(8 * TAXA), dtype=np.float32)
    for inicio in np.arange(0.3, 8, 0.9):  # um clique de 20 ms a cada 0,9 s: teclado, mouse
        a = int(inicio * TAXA)
        estalos[a : a + int(0.02 * TAXA)] = 0.05
    sinal = _montar(1.5, _fala(1.5), estalos)
    vistos, _ = _cortar(sinal)
    assert len(vistos) == 1 and vistos[0][1] == pytest.approx(1.5 + 1.5 + 1.0, abs=0.3)  # um segundo depois da fala, não oito


def test_som_de_fundo_que_nao_para_vira_o_fundo_da_sala():
    sinal = np.concatenate([_ruido(3, 0.002), _tv(60, 0.01, 0.04)])  # alguém ligou uma música
    vistos, cortador = _cortar(sinal, resposta="dispensar")
    assert [v[0] for v in vistos] == [COMECO]  # conferida uma vez; depois é fundo, sem gastar transcrição
    assert not cortador.em_fala and cortador.limiar > 0.05  # o limiar ficou acima dos picos da música
    # Com a música tocando, uma voz mais alta chama; a música sozinha, não.
    mais, _ = _cortar(_tv(4, 0.01, 0.04, semente=4) + np.concatenate([np.zeros(int(1.0 * TAXA)), _fala(1.5, volume=0.2), np.zeros(int(1.5 * TAXA))]).astype(np.float32), cortador=cortador)
    assert [v[0] for v in mais] == [FIM] and mais[0][2] == pytest.approx(2.0, abs=0.4)
    assert _cortar(_tv(20, 0.01, 0.04, semente=6), cortador=cortador)[0] == []


def test_tv_baixa_ao_fundo_nao_engole_o_pedido():
    """O fundo oscila o tempo todo (TV baixa) e alguém pede treze vezes mais alto: o pedido sai na hora, sozinho."""
    tv = _tv(40, 0.002, 0.006)
    fala = np.zeros(len(tv), dtype=np.float32)
    inicio = int(20 * TAXA)
    fala[inicio : inicio + int(2.0 * TAXA)] = _fala(2.0, volume=0.06)
    vistos, _ = _cortar(tv + fala, resposta="dispensar")
    fins = _fins(vistos)
    assert len(fins) == 1 and not fins[0][3]
    assert fins[0][1] == pytest.approx(20 + 2.0 + 1.0, abs=0.4)  # um segundo depois de a pessoa se calar, não no teto
    assert fins[0][2] == pytest.approx(2.5, abs=0.5)  # e só com a fala dela dentro
    assert len(vistos) <= 3  # a TV não fica gerando falas para transcrever


def test_dentro_de_uma_fala_dispensada_uma_voz_mais_alta_comeca_outra():
    """A sala conversa (já conferido: não é chamado) e, por cima, alguém chama mais alto."""
    conversa = _fala(12.0, volume=0.03)
    chamado = np.zeros(len(conversa), dtype=np.float32)
    inicio = int(5 * TAXA)
    chamado[inicio : inicio + int(1.5 * TAXA)] = _fala(1.5, volume=0.2)
    vistos, cortador = _cortar(_montar(1.5, conversa + chamado, 2.0), resposta="dispensar")
    assert vistos[0][0] == COMECO and vistos[0][1] == pytest.approx(1.5 + JANELA - 0.3, abs=0.25)
    # A voz mais alta virou uma fala própria, que acaba quando o volume volta ao da conversa, e não com a conversa.
    assert vistos[1][0] == FIM and not vistos[1][3]
    assert vistos[1][1] == pytest.approx(1.5 + 5.0 + 1.5 + 1.0, abs=0.4) and vistos[1][2] == pytest.approx(2.0, abs=0.4)
    assert len(vistos) == 2  # e a conversa, já dispensada, não é conferida de novo
    assert cortador.limiar == pytest.approx(0.004, abs=0.001)  # nem vira fundo: ela acabou sozinha


def test_ventilador_que_liga_no_meio_nao_estica_o_pedido():
    """O fundo sobe de repente e fica (ventilador). Um chamado logo depois sai inteiro e só ele; em segundos o ventilador é fundo."""
    ventilador = np.concatenate([_ruido(3, 0.001), _ruido(40, 0.02, semente=2)])
    fala = np.zeros(len(ventilador), dtype=np.float32)
    for quando in (8, 30):
        a = int(quando * TAXA)
        fala[a : a + int(1.5 * TAXA)] = _fala(1.5, volume=0.1)
    vistos, cortador = _cortar(ventilador + fala, resposta="dispensar")
    fins = _fins(vistos)
    assert [f[1] for f in fins] == pytest.approx([8 + 1.5 + 1.0, 30 + 1.5 + 1.0], abs=0.4)  # cada um, um segundo depois de acabar
    assert all(f[2] < 2.6 for f in fins)  # sem segundos de ventilador junto
    assert len(vistos) <= 4 and 0.03 < cortador.limiar < 0.06  # o ventilador custou no máximo duas olhadas e virou fundo


def test_som_por_cima_do_fundo_que_tambem_nao_chama_junta_se_ao_fundo():
    """A música já dispensada fica três vezes mais alta de vez: não pode virar uma fala nova a cada instante."""
    sinal = np.concatenate([_ruido(2, 0.001), _ruido(5, 0.01, semente=2), _ruido(40, 0.04, semente=3)])
    vistos, cortador = _cortar(sinal, resposta="dispensar")
    assert len(vistos) <= 4 and all(v[0] == COMECO for v in vistos)  # poucas olhadas até se acostumar
    assert not cortador.em_fala and cortador.limiar > 0.06


def test_cortador_funciona_em_outra_taxa_e_com_blocos_quebrados():
    taxa = 48_000
    sinal = _montar(1.5, _fala(1.2, taxa=taxa), 2.0, taxa=taxa)
    for bloco in (0.01, 0.0213, 0.2):
        vistos, _ = _cortar(sinal, taxa=taxa, bloco=bloco)
        assert len(vistos) == 1, bloco
        assert vistos[0][2] == pytest.approx(2.0, abs=0.2)


def test_zerar_esquece_a_fala_mas_nao_a_sala():
    cortador = Cortador()
    cortador.alimentar(_montar(1.5, _fala(1.0)))
    assert cortador.em_fala
    cortador.zerar()
    assert not cortador.em_fala
    # Reaberto o microfone, não há outro segundo de medição: uma fala logo em seguida é ouvida inteira.
    vistos, _ = _cortar(_montar(0.2, _fala(1.2), 1.5), cortador=cortador)
    assert len(vistos) == 1 and vistos[0][2] == pytest.approx(2.0, abs=0.2)
    assert 0.0 <= cortador.nivel <= 1.0


# --- a vigília --------------------------------------------------------------------------------------


class Relogio:
    def __init__(self):
        self.agora = 100.0

    def __call__(self):
        return self.agora


class Ouvido:
    """Transcritor falso: devolve as respostas na ordem e anota o que recebeu."""

    def __init__(self, *respostas):
        self.respostas, self.chamadas, self.dicas = list(respostas), [], []

    def __call__(self, audio, rapido=False, dica=""):
        self.chamadas.append((len(audio), rapido))
        self.dicas.append(dica)
        return self.respostas.pop(0)


def _vigia(*respostas, **opcoes):
    eventos = []
    relogio = Relogio()
    ouvido = Ouvido(*respostas)
    vigia = Vigia(
        ouvido,
        lambda texto: eventos.append(("pedido", texto)),
        ao_acordar=lambda: eventos.append(("acordou",)),
        ao_atender=lambda: eventos.append(("atendeu",)),
        ao_desistir=lambda: eventos.append(("desistiu",)),
        ao_sobrepor=lambda: eventos.append(("calou",)),
        ao_engano=lambda: eventos.append(("engano",)),
        relogio=relogio,
        **opcoes,
    )
    vigia.atenta = True
    return vigia, eventos, ouvido, relogio


def _som(segundos):
    return np.zeros(int(segundos * TAXA), dtype=np.float32)


def test_vigia_pedido_curto_e_transcrito_uma_vez_so_com_capricho():
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube.")
    vigia.tratar(_som(2))
    assert eventos == [("pedido", "abre o YouTube")]
    assert ouvido.chamadas == [(2 * TAXA, False)]  # uma transcrição, não duas: metade da espera
    assert ouvido.dicas == ["Jarvis"]  # e ela sabe como o nome se escreve
    assert vigia.atenta is True  # segue ouvindo: é assim que "Jarvis, para" corta a resposta
    assert len(vigia.tempos) == 1  # quanto levou fica anotado, para o /ouvido


def test_vigia_com_modelo_leve_ele_olha_primeiro_e_o_capricho_confirma():
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube", "Jarvis, abre o YouTube.", leve=True)
    vigia.tratar(_som(2))
    assert eventos == [("pedido", "abre o YouTube")]
    assert ouvido.chamadas == [(2 * TAXA, True), (2 * TAXA, False)]
    vigia, eventos, ouvido, _ = _vigia("Jarvis.", leve=True)
    vigia.tratar(_som(1))  # só o nome: a palavra do modelo leve basta, para a resposta sair logo
    assert eventos == [("acordou",)] and ouvido.chamadas == [(1 * TAXA, True)]


def test_vigia_pedido_ja_conferido_vai_direto_para_a_transcricao_caprichada():
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube e toca lo-fi.", leve=True)
    vigia.tratar(_som(5), conferido=True)
    assert eventos == [("pedido", "abre o YouTube e toca lo-fi")] and ouvido.chamadas == [(5 * TAXA, False)]


def test_vigia_so_o_nome_e_depois_o_pedido():
    vigia, eventos, ouvido, _ = _vigia("Jarvis.", "abre o YouTube")
    vigia.tratar(_som(1))
    assert eventos == [("acordou",)] and vigia.esperando_pedido
    vigia.tratar(_som(2))
    assert eventos == [("acordou",), ("pedido", "abre o YouTube")]
    assert ouvido.chamadas[-1] == (2 * TAXA, False)  # o pedido vai inteiro para a transcrição caprichada
    assert not vigia.esperando_pedido


def test_vigia_ignora_conversa_que_nao_chama_e_guarda_so_na_memoria():
    vigia, eventos, _, _ = _vigia("vamos almoçar hoje", "", "eu acho o Jarvis legal")
    for _ in range(3):
        vigia.tratar(_som(2))
    assert eventos == []
    assert list(vigia.ouvidos) == ["vamos almoçar hoje", "eu acho o Jarvis legal"]
    vigia, _, ouvido, _ = _vigia("vamos almoçar hoje", leve=True)
    vigia.tratar(_som(2))
    assert ouvido.chamadas == [(2 * TAXA, True)]  # com modelo leve, o que não chama nem gasta a transcrição caprichada


def test_vigia_nome_repetido_e_barulho_enquanto_espera_o_pedido():
    vigia, eventos, _, relogio = _vigia("Jarvis", "", "Jarvis!", "Jarvis, que horas são?")
    vigia.tratar(_som(1))  # acordou
    vigia.tratar(_som(1))  # barulho: continua esperando
    assert vigia.esperando_pedido
    relogio.agora += 3
    vigia.tratar(_som(1))  # chamou de novo: o prazo recomeça
    assert vigia.esperando_pedido and vigia._espera_ate == relogio.agora + ESPERA_DO_PEDIDO
    vigia.tratar(_som(2))  # agora com o pedido junto
    assert eventos == [("acordou",), ("pedido", "que horas são")]


def test_vigia_so_age_se_a_transcricao_caprichada_confirmar_o_nome():
    """A primeira olhada é rápida e erra mais. Se a caprichada não acha o nome, nada vai para a conversa."""
    vigia, eventos, _, _ = _vigia("Jarvis, abre", "Já vi, abre o YouTube agora", leve=True)
    vigia.tratar(_som(2))
    assert eventos == [] and list(vigia.ouvidos) == ["Já vi, abre o YouTube agora"]
    vigia, eventos, _, _ = _vigia("")  # idem para o que foi conferido no começo e não se confirma no todo
    vigia.tratar(_som(6), conferido=True)
    assert eventos == [] and not vigia.esperando_pedido


def test_vigia_palavra_escolhida():
    vigia, eventos, ouvido, _ = _vigia("Sexta-feira, pausa o vídeo", palavras=ler_palavras("sexta-feira;jarvis"))
    vigia.tratar(_som(2))
    assert eventos == [("pedido", "pausa o vídeo")] and ouvido.dicas == ["Sexta Feira, Jarvis"]


def test_vigia_comeco_que_parecia_chamado_e_nao_se_confirma_avisa_que_desistiu():
    """Quem foi avisado de que ele ouviu o nome (e calou a fala, e mostrou "ouvindo") precisa saber que não deu em nada."""
    vigia, eventos, _, _ = _vigia("Jarvis, abre", "Já vi, abre o YouTube agora", "vamos almoçar")
    vigia._acontecimento(COMECO, _som(3))
    assert eventos == [("acordou",)]
    vigia._acontecimento(FIM, _som(6), True, 150)
    assert eventos == [("acordou",), ("desistiu",)] and not vigia.esperando_pedido
    vigia._acontecimento(FIM, _som(2), False, 40)  # fala comum que não chama: ninguém tinha sido avisado, nada a desfazer
    assert eventos == [("acordou",), ("desistiu",)]


def test_vigia_o_que_se_viu_na_pausa_de_uma_fala_nao_vale_para_outra():
    """Uma fala curta dispensada e, depois, um chamado comprido com o mesmo tanto de fala: o pedido não some."""
    vigia, eventos, _, _ = _vigia("vamos almoçar", "Jarvis, abre o YouTube e", "Jarvis, abre o YouTube e procura um vídeo")
    vigia._acontecimento(PAUSA, _som(2), 150)  # não chamava: dispensada na pausa
    vigia._acontecimento(COMECO, _som(3))
    vigia._acontecimento(FIM, _som(6), True, 150)  # por acaso, o mesmo número de quadros de fala
    assert eventos == [("acordou",), ("pedido", "abre o YouTube e procura um vídeo")]


class FluxoFalso:
    def __init__(self, sd, samplerate, callback):
        self.sd, self.taxa, self.callback, self.estado = sd, samplerate, callback, "criado"
        sd.fluxos.append(self)

    def start(self):
        self.estado = "aberto"

    def stop(self):
        self.estado = "parado"

    def abort(self):
        self.estado = "abortado"

    def close(self):
        self.estado = "abortado e fechado" if self.estado == "abortado" else "fechado"


class SomFalso:
    """sounddevice falso: guarda os fluxos abertos; `recusa` é a taxa que o microfone não aceita."""

    class PortAudioError(Exception):
        pass

    def __init__(self, recusa=None):
        self.fluxos, self.recusa, self.quebrado, self.releituras = [], recusa, False, 0

    def _terminate(self):
        self.releituras += 0.5

    def _initialize(self):
        self.releituras += 0.5
        if self.quebrado == "até reler":
            self.quebrado = False  # o microfone novo aparece na lista

    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802
        if self.quebrado:
            raise OSError("microfone desconectado")
        if samplerate == self.recusa:
            raise self.PortAudioError("taxa não aceita")
        return FluxoFalso(self, samplerate, callback)

    def query_devices(self, kind):
        return {"default_samplerate": 48000.0}

    @property
    def abertos(self):
        return [f for f in self.fluxos if f.estado == "aberto"]


def _tocar(vigia, sinal, taxa=TAXA, bloco=0.05, tocado=None, relogio=None, desde=0.0):
    """Entrega o som à vigília como o microfone faria, mas na mesma thread (sem esperar).

    `tocado(segundos)`: o volume do que o Jarvis toca em cada instante. Com `relogio`, o tempo anda junto com o som.
    """
    passo = int(bloco * taxa)
    for i in range(0, len(sinal), passo):
        if relogio is not None:
            relogio.agora += bloco
        vigia._bloco(True, tocado(desde + i / taxa) if tocado else 0.0, sinal[i : i + passo].reshape(-1, 1))


def test_vigia_do_microfone_ao_pedido():
    sd = SomFalso()
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube", sd=sd)
    vigia.iniciar()
    try:
        assert len(sd.abertos) == 1 and vigia.aberta
        _tocar(vigia, _montar(1.5, _fala(2.0), 0.6))
        # Meio segundo depois de a fala parar o nome já foi ouvido: é a hora de ele se calar, se estiver falando.
        assert eventos == [("acordou",)] and len(ouvido.chamadas) == 1
        _tocar(vigia, _montar(1.4))
        assert eventos == [("acordou",), ("pedido", "abre o YouTube")]
        assert len(ouvido.chamadas) == 1  # nada foi dito depois da pausa: o que foi transcrito nela ainda vale
    finally:
        vigia.parar()
    assert sd.abertos == [] and not vigia.aberta


def test_vigia_frase_que_continua_depois_da_pausa_e_transcrita_de_novo_no_fim():
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube", "Jarvis, abre o YouTube e toca lo-fi", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(1.2), 0.6, _fala(1.0), 2.0))  # "Jarvis, abre o YouTube… e toca lo-fi"
        assert eventos == [("acordou",), ("pedido", "abre o YouTube e toca lo-fi")]
        assert [rapido for _, rapido in ouvido.chamadas] == [False, False]
    finally:
        vigia.parar()


def test_vigia_o_pedido_que_vem_depois_do_nome_acaba_pela_voz_de_quem_pede():
    """Com um som de fundo que sobe e desce, o fim do pedido é o fim da voz, não o primeiro segundo de sala quieta."""
    quadro = int(0.03 * TAXA)

    def fundo(segundos, ligados, de):
        som = _ruido(segundos, 0.001, semente=5)
        for i in range(0, len(som) - quadro, quadro):
            if (i // quadro) % de < ligados:
                som[i : i + quadro] += _ruido(0.03, 0.006, semente=i)
        return som

    vigia, eventos, ouvido, _ = _vigia("Jarvis", "abre", "abre o site", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.2))
        assert eventos == [("acordou",), ("atendeu",)]
        pedido = _montar(_fala(0.4), 0.6, _fala(0.6), 4.0, fundo=0.0)  # "abre… o site", e logo depois um som de fundo começa
        _tocar(vigia, pedido + np.concatenate([_ruido(1.6, 0.001, semente=5), fundo(len(pedido) / TAXA, 4, 12)])[: len(pedido)])
        assert eventos[2:] == [("pedido", "abre o site")]
        # O pedido inteiro e nada da sala depois: 0,3 s de antes, 1,6 s de fala e pausa, meio segundo de cauda.
        assert ouvido.chamadas[-1][0] == pytest.approx((0.3 + 0.4 + 0.6 + 0.6 + 0.5) * TAXA, rel=0.1)
    finally:
        vigia.parar()


def test_vigia_so_o_nome_e_ele_responde_em_meio_segundo():
    vigia, eventos, ouvido, _ = _vigia("Jarvis", "que horas são?", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 0.6))
        assert eventos == [("acordou",), ("atendeu",)]  # não esperou o segundo inteiro de silêncio
        assert vigia.esperando_pedido and not vigia._cortador.em_fala
        _tocar(vigia, _montar(0.4, _fala(1.2), 2.0))
        assert eventos[2:] == [("pedido", "que horas são?")]
    finally:
        vigia.parar()


def _chegar(vigia, sinal, relogio, bloco=0.05):
    """O som chega do microfone e fica na fila, como enquanto uma transcrição segura a thread da vigília."""
    passo = int(bloco * TAXA)
    for i in range(0, len(sinal), passo):
        relogio.agora += bloco
        vigia._receber(sinal[i : i + passo].reshape(-1, 1), passo, None, None)


def _tratar_fila(vigia, ate=lambda: False):
    while not vigia._fila.empty() and not ate():
        vigia._bloco(*vigia._fila.get())


def _vigia_com_fila(*respostas, **opcoes):
    """Uma vigília sem a thread dela: os pedaços que chegam esperam na fila até o teste tratá-los."""
    vigia, eventos, ouvido, relogio = _vigia(*respostas, sd=SomFalso(), **opcoes)
    vigia.iniciar()
    vigia.parar()
    vigia._abrir()
    _chegar(vigia, _montar(1.5), relogio)  # a sala, para ela saber o que é silêncio
    _tratar_fila(vigia)
    return vigia, eventos, ouvido, relogio


def test_vigia_quem_emenda_o_pedido_no_nome_nao_e_interrompido_por_um_sim():
    """A transcrição do nome demorou e a pessoa já está falando o pedido: não há o que responder."""
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "abre o YouTube")
    try:
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(0.8)), relogio)  # o nome, a pausa e o começo do pedido
        _tratar_fila(vigia)
        assert eventos == [("acordou",)]  # ouviu o nome com a pessoa falando: fica calado
        _chegar(vigia, _montar(_fala(0.7), 2.0), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("pedido", "abre o YouTube")]
    finally:
        vigia._fechar()


def test_vigia_responde_ao_nome_com_som_em_atraso_na_fila_se_o_microfone_esta_calado():
    """Quem não ouve resposta chama de novo. O segundo chamado, ainda na fila, não pode segurar a resposta ao primeiro."""
    vigia, eventos, ouvido, relogio = _vigia_com_fila("Jarvis", "Jarvis")
    try:
        # Enquanto o primeiro "Jarvis" era transcrito, a pessoa esperou, chamou de novo e se calou.
        _chegar(vigia, _montar(_fala(0.6), 1.6, _fala(0.6), 1.2), relogio)
        _tratar_fila(vigia, ate=lambda: bool(eventos))
        assert eventos == [("acordou",), ("atendeu",)]  # respondeu na hora em que entendeu o nome
        assert not vigia._fila.empty()  # com o segundo chamado ainda por tratar
        relogio.agora += 3.0  # e tratá-lo leva tempo: quando termina, a resposta já saiu há segundos
        _tratar_fila(vigia)
        # O chamado repetido não é um pedido nem pede outra resposta: ele segue à espera do pedido.
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
        assert len(ouvido.chamadas) == 2
    finally:
        vigia._fechar()


def test_vigia_com_som_em_atraso_nao_responde_por_cima_de_quem_fala_e_responde_quando_ela_se_cala():
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "Jarvis")
    try:
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(0.5)), relogio)  # o nome, e a pessoa chamando de novo neste instante
        _tratar_fila(vigia)
        assert eventos == [("acordou",)]
        _chegar(vigia, _montar(_fala(0.1), 1.6), relogio)  # ela se cala; o segundo chamado era só o nome de novo
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
    finally:
        vigia._fechar()


def test_vigia_quem_chama_de_novo_depois_da_resposta_e_respondido_de_novo():
    """Chamou outra vez depois do "Sim?": não escutou. Ele responde de novo e segue à espera do pedido."""
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "Jarvis", "que horas são?")
    try:
        _chegar(vigia, _montar(_fala(0.6), 1.5), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",)]
        _chegar(vigia, _montar(_fala(0.6), 1.5), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",), ("atendeu",)] and vigia.esperando_pedido
        _chegar(vigia, _montar(_fala(1.2), 1.5), relogio)
        _tratar_fila(vigia)
        assert eventos[3:] == [("pedido", "que horas são?")]
    finally:
        vigia._fechar()


def test_vigia_com_som_em_atraso_espera_tres_decimos_de_microfone_calado_para_responder():
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis")
    try:
        # O nome, e alguma coisa dita enquanto ele era transcrito; dois décimos de segundo de silêncio até agora.
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(0.5), 0.2), relogio)
        _tratar_fila(vigia, ate=lambda: bool(eventos))
        assert eventos == [("acordou",)] and not vigia._fila.empty()  # ainda é cedo: ela acabou de falar
        _chegar(vigia, _montar(0.15), relogio)
        vigia._bloco(*vigia._fila.get())
        assert eventos == [("acordou",), ("atendeu",)] and not vigia._fila.empty()  # calada há mais de três décimos
    finally:
        vigia._fechar()


def test_vigia_pausa_no_meio_do_pedido_emendado_nao_faz_ele_responder():
    """A pessoa emendou o pedido no nome e parou para pensar: o silêncio dela não é hora de dizer "Sim?"."""
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "abre o YouTube", "abre o YouTube e toca lo-fi")
    try:
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(0.5)), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",)]
        _chegar(vigia, _montar(0.6), relogio)  # pensando
        _tratar_fila(vigia)
        assert eventos == [("acordou",)] and vigia._cortador.em_fala
        _chegar(vigia, _montar(_fala(0.8), 1.6), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("pedido", "abre o YouTube e toca lo-fi")]
    finally:
        vigia._fechar()


def test_vigia_barulho_no_lugar_do_pedido_nao_deixa_o_nome_sem_resposta():
    """Ouviu o nome com a pessoa fazendo barulho (uma tosse) e por isso não respondeu. Era só barulho: responde."""
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "")
    try:
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(0.5)), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",)]
        _chegar(vigia, _montar(1.6), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
    finally:
        vigia._fechar()


def test_vigia_barulho_depois_da_resposta_nao_faz_responder_de_novo():
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "")
    try:
        _chegar(vigia, _montar(_fala(0.6), 1.5), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",)]
        vigia._respondeu_em = relogio.agora + 5.0  # a resposta saiu depois deste barulho ser captado (ele esperava na fila)
        _chegar(vigia, _montar(_fala(0.5), 1.6), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
    finally:
        vigia._fechar()


def test_vigia_so_responde_de_novo_a_fala_que_terminou_depois_da_resposta():
    """Uma fala é entregue um segundo depois de terminar. Entregue até um segundo depois da resposta, é de antes dela."""
    vigia, _, _, _ = _vigia()
    vigia._chamado_em, vigia._respondeu_em = 99.5, 100.0
    for captado, falta in ((99.0, False), (100.5, False), (101.0, False), (101.05, True), (130.0, True)):
        vigia._atender, vigia._captado_em, vigia._mais_respostas = False, captado, 0
        vigia._falta_responder(repetiu_o_nome=True)
        assert vigia._atender is falta, captado
    # Barulho depois da resposta não faz responder de novo; antes da primeira resposta, faz.
    vigia._atender, vigia._captado_em, vigia._mais_respostas = False, 130.0, 0
    vigia._falta_responder(repetiu_o_nome=False)
    assert vigia._atender is False
    vigia._chamado_em = 120.0  # chamado novo, ainda sem resposta
    vigia._falta_responder(repetiu_o_nome=False)
    assert vigia._atender is True
    # Responde de novo uma vez só por chamado.
    vigia._chamado_em, vigia._mais_respostas = 99.5, 0
    for vez, falta in ((1, True), (2, False), (3, False)):
        vigia._atender = False
        vigia._falta_responder(repetiu_o_nome=True)
        assert vigia._atender is falta, vez


def _com_resposta_tocando(*respostas, ultima="Pois não?"):
    """Vigília que acabou de responder ao nome: o que ele disse por último foi a resposta, que acabou agora."""
    ditas, falou = [ultima], [float("inf")]
    vigia, eventos, ouvido, relogio = _vigia(*respostas, ditas=lambda: list(ditas), falou_ha=lambda: falou[0], proprias=("Sim?", "Pois não?", "Diga."))
    return vigia, eventos, ouvido, relogio, ditas, falou


def test_vigia_nao_responde_a_propria_resposta_escrita_como_o_nome():
    """O bug de campo: chamado uma vez, ele respondia "Pois não?", ouvia isso de volta escrito como "Jarvis",
    respondia "Sim?", e assim sem parar."""
    vigia, eventos, ouvido, relogio, ditas, falou = _com_resposta_tocando("Jarvis", "Jarvis", "Jarvis", "Jarvis", "Jarvis")
    vigia.tratar(_som(1))
    assert eventos == [("acordou",)] and vigia._atender
    vigia._atender, vigia._respondeu_em, vigia._eco_da_resposta = False, relogio.agora, True
    eventos.append(("atendeu",))
    # A resposta tocou por meio segundo e o eco dela foi entregue como fala um segundo depois de acabar.
    relogio.agora += 0.5
    falou[0] = 0.0
    relogio.agora += 1.0 + 0.6
    falou[0] = 1.6
    vigia._captado_em = relogio.agora
    for _ in range(3):  # a primeira, a seguinte, e outra mais: nenhuma vira resposta nova
        vigia.tratar(_som(0.3 + 0.6 + 0.5))
        assert eventos == [("acordou",), ("atendeu",)] and not vigia._atender and vigia.esperando_pedido
    # Fora da espera (o prazo do pedido venceu), o mesmo eco também não acorda.
    vigia._espera_ate, eventos[:] = 0.0, []
    vigia.tratar(_som(1.4))
    assert ("acordou",) not in eventos


def test_vigia_pessoa_que_chama_de_novo_depois_do_eco_e_respondida_uma_vez_mais():
    vigia, eventos, _, relogio, ditas, falou = _com_resposta_tocando("Jarvis", "Jarvis", "Jarvis", "que horas são?")
    vigia.tratar(_som(1))
    vigia._atender, vigia._respondeu_em = False, relogio.agora
    relogio.agora += 4.0  # bem depois de a resposta acabar
    falou[0] = 3.5
    vigia._captado_em = relogio.agora
    vigia.tratar(_som(1.4))  # a pessoa repetiu o nome: não ouviu
    assert vigia._atender
    vigia._atender, vigia._respondeu_em = False, relogio.agora
    ditas.append("Sim?")
    relogio.agora += 4.0
    falou[0] = 3.5
    vigia._captado_em = relogio.agora
    vigia.tratar(_som(1.4))  # de novo: ele já respondeu duas vezes a este chamado, agora espera o pedido calado
    assert not vigia._atender and vigia.esperando_pedido
    vigia.tratar(_som(1.4))
    assert eventos[-1] == ("pedido", "que horas são?")


def test_vigia_pedido_dito_logo_depois_da_resposta_continua_valendo():
    vigia, eventos, _, relogio, ditas, falou = _com_resposta_tocando("Jarvis", "que horas são?")
    vigia.tratar(_som(1))
    vigia._atender, vigia._respondeu_em = False, relogio.agora
    relogio.agora += 2.0
    falou[0] = 1.5
    vigia._captado_em = relogio.agora
    vigia.tratar(_som(1.4))  # começou colado na resposta, mas é um pedido, não o nome
    assert eventos[-1] == ("pedido", "que horas são?")


def test_vigia_eco_so_quando_o_ultimo_que_ele_disse_foi_resposta_ao_nome():
    vigia, _, _, relogio, ditas, falou = _com_resposta_tocando(ultima="O dólar está em cinco reais.")
    vigia._respondeu_em, vigia._captado_em = relogio.agora - 2.0, relogio.agora  # a fala começou 1,6 s antes de chegar
    falou[0] = 0.2
    assert not vigia._eco_da_propria_resposta(_som(1.4), 1.0)
    ditas.append("Sim?")
    assert vigia._eco_da_propria_resposta(_som(1.4), 1.0)
    falou[0] = 5.0  # a fala começou muito depois de ele se calar: é gente
    assert not vigia._eco_da_propria_resposta(_som(1.4), 1.0)
    vigia._cortador.sobre_ele = True  # começou com ele falando
    assert vigia._eco_da_propria_resposta(_som(1.4), 1.0)
    vigia._ditas = lambda: (_ for _ in ()).throw(RuntimeError("sem fala"))
    assert not vigia._eco_da_propria_resposta(_som(1.4), 1.0)


def test_vigia_o_microfone_de_agora_e_medido_pelo_limiar_da_sala():
    """Barulho abaixo do limiar da sala não é "alguém falando": a resposta ao nome não espera por ele."""
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis")
    try:
        assert vigia._limiar_de_agora == vigia._cortador.limiar
        _chegar(vigia, _montar(_fala(0.6), 0.6, _ruido(1.0, 0.0015)), relogio)
        _tratar_fila(vigia, ate=lambda: bool(eventos))
        assert eventos == [("acordou",), ("atendeu",)] and not vigia._fila.empty()
    finally:
        vigia._fechar()


def test_vigia_nao_responde_sim_a_quem_ja_esta_no_meio_do_pedido():
    vigia, eventos, _, relogio = _vigia_com_fila("Jarvis", "abre o YouTube e toca lo-fi")
    try:
        # A transcrição do nome ficou pronta com a pessoa um segundo dentro do pedido.
        _chegar(vigia, _montar(_fala(0.6), 0.6, _fala(1.0)), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",)] and vigia._cortador.em_fala  # nada de "Sim?" por cima dela
        _chegar(vigia, _montar(_fala(2.5), 2.0), relogio)
        _tratar_fila(vigia)
        assert eventos == [("acordou",), ("pedido", "abre o YouTube e toca lo-fi")]
    finally:
        vigia._fechar()


def test_vigia_a_resposta_do_proprio_jarvis_voltando_pelo_microfone_nao_e_o_pedido():
    """A primeira coisa ouvida depois do "Sim?", se for igual a ele, é ele: por mais que a transcrição demore."""
    vigia, eventos, _, relogio = _vigia("Jarvis", "Sim?", "sim", sd=SomFalso(), proprias=("Sim?", "Pois não?"))
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 0.6))
        assert eventos == [("acordou",), ("atendeu",)]
        relogio.agora += 4  # máquina lenta: a transcrição do que veio depois levou segundos
        vigia.tratar(_som(0.5))  # o microfone ouviu a resposta dele
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
        vigia.tratar(_som(0.5))  # a seguinte, igual, já é a pessoa respondendo
        assert eventos[2:] == [("pedido", "sim")]
    finally:
        vigia.parar()


def test_vigia_chamado_repetido_com_a_resposta_dele_junto_nao_e_um_pedido():
    """Chamou de novo e o microfone pegou junto o "Pois não?" dele: é um chamado repetido, não o pedido "pois não"."""
    vigia, eventos, _, _ = _vigia("Jarvis", "Jarvis. Pois não?", "Jarvis, pois não vou", proprias=("Sim?", "Pois não?"))
    vigia.tratar(_som(1))
    assert eventos == [("acordou",)] and vigia.esperando_pedido
    vigia._eco_da_resposta = False  # a resposta já foi ouvida de volta uma vez; esta veio colada no chamado
    vigia.tratar(_som(1.5))
    assert eventos == [("acordou",)] and vigia.esperando_pedido
    vigia.tratar(_som(1.5))  # um pedido que só começa com as mesmas palavras é pedido
    assert eventos[1:] == [("pedido", "pois não vou")]


def _para_por_cima_dele(*respostas, imediato=lambda ordem: ordem == "para", depois=1.2):
    """Ele fala; alguém diz "Jarvis, para" bem alto por cima; ele se cala 0,4 s depois de a voz entrar."""
    vigia, eventos, ouvido, relogio = _vigia(*respostas, imediato=imediato)
    _tocar(vigia, _montar(1.5), relogio=relogio)
    antes, tocado_antes = _caixa(_fala(2.0, volume=0.15), 1.0)  # ele já falou: sabe-se com que volume chega ao microfone
    _tocar(vigia, antes, tocado=tocado_antes, relogio=relogio)
    dele, tocado = _caixa(_fala(1.4, volume=0.15), 3.0)
    microfone = _por_cima(dele, _fala(depois, volume=0.4), 1.0)
    _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
    return vigia, eventos, ouvido


def test_vigia_para_dito_por_cima_dele_vale_mesmo_com_o_nome_coberto_pela_voz_dele():
    """A transcrição errou o nome, dito junto com a voz dele. O "para", dito depois de ele se calar, chega limpo e basta."""
    vigia, eventos, ouvido = _para_por_cima_dele("Gravíssimo, para.", "Vis, para!")
    assert eventos == [("calou",), ("acordou",), ("pedido", "para")]
    inteira, limpa = ouvido.chamadas
    # A segunda transcrição é só do que veio depois de ele se calar: do fim do som dele (1,55 s, mais o resto na sala) ao fim da fala.
    assert limpa[0] / TAXA == pytest.approx((2.2 + 0.45) - (1.55 + 0.35), abs=0.15) and limpa[0] < inteira[0] / 2
    assert not vigia._cortador.em_fala and not vigia.esperando_pedido


def test_vigia_fala_por_cima_dele_sem_nome_e_sem_ordem_de_parar_e_engano():
    vigia, eventos, ouvido = _para_por_cima_dele("Gravíssimo, para.", "e a reunião de amanhã")
    assert eventos == [("calou",), ("engano",)] and len(ouvido.chamadas) == 2
    vigia, eventos, ouvido = _para_por_cima_dele("Gravíssimo, para.", "Vis, para!", imediato=lambda ordem: False)
    assert eventos == [("calou",), ("engano",)]  # "para" sem nada para parar não é ordem


def test_vigia_ordem_de_parar_por_cima_dele_e_o_fim_do_que_veio_limpo():
    vigia, _, ouvido, _ = _vigia(imediato=lambda ordem: ordem in ("para", "pode parar por favor"))
    som = _som(2.0)
    cortador = vigia._cortador
    cortador.sobre_ele, cortador.limpo_desde = True, TAXA  # ele se calou com um segundo de fala
    for texto, ordem in (
        ("Para.", "para"),
        ("Vis, para!", "para"),  # o fim do nome, ainda meio coberto, não atrapalha
        ("Pode parar, por favor.", "pode parar por favor"),
        ("isso aí não, para", "para"),
        ("", None),
        ("para o vídeo", None),  # um pedido, não uma ordem de parar
        ("eu acho que ele devia para", None),  # comprido demais para ser só uma ordem
    ):
        ouvido.respostas.append(texto)
        assert vigia._mandou_parar(som) == ordem, texto
    assert {tamanho for tamanho, _ in ouvido.chamadas} == {TAXA} and not any(rapido for _, rapido in ouvido.chamadas)
    # Sem voz dele no começo da fala, ou sem quase nada depois dela, nem transcreve.
    antes = len(ouvido.chamadas)
    cortador.limpo_desde = int(TAXA * 1.75)
    assert vigia._mandou_parar(som) is None
    cortador.sobre_ele, cortador.limpo_desde = False, TAXA
    assert vigia._mandou_parar(som) is None
    assert len(ouvido.chamadas) == antes
    # Microfone em outra taxa: a marca do cortador é na taxa dele; o trecho já vem em 16 kHz.
    vigia._taxa, cortador.sobre_ele, cortador.limpo_desde = 48000, True, 48000 * 3 // 2
    ouvido.respostas.append("chega")
    assert vigia._mandou_parar(som) is None and ouvido.chamadas[-1][0] == TAXA // 2


def test_cortador_marca_onde_a_voz_do_jarvis_acaba_dentro_da_fala():
    cortador = _sala_medida()
    antes, tocado_antes = _caixa(_fala(2.0, volume=0.15), 1.0)
    _cortar(antes, cortador=cortador, tocado=tocado_antes)
    dele, tocado = _caixa(_fala(1.4, volume=0.15), 3.0)
    microfone = _por_cima(dele, _fala(1.2, volume=0.4), 1.0)
    marcas = []
    for i in range(0, len(microfone), 800):
        for tipo, som, *_ in cortador.alimentar(microfone[i : i + 800], tocado(i / TAXA)):
            marcas.append((tipo, cortador.sobre_ele, cortador.limpo_desde / TAXA, len(som) / TAXA))
            if tipo == PAUSA:
                cortador.dispensar()
    ((tipo, sobre_ele, limpo, tamanho),) = marcas
    assert tipo == PAUSA and sobre_ele
    # A fala começou 0,3 s antes de 1,0 s; ele tocou até 1,55 s e o resto do som dele durou mais 0,35 s.
    assert limpo == pytest.approx(1.55 + 0.35 - 0.7, abs=0.1) and tamanho == pytest.approx(2.65 - 0.7, abs=0.1)
    # Fala sem ele tocando: nada a marcar.
    for tipo, som, *_ in cortador.alimentar(_montar(1.5, _fala(0.8), 2.0)):
        assert not cortador.sobre_ele and cortador.limpo_desde == 0


def test_vigia_pedido_que_por_acaso_e_igual_a_resposta_dele_so_e_ignorado_logo_depois_dela():
    vigia, eventos, _, _ = _vigia("Jarvis", "abre o site", "Jarvis", "Diga.", proprias=("Sim?", "Diga."))
    vigia.tratar(_som(1))
    vigia.tratar(_som(1))
    vigia.tratar(_som(1))  # chamou de novo, sem que ele tenha respondido em voz alta desta vez
    vigia.tratar(_som(1))
    assert eventos == [("acordou",), ("pedido", "abre o site"), ("acordou",), ("pedido", "Diga.")]


def test_vigia_atalho_no_meio_da_transcricao_do_nome_nao_responde_para_dentro_da_gravacao():
    """O usuário apertou o atalho enquanto o "Jarvis" ainda era transcrito: o microfone agora é da gravação."""
    eventos = []

    def transcrever(audio, rapido=False, dica=""):
        vigia.suspender()  # nesse meio-tempo a gravação pelo atalho começou
        return "Jarvis"

    vigia = Vigia(transcrever, lambda t: eventos.append(("pedido", t)), ao_acordar=lambda: eventos.append("acordou"), ao_atender=lambda: eventos.append("atendeu"), sd=SomFalso())
    vigia.atenta = True
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.5))
        assert eventos == [] and not vigia.esperando_pedido and vigia.suspensa
    finally:
        vigia.parar()


def test_vigia_atalho_com_ele_esperando_o_pedido_desfaz_a_espera():
    """Disse "Jarvis", ouviu "Sim?" e resolveu ditar pelo atalho. O que for dito depois da gravação não é "o pedido"."""
    vigia, eventos, ouvido, _ = _vigia("Jarvis", "vamos almoçar", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.2))
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
        vigia.suspender()  # a gravação pelo atalho pegou o microfone
        assert not vigia.esperando_pedido
        vigia.retomar()
        _tocar(vigia, _montar(0.5, _fala(1.0), 2.0))  # uma fala qualquer, logo depois
        assert eventos == [("acordou",), ("atendeu",)] and list(vigia.ouvidos) == ["vamos almoçar"]
    finally:
        vigia.parar()


def test_vigia_microfone_que_cai_com_ele_esperando_o_pedido_avisa_que_desistiu():
    vigia, eventos, _, relogio = _vigia("Jarvis", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.2))
        assert eventos == [("acordou",), ("atendeu",)]
        relogio.agora += 10  # o microfone parou de mandar som (cabo, suspensão)
        assert vigia.conferir_microfone() is True
        assert eventos[2:] == [("desistiu",)] and not vigia.esperando_pedido  # quem mostrava "ouvindo" volta ao normal
    finally:
        vigia.parar()


def test_vigia_pedido_que_nao_espera_e_entregue_na_pausa():
    """ "Jarvis, para" corta a fala meio segundo depois de dito, sem esperar o segundo inteiro de silêncio."""
    vigia, eventos, _, _ = _vigia("Jarvis, para.", sd=SomFalso(), imediato=lambda pedido: pedido == "para")
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(1.0), 0.6))
        assert eventos == [("acordou",), ("pedido", "para")] and not vigia._cortador.em_fala
        _tocar(vigia, _montar(2.0))
        assert len(eventos) == 2  # e não é entregue outra vez no fim
    finally:
        vigia.parar()


def test_vigia_pedido_comprido_e_conferido_na_janela_e_entregue_no_fim():
    vigia, eventos, ouvido, _ = _vigia("Jarvis, abre o YouTube e", "Jarvis, abre o YouTube e procura um vídeo de receita.", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(3.5)))  # ainda falando…
        assert ouvido.chamadas == [(int(JANELA * TAXA), True)] and eventos == [("acordou",)]  # o começo já foi conferido
        _tocar(vigia, _montar(_fala(2.5), 2.0))
        assert eventos[1:] == [("pedido", "abre o YouTube e procura um vídeo de receita")]
        assert ouvido.chamadas[1][1] is False and ouvido.chamadas[1][0] == pytest.approx(6.5 * TAXA, rel=0.1)
    finally:
        vigia.parar()


def test_vigia_conversa_comprida_da_sala_custa_uma_olhada_e_nada_mais():
    vigia, eventos, ouvido, _ = _vigia("então eu disse para ele que", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(9.0), 2.0))
        assert eventos == [] and len(ouvido.chamadas) == 1 and ouvido.chamadas[0] == (int(JANELA * TAXA), True)
        assert list(vigia.ouvidos) == ["então eu disse para ele que"]
    finally:
        vigia.parar()


def test_vigia_chamado_repetido_depois_de_um_mal_entendido_e_ouvido():
    """Chamou, a transcrição errou o nome, chamou de novo sem esperar: a segunda vale."""
    vigia, eventos, ouvido, _ = _vigia("Chaves.", "Jarvis.", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 0.6, _fala(0.6), 0.6))
        assert eventos == [("acordou",), ("atendeu",)] and list(vigia.ouvidos) == ["Chaves."]
    finally:
        vigia.parar()


def test_vigia_depois_do_nome_o_pedido_comprido_nao_precisa_do_nome_de_novo():
    vigia, eventos, ouvido, _ = _vigia("Jarvis", "abre o YouTube e procura um vídeo de receita de bolo", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.5))
        assert eventos == [("acordou",), ("atendeu",)]
        _tocar(vigia, _montar(0.5, _fala(5.0), 2.0))  # mais comprido que a janela: aceito sem conferência
        assert eventos[-1] == ("pedido", "abre o YouTube e procura um vídeo de receita de bolo")
        assert [rapido for _, rapido in ouvido.chamadas] == [False, False]
    finally:
        vigia.parar()


def test_vigia_microfone_que_nao_aceita_16k():
    sd = SomFalso(recusa=TAXA)
    vigia, eventos, ouvido, _ = _vigia("Jarvis, que horas são", sd=sd)
    vigia.iniciar()
    try:
        assert sd.abertos[0].taxa == 48000
        _tocar(vigia, _montar(1.5, _fala(2.0, taxa=48000), 2.0, taxa=48000), taxa=48000)
        assert eventos == [("acordou",), ("pedido", "que horas são")]
        assert ouvido.chamadas[0][0] == pytest.approx((0.3 + 2.0 + 0.45) * TAXA, rel=0.1)  # chegou convertido para 16 kHz
    finally:
        vigia.parar()


# --- ouvir enquanto o Jarvis fala -------------------------------------------------------------------


def test_vigia_a_voz_do_proprio_jarvis_nao_e_transcrita():
    vigia, eventos, ouvido, _ = _vigia(sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(0.4, 0.2), 1.0, _fala(6.0, 0.15), 2.0)
        _tocar(vigia, microfone, tocado=tocado)
        assert eventos == [] and ouvido.chamadas == []
    finally:
        vigia.parar()


def test_vigia_jarvis_para_dito_por_cima_da_fala_dele():
    """Ele se cala assim que ouve uma voz firme mais alta que a dele, e o que foi dito corta a resposta."""
    vigia, eventos, ouvido, relogio = _vigia("a previsão. Jarvis, para.", sd=SomFalso(), imediato=lambda pedido: pedido == "para")
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(3.3, 0.1), 5.0)  # ele fala; aos 3,3 s é calado (ver abaixo) e fica quieto
        microfone = _por_cima(microfone, _fala(1.0, 0.2), em=3.0)
        _tocar(vigia, microfone[: int(3.4 * TAXA)], tocado=tocado, relogio=relogio)
        assert eventos == [("calou",)]  # 0,3 s de voz por cima: ele para para ouvir
        _tocar(vigia, microfone[int(3.4 * TAXA) : int(4.7 * TAXA)], tocado=tocado, relogio=relogio, desde=3.4)
        # A transcrição pegou o fim da frase dele antes do nome; como a fala nasceu por cima dele, o nome vale assim mesmo.
        assert eventos == [("calou",), ("acordou",), ("pedido", "para")]
        assert len(ouvido.chamadas) == 1
    finally:
        vigia.parar()


def test_vigia_por_cima_dele_o_nome_no_meio_da_frase_so_vale_para_chamar_ou_parar():
    """ "Deixa o Jarvis terminar", dito enquanto ele fala, não é um pedido. "Jarvis!" e "Jarvis, para" são."""

    def cena(texto):
        vigia, eventos, _, relogio = _vigia(texto, "", "", sd=SomFalso(), imediato=lambda pedido: pedido == "para")
        vigia.iniciar()
        try:
            _tocar(vigia, _montar(1.5))
            microfone, tocado = _caixa(_fala(3.3, 0.1), 5.0)
            _tocar(vigia, _por_cima(microfone, _fala(1.0, 0.2), em=3.0), tocado=tocado, relogio=relogio)
            return eventos
        finally:
            vigia.parar()

    assert cena("de sol. Espera, deixa o Jarvis terminar") == [("calou",), ("engano",)]
    assert cena("de sol. Jarvis!")[:2] == [("calou",), ("acordou",)]
    assert cena("de sol. Jarvis, para") == [("calou",), ("acordou",), ("pedido", "para")]
    assert cena("Jarvis, abre o site") == [("calou",), ("acordou",), ("pedido", "abre o site")]  # no começo, vale qualquer pedido


def test_vigia_depois_de_ouvir_o_nome_nao_se_cala_de_novo_pela_mesma_fala():
    vigia, eventos, _, relogio = _vigia("Jarvis, que horas", "Jarvis, que horas são agora", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(8.0, 0.1), 3.0)  # quem cala a fala dele, na vida real, é quem recebe os avisos; aqui ele segue
        microfone = _por_cima(microfone, _fala(1.0, 0.25), em=3.0)
        microfone = _por_cima(microfone, _fala(1.0, 0.25), em=4.5)  # "Jarvis, que horas… são agora", com uma pausa no meio
        _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
        assert eventos == [("calou",), ("acordou",), ("pedido", "que horas são agora")]
    finally:
        vigia.parar()


def test_vigia_microfone_desligado_com_ele_calado_para_ouvir_devolve_a_fala_na_hora():
    vigia, eventos, _, relogio = _vigia(sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(6.0, 0.1))
        _tocar(vigia, _por_cima(microfone, _fala(2.0, 0.25), em=3.0)[: int(3.6 * TAXA)], tocado=tocado, relogio=relogio)
        assert eventos == [("calou",)]
        vigia.pausar()  # o usuário desligou o microfone no ícone
        assert eventos == [("calou",), ("engano",)]
    finally:
        vigia.parar()


def test_vigia_fora_da_fala_dele_o_nome_no_meio_da_frase_nao_chama():
    vigia, eventos, _, _ = _vigia("a previsão. Jarvis, para.", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(1.0), 2.0))
        assert eventos == []
    finally:
        vigia.parar()


def test_vigia_volume_da_caixa_aumentado_ele_se_estranha_uma_vez_e_aprende():
    """A voz dele passou a chegar bem mais alta do que ele conhecia. Toma a si mesmo por gente uma vez, e não mais."""
    respostas = ["de sol pela manhã"] * 20  # o que a transcrição ouve é o que ele mesmo estava dizendo
    vigia, eventos, ouvido, relogio = _vigia(*respostas, sd=SomFalso(), ditas=lambda: ["Hoje a previsão é de sol pela manhã.", "À tarde chove."])
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(4.0, 0.1), 1.5, ganho=0.1)
        _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
        assert eventos == [] and ouvido.chamadas == []
        for _ in range(4):  # quatro respostas com a caixa três vezes mais alta
            microfone, tocado = _caixa(_fala(4.0, 0.1), 1.5, ganho=0.3)
            _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
        assert eventos == [("calou",), ("engano",)]  # um engano só, na primeira
        assert len(ouvido.chamadas) <= 2 and list(vigia.ouvidos)  # e parou de transcrever a própria voz
    finally:
        vigia.parar()


def test_vigia_comentario_por_cima_dele_nao_o_deixa_surdo_para_o_para_que_vem_depois():
    """Alguém comenta algo por cima da fala dele e, dois segundos depois, manda parar no mesmo volume. Com fone: é para ouvir."""
    vigia, eventos, _, relogio = _vigia(
        "que horas a gente sai?", "Jarvis, para", sd=SomFalso(), imediato=lambda pedido: pedido == "para", ditas=lambda: ["A temperatura máxima fica perto dos trinta graus."]
    )
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(4.0, 0.15), 1.0, ganho=0.0)
        _tocar(vigia, microfone, tocado=tocado, relogio=relogio)  # ele já falou antes: nada dele chega ao microfone
        microfone, tocado = _caixa(_fala(12.0, 0.15), ganho=0.0)
        microfone = _por_cima(microfone, _fala(1.2, 0.03), em=3.0)  # o comentário
        microfone = _por_cima(microfone, _fala(1.0, 0.03), em=6.5)  # e o "Jarvis, para", no mesmo volume
        _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
        assert eventos == [("calou",), ("engano",), ("acordou",), ("pedido", "para")]
    finally:
        vigia.parar()


@pytest.mark.parametrize(
    ("ouvido", "dele"),
    [
        ("de sol pela manhã", True),  # o que ele dizia
        ("a previsão pra hoje é do sol", True),  # o que ele dizia, com a transcrição errando um pouco
        ("", True),  # nada que se entenda: não era gente
        ("que horas a gente sai?", False),
        ("espera, deixa ele terminar", False),
        ("a previsão do jogo de hoje ficou pra amanhã", False),  # algumas palavras iguais não bastam
    ],
)
def test_vigia_reconhece_a_voz_dele_pelo_que_foi_dito(ouvido, dele):
    vigia, _, _, _ = _vigia(ditas=lambda: ["Hoje a previsão é de sol pela manhã.", "À tarde chove."])
    assert vigia._parece_dele(ouvido) is dele


def test_vigia_sem_saber_o_que_ele_dizia_trata_como_voz_dele():
    def quebrada():
        raise RuntimeError("a fala caiu")

    vigia, _, _, _ = _vigia(ditas=quebrada)
    assert vigia._parece_dele("qualquer coisa") is True
    vigia, _, _, _ = _vigia()  # ninguém informou as frases: nada é reconhecido como dele
    assert vigia._parece_dele("qualquer coisa") is False


def test_vigia_quem_fala_por_cima_sem_chamar_faz_ele_parar_e_continuar_uma_vez_so():
    """Calou-se à toa: continua de onde parou e passa um tempo sem se calar de novo, para não gaguejar."""
    vigia, eventos, ouvido, relogio = _vigia("que horas a gente sai?", "e o almoço?", "nossa", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(40.0, 0.1))
        for em in (3.0, 9.0, 25.0):
            microfone = _por_cima(microfone, _fala(1.0, 0.25), em=em)
        _tocar(vigia, microfone[: int(7 * TAXA)], tocado=tocado, relogio=relogio)
        assert eventos == [("calou",), ("engano",)]
        _tocar(vigia, microfone[int(7 * TAXA) : int(15 * TAXA)], tocado=tocado, relogio=relogio, desde=7.0)
        assert eventos == [("calou",), ("engano",)] and len(ouvido.chamadas) == 2  # ouviu e conferiu, mas sem se calar
        _tocar(vigia, microfone[int(15 * TAXA) :], tocado=tocado, relogio=relogio, desde=15.0)
        assert eventos == [("calou",), ("engano",)] * 2  # passada a folga, volta a se calar para ouvir
        assert vigia._folga == 20.0  # e a folga seguinte é maior
    finally:
        vigia.parar()


def test_vigia_nao_fica_calada_para_sempre_esperando_uma_voz_que_nao_para():
    vigia, eventos, _, relogio = _vigia("e aí eu falei", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(4.0, 0.1), 12.0)
        microfone = _por_cima(microfone, _fala(11.0, 0.25), em=3.0)  # alguém desata a falar por cima
        _tocar(vigia, microfone, tocado=tocado, relogio=relogio)
        assert eventos == [("calou",), ("engano",)]
    finally:
        vigia.parar()


def test_vigia_chamado_por_cima_zera_a_folga():
    vigia, eventos, _, relogio = _vigia("Jarvis", sd=SomFalso())
    vigia._folga = 40.0
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(3.3, 0.1), 3.0)
        _tocar(vigia, _por_cima(microfone, _fala(0.6, 0.25), em=3.0), tocado=tocado, relogio=relogio)
        assert eventos[:2] == [("calou",), ("acordou",)] and vigia._folga == 0.0
    finally:
        vigia.parar()


def test_vigia_anota_na_captura_o_que_o_jarvis_tocava():
    """Quem trata o som pode estar atrasado. Vale o que era verdade na hora em que o som entrou."""
    tocando = [0.0]
    vigia, _, _, _ = _vigia(sd=SomFalso(), tocado=lambda: tocando[0])
    vigia.iniciar()
    vigia.parar()
    bloco = np.zeros((800, 1), dtype=np.float32)
    vigia._receber(bloco, 800, None, None)
    tocando[0] = 0.2
    vigia._receber(bloco, 800, None, None)
    vigia.atenta = False
    vigia._receber(bloco, 800, None, None)
    assert [(ouvir, tocado) for ouvir, tocado, _, _ in list(vigia._fila.queue)] == [(True, 0.0), (True, 0.2), (False, 0.0)]


def test_vigia_o_que_foi_captado_durante_uma_confirmacao_nao_vale_depois():
    vigia, eventos, ouvido, _ = _vigia(sd=SomFalso())
    vigia.iniciar()
    vigia.parar()  # sem a thread: os pedaços ficam na fila, como se ela estivesse presa numa transcrição
    sinal = _montar(1.5, _fala(2.0), 2.0)
    vigia.atenta = False  # o Jarvis espera um "s" no teclado enquanto este som entra
    for i in range(0, len(sinal), 800):
        vigia._receber(sinal[i : i + 800].reshape(-1, 1), 800, None, None)
    vigia.atenta = True  # a confirmação acabou antes de a fila ser tratada
    while not vigia._fila.empty():
        vigia._bloco(*vigia._fila.get())
    assert eventos == [] and ouvido.chamadas == []


def test_vigia_esquece_o_que_estava_pela_metade_quando_deixa_de_ouvir():
    vigia, eventos, ouvido, _ = _vigia("Jarvis", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.5))
        assert eventos == [("acordou",), ("atendeu",)] and vigia.esperando_pedido
        vigia._bloco(False, 0.0, _montar(0.2).reshape(-1, 1))  # uma confirmação no teclado
        assert not vigia.esperando_pedido  # a próxima fala não será tomada por pedido
        assert eventos[2:] == [("desistiu",)]  # e quem mostrava "ouvindo" fica sabendo
        _tocar(vigia, _montar(1.0, _fala(1.0)))  # começou a falar…
        assert vigia._cortador.em_fala
        vigia._bloco(False, 0.0, _montar(0.2).reshape(-1, 1))
        assert not vigia._cortador.em_fala and len(ouvido.chamadas) == 1  # a meia frase não é emendada com o que vier depois
    finally:
        vigia.parar()


def test_vigia_que_deixa_de_ouvir_com_o_jarvis_calado_para_ouvir_devolve_a_fala():
    vigia, eventos, _, relogio = _vigia(sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))
        microfone, tocado = _caixa(_fala(6.0, 0.1))
        _tocar(vigia, _por_cima(microfone, _fala(2.0, 0.25), em=3.0)[: int(3.6 * TAXA)], tocado=tocado, relogio=relogio)
        assert eventos == [("calou",)]
        vigia._bloco(False, 0.0, _montar(0.2).reshape(-1, 1))  # a tela foi bloqueada
        assert eventos == [("calou",), ("engano",)]  # ele não fica mudo esperando
    finally:
        vigia.parar()


def test_vigia_desiste_se_o_pedido_nao_vem():
    vigia, eventos, _, relogio = _vigia("Jarvis", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5, _fala(0.6), 1.5))
        assert eventos == [("acordou",), ("atendeu",)]
        relogio.agora += ESPERA_DO_PEDIDO - 1
        _tocar(vigia, _montar(0.5))
        assert vigia.esperando_pedido
        relogio.agora += 2
        _tocar(vigia, _montar(0.5))
        assert eventos[2:] == [("desistiu",)] and not vigia.esperando_pedido
    finally:
        vigia.parar()


def test_vigia_solta_o_microfone_para_a_gravacao_e_respeita_a_pausa():
    sd = SomFalso()
    vigia, _, _, _ = _vigia(sd=sd)
    vigia.iniciar()
    try:
        vigia.suspender()
        assert sd.abertos == [] and not vigia.aberta
        assert vigia.conferir_microfone() is False and sd.abertos == []  # a gravação pelo atalho está com ele: não é queda
        vigia.retomar()
        assert len(sd.abertos) == 1
        vigia.pausar()  # o usuário desligou o microfone no ícone
        assert sd.abertos == []
        vigia.retomar()  # o fim de uma gravação pelo atalho não religa o que o usuário desligou
        assert sd.abertos == [] and vigia.conferir_microfone() is False
        vigia.continuar()
        assert len(sd.abertos) == 1 and not vigia.pausada
    finally:
        vigia.parar()


def test_vigia_reabre_o_microfone_que_caiu():
    sd = SomFalso()
    vigia, _, _, relogio = _vigia(sd=sd)
    vigia.iniciar()
    try:
        primeiro = sd.abertos[0]
        relogio.agora += 2
        assert vigia.conferir_microfone() is True and sd.abertos == [primeiro]  # som chegando há pouco: está vivo
        relogio.agora += 10  # o Windows voltou de uma suspensão: o fluxo existe, mas não manda mais nada
        assert vigia.conferir_microfone() is True
        assert primeiro.estado == "abortado e fechado"  # largado sem esperar por ele
        assert len(sd.abertos) == 1 and sd.abertos[0] is not primeiro and sd.releituras == 0
        sd.quebrado = True  # agora o cabo saiu
        relogio.agora += 10
        assert vigia.conferir_microfone() is False and not vigia.aberta and "desconectado" in vigia.erro
        assert sd.releituras == 1  # não abriu: releu a lista de microfones e tentou de novo
        sd.quebrado = False  # e voltou
        assert vigia.conferir_microfone() is True and vigia.aberta and vigia.erro == ""
    finally:
        vigia.parar()


def test_vigia_acha_o_microfone_ligado_depois_relendo_a_lista():
    sd = SomFalso()
    falando = [False]
    vigia, _, _, relogio = _vigia(sd=sd, falando=lambda: falando[0])
    vigia.iniciar()
    try:
        vigia.suspender()
        vigia.retomar()
        sd.quebrado = "até reler"  # o fone Bluetooth conectou depois: só aparece relendo a lista
        relogio.agora += 10
        falando[0] = True  # com o Jarvis falando não se mexe no áudio
        assert vigia.conferir_microfone() is False and sd.releituras == 0
        falando[0] = False
        assert vigia.conferir_microfone() is True and sd.releituras == 1 and vigia.aberta
    finally:
        vigia.parar()


def test_vigia_reaberta_nao_mede_a_sala_de_novo():
    vigia, eventos, _, _ = _vigia("Jarvis, que horas são", sd=SomFalso())
    vigia.iniciar()
    try:
        _tocar(vigia, _montar(1.5))  # a sala já foi medida
        vigia.suspender()  # uma gravação pelo atalho
        vigia.retomar()
        _tocar(vigia, _montar(0.2, _fala(1.5), 1.5))  # chamado logo depois de reabrir
        assert eventos == [("acordou",), ("pedido", "que horas são")]
    finally:
        vigia.parar()


def test_vigia_descarta_o_som_acumulado_quando_fica_para_tras():
    vigia, eventos, ouvido, _ = _vigia(sd=SomFalso())
    vigia.iniciar()
    try:
        vigia.parar()  # sem a thread: a fila só enche
        bloco = np.zeros((1600, 1), dtype=np.float32)
        for _ in range(300):  # 30 s esperando
            vigia._fila.put((True, 0.0, bloco))
        vigia._bloco(True, 0.0, bloco)
        assert vigia._fila.empty() and eventos == [] and ouvido.chamadas == []
    finally:
        vigia.parar()


def test_vigia_sobrevive_a_quem_recebe_o_aviso_e_a_transcricao_com_erro():
    def explode(_texto):
        raise RuntimeError("quem recebe caiu")

    vigia = Vigia(Ouvido("Jarvis, oi"), explode)
    vigia.atenta = True
    vigia.tratar(_som(1))  # não levanta

    def transcricao_quebrada(audio, rapido=False, dica=""):
        raise RuntimeError("modelo caiu")

    sd = SomFalso()
    vigia = Vigia(transcricao_quebrada, lambda t: None, sd=sd)
    vigia.atenta = True
    vigia.iniciar()
    try:
        vigia._fila.put((True, 0.0, _montar(1.5, _fala(1.0), 2.0).reshape(-1, 1)))
        vigia._fila.put(None)
        vigia._thread.join(5)  # o laço tratou o trecho com erro e saiu só pelo aviso de fim
        assert not vigia._thread.is_alive()
        assert len(vigia.tempos) == 1  # mesmo a que falhou tomou tempo
    finally:
        vigia.parar()


def test_vigia_sem_microfone_na_partida_nao_deixa_thread_para_tras():
    sd = SomFalso()
    sd.quebrado = True
    vigia, _, _, _ = _vigia(sd=sd)
    with pytest.raises(OSError):
        vigia.iniciar()
    assert vigia._thread is None and not vigia.aberta


# --- a voz com a vigília ----------------------------------------------------------------------------


class VigiaFalsa:
    def __init__(self, transcrever, ao_pedir, palavras=(), falando=None, tocado=None, impedido=None, leve=False, **avisos):
        self.transcrever, self.ao_pedir, self.palavras, self.avisos = transcrever, ao_pedir, palavras, avisos
        self.falando, self.tocado, self.impedido, self.leve = falando, tocado, impedido, leve
        self.eventos = []

    def iniciar(self):
        self.eventos.append("iniciar")

    def suspender(self):
        self.eventos.append("suspender")

    def retomar(self):
        self.eventos.append("retomar")

    def parar(self):
        self.eventos.append("parar")


class Peca:
    def __init__(self, nome, registro):
        self.nome, self.registro, self.falando, self.volume = nome, registro, False, 0.0

    def transcrever(self, audio, rapido=False, dica=""):
        self.registro.append((self.nome, rapido, dica))
        return self.nome

    def falar(self, frase):
        self.registro.append(("falou", frase))

    def tocado(self):
        return self.volume

    def interromper(self):
        self.registro.append("interrompeu")

    def comecar(self):
        self.registro.append("gravando")

    def terminar(self):
        return "audio"


def test_voz_liga_a_vigilia_e_divide_o_microfone_com_a_gravacao():
    registro = []
    falante, principal, leve = Peca("falante", registro), Peca("principal", registro), Peca("leve", registro)
    voz = Voz(falante, principal, principal, ouvinte=leve)
    bloqueada = [False]
    voz.vigiar(("jarvis",), lambda texto: None, fabrica=VigiaFalsa, impedido=lambda: bloqueada[0], ao_acordar=lambda: None)
    vigia = voz.vigia
    assert vigia.eventos == ["iniciar"] and vigia.palavras == ("jarvis",) and "ao_acordar" in vigia.avisos
    # O começo de cada trecho vai para o modelo leve; o pedido, para o principal. A dica do nome vai junto.
    assert vigia.transcrever("som", rapido=True, dica="Jarvis") == "leve" and vigia.transcrever("som") == "principal"
    assert registro[:2] == [("leve", True, "Jarvis"), ("principal", False, "")]
    assert vigia.leve is True  # há um modelo menor para a procura do nome
    assert vigia.falando() is False and vigia.tocado() == 0.0 and vigia.avisos["ditas"]() == []
    falante.falando, falante.volume = True, 0.12
    assert vigia.falando() is True and vigia.tocado() == 0.12  # a vigília sabe o que ele toca, para não tomar por gente
    bloqueada[0] = True
    assert vigia.impedido() is True  # e não atende com a tela bloqueada
    voz.comecar_gravacao()
    assert voz.terminar_gravacao() == "principal"
    assert vigia.eventos == ["iniciar", "suspender", "retomar"]
    voz.parar_vigilia()
    assert vigia.eventos[-1] == "parar" and voz.vigia is None
    assert Voz(falante, principal, principal)._ouvinte is principal  # sem modelo leve, um só faz tudo


def test_voz_calada_nao_narra_o_resto_da_resposta_mas_responde_ao_nome():
    registro = []
    falante = Peca("falante", registro)
    voz = Voz(falante, falante, falante)
    voz.narrar("Primeira frase. ")
    voz.calar()  # chamaram no meio da resposta
    voz.narrar("Segunda frase. Terceira frase. ")
    voz.fim_de_bloco()
    voz.dizer("Sim?")
    assert registro == [("falou", "Primeira frase."), "interrompeu", ("falou", "Sim?")]
    voz.soltar()  # pedido novo: volta a narrar
    voz.narrar("Quarta frase. ")
    assert registro[-1] == ("falou", "Quarta frase.")


def test_voz_sem_microfone_para_a_vigilia_vira_erro_claro():
    from jarvis.voz import ErroVoz

    class SemMicrofone(VigiaFalsa):
        def iniciar(self):
            raise OSError("nenhum dispositivo de entrada")

    peca = Peca("p", [])
    voz = Voz(peca, peca, peca)
    with pytest.raises(ErroVoz, match="palavra de ativação"):
        voz.vigiar(("jarvis",), lambda texto: None, fabrica=SemMicrofone)
    assert voz.vigia is None


def test_vigia_reconhece_pedaco_da_propria_resposta_e_nao_confunde_com_pedido():
    vigia, _, _, _ = _vigia(proprias=("Sim, mestre. Com o que eu posso te ajudar?", "À disposição, senhor. Qual é a minha missão do dia?", "Pois não?"))
    for texto in ("Sim, mestre. Com o que eu posso te ajudar?", "com o que eu posso te ajudar", "Com o que eu posso ajuda?",
                  "a disposicao senhor", "Qual é a minha missão do dia?", "Pois não."):
        assert vigia._parece_resposta(texto), texto
    for texto in ("abre a planilha", "pois não vou", "qual é a previsão do dia", "me ajuda com a planilha", ""):
        assert not vigia._parece_resposta(texto), texto


def test_vigia_pedaco_da_resposta_comprida_voltando_pela_sala_nao_vira_pedido():
    """A transcrição pegou só o fim da resposta, voltando pelo microfone: não é o pedido "com o que eu posso te ajudar"."""
    vigia, eventos, _, _ = _vigia("Jarvis", "com o que eu posso te ajudar", "que horas são?", proprias=("Sim, mestre. Com o que eu posso te ajudar?",))
    vigia.tratar(_som(1))
    vigia._atender, vigia._eco_da_resposta = False, True
    vigia.tratar(_som(1.5))
    assert eventos == [("acordou",)] and vigia.esperando_pedido
    vigia.tratar(_som(1.5))
    assert eventos[-1] == ("pedido", "que horas são?")


def test_vigia_tira_do_comeco_do_pedido_a_resposta_dele_que_entrou_junto():
    resposta = "Sim, mestre. Com o que eu posso te ajudar?"
    vigia, eventos, _, relogio = _vigia("Jarvis", "Sim, mestre, com o que eu posso te ajudar? Que horas são agora?", proprias=(resposta,))
    vigia.tratar(_som(1))
    vigia._atender, vigia._respondeu_em = False, relogio.agora + 0.1
    vigia.tratar(_som(4))
    assert eventos == [("acordou",), ("pedido", "Que horas são agora?")]


def test_vigia_so_tira_a_resposta_do_comeco_e_so_depois_de_responder():
    resposta = "Sim, mestre. Com o que eu posso te ajudar?"
    vigia, _, _, _ = _vigia(proprias=(resposta,))
    assert vigia._sem_a_resposta("Com o que eu posso te ajudar? Abre o YouTube. Com o que eu posso te ajudar?") == "Abre o YouTube. Com o que eu posso te ajudar?"
    assert vigia._sem_a_resposta("Abre o YouTube.") == "Abre o YouTube."
    # Antes de ele responder a este chamado, nada é tirado: a pessoa pode ter emendado o pedido no nome.
    vigia, eventos, _, relogio = _vigia("Jarvis", "Sim, mestre. Abre o YouTube.", proprias=(resposta,))
    vigia.tratar(_som(1))
    vigia._atender = False
    vigia.tratar(_som(3))
    assert eventos[-1] == ("pedido", "Sim, mestre. Abre o YouTube.")


# --- a voz do dono ------------------------------------------------------------------------------------------


class Dono:
    """A conferência da voz, de mentira: aceita ou recusa conforme a lista, e guarda o que recebeu."""

    def __init__(self, *respostas):
        self.respostas, self.recebidos = list(respostas), []

    def __call__(self, audio):
        self.recebidos.append(len(audio))
        resposta = self.respostas.pop(0) if self.respostas else (True, 0.9)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def test_vigia_chamado_de_outra_voz_e_ignorado_e_fica_no_ouvido():
    dono = Dono((False, 0.12))
    vigia, eventos, _, _ = _vigia("Jarvis, abre o YouTube.", dono=dono)
    vigia.tratar(_som(2))
    assert eventos == []  # nada de pedido (e nada a desistir: ninguém tinha sido avisado)
    assert vigia.recusados == 1 and list(vigia.ouvidos) == ["(outra voz, 0.12) abre o YouTube"] and dono.recebidos == [2 * TAXA]
    # Só o nome, de outra voz: nem o "Sim?".
    dono = Dono((False, 0.2))
    vigia, eventos, _, _ = _vigia("Jarvis.", dono=dono)
    vigia.tratar(_som(1))
    assert eventos == [] and vigia._espera_ate == 0.0 and not vigia._atender and vigia.recusados == 1


def test_vigia_chamado_do_dono_passa_e_a_falha_da_conferencia_nao_tranca_ninguem(caplog):
    vigia, eventos, _, _ = _vigia("Jarvis, abre o YouTube.", dono=Dono((True, 0.71)))
    vigia.tratar(_som(2))
    assert eventos == [("pedido", "abre o YouTube")] and vigia.recusados == 0
    vigia, eventos, _, _ = _vigia("Jarvis, abre o YouTube.", dono=Dono(RuntimeError("modelo caiu")))
    vigia.tratar(_som(2))
    assert eventos == [("pedido", "abre o YouTube")] and "conferência da voz do dono falhou" in caplog.text


def test_vigia_ordem_de_parar_nao_passa_pela_conferencia():
    dono = Dono((False, 0.1))
    vigia, eventos, _, _ = _vigia("Jarvis, para.", dono=dono, imediato=lambda pedido: pedido == "para")
    vigia._pausa(_som(1), 10)
    assert ("pedido", "para") in eventos and dono.recebidos == []


def test_vigia_esperando_o_pedido_ignora_outra_voz_e_atende_o_dono_depois():
    dono = Dono((True, 0.8), (False, 0.15), (True, 0.75))
    vigia, eventos, ouvido, relogio = _vigia("Jarvis.", "abre o Instagram", "abre o YouTube", dono=dono)
    vigia.tratar(_som(1))  # o dono chama
    assert eventos == [("acordou",)] and vigia._espera_ate > 0
    relogio.agora += 1.0
    vigia.tratar(_som(1.5))  # outra pessoa diz algo: não é o pedido
    assert eventos == [("acordou",)] and vigia._espera_ate > 0 and vigia.recusados == 1
    assert list(vigia.ouvidos) == ["(outra voz, 0.15) abre o Instagram"]
    relogio.agora += 1.0
    vigia.tratar(_som(1.5))  # o dono pede
    assert eventos == [("acordou",), ("pedido", "abre o YouTube")] and dono.recebidos == [TAXA, int(1.5 * TAXA), int(1.5 * TAXA)]
