# Changelog

> Chronologisches Änderungsprotokoll, **neueste Einträge zuerst**. Zielbild,
> Architektur, Sicherheitsmodell, offene Punkte und Phasenplan stehen in
> `docs/PLAN.md`; Verifiziert-/Offen-Stand in `docs/test_status.md`.
> Ausführliche Spike-/Test-Protokolle liegen als eigene Dateien in `docs/`
> (`docs/spikes/`, `docs/phase2_e2e_2026-06-15.md`,
> `docs/phase4_modus_b_2026-06-15.md`, `docs/hardening_2026-06-18.md`) und
> werden hier nur verlinkt, nicht dupliziert.

## 2026-09-05 — Zettel-Code-Trio als `StationSlipCodes` aus `AppState` gezogen

Die eine Teil-Extraktion, die die im Eintrag darunter gemessene Schwelle
(~25 Aufrufstellen) unterschreitet — und die einzige, die gemacht wurde.
Reiner Struktur-Refactor, keine Verhaltensänderung: 635 Tests grün.

- `station_codes` / `station_code_by_student` / `station_last_code_by_student`
  heißen jetzt `by_code` / `by_student` / `last_by_student` auf der neuen
  Dataclass `StationSlipCodes`, erreichbar über `state.slip_codes`. Die drei
  Dicts wurden ohnehin nur gemeinsam verändert; der Kommentarblock zur
  Nie-Recyceln-Regel wandert mit an die Klasse.
- **Die vier Methoden auf `AppState` bleiben** (`allocate_station_code`,
  `invalidate_station_code`, `station_reactivate_code`,
  `student_id_for_station_code`) — jetzt als einzeilige Weiterleitungen. Sie
  haben 42 Aufrufstellen außerhalb von `state.py`; die zu ändern hätte genau
  den Fehler wiederholt, wegen dem Modus B und Drucker/Display flach bleiben.
  Verschoben wurde die Zusammengehörigkeit der Daten, nicht die Aufruffläche.
- Neu `has_active_code(student_id)`: die vier Direktzugriffe außerhalb von
  `state.py` (`ws.py`, `loan_slip_flow.py`, zweimal in `test_scan_station.py`)
  fragten alle dasselbe — „hat dieser Schüler gerade einen gültigen Zettel?".
  Als `student_id in state.station_code_by_student` war das ein Dict-Detail,
  jetzt ist es die Frage selbst.
- Begründung und Feld-Rationale in `docs/PLAN.md` § State-Feld-Rationale.

## 2026-09-05 — Zwei rote CI-Läufe behoben, pytest-timeout, AppState-Gruppierung abgelehnt

Die am selben Tag frisch eingerichtete CI war im ersten Lauf rot — genau dafür
ist sie da. Beide Befunde waren echte Fehler, keine Einrichtungs-Kosmetik.

- **~35 Tests, beide Linux-Jobs**: `state.state_snapshot()` importiert
  `get_config` **pro Aufruf lokal** aus `server.config` (`state.py:1249`) —
  die Fixtures patchten `get_config` nur in den Route-Modulen, wo jedes Modul
  seinen eigenen `from`-Import auflöst. Ohne `.env` lief dadurch der echte
  `load_config()` und brach mit `SystemExit(ISERV_DOMAIN fehlt)`. Lokal deckte
  die vorhandene `.env` das still zu — deshalb „nicht reproduzierbar".
  ⚠️ Die in den CI-Logs prominenten `AttributeError: '_FakeIServForClasses'
  object has no attribute 'get_student_info'` waren **Captured-Logs bereits
  gefangener, nicht fataler** Fehler — eine falsche Fährte, die zunächst als
  Ursache notiert worden war. Gepatcht in `test_api_guards.py`,
  `test_app_runtime.py`, `test_booklist_empty_endpoint.py` (dort zusätzlich
  `_deps.get_config` für `require_host`) und `test_class_book_order.py`.
- **`pytest-timeout`** ergänzt (`--timeout=300 --timeout-method=thread`),
  Werte und Begründung gespiegelt von `sba-dashboard`: die Suite startet
  Threads und `multiprocessing`-spawn-Kinder, ein Hänger verbrannte bisher
  still die vollen 30 CI-Minuten.

**Bewusst nicht gemacht — AppState-Gruppierung (Modus-B + Drucker/Display).**
Die Regel war: über ~25 Aufrufstellen bleibt ein Cluster flach. Gemessen:

| Cluster | Aufrufstellen |
|---------|---------------|
| Modus B (9 Attribute) | **148** (davon `student_sessions` 48) |
| Drucker/Display (10 Attribute) | **338** (davon `print_queue` 120, `printer_displays` 78, `scan_stations` 54) |

Beide liegen um ein Vielfaches über der Grenze. Anders als `RuntimeSettings`
(5 Toggles) und `IservCaches` (5 Caches), die sich lohnten, sind das hier
keine Randnotizen am State, sondern **die zentrale Arbeitsfläche der App** —
eine Umbenennung dieser Größe ist reines Risiko ohne Lesbarkeitsgewinn.
💡 Einzige tragfähige Teil-Extraktion für später: das Zettel-Code-Trio
(`station_codes`, `station_code_by_student`, `station_last_code_by_student`)
mit zusammen 23 Stellen, davon nur **4 außerhalb** von `state.py` — es hat mit
`allocate_station_code`/`invalidate_station_code`/`station_reactivate_code`/
`student_id_for_station_code` bereits vier eigene Methoden und ist damit
faktisch schon ein Objekt ohne Klasse.

## 2026-09-05 — server/sessions.py in neun Module aufgeteilt (Welle 6)

Reiner Struktur-Refactor, keine Verhaltensänderung: `server/sessions.py`
(2976 Zeilen, 92 Funktionen, mindestens sechs unabhängige Aufgaben) ist jetzt
eine 250-Zeilen-Fassade ohne eigene Logik. Details, Modul-Übersicht und die
Zyklus-Begründung stehen in `docs/PLAN.md` §2.1. Neue Dateien:
`session_tokens.py`, `scan_booking.py`, `book_visibility.py`,
`loan_slip_flow.py`, `device_broadcast.py`, `session_lifecycle.py`,
`helper_queue.py`, `scan_station_session.py`, `device_persistence.py`.
Jede der ~40 bisherigen Importstellen (Routen, Tests) blieb unverändert —
`sessions.py` re-exportiert alle Namen mit explizitem `__all__`. Vollständige
Test-/Ruff-Verifikation nach jedem einzelnen ausgelagerten Modul (nicht erst
am Ende), s. `docs/test_status.md`.

## 2026-08-13 — Drucker-Scanner: Klassenpräfix, Beep, 10s-Kamerapause, Scan-Station-Auto-Fertig

Vier kleinere Nachbesserungen:

- **Klassenpräfix entfernt**: Die Scanner-Karte am Drucker-Display zeigte
  bisher „Klasse 10a" statt „10a" — inkonsistent mit der normalen
  Druckerwarteschlange, die das Präfix über `print_queue.slip_name` bereits
  abschneidet. `_handle_drucker_scan` (`server/routes/ws.py`) schneidet das
  Präfix jetzt genauso ab (`form_clean`, einmal berechnet, in allen drei
  Payloads verwendet).
- **Beep am Drucker-Scanner**: `web/drucker-scan.js` spielt jetzt sofort
  einen Ton (`Beeper.playBeep()`, gleicher Mechanismus wie der Drucker-
  Display-Fertigton), sobald ein Code erkannt wurde — für Kamera- und
  manuelle Eingabe gleichermaßen (beide laufen über `onScanSuccess`).
- **10s statt 5s + Kamerapause**: `_SCANNER_RESULT_TTL_S` (Anzeigedauer der
  Scanner-Karte am Drucker-Display) auf 10s erhöht. Der Drucker-Scanner
  pausiert für dieselbe Dauer die Kamera komplett (`html5QrCode.pause(true)`/
  `.resume()`) statt nur erkannte Codes zu ignorieren — kein Decoding, kein
  Videobild, bis das Ergebnis am Display abgelaufen ist.
- **Scan-Station-Schüler schalten nach Druck automatisch weiter**: bisher
  landete ein Scan-Station-Schüler nach dem physischen Leihschein-Druck in
  keinem Auto-Übergang — anders als Modus-B-Schüler (die per „Leihschein
  erhalten"-Bestätigung im Client in den Unterschriften-Modus bzw. auf
  „abgeschlossen" wechseln, s. `confirm_slip_received`) haben Scan-Station-
  Schüler keine Session und damit keinen Bestätigungsweg — der Host musste
  bisher manuell abschließen. `_mark_slip_printed` (`server/sessions.py`)
  erkennt jetzt Scan-Station-Schüler (vergebener Zettel-Code,
  `state.station_code_by_student`) und schaltet SOFORT bei Druckende weiter:
  in den Unterschriften-Modus (`done_signed` aktiv) oder direkt auf
  „abgeschlossen" (`end_student`) — unabhängig vom auslösenden `slip_trigger`
  (Automatisch/Betreuer-/Schülerauslöser über den Drucker-Scanner).
- Tests: `tests/test_scan_station.py` (3 neue: Unterschriften-Modus,
  Auto-Fertig ohne Unterschreiben, Nicht-Station-Schüler bleibt unberührt —
  rufen `sessions._mark_slip_printed` direkt auf, da
  `PrintQueue._mark_slip_printed_after_completion` intern den globalen
  `state.get_state()`-Singleton statt der Test-lokalen State-Instanz
  verwendet), `tests/test_ws_drucker_scan.py` (Klassenpräfix-Assertion). `uv
  run pytest` (635 Tests grün) + `uvx ruff check server/ automation/ tests/`
  sauber. Kein Browser-/Hardware-Livecheck.

## 2026-08-13 — Drucker-Scanner-Nachbesserungen: Reise-Animation, Doppel-Scan-Blitzer, kombinierte Reihenfolge

Drei Nachbesserungen zum Drucker-Scanner (s. Eintrag darunter):

- **„Zettel entsorgen"-Hinweis**: im `ready`-Fall (Auftrag erfolgreich
  gesendet) steht am Drucker-Scanner-Kästchen jetzt immer zusätzlich (eigene
  Zeile, auch ohne aktives „Leihschein unterschreiben") „Du kannst den Zettel
  mit dem Schülercode jetzt entsorgen." (`scannerBelowLines` in
  `web/drucker-display.js`).
- **Reise-Animation für frisch erzeugte Aufträge**: das Namens-Kästchen an der
  Scanner-Karte ist jetzt dasselbe Kästchen, das nach Ablauf der 5s-Anzeige
  an seiner echten Position in der zentralen Warteschlange oder auf einem
  Drucker weiterlebt — kein zweites Kästchen. Serverseitig liefert
  `_handle_drucker_scan` (`server/routes/ws.py`) die `job_id` im `ready`-
  Payload (neue `PrintQueue.active_job_id_for_student`); clientseitig wird der
  Auftrag so lange aus Warteschlange/Drucker-Karte unterdrückt
  (`readyTravelJobIds` in `renderQueue`), bis `revertScannerAndFlip` nach
  Ablauf einen erzwungenen Re-Render mit einem „Seed-Rect" auslöst — der
  bestehende FLIP-Mechanismus interpoliert dank des geteilten `job_id`-
  Schlüssels automatisch von der alten (Scanner) zur neuen (Warteschlange/
  Drucker) Position.
- **Doppel-Scan-Blitzer**: im `already`-Fall (Auftrag existiert schon) bleibt
  die Scanner-Karte unverändert (kein Reise-Effekt), stattdessen wird das
  bereits existierende Kästchen in Warteschlange/Drucker-Karte einmalig gelb
  umrandet (`markFlaggedJob`/`.dd-order-flagged`, Mirror des grünen „fertig"-
  Blitzers `markFinishedJob`).
- **Kombinierte Drucker+Scanner-Reihenfolge**: die Host-Einstellungen eines
  Drucker-Displays zeigen Drucker und Scanner jetzt in EINER Box-Reihe
  („Drucker und Scanner (N)"), frei durcheinander verschiebbar (Drag zwischen
  beiden Arten). Neues `PrinterDisplaySession.item_order` (Schlüssel
  `"printer:<id>"`/`"scanner:<id>"`, `AppState._ordered_display_items` löst
  gegen die aktuelle Zuweisung auf, nicht gelistete Items hängen stabil ans
  Ende), neuer Endpunkt `/api/drucker-display/reorder-items`. Dieselbe
  Reihenfolge bestimmt jetzt auch die Spaltenreihenfolge am physischen
  Drucker-Display (`view["card_order"]`, `web/drucker-display.js` baut die
  Kartenreihe daraus statt „erst alle Drucker, dann alle Scanner"). Drucker
  werden dabei über den (laufzeitstabilen) Namen persistiert (wie
  `assigned_printer_names`), Scanner über die stabile `scanner_id` direkt.
- Tests: `tests/test_print_queue.py::test_active_job_id_for_student`,
  `tests/test_ws_drucker_scan.py` (job_id-Payload-Assertions ergänzt),
  `tests/test_drucker_display.py` (reorder-items-Endpunkt,
  `card_order`-Interleaving, stabiles Anhängen ungelisteter Items),
  `tests/test_device_persistence.py` (item_order-Roundtrip). `uv run pytest`
  (632 Tests grün) + `uvx ruff check server/ automation/ tests/` sauber.
  **Kein Browser-/Hardware-Livecheck** für die visuellen Effekte (Reise-
  Animation, Blitzer, Drag zwischen Drucker/Scanner-Boxen) — s.
  `docs/test_status.md`.

## 2026-08-13 — Drucker-Scanner: Schülerauslöser-Druck an der Scan-Station

Neues persistentes Gerät **Drucker-Scanner** (`/drucker-scan`, wie Drucker-
Display/Scan-Station/QR-Display: Token-Pairing, Host-Freischaltung per Name,
Theme, Kamera/Manuell-Eingabeart vom Host vorgegeben). Steht physisch an
einem oder mehreren Druckern und löst dort den Leihschein-Druck für Scan-
Station-Schüler (Kiosk ohne Handy) mit `slip_trigger == "student"` aus —
bisher verhielt sich „Selbstauslöser" für sie provisorisch wie „Automatisch"
(sofortiger Druck ohne eigenes Zutun).

- **State/Persistenz** (`server/state.py`, `server/printer_scanner_store.py`):
  neue `PrinterScannerSession` (Pairing-Felder + transientes Scan-Ergebnis mit
  5s-TTL: `last_scan_status/code/payload/expires_at`), `AppState.
  printer_scanners`. `PrinterDisplaySession.assigned_scanner_ids` (Mirror
  `assigned_printer_ids`, ein Scanner kann mehreren Displays zugeordnet sein,
  referenziert über den stabilen Token — kein Namens-Remapping wie bei
  Druckern nötig). `AppState.printer_display_view` hängt die zugeordneten
  Scanner-Karten an die Display-Push-Nachricht.
- **Routen** (`server/routes/drucker_scan.py` neu, `drucker_display.py`
  erweitert): Seite + `qr/enable/label/theme/input-mode/forget`-Endpunkte,
  plus `/api/drucker-display/assign-scanners`.
- **WS-Handler** `ws_drucker_scan` (`server/routes/ws.py`): Pairing-
  Lebenszyklus wie `ws_drucker_display`. Ein Scan (`{"type":"scan","code":…}`)
  löst zuerst den Zettel-Code auf (`student_id_for_station_code`), ersatzweise
  einen Buchcode (`get_book_by_code`, read-only) über dessen `loaned_to_id`,
  klassifiziert dann: unbekannt (Code/Schüler nicht auflösbar oder Status
  „fertig") → `already` (Job wartet/druckt/bereits gedruckt) → offene
  vorgemerkte Bücher (`sessions.pending_vormerk_isbns_for`, inkl. Ausblenden-/
  Bestand-leer-Filter) → `ready` (Job anlegen, `station_display_gate=True`,
  Mirror des Auto-Pfads). Ergebnis geht NICHT an den Scanner selbst, sondern
  per `broadcast_scanner_result` an die zugeordneten Drucker-Display(s) — der
  Scanner-Bildschirm bleibt bewusst stumm (nur Kamera/Eingabefeld).
- `ws_scan_station`s `print_mode`-Handler: `"student"` prüft jetzt
  `sessions.eligible_drucker_scanners_for` (autorisiertes, verbundenes
  Display mit erlaubtem, sichtbarem Drucker UND zugeordnetem, verbundenem
  Scanner). Gibt es welche, wird **kein** Auftrag erzeugt — die Station zeigt
  „Bitte scanne deinen Schülercode an X, Y oder Z" (`web/scan-station.js`,
  Namen kommasepariert, letzter mit „oder"). Sonst identischer Fallback wie
  „Automatisch" (inkl. `station_print_needs_host`, jetzt zusätzlich mit
  sichtbarem Hinweistext in der Schülerkartei, `web/host-render.js::
  renderCtxNowServing`).
- **Drucker-Display** (`web/drucker-display.js`/`.html`): neue Scanner-Karte
  im selben Grid wie die Drucker-Karten, sechs Zustände (Default/„wird
  geprüft"/bereit/bereits im System/offene Bücher/unbekannt) mit Schritt-Label
  + Namens-Kästchen, das je nach Zustand die Position wechselt — läuft
  vollständig über den bestehenden FLIP-Mechanismus (`flipFromOldRects`,
  gleiche Klassen/Datenattribute wie die Drucker-Karten), kein eigener
  Animationscode. Client-seitiger 5s-Revert-Timer (Mirror `printedTimers`).
- **Host-UI**: dritter fester Reiter „Scanner" in der „Drucker"-Karte
  (Geräteverwaltung, Mirror Scan-Stations-Reiter), zweiter Box-Grid-Abschnitt
  „Scanner" im „Displays"-Reiter (Zuordnung, Mirror Drucker-Box-Grid inkl.
  Drag-Umsortieren, `kind`-Parameter unterscheidet Drucker-/Scanner-Boxen).
- Neues `web/drucker-scan.html`/`.js`: Registrierungs-/Gesperrt-Views wie
  Drucker-Display, Kamera (html5-qrcode) oder manuelles Feld je nach
  gepushtem `input_mode`, sendet nur `{"type":"scan"}` — kein eigenes
  Feedback.
- Test: `tests/test_ws_drucker_scan.py` (Pairing-Lebenszyklus, alle sechs
  Klassifikationszweige inkl. Buchcode-Auflösung, `print_mode`-Aufteilung
  student/eligible vs. Fallback-auto) + `tests/test_device_persistence.py`
  (Roundtrip `printer_scanners`, `assigned_scanner_ids`) + angepasster
  `tests/test_state_contract.py` (neues `printer_scanners`-Feld im
  Snapshot-Vertrag). `uv run pytest` (626 Tests grün) + `uvx ruff check
  server/ automation/ tests/` sauber. **Kein Browser-/Hardware-Livecheck**
  (s. `docs/test_status.md`).

## 2026-08-13 — Scan-Station meldet Schüler auch beim Abschließen ab

- `end_student` (`server/sessions.py`): Bisher wurde die Scan-Station-Bindung
  nur beim **Trennen/Reset** (`queue_status == "pending"`) gelöst — der Zettel-
  Code zusätzlich entwertet. Beim **Abschließen/Überspringen** (`done`/`skipped`/
  `absent`) blieb der Schüler stale auf der Station stehen. Jetzt löst jeder
  finale Durchlauf die Station-Bindung via `release_station_student` (gleicher
  Pfad wie beim Trennen → `released`-Event → Station fällt auf „Zettel-Code
  scannen" zurück). Der Zettel-Code bleibt dabei unangetastet: ein Re-Scan wird
  via `resolve_station_code` am `done`-Status ohnehin abgelehnt, und nach einem
  Reset-Queue (`done → pending`) ist der alte Zettel ohne Neudruck wieder
  nutzbar. Grund: Host-Abschluss soll die Station genauso abmelden wie Trennen.
- Test: `tests/test_scan_station.py::test_end_student_done_releases_station`
  (Suite: 610 Tests, 608 grün — 2 vorbestehende, unabhängige Failures aus der
  laufenden `printer_scanners`-Arbeit in `state.py`/Persistenz, nicht durch
  diesen Eintrag verursacht).

## 2026-08-13 — Host: Druckbutton bleibt ab Druckermodus sichtbar + Nachdruck

- Druckbutton in der Schüler-Kachel „Aktuell in Ausgabe" (`renderCtxNowServing`,
  `studentClientPrint`): Der Button erscheint weiter erst mit Erreichen des
  Druckmodus, bleibt danach aber bis zum Abschluss sichtbar — auch während/
  nach dem Druck und im Unterschriften-Modus (links neben dem rechtsbündigen
  Unterschrift-Button). Bisher wurde er bei `slip_printing`/`slip_printed`
  ausgeblendet. gilt für Schülerclients wie Scan-Station-Schüler (Pfad via
  `slip_trigger == 'helper'` bzw. `station_print_needs_host`).
- Nachdruck möglich: Ein Klick NACH dem ersten erfolgreichen Druck legt den
  Auftrag immer als Host-Auftrag an (`role="host"`, `host_sid`, kein
  `student_token`), unabhängig davon, wer den ersten Druck ausgelöst hat
  (Host-Betreuerauslöser, Schüler-Selbstauslöser oder Scan-Station) und
  unabhängig vom `slip_trigger`. Umgesetzt in `routes/slips.py::
  print_loan_slip` über `is_reprint` (`slip_printed`) — `student_client`
  greift nur noch für den Erstdruck. `_mark_slip_printed` ist idempotent,
  der Leihschein-Marker verändert sich durch den Nachdruck nicht. Ein
  laufender Erstdruck bleibt über `in_flight_student_ids()` doppelt blockiert.
- Tests: `test_print_loan_slip_route.py` — alter „rejects when already
  printed"-Test ersetzt durch Reprint-als-Host-Job-Tests (helper + auto
  trigger) plus In-Flight-Block-Test.

## 2026-08-13 — Scan-Station: Druckermodus

- Sind für den angemeldeten Schüler alle vorgemerkten Bücher gescannt,
  wechselt die Scan-Station jetzt (wie der Schülerclient) in einen
  Druckermodus — dieselbe `#view-print`-Ansicht (Name/Klasse fix oben,
  Icon/Titel/Statustext vertikal zentriert per `positionScrollCenter`-Port,
  Info-Kasten), aber ohne Auslöser-/„Leihschein erhalten"-Buttons: die
  Station löst automatisch aus und meldet sich nach einem festen 30-s-Timer
  (kein Reset) ab, statt auf eine Bestätigung zu warten.
- Bei `slip_trigger` „Automatisch"/„Selbstauslöser" (Selbstauslöser verhält
  sich vorübergehend wie Automatisch — noch kein eigener Button) wird der
  Leihschein-Druckauftrag nur erzeugt, wenn mindestens ein für die Klasse
  erlaubter Drucker gerade auf einem verbundenen Drucker-Display sichtbar
  ist. Neues `PrintJob.station_display_gate`-Feld + `sessions.
  displayed_printer_ids()`: `PrintQueue._claim_fills` dispatcht einen
  solchen Auftrag nur an einen gerade sichtbaren Drucker, ohne dabei andere
  wartende Aufträge zu blockieren (die Suche geht zum nächsten Auftrag in
  der Warteschlange weiter).
- Verschwindet die Sichtbarkeit, während der Auftrag noch **wartet**
  (`status == "queued"`), pausiert er automatisch — die dynamische Prüfung
  läuft bei jedem Scheduler-Tick neu, ein zurückkehrender Drucker nimmt ihn
  ohne Zutun wieder auf (`wake()` zusätzlich explizit bei Display-
  Freischaltung/-Zuweisung/-Reconnect, damit nicht auf den nächsten
  zufälligen Trigger gewartet wird). Der Host sieht in der Schüler-Kachel
  „Aktuell in Ausgabe" währenddessen einen gelben Hinweis „Kein erlaubter
  freigegebener Drucker verfügbar" samt eingebettetem Drucker-Auswahlmenü
  (`PrintQueue.station_gate_snapshot`, neuer Endpoint `POST
  /api/print-queue/{job_id}/adopt-station` → `PrintQueue.
  host_adopt_station_job`): Klick befördert den Auftrag zu `role="host"`
  und reiht ihn an der Host-Rangposition neu ein. Bereits dispatchte
  Aufträge zeigen keinen Hinweis mehr (nicht mehr umbuchbar).
- War von Anfang an — bevor überhaupt ein Auftrag erzeugt wurde — kein
  erlaubter Drucker sichtbar, entsteht **kein** Auftrag und **kein** gelber
  Host-Hinweis. Die Station schickt den Schüler stattdessen zum Host; dessen
  normaler Druckbutton in „Aktuell in Ausgabe" wird dafür unabhängig vom
  Klassen-`slip_trigger` freigeschaltet (neues `QueueStudent.
  station_print_needs_host`-Feld, zurückgesetzt bei `reset_progress`/sobald
  wieder ein Auftrag erzeugt wird).
- Statustexte: „Der Leihschein wartet in der Druckerwarteschlange. Bitte
  achte auf die Druckeranzeige(n)." (Singular/Plural über die neue
  `sessions.relevant_display_count()` — wie viele Displays zeigen gerade
  mindestens einen erlaubten Drucker) bei erfolgreichem Enqueue; „Bitte
  wende dich an einen Betreuer, damit dein Leihschein gedruckt werden
  kann." einheitlich für Betreuerauslöser UND den „kein Drucker"-Fall
  (kein Auftrag). Ist an der Klasse „Leihschein unterschreiben" aktiv, hängt
  der Server `done_signed`/`recipient` an jede Statusmeldung an (außer beim
  Barcode-Platzhalter); die Station zeigt dann nach einer Leerzeile „Nach
  dem Druck den Leihschein bitte unterschreiben und beim Betreuer/Lehrer
  abgeben." (`#print-text` nutzt jetzt `white-space: pre-line`).
- Nutzer-Korrekturen im Feinschliff: der 30-s-Countdown läuft jetzt wie der
  Leerlauf-Countdown im Scanmodus unter der Klasse (gleiche Position,
  gleiche Schrifteigenschaften — `#countdown, #print-countdown` teilen sich
  dieselbe CSS-Regel); der Fokus-Warnbanner (manueller Eingabemodus) bleibt
  im Druckermodus verborgen; nach der Abmeldung springt der Fokus im
  manuellen Modus automatisch zurück ins Eingabefeld.
- Vor der Anmeldung unterscheidet die Station jetzt drei Fälle statt nur
  „ist es ein 4-stelliger Code": ein gescannter Buch-Barcode (irgendein
  Buch, geprüft rein lesend über `get_book_by_code` — nicht mehr nur an der
  Zeichenform erkannt) zeigt gelb „Bitte zunächst Schülercode scannen"; ein
  weder als Zettel-Code noch als Buch erkannter Wert zeigt rot „Ungültiger
  Code" (`code_error.kind` = `"book"`/`"invalid"`). Der Client sendet dafür
  jeden gescannten Wert an den Server, nicht mehr nur 4-stellige lokal
  gefilterte Codes — die bestehende 10/min-Rate-Begrenzung des
  `student_code`-Kanals deckt das bereits ab.
- 607 Offline-Tests grün (neu u. a. `_claim_fills`-Gating/-Wiederaufnahme,
  `host_adopt_station_job`, `displayed_printer_ids`/
  `relevant_display_count`, Buch-vs-ungültig-Unterscheidung vor der
  Anmeldung). Echter Live-Check (mehrere Drucker-Displays, Drucker kommt/
  geht während ein Auftrag wartet, Unterschreiben-Hinweis am echten
  Drucker) steht noch aus.

## 2026-08-13 — Scan-Station-Zettel: Layout, Sortierung, Dateiname

- Abhak-Kästchen vor „Noch vorgemerkt" jetzt bündig mit Ober-/Unterkante der
  zugehörigen Textzeile (Rect-Offset in `build_sheet_pdf` nachjustiert).
- „Bereits ausgeliehen" erscheint auf dem Zettel nur noch, wenn der Schüler
  tatsächlich schon etwas ausgeliehen hat (`section(..., hide_if_empty=True)`);
  „Noch vorgemerkt" zeigt bei leerer Liste weiterhin „—".
- „Noch vorgemerkt" wird jetzt nach der klassenweit konfigurierten
  Bücherliste-Reihenfolge sortiert (`get_book_order_for_form`, wie im Client),
  statt alphabetisch. „Bereits ausgeliehen" (Ausgabezeitpunkt absteigend) und
  die Bestand-leer-/Ausgeblendet-Filterung waren bereits korrekt.
- Unter den Bücherlisten steht jetzt eine nummerierte 4-Schritt-Anleitung
  „So gehst du vor:" (Bücher holen → zur Scan-Station gehen → eigenen Barcode
  scannen → Bücher einzeln scannen) mit mehr Abstand zu den Listen.
- Neuer PDF-/Download-Dateiname `Scanstation <Klasse> <Nachname>, <Vorname>`
  (`_station_sheet_filename`/`_station_sheet_label` in `server/sessions.py`,
  analog zu den bestehenden Leihschein-Helfern) statt des generischen
  `scan_station_<student_id>`.

## 2026-08-12 — Geräte-Persistenz: Aufräumen erst nach 5 min Serverlaufzeit

- Helfer, Drucker-Displays und Scan-Stationen, die in einem Serverlauf nie
  per WebSocket verbunden waren, wurden bisher immer aus der Persistenz
  entfernt. Nach einem kurzen Lauf (Neustart kurz nach dem Start, Fehlstart,
  schnelles Durchstarten beim Aufbau) waren die Geräte dadurch beim
  übernächsten Start endgültig weg, obwohl sie gar keine Zeit zum Reconnect
  hatten.
- Die Verwerfungsregel greift jetzt nur noch, wenn der Lauf mindestens
  `sessions.PRUNE_MIN_UPTIME_S` (5 min) gedauert hat; bei kürzeren Läufen
  werden alle Einträge weitergeschrieben. Basis ist die neue monotone
  Startzeit `AppState.started_at_monotonic` (in `app.lifespan` gesetzt).
- Unverändert: der IP-Fingerprint (Regel 1) und die `authorized`-Bedingung
  bei Displays/Stationen.

## 2026-08-12 — Drucker-Display: Fertigmeldung mit Ton und Highlight

- Ein neu fertig gedruckter Auftrag löst am Drucker-Display einmalig den
  vorhandenen Beep-Ton aus.
- Das zugehörige „Gedruckt"-Kästchen wird fünf Sekunden lang grün umrandet;
  die Umrandung blendet anschließend transparent aus. Die Animation wird bei
  zwischenzeitlichen WebSocket-Updates zeitlich fortgesetzt statt neu gestartet.

## 2026-08-12 — Lehreransicht: Offene Leihschein-Eingänge zuerst

- Innerhalb der Statusgruppe „Ausgabe abgeschlossen" stehen Schüler mit
  aktivem, noch nicht angeklicktem Button „Leihschein entgegengenommen" jetzt
  zuerst. Danach folgt die alphabetische Sortierung nach Nachname, Vorname und
  stabiler Schüler-ID. Bereits angeklickte oder erledigte Eingänge stehen
  dahinter.

## 2026-08-12 — Lehreransicht: Standardsortierung nach Status

- Die Schülerliste startet jetzt standardmäßig mit „Nach Status".
- Die Reihenfolge lautet: **aktiv → offen → abwesend → abgeschlossen →
  übersprungen**. Innerhalb jeder Statusgruppe gilt **Nachname → Vorname →
  stabile Schüler-ID**. Die alphabetische Sortierung bleibt auswählbar.

## 2026-08-12 — Lehreransicht: Host-Übersprungen und Abwesend getrennt behandeln

- Die Lehreransicht rendert den Rücknahme-Button „Nicht abwesend" nur noch
  für `status == "absent"`. Host-übersprungene Schüler (`skipped`) erscheinen
  als „Übersprungen" und bleiben ohne Lehreraktion.
- Die bestehende `auto_skipped`-Semantik bleibt erhalten: automatisch beim
  Klassen-Laden fertig gesetzte Schüler werden in der Lehreransicht als
  `skipped` dargestellt und bleiben aktionslos.
- Modul-/Plantexte und der Teacher-Test für das Rücknahme-Gate verwenden nun
  den tatsächlichen Lehrer-Übergang `pending <-> absent`; `skipped` bleibt der
  Host-/Auto-Skip-Status.

## 2026-08-12 — Lehreransicht: fünf Statuszähler, irreversible Leihschein-Aktion und Sortierung

- Die fünf Lehrerzähler bleiben getrennt: **abgeschlossen**, **aktiv**,
  **offen**, **übersprungen** und **abwesend**. `teacher_snapshot()` ordnet
  jeden Schüler genau einem Zähler zu; `done + auto_skipped` wird weiterhin nur
  in der Lehreransicht als `skipped` gezählt.
- `POST /api/teacher/slip-collected` ist jetzt eine reine Setz-Aktion:
  `collected=true` setzt den Marker idempotent auf `true`, `collected=false`
  wird abgewiesen. Session-, Klassen-, Status- und Druck-Gates bleiben
  erhalten. `reset_progress()` löscht den Marker weiterhin bewusst für einen
  neuen Durchlauf.
- Die Checkbox wurde durch einen Button ersetzt. Nach Erfolg ist er
  deaktiviert und als erledigt markiert; während des Requests verhindert er
  Doppelklicks, bei einem Fehler wird er wieder freigegeben. Der bestehende
  Text „Leihschein & Bücherstapel entgegengenommen" bleibt bei `helper_scanned`
  erhalten.
- Die Schülerliste ist per Auswahl alphabetisch oder nach Status sortierbar.
  Die Statusreihenfolge ist `active -> pending -> absent -> done -> skipped`;
  innerhalb der Gruppe wird nach Nachname, Vorname und stabiler Schüler-ID
  sortiert. WebSocket-Updates behalten den Modus; die Eingangsdaten werden
  nicht mutiert.
- Verifiziert mit `tests/test_teacher.py`, `tests/test_queue_progress.py`, der
  vollständigen Offline-Suite, Ruff, JavaScript-Syntaxprüfung, `git diff
  --check` und einem lokalen Headless-Browsercheck.

## 2026-08-12 — Helfer/Drucker-Displays/Scan-Stationen überleben einen Serverneustart

- **Warum:** Bisher gingen Helfer-Registrierungen, freigeschaltete Drucker-
  Displays (Name/Theme/Drucker-Zuweisung) und freigeschaltete Scan-Stationen
  (Name/Theme/Eingabeart) bei jedem Serverneustart verloren — Niklas wollte,
  dass nach einem Neustart mit demselben Token (URL bereits auf Handy/
  Display/Station offen) wieder alles da ist.
- **Architektur:** drei neue, schmale Sync-IO-Store-Module (Spiegel von
  `booklist_store.py`/`printer_store.py`, jeweils eigene JSON statt einer
  gemeinsamen Datei — andere Domäne, andere Validierung):
  `server/helper_store.py` → `data/helpers.json` (nur `token`+`name`, kein
  Klassen-/Queue-/Worker-Zustand), `server/printer_display_store.py` →
  `data/printer_displays.json` (Drucker-Zuweisung über den *Namen*
  referenziert, nicht die nur laufzeitstabile Pool-`id`, s.
  `printer_store.py`), `server/scan_station_store.py` →
  `data/scan_stations.json`. Persist-Hooks (jetzt zentral als
  `sessions.persist_helpers/persist_printer_displays/persist_scan_stations`,
  nicht in den einzelnen `routes/*.py`, weil `routes/ws.py` sie ebenfalls
  braucht) laufen bei jeder Konfigurationsänderung (add/enable/assign/label/
  theme/input-mode/forget); Laden passiert in `app.py`s Lifespan-Startup,
  nach dem Drucker-Pool-Load (Namen → frische Pool-`id`s auflösen).
- **Zwei Verwerfungsregeln (Nachbesserung auf Niklas' Wunsch), unabhängig
  von der reinen Datei-IO:**
  1. **Andere Server-IP:** `sessions.server_lan_ip()` (Auto-Erkennung wie
     bei QR-URLs, ohne Request) wird als Fingerprint mitgespeichert; weicht
     die beim Laden erkannte IP ab, wird die jeweilige Persistenz GAR NICHT
     geladen — die alten Token stecken in URLs, die auf die alte IP zeigen
     und auf einem anderen Netz ohnehin nie erreichbar wären.
  2. **Nie verbunden im Serverlauf:** neues transientes Feld
     `connected_since_start: bool` auf `HelperSession`/
     `PrinterDisplaySession`/`ScanStationSession`, gesetzt bei der ersten
     echten WS-Verbindung dieses Laufs (`routes/ws.py`). Die drei
     `persist_*`-Funktionen schreiben nur Einträge mit
     `connected_since_start=True` — ein Eintrag (auch ein wiederhergestellter),
     der einen kompletten Lauf über nie verbunden war, fällt beim nächsten
     Neustart automatisch raus. `app.py`s Lifespan ruft die drei Funktionen
     zusätzlich beim Shutdown auf, damit das auch ohne ein anderes
     auslösendes Ereignis im selben Lauf greift.
- **Gotcha (Testpollution):** die neuen Persist-Aufrufe in `routes/ws.py`
  hätten bei praktisch jedem WS-Test (nicht nur den beiden
  Drucker-Display-/Scan-Station-Routen-Testdateien) in die echten
  `data/*.json` geschrieben. Fix: `autouse`-Fixture
  `_isolate_device_persistence` in `conftest.py` leitet alle drei
  Store-Pfade für JEDEN Test automatisch auf `tmp_path` um.
- Neue `tests/test_device_persistence.py` (7 Tests) prüft beide
  Verwerfungsregeln direkt auf Store- und Sessions-Ebene. 576 → **583**
  Tests grün, Ruff clean. Details:
  `_logs/2026-08-12_sba_geraete_persistenz.md`.

## 2026-08-12 — Fix: Bestand-leer-/Ausblenden-Änderungen erreichen jetzt auch laufende Scan-Station-Sessions live

- **Warum:** Niklas meldete, dass an der Scan-Station eine als „Bestand
  leer" oder „Ausgeblendet" markierte Buchreihe erst nach Neuladen/Neu-
  Anmelden aus der Liste verschwand, nicht sofort wie am Schülerclient. Der
  Repush-Fanout beim Speichern der Buchreihen-Einstellungen
  (`POST /api/booklist-empty`/`POST /api/booklist-hidden`) lief bisher nur
  über `state.helper_sessions` und `state.student_sessions` — eine an einer
  Scan-Station angemeldete Session (`state.scan_stations`) wurde dabei
  schlicht nie mitgenommen, obwohl `ScanStationSession` dieselben Felder wie
  `HelperSession`/`StudentSessionB` trägt und `repush_booklist` sie darüber
  genauso bedienen kann.
- **`server/sessions.py::repush_for_changed_empty_isbns`** (Bestand-leer,
  global) und **`server/routes/booklists.py::set_booklist_hidden`**
  (Ausblenden, jahrgangsgebunden über `_student_in_grade`) iterieren jetzt
  zusätzlich über `state.scan_stations.values()` und stoßen bei betroffenen
  Sessions denselben `repush_booklist(..., helper=False)` an wie für Modus B.
- **`web/scan-station.js`**: neuer `booklist_update`-Handler (bisher
  komplett ungehandhabt — die Nachricht kam vorher nie an, da der Server sie
  nie schickte) — Gegenstück zum bereits vorhandenen Handler in
  `web/student.js`, ersetzt nur Bücherliste + Reihenfolge, lässt
  `scannedIsbns`/`scanOrder` unangetastet. Kein Druckmodus an der Station,
  daher kein `maybeEnterDruckmodus()`-Äquivalent.
- **Neue Tests:** `tests/test_booklist_empty_endpoint.py`
  (`test_set_booklist_empty_repushes_affected_scan_station_session`,
  `test_set_booklist_hidden_repushes_affected_scan_station_session` — neu
  `hub.ws_sent` in `_FakeHub` zum Erfassen von `send_websocket`-Aufrufen,
  vorher nur `send_scanner` erfasst). 586 Tests grün (583 → 586), Ruff und
  JavaScript-Syntaxprüfung grün.

## 2026-08-12 — „Bestand leer"-Reihen verschwinden aus Schülerclient/Scan-Station/Zettel

- **Warum:** Bisher war die Bestand-leer-Markierung nur für den Helfer-
  Client (Modus A) sichtbar — der Schülerclient (Modus B), die Scan-Station
  und der Scan-Station-Zettel zeigten eine als „Bestand leer" markierte
  Buchreihe unverändert als ganz normal vorgemerkt an. Niklas wollte, dass
  solche Reihen dort gar nicht erst auftauchen, solange sie tatsächlich
  nicht ausgeliehen sind — sobald ein Exemplar doch ausgegeben wird, soll
  die Reihe ganz normal als „ausgeliehen" erscheinen. Die Reihe soll dabei
  weiterhin regulär buchbar bleiben (kein `apply_hidden_books`-Äquivalent)
  und die Druckmodus-Prüfung im Schülerclient soll eine so ausgeblendete
  Reihe wie eine tatsächlich ausgeblendete behandeln — auch dann, wenn sie
  erst NACH dem Laden (durch eine Änderung der Buchreihen-Einstellungen)
  als „Bestand leer" markiert wird und der Schüler die übrigen Reihen
  bereits fertig hat.
- **`server/sessions.py`**: neue `apply_empty_stock_visibility(info,
  empty_isbns)` — entfernt eine Bestand-leer-Reihe komplett aus
  `info["books"]`, außer sie ist bereits `status == "ausgeliehen"`.
  Eingehängt in `hydrate_student_info` für `is_helper=False` (deckt alle
  vier Lade-/Reconnect-Pfade von Modus B und Scan-Station ab) sowie separat
  in `_load_and_activate_station_student` (gemeinsamer Kern von
  `print_station_sheet_for`/`activate_station_student` — betrifft also auch
  den gedruckten Zettel). Beide Aufrufstellen wenden den Filter bewusst
  NACH der Buchungsmengen-Berechnung
  (`expected_isbns_from_info`/`booking_isbn_sets_from_info`) und nach
  `init_book_progress` an: die Reihe bleibt dadurch weiterhin buchbar, und
  der Host-„X/Y"-Zähler (inkl. `books_empty_outstanding`) rechnet
  unverändert mit der vollen Liste.
- **Kein Client-Code nötig für die Druckmodus-Prüfung:** `allVorgemerkteDone()`
  in `web/student.js` filtert bereits auf `currentBooks`, das dank des
  serverseitigen Filters die offene Bestand-leer-Reihe gar nicht mehr
  enthält. Wird eine Reihe nachträglich als „Bestand leer" markiert (Host-
  Einstellungen-Dialog → `POST /api/booklist-empty` →
  `repush_for_changed_empty_isbns` → `repush_booklist` →
  `booklist_update`), war der `maybeEnterDruckmodus()`-Aufruf im
  `booklist_update`-Handler bereits vorhanden (ursprünglich für
  „Ausblenden") — er greift jetzt automatisch mit, ohne Änderung.
- **Leihschein-„fehlt"-Hinweis** (`_missing_stock_subjects_for`/
  `overlay_missing_stock_note` in `server/sessions.py`/`server/loan_slip.py`)
  war bereits unabhängig von der Auslöserquelle (Helfer/Host/Schülerclient
  rufen alle denselben `print_loan_slip_for` auf) — keine Änderung nötig,
  nur verifiziert.
- **Neue Tests:** `tests/test_class_book_order.py`
  (`test_apply_empty_stock_visibility_*`, 3 Fälle) und
  `tests/test_queue_progress.py` (`test_hydrate_hides_unloaned_empty_stock_book_for_non_helper`,
  `test_hydrate_keeps_loaned_empty_stock_book_visible_for_non_helper`,
  `test_hydrate_keeps_empty_stock_book_visible_for_helper` — bestätigt auch,
  dass Modus A unverändert bleibt). 583 Tests grün (574 → 583), Ruff clean.
  Details: `docs/test_status.md` V76.

## 2026-08-12 — Scan-Station: „Trennen" entwertet den Zettel-Code, „Erstellen" bietet Reaktivierung per Checkbox

- **Warum:** Bisher blieb der Zettel-Code eines Schülers über ein „Trennen"
  am Host hinweg unverändert gültig — ein Schüler, der getrennt und später
  neu zugeordnet wurde, konnte sich mit demselben (evtl. herumliegenden)
  Zettel wieder an einer Station anmelden. Niklas wollte, dass „Trennen"
  den Code hart entwertet (Station nimmt ihn nicht mehr an, eine laufende
  Stationsanmeldung wird sofort gelöst), der Host beim nächsten „Erstellen"
  aber die Wahl hat, genau diesen zuletzt entwerteten Code per Checkbox
  wieder zu aktivieren (Default: angehakt) statt einen neuen zu ziehen.
- **Server** (`server/state.py`): neues `AppState.
  station_last_code_by_student` (Code → wird bei JEDER Neuvergabe
  überschrieben, zeigt also immer auf den zeitlich jüngsten, egal ob
  reaktiviert oder frisch gezogen). `AppState.invalidate_station_code()`
  entwertet den aktiven Code (entfernt aus `station_codes`/
  `station_code_by_student`, `station_last_code_by_student` bleibt stehen).
  `allocate_station_code(reactivate_old=True)` (Default) reaktiviert den
  letzten Code, wenn kein aktiver existiert; `reactivate_old=False` zieht
  immer einen frischen. Neu: `AppState.station_reactivate_code()` (nur ein
  Vorschlag, solange kein aktiver Code existiert) für den Host-Snapshot.
- **`server/sessions.py::end_student`**: der `pending`-Zweig (Trennen/Alle
  trennen/Zurücksetzen/Teardown) löst jetzt zusätzlich eine laufende
  Stationsbindung (`release_station_student`) und entwertet den Code
  (`invalidate_station_code`) — vorher blieb beides von einem „Trennen"
  unberührt. `activate_station_student`/`print_station_sheet_for`/
  `_load_and_activate_station_student` nehmen neu `reactivate_old_code`
  entgegen und reichen es an `allocate_station_code` durch.
- **`server/print_queue.py`**: `PrintJob` bekommt `reactivate_station_code`
  (Default `True`), weil der Druckauftrag für „Erstellen und Drucken"
  asynchron durch die Warteschlange läuft — der Checkbox-Wert muss den
  `_dispatch`-Aufruf von `print_station_sheet_for` überleben.
- **Endpunkte** (`server/routes/scan_station.py`, `_deps.py`):
  `ScanStationPrintRequest`/neues `ScanStationActivateRequest` bekommen
  `reactivate_old_code: bool = True`.
- **Client** (`web/host.html`, `web/host-render.js`): neue Checkbox-Zeile im
  Druck-Dialog (`#print-dialog-reactivate-row`, per `style.display`
  ein-/ausgeblendet wie die bestehende Leihschein-Checkbox-Zeile — ein
  `hidden`-Attribut hätte gegen die `.modal-check { display:flex }`-Regel
  verloren). Sichtbar nur im Zettel-Erstellen-Dialog (nicht beim reinen
  Nachdruck, dort ist der Code ohnehin schon aktiv), Text „Alten Code
  (‹Code›) reaktivieren" aus `station_reactivate_code` im Host-Snapshot
  (`AppState._queue_student_as_dict`, nur Host-Pfad wie `station_code`
  selbst — Credential, PLAN §3.7).
- Getestet: `uv run pytest` (weiterhin grün, keine Regressionen) +
  `uvx ruff check server/ automation/ tests/` grün. **Kein dedizierter
  Unit-Test für die neue Entwertungs-/Reaktivierungslogik selbst** (s.
  `docs/test_status.md` „Offen 2026-08-12") — nur die bestehende Suite
  wurde als Regressionsnetz genutzt, keine neuen Testfälle geschrieben.

## 2026-08-12 — Drucker-Nachfrage bei feststeckendem Druckauftrag + Fix: dauerhaft gesperrter Druckenbutton

- **Warum:** Ist ein Druckauftrag noch unzugewiesen in der zentralen
  Warteschlange und werden ALLE seine erlaubten Drucker fehlerhaft (Stall-
  Erkennung), blieb er zuvor unbegrenzt stecken — der Nutzer sah nur eine
  generische „Es dauert ungewöhnlich lange …"-Meldung, konnte aber nichts
  tun außer warten. Niklas wollte stattdessen aktiv nachgefragt bekommen, ob
  der Zieldrucker geändert werden soll.
- **Server** (`server/print_queue.py`): neue `PrintQueue.update_job_printers
  (job_id, requester, printer_ids)` — findet den Job ausschließlich in
  `self.waiting` (nur erlaubt, solange „noch an keinen Drucker gesendet"),
  prüft die Urheberschaft (`helper_token`/`host_sid`-Abgleich gegen
  `requester`), mutiert `job.allowed_printers` **in-place** (kein Pop/
  Insert) — der Job behält seinen bestehenden Platz und damit seine
  Warteposition unverändert. Weckt danach den Scheduler und pusht sofort
  aktualisierte Positionen/Status (`_notify_all`). `print_progress`-Payload
  trägt jetzt zusätzlich `allowed_printers` (aktuelle Auswahl des Jobs, zum
  Vorbefüllen des Dropdowns beim Client). Neuer WS-Typ
  `update_print_printers` (`server/routes/ws.py`, Helferclient) sowie
  `POST /api/print-queue/{job_id}/printers` (`server/routes/slips.py`,
  Host) — beide rufen dieselbe Methode auf.
- **Client — Helferclient** (`web/scan.html`, `scan-render.js`,
  `scan-state.js`, `scan-ws.js`): neues, schlankes Modal
  `#printer-change-modal` (Dropdown wie gewohnt + „Druckauftrag
  aktualisieren"/„Abbrechen"). Zwei Zugriffswege: **automatisch**, wenn
  wirklich alle erlaubten Drucker fehlerhaft sind (`status=='queued' &&
  peer_error` aus `print_progress`); **manuell** über den Druckenbutton
  (`#print-btn`), solange der Auftrag nur noch unzugewiesen `queued` ist —
  unabhängig vom `peer_error`-Flag. „Abbrechen" schließt nur das Fenster,
  ändert nichts am Auftrag.
- **Client — Host** (`web/host-render.js`, `web/host.css`): keine neues
  Fenster, sondern der bestehende persistente Druck-Toast (rechts unten)
  wird bei `status=='queued'` klickbar und klappt Picker + „Druckauftrag
  aktualisieren"-Button ein (`renderPrintToast`, kompakte CSS-Variante —
  Trigger/Panel/Button nur Text-Höhe + etwas Puffer). Im Stuck-Fall klappt
  er automatisch auf; kein Abbrechen-Button (der Hinweis wartet einfach).
- **Gemeinsam** (`web/common.js`, `web/host.css`, `web/scan.html`):
  `mountPrinterPicker` zeigt fehlerhafte Drucker jetzt überall ausgegraut
  mit „(deaktiviert)"-Hinweis, bleiben aber anwählbar — gilt auch für die
  normalen Druck-Start-Dialoge, nicht nur das neue Nachfrage-Fenster.
- **Nutzer-Korrektur (Host-CSS):** der klickbare Toast-Hinweis war
  unterstrichen (`text-decoration: underline dotted`) — auf Wunsch entfernt
  (nur noch `cursor: pointer`). Dropdown-Text im Toast erbte außerdem die
  Textfarbe des umgebenden `.toast`/`.toast-warn` (z. B. weiß auf rotem
  Grund im Stuck-Fall), obwohl der Dropdown-Panel-Hintergrund unverändert
  dunkel (`--surface-2`) blieb — `.printer-picker` bekommt jetzt
  `color: var(--text)` explizit, statt vererbt zu werden.
- **Nachträglich gefundener Bug (Nutzer-Feedback „ich kann den Button nicht
  drücken"):** der Druckenbutton wurde beim Absenden eines Drucks auf
  `disabled` gesetzt und nur bei `print_result` wieder freigegeben. Ein
  feststeckender Auftrag bekommt aber NIE ein `print_result` — nur
  wiederholte `print_progress`-Meldungen, solange er in `waiting` steht —
  der Button blieb also dauerhaft gesperrt, das eben gebaute Nachfrage-Menü
  war so über den Druckenbutton gar nicht erst erreichbar. Fix
  (`scan-ws.js`): `printBtn.disabled = msg.status !== 'queued'` bei jedem
  `print_progress` statt nur bei `print_result` aufgehoben. **Lektion:**
  ein Disabled-Zustand, der auf ein „terminales" Ereignis wartet, muss für
  JEDEN Job-Endzustand erreichbar sein — ein Job, der (absichtlich) nie
  terminiert, hält ihn sonst für immer gesperrt.
- Getestet: `tests/test_print_queue.py`
  (`test_update_job_printers_ok_preserves_position_and_redispatches` —
  Position bleibt exakt erhalten, Dispatch klappt nach Reaktivierung;
  `test_update_job_printers_forbidden_for_non_owner`,
  `test_update_job_printers_forbidden_for_wrong_host_sid`,
  `test_update_job_printers_not_waiting_once_dispatched`,
  `test_update_job_printers_empty_selection_rejected`). Volle Suite (570
  Tests, 565 → 570) + `uvx ruff check server/` + `node --check web/*.js`
  grün. Kein Browser-Livecheck (keine GUI in der Entwicklungsumgebung) —
  Details `docs/test_status.md` V75.

## 2026-08-12 — Korrektur: Zettel-Code doch wieder stabil, nur die Bücherliste wird pro Druck aktualisiert

- **Warum:** Der vorherige Eintrag (unten) führte `AppState.
  reallocate_station_code()` ein, die bei JEDEM Erstellen/Nachdruck einen
  neuen Zettel-Code zog und den alten ungültig machte. Niklas wollte das
  zurückgenommen haben: der Code soll wieder pro Schüler stabil bleiben
  (derselbe Zettel bleibt gültig), NUR die Bücherliste auf dem Zettel soll
  bei jedem Druck aktuell sein — das war ohnehin schon immer der Fall
  (`get_student_info` wird bei jedem Aufruf frisch geholt), unabhängig vom
  Code.
- **Server:** `AppState.reallocate_station_code()`/`_draw_station_code()`
  wieder entfernt (waren erst seit dem vorherigen Eintrag da, nie in einer
  Zwischen-Version live) — `allocate_station_code()` zurück auf den
  Originalstand (Stabil-Lookup, „ein Nachdruck trägt garantiert denselben
  Code"). `server/sessions.py::_load_and_activate_station_student` nutzt
  wieder `allocate_station_code` statt `reallocate_station_code`.
- **Client** (`web/host-render.js`): Kommentare an `openPrintDialog`/
  `printStationSheet` korrigiert (kein „frischer Code" mehr, sondern
  „Bücherliste wird pro Aufruf frisch geholt, Code bleibt stabil"). Der
  Zwei-Knopf-Nachdruck-Dialog (nur „Drucken"/„Abbrechen" im „Aktuell in
  Ausgabe"-Kästchen, s. unten) bleibt unverändert bestehen — das war ein
  eigenständiger, weiterhin gewollter Teil des vorherigen Eintrags.
- Getestet: die drei `test_reallocate_station_code_*`-Tests entfernt
  (Methode existiert nicht mehr). `test_print_sheet_reprint_draws_a_fresh_
  code` → `test_print_sheet_reprint_keeps_same_code` (Code jetzt gleich
  statt verschieden), `test_activate_and_print_both_draw_fresh_codes_
  across_each_other` → `test_activate_and_print_share_the_same_code`. Neu:
  `test_print_sheet_reprint_refreshes_book_list` (per `_FakeIServChanging
  Books`-Fake, das zwischen zwei Aufrufen den Buchstatus wechselt) belegt
  explizit, dass `done_isbns` sich zwischen zwei Drucken aktualisiert,
  während der Code gleich bleibt. Volle Suite (565 Tests) + Ruff +
  `node --check` grün.

## 2026-08-12 — Zettel-Nachdruck: nur Drucken/Abbrechen + immer frischer Code

- **Warum:** Niklas wollte im Nachdruck-Dialog im „Aktuell in
  Ausgabe"-Kästchen nur „Drucken" und „Abbrechen" — der Zettel-Code wurde
  ja schon vorher erstellt, ein separates „Erstellen" ergibt dort keinen
  Sinn. Zusätzlich sollen BEIDE Wege (Pairing-Kasten „Erstellen" UND der
  „Drucken"-Knopf im „Aktuell in Ausgabe"-Kästchen) bei jedem Aufruf einen
  komplett neuen Zettel-Code ziehen, damit der Zettel immer den
  aktuellsten Bücherlisten-Stand trägt — ein alter, physisch
  herumliegender Zettel soll danach nicht mehr laden.
- **Server** (`server/state.py`): neue `AppState.reallocate_station_code()`
  neben dem bestehenden `allocate_station_code()` (bleibt als
  Stabil-Lookup-Primitiv unverändert, z. B. für die WS-Annahmeprüfung
  `resolve_station_code`). `reallocate_station_code` macht einen evtl.
  vorhandenen alten Code des Schülers zuerst ungültig (aus `station_codes`
  entfernt — löst danach nicht mehr auf) und zieht dann einen frischen;
  beide teilen sich den Ziehungs-Kern `_draw_station_code`.
  `server/sessions.py::_load_and_activate_station_student` (gemeinsamer
  Kern von `print_station_sheet_for`/`activate_station_student`) nutzt
  jetzt `reallocate_station_code` statt `allocate_station_code` — betrifft
  also automatisch beide Wege ohne Sonderfall-Code.
- **Client** (`web/host.html`, `web/host-render.js`): `openPrintDialog`
  bekommt einen neuen `opts.reprint`-Schalter. `reprint:true`
  (Nachdruck-Knopf im „Aktuell in Ausgabe"-Kästchen) blendet den
  „Erstellen"-Knopf aus und beschriftet den OK-Knopf schlicht „Drucken"
  (Titel „Zettel für Scan-Station drucken") — nur zwei Optionen.
  `reprint:false` (Pairing-Kasten, weiterhin über `printStationSheet(id,
  btn)` ohne Optionen aufgerufen) bleibt bei den bisherigen drei Knöpfen
  „Erstellen und Drucken"/„Erstellen"/„Abbrechen". Der Nachdruck-Aufruf
  (`data-action="reprint-station-sheet"`) übergibt jetzt `{reprint: true}`.
- Getestet: `tests/test_scan_station.py`
  (`test_reallocate_station_code_invalidates_old_code`,
  `test_reallocate_station_code_first_call_behaves_like_allocate`,
  `test_reallocate_station_code_repeated_calls_always_differ` — reiner
  State-Primitiv; `test_print_sheet_reprint_draws_a_fresh_code`,
  `test_activate_and_print_both_draw_fresh_codes_across_each_other` —
  beide Wege quer gegeneinander); bestehende Code-Stabilitätstests für
  `allocate_station_code` unverändert grün (unberührter Primitiv). Der
  Zwei-Knopf-Nachdruck-Dialog per Playwright-Screenshot gegengeprüft. Volle
  Suite (567 Tests) + Ruff + `node --check` grün.

## 2026-08-12 — Host: „Erstellen" statt „Drucken" im Pairing-Kasten — Zettel aktivieren ohne Sofortdruck

- **Warum:** Niklas wollte im Pairing-Kasten den Knopf neben der
  Schüler-Auswahl der „Scan-Station:"-Zeile nicht mehr „Drucken", sondern
  „Erstellen" nennen. Klick öffnet weiterhin einen Druckdialog, der aber
  jetzt zusätzlich zu „Erstellen und Drucken" auch ein reines „Erstellen"
  anbietet (Rückfrage bestätigt: aktiviert den Schüler — Zettel-Code,
  Status, Fortschritt —, baut aber KEIN PDF und druckt nicht), daneben
  „Abbrechen". Druckerauswahl bleibt wie gehabt auf die erlaubten
  Klassen-Drucker vorausgewählt.
- `web/host.html`: neuer dritter Knopf `#print-dialog-activate-only`
  („Erstellen", `secondary`, initial `hidden`) zwischen OK und Abbrechen im
  `#print-dialog`.
- `web/host-render.js::openPrintDialog`: im Zettel-Modus (`opts.sheet`)
  heißt der OK-Knopf jetzt „Erstellen und Drucken" statt „Drucken" (Titel:
  „Zettel für Scan-Station erstellen"), der neue Knopf wird eingeblendet
  und löst direkt `{mode:'activate'}` aus — ohne die
  Druckerauswahl-Pflichtprüfung des OK-Pfads, da nicht gedruckt wird.
  `printStationSheet()` verzweigt auf `choice.mode`: `'activate'` postet an
  den neuen Endpoint, `'print'` läuft wie bisher über
  `/api/scan-station/print-sheet`. `renderCtxStationPrint`s Zeilen-Knopf
  heißt jetzt ebenfalls „Erstellen" (derselbe Dialog dahinter).
- **Server:** `server/sessions.py` — die Aktivierungslogik aus
  `print_station_sheet_for` (Status-Flip, Zettel-Code-Vergabe,
  `init_book_progress`, Host-Broadcast) in einen gemeinsamen Kern
  `_load_and_activate_station_student()` extrahiert; neues
  `activate_station_student()` ruft nur diesen Kern auf (kein PDF, kein
  Druck). Neuer Endpoint `POST /api/scan-station/activate`
  (`server/routes/scan_station.py`) — synchron (kein `PrintJob`, anders als
  `/print-sheet`, das über die Druckerwarteschlange läuft), gibt
  `{ok, code}` zurück. Der physische Druck kann jederzeit später über den
  Zettel-Nachdruck-Knopf im „Aktuell in Ausgabe"-Kästchen nachgeholt werden
  — derselbe Zettel-Code bleibt stabil (`allocate_station_code` ist bereits
  idempotent pro Schüler).
- Getestet: `tests/test_scan_station.py`
  (`test_activate_station_student_marks_active_without_printing`,
  `test_activate_station_student_reprint_keeps_baseline`,
  `test_scan_station_activate_endpoint` — bestätigt auch, dass KEIN
  `PrintJob` enqueued wird, `test_scan_station_activate_for_unknown_
  student_404`); bestehende `print_station_sheet_for`-Tests unverändert
  grün (reiner Extract-Refactor, keine Verhaltensänderung). Drei-Knopf-
  Dialog per Playwright-Screenshot gegengeprüft. Volle Suite (562 Tests) +
  Ruff + `node --check` grün.

## 2026-08-12 — Host-Badge: gesamt-Zeile statt Doppelzeile bei Gleichstand

- `web/host-render.js::activeStatusDetails()`: die zweizeilige
  Fortschrittsanzeige (`X/Y ohne Mjb` + `X/Y gesamt`) erscheint nur noch,
  wenn sich Session- und Gesamtwerte tatsächlich unterscheiden. Bisher
  wurde an der Station immer zweizeilig angezeigt, auch ohne Vorbestand
  (`sessionCombined === totalCombined`) — jetzt reicht dann die
  `X/Y gesamt`-Zeile allein.

## 2026-08-12 — Drucker-Display: Schülerauftrag-Modus in der Warteschlange + Vorrang-Hinweis

- **Warum:** Niklas wollte, dass das Drucker-Display (`/drucker-display`)
  in seiner zentralen Warteschlangen-Anzeige nicht mehr Host-/Helfer-Namen
  zeigt, sobald eine Klasse mit offener Liveausgabe aktiv ist, deren
  freigegebene Drucker das Display abdecken und die einen zugeordneten,
  noch nicht fertigen Modus-B-Schüler hat — nur dann sollen ausschließlich
  Schüleraufträge in der Warteschlange stehen. Vorab per drei
  `AskUserQuestion`-Fragen geklärt: Filter gilt für die **gesamte** Liste
  (nicht pro Eintrag), Fallback ohne passende Klasse ist **wie bisher**
  (alle Rollen), `pending_pairing`-Sessions (noch keiner Klasse
  zuordenbar) werden **ignoriert**.
- `server/state.py::AppState._printer_display_students_only()`: neue
  Bedingung — `live_ausgabe=True`, Schnittmenge Display-Zuweisung ∩
  Klassen-Druck-Allowlist nicht leer, mindestens ein `paired`-Schüler
  dieser Klasse mit `status != "done"`. `printer_display_view()` reicht
  das Ergebnis an `PrintQueue.display_view()` durch.
- `server/print_queue.py::display_view()`: neuer Parameter
  `students_only` filtert `waiting_list` auf
  `originator_info.type == "student"` — nur die zentrale Warteschlange,
  nicht die „druckt"/„als nächstes"-Kategorien je Drucker (dort bleiben
  bereits gesendete Aufträge aller Rollen sichtbar, sie sind physisch
  verbindlich). Flag wird zusätzlich im Response-Dict mitgegeben.
- **Broadcast-Lücke geschlossen:** der Gate hängt von State ab, der bisher
  keinen Drucker-Display-Push auslöste (`broadcast_printer_displays` lief
  nur bei Druck-Übergängen). Ergänzt in `sessions.py::end_student`,
  `routes/modus_b.py` (beide Pairing-Wege), `routes/classes.py` (Klasse
  öffnen/schließen, Liveausgabe-Toggle, Drucker-Allowlist-Änderung,
  Schuljahrwechsel), `routes/queue.py::clear_queue`.
- **Vorrang-Hinweis** (Folge-Anforderung): Da die „Nächster"-Kategorie
  weiterhin alle Rollen zeigt, kann dort im Schülerauftrag-Modus ein
  Host-/Helferauftrag auftauchen, der in der zentralen Warteschlange
  unsichtbar wäre. `web/drucker-display.js` prüft pro Drucker-Karte
  `msg.students_only` + ob ein Auftrag in `nextOrds` `originator.type` ∈
  {`host`,`helper`} hat; falls ja (Fehler hat Vorrang vor diesem Hinweis):
  Druckername wird zu `"<Name> - Vorrang"`, darunter
  `"Ein Betreuer druckt etwas. Dieser Druckauftrag hat Vorrang."` —
  identisches Markup/CSS-Muster wie der Fehler-Hinweis
  (`.dd-fault-name`/`.dd-fault-msg` → neue `.dd-priority-name`/
  `.dd-priority-msg`), nur Farbe `#e69500` (dasselbe Gelb wie
  `.status-alert-orange` im Helferclient) statt Rot.
- Getestet: Ruff clean, `node --check` auf `drucker-display.js`, volle
  Offline-Testsuite grün (Exit 0, keine Regressionen). Details/Lektionen:
  [`_logs/2026-08-12_sba_drucker_display_students_only.md`](../../../../_logs/2026-08-12_sba_drucker_display_students_only.md).

## 2026-08-12 — Host: Zettel-Nachdruck-Knopf im „Aktuell in Ausgabe"-Kästchen

- **Warum:** Niklas wollte neben dem „Trennen"-Knopf eines aktiven Schülers
  einen Weg, dessen Scan-Station-Zettel (Barcode + Bücherliste zum Abhaken)
  erneut zu drucken — z. B. wenn das Original verloren geht oder unleserlich
  wird.
- `web/host-render.js::renderCtxNowServing`: neuer Icon-Knopf
  (`data-action="reprint-station-sheet"`) rechts neben „Trennen", sichtbar
  sobald der Schüler überhaupt einen Zettel-Code hat (`s.station_code`,
  dieselbe Bedingung wie die bestehende Code-Anzeige links). Neues
  `ICON_SHEET` — Blatt mit Eselsohr oben LINKS (Grundform wie
  `docLetterIcon` in `scan-render.js`, aber gespiegelt — die Antrags-Icons
  dort knicken oben rechts), Barcode oben rechts davon (fünf schmale
  Striche) und darunter zwei gleich lange Listenzeilen mit sichtbarem
  Abstand zum Häkchen, das NUR die untere trägt — spiegelt den echten
  Zettel (Barcode + Abhak-Liste, s. `server/scan_station.py::
  build_sheet_pdf`). Höhe (`y2–22` im 24er viewBox) deckungsgleich mit
  `ICON_ACTION_CHECK`/`ICON_PRINTER` (per `getBBox()` gegengeprüft); mit
  `ICON_DISCONNECT` (`y3–21`) bis auf 1 px identisch — dessen eigene Form
  gibt dort das Limit vor; die Breite ist bewusst SCHMALER als die anderen
  Icons (`x5–19` statt `x2–22`) — Seitenverhältnis ~1:1,43, angelehnt an ein
  echtes A4-Blatt (1:√2), muss also nicht zu den quadratischeren
  Nachbar-Icons passen. Vier Korrekturrunden nach Niklas' Feedback: (1)
  Eselsohr raus + Inhalt zentriert, (2) Blatt vergrößert + Häkchen nur an
  der unteren Zeile, (3) Zeilen gleich lang + mehr Abstand vor dem Häkchen
  + Höhen-Angleich an Abschließen/Trennen/Drucken + Eselsohr zurück (jetzt
  oben links) + Barcode nach rechts verschoben, (4) Breite reduziert aufs
  A4-Seitenverhältnis (Höhe blieb deckungsgleich mit den Nachbar-Icons).
  Jede Runde per Playwright-Screenshot gegengeprüft. Der Klick ruft die
  bestehende `printStationSheet()` auf
  (öffnet denselben Druck-Dialog/Druckerauswahl wie beim ersten Zettel-Druck
  aus der Klassen-Queue, postet an `/api/scan-station/print-sheet`) — keine
  neue Funktion nötig, nur ein neuer Aufrufweg direkt für den bereits aktiven
  Schüler statt über die Wartend-Auswahl.
- **Server-Korrektur** (`server/sessions.py::print_station_sheet_for`):
  `reset_baseline` war bisher hart auf `True` verdrahtet — richtig für das
  echte „Aufrufen" (`pending → active`, wo eine neue Fortschritts-Zählung für
  die Host-Statuszeile beginnen soll), aber falsch für einen reinen Nachdruck
  eines bereits aktiven Schülers: der hätte den „seit Aufrufen"-Fortschritt
  (`loaned_at_load`) unbeabsichtigt zurückgesetzt. Jetzt nur noch bei
  `status == "pending"` vor der Statusänderung gesetzt — der neue Host-Knopf
  ist der erste tatsächliche Aufrufer dieses Pfads mit einem bereits aktiven
  Schüler (die alte Klassen-Queue-Auswahl zeigte nur Wartende).
- Getestet: `tests/test_scan_station.py::
  test_print_sheet_reprint_for_active_student_keeps_baseline` (neu,
  `loaned_at_load` bleibt bei einem Nachdruck für einen aktiven Schüler
  unverändert); bestehende `test_print_sheet_marks_student_active_and_
  collecting`/`test_print_sheet_does_not_reactivate_a_done_student`
  weiterhin grün (unveränderte Semantik für `pending`/`done`). Volle Suite
  + Ruff + `node --check` grün.

## 2026-08-12 — Helferclient: Namenskollision mit `common.js` behoben (`ALERT_META`)

- **Symptom:** Der Helferclient (`web/scan.html`) hing im Dev/Test-Setup
  dauerhaft bei „Verbinde…", Buttons reagierten nicht. Browser-Konsole:
  `Uncaught SyntaxError: Identifier 'ALERT_META' has already been declared
  (at scan-state.js:1:1)`, gefolgt von `token is not defined` (`scan-ws.js`)
  und `bookRowsEl is not defined` (`scan-render.js`).
- **Ursache:** Der laufende Umbau (Scan-Ansicht-Logik von `student.js` nach
  `web/common.js` ausgelagert, geteilt zwischen Schülerclient + Scan-Station —
  **nicht** dem Helferclient, der eigene Perspektiven-Texte hat, „an den
  Schüler" statt „an dich") führte in `common.js` eine neue `const
  ALERT_META` ein, die mit der bereits bestehenden, inhaltlich anderen `const
  ALERT_META` in `web/scan-state.js` kollidiert — beide Skripte teilen sich in
  `scan.html` dieselbe globale Top-Level-Scope (kein Build-Step). Die doppelte
  `const`-Deklaration ist ein Parse-Time-`SyntaxError`, wodurch `scan-state.js`
  komplett nicht ausgeführt wurde und alle nachfolgend geladenen Skripte auf
  undefinierten Variablen liefen. Da der Crash schon vor `connect()` passierte,
  wurde nie ein WS-Handshake versucht — daher keine Spur im Server-Log.
- **Fix:** Helfer-lokale Konstante in `web/scan-state.js` zu
  `HELPER_ALERT_META` umbenannt (+ Referenz in `web/scan-render.js`);
  `common.js`s `ALERT_META` bleibt für Schülerclient/Scan-Station unverändert.
  Verhalten/Texte des Helferclients unverändert.
- **Verifiziert:** `node --check` auf den konkatenierten Skripten jeder Seite
  (Ladereihenfolge wie im Browser) für `scan.html`, `student.html`,
  `scan-station.html`, `host.html`, `teacher.html` — keine weiteren
  Namenskollisionen. Kein Server-Neustart nötig (statische Dateien werden
  direkt vom Disk ausgeliefert).
- **Lektion:** Bei geteilten Top-Level-`<script>`-Scopes (kein Modul-System)
  kollidiert eine neue „gemeinsame" Datei garantiert mit gleichnamigen,
  bewusst abweichenden lokalen Deklarationen von Konsumenten, die NICHT zur
  Zielgruppe der Auslagerung gehören. Details:
  `_logs/2026-08-12_sba_helferclient_alert_meta_collision.md`.

## 2026-08-12 — Scan-Station: „Fertig"-Knopf neben der Statuszeile entfernt

- **Warum:** Niklas wollte den „Fertig"-Knopf neben der Statuszeile ganz
  weghaben — die Station meldet Schüler ohnehin automatisch nach 30 s
  Inaktivität ab (s. Hinweistext oben), ein manueller Weg war nicht gewollt.
- `web/scan-station.html`: `#done-btn` entfernt, die jetzt überflüssige
  `.status-row`-Wrapper-Zeile (nur noch ein Kind) samt zugehöriger
  CSS-Regeln (`.status-row`, `#done-btn`) ebenfalls entfernt — `#status`
  bringt Breite/Abstand bereits selbst aus `scan-view.css` mit.
  `web/scan-station.js`: `$('done-btn')`-Zeile aus `renderBinding()` und der
  Klick-Listener entfernt; `release()` bleibt (wird weiterhin vom
  Leerlauf-Timer aufgerufen).
- Getestet: `node --check`, `uv run pytest tests/test_scan_station.py
  tests/test_ws_scan_station.py` weiterhin grün (keine Assertion referenzierte
  `done-btn`); per Playwright gegen einen lokalen HTTP-Server geladen, keine
  JS-Fehler beim Fehlen des Elements.

## 2026-08-12 — Scan-Station: Startbildschirm erklärt den Ablauf statt nur die Kamera-Position

- **Warum:** Der Hinweistext vor der Anmeldung nannte nur, wo der Barcode auf
  dem Zettel sitzt („Halte den Barcode oben rechts … vor die Kamera"), aber
  nicht den eigentlichen Ablauf. Niklas wollte stattdessen kurz erklären, dass
  zuerst der Schülerbarcode, danach die Bücher gescannt werden und dass 30 s
  Inaktivität zur automatischen Abmeldung führt.
- `web/scan-station.html`: `#ready-block`-Hinweistext ersetzt durch „Scanne
  zuerst deinen Schülerbarcode vom Zettel, danach deine Bücher. Nach 30 s
  Inaktivität wirst du automatisch abgemeldet." Reiner Text-/Markup-Fix, kein
  Server-/JS-Bezug.

## 2026-08-12 — Erkennbare Dateinamen: Leihschein-Druckjob (OS-Warteschlange) + Schülerleihschein-Download (Klasse ergänzt)

- **Warum:** Landete ein Leihschein-Druck in der **physischen** OS-
  Druckerwarteschlange (nicht die interne App-Queue mit Sortierung) unter
  einem generischen `leihschein_<student_id>`-Namen, war er dort im
  Fehlerfall (Stau, Papierstau, falscher Drucker) kaum einem Schüler
  zuzuordnen. Niklas wollte stattdessen Klasse, Nachname, Vorname sehen.
  Zusätzlich sollte der Download-Dateiname des Schülerleihscheins (Eigenabruf
  am Abschluss-Screen) neben Name/Vorname auch die Klasse tragen.
- **Leihschein-Druckjob** (`server/sessions.py`): neuer Helper
  `_slip_print_label(state, student_id)` baut `Leihschein_<Klasse>_
  <Nachname>_<Vorname>` (Sonderzeichen/Leerzeichen zu `_` normalisiert) und
  ersetzt in `print_loan_slip_for` das bisherige `label=f"leihschein_
  {student_id}"` an beiden `print_pdf`-Aufrufen (Datei- und regulärer
  Druckpfad). `printing.py` verwendet `label` unverändert als Präfix der
  Temp-PDF-Datei — `lp` (kein `-t`-Flag) und SumatraPDF reichen den
  Dateinamen 1:1 als Job-/Dokumentname an die OS-Druckerwarteschlange durch,
  daher wirkt die Umbenennung dort direkt. Fallback auf die alte
  `leihschein_<student_id>`-Form, wenn Klasse/Name nicht ermittelbar sind
  (defensiv per `getattr`, nicht über den bestehenden `_student_form`-Direkt-
  zugriff, damit Test-Doubles ohne diese Attribute nicht crashen). Der
  Scan-Station-Zettel (`print_station_sheet_for`) blieb bewusst unverändert
  — nur explizit nach dem Leihschein gefragt.
- **Schülerleihschein-Download** (`server/sessions.py`): neuer Helper
  `_own_slip_filename(lastname, firstname, form)` baut
  `Schülerleihschein <Nachname>, <Vorname>, <Klasse>.pdf` (Klasse fällt weg,
  wenn unbekannt) und ersetzt das bisherige Format ohne Klasse in
  `_prefetch_own_slip` (Vorlade-Pfad beim Leihschein-Druck) und
  `_send_own_slip_download` (Frisch-Fetch-Fallback kurz vor Session-Ende) —
  beide nutzen dafür `_student_form(state, student_id)`.
- Getestet: bestehende Assertions in `tests/test_print_queue.py` und
  `tests/test_printing.py` angepasst (`"Schülerleihschein Test,
  Schüler.pdf"` → `"...Schüler, 10a.pdf"`); volle Suite weiterhin grün.

## 2026-08-12 — Scan-Station: Schülername blieb nach dem Abmelden sichtbar (CSS-Bug)

- **Warum:** Niklas meldete, dass nach dem Abmelden eines Schülers (Timeout,
  „Fertig"-Knopf, Stationswechsel) dessen Name weiter über der Namenszeile
  stand, statt zu verschwinden.
- **Ursache:** `renderBinding()` (`web/scan-station.js`) setzt zwar
  `#student-row.hidden = true`, aber `.name-row { display: flex; … }`
  (`web/scan-view.css`) ist eine normale Autor-Regel und schlägt die
  UA-Regel `[hidden]{display:none}` unabhängig von der Selektor-Spezifität —
  Autor-Deklarationen gewinnen gegen User-Agent-Deklarationen immer, auch bei
  gleicher Spezifität. Das `hidden`-Attribut blieb dadurch wirkungslos, die
  Zeile war permanent `display:flex`. Mit Playwright gegen einen lokalen
  HTTP-Server (nicht `file://`, sonst lädt `/scan-view.css` wegen des
  Root-Pfads gar nicht) nachgestellt: `getComputedStyle(#student-row).display`
  blieb `flex` trotz gesetztem `hidden`-Attribut.
- **Fix:** `#student-row[hidden] { display: none; }` in `scan-view.css`
  ergänzt (ID+Attribut schlägt die Klassen-Regel per Spezifität). Andere über
  `hidden` gesteuerte Elemente (`#book-wrap`, `#done-btn`, Modal-Absätze)
  waren nicht betroffen — sie haben keine eigene `display`-Deklaration, die
  UA-Regel griff dort bereits. Kein Server-/Test-Impact (reiner CSS-Fix),
  nachverifiziert per Playwright: `display:none` mit, `display:flex` ohne
  `hidden`-Attribut.

## 2026-08-12 — Scan-Station: Leerlauf-Timer während blockierendem Buch-Hinweis ausgesetzt

- **Warum:** Öffnet ein Buch-Scan an der Station ein blockierendes Hinweis-
  Modal (ausgemustert/anderweitig verliehen — wartet auf Freigabe durch den
  Host per `/api/clear-book-alert`), lief der 30-s-Leerlauf-Timer bislang
  unbeeindruckt weiter mit. Wartete der Betreuer länger als 30 s, wurde der
  wartende Schüler von der Station geworfen, obwohl er nichts falsch gemacht
  hatte. Niklas wollte den Timer für die Dauer genau dieser (und nur dieser)
  Meldung aussetzen; alle anderen, selbst schließbaren Meldungen sollen den
  Timer unverändert weiterlaufen lassen.
- **Server** (`server/sessions.py::expired_scan_stations`): Stationen mit
  offenem `book_alert_open` zählen jetzt nicht mehr zum 30-s-Sweeper — analog
  zur bestehenden `worker_ready`-Ausnahme. `server/routes/queue.py::
  clear_book_alert` setzt beim Freigeben `station.last_activity` neu, damit
  der Timer ab der Host-Freigabe wieder mit vollen 30 s zählt, statt die
  stillstehende Wartezeit sofort nachzuholen.
- **Client** (`web/scan-station.js`): neue `freezeIdle()`/`unfreezeIdle()`
  stoppen bzw. starten den lokalen Countdown-Timer — `freezeIdle()` beim
  Öffnen eines blockierenden `scan_result` (parallel zu `bookAlertOpen =
  true`), `unfreezeIdle()` bei `book_alert_clear` (startet über `startIdle()`
  wieder frisch bei vollen 30 s, spiegelbildlich zum Server-Reset).
- Getestet: `tests/test_scan_station.py`
  (`test_idle_sweep_ignores_stations_with_open_book_alert`,
  `test_clear_book_alert_unblocks_station` um den `last_activity`-Reset
  erweitert); volle Suite `uv run pytest` grün (557 Tests).

## 2026-08-12 — Now-Serving-Kästchen: Info-Zeile in Zwei-Spalten-Layout

- **Warum:** Die am 2026-08-11 eingeführte Stationszeile stand als eigene
  Zeile über der Klasse/Status-Zeile; Niklas wollte stattdessen ein
  kompakteres, klar spaltenweise sortiertes Layout: links bündig Zettel-Code
  (1. Zeile) und Klasse (2. Zeile), rechts bündig Stationsname + Symbol +
  Trennen-Knopf (1. Zeile) und Status (2. Zeile) — dazu die Zeilen enger.
- `renderCtxNowServing` (`web/host-render.js`) baut die beiden vormals
  getrennten Zeilen (`ns-station-row`, `ns-meta`) jetzt zu einem gemeinsamen
  `ns-info-grid` mit `ns-info-left`/`ns-info-right` zusammen; reine
  Markup-/CSS-Umstellung, keine neuen Server-Felder oder Endpunkte.
- `web/host.css`: `.ns-tile`-Gap von 8px auf 4px reduziert, neue
  `.ns-info-grid`/`.ns-info-left`/`.ns-info-right`-Regeln (Flex, je 1px
  Zeilenabstand) ersetzen `.ns-station-row`/`.ns-station-left`/`.ns-meta`.

## 2026-08-11 — Now-Serving-Kästchen: Stationszeile mit Zettel-Code + eigenständigem Stations-Trennen

- **Warum:** Der Stationsname stand bisher als kleines Badge zwischen Status
  und Helfer-Badge; der vierstellige Zettel-Code des Schülers war im Host
  gar nicht sichtbar (nützlich, um ihn dem Schüler erneut zu nennen oder
  gegen den gedruckten Zettel zu prüfen). Ein „nur von der Station
  abmelden"-Weg (ohne den Schüler komplett zu trennen) fehlte ebenfalls.
- **Neue eigene Zeile im Now-Serving-Kästchen** (`web/host-render.js::
  renderCtxNowServing`), direkt unter dem Namen: links Stationsname + Symbol
  + kleiner Trennen-Knopf (grau, wie die Klasse in der Zeile darunter),
  rechts der Zettel-Code (blau, wie der Status). Die Zeile erscheint nur,
  wenn der Schüler überhaupt einen Zettel-Code hat; der Stationsname/Knopf
  darin nur, solange er GERADE an einer Station angemeldet ist.
- **`QueueStudent.as_dict()`** führt jetzt `station_code`. Bewusst NICHT
  einfach am bestehenden `_queue_student_as_dict` ergänzt (der ist auch der
  Serialisierungsweg für die Helferclient-Queue-Ansichten
  `pending_queue_as_list`/`real_contexts_summary`!) — neuer Parameter
  `include_station_code: bool = False`, nur an den beiden `state_snapshot()`-
  Stellen (Host) auf `True` gesetzt. Der Zettel-Code bleibt damit ein
  Credential, das ausschließlich der Host sieht (PLAN §3.7) — der Host kennt
  ihn ohnehin, er hat den Zettel selbst gedruckt.
- **`POST /api/scan-station/release-student`** (neu, `server/routes/
  scan_station.py`) — Spiegel von `/api/scan-station/release`, aber vom
  Schüler statt von der Station aus adressiert (das Now-Serving-Kästchen
  kennt nur die `student_id`, nicht die interne `station_id`). Löst NUR die
  Stationsbindung (`release_station_student`), der Schüler bleibt ansonsten
  unverändert aktiv — anders als der allgemeine „Trennen"-Knopf. Idempotent
  ohne aktive Stationsbindung.
- Tests: `tests/test_scan_station.py` (`station_code` nur mit
  `include_station_code=True`, nie in den Helferclient-Pfaden, im echten
  `state_snapshot()` an beiden Stellen; neuer Endpoint inkl. Host-Auth-Gate,
  fehlendem `student_id` und Idempotenz ohne Station) + `uv run pytest`
  (556 Tests) + `uvx ruff check server/ automation/ tests/` + `node --check
  web/*.js`. 556 Tests grün (547 → 556).

## 2026-08-11 — Scan-Station: Host-Queue-Status „Bücher sammeln" + Stationsname-Badge

- **Warum:** Ein Schüler, für den ein Zettel gedruckt wurde, tauchte in der
  Host-Queue bis zur ersten Anmeldung an einer Station weiterhin als
  „Wartend" auf — kein sichtbarer Unterschied zu einem Schüler, der noch gar
  nicht dran ist.
- **Der Zettel-Druck ist jetzt das „Aufrufen" des Zettel-/Stations-Flusses**
  (`server/sessions.py::print_station_sheet_for`): ein wartender Schüler
  wechselt auf `active`, sein Fortschritt wird befüllt
  (`init_book_progress(..., reset_baseline=True)`, wie beim Aufrufen durch
  einen Helfer), und der Host bekommt sofort einen frischen Snapshot (nicht
  erst beim nächsten ohnehin fälligen Broadcast).
- **`QueueStudent.station_zettel_printed`** (`server/state.py`, neu,
  persistiert über ein Ab-/Anmelden an der Station hinweg — nur „Status
  zurücksetzen"/„Trennen" am Host löscht sie über `reset_progress()`).
  Steuert zusammen mit dem aktuellen Stationsstatus die Host-Statuszeile
  (`activeStatusDetails` in `web/host-render.js`):
  - Zettel gedruckt, NICHT an einer Station angemeldet, noch nicht alles
    ausgeliehen → „Bücher sammeln" statt einer Zahl.
  - An einer Station angemeldet → immer die zweizeilige Anzeige
    „X/Y ohne Mjb" + „X/Y gesamt" (bisher nur bei vorhandenem Vorbestand
    zweizeilig — an der Station jetzt unabhängig davon, s. `atStation` in
    `activeStatusDetails`).
  - Abgemeldet → zurück zu „Bücher sammeln", **außer** alle Bücher sind
    bereits ausgeliehen — dann bleibt es bei der Zahl.
- **`AppState._queue_student_as_dict`** löst jetzt zusätzlich `station_name`
  auf (Spiegel von `assigned_helper_name`, über das neue
  `AppState.find_station_by_student`) — Stationsname bzw. Kurz-ID als
  Fallback ohne gesetzten Namen.
- **Stationsname-Badge wie beim Helfer**, Host-Symbol statt Helfer-Symbol:
  `ICO_HOST` (`web/host-state.js`, dasselbe SVG wie `ICO_LAPTOP` im
  Drucker-Display) im Now-Serving-Kästchen und in der Klassen-Queue-Tabelle;
  ein übernehmender Helfer hat Vorrang vor dem Stations-Badge.
- Tests: `tests/test_scan_station.py` (Zettel-Druck aktiviert wartende
  Schüler, reaktiviert aber keinen bereits fertigen; „Status zurücksetzen"
  löscht die Markierung; `station_name` im Queue-Snapshot mit/ohne
  Stationsname/Anmeldung) + `uv run pytest` (547 Tests) + `uvx ruff check
  server/ automation/ tests/` + `node --check web/*.js`. 547 Tests grün
  (541 → 547). **Browser-Livecheck der neuen Statustexte/Badges offen.**

## 2026-08-11 — Scan-Station: Blockierverhalten wie am Handy + Stationswechsel per Zettel-Code

- **Warum:** Die Station verhielt sich bei ausgemusterten/anderweitig
  verliehenen Büchern bisher anders als das Handy — der Schüler konnte den
  Hinweis selbst schließen. Jetzt exakte Parität: `book_deleted`/`not_in_stock`
  öffnen ein blockierendes Modal, das nur der Host per
  `/api/clear-book-alert` freigibt.
- **`ScanStationSession.book_alert_open`** (`server/state.py`, neu, kein
  `book_alert_payload` — anders als `StudentSessionB` braucht die Station
  keinen Reconnect-Wiederherstellungsfall, ein Reconnect setzt sie ohnehin
  ganz zurück). Solange offen werden Kamera-Scan **und** das manuelle
  Eingabefeld ignoriert (`onScanSuccess`-Guard) — das Feld selbst bleibt
  bedienbar (weitere Codes eintippen + Enter drücken funktioniert, wirkt nur
  nicht), keine Auto-Schließung durch den nächsten Scan.
- **`/api/clear-book-alert`** (`server/routes/queue.py`) prüft jetzt zusätzlich
  `AppState.find_station_by_student` (neu, Spiegel von
  `find_session_by_student`/`find_helper_for_student`) und schickt bei Bedarf
  `book_alert_clear` an die Station.
- **Ersatzansprüche bleiben ausschließlich am Host** — hierfür war keine
  Änderung nötig: `process_scan(..., source="student")` (Default-Parameter,
  von der Station bereits so aufgerufen) liefert `loaned_to`-Details grundsätzlich
  nicht an Station/Handy zurück, nur der `book_alert`-Broadcast an den Host
  trägt sie (`server/sessions.py::_process_scan_locked`, unverändert).
- **Stationswechsel per Zettel-Code** (`server/routes/ws.py::ws_scan_station`):
  Jeder Scan während einer laufenden Anmeldung wird zuerst geprüft, ob er
  eigentlich der Zettel-Code eines ANDEREN Schülers ist. Der Treffer-Check
  selbst läuft **unabhängig von Länge/Ziffernform** (einfacher Dict-Lookup) —
  eine anfängliche Fassung filterte vorab auf „exakt 4-stellig", was echte
  Treffer je nach vom Scanner gelieferter Zeichenkette zu Unrecht als
  Buch-Barcode fehlschlagen ließ; korrigiert, sodass ein Treffer nie an einer
  zu strengen Formannahme scheitert. Trifft es zu, wechselt die Station sofort
  (`release_station_student` + `load_station_student` für den neuen Schüler)
  statt „Buch unbekannt" zu melden. Scheitert der Wechsel (Zielschüler z. B.
  inzwischen fertig), bleibt der aktuell angemeldete Schüler unangetastet —
  nur eine Fehlermeldung erscheint. Das bestehende 10/Minute-Rate-Limit
  bleibt an die 4-stellige Form gebunden und greift weiterhin für JEDEN
  4-stelligen Versuch (Treffer wie Fehlversuch), nicht nur beim initialen
  Zettel-Code-Scan.
- Tests: `tests/test_scan_station.py` (`find_station_by_student`,
  `/api/clear-book-alert` löst Stationen und zielt nur auf die passende),
  `tests/test_ws_scan_station.py` (Blockade hält über einen ignorierten Scan
  hinweg, Host-Freigabe schaltet frei, erfolgreicher/gescheiterter
  Stationswechsel, ein zufällig 4-stelliger unbekannter Barcode landet
  weiterhin bei `unknown_book` statt fälschlich als Wechselversuch). 541 Tests
  grün (532 → 541).

## 2026-08-11 — Scan-Station: Zettel mit Barcode für Schüler ohne Handy

- **Warum:** Modus B setzt ein eigenes Handy pro Schüler voraus. Wer keins hat,
  fiel komplett auf die Helfer-Bedienung zurück. Neu: ein festes Scan-Gerät
  (`/scan-station`), an dem sich Schüler nacheinander mit einem gedruckten
  Zettel anmelden. Zielbild + Sicherheitsmodell: `docs/PLAN.md` §3.8.
- **Zettel** (`server/scan_station.py`, neu): A4 hoch — oben links Klasse und
  „Nachname, Vorname", oben rechts ein Code-39-Barcode **6,5 × 1,2 cm** mit der
  vierstelligen Nummer darunter, im Blattkörper „Noch vorgemerkt" (mit Kästchen
  zum Abhaken) und „Bereits ausgeliehen". Der Code-39-Encoder ist selbst
  gebaut (Standardtabelle, Breitverhältnis 2), gezeichnet wird mit dem schon
  vorhandenen PyMuPDF — kein neues Dependency.
- **Code-Vergabe** (`AppState.allocate_station_code`): stabil pro Schüler (ein
  Nachdruck trägt denselben Barcode) und **nie recycelt** innerhalb einer
  Server-Laufzeit — ein alter, herumliegender Zettel darf nie jemand anderen
  laden. Der Code steht weder im Log noch im Host-Snapshot.
- **Station** (`web/scan-station.html`/`.js`, `server/routes/scan_station.py`,
  `ws_scan_station`): Pairing-Fluss wie beim Drucker-Display — Token in der URL,
  Registrierungs-Code auf dem Schirm, Freischaltung am Host per Namenseingabe,
  Light/Dark und **Kamera/Manuell** vom Host umschaltbar. Nach einem gültigen
  Zettel-Code lädt der Server den Schüler; gescannt wird über dasselbe
  `process_scan` wie am Handy (read-only, staged ohne `ALLOW_BOOKING`). Kein
  Leihschein-Druck, kein Abschließen — das bleibt bei Host/Helfer. Die Station
  sieht nur Name und Klasse (geteiltes Gerät), nicht den Zahl-/Anmeldestatus.
- **Der Scanmodus ist derselbe wie im Schülerclient — nicht nur „ähnlich":**
  Das Aussehen liegt jetzt in `web/scan-view.css` (aus `student.html`
  herausgezogen), die Logik in `web/common.js` (`renderBookRows`,
  `renderBookAlert`/`hideBookAlert`, `ALERT_META`, `statusAlertClass`,
  `OK_SCAN_STATUSES`/`BLOCKING_SCAN_STATUSES` — vormals `*_STUDENT` in
  `student.js`). Beide Seiten nutzen dieselbe obere Leiste, Statuszeile samt
  Farbregeln, Namenszeile, Bücher-Tabelle mit FLIP-Animation und dasselbe
  Buch-Hinweis-Modal. Ein Unterschied bleibt: An der Station schließt der
  Schüler auch blockierende Buch-Meldungen selbst — den Host-Freigabe-Weg
  (`book_alert_clear`) gibt es nur für Modus-B-Sessions.
- **Eingabeart Kamera/Manuell wie im Helferclient** (`ScanStationSession.
  input_mode`, `POST /api/scan-station/input-mode`): Umschalter im
  Kamera-Dropdown der Station (Aufbau aus `scan.html`) — im manuellen Modus
  wird die Kamera gestoppt, `#reader` zum Eingabefeld, der Taschenlampen- zum
  Enter-Knopf, plus rotes Fokus-Warnbanner, wenn das Feld den Fokus verliert
  (sonst tippt ein Handscanner ins Leere). Die Station merkt sich die Wahl
  lokal; eine Host-Vorgabe überschreibt sie — genau wie beim Theme, mit einem
  zweiten Schieberegler „Manuell" neben „Dunkel" im Stations-Reiter.
- **Bugfix im Aufräumpfad:** Der `finally`-Block des Stations-WS (und
  `/departed`) löst die `ws`-Referenz jetzt **vor** dem Freigeben des
  Schülers. Vorher versuchte `release_station_student` noch `released`/`ready`
  auf den bereits toten Socket zu schicken; das verzögerte das Aufräumen von
  Schüler und Worker-Context (im Test als Zeitrennen sichtbar).
- **Annahmeregeln** (`resolve_station_code`): streng bei Unsicherheit — Schüler
  muss in einer offenen Klasse sein, nicht `done`, ohne Helfer, ohne gepairte
  Handy-Session und nicht an einer anderen Station. Code-Versuche sind pro
  Station auf 10/Minute gedrosselt.
- **30-s-Leerlauf**: fällt zurück auf „Zettel-Code scannen", zählt aber **erst
  ab `worker_ready`** — Warten auf einen freien Playwright-Context oder das
  Laden der Kartei darf den Schüler nicht hinauswerfen. Client-Timer mit
  Restzeit-Anzeige plus Server-Sweeper (5-s-Takt) als Sicherheitsnetz.
- **Worker-Pool bekommt eine echte Warteschlange** (`automation/worker.py`):
  Statt `Condition.notify_all()` („wer den Lock zuerst erwischt") jetzt eine
  Waiter-Liste mit Rangfolge **Helfer → Scan-Station → Schülerclient**, FIFO
  innerhalb einer Rolle; ein freiwerdender Context wird dem ranghöchsten
  Wartenden direkt zugeteilt. `open_student(priority=…, on_wait=…)`; Wartezeit
  12 s für Helfer, 60 s für Station/Handy. Wartende bekommen ihre Position
  gemeldet (`worker_waiting`) statt eines stummen „Wird geladen…".
- **Druck** läuft durch dieselbe Druckerwarteschlange wie ein Host-Leihschein
  (`PrintJob.kind="station_sheet"`, `role="host"`) — auf dem Drucker-Display
  also gewohnt mit Klasse/Name und Host-Symbol. `_mark_slip_printed_after_
  completion` überspringt `station_sheet` explizit: ein Zetteldruck darf weder
  den „Leihschein gedruckt"-Marker setzen noch eine Modus-B-Session abschließen.
- **Host-UI**: Reiter je Station unten im Live-Ausgabe-Kasten (Titel = Name,
  sonst Registrierungs-Code; „+" zeigt QR + URL; × verbietet endgültig). Panel
  mit Name, QR, Dunkel-Schalter und — falls jemand angemeldet ist — dessen Name
  plus „Freigeben". Gedruckt wird im Pairing-Kasten der Klasse:
  „Scan-Station: [Schüler] [Drucken]" öffnet den bekannten Druck-Dialog (ohne
  die „2. Seite"-Option, der Zettel ist einseitig).
- **Draht-Format**: `state_snapshot()` bekommt `scan_stations`,
  `worker_pool` zusätzlich `waiting` (Wartende je Rolle) —
  `tests/test_state_contract.py` entsprechend erweitert.
- Tests: `tests/test_scan_station.py` (Barcode-Maße im PDF, Code-Vergabe,
  Endpunkte inkl. Eingabeart, Annahmeregeln, TTL, Druckauftrag),
  `tests/test_ws_scan_station.py` (WS-Fluss end-to-end) und vier neue
  Rangfolge-Tests in `tests/test_worker_pool.py`. **532 Tests grün.**

## 2026-08-11 — Schüler-Session überlebt ein ausgeschaltetes Handy

- Symptom: Wurde ein Schüler-Handy mitten in der Ausgabe ausgeschaltet (oder nur
  lange gesperrt) und wieder eingeschaltet, landete der Schülerclient wieder
  beim Pairing-Code. Zwei unabhängige Ursachen:
  1. Der `session_token` lag in `sessionStorage` und starb mit der Tab-Session,
     sobald das Handy den Browser-Prozess beendete → `join()` legte eine neue
     Session mit neuem Code an.
  2. Der Sweeper entwertete gepairte Sessions nach `paired_idle_ttl_s` ab
     `last_activity`; das Feld wird nur von eingehenden WS-Frames aufgefrischt,
     einen Heartbeat gibt es nicht. 15 min ohne Scan reichten also — auch bei
     offenem Tab, etwa beim Warten in der Schlange.
- Fix 1 (`web/student.js`): Der Token liegt jetzt in `localStorage` (inklusive
  Einmal-Migration aus `sessionStorage`); `clearToken()` räumt beide Ablagen,
  greift also weiterhin bei `closed` und Close-Code 4006 — ein Folgeschüler am
  selben Gerät erbt keinen fremden Token. Neu behandelt wird Close-Code 4009
  („Neue Verbindung" aus `_take_over_ws`): Da sich zwei Tabs den Token über
  `localStorage` teilen können, reconnectet der übernommene Tab nicht mehr,
  sondern zeigt „In anderem Tab geöffnet" — sonst würden sich beide Tabs
  endlos gegenseitig rauswerfen.
- Fix 2 (`server/sessions.py`, `server/state.py`, `server/routes/ws.py`):
  `StudentSessionB.disconnected_at` hält fest, seit wann keine WebSocket mehr
  hängt (`None` = verbunden; gesetzt im `finally` von `ws_student`, geleert
  beim Verbindungsaufbau). Die TTL-Regeln stecken jetzt in der reinen Funktion
  `expired_student_sessions()`: Eine **verbundene** gepairte Session verfällt
  nicht mehr — der offene Socket ist der Liveness-Beweis, tote Sockets schließt
  Uvicorns WS-Ping/Pong-Keepalive (~40 s) und setzt damit `disconnected_at`.
  Erst getrennt läuft das TTL, mit Fallback auf `last_activity`.
- `paired_idle_ttl_s` (`PAIRED_IDLE_TTL_S`) bedeutet damit „gepairt **und
  getrennt**" und steigt von 900 s auf 1800 s — 30 min überbrücken ein
  zwischendurch ausgeschaltetes Handy, ohne dass eine wirklich abgebrochene
  Session den Worker-Context ewig hält.
- Neu: `tests/test_session_ttl.py` (5 Fälle: verbunden/still, kurze Trennung,
  Trennung über TTL, Fallback ohne `disconnected_at`, pending nach Alter).
  Suite grün.

## 2026-08-11 — Hinweis „Seite noch nicht schließen" im Unterschreiben-Modus

- Im Schülerclient endete der Leihschein-unterschreiben-Modus (`view-slip`,
  `web/student.html`) bisher mit der Leihschein-Info-Box; es fehlte ein Hinweis,
  dass die Seite noch offen bleiben muss, bis der Helfer abschließt.
- Unter der Info-Box steht jetzt „Seite noch nicht schließen" — bewusst nicht
  fett und mit denselben Schrifteigenschaften/Abständen wie der Satz „Du hast
  den Ausleihvorgang nun abgeschlossen und kannst die Seite schließen." im
  Schülerleihscheinmodus (`view-done`): `class="text"` plus `margin-top: 20px`,
  dazu `font-weight: 400`, damit die Zeile garantiert nicht fett rendert. Nur
  das Wort „nicht" steht — wie beim Name/Klasse/Datum-Hinweis im
  Schülerleihscheinmodus — in `<strong>`.

## 2026-08-11 — „Bestand leer"-Hinweis auf dem Leihschein größer und mittig

- Der Hinweis auf fehlende Fächer (Reihen im Status „Bestand leer") saß dicht
  unter der Klassen-Wertzeile und war mit 85 % der Klassen-Schriftgröße klein.
  `overlay_missing_stock_note()` (`server/loan_slip.py`) setzt ihn jetzt mit
  130 % dieser Größe und platziert ihn vertikal mittig in die Lücke zwischen
  der Klassen-Zeile und der großen Überschrift „Leihschein" — die Oberkante der
  Überschrift wird dafür über `_find_heading_top()` aus dem PDF gelesen.
- Defensiv wie zuvor: Wird die Überschrift nicht gefunden, bleibt es beim alten
  Verhalten (fester Abstand unter der Klassen-Zeile); der Hinweis ragt nie in
  die Klassen-Zeile hinein, und Fehler lassen den Druck nie scheitern.

## 2026-08-11 — Bücherliste scrollt hinter dem Fokus-Warnbanner weiter

- Das rote Banner „Vorsicht: Eingabefeld nicht fokussiert!" (manueller Modus im
  Helfer-Client) liegt `position: fixed` über der Bücherliste und verdeckte
  dadurch meist die unterste Buchzeile. `updateFocusBanner()`
  (`web/scan-render.js`) misst jetzt die tatsächliche Bannerhöhe und setzt sie
  als CSS-Variable `--focus-banner-h`; `.book-table-wrap` (`web/scan.html`)
  nutzt sie als `padding-bottom`, sodass sich die Liste genau bis zur
  Banner-Oberkante weiterscrollen lässt. Gemessen statt fest verdrahtet, weil
  der Bannertext auf schmalen Displays umbrechen kann; ein `resize`-Listener
  misst nach Rotation neu, ohne Banner ist das Padding `0px`.

## 2026-08-10 — „Bestand leer": roter Strich ohne Kasten + Fehlt-Hinweis auf dem Leihschein

- **Helfer-Client:** das rote „−"-Icon für Bestand-leer-Zeilen zeigt jetzt nur
  den roten Strich, ohne umgebenden Kasten/Hintergrund (`web/scan.html`,
  `.empty-stock-badge`).
- **Leihschein (Seite 1, Schul-Kopie):** hat ein Schüler eine vorgemerkte,
  noch nicht ausgeliehene Reihe, die als „Bestand leer" markiert ist, wird
  mit etwas Abstand unter der Klassen-Zeile eine Hinweiszeile mit den
  betroffenen Fächern (kommagetrennt) + „fehlt"/„fehlen" eingefügt
  (`server/loan_slip.py::overlay_missing_stock_note`, aufgerufen aus
  `server/sessions.py::print_loan_slip_for` über die neue Hilfsfunktion
  `_missing_stock_subjects_for`). Rein lokale PDF-Bearbeitung wie die
  bestehende Klassen-Korrektur, betrifft nur Seite 1 (Seite 2/
  Schüler-Leihschein bleibt unberührt).

## 2026-08-10 — Dritter Buchreihen-Status „Bestand leer"

- Neben „da"/„ausgeblendet" gibt es jetzt einen dritten Status für Buchreihen:
  **„Bestand leer"** (physischer Bestand vorübergehend leer, Reihe bleibt aber
  vorgemerkt/buchbar — anders als „ausgeblendet"). Global gepflegt
  (`IservCaches.empty_isbns` in `server/state.py`, nicht pro Jahrgang, da
  Mehrjahresbände denselben physischen Bestand über mehrere Jahrgangs-
  Kataloge hinweg teilen), persistiert über `server/booklist_store.py`
  (dritter, flacher JSON-Key `"empty"`).
- **Helfer-Client** (Modus A, `web/scan-*.js`): „Bestand leer"-Zeilen zeigen
  ein rotes Kästchen mit „−" statt des normalen Status-Icons. Beim Drucken
  oder Aufrufen des nächsten Schülers (nur wenn dabei ein zugewiesener
  Schüler beendet wird) erscheint bei noch offenen vorgemerkten Büchern eine
  Checkbox „Als Keine Bücher im Lager mehr markieren" — angehakt + bestätigt
  markiert die noch offenen Bücher als Bestand leer (WS `mark_empty_stock`).
  Wird ein Bestand-leer-Buch erneut gescannt, fragt ein nicht-blockierendes
  Ja/Nein-Popup „Soll dieses Buch wieder als 'da' gelten?" — die Buchung
  selbst läuft in jedem Fall normal weiter (WS `clear_empty_stock` bei „Ja").
- **Schüler-Client** (Modus B): sieht nichts davon — weder Markierung noch
  Popup — kann das Buch aber ganz normal selbst einscannen
  (`apply_empty_stock_flag(..., visible=False)` in `server/sessions.py`
  setzt das Flag nur für den Helfer-Payload, entfernt die Zeile nie).
- **Buchungs-Vorabprüfung** (`evaluate_scan_for_booking`) bewusst unverändert
  — „Bestand leer" darf den `not_in_stock`-Gate niemals blockieren.
- **X/Y-Fortschrittszähler** (Host-Queue, `web/host-render.js`
  `activeStatusDetails`): Bestand-leer-Bücher zählen wie ausgeblendete aus
  dem aktiven Soll raus, die wahre Gesamtzahl bleibt aber in Klammern
  sichtbar (`X/Y (Z)`) — verschwindet automatisch, sobald das Buch
  tatsächlich gescannt wird (`QueueStudent.books_empty_outstanding`).
- **Host-Admin-Bücherlisten-Editor**: drittes gelbes Feld mit „0"
  (`ICON_EMPTY`), 3-Wege-Klick-Zyklus da → Bestand leer → ausgeblendet → da
  (`onBlCycleStatus` in `web/host-render.js`), neuer Endpoint
  `POST /api/booklist-empty` mit „scoped replace" (ein Jahrgang darf beim
  Speichern keine Bestand-leer-Flags anderer Jahrgänge löschen).

## 2026-08-10 — Drucker-Auswahlmenüs: Popover/Dropdown ragten aus dem Bildschirm

- **Druckerauswahl-Dropdown** (`common.js` `mountPrinterPicker`, Klasse
  `.printer-picker`/`.pp-panel`, genutzt in `host.css` und `scan.html`):
  öffnet jetzt immer nach oben statt nach unten (`.pp-panel-up`), `max-height`
  wird dynamisch auf den tatsächlich verfügbaren Platz oberhalb des Triggers
  begrenzt (`positionPanel()` in `common.js`).
- **„+"-Popover an einer Drucker-Anzeige** (`openPdAddMenu` in
  `host-render.js`, Klasse `.pd-add-popover`): öffnet ebenfalls oberhalb der
  „+"-Box statt darunter; Höhe/Breite werden erst nach dem Anhängen an
  `document.body` gemessen (Timing-Voraussetzung für korrekte
  `getBoundingClientRect()`-Werte), horizontal an den rechten Viewport-Rand
  geklemmt; zusätzlich `max-height: 60vh` mit Scroll in `host.css` für lange
  Drucker-Listen.
- **Debugging-Lektion:** Nach dem Fix wurde der Bug vom Nutzer zunächst als
  weiterhin bestehend gemeldet. Isolierte Nachstellung der Positionierungs-
  Logik mit Playwright (Button ganz oben + viele Drucker, Button ganz unten +
  wenige Drucker, kleines Viewport 500×700) zeigte, dass die Berechnung
  korrekt innerhalb des Viewports bleibt. Ursache war ein veralteter Stand auf
  dem Ausgabegerät (eigenständiger Checkout/Server, s. `docs/deployment.md`)
  — kein Code-Fehler. Reproduktion in Isolation vor erneutem Rätselraten am
  Live-Code hat die falsche Fährte (vermeintlich weiterhin fehlerhafte Logik)
  vermieden.

## 2026-08-10 — Betreuerauslöser-Druck: Server statt Client entscheidet, ob ein Auftrag als Schüler-Auftrag zählt

- `POST /api/print-loan-slip` (Host-Button „Aktuell in Ausgabe") verließ sich
  bisher auf ein vom Client mitgesendetes `student_client`-Flag, um einen
  Betreuerauslöser-Druck als Schüler-Auftrag (kein `host_sid`, dafür
  `student_token`) anzulegen. In der Praxis kam trotz korrekt gerendertem
  `data-student-client="true"`-Button ein Host-Auftrag an: Druckerwarteschlange
  zeigte „Host" statt „Schüler" als Auftraggeber, und der Schülerclient bekam
  keinen `print_progress`/`print_result`.
- Fix: `student_client` wird jetzt ausschließlich serverseitig aus dem State
  abgeleitet (`found[1].assigned_helper is None and found[0].slip_trigger ==
  "helper"`) — das Client-Feld ist entfallen (`PrintLoanSlipRequest.student_client`
  gestrichen, `host-render.js` sendet es nicht mehr). Damit kann der Host-Client
  diese Zuordnung nicht mehr falsch treffen; der Auftrag wird für einen live
  Schülerclient (Modus B, kein Helfer, `slip_trigger == "helper"`) ausnahmslos
  wie ein Auftrag vom Schüler selbst behandelt — identisch zum
  Betreuerauslöser-Pfad im Helferclient (`print_for_student` in
  `routes/ws.py`, war bereits korrekt).
- Neue Regressionstests: `tests/test_print_loan_slip_route.py`.
- Vorheriger Zwischenschritt derselben Session (Druckerbutton in der
  Klassen-Tabelle `renderCtxQueue` entfernt, nur noch „Aktuell in Ausgabe" +
  Helferclient-Warteschlange als Auslöser) bleibt bestehen.

## 2026-08-10 — Host-Aktionssymbole „Aktuell in Ausgabe" überarbeitet

- **Trennen:** Statt des geometrischen Ketten-Symbols (Eintrag 2026-08-09)
  jetzt ein durchgestrichenes WLAN-Symbol (Bögen + Punkt, Diagonale von
  links unten nach rechts oben).
- **Abschließen:** Statt des Häkchens jetzt eine Zielflagge, reduziert auf
  ein großes 4x4-Karomuster ohne Fahnenmast.
- Drucker- und Unterschrift-Symbol (`ICON_PRINTER`, `ICON_SIGN`) auf dieselbe
  vergrößerte Darstellung (neue CSS-Klasse `.ico-lg`, 1.55em statt 1em)
  gebracht, damit alle vier Aktionssymbole in „Aktuell in Ausgabe"
  größenkonsistent wirken.

## 2026-08-10 — Schülerclient: Unterschriften-Bestätigung entfernt, Scan-View/Statuszeilen-Layout überarbeitet

- **Unterschriften-Modus:** Der Schülerclient bietet keine Möglichkeit mehr,
  den Leihschein selbst als unterschrieben zu markieren — der „Leihschein
  unterschrieben"-Button (samt `finish_signed`-Aufruf) wurde entfernt. Der
  Abschluss dieses Schritts läuft damit ausschließlich noch über den
  Helferclient (Stift-Button, s. Eintrag 2026-08-07); serverseitig ist
  `finish_signed`/`slip_signing` unverändert. Dies ersetzt die Änderung vom
  2026-08-09 („Leihschein-unterschrieben"-Button im Schülerclient").
- Der Button „Leihschein erhalten" heißt jetzt „Ich habe den Leihschein
  erhalten"; die Druck-Statusbox behält nach dem Druck ihr Aussehen
  (Rahmen/Fläche) bei, statt sich auf eine schlichte Zeile zu reduzieren.
- **Scan-View:** Die Statuszeile sitzt jetzt direkt unter der Kamera (gleicher
  Abstand wie zwischen den übrigen Top-Bar-Elementen: Zahnrad/Kamera/Torch),
  Name und Klasse stehen darunter ohne umschließende Box. Ein Scan erzeugt
  sofort die Rückmeldung „<Code> wird geprüft" in der Statuszeile, statt erst
  nach der Serverantwort.
- **Druck-/Unterschreiben-/Abschluss-Screens:** Name/Klasse stehen dort
  ebenfalls ohne Box; der Abschluss-Screen („Vorgang abgeschlossen") zeigt
  jetzt zusätzlich Name/Klasse, fix oben stehend und außerhalb des
  scrollbaren Bereichs (analog zu Druck-/Unterschreiben-Modus). Der
  Leihschein-Hinweiskasten ist dort nicht mehr am unteren Bildschirmrand
  fixiert, sondern folgt im normalen Fluss direkt unter den
  Text-/Button-Elementen.

## 2026-08-09 — Betreuerauslöser-Hinweis im Schülerclient

- Im Schülerclient zeigt der Betreuerauslöser jetzt den Hinweis „Bitte wende
  dich an einen Betreuer, damit dein Leihschein gedruckt werden kann."
- Der Schülerclient bietet in diesem Modus keinen eigenen Druckbutton mehr;
  der Druck wird über den Betreuer/Host ausgelöst. Die Bestätigung „Leihschein
  erhalten" nach einem erfolgreichen Druck bleibt davon unberührt.

## 2026-08-09 — Aktionssymbole und Modus-B-Leihscheinaktionen

- **Host:** Die Aktionen in „Aktuell in Ausgabe" verwenden jetzt Haken-,
  Drucker- und geometrisches Link-trennen-Symbol; die Beschriftungen bleiben
  über Tooltip und `aria-label` erreichbar. Im Unterschriftenmodus erscheint
  zusätzlich das Signatur-Symbol.
- **Betreuerauslöser:** Ein Schülerclient kann den Druckbutton im Druckmodus
  anzeigen und den Auftrag als `student` statt als Host-/Helferauftrag einreihen.
  Der Host-Pfad für diese Aktion verwendet dieselbe Rollen-/Statuslogik.
- **Unterschriftenmodus:** Der Schülerclient erhält wie der Helferclient einen
  „Leihschein unterschrieben"-Button. Der Abschluss ist serverseitig auf die
  aktivierte Klassenoption und den echten Unterschriftenmodus begrenzt.

## 2026-08-09 — Modus-B-QR-Steuerung verfeinert

- Der Host nennt den Verbindungs-QR jetzt „QR für QR-Display anzeigen".
- Verbundene, autorisierte QR-Displays und Lehrkraft-Ansichten verwenden zum
  Trennen ein einheitliches X-Symbol.
- Die Pause-/Fortsetzen-Aktion steht rechts getrennt von „Ausgabe schließen"
  und verwendet Pause-/Play-Symbole.
- Bei pausierter Anzeige kann der Host die QR-Anzeige für genau drei neue
  Schüler-Scans freischalten; danach pausiert sie automatisch wieder.
- Die Pause-/Play-Steuerung erscheint nur, wenn mindestens ein autorisiertes
  QR-Display verbunden ist; der Pausenhinweis ist für 11"-iPads größer und
  durch zusätzliche Zeilenumbrüche aufgelockert.

## 2026-08-09 — Modus B: iPad-Registrierungscode im Host aufgeräumt

- **Host-UI:** Der iPad-Eintrag nutzt jetzt dieselbe `code-row`-/`code-val`-
  Darstellung wie die übrigen Pairing-Codes statt einer Inline-Textzeile.
- **Ignore:** Ein X blendet einen nicht autorisierten iPad-Code lokal in dieser
  Host-Ansicht aus. Das iPad bleibt verbunden; ein Host-Reload zeigt den Code
  wieder und erzeugt keinen unerwarteten Reconnect mit neuem Code.
- **Verbundene iPads:** Freigeschaltete und wartende, verbundene iPads bleiben
  in der Liste sichtbar. Über „Trennen" wird die Display-Session beendet; die
  QR-Seite reconnectet dabei nicht automatisch, sondern muss bewusst neu
  geladen werden.
- **Pause:** Neben „Ausgabe schließen" pausiert „QR pausieren" die QR-Anzeige
  auf allen autorisierten iPads. QR-Code und URL werden durch einen Hinweis
  ersetzt; der Button wird zu „QR fortsetzen".

## 2026-08-09 — Security review of Modus B and public routes

- Read-only source review found no cross-student data-access path: a Modus-B
  session receives data only after host pairing; completion, abort and timeout
  hard-revoke its token, worker and WebSocket.
- Deliberately public static/capability routes were catalogued. FastAPI's
  default `/docs`, `/redoc` and `/openapi.json` disclose the API contract but
  bypass no authorization; disabling them remains an optional deployment
  hardening measure.
- **Follow-up:** server-side checks do not yet require completed books before
  a student manually sends the WebSocket `print_request` or `finish` frame.
  This is workflow integrity, not cross-student data disclosure.
- Details: `docs/security_review_2026-08-09.md`.

## 2026-08-09 — Ladezustand von Host und Client synchronisiert

- **Host:** Aktive Schüler zeigen während des Bücher- und Worker-Ladevorgangs
  den Status „Lädt" statt „Aktiv" oder eines vorzeitigen X/Y-Fortschritts.
- **Helferclient:** Der Schüler erscheint direkt als aktiv mit Helfernamen;
  der X/Y-Fortschritt wird nach abgeschlossenem Ladevorgang ergänzt.

## 2026-08-09 — Status in „Aktuell in Ausgabe" synchronisiert

- **Host:** Die Statusanzeige in der Kachel „Aktuell in Ausgabe" verwendet
  jetzt dieselben dynamischen Werte wie die Klassenliste: Fortschritt mit
  „ohne Mjb"/„gesamt" sowie die Leihschein-Status „wartet", „druckt",
  „gedruckt" und „Unterschrift".
- **Wartbarkeit:** Die gemeinsame Statuslogik liegt nur noch an einer Stelle,
  damit beide Ansichten synchron bleiben.

## 2026-08-09 — Status in „Aktuell in Ausgabe"

- **Host:** Die Kachel „Aktuell in Ausgabe" zeigt neben der Klasse linksbündig
  den Schülerstatus und — sofern ein Helfer zugeordnet ist — den Helfernamen
  rechtsbündig in einer gemeinsamen Zeile. Status und Helfer verwenden dabei
  dieselben Schrifteigenschaften.

## 2026-08-09 — Aktiver Helfer in Queue-Status + Druckstatus nach neuem Scan

- **Host:** Aktive Schüler zeigen in der Statusbox die Reihenfolge Status →
  Personensymbol/Helfername → Hinweis.
- **Helferclient:** Aktive Schüler zeigen Helfername → X/Y bzw. Druckersymbol →
  Hinweis. Der Helfername wird serverseitig aus der Zuordnung aufgelöst.
- **Schülerclient:** Die Identitätsbox steht vor der Statusbox; ein vorhandener
  Helfername wird ohne Personensymbol angezeigt.
- **Druckstatus:** Ein neuer erfolgreicher Buchscan macht einen laufenden oder
  bereits abgeschlossenen Leihschein logisch veraltet. Der alte physische
  Druck läuft gegebenenfalls weiter, treibt aber weder `Leihschein ...` noch
  `slip_printed` weiter. Erst ein neuer Druckauftrag aktiviert den Statuszyklus
  erneut; alte Druckergebnisse können einen neuen Auftrag nicht überschreiben.
+ **Tests:** `uv run pytest --no-cov -ra` (424 Tests), Ruff und `node --check` grün.

## 2026-08-09 — Helfer-Queue: Aktionsabstände und Unterschriftenstatus

- **UI:** In der Aktiv-Gruppe entfällt die leere Aktionsspalte ohne Druck- oder
  Unterschriftbutton. Das Druckersymbol steht dadurch mit normalem Abstand vor
  dem „Aufrufen"-Button.
- **UI:** Während des Unterschriftenstatus wird die X/Y-Fortschrittsanzeige
  ausgeblendet; der Unterschriftbutton bleibt als Aktion sichtbar.
- **Tests:** `uv run pytest -q` erfolgreich.

## 2026-08-09 — Modus B: Leihschein-Status-Feinschliff (Unterschrift live + Helfer-Druckersymbol)

- **Problem (Host):** der Status „Unterschrift" erschien in der Host-Queue erst
  nach einem Seiten-Reload statt live wie die übrigen Leihschein-Status.
  `confirm_slip_received` (`server/sessions.py`) setzte `slip_signing` und
  broadcastete nur an den Helfer-Client (`broadcast_queue_size`), nicht an den
  Host — der Host-Snapshot blieb bis zum nächsten Reload auf „Leihschein
  gedruckt".
- **Fix (Host):** `confirm_slip_received` broadcastet jetzt zusätzlich
  `state_snapshot()` an den Host (analog `_mark_slip_printed`), sodass der
  Wechsel auf „Unterschrift" live passiert.
- **Problem (Helfer):** im Helfer-Client blieb das Druckersymbol sichtbar, wenn
  bereits der „Leihschein unterschreiben"-Button erschien.
- **Fix (Helfer):** `queueInfoIcons` (`web/scan-render.js`) unterdrückt das
  Druckersymbol, sobald `slip_signing` gesetzt ist (der Sign-Button verdrängt
  es). Info-Badges (nicht angemeldet / Antrag / offener Betrag) bleiben
  unverändert.
- **Tests:** `test_student_session_enters_slip_mode_when_signature_required`
  (`tests/test_print_queue.py`) prüft jetzt zusätzlich, dass nach
  `confirm_slip_received` ein Host-Snapshot-Push geht, der den Schüler mit
  `slip_signing: True` enthält (Regression für den Reload-Bug).

## 2026-08-08 — Modus B: granulare Leihschein-Status in Host-Queue + Helfer-Client

- **Problem:** die Host-Queue zeigte für einen Modus-B-Schüler (Schülerclient)
  die aktive Phase nur grob (`X/Y` → `Leihschein` während Druck → `Aktiv` →
  `Fertig`). Der Leihschein-Workflow ließ sich so nicht live verfolgen.
- **Neu:** granulare Status in der Host-Queue, nacheinander: `Wartend` →
  `X/Y ohne Mjb X/Y gesamt` → `Leihschein wartet` (Druckauftrag gesendet) →
  `Leihschein druckt` (OS druckt aktiv) → `Leihschein gedruckt` →
  `Unterschrift` (Schüler im Unterschreiben-Modus) → `Fertig` (erst bei echtem
  Abschluss, Session geschlossen).
- **Server:** `PrintQueue.print_job_states()` (`server/print_queue.py`) liefert
  je Schüler `"waiting"` (queued/dispatching/spooled) oder `"printing"`.
  `QueueStudent.as_dict` (`server/state.py`) bekommt `slip_status` statt des
  reinen Bools; `slip_printing` bleibt als abgeleitetes Bool im Payload
  (Helfer-Client nutzt es weiter). `queue_as_list`/`real_contexts_summary`/
  `state_snapshot` stellen auf `print_job_states()` um; `teacher_snapshot`
  bleibt unverändert.
- **UI:** Host-Queue (`web/host-render.js`) zeigt die neuen Badges
  `Leihschein wartet`/`druckt`/`gedruckt`/`Unterschrift`. Helfer-Client
  (`web/scan-render.js`) zeigt das Druckersymbol für wartet/druckt/gedruckt
  und keine Symbole mehr bei `Fertig` (Info-Badges wie nicht angemeldet /
  Antrag / offener Betrag bleiben).
- **Tests:** `tests/test_print_queue.py` neu: `print_job_states()` liefert
  `waiting` bei Enqueue und `printing` bei OS-Druck, danach leer.

## 2026-08-08 — Modus B: eigener Status „abwesend" statt „übersprungen" für Lehrkraft-Aktion

- **Problem:** die Lehrkraft-Aktion „Als abwesend markieren" (`/api/teacher/skip`)
  setzte den Schüler-Status auf `skipped` (übersprungen) — das entfernte den
  Schüler aus der Warteschlange und vermischte zwei Konzepte.
- **Neu:** eigener Status `absent` (`QueueStudent.status`, `server/state.py`).
  Die Lehrkraft-Aktion setzt jetzt `pending -> absent` (`server/routes/teacher.py`),
  Rücknahme `absent -> pending`. Ein abwesender Schüler bleibt wie ein wartender
  in der Queue (aufrufbar, `next_pending`/`pending_queue_as_list`/
  `real_contexts_summary` zählen ihn mit), mit der einzigen Einschränkung, dass
  die normale Schülerclient-Zuordnung (`student_pair`) für ihn blockiert ist.
  Der Helfer-Scan-Einmal-QR bleibt verfügbar (`helper_scan_start`/
  `student_helper_join` erlauben jetzt `skipped` und `absent`).
- **UI:** Lehrkraft-Ansicht zeigt „Abwesend" (eigener Zähler + Punkt), Host-Queue
  zeigt ein „Abwesend"-Badge ohne Pairing-Button (dafür Helfer-Scan +
  Überspringen + Trennen). `skipped` bleibt für die Host-Aktion „Überspringen"
  und das Auto-Skip beim Klassen-Laden bestehen.
- **Tests:** `tests/test_teacher.py` angepasst (skip → absent, counts um
  `absent` erweitert) + neu: abwesend aufrufbar, nicht paarbar, Helfer-Scan
  verfügbar, `teacher_snapshot` zählt `absent`.

## 2026-08-08 — Modus B: Schülerleihschein beim Leihschein-Druck vorladen

- **Problem:** der Schülerleihschein (Eigenabruf, Aktionen der letzten 3
  Monate) wurde erst in `invalidate_session` beim Übergang zu „abgeschlossen"
  frisch von IServ geholt — das verzögerte den Wechsel in den
  Schülerleihscheinmodus (Abschluss-Screen).
- **Neu:** `StudentSessionB` trägt einen Cache (`own_slip_data_b64` +
  `own_slip_filename`, `server/state.py`). `print_loan_slip_for` stößt nach
  dem normalen Leihschein-Fetch einen Fire-and-forget-Prefetch an
  (`sessions._prefetch_own_slip` / `_prefetch_own_slip_task`, starke
  Referenz analog `_release_tasks`), der den Schülerleihschein
  (`variant="student"`, `3months`) auf der Session cacht. Nur Modus B
  relevant — Modus-A-Drucke ohne Session tun nichts.
- **Abschluss:** `_send_own_slip_download` nutzt den Cache (verzögerungsfrei)
  und fällt sonst auf den Frisch-Fetch zurück; Signatur nimmt jetzt die
  `session` statt nur `student_id` (nötig, weil `find_session_by_student` nur
  `pending_pairing`/`paired` findet, beim Abschluss ist die Session aber schon
  `completed`). Fehler bleiben kosmetisch — der Abschluss wird nie blockiert.
- **Tests** (`tests/test_print_queue.py` +1): Prefetch füllt den Cache,
  Abschluss nutzt ihn ohne weiteren IServ-Fetch. **416** Offline-Tests grün;
  `ruff` clean.

## 2026-08-08 — Modus B: „Abwesend" + Helfer-Scan in Host-Queue & Lehreransicht

- **Problem:** ein abwesender Schüler, dessen Bücher per „Bücher als Helfer
  einscannen" eingescant wurden, war in Host-Queue und Lehreransicht nicht vom
  normalen (anwesenden) Ablauf zu unterscheiden. Dabei ist der physische
  Bücherstapel beim Abwesenden ein eigener Übergabe-Schritt.
- **Neu:** `QueueStudent.helper_scanned` (`server/state.py`) — wird in
  `student_helper_join` gesetzt, wenn der Schüler beim Helfer-Scan `skipped`
  war. Im Draht-Format (`as_dict`, `teacher_snapshot`) mitgeführt; beim
  Zurücksetzen auf „Wartend" (`reset_progress`) gelöscht.
- **Host-Queue** (`web/host-render.js`): ein `done`-Schüler mit
  `helper_scanned` zeigt **„Fertig (abwesend)"** statt „Fertig" (Hint-Badges
  ersetzen ihn wie bisher, wenn einer vorliegt).
- **Lehreransicht** (`web/teacher.js`): die Leihschein-Checkbox lautet bei
  `helper_scanned` **„Leihschein & Bücherstapel entgegengenommen"** statt
  „Leihschein entgegengenommen".
- **Tests** (`tests/test_teacher.py` +1, `tests/test_sessions.py` +1): Snapshot
  propagiert `helper_scanned` (done, Status unverändert), Seri-
  alisierung + Reset. Bestehender Shape-Test um den neuen Key erweitert.
  **415** Offline-Tests grün; `ruff` clean.

## 2026-08-08 — IServ-Read-Timeout konfigurierbar, Default gesenkt (30s → 10s)

- **Anlass:** Der Leihschein-Druck fühlte sich manchmal langsam an, obwohl der
  Drucker selbst gerade nichts zu tun hatte. Ursache: `print_loan_slip_for()`
  holt das PDF synchron per IServ-GET, *bevor* überhaupt etwas an `lp`/Sumatra
  übergeben wird — der Drucker-Slot ist dabei laut interner Warteschlange
  schon belegt (`print_queue._claim_fills`/`_dispatch`), aber am OS-Drucker
  taucht noch nichts auf. Mit dem alten Default-Read-Timeout der `ausleihe-api`
  (30s, plus bis zu 2 Retries) konnte eine hängende IServ-Antwort den Auftrag
  entsprechend lange unsichtbar hängen lassen.
- **Neu:** `server/config.py` — Feld `iserv_read_timeout_s` (Default **10s**),
  per `ISERV_READ_TIMEOUT_S` in `.env` einstellbar (Connect-Timeout bleibt fest
  bei 5s). `server/iserv_client.py` (`IsServClient.__init__`) reicht
  `timeout=(5.0, read_timeout_s)` an `AusleiheClient` durch; `server/app.py`
  übergibt `cfg.iserv_read_timeout_s` bei der Instanziierung. Betrifft alle
  IServ-Reads (nicht nur den Leihschein-PDF-Abruf).
- Test-Fakes in `tests/test_iserv_client_locking.py` und
  `tests/test_iserv_client_borrower.py` mussten den neuen `timeout`-Kwarg in
  ihrer `AusleiheClient`-Konstruktor-Signatur akzeptieren. Volle Suite grün,
  `ruff` clean.

## 2026-08-08 — Modus B: „Bücher als Helfer einscannen" für übersprungene Schüler

- **Problem:** ein übersprungener Schüler (Status `skipped` — z. B. Gerät
  defekt, Schüler anwesend aber nicht scannfähig) konnte seine Bücher bisher
  nicht ausleihen: der normale Modus-B-Pfad verlangt, dass der Schüler selbst
  über `student.html` scannt.
- **Neu:** Host-Button **„Bücher als Helfer einscannen"** in der Queue für
  `skipped`-Schüler (nur bei offener Ausgabe). Er öffnet ein QR-Modal mit
  einem **einmaligen Helfer-Secret** (`/student?h=<secret>`). Ein Helfer/Admin
  scannt den QR mit dem eigenen Handy → `student.html` bindet eine Modus-B-
  Session **direkt** an den übersprungenen Schüler (ohne Pairing-Schritt) →
  der Helfer scannt die Bücher, sie werden über den bestehenden
  `process_scan`-Pfad für den Schüler verbucht.
- **Server** (`server/routes/modus_b.py`):
  - `POST /api/helper-scan/start` (Host-only, Body `StudentRef`): validiert
    `skipped`, ersetzt ein evtl. altes Secret desselben Schülers (nur ein
    gültiger QR), liefert `{url, qr}`.
  - `POST /api/student/helper-join` (öffentlich, Body `HelperJoinRequest`):
    poppt das Secret (einmalig), validiert Schüler noch `skipped` + ohne
    bestehende Session, bindet die Session (analog `student_pair`, ohne
    Payment-Gate — der Host entscheidet bewusst) und startet
    `load_and_push_paired_student`. Bewusst **kein** `modus_b_open`-Check:
    das Secret ist eine vom Host gezielt erzeugte Einmal-Capability.
  - `state.helper_scan_secrets` (`server/state.py`), TTL
    `helper_scan_ttl_s` (Default 600 s, `server/config.py`), Aufräumen im
    Sweeper via `sweep_helper_scan_secrets` (`server/sessions.py`).
- **Frontend** (`web/host-render.js`): `helperScanBtn` in `renderCtxQueue`
  für `skipped`-Schüler, `helperScan(studentId)` → `showQr(d.qr, d.url)`,
  Action-Switch `helper-scan`. (`web/student.js`): `?h=`-Param →
  `helperJoin()` → `POST /api/student/helper-join`, Boot-Zweig
  `helperSecret ? await helperJoin() : await join()`.
- **Tests** (`tests/test_api_guards.py` +9, `tests/test_sessions.py` +1):
  Start-Endpoint (403/400/404/409, `url`+`qr`, Secret-Ersetzung), Join-Endpoint
  (403 unbekannt, einmalige Nutzung, Bindung → `active`+`paired`, 409 wenn
  nicht mehr `skipped`), Sweeper-TTL. **413** Offline-Tests grün; `ruff`
  clean.

## 2026-08-08 — Modus B: wartenden Pairing-Code verwerfen (X-Button neben „Zuordnen")

- **Problem:** ein Schüler, der die Ausleihe abgeschlossen hat und die
  Schüler-Seite neu lädt, re-joined über das in der URL verbleibende
  Join-Secret → neue `pending_pairing`-Session → neuer 4-stelliger Code poppt
  am Host auf. Bisher ließ sich so ein Code nur durch Zuordnung oder
  Timeout-Ablauf loswerden.
- **Neu:** `POST /api/student/dismiss` (`server/routes/modus_b.py`, Body
  `StudentDismissRequest { pairing_code }` in `_deps.py`) revokiert die
  passende pending-Session via `invalidate_session(... "revoked",
  reason="host-dismissed")` + `broadcast_host` (Host-only, wie `student_pair`).
- **Kein Re-Join-Loop:** `invalidate_session` sendet dem Schüler-WS ein
  `{"type":"closed"}`-Frame **vor** `ws.close(code=4006)` → Schüler-Client
  (`student.js`) setzt `finished=true; show('done')`, der 4006-Close trifft
  auf `if (finished) return;` → kein Reconnect, kein neuer Code. Schüler
  landet auf dem bestehenden „Ausleihvorgang abgeschlossen"-Screen — richtige
  Botschaft für jemanden, der fertig ist. Kein neuer Client-Zustand nötig.
- **Frontend** (`web/host-render.js`): `ICON_CLOSE` (X-Pfad-SVG wie
  `remove-helper`); in `renderCtxPairing` ein `.danger` X-Button in **beiden**
  Code-Row-Varianten (code-first neben „Zuordnen" + armed neben dem Code-Chip),
  `data-action="dismiss-code"`. `dismissCode(code, btn)` mit
  `confirmDialog` (bewusst mit Bestätigung — Verwerfen trennt ggf. einen
  echten wartenden Schüler). Wired im Action-Switch als `dismiss-code`.
- **Tests** (`tests/test_api_guards.py`, +4):
  `test_student_dismiss_revokes_pending_session` (Session verschwindet,
  `find_session_by_code` → None), `_unknown_code_404`, `_empty_code_400`,
  `_requires_host_cookie` (403). 397 → **401** Offline-Tests grün; `ruff`
  clean.
- **Deployed:** Commit `2dd9e46` auf `main`; Server-Restart durch Niklas
  (12:38); `host-render.js` vom Server mit neuem Inhalt verifiziert. Hinweis:
  der X-Button erscheint nur auf einer pending Code-Row — bei leerer
  Modus-B-Liste ist nichts zu sehen (Host hart neu laden + Code erzeugen zum
  Sehen). Details: `_logs/2026-08-08_sba_modus_b_dismiss_button.md`.

## 2026-08-08 — Schülerleihscheinmodus: Erklär-Box, Abschluss-Text, Layout, Dateiname

- `view-done` („Vorgang abgeschlossen", jetzt umbenannt zu **„Ausleihvorgang
  abgeschlossen"**) bekommt eine Erklär-Box (`.info-box`, gleiches Muster wie
  in `view-print`/`view-slip`) mit Überschrift „Schülerleihschein": erklärt,
  dass der Schülerleihschein neben den aktuell ausgeliehenen Büchern auch
  kürzliche Rückgaben und aktuelle Ersatzansprüche auflistet, und verweist auf
  die jederzeitige Einsicht via IServ (Modul „Schulbücher" → „Meine Bücher",
  `iserv-trg-oha.de/iserv/ausleihe/myprofile`).
- Abschluss-Text umformuliert: fett hervorgehobener Hinweis „Bitte trage nun
  noch Name, Klasse und Ausgabedatum in die Bücher ein." plus Mängel-Hinweis
  (Mängel-Anzeige innerhalb einer Woche, sonst Zurechnung an den Schüler),
  jeweils mit Zeilenumbruch pro Satz; unterhalb der Info-Box zusätzlich „Du
  kannst das Fenster dann schließen." (20px Abstand zur Box).
- Download-Button umbenannt zu „Schülerleihschein herunterladen"; Größe/
  Position (Padding, Font-Size, Container-`max-width`) an die
  Druckmenü-Buttons (`#print-actions`/`#print-received-actions`) angeglichen
  (`#own-slip-actions` in dieselben CSS-Selektoren aufgenommen).
- Server: `sessions._send_own_slip_download` holt zusätzlich
  `get_student_info` und benennt die heruntergeladene PDF-Datei jetzt
  `Schülerleihschein: <Nachname>, <Vorname>.pdf` statt
  `leihschein_<student_id>_<Zeitstempel>.pdf` (Test in
  `tests/test_print_queue.py` entsprechend angepasst).
- Layout (erster Schritt, s. Nachträge unten für den finalen Stand):
  Druckmodus (`view-print`), Leihschein-unterschreiben-Modus (`view-slip`)
  und Schülerleihscheinmodus (`view-done`) zunächst über
  `flex:1 1 auto; justify-content:center` (analog `view-pending`) statt
  fester `margin-top`-Werte (90px/60px) vertikal zentriert, mit
  `overflow-y:auto` bei zu viel Inhalt.
- **Nachtrag 1 — Name bleibt oben fix:** `view-print`/`view-slip` bekommen
  `.scroll-center` als eigenen Wrapper um `.big-msg`, außerhalb der
  Namenszeile (`print-name-row`) — beim Scrollen bleibt der Schülername
  jetzt fix am oberen Rand stehen, nur der übrige Inhalt scrollt.
  Schülerleihschein-Dateiname korrigiert: kein Doppelpunkt mehr, nur
  Leerzeichen — `Schülerleihschein <Nachname>, <Vorname>.pdf`.
- **Nachtrag 2 — Zentrierung bezogen auf die GESAMTE Bildschirmhöhe:** die
  reine `justify-content:center`-Lösung zentrierte nur im Bereich UNTER der
  Namenszeile. Jetzt übernimmt `student.js::positionScrollCenter`
  (ResizeObserver-getrieben auf View + `.big-msg`) die Positionierung: der
  Inhalt zentriert sich bezogen auf die volle View-Höhe (ignoriert den
  Namen bei der Mittelpunkt-Berechnung), außer das würde mit der Namenszeile
  kollidieren — dann sitzt er direkt darunter. Reicht der Platz selbst dafür
  nicht, scrollt `.scroll-center` bis exakt zum unteren Rand (keine
  abgeschnittenen Icons).
- **Nachtrag 3 — Scroll-Bug in `view-done` behoben:** `view-done` hatte
  (anders als `view-print`/`view-slip`) noch die reine CSS-Lösung
  (`justify-content:center` + `overflow-y:auto` direkt auf der View). Bei
  überlaufendem Inhalt ist der zum Zentrieren "oben" abgeschnittene Teil
  darüber **unerreichbar**, weil `scrollTop` nicht negativ werden kann — das
  Häkchen-Icon und die Überschrift blieben dauerhaft unsichtbar. Auf
  dasselbe `.scroll-center` + `positionScrollCenter`-Muster umgestellt
  (Funktion generalisiert: `nameRow` ist optional, `view-done` hat keine
  Namenszeile). Alle drei Views jetzt per Playwright über einen lokalen
  HTTP-Server verifiziert (⚠️ `file://` lädt die `/`-absoluten Skriptpfade
  in `student.html` nicht — Tests dafür immer über `python3 -m http.server`
  im `web/`-Verzeichnis laufen lassen, sonst läuft `student.js` gar nicht
  und ein Layout-Test bestätigt nur Zufall).

## 2026-08-07 — Schülerleihscheinmodus: Eigenabruf des Leihscheins (letzte 3 Monate) beim Abschluss

- Der bisherige, generisch benannte „Leihscheinmodus" (Unterschriften-Screen
  nach dem Druck) heißt jetzt konsistent **Leihschein-unterschreiben-Modus**
  (Kommentare/Doku angepasst, `view-slip`; UI-Text „Leihschein
  unterschreiben" war unverändert schon vorher sichtbar).
- Neuer **Schülerleihscheinmodus**: der „Vorgang abgeschlossen"-Screen
  (`view-done`) des Schülerclients — egal ob er nach dem
  Leihschein-unterschreiben-Modus oder (ohne aktivierte Klassenoption) direkt
  nach „Leihschein erhalten" erreicht wird — zeigt jetzt einen Button
  „Meinen Leihschein herunterladen". Er lädt den eigenen Leihschein mit den
  Aktionen der letzten drei Monate herunter (IServ-Parameter
  `startReportingPeriod=3months`, bisher in `IsServClient.get_loan_slip_pdf`
  nicht durchgereicht — jetzt als optionaler `start_reporting_period`-Parameter
  ergänzt).
- Technisch löst der Button **keinen neuen Serverzugriff** aus: der Session-
  Token wird beim regulären Abschluss (`invalidate_session`) unmittelbar nach
  dem `closed`-Frame hart entwertet, ein Nachfordern über die WS wäre danach
  nicht mehr möglich. Der Server fetcht das PDF deshalb bereits **kurz VOR**
  `closed` (solange die Session/WS noch lebt) und pusht es einmalig
  base64-kodiert als `own_slip_download` (neue Helferfunktion
  `sessions._send_own_slip_download`, aufgerufen aus `invalidate_session` bei
  `new_state == "completed"`); der Client hält die Daten nur im Speicher und
  löst den Blob-Download beim Klick rein lokal aus. Der IServ-Abruf ist
  Best-effort (try/except) — ein Fehler (kein IServ-Client, Netzproblem) darf
  den eigentlichen Sessionabschluss nie verzögern oder verhindern; der Button
  bleibt dann einfach verborgen (`#own-slip-unavailable`-Hinweis stattdessen).
- Der Blob-Download (base64 → `Blob` → `<a download>`) war bisher nur in
  `host-ws.js` (Leihschein-PDF „lokal speichern") implementiert — als
  `downloadBase64Pdf` nach `common.js` verschoben und in `host-ws.js` sowie
  `student.js` gemeinsam genutzt.
- Tests: `tests/test_print_queue.py::test_confirm_slip_received_pushes_own_slip_before_closed_when_no_signature`
  + `::test_confirm_slip_received_completion_survives_own_slip_fetch_failure`.

## 2026-08-07 — „Leihschein erhalten"-Bestätigung im Druckmodus (Schülerclient)

- Der Druckmodus (`view-print`) des Schülerclients sprang nach erfolgreichem
  Druck sofort automatisch weiter (Leihscheinmodus bzw. Abschluss). Neu: nach
  „Leihschein gedruckt." bleibt die Ansicht stehen und zeigt darunter den
  Button „Leihschein erhalten"; optisch identisch zum „Leihschein
  drucken"-Button (gleiche Breite/Abstand), nur mit anderem Label.
- Der eigentliche Wechsel in den Leihscheinmodus bzw. der Auto-Abschluss
  passiert jetzt **serverseitig erst nach der Bestätigung**, nicht mehr
  automatisch mit dem physischen Druckende: `sessions._mark_slip_printed`
  markiert nur noch `student.slip_printed` (Host-Badge); der neue
  `sessions.confirm_slip_received()` — ausgelöst durch den neuen WS-Handler
  `slip_received` in `server/routes/ws.py` (Klick auf „Leihschein
  erhalten") — setzt danach `session.loan_slip_mode`/schickt `slip_mode` bzw.
  ruft `end_student` und schickt `closed`. Neues Feld
  `StudentSessionB.slip_receipt_confirmed` macht das idempotent.
  Grund: ohne diese Verschiebung ließ ein Reload des Schülerclients zwischen
  Druckende und Bestätigung den Vorgang so aussehen, als wäre der Button
  bereits gedrückt worden (Server hatte den nächsten Schritt schon
  unwiderruflich ausgelöst).
- Reload-sicher: `worker_ready` trägt jetzt zusätzlich `slip_printed` — ist
  gedruckt, aber noch nicht bestätigt, zeigt der Client beim (Re-)Connect
  direkt wieder den Druckmodus mit sichtbarem Button (`web/student.js`:
  `enterDruckmodusAwaitingReceipt()`), statt erneut zu drucken oder den
  nächsten Schritt zu überspringen. Damit dabei auch die normale „Leihschein
  von X gedruckt."-Meldung (statt nur generisch „Leihschein gedruckt.")
  erhalten bleibt, merkt sich `QueueStudent` jetzt zusätzlich
  `slip_printer`/`slip_printer_label` (gesetzt von
  `PrintQueue._mark_slip_printed_after_completion` beim Druckende, per
  `worker_ready` an den Client gereicht) — das transiente `print_result`-Frame
  allein hätte einen Reload nicht überlebt.
- Der Button sendet zusätzlich `{type: "slip_received"}` über die Schüler-WS.
  Derselbe Handler ruft auch `PrintQueue.clear_last_printed_for_student()`
  auf — löscht den „Gedruckt"-Marker im Drucker-Display für diesen Schüler,
  falls er dort noch angezeigt wird, und broadcastet die Displays neu. Damit
  gibt es jetzt drei gleichberechtigte Wege, wie ein Name im Drucker-Display
  aus der „Gedruckt"-Anzeige verschwindet: nächster fertiger Auftrag am
  selben Drucker (überschreibt), 30s-TTL (`_PRINTED_TTL_S`, rein
  client-seitig im Drucker-Display), oder „Leihschein erhalten" im
  Livemodus. `_last_printed` trägt dafür neu die `student_id` im Tupel
  (fünftes Feld).

## 2026-08-07 — „Leihschein unterschreiben"-Button im Helferclient

- Bei aktivem „Leihschein unterschreiben" (`ClassContext.done_signed`, Live-
  Klasse) blieb ein Modus-B-Schüler nach dem Druck im Unterschriften-Modus
  offen — der Helferclient (`scan.html`) hatte bisher keinen eigenen Weg, ihn
  nach der physischen Übergabe abzuschließen (nur der Host über
  „Abschließen").
- Neu: sobald der Schülerclient nach dem Druck in den Unterschriften-Modus
  wechselt (`QueueStudent.slip_signing`, gesetzt in `_mark_slip_printed`),
  zeigt die Aktiv-Gruppe der Klassenliste vor dem „Aufrufen"-Pfeil einen
  Stift-Button (geometrischer Stiftkörper über einer geschwungenen Linie als
  Unterschrifts-Andeutung) — an derselben Stelle wie der Betreuerauslöser-
  Druckbutton, mit dem er sich die dritte Grid-Spalte teilt (beide schließen
  sich gegenseitig aus). Klick sendet direkt, ohne Dialog; der Button
  verschwindet danach fleet-weit, der Schüler gilt als fertig.
- Neuer WS-Handler `finish_signed` (`server/routes/ws.py`) validiert
  serverseitig `done_signed` und `slip_signing`, bevor er denselben
  `end_student`-Abschluss wie `/api/finish` auslöst, und broadcastet per
  `hub.broadcast_queue_size` an alle Helfer. `_mark_slip_printed`
  (`server/sessions.py`) setzt `slip_signing` und broadcastet ebenso, sobald
  Helfer verbunden sind.
- Volle Testsuite und `ruff check` grün.
- Nachbesserung (Nutzer-Feedback): das Signatur-Icon war eine zu tiefe, zu
  regelmäßige Sinuswelle (tiefster Punkt fast am Rand der 24×24-viewBox) —
  ersetzt durch eine unregelmäßige, flachere Schwunglinie mit aufsteigendem
  Abschluss-Strich, die eher nach Unterschrift als nach Welle aussieht.

## 2026-08-07 — Betreuerauslöser-Druckbutton im Helferclient

- Bei `slip_trigger == "helper"` konnte bisher nur der Host oder der Helfer
  für seinen eigenen zugewiesenen Schüler drucken — kein direkter Weg, einen
  beliebigen anderen aktiven Schüler aus der Klassenliste des Helferclients
  (`scan.html`) heraus anzudrucken.
- Neu: sobald ein Schüler bei „Betreuerauslöser" in den Druckmodus wechselt
  (`QueueStudent.print_mode`, gesetzt via WS `print_mode`), zeigt die
  Aktiv-Gruppe der Klassenliste vor dem „Aufrufen"-Pfeil einen Druckbutton.
  Klick öffnet denselben Druck-Dialog wie der eigene Druckbutton
  (`printTargetStudentId`), mit der Drucker-Vorauswahl der Klasse des
  Zielschülers (`real_contexts_summary().print_default_ids`, nicht die der
  eigenen Klasse des Helfers).
- Neuer WS-Handler `print_for_student` (`server/routes/ws.py`) validiert
  serverseitig `slip_trigger`, `print_mode` und „kein laufender Auftrag"
  (Check-then-Enqueue ohne `await` dazwischen ist gegen gleichzeitige Klicks
  zweier Helfer atomar). Der `PrintJob` läuft mit `role="student"` ohne
  `helper_token` — das Drucker-Display zeigt dadurch keinen Helfernamen,
  genau wie bei einem Auftrag, den der Schüler selbst gesendet hätte.
  Rückmeldung über einen eigenen Nachrichtentyp `print_for_student_result`,
  damit ein fremder Druckauftrag nicht die eigene Druck-Statuszeile des
  Helfers überschreibt.
- `PrintQueue._notify_all()` broadcastet Druck-Übergänge jetzt zusätzlich an
  alle Helfer-Sessions (`hub.broadcast_queue_size`) — Grundlage dafür, dass
  Button und offenes Druckmenü fleet-weit verschwinden/sich schließen,
  sobald irgendein Helfer den Auftrag gesendet hat.
- Volle Testsuite und `ruff check` grün; `test_state_contract.py` (eingefroren
  Top-Level-Schlüssel von `state_snapshot()`) unverändert grün — die neuen
  Felder betreffen nur `queue`-/Kontext-Einträge. Details:
  `_logs/2026-08-07_sba_betreuerausloeser_druckbutton.md` (Wiki).

## 2026-08-06 — Host-Fortschrittsbaseline übersteht Seiten-Reload

- Die „seit Aufrufen"-Baseline (`loaned_at_load`, Grundlage für X/Y in der
  Host-Queue-Statusspalte) wurde bislang bei jedem Reload der Helfer-/
  Schülerseite (WS-Reconnect) neu auf den aktuellen Ausleihstand gesetzt —
  ein zwischenzeitlich ausgeliehenes Buch verschwand dadurch unbemerkt aus
  der session-bezogenen Y-Zahl.
- `init_book_progress`/`hydrate_student_info` bekommen einen neuen Parameter
  `reset_baseline` (Default `True`). Nur der echte „Aufrufen"-Pfad
  (`assign_student_to_helper` → `load_and_push_helper_student`/
  `load_and_push_paired_student`) setzt die Baseline neu; Reconnects
  (`ws_scanner`, `ws_student`) und reine Booklist-Refreshes
  (`repush_booklist`) übergeben `reset_baseline=False` — `done_isbns` wird
  weiterhin aus IServ aufgefrischt, nur `loaned_at_load` bleibt stehen, bis
  der Schüler erneut aufgerufen wird (neuer Helfer/neue Zuweisung).
- Neuer Test `test_reconnect_preserves_baseline_but_refreshes_done` in
  `tests/test_queue_progress.py`. Volle Testsuite und `ruff check` grün.

## 2026-08-06 — Direkter Druckmodus bei bereits vollständig ausgeliehenen Büchern

- Beim Pairing (Code wird Schüler zugeordnet) prüft der Server jetzt vorab
  anhand der bereits geladenen Bücherliste (`hydrate_student_info`, IServ-
  Read-API — kein Playwright), ob alle Bücher des Schülers schon ausgeliehen
  sind (`all_books_already_loaned` in `server/sessions.py`).
- Trifft das zu, wird **kein** Playwright-Worker mehr geöffnet — `worker_ready`
  geht sofort mit der vollständigen Bücherliste an den Schülerclient, der wie
  gehabt in den Druckmodus wechselt und `print_mode` zurückmeldet.
- Vorher wartete der Client in diesem Fall auf `state.worker_pool.open_student`
  (mehrsekündige Browser-Navigation) und blockierte, wenn gerade kein Worker
  frei war — obwohl der Worker sofort danach ungenutzt wieder freigegeben
  wurde (`release_student_worker` bei `print_mode`).
- `release_student_worker` ist bereits idempotent (No-op ohne registrierten
  Worker), der Reconnect-Pfad (`routes/ws.py`) profitiert über denselben
  `load_task` automatisch mit. Keine IServ-Schreibzugriffe; volle Testsuite
  und `ruff check` grün.

## 2026-08-06 — Schülerclient-Leihscheinmodus nach dem Druck

- Ist in der Klasse „Leihschein unterschreiben" aktiviert, bleibt der Modus-B-
  Schüler nach abgeschlossenem Leihschein-Druck offen und wechselt in einen
  eigenen Leihscheinmodus.
- Der Schüler wird aufgefordert, den Leihschein zu unterschreiben und ihn bei
  einem Betreuer abzugeben. Ist zusätzlich „Leihschein wird vom Lehrer
  eingesammelt" aktiv, lautet das Ziel „beim Lehrer".
- Der Modus bleibt bei einem Reconnect erhalten. Der Host schließt den Schüler
  nach der physischen Übergabe wie bisher über „Abschließen"; ohne aktivierte
  Unterschrift bleibt der automatische Abschluss nach dem Druck unverändert.
- Tests decken beide Übergabeziele, den offenen Session-Zustand und den
  Reconnect-Payload ab; keine IServ-Schreibzugriffe.

## 2026-08-06 — Schülerclient-Druckmodus: Überschrift und Status getrennt

- Der Druckmodus zeigt dauerhaft „Leihschein Drucken" als Überschrift.
- Die Erklärung für den nächsten Schritt steht getrennt über dem dynamischen
  Druckstatus; der Status bleibt an seiner bisherigen Stelle.
- Die blaue „Status"-Beschriftung und blaue Hervorhebung wurden entfernt. Der
  Status bleibt als neutral abgegrenzter Bereich sichtbar; der optionale
  Druckbutton folgt darunter.
- Verifiziert mit `node --check web/student.js` und `git diff --check`; keine
  IServ-Schreibzugriffe.

## 2026-08-06 — Druckhinweise beginnen großgeschrieben

- Serverseitig gelieferte Drucker-Warnmeldungen werden vor der Anzeige am
  Satzanfang großgeschrieben; das gilt auch für weitere Sätze innerhalb der
  Meldung, etwa „Bitte überprüfe dies".

## 2026-08-06 — Druckfehler verweist auf den Betreuer

- Bei einem fehlgeschlagenen Druck zeigt der Schülerclient immer den Hinweis,
  sich bei einem Betreuer zu melden.
- Der Druckbutton bleibt nach dem Absenden verborgen.

## 2026-08-06 — Druckbutton nach Selbstauslöser ausblenden

- Beim Absenden des Druckauftrags wird der Button „Leihschein drucken" im
  Schülerclient sofort ausgeblendet.
- Bei einem Druckfehler bleibt der Button verborgen; der Schüler soll sich bei
  einem Betreuer melden.

## 2026-08-06 — Schülerclient gibt Worker beim Druckmodus frei

- Beim Eintritt in den Druckmodus signalisiert der Schülerclient dem Server,
  dass seine Playwright-Kartei nicht mehr benötigt wird.
- Der Worker wird beendet und bleibt nicht bis zum Abschluss des Druckauftrags
  gebunden; die Modus-B-Session bleibt für Druckstatus und Abschluss bestehen.
- Das Druck-Request enthält zusätzlich ein idempotentes Fallback für verlorene
  Druckmodus-Signale nach einem Reconnect.

## 2026-08-06 — Schülerclient blendet Systemnamen der Drucker aus

- Druckfortschritts- und Ergebnistexte am Schülerclient zeigen nur einen
  konfigurierten Anzeigenamen.
- Technische Systemnamen werden auch aus Stall-/Fehlermeldungen entfernt; bei
  fehlendem Anzeigenamen bleibt der Druckername vollständig verborgen.

## 2026-08-06 — Schülerclient erst nach abgeschlossenem Druck fertig

- `print_loan_slip_for()` markiert einen Leihschein nicht mehr beim bloßen
  Absenden an das Druck-Backend als gedruckt.
- Die PrintQueue setzt `slip_printed` und beendet die Modus-B-Session erst,
  wenn der OS-Druckauftrag abgeschlossen (`absent`) ist. Der Schülerclient
  erhält davor weiterhin den Druckstatus und bleibt im Druckmodus.
- Verifiziert mit 391 Offline-Tests, Ruff und `node --check web/*.js`; kein
  IServ-Schreibzugriff.

## 2026-08-06 — Schülerclient zeigt den Druckstatus

- Der Druckmodus des Schülerclients übernimmt die Statusmeldungen des
  Helferclients: Warten in der Queue, Druckbeginn, Druckerposition,
  erfolgreicher Druck sowie Fehler-/Stallmeldungen.
- Für Schüleraufträge berechnet der Server die Position in der zentralen
  Warteschlange relativ zu den zuvor von Schülerclients gesendeten Aufträgen;
  Host-/Helferaufträge vor ihnen werden in diesem Queue-Anteil nicht gezählt.
- Die physische Reihenfolge bereits an einen Drucker gesendeter Aufträge bleibt
  unverändert verbindlich. Verifiziert mit 389 Offline-Tests, Ruff und
  JavaScript-Syntaxchecks; kein IServ-Schreibzugriff.

## 2026-08-06 — Schülerclient zeigt Name und Klasse im Druckmodus

- Name und Klasse bleiben im Modus B auch bei einer Druckfehlermeldung sichtbar.
  So kann ein Betreuer den betroffenen Schüler direkt zuordnen und den Druck
  gegebenenfalls übernehmen.

## 2026-08-06 — Schülerclient: Betreuerauslöser serverseitig abgesichert

- Der Schülerclient verarbeitet beim Eintritt in den Druckmodus die Einstellung
  der zugehörigen Klasse: „Automatisch" druckt sofort, „Schülerauslöser" zeigt
  den Druckbutton, „Betreuerauslöser" verweist an den Betreuer.
- „Barcode" bleibt als auswählbare Einstellung vorbereitet, hat aber weiterhin
  keine Funktion.
- Der Schüler-WebSocket weist print_request im Betreuerauslöser-Modus ab; der
  Betreuer-/Host-Druckweg bleibt dafür zuständig. Der vorbereitete Barcode-
  Modus wird serverseitig ebenfalls noch nicht als Druckauslöser akzeptiert.

## 2026-08-05 — Schüler- und Helfer-Scanner verarbeiten nur einen Scan gleichzeitig

- Schüler- und Helferclient sperren nach dem Annehmen eines Barcodes weitere
  Scans, bis das zugehörige `scan_result` eingetroffen ist.
- Bei ausgemusterten oder anderweitig verliehenen Büchern bleibt die Sperre bis
  zum bewussten Aufräumen der blockierenden Meldung bestehen; unbekannte Codes
  und andere selbst schließbare Hinweise geben den Scanner direkt nach der
  Antwort frei.
- Verifiziert mit 385 pytest-Tests, Ruff und `node --check web/*.js`; keine
  IServ-Schreibzugriffe.

## 2026-08-05 — Teacher-Kachel „Leihschein abgegeben" verbreitert

- Der optionale Zähler heißt jetzt vollständig „Leihschein abgegeben" statt
  nur „abgegeben".
- Die Kachel ist bei aktivierter Leihschein-Sammlung auf Desktop-Viewports
  doppelt so breit wie die vier Statuskacheln; mobil spannt sie beide Spalten
  der Zählerzeile.
- Verifiziert mit 385 pytest-Tests, Ruff, `node --check web/*.js` und einem
  lokalen Chromium-Check bei 390px/1024px; keine IServ-Schreibzugriffe.

## 2026-08-05 — Teacher-Statuszähler mobil als Raster

- Die Statuszähler „abgeschlossen", „aktiv", „offen" und „übersprungen" stehen
  auf mobilen Viewports jetzt in einem 2×2-Raster statt in einer überfüllten
  Reihe. Bei aktivierter Leihschein-Sammlung wird der fünfte Zähler ebenfalls
  responsiv einsortiert.
- Die Zählerlabels bleiben mit `white-space: nowrap` vollständig lesbar;
  Desktop-Viewports verwenden weiterhin vier bzw. fünf Spalten.
- Verifiziert mit 385 pytest-Tests, Ruff, `node --check web/*.js` und einem
  lokalen Chromium-Check bei 390px/1024px; keine IServ-Schreibzugriffe.

## 2026-08-05 — Teacher-Statuszeilen zweizeilig und wortfest

- Jede Schülerzeile der Teacher-Ansicht ist jetzt in eine Kopfzeile mit Name,
  Statuspunkt und rechtem Wisch-Chevron sowie eine eigene Status-/Aktionszeile
  geteilt. Dadurch bleiben Statuslabels wie „Ausgabe abgeschlossen" und
  „Übersprungen / abwesend" als vollständige Wörter erhalten.
- Status- und Aktionsbereich reagieren weiterhin auf schmale Viewports; die
  Rücknahmeaktion bleibt erreichbar, während die bestehende Links-Wisch-Geste
  und die rechte Chevron-Position unverändert bleiben (`web/teacher.html`,
  `web/teacher.js`).
- Verifiziert mit 385 pytest-Tests, Ruff, `node --check web/*.js` und einem
  lokalen Chromium-Check bei 390px/1024px inklusive Links-Wisch; keine
  IServ-Schreibzugriffe.

## 2026-08-05 — Teacher-Wischhinweis rechts ausgerichtet

- Der dezente linke Chevron-Hinweis in wartenden Schülerzeilen steht jetzt am
  rechten Rand und zeigt damit konsistent zur Wischrichtung nach links.
- Verifiziert mit `node --check` und einem gerenderten Chromium-Check bei
  390px Viewportbreite.

## 2026-08-05 — Teacher-Status und Modus-B-Pairing korrigiert

- Der Verbindungsindikator der Teacher-Ansicht scrollt jetzt mit dem Dokument
  statt dauerhaft am Viewport zu kleben.
- Beim Klassen-Laden automatisch übersprungene Schüler (z. B. „Nicht
  angemeldet") bleiben in der Host-Queue als `done` mit dem bestehenden
  Hinweis sichtbar, erscheinen in der Teacher-Ansicht aber korrekt als
  „Übersprungen / abwesend" und werden dort nicht als abgeschlossen gezählt.
- Das Modus-B-Pairing richtet Code, Schülerauswahl und „Zuordnen"-Button
  sauber aus; der globale Select-Abstand wird für diese Zeile aufgehoben.
- Verifiziert mit 385 pytest-Tests, Ruff, JavaScript-Syntaxchecks und einem
  lokalen Playwright-Check ohne IServ-Writes.

## 2026-08-05 — UI-Visual-Audit: vier Inkonsistenzen behoben

- Die Klassen-Reiter in der Host-Tab-Leiste nehmen jetzt wie der Host-Reiter
  an der vollen Höhe teil; Status-Badges für Warnhinweise verwenden dasselbe
  Schriftgewicht wie die übrigen Badge-Typen (`web/host.css`).
- Die Teacher-Statistik-Kacheln bleiben auf schmalen Viewports in einer
  gleichmäßig breiten Zeile, auch wenn der optionale fünfte Zähler erscheint;
  lange Bezeichnungen dürfen innerhalb der Kachel umbrechen (`web/teacher.html`).
- Der Verbindungsindikator des Drucker-Displays steht jetzt wie bei QR- und
  Teacher-Display oben rechts (`web/drucker-display.html`).
- Read-only-Playwright-Visualprüfung gegen den lokalen Server mit Login und
  `POST /api/open-test-config`: Tab- und Badge-Messwerte, Kachel-Geometrie
  sowie die `#conn`-Positionen bestätigt; keine IServ-Writes.

## 2026-08-05 — Lehreransicht: rotes Wisch-Hintergrund-Bleeding behoben

- Die Schülerzeilen behalten wieder ihre normalen runden Ecken; zusätzlich ist
  die rote Reveal-Fläche minimal eingerückt und separat abgerundet. Dadurch
  scheint sie in Ruhe nicht mehr an den Zeilenecken durch (`web/teacher.html`).
- Verifiziert mit einem gerenderten Chromium-Eckentest, `git diff --check` und
  `node --check web/teacher.js`.

## 2026-08-05 — Teacher-QR-Modal schließt nach Scan automatisch

- Der Host registriert beim Anzeigen des Lehrer-QR-Codes jetzt einen
  `qrWatch` für den jeweiligen Klassen-Kontext. Sobald die Lehrkraft den QR
  scannt und ihre WebSocket-Verbindung sichtbar verbunden ist, schließt sich
  das gemeinsame QR-Modal automatisch — analog zu Helfer-, Schüler- und
  Display-QRs. Ein zusätzlicher Direktcheck deckt den engen Broadcast-/Modal-
  Race ab.
- Der Lehrer-WebSocket broadcastet den Host-Snapshot jetzt beim Verbinden und
  Trennen. Dadurch wird `teacher.connected` nach dem Scan tatsächlich an den
  Host übertragen; zuvor konnte der Frontend-Watcher keinen Zustandswechsel
  erkennen.
- Verifiziert mit `node --check web/host-render.js web/host-ws.js`; der echte
  Scan im Schul-WLAN bleibt Bestandteil des offenen Live-Checks.

## 2026-08-05 — Klassen-Lehreransicht: Wischrichtung und Leihschein-Eingang

- **Abwesenheits-Geste gespiegelt:** Wartende Schüler werden jetzt per Wisch
  nach **links** als abwesend markiert. Rote Reveal-Fläche, Chevrons,
  Hinweistext und Rücksprung-Animation zeigen dieselbe Richtung.
- **Leihschein-Eingang an Klasseneinstellung gekoppelt:**
  `done_collected` wird im minimierten `teacher_snapshot` mitgeliefert. Die
  Lehreransicht blendet Counter und „Leihschein entgegengenommen"-Checkboxen
  nur bei aktivierter Klassenoption und gedrucktem Schein ein; der Endpunkt
  prüft zusätzlich Klassenoption, Abschlussstatus und `slip_printed`.
- **Counter-Synchronisierung korrigiert/abgesichert:** Der Zähler zählt nur
  abgeschlossene Schüler bei aktivierter Option. Host-Snapshot und Teacher-WS
  erhalten das Flag weiterhin über denselben `Hub.broadcast_host`-Pfad; dafür
  gibt es jetzt Regressionstests für beide Ausgabeseiten und für deaktivierte
  Optionen.
- **Tests:** **383 Tests grün**, Ruff clean, `node --check` für die Web-JS
  erfolgreich. Live-Check am echten Touchscreen/Lehrkraft-Handy bleibt offen.

## 2026-08-04 — Schülerclient: Druckmodus nach Laden und Buchlisten-Änderung

- **Druckmodus-Prüfung korrigiert:** bereits beim Laden ausgeliehene Bücher
  werden nun als erledigt berücksichtigt. Die Prüfung läuft damit nicht nur
  nach einem erfolgreichen Scan, sondern greift zuverlässig bei
  `worker_ready` sowie bei `booklist_update` nach Ausblenden oder Einblenden
  durch den Host.
- Wenn das Ausblenden die letzte sichtbare Reihe entfernt, merkt sich der
  Client, dass zuvor Bücher geladen waren, und schaltet ebenfalls in den
  Druckmodus. Eine von Anfang an leere Bücherliste löst weiterhin keinen
  Druckmodus aus.

## 2026-08-04 — Klasseneinstellungen: Drucker-Default-Fix, Fertig-Optionen persistiert, iPad-Freischalt ohne Tippen

Drei Nachbesserungen aus der frisch gebauten Klassen-Feature-Session:

- **Bugfix (Drucker-Default nach Neustart):** `printer_store.py` vergibt
  Drucker-`id`s bewusst nur laufzeitstabil neu (dokumentiert, kein Fehler),
  aber die `classPrinters`-Auswahl im panel-new war client-seitig per `id` in
  `localStorage` gespeichert — nach jedem Server-Neustart liefen die
  gespeicherten IDs ins Leere, `saved.has(p.id)` matchte nichts mehr, keine
  Checkbox blieb angehakt. Fix: Persistenz jetzt über den stabilen
  `printer.name` (`printerStableKey()`, neu in `host-state.js`) statt der
  `id`; `_printerCheckboxHTML` trägt zusätzlich `data-pname`. IDs bleiben
  weiterhin das, was `/api/open-class` bekommt. Verifiziert per Node/vm-
  Testharness (Restart mit neuen IDs, gleichen Namen → weiterhin alle
  angehakt).
- **„Leihschein unterschreiben"/„…wird vom Lehrer eingesammelt" persistiert:**
  bisher reine UI ohne Serverkontakt. Neue `ClassContext.done_signed`/
  `done_collected` (In-Memory, Muster wie `live_ausgabe`/`slip_trigger`), in
  `state_snapshot()`, gesetzt über `/api/open-class` (`done_signed`/
  `done_collected`, beide Zweige: Neu-Öffnen + Reuse) sowie neuer Endpunkt
  `POST /api/context-done-options` fürs nachträgliche Setzen im Klassen-Tab.
  `done_collected` wird serverseitig auf `False` normalisiert, sobald
  `done_signed=False` gesetzt wird (`_resolve_done_collected`) — kein
  inkonsistenter „eingesammelt ohne unterschrieben"-Zustand. panel-new
  bekommt eigene localStorage-Persistenz (`loadClassDoneSigned/-Collected`),
  vorbelegt bei jedem Render, gespeichert bei Änderung. Weiterhin reine
  Einstellung ohne Auswirkung auf den Fertig-Übergang selbst (folgt später).
- **iPad-Freischalten ohne Tippen:** Recherche zeigte, dass drei von vier
  Pairing-Flows (Drucker-Display, Lehrkraft, Modus-B-Schüler) bereits
  klickbasiert sind — nur das iPad-Display (`/qr-display`) verlangte noch
  den vom iPad abgelesenen Code manuell im Host-Textfeld. Historisch, weil
  dieser Flow (Phase 4, 2026-06-15) nie das UX-Upgrade bekam, das
  Drucker-Display später erhielt. Jetzt angeglichen: `modus_b_snapshot()`
  liefert `registration_code` je Display, der Host zeigt eine Liste aller
  verbundenen-aber-unautorisierten iPads mit Klick-Button „Freischalten"
  (`#mb-display-pending`, ersetzt `#mb-display-code`-Textfeld); `POST
  /api/display/authorize` nimmt jetzt `display_id` statt
  `registration_code` (wie `printer_display_enable`). Der Code dient nur
  noch dem visuellen Abgleich mit dem iPad-Bildschirm, ist kein Credential
  mehr. Tastatur-Shortcut „c" (fokussierte das alte Textfeld) entfernt.
- Tests: `tests/test_api_guards.py` (+4: `test_open_class_seeds_done_options`,
  `test_context_done_options_collected_requires_signed`,
  `test_display_authorize_by_id_not_code` deckt auch den 400-/404-Pfad ab) —
  **381 Tests grün**, Ruff clean, `node --check` auf allen `web/*.js` OK.
  **Live-Check (Drucker-Persistenz über echten Neustart, iPad-Freischalt-Klick
  am echten Gerät) noch offen.**

## 2026-08-04 — Lehreransicht: QR-Bugfix, Button-Größe, Wisch-Schwelle + Hinweis

Drei weitere Nachbesserungen im direkten Anschluss:

- **Bugfix (fehlender Broadcast):** `GET /api/teacher/qr` mintete zwar die
  Session serverseitig, pushte aber nie einen frischen Host-Snapshot — die
  Code-/Bestätigen-Anzeige im Klassen-Tab blieb deshalb auf altem Stand,
  bis zufällig eine ANDERE Aktion einen Snapshot auslöste (z. B. Tab-Wechsel,
  der `POST /api/set-active-context` feuert). Fix: `teacher_qr` broadcastet
  jetzt selbst nach dem Minten (`server/routes/teacher.py`), Regression in
  `tests/test_teacher.py::test_qr_mints_session_with_token_in_url` mitgeprüft.
- **Button-Größe:** „Abbrechen" neben „Bestätigen" im Klassen-Tab nutzte
  `ghost warn` (kleinere Schrift/Padding als der Default-Button) statt
  `secondary` (gleiche Größe wie `success`) — jetzt einheitlich groß
  (`web/host-render.js`).
- **Wisch-Schwelle + Auffindbarkeit:** die Auslöseschwelle der „Als
  abwesend"-Wisch-Geste ist jetzt relativ zur tatsächlichen Zeilenbreite
  (55 % zum Auslösen, bis 90 % ausziehbar) statt fixer 90px — die rote
  „Als abwesend markieren"-Fläche ist beim Ziehen vollständig lesbar, auch
  auf schmalen Geräten. Zusätzlich zwei dezente Wisch-Chevrons (›​›) am
  rechten Rand jeder wartenden Zeile plus ein Hinweistext oberhalb der Liste
  („Wartende Schüler nach rechts wischen, um sie als abwesend zu
  markieren.", nur sichtbar solange es wartende Schüler gibt) — die Geste
  war vorher nur durch Zufall auffindbar (`web/teacher.html`/`teacher.js`).
- **378 Tests grün** (Regression-Assertion in bestehendem Testfall ergänzt,
  keine neue Testfunktion), Ruff clean, `node --check web/teacher.js
  web/host-render.js` OK.

## 2026-08-04 — Lehreransicht: Wisch-Geste, Leihschein-Eingang, Wording-Fix

Drei Nachbesserungen an der frisch implementierten `/teacher`-Ansicht:

- **Wisch statt Button:** „Als abwesend" ist jetzt eine Wisch-Geste nach
  rechts auf der Schüler-Zeile (`web/teacher.js`, Pointer Events — Touch UND
  Maus) statt eines Buttons. Die Ziehweite (`SWIPE_THRESHOLD_PX = 90`) dient
  zugleich als Bestätigung — kein Modal mehr für diese Aktion (`Wieder
  wartend" behält das Bestätigungs-Modal, da Rücknahme weiterhin ein Button
  ist). `POST /api/teacher/skip` unverändert.
- **„Leihschein entgegengenommen"-Checkbox je Schüler:** neues Flag
  `QueueStudent.slip_collected` (`server/state.py`, orthogonal zu `status`
  wie `slip_printed`; zurückgesetzt in `reset_progress()`). Neuer Endpunkt
  `POST /api/teacher/slip-collected` (token-authentifiziert, klassenscharf,
  verlangt `slip_printed=True` — ohne gedruckten Schein nichts entgegen-
  genommen). `AppState.teacher_snapshot()` liefert das Flag je Schüler plus
  `slip_collected_count` als Summe; die Lehrer-Ansicht zeigt es als fünfte
  Kachel „abgegeben" neben den vier Status-Kacheln. **Host sieht die
  Statistik mit** (read-only, Host setzt das Flag nicht selbst): eine
  Zeile „Leihschein entgegengenommen: X / Y abgeschlossen" in der
  „Lehrkraft-Ansicht"-Karte je Klassen-Tab (`teacherSlipStat`,
  `web/host-render.js`) sowie ein grünes „Leihschein ✓"-Badge in der
  Queue-Tabelle bei abgeschlossenen Schülern.
- **Wording-Fix:** der Hinweistext im Klassen-Tab behauptete „ohne
  Steuerung" — nicht mehr korrekt, seit die Lehrkraft Anwesenheit und
  Leihschein-Eingang selbst steuert. Neu: „mit eingeschränkter Steuerung
  (nur „Als abwesend"-Markierung und Leihschein-Eingang)".
- Tests: `tests/test_teacher.py` (+5: slip-collected-Endpunkt Auth/Gate/
  Isolation/Toggle, `teacher_snapshot`-Zähler), `tests/test_queue_progress.py`
  (`slip_collected` in `reset_progress()`-Assertion ergänzt) — **378 Tests
  grün**, Ruff clean, `node --check web/teacher.js web/host-render.js` OK.
  **Live-Check (Wisch-Geste auf echtem Touchscreen, Checkbox-Sync
  Lehrkraft↔Host) noch offen.**

## 2026-08-04 — Lehreransicht für Modus B implementiert (`/teacher`)

- Umsetzung des am selben Tag beschlossenen Plans (`docs/teacher_status_page_plan.md`).
- **State/Lifecycle:** neue `TeacherSession` (`server/state.py`) — Token
  (lang, zufällig, der eigentliche Zugangs-Credential), `context_id`
  (unveränderlich an eine Klasse gebunden), `registration_code` (nur
  menschlich vermittelte Host-Zuordnung), `authorized`, `ws`. Neuer
  `AppState.teacher_snapshot(context_id)` liefert ausschließlich Klassenname,
  Summen je Status und je Schüler Name/Status/Buch-Fortschritt/Druckstatus —
  bewusst nicht `state_snapshot()`. Host-Snapshot bekommt pro Klassen-Tab eine
  `teacher`-Kachel (Registrierungscode/autorisiert/verbunden), **ohne** den
  Token zu leaken.
- **API** (`server/routes/teacher.py`): `GET /api/teacher/qr?context_id=`
  mintet/ersetzt eine noch unautorisierte Session (blockt bei bereits
  autorisierter mit 409 — der Host muss zuerst trennen); `POST
  /api/teacher/authorize` bestätigt den Registrierungscode im Klassen-Tab;
  `POST /api/teacher/disconnect` trennt explizit; die token-authentifizierten
  `POST /api/teacher/skip` / `/undo-skip` erlauben ausschließlich
  `pending -> skipped` bzw. die Rücknahme, strikt auf die eigene Klasse
  beschränkt (Schüler einer anderen Klasse → 404, nicht 403 — keine
  Existenzbestätigung über Klassengrenzen).
- **WebSocket** (`/ws/teacher?token=...`, `server/routes/ws.py`):
  unauthentifiziert im Cookie-Sinn (der lange Token ist der Credential, wie
  bei `/ws/student/{session_token}`); vor Autorisierung nur `registration`,
  danach `teacher_state`. Unbekannter/entwerteter Token → `forbidden` + Close
  4009, kein Session-Aufbau. `Hub.broadcast_host` pusht bei **jeder**
  Zustandsänderung zusätzlich an alle verbundenen Lehrer-Sessions
  (`sessions.broadcast_teacher_sessions`) — dieselbe zentrale Stelle, die
  auch die Helfer-Queue-Größe nachzieht.
- **Teardown:** `close_class` und `select_schoolyear` entwerten gebundene
  Lehrer-Sessions hart (`sessions.revoke_teacher_session(s)_for_context` /
  `revoke_all_teacher_sessions`) — Token wird aus dem State entfernt statt auf
  eine Bannliste gesetzt (lang & zufällig, nie wiederverwendet reicht ein
  „unbekannt" beim nächsten Connect).
- **Host-UI:** neue Karte „Lehrkraft-Ansicht" im jeweiligen Klassen-Tab
  (`web/host-render.js`) — QR-Button, Registrierungscode + Bestätigen/
  Abbrechen vor Autorisierung, Verbindungsstatus + sichtbares „Lehrkraft
  trennen" danach (verhindert versehentliches Kappen einer laufenden Ansicht).
- **Teacher-UI:** `web/teacher.html`/`teacher.js` + Clean-Route `/teacher`
  (`server/app.py::_CLEAN_PAGES`) — Registrierungscode-Ansicht, Klassenname +
  Summen `abgeschlossen/aktiv/offen/übersprungen`, Statusliste mit
  Skip-/Undo-Bestätigungsdialog; kein Login, keine Host-/Druck-Steuerung.
- **Tests:** `tests/test_teacher.py` (38 neue Tests) — QR-Minten/-Ersetzen/
  -Blockieren, Autorisieren, Trennen, `teacher_snapshot`-Privacy (keine
  fremde Klasse/Zahl-/Buchdaten), Klassen-Isolation bei skip/undo-skip,
  WS-Pairing/Reconnect/Entwertung, Teardown bei Klassen-Schließen +
  Schuljahreswechsel, direkte `Hub.broadcast_host`-Verdrahtung.

## 2026-08-04 — Lehreransicht für Modus B geplant

- Beschlossen: Eine Lehrkraft wird innerhalb eines konkreten Klassen-Tabs per
  QR-Token und nachgeschaltetem Host-Registrierungscode gepaart. Sie sieht live
  ausschließlich diese Klasse und kann nur wartende Schüler als abwesend
  überspringen bzw. zurück auf wartend setzen.
- Datenschutz-/Rechte-Grenze: eigener minimierter Teacher-Snapshot statt
  Host-Snapshot; keine anderen Klassen, Zahlungs-/Buchdaten, QR-Secrets oder
  Host-Steuerung. „Ausgabe abgeschlossen" bedeutet alle Bücher gescannt plus
  Leihschein gedruckt, nicht eine zusätzliche IServ-Buchungsbehauptung.
- Detail-Zielbild und Abnahmekriterien: `docs/teacher_status_page_plan.md`.

## 2026-08-04 — Leihschein-Druckmodus am Schülerclient (slip_trigger)

- **Host-Dropdown „Leihschein Druck:"** (Automatisch / Schülerauslöser /
  Betreuerauslöser / Barcode; im vorigen Schritt als UI-Scaffold eingebaut)
  **an echtes Verhalten gebunden**. pro Klasse persistiert (In-Memory),
  per Snapshot an den Host und per `worker_ready` an den Schülerclient
  gepusht.
- **Druckmodus am Schülerclient (Modus B):** sobald alle vorgemerkten Bücher
  gescannt sind (clientseitig erkannt aus `currentBooks` + `scannedIsbns`;
  geprüft nach `worker_ready`, jedem erfolgreichen Scan **und** nach
  `booklist_update` — eine nachträglich ausgeblendete Reihe kann den
  Druckmodus erst auslösen), geht der Schüler in eine neue `#view-print`-
  Ansicht. Verhalten je `slip_trigger`:
  - **Automatisch** — Druckauftrag sofort gesendet.
  - **Schülerauslöser** — Druck per Button.
  - **Betreuerauslöser** — Hinweis „bei einem Betreuer melden"; Druck läuft
    über das bestehende Helfer-/Host-Druckmenü.
  - **Barcode** — Platzhalter (kein Verhalten, folgt später).
- **Druckauftrag role="student":** neuer WS-Message-Typ `print_request` im
  Schüler-WS; enqueued über die Druckerwarteschlange mit der Klassen-Druck-
  Allowlist (`allowed_printers_for`), `pages` am globalen
  `slip_second_page_default` orientiert. `PrintJob.student_token` ergänzt;
  `print_progress`/`print_result` werden ans Schüler-WS geroutet (Guard:
  Session kann nach Auto-Fertig weg sein).
- **Auto-Fertig nach Druck:** `_mark_slip_printed` ist die einzige Stelle, an
  der `slip_printed` true wird (für alle Druckpfade — Host-Endpoint,
  Helfer-WS, Queue-dispatched Student-Job). Dort wird für aktive Modus-B-
  Sessions (`find_session_by_student` + `state == "paired"`) automatisch
  `end_student(done/completed)` aufgerufen → `closed` an den Schüler →
  „Vorgang abgeschlossen". Modus A unberührt (keine Modus-B-Session).
- **Server:** `ClassContext.slip_trigger` (Default `"auto"`) +
  `state_snapshot`; `OpenClassRequest.slip_trigger` (Literal-validiert) +
  neuer Endpoint `POST /api/context-slip-trigger`; `slip_trigger_for()`
  Helfer; `worker_ready`-Payload in `load_and_push_paired_student` + WS-
  Reconnect um `slip_trigger` ergänzt.
- **Host-UI:** `load/saveClassSlipTrigger` (localStorage); Two-Way-Binding
  in `openClass`/`renderNewClassPrinters`/`renderCtxPrinters`;
  `setContextSlipTrigger` (Spiegel von `setContextLiveAusgabe`) +
  delegierter Change-Handler.
- **Tests:** `tests/test_loan_slip.py` FakeState um `find_session_by_student`
  ergänzt (Auto-Fertig-Hook). Suite grün (336 passed).

## 2026-08-04 — Host-Status-Spalte: X/Y / Leihschein / Aktiv

- **Status-Spalte aktiver Schüler** zeigt statt starr „Aktiv" den Fortschritt:
  - **X/Y** (ausgegebene/angemeldete Bücher) — wie bisher das Info-Badge, nun in
    der Status-Spalte. **Zweizeilig**, wenn beim Aufrufen schon Bücher ausgeliehen
    waren (`loaned_at_load > 0`): oben **session-basiert** (seit Aufrufen
    ausgeliehene / beim Aufrufen noch offene vorgemerkte — dieselben Zahlen wie
    im Druck- und Nächster-Schüler-Hinweis des Helfers), unten **gesamt**
    (ausgeliehene / angemeldete). Ohne Vorbestand identisch → eine Zeile.
  - **„Leihschein"** während ein Druckauftrag für den Schüler läuft
    (`slip_printing`).
  - **„Aktiv"** nach fertigen Druck (`slip_printed`); **„Fertig"** beim
    Abschließen (wie bisher).
- **Info-Spalte:** X/Y- und Leihschein-Badges entfernt (in die Status-Spalte
  gewandert); nur noch Anmelde-/Zahlstatus-Badges.
- **Info-Spalte ganz entfallen** — die roten Anmelde-/Zahlstatus-Hinweise
  („Nicht angemeldet", „… € offen", „Ermäßigungs-/Befreiungsantrag ausstehend")
  wandern ebenfalls in die Status-Spalte: ergänzend hinter dem Status-Badge
  (immer rot); bei **fertig** ersetzt der Hinweis das grüne „Fertig"-Badge
  (kein Hinweis bekannt → bleibt „Fertig"). Tabellenkopf/`colspan` angepasst
  (5 → 4 Spalten); `infoBadges` → `hintBadges` (liefert Badge-Array);
  `.q-info` → `.q-status` (Flex-Container, `align-items: center`).
- **Server:**
  - `slip_printing` wird **dynamisch** aus der Print-Queue im Snapshot
    abgeleitet (`PrintQueue.in_flight_student_ids()`, abgefragt in
    `AppState.state_snapshot`), nicht auf dem `QueueStudent` gemutet. Da
    `_notify_all` der Print-Queue bei jedem Druck-Übergang den vollen
    Host-Snapshot broadcastet, folgt die Anzeige live — vom Enqueue
    („Leihschein") bis zur Fertigstellung („Aktiv"). `QueueStudent.as_dict(slip_printing=…)`
    ergänzt das Feld; Default `False` hält die Helfer-Client-Pfade unverändert.
  - `loaned_at_load` (bei Hydration bereits ausgeliehene Bücher) auf dem
    `QueueStudent` gespeichert, gesetzt in `init_book_progress`, zurückgesetzt in
    `reset_progress`. Im Snapshot ausgeliefert; der Host leitet daraus die
    session-basierten Zahlen ab.
- **CSS:** `.q-progress` (zweizeiliger Badge: `.q-progress-main` / `.q-progress-sub`).
- **Tests:** `test_in_flight_student_ids_tracks_enqueue_and_done` sichert den
  Übergang Enqueue→fertig im Snapshot; `test_session_progress_excludes_pre_loaned_books`
  sichert die session-basierte X/Y-Ableitung.

## 2026-08-04 — Fertig-Optionen unter der Live-Ausgabe (UI-Scaffold)

- **Host, Klassen-Tab + panel-new:** unter dem Live-Ausgabe-Schalter zwei
  **Checkboxen** (`.check-line`) ergänzt — rein UI, die eigentliche Funktion
  folgt später (kein Serverkontakt, keine Persistenz):
  1. **„Leihschein unterschreiben"** — angehakt = Schüler erst fertig, wenn der
     Leihschein unterschrieben ist; nicht angehakt = fertig bereits bei
     gedruckt (Default).
  2. **„Leihschein wird vom Lehrer eingesammelt"** — darunter.
- **Ausgrau-Regel:** beide Checkboxen ausgegraut, wenn die Live-Ausgabe
  **aus** ist; „eingesammelt" zusätzlich ausgegraut, wenn „Leihschein
  unterschreiben" **nicht** angehakt ist (= fertig bei gedruckt).
  - Klassen-Tab: `updateCtxDoneOpts(id)` (gerufen aus `renderCtxPrinters`,
    `setContextLiveAusgabe` und beim Wechsel der signed-Checkbox via
    delegiertem `change`-Handler).
  - panel-new: `updateNewClassDoneOpts()` (gerufen aus
    `renderNewClassPrinters`, dem Live-Schalter-Listener und dem
    signed-Checkbox-Listener).
- **CSS:** `.check-line input:disabled` / `.check-line:has(input:disabled)`
  (eingegraut, `cursor: not-allowed`) ergänzt.

## 2026-08-04 — Kopplung Drucker ↔ Live-Ausgabe pro Klasse

- **Regel:** Live-Ausgabe (Modus B) für eine Klasse setzt **mindestens einen
  Drucker** voraus; umgekehrt darf bei **aktiver** Live-Ausgabe der **letzte
  Drucker nicht abgewählt** werden — erst Live-Ausgabe schließen, dann den
  Drucker entfernen. `None` (alle Pool-Drucker) gilt als „Drucker gewählt".
- **Client (rot wie Helfer-Statuszeile, `var(--danger-text)`):**
  - **Klassen-Tab** (`buildClassPanel`/`renderCtxPrinters`): der Hinweis
    „Es ist mindestens ein Drucker auszuwählen" erscheint **erst beim Versuch**,
    den Live-Ausgabe-Schalter ohne gewählten Drucker zu aktivieren (Schalter
    wird revertiert, Hinweis unter dem Schalter, `data-ctx-live-warn`). Wird bei
    aktiver Live-Ausgabe der letzte Drucker abgewählt, wird die Checkbox
    **revertiert** + roter Hinweis „Zuerst Live-Ausgabe schließen" (unter den
    Druckern, `data-ctx-printer-warn`). Beide Hinweise verschwinden, sobald
    wieder ein Drucker gewählt ist.
  - **panel-new** („Neue Klasse"): analog — Hinweis „Es ist mindestens ein
    Drucker auszuwählen" erst beim Aktivierungsversuch ohne Drucker
    (`#new-class-live-warn`), Revert + Hinweis beim Versuch, den letzten
    Drucker bei aktiver Live-Ausgabe abzuwählen (`#new-class-printer-warn`).
    Auswahl wird jetzt bei jeder Änderung nach `localStorage` persistiert
    (vorher nur beim Öffnen), damit ein `applyState`→`renderNewClassPrinters`-
    Re-Render die Wahl nicht verwirft.
- **Server (defensive Absicherung):**
  - `open_class`: `printers: []` + `live_ausgabe: true` → 400
    „Es ist mindestens ein Drucker auszuwählen" (beide Pfade: neu + reused).
  - `set_context_printers`: leere Menge + `live_ausgabe` aktiv → 400
    „Zuerst Live-Ausgabe schließen".
  - `set_context_live_ausgabe`: Aktivieren ohne Drucker → 400
    „Es ist mindestens ein Drucker auszuwählen"; Ausschalten immer erlaubt.
  - Gemeinsamer Helfer `_require_printer_for_live`.
- **Touch:** `server/routes/classes.py` (Helfer + 3 Endpunkte + Docstrings),
  `web/host.html` + `web/host-render.js` (Klassen-Tab + panel-new: Gate,
  Hinweise, Listener, `updateCtxCouplingHints`/`updateNewClassLiveGate`),
  `web/host.css` (deaktivierter Switch `.switch:has(input:disabled)`,
  `.ctx-coupling-warn`), `tests/test_api_guards.py` (3 neue Tests + vorhandener
  um `live_ausgabe` ergänzt). Keine IServ-Writes, rein In-Memory. Suite grün.

## 2026-08-04 — Leere Drucker-Auswahl pro Klasse = kein Drucker (nicht „alle")

- **Bisher** wurde eine leere Drucker-Auswahl beim Öffnen/Umkonfigurieren einer
  Klasse still zu „alle Pool-Drucker": `_resolve_allowed_printers` mappte
  `None` **und** `[]` auf `None` (Allowlist = alle). Wer alle Checkboxen
  abwählte, bekam trotzdem still alle Drucker als Vorauswahl.
- **Neu** unterscheidet die Funktion: `None` (Feld fehlt, Default/Tests) →
  `None` (alle), aber eine **explizit leere Liste** `[]` → **leere Menge** =
  bewusst „kein Drucker ausgewählt". Wirkung (mit Nutzer geklärt: nur
  Vorauswahl, **nicht** Druck blockieren):
  - Helfer-/Host-Druckdialog bekommt für diese Klasse **keine Vorauswahl**
    (`own_print_defaults` → `[]`); der Druck bleibt per **manueller Auswahl**
    im Druckdialog möglich (die `printers`-Auswahl übersteuert die Allowlist,
    s. `slips.py` / `ws.py` `_handle_print`).
  - Klassen-Tab und panel-new zeigen die leere Auswahl **ehrlich** an (keine
    Checkbox angehakt) plus Hinweis „Kein Drucker ausgewählt — Leihschein-Druck
    nur per manueller Auswahl im Druckdialog."
  - Der Fallback-Pfad (Druckdialog sendet keinen `printers`-Key, nur alt/Tests)
    verweigert bei leerer Menge weiterhin mit „Kein erlaubter Drucker im Pool
    für diese Klasse" — das betrifft die echte UI nicht (sie sendet immer eine
    `printers`-Auswahl).
- **Draht-Format:** `state_snapshot().contexts[id].allowed_printers` ist nun
  `[]` statt `null`, wenn die Auswahl leer war. `null` bleibt „alle". Der
  Charakterisierungs-Test `test_state_contract.py` friert nur Top-Level-Schlüssel
  ein → keine Anpassung nötig. Neuer Test `test_open_class_empty_printers_means_none_not_all`
  pinnt die Semantik (`[]` → `set()`, Feld fehlt → `None`). Suite grün.
- Touch: `server/routes/classes.py` (`_resolve_allowed_printers`),
  `web/host-render.js` (`renderCtxPrinters`, `renderNewClassPrinters`,
  `setContextPrinters`-Kommentar), `web/host-state.js` (Speicher-Kommentare),
  `tests/test_api_guards.py`. Keine IServ-Writes, rein In-Memory.

## 2026-08-04 — Live-Ausgabe (Modus B) pro Klasse ein/aus

- **Bisher** war Modus B (Live-Ausgabe) rein **global** (ein `modus_b_open`-
  Schalter, ein Join-Secret/QR, ein Pairing-Code-Pool) — in jeder Klassenansicht
  erschien der „Pairing (Modus B)"-Kasten immer, unabhängig davon, ob die Klasse
  Live-Ausgabe nutzen will.
- **Neu** gibt es einen **pro-Klasse**-Schalter `live_ausgabe` (Default `true`):
  - **panel-new** („Neue Klasse" öffnen): unter der Druckerauswahl, mit Strich
    getrennt, ein Switch „Live-Ausgabe (Modus B) aktivieren". Wahl wird fürs
    nächste Öffnen in `localStorage` gemerkt (analog der Drucker-Allowlist).
  - **Klasseneinstellungen-Detail** im Klassen-Tab: derselbe Switch unter der
    Drucker-Karte, nachträglich live änderbar (`POST /api/context-live-ausgabe`).
  - **Aus = kein Modus-B-Kasten** in der Klassenansicht (`display:none` auf der
    Pairing-Karte) **und kein Pairing-Button** in der Queue-Zeile **und kein
    Pairing-Hinweis** in der „Aktuell in Ausgabe"-Box (Now-Serving-Empty-Text
    nennt dann nur noch „Nächster" statt „Pairing oder Nächster").
- **Reichweite bewusst nur UI/Sichtbarkeit** (mit Nutzer geklärt): das globale
  Modus-B-Backend (Join-Secret/QR, iPad-Freischalt, offene pending-Sessions)
  bleibt. Pairing-Codes lassen sich nur für Klassen mit `live_ausgabe=true`
  zuordnen — client-seitig (Button fehlt) **und** defensiv serverseitig
  (`/api/student/pair` → 403, wenn der Kontext des Schülers `live_ausgabe=false`).
- **Draht-Format:** `state_snapshot().contexts[id]` erhält zusätzlich
  `live_ausgabe: bool`. Der Charakterisierungs-Test `test_state_contract.py`
  friert nur Top-Level-/`modus_b`-Schlüssel ein → kein Test-Anpassung nötig,
  Suite bleibt grün (331 passed).
- Touch: `server/state.py` (`ClassContext.live_ausgabe`, Snapshot),
  `server/routes/_deps.py` (`OpenClassRequest.live_ausgabe`,
  `ContextLiveAusgabeRequest`), `server/routes/classes.py` (`open_class` setzt
  es, neuer Endpunkt `/api/context-live-ausgabe`), `server/routes/modus_b.py`
  (Pairing-Gate), `web/host.html` + `web/host-render.js` + `web/host-state.js`
  (Switch, Rendering, Persistenz). Keine IServ-Writes, rein In-Memory.

## 2026-08-04 — Host/Klassen-Reiter: „Klasseneinstellungen"-Block

- **Bisher** ließ sich rechts der Klasse nur „Schüler hinzufügen" aufklappen;
  die Drucker-Auswahl stand separat unten in der Betrieb-Spalte. Neu heißen
  beide zusammen **„Klasseneinstellungen"** (aufklappbarer `<details>`-Block
  in der linken Setup-Spalte, Drucker-Karte oberhalb der Schüler-hinzufügen-Karte).
- **Platz zurückgeben beim Zuklappen:** ist der Block geschlossen, wird das
  Zweispalten-Grid (`minmax(320px,360px) 1fr`) per CSS-`:has()` einspaltig
  (`1fr`) — die Klassen-Übersicht (Now-Serving, Pairing, Queue) nutzt die volle
  Breite. Beim Aufklappen greift wieder das Zweispalten-Grid.
- **Abstand zum Button:** geschlossener Block setzt das Summary-`margin-bottom`
  auf 0 und die Grid-Gap auf 16px, sodass der Abstand zwischen Button und
  „Aktuell in Ausgabe"-Box dem Karten-Abstand (16px) entspricht statt 10+18px.
- **Default geschlossen:** der Block ist beim Öffnen eines Klassen-Reiters
  initial zu — Übersicht rückt direkt an den Button; Aufklappen per Klick.
- **„Betrieb — <Klasse>"-Label** entfällt (war nur Orientierungstext).
- Touch: `web/host-render.js` (`buildClassPanel`) + `web/host.css` (`.layout`
  `:has`-Regel, Summary-Margin). Keine Test-/Server-Änderung.

## 2026-08-04 — Drucker-Display: Reiter-Aufräumen beim Trennen

- **Nicht eingeschaltete Displays verschwinden beim Schließen:** bislang blieb
  jede Drucker-Display-Session beim WS-Trennen erhalten (damit Reload dieselbe
  Session wiederverwendet). Neu: im `finally`-Block von `ws_drucker_display`
  (`server/routes/ws.py`) werden **nicht autorisierte** Sessions beim Trennen
  **ganz entfernt** — der Host-Reiter verschwindet automatisch. Das deckt zwei
  Fälle zusammen: (1) Display wird vor dem Einschalten geschlossen (Tab zu),
  (2) Display wird durch einen erneuten `/drucker-display`-Aufruf abgelöst
  (frischer Token via Redirect → neue Session; die alte WS trennt dabei).
- **Eingeschaltete Displays bleiben als grauer Punkt:** autorisierte Sessions
  werden beim Trennen NICHT entfernt, nur `ws=None` (`connected=False` → grauer
  Punkt). Ein Reload mit demselben Token verbindet sie wieder (grün). So bleibt
  ein versehentlich geschlossenes, bereits eingerichtetes Display am Host
  sichtbar und per QR wiederbelebbar.
- Takeover-Schutz erhalten: löst/entfernt nur, wenn `d.ws is websocket` (kein
  zwischenzeitlicher Reconnect hat den Token übernommen).
- **Zuverlässige Disconnect-Erkennung (Follow-up):** das `finally`-Aufräumen
  greift nur, wenn der Server den WS-Disconnect erkennt. Beim Navigieren auf
  einen neuen Token (`/drucker-display` ohne Token → Redirect) schließt der
  Browser die alte WS, aber der Close-Frame erreichte den Server unzuverlässig
  — die alte Seite blieb am Host fälschlich grün (bis zum uvicorn-Ping, Default
  20+20 s). Wie beim Tab-Schließen den Server jetzt **aktiv beim Entladen
  benachrichtigen** — beides male wird die Seite mit Token geschlossen, nur der
  Benachrichtigungsweg unterscheidet sich:
  - **`navigator.sendBeacon` auf `/api/drucker-display/departed`** (`pagehide`/
    `beforeunload`): sendBeacon ist genau für „Server beim Entladen zuverlässig
    benachrichtigen" gemacht und kommt auch beim Navigieren/Redirect verlässlich
    an. Der neue unauthentifizierte Endpunkt (`server/routes/drucker_display.py`)
    schließt die WS und räumt auf (autorisiert → grau, nicht autorisiert →
    entfernt) — derselbe Effekt wie der `finally`-Block, nur Client-getriggert.
    Sofort, kein Warten auf einen Ping. Idempotent (pagehide + beforeunload
    können beide feuern).
  - **Client schließt zusätzlich sauber:** `pagehide`/`beforeunload` schließen
    die WS mit Code 1001; ein `unloading`-Flag unterdrückt den Auto-Reconnect,
    damit die alte Session nicht wiederbelebt wird.
  - Ein früherer Heartbeat-Ansatz (Client-Ping alle 10 s + `asyncio.wait_for`-
    Timeout 20 s) wurde wieder entfernt — er machte die Erkennung erst nach 20 s
    bemerkbar; `sendBeacon` ist sofort und simpler. Für den Restfall (echter
    Verbindungsabbruch ohne Entladen, z. B. WLAN weg) bleibt der uvicorn-Ping
    als Fallback. Greift nur pro geschlossener Display-Seite — weitere Displays
    auf demselben Gerät (andere Tabs) bleiben unberührt (mehrere pro Gerät
    weiterhin möglich).
- Tests: `tests/test_drucker_display.py` — zwei WS-Disconnect-Tests
  (unautorisiert → entfernt; autorisiert → bleibt `ws=None`) + vier
  `/departed`-Endpunkt-Tests (unautorisiert → entfernt; autorisiert → grau;
  ungültig/unbekannt → No-op; idempotent).

## 2026-08-03 — Klassen-Lade-Hinweis + Snapshot-Race beim nebenläufigen Broadcast

- **Host-Hinweis beim Klassen-Öffnen:** der bisherige „Lade Schüler…"-Toast
  verschwand nach 4 s (Auto-Dismiss) — bei langsamen IServ-Laden war er weg,
  bevor die Klasse da war. Neu: ein **persistenter Toast** (`showMsgPersistent`
  + `finalizeToast` in `web/host-render.js`, ohne Auto-Dismiss bzw. Text-in-
  place-Austausch) zeigt „Lade {form}…" und bleibt stehen, bis die Klasse
  wirklich geladen ist; danach wird **derselbe Toast-Element** in-place zu
  „{form} geladen — {count} Schüler" (kein zweiter Toast, der sich mit dem
  ersten in der Fade-Transition überlappt). `form` aus IServ ist bereits
  „Klasse 8a" o. ä. → kein zusätzliches „Klasse"-Präfix davor (sonst
  „Klasse Klasse 8a"); Wortwahl: „Lade Klasse 8a…" / „Klasse 8a geladen —
  28 Schüler".
- **Per-Klassen-Sperre statt globalem Button-Lock:** `openingForms: Set` in
  `host-render.js` blockiert ein erneutes „Öffnen" für **dieselbe** Klasse,
  solange sie lädt — der „Öffnen"-Button bleibt für **andere** Klassen
  drückbar (kein `disabled`, kein globales `classLoading`-Lock mehr). 409-
  „Trotzdem öffnen"-Pfad gibt die Sperre vor dem `confirmDialog` frei, damit
  der rekursive Aufruf sauber neu startet.
- **Root-Cause „Klasse öffnet sich zu früh":** `open_class` registrierte den
  Kontext sofort per `state.open_context(form)` in `state.contexts` (leere
  Queue), dann folgten langsame IServ-Awaits (`get_students_for_form`,
  `_load_student_flags`, `_ensure_class_catalog`). Während dieser Pause
  feuerte jeder nebenläufige Endpoint, der `broadcast_host(state_snapshot())`
  aufruft — Helfer hinzufügen (`/api/add-helper`), Modus B aktivieren/
  deaktivieren (`/api/modus-b/open|close`) — und snapshottete den halb
  geladenen Kontext mit leerer Queue. Der Host sah den neuen Kontext in
  `state.contexts` → Klassen-Tab erschien **vorzeitig** (0 Schüler), lange
  bevor `open_class` fertig war.
- **Fix — Kontext erst veröffentlichen, wenn vollständig geladen**
  (`server/state.py`, `server/routes/classes.py`): neues
  `ClassContext.loading: bool`; `state_snapshot()` und
  `real_contexts_summary()` schließen `loading`-Kontexte aus (`if not
  c.loading`), `active_form` wird für ladende Kontexte unterdrückt. Der
  Kontext bleibt intern in `self.contexts` (für Lookups wie
  `_ensure_class_catalog` via `ctx_or_active`), wird aber nicht an Clients
  gesendet. `open_class`: `ctx.loading = True` direkt nach `open_context`,
  Flags + Katalog bleiben einzeln nicht-fatal (`try`/`except` wie bisher),
  `ctx.loading = False` erst kurz vor dem finalen `broadcast_host`. Re-Open-
  Pfad: `existing.loading` → `409 {reason: "loading"}` (Absicherung gegen
  Races/andere Hosts), statt vorzeitig `count: 0` als „geladen" zu melden.
- **Draht-Format unverändert** — `test_state_contract.py` bleibt grün
  (`loading`-Kontexte sind im Test nicht beteiligt; nur ein Filter mehr, kein
  neues Feld). Volle Suite grün, `node --check` OK, Ruff clean. Live-Check am
  echten Gerät offen. Details: `_logs/2026-08-03_sba_klasse_laden_hinweis.md`;
  Lessons Learned: neuer Gotcha „Snapshot-Race bei nebenläufigem Broadcast
  während asyncem Kontext-Aufbau".

## 2026-08-03 — Druckerauswahl im Druck-Dialog (Host + Helfer)

- **Neu:** im Host- und im Helfer-Druck-Dialog gibt es direkt über dem
  „Schüler-Leihschein (2. Seite)"-Checkbox ein Dropdown zur Druckerauswahl.
  Geschlossen zeigt es die gewählten Drucker als kommaseparierte
  „Label (Systemname)" (Platzhalter bei Leer-Auswahl); aufgeklappt eine
  Checkbox-Liste aller Pool-Drucker. Vor dem Drücken auf Drucken muss
  mindestens ein Drucker ausgewählt sein, sonst wird der Druck blockiert
  und ein Hinweis angezeigt.
- **Vorauswahl:** erlaubte Drucker der jeweiligen Klasse — am Host die
  Klasse des gedruckten Schülers, am Helfer die eigene (letzte zugehörige)
  Klasse (`helper.context_id`). „Klasse erlaubt alle" (`None`) → alle
  Pool-Drucker; keine Klasse → kein Drucker. Die Auswahl wird mit dem
  Druckauftrag an den Server geschickt (`printers`-Feld im WS-`print` bzw.
  im POST `/api/print-loan-slip`), der sie statt `allowed_printers_for`
  verwendet (Fallback auf die Klassen-Allowlist, wenn das Feld fehlt —
  alt/Tests).
- **Datenfluss Helfer:** der Helfer bekommt Pool + Vorauswahl-IDs über die
  `settings`-WS (neue Felder `printers`, `print_default_ids`); bei Pool-/
  Klassenänderungen neu gepusht (`broadcast_settings` in `_after_pool_change`,
  `/api/context-printers`, `/api/open-class`). Vorauswahl wird nur
  übernommen, solange der Dialog *nicht* offen ist (fremde settings-Pushes wie
  Dev-Toggles setzen eine laufende manuelle Auswahl nicht zurück).
- **Server:** `pool_light`/`own_print_defaults` (neu in `state.py`, neben
  `allowed_printers_for`); `PrintLoanSlipRequest.printers` neu.
- **Komponente:** gemeinsames `mountPrinterPicker` in `web/common.js` (CSS in
  `host.css` bzw. inline in `scan.html`).
- **Tests:** 325 passed, ruff clean. (Kein eigener Test für die neue
  Druckerauswahl; Fallback-Pfad für `printers=None` erhält bestehende Tests.)

## 2026-08-03 — Host-Druck: viele Aufträge gleichzeitig (Endpoint nicht-blockierend)

- **Symptom:** startete der Host viele Druckaufträge, zeigte der Druck-Button
  ab einer bestimmten Anzahl „…" und der Auftrag tauchte nicht in der Host-
  Druckerwarteschlange auf.
- **Ursache:** `/api/print-loan-slip` enqueued zwar sofort, blockierte danach
  aber bis zum fertigen Druck (`job.done.wait()`) und hielt die HTTP-Verbindung
  die gesamte Druckdauer offen. Browser erlauben nur ~6 gleichzeitige
  Verbindungen pro Origin → ab dem 7. Auftrag blieben die Fetches beim Browser
  hängen (Button „…"), der Request wurde gar nicht erst gesendet, der Auftrag
  kam nie in die Warteschlange.
- **Fix:** der Endpoint enqueued und kehrt **sofort** zurück
  (`{"ok": true, "queued": true, "job_id": …}`). Status, Position und Ergebnis
  liefert weiterhin die WS (`print_progress`/`print_result`, zielgerichtet an
  den startenden Host — `showPrintProgress`/`showPrintResult` bereits
  vorhanden). Validierung (kein Drucker / kein erlaubter Drucker) bleibt
  vorab als sofortige 400. Entspricht dem Helfer-WS-Pfad (`_handle_print`),
  der schon immer nur enqueued.
- **Tests:** 325 passed, ruff clean (kein HTTP-Level-Test für den Endpoint
  vorhanden; Tests nutzen `print_loan_slip_for` + `job.done.wait()` direkt).

## 2026-08-03 — Drucker-Display: Warteschlange zeigt alle Namen, Überlauf transparent, Resize-adaptiv

- **Alle Namen stets gerendert (kein Cap, kein Clip):** die Warteschlangen-Karte
  behält ihre natürliche Höhe — jeder Auftrag bleibt im DOM und sichtbar
  gerendert, auch die untersten (vollständig transparent). Zuvor kappte
  `maxHeight`+`overflow:hidden` den Kasten und schnitt Namen hart ab.
- **Maske statt Clipping:** `applyWaitingOverflow` setzt nur noch eine
  `mask-image`-Gradient (deckend bis vorletzte Zeile, eine Zeile Ausblenden
  von deckend nach vollständig transparent, darunter vollständig transparent).
  Der weiße Kasten endet weich (auslaufend), überlappende Namen sind
  transparent, nicht abgeschnitten.
- **Seite fix im Viewport:** `html/body { overflow:hidden }` (Drucker-Display
  ist ein festes Bildschirmgerät, nicht zum Scrollen) — die transparente
  Überlappung wird unten unsichtbar abgeschnitten, kein Scrollbalken.
- **Resize-adaptiv:** Maske wird nach jedem Render (vor dem FLIP), nach Ablauf
  einer FLIP-Animation (`flushPendingQueue`) und bei Fenster-Resize (debounced)
  neu berechnet — Fenstergrößenänderung verschiebt die Ausblende-Grenze mit.
- Backend nimmt ohnehin alle Aufträge in die Warteschlange auf (`enqueue` ohne
  Cap) und sendet alle (`display_view` filtert nur nach Display-Zuweisung);
  keine Backend-Änderung nötig.

## 2026-08-03 — Drucker-Display: Labels gleiten, z-index, Überlauf-Ausblendung, Vorgänger-Dispatch

- **Kategorie-Labels gleiten (Q2 + neu):** „Gedruckt"/„Wird gedruckt"/„Nächster"
  bekamen eigene FLIP-Schlüssel (`data-flip-id`). Kollabiert eine Kategorie —
  „Gedruckt"-Kästchen verschwindet nach TTL, oder ein Auftrag verlässt „Wird
  gedruckt"/„Nächster" — gleiten das/die Label(s) und alle Kästchen darunter
  nach oben statt zu springen. Realisiert über den neuen `flipFromOldRects`-
  Helfer, der Karten, Auftrags-Kästchen UND Labels FLIPt.
- **Kein Doppel-FLIP (Karte + Inhalt):** innere Elemente (Kästchen, Labels)
  werden RELATIV zur jeweiligen Karte gerechnet (Karten-Delta abgezogen). Bisher
  wurde die absolute Bewegung genutzt — das hätte doppelt gegriffen, sobald eine
  Karte selbst wandert (z. B. Warteschlangen-Karte rückt nach, weil das Grid
  darüber wächst). Jetzt übernimmt der Karten-FLIP das Mitwandern, der innere
  FLIP nur die Bewegung innerhalb der Karte.
- **z-index (Teil 3):** `.printer-card` (z 2) über `.dd-waiting-card` (z 1) +
  `.dd-order { z-index:1 }` — ein Auftrag, der per FLIP von der Warteschlange in
  einen Drucker wandert, malt VOR den weißen Kästen (Drucker + Warteschlange),
  nicht dahinter. `position: relative` für z-index nötig (Karten).
- **Überlauf-Ausblendung, nur Warteschlangen-Karte (Q3):** `applyWaitingOverflow`
  kappt bei Namens-Überlauf die Kartenhöhe (`maxHeight`, `overflow:hidden`) und
  blendet den untersten Bereich (eine Zeile) von oben deckend nach unten
  transparent aus (`mask-image: linear-gradient`); überlappende Namen sind
  vollständig transparent — der Kasten endet rechtzeitig. Aufgerufen nach
  jedem Render (vor dem FLIP) + bei Resize (debounced). Drucker-Karten bleiben
  unberührt.
- **Vorgänger-Dispatch (Q1):** wenn ein Vorgänger beim Start des nächsten
  finalisiert wird (voriger Commit), wird jetzt auch der Scheduler geweckt
  (`_wake.set()`) — der nächste Warteschlangen-Auftrag rückt sofort nach
  (Pipeline voll halten), wie im normalen Finalize-Pfad.
- **Tests:** 325 passed, ruff clean.

## 2026-08-03 — Druckwechsel: Vorgänger beim Start des nächsten finalisieren

- **Wurzel im Backend gekappt (nicht nur Frontend-Kompensation):** ein Drucker
  druckt nur einen gleichzeitig — wird ein Auftrag `printing`, muss jeder
  FRÜHERE im Slot, der noch `printing` ist, tatsächlich fertig sein (das OS
  ist zum nächsten übergegangen, sonst würde dieser nicht drucken). Bisher
  entstand eine Status-Überlappung durch Polling-Lag: Y's Tracker sah
  `printing`, bevor X's Tracker `absent` sah → ein Snapshot mit zwei
  `printing`-Jobs → Sprung im Display.
- **`_track_job`:** beim `spooled→printing`-Übergang werden alle früheren
  `printing`-Jobs im selben Slot finalisiert (done, aus dem Slot,
  `_last_printed` mit job_id) und ihr Tracker abgebrochen (`cancel` + join,
  s. `_handle_stall`-Muster) — sonst würde der Vorgänger-Tracker noch selbst
  finalisieren und `_last_printed` überschreiben. Das Druckergebnis des
  Vorgängers sendet der übernehmende Tracker (`_notify_result`), da der
  Vorgänger-Tracker nicht mehr dazu kommt.
- Frontend (voriger Commit) behält das Mitführen überlappender `printing`-
  Jobs als „Nächster" als defensives Safety-Net; `pool_printers` liefert sie
  nach dem Backend-Fix regulär nicht mehr.
- **Tests:** `test_printing_finalizes_predecessor_on_overlap` (Vorgänger wird
  beim `printing`-Übergang finalisiert, Tracker abgebrochen, kein Doppel-
  `printing` im Slot, Vorgänger-Ergebnis gesendet). 325 passed, ruff clean.

## 2026-08-03 — Drucker-Display: Sprung Nächster→Wird gedruckt beim Druckwechsel

- **Symptom (intermittierend):** beim Druckwechsel sprang der Name manchmal
  von „Nächster" nach „Wird gedruckt" (hinter den noch stehenden Namen), statt
  zu gleiten — und der vorige Name verschob sich danach.
- **Ursache:** beim Druckwechsel überlappt sich der Status kurz. Der vorige
  Job ist im OS-Poll noch `printing` (erst beim nächsten Poll `absent`/
  finalisiert), während der nächste schon physisch druckt und ebenfalls
  `printing` wird. `pool_printers` gibt dann ZWEI `printing`-Orders aus. Das
  Display nahm per `orders.find(status==='printing')` aber nur den ERSTEN
  (den vorigen) als „Wird gedruckt"; der zweite `printing`-Job fiel durchs
  Raster (weder `printing` noch `spooled`) → aus dem DOM verschwunden. Sein
  FLIP-Schlüssel (job_id) war eine Runde lang weg → beim Finalisieren des
  Vorgängers kein alter Rect → Sprung statt FLIP.
- **Fix (Frontend):** nur der erste `printing`-Job wird als „Wird gedruckt"
  gezeigt; weitere gleichzeitig `printing`-Jobs bleiben als „Nächster"
  sichtbar (`nextOrds` = spooled + überlappende printing, in Slot-Reihenfolge).
  So bleibt der job_id-Schlüssel erhalten und der Auftrag gleitet bei der
  Finalisierung des Vorgängers fließend von „Nächster" nach „Wird gedruckt".
- **Test:** `test_pool_printers_overlap_two_printing_jobs` friert ein, dass
  `pool_printers` bei zwei `printing`-Jobs im Slot zwei `printing`-Orders
  mit stabilen job_ids liefert (Grundlage fürs Frontend-Handling).
- 324 passed, ruff clean.

## 2026-08-03 — Drucker-Display: beim Verschwinden gleitet der Rest nach

- **Verschwinden → Rest gleitet, statt springt:** das „Gedruckt"-Kästchen
  wurde nach seiner TTL (30s) bisher direkt per `remove()` aus dem DOM
  gerissen — dabei sprang der Rest (z. B. der Warteschlangen-Kasten darunter)
  sofort hoch. Jetzt: `removePrintedAndFlip` merkt die Karten-Positionen,
  entfernt das Kästchen (ohne Animation — nur das Verschwinden selbst ist
  nicht dynamisch, s. Nutzer-Vorgabe) und lässt die betroffenen Karten per
  FLIP an ihre neue Position gleiten. Gezielt (kein vollständiger Re-Render),
  damit andere Drucker-TTL-Timer nicht zurückgesetzt werden.
- **TTL-Entfernung im Coalescing-Schutz:** trifft der TTL-Timer während
  einer laufenden FLIP-Animation, wird die Entfernung aufgeschoben
  (`pendingTtlPid`) und nach Ablauf nachgezogen — keine Bewegung bricht eine
  andere ab. Ein frischer Snapshot macht die aufgeschobene Entfernung
  hinfällig (Kästchen ist im Snapshot eh weg).
- Die Snapshot-basierten Verschwinden-Fälle (Auftrag verlässt die
  Warteschlange → Rest gleitet hoch; Auftrag wandert per FLIP zum Drucker)
  funktionieren bereits über den FLIP+Coalescing-Mechanismus.
- Frontend-only. 323 passed, ruff clean.

## 2026-08-03 — Drucker-Display: Bewegungen nicht abbrechen, Klasse robuster

- **Snapshot-Koalescing (Bewegungen nicht abbrechen):** trifft während einer
  laufenden FLIP-Animation ein neuer Snapshot ein, wird er gehalten und erst
  nach Ablauf der Animation (FLIP_MS = 500ms) als aktuellster nachgezogen. So
  startet keine neue Bewegung, die eine noch laufende mittendrin abbricht —
  jede Bewegung wird vollständig sichtbar (Nutzer-Vorgabe). Eintreffende
  Snapshots werden zum letzten gepoolt (nur der aktuellste zählt). Bei
  `prefers-reduced-motion` nur 80ms Pooling (die FLIP-Animation ist eh instant).
- **Klasse in der Warteschlange robuster:** `orderBox` bekommt `name` + `form`
  jetzt direkt (vorher kombinierter String + parseOrder-Roundtrip). Die
  Warteschlange reicht `w.student` + `w.form` getrennt — die Klasse steht
  zuverlässig in der eigenen Spalte, auch wenn `w.form` fehlen sollte. Neue
  Helferfunktion `orderBoxFromRaw` für kombinierte Strings (`j.name`,
  `printed_name`).
- Frontend-only (kein Backend-/Test-Änderung). 323 passed, ruff clean.

## 2026-08-03 — Drucker-Display: FLIP Warteschlange↔Drucker, Klasse, Schriftgrößen

- **FLIP über Behälter hinweg (Warteschlange → Drucker):** Auftrags-Kästchen
  wandern beim Dispatch fließend von der vollbreiten Warteschlange in die
  (schmalere) Drucker-Spalte — bewegt **und** schrumpft, weil die Drucker-Spalte
  schmaler ist. Dafür ist der FLIP-Schlüssel je Kästchen jetzt die stabile
  `job_id` (vorher containerspezifisch `${pid}::${raw}`, was einen Wechsel des
  Behälters unterbrach). Ein Auftrag behält seinen Schlüssel über den gesamten
  Lebensweg: Warteschlange → Nächster → Wird gedruckt → Gedruckt.
- **Backend:** `pool_printers` reichert `orders` um `id` (job_id) an;
  `waiting_list`-Einträge bekommen `job_id`; `_last_printed` führt zusätzlich
  die `job_id` mit (TTL 30s) und `display_view` reicht sie als `printed_job_id`
  durch. So sind alle vier Behälter über denselben Schlüssel verbunden.
- **Karte gleitet nach (FLIP):** die Drucker- und Warteschlangen-Karten selbst
  bekommen `transition: transform .5s` + einen `data-flip-id`-Schlüssel; beim
  Reflow (z. B. eine Reihe wächst durch einen neuen Auftrag) gleiten die Karten
  an ihre neue Position statt zu springen (nur translate, kein scale — die
  Karten behalten ihre Breite).
- **Klasse in der Warteschlange:** `waiting_list.student` führt die Klasse nicht
  im String (`slip_name` bekommt `form=None`); das Frontend hängt `(form)` an,
  bevor es `parseOrder()` füttert, sodass die Klasse jetzt in der Warteschlange
  wie in den Druckerkarten angezeigt wird.
- **Schriftgröße Auftraggeber = Name:** `.dd-origin` (1rem → 1.6rem) und
  `.dd-ico` (1.3em → 1em) so groß wie der Name (1.6rem); vorher war der
  Auftraggeber (Symbol + Helfername) kleiner als der Name.
- Tests: `test_pool_printers_orders_carry_originator` (asserts `orders[0]["id"]`),
  `test_display_view_waiting_list_has_originator_info` (asserts `job_id` je
  Eintrag), neu `test_display_view_printed_carries_job_id_and_originator`.
  323 passed, ruff clean.

## 2026-08-03 — Drucker-Display: Auftraggeber-Symbol in den Auftrags-Kästchen

- **Auftraggeber rechts im Kästchen:** jedes Auftrags-Kästchen (Gedruckt / Wird
  gedruckt / Nächster / Warteschlange) zeigt rechts den Auftraggeber:
  - **Helfer** → Person-Symbol (Kopf + Schultern, dasselbe SVG wie das
    Helferlabel in der „aktuelle Ausgabe"-Now-Serving-Karte im Host) + Helfername.
  - **Host** → Laptop-Symbol (Display-Rechteck + Basis, aus geometrischen
    Figuren), kein Name.
  - Schüler/sonstige → nichts (derzeit nicht enqueueiert).
- **Backend:** `PrintQueue._originator_info(state, job)` liefert `{type, name}`
  (helper namentlich via Token-Lookup, Fallback „Helfer"; host; student).
  `pool_printers(printers, state=None)` reichert pro Drucker `orders` an
  (`{name, status, originator}`) — nur mit `state` (Display), ohne bleibt
  `orders` leer (Host-Snapshot/Druckdialog). `display_view` reicht `state` durch
  und liefert `printed_originator` (im `_last_printed`-Memory mitgeführt) sowie
  `originator_info` pro `waiting_list`-Eintrag. Die flachen `printing_name`/
  `spooled_names`/`blocked_names` für den Host bleiben erhalten.
- Frontend: `orderBox` bekommt eine dritte Grid-Spalte (`4em 1fr auto`),
  `.dd-origin` rechtsbündig + dezent (muted, kleiner); SVGs `ICO_HELPER`/
  `ICO_LAPTOP`. `renderQueue` nutzt `p.orders` (strukturiert) statt der flachen
  Namen und `w.originator_info` für die Warteschlange.
- Tests: `test_originator_info_helper_host_student`,
  `test_pool_printers_orders_carry_originator`,
  `test_display_view_waiting_list_has_originator_info`. 322 passed, ruff clean.

## 2026-08-03 — Drucker-Display: allgemeine Warteschlange unter den Druckern

- **Warteschlangen-Kasten unter den Druckern:** das Display zeigt unter den
  Drucker-Karten einen vollbreiten Kasten „Warteschlange (N)" mit den
  zentralen Druckaufträgen, die für die oben gezeigten Drucker freigegeben
  sind (serverseitig via `display_view` gefiltert — `waiting_list` war bereits
  im Payload, wurde clientseitig bisher ignoriert). Einträge als Klasse +
  Name-Kästchen wie die Druckeraufträge; FLIP-Schlüsselpräfix `queue::`, sodass
  auch die Warteschlange die Positionsanimation übernimmt. Leere Warteschlange
  → Hinweis „Keine Aufträge in der Warteschlange."
- Keine Backend-Änderung (Daten waren schon da); nur `web/drucker-display.js`
  + `.html`. 319 passed, ruff clean.

## 2026-08-03 — Drucker-Display: Drucker pre-Einschalten, Verbindungspunkt, Token-QR

- **Drucker schon vor dem Einschalten zuordnen:** `/api/drucker-display/assign`
  erfordert nicht mehr `authorized` — der Host kann die Drucker-Boxen (Boxen +
  „+"-Popover, Drag-Reihenfolge) im unautorisierten Panel einrichten, bevor das
  Display per Namen-Eingabe freigeschaltet wird. Das unautorisierte Panel zeigt
  jetzt zusätzlich zu Code + Name + Einschalten die Drucker-Sektion.
- **Punkt = Verbindungsstatus:** der Statuspunkt am Display-Reiter ist grün,
  wenn ein Display mit diesem Token geöffnet ist (WS verbunden), sonst grau —
  nicht mehr abhängig vom Freigabe-Status. Tab-Titel „verbunden"/„nicht
  verbunden".
- **QR-Button pro Display (mit Token):** im autorisierten Panel hinter Name +
  Speichern ein „QR"-Button, der den QR-Code für die URL dieses Displays inkl.
  `?token=` anzeigt (`GET /api/drucker-display/qr?display_id=…`). Scannt ein
  Gerät ihn, öffnet sich dasselbe Display (Reload wiederverwendet die Session).
  `/qr` ohne `display_id` bleibt die Basis-URL für den „+"-Reiter.
- Tests: `test_assign_works_pre_authorization` (war 404, jetzt 200) + neue
  `test_qr_with_display_id_includes_token` / `test_qr_with_unknown_display_id_404`.
  319 passed, ruff clean.

## 2026-08-03 — Drucker-Display: Token-Persistenz, Namens-Freischaltung, Verboten, Styling

- **Token in der URL (Persistenz):** Öffnen von `/drucker-display` leitet per
  307 auf `/drucker-display?token=<12-hex>` weiter (eigene Route in
  `routes/drucker_display.py`, dafür aus `_CLEAN_PAGES` in `app.py` entfernt).
  Ein Reload liefert denselben Token → dieselbe Session wird wiederverwendet
  (gleicher Code, gleicher Freigabe-/Drucker-Stand), kein neuer Code pro Reload.
  Der WS-Handler (`routes/ws.py::ws_drucker_display`) validiert den Token und
  legt sonst eine neue Session mit `assigned_printer_ids=[]` an.
- **× am Host-Reiter = Display verbieten (endgültig):** `/api/drucker-display/
  forget` bannt den Token (`AppState.banned_printer_display_tokens`), entfernt
  die Session und sendet `{"type":"forbidden"}` ans Display (dann WS-close
  4009). Reload mit dem verbotenen Token bleibt gesperrt; ein frisch geöffnetes
  Display (neuer Token) ist erlaubt. Nicht reaktivierbar — Bestätigungsdialog
  am Host weist darauf hin.
- **Freischaltung per Namen statt Code:** Code-Eingabefeld am Host entfällt.
  Reiter zeigt den Registrierungs-Code (visuelle Zuordnung vor der
  Freischaltung); im unautorisierten Panel Name eingeben + „Einschalten" →
  `POST /api/drucker-display/enable` (`/authorize` + `authorizePrinterDisplay`
  entfernt). `/label`/`/assign`/`/theme` prüfen jetzt alle auf `authorized`.
- **Neue Displays starten ohne Drucker** (`assigned_printer_ids=[]`, nicht mehr
  `None` = alle); der Host weiht sie explizit ein.
- **Styling Drucker-Display:**
  - Gedruckte Aufträge **nicht mehr ausgegraut** (`.dd-order-printed` opacity
    entfernt, Klasse entfällt).
  - Schrift durchgehend größer: Druckername 2.4→3rem, Kategorie-Label
    1.2→1.5rem, Fehlerhinweis 1.6→2rem, Aufträge 1.25→1.6rem (Klassen-Spalte
    3.5em→4em).
  - **Theme folgt zu Beginn der System-/Browser-Einstellung** des Geräts
    (`prefers-color-scheme`), nicht mehr hardcodiert dark. `data-theme="dark"`
    Hardcode im `<html>` entfernt; JS setzt das System-Theme initial. Host-
    Override (`theme`-Nachricht) greift nur, wenn der Host es explizit setzt
    (`PrinterDisplaySession.theme` Default `None` → kein `theme`-Key im
    Update-Payload).
- **Display: `#view-forbidden`** („Dieses Display wurde vom Betreuer
  gesperrt.") — `forbidden`-Flag unterdrückt den automatischen Reconnect.
- Snapshot: `printer_displays` liefert jetzt zusätzlich `registration_code`
  (für den Reiter-Label vor der Namensgebung); `theme` kann `None` sein.
- Tests (`test_drucker_display.py`) auf enable/forget-bannt/registration_code/
  theme-None aktualisiert; `test_state_contract.py` unangetastet (Subset-only).
  317 passed, ruff clean.

## 2026-08-03 — Drucker-Display-Reiter: verwalten + schließen

- **Display-Reiter schließbar:** × am Reiter (mit Bestätigungsdialog) trennt
  das Display (`/api/drucker-display/forget`). Reiter-Label = Display-Name,
  sobald gesetzt, sonst Short-ID (Statuspunkt bleibt).
- **Verwaltung pro Display (im Reiter nach Pairing):**
  - **Name-Feld** — frei wählbarer Display-Name, erscheint als Überschrift auf
    dem Display (`/api/drucker-display/label`).
  - **Light/Dark-Schieberegler** — Darstellung auf dem Display
    (`/api/drucker-display/theme`); Display wendet `data-theme` an
    (`web/drucker-display.html` jetzt CSS-Variablen + `[data-theme="light"]`).
  - **Drucker-Boxen** statt Checkboxes: je zugewiesenen Drucker ein Kästchen
    (per Drag umsortierbar, × entfernt), ganz rechts eine „+"-Box zum Hinzufügen
    noch nicht zugewiesener Pool-Drucker (Popover).
- **Zuweisung geordnet:** `assigned_printer_ids` von Menge (`set`) auf
  geordnete Liste umgestellt — die Display-Reihenfolge bleibt erhalten
  (`server/print_queue.py::display_view` ordnet danach). Einmal explizit,
  immer explizit; nur der Default nach Pairing ist `None` = alle.
- Server: `PrinterDisplaySession` +`label`/`theme`;
  `printer_displays_snapshot` liefert geordnete Liste + Name + Theme.
  Tests (`test_drucker_display.py`, `test_print_queue.py`) angepasst + neue
  für Label/Theme/Reihenfolge. `test_state_contract.py` unangetastet
  (friert nur Top-Level-Keys + leere Display-Liste).

## 2026-08-03 — Drucker-Displays als Reiter in der Druckerwarteschlange

- **Host-UI:** die verbundene Drucker-Displays in der
  „Druckerwarteschlange"-Karte sind keine flache Liste mehr, sondern eigene
  **Reiter** innerhalb der Karte. Reiter 1 („Druckerwarteschlange") ist immer
  da und zeigt Pool + zentrale Warteschlange. Pro verbundenem Drucker-Display
  kommt ein weiterer Reiter hinzu (Short-ID + Statuspunkt: grau = unautorisiert,
  grün = autorisiert); beim ersten Öffnen steht dort die Code-Eingabe, danach
  die Drucker-Zuweisungs-Checkboxes. „+" am Tab-Ende öffnet den QR zum Verbinden
  eines neuen Displays (ersetzt den bisherigen „QR für Druckerdisplay"-Button).
- Rein client-seitig (`web/host.html`/`host-state.js`/`host-render.js`/
  `host.css`); Endpunkte, WS und `state_snapshot()`-Format unverändert
  (`tests/test_state_contract.py` bleibt grün). Reiter kommen/verschwinden
  automatisch mit dem Verbindungsstand (`state.printer_displays`).

## 2026-08-03 — Drucker-Display (`/drucker-display`) mit Pairing + Zuweisung

- **Neue anzeigbare Druckerwarteschlange** für einen Bildschirm neben den
  Druckern, aufrufbar über `https://<ip>:<port>/drucker-display` (Clean URL,
  kein Login). Vor dem Pairing zeigt das Display **nur eine 4-stellige
  Registrierungs-Nummer** — keine Schülerdaten. Erst nach Zuordnung durch den
  Host + Drucker-Zuweisung erscheint die Warteschlange (mit Schülernamen, wie
  im Host).
- **Pairing-Flow** (Muster vom iPad-Display `/ws/display` übernommen): neue
  Rolle `PrinterDisplaySession` (`server/state.py`) + WS `/ws/drucker-display`
  (`server/routes/ws.py`). Push bei Druck-Übergängen
  (`print_queue._notify_all` → `broadcast_printer_displays`) und Pool-
  Mutationen (`_after_pool_change`), kein Polling.
- **Host-Zuweisung:** pro Display legt der Host fest, *welche* Pool-Drucker
  dieses Display sieht (`assigned_printer_ids`: `None` = alle, explizite Menge =
  Teilmenge). Mehrere Displays mit je verschiedenen Druckern möglich, mehrere
  Drucker auf einem Display. Warteliste serverseitig gefiltert
  (`print_queue.display_view`): ein Eintrag ist relevant, wenn seine Allowlist
  die Display-Zuweisung schneidet. Endpunkte: `/api/drucker-display/{qr,
  authorize, assign, forget}` (Host-authentifiziert, `server/routes/
  drucker_display.py`).
- **Host-UI:** in der bestehenden „Druckerwarteschlange"-Karte neuer Button
  „QR für Druckerdisplay" + eine Drucker-Displays-Liste (Code-Eingabe vor
  Pairing, Zuweisungs-Checkboxes danach). `web/host-render.js::renderPrinterDisplays`.
- **Schnittstelle:** `state_snapshot()` hat den neuen Top-Level-Key
  `printer_displays` (Display-Liste für den Host); `print_queue.waiting_list`
  liefert zusätzlich `allowed_printer_ids` (IDs zur serverseitigen Filterung).
  Characterisierungs-Test `tests/test_state_contract.py` um den Key erweitert.
- **Tests:** `tests/test_drucker_display.py` (QR/Auth-Guard, Authorize/Assign/
  Forget, Snapshot-Shape, `send_printer_display_update`-Payloads),
  `tests/test_print_queue.py` ergänzt um `display_view`-Filterung + `allowed_
  printer_ids`. Suite grün (316 Tests), Ruff sauber.

## 2026-08-03 — Helfer-Statuszeile: aktiv vs. terminal + Reihenfolge

- **Neue Unterscheidung (aktiv vs. terminal):** Meldungen in den drei Zeilen
  (`wait`/`trans`/`print`) sind jetzt entweder **aktiv** (andauernder Vorgang,
  z.B. „Warten …", „wird gedruckt …", „wartet auf Druck …") oder **terminal**
  (End-/Bereit-Meldung: „Leihschein gedruckt.", „Druck fehlgeschlagen …",
  „Scanner bereit — Buch scannen", Countdown „Nächster Schüler in Xs").
  - Aktiv: persistiert, wird nur durch eine neue Meldung im **selben** Slot
    ersetzt — Meldungen anderer Slots verdrängen sie nicht.
  - Terminal: überschreibt die vorige Meldung im selben Slot, ist aber von
    **jeder neuen Meldung** (egal welcher Slot) überschreibbar.
- **Umsetzung:** `setStatusText(text, alertClass, slot, terminal=false)` mit
  einem `terminal`-Flag pro Slot (`statusTerminal`). Jede neue nicht-leere
  Meldung verdrängt terminale Meldungen in den ANDEREN Slots, aktive dort
  bleiben stehen. `clearStatus(slot)` leert gezielt eine Zeile (ohne Kaskade)
  für explizite Resets (neuer Schüler/idle).
- **Reihenfolge neu (oben → unten):** `#status-wait` (Worker-Status, jetzt
  oben) → `#status-trans` (Scan-Ergebnisse) → `#status-print` (Drucker).
  Zuvor war `wait` unten; die unterste Zeile ist nun nach oben gewandert.
- **Löst das Problem-Szenario:** Druck startet während der Worker lädt →
  `wait`=„Warten …" (aktiv) + `print`=Druckstatus (aktiv) gleichzeitig
  sichtbar; das späte `worker_ready` setzt `wait`=„Scanner bereit" (terminal)
  und verdrängt nur das terminale Ende, **nicht** den aktiven Druckstatus.
  Danach ist „Scanner bereit" selbst von jedem neuen Scan-Ergebnis
  überschreibbar; „Leihschein gedruckt." überschreibt die vorige Druckmeldung
  und ist ebenfalls von jeder neuen Meldung überschreibbar.
- Betrifft nur den Helfer-Client (Modus A); Schüler-Client (`student.*`)
  unverändert.

## 2026-08-03 — Helfer-Statuszeile: dritte Zeile für Drucker (Refinement)

- **Problem des Zwei-Zeilen-Modells:** Wurde ein Leihschein gedruckt während
  der Worker noch lud (sofort nach Aufrufen des Schülers), lief die Kette
  `Warten…` → (Druck startet) `Leihschein in Druckerwarteschlange …` →
  (`worker_ready`) `Scanner bereit — Buch scannen`, und das späte
  `worker_ready` hat den Druckstatus aus der `wait`-Zeile gelöscht. Der
  Nutzer-Wunsch „Druckstatus bleibt stehen" war so nicht erfüllt.
- **Lösung: dritte, eigene Zeile `#status-print`** (zwischen `#status-trans`
  und `#status-wait`). Jetzt drei gestapelte Zeilen (oben → unten):
  - **`#status-trans`** — flüchtig: Scan-Ergebnisse, „Gesendet", „Prüfe Scans
    …", „Schüler wird aufgerufen …", Peek-Status, Kamera-/Verbindungs-Hinweise.
    Wird bei jeder neuen Meldung überschrieben.
  - **`#status-print`** — persistent: alle Drucker-Meldungen (`print_progress`,
    `print_result`, „Leihschein in Druckerwarteschlange …") + Countdown
    „Nächster Schüler in Xs". Wird NUR durch eine neue Druckermeldung ersetzt.
  - **`#status-wait`** — persistent: Worker-/Helfer-Zustand („Warten …",
    „Warten bis Schüler frei …", „Scanner bereit — Buch scannen", idle-
    Warteschlangengröße). Wird nur durch eine neue `wait`-Meldung ersetzt.
- **Slot-Übergänge (neu):** `setStatusText(text, alertClass, slot)` mit
  `slot='trans'|'print'|'wait'`. Aufräumen nur der Zeilen, deren Inhalt
  hinfällig wird — **`worker_ready` („Scanner bereit") tastet `print` nicht
  mehr an** (Fix), ebenso little `sendPrint`/`print_progress`/`print_result`
  die `wait`-Zeile nicht. Ein neuer Schüler (`loading`/`student_info`) bzw.
  idle löscht `trans` + `print` (Druckstatus des vorigen Schülers); Druckbeginn
  löscht `trans`. So bleiben im Problem-Szenario „Warten …" und Druckstatus
  gleichzeitig sichtbar, und „Scanner bereit" verdrängt nur „Warten …".
- Leere Zeilen kollabieren (`.empty`); bei einer aktiven Zeile sieht es aus
  wie eine einzige. Alert-Farben gelten über `.status-line.status-alert-*`
  für alle drei Zeilen.

## 2026-08-03 — Helfer-Statuszeile: Warten-/Drucker-Meldungen bleiben stehen

- **Helfer-Client (`scan.html`, `scan-state.js`, `scan-ws.js`,
  `scan-render.js`):** Die Statuszeile besteht jetzt aus zwei gestapelten
  Zeilen statt einer:
  - **`#status-trans` (oben, flüchtig):** Scan-Ergebnisse, „Gesendet",
    „Prüfe Scans …", „Schüler wird aufgerufen …", Peek-Status, Kamera-/
    Verbindungs-Hinweise, „Scanner bereit — Buch scannen". Wird von jeder
    neuen flüchtigen Meldung überschrieben.
  - **`#status-wait` (unten, persistent):** „Warten…", „Warten bis Schüler
    frei…", Warteschlangengröße (idle) und alles Drucker-Bezogene
    (`print_progress`, `print_result`, „Leihschein in
    Druckerwarteschlange …", Countdown „Nächster Schüler in Xs"). Wird
    NUR durch eine neue `wait`-Meldung ersetzt — flüchtige Scan-Ergebnisse
    lassen sie stehen, damit der Helfer während eines laufenden Drucks
    sieht, worauf er wartet.
  - Leere Zeilen kollabieren (`.empty` → `display:none`); bei nur einer
    aktiven Zeile bleibt die Optik wie zuvor (eine Zeile, zentriert).
- **Slot-Übergänge:** `setStatusText(text, alertClass, slot)` mit
  `slot='trans'|'wait'` (Default `trans`); `clearStatus(slot)` leert eine
  Zeile. Zustandswechsel räumen die jeweils andere Zeile auf: „Scanner
    bereit" löscht `wait` (nicht mehr Warten), neuer Schüler/Warten/idle
  löscht `trans` (alter Scan-Ergebnis hinfällig), Druckbeginn
  (`sendPrint`) löscht `trans`. Drucker-Fortschritt (`print_progress`)
  berührt `trans` nicht → Scan-Ergebnis und Drucker-Status gleichzeitig
  sichtbar.
- **Alert-Farben:** `.status-line.status-alert-red/-orange/-book-issued`
  (statt `#status-text.…`) gelten für beide Zeilen.
- Betrifft nur den Helfer-Client (Modus A); Schüler-Client (`student.*`)
  unverändert.
> `docs/phase4_modus_b_2026-06-15.md`, `docs/hardening_2026-06-18.md`) und
> werden hier nur verlinkt, nicht dupliziert.

## 2026-08-02 — Druck-Status-Texte vereinheitlicht (Helfer + Host) + Countdown „Nächster Schüler"

- **Helfer-Client (`scan-ws.js`):** Druck-Status-Texte überarbeitet. Drucker
  jetzt inline als `<Druckername> (<Systemname>)` (`msg.printer_label`), kein
  „ von Drucker …"-Anhang mehr. Neu:
  - `printing` → „Leihschein wird von <pname> gedruckt…"
  - Position 0 (spooled) → „Leihschein wartet an <pname> auf Druck…"
  - Position 1 mit Drucker (Slot) → „Leihschein an 1. Druckerwarteschlangenposition von <pname>"
  - Position 1 ohne Drucker (zentraler Wartender) → „Leihschein an 1. Druckerwarteschlangenposition" (Drucker noch nicht zugewiesen)
  - Position ≥ 2 → „Leihschein an X. Druckerwarteschlangenposition" (kurz, ohne Drucker)
  - `gedruckt` → „Leihschein von <pname> gedruckt." (mit Abschlusspunkt)
- **Countdown „Nächster Schüler in Xs" (Helfer):** bei „Drucken & nächster
  Schüler" wird nach erfolgreichem Druck nicht mehr sofort weitergeschaltet,
  sondern ein 4-Sekunden-Countdown angezeigt (entspricht der Host-Toast-Dauer
  von 4000 ms). Tickt 4→3→2→1; die 0 wird nie gezeigt, weil bei Erreichen sofort
  `advanceToNext()` feuert. Generationenzähler schützt vor Doppel-Advance:
  ein manueller „Nächster Schüler"-Klick während des Countdowns cancelt den
  laufenden Timer (`cancelNextCountdown` am Anfang von `advanceToNext`).
  Konstante `NEXT_STUDENT_COUNTDOWN_S = 4` in `scan-render.js`.
- **Host-Client (`host-render.js`, `printToastText`):** gleiche Status-Texte,
  aber Präfix „Leihschein von <Name>, <Vorname> (<Klasse>)" (`msg.name`, bereits
  im Format „Nachname, Vorname (Form)" vom Server via `slip_name`). Kein
  „ — Drucker …"-Suffix mehr; Drucker inline. Abschlusspunkt bei „gedruckt.".
  Host ohne Auto-Advance → kein Countdown-Fall. peer_error/stalled/Fehler
  unverändert (Server-Text bzw. „— Druck fehlgeschlagen: …").
- **Keine Server-Änderung:** die nötigen WS-Felder (`printer_label`, `name`,
  `position`, `status`) waren bereits vorhanden. `test_state_contract.py`
  (friert `state_snapshot()` ein) unberührt.

## 2026-08-02 — Drucker-Anzeigename + vereinheitlichte Drucker-Anzeige + Einstellungs-Latenz + Cache-Busting

- **Anzeigename pro Pool-Drucker (`PrinterConfig.label`):** frei wählbarer,
  rein kosmetischer Name (`name` bleibt die technische Identität für
  `lp`/Sumatra-Dispatch + Dedup). Beim Hinzufügen angebbar, im Einstellungs-
  Panel unter „Name" bearbeitbar (Enter/Speichern-Button). Persistiert in
  `data/printers.json` (Roundtrip + Blank→`None`); `pool_printers` liefert
  `label` mit. Endpunkte: `POST /api/printers/add` nimmt optional `label`,
  neu `POST /api/printers/label` `{id, label}` (setzen/löschen).
- **Drucker überall als „Name (Systemname)" angezeigt:** `printerLabel(p)`
  im Host (Reiter, Panel, Warteschlangen-Tabelle, Klassen-Drucker-Checkboxen)
  zeigt `<Label> (<Systemname>)`; ohne Label nur der Systemname. Beim
  Standarddrucker mit Label steht nur „Standarddrucker" in der Klammer
  (keine verschachtelten Klammern), der Gerätename bleibt in der Panel-
  „System:"-Zeile sichtbar. Warteschlangen-Warteliste (`_printer_display`)
  und Druck-Toast/Scanner nutzen dasselbe Format über das neue WS-Feld
  `printer_label` in `print_progress`/`print_result` (`printer` als reiner
  Systemname bleibt erhalten — testgefriert + Dispatch).
- **Einstellungs-Reiter ohne lpstat-Verzögerung:** nach Pool-Mutationen
  (add/remove/duplex/label/reorder/reactivate) wurde bisher erneut
  `GET /api/printers` gerufen (serverseitig `lpstat`/`Get-Printer`) — der
  langsame Teil, der den Einstellungs-Dialog verzögerte. Neu: Geräte-Liste
  nur beim Öffnen des Dialogs holen (`refreshPrinterDeviceInfo`);
  Mutationen rendern direkt aus der Endpoint-Antwort `{ok, pool}`
  (`_applyPoolResponse`). Zusätzlich wird der Pool aus dem WS-Snapshot live
  nachgeführt (`maybeRefreshPrinterPoolFromSnapshot` in `applyState`), so
  dass add/remove im selben Moment auftaucht wie in der Warteschlange —
  geschützt gegen fokussierte Eingaben im Panel (kein Eingabe-Verlust).
- **Static-File-Cache-Busting:** statische Web-Dateien (host/scan/student
  JS/CSS/HTML) bekommen `Cache-Control: no-cache, must-revalidate`
  (HTTP-Middleware in `server/app.py`, nur für Nicht-`/api`-Pfade). Zuvor
  konnte der Browser `host-render.js` heuristic-cachen, sodass neue
  Einstellungs-Felder bei Host-Rechnern nicht ankamen. StaticFiles liefert
  `Last-Modified`, unveränderte Dateien antworten mit 304. Nach dem
  nächsten Server-Start einmalig Hard-Refresh nötig.
- **Tests:** `tests/test_printer_store.py` (+1: Label-Roundtrip +
  Blank→`None`; bestehender Roundtrip-Test um `label` erweitert).
  `tests/test_state_contract.py` unverändert (neuer `label`-Schlüssel bricht
  die Per-Printer-Checks nicht). **295 Tests grün**, Ruff clean.

## 2026-08-01 — Sicherheits-/Wartbarkeits-Audit umgesetzt

Repo-weites Audit mit drei GPT-5.6-Terra-Subagents, anschließender
adversarialer Kreuzprüfung und vollständiger Offline-Verifikation:

- **Buchungs-Sicherheitsgrenze geschlossen:** `/api/commit-book` läuft nicht
  mehr direkt auf `handle_commit`, sondern über dieselbe fail-closed
  Lager-/Vormerk-/Bereits-verliehen-Prüfung wie reguläre Scans. Ein
  studentenspezifischer Lock serialisiert Vorabprüfung, Playwright-Enter und
  State-Update.
- **Einheitlicher Teardown:** `clear-queue` und erzwungener Schuljahreswechsel
  verwenden `teardown_students()`/`end_student()`; Load-Tasks, Helper-State,
  Modus-B-Credentials und Worker-Contexts werden vollständig und genau einmal
  aufgeräumt.
- **Session-/WebSocket-Härtung:** erneutes Öffnen von Modus B widerruft alte
  Pending-Pairings; ersetzte Scanner-/Schüler-Sockets dürfen keine gepufferten
  Frames mehr ausführen; abgelaufene bzw. ausgeloggte Host-Sessions schließen
  ihre offenen WebSockets aktiv. Capability-Tokens erscheinen nicht mehr in
  Anwendungs- oder Uvicorn-Access-Logs.
- **App-lokaler Runtime-State:** `server/runtime.py` bindet je FastAPI-App eine
  eigene `AppState`-/`Hub`-Instanz über ContextVars; Produktion läuft explizit
  mit einem Uvicorn-Worker. Zwei `create_app()`-Instanzen sind testverifiziert
  isoliert.
- **Konfiguration/Pfade:** Port, Workerzahl, TTLs, Slow-Mo, Print-Backend und
  TLS-Pfade werden beim Start validiert; TLS-, Log-, Persistenz- und
  Print-Ausgabepfade hängen nicht mehr vom Aufruf-CWD ab.
- **Test-Infrastruktur:** Starlette-TestClient auf `httpx2` migriert;
  Cookie-/Import-Deprecations beseitigt. Ergebnis: **294 Tests grün**, Ruff
  clean, komplette Suite mit `-W error` ohne Warnungen; kein IServ-Write und
  keine echte Buchung ausgeführt.

Offen bleibt bewusst der Schul-WLAN-/Echtgeräte-Smoke mit `ALLOW_BOOKING=false`
(QR, Zertifikatswarnung, Scanner-/Modus-B-Reconnect); Unit-Tests können Routing,
Firewall und Zertifikatsannahme auf den Schulhandys nicht beweisen.

## 2026-07-20 — Statusanzeige ergänzt „von Drucker <Name>" nach Versand

Sobald ein Auftrag an einen Drucker gesendet wurde, zeigt die Statusanzeige
des Auftraggebers den Druckername — damit er weiß, welcher Drucker seinen
Leihschein druckt. Änderung in `server/print_queue.py` + `web/scan-ws.js` +
`web/host-render.js`:

- **`print_progress`** trägt bereits das `printer`-Feld (Druckername); der
  Helfer-Client (`scan-ws.js`) hängt für nicht-`peer_error`-Status jetzt
  „ von Drucker <Name>" an den Statustext an (z. B. „Wird gedruckt … von
  Drucker P1", „Leihschein gesendet, wartet auf Druck … von Drucker P1",
  „Leihschein an 1. Druckerwarteschlangenposition von Drucker P1"). Der Host-
  Toast (`printToastText` in `host-render.js`) hängt analog „ — Drucker
  <Name>" an (z. B. „Leihschein von A wird gedruckt — Drucker P1", „… gedruckt
  — Drucker P1"). Zentrale-Warteschlangen-Jobs ohne zugewiesenen Drucker
  (`printer` null) bekommen keinen Zusatz. Für `peer_error`/`stalled` übernimmt
  der Server den Text (Pos.-0-Hinweis nennt den Drucker bereits) — kein Zusatz.
- **`print_result`** bekommt neu ein `printer`-Feld (aus `assigned_printer_id`
  via `_printer_name`); der Helfer zeigt bei Erfolg „Gedruckt von Drucker
  <Name>", der Host-Toast „… gedruckt — Drucker <Name>" (statt nur „gedruckt").

Tests: `tests/test_print_queue.py` (+1: `print_progress` + `print_result`
tragen `printer` für gesendete Aufträge). Suite grün, Ruff clean.

## 2026-07-20 — Fehlermeldung positionsbasiert: nur Pos. 0 bekommt den Inaktivitäts-Hinweis

Aufbauend auf der „keine +1-Hochzählung"-Änderung (s. u.): Die Fehlermeldung am
hängenden Drucker ist jetzt **positionsbasiert**, nicht mehr nach Urheber/Peer
getrennt. Neu in `server/print_queue.py` (`_error_msg`, verwendet in
`_handle_stall` + `_notify_all`):

- **Pos. 0 (der vorn am Drucker druckende/gesendete Auftrag)** bekommt den
  Inaktivitäts-Hinweis „Es dauert ungewöhnlich lange, vielleicht liegt ein
  Fehler vor. Bitte überprüfe dies. - Drucker <Name>: <Label>" (Label aus
  `_stall_label`: „wird gedruckt"/„gesendet, wartet auf Druck").
- **Pos. ≥ 1 (die dahinter wartenden Aufträge)** bekommt die gewohnte Peer-
  Formulierung „Fehler bei vorigem Druckauftrag - X. Warteschlangenposition"
  (X = Position, ohne +1) zurück — nur der **erste** Auftrag zeigt also
  „es dauert ungewöhnlich lange", alle weiteren die Peer-Formulierung.
- **Ersatzdrucker-Aufträge** bekommen weiterhin keine Fehlermeldung, sobald
  ein erlaubter Drucker nicht fehlerhaft ist (s. `_is_peer_error`/Slot-
  Markierung) — nur wenn *alle* erlaubten Drucker fehlerhaft sind, greift die
  obige Regel.

Tests: `tests/test_print_queue.py` (+1: `_error_msg` positionsbasiert);
`test_stall_peer_at_same_printer_gets_peer_error` und
`test_stall_peer_at_position_zero_shows_pos_zero_label` auf die neue
Pos.≥1-Formulierung umgestellt. Suite grün, Ruff clean.

## 2026-07-20 — Stall-/Peer-Meldung: Positionsnummer stabil (keine +1-Hochzählung)

Bisher zeigte die Peer-Error-Meldung „Fehler bei vorigem Auftrag -
(Position+1). Warteschlangenposition" — der vordere Job (Slot 0, bisher
„wird gedruckt"/„gesendet") rutschte beim Fehler seines Hintermannes auf
„1. Warteschlangenposition" hoch, und alle folgenden Jobs wurden eine
Position hochgezählt. Gewünschtes Verhalten: die Positionsnummer eines
Auftrags bleibt beim Fehler stabil (der an Pos. 0 war, bleibt an 0). Umbau
in `server/print_queue.py` + `web/scan-ws.js`/`web/host-render.js`:

- **Einheitliche Inaktivitäts-Meldung:** Urheber (`stalled`) und Peers
  (`peer_error`) zeigen beide „Es dauert ungewöhnlich lange, vielleicht
  liegt ein Fehler vor. Bitte überprüfe dies. - Drucker <Name>: <Label>"
  (vorher: Peers „Fehler bei vorigem Auftrag …"). Das Label ist das normale
  Warteschlangen-Label des Auftrags, gebaut aus Position + Pre-Error-Status
  (`_stall_label`/`_stall_msg`): Pos. 0 + `printing` → „wird gedruckt",
  Pos. 0 + `spooled` → „gesendet, wartet auf Druck" (keine Nummer), Pos. ≥ 1
  → „an X. Druckerwarteschlangenposition" (X = Position, **ohne +1**). So
  bleibt der an Pos. 0 wartende/druckende Job an 0 statt auf 1 hochgezählt.
- **Server liefert den Text, der Client zeigt ihn nur:** `print_progress`
  bekommt für `peer_error`-Jobs ein `msg`-Feld (gebaut in `_notify_all` für
  zentrale No-Alternative-Jobs via `_peer_pname`; Slot-Peers erhalten ihr
  `msg` über `print_result`). `scan-ws.js`/`host-render.js` zeigen für
  `peer_error` nur noch `msg.msg` statt die Position selbst +1 hochzuzählen.
- **Zentrale No-Alternative-Jobs** bekommen die gleiche Inaktivitäts-Meldung
  (vorher „Fehler bei vorigem Auftrag - (Pos+1). Warteschlangenposition") —
  Positionsnummer ohne +1.

Tests: `tests/test_print_queue.py` (+3: `_stall_label` ohne +1, `_stall_msg`
mit Label, Pos.-0-Peer zeigt Pos.-0-Label statt „1."); bestehende Peer-Test-
Assertion auf die neue Meldung umgestellt. Suite grün, Ruff clean.

## 2026-07-20 — Stall-Aufträge im Drucker-Slot belassen (für Warteschlange mitzählen)

Bisher hat `_handle_stall` den Urheber-Auftrag (`stalled`) und alle Peer-Aufträge
am selben Drucker (`peer_error`) **aus dem Slot entfernt** — die Last des
hängenden Druckers fiel damit auf 0, und die Warteschlangen-Positionen der
noch wartenden Aufträge stürzten ein (No-Alternative-Jobs sprangen auf Pos. 0,
obwohl blockierte Aufträge vor ihnen standen). Gewünschtes Verhalten: die
fehlgeschlagenen Aufträge sollen im Slot bleiben und für die Warteschlange
weiter mitzählen — nur so bleiben die Positions-Nummern stabil. Umbau in
`server/print_queue.py` + `web/host-render.js`/`web/host.css`:

- **Im Slot belassen, mitzählen:** `_handle_stall` entfernt Urheber und Peers
  nicht mehr aus `slots[pid].jobs`; sie behalten ihren Status (`stalled`/
  `peer_error`) und ihre Slot-Position (Urheber vorne, Peers dahinter). Die
  Last des Druckers bleibt unverändert, und `_compute_positions` zählt sie für
  No-Alternative-Jobs (Allowlist nur auf den fehlerhaften Drucker) als
  vordrängelnde Aufträge mit — deren Position bleibt hinter den Blockierten.
  Peers werden im Peer-Durchlauf übersprungen, damit der Urheber nicht
  versehentlich als Peer doppelt finalisiert wird.
- **Reaktivierung räumt den Slot:** `reactivate()` nimmt nur die
  fehlerhaft-Marke zurück; der folgende `_reconcile`-Lauf (unter Lock, raced
  nicht gegen `_claim_fills`) räumt für nicht-fehlerhafte Drucker die
  blockierten Aufträge aus dem Slot und gibt so die Kapazität frei — der
  Scheduler dispatcht danach wieder dorthin. Fehlerhafte Drucker behalten
  ihre Blockierten bis zur Reaktivierung.
- **Ersatzdrucker-Position unverändert:** Aufträge mit Ersatzdrucker
  (Allowlist umfasst auch andere Drucker) bekommen weiterhin die reduzierte
  Position ohne den fehlerhaften Drucker (`_compute_positions` schließt
  fehlerhafte Drucker für `usable` aus) — sie springen auf die freie Position
  des Ersatzdruckers, anstatt hinter die Blockierten zu warten.
- **Anzeige (Host-Druckerwarteschlangen-Box):** `pool_printers` zählt
  blockierte Aufträge in `load` mit, listet sie aber nicht als
  druckend/wartend (`printing_name`/`spooled_names` bleiben leer). Die Box
  zeigt fehlerhafte Drucker mit rotem Punkt + „⚠ fehlerhaft — N blockiert";
  ein kurz nach der Reaktivierung belegter, aber aktiver-freier Slot zeigt
  „N blockiert (wird geräumt)" (Transient bis zum nächsten Scheduler-Schritt).
  `.pq-dot.pq-fault` in `web/host.css`.
- **`_notify_all`** überspringt blockierte Slot-Aufträge (kein Progress-Push
  für finalisierte Jobs — sie haben ihr `print_result` bereits).

Tests: `tests/test_print_queue.py` um drei Fälle erweitert
(No-Alternative-mitzählen, `pool_printers`-Load-vs-Anzeige, `_reconcile`-
Räumung nach Reaktivierung); die bestehenden Stall-/Peer-Tests asserten
zusätzlich die verbleibende Slot-Belegung. Suite grün, Ruff clean.

## 2026-07-19 — Hängender Drucker: Inaktivitäts-Fehler, Peer-Benachrichtigung, Label-Korrektur

Bisher wertete der Print-Queue-Tracker das OS-Polling nach 90 s still als
„gedruckt" — ein hängender Drucker führte also dazu, dass der Helfer nach
Ablauf automatisch den **nächsten Schüler aufrief**, ohne dass jemand den
Fehler bemerkte. Zudem zeigte der Client „Wird gedruckt" bereits an Slot-
Position 0 (statt erst bei aktivem OS-Druck), und die „gesendet, wartet auf
Druck"-Meldung erschien an der Stelle, die „Pos. 1" heißen sollte. Umbau in
`server/print_queue.py` + `server/routes/slips.py` + Web-Clients:

- **Inaktivitäts-Fehler statt stillfertig:** neuer `_INACTIVITY_TIMEOUT_S`
  (30 s) im Tracker — bleibt der OS-Status (`spooled`/`printing`) länger ohne
  Wechsel, gilt der Drucker als hängend. Der Urheber bekommt
  `print_result{ok:false, stalled:true}` mit „Es dauert ungewöhnlich lange … -
  <aktueller Status>", der Helfer rückt **nicht** zum nächsten Schüler vor
  (`printThenNext` advanced nur bei `ok:true`). Absolut-Cap `_TRACK_TIMEOUT_S`
  bleibt als „wird als fertig gewertet"-Backstop für langsame (nicht hängende)
  Drucker.
- **Peer-Benachrichtigung am selben Drucker:** beim ersten Stall werden alle
  **anderen** Aufträge im fehlerhaften Drucker-Slot als `peer_error`
  finalisiert (`ok:false, peer_error:true`, „Fehler bei vorigem Auftrag -
  <Position>") und deren Tracker cancelt. Zentrale-Warteschlangen-Jobs **ohne
  Ersatzdrucker** bekommen `peer_error` via `_notify_all` und bleiben stehen,
  bis der Drucker reaktiviert wird.
- **Ersatzdrucker-Positionen:** `_compute_positions(printers, faulty_ids=)`
  zählt den fehlerhaften Drucker für Aufträge mit Ersatzdrucker **nicht** mit
  (reduzierte Position); No-Alternative-Jobs bekommen ihre Position relativ
  zum fehlerhaften Drucker. `_claim_fills` überspringt fehlerhafte Drucker
  (keine neuen Dispatches dorthin).
- **„Wieder aktivieren":** fehlerhafte Drucker werden im Pool-Snapshot als
  `faulty:true` markiert; die Host-Einstellungen zeigen einen „Wieder
  aktivieren"-Button + ⚠-Marker am Reiter. `POST /api/printers/reactivate`
  (`PrinterReactivateRequest`) nimmt die Marke zurück und weckt den Scheduler.
  Entfernen/Verwaisten-Lauf des Druckers räumt die Marke ebenfalls ab.
- **Label-Korrektur (scan-ws + host-render):** „Wird gedruckt" nur noch bei
  OS-Status `printing`; Slot-Pos. 0 + `spooled` → „Leihschein gesendet, wartet
  auf Druck …"; Pos. ≥ 1 → „an X. Druckerwarteschlangenposition" (vorher
  zeigte Pos. 1 fälschlich „gesendet, wartet auf Druck"). `peer_error`/
  `stalled`-Toasts am Host werden nicht auto-dismissed.

Tests: `tests/test_print_queue.py` um fünf Fälle erweitert (Stall-Urheber,
Peer am selben Drucker, Ersatzdrucker-Position, No-Alternative-`peer_error`,
Reactivate); `tests/test_state_contract.py` sichert `faulty:false` im
Erststart-Snapshot. Suite grün, Ruff clean.

## 2026-07-19 — Parallele Drucker-Verteilung + OS-echter Druckstatus + Positionen

Am Live-Gerät (Windows/SumatraPDF) funktionierte die Verteilung trotz grüner
Unit-Logik nicht wie entworfen — drei Wurzelursachen, behoben in einem Umbau
der `server/print_queue.py` + `server/printing.py`:

- **„Nur ein Drucker wurde genutzt":** der Worker war eine serielle Schleife,
  die in `asyncio.gather` über alle Completion-Polls blockte. Während ein
  Druck auf Drucker A pollte, konnte kein neuer Auftrag an einen anderen
  idle-Drucker dispatcht werden. **Fix:** jeder gesendete Auftrag läuft in
  einem eigenen Hintergrund-Task (`_track_job`); der Worker dispatcht
  nicht-blockierend und schläft, bis ein finalisierter Tracker ihn weckt. So
  drucken mehrere Drucker wirklich parallel.
- **„Wird gedruckt" wurde zu früh gezeigt:** `_promote_all` hatte den Status
  rein logisch gesetzt, sobald der Slot frei wurde. **Fix:** Status ist jetzt
  OS-getrieben — `printing.read_job_state` pollt den echten Druckstatus (Windows
  `Get-PrintJob.JobStatus`, CUPS `lpstat` „active"); `spooled` = an OS gesendet
  (wartet), `printing` = OS druckt aktiv, `done` = OS-Job weg. Logische
  Slot-Beförderung entfällt.
- **Positionen waren falsch:** zentrale-Warteschlangen-Jobs bekamen ihren
  globalen Index. **Fix:** `_compute_positions` liefert je Job das Minimum
  über alle *erlaubten* Drucker, wie viele Aufträge dort noch vor ihm liegen
  (0 = druckt, 1 = gesendet/wartet, 2 = erster zentraler Wartender bei vollem
  Drucker). Host- und Scanner-UI zeigen die 0-basierte Position; „gesendet,
  wartet auf Druck" für Pos 1.

Änderungen im Detail:

- **`server/print_queue.py`** — Rewrite auf Tracker-Architektur: `_Slots.jobs`
  (FIFO, max 2) statt `printing`/`spooled`-Slots; `_claim_fills` liefert
  3-Tupel (kein `slot_type`); `_step` nicht-blockierend + `_spawn_tracker`;
  `_track_job` (Dispatch → `spooled` → OS-Poll-Loop → `done`/`failed`); neu
  `_compute_positions` (min über erlaubte Drucker); `_remove_from_slot`;
  `pool_printers` liefert zusätzlich `spooled_names` (Liste aller gesendeten
  Nicht-Druck-Jobs); `waiting_list.position` = berechnetes Minimum.
- **`server/printing.py`** — neu `read_job_state(handle) -> absent|spooled|
  printing` (single shot) + `_read_cups_job_state` / `_read_win_job_state`;
  `await_print_completion` bleibt als Kompatibilitätssymbol (intern ungenutzt).
  Windows-Standarddrucker wird einmal aufgelöst und am Handle gecacht.
- **`web/host-render.js`** — `renderPrintQueue` unterscheidet „druckt" (OS-
  aktiv) vs „gesendet, wartet auf Druck"; `#` der zentralen Queue = 0-basierte
  Position; `buildPrinterPanel` entsprechend. `printToastText` Pos-1-Text.
- **`web/scan-ws.js`** — Pos 0 = „Wird gedruckt", Pos 1 = „gesendet, wartet
  auf Druck", Pos ≥2 = „an X. Druckerwarteschlangenposition".
- **Tests:** `tests/test_print_queue.py` Worker-Tests auf `read_job_state`-
  Mock umgestellt + 1 neuer parallele-2-Drucker-Test (beide Drucker
  gleichzeitig belegt, nicht serialisiert); `tests/test_printing.py` +3
  (`read_job_state` None/cups/win). Suite 261 → 266 grün.
- **Draht-Format** (`test_state_contract.py`): Top-Level-Keys + Default-
  Drucker-Form unverändert; `printers[].spooled_names` zusätzlich (abwärts-
  kompatibel, nicht vertraglich eingefroren).

**Offen:** Live-Verifikation am echten Windows-Gerät (≥ 2 Drucker) — parallele
Verteilung, OS-Status-Übergänge spooled→printing→done, Position 0/1/2 — nur
nach Freigabe nach CLAUDE.md §6 mit ausgemusterten Büchern + Rückbau-Plan.

## 2026-07-19 — Pro-Klasse Drucker-Allowlist + pool-gerechte Verteilung

Bisher druckte die interne Warteschlange jeden Auftrag auf *irgendeinen*
Pool-Drucker (niedrigste Last, linkester Tie-Break) — ohne Bindung
Auftrag→Drucker. Neu: pro Klasse wählbare **Drucker-Allowlist** und eine
Verteilung, die sie respektiert (Nutzer-Vorgabe).

- **UI:** Im „Neue Klasse öffnen"-Menü (dort wo schon die Auto-Fertig-Filter
  stehen) eine Checkbox-Liste der Pool-Drucker; angehakte Drucker sind für
  diese Klasse erlaubt. **Nachträglich änderbar** im Klassen-Reiter (eigene
  „Drucker für {Klasse}"-Karte). Leere Auswahl = **alle** Pool-Drucker
  (Default, kompatibel mit Test-Config / Öffnen ohne Auswahl). Auswahl im
  panel-new wird für das nächste Öffnen gemerkt (`localStorage`).
- **State:** `ClassContext.allowed_printer_ids: set[str] | None` (`None` =
  alle). `OpenClassRequest.printers` setzt sie beim Öffnen (auch beim
  erneuten Öffnen desselben Kontexts = Aktualisierung). Neuer Endpoint
  `POST /api/context-printers` ändert sie an der laufenden Klasse + weckt
  den Scheduler. Reiner In-Memory-State (Kontexte nicht persistiert), kein
  DB-/IServ-Zugriff. Im `state_snapshot` führt jeder Kontext nun
  `allowed_printers` (`null` | sortierte ID-Liste).
- **Auftrag:** `PrintJob.allowed_printers` snapshottet die Klassen-Allowlist
  zum Enqueue-Zeitpunkt — bereits wartende Aufträge behalten ihre Allowlist,
  auch wenn die Klasse später umkonfiguriert wird (gewollt: „mit in der
  Warteschlange gespeichert"). `slips.py::print_loan_slip` und
  `ws.py::_handle_print` kopieren sie beim Enqueue (`sessions.
  allowed_printers_for`). Explizite Allowlist ohne Treffer im Pool → Druck
  wird vorab verweigert (HTTP 400 / WS `print_result ok:false`).
- **Scheduler (`print_queue._claim_fills`)** neu **level-weise + allowlist-
  gerecht**: erst alle idle-Drucker (Last 0) einen Auftrag bekommen, dann
  Last-1-Drucker — damit bekommt **kein Drucker einen 2. Auftrag (Slot 1)**,
  solange ein anderer *erlaubter* Drucker noch idle ist (Parallelismus
  statt nacheinander). Pro Level picken die Drucker in der konfigurierten
  Reihenfolge (linkester zuerst); jeder zieht den ranghöchsten Auftrag, der
  ihn erlaubt (`None` = alle, sonst ID darin). Ist der Kopf der Warteschlange
  für mehrere freie Drucker erlaubt, druckt der linkeste. `waiting` bleibt
  rollen-gerecht geordnet. Verhalten bei `allowed_printers=None` identisch
  zum bisherigen (bestehende Pool-Tests grün).
- **Tests:** `tests/test_print_queue.py` +5 (Allowlist-Verteilung: nur-p2,
  linkester-bei-mehrfach-erlaubt, idle-Vorrang-gegen-2.-Slot, Kopf-überspringen-
  wenn-nicht-erlaubt, leere-Menge-bleibt-waiting). `tests/test_state_contract.py`
  unangetastet (Top-Level-Keys + Default-Drucker-Form identisch). Suite 261 grün.
- **Offen:** Live-Verifikation am echten Drucker (read-only Druck mit
  `save_pdf_locally`-Sicherheitsnetz möglich). Modus-B-Schüler-Druck hat keine
  UI, der Allowlist-Pfad greift aber bereits.

## 2026-07-19 — Drucker-Pool in den Einstellungen (Reiter, Round-Robin-Verteilung, Duplex, Persistenz)

Bisher war in den Einstellungen ein **einzelner** Leihschein-Drucker
wählbar (`printer_name_override` + `POST /api/printer`). Neu: eine
**Drucker-Pool-Verwaltung** in den Einstellungen, analog den Klassen-Reitern.

- **Reiter pro Drucker** (wie Klassen-Tabs): Drucker hinzufügen/entfernen, per
  HTML5-Drag umsortieren. Der Standarddrucker des Geräts ist **nur zu Anfang**
  als erster Reiter vorhanden — er ist entfernbar wie jeder andere; der Pool
  darf **leer** sein (dann Druck nicht möglich, s. u.). Kein Drucker ist
  besonders gesperrt.
- **Round-Robin-Verteilung** auf den Pool: der linkeste Drucker mit der
  niedrigsten Last nimmt an, dann der nächste usw. — pro Drucker Kapazität 2
  (1 druckend + 1 gespoolt). Sind alle Drucker voll (Last 2), warten weitere
  Aufträge zentral, bis ein Drucker Kapazität hat (klassische Füllung: erst
  alle auf Last 1, dann auf Last 2). Umsetzung als „niedrigste Last, linkester
  Tie-Break".
- **Duplex pro Drucker** per Dropdown (einseitig / doppelseitig lange Seite /
  doppelseitig kurze Seite) — **nur speichern, nirgends anwenden** (Nutzer-
  entscheid; Backends können Duplex CLI-seitig ohnehin nicht zuverlässig).
- **Persistenz** in `data/printers.json` (Spiegel von `booklist_store.py`):
  Beim Laden wird jeder gespeicherte *benannte* Drucker gegen die Geräte-
  Druckerliste (`list_printers`) geprüft; fehlt er, wird er nicht geladen
  **und** aus der JSON gelöscht. Der `name=null`-Eintrag (Standarddrucker)
  gilt als immer gültig. Erster Start (keine JSON) → `[Standarddrucker]`;
  danach gilt der gespeicherte Stand (auch `[]`). IDs werden nicht persistiert
  (nur Laufzeit-stabil für Slot-Zuordnung/Endpoint-Bezug).
- **Schüler-Leihschein-Auswahl** rückt **über** die Drucker-Reiter (global,
  nicht pro Drucker).
- **Leerer Pool:** der Scheduler dispatcht nichts — Aufträge bleiben in der
  zentralen Warteschlange. Die Enqueue-Stellen (`/api/print-loan-slip`,
  Scanner-WS `print`) verweigern vorab mit „Kein Drucker konfiguriert" (400
  bzw. `print_result{ok:false}`), damit kein Auftrag endlos wartet. Sobald ein
  Drucker hinzugefügt wird, weckt das den Scheduler (`wake`) und die wartenden
  Aufträge verteilen sich.
- **Draht-Format-Änderung** (bewusst): `state_snapshot()` liefert jetzt
  `printers` (Liste von `{id, name, duplex, is_default, load, printing_name,
  spooled_name}`) + `print_queue_summary {waiting}` statt des alten
  `printer_name`. `tests/test_state_contract.py` wurde mitgeführt
  (Schemaänderung, keine unbeabsichtigte Drift).

Dateien: neu `server/printer_store.py`, `tests/test_printer_store.py`;
geändert `server/state.py` (`PrinterConfig`, `RuntimeSettings.printers`,
`state_snapshot`), `server/print_queue.py` (Pool-Scheduler-Rewrite: zentrale
`waiting` + `slots` dict pro Drucker, 2-Slots-Pipeline, Round-Robin-Füllung,
`wake`), `server/app.py` (lifespan lädt Pool), `server/sessions.py`
(`print_loan_slip_for(printer_name=…)`), `server/routes/slips.py`
(`/api/printers` erweitert, `/api/printer` entfernt, neue Pool-Endpoints
`add`/`remove`/`duplex`/`reorder`), `server/routes/_deps.py` (neue Request-
Models), `server/routes/ws.py` (Leer-Pool-Gate), `web/host.html`,
`web/host-render.js`, `web/host.css` (Reiter + Panels + Duplex-Dropdown +
Drag-Reorder). Tests: `tests/test_print_queue.py` (Pool-Round-Robin, leerer
Pool, Snapshot-Form), `tests/test_state_contract.py` (Snapshot-Schema),
`tests/test_printer_store.py` (Roundtrip, Drop fehlender Drucker, erster
Start, unbekannter Duplex). Verifikation: `256 passed`, `ruff check` sauber.
Offen: manueller Smoke mit `lp`-Backend auf CUPS-Rechner + ausgemusterten
Büchern (nur nach Freigabe nach CLAUDE.md §6) — Verteilung auf mehrere Drucker.

## 2026-07-19 — Interne Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight, Live-Status)

Bisher war der Leihschein-Druck Fire-and-Forget: `print_pdf` schickt das PDF
an `lp`/`SumatraPDF` und kehrt zurück, sobald der **OS-Spooler** den Auftrag
angenommen hat — mehrere Aufrufer feuerten nebeneinander, ohne Serialisierung
am Drucker und ohne Rangfolge zwischen Host/Helfer/Schüler. Neu: eine
**server-interne Warteschlange** (`server/print_queue.py`) serialisiert alle
Drucke und ordnet sie rollen-gerecht (HOST > HELFER > SCHÜLER).

- **2-in-flight-Pipeline:** Position 0 druckt gerade, Position 1 ist schon an
  den Drucker gespoolt (wartet), Position 2+ ist intern noch nicht gesendet.
  Der nächste Auftrag geht bereits ans OS, bevor der vorige physisch fertig ist
  — der Drucker bleibt ohne Lücke beschäftigt (Geschwindigkeit vor strikter
  Rangfolge). Bewusster Trade-off: ein späterer HOST-Auftrag reiht sich hinter
  einen *bereits gespoolten* niedrigerrangigen Auftrag (am OS nicht umsortierbar);
  alle intern Wartenden bleiben aber rollen-gerecht geordnet.
- **„gedruckt" = auf Papier fertig**, erkannt per OS-Queue-Polling
  (`printing.await_print_completion`): CUPS `lpstat` bis die Job-ID
  verschwindet, Windows `Get-PrintJob` per DocumentName-Match; `file`- und
  `win-default`-Backend ohne Polling (sofort „gedruckt", dokumentierter
  Fallback). Timeout-Fallback 90 s, damit die Queue nie blockiert.
- **Helfer-Statuszeile** zeigt live `Leihschein an X. Druckerwarteschlangen-
  position` → `Wird gedruckt …` → `Gedruckt` (neue WS-Nachricht `print_progress`
  + vorhandenes `print_result`). „Drucken & nächster Schüler" lädt den neuen
  Schüler erst nach „Gedruckt"; bei Fehler bleibt der Schüler stehen
  (`Druck fehlgeschlagen: …`).
- **Host-Popup** unten rechts, persistenter Toast (keyed per `job_id`):
  `Leihschein von <Nachname>, <Vorname> (<Klasse ohne „Klasse "-Präfix>) an X.
  Druckerwarteschlangenposition` → `… wird gedruckt` → `… gedruckt`, auto-
  dismiss 4 s nach fertig. Erscheint **nur am startenden Host** — neu
  `state.host_ws_by_sid` (sid→WS-Map, da HTTP und WS denselben `session_id`-
  Cookie teilen).
- `print_pdf` liefert ein `job_handle` im Result (CUPS-Job-ID bzw. SumatraPDF-
  DocumentName); `/api/print-loan-slip` enqueued + blockiert bis fertig
  (HTTP als Rückversicherung, falls der WS nicht live ist).

Dateien: neu `server/print_queue.py`; geändert `server/printing.py`,
`server/state.py`, `server/app.py`, `server/routes/ws.py`, `server/routes/
slips.py`, `web/scan-ws.js`, `web/scan-render.js`, `web/host-ws.js`,
`web/host-render.js`. Tests: `tests/test_print_queue.py` (Einfüge-Logik,
2-in-flight, Positionen, Fehler-Fall), `tests/test_printing.py` (job_handle,
Completion-None). Offen: Schüler-Druck (Modus B) als `role="student"` ist
angelegt, aber ohne UI; Rangfolgen-Schlupf bei 2-in-flight dokumentiert.

## 2026-07-18 — Helfer-Queue-Info-Icons: Feinschliff (Kästchen, Drucker/X-Y exklusiv)

Zwei Nachschärf-Runden auf den vorigen Eintrag (Helfer-Queue-Info-Icons):

- **Druckersymbol vereinheitlicht:** Strichstärke von 2 auf 2.5 angehoben —
  war als einziges der Info-Icons noch dünn, wirkte dadurch kleiner als die
  Antrags-/Buch-Icons, obwohl `.q-ico` (Breite/Höhe) schon identisch war.
- **X/Y bekommt dieselbe Schriftgröße wie der Name:** Klasse `q-info-amount`
  (bisher nur für den offenen Betrag) jetzt auch auf dem X/Y-Text — beide
  damit `.9rem` wie `.b-title`, nicht mehr die kompaktere `.78rem`-
  Basisgröße der reinen Icon-Items.
- **X/Y + Betrag als eigenes Kästchen**, genauso hoch wie die Icon-Symbole:
  neue CSS-Variable `--q-info-h` (`1.6rem`, in `:root` neben `--btn`/`--gap`)
  treibt sowohl `.q-ico` (Breite/Höhe) als auch `.q-info-amount` (`height`,
  `box-sizing: border-box`, Rahmen + `border-radius` + `--surface-2`-
  Hintergrund) — beide dadurch exakt gleich hoch, unabhängig von
  verschachtelten `em`-Schriftgrößen-Kontexten.
- **Drucker verdrängt X/Y:** Ist ein Leihschein bereits gedruckt
  (`slip_printed`), zeigt die Info-Spalte nur noch das Druckersymbol, nicht
  mehr zusätzlich X/Y — sobald gedruckt ist, ist der Bücher-Fortschritt für
  den Helfer nicht mehr die relevante Info. `queueInfoIcons` in
  `web/scan-render.js` prüft jetzt `if (slip_printed) … else if
  (books_total) …` statt beide unabhängig voneinander zu pushen.

## 2026-07-18 — Helfer-Queue: Info-Icons vor dem Aufrufen-Button (Gegenstück zur Host-Info-Spalte)

Die Host-Info-Spalte (X/Y Bücher, Leihschein, Anmelde-/Zahlstatus, s. Eintrag
weiter unten) war bisher nur im Host sichtbar. Jetzt zeigt auch die
Warteschlangen-Ansicht im Helfer-Client (`scan.html`/`scan-render.js`) dieselben
Felder — rechtsbündig links vom „Aufrufen"-Pfeil, sowohl bei wartenden
Schülern (`book-row.queue-row`) als auch in den Aktiv-/Fertig-Gruppenboxen
(`queue-group-item`). Anders als im Host (Text-Badges, viel Platz in der
Tabelle) sind es hier kompakte Icons — die schmale Queue-Zeile auf dem Handy
hat keinen Platz für ausgeschriebene Labels:

- **X/Y Bücher** — Text wie im Host.
- **Leihschein gedruckt** — dasselbe Druckersymbol wie der Leihschein-Button.
- **Nicht angemeldet** — Buchsymbol (Deckel + Buchrücken-Kurve unten links,
  Lucide-„book"-Stil) mit diagonalem Strich durchgestrichen.
- **Ermäßigungsantrag offen** / **Befreiungsantrag offen** — dieselbe Form
  (Blatt mit gefalteter Ecke), unterschieden nur durch den großen,
  zentrierten Buchstaben „E"/„B" — Icon-Design nach Nutzer-Referenzbildern
  (`Antragfehlt.PNG`, `Buch.PNG`, nicht ins Repo übernommen) mehrfach
  nachgeschärft: rundere Ecken (via `stroke-linejoin="round"` + dickerer
  Strichstärke 2.5 statt 2), größere Darstellung (`.q-ico` 1.15em → 1.5em →
  1.9em über mehrere Iterationen).
- **Offener Betrag** (z. B. „40,54€", ohne bekannten Betrag „offen") — als
  Text in eigener Schriftgröße `.q-info-amount` (.9rem, normales Gewicht),
  bewusst genauso groß wie der Schülername (`.b-title`) — die übrigen
  Info-Items (X/Y, Icons) bleiben kompakter (.78rem).
- Alle Icons ausschließlich aus geometrischen SVG-Grundformen (Pfad/Linie/
  Rect), keine externen Icon-Fonts. Neue Funktion `queueInfoIcons(s)` in
  `web/scan-render.js`, Grid-Spalte `.q-info` zusätzlich in
  `.book-row.queue-row` und `.queue-group-item[data-student-id]`
  (`grid-template-columns` je um ein `auto` erweitert); leer, wenn keine
  Infos vorliegen, nimmt dann keinen Platz ein. Reine Frontend-Änderung,
  keine neuen Server-Felder — nutzt dieselben `QueueStudent`-Felder, die
  bereits für die Host-Info-Spalte über `contexts_update` an alle Clients
  (Host **und** Helfer) gehen.

## 2026-07-18 — Einstellungen: Ausblenden wirkt sofort auf den Clients (Live-Repush)

Bisher wurde beim Ausblenden einer Buchreihe im Einstellungen-Dialog zwar der
jahrgangsweite Hidden-Stand gespeichert und `broadcast_settings()` geschickt,
aber die `settings`-Nachricht trägt nur `book_order` — nicht die gefilterte
Bücherliste. Folge: Die Reihenfolge („Verschieben") kam live auf den
Helfer-Geräten an (Client sortiert `currentBooks` neu), das **Ausblenden**
aber erst beim nächsten Schülerladen. Abhilfe über einen gezielten Live-Nachzug
der Bücherliste, ohne den langsameren Worker-Neuaufbau und ohne den Scan-
Fortschritt am Client zu löschen:

- **Neue Nachricht `booklist_update`** (`{type, books, book_order}`) — ersetzt
  auf dem Client nur die Bücherliste + Reihenfolge und rendert neu;
  `scannedIsbns`/`scanOrder` bleiben erhalten (ein ausgeblendetes, bereits
  gescanntes Buch fällt einfach raus, ein wieder eingeblendetes taucht mit
  seinem IServ-Status auf). Handler in `web/scan-ws.js` (Modus A) und
  `web/student.js` (Modus B). Bewusst KEINE volle `student_info`, da diese
  clientseitig `resetScannedState` auslöst.
- **`repush_booklist`** (`server/sessions.py`) — holt die Schülerinfo frisch,
  filtert ausgeblendete Reihen neu (`hydrate_student_info` → `apply_hidden_books`),
  rechnet die ISBN-Vorabmengen (expected/vormerk/lent/lent_codes) auf dem
  HelperSession/StudentSessionB neu (damit `evaluate_scan_for_booking` den
  neuen Hidden-Stand sieht) und schickt `booklist_update`. Worker-Context wird
  NICHT angefasst. Session-Scan-Fortschritt (X/Y-Zählung auf dem Host) bleibt
  für sichtbare Bücher erhalten (`done_isbns |= prev_done & new_isbns`), ein
  ausgeblendetes Buch fällt aus BOTH X und Y.
- **`/api/booklist-hidden`** (`server/routes/booklists.py`) — schickt nach
  `broadcast_settings()` an alle aktiven Helfer (Modus A) UND gepaarten
  Schüler-Sessions (Modus B), deren zugewiesener Schüler via
  `form_catalog_cache` dem geänderten Jahrgang zugeordnet ist, per
  `asyncio.gather` parallel den `repush_booklist`; danach
  `broadcast_host` (Y der „X/Y Bücher"-Queue-Anzeige hat sich geändert).
  `_student_in_grade` filtert per Cache (beim Schülerladen befüllt; ohne
  Eintrag wurde der Schüler nie geladen → keine Liste zu aktualisieren).
- Ausgeblendet ist damit jetzt so „sofort" wie Verschieben, betriebssicher
  (read-only, kein IServ-Write) und fortlaufender Scan-Fortschritt bleibt
  erhalten. Neuer Test `tests/test_booklist_repush.py` (4 Fälle: Filter +
  Vormerk-Neuerung, Scan-Fortschritt erhalten, Modus-B ws=None-Skip,
  IServ-Fehler-resilient). Suite: 238 grün.

## 2026-07-18 — Einstellungen: Ausblenden-Toggle als grünes/rotes Kästchen

Das Ausblenden-Icon in der Bücherlisten-Ansicht des Einstellungen-Dialogs
war bislang ein Auge/Auge-durchgestrichen. Jetzt ist der Toggle ein farbiges
Kästchen mit klarem Zustand: **grün + Haken** (aus Geraden, `polyline`) =
sichtbar, **rot + Verbotssymbol** (Kreis + 45°-Strich) = ausgeblendet. Das
rote Kästchen bleibt auch im abgedunkelten ausgeblendeten Row deutlich
(`opacity: 1` auf dem Button). Symbole als `ICON_CHECK`/`ICON_NO` in
`web/host-render.js`; Box-Style in `web/host.css` (`.bo-hide-btn` 30×30,
farbig, `brightness(1.12)` bei hover). Keine Server-/Datenflussänderung;
rein frontend, keine neuen Tests, `node --check` ok.

## 2026-07-18 — Host: Leihschein-Button als Druckersymbol statt Schrift

Die Leihschein-Buttons im Host zeigen statt des Worts „Leihschein" jetzt das
dasselbe Druckersymbol wie der Helfer-Client (`web/scan.html #print-btn`):
`<svg class="ico">` aus `polyline`/`path`/`rect`. Betroffen sind beide
Vorkommen in `web/host-render.js` — die „Aktuell in Ausgabe"-Kachel
(`renderCtxNowServing`) und die Queue-Tabelle (`renderCtxQueue`, dort in der
`printBtn`-Konstante). Das SVG liegt als `ICON_PRINTER`-Konstante neben
`ICON_SUN`/`ICON_MOON`; `title`/`aria-label="Leihschein drucken"` sichern
Tooltip und Bedienung. `.ico` ist in `host.css` und `scan.html` identisch
definiert (`1em`, `currentColor`), daher gleiche Optik. Keine Server-/
Datenflussänderung; rein frontend, keine neuen Tests, `node --check` ok.

## 2026-07-18 — Host-Queue: Klassen-Präfix „Klasse " in der Spalte entfernt

In der Queue-Tabelle eines Klassen-Tabs (`renderCtxQueue`, `web/host-render.js`)
wurde `s.form` ungefiltert ausgegeben — z. B. „Klasse 10a". Da die
Spaltenüberschrift bereits „Klasse" lautet (`<th>Klasse</th>`), ist das Präfix
redundantes Rauschen. Es wird jetzt wie schon in der Helferliste (Lupe-Zuweisung,
host-render.js:796) mit `.replace(/^Klasse\s+/i, '')` entfernt — darunter steht
nur noch „10a". Keine Server-/Datenflussänderung; rein frontend, keine neuen
Tests, `node --check` ok.

## 2026-07-18 — Now-Serving-Kachel: Trennen-Button für aktive Schüler

Die „Aktuell in Ausgabe"-Kacheln (`renderCtxNowServing`, ein Tile pro aktiver
Schüler) hatten nur `Abschließen` und `Leihschein`, nicht aber `Trennen` —
obwohl derselbe Schüler in der Queue-Tabelle darunter einen Trennen-Button
hatte. Host muss also bislang in die untere Tabelle wechseln, um eine
Verbindung aus der laufenden Ausgabe zu lösen.

- **`web/host-render.js`** (`renderCtxNowServing`): dritter Button
  `<button class="secondary" data-action="disconnect" data-student-id="…">Trennen</button>`
  im `.ns-actions`-Block. Dasselbe `data-action` wie in `renderCtxQueue`,
  derselbe Handler (`disconnectStudent` → `/api/disconnect`), keine
  Server-Änderung.
- **Endpunkt deckt aktive Schüler ab:** `/api/disconnect`
  (`server/routes/queue.py`) lehnt nur `done`/`skipped` ab; für `active`
  läuft `end_student(queue_status="pending", session_state="revoked")` —
  löst Helfer-/Schüler-Verbindung und setzt den Schüler auf `Wartend`
  zurück (nicht übersprungen), wie in der Queue auch.
- Keine neuen Tests (Frontend hat keine JS-Tests); `node --check` ok,
  bestehende Suite unbetroffen.

## 2026-07-18 — Host-Helferliste: Lupe-Schüler als aktuell angezeigt (mit Klasse)

Bislang erschien in der Host-Helferliste ein per **Helfer-Lupe** (`search_call`)
aufgerufener Schüler oft als „–": `findStudentInState(h.student_id)` durchsucht
nur die Klassen-Queues, ein **transienter** Lupe-Schüler (Schnellsprung zu
einem IServ-Schüler, der in KEINER Queue steht) steht dort aber nicht. Zudem
fehlte dem Host das Signal „via Lupe gekommen", um die Klasse überhaupt
unterschiedlich anzuzeigen.

- **`HelperSession`** bekommt drei Felder: `student_lastname` /
  `student_firstname` (redundant zum `QueueStudent`, aber die einzige
  Namensquelle im Host-Snapshot für transiente Lupe-Schüler) und
  `student_via_search` (bool). `student_form` wird zusätzlich im
  `as_dict()`-Snapshot ausgeliefert (vorher nur intern für den Reconnect).
  Alle gesetzt in `assign_student_to_helper`, gelöscht in `_detach_helper`.
- **`assign_student_to_helper(..., via_search=False)`** — Keyword-Default
  `False`; `_handle_search_call` übergibt `True`, alle anderen Aufrufer
  (`_handle_call`, `assign_next_pending_to_helper`, `/api/next-student`)
  belassen es beim Default. Setzt die Namen + das Flag am Helfer VOR dem
  `broadcast_host`, sodass der Host sie im selben Snapshot sieht.
- **`SpectatorWaiter` + `spectate_student`** schleppen `via_search` mit,
  damit die Beförderung aus einer Spectator-Warteliste (Übernahme des
  Schülers nach Wartezeit, s. `end_student`) die Lupe-Herkunft vererbt —
  der Host zeigt die Klasse in Klammern auch nach zwischenzeitlichem
  Zuschauen. `_handle_search_call` übergibt `via_search=True` an alle
  drei Spectator-Pfade; `_handle_call` an seine beiden (Default `False`).
- **Host `renderHelpers`** (`web/host-render.js`): Name primär aus dem
  Queue-Eintrag (`findStudentInState`), Fallback auf die am Helfer
  hinterlegten `student_lastname`/`student_firstname` (greift bes. bei
  transienten Lupe-Schülern). Klasse in Klammern **nur** bei
  `student_via_search` (`<span class="helper-student-class">(10a)</span>`);
  bei Queue-Aufrufen nicht, da der Klassen-Tab die Klasse ohnehin
  impliziert. „Klasse "-Präfix im `form`-Wert gestrichen
  (`.replace(/^Klasse\s+/i, '')`, analog `scan-render.js`), sonst stünde
  „(Klasse 10a)". Stil dafür in `web/host.css` (`.helper-student-class`,
  dezenter `--text-muted`).
- **Draht-Format:** `helpers[*]` bekommt vier Schlüssel dazu
  (`student_lastname`, `student_firstname`, `student_form`,
  `student_via_search`). Der Charakterisierungs-Test
  `tests/test_state_contract.py` friert nur die **Top-Level**-Keys des
  Snapshots ein, nicht die Helper-Sub-Keys → unverändert grün (234 Tests).

## 2026-07-18 — Host-Queue: eigene Info-Spalte (X/Y Bücher, Leihschein, Anmelde-/Zahlstatus)

Die Klassen-Warteschlange (`renderCtxQueue`) hat eine **neue Spalte „Info"**.
Bewusst getrennt von der Status-Spalte: `status`
(Wartend/Aktiv/Fertig/Übersprungen) steuert den Ablauf, die Info-Spalte ist rein
informativ und hat auf keine Queue-Logik Einfluss. Server-seitig heißt das:
keine neuen `status`-Werte, sondern eigene Felder auf `QueueStudent`.

- **X/Y Bücher:** Y = die beim Schüler angemeldeten Bücher **ohne** die
  ausgeblendeten Reihen (exakt die Liste, die Helfer-/Schüler-Client sehen —
  `apply_hidden_books` läuft vorher), X = davon erledigte. „Erledigt" ist
  dieselbe Definition wie in den Clients (`isBookDone`): bereits ausgeliehen
  ODER in dieser Session gescannt. `staged` zählt mit, sonst stünde im
  read-only Regelbetrieb (`ALLOW_BOOKING=false`) dauerhaft X=0. Felder
  `books_total`/`done_isbns` (Draht: `books_total`/`books_done`), gefüllt in
  `hydrate_student_info` (→ `init_book_progress`), fortgeschrieben in
  `process_scan` (→ `mark_book_done`). `books_total = null` (noch nie geladen)
  → kein Badge.
- **Leihschein:** Flag `QueueStudent.slip_printed`, gesetzt in
  `print_loan_slip_for` auf allen drei Erfolgspfaden (Druck, Host-Download,
  Datei-Fallback) via `_mark_slip_printed` inkl. Host-Broadcast.
- **Anmelde-/Zahlstatus:** „Nicht angemeldet", „Ermäßigungsantrag ausstehend",
  „Befreiungsantrag ausstehend" — aus `get_student_info`
  (`enrolled`/`paid`/`remission_pending`/`exemption_pending`) über
  `QueueStudent.set_info_flags`. „Nicht angemeldet" steht allein: ohne
  Anmeldung liefert IServ zu Zahlung und Anträgen nichts Belastbares, die
  übrigen Felder bleiben dann `None` (= kein Badge) statt einen unbekannten
  Stand als Tatsache zu zeigen. `None` heißt generell „noch nicht abgefragt"
  und erzeugt nie ein Badge.
- **Offener Betrag statt Pauschal-Hinweis:** Statt „nicht bezahlt" zeigt das
  Badge den konkreten Rest, z. B. **„40,54 € offen"** — neues Feld
  `QueueStudent.amount_open` (Float, aus IServ `amountOpen`, robust gegen
  String-Werte), befüllt in `set_info_flags`. Fehlt `amount_open` (IServ
  liefert ihn nicht in jeder Einschreibung), fällt die Badge-Anzeige auf den
  Text „Bezahlung ausstehend" zurück. Formatierung deutsch
  (`toLocaleString('de-DE', {minimumFractionDigits:2, maximumFractionDigits:2})`).
- **Ein Abruf für beides:** `_apply_auto_done` heißt jetzt
  `_load_student_flags` und läuft beim Klassen-Öffnen **immer** (vorher nur bei
  gewählten Auto-Fertig-Filtern) — derselbe parallele, read-only
  `get_student_info`-Fan-out füllt die Info-Flags und wendet, falls gewählt,
  die Auto-Fertig-Filter an. Merkbare Änderung: Klassen-Öffnen macht jetzt in
  jedem Fall N read-only GETs (vorher nur mit Filter). Fehler bleiben pro
  Schüler gekapselt (Flags bleiben `None`, Status unverändert).
- **Reset:** `end_student(queue_status="pending")` ruft
  `QueueStudent.reset_progress()` (Zähler + Leihschein-Marker) — die
  IServ-Flags bleiben, das sind keine Durchlauf-Daten. Bei `done`/`skipped`
  bleibt alles stehen.
- CSS: `.q-info` (Badge-Reihe), `.badge-slip` (neue Variablen
  `--slip-tint`/`--slip-text`, hell + dunkel), `.badge-info-neutral/-ok/-warn`.
  Tests: `tests/test_queue_progress.py` (11).

## 2026-07-13 — Helferclient: Warteschlange — Aufruf-Pfeil statt „Aufrufen"-Button, Klassen-Spalte dynamisch

Die Warteschlangen-Anzeige im Helferclient (`web/scan-render.js`, `web/scan.html`)
wurde an drei Stellen überarbeitet:

- **Aufruf-Pfeil statt Textbutton:** Der „Aufrufen"-Button pro Warteschlangen-
  Zeile (wartende Schüler sowie aktiv/fertig Gruppen) zeigt jetzt einen
  geometrischen Rechts-Pfeil als `<svg class="ico">` (Lucide-Stil: Linie +
  Pfeilspitze, `stroke=currentColor`, wie die Top-Bar-Icons Lupe/Menü) statt
  des Texts „Aufrufen". `title`/`aria-label` bleiben „Aufrufen". Klick-Handler
  (WS `call` via `.call-btn` + `data-student-id`) unverändert.
- **Klassen-Spalte = längste Klasse:** Neue `maxClassWidth()` misst alle
  vorkommenden Klassen (gleiche Schrift wie `.b-fach`/`.qg-fach`: .9rem/600)
  über einen versteckten Span und setzt `--queue-class-w` auf `#book-rows`.
  `.book-row.queue-row` und `.queue-group-item` nutzen
  `var(--queue-class-w, 4.5rem)` als erste Grid-Spalte → die Namen starten in
  jeder Zeile (wartend + aktiv + fertig) am selben X.
- **Abstände:** Klasse↔Name 14px (7px grid-gap + 7px `margin-left` auf der
  Name-Zelle); Name↔Pfeil und Eintrag↔Eintrag bleiben bei 7px (wie zwischen
  den Steuer-Elementen oben: `.top-bar`/`.status-bar`/`.gear-wrap`).

Keine Server-/Datenflussänderung; rein frontend. Keine neuen Tests (Frontend
hat keine JS-Tests); `node --check` ok, bestehende Suite unbetroffen.

## 2026-07-13 — Helferclient: Lupe übernimmt existierenden Queue-Eintrag (kein Doppel-Aktiv bei Search→Queue)

Wurde ein Schüler per Lupe (`search_call`) geladen, der zusätzlich als `pending`
in einer Klassen-Queue stand, baute die Lupe einen **transienten Doppelgänger**
(`status=active`, zugewiesen) und ließ den echten Queue-Eintrag unangetastet
`pending`. Ein zweiter Helfer, der denselben Schüler aus der Warteschlangen-Ansicht
aufruft (`call`), sah am Queue-Eintrag `status == "pending"` (nicht `"active"`),
übersah den transienten Besitzer und übernahm den Schüler regulär → **derselbe
Schüler war von zwei Helfern gleichzeitig aktiv geladen** (inkl. zweitem Worker-
Context für dieselbe `student_id`).

- `server/routes/ws.py::_handle_search_call`: Claim-Logik — steht der Ziel-Schüler
  bereits in einer Klassen-Queue (beliebiger Status), wird dieser ECHTE Eintrag
  übernommen (`active` + zugewiesen via `assign_student_to_helper`) statt ein
  transienter erzeugt. Steht er in keiner Queue (Schnellsprung zu beliebigem
  IServ-Schüler), bleibt es beim transienten Eintrag (bewusst nicht in einer
  Queue). Damit läuft der `call` des zweiten Helfers sauber in die bestehende
  Spectator-Warteliste (Status „Warten bis Schüler frei…", automatische
  Beförderung bei Freigabe). Nebeneffekt behoben: `end_student` findet beim
  Abschluss jetzt den aktiven Eintrag und löst den Helfer sauber (vorher stieß
  es auf den noch-`pending`-Eintrag mit `assigned_helper == None` und ließ den
  Helfer mit stale `student_id`/leakendem Worker zurück).
- Regressionstest `tests/test_ws_scanner.py::
  test_search_call_claims_existing_queue_entry_preventing_double_active`.
  Suite grün (223 Tests).

## 2026-07-13 — Helferclient: Spectator-Status bleibt nach Menü-Schließen erhalten

Beim Schließen des Peek-Menüs rief `closePeek()` `setReadyStatus()` auf, das bei
einem Zuschauer (Spectator) den Status mit „Warten…" überschrieb statt
„Warten bis Schüler frei…" zu zeigen. Ursache: der Zuschauer-Zustand war nur
implizit (`workerPending` dauerhaft true) vorhanden, nicht als eigener Flag.

- `web/scan-state.js`: neuer State `spectating`; `setReadyStatus()` zeigt bei
  `spectating` wieder „Warten bis Schüler frei…".
- `web/scan-ws.js`: `spectating` in `student_info` aus `msg.spectator` gesetzt,
  zurückgesetzt in `worker_ready`/`loading`/`waiting`. Der explizite
  spectator-Zweig in `student_info` läuft jetzt einheitlich über
  `setReadyStatus()`.
- Rein clientseitig, kein Server-/API-Kontakt, kein Testbruch (222 Tests grün).

## 2026-07-13 — Warteschlange: aktive Schüler aufrufbar (Spectator-Warteschlange)

In der Warteschlangen-Ansicht des Helferclients (`web/scan.html`, Peek/Idle-Menü)
bekommen aktive Schüler jetzt ebenfalls einen „Aufrufen"-Button — bisher nur
wartende und fertige. Klick macht den aufrufenden Helfer zum **Zuschauer
(Spectator)**: Bücherliste read-only, Status „Warten bis Schüler frei…", und
automatische Beförderung, sobald der aktive Helfer (oder Schülerclient) den
Schüler freigibt (`end_student`/`pop_next_spectator`).

Zwei serverseitige Änderungen in `server/routes/ws.py`, damit das auch greift,
wenn der Schüler nicht bei einem Helfer, sondern bei einem **Schülerclient
(Modus B)** aktiv ist: Modus-B-Pairing setzt `status='active'` ohne
`assigned_helper`, sodass `find_helper_for_student` None lieferte und der Call
bisher mit „Schüler nicht (mehr) in der Warteschlange" fehlschlug.

- `_handle_call`: aktiver Schüler ohne Helfer-Owner (Modus B) → `spectate_student`
  statt Fehler. Der bisherige „anderer Helfer"-Spectator-Zweig wird dadurch zum
  `else` (aktive NICHT beim Aufrufer → immer Zuschauer).
- `_handle_search_call`: gleicher Guard nach dem „anderer Helfer"-Zweig, damit
  die Lupe einen Modus-B-aktiven Schüler nicht als transienten Doppel-Aktiven
  übernimmt, sondern ebenfalls als Zuschauer wartet.

Frontend: `web/scan-render.js` (`renderQueue` rendert die aktive Gruppe jetzt mit
Button). Keine CSS-Änderung (3-Spalten-Grid für `data-student-id` vorhanden).
Tests: neuer `test_call_helper_becomes_spectator_of_modus_b_active_student`;
`test_call_non_pending_student_errors` nutzt jetzt `skipped` (aktive → Spectator
ist neuer Soll-Zustand). Read-only: kein IServ-/DB-Schreibzugriff.

## 2026-07-13 — Helferclient: Lupen-Suche startet auf „Klasse wählen"

Die Lupen-Suche (Peek-Modus, Helfer-Scanner `web/scan.html`) hat beim Öffnen
bislang die zuletzt gewählte Klasse vorausgewählt — und beim allerersten Öffnen
(dann war `localStorage` noch leer) die erste Klasse des Schuljahrs. Das war
unbeabsichtigt: ohne bewusste Auswahl springt der Helfer so sofort auf eine
fremde Klasse und lädt deren Schüler.

Neu steht beim ersten Öffnen der Suche in einem Tab der Platzhalter
„— Klasse wählen —" gewählt, das Schüler-Dropdown zeigt „Zuerst Klasse wählen".
Wählt der Helfer eine Klasse, wird sie fortan vorausgewählt — aber nur noch
innerhalb desselben Tabs.

Dazu ist die letzte Klasse — analog zum Kamera-Modus-Umschalter — von
`localStorage` nach `sessionStorage` gewandert (`SEARCH_LASTCLASS_KEY =
ausleihe-search-lastclass`): ein frischer Tab/QR-Scan beginnt immer auf
„Klasse wählen" (sessionStorage leer), ein Reload desselben Tabs behält die
zuletzt gewählte Klasse. Der Platzhalter ist `disabled` (wie „— Schüler
wählen —" im Schüler-Dropdown), also nach dem Verlassen nicht wieder
anwählbar.

Rein client-seitig (`web/scan-render.js`, `web/scan-state.js`); kein Server-,
API- oder IServ-Kontakt.

## 2026-07-13 — Helferclient: Modus-Umschalter Kamera ↔ manuelle Eingabe

Der Helfer-Scanner (`web/scan.html`, Modus A) lief bisher ausschließlich über
die Gerätekamera. Für Fälle, in denen die Kamera unbrauchbar ist (Defekt,
Berechtigungen, stark beschädigte Codes), kann der Helfer jetzt den Barcode per
Tastatur eintippen — ohne die bewährte Scan-Logik zu umgehen.

Im Kamera-Dropdown (Zahnrad) gibt es oben eine Segmented-Control
„Kamera"/„Manuell". Im manuellen Modus gilt:

- Kamera wird gestoppt, Taschenlampe aus.
- `#reader` (Kamerafeld) wird zum Text-Eingabefeld.
- Der Taschenlampen-Button wird zum Enter-Button (geometrisches Icon: Pfeil
  nach unten, dann nach links, ↵-Return-Symbol).
- Enter-Taste auf der Tastatur sendet den getippten Wert.
- Ist das Eingabefeld nicht fokussiert, erscheint unten ein rotes Vollbreite-
  Banner „Vorsicht: Eingabefeld nicht fokussiert!" (weiße Schrift); Klick aufs
  Banner fokussiert das Feld. Bei offenen Dialogen (Drucken/Buch-Hinweis/
  Nächster/Ausleihe-Freigabe) wird das Banner unterdrückt.

Gesendet wird über denselben Pfad wie ein Kamera-Scan (`onScanSuccess`), sodass
Duplicate-Schutz, Peek-Sperre, Freigabe-Dialog, Statuszeile und
Buch-Hinweis-Modal identisch laufen. Der Modus wird in `sessionStorage`
(`ausleihe-scan-input-mode`) gespeichert: ein frischer Tab/QR-Scan beginnt
immer in der Kamera (sessionStorage leer), ein Reload desselben Tabs behält
den zuletzt gewählten Modus. `#reader` bleibt ein leerer Container, den
`Html5Qrcode` im Kameramodus ungeachtet übernimmt (input wird erst nach
`stop()`+`clear()` injiziert und vor `initScanner` wieder entfernt).

Rein client-seitig (HTML/CSS/JS in `web/scan.html`, `web/scan-state.js`,
`web/scan-render.js`); kein Server-/API-/Python-Kontakt, kein IServ-Zugriff.
Auto-Restart der Kamera (`visibilitychange`, eingefrorenes-Video-Intervall),
Kamera-Select/-Reload sind im manuellen Modus per Guard deaktiviert.

## 2026-07-12 — Nachbesserung: kürzerer Notiztext für unbekannten Code am Schüler-Client

Notiztext bei `unknown_book` am Schüler-Client (`web/student.js`) geändert
von „Dieser Buchcode ist unbekannt. Das Buch kann nicht verliehen werden."
zu „Dieser Code ist unbekannt. Bitte nochmal scannen." — reines Wording,
die Formatierung (Notiz normal, Betreuer-Hinweis darunter gedämpft)
stimmte bereits mit den Regeln für selbst schließbare Meldungen überein.
Helfer-Client (`web/scan-render.js`) unverändert.

## 2026-07-12 — Unbekannter Code (`unknown_book`) bekommt eigene Meldung, jetzt orange

`unknown_book` (gescannter Barcode existiert laut API nicht — kein Titel/
keine ISBN bekannt) fiel bisher in beiden Clients in den generischen
Fallback-Zweig (Fenster: nur `<Buchcode> — Buch unbekannt`, keine Notiz;
Statuszeile: `<Buchcode> — Buch unbekannt`) und war rot. Jetzt:

- **Farbe.** `#f44336` (rot) → `#e69500` (orange/gelb) in `ALERT_META`
  (`web/scan-state.js`) und `ALERT_META_STUDENT` (`web/student.js`) — die
  Meldung ist ohnehin selbst schließbar (nicht in `BLOCKING_STATUSES_STUDENT`),
  Statuszeile und Fenster-Überschrift folgen automatisch (strukturelle
  Farbkopplung aus den vorherigen Einträgen).
- **Modal (beide Clients, identisch).** Überschrift „Buch unbekannt" (gelb),
  darunter nur `<Buchcode>` (kein Titel/Bindestrich — es gibt keinen),
  darunter „Dieser Buchcode ist unbekannt. Das Buch kann nicht verliehen
  werden." — neuer eigener Zweig in `showBookAlertModal()`
  (`web/scan-render.js`, `web/student.js`) statt des bisherigen Fallbacks.
- **Statuszeile.** "<Buchcode> unbekannt" (ohne Bindestrich/Titel) —
  `scanResultStatusText()` (`web/common.js`).
- **Schüler-Client-Formatierung „automatisch mitgekommen":** weil
  `unknown_book` jetzt einen echten Notiz-Text hat statt eines leeren
  Fallbacks, greifen die bereits bestehenden Regeln für selbst schließbare
  Meldungen (normale statt gedämpfte Notiz-Schrift, Betreuer-Hinweis „Falls
  dieser Fehler unerwartet weiterhin auftritt, melde dich bitte beim
  Betreuer." über dem Schließen-Button) jetzt auch hier — kein
  Sonder-Code nötig.

## 2026-07-12 — Schüler-Client: "Du kannst diese Meldung selbst schließen." entfernt

Nachbesserung am Eintrag darunter: die Hinweiszeile „Du kannst diese
Meldung selbst schließen." existiert bei selbst schließbaren Meldungen
nicht mehr (`#book-alert-hint` ist dort jetzt `hidden`) — der Schließen-
Button spricht für sich. Bei blockierenden Meldungen (`book_deleted`,
`not_in_stock`) bleibt „Bitte warte, bis ein Helfer dieses Buch einsammelt
und dich freigibt." unverändert stehen.

Da die Notiz-Zeile (`#book-alert-note`) bei selbst schließbaren Meldungen
jetzt keine Hinweiszeile mehr darunter hat, wäre die bisherige gedämpfte
Schrift (`opacity:.6`) dort zu unauffällig — sie ist jetzt NUR noch bei
blockierenden Meldungen gedämpft, sonst normal. Dafür neue CSS-Klasse
`.book-alert-dim` (statt der bisherigen statischen Inline-Styles auf
`#book-alert-note`), per JS getoggelt (`classList.toggle('book-alert-dim',
!dismissible)`) — `web/student.html`, `web/student.js`.

## 2026-07-12 — Schüler-Client: Betreuer-Hinweis bei selbst schließbaren Meldungen

Selbst schließbare Hinweis-Meldungen (`dismissible` — alles außer den
Host-blockierten `book_deleted`/`not_in_stock`, z. B. „bereits an dich
verliehen", „nicht bestellt", „unbekannt") bekommen jetzt einen zusätzlichen
Satz über dem Schließen-Button, in derselben unscheinbaren Schrift wie die
Code/Titel-Zeile oben (`opacity:.6; font-size:.85rem`): „Falls dieser Fehler
unerwartet weiterhin auftritt, melde dich bitte beim Betreuer." Neues
Element `#book-alert-support` in `web/student.html`, befüllt/versteckt in
`showBookAlertModal()` (`web/student.js`) je nach `dismissible`. Nur
Schüler-Client — der Helfer-Client hat kein analoges Element (schließt seine
Meldungen selbst, ohne Betreuer-Bezug).

## 2026-07-12 — Helfer-Client: "dich" durch "den Schüler" ersetzt

Bei `book_already_lent`/`series_already_lent` sprachen Modal und Statuszeile
am Helfer-Client (Modus A) bisher von „dich" — falsch, denn der Helfer
scannt für den zugewiesenen SCHÜLER, nicht für sich selbst. Am
Schüler-Client (Modus B) bleibt „dich" korrekt (der Schüler scannt sein
eigenes Buch).

- `scanResultStatusText()` (`web/common.js`) bekommt einen neuen Parameter
  `targetLabel` (Default `'dich'`). `web/scan-ws.js` (Helfer-Client) ruft
  sie jetzt mit `'den Schüler'` auf; `web/student.js` (Schüler-Client)
  unverändert mit dem Default.
- `ALERT_META` (`web/scan-state.js`, nur Helfer-Client) — Titel
  „Buch bereits an dich verliehen"/„Buchreihe bereits an dich verliehen" →
  „… an den Schüler verliehen". `ALERT_META_STUDENT` (`web/student.js`)
  unverändert.
- Modal-Notiztexte (`web/scan-render.js`, nur Helfer-Client) auf die dritte
  Person umformuliert: „Dieses Buch ist bereits an den Schüler verliehen.
  Es musste nicht noch einmal gescannt werden." bzw. „Ein Buch dieser
  Buchreihe ist bereits an den Schüler verliehen. Es kann einfach wieder
  zurückgelegt werden." (statt der bisherigen Du-Form).

## 2026-07-12 — Statuszeile und Fenster-Überschrift strukturell auf dieselbe Farbe festgelegt

Bisher entschied die Statuszeile ihre Farbe über eigene, hart codierte
Bool-Flags (`isAlert`/`isAlreadyLent`), während die Fenster-Überschrift ihre
Farbe aus `ALERT_META`/`ALERT_META_STUDENT` bezog — beide Quellen konnten
auseinanderlaufen. Sichtbar wurde das bei `not_ready`/`not_enrolled`: Fenster
orange, Statuszeile aber rot. Niklas wollte beides synchron UND festgelegt,
welche Fälle rot sein müssen: die, bei denen am Schüler-Client der Host auf
„Freigeben" drücken muss (`book_deleted` — beide Ausgemustert-Fälle — und
`not_in_stock`), analog auch am Helfer-Client.

- **Strukturelle Lösung statt Einzel-Patch.** `setStatusText(text,
  alertClass)` nimmt jetzt direkt den Ziel-CSS-Klassennamen entgegen (statt
  drei Bool-Parametern). Neue Funktion `statusAlertClass(status)`
  (`web/scan-state.js`, `web/student.js`) leitet ihn aus DEMSELBEN
  `ALERT_META`/`ALERT_META_STUDENT` ab, das auch die Modal-Überschrift
  einfärbt — Statuszeile und Fenster können dadurch strukturell nicht mehr
  auseinanderlaufen, ganz gleich, welche Status künftig hinzukommen.
  `'booked'` bleibt grün, `staged`/OK-Status normal.
- **CSS-Klassen umbenannt** (Farbe statt Bedeutung im Namen, da mehrere
  fachlich unterschiedliche Status dieselbe Farbe teilen):
  `status-book-deleted` → `status-alert-red`, `status-already-lent` →
  `status-alert-orange` (`web/scan.html`, `web/student.html`).
- **Ergebnis:** `book_deleted` (beide Fälle) und `not_in_stock` sind jetzt
  in Fenster UND Statuszeile durchgehend rot (die Host-Freigabe-Fälle);
  `book_already_lent`/`series_already_lent`/`not_enrolled`/`not_ready`
  jetzt durchgehend orange (vorher Statuszeile fälschlich rot bei
  `not_enrolled`/`not_ready`); `unknown_book`/`error` bleiben rot
  (unverändert, waren schon konsistent).

## 2026-07-12 — Nachbesserung: Klammern um die Klasse doch fett (gesamter Namensbereich)

Korrektur am Eintrag darunter: Niklas wollte die Klammern um die Klasse
entgegen der vorherigen Nachbesserung DOCH fett — `borrowerNameHtml()`
(`web/scan-render.js`) umschließt jetzt wieder den gesamten Namensbereich
inkl. Klammern in einem `<strong>` ("Nachname, Vorname (Klasse)"), statt
Name/Klasse und Klammern separat zu stylen.

## 2026-07-12 — Nachbesserung: Name/Klasse auch bei "an jemand anderen verliehen" fett, Klammern selbst nicht fett

Zwei Korrekturen an der Helfer-Client-Notiz aus dem Eintrag darunter:

- Format von "Nachname, Vorname, Klasse" (Komma-getrennt) auf "Nachname,
  Vorname (Klasse)" (Klammern, wie beim Ersatzanspruch) geändert.
- Name UND Klasse jetzt fett — Niklas wollte das ursprünglich als "plain"
  eingestufte `not_in_stock`-Format doch fett, wie beim Ersatzanspruch.
  Die Klammern selbst bleiben bewusst NICHT fett (nur ihr Inhalt).
  `borrowerNameHtml()` in `web/scan-render.js` baut jetzt
  `<strong>Nachname, Vorname</strong> (<strong>Klasse</strong>)` — von
  BEIDEN Notizen (Ersatzanspruch UND „an jemand anderen verliehen")
  gemeinsam genutzt, damit die Klammer-Regel an beiden Stellen konsistent
  ist (vorher umschloss der Ersatzanspruch-Aufruf den gesamten String
  inkl. Klammern in einem `<strong>`).

## 2026-07-12 — "An jemand anderen verliehen" (`not_in_stock`) bekommt eigene Meldung analog zu ausgemustert

Bisher zeigte `not_in_stock` ("Buch aktuell an jemand anderen verliehen")
generisch „Buch noch verliehen" + technische `msg`, am Helfer-Client
zusätzlich eine separate „Aktuell verliehen an: {Name}"-Zeile ohne Klasse.
Niklas wollte dieselbe dreiteilige Struktur wie bei den anderen Alerts
(Titel/Code+Titel/Notiz), am Schüler-Client identisch zum Aufbau von
„ausgemustert", am Helfer-Client mit Name+Klasse in der Notiz statt der
separaten Zeile.

- **Backend (`server/iserv_client.py`).** `loaned_to_form` (Klasse) wird
  jetzt bei JEDEM Buch mit bekanntem Ausleiher aufgelöst (vorher nur bei
  ausgemusterten Büchern) — sowohl für `not_in_stock` als auch weiterhin für
  den Ersatzanspruch-Fall. `evaluate_scan_for_booking()`s `not_in_stock`-
  Zweig (`server/sessions.py`) reicht `loaned_to_firstname`/`loaned_to_lastname`/
  `loaned_to_form` jetzt ebenfalls durch (Host-Broadcast + Helfer-Payload;
  Schüler-Client weiterhin `null`, Privatheit — `msg` bleibt namensfrei).
- **Modal Schüler-Client (`web/student.js`).** Titel „Buch bereits
  verliehen" (rot, ersetzt „Buch noch verliehen"), darunter
  `<Buchcode> — <Titel>`, darunter „Dieses Buch ist bereits an jemand
  anders verliehen. Es kann derzeit nicht an dich verliehen werden."
- **Modal Helfer-Client (`web/scan-render.js`).** Titel „Buch bereits
  verliehen" (rot), darunter `<Buchcode> — <Titel>`, darunter „Dieses Buch
  ist bereits an <Nachname>, <Vorname>, <Klasse> verliehen. Es kann nicht
  auf den Schüler verliehen werden." (plain, kein Fett — anders als beim
  Ersatzanspruch). Die alte separate `#book-alert-borrower`-Zeile
  (`"Aktuell verliehen an: …"`) ist komplett entfernt (HTML+CSS+JS,
  `scan.html`/`scan-state.js`/`scan-render.js`) — war zuletzt nur noch für
  `not_in_stock` in Gebrauch und ist jetzt durch die Notiz ersetzt.
- **Statuszeile.** "<Buchcode> bereits verliehen — <Titel>" (rot, ohne
  Name — der Schüler sieht nie WEM) — `web/common.js`
  (`scanResultStatusText`), für beide Clients gleich.
- **Tests.** `tests/test_iserv_client_borrower.py` angepasst: der bisherige
  „kein Extra-Request für nicht-ausgemusterte Bücher"-Test ist jetzt ein
  „Klasse wird auch für normal verliehene Bücher aufgelöst"-Test (die
  Effizienz-Ausnahme galt nur, solange die Klasse ausschließlich für den
  Ersatzanspruch gebraucht wurde). Volle Suite grün.

## 2026-07-12 — Nachbesserung: Klasse ohne "Klasse "-Präfix, Warten-Hinweis "dieses Buch" statt "dein Buch"

Zwei kleine Textkorrekturen an den beiden vorherigen Einträgen:

- **Ersatzanspruch-Klasse ohne Präfix.** `loaned_to_form` kommt roh aus der
  IServ-API mit "Klasse "-Präfix (z. B. "Klasse 6a"), wie `s.form` überall
  sonst im Frontend auch — und wird dort jeweils client-seitig gestrippt
  (`student.js`, `scan-render.js`, `scan-ws.js`). Dieselbe
  `.replace(/^Klasse\s+/i, '')`-Behandlung fehlte bisher bei der neuen
  Ersatzanspruch-Notiz/Statuszeile; ergänzt in `web/scan-render.js`
  (`borrowerNameHtml()`) und `web/common.js` (`scanResultStatusText()`) —
  zeigt jetzt "6a" statt "Klasse 6a".
- **Schüler-Warten-Hinweis.** "Bitte warte, bis ein Helfer dein Buch
  einsammelt und dich freigibt." → "… bis ein Helfer **dieses** Buch
  einsammelt …" (`web/student.js`) — gilt für beide `book_deleted`-Fälle
  (mit/ohne Ersatzanspruch), da der Hinweis für alle blockierenden Status
  gemeinsam ist.

## 2026-07-12 — Ersatzanspruch-Meldung: Schüler-Client wie „nur ausgemustert", Helfer-Client mit Name+Klasse

Ausgemustertes Buch MIT Ersatzanspruch (`loaned_to` gesetzt) bekam bisher
am Helfer-Client noch die alte generische „Ausgemustertes Buch gescannt"-
Meldung + technische `msg` + separate „Ersatzanspruch: {Name}"-Zeile.
Niklas wollte: Schüler-Client identisch zum Fall OHNE Ersatzanspruch (war
bereits so, da `loaned_to` dort aus Privatheitsgründen immer `null` ist —
keine Code-Änderung nötig, nur verifiziert); Helfer-Client mit eigener
Meldung inkl. Name **und Klasse** des Ersatzanspruch-Inhabers.

- **Backend (`server/iserv_client.py`).** `get_book_by_code()` liefert neu
  `loaned_to_firstname`/`loaned_to_lastname` (statt nur des zusammengesetzten
  `loaned_to`-Strings) sowie `loaned_to_form` (Klasse). Die Klasse wird NUR
  bei ausgemusterten Büchern mit Schüler-Verknüpfung per zusätzlichem
  read-only Request (`GET /students/:id?forms=true`) aufgelöst — bei allen
  anderen Büchern (auch beim normalen „an jemand anderen verliehen") bleibt
  sie `None`, ohne Extra-Request. Bei mehreren Schuljahren gewinnt das mit
  der höchsten `schoolyear`-id. Neue statische Helper `_resolve_student_form`
  (+ `_resolve_current_borrower` liefert jetzt Vor-/Nachname getrennt statt
  eines zusammengesetzten Strings). `evaluate_scan_for_booking()` und
  `process_scan()` (`server/sessions.py`) reichen die drei neuen Felder
  analog zu `loaned_to`/`loaned_to_id` durch (Host-Broadcast immer, Client-
  Payload nur für den Helfer-Scanner — der Schüler-Client bekommt sie wie
  bisher `null`, Privatheit).
- **Modal (Helfer-Client, `web/scan-render.js`).** `book_deleted` MIT
  `loaned_to`: Überschrift „Für das Buch gibt es einen Ersatzanspruch" (rot,
  wie bisher), darunter `<Buchcode> — <Titel>`, darunter „Für dieses Buch
  liegt ein Ersatzanspruch an **<Nachname>, <Vorname> (<Klasse>)** vor. Es
  kann derzeit nicht ausgeliehen werden." (Name/Klasse fett, `innerHTML` mit
  `escapeHtml()`-ten Werten). Die bisherige separate Ersatzanspruch-Zeile
  (`bookAlertBorrowerEl`) entfällt für diesen Fall (Info steckt jetzt in der
  Notiz) — bleibt nur noch für „an jemand anderen verliehen" (`not_in_stock`).
- **Statuszeile.** "<Buchcode> Ersatzanspruch an <Nachname>, <Vorname>
  (<Klasse>) — <Titel>", rot wie beim einfachen „ausgemustert" (gleiche
  `status-book-deleted`-Klasse, kein Code-Unterschied nötig) — `web/common.js`
  (`scanResultStatusText`).
- **Tests.** Neue `tests/test_iserv_client_borrower.py` (6 Tests: Name-Split
  aus eingebettetem Student, Fallback auf `get_by_id`, `loaned_to_form`
  bleibt `None` beim normalen Verleih-Fall ohne Extra-Request, Auflösung +
  aktuellstes-Schuljahr-Sortierung beim Ersatzanspruch-Fall, Fehlertoleranz).
  `tests/test_booking_precheck.py` um die drei neuen Felder in den
  bestehenden Ersatzanspruch-Tests ergänzt. 214 → 220 Tests.

## 2026-07-12 — Fix: Statuszeile am Schüler-Client verlor Aussehen ohne Textwechsel

Niklas meldete, dass die Statuszeile am Schüler-Client (Modus B) ihr
Aussehen (z. B. Rot bei „ausgemustert") verlieren sollte, aber nur wenn
sich der TEXT ändert — nicht vorher. Ursache: `student.js` setzte
`#status-text`-Textinhalt und die Alert-CSS-Klassen (`status-book-deleted`
etc.) an mehreren Stellen unabhängig voneinander (direktes
`textContent`/`classList` statt eines gemeinsamen Helpers, anders als
`scan.js`/`scan-ws.js`, die von Anfang an über `setStatusText()` liefen).
Konkret riss der `book_alert_clear`-Handler (Host gibt eine ausgemustert-/
verliehen-Meldung frei) `status-book-deleted` sofort weg, OBWOHL der Text
unverändert blieb — die rote Statuszeile wurde vorzeitig wieder normal,
obwohl noch dieselbe Meldung dastand. Umgekehrt setzten mehrere Stellen
(`worker_ready`, Kamera-Neustart, Reconnect, Fehler) neuen Text OHNE die
Alert-Klassen zurückzusetzen — eine neue neutrale Meldung hätte fälschlich
in der alten Alert-Farbe erscheinen können.

Fix: neuer gemeinsamer `setStatusText(text, isAlert, isIssued,
isAlreadyLent)`-Helper in `web/student.js` (analog zu `scan-state.js`),
JEDE Statuszeilen-Änderung läuft jetzt darüber. Der `book_alert_clear`-
Handler ruft ihn bewusst NICHT mehr auf — nur das Modal schließt, Text und
Farbe der Statuszeile bleiben bis zur nächsten `scan_result`-Meldung
unverändert stehen.

## 2026-07-12 — Schüler-Client: Warten-Hinweistext + Schrift-Gewichtung im Bereits-verliehen-Modal getauscht

Blockierendes Hinweis-Modal am Schüler-Client (`book_deleted`/`not_in_stock`,
nicht selbst schließbar, nur der Host gibt frei): Hinweistext geändert von
„Bitte warte, bis der Betreuer dies freigibt." zu „Bitte warte, bis ein
Helfer dein Buch einsammelt und dich freigibt." (`web/student.js`). Dazu
Schrift-Gewichtung getauscht: der Hinweis (`#book-alert-hint`) steht jetzt
in normaler Schrift, Code-/Titel-Zeile (`#book-alert-text`) und die
Zusatzmeldung darunter (`#book-alert-note`) bekommen stattdessen das
gedämpfte Styling, das vorher nur der Hinweis hatte (`opacity:.6;
font-size:.85rem`) — `web/student.html`.

## 2026-07-12 — Eigene Meldung für „Buch ausgemustert" ohne Ersatzanspruch

Bisher zeigte `book_deleted` immer die generische Überschrift
„Ausgemustertes Buch gescannt" + die technische Server-`msg` ("Buch
ausgemustert: {title}"), egal ob ein Ersatzanspruch (`loaned_to`) vorliegt
oder nicht. Niklas wollte für den Fall OHNE Ersatzanspruch eine eigene,
kürzere Meldung — der Ersatzanspruch-Fall (Helfer-Client, `loaned_to`
gesetzt) bleibt unverändert.

- **Modal (Helfer- und Schüler-Client).** `book_deleted` ohne `loaned_to`:
  Überschrift „Buch ausgemustert" (rot `#f44336`, wie bisher), darunter
  `<Buchcode> — <Titel>`, darunter „Dieses Buch ist ausgemustert. Es kann
  nicht mehr verliehen werden." Mit `loaned_to` (Ersatzanspruch, nur
  Helfer-Client — am Schüler-Client ist `loaned_to` aus Privatheitsgründen
  ohnehin immer `null`, der neue Fall greift dort also immer): unverändert
  generische Überschrift + technische `msg` + Ersatzanspruch-Zeile.
  (`web/scan-render.js`, `web/student.js`.)
- **Statuszeile.** Ohne `loaned_to`: "<Buchcode> ausgemustert — <Titel>"
  (rot, wie bisher über die bestehende `status-book-deleted`-Klasse). Mit
  `loaned_to`: unverändert der generische Fallback (technische `msg`).
  (`web/common.js`, `scanResultStatusText`.)

## 2026-07-12 — Fix: fehlender Buchtitel im Bereits-verliehen-Modal/Statuszeile

Nachbesserung am Eintrag darunter: `process_scan()` (`server/sessions.py`)
reichte im `scan_result`-Payload für nicht-buchbare Status (u. a.
`book_already_lent`/`series_already_lent`) keinen `title` durch — nur
`msg` (die technische, längere Server-Meldung). Folge: Im Modal stand hinter
dem Buchcode statt des Titels die Modal-Überschrift (Fallback `meta.title`),
in der Statuszeile stand hinter dem Bindestrich die ganze technische `msg`
statt nur des Titels. Fix: `title` (bereits Teil von `decision` in
`evaluate_scan_for_booking`) wird jetzt mit ins `scan_result`-Payload
gegeben. Zusätzlich bekommt die Statuszeile für `series_already_lent`
jetzt ebenfalls " — <Titel>" (fehlte bisher komplett, war nur für
`book_already_lent` vorhanden) — `web/common.js` (`scanResultStatusText`).
Neuer Test `test_process_scan_result_carries_title_for_already_lent`
(`tests/test_booking_precheck.py`).

## 2026-07-12 — Unterscheidung Buch- vs. Buchreihe-bereits-verliehen (`book_already_lent` neu)

Nachbesserung am bisherigen `series_already_lent`-Hinweis (Eintrag darunter):
Niklas wollte unterscheiden, ob genau DAS gescannte Exemplar schon auf den
Schüler läuft oder nur ein ANDERES Exemplar derselben Buchreihe. Neuer Status
`book_already_lent` deckt den ersten Fall ab, `series_already_lent` bleibt für
den zweiten.

- **Backend (`server/sessions.py`).** `booking_isbn_sets_from_info()` gibt
  jetzt zusätzlich `lent_codes` zurück (Barcodes der konkret ausgeliehenen
  Exemplare aus `info["current_books"]`). `evaluate_scan_for_booking()`
  vergleicht bei ISBN-Treffer den gescannten Barcode gegen `lent_codes`:
  Treffer → `book_already_lent` ("Bereits an dich verliehen: {title}"),
  sonst weiterhin `series_already_lent` ("Reihe bereits ausgeliehen: {title}").
  `process_scan()` trägt den Code nach erfolgreicher Buchung zusätzlich in
  `lent_codes` nach, damit ein erneuter Scan desselben Exemplars in derselben
  Session korrekt als `book_already_lent` erkannt wird. Neues Session-Feld
  `lent_codes: set[str]` auf `HelperSession`/`StudentSessionB`
  (`server/state.py`), durchgereicht durch `hydrate_student_info` und beide
  `process_scan`-Aufrufer (`server/routes/ws.py`); Reset an den bestehenden
  `vormerk_isbns`/`lent_isbns`-Reset-Stellen (`sessions.py`, `routes/queue.py`).
- **Modal (Helfer- und Schüler-Client).** `book_already_lent`: Überschrift
  „Buch bereits an dich verliehen" (gelb), darunter `<Buchcode> — <Titel>`,
  darunter „Dieses Buch ist bereits an dich verliehen. Du musstest es nicht
  noch einmal scannen." `series_already_lent`: Überschrift „Buchreihe bereits
  an dich verliehen" (gelb), darunter `<Buchcode> — <Titel>`, darunter „Ein
  Buch dieser Buchreihe ist bereits an dich verliehen. Leg es einfach wieder
  zurück." (`web/scan-render.js`, `web/student.js`).
- **Statuszeile.** `book_already_lent`: "<Buchcode> bereits an dich verliehen
  — <Titel>". `series_already_lent`: "<Buchcode> Buchreihe bereits an dich
  verliehen" (ohne Titel). Beide im gleichen Gelb wie die Modal-Überschrift
  (`status-already-lent`, `#e69500`) — `web/common.js` (`scanResultStatusText`),
  `web/scan-ws.js`/`web/student.js` (Klassen-Toggle). Alle Trennstriche in den
  neuen/angepassten Strings sind Halbgeviertstriche ("—"), konsistent mit dem
  Rest der Statuszeilen.
- **Tests.** `tests/test_booking_precheck.py` um Fälle für die neue
  Unterscheidung erweitert (`test_reject_book_already_lent_same_code`,
  `test_series_already_lent_when_different_code_of_same_isbn`,
  `test_process_scan_booked_isbn_moves_to_lent` prüft jetzt zusätzlich, dass
  ein erneuter Scan desselben Exemplars `book_already_lent` liefert).

## 2026-07-12 — Meldung + Statuszeile bei „Buch bereits an dich verliehen" (`series_already_lent`)

Nachbesserung an der Modal-Meldung und der Statuszeile für den Status
`series_already_lent` (ein einzelnes Buch — nicht die ganze Reihe — ist
bereits an den scannenden Schüler ausgeliehen), in `scan-render.js`
(Modus A) und `student.js` (Modus B):

- **Modal.** Überschrift bleibt wie bisher gelb (`#e69500`, „Buch bereits
  an dich verliehen"). Darunter weiterhin `<Buchcode> - <Buchtitel>`. Neu
  darunter, mit Abstand über den bestehenden `.modal-box`-Flex-`gap`, eine
  eigene Zeile `"Dieses Buch ist bereits an dich verliehen."` statt der
  technischen Server-`msg` (neues `<p id="book-alert-note">` in
  `scan.html`/`student.html`).
- **Statuszeile.** Neuer Helper-Fall in `scanResultStatusText()`
  (`common.js`) formatiert `"<Buchcode> bereits an dich verliehen -
  <Titel>"`. Neue CSS-Klasse `status-already-lent` (`#e69500`, fett,
  gleiches Gelb wie die Modal-Überschrift) in `scan.html`/`student.html`;
  `setStatusText()` (`scan-state.js`) bekommt dafür einen vierten
  Parameter `isAlreadyLent`.

## 2026-07-12 — Statuszeile bei erfolgreicher Buchung: Fach + Titel statt Worker-Rohtext, grün eingefärbt

Zwei Nachbesserungen an der Statuszeile für `scan_result`-Status `'booked'`
(tatsächliche Ausgabe bei `ALLOW_BOOKING=true`):

- **Fach + Titel statt DOM-Best-effort-Meldung.** Neue Helper-Funktion
  `scanResultStatusText(msg, books)` in `web/common.js` ersetzt für Status
  `'booked'` die technische Worker-Meldung ("Buchung im DOM bestätigt
  (best-effort)") durch Fach + Titel, nachgeschlagen per ISBN aus der
  Bücherliste des Schülers (`currentBooks`/`student_info`). `scan-ws.js`
  (Modus A) und `student.js` (Modus B) nutzen den gemeinsamen Helper statt
  eigener Ad-hoc-Formatierung.
- **Formatierung + Farbe.** Bei `'booked'` baut der Helper jetzt die
  komplette Zeile selbst: `"<Buchcode> ausgegeben — <Fach> — <Titel>"` —
  ohne Bindestrich zwischen Buchcode und "ausgegeben" (anders als bei allen
  übrigen Status, die weiterhin `"<Buchcode> — <Meldung>"` mit Trenner
  zeigen). Neue CSS-Klasse `status-book-issued` (`#2e7d32`, fett) in
  `scan.html`/`student.html` färbt die Zeile grün; `setStatusText()`
  (`scan-state.js`) bekommt dafür einen dritten Parameter `isIssued`.

## 2026-07-11 — Selbst-Aufruf zählt jetzt als neuer Zugriff (Menü-Schließen-Fix + Rückstellungspflicht)

Nachbesserung am `refresh_active_student`-Kurzschluss aus dem Eintrag
darunter: der reine Info-Refresh bei Selbst-Aufruf (Helfer ruft seinen
EIGENEN aktiven Schüler per Queue-`call`/Lupe erneut auf) sendete bewusst
kein `loading` — dadurch blieb im Helferclient das Menü/Such-Panel offen
(kein Trigger zum Schließen). Niklas wollte zusätzlich eine
Verhaltensänderung: ein Selbst-Aufruf soll wie ein neuer Zugriff zählen,
nicht wie ein bloßer Refresh — existiert eine Warteliste für den Schüler,
muss sich der Aufrufer hinten anstellen (der bisher Wartende übernimmt
sofort), statt sich die Aktivität direkt zurückzuholen.

`refresh_active_student` wieder entfernt (kein Aufrufer mehr). Neue Logik in
`_handle_call`/`_handle_search_call`: Selbst-Aufruf + existierende
Warteliste → regulärer `end_student` (befördert den Ersten in der Liste
automatisch, wie beim normalen Beenden) gefolgt von `spectate_student` für
den bisherigen Besitzer (stellt sich hinten an — KEIN Zurückholen, sonst
wieder zwei aktive Clients). Selbst-Aufruf OHNE Warteliste → fällt in den
unveränderten Standard-Pfad (`end_student` + `assign_student_to_helper` an
denselben Helfer) durch, der ohnehin `loading` sendet und damit auch das
Menü schließt — ein vollständiger Reload statt eines Teil-Refreshs, exakt
wie bei jedem anderen Aufruf.

Tests umbenannt/angepasst (`test_..._does_not_dual_assign` →
`test_..._demotes_caller_to_back_of_queue`) + ein neuer Test für den
No-Queue-Reload-Pfad (`loading` wird gesendet). 209 → 210 Tests.

## 2026-07-11 — Spectator-Feinschliff: Live-Refresh, Warteposition über Reload, Selbst-Aufruf-Bug behoben

Drei Nachbesserungen am Spectator-Mechanismus (Eintrag darunter), gemeldet
nach dem ersten Live-Test:

- **Live-Refresh für Spectators.** Lädt der AKTIVE Helfer seine Seite neu
  (Reconnect in `ws_scanner`), bekommen jetzt auch alle Spectators dieses
  Schülers ein aufgefrischtes `student_info` (neue Funktion
  `sessions.broadcast_student_info_to_spectators`) — vorher blieb ihre
  Ansicht bis zum nächsten Scan auf altem Stand.
- **Warteposition bleibt über Reload erhalten.** Der Disconnect-Handler in
  `ws_scanner` entfernt einen Spectator NICHT mehr sofort aus
  `state.student_spectators` (das tat er vorher, ohne Gnadenfrist). Lädt ein
  wartender Client seine Seite neu, bleibt sein Platz in der FIFO-Liste
  erhalten (Reconnect-Zweig `elif helper.spectating_student_id is not
  None`); nur echte, dauerhaft verwaiste Einträge werden bei ihrer eigenen
  Beförderung von `pop_next_spectator` (das tote WS bereits übersprang)
  verworfen.
- **Kritischer Bugfix — Doppel-Aktiv bei Selbst-Aufruf.** Rief der AKTIVE
  Helfer seinen EIGENEN Schüler über Queue-`call` oder Lupe-`search_call`
  erneut auf, während ein anderer Helfer als Spectator wartete, löste das
  interne `end_student` (Teil des bisherigen „erst beenden, dann neu
  zuweisen"-Musters) dessen Beförderung aus — der Handler wies den Schüler
  aber direkt danach trotzdem wieder dem ursprünglichen Helfer zu: zwei
  Clients gleichzeitig aktiv, genau die Invariante, die der Spectator-
  Mechanismus eigentlich verhindern soll. Neue Funktion
  `sessions.refresh_active_student`: `_handle_call`/`_handle_search_call`
  erkennen jetzt den Fall „Aufrufer ist bereits selbst der Besitzer"
  (`find_helper_for_student(sid).token == helper.token`) und laufen
  stattdessen über einen reinen Info-Refresh (kein `end_student`, keine
  Neuzuweisung, kein Beförderungsrisiko) — inklusive Spectator-Fan-out wie
  oben.

Tests: `tests/test_ws_scanner.py` (Selbst-Aufruf via `call` und
`search_call` je mit wartendem Spectator, Reload-Fan-out, Reload-mit-
erhaltener-Warteposition). 206 → 209 Tests.

## 2026-07-11 — Spectator-Modus + Warteliste statt Doppel-Öffnen-Fehler

Ersetzt den vorherigen reinen Busy-Fehler (Eintrag darunter) durch einen
vollen Zuschauer-/Wartelisten-Mechanismus: versucht ein zweiter Helfer
(Queue-`call` oder Lupe-`search_call`), einen bereits bei einem ANDEREN
Helfer aktiven Schüler zu laden, bekommt er sofort dessen Bücherliste
read-only angezeigt (live mit jedem Scan des aktiven Helfers mitaktualisiert)
— aber KEINEN eigenen Playwright-Worker (es gibt ohnehin nur einen Worker pro
`student_id`). Statuszeile: „Warten bis Schüler frei…". Erst wenn der aktive
Helfer den Schüler beendet, wird der am längsten Wartende automatisch
befördert (jetzt MIT Worker); ein dritter Wartender bleibt entsprechend in
der Liste, bis auch der neu beförderte fertig ist (FIFO-Handoff-Kette).

Neu: `HelperSession.spectating_student_id` (getrennt von `student_id` — das
bleibt strikt „ich besitze Worker + Queue-Slot"), `SpectatorWaiter`-Dataclass
und `AppState.student_spectators`/`add_spectator`/`remove_spectator`/
`pop_next_spectator` (`server/state.py`). `sessions.spectate_student()`
registriert den Zuschauer (räumt vorherige eigene/andere Zuschauer-
Registrierung zuerst auf) und pusht `student_info` mit `spectator: true` —
kein `worker_pool.open_student`. `assign_student_to_helper()` räumt am Anfang
automatisch eine noch offene Zuschauer-Registrierung des Helfers ab (jeder
Pfad, der einen Helfer wirklich einen Schüler zuweist, egal ob „Nächster",
„Aufrufen" oder die neue Beförderung, läuft darüber). `end_student()` bekommt
dafür einen Beförderungs-Zweig (für echte Queue-Schüler UND transiente
Lupe-Ziele, die redundant im `SpectatorWaiter` gespeicherte lastname/
firstname/form nutzen) — bewusst synchron ohne Await zwischen
`pop_next_spectator` und dem Aufruf von `assign_student_to_helper`, damit
kein Zeitfenster entsteht, in dem ein dritter Helfer den Schüler regulär
„callen" könnte, bevor die Beförderung feststeht. `_handle_scan`
(`server/routes/ws.py`) spiegelt jeden Scan des aktiven Helfers zusätzlich an
alle Spectator-Tokens (`spectator: true`). Disconnect eines Zuschauers
räumt ihn sofort (keine Reconnect-Gnadenfrist — er hält keine exklusive
Ressource) aus der Warteliste.

Der neue Guard erkennt jetzt auch belegte TRANSIENTE Lupe-Ziele (über
`find_helper_for_student` statt `find_student`), was der vorherige Fix noch
verpasste (transiente Schüler stehen in keiner Queue). Frontend
(`web/scan-ws.js`): `student_info`/`scan_result` mit `spectator: true` zeigen
die Bücherliste read-only, ohne Statuszeile/Alert-Modal zu überschreiben;
`workerPending` bleibt dauerhaft `true` (sperrt Scans über den bestehenden
Client-Gate). Tests: `tests/test_ws_scanner.py` (Spectate über echte
Websockets, Scan-Fan-out, Disconnect-Aufräumung),
`tests/test_queue_flow.py` (Beförderung + FIFO-Kette, low-level über
`end_student`).

## 2026-07-11 — Guard gegen Doppel-Öffnen desselben Schülers (Lupe)

`_handle_search_call` (`server/routes/ws.py`) prüfte bislang nicht, ob der per
Lupe angesprungene Schüler bereits bei einem ANDEREN Helfer aktiv ist — anders
als `_handle_call` (Queue-Aufruf), das `status not in (pending, done)` bereits
abfängt. Da die Lupe gezielt JEDEN Schüler laden kann (auch außerhalb der
eigenen Queue), konnte so derselbe Schüler auf zwei Clients gleichzeitig
geöffnet werden (zwei parallele Worker-Sessions). Neuer Guard: `state.
find_student(sid)` vor dem Laden prüfen — ist der Treffer `status == "active"`
und `assigned_helper != helper.token`, wird nichts geladen, stattdessen
`{"type": "error", "busy": true, "msg": "Warte bis Schüler frei…"}` gesendet.
Frontend (`web/scan-ws.js`) zeigt bei `busy: true` den Text unverändert in der
Statuszeile (ohne den sonstigen `"Fehler: "`-Prefix); das Such-Panel bleibt
offen für einen erneuten Versuch. Test: `tests/test_ws_scanner.py::
test_search_call_blocks_student_active_on_other_helper`.

## 2026-07-11 — Auto-fertig-Filter „Alle Bücher bereits ausgeliehen"

Fünfter Sofort-fertig-Filter beim Klassen-Öffnen (`_AUTO_DONE_FILTERS` in
`server/routes/classes.py`, ergänzt neben `not_enrolled`/`unpaid`/
`remission_pending`/`exemption_pending`): `all_lent` setzt einen Schüler direkt
auf `done`, wenn seine vorgemerkten Buchreihen — nach Anwendung der
ausgeblendeten ISBNs (`get_hidden_isbns_for_form`) — bereits vollständig
ausgeliehen sind (`booking_isbn_sets_from_info` liefert kein `vormerk` mehr).
UI-Checkbox in `web/host.html`, Persistenz in `web/host-state.js`
(`AUTO_DONE_KEYS`). Spart manuelles Durchklicken von Schülern, die schon
komplett versorgt sind.

## 2026-07-11 — Wartbarkeits-Welle 7 (Subagent-Refactoring)

Neun Verbesserungspunkte aus einem Codebase-Review, ausgeführt von Sonnet-5-
Subagents (Fortsetzung der Wellen 0–6). Alles verhaltenserhaltend; Baseline
`ccdcbd9`, Ergebnis auf `main` (`84497cb`):

- **`routes/ws.py`** — `safe_broadcast()` und `_take_over_ws()` extrahiert
  (ersetzen den ~4× wiederholten `try/except: pass`-Broadcast bzw. den
  Reconnect-Ownership-Swap). `ws_scanner`-Empfangsschleife (`if mtype==…`-Kette)
  auf eine Dispatch-Table `_SCANNER_HANDLERS` (10 kleine `_handle_*`) umgestellt.
  `ws_student`-Reconnect auf dieselbe „Swap vor `await close()`"-Ordnung wie
  `ws_scanner` vereinheitlicht (strikt sicherer gegen den Finally-Race).
- **`web/host.js`/`web/scan.js`** (je ~1500 Z.) in `*-state.js`/`*-ws.js`/
  `*-render.js` gesplittet (geordnete `<script>`-Tags, geteilter Top-Level-
  Scope, additive `window.__host`/`__scan`-Introspektion). `student.js` in eine
  IIFE gewrappt. Verhalten browser-verifiziert (headless-Chromium-Smoke: alle
  drei Seiten laden ohne uncaught JS/ReferenceError/TypeError).
- **`server/state.py`** — die toten `AppState`-Forwarding-Shims (`RuntimeSettings`/
  `IservCaches`, ~110 Z.) entfernt; einziger verbliebener Consumer war
  `setattr(state, …)` in `routes/settings.py` → auf `state.settings` umgebogen.
  Lange Feld-Rationale-Kommentare nach neuem `docs/PLAN.md § State-Feld-Rationale`
  ausgelagert (Typdefinitionen wieder skimmbar).
- **`server/iserv_client.py`** — doppelte TTL-Staleness-Prüfung in `_resolve_sy`
  in einen `_sy_cache_stale()`-Helper faktorisiert (Double-Checked-Locking
  erhalten).
- **`docs/test_status.md`** — fragile Buchungs-Erfolgs-/Fehler-Selektoren
  (`automation/worker.py::_read_booking_result`, Code-TODO) als offener Punkt
  getrackt (Produktions-Schreibpfad).
- Verwaiste, gelockte Worktree `queue-status-boxes` entfernt.

**Tests:** 201 → **199** — zwei Tests in `tests/test_state_contract.py`, die
ausschließlich die entfernten Forwarding-Shims prüften, wurden gelöscht; alle
`state_snapshot()`-Wire-Format-Assertions bleiben unangetastet. `ruff` clean.

**Prozess-Gotcha** (s. `_logs/2026-07-11_…` im Wiki): die parallelen
Isolation-Worktrees wurden von `547cb6a` (First-Parent von `ccdcbd9`) statt vom
Session-HEAD angelegt → zwei Agents refactorten veraltete Dateien und hätten
`queue_all` still gelöscht. Beim Merge via Feature-Marker-Grep erkannt, betroffene
Agents (ws/frontend) im Haupt-Baum neu ausgeführt.

## 2026-07-10 — Helferclient: aktive/fertige Schüler als Gruppen-Boxen unter der Warteschlange

Die Warteschlangen-Ansicht im Helferclient (`web/scan.js`, `renderQueue`)
zeigt jetzt zusätzlich zu den wartenden Schülern (unverändert je eigene Zeile
mit „Aufrufen"-Button) die gerade aufgerufenen (`status: "active"`) und
bereits fertigen (`status: "done"`) Schüler der gewählten Klasse — je Status
eine gemeinsame Box (blau/grün, `.queue-group`) statt einer Einzel-Box pro
Schüler wie bei den Büchern. Aktive Schüler haben keinen Button (bereits bei
einem Helfer); fertige lassen sich erneut aufrufen (z. B. um nachträglich
ein Buch zu erfassen) — Button wie bei den Wartenden. Abstände zwischen den
Boxen sowie zwischen den Namen innerhalb einer Box sind auf 7px
vereinheitlicht (wie zwischen den Steuer-Elementen der oberen Leiste,
`.top-bar`/`.gear-wrap`).

Serverseitig liefert `AppState.real_contexts_summary()` (`server/state.py`)
sowie die `waiting`/`queue_update`-Nachrichten (`server/sessions.py`,
`server/hub.py`, `server/routes/ws.py`) dafür zusätzlich zum bisherigen
`queue`-Feld (nur pending, für Tab-Badge/Status-Count unverändert) ein neues
`queue_all`-Feld mit allen Schülern des Kontexts (inkl. active/done/skipped).
Der `call`-WS-Handler erlaubt jetzt auch das Aufrufen bereits fertiger
Schüler (bisher nur `pending`), damit die Fertig-Box nutzbar ist.

## 2026-07-10 — Wartbarkeits-Wellen 0–4: Hygiene, Tests, Kommentar-Diät

Fünf Wellen Aufräumarbeit an Server/Automation/Web, ohne Verhaltensänderung
am Buchungspfad:

- **Welle 0 (Hygiene):** `.claude/` ignoriert, macOS-Artefakte entfernt,
  ruff-format-Pre-Commit-Hook eingerichtet, danach einmalig `ruff format`
  über `server/`, `automation/`, `tests/` laufen lassen; E501-Ignore auf
  `automation/` eingegrenzt statt global (`e9d603f`, `ab3f62f`, `23ff27d`).
- **Welle 1 (Bugfixes):** doppelte HTML-Maskierung durch `escapeHtml` in
  `textContent`-Zuweisungen entfernt (Host- und Schüler-Client,
  `db6452b`, `101c285`); Selbst-Deadlock in `_get_series_map` durch einen
  nicht-reentranten Lock behoben (`53d0fd4`).
- **Welle 2 (WS-Serialisierung):** alle WebSocket-Sends laufen jetzt über
  den Hub-Lock (`12b3777`), abgesichert durch einen Test, der konkurrierende
  Sends auf derselben Verbindung serialisiert nachweist (`a48bf24`).
- **Welle 3 (Web-Refactor):** `student.html`s Inline-JS nach `web/student.js`
  ausgelagert (`077167b`); `host.js` nutzt den gemeinsamen `Beeper` aus
  `common.js` statt einer eigenen Audio-Kopie (`66dbcef`).
- **Welle 4 (Nebenläufigkeits-Invarianten):** sieben zuvor nur in Prosa
  behauptete Concurrency-Garantien durch benannte Tests abgesichert
  (`4b9bf69`, `4a43fde`). Die Invarianten und ihr jeweiliger Grund:
  1. `_deferred_end` (ws.py) — ein Reconnect innerhalb der Grace-Frist ODER
     ein zwischenzeitliches Weiterschalten darf den verzögerten
     Schüler-Teardown NICHT mehr auslösen (Re-Checks auf `helper.ws` und
     `helper.student_id`).
  2. `ws_scanner`s `finally` (ws.py) — das `if helper.ws is websocket`-Gate
     verhindert, dass die alte Verbindung nach einem Reconnect den frisch
     übernommenen Schüler/Worker wieder abbaut.
  3. `load_and_push_helper_student` (sessions.py) — Stale-Guard vor
     `set_worker_session`: wurde der Helfer während `open_student` schon
     weitergeschaltet, muss der Context selbst geschlossen werden, sonst
     bleibt er als Orphan unter einer toten `student_id` im Pool hängen.
  4. `load_and_push_paired_student` (sessions.py) — dieselbe Garantie für
     Modus B (Prüfung auf `session.student_id`/`session.state`).
  5. `release_worker` + `_release_tasks` (sessions.py) — Release-Tasks
     werden in einem modulglobalen Set stark referenziert, weil
     `asyncio` Tasks sonst nur schwach hält; ohne das Set kann ein
     Fire-and-forget-Task mitten in der Coroutine GC't werden und der
     Context bleibt für immer draußen (bei `WORKER_CONTEXTS=2` genügen
     zwei stille Drains, um den Pool leerzuräumen).
  6. `WorkerPool.open_student` (worker.py) — beide Fehlerpfade
     (`new_page()`, `load_card()`) fangen `BaseException` statt
     `Exception`, weil `except Exception` `asyncio.CancelledError` seit
     Python 3.8 nicht mehr abfängt; ohne den weiten Fang würde ein
     Cancel (z. B. schnelles „Weiter") den Context aus dem Pool verlieren.
  7. `WorkerPool.release` — idempotent per Attribut-Nullung
     (`session._context = None`), damit ein doppelter Release (Race im
     Server-Code) nicht denselben Context zweimal in den Pool anhängt.

Die Kommentare an diesen sieben Stellen sind auf je einen Satz (die
Invariante im Präsens) plus einen Test-Verweis gekürzt; die vorherige
Regressions-Prosa ("ohne X würde Y passieren") lebt jetzt hier. Laut
CLAUDE.md gehört Änderungshistorie ausschließlich ins Changelog, nicht in
Code-Kommentare.

## 2026-07-10 — Welle 4b + 5: Kommentar-Trim vollzogen, AppState entflochten

Ergänzt die „Wellen 0–4"-Zusammenfassung oben um zwei weitere Schritte, die
im direkten Anschluss folgten:

- **Welle 4b (Vollzug):** Der in Welle 4 beschriebene Kommentar-Trim wurde
  umgesetzt — `b1b83f3` hielt die Absicht im Changelog fest, `35b269e` führte
  ihn im Code aus: netto **−34 Kommentarzeilen** über `server/routes/ws.py`,
  `server/sessions.py`, `automation/worker.py`. Die Regressions-Prosa lebt
  jetzt ausschließlich im Changelog (siehe oben); im Code bleibt je
  Invariante ein Satz (die Invariante im Präsens) plus ein Test-Verweis.
- **Welle 5 (State-Split):** `AppState` (`server/state.py`) trug 25 Felder
  über fünf Zuständigkeiten. `RuntimeSettings` (die fünf Host-/Entwickler-
  Toggles) und `IservCaches` (die fünf schuljahresbezogenen IServ-Caches)
  wurden als eigene Dataclasses herausgelöst — `AppState` behält nur noch
  17 direkte Felder plus 11 dünne Forwarding-Properties (nötig, weil
  `server/routes/settings.py::_BOOL_SETTINGS` per `setattr(state, attr,
  value)` auf die alten Attributnamen schreibt). Das Draht-Format von
  `state_snapshot()` bleibt dabei unverändert — vor dem Split per
  Charakterisierungs-Test eingefroren (`tests/test_state_contract.py`,
  `09e2ed5`); dieser Test darf bei künftigen Refactorings **nicht**
  angepasst werden, ein Fehlschlag bedeutet, dass sich das Draht-Format
  geändert hat (`0fef31d`).

Test-Suite: **187 → 201** grün.

## 2026-07-10 — Host: Sofort-fertig-Filter beim Klassen-Öffnen

Im „Neue Klasse öffnen"-Reiter vier Umschalter ergänzt: Schüler ohne aktuelle
Anmeldung, nicht bezahlt, Ermäßigungsantrag ohne Nachweis, Befreiungsantrag
ohne Nachweis. Beim Laden einer neuen Klasse (nicht beim Wieder-Aktivieren
eines bereits offenen Tabs) prüft `_apply_auto_done` (`server/routes/
classes.py`) jeden Schüler parallel per `get_student_info` (read-only,
schuljahrbezogen) gegen die gewählten Bedingungen und setzt Treffer sofort auf
Status `done` — nicht angemeldete Schüler zählen dabei ausschließlich für den
„Nicht angemeldet"-Filter (ohne Anmeldung liefert IServ keinen sinnvollen
Zahl-/Nachweis-Status). Die Auswahl wird im Browser (`localStorage`) gemerkt
und beim nächsten Öffnen vorbelegt (`OpenClassRequest.auto_done`).

## 2026-07-10 — Helferclient: Weiter-Button wandert ins Menü, Lupe zieht in die Warteschlangen-Kopfzeile

`#next-btn` ist jetzt in und außerhalb des Menüs dieselbe, immer sichtbare
Schaltfläche (kein Verschwinden bei leerer Warteschlange mehr): außerhalb
Kind von `.status-bar` wie bisher, im Menü per JS in `.top-section` umgehängt
und dort an die Stelle gesetzt, an der zuvor die Lupe saß (`grid-area: next`,
ersetzt die alte `search`-Spalte). Die Lupe (`#search-btn`) sitzt im Gegenzug
jetzt fest in der „Warteschlange"-Kopfzeile (rechts neben dem Titel) und
blendet dort rein per CSS-Opacity ein/aus, ohne Reparenting. Dazu einheitlicher
7px-Abstand zwischen dieser Kopfzeile und ihren Nachbarn (Statuszeile/Lupen-
Dropdown oben, Klassen-Reiter unten) — passend zum Abstand zwischen Statuszeile
und Menü-/Weiter-Button.

Beim Umbau zwei FLIP-Animations-Bugs behoben: (1) die alte Button-Position
wurde nach statt vor dem Klassen-Toggle gemessen, wodurch der Button ohne
sichtbare Bewegung an sein Ziel sprang; (2) da der Button beim Schließen des
Menüs Kind eines selbst FLIP-animierten Elements (`.status-bar`) wird, addierte
sich sein eigener Transform zum ererbten — er schoss weit über die Zielposition
hinaus statt sanft mitzuwandern. Details + wiederverwendbare Faustregel:
`~/wiki/_logs/2026-07-10_sba_helfer_weiter_lupe_swap.md`.

Reiner UI-Fix im Helferclient (`web/scan.html`, `web/scan.js`), kein
Verhaltenseingriff auf dem Buchungspfad. Commit `de59af6`.

## 2026-07-10 — Scan-Client: Alert-Farbe der Statuszeile bleibt am Alert-Text

`web/scan.js` toggelte `status-book-deleted` (rot/fett) auf `#status-text`
direkt neben etlichen `textContent`-Zuweisungen, ohne die Klasse an anderer
Stelle zuverlässig zurückzunehmen — nach einem Alert (ausgemustert/an
jemand anders verliehen) blieb die Formatierung teils auf nachfolgenden,
harmlosen Statustexten (z. B. „Gesendet: `<Code>`") hängen.

Neuer zentraler Setter `setStatusText(text, isAlert = false)` setzt Text und
Klasse in einem Schritt; alle ~25 Zuweisungsstellen laufen jetzt darüber.
Die Alert-Formatierung gilt damit nur noch für den einen Aufruf im
`scan_result`-Handler, der sie mit `isAlert = true` explizit anfordert —
jeder andere Statustext setzt automatisch die normale Schrift zurück.

Reiner UI-Fix, kein Verhaltenseingriff auf dem Buchungspfad.

## 2026-07-09 — `_read_booking_result`: DOM-Annahme geklärt, Selektoren bereinigt

Auswertung des DOM-Dumps `automation/out/06b_kartei_geladen.html` klärt die
zuvor als offen geführte Frage zum `has_not`-Filter:

- `input.tt-input` liegt in einem `<form>` oberhalb der Tabellen; **keine** der
  16 `<tr>` enthält ein `<input>`. Der Filter ist im heutigen DOM ein No-op.
- Der befürchtete False-Positive kann trotzdem nicht eintreten: der Erfolgs-Check
  liest `inner_text()`, und der Wert eines `<input>` ist kein Textknoten. Der
  Filter stammt aus einer Implementierung mit `get_by_text(barcode)` über die
  ganze Seite. Er bleibt — als Schutz gegen Selektor-Drift (Typeahead-Dropdowns
  rendern echte Textknoten).
- `.books-list`, `.lent-books`, `.student-books` kamen im DOM nicht vor und sind
  entfernt. Es bleiben die zwei verifizierten Selektoren, die dieselben
  `<tr ng-repeat="book in bl.books">`-Zeilen treffen. Weniger Kandidaten kann eine
  Erkennung höchstens von `booked` auf `unknown` kippen — die sichere Richtung.

Der Eintrag in `docs/test_status.md` war entsprechend zu alarmistisch und ist
korrigiert. Neu dort als offen geführt: der Substring-Vergleich gegen den ganzen
Zeilentext (statt gegen die Code-Spalte) und das feste `wait_for_timeout(1500)`.
Beide zeigen Richtung `unknown`, nie Richtung `booked`; eine Änderung im scharfen
Buchungspfad nur mit Freigabe (PLAN §6). Kein Verhaltenseingriff in diesem Commit.

## 2026-07-09 — Wartbarkeits-Refactoring (ruff, Modularisierung, Testabdeckung)

Sieben Commits, reines Aufräumen — keine neuen Endpoints, keine Feature-Änderung
außer den beiden unten markierten Verhaltensänderungen.

- **Linter eingezogen** (`39c94f9`): `ruff` (E/F/W/I/B/UP/SIM) + `.pre-commit-config.yaml`;
  vorher gab es keinen. 38 Findings automatisch behoben, 22× `raise … from e/None`
  ergänzt. `E501`/`SIM105` bewusst ignoriert (Begründung in `pyproject.toml`).
- **Toter Code entfernt** (`b7ac0cc`): `/api/select-class` + `/api/add-test-students`
  hatten keine Aufrufer mehr. Damit fiel der als Strangler-Pattern markierte
  AppState-Kompat-Layer (`queue`/`active_form`/`book_order`/`class_catalog*`-
  Properties, `ClassContext.implicit`, `ensure_active_context`) weg. Neu:
  `AppState.book_order_of(context_id)` und `AppState.active_students()`.
  - **Verhaltensänderung:** `book_order_of()` liefert `[]` für einen Kontext
    ohne eigene Reihenfolge statt — wie der Kompat-Layer es tat — still auf die
    gerade aktive Klasse zurückzufallen. Ein Helfer ohne Klassenbindung bekam
    dadurch bisher unbemerkt die Buchreihenfolge einer fremden, zufällig
    aktiven Klasse; jetzt bekommt er eine leere Liste (Client rendert dann in
    Server-Sortierung).
  - **Bugfix:** Der Guard in `/api/select-schoolyear` prüfte nur die Queue des
    aktiven Klassen-Tabs. Aktive Schüler in anderen, nicht-aktiven Tabs wurden
    beim Schuljahreswechsel ohne Warnung abgerissen. `AppState.active_students()`
    iteriert jetzt alle Kontexte, nicht nur den aktiven.
- **Frontend entflochten** (`d66e2e9`): neu `web/common.js` (`escapeHtml`,
  `isBookDone`, `Beeper`, gemeinsames `connectWebSocket`); `web/host.html`
  (2167 Zeilen) aufgeteilt in `web/host.html` (221 Zeilen) + `web/host.js` +
  `web/host.css`. Weiterhin kein Build-Step.
- **Dokumentation strukturiert** (`a0ccb72`): `docs/CHANGELOG.md` neu angelegt;
  `docs/PLAN.md` 993 → 675 Zeilen, `docs/test_status.md` 619 → 461 Zeilen
  (Chronologie-Prosa ausgelagert ins Changelog).
- **Server-Duplikate entfernt** (`84ad84c`): `hydrate_student_info()`,
  `_detach_helper()`, `_grade_and_catalog()`, `QueueStudent.from_iserv()`
  waren mehrfach implementiert bzw. inline dupliziert.
- **API-Schicht umgebaut** (`7dc1f67`, `a7a75b4`): `require_host` ist jetzt eine
  FastAPI-Dependency auf einem `host_router` statt 30× wiederholter Cookie-
  Boilerplate; ~20 Pydantic-Request-Models ersetzen die manuelle Body-Validierung.
  Die drei Dev-Bool-Toggles laufen jetzt über `POST /api/settings/{key}`
  (Whitelist) statt eigener Endpunkte. `server/routes/api.py` (1425 Zeilen) ist
  in neun Module aufgeteilt (`_deps.py`, `auth.py`, `classes.py`, `booklists.py`,
  `helpers.py`, `queue.py`, `slips.py`, `modus_b.py`, `settings.py`); `api.py`
  bleibt als Aggregator/Re-Export, `server/app.py` unverändert.
  - **Verhaltensänderung:** Validierungsfehler bei Request-Bodies liefern jetzt
    HTTP 422 statt 400 (Pydantic-Standard). Kein bestehender Client wertete den
    400er-Statuscode aus, daher unkritisch. Die strukturierten 409-Responses
    (`active_sessions`/`blocked`) und die Buchungs-Gates sind unverändert;
    `confirm` bleibt bewusst `bool = False` statt Pflichtfeld, damit ein
    fehlendes `confirm` weiterhin NACH dem `ALLOW_BOOKING`-Gate abgewiesen wird
    (403 vor 400/422) — empirisch mit Spion-Worker nachgeprüft.
- **Testabdeckung ausgebaut** (`d17ee5b`): 158 → 187 Tests. Neu
  `tests/test_stale_guards.py` (Stale-Guards Modus A/B), `tests/test_ws_scanner.py`
  (WS-Message-Dispatch: `call`, `search_call`, Peek-Toggle, malformed Frame),
  `tests/test_booking_result.py` (`_read_booking_result`, inkl. Typeahead-
  False-Positive-Schutz). Coverage gesamt 47 % → 59 %, `routes/ws.py` 13 % → 38 %,
  `sessions.py` 65 % → 74 %. Jeder neue Guard-Test hat eine Mutationsprobe
  bestanden (Guard-Zeile auskommentiert → Test rot → zurückgenommen).
- **Kommentar-Historie aufgeräumt** (`b1c1d59`): Datums-/Freigabe-Marker und
  Vorher-Nachher-Erzählungen aus Code-Kommentaren entfernt (leben jetzt hier im
  Changelog bzw. im Git-Log). Invarianten, Race-Condition-Hinweise und alle
  Produktionsschutz-/`noqa`-Begründungen blieben unangetastet — verifiziert per
  AST-Vergleich, kein ausführbarer Code geändert.

Ergebnis: 187/187 Tests grün, ruff sauber, kein produktives Verhalten außer den
zwei oben markierten Punkten geändert.

## 2026-07-09 — Menü-Icon-Animation, Warteschlangen-Überschrift

Menü-Icon: drei Balken → Linkspfeil (←) beim Öffnen, auf derselben
`.35s cubic-bezier`-Kurve wie der Menü-FLIP; `prefers-reduced-motion`
respektiert (.01ms). CSS-only, kein JS.

Warteschlangen-Überschrift `qh-title` übernimmt die Schrift des
Schülernamens (`.s-name`: 1.5rem/700/line-height 1.2) — keine
Kapitälchen/Sperrung/Transparenz mehr.

## 2026-07-09 — Animations-Sync Peek-Menü

Beim Öffnen/Schließen faden die ausgeblendeten Steuer-Elemente
(gear/reader/right-col/print/next) und die Lupe (`#search-btn`) jetzt
synchron mit der Statuszeilen-FLIP-Bewegung aus/ein — alles
`.35s cubic-bezier(.22,.61,.36,1)`, beide Richtungen. `flipAnimate` →
`animateMenu(open)`: Steuer-Elemente werden per `position:absolute` an
alter Stelle festgepinnt (aus dem Fluss → Grid kollabiert weiter) und per
`opacity` gefadet; Lupe öffnet per CSS-Opacity, schließt per Pin.
`print`/`next` (in `.status-bar`, das der FLIP per `transform` versieht)
werden für den Übergang ins nicht-transformierte `.top-section`
umgehängt — sonst reiten sie auf dem Transform und machen dessen
diskreten x-Sprung (full-width→Mittel-Spalte) mit. Generation-Guard +
Reset fangen schnelles Toggeln ab. Headless verifiziert (Playwright):
kein JS-Fehler, Layout-Kollaps real (Statuszeile 125→7 px), print/next
nach Zyklus wieder in `.status-bar` in Reihenfolge, keine Inline-Reste.
Live am Gerät offen.

## 2026-07-09 — Scanner: Reconnect stellt auch Lupe-Schüler wieder her + schneller Worker-Reload

Wird die Helferclient-Seite neu geladen, stellt der Reconnect-Pfad
(`server/routes/ws.py` `ws_scanner`) den aktuell geladenen Schüler wieder
her und lädt die Kartei im Worker neu (`StudentSession.reload()` →
`worker_ready`). Zwei Lücken/Verbesserungen:

- **Lupe-Schüler (`search_call`)** ging bisher beim Reload verloren: er
  ist bewusst **nicht** in einer Queue eingetragen, also lief
  `state.find_student` None → der Reconnect sendete `waiting`, der Schüler
  war weg, der Worker wurde **nicht** neu geladen. Fix:
  `HelperSession.student_form` speichert die Klasse beim Zuweisen
  (`assign_student_to_helper`); der Reconnect nimmt die Form daraus, falls
  `find_student` None liefert, und durchläuft dann auch für den
  Lupe-Schüler den Wiederherstellungs-+Worker-Reload-Pfad. `end_student`
  räumt `student_form` in beiden Zweigen mit auf. (Hintergrund/Peek ist nur
  eine Ansicht — beim Reconnect kommt der Schüler ohnehin als aktiv
  zurück, `helper.peeking` wird auf False gesetzt.)
- **`StudentSession.reload()` beschleunigt**: Angular steht auf der
  bereits geöffneten Page → kein App-Root-Load (~4 s) mehr. Stattdessen
  Hop auf `#/counter` (erzwingt echten Re-Render — gleicher Hash allein
  wäre ein Angular-No-Op ohne frische Buchdaten) und zurück auf
  `#/counter/student/<id>`, beides In-App-Hashrouten via `_goto_authed`
  (inkl. Re-Login-Recovery). Sicherer Fallback auf vollständiges
  `load_card()` (Root + Schüler-Route), falls das Barcode-Feld nicht
  erscheint. `load_card` (frisches `open_student`) bleibt unverändert —
  dort muss Angular von der Root initialisiert werden (Spike B). Nur
  GET-Routen, kein `page.reload()` (kein Post-Re-Post-Risiko).

Unit-Suite: `uv run pytest` **149 grün**. `tests/test_scanner_reconnect.py`
reload-Tests an neue Goto-Sequenz (`#/counter` → Schüler-Route, Fallback
`load_card`) angepasst (Re-Login/Timeout/fehlendes-Re-Login/
Schüler-Route-Redirect). `tests/test_queue_flow.py` +1
(`assign_student_to_helper` setzt `student_form` für Queue- wie
Lupe-Schüler; Advance wechselt die Form mit) sowie `student_form`-Clear-
Assertionen im transienten `end_student`- und `assign`-Test.

Am Gerät (manuell, read-only, erst nach Freigabe — PLAN §6) offen: siehe
`docs/test_status.md`.

## 2026-07-09 — Host: „Test Config" als eigener Tab statt Sub-Reiter

Der „Test Config"-Sub-Reiter im „Schüler hinzufügen"-Bereich jedes
Klassen-Tabs entfällt; stattdessen bietet das „+"-Menü (`panel-new`) neben
„Neue Klasse öffnen" jetzt eine zweite Karte „Test Config öffnen". Klick
öffnet einen eigenen, dedizierten Tab (Pseudo-Klasse `Test Config`, kein
echter IServ-Code, kein Katalog-Abruf) und befüllt ihn **sofort** mit den
festen Testschülern. Erneutes Öffnen (weiterer Klick, oder Reload)
reaktiviert denselben Kontext statt eine zweite Queue anzulegen (Dedup
über `ctx.form`, analog `/api/open-class`). „Schüler hinzufügen" in
normalen Klassen-Tabs bleibt unverändert bei „Einzelne Schüler" (jetzt
ohne Sub-Tab-Leiste, da nur noch ein Inhalt).

Löst damit den früheren Reiter „Test Config" ab (2026-06-17, siehe
weiter unten): `TEST_STUDENTS`/`add-test-students` (IDs, Idempotenz-Test)
bleiben unverändert gültig.

- Backend: neue Route `POST /api/open-test-config` (`server/routes/api.py`,
  Konstante `TEST_CONFIG_FORM = "Test Config"`); nutzt weiterhin
  `TEST_STUDENTS`/`_load_test_students()`, aber ohne IServ-Roundtrip.
  Bestehende Route `POST /api/add-test-students` bleibt unverändert
  (weiter nutzbar, um Testschüler in **jeden** offenen Kontext
  nachzuziehen).
- Frontend (`web/host.html`): `panel-new` hat zweite Karte +
  `openTestConfig()` (spiegelt `openClass()`); `buildClassPanel()` ohne
  Sub-Tab-Leiste mehr, tote Funktionen `ctxAddTestStudents`/
  `ctxSwitchSubTab` + Dispatch-Cases entfernt.

Unit-Test: `tests/test_api_guards.py::test_open_test_config_populates_and_reuses`
— erster Aufruf befüllt mit allen `TEST_STUDENTS`, zweiter Aufruf
reaktiviert denselben Kontext (`reused: True`, kein zweiter Eintrag in
`state.contexts`). Suite grün (148 passed). `node --check` auf den
extrahierten `<script>`-Block → OK.

## 2026-07-09 — Scanner: Hinweis-Modal für JEDEN nicht-verbuchbaren Scan (beide Clients)

Bisher öffnete nur `book_deleted`/`not_in_stock`/`series_already_lent` ein
Hinweis-Modal; alle anderen nicht-OK Auswertungen (`not_enrolled` =
„nicht bestellt", `unknown_book` = „unbekannt", `not_ready` = „Buchliste
noch nicht geladen", `error` = Lookup/Client-Fehler) liefen nur als Text
in der Statuszeile mit. Jetzt öffnet **jeder** nicht-OK Scan ein Fenster
(gleicher Modal-Baukasten wie die bestehenden Alerts):

- **Schüler-Client (Modus B, `web/student.html`):** die drei
  sicherheitskritischen Fälle bleiben **Host-geschlossen** (blockierend,
  kein Schließen-Button, serverseitig `book_alert_open` blockiert weitere
  Scans, nur der Betreuer gibt per `book_alert_clear` frei) —
  `book_deleted` (ausgemustert, mit **und** ohne Ersatzanspruch, d. h.
  `loaned_to` spielt keine Rolle für die Schließ-Logik) **und**
  `not_in_stock` (an andere Person verliehen). **Alle übrigen nicht-OK
  Status** (`series_already_lent`, `not_enrolled`, `unknown_book`,
  `not_ready`, `error`) schließt der Schüler **selbst** (Schließen-Button
  **oder** nächster Scan) und scannt weiter — der bestehende
  close-on-next-scan-Pfad greift für jeden dismissiblen Hinweis. Neue
  Hilfs-Sets `OK_STATUSES_STUDENT` (`staged`/`booked`) und
  `BLOCKING_STATUSES_STUDENT` (`book_deleted`/`not_in_stock`);
  `dismissible = !ok && !blocking`.
- **Helfer-Client (Modus A, `web/scan.js`):** **jedes** nicht-OK Modal ist
  am Gerät schließbar (Button / Klick außerhalb / Escape / nächster
  Scan); `dismissBookAlert` beim nächsten Scan räumt ggfls. die
  Host-Meldung auf (`clear_book_alert`), bei Status ohne Host-Broadcast
  (alle neuen + die Selbst-Leihe) ist das Clear ein No-op. `OK_STATUSES`
  statt der alten `ALERT_STATUSES`-Menge.

Beide Clients: `ALERT_META` um Titel/Farbe für die neuen Status ergänzt
(orange = Hinweis: `not_enrolled`/`not_ready`/`series_already_lent`; rot =
Fehler: `unknown_book`/`error`). Rein client-seitig — Server-Pfad
(`evaluate_scan_for_booking`, `process_scan`, `book_alert`-Broadcast) und
IServ/DB unangetastet (read-only, kein GET mehr als bisher, kein Write).
`node --check` OK; manuelle Geräte-Verifikation offen. Commit `eba6071`.

## 2026-07-09 — Host: Tabs & Einstellungen global — Server-State statt localStorage

Offene Klassen-Reiter und Einstellungen sind jetzt auf jedem angemeldeten
Host-Rechner sichtbar/synchron. Quelle der Wahrheit = der bereits globale
In-Memory-Serverstate (`state_snapshot` + `broadcast_host`), nicht mehr
pro-Browser `localStorage`. `web/host.html`: `tabOrder` in `applyState`
aus `state.contexts` abgeleitet; `activeTab` rein pro Bediener
(In-Memory, nicht persistiert — *Menge* offen global, *Fokus* pro
Browser); Dev-Toggles (PDF-lokal, Klasse-korrigieren, Schüler-Leihschein)
aus `state` spiegeln statt localStorage; Login pusht nicht mehr lokal →
Server. `server/routes/api.py`: `/api/slip-default` broadcastet
zusätzlich `broadcast_host`. **Theme (Auto/Hell/Dunkel) bleibt bewusst
pro Browser in localStorage.** Keine IServ-/DB-Writes; nur App-eigene
In-Memory-Endpunkte. Commit `0e39cd5`.

Unit-Suite: `uv run pytest` **145 grün** (keine Logik auf
Server-Modellebene geändert; `state_snapshot` unverändert;
1-Zeilen-Broadcast-Zusatz in `/api/slip-default` wird von keiner
Bestands-Assertion getroffen). `grep localStorage web/host.html` → nur
noch `theme` (cycleTheme/applyTheme).

## 2026-07-09 — Scanner: Lupen-Suche — Schnellsprung zu beliebigem Schüler

Peek-Modus (`scan.js`): die **Lupe** öffnet ein Such-Panel unter der
Statuszeile — Warteliste fährt per FLIP nach unten, zwei Dropdowns
blenden synchron ein (oben Klasse, unten Schüler der gewählten Klasse).
Schüler wählen → `search_call` lädt ihn (ersetzt den
Hintergrund-Schüler). Letzte Klasse wird beim erneuten Öffnen
vorausgewählt (`localStorage`), änderbar. **Read-only** (nur IServ-GETs).

Backend: neue WS-Nachrichten `search_classes`/`search_students` (IServ
`get_class_names`/`get_students_for_form`, schuljahrbezogen im
`state.class_names_cache`/`form_students_cache` gecacht, geleert im
Schuljahreswechsel) + `search_call` (transienter `QueueStudent`, **nicht**
in einer Queue, laden via `assign_student_to_helper`). `end_student`
räumt auch nicht-gequeuete Schüler auf (neuer `else`-Zweig via
`find_helper_for_student`). Unit-Suite grün (145 passed; +2 Tests in
`tests/test_queue_flow.py`: transienter `end_student` + transienter
`assign_student_to_helper`). `node --check web/scan.js` OK;
Server-Imports OK.

## 2026-07-09 — Helfer-Menü: Klassen-Reiter für alle offenen Host-Klassen

Im Peek-Modus (`web/scan.js`/`scan.html` + Server-WS) zeigt das
Helfermenü jetzt **Reiter für alle offenen Host-Klassen** (alle
nicht-impliziten `state.contexts`), horizontal scrollbar; eigene Klasse
vorausgewählt, sonst erste offene. Pro Reiter darunter die Warteschlange
dieser Klasse mit „Aufrufen"-Button (wie bisher). Der im Hintergrund
verbundene Schüler steht im Peek **nur in der Statuszeile**, die große
`.name-row` ist verborgen. Aufrufen aus einer **fremden** Klasse rebindet
den Helfer an diese Klasse (`helper.context_id` wechselt; danach zieht
„Nächster" aus der neuen Klasse) statt abzuweisen. Die Lupe bleibt
unverhalten zusätzlich. Commit `8bf6c08`.

Backend: `state.real_contexts_summary()` (alle offenen Klassen + je
wartende Schüler); `hub.broadcast_queue_size` sendet zusätzlich
`contexts_update` (`{contexts, own_context_id}`, pro Helfer) an denselben
Kreis (`student_id is None or peeking`), `queue_update` bleibt bestehen;
`routes/ws.py`: `contexts_update` bei Connect + `peek_queue`; `call` aus
fremder Klasse rebindet statt Fehler (`rebind_helper_to_context` in
`sessions.py`). Unit-Suite grün (147 passed; +1 in `tests/test_hub.py`
`contexts_update`-Broadcast, +1 in `tests/test_queue_flow.py` Rebind).
`node --check web/scan.js` OK; Server-Imports OK.

**Nachbesserung (Commit `9b11c75`):** Der aktive Reiter ist „nach unten
offen" (Host-Stil: Basis-Linie + 3-seitiger Rahmen ohne Unterkante → geht
in die Queue über), und bei jedem Öffnen des Menüs wird die eigene
Klasse (re-)selektiert (manuelle Reiter-Wahl bleibt nur bis zum
Schließen). Tests: 147 grün.

**Nachbesserung:** Ist keine Klasse offen, steht „Keine Klasse offen" nur
an Stelle der Klassen-Reiter (`renderQueueTabs` in `web/scan.js`), nicht
noch einmal darunter in der eigentlichen Warteschlange (`renderQueue`
lässt die Liste leer, statt den Text zu wiederholen).

## 2026-07-09 — Helfer-Menü: Menü-Button im Idle nutzbar

Das Hamburger-Menü ist jetzt auch **ohne zugewiesenen Schüler** (Idle)
funktionsfähig (Commit `9d5f413`). Es klappt im Idle lediglich die
Kamera-Zeile ein (Fokus auf die ohnehin sichtbare Warteschlange) und
fährt sie wieder aus — **kein Server-Roundtrip** (`peek_queue`/
`peek_close` entfallen), `queue-view` bleibt durchgehend an
(`keepQueueView`-Flag an `animateMenu`). Die Lupe ist im Idle-Menü
ebenfalls nutzbar (`search_call` funktioniert serverseitig auch ohne
aktuellen Schüler). Rein client-seitig (`idleMenuOpen`-Flag in
`web/scan.js`); keine neuen WS-Typen, kein Server-/DB-/IServ-Zugriff. Das
Burger-Icon morphet synchron mit dem Menü-FLIP zu einem Linkspfeil (←).
`node --check web/scan.js` OK; keine Server-Änderung.

## 2026-07-08 — Serverseitige Persistenz der Buchreihenfolge/Ausblendung

`book_orders_by_grade` + `hidden_isbns_by_grade` waren bislang reiner
In-Memory-State (weg beim Neustart). Neues `server/booklist_store.py`
speichert beide als einzelner globaler Satz in
`data/booklist_settings.json` (atomar, `data/` gitignored). Startup lädt
sie (`app.py` lifespan, non-fatal); `POST /api/booklist-order`/
`POST /api/booklist-hidden` schreiben nach jeder Mutation weg.
Schuljahreswechsel wischt die Konfiguration **nicht mehr** — nur
`form_catalog_cache` (ISBNs jahresspezifisch); `reset_booklist_orders()`
bleibt als Utility. ISBN-Drift zwischen Schuljahren fängt
`normalize_book_order` + `hidden & catalog` beim Lesen ab: neue
Katalog-Bücher sichtbar ans Ende, weggefallene gedroppt. Tests:
`tests/test_booklist_store.py` (+8; Round-Trip, fehlende/korrupte Datei,
data-Dir-Anlage, deterministische Serialisierung, neue-ISBNs-ans-Ende,
Nicht-String-Einträge gedroppt); Suite grün. Schreib-/Ladefehler
non-fatal (In-Memory-State bleibt Leading). Manueller Smoke am Gerät
offen (Neustart → Konfiguration wieder da).

## 2026-07-08 — Host-Überarbeitung: Settings + Tab-System (Multi-Kontext-Refactor)

Multi-Kontext-Refactor des Hosts (`web/host.html`) + Backend
(`server/state.py`, `routes/api.py`, `ws.py`, `sessions.py`, `hub.py`).

- **Backend-Kontext-Modell** (`state.py`): `ClassContext`, `contexts`-Dict,
  `active_context_id`, Kompat-Properties (`queue`/`active_form`/
  `book_order` delegieren an aktiven Kontext), `find_student`/
  `find_student_with_ctx` suchen über alle Kontexte, `next_pending`/
  `pending_count`/… nehmen `context_id`. `HelperSession.context_id` neu.
  Unit-Suite grün (143 passed) — bestehende Tests laufen über die
  Kompat-Properties weiter.
- **Routen-Migration**: `/api/open-class`, `/api/close-class`,
  `/api/set-active-context`, `/api/helper/{token}/class` neu;
  `add-student`/`add-test-students`/`disconnect-all`/`reset-queue`/
  `clear-queue` nehmen `context_id` im Body; `next-student` zieht aus
  `helper.context_id`; Scanner-WS-Handler (`peek_queue`, Waiting-Msg,
  `call`-Guard) kontextbewusst. Suite grün (143 passed).
- `node --check` auf den extrahierten `<script>`-Block → OK;
  Server-Imports (`server.main`/`routes.api`/`routes.ws`/`hub`/
  `sessions`/`state`) sauber.

Offene Teile (Frontend-Tab-Chrome, Klassen-Tab pro Kontext,
Helfer-Klassen-Bindung, E2E-Skript-Migration) siehe
`docs/test_status.md`.

## 2026-07-08 — Helferclient: Menü-Toggle / Peek zwischen Schüler- und Warteschlangen-Ansicht

Hamburger-Menü (≡) schaltet bei zugewiesenem Schüler auf die
Warteschlangen-Ansicht, **ohne** ihn zu trennen — er bleibt im
Hintergrund verbunden, Statuszeile zeigt ihn (`renderPeekStatus`),
Name/Zeile bleibt sichtbar. Nochmal Drücken kehrt zur Bücherliste zurück.
Im Peek werden Scans ignoriert. WS `{type:'peek_queue'}`/
`{type:'peek_close'}` + transient `helper.peeking` (Server) steuern
Live-`queue_update`s (`broadcast_queue_size`:
`student_id is None or peeking`).

- **Aufrufen eines anderen Schülers aus der Peek-Ansicht** legt den alten
  als **`pending`** (wartend) zurück in die Warteschlange, **nicht** als
  `done` — `call`-Handler `end_student(queue_status="pending",
  session_state="revoked")` (analog Disconnect-Teardown `_deferred_end`).
  „Weiter" (`next`/`advance_helper`) schließt den alten weiter als
  `done`.
- Scheitert der Aufruf (Schüler inzwischen von anderem Helfer genommen),
  kehrt der Client automatisch in die Peek-Ansicht zurück (kein
  „Schüler wird geladen …"-Stuck).

Unit: `tests/test_hub.py` +1 (Peek-Helfer erhält `queue_update`),
`tests/test_queue_flow.py` +2 (`end_student`/`assign_student_to_helper`
resetten `peeking`); Suite **133 grün**; `node --check` OK. Live am Gerät
offen (read-only, kein Enter — Niklas+Lukas-Freigabe).

## 2026-07-07 — Helferclient: Ausleih-Freigabe-Dialog bei Unstimmigkeit (O10)

Im Helferclient (`web/scan.js`/`scan.html`) wird beim ersten Buch-Scan
eines Schülers mit `remission_pending`/`exemption_pending`/`!paid`
(jeweils nur bei `enrolled`) der Scan zurückgehalten und ein
Bestätigungsdialog (Bauform wie Druck-Dialog) mit gelisteter
Unstimmigkeit gezeigt, **bevor** server-seitig
`evaluate_scan_for_booking` (Lager/angemeldet) + Worker-Eintragung
laufen.

- **„Ja, ausleihen"** → Scan geht raus, Flag `lendingApproved` merkt die
  Freigabe bis zum Neuladen des Schülers (`student_info`/`loading`/
  `waiting` resetten es) → weitere Bücher nicht mehr angefragt.
- **„Nicht ausleihen"**/Escape/Click-außerhalb → Scan verwirft, Flag
  bleibt false → nächster Scan fragt erneut.

Nur GET (`student_info`-Flags kommen ohnehin vom Server), kein
DB-/IServ-Schreibzugriff, keine Host-Benachrichtigung (bewusst
ausgeblendet). Analog zu Modus-B-O6, aber am Helfer-Client statt
Host-Pairing. Manuell verifiziert; kein automatisierter Test (UI-Gate).
Live am Testschüler mit künstlicher Unstimmigkeit offen (read-only —
Niklas+Lukas-Freigabe).

## 2026-07-07 — Bugfix: „Reihe an dich ausgeliehen" bei ausgeblendeten Reihen UND nach Buchung in derselben Session

Zwei Lücken im Erkennen „Buch bereits an dich selbst verliehen"
(`series_already_lent`), die beide denselben Symptom-Pfad hatten — ein
Scan des *eigenen* Exemplars fiel zu `not_in_stock` und deklarierte es
fälschlich als „verliehen an jemand anderes".

1. **Ausgeblendete Buchserie, die der Schüler bereits hat.**
   `apply_hidden_books` entfernt eine ausgeblendete Reihe nur aus
   `info["books"]`, **nicht** aus `info["current_books"]`. Bisher baute
   `booking_isbn_sets_from_info` die `lent`-Menge aus `info["books"]`
   status-basiert auf → eine ausgeblendete, aber bereits ausgeliehene
   Reihe fehlte in `lent` → der Scan des eigenen (durch `distributed`
   gekennzeichneten) Exemplars lief auf die Lager-Prüfung auf
   (`not_in_stock`). Fix: `lent` wird **autoritativ aus
   `info["current_books"]`** (ungefiltert) gebildet; nur falls
   `current_books` fehlt (Unit-Test-Fixture), wird auf die
   status-basierte Menge aus `info["books"]` zurückgefallen.
   `current_books` ist in echten `info`-Payloads aus
   `get_student_info` stets vorhanden.
2. **In derselben Session frisch gebuchtes Buch.** Nach einer Buchung
   (`status == "booked"`) ist das Exemplar serverseitig `distributed` an
   den Schüler, aber `lent_isbns` stammt noch aus der Lade-Zeit (ISBN
   steht dort in `vormerk_isbns`). Ein erneuter Scan desselben Exemplars
   — oder eines weiteren Exemplars derselben Reihe — in derselben Session
   (ohne Schüler-Neuladen) lief deshalb ebenfalls auf `not_in_stock` (mit
   `loaned_to` = Schüler selbst). Fix: `process_scan` hängt nach `booked`
   die ISBN von `vormerk_isbns` nach `lent_isbns` um. Die übergebenen
   Mengen sind die Session-Mutables (passed-by-reference) — das Update
   greift am Helfer- bzw. Schüler-Session-State direkt, ein Neuladen ist
   nicht nötig.

Beide Fixes sind reine read-only-Logik (kein IServ-/DB-Write, keine neuen
Endpunkte). **Lesson:** eine „ist das Buch an dich ausgeliehen"-Prüfung
muss die *ungefilterte* Buchliste des Schülers sehen — ein UI-Filter, der
Reihen für die Anzeige/Tabelle ausblendet (`apply_hidden_books`), darf
nicht die autoritative Quelle für den Verliehen-Status sein; und ein
serverseitiger Zustandswechsel (Buchung) muss die gecachten Prüf-Mengen
der Session mitschreiben, sonst veraltet der Cache bis zum nächsten
Neuladen. Tests: `tests/test_booking_precheck.py` +2
(`test_lent_from_current_books_ignores_hidden_filter`,
`test_process_scan_booked_isbn_moves_to_lent`), Suite 107 grün.
Live-Verifikation am Testschüler offen. Details:
`_logs/2026-07-07_sba_reihe_an_dich_erkannt.md`.

## 2026-07-07 — Ersatzanspruch-Hinweis + Lager-Prüfung vor Bestell-Prüfung

Zwei aufbauende Änderungen an `evaluate_scan_for_booking`.

1. **Ersatzanspruch bei ausgemusterten Büchern mit Schülerbezug.** Ein
   `book_deleted`-Buch, das noch eine `student_id != null` trägt (z. B.
   `[not_timely]` verloren, `[unusable]` beschädigt), reicht `loaned_to`/
   `loaned_to_id` durch — Host + Helfer zeigen zusätzlich „Ersatzanspruch:
   …" (Toast, Now-Serving-Kästchen `ns-borrower`, Helfer-Modal-Borrower-
   Zeile), der **Schüler-Client sieht nur „ausgemustert"** (kein Name,
   kein Hinweis; `process_scan` strippt für `source="student"` wie bei
   `not_in_stock`). `web/scan.js`/`web/host.html` branchen das Wording am
   `kind`/`status` (`book_deleted` → „Ersatzanspruch …", sonst „verliehen
   an …"). Ablösend zur früheren Idee, `[not_timely]` wie verliehen mit
   „verloren"-Wording zu behandeln — solche Bücher bleiben auf dem
   `book_deleted`-Pfad.
2. **Lager-Prüfung VOR Bestell-Prüfung.** Neue Prüf-Reihenfolge:
   `deleted → series_already_lent → nicht-im-Lager (not_in_stock) →
   nicht bestellt (not_enrolled)`. Ein verliehenes Buch zeigt jetzt immer
   „verliehen", auch wenn der Schüler es gar nicht bestellt hat (früher
   kam „Nicht bestellt" durch). `series_already_lent` (ISBN ∈
   `lent_isbns`) bleibt **vor** `not_in_stock`, da das Exemplar an dich
   selbst verliehen sein kann (distributed) — sonst würde „verliehen an
   dich selbst" gemeldet; es greift auch bei lagernden Exemplaren einer
   schon ausgeliehenen Reihe. `book_deleted` bleibt erste Prüfung
   (Ersatzanspruch-Display).

Kein DB-/IServ-Write — nur read-only Flags + WS-Broadcasts. Tests:
`tests/test_booking_precheck.py` +8 (Ersatzanspruch: Durchreichung +
Helper/Student-Unterschied für `book_deleted`; Reihenfolge:
`not_in_stock`-vor-`not_enrolled`, `series_already_lent`-vor-
`not_in_stock`, `series_already_lent`-bei-lagerndem-Exemplar), Suite 100
grün. Commit `9551f4e` (Ersatzanspruch), Reihenfolge-Update folgt.

## 2026-07-07 — Lade-State bis Worker bereit (`worker_ready`)

Beim Aufrufen eines Schülers wurden bisher die komplette `student_info`
(inkl. Bücherliste) sofort gepusht und der Playwright-Worker erst danach
geöffnet (`open_student`, mehrere Sekunden Browser-Navigation) — die
Bücherliste/der „Scanner bereit"-Status erschienen, bevor der Worker
buchungsbereit war, und Früh-Scans liefen auf „Worker-Session nicht
bereit". Neue getrennte Push-Phase über die WS-Nachricht `worker_ready`
(signalisiert „Worker buchungsbereit, Scans frei"), client-spezifisch:

- **Modus A (`web/scan.js`):** `student_info` bleibt vollständig (Bücher
  sofort sichtbar). `worker_ready` (ohne Bücher-Payload) flippt nur
  Statuszeile von „Warten…" auf „Scanner bereit — Buch scannen" + gibt
  Scans frei. Bis dahin ignoriert `onScanSuccess` Scans clientseitig
  (früher „Wird geladen…"-Text → jetzt „Warten…" konsistent mit
  `workerPending`-Flag).
- **Modus B (`web/student.html`):** `student_info` künftig **ohne
  Bücher** (`books: []`, nur Name/Klasse/Bezahlt + `book_order`).
  `worker_ready` trägt die Bücherliste und flippt Status von „Wird
  geladen…" auf „Scanner bereit" + gibt Scans frei. Bücher-Bereich zeigt
  bis dahin Placeholder „Bücher werden geladen…"; `onScanSuccess`
  ignoriert Scans (wie der ausgemusterte-Buch-Block via
  `workerPending`).

Server: `load_and_push_helper_student` (Modus A) sendet `worker_ready`
nach `set_worker_session` (oder sofort ohne `worker_pool`); bei
Playwright-Fehler nur `error`, kein `worker_ready` (Worker nie bereit →
Scans bleiben ignoriert, Helfer hat Bücher schon).
`load_and_push_paired_student` (Modus B) sendet `student_info` ohne
Bücher + `worker_ready` mit Büchern; bei Fehler nur `error` (Bücherliste
bleibt aus, Host muss eingreifen). Stale-Guards in beiden Routinen senden
kein `worker_ready` (neuer Schüler wird separat geladen). Reconnect
(`routes/ws.py` ×2): `student_info` neu + `worker_ready`, wenn Worker
bereits in `state.student_worker_sessions` registriert oder kein
Lade-Task (`helper.load_task`/`session.load_task`) mehr läuft — sonst
liefert der Task es an die neue WS.

Nur GET / read-only — `get_student_info` (GET) + `open_student`
(Browser-Navigation ohne Submit), keine DB-/IServ-Writes, keine neuen
Endpoints. Tests: `tests/test_queue_flow.py` +Assertion (`student_info`
mit `books==[]` + `worker_ready` nach `_advance_and_drain`), Suite grün.
Live-Verifikation am Testschüler noch offen (read-only, braucht
Niklas+Lukas-Freigabe).

**Scanner-Reconnect-Grace (Modus A, gleicher Tag):** Das `finally` des
Scanner-WS ruft den Schüler-Teardown (`end_student`: Schüler `pending`,
Worker zu) nicht mehr inline auf, sondern verzögert als Task
(`_deferred_end`, `_RECONNECT_GRACE_S=3.0`). Lädt der Helfer die Seite
neu (Reconnect), cancelt der neue WS den Grace-Task, übernimmt
`helper.ws` synchron (vor jedem await — so erkennt das alte `finally` an
`helper.ws is websocket` den Reconnect und löst keinen Teardown aus),
lädt `student_info` (GET) neu und — falls der Worker bereits bereit
stand — `StudentSession.reload()` (Re-Navigation über `load_card`/
GET-Routen inkl. Re-Login-Recovery, bewusst KEIN `page.reload()` wegen
Post-Re-Post-Risiko) auf dem **bestehenden** Context, dann
`worker_ready`. Läuft der Lade-Task noch, liefert dieser `worker_ready`
selbst an den neuen WS (`student_info` steht schon). Re-Checks in
`_deferred_end` (`helper.ws` gesetzt bzw. `helper.student_id` ≠ Original)
machen den Task zum No-op, falls er doch durchläuft (Cancel-RC,
`/api/skip`, neuer Schüler, …). Echte Trennung (Tab zu, kein Reconnect) →
Teardown nach der Frist — so steht kein „active" auf einem toten
Helfer-Token (Modus-A-Queue-Einträge räumt der Sweeper nicht ab). Vorbild
war Modus-B `ws_student`, dessen `finally` die Session ohnehin nicht
abbaut. `Hub.send_websocket` serialisiert die Reconnect-Sends über das
Per-WS-Lock gegen den In-Flight-Lade-Task. Nur GET, kein DB-/IServ-Write.
Tests: `tests/test_scanner_reconnect.py` (14). Live am Gerät noch offen.

## 2026-07-07 — Bugfix: Scanner reagiert nicht auf Host-Trennung

`end_student()` löste die Helfer-Zuordnung serverseitig, informierte aber
nie den Scanner-WebSocket selbst — `web/scan.html` hat keinen
Host-State-Feed und reagiert nur auf gezielt gepushte Nachrichten. Betraf
„Trennen" **und** „Alle Verbindungen trennen". Fix: `end_student()`
schickt jetzt zusätzlich `hub.send_scanner(old_helper, {"type": "waiting",
...})` an den betroffenen Helfer. **Lesson:** jede neue serverseitige
Aktion, die einen Helfer-Zustand ändert, braucht einen expliziten
`send_scanner`-Push — ein `broadcast_host`-Aufruf allein erreicht den
Scanner nicht.

## 2026-07-07 — Warteschlange im Helferclient + gezielter Aufruf (`call`)

Bisher zeigte der Helfer-Scanner bei keinem zugewiesenen Schüler eine
*leere* Buchliste + in der Statuszeile nur die Warteschlangen-**größe**
(`queue_update` trug nur `queue_size`, nie die Einträge); „Weiter" nahm
den ältesten Wartenden (`next_pending`), ein *gezielter* Aufruf fehlte.
Neu: bei keinem Schüler zeigt der Buchlistenbereich die
**Warteschlange** — selbes Zeilenformat wie die Bücherliste, aber
**ohne Farbgebung**, mit **„Aufrufen"-Button** pro wartendem Schüler.
Klick ruft genau diesen Schüler gezielt auf (neuer WS-Handler
`{type:'call', student_id}`).

- **Server (read-only, nur lokale Helfer-Zuweisung — kein DB-/IServ-
  Write):** `state.pending_queue_as_list()` (nur `status='pending'`);
  `queue_update` + alle `waiting`-Nachrichten tragen jetzt die
  `queue`-Liste (nur an unzugewiesene Helfer); `assign_student_to_helper()`
  aus `assign_next_pending_to_helper` extrahiert (wird von „nächster" und
  „aufrufen" geteilt); `call`-Handler prüft `target.status == 'pending'`
  **atomar** (kein Await zwischen Prüfung und Zuweisung → kein
  Doppel-Aufruf zweier Helfer auf denselben Schüler), beendet ggf. den
  alten Schüler, weist den gezielten zu; bei Nicht-verfügbar `error` +
  sofortiger `queue_update`-Push.
- **Client (`web/scan.js`/`scan.html`):** `renderQueue()` rendert
  `.queue-row` (transparent, keine `row-vorgemerkt`/`row-ausgeliehen`-
  Tint) mit `.call-btn`; delegierter Klick-Handler sendet
  `{type:'call', student_id}`.

Nur GET / read-only, keine DB-/IServ-Writes, keine neuen
REST-Endpoints. Tests: 105 grün (+2 in `test_queue_flow.py`:
`assign_student_to_helper` gezielt, `pending_queue_as_list`; 2 angepasste
Assertions wegen neuem `queue`-Feld). Live-Verifikation am Testschüler
offen. Details: `_logs/2026-07-07_sba_helfer_queue_anzeige.md`.

**Bugfix (gleicher Tag) — Queue während des Schüler-Ladens verbergen
(auch „Weiter"):** die Queue darf nur erscheinen, wenn *weder* ein
Schüler geladen ist *noch* gerade einer geladen wird. Erster Entwurf
flaggte nur den „Aufrufen"-Klick (`awaitingCall`) — bei „Weiter" (`next`)
stand der nächste Schüler schon fest, aber `student_info` fehlte noch;
in diesem Fenster konnte eine späte `queue_update` die Queue wieder
aufblitzen lassen. Generalisiert: `awaitingCall` → `loadingStudent`,
gesetzt in **beiden** Pfaden (`advanceToNext` für `next` UND
Aufrufen-Klick für `call`); Queue rendert nur bei `!studentActive &&
!loadingStudent`; freigegeben bei `student_info`/`waiting`/`error`.
**Lesson:** ein Lade-Flag vor der ersten Server-Bestätigung muss *jede*
Aktion abdecken, die `student_info` nach sich zieht — nicht nur den neu
eingeführten Pfad.

**Bugfix (gleicher Tag) — Queue während des Schüler-Ladens verbergen,
auch bei Host-„Nächster":** das reine Client-`loadingStudent`-Flag
reichte nicht — der Host-„Nächster"-Button (`/api/next-student`)
triggert `advance_helper`/Zuweisung serverseitig, ohne dass der
Helfer-Client davon weiß; und das `waiting`, das `end_student` beim alten
Schüler schickt, renderte die Queue („Warteschlange angezeigt, obwohl
schon ein neuer Schüler geladen wird"). Neue WS-Nachricht
`{"type":"loading"}`: versetzt den Helfer-Client in den Lade-Zustand
(Queue verbergen, „Schüler wird geladen …", `loadingStudent=true`, kein
`studentActive`). Gesendet (a) von `end_student` im Advance-Kontext statt
des Idle-`waiting` (neuer Param `helper_notify={"type":"loading"}`;
Default `None` → weiter Idle-`waiting` für Disconnect/Skip/Reset, dort
soll die Queue erscheinen), (b) von `assign_student_to_helper` beim
Zuweisen — deckt auch den Fall, dass der Helfer keinen alten Schüler
hatte (Host-„Nächster", „Aufrufen" aus der Queue-Anzeige → kein
`end_student`). `/api/next-student` nutzt jetzt `assign_student_to_helper`
(DRY, bekommt den `loading`-Send gratis). `waiting` heißt jetzt
zuverlässig „idle" → Queue. **Lesson:** ein serverseitig ausgelöster
Übergang am Client braucht ein eigenes Signal (`loading`), wenn der
Client den Zustand nicht selbst initiiert hat — ein Client-Flag greift
nur bei selbst getätigten Aktionen. Tests: `test_queue_flow.py`
+Assertion (`advance_helper` sendet `loading`, kein `waiting`;
`assign_student_to_helper` sendet `loading`), Suite 105 grün.

## 2026-07-06 — `current_books`-Jahrgangsfilter entfernt

Der konservative `distributed_at`-Schuljahresfilter in
`get_student_info` (aus dem Review-Tier-2-Hardening vom 2026-07-05, s.
u.) ist raus; `?books=true` liefert zuverlässig nur aktuell ausgeliehene
Bücher (API-Referenz), der Filter hatte legitime Vorjahres-Bücher (noch
nicht zurückgegeben) unterschlagen. Jetzt werden alle aktuell
ausgeliehenen Exemplare ungefiltert als „ausgeliehen" ausgewiesen —
unabhängig vom Ausgabezeitpunkt. Siehe `server/iserv_client.py::get_student_info`.

## 2026-07-06 — Alert-Topologie verfeinert (Helfer schließt selbst, verliehen-an-andere symmetrisch zu ausgemustert, Selbst-Leihe als Hinweis)

Drei aufeinander aufbauende Nutzer-Korrekturen am Ausgemustert/
verliehen-Alarm.

1. **Helfer-Modal bekommt Schließen-Button, Host ohne für Helfer-Scans.**
   `process_scan()` trägt jetzt `source` (`"helper"` Modus A /
   `"student"` Modus B) in den `book_alert`-Broadcast ein. Der Host
   rendert seinen Schließen-Button im Now-Serving-Kästchen **nur** für
   `source !== "helper"` — am Helfer-Scanner schließt der Helfer sein
   Modal selbst (Button im `web/scan.html`-Modal), der Host zeigt die
   Meldung rot, aber ohne Button.
2. **Helfer-Schließen räumt den Host mit auf.** Neuer
   WS-Message-Typ `clear_book_alert` am Helfer-Scanner
   (`server/routes/ws.py`/`ws_scanner`) — der Server feuert
   `{"type": "book_alert", "student_id", "cleared": true}` an alle
   Host-Verbindungen. `dismissBookAlert()` im Helfer schließt das Modal
   **und** sendet das Clear (guard: nur wenn Modal offen war).
   Kontextwechsel (neuer Schüler/Wartend) bleiben rein lokal — dort räumt
   die Queue das Host-Kästchen ohnehin.
3. **Verliehen-Unterscheidung: an andere vs. an sich selbst.**
   - `not_in_stock` (Buch an **jemand anderen** verliehen) →
     **symmetrisch zu `book_deleted`**: Helfer-Modal mit
     Schließen-Button (räumt Host), Schüler-Modal **ohne** Button +
     **blockierend** (`StudentSessionB.book_alert_open` jetzt auch für
     `not_in_stock`, Scans werden serverseitig ignoriert bis Host-Clear),
     Host-Kästchen rot ohne Button (bei Helfer-Source) / mit Button (bei
     Schüler-Source).
   - `series_already_lent` (Buch bereits an **sich selbst** verliehen) →
     nur ein **Hinweis**, den Helfer wie Schüler **lokal** selbst
     schließen können (Button/nächster Scan), **nicht blockierend**,
     **ohne Host-Bezug** (`process_scan` broadcastet bei
     `series_already_lent` bewusst **nicht**).

   Modal-Titel/Farbe sind dynamisch per Status: `book_deleted`/
   `not_in_stock` rot („Ausgemustertes Buch gescannt" / „Buch noch
   verliehen"), `series_already_lent` orange („Buch bereits an dich
   verliehen"). Der Schüler-Client zeigt bei der blockierenden Variante
   „Bitte warte, bis der Betreuer dies freigibt.", beim Hinweis „Du
   kannst diese Meldung selbst schließen." + Schließen-Button.

Kein DB-/IServ-Write — nur read-only `book["deleted"]`/`distributed`/
`available` + WS-Broadcasts. Tests: `tests/test_booking_precheck.py` +2
(`test_process_scan_broadcasts_alert_for_not_in_stock`,
`test_process_scan_no_alert_for_series_already_lent`), Suite 92 grün.
Commits `09296f2`, `440f5b4`, `b4610de`.

## 2026-07-06 — Verliehen-an-Name bei `not_in_stock`

Wird ein Buch gescannt, das derzeit an **jemand anders** verliehen ist
(`not_in_stock`, `distributed`), zeigen **Helfer-Scanner und Host**
zusätzlich, **an wen** es verliehen ist — der **Schüler-Client (Modus B)
sieht den Namen bewusst nicht** (Privatheit: der Schüler scannt nur, der
Betreuer am Host/Helfer muss wissen, wem das Buch gerade gehört).
`server/iserv_client.py::get_book_by_code` liefert neben `student_id`
`loaned_to` („Vorname Nachname") + `loaned_to_id`. Der aktuelle Ausleiher
ist in `GET /books/:code` bereits als eingebetteter `Student` enthalten →
im Normalfall **kein Extra-Request**; nur falls die Einbettung
fehlt/anonymisiert ist, Nachladen per `GET /students/:id` (read-only,
tolerant bei Fehlern → `None`). `evaluate_scan_for_booking` hält die
`msg` bewusst **name-frei** („Nicht im Lager (verliehen): …") und trägt
den Namen nur als eigenes `loaned_to`-Feld. `process_scan` steuert die
Sichtbarkeit pro Source: der `book_alert`-Broadcast an den Host enthält
`loaned_to` immer (unabhängig davon, wer gescannt hat); das
zurückgegebene `scan_result`-Payload enthält `loaned_to`/`loaned_to_id`
**nur für `source != "student"`** (Helfer Modus A), für den Schüler
werden beide auf `None` gesetzt. UI: `web/scan.html` eigene Zeile
„Aktuell verliehen an: …" im Buch-Hinweis-Modal (liest `msg.loaned_to`);
`web/host.html` ergänzt Toast („— verliehen an …") und eine
`ns-borrower`-Zeile im Now-Serving-Kästchen; `web/student.html` zeigt
unverändert nur die name-freie `msg`. Host-Farbigkeit: im
Now-Serving-Kästchen ist nur der „verliehen an …"-Text rot
(`ns-borrower`-Zeile), der Alert-Meldungstext ist normal
(`ns-alert-muted`); Kästchen selbst bleibt rot (`ns-tile-alert`). Der
Toast bleibt als rotes Kästchen (`toast-warn`, weißer Text inkl.
„verliehen an …"). Namen werden **nicht geloggt** (PLAN §3.7), nur an
Host + Helfer durchgereicht. Kein DB-/IServ-Write. Tests:
`tests/test_booking_precheck.py` +4 (`test_not_in_stock_carries_loaned_to`,
`test_not_in_stock_without_borrower_stays_silent`,
`test_process_scan_loaned_to_for_helper`,
`test_process_scan_hides_loan_from_student`), Suite 96 grün. Commits
`15bf5f1`, `<follow-up>`.

## 2026-07-06 — Bezahlstatus-Quelle geklärt (O5) + Ermäßigungs-/Befreiungsnachweis + Modus-B-Host-Freigabe (O6 erweitert)

`enrollments`-Payload trägt `remission_*` (Ermäßigung) / `exemption_*`
(Befreiung) je Jahrganmeldung; `*_accepted` ist tri-state
(`null`=unentschieden). „Nachweis fehlt" = `*_request is True and
*_accepted is None`. Verifiziert am Testschüler 2159 (kein Antrag →
beide Pending=False). `get_student_info` liefert `paid`/`amount_open`/
`remission_pending`/`exemption_pending`; Clients zeigen „Nachweis fehlt"
in Offen-Farbe vor dem Betrag, „Bezahlt" bei Nachweis unterdrückt;
„Nicht angemeldet" im Schülerclient grau. Suite 92 grün.

O6 erweitert: UI zeigt Bücher + „nicht bezahlt"-Banner; Host kann beim
Pairing per `override_payment` freigeben. Ein ausstehender
Ermäßigungs-/Befreiungsnachweis blockt das Pairing ebenfalls; beide
Blocker (nicht bezahlt + Nachweis) werden gesammelt und in **einem**
kombinierten Host-Dialog freigegeben (`reason:"blocked"`-409 +
`blockers`-Liste; `override_payment` hebt alle auf). Nicht-angemeldete
Schüler lösen keine Nachfrage aus (Prüfung auf `enrolled` gegated,
verifiziert per Logik-Review — kein echter Nicht-angemeldet-Schüler auf
Prod verfügbar). Fachlicher Wortlaut/Workflow noch mit Hr. Pühn final.
Nachweis-Hinweis am Gerät mit echtem Pending-Fall steht noch aus (auf
Prod kein solcher Schüler bekannt) — siehe `docs/test_status.md`.

## 2026-07-05 — Bugfix: Context-Leak bei schnellem „Weiter"-Klicken

Wahrer Grund war ein permanenter Context-Leak, nicht nur eine Race.
`load_and_push_helper_student` läuft als `create_task`; `open_student`
pop'd einen Context und lief in `load_card()` (~5 s), aber erst **nach**
Return registrierte `set_worker_session` den Worker in
`student_worker_sessions[id]`. „Weiter" vor `load_card`-Ende →
`end_student(id)` → `pop(id)` → None → nichts freigegeben → Context
geleakt. Bei `WORKER_CONTEXTS=2` und zwei schnellen Klicks Pool dauerhaft
leer (jeder weitere Schüler: 12 s Timeout). Fix (gekoppelt): (a)
`open_student`: `except Exception` → `except BaseException` —
`CancelledError` ist seit Py3.8 `BaseException`, der alte Code ließ den
Context beim Cancel durchrutschen; Handler gibt Context + `notify_all()`
zurück. (b) `load_task`-Feld an `HelperSession`/`StudentSessionB`;
`end_student`/`invalidate_session` canceln den laufenden Lade-Task →
Context kommt zurück. Zusätzlich (mildere Race) `WorkerPool._lock` →
`asyncio.Condition`, `open_student` wartet bis 12 s statt sofort zu
werfen. Regressionstests in `tests/test_worker_pool.py` +
`tests/test_queue_flow.py`. Siehe
`_logs/2026-07-05_sba_worker_pool_release_race.md`.

## 2026-07-05 — Root-Cause-Fix Context-Leak (Review-Tier 1, Commit `d3a75bd`)

Der obige Fix war symptomatisch; vier strukturelle Lücken blieben:

(a) `release_worker` feuerte `asyncio.create_task(pool.release(...))` ohne
Strong-Ref → Task konnte mid-Release geGC'd werden (asyncio hält Tasks
nur schwach) → Context-leak. Fix: modullevel `_release_tasks`-Set +
`add_done_callback(discard)`.
(b) `load_task.cancel()` wurde **nicht awaited** — war der Task bereits
nach `await open_student` im **synchronen** `set_worker_session`, traf
`CancelledError` erst am nächsten `await` (keines mehr) → Task
registriert Worker für bereits abgebrochenen Schüler → orphaned. Fix:
jedes `cancel()` jetzt `with contextlib.suppress(asyncio.CancelledError):
await task`; plus Stale-Guard in `load_and_push_*` (`assigned_student_id`
capturen, nach `open_student` re-checken, sonst Worker schließen ohne
Registrierung).
(c) `remove_helper` (api.py) + `ws_scanner`-finally (ws.py) clear'ten nur
die WS — Schüler blieb `active`, Worker orphaned (Modus A hatte keine
TTL-Recovery wie Modus B). Fix: beide rufen jetzt `end_student(...,
pending, revoked)` + cancel/await `load_task`.
(d) `sweep_expired_sessions` ohne try/except → eine Exception tötet den
Sweeper dauerhaft. Fix: try/except pro Iteration (CancelledError
re-raise, Rest log+continue) + Batch-Broadcast.

**Privacy im gleichen Commit:** `TEST_STUDENTS` (echte Schülernamen) aus
`server/routes/api.py` in gitignored `tests/test_students.local.json`
ausgelagert (Default nur Niklas); `session_token[:6]`-Logging →
`sha256[:8]`-Handle. Suite grün (85). Siehe
`_logs/2026-07-05_sba_pool_leak_root_causes.md` +
`wiki/40_experience_logs/lessons_learned.md` („Await task.cancel()").

## 2026-07-05 — Review-Tier-2-Hardening (Commit `63a4cb3`)

Edge-Case-Bugs + Härtung aus dem Codebase-Review (4 Review-Agenten,
Tier 2). Dateibegrenzt parallel umgesetzt, Suite grün (85):

(a) `automation/worker.py`: `new_page()` an beiden Stellen im
try/except (Context wird bei Fehlschlag zurück in den Pool gelegt);
`release()` Double-Release-Guard (`session._context = None`);
`start()`-Cancel schließt aufgebaute Contexts; `_read_booking_result`
scoped auf Bücher-Liste (exkl. Eingabefeld), bleibt `unknown`-Default.
(b) `server/iserv_client.py`: `(b.get("BookView") or {})` (null-safe);
`threading.Lock` um Lazy-Init von `_client`/`_resolve_sy`/
`_get_series_map` (Lock hält nicht während API-Calls); konservativer
`current_books`-Jahrgangsfilter via `distributed_at` (keep-when-unknown
— sicher gegen falsche Enter). **Wieder entfernt am 2026-07-06** (siehe
Eintrag oben): der Filter hatte legitime Vorjahres-Bücher unterschlagen.
(c) `web/`: `escapeHtml` auf Kamera-id/-label (scan+student); `host.html`
`JSON.parse` try/catch; `pushSlipDefault` erst post-Login; `qr-img.src`
nur bei `data:image/`-Prefix.
(d) `server/routes/api.py`: 7× `int(student_id)`→400;
`secrets.compare_digest` für Host-Passwort + `join_secret` + neues
`login_limiter` (5/15s); `request.client is None`→400; `_base_url`
vertraut **nicht mehr** dem `Host`-Header-Hostnamen (IP aus
`cfg.host_ip`/Auto-Erkennung, nur Port aus Host — sonst
Host-Header-Injection ins QR-URL mit `join_secret`). `ws.py`:
`receive_json` fängt `json.JSONDecodeError`. `ratelimit.py`:
Dead-Pop-then-recreate entfernt (leere Deques werden jetzt echt
evicted). `config.py`: `req_int`-Helper (klare `SystemExit`-Fehler).
(e) `server/printing.py`: PDF-Dateiname µs+`token_hex` (keine
Sekunden-Kollision); PowerShell UTF-8-Console-Prefix; `_print_win_default`
via `asyncio.to_thread` (blockiert nicht den Event-Loop);
`pages`-Regex-Validierung. `server/tls.py`: Zertifikat-Expiry-Check beim
Start (regeneriert <30d); Key via `os.open(0o600)` (kein
world-readable-Fenster).
(f) `automation/`: Spike-Login-Check `and`→`or` (wie `worker.py`);
`test_printer.py` Single-Quote-Escaping; e2e `HOST_PASSWORD` in `main()`
mit klarem `SystemExit`. Test
`test_base_url_keeps_routable_host` → `test_base_url_ignores_spoofed_host_header_uses_config_ip`
(asserted jetzt die neue Security-Eigenschaft).

Siehe `_logs/2026-07-05_sba_tier2_hardening.md` +
`wiki/40_experience_logs/lessons_learned.md` („Host-Header nicht für
URL-Hostnamen vertrauen").

## 2026-07-05 — Review-Tier-3 (UI-Architektur + Server-Robustheit)

5 dateibegrenzt parallele Agenten + 1 Polish-Agent danach, Suite grün
(85):

(a) `web/scan.html`: großer Inline-`<script>`-Block mechanisch nach
`web/scan.js` extrahiert (493 Zeilen), `scan.html` auf 234 Zeilen
(Markup + `<script src="scan.js">`) reduziert. Ladereihenfolge
(`html5-qrcode.min.js` vor `scan.js`) erhalten, `node --check` grün.
(b) `web/host.html`: alle 34 inline `onclick=`/`onchange=`/`onkeydown=`
entfernt → `addEventListener` (direkt für statische Elemente, delegiert
via `data-action`/`data-student-id`/`data-token`/`data-code` für
dynamisch gerenderte Zeilen/Buttons). Grep bestätigt: keine
`on*=`-Attribute mehr im Markup oder in Template-Literal-`innerHTML`.
(c) `server/sessions.py`: `advance_helper` in zwei klare Schritte
gesplittet — ruft `end_student` und delegiert dann an neues
`assign_next_pending_to_helper` (Zuweisung + Broadcast + Hintergrund-Task
für `load_and_push_helper_student`), analog zur Cleanup-Reihenfolge bei
`/api/helper/{token}` DELETE. Tier-1-Stale-Task-Guards unangetastet.
(d) `server/hub.py`: Broadcast-Race behoben — `broadcast_host`,
`broadcast_queue_size`, `broadcast_settings` und `send_scanner` liefen
als unabhängige Tasks und konnten dieselbe WebSocket-Verbindung
gleichzeitig treffen (Interleaving/Reihenfolge-Risiko bei parallelen
Sends). Neuer `Hub._safe_send()` mit Pro-Verbindung-`asyncio.Lock` (in
`WeakKeyDictionary`, damit Locks toter Verbindungen nicht leaken).
`server/sessions.py`: `print_loan_slip_for` bekommt expliziten
`state.iserv is None`-Guard mit klarer `RuntimeError`-Meldung (statt
unklarem `AttributeError` auf `None.get_loan_slip_pdf`, wird von den
Aufrufern ohnehin generisch abgefangen).
(e) `server/tls.py`: dreifach duplizierte `ipaddress.ip_address`/
`ValueError`-Blöcke zu `_parsed_ip()`-Helper zusammengeführt;
`_hostname_ipv4s` vor Verwendung in `_candidate_ipv4s` einsortiert.
`server/printing.py`: toter `import subprocess` entfernt (nur
`asyncio.subprocess.PIPE`/`STDOUT` in Gebrauch). `automation/e2e_*.py`
bereits konsistent aus Tier 2, unverändert gelassen.
(f) Polish-Pass (nach a+b, gleiche Dateien): `host.html`
`renderStatusBar()` nutzt jetzt `settingsOpen()` statt eigener
DOM-Query-Duplikation; kein Dead-Code/`window.*`-Exposure-Rest aus den
onclick→addEventListener- bzw. Inline-Script-Extraktions-Refactors
gefunden (bereits sauber). Token-Rotation-Kommentare in `showMbQr()`
bereits ausreichend (WHY-only, keine Ergänzung nötig).

Verifiziert: `uv run pytest` 85/85, `node --check` auf `scan.js` +
extrahiertem `host.html`-Inline-Script grün, Grep bestätigt 0
verbleibende `on*=`-Attribute in `web/`. Kein Verhaltensunterschied im
Buchungspfad, `ALLOW_BOOKING`-Gate unangetastet.

## 2026-07-05 — Buchreihen ausblenden (Einstellungen-Dialog)

Jedes Buch im Reiter „Bücherlisten ordnen" (`host.html`) hat einen
👁/🚫-Button; ausgeblendete Reihen
(`state.hidden_isbns_by_grade: dict[grade→set[isbn]]`, reiner
In-Memory-State, kein DB-/IServ-Write) gelten beim Scannen nicht mehr
als „vorgemerkt" (weder Scanner- noch Schüler-Anzeige) und sind damit
nicht buchbar. Neue Funktionen `get_hidden_isbns_for_form()`
(`server/book_order.py`, spiegelt `get_book_order_for_form()`) und
`apply_hidden_books()` (`server/sessions.py`), gefiltert direkt nach
jedem `get_student_info`-Aufruf (4 Call-Sites: Modus A/B je Zuweisung +
Reconnect in `sessions.py`/`routes/ws.py`). Neuer Endpoint
`POST /api/booklist-hidden` (mirrort `/api/booklist-order`);
`GET /api/booklist-order` liefert zusätzlich `hidden: [isbn...]`. Tests:
`tests/test_class_book_order.py` +5, Suite 90 grün. **Live-Effekt bei
bereits geladenem Schüler bewusst nicht sofort** — analog zur
bestehenden Bücher-Reihenfolge greift eine Änderung erst beim nächsten
Laden/Reconnect, nicht rückwirkend auf eine schon offene
Scanner-Session.

**Gotcha (direkt nach Deploy):** Nutzer meldete „anwählbar, aber nicht
speicherbar" — Ursache war kein Code-Bug, sondern ein laufender
Server-Prozess (`reload=False`, kein systemd), der vor dem Code-Edit
gestartet war und die neue Route noch nicht kannte, während das
statische `host.html` sofort die neue UI zeigte. Diagnostiziert via
`ps -o lstart` vs. `stat -c %y`; Neustart bewusst dem Nutzer überlassen
(aktive Helfer-/Queue-Sessions wären sonst verloren gegangen). Details:
`~/wiki/_logs/2026-07-05_sba_hide_book_series_and_reload_gotcha.md`,
`~/wiki/wiki/40_experience_logs/lessons_learned.md`.

## 2026-07-05 — Karte „Bücher-Reihenfolge (Scanner)" entfernt

Mit dem Einstellungen-Dialog (Bücherlisten-Reiter, 2026-07-04) war die
Klassen-Karte funktional komplett redundant (gleicher Katalog, gleiche
`book_orders_by_grade`-Ablage), zeigte aber zwei Bugs:

1. `POST /api/booklist-order` pushte nur per `broadcast_settings` an die
   Scanner-Helfer-Sessions, nie per `broadcast_host` an den Host selbst —
   eine im Einstellungen-Dialog gespeicherte Reihenfolge aktualisierte
   weder die (jetzt entfernte) Klassen-Karte noch `state.book_order` am
   Host live, bevor man neu geladen hat. Fix: beide
   Bücher-Reihenfolge-POST-Endpunkte rufen jetzt zusätzlich
   `broadcast_host(state.state_snapshot())`.
2. `_ensure_class_catalog` (seedet `book_order` aus
   `book_orders_by_grade`) wurde bisher nur durch den Klick auf „Bücher
   laden & anordnen" ausgelöst — ohne den Klick blieb `book_order` leer,
   auch wenn im Einstellungen-Dialog längst eine Reihenfolge
   vorkonfiguriert war. Fix: `select_class` ruft `_ensure_class_catalog`
   jetzt automatisch auf, Fehler dabei sind nicht fatal (Klasse bleibt
   geladen, `book_order` bleibt leer wie bisher ohne Klick). Damit greift
   eine vorab im Einstellungen-Dialog gesetzte Reihenfolge sofort beim
   Klassenwechsel, ganz ohne Zusatzklick.

`GET|POST /api/class-book-order` + zugehöriges Frontend (`web/host.html`:
`boOrder`/`loadBookOrder`/`renderBookOrderList`/Drag-Handler/
`saveBookOrder`/`syncBookOrderCard`) entfernt; `normalize_book_order`/
`_ensure_class_catalog` bleiben (jetzt einzig von `select_class`
genutzt). Bestehende Tests (`tests/test_class_book_order.py`) testen nur
die Katalog-/Normalisierungs-Logik, nicht die entfernten Endpunkte —
unverändert grün (Suite 92).

## 2026-07-05 — Bücher-Reihenfolge pro Schüler-Jahrgang statt globaler Klassen-Order

Bis hierhin hing die Helfer-Anzeige an **einer** globalen
`state.book_order` für „die aktive Klasse". Für klassenübergreifende
Warteschlangen (einzeln hinzugefügte Schüler, „Test Config"-Tab) mit
Schülern aus verschiedenen Jahrgängen war das falsch: alle Helfer
bekamen dieselbe (meist leere oder zum falschen Jahrgang passende)
Reihenfolge. Fix: neues Modul `server/book_order.py` mit
`get_book_order_for_form(state, form)` — ermittelt den Jahrgang **des
jeweils zugewiesenen Schülers** (über `IsServClient.get_class_book_catalog`)
und liefert dessen `book_orders_by_grade`-Konfiguration, mit
`state.form_catalog_cache` (form → (grade, catalog_isbns)) gegen
wiederholte IServ-Roundtrips. `hub.broadcast_settings()` berechnet die
Reihenfolge jetzt **pro verbundenem Helfer** anhand seines eigenen
Schülers, statt einen globalen Wert an alle zu pushen; alle vier
`student_info`-Baustellen (`sessions.py` ×2, `routes/ws.py` ×2 —
Scanner-Reconnect + Modus-B-Reconnect) nutzen dieselbe Funktion. Live
per Playwright-freiem WS-Test verifiziert: zwei Helfer mit Schülern aus
Jahrgang 10 und 12 (ohne geladene Klasse, reiner Test-Config-Betrieb)
bekamen nach einer Jahrgangs-Umsortierung im Einstellungen-Dialog sofort
ihre jeweils eigene, unterschiedliche Reihenfolge gepusht.
`get_book_order_for_form` fängt IServ-Fehler intern ab (Fallback
`state.book_order`) — ein Fehler dort darf `student_info` nie
verhindern, da der Aufruf in `load_and_push_helper_student` außerhalb
des einzigen Try/Except-Blocks liegt. Suite weiter grün (85).

## 2026-07-04 — Host-Einstellungen-Dialog

Die zwei Inline-Umschalter der Status-Bar (Tailscale-IP,
Schüler-Leihschein) wurden in einen Modal-Dialog
(„Einstellungen"-Button, Stil wie Druck-Dialog) ausgelagert. Speichern
übernimmt nur Änderungen, Abbrechen/Esc verwirft. Enthält zusätzlich:

- **Drucker-Auswahl:** Dropdown der dem Gerät bekannten Drucker.
  `list_printers()` in `server/printing.py` (rein lesend: Windows
  `Get-Printer`/`Win32_Printer Default=TRUE`, macOS/Linux `lpstat
  -e/-d`). `GET /api/printers`, `POST /api/printer` → In-Memory
  `state.printer_name_override` (None = `PRINTER_NAME` aus `.env` bzw.
  Systemstandard). `print_loan_slip_for` nutzt Override vor
  `cfg.printer_name` (Host + Helfer). „Kein Drucker gefunden", wenn
  nichts verfügbar.
- **Bücherlisten ordnen (jahrgangsweit):** verallgemeinert die
  klassenweite Reihenfolge auf **alle Jahrgänge** des Schuljahrs, vorab
  konfigurierbar — ein **Reiter je Booklist** (Jahrgang), Katalog lazy
  geladen, per Drag & Drop sortierbar. `state.book_orders_by_grade`
  (dict grade→ISBN-Liste, In-Memory; Reset nur bei Schuljahreswechsel via
  `reset_booklist_orders`). `GET /api/booklists`
  (`get_booklists_overview` → `[{id,grade,title}]`), `GET|POST
  /api/booklist-order?grade=` (`get_booklist_catalog_by_grade`).
  `get_class_book_catalog` liefert jetzt `(grade, catalog)`;
  `_ensure_class_catalog` seedet `book_order` aus der jahrgangsweiten
  Reihenfolge, `POST /api/class-book-order` schreibt in dieselbe Map —
  Klassen- und Jahrgangs-Ordnung teilen sich `grade` als Key. Speichern
  für den Jahrgang der geladenen Klasse zieht `book_order` live nach
  (`broadcast_settings`). Alles nur GET/In-Memory, kein DB-Write. Tests:
  `tests/test_class_book_order.py` erweitert (Suite 79 grün).

## 2026-07-02 — Konfigurierbare klassenweite Bücher-Reihenfolge (Scanner)

Host legt per Drag & Drop die Anzeige-Reihenfolge fest, gilt für die
ganze Klasse und bleibt über Schülerwechsel (Reset nur bei Klassen-/
Schuljahreswechsel, Queue-leeren). Karte „Bücher-Reihenfolge (Scanner)"
in `web/host.html` zeigt die **ausleihbaren Bücher des Jahrgangs** aus
der offiziellen **Jahrgangs-Bücherliste** (`GET
/schoolyears/:sy/booklists/:id`, Klassenstufe = `form["grade"]`) — Basis
geändert (2026-07-02b): nicht mehr die Vereinigung der
Einzelanmeldungen, sondern die vollständige Jahrgangsliste (unabhängig
davon, welche Schüler gerade angemeldet sind). Nur `borrowable=True`
(keine Kauf-/Arbeitshefte), dedupliziert, `series_data` liefert
Titel/Fach direkt. Zugriff über `GET /api/class-book-order` (on-demand,
`iserv_client.get_class_book_catalog`, read-only, 2 GETs statt N).
**Mehrjahresbände sind enthalten** (2026-07-02d): die komplette
ausleihbare Jahrgangsliste wird gezeigt — der frühere
`min(gradesFlat)`-Filter (nur unterster Jahrgang) wurde auf Wunsch
entfernt. Drag & Drop mit **horizontaler Einfügemarke** (kein
Zeilen-Highlight). Speichern via `POST /api/class-book-order`
(`normalize_book_order` beschränkt auf Katalog + hängt fehlende an).
`state.book_order` reist in `student_info`/`settings` mit; Scanner
(`web/scan.html`, Modus A) **und** Schülerseite (`web/student.html`,
Modus B) sortieren nach `[erledigt, Klassen-Reihenfolge, Original]`.
Jeder Schüler sieht weiterhin nur seine eigenen Bücher. Tests:
`tests/test_class_book_order.py`.

**Erledigt-Gruppe nach Ausgabe-Aktualität sortiert** (2026-07-02d,
jüngstes oben): In der Erledigt-Gruppe ersetzt der „doneRank" die
Klassen-Reihenfolge — **gerade in dieser Session gescannte/ausgegebene
Bücher zuerst** (nach Scan-Reihenfolge, zuletzt oben; `scanOrder`-Map,
da staged/gebuchte Bücher im Client-Payload noch kein `distributed_at`
tragen), darunter die schon vorher ausgeliehenen nach `distributed_at`
(desc). `web/scan.html` + `web/student.html`.

Scanner-Buchliste: erledigte (gescannt/ausgeliehen) sinken nach unten —
`web/scan.html`, `isBookDone()` + stabile Sortierung.

## 2026-07-02 — Buchungs-Freigabe: Auto-Buchung mit Vorabprüfung (O10)

Niklas hat das Klicken auf **Enter** (Buchung gegen die Produktion)
freigegeben — aber **nur**, wenn eine gescannte Buchung **beide**
Bedingungen erfüllt (Details: `docs/PLAN.md` §6.1, dort inhaltlich
gepflegt und sicherheitskritisch unangetastet). Umsetzung:
`server/sessions.py::evaluate_scan_for_booking()` (read-only
Vorabprüfung, streng bei Unsicherheit) + `process_scan()` (gemeinsame
Scan-Verarbeitung Scanner/Schüler) + Master-Gate `ALLOW_BOOKING`
(Default `false`).

**Manueller „Buchen"-Button entfernt:** Der Host-UI-Button
(`web/host.html`, Kachel- + Queue-Ansicht) plus die `commitBook`-JS-
Funktion sind raus — er wurde nur bei `allow_booking=true` gerendert,
also genau dann, wenn die Auto-Buchung ohnehin läuft (redundant). Der
Endpoint `POST /api/commit-book` (+ `handle_commit`) **bleibt** als
dreifach gegateter Fallback bestehen, nur ohne UI-Fläche. Tests:
`tests/test_booking_precheck.py`, `tests/test_booking_gate.py`.

Nachfolgende Updates zu diesem Mechanismus (Ausgemustert-Prüfung
vorgezogen, Alert-Topologie, Ersatzanspruch, …) stehen jeweils unter
ihrem eigenen Datum in diesem Changelog; die aktuelle, vollständige
Beschreibung des Mechanismus steht in `docs/PLAN.md` §6.1.

## 2026-06-23 — Helfer-Druck-Dialog statt Sofortdruck

Klick auf den Drucker-Button (`web/scan.html`) öffnet ein Modal mit (a)
Warnung „Erst X von Y vorgemerkten Büchern gescannt" inkl. Liste der
offenen Titel, (b) Checkbox „Schüler-Leihschein (2. Seite)", (c) Buttons
**Abbrechen / Drucken / Drucken & nächster Schüler** (letzterer schaltet
nur bei `print_result.ok` weiter).

- Checkbox-Default = Host-Toggle, server-gesynct: Host pusht seinen
  `slip-second-page`-Stand via `POST /api/slip-default` →
  `state.slip_second_page_default` → `Hub.broadcast_settings` → Helfer
  (`{type:"settings"}`); Helfer bekommt den Wert auch beim WS-Connect.
  Reines UI-Setting, **kein IServ-/DB-Zugriff**.
- WS `print` nimmt jetzt `second_page` entgegen → `pages = None|"1"`.
- Buchliste aktualisiert sich live nach jedem Scan: `scan_result` trägt
  die `isbn`, der Client markiert das Buch „erledigt" (rein visuell;
  Scans bleiben `staged`, kein Submit). Dialog wartet vor dem Vergleich
  via `pendingScans`-Zähler auf den Abschluss laufender Scans.

## 2026-06-22 — Scan-Vorabprüfung gegen Anmelde-Buchliste

Bevor ein gescannter Barcode an den Worker gestaged wird, prüft der
Server read-only (`GET /books/{code}` → ISBN), ob das Buch zur
Anmelde-Buchliste des Schülers gehört (`check_scanned_book` in
`server/sessions.py`). ISBN-Set `expected_isbns` wird je Session
gehalten — **Modus B** auf `StudentSessionB` (befüllt beim
Pairing/Reconnect), **Modus A** auf `HelperSession` (befüllt beim Laden
des Schülers/Reconnect, geleert beim Schülerwechsel). Treffer → wie
bisher; „not_enrolled"/„unknown_book" → sofortiges `scan_result`,
**kein** Worker-Kontakt. Leeres Set (Buchliste noch nicht geladen) oder
API-Fehler blockieren nicht (der offizielle Frontend-Submit validiert
ohnehin). Reiner Read-Pfad, in Scanner- (`/ws/scanner`) und
Schüler-WS (`/ws/student`) verdrahtet.

Leihschein-Druck-Backends: `file`/`lp`/`sumatra`/`win-default`/`auto`
gebaut (`server/printing.py`), read-only PDF-Abruf via
`get_loan_slip_pdf`, Endpoint `POST /api/print-loan-slip`, Host- und
Scanner-Button verdrahtet.

## 2026-06-18 — Join-QR-Rotation entfernt, Hardening-Pass

Das Join-Secret wird jetzt **bei jedem Öffnen der Ausgabe** neu erzeugt
(`gen_join_secret()` in `/api/modus-b/open`) und bleibt **innerhalb**
einer Ausgabe konstant — der Schüler-QR ändert sich nicht mehr mitten in
der Ausgabe. `_rotate_join_secret` (Pro-Zuordnung-Rotation, eingeführt
2026-06-17) ist entfallen. Schutz liegt weiter auf `modus_b_open`-Gate +
Per-IP-Ratelimit + **manueller Host-Zuordnung** (Pairing). Trade-off: ein
Screenshot des QR bleibt gültig, solange dieselbe Ausgabe offen ist —
neue Joins erzeugen aber nur ungepairte pending-Sessions (verfallen per
TTL). Alte QRs aus einer früheren Ausgabe werden mit dem nächsten Öffnen
ungültig. „Ausgabe öffnen" zeigt den QR nicht automatisch. Auch der
QR-Anzeige-Text (`#qr-url`) zeigt die aktuelle Join-URL.

**Hardening-Pass aus Code-Review:** Worker-Context-Leak (Pool-
Erschöpfung), WS-Reconnect-Leak, Host-Login-TTL (`HOST_SESSION_TTL_S`),
QR-IP-Override (`HOST_IP`), Pairing-TOCTOU, `commit-book`-ok-nur-bei-
booked u. a. Write-Pfad-Gating unangetastet. Details:
`docs/hardening_2026-06-18.md`.

## 2026-06-17 — Modus A: Weiter-Button (O1), Statuszeile, Schuljahr-Auswahl

- **Weiter-Button (⏭):** Helfer tippt „Weiter" im Scanner → WS
  `{type:"next"}` → `sessions.advance_helper`: schließt den aktuellen
  Schüler ab (`end_student`, **kein** Browser-Submit) und vergibt den
  nächsten Pending aus der Queue. Host kann weiterhin via „Nächster
  Schüler" zuweisen. **Kein** Browser-Submit
  (`end_student`→`release_worker`→`page.close()`). Schüler verschwindet
  sofort, Statuszeile „Wird geladen…". Status-Push jetzt **vor**
  Worker-Aufbau (sofort sichtbar statt erst nach Reload); Modus-A-Laden
  zentral in `sessions.load_and_push_helper_student`. Scanner-Statuszeile
  auf Kamerafeld-Breite, flankiert von Drucker-Button (Platzhalter) +
  Weiter-Button; Status-Punkt entfernt.
- **Kartei per Schüler-ID-Route:** `#/counter/student/<id>` via
  `_goto_authed` statt Nachnamen-Typeahead — eindeutig pro Schüler, keine
  Namensgleichheit/Tippfehler (Commit `38c5094`). Debug: `.env`
  `HEADLESS=false` (sichtbarer Browser) + `SLOW_MO_MS` (verlangsamt jede
  Aktion) — nur auf Geräten mit Display (Commit `c77436c`).
- **Host-UI: Schuljahr auswählbar** (`GET /api/schoolyears` + `POST
  /api/select-schoolyear`, read-only). Default = laufendes Jahr, sonst
  das nächste (deterministisch aus `begin`/`end`, nicht blind
  `/schoolyears/current`); Wechsel resettet Queue/Klasse mit
  Active-Session-Guard. Schuljahr wird durch Klassen-/Schüler-/
  Karteiabrufe durchgereicht.
- **Host-Pairing-UI ohne Tippen (Modus B):** wartende Codes werden am
  Host **angezeigt** und per Klick zugeordnet (`web/host.html`, rein
  Frontend). Zwei Wege: *Code-zuerst* (Codes-Liste in der Modus-B-Karte
  mit Schüler-`<select>` + „Zuordnen") und *Schüler-zuerst*
  (Pairing-Button stellt Schüler scharf → Code-Chip klicken). Gemeinsame
  `doPair()` inkl. O6-Override. `prompt()` entfällt.
- **Pairing-Latenz-Fix:** `student_info` wird in
  `load_and_push_paired_student` **vor** dem Worker-Open ans Handy
  gepusht (Worker-`load_card` lief vorher davor und blockierte die
  Anzeige ~7 s). Sicher, weil `handle_scan` „Worker nicht bereit" sauber
  meldet.
- **iPad-Display am Host bedienbar:** Button „QR für iPad anzeigen"
  (`GET /api/display/qr` → QR auf `/qr-display`, host-auth) +
  Freischalt-Feld für den iPad-Registrierungscode (`POST
  /api/display/authorize`, erscheint nur bei verbundenem,
  unautorisiertem iPad). Bestehender Button → „QR für Schüler anzeigen";
  Karte „Live-Ausgabe (Modus B)" → „Schüler".
- **Queue-Steuerung erweitert:** pro Schüler „Trennen" (`/api/disconnect`
  → zurück auf „Wartend", trennt Helfer/Session), global „Alle
  Verbindungen … trennen" (`/api/disconnect-all`) und „Queue Status
  zurücksetzen" (`/api/reset-queue`, alle → pending). Beide global mit
  doppelter Bestätigung, dezenter Link-Stil. Alle bauen auf `end_student`.
- **Reiter „Test Config"** (`host.html`, inzwischen überholt — siehe
  2026-07-09 oben): Auswahl des Reiters fügte die festen Testschüler
  automatisch an die Queue an (`switchTab('test')` →
  `addTestStudents()`); Button als manueller Re-Trigger. IDs fest
  verdrahtet in `TEST_STUDENTS`: Niklas Müller (2159), Lukas Podleschny
  (2164), Lucas Stolpe (2167). Idempotent (Duplikate übersprungen).

## 2026-06-16 — Scanner-UI-Redesign + Buch-Daten-Anreicherung

Obere Leiste Zahnrad/Kamera-Streifen/Taschenlampe+Ton, volle
Statuszeile, großer Name mit Bezahlstatus rechtsbündig, scrollbare
Bücher-Tabelle. Bücher-Tabelle mit echten Daten: Spalten Fach | Titel |
Status-Icon; vorgemerkt (gelb/orange, ⏳) oben, ausgeliehen
(hellgrün/dunkelgrün, ✓) unten; Titel + Fach korrekt aus `client.series`
aufgelöst. Serien-Katalog-Cache (`IsServClient._get_series_map`,
read-only `GET /series`): erste Schülerauswahl lädt den Katalog einmalig;
Titel/Fach auch für bereits ausgeliehene Bücher (nur `code`+`isbn` im
Roh-Payload) gefüllt.

## 2026-06-15 — Kern Modus A: Server, Worker, Druck, Modus-B-Grundgerüst

Umfangreicher Ausbau an einem Tag (Details in
`docs/phase2_e2e_2026-06-15.md`, `docs/phase4_modus_b_2026-06-15.md`):

- FastAPI-Server: HTTPS (selbstsigniert), WebSocket-Hub,
  Session-/Rollenmodell. Host-UI: Login, Klasse wählen, alphabetische
  Queue, Live-Status Helfer-Sessions. Helfer-Scanner-UI: Token-basiert,
  Schüleranzeige (angemeldet/bezahlt/Bücher), Scan-Feedback.
- Playwright-Worker: Context-Pool (N unabhängige Logins), Schülerkartei
  laden, Barcode staged (kein Submit). Recovery (Re-Login bei
  Session-Ablauf, `automation/worker.py`, deterministisch getestet via
  `automation/recovery_test.py`).
- E2E-Smoke headless (read-only): voller Modus-A-Flow
  Host→Scanner→Worker→Kartei→staged (`automation/e2e_smoke.py`) → V3.
  2-Helfer-Paralleltest: zwei Schüler gleichzeitig aktiv, beide Karteien
  parallel, unabhängiges Staging (`automation/e2e_parallel.py`) → V5.
  Pool-Härtung: fehlgeschlagene Worker-Logins werden in `start()` einmal
  nachgezogen, geleakte Contexts geschlossen → V6.
- Buchender Submit-Pfad als Code vorhanden, **dreifach gated**:
  `commit_barcode()` (Enter+Result-Parse) + `handle_commit()` + Endpoint
  `POST /api/commit-book`. Gates: `ALLOW_BOOKING=false` (Default) +
  Host-Auth + `confirm:true`. Feuert ohne Freigabe **nie** gegen
  Produktion (verifiziert: bei Default wird der Worker nicht berührt) →
  V10. Enter/Selektoren unverifiziert bis zum freigegebenen Test.
- Leihschein-Druck — Code fertig: read-only PDF-Abruf + Druck-
  Abstraktion (`server/printing.py`, Endpoint `POST
  /api/print-loan-slip`, Host-Button).
- Modus B (Phase 4, initialer Aufbau, reiner Server-/Web-Code, keine
  Buchung): QR-Display-Rolle (iPad): Registrierung, vom Host gesteuerte
  Anzeige (`web/qr-display.html`, allgemeiner anonymer QR).
  Einmal-Token-System + Pairing-Flow (langer `session_token` +
  4-stelliger Code, Host-Bestätigung). Schüler-UI: reduziert und
  selbsterklärend (`web/student.html`: Bestellliste, Scan, Abschluss).
  Harter Zugriffsentzug (Token-Invalidierung + WS-Close + Worker zu);
  Skip-Funktion deckt Modus B mit ab. Sicherheits-Review
  Token-Lebenszyklus (initial, E2E-verifiziert); iPad-Härtung (iOS-Kiosk)
  bleibt organisatorisch. Rate-Limit `/api/student/join` (pro-IP, 5/10 s,
  `server/ratelimit.py`).

## 2026-06-12 — Projekt-Setup, Spike B, Stack-Entscheidungen

Repo umstrukturiert: Alt-Code raus, Python-Projektgerüst (`server/`,
`web/`, `automation/`, `docs/`, `pyproject.toml`). Scanner-Assets
übernommen (`html5-qrcode.min.js`, `beep.mp3`, Scan-Logik aus
`scanner.html` → `web/scan.html`/`web/scan.js`). `.env`-Handling +
`CLAUDE.md` mit Read-only-/Produktions-Schutzregeln (analog
`ausleihe-api`). Plandokument committet; README neu geschrieben.

Stack-Entscheidungen geklärt (Details in `docs/PLAN.md` §2): Backend
Python (FastAPI + websockets), Write-Pfad Playwright gegen die
offizielle UI, Frontend Vanilla HTML/JS, ein Ausleihe-Admin-Account
(Niklas) für API-Reads **und** Playwright-UI-Sessions.

**Spike B** (→ O2, parallele IServ-Sessions desselben Accounts):
3/3 parallele unabhängige Logins + 3/3 Cookie-Sharing-Contexts, keine
Invalidierung (`automation/spike_b_parallel.py`) → V2.
