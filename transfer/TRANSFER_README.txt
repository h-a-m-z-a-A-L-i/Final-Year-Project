Transfer package for normal-chrome (single version: testing stack)

Included:
- one_click_start_testing.cmd
- requirements.txt
- testing/extension/*
- testing/host/*

How to run on a new laptop:
1) Install Google Chrome and Python 3.10+.
2) Open terminal in this transfer folder.
3) Run: one_click_start_testing.cmd
4) Open chrome://extensions and enable Developer mode.
5) Click Load unpacked and select testing/extension.
6) If already loaded, click Reload for extension id: kpgffbnnihefokomkllcnenpdcllaapb.

Important:
- If your API URL changed, set NORMALCHROME_GENERATE_URL to the current /generate endpoint before running.
