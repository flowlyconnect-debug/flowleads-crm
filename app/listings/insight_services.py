"""Property-manager view and housing-company card.

Both are read models built from what the organization already has: the
targets it can see on Kohteet, the targets it has handled and its leads.
Nothing here reads the reserve (matches not yet released with "Hae kohteita"),
so these pages never reveal targets the customer has not received.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.leads.models import Lead
from app.listings.models import Listing, ListingMatch
from app.listings.services import _norm
from app.listings.target_services import _visible_match_ids

# How a listing relates to the organization, strongest first.
STATE_LABELS_FI = {
    "lead": "Liidinä",
    "available": "Saatavilla",
    "hidden": "Piilotettu",
}


def _visible_matches(organization_id: int) -> list[ListingMatch]:
    """The organization's matches except the reserve: handled ones
    (lead / merged / hidden) and the released pending ones."""
    handled = ListingMatch.query.filter(
        ListingMatch.organization_id == organization_id,
        ListingMatch.status.in_(("lead_created", "merged", "dismissed")),
    )
    available = ListingMatch.query.filter(
        ListingMatch.id.in_(_visible_match_ids(organization_id, "pending"))
    )
    matches = handled.union(available).options(
        selectinload(ListingMatch.listing).selectinload(Listing.renovations)
    )
    return [m for m in matches.all() if m.listing is not None]


def _state(match: ListingMatch) -> str:
    if match.status in ("lead_created", "merged"):
        return "lead"
    return "hidden" if match.status == "dismissed" else "available"


def _company_key(listing: Listing) -> tuple[str, str]:
    """A housing company is its name within a city; a listing without a
    company name stands alone under its address."""
    name = _norm(listing.taloyhtio) or _norm(listing.osoite) or f"#{listing.id}"
    return name, _norm(listing.kaupunki)


def _best_state(states) -> str:
    for state in ("lead", "available", "hidden"):
        if state in states:
            return state
    return "available"


def _lead_meta(lead: Lead) -> dict:
    return lead.ai_contact_info if isinstance(lead.ai_contact_info, dict) else {}


def _companies(organization_id: int) -> "OrderedDict[tuple[str, str], dict]":
    """Housing companies the organization knows about, keyed by (name, city)."""
    companies: OrderedDict[tuple[str, str], dict] = OrderedDict()
    lead_ids: set[int] = set()
    for match in _visible_matches(organization_id):
        listing = match.listing
        key = _company_key(listing)
        company = companies.setdefault(
            key,
            {
                "key": key,
                "name": listing.taloyhtio or listing.osoite or "Kohde",
                "city": listing.kaupunki or "",
                "listing_id": listing.id,
                "listings": {},
                "states": set(),
                "lead_ids": set(),
                "managers": OrderedDict(),
                "pending_match_ids": [],
            },
        )
        company["listings"][listing.id] = listing
        company["listing_id"] = min(company["listing_id"], listing.id)
        company["states"].add(_state(match))
        if match.lead_id:
            company["lead_ids"].add(match.lead_id)
            lead_ids.add(match.lead_id)
        if match.status == "pending":
            company["pending_match_ids"].append(match.id)
        if (listing.isannoitsija or "").strip():
            company["managers"].setdefault(_norm(listing.isannoitsija), listing.isannoitsija.strip())

    leads = {}
    if lead_ids:
        leads = {
            lead.id: lead
            for lead in Lead.query.filter(
                Lead.organization_id == organization_id, Lead.id.in_(lead_ids)
            ).all()
        }
    for company in companies.values():
        company["leads"] = [leads[i] for i in sorted(company["lead_ids"]) if i in leads]
        company["state"] = _best_state(company["states"])
        company["renovations"] = _renovations(company["listings"].values())
    return companies


def _renovations(listings) -> list[dict]:
    """Upcoming renovations of a housing company, one row per type (the most
    confident mention wins), soonest first."""
    best: dict[str, dict] = {}
    for listing in listings:
        for renovation in listing.renovations:
            current = best.get(renovation.remonttityyppi)
            if current is None or renovation.confidence > current["confidence"]:
                best[renovation.remonttityyppi] = {
                    "type": renovation.remonttityyppi,
                    "timing": renovation.timing or "",
                    "confidence": renovation.confidence,
                    "evidence": getattr(renovation, "evidence", None) or "",
                }
    return sorted(best.values(), key=lambda r: (r["timing"] or "9999", r["type"]))


# --- property managers -----------------------------------------------------------


def list_managers(organization_id: int, search: str | None = None) -> list[dict]:
    """Property managers with their housing companies, targets and leads,
    busiest first. Sources: the organization's visible targets and leads."""
    managers: dict[str, dict] = {}

    def entry(name: str) -> dict:
        key = _norm(name)
        return managers.setdefault(
            key,
            {
                "key": key,
                "name": name.strip(),
                "phone": "",
                "email": "",
                "website": "",
                "companies": OrderedDict(),
                "lead_ids": set(),
                "last_contacted_at": None,
            },
        )

    companies = _companies(organization_id)
    for company in companies.values():
        for listing in company["listings"].values():
            if not (listing.isannoitsija or "").strip():
                continue
            manager = entry(listing.isannoitsija)
            manager["phone"] = manager["phone"] or (listing.isannoitsija_puh or "").strip()
            manager["email"] = manager["email"] or (listing.isannoitsija_email or "").strip()
            manager["website"] = manager["website"] or (listing.isannoitsija_website or "").strip()
            manager["companies"][company["key"]] = company
            manager["lead_ids"].update(company["lead_ids"])

    # leads whose listing is gone (or that were added by hand with manager details)
    leads = Lead.query.filter(
        Lead.organization_id == organization_id,
        Lead.status != "archived",
        Lead.ai_contact_info.isnot(None),
    ).all()
    lead_by_id = {lead.id: lead for lead in leads}
    for lead in leads:
        name = str(_lead_meta(lead).get("isannoitsija_yritys") or "").strip()
        if not name:
            continue
        manager = entry(name)
        meta = _lead_meta(lead)
        manager["phone"] = manager["phone"] or str(meta.get("isannoitsija_puh") or "").strip()
        manager["email"] = manager["email"] or str(meta.get("isannoitsija_email") or "").strip()
        manager["lead_ids"].add(lead.id)

    result = []
    term = _norm(search)
    for manager in managers.values():
        if term and term not in manager["key"]:
            continue
        manager_leads = [lead_by_id[i] for i in sorted(manager["lead_ids"]) if i in lead_by_id]
        contacted = [lead.last_contacted_at for lead in manager_leads if lead.last_contacted_at]
        company_list = list(manager["companies"].values())
        manager.update(
            leads=manager_leads,
            company_list=company_list,
            company_count=max(len(company_list), len({_lead_meta(l).get("taloyhtio") for l in manager_leads} - {None, ""})),
            available_count=sum(1 for c in company_list if c["state"] == "available"),
            open_lead_count=sum(1 for lead in manager_leads if lead.status == "active"),
            won_count=sum(1 for lead in manager_leads if lead.status == "won"),
            last_contacted_at=max(contacted) if contacted else None,
        )
        result.append(manager)
    result.sort(key=lambda m: (-(m["available_count"] + m["open_lead_count"]), -m["company_count"], m["key"]))
    return result


def get_manager(organization_id: int, name: str) -> dict | None:
    key = _norm(name)
    for manager in list_managers(organization_id):
        if manager["key"] == key:
            return manager
    return None


# --- housing company card ----------------------------------------------------------------


def get_company_card(organization_id: int, listing_id: int) -> dict | None:
    """Everything the organization knows about the housing company of one
    listing. None when the listing is not visible to the organization."""
    for company in _companies(organization_id).values():
        if listing_id in company["listings"]:
            return _card(company)
    return None


def _card(company: dict) -> dict:
    listings = sorted(
        company["listings"].values(),
        key=lambda l: (l.published_at or l.first_seen_at or datetime.min.replace(tzinfo=None)).replace(tzinfo=None),
        reverse=True,
    )
    build_years = sorted({l.build_year for l in listings if l.build_year})
    building_types = sorted({l.building_type for l in listings if l.building_type})
    addresses = list(OrderedDict.fromkeys(l.osoite for l in listings if l.osoite))
    notes = list(OrderedDict.fromkeys(l.tulevat_remontit for l in listings if l.tulevat_remontit))
    manager_contacts = []
    for key, name in company["managers"].items():
        source = next((l for l in listings if _norm(l.isannoitsija) == key), None)
        manager_contacts.append(
            {
                "name": name,
                "phone": (source.isannoitsija_puh or "").strip() if source else "",
                "email": (source.isannoitsija_email or "").strip() if source else "",
                "website": (source.isannoitsija_website or "").strip() if source else "",
            }
        )
    return {
        **company,
        "listing_list": listings,
        "addresses": addresses,
        "region": listings[0].maakunta if listings else "",
        "build_year": build_years[0] if build_years else None,
        "building_type": ", ".join(building_types),
        "renovation_notes": notes,
        "manager_contacts": manager_contacts,
        "state_label": STATE_LABELS_FI[company["state"]],
    }
