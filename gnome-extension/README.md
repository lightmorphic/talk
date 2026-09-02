# Talk: Always on Top (GNOME Shell extension)

Keeps the Lightmorphic Talk floating record button above other windows
on GNOME/Wayland, where an ordinary application has no way to do this
for itself.

## Install (until this is published to extensions.gnome.org)

```bash
mkdir -p ~/.local/share/gnome-shell/extensions
cp -r talk-always-on-top@lightmorphic.com ~/.local/share/gnome-shell/extensions/
```

Then:

- **X11 session:** press `Alt+F2`, type `r`, press Enter to restart GNOME
  Shell, then enable it with:
  ```bash
  gnome-extensions enable talk-always-on-top@lightmorphic.com
  ```
- **Wayland session:** log out and back in first (Shell can't restart
  itself in place under Wayland), then run the same `enable` command.

Check it's running:

```bash
gnome-extensions info talk-always-on-top@lightmorphic.com
```

## If it says "State: OUT OF DATE"

GNOME switches an extension off the moment the running Shell version is
not one the extension claims to support, which is what happens after a
GNOME upgrade. Updating the copy in
`~/.local/share/gnome-shell/extensions/` to the latest `metadata.json`
here fixes it, but GNOME keeps the old one in memory, so on Wayland it
takes a log out and back in before it reads the new file. There is no
way around that: `ReloadExtension` is deprecated and Shell cannot
restart itself in place under Wayland.

To stop GNOME disabling extensions over version numbers at all:

```bash
gsettings set org.gnome.shell disable-extension-version-validation true
```

## Uninstall

```bash
gnome-extensions disable talk-always-on-top@lightmorphic.com
rm -rf ~/.local/share/gnome-shell/extensions/talk-always-on-top@lightmorphic.com
```
