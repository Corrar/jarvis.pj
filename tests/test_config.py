import os
from pathlib import Path

import pytest

from jarvis import config
from jarvis.config import carregar, conferir_env

CHAVE = {"ANTHROPIC_API_KEY": "sk-teste"}


def test_sem_chave_explica_o_que_fazer(tmp_path):
    with pytest.raises(config.ErroConfig, match="ANTHROPIC_API_KEY"):
        config.carregar({}, home=tmp_path)
    with pytest.raises(config.ErroConfig):
        config.carregar({"ANTHROPIC_API_KEY": "   "}, home=tmp_path)


def test_pastas_padrao_usam_as_que_existem(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Downloads").mkdir()
    cfg = config.carregar(CHAVE, home=tmp_path)
    assert [p.name for p in cfg.pastas] == ["Documents", "Downloads"]


def test_sem_pastas_padrao_cai_na_pasta_do_usuario(tmp_path):
    cfg = config.carregar(CHAVE, home=tmp_path)
    assert cfg.pastas == (Path(os.path.realpath(tmp_path)),)


def test_jarvis_pastas_separadas_por_ponto_e_virgula(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b com espaço"
    a.mkdir()
    b.mkdir()
    cfg = config.carregar({**CHAVE, "JARVIS_PASTAS": f'{a}; "{b}" ;'}, home=tmp_path)
    assert cfg.pastas == (Path(os.path.realpath(a)), Path(os.path.realpath(b)))


def test_jarvis_pastas_inexistente_e_erro(tmp_path):
    with pytest.raises(config.ErroConfig, match="não existe"):
        config.carregar({**CHAVE, "JARVIS_PASTAS": str(tmp_path / "nada")}, home=tmp_path)


def test_valores_padrao(tmp_path):
    cfg = config.carregar(CHAVE, home=tmp_path)
    assert cfg.modelo == config.MODELO_PADRAO
    assert cfg.usuario is None
    assert cfg.orcamento_usd is None
    assert cfg.voz == "pt_BR-faber-medium"
    assert cfg.whisper == "small"


def test_voz_e_whisper_configuraveis(tmp_path):
    cfg = config.carregar({**CHAVE, "JARVIS_VOZ": "pt_BR-cadu-medium", "JARVIS_WHISPER": "base"}, home=tmp_path)
    assert (cfg.voz, cfg.whisper) == ("pt_BR-cadu-medium", "base")


def test_orcamento_aceita_virgula_decimal(tmp_path):
    cfg = config.carregar({**CHAVE, "JARVIS_ORCAMENTO_USD": "1,5"}, home=tmp_path)
    assert cfg.orcamento_usd == 1.5


@pytest.mark.parametrize("valor", ["abc", "0", "-3"])
def test_orcamento_invalido_e_erro(tmp_path, valor):
    with pytest.raises(config.ErroConfig, match="JARVIS_ORCAMENTO_USD"):
        config.carregar({**CHAVE, "JARVIS_ORCAMENTO_USD": valor}, home=tmp_path)


def test_env_do_windows_e_lido_sem_estragar_as_barras(tmp_path):
    """O python-dotenv não pode tratar \\U ou \\D como escape em valores sem aspas."""
    from dotenv import dotenv_values

    arquivo = tmp_path / ".env"
    arquivo.write_text("JARVIS_PASTAS=C:\\Users\\voce\\Documents;D:\\Projetos\n", encoding="utf-8")
    assert dotenv_values(arquivo)["JARVIS_PASTAS"] == "C:\\Users\\voce\\Documents;D:\\Projetos"


@pytest.mark.parametrize(
    "valor, esperado",
    [(None, False), ("", False), ("nunca", False), ("nao", False), ("sempre", True), ("SIM", True), (" sempre ", True)],
)
def test_confirmacao_das_acoes(tmp_path, valor, esperado):
    env = dict(CHAVE) if valor is None else {**CHAVE, "JARVIS_CONFIRMAR": valor}
    assert config.carregar(env, home=tmp_path).confirmar_acoes is esperado


@pytest.mark.parametrize("valor, esperado", [(None, True), ("", True), ("sim", True), ("nao", False), ("NÃO", False), ("0", False), (" desligado ", False)])
def test_ver_paginas_liga_e_desliga(tmp_path, valor, esperado):
    env = dict(CHAVE) if valor is None else {**CHAVE, "JARVIS_TELA": valor}
    assert config.carregar(env, home=tmp_path).ver_paginas is esperado


@pytest.mark.parametrize("valor, esperado", [(None, True), ("terminal", True), ("navegador", False), (" Navegador ", False)])
def test_para_onde_vai_o_foco_depois_de_mexer_na_pagina(tmp_path, valor, esperado):
    env = dict(CHAVE) if valor is None else {**CHAVE, "JARVIS_FOCO": valor}
    assert config.carregar(env, home=tmp_path).devolver_foco is esperado


def test_atalho_padrao_configurado_e_invalido(tmp_path):
    assert config.carregar(CHAVE, home=tmp_path).atalho == ""  # vazio = Ctrl+Alt+J
    assert config.carregar({**CHAVE, "JARVIS_ATALHO": " F9 "}, home=tmp_path).atalho == "F9"
    assert config.carregar({**CHAVE, "JARVIS_ATALHO": "nao"}, home=tmp_path).atalho == "nao"
    with pytest.raises(config.ErroConfig, match="JARVIS_ATALHO inválido"):
        config.carregar({**CHAVE, "JARVIS_ATALHO": "j"}, home=tmp_path)


def test_programas_a_mais_para_a_tela(tmp_path):
    assert config.carregar(CHAVE, home=tmp_path).programas_na_tela == ""
    assert config.carregar({**CHAVE, "JARVIS_PROGRAMAS_TELA": " spotify;vlc "}, home=tmp_path).programas_na_tela == "spotify;vlc"


@pytest.mark.parametrize("valor, esperado", [(None, True), ("", True), ("sim", True), ("nao", False), ("NÃO", False), ("0", False)])
def test_painel_liga_e_desliga(tmp_path, valor, esperado):
    env = dict(CHAVE) if valor is None else {**CHAVE, "JARVIS_PAINEL": valor}
    assert config.carregar(env, home=tmp_path).painel is esperado


def test_respostas_ao_nome_do_env():
    from jarvis.config import conferir_env, MAX_RESPOSTA, ErroConfig, ler_respostas

    assert ler_respostas("") == () and ler_respostas("  |  ") == ()
    assert ler_respostas("Sim, mestre?| À disposição,   senhor. |Sim, mestre?") == ("Sim, mestre?", "À disposição, senhor.")
    with pytest.raises(ErroConfig, match="JARVIS_RESPOSTAS"):
        ler_respostas("a" * (MAX_RESPOSTA + 1))


# --- conferir o .env ---------------------------------------------------------------------


def test_nome_trocado_no_env_vira_aviso_com_palpite(tmp_path):
    """O caso real: a chave da ElevenLabs com outro nome ficava sem efeito e ninguém avisava."""
    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "ANTHROPIC_API_KEY=x\nELEVENLABS_API_KEY=y\nJARVIS_ELEVEN_LABS_KEY=z\n"
        "JARVIS_ELEVENLABS_VOICE_ID=w\n# JARVIS_PASTAS=C:/x\nLIXO=1\n",
        encoding="utf-8",
    )
    avisos = " | ".join(conferir_env(arquivo))
    for errado, certo in (("ELEVENLABS_API_KEY", "JARVIS_ELEVENLABS_KEY"),
                          ("JARVIS_ELEVEN_LABS_KEY", "JARVIS_ELEVENLABS_KEY"),
                          ("JARVIS_ELEVENLABS_VOICE_ID", "JARVIS_ELEVENLABS_VOZ")):
        assert f"{errado} não é um ajuste" in avisos and f"Você quis dizer {certo}" in avisos
    assert "LIXO não é um ajuste" in avisos
    assert "ANTHROPIC_API_KEY" not in avisos and "JARVIS_PASTAS" not in avisos, "o que está certo ou comentado não vira aviso"


def test_o_aviso_nunca_mostra_o_valor(tmp_path):
    """O arquivo tem chaves: o aviso é lido em voz alta e vai parar em captura de tela."""
    arquivo = tmp_path / ".env"
    arquivo.write_text("ELEVENLABS_API_KEY=sk_minha_chave_secreta_123\n", encoding="utf-8")
    assert "sk_minha_chave_secreta_123" not in " ".join(conferir_env(arquivo))


def test_ajuste_repetido_avisa_qual_vale(tmp_path):
    arquivo = tmp_path / ".env"
    arquivo.write_text("JARVIS_VOZ=a\nJARVIS_VOZ=b\n", encoding="utf-8")
    assert "aparece duas vezes" in " ".join(conferir_env(arquivo))


def test_env_que_nao_existe_nao_atrapalha(tmp_path):
    assert conferir_env(tmp_path / "nao-existe") == []


def test_todo_ajuste_lido_do_ambiente_esta_na_lista_de_conhecidos():
    """Trava para o futuro: ajuste novo no config sem entrar na lista faria o conferidor mentir."""
    import re

    fonte = Path(config.__file__).read_text(encoding="utf-8")
    usados = set(re.findall(r'env\.get\("([A-Z_]+)"', fonte))
    assert usados - set(config.CONHECIDOS) == set(), "ajuste lido do .env que o /ajustes diria não existir"


def test_turno_limpo_pode_ser_solto_pelo_env():
    assert carregar({"ANTHROPIC_API_KEY": "x"}).turno_limpo is True, "em pé por padrão"
    assert carregar({"ANTHROPIC_API_KEY": "x", "JARVIS_TURNO_LIMPO": "nao"}).turno_limpo is False
