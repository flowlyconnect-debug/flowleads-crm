"""How far a renovation is: decided, planned or only a need.

- decided ("Päätetty"): the housing company has decided it (yhtiökokous, a
  contractor chosen or being tendered, work started, a building permit).
- planned ("Suunniteltu"): in a maintenance plan (PTS) or a plan with a year,
  design or a condition survey under way, but not decided.
- need ("Tarve"): neither, e.g. an estimate from the building's age, oil
  heating, or a mention with no plan or year ("edessä joskus").

Read from the renovation's own evidence and timing (the Analyzer's quote, an
import row, a register signal). ListingRenovation.stage stores it; rows saved
before the column existed are read on the fly (`renovation_stage`). Kohteet
keeps "need" targets on a tab of their own (housing.classify -> tier "T").
"""

from __future__ import annotations

import re

from app.listings.housing import fold

STAGES = ("decided", "planned", "need")
STAGE_LABELS = {"decided": "Päätetty", "planned": "Suunniteltu", "need": "Tarve"}
STAGE_POINTS = {"decided": 20, "planned": 10, "need": 0}

# folded text (lowercase, no accents); matched on word starts
_NOT_DECIDED = re.compile(r"\bei (ole |viela |ole viela )?(paatetty|paatoksia|hyvaksytty)|\bpaatos puuttuu|\bpaattamatta")
# design or a survey being under way is planning, not a decision to renovate
_PLANNING_UNDER_WAY = re.compile(
    r"\b((hanke)?suunnittelu|kuntotutkimu\w*|kuntoarvi\w*|(tarve|esi)?selvity\w*|suunnitelman laadinta)"
    r"( \w+){0,2} (on )?(kaynnissa|aloitettu|alkanut|tilattu|kaynnistetty|valmistuu)"
    # approving the design or a survey is not yet deciding the renovation
    r"|\b(hyvaksyi|hyvaksytty|paatti|paatetty|paatettiin)( \w+){0,2} "
    r"((hanke)?suunnittelu|kuntotutkimukse|kuntoarvio|selvitykse|tilata)\w*"
)
_DECIDED = re.compile(
    r"\b("
    r"paatetty|paatti|paattanyt|paatos tehty|paatoksen|paatettiin|"
    r"hyvaksytty|hyvaksyi|hyvaksynyt|"
    r"urakoitsija (on )?valittu|urakoitsijaksi valittiin|urakkasopimus|"
    r"kilpailutettu|kilpailutus (on )?kaynnissa|kilpailutetaan|tarjouspyynnot|"
    r"(\w*remontti|\w*korjaus|\w*saneeraus|tyot|urakka) (on |ovat )?(kaynnissa|alkanut|alkavat|alkaa|aloitettu|aloitetaan)|"
    r"(rakennus|toimenpide|muutostyo)?lupa (on )?(myonnetty|saatu)|muutostyolupa|"
    r"rahoitus jarjestetty|laina (on )?myonnetty|lainapaatos"
    r")"
)
_PLANNED = re.compile(
    r"\b("
    r"pts|kunnossapitosuunnitelm|kunnossapitotarveselvity|kpts|"
    r"suunnitelm|suunnitteilla|suunniteltu|suunnittelu|hankesuunnittelu|"
    r"kuntotutkimu|kuntoarvi|tarveselvity|esiselvity|selvitetaan|"
    r"budjetoitu|aikataulutettu|ohjelmoitu"
    r")"
)
_YEAR = re.compile(r"(?<!\d)(19|20)\d\d(?!\d)")
_ESTIMATE_NOTE = "ei varmistettu tieto"  # app/registry/publish.py::ESTIMATE_NOTE


def classify_stage(evidence: str | None, timing: str | None, timing_year: int | None = None) -> str:
    """decided / planned / need from the renovation's own words."""
    raw = " ".join(p for p in (evidence, timing) if p)
    text = fold(raw)
    if _ESTIMATE_NOTE in text:
        # a register estimate: only an alteration permit is a decision
        return "decided" if text.startswith("muutostyolupa") else "need"
    decided_text = _PLANNING_UNDER_WAY.sub(" ", text)
    if _DECIDED.search(decided_text) and not _NOT_DECIDED.search(text):
        return "decided"
    if timing_year or _YEAR.search(timing or "") or _PLANNED.search(text):
        return "planned"
    return "need"


def renovation_stage(renovation) -> str:
    """The stored stage, or read from the renovation when not stored yet."""
    stage = getattr(renovation, "stage", None)
    if stage in STAGES:
        return stage
    return classify_stage(
        getattr(renovation, "evidence", None), getattr(renovation, "timing", None), getattr(renovation, "timing_year", None)
    )


def best_stage(renovations) -> str | None:
    """The furthest stage among the renovations (decided > planned > need)."""
    stages = [renovation_stage(r) for r in renovations if r is not None]
    return min(stages, key=STAGES.index) if stages else None
