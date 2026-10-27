@echo off
rem Vercel's local Python builder invokes `python3`.  Windows installations
rem commonly expose only `python.exe`, while `python3.exe` may be a Store alias.
rem Keeping this shim at the project root makes `vercel dev` use the installed
rem Python interpreter without changing global Windows aliases.
python.exe %*
