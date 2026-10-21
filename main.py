import sys
from gui.main_window import MainWindow
from modules.database import init_db
from PyQt6.QtWidgets import QApplication

if __name__ == "__main__":
  # 1. Inisialisasi Database SQLite
  init_db()

  # 2. Jalankan Aplikasi GUI PyQt6
  app = QApplication(sys.argv)
  window = MainWindow()
  window.show()
  sys.exit(app.exec())