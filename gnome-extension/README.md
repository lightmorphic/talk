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

## Uninstall

```bash
gnome-extensions disable talk-always-on-top@lightmorphic.com
rm -rf ~/.local/share/gnome-shell/extensions/talk-always-on-top@lightmorphic.com
```
