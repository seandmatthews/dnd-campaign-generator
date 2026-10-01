CAMPAIGN STUDIO
===============

What this is
------------
A self-contained local web app for running a D&D campaign: fantasy map
generation, world building, a living faction simulation, and one review
queue that turns session notes into canon. It runs entirely on your
machine and talks only to your browser.

Starting it
-----------
The exe you downloaded unpacked this folder. To start the app:

  * double-click the downloaded exe again, or
  * use the "Campaign Studio" desktop shortcut (created on first start),
  * or double-click launch.cmd directly.

Your browser opens at http://127.0.0.1:8765. Close the black console
window to stop the Studio.

Requirements
------------
64-bit Windows 10 or 11. Nothing else - Python, Node.js and every
library are bundled. Do NOT move this folder into "Program Files"
(the app writes its data here, and that folder is write-protected).

Your data
---------
Everything you create lives in the "campaigns" folder next to this file.
Your API key (see below) is in the "llm.env" file.

Disk space
----------
The app needs about 2 GB: ~1.6 GB installed here, plus the same again
transiently when a new version installs. Map generation also writes
scratch files.

Updating
--------
When you get a new exe from the same place, just run it: it refreshes
the program in this folder while leaving your campaigns and settings
alone. Close the Studio before you do.

A note on the API key
---------------------
The "llm.env" file in this folder contains an API key that pays for the
AI features (world generation, week simulation). Anyone who gets a copy
of this folder or the exe can use that key. Treat the file like a
password: don't re-share the folder or upload it anywhere public.

Windows SmartScreen
-------------------
The exe is unsigned, so the first double-click may show "Windows
protected your PC". Click "More info", then "Run anyway". This is the
normal price of software that isn't sold through a store.
