@echo off
rem ===================================================================
rem Разбор XML-выгрузки 1С в data\metadata.json
rem Синоним объекта сохраняется. phrases.json этот скрипт не трогает.
rem ===================================================================

chcp 65001 > nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в PATH.
    pause
    exit /b 1
)

set SRC=C:\Work\conf-K2\src\cf
set OUT=data\metadata.json
if not "%~1"=="" set SRC=%~1
if not "%~2"=="" set OUT=%~2

if not exist "%SRC%" (
    echo [ОШИБКА] Каталог выгрузки не найден: %SRC%
    echo Укажите путь: run_parse_xml.bat "C:\Work\conf-K2\src\cf"
    pause
    exit /b 1
)

if not exist "data" mkdir data

echo ===================================================================
echo Разбор XML-выгрузки 1С
echo Источник: %SRC%
echo Результат: %OUT%
echo ===================================================================

python parse_xml_to_json.py "%SRC%" "%OUT%"
if %errorlevel% neq 0 (
    echo [ОШИБКА] Парсер завершился с кодом %errorlevel%.
    pause
    exit /b %errorlevel%
)

echo Готово. Словарь фраз data\phrases.json не изменялся.
pause
