import threading
import time

import pytest

np = pytest.importorskip("numpy")

from jarvis.voz import fala  # noqa: E402


class SomFalso:
    """Registra o que 'tocou'. Cada áudio leva `duracao` segundos, e parar() corta na hora."""

    def __init__(self, duracao: float = 0.0):
        self.tocados: list[str] = []
        self.duracao = duracao
        self._parar = threading.Event()

    def tocar(self, audio, taxa):
        self._parar.clear()
        self.tocados.append(audio)
        self._parar.wait(self.duracao)

    def parar(self):
        self._parar.set()


def sintetizar_falso(frase: str):
    return frase, 22050  # o "áudio" é a própria frase, para conferir a ordem


def test_fala_na_ordem_em_que_recebeu():
    som = SomFalso()
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    for frase in ("um", "dois", "três", "quatro"):
        falante.falar(frase)
    assert falante.esperar(5)
    assert som.tocados == ["um", "dois", "três", "quatro"]
    falante.fechar()


def test_interromper_cala_e_descarta_a_fila():
    som = SomFalso(duracao=5)
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    for frase in ("um", "dois", "três"):
        falante.falar(frase)
    time.sleep(0.2)  # "um" está tocando
    inicio = time.monotonic()
    falante.interromper()
    assert falante.esperar(2), "a fila não esvaziou depois da interrupção"
    assert time.monotonic() - inicio < 1
    assert som.tocados == ["um"]

    som.duracao = 0
    falante.falar("depois")  # continua funcionando depois de interromper
    assert falante.esperar(2)
    assert som.tocados == ["um", "depois"]
    falante.fechar()


def test_erro_de_sintese_nao_trava_a_fila():
    som = SomFalso()

    def sintetizar(frase):
        if frase == "defeito":
            raise RuntimeError("modelo falhou")
        return frase, 22050

    falante = fala.Falante(sintetizar, som.tocar, som.parar)
    for frase in ("antes", "defeito", "depois"):
        falante.falar(frase)
    assert falante.esperar(5)
    assert som.tocados == ["antes", "depois"]
    assert isinstance(falante.ultimo_erro, RuntimeError)
    falante.fechar()


def test_audio_vazio_e_pulado():
    som = SomFalso()
    falante = fala.Falante(lambda f: ("" if f == "nada" else f, 22050), som.tocar, som.parar)
    falante.falar("nada")
    falante.falar("algo")
    assert falante.esperar(5)
    assert som.tocados == ["algo"]
    falante.fechar()


def test_localizar_voz_aceita_arquivo_proprio(tmp_path):
    modelo = tmp_path / "minha.onnx"
    modelo.write_bytes(b"x")
    assert fala.localizar_voz(str(modelo), tmp_path) == modelo
    with pytest.raises(FileNotFoundError):
        fala.localizar_voz(str(tmp_path / "nao-existe.onnx"), tmp_path)


def test_localizar_voz_recusa_nome_invalido(tmp_path):
    with pytest.raises(ValueError, match="pt_BR-faber-medium"):
        fala.localizar_voz("faber", tmp_path)


def test_localizar_voz_baixa_so_o_que_falta(tmp_path, monkeypatch):
    baixados = []

    def baixar_falso(url, destino, avisar):
        baixados.append(url)
        destino.write_bytes(b"conteudo")

    monkeypatch.setattr(fala, "_baixar", baixar_falso)
    modelo = fala.localizar_voz("pt_BR-faber-medium", tmp_path, avisar=lambda _m: None)
    assert modelo == tmp_path / "pt_BR-faber-medium.onnx"
    assert baixados == [
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx?download=true",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json?download=true",
    ]
    fala.localizar_voz("pt_BR-faber-medium", tmp_path, avisar=lambda _m: None)
    assert len(baixados) == 2  # segunda vez: já está no disco


def test_download_interrompido_nao_deixa_arquivo_final(tmp_path, monkeypatch):
    class RespostaQueCai:
        headers = {"Content-Length": "100"}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, _n):
            raise ConnectionError("caiu")

    monkeypatch.setattr(fala.urllib.request, "urlopen", lambda *_a, **_k: RespostaQueCai())
    destino = tmp_path / "voz.onnx"
    with pytest.raises(ConnectionError):
        fala._baixar("https://exemplo/voz.onnx", destino, lambda _m: None)
    assert not destino.exists()


def test_quem_observa_recebe_cada_frase_e_o_fim_da_fala():
    som, eventos = SomFalso(), []
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.ao_tocar = lambda audio, taxa: eventos.append(("tocar", audio, taxa))
    falante.ao_calar = lambda: eventos.append("calou")
    falante.falar("um")
    falante.falar("dois")
    assert falante.esperar(5)
    time.sleep(0.05)
    assert [e for e in eventos if e != "calou"] == [("tocar", "um", 22050), ("tocar", "dois", 22050)]
    assert eventos[-1] == "calou" and eventos.index(("tocar", "um", 22050)) < eventos.index("calou")
    falante.fechar()


def test_interromper_avisa_que_calou_e_frase_descartada_nao_e_anunciada():
    som, eventos = SomFalso(duracao=5.0), []
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.ao_tocar = lambda audio, taxa: eventos.append(audio)
    falante.ao_calar = lambda: eventos.append("calou")
    falante.falar("longa")
    falante.falar("nunca toca")
    for _ in range(100):
        if eventos:
            break
        time.sleep(0.01)
    falante.interromper()
    assert falante.esperar(5)
    assert "nunca toca" not in eventos and eventos[0] == "longa" and "calou" in eventos
    falante.fechar()


def test_defeito_em_quem_observa_nao_atrapalha_a_fala():
    som = SomFalso()
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)

    def quebra(*_a):
        raise RuntimeError("painel fora do ar")

    falante.ao_tocar = falante.ao_calar = quebra
    falante.falar("um")
    falante.falar("dois")
    assert falante.esperar(5) and som.tocados == ["um", "dois"]
    falante.fechar()


def _esperar(condicao, tempo=3.0):
    limite = time.monotonic() + tempo
    while not condicao():
        assert time.monotonic() < limite, "não chegou ao ponto esperado"
        time.sleep(0.01)


def test_pausar_cala_sem_perder_o_fio_e_retomar_recomeca_da_frase_cortada():
    som = SomFalso(duracao=5)
    eventos = []
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.ao_tocar = lambda audio, taxa: eventos.append(("tocou", audio))
    falante.ao_calar = lambda: eventos.append(("calou",))
    for frase in ("um", "dois"):
        falante.falar(frase)
    _esperar(lambda: som.tocados == ["um"])
    inicio = time.monotonic()
    falante.pausar()  # alguém falou por cima
    time.sleep(0.4)
    assert som.tocados == ["um"] and falante.falando  # calado, mas com a fila guardada
    assert time.monotonic() - inicio < 1 and ("calou",) in eventos
    som.duracao = 0
    falante.retomar()  # não era nada: continua de onde parou
    assert falante.esperar(3)
    assert som.tocados == ["um", "um", "dois"]  # a frase cortada toca de novo, do começo
    falante.fechar()


def test_interromper_durante_a_pausa_descarta_tudo():
    som = SomFalso(duracao=5)
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    for frase in ("um", "dois"):
        falante.falar(frase)
    _esperar(lambda: som.tocados == ["um"])
    falante.pausar()
    falante.pausar()  # duas vezes é o mesmo que uma
    falante.interromper()  # era um chamado: a resposta antiga não volta
    assert falante.esperar(2) and som.tocados == ["um"]
    som.duracao = 0
    falante.falar("nova")  # e a fala seguinte não nasce pausada
    assert falante.esperar(2) and som.tocados == ["um", "nova"]
    falante.fechar()


def test_pausar_antes_de_a_frase_comecar_segura_a_fila():
    som = SomFalso()
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.pausar()
    falante.falar("um")
    time.sleep(0.3)
    assert som.tocados == [] and falante.falando
    falante.retomar()
    assert falante.esperar(2) and som.tocados == ["um"]
    falante.fechar()


def test_pausa_que_chega_no_instante_em_que_a_frase_comeca_ainda_cala():
    """O corte pode cair entre "vou tocar" e o som começar de fato: ele é conferido de novo logo depois."""
    paradas = []

    class SomAtrasado(SomFalso):
        def tocar(self, audio, taxa):
            time.sleep(0.02)  # o som demora um instante para sair
            super().tocar(audio, taxa)

        def parar(self):
            paradas.append(time.monotonic())
            super().parar()

    som = SomAtrasado(duracao=5)
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.falar("um")
    _esperar(lambda: falante._tocando)
    falante.pausar()  # antes de o som sair
    time.sleep(0.5)
    assert len(paradas) >= 2  # o primeiro corte não pegou nada; o reforço, sim
    som.duracao = 0
    falante.retomar()
    assert falante.esperar(3) and som.tocados == ["um", "um"]
    falante.fechar()


def test_frases_curtas_ficam_prontas_e_saem_sem_nova_sintese():
    som, sintetizadas = SomFalso(), []

    def sintetizar(frase):
        sintetizadas.append(frase)
        return frase, 22050

    falante = fala.Falante(sintetizar, som.tocar, som.parar)
    falante.preparar(["Sim?", "Pois não?"])  # de antemão, sem tocar
    _esperar(lambda: sintetizadas == ["Sim?", "Pois não?"])
    assert som.tocados == [] and not falante.falando
    falante.falar("Sim?")
    falante.falar("Abri o YouTube.")
    falante.falar("Abri o YouTube.")  # frase curta repetida também não é sintetizada de novo
    comprida = "Esta frase é comprida demais para valer a pena guardar, então ela é sintetizada toda vez."
    falante.falar(comprida)
    falante.falar(comprida)
    assert falante.esperar(3)
    assert som.tocados == ["Sim?", "Abri o YouTube.", "Abri o YouTube.", comprida, comprida]
    assert sintetizadas == ["Sim?", "Pois não?", "Abri o YouTube.", comprida, comprida]
    falante.fechar()


def test_falha_ao_preparar_nao_impede_de_falar():
    som, tentativas = SomFalso(), []

    def sintetizar(frase):
        tentativas.append(frase)
        if len(tentativas) == 1:
            raise RuntimeError("modelo ainda carregando")
        return frase, 22050

    falante = fala.Falante(sintetizar, som.tocar, som.parar)
    falante.preparar(["Sim?"])
    falante.falar("Sim?")
    assert falante.esperar(3) and som.tocados == ["Sim?"] and tentativas == ["Sim?", "Sim?"]
    falante.fechar()


# --- o volume do que está tocando (para a escuta não tomar a voz dele por gente) -----------------------


def _tom(segundos, amplitude, taxa=22050):
    t = np.arange(int(segundos * taxa)) / taxa
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype(np.int16)


def test_medir_volumes_usa_a_escala_do_microfone():
    alto, baixo = _tom(0.3, 16384), _tom(0.3, 1638)
    volumes = fala.medir_volumes(np.concatenate([alto, baixo]), 22050)
    assert len(volumes) == 21  # um valor a cada 30 ms; o último pedaço, incompleto, é completado com silêncio
    assert volumes[2] == pytest.approx(0.5 / np.sqrt(2), rel=0.05) and volumes[-3] == pytest.approx(0.05 / np.sqrt(2), rel=0.05)
    reais = fala.medir_volumes(np.concatenate([alto, baixo]).astype(np.float32) / 32768, 22050)
    assert reais == pytest.approx(volumes, rel=1e-3)  # áudio já em -1..1 dá o mesmo
    assert len(fala.medir_volumes(np.zeros(0, dtype=np.int16), 22050)) == 0


def test_volume_tocado_vale_o_ponto_mais_alto_desde_o_atraso_da_saida():
    volumes = fala.medir_volumes(np.concatenate([_tom(0.3, 16384), _tom(1.2, 1638), np.zeros(22050, dtype=np.int16)]), 22050)
    alto, baixo = 0.5 / np.sqrt(2), 0.05 / np.sqrt(2)
    assert fala.volume_tocado(volumes, 0.0) == pytest.approx(alto, rel=0.05)
    # O trecho alto acabou aos 0,3 s, mas o som demora a sair da caixa: ele ainda vale um pouco depois.
    assert fala.volume_tocado(volumes, 0.3 + fala.ATRASO_DA_SAIDA - 0.05) == pytest.approx(alto, rel=0.05)
    assert fala.volume_tocado(volumes, 0.3 + fala.ATRASO_DA_SAIDA + 0.1) == pytest.approx(baixo, rel=0.05)
    assert fala.volume_tocado(volumes, 1.5 + fala.ATRASO_DA_SAIDA + 0.1) == 0.0  # silêncio dentro da frase
    assert fala.volume_tocado(volumes, 30.0) == 0.0 and fala.volume_tocado(volumes[:0], 0.1) == 0.0
    chiado = fala.medir_volumes(_tom(1.0, 100), 22050)  # som que mal existe (0,002) é silêncio, não "ele falando"
    assert 0 < chiado.max() < fala.SOM_MINIMO and fala.volume_tocado(chiado, 0.5) == 0.0


def test_tocado_acompanha_a_frase_e_some_pouco_depois_de_ela_parar():
    som, agora = SomFalso(duracao=5), [50.0]
    falante = fala.Falante(lambda frase: (_tom(2.0, 16384), 22050), som.tocar, som.parar, relogio=lambda: agora[0])
    assert falante.tocado() == 0.0
    falante.falar("uma frase")
    _esperar(lambda: falante.tocado() > 0)
    agora[0] += 1.0
    assert falante.tocado() == pytest.approx(0.5 / np.sqrt(2), rel=0.05)
    falante.interromper()  # cortada no meio
    _esperar(lambda: not falante.falando)
    cheio = 0.5 / np.sqrt(2)
    agora[0] += fala.CAUDA_CHEIA - 0.02
    assert falante.tocado() == pytest.approx(cheio, rel=0.05)  # o som ainda está chegando ao microfone
    agora[0] += 0.02 + 2 * fala.MEIA_CAUDA
    assert falante.tocado() == pytest.approx(cheio / 4, rel=0.05)  # e vai morrendo depressa, para não tapar quem responde em seguida
    agora[0] += fala.CAUDA_DO_SOM - fala.CAUDA_CHEIA - 2 * fala.MEIA_CAUDA + 0.01
    assert falante.tocado() == 0.0  # passado o prazo, acabou: mesmo que a conta ainda desse algum volume
    assert cheio * 0.5 ** ((fala.CAUDA_DO_SOM - fala.CAUDA_CHEIA) / fala.MEIA_CAUDA) > fala.SOM_MINIMO
    falante.fechar()


def test_lembra_o_que_disse_ha_pouco_e_ha_quanto_tempo_se_calou():
    som, agora = SomFalso(), [50.0]
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar, relogio=lambda: agora[0])
    assert falante.ditas() == [] and falante.falou_ha() == float("inf")
    falante.falar("Hoje faz sol.")
    assert falante.falou_ha() == 0.0  # está falando
    assert falante.esperar(3)
    agora[0] += 5
    falante.falar("À tarde chove.")
    assert falante.esperar(3)
    assert falante.ditas() == ["Hoje faz sol.", "À tarde chove."]
    agora[0] += 1.5
    assert falante.falou_ha() == pytest.approx(1.5)
    agora[0] += 17
    assert falante.ditas() == ["À tarde chove."]  # o que passou de vinte segundos não conta mais
    falante.fechar()


def test_fala_baixa_some_do_microfone_antes_do_prazo():
    som, agora = SomFalso(), [50.0]
    falante = fala.Falante(lambda frase: (_tom(1.0, 1638), 22050), som.tocar, som.parar, relogio=lambda: agora[0])  # volume 0,035
    falante.falar("baixinho")
    assert falante.esperar(3)
    assert falante.tocado() == pytest.approx(0.05 / np.sqrt(2), rel=0.05)
    agora[0] += fala.CAUDA_CHEIA + 3 * fala.MEIA_CAUDA  # um oitavo do volume: já é silêncio, bem antes do prazo
    assert agora[0] - 50.0 < fala.CAUDA_DO_SOM and falante.tocado() == 0.0
    falante.fechar()


def test_audio_que_nao_da_para_medir_toca_do_mesmo_jeito():
    som = SomFalso()
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)  # o "áudio" aqui é um texto
    falante.falar("um")
    assert falante.esperar(3) and som.tocados == ["um"] and falante.tocado() == 0.0
    falante.fechar()


def test_pausa_que_ninguem_desfaz_acaba_sozinha():
    som = SomFalso(duracao=5)
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.falar("um")
    _esperar(lambda: som.tocados == ["um"])
    falante.pausar(maximo=0.3)  # quem pausou sumiu
    som.duracao = 0
    assert falante.esperar(3) and som.tocados == ["um", "um"]
    falante.fechar()


def test_pausa_vencida_nao_desfaz_uma_pausa_mais_nova():
    som = SomFalso(duracao=5)
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.falar("um")
    _esperar(lambda: som.tocados == ["um"])
    falante.pausar(maximo=0.3)
    falante.retomar()
    _esperar(lambda: som.tocados == ["um", "um"])
    falante.pausar(maximo=30)  # outra pausa, antes de a primeira vencer
    time.sleep(0.6)
    assert som.tocados == ["um", "um"] and falante.falando  # segue pausada
    falante.fechar()


# --- velocidade e troca de voz ------------------------------------------------------------------------


class SinteseQueVaria:
    """Uma voz de mentira que sabe mudar de velocidade: o "áudio" diz com que voz e velocidade foi feito."""

    def __init__(self, nome="a"):
        self.nome, self.velocidade, self.feitas = nome, 1.0, []

    def __call__(self, frase):
        self.feitas.append(frase)
        return f"{frase}|{self.nome}|{self.velocidade}", 22050


def test_mudar_a_velocidade_vale_da_proxima_frase_e_refaz_as_frases_prontas():
    som, sintese = SomFalso(), SinteseQueVaria()
    falante = fala.Falante(sintese, som.tocar, som.parar)
    falante.preparar(["Sim?", "Diga."])
    falante.falar("bom dia")
    assert falante.esperar(5) and som.tocados == ["bom dia|a|1.0"]
    _esperar(lambda: set(falante._prontas) == {"Sim?", "Diga.", "bom dia"})
    falante.velocidade(1.5)
    _esperar(lambda: falante._prontas.get("Sim?") == ("Sim?|a|1.5", 22050) and "Diga." in falante._prontas)
    assert "bom dia" not in falante._prontas  # o que estava guardado da velocidade antiga saiu
    falante.falar("Sim?")
    falante.falar("bom dia")
    assert falante.esperar(5) and som.tocados[1:] == ["Sim?|a|1.5", "bom dia|a|1.5"]
    # A mesma velocidade de novo não refaz nada.
    feitas = len(sintese.feitas)
    falante.velocidade(1.5)
    time.sleep(0.1)
    assert len(sintese.feitas) == feitas
    falante.fechar()


def test_voz_que_nao_sabe_variar_ignora_a_velocidade():
    som = SomFalso()
    falante = fala.Falante(sintetizar_falso, som.tocar, som.parar)
    falante.falar("oi")
    assert falante.esperar(5)
    falante.velocidade(1.5)  # não quebra, não apaga o que estava pronto
    assert "oi" in falante._prontas and not hasattr(sintetizar_falso, "velocidade")
    falante.fechar()


def test_trocar_de_voz_vale_da_proxima_frase_e_refaz_as_frases_prontas():
    som = SomFalso()
    falante = fala.Falante(SinteseQueVaria("a"), som.tocar, som.parar)
    falante.preparar(["Sim?"])
    _esperar(lambda: "Sim?" in falante._prontas)
    falante.trocar_sintese(SinteseQueVaria("b"))
    _esperar(lambda: falante._prontas.get("Sim?") == ("Sim?|b|1.0", 22050))
    falante.falar("bom dia")
    assert falante.esperar(5) and som.tocados == ["bom dia|b|1.0"]
    falante.fechar()


def test_frase_sintetizada_enquanto_a_voz_mudava_nao_fica_guardada():
    """A voz mudou com uma frase no meio da síntese: o que sair dela é da voz antiga e não pode ser reaproveitado."""
    som, entrou, seguir = SomFalso(), threading.Event(), threading.Event()

    class Lenta(SinteseQueVaria):
        def __call__(self, frase):
            if frase == "oi":
                entrou.set()
                seguir.wait(3)
            return super().__call__(frase)

    sintese = Lenta()
    falante = fala.Falante(sintese, som.tocar, som.parar)
    falante.falar("oi")
    assert entrou.wait(3)
    falante.velocidade(1.4)
    seguir.set()
    assert falante.esperar(5)
    assert "oi" not in falante._prontas
    falante.preparar(["Diga."])
    _esperar(lambda: falante._prontas.get("Diga.") == ("Diga.|a|1.4", 22050))
    falante.fechar()


def _piper_falso(monkeypatch, natural=1.0):
    import sys
    import types

    pedidos = []

    class Pedaco:
        def __init__(self, n):
            self.audio_int16_array = np.full(n, 7, dtype=np.int16)

    class VozDoPiper:
        config = types.SimpleNamespace(sample_rate=22050, length_scale=natural)

        @classmethod
        def load(cls, modelo):
            pedidos.append(("carregar", modelo))
            return cls()

        def synthesize(self, frase, syn_config=None):
            pedidos.append((frase, None if syn_config is None else syn_config.length_scale))
            return [Pedaco(3), Pedaco(2)] if frase else []

    piper = types.ModuleType("piper")
    piper.PiperVoice, piper.SynthesisConfig = VozDoPiper, lambda length_scale=None: types.SimpleNamespace(length_scale=length_scale)
    monkeypatch.setitem(sys.modules, "piper", piper)
    return pedidos


def test_sintese_do_piper_fala_na_velocidade_pedida(monkeypatch, tmp_path):
    pedidos = _piper_falso(monkeypatch)
    sintese = fala.sintetizador_piper(tmp_path / "voz.onnx")
    assert pedidos == [("carregar", tmp_path / "voz.onnx")] and sintese.velocidade == 1.0 and sintese.taxa == 22050
    audio, taxa = sintese("bom dia")
    assert taxa == 22050 and list(audio) == [7] * 5
    assert pedidos[-1] == ("bom dia", None)  # na velocidade natural, a voz fica como veio
    sintese.velocidade = 1.25
    sintese("bom dia")
    assert pedidos[-1] == ("bom dia", pytest.approx(0.8))  # mais depressa é cada som mais curto
    sintese.velocidade = 0.6
    sintese("bom dia")
    assert pedidos[-1] == ("bom dia", pytest.approx(1 / 0.6))
    for pedida, usada in ((9.0, fala.VELOCIDADE_MAXIMA), (0.01, fala.VELOCIDADE_MINIMA)):  # fora da régua, fica na ponta
        sintese.velocidade = pedida
        sintese("bom dia")
        assert pedidos[-1] == ("bom dia", pytest.approx(1 / usada))
    sintese.velocidade = 1.003  # diferença que ninguém ouve
    sintese("bom dia")
    assert pedidos[-1] == ("bom dia", None)
    vazio, _ = sintese("")
    assert len(vazio) == 0


def test_sintese_do_piper_respeita_a_velocidade_natural_da_voz(monkeypatch, tmp_path):
    pedidos = _piper_falso(monkeypatch, natural=1.2)  # uma voz que já vem mais lenta de fábrica
    sintese = fala.sintetizador_piper(tmp_path / "voz.onnx")
    sintese.velocidade = 1.5
    sintese("oi")
    assert pedidos[-1] == ("oi", pytest.approx(1.2 / 1.5))


def test_frase_preparada_enquanto_a_voz_mudava_e_refeita_na_voz_nova():
    som, entrou, seguir = SomFalso(), threading.Event(), threading.Event()

    class Lenta(SinteseQueVaria):
        def __call__(self, frase):
            na_entrada = self.velocidade  # a síntese vale a velocidade de quando começou
            if not entrou.is_set():
                entrou.set()
                seguir.wait(3)
            return f"{frase}|{self.nome}|{na_entrada}", 22050

    falante = fala.Falante(Lenta(), som.tocar, som.parar)
    falante.preparar(["Sim?"])
    assert entrou.wait(3)
    falante.velocidade(1.4)  # a síntese de "Sim?" na velocidade antiga ainda está em curso
    seguir.set()
    _esperar(lambda: "Sim?" in falante._prontas)
    time.sleep(0.1)
    assert falante._prontas["Sim?"] == ("Sim?|a|1.4", 22050)
    falante.fechar()


# --- retomar do meio e a saída de som que fica aberta ---------------------------------------------------------


def test_retomar_de_frase_comprida_volta_um_pouco_antes_de_onde_parou():
    taxa = 16000
    assert fala._retomar_de(0, 10 * taxa, 4.0, taxa) == int(3.7 * taxa)  # parou aos 4 s: volta aos 3,7 s
    assert fala._retomar_de(int(3.7 * taxa), 10 * taxa, 2.0, taxa) == int(5.4 * taxa)  # cortada de novo, mais adiante
    assert fala._retomar_de(0, 10 * taxa, 0.6, taxa) == 0  # cortada logo no começo: toca inteira
    assert fala._retomar_de(0, int(0.8 * taxa), 0.5, taxa) == 0  # frase curta: inteira
    assert fala._retomar_de(int(2 * taxa), 10 * taxa, 20.0, taxa) == 9 * taxa  # o relógio passou do fim: só o último segundo


def test_frase_comprida_cortada_por_uma_pausa_continua_de_onde_parou():
    class SomCronometrado(SomFalso):
        def __init__(self):
            super().__init__(duracao=5)
            self.tamanhos = []

        def tocar(self, audio, taxa):
            self.tamanhos.append(len(audio))
            self._parar.clear()
            self.tocados.append("frase")
            inicio = time.monotonic()
            self._parar.wait(self.duracao)
            self.tocou_por = time.monotonic() - inicio

    som = SomCronometrado()
    taxa = 16000
    audio = np.zeros(10 * taxa, dtype=np.int16)  # dez segundos de "fala"
    falante = fala.Falante(lambda f: (audio, taxa), som.tocar, som.parar)
    falante.falar("comprida")
    _esperar(lambda: som.tocados == ["frase"])
    time.sleep(1.2)
    falante.pausar()
    time.sleep(0.2)
    som.duracao = 0
    falante.retomar()
    assert falante.esperar(3)
    assert som.tocados == ["frase", "frase"] and som.tamanhos[0] == 10 * taxa
    # Recomeçou do meio: uns 1,2 s tocados menos a folga de 0,3 s, com alguma tolerância de relógio.
    assert int(0.6 * taxa) < 10 * taxa - som.tamanhos[1] < int(1.6 * taxa)
    falante.fechar()


class FluxoFalso:
    """Um OutputStream de mentira: guarda o que foi escrito e pode quebrar no meio."""

    criados: list = []

    def __init__(self, samplerate, channels, dtype, latency):
        self.taxa, self.escritos, self.aberto, self.quebra = samplerate, [], False, False
        FluxoFalso.criados.append(self)

    def start(self):
        self.aberto = True

    def write(self, dados):
        if self.quebra:
            raise OSError("dispositivo sumiu")
        self.escritos.append(len(dados))
        time.sleep(0.005)

    def abort(self):
        pass

    def close(self):
        self.aberto = False


class SdFalso:
    OutputStream = FluxoFalso


def test_saida_fica_aberta_entre_as_frases_e_para_no_pedaco_seguinte():
    FluxoFalso.criados.clear()
    saida = fala.SaidaDeSom(SdFalso())
    taxa = 16000
    saida.tocar(np.zeros(taxa, dtype=np.int16), taxa)
    saida.tocar(np.zeros(taxa // 2, dtype=np.int16), taxa)
    assert len(FluxoFalso.criados) == 1  # a mesma saída serviu às duas frases
    fluxo = FluxoFalso.criados[0]
    assert sum(fluxo.escritos) == taxa + taxa // 2 and fluxo.escritos[0] == int(taxa * fala.BLOCO_DA_SAIDA)
    # Parar no meio: o resto da frase não é escrito.
    fluxo.escritos.clear()
    pronto = threading.Event()

    def tocar_comprido():
        saida.tocar(np.zeros(10 * taxa, dtype=np.int16), taxa)
        pronto.set()

    threading.Thread(target=tocar_comprido, daemon=True).start()
    time.sleep(0.05)
    saida.parar()
    assert pronto.wait(2) and sum(fluxo.escritos) < 10 * taxa
    # Outra taxa de amostragem (outra voz): a saída é reaberta.
    saida.tocar(np.zeros(100, dtype=np.int16), 22050)
    assert len(FluxoFalso.criados) == 2 and not fluxo.aberto and FluxoFalso.criados[1].taxa == 22050
    saida.fechar()
    assert not FluxoFalso.criados[1].aberto


def test_saida_que_quebra_e_reaberta_uma_vez(caplog):
    FluxoFalso.criados.clear()
    saida = fala.SaidaDeSom(SdFalso())
    saida.tocar(np.zeros(100, dtype=np.int16), 16000)
    FluxoFalso.criados[0].quebra = True
    saida.tocar(np.zeros(100, dtype=np.int16), 16000)  # a primeira escrita falha: reabre e toca
    assert len(FluxoFalso.criados) == 2 and FluxoFalso.criados[1].escritos == [100] and "reabrindo" in caplog.text
    FluxoFalso.criados[1].quebra = True
    FluxoFalso.quebra_sempre = True
    original = FluxoFalso.__init__

    def quebrado(self, *a, **k):
        original(self, *a, **k)
        self.quebra = True

    FluxoFalso.__init__ = quebrado
    try:
        with pytest.raises(OSError):
            saida.tocar(np.zeros(100, dtype=np.int16), 16000)  # nem reaberta funciona: a falha sobe (o Falante a registra)
    finally:
        FluxoFalso.__init__ = original


def test_saida_de_som_de_verdade_usa_a_saida_aberta(monkeypatch):
    import sys
    import types

    falso = types.ModuleType("sounddevice")
    falso.OutputStream = FluxoFalso
    monkeypatch.setitem(sys.modules, "sounddevice", falso)
    FluxoFalso.criados.clear()
    tocar, parar = fala.saida_de_som()
    tocar(np.zeros(10, dtype=np.int16), 16000)
    parar()
    assert len(FluxoFalso.criados) == 1
