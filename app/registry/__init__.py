"""Public registers as a lead source: Ryhti (SYKE) buildings and permits and the
PRH open data list of housing companies (asunto-osakeyhtiöt).

Status: internal only. The data is collected, linked and turned into signals
(age-based estimates, oil heating, alteration permits), and a superadmin can
preview it on `/admin/registry`. Nothing here reaches a customer: no
`Listing`, no `ListingMatch`, no target. Publishing the signals to the
catalogue is a later, separate step. See docs/ARKKITEHTUURI_KOHDEKATALOGI.md
("Julkiset rekisterit").
"""
