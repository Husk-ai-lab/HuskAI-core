from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.services import Services
from app.data.models.user import User
from app.endpoints.deps import SESSION_COOKIE
from app.gateways.sqlite.repositories import SessionRepo, UserRepo
from app.helpers.settings import Settings
from app.helpers.time import utcnow
from app.main import build_services, create_app
from test.fakes import FakeDecisionModel, FakeProvider

ORG = "org_test"
USER = "user_test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(HUSK_ENV="test", HUSK_DATA_DIR=tmp_path / "data", HUSK_PORT=8765,
                    HUSK_STUDIO_UI_URL="http://127.0.0.1:9", HUSK_STUDIO_MCP_URL="http://127.0.0.1:9/mcp",
                    REGOLO_API_KEY=None, NEON_DATA_API_URL=None, CLERK_ISSUER=None, CLERK_CLIENT_ID=None, HUSK_OPEN_BROWSER=False)


@pytest.fixture
def services(settings: Settings) -> Services:
    svc = build_services(settings)
    svc.llm = FakeProvider()  # type: ignore[assignment]
    svc.decisions = FakeDecisionModel()  # type: ignore[assignment]
    return svc


def login(services: Services, user_id: str = USER, org_id: str = ORG) -> str:
    user = User(id=user_id, org_id=org_id, org_name="Officine Esempio", email="op@officine-esempio.it",
                display_name="Operatore", roles=["admin"])
    UserRepo(services.db).upsert(user)
    sid = f"sid-{user_id}"
    SessionRepo(services.db).create(sid, user, utcnow() + timedelta(days=1))
    return sid


@pytest.fixture
def client(settings: Settings, services: Services) -> Iterator[TestClient]:
    app = create_app(settings, services)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set(SESSION_COOKIE, login(services))
        yield c


# --- synthetic documents (fictional company, no personal data) -------------------------
PROCEDURE_MD = """# Procedura di accettazione materiali

## Scopo
Ogni mattina il magazzino controlla i DDT del fornitore e ricopia a mano i dati nel gestionale.

## Controllo qualità
La qualità verifica i certificati dei lotti e compila il registro non conformità ogni settimana.
Se manca il certificato, il lotto viene bloccato e si invia una email all'ufficio acquisti.
"""


def bom_xlsx(rev: str = "C") -> bytes:
    df = pd.DataFrame({
        "Codice": ["PX200-01", "PX200-02", "PX200-03", "PX200-04"],
        "Descrizione": ["Corpo pompa", "Tenuta meccanica", "Vite M8", "Supporto motore"],
        "Quantità": [1, 1, 16 if rev == "C" else 12, 1],
        "Fornitore": ["Fonderia Nord", None, "Viteria Sud", None],
        "Peso unitario": [12.5, 0.3, None, 2.1],
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def production_csv() -> bytes:
    rows = ["Data,Turno,Pezzi,Scarti,Fermo minuti"]
    for d in range(1, 15):
        rows.append(f"2026-09-{d:02d},A,{400 + d},{d % 5},{(d * 7) % 30}")
    return "\n".join(rows).encode()


def upload_samples(client: TestClient) -> list[dict]:
    files = [
        ("files", ("Procedura_Accettazione_Materiali.md", PROCEDURE_MD.encode(), "text/markdown")),
        ("files", ("Distinta_Base_Pompa_PX200_RevC.xlsx", bom_xlsx(), "application/octet-stream")),
        ("files", ("Report_Produzione_Settimana_38.csv", production_csv(), "text/csv")),
    ]
    r = client.post("/api/v1/documents", files=files, data={"authorized": "true"})
    assert r.status_code == 200, r.text
    return r.json()["documents"]
