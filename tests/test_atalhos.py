"""O atalho do Jarvis na Área de Trabalho e no menu Iniciar."""

import shutil
import tempfile
import subprocess
from pathlib import Path

import pytest

from jarvis import atalhos
from jarvis.atalhos import _ROTEIRO as _ROTEIRO_TESTE
from jarvis.atalhos import Atalhos, ErroAtalho, ajustar_atalhos, gerar_icone

COMANDO = '"C:\\Python\\pythonw.exe" "C:\\Users\\Micro\\jarvis-desktop\\iniciar.pyw"'


def _atalhos(saida="", erro=None, raiz=None, tmp=None):
    """Os testes não encostam no disco de verdade: a raiz é sempre uma pasta temporária.

    Sem isto, gravar o ícone criaria `C:/Users/...` como pasta de verdade dentro do projeto.
    """
    raiz = raiz if raiz is not None else (tmp or Path(tempfile.mkdtemp()))
    vistos = []

    def rodar(roteiro, ambiente, espera=60.0):
        vistos.append((roteiro, dict(ambiente)))
        if erro is not None:
            raise erro
        return saida

    return Atalhos(raiz=raiz, comando=COMANDO, rodar=rodar), vistos


def test_o_atalho_aponta_para_o_pythonw_e_o_iniciar():
    """Tem de ser o `pythonw.exe`: com o `python.exe` fica uma janela preta aberta na tela."""
    alvo, vistos = _atalhos("CRIADO C:/Users/Micro/Desktop/Jarvis.lnk")
    assert alvo.criar() == [Path("C:/Users/Micro/Desktop/Jarvis.lnk")]
    ambiente = vistos[0][1]
    assert ambiente["JV_ALVO"] == "C:\\Python\\pythonw.exe"
    assert ambiente["JV_ARGUMENTOS"] == "C:\\Users\\Micro\\jarvis-desktop\\iniciar.pyw"
    assert ambiente["JV_PASTA"] == str(alvo.raiz), "a pasta de trabalho é a do Jarvis"
    assert ambiente["JV_NOME"] == "Jarvis" and ambiente["JV_ICONE"].endswith(",0")


def test_caminho_com_espaco_vai_entre_aspas_no_argumento():
    """`C:\\Users\\Micro\\OneDrive\\jarvis desktop\\iniciar.pyw` sem aspas vira dois argumentos."""
    alvo, vistos = _atalhos("CRIADO x")
    alvo.argumentos = "C:\\Users\\Micro\\One Drive\\jarvis desktop\\iniciar.pyw"
    alvo.criar()
    assert vistos[0][1]["JV_ARGUMENTOS"].startswith('"') and vistos[0][1]["JV_ARGUMENTOS"].endswith('"')


def test_o_caminho_nunca_entra_no_texto_do_roteiro():
    """A proteção: nome de usuário com aspas ou `;` não pode virar comando. Vai por variável."""
    alvo, vistos = _atalhos("CRIADO x")
    alvo.raiz = Path("C:/Users/O'Brien\"; calc.exe; #/jarvis")  # só no ambiente; nada é gravado aqui
    alvo._rodar = lambda r, amb, espera=60.0: (vistos.append((r, dict(amb))), "CRIADO x")[1]
    alvo._rodar(_ROTEIRO_TESTE, alvo._ambiente(None))
    roteiro, ambiente = vistos[-1]
    assert "O'Brien" not in roteiro and "calc.exe" not in roteiro
    assert "O'Brien" in ambiente["JV_PASTA"]


def test_sem_nenhum_atalho_criado_o_erro_e_claro():
    """As duas pastas podem não existir (perfil estranho, política de empresa): é falha, não silêncio."""
    alvo, _ = _atalhos("")
    with pytest.raises(ErroAtalho, match="nenhum atalho criado"):
        alvo.criar()


def test_remover_diz_o_que_apagou_e_aguenta_nao_haver_nada():
    alvo, vistos = _atalhos("APAGADO C:/Users/Micro/Desktop/Jarvis.lnk")
    assert alvo.remover() == [Path("C:/Users/Micro/Desktop/Jarvis.lnk")]
    assert "JV_ALVO" not in vistos[0][1], "apagar não precisa saber para onde o atalho apontava"
    vazio, _ = _atalhos("")
    assert vazio.remover() == []


def test_falha_do_windows_vira_mensagem_e_nao_estouro(capsys):
    alvo, _ = _atalhos(erro=ErroAtalho("não achei o powershell.exe nesta máquina"))
    assert ajustar_atalhos(True, sistema="win32", atalhos=alvo) == 1
    assert "powershell.exe" in capsys.readouterr().err


def test_fora_do_windows_recusa(capsys):
    assert ajustar_atalhos(True, sistema="linux") == 2
    assert "só existe no Windows" in capsys.readouterr().err


def test_o_aviso_ensina_a_fixar_na_barra(capsys):
    alvo, _ = _atalhos("CRIADO C:/Users/Micro/Desktop/Jarvis.lnk")
    assert ajustar_atalhos(True, sistema="win32", atalhos=alvo) == 0
    saida = capsys.readouterr().out
    assert "Jarvis.lnk" in saida and "Fixar na barra de tarefas" in saida


def test_o_icone_tem_todos_os_tamanhos(tmp_path):
    """Sem os tamanhos pequenos, o Windows reduz o grande e o ícone sai borrado na barra."""
    pytest.importorskip("PIL")
    from PIL import Image

    feito = gerar_icone(tmp_path / "jarvis.ico")
    assert feito is not None and feito.exists()
    assert sorted(Image.open(feito).ico.sizes()) == sorted((t, t) for t in atalhos.TAMANHOS)


def test_sem_pillow_usa_o_icone_que_veio_no_pacote(tmp_path):
    """O `.ico` vai no zip: falta de Pillow não pode deixar o atalho sem cara."""
    def sem_pillow(*a, **k):
        raise ImportError("No module named 'PIL'")

    assert gerar_icone(tmp_path / "jarvis.ico", desenhar=sem_pillow) is None
    (tmp_path / "pronto.ico").write_bytes(b"\x00\x00\x01\x00")
    assert gerar_icone(tmp_path / "pronto.ico", desenhar=sem_pillow) == tmp_path / "pronto.ico"


def test_o_icone_do_pacote_esta_la():
    """O arquivo que o atalho aponta tem de existir no pacote, não só ser gerado aqui."""
    assert (Path(atalhos.__file__).resolve().parent.parent / atalhos.ARQUIVO_DO_ICONE).exists()


# --- com um PowerShell de verdade, quando houver -----------------------------------------

def _powershell_de_verdade():
    """O Wine traz um `powershell.exe` que não executa nada: achar o binário não basta."""
    for caminho in (shutil.which("pwsh"), shutil.which("powershell.exe"), shutil.which("powershell")):
        if not caminho:
            continue
        try:
            feito = subprocess.run([caminho, "-NoProfile", "-Command", "Write-Output VIVO"],
                                   capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if feito.returncode == 0 and "VIVO" in (feito.stdout or ""):
            return caminho
    return None


POWERSHELL = _powershell_de_verdade()


@pytest.mark.skipif(not POWERSHELL, reason="não há PowerShell nesta máquina")
def test_os_roteiros_sao_powershell_valido():
    """Erro de sintaxe no roteiro só apareceria na máquina do usuário, na hora de instalar."""
    conferir = (
        "$e=$null; [System.Management.Automation.Language.Parser]::ParseInput("
        "[IO.File]::ReadAllText($env:JV_ARQ), [ref]$null, [ref]$e) > $null; "
        "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 } else { 'ok' }"
    )
    for roteiro in (atalhos._ROTEIRO, atalhos._ROTEIRO_REMOVER):
        arquivo = Path(__file__).parent / "_roteiro.ps1"
        arquivo.write_text(roteiro, encoding="utf-8")
        try:
            feito = subprocess.run([POWERSHELL, "-NoProfile", "-Command", conferir],
                                   capture_output=True, text=True, timeout=120,
                                   env={"PATH": "/usr/bin:/bin", "JV_ARQ": str(arquivo), "HOME": str(arquivo.parent)})
            assert feito.returncode == 0, feito.stdout + feito.stderr
        finally:
            arquivo.unlink(missing_ok=True)


@pytest.mark.skipif(not POWERSHELL, reason="não há PowerShell nesta máquina")
def test_o_valor_hostil_chega_como_texto_e_nao_como_comando(monkeypatch):
    """A prova da decisão de passar por ambiente: isto viraria `calc.exe` se fosse texto no roteiro."""
    monkeypatch.setattr(atalhos.subprocess, "run", _rodando_com(POWERSHELL))
    hostil = "C:\\Users\\O'Brien\"; Write-Output INVADIU; #"
    saida = atalhos._powershell("Write-Output $env:JV_PASTA", {"JV_PASTA": hostil})
    # Devolvido inteiro e em UMA linha: o `";` não fechou nada e o `Write-Output INVADIU` não rodou
    # (se tivesse rodado, viria uma segunda linha só com INVADIU).
    assert saida.strip() == hostil and len(saida.strip().splitlines()) == 1


def _rodando_com(binario):
    """Troca o `powershell.exe` da linha pelo PowerShell que existe aqui, sem mexer no resto."""
    real = subprocess.run

    def rodar(linha, **k):
        k.pop("creationflags", None)
        return real([binario, *linha[1:]], **k)

    return rodar


# --- achar o Python certo ----------------------------------------------------------------


def test_escolhe_o_python_que_tem_as_bibliotecas_e_nao_o_que_digitou(tmp_path):
    """O que estava quebrado: `python -m jarvis` pode ser outro Python, sem as bibliotecas.

    O atalho tem de apontar para o que consegue subir o Jarvis, senão ele abre e morre calado.
    """
    errado, certo = tmp_path / "Loja" / "python.exe", tmp_path / ".venv" / "Scripts" / "python.exe"
    for caminho in (errado, certo, certo.with_name("pythonw.exe")):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("")
    escolhido = atalhos.python_do_jarvis(tmp_path, executavel=str(errado), conferir=lambda c: c == certo)
    assert escolhido == certo
    # e o atalho usa o `pythonw.exe` ao lado dele: sem isso fica uma janela preta aberta na tela
    assert atalhos.sem_janela(escolhido).name == "pythonw.exe"


def test_sem_nenhum_python_com_as_bibliotecas_o_erro_ensina_o_que_fazer(tmp_path):
    with pytest.raises(ErroAtalho, match="pip install -r requirements.txt"):
        atalhos.python_do_jarvis(tmp_path, executavel=str(tmp_path), conferir=lambda c: False)


def test_a_conferencia_e_um_import_de_verdade(tmp_path):
    """Não basta o arquivo existir: o que separa um Python do outro é importar a biblioteca."""
    vistos = []

    def rodar(linha, **k):
        vistos.append(linha)
        return type("Feito", (), {"returncode": 0 if "bom" in linha[0] else 1})()

    assert atalhos._tem_as_bibliotecas(Path("/bom/python"), rodar=rodar) is True
    assert atalhos._tem_as_bibliotecas(Path("/ruim/python"), rodar=rodar) is False
    assert vistos[0][1:] == ["-c", f"import {atalhos.DEPENDENCIA}"]


def test_criar_atalho_nao_carrega_o_jarvis_inteiro():
    """A causa do erro que apareceu na máquina do usuário: a opção morria no import do SDK."""
    fonte = (Path(atalhos.__file__).resolve().parent / "__main__.py").read_text(encoding="utf-8")
    antes = fonte.index("--criar-atalho")
    assert antes < fonte.index("from jarvis.cli import main"), "a opção tem de ser tratada antes do import pesado"
    import subprocess as sp
    import sys as s

    feito = sp.run([s.executable, "-c",
                    "import sys; import jarvis.atalhos; "
                    "print([m for m in sys.modules if 'claude_agent' in m or m.startswith('anthropic')])"],
                   capture_output=True, text=True, timeout=120)
    assert feito.stdout.strip() == "[]", f"jarvis.atalhos puxou o SDK: {feito.stdout}"


def test_apagar_nao_depende_de_achar_python(tmp_path, monkeypatch):
    """Quem só quer apagar o atalho não pode esbarrar no erro de 'não achei Python'."""
    def nunca(*a, **k):
        raise AssertionError("procurou Python para apagar")

    monkeypatch.setattr(atalhos, "python_do_jarvis", nunca)
    alvo = Atalhos(raiz=tmp_path, rodar=lambda r, amb, espera=60.0: "APAGADO C:/x/Jarvis.lnk")
    assert alvo.remover() == [Path("C:/x/Jarvis.lnk")]
