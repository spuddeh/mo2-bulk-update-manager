"""Category colours that survive whatever theme MO2 is wearing.

MO2 applies themes as Qt stylesheets rather than palettes
(``moapplication.cpp:662``), so there is no app-level "is this dark?" flag to
read. What *is* reliable is a widget's effective palette once Qt has polished
it -- the stylesheet's background and text colours end up there. So: measure
the list's actual background, then build each category colour at a lightness
that contrasts with it.

Hues stay fixed so a category is recognisable across themes; only lightness
and saturation move. Nothing here is hard-coded to white or black.
"""

from __future__ import annotations

try:
    from PyQt5.QtCore import QSize, Qt
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
except ImportError:
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

# Hue per category, in degrees. Neutral categories carry no hue and are drawn
# by blending the theme's own text colour toward its background instead.
_HUES = {
    "downloading": 172.0,  # teal: in flight
    "downloaded": 199.0,  # cyan: ready to install
    "update": 134.0,  # green: ready to download
    "delisted": 2.0,  # red: gone
    "hidden": 32.0,  # amber: temporarily unavailable
    "superseded": 276.0,  # violet: needs a decision, not an action
}

# Categories drawn from the theme's text colour, with how far to fade them.
_NEUTRAL_FADE = {
    "ignored": 0.45,
    "error": 0.35,
    "unchecked": 0.35,
    "current": 0.50,
}


# WCAG AA for normal text. Group headings are bold and would pass at 3.0, but
# aiming higher costs nothing and keeps the marks legible too.
_TARGET_CONTRAST = 4.5


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(colour: QColor) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    return (
        0.2126 * _channel(colour.redF())
        + 0.7152 * _channel(colour.greenF())
        + 0.0722 * _channel(colour.blueF())
    )


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _luminance(a), _luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Mix ``a`` toward ``b`` by ``t``."""
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
    )


class Theme:
    """Category colours resolved against one widget's real palette."""

    def __init__(self, widget):
        palette = widget.palette()

        background = palette.base().color()
        if background.alpha() == 0:
            background = palette.window().color()
        self.background = background
        self.text = palette.text().color()
        self.dark = _luminance(background) < 0.18

        # On a light background a hue has to be darkened to read, on a dark one
        # lightened -- and by different amounts per hue, since a saturated green
        # is far brighter than a saturated blue at the same nominal lightness.
        # So rather than pick a lightness, solve for one that hits the contrast
        # target against this particular background.
        self._saturation = 0.52 if self.dark else 0.92
        self._start = 0.80 if self.dark else 0.72
        self._step = 0.02 if self.dark else -0.02

        self._cache: dict[str, QColor] = {}
        self._icons: dict[str, QIcon] = {}

    # -- colours -----------------------------------------------------------

    def colour(self, status: str) -> QColor:
        """The accent colour for a category, contrasting with this theme."""
        cached = self._cache.get(status)
        if cached is not None:
            return cached

        hue = _HUES.get(status)
        if hue is None:
            colour = _blend(self.text, self.background, _NEUTRAL_FADE.get(status, 0.4))
        else:
            colour = self._solve(hue)

        self._cache[status] = colour
        return colour

    def _solve(self, hue: float) -> QColor:
        """Walk lightness until the hue clears the contrast target.

        Falls back to the best value found, so an unusual mid-tone theme
        degrades to "as readable as this hue can be" rather than to noise.
        """
        value = self._start
        best: tuple[float, QColor] = (0.0, QColor.fromHsvF(hue / 360.0, self._saturation, value))

        for _ in range(50):
            if not 0.16 <= value <= 1.0:
                break
            colour = QColor.fromHsvF(hue / 360.0, self._saturation, value)
            ratio = _contrast(colour, self.background)
            if ratio >= _TARGET_CONTRAST:
                return colour
            if ratio > best[0]:
                best = (ratio, colour)
            value += self._step

        return best[1]

    def muted(self, amount: float = 0.4) -> QColor:
        """Secondary text that stays legible without competing."""
        return _blend(self.text, self.background, amount)

    # -- marks -------------------------------------------------------------

    def dot(self, status: str, size: int = 10) -> QIcon:
        """A filled circle in the category colour, for the name column.

        Colouring the row text itself fights readability under themes that
        already style it; a small mark beside the name does not.
        """
        cached = self._icons.get(status)
        if cached is not None:
            return cached

        scale = 4  # drawn oversized, then smooth-scaled, so the edge is clean
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = self.colour(status)
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        inset = scale
        painter.drawEllipse(
            inset, inset, size * scale - inset * 2, size * scale - inset * 2
        )
        painter.end()

        icon = QIcon(
            pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._icons[status] = icon
        return icon
