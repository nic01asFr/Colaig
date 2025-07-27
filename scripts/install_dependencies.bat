@echo off
echo Installation des dependances du projet et de Playwright pour browser-use

REM Vérifier que Python est installé
python --version 2>NUL
if errorlevel 1 (
    echo Python n'est pas installe ou n'est pas dans le PATH. Veuillez installer Python 3.10 ou superieur.
    exit /b 1
)

REM Vérifier que pip est installé
pip --version 2>NUL
if errorlevel 1 (
    echo pip n'est pas installe ou n'est pas dans le PATH. Veuillez installer pip.
    exit /b 1
)

REM Installer les dépendances du projet
echo Installation des dependances du projet...
pip install -e .
if errorlevel 1 (
    echo Erreur lors de l'installation des dependances du projet.
    exit /b 1
)

REM Installer Playwright
echo Installation de Playwright pour browser-use...
python scripts\install_playwright.py
if errorlevel 1 (
    echo Erreur lors de l'installation de Playwright.
    exit /b 1
)

echo Installation terminee avec succes !
pause 