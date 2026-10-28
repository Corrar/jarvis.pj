"""Dentro da página: rolar, mídia, ver, clicar, digitar e teclas, com o Windows de mentira."""

import pytest

from jarvis.acoes import PROGRAMAS_PADRAO, ErroAcao
from jarvis.janelas import CTRL, END, ENTER, HOME, MIDIA, PGDN, TECLAS_DE_PAGINA, W, Janelas, reservada

from sistema_falso import Navegador, Programa, SistemaFalso, mesa


@pytest.fixture
def sistema():
    return mesa()


@pytest.fixture
def janelas(sistema):
    return Janelas(sistema, PROGRAMAS_PADRAO)


# --- rolar -----------------------------------------------------------------------


def test_rolar_usa_a_roda_do_mouse_sobre_o_navegador_da_frente(janelas, sistema):
    plano = janelas.rolar("baixo")
    assert plano.titulo == "rolar a página" and plano.linhas == ["para baixo, uma tela"]
    assert sistema.rodadas == []  # planejar não mexe em nada
    assert plano.executar() == ["Rolei a página para baixo."]
    assert sistema.rodadas == [(10, 800, 600, -6)]  # no meio da janela, um pouco abaixo do centro; negativo desce
    assert sistema.teclas_enviadas == [] and sistema.cliques == []
    assert sistema.frente == 10  # o foco só volta ao terminal no fim do turno
    janelas.fim_do_turno()
    assert sistema.frente == 1


def test_rolar_para_cima_varias_telas(janelas, sistema):
    assert janelas.rolar("cima", 3).linhas == ["para cima, 3 telas"]
    janelas.rolar("cima", 3).executar()
    assert sistema.rodadas == [(10, 800, 600, 18)]


def test_rolar_respeita_onde_a_janela_esta(janelas, sistema):
    sistema.retangulos[10] = (-1920, 40, -960, 1040)  # segundo monitor, à esquerda
    janelas.rolar("baixo").executar()
    assert sistema.rodadas == [(10, -1440, 640, -6)]


def test_topo_e_fim_usam_o_teclado(janelas, sistema):
    assert janelas.rolar("topo").executar() == ["Fui para o topo da página."]
    assert janelas.rolar("fim").executar() == ["Fui para o fim da página."]
    assert sistema.teclas_enviadas == [(10, (CTRL, HOME)), (10, (CTRL, END))]
    assert sistema.rodadas == []


def test_rolar_em_uma_aba_pelo_titulo(janelas, sistema):
    plano = janelas.rolar("baixo", 2, "instagram")
    assert plano.linhas == ["para baixo, 2 telas, na aba 'instagram'"]
    plano.executar()
    assert sistema.janelas[10].ativa == 2  # trocou para a aba do Instagram
    assert sistema.teclas_enviadas == [(10, (CTRL, PGDN)), (10, (CTRL, PGDN))]
    assert sistema.rodadas == [(10, 800, 600, -12)]


def test_rolar_aba_que_nao_existe(janelas, sistema):
    with pytest.raises(OSError, match="não achei nenhuma aba com 'netflix'"):
        janelas.rolar("baixo", 1, "netflix").executar()
    assert sistema.rodadas == []


def test_nao_rola_com_outra_janela_por_cima(janelas, sistema):
    sistema.por_cima = 30  # o bloco de notas está sempre-no-topo, em cima do navegador
    with pytest.raises(OSError, match="outra janela por cima"):
        janelas.rolar("baixo").executar()
    assert sistema.rodadas == []


@pytest.mark.parametrize("direcao, telas, aba", [("lado", 1, None), ("baixo", 0, None), ("baixo", 11, None), ("baixo", True, None), ("baixo", "2", None), ("baixo", 1, "x"), ("baixo", 1, 7)])
def test_pedido_de_rolagem_invalido(janelas, direcao, telas, aba):
    with pytest.raises(ErroAcao):
        janelas.rolar(direcao, telas, aba)


def test_rolar_sem_navegador(sistema):
    with pytest.raises(ErroAcao, match="nenhuma janela de navegador"):
        Janelas(SistemaFalso({1: sistema.janelas[1], 30: sistema.janelas[30]})).rolar("baixo")


# --- mídia ------------------------------------------------------------------------


def test_midia_manda_a_tecla_de_midia_sem_trocar_de_janela(janelas, sistema):
    plano = janelas.midia("tocar_pausar")
    assert plano.titulo == "comando de mídia" and plano.linhas == ["tocar_pausar"]
    assert plano.executar() == ["Enviei o comando de mídia: tocar_pausar."]
    assert sistema.midia == [MIDIA["tocar_pausar"]]
    assert sistema.frente == 1 and sistema.teclas_enviadas == []


def test_volume_repete_e_o_resto_nao(janelas, sistema):
    janelas.midia("volume_mais", 5).executar()
    assert sistema.midia == [MIDIA["volume_mais"]] * 5
    sistema.midia.clear()
    janelas.midia("tocar_pausar", 4).executar()  # repetir desfaria o comando
    janelas.midia("mudo", 2).executar()
    assert sistema.midia == [MIDIA["tocar_pausar"], MIDIA["mudo"]]


@pytest.mark.parametrize("comando, vezes", [("desligar", 1), (None, 1), ("volume_mais", 0), ("volume_mais", 21), ("volume_mais", True)])
def test_midia_invalida(janelas, comando, vezes):
    with pytest.raises(ErroAcao):
        janelas.midia(comando, vezes)


# --- ver ---------------------------------------------------------------------------


def test_ver_traz_o_navegador_e_fotografa_a_janela_inteira(janelas, sistema):
    foto = janelas.ver()
    assert sistema.frente == 10
    assert (foto.largura, foto.altura) == (1264, 790)  # 1600x1000 encolhida para caber em um milhão de pixels
    assert foto.largura * foto.altura <= 1_000_000
    assert sistema.fotos == [((0, 0, 1600, 1000), 1264, 790)]
    assert foto.dados == b"JPEG 1264x790 de Gmail - Google Chrome"


def test_janela_pequena_vai_sem_encolher(janelas, sistema):
    sistema.retangulos[10] = (100, 50, 1000, 750)
    foto = janelas.ver()
    assert (foto.largura, foto.altura) == (900, 700)


def test_janela_muito_larga_respeita_o_lado_maximo(janelas, sistema):
    sistema.retangulos[10] = (0, 0, 3440, 500)
    foto = janelas.ver()
    assert foto.largura == 1280 and foto.altura == 186


def test_ver_uma_aba_pelo_titulo(janelas, sistema):
    janelas.ver("mercado livre")
    assert sistema.frente == 20 and sistema.fotos[-1][0] == (0, 0, 1600, 1000)


def test_janela_minuscula_nao_e_fotografada(janelas, sistema):
    sistema.retangulos[10] = (0, 0, 150, 900)
    with pytest.raises(OSError, match="pequena demais"):
        janelas.ver()


@pytest.mark.parametrize(
    "titulo",
    ["Nubank", "Internet Banking Caixa Econômica", "Itaú - Feito para você", "PIX - Transferir", "Pagamento | Mercado Livre", "Entrar com senha", "gov.br - Acesse sua conta", "Checkout - Loja", "Banco do Brasil"],
)
def test_pagina_de_banco_pagamento_ou_senha_nao_e_vista(titulo):
    sistema = SistemaFalso({1: Programa("WindowsTerminal.exe", "jarvis"), 10: Navegador("chrome.exe", [titulo, "Gmail"])})
    janelas = Janelas(sistema)
    with pytest.raises(ErroAcao, match="banco, pagamento ou senha"):
        janelas.ver()
    assert sistema.fotos == []
    with pytest.raises(ErroAcao, match="banco, pagamento ou senha"):
        janelas.tecla("enter").executar()
    assert sistema.teclas_enviadas == []


@pytest.mark.parametrize(
    "titulo", ["Caixa de entrada (3) - Gmail", "Pixel 9 review - YouTube", "Instagram", "Bancos de dados - Wikipédia", "Embankment - Maps", "Passeio - Booking.com"]
)
def test_titulos_parecidos_nao_sao_reservados(titulo):
    assert reservada(titulo + " - Google Chrome") is None


# --- clicar ---------------------------------------------------------------------------


def test_clicar_exige_ter_visto_a_pagina_neste_turno(janelas, sistema):
    with pytest.raises(ErroAcao, match="Veja a janela antes"):
        janelas.clicar(10, 10)
    janelas.ver()
    janelas.clicar(10, 10)  # agora pode
    janelas.fim_do_turno()
    with pytest.raises(ErroAcao, match="Veja a janela antes"):
        janelas.clicar(10, 10)  # a foto do turno anterior não vale mais
    assert sistema.cliques == []


def test_clique_converte_o_ponto_da_foto_em_ponto_da_tela(janelas, sistema):
    sistema.retangulos[10] = (200, 100, 1800, 1100)  # 1600x1000 → foto de 1264x790
    janelas.ver()
    plano = janelas.clicar(632, 395)
    assert plano.titulo == "clicar na janela" and plano.linhas == ["ponto (632, 395) da foto, em: Gmail"]
    assert sistema.cliques == []
    assert plano.executar() == ["Cliquei em (632, 395)."]
    ((hwnd, x, y, duplo),) = sistema.cliques
    assert hwnd == 10 and duplo is False
    assert abs(x - 1000) <= 1 and abs(y - 600) <= 1  # o centro da foto é o centro da janela


@pytest.mark.parametrize("ponto, esperado", [((0, 0), (200, 100)), ((1263, 789), (1799, 1099))])
def test_cantos_da_foto_caem_dentro_da_janela(janelas, sistema, ponto, esperado):
    sistema.retangulos[10] = (200, 100, 1800, 1100)
    janelas.ver()
    janelas.clicar(*ponto).executar()
    _, x, y, _ = sistema.cliques[0]
    assert abs(x - esperado[0]) <= 1 and abs(y - esperado[1]) <= 1
    assert 200 <= x < 1800 and 100 <= y < 1100


def test_clique_duplo(janelas, sistema):
    janelas.ver()
    plano = janelas.clicar(100, 100, True)
    assert plano.titulo == "dar dois cliques na janela"
    assert plano.executar() == ["Cliquei duas vezes em (100, 100)."]
    assert sistema.cliques[0][3] is True
    janelas.clicar(100, 100, "sim").executar()  # só o booleano true vale
    assert sistema.cliques[1][3] is False


@pytest.mark.parametrize("x, y", [(-1, 10), (10, -1), (1264, 10), (10, 790), (5000, 5000), (1.5, 2), ("10", 10), (True, 10), (None, None)])
def test_ponto_fora_da_foto_ou_invalido(janelas, sistema, x, y):
    janelas.ver()
    with pytest.raises(ErroAcao):
        janelas.clicar(x, y)
    assert sistema.cliques == []


def test_nao_clica_se_a_janela_mudou_de_lugar(janelas, sistema):
    janelas.ver()
    plano = janelas.clicar(100, 100)
    sistema.retangulos[10] = (50, 50, 1650, 1050)  # o usuário arrastou a janela
    with pytest.raises(OSError, match="mudou de lugar"):
        plano.executar()
    assert sistema.cliques == []


def test_nao_clica_com_outra_janela_por_cima_do_ponto(janelas, sistema):
    janelas.ver()
    sistema.por_cima = 30
    with pytest.raises(OSError, match="outra janela por cima"):
        janelas.clicar(100, 100).executar()
    assert sistema.cliques == []


def test_nao_clica_se_a_aba_virou_pagina_reservada(janelas, sistema):
    janelas.ver()
    plano = janelas.clicar(100, 100)
    sistema.janelas[10].abas[0] = "Nubank"  # a página navegou
    with pytest.raises(ErroAcao, match="banco, pagamento ou senha"):
        plano.executar()
    assert sistema.cliques == []


def test_nao_clica_se_a_janela_fechou(janelas, sistema):
    janelas.ver()
    plano = janelas.clicar(100, 100)
    del sistema.janelas[10]
    with pytest.raises(OSError, match="fechou"):
        plano.executar()


def test_clique_traz_o_navegador_de_volta_se_o_terminal_tomou_a_frente(janelas, sistema):
    janelas.ver()
    sistema.frente = 1
    janelas.clicar(100, 100).executar()
    assert sistema.cliques[0][0] == 10


def test_sem_foco_nao_ha_clique(janelas, sistema):
    janelas.ver()
    sistema.frente = 1
    sistema.nao_focam = {10}
    with pytest.raises(OSError, match="não deixou trazer"):
        janelas.clicar(100, 100).executar()
    assert sistema.cliques == []


def test_rever_fotografa_de_novo_a_mesma_janela(janelas, sistema):
    assert janelas.rever() is None  # nada visto ainda
    janelas.ver()
    janelas.clicar(100, 100).executar()
    foto = janelas.rever()
    assert foto is not None and len(sistema.fotos) == 2
    sistema.frente = 1
    assert janelas.rever() is None  # o navegador não está mais na frente: sem foto, sem engano


# --- digitar e teclas --------------------------------------------------------------------


def test_digitar_vai_para_a_janela_vista_e_pode_dar_enter(janelas, sistema):
    janelas.ver()
    plano = janelas.digitar("receita de pão de queijo", True)
    assert plano.titulo == "digitar na janela" and plano.linhas == ["receita de pão de queijo  [Enter]"]
    assert sistema.digitado == []
    assert plano.executar() == ["Digitei o texto e apertei Enter."]
    assert "".join(t for _, t in sistema.digitado) == "receita de pão de queijo"
    assert {h for h, _ in sistema.digitado} == {10}
    assert sistema.teclas_enviadas == [(10, (ENTER,))]


def test_digitar_sem_enter(janelas, sistema):
    janelas.ver()
    assert janelas.digitar("olá").executar() == ["Digitei o texto."]
    assert sistema.digitado == [(10, "olá")] and sistema.teclas_enviadas == []


def test_digitar_exige_ter_visto_a_pagina(janelas):
    with pytest.raises(ErroAcao, match="Veja a janela antes"):
        janelas.digitar("oi")


@pytest.mark.parametrize("texto", ["", None, 7, "a" * 301, "linha\nquebrada", "com\ttab", "sino\x07"])
def test_texto_invalido(janelas, texto):
    janelas.ver()
    with pytest.raises(ErroAcao):
        janelas.digitar(texto)


def test_para_de_digitar_se_outra_janela_toma_a_frente(janelas, sistema):
    janelas.ver()
    sistema.sai_da_frente_ao_digitar = 1  # o terminal pula na frente depois do primeiro lote
    with pytest.raises(OSError, match="parei de digitar"):
        janelas.digitar("x" * 100).executar()
    assert sistema.digitado == [(10, "x" * 20)]  # só o primeiro lote saiu, e foi para o navegador


def test_tecla_de_pagina_sem_foto_vai_para_o_navegador_da_frente(janelas, sistema):
    plano = janelas.tecla("k")
    assert plano.titulo == "apertar uma tecla na janela" and plano.linhas == ["k"]
    assert plano.executar() == ["Apertei a tecla k."]
    assert sistema.teclas_enviadas == [(10, TECLAS_DE_PAGINA["k"])]


def test_tecla_depois_de_ver_vai_para_a_janela_vista(janelas, sistema):
    janelas.ver("bing")
    janelas.tecla("esc").executar()
    assert sistema.teclas_enviadas[-1] == (20, TECLAS_DE_PAGINA["esc"])


@pytest.mark.parametrize("nome", ["ctrl+w", "w", "f4", "alt", "ctrl+l", "win+r", "", None, 13])
def test_so_as_teclas_da_lista(janelas, nome):
    with pytest.raises(ErroAcao, match="Tecla desconhecida"):
        janelas.tecla(nome)


def test_lista_de_teclas_nao_tem_alt_win_nem_atalho_de_aba_ou_de_janela():
    combinacoes = set(TECLAS_DE_PAGINA.values())
    assert all(isinstance(c, tuple) and c for c in combinacoes)
    usadas = {codigo for c in combinacoes for codigo in c}
    assert not {0x12, 0x5B, 0x5C, 0x10} & usadas  # nada de Alt, Win ou Shift
    com_ctrl = {c for c in combinacoes if CTRL in c}
    assert com_ctrl == {(CTRL, 0x53), (CTRL, 0x5A), (CTRL, 0x43), (CTRL, 0x56), (CTRL, 0x41)}  # salvar, desfazer, copiar, colar, selecionar tudo
    proibidas = {(CTRL, W), (CTRL, 0x54), (CTRL, 0x4E), (CTRL, PGDN), (CTRL, 0x4C), (0x73,)}  # fechar aba, nova aba, nova janela, trocar aba, barra de endereço, F4
    assert not proibidas & combinacoes


# --- foco no fim do turno -------------------------------------------------------------------


def test_fim_do_turno_devolve_o_foco_so_se_mexeu_na_pagina(janelas, sistema):
    janelas.fim_do_turno()
    assert sistema.frente == 1
    sistema.frente = 40  # o usuário está no Excel e só conversou
    janelas.fim_do_turno()
    assert sistema.frente == 40  # ninguém mexeu: fica onde está


def test_fim_do_turno_pode_deixar_o_foco_no_navegador(janelas, sistema):
    janelas.ver()
    janelas.fim_do_turno(devolver_foco=False)
    assert sistema.frente == 10
    janelas.fim_do_turno()  # e não devolve depois, por conta de um turno antigo
    assert sistema.frente == 10


def test_pedir_uma_janela_na_frente_vence_a_devolucao_do_foco(janelas, sistema):
    janelas.rolar("baixo").executar()
    janelas.focar("excel").executar()
    janelas.fim_do_turno()
    assert sistema.frente == 40


def test_ver_pode_esperar_a_pagina_carregar(janelas, sistema):
    janelas.ver()
    sem_espera = sistema.tempo_parado
    janelas.ver(None, 2.5)
    assert sistema.tempo_parado - sem_espera == pytest.approx(sem_espera + 2.5)


@pytest.mark.parametrize("esperar", [-1, 5.5, "2", True])
def test_espera_invalida(janelas, sistema, esperar):
    with pytest.raises(ErroAcao, match="esperar"):
        janelas.ver(None, esperar)
    assert sistema.fotos == []


def test_trazer_o_terminal_para_frente(janelas, sistema):
    sistema.frente = 10
    assert janelas.trazer_terminal() is True and sistema.frente == 1
    sistema.propria = 0  # o Jarvis não sabe qual é a janela dele
    sistema.frente = 10
    assert janelas.trazer_terminal() is False and sistema.frente == 10
