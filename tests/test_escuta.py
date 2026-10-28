import pytest

np = pytest.importorskip("numpy")

from jarvis.voz import escuta  # noqa: E402


class ErroDeAudio(Exception):
    pass


class FluxoFalso:
    def __init__(self, callback):
        self.callback = callback
        self.estado = "aberto"

    def start(self):
        self.estado = "gravando"

    def stop(self):
        self.estado = "parado"

    def close(self):
        self.estado = "fechado"


class SdFalso:
    """Imita o sounddevice. `taxas_aceitas` simula um microfone que recusa 16 kHz."""

    PortAudioError = ErroDeAudio

    def __init__(self, taxas_aceitas=(16000,), taxa_padrao=48000):
        self.taxas_aceitas = taxas_aceitas
        self.taxa_padrao = taxa_padrao
        self.fluxo = None
        self.abertos = []

    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802 - nome do sounddevice
        assert channels == 1 and dtype == "float32"
        self.abertos.append(samplerate)
        if samplerate not in self.taxas_aceitas:
            raise ErroDeAudio("Invalid sample rate")
        self.fluxo = FluxoFalso(callback)
        return self.fluxo

    def query_devices(self, kind=None):
        assert kind == "input"
        return {"default_samplerate": float(self.taxa_padrao)}


def tom(taxa: int, segundos: float, frequencia: float = 440.0):
    t = np.arange(int(taxa * segundos)) / taxa
    return np.sin(2 * np.pi * frequencia * t).astype(np.float32)


def test_grava_e_devolve_mono_16k():
    sd = SdFalso()
    gravador = escuta.Gravador(sd)
    gravador.comecar()
    assert gravador.gravando and sd.fluxo.estado == "gravando"
    sinal = tom(16000, 1.0)
    for bloco in np.split(sinal, 10):
        sd.fluxo.callback(bloco.reshape(-1, 1), len(bloco), None, None)
    audio = gravador.terminar()
    assert sd.fluxo.estado == "fechado" and not gravador.gravando
    assert audio.dtype == np.float32 and audio.shape == (16000,)
    np.testing.assert_allclose(audio, sinal)


def test_microfone_que_recusa_16k_e_convertido():
    sd = SdFalso(taxas_aceitas=(48000,), taxa_padrao=48000)
    gravador = escuta.Gravador(sd)
    gravador.comecar()
    assert sd.abertos == [16000, 48000]
    sinal = tom(48000, 1.0)
    sd.fluxo.callback(sinal.reshape(-1, 1), len(sinal), None, None)
    audio = gravador.terminar()
    assert audio.dtype == np.float32 and len(audio) == 16000
    # O tom de 440 Hz continua sendo 440 Hz depois da conversão.
    espectro = np.abs(np.fft.rfft(audio))
    assert abs(np.argmax(espectro) * 16000 / len(audio) - 440) < 2


def test_terminar_sem_gravar_devolve_vazio():
    audio = escuta.Gravador(SdFalso()).terminar()
    assert len(audio) == 0


def test_segunda_gravacao_nao_carrega_a_primeira():
    sd = SdFalso()
    gravador = escuta.Gravador(sd)
    gravador.comecar()
    sd.fluxo.callback(tom(16000, 0.5).reshape(-1, 1), 8000, None, None)
    assert len(gravador.terminar()) == 8000
    gravador.comecar()
    sd.fluxo.callback(tom(16000, 0.25).reshape(-1, 1), 4000, None, None)
    assert len(gravador.terminar()) == 4000


class Segmento:
    def __init__(self, text):
        self.text = text


class ModeloFalso:
    def __init__(self, nome, **opcoes):
        self.nome, self.opcoes, self.chamadas = nome, opcoes, []

    def transcribe(self, audio, **opcoes):
        self.chamadas.append(opcoes)
        return iter([Segmento(" que horas"), Segmento(" são? ")]), object()


def test_transcritor_junta_os_segmentos_em_portugues(tmp_path):
    transcritor = escuta.Transcritor("small", tmp_path / "whisper", fabrica=ModeloFalso)
    modelo = transcritor._modelo
    assert modelo.nome == "small"
    assert modelo.opcoes == {
        "device": "cpu",
        "compute_type": "int8",
        "download_root": str(tmp_path / "whisper"),
        "local_files_only": True,  # já baixado: não consulta a internet
    }
    assert transcritor.transcrever(tom(16000, 1.0)) == "que horas são?"
    assert modelo.chamadas[0]["language"] == "pt" and modelo.chamadas[0]["vad_filter"] is True


def test_toque_acidental_nao_vai_para_o_modelo(tmp_path):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloFalso)
    assert transcritor.transcrever(tom(16000, 0.1)) == ""
    assert transcritor._modelo.chamadas == []


def test_primeira_vez_baixa_o_modelo(tmp_path):
    tentativas = []

    def fabrica(nome, **opcoes):
        tentativas.append(opcoes.get("local_files_only", False))
        if opcoes.get("local_files_only"):
            raise FileNotFoundError("modelo ainda não está no disco")
        return ModeloFalso(nome, **opcoes)

    avisos = []
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=fabrica, avisar=avisos.append)
    assert tentativas == [True, False]
    assert len(avisos) == 1 and "Baixando" in avisos[0]
    assert transcritor.transcrever(tom(16000, 1.0)) == "que horas são?"


def test_medidor_de_volume_do_microfone():
    sd = SdFalso()
    gravador = escuta.Gravador(sd)
    assert gravador.nivel == 0.0
    gravador.comecar()
    sd.fluxo.callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, None)
    assert gravador.nivel == 0.0
    sd.fluxo.callback((tom(16000, 0.1) * 0.05).reshape(-1, 1), 1600, None, None)  # fala baixa
    baixo = gravador.nivel
    sd.fluxo.callback((tom(16000, 0.1) * 0.5).reshape(-1, 1), 1600, None, None)  # fala alta
    assert 0.1 < baixo < 0.6 and gravador.nivel == 1.0
    gravador.terminar()
    assert gravador.nivel == 0.0  # parou de gravar: o medidor zera


# --- a janela curta ---------------------------------------------------------------------------------


class Saida:
    def __init__(self, fichas, nota=-0.3, mudo=0.01):
        self.sequences_ids, self.scores, self.no_speech_prob = [list(fichas)], [nota], mudo


class Motor:
    """A parte do modelo que escreve o texto (do ctranslate2)."""

    is_multilingual = True

    def __init__(self):
        self.saida, self.chamadas = Saida([70, 71]), []

    def generate(self, ouvido, comecos, **opcoes):
        self.chamadas.append((ouvido, comecos, opcoes))
        if isinstance(self.saida, Exception):
            raise self.saida
        return [self.saida]


class ModeloCompleto(ModeloFalso):
    """Com as peças que a janela curta usa."""

    hf_tokenizer = "fichas do modelo"

    def __init__(self, nome, **opcoes):
        super().__init__(nome, **opcoes)
        self.model, self.ouvidos = Motor(), []

    def feature_extractor(self, voz):
        self.ouvidos.append(len(voz))
        return np.zeros((80, len(voz) // 160), dtype=np.float32)

    def encode(self, tracos):
        return ("ouvido", tracos.shape)


@pytest.fixture
def biblioteca(monkeypatch):
    """As três peças da faster-whisper de que a janela curta precisa, de mentira: o que cada uma recebeu fica anotado."""
    import sys
    import types

    anotado = types.SimpleNamespace(falas=None, janelas=[], opcoes_da_voz=[], fichas=[])

    class Fichas:
        sot_prev, sot_sequence, no_timestamps = 1, [2, 3, 4], 5
        textos = {(70, 71): " Jarvis ", (80,): " que horas são? ", (90,) * 40: "ha " * 40}

        def __init__(self, do_modelo, varias_linguas, task, language):
            anotado.fichas.append((do_modelo, varias_linguas, task, language))

        def encode(self, texto):
            return [1000 + len(texto)]

        def decode(self, fichas):
            return self.textos[tuple(fichas)]

    def cortar_ou_completar(tracos, tamanho):
        anotado.janelas.append(tamanho)
        return tracos[:, :tamanho]

    def achar_falas(audio, opcoes):
        anotado.opcoes_da_voz.append(opcoes)
        return [{"start": 0, "end": len(audio)}] if anotado.falas is None else anotado.falas

    pecas = {
        "faster_whisper": types.ModuleType("faster_whisper"),
        "faster_whisper.audio": types.SimpleNamespace(pad_or_trim=cortar_ou_completar),
        "faster_whisper.tokenizer": types.SimpleNamespace(Tokenizer=Fichas),
        "faster_whisper.vad": types.SimpleNamespace(VadOptions=lambda **opcoes: opcoes, get_speech_timestamps=achar_falas),
    }
    for nome, peca in pecas.items():
        monkeypatch.setitem(sys.modules, nome, peca)
    return anotado


def test_fala_curta_vai_pela_janela_curta_com_a_dica(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    assert transcritor.transcrever(tom(16000, 1.5), rapido=True, dica="Jarvis") == "Jarvis"
    assert modelo.chamadas == []  # a transcrição comum, de trinta segundos, nem foi chamada
    assert biblioteca.janelas == [escuta.JANELA_CURTA * 100]  # dez segundos de janela para um segundo e meio de fala
    assert biblioteca.fichas == [("fichas do modelo", True, "transcribe", "pt")]
    assert biblioteca.opcoes_da_voz == [{"min_silence_duration_ms": 160}]
    ouvido, comecos, opcoes = modelo.model.chamadas[0]
    assert ouvido == ("ouvido", (80, 150))
    assert comecos == [[1, 1000 + len(" Jarvis"), 2, 3, 4, 5]]  # a dica antes do começo, e sem marcas de tempo
    assert opcoes["beam_size"] == 5 and opcoes["return_scores"] and opcoes["return_no_speech_prob"]
    assert opcoes["max_length"] == len(comecos[0]) + escuta.MAX_DE_FICHAS
    # As fichas do modelo são montadas uma vez só.
    transcritor.transcrever(tom(16000, 1.0))
    assert len(biblioteca.fichas) == 1
    assert modelo.model.chamadas[1][1] == [[2, 3, 4, 5]]  # sem dica, nada antes do começo


def test_a_janela_curta_cresce_com_a_fala_e_so_leva_o_que_e_voz(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    audio = tom(16000, 12.0)
    biblioteca.falas = [{"start": 16000, "end": 16000 * 5}, {"start": 16000 * 6, "end": int(16000 * 10.5)}]
    assert transcritor.transcrever(audio) == "Jarvis"
    assert transcritor._modelo.ouvidos == [int(16000 * 8.5)]  # os dois trechos de voz, sem os silêncios
    assert biblioteca.janelas == [(9 + escuta.FOLGA_DA_JANELA) * 100]  # 8,5 s de fala: 9 e a folga


def test_sem_voz_no_audio_o_modelo_nem_e_chamado(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    biblioteca.falas = []
    assert transcritor.transcrever(tom(16000, 2.0), dica="Jarvis") == ""
    biblioteca.falas = [{"start": 0, "end": 1600}]  # um décimo de segundo de voz: estalo
    assert transcritor.transcrever(tom(16000, 2.0), dica="Jarvis") == ""
    assert transcritor._modelo.model.chamadas == [] and transcritor._modelo.chamadas == []


def test_o_que_o_proprio_modelo_diz_que_nao_e_fala_nao_vira_texto(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    modelo.model.saida = Saida([70, 71], nota=-2.4, mudo=0.9)  # com duas fichas, a nota vira -1,6
    assert transcritor.transcrever(tom(16000, 1.0), rapido=True, dica="Jarvis") == ""
    assert transcritor.transcrever(tom(16000, 1.0), dica="Jarvis") == ""
    assert modelo.chamadas == []
    # Só uma das duas coisas não basta: texto seguro num áudio que parece mudo, ou texto fraco num áudio de fala.
    modelo.model.saida = Saida([70, 71], nota=-0.3, mudo=0.9)
    assert transcritor.transcrever(tom(16000, 1.0), rapido=True) == "Jarvis"
    modelo.model.saida = Saida([70, 71], nota=-2.4, mudo=0.5)
    assert transcritor.transcrever(tom(16000, 1.0), rapido=True) == "Jarvis"


def test_texto_duvidoso_na_cuidadosa_e_refeito_pela_transcricao_comum(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    modelo.model.saida = Saida([80], nota=-2.1)  # com uma ficha, a nota vira -1,05: pouco provável
    assert transcritor.transcrever(tom(16000, 2.0), dica="Jarvis") == "que horas são?"
    assert len(modelo.chamadas) == 1 and modelo.chamadas[0]["hotwords"] == "Jarvis" and modelo.chamadas[0]["beam_size"] == 5
    assert transcritor._curta  # não foi falha: a janela curta segue valendo
    # Na procura rápida não há segunda tentativa: vale o que saiu.
    assert transcritor.transcrever(tom(16000, 2.0), rapido=True) == "que horas são?"
    assert len(modelo.chamadas) == 1
    # No limite ainda vale (a nota -1,0 não é "abaixo de -1,0").
    modelo.model.saida = Saida([80], nota=-2.0)
    assert transcritor.transcrever(tom(16000, 2.0)) == "que horas são?"
    assert len(modelo.chamadas) == 1


def test_texto_repetido_em_laco_na_cuidadosa_e_refeito_pela_transcricao_comum(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    modelo.model.saida = Saida([90] * 40, nota=-0.2)
    assert transcritor.transcrever(tom(16000, 2.0)) == "que horas são?"
    assert len(modelo.chamadas) == 1
    assert transcritor.transcrever(tom(16000, 2.0), rapido=True) == ("ha " * 40).strip()


def test_audio_comprido_vai_pela_transcricao_comum(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    assert transcritor.transcrever(tom(16000, escuta.MAX_DA_JANELA_CURTA + 0.5)) == "que horas são?"
    assert modelo.model.chamadas == [] and len(modelo.chamadas) == 1
    assert transcritor.transcrever(tom(16000, escuta.MAX_DA_JANELA_CURTA)) == "Jarvis"  # no limite ainda é curta
    assert len(modelo.model.chamadas) == 1 and len(modelo.chamadas) == 1


def test_falha_na_janela_curta_cai_na_transcricao_comum_e_nao_tenta_de_novo(tmp_path, biblioteca, caplog):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloCompleto)
    modelo = transcritor._modelo
    modelo.model.saida = TypeError("generate() mudou de assinatura")
    with caplog.at_level("ERROR"):
        assert transcritor.transcrever(tom(16000, 1.0), dica="Jarvis") == "que horas são?"
    assert "janela curta" in caplog.text and not transcritor._curta
    assert transcritor.transcrever(tom(16000, 1.0), dica="Jarvis") == "que horas são?"
    assert len(modelo.model.chamadas) == 1 and len(modelo.chamadas) == 2  # a segunda foi direto para a comum


def test_modelo_sem_as_pecas_da_janela_curta_usa_so_a_transcricao_comum(tmp_path, biblioteca):
    transcritor = escuta.Transcritor("small", tmp_path, fabrica=ModeloFalso)
    assert not transcritor._curta
    assert transcritor.transcrever(tom(16000, 1.0), rapido=True, dica="Jarvis") == "que horas são?"
    assert biblioteca.opcoes_da_voz == []

    class SemMotor(ModeloCompleto):
        def __init__(self, nome, **opcoes):
            super().__init__(nome, **opcoes)
            self.model = object()  # sem generate

    assert not escuta.Transcritor("small", tmp_path, fabrica=SemMotor)._curta
    for peca in ("hf_tokenizer", "feature_extractor", "encode"):
        falta = type("Falta", (ModeloCompleto,), {peca: property(lambda self: (_ for _ in ()).throw(AttributeError(peca)))})
        assert not escuta.Transcritor("small", tmp_path, fabrica=falta)._curta, peca
