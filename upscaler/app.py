from __future__ import annotations

import sys

from PySide6 import QtWidgets

from .config import load_settings
from .ui import MainWindow


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Upscaler")
    app.setStyle("Fusion")
    window = MainWindow(load_settings())
    window.show()
    return app.exec()
