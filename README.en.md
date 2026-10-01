[Español](README.md) | **English**

# CORTEC Helpdesk

Frappe Helpdesk customizations for Corporación de Tecnología CORTEC S.R.L.

> UI labels in this app are in Spanish, since that is the language of the site
> it runs on. Where a label is quoted below, the Spanish string is the one you
> will see on screen.

## License

AGPL-3.0-or-later — see the [LICENSE](LICENSE) file.

## Features

1. **Per-agent ticket visibility** — each HD agent only sees and opens the
   tickets assigned to them.
2. **Automatic ticket assignment** — when a ticket is created, the responsible
   agent is resolved through the chain:
   `sender email → Contact → Customer → account_manager`
3. **Per-doctype email routing** — forces every outgoing email to use the right
   email account for the module it came from.
4. **Telegram alerts** — audible alert on the assigned agent's phone when a
   WhatsApp message or an email comes in from a customer (optional).
5. **Browser alerts** — sound and browser notification in /crm and /helpdesk for
   the same events (optional).
6. **Raven alerts** — direct message from a Raven bot, without leaving the
   server (optional).
7. **Consent log** — every privacy or marketing consent is kept as an immutable
   record with date, channel, IP, URL and the accepted text, in the style of
   Bitrix24's "Agreements" (Costa Rican Law 8968).
8. **Erasure and revocation requests** — a case file with the deadlines of
   Decree 37554-JP, a search across everything Frappe stores about the data
   subject, and irreversible anonymization.

## Layout

```
cortec_helpdesk/
├── LICENSE
├── setup.py
├── requirements.txt
├── README.md                   # Spanish
├── README.en.md                # this file
└── cortec_helpdesk/
    ├── __init__.py
    ├── hooks.py
    ├── api.py                   # leads-intake Worker endpoints
    ├── consent_log.py           # consent log
    ├── privacy.py               # erasure requests (Law 8968)
    ├── patches/                 # migration of the legacy consent fields
    ├── public/js/
    │   └── consent_record.js    # "Registrar consentimiento" button
    └── overrides/
        ├── __init__.py
        ├── hd_ticket.py         # permissions and automatic assignment
        ├── communication.py     # per-doctype email routing
        ├── alert_utils.py       # shared alert helpers
        ├── alerts.py            # dispatcher: resolves the event and fans out
        ├── telegram.py          # Telegram channel
        ├── raven.py             # Raven channel
        ├── browser_alerts.py    # audible alerts in /crm and /helpdesk
        ├── consent.py           # consent fields on Lead/Contact
        └── email_group_member.py  # marketing list subscribe/unsubscribe
```

## Installation

```bash
bench get-app cortec_helpdesk https://github.com/pvanderlaatu/cortec_helpdesk
bench --site site.domain.com install-app cortec_helpdesk
bench --site site.domain.com migrate
bench restart
```

## Required configuration

### 1. Email accounts

| Account                 | Default Outgoing | Default Incoming | Outgoing | Incoming |
| ----------------------- | :--------------: | :--------------: | :------: | :------: |
| no-reply@domain.com     |        ✅        |        ❌        |    ✅    |    ❌    |
| mail1@domain.com        |        ❌        |        ✅        |    ✅    |    ✅    |
| mail2@domain.com        |        ❌        |        ❌        |    ✅    |    ✅    |

Extra settings on **mail1@** and **mail2@** (Outgoing tab):

- ✅ Always use this address as the sender address
- ✅ Always use this name as the sender name

Settings on **mail1@** (Incoming tab):

- Sync option: UNSEEN
- IMAP folder: INBOX only → Append to: HD Ticket
- Do not link INBOX.Sent
- ✅ Enable automatic linking to documents

### 2. Roles

Frappe Helpdesk uses the roles **Agent** and **Agent Manager** (assigns and
administers). These are the ones `helpdesk.utils.is_agent` / `is_agent_manager`
recognize: without one of them, the user lands in the customer portal instead of
the agent interface.

| Role           | Purpose                   | Visibility           |
| -------------- | ------------------------- | -------------------- |
| System Manager | Administrator             | All tickets          |
| Agent Manager  | Supervisor / assigner     | All tickets          |
| HD Manager     | Helpdesk supervisor       | All tickets          |
| Agent          | Support agent             | Assigned tickets only |

The legacy names **HD Agent** and **HD Agent Lead** are still recognized, in case
a site created them by hand, but they are not standard Helpdesk roles.

Assignment notices and unassigned-ticket alerts go to users holding
**Agent Manager** or **HD Agent Lead**.

An agent must also be created from the Helpdesk admin area (that creates the
**HD Agent** record and grants the "Agent" role). If they also handle WhatsApp in
/crm, they need the **Sales User** role.

### 3. Account Manager on each Customer

```
ERPNext → Selling → Customers → [Customer]
  → More information → Account Manager → [agent's email]
```

### 4. Contacts linked to Customers

```
ERPNext → CRM → Contacts → [Contact]
  → Link with → Customer → [customer name]
```

### 5. Scheduler

```
System Settings
  → Run Jobs only Daily if Inactive For (Days) → 365
```

### 6. Telegram alerts (optional)

Frappe CRM and Helpdesk only alert on screen. With this option, the assigned
agent gets a message from a Telegram bot — audible even with the phone locked —
when either of these arrives:

- a **WhatsApp message** from a customer on a CRM Lead / CRM Deal, or
- an **email** received on a CRM Lead, CRM Deal or HD Ticket.

The alert carries only the document, the customer name and a link; **never the
message body or the subject**. Emails sent by agents themselves raise no alert.

**Setup**

1. In Telegram, talk to **@BotFather** → `/newbot` and copy the token.
2. `CORTEC Helpdesk Settings` → **Avisos por Telegram** (Telegram alerts):
   - ✅ Enable Telegram alerts and paste the token.
   - Choose what to alert on: WhatsApp and/or email.
   - *Agrupar avisos (minutos)* (group alerts, minutes): several messages in a
     row from the same customer produce a single alert per agent and document
     (0 = every message).
   - Save.
3. Each agent opens the bot in Telegram and presses **/start**.
4. Within the next 24 h: **Telegram → Detectar chats** (detect chats), pick the
   chat and the agent → Add. WhatsApp or email can be turned off per agent in the
   table.
5. **Telegram → Enviar prueba** (send test) to confirm it reaches every phone.

The site needs `host_name` in `site_config.json` so the alert links point at the
right domain, and the `short` queue workers must be running.

**On the agent's phone**

- Set a custom tone for the bot chat (Telegram → chat → Notifications → Sound)
  and do not mute it.
- Android: mark the chat as a *priority conversation* if it must ring while Do
  Not Disturb is on.
- iOS: with the silent switch on it only vibrates; allow Telegram in the Focus
  modes they use.
- Xiaomi/Redmi/POCO, Oppo/Realme/OnePlus, Vivo, Honor, Huawei: enable Telegram
  autostart, set battery to "No restrictions" and lock the app in recents (see
  <https://dontkillmyapp.com>).
- If the agent has Telegram open on their computer, Telegram may not notify the
  phone meanwhile.

### 7. Browser alerts (optional)

While the agent has **/crm** or **/helpdesk** open — even with the tab in the
background — a tone plays when a WhatsApp message or an email from a customer
arrives on a document assigned to them. It can also show a browser notification;
clicking it opens the document. WhatsApp and email use different tones.

It fires on the same events as Telegram, and the two channels are independent:
they can be used together (browser on the desktop, Telegram on the phone).

**Setup**

1. `CORTEC Helpdesk Settings` → **Alertas en el navegador** (browser alerts) →
   ✅ Enable. Choose WhatsApp and/or email and the polling interval (10 s by
   default, 5 s minimum). Save.
2. `bench build --app cortec_helpdesk` (publishes the script under `/assets`).
3. Each agent reloads /crm or /helpdesk and clicks the **🔕** button once
   (bottom-right corner) → it turns into 🔔 and the browser asks for notification
   permission. The same button mutes and unmutes the sound.

**Limitations**

- Browsers require an interaction before playing sound: after reloading the page,
  any click anywhere is enough.
- Once a tab has been hidden for more than 5 minutes, Chrome throttles polling to
  once per minute, so the alert can take up to a minute.
- With /crm and /helpdesk both open, it sounds only once.
- On mobile it only sounds while the page is on screen; use Telegram for phones.

### 8. Raven alerts (optional)

A direct message from a Raven bot to the assigned agent, on the same events as
Telegram. **Raven runs on this same server**, so the alert never reaches an
external service: that is why it is the only channel that may include the message
content.

Advantage over Telegram: the recipient is the Frappe user itself, so there is no
Chat ID to link.

**Setup**

1. Install the Raven app on the bench and add the agents (they need an enabled
   **Raven User**).
2. In Raven, create a bot, for example "Alertas CORTEC".
3. `CORTEC Helpdesk Settings` → **Avisos por Raven** (Raven alerts) → ✅ Enable,
   pick the bot and save. Optionally turn on *Incluir el contenido del mensaje*
   (include the message content).
4. **Raven → Enviar prueba** (send test) to confirm the direct message arrives.

**Mobile limitation:** ringing with the screen locked requires Frappe push
notifications (*Push Notification Settings*), which on self-hosted sites depend on
a relay. If those are unavailable, Raven covers the desktop and **Telegram
remains the mobile channel**.

### Comparing the three channels

| | Raven | Telegram | Browser |
| --- | --- | --- | --- |
| Leaves the server | No | Yes | No |
| May include the message | Yes, optional | No | No |
| Per-agent linking | None | Chat ID | None |
| Desktop | Yes | With Telegram Desktop | Yes, with /crm or /helpdesk open |
| Phone with locked screen | Only with push (relay) | Yes | No |

### 9. Consent log

Mirrors the "Agreements" and the "User consent" screen of Bitrix24 web forms.

- **CORTEC User Agreement** — the text of each agreement. Changing the text bumps
  the version. The app uses two of them: `politica-privacidad` (mandatory) and
  `promociones` (optional); `bench migrate` creates them with placeholder text.
- **CORTEC Consent Record** — one record per event (Granted or Revoked) with
  date, channel, IP, URL, user agent, who recorded it and a copy of the accepted
  text. It is born submitted and cannot be modified, cancelled or deleted. A
  mistake is corrected with another event.

The `custom_acepta_promociones`, `custom_promociones_origen` and
`custom_consentimiento` fields on CRM Lead and Contact are **read-only**: they
show the latest event in the log for any of that record's email addresses. That
is why the Contact created when a Lead is converted inherits the consent without
copying anything.

Ways into the log:

| Source | Channel | How |
| --- | --- | --- |
| Website form | Web form | `leads-intake` Worker → `cortec_helpdesk.api.web_lead_intake` |
| Call, visit, email | Phone / In person / Email | **Consentimientos → Registrar consentimiento** button on the Lead or Contact (Desk) |
| Marketing list unsubscribe | Email unsubscribe | Email Group Member and Email Unsubscribe hooks, plus the daily reconciliation |
| Fields predating v1.0.12 | Historical | `migrate_consent_fields` patch |
| Bitrix24 | Bitrix24 | `cortec_bitrix24.api.import_webform_consents` |

The marketing list follows the **latest event** for each email address:

- A Revoked event unsubscribes the address.
- A Granted event subscribes it, unless it has a site-wide email unsubscribe.
- Someone previously unsubscribed is only reactivated by a **new** consent (web
  form, phone, in person or email). A historical one (Bitrix24, Historical) never
  overrides an unsubscribe.

The patch does not touch the list. The Bitrix24 import only does so with
`'subscribe': 1`.

During the Bitrix24 migration (`frappe.flags.in_bitrix24_migration`), agent
alerts and WhatsApp lead creation do not act on the historical data.

**Setup**

1. `bench migrate`.
2. **CORTEC User Agreement** → open `politica-privacidad` and `promociones` and
   paste the **exact** text the website form displays. Every site has its own:
   they are not fixtures.
3. Deploy the `leads-intake` Worker that calls `web_lead_intake`.

**Limitations**

- A Lead or Contact with consent records cannot be deleted: Frappe does not delete
  documents linked from submitted records. Erasure requests are served by
  anonymizing instead (section 10).
- The button lives on the Desk form (`/app/crm-lead/...`). The `/crm` interface
  shows the fields but does not have the button yet.

### 10. Erasure and revocation requests (Law 8968)

Each data subject request is handled in a **CORTEC Suppression Request**, used
only by the System Manager. The case file cannot be cancelled or deleted.

**Deadlines** (Decree 37554-JP). They are counted in business days, excluding
Saturdays, Sundays and the holidays in `CORTEC Helpdesk Settings → Feriados`
(holidays):

| Deadline | Article |
| --- | --- |
| Reply: 5 business days from the day after receipt | 18 |
| Request additional information: once only, within those 5 days. The clock pauses; if the subject does not reply within 5 business days, the request is treated as not filed | 19 |
| Confirm that processing has ceased, if asked: 3 business days | 9 |
| Inform the processors of the revocation: 5 business days | 8 |

A daily job notifies System Managers of requests coming due and marks as "Not
filed" those that were waiting for information that never arrived.

**Flow**

1. Create the request: type (Erasure or Revocation), date of receipt, notification
   channel (art. 17), how identity was verified (art. 15), and the subject's email
   addresses, phone numbers and names.
2. **Search data**. Lists everything found, whether each document's content is
   kept or redacted, and proposes B2B or B2C.
3. Pick the resolution and submit. It runs in the background.
4. Review the **report** and the **reply text**, send it to the subject and press
   **Marcar respuesta enviada** (mark reply sent), which clears the notification
   channel.

**Erasure resolutions**

| Resolution | For | Effect |
| --- | --- | --- |
| Unlink | B2B | Anonymizes the person and keeps the content of tickets, deals and emails without their identifiers. Removes them from the company (Law 8968, art. 6.1) |
| Erase | B2C | Anonymizes the person and redacts content and attachments. Documents belonging to a corporate customer keep their content without the identifiers |
| Keep — professional data | B2B | Does not anonymize; revokes marketing and produces the written refusal (art. 22). **Off** until *Permitir «Conservar — dato profesional»* is enabled in Settings (Decree art. 3, last paragraph: confirm with legal counsel) |

A **Revocation** only withdraws the consents and unsubscribes from the marketing
list; it does not anonymize.

**What erasure does**

- The subject's Leads and Contacts: data replaced with `Suprimido <hash>` and
  `suprimido-<hash>@suprimido.invalid`. Contacts are renamed (`rename_doc` updates
  every link).
- Emails, notes, comments, tasks, calls, WhatsApp messages and tickets: their
  email address, phone numbers and full names are replaced inside the text, or the
  content is redacted.
- Deleted: attachments of redacted content, Version, Activity Log, email queue,
  Deleted Document, list memberships and email unsubscribes.
- Portal users (Website User): anonymized and disabled.
- Consent log: a "Supresión" (erasure) Revoked event is recorded. The email
  address becomes its HMAC and the IP, URL and user agent are deleted. That keeps
  the proof of consent (Decree art. 6) without storing the address in the clear.
- The case file keeps only the HMACs of the identifiers.
- Every touched document is listed in **CORTEC Suppressed Document**, and
  cortec_bitrix24 never writes to it again.

The HMAC uses the site's `encryption_key`. **Without that key there is no way to
check whether an address was erased**: it must be part of your `site_config.json`
backups.

**Limits**

- Only names of two or more words are replaced. Nicknames, scanned signatures, ID
  numbers and data written some other way are not detected: review what was marked
  "keep" in "Search data".
- The report always lists the steps Frappe cannot perform: backups, IMAP
  mailboxes, alerts already sent through Telegram or Raven, Error Log and earlier
  exports.
- Internal Frappe users (agents) are handled manually.

## Ticket flow

```
1. Customer emails mail1@domain.com
2. Frappe creates the HD Ticket → auto_assign_ticket runs
3. The system resolves: email → Contact → Customer → account_manager
4. Ticket assigned to the agent automatically
5. Agent and supervisor are notified from mail1@
6. Helpdesk sends the acknowledgement to the customer from mail1@
7. Agent replies from the UI → the customer receives it from mail1@
8. If no agent is resolved → the supervisors (Agent Manager) get an alert
```

## Email routing

| Doctype                      | Account              |
| ---------------------------- | -------------------- |
| HD Ticket                    | mail1@domain.com     |
| CRM Lead, CRM Deal, Prospect | mail2@domain.com     |
| Quotation, Sales Order       | mail2@domain.com     |
| Sales Invoice, Payment Entry | no-reply@domain.com  |

Edit `DOCTYPE_EMAIL_MAP` in `communication.py` to add routes.

## Troubleshooting

### Check the email → agent chain

```python
import frappe
from cortec_helpdesk.overrides.hd_ticket import (
    _find_contact_by_email,
    _find_customer_for_contact,
)

email = "customer@company.com"
contact = _find_contact_by_email(email)
customer = _find_customer_for_contact(contact) if contact else None
manager = frappe.db.get_value("Customer", customer, "account_manager") if customer else None
print(f"{email} → {contact} → {customer} → {manager}")
```

### Bulk check of contacts

```python
import frappe
contacts = frappe.db.sql("""
    SELECT ce.email_id, dl.link_name AS customer, cust.account_manager
    FROM `tabContact` c
    JOIN `tabContact Email` ce ON ce.parent = c.name
    LEFT JOIN `tabDynamic Link` dl
        ON dl.parent = c.name AND dl.parenttype = 'Contact'
        AND dl.link_doctype = 'Customer'
    LEFT JOIN `tabCustomer` cust ON cust.name = dl.link_name
    ORDER BY ce.email_id
""", as_dict=True)

for c in contacts:
    if c.customer and c.account_manager:
        print(f"  OK  {c.email_id} → {c.customer} → {c.account_manager}")
    elif c.customer:
        print(f"  !!  {c.email_id} → {c.customer} → NO MANAGER")
    else:
        print(f"  XX  {c.email_id} → NO CUSTOMER")
```

### Check email routing

```python
from cortec_helpdesk.overrides.communication import (
    _get_email_account_name, DOCTYPE_EMAIL_MAP,
)
for dt, email in DOCTYPE_EMAIL_MAP.items():
    acc = _get_email_account_name(email)
    print(f"  {'OK' if acc else 'XX'}  {dt} → {email} → {acc}")
```

### Duplicate leads from WhatsApp

Lists the phone numbers with more than one CRM Lead, to review and merge by hand:

```python
import frappe
rows = frappe.db.sql("""
    SELECT RIGHT(REGEXP_REPLACE(mobile_no, '[^0-9]', ''), 8) AS tel,
           COUNT(*) AS n, GROUP_CONCAT(name) AS leads
    FROM `tabCRM Lead`
    WHERE IFNULL(mobile_no, '') != ''
    GROUP BY tel HAVING n > 1
    ORDER BY n DESC
""", as_dict=True)

for r in rows:
    print(f"{r.tel}  x{r.n}  {r.leads}")
```

Since v1.0.12 no new duplicates are created: a WhatsApp message from a number that
already has a Lead or Deal in progress is linked to the existing one.

### Merge the duplicates you already have

`cortec_helpdesk.merge_whatsapp_leads` keeps the **oldest** Lead for each number,
moves its history over (WhatsApp, emails, notes, tasks, calls, comments and
attachments) and marks the rest as **Junk**. It deletes nothing.

```bash
bench --site site.domain.com backup     # always, first
bench --site site.domain.com console
```

```python
from cortec_helpdesk.merge_whatsapp_leads import report, merge

report()                                  # list the duplicate groups
merge()                                   # dry run: changes nothing
merge(dry_run=False)                      # apply the changes
merge(phone="61591066", dry_run=False)    # a single number
```

Groups containing a Lead already converted to a Deal are skipped and reported:
there is a deal behind them, so they are reviewed by hand.

### Recent errors

```python
import frappe
frappe.get_all("Error Log",
    filters={"title": ["like", "%CORTEC%"]},
    fields=["title", "error", "creation"],
    order_by="creation desc", limit=10)
```

## Notes

- It does not modify existing Frappe, Helpdesk, CRM or ERPNext doctypes.
- Compatible with Frappe v15/v16 and Frappe Helpdesk v2.x.
- Notifications use `now=True` (immediate send). For high volume, switch to
  `now=False` to use the email queue.
