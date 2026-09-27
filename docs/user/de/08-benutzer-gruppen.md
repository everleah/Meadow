# Benutzer & Gruppen

Zwei Dinge auf einem Bildschirm: die **Personen** und die **Rechte**, die sie
tragen. Benutzer bekommen Rollen, Gruppen bündeln zusätzliche Rechte und
entscheiden über den Datenausschnitt; beides zusammen ergibt, was jemand darf und
sieht.

![Die Rechte-Matrix einer Gruppe](/help-assets/group-permissions.png)

## Rollen

Jeder Benutzer hat eine oder mehrere Rollen. Sie sind die Grundausstattung; eine
Gruppe ergänzt oder entzieht davon.

| Rolle | Bedeutung |
| --- | --- |
| **Betrachter** | Darf lesen: Objekte, Definitionen, Regeln, Läufe. |
| **Bearbeiter** | Darf zusätzlich pflegen: Objekte anlegen/ändern/löschen/veröffentlichen, Beziehungen, Jobs starten. |
| **Administrator** | Darf zusätzlich verwalten: Benutzer, Gruppen, Definitionen, Einheiten, Kanäle, Integrationen, System-Logs. |
| **Superadmin** | Darf alles, systemweit — und wird durch **nichts** eingeschränkt. |

Nur der **Superadmin** hat eine Sonderstellung: Er ist von jeder Prüfung
ausgenommen, auch von einem Daten-Scope. Ein Administrator **wird** durch einen
Scope eingeschränkt. Der letzte aktive Superadmin lässt sich weder löschen noch
herabstufen.

## Benutzer anlegen und pflegen

Der Dialog fragt Benutzername, E-Mail, Passwort, Rollen, Status und Sprache.
Benutzername und E-Mail sind Pflicht, alles andere hat sinnvolle Vorgaben.

- **Passwort** — mindestens 8 Zeichen und verschieden vom bisherigen. Beim
  Anlegen und beim Setzen durch einen Administrator entscheidet der Schalter
  **Neues Passwort erforderlich**, ob der Benutzer beim ersten Anmelden ein
  eigenes Passwort setzen muss. Aus für Passwörter, die Sie abschließend
  übergeben — etwa für ein Integrationskonto.
- **Status** — `active` oder `suspended`. Ein gesperrter Benutzer kann sich nicht
  anmelden; seine laufende Sitzung endet sofort.
- **Sprache** — die Sprache der Oberfläche für diesen Benutzer.
- **Passwort-Spalte in der Liste** — `new` heißt: der Benutzer hat noch kein
  eigenes Passwort gesetzt, `ok` heißt: erledigt.
- **Anmelden als** (nur Superadmin) — übernimmt für Tests und Support die Sitzung
  eines anderen Benutzers. Es ist **keine** Rechteübertragung: Die Sitzung läuft
  mit dessen Rollen und wird protokolliert; ein Banner führt zurück.

## Gruppen: Rechte bündeln

Eine Gruppe hat Rechte, einen Daten-Scope, Mitglieder und eine Stellung in der
Hierarchie. Die **effektiven Rechte** einer Person sind:

> Rollen-Vorgabe **+** alles, was ihre Gruppen (auch übergeordnete) erlauben
> **−** alles, was eine ihrer Gruppen ausdrücklich verbietet.

Ein **Verbot gewinnt** immer. Eine Gruppe ohne Rechte-Einträge ändert nichts —
sie ist der bequeme Weg, nur einen Daten-Scope oder nur eine Mitgliedschaft zu
bündeln.

## Die Rechte-Matrix

Jede Zeile ist ein Recht, jede Spalte eine der drei Antworten: **wie Rolle**
(nichts eintragen), **erlauben** (zusätzlich geben) oder **verbieten**
(ausdrücklich nehmen). Neben jedem Schalter steht die Rolle, deren Vorgabe gilt —
so ist ohne Nachschlagen klar, was „wie Rolle" bedeutet. Die Matrix bietet genau
die Rechte an, die das System kennt; ein unbekannter Schlüssel wird beim Speichern
abgelehnt, damit ein Tippfehler nicht wie ein gesetztes Recht aussieht.

## Der Daten-Scope

Der Scope beantwortet: **welche Daten** sieht diese Gruppe? Er wird aus
Bausteinen gebaut — Objekttyp, Status, Kategorie, Collection — und ist
standardmäßig leer: Ohne Baustein sieht die Gruppe den ganzen Katalog. Mehrere
Bausteine gelten **alle zusammen** („Produkte, die freigegeben sind **und** in
dieser Kategorie liegen").

Unter den Bausteinen steht der Scope **in Worten** und ein **Zähler**, der am
Kern misst, was der Scope zeigt („sichtbar: 2 000 von 11 633 Objekten"). Der
Zähler wird vor dem Speichern geprüft, ist aber keine Zusage: Er zeigt, was
gerade gilt.

Zwei Dinge, die man wissen muss:

- **Kategorie- und Collection-Bausteine meinen die direkte Zuordnung.** Ein
  Objekt, das nur einer Unterkategorie zugeordnet ist, fällt nicht darunter.
  Wenn Sie einen ganzen Zweig einschließen wollen, wählen Sie mehrere Kategorien
  aus.
- **Ein Baustein ohne Werte zählt nicht** — er ist erst ein Scope, wenn er Werte
  hat; der Zähler sagt das, statt eine Zahl zu erfinden.

Regeln, die nicht aus dem Assistenten stammen (aus einem Import oder einer
älteren Datei), bleiben erhalten: Der Editor zeigt sie wörtlich und speichert sie
unverändert.

## Rechte und Scope vorschlagen lassen (optional)

Ist die KI-Assistenz in den System-Einstellungen eingeschaltet und eingerichtet,
steht neben **Berechtigungen** der Knopf **Vorschlag erzeugen**. Er öffnet einen
Dialog: Sie beschreiben in eigenen Worten, was die Gruppe darf und welche Daten
sie sehen soll („darf nichts löschen und nur freigegebene Objekte sehen"), wählen
**Neu schreiben** oder **Bestehendes verbessern** und erzeugen den Vorschlag.

Der Vorschlag füllt die Rechte-Matrix und die Bausteine **Objekttyp** und
**Status** — er ist der vollständige Zustand, den er vorschlägt; alles, was er
nicht nennt, geht auf „wie Rolle" zurück. **Kategorie- und Collection-Bausteine
bleiben unverändert**, weil sie einzelne Objekte benennen, die der Vorschlag
nicht kennen kann; kann ein Wunsch so nicht ausgedrückt werden, sagt der Dialog
es als Hinweis. **Gespeichert wird nichts automatisch:** Prüfen Sie Matrix, Scope
und Zähler und drücken Sie dann **Speichern**.

## Mitglieder

Mitglieder werden sofort geschrieben, nicht gespeichert — der Abschnitt
**Mitglieder** im Gruppen-Dialog fügt einen Benutzer per Auswahl hinzu und
entfernt ihn wieder. Neben den direkten Mitgliedern stehen die **geerbten**: Wer
in einer Kindgruppe ist, trägt auch die Rechte der Elterngruppe. Solche Zeilen
sind mit „über <Kindgruppe>" gekennzeichnet und lassen sich hier nicht entfernen —
sie gehören zur anderen Gruppe.

Zwei Hinweise erscheinen genau dann, wenn sie zutreffen:

- Ein **Superadmin** in der Gruppe: Seine Mitgliedschaft ist wirkungslos, weil er
  ohnehin alles darf.
- Ein **Administrator** in einer Gruppe **mit** Daten-Scope: Er wird durch diesen
  Scope eingeschränkt — nur der Superadmin ist ausgenommen.

## Hierarchie

Die Hierarchie vererbt Rechte **nach oben**: Eine Kindgruppe gibt ihre Rechte an
ihre Elterngruppen weiter — nie umgekehrt. Jede Gruppe kann mehrere Eltern und
mehrere Kinder haben. Ein Kreis ist nicht möglich; der Kern lehnt einen solchen
Versuch mit einer Meldung ab, die der Dialog im Klartext zeigt.

## Effektive Rechte eines Benutzers

Die Frage „warum darf Peter das?" beantwortet der **Benutzer**-Dialog: Er lädt die
Rechte des geöffneten Benutzers aus dem Kern und nennt zu jedem Recht die Quelle.

![Die effektiven Rechte eines Benutzers](/help-assets/user-rights.png)

- Die **Kopfzeile** zählt zusammen: Rollen **+** Gruppen **⇒** wie viele der
  Rechte gelten.
- Darunter stehen nur die **Abweichungen** von der Rollen-Vorgabe: zusätzlich
  erlaubte und ausdrücklich verbotene Rechte, jeweils mit der Gruppe, die es
  sagt. Ein grüner Eintrag kommt von einer Gruppe, ein roter wurde verboten.
- Hinter **Alle Rechte des Katalogs** steht die vollständige Liste mit dem Zustand
  je Recht: `Rolle` (Vorgabe), `Gruppe` (zusätzlich erlaubt), `verboten`, und bei
  einem Superadmin `Superadmin`.
- Ein **↳** neben einer Gruppe heißt: Diese Gruppe zählt über die Hierarchie mit,
  die Person ist nicht direkt Mitglied.

## Was jede Änderung bewirkt

- **Rechte wirken sofort.** Jede Änderung an Rechten, Gruppen, Mitgliedschaften
  oder Passwörtern beendet die laufenden Sitzungen der **anderen** Benutzer — sie
  melden sich neu an und arbeiten dann mit dem neuen Stand. Wer die Änderung
  selbst ausführt, bleibt angemeldet.
- **Alles wird protokolliert.** Anlegen, Ändern, Löschen, Mitgliedschaften,
  Hierarchie und Passwort-Änderungen stehen im Audit-Log.
- **Gruppen sind keine Objekte.** Benutzer und Gruppen liegen in eigenen Tabellen
  und tauchen deshalb nicht in der Produktliste, im Import oder im Export auf.
