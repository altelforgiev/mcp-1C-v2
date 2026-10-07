@echo off
rem ===================================================================
rem Скрипт запуска MCP-сервера метаданных 1С (mcp_server.py)
rem ===================================================================

rem Установка кодировки UTF-8 для корректной работы с кириллицей 1С
chcp 65001 > nul

rem Проверка наличия Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в PATH. Установите Python 3.10+ и добавьте его в переменные среды.
    pause
    exit /b 1
)

rem Определение пути к файлу метаданных (по умолчанию metadata.json)
set METADATA_FILE=data\metadata.json
if "%METADATA_FILE%"=="" (
    set METADATA_FILE=metadata.json
)

rem Проверка наличия файла метаданных
if not exist "%METADATA_FILE%" (
    echo [ПРЕДУПРЕЖДЕНИЕ] Файл метаданных "%METADATA_FILE%" не найден в текущей директории.
    echo Сервер запустится, но метаданные будут пустыми до создания/указания файла.
    echo.
)

echo ===================================================================
echo Запуск MCP-сервера метаданных 1С...
echo Файл метаданных: %METADATA_FILE%
echo ===================================================================
echo.

rem Запуск MCP-сервера на Python
python mcp_server.py "%METADATA_FILE%"

if %errorlevel% neq 0 (
    echo.
    echo [ОШИБКА] MCP-сервер завершил работу с ошибкой код: %errorlevel%.
    pause
)
