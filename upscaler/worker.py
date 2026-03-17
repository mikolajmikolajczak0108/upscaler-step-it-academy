from __future__ import annotations

from PySide6 import QtCore

from .models import AppSettings, JobOptions
from .pipeline import PipelineError, PipelineRunner
from .tools import download_release_tool


class JobWorker(QtCore.QThread):
    progress_changed = QtCore.Signal(int, str)
    log_line = QtCore.Signal(str)
    completed = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, settings: AppSettings, options: JobOptions) -> None:
        super().__init__()
        self.runner = PipelineRunner(settings, options, self._log, self._progress)

    def _log(self, message: str) -> None:
        self.log_line.emit(message)

    def _progress(self, value: int, stage: str) -> None:
        self.progress_changed.emit(max(0, min(value, 100)), stage)

    def cancel(self) -> None:
        self.runner.cancel()

    def run(self) -> None:
        try:
            self.runner.run()
        except PipelineError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - smoke test fallback
            self.failed.emit(f"Unexpected error: {exc}")
        else:
            self.completed.emit()


class ToolInstallWorker(QtCore.QThread):
    progress_changed = QtCore.Signal(int, str)
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, tool_key: str) -> None:
        super().__init__()
        self.tool_key = tool_key

    def run(self) -> None:
        try:
            exe_path = download_release_tool(self.tool_key, self._progress)
        except Exception as exc:  # pragma: no cover - network dependent
            self.failed.emit(str(exc))
        else:
            self.completed.emit(exe_path)

    def _progress(self, value: int, stage: str) -> None:
        self.progress_changed.emit(value, stage)
