"""Quando foi o último backup — Etapa 5, Bloco 18c.

Backup que falha em silêncio é pior que nenhum: dá falsa segurança. O
Painel do administrador mostra quando foi a última cópia e fica em alerta
se ela tiver mais de `LIMITE_DE_HORAS`.

A data vem do **nome** do arquivo (`villaju_AAAA-MM-DD_HHMM.sql`), e não da
data de modificação, que muda ao copiar a pasta para um pen drive. Só
arquivos com esse nome contam: o `.parcial` de um backup interrompido não
conta como cópia.

Este módulo é também a fonte do nome e da pasta para `scripts/backup.py`:
os dois nunca divergem.
"""

import datetime as dt
import os
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PASTA_PADRAO = RAIZ / "instance" / "backups" / "automaticos"
PREFIXO = "villaju_"
PADRAO_DO_NOME = re.compile(r"^villaju_(\d{4}-\d{2}-\d{2}_\d{4})\.sql$")
LIMITE_DE_HORAS = 48


def pasta_dos_backups() -> pathlib.Path:
    return pathlib.Path(os.environ.get("VILLAJU_PASTA_BACKUP") or PASTA_PADRAO)


def nome_do_arquivo(agora: dt.datetime) -> str:
    return f"{PREFIXO}{agora:%Y-%m-%d_%H%M}.sql"


def data_da_copia(nome: str):
    achado = PADRAO_DO_NOME.match(nome)
    if not achado:
        return None
    try:
        return dt.datetime.strptime(achado.group(1), "%Y-%m-%d_%H%M")
    except ValueError:
        return None


def copias(pasta: pathlib.Path) -> list:
    """As cópias, da mais nova para a mais velha. Nada mais da pasta conta."""
    if not pasta.is_dir():
        return []
    achadas = [a for a in pasta.iterdir() if a.is_file() and data_da_copia(a.name)]
    return sorted(achadas, key=lambda a: data_da_copia(a.name), reverse=True)


def situacao(pasta: pathlib.Path = None, agora: dt.datetime = None) -> dict:
    pasta = pasta or pasta_dos_backups()
    agora = agora or dt.datetime.now()
    achadas = copias(pasta)
    if not achadas:
        return {"ultimo": None, "atrasado": True,
                "texto": "Nenhum backup encontrado. Confira o agendamento."}
    ultimo = data_da_copia(achadas[0].name)
    horas = (agora - ultimo).total_seconds() / 3600
    if horas < 1:
        quando = "há menos de uma hora"
    elif horas < 48:
        quando = f"há {int(horas)} hora{'s' if int(horas) != 1 else ''}"
    else:
        quando = f"há {int(horas // 24)} dias"
    atrasado = horas > LIMITE_DE_HORAS
    texto = f"Último backup: {ultimo:%d/%m/%Y às %Hh%M} ({quando})."
    if atrasado:
        texto += " Atrasado — confira o agendamento."
    return {"ultimo": ultimo, "atrasado": atrasado, "texto": texto}
