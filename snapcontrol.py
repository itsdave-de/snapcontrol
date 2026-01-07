#!/usr/bin/env python3
"""
SnapControl - Drive Snapshot Backup Wrapper
Automatisiert Backups mit Drive Snapshot inkl. Differential-Rotation
"""

import json
import os
import sys
import subprocess
import socket
import argparse
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum


class BackupType(Enum):
    FULL = "full"
    DIFFERENTIAL = "differential"


@dataclass
class BackupResult:
    success: bool
    backup_type: str
    source_drive: str
    target_path: str
    image_file: str
    hash_file: str
    start_time: str
    end_time: str
    duration_seconds: float
    exit_code: int
    error_message: Optional[str] = None
    file_size_bytes: int = 0
    differential_number: int = 0
    total_differentials: int = 0
    disk_id: str = ""
    disk_name: str = ""
    disk_drive_letter: str = ""


@dataclass
class BackupState:
    """Speichert den aktuellen Backup-Status"""
    last_full_backup: Optional[str] = None
    last_full_hash_file: Optional[str] = None
    differential_count: int = 0
    backups: list = field(default_factory=list)


@dataclass
class BackupCycle:
    """Repraesentiert einen Backup-Zyklus (Vollbackup + Differentials)"""
    full_backup: Path
    hash_file: Optional[Path]
    differentials: list  # Liste von Paths
    timestamp: datetime
    total_size_bytes: int = 0

    def get_all_files(self) -> list:
        """Gibt alle Dateien des Zyklus zurueck (inkl. Split-Dateien)"""
        files = []
        # Vollbackup und alle Split-Dateien (.sna, .sn1, .sn2, ... .s10, .s11, etc.)
        base_name = self.full_backup.stem
        parent = self.full_backup.parent
        for f in parent.glob(f"{base_name}.*"):
            files.append(f)
        # Hash-Datei
        if self.hash_file and self.hash_file.exists():
            files.append(self.hash_file)
        # Differentials und deren Split-Dateien
        for diff in self.differentials:
            diff_base = diff.stem
            diff_parent = diff.parent
            for f in diff_parent.glob(f"{diff_base}.*"):
                files.append(f)
        return files


@dataclass
class DiskSpaceInfo:
    """Speicherplatz-Informationen"""
    total_bytes: int
    free_bytes: int
    used_bytes: int
    last_cycle_size_bytes: int
    required_bytes: int  # Mit Reserve
    has_enough_space: bool

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024**3)

    @property
    def required_gb(self) -> float:
        return self.required_bytes / (1024**3)

    @property
    def last_cycle_gb(self) -> float:
        return self.last_cycle_size_bytes / (1024**3)


@dataclass
class TargetDisk:
    """Repraesentiert ein erkanntes Backup-Ziel-Laufwerk"""
    disk_id: str
    name: str
    drive_letter: str
    base_path: Path
    volume_label: str = ""
    total_bytes: int = 0
    free_bytes: int = 0

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024**3)

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)


class DiskScanner:
    """Scannt und erkennt Backup-Laufwerke anhand von ID-Dateien"""

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self.id_filename = config.get("disk_id_filename", ".backup_disk_id")
        self.target_disks_config = config.get("target_disks", [])

        # Mapping von Disk-ID zu Config
        self.disk_config_map = {d["id"]: d for d in self.target_disks_config}

    def get_available_drives(self) -> list:
        """Gibt alle verfuegbaren Laufwerksbuchstaben zurueck (Windows)"""
        import string
        drives = []
        for letter in string.ascii_uppercase:
            drive_path = Path(f"{letter}:")
            if drive_path.exists():
                try:
                    # Pruefen ob Laufwerk zugreifbar ist
                    list(drive_path.iterdir())
                    drives.append(letter)
                except (PermissionError, OSError):
                    pass
        return drives

    def read_disk_id(self, drive_letter: str) -> Optional[str]:
        """Liest die Disk-ID aus der ID-Datei auf dem Laufwerk"""
        id_file = Path(f"{drive_letter}:") / self.id_filename
        try:
            if id_file.exists():
                content = id_file.read_text(encoding="utf-8").strip()
                return content if content else None
        except (PermissionError, OSError):
            pass
        return None

    def get_volume_label(self, drive_letter: str) -> str:
        """Gibt das Volume-Label des Laufwerks zurueck"""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            volume_name = ctypes.create_unicode_buffer(1024)
            kernel32.GetVolumeInformationW(
                f"{drive_letter}:\\",
                volume_name, 1024,
                None, None, None, None, 0
            )
            return volume_name.value
        except Exception:
            return ""

    def scan_for_target_disks(self) -> list:
        """
        Scannt alle Laufwerke und gibt erkannte Backup-Laufwerke zurueck
        Nur Laufwerke mit bekannter ID werden zurueckgegeben
        """
        import shutil
        found_disks = []
        known_ids = set(self.disk_config_map.keys())

        self.logger.info("=== Laufwerks-Scan ===")

        for drive_letter in self.get_available_drives():
            disk_id = self.read_disk_id(drive_letter)
            volume_label = self.get_volume_label(drive_letter)

            if disk_id:
                if disk_id in known_ids:
                    # Bekanntes Backup-Laufwerk gefunden
                    disk_config = self.disk_config_map[disk_id]
                    drive_path = Path(f"{drive_letter}:")

                    try:
                        usage = shutil.disk_usage(drive_path)
                        total_bytes = usage.total
                        free_bytes = usage.free
                    except Exception:
                        total_bytes = 0
                        free_bytes = 0

                    target_disk = TargetDisk(
                        disk_id=disk_id,
                        name=disk_config.get("name", disk_id),
                        drive_letter=drive_letter,
                        base_path=Path(f"{drive_letter}:\\") / disk_config.get("base_path", "Backups"),
                        volume_label=volume_label,
                        total_bytes=total_bytes,
                        free_bytes=free_bytes
                    )
                    found_disks.append(target_disk)
                    self.logger.success(f"  {drive_letter}: [{disk_id}] {target_disk.name} - {volume_label}")
                    self.logger.info(f"      Frei: {target_disk.free_gb:.1f} GB / {target_disk.total_gb:.1f} GB")
                else:
                    # Unbekannte ID
                    self.logger.warning(f"  {drive_letter}: Unbekannte Disk-ID '{disk_id}' - wird ignoriert")
            else:
                # Keine ID-Datei oder leer
                if volume_label:
                    self.logger.info(f"  {drive_letter}: Kein Backup-Laufwerk ({volume_label})")
                else:
                    self.logger.info(f"  {drive_letter}: Kein Backup-Laufwerk")

        if not found_disks:
            self.logger.warning("Keine konfigurierten Backup-Laufwerke gefunden!")
        else:
            self.logger.info(f"  Gefunden: {len(found_disks)} Backup-Laufwerk(e)")

        return found_disks

    def select_best_disk(self, disks: list) -> Optional[TargetDisk]:
        """Waehlt das beste Laufwerk aus (das mit dem meisten freien Platz)"""
        if not disks:
            return None
        return max(disks, key=lambda d: d.free_bytes)

    def create_id_file(self, drive_letter: str, disk_id: str) -> bool:
        """Erstellt eine ID-Datei auf einem Laufwerk (fuer Setup)"""
        id_file = Path(f"{drive_letter}:") / self.id_filename
        try:
            id_file.write_text(disk_id, encoding="utf-8")
            return True
        except Exception as e:
            self.logger.error(f"Konnte ID-Datei nicht erstellen: {e}")
            return False


class BackupLogger:
    """Logging in menschlich lesbarem Format und JSON"""

    def __init__(self, log_dir: Path, session_id: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.text_log_path = self.log_dir / f"backup_{timestamp}.log"
        self.json_log_path = self.log_dir / f"backup_{timestamp}.json"

        self.entries = []
        self._log_text(f"=== SnapControl Backup Session {session_id} ===")
        self._log_text(f"Gestartet: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log_text("=" * 50)

    def _log_text(self, message: str):
        """Schreibt ins Text-Log"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line)
        with open(self.text_log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, message: str):
        self._log_text(f"INFO: {message}")
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": message
        })

    def warning(self, message: str):
        self._log_text(f"WARNUNG: {message}")
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "level": "WARNING",
            "message": message
        })

    def error(self, message: str):
        self._log_text(f"FEHLER: {message}")
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "level": "ERROR",
            "message": message
        })

    def success(self, message: str):
        self._log_text(f"ERFOLG: {message}")
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "level": "SUCCESS",
            "message": message
        })

    def save_json_log(self, result: BackupResult):
        """Speichert das JSON-Log"""
        log_data = {
            "session_id": self.session_id,
            "entries": self.entries,
            "result": asdict(result)
        }
        with open(self.json_log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
        self._log_text(f"JSON-Log gespeichert: {self.json_log_path}")


class SnapshotWrapper:
    """Wrapper fuer Drive Snapshot Kommandozeile"""

    def __init__(self, snapshot_exe: Path, logger: BackupLogger):
        self.snapshot_exe = Path(snapshot_exe)
        self.logger = logger

        if not self.snapshot_exe.exists():
            raise FileNotFoundError(f"snapshot.exe nicht gefunden: {self.snapshot_exe}")

    def create_full_backup(self, source: str, target_image: Path,
                           verify: bool = True) -> tuple[int, str]:
        """
        Erstellt ein Vollbackup
        Gibt (exit_code, output) zurueck
        """
        cmd = [
            str(self.snapshot_exe),
            source,
            str(target_image),
            "-W",   # Keine Tastendruck-Aufforderung
            "-Go",  # GUI mit Auto-Exit bei Erfolg
        ]

        if verify:
            cmd.append("-T")  # Verify nach Backup

        self.logger.info(f"Starte Vollbackup: {source} -> {target_image}")
        self.logger.info(f"Kommando: {' '.join(cmd)}")

        return self._run_command(cmd)

    def create_differential_backup(self, source: str, target_image: Path,
                                   hash_file: Path, verify: bool = True) -> tuple[int, str]:
        """
        Erstellt ein differentielles Backup basierend auf Hash-Datei
        """
        cmd = [
            str(self.snapshot_exe),
            source,
            str(target_image),
            f"-h{hash_file}",  # Hash-Datei fuer Differential
            "-W",
            "-Go",
        ]

        if verify:
            cmd.append("-T")

        self.logger.info(f"Starte Differentielles Backup: {source} -> {target_image}")
        self.logger.info(f"Basierend auf Hash: {hash_file}")
        self.logger.info(f"Kommando: {' '.join(cmd)}")

        return self._run_command(cmd)

    def _run_command(self, cmd: list) -> tuple[int, str]:
        """Fuehrt Kommando aus und gibt (exit_code, output) zurueck"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200  # 2 Stunden Timeout
            )
            output = result.stdout + result.stderr

            for line in output.strip().split("\n"):
                if line.strip():
                    self.logger.info(f"  > {line.strip()}")

            return result.returncode, output

        except subprocess.TimeoutExpired:
            self.logger.error("Backup Timeout nach 2 Stunden")
            return -1, "Timeout"
        except Exception as e:
            self.logger.error(f"Fehler beim Ausfuehren: {e}")
            return -1, str(e)


class BackupManager:
    """Verwaltet Backup-Strategie, Ordnerstruktur und State"""

    def __init__(self, config: dict, logger: BackupLogger, target_disk: TargetDisk = None):
        self.config = config
        self.logger = logger
        self.target_disk = target_disk
        self.source_drive = config["source_drive"]
        self.max_differentials = config["max_differential_backups"]
        self.computer_name = config.get("hostname") or socket.gethostname()
        self.verify = config.get("verify_after_backup", True)

        # Retention-Einstellungen
        retention = config.get("retention", {})
        self.keep_cycles = retention.get("keep_cycles", 3)
        self.space_reserve_percent = retention.get("space_reserve_percent", 50)

        # Pfade einrichten - basierend auf target_disk oder Fallback
        if target_disk:
            self.target_base = target_disk.base_path
            self.logger.info(f"Verwende Backup-Laufwerk: {target_disk.name} ({target_disk.drive_letter}:)")
            self.logger.info(f"  Disk-ID: {target_disk.disk_id}")
            self.logger.info(f"  Pfad: {target_disk.base_path}")
        else:
            # Fallback fuer Kompatibilitaet
            self.target_base = Path(config.get("target_base_path", "E:\\Backups"))

        self.backup_dir = self.target_base / self.computer_name / self.source_drive.rstrip(":")
        self.state_file = self.backup_dir / "backup_state.json"

        # Wrapper initialisieren
        snapshot_path = Path(config["snapshot_exe"])
        if not snapshot_path.is_absolute():
            snapshot_path = Path(__file__).parent / snapshot_path
        self.wrapper = SnapshotWrapper(snapshot_path, logger)

        # State laden
        self.state = self._load_state()

    def _load_state(self) -> BackupState:
        """Laedt den Backup-Status"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    state = BackupState(
                        last_full_backup=data.get("last_full_backup"),
                        last_full_hash_file=data.get("last_full_hash_file"),
                        differential_count=data.get("differential_count", 0),
                        backups=data.get("backups", [])
                    )
                    self.logger.info(f"State geladen: {self.state_file}")
                    return state
            except Exception as e:
                self.logger.warning(f"State konnte nicht geladen werden: {e}")
        return BackupState()

    def _save_state(self):
        """Speichert den Backup-Status"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, indent=2, ensure_ascii=False)
        self.logger.info(f"State gespeichert: {self.state_file}")

    def setup_directory_structure(self):
        """Erstellt die Ordnerstruktur"""
        self.logger.info(f"Erstelle Ordnerstruktur: {self.backup_dir}")

        # Hauptverzeichnisse
        dirs = [
            self.backup_dir / "full",
            self.backup_dir / "differential",
        ]

        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"  Verzeichnis: {d}")

    def determine_backup_type(self) -> BackupType:
        """Bestimmt ob Voll- oder Differentielles Backup noetig ist"""
        # Kein vorheriges Vollbackup -> Vollbackup
        if not self.state.last_full_backup or not self.state.last_full_hash_file:
            self.logger.info("Kein vorheriges Vollbackup gefunden -> Vollbackup")
            return BackupType.FULL

        # Hash-Datei existiert nicht mehr -> Vollbackup
        hash_path = Path(self.state.last_full_hash_file)
        if not hash_path.exists():
            self.logger.info(f"Hash-Datei nicht gefunden ({hash_path}) -> Vollbackup")
            return BackupType.FULL

        # Max Differentials erreicht -> Vollbackup
        if self.state.differential_count >= self.max_differentials:
            self.logger.info(f"Max. Differentials erreicht ({self.state.differential_count}/{self.max_differentials}) -> Vollbackup")
            return BackupType.FULL

        # Sonst Differential
        self.logger.info(f"Differentielles Backup ({self.state.differential_count + 1}/{self.max_differentials})")
        return BackupType.DIFFERENTIAL

    def run_backup(self, force_type: Optional[BackupType] = None) -> BackupResult:
        """Fuehrt das Backup durch"""
        start_time = datetime.now()

        # Ordnerstruktur sicherstellen
        self.setup_directory_structure()

        # Backup-Typ bestimmen
        backup_type = force_type or self.determine_backup_type()

        # Dateinamen generieren
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")
        drive_letter = self.source_drive.rstrip(":")

        if backup_type == BackupType.FULL:
            image_name = f"{drive_letter}_full_{timestamp}.sna"
            image_path = self.backup_dir / "full" / image_name
            hash_path = image_path.with_suffix(".hsh")

            exit_code, output = self.wrapper.create_full_backup(
                self.source_drive,
                image_path,
                self.verify
            )

            # State aktualisieren bei Erfolg
            if exit_code == 0:
                self.state.last_full_backup = str(image_path)
                self.state.last_full_hash_file = str(hash_path)
                self.state.differential_count = 0
        else:
            diff_num = self.state.differential_count + 1
            image_name = f"{drive_letter}_diff_{timestamp}_#{diff_num:02d}.sna"
            image_path = self.backup_dir / "differential" / image_name
            hash_path = Path(self.state.last_full_hash_file)

            exit_code, output = self.wrapper.create_differential_backup(
                self.source_drive,
                image_path,
                hash_path,
                self.verify
            )

            # State aktualisieren bei Erfolg
            if exit_code == 0:
                self.state.differential_count = diff_num

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        # Dateigroesse ermitteln
        file_size = 0
        if image_path.exists():
            file_size = image_path.stat().st_size

        # Ergebnis erstellen
        result = BackupResult(
            success=(exit_code == 0),
            backup_type=backup_type.value,
            source_drive=self.source_drive,
            target_path=str(self.backup_dir),
            image_file=str(image_path),
            hash_file=str(hash_path),
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            duration_seconds=duration,
            exit_code=exit_code,
            error_message=output if exit_code != 0 else None,
            file_size_bytes=file_size,
            differential_number=self.state.differential_count,
            total_differentials=self.max_differentials,
            disk_id=self.target_disk.disk_id if self.target_disk else "",
            disk_name=self.target_disk.name if self.target_disk else "",
            disk_drive_letter=self.target_disk.drive_letter if self.target_disk else ""
        )

        # Zum State hinzufuegen
        self.state.backups.append({
            "timestamp": start_time.isoformat(),
            "type": backup_type.value,
            "file": str(image_path),
            "success": result.success
        })

        # State speichern
        self._save_state()

        # Log-Zusammenfassung
        if result.success:
            self.logger.success(f"Backup erfolgreich abgeschlossen")
            self.logger.info(f"  Typ: {backup_type.value}")
            self.logger.info(f"  Datei: {image_path}")
            self.logger.info(f"  Groesse: {self._format_size(file_size)}")
            self.logger.info(f"  Dauer: {self._format_duration(duration)}")
        else:
            self.logger.error(f"Backup fehlgeschlagen (Exit-Code: {exit_code})")

        return result

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Formatiert Bytes in lesbare Groesse"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} PB"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Formatiert Sekunden in lesbare Dauer"""
        if seconds < 60:
            return f"{seconds:.1f} Sekunden"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins} Min {secs} Sek"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours} Std {mins} Min"

    def get_backup_cycles(self) -> list:
        """
        Ermittelt alle Backup-Zyklen (Vollbackup + zugehoerige Differentials)
        Sortiert nach Datum (aelteste zuerst)
        """
        cycles = []
        full_dir = self.backup_dir / "full"
        diff_dir = self.backup_dir / "differential"

        if not full_dir.exists():
            return cycles

        # Alle Vollbackups finden (nur .sna Dateien, keine Split-Dateien)
        full_backups = sorted(full_dir.glob("*_full_*.sna"))

        for full_backup in full_backups:
            # Timestamp aus Dateinamen extrahieren (Format: D_full_20260107_202812.sna)
            name_parts = full_backup.stem.split("_")
            if len(name_parts) >= 4:
                try:
                    timestamp_str = f"{name_parts[2]}_{name_parts[3]}"
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                except ValueError:
                    timestamp = datetime.fromtimestamp(full_backup.stat().st_mtime)
            else:
                timestamp = datetime.fromtimestamp(full_backup.stat().st_mtime)

            # Hash-Datei finden
            hash_file = full_backup.with_suffix(".hsh")
            if not hash_file.exists():
                hash_file = None

            # Zugehoerige Differentials finden (basierend auf Hash-Datei-Referenz)
            differentials = []
            if diff_dir.exists():
                # Differentials die nach diesem Vollbackup erstellt wurden
                # und vor dem naechsten Vollbackup
                for diff in sorted(diff_dir.glob("*_diff_*.sna")):
                    diff_time = datetime.fromtimestamp(diff.stat().st_mtime)
                    if diff_time >= timestamp:
                        # Pruefen ob es ein neueres Vollbackup gibt
                        is_for_this_cycle = True
                        for other_full in full_backups:
                            if other_full != full_backup:
                                other_time = datetime.fromtimestamp(other_full.stat().st_mtime)
                                if timestamp < other_time <= diff_time:
                                    is_for_this_cycle = False
                                    break
                        if is_for_this_cycle:
                            differentials.append(diff)

            # Gesamtgroesse berechnen
            cycle = BackupCycle(
                full_backup=full_backup,
                hash_file=hash_file,
                differentials=differentials,
                timestamp=timestamp
            )

            # Groesse aller Dateien im Zyklus berechnen
            total_size = 0
            for f in cycle.get_all_files():
                if f.exists():
                    total_size += f.stat().st_size
            cycle.total_size_bytes = total_size

            cycles.append(cycle)

        # Nach Timestamp sortieren (aelteste zuerst)
        cycles.sort(key=lambda c: c.timestamp)
        return cycles

    def get_disk_space_info(self) -> DiskSpaceInfo:
        """
        Ermittelt Speicherplatz-Informationen inkl. Schaetzung fuer naechstes Backup
        """
        import shutil

        # Freien Speicherplatz auf Ziel-Laufwerk ermitteln
        target_path = self.backup_dir
        if not target_path.exists():
            target_path = self.target_base

        try:
            disk_usage = shutil.disk_usage(target_path)
            total_bytes = disk_usage.total
            free_bytes = disk_usage.free
            used_bytes = disk_usage.used
        except Exception as e:
            self.logger.error(f"Konnte Speicherplatz nicht ermitteln: {e}")
            return DiskSpaceInfo(
                total_bytes=0, free_bytes=0, used_bytes=0,
                last_cycle_size_bytes=0, required_bytes=0, has_enough_space=False
            )

        # Groesse des letzten Zyklus ermitteln
        cycles = self.get_backup_cycles()
        last_cycle_size = 0
        if cycles:
            last_cycle_size = cycles[-1].total_size_bytes

        # Benoetigter Platz mit Reserve berechnen
        reserve_factor = 1 + (self.space_reserve_percent / 100)
        required_bytes = int(last_cycle_size * reserve_factor)

        # Wenn kein letzter Zyklus, schaetze 50GB als Minimum
        if required_bytes == 0:
            required_bytes = 50 * 1024**3  # 50 GB default

        has_enough = free_bytes >= required_bytes

        return DiskSpaceInfo(
            total_bytes=total_bytes,
            free_bytes=free_bytes,
            used_bytes=used_bytes,
            last_cycle_size_bytes=last_cycle_size,
            required_bytes=required_bytes,
            has_enough_space=has_enough
        )

    def log_disk_space(self) -> DiskSpaceInfo:
        """Loggt die Speicherplatzverhältnisse"""
        info = self.get_disk_space_info()

        self.logger.info("=== Speicherplatz-Analyse ===")
        self.logger.info(f"  Ziel-Laufwerk: {self.target_base}")
        self.logger.info(f"  Gesamt: {self._format_size(info.total_bytes)}")
        self.logger.info(f"  Belegt: {self._format_size(info.used_bytes)}")
        self.logger.info(f"  Frei: {self._format_size(info.free_bytes)}")
        self.logger.info(f"  Letzter Zyklus: {self._format_size(info.last_cycle_size_bytes)}")
        self.logger.info(f"  Benoetigt (mit {self.space_reserve_percent}% Reserve): {self._format_size(info.required_bytes)}")

        if info.has_enough_space:
            self.logger.success(f"  Genuegend Speicherplatz vorhanden")
        else:
            self.logger.warning(f"  WARNUNG: Nicht genuegend Speicherplatz!")
            self.logger.warning(f"  Fehlend: {self._format_size(info.required_bytes - info.free_bytes)}")

        return info

    def cleanup_old_cycles(self, dry_run: bool = False) -> dict:
        """
        Loescht alte Backup-Zyklen, behaelt nur die konfigurierten Anzahl
        Gibt Statistik zurueck
        """
        cycles = self.get_backup_cycles()
        stats = {
            "total_cycles": len(cycles),
            "kept_cycles": 0,
            "deleted_cycles": 0,
            "deleted_files": 0,
            "freed_bytes": 0,
            "errors": []
        }

        if len(cycles) <= self.keep_cycles:
            self.logger.info(f"Cleanup: {len(cycles)} Zyklen vorhanden, {self.keep_cycles} werden behalten - nichts zu tun")
            stats["kept_cycles"] = len(cycles)
            return stats

        # Zyklen zum Loeschen (die aeltesten)
        cycles_to_delete = cycles[:-self.keep_cycles]
        cycles_to_keep = cycles[-self.keep_cycles:]

        stats["kept_cycles"] = len(cycles_to_keep)
        stats["deleted_cycles"] = len(cycles_to_delete)

        self.logger.info(f"=== Cleanup alte Backups ===")
        self.logger.info(f"  Gefundene Zyklen: {len(cycles)}")
        self.logger.info(f"  Zu behalten: {self.keep_cycles}")
        self.logger.info(f"  Zu loeschen: {len(cycles_to_delete)}")

        for cycle in cycles_to_delete:
            self.logger.info(f"  Loesche Zyklus vom {cycle.timestamp.strftime('%Y-%m-%d %H:%M')}")
            self.logger.info(f"    Vollbackup: {cycle.full_backup.name}")
            self.logger.info(f"    Differentials: {len(cycle.differentials)}")
            self.logger.info(f"    Groesse: {self._format_size(cycle.total_size_bytes)}")

            if dry_run:
                self.logger.info(f"    [DRY-RUN] Wuerde loeschen")
                stats["freed_bytes"] += cycle.total_size_bytes
                continue

            # Alle Dateien des Zyklus loeschen
            for file_path in cycle.get_all_files():
                try:
                    if file_path.exists():
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        stats["deleted_files"] += 1
                        stats["freed_bytes"] += file_size
                        self.logger.info(f"    Geloescht: {file_path.name}")
                except Exception as e:
                    error_msg = f"Fehler beim Loeschen von {file_path}: {e}"
                    self.logger.error(f"    {error_msg}")
                    stats["errors"].append(error_msg)

        self.logger.info(f"  Cleanup abgeschlossen: {stats['deleted_files']} Dateien, {self._format_size(stats['freed_bytes'])} freigegeben")

        return stats

    def check_and_prepare_backup(self) -> tuple[bool, str, DiskSpaceInfo]:
        """
        Prueft Speicherplatz und fuehrt ggf. Cleanup durch
        Gibt (kann_starten, meldung, disk_info) zurueck
        """
        # Speicherplatz analysieren
        disk_info = self.log_disk_space()

        # Wenn nicht genug Platz, versuche Cleanup
        if not disk_info.has_enough_space:
            self.logger.warning("Nicht genuegend Speicherplatz - versuche Cleanup...")

            cleanup_stats = self.cleanup_old_cycles(dry_run=False)

            # Speicherplatz erneut pruefen
            disk_info = self.get_disk_space_info()

            if not disk_info.has_enough_space:
                msg = (f"Nicht genuegend Speicherplatz nach Cleanup. "
                       f"Frei: {self._format_size(disk_info.free_bytes)}, "
                       f"Benoetigt: {self._format_size(disk_info.required_bytes)}")
                self.logger.error(msg)
                return False, msg, disk_info

            self.logger.success(f"Nach Cleanup: {self._format_size(disk_info.free_bytes)} frei")

        # Zyklen-Info loggen
        cycles = self.get_backup_cycles()
        self.logger.info(f"  Aktuelle Backup-Zyklen: {len(cycles)}/{self.keep_cycles}")

        return True, "OK", disk_info


class SummaryGenerator:
    """Generiert Zusammenfassung fuer HTTP API"""

    def __init__(self, config: dict):
        self.config = config
        self.computer_name = config.get("hostname") or socket.gethostname()

    def generate(self, result: BackupResult, log_entries: list,
                 disk_info: DiskSpaceInfo = None, cycles_count: int = 0,
                 keep_cycles: int = 0) -> dict:
        """Generiert die API-Zusammenfassung"""
        summary = {
            "version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "computer_name": self.computer_name,
            "backup": {
                "success": result.success,
                "type": result.backup_type,
                "source": result.source_drive,
                "target": result.target_path,
                "image_file": result.image_file,
                "file_size_bytes": result.file_size_bytes,
                "file_size_human": BackupManager._format_size(result.file_size_bytes),
                "duration_seconds": result.duration_seconds,
                "duration_human": BackupManager._format_duration(result.duration_seconds),
                "started_at": result.start_time,
                "finished_at": result.end_time,
                "exit_code": result.exit_code,
                "error": result.error_message,
                "differential_info": {
                    "current": result.differential_number,
                    "max": result.total_differentials,
                    "next_full_in": result.total_differentials - result.differential_number
                }
            },
            "target_disk": {
                "disk_id": result.disk_id,
                "disk_name": result.disk_name,
                "drive_letter": result.disk_drive_letter
            },
            "storage": {
                "total_bytes": disk_info.total_bytes if disk_info else 0,
                "free_bytes": disk_info.free_bytes if disk_info else 0,
                "used_bytes": disk_info.used_bytes if disk_info else 0,
                "free_percent": round((disk_info.free_bytes / disk_info.total_bytes * 100), 1) if disk_info and disk_info.total_bytes > 0 else 0,
                "last_cycle_size_bytes": disk_info.last_cycle_size_bytes if disk_info else 0,
                "cycles_count": cycles_count,
                "cycles_max": keep_cycles
            },
            "log_summary": {
                "total_entries": len(log_entries),
                "errors": len([e for e in log_entries if e["level"] == "ERROR"]),
                "warnings": len([e for e in log_entries if e["level"] == "WARNING"]),
            },
            "log_entries": log_entries
        }
        return summary

    def save(self, summary: dict, path: Path):
        """Speichert die Zusammenfassung"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    def post_to_api(self, summary: dict, logger) -> tuple[bool, str]:
        """
        Postet Zusammenfassung an HTTP API
        API erwartet multipart/form-data mit hostname, backuplog (JSON-Datei), backup_type
        backup_type ist immer "snapcontrol-v1", die eigentliche Backup-Art (full/differential)
        ist in der JSON unter backup.type enthalten
        """
        api_config = self.config.get("api_settings", {})
        if not api_config.get("enabled"):
            return False, "API nicht aktiviert"

        endpoint = api_config.get("endpoint")
        token = api_config.get("token", "")

        try:
            import urllib.request
            import urllib.error
            import uuid

            # Multipart boundary generieren
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"

            # JSON-Daten als "Datei"
            json_data = json.dumps(summary, indent=2, ensure_ascii=False).encode("utf-8")

            # Multipart body erstellen
            body_parts = []

            # hostname Feld
            body_parts.append(f"--{boundary}".encode())
            body_parts.append(b'Content-Disposition: form-data; name="hostname"')
            body_parts.append(b"")
            body_parts.append(self.computer_name.encode("utf-8"))

            # backup_type Feld - immer "snapcontrol-v1"
            body_parts.append(f"--{boundary}".encode())
            body_parts.append(b'Content-Disposition: form-data; name="backup_type"')
            body_parts.append(b"")
            body_parts.append(b"snapcontrol-v1")

            # backuplog Datei
            body_parts.append(f"--{boundary}".encode())
            body_parts.append(b'Content-Disposition: form-data; name="backuplog"; filename="backup.json"')
            body_parts.append(b"Content-Type: application/json")
            body_parts.append(b"")
            body_parts.append(json_data)

            # Abschluss
            body_parts.append(f"--{boundary}--".encode())
            body_parts.append(b"")

            body = b"\r\n".join(body_parts)

            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "authorization": f"Bearer {token}"
            }

            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=30) as response:
                response_body = response.read().decode("utf-8")
                if response.status in [200, 201]:
                    return True, response_body
                else:
                    return False, f"Status {response.status}: {response_body}"

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            return False, f"HTTP {e.code}: {error_body}"
        except Exception as e:
            return False, f"Fehler: {e}"


def load_config(config_path: Path) -> dict:
    """Laedt die Konfiguration"""
    if not config_path.exists():
        raise FileNotFoundError(f"Konfiguration nicht gefunden: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="SnapControl - Drive Snapshot Backup Wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python snapcontrol.py                    # Automatisches Backup (Voll oder Differential)
  python snapcontrol.py --full             # Erzwinge Vollbackup
  python snapcontrol.py --differential     # Erzwinge Differentielles Backup
  python snapcontrol.py --status           # Zeige aktuellen Status
  python snapcontrol.py --dry-run          # Simuliere ohne Ausfuehrung
  python snapcontrol.py --scan-disks       # Scanne nach Backup-Laufwerken
  python snapcontrol.py --init-disk E backup-disk-01  # Initialisiere Laufwerk mit ID
        """
    )

    parser.add_argument("--config", "-c", type=Path,
                       default=Path(__file__).parent / "config.json",
                       help="Pfad zur Konfigurationsdatei")
    parser.add_argument("--full", "-f", action="store_true",
                       help="Erzwinge Vollbackup")
    parser.add_argument("--differential", "-d", action="store_true",
                       help="Erzwinge Differentielles Backup")
    parser.add_argument("--status", "-s", action="store_true",
                       help="Zeige aktuellen Backup-Status")
    parser.add_argument("--dry-run", action="store_true",
                       help="Simuliere ohne Ausfuehrung")
    parser.add_argument("--cleanup", action="store_true",
                       help="Fuehre nur Cleanup alter Backups durch")
    parser.add_argument("--cleanup-dry-run", action="store_true",
                       help="Zeige was Cleanup loeschen wuerde")
    parser.add_argument("--scan-disks", action="store_true",
                       help="Scanne nach konfigurierten Backup-Laufwerken")
    parser.add_argument("--init-disk", nargs=2, metavar=("DRIVE", "DISK_ID"),
                       help="Initialisiere ein Laufwerk mit einer Disk-ID (z.B. --init-disk E backup-disk-01)")
    parser.add_argument("--test-api", action="store_true",
                       help="Teste API-Verbindung mit Dummy-Daten")

    args = parser.parse_args()

    # Konfiguration laden
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Fehler: {e}")
        sys.exit(1)

    # Session-ID generieren
    session_id = datetime.now().strftime("%Y%m%d%H%M%S")

    # Temporaerer Logger fuer fruehe Operationen (ins Script-Verzeichnis)
    temp_log_dir = Path(__file__).parent / "logs"
    temp_log_dir.mkdir(exist_ok=True)
    logger = BackupLogger(temp_log_dir, session_id)

    try:
        # Disk-Scanner erstellen
        disk_scanner = DiskScanner(config, logger)

        # Disk initialisieren
        if args.init_disk:
            drive_letter, disk_id = args.init_disk
            drive_letter = drive_letter.rstrip(":")

            # Pruefen ob Disk-ID in Config existiert
            if disk_id not in disk_scanner.disk_config_map:
                logger.error(f"Unbekannte Disk-ID: {disk_id}")
                logger.info("Konfigurierte Disk-IDs:")
                for did, dconf in disk_scanner.disk_config_map.items():
                    logger.info(f"  - {did}: {dconf.get('name', did)}")
                sys.exit(1)

            logger.info(f"Initialisiere Laufwerk {drive_letter}: mit ID '{disk_id}'...")
            if disk_scanner.create_id_file(drive_letter, disk_id):
                logger.success(f"ID-Datei erstellt: {drive_letter}:\\{config.get('disk_id_filename', '.backup_disk_id')}")
            else:
                sys.exit(1)
            return

        # Nur Disk-Scan
        if args.scan_disks:
            disk_scanner.scan_for_target_disks()
            return

        # API-Test
        if args.test_api:
            logger.info("=== API-Verbindungstest ===")

            # Dummy-Summary erstellen
            test_summary = {
                "version": "1.0",
                "generated_at": datetime.now().isoformat(),
                "computer_name": config.get("hostname") or socket.gethostname(),
                "backup": {
                    "success": True,
                    "type": "test",
                    "source": config.get("source_drive", "C:"),
                    "target": "TEST",
                    "image_file": "TEST",
                    "file_size_bytes": 0,
                    "file_size_human": "0 B",
                    "duration_seconds": 0,
                    "duration_human": "0 Sekunden",
                    "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                    "exit_code": 0,
                    "error": None,
                    "differential_info": {
                        "current": 0,
                        "max": config.get("max_differential_backups", 6),
                        "next_full_in": config.get("max_differential_backups", 6)
                    }
                },
                "target_disk": {
                    "disk_id": "test",
                    "disk_name": "API-Test",
                    "drive_letter": "X"
                },
                "storage": {
                    "total_bytes": 0,
                    "free_bytes": 0,
                    "used_bytes": 0,
                    "free_percent": 0,
                    "last_cycle_size_bytes": 0,
                    "cycles_count": 0,
                    "cycles_max": 0
                },
                "log_summary": {
                    "total_entries": 1,
                    "errors": 0,
                    "warnings": 0
                },
                "log_entries": [{
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": "API-Verbindungstest"
                }]
            }

            summary_gen = SummaryGenerator(config)
            logger.info(f"Sende Test-Daten an: {config.get('api_settings', {}).get('endpoint')}")

            success, response = summary_gen.post_to_api(test_summary, logger)
            if success:
                logger.success(f"API-Verbindung erfolgreich!")
                logger.info(f"Antwort: {response}")
            else:
                logger.error(f"API-Verbindung fehlgeschlagen: {response}")
                sys.exit(1)
            return

        # Backup-Laufwerk finden
        found_disks = disk_scanner.scan_for_target_disks()

        if not found_disks:
            logger.error("Kein konfiguriertes Backup-Laufwerk gefunden!")
            logger.info("Verwenden Sie --init-disk um ein Laufwerk zu initialisieren")
            logger.info("Oder pruefen Sie die Konfiguration in config.json -> target_disks")
            sys.exit(1)

        # Bestes Laufwerk waehlen (meisten freien Platz)
        target_disk = disk_scanner.select_best_disk(found_disks)
        logger.info(f"Ausgewaehltes Laufwerk: {target_disk.name} ({target_disk.drive_letter}:)")

        # Logger neu initialisieren mit richtigem Pfad
        log_dir = target_disk.base_path / config["log_settings"]["log_dir"]
        logger = BackupLogger(log_dir, session_id)

        # Backup-Manager mit Ziel-Laufwerk erstellen
        manager = BackupManager(config, logger, target_disk)

        # Status anzeigen
        if args.status:
            logger.info("=== Backup Status ===")
            logger.info(f"Computer: {manager.computer_name}")
            logger.info(f"Quelle: {manager.source_drive}")
            logger.info(f"Ziel: {manager.backup_dir}")
            logger.info(f"Letztes Vollbackup: {manager.state.last_full_backup or 'Keines'}")
            logger.info(f"Differentielle Backups: {manager.state.differential_count}/{manager.max_differentials}")

            next_type = manager.determine_backup_type()
            logger.info(f"Naechster Backup-Typ: {next_type.value}")

            # Zyklen anzeigen
            cycles = manager.get_backup_cycles()
            logger.info(f"=== Backup-Zyklen ({len(cycles)}/{manager.keep_cycles}) ===")
            for i, cycle in enumerate(cycles, 1):
                logger.info(f"  Zyklus {i}: {cycle.timestamp.strftime('%Y-%m-%d %H:%M')}")
                logger.info(f"    Vollbackup: {cycle.full_backup.name}")
                logger.info(f"    Differentials: {len(cycle.differentials)}")
                logger.info(f"    Groesse: {manager._format_size(cycle.total_size_bytes)}")

            # Speicherplatz anzeigen
            manager.log_disk_space()
            return

        # Cleanup-Modus
        if args.cleanup or args.cleanup_dry_run:
            manager.log_disk_space()
            manager.cleanup_old_cycles(dry_run=args.cleanup_dry_run)
            return

        # Backup-Typ bestimmen
        force_type = None
        if args.full:
            force_type = BackupType.FULL
        elif args.differential:
            force_type = BackupType.DIFFERENTIAL

        # Dry-Run
        if args.dry_run:
            backup_type = force_type or manager.determine_backup_type()
            logger.info("=== DRY RUN ===")
            logger.info(f"Wuerde ausfuehren: {backup_type.value} Backup")
            logger.info(f"Quelle: {manager.source_drive}")
            logger.info(f"Ziel: {manager.backup_dir}")
            manager.log_disk_space()
            manager.cleanup_old_cycles(dry_run=True)
            return

        # Speicherplatz pruefen und ggf. Cleanup
        can_start, msg, disk_info = manager.check_and_prepare_backup()
        if not can_start:
            logger.error(f"Backup kann nicht gestartet werden: {msg}")
            sys.exit(1)

        # Backup durchfuehren
        logger.info("=== Starte Backup ===")
        result = manager.run_backup(force_type)

        # JSON-Log speichern
        logger.save_json_log(result)

        # Zusammenfassung generieren (mit aktuellem Speicherplatz)
        final_disk_info = manager.get_disk_space_info()
        cycles = manager.get_backup_cycles()
        summary_gen = SummaryGenerator(config)
        summary = summary_gen.generate(
            result, logger.entries,
            disk_info=final_disk_info,
            cycles_count=len(cycles),
            keep_cycles=manager.keep_cycles
        )

        summary_path = log_dir / f"summary_{session_id}.json"
        summary_gen.save(summary, summary_path)
        logger.info(f"Zusammenfassung gespeichert: {summary_path}")

        # An API senden falls aktiviert
        if config.get("api_settings", {}).get("enabled"):
            logger.info("Sende an API...")
            success, response = summary_gen.post_to_api(summary, logger)
            if success:
                logger.success(f"API-Upload erfolgreich: {response}")
            else:
                logger.warning(f"API-Upload fehlgeschlagen: {response}")

        # Abschluss
        logger.info("=" * 50)
        if result.success:
            logger.success("Backup-Vorgang abgeschlossen")
            sys.exit(0)
        else:
            logger.error("Backup-Vorgang mit Fehlern beendet")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
