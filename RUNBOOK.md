# Talkmorphic Runbook

Plain-language guide for when something needs doing. Nearly everything
is a button in Settings (tray icon → Settings), and this file is for
the rare cases the buttons can't cover.

## Everyday things (all in Settings, no terminal)

- **Restart Talkmorphic**: Settings → Maintenance → Restart Talkmorphic.
- **See what's wrong**: Settings → Maintenance → View log.
- **Back everything up**: Settings → Maintenance → Download everything
  (zip). That zip contains your config, dictionary, history and
  translations, the lot.
- **Move my dictionary to another machine**: Settings → Personal
  dictionary → Export, then Import on the other machine.
- **Update**: the dot next to the version number shows the state.
  Green means you're up to date. Yellow means an update is ready;
  click it and Talkmorphic updates and restarts itself. Red means the check
  failed (hover for why, usually a connection problem). The check
  happens whenever Settings is opened, or on demand by clicking the
  dot.

## If Talkmorphic won't start

1. Reboot once (fixes most things).
2. Still stuck? Open a terminal and run:
   ```bash
   ~/talkmorphic/scripts/talkmorphic.sh
   ```
   The error it prints says what's wrong. `data/talkmorphic.log` has detail.

## Roll back a bad update

```bash
cd ~/talkmorphic && git checkout "tags/$(cat data/previous-version.txt)" && ./scripts/talkmorphic.sh
```

That returns you to the version you were on before the last update.

## Restore from a backup zip

Unzip it, then copy the `data` folder over `~/talkmorphic/data` and restart
Talkmorphic from the tray (or run the launcher above).

## Start fresh

Delete `~/talkmorphic/data` and restart. Settings, dictionary and history
reset to defaults; the speech model is untouched.

## The mic stopped working

Settings → Microphone → pick your mic → Test microphone. If the test
hears nothing, check the mic is plugged in and not muted in the system
sound settings (speaker icon in your panel).
