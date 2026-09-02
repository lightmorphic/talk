import GLib from 'gi://GLib';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

const WINDOW_TITLE = 'Lightmorphic Talk — Float Button';

// A plain application window on Wayland cannot ask the compositor to
// keep it above everything else; only the compositor (Mutter, via the
// Shell) can do that to itself. This extension does exactly one thing:
// whenever a window with the float button's title exists, it is kept
// stacked above the rest, on every workspace, undecorated status
// unchanged. It never touches any other window.
export default class TalkAlwaysOnTopExtension extends Extension {
    _matches(metaWindow) {
        return !!metaWindow && metaWindow.get_title() === WINDOW_TITLE;
    }

    _apply(metaWindow) {
        if (!this._matches(metaWindow))
            return;
        if (!metaWindow.is_above())
            metaWindow.make_above();
    }

    _onWindowCreated(_display, metaWindow) {
        if (!this._matches(metaWindow))
            return;
        this._apply(metaWindow);
        // Owned by `this`, so disable() can drop every one of them at
        // once. Connecting by hand instead leaves a live handler on any
        // window still open when the extension is switched off, which
        // then fires into disabled code.
        metaWindow.connectObject(
            'raised', w => this._apply(w),
            'unmanaged', () => metaWindow.disconnectObject(this),
            this);
    }

    enable() {
        const display = global.display;
        this._createdId = display.connect('window-created',
            (d, w) => this._onWindowCreated(d, w));

        // Catch it if it's already running when the extension is enabled.
        for (const actor of global.get_window_actors()) {
            const metaWindow = actor.get_meta_window();
            if (this._matches(metaWindow))
                this._apply(metaWindow);
        }

        // Belt and braces: Mutter can drop "above" across workspace
        // switches or fullscreen changes for other windows. A light
        // periodic re-check costs nothing and needs no per-signal
        // bookkeeping beyond the window's own 'raised'/'unmanaged'.
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            for (const actor of global.get_window_actors()) {
                const metaWindow = actor.get_meta_window();
                if (this._matches(metaWindow))
                    this._apply(metaWindow);
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._createdId) {
            global.display.disconnect(this._createdId);
            this._createdId = null;
        }
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        // Every per-window handler connected above, in one go.
        for (const actor of global.get_window_actors()) {
            const metaWindow = actor.get_meta_window();
            if (metaWindow)
                metaWindow.disconnectObject(this);
        }
    }
}
