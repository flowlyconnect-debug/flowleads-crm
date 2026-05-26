TRIGGER_TYPES = (
    "lead_created",
    "lead_stage_changed",
    "lead_score_changed",
    "lead_no_activity",
    "lead_tag_added",
    "task_overdue",
    "email_opened",
    "sequence_completed",
)

ACTION_TYPES = (
    "create_task",
    "send_email",
    "enroll_in_sequence",
    "change_stage",
    "assign_lead",
    "add_tag",
    "remove_tag",
    "send_webhook",
    "notify_user",
)

LOG_RESULTS = ("success", "failed", "skipped")

TRIGGER_LABELS_FI = {
    "lead_created": "Uusi liidi",
    "lead_stage_changed": "Liidin vaihe muuttui",
    "lead_score_changed": "Pisteet muuttuivat",
    "lead_no_activity": "Ei aktiviteettia",
    "lead_tag_added": "Tunniste lisätty",
    "task_overdue": "Tehtävä myöhässä",
    "email_opened": "Sähköposti avattu",
    "sequence_completed": "Sekvenssi valmis",
}

TRIGGER_DESCRIPTIONS_FI = {
    "lead_created": "Kun uusi liidi luodaan järjestelmään.",
    "lead_stage_changed": "Kun liidi siirtyy tiettyyn putken vaiheeseen.",
    "lead_score_changed": "Kun liidin pistemäärä ylittää tai alittaa rajan.",
    "lead_no_activity": "Kun liidillä ei ole aktiviteettia N päivään.",
    "lead_tag_added": "Kun liidille lisätään tietty tunniste.",
    "task_overdue": "Kun tehtävä on myöhässä määritellyn ajan.",
    "email_opened": "Kun liidi avaa lähetetyn sähköpostin.",
    "sequence_completed": "Kun liidi suorittaa sähköpostisekvenssin.",
}

ACTION_LABELS_FI = {
    "create_task": "Luo tehtävä",
    "send_email": "Lähetä sähköposti",
    "enroll_in_sequence": "Lisää sekvenssiin",
    "change_stage": "Vaihda vaihe",
    "assign_lead": "Osoita liidi",
    "add_tag": "Lisää tunniste",
    "remove_tag": "Poista tunniste",
    "send_webhook": "Lähetä webhook",
    "notify_user": "Ilmoita käyttäjälle",
}
