"""The local Settings server.

Binds to 127.0.0.1 only. Every state-changing request must carry the
session token (generated fresh at each start), and the Host header is
checked, so no website or other machine can drive it.
"""

import io
import json
import logging
import os
import secrets
import threading
import time
import zipfile

from flask import (Flask, abort, jsonify, redirect, render_template,
                   request, send_file, session)

from .. import cleanup, i18n
from ..config import (BASE_DIR, DATA_DIR, LOG_PATH, SETTINGS_HOST,
                      SETTINGS_PORT, DEFAULTS)
from ..engine import MODEL_NAME, list_microphones
from ..hotkeys import CORRECTION_KEYS, DICTATION_KEYS

log = logging.getLogger("talkin.web")

_ALLOWED_HOSTS = {f"{SETTINGS_HOST}:{SETTINGS_PORT}",
                  f"localhost:{SETTINGS_PORT}"}


def start_server(app_obj):
    web = Flask(__name__)
    web.secret_key = secrets.token_hex(32)
    web.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # dictionary imports
    launch_token = secrets.token_urlsafe(24)

    config, dictionary, history = (
        app_obj.config, app_obj.dictionary, app_obj.history)

    @web.before_request
    def guard():
        if request.host not in _ALLOWED_HOSTS:
            abort(403)
        if request.method != "GET":
            if request.headers.get("X-Talkin-Token") != session.get("token"):
                abort(403)

    @web.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'")
        return resp

    @web.route("/")
    def index():
        # The tray opens /?key=<token>; that grants this browser a session.
        if request.args.get("key") == launch_token:
            session["token"] = secrets.token_urlsafe(24)
            session.permanent = False
            return redirect("/")
        if "token" not in session:
            abort(403)
        strings = i18n.all_strings()
        boot = {"token": session["token"],
                "s": {k: v for k, v in strings.items()
                      if k.startswith(("settings.", "error.", "update."))}}
        return render_template(
            "settings.html",
            s=strings,
            config=config.all(),
            languages=i18n.available_languages(),
            dictation_keys=DICTATION_KEYS,
            correction_keys=CORRECTION_KEYS,
            mics=list_microphones(),
            model=MODEL_NAME,
            stats=history.stats(),
            # Rendered with |safe inside a JSON script block; escape the
            # only character that could break out of that block.
            boot_json=json.dumps(boot).replace("<", "\\u003c"),
            version=_version())

    # -- config -----------------------------------------------------

    @web.route("/api/config", methods=["POST"])
    def save_config():
        data = request.get_json(silent=True) or {}
        changes = {}
        for key, default in DEFAULTS.items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(default, bool):
                changes[key] = bool(value)
            else:
                changes[key] = str(value)
        config.update(changes)
        from gi.repository import GLib
        GLib.idle_add(app_obj.apply_settings)
        if "autostart" in changes:
            from ..config import set_autostart
            set_autostart(changes["autostart"])
        return jsonify(ok=True)

    # -- dictionary -------------------------------------------------

    @web.route("/api/dictionary")
    def get_dict():
        return jsonify(entries=dictionary.entries())

    @web.route("/api/dictionary", methods=["POST"])
    def add_dict():
        data = request.get_json(silent=True) or {}
        dictionary.add(str(data.get("heard", "")), str(data.get("say", "")))
        return jsonify(entries=dictionary.entries())

    @web.route("/api/dictionary/delete", methods=["POST"])
    def del_dict():
        data = request.get_json(silent=True) or {}
        dictionary.remove(str(data.get("heard", "")))
        return jsonify(entries=dictionary.entries())

    @web.route("/api/dictionary/export")
    def export_dict():
        payload = json.dumps(
            {"talkin_dictionary": 1, "entries": dictionary.entries()},
            ensure_ascii=False, indent=2)
        return send_file(
            io.BytesIO(payload.encode("utf-8")), as_attachment=True,
            download_name="talkin-dictionary.json",
            mimetype="application/json")

    @web.route("/api/dictionary/import", methods=["POST"])
    def import_dict():
        file = request.files.get("file")
        try:
            data = json.load(file) if file else {}
        except ValueError:
            data = {}
        if data.get("talkin_dictionary") != 1 or \
                not isinstance(data.get("entries"), list):
            return jsonify(ok=False), 400
        merged = {e["heard"].lower(): e for e in dictionary.entries()}
        for e in data["entries"]:
            heard = str(e.get("heard", "")).strip()
            say = str(e.get("say", "")).strip()
            if heard and say:
                merged[heard.lower()] = {"heard": heard, "say": say}
        dictionary.replace_all(list(merged.values()))
        return jsonify(ok=True, entries=dictionary.entries())

    # -- history ----------------------------------------------------

    @web.route("/api/history")
    def get_history():
        return jsonify(entries=history.entries(limit=100))

    @web.route("/api/history/clear", methods=["POST"])
    def clear_history():
        history.clear()
        return jsonify(ok=True)

    @web.route("/api/history/export")
    def export_history():
        lines = ["{}\t{}".format(
            time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"])),
            e.get("clean", "")) for e in history.entries(limit=100000)]
        return send_file(
            io.BytesIO("\n".join(lines).encode("utf-8")),
            as_attachment=True, download_name="talkin-history.txt",
            mimetype="text/plain")

    # -- microphone test --------------------------------------------

    @web.route("/api/mic-test", methods=["POST"])
    def mic_test():
        if app_obj.state != "idle":
            return jsonify(ok=False), 409
        try:
            app_obj.recorder.start()
            time.sleep(3)
            audio = app_obj.recorder.stop()
        except Exception:
            log.exception("mic test failed")
            return jsonify(ok=False, error=i18n.t("error.mic")), 500
        peak = float(abs(audio).max()) if len(audio) else 0.0
        result = {"ok": True, "peak": round(peak, 3), "text": ""}
        if peak > 0.01 and app_obj.transcriber.ready:
            done = threading.Event()
            out = {}

            def collect(text, err):
                out["text"] = text or ""
                done.set()

            app_obj.transcriber.submit(audio, collect)
            done.wait(timeout=30)
            result["text"] = cleanup.clean(
                out.get("text", ""), config, dictionary)
        return jsonify(**result)

    # -- maintenance ------------------------------------------------

    @web.route("/api/log")
    def view_log():
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                tail = f.readlines()[-300:]
        except OSError:
            tail = []
        return jsonify(lines=[line.rstrip() for line in tail])

    @web.route("/api/export-all")
    def export_all():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for folder in (DATA_DIR, os.path.join(BASE_DIR, "locales")):
                for name in sorted(os.listdir(folder)):
                    path = os.path.join(folder, name)
                    if os.path.isfile(path):
                        z.write(path, os.path.join(
                            "talkin-export", os.path.basename(folder), name))
        buf.seek(0)
        return send_file(buf, as_attachment=True,
                         download_name="talkin-export.zip",
                         mimetype="application/zip")

    @web.route("/api/restart", methods=["POST"])
    def restart():
        from gi.repository import GLib
        GLib.timeout_add(300, app_obj.restart)
        return jsonify(ok=True)

    # -- self-update ------------------------------------------------

    @web.route("/api/update/check", methods=["POST"])
    def update_check():
        from .. import updater
        return jsonify(**updater.check())

    @web.route("/api/update/apply", methods=["POST"])
    def update_apply():
        from .. import updater
        data = request.get_json(silent=True) or {}
        ok = updater.apply(str(data.get("tag", "")))
        if ok:
            from gi.repository import GLib
            GLib.timeout_add(500, app_obj.restart)
        return jsonify(ok=ok)

    def run():
        web.run(host=SETTINGS_HOST, port=SETTINGS_PORT,
                debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, name="settings-web", daemon=True).start()
    url = "http://{}:{}/?key={}".format(
        SETTINGS_HOST, SETTINGS_PORT, launch_token)
    # The tray reads this; it also means the runbook can say "open the
    # address in this file" if the tray is ever unavailable.
    with open(os.path.join(DATA_DIR, "settings-url.txt"), "w",
              encoding="utf-8") as f:
        f.write(url + "\n")
    log.info("settings on %s:%s", SETTINGS_HOST, SETTINGS_PORT)
    return url


def _version():
    from .. import __version__
    return __version__
