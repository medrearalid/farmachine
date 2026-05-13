import os
import sys


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    # PyInstaller stores unpacked resources in sys._MEIPASS.
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))

    return os.path.join(base_path, relative_path)
