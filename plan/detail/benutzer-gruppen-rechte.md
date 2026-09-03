# Benutzer, Gruppen & Rechte (RBAC + ABAC-Scopes)

- **Hauptdokument:** Abschnitt 9 ([Benutzer, Gruppen & Rechte](../meadow-pim-architektur.md#9-benutzer-gruppen--rechte-rbac--abac-scopes))
- **Relevante Phasen:** Phase 1, 7, 9
- **Stand:** Entwurf

## Grundprinzip

- Die Rechteprüfung erfolgt **ausschließlich im Rust-Kern** (autoritative Quelle). Das Frontend spiegelt Rechte nur wider (Ausblenden von Menüs/Aktionen), erzwingt aber nichts.
- Kombination aus **RBAC** (Rollen & Gruppen), **ABAC-Scopes** (welche Daten sichtbar/bearbeitbar sind) und optional später **ReBAC** (beziehungsbasierte Rechte über den SurrealDB-Graphen).
- Benutzer und Gruppen sind **technische Tabellen** und damit klar von den fachlichen `object`-Entitäten getrennt.
- Effektive Rechte = direkte Rolle + Berechtigungen aller Gruppen (transitiv) abzüglich expliziter Deny-Liste.

## Rollen

Vier fest eingebaute Rollen; `admin` ist eine Sonderform des `editor`:

| Rolle        | Bedeutung                                                       | Besonderheit                                                  |
| ------------ | --------------------------------------------------------------- | ------------------------------------------------------------- |
| `superadmin` | Darf alles, systemweit                                          | Geschützt: nicht löschbar, mindestens einer existiert immer   |
| `admin`      | `editor` + Verwaltung (Nutzer, Gruppen, Definitionen, Jobs/DLQ) | Sonderform des Editors                                        |
| `editor`     | Pflege von Objekten, Referenzen, Kategorien, Statuswechsel      | –                                                             |
| `viewer`     | Nur lesen (Read-only)                                           | –                                                             |

## Berechtigungen

Rollen sind Bündel feingranularer Berechtigungen; Gruppen können weitere Berechtigungen ergänzen.

```text
object.read / create / update / delete / publish
reference.read / create / delete
attribute_definition.read / manage
business_rule.read / manage
attribute_group.manage
language_tree.read / manage
job.read / run / manage
audit.read
log.read / manage
user.read / manage
group.read / manage
system.manage
```

**Business Rules (`business_rule.read` / `manage`):** `manage` (anlegen, bearbeiten, aktivieren/deaktivieren, Rule Lab/Dry-Run) ist nur für `admin`/`superadmin` freigeschaltet; `read` ist für alle Rollen sichtbar (Verhalten nachvollziehen). Jede Änderung und jeder Zustandswechsel ist im Audit-Log protokolliert (Abschnitt 8 / [business-rules.md](business-rules.md)).

**System-Logs & -Einstellungen (`log.read` / `log.manage`):** Technische System-Logs (`log_entry`) und die persistierten Runtime-Einstellungen (`system_setting`, Abschnitt 12 / [betrieb-deployment.md](betrieb-deployment.md#11-monitoring--logging)) sind **nur** für `admin`/`superadmin` sichtbar bzw. änderbar (`log.read` = Lesen von System-Logs + Einstellungen, `log.manage` = Ändern der Logging-Einstellungen). `editor`/`viewer` sehen keine technischen Logs. Jede Änderung an `system_setting` wird im Audit-Log protokolliert.

Zuordnung (Auszug):

| Berechtigung                                      | viewer | editor | admin | superadmin |
| ------------------------------------------------- | ------ | ------ | ----- | ---------- |
| `object.read`                                     | ✓      | ✓      | ✓     | ✓          |
| `object.create` / `update` / `delete` / `publish` | –      | ✓      | ✓     | ✓          |
| `reference.create` / `delete`                     | –      | ✓      | ✓     | ✓          |
| `attribute_definition.manage`                     | –      | –      | ✓     | ✓          |
| `language_tree.manage`                            | –      | –      | ✓     | ✓          |
| `job.run` / `job.manage`                          | –      | –      | ✓     | ✓          |
| `user.manage` / `group.manage`                    | –      | –      | ✓     | ✓          |
| `audit.read`                                      | –      | –      | ✓     | ✓          |
| `log.read` / `log.manage`                         | –      | –      | ✓     | ✓          |
| `system.manage`                                   | –      | –      | –     | ✓          |

## Gruppen & Vererbung

- `user → group` (Mitgliedschaft) und `group → group` (Hierarchie) werden als **Graph-Relationen** modelliert (`member_of`, `child_of`).
- Effektive Rechte = Vereinigung aus direkter Rolle + allen Gruppen (transitiv, inkl. Eltern-Gruppen).
- Konflikte werden über eine explizite `deny_permissions`-Liste gelöst; ansonsten gilt „Allow vereinigt“.
- `superadmin` umgeht sämtliche Prüfungen.

## Daten-Scope (ABAC-light)

Berechtigungen regeln **Operationen**, Scopes regeln **Daten**:

```json
{
  "id": "scope_group_webshop",
  "subject": { "type": "group", "id": "group_webshop" },
  "rules": [
    { "field": "type", "operator": "in", "value": ["product"] },
    { "reference": { "type": "category_member", "out": "category_webshop" } }
  ]
}
```

Der Kern übersetzt diese Regeln beim Lesen in SurrealQL-`WHERE`-Bedingungen (serverseitige Filterung). Ohne Scope-Regeln gelten die Standardrechte der Rolle/Gruppe.

## Datenmodell

```json
// Tabelle user
{
  "id": "user_42",
  "username": "h.mueller",
  "email": "h.mueller@example.com",
  "password_hash": "$argon2...",
  "auth_provider": "local",        // local | oidc (SSO-Vorbereitung)
  "external_sub": null,            // Subject aus dem IdP bei SSO
  "roles": ["editor"],
  "status": "active",              // active | suspended
  "token_version": 3,              // Invalidierung bestehender JWTs
  "locale": "de",
  "created_at": "...",
  "updated_at": "..."
}

// Tabelle group
{
  "id": "group_webshop",
  "name": "Webshop-Team",
  "grant_permissions": ["object.publish"],
  "deny_permissions": [],
  "scope": { "rules": [] },
  "branding": { "theme": "dark", "accent_color": "#2f6f4f" }
}
```

- **Superadmin-Schutz:** Die API verweigert `DELETE` und den Rollenentzug, solange der betroffene Benutzer der letzte aktive `superadmin` ist.
- Passwörter werden mit **argon2** gehasht; Passwort-Reset ist optional.

## JWT & API

- JWT-Claims: `sub`, `username`, `roles[]`, `groups[]`, `perms[]`, `token_version`, `exp`. In Phase 1 trägt das Token `sub` (volle Record-ID, z. B. `user:admin`), `username`, `roles[]`, `token_version`, `exp`; `groups[]`/`perms[]` werden mit der Gruppenmitgliedschaft (Phase 7/9) ergänzt.
- Kurzlebige Access-Tokens; `token_version` erlaubt sofortiges Sperren/Logout einzelner Benutzer.
- **Dev-Bootstrap:** Beim ersten Start wird ein Standard-`superadmin` (`admin`/`admin`) angelegt, sofern `user:admin` nicht existiert; JWT-Config über `JWT_SECRET`/`JWT_TTL_SECONDS`.
- Neue Endpunkte: `GET /auth/me`, `POST|PATCH|DELETE /users`, `POST|PATCH|DELETE /groups`, `POST /groups/{id}/members`, `POST /groups/{id}/children`.
- **Service-Accounts:** Der Worker greift über API-Keys/Service-Accounts (Machine-to-Machine) auf den Kern zu, nicht über Personen-Logins.

## Impersonation („Login als Benutzer XY“, `superadmin`)

Zum Testen/Support kann ein `superadmin` vorübergehend **als ein anderer Benutzer** arbeiten (derselbe Daten-Scope, dessen Rollen/Gruppen). Es ist **kein Rechtetransfer**: Die Sitzung läuft mit den Rechten des Zielbenutzers, bleibt aber als Impersonation gekennzeichnet und wird auditiert („wer hat wann als wen agiert“).

- **Endpunkt:** `POST /auth/impersonate/{user_id}` (nur `superadmin`; sonst 403). Fremde (nicht eigene) Benutzer werden als `impersonator`-Claim getragen.
- **Verhalten:** Der Kern lädt den Zielbenutzer (muss `status == "active"` sein, sonst 404/400), baut ein reguläres JWT für diesen Benutzer (`sub`, `username`, `roles`, `token_version`, `exp`) und ergänzt einen **`impersonator`-Claim** `{ sub, username }` (der ausführende `superadmin`). Antwort `200 { token, token_type, impersonator: true }`.
- **Validierung:** Das Token bindet an das aktuelle `token_version` des Zielbenutzers (sofortige Invalidierung bei Sperre/Logout). Der `superadmin` darf sich nicht selbst impersonieren (kein `user_id == claims.sub`).
- **Audit:** `audit_log`-Eintrag `user_impersonated` (from = Superadmin, to = Zielbenutzer) + `log_entry` (Kategorie `auth`, Level INFO). Jede Impersonation ist nachvollziehbar.
- **Frontend:** Auf der Admin-Seite „Benutzer“ ein „Login als“-Button je Zeile (nur sichtbar für `superadmin`). Nach dem Aufruf wird das Ziel-Token als aktive Sitzung gespeichert (`app.storage.user`), zusätzlich `impersonator`; ein **Banner** „Angemeldet als `<user>` (Superadmin)“ mit „Zurück zur eigenen Sitzung“ (Logout → Login als eigener `superadmin`). `GET /auth/me` liefert den `impersonator`-Claim mit, damit die Oberfläche den Hinweis anzeigen kann.
- **Abgrenzung:** Kein Ersatz für echte Benutzer-Authentifizierung und kein sicherheitsrelevanter „Privilege Escalation“-Pfad — der `impersonator` ist ein reiner Audit-/Hinweis-Zusatz, die Rechte stammen vom Zielbenutzer.

## UI & Branding

- Sichtbarkeit von Menüs/Aktionen anhand der Berechtigungen aus `GET /auth/me`.
- `group.branding` (Theme, Farben, Logo) wird über die Gruppenhierarchie vererbt und von NiceGUI angewendet. **Kein Multi-Tenant** – reines UI-Styling ohne Auswirkung auf den Daten-Scope.
- Ein UI-Branding ist noch nicht formuliert; das Feld wird vorbereitet und bleibt zunächst leer.

## SSO (spätere Version)

- Ziel: **OIDC** (OpenID Connect); SAML optional für Enterprise-Anbindung.
- Vorbereitung jetzt: `auth_provider`/`external_sub`, abstrakter Token-Validator (eigene JWTs **oder** IdP-JWTs via JWKS) sowie Mapping von IdP-Gruppen auf interne Gruppen.
- Meadow bleibt für die **Autorisierung** zuständig; die **Identität** kommt vom IdP.

## Audit & Sicherheitshinweise

- Änderungen an Benutzern, Gruppen, Rollen und Scopes werden im `audit_log` protokolliert.
- Die Rechteprüfung erfolgt zentral im Kern vor jeder Operation – niemals nur im Frontend.
- Benutzer-/Gruppendaten sind technische Tabellen und werden nicht über das generische `object`-Modell exponiert.
