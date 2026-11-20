"""
tests/test_ph247_flug_store_orte.py – Phase 247 (Issue #385)

Watch-Store (JSON, atomar, nutzergetrennt) und Ortssuche (Travelpayouts places2).
"""

import json
import stat

import httpx
import pytest

from bot.flug import store
from bot.flug.orte import OrtsFehler, suche_orte
from tests.flug_testdaten import watch


@pytest.fixture(autouse=True)
def store_isoliert(tmp_path, monkeypatch):
    """Verhindert, dass Tests in die echte ~/.fabbot/flug_watches.json schreiben."""
    monkeypatch.setattr(store, "_STORE_FILE", tmp_path / "flug_watches.json")
    yield


# ── Store ─────────────────────────────────────────────────────────────────────


def test_leerer_store():
    assert store.lade_watches() == ()


async def test_hinzufuegen_und_laden():
    w = watch()
    assert await store.fuege_hinzu(w) == w
    assert store.lade_watches() == (w,)


async def test_datei_nur_fuer_owner_lesbar():
    await store.fuege_hinzu(watch())
    modus = stat.S_IMODE(store._STORE_FILE.stat().st_mode)
    assert modus == 0o600


async def test_keine_tmp_datei_bleibt_liegen():
    await store.fuege_hinzu(watch())
    assert [p.name for p in store._STORE_FILE.parent.iterdir()] == ["flug_watches.json"]


async def test_limit_pro_nutzer(monkeypatch):
    monkeypatch.setattr(store, "MAX_WATCHES_PRO_USER", 2)
    assert await store.fuege_hinzu(watch(id="00000001"))
    assert await store.fuege_hinzu(watch(id="00000002"))
    assert await store.fuege_hinzu(watch(id="00000003")) is None
    # Limit gilt pro Nutzer, nicht global
    assert await store.fuege_hinzu(watch(id="00000004", user_id=222, chat_id=222))


async def test_watches_von_filtert_nach_nutzer():
    eigene = watch(id="00000001")
    await store.fuege_hinzu(eigene)
    await store.fuege_hinzu(watch(id="00000002", user_id=222, chat_id=222))
    assert store.watches_von(111) == (eigene,)


async def test_entfernen_nur_eigene():
    await store.fuege_hinzu(watch(id="00000001", user_id=222, chat_id=222))
    assert await store.entferne(111, "00000001") is None
    assert len(store.lade_watches()) == 1
    entfernt = await store.entferne(222, "00000001")
    assert entfernt is not None and entfernt.id == "00000001"
    assert store.lade_watches() == ()


async def test_ersetze_aktualisiert_und_entfernt():
    a, b = watch(id="0000000a"), watch(id="0000000b")
    await store.fuege_hinzu(a)
    await store.fuege_hinzu(b)
    await store.ersetze((a.model_copy(update={"letzter_preis": 77}),), entfernt_ids=frozenset({"0000000b"}))
    (uebrig,) = store.lade_watches()
    assert uebrig.id == "0000000a"
    assert uebrig.letzter_preis == 77


async def test_ersetze_belebt_zwischendurch_geloeschte_watch_nicht_wieder():
    a = watch(id="0000000a")
    await store.fuege_hinzu(a)
    await store.entferne(111, "0000000a")
    await store.ersetze((a.model_copy(update={"letzter_preis": 77}),))
    assert store.lade_watches() == ()


def test_kaputte_datei_ergibt_leeren_store():
    store._STORE_FILE.write_text("{kaputt", encoding="utf-8")
    assert store.lade_watches() == ()


async def test_ungueltige_eintraege_werden_uebersprungen():
    gut = watch()
    store._STORE_FILE.write_text(json.dumps({"watches": [{"id": "x"}, gut.model_dump(mode="json")]}), encoding="utf-8")
    assert store.lade_watches() == (gut,)


async def test_fehlerstatus_ueberlebt_watch_aenderungen():
    await store.speichere_status(fehlschlaege=2, gemeldet=True)
    await store.fuege_hinzu(watch())
    assert store.lade_status() == {"fehlschlaege": 2, "gemeldet": True, "letzter_lauf": None}


async def test_status_felder_werden_einzeln_gemergt():
    await store.speichere_status(fehlschlaege=2, gemeldet=True)
    await store.speichere_status(letzter_lauf="2026-11-20T18:00:00+00:00")
    assert store.lade_status() == {"fehlschlaege": 2, "gemeldet": True, "letzter_lauf": "2026-11-20T18:00:00+00:00"}


@pytest.mark.parametrize("inhalt", ["{kaputt", "[1, 2]", b"\xff\xfe"])
async def test_beschaedigte_datei_wird_beim_schreiben_nicht_ueberschrieben(inhalt):
    """Review M-2/L5: eine kaputte Datei darf nicht mit einem leeren Stand überschrieben werden."""
    if isinstance(inhalt, bytes):
        store._STORE_FILE.write_bytes(inhalt)
    else:
        store._STORE_FILE.write_text(inhalt, encoding="utf-8")
    vorher = store._STORE_FILE.read_bytes()
    for schreibpfad in (
        store.fuege_hinzu(watch()),
        store.entferne(111, "ab12cd34"),
        store.ersetze((watch(),)),
        store.speichere_status(fehlschlaege=1),
    ):
        with pytest.raises(store.StoreFehler):
            await schreibpfad
    assert store._STORE_FILE.read_bytes() == vorher


async def test_ungueltige_eintraege_ueberleben_das_schreiben():
    """Review M-2: z. B. nach einer Modelländerung – nicht stillschweigend löschen."""
    fremd = {"id": "zukunft", "neues_feld": True}
    store._STORE_FILE.write_text(json.dumps({"watches": [fremd]}), encoding="utf-8")
    await store.fuege_hinzu(watch())
    roh = json.loads(store._STORE_FILE.read_text(encoding="utf-8"))
    assert fremd in roh["watches"]
    assert len(roh["watches"]) == 2


async def test_id_kollision_bekommt_neue_id():
    """Review L4: gleiche id bei zwei Nutzern darf nicht zu Fremd-Löschung führen."""
    await store.fuege_hinzu(watch(id="0000000a", user_id=222, chat_id=222))
    gespeichert = await store.fuege_hinzu(watch(id="0000000a"))
    assert gespeichert is not None and gespeichert.id != "0000000a"
    await store.entferne(111, gespeichert.id)
    assert [(w.id, w.user_id) for w in store.lade_watches()] == [("0000000a", 222)]


async def test_ersetze_trifft_nur_gleichen_nutzer():
    fremde = watch(id="0000000a", user_id=222, chat_id=222)
    store._STORE_FILE.write_text(json.dumps({"watches": [fremde.model_dump(mode="json")]}), encoding="utf-8")
    await store.ersetze((watch(id="0000000a", letzter_preis=1),))
    assert store.lade_watches() == (fremde,)


# ── Ortssuche ─────────────────────────────────────────────────────────────────


def _places(*eintraege: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["term"] == "Rom"
        assert request.url.params["locale"] == "de"
        return httpx.Response(200, json=list(eintraege))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ortssuche_liest_felder():
    async with _places({"type": "city", "code": "ROM", "name": "Rom", "country_name": "Italien"}) as client:
        (ort,) = await suche_orte("Rom", client=client)
    assert (ort.code, ort.name, ort.land) == ("ROM", "Rom", "Italien")


async def test_ortssuche_verwirft_ungueltige_codes_und_begrenzt():
    eintraege = [{"type": "city", "code": "rom!", "name": "X"}, {"type": "city", "code": "", "name": "Y"}]
    eintraege += [{"type": "city", "code": f"A{c}{c}", "name": f"Ort {c}"} for c in "BCDEFGH"]
    async with _places(*eintraege) as client:
        orte = await suche_orte("Rom", client=client)
    assert [o.code for o in orte] == ["ABB", "ACC", "ADD", "AEE", "AFF"]


async def test_ortssuche_dedupliziert_codes():
    gleich = {"type": "city", "code": "ROM", "name": "Rom"}
    async with _places(gleich, {**gleich, "type": "airport"}) as client:
        assert len(await suche_orte("Rom", client=client)) == 1


@pytest.mark.parametrize("term", ["", " ", "x", "y" * 61])
async def test_ortssuche_lehnt_ungueltige_eingabe_ab(term):
    with pytest.raises(OrtsFehler):
        await suche_orte(term)


async def test_ortssuche_http_fehler():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    async with client:
        with pytest.raises(OrtsFehler):
            await suche_orte("Rom", client=client)


async def test_ortssuche_entschaerft_links_und_zeilenumbrueche():
    """Security M1: API-Namen landen als Klartext in Telegram, das URLs automatisch verlinkt."""
    eintraege = [
        {"type": "city", "code": "AAA", "name": "Rom\nJetzt buchen: https://evil.example/pay", "country_name": "X"},
        {"type": "city", "code": "BBB", "name": "evil.example/pay", "country_name": "Italien"},
        {"type": "city", "code": "CCC", "name": "Neapel", "country_name": "bit.ly/x"},
        {"type": "city", "code": "DDD", "name": "Ro\u202em", "country_name": "Italien"},
    ]
    async with _places(*eintraege) as client:
        orte = {o.code: o for o in await suche_orte("Rom", client=client)}
    assert orte["AAA"].name == "AAA"
    assert orte["BBB"].name == "BBB"
    assert (orte["CCC"].name, orte["CCC"].land) == ("Neapel", "")
    assert orte["DDD"].name == "Ro m"


async def test_ortssuche_bricht_bei_riesiger_antwort_ab(monkeypatch):
    """Security L11: Antwortgröße ist begrenzt."""
    from bot.flug import netz

    monkeypatch.setattr(netz, "MAX_BYTES", 100)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=["x" * 500])))
    async with client:
        with pytest.raises(OrtsFehler, match="zu groß"):
            await suche_orte("Rom", client=client)
