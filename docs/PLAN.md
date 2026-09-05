# Projektplan: IServ Ausleihe-Ausgabe

> Initialisierungsplan vom 2026-06-12, seither laufend fortgeschrieben
> (zuletzt 2026-07-05, Review Tier 1–3). Basiert auf der Projektskizze
> (Seminarfach) und den Klärungsfragen vom 2026-06-12.
> Dieses Dokument ist die Arbeitsgrundlage — Änderungen hier einpflegen.
>
> **Änderungshistorie:** Das chronologische Protokoll aller Umsetzungs-
> details, Bugfixes und Nutzer-Korrekturen steht in `docs/CHANGELOG.md`
> (neueste zuerst). Dieses Dokument hält den *aktuellen* Stand fest
> (Zielbild, Architektur, offene Punkte, Phasenplan), keine Erzählung der
> Vergangenheit — Ausnahme: §6/§6.1 (sicherheitskritisch, bewusst inkl.
> eigener Historie belassen).

## 1. Zielbild

**Eine App, zwei Modi**, gehostet auf dem Windows-Laptop der Schulbuchausleihe
im Schul-WLAN:

| Modus | Einsatz | Wer scannt | Kern-Ablauf |
|-------|---------|-----------|-------------|
| **A — Stapel** (Teil 1) | Sommerferien, Stapelerstellung | Helfer mit eigenem Handy | Laptop wählt Klasse → Schüler alphabetisch abarbeiten → Helfer scannt Bücher per Handykamera → Buchung → Leihschein-Druck |
| **B — Live-Ausgabe** (Teil 2, Pilot) | Schuljahresbeginn, Testklasse/-jahrgang ab Jg. 9 | Schüler mit eigenem Gerät | iPad zeigt allgemeinen QR → Schüler scannt → Handy zeigt 4-stelligen Code → Host ordnet Code einem Schüler zu und bestätigt → Schüler sieht bestellte Bücher, scannt sie selbst → Buchung |

**Leitplanken aus der Skizze (nicht verhandelbar):**

- Keine Schreiboperationen auf die Ausleihe-Datenbank durch selbstprogrammierten
  Code. Alle Writes laufen durch das **offizielle IServ-Frontend** (siehe
  Write-Pfad). Die `ausleihe-api` wird ausschließlich **read-only** genutzt.
- Bestehendes System (USB-Handscanner) bleibt jederzeit als Fallback nutzbar.
- Keine dauerhafte Speicherung von Schülerdaten; Website nur im Schul-WLAN,
  zugriffsgeschützt.
- Tests ausschließlich mit Niklas' Account und ausgemusterten Büchern.

## 2. Architektur

```text
Helfer-/Schüler-Handy (Browser: Kamera-Scanner)      iPad (QR-Anzeige)
        │  HTTPS + WebSocket                              │
        ▼                                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Python-Server (FastAPI) — Windows-Laptop, Schul-WLAN, Port 3443 │
│                                                                  │
│  ├─ Web-UI (statisch, vanilla JS):                               │
│  │    host.html   Laptop: Klasse/Schüler wählen, Pairing,   │
│  │                     Status aller Sessions, Skip-Funktion      │
│  │    scan.html        Handy: Scanner-UI (aus Fork übernommen)   │
│  │    qr-display.html  iPad: zeigt allgemeinen anonymen QR-Code  │
│  │                                                              │
│  ├─ ausleihe-api (read-only, Admin-Account):                     │
│  │    Klassen/Schüler, Anmeldungen, Bezahlstatus,               │
│  │    bereits ausgeliehene Bücher, Leihschein-PDF               │
│  │                                                              │
│  ├─ Playwright-Worker:                                           │
│  │    N Browser-Contexts auf der offiziellen IServ-Ausleihe-     │
│  │    Counter-Seite (eingeloggt). Pro aktivem Schüler ein        │
│  │    Context. Scan → Barcode-Feld füllen → Submit → Ergebnis    │
│  │    (Erfolg/Fehler) aus der UI zurücklesen.                    │
│  │    → Der Write geht durchs offizielle Frontend inkl. dessen   │
│  │      Validierung (bezahlt? richtige Serie? schon verliehen?)  │
│  │                                                              │
│  └─ Druck-Service:                                               │
│       get_loan_slip_pdf() → Drucker (Windows, silent print)      │
└──────────────────────────────────────────────────────────────────┘
```

**Stack-Entscheidungen** (geklärt 2026-06-12):

- **Backend:** Python (FastAPI + websockets), Neuaufbau. Die `ausleihe-api`
  wird als Dependency eingebunden (`pip install git+…` oder lokaler Pfad).
- **Write-Pfad:** Playwright-Browser-Automatisierung der offiziellen UI —
  **ein Mechanismus für beide Modi**. Begründung: skizzenkonform (offizielles
  Modul schreibt), parallelisierbar (ein Context pro Schüler), nutzt die
  eingebaute Validierung der offiziellen Website.
- **Frontend:** Vanilla HTML/JS ohne Build-Step (wie bisher);
  `html5-qrcode` (vendored), `beep.mp3` und die Scanner-UI-Basis werden aus
  dem Fork übernommen.
- **Alt-Code:** Node-Server und Python-Keyboard-Client werden entfernt
  (Git-Historie bewahrt sie); sauberes neues Projekt-Layout.
- **Accounts:** Ein Ausleihe-Admin-Account (Niklas) für API-Reads **und**
  Playwright-UI-Sessions. Credentials in `.env` (nicht im Repo).
- **Druck:** Server holt das offizielle Leihschein-PDF über die API und
  druckt direkt (SumatraPDF `-print-to` oder `win32print`).

**IServ-API-Verhalten (Gotchas, Phase-2-Spikes 2026-06-12):**

- **Klassen/Schüler-Endpoint:** `GET /schoolyears/:sy/forms` (direkter
  Client-Call, kein SDK-Wrapper) liefert in **einem** Request alle Klassen
  mit Schüler-Members, deren aktuell ausgeliehenen Büchern und
  Enrollment-Status. Klassen mit **weniger als 5 Mitgliedern** werden
  herausgefiltert (= Puffer-Klassen, siehe `get_forms` in
  `server/iserv_client.py`).
- **`student_upcoming_form` ist null am Schuljahresende** — die aktuelle
  Klasse eines Schülers ist dann nur über den `/forms`-Endpoint abrufbar,
  **nicht** aus `admin.get_enrollments()`.
- **Klasse im Scanner (2026-06-17):** `get_student_info` liefert **keine**
  Klasse; die Klasse (`form`) wird aus der Queue (`QueueStudent.form`) in
  den `student_info`-Payload injiziert. IServ-Klassennamen tragen ein
  Präfix „Klasse "; der gespeicherte `form`-Wert darf **nicht** verändert
  werden (muss für `get_students_for_form` matchen) — das Präfix wird
  daher nur in der UI gestrippt, nicht im gespeicherten Wert.

### 2.1 Aufteilung innerhalb von `server/` (Welle 6, 2026-09-05)

Eine Datei, eine Aufgabe. Bis 2026-09-05 vereinte `sessions.py` mit 2976
Zeilen und 92 Funktionen mindestens sechs unabhängige Aufgaben — Token-
Erzeugung, Buchungs-Precheck, Bücherlisten-Sichtbarkeit, Leihschein-Druck,
Geräte-Broadcast und den Scan-Station-/Modus-B-Lebenszyklus — und war damit
die Datei, die praktisch jedes neue Feature anfassen musste (Vorbild für
diesen Schnitt: `sba-dashboard/docs/architektur.md`, Abschnitt „Aufteilung
innerhalb von `app/`").

```text
server/
  sessions.py               Fassade: re-exportiert alle neun Module unten
                             mit explizitem __all__, keine eigene Logik mehr
  session_tokens.py         Token-/Code-/QR-Erzeugung
  scan_booking.py           Scan-Auswertung + Buchungs-Precheck/Commit (PLAN §6)
  book_visibility.py        Bücherlisten-Hydration & Sichtbarkeitsfilter
  loan_slip_flow.py         Leihschein-Druck (Modus A/B, telefonbasiert)
  device_broadcast.py       iPad-/Drucker-Display(+-Scanner)/Lehrkraft-Broadcast
  session_lifecycle.py      Modus-A/B Session-/Worker-Lebenszyklus + Sweeper
  helper_queue.py           Helfer-Warteschlange: Zuweisung, Booklist, Zuschauer
  scan_station_session.py   Scan-Station: Gerät, Laden, Zettel-Druck, TTL-Sweep
  device_persistence.py     Persistenz über Serverneustarts
```

`sessions.py` bleibt die öffentliche Fassade, damit die ~40 bestehenden
Importstellen (Routen, Tests) unverändert `from .sessions import X` bzw.
`from server.sessions import X` schreiben können — der Split hat keinen
einzigen Call-Site anfassen müssen. Aus demselben Grund lösen die neuen
Module gemeinsam genutzte Collaborators (`get_hub`, `get_config`,
`get_state`, `get_book_order_for_form`, `get_hidden_isbns_for_form`,
`server_lan_ip`, `secrets`) zur Laufzeit über diese Fassade auf
(`_sessions.get_hub()` usw., s. `scan_booking.py`-Modul-Docstring) statt sie
direkt aus ihrem Ursprungsmodul zu importieren — Unit-Tests patchen sie als
`sessions.get_hub` & Co. (Monkeypatch auf dem Fassaden-Modul-Objekt), und nur
ein zur Laufzeit aufgelöster Zugriff über genau dieses Modul-Objekt sieht
einen solchen Patch. Zwei echte Modul-Zyklen ließen sich beim Schnitt nicht
vermeiden und werden lokal über dieselbe Fassade gebrochen: `scan_booking.py`
<-> `book_visibility.py`/`loan_slip_flow.py`, sowie `session_lifecycle.py`
<-> `helper_queue.py`/`scan_station_session.py` (Details in den betroffenen
Modul-Docstrings/Kommentaren).

## 3. Rollen- und Sicherheitsmodell

| Rolle | Gerät | Zugang | Darf |
|-------|-------|--------|------|
| Host | Laptop | Passwort-Login → Session-Cookie | alles: Klasse/Schüler wählen, Pairing bestätigen, Skip, Abbruch, Druck |
| Helfer-Scanner (Modus A) | Helfer-Handy | Join-Code vom Host (oder Passwort) | zugewiesenen Schüler sehen, Bücher scannen |
| QR-Anzeige (Modus B) | iPad | Registrierung am Host per Code; danach **nur** QR-Anzeige, keine Schülerdaten im Klartext | QR-Codes anzeigen |
| Schüler-Session (Modus B) | Schüler-Handy | allgemeiner **konstanter** Join-QR (festes Secret, kein Rotieren) → Server mintet pro Browser-Session `session_token` + 4-stelligen Pairing-Code, vom Host bestätigt | nur eigene Bestelldaten sehen, nur eigene Bücher scannen |
| Scan-Station (Modus B) | festes Gerät neben den Regalen | Token in der URL (wie das Drucker-Display) + Freischaltung durch den Host per Namenseingabe; Schüler meldet sich mit dem vierstelligen Code seines gedruckten Zettels an | Bücher für den gerade angemeldeten Schüler scannen — **kein** Leihschein-Druck, **kein** Abschließen |

Sicherheitsanforderungen (aus Klärung 2026-06-12, „keine Sicherheitslücken"):

1. **Einmal-Tokens:** _(Mechanismus geändert 2026-06-15, siehe
   `docs/phase4_modus_b_2026-06-15.md`.)_ Das iPad zeigt einen **allgemeinen,
   anonymen** QR. Beim Scan mintet der Server pro Browser-Session einen langen,
   kryptographisch zufälligen `session_token` (~256 bit) — den **einzigen**
   Daten-Zugang — und einen 4-stelligen `pairing_code`. Der Code dient nur der
   menschlich vermittelten Zuordnung am Host und gewährt **nie** selbst
   Datenzugriff. Server-seitiger Zustand entscheidet über Gültigkeit.
2. **Harter Zugriffsentzug:** Nach erfolgreichem Abschluss des Ausgabe-Prozesses
   (oder Timeout/Abbruch durch Host) wird das Token serverseitig
   invalidiert und die WebSocket-Session beendet. Ist „Leihschein unterschreiben"
   für die Klasse aktiv, gehört die physische Unterschrift/Übergabe noch zum
   Ausgabe-Prozess; der Host beendet die offene Schüler-Session danach
   ausdrücklich. Erneuter Aufruf der URL → neutrale
   „Vorgang abgeschlossen"-Seite, keine Daten.
   Der Timeout-Arm (`sessions.expired_student_sessions`) läuft **nur, solange
   die Session getrennt ist**: eine gepairte Session mit offener WebSocket gilt
   als lebend (tote Sockets schließt Uvicorns WS-Keepalive nach ~40 s), erst ab
   `disconnected_at` tickt `PAIRED_IDLE_TTL_S` (30 min). Sonst verlor ein
   Schüler, der nur wartet oder sein Handy kurz ausschaltet, mitten im Vorgang
   den Zugang. Der Token liegt clientseitig in `localStorage` (überlebt den
   Browser-Neustart des Handys) — er bleibt trotzdem nur so lange gültig, wie
   der Server-Zustand ihn führt, und wird bei `closed`/Code 4006 lokal
   gelöscht.
3. **Doppelte Bestätigung:** Token allein reicht nicht — die Session wird erst
   aktiv, wenn der Host den 4-stelligen Pairing-Code dem Schüler zuordnet
   und bestätigt.
4. **iPad-Absicherung:** Das QR-Display zeigt **ausschließlich** den allgemeinen
   QR-Code — **keine** Namen/Initialen (O8 geklärt: anonym, 2026-06-15). Die
   Display-Session ist eine eigene Rolle ohne Datenzugriff; Registrierung nur
   über den Host. iPads zusätzlich im geführten Zugriff (iOS Kiosk-Modus).
5. **Skip-Funktion:** Host kann Schüler überspringen (krank/abwesend);
   deren Tokens werden nie ausgegeben bzw. sofort invalidiert.
6. **Transport:** HTTPS mit selbstsigniertem Zertifikat (Logik aus dem Fork
   nach Python portieren), nur im Schul-WLAN erreichbar.
7. **Keine Persistenz:** Schülerdaten nur im RAM der Session; Logs ohne
   personenbezogene Daten (Buch-Codes ja, Namen nein).

### 3.8 Scan-Station (`/scan-station`) — Schüler ohne Handy

Modus B setzt ein eigenes Handy pro Schüler voraus. Wer keins hat, fiel bisher
komplett auf die Helfer-Bedienung zurück. Die **Scan-Station** schließt die
Lücke: ein festes Gerät (Laptop/Tablet mit Kamera), an dem sich nacheinander
mehrere Schüler kurz anmelden.

**Der Scanmodus ist derselbe wie am Handy — inklusive Blockierverhalten.**
Station und Schülerclient teilen sich Aussehen (`web/scan-view.css`) und Logik
(`renderBookRows`/`renderBookAlert`/`statusAlertClass` in `web/common.js`) —
obere Leiste, Statuszeile samt Farbregeln, Namenszeile, Bücher-Tabelle mit
FLIP-Animation und Buch-Hinweis-Modal sind identisch. Ein ausgemustertes oder
anderweitig verliehenes Buch (`book_deleted`/`not_in_stock`) öffnet an der
Station **exakt wie am Handy** ein blockierendes Modal ohne eigenen
Schließen-Weg (`ScanStationSession.book_alert_open`) — Kamera-Scan **und**
manuelle Eingabe werden ignoriert (das Feld selbst bleibt bedienbar, wirkt
nur nicht), bis der Host per `/api/clear-book-alert` freigibt (dieselbe
Route wie für Modus-B-Sessions, jetzt auch für Stationen,
s. `AppState.find_station_by_student`). Ersatzansprüche (`loaned_to`-Details)
gehen unverändert **nur** an den Host — `process_scan(..., source="student")`
liefert sie an Station wie Handy grundsätzlich nicht mit, der volle Umfang
landet ausschließlich im `book_alert`-Broadcast an den Host. Einzige bewusste
Abweichung: die Station zeigt keinen Zahl-/Anmeldestatus (geteiltes Gerät).
Zusätzlich lässt sich — wie im Helferclient — zwischen **Kamera** und
**Manuell** (Tastatur-/Handscanner tippt ins Lesefeld, mit Fokus-Warnbanner)
umschalten; die Station merkt sich die Wahl lokal, eine Host-Vorgabe
(`input_mode`) überschreibt sie, genau wie beim Theme.

**Stationswechsel per Zettel-Code.** Jeder Scan während einer laufenden
Anmeldung wird zuerst darauf geprüft, ob es sich eigentlich um den
Zettel-Code eines ANDEREN Schülers handelt. Der Treffer-Check selbst läuft
**unabhängig von Länge/Ziffernform** des gescannten Werts (ein einfacher,
billiger Dict-Lookup gegen die vergebenen Codes) — nur so scheitert ein
echter Treffer nie an einer zu strengen Formannahme über das, was der
Scanner tatsächlich liefert. Trifft es zu, wechselt die Station sofort zu
diesem Schüler (der bisherige wird über `release_station_student`
freigegeben, inkl. Worker-Rückgabe), statt den Code als unbekannten
Buch-Barcode zu melden — ein Schüler, der an einer belegten Station seinen
eigenen Zettel scannt, muss nicht warten. Ist der Zielschüler nicht (mehr)
wechselbar (z. B. inzwischen `done`), bleibt der aktuell angemeldete Schüler
unangetastet, nur eine Fehlermeldung erscheint. Das Rate-Limit (10/Minute)
bleibt an die 4-stellige Form gebunden (Buch-Barcodes sind länger, s.
`Code.PNG`) und greift für jeden 4-stelligen Versuch — Treffer wie
Fehlversuch — sonst liefe ein Durchprobieren fremder Codes während einer
laufenden Sitzung daran vorbei.

**Ablauf.** Der Host druckt dem Schüler im Pairing-Kasten seiner Klasse einen
A4-Zettel („Scan-Station: [Schüler] [Drucken]"). Darauf stehen oben links
Klasse und Name, oben rechts ein **Code-39-Barcode (6,5 × 1,2 cm)** mit der
vierstelligen Nummer darunter, im Blattkörper die bereits ausgeliehenen und die
noch vorgemerkten Reihen — letztere mit Kästchen zum Abhaken mit dem Stift. Der
Schüler scannt an der Station den Barcode, bekommt seine Bücherliste und scannt
seine Bücher wie sonst am Handy. Nach 30 s ohne Aktivität fällt die Station auf
„Zettel-Code scannen" zurück und ist für den Nächsten frei.

**Sicherheitsmodell** (Ergänzung zu 1.–7. oben):

- Der vierstellige Code ist der Zugangs-Credential des Zettels. Er wird
  **nie geloggt** und steht **nicht** im Host-Snapshot (nur der
  Registrierungs-Code des Geräts, der keinen Datenzugriff gewährt).
- Codes werden **innerhalb einer Server-Laufzeit nie an einen ANDEREN
  Schüler recycelt** — auch nicht, wenn der Schüler fertig ist. Sonst könnte
  ein alter, noch herumliegender Zettel plötzlich einen anderen Schüler
  laden. Umgekehrt bleibt der Code pro Schüler stabil, ein Nachdruck trägt
  also denselben Barcode.
- **„Trennen" am Host entwertet den aktiven Code** (seit 2026-08-12):
  `AppState.invalidate_station_code` — der Zettel wird an der Station nicht
  mehr angenommen, und eine laufende Stationsanmeldung wird zugleich gelöst
  (`release_station_student`). Der entwertete Code bleibt aber unter
  `AppState.station_last_code_by_student` als „letzter Code" gemerkt. Beim
  nächsten „Erstellen"/„Erstellen und Drucken" für denselben Schüler zeigt
  der Host-Druckdialog eine Checkbox „Alten Code (‹Code›) reaktivieren",
  **standardmäßig angehakt** — reaktiviert genau diesen Code (der alte
  Zettel bleibt gültig) statt einen neuen zu ziehen (`AppState.
  allocate_station_code(reactivate_old=True)`, Default). Ein abgewählter
  Haken zieht einen frischen Code; dieser wird ab dann selbst zum „letzten
  Code" für eine spätere Reaktivierung — bei mehrfachem Trennen/Erstellen
  zeigt die Checkbox also immer den zeitlich JÜNGSTEN entwerteten Code, nie
  einen älteren. Durchgereicht bis in den `PrintJob` (Checkbox-Wert
  überlebt die asynchrone Druckerwarteschlange bei „Erstellen und
  Drucken").
- Code-Versuche sind pro Station auf **10/Minute** gedrosselt (4 Stellen =
  10 000 Möglichkeiten wären sonst durchprobierbar).
- Ein Code wird nur angenommen, wenn der Schüler eindeutig frei ist: in einer
  offenen Klasse, nicht `done`, keinem Helfer zugewiesen, keine gepairte
  Handy-Session, nicht an einer anderen Station angemeldet.
- Die Station sieht **nur Name und Klasse** des angemeldeten Schülers — anders
  als am eigenen Handy keine Zahl-/Anmeldedaten (geteiltes Gerät).
- Ein Reload/Disconnect der Station gibt einen angemeldeten Schüler sofort
  frei; ein Gerät kann per × am Reiter endgültig verboten werden (Bannliste).

**Host-Queue-Status des Zettel-/Stations-Flusses.** Der Zettel-Druck ist das
„Aufrufen" dieses Flusses — der Schüler wechselt (falls noch `pending`) auf
`active` und bekommt seinen Fortschritt befüllt wie beim Aufrufen durch einen
Helfer (`init_book_progress`, `reset_baseline=True`). Solange er NICHT an
einer Station angemeldet ist und noch nicht alles ausgeliehen hat, zeigt die
Host-Queue statt einer Zahl „Bücher sammeln" (`QueueStudent.
station_zettel_printed`, persistiert über ein Ab-/Anmelden hinweg — nur
„Status zurücksetzen"/„Trennen" am Host löscht die Markierung). Ist er an
einer Station angemeldet, zeigt die Queue immer die zweizeilige Zahl
(„X/Y ohne Mjb" + „X/Y gesamt", wie beim Helfer mit Vorbestand). Meldet er
sich ab, fällt die Anzeige auf „Bücher sammeln" zurück — außer alle Bücher
sind bereits ausgeliehen, dann bleibt es bei der Zahl stehen. Der
Stationsname erscheint dabei wie ein Helfer-Badge (Now-Serving-Kästchen +
Klassen-Queue), mit dem Host-Symbol aus dem Drucker-Display (`ICO_LAPTOP`
dort, `ICO_HOST` im Host) statt des Helfer-Symbols; ein übernehmender Helfer
hat Vorrang vor dem Stations-Badge. Im Now-Serving-Kästchen ("Aktuell in
Ausgabe") steht das seit 2026-08-11 als eigene Zeile direkt unter dem Namen:
links Stationsname + Symbol + ein kleiner Trennen-Knopf, der NUR die
Stationsbindung löst (`POST /api/scan-station/release-student`, adressiert
über die `student_id` statt der `station_id` — Spiegel von
`/api/scan-station/release`), rechts der vierstellige Zettel-Code. Die Zeile
erscheint, sobald der Schüler einen Code hat (unabhängig vom aktuellen
Anmeldestatus); der Stations-Trennen-Knopf nur, solange er GERADE
angemeldet ist. Der Code wird bewusst NUR in den beiden `state_snapshot()`-
Aufrufen von `_queue_student_as_dict`/`queue_as_list` mitgegeben
(`include_station_code=True`) — die Helferclient-Queue-Pfade
(`pending_queue_as_list`/`real_contexts_summary`) lassen ihn weiterhin weg,
er bleibt ein Host-only sichtbares Credential (§3.7).

**Rangfolge vor dem Worker-Pool.** Mit der Station bewerben sich drei Rollen um
die begrenzten Playwright-Contexts. `WorkerPool.open_student(priority=…)`
bedient sie in der Reihenfolge **Helfer → Scan-Station → Schülerclient**
(innerhalb einer Rolle FIFO), analog der Rollen-Rangfolge der
Druckerwarteschlange. Der Helfer bedient eine ganze Queue und darf nie hinter
Einzelgeräten anstehen. Wartende Clients bekommen ihre Position gemeldet
(`worker_waiting`), statt stumm „Wird geladen…" zu zeigen.

**Leerlauf-TTL.** Die 30 s zählen **erst ab `worker_ready`** — solange die
Station auf einen freien Worker wartet oder die Kartei lädt, läuft die Uhr
nicht. Sonst würde ein wartender Schüler mitten im Laden hinausgeworfen. Der
Client hat denselben Timer lokal (Restzeit-Anzeige), der Server-Sweeper
(5-s-Takt) ist das Sicherheitsnetz für eingefrorene Geräte.

**Produktionsschutz.** Der gesamte Pfad bleibt read-only: `get_student_info`
ist ein GET, der Zettel wird lokal gebaut (`server/scan_station.py`) und lokal
gedruckt. Buch-Scans laufen über dasselbe `process_scan` wie am Handy und
stagen ohne `ALLOW_BOOKING` nur (§ 6.1). Der Zetteldruck läuft durch dieselbe
Druckerwarteschlange wie ein Host-Leihschein (`kind="station_sheet"`,
`role="host"`), setzt aber **keinen** Leihschein-Status.

**Druckermodus (2026-08-13).** Sind für den angemeldeten Schüler alle
vorgemerkten Bücher gescannt, wechselt die Station — wie der Schülerclient —
in einen Druckermodus (dieselbe `#view-print`-Ansicht/Optik, ohne Buttons).
Bei `slip_trigger` „Automatisch"/„Selbstauslöser" (Selbstauslöser verhält
sich hier vorübergehend wie Automatisch) wird der Leihschein-Druckauftrag
NUR erzeugt, wenn mindestens ein für die Klasse erlaubter Drucker gerade auf
einem verbundenen Drucker-Display sichtbar ist (`PrintJob.
station_display_gate`, `sessions.displayed_printer_ids`); dispatcht wird er
ebenfalls nur auf einem gerade sichtbaren Drucker (`PrintQueue._claim_fills`).
Verschwindet die Sichtbarkeit, während der Auftrag noch **wartet**, pausiert
er automatisch und der Host bekommt in der Schüler-Kachel „Aktuell in
Ausgabe" einen gelben Hinweis samt Drucker-Auswahlmenü, um ihn manuell als
Host-Auftrag zu übernehmen (`PrintQueue.host_adopt_station_job`, Endpoint
`POST /api/print-queue/{id}/adopt-station`). War von Anfang an kein
erlaubter Drucker sichtbar, wird **gar kein** Auftrag erzeugt (kein
Host-Hinweis) — die Station schickt den Schüler zum Host, dessen normaler
Druckbutton dafür unabhängig vom `slip_trigger` freigeschaltet wird
(`QueueStudent.station_print_needs_host`). Bei `slip_trigger` „Betreuer"
zeigt die Station nur den Hinweis, bei „Barcode" bleibt es beim Platzhalter.
Fester 30-s-Abmelde-Timer ohne Reset nach Eintritt in den Druckermodus (die
Station bleibt ein Gemeinschaftsgerät). Details: `docs/CHANGELOG.md`.

## 4. Offene Punkte

| # | Frage | Vorschlag / nächster Schritt |
|---|-------|------------------------------|
| O1 | Modus A: Wie kommt ein Schüler auf ein bestimmtes Helfer-Handy? | **Umgesetzt.** „Weiter"-Button (⏭, WS `next` → `advance_helper`) lädt den nächsten Pending; Host weist zusätzlich per „Nächster Schüler" zu. Peek-Menü (Hamburger ≡) zeigt die Warteschlange **aller offenen Host-Klassen** (Reiter, eigene vorausgewählt) mit „Aufrufen"-Button, ohne den Hintergrund-Schüler zu trennen; Aufruf aus fremder Klasse rebindet den Helfer. Lupe (Such-Panel) erlaubt Schnellsprung zu jedem Schüler des Schuljahrs. Menü ist auch im Idle (kein Schüler zugewiesen) nutzbar. Details/Chronologie (2026-06-17 bis 2026-07-09): `docs/CHANGELOG.md`. |
| O2 | Erlaubt IServ mehrere parallele Sessions desselben Accounts? | **Geklärt (Spike B, 2026-06-12):** Ja — 3/3 parallele unabhängige Logins + 3/3 Cookie-Sharing-Contexts, keine Invalidierung. Context-Pool mit unabhängigen Contexts. |
| O3 | Exaktes Verhalten der offiziellen Counter-Seite (DOM, Fehlerfälle, Schüler-Wechsel) | Spike A erkundet das mit Test-Account + ausgemustertem Buch. |
| O4 | Welcher Drucker (USB am Laptop? Netzwerk? Treiberlage unter Windows)? | **Adressiert (2026-06-15/2026-06-22):** Druck-Service gebaut (`server/printing.py`, Backends `file`/`lp`/`sumatra`/`win-default`/`auto`), read-only PDF-Abruf via `get_loan_slip_pdf`. Silent-Print am Zielgerät verifiziert (Spike C, V12) → `docs/test_status.md`, `docs/deployment.md`. |
| O5 | Bezahlstatus-Anzeige: genaue Quelle (`enrollments`/`payments` via Admin-API) und Sonderfälle (Befreiung/Ermäßigung) | **Geklärt (2026-07-06):** `enrollments`-Payload trägt `remission_*`/`exemption_*` je Anmeldung, `*_accepted` tri-state (`null`=unentschieden). `get_student_info` liefert `paid`/`amount_open`/`remission_pending`/`exemption_pending`; Clients zeigen „Nachweis fehlt" vor dem Betrag. Read-only verifiziert am Testschüler (kein Antrag). Details: `docs/CHANGELOG.md`. |
| O6 | Modus B: Was passiert bei „nicht bezahlt"? (Buch zurücklegen, Helfer rufen?) | **Umgesetzt, fachlich noch offen.** UI zeigt Bücher + „nicht bezahlt"-Banner; Host gibt beim Pairing per `override_payment` frei. Ein ausstehender Ermäßigungs-/Befreiungsnachweis blockt das Pairing ebenso; beide Blocker werden in einem kombinierten Host-Dialog freigegeben (`reason:"blocked"`-409 + `blockers`-Liste). Nicht-angemeldete Schüler lösen keine Nachfrage aus. **Fachlicher Wortlaut/Workflow noch mit Hr. Pühn final.** Details: `docs/CHANGELOG.md` (2026-07-06). |
| O7 | Deployment-Packaging für den Windows-Laptop (Python-Installation? portable venv? `start.bat`?) | Phase 3; Kandidat: `uv` + Lockfile + Start-Skript, alternativ portable Python. |
| O8 | Zeigt das QR-Display Namen/Initialen zur Orientierung oder nur anonyme QRs? | **Geklärt (2026-06-15): anonym.** Ein allgemeiner QR, keine Schülerdaten auf dem iPad (Mechanismus geändert → `docs/phase4_modus_b_2026-06-15.md`). |
| O9 | Schul-WLAN: Client-Isolation zwischen Handy und Laptop? | Spike D; Erfahrung mit dem bisherigen Barcode-Scanner spricht dagegen, trotzdem vor Ort verifizieren. |
| O10 | Modus A: Nachfrage bei Unstimmigkeit (Nachweis fehlt / Rechnung offen) vor der Ausleihe? | **Umgesetzt (2026-07-07, rein client-seitig).** Beim ersten Buch-Scan eines Schülers mit `remission_pending`/`exemption_pending`/`!paid` (nur bei `enrolled`) zeigt der Helferclient (`web/scan.js`/`scan.html`) einen Bestätigungsdialog, **bevor** der Scan an den Server geht; „Ja, ausleihen" gibt für den Rest der Session frei, „Nicht ausleihen" verwirft den Scan. Nur GET, kein Schreibzugriff, keine Host-Benachrichtigung. Manuell verifiziert; kein automatisierter Test (UI-Gate). Details: `docs/CHANGELOG.md`. |

## 5. Phasenplan

Timeline-Anker: **Teil 1 muss zum Ferienbeginn (Anfang/Mitte Juli 2026)
einsatzbereit sein.** Teil 2 zum Schuljahresbeginn (Ende August 2026).

### Phase 0 — Projekt-Setup (KW 24/25) — abgeschlossen

- [x] Repo umstrukturiert: Alt-Code raus, Python-Projektgerüst
      (`server/`, `web/`, `automation/`, `docs/`, `pyproject.toml`)
- [x] Scanner-Assets übernommen (`html5-qrcode.min.js`, `beep.mp3`,
      Scan-Logik aus `scanner.html` → `web/scan.html`/`web/scan.js`)
- [x] `.env`-Handling + `CLAUDE.md` mit Read-only-/Produktions-Schutzregeln
      (analog `ausleihe-api`)
- [x] Dieses Plandokument committen; README neu geschrieben

### Phase 1 — Spikes: Risiken zuerst (KW 25/26)

> Erst wenn Spike A funktioniert, lohnt der Rest. Scheitert er, müssen wir
> den Write-Pfad neu diskutieren (→ `processBook`-API wäre die Alternative,
> erfordert aber eine Skizzen-/Policy-Entscheidung).

- [ ] **Spike A (kritisch):** Playwright gegen die offizielle Counter-Seite —
      Login, Schüler öffnen, Barcode eintragen, Submit, Erfolg/Fehler aus dem
      DOM auslesen. Test: ausgemustertes Buch auf Niklas' Account ausleihen
      **und zurücknehmen**.
- [x] **Spike B:** 2–3 parallele Contexts mit demselben Account (→ O2) — erledigt 2026-06-12
- [ ] **Spike C:** Silent-Print eines PDFs unter Windows (→ O4)
- [ ] **Spike D:** Reichweitentest im Schul-WLAN: Handy ↔ Laptop (→ O9)

### Phase 2 — Kern Modus A (KW 26–28)

> Details, Bugfixes und Nutzer-Korrekturen zu jedem Punkt: `docs/CHANGELOG.md`.

- [x] FastAPI-Server: HTTPS (selbstsigniert), WebSocket-Hub, Session-/Rollenmodell — 2026-06-12
- [x] Host-UI: Login, Klasse wählen, alphabetische Queue, Live-Status Helfer-Sessions — 2026-06-12
- [x] Host-UI: Schuljahr auswählbar (`GET /api/schoolyears` + `POST /api/select-schoolyear`, read-only) — 2026-06-17
- [x] Helfer-Scanner-UI: Token-basiert, Schüleranzeige (angemeldet/bezahlt/Bücher), Scan-Feedback — 2026-06-12
- [x] Scanner-„Weiter"-Button (⏭): Helfer schließt aktuellen Schüler ab + lädt nächsten aus der Queue selbst (O1) — 2026-06-17
- [x] Playwright-Worker: Context-Pool (N unabhängige Logins), Schülerkartei laden, Barcode staged (kein Submit) — 2026-06-12; Kartei per Schüler-ID-Route seit 2026-06-17
- [x] Recovery (Re-Login bei Session-Ablauf) — 2026-06-15 (`automation/worker.py`, deterministisch getestet via `automation/recovery_test.py`)
- [x] E2E-Smoke headless (read-only): voller Modus-A-Flow Host→Scanner→Worker→Kartei→staged — 2026-06-15 (`automation/e2e_smoke.py`)
- [x] 2-Helfer-Paralleltest: zwei Schüler gleichzeitig aktiv, beide Karteien parallel, unabhängiges Staging — 2026-06-15 (`automation/e2e_parallel.py`)
- [x] Pool-Härtung: fehlgeschlagene Worker-Logins werden in `start()` einmal nachgezogen, geleakte Contexts geschlossen — 2026-06-15; Context-Leak bei schnellem „Weiter" gefunden + strukturell behoben — 2026-07-05
- [x] Buchender Submit-Pfad als Code vorhanden, **dreifach gated** — 2026-06-15:
      `commit_barcode()` (Enter+Result-Parse) + `handle_commit()` + Endpoint
      `POST /api/commit-book`. Gates: `ALLOW_BOOKING=false` (Default) + Host-Auth
      + `confirm:true`. Feuert ohne Freigabe **nie** gegen Produktion (verifiziert:
      bei Default wird der Worker nicht berührt). Enter/Selektoren unverifiziert bis
      zum freigegebenen Test.
- [ ] Fehlerfälle Scanner: falsches Buch, nicht angemeldet, schon ausgeliehen (braucht freigegebenen Buchungstest)
- [x] Leihschein-Druck — Code fertig: read-only PDF-Abruf + Druck-Abstraktion
      (`server/printing.py`, Endpoint `POST /api/print-loan-slip`, Host-Button) —
      2026-06-15. Echter Druck am Zielgerät noch zu verifizieren (`docs/test_status.md`).
- [x] Helfer-Druck-Dialog (`web/scan.html`) statt Sofortdruck — 2026-06-23
- [x] Scanner-Buchliste: erledigte (gescannt/ausgeliehen) sinken nach unten, nach Ausgabe-Aktualität sortiert — 2026-07-02
- [x] Konfigurierbare **klassenweite Bücher-Reihenfolge** für den Scanner (Drag & Drop, Jahrgangs-Bücherliste) — 2026-07-02
- [x] **Host-Einstellungen-Dialog** (Drucker-Auswahl, Bücherlisten jahrgangsweit ordnen) — 2026-07-04
- [x] Karte „Bücher-Reihenfolge (Scanner)" entfernt (redundant zum Einstellungen-Dialog, 2 Bugs mitgefixt) — 2026-07-05
- [x] Bücher-Reihenfolge pro Schüler-Jahrgang statt globaler Klassen-Order — 2026-07-05
- [x] Review-Tier-2-Hardening (Worker/IServ-Client/Web/API/Printing/TLS, Commit `63a4cb3`) — 2026-07-05
- [x] Review-Tier-3 (UI-Architektur: `scan.js`-Extraktion, `onclick`-Entfernung; Server-Robustheit: `advance_helper`-Split, Broadcast-Race-Fix) — 2026-07-05
- [x] Buchreihen ausblenden (Einstellungen-Dialog) — 2026-07-05
- [x] Serverseitige Persistenz der Buchreihenfolge/Ausblendung (`data/booklist_settings.json`) — 2026-07-08
- [ ] End-to-End-Test mit ausgemusterten Büchern **inkl. Buchung** (wartet auf Buchungstest-Freigabe Niklas + Lukas)

### Phase 3 — Generalprobe Teil 1 (vor Ferienbeginn, Anfang Juli)

- [x] Deployment-Packaging (→ O7): `setup.bat`/`start.bat`/`start.sh` +
      `docs/deployment.md` (Windows + Macbook, USB-Drucker) — 2026-06-15.
      `setup.bat` installiert `uv` seit 2026-07-05 automatisch, falls es fehlt.
      Lauf am echten Ausleihe-Laptop noch offen (`docs/test_status.md`).
- [ ] Probelauf im Schul-WLAN mit echtem Drucker
- [ ] Helfer-Kurzanleitung (1 Seite) + dokumentierter Fallback auf USB-Scanner
- [ ] **Meilenstein: Einsatz bei der Stapelerstellung**

### Phase 4 — Modus B: Live-Ausgabe-Pilot (Juli–August)

> Initialer Aufbau erledigt 2026-06-15 (reiner Server-/Web-Code, keine Buchung).
> Details + Sicherheits-Review: `docs/phase4_modus_b_2026-06-15.md`; laufende
> Chronologie danach: `docs/CHANGELOG.md`.

- [x] QR-Display-Rolle (iPad): Registrierung, vom Host gesteuerte Anzeige
      (`web/qr-display.html`, allgemeiner anonymer QR) — 2026-06-15
- [x] Einmal-Token-System + Pairing-Flow (langer `session_token` + 4-stelliger
      Code, Host-Bestätigung; Mechanismus geändert, s. Doku) — 2026-06-15
- [x] Host-Pairing-UI ohne Tippen (wartende Codes am Host anzeigen + per Klick
      zuordnen) — 2026-06-17
- [x] Pairing-Latenz-Fix (`student_info` vor Worker-Open pushen) — 2026-06-17
- [x] iPad-Display am Host bedienbar (Button „QR für iPad anzeigen" +
      Freischalt-Feld für Registrierungscode) — 2026-06-17
- [x] Join-QR: Rotation pro Zuordnung eingeführt (2026-06-17), dann durch
      Rotation pro Ausgabe-Öffnen ersetzt (2026-06-18) — Schutz liegt auf
      `modus_b_open`-Gate + Ratelimit + manueller Host-Zuordnung
- [x] Queue-Steuerung erweitert: pro Schüler „Trennen", global „Alle
      Verbindungen trennen" / „Queue Status zurücksetzen" — 2026-06-17
- [x] Schüler-UI: reduziert und selbsterklärend (`web/student.html`:
      Bestellliste, Scan, Abschluss) — 2026-06-15
- [x] Scan-Vorabprüfung gegen die Anmelde-Buchliste (read-only,
      `check_scanned_book`) — 2026-06-22
- [x] Harter Zugriffsentzug (Token-Invalidierung + WS-Close + Worker zu) —
      2026-06-15; Skip-Funktion deckt Modus B mit ab
- [x] Sicherheits-Review Token-Lebenszyklus (initial, E2E-verifiziert) —
      2026-06-15; iPad-Härtung (iOS-Kiosk) bleibt organisatorisch
- [ ] Lasttest: 5 parallele Schüler-Sessions
- [x] Rate-Limit `/api/student/join` (pro-IP, 5/10 s, `server/ratelimit.py`) —
      2026-06-15; Logik verifiziert, End-to-End-Drosselung noch im Lasttest zu prüfen.
- [x] Hardening-Pass aus Code-Review (2026-06-18): Worker-Context-Leak (Pool-
      Erschöpfung), WS-Reconnect-Leak, Host-Login-TTL (`HOST_SESSION_TTL_S`),
      QR-IP-Override (`HOST_IP`), Pairing-TOCTOU, `commit-book`-ok-nur-bei-booked
      u. a. Write-Pfad-Gating unangetastet. Details: `docs/hardening_2026-06-18.md`.
- [x] **Klassen-Lehreransicht `/teacher`:** QR-Token + Host-Pairing pro
      Klassen-Kontext, strikt minimierte Live-Statusansicht und nur
      `wartend ↔ übersprungen` als Lehreraktion — implementiert 2026-08-04
      (`server/routes/teacher.py`, `server/routes/ws.py::ws_teacher`,
      `AppState.teacher_snapshot`, `web/teacher.html`/`teacher.js`). Detailplan:
      `docs/teacher_status_page_plan.md`. Live-Check im Schul-WLAN offen
      (`docs/test_status.md`).
- [x] **Leihschein-unterschreiben-Modus im Schülerclient:** Bei aktivierter
      Klassenoption „Leihschein unterschreiben" folgt nach abgeschlossenem
      Druck eine offene Ansicht mit Aufforderung zur Unterschrift und
      Übergabe an Betreuer oder Lehrer; der Zustand überlebt Reconnects —
      2026-08-06
- [x] **Schülerleihscheinmodus (Abschluss-Screen mit Eigenabruf):** Der
      „Vorgang abgeschlossen"-Screen des Schülerclients (nach dem
      Leihschein-unterschreiben-Modus bzw. direkt nach „Leihschein erhalten"
      ohne aktivierte Unterschrift) bietet einen Button zum Herunterladen des
      eigenen Leihscheins mit den Aktionen der letzten drei Monate
      (`IsServClient.get_loan_slip_pdf(..., start_reporting_period="3months")`).
      Der Server pusht das PDF base64-kodiert über die noch offene
      Schüler-WS, BEVOR die Session regulär schließt (`closed`) und der
      Session-Token hart entwertet wird — ein Nachfordern danach ist nicht
      mehr möglich (`server/sessions.py::_send_own_slip_download`,
      `invalidate_session`) — 2026-08-07
- [ ] O6 fachlich mit Hr. Pühn finalisieren (Wortlaut „Nachweis fehlt" +
      kombinierter Host-Freigabe-Dialog bei nicht-bezahlt/Nachweis, 2026-07-06)
- [ ] Generalprobe vor Schuljahresbeginn
- [ ] **Meilenstein: Pilot mit Testklasse/-jahrgang**

### Begleitend (Seminarfach)

- Entscheidungen und Messergebnisse (Zeitersparnis!) fortlaufend in `docs/`
  festhalten — Spike-Ergebnisse, Architekturentscheidungen, Probelauf-Protokolle
  sind direkt verwertbares Material für die Seminarfacharbeit.
- **`docs/test_status.md`** (lebend) führt Verifiziertes vs. Offenes; neue zu
  testende Dinge dort eintragen.

## 6. Test- und Produktionsschutz

- Die `ausleihe-api` läuft hier **ausnahmslos read-only** (`allow_writes=False`,
  Default). Es gibt keinen Grund, das in diesem Projekt je zu ändern. Buchungen
  laufen **ausschließlich** über den Playwright-Write-Pfad (offizielles Frontend,
  Enter auf der Counter-Seite), **nie** per API-Write.
- Playwright-Tests nur mit Niklas' Account und ausgemusterten Büchern;
  Test-Ausleihen werden unmittelbar zurückgenommen.
- Vor jedem Probelauf: Rückbau-Plan (welche Test-Buchungen müssen rückgängig
  gemacht werden) schriftlich festhalten.

### 6.1 Buchungs-Freigabe (2026-07-02) — Auto-Buchung mit Vorabprüfung

Niklas hat das Klicken auf **Enter** (Buchung gegen die Produktion) freigegeben —
aber **nur**, wenn eine gescannte Buchung **beide** Bedingungen erfüllt. Sind sie
nicht erfüllt, wird der Barcode **gar nicht erst ins Eingabefeld getippt**:

1. **Buch im Lager** — `book.available and not book.distributed and not book.deleted`
   (Lager-Status aus `GET /books/{code}`).
2. **Bestellt & Reihe noch nicht ausgeliehen** — die ISBN gehört zur Anmelde-
   Buchliste des Schülers **und** von der Reihe ist noch kein Exemplar auf ihn
   ausgeliehen (= ISBN im Status „vorgemerkt" der Schülerinfo).

Umsetzung:

- `server/sessions.py::evaluate_scan_for_booking()` — read-only Vorabprüfung.
  **Streng bei Unsicherheit** (kein Client / Buchliste noch nicht geladen /
  Lookup-Fehler → nicht buchen), weil bei Erfolg automatisch Enter folgt.
- `server/sessions.py::process_scan()` — gemeinsame Scan-Verarbeitung für
  Scanner (Modus A) und Schüler (Modus B): Prüfung → bei Erfolg buchen
  (`handle_commit`, Enter) **falls `ALLOW_BOOKING=true`**, sonst nur stagen
  (`handle_scan`, fill ohne Enter). Bedingungen nicht erfüllt → **kein**
  Feldkontakt.
- **`ALLOW_BOOKING` bleibt Master-Gate** (Default `false` = kompletter read-only-
  Betrieb, Scan bleibt staged). Erst auf `true` feuert die Auto-Buchung.
- **Manueller „Buchen"-Button entfernt (2026-07-02):** Der Host-UI-Button
  (`web/host.html`, Kachel- + Queue-Ansicht) plus die `commitBook`-JS-Funktion
  sind raus — er wurde nur bei `allow_booking=true` gerendert, also genau dann,
  wenn die Auto-Buchung ohnehin läuft (redundant). Der Endpoint
  `POST /api/commit-book` (+ `handle_commit`) **bleibt** als dreifach gegateter
  Fallback bestehen, nur ohne UI-Fläche.
- Getrennte ISBN-Mengen pro Session: `vormerk_isbns` (buchbar) / `lent_isbns`
  (für die Meldung „Reihe schon ausgeliehen") in `HelperSession`/`StudentSessionB`.
- Tests: `tests/test_booking_precheck.py` (Bedingungslogik + Gate-Verhalten),
  `tests/test_booking_gate.py` (Enter-Gate unverändert).

⚠️ Die Erfolgs-/Fehler-Selektoren in `worker.commit_barcode()` /
`_read_booking_result()` sind bis zum ersten freigegebenen Realtest **unverifiziert**
(nur ein „booked" aus dem DOM gilt als Erfolg; „unknown" täuscht keine Buchung vor).
Vor Scharfschalten: ausgemustertes Buch + Rückbau-Plan.

**Update (2026-07-05) — Ausgemustert-Prüfung vorgezogen:** `book["deleted"]`
wird jetzt als **erste** Bedingung geprüft, noch vor „bestellt & Reihe nicht
ausgeliehen" — eigener Status `"book_deleted"`, unabhängig davon, ob der
Schüler das Buch überhaupt bestellt hat. Grund: ein ausgemustertes Buch soll
sofort als solches erkennbar sein, statt hinter „nicht bestellt" versteckt zu
werden. Die Bedingung „im Lager" (`not_in_stock`) prüft jetzt nur noch
`distributed`/`available`, `deleted` läuft separat vorher. Sichtbarkeit:
`process_scan()` broadcastet bei `book_deleted` UND `not_in_stock` (bereits
verliehen) einen `{"type": "book_alert", "kind", "student_id", ...}` an alle
Host-WS-Verbindungen (roter Toast + rot markiertes Kästchen der betreffenden
Person unter „Aktuell in Ausgabe" in `web/host.html`, inkl. eigenem
„Schließen"-Button pro Kästchen). Scanner (`web/scan.html`) und Schüler-Client
(`web/student.html`) färben bei `book_deleted` die Statuszeile rot
(`status-book-deleted`) und zeigen ein Hinweis-Modal ohne eigenen
Schließen-Button.

- Scanner (Modus A, Helfer bedient): schließt per Klick außerhalb der Box
  oder automatisch beim nächsten Scan — der Helfer steuert das selbst.
- Schüler-Client (Modus B, Schüler scannt selbst): das Modal ist **blockierend**
  — kein Klick-außerhalb, kein Auto-Close. `StudentSessionB.book_alert_open`
  wird server-seitig gesetzt; jeder weitere Scan wird ignoriert
  (`ws.py`/`ws_student`, vor `process_scan`), bis der Host über
  `POST /api/clear-book-alert` (Button im Now-Serving-Kästchen) freigibt —
  das schickt `{"type": "book_alert_clear"}` an die Schüler-WS und löscht das
  Kästchen bei allen Host-Verbindungen. Überlebt Reconnect (`book_alert_payload`
  wird erneut gesendet).

Tests: `tests/test_booking_precheck.py`
(`test_reject_deleted_before_not_enrolled`,
`test_reject_deleted_before_not_in_stock`).

**Update (2026-07-06) — Alert-Topologie verfeinert (Helfer schließt selbst,
verliehen-an-andere symmetrisch zu ausgemustert, Selbst-Leihe als Hinweis):**
Drei aufeinander aufbauende Nutzer-Korrekturen am Ausgemustert/verliehen-Alarm.

1. **Helfer-Modal bekommt Schließen-Button, Host ohne für Helfer-Scans.**
   `process_scan()` trägt jetzt `source` (`"helper"` Modus A / `"student"`
   Modus B) in den `book_alert`-Broadcast ein. Der Host rendert seinen
   Schließen-Button im Now-Serving-Kästchen **nur** für `source !== "helper"`
   — am Helfer-Scanner schließt der Helfer sein Modal selbst (Button im
   `web/scan.html`-Modal), der Host zeigt die Meldung rot, aber ohne Button.
2. **Helfer-Schließen räumt den Host mit auf.** Neuer WS-Message-Typ
   `clear_book_alert` am Helfer-Scanner (`server/routes/ws.py`/`ws_scanner`)
   — der Server feuert `{"type": "book_alert", "student_id", "cleared": true}`
   an alle Host-Verbindungen. `dismissBookAlert()` im Helfer schließt das
   Modal **und** sendet das Clear (guard: nur wenn Modal offen war). Kontext-
   wechsel (neuer Schüler/Wartend) bleiben rein lokal — dort räumt die Queue
   das Host-Kästchen ohnehin.
3. **Verliehen-Unterscheidung: an andere vs. an sich selbst.**
   - `not_in_stock` (Buch an **jemand anderen** verliehen) → **symmetrisch zu
     `book_deleted`**: Helfer-Modal mit Schließen-Button (räumt Host),
     Schüler-Modal **ohne** Button + **blockierend**
     (`StudentSessionB.book_alert_open` jetzt auch für `not_in_stock`,
     Scans werden serverseitig ignoriert bis Host-Clear), Host-Kästchen rot
     ohne Button (bei Helfer-Source) / mit Button (bei Schüler-Source).
   - `series_already_lent` (Buch bereits an **sich selbst** verliehen) → nur
     ein **Hinweis**, den Helfer wie Schüler **lokal** selbst schließen
     können (Button/nächster Scan), **nicht blockierend**, **ohne Host-Bezug**
     (`process_scan` broadcastet bei `series_already_lent` bewusst **nicht**).

   Modal-Titel/Farbe sind dynamisch per Status: `book_deleted`/`not_in_stock`
   rot („Ausgemustertes Buch gescannt" / „Buch noch verliehen"),
   `series_already_lent` orange („Buch bereits an dich verliehen"). Der
   Schüler-Client zeigt bei der blockierenden Variante „Bitte warte, bis der
   Betreuer dies freigibt.", beim Hinweis „Du kannst diese Meldung selbst
   schließen." + Schließen-Button.

Kein DB-/IServ-Write — nur read-only `book["deleted"]`/`distributed`/
`available` + WS-Broadcasts. Tests: `tests/test_booking_precheck.py` +2
(`test_process_scan_broadcasts_alert_for_not_in_stock`,
`test_process_scan_no_alert_for_series_already_lent`), Suite 92 grün.
Commits `09296f2`, `440f5b4`, `b4610de`.

**Update (2026-07-06) — Verliehen-an-Name bei `not_in_stock`:** Wird ein
Buch gescannt, das derzeit an **jemand anders** verliehen ist (`not_in_stock`,
`distributed`), zeigen **Helfer-Scanner und Host** zusätzlich, **an wen** es
verliehen ist — der **Schüler-Client (Modus B) sieht den Namen bewusst nicht**
(Privatheit: der Schüler scannt nur, der Betreuer am Host/Helfer muss wissen,
wem das Buch gerade gehört). `server/iserv_client.py::get_book_by_code` liefert
neben `student_id` `loaned_to` („Vorname Nachname") + `loaned_to_id`. Der
aktuelle Ausleiher ist in `GET /books/:code` bereits als eingebetteter
`Student` enthalten → im Normalfall **kein Extra-Request**; nur falls die
Einbettung fehlt/anonymisiert ist, Nachladen per `GET /students/:id`
(read-only, tolerant bei Fehlern → `None`). `evaluate_scan_for_booking` hält
die `msg` bewusst **name-frei** („Nicht im Lager (verliehen): …") und trägt den
Namen nur als eigenes `loaned_to`-Feld. `process_scan` steuert die Sichtbarkeit
pro Source: der `book_alert`-Broadcast an den Host enthält `loaned_to` immer
(unabhängig davon, wer gescannt hat); das zurückgegebene `scan_result`-Payload
enthält `loaned_to`/`loaned_to_id` **nur für `source != "student"`** (Helfer
Modus A), für den Schüler werden beide auf `None` gesetzt. UI:
`web/scan.html` eigene Zeile „Aktuell verliehen an: …" im Buch-Hinweis-Modal
(liest `msg.loaned_to`); `web/host.html` ergänzt Toast („— verliehen an …")
und eine `ns-borrower`-Zeile im Now-Serving-Kästchen; `web/student.html`
zeigt unverändert nur die name-freie `msg`. **Host-Farbigkeit (Verliehen-Alert):
im Now-Serving-Kästchen ist nur der „verliehen an …"-Text rot (`ns-borrower`-
Zeile), der Alert-Meldungstext ist normal (`ns-alert-muted`); Kästchen selbst
bleibt rot (`ns-tile-alert`). Der Toast bleibt als rotes Kästchen (`toast-warn`,
weißer Text inkl. „verliehen an …"). Namen werden **nicht geloggt** (PLAN §3.7),
nur an Host + Helfer durchgereicht. Kein DB-/IServ-Write.
Tests: `tests/test_booking_precheck.py` +4 (`test_not_in_stock_carries_loaned_to`,
`test_not_in_stock_without_borrower_stays_silent`,
`test_process_scan_loaned_to_for_helper`,
`test_process_scan_hides_loan_from_student`), Suite 96 grün. Commits `15bf5f1`,
`<follow-up>`.

**Update (2026-07-09) — Hinweis-Modal für JEDEN nicht-verbuchbaren Scan
(beide Clients).** Bisher öffnete nur `book_deleted`/`not_in_stock`/
`series_already_lent` ein Hinweis-Modal; alle anderen nicht-OK Auswertungen
(`not_enrolled` = „nicht bestellt", `unknown_book` = „unbekannt",
`not_ready` = „Buchliste noch nicht geladen", `error` = Lookup/Client-Fehler)
liefen nur als Text in der Statuszeile mit. Jetzt öffnet **jeder** nicht-OK
Scan ein Fenster (gleicher Modal-Baukasten wie die bestehenden Alerts):

- **Schüler-Client (Modus B, `web/student.html`):** die drei
  sicherheitskritischen Fälle bleiben **Host-geschlossen** (blockierend, kein
  Schließen-Button, serverseitig `book_alert_open` blockiert weitere Scans,
  nur der Betreuer gibt per `book_alert_clear` frei) — `book_deleted`
  (ausgemustert, mit **und** ohne Ersatzanspruch, d. h. `loaned_to` spielt
  keine Rolle für die Schließ-Logik) **und** `not_in_stock` (an andere Person
  verliehen). **Alle übrigen nicht-OK Status** (`series_already_lent`,
  `not_enrolled`, `unknown_book`, `not_ready`, `error`) schließt der Schüler
  **selbst** (Schließen-Button **oder** nächster Scan) und scannt weiter —
  der bestehende close-on-next-scan-Pfad greift für jeden dismissiblen
  Hinweis. Neue Hilfs-Sets `OK_STATUSES_STUDENT` (`staged`/`booked`) und
  `BLOCKING_STATUSES_STUDENT` (`book_deleted`/`not_in_stock`); `dismissible =
  !ok && !blocking`.
- **Helfer-Client (Modus A, `web/scan.js`):** **jedes** nicht-OK Modal ist am
  Gerät schließbar (Button / Klick außerhalb / Escape / nächster Scan);
  `dismissBookAlert` beim nächsten Scan räumt ggfls. die Host-Meldung auf
  (`clear_book_alert`), bei Status ohne Host-Broadcast (alle neuen + die
  Selbst-Leihe) ist das Clear ein No-op. `OK_STATUSES` statt der alten
  `ALERT_STATUSES`-Menge.

Beide Clients: `ALERT_META` um Titel/Farbe für die neuen Status ergänzt
(orange = Hinweis: `not_enrolled`/`not_ready`/`series_already_lent`; rot =
Fehler: `unknown_book`/`error`). Rein client-seitig — Server-Pfad
(`evaluate_scan_for_booking`, `process_scan`, `book_alert`-Broadcast) und
IServ/DB unangetastet (read-only, kein GET mehr als bisher, kein Write).
`node --check` OK; manuelle Geräte-Verifikation offen. Commit `eba6071`.

**Bugfix (2026-07-07) — „Reihe an dich ausgeliehen" greift bei ausgeblendeten
Reihen UND nach Buchung in derselben Session:** zwei Lücken im Erkennen
„Buch bereits an dich selbst verliehen" (`series_already_lent`), die beide den
selben Symptom-Pfad hatten — ein Scan des *eigenen* Exemplars fiel zu
`not_in_stock` und deklarierte es fälschlich als „verliehen an jemand anderes".

1. **Ausgeblendete Buchserie, die der Schüler bereits hat.**
   `apply_hidden_books` entfernt eine ausgeblendete Reihe nur aus
   `info["books"]`, **nicht** aus `info["current_books"]`. Bisher baute
   `booking_isbn_sets_from_info` die `lent`-Menge aus `info["books"]`
   status-basiert auf → eine ausgeblendete, aber bereits ausgeliehene Reihe
   fehlte in `lent` → der Scan des eigenen (durch `distributed`
   gekennzeichneten) Exemplars lief auf die Lager-Prüfung auf (`not_in_stock`).
   Fix: `lent` wird **autoritativ aus `info["current_books"]`** (ungefiltert)
   gebildet; nur falls `current_books` fehlt (Unit-Test-Fixture), wird auf die
   status-basierte Menge aus `info["books"]` zurückgefallen. `current_books`
   ist in echten `info`-Payloads aus `get_student_info` stets vorhanden.

2. **In derselben Session frisch gebuchtes Buch.** Nach einer Buchung
   (`status == "booked"`) ist das Exemplar serverseitig `distributed` an den
   Schüler, aber `lent_isbns` stammt noch aus der Lade-Zeit (ISBN steht dort
   in `vormerk_isbns`). Ein erneuter Scan desselben Exemplars — oder eines
   weiteren Exemplars derselben Reihe — in derselben Session (ohne
   Schüler-Neuladen) lief deshalb ebenfalls auf `not_in_stock` (mit `loaned_to` =
   Schüler selbst). Fix: `process_scan` hängt nach `booked` die ISBN von
   `vormerk_isbns` nach `lent_isbns` um. Die übergebenen Mengen sind die
   Session-Mutables (passed-by-reference) — das Update greift am Helfer- bzw.
   Schüler-Session-State direkt, ein Neuladen ist nicht nötig.

Beide Fixes sind reine read-only-Logik (kein IServ-/DB-Write, keine neuen
Endpunkte). **Lesson:** eine „ist das Buch an dich ausgeliehen"-Prüfung muss
die *ungefilterte* Buchliste des Schülers sehen — ein UI-Filter, der Reihen
für die Anzeige/Tabelle ausblendet (`apply_hidden_books`), darf nicht die
autoritative Quelle für den Verliehen-Status sein; und ein serverseitiger
Zustandswechsel (Buchung) muss die gecachten Prüf-Mengen der Session
mitschreiben, sonst veraltet der Cache bis zum nächsten Neuladen.
Tests: `tests/test_booking_precheck.py` +2 (`test_lent_from_current_books_ignores_hidden_filter`,
`test_process_scan_booked_isbn_moves_to_lent`), Suite 107 grün. Live-Verifikation
am Testschüler offen. Details: `_logs/2026-07-07_sba_reihe_an_dich_erkannt.md`.

**Update (2026-07-07) — Ersatzanspruch-Hinweis + Lager-Prüfung vor
Bestell-Prüfung:** Zwei aufbauende Änderungen an `evaluate_scan_for_booking`.

1. **Ersatzanspruch bei ausgemusterten Büchern mit Schülerbezug.** Ein
   `book_deleted`-Buch, das noch eine `student_id != null` trägt (z. B.
   `[not_timely]` verloren, `[unusable]` beschädigt), reicht `loaned_to`/
   `loaned_to_id` durch — Host + Helfer zeigen zusätzlich „Ersatzanspruch: …"
   (Toast, Now-Serving-Kästchen `ns-borrower`, Helfer-Modal-Borrower-Zeile),
   der **Schüler-Client sieht nur „ausgemustert"** (kein Name, kein Hinweis;
   `process_scan` strippt für `source="student"` wie bei `not_in_stock`).
   `web/scan.js`/`web/host.html` branchen das Wording am `kind`/`status`
   (`book_deleted` → „Ersatzanspruch …", sonst „verliehen an …"). Ablösend zur
   früheren Idee, `[not_timely]` wie verliehen mit „verloren"-Wording zu
   behandeln — solche Bücher bleiben auf dem `book_deleted`-Pfad.
2. **Lager-Prüfung VOR Bestell-Prüfung.** Neue Prüf-Reihenfolge:
   `deleted → series_already_lent → nicht-im-Lager (not_in_stock) → nicht
   bestellt (not_enrolled)`. Ein verliehenes Buch zeigt jetzt immer
   „verliehen", auch wenn der Schüler es gar nicht bestellt hat (früher kam
   „Nicht bestellt" durch). `series_already_lent` (ISBN ∈ `lent_isbns`)
   bleibt **vor** `not_in_stock`, da das Exemplar an dich selbst verliehen
   sein kann (distributed) — sonst würde „verliehen an dich selbst"
   gemeldet; es greift auch bei lagernden Exemplaren einer schon ausgeliehenen
   Reihe. `book_deleted` bleibt erste Prüfung (Ersatzanspruch-Display).

Kein DB-/IServ-Write — nur read-only Flags + WS-Broadcasts. Tests:
`tests/test_booking_precheck.py` +8 (Ersatzanspruch: Durchreichung +
Helper/Student-Unterschied für `book_deleted`; Reihenfolge:
`not_in_stock`-vor-`not_enrolled`, `series_already_lent`-vor-`not_in_stock`,
`series_already_lent`-bei-lagerndem-Exemplar), Suite 100 grün.
Commit `9551f4e` (Ersatzanspruch), Reihenfolge-Update folgt.

**Update (2026-07-07) — Lade-State bis Worker bereit (`worker_ready`):** Beim
Aufrufen eines Schülers wurden bisher die komplette `student_info` (inkl.
Bücherliste) sofort gepusht und der Playwright-Worker erst danach geöffnet
(`open_student`, mehrere Sekunden Browser-Navigation) — die Bücherliste/der
„Scanner bereit"-Status erschienen, bevor der Worker buchungsbereit war, und
Früh-Scans liefen auf „Worker-Session nicht bereit". Neue getrennte Push-Phase
über die WS-Nachricht `worker_ready` (signalisiert „Worker buchungsbereit, Scans
frei"), client-spezifisch:

- **Modus A (`web/scan.js`):** `student_info` bleibt vollständig (Bücher sofort
  sichtbar). `worker_ready` (ohne Bücher-Payload) flippt nur Statuszeile von
  „Warten…" auf „Scanner bereit — Buch scannen" + gibt Scans frei. Bis dahin
  ignoriert `onScanSuccess` Scans clientseitig (früher „Wird geladen…"-Text
  → jetzt „Warten…" konsistent mit `workerPending`-Flag).
- **Modus B (`web/student.html`):** `student_info` künftig **ohne Bücher**
  (`books: []`, nur Name/Klasse/Bezahlt + `book_order`). `worker_ready` trägt
  die Bücherliste und flippt Status von „Wird geladen…" auf „Scanner bereit" +
  gibt Scans frei. Bücher-Bereich zeigt bis dahin Placeholder
  „Bücher werden geladen…"; `onScanSuccess` ignoriert Scans (wie der
  ausgemusterte-Buch-Block via `workerPending`).

Server: `load_and_push_helper_student` (Modus A) sendet `worker_ready` nach
`set_worker_session` (oder sofort ohne `worker_pool`); bei Playwright-Fehler
nur `error`, kein `worker_ready` (Worker nie bereit → Scans bleiben ignoriert,
Helfer hat Bücher schon). `load_and_push_paired_student` (Modus B) sendet
`student_info` ohne Bücher + `worker_ready` mit Büchern; bei Fehler nur
`error` (Bücherliste bleibt aus, Host muss eingreifen). Stale-Guards in beiden
Routinen senden kein `worker_ready` (neuer Schüler wird separat geladen).
Reconnect (`routes/ws.py` ×2): `student_info` neu + `worker_ready`, wenn Worker
bereits in `state.student_worker_sessions` registriert oder kein Lade-Task
(`helper.load_task`/`session.load_task`) mehr läuft — sonst liefert der Task
es an die neue WS.

**Scanner-Reconnect-Grace (Modus A, 2026-07-07):** Das `finally` des Scanner-WS
ruft den Schüler-Teardown (`end_student`: Schüler `pending`, Worker zu) nicht
mehr inline auf, sondern verzögert als Task (`_deferred_end`,
`_RECONNECT_GRACE_S=3.0`). Lädt der Helfer die Seite neu (Reconnect), cancelt
der neue WS den Grace-Task, übernimmt `helper.ws` synchron (vor jedem await —
so erkennt das alte `finally` an `helper.ws is websocket` den Reconnect und
löst keinen Teardown aus), lädt `student_info` (GET) neu und — falls der
Worker bereits bereit stand — `StudentSession.reload()` (Re-Navigation über
`load_card`/GET-Routen inkl. Re-Login-Recovery, bewusst KEIN `page.reload()`
wegen Post-Re-Post-Risiko) auf dem **bestehenden** Context, dann `worker_ready`.
Läuft der Lade-Task noch, liefert dieser `worker_ready` selbst an den neuen WS
(`student_info` steht schon). Re-Checks in `_deferred_end` (`helper.ws` gesetzt
bzw. `helper.student_id` ≠ Original) machen den Task zum No-op, falls er doch
durchläuft (Cancel-RC, `/api/skip`, neuer Schüler, …). Echte Trennung (Tab zu,
kein Reconnect) → Teardown nach der Frist — so steht kein „active" auf einem
toten Helfer-Token (Modus-A-Queue-Einträge räumt der Sweeper nicht ab).
Vorbild war Modus-B `ws_student`, dessen `finally` die Session ohnehin nicht
abbaut. `Hub.send_websocket` serialisiert die Reconnect-Sends über das
Per-WS-Lock gegen den In-Flight-Lade-Task. Nur GET, kein DB-/IServ-Write.
Tests: `tests/test_scanner_reconnect.py` (14). Live am Gerät noch offen.

**Reconnect-Ergänzung (2026-07-09):** Zwei Punkte.
(1) **Lupe-Schüler überleben den Reload.** Bisher stellte der Reconnect nur
Schüler wieder her, die in einer Queue stehen (`call`/`next`) — der Lupe-Schüler
(`search_call`, bewusst nicht gequeuät) fiel durchs Raster: `find_student` →
None → `waiting`, Schüler weg, Worker nicht neu geladen. Neu speichert
`HelperSession.student_form` die Klasse beim Zuweisen (`assign_student_to_helper`); der Reconnect nimmt die Form daraus, falls `find_student` None
liefert, und durchläuft für den Lupe-Schüler denselben Wiederherstellungs- +
Worker-Reload-Pfad. `end_student` räumt `student_form` in beiden Zweigen.
(Peek/Hintergrund ist nur eine Ansicht — beim Reconnect kommt der Schüler als
aktiv zurück, `helper.peeking` wird False.)
(2) **`StudentSession.reload()` schneller.** Auf der bereits initialisierten
Page entfällt der App-Root-Load (~4 s); stattdessen Hop `#/counter` →
`#/counter/student/<id>` (In-App-Hashrouten via `_goto_authed`, inkl. Re-Login-
Recovery). Der `#/counter`-Hop erzwingt einen echten Re-Render (gleicher Hash
allein wäre ein Angular-No-Op ohne frische Buchdaten). Fallback auf
`load_card()` (Root + Schüler-Route), falls das Barcode-Feld nicht erscheint.
`load_card` (frisches `open_student`) bleibt unverändert — dort muss Angular
von der Root initialisiert werden (Spike B). Nur GET, kein `page.reload()`.
Tests: Suite **149 grün** (`tests/test_scanner_reconnect.py` reload-Sequenzen
angepasst, `tests/test_queue_flow.py` +`student_form`-Setzen/Clear). Live am
Gerät offen (read-only, Freigabe — §6).

Nur GET / read-only — `get_student_info` (GET) + `open_student` (Browser-
Navigation ohne Submit), keine DB-/IServ-Writes, keine neuen Endpoints.
Tests: `tests/test_queue_flow.py` +Assertion (`student_info` mit `books==[]`
+ `worker_ready` nach `_advance_and_drain`), Suite grün. Live-Verifikation am
Testschüler noch offen (read-only, braucht Niklas+Lukas-Freigabe).

**Bugfix (2026-07-05) — Scanner reagiert nicht auf Host-Trennung:**
`end_student()` löste die Helfer-Zuordnung serverseitig, informierte aber nie
den Scanner-WebSocket selbst — `web/scan.html` hat keinen Host-State-Feed und
reagiert nur auf gezielt gepushte Nachrichten. Betraf „Trennen" **und** „Alle
Verbindungen trennen". Fix: `end_student()` schickt jetzt zusätzlich
`hub.send_scanner(old_helper, {"type": "waiting", ...})` an den betroffenen
Helfer. **Lesson:** jede neue serverseitige Aktion, die einen Helfer-Zustand
ändert, braucht einen expliziten `send_scanner`-Push — ein
`broadcast_host`-Aufruf allein erreicht den Scanner nicht.

**Update (2026-07-07) — Warteschlange im Helferclient + gezielter Aufruf
(`call`):** Bisher zeigte der Helfer-Scanner bei keinem zugewiesenen Schüler
eine *leere* Buchliste + in der Statuszeile nur die Warteschlangen-**größe**
(`queue_update` trug nur `queue_size`, nie die Einträge); „Weiter" nahm den
ältesten Wartenden (`next_pending`), ein *gezielter* Aufruf fehlte. Neu: bei
keinem Schüler zeigt der Buchlistenbereich die **Warteschlange** — selbes
Zeilenformat wie die Bücherliste, aber **ohne Farbgebung**, mit
**„Aufrufen"-Button** pro wartendem Schüler. Klick ruft genau diesen Schüler
gezielt auf (neuer WS-Handler `{type:'call', student_id}`).

- **Server (read-only, nur lokale Helfer-Zuweisung — kein DB-/IServ-Write):**
  `state.pending_queue_as_list()` (nur `status='pending'`); `queue_update` +
  alle `waiting`-Nachrichten tragen jetzt die `queue`-Liste (nur an
  unzugewiesene Helfer); `assign_student_to_helper()` aus
  `assign_next_pending_to_helper` extrahiert (wird von „nächster" und „aufrufen"
  geteilt); `call`-Handler prüft `target.status == 'pending'` **atomar** (kein
  Await zwischen Prüfung und Zuweisung → kein Doppel-Aufruf zweier Helfer auf
  denselben Schüler), beendet ggf. den alten Schüler, weist den gezielten zu;
  bei Nicht-verfügbar `error` + sofortiger `queue_update`-Push.
- **Client (`web/scan.js`/`scan.html`):** `renderQueue()` rendert `.queue-row`
  (transparent, keine `row-vorgemerkt`/`row-ausgeliehen`-Tint) mit
  `.call-btn`; delegierter Klick-Handler sendet `{type:'call', student_id}`.

**Bugfix (2026-07-07) — Queue während des Schüler-Ladens verbergen (auch
„Weiter"):** die Queue darf nur erscheinen, wenn *weder* ein Schüler geladen
ist *noch* gerade einer geladen wird. Erster Entwurf flaggte nur den
„Aufrufen"-Klick (`awaitingCall`) — bei „Weiter" (`next`) stand der nächste
Schüler schon fest, aber `student_info` fehlte noch; in diesem Fenster konnte
eine späte `queue_update` die Queue wieder aufblitzen lassen. Generalisiert:
`awaitingCall` → `loadingStudent`, gesetzt in **beiden** Pfaden
(`advanceToNext` für `next` UND Aufrufen-Klick für `call`); Queue rendert nur
bei `!studentActive && !loadingStudent`; freigegeben bei `student_info`/
`waiting`/`error`. **Lesson:** ein Lade-Flag vor der ersten Server-Bestätigung
muss *jede* Aktion abdecken, die `student_info` nach sich zieht — nicht nur
den neu eingeführten Pfad.

Nur GET / read-only, keine DB-/IServ-Writes, keine neuen REST-Endpoints.
Tests: 105 grün (+2 in `test_queue_flow.py`: `assign_student_to_helper`
gezielt, `pending_queue_as_list`; 2 angepasste Assertions wegen neuem
`queue`-Feld). Live-Verifikation am Testschüler offen. Details:
`_logs/2026-07-07_sba_helfer_queue_anzeige.md`.

**Bugfix (2026-07-07) — Queue während des Schüler-Ladens verbergen, auch bei
Host-„Nächster":** das reine Client-`loadingStudent`-Flag reichte nicht — der
Host-„Nächster"-Button (`/api/next-student`) triggert `advance_helper`/
Zuweisung serverseitig, ohne dass der Helfer-Client davon weiß; und das
`waiting`, das `end_student` beim alten Schüler schickt, renderte die Queue
(„Warteschlange angezeigt, obwohl schon ein neuer Schüler geladen wird").
Neue WS-Nachricht `{"type":"loading"}`: versetzt den Helfer-Client in den
Lade-Zustand (Queue verbergen, „Schüler wird geladen …", `loadingStudent=true`,
kein `studentActive`). Gesendet (a) von `end_student` im Advance-Kontext statt
des Idle-`waiting` (neuer Param `helper_notify={"type":"loading"}`; Default
`None` → weiter Idle-`waiting` für Disconnect/Skip/Reset, dort soll die Queue
erscheinen), (b) von `assign_student_to_helper` beim Zuweisen — deckt auch den
Fall, dass der Helfer keinen alten Schüler hatte (Host-„Nächster", „Aufrufen"
aus der Queue-Anzeige → kein `end_student`). `/api/next-student` nutzt jetzt
`assign_student_to_helper` (DRY, bekommt den `loading`-Send gratis). `waiting`
heißt jetzt zuverlässig „idle" → Queue. **Lesson:** ein serverseitig
ausgelöster Übergang am Client braucht ein eigenes Signal (`loading`), wenn
der Client den Zustand nicht selbst initiiert hat — ein Client-Flag greift
nur bei selbst getätigten Aktionen. Tests: `test_queue_flow.py` +Assertion
(`advance_helper` sendet `loading`, kein `waiting`; `assign_student_to_helper`
sendet `loading`), Suite 105 grün.

## § State-Feld-Rationale

Ausgelagerte Detail-Begründungen für einzelne Felder der Dataclasses in
`server/state.py`. Ziel: die Typdefinitionen bleiben überfliegbar (nur eine
Zeile Zusammenfassung + Verweis am Feld), das „Warum" lebt hier. Kurze 1–2-
Zeilen-Kommentare und alle `# Abgesichert: tests/…`-Zeiger bleiben am Code.

### `RuntimeSettings`

- **`save_pdf_locally`** — Entwickler-Toggle „PDF lokal speichern": erzwingt beim
  Drucken das `file`-Backend (Leihschein wird ins Ausgabeverzeichnis geschrieben
  statt an den Drucker geschickt) — unabhängig von `PRINT_BACKEND`. Für Tests
  ohne physischen Drucker. `False` = normaler Druckweg.
- **`fix_class_on_slip`** — experimenteller Entwickler-Toggle „Klasse auf
  Leihschein korrigieren": ersetzt beim Drucken den (teils falschen) Klassen-Code
  hinter „Klasse " auf dem IServ-Leihschein durch die echte Klasse des Schülers
  aus dem Serverstate. Rein lokale PDF-Bearbeitung, kein IServ-Write. `False` = aus.

### `IservCaches`

- **`book_orders_by_grade`** — jahrgangsweite Bücher-Reihenfolgen (im
  Einstellungen-Dialog vorab pro Bücherliste gesetzt). `grade -> ISBN-Sequenz`.
  Speist beim Klassenladen den Kontext-`book_order` (Jahrgang der Klasse). Reiner
  In-Memory-State, kein DB-/IServ-Write. Wird erst beim Schuljahreswechsel geleert.
- **`hidden_isbns_by_grade`** — ausgeblendete Buchreihen pro Jahrgang
  (Einstellungen-Dialog, „Ausblenden"-Button je Buch). Ausgeblendete ISBNs werden
  beim Scannen nicht mehr als „vorgemerkt" geführt/angezeigt (weder Scanner- noch
  Handy-Anzeige) und sind daher auch nicht buchbar. Reiner In-Memory-State, kein
  DB-/IServ-Write — betrifft nur die lokale Anzeige/Buchungsprüfung. Wird wie
  `book_orders_by_grade` erst beim Schuljahreswechsel geleert.
- **`form_catalog_cache`** — Katalog-Cache für klassenübergreifende
  Warteschlangen (einzeln hinzugefügte Schüler/„Test Config", ggf. aus
  verschiedenen Jahrgängen): `form-Name -> (grade, catalog_isbns)`. Erspart einen
  IServ-Roundtrip pro Schüler-Zuweisung; wird wie `book_orders_by_grade` erst beim
  Schuljahreswechsel geleert.
- **`class_names_cache` / `form_students_cache`** — Caches für die Helfer-
  Lupensuche (read-only IServ-GETs, schuljahrbezogen): Klassennamen + Schüler pro
  Klasse. Sparen IServ-Roundtrips beim wiederholten Öffnen der Suche. Werden wie
  die anderen Caches beim Schuljahreswechsel geleert. Keys: `schoolyear` bzw.
  `"schoolyear|form"`.

### `HelperSession`

- **`student_form`** — Klasse (form) des aktuell zugewiesenen Schülers. Quelle für
  `book_order` + `info["form"]` beim Reconnect, falls der Schüler NICHT in einer
  Queue steht (Helfer-Lupe / `search_call` — dort gibt es keinen `QueueStudent`,
  an dem die Form hing; s. `ws_scanner`-Reconnect). Invariant: nur relevant, wenn
  `student_id is not None`; gesetzt ausschließlich in `assign_student_to_helper`.
  Wird zusätzlich im `as_dict()`-Snapshot ausgeliefert (für die Host-Helferliste,
  s. `student_via_search`).
- **`student_lastname` / `student_firstname`** — Name des zugewiesenen Schülers,
  redundant zum `QueueStudent`, aber die einzige Namensquelle im Host-Snapshot
  für **transiente Lupe-Schüler** (stehen in KEINER Queue, sonst würde
  `findStudentInState` sie nicht finden und die Helferliste zeigte „–"). Setzt in
  `assign_student_to_helper`, gelöscht in `_detach_helper`.
- **`student_via_search`** — `True`, wenn der Schüler per Helfer-Lupe
  (`search_call`) zugewiesen wurde. Der Host zeigt dann in der Helferliste die
  Klasse in Klammern hinter dem Namen (bei Queue-Aufrufen nicht, da der Klassen-
  Tab die Klasse impliziert). Wird bei der Beföderung aus einer Spectator-
  Warteliste vom `SpectatorWaiter.via_search` vererbt.
- **`context_id`** — Klasse (Kontext), die dieser Helfer bedient.
  „Nächster"/„Aufrufen" zieht aus der Queue dieses Kontexts; `None` = noch keiner
  Klasse zugewiesen (Fallback auf den aktiven Kontext, s. `next_pending`). Rein
  transient — kein IServ-/DB-Zustand. Umbindbar per `/api/helper/{token}/class`.
- **`vormerk_isbns` / `lent_isbns`** — Buchungs-Vorabprüfung: `vormerk` = bestellt
  UND Reihe noch nicht auf den Schüler ausgeliehen (= buchbar); `lent` = Reihe
  bereits ausgeliehen (für klare Fehlermeldung). Getrennt gehalten, weil
  `expected_isbns` beides vereint und die Buchbarkeit nicht unterscheiden kann.
- **`load_task`** — In-flight Lade-Task (`load_and_push_helper_student`). Wird
  beim Abbruch des Schülers (`end_student`) gecancelt, damit ein noch laufendes
  `open_student` seinen Worker-Context zurückgibt — sonst leakt der Context, weil
  er erst nach `open_student` in `student_worker_sessions` registriert wird.
- **`end_task`** — verzögerter Disconnect-Teardown („Grace"): beim Trennen des
  Scanner-WS wird `end_student` nicht sofort, sondern nach `_RECONNECT_GRACE_S`
  als Task angestoßen. Lädt der Helfer die Seite neu (Reconnect innerhalb der
  Frist), wird dieser Task gecancelt und der Schüler stattdessen neugeladen
  (s. `ws_scanner`). Ohne Reconnect → echte Trennung → Schüler zurück auf
  `pending`, Worker zu.
- **`peeking`** — View-Toggle „Menü": Helfer hat per Menü-Button die
  Warteschlangen-Ansicht geöffnet, während sein zugewiesener Schüler im
  Hintergrund verbunden bleibt. Solange `True` bekommt dieser Helfer Live-
  `queue_update`s (wie ein unzugewiesener), damit die Queue-Ansicht aktuell
  bleibt. Rein transient — kein Schüler-/IServ-/DB-Zustand. Reset bei
  Schülerwechsel/-ende/Reconnect.

### `StudentSessionB`

- **`book_alert_open`** — ausgemustertes/verliehenes Buch gescannt → Client zeigt
  ein blockierendes Hinweis-Modal ohne eigenen Schließen-Button; erst der Host
  darf es per `/api/clear-book-alert` wieder freigeben. Solange `True`: Scans
  ignorieren.
