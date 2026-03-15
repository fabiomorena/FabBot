import rumps
import threading
import subprocess
import sys
from pathlib import Path


class FabBotMenubar(rumps.App):
    def __init__(self):
        super().__init__("FabBot", quit_button=None)
        self._bot_process = None
        self._setup_menu()

    def _setup_menu(self):
        self.menu = [
            rumps.MenuItem("Status: Gestoppt", callback=None),
            None,
            rumps.MenuItem("Starten", callback=self.start_bot),
            rumps.MenuItem("Stoppen", callback=self.stop_bot),
            None,
            rumps.MenuItem("Audit Log anzeigen", callback=self.show_audit_log),
            None,
            rumps.MenuItem("Beenden", callback=self.quit_app),
        ]
        self.menu["Stoppen"].set_callback(None)

    def _set_status(self, status: str):
        if status == "running":
            self.title = "FabBot ●"
            self.menu["Status: Gestoppt"].title = "Status: Aktiv"
            self.menu["Starten"].set_callback(None)
            self.menu["Stoppen"].set_callback(self.stop_bot)
        else:
            self.title = "FabBot"
            self.menu["Status: Aktiv"].title = "Status: Gestoppt"
            self.menu["Starten"].set_callback(self.start_bot)
            self.menu["Stoppen"].set_callback(None)

    @rumps.clicked("Starten")
    def start_bot(self, _):
        if self._bot_process and self._bot_process.poll() is None:
            rumps.notification("FabBot", "", "Bot läuft bereits.")
            return

        project_dir = Path(__file__).parent
        venv_python = project_dir / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable

        self._bot_process = subprocess.Popen(
            [python, str(project_dir / "main.py")],
            cwd=str(project_dir),
        )
        self._set_status("running")
        rumps.notification("FabBot", "", "Bot gestartet.")

        def watch():
            self._bot_process.wait()
            self._set_status("idle")
            rumps.notification("FabBot", "", "Bot wurde beendet.")

        threading.Thread(target=watch, daemon=True).start()

    @rumps.clicked("Stoppen")
    def stop_bot(self, _):
        if self._bot_process and self._bot_process.poll() is None:
            self._bot_process.terminate()
            self._bot_process = None
        self._set_status("idle")
        rumps.notification("FabBot", "", "Bot gestoppt.")

    @rumps.clicked("Audit Log anzeigen")
    def show_audit_log(self, _):
        log_path = Path.home() / ".fabbot" / "audit.log"
        if not log_path.exists():
            rumps.notification("FabBot", "Audit Log", "Noch keine Eintraege.")
            return
        lines = log_path.read_text().strip().split("\n")
        last = lines[-5:] if len(lines) > 5 else lines
        rumps.notification("FabBot", "Letzte Aktionen", "\n".join(last))

    def quit_app(self, _):
        self.stop_bot(None)
        rumps.quit_application()


if __name__ == "__main__":
    FabBotMenubar().run()
