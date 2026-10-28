"""Registro das conversas em SQLite: cada turno, o que foi pedido, o que rodou e quanto custou."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS turnos (
    id          INTEGER PRIMARY KEY,
    sessao      TEXT,
    inicio      TEXT NOT NULL,          -- data e hora local, ISO 8601
    duracao_ms  INTEGER NOT NULL,
    modo        TEXT NOT NULL,          -- 'texto' ou 'voz'
    pergunta    TEXT NOT NULL,
    resposta    TEXT NOT NULL,
    custo_usd   REAL,                   -- custo só deste turno (estimativa do SDK)
    erro        TEXT
);
CREATE TABLE IF NOT EXISTS chamadas (
    id          INTEGER PRIMARY KEY,
    turno_id    INTEGER NOT NULL REFERENCES turnos(id) ON DELETE CASCADE,
    ferramenta  TEXT NOT NULL,
    entrada     TEXT NOT NULL,          -- JSON
    erro        TEXT                    -- preenchido se falhou ou foi recusada
);
CREATE INDEX IF NOT EXISTS chamadas_por_turno ON chamadas(turno_id);
"""


@dataclass
class Chamada:
    ferramenta: str
    entrada: dict[str, Any]
    erro: str | None = None


@dataclass
class Turno:
    pergunta: str
    modo: str = "texto"
    inicio: datetime = field(default_factory=datetime.now)
    resposta: str = ""
    chamadas: list[Chamada] = field(default_factory=list)
    sessao: str | None = None
    duracao_ms: int = 0
    custo_usd: float | None = None  # custo deste turno
    custo_acumulado: float | None = None  # custo da sessão até aqui (não vai para o banco)
    erro: str | None = None
    interrompido: bool = False  # largado no meio a pedido do usuário (não vai para o banco; o motivo fica em `erro`)


def _escapar(termo: str) -> str:
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Registro:
    def __init__(self, caminho: Path | str) -> None:
        if isinstance(caminho, Path):
            caminho.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(caminho))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_ESQUEMA)
        self._db.commit()

    def gravar(self, turno: Turno) -> int:
        with self._db:  # uma transação: ou grava o turno inteiro, ou nada
            cursor = self._db.execute(
                "INSERT INTO turnos (sessao, inicio, duracao_ms, modo, pergunta, resposta, custo_usd, erro) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turno.sessao,
                    turno.inicio.isoformat(timespec="seconds"),
                    turno.duracao_ms,
                    turno.modo,
                    turno.pergunta,
                    turno.resposta,
                    turno.custo_usd,
                    turno.erro,
                ),
            )
            self._db.executemany(
                "INSERT INTO chamadas (turno_id, ferramenta, entrada, erro) VALUES (?, ?, ?, ?)",
                [
                    (cursor.lastrowid, c.ferramenta, json.dumps(c.entrada, ensure_ascii=False, default=str), c.erro)
                    for c in turno.chamadas
                ],
            )
        return int(cursor.lastrowid or 0)

    def ultimos(self, limite: int = 10) -> list[sqlite3.Row]:
        """Os turnos mais recentes, do mais novo para o mais antigo."""
        return self._db.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM chamadas c WHERE c.turno_id = t.id) AS n_chamadas "
            "FROM turnos t ORDER BY t.id DESC LIMIT ?",
            (max(1, min(limite, 100)),),
        ).fetchall()

    def buscar(self, termo: str, limite: int = 5) -> list[sqlite3.Row]:
        """Turnos em que a pergunta ou a resposta contém o termo, do mais novo para o mais antigo."""
        padrao = f"%{_escapar(termo.strip())}%"
        return self._db.execute(
            "SELECT * FROM turnos WHERE pergunta LIKE ? ESCAPE '\\' OR resposta LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT ?",
            (padrao, padrao, max(1, min(limite, 10))),
        ).fetchall()

    def total(self) -> int:
        """Quantas conversas (turnos) já foram registradas."""
        return int(self._db.execute("SELECT COUNT(*) FROM turnos").fetchone()[0])

    def chamadas_do_turno(self, turno_id: int) -> list[sqlite3.Row]:
        return self._db.execute("SELECT * FROM chamadas WHERE turno_id = ? ORDER BY id", (turno_id,)).fetchall()

    def fechar(self) -> None:
        self._db.close()
