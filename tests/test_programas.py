"""Ver, clicar e digitar na janela de um programa que não é o navegador."""

import pytest

from jarvis.acoes import PROGRAMAS_PADRAO, ErroAcao
from jarvis.janelas import CTRL, NUNCA_NA_TELA, TECLAS_DE_PAGINA, Janelas, reservada

from sistema_falso import Programa, mesa


@pytest.fixture
def sistema():
    s = mesa()  # terminal (1), dois Chromes (10, 11), Edge (20), bloco de notas (30), Excel (40)
    s.janelas[50] = Programa("cmd.exe", "Prompt de Comando")
    s.janelas[60] = Programa("explorer.exe", "Downloads")
    s.janelas[70] = Programa("smartclient.exe", "TOTVS Protheus")
    s.janelas[80] = Programa("KeePass.exe", "Cofre - KeePass")
    return s


@pytest.fixture
def janelas(sistema):
    return Janelas(sistema, PROGRAMAS_PADRAO)


def test_quais_programas_ele_pode_operar(janelas):
    assert janelas.operaveis == {"calc", "notepad", "mspaint", "excel", "winword", "powerpnt", "outlook"}
    # o Explorador e os navegadores estão na lista de abrir, mas não entram aqui
    assert not janelas.operaveis & {"explorer", "chrome", "edge", "msedge", "firefox"}


def test_os_que_ficam_sempre_de_fora_nao_entram_nem_se_o_usuario_listar(sistema):
    apelidos = {**PROGRAMAS_PADRAO, "terminal": "cmd", "vscode": "C:\\Apps\\Code.exe"}
    janelas = Janelas(sistema, apelidos, ["powershell", " Spotify.exe ", "regedit", "", "smartclient"])
    assert {"spotify", "smartclient"} <= janelas.operaveis
    assert not janelas.operaveis & NUNCA_NA_TELA
    assert {"cmd", "powershell", "windowsterminal", "python", "code", "explorer", "regedit", "taskmgr", "keepass", "mstsc"} <= NUNCA_NA_TELA


def test_ver_o_excel_e_mexer_nele(janelas, sistema):
    foto = janelas.ver_programa("excel")
    assert sistema.frente == 40 and foto.dados == b"JPEG 1264x790 de romaneio.xlsx - Excel"
    assert janelas.clicar(100, 200).executar() == ["Cliquei em (100, 200)."]
    assert janelas.digitar("1500", True).executar() == ["Digitei o texto e apertei Enter."]
    assert janelas.tecla("salvar").executar() == ["Apertei a tecla salvar."]
    assert janelas.rolar("baixo").executar() == ["Rolei a página para baixo."]
    assert [h for h, *_ in sistema.cliques] == [40] and sistema.digitado == [(40, "1500")]
    assert sistema.teclas_enviadas == [(40, TECLAS_DE_PAGINA["enter"]), (40, (CTRL, 0x53))]
    assert sistema.rodadas[0][0] == 40
    janelas.fim_do_turno()
    assert sistema.frente == 1


def test_pelo_apelido_e_pelo_titulo(janelas, sistema):
    janelas.ver_programa("bloco de notas")
    assert sistema.frente == 30
    janelas.fim_do_turno()
    janelas.ver_programa("romaneio")
    assert sistema.frente == 40


def test_tela_ver_no_mesmo_turno_fotografa_o_programa_de_novo(janelas, sistema):
    janelas.ver_programa("excel")
    sistema.frente = 1  # o terminal passou na frente
    foto = janelas.ver(None, 1)
    assert sistema.frente == 40 and foto.dados.endswith(b"Excel") and len(sistema.fotos) == 2


def test_o_alvo_nao_muda_no_meio_do_turno(janelas, sistema):
    janelas.ver_programa("excel")
    with pytest.raises(ErroAcao, match="já está em outra janela"):
        janelas.ver_programa("bloco de notas")
    with pytest.raises(ErroAcao, match="abas são do navegador"):
        janelas.ver("gmail")
    with pytest.raises(ErroAcao, match="abas são do navegador"):
        janelas.rolar("baixo", 1, "gmail")
    janelas.ver_programa("excel")  # a mesma janela pode ser vista de novo
    assert sistema.frente == 40
    janelas.fim_do_turno()
    janelas.ver_programa("bloco de notas")  # nova fala do usuário, novo alvo
    assert sistema.frente == 30


def test_depois_de_ver_o_navegador_nao_vai_para_um_programa(janelas, sistema):
    janelas.ver()
    with pytest.raises(ErroAcao, match="já viu o navegador"):
        janelas.ver_programa("excel")
    assert sistema.frente == 10 and len(sistema.fotos) == 1


@pytest.mark.parametrize("termo", ["prompt de comando", "cmd", "downloads", "explorer", "keepass", "cofre"])
def test_terminal_explorador_e_cofre_de_senha_ficam_de_fora(janelas, sistema, termo):
    with pytest.raises(ErroAcao, match="ficam sempre de fora"):
        janelas.ver_programa(termo)
    assert sistema.fotos == [] and sistema.frente == 1  # nem trouxe a janela para frente


def test_programa_fora_da_lista_e_como_liberar(sistema, janelas):
    with pytest.raises(ErroAcao, match="não está na lista.*JARVIS_PROGRAMAS_TELA"):
        janelas.ver_programa("protheus")
    assert sistema.fotos == []
    liberado = Janelas(sistema, PROGRAMAS_PADRAO, ["smartclient"])
    liberado.ver_programa("protheus")
    assert sistema.frente == 70


def test_navegador_tem_caminho_proprio(janelas):
    with pytest.raises(ErroAcao, match="use tela_ver"):
        janelas.ver_programa("chrome")


def test_o_terminal_do_jarvis_nao_e_alvo(janelas, sistema):
    for termo in ("jarvis", "windowsterminal"):
        with pytest.raises(ErroAcao, match="Não há janela aberta"):
            janelas.ver_programa(termo)
    for ruim in ("", "x", None, 7):
        with pytest.raises(ErroAcao):
            janelas.ver_programa(ruim)


def test_janela_de_senha_nao_e_vista_mas_banco_de_horas_e(janelas, sistema):
    sistema.janelas[40].titulo = "senhas.xlsx - Excel"
    with pytest.raises(ErroAcao, match="banco, pagamento ou senha"):
        janelas.ver_programa("excel")
    assert sistema.fotos == []
    sistema.janelas[40].titulo = "banco de horas.xlsx - Excel"
    janelas.ver_programa("excel")
    assert len(sistema.fotos) == 1
    assert reservada("banco de horas.xlsx - Excel", de_programa=True) is None
    assert reservada("banco de horas - Google Chrome") == "banco"  # no navegador a lista é a completa


def test_dialogo_do_programa_conta_como_a_janela_dele(janelas, sistema):
    janelas.ver_programa("excel")
    janelas.tecla("salvar").executar()
    sistema.janelas[41] = Programa("EXCEL.EXE", "Salvar como")  # o Excel abriu o diálogo, que fica na frente
    sistema.donas[41] = 40
    sistema.frente = 41
    assert 41 not in [j.hwnd for j in janelas.listar()]
    assert janelas.digitar("relatorio de outubro", True).executar() == ["Digitei o texto e apertei Enter."]
    assert sistema.digitado == [(41, "relatorio de outubro")]  # foi para o diálogo, que é quem tem o cursor
    assert janelas.clicar(600, 400).executar() == ["Cliquei em (600, 400)."]
    assert janelas.rever() is not None
    sistema.frente = 30  # outra janela, sem relação com o Excel
    assert janelas.rever() is None


def test_dialogo_de_outro_programa_nao_engana(janelas, sistema):
    janelas.ver_programa("excel")
    sistema.janelas[31] = Programa("notepad.exe", "Salvar como")
    sistema.donas[31] = 30
    sistema.frente = 31
    sistema.nao_focam = {40}
    with pytest.raises(OSError, match="não deixou trazer a janela"):
        janelas.digitar("x").executar()
    assert sistema.digitado == []


def test_atalho_de_aba_continua_exigindo_a_propria_janela(janelas, sistema):
    """Ctrl+W com um diálogo na frente fecharia outra coisa: para abas, só a janela do navegador vale."""
    sistema.janelas[12] = Programa("chrome.exe", "Salvar como")
    sistema.donas[12] = 10
    sistema.rouba_o_foco = 12  # depois de focar o Chrome, quem fica na frente é o diálogo dele
    with pytest.raises(OSError, match="saiu do primeiro plano"):
        janelas.fechar_aba("gmail").executar()
    assert sistema.teclas_enviadas == []


def test_sem_programa_aberto(janelas):
    with pytest.raises(ErroAcao, match="Não há janela aberta que combine com 'photoshop'"):
        janelas.ver_programa("photoshop")
