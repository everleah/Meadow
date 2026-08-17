# Benutzer, Gruppen & Rechte (RBAC + ABAC-Scopes)

- **Hauptdokument:** Abschnitt 9 ([Benutzer, Gruppen & Rechte](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 1, 7, 9
- **Stand:** Entwurf

## Grundprinzip

- Die Rechteprüfung erfolgt **ausschließlich im Rust-Kern** (autoritative Quelle). Das Frontend spiegelt Rechte nur wider (Ausblenden von Menüs/Aktionen), erzwingt aber nichts.
- Kombination aus **RBAC** (Rollen & Gruppen), **ABAC-Scopes** (welche Daten sichtbar/bearbeitbar sind) und optional später **ReBAC** (beziehungsbasierte Rechte über den SurrealDB-Graphen).
- Benutzer und Gruppen sind **technische Tabellen** und damit klar von den fachlichen `object`-Entitäten getrennt.
- Effektive Rechte = direkte Rolle + Berechtigungen aller Gruppen (transitiv) abzüglich expliziter Deny-Liste.

## Rollen

Vier fest eingebaute Rollen; `admin` ist eine Sonderform des `editor`:

| Rolle | Bedeutung | Besonderheit |
|---|---|---|
| `superadmin` | Darf alles, systemweit | Geschützt: nicht löschbar, mindestens einer existiert immer |
| `admin` | `editor` + Verwaltung (Nutzer, Gruppen, Definitionen, Jobs/DLQ) | Sonderform des Editors |
| `editor` | Pflege von Objekten, Referenzen, Kategorien, Statuswechsel | – |
| `viewer` | Nur lesen (Read-only) | – |

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
user.read / manage
group.read / manage
system.manage
```

**Business Rules (`business_rule.read` / `manage`):** `manage` (anlegen, bearbeiten, aktivieren/deaktivieren, Rule Lab/Dry-Run) ist nur für `admin`/`superadmin` freigeschaltet; `read` ist für alle Rollen sichtbar (Verhalten nachvollziehen). Jede Änderung und jeder Zustandswechsel ist im Audit-Log protokolliert (Abschnitt 8 / [business-rules.md](business-rules.md)).

Zuordnung (Auszug):

| Berechtigung | viewer | editor | admin | superadmin |
|---|---|---|---|---|
| `object.read` | ✓ | ✓ | ✓ | ✓ |
| `object.create` / `update` / `delete` / `publish` | – | ✓ | ✓ | ✓ |
| `reference.create` / `delete` | – | ✓ | ✓ | ✓ |
| `attribute_definition.manage` | – | – | ✓ | ✓ |
| `language_tree.manage` | – | – | ✓ | ✓ |
| `job.run` / `job.manage` | – | – | ✓ | ✓ |
| `user.manage` / `group.manage` | – | – | ✓ | ✓ |
| `audit.read` | – | – | ✓ | ✓ |
| `system.manage` | – | – | – | ✓ |

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
    { "reference": { "type": "category_member", "to": "category_webshop" } }
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

- JWT-Claims: `sub`, `username`, `roles[]`, `groups[]`, `perms[]`, `token_version`, `exp`.
- Kurzlebige Access-Tokens; `token_version` erlaubt sofortiges Sperren/Logout einzelner Benutzer.
- Neue Endpunkte: `GET /auth/me`, `POST|PATCH|DELETE /users`, `POST|PATCH|DELETE /groups`, `POST /groups/{id}/members`, `POST /groups/{id}/children`.
- **Service-Accounts:** Der Worker greift über API-Keys/Service-Accounts (Machine-to-Machine) auf den Kern zu, nicht über Personen-Logins.

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
