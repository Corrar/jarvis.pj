import pytest

from jarvis.voz import ErroVoz, Voz


class FalanteFalso:
    def __init__(self):
        self.frases, self.interrupcoes = [], 0

    def falar(self, frase):
        self.frases.append(frase)

    def interromper(self):
        self.interrupcoes += 1

    def esperar(self, tempo=None):
        return True

    def fechar(self):
        pass


class GravadorFalso:
    def __init__(self, erro=None):
        self.erro, self.eventos = erro, []

    def comecar(self):
        if self.erro:
            raise self.erro
        self.eventos.append("comecar")

    def terminar(self):
        self.eventos.append("terminar")
        return "audio"


class TranscritorFalso:
    def __init__(self, texto="que horas são"):
        self.texto = texto

    def transcrever(self, audio):
        assert audio == "audio"
        return self.texto


def montar(**pecas):
    falante = pecas.get("falante", FalanteFalso())
    return Voz(falante, pecas.get("gravador", GravadorFalso()), pecas.get("transcritor", TranscritorFalso())), falante


def test_narrar_fala_cada_frase_quando_ela_fecha():
    voz, falante = montar()
    voz.narrar("São nove ho")
    assert falante.frases == []
    voz.narrar("ras. O dólar está em R$ 4,98")
    assert falante.frases == ["São nove horas."]
    voz.fim_de_bloco()
    assert falante.frases == ["São nove horas.", "O dólar está em 4,98 reais"]


def test_lista_em_markdown_e_falada_sem_os_marcadores():
    voz, falante = montar()
    voz.narrar("Achei três grupos:\n- **Relatórios** da Royale\n- Pedidos\n")
    voz.fim_de_bloco()
    assert falante.frases == ["Achei três grupos:", "Relatórios da Royale", "Pedidos"]


def test_pedaco_so_com_simbolos_nao_e_falado():
    voz, falante = montar()
    voz.narrar("---\n")
    voz.fim_de_bloco()
    assert falante.frases == []


def test_interromper_descarta_a_frase_pela_metade():
    voz, falante = montar()
    voz.narrar("Isto ia ser uma frase lon")
    voz.interromper()
    voz.narrar("Outra coisa.")
    voz.fim_de_bloco()
    assert falante.frases == ["Outra coisa."]
    assert falante.interrupcoes == 1


def test_gravar_cala_a_fala_antes_de_abrir_o_microfone():
    gravador = GravadorFalso()
    voz, falante = montar(gravador=gravador)
    voz.comecar_gravacao()
    assert falante.interrupcoes == 1 and gravador.eventos == ["comecar"]
    assert voz.terminar_gravacao() == "que horas são"


def test_microfone_indisponivel_vira_erro_de_voz():
    voz, _ = montar(gravador=GravadorFalso(erro=OSError("No Default Input Device")))
    with pytest.raises(ErroVoz, match="microfone"):
        voz.comecar_gravacao()


def test_falta_de_biblioteca_explica_o_que_instalar(tmp_path, monkeypatch):
    import builtins

    importar = builtins.__import__

    def sem_piper(nome, *a, **k):
        if nome == "piper":
            raise ImportError("No module named 'piper'", name="piper")
        return importar(nome, *a, **k)

    monkeypatch.setattr(builtins, "__import__", sem_piper)
    with pytest.raises(ErroVoz, match="requirements-voz.txt"):
        Voz.carregar("pt_BR-faber-medium", "small", tmp_path, avisar=lambda _m: None)


# --- os ajustes de voz do painel ------------------------------------------------------------------------


def test_muda_ele_nao_fala_nada_e_religada_volta_a_falar():
    voz, falante = montar()
    voz.narrar("Bom dia. ")
    assert falante.frases == ["Bom dia."]
    voz.emudecer(True)
    assert voz.muda and falante.interrupcoes == 1  # o que estava falando é cortado
    voz.narrar("São nove horas. ")
    voz.fim_de_bloco()
    voz.dizer("Sim?")
    assert falante.frases == ["Bom dia."]
    voz.emudecer(False)
    assert not voz.muda and falante.interrupcoes == 1
    voz.dizer("Sim?")
    voz.narrar("Pronto. ")
    assert falante.frases == ["Bom dia.", "Sim?", "Pronto."]


def test_velocidade_vai_para_o_falante_que_souber_variar():
    voz, falante = montar()
    voz.velocidade(1.3)  # o falante de mentira não sabe: não quebra

    class FalanteQueVaria(FalanteFalso):
        def velocidade(self, valor):
            self.pedida = valor

    voz, falante = montar(falante=FalanteQueVaria())
    voz.velocidade(1.3)
    assert falante.pedida == 1.3


def _com_vozes(tmp_path, monkeypatch, *nomes):
    from jarvis.voz import fala

    pasta = tmp_path / "vozes"
    pasta.mkdir()
    for nome in ("pt_BR-faber-medium", *nomes):
        (pasta / f"{nome}.onnx").write_bytes(b"modelo")
        (pasta / f"{nome}.onnx.json").write_text("{}")
    carregadas = []

    class Sintese:
        def __init__(self, modelo):
            if "quebrada" in modelo.name:
                raise OSError("arquivo corrompido")
            self.modelo, self.velocidade = modelo, 1.0
            carregadas.append(modelo.stem)

    monkeypatch.setattr(fala, "sintetizador_piper", Sintese)

    class FalanteQueTroca(FalanteFalso):
        sintese = None

        def trocar_sintese(self, sintese):
            self.sintese = sintese

        def velocidade(self, valor):
            pass

    voz, falante = montar(falante=FalanteQueTroca())
    voz._pasta_de_vozes, voz._voz_padrao = pasta, pasta / "pt_BR-faber-medium.onnx"
    return voz, falante, carregadas, pasta


def test_vozes_lista_as_outras_vozes_baixadas_e_completas(tmp_path, monkeypatch):
    voz, _, _, pasta = _com_vozes(tmp_path, monkeypatch, "pt_BR-cadu-medium", "pt_BR-jeff-medium")
    (pasta / "pt_BR-meia-medium.onnx").write_bytes(b"modelo")  # sem o .json: download pela metade
    (pasta / "pt_BR-vazia-medium.onnx").write_bytes(b"")
    (pasta / "pt_BR-vazia-medium.onnx.json").write_text("{}")
    (pasta / "leia-me.txt").write_text("não é voz")
    assert voz.vozes() == ["pt_BR-cadu-medium", "pt_BR-jeff-medium"]  # a de sempre não entra
    sem_pasta, _ = montar()
    assert sem_pasta.vozes() == [] and sem_pasta.voz_atual == ""


def test_trocar_de_voz_carrega_a_outra_na_velocidade_em_uso_e_volta_para_a_de_sempre(tmp_path, monkeypatch):
    voz, falante, carregadas, pasta = _com_vozes(tmp_path, monkeypatch, "pt_BR-cadu-medium")
    voz.velocidade(1.2)
    voz.trocar_voz("pt_BR-cadu-medium")
    assert voz.voz_atual == "pt_BR-cadu-medium" and carregadas == ["pt_BR-cadu-medium"]
    assert falante.sintese.modelo == pasta / "pt_BR-cadu-medium.onnx" and falante.sintese.velocidade == 1.2
    voz.trocar_voz("pt_BR-cadu-medium")  # a mesma: nada a carregar
    assert carregadas == ["pt_BR-cadu-medium"]
    voz.trocar_voz("")
    assert voz.voz_atual == "" and carregadas[-1] == "pt_BR-faber-medium" and falante.sintese.modelo == pasta / "pt_BR-faber-medium.onnx"


def test_trocar_para_voz_que_nao_existe_ou_nao_carrega_mantem_a_que_estava(tmp_path, monkeypatch):
    voz, falante, carregadas, _ = _com_vozes(tmp_path, monkeypatch, "pt_BR-quebrada-medium")
    for nome in ("pt_BR-inventada-medium", "../../segredo", "pt_BR-faber-medium"):  # a de sempre se pede com ""
        with pytest.raises(ErroVoz, match="baixada"):
            voz.trocar_voz(nome)
    with pytest.raises(ErroVoz, match="arquivo corrompido"):
        voz.trocar_voz("pt_BR-quebrada-medium")
    assert voz.voz_atual == "" and carregadas == [] and falante.sintese is None
    sem_pasta, _ = montar()
    with pytest.raises(ErroVoz):
        sem_pasta.trocar_voz("pt_BR-cadu-medium")
