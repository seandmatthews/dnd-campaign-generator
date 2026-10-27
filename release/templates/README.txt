CAMPAIGN STUDIO
===============

What this is
------------
A self-contained local web app for running a D&D campaign: fantasy map
generation, world building, a living faction simulation, and one review
queue that turns session notes into canon. It runs entirely on your
machine and talks only to your browser. Not affiliated with or endorsed
by Wizards of the Coast.

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
Everything you create lives in the "campaign-data" folder next to this
file; your campaigns are inside it, in "campaign-data\campaigns".
Your API key (see below) is in the "llm.env" file.

Backing up
----------
This machine holds the only copy of your campaign. Every month or so,
close the Studio and copy the whole "campaign-data" folder somewhere
else - a USB stick, a cloud folder, another drive. To save a single
campaign as one zip file, double-click "export.cmd" (next to
launch.cmd), follow its message, and copy the zip it writes somewhere
safe too. A dead laptop loses only what was never copied anywhere else.
(Note: the app itself has no undo history on this kind of install, so a
backup copy is the only way back.)

Disk space
----------
The installer needs about 1.7 GB of free disk space to run: the ~155 MB
exe, ~750 MB it unpacks to a temp folder, and ~750 MB it installs here.
The temp copy goes away when it finishes, leaving ~750 MB installed
plus the exe. Map generation also writes scratch files.

Updating
--------
Two ways to get a new version. When you get a new exe from the same
place, just run it: it refreshes the program in this folder while
leaving your campaigns and settings alone. Or skip the exe entirely:
double-click "update.cmd" (next to launch.cmd) and it checks online
for the latest release, shows you what it found, and asks before it
updates this folder by itself - a few megabytes instead of the whole
exe. Either way, close the Studio first; your campaigns and your key
are never touched.

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
