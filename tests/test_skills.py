"""Skills de fora: endereço aceito, o que é mostrado ao usuário, instalar e remover. O download é de mentira."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from jarvis import skills as modulo
from jarvis.acoes import ErroAcao
from jarvis.skills import ErroSkill, Origem, Skills, ler_cabecalho, ler_origem, limpo, procurar, skills_do_projeto


def escrever(pasta: Path, nome: str | None, descricao: str = "Faz relatórios.", extras: dict[str, str] | None = None) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    cabecalho = f"---\n{f'name: {nome}' if nome else ''}\ndescription: {descricao}\n---\n" if nome or descricao else ""
    (pasta / "SKILL.md").write_text(f"{cabecalho}# Instruções\n", encoding="utf-8")
    for caminho, texto in (extras or {}).items():
        (pasta / caminho).parent.mkdir(parents=True, exist_ok=True)
        (pasta / caminho).write_text(texto, encoding="utf-8")


def baixador(montar):
    """No lugar do git clone: monta o 'repositório' no destino e devolve um commit."""
    pedidos: list[Origem] = []

    def clonar(origem: Origem, destino: Path) -> str:
        pedidos.append(origem)
        destino.mkdir(parents=True)
        (destino / ".git").mkdir()
        (destino / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        montar(destino)
        return "abc123def456"

    clonar.pedidos = pedidos
    return clonar


# --- endereço ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bruto, esperado",
    [
        ("https://github.com/anthropics/skills", Origem("https://github.com/anthropics/skills.git", None, "")),
        ("https://github.com/anthropics/skills/", Origem("https://github.com/anthropics/skills.git", None, "")),
        ("https://github.com/anthropics/skills.git", Origem("https://github.com/anthropics/skills.git", None, "")),
        ("  anthropics/skills ", Origem("https://github.com/anthropics/skills.git", None, "")),
        ("https://www.github.com/a/b", Origem("https://github.com/a/b.git", None, "")),
        ("https://github.com/a/b/tree/main/skills/pdf", Origem("https://github.com/a/b.git", "main", "skills/pdf")),
        ("https://github.com/a/b/blob/v1.2/skills/pdf/SKILL.md", Origem("https://github.com/a/b.git", "v1.2", "skills/pdf")),
        ("https://github.com/a/b/tree/main", Origem("https://github.com/a/b.git", "main", "")),
        ("https://gitlab.com/a/b/-/tree/dev/x", Origem("https://gitlab.com/a/b.git", "dev", "x")),
        ("https://bitbucket.org/a/b/src/main/x/y", Origem("https://bitbucket.org/a/b.git", "main", "x/y")),
    ],
)
def test_enderecos_aceitos(bruto, esperado):
    assert ler_origem(bruto) == esperado


@pytest.mark.parametrize(
    "bruto",
    [
        "", "   ", None, 7,
        "http://github.com/a/b",  # sem https
        "git@github.com:a/b.git", "ssh://git@github.com/a/b", "file:///etc", "/etc/passwd", "C:\\skills\\x",
        "https://exemplo.com/a/b", "https://github.com.evil.com/a/b", "https://evil.com/github.com/a/b",
        "https://usuario:senha@github.com/a/b", "https://github.com:8443/a/b",
        "https://github.com/a", "https://github.com/", "https://github.com/a/b/issues/3",
        "https://github.com/a/b/tree/main/../../x", "https://github.com/a/b/tree/main/./x",
        "https://github.com/a/--upload-pack=x", "https://github.com/-a/b", "https://github.com/a/b/tree/-x",
        "https://github.com/a/b c", "https://github.com/a/b?x=1#y/../..", "a/b/c", "a/..",
    ],
)  # fmt: skip
def test_enderecos_recusados(bruto):
    with pytest.raises(ErroSkill):
        ler_origem(bruto)


def test_o_clone_nao_pede_senha_nem_segue_atalhos(monkeypatch, tmp_path):
    chamadas = []

    class Feito:
        returncode, stdout, stderr = 0, "0123456789abcdef0123\n", ""

    def rodar(comando, **opcoes):
        chamadas.append((comando, opcoes))
        return Feito()

    monkeypatch.setattr(modulo.subprocess, "run", rodar)
    commit = modulo.clonar(Origem("https://github.com/a/b.git", "main", "x"), tmp_path / "r")
    (comando, opcoes), _ = chamadas
    assert commit == "0123456789ab"
    assert comando[0] == "git" and "clone" in comando and comando[-3:] == ["--", "https://github.com/a/b.git", str(tmp_path / "r")]
    assert comando[comando.index("--branch") + 1] == "main" and "--depth" in comando
    for ajuste in ("credential.helper=", "core.symlinks=false", "core.hooksPath=", "protocol.file.allow=never"):
        assert ajuste in comando
    assert opcoes["env"]["GIT_TERMINAL_PROMPT"] == "0" and opcoes["env"]["GCM_INTERACTIVE"] == "never"
    assert "shell" not in opcoes and opcoes["timeout"] == modulo.TEMPO_DO_CLONE


def test_falha_do_clone_vira_mensagem(monkeypatch, tmp_path):
    class Falhou:
        returncode, stdout, stderr = 128, "", "Cloning...\nfatal: repository 'https://github.com/a/b.git/' not found\x1b[0m\n"

    monkeypatch.setattr(modulo.subprocess, "run", lambda *a, **k: Falhou())
    with pytest.raises(ErroSkill, match="Não consegui baixar https://github.com/a/b: o repositório não existe ou é privado"):
        modulo.clonar(Origem("https://github.com/a/b.git", None, ""), tmp_path / "r")
    Falhou.stderr = "fatal: unable to access 'https://github.com/a/b.git/': Could not resolve host\x1b[0m: github.com\n"
    with pytest.raises(ErroSkill, match="Could not resolve host") as erro:
        modulo.clonar(Origem("https://github.com/a/b.git", None, ""), tmp_path / "r")
    assert "\x1b" not in str(erro.value)

    def sem_git(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(modulo.subprocess, "run", sem_git)
    with pytest.raises(ErroSkill, match="git não está instalado"):
        modulo.clonar(Origem("https://github.com/a/b.git", None, ""), tmp_path / "r")


# --- leitura do que veio -----------------------------------------------------------------------


def test_cabecalho(tmp_path):
    arquivo = tmp_path / "SKILL.md"
    arquivo.write_text('---\nname: "pdf-tools"\ndescription: >\n  Lê e preenche\n  arquivos PDF.\nlicense: MIT\n---\n# x\nname: outro\n', encoding="utf-8")
    assert ler_cabecalho(arquivo) == {"name": "pdf-tools", "description": "Lê e preenche arquivos PDF.", "license": "MIT"}
    arquivo.write_text("# sem cabeçalho\nname: x\n", encoding="utf-8")
    assert ler_cabecalho(arquivo) == {} and ler_cabecalho(tmp_path / "nao-existe.md") == {}


def test_texto_de_terceiros_vai_limpo_para_o_terminal():
    assert limpo("ok\x1b[2J\x07 apaga\r\n  a tela\u202e", 80) == "ok [2J apaga a tela"
    assert limpo("a" * 500, 20) == "a" * 19 + "…" and limpo(None) == "None"


def test_procurar_skills_no_repositorio(tmp_path):
    escrever(tmp_path / "skills" / "pdf", "pdf")
    escrever(tmp_path / "skills" / "pdf" / "aninhada", "aninhada")  # o que está dentro de uma skill é dela
    escrever(tmp_path / "skills" / "docx", "docx")
    escrever(tmp_path / ".git" / "x", "do-git")
    escrever(tmp_path / "node_modules" / "y", "dependencia")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    assert [p.name for p in procurar(tmp_path)] == ["docx", "pdf"]
    assert [p.name for p in procurar(tmp_path, "skills/pdf")] == ["pdf"]
    for ruim in ("nao-existe", "..", "skills/../.."):
        with pytest.raises(ErroSkill, match="não tem a pasta"):
            procurar(tmp_path, ruim)


# --- preparar, instalar, remover -----------------------------------------------------------------


def test_instalar_uma_skill_do_comeco_ao_fim(tmp_path):
    def montar(destino):
        escrever(destino / "skills" / "relatorio", "Relatório Semanal", "Monta o relatório\x1b[31m da semana.", {"scripts/gerar.py": "print(1)\n", "modelo.md": "# m\n"})

    clonar = baixador(montar)
    skills = Skills(tmp_path / "skills", clonar=clonar)
    assert skills.instaladas() == [] and skills.nomes() == []

    proposta = skills.preparar("https://github.com/dona/caixa/tree/main/skills/relatorio")
    assert clonar.pedidos == [Origem("https://github.com/dona/caixa.git", "main", "skills/relatorio")]
    assert proposta.skill.nome == "relatorio-semanal" and proposta.commit == "abc123def456" and not proposta.substitui
    assert proposta.skill.arquivos == ["SKILL.md", "modelo.md", "scripts/gerar.py"] and proposta.skill.scripts == ["scripts/gerar.py"]
    linhas = "\n".join(proposta.linhas())
    for trecho in ("Skill: relatorio-semanal", "Monta o relatório [31m da semana.", "https://github.com/dona/caixa (pasta skills/relatorio)",
                   "commit abc123def456", "Arquivos: 3", "scripts/gerar.py", "terceiros", str(proposta.skill.pasta / "SKILL.md")):  # fmt: skip
        assert trecho in linhas, trecho
    assert "\x1b" not in linhas
    assert skills.instaladas() == []  # preparar não instala

    assert "instalada" in skills.instalar(proposta)
    skills.descartar(proposta)
    assert not proposta.temporaria.exists()
    raiz = tmp_path / "skills"
    assert json.loads((raiz / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"] == "jarvis"
    instalada = raiz / "skills" / "relatorio-semanal"
    assert (instalada / "SKILL.md").is_file() and (instalada / "scripts" / "gerar.py").is_file() and not (instalada / ".git").exists()
    (unica,) = skills.instaladas()
    assert unica.nome == "relatorio-semanal" and unica.origem.startswith("https://github.com/dona/caixa") and unica.commit == "abc123def456"
    assert unica.scripts == 1 and "relatório" in unica.descricao and skills.nomes() == ["relatorio-semanal"]

    # instalar de novo substitui, e isso é dito ao usuário
    de_novo = skills.preparar("dona/caixa")
    assert de_novo.substitui and "substitui" in de_novo.linhas()[0]
    skills.descartar(de_novo)

    assert skills.conferir(" Relatório  Semanal ") == "relatorio-semanal"
    assert "removida" in skills.remover("relatorio-semanal") and skills.instaladas() == []
    for ruim in ("relatorio-semanal", "", None, "../skills"):
        with pytest.raises(ErroSkill):
            skills.remover(ruim)


def test_repositorio_com_varias_skills_pede_o_nome(tmp_path):
    def montar(destino):
        escrever(destino / "skills" / "pdf", "pdf")
        escrever(destino / "skills" / "docx", None, "Sem nome no cabeçalho.")
        escrever(destino / "plugins" / "x" / "skills" / "planilha", "planilha")

    skills = Skills(tmp_path / "skills", clonar=baixador(montar))
    with pytest.raises(ErroSkill, match=r"tem 3 skills.*Nomes: docx, pdf, planilha") as erro:
        skills.preparar("a/b")
    assert "Pergunte ao usuário" in str(erro.value)
    with pytest.raises(ErroSkill, match="não tem a skill 'xlsx'. Tem: docx, pdf, planilha"):
        skills.preparar("a/b", "xlsx")
    proposta = skills.preparar("a/b", " Planilha ")
    assert proposta.skill.nome == "planilha"
    skills.descartar(proposta)
    sem_nome = skills.preparar("a/b", "docx")
    assert sem_nome.skill.nome == "docx" and sem_nome.skill.descricao == "Sem nome no cabeçalho."
    skills.descartar(sem_nome)


def test_skill_na_raiz_do_repositorio_leva_o_nome_dele(tmp_path):
    skills = Skills(tmp_path / "skills", clonar=baixador(lambda destino: escrever(destino, None, "Na raiz.")))
    proposta = skills.preparar("https://github.com/dona/minha-skill")
    assert proposta.skill.nome == "minha-skill" and ".git/config" not in proposta.skill.arquivos
    skills.instalar(proposta)
    skills.descartar(proposta)
    assert skills.nomes() == ["minha-skill"] and not (tmp_path / "skills" / "skills" / "minha-skill" / ".git").exists()


def test_recusas_ao_preparar_limpam_o_temporario(tmp_path, monkeypatch):
    criadas = []
    original = modulo.tempfile.mkdtemp

    def mkdtemp(**opcoes):
        criadas.append(Path(original(**opcoes)))
        return str(criadas[-1])

    monkeypatch.setattr(modulo.tempfile, "mkdtemp", mkdtemp)

    def falha(_origem, _destino):
        raise ErroSkill("Não consegui baixar")

    casos = [
        (baixador(lambda destino: (destino / "README.md").write_text("x", encoding="utf-8")), "Não achei nenhuma skill"),
        (falha, "Não consegui baixar"),
        (baixador(lambda destino: escrever(destino / "s", "grande", extras={f"a{i}.txt": "x" for i in range(modulo.MAX_ARQUIVOS + 1)})), "grande demais"),
        (baixador(lambda destino: escrever(destino / "s", "pesada", extras={"dados.bin": "x" * (modulo.MAX_BYTES + 1)})), "grande demais"),
    ]
    for clonar, trecho in casos:
        with pytest.raises(ErroSkill, match=trecho):
            Skills(tmp_path / "skills", clonar=clonar).preparar("a/b")
    assert len(criadas) == len(casos) and not any(pasta.exists() for pasta in criadas)
    assert not (tmp_path / "skills").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="criar atalho simbólico no Windows pede privilégio")
def test_skill_com_atalho_simbolico_e_recusada(tmp_path):
    def montar(destino):
        escrever(destino / "s", "esperta")
        os.symlink("/etc/passwd", destino / "s" / "dados.txt")

    with pytest.raises(ErroSkill, match="atalho simbólico"):
        Skills(tmp_path / "skills", clonar=baixador(montar)).preparar("a/b")


def test_erro_de_skill_e_erro_de_acao():
    assert issubclass(ErroSkill, ErroAcao)


def test_skills_que_o_projeto_traz(tmp_path):
    assert skills_do_projeto(tmp_path) == []
    base = tmp_path / ".claude" / "skills"
    escrever(base / "revisao", "revisao")
    escrever(base / "Deploy_2", "deploy")
    escrever(base / "com espaço", "x")  # nome que não passa na regra de permissão do SDK
    escrever(base / "com(parenteses)", "x")
    (base / "sem-skill").mkdir()
    (base / "solto.md").write_text("x", encoding="utf-8")
    assert skills_do_projeto(tmp_path) == ["Deploy_2", "revisao"]


def test_pasta_instalada_mexida_a_mao_nao_quebra_a_listagem(tmp_path):
    skills = Skills(tmp_path / "skills", clonar=baixador(lambda destino: escrever(destino, "boa")))
    proposta = skills.preparar("a/b")
    skills.instalar(proposta)
    skills.descartar(proposta)
    base = tmp_path / "skills" / "skills"
    (base / "boa" / ".origem.json").write_text("{quebrado", encoding="utf-8")
    escrever(base / "nome ruim", "x")
    (base / "vazia").mkdir()
    shutil.copy(base / "boa" / "SKILL.md", base / "solto.md")
    (unica,) = skills.instaladas()
    assert unica.nome == "boa" and unica.origem == "desconhecida"
