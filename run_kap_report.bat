@echo off
REM KAP Fon Repo/Ters Repo gunluk raporu - Windows Task Scheduler bu dosyayi calistirir.
REM Bu .bat dosyasinin proje klasorunde durmasi gerekir.

cd /d "%~dp0"

REM Sanal ortam varsa onu kullan, yoksa sistem Python'u
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)

REM Rapor uretildikten sonra varsayilan tarayicida ac.
REM Otomatik acilmasini istemiyorsaniz asagidaki satiri silin.
if exist "reports\son_rapor.html" start "" "reports\son_rapor.html"
