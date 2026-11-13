"""Finnish text for messages shown in the UI.

Services raise errors with English messages because the same text is returned
by the JSON API. The web UI is Finnish, so every flashed message goes through
`fi_message()` (the `fi_msg` template filter) right before it is displayed.
Messages that are already Finnish, and unknown ones, pass through unchanged.
"""

from __future__ import annotations

_MESSAGES: dict[str, str] = {
    # --- auth ---
    "You have been signed out.": "Olet kirjautunut ulos.",
    "Your password has been updated.": "Salasana on vaihdettu.",
    "Passwords do not match.": "Salasanat eivät täsmää.",
    "The reset link is invalid or has expired.": "Palautuslinkki on virheellinen tai vanhentunut.",
    "Unable to reset password. Please try again.": "Salasanan vaihto epäonnistui. Yritä uudelleen.",
    "Registration is disabled.": "Rekisteröityminen ei ole käytössä.",
    "Registration is not available.": "Rekisteröityminen ei ole käytössä.",
    "Invalid verification code.": "Virheellinen vahvistuskoodi.",
    "Invalid 2FA code.": "Virheellinen vahvistuskoodi.",
    "2FA is not initialized.": "Kaksivaiheista tunnistautumista ei ole otettu käyttöön.",
    "Unable to initialize 2FA.": "Kaksivaiheisen tunnistautumisen käyttöönotto epäonnistui.",
    "Invalid email or password.": "Virheellinen sähköposti tai salasana.",
    "If an account exists for that email, you will receive reset instructions.": "Jos osoitteella on käyttäjätili, saat ohjeet salasanan vaihtoon sähköpostiisi.",
    "This account is inactive.": "Tili ei ole käytössä.",
    "Invalid password.": "Virheellinen salasana.",
    "Access denied.": "Ei käyttöoikeutta.",
    "Insufficient permissions.": "Käyttöoikeutesi eivät riitä tähän toimintoon.",
    "Cross-tenant access denied.": "Ei käyttöoikeutta.",
    # --- users / organizations ---
    "Email is already registered.": "Sähköpostiosoite on jo käytössä.",
    "Email is required.": "Sähköpostiosoite on pakollinen.",
    "A valid email address is required.": "Anna kelvollinen sähköpostiosoite.",
    "Invalid email address.": "Virheellinen sähköpostiosoite.",
    "Invalid role.": "Virheellinen rooli.",
    "User not found.": "Käyttäjää ei löytynyt.",
    "User not found in organization.": "Käyttäjää ei löytynyt organisaatiosta.",
    "Assigned user not found in organization.": "Valittua vastuuhenkilöä ei löytynyt organisaatiosta.",
    "User has no email.": "Käyttäjällä ei ole sähköpostiosoitetta.",
    "You cannot deactivate your own account.": "Et voi poistaa omaa tiliäsi käytöstä.",
    "You cannot delete your own account.": "Et voi poistaa omaa tiliäsi.",
    "Organization is required.": "Valitse organisaatio.",
    "Organization not found.": "Organisaatiota ei löytynyt.",
    "Organization name is required.": "Organisaation nimi on pakollinen.",
    "Organization slug already exists.": "Samanniminen organisaatio on jo olemassa.",
    "API key revoked.": "API-avain poistettu käytöstä.",
    "API key not found.": "API-avainta ei löytynyt.",
    # --- leads ---
    "At least one identifier is required (email, phone, company, name, or source ref).": "Anna vähintään yksi tunnistetieto: nimi, yritys, sähköposti tai puhelin.",
    "At least one identifier is required.": "Anna vähintään yksi tunnistetieto: nimi, yritys, sähköposti tai puhelin.",
    "Lead created successfully.": "Liidi luotu.",
    "Lead updated.": "Liidi tallennettu.",
    "Lead archived.": "Liidi arkistoitu.",
    "Note added.": "Muistiinpano lisätty.",
    "Stage updated.": "Vaihe päivitetty.",
    "Bulk action completed.": "Massatoiminto suoritettu.",
    "Lead not found.": "Liidiä ei löytynyt.",
    "One or more leads not found.": "Kaikkia valittuja liidejä ei löytynyt.",
    "No leads selected.": "Valitse vähintään yksi liidi.",
    "Lead is required.": "Valitse liidi.",
    "Stage is required.": "Valitse vaihe.",
    "Pipeline stage not found.": "Vaihetta ei löytynyt.",
    "No pipeline stages available.": "Organisaatiolla ei ole myyntiputken vaiheita.",
    "Invalid bulk action.": "Tuntematon massatoiminto.",
    "Cannot move archived lead.": "Arkistoitua liidiä ei voi siirtää.",
    "Cannot update archived lead.": "Arkistoitua liidiä ei voi muokata.",
    "Cannot add notes to archived leads.": "Arkistoituun liidiin ei voi lisätä muistiinpanoja.",
    "Cannot email archived leads.": "Arkistoidulle liidille ei voi lähettää sähköpostia.",
    "Cannot enrich archived leads.": "Arkistoitua liidiä ei voi rikastaa.",
    "Note content is required.": "Kirjoita muistiinpano.",
    "Note must be at most 5000 characters.": "Muistiinpano voi olla enintään 5000 merkkiä.",
    "Invalid lead source.": "Virheellinen liidin lähde.",
    "Invalid website URL.": "Virheellinen verkkosivun osoite.",
    "Invalid LinkedIn URL.": "Virheellinen LinkedIn-osoite.",
    "Invalid date format.": "Virheellinen päivämäärä.",
    "A lead with this source and reference already exists.": "Sama liidi on jo olemassa.",
    "Lead has no email address.": "Liidillä ei ole sähköpostiosoitetta.",
    "Lead is unsubscribed.": "Liidi on kieltänyt sähköpostiviestit.",
    "Company not found.": "Yritystä ei löytynyt.",
    "Contact not found.": "Kontaktia ei löytynyt.",
    "Name is required.": "Nimi on pakollinen.",
    # --- AI ---
    "AI enrichment queued.": "AI-rikastus on jonossa.",
    "AI enrichment is disabled.": "AI-rikastus ei ole käytössä.",
    "Enrichment is already in progress.": "Rikastus on jo käynnissä.",
    "Could not queue enrichment.": "Rikastusta ei voitu käynnistää.",
    "Lead must have company, website, or LinkedIn URL.": "Rikastus vaatii yrityksen nimen, verkkosivun tai LinkedIn-osoitteen.",
    # --- tasks ---
    "Task created.": "Tehtävä luotu.",
    "Invalid task priority.": "Virheellinen prioriteetti.",
    "Invalid task type.": "Virheellinen tehtävätyyppi.",
    "Task completed.": "Tehtävä merkitty tehdyksi.",
    "Invalid task data.": "Tarkista tehtävän tiedot.",
    "Task not found.": "Tehtävää ei löytynyt.",
    "Invalid call outcome.": "Valitse puhelun tulos.",
    "This call is already handled.": "Tämä puhelu on jo kirjattu.",
    "You can only record your own calls.": "Voit kirjata vain omia puheluitasi.",
    "Title is required.": "Otsikko on pakollinen.",
    "Title must be at most 200 characters.": "Otsikko voi olla enintään 200 merkkiä.",
    "due_date is required.": "Eräpäivä on pakollinen.",
    "Invalid due_date format.": "Virheellinen eräpäivä.",
    "assigned_to is required.": "Valitse vastuuhenkilö.",
    "Cannot complete a cancelled task.": "Peruttua tehtävää ei voi merkitä tehdyksi.",
    # --- email ---
    "Email sent successfully.": "Sähköposti lähetetty.",
    "Email settings saved.": "Sähköpostiasetukset tallennettu.",
    "Email sending is disabled.": "Sähköpostin lähetys ei ole käytössä.",
    "Invalid form submission.": "Tarkista lomakkeen tiedot.",
    "Failed to send email.": "Sähköpostin lähetys epäonnistui.",
    "An unexpected error occurred while sending email.": "Sähköpostin lähetys epäonnistui odottamattoman virheen vuoksi.",
    "Template created.": "Sähköpostimalli luotu.",
    "Template updated.": "Sähköpostimalli tallennettu.",
    "Template deleted.": "Sähköpostimalli poistettu.",
    "Template not found.": "Sähköpostimallia ei löytynyt.",
    "System templates cannot be deleted.": "Järjestelmän mallia ei voi poistaa.",
    "Test email sent.": "Testiviesti lähetetty.",
    "Test email failed.": "Testiviestin lähetys epäonnistui.",
    "Subject is required.": "Aihe on pakollinen.",
    "Subject template is required.": "Aihe on pakollinen.",
    "Email body is too large.": "Viesti on liian pitkä.",
    "Sender email is not configured.": "Lähettäjän sähköpostiosoitetta ei ole määritetty.",
    "No sending domain configured.": "Lähetysdomainia ei ole määritetty.",
    "Set a sending domain first.": "Määritä ensin lähetysdomain.",
    "Invalid from email address.": "Virheellinen lähettäjän sähköpostiosoite.",
    "Mailgun is not configured (MAILGUN_API_KEY missing).": "Sähköpostipalvelua ei ole määritetty. Ota yhteyttä ylläpitoon.",
    "Daily email sending limit reached for this organization.": "Organisaation päivittäinen sähköpostien lähetysraja on täynnä.",
    # --- sequences / automations ---
    "Sequence not found.": "Sekvenssiä ei löytynyt.",
    "Sequence has no steps.": "Sekvenssissä ei ole yhtään vaihetta.",
    "Lead is already enrolled in this sequence.": "Liidi on jo tässä sekvenssissä.",
    "Enrollment not found.": "Liidi ei ole tässä sekvenssissä.",
    "Step not found.": "Vaihetta ei löytynyt.",
    "Automation not found.": "Automaatiota ei löytynyt.",
    "Invalid trigger type.": "Virheellinen käynnistin.",
    "Invalid action type.": "Virheellinen toiminto.",
    # --- webhooks ---
    "Webhook endpoint created.": "Webhook luotu.",
    "Webhook endpoint updated.": "Webhook tallennettu.",
    "Webhook endpoint deleted.": "Webhook poistettu.",
    "Failed to create webhook endpoint.": "Webhookin luonti epäonnistui.",
    "Failed to update webhook endpoint.": "Webhookin tallennus epäonnistui.",
    "Invalid webhook endpoint data.": "Tarkista webhookin tiedot.",
    "Select at least one event.": "Valitse vähintään yksi tapahtuma.",
    "Test webhook sent successfully.": "Testikutsu lähetetty.",
    "Test webhook failed.": "Testikutsu epäonnistui.",
    "Webhook delivery retry executed.": "Toimitus yritetty uudelleen.",
    # --- proposals ---
    "Proposal not found.": "Tarjousta ei löytynyt.",
    "Only draft proposals can be deleted.": "Vain luonnoksen voi poistaa.",
    "Only draft proposals can be sent.": "Vain luonnoksen voi lähettää.",
    "Proposal is already locked.": "Lähetettyä tarjousta ei voi enää muokata.",
    "Proposal has expired.": "Tarjouksen voimassaolo on päättynyt.",
    "Proposal already accepted.": "Tarjous on jo hyväksytty.",
    "Proposal cannot be accepted.": "Tarjousta ei voi hyväksyä.",
    "Proposal cannot be declined.": "Tarjousta ei voi hylätä.",
    "Signature name is required.": "Kirjoita allekirjoittajan nimi.",
    # --- calendar ---
    "Event not found.": "Tapaamista ei löytynyt.",
    "Unsupported calendar provider.": "Kalenteripalvelua ei tueta.",
    "Invalid provider.": "Kalenteripalvelua ei tueta.",
    # --- forms ---
    "Form not found.": "Lomaketta ei löytynyt.",
    # --- GDPR / exports ---
    "Invalid export type.": "Virheellinen vientityyppi.",
    "Invalid export request.": "Virheellinen vientipyyntö.",
    "Export is not ready.": "Vienti ei ole vielä valmis.",
    "Export file missing.": "Vientitiedostoa ei löytynyt. Tee vienti uudelleen.",
    "Download link has expired.": "Latauslinkki on vanhentunut.",
    "Download link not found.": "Latauslinkkiä ei löytynyt.",
    # --- backups ---
    "Backup file not found.": "Varmuuskopiota ei löytynyt.",
    "Backup restored successfully.": "Varmuuskopio palautettu.",
    "Backup failed.": "Varmuuskopiointi epäonnistui.",
    "Restore failed.": "Palautus epäonnistui.",
    "Database dump failed.": "Tietokannan vienti epäonnistui.",
    "Invalid backup filename.": "Virheellinen varmuuskopion nimi.",
    "Only superadmin can restore backups.": "Vain pääkäyttäjä voi palauttaa varmuuskopion.",
    "2FA must be enabled for restore.": "Palautus vaatii kaksivaiheisen tunnistautumisen.",
    "BACKUP_S3_BUCKET is not configured.": "Varmuuskopioiden tallennuspaikkaa (BACKUP_S3_BUCKET) ei ole määritetty.",
    "You must confirm that you understand data will be overwritten.": "Vahvista, että ymmärrät nykyisten tietojen korvautuvan.",
    "The organization must keep at least one active admin.": "Organisaatiolla pitää olla vähintään yksi aktiivinen ylläpitäjä.",
    "You cannot change your own role.": "Et voi vaihtaa omaa rooliasi.",
    "Password must be at least 12 characters.": "Salasanan pitää olla vähintään 12 merkkiä.",
    "Two-factor sign-in required.": "Kaksivaiheinen tunnistautuminen vaaditaan.",
}

# Messages that carry a variable part: (English prefix, Finnish prefix).
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Backup created: ", "Varmuuskopio luotu: "),
    ("Database restore failed: ", "Tietokannan palautus epäonnistui: "),
    ("Mailgun request failed: ", "Sähköpostipalvelun kutsu epäonnistui: "),
    ("S3 upload failed: ", "Varmuuskopion tallennus epäonnistui: "),
    ("Unknown template variable: ", "Tuntematon muuttuja: "),
    ("Account is temporarily locked", "Tili on väliaikaisesti lukittu liian monen epäonnistuneen kirjautumisen vuoksi. Yritä hetken kuluttua uudelleen."),
)


def fi_message(message) -> str:
    """Finnish version of a UI message; the message itself when unknown."""
    if not isinstance(message, str):
        return message
    text = message.strip()
    translated = _MESSAGES.get(text)
    if translated is not None:
        return translated
    for english, finnish in _PREFIXES:
        if text.startswith(english):
            if finnish.endswith(": "):
                return finnish + text[len(english):]
            return finnish
    return message
