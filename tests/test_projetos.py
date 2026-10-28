"""Cadastro de projetos, situação pelo git, cópia de trabalho e diário. Usa repositórios git de verdade."""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from jarvis import projetos
from jarvis.projetos import Diario, ErroProjeto, Projeto, achar, fatia, ler_projetos

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="precisa do git instalado")


def rodar(pasta, *argumentos):
    ambiente = {**os.environ, "GIT_AUTHOR_NAME": "Dono", "GIT_AUTHOR_EMAIL": "dono@x", "GIT_COMMITTER_NAME": "Dono", "GIT_COMMITTER_EMAIL": "dono@x"}
    return subprocess.run(["git", *argumentos], cwd=pasta, check=True, capture_output=True, text=True, env=ambiente).stdout.strip()


@pytest.fixture
def repositorio(tmp_path):
    pasta = Path(os.path.realpath(tmp_path / "royale-vision"))
    pasta.mkdir()
    rodar(pasta, "init", "-q", "-b", "main")
    (pasta / "README.md").write_text("# Royale Vision\n", encoding="utf-8")
    (pasta / "app.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    rodar(pasta, "add", "-A")
    rodar(pasta, "commit", "-q", "-m", "primeiro commit")
    return Projeto("Royale Vision", pasta)


# --- cadastro ------------------------------------------------------------------------------


def test_ler_projetos(tmp_path):
    a, b = tmp_path / "rv", tmp_path / "pasta com espaço"
    a.mkdir(), b.mkdir()
    lidos = ler_projetos(f' Royale  Vision = {a} ; dash="{b}" ;; ')
    assert list(lidos) == ["royale vision", "dash"]
    assert lidos["royale vision"] == Projeto("Royale Vision", Path(os.path.realpath(a)))
    assert lidos["dash"].pasta == Path(os.path.realpath(b)) and lidos["dash"].slug == "dash"
    assert ler_projetos("") == {} and ler_projetos(" ; ") == {}


@pytest.mark.parametrize("bruto, trecho", [("dash", "não entendi"), ("=C:/x", "não entendi"), ("dash=", "não entendi"), ("dash=/nao/existe/mesmo", "não existe")])
def test_cadastro_invalido(bruto, trecho):
    with pytest.raises(ErroProjeto, match=trecho):
        ler_projetos(bruto)


def test_projeto_repetido(tmp_path):
    with pytest.raises(ErroProjeto, match="duas vezes"):
        ler_projetos(f"Dash={tmp_path};dash={tmp_path}")


def test_achar_pelo_nome_sem_acento_ou_por_um_pedaco(tmp_path):
    cadastro = ler_projetos(f"Royale Vision={tmp_path};Fluxo Royale={tmp_path};Dash={tmp_path}")
    assert achar(cadastro, "royale vision").nome == "Royale Vision"
    assert achar(cadastro, "  ROYALE   VISION ").nome == "Royale Vision"
    assert achar(cadastro, "vision").nome == "Royale Vision"
    assert achar(cadastro, "o projeto dash").nome == "Dash"
    with pytest.raises(ErroProjeto, match="Cadastrados: Royale Vision, Fluxo Royale, Dash"):
        achar(cadastro, "royale")  # dois combinam: não adivinha
    with pytest.raises(ErroProjeto, match="Não há projeto"):
        achar(cadastro, "netflix")
    for ruim in ("", None, 7):
        with pytest.raises(ErroProjeto):
            achar(cadastro, ruim)
    with pytest.raises(ErroProjeto, match="JARVIS_PROJETOS"):
        achar({}, "dash")


def test_fatia_serve_de_nome_de_ramo():
    assert fatia("Corrigir o login (v2)!") == "corrigir-o-login-v2"
    assert fatia("Ação: migrar para açúcar") == "acao-migrar-para-acucar"
    assert fatia("x" * 100) == "x" * 40 and fatia("!!!") == "trabalho"
    assert not fatia("a" * 39 + " b").endswith("-")


# --- git --------------------------------------------------------------------------------------


def test_situacao_do_projeto(repositorio):
    estado = projetos.situacao(repositorio)
    assert estado["ramo"] == "main" and estado["pendentes"] == 0 and estado["ramos_do_jarvis"] == []
    assert len(estado["commits"]) == 1 and "primeiro commit" in estado["commits"][0]
    (repositorio.pasta / "novo.txt").write_text("x", encoding="utf-8")
    (repositorio.pasta / "app.py").write_text("mudou\n", encoding="utf-8")
    rodar(repositorio.pasta, "branch", "jarvis/1-teste")
    estado = projetos.situacao(repositorio)
    assert estado["pendentes"] == 2 and estado["ramos_do_jarvis"] == ["jarvis/1-teste"]


def test_pasta_que_nao_e_repositorio(tmp_path):
    pasta = Path(os.path.realpath(tmp_path / "solta"))
    pasta.mkdir()
    with pytest.raises(ErroProjeto, match="não é um repositório git"):
        projetos.conferir_repositorio(Projeto("Solta", pasta))


def test_subpasta_de_repositorio_e_repositorio_vazio(repositorio, tmp_path):
    sub = repositorio.pasta / "src"
    sub.mkdir()
    with pytest.raises(ErroProjeto, match="subpasta do repositório"):
        projetos.conferir_repositorio(Projeto("Sub", sub))
    vazio = Path(os.path.realpath(tmp_path / "vazio"))
    vazio.mkdir()
    rodar(vazio, "init", "-q")
    with pytest.raises(ErroProjeto, match="nenhum commit"):
        projetos.conferir_repositorio(Projeto("Vazio", vazio))


def test_copia_de_trabalho_em_ramo_novo_nao_mexe_na_pasta_do_usuario(repositorio, tmp_path):
    (repositorio.pasta / "rascunho.txt").write_text("trabalho do usuário, sem commit", encoding="utf-8")
    destino = tmp_path / "trabalhos" / "royale-vision-1"
    base = projetos.criar_copia(repositorio, destino, "jarvis/1-somar")
    assert base == rodar(repositorio.pasta, "rev-parse", "HEAD")
    assert (destino / "app.py").is_file() and not (destino / "rascunho.txt").exists()  # só o que está no git
    assert rodar(destino, "branch", "--show-current") == "jarvis/1-somar"
    assert rodar(repositorio.pasta, "branch", "--show-current") == "main"  # a pasta do usuário continua onde estava

    # o trabalhador edita e deixa parte sem commit
    (destino / "app.py").write_text("def soma(a, b):\n    return a + b\n\n\ndef dobro(a):\n    return a * 2\n", encoding="utf-8")
    (destino / "teste.py").write_text("from app import dobro\nassert dobro(2) == 4\n", encoding="utf-8")
    assert projetos.fechar_copia(destino, "jarvis: somar") is True
    assert projetos.fechar_copia(destino, "jarvis: somar") is False  # nada mais a guardar
    assert rodar(destino, "log", "-1", "--pretty=%an <%ae> %s") == "Jarvis <jarvis@localhost> jarvis: somar"

    resumo = projetos.resumo_do_ramo(destino, base)
    assert len(resumo["commits"]) == 1 and "jarvis: somar" in resumo["commits"][0]
    assert any("app.py" in a for a in resumo["arquivos"]) and "2 files changed" in resumo["total"]

    assert projetos.remover_copia(repositorio, destino) is True and not destino.exists()
    # a cópia saiu do disco; o ramo ficou, com o commit, para o usuário revisar
    assert rodar(repositorio.pasta, "log", "-1", "--pretty=%s", "jarvis/1-somar") == "jarvis: somar"
    assert (repositorio.pasta / "rascunho.txt").read_text(encoding="utf-8") == "trabalho do usuário, sem commit"
    assert "dobro" not in (repositorio.pasta / "app.py").read_text(encoding="utf-8")
    assert projetos.situacao(repositorio)["ramos_do_jarvis"] == ["jarvis/1-somar"]


def test_ramo_que_ja_existe_e_erro_claro(repositorio, tmp_path):
    projetos.criar_copia(repositorio, tmp_path / "t" / "a", "jarvis/1-x")
    with pytest.raises(ErroProjeto, match="already exists|já existe"):
        projetos.criar_copia(repositorio, tmp_path / "t" / "b", "jarvis/1-x")


def test_remover_copia_que_ja_sumiu(repositorio, tmp_path):
    destino = tmp_path / "t" / "a"
    projetos.criar_copia(repositorio, destino, "jarvis/1-x")
    shutil.rmtree(destino)
    assert projetos.remover_copia(repositorio, destino) is True
    assert "t/a" not in rodar(repositorio.pasta, "worktree", "list").replace("\\", "/")


def test_git_ausente_vira_mensagem(repositorio, monkeypatch):
    def sem_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(projetos.subprocess, "run", sem_git)
    with pytest.raises(ErroProjeto, match="git não está instalado"):
        projetos.situacao(repositorio)


def test_nenhum_comando_git_daqui_publica_nada():
    fonte = Path(projetos.__file__).read_text(encoding="utf-8")
    for proibido in ('"push"', '"pull"', '"fetch"', '"clone"', '"remote"'):
        assert proibido not in fonte, proibido
    assert "shell=True" not in fonte


# --- diário --------------------------------------------------------------------------------------


def test_diario_guarda_e_devolve_o_mais_recente(tmp_path):
    diario, projeto = Diario(tmp_path / "projetos"), Projeto("Royale Vision", tmp_path)
    assert diario.recente(projeto) == ""
    diario.anotar(projeto, "trabalho 1: somar", "Feito: função dobro.\nPendente: testes.", datetime(2026, 10, 7, 15, 30))
    diario.anotar(projeto, "trabalho 2: testes", "Feito: testes do dobro.", datetime(2026, 10, 8, 9, 0))
    texto = diario.recente(projeto)
    assert texto.startswith("## 07/10/2026 15:30 · trabalho 1: somar") and "## 08/10/2026 09:00 · trabalho 2: testes" in texto
    assert diario.arquivo(projeto).name == "royale-vision.md"
    for n in range(40):
        diario.anotar(projeto, f"trabalho {n + 3}", "x" * 200, datetime(2026, 10, 9, 10, n))
    curto = diario.recente(projeto, limite=1000)
    assert len(curto) <= 1000 and curto.startswith("## 09/10/2026") and "trabalho 42" in curto and "trabalho 1:" not in curto


# --- continuar de um ramo, ramo vazio e commit de fechamento sem dependências -------------------


def test_continuar_de_um_ramo_do_jarvis(repositorio, tmp_path):
    primeira = tmp_path / "copias" / "a"
    projetos.criar_copia(repositorio, primeira, "jarvis/aaa-primeiro")
    (primeira / "novo.py").write_text("x = 1\n", encoding="utf-8")
    assert projetos.fechar_copia(primeira, "primeiro trabalho")
    ponta = rodar(primeira, "rev-parse", "HEAD")
    projetos.remover_copia(repositorio, primeira)

    segunda = tmp_path / "copias" / "b"
    base = projetos.criar_copia(repositorio, segunda, "jarvis/bbb-segundo", partida="jarvis/aaa-primeiro")
    assert base == ponta and (segunda / "novo.py").exists()
    assert not (repositorio.pasta / "novo.py").exists()  # a pasta do usuário continua como estava

    for ruim in ("main", "jarvis/nao-existe", "--upload-pack=x", "HEAD"):
        with pytest.raises(ErroProjeto, match="Não há ramo do Jarvis"):
            projetos.criar_copia(repositorio, tmp_path / "copias" / "c", "jarvis/ccc", partida=ruim)


def test_apagar_so_ramo_do_jarvis(repositorio, tmp_path):
    copia = tmp_path / "copias" / "a"
    projetos.criar_copia(repositorio, copia, "jarvis/aaa-vazio")
    projetos.remover_copia(repositorio, copia)
    assert projetos.ramos_do_jarvis(repositorio) == ["jarvis/aaa-vazio"]
    for ruim in ("main", "jarvis/outro", "-D"):
        with pytest.raises(ErroProjeto, match="não é um ramo do Jarvis"):
            projetos.apagar_ramo(repositorio, ruim)
    projetos.apagar_ramo(repositorio, "jarvis/aaa-vazio")
    assert projetos.ramos_do_jarvis(repositorio) == []
    assert rodar(repositorio.pasta, "branch", "--show-current") == "main"


def test_commit_de_fechamento_deixa_dependencias_de_fora(repositorio, tmp_path):
    copia = tmp_path / "copias" / "a"
    base = projetos.criar_copia(repositorio, copia, "jarvis/aaa-deps")
    for pasta in ("node_modules/pacote", "frontend/node_modules/x", ".venv/lib", "src/__pycache__"):
        (copia / pasta).mkdir(parents=True)
        (copia / pasta / "arquivo.js").write_text("x", encoding="utf-8")
    # só dependências: não há o que guardar
    assert projetos.fechar_copia(copia, "nada") is False
    (copia / "src" / "novo.py").write_text("x = 1\n", encoding="utf-8")
    assert projetos.fechar_copia(copia, "com código") is True
    assert rodar(copia, "diff", "--name-only", f"{base}..HEAD") == "src/novo.py"


def test_erro_de_projeto_e_erro_de_acao():
    from jarvis.acoes import ErroAcao

    assert issubclass(ErroProjeto, ErroAcao)  # as ferramentas devolvem a mensagem ao modelo, sem derrubar o turno
