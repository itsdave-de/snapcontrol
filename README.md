# SnapControl

Drive Snapshot Backup Wrapper mit Differential-Rotation, Multi-Disk-Support und API-Reporting.

## Wichtige Warnungen

**ACHTUNG: Dieses Script loescht Dateien und Ordner!**

Das Script entfernt im Rahmen der Retention-Policy automatisch alte Backup-Zyklen. Dies umfasst das unwiderrufliche Loeschen von Verzeichnissen und allen darin enthaltenen Dateien. Stellen Sie sicher, dass Sie die Konfiguration verstanden haben, bevor Sie das Script ausfuehren.

**Backup-Laufwerke ausschliesslich fuer Backups verwenden!**

Die konfigurierten Backup-Laufwerke sollten ausschliesslich fuer SnapControl-Backups genutzt werden. Speichern Sie keine anderen Daten auf diesen Laufwerken. Das Script verwaltet den Speicherplatz aktiv und loescht Daten basierend auf der Retention-Konfiguration. Andere Dateien auf dem Backup-Laufwerk koennten versehentlich die Speicherplatz-Berechnung beeinflussen oder im schlimmsten Fall bei manuellen Aufraeum-Aktionen verloren gehen.

## Features

- Automatische Vollbackup/Differential-Rotation
- Multi-Disk-Support mit ID-basierter Erkennung
- Automatisches Speicherplatz-Management und Cleanup
- Logging in Text und JSON
- HTTP-API Integration fuer Backup-Reports

## Installation

### Voraussetzungen

- Python 3.8+
- [Drive Snapshot](https://www.drivesnapshot.de/) (`snapshot.exe`) im gleichen Verzeichnis
- Windows

### Setup

1. Repository klonen
2. `config.example.json` nach `config.json` kopieren und anpassen
3. `snapshot.exe` ins Verzeichnis kopieren
4. Backup-Laufwerk initialisieren:
   ```
   python snapcontrol.py --init-disk E backup-disk-01
   ```

## Konfiguration

Kopiere `config.example.json` nach `config.json`:

```json
{
    "snapshot_exe": "snapshot.exe",
    "source_drive": "D:",
    "hostname": null,
    "max_differential_backups": 6,
    "verify_after_backup": false,

    "target_disks": [
        {
            "id": "backup-disk-01",
            "name": "Backup Disk 1",
            "base_path": "Backups"
        }
    ],
    "disk_id_filename": ".backup_disk_id",

    "retention": {
        "keep_cycles": 3,
        "space_reserve_percent": 50
    },

    "log_settings": {
        "log_dir": "logs",
        "keep_logs_days": 90
    },

    "api_settings": {
        "enabled": false,
        "endpoint": "https://your-api.example.com/api/v1/backup",
        "token": "YOUR_API_TOKEN"
    }
}
```

### Optionen

| Option | Beschreibung |
|--------|--------------|
| `source_drive` | Quell-Laufwerk (z.B. `D:`) |
| `hostname` | Hostname fuer API (`null` = automatisch) |
| `max_differential_backups` | Anzahl Differentials vor neuem Vollbackup |
| `verify_after_backup` | Backup nach Erstellung verifizieren |
| `target_disks` | Liste der erlaubten Backup-Laufwerke |
| `retention.keep_cycles` | Anzahl Backup-Zyklen die behalten werden |
| `retention.space_reserve_percent` | Reserve fuer Speicherplatz-Check (%) |

## Verwendung

### Backup ausfuehren

```bash
# Automatisches Backup (Voll oder Differential)
python snapcontrol.py

# Vollbackup erzwingen
python snapcontrol.py --full

# Differentielles Backup erzwingen
python snapcontrol.py --differential

# Simulation ohne Ausfuehrung
python snapcontrol.py --dry-run
```

### Status und Verwaltung

```bash
# Status anzeigen
python snapcontrol.py --status

# Nach Backup-Laufwerken scannen
python snapcontrol.py --scan-disks

# Laufwerk initialisieren
python snapcontrol.py --init-disk E backup-disk-01
```

### Cleanup

```bash
# Alte Backup-Zyklen loeschen
python snapcontrol.py --cleanup

# Zeigen was geloescht wuerde
python snapcontrol.py --cleanup-dry-run
```

### API

```bash
# API-Verbindung testen
python snapcontrol.py --test-api
```

## Backup-Strategie

### Zyklen

Ein Zyklus besteht aus:
- 1 Vollbackup (`.sna` + `.hsh` Hash-Datei)
- N Differentielle Backups (basierend auf Hash)

Nach `max_differential_backups` Differentials wird automatisch ein neues Vollbackup erstellt.

### Ordnerstruktur

```
E:\Backups\
  [COMPUTERNAME]\
    [LAUFWERK]\
      full\
        D_full_20260107_202812.sna
        D_full_20260107_202812.hsh
      differential\
        D_diff_20260107_210716_#01.sna
        D_diff_20260107_210716_#02.sna
      backup_state.json
  logs\
    backup_20260107_210716.log
    backup_20260107_210716.json
    summary_20260107210716.json
```

### Speicherplatz-Management

Vor jedem Backup:
1. Pruefe freien Speicherplatz
2. Berechne benoetigt: letzter Zyklus * (1 + reserve_percent/100)
3. Falls zu wenig: Loesche aelteste Zyklen
4. Falls immer noch zu wenig: Abbruch mit Fehler

## Laufwerks-Erkennung

Das Script erkennt Backup-Laufwerke anhand einer ID-Datei im Root-Verzeichnis.

### Neues Laufwerk einrichten

1. Laufwerk in `config.json` unter `target_disks` eintragen
2. Laufwerk initialisieren:
   ```
   python snapcontrol.py --init-disk E mein-backup-disk
   ```

### Sicherheit

- Nur Laufwerke mit bekannter ID werden verwendet
- Fremde Laufwerke werden ignoriert
- Bei mehreren Laufwerken wird das mit meistem freien Platz gewaehlt

## API-Integration

Das Script kann Backup-Reports an eine HTTP-API senden.

| Feld | Beschreibung |
|------|--------------|
| `hostname` | Computername |
| `backup_type` | Immer `snapcontrol-v1` |
| `backuplog` | JSON-Summary als Datei |

Die eigentliche Backup-Art (full/differential) ist in der JSON unter `backup.type` enthalten.

## Fehlerbehebung

| Fehler | Loesung |
|--------|---------|
| Kein Backup-Laufwerk gefunden | `--scan-disks` ausfuehren, `--init-disk` verwenden |
| Nicht genuegend Speicherplatz | `--cleanup` ausfuehren, `keep_cycles` reduzieren |
| Hash-Datei nicht gefunden | Neues Vollbackup wird automatisch erstellt |
| API-Upload fehlgeschlagen | Token und Endpoint pruefen |

## Haftungsausschluss

DIE SOFTWARE WIRD "WIE SIE IST" OHNE JEGLICHE GEWAEHRLEISTUNG BEREITGESTELLT, WEDER AUSDRUECKLICH NOCH STILLSCHWEIGEND, EINSCHLIESSLICH, ABER NICHT BESCHRAENKT AUF DIE GEWAEHRLEISTUNG DER MARKTGAENGIGKEIT, DER EIGNUNG FUER EINEN BESTIMMTEN ZWECK UND DER NICHTVERLETZUNG VON RECHTEN DRITTER.

DIE AUTOREN ODER URHEBERRECHTSINHABER SIND IN KEINEM FALL HAFTBAR FUER ANSPRUECHE, SCHAEDEN ODER ANDERE VERBINDLICHKEITEN, OB IN EINER VERTRAGS- ODER HAFTUNGSKLAGE, EINER UNERLAUBTEN HANDLUNG ODER ANDERWEITIG, DIE SICH AUS, AUS ODER IN VERBINDUNG MIT DER SOFTWARE ODER DER NUTZUNG ODER ANDEREN GESCHAEFTEN MIT DER SOFTWARE ERGEBEN.

Die Nutzung dieser Software erfolgt auf eigenes Risiko. Der Benutzer ist allein verantwortlich fuer:
- Die korrekte Konfiguration der Backup-Parameter
- Die Ueberpruefung der Backup-Integritaet
- Die Sicherstellung ausreichender Speicherkapazitaet
- Regelmaessige Tests der Wiederherstellbarkeit

Es wird dringend empfohlen, die `--dry-run` Option zu verwenden, bevor Aenderungen an der Konfiguration produktiv eingesetzt werden.

## Lizenz

Copyright (C) 2025

Dieses Programm ist freie Software: Sie koennen es unter den Bedingungen der GNU General Public License, wie von der Free Software Foundation veroeffentlicht, weitergeben und/oder modifizieren, entweder gemaess Version 3 der Lizenz oder (nach Ihrer Wahl) jeder spaeteren Version.

Siehe [LICENSE](LICENSE) fuer Details.

---

**Hinweis:** [Drive Snapshot](https://www.drivesnapshot.de/) ist ein kommerzielles Produkt von Tom Ehlert Software und nicht Teil dieses Projekts.
