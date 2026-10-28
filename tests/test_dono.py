"""A voz do dono: cadastro, conferência, aprendizado, o modelo e o /minha-voz."""

import asyncio
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # a voz é opcional: sem numpy (sem requirements-voz.txt) estes testes não valem

from jarvis import cli
from jarvis.config import carregar
from jarvis.painel import MUDO
from jarvis.voz import ErroVoz, Voz
from jarvis.voz import dono as modulo
from jarvis.voz.dono import APRENDER, FRASES_DE_CADASTRO, ErroDono, VozDoDono
from jarvis.voz.escuta import TAXA


def _som(segundos, semente=0):
    return np.random.default_rng(semente).standard_normal(int(segundos * TAXA)).astype(np.float32) * 0.1


class Extrator:
    """Um modelo de mentira: a "assinatura" é a direção dada pela média e pelo desvio do áudio, com a duração junto."""

    def __init__(self):
        self.chamadas = 0

    def __call__(self, som):
        self.chamadas += 1
        base = np.array([float(som.mean()) * 1000, float(som.std()) * 10, 1.0, 0.3], dtype=np.float32)
        return base


def _voz(tmp_path, limiar=0.4, modelo_presente=True, baixar=None, extrator=None, relogio=None):
    pasta = tmp_path / "modelos"
    if modelo_presente:
        pasta.mkdir(exist_ok=True)
        (pasta / modulo.MODELO).write_bytes(b"x")
    chamadas = []

    def fabrica(caminho):
        chamadas.append(caminho)
        return extrator or Extrator()

    dono = VozDoDono(pasta, tmp_path / "voz-do-dono.npz", limiar, extrator=fabrica, baixar_modelo=baixar or (lambda *a: None), relogio=relogio or (lambda: 0.0))
    return dono, chamadas


# --- cadastro e conferência --------------------------------------------------------------------


def test_sem_cadastro_todo_mundo_e_o_dono(tmp_path):
    dono, _ = _voz(tmp_path)
    assert not dono.tem_perfil and dono.quantos == 0
    dono.preparar(lambda *_: None)
    aceito, nota = dono.conferir(_som(1))
    assert aceito and nota != nota  # nan: não houve comparação


def test_cadastrar_conferir_e_aprender(tmp_path):
    agora = [0.0]
    dono, _ = _voz(tmp_path, relogio=lambda: agora[0])
    dono.preparar(lambda *_: None)
    # Três gravações da "mesma pessoa" (mesmo desvio): parecidas entre si.
    minhas = [_som(1.5, s) for s in range(3)]
    assert dono.cadastrar(minhas) == 3 and dono.tem_perfil and dono.quantos == 3
    assert (tmp_path / "voz-do-dono.npz").is_file()
    aceito, nota = dono.conferir(_som(1.0, 7))
    assert aceito and nota > APRENDER and dono.aceitos == 1 and dono.recusados == 0 and list(dono.notas) == [(pytest.approx(nota), True)]
    assert dono.quantos == 4  # aceito com folga: entrou no cadastro
    outra = _som(1.0, 9) * 5 + 0.05  # outra "voz": média e desvio bem diferentes
    aceito, nota = dono.conferir(outra)
    assert not aceito and nota < 0.4 and dono.recusados == 1 and dono.quantos == 4
    # O cadastro aprendido é gravado, mas não a cada chamado.
    assert len(np.load(tmp_path / "voz-do-dono.npz")["vetores"]) == 3
    agora[0] = 100.0
    dono.conferir(_som(1.0, 8))
    assert len(np.load(tmp_path / "voz-do-dono.npz")["vetores"]) == 5
    # Outra instância lê o cadastro gravado.
    de_novo, _ = _voz(tmp_path)
    assert de_novo.tem_perfil and de_novo.quantos == 5
    de_novo.apagar()
    assert not de_novo.tem_perfil and not (tmp_path / "voz-do-dono.npz").exists()


def test_cadastro_recusa_gravacao_curta_ou_vozes_diferentes(tmp_path):
    dono, _ = _voz(tmp_path)
    dono.preparar(lambda *_: None)
    with pytest.raises(ErroDono, match="Nenhuma gravação"):
        dono.cadastrar([])
    with pytest.raises(ErroDono, match="curta demais"):
        dono.cadastrar([_som(1.5), _som(0.5)])
    with pytest.raises(ErroDono, match="diferentes demais entre si"):
        dono.cadastrar([_som(1.5, 1), _som(1.5, 2) * 5 + 0.05])
    assert not dono.tem_perfil
    with pytest.raises(ErroDono, match="curta demais"):
        dono.vetor(_som(0.2))


def test_cadastro_estragado_nao_derruba(tmp_path, caplog):
    (tmp_path / "voz-do-dono.npz").write_bytes(b"lixo")
    dono, _ = _voz(tmp_path)
    assert not dono.tem_perfil and "Não consegui ler o cadastro" in caplog.text


def test_limite_de_vetores_e_limiar(tmp_path):
    dono, _ = _voz(tmp_path, limiar=0.95)
    dono.preparar(lambda *_: None)
    dono.cadastrar([_som(1.5, s) for s in range(3)])
    for s in range(60):
        dono.conferir(_som(1.0, 100 + s))
    assert dono.quantos == modulo.MAX_VETORES
    assert _voz(tmp_path, limiar=5)[0].limiar == modulo.LIMIAR_MAXIMO and _voz(tmp_path, limiar=0)[0].limiar == modulo.LIMIAR_MINIMO


# --- o modelo ----------------------------------------------------------------------------------


def test_preparar_baixa_o_modelo_uma_vez(tmp_path):
    baixados = []

    def baixar(endereco, destino, avisar):
        baixados.append(endereco)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"modelo")

    dono, carregados = _voz(tmp_path, modelo_presente=False, baixar=baixar)
    assert not dono.pronto
    dono.preparar(lambda *_: None)
    dono.preparar(lambda *_: None)
    assert baixados == [modulo.ENDERECO] and carregados == [tmp_path / "modelos" / modulo.MODELO] and dono.pronto


def test_preparar_explica_as_falhas(tmp_path):
    def falha(*_):
        raise OSError("sem rede")

    dono, _ = _voz(tmp_path, modelo_presente=False, baixar=falha)
    with pytest.raises(ErroDono, match="Não consegui baixar.*sem rede"):
        dono.preparar(lambda *_: None)

    def pela_metade(endereco, destino, avisar):
        raise ErroDono("O download do modelo veio pela metade.")

    dono, _ = _voz(tmp_path, modelo_presente=False, baixar=pela_metade)
    with pytest.raises(ErroDono, match="pela metade"):
        dono.preparar(lambda *_: None)

    def sem_biblioteca(caminho):
        raise ImportError("x", name="sherpa_onnx")

    dono = VozDoDono(tmp_path / "modelos", tmp_path / "v.npz", extrator=sem_biblioteca, baixar_modelo=lambda *a: None)
    (tmp_path / "modelos").mkdir()
    (tmp_path / "modelos" / modulo.MODELO).write_bytes(b"x")
    with pytest.raises(ErroDono, match="Falta a biblioteca.*sherpa_onnx"):
        dono.preparar(lambda *_: None)

    def quebrado(caminho):
        raise RuntimeError("protobuf")

    dono = VozDoDono(tmp_path / "modelos", tmp_path / "v.npz", extrator=quebrado, baixar_modelo=lambda *a: None)
    with pytest.raises(ErroDono, match="Não consegui carregar.*protobuf"):
        dono.preparar(lambda *_: None)
    with pytest.raises(ErroDono, match="não está carregado"):
        dono.vetor(_som(1))


def test_baixar_de_verdade_confere_o_tamanho(tmp_path, monkeypatch):
    class Resposta:
        def __init__(self, tamanho):
            self.resto = tamanho

        def read(self, n):
            pedaco = b"x" * min(n, self.resto)
            self.resto -= len(pedaco)
            return pedaco

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(modulo.urllib.request, "urlopen", lambda pedido, timeout: Resposta(1000))
    avisos = []
    with pytest.raises(ErroDono, match="pela metade"):
        modulo.baixar("https://x", tmp_path / "m" / "a.onnx", avisos.append)
    assert not (tmp_path / "m" / "a.onnx").exists() and not (tmp_path / "m" / "a.onnx.parte").exists() and "Baixando" in avisos[0]
    monkeypatch.setattr(modulo.urllib.request, "urlopen", lambda pedido, timeout: Resposta(modulo.TAMANHO_MINIMO))
    modulo.baixar("https://x", tmp_path / "m" / "a.onnx", avisos.append)
    assert (tmp_path / "m" / "a.onnx").stat().st_size == modulo.TAMANHO_MINIMO


MODELO_REAL = Path("/tmp/claude-0/-home-claude/48d37bf1-0207-536e-b651-8ef9c40b4a61/scratchpad/falante") / modulo.MODELO
VOZES = Path("/tmp/claude-0/-home-claude/48d37bf1-0207-536e-b651-8ef9c40b4a61/scratchpad/vozes")


@pytest.mark.skipif(not MODELO_REAL.is_file() or not VOZES.is_dir() or os.environ.get("JARVIS_TESTES_REAIS") != "1", reason="precisa do modelo e das vozes de verdade (JARVIS_TESTES_REAIS=1)")
def test_com_o_modelo_de_verdade_outra_voz_fica_de_fora(tmp_path):
    from jarvis.voz import fala
    from jarvis.voz.escuta import reamostrar

    def audio(nome, frase):
        voz = fala.sintetizador_piper(VOZES / f"vits-piper-pt_BR-{nome}-medium" / f"pt_BR-{nome}-medium.onnx")
        a, taxa = voz(frase)
        return reamostrar(a.astype(np.float32) / 32768.0, taxa, TAXA)

    (tmp_path / "modelos").mkdir()
    os.symlink(MODELO_REAL, tmp_path / "modelos" / modulo.MODELO)
    dono = VozDoDono(tmp_path / "modelos", tmp_path / "v.npz")
    dono.preparar(lambda *_: None)
    assert dono.cadastrar([audio("faber", f) for f in FRASES_DE_CADASTRO]) == len(FRASES_DE_CADASTRO)
    for frase in ("Jarvis", "Jarvis, abre o YouTube", "Pesquise os melhores notebooks até cinco mil reais"):
        aceito, nota = dono.conferir(audio("faber", frase))
        assert aceito and nota >= 0.45, (frase, nota)
        for outro in ("cadu", "jeff"):
            aceito, nota = dono.conferir(audio(outro, frase))
            assert not aceito and nota < 0.4, (outro, frase, nota)


# --- configuração e a conversa -------------------------------------------------------------------


def test_configuracao(tmp_path):
    base = {"ANTHROPIC_API_KEY": "k", "JARVIS_PASTAS": str(tmp_path)}
    cfg = carregar(base, tmp_path)
    assert cfg.so_o_dono and cfg.limiar_da_voz == 0.4
    cfg = carregar({**base, "JARVIS_SO_O_DONO": "nao", "JARVIS_LIMIAR_DA_VOZ": "0,55"}, tmp_path)
    assert not cfg.so_o_dono and cfg.limiar_da_voz == 0.55
    for ruim in ("x", "0.1", "0.9"):
        with pytest.raises(Exception, match="JARVIS_LIMIAR_DA_VOZ"):
            carregar({**base, "JARVIS_LIMIAR_DA_VOZ": ruim}, tmp_path)


def test_ligar_voz_do_dono_na_partida(tmp_path, capsys, monkeypatch):
    from test_ativacao import cfg

    monkeypatch.setattr(cli, "PASTA_DADOS", tmp_path)
    dono = cli.ligar_voz_do_dono(cfg(tmp_path))
    assert isinstance(dono, VozDoDono) and not dono.pronto and "não cadastrada. Digite /minha-voz" in capsys.readouterr().out
    pronto, _ = _voz(tmp_path)
    pronto.preparar(lambda *_: None)
    pronto.cadastrar([_som(1.5, s) for s in range(2)])
    assert cli.ligar_voz_do_dono(cfg(tmp_path), pronto) is pronto and "cadastrada (2 amostras). Só a sua voz" in capsys.readouterr().out
    assert cli.ligar_voz_do_dono(cfg(tmp_path, so_o_dono=False), pronto) is pronto and "JARVIS_SO_O_DONO=nao" in capsys.readouterr().out
    outra = tmp_path / "outra"
    outra.mkdir()
    sem_modelo, _ = _voz(outra, modelo_presente=False, baixar=lambda *a: (_ for _ in ()).throw(OSError("sem rede")))
    sem_modelo._definir(np.ones((2, 4), dtype=np.float32))  # tem cadastro, mas o modelo não baixa
    assert cli.ligar_voz_do_dono(cfg(tmp_path), sem_modelo) is sem_modelo and "desligado por enquanto" in capsys.readouterr().out

    class Quebrado:
        def __init__(self, *a, **k):
            raise RuntimeError("disco")

    monkeypatch.setattr(cli, "VozDoDono", Quebrado)
    assert cli.ligar_voz_do_dono(cfg(tmp_path)) is None and "Reconhecimento da sua voz desligado: disco" in capsys.readouterr().out


class VozParaCadastro:
    """A voz sem áudio: cada gravação devolve a próxima amostra da lista."""

    def __init__(self, amostras, dono=None):
        self.amostras, self.dono, self.eventos = list(amostras), dono, []

    def comecar_gravacao(self):
        self.eventos.append("comecar")

    def terminar_gravacao_bruta(self):
        self.eventos.append("terminar")
        amostra = self.amostras.pop(0)
        if isinstance(amostra, Exception):
            raise amostra
        return amostra


def _cadastro(tmp_path, monkeypatch, amostras, respostas, dono=None):
    from test_ativacao import teclado as fabrica_do_teclado  # noqa: F401 - só para documentar de onde vem a ideia

    bipes = []
    monkeypatch.setattr(cli, "_bipe", bipes.append)
    fila = asyncio.Queue()

    async def ler(_prompt):
        valor = await fila.get()
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr(cli, "ler_linha", ler)
    for r in respostas:
        fila.put_nowait(r)
    dono = dono or _voz(tmp_path)[0]
    voz = VozParaCadastro(amostras, dono)
    return voz, bipes


class ChamadoFalso:
    def __init__(self):
        self.ouvindo = []

    def ouvir(self, sim):
        self.ouvindo.append(sim)


def test_minha_voz_grava_as_frases_e_cadastra(tmp_path, monkeypatch, capsys):
    amostras = [_som(1.5, s) for s in range(len(FRASES_DE_CADASTRO))]
    voz, bipes = _cadastro(tmp_path, monkeypatch, amostras, [""] * (2 * len(FRASES_DE_CADASTRO)))
    chamado = ChamadoFalso()
    assert asyncio.run(cli.cadastrar_voz(voz, None, MUDO, chamado)) is True
    assert voz.dono.tem_perfil and voz.dono.quantos == len(FRASES_DE_CADASTRO) and voz.eventos == ["comecar", "terminar"] * len(FRASES_DE_CADASTRO)
    assert bipes == [880, 660] * len(FRASES_DE_CADASTRO) and chamado.ouvindo == [False, True]
    saida = capsys.readouterr().out
    assert f"Frase 1 de {len(FRASES_DE_CADASTRO)}: «{FRASES_DE_CADASTRO[0]}»" in saida and "Pronto: guardei 4 amostras" in saida


def test_minha_voz_repete_a_frase_curta_e_desiste_se_nao_der(tmp_path, monkeypatch, capsys):
    amostras = [_som(0.3), _som(1.5, 1), *[_som(1.5, s) for s in range(2, 2 + len(FRASES_DE_CADASTRO) - 1)]]
    voz, _ = _cadastro(tmp_path, monkeypatch, amostras, [""] * 20)
    assert asyncio.run(cli.cadastrar_voz(voz, None, MUDO)) is True
    assert "Ficou curta demais" in capsys.readouterr().out and voz.dono.quantos == len(FRASES_DE_CADASTRO)
    voz, _ = _cadastro(tmp_path, monkeypatch, [_som(0.3)] * 3, [""] * 20)
    assert asyncio.run(cli.cadastrar_voz(voz, None, MUDO)) is False
    assert "cadastro cancelado" in capsys.readouterr().out


def test_minha_voz_cancelado_sem_voz_e_apagar(tmp_path, monkeypatch, capsys):
    voz, _ = _cadastro(tmp_path, monkeypatch, [], [EOFError()])
    assert asyncio.run(cli.cadastrar_voz(voz, None, MUDO)) is False and "Cadastro cancelado" in capsys.readouterr().out
    assert asyncio.run(cli.cadastrar_voz(None)) is False and "precisa do modo de voz" in capsys.readouterr().out
    voz.dono = None
    assert asyncio.run(cli.cadastrar_voz(voz)) is False and "indisponível" in capsys.readouterr().out
    dono, _ = _voz(tmp_path)
    dono.preparar(lambda *_: None)
    dono.cadastrar([_som(1.5, s) for s in range(2)])
    voz = VozParaCadastro([], dono)
    assert asyncio.run(cli.cadastrar_voz(voz, apagar=True)) is True and not dono.tem_perfil and "apagado" in capsys.readouterr().out


def test_minha_voz_com_modelo_que_nao_baixa_ou_microfone_que_falha(tmp_path, monkeypatch, capsys):
    dono, _ = _voz(tmp_path, modelo_presente=False, baixar=lambda *a: (_ for _ in ()).throw(OSError("sem rede")))
    voz, _ = _cadastro(tmp_path, monkeypatch, [], [], dono)
    assert asyncio.run(cli.cadastrar_voz(voz)) is False and "Não consegui baixar" in capsys.readouterr().out
    voz, _ = _cadastro(tmp_path, monkeypatch, [ErroVoz("microfone sumiu")], ["", ""])
    assert asyncio.run(cli.cadastrar_voz(voz)) is False and "microfone sumiu" in capsys.readouterr().out


def test_voz_de_verdade_entrega_o_dono_a_vigilia(tmp_path):
    class Falante:
        falando = False

    class Gravador:
        def comecar(self):
            pass

        def terminar(self):
            return _som(1)

    class Transcritor:
        def transcrever(self, audio, **k):
            return "x"

    voz = Voz(Falante(), Gravador(), Transcritor())
    assert voz.dono is None
    assert len(voz.terminar_gravacao_bruta()) == TAXA
    recebidos = {}

    class VigiaFalsa:
        def __init__(self, *a, **k):
            recebidos.update(k)

        def iniciar(self):
            pass

    voz.vigiar(("jarvis",), lambda t: None, fabrica=VigiaFalsa)
    assert recebidos["dono"] is None
    dono, _ = _voz(tmp_path)
    voz.dono = dono
    voz.vigiar(("jarvis",), lambda t: None, fabrica=VigiaFalsa)
    assert recebidos["dono"] == dono.conferir
