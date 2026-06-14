"""Konfiguracja Sphinx dla dokumentacji travel_agent.

Generuje dokumentacje HTML z docstringow (autodoc) w stylu Google (napoleon).
Build: cd docs && make html  ->  docs/_build/html/index.html
"""

import os
import sys

# Dodaj katalog projektu do sciezki, zeby autodoc znalazl moduly.
sys.path.insert(0, os.path.abspath(".."))

# --- Informacje o projekcie ---
project = "travel_agent"
author = "Szymon Ambroziak"
copyright = "2026, Szymon Ambroziak"
release = "1.0"

# --- Rozszerzenia ---
extensions = [
    "sphinx.ext.autodoc",      # wciaga docstringi z kodu
    "sphinx.ext.napoleon",     # rozumie styl Google/NumPy
    "sphinx.ext.viewcode",     # linki do zrodla
    "sphinx.ext.autosummary",  # tabele podsumowan modulow
    "sphinxcontrib.mermaid",   # diagramy UML (Mermaid)
]

autosummary_generate = True
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

# Moduly ktore moga nie miec zainstalowanych zaleznosci przy budowaniu docs -
# autodoc i tak je zaimportuje, ale gdyby brakowalo bibliotek, mock je pomija.
autodoc_mock_imports = ["playwright", "telegram", "httpx", "dotenv"]

# --- Jezyk i wyglad ---
language = "pl"
html_theme = "sphinx_rtd_theme"
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
