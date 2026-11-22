"""Maus und Tastatur am PC steuern (X11, über die XTest-Erweiterung).

Nutzt libX11/libXtst per ctypes, die unter SteamOS im Desktop-Modus immer da
sind – nichts nachzuinstallieren. Texte werden zeichengenau getippt, auch
Umlaute und Sonderzeichen: Fehlt ein Zeichen im Tastaturlayout, wird es kurz
auf eine freie Taste gelegt (wie bei xdotool).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time

BUTTONS = {"left": 1, "middle": 2, "right": 3}

# Namen der Sondertasten (X11-Keysym-Namen)
KEYS = {
    "Return", "BackSpace", "Escape", "Tab", "space", "Left", "Right", "Up", "Down", "Home", "End",
    "Page_Up", "Page_Down", "Delete", "F5", "F11", "Super_L",
    "XF86AudioLowerVolume", "XF86AudioRaiseVolume", "XF86AudioMute", "XF86AudioPlay",
}


class XInputError(RuntimeError):
    pass


def _load(name: str):
    path = ctypes.util.find_library(name)
    for cand in ([path] if path else []) + [f"lib{name}.so.6", f"lib{name}.so"]:
        try:
            return ctypes.CDLL(cand)
        except OSError:
            continue
    raise XInputError(f"lib{name} fehlt")


class XInput:
    def __init__(self, display: str | None = None):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" and not os.environ.get("DISPLAY"):
            raise XInputError("Maussteuerung geht nur im Desktop-Modus mit X11.")
        self.x = _load("X11")
        self.xt = _load("Xtst")
        x, xt = self.x, self.xt
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.dpy = x.XOpenDisplay((display or os.environ.get("DISPLAY", ":0")).encode())
        if not self.dpy:
            raise XInputError("Kein Zugriff auf den Bildschirm (DISPLAY).")
        vp, ci, cu = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        xt.XTestFakeRelativeMotionEvent.argtypes = [vp, ci, ci, ctypes.c_ulong]
        xt.XTestFakeMotionEvent.argtypes = [vp, ci, ci, ci, ctypes.c_ulong]
        xt.XTestFakeButtonEvent.argtypes = [vp, cu, ci, ctypes.c_ulong]
        xt.XTestFakeKeyEvent.argtypes = [vp, cu, ci, ctypes.c_ulong]
        x.XFlush.argtypes = [vp]
        x.XSync.argtypes = [vp, ci]
        x.XDefaultScreen.argtypes = [vp]
        x.XDisplayWidth.argtypes = [vp, ci]
        x.XDisplayHeight.argtypes = [vp, ci]
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XKeysymToKeycode.argtypes = [vp, ctypes.c_ulong]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XDisplayKeycodes.argtypes = [vp, ctypes.POINTER(ci), ctypes.POINTER(ci)]
        x.XGetKeyboardMapping.argtypes = [vp, ctypes.c_ubyte, ci, ctypes.POINTER(ci)]
        x.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
        x.XChangeKeyboardMapping.argtypes = [vp, ci, ci, ctypes.POINTER(ctypes.c_ulong), ci]
        x.XFree.argtypes = [vp]
        x.XDefaultRootWindow.argtypes = [vp]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XQueryPointer.argtypes = [vp, ctypes.c_ulong] + [ctypes.POINTER(ctypes.c_ulong)] * 2 + \
                                   [ctypes.POINTER(ci)] * 4 + [ctypes.POINTER(cu)]
        self.lock = threading.Lock()
        self.screen = x.XDefaultScreen(self.dpy)
        self._held: set[int] = set()
        self._scratch = self._find_scratch_keycode()

    # ------------------------------------------------------------- Bildschirm
    @property
    def size(self) -> tuple[int, int]:
        return self.x.XDisplayWidth(self.dpy, self.screen), self.x.XDisplayHeight(self.dpy, self.screen)

    def pointer(self) -> tuple[int, int]:
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        self.x.XQueryPointer(self.dpy, self.x.XDefaultRootWindow(self.dpy), ctypes.byref(root), ctypes.byref(child),
                             ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
        return rx.value, ry.value

    # ------------------------------------------------------------- Maus
    def move(self, dx: float, dy: float) -> None:
        with self.lock:
            self.xt.XTestFakeRelativeMotionEvent(self.dpy, int(round(dx)), int(round(dy)), 0)
            self.x.XFlush(self.dpy)

    def move_to(self, fx: float, fy: float) -> None:
        """Absolut, fx/fy als Anteil 0..1 des Bildschirms."""
        w, h = self.size
        with self.lock:
            self.xt.XTestFakeMotionEvent(self.dpy, -1, int(max(0, min(1, fx)) * (w - 1)),
                                         int(max(0, min(1, fy)) * (h - 1)), 0)
            self.x.XFlush(self.dpy)

    def button(self, name: str, down: bool) -> None:
        b = BUTTONS.get(name, 1)
        with self.lock:
            self.xt.XTestFakeButtonEvent(self.dpy, b, 1 if down else 0, 0)
            self.x.XFlush(self.dpy)
        (self._held.add if down else self._held.discard)(b)

    def click(self, name: str = "left", double: bool = False) -> None:
        for _ in range(2 if double else 1):
            self.button(name, True)
            self.button(name, False)

    def scroll(self, dy: int, dx: int = 0) -> None:
        """dy > 0 = nach unten. Ein Schritt = ein Rad-Klick."""
        with self.lock:
            for _ in range(min(abs(int(dy)), 20)):
                b = 5 if dy > 0 else 4
                self.xt.XTestFakeButtonEvent(self.dpy, b, 1, 0)
                self.xt.XTestFakeButtonEvent(self.dpy, b, 0, 0)
            for _ in range(min(abs(int(dx)), 20)):
                b = 7 if dx > 0 else 6
                self.xt.XTestFakeButtonEvent(self.dpy, b, 1, 0)
                self.xt.XTestFakeButtonEvent(self.dpy, b, 0, 0)
            self.x.XFlush(self.dpy)

    def release_all(self) -> None:
        for b in list(self._held):
            self.button({1: "left", 2: "middle", 3: "right"}.get(b, "left"), False)

    # ------------------------------------------------------------- Tastatur
    def _find_scratch_keycode(self) -> int | None:
        lo, hi = ctypes.c_int(), ctypes.c_int()
        self.x.XDisplayKeycodes(self.dpy, ctypes.byref(lo), ctypes.byref(hi))
        per = ctypes.c_int()
        count = hi.value - lo.value + 1
        mapping = self.x.XGetKeyboardMapping(self.dpy, lo.value, count, ctypes.byref(per))
        try:
            for i in range(count - 1, -1, -1):  # von hinten: meist frei
                if all(mapping[i * per.value + j] == 0 for j in range(per.value)):
                    return lo.value + i
        finally:
            self.x.XFree(mapping)
        return None

    def _keysym(self, name: str) -> int:
        ks = self.x.XStringToKeysym(name.encode())
        if not ks:
            raise ValueError(f"Unbekannte Taste: {name}")
        return ks

    def _tap_keycode(self, kc: int, shift: bool = False) -> None:
        shift_kc = self.x.XKeysymToKeycode(self.dpy, self._keysym("Shift_L")) if shift else 0
        if shift_kc:
            self.xt.XTestFakeKeyEvent(self.dpy, shift_kc, 1, 0)
        self.xt.XTestFakeKeyEvent(self.dpy, kc, 1, 0)
        self.xt.XTestFakeKeyEvent(self.dpy, kc, 0, 0)
        if shift_kc:
            self.xt.XTestFakeKeyEvent(self.dpy, shift_kc, 0, 0)

    def _level_of(self, kc: int, ks: int) -> int | None:
        per = ctypes.c_int()
        m = self.x.XGetKeyboardMapping(self.dpy, kc, 1, ctypes.byref(per))
        try:
            for lvl in range(min(per.value, 2)):
                if m[lvl] == ks:
                    return lvl
        finally:
            self.x.XFree(m)
        return None

    def _tap_keysym(self, ks: int) -> None:
        kc = self.x.XKeysymToKeycode(self.dpy, ks)
        lvl = self._level_of(kc, ks) if kc else None
        if kc and lvl is not None:
            self._tap_keycode(kc, shift=lvl == 1)
            return
        # Nicht im Layout (z. B. € oder Emoji): kurz auf eine freie Taste legen
        if self._scratch is None:
            return
        arr = (ctypes.c_ulong * 2)(ks, ks)
        self.x.XChangeKeyboardMapping(self.dpy, self._scratch, 2, arr, 1)
        self.x.XSync(self.dpy, 0)
        time.sleep(0.02)
        self._tap_keycode(self._scratch)
        self.x.XSync(self.dpy, 0)
        time.sleep(0.02)
        empty = (ctypes.c_ulong * 2)(0, 0)
        self.x.XChangeKeyboardMapping(self.dpy, self._scratch, 2, empty, 1)
        self.x.XSync(self.dpy, 0)

    def key(self, name: str) -> None:
        if name not in KEYS:
            raise ValueError(f"Taste nicht erlaubt: {name}")
        with self.lock:
            self._tap_keysym(self._keysym(name))
            self.x.XFlush(self.dpy)

    def type_text(self, text: str) -> None:
        with self.lock:
            for ch in text[:500]:
                if ch == "\n":
                    ks = self._keysym("Return")
                elif ch == "\t":
                    ks = self._keysym("Tab")
                else:
                    cp = ord(ch)
                    ks = cp if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF else 0x01000000 + cp
                self._tap_keysym(ks)
            self.x.XFlush(self.dpy)
