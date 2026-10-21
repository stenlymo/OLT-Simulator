import getpass
import os
from colorama import Fore, Style
from modules.database import get_connection, init_user_data


def clear_screen():
  os.system("cls" if os.name == "nt" else "clear")


def login():
  """Modul Autentikasi CLI"""
  clear_screen()
  print(Fore.CYAN + "==========================================")
  print(Fore.CYAN + "   HUAWEI SMARTAX GPON OLT SIMULATOR     ")
  print(Fore.CYAN + "==========================================")
  print(
      Style.DIM
      + "Default login: admin / admin123 atau user1 / password123\n"
  )

  while True:
    username = input(Fore.YELLOW + "Username : ").strip()
    password = getpass.getpass(Fore.YELLOW + "Password : ")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role FROM users WHERE username = ? AND password = ?",
        (username, password),
    )
    user = cursor.fetchone()
    conn.close()

    if user:
      print(
          Fore.GREEN
          + f"\n[OK] Login berhasil! Selamat datang, {username} ({user[0]}).\n"
      )
      init_user_data(username)
      input(Style.DIM + "Tekan [ENTER] untuk masuk ke terminal OLT...")
      return username
    else:
      print(
          Fore.RED
          + "[ERROR] Login gagal! Username atau password salah. Coba lagi.\n"
      )