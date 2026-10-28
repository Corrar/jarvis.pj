"""A voz da nuvem (ElevenLabs): síntese pela API, a voz local de reserva, o crédito do mês e as falhas."""

import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

np = pytest.importorskip("numpy")

from jarvis import cli, config  # noqa: E402
from jarvis.voz import Voz, nuvem  # noqa: E402
from jarvis.voz.nuvem import ErroNuvem, SinteseNuvem  # noqa: E402

VOZES = {"voices": [{"voice_id": "AbCdEfGhIjKlMnOpQrSt", "name": "Marcos"}, {"voice_id": "ZyXwVuTsRqPoNmLkJiHg", "name": "Ana Clara"}]}
ASSINATURA = {"character_count": 1200, "character_limit": 10000}


def som(n=400):
    return np.arange(n, dtype="<i2").tobytes()


class Api:
    """A ElevenLabs de mentira: responde conforme a configuração e anota cada pedido."""

    def __init__(self, assinatura=ASSINATURA, vozes=VOZES, tts=(200, None), estoura=None):
        self.assinatura, self.vozes, self.tts, self.estoura = assinatura, vozes, tts, estoura
        self.pedidos = []

    def __call__(self, metodo, caminho, chave, corpo=None, espera=12.0):
        self.pedidos.append((metodo, caminho, chave, corpo))
        if self.estoura is not None:
            raise self.estoura
        if caminho == "/user/subscription":
            return (200, json.dumps(self.assinatura).encode()) if isinstance(self.assinatura, dict) else (self.assinatura, b"")
        if caminho == "/voices":
            return (200, json.dumps(self.vozes).encode()) if isinstance(self.vozes, dict) else (self.vozes, b"")
        assert caminho.startswith("/text-to-speech/")
        codigo, dados = self.tts
        return codigo, (som() if dados is None else dados)


class Reserva:
    def __init__(self):
        self.frases, self.velocidade = [], 1.0

    def __call__(self, frase):
        self.frases.append(frase)
        return np.zeros(100, dtype=np.int16), 22050


def _nuvem(tmp_path, api=None, voz="", relogio=None, hoje=None, **extra):
    reserva = Reserva()
    avisos = []
    s = SinteseNuvem(
        "chave-x", reserva, tmp_path / "dados", voz=voz, pedir_api=api or Api(), relogio=relogio or (lambda: 1000.0),
        hoje=hoje or (lambda: datetime(2026, 10, 9, 10, 0)), avisar=avisos.append, **extra,
    )  # fmt: skip
    return s, reserva, avisos


# --- preparar -------------------------------------------------------------------------------


def test_preparar_le_o_credito_e_acha_a_voz_pelo_nome(tmp_path):
    api = Api()
    s, _, _ = _nuvem(tmp_path, api, voz="ana clara")
    s.preparar()
    assert s.creditos == (1200, 10000) and s.voz_id == "ZyXwVuTsRqPoNmLkJiHg" and s.voz_nome == "Ana Clara"
    assert [c for _, c, *_ in api.pedidos] == ["/user/subscription", "/voices"] and api.pedidos[0][2] == "chave-x"
    assert "ligada: 0 de 9000 letras neste mês; a API conta 1200 de 10000 créditos usados, voz Ana Clara" in s.situacao()
    # Outra instância lê a voz guardada e não consulta a lista de novo.
    api2 = Api()
    s2, _, _ = _nuvem(tmp_path, api2, voz="ana clara")
    s2.preparar()
    assert s2.voz_id == "ZyXwVuTsRqPoNmLkJiHg" and [c for _, c, *_ in api2.pedidos] == ["/user/subscription"]
    # Voz pedida por ID: não precisa da lista. Sem voz pedida: a primeira da biblioteca.
    s3, _, _ = _nuvem(tmp_path / "b", Api(), voz="AbCdEfGhIjKlMnOpQrSt")
    s3.preparar()
    # O ID agora é conferido contra a biblioteca, e com isso o nome aparece no lugar do código.
    assert s3.voz_id == "AbCdEfGhIjKlMnOpQrSt" and s3.voz_nome == "Marcos"
    s4, _, _ = _nuvem(tmp_path / "c", Api())
    s4.preparar()
    assert (s4.voz_id, s4.voz_nome) == ("AbCdEfGhIjKlMnOpQrSt", "Marcos")


@pytest.mark.parametrize(
    ("api", "voz", "trecho"),
    [
        (Api(assinatura=401), "", "chave foi recusada"),
        (Api(assinatura=503), "", r"não respondeu \(503\)"),
        (Api(vozes=500), "", "não consegui listar as vozes"),
        (Api(vozes={"voices": []}), "", "biblioteca de vozes está vazia"),
        (Api(vozes={"x": 1}), "", "formato que não entendi"),
    ],
    ids=["401", "503", "vozes 500", "sem vozes", "formato"],
)
def test_preparar_explica_o_que_impede(tmp_path, api, voz, trecho):
    s, _, _ = _nuvem(tmp_path, api, voz=voz)
    with pytest.raises(ErroNuvem, match=trecho):
        s.preparar()


def test_voz_inexistente_usa_a_primeira_em_vez_de_desligar_a_nuvem(tmp_path):
    """Ajuste errado não vale parar o recurso inteiro: usa a primeira voz e conta o que houve."""
    alvo, _, avisos = _nuvem(tmp_path, Api(), voz="Joaquim")
    alvo.preparar()
    assert alvo.voz_nome == "Marcos", "assumiu a primeira da biblioteca"
    assert "Não há voz 'Joaquim'" in " ".join(avisos) and "As suas: Marcos" in " ".join(avisos)
    assert not alvo.desligada, "a nuvem continua ligada"


def test_a_dica_do_tamanho_so_aparece_para_quem_tentou_por_um_id(tmp_path):
    """Para um nome ('Joaquim') a dica dos 20 caracteres confundiria em vez de ajudar."""
    for pedida, tem_dica in (("ZuQ7cXwwlRcQDd4yDWBoja", True), ("Joaquim", False), ("Ana Clara Dois", False)):
        alvo, _, avisos = _nuvem(tmp_path, Api(), voz=pedida)
        alvo.preparar()
        assert ("caracteres e os ID de lá têm 20" in " ".join(avisos)) is tem_dica, pedida


def test_sem_chave(tmp_path):
    s = SinteseNuvem("  ", Reserva(), tmp_path, pedir_api=Api())
    with pytest.raises(ErroNuvem, match="sem chave"):
        s.preparar()


def test_assinatura_em_formato_estranho_nao_impede(tmp_path):
    s, _, _ = _nuvem(tmp_path, Api(assinatura={"x": 1}))
    s.preparar()
    assert s.creditos is None and s.voz_id


# --- sintetizar ----------------------------------------------------------------------------------


def test_sintetiza_pela_nuvem_conta_as_letras_e_guarda_a_frase_curta(tmp_path):
    api = Api()
    s, reserva, _ = _nuvem(tmp_path, api)
    s.preparar()
    s.velocidade = 1.1
    audio, taxa = s("Sim, mestre?")
    assert taxa == 22050 and audio.dtype == np.int16 and len(audio) == 400 and reserva.frases == [] and reserva.velocidade == 1.1
    metodo, caminho, chave, corpo = api.pedidos[-1]
    assert (metodo, caminho) == ("POST", "/text-to-speech/AbCdEfGhIjKlMnOpQrSt?output_format=pcm_22050")
    assert corpo == {"text": "Sim, mestre?", "model_id": "eleven_flash_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.1}}
    assert s.gastas == 12 and s.creditos == (1212, 10000) and s.ultimo_motivo == ""
    gravado = json.loads((tmp_path / "dados" / "elevenlabs.json").read_text())
    assert gravado["gastas"] == 12 and gravado["mes"] == "2026-10"
    # A mesma frase curta de novo: sai do disco, sem pedido e sem gasto.
    pedidos = len(api.pedidos)
    audio2, _ = s("Sim, mestre?")
    assert len(api.pedidos) == pedidos and s.gastas == 12 and np.array_equal(audio, audio2)
    # Frase comprida não é guardada.
    comprida = "x" * 60
    s(comprida)
    s(comprida)
    assert len(api.pedidos) == pedidos + 2 and s.gastas == 132
    # Velocidade fora do que a API aceita é aparada.
    s.velocidade = 2.0
    s("outra")
    assert api.pedidos[-1][3]["voice_settings"]["speed"] == 1.2


def test_credito_do_mes_e_virada_do_mes(tmp_path):
    hoje = [datetime(2026, 10, 9)]
    s, reserva, _ = _nuvem(tmp_path, Api(), hoje=lambda: hoje[0], limite=20)
    s.preparar()
    s("uma frase de 25 letras..")  # 25 > 20: nem tenta
    assert reserva.frases == ["uma frase de 25 letras.."] and s.ultimo_motivo == "crédito do mês acabou" and s.gastas == 0
    s2, reserva2, _ = _nuvem(tmp_path / "b", Api(), hoje=lambda: hoje[0], limite=20)
    s2.preparar()
    s2("doze letras!")
    s2("doze letras!!")  # 12 + 13 > 20: a segunda sai local
    assert reserva2.frases == ["doze letras!!"] and s2.gastas == 12 and s2.ultimo_motivo == "crédito do mês acabou"
    hoje[0] = datetime(2026, 11, 1)
    s2("de novo")
    assert reserva2.frases == ["doze letras!!"] and s2.gastas == 7 and s2.mes == "2026-11"  # mês novo: a nuvem volta
    # A conta da própria API também segura.
    s3, reserva3, _ = _nuvem(tmp_path / "c", Api(assinatura={"character_count": 10000, "character_limit": 10000}))
    s3.preparar()
    s3("oi")
    assert reserva3.frases == ["oi"] and not s3.ativa


def test_falhas_da_api(tmp_path):
    relogio = [1000.0]
    api = Api(tts=(500, b'{"detail": {"message": "deu ruim"}}'))
    s, reserva, avisos = _nuvem(tmp_path, api, relogio=lambda: relogio[0])
    s.preparar()
    for i in range(3):
        s(f"frase {i}")
    assert reserva.frases == ["frase 0", "frase 1", "frase 2"] and s.ultimo_motivo == "resposta 500: deu ruim"
    assert not s.ativa and any("falhou 3 vezes seguidas" in a for a in avisos) and s.gastas == 0
    api.tts = (200, None)
    s("ainda dormindo")
    assert reserva.frases[-1] == "ainda dormindo" and s.ultimo_motivo == "descansando depois de falhas"
    relogio[0] += nuvem.DESCANSO + 1
    s("acordou")
    assert reserva.frases[-1] == "ainda dormindo" and s.gastas == 7
    # A rede caiu: a frase sai local, sem levantar.
    api.estoura = OSError("sem rede")
    s("sem rede")
    assert reserva.frases[-1] == "sem rede" and "não respondeu (OSError)" in s.ultimo_motivo


def test_chave_recusada_desliga_e_quota_segura_ate_o_mes_virar(tmp_path):
    api = Api(tts=(401, b'{"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}'))
    s, reserva, avisos = _nuvem(tmp_path, api)
    s.preparar()
    s("oi")
    assert reserva.frases == ["oi"] and s.desligada.startswith("chave recusada (401)") and "desligada" in s.situacao()
    s("de novo")
    assert len(api.pedidos) == 3  # não tenta mais
    api2 = Api(tts=(429, b'{"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota"}}'))
    s2, reserva2, avisos2 = _nuvem(tmp_path / "b", api2)
    s2.preparar()
    s2("oi")
    assert reserva2.frases == ["oi"] and s2.gastas == s2.limite and any("crédito do mês" in a for a in avisos2) and not s2.ativa


def test_motivos():
    assert nuvem._motivo(422, b'{"detail": {"message": "voice not found"}}') == "pedido recusado (422): voice not found"
    assert nuvem._motivo(429, b"nada json") == "limite da ElevenLabs (429): nada json"
    assert nuvem._motivo(502, b"") == "resposta 502"
    assert nuvem._motivo(400, b'{"detail": "texto solto"}') == "resposta 400: texto solto"


def test_registro_estragado_e_guardada_estragada(tmp_path, caplog):
    (tmp_path / "dados").mkdir()
    (tmp_path / "dados" / "elevenlabs.json").write_text("{", encoding="utf-8")
    s, _, _ = _nuvem(tmp_path)
    assert s.gastas == 0 and "Não consegui ler" in caplog.text
    s.preparar()
    arquivo = s._chave_da_guardada("Sim?", 1.0)
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(b"x")  # menos de uma amostra
    audio, _ = s("Sim?")
    assert len(audio) == 400  # ignorou o lixo e sintetizou


def test_registro_de_outro_mes_zera(tmp_path):
    (tmp_path / "dados").mkdir()
    (tmp_path / "dados" / "elevenlabs.json").write_text(json.dumps({"mes": "2026-09", "gastas": 8000, "voz_pedida": "", "voz_id": "AbCdEfGhIjKlMnOpQrSt", "voz_nome": "Marcos"}))
    s, _, _ = _nuvem(tmp_path)
    assert s.gastas == 0 and s.mes == "2026-10" and s.voz_id == "AbCdEfGhIjKlMnOpQrSt"


# --- na Voz e na configuração ----------------------------------------------------------------------


def test_voz_carrega_a_nuvem_por_cima_da_local(tmp_path, monkeypatch):
    import sys
    import types

    from jarvis.voz import escuta, fala

    for nome in ("sounddevice", "piper", "faster_whisper"):
        if nome not in sys.modules:
            monkeypatch.setitem(sys.modules, nome, types.ModuleType(nome))

    class Sintese:
        def __init__(self, modelo):
            self.modelo, self.velocidade = modelo, 1.0

        def __call__(self, frase):
            return np.zeros(10, dtype=np.int16), 22050

    class Transcritor:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(fala, "localizar_voz", lambda voz, pasta, avisar: tmp_path / f"{voz}.onnx")
    monkeypatch.setattr(fala, "sintetizador_piper", Sintese)
    monkeypatch.setattr(fala, "saida_de_som", lambda: (lambda a, t: None, lambda: None))
    monkeypatch.setattr(escuta, "Transcritor", Transcritor)
    monkeypatch.setattr(escuta, "Gravador", lambda: object())
    monkeypatch.setattr(nuvem, "pedir", Api())
    avisos = []
    voz = Voz.carregar("pt_BR-faber-medium", "small", tmp_path, avisar=avisos.append, nuvem={"chave": "k", "voz": "marcos", "modelo": "flash", "limite": 500})
    assert voz.nuvem is not None and voz.nuvem.voz_nome == "Marcos" and voz._falante._sintetizar is voz.nuvem
    assert any("Voz da nuvem (ElevenLabs) ligada" in a for a in avisos)
    # Trocar a voz local no painel: ela vira a reserva, e a nuvem continua por cima.
    (tmp_path / "vozes").mkdir()
    (tmp_path / "vozes" / "pt_BR-cadu-medium.onnx").write_bytes(b"x")
    (tmp_path / "vozes" / "pt_BR-cadu-medium.onnx.json").write_text("{}")
    voz.trocar_voz("pt_BR-cadu-medium")
    assert voz._falante._sintetizar is voz.nuvem and voz.nuvem.reserva.modelo.name == "pt_BR-cadu-medium.onnx"
    # Sem chave: nada da nuvem.
    sem = Voz.carregar("pt_BR-faber-medium", "small", tmp_path, avisar=avisos.append)
    assert sem.nuvem is None
    # Chave recusada: avisa e segue local.
    monkeypatch.setattr(nuvem, "pedir", Api(assinatura=401))
    avisos.clear()
    com_erro = Voz.carregar("pt_BR-faber-medium", "small", tmp_path, avisar=avisos.append, nuvem={"chave": "k"})
    assert com_erro.nuvem.desligada and com_erro._falante._sintetizar is com_erro.nuvem  # fica por cima, desligada: tudo sai pela reserva
    assert isinstance(com_erro.nuvem.reserva, Sintese) and len(com_erro.nuvem("oi")[0]) == 10
    assert any("desligada: a chave foi recusada" in a for a in avisos)


def test_configuracao(tmp_path):
    base = {"ANTHROPIC_API_KEY": "k", "JARVIS_PASTAS": str(tmp_path)}
    cfg = config.carregar(base, tmp_path)
    assert (cfg.elevenlabs_chave, cfg.elevenlabs_voz, cfg.elevenlabs_modelo, cfg.elevenlabs_limite) == ("", "", "flash", 9000)
    cfg = config.carregar({**base, "JARVIS_ELEVENLABS_KEY": " sk ", "JARVIS_ELEVENLABS_VOZ": "Ana", "JARVIS_ELEVENLABS_MODELO": "Multilingual", "JARVIS_ELEVENLABS_LIMITE": "5.000"}, tmp_path)
    assert (cfg.elevenlabs_chave, cfg.elevenlabs_voz, cfg.elevenlabs_modelo, cfg.elevenlabs_limite) == ("sk", "Ana", "multilingual", 5000)
    with pytest.raises(config.ErroConfig, match="JARVIS_ELEVENLABS_MODELO"):
        config.carregar({**base, "JARVIS_ELEVENLABS_MODELO": "premium"}, tmp_path)
    with pytest.raises(config.ErroConfig, match="JARVIS_ELEVENLABS_LIMITE"):
        config.carregar({**base, "JARVIS_ELEVENLABS_LIMITE": "muito"}, tmp_path)
    with pytest.raises(config.ErroConfig, match="negativo"):
        config.carregar({**base, "JARVIS_ELEVENLABS_LIMITE": "-1"}, tmp_path)


def test_carregar_voz_passa_a_nuvem_so_com_chave(tmp_path):
    from test_ativacao import cfg

    recebidos = []

    def carregar(voz, whisper, pasta, whisper_da_vigilia="", **extra):
        recebidos.append(extra)
        return "voz"

    cli.carregar_voz(cfg(tmp_path), carregar_modelos=carregar)
    cli.carregar_voz(cfg(tmp_path, elevenlabs_chave="k", elevenlabs_voz="Ana"), carregar_modelos=carregar)
    assert recebidos == [{}, {"nuvem": {"chave": "k", "voz": "Ana", "modelo": "flash", "limite": 9000}}]


def test_chave_restrita_sem_ler_a_assinatura_ainda_fala(tmp_path):
    api = Api(assinatura=None)
    restrita = b'{"detail": {"status": "missing_permissions", "message": "The API key you used is missing the permission user_read"}}'

    def pedir(metodo, caminho, chave, corpo=None, espera=6.0):
        api.pedidos.append((metodo, caminho, chave, corpo))
        if caminho in ("/user/subscription", "/voices"):
            return 401, restrita
        return 200, som()

    s, reserva, _ = _nuvem(tmp_path, pedir, voz="AbCdEfGhIjKlMnOpQrSt")
    s.preparar()
    assert s.creditos is None and s.voz_id == "AbCdEfGhIjKlMnOpQrSt" and not s.desligada
    audio, _ = s("oi")
    assert reserva.frases == [] and len(audio) == 400
    sem_id, _, _ = _nuvem(tmp_path / "b", pedir, voz="Marcos")
    with pytest.raises(ErroNuvem, match="ponha o ID da voz"):
        sem_id.preparar()


def test_testar_conta_o_que_aconteceu(tmp_path):
    s, reserva, _ = _nuvem(tmp_path, Api())
    s.desligada = "x"
    assert s.testar().startswith("Funcionou: ") and "ligada" in s.testar() and reserva.frases == []
    s2, reserva2, _ = _nuvem(tmp_path / "b", Api(tts=(500, b"")))
    assert "saiu pela voz local: resposta 500" in s2.testar()
    s3, _, _ = _nuvem(tmp_path / "c", Api(assinatura=401))
    assert s3.testar().startswith("Não deu: a chave foi recusada") and s3.desligada


def test_comando_nuvem(tmp_path, monkeypatch, capsys):
    import asyncio

    from test_ativacao import Cliente, SessaoFalsa, VozFalsa, cfg
    from jarvis.memoria import Memoria
    from jarvis.painel import MUDO
    from jarvis.registro import Registro

    fila = asyncio.Queue()

    async def ler(_prompt):
        return await fila.get()

    monkeypatch.setattr(cli, "ler_linha", ler)

    class Nuvem:
        def testar(self):
            return "Funcionou: teste."

    async def cena(voz):
        monkeypatch.setattr(cli, "ClaudeSDKClient", Cliente())
        laco = cli._laco_da_conversa(cfg(tmp_path), Memoria(tmp_path / "m"), Registro(":memory:"), voz, SessaoFalsa(), None, MUDO)
        conversa = asyncio.ensure_future(laco)
        fila.put_nowait("/nuvem")
        fila.put_nowait("sair")
        await asyncio.wait_for(conversa, 5)

    asyncio.run(cena(None))
    assert "Precisa do modo de voz" in capsys.readouterr().out
    voz = VozFalsa()
    voz.nuvem = None
    asyncio.run(cena(voz))
    assert "JARVIS_ELEVENLABS_KEY" in capsys.readouterr().out
    voz.nuvem = Nuvem()
    asyncio.run(cena(voz))
    assert "Funcionou: teste." in capsys.readouterr().out


# --- o ID torto (caso real: dois caracteres a mais na cópia) -------------------------------


def _nuvem_com(respostas, voz_pedida="", avisos=None):
    """Uma nuvem cujas respostas da API são dadas por uma função (caminho -> (código, corpo))."""
    def pedir(metodo, caminho, chave, corpo=None, espera=30.0):
        return respostas(metodo, caminho)

    import numpy as np

    return SinteseNuvem(
        chave="sk_teste",
        reserva=lambda _f: (np.zeros(10, dtype="<i2"), 22050),
        pasta=Path(tempfile.mkdtemp()),
        voz=voz_pedida,
        pedir_api=pedir,
        avisar=(avisos.append if avisos is not None else (lambda _t: None)),
    )


def test_id_com_tamanho_errado_nao_passa_por_id():
    """O que aconteceu de verdade: 22 caracteres foram aceitos como ID e nunca conferidos."""
    assert nuvem._ID_DE_VOZ.match("ZuQ7cXwwlRcQDd4yDWBoja") is None, "22 caracteres não é ID da ElevenLabs"
    assert nuvem._ID_DE_VOZ.match("21m00Tcm4TlvDq8ikWAM") is not None


def test_voz_que_nao_existe_e_trocada_pela_primeira_da_biblioteca():
    """Um ID errado derrubava TODA frase para a voz local, para sempre, sem avisar."""
    chamadas, avisos = [], []

    def respostas(metodo, caminho):
        chamadas.append(caminho)
        if caminho == "/user/subscription":
            return 200, json.dumps({"character_count": 0, "character_limit": 10000}).encode()
        if caminho == "/voices":
            return 200, json.dumps({"voices": [{"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"}]}).encode()
        if "ZuQ7cXwwlRcQDd4yDWBoja" in caminho:
            return 404, b'{"detail":{"message":"A voice with voice_id ZuQ7cXwwlRcQDd4yDWBoja was not found."}}'
        return 200, b"\x00\x01" * 800

    alvo = _nuvem_com(respostas, voz_pedida="ZuQ7cXwwlRcQDd4yDWBoja", avisos=avisos)
    alvo.voz_id, alvo.voz_pedida = "ZuQ7cXwwlRcQDd4yDWBoja", "ZuQ7cXwwlRcQDd4yDWBoja"
    alvo("primeira frase")  # cai na voz local, mas já troca a voz
    assert alvo.voz_id == "21m00Tcm4TlvDq8ikWAM" and alvo.voz_nome == "Rachel"
    assert any("não existe na sua conta" in a for a in avisos), avisos
    audio, _ = alvo("segunda frase")  # e a seguinte já sai pela nuvem
    assert len(audio) > 0 and not alvo.ultimo_motivo


def test_voz_inexistente_nao_desliga_a_nuvem_para_sempre():
    """Sem a troca, o 404 contava como falha e levava ao descanso de cinco minutos."""
    def respostas(metodo, caminho):
        if caminho == "/voices":
            return 200, json.dumps({"voices": [{"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"}]}).encode()
        if "errada" in caminho:
            return 404, b'{"detail":{"message":"A voice with voice_id errada was not found."}}'
        return 200, b"\x00\x01" * 800

    alvo = _nuvem_com(respostas)
    alvo.voz_id = "errada"
    alvo("frase")
    assert alvo.dormindo_ate == 0.0 and not alvo.desligada
