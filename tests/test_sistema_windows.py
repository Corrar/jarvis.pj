"""Exercita o código que fala com o Windows contra um user32 de mentira.

Não prova que a API do Windows se comporta assim; pega erro de digitação, ordem de
argumentos e a lógica de filtro e de foco, em qualquer sistema.
"""

import copy
import ctypes
from types import SimpleNamespace

import pytest

from jarvis import janelas

TERMINAL, CHROME, BLOCO, DIALOGO, FERRAMENTA, INVISIVEL, CAMUFLADA, SEM_TITULO, CONSOLE = range(100, 109)

MESA = {
    TERMINAL: dict(titulo="python -m jarvis", exe="C:\\Program Files\\WindowsApps\\WindowsTerminal.exe"),
    CHROME: dict(titulo="Instagram - Google Chrome", exe="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", caixa=(100, 50, 1700, 1050)),
    BLOCO: dict(titulo="Sem título - Bloco de Notas", exe="C:\\Windows\\System32\\notepad.exe", minimizada=True, caixa=(2000, 100, 2400, 400)),
    DIALOGO: dict(titulo="Salvar como", exe="C:\\Windows\\System32\\notepad.exe", dona=BLOCO, caixa=(2100, 150, 2300, 300)),
    FERRAMENTA: dict(titulo="Barra flutuante", exe="C:\\x\\app.exe", estilo=0x80),
    INVISIVEL: dict(titulo="Oculta", exe="C:\\x\\app.exe", visivel=False),
    CAMUFLADA: dict(titulo="Configurações", exe="C:\\Windows\\ImmersiveControlPanel\\SystemSettings.exe", camuflada=1),
    SEM_TITULO: dict(titulo="", exe="C:\\x\\app.exe"),
    # No Windows Terminal o console do processo é uma janela escondida.
    CONSOLE: dict(titulo="Console", exe="C:\\Windows\\System32\\conhost.exe", visivel=False),
}

ALT_SOLTO = (0x12, 2)


@pytest.fixture
def windows(monkeypatch):
    estado = SimpleNamespace(
        mesa=copy.deepcopy(MESA), frente=TERMINAL, teclas=[], mensagens=[], restauradas=[], ligacoes=[],
        desligou=False, exige_ligacao=False, exige_alt=False, trava_foco=False, tecla_que_falha=None,
        cursor=(5, 5), mouse=[], texto=[], recusa_texto=False, dpi=[], sem_moldura=False, fotos=[],
    )  # fmt: skip
    mesa = estado.mesa

    def num(hwnd):
        return int(hwnd or 0)

    def EnumWindows(visitar, extra):  # noqa: N802 - nomes da API do Windows
        for hwnd in list(mesa):
            visitar(hwnd, extra)
        return 1

    def GetWindowTextW(hwnd, saida, tamanho):  # noqa: N802
        saida.value = mesa[num(hwnd)]["titulo"][: tamanho - 1]
        return len(saida.value)

    def GetWindowThreadProcessId(hwnd, pid):  # noqa: N802
        if pid is not None:
            pid._obj.value = num(hwnd) * 10
        return num(hwnd) + 1000  # "thread" dona da janela

    def SetForegroundWindow(hwnd):  # noqa: N802
        if estado.trava_foco:
            return 0
        if estado.exige_ligacao and not estado.ligacoes:
            return 0
        if estado.exige_alt and estado.teclas[-1:] != [ALT_SOLTO]:
            return 0
        estado.frente = num(hwnd)
        return 1

    def AttachThreadInput(eu, dona, ligar):  # noqa: N802
        if ligar:
            estado.ligacoes.append((eu, dona))
        else:
            estado.ligacoes.remove((eu, dona))
            estado.desligou = True
        return 1

    def QueryFullProcessImageNameW(processo, _flags, saida, tamanho):  # noqa: N802
        saida.value = mesa[processo // 10]["exe"]
        return 1

    def caixa_de(hwnd):
        return mesa[num(hwnd)].get("caixa", (0, 0, 800, 600))

    def DwmGetWindowAttribute(hwnd, atributo, valor, _tamanho):  # noqa: N802
        if atributo == 9:  # a moldura que o usuário vê
            if estado.sem_moldura:
                return -1
            valor._obj.left, valor._obj.top, valor._obj.right, valor._obj.bottom = caixa_de(hwnd)
            return 0
        assert atributo == 14
        valor._obj.value = mesa[num(hwnd)].get("camuflada", 0)
        return 0

    def GetWindowRect(hwnd, r):  # noqa: N802 - inclui a borda invisível de 7 pixels
        esquerda, topo, direita, base = caixa_de(hwnd)
        r._obj.left, r._obj.top, r._obj.right, r._obj.bottom = esquerda - 7, topo, direita + 7, base + 7
        return 1

    def WindowFromPoint(ponto):  # noqa: N802 - devolve um "filho" da janela da frente que contém o ponto
        ordem = [estado.frente, *[h for h in mesa if h != estado.frente]]
        for hwnd in ordem:
            dados = mesa[hwnd]
            esquerda, topo, direita, base = dados.get("caixa", (0, 0, 800, 600))
            if dados.get("visivel", True) and not dados.get("minimizada") and esquerda <= ponto.x < direita and topo <= ponto.y < base:
                return hwnd + 5000
        return None

    def GetAncestor(hwnd, qual):  # noqa: N802
        assert qual == 3  # a dona de todas: um menu aberto pela página responde pela janela do navegador
        raiz = num(hwnd) - 5000 if num(hwnd) >= 5000 else num(hwnd)
        return mesa[raiz].get("dona", raiz)

    def GetCursorPos(ponto):  # noqa: N802
        ponto._obj.x, ponto._obj.y = estado.cursor
        return 1

    def SetCursorPos(x, y):  # noqa: N802
        estado.cursor = (x, y)
        estado.mouse.append(("move", x, y))
        return 1

    def mouse_event(flags, _dx, _dy, dados, _extra):
        estado.mouse.append((flags, dados, estado.cursor))

    def SendInput(quantas, entradas, tamanho):  # noqa: N802
        assert tamanho == 40
        if estado.recusa_texto:
            return 0
        for i in range(quantas):
            assert entradas[i].type == 1 and entradas[i].u.ki.wVk == 0
            estado.texto.append((entradas[i].u.ki.wScan, entradas[i].u.ki.dwFlags))
        return quantas

    def keybd_event(vk, _scan, flags, _extra):
        if (vk, flags) == estado.tecla_que_falha:
            raise OSError("falha simulada")
        estado.teclas.append((vk, flags))
        if (vk, flags) == (0x57, 0) and estado.frente == CHROME:  # Ctrl+W no Chrome: a aba fecha
            if mesa[CHROME].get("some_ao_fechar"):  # era a última: a janela se esconde e mantém o título
                mesa[CHROME]["visivel"] = False
            else:
                mesa[CHROME]["titulo"] = "Gmail - Google Chrome"

    user32 = SimpleNamespace(
        EnumWindows=EnumWindows,
        IsWindowVisible=lambda h: int(num(h) in mesa and mesa[num(h)].get("visivel", True)),
        IsIconic=lambda h: int(mesa[num(h)].get("minimizada", False)),
        GetWindowTextLengthW=lambda h: len(mesa[num(h)]["titulo"]) if num(h) in mesa else 0,
        GetWindowTextW=GetWindowTextW,
        GetWindowThreadProcessId=GetWindowThreadProcessId,
        GetWindow=lambda h, qual: mesa[num(h)].get("dona") if qual == 4 else None,
        GetWindowLongW=lambda h, indice: mesa[num(h)].get("estilo", 0) if indice == -20 else 0,
        GetForegroundWindow=lambda: estado.frente,
        SetForegroundWindow=SetForegroundWindow,
        BringWindowToTop=lambda h: 1,
        ShowWindow=lambda h, modo: estado.restauradas.append((num(h), modo)) or 1,
        AttachThreadInput=AttachThreadInput,
        PostMessageW=lambda h, msg, w, l: estado.mensagens.append((num(h), msg, w, l)) or 1,
        keybd_event=keybd_event,
        GetWindowRect=GetWindowRect,
        GetSystemMetrics=lambda qual: {76: -1920, 77: 0, 78: 3840, 79: 1080}[qual],  # duas telas, uma à esquerda
        WindowFromPoint=WindowFromPoint,
        GetAncestor=GetAncestor,
        GetCursorPos=GetCursorPos,
        SetCursorPos=SetCursorPos,
        mouse_event=mouse_event,
        SendInput=SendInput,
        SetProcessDpiAwarenessContext=lambda contexto: estado.dpi.append(("processo", contexto)) or 1,
        SetThreadDpiAwarenessContext=lambda contexto: estado.dpi.append(("thread", contexto)) or 1,
    )
    estado.user32 = user32
    kernel32 = SimpleNamespace(
        GetCurrentThreadId=lambda: 7,
        GetConsoleWindow=lambda: CONSOLE,
        OpenProcess=lambda acesso, herdar, pid: pid if acesso == 0x1000 else 0,
        QueryFullProcessImageNameW=QueryFullProcessImageNameW,
        CloseHandle=lambda h: 1,
    )
    dwmapi = SimpleNamespace(DwmGetWindowAttribute=DwmGetWindowAttribute)
    dlls = {"user32": user32, "kernel32": kernel32, "dwmapi": dwmapi}
    monkeypatch.setattr(ctypes, "WinDLL", lambda nome, **_k: dlls[nome], raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(janelas.time, "sleep", lambda _s: None)
    monkeypatch.setattr(janelas, "foto_da_tela", lambda caixa, w, h: estado.fotos.append((caixa, w, h)) or b"jpeg")
    return estado


# --- qual janela é o terminal do Jarvis ---------------------------------------


def test_no_windows_terminal_a_janela_do_jarvis_e_a_que_estava_na_frente(windows):
    assert janelas.SistemaWindows().propria == TERMINAL


def test_no_console_classico_a_janela_do_jarvis_e_o_proprio_console(windows):
    windows.mesa[CONSOLE]["visivel"] = True
    windows.frente = BLOCO  # mesmo que outra janela esteja na frente
    sistema = janelas.SistemaWindows()
    assert sistema.propria == CONSOLE
    assert CONSOLE not in [j.hwnd for j in sistema.listar()]


def test_se_um_navegador_estava_na_frente_ao_iniciar_nao_adivinha(windows):
    windows.frente = CHROME
    sistema = janelas.SistemaWindows()
    assert sistema.propria == 0
    assert CHROME in [j.hwnd for j in janelas.Janelas(sistema).listar()]  # o Chrome continua visível para o Jarvis


# --- listar -----------------------------------------------------------------------


def test_listar_so_traz_janelas_de_programa_que_o_usuario_ve(windows):
    achadas = janelas.SistemaWindows().listar()
    assert [(j.hwnd, j.titulo, j.processo) for j in achadas] == [
        (TERMINAL, "python -m jarvis", "WindowsTerminal.exe"),
        (CHROME, "Instagram - Google Chrome", "chrome.exe"),
        (BLOCO, "Sem título - Bloco de Notas", "notepad.exe"),
    ]
    assert achadas[1].navegador == "chrome" and achadas[2].programa == "notepad"


def test_titulo_e_janela_ativa(windows):
    sistema = janelas.SistemaWindows()
    assert sistema.titulo(CHROME) == "Instagram - Google Chrome"
    assert sistema.titulo(SEM_TITULO) == ""
    assert sistema.titulo(9999) == ""  # janela que não existe mais
    assert sistema.titulo(INVISIVEL) == ""  # fechada mas ainda não destruída: escondida, com o título antigo
    assert sistema.ativa() == TERMINAL


# --- trazer para frente -------------------------------------------------------------


def test_focar_direto(windows):
    assert janelas.SistemaWindows().focar(CHROME) is True
    assert windows.frente == CHROME
    assert windows.ligacoes == [] and windows.restauradas == [] and windows.teclas == []


def test_focar_restaura_janela_minimizada(windows):
    assert janelas.SistemaWindows().focar(BLOCO) is True
    assert windows.restauradas == [(BLOCO, 9)]


def test_focar_quando_o_windows_exige_ligacao_com_a_janela_ativa(windows):
    windows.exige_ligacao = True
    assert janelas.SistemaWindows().focar(CHROME) is True
    assert windows.frente == CHROME
    assert windows.ligacoes == [] and windows.desligou  # ligou só durante a troca e desfez
    assert windows.teclas == []  # não precisou do Alt


def test_focar_com_o_toque_de_alt_como_ultimo_recurso(windows):
    windows.exige_alt = True
    assert janelas.SistemaWindows().focar(CHROME) is True
    assert windows.frente == CHROME
    assert windows.teclas == [(0x12, 0), (0x12, 2), (0x12, 0), (0x12, 2)]  # dois toques, nada fica apertado


def test_focar_que_nao_da_certo_devolve_falso(windows):
    windows.trava_foco = True
    assert janelas.SistemaWindows().focar(CHROME) is False
    assert windows.frente == TERMINAL and windows.ligacoes == []


# --- teclas e fechamento --------------------------------------------------------------


def test_teclas_aperta_na_ordem_e_solta_na_ordem_inversa(windows):
    janelas.SistemaWindows().teclas(janelas.CTRL, janelas.SHIFT, janelas.T)
    assert windows.teclas == [(0x11, 0), (0x10, 0), (0x54, 0), (0x54, 2), (0x10, 2), (0x11, 2)]


def test_page_down_e_page_up_saem_como_teclas_de_navegacao(windows):
    sistema = janelas.SistemaWindows()
    sistema.teclas(janelas.CTRL, janelas.PGDN)
    sistema.teclas(janelas.CTRL, janelas.PGUP)
    assert windows.teclas == [
        (0x11, 0), (0x22, 1), (0x22, 3), (0x11, 2),
        (0x11, 0), (0x21, 1), (0x21, 3), (0x11, 2),
    ]  # fmt: skip


def test_falha_no_meio_nao_deixa_tecla_presa(windows):
    windows.tecla_que_falha = (0x54, 0)  # falha ao apertar o T de Ctrl+Shift+T
    with pytest.raises(OSError):
        janelas.SistemaWindows().teclas(janelas.CTRL, janelas.SHIFT, janelas.T)
    assert windows.teclas == [(0x11, 0), (0x10, 0), (0x10, 2), (0x11, 2)]


def test_pedir_fechamento_manda_wm_close(windows):
    janelas.SistemaWindows().pedir_fechamento(BLOCO)
    assert windows.mensagens == [(BLOCO, 0x0010, 0, 0)]


# --- lógica e sistema juntos -------------------------------------------------------------


def test_fecha_a_aba_a_mostra_e_volta_ao_terminal(windows):
    controle = janelas.Janelas(janelas.SistemaWindows())
    assert [j.hwnd for j in controle.listar()] == [CHROME, BLOCO]  # sem o terminal
    assert controle.fechar_aba("instagram").executar() == ["Fechei a aba 'instagram' no chrome."]
    assert windows.teclas == [(0x11, 0), (0x57, 0), (0x57, 2), (0x11, 2)]  # só Ctrl+W
    assert windows.frente == TERMINAL


def test_fecha_a_aba_mesmo_quando_o_foco_so_vem_com_ligacao(windows):
    windows.exige_ligacao = True
    controle = janelas.Janelas(janelas.SistemaWindows())
    assert controle.fechar_aba("instagram").executar() == ["Fechei a aba 'instagram' no chrome."]
    assert windows.frente == TERMINAL and windows.ligacoes == []


def test_sem_foco_nenhuma_tecla_sai(windows):
    windows.trava_foco = True
    controle = janelas.Janelas(janelas.SistemaWindows())
    with pytest.raises(OSError, match="não deixou trazer o navegador"):
        controle.fechar_aba("instagram").executar()
    assert [t for t in windows.teclas if t[0] != 0x12] == []  # no máximo os toques de Alt da tentativa de foco
    assert windows.mesa[CHROME]["titulo"] == "Instagram - Google Chrome"


def test_fechar_a_ultima_aba_conta_como_fechada_mesmo_com_a_janela_ainda_escondida(windows):
    """Visto no Wine: a janela some da tela, mas o Windows ainda a conhece, com o mesmo título."""
    controle = janelas.Janelas(janelas.SistemaWindows())
    plano = controle.fechar_aba("instagram")
    windows.mesa[CHROME]["some_ao_fechar"] = True
    assert plano.executar() == ["Fechei a aba 'instagram' no chrome."]


# --- medidas, mouse, texto e foto ----------------------------------------------------------

SOBE, DESCE, RODA = 0x0004, 0x0002, 0x0800


def test_pede_pixels_de_verdade_ao_windows(windows):
    sistema = janelas.SistemaWindows()
    assert windows.dpi[:2] == [("processo", -4), ("thread", -4)]
    windows.dpi.clear()
    sistema.retangulo(CHROME), sistema.janela_em(0, 0), sistema.clicar(1, 1), sistema.roda(1, 1, 1), sistema.fotografar((0, 0, 1, 1), 1, 1)
    assert windows.dpi == [("thread", -4)] * 5  # a cada operação com coordenadas


def test_windows_antigo_sem_ajuste_de_escala_ainda_funciona(windows):
    del windows.user32.SetProcessDpiAwarenessContext, windows.user32.SetThreadDpiAwarenessContext
    sistema = janelas.SistemaWindows()
    assert sistema.retangulo(CHROME) == (100, 50, 1700, 1050)
    sistema.clicar(10, 10)


def test_retangulo_e_a_moldura_visivel(windows):
    assert janelas.SistemaWindows().retangulo(CHROME) == (100, 50, 1700, 1050)  # sem os 7 pixels de borda invisível


def test_retangulo_cai_para_a_medida_comum_se_a_moldura_falhar(windows):
    windows.sem_moldura = True
    assert janelas.SistemaWindows().retangulo(CHROME) == (93, 50, 1707, 1057)


def test_retangulo_e_cortado_na_area_de_trabalho(windows):
    windows.mesa[CHROME]["caixa"] = (-8, -8, 1928, 1088)  # maximizada: passa da tela
    assert janelas.SistemaWindows().retangulo(CHROME) == (-8, 0, 1920, 1080)
    windows.mesa[CHROME]["caixa"] = (-2500, -50, -1000, 2000)  # metade para fora da tela da esquerda
    assert janelas.SistemaWindows().retangulo(CHROME) == (-1920, 0, -1000, 1080)


def test_janela_em_devolve_a_dona_do_que_esta_no_ponto(windows):
    sistema = janelas.SistemaWindows()
    assert sistema.janela_em(5, 5) == TERMINAL  # o terminal está na frente e cobre esse ponto
    assert sistema.janela_em(1650, 1000) == CHROME
    assert sistema.janela_em(3000, 900) == 0  # nada ali
    windows.mesa[BLOCO]["minimizada"] = False
    assert sistema.janela_em(2200, 200) == BLOCO  # o diálogo "Salvar como" responde pelo bloco de notas


def test_clicar_move_o_ponteiro_e_aperta_o_botao_esquerdo(windows):
    janelas.SistemaWindows().clicar(900, 500)
    assert windows.mouse == [("move", 900, 500), (DESCE, 0, (900, 500)), (SOBE, 0, (900, 500))]


def test_clique_duplo_sao_dois_cliques_no_mesmo_ponto(windows):
    janelas.SistemaWindows().clicar(900, 500, duplo=True)
    assert [m[0] for m in windows.mouse] == ["move", DESCE, SOBE, DESCE, SOBE]


def test_roda_desce_e_sobe_e_devolve_o_ponteiro(windows):
    sistema = janelas.SistemaWindows()
    sistema.roda(900, 500, -3)
    assert windows.mouse == [("move", 900, 500), *[(RODA, 0xFFFFFF88, (900, 500))] * 3, ("move", 5, 5)]  # -120 em 32 bits
    windows.mouse.clear()
    sistema.roda(900, 500, 2)
    assert windows.mouse == [("move", 900, 500), *[(RODA, 120, (900, 500))] * 2, ("move", 5, 5)]


def test_digitar_manda_cada_letra_como_caractere(windows):
    janelas.SistemaWindows().digitar("pão 😀")
    desce, sobe = 0x0004, 0x0006
    unidades = [ord("p"), ord("ã"), ord("o"), ord(" "), 0xD83D, 0xDE00]  # o emoji vai em duas metades
    assert windows.texto == [par for u in unidades for par in ((u, desce), (u, sobe))]
    assert windows.teclas == []  # nenhuma tecla de verdade: só caracteres


def test_digitacao_recusada_pelo_windows_vira_erro(windows):
    windows.recusa_texto = True
    with pytest.raises(OSError, match="recusou a digitação"):
        janelas.SistemaWindows().digitar("oi")


def test_teclas_de_midia_e_de_navegacao_saem_marcadas(windows):
    sistema = janelas.SistemaWindows()
    sistema.teclas(janelas.MIDIA["tocar_pausar"])
    sistema.teclas(janelas.CTRL, janelas.END)
    sistema.teclas(*janelas.TECLAS_DE_PAGINA["k"])
    sistema.teclas(*janelas.TECLAS_DE_PAGINA["delete"])
    sistema.teclas(*janelas.TECLAS_DE_PAGINA["salvar"])
    assert windows.teclas == [
        (0xB3, 1), (0xB3, 3), (0x11, 0), (0x23, 1), (0x23, 3), (0x11, 2), (0x4B, 0), (0x4B, 2),
        (0x2E, 1), (0x2E, 3), (0x11, 0), (0x53, 0), (0x53, 2), (0x11, 2),
    ]  # fmt: skip


def test_ver_e_clicar_com_logica_e_sistema_juntos(windows):
    controle = janelas.Janelas(janelas.SistemaWindows())
    foto = controle.ver()
    assert windows.frente == CHROME and windows.fotos == [((100, 50, 1700, 1050), 1264, 790)]
    assert (foto.largura, foto.altura, foto.dados) == (1264, 790, b"jpeg")
    assert controle.clicar(632, 395).executar() == ["Cliquei em (632, 395)."]
    assert windows.mouse == [("move", 900, 550), (DESCE, 0, (900, 550)), (SOBE, 0, (900, 550))]  # o centro da janela
    assert controle.digitar("gatos", True).executar() == ["Digitei o texto e apertei Enter."]
    assert [chr(u) for u, flags in windows.texto if flags == 4] == list("gatos")
    assert windows.teclas == [(0x0D, 0), (0x0D, 2)]
    controle.fim_do_turno()
    assert windows.frente == TERMINAL


def test_nao_clica_se_o_terminal_cobre_o_ponto(windows):
    controle = janelas.Janelas(janelas.SistemaWindows())
    controle.ver()
    windows.user32.GetAncestor = lambda h, qual: TERMINAL  # uma janela sempre-no-topo cobre o navegador
    with pytest.raises(OSError, match="outra janela por cima"):
        controle.clicar(10, 10).executar()
    assert [m for m in windows.mouse if m[0] != "move"] == []


def test_rolar_com_logica_e_sistema_juntos(windows):
    controle = janelas.Janelas(janelas.SistemaWindows())
    assert controle.rolar("baixo", 2).executar() == ["Rolei a página para baixo."]
    assert windows.mouse[0] == ("move", 900, 650) and windows.mouse[-1] == ("move", 5, 5)
    assert [m[:2] for m in windows.mouse[1:-1]] == [(RODA, 0xFFFFFF88)] * 12


def test_foto_da_tela_devolve_jpeg_no_tamanho_pedido(monkeypatch):
    pytest.importorskip("PIL")
    import io

    from PIL import Image, ImageGrab

    pedidos = []

    def grab(bbox=None, all_screens=False):
        pedidos.append((bbox, all_screens))
        return Image.new("RGBA", (bbox[2] - bbox[0], bbox[3] - bbox[1]), (200, 30, 30, 255))

    monkeypatch.setattr(ImageGrab, "grab", grab, raising=False)
    dados = janelas.foto_da_tela((0, 0, 1600, 1000), 1264, 790)
    imagem = Image.open(io.BytesIO(dados))
    assert imagem.format == "JPEG" and imagem.size == (1264, 790) and imagem.mode == "RGB"
    assert pedidos == [((0, 0, 1600, 1000), True)]  # todas as telas: a janela pode estar no segundo monitor


def test_dona_de_um_dialogo_e_a_janela_principal(windows):
    sistema = janelas.SistemaWindows()
    assert sistema.dona(DIALOGO) == BLOCO  # "Salvar como" pertence ao bloco de notas
    assert sistema.dona(CHROME) == CHROME and sistema.dona(0) == 0


def test_dialogo_na_frente_conta_como_o_programa_e_recebe_o_texto(windows):
    windows.mesa[BLOCO].update(minimizada=False, caixa=(300, 100, 1000, 700))
    windows.mesa[DIALOGO]["caixa"] = (350, 150, 800, 500)
    controle = janelas.Janelas(janelas.SistemaWindows(), {"bloco de notas": "notepad"})
    controle.ver_programa("bloco de notas")
    assert windows.frente == BLOCO and windows.fotos[-1][0] == (300, 100, 1000, 700)
    windows.frente = DIALOGO  # o programa abriu o "Salvar como"
    assert controle.digitar("lista", True).executar() == ["Digitei o texto e apertei Enter."]
    assert [chr(u) for u, flags in windows.texto if flags == 4] == list("lista")
    assert controle.clicar(100, 100).executar() == ["Cliquei em (100, 100)."]  # o ponto é do diálogo, que responde pelo bloco


def test_dialogo_ja_fechado_em_primeiro_plano_conta_como_a_dona(windows):
    """Visto no Wine: depois do Esc, o diálogo escondido ainda aparecia como janela em primeiro plano."""
    sistema = janelas.SistemaWindows()
    windows.mesa[BLOCO]["minimizada"] = False
    windows.mesa[DIALOGO]["visivel"] = False
    windows.frente = DIALOGO
    assert sistema.ativa() == BLOCO
    windows.frente = INVISIVEL  # escondida e sem dona: ninguém está na frente
    assert sistema.ativa() == 0
