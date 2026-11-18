"""Datan laatu (superadmin `/admin/data-quality`): how complete and clean the
shared catalogue is, measured on what customers can see. Each row says what
is missing, how much, why it matters and what fixes it."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_

from app.extensions import db


def _level(share: float, good: float, poor: float, *, higher_is_better: bool = True) -> str:
    if higher_is_better:
        return "ok" if share >= good else ("bad" if share < poor else "warn")
    return "ok" if share <= good else ("bad" if share > poor else "warn")


def _row(label, count, total, *, good, poor, higher_is_better=True, why="", fix=""):
    share = (count / total) if total else (1.0 if higher_is_better else 0.0)
    return {
        "label": label,
        "count": count,
        "total": total,
        "percent": round(100 * share),
        "level": _level(share, good, poor, higher_is_better=higher_is_better) if total else "ok",
        "why": why,
        "fix": fix,
    }


def data_quality(*, now: datetime | None = None) -> dict:
    from app.listings.models import Listing, ListingMatch, ListingRenovation

    now = now or datetime.now(timezone.utc)
    active = Listing.query.filter(Listing.is_active.is_(True))
    total = active.count()
    # what customers see: listings some profile matched and nobody handled yet
    shown_ids = (
        db.session.query(ListingMatch.listing_id).filter(ListingMatch.status == "pending").distinct().subquery()
    )
    shown = active.filter(Listing.id.in_(db.session.query(shown_ids.c.listing_id)))
    shown_total = shown.count()

    def has(column):
        return func.coalesce(func.trim(column), "") != ""

    contact = or_(has(Listing.isannoitsija_puh), has(Listing.isannoitsija_email))
    contacts = [
        _row("Kohteissa puhelin tai sähköposti", shown.filter(contact).count(), shown_total, good=0.7, poor=0.4,
             why="Ilman yhteystietoa asiakas ei voi ottaa yhteyttä; rivillä lukee \"isännöitsijä haussa\".",
             fix="Yhteystietohaku (Enricher) n8n:ssä päälle. Se hakee vain asiakkaiden kohteille."),
        _row("Kohteissa isännöitsijän nimi", shown.filter(has(Listing.isannoitsija)).count(), shown_total, good=0.7, poor=0.4,
             why="Isännöitsijät-sivu ja yhteydenotto nojaavat siihen.", fix="Yhteystietohaku tai tuonnin sarake \"isannoitsija\"."),
        _row("Yhteystietoa odottaa", shown.filter(Listing.contact_status == "pending").count(), shown_total,
             good=0.1, poor=0.3, higher_is_better=False,
             why="Jono, jota yhteystietohaku käsittelee.", fix="Jos jono ei pienene, Enricher ei ole käynnissä."),
    ]
    last_contact = db.session.query(func.max(Listing.contact_claimed_at)).scalar()
    identity = [
        _row("Taloyhtiön nimi tiedossa", active.filter(has(Listing.taloyhtio)).count(), total, good=0.8, poor=0.5,
             why="Ilman nimeä otsikkona näkyy katuosoite ja saman yhtiön tunnistus nojaa osoitteeseen.",
             fix="Tuontiin sarake \"taloyhtio\" tai nimet PRH:sta (julkiset rekisterit)."),
        _row("Y-tunnus tiedossa", active.filter(has(Listing.ytunnus)).count(), total, good=0.5, poor=0.2,
             why="Varmin tapa yhdistää saman taloyhtiön ilmoitukset ja rekisterit.", fix="PRH-haku nimellä ja kunnalla."),
        _row("Rakennusvuosi tiedossa", active.filter(Listing.build_year.isnot(None)).count(), total, good=0.6, poor=0.3,
             why="Rakennusvuosi- ja ikäsuodattimet ohittavat kohteet, joilta se puuttuu.", fix="Ryhti-rakennustiedot (julkiset rekisterit)."),
        _row("Julkaisupäivä tiedossa", active.filter(Listing.published_at.isnot(None)).count(), total, good=0.6, poor=0.2,
             why="Tuoreuspisteet lasketaan muuten tuontipäivästä, jolloin kaikki näyttävät tuoreilta.",
             fix="Tuontiin sarake \"julkaistu\"."),
    ]
    renovations = ListingRenovation.query.join(Listing).filter(Listing.is_active.is_(True))
    renovation_total = renovations.count()
    year = now.year
    content = [
        _row("Remontilla vuosi", renovations.filter(ListingRenovation.timing_year.isnot(None)).count(), renovation_total,
             good=0.8, poor=0.5, why="Aikaikkuna ja ajoituspisteet tarvitsevat vuoden.", fix="Analyzer lukee vuoden tekstistä."),
        _row("Vuosi jo mennyt (mahdollisesti tehty)", renovations.filter(ListingRenovation.timing_year < year).count(),
             renovation_total, good=0.05, poor=0.15, higher_is_better=False,
             why="Näytetään merkinnällä \"mahdollisesti jo tehty\"; yli vuoden vanhat eivät ole kohteita.",
             fix="Uudet ilmoitukset päivittävät tiedon."),
        _row("Päätetty tai suunniteltu", renovations.filter(ListingRenovation.stage.in_(("decided", "planned"))).count(),
             renovation_total, good=0.6, poor=0.3, why="Vain nämä näkyvät Kohteet-päälistalla; tarpeet ovat Ennakoissa.", fix=""),
        _row("Päätetty", renovations.filter(ListingRenovation.stage == "decided").count(), renovation_total,
             good=0.15, poor=0.05, why="Vahvin signaali. Pieni osuus tarkoittaa, että lähes kaikki ovat PTS-listoja.",
             fix="Lähteet, joissa on yhtiökokousten päätöksiä (esim. isännöitsijäkumppanit)."),
        _row("Tyyppinä \"Muu remontti\"", renovations.filter(ListingRenovation.remonttityyppi == "Muu remontti").count(),
             renovation_total, good=0.1, poor=0.25, higher_is_better=False,
             why="Ei osu yhdenkään asiakkaan urakkaan.", fix="Analyzerin ohje tai tekstisäännöt tarkemmiksi."),
        _row("Varmuus alle 60 %", renovations.filter(ListingRenovation.confidence < 60).count(), renovation_total,
             good=0.15, poor=0.3, higher_is_better=False,
             why="Profiilin vähimmäisvarmuus (oletus 50) suodattaa osan pois.", fix=""),
    ]
    pipeline = {
        "analysis_pending": active.filter(Listing.analysis_status == "pending").count(),
        "analysis_failed": active.filter(Listing.analysis_status == "failed").count(),
        "last_analysis": db.session.query(func.max(Listing.analyzed_at)).scalar(),
        "last_contact": last_contact,
        "contacts_never_run": last_contact is None,
        "sources": db.session.query(Listing.source, func.count(Listing.id)).filter(Listing.is_active.is_(True))
        .group_by(Listing.source).order_by(func.count(Listing.id).desc()).all(),
    }
    sections = [
        {"title": "Yhteystiedot asiakkaiden kohteissa", "rows": contacts},
        {"title": "Taloyhtiön tunnistus", "rows": identity},
        {"title": "Remonttitiedot", "rows": content},
    ]
    issues = sum(1 for s in sections for r in s["rows"] if r["level"] == "bad")
    return {"total": total, "shown_total": shown_total, "renovation_total": renovation_total,
            "sections": sections, "pipeline": pipeline, "issues": issues}
