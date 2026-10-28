"""Segundo plano: o terminal escondido, um Jarvis só, o início com o Windows e o ícone perto do relógio."""

import subprocess
import types
from pathlib import Path

import pytest

from jarvis import segundo_plano
from jarvis.segundo_plano import (
    ARGUMENTO,
    CLASSICO,
    ESTADOS,
    PROPRIO,
    Bandeja,
    BandejaMuda,
    Bloqueio,
    Console,
    ConsoleAusente,
    InicioAutomatico,
    Instancia,
    assumir_console,
    comando_de_inicio,
    console_atual,
    desenhar_icone,
    ja_esta_rodando,
    lancar,
)

# --- a janela do terminal ---------------------------------------------------------------------------


class Janelas:
    """user32 e kernel32 falsos: uma janela de console e o que foi feito com ela."""

    def __init__(self, console=100, classe="ConsoleWindowClass", dona=0, visivel=True, minimizada=False):
        self.console, self.classe, self.dona = console, classe, dona
        self.visiveis = {console} if visivel else set()
        self.minimizadas = {console} if minimizada else set()
        self.feito = []
        self.titulo = ""
        self.modo = 0x0001 | 0x0004 | 0x0040  # um terminal comum: com a edição rápida ligada

    # kernel32
    def GetConsoleWindow(self):  # noqa: N802 - nomes da API do Windows
        return self.console

    def SetConsoleTitleW(self, texto):  # noqa: N802
        self.titulo = texto
        return 1

    def GetStdHandle(self, qual):  # noqa: N802
        assert qual == -10
        return 9

    def GetConsoleMode(self, alca, destino):  # noqa: N802
        destino._obj.value = self.modo
        return 1

    def SetConsoleMode(self, alca, modo):  # noqa: N802
        self.modo = modo
        return 1

    # user32
    def GetClassNameW(self, hwnd, texto, tamanho):  # noqa: N802
        texto.value = self.classe
        return len(self.classe)

    def GetAncestor(self, hwnd, qual):  # noqa: N802
        assert qual == 3
        return self.dona or hwnd

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return hwnd in self.visiveis

    def IsIconic(self, hwnd):  # noqa: N802
        return hwnd in self.minimizadas

    def ShowWindow(self, hwnd, como):  # noqa: N802
        self.feito.append(("mostrar", hwnd, como))
        if como == 0:
            self.visiveis.discard(hwnd)
        else:
            self.visiveis.add(hwnd)
        if como == 9:
            self.minimizadas.discard(hwnd)
        return 1

    def SetForegroundWindow(self, hwnd):  # noqa: N802
        self.feito.append(("frente", hwnd))
        return 1

    def GetSystemMenu(self, hwnd, restaurar):  # noqa: N802
        return 55

    def DeleteMenu(self, menu, item, como):  # noqa: N802
        self.feito.append(("sem", menu, item, como))
        return 1

    def DrawMenuBar(self, hwnd):  # noqa: N802
        self.feito.append(("redesenhar", hwnd))
        return 1


def test_console_classico_some_e_volta_por_inteiro():
    w = Janelas()
    console = Console(w, w)
    assert console.existe and console.real and console.janela == 100 and console.visivel
    assert console.esconder() and not console.visivel
    assert w.feito == [("mostrar", 100, 0)]
    assert console.mostrar() and console.visivel
    assert w.feito[1:] == [("mostrar", 100, 5), ("frente", 100)]  # mostra e traz para frente


def test_o_clique_no_icone_alterna_e_restaura_o_que_estava_minimizado():
    w = Janelas(visivel=False)
    console = Console(w, w)
    assert console.alternar() is True and console.visivel  # estava escondido: mostra
    assert console.alternar() is False and not console.visivel  # estava à vista: esconde
    console.mostrar()
    w.minimizadas.add(100)  # o usuário minimizou: o clique traz de volta, não esconde
    w.feito.clear()
    assert console.alternar() is True
    assert ("mostrar", 100, 9) in w.feito and 100 not in w.minimizadas


def test_console_perde_o_x_e_ganha_nome():
    w = Janelas()
    console = Console(w, w)
    console.sem_fechar()
    console.titulo("Jarvis")
    assert ("sem", 55, 0xF060, 0) in w.feito and ("redesenhar", 100) in w.feito
    assert w.titulo == "Jarvis"


def test_console_sem_edicao_rapida_nao_congela_com_um_clique():
    w = Janelas()
    Console(w, w).sem_selecao()
    assert w.modo == 0x0001 | 0x0004 | 0x0080  # tirou a edição rápida, manteve o resto e marcou as opções estendidas


def test_primeiro_mostrar_de_um_processo_que_nasceu_escondido_e_repetido():
    class Teimoso(Janelas):
        """Como o Windows faz com quem foi criado escondido: troca o primeiro "mostrar" por "esconder"."""

        primeira = True

        def ShowWindow(self, hwnd, como):  # noqa: N802
            if como == 5 and self.primeira:
                self.primeira, como = False, 0
            return super().ShowWindow(hwnd, como)

    w = Teimoso(visivel=False)
    console = Console(w, w)
    assert console.mostrar() and console.visivel


def test_dentro_do_windows_terminal_quem_obedece_e_o_hospedeiro():
    w = Janelas(console=7, classe="PseudoConsoleWindow", dona=900)
    console = Console(w, w)
    assert console.existe and not console.real and console.janela == 900
    assert console.visivel  # a fachada não diz se está à vista: vale o que o Jarvis pediu por último
    console.esconder()
    assert not console.visivel and w.feito == [("mostrar", 7, 0)]  # o pedido vai para a fachada; o hospedeiro reage
    w.minimizadas.add(900)
    console.mostrar()
    assert w.feito[1:] == [("mostrar", 7, 5), ("mostrar", 900, 9), ("frente", 900)]  # e a janela de verdade vem para frente
    w.feito.clear()
    console.sem_fechar()  # o X é do hospedeiro: não mexe
    assert w.feito == []


def test_sem_console_nada_acontece():
    w = Janelas(console=0)
    console = Console(w, w)
    assert not console.existe and not console.real and not console.visivel and console.janela == 0
    assert console.esconder() is False and console.mostrar() is False and console.alternar() is False
    console.sem_fechar()
    console.sem_selecao()
    console.titulo("Jarvis")
    assert w.feito == [] and w.titulo == "" and w.modo & 0x0040


def test_fora_do_windows_o_console_e_ausente():
    console = console_atual("linux")
    assert isinstance(console, ConsoleAusente)
    assert not console.existe and not console.visivel
    assert console.esconder() is False and console.mostrar() is False and console.alternar() is False
    console.sem_fechar()
    console.sem_selecao()
    console.titulo("x")


# --- um Jarvis só -----------------------------------------------------------------------------------


class Travas:
    def __init__(self, existentes=()):
        self.existentes, self.erro, self.fechadas = set(existentes), 0, []

    def CreateMutexW(self, _seguranca, _dono, nome):  # noqa: N802
        self.erro = 183 if nome in self.existentes else 0
        self.existentes.add(nome)
        return 321

    def OpenMutexW(self, _acesso, _herda, nome):  # noqa: N802
        return 654 if nome in self.existentes else 0

    def CloseHandle(self, alca):  # noqa: N802
        self.fechadas.append(alca)
        return 1


def test_um_jarvis_so_por_instalacao(tmp_path):
    k = Travas()
    assert not ja_esta_rodando(tmp_path / "a", k, "win32")
    primeira = Instancia(tmp_path / "a", k, "win32", lambda: k.erro)
    assert primeira.unica
    assert ja_esta_rodando(tmp_path / "a", k, "win32") and k.fechadas == [654]  # só olhou; não ficou com a trava
    assert not Instancia(tmp_path / "a", k, "win32", lambda: k.erro).unica
    assert not Instancia(Path(str(tmp_path / "a").upper()), k, "win32", lambda: k.erro).unica  # maiúsculas não importam no Windows
    assert Instancia(tmp_path / "b", k, "win32", lambda: k.erro).unica  # outra pasta, outro Jarvis
    assert all(nome.startswith("Local\\Jarvis-") for nome in k.existentes)


def test_trava_de_um_jarvis_elevado_tambem_conta():
    """O primeiro Jarvis roda como administrador: o segundo nem consegue abrir a trava, e isso já diz que ela existe."""

    class Negada:
        def CreateMutexW(self, *_):  # noqa: N802
            return 0

    assert not Instancia(kernel32=Negada(), sistema="win32", ultimo_erro=lambda: 5).unica
    assert Instancia(kernel32=Negada(), sistema="win32", ultimo_erro=lambda: 8).unica  # outro erro: sobe do mesmo jeito


def test_fora_do_windows_e_com_falha_na_trava_o_jarvis_sobe():
    assert Instancia(sistema="linux").unica
    assert not ja_esta_rodando(sistema="linux")

    class Quebrado:
        def CreateMutexW(self, *_):  # noqa: N802
            raise OSError("sem trava")

        def OpenMutexW(self, *_):  # noqa: N802
            raise OSError("sem trava")

    assert Instancia(kernel32=Quebrado(), sistema="win32", ultimo_erro=lambda: 0).unica
    assert not ja_esta_rodando(kernel32=Quebrado(), sistema="win32")


# --- tela bloqueada ---------------------------------------------------------------------------------


class Areas:
    """user32 falso: a área de trabalho que recebe o teclado se chama "Default" só com a sessão aberta."""

    def __init__(self, nome="Default"):
        self.nome, self.consultas, self.fechadas = nome, 0, []  # nome None: nem se deixa abrir

    def OpenInputDesktop(self, _opcoes, _herda, acesso):  # noqa: N802
        assert acesso == 0x80000000
        self.consultas += 1
        return 0 if self.nome is None else 77

    def GetUserObjectInformationW(self, alca, qual, destino, tamanho, _precisa):  # noqa: N802
        assert (alca, qual) == (77, 2) and tamanho >= 512
        destino.value = self.nome
        return 1

    def CloseDesktop(self, alca):  # noqa: N802
        self.fechadas.append(alca)
        return 1


def test_bloqueio_acompanha_a_tela_e_nao_consulta_a_cada_pedaco_de_som():
    u, agora = Areas(), [100.0]
    bloqueio = Bloqueio(u, "win32", lambda: agora[0])
    assert bloqueio.disponivel and bloqueio() is False
    u.nome = None  # Win+L: a área de trabalho da vez passa a ser a da tela de bloqueio, que não abre
    assert bloqueio() is False  # a resposta anterior ainda vale (até dois segundos)
    agora[0] += 2.1
    assert bloqueio() is True
    consultas = u.consultas
    for _ in range(50):
        assert bloqueio() is True
    assert u.consultas == consultas  # sem consultar o Windows cinquenta vezes por segundo
    u.nome = "Winlogon"  # ou abre, mas não é a do usuário
    agora[0] += 2.1
    assert bloqueio() is True
    u.nome = "default"
    agora[0] += 2.1
    assert bloqueio() is False
    assert u.fechadas and set(u.fechadas) == {77}  # cada consulta fecha o que abriu


def test_jarvis_que_sobe_com_a_tela_bloqueada_fica_surdo_ate_ela_abrir():
    """Depois de uma atualização o Windows entra sozinho e fica bloqueado: os programas sobem assim."""
    u, agora = Areas(nome=None), [0.0]
    bloqueio = Bloqueio(u, "win32", lambda: agora[0])
    assert bloqueio.disponivel and bloqueio() is True
    u.nome = "Default"
    agora[0] += 3
    assert bloqueio() is False


def test_sem_a_consulta_nada_e_bloqueado():
    assert Bloqueio(sistema="linux")() is False and not Bloqueio(sistema="linux").disponivel

    class Quebrado:
        def OpenInputDesktop(self, *_):  # noqa: N802
            raise OSError("sem acesso")

    quebrado = Bloqueio(Quebrado(), "win32")
    assert not quebrado.disponivel and quebrado() is False


def test_bloqueio_com_falha_no_meio_volta_a_ouvir():
    u, agora = Areas(), [0.0]
    bloqueio = Bloqueio(u, "win32", lambda: agora[0])
    u.nome = None
    agora[0] += 3
    assert bloqueio() is True

    def falha(*_):
        raise OSError("consulta caiu")

    u.OpenInputDesktop = falha
    agora[0] += 3
    assert bloqueio() is False


# --- subir escondido --------------------------------------------------------------------------------


def _python(tmp_path, *nomes):
    for nome in nomes:
        (tmp_path / nome).write_bytes(b"")
    return str(tmp_path / nomes[0])


def test_lancar_sobe_num_terminal_proprio_e_escondido(tmp_path):
    chamadas = []
    executavel = _python(tmp_path, "pythonw.exe", "python.exe")  # chamado pelo iniciar.pyw, que roda sem terminal
    lancar(executavel=executavel, raiz=tmp_path, abrir=lambda *a, **k: chamadas.append((a, k)), escondida=lambda: "escondida")
    (comando,), opcoes = chamadas[0]
    assert comando == [str(tmp_path / "python.exe"), "-m", "jarvis", ARGUMENTO, PROPRIO]  # o python com terminal, não o pythonw
    assert opcoes == {"cwd": str(tmp_path), "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10), "startupinfo": "escondida", "close_fds": True}


def test_lancar_pelo_python_do_sistema_prefere_a_venv_da_pasta_do_jarvis(tmp_path):
    """Dois cliques no iniciar.pyw rodam com o Python do sistema; as bibliotecas estão na .venv do projeto."""
    sistema = tmp_path / "Python312"
    sistema.mkdir()
    executavel = _python(sistema, "pythonw.exe", "python.exe")
    da_venv = tmp_path / "jarvis" / ".venv" / "Scripts"
    da_venv.mkdir(parents=True)
    (da_venv / "python.exe").write_bytes(b"")
    escolha = segundo_plano._python_com_terminal
    assert escolha(executavel, tmp_path / "jarvis", em_ambiente=False) == str(da_venv / "python.exe")
    # Já dentro de um ambiente (o Windows chamou pelo pythonw dele): fica nele.
    assert escolha(executavel, tmp_path / "jarvis", em_ambiente=True) == str(sistema / "python.exe")
    assert escolha(executavel, tmp_path / "sem-venv", em_ambiente=False) == str(sistema / "python.exe")


def test_lancar_sem_python_vizinho_usa_o_que_tem(tmp_path):
    chamadas = []
    lancar(["--sem-painel"], executavel=str(tmp_path / "outro.exe"), raiz=tmp_path, abrir=lambda *a, **k: chamadas.append(a[0]), escondida=lambda: None)
    assert chamadas == [[str(tmp_path / "outro.exe"), "-m", "jarvis", ARGUMENTO, PROPRIO, "--sem-painel"]]


class ConsoleFalso:
    def __init__(self, real=True, existe=True, visivel=True):
        self.real, self.existe, self.visivel, self.janela, self.feito = real, existe, visivel, 100 if existe else 0, []

    def esconder(self):
        self.feito.append("esconder")
        self.visivel = False
        return self.existe

    def mostrar(self):
        self.feito.append("mostrar")
        self.visivel = True
        return self.existe

    def alternar(self):
        if self.visivel:
            self.esconder()
            return False
        return self.mostrar()

    def sem_fechar(self):
        self.feito.append("sem_fechar")

    def titulo(self, texto):
        self.feito.append(("titulo", texto))


def test_chamado_a_mao_sobe_outro_processo_e_devolve_o_terminal():
    lancados, avisos, console = [], [], ConsoleFalso()
    with pytest.raises(SystemExit) as fim:
        assumir_console([ARGUMENTO, "--sem-painel"], console, lancar=lancados.append, relancar=None, avisar=avisos.append)
    assert fim.value.code == 0
    assert lancados == [["--sem-painel"]]  # o lancar acrescenta --segundo-plano e a marca de terminal próprio
    assert console.feito == [] and "perto do relógio" in avisos[0]  # o terminal do usuário fica como estava


def test_no_terminal_proprio_e_classico_so_esconde():
    console = ConsoleFalso(real=True)
    assumir_console([ARGUMENTO, PROPRIO], console, lancar=None, relancar=None)
    assert console.feito == ["esconder"]


def test_dentro_do_windows_terminal_recomeca_uma_vez_no_classico():
    relancados, console = [], ConsoleFalso(real=False)
    with pytest.raises(SystemExit) as fim:
        assumir_console([ARGUMENTO, PROPRIO], console, lancar=None, relancar=relancados.append, subiu=lambda: True)
    assert fim.value.code == 0 and relancados == [[ARGUMENTO, PROPRIO]]
    assert console.feito == ["esconder"]  # enquanto o outro sobe, este já sai da frente
    # Na segunda vez (já marcado como clássico) fica onde está, mesmo que não tenha dado certo: sem laço.
    console.feito.clear()
    assumir_console([ARGUMENTO, CLASSICO], console, lancar=None, relancar=relancados.append, subiu=None)
    assert len(relancados) == 1 and console.feito == ["esconder"]


def test_se_o_recomeco_nao_der_sinal_de_vida_o_jarvis_fica_neste_terminal():
    relancados, console = [], ConsoleFalso(real=False)
    assumir_console([ARGUMENTO, PROPRIO], console, lancar=None, relancar=relancados.append, subiu=lambda: False)  # não sai
    assert relancados and console.feito == ["esconder"]


def test_esperar_o_outro_olha_a_trava_ate_o_prazo():
    respostas, pausas = iter([False, False, True]), []
    assert segundo_plano._esperar_o_outro(30, lambda: next(respostas), pausas.append) is True
    assert pausas == [0.5, 0.5]
    assert segundo_plano._esperar_o_outro(0.0, lambda: False, pausas.append) is False


def test_se_nao_der_para_recomecar_segue_no_terminal_que_tem():
    def sem_conhost(_argumentos):
        raise OSError("conhost.exe não encontrado")

    console = ConsoleFalso(real=False)
    assumir_console([ARGUMENTO, PROPRIO], console, lancar=None, relancar=sem_conhost, subiu=None)
    assert console.feito == ["esconder"]


def test_recomecar_no_classico_chama_o_conhost_pelo_nome(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    executavel = _python(tmp_path, "python.exe")
    segundo_plano._relancar_no_console_classico(
        [ARGUMENTO, PROPRIO, "--sem-painel"], executavel, tmp_path, lambda *a, **k: chamadas.append((a[0], k)), lambda: "escondida"
    )
    comando, opcoes = chamadas[0]
    assert comando[0].replace("/", "\\").endswith("System32\\conhost.exe")
    assert comando[1:] == [executavel, "-m", "jarvis", ARGUMENTO, "--sem-painel", CLASSICO]  # troca a marca; não acumula
    assert opcoes["cwd"] == str(tmp_path) and "creationflags" not in opcoes


# --- iniciar com o Windows --------------------------------------------------------------------------


class RegistroFalso:
    """winreg falso: um dicionário por chave."""

    HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.chaves: dict[str, dict[str, str]] = {}

    class _Chave:
        def __init__(self, valores):
            self.valores = valores

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def OpenKey(self, raiz, caminho, _reservado, _acesso):  # noqa: N802
        assert raiz == "HKCU"
        if caminho not in self.chaves:
            raise FileNotFoundError(caminho)
        return self._Chave(self.chaves[caminho])

    def CreateKeyEx(self, raiz, caminho, _reservado, _acesso):  # noqa: N802
        return self._Chave(self.chaves.setdefault(caminho, {}))

    def QueryValueEx(self, chave, nome):  # noqa: N802
        if nome not in chave.valores:
            raise FileNotFoundError(nome)
        return chave.valores[nome], 1

    def SetValueEx(self, chave, nome, _reservado, tipo, valor):  # noqa: N802
        assert tipo == 1
        chave.valores[nome] = valor

    def DeleteValue(self, chave, nome):  # noqa: N802
        if nome not in chave.valores:
            raise FileNotFoundError(nome)
        del chave.valores[nome]


CHAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_inicio_com_o_windows_liga_e_desliga():
    registro = RegistroFalso()
    inicio = InicioAutomatico(registro, comando='"pythonw.exe" "C:\\jarvis\\iniciar.pyw"')
    assert not inicio.ligado()
    inicio.desligar()  # desligar o que não está ligado não é erro
    inicio.ligar()
    assert inicio.ligado() and registro.chaves[CHAVE] == {"Jarvis": '"pythonw.exe" "C:\\jarvis\\iniciar.pyw"'}
    inicio.desligar()
    assert not inicio.ligado() and registro.chaves[CHAVE] == {}
    inicio.desligar()


def test_inicio_desligado_nas_configuracoes_do_windows_conta_como_desligado():
    escolhas = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    registro = RegistroFalso()
    inicio = InicioAutomatico(registro, comando="jarvis")
    inicio.ligar()
    registro.chaves[escolhas] = {"Jarvis": bytes([3, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]), "Outro": bytes([3] + [0] * 11)}
    assert not inicio.ligado()  # a linha existe, mas o usuário desligou pelo Windows
    registro.chaves[escolhas]["Jarvis"] = bytes([2] + [0] * 11)
    assert inicio.ligado()
    registro.chaves[escolhas]["Jarvis"] = bytes([3] + [0] * 11)
    InicioAutomatico(registro, comando="jarvis em outra pasta").conferir()
    assert registro.chaves[CHAVE]["Jarvis"] == "jarvis em outra pasta" and not inicio.ligado()  # corrigir o endereço não religa
    inicio.ligar()  # ligar de novo pelo Jarvis desfaz a escolha antiga, e só a dele
    assert inicio.ligado() and registro.chaves[escolhas] == {"Outro": bytes([3] + [0] * 11)}


def test_inicio_acompanha_a_pasta_do_jarvis():
    registro = RegistroFalso()
    InicioAutomatico(registro, comando="antigo").ligar()
    novo = InicioAutomatico(registro, comando="novo")
    novo.conferir()  # a pasta mudou: o Windows passa a chamar a nova
    assert registro.chaves[CHAVE]["Jarvis"] == "novo"
    novo.desligar()
    novo.conferir()  # desligado continua desligado
    assert not novo.ligado()


def test_comando_de_inicio_prefere_o_python_sem_janela(tmp_path):
    executavel = _python(tmp_path, "python.exe", "pythonw.exe")
    assert comando_de_inicio(executavel, tmp_path) == f'"{tmp_path / "pythonw.exe"}" "{tmp_path / "iniciar.pyw"}"'
    sozinho = tmp_path / "so"
    sozinho.mkdir()
    executavel = _python(sozinho, "python.exe")
    assert comando_de_inicio(executavel, tmp_path).startswith(f'"{sozinho / "python.exe"}" ')


def test_o_iniciar_pyw_existe_e_chama_o_lancar():
    arquivo = segundo_plano.RAIZ / "iniciar.pyw"
    texto = arquivo.read_text(encoding="utf-8")
    assert "from jarvis.segundo_plano import lancar" in texto and "lancar()" in texto
    compile(texto, str(arquivo), "exec")


def test_o_modulo_nao_puxa_nada_pesado():
    """Ele roda antes de tudo, para o terminal sumir logo: só biblioteca padrão."""
    import ast

    arvore = ast.parse(Path(segundo_plano.__file__).read_text(encoding="utf-8"))
    do_topo = set()
    for no in arvore.body:
        if isinstance(no, ast.Import):
            do_topo |= {a.name.split(".")[0] for a in no.names}
        elif isinstance(no, ast.ImportFrom):
            do_topo.add((no.module or "").split(".")[0])
    assert do_topo <= {"__future__", "ctypes", "hashlib", "logging", "os", "subprocess", "sys", "threading", "collections", "pathlib", "typing"}


# --- o ícone ----------------------------------------------------------------------------------------


class Item:
    def __init__(self, texto, acao, default=False, visible=True, checked=None):
        self._texto, self.acao, self.default, self._visivel, self._marcado = texto, acao, default, visible, checked

    @property
    def texto(self):
        return self._texto(self) if callable(self._texto) else self._texto

    @property
    def visivel(self):
        return self._visivel(self) if callable(self._visivel) else self._visivel

    @property
    def marcado(self):
        return None if self._marcado is None else self._marcado(self)


class IconeFalso:
    def __init__(self, nome, imagem, titulo, menu):
        self.nome, self.icon, self.title, self.menu = nome, imagem, titulo, menu
        self.rodou, self.parou, self.avisos, self.atualizacoes = False, False, [], 0

    def run(self):
        self.rodou = True

    def stop(self):
        self.parou = True

    def update_menu(self):
        self.atualizacoes += 1

    def notify(self, texto, titulo):
        self.avisos.append((titulo, texto))


def _pystray_falso():
    falso = types.SimpleNamespace(icones=[])

    class Menu(list):
        SEPARATOR = "---"

        def __init__(self, *itens):
            super().__init__(itens)

    def icone(*dados):
        novo = IconeFalso(*dados)
        falso.icones.append(novo)
        return novo

    falso.Menu, falso.MenuItem, falso.Icon = Menu, Item, icone
    return falso


def _bandeja():
    pystray = _pystray_falso()
    bandeja = Bandeja(pystray, desenhar=lambda cor, riscado=False: ("imagem", cor, riscado))
    bandeja.iniciar()
    return bandeja, pystray.icones[0]


def _visiveis(icone):
    return [i.texto for i in icone.menu if i != "---" and i.visivel]


def test_icone_sobe_carregando_e_so_com_sair():
    import time

    bandeja, icone = _bandeja()
    for _ in range(100):
        if icone.rodou:
            break
        time.sleep(0.01)
    assert icone.rodou and icone.nome == "jarvis"
    assert icone.icon == ("imagem", ESTADOS["carregando"][0], False) and icone.title == "Jarvis: carregando…"
    assert _visiveis(icone) == ["Sair"]  # enquanto carrega, o menu só oferece sair
    bandeja.parar()
    assert icone.parou
    bandeja.parar()  # duas vezes não é erro


def test_menu_do_icone_mostra_esconde_e_marca():
    bandeja, icone = _bandeja()
    estado = {"terminal": False, "microfone": True, "inicio": False}
    feito = []

    def alternar(nome):
        def agir():
            feito.append(nome)
            estado[nome] = not estado[nome]

        return agir

    bandeja.ligar(
        {"terminal": alternar("terminal"), "microfone": alternar("microfone"), "inicio": alternar("inicio"), "painel": lambda: feito.append("painel"), "sair": lambda: feito.append("sair")},
        {nome: (lambda nome=nome: estado[nome]) for nome in estado},
    )
    assert _visiveis(icone) == ["Mostrar o terminal", "Abrir o painel", "Microfone ligado", "Iniciar com o Windows", "Sair"]
    terminal, painel, microfone, inicio, _, sair = icone.menu
    assert terminal.default and not painel.default  # o clique simples no ícone é mostrar ou esconder o terminal
    assert (microfone.marcado, inicio.marcado) == (True, False)
    antes = icone.atualizacoes
    terminal.acao(icone, terminal)
    assert terminal.texto == "Esconder o terminal" and icone.atualizacoes == antes + 1  # o menu é refeito a cada ação
    microfone.acao()
    inicio.acao(icone)
    painel.acao()
    sair.acao()
    assert feito == ["terminal", "microfone", "inicio", "painel", "sair"]
    assert (microfone.marcado, inicio.marcado) == (False, True)


def test_icone_troca_de_cor_avisa_e_aguenta_erros():
    bandeja, icone = _bandeja()
    bandeja.estado("pronto", 'Jarvis: diga "Jarvis" para chamar')
    assert icone.icon == ("imagem", ESTADOS["pronto"][0], False) and icone.title == 'Jarvis: diga "Jarvis" para chamar'
    bandeja.estado("surdo")
    assert icone.icon == ("imagem", ESTADOS["surdo"][0], True) and icone.title == ESTADOS["surdo"][1]  # riscado
    bandeja.estado("inexistente")
    assert icone.title == ESTADOS["surdo"][1]
    bandeja.estado("ocupado", "x" * 300)
    assert len(icone.title) <= 120  # o Windows corta o texto do ícone
    bandeja.avisar("Terminei o trabalho no projeto Royale Vision.")
    assert icone.avisos == [("Jarvis", "Terminei o trabalho no projeto Royale Vision.")]

    def explode():
        raise RuntimeError("ação com defeito")

    bandeja.ligar({"painel": explode}, {"microfone": explode})
    icone.menu[1].acao()  # não levanta
    assert icone.menu[2].marcado is False  # leitura com erro conta como não
    icone.notify = icone.update_menu = lambda *_: 1 / 0
    bandeja.avisar("x")
    bandeja.atualizar()


def test_antes_de_iniciar_e_sem_icone_nada_quebra():
    bandeja = Bandeja(_pystray_falso())
    bandeja.estado("pronto")
    bandeja.avisar("x")
    bandeja.atualizar()
    bandeja.parar()
    muda = BandejaMuda()
    muda.ligar({}, {})
    muda.estado("pronto", "dica")
    muda.avisar("x")
    muda.atualizar()
    muda.parar()


def test_o_desenho_do_icone():
    pytest.importorskip("PIL")
    normal, riscado = desenhar_icone((0, 140, 255)), desenhar_icone((125, 130, 140), riscado=True)
    assert normal.size == riscado.size == (64, 64) and normal.mode == "RGBA"
    assert normal.getpixel((0, 0))[3] == 0  # canto transparente: o ícone é um disco
    assert normal.getpixel((32, 6))[:3] == (0, 140, 255)  # a cor do estado
    pontos = normal.load()
    brancos = sum(1 for x in range(64) for y in range(64) if pontos[x, y][:3] == (255, 255, 255))
    assert 200 < brancos < 1500  # tem um J, e ele não toma o disco todo
    assert normal.tobytes() != riscado.tobytes()
