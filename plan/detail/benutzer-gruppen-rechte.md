# Benutzer, Gruppen & Rechte (RBAC + ABAC-Scopes)

- **Hauptdokument:** Abschnitt 9 ([Benutzer, Gruppen & Rechte](../meadow-pim-architektur.md#9-benutzer-gruppen--rechte-rbac--abac-scopes))
- **Relevante Phasen:** Phase 1, 7, 9
- **Stand:** A1–A6 umgesetzt (Rechte-Katalog, Mitgliedschaft als Graph, effektive Menge in den Claims + `auth_version`, Schreibpfad-Prüfung, Scope-Compiler auf den Lesepfaden, Performanz-Zusagen als Tests + Abnahmeskript; 2026-09-25); Stufe B (Durchsetzung) **umgesetzt** — alle 22 Module laufen über den Rechte-Katalog, die Rollen-Helfer sind entfernt (2026-09-26); C (UI) und D (KI) offen

## Umsetzungsstand

Stufenplan (Nutzerentscheidung 2026-09-25): **A** Kern-Vertrag → **B** Durchsetzung → **C** Oberfläche → **D** KI-Unterstützung (optional). Jede Stufe hält den laufenden Betrieb grün.

- **A1 — Rechte-Katalog (umgesetzt 2026-09-25).** `crates/pim-core/src/permissions.rs` ist die eine Quelle der Wahrheit über die **26** Berechtigungen (Bereich, Aktion, Rollen-Default, Label-Schlüssel, englische Beschreibung); `GET /permissions` liefert daraus das Vertragsdokument (Bereiche in Reihenfolge, vier Rollen, je Recht die **expandierten** Rollen, damit die Matrix „geerbt" ohne Kenntnis der Hierarchie zeichnen kann). Bis dahin waren die Rechte **Prosa** und `grant_permissions`/`deny_permissions` freie Zeichenkettenfelder, die kein Schreibpfad prüfte und kein Leser auswertete.
- **Bitnummern sind append-only.** Jedes Recht hat eine feste `index`-Position im `PermissionSet` (`[u64; WORDS]`, `Copy`), und die Bitmenge reist im JWT. Ein **entzogenes** Recht behält seinen Platz, eine neue Berechtigung bekommt den **nächsten** — eine Umsortierung gäbe einem alten Token stillschweigend eine andere Bedeutung. Die Prüfung ist deshalb ein **Bit-Test ohne Allokation**; `can()` auf einen unbekannten Schlüssel ist `false` (fail-closed), gemeldet werden unbekannte Schlüssel nur auf dem **Schreibpfad**.
- **Rollen-Defaults aus `min_role`.** Jede Berechtigung gehört der **niedrigsten** Rolle, die sie hält; jeder höhere Rang erbt sie (`admin` ist eine Sonderform des `editor`). Das ist genau die Hierarchie, die der Kern vorher mit `is_admin()`/`is_editor()` erzwungen hat — Stufe B ersetzt diese Rollen-Checks durch `can(...)`, und diese Zuordnung ist es, die das Verhalten **ohne** Gruppen unverändert hält (jede geänderte Stelle wird dagegen geprüft).
- **A2 — Mitgliedschaft als Graph (umgesetzt 2026-09-25).** `member_of` (`in` = Nutzer, `out` = Gruppe) und `child_of` (`in` = Kindgruppe, `out` = Elterngruppe) sind SurrealDB-**RELATION**-Tabellen mit **expliziten Indizes in beide Richtungen** (`idx_member_of_in`/`_out`, `idx_child_of_in`/`_out`) — der Login-Pfad läuft die Kanten einmal je Sitzung entlang, je Ebene eine Abfrage ab einem Index, gefiltert mit Record-List-Literalen (nicht `array::map`, siehe R8). Geschrieben wird über `POST /groups/{id}/members`, `DELETE /groups/{id}/members/{user_id}`, `POST /groups/{id}/children`, `DELETE /groups/{id}/children/{child_id}`: **idempotent** (eine vorhandene Kante antwortet `200 {created: false}`, eine fehlende `204`), **zyklusgeprüft** (`400`, ein Ring machte „wer erbt von wem" unbeantwortbar), **auditiert** (`group_member_added`/`_removed`, `group_child_added`/`_removed`). `GET /memberships` liefert beide Kantenlisten in **einem** Aufruf, damit der Gruppen-Dialog keine N+1-Abfragen braucht.
- **A3 — Die effektive Menge reist im Token (umgesetzt 2026-09-25).** `Claims` trägt jetzt `groups`, `perms` (die Rechtbits) und `auth_version`; die Auflösung passiert **einmal beim Login** (`rbac::resolve`), `/auth/me` meldet Gruppen und Rechte aus den Claims (ohne Abfrage), und die Prüfung ist ein Bit-Test. Sichtbar wird das erst mit Stufe B — die Durchsetzung hängt noch an Rollen, aber die Daten liegen bereit.
- **A4 — Der Schreibpfad prüft gegen den Katalog (umgesetzt 2026-09-25).** `POST`/`PATCH /groups` lehnen unbekannte Schlüssel in `grant_permissions`/`deny_permissions` mit **`400`** ab („unknown permission(s): … — the catalog is at GET /permissions"). Der Lesepfad ignoriert einen unbekannten Schlüssel (fail-closed), ein gespeicherter Tippfehler wäre also ein Recht, das **gesetzt aussieht und nichts tut** — genau deshalb prüft der Schreibpfad, wie bei Konnektor- und Exportprofil-Parametern. Die Prüfung läuft **vor** dem Zähler-Sprung: eine abgelehnte Liste meldet niemanden ab. Gruppenänderungen werden auditiert (`group_created`/`_updated`/`_deleted`, beim Update die **geänderten Felder** statt einer zweiten Kopie der Werte).
- **A5 — Der Scope greift auf den Lesepfaden (umgesetzt 2026-09-25).** `crate::scope` liest und prüft `group.scope`, kompiliert die Regeln zu **einer** Bedingung und hängt sie in dieselbe `WHERE`-Kette wie alle anderen Filter (Details und Messwerte im Abschnitt „Daten-Scope"); die Gruppendokumente sind auf `auth_version` gecacht, die aufgelösten Schlüsselmengen auf `stats_version`. Live geprüft: eine Gruppe mit Scope „Kategorie X" (455 der 2000 Produkte) liefert für die zugeordnete Person **455** Objekte, die Seitenschlüssel sind **id-gleich** mit dem expliziten Kategoriefilter, die Spaltenprobe zählt 455, und unverändert bleibt der unbeschränkte Nutzer bei 2000. Die drei Sicht-Endpunkte verlangen jetzt eine Identität (vorher antworteten sie **ohne Token** mit 200); der Worker kommt weiterhin über den API-Key durch. **Der Superadmin ist von der Beschränkung ausgenommen** (siehe „Daten-Scope").

### Performanz-Regeln (verbindlich)

Ein Rechtesystem kann den Lesepfad erheblich verteuern. Diese Regeln halten die Kosten im notwendigen Maß; sie sind Teil der Definition of Done jeder Stufe.

1. **Kein Datenbankzugriff pro Anfrage.** Beim Login wird die effektive Menge **einmal** aufgelöst (Rollen-Defaults + Gruppen-Grants − Denies) und reist in den Claims; `can()` bleibt ein Speicher-Test wie `is_admin()` heute. Der Ist-Zustand ist die Messlatte: die Authentifizierung kostet heute **null** Zugriffe (nur Signaturprüfung).
2. **Der Scope wird zur `WHERE`-Bedingung, nie zum Nachfilter** — und fließt in **dieselbe** Bedingung wie `count` und die Seite (sonst lügt der Pager: „1–200 von 9633", während die nächste Seite etwas anderes zeigt).
3. **Für unbeschränkte Nutzer entsteht kein Clause.** Der Compiler gibt `None` zurück; die Abfrage bleibt byte-identisch zum heutigen Zustand. Das ist die messbare Form von „nur im notwendigen Maß".
4. **Clause einmal kompilieren, versionsgebunden cachen.** Schlüssel `(Scope-Identität, stats_version)`; die Übersetzung passiert bei einer Änderung, nicht pro Anfrage und nicht pro Zeile. **Jeder Cache, der aus einer gefilterten Sicht entsteht, trägt die Scope-Identität im Schlüssel** (`cached_view_keys(filter, order)` heute nicht — ohne Scope im Schlüssel bekäme ein Nutzer die Schlüsselliste eines anderen).
5. **Mitgliedschaft einmal je Sitzung** über die indizierte Graph-Kante (`user -> member_of -> group`, transitiv über `child_of`), mit expliziten Indizes nach der `reference`-Konvention; nicht pro Anfrage.
6. **`deny` wird beim Auflösen verrechnet**, nicht bei jeder Prüfung.
7. **Fail-fast vor jedem DB-Zugriff**, und **Stapel prüfen einmal**: ein Bulk-Lauf, ein Import, ein Export kosten einen Rechtecheck, nicht einen je Objekt; das Audit bekommt einen Eintrag pro Lauf.
8. **Übersetzungsform per Messung entscheiden.** Bekannte SurrealDB-Zahlen als Ausgangspunkt: `id NOT IN (SELECT …)` mit `SELECT *` **159 s**, Record-Literal **1,4 s**, Subquery mit `SELECT id` **0,36 s**, `IN`-Liste pro gescannter Zeile geprüft (1000 Ids ≈ 2,6 s). Deshalb: Record-Literale aus gecachten Schlüsselmengen oder eine `id`-projizierende Subquery — welche günstiger ist, wird je Scope-Art am Bestand gemessen. **Ergebnis für den Scope (A5):** die Subquery gegen die Relationskante kostet **28 918 ms** gegen **896 ms** für die Literal-Schlüsselliste (ungefiltert 33 ms) — deshalb Literal, siehe „Daten-Scope".

### Was die Gates prüfen — und was bewusst nicht

Die Performanz-Zusagen sind dort verankert, wo sie **ohne laufende Datenbank** prüfbar sind (`scripts/check.*` läuft bei gestopptem Kern):

1. **Rein und getestet** (in `cargo test`, also im Gate): `scope::needs_scope` (Worker und Nutzer ohne Gruppen brauchen keinen Scope ⇒ kein Zugriff), `scope::restricts` (eine Gruppe **ohne** Regeln macht unbeschränkt), `scope::apply(None, …)` (ändert **nichts** an Conditions/Binds — das ist die Byte-Identität der unbeschränkten Abfrage, ausgedrückt an der Stelle, die den SQL-Text bildet) sowie UND je Gruppe / ODER über Gruppen / `id IN []` als Bedingung. `objects::build_object_filter` ist dagegen **nicht** unit-testbar: es hängt an `AppState` (echte DB-Verbindung), und ein Test-Doppel dafür gäbe es nur um des Tests willen.
2. **Bewusst nicht im Gate: der Zähl-Test „keine zusätzliche Abfrage".** Er bräuchte eine Datenbank und würde eine Messung in den Schnellauf bringen; die Abwesenheit von Zugriffen folgt hier strukturell aus zwei frühen Rücksprüngen **vor** jedem DB-Zugriff (in `needs_scope`/`restricts`), beide oben getestet.
3. **Manuell, weil sie einen laufenden Kern und Daten brauchen** (wie `load_test.py` und der Deslop-Scan): `scripts/scope_bench.py` — stellt die beiden Zusagen am echten Bestand fest. Es **prüft**, statt nur zu drucken (Exit ≠ 0 bei einem Fehlschlag): der Scope hat Mitglieder, er schränkt ein, er ist nicht weiter als die Kategorien-Ansicht, und ein gezeigtes Objekt trägt **nachweislich** die Kante zum Ziel (Rückwärtssuche über `GET /objects/{id}/references`). Optional `--max-overhead-ms` als Budget. Der erste Check („hat Mitglieder") ist der wichtigste: ohne ihn liefen die übrigen auf einer **leeren** Sicht grün durch — genau das passierte beim ersten Lauf dieses Skripts, weil das automatisch gewählte Taxon ein Wurzelknoten war, dessen Produkte nur am Unterbaum hängen (`?category=` schließt Nachfahren ein). Seitdem wird das Ziel aus einer **echten Kante** gelesen.
4. **Gemessen (2026-09-25, Dev-Daten, 3 Zeitläufe je Endpunkt):** Scope mit 69 Mitgliedern +5 ms (Count) / −32 ms (Seite) / −126 ms (Spalten) gegen ungefiltert; Scope mit 455 Mitgliedern +93 ms / +97 ms / −101 ms. Ein **enges** Scope ist also *schneller* als gar keiner (weniger Kandidaten zu lesen, sortieren, rendern), ein weites kostet den Literal-Vergleich zusätzlich.

### Entscheidung: Rechteänderung bricht die laufenden Sitzungen ab (2026-09-25)

Ein globaler Zähler `auth_version` (dasselbe Muster wie `stats_version`) steigt bei **jeder** Änderung an Rechten, Gruppen, Mitgliedschaften und Passwörtern; das Token trägt den Stand beim Ausstellen, ein abweichender Wert ergibt `401` und die Anmeldeseite. Damit wirkt ein **entzogenes** Recht sofort (fail-closed) statt erst nach der Tokenlaufzeit, und die Prüfung kostet weiterhin **keinen** Zugriff.

- Preis: eine Rechteänderung meldet die **laufenden** Sitzungen ab (nicht die Person selbst — `POST /auth/password` antwortet mit einem frischen Token). Bei einer KMU-Instanz mit wenigen Admins ist das vertretbar; die Alternative wäre ein bis zu einstündiges Fenster, in dem ein entzogenes Recht noch wirkt.
- **Der Zähler liegt in der Datenbank** (`access_state:state`), nicht im Prozess: ein rein speicherbasierter Zähler würde bei jedem Deployment wieder bei seinem Startwert beginnen und damit **alle** Sitzungen abmelden. Der Kern liest ihn beim Start und spiegelt ihn in den Cache — der **Anfragepfad** liest nur den Cache, kein SQL.
- **Nicht jede Schreiboperation zählt als Änderung:** das Anlegen eines **Nutzers** erhöht den Zähler nicht (für einen noch nicht existierenden Nutzer kann es keine Sitzung geben), und eine Löschung, die **keine** Kante findet, ebenso wenig (`204`, aber niemand wird abgemeldet). Alles andere — Rechte, Gruppen, Mitgliedschaften, Hierarchie, Rollen, Status, Passwort, Nutzerlöschung — erhöht ihn, jeweils **vor** dem Schreibvorgang (schlägt der Schreibvorgang danach fehl, kostet das nur eine Anmeldung; die umgekehrte Reihenfolge ließe ein entzogenes Recht in einem ausgestellten Token am Leben).
- **Nebenwirkung, die die Oberfläche betrifft:** der **Ändernde selbst** verliert seine Sitzung mit seiner eigenen Änderung. Der Passwortwechsel löst das bereits (die Antwort enthält ein frisches Token, das die Oberfläche übernimmt); für die Nutzer-/Gruppen-Editoren ist das der saubere Weg (Stufe C) — bis dahin führt die nächste Anfrage auf die Anmeldeseite.
- Nebeneffekt: die bisherige Lücke „`token_version` wird beim Request nie geprüft" ist damit geschlossen — schärfer als vorher, weil auch Passwortwechsel sofort greifen.
- **Geschlossen (Stufe B, 2026-09-26):** jeder Endpunkt läuft durch `auth::require_permission` beziehungsweise `Claims::require` — Recht **und** Sitzungsversion werden geprüft, also wird auch ein auf dem API-Key-Weg beendetes Token dort abgewiesen. Der Rollen-Helfer `auth::api_key_or_role` (und mit ihm `is_admin`/`is_editor`) ist entfernt; `has_role` bleibt für die Impersonation-Ausnahme und den Scope.

- **A6 — Die Performanz-Zusagen sind geprüft, nicht behauptet (umgesetzt 2026-09-25).** Die drei frühen Entscheidungen des Scopes sind reine, im Gate getestete Funktionen (`needs_scope`, `restricts`, `apply` — siehe „Was die Gates prüfen\"); die Abnahme am echten Bestand macht `scripts/scope_bench.py`.
  - **Stand der Lesepfade (2026-09-26):** `/objects`, `/objects/count` und `/objects/attribute-usage` verlangen Identität **und** `object.read`; Einzelobjekt, `/objects/stats`, `/objects/composition-types`, Picker, Baum und `/search` verlangen weiterhin nur ein Token (bzw. gar keins). Der Scope greift auf den Objekt-Lesepfaden, aber noch nicht auf den Schreibpfaden.

### Stufe B — Durchsetzung (umgesetzt 2026-09-26)
**Bestand:** ~130 Rollenprüfungen in 22 Modulen, in vier Formen: **66×** `claims.is_admin()`, **19×** `claims.is_editor()`, **38×** `api_key_or_role(…, ROLE_*)` (33× `editor`, 7× `admin`, 5× `viewer`), dazu **4×** die optionale Form `.is_some_and(|claims| claims.is_admin())` und **1×** `has_role(SUPERADMIN)`.

**Entscheidungen (2026-09-26):**

- **`job.run` bleibt `editor`.** Der Code ließ jeden Editor einen Lauf starten, die Plan-Tabelle sagte `admin`; ein Lauf ist eine Datenoperation am Katalog (Export, Import, Bulk, Neuberechnung), keine Verwaltung — der Katalog wurde angepasst, nicht das Verhalten.
- **Editor-gebundene Metadaten-Lesepfade werden Katalog-Leserechte (viewer+):** Enum-Werte-Nutzung, Merkmalsgruppen, Kategorie-Merkmale, Kanäle, Enum-Sets/-Optionen, Definitions- und Jobläufe, Objekttyp, Relationen-Definitionen, Kompositionsbaum sowie Suchen/Collections **lesen**. Sie sind Anzeige-Metadaten, die ein Viewer öffnen darf (Produktseite, Baum), und Lesen ist keine Änderung. Die Reads der **Export-Profile** bleiben editor+ (`job.run`), weil sie den Export vorbereiten.
- **Ein fehlendes Recht wird benannt:** die Antwort ist ``missing permission `object.create` `` statt „requires editor role" — Stufe C zeigt dieselben Schlüssel in der Matrix.
- **`GET /config/export` und `POST /config/import` teilen ein Recht** (`integration.manage`): beide Richtungen desselben Konfigurationsdokuments, und ein Restore liest und schreibt genau dieses Dokument.
- **`GET /permissions` verlangt `group.read`** (vorher: jede angemeldete Sitzung): der Katalog ist die Eingabe des Gruppen-Editors; die **eigenen** Rechte einer Sitzung stehen weiterhin in `/auth/me`.
- **Ein handgeschriebenes Duplikat weniger:** `PATCH /objects/{id}/computed` trug eine zweite Fassung von „API-Key oder Admin"; sie ist durch die eine Prüfung ersetzt (`object.update`).

**Vorgehen (in dieser Reihenfolge, jeder Schritt lauffähig):**

1. **Infrastruktur (umgesetzt 2026-09-26):** eine Prüffunktion, die beides kann — API-Key (Maschine, alle Rechte, **keine** Sitzungsversion nötig) oder Token mit einem benannten Recht **plus** Versionsprüfung (`auth::require_permission`; `require_caller` liefert zusätzlich die Identität für den Scope). Sie ersetzt `api_key_or_role` Stelle für Stelle und schließt damit die oben genannte Lücke. Dazu `Claims::require(key)` für die reine Token-Form — der `AuthUser`-Extractor prüft die Version bereits selbst.
2. **Zuordnungstabelle prüfen, dann umstellen** — **alle 22 Module umgesetzt (2026-09-26)**, in der Reihenfolge `objects` → `references` → `attribute_definitions` → `config`/`language_tree` → `jobs` → `rules` → `users`/`groups`/`system`. Die Zuordnung folgt dem Katalog (`min_role`), und die 227 Kern-Tests sind das Netz: **das Verhalten von Nutzern ohne Gruppen darf sich nicht ändern**, jede Abweichung wäre ein Fund. Am laufenden Kern gemessen: 20 Seiten der Oberfläche laden unverändert, ein Viewer liest (`200`) und wird bei **jedem** Schreibpfad mit `403 missing permission …` abgewiesen (64 Sonden), Admin und Maschinen-Schlüssel kommen durch, `scripts/scope_bench.py` bleibt grün (69 von 2000, +49 ms).
3. **Scope nachziehen:** Einzelobjekt-Lesen (404 statt Fremddaten), `/objects/stats`, Picker, Baum, `/search` und die Schreibpfade (ein Objekt außerhalb des Scopes ist nicht lesbar **und** nicht änderbar).
4. **Sichtbarkeit in der Oberfläche** folgt in Stufe C aus `GET /auth/me` (Rechte je Sitzung), damit Menüs und Aktionen dieselbe Quelle nutzen wie der Kern.

**Bewusste Ausnahmen (bleiben Rollenprüfungen, mit Begründung im Code):** die **Impersonation** braucht `superadmin` — sie ist eine Frage der *Identität*, nicht der Daten, und der Katalog kennt dafür bewusst kein Recht; `POST /auth/password` bleibt für jede angemeldete Rolle offen (Selbstbedienung); der Worker-Pfad bleibt `system` (Maschine).

### Stufe C — Oberfläche (offen)

- **C1 — Rechte-Matrix:** Bereiche × Aktionen aus `GET /permissions` mit **drei Zuständen** je Zelle (von der Rolle geerbt ✓ · zusätzlich gewährt + · ausdrücklich verboten ✗), statt zwei JSON-Feldern. Kein Tippen von Schlüsseln; `deny` wird sichtbar.
- **C2 — Datenkarte statt JSON-Textarea:** Objekttypen, Kategorien (bestehender Baum-Picker), Kanäle, Collections als Bausteine; **Satz-Vorschau in Worten** und ein **Zähler** über `/objects/count` mit dem Scope („sichtbar: 812 von 9633 Objekten\").
- **C3 — Gruppe bearbeiten:** Mitglieder-Tab (Nutzer-Picker) mit **zwei Hinweisen**: bei einem **Superadmin** ist die Mitgliedschaft wirkungslos, und eine Gruppe mit Scope, die einen **`admin`** enthält, **schränkt diesen ein** (Nutzerentscheidung 2026-09-26: nur der Superadmin hat Sonderstellungen). Die Hierarchie (`child_of`) als Eltern-Auswahl, Audit-Einträge sind vorhanden (A2/A4).
- **C4 — Effektiv-Panel:** „Peter: editor + Gruppe webshop ⇒ darf X, weil Rolle / weil Gruppe / verboten durch Gruppe\" — beantwortet „warum darf der das?\" ohne SQL.
- **C5 — Texte:** Label-Schlüssel der Bereiche und Rechte (`perm.*`) in `de`/`en`/`fr`; Handbuchkapitel; der **Fehlschlag des Ändernden** (seine eigene Sitzung endet) wird über das frische-Token-Muster des Passwortwechsels abgefangen.

### Stufe D — KI-Unterstützung (optional, offen)

Area `group_permissions` im vorhandenen KI-Vertrag (`plan/detail/ki-assistenz.md`): Vorschlag für Rechte und Scope landet im Formular, wird gegen den Katalog bzw. `scope::parse`/`validate` geprüft und **nie** automatisch gespeichert. Der Prompt enthält Schema und Metadaten, keine Objektwerte.

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

Berechtigungen regeln **Operationen**, Scopes regeln **Daten**. Der Scope hängt an der **Gruppe** (`group.scope`); die Gruppe ist damit ihr eigenes Subjekt, es gibt keine Hülle mit eigener `id`. Umgesetzt in `crates/pim-core/src/scope.rs` (A5, 2026-09-25):

```json
{
  "rules": [
    { "field": "type", "operator": "in", "value": ["product"] },
    { "reference": { "type": "rel_category_member", "out": "object:webshop" } }
  ]
}
```

- **Zwei Regelformen.** `field` filtert auf ein Feld des Objekts (`type`, `status` — der Katalog steht in `scope::FIELDS`, bewusst kurz: ein Scope, der jedes Attribut nennen dürfte, wäre ein Query-Baukasten in einem Formularfeld), `reference` verlangt eine **Kante** in einem Relationstyp zu einem der genannten Ziele. `reference.out` darf eine Id **oder eine Liste** sein (mehrere Ziele = „eines davon").
- **Gespeichert wird der stabile Relations-Schlüssel** (`rel_category_member`), nicht der umbenennbare Name; die Oberfläche zeigt den Namen und speichert den Schlüssel (dieselbe Regel wie bei Kanälen und `definition_id`).
- **Semantik:** Regeln **innerhalb** einer Gruppe sind **UND**-verknüpft („Typ ist Produkt *und* in einer dieser Kategorien"), Gruppen **mit** Regeln werden **ODER**-verknüpft („was irgendeine meiner Gruppen erlaubt"), und eine Gruppe **ohne** Regeln schränkt **nichts** ein — die Standardrechte der Rolle/Gruppe, wie bisher.
- **Der Clause geht in dieselbe `WHERE`-Kette wie alle anderen Filter** (`api::objects::ObjectFilter`), also gilt er für Seite, Gesamtzahl und Spaltenprobe gleichzeitig. Kein Nachfiltern in Rust, kein Filtern nach der Seitenscheibe — sonst loge der Pager und die Zahl zählte Unsichtbares. Er ist damit automatisch Teil der View-Cache-Schlüssel (`ObjectFilter::view_key`): eine beschränkte Sicht kann nie die Zeilen einer unbeschränkten wiederverwenden.
- **Unbeschränkt = kein Clause.** Ohne Gruppen (oder bei einer Gruppe ohne Regeln, oder für den Worker mit API-Key) liefert der Compiler `None` und es wird **keine** Bedingung angehängt: die Abfrage ist byte-identisch zu vorher. Das ist der Normalfall und der Grund, warum das System nichts kostet, wo es niemand nutzt.
- **Schreibpfad prüft, Lesepfad ist fail-closed.** `POST`/`PATCH /groups` lehnen einen Scope ab, den der Kern nicht nutzen kann (`400`: unbekanntes Feld, unbekannter Operator, unbekannte Relation, unbekanntes Ziel, Regel mit beiden Formen). Ein **gespeicherter** Scope, der nicht mehr parst (alte Datei, Handedit), wird beim Lesen als „keine Regeln" gelesen — er darf niemanden aus dem ganzen Katalog aussperren —, und eine Regel, deren Auflösung **leer** ist, bleibt eine Bedingung (`id IN []`): eine Regel fallen zu lassen würde die Sicht auf alles erweitern, also genau das Gegenteil.
- **Die Form des Clauses ist gemessen, nicht geraten** (2026-09-25, Dev-Daten: 16 926 Produkte, größte Demo-Kategorie mit 455 Mitgliedern): die naheliegende Subquery `id IN (SELECT VALUE in FROM reference WHERE …)` kostet im Count **28 918 ms**, dieselbe Regel mit gebundener Zielliste 29 911 ms, das Materialisieren der Schlüssel 29 ms und `id IN [<455 Literal-Keys>]` **896 ms** gegen 33 ms ohne Scope. Deshalb: Schlüssel in Rust holen, als **Record-Literal** einsetzen (dieselbe Lehre wie bei `?category`/`?released_for`, siehe `api::keys::record_list_literal`) und die Schlüsselliste datenversionsgebunden cachen. End-to-End gemessen kostet der Scope damit **~90 ms** beim Zählen und **~100–150 ms** bei einer Seite; die Spaltenabfrage bleibt unverändert.
- **Der Superadmin wird nie beschränkt** — auch nicht, wenn er in einer Gruppe mit Regeln ist. Der Plan sagt, die Rolle umgehe *sämtliche* Prüfungen, und ein Scope ist eine Prüfung auf Daten. **Gemessen (2026-09-25), bevor die Regel eingebaut war:** der Seed-Superadmin in einer Gruppe mit Scope sah **69 von 2000** Produkten, während sein Rechtesatz vollständig blieb (26/26 im Token) — eine Rolle, der man weder ein Recht entziehen noch eines erlassen kann, darf auch nicht die Sicht auf ihren Katalog verlieren. Eine **Mitgliedschaft bleibt möglich und ist dann wirkungslos** (sie zu verbieten hieße, bei jeder Beförderung zum Superadmin Mitgliedschaften aufzuräumen); `/auth/me` nennt die Gruppen weiterhin, und der Gruppen-Editor darf auf den Widerspruch hinweisen. **Ein `admin` wird dagegen sehr wohl beschränkt** — der Plan nimmt nur den Superadmin aus, eine Abteilung mit eigenem Admin ist damit ausdrückbar; die Oberfläche sollte das sagen, weil es eine mächtige Rolle einschränkt.
- **Entscheidung (Nutzer, 2026-09-26): Nur der Superadmin hat diese Sonderstellung.** Ein `admin` wird durch einen Scope beschränkt, und er wird durch Gruppen-Grants nicht über die Rechte seiner Rolle hinaus befüllt — die Rollen-Zuordnung im Katalog bleibt für ihn maßgeblich. Der Code hält das an zwei Stellen fest: als Test (`scope::needs_scope` lässt `admin` bewusst zu, `superadmin` bewusst nicht) und in `rbac::effective` (Superadmin ⇒ `all()`, sonst Rollen-Defaults + Grants − Denies). Wer das ändern will, muss diesen Beschluss öffnen.
- **Die Lesepfade brauchen eine Identität.** `/objects`, `/objects/count` und `/objects/attribute-usage` antworteten bis hierher **ohne Token** (200) — ein Scope, der nur greift, wenn zufällig ein Token mitkommt, wäre umgehbar. Sie nehmen jetzt Bearer-Token **oder** den Worker-API-Key (`auth::require_caller`) **und** verlangen `object.read`; der Worker bleibt unbeschränkt. **Noch offen:** die übrigen Lesepfade (Einzelobjekt, `/objects/stats`, Picker, Baum, `/search`) sind weiterhin offen und werden dort zusammen mit den Rechteprüfungen geschlossen.

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
- Passwörter werden mit **argon2** gehasht.

## Passwort-Verwaltung (implementiert, Phase 9)

- **Richtlinie:** `MIN_PASSWORD_LENGTH` = **8 Zeichen** (Zeichen, nicht Bytes) und das neue Passwort muss sich vom aktuellen unterscheiden (`auth::validate_new_password`); gilt für Selbst-Änderung und Admin-Pfade.
- **Selbst-Änderung — `POST /auth/password`** (jede authentifizierte Rolle): Body `{current_password, new_password}`. Falsches aktuelles Passwort → **400**, Richtlinienverstoß → **400**, in einer **Impersonations-Sitzung** → **403**. Erfolg: neuer Hash, `password_changed_at = time::now()`, `must_change_password = false`, `token_version + 1`, Audit `password_changed` und die Antwort enthält ein **frisches JWT** (`200 {token, token_type}`), damit die laufende Sitzung nahtlos weiterläuft.
- **Kennzeichen `must_change_password`** steuert den UI-Hinweis (Banner „Sie haben noch kein eigenes Passwort gesetzt“): **fehlendes Feld gilt als `true`** — deshalb mahnt der Seed-`admin`/`admin` weiterhin an, ebenso vom Admin erstellte Nutzer mit aktivierter Checkbox. Der Admin kann es beim Anlegen/Ändern über die Checkbox **„Neues Passwort erforderlich“** setzen (`POST /users`-Feld, Default `true`) bzw. unabhängig vom Passwort wieder löschen/setzen (`PATCH /users/{id}` mit `must_change_password`); so lässt sich das Banner z. B. für **Integrationskonten oder bewusst übergebenen Passwörter** abschalten. Die Nutzerliste zeigt den Zustand als Badge (`new`/`ok`).
- **Admin-Reset — `PATCH /users/{id}` mit `password`** (`admin`/`superadmin`): setzt einen neuen Hash, **entfernt** `password_changed_at` (`UPDATE … UNSET` — ein `MERGE` kann kein `null` in ein `option<datetime>` schreiben → sonst 500), setzt `must_change_password` (Default `true`, ein expliziter Wert im Request gewinnt) und erhöht `token_version`; Audit `user_password_reset`. `POST /users` validiert das Startpasswort ebenfalls.
- **UI:** Der Kopfbereich jeder Seite hat einen **Schlüssel-Button** („Passwort ändern“) mit Dialog (aktuelles/neues/wiederholtes Passwort, Client-Validierung: Länge + Gleichheit). Solange `GET /auth/me` **`must_change_password: true`** liefert (Seed-`admin`/`admin` oder vom Admin gesetztes Startpasswort), zeigt das Layout ein **Hinweis-Banner** „Sie haben noch kein eigenes Passwort gesetzt“ mit direktem Button; nach der Änderung (oder wenn der Admin das Kennzeichen entfernt) verschwindet es. In Impersonations-Sitzungen ist der Flow gesperrt (Hinweis statt Dialog).
- **Noch offen (Härtung):** `token_version` wird beim Request **nicht** geprüft (der `AuthUser`-Extractor verifiziert nur das JWT) — ältere Tokens bleiben also bis zum Ablauf gültig. Eine Durchsetzung bräuchte einen DB-/Cache-Lookup pro Request; bis dahin hilft nur `JWT_TTL_SECONDS` kurz zu halten.

## JWT & API

- JWT-Claims: `sub`, `username`, `roles[]`, `groups[]`, `perms[]`, `auth_version`, `token_version`, `exp`. Der `perms[]`-Eintrag ist die **Bitmenge** der effektiven Rechte (`permissions::PermissionSet::words`), `groups[]` sind die Record-Ids der Gruppen (direkt und geerbt). **Umsetzungsstand:** Katalog, Gruppen/`perms` und `auth_version` sind umgesetzt (A1–A3); die Durchsetzung dieser Rechte an den Endpunkten läuft in Stufe B **Modul für Modul** — im Objektmodul entscheidet sie bereits (`auth::require_permission` bzw. `Claims::require`), in den übrigen Modulen noch die Rolle aus den Claims.
- Kurzlebige Access-Tokens; `token_version` erlaubt sofortiges Sperren/Logout einzelner Benutzer. **Umsetzungsstand:** `token_version` wird beim Passwortwechsel/Reset erhöht, aber noch **nicht** pro Request geprüft (s. Abschnitt „Passwort-Verwaltung“).
- **Dev-Bootstrap:** Beim ersten Start wird ein Standard-`superadmin` (`admin`/`admin`) angelegt, sofern `user:admin` nicht existiert; JWT-Config über `JWT_SECRET`/`JWT_TTL_SECONDS`.
- Neue Endpunkte: `GET /auth/me`, `POST /auth/password` (Selbst-Änderung, s. o.), `POST|PATCH|DELETE /users`, `POST|PATCH|DELETE /groups`, `POST /groups/{id}/members`, `POST /groups/{id}/children`.
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
