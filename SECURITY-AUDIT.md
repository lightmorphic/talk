# Talkin — security and code audit

Reviewed at version 1.0.6, 27 August 2026. Every item below was checked
against the source, not assumed.

## What an attacker would have to reach

Talkin runs as you, on your machine, with no server, no accounts and no
listening ports. It touches the network exactly twice in its life: it
downloads the speech model once on first run, and it asks GitHub for the
latest release when you click the update dot. Nothing else, ever — after
the model is cached the Hugging Face client is pinned hard offline.

That leaves a small attack surface: files it reads (an imported
dictionary, its own config), what it downloads, and what it can type.

## Fixed in this pass

**Private files were world-readable.** The data folder was created at
0755 and its files at 0644, so any other account on the machine could
read your dictation history, your dictionary, and the portal permission
token in config.json. The folder is now 0700 and the files 0600.

**Exports carried a permission token.** "Export everything" bundled
config.json verbatim, including the Wayland restore token — a capability
granted to this machine, in a file people mail to themselves. Settings
still travel; the token is stripped.

**Certificates were resolved against the build machine's paths.** The
bundled OpenSSL looked for the trust store where Debian keeps it, so on
openSUSE, Fedora and Arch every HTTPS call failed to verify. Now it uses
the certificate bundle that ships inside the AppImage. (This was the
red-dot fault.)

**An imported dictionary was unbounded.** Entries become regular
expressions run over every transcript, and their replacements are text
the app types into whatever window has focus. Imports are now capped at
5000 entries of 200 characters, non-object entries are skipped, and both
fields are coerced to strings.

**A cleanup watcher could be aimed at a temporary path.** Talkin leaves a
login-time script that deletes the model and settings if its AppImage has
gone. Run from /tmp, that script was pointed at a path guaranteed to
vanish, so it would delete the real install's data at the next login. It
is no longer installed for a copy running from a temporary location, and
running from source removes any stale one.

**Desktop entries did not quote the executable path.** An AppImage in a
folder with a space produced an Exec line the launcher read as two
arguments. Paths are now quoted and escaped per the desktop entry spec.

## Checked and found sound

- **No shell.** Every subprocess call passes an argument list; there is
  no `shell=True`, no `os.system`, no string-built command anywhere.
- **No dynamic code.** No `eval`, no `exec`, no `pickle`, no `marshal`.
- **URLs are constants.** The only variable part of any URL is a release
  tag, and that is matched against `v\d+.\d+.\d+` before use.
- **Downloads are size-checked.** A "release" smaller than 20 MB is
  rejected rather than written over the running application.
- **Archives are written, never extracted.** The export writes a zip; the
  app never unpacks one, so there is no path-traversal route in.
- **The dictionary is escaped before it becomes a regular expression**
  (`re.escape`), so an entry cannot inject a pattern.
- **Transcripts are never logged.** The log records lengths and durations
  — "transcribed 42 chars" — never the words. With history switched off,
  nothing of what you said is written anywhere.
- **Portal permission is real permission.** The app reports itself ready
  to type only after the compositor has actually granted a keyboard, not
  when the session object exists.

## Accepted risks, stated plainly

**The update is not signed.** Talkin trusts TLS and the fixed GitHub
repository: it verifies the certificate, checks the file is plausibly
large, and replaces itself. It does not verify a signature, so anyone who
could publish a release to that repository could publish a malicious
build. This is how nearly all AppImages work, and a signature checked
against a key fetched from the same place would add ceremony rather than
security. The real protection is the repository's own account security.

**XTEST on X11 is unrestricted by design.** On an X11 session any client
can synthesise keystrokes; that is why Talkin needs no permission there,
and equally why any other program on that session could watch or fake
input. This is a property of X11, not of Talkin. Wayland's portal exists
precisely to end it, and on Wayland Talkin asks.

**The clipboard briefly holds your transcript.** Pasting is how text gets
in; the previous clipboard contents are restored immediately afterwards.
If the paste fails, the transcript can remain on the clipboard until
something else replaces it.

## Code quality

Two entire modules and about 800 lines went in this pass: the keyboard
shortcut system and its portal backend, the key-capture UI, its styling
and its 28 translated strings. All of it was unreachable — there is no
Shortcuts page and the feature is off — and unreachable code that looks
maintained is worse than no code at all.

Also removed: two portal helpers only the shortcut backend used, four
dead configuration keys, and every comment still describing the app as
Wayland-only or as a fork.

Every translated string is now verified to exist, and every string in the
catalogue is verified to be reachable.
